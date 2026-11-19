#!/usr/bin/env python3
"""Collect bounded local host/cluster readiness evidence. Never copies or changes state."""
import datetime
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time


def parse_psi(value):
    return {line.split()[0]: {k: float(v) for k, v in (item.split('=') for item in line.split()[1:])}
            for line in value.splitlines()}


def main():
    host = socket.gethostname().split('.')[0]
    if host not in ('proxmox3', 'proxmox2-old') or os.geteuid() != 0:
        raise RuntimeError('Run with sudo on proxmox3 and proxmox2-old separately')
    folder = Path('/var/tmp') / ('kuma-copy-readiness-' + host + '-' + datetime.datetime.now(
        datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    folder.mkdir(mode=0o700)
    failures = []
    evidence = {}

    def run(name, argv, required=True, timeout=25):
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
            result = {'command': argv, 'returnCode': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr}
            if p.returncode and required:
                failures.append(name + ': command failed')
        except (OSError, subprocess.TimeoutExpired) as exc:
            result = {'command': argv, 'error': str(exc)}
            if required:
                failures.append(name + ': ' + str(exc))
        evidence[name] = result
        (folder / (name + '.json')).write_text(json.dumps(result, indent=2) + '\n')
        return result

    kube = ['kubectl', '--kubeconfig=/etc/rancher/k3s/k3s.yaml', '--request-timeout=15s']
    run('api-readyz', kube + ['get', '--raw=/readyz?verbose'])
    if host == 'proxmox3':
        result = run('maintenance-verify', [sys.executable,
                    str(Path(__file__).with_name('kuma-recovery-maintenance.py')), 'verify'], timeout=180)
        print(result.get('stdout', ''), end='')
        if result.get('stderr'):
            print(result['stderr'], file=sys.stderr, end='')
        if result.get('error'):
            print(result['error'], file=sys.stderr)
    first = run('k3s-before', ['systemctl', 'show', 'k3s', '-p', 'NRestarts',
                             '-p', 'ExecMainStartTimestampMonotonic', '-p', 'ActiveState'])
    samples = []
    for i in range(3):
        value = parse_psi(Path('/proc/pressure/io').read_text())
        samples.append({'time': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'ioPSI': value})
        print('I/O sample', i+1, host, json.dumps(value), flush=True)
        if value.get('some', {}).get('avg10', 100) > 10 or value.get('full', {}).get('avg10', 100) > 2:
            failures.append('I/O PSI exceeds conservative review threshold')
        if i < 2:
            time.sleep(10)
    last = run('k3s-after', ['systemctl', 'show', 'k3s', '-p', 'NRestarts',
                            '-p', 'ExecMainStartTimestampMonotonic', '-p', 'ActiveState'])
    if first.get('stdout') != last.get('stdout') or 'ActiveState=active' not in last.get('stdout', ''):
        failures.append('k3s service changed/restarted or is not active during observation')
    run('longhorn-mount', ['findmnt', '-T', '/var/lib/longhorn', '-o', 'SOURCE,TARGET,FSTYPE,OPTIONS'])
    run('filesystem-space', ['df', '-B1', '/var/lib/longhorn', '/var/tmp'])
    run('block-tree', ['lsblk', '-o', 'NAME,TYPE,SIZE,FSTYPE,MOUNTPOINTS'])
    if shutil.which('lvs'):
        run('lvm', ['lvs', '--reportformat', 'json', '-a', '-o',
                    'lv_name,vg_name,lv_size,segtype,data_percent,metadata_percent,pool_lv'], required=False)
        run('physical-volumes', ['pvs', '--reportformat', 'json'], required=False)
        run('volume-groups', ['vgs', '--reportformat', 'json'], required=False)
    if shutil.which('zpool'):
        run('zpool-space', ['zpool', 'list', '-p'], required=False)
        run('zpool-status', ['zpool', 'status'], required=False)
    run('kernel-storage-errors', ['journalctl', '-k', '--since=-20min', '--no-pager', '-n', '120'], required=False)
    run('longhorn-state', kube + ['get', 'nodes.longhorn.io,instancemanagers.longhorn.io,volumes.longhorn.io',
                                 '-n', 'longhorn-system', '-o', 'wide'])
    summary = {'host': host, 'directory': str(folder), 'samples': samples,
               'k3sBefore': first, 'k3sAfter': last, 'failures': sorted(set(failures)),
               'maintenanceVerification': evidence.get('maintenance-verify'),
               'filesystemSpace': evidence['filesystem-space'],
               'backingMount': evidence['longhorn-mount'],
               'limitations': 'Review physical backing pool/thin-pool headroom and recent kernel errors separately. '
                              'No automatic capacity, source-writer, replica-integrity or copy approval. '
                              'Short k3s stability sample is not proof of long-term health. No cap set or data copied.'}
    (folder / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2))
    if failures:
        raise SystemExit(2)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        raise SystemExit(f'Readiness collection failed: {exc}')
