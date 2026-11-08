#!/usr/bin/python3
import copy
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
import yaml
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('values_repair', ROOT / 'lib/helm-values-repair.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class ValuesRepair(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.file = self.root / 'values.yaml'
        self.values = {'securityContext': {'container': {'fsGroup': 568, 'runAsUser': 0, 'runAsGroup': 0}},
                       'persistence': {'config': {'existingClaim': 'retain-existing'}},
                       'workload': {'main': {'replicas': 1, 'strategy': 'Recreate'}}}
        self.write()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self):
        self.file.write_text(yaml.safe_dump(self.values))

    def test_only_fsgroup_moves_and_original_is_backed_up(self):
        plan = m.plan_repair(self.file)
        original = self.file.read_text()
        m.apply_plan(plan, self.root)
        expected = copy.deepcopy(self.values)
        del expected['securityContext']['container']['fsGroup']
        expected['securityContext']['pod'] = {'fsGroup': 568}
        self.assertEqual(yaml.safe_load(self.file.read_text()), expected)
        self.assertEqual((self.root / 'values-before.yaml').read_text(), original)
        self.assertIsNone(m.plan_repair(self.file))

    def test_equal_pod_group_is_preserved(self):
        self.values['securityContext']['pod'] = {'fsGroup': 568, 'fsGroupChangePolicy': 'OnRootMismatch'}
        self.write()
        m.apply_plan(m.plan_repair(self.file), self.root)
        self.assertEqual(yaml.safe_load(self.file.read_text())['securityContext']['pod'], self.values['securityContext']['pod'])

    def test_conflicting_group_is_not_selected(self):
        self.values['securityContext']['pod'] = {'fsGroup': 1000}
        self.write()
        with self.assertRaises(ValueError):
            m.plan_repair(self.file)
        self.assertEqual(yaml.safe_load(self.file.read_text()), self.values)

    def test_invalid_groups_are_not_coerced(self):
        for group in [True, -1, '568']:
            self.values['securityContext']['container']['fsGroup'] = group
            self.write()
            with self.assertRaises(ValueError):
                m.plan_repair(self.file)

    def test_concurrent_values_edit_is_preserved(self):
        plan = m.plan_repair(self.file)
        self.file.write_text('newer: true\n')
        with self.assertRaises(ValueError):
            m.apply_plan(plan, self.root)
        self.assertEqual(self.file.read_text(), 'newer: true\n')

    def test_tampered_plan_is_rejected(self):
        plan = m.plan_repair(self.file)
        plan['fsGroup'] = 1000
        with self.assertRaises(ValueError):
            m.apply_plan(plan, self.root)

    def test_decline_dry_run_and_yes_never_bypass_confirmation(self):
        source = (ROOT / 'helm-upgrade-apps.sh').read_text()
        function = source[source.index('maybe_repair_values_schema() {'):source.index('\napply_fleet_bundle() {')]
        lib = self.root / 'lib'
        lib.mkdir()
        marker = self.root / 'write-marker'
        (lib / 'helm-values-repair.py').write_text('import pathlib,sys\n' +
            'if "--apply" in sys.argv: pathlib.Path(' + repr(str(marker)) + ').write_text("unexpected")\n' +
            'else: print("Move fsGroup 568 to pod scope")\n')
        for dry, expected in [(0, 1), (1, 0)]:
            script = function + '\n' + f'''
script_dir={str(self.root)!r}
log_dir={str(self.root)!r}
log_file={str(self.root / 'log')!r}
yes_mode=1
dry_run={dry}
log() {{ :; }}
dialog() {{ return 1; }}
record_failure_and_maybe_abort() {{ :; }}
maybe_repair_values_schema youtubedl-material {str(self.root)!r}
'''
            result = subprocess.run(['bash', '-c', script], text=True, capture_output=True)
            self.assertEqual(result.returncode, expected, result.stderr)
            self.assertFalse(marker.exists())

    def test_approved_repair_is_not_staged_before_postchecks(self):
        source = (ROOT / 'helm-upgrade-apps.sh').read_text()
        apply = source[source.index('apply_fleet_bundle() {'):source.index('\nverify_fleet_upgrade_version() {')]
        self.assertNotIn('stage_fleet_file', apply)
        post = source[source.index('run_postchecks() {'):source.index('\n}', source.index('run_postchecks() {'))]
        self.assertLess(post.index('check_ingress_http_codes'), post.index('stage_fleet_file'))


if __name__ == '__main__':
    unittest.main()
