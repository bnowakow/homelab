#!/usr/bin/env python3
"""Apply copy-tested filesystem recovery to original Kuma volume; dry-run by default."""
import argparse
import datetime
import importlib.util
import json
import os
from pathlib import Path
import pwd
import shlex
import shutil
import socket
import subprocess
import sys

PRESERVED=Path('/var/tmp/kuma-preservation-20261118T184540364827Z')
WORK=Path('/var/tmp/kuma-working-20261118T192530502463Z')
DATABASE=Path('/var/tmp/kuma-database-validation-20261118T194659989258Z')
HASH='3470b1fd2130d7b2ed136725941695177146a753f9593436af8460ce8d1666a1'


def load_helper(name, filename):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(filename))
    helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
    return helper


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--execute',action='store_true')
    ap.add_argument('--yes',action='store_true')
    args=ap.parse_args()
    if os.geteuid()!=0 or socket.gethostname().split('.')[0]!='proxmox3':
        raise RuntimeError('Run with sudo on proxmox3')
    preservation=json.loads((PRESERVED/'verified.json').read_text())
    if preservation.get('sha256')!=HASH or preservation.get('bytes')!=268435456:
        raise RuntimeError('Verified preservation checkpoint missing')
    info=(PRESERVED/'source-preserved.img').stat()
    if info.st_size!=268435456 or info.st_mode&0o222:raise RuntimeError('Preservation file size/mode changed')
    filesystem=json.loads((WORK/'filesystem-validated.json').read_text())
    database=json.loads((DATABASE/'database-validated.json').read_text())
    if not filesystem.get('readOnlyFilesystemCheckPassed') or database.get('integrityCheck')!='ok' or not database.get('walIncluded'):
        raise RuntimeError('Working filesystem/SQLite WAL validation missing')
    prior=list(Path('/var/tmp').glob('kuma-original-repair-*/repair-started.json'))
    if prior:raise RuntimeError('Original-volume repair already attempted; inspect '+str(prior))
    helper=load_helper('source_fs','check-kuma-filesystem.py')
    device,unmounted,evidence=helper.source_guard()
    commands=[['e2fsck','-p','-E','journal_only',str(device)],
              ['e2fsck','-f','-p',str(device)],['e2fsck','-f','-n',str(device)]]
    print('THIS STEP WRITES FILESYSTEM METADATA ON THE ORIGINAL KUMA VOLUME:',device)
    print('Copy-tested recovery; verified preservation image retained. No -y, format, forced attach or app startup.')
    print(json.dumps(commands,indent=2))
    if not args.execute:
        print('Dry run. Use --execute only after reviewing the original-volume repair plan.');return
    if not args.yes:
        if not (shutil.which('dialog') and sys.stdin.isatty() and sys.stdout.isatty()):
            raise RuntimeError('Interactive dialog unavailable; explicit --execute --yes required')
        if subprocess.run(['dialog','--defaultno','--yesno',
            'Apply copy-tested journal recovery and automatic filesystem repair to ORIGINAL Kuma volume? '
            'Verified preservation image exists; Kuma must stay stopped/unmounted. No -y or formatting.', '12','76']).returncode:return

    def command(argv,timeout=25):
        p=subprocess.run(argv,capture_output=True,text=True,timeout=timeout)
        if p.returncode:raise RuntimeError(p.stdout+p.stderr)
        return p.stdout

    user=pwd.getpwnam(os.environ.get('SUDO_USER') or 'sup')
    if user.pw_uid==0:raise RuntimeError('Non-root peer SSH account required')
    peer_code="""import pathlib,json,socket,subprocess
active=[]
for cg in pathlib.Path('/sys/fs/cgroup/system.slice').glob('docker-*.scope/cgroup.procs'):
    try:
        if cg.read_text().strip():active.append(str(cg.parent))
    except FileNotFoundError:pass
print(json.dumps({'host':socket.gethostname().split('.')[0],'psi':pathlib.Path('/proc/pressure/io').read_text(),'docker':active,
 'service':subprocess.check_output(['systemctl','show','k3s','-p','NRestarts','-p','ExecMainStartTimestampMonotonic','-p','ActiveState'],text=True)}))"""
    def health():
        remote=json.loads(command(['runuser','-u',user.pw_name,'--','ssh','-oBatchMode=yes','-oStrictHostKeyChecking=yes',
            '-oConnectTimeout=5','sup@10.0.0.72','python3 -c '+shlex.quote(peer_code)]))
        if remote['host']!='proxmox2-old' or remote['docker']:raise RuntimeError('Peer host/Docker pause drift')
        local=Path('/proc/pressure/io').read_text()
        for text in (local,remote['psi']):
            psi={line.split()[0]:float(line.split()[1].split('=')[1]) for line in text.splitlines()}
            if psi.get('some',100)>10 or psi.get('full',100)>2:raise RuntimeError('High I/O pressure')
        service=command(['systemctl','show','k3s','-p','NRestarts','-p','ExecMainStartTimestampMonotonic','-p','ActiveState'])
        if 'ActiveState=active' not in service or 'ActiveState=active' not in remote['service']:raise RuntimeError('k3s not active')
        return {'localService':service,'peerService':remote['service'],'localIO':local,'peerIO':remote['psi']}

    command([sys.executable,str(Path(__file__).with_name('kuma-recovery-maintenance.py')),'verify'],180)
    original_device=device
    device,unmounted,evidence=helper.source_guard()
    if device!=original_device:raise RuntimeError('Device mapping changed during confirmation')
    baseline=health()
    folder=Path('/var/tmp')/('kuma-original-repair-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    folder.mkdir(mode=0o700)
    (folder/'repair-started.json').write_text(json.dumps({'time':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'sourceEvidence':evidence,'commands':commands,'verifiedPreservation':str(PRESERVED),
        'workingValidation':str(DATABASE),'hostHealth':baseline},indent=2)+'\n')
    print('Original-repair evidence:',folder,flush=True)
    for index,argv in enumerate(commands):
        unmounted()
        current=health()
        if current['localService']!=baseline['localService'] or current['peerService']!=baseline['peerService']:
            raise RuntimeError('k3s state changed; stop original-volume repair')
        log=folder/('phase-'+str(index+1)+'.txt')
        print('Running:',' '.join(argv),flush=True)
        with log.open('w') as output:
            result=subprocess.run(argv,stdout=output,stderr=subprocess.STDOUT,timeout=60)
        text=log.read_text();print(text,end='')
        (folder/('phase-'+str(index+1)+'-result.json')).write_text(json.dumps({'time':datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'exitCode':result.returncode,'originalVolumeWritten':index<2,'pressure':current},indent=2)+'\n')
        if result.returncode not in ((0,1) if index<2 else (0,)):
            raise RuntimeError('Original-volume repair/check needs review; exit '+str(result.returncode))
        if index==2 and any(t in text for t in ('count wrong','WARNING','UNEXPECTED INCONSISTENCY','skipping journal recovery')):
            raise RuntimeError('Original filesystem still reports an unresolved warning')
    unmounted();health()
    (folder/'original-filesystem-validated.json').write_text(json.dumps({'time':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'originalFilesystemRepaired':True,'readOnlyCheckPassed':True,'kumaStillStopped':True,
        'preservationImageUntouched':True,'applicationValidationPending':True},indent=2)+'\n')
    print('Original filesystem validation passed; leave unmounted and Kuma stopped for controlled attachment handoff.')


if __name__=='__main__':
    try:main()
    except (RuntimeError,OSError,ValueError,KeyError,subprocess.SubprocessError) as exc:
        raise SystemExit('Refused/failed; original repair may be partial, inspect logs; no blind retry: '+str(exc))
