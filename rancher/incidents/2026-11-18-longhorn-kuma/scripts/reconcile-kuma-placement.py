#!/usr/bin/env python3
"""Apply only Kuma's reviewed Git placement through Fleet, then validate rollout/storage/ingress."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[3]
NS='apps-uptime-kuma'
V='pvc-326def38-faba-49f6-8a78-ebf719227220'


def parse_kubernetes_objects(text):
    """kubectl may emit one JSON object per YAML document rather than one List."""
    decoder=json.JSONDecoder()
    cursor=0
    objects=[]
    while cursor<len(text):
        while cursor<len(text) and text[cursor].isspace():cursor+=1
        if cursor==len(text):break
        obj,cursor=decoder.raw_decode(text,cursor)
        if not isinstance(obj,dict):raise RuntimeError('Expected Kubernetes JSON object')
        if obj.get('kind','').endswith('List') or obj.get('kind')=='List':
            entries=obj.get('items')
            if not isinstance(entries,list) or any(not isinstance(item,dict) for item in entries):
                raise RuntimeError('Invalid Kubernetes List output')
            objects.extend(entries)
        else:
            objects.append(obj)
    if not objects:raise RuntimeError('Empty Kubernetes client conversion output')
    return objects


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--execute',action='store_true')
    ap.add_argument('--yes',action='store_true')
    args=ap.parse_args()
    if os.geteuid()!=0 or socket.gethostname().split('.')[0]!='proxmox3':raise RuntimeError('Run with sudo on proxmox3')
    folder=Path('/var/tmp')/('kuma-fleet-placement-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    folder.mkdir(mode=0o700)
    print('Evidence directory:',folder,flush=True)
    kube=['kubectl','--kubeconfig=/etc/rancher/k3s/k3s.yaml','--request-timeout=15s']
    def command(argv,timeout=90):
        p=subprocess.run(argv,cwd=ROOT,capture_output=True,text=True,timeout=timeout)
        if p.returncode:raise RuntimeError(str(argv[:3])+': '+p.stderr.strip())
        return p.stdout
    def get(kind,name,namespace):return json.loads(command(kube+['get',kind,name,'-n',namespace,'-o','json']))
    def items(kind,selector=None):return json.loads(command(kube+['get',kind,'-A']+(['-l',selector] if selector else [])+['-o','json']))['items']
    def low_io():
        text=Path('/proc/pressure/io').read_text()
        values={line.split()[0]:float(line.split()[1].split('=')[1]) for line in text.splitlines()}
        if values.get('some',100)>10 or values.get('full',100)>2:raise RuntimeError('High local I/O pressure')
    def live_guards():
        bundle=get('bundles.fleet.cattle.io','uptime-kuma','fleet-local')
        if bundle['metadata'].get('labels',{}).get('fleet.cattle.io/repo-name')!='rancher-cluster':
            raise RuntimeError('Expected rancher-cluster GitRepo ownership; inspect live labels')
        if bundle['spec'].get('helm',{}).get('version')!='15.1.4':
            raise RuntimeError('Live chart version differs; this helper must not upgrade versions')
        deployment=get('deployment','uptime-kuma',NS)
        if deployment['spec'].get('replicas')!=1 or deployment.get('status',{}).get('readyReplicas')!=1:
            raise RuntimeError('Recovered Kuma not ready at one replica')
        pods=json.loads(command(kube+['get','pods','-n',NS,'-o','json']))['items']
        consumers=[p for p in pods if not p['metadata'].get('deletionTimestamp')
                   and any(v.get('persistentVolumeClaim',{}).get('claimName')=='uptime-kuma-config-longhorn-rwo' for v in p['spec'].get('volumes',[]))]
        if len(consumers)!=1 or consumers[0]['spec'].get('nodeName')!='proxmox3':
            raise RuntimeError('Expected one live Kuma PVC consumer on proxmox3')
        helm=json.loads(command(['helm','status','uptime-kuma','--kubeconfig','/etc/rancher/k3s/k3s.yaml','-n',NS,'-o','json']))
        if helm.get('info',{}).get('status')!='deployed':
            raise RuntimeError('Helm release not deployed/settled; inspect pending reconciliation first')
        if not bundle['spec'].get('paused'):
            current_bds=items('bundledeployments.fleet.cattle.io','fleet.cattle.io/bundle-name=uptime-kuma')
            if (len(current_bds)!=1 or current_bds[0].get('status',{}).get('ready') is not True
                or not current_bds[0].get('spec',{}).get('deploymentID')
                or current_bds[0]['spec']['deploymentID']!=current_bds[0]['status'].get('appliedDeploymentID')):
                raise RuntimeError('Unpaused Bundle has pending/unready deployment; inspect before pausing/applying')
        volume=get('volumes.longhorn.io',V,'longhorn-system')
        if volume['status'].get('state')!='attached' or volume['status'].get('currentNodeID')!='proxmox3':
            raise RuntimeError('Unexpected Kuma volume attachment')
        if any(b['metadata']['name']!='uptime-kuma' for b in items('bundles.fleet.cattle.io','fleet.cattle.io/repo-name=uptime-kuma')):
            raise RuntimeError('Native Fleet positional scope includes unrelated Bundles; refuse pruning risk')
        low_io();return bundle,deployment,volume
    bundle,deployment,volume=live_guards()
    for name,obj in [('bundle-before',bundle),('deployment-before',deployment),('volume-before',volume)]:
        (folder/(name+'.json')).write_text(json.dumps(obj,indent=2)+'\n')
    values_path=ROOT/'fleet/apps/uptime-kuma/values.yaml'
    original_digest=hashlib.sha256(values_path.read_bytes()).hexdigest()
    cache=Path('/home/sup/.cache/helm/repository/uptime-kuma-15.1.4.tgz')
    chart=str(cache) if cache.is_file() else 'oci://oci.trueforge.org/truecharts/uptime-kuma'
    # Full chart schema validation stays enabled; no --skip-schema-validation here.
    rendered=command(['helm','template','uptime-kuma',chart,'--version','15.1.4','--namespace',NS,'--values',str(values_path)],180)
    render_path=folder/'rendered.yaml';render_path.write_text(rendered)
    docs=parse_kubernetes_objects(command(kube+['create','--dry-run=client','--validate=false','-f',str(render_path),'-o','json']))
    planned=[o for o in docs if o.get('kind')=='Deployment' and o['metadata']['name']=='uptime-kuma']
    if len(planned)!=1:raise RuntimeError('Expected exactly one rendered Kuma Deployment')
    spec=planned[0]['spec'];pod=spec['template']['spec']
    if (spec.get('replicas')!=1 or spec.get('strategy',{}).get('type')!='Recreate'
        or pod.get('nodeSelector',{}).get('kubernetes.io/hostname')!='proxmox3'
        or pod.get('affinity',{}).get('podAffinity')
        or not any(x.get('persistentVolumeClaim',{}).get('claimName')=='uptime-kuma-config-longhorn-rwo' for x in pod.get('volumes',[]))):
        raise RuntimeError('Rendered placement/affinity/replica/claim validation failed')
    labels=[]
    for key,value in bundle['metadata'].get('labels',{}).items():
        if key!='fleet.cattle.io/commit':labels+=['--label',key+'='+value]
    fleet=['fleet','apply','--kubeconfig','/etc/rancher/k3s/k3s.yaml','--namespace','fleet-local',*labels,'--paused']
    generated=folder/'generated-bundle.yaml'
    command(fleet+['--output',str(generated),'uptime-kuma','fleet/apps/uptime-kuma'],180)
    generated_items=parse_kubernetes_objects(command(kube+['create','--dry-run=client','--validate=false','-f',str(generated),'-o','json']))
    candidates=[x for x in generated_items if x.get('kind')=='Bundle']
    if len(candidates)!=1 or candidates[0]['metadata']['name']!='uptime-kuma':raise RuntimeError('Generation includes unexpected Bundle scope')
    if candidates[0]['spec'].get('helm',{}).get('version')!='15.1.4':raise RuntimeError('Generated chart version changed')
    if candidates[0]['spec'].get('targets')!=bundle['spec'].get('targets'):
        raise RuntimeError('Generated targets differ from live targeting; inspect preview')
    print('Plan/evidence:',folder)
    print('Full schema/render validation passed. Apply only uptime-kuma via native Fleet; preserve ownership/targets; unpause only Kuma; wait for new applied deployment and verify mount/ingress.')
    print('Current Kuma paused:',bundle['spec'].get('paused',False),'; execution will pause only Kuma before apply.')
    print('May briefly recreate Kuma. Longhorn Bundle, old-node maintenance and storage replica count are not modified.')
    if not args.execute:print('Dry run. No cluster changes; use --execute after reviewing values/preview.');return
    if not args.yes:
        if not (shutil.which('dialog') and sys.stdin.isatty() and sys.stdout.isatty()):raise RuntimeError('Interactive dialog unavailable; explicit --execute --yes required')
        if subprocess.run(['dialog','--defaultno','--yesno','Apply only Kuma placement through native Fleet and unpause Kuma? '
            'Kuma may briefly restart. Longhorn remains paused; PVC unchanged. Validate before committing.', '11','76']).returncode:return
    live,_,_=live_guards()
    if live['metadata']['uid']!=bundle['metadata']['uid'] or live['spec'].get('targets')!=bundle['spec'].get('targets'):
        raise RuntimeError('Bundle identity/targeting drift')
    if hashlib.sha256(values_path.read_bytes()).hexdigest()!=original_digest:raise RuntimeError('Values changed during review')
    previous={b.get('status',{}).get('appliedDeploymentID') for b in items('bundledeployments.fleet.cattle.io','fleet.cattle.io/bundle-name=uptime-kuma')}
    def log(event,**fields):
        row=dict(time=datetime.datetime.now(datetime.timezone.utc).isoformat(),event=event,**fields)
        with (folder/'actions.jsonl').open('a') as f:f.write(json.dumps(row)+'\n');f.flush();os.fsync(f.fileno())
        print(json.dumps(row),flush=True)
    if not live['spec'].get('paused'):
        log('temporary_kuma_pause_requested',previousPaused=False)
        pause_patch=[{'op':'test','path':'/metadata/uid','value':live['metadata']['uid']},
                     {'op':'test','path':'/metadata/resourceVersion','value':live['metadata']['resourceVersion']},
                     {'op':'add','path':'/spec/paused','value':True}]
        command(kube+['patch','bundles.fleet.cattle.io','uptime-kuma','-n','fleet-local','--type=json','-p',json.dumps(pause_patch),'-o','json'])
    paused=get('bundles.fleet.cattle.io','uptime-kuma','fleet-local')
    if paused['metadata']['uid']!=bundle['metadata']['uid'] or not paused['spec'].get('paused'):
        raise RuntimeError('Temporary Kuma pause verification failed')
    log('temporary_kuma_pause_confirmed',originalPaused=bundle['spec'].get('paused',False))
    log('native_fleet_apply_requested')
    command(fleet+['uptime-kuma','fleet/apps/uptime-kuma'],180)
    current=get('bundles.fleet.cattle.io','uptime-kuma','fleet-local')
    if current['metadata']['uid']!=bundle['metadata']['uid']:raise RuntimeError('Bundle replaced')
    if not current['spec'].get('paused'):raise RuntimeError('Kuma pause drifted during native apply; inspect live reconciliation')
    for key,value in bundle['metadata'].get('labels',{}).items():
        if key!='fleet.cattle.io/commit' and current['metadata'].get('labels',{}).get(key)!=value:raise RuntimeError('Ownership/labels changed')
    if current['spec'].get('targets')!=bundle['spec'].get('targets'):raise RuntimeError('Targeting changed')
    log('native_fleet_apply_confirmed',bundleUID=current['metadata']['uid'])
    log('kuma_unpause_requested')
    patch=[{'op':'test','path':'/metadata/resourceVersion','value':current['metadata']['resourceVersion']},
           {'op':'test','path':'/metadata/uid','value':bundle['metadata']['uid']},
           {'op':'add','path':'/spec/paused','value':False},
           {'op':'add','path':'/spec/forceSyncGeneration','value':current['spec'].get('forceSyncGeneration',0)+1}]
    applied=json.loads(command(kube+['patch','bundles.fleet.cattle.io','uptime-kuma','-n','fleet-local','--type=json','-p',json.dumps(patch),'-o','json']))
    if applied['spec'].get('paused'):raise RuntimeError('Kuma unpause not applied')
    log('kuma_unpause_confirmed')
    expected=applied['metadata']['generation'];deadline=time.monotonic()+600
    while True:
        current=get('bundles.fleet.cattle.io','uptime-kuma','fleet-local')
        bds=items('bundledeployments.fleet.cattle.io','fleet.cattle.io/bundle-name=uptime-kuma')
        if (current.get('status',{}).get('observedGeneration',0)>=expected and len(bds)==1
            and bds[0].get('status',{}).get('ready') is True
            and bds[0].get('spec',{}).get('deploymentID')
            and bds[0]['spec']['deploymentID']==bds[0]['status'].get('appliedDeploymentID')
            and bds[0]['status']['appliedDeploymentID'] not in previous):break
        if time.monotonic()>deadline:raise RuntimeError('Fresh Fleet BundleDeployment readiness timed out; inspect evidence, do not commit yet')
        time.sleep(5)
    (folder/'bundledeployment-after.json').write_text(json.dumps(bds[0],indent=2)+'\n')
    command(kube+['rollout','status','deployment/uptime-kuma','-n',NS,'--timeout=180s'],190)
    live=get('deployment','uptime-kuma',NS);pod_spec=live['spec']['template']['spec']
    if live['spec'].get('replicas')!=1 or pod_spec.get('nodeSelector',{}).get('kubernetes.io/hostname')!='proxmox3' or pod_spec.get('affinity',{}).get('podAffinity'):
        raise RuntimeError('Live placement/affinity drift')
    pods=json.loads(command(kube+['get','pods','-n',NS,'-o','json']))['items']
    consumers=[p for p in pods if not p['metadata'].get('deletionTimestamp') and any(v.get('persistentVolumeClaim',{}).get('claimName')=='uptime-kuma-config-longhorn-rwo' for v in p['spec'].get('volumes',[]))]
    if len(consumers)!=1 or consumers[0]['spec'].get('nodeName')!='proxmox3':raise RuntimeError('Consumer placement mismatch')
    containers=[c['name'] for c in consumers[0]['spec']['containers'] if any(m.get('mountPath')=='/app/data' for m in c.get('volumeMounts',[]))]
    if len(containers)!=1:raise RuntimeError('Expected one application container mounting /app/data')
    mount_code="const fs=require('fs');const m=fs.readFileSync('/proc/self/mountinfo','utf8').split('\\n').some(l=>l.split(' ')[4]==='/app/data');if(!m||!(fs.statfsSync('/app/data').blocks>0))process.exit(2);console.log('app-data-mounted-readable')"
    command(kube+['exec','-n',NS,consumers[0]['metadata']['name'],'-c',containers[0],'--','node','-e',mount_code])
    volume=get('volumes.longhorn.io',V,'longhorn-system')
    if volume['status'].get('currentNodeID')!='proxmox3' or volume['status'].get('state')!='attached':raise RuntimeError('Storage attachment mismatch')
    for host in ('uptime-kuma.rancher.tailscale.bnowakowski.pl','uptime-kuma.rancher.localdomain.bnowakowski.pl'):
        status=command(['curl','--silent','--show-error','--fail','--max-time','20','--output','/dev/null','--write-out','%{http_code}','https://'+host+'/'],25)
        log('ingress_https_validated',host=host,status=status)
    low_io()
    (folder/'placement-validated.json').write_text(json.dumps({'time':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'valuesSHA256':original_digest,'bundle':'uptime-kuma','nativeFleetApplied':True,'bundleDeploymentReady':True,
        'replicas':1,'node':'proxmox3','ingressValidated':True},indent=2)+'\n')
    log('kuma_git_placement_live_validated_commit_eligible')
    print('Kuma placement applied/validated through Fleet. Review Git diff and commit; no commit/push performed.')


if __name__=='__main__':
    try:main()
    except (RuntimeError,OSError,ValueError,KeyError,subprocess.SubprocessError) as exc:
        raise SystemExit('Refused/failed; changes may be unapplied or partly applied/unverified. Inspect evidence before commit/push: '+str(exc))
