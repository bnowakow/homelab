#!/usr/bin/env python3
"""Piwigo recovery baseline, UID-checked shutdown, and staged restoration."""
import argparse
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time

K = ["kubectl", "--kubeconfig", "/etc/rancher/k3s/k3s.yaml", "--request-timeout=30s"]
RESOURCES = ["deployments", "statefulsets", "daemonsets", "cronjobs", "jobs", "pods",
             "horizontalpodautoscalers", "gitrepos.fleet.cattle.io"]


def api(*args):
    result = subprocess.run(K + list(args), check=True, capture_output=True, text=True, timeout=40)
    return json.loads(result.stdout)


def stamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def stopped_namespace(ns):
    return ns.startswith("apps-") and ns != "apps-adguard-home" or ns == "cloudcasa-io"


def controller(ns, name):
    return ns.startswith("cattle-fleet-") or (ns == "cattle-system" and name in ("rancher", "system-upgrade-controller"))


def row(obj):
    return {"namespace": obj["metadata"]["namespace"], "name": obj["metadata"]["name"],
            "uid": obj["metadata"]["uid"], "kind": obj["kind"]}


def write(path, data):
    with path.open("x") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def log(directory, message):
    with (directory / "actions.log").open("a") as f:
        f.write(stamp() + " " + message + "\n")


def patch(directory, item, field, value):
    ops = [{"op": "test", "path": "/metadata/uid", "value": item["uid"]},
           {"op": "add", "path": "/spec/" + field, "value": value}]
    log(directory, "REQUEST " + json.dumps({**item, field: value}))
    obj = api("-n", item["namespace"], "patch", item["kind"], item["name"],
              "--type=json", "-p", json.dumps(ops), "-o", "json")
    if obj["spec"].get(field) != value:
        raise RuntimeError("Patch was not confirmed")
    log(directory, "CONFIRMED " + item["namespace"] + "/" + item["name"] + " " + field + "=" + str(value))


def _release_field_once(directory, item, field):
    """Relinquish only the maintenance patch manager's saved spec field."""
    obj = api("-n", item["namespace"], "get", item["kind"], item["name"], "--show-managed-fields=true", "-o", "json")
    if obj["metadata"]["uid"] != item["uid"]:
        raise RuntimeError("Object identity changed before field handoff")
    import copy
    entries = []
    changed = False
    for original in obj["metadata"].get("managedFields", []):
        entry = copy.deepcopy(original)
        fields = entry.get("fieldsV1", {})
        spec_fields = fields.get("f:spec", {})
        if entry.get("manager") == "kubectl-patch" and "f:" + field in spec_fields:
            del spec_fields["f:" + field]
            changed = True
            if not spec_fields:
                del fields["f:spec"]
            if not fields:
                continue
        entries.append(entry)
    if changed:
        ops = [{"op": "test", "path": "/metadata/resourceVersion", "value": obj["metadata"]["resourceVersion"]},
               {"op": "replace", "path": "/metadata/managedFields", "value": entries}]
        result = api("-n", item["namespace"], "patch", item["kind"], item["name"], "--type=json", "-p", json.dumps(ops), "-o", "json")
        if result["spec"] != obj["spec"]:
            raise RuntimeError("Spec changed concurrently during field handoff; inspect before continuing")
        log(directory, "RESTORED FIELD HANDOFF " + item["namespace"] + "/" + item["name"] + " spec." + field + "; other ownership preserved")


def release_field(directory, item, field):
    """Refetch and retry optimistic-lock races without broadening field scope."""
    for attempt in range(5):
        try:
            return _release_field_once(directory, item, field)
        except subprocess.CalledProcessError as error:
            diagnostic = directory / ("field-handoff-error-" + str(time.time_ns()) + ".json")
            write(diagnostic, {"namespace": item["namespace"], "name": item["name"],
                               "field": field, "returncode": error.returncode, "stderr": error.stderr})
            message = (error.stderr or "").lower()
            transient = any(text in message for text in ("conflict", "test failed", "test operation", "testing value", "object has been modified"))
            if not transient or attempt == 4:
                raise RuntimeError("Field handoff failed; private diagnostic: " + str(diagnostic)) from error
            log(directory, "RETRY FIELD HANDOFF " + item["namespace"] + "/" + item["name"] +
                " spec." + field + " after concurrent update; attempt=" + str(attempt + 2))
            time.sleep(0.2 * (attempt + 1))


