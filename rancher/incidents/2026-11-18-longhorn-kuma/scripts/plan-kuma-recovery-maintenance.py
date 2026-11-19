#!/usr/bin/env python3
"""Save a fresh cluster-wide shutdown baseline for Kuma recovery. No cluster writes."""
import argparse
import datetime
import json
from pathlib import Path
import subprocess


def compact_summary(folder):
    if not (folder / 'inventory-complete').is_file():
        raise RuntimeError('Inventory completion marker absent')
    baseline = json.loads((folder / 'shutdown-baseline.json').read_text())
    print('Baseline:', folder)
    print('WORKLOADS (replicas/schedules are original restoration values)')
    for row in baseline['applicationWorkloads'] + baseline['otherWorkloadsForClassification']:
        if row['kind'] == 'Job' and row.get('complete'):
            continue
        values = {k: v for k, v in row.items()
                  if k not in ('namespace', 'kind', 'name', 'uid', 'applicationNamespace')}
        print(row['namespace'] + '/' + row['kind'] + '/' + row['name'], json.dumps(values))
    print('PAUSED BUNDLES:', ', '.join(b['namespace'] + '/' + b['name']
                                      for b in baseline['bundles'] if b['paused']) or '<none>')
    for resource in ['gitrepos.fleet.cattle.io', 'helmcharts.helm.cattle.io',
                     'recurringjobs.longhorn.io']:
        items = json.loads((folder / (resource + '.json')).read_text())['items']
        print('CONTROLS:', resource)
        for obj in items:
            spec = obj['spec']
            selected = {k: spec[k] for k in
                        ('paused', 'suspend', 'targetNamespace', 'schedule', 'task', 'groups', 'concurrency')
                        if k in spec}
            print(obj['metadata'].get('namespace', '') + '/' + obj['metadata']['name'], json.dumps(selected))
    print('ACTIVE UNMANAGED PODS:', json.dumps({ns: v['unmanaged']
          for ns, v in baseline['podNamespaces'].items() if v['unmanaged']}))
    print('Read-only saved inventory summary. No API calls or cluster changes.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--summary', action='store_true', help='Summarize latest complete saved baseline without API calls')
    parser.add_argument('--directory', type=Path, help='Saved directory for --summary')
    args = parser.parse_args()
    if args.summary:
        candidates = sorted(p for p in Path('/var/tmp').glob('kuma-recovery-maintenance-*')
                            if (p / 'inventory-complete').is_file())
        folder = args.directory or (candidates[-1] if candidates else None)
        if folder is None:
            raise RuntimeError('No complete saved maintenance inventory found')
        compact_summary(folder)
        return
    if args.directory:
        parser.error('--directory requires --summary')

    folder = Path('/var/tmp') / ('kuma-recovery-maintenance-' + datetime.datetime.now(
        datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    folder.mkdir(mode=0o700)
    kube = ['kubectl', '--kubeconfig=/etc/rancher/k3s/k3s.yaml', '--request-timeout=20s']
    kinds = ['deployments', 'statefulsets', 'daemonsets', 'cronjobs', 'jobs',
             'pods', 'horizontalpodautoscalers', 'bundles.fleet.cattle.io',
             'gitrepos.fleet.cattle.io', 'helmcharts.helm.cattle.io',
             'nodes', 'nodes.longhorn.io', 'volumes.longhorn.io',
             'replicas.longhorn.io', 'instancemanagers.longhorn.io',
             'recurringjobs.longhorn.io', 'persistentvolumeclaims']
    data = {}
    for kind in kinds:
        command = kube + ['get', kind, '-A', '-o', 'json']
        p = subprocess.run(command, capture_output=True, text=True, timeout=30)
        if p.returncode:
            raise RuntimeError(f'Incomplete inventory: {kind}: {p.stderr.strip()}; do not use this baseline')
        data[kind] = json.loads(p.stdout)
        (folder / (kind + '.json')).write_text(json.dumps(data[kind], indent=2) + '\n')
    workloads = []
    for kind in ['deployments', 'statefulsets', 'daemonsets', 'cronjobs', 'jobs', 'horizontalpodautoscalers']:
        for obj in data[kind]['items']:
            meta, spec = obj['metadata'], obj['spec']
            ns = meta['namespace']
            row = {'namespace': ns, 'kind': obj['kind'], 'name': meta['name'], 'uid': meta['uid']}
            row['applicationNamespace'] = ns.startswith('apps-')
            if kind in ('deployments', 'statefulsets'):
                row['replicas'] = spec.get('replicas', 1)
            elif kind in ('cronjobs', 'jobs'):
                row['suspend'] = spec.get('suspend', False)
                if kind == 'jobs':
                    row['active'] = obj.get('status', {}).get('active', 0)
                    row['complete'] = any(c.get('type') == 'Complete' and c.get('status') == 'True'
                                          for c in obj.get('status', {}).get('conditions', []))
            elif kind == 'daemonsets':
                row['currentScheduled'] = obj.get('status', {}).get('currentNumberScheduled')
            else:
                row['scaleTargetRef'] = spec.get('scaleTargetRef')
            workloads.append(row)
    bundles = [{'namespace': x['metadata']['namespace'], 'name': x['metadata']['name'],
                'uid': x['metadata']['uid'], 'paused': x['spec'].get('paused', False),
                'targets': x['spec'].get('targets', [])}
               for x in data['bundles.fleet.cattle.io']['items']]
    pod_groups = {}
    for pod in data['pods']['items']:
        ns = pod['metadata']['namespace']
        group = pod_groups.setdefault(ns, {'pods': 0, 'active': 0, 'unmanaged': []})
        group['pods'] += 1
        if pod.get('status', {}).get('phase') not in ('Succeeded', 'Failed'):
            group['active'] += 1
            if not pod['metadata'].get('ownerReferences'):
                group['unmanaged'].append(pod['metadata']['name'])
    summary = {'directory': str(folder), 'inventoryComplete': True,
               'applicationWorkloads': [w for w in workloads if w['applicationNamespace']],
               'otherWorkloadsForClassification': [w for w in workloads if not w['applicationNamespace']],
               'bundles': bundles, 'podNamespaces': pod_groups,
               'reviewRequired': [
                   'Classify applications outside apps-* and essential infrastructure exceptions (including DNS).',
                   'Map every application to Fleet/GitOps/Helm/operator reconciliation; pause before scales.',
                   'Plan HPA, DaemonSet, active Job and unmanaged-pod handling; suspend schedules and backup jobs.',
                   'Save changed reconciliation/autoscaling settings before mutations; frontend shutdown before databases.',
                   'Verify actual application processes/pods stop cluster-wide before copying; preserve prior zeros.',
                   'Check API/etcd readiness, both storage hosts I/O PSI, k3s restart stability and physical pool headroom.',
                   'Prepare bounded copy with live confirmed cap, cap-change record and logs; no copy authorized by inventory.',
                   'Restore databases then applications gradually from this baseline after verification; preserve prior pauses.']}
    (folder / 'shutdown-baseline.json').write_text(json.dumps(summary, indent=2) + '\n')
    # The marker is written last; partial directories never become valid plans.
    (folder / 'inventory-complete').write_text('Complete API inventory; shutdown classification still requires review.\n')
    print(json.dumps(summary, indent=2))
    print('No cluster changes made. Private full objects saved; do not publish them.')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        raise SystemExit(f'Inventory failed; do not begin shutdown/copy: {exc}')
