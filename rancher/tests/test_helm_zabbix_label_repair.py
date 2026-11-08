#!/usr/bin/python3
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('zabbix_labels', ROOT / 'lib/helm-zabbix-label-repair.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
ERROR = '''Upgrade "zabbix" failed: conflict occurred while applying object apps-zabbix/zabbix-postgresql apps/v1, Kind=StatefulSet: Apply failed with 2 conflicts: conflicts with "kubectl-patch" using apps/v1:
- .metadata.labels.helm.sh/chart
- .spec.template.metadata.labels.helm.sh/chart'''


def sample():
    claims = {'f:metadata': {'f:labels': {'f:helm.sh/chart': {}, 'f:app.kubernetes.io/version': {}}},
              'f:spec': {'f:template': {'f:metadata': {'f:labels': {'f:helm.sh/chart': {}, 'f:app.kubernetes.io/version': {}}}}}}
    return {'metadata': {'name': 'zabbix-postgresql', 'uid': 'original-uid', 'resourceVersion': '100',
            'labels': {'helm.sh/chart': 'zabbix-8.0.0'}, 'managedFields': [
                {'manager': 'kubectl-patch', 'apiVersion': 'apps/v1', 'fieldsV1': claims},
                {'manager': 'another-controller', 'apiVersion': 'apps/v1', 'fieldsV1': {'f:spec': {'f:replicas': {}}}}]},
            'spec': {'serviceName': 'zabbix-postgresql-headless', 'template': {'metadata': {'labels': {'helm.sh/chart': 'zabbix-8.0.0'}}, 'spec': {'volumes': [{'persistentVolumeClaim': {'claimName': 'existing-db'}}]}}}}


class ZabbixLabels(unittest.TestCase):
    def test_exact_known_failure(self):
        self.assertTrue(m.supported_error(ERROR))
        self.assertFalse(m.supported_error(ERROR + '\n- .spec.selector'))
        self.assertFalse(m.supported_error(ERROR.replace('kubectl-patch', 'other-controller')))
        self.assertFalse(m.supported_error(ERROR.replace('apps-zabbix/zabbix-postgresql', 'other/db')))

    def test_only_two_claims_removed(self):
        before = sample()
        original = copy.deepcopy(before)
        edits = m.ownership_patch(before)
        self.assertEqual(before, original)
        after = edits[-1]['value']
        self.assertEqual(after[1], before['metadata']['managedFields'][1])
        fields = after[0]['fieldsV1']
        self.assertEqual(fields['f:metadata']['f:labels'], {'f:app.kubernetes.io/version': {}})
        self.assertEqual(fields['f:spec']['f:template']['f:metadata']['f:labels'], {'f:app.kubernetes.io/version': {}})
        self.assertEqual([o['path'] for o in edits[:-1]], ['/metadata/uid', '/metadata/resourceVersion', '/metadata/managedFields'])

    def test_missing_claim_refuses_broad_reset(self):
        obj = sample()
        del obj['metadata']['managedFields'][0]['fieldsV1']['f:metadata']
        with self.assertRaises(ValueError):
            m.ownership_patch(obj)

    def test_unrelated_live_value_change_is_rejected(self):
        before = sample()
        for field in ['spec', 'uid', 'label']:
            after = copy.deepcopy(before)
            if field == 'spec':
                after['spec']['serviceName'] = 'changed'
            elif field == 'uid':
                after['metadata']['uid'] = 'new-uid'
            else:
                after['metadata']['labels']['helm.sh/chart'] = 'changed'
            with self.assertRaises(ValueError):
                m.unchanged(before, after)

    def test_apply_uses_dry_run_before_real_patch(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            before = sample()
            snapshot = root / 'before.json'
            snapshot.write_text(json.dumps(before))
            plan = root / 'plan.json'
            edits = m.ownership_patch(before)
            plan.write_text(json.dumps({'before': str(snapshot), 'beforeSha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(), 'patch': edits}))
            after = copy.deepcopy(before)
            after['metadata']['resourceVersion'] = '101'
            after['metadata']['managedFields'] = edits[-1]['value']
            with patch.object(m, 'run', return_value=json.dumps(after)) as run:
                m.apply(argparse.Namespace(apply=str(plan), kubeconfig='test'))
            self.assertEqual(run.call_count, 2)
            self.assertIn('--dry-run=server', run.call_args_list[0].args[0])
            self.assertNotIn('--dry-run=server', run.call_args_list[1].args[0])

    def test_decline_and_dry_run_do_not_apply_under_yes(self):
        source = (ROOT / 'helm-upgrade-apps.sh').read_text()
        function = source[source.index('repair_zabbix_chart_labels() {'):source.index('\nrepair_cloudcasa_field_ownership() {')]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'lib').mkdir()
            marker = root / 'mutation'
            (root / 'lib/helm-zabbix-label-repair.py').write_text('import pathlib,sys\nif "--apply" in sys.argv: pathlib.Path(' + repr(str(marker)) + ').write_text("unexpected")\nelse: print("A verified metadata-only repair")\n')
            for dry in [0, 1]:
                script = function + '\n' + f'''
script_dir={str(root)!r}
log_dir={str(root)!r}
log_file={str(root / 'log')!r}
kubeconfig_path=unused
dry_run={dry}
yes_mode=1
log() {{ :; }}
dialog() {{ return 1; }}
repair_zabbix_chart_labels zabbix apps-zabbix 8.0.1 error
'''
                result = subprocess.run(['bash', '-c', script], capture_output=True, text=True)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertFalse(marker.exists())


if __name__ == '__main__':
    unittest.main()
