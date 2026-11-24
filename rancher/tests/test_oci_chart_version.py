import importlib.util
import io
import json
from pathlib import Path
import unittest
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('oci_chart_version', ROOT / 'zabbix/lib/oci-chart-version.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class Response(io.BytesIO):
    def __init__(self, payload, headers=None):
        super().__init__(json.dumps(payload).encode())
        self.headers = headers or {}


class RegistryVersions(unittest.TestCase):
    def test_latest_stable_is_numeric_and_ignores_prereleases(self):
        def opener(req, timeout):
            self.assertEqual(req.full_url, 'https://registry.test/v2/charts/sonarr/tags/list?n=1000')
            self.assertLessEqual(timeout, 5)
            return Response({'tags': ['1.9.0', '1.10.0', '2.0.0-rc1', 'latest']})
        self.assertEqual(m.latest_version('oci://registry.test/charts/sonarr', opener), '1.10.0')

    def test_follows_pagination(self):
        calls = []
        def opener(req, timeout):
            calls.append(req.full_url)
            if len(calls) == 1:
                return Response({'tags': ['1.0.0']}, {'Link': '</v2/charts/sonarr/tags/list?last=1.0.0>; rel="next"'})
            return Response({'tags': ['2.0.0']})
        self.assertEqual(m.latest_version('oci://registry.test/charts/sonarr', opener), '2.0.0')
        self.assertEqual(len(calls), 2)

    def test_anonymous_bearer_auth(self):
        calls = []
        def opener(req, timeout):
            calls.append(req)
            if len(calls) == 1:
                raise HTTPError(req.full_url, 401, 'Unauthorized', {'WWW-Authenticate': 'Bearer realm="https://auth.test/token",service="registry.test",scope="repository:charts/sonarr:pull"'}, None)
            if len(calls) == 2:
                self.assertIsNone(req.get_header('Authorization'))
                return Response({'token': 'test-token'})
            self.assertEqual(req.get_header('Authorization'), 'Bearer test-token')
            return Response({'tags': ['28.1.8']})
        self.assertEqual(m.latest_version('oci://registry.test/charts/sonarr', opener), '28.1.8')

    def test_temporary_failure_recovers(self):
        calls = []
        def opener(req, timeout):
            calls.append(req)
            if len(calls) == 1:
                raise HTTPError(req.full_url, 503, 'Unavailable', {}, None)
            return Response({'tags': ['2.0.0']})
        self.assertEqual(m.latest_version('oci://registry.test/charts/sonarr', opener), '2.0.0')
        self.assertEqual(len(calls), 2)

    def test_registry_failure_never_becomes_a_version(self):
        calls = []
        def opener(req, timeout):
            calls.append(req)
            raise HTTPError(req.full_url, 503, 'Unavailable', {}, None)
        with self.assertRaises(HTTPError):
            m.latest_version('oci://registry.test/charts/sonarr', opener)
        self.assertEqual(len(calls), 3)

    def test_no_stable_versions_is_error(self):
        with self.assertRaisesRegex(ValueError, 'no stable'):
            m.latest_version('oci://registry.test/charts/sonarr', lambda *a, **k: Response({'tags': ['latest', '1.0.0-rc1']}))

    def test_cross_origin_pagination_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'outside'):
            m.latest_version('oci://registry.test/charts/sonarr', lambda *a, **k: Response({'tags': ['1.0.0']}, {'Link': '<https://other.test/tags>; rel="next"'}))


if __name__ == '__main__':
    unittest.main()
