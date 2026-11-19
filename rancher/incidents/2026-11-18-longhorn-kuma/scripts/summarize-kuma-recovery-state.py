#!/usr/bin/env python3
"""Summarize existing private Kuma evidence without contacting Kubernetes."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', nargs='?', help='Default: latest /var/tmp/kuma-recovery-state-*')
    args = parser.parse_args()
    folder = Path(args.directory) if args.directory else max(
        Path('/var/tmp').glob('kuma-recovery-state-*'), key=lambda p: p.name)

    def read(name):
        return json.loads((folder / (name + '.json')).read_text())

    def pick(obj, keys):
        return {key: obj.get(key) for key in keys}

    volume = read('volume')
    bundle = read('kuma-bundle')
    deployments = read('kuma-deployments')['items']
    report = {
        'evidence': str(folder),
        'volumeSpec': volume['spec'],
        'volumeStatus': volume.get('status'),
        'kumaBundlePaused': bundle.get('spec', {}).get('paused', False),
        'deployments': [
            {'name': d['metadata']['name'], 'replicas': d['spec'].get('replicas', 1),
             'status': d.get('status'),
             'placement': pick(d['spec']['template']['spec'],
                               ['nodeName', 'nodeSelector', 'affinity', 'tolerations']),
             'volumes': d['spec']['template']['spec'].get('volumes', [])}
            for d in deployments],
        'kumaPods': [
            {'name': p['metadata']['name'], 'node': p['spec'].get('nodeName'),
             'phase': p.get('status', {}).get('phase'),
             'deleting': p['metadata'].get('deletionTimestamp')}
            for p in read('pods')['items'] if p['metadata']['namespace'] == 'apps-uptime-kuma'],
        'engines': read('engines')['items'],
        'kubernetesAttachments': read('kubernetes-attachments')['items'],
        'longhornNodes': [
            {'name': n['metadata']['name'], 'spec': n['spec'],
             'conditions': n.get('status', {}).get('conditions'),
             'diskStatus': n.get('status', {}).get('diskStatus')}
            for n in read('longhorn-nodes')['items']],
        'instanceManagers': [
            {'name': i['metadata']['name'], 'node': i['spec'].get('nodeID'),
             'state': i.get('status', {}).get('currentState')}
            for i in read('instance-managers')['items']],
    }
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError) as exc:
        raise SystemExit(f'Cannot summarize evidence: {exc}')
