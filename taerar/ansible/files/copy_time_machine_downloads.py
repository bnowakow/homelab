"""Copy ~/Downloads from the latest backup inside a Time Machine sparse bundle.

The bundle is attached read-only and always detached again. Nothing in the
backup is modified. Existing files in the destination are never overwritten.
Every created file and directory is recorded in a JSON-lines manifest before it
is created, so undo-provision.sh can remove exactly what this script added.

Usage: copy_time_machine_downloads.py BUNDLE DESTINATION MANIFEST PROD|TEST
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
        progress(result.stderr.decode(errors="replace").strip())
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
    listing = subprocess.run(["diskutil", "apfs", "listSnapshots", "-plist", volume],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    names = []
    if listing.returncode == 0:
        names = [snapshot.get("SnapshotName", "")
                 for snapshot in plistlib.loads(listing.stdout).get("Snapshots", [])]
    dates = sorted(match.group(1) for match in map(SNAPSHOT_NAME.fullmatch, names) if match)
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
        raise CopyError("No completed backups were found in this Time Machine bundle.")
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


def copy_files(files, destination, manifest):
    copied = skipped = 0
    for relative, source in files:
        target = destination / relative
        if target.exists() or target.is_symlink():
            skipped += 1
            continue
        # Create missing parent folders, recording each before it is made.
        missing = []
        parent = target.parent
        while not parent.exists():
            missing.append(parent)
            parent = parent.parent
        for folder in reversed(missing):
            record(manifest, {"type": "directory", "path": str(folder)})
            folder.mkdir()
        temporary = target.with_name(".homelab-partial-" + target.name)
        stat = source.lstat()
        record(manifest, {"type": "file", "path": str(target), "temporary": str(temporary),
                          "size": stat.st_size, "mtime_ns": stat.st_mtime_ns})
        if temporary.exists() or temporary.is_symlink():
            temporary.unlink()
        shutil.copy2(source, temporary, follow_symlinks=False)
        os.replace(temporary, target)
        copied += 1
    return copied, skipped


def main():
    if sys.argv[1:] == ["--check-access"]:
        # Used by full-disk-access_playbook.yml early in provisioning.
        print(json.dumps({"full_disk_access": full_disk_access_status(), "app": responsible_app()}))
        return
    bundle, destination, manifest, mode = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]
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
        source = find_downloads(backup, os.environ.get("USER") or Path.home().name)
        progress(f"Latest backup: {backup.name}")
        try:
            files = plan_copy(source, mode)
        except PermissionError as error:
            raise CopyError(FULL_DISK_ACCESS) from error
        needed = sum(path.lstat().st_size for relative, path in files
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
        copied, skipped = copy_files(files, destination, manifest)
        print(json.dumps({"backup": backup.name, "mode": mode, "copied": copied,
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