def plan(directory):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if (directory / "plan.json").exists():
        raise RuntimeError("Baseline already exists; use a new directory, never overwrite it")
    snapshots = {resource: api("get", resource, "-A", "-o", "json") for resource in RESOURCES}
    for resource, data in snapshots.items():
        if not isinstance(data.get("items"), list):
            raise RuntimeError("Incomplete inventory: " + resource)
    if snapshots["horizontalpodautoscalers"]["items"]:
        raise RuntimeError("Autoscalers exist; explicitly plan their suspension first")
    for resource, data in snapshots.items():
        write(directory / (resource + ".json"), data)
    workloads, kept = [], []
    for resource in ("deployments", "statefulsets"):
        for obj in snapshots[resource]["items"]:
            ns, name = obj["metadata"]["namespace"], obj["metadata"]["name"]
            item = {**row(obj), "replicas": obj["spec"].get("replicas", 1),
                    "control": controller(ns, name),
                    "database": any(word in name for word in ("mariadb", "postgres", "mongodb"))}
            (workloads if stopped_namespace(ns) or controller(ns, name) else kept).append(item)
    jobs = [{**row(x), "suspend": x["spec"].get("suspend", False)}
            for x in snapshots["jobs"]["items"] if x.get("status", {}).get("active", 0)]
    cronjobs = [{**row(x), "suspend": x["spec"].get("suspend", False)}
                for x in snapshots["cronjobs"]["items"]]
    gitrepos = [{**row(x), "paused": x["spec"].get("paused", False)}
               for x in snapshots["gitrepos.fleet.cattle.io"]["items"]]
    data = {"created": stamp(), "excludedStorageNode": "proxmox2-old", "workloads": workloads,
            "kept": kept, "jobs": jobs, "cronjobs": cronjobs, "gitrepos": gitrepos}
    write(directory / "plan.json", data)
    log(directory, "BASELINE SAVED; NO CLUSTER CHANGES; AdGuard kept for DNS")
    print(json.dumps({"baseline": str(directory), "stop": workloads, "activeJobs": jobs,
                      "keep": kept, "cronjobs": cronjobs}, indent=2))


def verify(data, databases_only=False, controls_only=False, allowed_pod_uids=()):
    live = []
    for resource in ("deployments", "statefulsets"):
        live.extend(api("get", resource, "-A", "-o", "json")["items"])
    by_uid = {x["metadata"]["uid"]: x for x in live}
    problems = []
    targets = [x for x in data["workloads"] if (not databases_only or not x["database"])
               and (not controls_only or x["control"])]
    for item in targets:
        obj = by_uid.get(item["uid"])
        if obj is None or obj["spec"].get("replicas", 1) != 0 or obj.get("status", {}).get("replicas", 0):
            problems.append(item["namespace"] + "/" + item["name"])
    for pod in api("get", "pods", "-A", "-o", "json")["items"]:
        if pod["metadata"]["uid"] in allowed_pod_uids:
            continue
        ns = pod["metadata"]["namespace"]
        if pod.get("status", {}).get("phase") in ("Succeeded", "Failed"):
            continue
        is_control = ns.startswith("cattle-fleet-") or (ns == "cattle-system" and
                     pod["metadata"]["name"].startswith(("rancher-", "system-upgrade-controller-"))
                     and not pod["metadata"]["name"].startswith("rancher-webhook-"))
        if is_control or (stopped_namespace(ns) and not controls_only):
            if databases_only and any(word in pod["metadata"]["name"] for word in ("mariadb", "postgres", "mongodb")):
                continue
            problems.append("pod " + ns + "/" + pod["metadata"]["name"])
    return problems


