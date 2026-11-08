#!/usr/bin/env python3
"""Install the applications listed in apps.json (Homebrew casks, App Store apps).

usage: install_apps.py CATALOG STATE_DIR ENV TTY

Apps already present in /Applications or ~/Applications (or installed as a cask
or App Store app) are skipped. Casks and their missing dependencies are recorded
in STATE_DIR before installing so undo-provision.sh can remove them; App Store
installs are recorded in apps-mas for information (they are not uninstalled).
In the TEST environment nothing is installed; the plan is only reported.
One failed install never stops the others. The last stdout line is a JSON summary.
"""
import json
import os
import subprocess
import sys
from pathlib import Path


def run(cmd, tty, check=False, capture=False, env=None):
    if capture:
        return subprocess.run(cmd, capture_output=True, text=True, env=env)
    stream = open(tty, "r+b", buffering=0)
    try:
        return subprocess.run(cmd, stdin=stream, stdout=stream, stderr=stream, env=env)
    finally:
        stream.close()


def merge_manifest(path, lines):
    lines = {line for line in lines if line}
    if path.exists():
        lines |= {line.strip() for line in path.read_text().splitlines() if line.strip()}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(sorted(lines)) + "\n")
    os.replace(tmp, path)


def main():
    catalog, state_dir, env_name, tty = sys.argv[1:5]
    state = Path(state_dir)
    apps = json.loads(Path(catalog).read_text())["apps"]
    home = Path.home()
    brew_env = dict(os.environ, HOMEBREW_NO_AUTO_UPDATE="1", HOMEBREW_NO_INSTALL_CLEANUP="1",
                    HOMEBREW_NO_INSTALL_UPGRADE="1", HOMEBREW_NO_INSTALLED_DEPENDENTS_CHECK="1")
    if os.environ.get("UNATTENDED") == "true":
        brew_env["HOMEBREW_NO_ASK"] = "1"
    else:
        brew_env.pop("HOMEBREW_NO_ASK", None)

    casks_out = run(["brew", "list", "--cask"], tty, capture=True, env=brew_env)
    installed_casks = set(casks_out.stdout.split()) if casks_out.returncode == 0 else set()
    mas_installed = set()
    mas_out = run(["mas", "list"], tty, capture=True) if shutil_which("mas") else None
    if mas_out is not None and mas_out.returncode == 0:
        mas_installed = {line.split()[0] for line in mas_out.stdout.splitlines() if line.split()}

    def present(app):
        bundle = app["app"] + ".app"
        if any((root / bundle).exists() for root in (Path("/Applications"), home / "Applications")):
            return True
        if "cask" in app and app["cask"] in installed_casks:
            return True
        return "mas" in app and str(app["mas"]) in mas_installed

    todo = [app for app in apps if not present(app)]
    casks = [a for a in todo if "cask" in a]
    store = [a for a in todo if "mas" in a]
    manual = [a for a in todo if "manual" in a and not a.get("from_backup")]
    from_backup = [a["name"] for a in todo if a.get("from_backup")]
    summary = {"present": len(apps) - len(todo), "casks": [a["name"] for a in casks],
               "store": [a["name"] for a in store], "manual": [{"name": a["name"], "why": a["manual"]} for a in manual],
               "from_backup": from_backup, "installed": [], "failed": [], "installed_mode": env_name}
    if env_name == "TEST":
        print(json.dumps(summary))
        return 0

    if casks:
        names = [a["cask"] for a in casks]
        deps = run(["brew", "deps", "--missing", "--cask", *names], tty, capture=True, env=brew_env)
        merge_manifest(state / "apps-casks", names)
        if deps.returncode == 0:
            merge_manifest(state / "apps-formulae", deps.stdout.split())
        for app in casks:
            print(f"Installing {app['name']} (cask {app['cask']})", file=sys.stderr, flush=True)
            result = run(["brew", "install", "--cask", app["cask"]], tty, env=brew_env)
            (summary["installed"] if result.returncode == 0 else summary["failed"]).append(app["name"])
    if store:
        merge_manifest(state / "apps-mas", [str(a["mas"]) for a in store])
        # mas 7 has no way to report the App Store sign-in and needs root to
        # install, so the first install is the check: when it fails, open the
        # App Store, let the user sign in and retry, or skip the store apps.
        run(["sudo", "-v"], tty)
        signed_in = False
        skipped = False
        for app in store:
            if skipped:
                summary["failed"].append(app["name"] + " (App Store not signed in)")
                continue
            while True:
                print(f"Installing {app['name']} from the App Store", file=sys.stderr, flush=True)
                result = run(["sudo", "mas", "install", str(app["mas"])], tty)
                if result.returncode == 0:
                    signed_in = True
                    summary["installed"].append(app["name"])
                    break
                if signed_in or os.environ.get("UNATTENDED") == "true":
                    summary["failed"].append(app["name"])
                    skipped = not signed_in
                    break
                if not retry_after_sign_in(tty):
                    summary["failed"].append(app["name"] + " (App Store not signed in)")
                    skipped = True
                    break
    print(json.dumps(summary))
    return 0


def retry_after_sign_in(tty):
    """The first App Store install failed: ask the user to sign in, then retry."""
    run(["open", "-a", "App Store"], tty)
    text = ("The App Store install failed. Sign in with your Apple ID in the App "
            "Store app that just opened (Store > Sign In), then choose Retry. "
            "Skip leaves the App Store apps for you to install.")
    if shutil_which("dialog"):
        result = run(["dialog", "--title", "App Store sign-in", "--default-button", "cancel",
                      "--yes-label", "Retry", "--no-label", "Skip App Store apps", "--yesno", text, "10", "70"], tty)
        return result.returncode == 0
    print(text, file=sys.stderr)
    return False


def shutil_which(name):
    import shutil
    return shutil.which(name)


if __name__ == "__main__":
    sys.exit(main())
