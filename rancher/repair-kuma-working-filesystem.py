#!/usr/bin/env python3
"""Run automatic ext4 repair on the exact disposable working image, never original data."""
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


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--execute',action='store_true')
    ap.add_argument('--yes',action='store_true')
    args=ap.parse_args()
    if os.geteuid()!=0 or socket.gethostname().split('.')[0]!='proxmox3':
        raise RuntimeError('Run with sudo on proxmox3')
    spec=importlib.util.spec_from_file_location('working_journal',Path(__file__).with_name('recover-kuma-working-journal.py'))
    helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
    folder=helper.WORK

    def guards():
        image,identity=helper.validate(folder,allow_previous_journal=True)
        replay=json.loads((folder/'journal-replay-result.json').read_text())
        check=json.loads((folder/'filesystem-after-journal-result.json').read_text())
        text=(folder/'filesystem-after-journal.txt').read_text()
        if replay['exitCode'] not in (0,1) or check['exitCode'] not in (0,4):
            raise RuntimeError('Previous journal/check result needs separate assessment')
        if 'Free blocks count wrong' not in text or 'skipping journal recovery' in text:
            raise RuntimeError('Unexpected previous filesystem result')
        if (folder/'filesystem-validated.json').exists() or (folder/'working-repair-started.json').exists():
            raise RuntimeError('Repair/validation already attempted; inspect logs rather than repeat')
        return image,identity

    image,identity=guards()
    commands=[['e2fsck','-f','-p',str(image)],['e2fsck','-f','-n',str(image)]]
    print('Repair ONLY disposable working image:',image)
    print(json.dumps(commands,indent=2))
    print('Automatic repair only; uncorrectable errors stop. No -y, mount, source writes or application startup.')
    if not args.execute:
        print('Dry run. Use --execute after reviewing this working-image-only repair.');return
    if not args.yes:
        if not (shutil.which('dialog') and sys.stdin.isatty() and sys.stdout.isatty()):
            raise RuntimeError('Interactive dialog unavailable; explicit --execute --yes required')
        if subprocess.run(['dialog','--defaultno','--yesno',
            'Run automatic e2fsck repair ONLY on the disposable working.img, then read-only validation? '
            'Original device and preservation image unchanged. No -y.', '11','76']).returncode:return
    p=subprocess.run([sys.executable,str(Path(__file__).with_name('kuma-recovery-maintenance.py')),'verify'],
                     capture_output=True,text=True,timeout=180)
    if p.returncode:raise RuntimeError('Maintenance verification failed: '+p.stdout+p.stderr)
    image,current=guards()
    if (identity.st_dev,identity.st_ino)!=(current.st_dev,current.st_ino):raise RuntimeError('Image identity changed')
    pressure=Path('/proc/pressure/io').read_text()
    psi={line.split()[0]:float(line.split()[1].split('=')[1]) for line in pressure.splitlines()}
    if psi.get('some',100)>10 or psi.get('full',100)>2:raise RuntimeError('High local I/O pressure')
    (folder/'working-repair-started.json').write_text(json.dumps({'time':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'commands':commands,'ioPressure':pressure,'originalAndPreservationUntouched':True},indent=2)+'\n')
    for index,command in enumerate(commands):
        log=folder/('working-repair.txt' if index==0 else 'filesystem-after-repair.txt')
        print('Running:',' '.join(command),flush=True)
        with log.open('w') as output:
            result=subprocess.run(command,stdout=output,stderr=subprocess.STDOUT,timeout=60)
        text=log.read_text();print(text,end='')
        (folder/(log.stem+'-result.json')).write_text(json.dumps({'exitCode':result.returncode,
            'time':datetime.datetime.now(datetime.timezone.utc).isoformat(),'sourceWritten':False})+'\n')
        if result.returncode not in ((0,1) if index==0 else (0,)):
            raise RuntimeError('Working-image repair/check needs review: exit '+str(result.returncode))
        if index==1 and any(message in text for message in ('count wrong','WARNING','UNEXPECTED INCONSISTENCY','skipping journal recovery')):
            raise RuntimeError('Unresolved filesystem warning despite exit code')
    (folder/'filesystem-validated.json').write_text(json.dumps({'time':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'workingFilesystemRepaired':True,'readOnlyFilesystemCheckPassed':True,'databaseIntegrityChecked':False})+'\n')
    print('Working-image filesystem validation passed. SQLite/WAL integrity still unchecked. Keep applications stopped.')


if __name__=='__main__':
    try:main()
    except (RuntimeError,OSError,ValueError,KeyError,subprocess.SubprocessError) as exc:
        raise SystemExit('Refused/failed; inspect working-image logs, do not repair source: '+str(exc))
