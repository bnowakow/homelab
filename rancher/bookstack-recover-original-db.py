#!/usr/bin/python3
"""One-time, Fleet-native BookStack recovery from revision 211. No data copying."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import yaml

ROOT = Path(__file__).resolve().parent
KC = '/etc/rancher/k3s/k3s.yaml'
NS = 'apps-bookstack'
# Verified from the cached chart 24.0.0 render. sudo may use a different
# Helm cache without that chart, so do not depend on its presence at runtime.
CHART24_WAIT_IMAGE = 'oci.trueforge.org/containerforge/mariadb-client:13.0.2@sha256:c6f01866f536935c8decda4f31038f636a93f70d4338e23c6e91426218ec13c8'

def run(args, timeout=60):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or 'Command failed: ' + args[0])
    return result.stdout

def kubectl(*args):
    return run(['kubectl', '--kubeconfig', KC, '--request-timeout=20s', *args])

def helm(*args):
    return run(['helm', '--kubeconfig', KC, '-n', NS, *args], timeout=120)

def objects(text):
    return [o for o in yaml.safe_load_all(text) if o]

def secret_data(obj, key):
    if key in obj.get('stringData', {}):
        return str(obj['stringData'][key]).encode()
    return base64.b64decode(obj['data'][key])

def main():
    os.umask(0o077)
    if sys.argv[1:] == ['--recreate-app-deployment']:
        repair_env_conflicts(recreate=True)
        return
    if sys.argv[1:] == ['--repair-env-conflicts']:
        repair_env_conflicts()
        return
    if sys.argv[1:] == ['--repair-connection']:
        repair_connection()
        return
    if sys.argv[1:] == ['--verify-only']:
        verify_recovery()
        return
    if sys.argv[1:]:
        raise RuntimeError('Usage: bookstack-recover-original-db.py [--verify-only|--repair-connection|--repair-env-conflicts]')
    recover_original()


def recover_original():
    fleet = ROOT/'fleet/apps/bookstack/fleet.yaml'
    values = fleet.with_name('values.yaml')
    if yaml.safe_load(fleet.read_text())['helm']['version'] != '23.2.3':
        raise RuntimeError('Recovery Fleet version must be 23.2.3')
    status = json.loads(helm('status', 'bookstack', '-o', 'json'))
    state = (status['version'], status['info']['status'])
    if state not in ((212, 'deployed'), (214, 'failed'), (217, 'failed')):

        raise RuntimeError('Expected deployed revision 212 or failed revision 214/217; review live release before retrying')
    source_revision = 213 if state[1] == 'failed' else 211
    history = json.loads(helm('history', 'bookstack', '--max', '10', '-o', 'json'))
    if not any(int(r['revision']) == source_revision and r['chart'] == 'bookstack-23.2.3' for r in history):
        raise RuntimeError('Expected original chart revision missing from history')
    if state[1] == 'failed' and not any(int(r['revision']) == state[0] and r['chart'] == 'bookstack-24.0.0' for r in history):
        raise RuntimeError('Failed release is not the expected chart 24 upgrade')
    backup = Path(tempfile.mkdtemp(prefix='bookstack-recovery-', dir=ROOT/'logs'))
    print('Private recovery evidence:', backup, flush=True)
    old_text = helm('get', 'manifest', 'bookstack', '--revision', str(source_revision))
    (backup/f'revision-{source_revision}.yaml').write_text(old_text)
    (backup/f'revision-{status["version"]}.yaml').write_text(helm('get', 'manifest', 'bookstack', '--revision', str(status['version'])))
    old = objects(old_text)
    old_db = next(o for o in old if o['kind']=='Deployment' and o['metadata']['name']=='bookstack-mariadb')
    # Use a cached exact chart when available; otherwise Helm may fetch it.
    cache = Path(run(['helm', 'env', 'HELM_REPOSITORY_CACHE']).strip().strip('"'))
    chart = cache/'bookstack-23.2.3.tgz'
    chart_ref = str(chart) if chart.exists() else 'oci://oci.trueforge.org/truecharts/bookstack'
    rendered = objects(run(['helm', 'template', 'bookstack', chart_ref, '--version', '23.2.3', '-n', NS,
                            '-f', str(values), '--kube-version', '1.34.0', '--skip-schema-validation'], timeout=180))
    new_db = next(o for o in rendered if o['kind']=='Deployment' and o['metadata']['name']=='bookstack-mariadb')
    if new_db['spec']['template']['spec']['containers'][0]['image'] != old_db['spec']['template']['spec']['containers'][0]['image']:
        raise RuntimeError(f'Recovery DB image differs from revision {source_revision}')
    for key in ('mariadb-password', 'mariadb-root-password'):
        original = next(o for o in old if o['kind']=='Secret' and o['metadata']['name']=='bookstack-mariadbcreds')
        proposed = next(o for o in rendered if o['kind']=='Secret' and o['metadata']['name']=='bookstack-mariadbcreds')
        if secret_data(original, key) != secret_data(proposed, key):
            raise RuntimeError(f'Recovery credentials differ from revision {source_revision}; refusing apply')
    kubectl('-n', NS, 'get', 'secret', 'bookstack-preserved-app-key', '-o', 'name')
    for claim, volume in [('bookstack-mariadb-data-longhorn-rwo', 'pvc-ad162742-8668-4b9f-8c32-88f80cb56801'),
                          ('bookstack-mariadb-data', 'pvc-d0b3aaa0-725a-477c-947c-131b5637090d')]:
        pvc = json.loads(kubectl('-n', NS, 'get', 'pvc', claim, '-o', 'json'))
        if pvc['status']['phase'] != 'Bound' or pvc['spec']['volumeName'] != volume:
            raise RuntimeError('Unexpected PVC binding: ' + claim)
        (backup/(claim+'.json')).write_text(json.dumps(pvc))
    bundle = json.loads(kubectl('-n', 'fleet-local', 'get', 'bundle', 'bookstack', '-o', 'json'))
    (backup/'bundle-before.json').write_text(json.dumps(bundle))
    scope = json.loads(kubectl('-n', 'fleet-local', 'get', 'bundles', '-l', 'fleet.cattle.io/repo-name=bookstack', '-o', 'json'))
    if any(o['metadata']['name'] != 'bookstack' for o in scope['items']):
        raise RuntimeError('Native Fleet apply scope includes unrelated bundles')
    labels = [item for k,v in bundle['metadata'].get('labels', {}).items() if k != 'fleet.cattle.io/commit' for item in ['--label', k+'='+v]]
    print('Verified original image, credentials and both PVC bindings. Applying only BookStack through Fleet.', flush=True)
    os.chdir(ROOT.parent)
    output = run(['fleet', 'apply', '--kubeconfig', KC, '-n', 'fleet-local', *labels,
                  'bookstack', 'rancher/fleet/apps/bookstack'], timeout=300)
    (backup/'fleet-apply.txt').write_text(output)
    verify_recovery()


def recovered_release(status, history):
    revision = int(status.get('version', 0))
    return revision > 212 and status.get('info', {}).get('status') == 'deployed' and any(
        int(row.get('revision', 0)) == revision and row.get('status') == 'deployed'
        and row.get('chart') == 'bookstack-23.2.3' for row in history)


def repair_connection():
    status = json.loads(helm('status', 'bookstack', '-o', 'json'))
    history = json.loads(helm('history', 'bookstack', '--max', '5', '-o', 'json'))
    if (status.get('version'), status.get('info', {}).get('status')) in ((214, 'failed'), (217, 'failed')):
        print('Failed chart 24 revision detected; running full original-backend recovery with explicit DB_HOST.', flush=True)
        recover_original()
        return
    if not recovered_release(status, history):
        raise RuntimeError('Connection repair requires recovered chart 23.2.3 or known failed chart 24 revision 214/217')
    directory = ROOT/'fleet/apps/bookstack'
    config = yaml.safe_load((directory/'fleet.yaml').read_text())
    values = yaml.safe_load((directory/'values.yaml').read_text())
    if config['helm']['version'] != '23.2.3' or values['workload']['main']['podSpec']['containers']['main']['env']['DB_HOST'] != 'bookstack-mariadb':
        raise RuntimeError('Expected chart 23.2.3 with explicit recovered DB_HOST')
    database = json.loads(kubectl('-n', NS, 'get', 'deployment', 'bookstack-mariadb', '-o', 'json'))
    if not any(v.get('persistentVolumeClaim', {}).get('claimName') == 'bookstack-mariadb-data-longhorn-rwo'
               for v in database['spec']['template']['spec'].get('volumes', [])):
        raise RuntimeError('Recovered database Deployment does not mount the original claim')
    bundle = json.loads(kubectl('-n', 'fleet-local', 'get', 'bundle', 'bookstack', '-o', 'json'))
    scope = json.loads(kubectl('-n', 'fleet-local', 'get', 'bundles', '-l', 'fleet.cattle.io/repo-name=bookstack', '-o', 'json'))
    if any(o['metadata']['name'] != 'bookstack' for o in scope['items']):
        raise RuntimeError('Unsafe Fleet apply scope')
    backup = Path(tempfile.mkdtemp(prefix='bookstack-connection-', dir=ROOT/'logs'))
    (backup/'bundle-before.json').write_text(json.dumps(bundle))
    (backup/'deployment-before.json').write_text(kubectl('-n', NS, 'get', 'deployment', 'bookstack', '-o', 'json'))
    labels = [item for k,v in bundle['metadata'].get('labels', {}).items() if k != 'fleet.cattle.io/commit' for item in ['--label', k+'='+v]]
    print('Applying explicit original DB_HOST through Fleet; private evidence:', backup, flush=True)
    os.chdir(ROOT.parent)
    output = run(['fleet', 'apply', '--kubeconfig', KC, '-n', 'fleet-local', *labels,
                  'bookstack', 'rancher/fleet/apps/bookstack'], timeout=300)
    (backup/'fleet-apply.txt').write_text(output)
    # A template change may not have reached the Deployment when Fleet apply returns.
    deadline = time.monotonic()+600
    while time.monotonic() < deadline:
        deployment = json.loads(kubectl('-n', NS, 'get', 'deployment', 'bookstack', '-o', 'json'))
        containers = deployment['spec']['template']['spec']['containers']
        if any(c['name']=='bookstack' and any(e.get('name')=='DB_HOST' and e.get('value')=='bookstack-mariadb'
               for e in c.get('env', [])) for c in containers):
            break
        time.sleep(5)
    else:
        raise RuntimeError('Fleet did not apply explicit DB_HOST before the deadline')
    verify_recovery()


def env_conflict_patch(live, desired, previous=None):
    """Replace only known env source transitions, guarded against stale state."""
    changes = []
    allowed = {('containers', 'bookstack', 'DB_HOST'),
               ('initContainers', 'bookstack-system-mariadb-wait', 'MARIADB_ROOT_PASSWORD')}
    actual_spec = live['spec']['template']['spec']
    wanted_spec = desired['spec']['template']['spec']
    previous_spec = previous['spec']['template']['spec'] if previous else {}
    for group in ('containers', 'initContainers'):
        wanted = {c['name']: c for c in wanted_spec.get(group, [])}
        for ci, container in enumerate(actual_spec.get(group, [])):
            target = wanted.get(container['name'])
            if not target:
                continue
            known_images = {target['image']}
            known_images.update(c['image'] for c in previous_spec.get(group, []) if c['name'] == container['name'])
            if (group, container['name']) == ('initContainers', 'bookstack-system-mariadb-wait'):
                known_images.add(CHART24_WAIT_IMAGE)
            if container['image'] not in known_images:
                raise RuntimeError(f'Unexpected image for {group}/{container["name"]}: {container["image"]}; refusing env repair')
            env = {e['name']: e for e in target.get('env', [])}
            for ei, entry in enumerate(container.get('env', [])):
                proposed = env.get(entry['name'])
                if not proposed or (group, container['name'], entry['name']) not in allowed:
                    continue
                # Switching a Secret reference to a literal needs removal of
                # the old source. Replacing this one list item does that atomically.
                if 'valueFrom' not in entry or 'value' not in proposed or 'valueFrom' in proposed:
                    continue
                if entry['name'] == 'DB_HOST' and proposed['value'] != 'bookstack-mariadb':
                    raise RuntimeError('Unexpected recovered database host')
                path = f'/spec/template/spec/{group}/{ci}/env/{ei}'
                changes.extend([{'op':'test', 'path':path, 'value':entry},
                                {'op':'replace', 'path':path, 'value':proposed}])
    if not changes:
        raise RuntimeError('No supported env source transitions found; refusing unrelated edits')
    return [{'op':'test', 'path':'/metadata/uid', 'value':live['metadata']['uid']},
            {'op':'test', 'path':'/metadata/resourceVersion', 'value':live['metadata']['resourceVersion']}] + changes


def repair_env_conflicts(recreate=False):
    directory = ROOT/'fleet/apps/bookstack'
    if yaml.safe_load((directory/'fleet.yaml').read_text())['helm']['version'] != '23.2.3':
        raise RuntimeError('Fleet must remain pinned to recovered chart 23.2.3')
    status = json.loads(helm('status', 'bookstack', '-o', 'json'))
    history = json.loads(helm('history', 'bookstack', '--max', '5', '-o', 'json'))
    if status['info']['status'] != 'failed' or not any(int(r['revision']) == int(status['version']) and
            r['chart']=='bookstack-23.2.3' for r in history) or 'valueFrom' not in status['info'].get('description', ''):
        raise RuntimeError('Expected failed chart 23.2.3 with the known env valueFrom validation error')
    bundle = json.loads(kubectl('-n', 'fleet-local', 'get', 'bundle', 'bookstack', '-o', 'json'))
    if bundle.get('spec', {}).get('helm', {}).get('version') != '23.2.3':
        raise RuntimeError('Live Fleet bundle is not targeting chart 23.2.3; stop other upgrades first')
    cache = Path(run(['helm', 'env', 'HELM_REPOSITORY_CACHE']).strip().strip('"'))
    chart = cache/'bookstack-23.2.3.tgz'
    if not chart.exists():
        raise RuntimeError('Exact cached chart 23.2.3 is required for this repair')
    rendered = objects(run(['helm', 'template', 'bookstack', str(chart), '-n', NS,
        '-f', str(directory/'values.yaml'), '--kube-version', '1.34.0', '--skip-schema-validation']))
    desired = next(o for o in rendered if o['kind']=='Deployment' and o['metadata']['name']=='bookstack')
    # A failed chart 24 upgrade can leave its init image on the live Deployment.
    # Accept only the exact image in the cached chart, never arbitrary tags.
    prior_chart = cache/'bookstack-24.0.0.tgz'
    previous = None
    if prior_chart.exists():
        prior_render = objects(run(['helm', 'template', 'bookstack', str(prior_chart), '-n', NS,
            '-f', str(directory/'values.yaml'), '--kube-version', '1.34.0', '--skip-schema-validation']))
        previous = next(o for o in prior_render if o['kind']=='Deployment' and o['metadata']['name']=='bookstack')
    original = objects(helm('get', 'manifest', 'bookstack', '--revision', '213'))
    for key in ('mariadb-password', 'mariadb-root-password'):
        before = next(o for o in original if o['kind']=='Secret' and o['metadata']['name']=='bookstack-mariadbcreds')
        after = next(o for o in rendered if o['kind']=='Secret' and o['metadata']['name']=='bookstack-mariadbcreds')
        if secret_data(before, key) != secret_data(after, key):
            raise RuntimeError('Recovery credentials differ from successful revision 213')
    live = json.loads(kubectl('-n', NS, 'get', 'deployment', 'bookstack', '-o', 'json'))
    patch = None if recreate else env_conflict_patch(live, desired, previous)
    backup = Path(tempfile.mkdtemp(prefix='bookstack-env-repair-', dir=ROOT/'logs'))
    (backup/'deployment-before.json').write_text(json.dumps(live))
    (backup/'bundle-before.json').write_text(json.dumps(bundle))
    if recreate:
        # A failed Helm revision can retain a conflicting patch baseline even
        # after the live env is fixed. Recreate only the frontend Deployment;
        # Fleet supplies the desired valid template without a three-way merge.
        if desired['spec']['replicas'] != 1:
            raise RuntimeError('Expected one application replica')
        for obj in (live, desired):
            claims = {v['persistentVolumeClaim']['claimName'] for v in
                      obj['spec']['template']['spec'].get('volumes', []) if 'persistentVolumeClaim' in v}
            if claims != {'bookstack-config-longhorn-rwo'}:
                raise RuntimeError('Unexpected application storage; refusing recreation')
        current = json.loads(kubectl('-n', NS, 'get', 'deployment', 'bookstack', '-o', 'json'))
        if current['metadata']['uid'] != live['metadata']['uid'] or current['spec'] != live['spec']:
            raise RuntimeError('Application changed during diagnosis; refusing recreation')
        print('Recreating only application Deployment bookstack through Fleet; brief web outage. Evidence:', backup, flush=True)
        kubectl('-n', NS, 'delete', 'deployment', 'bookstack', '--cascade=foreground', '--wait=true', '--timeout=45s')
    else:
        patch_file = backup/'env-patch.json'
        patch_file.write_text(json.dumps(patch))
        print('Replacing only known environment-source conflicts; private evidence:', backup, flush=True)
        kubectl('-n', NS, 'patch', 'deployment', 'bookstack', '--type=json', '--patch-file', str(patch_file))
    # Request a retry without altering the chart version or any storage fields.
    bundle = json.loads(kubectl('-n', 'fleet-local', 'get', 'bundle', 'bookstack', '-o', 'json'))
    if bundle.get('spec', {}).get('helm', {}).get('version') != '23.2.3':
        raise RuntimeError('Fleet target changed during repair; stop the other upgrader and restore chart 23.2.3')
    sync_patch = [{'op':'test', 'path':'/metadata/resourceVersion', 'value':bundle['metadata']['resourceVersion']},
                  {'op':'add', 'path':'/spec/forceSyncGeneration',
                   'value':int(bundle.get('spec', {}).get('forceSyncGeneration', 0))+1}]
    sync_file = backup/'sync-patch.json'
    sync_file.write_text(json.dumps(sync_patch))
    kubectl('-n', 'fleet-local', 'patch', 'bundle', 'bookstack', '--type=json', '--patch-file', str(sync_file))
    verify_recovery()


def verify_recovery():
    # Wait for the applied content and actual Helm chart, not just old Ready status.
    deadline = time.monotonic()+600
    while time.monotonic() < deadline:
        release = json.loads(helm('status', 'bookstack', '-o', 'json'))
        history = json.loads(helm('history', 'bookstack', '--max', '5', '-o', 'json'))
        deployments = json.loads(kubectl('get', 'bundledeployments', '-A', '-l', 'fleet.cattle.io/bundle-name=bookstack', '-o', 'json'))['items']
        good = bool(deployments) and all(d.get('status', {}).get('ready') is True and
                    d.get('status', {}).get('appliedDeploymentID') == d.get('spec', {}).get('deploymentID') for d in deployments)
        if good and recovered_release(release, history):
            break
        time.sleep(5)
    else:
        raise RuntimeError('Recovery reconciliation timed out; inspect Fleet/Helm before further action')
    for workload in ('bookstack-mariadb', 'bookstack'):
        print(run(['kubectl', '--kubeconfig', KC, '-n', NS, 'rollout', 'status', 'deployment/'+workload, '--timeout=600s'], timeout=620), flush=True)
    host = kubectl('-n', NS, 'exec', 'deployment/bookstack', '-c', 'bookstack', '--', 'printenv', 'DB_HOST').strip()
    if host != 'bookstack-mariadb':
        raise RuntimeError('Application DB_HOST is '+host+'; expected bookstack-mariadb. Run --repair-connection.')
    pods = json.loads(kubectl('-n', NS, 'get', 'pods', '-o', 'json'))['items']
    if not any(p['metadata']['name'].startswith('bookstack-mariadb-') and any(v.get('persistentVolumeClaim', {}).get('claimName')=='bookstack-mariadb-data-longhorn-rwo' for v in p['spec'].get('volumes', [])) for p in pods):
        raise RuntimeError('Original DB claim not found on a running pod')
    kubectl('-n', NS, 'get', 'pvc', 'bookstack-mariadb-data', '-o', 'name')
    for deployment, path in [('bookstack', '/config'), ('bookstack-mariadb', '/bitnami/mariadb')]:
        print(kubectl('-n', NS, 'exec', 'deployment/'+deployment, '--', 'df', '-h', path))
    code = run(['curl', '--fail', '--silent', '--show-error', '--location', '--max-time', '30',
                '--output', '/dev/null', '--write-out', '%{http_code}',
                'https://bookstack.rancher.tailscale.bnowakowski.pl/login'])
    if code.strip() != '200':
        raise RuntimeError('Recovered login endpoint did not return HTTP 200')
    print('Recovery rollout and original mount verified; replacement PVC retained. Test your original login and books in the UI.')

if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('STOPPED: '+str(error), file=sys.stderr)
        sys.exit(1)
