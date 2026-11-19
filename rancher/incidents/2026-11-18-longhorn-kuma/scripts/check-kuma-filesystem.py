#!/usr/bin/env python3
"""Check the unmounted Kuma ext4 filesystem with e2fsck -f -n. Never repair."""
import datetime
import errno
import json
import os
from pathlib import Path
import socket
import stat
import subprocess

V = 'pvc-326def38-faba-49f6-8a78-ebf719227220'
UUID = 'cb578054-4075-47b7-afe3-c1f8c31381da'
TICKET = 'longhorn-api-kuma-validation-20261118'


def read_process_mounts(proc):
    """Skip vanished tasks or EINVAL only when the task has no mount namespace."""
    try:
        return (proc / 'mountinfo').read_text().splitlines()
    except (FileNotFoundError, ProcessLookupError):
        return []
    except OSError as exc:
        if exc.errno != errno.EINVAL:
            raise
        try:
            os.readlink(proc / 'ns' / 'mnt')
        except (FileNotFoundError, ProcessLookupError):
            # Kernel threads and exiting tasks may have no nsproxy.
            return []
        raise RuntimeError(f'Cannot inspect mountinfo for existing mount namespace: {proc}') from exc


def source_guard():
    if os.geteuid() != 0 or socket.gethostname().split('.')[0] != 'proxmox3':
        raise RuntimeError('Run as root on proxmox3 only')
    kube = ['kubectl', '--kubeconfig=/etc/rancher/k3s/k3s.yaml', '--request-timeout=15s']

    def get(kind, name=None, namespace='longhorn-system'):
        command = kube + ['get', kind] + ([name] if name else [])
        command += (['-n', namespace] if namespace else ['-A']) + ['-o', 'json']
        p = subprocess.run(command, capture_output=True, text=True, timeout=25, check=True)
        return json.loads(p.stdout)

    volume = get('volumes.longhorn.io', V)
    if (volume['metadata']['uid'] != '7150b0cb-18c8-448f-9831-cefca486672c'
            or volume['status']['state'] != 'attached'
            or volume['status'].get('currentNodeID') != 'proxmox3'
            or volume['spec']['numberOfReplicas'] != 1):
        raise RuntimeError('Unexpected volume identity/attachment/count')
    tickets = get('volumeattachments.longhorn.io', V)['spec'].get('attachmentTickets', {})
    if set(tickets) != {TICKET} or tickets[TICKET].get('nodeID') != 'proxmox3':
        raise RuntimeError('Unexpected attachment tickets')
    if not get('bundles.fleet.cattle.io', 'uptime-kuma', 'fleet-local')['spec'].get('paused'):
        raise RuntimeError('Kuma Bundle is not paused')
    if get('deployment', 'uptime-kuma', 'apps-uptime-kuma')['spec'].get('replicas', 1) != 0:
        raise RuntimeError('Kuma is not stopped')
    claims = {(p['metadata']['namespace'], p['metadata']['name'])
              for p in get('pvc', namespace=None)['items'] if p['spec'].get('volumeName') == V}
    if not claims:
        raise RuntimeError('Bound PVC missing')
    for pod in get('pods', namespace=None)['items']:
        for item in pod['spec'].get('volumes', []):
            claim = item.get('persistentVolumeClaim', {}).get('claimName')
            if (pod['metadata']['namespace'], claim) in claims:
                raise RuntimeError('PVC consumer pod exists')
    device = Path('/dev/longhorn/' + V).resolve(strict=True)
    identity = device.stat()
    if not stat.S_ISBLK(identity.st_mode):
        raise RuntimeError('Expected block device')
    devnum = f'{os.major(identity.st_rdev)}:{os.minor(identity.st_rdev)}'

    def unmounted():
        if device.stat().st_rdev != identity.st_rdev:
            raise RuntimeError('Device identity changed')
        # Inspect visible process mount namespaces; aliases/bind mounts match by device number.
        for proc in Path('/proc').glob('[0-9]*'):
            lines = read_process_mounts(proc)
            for line in lines:
                if line.split()[2] == devnum:
                    raise RuntimeError(f'Device mounted in namespace visible at {proc}/mountinfo')
        if list(Path('/sys/dev/block', devnum, 'holders').iterdir()):
            raise RuntimeError('Device has holders')

    unmounted()
    signature = subprocess.run(['blkid', '-p', '-o', 'export', str(device)],
                               capture_output=True, text=True, timeout=20, check=True).stdout
    fields = dict(line.split('=', 1) for line in signature.splitlines() if '=' in line)
    if fields.get('TYPE') != 'ext4' or fields.get('UUID') != UUID:
        raise RuntimeError('Unexpected filesystem type/UUID')
    return device, unmounted, {'volume': volume, 'majorMinor': devnum, 'signature': fields}


def main():
    device, unmounted, evidence = source_guard()
    folder = Path('/var/tmp') / ('kuma-filesystem-check-' + datetime.datetime.now(
        datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    folder.mkdir(mode=0o700)
    command = ['e2fsck', '-f', '-n', str(device)]
    (folder / 'before.json').write_text(json.dumps(dict(evidence, device=str(device), command=command), indent=2) + '\n')
    unmounted()
    print('Read-only check; no repair. Log directory:', folder, flush=True)
    try:
        p = subprocess.run(command, capture_output=True, text=True, timeout=60)
        output = p.stdout + p.stderr
        (folder / 'e2fsck.txt').write_text(output)
        (folder / 'result.json').write_text(json.dumps({'exitCode': p.returncode,
            'repairAttempted': False, 'command': command}) + '\n')
        print(output, end='')
        print('e2fsck exit code:', p.returncode)
        print('Leave unmounted and Kuma stopped. Share output; do not run repair flags.')
        unmounted()
        return p.returncode
    except subprocess.TimeoutExpired as exc:
        (folder / 'timeout.txt').write_text(str(exc) + '\n' + str(exc.stdout or '') + str(exc.stderr or ''))
        raise RuntimeError('Read-only check timed out; inspect logs, no repair attempted') from exc


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        raise SystemExit(f'Refused/failed: {exc}')
