#!/usr/bin/env python3
"""Assess current embedded-etcd membership/endpoint health; never changes membership."""
import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess


def main():
    if os.geteuid()!=0:raise RuntimeError('Run with sudo on a k3s server')
    if not shutil.which('etcdctl'):raise RuntimeError('etcdctl unavailable; no membership inference made')
    tls=Path('/var/lib/rancher/k3s/server/tls/etcd')
    for name in ('server-ca.crt','client.crt','client.key'):
        if not (tls/name).is_file():raise RuntimeError('Missing k3s etcd client credential file: '+name)
    folder=Path('/var/tmp')/('etcd-membership-assessment-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    folder.mkdir(mode=0o700)
    base=['etcdctl','--endpoints=https://127.0.0.1:2379',
          '--cacert='+str(tls/'server-ca.crt'),'--cert='+str(tls/'client.crt'),'--key='+str(tls/'client.key'),
          '--dial-timeout=3s','--command-timeout=10s','--write-out=json']
    data={};errors=[]
    for name,args in [('members',['member','list']),('health',['endpoint','health','--cluster']),('status',['endpoint','status','--cluster'])]:
        p=subprocess.run(base+args,capture_output=True,text=True,timeout=30,env=dict(os.environ,ETCDCTL_API='3'))
        record={'exitCode':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
        (folder/(name+'-command.json')).write_text(json.dumps(record,indent=2)+'\n')
        try:data[name]=json.loads(p.stdout)
        except ValueError:errors.append(name+': output unavailable/unparseable')
        if p.returncode:errors.append(name+': command failed: '+p.stderr.strip())
    members=data.get('members',{}).get('members',[])
    voters=[m for m in members if not m.get('isLearner',False)]
    health=data.get('health',[])
    healthy=[x for x in health if x.get('health') is True or x.get('health')=='true'] if isinstance(health,list) else []
    confirmed=(len(voters)==3 and all(m.get('name') for m in voters) and len(healthy)>=3 and not errors)
    summary={'directory':str(folder),'members':[{'name':m.get('name'),'id':str(m.get('ID')),
        'learner':m.get('isLearner',False),'peerURLs':m.get('peerURLs'),'clientURLs':m.get('clientURLs')} for m in members],
        'voters':len(voters),'healthyEndpoints':health,'endpointStatus':data.get('status'),
        'threeVoterHealthConfirmed':confirmed,'errors':errors,
        'limitations':'Membership/health snapshot only, not authorization to stop/remove any node. Kubernetes Node registration and storage placement are separate. No membership/configuration changes made.'}
    (folder/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2))
    if not confirmed:raise SystemExit(2)


if __name__=='__main__':
    try:main()
    except (RuntimeError,OSError,ValueError,KeyError,subprocess.SubprocessError) as exc:
        raise SystemExit('Etcd assessment incomplete; do not shut down/remove a voter: '+str(exc))
