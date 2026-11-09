"""Versioned, atomic local answer store. Never store authentication material."""
import json
import os
import re
from pathlib import Path
import sys
import tempfile



# Stable stored values are independent of menu positions and visible labels.
HOME_FOLDERS = ["Documents", "Downloads", "Movies", "Music", "Pictures",
                "Desktop", "code", "Sync", "go", "Public"]
MENU_VALUES = {
    "action": {"1": "create", "2": "restore"},
    "create": {"1": "new_version", "2": "new_computer"},
    "version": {"1": "minor", "2": "major"},
    "major": {"1": "suggested", "2": "manual"},
}


def normalize_answers(answers):
    for step, mapping in MENU_VALUES.items():
        key = "time_machine." + step
        if isinstance(answers.get(key), str):
            answers[key] = mapping.get(answers[key], answers[key])
    # Older stores kept only the source; recover its exact number if available.
    major = answers.get("time_machine.major")
    if isinstance(major, str):
        result = answers.get("time_machine.result", {})
        version = result.get("version") if isinstance(result, dict) else None
        if not version and major == "manual":
            version = answers.get("time_machine.manual")
        answers["time_machine.major"] = {"source": major, "version": version}
    source = answers.get("time_machine.major", {})
    result = answers.get("time_machine.result", {})
    number = (result.get("version") if isinstance(result, dict) else None) or (
        source.get("version") if isinstance(source, dict) else None) or answers.get("time_machine.manual")
    if number:
        answers["time_machine.number"] = number
    return answers


def menu_selection(answers, step):
    value = normalize_answers(dict(answers)).get("time_machine." + step, "")
    if step == "major" and isinstance(value, dict):
        value = value.get("source", "")
    return next((tag for tag, meaning in MENU_VALUES.get(step, {}).items()
                 if meaning == value), value)


def load(path):
    if not path.exists():
        return {"schema_version": 2, "answers": {}}
    data = json.loads(path.read_text())
    if data.get("schema_version") == 1:
        data = {"schema_version": 2, "answers": {"time_machine.result": data}}
    if data.get("schema_version") != 2 or not isinstance(data.get("answers"), dict):
        raise ValueError("Unsupported choices file; remove it to start over")
    answers = normalize_answers(data["answers"])
    for key in ("onepassword.signin", "onepassword.setup", "onepassword.autofill",
                "finder.time_machine_connected"):
        if key in answers and answers[key] is not True:
            raise ValueError("Invalid confirmation: " + key)
    for step, mapping in MENU_VALUES.items():
        value = answers.get("time_machine." + step)
        if step == "major" and isinstance(value, dict):
            number = value.get("version")
            if number is not None and (not isinstance(number, str) or not re.fullmatch(r"[0-9]+\.[0-9]+", number)):
                raise ValueError("Invalid chosen version")
            value = value.get("source")
        if value is not None and value not in mapping.values():
            raise ValueError("Invalid Time Machine step: " + step)
    if "time_machine.result" in answers:
        result = answers["time_machine.result"]
        if (not isinstance(result, dict) or result.get("action") != "create"
                or result.get("create_type") != "new_version" or result.get("version_type") not in ["major", "minor"]
                or not isinstance(result.get("computer"), str) or not result["computer"]
                or not isinstance(result.get("version"), str)
                or not re.fullmatch(r"[0-9]+\.[0-9]+", result["version"])):
            raise ValueError("Invalid completed Time Machine selection")
    # Results saved before the copy question existed have no copy_from.
    for value in [answers.get("time_machine.copy"),
                  answers.get("time_machine.result", {}).get("copy_from", None)]:
        if value is not None and not valid_copy_source(value):
            raise ValueError("Invalid version to copy data from")
    if answers.get("time_machine.downloads", "copy") not in ["copy", "skip"]:
        raise ValueError("Invalid Downloads copy choice")   # legacy key, no longer asked
    folders = answers.get("time_machine.folders", [])
    if (not isinstance(folders, list) or len(set(folders)) != len(folders)
            or any(folder not in HOME_FOLDERS for folder in folders)):
        raise ValueError("Invalid home folders choice")
    if answers.get("time_machine.user_files", "copy") not in ["copy", "skip"]:
        raise ValueError("Invalid user files copy choice")
    if answers.get("time_machine.private_data", "copy") not in ["copy", "skip"]:
        raise ValueError("Invalid private data copy choice")
    if answers.get("time_machine.settings", "copy") not in ["copy", "skip"]:
        raise ValueError("Invalid settings copy choice")
    if answers.get("time_machine.apps", "copy") not in ["copy", "skip"]:
        raise ValueError("Invalid apps copy choice")
    if answers.get("time_machine.dia", "copy") not in ["copy", "skip"]:
        raise ValueError("Invalid Dia copy choice")
    if answers.get("time_machine.dia_extensions", "open") not in ["open", "skip"]:
        raise ValueError("Invalid Dia extensions choice")
    return data


