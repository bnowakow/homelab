"""Copy ~/Downloads from the latest backup inside a Time Machine sparse bundle.

The bundle is attached read-only and always detached again. Nothing in the
backup is modified. Existing files in the destination are never overwritten.
Every created file and directory is recorded in a JSON-lines manifest before it
is created, so undo-provision.sh can remove exactly what this script added.

Usage: copy_time_machine_downloads.py BUNDLE DESTINATION MANIFEST PROD|TEST
Progress goes to stderr; a JSON summary is printed to stdout.
"""
import json
import os
from pathlib import Path
import plistlib
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


def progress(message):
    print(message, file=sys.stderr, flush=True)


ATTACH_FAILED = ("Could not attach the backup read-only. If Time Machine is currently "
                 "using it, wait until that backup finishes, then rerun provisioning.")


def image_devices(output):
    entities = plistlib.loads(output).get("system-entities", [])
    devices = [entity["dev-entry"] for entity in entities if "dev-entry" in entity]
    if not devices:
        raise CopyError("hdiutil attached the backup but reported no device.")
    return devices


def attach(bundle, mountpoint):
    """Attach read-only and mount the backup volume; return the image device."""
    # stdin stays on the terminal so hdiutil can ask for an image password.
    result = subprocess.run(
        ["hdiutil", "attach", "-readonly", "-nobrowse", "-noautoopen", "-plist",
         "-mountpoint", str(mountpoint), str(bundle)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode == 0:
        # The shortest entry (e.g. /dev/disk12) is the whole image.
        return min(image_devices(result.stdout), key=len)
    if b"no mountable file systems" not in result.stderr:
        progress(result.stderr.decode(errors="replace").strip())
        raise CopyError(ATTACH_FAILED)
    # An encrypted APFS backup stays locked here: attach without mounting,
    # unlock it with a terminal password prompt, then mount it read-only.
    result = subprocess.run(["hdiutil", "attach", "-readonly", "-nomount", "-plist", str(bundle)],
                            stdout=subprocess.PIPE)
    if result.returncode != 0:
        raise CopyError(ATTACH_FAILED)
    devices = image_devices(result.stdout)
    whole = min(devices, key=len)
    try:
        mount_apfs_backup(devices, mountpoint)
    except BaseException:
        detach(whole)
        raise
    return whole


def mount_apfs_backup(devices, mountpoint):
    stores = {device.removeprefix("/dev/") for device in devices}
    listing = subprocess.run(["diskutil", "apfs", "list", "-plist"], stdout=subprocess.PIPE)
    containers = plistlib.loads(listing.stdout).get("Containers", []) if listing.returncode == 0 else []
    volumes = [volume for container in containers
               if any(store.get("DeviceIdentifier") in stores for store in container.get("PhysicalStores", []))
               for volume in container.get("Volumes", [])]
    if not volumes:
        raise CopyError("The backup image contains no mountable APFS volume.")
    volume = next((volume for volume in volumes if "Backup" in volume.get("Roles", [])), volumes[0])
    identifier = volume["DeviceIdentifier"]
    mount = ["diskutil", "mount", "readOnly", "-mountPoint", str(mountpoint), identifier]
    if subprocess.run(mount, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        return
    if not (volume.get("Locked") or volume.get("Encryption") or volume.get("FileVault")):
        raise CopyError("Could not mount the backup volume read-only.")
    progress(f"The backup volume {volume.get('Name', identifier)} is encrypted. "
             "Enter the Time Machine backup password when asked (it is not stored).")
    if subprocess.run(["diskutil", "apfs", "unlockVolume", identifier, "-nomount"]).returncode != 0:
        raise CopyError("Could not unlock the encrypted backup. Check the backup password and rerun.")
    if subprocess.run(mount, stdout=subprocess.DEVNULL).returncode != 0:
        raise CopyError("Could not mount the unlocked backup volume read-only.")


def detach(device):
    for arguments in (["hdiutil", "detach", device], ["hdiutil", "detach", "-force", device]):
        if subprocess.run(arguments, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            return True
    progress(f"Warning: could not detach {device}. Eject it in Finder or run: hdiutil detach {device}")
    return False


def latest_backup(mountpoint):
    """Return the newest backup directory, mounting its snapshot if needed."""
    result = subprocess.run(["tmutil", "listbackups", "-d", str(mountpoint)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if "Full Disk Access" in result.stderr + result.stdout:
        raise CopyError(FULL_DISK_ACCESS)
    backups = [Path(line) for line in result.stdout.splitlines() if line.strip()]
    backups = [path for path in backups if path.is_dir()]
    if not backups:
        # HFS+ backups keep a Latest link per computer; APFS needs tmutil above.
        try:
            backups = sorted(path for path in (mountpoint / "Backups.backupdb").glob("*/Latest")
                             if path.is_dir())
        except PermissionError as error:
            raise CopyError(FULL_DISK_ACCESS) from error
    if not backups:
        raise CopyError("No completed backups were found in this Time Machine bundle.")
    return max(backups, key=lambda path: path.name)


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
    bundle, destination, manifest, mode = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]
    if mode not in ("PROD", "TEST"):
        raise CopyError("Environment must be PROD or TEST.")
    if not bundle.is_dir():
        raise CopyError(f"Backup not found: {bundle}. Connect the Time Machine share in Finder.")
    mountpoint = Path(tempfile.mkdtemp(prefix="homelab-time-machine-"))
    device = None
    try:
        progress(f"Attaching {bundle.name} read-only (this can take a while over the network)...")
        device = attach(bundle, mountpoint)
        backup = latest_backup(mountpoint)
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
        progress(f"Copying {len(files)} item(s) ({needed / 1024 ** 2:.1f} MiB) into {destination}...")
        copied, skipped = copy_files(files, destination, manifest)
        print(json.dumps({"backup": backup.name, "mode": mode, "copied": copied,
                          "skipped_existing": skipped,
                          "copied_names": [str(relative) for relative, _ in files][:5]}))
    finally:
        if device:
            detach(device)
        try:
            mountpoint.rmdir()
        except OSError:
            pass


if __name__ == "__main__":
    try:
        main()
    except CopyError as error:
        sys.exit(str(error))
