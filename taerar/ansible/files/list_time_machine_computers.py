"""List top-level backup directories without opening or modifying bundles."""
import json
import os
from pathlib import Path
import re
import sys


def scan_computers(volume):
    groups = {}
    with os.scandir(volume) as entries:
        for entry in entries:
            if not entry.name.endswith(".sparsebundle") or not entry.is_dir(follow_symlinks=False):
                continue
            name = entry.name[:-len(".sparsebundle")]
            match = re.fullmatch(r"(.+) (\d+(?:\.\d+)*)", name)
            computer, version = match.groups() if match else (name, None)
            group = groups.setdefault(computer, {"name": computer, "unversioned": False,
                                                  "versions": []})
            if version is None:
                group["unversioned"] = True
            else:
                group["versions"].append(version)
    result = sorted(groups.values(), key=lambda item: (item["name"].casefold(), item["name"]))
    for group in result:
        group["versions"].sort(key=lambda version: (tuple(map(int, version.split("."))), version))
    return result


def display_name(value):
    return "".join(character if character.isprintable() else "?" for character in value)


def format_computers(computers):
    lines = []
    for computer in computers:
        lines.append("- " + display_name(computer["name"]))
        if computer["versions"] and computer["unversioned"]:
            lines.append("\t- (unversioned)")
        lines.extend("\t- " + version for version in computer["versions"])
    return "\n".join(lines) if lines else "(No .sparsebundle directories found.)"


def main():
    volume = Path(sys.argv[1] if len(sys.argv) > 1 else "/Volumes/time-machine")
    try:
        computers = scan_computers(volume)
    except OSError as error:
        print("Cannot read Time Machine share. Connect it in Finder before continuing: "
              + str(error), file=sys.stderr)
        return 1
    for computer in computers:
        computer["display"] = format_computers([computer])
    print(json.dumps({"computers": computers, "display": format_computers(computers)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
