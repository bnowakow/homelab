#!/usr/bin/env python3
"""Read-only check of current apps-* pods tolerating the maintenance taint."""
import argparse
import json
import subprocess


def matches(t):
    return (
        t.get('effect', '') in ('', 'NoSchedule')
        and (t.get('key') == 'maintenance'
             or (not t.get('key') and t.get('operator') == 'Exists'))
        and (t.get('operator', 'Equal') == 'Exists'
             or t.get('value', '') == 'storage-repair')
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kubeconfig', default='/etc/rancher/k3s/k3s.yaml')
    args = parser.parse_args()
    result = subprocess.run(
        ['kubectl', '--kubeconfig=' + args.kubeconfig,
         '--request-timeout=20s', 'get', 'pods', '-A', '-o', 'json'],
        check=True, capture_output=True, text=True, timeout=30,
    )
    count = 0
    for pod in json.loads(result.stdout)['items']:
        meta, spec = pod['metadata'], pod['spec']
        if not meta['namespace'].startswith('apps-'):
            continue
        tolerations = [t for t in spec.get('tolerations', []) if matches(t)]
        if tolerations:
            count += 1
            print(meta['namespace'], meta['name'],
                  'node=' + spec.get('nodeName', '<pending>'),
                  json.dumps(tolerations))
    print(f'Matching current apps-* pods: {count}')
    print('Read-only check. Controller templates and future pods are not covered.')


if __name__ == '__main__':
    try:
        main()
    except (subprocess.SubprocessError, OSError, ValueError, KeyError) as exc:
        raise SystemExit(f'Check failed; no safety conclusion: {exc}')
