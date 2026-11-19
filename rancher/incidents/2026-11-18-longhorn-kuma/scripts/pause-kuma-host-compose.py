#!/usr/bin/env python3
"""Pause three approved standalone Docker containers on proxmox2-old; save restore state."""
import argparse
import datetime
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys

TARGETS = [('home-assistant-watchdog-springboot-1', 'ff5164114c53'),
           ('scrutiny', '01af255a85bb'),
           ('home-assistant-watchdog-postgres-1', 'efd0325fc503')]


def docker(*args):
    p = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=140)
    if p.returncode:
        raise RuntimeError(p.stderr.strip())
    return p.stdout


def inspect(name):
    obj = json.loads(docker('inspect', '--format', '{{json .}}', name))
    # Deliberately retain only non-secret restoration fields; never save full inspect/env.
    return {'name': obj['Name'].lstrip('/'), 'id': obj['Id'],
            'running': obj['State']['Running'], 'status': obj['State']['Status'],
            'restartPolicy': obj['HostConfig']['RestartPolicy'],
            'autoRemove': obj['HostConfig'].get('AutoRemove', False),
            'stopSignal': obj['Config'].get('StopSignal') or 'SIGTERM',
            'composeProject': obj['Config'].get('Labels', {}).get('com.docker.compose.project'),
            'composeService': obj['Config'].get('Labels', {}).get('com.docker.compose.service')}


