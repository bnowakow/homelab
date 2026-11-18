#!/usr/bin/env python3
"""Guarded placement/manual-ticket handoff and one-replica Kuma startup on proxmox3."""
import argparse
import datetime
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

V='pvc-326def38-faba-49f6-8a78-ebf719227220'
TICKET='longhorn-api-kuma-validation-20261118'
REPAIR=Path('/var/tmp/kuma-original-repair-20261118T195344656712Z')


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--execute',action='store_true')
    ap.add_argument('--yes',action='store_true')
    args=ap.parse_args()
    if os.geteuid()!=0 or socket.gethostname().split('.')[0]!='proxmox3':raise RuntimeError('Run with sudo on proxmox3')
    if not json.loads((REPAIR/'original-filesystem-validated.json').read_text()).get('readOnlyCheckPassed'):
        raise RuntimeError('Original filesystem validation missing')
    if list(Path('/var/tmp').glob('kuma-service-restore-*/handoff-started.json')):
        raise RuntimeError('Handoff already attempted; inspect saved state before retrying')
    kube=['kubectl','--kubeconfig=/etc/rancher/k3s/k3s.yaml','--request-timeout=15s']
    def call(argv):
        p=subprocess.run(kube+argv,capture_output=True,text=True,timeout=25)
        if p.returncode:raise RuntimeError(p.stderr.strip())
        return json.loads(p.stdout)
    def get(kind,name,namespace='longhorn-system'):
        return call(['get',kind,name,'-n',namespace,'-o','json'])
    def patch(kind,obj,ops,namespace):
        return call(['patch',kind,obj['metadata']['name'],'-n',namespace,'--type=json','-p',json.dumps([
            {'op':'test','path':'/metadata/uid','value':obj['metadata']['uid']},
            {'op':'test','path':'/metadata/resourceVersion','value':obj['metadata']['resourceVersion']},*ops]),'-o','json'])
    spec=importlib.util.spec_from_file_location('source_guard',Path(__file__).with_name('check-kuma-filesystem.py'))
    helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
    device,unmounted,evidence=helper.source_guard()
    deployment=get('deployment','uptime-kuma','apps-uptime-kuma')
    placement=deployment['spec']['template']['spec']
    selector=dict(placement.get('nodeSelector',{}));selector['kubernetes.io/hostname']='proxmox3'
    ops=[{'op':'add','path':'/spec/template/spec/nodeSelector','value':selector}]
    if placement.get('affinity',{}).get('podAffinity') is not None:
        ops.append({'op':'remove','path':'/spec/template/spec/affinity/podAffinity'})
    print('Plan: keep Fleet paused; pin Kuma to proxmox3, remove required podAffinity; remove only validation ticket; verify detach; start 1 replica.')
    print(json.dumps({'placementPatch':ops,'ticketToRemove':TICKET,'replicas':1},indent=2))
    print('Kuma will run normally and may resume configured checks/notifications. Other applications remain stopped.')
    if not args.execute:print('Dry run. Use --execute for handoff/startup.');return
    if not args.yes:
        if not (shutil.which('dialog') and sys.stdin.isatty() and sys.stdout.isatty()):raise RuntimeError('Interactive dialog unavailable; explicit --execute --yes required')
        if subprocess.run(['dialog','--defaultno','--yesno','Hand repaired Kuma volume to CSI on proxmox3 and start ONE Kuma replica? '
            'Fleet stays paused; normal checks/notifications may resume. Other applications remain stopped.', '11','76']).returncode:return
    p=subprocess.run([sys.executable,str(Path(__file__).with_name('kuma-recovery-maintenance.py')),'verify'],capture_output=True,text=True,timeout=180)
    if p.returncode:raise RuntimeError('Maintenance drift: '+p.stdout+p.stderr)
    helper.source_guard()
    current=get('deployment','uptime-kuma','apps-uptime-kuma')
    if current['metadata']['resourceVersion']!=deployment['metadata']['resourceVersion']:
        raise RuntimeError('Deployment changed during review; repeat dry-run')
    folder=Path('/var/tmp')/('kuma-service-restore-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    folder.mkdir(mode=0o700)
    (folder/'deployment-before.json').write_text(json.dumps(deployment,indent=2)+'\n')
    (folder/'handoff-started.json').write_text(json.dumps({'time':datetime.datetime.now(datetime.timezone.utc).isoformat(),'volume':V,'node':'proxmox3'},indent=2)+'\n')
    def log(event,**fields):
        row=dict(time=datetime.datetime.now(datetime.timezone.utc).isoformat(),event=event,**fields)
        with (folder/'actions.jsonl').open('a') as f:f.write(json.dumps(row)+'\n');f.flush();os.fsync(f.fileno())
        print(json.dumps(row),flush=True)
    print('Handoff evidence:',folder,flush=True)
    log('placement_requested')
    placed=patch('deployment',deployment,ops,'apps-uptime-kuma')
    if placed['spec'].get('replicas',1)!=0 or placed['spec']['template']['spec'].get('nodeSelector',{}).get('kubernetes.io/hostname')!='proxmox3':
        raise RuntimeError('Placement/zero replica verification failed')
    log('placement_confirmed')
    unmounted()
    va=get('volumeattachments.longhorn.io',V)
    if set(va['spec'].get('attachmentTickets',{}))!={TICKET}:raise RuntimeError('Unexpected attachment tickets')
    (folder/'attachment-before.json').write_text(json.dumps(va,indent=2)+'\n')
    log('validation_ticket_removal_requested')
    patch('volumeattachments.longhorn.io',va,[{'op':'remove','path':'/spec/attachmentTickets/'+TICKET}],'longhorn-system')
    deadline=time.monotonic()+120
    while True:
        vol=get('volumes.longhorn.io',V);va=get('volumeattachments.longhorn.io',V)
        if va['spec'].get('attachmentTickets'):raise RuntimeError('New attachment ticket during detach; inspect before proceeding')
        if vol['status']['state']=='detached' and not vol['status'].get('currentNodeID') and not vol['spec'].get('nodeID') and not os.path.lexists('/dev/longhorn/'+V):break
        if time.monotonic()>deadline:raise RuntimeError('Normal detach/device removal timed out; no force detach')
        time.sleep(3)
    log('normal_detach_and_device_removal_confirmed')
    bundle=get('bundles.fleet.cattle.io','uptime-kuma','fleet-local')
    if not bundle['spec'].get('paused'):raise RuntimeError('Kuma Bundle unpaused')
    deployment=get('deployment','uptime-kuma','apps-uptime-kuma')
    if deployment['spec'].get('replicas',1)!=0 or deployment['spec']['template']['spec'].get('nodeSelector',{}).get('kubernetes.io/hostname')!='proxmox3':raise RuntimeError('Placement/scale drift')
    log('kuma_start_requested',replicas=1)
    patch('deployment',deployment,[{'op':'replace','path':'/spec/replicas','value':1}],'apps-uptime-kuma')
    p=subprocess.run(kube+['rollout','status','deployment/uptime-kuma','-n','apps-uptime-kuma','--timeout=180s'],timeout=190)
    if p.returncode:raise RuntimeError('Kuma rollout not ready; preserve logs, no blind scaling/force attach')
    pods=call(['get','pods','-n','apps-uptime-kuma','-o','json'])['items']
    consumers=[p for p in pods if any(v.get('persistentVolumeClaim',{}).get('claimName')=='uptime-kuma-config-longhorn-rwo' for v in p['spec'].get('volumes',[])) and not p['metadata'].get('deletionTimestamp')]
    if len(consumers)!=1 or consumers[0]['spec'].get('nodeName')!='proxmox3':raise RuntimeError('Unexpected Kuma consumer/placement')
    vol=get('volumes.longhorn.io',V)
    if vol['status']['state']!='attached' or vol['status'].get('currentNodeID')!='proxmox3':raise RuntimeError('Kuma attachment verification failed')
    (folder/'pod-after.json').write_text(json.dumps(consumers[0],indent=2)+'\n')
    (folder/'volume-after.json').write_text(json.dumps(vol,indent=2)+'\n')
    (folder/'service-started.json').write_text(json.dumps({'time':datetime.datetime.now(datetime.timezone.utc).isoformat(),'pod':consumers[0]['metadata']['name'],'node':'proxmox3','fleetPaused':True,'applicationDataValidationPending':True},indent=2)+'\n')
    log('kuma_ready_on_proxmox3',pod=consumers[0]['metadata']['name'])
    print('Verify Kuma login/data/notifications and live logs before restoring unrelated apps. Fleet remains paused.')


if __name__=='__main__':
    try:main()
    except (RuntimeError,OSError,ValueError,KeyError,subprocess.SubprocessError) as exc:
        raise SystemExit('Refused/failed; handoff may be partial, inspect actions, no blind retry: '+str(exc))
