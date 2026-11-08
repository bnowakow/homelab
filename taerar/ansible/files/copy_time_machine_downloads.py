"""Copy ~/Downloads from the latest backup inside a Time Machine sparse bundle.

The bundle is attached read-only and always detached again. Nothing in the
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

SKIPPED_NAMES = {".DS_Store", ".localized"}
# Keep headroom so copying cannot fill the disk completely.
FREE_SPACE_MARGIN = 1024 ** 3
FULL_DISK_ACCESS = (
    "macOS denied access to the backup contents. Give the terminal app running "
    "provisioning Full Disk Access (System Settings > Privacy & Security > Full Disk "
    "Access), quit and reopen it, then rerun provisioning.")


class CopyError(Exception):
    pass


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
        result = subprocess.run(arguments, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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


def main():
    if sys.argv[1:] == ["--check-access"]:
        # Used by full-disk-access_playbook.yml early in provisioning.
        print(json.dumps({"full_disk_access": full_disk_access_status(), "app": responsible_app()}))
        return
    args = sys.argv[1:]
    what = "downloads"
    if args and args[0] in ("--dia", "--dia-extensions"):
        what, args = args[0][2:], args[1:]
    if what == "dia-extensions":
        args += ["-", "-", "PROD"]  # listing only: no destination, manifest or copy
    bundle, destination, manifest, mode = Path(args[0]), Path(args[1]), Path(args[2]), args[3]
    if mode not in ("PROD", "TEST"):
        raise CopyError("Environment must be PROD or TEST.")
    if not bundle.is_dir():
        raise CopyError(f"Backup not found: {bundle}. Connect the Time Machine share in Finder.")
    # Fail in seconds, before the slow network attach, when access is missing.
    check_full_disk_access()
    mountpoint = Path(tempfile.mkdtemp(prefix="homelab-time-machine-"))
    snapshot_mountpoint = Path(tempfile.mkdtemp(prefix="homelab-time-machine-snapshot-"))
    whole = None
    try:
        progress(f"Attaching {bundle.name} read-only (this can take a while over the network)...")
        whole, devices, volume = attach(bundle, mountpoint)
        backup = latest_backup(mountpoint, volume, snapshot_mountpoint)
        user = os.environ.get("USER") or Path.home().name
        progress(f"Latest backup: {backup.name}")
        if what == "dia-extensions":
            print(json.dumps({"what": what, "backup": backup.name,
                              "extensions": list_dia_extensions(find_user_home(backup, user))}))
            return
        try:
            if what == "dia":
                source = find_user_home(backup, user)
                files = plan_dia(source, mode)
                if not files:
                    raise CopyError(f"The latest backup has no Dia configuration for user {user}.")
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
        progress(f"Copying {new} new item(s) ({needed / 1024 ** 2:.1f} MiB) into {destination}; "
                 f"{len(files) - new} already present will be kept...")
        copied, skipped = copy_files(files, source, destination, manifest, work_dir, backup.name)
        extensions = list_dia_extensions(source) if what == "dia" else []
        print(json.dumps({"what": what, "backup": backup.name, "mode": mode, "copied": copied,
                          "extensions": extensions,
                          "skipped_existing": skipped,
                          "copied_names": [str(relative) for relative, _ in files][:5]}))
    finally:
        # Unmount our snapshot mount first; it would otherwise keep the image busy.
        if os.path.ismount(snapshot_mountpoint):
            subprocess.run(["umount", str(snapshot_mountpoint)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if whole:
            detach(whole, devices)
        for folder in (snapshot_mountpoint, mountpoint):
            try:
                folder.rmdir()
            except OSError:
                pass


if __name__ == "__main__":
    try:
        main()
    except CopyError as error:
        sys.exit(str(error))
