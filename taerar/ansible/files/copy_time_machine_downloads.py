"""Copy ~/Downloads from the latest backup inside a Time Machine sparse bundle.

The bundle is attached read-only. With HOMELAB_KEEP_BACKUP_ATTACHED=true (set by
provision.sh) it stays attached after the copy and the next copy step reuses it
instead of repeating the slow network attach; provision.sh detaches it at the end
of the run (--detach) unless the user chooses to keep it for reruns. Without
that variable it is detached again right after the copy. Nothing in the
backup is modified. Existing files in the destination are never overwritten.
Every created file and directory is recorded in a JSON-lines manifest before it
is created, so undo-provision.sh can remove exactly what this script added.
Files are copied with Homebrew rsync 3.x (extended attributes included); the
macOS openrsync is never used because it cannot copy extended attributes.

Usage: copy_time_machine_downloads.py [--dia] BUNDLE DESTINATION MANIFEST PROD|TEST
Without --dia, copies ~/Downloads into DESTINATION (the Downloads folder). With
--dia, copies Dia's plain configuration files from the user's home folder in
the backup into DESTINATION (the home folder); TEST copies only Preferences. Its
summary also lists the extensions installed in Dia in the backup.
       copy_time_machine_downloads.py --dia-extensions BUNDLE
only prints that extension list (nothing is copied).
Progress goes to stderr; a JSON summary is printed to stdout.
       copy_time_machine_downloads.py --check-access
prints {"full_disk_access": "granted"|"missing"|"unknown", "app": NAME}.
       copy_time_machine_downloads.py --detach
detaches a backup that was kept attached (prints {"detached": true|false}).
"""
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time

SKIPPED_NAMES = {".DS_Store", ".localized"}
# Keep headroom so copying cannot fill the disk completely.
FREE_SPACE_MARGIN = 1024 ** 3
FULL_DISK_ACCESS = (
    "macOS denied access to the backup contents. Give the terminal app running "
    "provisioning Full Disk Access (System Settings > Privacy & Security > Full Disk "
    "Access), quit and reopen it, then rerun provisioning.")


class CopyError(Exception):
    pass


sys.path.insert(0, str(Path(__file__).resolve().parent))
import copy_status  # noqa: E402  (records which copies were done; see copy_status.py)

# Set by main() once the step is known; None for checks that copy nothing.
STEP = None
BUNDLE_NAME = None   # name of the backup bundle being copied (for per-app status records)
STEP_DETAIL = {}
RESULT = {}


def emit(result):
    """Print the JSON summary for Ansible and remember it for the status record."""
    RESULT.clear()
    RESULT.update(result)
    print(json.dumps(result))


# Readable only with Full Disk Access; listing it reads no private data.
FULL_DISK_ACCESS_PROBE = "/Library/Application Support/com.apple.TCC"


def responsible_app():
    """Name the outermost .app above this process; macOS checks that app."""
    pid, app = os.getpid(), None
    for _ in range(32):
        result = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        parts = result.stdout.strip().split(None, 1)
        if result.returncode != 0 or len(parts) != 2:
            break
        parent, command = parts
        if ".app/Contents/" in command:
            app = command.split(".app/Contents/")[0].rsplit("/", 1)[-1]
        pid = int(parent)
        if pid <= 1:
            break
    return app


def full_disk_access_status():
    """Return "granted", "missing" or "unknown" (probe unavailable)."""
    try:
        os.listdir(FULL_DISK_ACCESS_PROBE)
    except PermissionError:
        return "missing"
    except OSError:
        return "unknown"
    return "granted"


def check_full_disk_access():
    # "unknown" continues; reading the backup then reports its own errors.
    if full_disk_access_status() == "missing":
        app = responsible_app()
        target = f"the {app} app" if app else "the terminal app running provisioning"
        raise CopyError(
            f"Full Disk Access is not enabled for {target}, so macOS will not let it read "
            f"Time Machine backups. Enable {app or 'it'} in System Settings > Privacy & "
            "Security > Full Disk Access, quit and reopen it, then rerun provisioning.")


def progress(message):
    print(message, file=sys.stderr, flush=True)


ATTACH_FAILED = ("Could not attach the backup read-only. If Time Machine is currently "
                 "using it, wait until that backup finishes, then rerun provisioning.")


def image_entities(output):
    entities = [entity for entity in plistlib.loads(output).get("system-entities", [])
                if "dev-entry" in entity]
    if not entities:
        raise CopyError("hdiutil attached the backup but reported no device.")
    return entities


