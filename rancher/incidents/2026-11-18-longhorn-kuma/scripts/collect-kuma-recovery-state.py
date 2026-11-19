#!/usr/bin/env python3
"""Collect read-only Kuma recovery evidence. Never attaches, mounts or copies data."""
import argparse
import datetime
import json
import os
from pathlib import Path
import socket
import subprocess

VOLUME = 'pvc-326def38-faba-49f6-8a78-ebf719227220'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kubeconfig', default='/etc/rancher/k3s/k3s.yaml')
    parser.add_argument('--output-dir', default='/var/tmp')
    args = parser.parse_args()
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    folder = Path(args.output_dir) / ('kuma-recovery-state-' + stamp)
    folder.mkdir(mode=0o700)
    kube = ['kubectl', '--kubeconfig=' + args.kubeconfig, '--request-timeout=15s']
    errors = []
    data = {}
    queries = {
        'volume': ['-n', 'longhorn-system', 'get', 'volumes.longhorn.io', VOLUME],
        'attachment': ['-n', 'longhorn-system', 'get', 'volumeattachments.longhorn.io', VOLUME],
        'engines': ['-n', 'longhorn-system', 'get', 'engines.longhorn.io'],
        'replicas': ['-n', 'longhorn-system', 'get', 'replicas.longhorn.io'],
        'longhorn-nodes': ['-n', 'longhorn-system', 'get', 'nodes.longhorn.io'],
        'instance-managers': ['-n', 'longhorn-system', 'get', 'instancemanagers.longhorn.io'],
        'nodes': ['get', 'nodes'],
        'pods': ['get', 'pods', '-A'],
        'kuma-deployments': ['-n', 'apps-uptime-kuma', 'get', 'deployments'],
        'kuma-bundle': ['-n', 'fleet-local', 'get', 'bundles.fleet.cattle.io', 'uptime-kuma'],
        'kubernetes-attachments': ['get', 'volumeattachments.storage.k8s.io'],
    }
    for name, query in queries.items():
        try:
            result = subprocess.run(kube + query + ['-o', 'json'],
                                    capture_output=True, text=True, timeout=25)
            if result.returncode:
                raise RuntimeError(result.stderr.strip())
            obj = json.loads(result.stdout)
            if name in ('engines', 'replicas'):
                obj['items'] = [x for x in obj['items'] if x.get('spec', {}).get('volumeName') == VOLUME]
            elif name == 'kubernetes-attachments':
                obj['items'] = [x for x in obj['items'] if x.get('spec', {}).get('source', {}).get('persistentVolumeName') == VOLUME]
            data[name] = obj
            (folder / (name + '.json')).write_text(json.dumps(obj, indent=2) + '\n')
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            errors.append({'query': name, 'error': str(exc)})
            print('FAILED:', name, str(exc))
    mounts = Path('/proc/self/mountinfo').read_text()
    matching_mounts = [line for line in mounts.splitlines() if VOLUME in line]
    (folder / 'local-matching-mountinfo.txt').write_text('\n'.join(matching_mounts) + '\n')
    volume = data.get('volume', {})
    summary = {
        'timeUTC': stamp, 'host': socket.gethostname(), 'volume': VOLUME,
        'state': volume.get('status', {}).get('state'),
        'robustness': volume.get('status', {}).get('robustness'),
        'requestedNode': volume.get('spec', {}).get('nodeID'),
        'currentNode': volume.get('status', {}).get('currentNodeID'),
        'attachmentTickets': data.get('attachment', {}).get('spec', {}).get('attachmentTickets'),
        'replicas': [{'name': x['metadata']['name'], 'spec': x.get('spec'), 'status': x.get('status')}
                     for x in data.get('replicas', {}).get('items', [])],
        'localDeviceSymlinkExists': os.path.lexists('/dev/longhorn/' + VOLUME),
        'localMatchingMounts': matching_mounts, 'errors': errors,
        'limitations': 'Local mount/device checks cover this host and mount namespace only. '
                       'This inventory does not establish etcd health, absence of all consumers, '
                       'replica data integrity or readiness to attach.',
    }
    (folder / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2))
    print('Evidence directory:', folder)
    if errors:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
