#!/usr/bin/env python3
"""Resume GitRepo only after publication, with the agent stopped during staging."""
import argparse
import base64
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import time

spec = importlib.util.spec_from_file_location("finalization", Path(__file__).with_name("finish-restoration.py"))
f = importlib.util.module_from_spec(spec)
spec.loader.exec_module(f)
a, m, s = f.a, f.m, f.s


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True, timeout=60).stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    root = Path(__file__).resolve().parents[3]
    plan = json.loads((args.directory / "execution-plan.json").read_text())
    repo_row = next(x for x in plan["gitrepos"] if x["name"] == "rancher-cluster")
    repo = m.api("-n", repo_row["namespace"], "get", "gitrepo", repo_row["name"], "-o", "json")
    if repo["metadata"]["uid"] != repo_row["uid"] or not repo["spec"].get("paused"):
        raise RuntimeError("Expected the original GitRepo to remain paused before source sync")
    branch = repo["spec"].get("branch", "master")
    if repo["spec"].get("revision"):
        raise RuntimeError("GitRepo pins a revision; inspect publication before resuming")
    commit = git(root, "rev-parse", "HEAD")
    remote = git(root, "ls-remote", "--heads", "origin", "refs/heads/" + branch).split()
    if not remote or remote[0] != commit:
        raise RuntimeError("Local HEAD is not published to origin/" + branch + "; no cluster changes made")
    prefix = root.name + "/fleet/apps/piwigo/"
    chart = git(root, "show", "HEAD:" + prefix + "fleet.yaml")
    values = git(root, "show", "HEAD:" + prefix + "values.yaml")
    if "version: 22.3.2" not in chart or "existingClaim: piwigo-original-copy-longhorn-crypto" not in values:
        raise RuntimeError("Published commit does not contain the validated recovery chart/claim")
    if git(root, "diff", "HEAD", "--", "fleet/apps/piwigo/fleet.yaml", "fleet/apps/piwigo/values.yaml"):
        raise RuntimeError("Recovery config differs from published HEAD; stage/commit/push the intended files first")
    a.validate_application(args.directory)
    f.settle_io(args.directory)
    s.ready()
    agent = next(x for x in plan["workloads"] if x["namespace"] == "cattle-fleet-local-system" and x["name"] == "fleet-agent")
    deployment = m.api("-n", agent["namespace"], "get", "deployment", agent["name"], "-o", "json")
    if deployment["metadata"]["uid"] != agent["uid"]:
        raise RuntimeError("Fleet agent identity changed")
    selector = ",".join(key + "=" + value for key, value in deployment["spec"]["selector"]["matchLabels"].items())
    a.save(args.directory, "gitops-before-source-sync", {"gitrepo": repo, "agent": deployment, "publishedCommit": commit})
    m.patch(args.directory, agent, "replicas", 0)
    deadline = time.monotonic() + 180
    while True:
        live = m.api("-n", agent["namespace"], "get", "deployment", agent["name"], "-o", "json")
        pods = m.api("-n", agent["namespace"], "get", "pods", "-l", selector, "-o", "json")["items"]
        if live["spec"].get("replicas") == 0 and not live.get("status", {}).get("replicas", 0) and not any(p.get("status", {}).get("phase") not in ("Succeeded", "Failed") for p in pods):
            break
        if time.monotonic() > deadline:
            raise RuntimeError("Agent did not stop; GitRepo remains protected")
        time.sleep(3)
    m.log(args.directory, "CONFIRMED Fleet agent stopped before source sync; published commit=" + commit + "; application replicas unchanged")
    m.patch(args.directory, repo_row, "paused", repo_row["paused"])
    m.patch(args.directory, repo_row, "forceSyncGeneration", repo["spec"].get("forceSyncGeneration", 0) + 1)
    staged = json.loads((args.directory / "staged-piwigo-bundledeployment.json").read_text())
    namespace = staged["metadata"]["namespace"]
    try:
        deadline = time.monotonic() + 600
        while True:
            repo_now = m.api("-n", repo_row["namespace"], "get", "gitrepo", repo_row["name"], "-o", "json")
            bundle = m.api("-n", "fleet-local", "get", "bundle", "piwigo", "-o", "json")
            bd = m.api("-n", namespace, "get", "bundledeployment", "piwigo", "-o", "json")
            if repo_now.get("status", {}).get("commit") == commit and bundle["spec"].get("helm", {}).get("version") == "22.3.2" and not bundle["spec"].get("paused") and bd["spec"].get("options", {}).get("helm", {}).get("version") == "22.3.2" and not bd["spec"].get("paused"):
                # Match the repo's established guard for externalized Fleet Helm values.
                secret = m.api("-n", namespace, "get", "secret", "piwigo", "-o", "json")
                actual_values = json.loads(base64.b64decode(secret["data"]["values"]))
                claim = actual_values.get("mariadb", {}).get("persistence", {}).get("data", {}).get("existingClaim")
                if claim == "piwigo-original-copy-longhorn-crypto":
                    break
            if time.monotonic() > deadline:
                raise RuntimeError("Published recovery was not staged correctly; preserve holds and inspect")
            time.sleep(5)
    except Exception:
        m.patch(args.directory, repo_row, "paused", True)
        m.log(args.directory, "SOURCE SYNC NOT VERIFIED; GitRepo re-paused; agent remains stopped; apps retain current data")
        raise
    proof = {"verifiedUTC": m.stamp(), "commit": commit, "gitRepoUID": repo_row["uid"],
             "chart": "22.3.2", "claim": claim, "deploymentID": bd["spec"]["deploymentID"]}
    a.save(args.directory, "gitops-source-verification", proof)
    (args.directory / "gitops-source-verified.json").write_text(json.dumps(proof, indent=2) + "\n")
    m.log(args.directory, "CONFIRMED published recovery source and downstream Fleet values; agent may safely resume " + json.dumps(proof))
    m.patch(args.directory, agent, "replicas", agent["replicas"])
    f.rollout(agent)
    m.release_field(args.directory, agent, "replicas")
    expected = {"metadata": bd["metadata"], "spec": {"deploymentID": bd["spec"]["deploymentID"]}}
    a.wait_and_validate(args.directory, expected)
    print("Published recovery is reconciled. GitRepo and Piwigo pauses are restored; run final read-only verification.")


if __name__ == "__main__":
    main()
