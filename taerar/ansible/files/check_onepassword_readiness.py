"""Read-only readiness checks. Print status only, never keys or account data."""
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import signal
import subprocess
import sys
import tempfile


def run(arguments, environment, timeout=10):
    try:
        process = subprocess.Popen(arguments, env=environment, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, start_new_session=True)
    except OSError:
        return None
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # Git spawns ssh; stop the entire probe rather than leaving an orphan
        # waiting for authorization after the readiness timeout.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()
        return None
    return subprocess.CompletedProcess(arguments, process.returncode, stdout, stderr)


def agent_status(environment, prove_key_use=False):
    result = run(["ssh-add", "-L"], environment)
    if result is None:
        return "unknown"
    if result.returncode != 0:
        return "not_ready"
    keys = [line for line in result.stdout.splitlines()
            if line.startswith(("ssh-ed25519 ", "ssh-rsa ", "ecdsa-sha2-"))]
    if not keys:
        return "not_ready"
    if prove_key_use:
        # Listing public keys alone does not prove the app can use a private key.
        # A local sign/verify test may require native 1Password authorization.
        with tempfile.TemporaryDirectory(prefix="homelab-agent-check-") as folder:
            public_key = Path(folder) / "key.pub"
            public_key.write_text(keys[0] + "\n")
            result = run(["ssh-add", "-T", str(public_key)], environment)
        if result is None:
            return "unknown"
        if result.returncode != 0:
            return "not_ready"
    return "ready"


def safari_status(home):
    # These are Safari's extension state files, not PlugInKit registration.
    # Missing/unreadable/unrecognized state is unknown, never assumed enabled.
    root = home / "Library/Containers/com.apple.Safari/Data/Library/Safari"
    try:
        profiles = [root] + [path for path in root.iterdir() if path.is_dir()]
    except OSError:
        return "unknown"
    enabled = []
    unreadable = False
    for profile in profiles:
        for kind in ("AppExtensions", "WebExtensions"):
            try:
                with (profile / kind / "Extensions.plist").open("rb") as stream:
                    extensions = plistlib.load(stream)
            except FileNotFoundError:
                continue
            except (OSError, ValueError, plistlib.InvalidFileException):
                unreadable = True
                continue
            if not isinstance(extensions, dict):
                unreadable = True
                continue
            for identifier, settings in extensions.items():
                if not re.match(r"^com\.1password\.safari(?:[. (]|$)", identifier):
                    continue
                value = settings.get("Enabled") if isinstance(settings, dict) else None
                if not isinstance(value, bool):
                    unreadable = True
                else:
                    enabled.append(value)
    if False in enabled:
        return "not_ready"
    return "ready" if enabled and not unreadable else "unknown"


def main():
    home = Path(os.environ["HOME"])
    environment = dict(os.environ)
    socket = str(home / "Library/Group Containers/2BUA8C4S2C.com.1password/t/agent.sock")
    environment["SSH_AUTH_SOCK"] = socket
    if sys.argv[1:] == ["signin"]:
        print(json.dumps({"signin": agent_status(environment, prove_key_use=True)}))
        return
    agent = agent_status(environment)
    github = "unknown"
    if agent == "ready":
        environment["GIT_TERMINAL_PROMPT"] = "0"
        environment["GIT_SSH_COMMAND"] = (
            "ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o UpdateHostKeys=no "
            "-o ConnectTimeout=5 -o " + shlex.quote("IdentityAgent=" + socket))
        result = run(["git", "ls-remote", "git@github.com:bnowakow/homelab.git", "HEAD"],
                     environment)
        if result is not None and result.returncode == 0:
            github = "ready"
    print(json.dumps({"agent": agent, "github": github, "safari": safari_status(home)}))


if __name__ == "__main__":
    main()
