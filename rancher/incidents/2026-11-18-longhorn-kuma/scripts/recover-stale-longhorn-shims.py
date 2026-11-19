#!/usr/bin/env python3
"""Incident-specific guarded cleanup of three stale Longhorn shims. Dry-run by default."""
import argparse, datetime, json, os, pathlib, shutil, signal, socket, subprocess, sys, time
P = pathlib.Path
ALLOWLIST = {"longhorn-manager-n4f65":"fbf58187-d4ee-49d2-b120-64c22e7b9b0d","longhorn-global-manager-6dc48c46cf-9vqx9":"7524b22f-9af8-455f-b775-4b8b072ff302","instance-manager-d3fcafc43c01bde60d0358c3be85ac94":"951c0149-19f4-40e1-a207-603059e8b893"}
INSTANCE = "instance-manager-d3fcafc43c01bde60d0358c3be85ac94"
def run(argv, structured=False):
    r = subprocess.run(argv, capture_output=True, text=True, timeout=25)
    if r.returncode:
        raise RuntimeError(str(argv) + ": " + r.stderr.strip())
    return json.loads(r.stdout) if structured else r.stdout
def read(pid, field):
    return P(f"/proc/{pid}/{field}").read_text()
def start(pid):
    return read(pid, "stat").rsplit(")", 1)[1].split()[19]
