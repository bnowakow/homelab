#!/usr/bin/python3
"""Diagnose narrowly defined ingress failures and prepare an approved values edit.

This helper never applies Kubernetes resources. The caller owns confirmation,
Fleet reconciliation, rollout/HTTP validation and staging.
"""
import argparse
import copy
import datetime
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

import yaml

CHECK_PATH = 'helm-upgrade.bnowakowski.pl/check-path'
ARR = {'radarr': 7878, 'prowlarr': 9696, 'sonarr': 8989}


def run(args):
    result = subprocess.run(args, text=True, capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError('Diagnostic command failed: ' + result.stderr.strip())
    return result.stdout


def kube(args, *parts):
    return ['kubectl', '--kubeconfig', args.kubeconfig, '--request-timeout=15s', *parts]


def get(args, *parts):
    return json.loads(run(kube(args, *parts, '-o', 'json')))


def probe(url, follow=False):
    options = ['curl', '--silent', '--show-error', '--connect-timeout', '3',
               '--max-time', '10', '--dump-header', '-', '--output', '/dev/null',
               '--write-out', '\nSTATUS:%{http_code}', url]
    if follow:
        options[1:1] = ['--location', '--proto-redir', '=https' if url.startswith('https:') else '=http,https']
    text = run(options)
    headers = {}
    for line in text.splitlines():
        if ':' in line:
            key, value = line.split(':', 1)
            headers[key.lower()] = value.strip()
    return {'status': int(headers['status']), 'location': headers.get('location'),
            'jenkins': bool(headers.get('x-jenkins'))}


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def diagnose(args):
    if args.app not in ARR and args.app != 'jenkins':
        return None
    file = Path(args.values).resolve()
    text = file.read_text()
    values = yaml.safe_load(text) or {}
    ingresses = get(args, '-n', args.namespace, 'get', 'ingress')['items']
    ingresses = [o for o in ingresses if
                 o['metadata'].get('annotations', {}).get('meta.helm.sh/release-name',
                 o['metadata'].get('labels', {}).get('app.kubernetes.io/instance')) == args.app]
    if not ingresses or any(o['metadata'].get('annotations', {}).get(CHECK_PATH, '/') != '/' for o in ingresses):
        return None
    hosts = sorted({r['host'] for o in ingresses for r in o['spec'].get('rules', [])})
    tls_hosts = {h for o in ingresses for tls in o['spec'].get('tls', []) for h in tls.get('hosts', [])}
    if not hosts or not set(hosts).issubset(tls_hosts):
        return None
    roots = {h: probe('https://' + h + '/') for h in hosts}
    plan = {'app': args.app, 'namespace': args.namespace, 'values': str(file),
            'originalSha256': sha(text), 'hosts': hosts}
    if args.app == 'jenkins':
        if not all(o['status'] == 403 and o['jenkins'] for o in roots.values()):
            return None
        if not all(probe('https://' + h + '/login', True)['status'] == 200 for h in hosts):
            return None
        if not values.get('ingress', {}).get('main', {}).get('enabled'):
            return None
        plan.update(kind='jenkins-login', description=
                    'Jenkins identifies itself and returns 403 at /; both HTTPS login checks pass. '
                    'Set the ingress health-check annotation to /login. Authentication stays unchanged.')
        return plan
    # Confirm the exact downgrade redirect, not a generic 404 or broken router.
    for host, response in roots.items():
        target = urlsplit(response.get('location') or '')
        if (response['status'] not in (301, 302, 303, 307, 308) or target.scheme != 'http'
                or target.hostname != host or not target.path.rstrip('/').endswith('/login')):
            return None
    services = get(args, '-n', args.namespace, 'get', 'services')['items']
    service = next((o for o in services if o['metadata']['name'] == args.app), None)
    if not service or service['spec'].get('clusterIP') in (None, 'None'):
        return None
    port = next((p['port'] for p in service['spec']['ports'] if p.get('name') == 'main'), None)
    if port != ARR[args.app]:
        return None
    ip = service['spec']['clusterIP']
    address = '[' + ip + ']' if ':' in ip else ip
    if probe('http://' + address + ':' + str(port) + '/', True)['status'] != 200:
        return None
    pods = get(args, '-n', args.namespace, 'get', 'pods')['items']
    candidates = [(p, c) for p in pods if p.get('status', {}).get('phase') == 'Running'
                  and p['metadata'].get('labels', {}).get('app.kubernetes.io/instance') == args.app
                  for c in p['spec']['containers'] if args.app in c['image'].lower()]
    if len(candidates) != 1:
        return None
    pod, container = candidates[0]
    prefix = args.app.upper() + '__'
    env = {e['name']: e.get('value', '') for e in container.get('env', [])}
    # Never override an existing trust policy or external authentication mode.
    if env.get(prefix + 'SERVER__TRUSTEDNETWORKS') or any(
            e['name'] == prefix + 'SERVER__TRUSTEDNETWORKS' and 'valueFrom' in e
            for e in container.get('env', [])):
        return None
    config = ET.fromstring(run(kube(args, '-n', args.namespace, 'exec', pod['metadata']['name'],
                                    '-c', container['name'], '--', 'cat', '/config/config.xml')))
    if (config.findtext('TrustedNetworks') or '').strip():
        return None
    method = env.get(prefix + 'AUTH__METHOD') or config.findtext('AuthenticationMethod')
    if method not in ('Forms', 'Basic'):
        return None
    nodes = get(args, 'get', 'nodes')['items']
    networks = sorted({str(ipaddress.ip_network(n)) for node in nodes for n in node['spec'].get('podCIDRs', [])})
    if not networks or any(ipaddress.ip_network(n).prefixlen == 0 for n in networks):
        return None
    proxies = get(args, '-n', 'kube-system', 'get', 'pods', '-l', 'app.kubernetes.io/name=traefik')['items']
    running = [p for p in proxies if p.get('status', {}).get('phase') == 'Running']
    if not running:
        return None
    for proxy in running:
        proxy_ip = proxy.get('status', {}).get('podIP')
        if proxy['spec'].get('hostNetwork') or not proxy_ip or not any(
                ipaddress.ip_address(proxy_ip) in ipaddress.ip_network(n) for n in networks):
            return None
    plan.update(kind='arr-proxy-trust', networks=networks, description=
                'The healthy backend redirects HTTPS to an HTTP login URL and has no proxy trust policy. '
                'Trust these observed cluster pod CIDRs: ' + ', '.join(networks) + '. '
                'Set ' + prefix + 'AUTH__REQUIRED=Enabled to require login, including local clients. '
                'This restarts the application; PVC definitions stay unchanged.')
    return plan


def apply_plan(plan):
    file = Path(plan['values'])
    text = file.read_text()
    if sha(text) != plan['originalSha256']:
        raise RuntimeError('Values changed after diagnosis; rerun prechecks')
    before = yaml.safe_load(text) or {}
    after = copy.deepcopy(before)
    if plan['kind'] == 'jenkins-login' and plan['app'] == 'jenkins':
        after.setdefault('ingress', {}).setdefault('main', {}).setdefault('annotations', {})[CHECK_PATH] = '/login'
    elif plan['kind'] == 'arr-proxy-trust' and plan['app'] in ARR:
        networks = [str(ipaddress.ip_network(n)) for n in plan['networks']]
        if not networks or any(ipaddress.ip_network(n).prefixlen == 0 for n in networks):
            raise RuntimeError('Invalid trust policy')
        env = after.setdefault('workload', {}).setdefault('main', {}).setdefault('podSpec', {}).setdefault('containers', {}).setdefault('main', {}).setdefault('env', {})
        prefix = plan['app'].upper() + '__'
        if env.get(prefix + 'SERVER__TRUSTEDNETWORKS'):
            raise RuntimeError('Existing values trust policy must be reviewed manually')
        env[prefix + 'SERVER__TRUSTEDNETWORKS'] = ','.join(networks)
        env[prefix + 'AUTH__REQUIRED'] = 'Enabled'
    else:
        raise RuntimeError('Unsupported repair plan')
    backup = Path(plan['planFile']).parent / 'values-before.yaml'
    backup.write_text(text)
    os.chmod(backup, 0o600)
    # Normalize YAML formatting; retain the original text and comments in the backup.
    temporary = file.with_name(file.name + '.http-repair.tmp')
    with temporary.open('x') as handle:
        handle.write(yaml.safe_dump(after, sort_keys=False))
    os.chmod(temporary, file.stat().st_mode & 0o777)
    os.replace(temporary, file)
    (backup.parent / 'applied.json').write_text(json.dumps({
        'appliedAt': datetime.datetime.now().astimezone().isoformat(),
        'values': str(file), 'kind': plan['kind'], 'backup': str(backup)}, indent=2) + '\n')



def verify_plan(args, plan):
    args.namespace = plan['namespace']
    for host in plan['hosts']:
        path = '/login' if plan['kind'] == 'jenkins-login' else '/'
        if probe('https://' + host + path, True)['status'] != 200:
            raise RuntimeError('HTTPS repair verification failed for ' + host)
        if plan['kind'] == 'jenkins-login':
            if probe('https://' + host + '/')['status'] != 403:
                raise RuntimeError('Protected Jenkins root behavior changed')
        else:
            version = 'v1' if plan['app'] == 'prowlarr' else 'v3'
            if probe('https://' + host + '/api/' + version + '/system/status')['status'] not in (401, 403):
                raise RuntimeError('Unauthenticated API request was not rejected')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--app')
    parser.add_argument('--namespace')
    parser.add_argument('--values')
    parser.add_argument('--kubeconfig')
    parser.add_argument('--plan')
    parser.add_argument('--apply')
    parser.add_argument('--verify')
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.verify:
            if not args.kubeconfig:
                parser.error('Verification requires kubeconfig')
            verify_plan(args, json.loads(Path(args.verify).read_text()))
        elif args.apply:
            plan = json.loads(Path(args.apply).read_text())
            plan['planFile'] = args.apply
            apply_plan(plan)
        else:
            if not all([args.app, args.namespace, args.values, args.kubeconfig, args.plan]):
                parser.error('Diagnosis requires app, namespace, values, kubeconfig and plan')
            plan = diagnose(args)
            if plan is None:
                return 1
            Path(args.plan).write_text(json.dumps(plan, indent=2) + '\n')
            print(plan['description'])
        return 0
    except Exception as exc:
        print('HTTP repair stopped: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
