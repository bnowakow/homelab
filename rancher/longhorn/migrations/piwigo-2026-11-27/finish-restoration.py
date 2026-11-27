#!/usr/bin/env python3
"""Finish staged restoration; hold GitRepo until the corrected source is published."""
import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
import time
import subprocess

spec = importlib.util.spec_from_file_location("activation", Path(__file__).with_name("activate-piwigo.py"))
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)
m, s = a.m, a.s


def json_objects(text):
    """kubectl can emit a List or concatenated JSON documents for YAML input."""
    decoder = json.JSONDecoder()
    offset = 0
    result = []
    while offset < len(text):
        while offset < len(text) and text[offset].isspace():
            offset += 1
        if offset == len(text):
            break
        obj, offset = decoder.raw_decode(text, offset)
        if not isinstance(obj, dict):
            raise RuntimeError("Expected Kubernetes JSON object")
        result.extend(obj.get("items", []) if obj.get("kind") == "List" else [obj])
    if not result:
        raise RuntimeError("No Kubernetes objects returned")
    return result


def settle_io(directory):
    deadline = time.monotonic() + 300
    while True:
        try:
            m.low_io()
            return
        except RuntimeError as error:
            m.log(directory, "FINAL RESTORATION WAIT " + str(error))
            if time.monotonic() >= deadline:
                raise RuntimeError("Final restoration held for elevated I/O for five minutes") from error
            time.sleep(10)


def restore_pause(directory, original):
    item = m.row(original)
    if "paused" in original["spec"]:
        # Preserve an explicitly saved value; no managedFields rewrite needed.
        m.patch(directory, item, "paused", original["spec"]["paused"])
        return
    live = m.api("-n", item["namespace"], "get", item["kind"], item["name"], "-o", "json")
    if live["metadata"]["uid"] != item["uid"]:
        raise RuntimeError("Fleet object identity changed before pause restoration")
    if "paused" in live["spec"]:
        ops = [{"op": "test", "path": "/metadata/uid", "value": item["uid"]},
               {"op": "test", "path": "/spec/paused", "value": live["spec"]["paused"]},
               {"op": "remove", "path": "/spec/paused"}]
        m.log(directory, "REQUEST restore originally absent pause field " + item["namespace"] + "/" + item["name"])
        try:
            live = m.api("-n", item["namespace"], "patch", item["kind"], item["name"],
                         "--type=json", "-p", json.dumps(ops), "-o", "json")
        except subprocess.CalledProcessError:
            live = m.api("-n", item["namespace"], "get", item["kind"], item["name"], "-o", "json")
            if live["metadata"]["uid"] != item["uid"] or "paused" in live["spec"]:
                raise
    if live["metadata"]["uid"] != item["uid"] or "paused" in live["spec"]:
        raise RuntimeError("Original pause-field absence not confirmed")
    m.log(directory, "CONFIRMED original pause-field absence restored " + item["namespace"] + "/" + item["name"] + "; other fields/ownership untouched")


def rollout(item):
    if item["replicas"]:
        subprocess.run(m.K + ["-n", item["namespace"], "rollout", "status", item["kind"] + "/" + item["name"], "--timeout=180s"],
                       check=True, timeout=200)


def verify_app_counts(plan, include_controls=False, directory=None):
    current = {}
    for resource in ("deployments", "statefulsets"):
        for obj in m.api("get", resource, "-A", "-o", "json")["items"]:
            current[obj["metadata"]["uid"]] = obj
    mismatches = []
    for item in plan["workloads"]:
        if (item["control"] and not include_controls) or item["namespace"] == "apps-piwigo":
            continue
        expected = m.effective_replicas(item)
        obj = current.get(item["uid"])
        if obj is None or obj["spec"].get("replicas", 1) != expected:
            mismatches.append({"namespace": item["namespace"], "name": item["name"],
                               "savedReplicas": item["replicas"], "expectedReplicas": expected, "currentReplicas": obj["spec"].get("replicas", 1) if obj else None,
                               "reason": "replica count differs" if obj else "saved workload UID absent"})
            continue
        ready = obj.get("status", {}).get("availableReplicas", obj.get("status", {}).get("readyReplicas", 0))
        if ready != expected or (expected == 0 and obj.get("status", {}).get("replicas", 0)):
            mismatches.append({"namespace": item["namespace"], "name": item["name"], "savedReplicas": item["replicas"],
                               "expectedReplicas": expected, "currentReplicas": obj["spec"].get("replicas", 1), "readyReplicas": ready, "reason": "rollout not converged"})
    if directory is not None:
        a.save(directory, "replica-verification", {"checkedUTC": m.stamp(), "mismatches": mismatches})
    if mismatches:
        print(json.dumps({"replicaMismatches": mismatches}, indent=2), flush=True)
        raise RuntimeError("Restoration has " + str(len(mismatches)) + " replica/readiness mismatch(es); all are listed above")