def policy_text(policy):
    name = policy['Name'] or 'no'
    retries = policy.get('MaximumRetryCount', 0)
    return name + (':' + str(retries) if name == 'on-failure' and retries else '')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('mode', choices=['plan', 'stop', 'verify', 'restore'])
    ap.add_argument('--execute', action='store_true')
    ap.add_argument('--yes', action='store_true')
    ap.add_argument('--directory', type=Path)
    args = ap.parse_args()
    if os.geteuid() != 0 or socket.gethostname().split('.')[0] != 'proxmox2-old':
        raise RuntimeError('Run with sudo on proxmox2-old only')
    folder = args.directory
    if folder is None:
        candidates = sorted(p for p in Path('/var/tmp').glob('kuma-host-compose-*') if (p / 'baseline.json').is_file())
        if candidates:
            folder = candidates[-1]
        elif args.mode in ('plan', 'stop'):
            folder = Path('/var/tmp') / ('kuma-host-compose-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
        else:
            raise RuntimeError('No saved baseline found')
    baseline_path = folder / 'baseline.json'
    if baseline_path.exists():
        baseline = json.loads(baseline_path.read_text())
    else:
        baseline = [inspect(name) for name, _ in TARGETS]
        for row, (name, prefix) in zip(baseline, TARGETS):
            if row['name'] != name or not row['id'].startswith(prefix) or row['autoRemove']:
                raise RuntimeError('Unexpected identity or auto-remove container: ' + name)
        folder.mkdir(mode=0o700)
        baseline_path.write_text(json.dumps(baseline, indent=2) + '\n')
    if len(baseline) != len(TARGETS):
        raise RuntimeError('Invalid baseline')
    for row, (name, prefix) in zip(baseline, TARGETS):
        if row['name'] != name or not row['id'].startswith(prefix):
            raise RuntimeError('Baseline target mismatch')
        live = inspect(name)
        if live['id'] != row['id'] or live['autoRemove'] or live['stopSignal'].upper() in ('SIGKILL', 'KILL', '9'):
            raise RuntimeError('Container replaced or auto-remove enabled: ' + name)
    print('Saved restoration baseline:', folder)
    for row in baseline:
        print(row['name'], 'originally running=' + str(row['running']), 'restart=' + policy_text(row['restartPolicy']))

    def log(event, **fields):
        line = json.dumps(dict(time=datetime.datetime.now(datetime.timezone.utc).isoformat(), event=event, **fields))
        with (folder / 'actions.jsonl').open('a') as f:
            f.write(line + '\n'); f.flush(); os.fsync(f.fileno())
        print(line, flush=True)

    def checked(row):
        live = inspect(row['name'])
        if live['id'] != row['id']:
            raise RuntimeError('Container identity changed: ' + row['name'])
        return live

    def verify():
        for row in baseline:
            live = checked(row)
            if live['running'] or live['status'] not in ('exited', 'created') or live['restartPolicy']['Name'] != 'no':
                raise RuntimeError('Container not safely paused: ' + row['name'])
        log('three_approved_docker_containers_stopped_restart_disabled')

    if args.mode == 'plan':
        print('Plan: disable automatic restart, stop watchdog app and Scrutiny, then PostgreSQL. No volumes removed.')
        return
    if args.mode == 'verify':
        verify(); return
    if not args.execute:
        print('Dry run. Use --execute for', args.mode); return
    if not args.yes:
        if not (shutil.which('dialog') and sys.stdin.isatty() and sys.stdout.isatty()):
            raise RuntimeError('Interactive dialog unavailable; explicit --execute --yes required')
        if subprocess.run(['dialog', '--defaultno', '--yesno', args.mode.upper() +
                           ' the three approved Docker containers using saved identities/states? No volumes deleted.', '10', '76']).returncode:
            return
    if args.mode == 'stop':
        for row in baseline:
            live = checked(row)
            if live['restartPolicy'] not in (row['restartPolicy'], {'Name': 'no', 'MaximumRetryCount': 0}):
                raise RuntimeError('Restart policy changed unexpectedly: ' + row['name'])
        for row in baseline:
            checked(row)
            log('restart_disable_requested', name=row['name'])
            docker('update', '--restart=no', row['id'])
            if checked(row)['restartPolicy']['Name'] != 'no':
                raise RuntimeError('Restart disable verification failed')
            log('restart_disabled_confirmed', name=row['name'])
        for row in baseline:
            if checked(row)['running']:
                log('graceful_stop_requested', name=row['name'], forceKill=False, clientTimeoutSeconds=140)
                # -1 sends the configured stop signal and NEVER escalates to SIGKILL.
                # Client timeout leaves the operation pending and blocks further steps.
                docker('stop', '--time=-1', row['id'])
            live = checked(row)
            if live['running'] or live['status'] not in ('exited', 'created'):
                raise RuntimeError('Stop not confirmed: ' + row['name'])
            log('stopped_confirmed', name=row['name'])
        verify()
        print('Do not run compose up/watchdogs during maintenance. Kubernetes maintenance remains separate.')
    else:
        # PostgreSQL first; wait for its saved Docker health check when present.
        for row in reversed(baseline):
            checked(row)
            if row['running']:
                log('start_requested', name=row['name'])
                docker('start', row['id'])
                if not checked(row)['running']:
                    raise RuntimeError('Start verification failed: ' + row['name'])
                if row['name'].endswith('postgres-1'):
                    import time
                    deadline = time.monotonic() + 120
                    while True:
                        health = docker('inspect', '--format', '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}', row['id']).strip()
                        if health == 'healthy': break
                        if health == 'none':
                            raise RuntimeError('Database has no health check; verify readiness before starting watchdog')
                        if time.monotonic() > deadline: raise RuntimeError('Database readiness timed out')
                        time.sleep(3)
                log('started_confirmed', name=row['name'])
            elif checked(row)['running']:
                raise RuntimeError('Originally stopped container unexpectedly running: ' + row['name'])
        for row in baseline:
            checked(row)
            docker('update', '--restart=' + policy_text(row['restartPolicy']), row['id'])
            live = checked(row)
            if live['restartPolicy'] != row['restartPolicy'] or live['running'] != row['running']:
                raise RuntimeError('Restoration verification failed: ' + row['name'])
            log('original_state_restored', name=row['name'])


if __name__ == '__main__':
    try: main()
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        raise SystemExit('Stopped/refused; inspect saved baseline/action log, partial changes may remain: ' + str(exc))
