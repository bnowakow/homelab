#!/usr/bin/env python3
"""Read-only, bounded watch for the existing three-member etcd cluster to recover."""
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def main():
    if os.geteuid() != 0:
        raise SystemExit('Run with sudo on proxmox3; existing etcd TLS certificates are required.')
    helper = Path(__file__).with_name('check-etcd-membership.py')
    deadline = time.monotonic() + 900
    consecutive = 0
    while time.monotonic() < deadline:
        result = subprocess.run([sys.executable, str(helper)], capture_output=True,
                                text=True, timeout=110)
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            data = {}
        members = data.get('members', [])
        expected = {'https://10.0.0.72:2379', 'https://10.0.0.50:2379',
                    'https://10.0.0.80:2379'}
        healthy = {item.get('endpoint') for item in data.get('healthyEndpoints', [])
                   if item.get('health') is True}
        ok = (result.returncode == 0 and data.get('threeVoterHealthConfirmed') is True
              and expected <= healthy and len(members) == 3)
        consecutive = consecutive + 1 if ok else 0
        print(json.dumps({'time': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                          'oldEtcdHealthy': 'https://10.0.0.72:2379' in healthy,
                          'allThreeHealthy': ok, 'consecutiveHealthy': consecutive,
                          'required': 3, 'evidence': data.get('directory'),
                          'errors': data.get('errors', []) if data else
                          [(result.stderr or result.stdout)[-600:]]}), flush=True)
        if consecutive >= 3:
            print('All three etcd endpoints healthy for three consecutive checks. '
                  'This verifies etcd recovery, not Docker, storage or node readiness.')
            return
        time.sleep(10)
    raise SystemExit('Timed out after 15 minutes; inspect the latest evidence. No changes made.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SystemExit(str(exc))
