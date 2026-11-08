#!/usr/bin/python3
import argparse
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('http_repair', ROOT / 'lib/helm-http-repair.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class HttpRepairs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.file = self.root / 'values.yaml'
        self.before = {'persistence': {'config': {'existingClaim': 'existing-pvc'}},
                       'ingress': {'main': {'enabled': True}},
                       'workload': {'main': {'replicas': 1, 'strategy': 'Recreate'}}}
        self.file.write_text(yaml.safe_dump(self.before))
        self.args = argparse.Namespace(app='prowlarr', namespace='apps-prowlarr', values=str(self.file), kubeconfig='unused')
        self.xml = '<Config><AuthenticationMethod>Forms</AuthenticationMethod><TrustedNetworks></TrustedNetworks><ApiKey>do-not-expose</ApiKey></Config>'
        self.host_network = False
        self.backend = 200
        self.redirect = True
        self.jenkins = True
        self.login = 200

    def tearDown(self):
        self.tmp.cleanup()

    def get(self, args, *parts):
        if 'ingress' in parts:
            return {'items': [{'metadata': {'annotations': {'meta.helm.sh/release-name': args.app}},
                    'spec': {'rules': [{'host': 'app.test'}], 'tls': [{'hosts': ['app.test']}]}}]}
        if 'services' in parts:
            return {'items': [{'metadata': {'name': args.app}, 'spec': {'clusterIP': '10.43.0.1', 'ports': [{'name': 'main', 'port': m.ARR[args.app]}]}}]}
        if 'nodes' in parts:
            return {'items': [{'spec': {'podCIDRs': ['10.42.0.0/24']}}]}
        if 'kube-system' in parts:
            return {'items': [{'spec': {'hostNetwork': self.host_network}, 'status': {'phase': 'Running', 'podIP': '10.42.0.8'}}]}
        return {'items': [{'metadata': {'name': 'app-pod', 'labels': {'app.kubernetes.io/instance': args.app}},
                'status': {'phase': 'Running'}, 'spec': {'containers': [{'name': 'main', 'image': 'vendor/' + args.app + ':1'}]}}]}

    def probe(self, url, follow=False):
        if url.startswith('http:'):
            return {'status': self.backend}
        if url.endswith('/login'):
            return {'status': self.login}
        if self.args.app == 'jenkins':
            return {'status': 403, 'jenkins': self.jenkins}
        return {'status': 302 if self.redirect else 404, 'location': 'http://app.test/login'}

    def diagnose(self):
        with patch.object(m, 'get', side_effect=self.get), patch.object(m, 'probe', side_effect=self.probe), patch.object(m, 'run', return_value=self.xml):
            return m.diagnose(self.args)

    def test_confirmed_proxy_diagnosis_and_exact_values_edit(self):
        plan = self.diagnose()
        self.assertEqual(plan['networks'], ['10.42.0.0/24'])
        self.assertNotIn('do-not-expose', json.dumps(plan))
        plan['planFile'] = str(self.root / 'plan.json')
        m.apply_plan(plan)
        actual = yaml.safe_load(self.file.read_text())
        expected = copy.deepcopy(self.before)
        expected['workload']['main']['podSpec'] = {'containers': {'main': {'env': {
            'PROWLARR__SERVER__TRUSTEDNETWORKS': '10.42.0.0/24', 'PROWLARR__AUTH__REQUIRED': 'Enabled'}}}}
        self.assertEqual(actual, expected)
        self.assertEqual(yaml.safe_load((self.root / 'values-before.yaml').read_text()), self.before)

    def test_sonarr_uses_its_own_port_and_environment_prefix(self):
        self.args.app = 'sonarr'
        self.args.namespace = 'apps-sonarr'
        plan = self.diagnose()
        self.assertEqual(plan['kind'], 'arr-proxy-trust')
        plan['planFile'] = str(self.root / 'plan.json')
        m.apply_plan(plan)
        values = yaml.safe_load(self.file.read_text())
        env = values['workload']['main']['podSpec']['containers']['main']['env']
        self.assertEqual(env['SONARR__SERVER__TRUSTEDNETWORKS'], '10.42.0.0/24')
        self.assertEqual(env['SONARR__AUTH__REQUIRED'], 'Enabled')
        self.assertEqual(values['persistence'], self.before['persistence'])

    def test_sonarr_verifies_v3_api_authentication(self):
        plan = {'kind': 'arr-proxy-trust', 'app': 'sonarr',
                'namespace': 'apps-sonarr', 'hosts': ['app.test']}
        calls = []
        def response(url, follow=False):
            calls.append(url)
            return {'status': 401 if '/api/' in url else 200}
        with patch.object(m, 'probe', side_effect=response):
            m.verify_plan(self.args, plan)
        self.assertIn('https://app.test/api/v3/system/status', calls)

    def test_generic_404_is_not_repaired(self):
        self.redirect = False
        self.assertIsNone(self.diagnose())

    def test_unhealthy_backend_is_not_repaired(self):
        self.backend = 503
        self.assertIsNone(self.diagnose())

    def test_existing_trust_policy_is_not_overridden(self):
        self.xml = self.xml.replace('<TrustedNetworks></TrustedNetworks>', '<TrustedNetworks>10.1.0.0/16</TrustedNetworks>')
        self.assertIsNone(self.diagnose())

    def test_host_network_proxy_is_not_repaired(self):
        self.host_network = True
        self.assertIsNone(self.diagnose())

    def test_jenkins_requires_identified_server_and_healthy_login(self):
        self.args.app = 'jenkins'
        self.assertEqual(self.diagnose()['kind'], 'jenkins-login')
        self.jenkins = False
        self.assertIsNone(self.diagnose())
        self.jenkins = True
        self.login = 503
        self.assertIsNone(self.diagnose())

    def test_values_changed_after_prompt_are_not_overwritten(self):
        plan = self.diagnose()
        plan['planFile'] = str(self.root / 'plan.json')
        self.file.write_text('changed: true\n')
        with self.assertRaises(RuntimeError):
            m.apply_plan(plan)
        self.assertEqual(self.file.read_text(), 'changed: true\n')

    def test_authentication_verification_rejects_unprotected_api(self):
        plan = {'kind': 'arr-proxy-trust', 'app': 'prowlarr', 'namespace': 'apps-prowlarr', 'hosts': ['app.test']}
        with patch.object(m, 'probe', return_value={'status': 200}):
            with self.assertRaises(RuntimeError):
                m.verify_plan(self.args, plan)

    def test_dry_run_and_decline_never_apply_even_with_yes_mode(self):
        source = (ROOT / 'helm-upgrade-apps.sh').read_text()
        function = source[source.index('maybe_repair_http_precheck() {'):source.index('\nrun_prechecks() {')]
        (self.root / 'lib').mkdir()
        helper = self.root / 'lib/helm-http-repair.py'
        marker = self.root / 'writes'
        helper.write_text('import pathlib,sys\n' +
                          'if "--apply" in sys.argv: pathlib.Path(' + repr(str(marker)) + ').write_text("unsafe")\n' +
                          'else: print("A diagnosed repair")\n')
        fleet = self.root / 'fleet.yaml'
        fleet.write_text('name: prowlarr\n')
        for dry in [0, 1]:
            script = function + '\n' + f'''
script_dir={str(self.root)!r}
log_dir={str(self.root)!r}
log_file={str(self.root / 'log')!r}
kubeconfig_path=unused
yes_mode=1
dry_run={dry}
log() {{ :; }}
fleet_file_for_app() {{ echo {str(fleet)!r}; }}
fleet_version_from_file() {{ echo 1.0.0; }}
dialog() {{ return 1; }}
apply_fleet_bundle() {{ touch {str(marker)!r}; }}
maybe_repair_http_precheck prowlarr apps-prowlarr
'''
            result = subprocess.run(['bash', '-c', script], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertFalse(marker.exists())


if __name__ == '__main__':
    unittest.main()
