#!/usr/bin/python3
"""Relinquish only two confirmed kubectl-patch chart-label claims for Zabbix."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import yaml

PATHS = [ ['f:metadata', 'f:labels', 'f:helm.sh/chart'],
          ['f:spec', 'f:template', 'f:metadata', 'f:labels', 'f:helm.sh/chart'] ]
ERROR_PATHS = {'.metadata.labels.helm.sh/chart', '.spec.template.metadata.labels.helm.sh/chart'}


def supported_error(message):
    paths = set(re.findall(r'(?:^|\s)-\s+(\.[A-Za-z0-9_./\[\]"=-]+)', message))
    managers = set(re.findall(r'conflicts? with "([^"]+)"', message))
    return (managers == {'kubectl-patch'} and paths == ERROR_PATHS and
            'apps-zabbix/zabbix-postgresql apps/v1' in message and 'StatefulSet' in message)


def remove_claim(fields, path):
    if not path or path[0] not in fields:
        return False
    key = path[0]
    if len(path) == 1:
        del fields[key]
        return True
    if not isinstance(fields[key], dict) or not remove_claim(fields[key], path[1:]):
        return False
    if not fields[key]:
        del fields[key]
    return True


def ownership_patch(obj):
    original = obj['metadata'].get('managedFields', [])
    updated = copy.deepcopy(original)
    removed = set()
    for entry in updated:
        if (entry.get('manager') != 'kubectl-patch' or entry.get('apiVersion') != 'apps/v1'
                or entry.get('subresource')):
            continue
        for path in PATHS:
            if remove_claim(entry.get('fieldsV1', {}), path):
                removed.add(tuple(path))
    if removed != {tuple(p) for p in PATHS}:
        raise ValueError('Expected kubectl-patch ownership of both chart labels was not found')
    updated = [e for e in updated if e.get('fieldsV1')]
    if not updated:
        raise ValueError('Refusing to clear all managedFields')
    return [
        {'op': 'test', 'path': '/metadata/uid', 'value': obj['metadata']['uid']},
        {'op': 'test', 'path': '/metadata/resourceVersion', 'value': obj['metadata']['resourceVersion']},
        {'op': 'test', 'path': '/metadata/managedFields', 'value': original},
        {'op': 'replace', 'path': '/metadata/managedFields', 'value': updated},
    ]


def unchanged(before, after):
    # The sole permitted difference is ownership metadata / resourceVersion.
    a = copy.deepcopy(before)
    b = copy.deepcopy(after)
    for obj in (a, b):
        obj['metadata'].pop('managedFields', None)
        obj['metadata'].pop('resourceVersion', None)
    if a != b:
        raise ValueError('Patch changes live values or object identity; refusing repair')


def run(args):
    p = subprocess.run(args, text=True, capture_output=True, timeout=30)
    if p.returncode:
        raise RuntimeError(p.stderr.strip() or 'Kubernetes/Helm command failed')
    return p.stdout


def kube(args, *parts):
    return ['kubectl', '--kubeconfig', args.kubeconfig, '--request-timeout=20s', *parts]


def prepare(args):
    if not supported_error(Path(args.error_file).read_text()):
        raise ValueError('Failure includes unknown fields, resources or managers; manual review required')
    obj = json.loads(run(kube(args, '-n', 'apps-zabbix', 'get', 'statefulset',
                              'zabbix-postgresql', '-o', 'json', '--show-managed-fields')))
    annotations = obj['metadata'].get('annotations', {})
    if (annotations.get('meta.helm.sh/release-name') != 'zabbix' or
            annotations.get('meta.helm.sh/release-namespace') != 'apps-zabbix'):
        raise ValueError('Unexpected StatefulSet Helm owner')
    history = json.loads(run(['helm', '--kubeconfig', args.kubeconfig, '-n', 'apps-zabbix',
                              'history', 'zabbix', '--max', '1', '-o', 'json']))
    if (not history or history[-1]['status'] != 'failed' or
            history[-1]['chart'] != 'zabbix-' + args.target_version):
        raise ValueError('Latest Helm failure is not for the requested chart version')
    manifest = run(['helm', '--kubeconfig', args.kubeconfig, '-n', 'apps-zabbix', 'get',
                    'manifest', 'zabbix', '--revision', str(history[-1]['revision'])])
    objects = []
    for doc in yaml.safe_load_all(manifest):
        if isinstance(doc, dict):
            objects.extend(doc.get('items', []) if doc.get('kind') == 'List' else [doc])
    desired = next(o for o in objects if o.get('kind') == 'StatefulSet'
                   and o['metadata']['name'] == 'zabbix-postgresql')
    target = 'zabbix-' + args.target_version
    if (desired['metadata']['labels'].get('helm.sh/chart') != target or
            desired['spec']['template']['metadata']['labels'].get('helm.sh/chart') != target):
        raise ValueError('Desired chart labels differ from the target version')
    patch = ownership_patch(obj)
    path = Path(args.plan)
    path.parent.mkdir(parents=True, exist_ok=True)
    before = path.parent / 'statefulset-before.json'
    before.write_text(json.dumps(obj, indent=2) + '\n')
    patch_file = path.parent / 'ownership-patch.json'
    patch_file.write_text(json.dumps(patch, indent=2) + '\n')
    command = kube(args, '-n', 'apps-zabbix', 'patch', 'statefulset', 'zabbix-postgresql',
                   '--type=json', '--patch-file', str(patch_file), '-o', 'json', '--show-managed-fields')
    simulated = json.loads(run(command + ['--dry-run=server']))
    unchanged(obj, simulated)
    plan = {'before': str(before), 'patch': patch,
            'beforeSha256': hashlib.sha256(before.read_bytes()).hexdigest(),
            'targetVersion': args.target_version}
    path.write_text(json.dumps(plan, indent=2) + '\n')
    print('Release kubectl-patch ownership of only the StatefulSet metadata and pod-template '
          'helm.sh/chart labels. Live label values, selectors, pod template, database image and '
          'PVC settings stay unchanged during this metadata repair. Then retry Fleet once '
          'so Helm can apply ' + target + '. The chart rollout may restart PostgreSQL.')


def apply(args):
    path = Path(args.apply)
    plan = json.loads(path.read_text())
    before_path = Path(plan['before'])
    if hashlib.sha256(before_path.read_bytes()).hexdigest() != plan['beforeSha256']:
        raise ValueError('Original snapshot changed; diagnose again')
    before = json.loads(before_path.read_text())
    if ownership_patch(before) != plan['patch']:
        raise ValueError('Plan contains unexpected ownership edits')
    patch_file = path.parent / 'approved-ownership-patch.json'
    patch_file.write_text(json.dumps(plan['patch']) + '\n')
    command = kube(args, '-n', 'apps-zabbix', 'patch', 'statefulset', 'zabbix-postgresql',
                   '--type=json', '--patch-file', str(patch_file), '-o', 'json', '--show-managed-fields')
    # Test the original resourceVersion again immediately before the write.
    unchanged(before, json.loads(run(command + ['--dry-run=server'])))
    after = json.loads(run(command))
    (path.parent / 'statefulset-after.json').write_text(json.dumps(after, indent=2) + '\n')
    unchanged(before, after)
    print('Confirmed: two chart-label ownership claims released; live values and StatefulSet UID unchanged')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--kubeconfig', required=True)
    parser.add_argument('--target-version')
    parser.add_argument('--error-file')
    parser.add_argument('--plan')
    parser.add_argument('--apply')
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.apply:
            apply(args)
        elif args.target_version and args.error_file and args.plan:
            prepare(args)
        else:
            parser.error('Specify --apply, or --target-version, --error-file and --plan')
        return 0
    except Exception as exc:
        print('Zabbix label repair stopped: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
