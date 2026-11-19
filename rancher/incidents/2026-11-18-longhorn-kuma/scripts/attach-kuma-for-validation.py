#!/usr/bin/env python3
"""Guarded manual Kuma attachment on proxmox3; never mounts or starts applications."""
import argparse
import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

V = 'pvc-326def38-faba-49f6-8a78-ebf719227220'
TICKET = 'longhorn-api-kuma-validation-20261118'


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--execute', action='store_true')
    ap.add_argument('--yes', action='store_true')
    args = ap.parse_args()
    if os.geteuid() != 0:
        ap.error('Run with sudo on proxmox3')
    kube = ['kubectl', '--kubeconfig=/etc/rancher/k3s/k3s.yaml', '--request-timeout=15s']

    def call(argv):
        p = subprocess.run(kube + argv, capture_output=True, text=True, timeout=25)
        if p.returncode:
            raise RuntimeError(p.stderr.strip())
        return json.loads(p.stdout)

    def get(kind, name):
        return call(['get', kind, name, '-n', 'longhorn-system', '-o', 'json'])

    def guards():
        # Reuse all live detached-volume, paused-Bundle and consumer checks.
        subprocess.run([sys.executable, str(Path(__file__).with_name('prepare-kuma-validation.py'))],
                       check=True, timeout=300)
        volume = get('volumes.longhorn.io', V)
        if volume['spec']['numberOfReplicas'] != 1:
            raise RuntimeError('Desired replicas must already be 1')
        va = get('volumeattachments.longhorn.io', V)
        if va['spec'].get('attachmentTickets'):
            raise RuntimeError('Attachment tickets exist')
        if volume['status']['state'] != 'detached' or volume['spec'].get('nodeID'):
            raise RuntimeError('Volume no longer detached')
        return volume, va

    volume, va = guards()
    ticket = {'id': TICKET, 'type': 'longhorn-api', 'nodeID': 'proxmox3',
              'parameters': {'disableFrontend': 'false', 'lastAttachedBy': 'kuma-validation-20261118'}}
    print(json.dumps({'addAttachmentTicket': ticket}, indent=2))
    print('Attachment starts storage processes and exposes a block device. No filesystem mount or copy.')
    if not args.execute:
        print('Dry run. Use --execute after confirming the old host remains free of Kuma writers/device/mount.')
        return
    if not args.yes:
        if not (shutil.which('dialog') and sys.stdin.isatty() and sys.stdout.isatty()):
            raise RuntimeError('No interactive dialog; explicit --execute --yes required')
        if subprocess.run(['dialog', '--defaultno', '--yesno',
                           'Confirm old-host Kuma device/mount/writers remain absent. Attach only '
                           'Kuma volume on proxmox3 with desired replicas 1? No mount or app startup.',
                           '11', '76']).returncode:
            return
    volume, va = guards()
    folder = Path('/var/tmp') / ('kuma-validation-attach-' + datetime.datetime.now(
        datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    folder.mkdir(mode=0o700)
    for name, obj in [('volume-before', volume), ('attachment-before', va)]:
        (folder / (name + '.json')).write_text(json.dumps(obj, indent=2) + '\n')

    def log(event):
        with (folder / 'actions.jsonl').open('a') as f:
            f.write(json.dumps({'time': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                                'event': event}) + '\n')
            f.flush()
            os.fsync(f.fileno())
        print(event, flush=True)

    patch = [{'op': 'test', 'path': '/metadata/resourceVersion', 'value': va['metadata']['resourceVersion']},
             {'op': 'test', 'path': '/spec/attachmentTickets', 'value': {}},
             {'op': 'add', 'path': '/spec/attachmentTickets/' + TICKET, 'value': ticket}]
    print('Backup/action log:', folder)
    log('attachment_requested')
    try:
        call(['patch', 'volumeattachments.longhorn.io', V, '-n', 'longhorn-system',
              '--type=json', '-p', json.dumps(patch), '-o', 'json'])
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            live = get('volumes.longhorn.io', V)
            attachment = get('volumeattachments.longhorn.io', V)
            if set(attachment['spec'].get('attachmentTickets', {})) != {TICKET}:
                raise RuntimeError('Unexpected attachment ticket inventory')
            if live['spec']['numberOfReplicas'] != 1:
                raise RuntimeError('Desired replica count changed')
            if live['status'].get('robustness') == 'faulted':
                raise RuntimeError('Volume faulted')
            state = live['status']['state']
            print('state:', state, 'node:', live['status'].get('currentNodeID'), flush=True)
            if state == 'attached' and live['status'].get('currentNodeID') == 'proxmox3':
                (folder / 'volume-after.json').write_text(json.dumps(live, indent=2) + '\n')
                log('attached_to_proxmox3_confirmed')
                device = '/dev/longhorn/' + V
                p = subprocess.run(['blkid', '-p', device], capture_output=True, text=True, timeout=20)
                (folder / 'blkid.txt').write_text(p.stdout + p.stderr)
                print(p.stdout + p.stderr, end='')
                if p.returncode:
                    raise RuntimeError('Block-device signature probe failed; no filesystem changes attempted')
                log('block_signature_probe_completed')
                print('Leave unmounted. Share output before filesystem validation. Ticket remains for controlled detach.')
                return
            time.sleep(5)
        raise RuntimeError('Attachment timed out')
    except Exception:
        log('failed_ticket_may_remain_do_not_force_detach_or_retry_blindly')
        raise


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        raise SystemExit(f'Refused/failed: {exc}')
