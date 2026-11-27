#!/usr/bin/env python3
"""Start the original pinned database against scratch only, socket access only."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import time

spec = importlib.util.spec_from_file_location("inspection", Path(__file__).with_name("inspect-source.py"))
i = importlib.util.module_from_spec(spec)
spec.loader.exec_module(i)
m, s = i.m, i.s
IMAGE = "docker.io/bitnamisecure/mariadb:latest@sha256:687181819d6e3f04124a21aec1aeef1d8a5d6a924c0d8e8f0cf66b8f0ce15a7c"
POD = "piwigo-recovery-database"


def exec_db(command):
    return subprocess.run(m.K + ["-n", "apps-piwigo", "exec", POD, "--"] + command,
                          capture_output=True, text=True, timeout=40)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    events = [json.loads(line) for line in (args.directory / "copy-worker.log").read_text().splitlines() if line.startswith("{")]
    if not events or events[-1].get("event") != "COMPLETE" or not events[-1].get("allFilesSHA256Verified"):
        raise RuntimeError("Protected copy has not completed verification")
    old = m.api("-n", "apps-piwigo", "get", "pod", "piwigo-recovery-copy", "-o", "json")
    if old["spec"].get("nodeName") != "proxmox3" or old["spec"]["containers"][0]["image"] != i.IMAGE or old["spec"]["containers"][0]["command"] != ["/bin/sh", "-c", "exec sleep infinity"]:
        raise RuntimeError("Copy helper identity changed")
    plan = json.loads((args.directory / "execution-plan.json").read_text())
    remaining = m.verify(plan, allowed_pod_uids=(old["metadata"]["uid"],))
    if remaining:
        raise RuntimeError("Shutdown changed: " + ", ".join(remaining))
    manifest = subprocess.run(m.K + ["-n", "apps-piwigo", "exec", "piwigo-recovery-copy", "--", "cat", "/scratch/original-copy-complete.json"],
                              check=True, capture_output=True, text=True, timeout=40)
    complete = json.loads(manifest.stdout)
    if complete["fileCount"] != events[-1]["fileCount"]:
        raise RuntimeError("Scratch completion manifest mismatch")
    m.write(args.directory / ("protected-copy-manifest-" + str(time.time_ns()) + ".json"), complete)
    m.low_io()
    s.ready()
    before = s.k3s()
    time.sleep(10)
    s.ready()
    if before != s.k3s():
        raise RuntimeError("k3s state/restarts changed")
    delete = {"apiVersion": "v1", "kind": "DeleteOptions", "preconditions": {"uid": old["metadata"]["uid"]}}
    subprocess.run(m.K + ["delete", "--raw", "/api/v1/namespaces/apps-piwigo/pods/piwigo-recovery-copy", "-f", "-"],
                   input=json.dumps(delete), capture_output=True, text=True, check=True, timeout=40)
    subprocess.run(m.K + ["-n", "apps-piwigo", "wait", "--for=delete", "pod/piwigo-recovery-copy", "--timeout=120s"],
                   check=True, timeout=140)
    deadline = time.monotonic() + 120
    while s.get("volumes.longhorn.io", s.SOURCE)["status"].get("state") != "detached":
        if time.monotonic() > deadline:
            raise RuntimeError("Original source did not detach; inspect, do not force")
        time.sleep(5)
    m.log(args.directory, "CONFIRMED copier removed; original source detached; scratch copy retained; original remains unmodified")
    pod = {"apiVersion": "v1", "kind": "Pod",
           "metadata": {"name": POD, "namespace": "apps-piwigo", "labels": {"recovery.homelab/purpose": "piwigo-database-check"}},
           "spec": {"nodeSelector": {"kubernetes.io/hostname": "proxmox3"}, "automountServiceAccountToken": False,
                    "restartPolicy": "Never", "terminationGracePeriodSeconds": 90,
                    "securityContext": {"runAsUser": 568, "runAsGroup": 568, "fsGroup": 568},
                    "containers": [{"name": "database", "image": IMAGE, "imagePullPolicy": "IfNotPresent",
                                    "command": ["/opt/bitnami/mariadb/sbin/mariadbd"],
                                    "args": ["--no-defaults", "--datadir=/bitnami/mariadb/data", "--socket=/tmp/recovery.sock",
                                             "--pid-file=/tmp/recovery.pid", "--skip-networking", "--skip-grant-tables",
                                             "--skip-log-bin", "--innodb-buffer-pool-size=64M", "--max-connections=5",
                                             "--tmpdir=/tmp"],
                                    "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                                                        "capabilities": {"drop": ["ALL"]}},
                                    "resources": {"requests": {"cpu": "50m", "memory": "128Mi"},
                                                  "limits": {"cpu": "1000m", "memory": "512Mi"}},
                                    "volumeMounts": [{"name": "scratch", "mountPath": "/bitnami/mariadb"},
                                                     {"name": "tmp", "mountPath": "/tmp"}]}],
                    "volumes": [{"name": "scratch", "persistentVolumeClaim": {"claimName": "piwigo-original-copy-longhorn-crypto"}},
                                {"name": "tmp", "emptyDir": {"medium": "Memory", "sizeLimit": "128Mi"}}]}}
    m.write(args.directory / ("database-check-pod-" + str(time.time_ns()) + ".json"), pod)
    m.low_io()
    i.create(pod)
    m.log(args.directory, "CONFIRMED original-image database pod created on proxmox3; only scratch mounted; skip-networking and local socket-only grant bypass; no Service")
    subprocess.run(m.K + ["-n", "apps-piwigo", "wait", "--for=condition=Ready", "pod/" + POD, "--timeout=180s"],
                   check=True, timeout=200)
    deadline = time.monotonic() + 120
    while True:
        result = exec_db(["/opt/bitnami/mariadb/bin/mariadb", "--no-defaults", "--socket=/tmp/recovery.sock", "-uroot", "-N", "-e", "SELECT VERSION();"])
        if result.returncode == 0:
            break
        if time.monotonic() > deadline:
            m.log(args.directory, "DATABASE CHECK FAILED to reach scratch socket; preserve pod logs and all PVCs")
            raise RuntimeError("Scratch database socket unavailable; inspect recovery database pod logs")
        time.sleep(3)
    sql = "SELECT VERSION() AS server_version; SELECT COUNT(*) AS piwigo_table_count FROM information_schema.tables WHERE table_schema='piwigo'; SELECT COUNT(*) AS config_rows FROM piwigo.piwigo_config; SELECT COUNT(*) AS images FROM piwigo.piwigo_images; SELECT COUNT(*) AS categories FROM piwigo.piwigo_categories; SELECT COUNT(*) AS users FROM piwigo.piwigo_users;"
    result = exec_db(["/opt/bitnami/mariadb/bin/mariadb", "--no-defaults", "--socket=/tmp/recovery.sock", "-uroot", "--batch", "-e", sql])
    m.write(args.directory / ("database-check-result-" + str(time.time_ns()) + ".json"),
            {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr})
    if result.returncode:
        m.log(args.directory, "DATABASE CHECK FAILED; counts unavailable; preserve source and scratch")
        raise RuntimeError("Required Piwigo table checks failed; inspect saved result and pod logs")
    print(result.stdout)
    m.log(args.directory, "CONFIRMED original-image scratch MariaDB started with local socket only; required Piwigo tables queried successfully; counts saved privately")
    print("Recovered tables accessible on scratch. Production target remains empty; applications remain stopped.")


if __name__ == "__main__":
    main()
