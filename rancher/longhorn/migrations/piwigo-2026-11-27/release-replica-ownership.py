#!/usr/bin/env python3
"""Relinquish only maintenance ownership of Piwigo's replica field to Fleet."""
import argparse
import copy
import importlib.util
import json
import os
from pathlib import Path
import time

spec = importlib.util.spec_from_file_location("activation", Path(__file__).with_name("activate-piwigo.py"))
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)
m = a.m


def relinquish(entries):
    result = []
    changed = 0
    for original in entries:
        entry = copy.deepcopy(original)
        fields = entry.get("fieldsV1", {})
        spec_fields = fields.get("f:spec", {})
        if entry.get("manager") == "kubectl-patch" and "f:replicas" in spec_fields:
            del spec_fields["f:replicas"]
            if not spec_fields:
                del fields["f:spec"]
            changed += 1
            if not fields:
                continue
        result.append(entry)
    return result, changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    a.require_pauses()
    a.stage.validate_bundle(m.api("-n", "fleet-local", "get", "bundle", "piwigo", "-o", "json"))
    plan = json.loads((args.directory / "execution-plan.json").read_text())
    expected = next(x for x in plan["workloads"] if x["namespace"] == "apps-piwigo" and x["name"] == "piwigo")
    obj = m.api("-n", "apps-piwigo", "get", "deployment", "piwigo", "--show-managed-fields=true", "-o", "json")
    if obj["metadata"]["uid"] != expected["uid"]:
        raise RuntimeError("Piwigo deployment identity changed; do not edit ownership")
    saved = args.directory / ("piwigo-before-replica-handoff-" + str(time.time_ns()) + ".json")
    m.write(saved, obj)
    updated, changed = relinquish(obj["metadata"].get("managedFields", []))
    if not changed:
        print("No kubectl-patch replica ownership remains; no mutation performed.")
        return
    ops = [{"op": "test", "path": "/metadata/resourceVersion", "value": obj["metadata"]["resourceVersion"]},
           {"op": "replace", "path": "/metadata/managedFields", "value": updated}]
    m.log(args.directory, "REQUEST relinquish kubectl-patch ownership of apps-piwigo/piwigo spec.replicas only; preserve all other field ownership and live spec")
    result = m.api("-n", "apps-piwigo", "patch", "deployment", "piwigo", "--type=json", "-p", json.dumps(ops), "-o", "json")
    if result["spec"] != obj["spec"]:
        raise RuntimeError("Live deployment spec changed concurrently; inspect saved baseline")
    live = m.api("-n", "apps-piwigo", "get", "deployment", "piwigo", "--show-managed-fields=true", "-o", "json")
    if relinquish(live["metadata"].get("managedFields", []))[1]:
        raise RuntimeError("Replica ownership handoff not confirmed")
    m.log(args.directory, "CONFIRMED only maintenance replica ownership relinquished; deployment spec unchanged; Fleet may retry corrected deployment")
    print("Maintenance replica ownership released. Fleet can retry; keep the activation monitor running.")


if __name__ == "__main__":
    main()
