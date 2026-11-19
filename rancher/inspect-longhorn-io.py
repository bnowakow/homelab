#!/usr/bin/env python3
"""Read-only mapping of a busy Longhorn PID to pod/volumes and active operations."""
import argparse
import datetime
import json
import os
from pathlib import Path
import re
import socket
import subprocess


def operation_needs_review(value):
    if value.get('errorPresent'):
        return True
    if any(value.get(k) is True for k in ('isRestoring','isPurging','isCloning')):
        return True
    state=value.get('state','').lower()
    return state not in ('','complete','completed','stopped','idle','finished','success')


def compact(result):
    summary=dict(result)
    kept=[]
    matched=set(result.get('process',{}).get('volumeIDs',[]))
    for engine in result.get('engines',[]):
        operations={kind:[entry for entry in entries if operation_needs_review(entry)]
                    for kind,entries in engine.get('operations',{}).items()}
        operations={kind:entries for kind,entries in operations.items() if entries}
        if engine.get('volume') in matched or operations:
            kept.append(dict(engine,operations=operations))
    summary['engines']=kept
    summary['engineFilter']='Only PID-matched volumes or active/error operation entries. Completed/idle entries omitted.'
    return summary


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('pid',type=int,nargs='?')
    ap.add_argument('--saved-summary',action='store_true',help='Print compact latest saved evidence; no API calls')
    args=ap.parse_args()
    if os.geteuid()!=0:raise RuntimeError('Run with sudo on the host where this PID was observed')
    if args.saved_summary:
        candidates=sorted(p for p in Path('/var/tmp').glob('longhorn-io-inspection-*') if (p/'summary.json').is_file())
        if not candidates:raise RuntimeError('No saved inspection found')
        print(json.dumps(compact(json.loads((candidates[-1]/'summary.json').read_text())),indent=2));return
    if args.pid is None:ap.error('PID required unless --saved-summary is used')
    proc=Path('/proc')/str(args.pid)
    info={'pid':args.pid,'present':False};volumes=set();pod_uid=None
    try:
        before=(proc/'stat').read_text().rsplit(')',1)[1].split()[19]
        argv=(proc/'cmdline').read_bytes().decode(errors='replace').split('\0')
        cgroup=(proc/'cgroup').read_text()
        after=(proc/'stat').read_text().rsplit(')',1)[1].split()[19]
        if before!=after:raise RuntimeError('PID changed while reading evidence')
        volumes=set(re.findall(r'pvc-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',' '.join(argv)))
        match=re.search(r'pod([0-9a-f_\-]{36})[./]',cgroup)
        if match:pod_uid=match.group(1).replace('_','-')
        role=argv[1] if len(argv)>1 and argv[1] in ('replica','controller','engine','sync-agent','backup','snapshot','daemon') else '<not recognized>'
        info.update(present=True,processName=(proc/'comm').read_text().strip(),startTicks=before,
                    executable=Path(argv[0]).name,role=role,volumeIDs=sorted(volumes),podUID=pod_uid,cgroup=cgroup.strip())
    except (FileNotFoundError,ProcessLookupError):
        info['note']='PID exited; current operations below cannot be attributed to it'
    kube=['kubectl','--kubeconfig=/etc/rancher/k3s/k3s.yaml','--request-timeout=15s']
    def get(resource,namespace='longhorn-system'):
        p=subprocess.run(kube+['get',resource]+(['-n',namespace] if namespace else ['-A'])+['-o','json'],
                         capture_output=True,text=True,timeout=25,check=True)
        return json.loads(p.stdout)['items']
    objects=get('engines.longhorn.io,replicas.longhorn.io,volumes.longhorn.io,instancemanagers.longhorn.io')
    pods=get('pods');jobs=get('jobs',None)
    matched=[{'name':p['metadata']['name'],'uid':p['metadata']['uid'],'node':p['spec'].get('nodeName'),
              'phase':p.get('status',{}).get('phase')} for p in pods if p['metadata']['uid']==pod_uid]
    engines=[]
    for e in objects:
        if e['kind']!='Engine':continue
        status=e.get('status',{});spec=e['spec'];operations={}
        for kind in ('rebuildStatus','backupStatus','restoreStatus','purgeStatus','cloneStatus'):
            entries=status.get(kind) or {}
            if isinstance(entries,dict):
                operations[kind]=[{k:v for k,v in value.items() if k in ('state','progress','isRestoring','isPurging','isCloning')}
                                  | {'errorPresent':bool(value.get('error'))}
                                  for value in entries.values() if isinstance(value,dict)]
        if (spec.get('volumeName') in volumes or any(operations.values())):
            engines.append({'name':e['metadata']['name'],'volume':spec.get('volumeName'),
                'node':spec.get('nodeID'),'state':status.get('currentState'),
                'replicaModeMap':status.get('replicaModeMap'),'operations':operations})
    chosen=[{'kind':o['kind'],'name':o['metadata']['name'],'spec':o['spec'],'status':o.get('status')}
            for o in objects if o['kind']=='Volume' and o['metadata']['name'] in volumes
            or o['kind']=='Replica' and o['spec'].get('volumeName') in volumes]
    active_jobs=[{'namespace':j['metadata']['namespace'],'name':j['metadata']['name'],'active':j.get('status',{}).get('active')}
                 for j in jobs if j.get('status',{}).get('active',0)]
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    folder=Path('/var/tmp')/('longhorn-io-inspection-'+stamp);folder.mkdir(mode=0o700)
    (folder/'volume-details-private.json').write_text(json.dumps(chosen,indent=2)+'\n')
    result={'host':socket.gethostname(),'timeUTC':stamp,'directory':str(folder),'process':info,'matchedPod':matched,
            'matchedVolumes':[{'kind':o['kind'],'name':o['name'],'node':o['spec'].get('nodeID'),
                              'state':o.get('status',{}).get('currentState',o.get('status',{}).get('state'))}
                             for o in chosen], 'engines':engines,'activeJobs':active_jobs,
            'limitations':'Current snapshot only; a busy replica/engine may serve ordinary application I/O. No rebuild/cause inference from PID name alone. Full argv/environment not printed.'}
    (folder/'summary.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(compact(result),indent=2))


if __name__=='__main__':
    try:main()
    except (RuntimeError,OSError,ValueError,KeyError,subprocess.SubprocessError) as exc:
        raise SystemExit('Read-only inspection failed; do not change storage blindly: '+str(exc))
