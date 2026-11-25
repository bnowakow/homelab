import copy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bookstack_recovery', ROOT/'bookstack-recover-original-db.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def sample():
    spec = {'containers': [{'name': 'bookstack', 'image': 'app-pinned', 'env': [
        {'name': 'DB_HOST', 'valueFrom': {'secretKeyRef': {'name': 'creds', 'key': 'plainhost'}}},
        {'name': 'APP_KEY', 'valueFrom': {'secretKeyRef': {'name': 'preserved', 'key': 'key'}}}]}],
        'initContainers': [{'name': 'bookstack-system-mariadb-wait', 'image': 'client-pinned', 'env': [
            {'name': 'MARIADB_ROOT_PASSWORD', 'valueFrom': {'secretKeyRef': {'name': 'creds', 'key': 'root'}}}]}]}
    live = {'metadata': {'uid': 'uid', 'resourceVersion': '123'}, 'spec': {'template': {'spec': spec}}}
    desired = copy.deepcopy(live)
    wanted = desired['spec']['template']['spec']
    wanted['containers'][0]['env'][0] = {'name': 'DB_HOST', 'value': 'bookstack-mariadb'}
    wanted['initContainers'][0]['env'][0] = {'name': 'MARIADB_ROOT_PASSWORD', 'value': 'test-only'}
    return live, desired


class EnvPatch(unittest.TestCase):
    def test_only_known_entries_are_replaced_and_state_is_guarded(self):
        live, desired = sample()
        saved = copy.deepcopy(live)
        patch = m.env_conflict_patch(live, desired)
        self.assertEqual(live, saved)
        self.assertEqual([p['path'] for p in patch[:2]], ['/metadata/uid', '/metadata/resourceVersion'])
        replacements = [p for p in patch if p['op'] == 'replace']
        self.assertEqual(len(replacements), 2)
        self.assertTrue(all('valueFrom' not in p['value'] for p in replacements))
        self.assertFalse(any('APP_KEY' in str(p) for p in patch))

    def test_changed_image_is_rejected(self):
        live, desired = sample()
        live['spec']['template']['spec']['containers'][0]['image'] = 'unexpected'
        with self.assertRaises(RuntimeError):
            m.env_conflict_patch(live, desired)

    def test_exact_previous_chart_init_image_is_allowed(self):
        live, desired = sample()
        previous = copy.deepcopy(live)
        previous['spec']['template']['spec']['initContainers'][0]['image'] = 'previous-pinned'
        live['spec']['template']['spec']['initContainers'][0]['image'] = 'previous-pinned'
        self.assertEqual(len([p for p in m.env_conflict_patch(live, desired, previous) if p['op']=='replace']), 2)

    def test_observed_chart24_digest_works_without_previous_chart_cache(self):
        live, desired = sample()
        live['spec']['template']['spec']['initContainers'][0]['image'] = m.CHART24_WAIT_IMAGE
        self.assertEqual(len([p for p in m.env_conflict_patch(live, desired) if p['op']=='replace']), 2)

    def test_unknown_image_still_rejected_with_previous_chart(self):
        live, desired = sample()
        previous = copy.deepcopy(live)
        live['spec']['template']['spec']['initContainers'][0]['image'] = 'unknown'
        with self.assertRaises(RuntimeError):
            m.env_conflict_patch(live, desired, previous)

    def test_wrong_host_is_rejected(self):
        live, desired = sample()
        desired['spec']['template']['spec']['containers'][0]['env'][0]['value'] = 'bookstack-mariadb-main'
        with self.assertRaises(RuntimeError):
            m.env_conflict_patch(live, desired)

    def test_no_conflict_refuses_unrelated_changes(self):
        live, desired = sample()
        with self.assertRaises(RuntimeError):
            m.env_conflict_patch(desired, desired)


if __name__ == '__main__':
    unittest.main()
