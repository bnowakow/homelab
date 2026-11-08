"""Read-only readiness checks. Print status only, never keys or account data."""
import json
import os
from pathlib import Path
import plistlib
import re
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


def autofill_status(environment):
    # PlugInKit marks the user's election for each AutoFill (credential provider)
    # extension: "+" enabled, "-" disabled. Anything else (missing extension,
    # failed query, other markers) is unknown, never assumed enabled.
    result = run(["pluginkit", "-m", "-v", "-p",
                  "com.apple.authentication-services-credential-provider-ui"], environment)
    if result is None or result.returncode != 0:
        return "unknown"
    states = set()
    for line in result.stdout.splitlines():
        match = re.match(r"^\s*([+\-!=?]?)\s*com\.1password\.", line)
        if match:
            states.add(match.group(1))
    if states == {"+"}:
        return "ready"
    if "-" in states:
        return "not_ready"
    return "unknown"


def github_status(environment, socket):
    # GitHub returns 1 after successful authentication because it has no shell.
    # Test the authenticated greeting, not just the exit code or repo access.
    result = run([
        "ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
        "-o", "UpdateHostKeys=no", "-o", "ConnectTimeout=10",
        "-o", "IdentityAgent=" + socket, "git@github.com",
    ], environment, timeout=30)
    if result is None:
        return "unknown", "timeout_or_unavailable"
    output = result.stdout + result.stderr
    if result.returncode == 1 and re.search(
            r"Hi [^!\r\n]+! You've successfully authenticated, but GitHub does not provide shell access\.",
            output):
        return "ready", "authenticated"
    if "Permission denied (publickey)" in output:
        return "not_ready", "authentication"
    if "REMOTE HOST IDENTIFICATION HAS CHANGED" in output:
        return "unknown", "host_key_changed"
    if "Host key verification failed" in output or re.search(r"No \S+ host key is known", output):
        return "unknown", "host_key"
    if any(message in output for message in (
            "Could not resolve hostname", "Connection timed out", "Connection refused",
            "Network is unreachable", "Operation timed out")):
        return "unknown", "network"
    return "unknown", "unrecognized_response"


def main():
    home = Path(os.environ["HOME"])
    environment = dict(os.environ)
    socket = str(home / "Library/Group Containers/2BUA8C4S2C.com.1password/t/agent.sock")
    environment["SSH_AUTH_SOCK"] = socket
    if sys.argv[1:] == ["signin"]:
        print(json.dumps({"signin": agent_status(environment, prove_key_use=True)}))
        return
    agent = agent_status(environment)
    github, github_reason = "unknown", "agent_not_ready"
    if agent == "ready":
        github, github_reason = github_status(environment, socket)
    print(json.dumps({"agent": agent, "github": github, "github_reason": github_reason,
                      "safari": safari_status(home), "autofill": autofill_status(environment)}))


if __name__ == "__main__":
    main()