def wait_stopped(data, **kwargs):
    deadline = time.monotonic() + 300
    while True:
        remaining = verify(data, **kwargs)
        if not remaining:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError("Shutdown not verified: " + ", ".join(remaining))
        time.sleep(5)


def low_io():
    values = {}
    for line in Path("/proc/pressure/io").read_text().splitlines():
        words = line.split()
        values[words[0]] = float(dict(x.split("=") for x in words[1:])["avg10"])
    if values.get("some", 100) > 5 or values.get("full", 100) > 1:
        raise RuntimeError("I/O pressure is elevated: " + json.dumps(values))
    return values


def approved_replica_overrides():
    path = Path(__file__).with_name("restoration-overrides.json")
    return json.loads(path.read_text()).get("replicaOverrides", []) if path.exists() else []


def effective_replicas(item):
    for override in approved_replica_overrides():
        if all(item.get(key) == override[key] for key in ("namespace", "name", "uid")) and item["replicas"] == override["savedReplicas"]:
            return override["replicas"]
    return item["replicas"]


def stop(directory, data):
    low_io()
    nodes = api("-n", "longhorn-system", "get", "nodes.longhorn.io", "-o", "json")["items"]
    old = next(x for x in nodes if x["metadata"]["name"] == "proxmox2-old")
    if old["spec"].get("allowScheduling") or any(x.get("allowScheduling") for x in old["spec"].get("disks", {}).values()):
        raise RuntimeError("Excluded node scheduling protection has changed")
    if api("get", "horizontalpodautoscalers", "-A", "-o", "json")["items"]:
        raise RuntimeError("Autoscalers now exist; stop and review")
    # Preflight all saved object identities before the first mutation.
    for group in ("workloads", "cronjobs", "gitrepos", "jobs"):
        for item in data[group]:
            obj = api("-n", item["namespace"], "get", item["kind"], item["name"], "-o", "json")
            if obj["metadata"]["uid"] != item["uid"]:
                raise RuntimeError("Object identity changed: " + item["name"])
    log(directory, "SHUTDOWN START; single-replica recovery authorized; proxmox2-old excluded; PSI=" + json.dumps(low_io()))
    # Freeze controllers before suspending schedules, so Fleet cannot undo it.
    controls = sorted((x for x in data["workloads"] if x["control"]),
                      key=lambda x: (x["name"] != "rancher", x["name"] != "fleet-agent"))
    for item in controls:
        patch(directory, item, "replicas", 0)
    wait_stopped(data, controls_only=True)
    for item in data["gitrepos"]:
        patch(directory, item, "paused", True)
    for item in data["cronjobs"]:
        patch(directory, item, "suspend", True)
    # Capture jobs which started between the baseline and schedule suspension.
    saved_jobs = {x["uid"] for x in data["jobs"]}
    for obj in api("get", "jobs", "-A", "-o", "json")["items"]:
        if obj.get("status", {}).get("active", 0) and obj["metadata"]["uid"] not in saved_jobs:
            data["jobs"].append({**row(obj), "suspend": obj["spec"].get("suspend", False)})
    state = directory / "execution-plan.json"
    if not state.exists():
        write(state, data)
    for item in data["jobs"]:
        patch(directory, item, "suspend", True)
    for item in data["workloads"]:
        if not item["control"] and not item["database"]:
            patch(directory, item, "replicas", 0)
    wait_stopped(data, databases_only=True)
    for item in data["workloads"]:
        if item["database"]:
            patch(directory, item, "replicas", 0)
    wait_stopped(data)
    active = api("get", "jobs", "-A", "-o", "json")["items"]
    if any(x.get("status", {}).get("active", 0) for x in active):
        raise RuntimeError("Jobs remain active; no copy authorized")
    log(directory, "APPLICATION SHUTDOWN VERIFIED; AdGuard and essential infrastructure kept; PSI=" + json.dumps(low_io()))
    print("Shutdown verified. No attachment changes or data copying performed.")


