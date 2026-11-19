#!/usr/bin/env python3
"""Review, stop, verify or gradually restore cluster applications from saved baseline."""
import argparse
import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

BASELINE = '/var/tmp/kuma-recovery-maintenance-20261118T180953924876Z'
CONTROL_NS = {'cattle-capi-system', 'cattle-fleet-system', 'cattle-fleet-local-system', 'cattle-turtles-system'}
KEEP_NS = {'kube-system', 'longhorn-system', 'cert-manager'}


def selected(row):
    ns, name = row['namespace'], row['name']
    return ((ns.startswith('apps-') and ns != 'apps-adguard-home')
            or ns in CONTROL_NS or ns == 'cloudcasa-io'
            or (ns == 'cattle-system' and name in ('rancher', 'system-upgrade-controller')))


def database(row):
    return any(x in row['name'] for x in ('mariadb', 'postgresql', 'mongodb'))


def control(row):
    return row['namespace'] in CONTROL_NS or row['namespace'] == 'cattle-system'


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('mode', choices=['plan', 'stop', 'verify', 'restore'])
    ap.add_argument('--directory', type=Path, default=Path(BASELINE))
    ap.add_argument('--execute', action='store_true')
    ap.add_argument('--yes', action='store_true')
    ap.add_argument('--keep-longhorn-paused', action='store_true', help='During restore, retain the incident Longhorn Bundle pause until manager settings are reviewed')
    ap.add_argument('--keep-recovered-kuma', action='store_true', help='During restore only, preserve separately verified Kuma startup at one replica')
    args = ap.parse_args()
    if (args.keep_recovered_kuma or args.keep_longhorn_paused) and args.mode!='restore':
        ap.error('Recovery-preservation flags are only valid for restore')
    folder = args.directory
    if not (folder / 'inventory-complete').is_file():
        raise RuntimeError('Complete inventory marker required')
    baseline = json.loads((folder / 'shutdown-baseline.json').read_text())
    rows = baseline['applicationWorkloads'] + baseline['otherWorkloadsForClassification']
    unknown = [r for r in rows if not selected(r) and not (
        r['namespace'] in KEEP_NS or r['namespace'] == 'apps-adguard-home'
        or (r['namespace'] == 'cattle-system' and r['name'] == 'rancher-webhook')
        or r['kind'] in ('CronJob', 'Job'))]
    if unknown:
        raise RuntimeError('Unclassified workloads: ' + json.dumps(unknown))
    unsupported = [r for r in rows if selected(r) and r['kind'] in ('DaemonSet', 'HorizontalPodAutoscaler')]
    if unsupported:
        raise RuntimeError('Explicit shutdown handling required for: ' + json.dumps(unsupported))
    workloads = [r for r in rows if selected(r) and r['kind'] in ('Deployment', 'StatefulSet')]
    schedules = [r for r in rows if r['kind'] == 'CronJob' or (r['kind'] == 'Job' and not r.get('complete'))]
    operations = []
    for r in workloads:
        operations.append(dict(r, field='replicas', original=r['replicas'], maintenance=0))
    for r in schedules:
        operations.append(dict(r, field='suspend', original=r['suspend'], maintenance=True))
    for b in baseline['bundles']:
        operations.append(dict(b, kind='bundles.fleet.cattle.io', field='paused',
                               original=b['paused'], maintenance=True))
    recurring = json.loads((folder / 'recurringjobs.longhorn.io.json').read_text())['items']
    if recurring:
        # A valid annual schedule outside this short maintenance window.
        # Never run this incident-specific plan close to January 1.
        now = datetime.datetime.now(datetime.timezone.utc)
        if now.month in (12, 1):
            raise RuntimeError('Annual backup maintenance schedule needs review near January 1')
    for obj in recurring:
        operations.append({'namespace': obj['metadata']['namespace'], 'name': obj['metadata']['name'],
                           'uid': obj['metadata']['uid'], 'kind': 'recurringjobs.longhorn.io',
                           'field': 'cron', 'original': obj['spec']['cron'], 'maintenance': '0 0 1 1 *'})
    planpath = folder / 'execution-plan.json'
    planned = {'baseline': str(folder), 'operations': operations,
               'exception': 'AdGuard DNS and service LB retained as essential DNS dependency; verify DNS before copy.',
               'limitations': 'No data copying. Shutdown verification alone is not storage/IO/copy readiness.'}
    if planpath.exists() and json.loads(planpath.read_text()) != planned:
        previous = json.loads(planpath.read_text())
        additions = [r for r in operations if r['field'] == 'cron']
        if previous != dict(planned, operations=[r for r in operations if r['field'] != 'cron']) or not additions:
            raise RuntimeError('Saved execution plan differs; do not replace restoration state')
        # Append only baseline-derived RecurringJob controls; preserve previous plan verbatim.
        backup = folder / 'execution-plan-before-recurring-schedule.json'
        if backup.exists():
            raise RuntimeError('Prior recurring-plan backup exists; inspect before updating')
        backup.write_text(planpath.read_text())
        planpath.write_text(json.dumps(planned, indent=2) + '\n')
        print('Execution plan extended with saved RecurringJob schedule control; previous plan retained.')
    if not planpath.exists():
        planpath.write_text(json.dumps(planned, indent=2) + '\n')
    print('Baseline:', folder)
    print('Stop workload counts:', len(workloads), '; suspend schedules/jobs:', len(schedules),
          '; pause Bundles:', len(baseline['bundles']))
    print(planned['exception'])
    if args.mode == 'plan':
        for r in operations:
            if r['field']=='cron': print('BACKUP SCHEDULE', r['name'], r['original'], '->', r['maintenance'])
        for r in workloads:
            print('STOP', r['namespace'], r['kind'], r['name'], 'saved replicas', r['replicas'])
        return
    kube = ['kubectl', '--kubeconfig=/etc/rancher/k3s/k3s.yaml', '--request-timeout=20s']

    def call(argv):
        p = subprocess.run(kube + argv, capture_output=True, text=True, timeout=30)
        if p.returncode:
            raise RuntimeError('kubectl ' + ' '.join(argv[:4]) + ': ' + p.stderr.strip())
        return json.loads(p.stdout)

    def get(row):
        obj = call(['get', row['kind'], row['name'], '-n', row['namespace'], '-o', 'json'])
        if obj['metadata']['uid'] != row['uid']:
            raise RuntimeError('UID changed: ' + row['namespace'] + '/' + row['name'])
        return obj

    def record(event, **fields):
        line = json.dumps(dict(time=datetime.datetime.now(datetime.timezone.utc).isoformat(), event=event, **fields))
        with (folder / 'maintenance-actions.jsonl').open('a') as f:
            f.write(line + '\n'); f.flush(); os.fsync(f.fileno())
        print(line, flush=True)

    def mutate(row, restore=False):
        obj = get(row)
        default = 1 if row['field'] == 'replicas' else False
        current = obj['spec'].get(row['field'], default)
        target = row['original'] if restore else row['maintenance']
        if current == target:
            return
        expected = row['maintenance'] if restore else row['original']
        if current != expected:
            raise RuntimeError('Unexpected live value for ' + row['name'] + ': ' + str(current))
        patch = [{'op': 'test', 'path': '/metadata/uid', 'value': row['uid']},
                 {'op': 'test', 'path': '/metadata/resourceVersion', 'value': obj['metadata']['resourceVersion']},
                 {'op': 'add', 'path': '/spec/' + row['field'], 'value': target}]
        record('change_requested', namespace=row['namespace'], name=row['name'], field=row['field'], previous=current, requested=target)
        call(['patch', row['kind'], row['name'], '-n', row['namespace'], '--type=json', '-p', json.dumps(patch), '-o', 'json'])
        if get(row)['spec'].get(row['field'], default) != target:
            raise RuntimeError('Change verification failed for ' + row['name'])
        record('change_confirmed', namespace=row['namespace'], name=row['name'], field=row['field'], confirmed=target)

    stop_ns = {r['namespace'] for r in workloads}

    def remaining(namespaces):
        pods = call(['get', 'pods', '-A', '-o', 'json'])['items']
        return [p['metadata']['namespace'] + '/' + p['metadata']['name'] for p in pods
                if (p['metadata']['namespace'] in namespaces
                    or (p['metadata']['namespace'].startswith('apps-') and p['metadata']['namespace'] != 'apps-adguard-home'))
                and (p.get('status', {}).get('phase') not in ('Succeeded', 'Failed')
                     or p['metadata'].get('deletionTimestamp'))
                and not (p['metadata']['namespace'] == 'cattle-system'
                         and any(o.get('kind') == 'ReplicaSet' and o['name'].startswith('rancher-webhook-')
                                 for o in p['metadata'].get('ownerReferences', [])))]

    def wait_stopped(namespaces):
        deadline = time.monotonic() + 180
        while True:
            active = remaining(namespaces)
            if not active:
                return
            if time.monotonic() >= deadline:
                raise RuntimeError('Pods still present; no copying: ' + ', '.join(active))
            print('Waiting for', len(active), 'pods to stop', flush=True)
            time.sleep(5)

    def deferred_backup_guard(cron):
        matching = [r for r in operations if r['field']=='cron'
                    and r['namespace']==cron['metadata']['namespace']
                    and r['name']==cron['metadata']['name']]
        if len(matching)!=1:
            return False
        row=matching[0]
        parent=get(row)
        owned=any(o.get('kind')=='RecurringJob' and o.get('uid')==row['uid']
                  for o in cron['metadata'].get('ownerReferences',[]))
        return (owned and parent['spec'].get('cron')==row['maintenance']
                and cron['spec'].get('schedule')==row['maintenance'])

    def verify():
        for row in operations:
            obj = get(row)
            default = 1 if row['field'] == 'replicas' else False
            if obj['spec'].get(row['field'], default) != row['maintenance']:
                if row['kind']=='CronJob' and row['field']=='suspend' and deferred_backup_guard(obj):
                    record('backup_suspension_not_persistent_deferred_schedule_confirmed',
                           namespace=row['namespace'], name=row['name'], requestedSuspend=True,
                           actualSuspend=obj['spec'].get('suspend',False),
                           confirmedSchedule=obj['spec']['schedule'])
                    continue
                raise RuntimeError('Maintenance state drift: ' + row['namespace'] + '/' + row['name'] + ' field=' + row['field'] + ' expected=' + str(row['maintenance']) + ' actual=' + str(obj['spec'].get(row['field'], default)))
            if row['field'] == 'replicas' and any(obj.get('status', {}).get(k, 0)
                    for k in ('replicas', 'readyReplicas', 'availableReplicas', 'terminatingReplicas')):
                raise RuntimeError('Workload replicas not stopped: ' + row['name'])
        for kind in ('deployments', 'statefulsets', 'daemonsets', 'horizontalpodautoscalers'):
            known = {(r['namespace'], r['name'], r['uid']) for r in rows if r['kind'].lower() ==
                     {'deployments':'deployment','statefulsets':'statefulset','daemonsets':'daemonset','horizontalpodautoscalers':'horizontalpodautoscaler'}[kind]}
            for obj in call(['get', kind, '-A', '-o', 'json'])['items']:
                row = dict(namespace=obj['metadata']['namespace'], name=obj['metadata']['name'])
                if selected(row) and (row['namespace'], row['name'], obj['metadata']['uid']) not in known:
                    raise RuntimeError('New application/control workload since baseline: ' + str(row))
        active = remaining(stop_ns)
        if active:
            raise RuntimeError('Application/control pods remain: ' + ', '.join(active))
        for pod in call(['get','pods','-A','-o','json'])['items']:
            if any(o.get('kind')=='Job' for o in pod['metadata'].get('ownerReferences',[])):
                if pod.get('status',{}).get('phase') not in ('Succeeded','Failed') or pod['metadata'].get('deletionTimestamp'):
                    raise RuntimeError('Job pod still present: '+pod['metadata']['namespace']+'/'+pod['metadata']['name'])
        knowncron={(r['namespace'],r['name'],r['uid']) for r in schedules if r['kind']=='CronJob'}
        for cron in call(['get','cronjobs','-A','-o','json'])['items']:
            key=(cron['metadata']['namespace'],cron['metadata']['name'],cron['metadata']['uid'])
            if key not in knowncron or (cron['spec'].get('suspend') is not True and not deferred_backup_guard(cron)):
                raise RuntimeError('New or unsuspended CronJob: '+str(key))
        # Include active backup/maintenance Jobs outside application namespaces.
        knownjobs = {(r['namespace'],r['name'],r['uid']) for r in schedules if r['kind']=='Job'}
        for job in call(['get','jobs','-A','-o','json'])['items']:
            activejob = job.get('status', {}).get('active',0)
            if activejob:
                raise RuntimeError('Active Job remains: ' + job['metadata']['namespace'] + '/' + job['metadata']['name'])
            key=(job['metadata']['namespace'],job['metadata']['name'],job['metadata']['uid'])
            done=any(c.get('status')=='True' and c.get('type') in ('Complete','Failed') for c in job.get('status',{}).get('conditions',[]))
            if not done and key not in knownjobs:
                raise RuntimeError('New unfinished Job since baseline: ' + str(key))
        for row in operations:
            if row['field']=='cron':
                generated=call(['get','cronjob',row['name'],'-n',row['namespace'],'-o','json'])
                if not deferred_backup_guard(generated):
                    raise RuntimeError('Longhorn parent/generated schedule or ownership drift: '+row['name'])
        record('shutdown_verified_not_yet_copy_ready')

    if args.mode == 'verify':
        verify(); return
    if not args.execute:
        print('Dry run: use --execute for', args.mode); return
    if not args.yes:
        if not (shutil.which('dialog') and sys.stdin.isatty() and sys.stdout.isatty()):
            raise RuntimeError('Interactive dialog unavailable; explicit --execute --yes required')
        if subprocess.run(['dialog','--defaultno','--yesno',
                           args.mode.upper() + ' cluster applications using saved counts? AdGuard DNS and essential infrastructure stay running. No copy.',
                           '10','76']).returncode:
            return
    if args.mode == 'stop':
        # Check all identities/current values before any writes; accept resumed maintenance values.
        for row in operations:
            obj=get(row); value=obj['spec'].get(row['field'],1 if row['field']=='replicas' else False)
            if value not in (row['original'],row['maintenance']):
                raise RuntimeError('Baseline drift: ' + row['name'])
        record('shutdown_begin')
        # Stop controllers that can overwrite Bundle pause or application scale first.
        controllers = [r for r in operations if r['field']=='replicas' and control(r)]
        controllers.sort(key=lambda r:(0 if r['name']=='rancher' else 1 if r['namespace'] in ('cattle-capi-system','cattle-turtles-system') else 2 if r['name']!='fleet-agent' else 3))
        for row in controllers: mutate(row)
        # Pause Bundles before changing Longhorn-owned schedules.
        for row in operations:
            if row['field']=='paused': mutate(row)
        for row in operations:
            if row['field']=='cron':
                mutate(row)
                deadline=time.monotonic()+90
                while True:
                    generated=call(['get','cronjob',row['name'],'-n',row['namespace'],'-o','json'])
                    if generated['spec']['schedule']==row['maintenance']: break
                    if time.monotonic()>deadline: raise RuntimeError('RecurringJob schedule not applied: '+row['name'])
                    time.sleep(3)
                record('generated_backup_schedule_confirmed',name=row['name'],schedule=row['maintenance'])
        # Suspend after Longhorn has applied its generated schedule.
        for row in operations:
            if row['field']=='suspend': mutate(row)
        for row in operations:
            if row['field']=='replicas' and not control(row) and not database(row): mutate(row)
        # Wait per frontend workload so database pods in shared namespaces may remain until next phase.
        for row in operations:
            if row['field']=='replicas' and not database(row):
                deadline=time.monotonic()+180
                while any(get(row).get('status',{}).get(k,0) for k in ('replicas','terminatingReplicas')):
                    if time.monotonic()>deadline: raise RuntimeError('Frontend/control still terminating: '+row['name'])
                    time.sleep(5)
        for row in operations:
            if row['field']=='replicas' and database(row): mutate(row)
        wait_stopped(stop_ns)
        verify()
        print('Applications stopped. Copy still requires both-host I/O/storage/readiness checks and logged cap.')
    else:
        record('restore_begin')
        work=[r for r in operations if r['field']=='replicas' and not control(r)]
        work.sort(key=lambda r:(not database(r),r['namespace'],r['name']))
        for row in work:
            # Preserve zeros except an explicitly requested, independently verified Kuma recovery.
            if row['original']==0:
                live=get(row)
                if live['spec'].get('replicas',1)!=0:
                    if not (args.keep_recovered_kuma and row['namespace']=='apps-uptime-kuma'
                            and row['name']=='uptime-kuma' and live['spec'].get('replicas')==1
                            and live.get('status',{}).get('readyReplicas')==1
                            and live['spec']['template']['spec'].get('nodeSelector',{}).get('kubernetes.io/hostname')=='proxmox3'):
                        raise RuntimeError('Originally zero workload drifted: '+row['name'])
                    markers=list(Path('/var/tmp').glob('kuma-service-restore-*/service-started.json'))
                    if not markers or not any(json.loads(p.read_text()).get('node')=='proxmox3' for p in markers):
                        raise RuntimeError('No verified separate Kuma startup marker')
                    volume=call(['get','volumes.longhorn.io','pvc-326def38-faba-49f6-8a78-ebf719227220','-n','longhorn-system','-o','json'])
                    if volume['status'].get('state')!='attached' or volume['status'].get('currentNodeID')!='proxmox3':
                        raise RuntimeError('Recovered Kuma storage state drifted')
                    record('deliberately_recovered_kuma_preserved_at_one_replica')
                continue
            psi=Path('/proc/pressure/io').read_text()
            metrics={line.split()[0]:float(line.split()[1].split('=')[1]) for line in psi.splitlines()}
            if metrics.get('some',100)>10 or metrics.get('full',100)>2:
                raise RuntimeError('High local I/O pressure; restoration paused: '+psi)
            mutate(row,True)
            p=subprocess.run(kube+['rollout','status',row['kind']+'/'+row['name'],'-n',row['namespace'],'--timeout=120s'],timeout=130)
            if p.returncode: raise RuntimeError('Restore rollout failed: '+row['name'])
            record('workload_restored_ready',namespace=row['namespace'],name=row['name']); time.sleep(5)
        # Restore original recurring schedules before re-enabling generated CronJobs.
        for row in operations:
            if row['field']=='cron':
                mutate(row,True)
                deadline=time.monotonic()+90
                while True:
                    generated=call(['get','cronjob',row['name'],'-n',row['namespace'],'-o','json'])
                    if generated['spec']['schedule']==row['original']: break
                    if time.monotonic()>deadline: raise RuntimeError('Original backup schedule not applied: '+row['name'])
                    time.sleep(3)
        for row in operations:
            if row['field'] not in ('replicas','cron'):
                if (args.keep_longhorn_paused and row['field']=='paused'
                        and row['namespace']=='fleet-local' and row['name']=='longhorn'):
                    if not get(row)['spec'].get('paused'):
                        raise RuntimeError('Longhorn incident pause drifted before restoration')
                    record('longhorn_bundle_kept_paused_pending_incident_setting_review',savedOriginalPaused=row['original'])
                    continue
                mutate(row,True)
        for row in sorted([r for r in operations if r['field']=='replicas' and control(r)],key=lambda r:(r['name']=='rancher',r['name']=='fleet-agent')):
            mutate(row,True)
        record('saved_values_restored_reconciliation_and_final_health_require_verification')


if __name__=='__main__':
    try: main()
    except (RuntimeError,OSError,ValueError,KeyError,subprocess.SubprocessError) as exc:
        raise SystemExit('Stopped/refused; partial changes may remain, inspect maintenance-actions.jsonl: '+str(exc))
