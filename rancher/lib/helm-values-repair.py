#!/usr/bin/python3
"""Plan and apply the known container.fsGroup placement correction, offline."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import yaml


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def plan_repair(file):
    text = Path(file).read_text()
    values = yaml.safe_load(text) or {}
    security = values.get('securityContext', {})
    container = security.get('container', {})
    if 'fsGroup' not in container:
        return None
    group = container['fsGroup']
    if type(group) is not int or group < 0:
        raise ValueError('fsGroup must be a non-negative integer; review values manually')
    pod = security.get('pod', {})
    if 'fsGroup' in pod and pod['fsGroup'] != group:
        raise ValueError('Pod and container fsGroup differ; refusing to choose a group')
    return {'kind': 'container-fsgroup', 'file': str(Path(file).resolve()),
            'sha256': digest(text), 'fsGroup': group}


def apply_plan(plan, directory):
    file = Path(plan['file'])
    text = file.read_text()
    if digest(text) != plan['sha256']:
        raise ValueError('Values changed after the repair prompt; diagnose again')
    if plan_repair(file) != plan:
        raise ValueError('Repair plan no longer matches values')
    before = yaml.safe_load(text)
    after = copy.deepcopy(before)
    after['securityContext']['container'].pop('fsGroup')
    after['securityContext'].setdefault('pod', {})['fsGroup'] = plan['fsGroup']
    backup = Path(directory) / 'values-before.yaml'
    backup.write_text(text)
    os.chmod(backup, 0o600)
    temporary = file.with_name(file.name + '.schema-repair.tmp')
    with temporary.open('x') as handle:
        handle.write(yaml.safe_dump(after, sort_keys=False))
    os.chmod(temporary, file.stat().st_mode & 0o777)
    os.replace(temporary, file)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--values')
    parser.add_argument('--plan')
    parser.add_argument('--apply')
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.apply:
            path = Path(args.apply)
            apply_plan(json.loads(path.read_text()), path.parent)
            return 0
        if not args.values or not args.plan:
            parser.error('Diagnosis requires --values and --plan')
        plan = plan_repair(args.values)
        if plan is None:
            return 1
        Path(args.plan).write_text(json.dumps(plan, indent=2) + '\n')
        print('Move fsGroup=' + str(plan['fsGroup']) +
              ' from securityContext.container to securityContext.pod. '
              'Keep its value, container UID/GID and all storage definitions unchanged.')
        return 0
    except Exception as exc:
        print('Schema repair stopped: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