def validate(sid, uid, containers):
    tasks = {}
    for line in run(["k3s", "ctr", "-n", "k8s.io", "tasks", "ls"]).splitlines()[1:]:
        f = line.split()
        if len(f) >= 3:
            tasks[f[0]] = (int(f[1]), f[2])
    pause, state = tasks.get(sid, (None, None))
    if state != "RUNNING":
        raise RuntimeError("No known running sandbox task")
    argv = P(f"/proc/{pause}/cmdline").read_bytes().split(b"\0")
    if not argv or P(os.fsdecode(argv[0])).name != "pause":
        raise RuntimeError("Sandbox task is not pause")
    for c in containers:
        if c.get("state") not in ("CONTAINER_UNKNOWN", "CONTAINER_EXITED"):
            raise RuntimeError("Container is live or in unexpected state: " + c["id"])
        if c["id"] in tasks:
            pid, state = tasks[c["id"]]
            if state != "STOPPED" or P(f"/proc/{pid}").exists():
                raise RuntimeError("Container task not STOPPED with absent PID")
    found = []
    for path in P("/proc").glob("[0-9]*/cmdline"):
        try:
            a = path.read_bytes().decode().strip("\0").split("\0")
            if (P(a[0]).name == "containerd-shim-runc-v2"
                and a[a.index("-id")+1] == sid
                and a[a.index("-namespace")+1] == "k8s.io"):
                pid = int(path.parent.name)
                found.append((pid, start(pid)))
        except (OSError, ValueError, IndexError):
            continue
    if len(found) != 1:
        raise RuntimeError("Expected exactly one matching shim")
    shim, born = found[0]
    if read(shim, f"task/{shim}/children").split() != [str(pause)]:
        raise RuntimeError("Shim has children other than pause")
    if read(pause, f"task/{pause}/children").strip():
        raise RuntimeError("Pause has children")
    for path in P("/proc").glob("[0-9]*/cgroup"):
        try:
            cg = path.read_text()
            if uid in cg or uid.replace("-", "_") in cg:
                if int(path.parent.name) != pause:
                    raise RuntimeError("Live process in pod cgroup: " + path.parent.name)
        except (FileNotFoundError, ProcessLookupError):
            pass
    return shim, born, pause
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--yes", action="store_true", help="Explicitly confirm execution without dialog")
    ap.add_argument("--namespace", help="Only longhorn-system is permitted")
    args = ap.parse_args()
    if os.geteuid() != 0 or socket.gethostname().split(".")[0] != "proxmox2-old":
        ap.error("Run as root on proxmox2-old only")
    if args.namespace and args.namespace != "longhorn-system":
        ap.error("Only longhorn-system allowed")
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    folder = P("/var/tmp/stale-longhorn-shims-" + stamp)
    folder.mkdir(mode=0o700)
    log = (folder / "actions.jsonl").open("a")
    def record(event, **fields):
        line = json.dumps(dict(time=datetime.datetime.now(datetime.timezone.utc).isoformat(), event=event, **fields))
        print(line, flush=True)
        log.write(line + "\n")
        log.flush()
    kube = ["kubectl", "--kubeconfig=/etc/rancher/k3s/k3s.yaml"]
    cri = ["k3s", "crictl", "--timeout=15s"]
    pods = run(kube + ["get", "pods", "-A", "--field-selector=spec.nodeName=proxmox2-old", "-o", "json"], True)["items"]
    candidates = [p["metadata"] for p in pods
                  if p["metadata"]["namespace"] == "longhorn-system"
                  and ALLOWLIST.get(p["metadata"]["name"]) == p["metadata"]["uid"]
                  and (p["metadata"].get("deletionTimestamp") or p["metadata"]["name"] == INSTANCE)]
    (folder / "inventory.json").write_text(json.dumps([{k:m.get(k) for k in ("name","namespace","uid","deletionTimestamp")} for m in candidates], indent=2) + "\n")
    record("inventory", path=str(folder), candidates=len(candidates), execute=args.execute)
    if args.execute and not args.yes:
        if not (sys.stdin.isatty() and sys.stdout.isatty() and shutil.which("dialog")):
            ap.error("No interactive dialog; supply --yes only to explicitly confirm execution")
        if subprocess.run(["dialog", "--defaultno", "--yesno", "Recover three incident-specific stale Longhorn shims? Live storage processes and healthy infrastructure are excluded. Logs: " + str(folder), "12", "76"]).returncode:
            record("cancelled")
            return 1
    failed = 0
    for m in candidates:
        ns, name, uid = (m[k] for k in ("namespace", "name", "uid"))
        try:
            current = run(kube + ["get", "pod", name, "-n", ns, "-o", "json"], True)
            if (current["metadata"]["uid"] != uid or ALLOWLIST.get(name) != uid
                or current["spec"]["nodeName"] != "proxmox2-old"
                or (name != INSTANCE and not current["metadata"].get("deletionTimestamp"))):
                raise RuntimeError("Pod identity or termination state changed")
            if any(c.get("ready") for c in current.get("status", {}).get("containerStatuses", [])):
                raise RuntimeError("Pod has a ready container")
            sandboxes = run(cri + ["pods", "-o", "json"], True).get("items", [])
            matches = [s for s in sandboxes if s.get("metadata", {}).get("uid") == uid]
            if len(matches) != 1:
                raise RuntimeError("Expected one sandbox; found " + str(len(matches)))
            sid = matches[0]["id"]
            containers = run(cri + ["ps", "-a", "--pod", sid, "-o", "json"], True).get("containers", [])
            if not containers:
                raise RuntimeError("No container records to validate")
            verified = validate(sid, uid, containers)
            shim, born, pause = verified
            record("eligible", namespace=ns, pod=name, sandbox=sid, shim=shim, pause=pause, containers=[c["id"] for c in containers])
            if not args.execute:
                continue
            if validate(sid, uid, containers) != verified or start(shim) != born:
                raise RuntimeError("Process identity changed")
            # No eviction or PDB override. All storage processes must already be absent.
            current = run(kube + ["get", "pod", name, "-n", ns, "-o", "json"], True)
            if current["metadata"]["uid"] != uid or any(c.get("ready") for c in current.get("status", {}).get("containerStatuses", [])):
                raise RuntimeError("Pod changed or recovered")
            if validate(sid, uid, containers) != verified:
                raise RuntimeError("Process state changed")
            os.kill(shim, signal.SIGTERM)
            record("SIGTERM", namespace=ns, pod=name, shim=shim)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if not P(f"/proc/{shim}").exists() and not P(f"/proc/{pause}").exists():
                    break
                time.sleep(0.5)
            else:
                raise RuntimeError("Shim/pause did not exit; no escalation")
            for c in containers:
                run(cri + ["rm", c["id"]])
                record("container-removed", namespace=ns, pod=name, container=c["id"])
            run(cri + ["stopp", sid])
            run(cri + ["rmp", sid])
            record("runtime-cleaned", namespace=ns, pod=name, note="Verify pod deletion, mounts and attachments separately")
        except Exception as exc:
            failed += 1
            record("skipped-or-failed", namespace=ns, pod=name, reason=str(exc))
    record("finished", skipped_or_failed=failed)
    return 2 if failed else 0
if __name__ == "__main__":
    sys.exit(main())
