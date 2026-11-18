#!/usr/bin/env python3
"""Read-only short I/O-pressure/process/storage diagnostic. Never retries a transfer."""
import datetime
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time


def processes():
    result={}
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            io=dict((k,int(v)) for k,v in (line.split(':',1) for line in (proc/'io').read_text().splitlines()))
            start=(proc/'stat').read_text().rsplit(')',1)[1].split()[19]
            result[(proc.name,start)]={'pid':int(proc.name),'name':(proc/'comm').read_text().strip(),
                'cgroup':(proc/'cgroup').read_text().strip(),'io':io}
        except (FileNotFoundError,ProcessLookupError):
            continue
    return result


def main():
    host=socket.gethostname().split('.')[0]
    if os.geteuid()!=0 or host not in ('proxmox3','proxmox2-old'):
        raise RuntimeError('Run with sudo on each storage host')
    folder=Path('/var/tmp')/('kuma-io-diagnostic-'+host+'-'+datetime.datetime.now(
        datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    folder.mkdir(mode=0o700)
    first=processes();samples=[]
    for i in range(6):
        sample={'time':datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'ioPSI':Path('/proc/pressure/io').read_text().strip()}
        samples.append(sample);print(json.dumps(sample),flush=True)
        if i<5:time.sleep(2)
    last=processes();busy=[]
    for key,current in last.items():
        if key not in first:continue
        delta={field:current['io'].get(field,0)-first[key]['io'].get(field,0)
               for field in ('read_bytes','write_bytes','rchar','wchar')}
        if any(delta.values()):
            busy.append(dict(pid=current['pid'],name=current['name'],cgroup=current['cgroup'],delta=delta))
    busy.sort(key=lambda row:row['delta']['read_bytes']+row['delta']['write_bytes'],reverse=True)
    commands={
        'k3s':['systemctl','show','k3s','-p','NRestarts','-p','ExecMainStartTimestampMonotonic','-p','ActiveState'],
        'kernel':['journalctl','-k','--since=-10min','--no-pager','-n','200'],
        'space':['df','-B1','/var/lib/longhorn','/var/tmp'],
    }
    if shutil.which('zpool'):commands['pool-io']=['zpool','iostat','-v','1','3']
    if shutil.which('iostat'):commands['device-io']=['iostat','-xz','1','3']
    kube=['kubectl','--kubeconfig=/etc/rancher/k3s/k3s.yaml','--request-timeout=15s']
    commands['longhorn']=kube+['get','engines.longhorn.io,replicas.longhorn.io,volumes.longhorn.io,instancemanagers.longhorn.io','-n','longhorn-system','-o','json']
    commands['pods']=kube+['get','pods','-A','-o','wide']
    commands['readyz']=kube+['get','--raw=/readyz?verbose']
    if host=='proxmox3':commands['maintenance']=[sys.executable,str(Path(__file__).with_name('kuma-recovery-maintenance.py')),'verify']
    reports={}
    for name,command in commands.items():
        try:
            p=subprocess.run(command,capture_output=True,text=True,timeout=180 if name=='maintenance' else 25)
            report={'returnCode':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
        except (OSError,subprocess.TimeoutExpired) as exc:
            report={'error':str(exc)}
        reports[name]=report
        (folder/(name+'.json')).write_text(json.dumps(report,indent=2)+'\n')
    summary={'host':host,'directory':str(folder),'samples':samples,'topIOProcesses':busy[:15],
             'k3s':reports['k3s'],'readyz':reports['readyz'],
             'maintenance':reports.get('maintenance'),'poolIO':reports.get('pool-io'),
             'limitations':'Process counters are best-effort over 10 seconds; exited/new tasks and kernel/ZFS attribution may be incomplete. No causal diagnosis or restart/copy approval.'}
    (folder/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':
    try:main()
    except (RuntimeError,OSError,ValueError,KeyError,subprocess.SubprocessError) as exc:
        raise SystemExit('Diagnostic failed; do not retry copying: '+str(exc))
