#!/usr/bin/env python3
"""Preserve Kuma's 256 MiB block device at a fixed 1 MiB/s. No mount or repair."""
import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import pwd
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import sys
import time

V = 'pvc-326def38-faba-49f6-8a78-ebf719227220'
SIZE = 268435456
CAP = 1048576
CHUNK = 65536
TICKET = 'longhorn-api-kuma-validation-20261118'


def psi(text):
    return {line.split()[0]: float(line.split()[1].split('=')[1]) for line in text.splitlines()}


def low_pressure(value):
    return value.get('some', 100) <= 10 and value.get('full', 100) <= 2


def capped_stream(src, dst=None, *, size=SIZE, cap=CAP, chunk=CHUNK, monitor=lambda: None):
    digest=hashlib.sha256();count=0;started=time.monotonic()
    while count<size:
        monitor()
        # No catch-up bursts after slow guards: pace every individual chunk.
        tick=time.monotonic()
        block=src.read(min(chunk,size-count))
        if not block: raise RuntimeError('Unexpected EOF')
        if dst is not None:
            written=dst.write(block)
            if written!=len(block): raise RuntimeError('Short destination write')
        digest.update(block);count+=len(block)
        time.sleep(max(0,len(block)/cap-(time.monotonic()-tick)))
    return digest.hexdigest(),count,time.monotonic()-started