def restore_backup_labels(directory):
    pvc = m.api("-n", "apps-piwigo", "get", "pvc", "piwigo-original-copy-longhorn-crypto", "-o", "json")
    volume = s.get("volumes.longhorn.io", pvc["spec"]["volumeName"])
    if not volume["spec"].get("encrypted") or volume["status"].get("currentNodeID") != "proxmox3":
        raise RuntimeError("Recovered production database storage changed")
    a.save(directory, "production-before-backup-labels", {"claim": pvc, "volume": volume})
    for obj in (pvc, volume):
        labels = {**obj["metadata"].get("labels", {}), "recurring-job-group.longhorn.io/nas-backup": "enabled"}
        if obj["kind"] == "PersistentVolumeClaim":
            labels["recurring-job.longhorn.io/source"] = "enabled"
        ops = [{"op": "test", "path": "/metadata/uid", "value": obj["metadata"]["uid"]},
               {"op": "add", "path": "/metadata/labels", "value": labels}]
        result = m.api("-n", obj["metadata"]["namespace"], "patch", obj["kind"], obj["metadata"]["name"],
                       "--type=json", "-p", json.dumps(ops), "-o", "json")
        if result["metadata"]["labels"].get("recurring-job-group.longhorn.io/nas-backup") != "enabled":
            raise RuntimeError("Recovered database backup selection not confirmed")
    m.log(directory, "CONFIRMED recovered encrypted production database selected for nas-backup recurring group; no backup execution or restore claimed")


