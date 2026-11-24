import os
from pathlib import Path
import pty
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / 'helm-upgrade-apps.sh').read_text()
VERSION_FUNCTION = SCRIPT[SCRIPT.index('fleet_version_from_file() {'):SCRIPT.index('\nupdate_fleet_version() {')]
FUNCTION = SCRIPT[SCRIPT.index('maybe_repair_adguard_rollout() {'):SCRIPT.index('\nrun_prechecks() {')]


class AdguardRecovery(unittest.TestCase):
    def run_repair(self, approve=0, ready=0, changed=0, replicas=1, dry=0, app='adguard-home', tty=True, schema=False):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, TEST_DIR=tmp, APPROVE=str(approve), READY=str(ready), CHANGED=str(changed), REPLICAS=str(replicas), DRY=str(dry), APP=app, SCHEMA='1' if schema else '0')
            harness = '''
script_dir="$TEST_DIR"; log_dir="$TEST_DIR"; log_file="$TEST_DIR/log"
kubeconfig_path=unused; dry_run="$DRY"; yes_mode=1
log() { echo "$*" >> "$log_file"; }
timeout() { shift; "$@"; }
dialog() { echo "dialog $*" >> "$TEST_DIR/calls"; return "$APPROVE"; }
fleet_file_for_app() { echo "$TEST_DIR/fleet.yaml"; }
apply_fleet_bundle() { echo fleet-repair >> "$TEST_DIR/calls"; }
touch "$TEST_DIR/values.yaml"
check_rollout_ready() { echo rollout-check >> "$TEST_DIR/calls"; return "$READY"; }
record_failure_and_maybe_abort() { echo failure >> "$TEST_DIR/calls"; }
kubectl() {
 echo "kubectl $*" >> "$TEST_DIR/calls"
 case "$*" in
 *'get deployment'*)
   local gen=1
   if [ -f "$TEST_DIR/read" ]; then gen=$((1 + CHANGED)); fi
   touch "$TEST_DIR/read"
   echo "{\\"metadata\\":{\\"uid\\":\\"original\\",\\"generation\\":$gen},\\"spec\\":{\\"replicas\\":$REPLICAS}}" ;;
 *'get pods'*) echo 'adguard-pod 0/1 CrashLoopBackOff' ;;
 esac
}
'''
            (Path(tmp)/'fleet.yaml').write_text((ROOT/'fleet/apps/adguard-home/fleet.yaml').read_text())
            harness += VERSION_FUNCTION + '\n' + FUNCTION + '\nmaybe_repair_adguard_rollout "$APP" apps-adguard-home\n'
            if schema:
                harness = harness.replace('\\"replicas\\":$REPLICAS', '\\"template\\":{\\"spec\\":{\\"containers\\":[{\\"name\\":\\"adguard-home\\",\\"image\\":\\"docker.io/adguard/adguardhome:v0.107.80@sha256:test\\"}]}},\\"replicas\\":$REPLICAS')
                harness = harness.replace("*'get pods'*)", "*'logs deployment'*) echo 'unknown current schema version 35' ;;\n *'get pods'*)")
            if tty:
                master, slave = pty.openpty()
                try:
                    result = subprocess.run(['bash', '-c', harness], stdin=slave, stdout=slave, stderr=subprocess.PIPE, env=env, timeout=10)
                finally:
                    os.close(slave)
                    os.close(master)
            else:
                result = subprocess.run(['bash', '-c', harness], capture_output=True, env=env, timeout=10)
            calls = (Path(tmp)/'calls').read_text() if (Path(tmp)/'calls').exists() else ''
            return result.returncode, calls, result.stderr

    def test_fleet_version_parser_reads_only_direct_helm_version(self):
        samples = [
            ('name: app\nhelm:\n  version: 16.0.0\n', '16.0.0'),
            ('version: wrong\nhelm:\n  values:\n    version: wrong\n  version: "16.0.0" # pinned\n', '16.0.0'),
            ("helm:\n  version: '16.0.0'\n", '16.0.0'),
            ('helm:\n  chart: example\nother:\n  version: wrong\n', ''),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'fleet.yaml'
            for source, expected in samples:
                with self.subTest(source=source):
                    path.write_text(source)
                    result = subprocess.run(['bash', '-c', VERSION_FUNCTION + '\nfleet_version_from_file "$1"', 'test', str(path)], capture_output=True, text=True)
                    self.assertEqual(result.stdout.strip(), expected)
                    self.assertEqual(result.returncode, 0 if expected else 1)

    def test_schema_mismatch_uses_fleet_instead_of_restart(self):
        rc, calls, err = self.run_repair(schema=True)
        self.assertEqual(rc, 0, err)
        self.assertIn('fleet-repair', calls)
        self.assertNotIn('rollout restart', calls)

    def test_schema_repair_requires_approval(self):
        rc, calls, err = self.run_repair(schema=True, approve=1)
        self.assertEqual(rc, 1, err)
        self.assertNotIn('fleet-repair', calls)
        self.assertNotIn('rollout restart', calls)

    def test_decline_prevents_restart_even_with_yes_mode(self):
        rc, calls, err = self.run_repair(approve=1)
        self.assertEqual(rc, 1, err)
        self.assertIn('--defaultno', calls)
        self.assertNotIn('rollout restart', calls)

    def test_approved_restart_waits_for_rollout(self):
        rc, calls, err = self.run_repair()
        self.assertEqual(rc, 0, err)
        self.assertEqual(calls.count('rollout restart deployment/adguard-home'), 1)
        self.assertIn('rollout-check', calls)

    def test_failed_restart_validation_blocks_upgrade(self):
        rc, calls, err = self.run_repair(ready=1)
        self.assertEqual(rc, 2, err)
        self.assertIn('failure', calls)

    def test_changed_deployment_cancels_restart(self):
        rc, calls, err = self.run_repair(changed=1)
        self.assertEqual(rc, 2, err)
        self.assertNotIn('rollout restart', calls)

    def test_zero_replicas_are_preserved(self):
        rc, calls, err = self.run_repair(replicas=0)
        self.assertEqual(rc, 1, err)
        self.assertNotIn('dialog', calls)
        self.assertNotIn('rollout restart', calls)

    def test_noninteractive_dry_run_and_other_apps_never_restart(self):
        for args in [dict(tty=False), dict(dry=1), dict(app='other')]:
            with self.subTest(args=args):
                rc, calls, err = self.run_repair(**args)
                self.assertEqual(rc, 1, err)
                self.assertEqual(calls, '')


if __name__ == '__main__':
    unittest.main()
