#!/usr/bin/env python3
"""Mount the source read-only without ext4 journal replay, on proxmox3 only."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time

spec = importlib.util.spec_from_file_location("source_access", Path(__file__).with_name("source-access.py"))
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)
m = s.m
IMAGE = "ghcr.io/linuxserver/piwigo:16.5.0@sha256:ec4757c74d66a49a292bf0551d4bfbcf20dc5d296aeda8a462957f6e4080f5f2"


def create(obj):
    return json.loads(subprocess.run(m.K + ["create", "-f", "-", "-o", "json"],
                      input=json.dumps(obj), capture_output=True, text=True,
                      check=True, timeout=40).stdout)


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    s.guards(args.directory)
    volume = s.get("volumes.longhorn.io", s.SOURCE)
    if volume["status"].get("state") != "detached" or volume["spec"].get("nodeID"):
        raise RuntimeError("Source is not detached")
    pv = m.api("get", "pv", s.SOURCE, "-o", "json")
    if pv["spec"].get("csi", {}).get("fsType") != "ext4":
        raise RuntimeError("Expected ext4 source before applying noload")
    if pv["spec"].get("claimRef", {}).get("name") != s.CLAIM:
        raise RuntimeError("Source PV claim identity changed")
    saved = args.directory / "source-pv-before-inspection.json"
    if not saved.exists():
        m.write(saved, pv)
    options = list(pv["spec"].get("mountOptions", []))
    if any(x in options for x in ("rw", "load", "recovery")):
        raise RuntimeError("Conflicting source mount options")
    desired = list(dict.fromkeys(options + ["ro", "noload"]))
    ops = [{"op": "test", "path": "/metadata/resourceVersion", "value": pv["metadata"]["resourceVersion"]},
           {"op": "add", "path": "/spec/mountOptions", "value": desired}]
    m.log(args.directory, "REQUEST source PV mountOptions=" + json.dumps(desired))
    result = m.api("patch", "pv", s.SOURCE, "--type=json", "-p", json.dumps(ops), "-o", "json")
    if result["spec"].get("mountOptions") != desired:
        raise RuntimeError("Read-only/noload source mount options not confirmed")
    m.log(args.directory, "CONFIRMED source PV read-only/noload options; original options saved privately")
    scratch = {"apiVersion": "v1", "kind": "PersistentVolumeClaim",
               "metadata": {"name": "piwigo-original-copy-longhorn-crypto", "namespace": "apps-piwigo",
                            "annotations": {"recovery.homelab/replica-exception": "temporary-single-replica-authorized-2026-11-27"}},
               "spec": {"accessModes": ["ReadWriteOnce"], "storageClassName": "piwigo-recovery-crypto-temporary",
                        "resources": {"requests": {"storage": "2Gi"}}}}
    create(scratch)
    m.log(args.directory, "CONFIRMED encrypted scratch PVC created; no database data copied")
    pod = {"apiVersion": "v1", "kind": "Pod",
           "metadata": {"name": "piwigo-recovery-inspect", "namespace": "apps-piwigo",
                        "labels": {"recovery.homelab/purpose": "piwigo-inspection"}},
           "spec": {"nodeSelector": {"kubernetes.io/hostname": "proxmox3"},
                    "automountServiceAccountToken": False, "restartPolicy": "Never",
                    "containers": [{"name": "inspect", "image": IMAGE,
                                    "command": ["/bin/sh", "-c", "exec sleep infinity"],
                                    "securityContext": {"runAsUser": 0, "allowPrivilegeEscalation": False,
                                                        "readOnlyRootFilesystem": True,
                                                        "capabilities": {"drop": ["ALL"]}},
                                    "resources": {"requests": {"cpu": "10m", "memory": "32Mi"},
                                                  "limits": {"cpu": "500m", "memory": "256Mi"}},
                                    "volumeMounts": [{"name": "source", "mountPath": "/source", "readOnly": True},
                                                     {"name": "scratch", "mountPath": "/scratch"},
                                                     {"name": "tmp", "mountPath": "/tmp"}]}],
                    "volumes": [{"name": "source", "persistentVolumeClaim": {"claimName": s.CLAIM, "readOnly": True}},
                                {"name": "scratch", "persistentVolumeClaim": {"claimName": "piwigo-original-copy-longhorn-crypto"}},
                                {"name": "tmp", "emptyDir": {"medium": "Memory", "sizeLimit": "64Mi"}}]}}
    m.write(args.directory / ("inspect-pod-" + str(time.time_ns()) + ".json"), pod)
    create(pod)
    m.log(args.directory, "CONFIRMED inspection pod created, pinned proxmox3; source read-only; command sleep only")
    subprocess.run(m.K + ["-n", "apps-piwigo", "wait", "--for=condition=Ready", "pod/piwigo-recovery-inspect", "--timeout=180s"],
                   check=True, timeout=200)
    # Metadata only: no credential files or database contents printed.
    command = "grep -E ' /source | /scratch ' /proc/mounts; printf '\\nTOOLS\\n'; command -v python3 || true; command -v php || true; command -v tar || true; command -v pv || true; printf '\\nSOURCE TOP LEVEL\\n'; ls -lan /source; printf '\\nDATA DIRECTORY\\n'; ls -ldn /source/data /source/mysql /source/data/piwigo /source/piwigo 2>/dev/null || true"
    result = subprocess.run(m.K + ["-n", "apps-piwigo", "exec", "piwigo-recovery-inspect", "--", "sh", "-c", command],
                            check=True, capture_output=True, text=True, timeout=40)
    m.write(args.directory / ("inspect-result-" + str(time.time_ns()) + ".json"), {"stdout": result.stdout})
    source_line = next((line for line in result.stdout.splitlines() if " /source " in line), "")
    if not source_line or "ro" not in source_line.split()[3].split(",") or "noload" not in source_line.split()[3].split(",") and "norecovery" not in source_line.split()[3].split(","):
        raise RuntimeError("Live source mount did not confirm ro/noload; no copying authorized")
    m.log(args.directory, "CONFIRMED live source mount read-only with journal replay disabled; metadata inspection saved; no copy or database startup")
    print(result.stdout)


if __name__ == "__main__":
    main()
