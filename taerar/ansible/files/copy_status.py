#!/usr/bin/env python3
"""Record which copies from the Time Machine backup have been done.

State lives in ~/.homelab-provision-state/copy-status.json (mode 0600), one entry
per copy step: apps, user-files, private-data, system-settings, dia,
dia-extensions and folder:<Name>. Each entry has a state (started, done, failed,
skipped), the time it was last updated, and details such as the backup (bundle and
snapshot) and counts. "done" means the step finished without error, even when it
had nothing new to copy. A step that was started and never finished stays
"started" (for example after Ctrl-C). Nothing here contains file contents.

Usage:
  copy_status.py show                       print a table
  copy_status.py get STEP                   print the state (nothing if unknown)
  copy_status.py set STEP STATE [KEY=VALUE ...]
  copy_status.py defaults BUNDLE MODE STEP=on|off ...
                                            print "<STEP>|on|off|<status text>" per step: "off" only when the
                                            copy finished for that bundle and mode and no later run was
                                            interrupted or failed; interrupted/failed copies are "on";
                                            never-copied steps use the given default
  copy_status.py done STEP BUNDLE [MODE]    print "<backup>|<completed>" when STEP finished
                                            before for that bundle (and mode), even if a later
                                            run was interrupted or failed; else nothing
Importable: record(step, state, **detail), read_status().
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

STATES = ("started", "done", "failed", "skipped")


def status_file():
    state_dir = Path(os.environ.get("HOMELAB_STATE_DIR") or Path.home() / ".homelab-provision-state")
    return state_dir / "copy-status.json"


def read_status():
    try:
        data = json.loads(status_file().read_text())
    except (OSError, ValueError):
        return {}
    return data.get("steps", {}) if isinstance(data, dict) else {}


def record(step, state, **detail):
    if state not in STATES:
        raise ValueError(f"unknown state {state}")
    steps = read_status()
    entry = steps.get(step, {})
    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    entry.update({key: value for key, value in detail.items() if value is not None})
    entry["state"] = state
    entry["updated"] = now
    if state == "started":
        entry["started"] = now
        # A restarted step is not complete now, but its last finish stays known so an
        # interrupted rerun does not hide that the copy was done before.
        if entry.get("completed"):
            entry["last_completed"] = entry.pop("completed")
            entry["last_backup"] = entry.get("backup")
    elif state == "done":
        entry["completed"] = now
    steps[step] = entry
    path = status_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".copy-status.")
    try:
        with os.fdopen(handle, "w") as stream:
            json.dump({"schema_version": 1, "steps": steps}, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def show():
    steps = read_status()
    if not steps:
        print("No copy from a Time Machine backup has been recorded yet.")
        return
    width = max(len(step) for step in steps)
    for step in sorted(steps):
        entry = steps[step]
        extra = []
        for key in ("backup", "copied", "skipped_existing", "reason", "error"):
            if key in entry:
                extra.append(f"{key}={entry[key]}")
        print(f"{step:<{width}}  {entry.get('state', '?'):<8}  {entry.get('updated', '')}  {' '.join(extra)}")


def describe(entry, bundle, mode, default):
    """(checked by default, status text) for one copy step."""
    when = (entry or {}).get("updated", "")[:16].replace("T", " ")
    state = (entry or {}).get("state")
    if state == "started":
        return True, f"interrupted {when}, copy again"
    if state == "failed":
        return True, f"failed {when}, copy again"
    if state == "done":
        if entry.get("bundle") == bundle and entry.get("mode") == mode:
            return False, f"copied OK {when}"
        return default, f"copied from another backup {when}"
    if state == "skipped":
        return default, f"skipped last time {when}"
    return default, "not copied yet"


def defaults(bundle, mode, steps):
    status = read_status()
    for item in steps:
        step, _, default = item.rpartition("=")
        checked, text = describe(status.get(step), bundle, mode, default != "off")
        print(f"{step}|{'on' if checked else 'off'}|{text}")


def main(argv):
    if argv[:1] == ["show"]:
        show()
    elif argv[:1] == ["defaults"] and len(argv) >= 4:
        defaults(argv[1], argv[2], argv[3:])
    elif argv[:1] == ["get"] and len(argv) == 2:
        entry = read_status().get(argv[1])
        if entry:
            print(entry.get("state", ""))
    elif argv[:1] == ["done"] and len(argv) in (3, 4):
        entry = read_status().get(argv[1]) or {}
        finished = entry.get("completed") or entry.get("last_completed")
        if (finished and entry.get("state") != "skipped" and entry.get("bundle") == argv[2]
                and (len(argv) == 3 or entry.get("mode") == argv[3])):
            backup = entry.get("backup") if entry.get("completed") else entry.get("last_backup")
            print(f"{backup or '?'}|{finished}")
    elif argv[:1] == ["set"] and len(argv) >= 3:
        detail = dict(item.split("=", 1) for item in argv[3:] if "=" in item)
        record(argv[1], argv[2], **detail)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
