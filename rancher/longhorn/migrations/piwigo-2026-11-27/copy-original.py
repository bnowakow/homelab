#!/usr/bin/env python3
"""Copy and checksum the read-only original database into encrypted scratch."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import time

spec = importlib.util.spec_from_file_location("inspection", Path(__file__).with_name("inspect-source.py"))
inspection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inspection)
m = inspection.m
s = inspection.s


def append(path, value):
    with path.open("a") as f:
        f.write(json.dumps(value) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    state = args.directory / "execution-plan.json"
    data = json.loads((state if state.exists() else args.directory / "plan.json").read_text())
    old = m.api("-n", "apps-piwigo", "get", "pod", "piwigo-recovery-inspect", "-o", "json")
    container = old["spec"]["containers"][0]
    if old["spec"].get("nodeName") != "proxmox3" or container["image"] != inspection.IMAGE or container["command"] != ["/bin/sh", "-c", "exec sleep infinity"]:
        raise RuntimeError("Inspection pod identity/specification changed")
    worker = Path(__file__).with_name("copy-original.php")
    with worker.open() as code:
        subprocess.run(m.K + ["-n", "apps-piwigo", "exec", "-i", "piwigo-recovery-inspect", "--", "php", "-l"],
                       stdin=code, check=True, timeout=40)
    problems = m.verify(data, allowed_pod_uids=(old["metadata"]["uid"],))
    if problems:
        raise RuntimeError("Application shutdown no longer verified: " + ", ".join(problems))
    if any(x.get("status", {}).get("active", 0) for x in m.api("get", "jobs", "-A", "-o", "json")["items"]):
        raise RuntimeError("Active jobs exist")
    target = m.api("-n", "apps-piwigo", "get", "pvc", "piwigo-original-copy-longhorn-crypto", "-o", "json")
    source = s.get("volumes.longhorn.io", s.SOURCE)
    scratch = s.get("volumes.longhorn.io", target["spec"]["volumeName"])
    for name, volume in (("source", source), ("scratch", scratch)):
        if not volume["spec"].get("encrypted") or volume["status"].get("state") != "attached" or volume["status"].get("currentNodeID") != "proxmox3":
            raise RuntimeError(name + " not confirmed encrypted/attached on proxmox3")
    if scratch["spec"]["numberOfReplicas"] != 1 or scratch["status"].get("robustness") != "healthy":
        raise RuntimeError("Temporary one-replica scratch not healthy")
    engines = s.get("engines.longhorn.io")["items"]
    for volume in (source, scratch):
        engine = next((x for x in engines if x["spec"].get("volumeName") == volume["metadata"]["name"] and x["spec"].get("active")), None)
        if not engine or engine["status"].get("currentState") != "running" or not engine["status"].get("replicaModeMap") or any(mode != "RW" for mode in engine["status"]["replicaModeMap"].values()):
            raise RuntimeError("Source/scratch engine does not have running RW replicas")
    m.low_io()
    s.ready()
    before = s.k3s()
    time.sleep(10)
    s.ready()
    if before != s.k3s():
        raise RuntimeError("k3s state/restarts changed")
    m.low_io()
    m.write(args.directory / ("before-copy-" + str(time.time_ns()) + ".json"),
            {"source": source, "scratch": scratch, "engines": engines, "k3s": before})
    # Replace only the sleep-only helper, with the minimum file-copy capabilities.
    pod = {"apiVersion": "v1", "kind": "Pod",
           "metadata": {"name": "piwigo-recovery-copy", "namespace": "apps-piwigo",
                        "labels": {"recovery.homelab/purpose": "piwigo-original-copy"}},
           "spec": {key: old["spec"][key] for key in ("nodeSelector", "automountServiceAccountToken", "restartPolicy", "containers", "volumes")}}
    pod["spec"]["containers"][0]["securityContext"]["capabilities"] = {
        "drop": ["ALL"], "add": ["DAC_OVERRIDE", "CHOWN", "FOWNER", "FSETID"]}
    delete = {"apiVersion": "v1", "kind": "DeleteOptions", "preconditions": {"uid": old["metadata"]["uid"]}}
    # kubectl delete --raw supports UID-preconditioned DeleteOptions without name races.
    subprocess.run(m.K + ["delete", "--raw", "/api/v1/namespaces/apps-piwigo/pods/piwigo-recovery-inspect", "-f", "-"],
                   input=json.dumps(delete), capture_output=True, text=True, check=True, timeout=40)
    subprocess.run(m.K + ["-n", "apps-piwigo", "wait", "--for=delete", "pod/piwigo-recovery-inspect", "--timeout=120s"],
                   check=True, timeout=140)
    inspection.create(pod)
    subprocess.run(m.K + ["-n", "apps-piwigo", "wait", "--for=condition=Ready", "pod/piwigo-recovery-copy", "--timeout=180s"],
                   check=True, timeout=200)
    live = m.api("-n", "apps-piwigo", "get", "pod", "piwigo-recovery-copy", "-o", "json")
    if live["spec"].get("nodeName") != "proxmox3":
        raise RuntimeError("Copy pod is not on proxmox3")
    problems = m.verify(data, allowed_pod_uids=(live["metadata"]["uid"],))
    if problems:
        raise RuntimeError("Shutdown changed before transfer: " + ", ".join(problems))
    m.low_io()
    m.log(args.directory, "COPY REQUEST original read-only -> encrypted scratch; requested read cap 1048576 bytes/s including checksum verification; worker confirmation pending")
    stream_k = [x for x in m.K if not x.startswith("--request-timeout=")] + ["--request-timeout=0"]
    with worker.open() as code:
        process = subprocess.Popen(stream_k + ["-n", "apps-piwigo", "exec", "-i", "piwigo-recovery-copy", "--", "php"],
                                   stdin=code, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        complete = False
        for line in process.stdout:
            print(line, end="", flush=True)
            with (args.directory / "copy-worker.log").open("a") as log:
                log.write(line)
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event") in ("CAP_REQUEST", "CAP_CONFIRMED"):
                append(args.directory / "cap-changes.jsonl", event)
                m.log(args.directory, "COPY " + json.dumps(event))
            elif event.get("event") in ("START", "COMPLETE", "FAILED"):
                m.log(args.directory, "COPY " + json.dumps(event))
                complete = event["event"] == "COMPLETE"
        rc = process.wait()
    if rc or not complete:
        raise RuntimeError("Copy incomplete; preserve partial scratch, keep applications stopped, inspect copy-worker.log")
    m.log(args.directory, "COPY OUTCOME all copied database files SHA256 verified; original remains read-only; no MariaDB process started")
    append(args.directory / "cap-changes.jsonl", {"time": m.stamp(), "event": "OUTCOME", "result": "All copied database files SHA256 verified; worker exited; no active transfer cap remains"})
    print("Protected scratch copy verified. No database has been started; applications remain stopped.")


if __name__ == "__main__":
    main()