def preservation_source(root=Path('/var/tmp')):
    candidates=sorted(p for p in root.glob('kuma-preservation-*') if (p/'verified.json').is_file())
    if not candidates:
        raise RuntimeError('No verified preservation image found')
    folder=candidates[-1]
    metadata=json.loads((folder/'verified.json').read_text())
    image=(folder/'source-preserved.img').resolve(strict=True)
    info=image.stat()
    if (not stat.S_ISREG(info.st_mode) or info.st_size!=SIZE
        or info.st_mode & 0o222 or metadata.get('bytes')!=SIZE
        or metadata.get('sourceUUID')!='cb578054-4075-47b7-afe3-c1f8c31381da'
        or metadata.get('sha256')!='3470b1fd2130d7b2ed136725941695177146a753f9593436af8460ce8d1666a1'):
        raise RuntimeError('Preservation file/verified metadata does not match the confirmed checkpoint')
    return image, metadata


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--working-copy', action='store_true', help='Copy the latest verified preservation image into a separate writable recovery image')
    ap.add_argument('--execute', action='store_true')
    ap.add_argument('--yes', action='store_true')
    ap.add_argument('--peer-local-user', default=os.environ.get('SUDO_USER') or 'sup',
                    help='Local non-root account whose existing SSH trust/authentication is used')
    ap.add_argument('--peer-ssh', default='sup@10.0.0.72', help='Existing trusted SSH account on proxmox2-old; no sudo needed')
    args = ap.parse_args()
    if os.geteuid()!=0 or socket.gethostname().split('.')[0]!='proxmox3':
        ap.error('Run with sudo on proxmox3')
    image_source, image_metadata = preservation_source() if args.working_copy else (None, None)
    kube=['kubectl','--kubeconfig=/etc/rancher/k3s/k3s.yaml','--request-timeout=15s']

    def command(argv):
        p=subprocess.run(argv,capture_output=True,text=True,timeout=25)
        if p.returncode:
            raise RuntimeError('Command failed: '+str(argv[:4])+': '+p.stderr.strip())
        return p.stdout

    def get(kind,name=None,namespace='longhorn-system'):
        return json.loads(command(kube+['get',kind]+([name] if name else [])+
                                  (['-n',namespace] if namespace else ['-A'])+['-o','json']))

    def service():
        return command(['systemctl','show','k3s','-p','NRestarts','-p','ExecMainStartTimestampMonotonic','-p','ActiveState'])

    # All remote operations are fixed read-only commands, with existing host trust required.
    peer_code = """import json,socket,pathlib,subprocess
active=[]
for cg in pathlib.Path('/sys/fs/cgroup/system.slice').glob('docker-*.scope/cgroup.procs'):
    try:
        if cg.read_text().strip(): active.append(str(cg.parent.name))
    except FileNotFoundError:
        pass
print(json.dumps({'host':socket.gethostname().split('.')[0],
    'psi':pathlib.Path('/proc/pressure/io').read_text(),
    'service':subprocess.check_output(['systemctl','show','k3s','-p','NRestarts',
        '-p','ExecMainStartTimestampMonotonic','-p','ActiveState'],text=True),
    'activeDockerScopes':active}))
"""
    import shlex
    local_ssh_user=pwd.getpwnam(args.peer_local_user)
    if local_ssh_user.pw_uid==0:
        raise RuntimeError('Peer SSH must use an existing non-root local account')
    if not shutil.which('runuser'):
        raise RuntimeError('runuser is required to use invoking-user SSH trust/authentication')
    peer_argv=['runuser','-u',local_ssh_user.pw_name,'--','ssh','-oBatchMode=yes','-oStrictHostKeyChecking=yes','-oConnectTimeout=5',
               args.peer_ssh,'python3 -c '+shlex.quote(peer_code)]

    def peer():
        obj=json.loads(command(peer_argv))
        if obj['host']!='proxmox2-old': raise RuntimeError('Unexpected peer SSH host')
        if obj.get('activeDockerScopes'):
            raise RuntimeError('Standalone Docker workloads active again on peer: '+str(obj['activeDockerScopes']))
        return obj

    dev=Path('/dev/longhorn/'+V).resolve(strict=True)
    identity=dev.stat()
    if not stat.S_ISBLK(identity.st_mode): raise RuntimeError('Source is not a block device')
    number=f'{os.major(identity.st_rdev)}:{os.minor(identity.st_rdev)}'
    helper_spec=importlib.util.spec_from_file_location('kuma_fs',Path(__file__).with_name('check-kuma-filesystem.py'))
    helper=importlib.util.module_from_spec(helper_spec);helper_spec.loader.exec_module(helper)

    def unmounted():
        if dev.stat().st_rdev!=identity.st_rdev: raise RuntimeError('Device identity changed')
        for proc in Path('/proc').glob('[0-9]*'):
            if any(line.split()[2]==number for line in helper.read_process_mounts(proc)):
                raise RuntimeError('Source mounted: '+str(proc))
        if list(Path('/sys/dev/block',number,'holders').iterdir()): raise RuntimeError('Block holders exist')

    def storage_guards():
        volume=get('volumes.longhorn.io',V)
        if (volume['metadata']['uid']!='7150b0cb-18c8-448f-9831-cefca486672c'
            or volume['status']['state']!='attached' or volume['status'].get('currentNodeID')!='proxmox3'
            or volume['spec']['numberOfReplicas']!=1): raise RuntimeError('Volume state drift')
        tickets=get('volumeattachments.longhorn.io',V)['spec'].get('attachmentTickets',{})
        if set(tickets)!={TICKET} or tickets[TICKET].get('nodeID')!='proxmox3': raise RuntimeError('Attachment ticket drift')
        replicas=[r for r in get('replicas.longhorn.io')['items'] if r['spec'].get('volumeName')==V]
        if (len(replicas)!=1 or replicas[0]['metadata']['name']!=V+'-r-9cdce044'
            or replicas[0]['spec'].get('nodeID')!='proxmox3' or replicas[0]['spec'].get('failedAt')
            or replicas[0]['status'].get('currentState')!='running'): raise RuntimeError('Sole replica not running safely on proxmox3')
        engines=[e for e in get('engines.longhorn.io')['items'] if e['spec'].get('volumeName')==V and e['status'].get('currentState')=='running']
        if len(engines)!=1 or engines[0]['spec'].get('nodeID')!='proxmox3': raise RuntimeError('Unexpected running engine')
        modes=engines[0]['status'].get('replicaModeMap') or {}
        if len(modes)!=1 or set(modes.values())!={'RW'}: raise RuntimeError('Replica engine mode is not RW')
        unmounted()
        return volume

    def maintenance():
        p=subprocess.run([sys.executable,str(Path(__file__).with_name('kuma-recovery-maintenance.py')),'verify'],
                         capture_output=True,text=True,timeout=180)
        if p.returncode: raise RuntimeError('Maintenance verification failed: '+p.stdout+p.stderr)
        return p.stdout

    maintenance()
    volume=storage_guards()
    signature=command(['blkid','-p','-o','export',str(dev)])
    if 'UUID=cb578054-4075-47b7-afe3-c1f8c31381da\n' not in signature or 'TYPE=ext4\n' not in signature:
        raise RuntimeError('Unexpected filesystem signature')
    if int(command(['blockdev','--getsize64',str(dev)]).strip())!=SIZE: raise RuntimeError('Unexpected source size')
    local_service=service();peer_initial=peer()
    if 'ActiveState=active' not in local_service or 'ActiveState=active' not in peer_initial['service']:
        raise RuntimeError('k3s not active on both hosts')
    observed={'local':psi(Path('/proc/pressure/io').read_text()),'peer':psi(peer_initial['psi'])}
    if not all(low_pressure(x) for x in observed.values()): raise RuntimeError('Initial I/O pressure high: '+str(observed))
    if shutil.disk_usage('/var/tmp').free < 4*SIZE: raise RuntimeError('Insufficient destination free space for preservation and working copies')
    print('Guards passed. Plan: 256 MiB '+('verified preservation image -> working image' if args.working_copy else 'source device -> preservation image')+'; fixed cap 1 MiB/s.')
    if image_source: print('Preservation source:',image_source)
    print('Peer SSH verified:',peer_initial['host'],'I/O:',json.dumps(observed))
    print('Image verification uses the same read cap. No journal replay, mount, repair or database startup.')
    if not args.execute:
        print('Dry run; no data copied. Use --execute to run. Existing SSH host verification is required.')
        return
    if not args.yes:
        if not (shutil.which('dialog') and sys.stdin.isatty() and sys.stdout.isatty()):
            raise RuntimeError('Interactive dialog unavailable; explicit --execute --yes required')
        if subprocess.run(['dialog','--defaultno','--yesno',
            ('Create a separate working copy from the verified preservation image at 1 MiB/s? ' if args.working_copy else 'Preserve the 256 MiB Kuma block device at 1 MiB/s while cluster apps remain stopped? ')+
            'Confirm no old-host Kuma writers/device/mount. No source writes or repair.', '11','76']).returncode:
            return
    maintenance();volume=storage_guards()
    folder=Path('/var/tmp')/(('kuma-working-' if args.working_copy else 'kuma-preservation-')+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    folder.mkdir(mode=0o700)
    source_path=image_source if args.working_copy else dev
    output_path=folder/('working.img' if args.working_copy else 'source-preserved.img')
    source_identity=source_path.stat()
    if args.working_copy:
        confirmed_source, confirmed_metadata=preservation_source()
        if confirmed_source!=image_source or confirmed_metadata!=image_metadata:
            raise RuntimeError('Preservation identity changed since initial checks')
    (folder/'volume-before.json').write_text(json.dumps(volume,indent=2)+'\n')
    (folder/'host-baseline.json').write_text(json.dumps({'localService':local_service,'peer':peer_initial,'pressure':observed},indent=2)+'\n')
    print('Recovery directory:',folder,flush=True)

    def event(kind,**fields):
        row=dict(time=datetime.datetime.now(datetime.timezone.utc).isoformat(),event=kind,**fields)
        for filename in ['actions.jsonl']+(['cap-changes.jsonl'] if kind.startswith('cap_') else []):
            with (folder/filename).open('a') as f:
                f.write(json.dumps(row)+'\n');f.flush();os.fsync(f.fileno())
        print(json.dumps(row),flush=True)

    transfer_deadline=time.monotonic()+1800
    last_io=last_guard=last_maintenance=0
    def monitor(force=False):
        nonlocal last_io,last_guard,last_maintenance,observed
        now=time.monotonic()
        if now>transfer_deadline: raise RuntimeError('Total transfer/verification deadline exceeded')
        if force or now-last_io>=5:
            remote=peer();local=psi(Path('/proc/pressure/io').read_text())
            observed={'local':local,'peer':psi(remote['psi'])}
            event('io_observation',pressure=observed)
            if not all(low_pressure(x) for x in observed.values()): raise RuntimeError('High I/O pressure; transfer stops')
            if service()!=local_service or remote['service']!=peer_initial['service']: raise RuntimeError('k3s service/restart identity changed')
            last_io=time.monotonic()
        if force or time.monotonic()-last_guard>=15:
            storage_guards();last_guard=time.monotonic()
        if force or time.monotonic()-last_maintenance>=30:
            maintenance();last_maintenance=time.monotonic()

    event('cap_requested',previousBytesPerSecond=0,newBytesPerSecond=CAP,reason='Initial conservative preservation/verification cap',pressure=observed,outcome='requested')
    applied_cap=0
    try:
        monitor(True)
        with source_path.open('rb',buffering=0) as src, output_path.open('xb',buffering=0) as dst:
            opened=os.fstat(src.fileno())
            if args.working_copy:
                if (opened.st_ino,opened.st_dev,opened.st_size)!=(source_identity.st_ino,source_identity.st_dev,SIZE):
                    raise RuntimeError('Preservation FD identity mismatch')
            elif opened.st_rdev!=identity.st_rdev:
                raise RuntimeError('Source FD identity mismatch')
            applied_cap=CAP
            event('cap_confirmed',previousBytesPerSecond=0,newBytesPerSecond=CAP,reason='Live worker configured fixed chunk pacing',pressure=observed,workerPID=os.getpid(),chunkBytes=CHUNK,outcome='applied before first read')
            source_hash,count,duration=capped_stream(src,dst,monitor=monitor);os.fsync(dst.fileno())
        event('copy_complete',bytes=count,seconds=duration,sha256=source_hash)
        if args.working_copy and source_hash!=image_metadata['sha256']:
            raise RuntimeError('Preservation content hash differs from verified checkpoint')
        with output_path.open('rb',buffering=0) as image:
            image_hash,verified,duration=capped_stream(image,monitor=monitor)
        if source_hash!=image_hash: raise RuntimeError('Preservation image checksum mismatch')
        monitor(True)
        output_path.chmod(0o600 if args.working_copy else 0o400)
        result={'bytes':verified,'sha256':image_hash,'sourceUUID':'cb578054-4075-47b7-afe3-c1f8c31381da','sourceWritten':False,'mountOrRepairAttempted':False,'purpose':'working-copy' if args.working_copy else 'preservation','sourcePath':str(source_path),'imagePath':str(output_path)}
        (folder/'verified.json').write_text(json.dumps(result,indent=2)+'\n')
        event('image_verified',**result)
        event('cap_confirmed',previousBytesPerSecond=CAP,newBytesPerSecond=0,reason='Copy and image checksum verification finished; file handles closed',pressure=observed,outcome='worker idle')
        print('Working copy verified; source preservation image unchanged. No recovery/repair has run yet.' if args.working_copy else 'Preservation complete. Keep this image unchanged; recovery must use a separate working copy.')
    except BaseException as exc:
        event('transfer_failed',reason=str(exc),pressure=observed,outcome='partial image retained; not approved for recovery')
        event('cap_confirmed',previousBytesPerSecond=applied_cap,newBytesPerSecond=0,reason='Worker stopped on failure/interruption; handles closed',pressure=observed,outcome='no further reads')
        raise


if __name__=='__main__':
    try: main()
    except (RuntimeError,OSError,ValueError,KeyError,subprocess.SubprocessError) as exc:
        raise SystemExit('Refused/failed: '+str(exc))
