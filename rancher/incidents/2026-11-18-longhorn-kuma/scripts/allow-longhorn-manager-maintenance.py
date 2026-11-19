#!/usr/bin/env python3
"""Allow Longhorn managers through the storage-repair taint; dry-run by default."""
import argparse
import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

TARGET = {"key": "maintenance", "operator": "Equal", "value": "storage-repair",
          "effect": "NoSchedule"}
NODE = "proxmox2-old"


def plan(ds):
    """Use OnDelete so changing the template cannot roll healthy managers."""
    if ds["metadata"].get("deletionTimestamp"):
        raise RuntimeError("Manager DaemonSet is terminating")
    spec = ds["spec"]
    tolerations = spec["template"]["spec"].get("tolerations", [])
    if any(all(t.get(k) == v for k, v in TARGET.items()) for t in tolerations):
        return []
    return [
        {"op": "test", "path": "/metadata/resourceVersion",
         "value": ds["metadata"]["resourceVersion"]},
        {"op": "add", "path": "/spec/updateStrategy", "value": {"type": "OnDelete"}},
        {"op": "add", "path": "/spec/template/spec/tolerations",
         "value": tolerations + [TARGET]},
    ]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--yes", action="store_true", help="Explicit confirmation without dialog")
    ap.add_argument("--kubeconfig", default="/etc/rancher/k3s/k3s.yaml")
    ap.add_argument("--log-dir", default="/var/tmp")
    args = ap.parse_args()
    kube = ["kubectl", "--kubeconfig=" + args.kubeconfig, "--request-timeout=20s"]

    def call(argv):
        p = subprocess.run(kube + argv, capture_output=True, text=True, timeout=30)
        if p.returncode:
            raise RuntimeError(p.stderr.strip() or p.stdout.strip())
        return json.loads(p.stdout)

    def get(kind, name, namespace=None):
        return call((["-n", namespace] if namespace else []) +
                    ["get", kind, name, "-o", "json"])

    node = get("node", NODE)
    if not node["spec"].get("unschedulable"):
        raise RuntimeError("Expected proxmox2-old to remain cordoned")
    if not any(all(t.get(k) == TARGET[k] for k in ("key", "value", "effect"))
               for t in node["spec"].get("taints", [])):
        raise RuntimeError("Expected maintenance=storage-repair:NoSchedule taint")
    ds = get("daemonset", "longhorn-manager", "longhorn-system")
    bundle = get("bundles.fleet.cattle.io", "longhorn", "fleet-local")
    patch = plan(ds)
    print(json.dumps({"daemonsetPatch": patch,
                      "pauseFleetBundle": not bundle.get("spec", {}).get("paused", False)}, indent=2))
    if not patch:
        print("Exact toleration already present; no changes made.")
        return
    if not args.execute:
        print("Dry run. Use --execute to apply after reviewing the patch.")
        return
    if not args.yes:
        if not (shutil.which("dialog") and sys.stdin.isatty() and sys.stdout.isatty()):
            raise RuntimeError("No interactive dialog terminal. Use --execute --yes for explicit confirmation.")
        result = subprocess.run(["dialog", "--defaultno", "--yesno",
                                 "Pause Fleet longhorn and add the manager maintenance toleration? "
                                 "OnDelete prevents automatic manager replacement. Node stays cordoned.",
                                 "10", "76"])
        if result.returncode:
            print("Cancelled; no changes made.")
            return
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    folder = Path(args.log_dir) / ("allow-longhorn-manager-maintenance-" + stamp)
    folder.mkdir(mode=0o700, parents=False)

    def save(name, value):
        (folder / name).write_text(json.dumps(value, indent=2) + "\n")

    def record(event, **fields):
        line = json.dumps({"time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                           "event": event, **fields})
        with (folder / "actions.jsonl").open("a") as log:
            log.write(line + "\n")
            log.flush()
            os.fsync(log.fileno())
        print(line, flush=True)

    save("node-before.json", node)
    save("daemonset-before.json", ds)
    save("bundle-before.json", bundle)
    save("daemonset-patch.json", patch)
    print("Backup and action log:", folder)
    try:
        record("begin")
        if not bundle.get("spec", {}).get("paused", False):
            pause = [{"op": "test", "path": "/metadata/resourceVersion",
                      "value": bundle["metadata"]["resourceVersion"]},
                     {"op": "add", "path": "/spec/paused", "value": True}]
            record("fleet_pause_requested")
            call(["-n", "fleet-local", "patch", "bundles.fleet.cattle.io", "longhorn",
                  "--type=json", "-p", json.dumps(pause), "-o", "json"])
        live_bundle = get("bundles.fleet.cattle.io", "longhorn", "fleet-local")
        if live_bundle["metadata"]["uid"] != bundle["metadata"]["uid"] or not live_bundle["spec"].get("paused"):
            raise RuntimeError("Fleet pause verification failed")
        record("fleet_pause_confirmed")
        record("daemonset_patch_requested")
        call(["-n", "longhorn-system", "patch", "daemonset", "longhorn-manager",
              "--type=json", "-p", json.dumps(patch), "-o", "json"])
        live = get("daemonset", "longhorn-manager", "longhorn-system")
        save("daemonset-after.json", live)
        if (live["metadata"]["uid"] != ds["metadata"]["uid"] or plan(live)
                or live["spec"]["updateStrategy"]["type"] != "OnDelete"):
            raise RuntimeError("DaemonSet change verification failed")
        record("daemonset_patch_confirmed")
        print("Template verified. Manager readiness and storage recovery require separate validation.")
        print("Fleet stays paused and strategy stays OnDelete; review saved originals before restoration.")
    except Exception as exc:
        record("failed", error=str(exc))
        raise


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print("error:", exc, file=sys.stderr)
        sys.exit(2)
