#!/usr/bin/env python3
"""Capped extraction from repaired working image, then SQLite checks including WAL."""
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
import sqlite3
import subprocess
import sys
import time

WORK=Path('/var/tmp/kuma-working-20261118T192530502463Z')
CAP=1048576
FILES=('kuma.db','kuma.db-wal','kuma.db-shm')


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--execute',action='store_true')
    ap.add_argument('--yes',action='store_true')
    args=ap.parse_args()
    if os.geteuid()!=0 or socket.gethostname().split('.')[0]!='proxmox3':
        raise RuntimeError('Run with sudo on proxmox3')
    marker=json.loads((WORK/'filesystem-validated.json').read_text())
    if marker.get('readOnlyFilesystemCheckPassed') is not True:
        raise RuntimeError('Working filesystem validation missing')
    image=WORK/'working.img'
    if image.is_symlink() or not image.is_file() or image.stat().st_size!=268435456 or image.stat().st_nlink!=1:
        raise RuntimeError('Unexpected working-image identity')
    if subprocess.run(['losetup','--associated',str(image),'--noheadings','--output','NAME'],
                      capture_output=True,text=True,timeout=15,check=True).stdout.strip():
        raise RuntimeError('Working image already attached to loop device')
    if not shutil.which('mount') or not shutil.which('umount'):
        raise RuntimeError('mount/umount required')
    print('Source: repaired disposable working image only:',image)
    print('Plan: mount loop,ro,noload; copy database/WAL/SHM at 1 MiB/s; verify hashes; unmount; SQLite integrity_check with WAL enabled.')
    print('Original device and preservation image unchanged. SQLite may recover/checkpoint the extracted disposable files only.')
    if not args.execute:
        print('Dry run. Use --execute for extraction and database validation.');return
    if not args.yes:
        if not (shutil.which('dialog') and sys.stdin.isatty() and sys.stdout.isatty()):
            raise RuntimeError('Interactive dialog unavailable; explicit --execute --yes required')
        if subprocess.run(['dialog','--defaultno','--yesno',
            'Read-only mount repaired working.img, extract DB/WAL/SHM at 1 MiB/s, then validate disposable SQLite files? '
            'Keep all applications stopped. No original or preservation writes.', '11','76']).returncode:return
    folder=Path('/var/tmp')/('kuma-database-validation-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    folder.mkdir(mode=0o700)
    mountpoint=folder/'working-mount';mountpoint.mkdir()
    destination=folder/'database';destination.mkdir(mode=0o700)
    print('Validation directory:',folder,flush=True)

    def log(event,**fields):
        row=dict(time=datetime.datetime.now(datetime.timezone.utc).isoformat(),event=event,**fields)
        for filename in ['actions.jsonl']+(['cap-changes.jsonl'] if event.startswith('cap_') else []):
            with (folder/filename).open('a') as f:
                f.write(json.dumps(row)+'\n');f.flush();os.fsync(f.fileno())
        print(json.dumps(row),flush=True)

    def command(argv,timeout=25):
        p=subprocess.run(argv,capture_output=True,text=True,timeout=timeout)
        if p.returncode:raise RuntimeError(str(argv[:3])+': '+p.stdout+p.stderr)
        return p.stdout

    def maintenance():
        return command([sys.executable,str(Path(__file__).with_name('kuma-recovery-maintenance.py')),'verify'],180)

    helper_spec=importlib.util.spec_from_file_location('copy_worker',Path(__file__).with_name('preserve-kuma-recovery-image.py'))
    helper=importlib.util.module_from_spec(helper_spec);helper_spec.loader.exec_module(helper)
    user=pwd.getpwnam(os.environ.get('SUDO_USER') or 'sup')
    if user.pw_uid==0:raise RuntimeError('Non-root local SSH account required')
    peer_code="""import pathlib,json,socket,subprocess
active=[]
for cg in pathlib.Path('/sys/fs/cgroup/system.slice').glob('docker-*.scope/cgroup.procs'):
    try:
        if cg.read_text().strip():active.append(str(cg.parent))
    except FileNotFoundError:pass
print(json.dumps({'host':socket.gethostname().split('.')[0],'psi':pathlib.Path('/proc/pressure/io').read_text(),
 'service':subprocess.check_output(['systemctl','show','k3s','-p','NRestarts','-p','ExecMainStartTimestampMonotonic','-p','ActiveState'],text=True),'docker':active}))"""
    def services():
        return command(['systemctl','show','k3s','-p','NRestarts','-p','ExecMainStartTimestampMonotonic','-p','ActiveState'])
    def peer():
        value=json.loads(command(['runuser','-u',user.pw_name,'--','ssh','-oBatchMode=yes','-oStrictHostKeyChecking=yes',
            '-oConnectTimeout=5','sup@10.0.0.72','python3 -c '+shlex.quote(peer_code)]))
        if value['host']!='proxmox2-old' or value['docker']:raise RuntimeError('Peer identity/Docker pause drift')
        return value
    baseline_local=services();baseline_peer=peer()
    if 'ActiveState=active' not in baseline_local or 'ActiveState=active' not in baseline_peer['service']:
        raise RuntimeError('k3s not active')
    last_io=last_maintenance=0;observed={}
    deadline=time.monotonic()+900
    def monitor(force=False):
        nonlocal last_io,last_maintenance,observed
        if time.monotonic()>deadline:raise RuntimeError('Extraction/validation deadline exceeded')
        if force or time.monotonic()-last_io>=5:
            remote=peer();observed={'local':helper.psi(Path('/proc/pressure/io').read_text()),'peer':helper.psi(remote['psi'])}
            log('io_observation',pressure=observed)
            if not all(helper.low_pressure(v) for v in observed.values()):raise RuntimeError('High I/O pressure')
            if services()!=baseline_local or remote['service']!=baseline_peer['service']:raise RuntimeError('k3s restarted/changed')
            last_io=time.monotonic()
        if force or time.monotonic()-last_maintenance>=30:
            maintenance();last_maintenance=time.monotonic()
    mounted=False;applied_cap=0
    try:
        monitor(True)
        command(['mount','-t','ext4','-o','loop,ro,noload',str(image),str(mountpoint)])
        mounted=True
        info=json.loads(command(['findmnt','--json','--mountpoint',str(mountpoint),'-o','SOURCE,TARGET,FSTYPE,OPTIONS']))['filesystems']
        if len(info)!=1 or 'ro' not in info[0]['options'].split(',') or info[0]['fstype']!='ext4':
            raise RuntimeError('Read-only working mount verification failed')
        log('working_image_mounted_read_only',mount=info[0])
        entries=[]
        for name in FILES:
            source=mountpoint/name
            if source.is_symlink() or not source.is_file():raise RuntimeError('Missing/unexpected database component: '+name)
            entries.append({'name':name,'bytes':source.stat().st_size})
        if not entries[0]['bytes'] or not entries[1]['bytes']:raise RuntimeError('Database/WAL unexpectedly empty')
        (folder/'extraction-plan.json').write_text(json.dumps(entries,indent=2)+'\n')
        log('cap_requested',previousBytesPerSecond=0,newBytesPerSecond=CAP,reason='Initial bounded DB/WAL/SHM extraction and hash verification',pressure=observed,outcome='requested')
        applied_cap=CAP
        log('cap_confirmed',previousBytesPerSecond=0,newBytesPerSecond=CAP,reason='Live worker configured fixed chunk pacing',pressure=observed,workerPID=os.getpid(),chunkBytes=helper.CHUNK,outcome='applied before first read')
        hashes=[]
        for entry in entries:
            name=entry['name'];size=entry['bytes']
            with (mountpoint/name).open('rb',buffering=0) as source,(destination/name).open('xb',buffering=0) as target:
                digest,count,elapsed=helper.capped_stream(source,target,size=size,cap=CAP,monitor=monitor)
                os.fsync(target.fileno())
            with (destination/name).open('rb',buffering=0) as target:
                verified,_,_=helper.capped_stream(target,size=size,cap=CAP,monitor=monitor)
            if verified!=digest:raise RuntimeError('Extracted component checksum mismatch: '+name)
            (destination/name).chmod(0o600)
            hashes.append(dict(entry,sha256=digest))
            log('component_extracted_verified',name=name,bytes=count,sha256=digest)
        command(['umount',str(mountpoint)]);mounted=False
        log('working_image_unmounted')
        applied_cap=0
        log('cap_confirmed',previousBytesPerSecond=CAP,newBytesPerSecond=0,reason='Extraction and component checksum verification done; handles closed',pressure=observed,outcome='copy worker idle')
        (folder/'extracted-components.json').write_text(json.dumps(hashes,indent=2)+'\n')
        monitor(True)
        log('sqlite_check_started',sqliteVersion=sqlite3.sqlite_version,immutable=False,walIncluded=True)
        connection=sqlite3.connect('file:'+str(destination/'kuma.db')+'?mode=rw',uri=True,timeout=5)
        end=time.monotonic()+60
        connection.set_progress_handler(lambda:1 if time.monotonic()>end else 0,10000)
        try:
            connection.execute('PRAGMA query_only=ON')
            integrity=[row[0] for row in connection.execute('PRAGMA integrity_check')]
            if integrity!=['ok']:
                (folder/'integrity-errors.json').write_text(json.dumps(integrity,indent=2)+'\n')
                raise RuntimeError('SQLite integrity_check failed; details saved privately')
            tables={row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            counts={table:connection.execute('SELECT COUNT(*) FROM "'+table+'"').fetchone()[0]
                    for table in ('monitor','user','heartbeat','notification') if table in tables}
            result={'sqliteVersion':sqlite3.sqlite_version,'integrityCheck':'ok','walIncluded':True,'immutable':False,
                    'tableCount':len(tables),'selectedRowCounts':counts,'workingDatabase':str(destination/'kuma.db'),
                    'originalAndPreservationUntouched':True}
        finally:connection.close()
        monitor(True)
        (folder/'database-validated.json').write_text(json.dumps(result,indent=2)+'\n')
        log('sqlite_integrity_passed',**result)
        print('SQLite working-copy integrity passed; original volume has NOT been repaired or restored. Keep maintenance active for recovery/cutover planning.')
    except BaseException as exc:
        log('validation_failed',reason=str(exc),pressure=observed,outcome='partial extraction/check retained; not approved for app startup')
        raise
    finally:
        if mounted:
            try:
                command(['umount',str(mountpoint)])
                log('working_image_unmounted_after_failure')
            except Exception as exc:log('unmount_failed',reason=str(exc),mountpoint=str(mountpoint))
        if applied_cap:
            log('cap_confirmed',previousBytesPerSecond=applied_cap,newBytesPerSecond=0,reason='Worker stopped; component file handles closed',pressure=observed,outcome='no further copy reads')


if __name__=='__main__':
    try:main()
    except (RuntimeError,OSError,ValueError,KeyError,sqlite3.Error,subprocess.SubprocessError) as exc:
        raise SystemExit('Refused/failed; retain evidence and do not start Kuma: '+str(exc))
