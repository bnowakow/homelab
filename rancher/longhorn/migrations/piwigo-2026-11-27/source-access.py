#!/usr/bin/env python3
"""Guarded removal of an orphaned snapshot ticket; no mounting or data copying."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import time

SOURCE = "pvc-6cbf25ee-1394-4a06-94ff-4fc9fcca03ae"
CLAIM = "piwigo-mariadb-data-longhorn-crypto"
NAMESPACE = "longhorn-system"
spec = importlib.util.spec_from_file_location("maintenance", Path(__file__).with_name("maintenance.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def get(kind, name=None):
    args = ["-n", NAMESPACE, "get", kind]
    if name:
        args.append(name)
    return m.api(*args, "-o", "json")


def k3s():
    result = subprocess.run(["systemctl", "show", "k3s", "-p", "NRestarts", "-p", "ActiveState"],
                            capture_output=True, text=True, check=True, timeout=10)
    values = dict(line.split("=", 1) for line in result.stdout.splitlines())
    if values.get("ActiveState") != "active":
        raise RuntimeError("Local k3s is not active")
    return values


def ready():
    result = subprocess.run(m.K + ["get", "--raw=/readyz"],
                            capture_output=True, text=True, check=True, timeout=40)
    if result.stdout.strip() != "ok":
        raise RuntimeError("API readiness failed")


def guards(directory):
    state = directory / "execution-plan.json"
    data = json.loads((state if state.exists() else directory / "plan.json").read_text())
    problems = m.verify(data)
    if problems:
        raise RuntimeError("Application shutdown no longer verified: " + ", ".join(problems))
    if any(x.get("status", {}).get("active", 0) for x in m.api("get", "jobs", "-A", "-o", "json")["items"]):
        raise RuntimeError("Active jobs remain")
    for pod in m.api("get", "pods", "-A", "-o", "json")["items"]:
        if pod.get("status", {}).get("phase") in ("Succeeded", "Failed"):
            continue
        for volume in pod["spec"].get("volumes", []):
            if pod["metadata"]["namespace"] == "apps-piwigo" and volume.get("persistentVolumeClaim", {}).get("claimName") == CLAIM:
                raise RuntimeError("Original claim has a live pod consumer")
    attachments = m.api("get", "volumeattachments.storage.k8s.io", "-o", "json")["items"]
    if any(x["spec"].get("source", {}).get("persistentVolumeName") == SOURCE for x in attachments):
        raise RuntimeError("Original volume still has a CSI attachment; inspect before removal")
    old = get("nodes.longhorn.io", "proxmox2-old")
    if old["spec"].get("allowScheduling") or any(d.get("allowScheduling") for d in old["spec"].get("disks", {}).values()):
        raise RuntimeError("Excluded-node scheduling protection changed")
    m.low_io()
    ready()
    before = k3s()
    time.sleep(10)
    ready()
    if before != k3s():
        raise RuntimeError("k3s state/restart count changed during preflight")
    m.log(directory, "SOURCE PREFLIGHT API ready; local k3s stable " + json.dumps(before) + "; PSI=" + json.dumps(m.low_io()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    guards(args.directory)
    volume = get("volumes.longhorn.io", SOURCE)
    engines = [x for x in get("engines.longhorn.io")["items"] if x["spec"].get("volumeName") == SOURCE]
    if not engines or any(x["status"].get("currentState") != "stopped" or x["status"].get("endpoint") for x in engines):
        raise RuntimeError("Source engine is not confirmed stopped")
    ticket = get("volumeattachments.longhorn.io", SOURCE)
    snapshots = get("snapshots.longhorn.io")["items"]
    tickets = ticket["spec"].get("attachmentTickets", {})
    expected = "snapshot-controller-1ac74e18-bf4d-45b4-9e80-91f3b40f505f"
    if tickets and set(tickets) != {expected}:
        raise RuntimeError("Attachment tickets changed; refusing removal")
    if tickets:
        entry = tickets[expected]
        uid = expected.removeprefix("snapshot-controller-")
        if entry.get("type") != "snapshot-controller" or entry.get("nodeID") != "proxmox2-old" or any(x["metadata"]["uid"] == uid for x in snapshots):
            raise RuntimeError("Ticket is not a confirmed orphan for the excluded node")
        saved = args.directory / ("source-before-detach-" + str(time.time_ns()) + ".json")
        m.write(saved, {"volume": volume, "attachment": ticket, "engines": engines, "snapshots": snapshots})
        ops = [{"op": "test", "path": "/metadata/resourceVersion", "value": ticket["metadata"]["resourceVersion"]},
               {"op": "test", "path": "/spec/attachmentTickets/" + expected, "value": entry},
               {"op": "remove", "path": "/spec/attachmentTickets/" + expected}]
        m.log(args.directory, "REQUEST remove confirmed orphaned snapshot ticket " + expected + "; source engines stopped; no CSI/pod consumers")
        result = m.api("-n", NAMESPACE, "patch", "volumeattachments.longhorn.io", SOURCE,
                       "--type=json", "-p", json.dumps(ops), "-o", "json")
        if expected in result["spec"].get("attachmentTickets", {}):
            raise RuntimeError("Ticket removal was not confirmed")
        m.log(args.directory, "CONFIRMED orphan ticket removed; no replicas or data deleted")
    deadline = time.monotonic() + 120
    while True:
        volume = get("volumes.longhorn.io", SOURCE)
        if volume["status"].get("state") == "detached" and not volume["status"].get("currentNodeID"):
            break
        if time.monotonic() >= deadline:
            raise RuntimeError("Source did not detach; do not force-detach or change replicas")
        time.sleep(5)
    m.write(args.directory / ("source-detached-" + str(time.time_ns()) + ".json"), volume)
    m.log(args.directory, "CONFIRMED original source detached; no database started and no data copied")
    print("Source detached. Original PVC, replicas and backup preserved. No data copied.")


if __name__ == "__main__":
    main()