class attach_heartbeat:
    """Print a reminder every few minutes while the (possibly very slow) attach runs."""

    def __init__(self, name, interval=120):
        self.name, self.interval = name, interval
        self.stop, self.started = threading.Event(), time.monotonic()

    def run(self):
        while not self.stop.wait(self.interval):
            minutes = int((time.monotonic() - self.started) // 60)
            progress(f"Still attaching {self.name} ({minutes} min). If this never ends, check that the NAS "
                     "is reachable and that no backup is running, then press Ctrl-C and rerun.")

    def __enter__(self):
        threading.Thread(target=self.run, daemon=True).start()

    def __exit__(self, *exc):
        self.stop.set()


def attach(bundle, mountpoint):
    """Attach read-only and mount the backup volume.

    Returns (image device, all image devices, mounted volume device).
    """
    # stdin stays on the terminal so hdiutil can ask for an image password.
    result = subprocess.run(
        ["hdiutil", "attach", "-readonly", "-nobrowse", "-noautoopen", "-plist",
         "-mountpoint", str(mountpoint), str(bundle)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode == 0:
        entities = image_entities(result.stdout)
        devices = [entity["dev-entry"] for entity in entities]
        # The shortest entry (e.g. /dev/disk8) is the whole image.
        whole = min(devices, key=len)
        volume = next((entity["dev-entry"] for entity in entities
                       if entity.get("mount-point") and Path(entity["mount-point"]).resolve() == mountpoint.resolve()),
                      None)
        if volume is None:
            detach(whole, devices)
            raise CopyError("hdiutil attached the backup but did not report its mounted volume.")
        return whole, devices, volume
    if b"no mountable file systems" not in result.stderr:
        detail = " ".join(result.stderr.decode(errors="replace").split())
        if "Resource busy" in detail:
            raise CopyError(
                "The backup image is already attached or in use: by Time Machine, Finder, another "
                "provisioning run, or the Mac that owns it while it backs up. Eject it (hdiutil info "
                "lists attached images) or wait until that finishes, then rerun provisioning.")
        progress(detail)
        raise CopyError(ATTACH_FAILED)
    # An encrypted APFS backup stays locked here: attach without mounting,
    # unlock it with a terminal password prompt, then mount it read-only.
    result = subprocess.run(["hdiutil", "attach", "-readonly", "-nomount", "-plist", str(bundle)],
                            stdout=subprocess.PIPE)
    if result.returncode != 0:
        raise CopyError(ATTACH_FAILED)
    devices = [entity["dev-entry"] for entity in image_entities(result.stdout)]
    whole = min(devices, key=len)
    try:
        volume = mount_apfs_backup(devices, mountpoint)
    except BaseException:
        detach(whole, devices)
        raise
    return whole, devices, volume


KEEP_ENV = "HOMELAB_KEEP_BACKUP_ATTACHED"
NAS_DOMAIN = os.environ.get("HOMELAB_NAS_DOMAIN", "")
ATTACHMENT_FILE = Path.home() / ".homelab-provision-state" / "backup-attachment.json"


def load_attachment(bundle):
    """Return the record of a still usable kept attachment of this bundle.
    A record that no longer fits (other bundle, unmounted, ejected) is released."""
    try:
        saved = json.loads(ATTACHMENT_FILE.read_text())
    except (OSError, ValueError):
        return None
    try:
        # The record also holds the NAS address of its location: after switching between
        # local and remote the old attachment goes through the other address.
        if (saved["bundle"] == str(bundle.resolve()) and saved.get("nas_domain", "") == NAS_DOMAIN
                and os.path.ismount(saved["mountpoint"])
                and Path(saved["backup"]).is_dir()):
            return saved
    except (KeyError, OSError):
        pass
    release_attachment(saved)
    return None


def save_attachment(**record):
    ATTACHMENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    ATTACHMENT_FILE.write_text(json.dumps(record))


def release_attachment(saved):
    """Unmount the snapshot, detach the image and forget the record."""
    snapshot, mount = Path(saved.get("snapshot_mountpoint", "")), Path(saved.get("mountpoint", ""))
    if saved.get("snapshot_mountpoint") and os.path.ismount(snapshot):
        subprocess.run(["umount", str(snapshot)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if saved.get("whole"):
        detach(saved["whole"], saved.get("devices", []))
    for folder in (snapshot, mount):
        if str(folder) != ".":
            try:
                folder.rmdir()
            except OSError:
                pass
    try:
        ATTACHMENT_FILE.unlink()
    except OSError:
        pass


def mount_apfs_backup(devices, mountpoint):
    """Mount the image's APFS backup volume read-only; return its device."""
    # hdiutil may list only whole disks (e.g. disk8 and container disk10), not
    # the partition (disk8s1) that backs the container, so compare whole disks.
    def whole_disk(identifier):
        match = re.match(r"(?:/dev/)?(disk\d+)", identifier or "")
        return match.group(1) if match else None

    image_disks = {whole_disk(device) for device in devices} - {None}
    listing = subprocess.run(["diskutil", "apfs", "list", "-plist"], stdout=subprocess.PIPE)
    containers = plistlib.loads(listing.stdout).get("Containers", []) if listing.returncode == 0 else []
    volumes = [volume for container in containers
               if whole_disk(container.get("ContainerReference")) in image_disks
               or any(whole_disk(store.get("DeviceIdentifier")) in image_disks
                      for store in container.get("PhysicalStores", []))
               for volume in container.get("Volumes", [])]
    if not volumes:
        raise CopyError("The backup image contains no mountable APFS volume.")
    volume = next((volume for volume in volumes if "Backup" in volume.get("Roles", [])), volumes[0])
    identifier = volume["DeviceIdentifier"]
    mount = ["diskutil", "mount", "readOnly", "-mountPoint", str(mountpoint), identifier]
    if subprocess.run(mount, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        return "/dev/" + identifier
    if not (volume.get("Locked") or volume.get("Encryption") or volume.get("FileVault")):
        raise CopyError("Could not mount the backup volume read-only.")
    progress(f"The backup volume {volume.get('Name', identifier)} is encrypted. "
             "Enter the Time Machine backup password when asked (it is not stored).")
    if subprocess.run(["diskutil", "apfs", "unlockVolume", identifier, "-nomount"]).returncode != 0:
        raise CopyError("Could not unlock the encrypted backup. Check the backup password and rerun.")
    if subprocess.run(mount, stdout=subprocess.DEVNULL).returncode != 0:
        raise CopyError("Could not mount the unlocked backup volume read-only.")
    return "/dev/" + identifier


def detach(whole, devices=()):
    """Detach the image; fall back to diskutil, which can also remove mounts
    that hdiutil reports as busy."""
    attempts = [["hdiutil", "detach", whole], ["hdiutil", "detach", "-force", whole]]
    # e.g. the synthesized APFS container disk10 of image disk8.
    disks = {match.group(0) for match in (re.match(r"/dev/disk\d+", device) for device in devices) if match}
    attempts += [["diskutil", "unmountDisk", "force", disk] for disk in sorted(disks - {whole})]
    attempts += [["diskutil", "eject", whole]]
    for arguments in attempts:
        try:
            # A dead network share can make these hang; give up on one attempt after a minute.
            result = subprocess.run(arguments, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        except subprocess.TimeoutExpired:
            continue
        if result.returncode == 0 and arguments[1] in ("detach", "eject"):
            return True
    progress(f"Warning: could not detach {whole}. Eject it in Finder or run: diskutil eject {whole}")
    return False


SNAPSHOT_NAME = re.compile(r"com\.apple\.TimeMachine\.(\d{4}-\d{2}-\d{2}-\d{6})\.backup")


def latest_backup(mountpoint, volume, snapshot_mountpoint):
    """Mount the newest completed backup read-only; return its backup folder.

    APFS Time Machine backups are volume snapshots. Folders at the volume
    root (e.g. an unfinished <date>.backup) are not completed backups.
    tmutil is deliberately not used: it mounts every snapshot through a root
    helper, which enforces the source Mac's user IDs and keeps the image busy.
    """
    attempts = []
    dates = []
    for target in (volume, str(mountpoint)):
        listing = subprocess.run(["diskutil", "apfs", "listSnapshots", "-plist", target],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        names = []
        if listing.returncode == 0:
            try:
                names = [snapshot.get("SnapshotName", "")
                         for snapshot in plistlib.loads(listing.stdout).get("Snapshots", [])]
            except Exception:  # unparsable output is reported below, never fatal here
                names = []
        dates = sorted(match.group(1) for match in map(SNAPSHOT_NAME.fullmatch, names) if match)
        note = f"{target}: exit {listing.returncode}, {len(names)} snapshot(s), {len(dates)} Time Machine backup(s)"
        if listing.returncode != 0:
            note += ", " + " ".join(listing.stderr.decode(errors="replace").split())[:160]
        attempts.append(note)
        if dates:
            break
    if dates:
        name = f"com.apple.TimeMachine.{dates[-1]}.backup"
        # noowners: the source Mac's user IDs need not match this Mac's.
        result = subprocess.run(["mount_apfs", "-o", "rdonly,nobrowse,noowners", "-s", name,
                                 volume, str(snapshot_mountpoint)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            raise CopyError(f"Could not mount the latest backup ({dates[-1]}) read-only: "
                            + " ".join(result.stderr.split()))
        backup = snapshot_mountpoint / f"{dates[-1]}.backup"
        if not backup.is_dir():
            raise CopyError(f"The latest backup snapshot has no {dates[-1]}.backup folder.")
        return backup
    # Older HFS+ backups keep a Latest link per computer instead of snapshots.
    try:
        backups = sorted(path for path in (mountpoint / "Backups.backupdb").glob("*/Latest")
                         if path.is_dir())
    except PermissionError as error:
        raise CopyError(FULL_DISK_ACCESS) from error
    if not backups:
        try:
            root = ", ".join(sorted(path.name for path in mountpoint.iterdir())[:8]) or "empty"
        except OSError as error:
            root = f"unreadable ({error})"
        raise CopyError("No completed backups were found in this Time Machine bundle ("
                        + "; ".join(attempts) + "; volume root: " + root + ").")
    return backups[-1]


def find_downloads(backup, user):
    """Find Users/<user>/Downloads in one of the backed-up volumes."""
    try:
        volumes = sorted((path for path in backup.iterdir() if path.is_dir()),
                         key=lambda path: (not path.name.endswith(" - Data"), path.name))
    except PermissionError as error:
        raise CopyError(FULL_DISK_ACCESS) from error
    users = set()
    for volume in volumes:
        downloads = volume / "Users" / user / "Downloads"
        if downloads.is_dir():
            return downloads
        try:
            users.update(path.name for path in (volume / "Users").iterdir() if path.is_dir())
        except OSError:
            pass
    found = ", ".join(sorted(users - {"Shared"})) or "none"
    raise CopyError(f"The latest backup has no Downloads folder for user {user} "
                    f"(users found in the backup: {found}).")


def find_user_home(backup, user):
    """Find Users/<user> in one of the backed-up volumes (the "Data" volume first)."""
    try:
        volumes = sorted((path for path in backup.iterdir() if path.is_dir()),
                         key=lambda path: (not path.name.endswith(" - Data"), path.name))
    except PermissionError as error:
        raise CopyError(FULL_DISK_ACCESS) from error
    users = set()
    for volume in volumes:
        home = volume / "Users" / user
        if home.is_dir():
            return home
        try:
            users.update(path.name for path in (volume / "Users").iterdir() if path.is_dir())
        except OSError:
            pass
    found = ", ".join(sorted(users - {"Shared"})) or "none"
    raise CopyError(f"The latest backup has no home folder for user {user} "
                    f"(users found in the backup: {found}).")


# Dia (Chromium based) configuration that moves between Macs. Only plain-text
# settings are listed: Dia's own databases (tabs, spaces, skills) and the
# browser's cookies, saved logins and autofill data are encrypted with a key that
# stays on the source Mac, and history, site data and caches are not
# configuration. Extensions cannot be copied: the browser keeps the list of
# installed extensions in "Secure Preferences", signed for the Mac that wrote it,
# and deletes extension folders that are not on that list. Their code, state and
# policy files and "Secure Preferences" are therefore not copied; the extensions
# are reinstalled from the Chrome Web Store (see list_dia_extensions). Per-extension
# storage is keyed by extension ID, so it is kept for when one is reinstalled.
DIA_ROOT = Path("Library/Application Support/Dia")
DIA_PLIST = Path("Library/Preferences/company.thebrowser.dia.plist")
DIA_CONFIG = [
    "User Data/Local State",
    "User Data/Default/Preferences",
    "User Data/Default/Bookmarks",
    "User Data/Default/Local Extension Settings",
    "User Data/Default/Sync Extension Settings",
]
DIA_TEST = DIA_ROOT / "User Data/Default/Preferences"


EXTENSION_ID = re.compile(r"[a-p]{32}")


def clean_name(text, fallback):
    text = "".join(character if character.isprintable() else " " for character in str(text))
    return " ".join(text.split())[:80] or fallback


def list_dia_extensions(home):
    """Return [{id, name, version}] for the extensions installed in Dia in the backup.

    Reads only each extension's manifest (and its default-language messages to
    resolve a localized name) from the read-only snapshot.
    """
    root = home / DIA_ROOT / "User Data/Default/Extensions"
    result = []
    try:
        folders = sorted(path for path in root.iterdir() if path.is_dir() and EXTENSION_ID.fullmatch(path.name))
    except OSError:
        return result
    for folder in folders:
        versions = sorted(path for path in folder.iterdir() if path.is_dir()) if folder.is_dir() else []
        if not versions:
            continue
        base = versions[-1]
        name, version = folder.name, None
        try:
            manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
            name, version = manifest.get("name", folder.name), manifest.get("version")
            if isinstance(name, str) and name.startswith("__MSG_") and name.endswith("__"):
                key = name[6:-2].lower()
                for locale in (manifest.get("default_locale"), "en", "en_US"):
                    messages = base / "_locales" / str(locale) / "messages.json"
                    if locale and messages.is_file():
                        found = {k.lower(): v for k, v in json.loads(messages.read_text(encoding="utf-8")).items()}
                        name = found.get(key, {}).get("message", name)
                        break
        except (OSError, ValueError, AttributeError):
            pass
        result.append({"id": folder.name, "name": clean_name(name, folder.name),
                       "version": clean_name(version or "", "")})
    return result


def plan_dia(home, mode):
    """Return (path relative to the home folder, source path) pairs for Dia."""
    if mode == "TEST":
        # Only the main preferences file.
        path = home / DIA_TEST
        return [(DIA_TEST, path)] if path.is_file() and not path.is_symlink() else []
    files = {}
    for entry in [DIA_ROOT / item for item in DIA_CONFIG] + [DIA_PLIST]:
        path = home / entry
        if path.is_dir() and not path.is_symlink():
            for folder, directories, names in os.walk(path):
                directories.sort()
                for name in sorted(names):
                    if name in SKIPPED_NAMES or name == "LOCK":
                        continue
                    found = Path(folder) / name
                    files[found.relative_to(home)] = found
        elif path.is_file() or path.is_symlink():
            files[entry] = path
    return sorted(files.items(), key=lambda item: str(item[0]))


def app_bundle_info(bundle):
    """Return (bundle id, executable) from an .app's Info.plist, or (None, None)."""
    try:
        info = plistlib.loads((bundle / "Contents/Info.plist").read_bytes())
    except (OSError, plistlib.InvalidFileException, ValueError):
        return None, None
    identifier = info.get("CFBundleIdentifier")
    executable = info.get("CFBundleExecutable")
    return (identifier if isinstance(identifier, str) else None,
            executable if isinstance(executable, str) else None)


def application_roots(backup_home):
    """Application folders in the backup and their targets on this Mac."""
    return [(backup_home.parent.parent / "Applications", Path("/Applications")),
            (backup_home / "Applications", Path.home() / "Applications")]


def plan_apps(catalog, backup_home, mode, only=None, matched=None):
    """Plan app bundles (from_backup, missing here) and preference plists.

    Returns (bundles, plists, notes). bundles: (backup bundle folder, target
    root, file list); plists: (relative path, source) pairs for the home folder;
    notes: what was skipped and why. With `only` (a set of catalog app names, used for
    apps the user installed by hand) just those apps' preferences are planned, no bundles.
    """
    apps = json.loads(Path(catalog).read_text())["apps"]
    if only is not None:
        apps = [app for app in apps if app["name"] in only]
    notes = []
    bundles = []
    present = {}   # app name -> (bundle id, executable) for apps on this Mac after copying
    if mode == "PROD" and only is None:
        for app in (a for a in apps if a.get("from_backup")):
            name = app["app"] + ".app"
            if any((target / name).exists() for _, target in application_roots(backup_home)):
                continue
            for source_root, target in application_roots(backup_home):
                folder = source_root / name
                if folder.is_dir() and not folder.is_symlink():
                    files = []
                    for walked, directories, names in os.walk(folder):
                        directories.sort()
                        for item in sorted(names):
                            found = Path(walked) / item
                            files.append((found.relative_to(source_root), found))
                    bundles.append((folder, target, files))
                    break
            else:
                notes.append(f"{app['name']}: not found in the backup")
    plists = []
    for app in apps:
        # "aliases" are other bundle names the same app has had (e.g. "Pages Creator Studio").
        names = [app["app"] + ".app"] + [alias + ".app" for alias in app.get("aliases", [])]
        name = names[0]
        backup_bundle = next((root / n for n in names for root, _ in application_roots(backup_home)
                              if (root / n).is_dir()), None)
        if backup_bundle is None:
            if only is not None:
                notes.append(f"{app['name']}: not found in the backup, so there are no preferences to copy")
            continue
        identifier, executable = app_bundle_info(backup_bundle)
        if not identifier:
            continue
        here = any((target / n).exists() for n in names for _, target in application_roots(backup_home)) \
            or any(item[0].name in names for item in bundles)
        if not here:
            if only is not None:
                notes.append(f"{app['name']}: not installed here, preferences not copied")
            continue
        if executable and subprocess.run(["pgrep", "-x", "-u", str(os.getuid()), executable], stdout=subprocess.DEVNULL).returncode == 0:
            notes.append(f"{app['name']}: running, preferences not copied (quit it and rerun)")
            continue
        for relative in (Path("Library/Preferences") / f"{identifier}.plist",
                         Path("Library/Containers") / identifier / "Data/Library/Preferences" / f"{identifier}.plist"):
            path = backup_home / relative
            if path.is_file() and not path.is_symlink():
                plists.append((relative, path))
                if matched is not None and app["name"] not in matched:
                    matched.append(app["name"])   # this app's preferences exist in the backup
        # Extra configuration files listed in the catalog ("config_paths", relative to the home folder).
        for entry in app.get("config_paths", []):
            path = backup_home / entry
            found_files = []
            if path.is_dir() and not path.is_symlink():
                for folder, directories, names in os.walk(path):
                    directories.sort()
                    found_files += [Path(folder) / n for n in sorted(names) if n not in SKIPPED_NAMES]
            elif path.is_file() and not path.is_symlink():
                found_files.append(path)
            for found in found_files:
                plists.append((found.relative_to(backup_home), found))
                if matched is not None and app["name"] not in matched:
                    matched.append(app["name"])
    if mode == "TEST":
        plists = sorted(plists, key=lambda item: str(item[0]))[:1]
    return bundles, plists, notes


def plan_copy(source, mode):
    """Return (relative path, source path) pairs of files to copy."""
    if mode == "TEST":
        # Only the first top-level visible regular file, by name.
        candidates = sorted((path for path in source.iterdir()
                             if not path.name.startswith(".") and path.is_file()
                             and not path.is_symlink()),
                            key=lambda path: (path.name.casefold(), path.name))
        return [(Path(candidates[0].name), candidates[0])] if candidates else []
    files = []
    for folder, directories, names in os.walk(source):
        directories.sort()
        for name in sorted(names):
            if name in SKIPPED_NAMES:
                continue
            path = Path(folder) / name
            files.append((path.relative_to(source), path))
    return files


def record(manifest, entry):
    with manifest.open("a") as stream:
        stream.write(json.dumps(entry) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def find_rsync():
    """Return Homebrew rsync 3.x; macOS's openrsync cannot copy xattrs (-X)."""
    candidates = [os.environ.get("HOMELAB_RSYNC"), "/opt/homebrew/bin/rsync", "/usr/local/bin/rsync"]
    for candidate in filter(None, candidates):
        try:
            result = subprocess.run([candidate, "--version"], stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, text=True)
        except OSError:
            continue
        if result.returncode == 0 and re.match(r"rsync\s+version\s+3\.", result.stdout):
            return candidate
    raise CopyError("Homebrew rsync 3.x is required to copy files from backups. "
                    "Provisioning installs it; rerun provisioning or run: brew install rsync")


def staged_complete(staged, source):
    """True when a staged file is a finished copy of its source.

    rsync sets the modification time only after a file is complete, so equal
    size and time mean complete; a partial file differs in time (and size).
    """
    try:
        staged_stat, source_stat = staged.lstat(), source.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(source_stat.st_mode):
        return stat.S_ISLNK(staged_stat.st_mode)
    return staged_stat.st_size == source_stat.st_size and staged_stat.st_mtime_ns == source_stat.st_mtime_ns


def staged_size(staged):
    try:
        return staged.lstat().st_size
    except OSError:
        return 0


def copy_files(files, source_root, destination, manifest, work_dir, snapshot_name):
    """Copy new files in two resumable steps; never overwrite existing files.

    1. rsync copies into a staging folder inside the state folder with
       --append: after any interruption, even a crash or power loss, a rerun
       continues each partial file after the bytes already there.
    2. Every finished file is moved into place with an atomic rename, so no
       half-written file ever appears in the destination.
    Each new file and missing folder is recorded in the manifest before any data
    is written. Staged data belongs to one snapshot (--append trusts the bytes
    already staged), so data from an older snapshot is discarded.
    """
    new = [(relative, source) for relative, source in files
           if not ((destination / relative).exists() or (destination / relative).is_symlink())]
    skipped = len(files) - len(new)
    if not new:
        return 0, skipped
    rsync = find_rsync()
    stage_root = work_dir / "stage"
    stage = stage_root / snapshot_name
    if stage_root.is_dir():
        for old in stage_root.iterdir():
            if old.name != snapshot_name:
                shutil.rmtree(old, ignore_errors=True)
    stage.mkdir(parents=True, exist_ok=True)
    missing = set()
    for relative, _ in new:
        parent = (destination / relative).parent
        while parent != destination and not parent.exists():
            missing.add(parent)
            parent = parent.parent
    for folder in sorted(missing, key=lambda path: len(path.parts)):
        record(manifest, {"type": "directory", "path": str(folder)})
    for relative, source in new:
        source_stat = source.lstat()
        record(manifest, {"type": "file", "path": str(destination / relative),
                          "size": source_stat.st_size, "mtime_ns": source_stat.st_mtime_ns})
    file_list = work_dir / "rsync-files"
    # rsync treats --files-from entries starting with "#" or ";" as comments
    # (even with --from0); "./" keeps names like "#notes.txt" as files.
    file_list.write_bytes(b"".join(os.fsencode("./" + str(relative)) + b"\0" for relative, _ in new))
    # -l symlinks as links, -p permissions, -t times, -X extended attributes
    # (Finder tags, "Where from"); owners are not kept (the source Mac's IDs
    # differ). --files-from creates the listed parent folders in the staging area.
    result = subprocess.run(
        [rsync, "-lptX", "--append", "--from0", f"--files-from={file_list}",
         "--info=progress2", f"{source_root}/", f"{stage}/"],
        stdout=sys.stderr, stdin=subprocess.DEVNULL)
    if result.returncode != 0:
        done = sum(1 for relative, source in new if staged_complete(stage / relative, source))
        raise CopyError(f"rsync failed (exit code {result.returncode}) after staging {done} of "
                        f"{len(new)} new item(s). Nothing was moved into place that was incomplete; "
                        "rerun provisioning to continue where it stopped.")
    unfinished = [relative for relative, source in new if not staged_complete(stage / relative, source)]
    if unfinished:
        raise CopyError(f"rsync finished but {len(unfinished)} of {len(new)} new item(s) are incomplete "
                        f"(e.g. {unfinished[0]}). Nothing incomplete was moved into place.")
    copied = 0
    for relative, _ in new:
        target = destination / relative
        staged = stage / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            skipped += 1  # appeared meanwhile; never overwrite
            continue
        try:
            os.rename(staged, target)
        except OSError as error:
            raise CopyError(f"Could not move {relative} into place ({error}). The staging folder "
                            f"{stage_root} must be on the same volume as the destination.") from error
        copied += 1
    file_list.unlink()
    shutil.rmtree(stage_root, ignore_errors=True)
    return copied, skipped


def run_git(repo, *args):
    """Run git in a repository without ever writing to it or prompting.

    The backup is a read-only snapshot mounted with noowners, so git's ownership
    check is disabled for these calls and optional index refreshes are off.
    """
    return subprocess.run(["git", "-c", "safe.directory=*", "-C", str(repo), *args],
                          capture_output=True, stdin=subprocess.DEVNULL,
                          env=dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0"))


def inspect_backup_repo(repo):
    """Read-only summary of a backed-up repository: HEAD, uncommitted diff, untracked files."""
    head = run_git(repo, "rev-parse", "--verify", "HEAD")
    untracked = run_git(repo, "ls-files", "-z", "--others", "--exclude-standard")
    diff = run_git(repo, "diff", "HEAD", "--binary", "--no-ext-diff", "--no-textconv")
    stash = run_git(repo, "stash", "list")
    if head.returncode or untracked.returncode or diff.returncode:
        return None
    return {"head": head.stdout.decode().strip(),
            "untracked": [os.fsdecode(name) for name in untracked.stdout.split(b"\0") if name],
            "patch": diff.stdout,
            "stashes": len([line for line in stash.stdout.splitlines() if line])}


def filter_existing_repos(files, source, destination, manifest=None):
    """Handle repositories that exist in both the backup and this Mac.

    For such a repository only the files git reports as untracked (and not
    ignored) are kept for copying; its .git folder and tracked files stay as they
    are here, and the backup's uncommitted changes to tracked files are returned
    as a patch to apply afterwards. Other folders are copied as they are.
    """
    repos = {}
    # A repository whose .git an earlier (interrupted) run created from the
    # backup is not "already here": it still needs the normal full copy.
    created = []
    if manifest and Path(manifest).is_file():
        for line in Path(manifest).read_text().splitlines():
            try:
                created.append(json.loads(line).get("path", ""))
            except ValueError:
                pass
    for entry in sorted(os.scandir(source), key=lambda item: item.name):
        if not entry.is_dir(follow_symlinks=False):
            continue
        if not (Path(entry.path) / ".git").exists() or not (destination / entry.name / ".git").exists():
            continue
        gitdir = str(destination / entry.name / ".git")
        if any(path == gitdir or path.startswith(gitdir + "/") for path in created):
            continue
        info = inspect_backup_repo(Path(entry.path))
        repos[entry.name] = info if info is not None else {"error": "could not read its git state in the backup"}
    if not repos:
        return files, {}
    kept = []
    for relative, path in files:
        name = relative.parts[0] if relative.parts else ""
        info = repos.get(name)
        if info is None:
            kept.append((relative, path))
            continue
        if "error" in info:
            continue
        inside = "/".join(relative.parts[1:])
        if any(inside == item or (item.endswith("/") and inside.startswith(item))
               for item in info["untracked"]):
            kept.append((relative, path))
    return kept, repos


def apply_repo_patches(repos, destination, manifest, work_dir):
    """Apply each backup repository's uncommitted changes to the clone here.

    The patch is applied only when it applies cleanly to the working tree (the
    index is not touched), never over conflicting edits. It is recorded before
    it is applied so undo can reverse it. Anything else is saved as a patch file
    and reported.
    """
    results = []
    patch_dir = work_dir / "patches"
    for name, info in repos.items():
        result = {"repo": name, "untracked": len([i for i in info.get("untracked", []) if not i.endswith("/")]),
                  "stashes": info.get("stashes", 0)}
        results.append(result)
        if "error" in info:
            result["status"] = "skipped: " + info["error"]
            continue
        if not info["patch"].strip():
            result["status"] = "no uncommitted changes to tracked files"
            continue
        repo = destination / name
        patch_dir.mkdir(parents=True, exist_ok=True)
        patch = patch_dir / f"{name}.patch"
        patch.write_bytes(info["patch"])
        result["patch"] = str(patch)
        if run_git(repo, "apply", "-R", "--check", str(patch)).returncode == 0:
            result["status"] = "already applied"
        elif run_git(repo, "apply", "--check", str(patch)).returncode == 0:
            record(manifest, {"type": "patch", "path": str(repo), "patch": str(patch)})
            applied = run_git(repo, "apply", "--whitespace=nowarn", str(patch))
            result["status"] = "applied" if applied.returncode == 0 else \
                "failed: " + applied.stderr.decode(errors="replace").strip()[:200]
        elif run_git(repo, "cat-file", "-e", info["head"] + "^{commit}").returncode != 0:
            result["status"] = "not applied: the backup's HEAD commit is not in this clone; patch saved"
        else:
            result["status"] = "not applied: does not apply cleanly to the files here; patch saved"
    return results


def copy_apps(catalog, backup, user, manifest, mode, only=None):
    """Copy the from_backup app bundles and the preference plists of catalog apps."""
    home = find_user_home(backup, user)
    try:
        matched = []
        bundles, plists, notes = plan_apps(catalog, home, mode, only, matched)
    except PermissionError as error:
        raise CopyError(FULL_DISK_ACCESS) from error
    base = manifest.parent / (manifest.stem + "-work")
    copied_bundles, copied_plists, skipped = [], 0, 0
    for folder, target, files in bundles:
        needed = sum(path.lstat().st_size for _, path in files)
        target.mkdir(parents=True, exist_ok=True)
        if not os.access(target, os.W_OK):
            raise CopyError(f"{target} is not writable by this user; copy {folder.name} manually.")
        if needed + FREE_SPACE_MARGIN > shutil.disk_usage(target).free:
            raise CopyError(f"Not enough free space for {folder.name} ({needed / 1024 ** 3:.1f} GiB).")
        progress(f"Copying {folder.name} ({needed / 1024 ** 2:.0f} MiB) into {target}...")
        done, kept = copy_files(files, folder.parent, target, manifest, base / "bundles", backup.name)
        skipped += kept
        copied_bundles.append(folder.name)
    if plists:
        progress(f"Copying {len(plists)} preference file(s) into {Path.home()}/Library...")
        copied_plists, kept = copy_files(plists, home, Path.home(), manifest, base / "plists", backup.name)
        skipped += kept
    if only is not None and mode == "PROD":
        # Remember per app that its preferences were handled, so the next run preselects only the rest.
        for name in matched:
            copy_status.record(f"vendor-prefs:{name}", "done", backup=backup.name, mode=mode,
                               bundle=BUNDLE_NAME)
    return {"what": "vendor-prefs" if only is not None else "apps", "backup": backup.name,
            "mode": mode, "bundles": copied_bundles,
            "plists": copied_plists, "skipped_existing": skipped, "notes": notes}


# Preference domains merged from the backup by --system-settings. Each is read
# from ~/Library/Preferences/<domain>.plist in the backup and applied with
# `defaults` (never by replacing the file: cfprefsd would overwrite it).
SETTINGS_DOMAINS = [
    ("NSGlobalDomain", ".GlobalPreferences.plist"),
    "com.apple.finder", "com.apple.desktopservices",
    "com.apple.dock", "com.apple.spaces", "com.apple.WindowManager",
    "com.apple.AppleMultitouchTrackpad", "com.apple.driver.AppleBluetoothMultitouch.trackpad",
    "com.apple.driver.AppleBluetoothMultitouch.mouse", "com.apple.HIToolbox",
    "com.apple.symbolichotkeys", "com.apple.screencapture", "com.apple.menuextra.clock",
    "com.apple.controlcenter", "com.apple.universalaccess", "com.apple.systempreferences",
    "com.apple.Music", "com.apple.mail", "com.apple.iCal", "com.apple.Notes",
]
# Volatile or machine-specific keys that are never merged.
SETTINGS_SKIPPED_KEYS = {"FXRecentFolders", "GoToField", "GoToFieldHistory", "FXConnectToLastURL",
                         "FXDesktopVolumePositions", "NSNavLastRootDirectory", "NSNavLastCurrentDirectory"}
SETTINGS_SKIPPED_PREFIXES = ("NSWindow Frame", "NSNavPanel", "NSSplitView", "NSTableView")


def settings_domains():
    for entry in SETTINGS_DOMAINS:
        domain, name = entry if isinstance(entry, tuple) else (entry, entry + ".plist")
        yield domain, name


def read_backup_plist(path):
    try:
        data = plistlib.loads(path.read_bytes())
    except (OSError, plistlib.InvalidFileException, ValueError):
        return None
    return data if isinstance(data, dict) else None


def copy_system_settings(backup, user, manifest, mode, state_dir=None):
    """Merge Finder, Dock and system preference keys from the backup into this Mac.

    The current domain is exported to <state>/system-settings-backup first and
    recorded in the manifest before anything changes, so undo can restore it.
    """
    home = find_user_home(backup, user)
    state_dir = state_dir or manifest.parent
    backups = state_dir / "system-settings-backup"
    backups.mkdir(parents=True, exist_ok=True)
    plan = []
    for domain, name in settings_domains():
        data = read_backup_plist(home / "Library/Preferences" / name)
        if not data:
            continue
        keys = {key: value for key, value in data.items()
                if key not in SETTINGS_SKIPPED_KEYS and not key.startswith(SETTINGS_SKIPPED_PREFIXES)}
        if keys:
            plan.append((domain, keys))
    if mode == "TEST":
        plan = [item for item in plan if item[0] == "com.apple.finder"][:1]
    applied = []
    for domain, keys in plan:
        exported = subprocess.run(["defaults", "export", domain, "-"], capture_output=True)
        current = {}
        saved = None
        if exported.returncode == 0:
            try:
                current = plistlib.loads(exported.stdout)
            except (plistlib.InvalidFileException, ValueError):
                current = {}
            saved = backups / f"{domain}.plist"
        if not any(current.get(key) != value for key, value in keys.items()):
            continue
        if saved and not saved.exists():   # keep the very first original across reruns
            saved.write_bytes(exported.stdout)
        record(manifest, {"type": "domain", "domain": domain, "backup": str(saved) if saved else None,
                          "keys": sorted(keys)})
        merged = dict(current)
        merged.update(keys)
        result = subprocess.run(["defaults", "import", domain, "-"], input=plistlib.dumps(merged),
                                capture_output=True)
        if result.returncode != 0:
            raise CopyError(f"defaults import {domain} failed: {result.stderr.decode(errors='replace').strip()}")
        applied.append(domain)
    if applied:
        for process in ("cfprefsd", "Finder", "Dock", "SystemUIServer"):
            subprocess.run(["killall", process], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"what": "system-settings", "backup": backup.name, "mode": mode, "domains": applied,
            "planned": [domain for domain, _ in plan]}


# Home folders offered by the folders question and copied by --folder NAME.
HOME_FOLDERS = ["Documents", "Downloads", "Movies", "Music", "Pictures",
                "Desktop", "code", "Sync", "go", "Public"]

# Folders under the home folder copied by --user-files (new files only).
USER_FILE_ROOTS = [".ssh", "Library/Fonts", "Library/LaunchAgents", "Library/Services", "Library/Scripts",
                   # Dotfiles and folders that make install-dotfiles does not install
                   # (.gitconfig, .p10k.zsh, .vimrc, .zpreztorc and .zshrc come from the
                   # repository). Caches, installed tools and model stores are left out.
                   ".zshenv", ".zprofile", ".zlogin", ".zlogout", ".zsh_history", ".viminfo",
                   ".config", ".docker", ".kube", ".cursor", ".vscode", ".codex", ".claude",
                   ".claude.json", ".gradle", ".m2", ".aws", ".gnupg", ".screenrc", ".npmrc",
                   ".gitignore_global", ".gitconfig-local"]
# Copied by --private-data (new files only): data that apps keep open and that
# needs the apps to be closed.
PRIVATE_DATA_ROOTS = ["Library/Keychains", "Library/Safari", "Library/Containers/com.apple.Safari",
                      "Library/Messages", "Library/Mail", "Library/Containers/com.apple.mail",
                      "Library/Group Containers/group.com.apple.mail"]
SIDEBAR_DIR = Path("Library/Application Support/com.apple.sharedfilelist")
SIDEBAR_PREFIX = "com.apple.LSSharedFileList.FavoriteItems.sfl"


def plan_user_files(home, mode, roots=None):
    """(relative path, source) pairs of ssh, fonts, login items, dotfiles and scripts."""
    files = {}
    for root in (USER_FILE_ROOTS if roots is None else roots):
        base = home / root
        if base.is_file() and not base.is_symlink():
            files[base.relative_to(home)] = base
            continue
        if not base.is_dir() or base.is_symlink():
            continue
        for folder, directories, names in os.walk(base):
            directories.sort()
            for name in sorted(names):
                if name in SKIPPED_NAMES:
                    continue
                found = Path(folder) / name
                if found.is_symlink() or found.is_file():   # skips sockets (ssh agent, ControlMaster)
                    files[found.relative_to(home)] = found
    ordered = sorted(files.items(), key=lambda item: str(item[0]))
    if mode == "TEST":
        # One small file only: known_hosts when present, else the first file.
        preferred = [item for item in ordered if item[0] == Path(".ssh/known_hosts")]
        return (preferred or ordered)[:1]
    return ordered


def replace_sidebar(home, destination_home, manifest, state_dir):
    """Replace the Finder sidebar favorites with the backup's, saving the original."""
    source_dir = home / SIDEBAR_DIR
    replaced = []
    if not source_dir.is_dir():
        return replaced
    for source in sorted(source_dir.glob(SIDEBAR_PREFIX + "*")):
        if not source.is_file() or source.is_symlink():
            continue
        target = destination_home / SIDEBAR_DIR / source.name
        if target.is_file() and target.read_bytes() == source.read_bytes():
            continue
        backup = None
        if target.exists():
            folder = state_dir / "user-files-backup"
            folder.mkdir(parents=True, exist_ok=True)
            backup = folder / target.name
            if not backup.exists():
                result = subprocess.run([find_rsync(), "-lptX", str(target), str(backup)],
                                        stdout=sys.stderr, stdin=subprocess.DEVNULL)
                if result.returncode != 0:
                    raise CopyError(f"rsync failed (exit code {result.returncode}) saving {target.name}.")
        record(manifest, {"type": "replaced", "path": str(target), "backup": str(backup) if backup else None})
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".homelab-tmp")
        # Homebrew rsync (keeps times and extended attributes) into a temporary
        # name next to the target, then an atomic rename.
        result = subprocess.run([find_rsync(), "-lptX", str(source), str(temporary)],
                                stdout=sys.stderr, stdin=subprocess.DEVNULL)
        if result.returncode != 0:
            if temporary.exists():
                temporary.unlink()
            raise CopyError(f"rsync failed (exit code {result.returncode}) copying {source.name}.")
        os.replace(temporary, target)
        replaced.append(target.name)
    return replaced


def copy_user_files(backup, user, manifest, mode):
    """Copy ~/.ssh, fonts, LaunchAgents, Services, Scripts (new files only) and the Finder sidebar."""
    home = find_user_home(backup, user)
    try:
        files = plan_user_files(home, mode)
    except PermissionError as error:
        raise CopyError(FULL_DISK_ACCESS) from error
    destination = Path.home()
    base = manifest.parent / (manifest.stem + "-work")
    copied, skipped = 0, 0
    if files:
        needed = sum(path.lstat().st_size for _, path in files)
        if needed + FREE_SPACE_MARGIN > shutil.disk_usage(destination).free:
            raise CopyError(f"Not enough free space for the user files ({needed / 1024 ** 2:.0f} MiB).")
        progress(f"Copying {len(files)} user file(s) ({needed / 1024 ** 2:.0f} MiB) into {destination}...")
        copied, skipped = copy_files(files, home, destination, manifest, base, backup.name)
    ssh = destination / ".ssh"
    if ssh.is_dir() and (ssh.stat().st_mode & 0o077):
        ssh.chmod(0o700)   # a folder we created gets default permissions; ssh wants it private
    gnupg = destination / ".gnupg"
    if gnupg.is_dir() and (gnupg.stat().st_mode & 0o077):
        gnupg.chmod(0o700)
    sidebar = []
    if mode == "PROD":
        sidebar = replace_sidebar(home, destination, manifest, manifest.parent)
        if sidebar:
            for process in ("sharedfilelistd", "Finder"):
                subprocess.run(["killall", process], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"what": "user-files", "backup": backup.name, "mode": mode, "copied": copied,
            "skipped_existing": skipped, "sidebar": sidebar}


def copy_private_data(backup, user, manifest, mode):
    """Copy keychains, Safari, Messages and Mail data (new files only)."""
    home = find_user_home(backup, user)
    try:
        files = plan_user_files(home, mode, PRIVATE_DATA_ROOTS)
    except PermissionError as error:
        raise CopyError(FULL_DISK_ACCESS) from error
    destination = Path.home()
    base = manifest.parent / (manifest.stem + "-work")
    copied, skipped = 0, 0
    if files:
        needed = sum(path.lstat().st_size for _, path in files)
        if needed + FREE_SPACE_MARGIN > shutil.disk_usage(destination).free:
            raise CopyError(f"Not enough free space for the private data ({needed / 1024 ** 2:.0f} MiB).")
        progress(f"Copying {len(files)} private data file(s) ({needed / 1024 ** 2:.0f} MiB) into {destination}...")
        copied, skipped = copy_files(files, home, destination, manifest, base, backup.name)
    return {"what": "private-data", "backup": backup.name, "mode": mode, "copied": copied,
            "skipped_existing": skipped}


def main():
    if sys.argv[1:] == ["--detach"]:
        # Used by provision.sh at the end of a run.
        try:
            saved = json.loads(ATTACHMENT_FILE.read_text())
        except (OSError, ValueError):
            print(json.dumps({"detached": False}))
            return
        release_attachment(saved)
        print(json.dumps({"detached": True}))
        return
    if sys.argv[1:] == ["--check-access"]:
        # Used by full-disk-access_playbook.yml early in provisioning.
        print(json.dumps({"full_disk_access": full_disk_access_status(), "app": responsible_app()}))
        return
    args = sys.argv[1:]
    what = "downloads"
    catalog = None
    folder_name = None
    if args and args[0] in ("--dia", "--dia-extensions"):
        what, args = args[0][2:], args[1:]
    elif args and args[0] == "--folder":
        what, folder_name, args = "folder", args[1], args[2:]
        if folder_name not in HOME_FOLDERS:
            raise CopyError(f"Unknown folder {folder_name}; allowed: {', '.join(HOME_FOLDERS)}.")
    elif args and args[0] == "--user-files":
        what, args = "user-files", args[1:]
    elif args and args[0] == "--private-data":
        what, args = "private-data", args[1:]
    elif args and args[0] == "--system-settings":
        what, args = "system-settings", args[1:]
    elif args and args[0] == "--apps":
        what, catalog, args = "apps", args[1], args[2:]
    elif args and args[0] == "--vendor-prefs":
        # Preferences only, for apps installed by hand: --vendor-prefs CATALOG JSON_LIST_OF_NAMES ...
        what, catalog, only, args = "vendor-prefs", args[1], set(json.loads(args[2])), args[3:]
    if what == "dia-extensions":
        args += ["-", "-", "PROD"]  # listing only: no destination, manifest or copy
    bundle, destination, manifest, mode = Path(args[0]), Path(args[1]), Path(args[2]), args[3]
    if mode not in ("PROD", "TEST"):
        raise CopyError("Environment must be PROD or TEST.")
    if not bundle.is_dir():
        raise CopyError(f"Backup not found: {bundle}. Connect the Time Machine share in Finder.")
    global STEP, BUNDLE_NAME
    BUNDLE_NAME = bundle.name
    STEP = f"folder:{folder_name}" if what == "folder" else what
    # Fail in seconds, before the slow network attach, when access is missing.
    check_full_disk_access()
    copy_status.record(STEP, "started", bundle=bundle.name, mode=mode)
    keep = os.environ.get(KEEP_ENV) == "true"
    saved = load_attachment(bundle)
    devices, volume, backup = [], None, None
    if saved:
        mountpoint, snapshot_mountpoint = Path(saved["mountpoint"]), Path(saved["snapshot_mountpoint"])
        whole, devices, volume, backup = saved["whole"], saved["devices"], saved["volume"], Path(saved["backup"])
    else:
        mountpoint = Path(tempfile.mkdtemp(prefix="homelab-time-machine-"))
        snapshot_mountpoint = Path(tempfile.mkdtemp(prefix="homelab-time-machine-snapshot-"))
        whole = None
    repos = {}
    try:
        if saved:
            progress(f"Reusing {bundle.name}, still attached from an earlier step.")
        else:
            progress(f"Attaching {bundle.name} read-only (this can take a while over the network)...")
            with attach_heartbeat(bundle.name):
                whole, devices, volume = attach(bundle, mountpoint)
            backup = latest_backup(mountpoint, volume, snapshot_mountpoint)
            if keep:
                save_attachment(bundle=str(bundle.resolve()), nas_domain=NAS_DOMAIN, whole=whole, devices=devices, volume=volume,
                                mountpoint=str(mountpoint), snapshot_mountpoint=str(snapshot_mountpoint),
                                backup=str(backup))
        user = os.environ.get("USER") or Path.home().name
        progress(f"Latest backup: {backup.name}")
        if what == "dia-extensions":
            emit({"what": what, "backup": backup.name,
                              "extensions": list_dia_extensions(find_user_home(backup, user))})
            return
        if what == "private-data":
            emit(copy_private_data(backup, user, manifest, mode))
            return
        if what == "user-files":
            emit(copy_user_files(backup, user, manifest, mode))
            return
        if what == "system-settings":
            try:
                emit(copy_system_settings(backup, user, manifest, mode))
            except PermissionError as error:
                raise CopyError(FULL_DISK_ACCESS) from error
            return
        if what == "apps":
            emit(copy_apps(catalog, backup, user, manifest, mode))
            return
        if what == "vendor-prefs":
            emit(copy_apps(catalog, backup, user, manifest, mode, only))
            return
        try:
            if what == "dia":
                source = find_user_home(backup, user)
                files = plan_dia(source, mode)
                if not files:
                    raise CopyError(f"The latest backup has no Dia configuration for user {user}.")
            elif what == "folder":
                source = find_user_home(backup, user) / folder_name
                if not source.is_dir() or source.is_symlink():
                    raise CopyError(f"The latest backup has no {folder_name} folder for user {user}.")
                files = plan_copy(source, mode)
                if folder_name == "code" and mode == "PROD":
                    files, repos = filter_existing_repos(files, source, destination, manifest)
                    for repo_name in repos:
                        progress(f"[~/code] {repo_name} already exists here: copying only its untracked files "
                                 "and applying its uncommitted changes as a patch (its .git is not copied).")
            else:
                source = find_downloads(backup, user)
                files = plan_copy(source, mode)
        except PermissionError as error:
            raise CopyError(FULL_DISK_ACCESS) from error
        work_dir = manifest.parent / (manifest.stem + "-work")
        stage = work_dir / "stage" / backup.name
        needed = sum(max(0, path.lstat().st_size - staged_size(stage / relative)) for relative, path in files
                     if not ((destination / relative).exists() or (destination / relative).is_symlink()))
        destination.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(destination).free
        if needed + FREE_SPACE_MARGIN > free:
            raise CopyError(f"Not enough free space: copying needs {needed / 1024 ** 3:.1f} GiB "
                            f"plus 1 GiB headroom, but only {free / 1024 ** 3:.1f} GiB is free.")
        new = sum(1 for relative, _ in files
                  if not ((destination / relative).exists() or (destination / relative).is_symlink()))
        progress(f"[{what if what != 'folder' else '~/' + folder_name}] Copying {new} new item(s) ({needed / 1024 ** 2:.1f} MiB) into {destination}; "
                 f"{len(files) - new} already present will be kept...")
        copied, skipped = copy_files(files, source, destination, manifest, work_dir, backup.name)
        repo_results = apply_repo_patches(repos, destination, manifest, work_dir) if repos else []
        for item in repo_results:
            progress(f"[~/code] {item['repo']}: uncommitted changes: {item['status']}"
                     + (f" ({item['stashes']} stash(es) in the backup were not copied)" if item["stashes"] else ""))
        extensions = list_dia_extensions(source) if what == "dia" else []
        emit({"what": what, "backup": backup.name, "mode": mode, "copied": copied,
                          "extensions": extensions, "repos": repo_results,
                          "skipped_existing": skipped,
                          "copied_names": [str(relative) for relative, _ in files][:5]})
    finally:
        if keep and backup is not None:
            # Stays attached for the next copy step; provision.sh detaches it at the end.
            pass
        else:
            release_attachment({"snapshot_mountpoint": str(snapshot_mountpoint), "mountpoint": str(mountpoint),
                                "whole": whole, "devices": devices})


def finish_status(state, **detail):
    """Remember the outcome of this copy; never let bookkeeping break a copy."""
    if STEP is None:
        return
    try:
        copy_status.record(STEP, state, **detail)
    except (OSError, ValueError):
        pass


if __name__ == "__main__":
    try:
        main()
    except CopyError as error:
        finish_status("failed", error=str(error)[:300])
        sys.exit(str(error))
    else:
        detail = {key: value for key, value in RESULT.items()
                  if key != "what" and isinstance(value, (int, str)) and not isinstance(value, bool)}
        if RESULT.get("repos"):
            detail["repos"] = [{"repo": item["repo"], "status": item["status"]} for item in RESULT["repos"]]
        finish_status("done", **detail)
