#!/usr/bin/env python3
"""Gracefully hand the recovered claim from the isolated database to Fleet."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import time

spec = importlib.util.spec_from_file_location("staging", Path(__file__).with_name("stage-cutover.py"))
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)
d, m, s = stage.d, stage.m, stage.s


def save(directory, name, obj):
    m.write(directory / (name + "-" + str(time.time_ns()) + ".json"), obj)


def require_pauses():
    for resource in ("bundles.fleet.cattle.io", "bundledeployments.fleet.cattle.io"):
        objects = stage.get_all(resource)
        if any(not x["spec"].get("paused") for x in objects if x["metadata"]["name"] != "piwigo"):
            raise RuntimeError("Other Fleet reconciliation is not fully paused")
    if any(not x["spec"].get("paused") for x in stage.get_all("gitrepos.fleet.cattle.io")):
        raise RuntimeError("GitRepo reconciliation is not paused")


def protect_empty_claim(directory):
    claim = m.api("-n", "apps-piwigo", "get", "pvc", "piwigo-mariadb-data", "-o", "json")
    pv = m.api("get", "pv", claim["spec"]["volumeName"], "-o", "json")
    save(directory, "empty-database-before-protection", {"claim": claim, "pv": pv})
    annotations = {**claim["metadata"].get("annotations", {}), "helm.sh/resource-policy": "keep"}
    ops = [{"op": "test", "path": "/metadata/uid", "value": claim["metadata"]["uid"]},
           {"op": "add", "path": "/metadata/annotations", "value": annotations}]
    result = m.api("-n", "apps-piwigo", "patch", "pvc", claim["metadata"]["name"],
                   "--type=json", "-p", json.dumps(ops), "-o", "json")
    if result["metadata"]["annotations"].get("helm.sh/resource-policy") != "keep":
        raise RuntimeError("Empty claim retention annotation not confirmed")
    if pv["spec"].get("persistentVolumeReclaimPolicy") != "Retain":
        ops = [{"op": "test", "path": "/metadata/uid", "value": pv["metadata"]["uid"]},
               {"op": "replace", "path": "/spec/persistentVolumeReclaimPolicy", "value": "Retain"}]
        result = m.api("patch", "pv", pv["metadata"]["name"], "--type=json", "-p", json.dumps(ops), "-o", "json")
        if result["spec"]["persistentVolumeReclaimPolicy"] != "Retain":
            raise RuntimeError("Empty original target PV retention not confirmed")
    m.log(directory, "CONFIRMED superseded empty local-path claim protected with Helm keep and PV Retain; original encrypted source preserved")


def validate_application(directory):
    php = r'''
define("PHPWG_ROOT_PATH", "/app/www/public/");
require "/config/www/local/config/database.inc.php";
$db = new mysqli($conf["db_host"], $conf["db_user"], $conf["db_password"], $conf["db_base"]);
$result = [];
foreach (["images", "categories", "users"] as $name) {
    $table = $prefixeTable.$name;
    $result[$name] = (int)$db->query("SELECT COUNT(*) FROM `".$table."`")->fetch_row()[0];
}
require "/config/www/local/config/config.inc.php";
$result["guestAccessDisabled"] = isset($conf["guest_access"]) && $conf["guest_access"] === false;
echo json_encode($result);
'''
    result = subprocess.run(m.K + ["-n", "apps-piwigo", "exec", "deploy/piwigo", "-c", "piwigo", "--", "php", "-r", php],
                            check=True, capture_output=True, text=True, timeout=40)
    counts = json.loads(result.stdout)
    if counts != {"images": 44, "categories": 2, "users": 2, "guestAccessDisabled": True}:
        raise RuntimeError("Application data/login restriction validation mismatch; inspect private result")
    command = 'curl -sS -L --max-time 20 -o /tmp/recovery-login-check.html -w "%{http_code}" http://127.0.0.1/; php -r \'$s=file_get_contents("/tmp/recovery-login-check.html"); if(stripos($s,"Fatal error")!==false || stripos($s,"mysqli_sql_exception")!==false || !preg_match("/name=[\\\"\\x27]username[\\\"\\x27]/i",$s)) {exit(1);} echo " LOGIN_FORM_CONFIRMED";\''
    response = subprocess.run(m.K + ["-n", "apps-piwigo", "exec", "deploy/piwigo", "-c", "piwigo", "--", "sh", "-ec", command],
                              check=True, capture_output=True, text=True, timeout=40)
    if response.stdout.strip() != "200 LOGIN_FORM_CONFIRMED":
        raise RuntimeError("Anonymous HTTP request did not return the login form")
    pods = m.api("-n", "apps-piwigo", "get", "pods", "-o", "json")["items"]
    database = [x for x in pods if x.get("status", {}).get("phase") == "Running" and
                any(v.get("persistentVolumeClaim", {}).get("claimName") == "piwigo-original-copy-longhorn-crypto" for v in x["spec"].get("volumes", []))]
    if len(database) != 1 or database[0]["spec"].get("nodeName") != "proxmox3":
        raise RuntimeError("Production recovered database mount/node validation failed")
    pvc = m.api("-n", "apps-piwigo", "get", "pvc", "piwigo-original-copy-longhorn-crypto", "-o", "json")
    volume = s.get("volumes.longhorn.io", pvc["spec"]["volumeName"])
    if not volume["spec"].get("encrypted") or volume["spec"].get("numberOfReplicas") != 1 or volume["status"].get("robustness") != "healthy" or volume["status"].get("currentNodeID") != "proxmox3":
        raise RuntimeError("Recovered production volume not encrypted/healthy with authorized one replica on proxmox3")
    endpoints = m.api("-n", "apps-piwigo", "get", "endpointslices", "-l", "kubernetes.io/service-name=piwigo", "-o", "json")
    if not any(e.get("conditions", {}).get("ready") and e.get("addresses") for x in endpoints["items"] for e in x.get("endpoints", [])):
        raise RuntimeError("Piwigo has no ready service endpoints")
    # Test the ingress Host/SNI on the active node without depending on host DNS.
    host = "cycuszki.rancher.tailscale.bnowakowski.pl"
    ingress = subprocess.run(["curl", "--silent", "--show-error", "--location", "--max-time", "20",
                              "--resolve", host + ":443:10.0.0.50", "--output", "/dev/null", "--write-out", "%{http_code}", "https://" + host + "/"],
                             capture_output=True, text=True, timeout=30)
    report = {"counts": counts, "localHttp": response.stdout.strip(), "ingressReturncode": ingress.returncode,
              "ingressHttp": ingress.stdout.strip(), "ingressError": ingress.stderr,
              "databasePod": database[0]["metadata"]["name"], "databasePVC": pvc["metadata"]["name"],
              "encrypted": True, "temporaryReplicas": 1, "checkedUTC": m.stamp()}
    save(directory, "piwigo-validation", report)
    if ingress.returncode or ingress.stdout.strip() != "200":
        raise RuntimeError("Piwigo local/data checks passed but ingress validation failed; inspect private validation report")
    if not (directory / "piwigo-validated.json").exists():
        m.write(directory / "piwigo-validated.json", report)
    m.log(directory, "CONFIRMED Piwigo data 44 images/2 categories/2 users; guest access disabled; login page HTTP200; ingress HTTP200; correct encrypted recovered claim healthy on proxmox3")
    print(json.dumps(report, indent=2))


def wait_and_validate(directory, expected):
    deadline = time.monotonic() + 600
    last_status = None
    while True:
        bd = m.api("-n", expected["metadata"]["namespace"], "get", "bundledeployment", "piwigo", "-o", "json")
        ready = any(c["type"] == "Ready" and c["status"] == "True" for c in bd.get("status", {}).get("conditions", []))
        if bd.get("status", {}).get("appliedDeploymentID") == expected["spec"]["deploymentID"] and ready:
            break
        status = (bd.get("status", {}).get("appliedDeploymentID"), ready, bd.get("status", {}).get("display"))
        if status != last_status:
            m.log(directory, "CUTOVER PROGRESS " + json.dumps(status))
            last_status = status
        if time.monotonic() >= deadline:
            save(directory, "piwigo-cutover-timeout", bd)
            raise RuntimeError("Piwigo Fleet rollout not ready; preserve target ownership; do not restart original source")
        time.sleep(5)
    save(directory, "piwigo-applied-bundledeployment", bd)
    validate_application(directory)
    print("Piwigo validated. Other applications and schedules remain stopped for gradual restoration.")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--resume", action="store_true", help="Resume only rollout waiting and validation after handoff")
    parser.add_argument("--validate-only", action="store_true", help="Repeat read-only application validation after a cutover")
    args = parser.parse_args()
    os.umask(0o077)
    if args.validate_only:
        validate_application(args.directory)
        return
    plan = json.loads((args.directory / "execution-plan.json").read_text())
    expected = json.loads((args.directory / "staged-piwigo-bundledeployment.json").read_text())
    live = m.api("-n", expected["metadata"]["namespace"], "get", "bundledeployment", "piwigo", "-o", "json")
    if live["metadata"]["uid"] != expected["metadata"]["uid"] or live["spec"]["deploymentID"] != expected["spec"]["deploymentID"] or live["spec"].get("paused"):
        raise RuntimeError("Staged Piwigo deployment identity/options changed")
    bundle = m.api("-n", "fleet-local", "get", "bundle", "piwigo", "-o", "json")
    stage.validate_bundle(bundle)
    require_pauses()
    if args.resume:
        wait_and_validate(args.directory, expected)
        return
    agent = next(x for x in plan["workloads"] if x["namespace"] == "cattle-fleet-local-system" and x["name"] == "fleet-agent")
    agent_live = m.api("-n", agent["namespace"], "get", "deployment", agent["name"], "-o", "json")
    if agent_live["metadata"]["uid"] != agent["uid"] or agent_live["spec"].get("replicas") != 0 or agent_live.get("status", {}).get("replicas", 0):
        raise RuntimeError("Agent is not confirmed stopped")
    pod = m.api("-n", "apps-piwigo", "get", "pod", d.POD, "-o", "json")
    if pod["spec"].get("nodeName") != "proxmox3" or pod["spec"]["containers"][0]["image"] != d.IMAGE:
        raise RuntimeError("Scratch recovery database identity changed")
    for kind in ("deployments", "statefulsets"):
        for obj in m.api("get", kind, "-A", "-o", "json")["items"]:
            if m.stopped_namespace(obj["metadata"]["namespace"]) and (obj["spec"].get("replicas", 1) or obj.get("status", {}).get("replicas", 0)):
                raise RuntimeError("Application workloads are no longer stopped")
    for obj in m.api("get", "pods", "-A", "-o", "json")["items"]:
        if m.stopped_namespace(obj["metadata"]["namespace"]) and obj["metadata"]["uid"] != pod["metadata"]["uid"] and obj.get("status", {}).get("phase") not in ("Succeeded", "Failed"):
            raise RuntimeError("Another application pod remains; no concurrent production start authorized")
    if any(x.get("status", {}).get("active", 0) for x in stage.get_all("jobs")):
        raise RuntimeError("Active jobs exist before cutover")
    if s.get("volumes.longhorn.io", s.SOURCE)["status"].get("state") != "detached":
        raise RuntimeError("Original source is not detached")
    m.low_io()
    s.ready()
    before = s.k3s()
    time.sleep(10)
    s.ready()
    if before != s.k3s():
        raise RuntimeError("k3s state/restarts changed before cutover")
    protect_empty_claim(args.directory)
    if pod["status"].get("phase") != "Succeeded":
        # Use the exact client/socket combination proven by the table checks.
        result = d.exec_db(["/opt/bitnami/mariadb/bin/mariadb", "--no-defaults", "--socket=/tmp/recovery.sock", "-uroot", "-e", "SHUTDOWN;"])
        save(args.directory, "recovery-shutdown-result", {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr})
        if result.returncode:
            time.sleep(3)
            current = m.api("-n", "apps-piwigo", "get", "pod", d.POD, "-o", "json")
            if current["status"].get("phase") != "Succeeded":
                raise RuntimeError("SQL shutdown failed; private recovery-shutdown-result diagnostic saved; agent remains stopped")
    deadline = time.monotonic() + 90
    while True:
        pod_live = m.api("-n", "apps-piwigo", "get", "pod", d.POD, "-o", "json")
        if pod_live["status"].get("phase") == "Succeeded":
            break
        if time.monotonic() > deadline:
            raise RuntimeError("Recovery database did not exit cleanly")
        time.sleep(3)
    delete = {"apiVersion": "v1", "kind": "DeleteOptions", "preconditions": {"uid": pod["metadata"]["uid"]}}
    subprocess.run(m.K + ["delete", "--raw", "/api/v1/namespaces/apps-piwigo/pods/" + d.POD, "-f", "-"],
                   input=json.dumps(delete), capture_output=True, text=True, check=True, timeout=40)
    subprocess.run(m.K + ["-n", "apps-piwigo", "wait", "--for=delete", "pod/" + d.POD, "--timeout=120s"], check=True, timeout=140)
    m.log(args.directory, "CONFIRMED isolated scratch database shut down cleanly and helper removed; production claim handed off without concurrent writers")
    require_pauses()
    m.low_io()
    m.patch(args.directory, agent, "replicas", agent["replicas"])
    m.log(args.directory, "CUTOVER START Fleet agent resumed for corrected Piwigo only; other bundles paused; source and superseded empty claim retained")
    wait_and_validate(args.directory, expected)


if __name__ == "__main__":
    main()
