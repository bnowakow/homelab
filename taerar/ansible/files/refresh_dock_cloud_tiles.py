#!/usr/bin/env python3
"""Show Dock folder tiles in cloud storage as a Stack and restart the Dock once.

A tile shown as a Folder ("displayas" 1) in ~/Library/CloudStorage (e.g. Dropbox/Zrzuty ekranu)
renders as a "?" icon; a Stack ("displayas" 0) shows previews of its files. Restoring the Dock
settings from a backup can bring the Folder style back, so it is set to Stack here.

The Dock settings are restored (and the Dock restarted) long before Dropbox is signed in
and has synced its folders, so a tile such as ~/Library/CloudStorage/Dropbox/Zrzuty ekranu
shows a "?" icon instead of a stack of its files until the Dock is restarted again.

Restarts the Dock once when a folder tile points into ~/Library/CloudStorage and the folder
exists now. For tiles whose folder does not exist yet (the cloud app is not signed in or has
not synced), prints a note to run `killall Dock` later. Prints nothing and does nothing when
there are no such tiles.
"""
import plistlib
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse


def read_dock():
    result = subprocess.run(["defaults", "export", "com.apple.dock", "-"], capture_output=True)
    return plistlib.loads(result.stdout) if result.returncode == 0 else {}


def cloud_tile_data(dock):
    """(label, folder path, tile-data dict) of every Dock folder tile inside cloud storage."""
    others = dock.get("persistent-others", [])
    cloud = str(Path.home() / "Library" / "CloudStorage") + "/"
    tiles = []
    for tile in others:
        data = tile.get("tile-data", {})
        url = data.get("file-data", {}).get("_CFURLString", "")
        path = unquote(urlparse(url).path)
        if path.startswith(cloud):
            tiles.append((data.get("file-label", Path(path).name), Path(path), data))
    return tiles


def cloud_tiles():
    return [(label, path) for label, path, _ in cloud_tile_data(read_dock())]


def set_stack(dock):
    """Switch Folder-style cloud tiles to Stack; returns the labels that changed."""
    changed = [label for label, _, data in cloud_tile_data(dock) if data.get("displayas") == 1]
    for _, _, data in cloud_tile_data(dock):
        if data.get("displayas") == 1:
            data["displayas"] = 0
    if changed:
        subprocess.run(["defaults", "import", "com.apple.dock", "-"], input=plistlib.dumps(dock), check=True)
    return changed


def main():
    dock = read_dock()
    changed = set_stack(dock)
    if changed:
        print("Set the Dock tile(s) to show as a Stack: " + ", ".join(changed))
    tiles = cloud_tiles()
    if not tiles:
        return 0
    present = [label for label, path in tiles if path.is_dir()]
    missing = [label for label, path in tiles if not path.is_dir()]
    if present:
        subprocess.run(["killall", "Dock"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("Restarted the Dock so the cloud folder tile(s) show their files: " + ", ".join(present))
    if missing:
        print("Dock tile(s) point to cloud folders that do not exist yet: " + ", ".join(missing)
              + ". Sign in to the cloud app, wait for it to sync, then run: killall Dock", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
