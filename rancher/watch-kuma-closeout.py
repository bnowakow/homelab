#!/usr/bin/env python3
"""Read-only backup/pressure watcher; never starts services or changes limits."""
import argparse
import datetime
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import socket
import subprocess
import time

JOB='nas-daily-backup-29917440'


def psi(text):
    return {line.split()[0]:float(line.split()[1].split('=')[1]) for line in text.splitlines()}


def low(value):
    return value.get('some',100)<=10 and value.get('full',100)<=2


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--minutes',type=int,default=30,help='Maximum observation time, default 30 minutes')
    args=ap.parse_args()
    if args.minutes<1 or args.minutes>120:ap.error('--minutes must be between 1 and 120')
    if os.geteuid()!=0 or socket.gethostname().split('.')[0]!='proxmox3':raise RuntimeError('Run with sudo on proxmox3')
    user=pwd.getpwnam(os.environ.get('SUDO_USER') or 'sup')
    if user.pw_uid==0:raise RuntimeError('Existing non-root SSH account required')
    kube=['kubectl','--kubeconfig=/etc/rancher/k3s/k3s.yaml','--request-timeout=15s']
    def command(argv):
        p=subprocess.run(argv,capture_output=True,text=True,timeout=25)
        if p.returncode:raise RuntimeError(p.stderr.strip())
        return p.stdout
    def local_service():
        return command(['systemctl','show','k3s','-p','NRestarts','-p','ExecMainStartTimestampMonotonic','-p','ActiveState'])
    peer_code="import json,pathlib,socket,subprocess;print(json.dumps({'host':socket.gethostname().split('.')[0],'psi':pathlib.Path('/proc/pressure/io').read_text(),'service':subprocess.check_output(['systemctl','show','k3s','-p','NRestarts','-p','ExecMainStartTimestampMonotonic','-p','ActiveState'],text=True)}))"
    peer_argv=['runuser','-u',user.pw_name,'--','ssh','-oBatchMode=yes','-oStrictHostKeyChecking=yes',
               '-oConnectTimeout=5','sup@10.0.0.72','python3 -c '+shlex.quote(peer_code)]
    folder=Path('/var/tmp')/('kuma-closeout-watch-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    folder.mkdir(mode=0o700)
    print('Read-only watch log:',folder,flush=True)
    start=time.monotonic();stable=0;baseline=None;baseline_uid=None
    while time.monotonic()-start<args.minutes*60:
        job=json.loads(command(kube+['get','job',JOB,'-n','longhorn-system','-o','json']))
        if baseline_uid is None:baseline_uid=job['metadata']['uid']
        if baseline_uid!=job['metadata']['uid']:raise RuntimeError('Backup Job replaced; inspect state')
        conditions=job.get('status',{}).get('conditions',[])
        complete=any(c.get('type')=='Complete' and c.get('status')=='True' for c in conditions)
        failed=any(c.get('type')=='Failed' and c.get('status')=='True' for c in conditions)
        peer=json.loads(command(peer_argv))
        if peer['host']!='proxmox2-old':raise RuntimeError('Wrong SSH peer')
        services=(local_service(),peer['service'])
        if any('ActiveState=active' not in s for s in services):raise RuntimeError('k3s not active on both hosts')
        if baseline is None:baseline=services
        if baseline!=services:raise RuntimeError('k3s restart/start identity changed during watch')
        pressure={'local':psi(Path('/proc/pressure/io').read_text()),'peer':psi(peer['psi'])}
        stable=stable+1 if complete and all(low(v) for v in pressure.values()) else 0
        row={'time':datetime.datetime.now(datetime.timezone.utc).isoformat(),'job':JOB,
             'jobComplete':complete,'jobFailed':failed,'activePods':job.get('status',{}).get('active',0),
             'pressure':pressure,'lowSamplesAfterCompletion':stable,'requiredLowSamples':3}
        if not complete and not failed:
            logs=command(kube+['logs','job/'+JOB,'-n','longhorn-system','--all-containers','--tail=5'])
            volumes=re.findall(r'volumeName=(pvc-[0-9a-f-]+)',logs)
            progress=re.findall(r'current progress (\d+)',logs)
            row['lastReportedVolume']=volumes[-1] if volumes else None
            row['lastReportedVolumeProgressPercent']=int(progress[-1]) if progress else None
            row['note']='Progress is for one volume, not the whole Job'
        with (folder/'observations.jsonl').open('a') as f:f.write(json.dumps(row)+'\n');f.flush();os.fsync(f.fileno())
        print(json.dumps(row),flush=True)
        if failed:raise RuntimeError('Backup Job failed; inspect its logs/status')
        if stable>=3:
            (folder/'backup-complete-pressure-low.json').write_text(json.dumps(row,indent=2)+'\n')
            print('Backup complete; three low-pressure samples on both hosts. No services started. Share this result for Docker restoration/final checks.')
            return
        time.sleep(30)
    raise RuntimeError('Observation window ended without confirmed completion plus sustained low pressure; no services changed')


if __name__=='__main__':
    try:main()
    except (RuntimeError,OSError,ValueError,KeyError,subprocess.SubprocessError) as exc:
        raise SystemExit('Watch stopped; no cluster changes: '+str(exc))
