#!/usr/bin/env python3
"""Replay ext4 journal on the verified disposable Kuma working image only."""
import argparse
import datetime
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import sys

WORK = Path('/var/tmp/kuma-working-20261118T192530502463Z')
PRESERVED = Path('/var/tmp/kuma-preservation-20261118T184540364827Z/source-preserved.img')
HASH = '3470b1fd2130d7b2ed136725941695177146a753f9593436af8460ce8d1666a1'


def validate(folder, allow_previous_journal=False):
    image = folder / 'working.img'
    if image.is_symlink() or folder.is_symlink():
        raise RuntimeError('Symlink working path refused')
    info = image.stat()
    marker = json.loads((folder / 'verified.json').read_text())
    if (not stat.S_ISREG(info.st_mode) or info.st_size != 268435456 or info.st_nlink != 1
        or marker.get('sha256') != HASH or marker.get('purpose') != 'working-copy'
        or marker.get('sourcePath') != str(PRESERVED) or marker.get('imagePath') != str(image)):
        raise RuntimeError('Working image checkpoint/identity mismatch')
    if os.path.samefile(image, PRESERVED):
        raise RuntimeError('Working image aliases preservation image')
    if (folder / 'journal-recovery-started.json').exists() and not allow_previous_journal:
        raise RuntimeError('Recovery already attempted; inspect logs before retrying')
    p = subprocess.run(['losetup', '--associated', str(image), '--noheadings', '--output', 'NAME'],
                       capture_output=True, text=True, timeout=15, check=True)
    if p.stdout.strip():
        raise RuntimeError('Working image is attached to a loop device')
    return image, info


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--execute', action='store_true')
    ap.add_argument('--yes', action='store_true')
    args = ap.parse_args()
    if os.geteuid() != 0 or socket.gethostname().split('.')[0] != 'proxmox3':
        raise RuntimeError('Run with sudo on proxmox3')
    image, identity = validate(WORK)
    commands = [['e2fsck', '-p', '-E', 'journal_only', str(image)],
                ['e2fsck', '-f', '-n', str(image)]]
    print('Disposable working image:', image)
    print('Plan: replay journal on working image, then check it read-only. No source or preservation writes.')
    print(json.dumps(commands, indent=2))
    if not args.execute:
        print('Dry run. Use --execute for journal recovery on this working image only.')
        return
    if not args.yes:
        if not (shutil.which('dialog') and sys.stdin.isatty() and sys.stdout.isatty()):
            raise RuntimeError('Interactive dialog unavailable; explicit --execute --yes required')
        if subprocess.run(['dialog', '--defaultno', '--yesno',
                           'Replay ext4 journal on the verified disposable working.img ONLY, then read-only check? '
                           'Original Longhorn device and preservation image remain unchanged.', '11', '76']).returncode:
            return
    p = subprocess.run([sys.executable, str(Path(__file__).with_name('kuma-recovery-maintenance.py')), 'verify'],
                       capture_output=True, text=True, timeout=180)
    if p.returncode:
        raise RuntimeError('Maintenance guard failed: ' + p.stdout + p.stderr)
    image, current = validate(WORK)
    if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
        raise RuntimeError('Working image changed during confirmation')
    pressure = Path('/proc/pressure/io').read_text()
    values = {line.split()[0]: float(line.split()[1].split('=')[1]) for line in pressure.splitlines()}
    if values.get('some', 100) > 10 or values.get('full', 100) > 2:
        raise RuntimeError('High local I/O pressure; no journal recovery started')
    (WORK / 'journal-recovery-started.json').write_text(json.dumps({
        'time': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'workingImage': str(image), 'commands': commands, 'ioPressure': pressure,
        'originalAndPreservationUntouched': True}, indent=2) + '\n')
    for index, command in enumerate(commands):
        log = WORK / ('journal-replay.txt' if index == 0 else 'filesystem-after-journal.txt')
        print('Running:', ' '.join(command), flush=True)
        with log.open('w') as output:
            try:
                result = subprocess.run(command, stdout=output, stderr=subprocess.STDOUT, timeout=60)
            except subprocess.TimeoutExpired:
                raise RuntimeError('Working-image operation timed out; inspect ' + str(log))
        text = log.read_text()
        print(text, end='')
        (WORK / (log.stem + '-result.json')).write_text(json.dumps({
            'exitCode': result.returncode, 'time': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'sourceWritten': False, 'workingImageChanged': index == 0}) + '\n')
        acceptable = (0, 1) if index == 0 else (0,)
        if result.returncode not in acceptable:
            raise RuntimeError('Working-image check needs review; exit ' + str(result.returncode))
        if index == 1 and any(message in text for message in ('count wrong', 'WARNING', 'UNEXPECTED INCONSISTENCY', 'skipping journal recovery')):
            raise RuntimeError('Filesystem text still reports an unresolved issue despite exit code; inspect log')
    (WORK / 'filesystem-validated.json').write_text(json.dumps({
        'time': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'journalReplayWorkingImageOnly': True, 'readOnlyFilesystemCheckPassed': True,
        'databaseIntegrityChecked': False}) + '\n')
    print('Working filesystem check passed. SQLite/WAL integrity still unverified; keep applications stopped.')


if __name__ == '__main__':
    try: main()
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        raise SystemExit('Refused/failed; do not repair source or retry blindly: ' + str(exc))