def restore_apps(directory, data):
    if not (directory / "piwigo-validated.json").exists():
        raise RuntimeError("Piwigo cutover must pass live validation before other apps restore")
    # Piwigo and reconcilers remain stopped until verified cutover is staged.
    rows = [{**x, "replicas": effective_replicas(x)} for x in data["workloads"] if not x["control"] and x["namespace"] != "apps-piwigo"]
    for item in sorted(rows, key=lambda x: not x["database"]):
        current = api("-n", item["namespace"], "get", item["kind"], item["name"], "-o", "json")
        if current["metadata"]["uid"] != item["uid"]:
            raise RuntimeError("Application identity changed during restoration")
        status = current.get("status", {})
        ready = status.get("availableReplicas", status.get("readyReplicas", 0))
        converged = (current["spec"].get("replicas", 1) == item["replicas"] and ready == item["replicas"]
                     and (item["replicas"] != 0 or status.get("replicas", 0) == 0)
                     and status.get("observedGeneration", 0) >= current["metadata"].get("generation", 0))
        if not converged:
            if current["spec"].get("replicas", 1) != item["replicas"]:
                deadline = time.monotonic() + 300
                while True:
                    try:
                        low_io()
                        time.sleep(3)
                        low_io()
                        break
                    except RuntimeError as error:
                        log(directory, "RESTORE WAIT " + str(error) + "; next=" + item["namespace"] + "/" + item["name"])
                        if time.monotonic() > deadline:
                            raise RuntimeError("Restoration held for elevated I/O for five minutes; saved state preserved") from error
                        time.sleep(10)
                patch(directory, item, "replicas", item["replicas"])
            if item["replicas"]:
                timeout = startup_timeout(current)
                log(directory, "RESTORE WAIT EXISTING ROLLOUT " + item["namespace"] + "/" + item["name"] + " timeoutSeconds=" + str(timeout) + "; no restart requested")
                subprocess.run(K + ["-n", item["namespace"], "rollout", "status",
                               item["kind"] + "/" + item["name"], "--timeout=" + str(timeout) + "s"],
                               check=True, timeout=timeout + 30)
        log(directory, "RESTORED APPLICATION " + item["namespace"] + "/" + item["name"])
        release_field(directory, item, "replicas")
        time.sleep(3)
    log(directory, "UNRELATED APPLICATION RESTORATION COMPLETE; saved replica counts restored; validated Piwigo remains running; remaining controls/schedules held for finalization")
    print("Other application replicas restored. Validated Piwigo remains running; remaining controls and schedules await finalization.")


def startup_timeout(obj):
    budgets = [180]
    for container in obj.get("spec", {}).get("template", {}).get("spec", {}).get("containers", []):
        probe = container.get("startupProbe", {})
        if probe:
            budgets.append(probe.get("initialDelaySeconds", 0) +
                           probe.get("failureThreshold", 3) * probe.get("periodSeconds", 10) + 60)
    return max(budgets)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "status", "stop", "restore-apps"))
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    if args.mode == "plan":
        plan(args.directory)
        return
    state = args.directory / "execution-plan.json"
    data = json.loads((state if state.exists() else args.directory / "plan.json").read_text())
    if args.mode == "stop":
        stop(args.directory, data)
        return
    if args.mode == "restore-apps":
        restore_apps(args.directory, data)
        return
    if args.mode == "status":
        problems = verify(data)
        print(json.dumps({"shutdownVerified": not problems, "remaining": problems}, indent=2))
        return



if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        if "--directory" in sys.argv:
            directory = Path(sys.argv[sys.argv.index("--directory") + 1])
            if directory.is_dir():
                log(directory, "HELPER FAILED " + type(error).__name__ + ": " + str(error))
        raise
