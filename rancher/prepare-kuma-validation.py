#!/usr/bin/env python3
"""Guardedly set Kuma's desired replicas to one before validation. Does not attach."""
import argparse
import datetime
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys

V = 'pvc-326def38-faba-49f6-8a78-ebf719227220'
R = V + '-r-9cdce044'


def get_arguments(resource, name=None, namespace='longhorn-system'):
    scope = ['-n', namespace] if namespace else ['-A']
    return ['get', resource] + ([name] if name else []) + scope + ['-o', 'json']


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--execute', action='store_true')
    ap.add_argument('--yes', action='store_true')
    args = ap.parse_args()
    if socket.gethostname().split('.')[0] != 'proxmox3':
        ap.error('Run on proxmox3')
    kube = ['kubectl', '--kubeconfig=/etc/rancher/k3s/k3s.yaml', '--request-timeout=15s']

    def call(command):
        p = subprocess.run(kube + command, capture_output=True, text=True, timeout=25)
        if p.returncode:
            raise RuntimeError('kubectl ' + ' '.join(command) + ': ' + p.stderr.strip())
        return json.loads(p.stdout)

    def get(resource, name=None, namespace='longhorn-system'):
        return call(get_arguments(resource, name, namespace))

    def assess():
        objects = {
            'volume': get('volumes.longhorn.io', V),
            'replicas': get('replicas.longhorn.io'),
            'engines': get('engines.longhorn.io'),
            'attachment': get('volumeattachments.longhorn.io', V),
            'bundle': get('bundles.fleet.cattle.io', 'uptime-kuma', 'fleet-local'),
            'deployment': get('deployment', 'uptime-kuma', 'apps-uptime-kuma'),
            'pods': get('pods', namespace=None),
            'pvcs': get('pvc', namespace=None),
            'kubernetes-attachments': get('volumeattachments.storage.k8s.io', namespace=None),
        }
        vol = objects['volume']
        assert vol['metadata']['uid'] == '7150b0cb-18c8-448f-9831-cefca486672c', 'Unexpected volume UID'
        assert not vol['metadata'].get('deletionTimestamp'), 'Volume terminating'
        assert vol['status']['state'] == 'detached', 'Volume not detached'
        assert not vol['spec'].get('nodeID') and not vol['status'].get('currentNodeID'), 'Volume has node'
        assert not objects['attachment']['spec'].get('attachmentTickets'), 'Attachment tickets exist'
        assert objects['bundle']['spec'].get('paused') is True, 'Kuma Fleet Bundle not paused'
        assert objects['deployment']['spec'].get('replicas', 1) == 0, 'Kuma not scaled to zero'
        claims = {(p['metadata']['namespace'], p['metadata']['name'])
                  for p in objects['pvcs']['items'] if p['spec'].get('volumeName') == V}
        assert claims, 'No bound PVC found'
        for pod in objects['pods']['items']:
            for volume in pod['spec'].get('volumes', []):
                claim = volume.get('persistentVolumeClaim', {}).get('claimName')
                assert (pod['metadata']['namespace'], claim) not in claims, 'PVC consumer pod exists'
        assert not any(a['spec'].get('source', {}).get('persistentVolumeName') == V
                       for a in objects['kubernetes-attachments']['items']), 'Kubernetes attachment exists'
        replicas = [r for r in objects['replicas']['items'] if r['spec'].get('volumeName') == V]
        assert len(replicas) == 1 and replicas[0]['metadata']['name'] == R, 'Unexpected replica inventory'
        replica = replicas[0]
        assert not replica['metadata'].get('deletionTimestamp'), 'Replica terminating'
        assert replica['spec'].get('nodeID') == 'proxmox3', 'Replica on wrong node'
        assert not replica['spec'].get('failedAt'), 'Replica has failure timestamp'
        assert replica['status'].get('currentState') == 'stopped', 'Replica running'
        assert replica['spec'].get('desireState') == 'stopped', 'Replica requested to run'
        engines = [e for e in objects['engines']['items'] if e['spec'].get('volumeName') == V]
        assert engines and all(e['status'].get('currentState') == 'stopped'
                               and e['spec'].get('desireState') == 'stopped' for e in engines), 'Engine not stopped'
        assert not os.path.lexists('/dev/longhorn/' + V), 'Local device exists'
        assert V not in Path('/proc/self/mountinfo').read_text(), 'Local mount exists'
        assert vol['spec']['numberOfReplicas'] in (1, 2), 'Unexpected desired replica count'
        return objects

    before = assess()
    old = before['volume']['spec']['numberOfReplicas']
    print(f'Guards passed. Desired replica count: {old} -> 1. Existing sole replica retained.')
    print('This step does not attach, mount, copy data, or start Kuma.')
    if old == 1 or not args.execute:
        print('No changes made.' if old == 1 else 'Dry run. Use --execute to apply.')
        return
    if not args.yes:
        if not (shutil.which('dialog') and sys.stdin.isatty() and sys.stdout.isatty()):
            raise RuntimeError('Interactive dialog unavailable; explicit --execute --yes required')
        if subprocess.run(['dialog', '--defaultno', '--yesno',
                           'Set Kuma desired replicas from 2 to 1 for controlled validation? '
                           'The sole existing replica remains. No attachment or data copy.', '10', '76']).returncode:
            return
    before = assess()
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    folder = Path('/var/tmp') / ('kuma-validation-preparation-' + stamp)
    folder.mkdir(mode=0o700)
    (folder / 'before.json').write_text(json.dumps(before, indent=2) + '\n')

    def log(event):
        with (folder / 'actions.jsonl').open('a') as f:
            f.write(json.dumps({'time': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                                'event': event}) + '\n')
            f.flush()
            os.fsync(f.fileno())
        print(event, flush=True)

    patch = [{'op': 'test', 'path': '/metadata/resourceVersion',
              'value': before['volume']['metadata']['resourceVersion']},
             {'op': 'test', 'path': '/spec/numberOfReplicas', 'value': 2},
             {'op': 'replace', 'path': '/spec/numberOfReplicas', 'value': 1}]
    print('Backup/action log:', folder)
    log('replica_count_2_to_1_requested')
    try:
        after = call(['-n', 'longhorn-system', 'patch', 'volumes.longhorn.io', V,
                      '--type=json', '-p', json.dumps(patch), '-o', 'json'])
        (folder / 'patch-response.json').write_text(json.dumps(after, indent=2) + '\n')
        verified = assess()
        assert verified['volume']['spec']['numberOfReplicas'] == 1, 'Count verification failed'
        (folder / 'after.json').write_text(json.dumps(verified, indent=2) + '\n')
        log('replica_count_1_confirmed_volume_still_detached')
    except Exception:
        log('failed_partial_change_possible_inspect_live_state')
        raise
    print('Keep count 1 until validation/recovery plan permits rebuilding; original count 2 saved.')


if __name__ == '__main__':
    if not __debug__:
        raise SystemExit('Do not run with python -O: safety assertions must remain enabled')
    try:
        main()
    except (AssertionError, RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        raise SystemExit(f'Refused/failed: {exc}')