def valid_copy_source(value):
    return isinstance(value, str) and (value in ["none", "unversioned"]
                                       or re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", value) is not None)


def copy_source_label(value):
    if value == "none":
        return "Do not copy data (start with an empty backup)"
    if value == "unversioned":
        return "Copy from the unversioned backup"
    return "Copy from version " + value


def save(path, data):
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".wizard-choices-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)



def format_summary(answers):
    answers = normalize_answers(dict(answers))
    labels = {
        "onepassword.signin": "1Password sign-in",
        "onepassword.setup": "1Password SSH agent and Safari setup",
        "onepassword.autofill": "1Password macOS AutoFill setup",
        "finder.time_machine_connected": "Finder Time Machine share connection",
        "time_machine.action": "Time Machine action",
        "time_machine.create": "Computer setup",
        "time_machine.computer": "Computer",
        "time_machine.version": "Version type",
        "time_machine.major": "Version selection",
        "time_machine.manual": "Manually entered version",
        "time_machine.copy": "Copy data from",
        "time_machine.downloads": "Copy ~/Downloads from backup (legacy answer)",
        "time_machine.folders": "Copy home folders from backup",
        "time_machine.apps": "Copy apps and preferences from backup",
        "time_machine.settings": "Copy Finder and system settings from backup",
        "time_machine.user_files": "Copy user files (ssh, fonts, dotfiles, login items) from backup",
        "time_machine.private_data": "Copy keychains, Safari, Messages and Mail data from backup",
        "time_machine.dia": "Copy Dia settings from backup",
        "time_machine.dia_extensions": "Reinstall Dia extensions",
    }
    meanings = {"create": "Create a new computer or version", "restore": "Restore an existing computer",
                "new_version": "New version of an existing computer", "new_computer": "Entirely new computer",
                "minor": "New minor version (macOS upgrade or fresh installation)",
                "major": "New major version (newly bought computer)"}
    rows = []
    for key, value in answers.items():
        if key in ["time_machine.result", "time_machine.number"]:
            continue
        if key == "time_machine.major" and isinstance(value, dict):
            source = "Suggested version" if value.get("source") == "suggested" else "Manual version"
            rendered = source + (": " + value["version"] if value.get("version") else " (number not recorded in older file)")
        elif key == "time_machine.copy":
            rendered = copy_source_label(value)
        elif key == "time_machine.dia_extensions":
            rendered = "Open the Chrome Web Store pages" if value == "open" else "Do not open"
        elif key == "time_machine.folders":
            rendered = ", ".join("~/" + folder for folder in value) if value else "None"
        elif key in ("time_machine.downloads", "time_machine.dia", "time_machine.apps", "time_machine.settings", "time_machine.user_files", "time_machine.private_data"):
            rendered = "Copy from the latest backup" if value == "copy" else "Do not copy"
        elif value is True:
            rendered = "Confirmed"
        elif isinstance(value, str):
            rendered = meanings.get(value, value) if key in labels and key not in ["time_machine.computer", "time_machine.manual"] else value
        else:
            rendered = json.dumps(value, ensure_ascii=False)
        rows.append((labels.get(key, key.replace("_", " ").replace(".", " / ")), rendered))
    result = answers.get("time_machine.result", {})
    for field, key in [("action", "action"), ("create_type", "create"), ("computer", "computer"), ("version_type", "version")]:
        if field in result and "time_machine." + key not in answers:
            rows.append((labels["time_machine." + key], meanings.get(result[field], result[field])))
    if "copy_from" in result and "time_machine.copy" not in answers:
        rows.append((labels["time_machine.copy"], copy_source_label(result["copy_from"])))
    source = answers.get("time_machine.major", {})
    number = (answers.get("time_machine.number") or result.get("version")
              or (source.get("version") if isinstance(source, dict) else None)
              or answers.get("time_machine.manual"))
    if any(key.startswith("time_machine.") for key in answers):
        rows.append(("Version number", number or "Not selected yet"))
    # Escape control characters so a saved answer stays on one displayed line.
    rows = [(key, str(value).replace("\n", r"\n").replace("\r", r"\r").replace("\t", " ")) for key, value in rows]
    width = max((len(key) for key, _ in rows), default=0)
    return "\n".join(f"{key:<{width}} : {value}" for key, value in rows)


def main():
    path = Path(sys.argv[1])
    data = load(path)
    if sys.argv[2] == "read":
        print(json.dumps(data))
    elif sys.argv[2] == "summary":
        print(format_summary(data["answers"]))
    elif sys.argv[2] == "put":
        key, value = sys.argv[3], json.loads(sys.argv[4])
        step = key.removeprefix("time_machine.")
        if step in MENU_VALUES and isinstance(value, str):
            value = MENU_VALUES[step].get(value, value)
        # Changing an earlier Time Machine answer invalidates dependent answers.
        order = ["action", "create", "computer", "version", "major", "manual", "copy"]
        step = key.removeprefix("time_machine.")
        existing = data["answers"].get(key)
        # Replaying "Specify version manually" must not discard the number that
        # the following manual-input step saved earlier.
        if (step == "major" and value == {"source": "manual", "version": None}
                and isinstance(existing, dict) and existing.get("source") == "manual"):
            value = existing
        if step in order and existing != value:
            # downloads is asked after the wizard, so it depends on every step
            # but changing it does not invalidate the completed result.
            for later in order[order.index(step) + 1:] + ["result", "number", "downloads", "folders", "apps", "settings", "user_files", "private_data", "dia", "dia_extensions"]:
                data["answers"].pop("time_machine." + later, None)
        data["answers"][key] = value
        if key in ["time_machine.major", "time_machine.result"] and isinstance(value, dict) and value.get("version"):
            data["answers"]["time_machine.number"] = value["version"]
        elif key == "time_machine.manual":
            data["answers"]["time_machine.number"] = value
        save(path, data)
    else:
        raise ValueError("Unknown store operation")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as error:
        sys.exit("Cannot use wizard choices: " + str(error))
