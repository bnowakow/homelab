import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class VersionChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root/'zabbix/lib').mkdir(parents=True)
        (self.root/'bin').mkdir()
        for f in ['zabbix/is-helm-image-up-to-date.sh', 'zabbix/lib/helm-current-version-of-chart.sh']:
            shutil.copy2(ROOT/f, self.root/f)
        self.script('helm-repositories.sh', 'helm_chart_ref_for_app() { echo oci://example.test/duckdns; }')
        self.script('zabbix/lib/helm-chart-repo-dir.sh', 'exit 1')
        self.script('bin/sleep', 'exit 0')
        self.env = dict(os.environ, PATH=str(self.root/'bin')+':'+os.environ['PATH'], COUNT=str(self.root/'count'))

    def script(self, name, code):
        p = self.root/name
        p.write_text('#!/bin/bash\n'+code+'\n')
        p.chmod(0o755)

    def run_check(self):
        return subprocess.run([str(self.root/'zabbix/is-helm-image-up-to-date.sh'), 'duckdns'], env=self.env, text=True, capture_output=True)

    def test_transient_registry_failure_recovers(self):
        self.script('bin/helm', '''if [ "$1" = show ]; then
n=$(cat "$COUNT" 2>/dev/null || echo 0); n=$((n+1)); echo "$n" > "$COUNT"
if [ "$n" -eq 1 ]; then echo '503 Service Unavailable' >&2; exit 1; fi
echo 'version: 2.0.0'
else echo '[{"name":"duckdns","chart":"duckdns-1.0.0"}]'; fi''')
        r=self.run_check()
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertEqual(r.stdout.strip(), 'false,1.0.0,2.0.0')
        self.assertIn('503 Service Unavailable', r.stderr)
        self.assertEqual((self.root/'count').read_text().strip(), '2')

    def test_registry_tag_lookup_avoids_helm_chart_download(self):
        shutil.copy2(ROOT/'zabbix/lib/oci-chart-version.py', self.root/'zabbix/lib/oci-chart-version.py')
        self.script('bin/python3', 'echo 2.0.0')
        self.script('bin/helm', '''if [ "$1" = show ]; then
echo 'chart downloads must not be used' >&2; exit 1
fi
echo '[{"name":"duckdns","chart":"duckdns-2.0.0"}]' ''')
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'true')
        self.assertEqual(result.stderr, '')

    def test_permanent_registry_error_is_reported(self):
        self.script('bin/helm', 'echo "unauthorized: access denied" >&2; exit 1')
        r=self.run_check()
        self.assertEqual(r.returncode, 3)
        self.assertEqual(r.stdout.strip(), 'false,error')
        self.assertIn('unauthorized: access denied', r.stderr)
        self.assertNotIn('attempt 2', r.stderr)

    def test_cluster_failure_is_not_not_running(self):
        self.script('bin/helm', '''if [ "$1" = show ]; then echo 'version: 2.0.0'; else echo 'connection refused' >&2; exit 1; fi''')
        r=self.run_check()
        self.assertEqual(r.returncode, 3)
        self.assertEqual(r.stdout.strip(), 'false,error')
        self.assertIn('could not read installed Helm releases', r.stderr)

    def test_transient_failure_stops_after_three_attempts(self):
        self.script('bin/helm', 'echo "503 Service Unavailable" >&2; exit 1')
        r=self.run_check()
        self.assertEqual(r.returncode, 3)
        self.assertIn('attempt 3/3', r.stderr)
        self.assertNotIn('attempt 4', r.stderr)

if __name__ == '__main__':
    unittest.main()
