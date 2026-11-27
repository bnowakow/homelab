#!/usr/bin/env python3
"""Stage the corrected Fleet bundle while all application reconciliation is held."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import time

spec = importlib.util.spec_from_file_location("database_check", Path(__file__).with_name("check-database.py"))
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)
m, s = d.m, d.s
COMMAND_LOG_DIR = None


def command(args, **kwargs):
    try:
        return subprocess.run(args, check=True, capture_output=True, text=True, timeout=180, **kwargs)
    except subprocess.CalledProcessError as error:
        if COMMAND_LOG_DIR is None:
            raise
        diagnostic = COMMAND_LOG_DIR / ("stage-command-error-" + str(time.time_ns()) + ".json")
        m.write(diagnostic, {"command": args, "returncode": error.returncode,
                             "stdout": error.stdout, "stderr": error.stderr})
        raise RuntimeError("Staging command failed; private diagnostic: " + str(diagnostic)) from None


def get_all(kind):
    return m.api("get", kind, "-A", "-o", "json")["items"]


def validate_bundle(bundle):
    helm = bundle["spec"].get("helm", {})
    claim = helm.get("values", {}).get("mariadb", {}).get("persistence", {}).get("data", {}).get("existingClaim")
    if helm.get("version") != "22.3.2" or claim != "piwigo-original-copy-longhorn-crypto":
        raise RuntimeError("Generated Fleet Bundle is not the intended recovery configuration")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    global COMMAND_LOG_DIR
    COMMAND_LOG_DIR = args.directory
    os.umask(0o077)
    plan = json.loads((args.directory / "execution-plan.json").read_text())
    pod = m.api("-n", "apps-piwigo", "get", "pod", d.POD, "-o", "json")
    if pod["spec"].get("nodeName") != "proxmox3" or pod["spec"]["containers"][0]["image"] != d.IMAGE:
        raise RuntimeError("Recovery database identity changed")
    if "--skip-networking" not in pod["spec"]["containers"][0].get("args", []):
        raise RuntimeError("Recovery database is not isolated")
    remaining = m.verify(plan, allowed_pod_uids=(pod["metadata"]["uid"],))
    if remaining:
        raise RuntimeError("Application shutdown changed: " + ", ".join(remaining))
    source = s.get("volumes.longhorn.io", s.SOURCE)
    if source["status"].get("state") != "detached":
        raise RuntimeError("Original source is not detached")
    render = (args.directory.parent / "cutover-rendered.yaml").read_text()
    if "claimName: piwigo-original-copy-longhorn-crypto" not in render or d.IMAGE not in render:
        raise RuntimeError("Validated render does not select recovered data/original image")
    if "claimName: piwigo-mariadb-data-longhorn-crypto" in render or "claimName: piwigo-mariadb-data\n" in render:
        raise RuntimeError("Render references a source or empty database claim")
    m.low_io()
    s.ready()
    # Confirm the installed CRDs support the pause field before using it.
    for name in ("bundles.fleet.cattle.io", "bundledeployments.fleet.cattle.io"):
        crd = m.api("get", "crd", name, "-o", "json")
        schema = next(x for x in crd["spec"]["versions"] if x.get("storage"))["schema"]["openAPIV3Schema"]
        if schema["properties"]["spec"]["properties"].get("paused", {}).get("type") != "boolean":
            raise RuntimeError("Installed Fleet CRD does not support spec.paused")
    # Fleet-native generation, then client-side conversion for precise JSON edits.
    root = Path(__file__).resolve().parents[3]
    output = args.directory / ("fleet-generated-" + str(time.time_ns()) + ".yaml")
    try:
        command(["fleet", "apply", "--kubeconfig", "/etc/rancher/k3s/k3s.yaml", "--namespace", "fleet-local",
                 "--label", "fleet.cattle.io/repo-name=rancher-cluster", "--output", str(output),
                 "piwigo", str(Path(root.name) / "fleet/apps/piwigo")], cwd=root.parent)
    except subprocess.CalledProcessError as error:
        diagnostic = args.directory / ("fleet-generation-error-" + str(time.time_ns()) + ".json")
        m.write(diagnostic, {"returncode": error.returncode, "stdout": error.stdout, "stderr": error.stderr})
        raise RuntimeError("Fleet generation failed before cluster changes; private diagnostic: " + str(diagnostic)) from None
    result = command(m.K + ["create", "--dry-run=client", "--validate=false", "-f", str(output), "-o", "json"])
    generated = json.loads(result.stdout)
    m.write(args.directory / ("fleet-generated-converted-" + str(time.time_ns()) + ".json"), generated)
    objects = generated["items"] if generated.get("kind") == "List" else [generated]
    bundles = get_all("bundles.fleet.cattle.io")
    deployments = get_all("bundledeployments.fleet.cattle.io")
    current = next(x for x in bundles if x["metadata"]["namespace"] == "fleet-local" and x["metadata"]["name"] == "piwigo")
    bundle = next(x for x in objects if x["kind"] == "Bundle" and x["metadata"]["name"] == "piwigo")
    bundle["metadata"]["namespace"] = "fleet-local"
    validate_bundle(bundle)
    for field in ("targets", "targetRestrictions", "targetCustomizationMode"):
        if field in current["spec"]:
            bundle["spec"][field] = current["spec"][field]
    bundle["metadata"]["labels"] = {**current["metadata"].get("labels", {}), **bundle["metadata"].get("labels", {})}
    if current["metadata"].get("ownerReferences"):
        bundle["metadata"]["ownerReferences"] = current["metadata"]["ownerReferences"]
    bundle["spec"]["paused"] = False
    bundle["spec"]["forceSyncGeneration"] = int(time.time())
    save = args.directory / "fleet-pause-baseline.json"
    if not save.exists():
        m.write(save, {"bundles": bundles, "bundledeployments": deployments})
    else:
        saved = json.loads(save.read_text())
        for key, live in (("bundles", bundles), ("bundledeployments", deployments)):
            identity = lambda x: (x["metadata"]["namespace"], x["metadata"]["name"], x["metadata"]["uid"])
            if {identity(x) for x in saved[key]} != {identity(x) for x in live}:
                raise RuntimeError("Fleet object identities changed since pause baseline; inspect before resuming")
        m.log(args.directory, "RESUME staging with original Fleet pause baseline preserved; object UIDs verified")
    for group in (bundles, deployments):
        for obj in group:
            if obj["metadata"]["name"] != "piwigo" and not obj["spec"].get("paused"):
                m.patch(args.directory, m.row(obj), "paused", True)
    # Verify all other existing BundleDeployments are held before any controller resumes.
    if any(not x["spec"].get("paused") for x in get_all("bundledeployments.fleet.cattle.io") if x["metadata"]["name"] != "piwigo"):
        raise RuntimeError("Other BundleDeployments are not all paused")
    payload = {"apiVersion": "v1", "kind": "List", "items": objects}
    m.write(args.directory / ("fleet-cutover-applied-input-" + str(time.time_ns()) + ".json"), payload)
    # Fleet embeds chart content (~813KB); client-side apply would exceed the
    # last-applied annotation limit. Update the Fleet-generated Bundle with a
    # live resourceVersion, preserving its current metadata and ownership.
    if len(objects) != 1 or objects[0]["kind"] != "Bundle":
        raise RuntimeError("Unexpected extra generated objects; inspect before applying")
    live = m.api("-n", "fleet-local", "get", "bundle", "piwigo", "-o", "json")
    if live["metadata"]["uid"] != current["metadata"]["uid"]:
        raise RuntimeError("Piwigo Bundle identity changed before update")
    updated = {"apiVersion": live["apiVersion"], "kind": live["kind"],
               "metadata": live["metadata"], "spec": bundle["spec"]}
    updated["metadata"]["labels"] = bundle["metadata"]["labels"]
    applied = command(m.K + ["replace", "-f", "-", "-o", "json"], input=json.dumps(updated))
    validate_bundle(json.loads(applied.stdout))
    m.log(args.directory, "CONFIRMED Fleet-native Piwigo recovery Bundle applied; ownership/targeting preserved; other bundles and BundleDeployments paused")
    control = next(x for x in plan["workloads"] if x["namespace"] == "cattle-fleet-system" and x["name"] == "fleet-controller")
    m.patch(args.directory, control, "replicas", control["replicas"])
    subprocess.run(m.K + ["-n", control["namespace"], "rollout", "status", "deployment/fleet-controller", "--timeout=180s"],
                   check=True, timeout=200)
    deadline = time.monotonic() + 180
    while True:
        matches = [x for x in get_all("bundledeployments.fleet.cattle.io") if x["metadata"]["name"] == "piwigo"]
        if len(matches) == 1 and matches[0]["spec"].get("options", {}).get("helm", {}).get("version") == "22.3.2":
            bd = matches[0]
            if bd["spec"].get("paused"):
                m.patch(args.directory, m.row(bd), "paused", False)
                continue
            break
        if time.monotonic() > deadline:
            raise RuntimeError("Corrected Piwigo BundleDeployment did not stage; keep agent stopped")
        time.sleep(3)
    m.write(args.directory / "staged-piwigo-bundledeployment.json", bd)
    m.log(args.directory, "CONFIRMED corrected Piwigo BundleDeployment staged: " + bd["spec"]["deploymentID"] + "; agent remains stopped; scratch database remains socket-only")
    print("Corrected Piwigo BundleDeployment staged. Other bundles paused; agent remains stopped. No production database started.")


if __name__ == "__main__":
    main()