def complete_verification(directory, plan, pauses, k3s_before, allow_paused_piwigo=False):
    verify_app_counts(plan, include_controls=True, directory=directory)
    cronjobs = {x["metadata"]["uid"]: x for x in a.stage.get_all("cronjobs")}
    for item in plan["cronjobs"]:
        current = cronjobs.get(item["uid"])
        if current is None or current["spec"].get("suspend", False) != item["suspend"]:
            raise RuntimeError("CronJob suspension state not restored: " + item["name"])
    fleet_not_ready = []
    intentional_holds = []
    for group, resource in (("bundles", "bundles.fleet.cattle.io"), ("bundledeployments", "bundledeployments.fleet.cattle.io")):
        objects = {x["metadata"]["uid"]: x for x in a.stage.get_all(resource)}
        for original in pauses[group]:
            current = objects.get(original["metadata"]["uid"])
            if current is None:
                raise RuntimeError("Fleet object identity changed: " + original["metadata"]["name"])
            if allow_paused_piwigo and current["metadata"]["name"] == "piwigo":
                helm = current["spec"].get("helm", current["spec"].get("options", {}).get("helm", {}))
                if helm.get("version") != "22.3.2":
                    raise RuntimeError("Piwigo hold cannot accept a stale/unsafe desired chart")
            if current["spec"].get("paused", False) != original["spec"].get("paused", False):
                if allow_paused_piwigo and current["metadata"]["name"] == "piwigo" and current["spec"].get("paused") is True:
                    intentional_holds.append({"kind": current["kind"], "namespace": current["metadata"]["namespace"],
                                              "name": "piwigo", "chart": "22.3.2", "reason": "User chose local application without publishing; prevent old remote source from undoing recovery"})
                else:
                    raise RuntimeError("Fleet pause state not restored: " + original["metadata"]["name"])
            if group == "bundledeployments":
                ready = any(c["type"] == "Ready" and c["status"] == "True" for c in current.get("status", {}).get("conditions", []))
                applied = current.get("status", {}).get("appliedDeploymentID") == current["spec"]["deploymentID"]
                if not ready or not applied:
                    fleet_not_ready.append(current["metadata"]["name"])
                    if current["metadata"]["name"] == "piwigo":
                        raise RuntimeError("Piwigo Fleet deployment is not applied and Ready")
    production = m.api("-n", "apps-piwigo", "get", "pvc", "piwigo-original-copy-longhorn-crypto", "-o", "json")
    volume = s.get("volumes.longhorn.io", production["spec"]["volumeName"])
    if volume["metadata"].get("labels", {}).get("recurring-job-group.longhorn.io/nas-backup") != "enabled":
        raise RuntimeError("Recovered production volume backup group is not enabled")
    s.ready()
    k3s_after = s.k3s()
    if k3s_before != k3s_after:
        a.save(directory, "restoration-k3s-change", {"before": k3s_before, "after": k3s_after})
        raise RuntimeError("k3s state/restart count changed during final restoration; inspect before declaring stability")
    retained = {}
    for name in ("piwigo-mariadb-data-longhorn-crypto", "piwigo-mariadb-data", "piwigo-mariadb-recovered-longhorn-crypto"):
        claim = m.api("-n", "apps-piwigo", "get", "pvc", name, "-o", "json")
        retained[name] = {"uid": claim["metadata"]["uid"], "volume": claim["spec"].get("volumeName"), "phase": claim["status"].get("phase")}
    # Never let the old remote chart/claim settings overwrite the validated recovery.
    gitrepo_held = False
    publication = directory / "gitops-source-verified.json"
    source_proof = json.loads(publication.read_text()) if publication.exists() else None
    for item in plan["gitrepos"]:
        obj = m.api("-n", item["namespace"], "get", "gitrepo", item["name"], "-o", "json")
        gitrepo_held = gitrepo_held or obj["spec"].get("paused", False)
        if not obj["spec"].get("paused") and not (source_proof and obj["metadata"]["uid"] == source_proof["gitRepoUID"] and obj.get("status", {}).get("commit") == source_proof["commit"]):
            raise RuntimeError("GitRepo resumed before recovery source publication; inspect immediately")
    report = {"completedUTC": m.stamp(), "applicationReplicasRestored": True,
              "savedZeroCountsPreserved": True, "controlsRestored": True,
              "scheduleStatesRestored": True, "bundlePauseStatesRestored": not intentional_holds,
              "otherBundlePauseStatesRestored": True, "intentionalPiwigoHolds": intentional_holds,
              "gitRepoHeldPaused": gitrepo_held, "reason": "Recovery source publication pending" if gitrepo_held else "Published recovery source verified and reconciliation resumed",
              "proxmox2OldExcludedFromRecoveryStorage": True, "temporaryDatabaseReplicas": 1,
              "newDatabaseBackupGroupSelected": True, "newBackupVerified": False}
    report["localK3s"] = k3s_after
    report["retainedClaims"] = retained
    report["fleetNotReady"] = fleet_not_ready
    report["approvedReplicaOverrides"] = m.approved_replica_overrides()
    report["sourcePublication"] = source_proof
    a.save(directory, "restoration-result", report)
    m.log(directory, "RESTORATION VERIFIED; gitRepoHeld=" + str(gitrepo_held) + "; approved replica counts/schedules/controls restored; source/unused claims retained")
    print(json.dumps(report, indent=2))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true", help="Only verify completed restoration; make no cluster changes")
    parser.add_argument("--allow-paused-piwigo", action="store_true", help="Accept the planned local-only Piwigo hold, requiring chart 22.3.2 and an applied Ready deployment")
    args = parser.parse_args()
    os.umask(0o077)
    if not (args.directory / "piwigo-validated.json").exists():
        raise RuntimeError("Piwigo validation marker is absent")
    plan = json.loads((args.directory / "execution-plan.json").read_text())
    pauses = json.loads((args.directory / "fleet-pause-baseline.json").read_text())
    if not args.verify_only:
        verify_app_counts(plan, directory=args.directory)
    s.ready()
    k3s_before = s.k3s()
    a.validate_application(args.directory)
    if args.verify_only:
        time.sleep(5)
        complete_verification(args.directory, plan, pauses, k3s_before, args.allow_paused_piwigo)
        return
    settle_io(args.directory)
    restore_backup_labels(args.directory)
    controls = [x for x in plan["workloads"] if x["control"]]
    # Upgrade automation resumes only after the remaining restoration completes.
    ordinary = [x for x in controls if x["name"] != "system-upgrade-controller"]
    for item in sorted(ordinary, key=lambda x: x["name"] != "rancher"):
        settle_io(args.directory)
        m.patch(args.directory, item, "replicas", item["replicas"])
        rollout(item)
        m.release_field(args.directory, item, "replicas")
        m.log(args.directory, "RESTORED CONTROL " + item["namespace"] + "/" + item["name"])
        time.sleep(3)
    # Match the original schedule values before allowing Fleet to apply them.
    for item in plan["jobs"] + plan["cronjobs"]:
        settle_io(args.directory)
        m.patch(args.directory, item, "suspend", item["suspend"])
        m.release_field(args.directory, item, "suspend")
    live_bds = {x["metadata"]["uid"]: x for x in a.stage.get_all("bundledeployments.fleet.cattle.io")}
    for item in pauses["bundledeployments"]:
        if item["metadata"]["name"] == "piwigo":
            continue
        live = live_bds.get(item["metadata"]["uid"])
        if live is None or live["spec"]["deploymentID"] != item["spec"]["deploymentID"]:
            raise RuntimeError("Another BundleDeployment changed; inspect before unpausing " + item["metadata"]["name"])
    # Filebot must remain zero even if a controller's desired config drifted.
    filebot = m.api("-n", "fleet-local", "get", "bundle", "filebot", "-o", "json")
    desired = filebot["spec"].get("helm", {}).get("values", {}).get("workload", {}).get("main", {}).get("replicas")
    if desired is None:
        # Older Fleet versions externalize Helm values. The unchanged applied
        # deployment ID plus its actual release manifest proves the desired zero.
        bd = next(x for x in live_bds.values() if x["metadata"]["name"] == "filebot")
        if bd.get("status", {}).get("appliedDeploymentID") != bd["spec"]["deploymentID"]:
            raise RuntimeError("Filebot desired deployment is not applied")
        manifest = subprocess.run(["helm", "--kubeconfig", "/etc/rancher/k3s/k3s.yaml", "-n", "apps-filebot", "get", "manifest", "filebot"],
                                  check=True, capture_output=True, text=True, timeout=40)
        converted = subprocess.run(m.K + ["create", "--dry-run=client", "--validate=false", "-f", "-", "-o", "json"],
                                   input=manifest.stdout, check=True, capture_output=True, text=True, timeout=40)
        a.save(args.directory, "filebot-release-conversion", {"stdout": converted.stdout})
        objects = json_objects(converted.stdout)
        matches = [x for x in objects if x["kind"] == "Deployment" and x["metadata"]["name"] == "filebot"]
        if len(matches) != 1:
            raise RuntimeError("Expected one Filebot Deployment in its applied release manifest")
        deployment = matches[0]
        desired = deployment["spec"].get("replicas", 1)
    if desired != 0:
        raise RuntimeError("Filebot desired Fleet replica count is not zero; keep its bundle held")
    for group in ("bundles", "bundledeployments"):
        for obj in pauses[group]:
            if obj["metadata"]["name"] == "piwigo":
                continue
            settle_io(args.directory)
            restore_pause(args.directory, obj)
            time.sleep(0.2)
    for item in controls:
        if item["name"] == "system-upgrade-controller":
            settle_io(args.directory)
            m.patch(args.directory, item, "replicas", item["replicas"])
            rollout(item)
            m.release_field(args.directory, item, "replicas")
    complete_verification(args.directory, plan, pauses, k3s_before, args.allow_paused_piwigo)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        if "--directory" in sys.argv:
            directory = Path(sys.argv[sys.argv.index("--directory") + 1])
            if directory.is_dir():
                m.log(directory, "FINAL RESTORATION FAILED " + type(error).__name__ + ": " + str(error))
        raise
