"""Versioned, atomic local answer store. Never store authentication material."""
import json
import os
import re
from pathlib import Path
import sys
import tempfile


def load(path):
    if not path.exists():
        return {"schema_version": 2, "answers": {}}
    data = json.loads(path.read_text())
    if data.get("schema_version") == 1:
        data = {"schema_version": 2, "answers": {"time_machine.result": data}}
    if data.get("schema_version") != 2 or not isinstance(data.get("answers"), dict):
        raise ValueError("Unsupported choices file; remove it to start over")
    answers = data["answers"]
    for key in ("onepassword.signin", "onepassword.setup", "finder.time_machine_connected"):
        if key in answers and answers[key] is not True:
            raise ValueError("Invalid confirmation: " + key)
    for step, values in {"action": ["1", "2"], "create": ["1", "2"],
                         "version": ["1", "2"], "major": ["1", "2"]}.items():
        if "time_machine." + step in answers and answers["time_machine." + step] not in values:
            raise ValueError("Invalid Time Machine step: " + step)
    if "time_machine.result" in answers:
        result = answers["time_machine.result"]
        if (not isinstance(result, dict) or result.get("action") != "create"
                or result.get("create_type") != "new_version" or result.get("version_type") != "major"
                or not isinstance(result.get("computer"), str) or not result["computer"]
                or not isinstance(result.get("version"), str)
                or not re.fullmatch(r"[0-9]+\.[0-9]+", result["version"])):
            raise ValueError("Invalid completed Time Machine selection")
    return data


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
    rows = []

    def collect(values, prefix=""):
        for key, value in values.items():
            name = prefix + key
            if isinstance(value, dict) and value:
                collect(value, name + ".")
            else:
                # JSON quoting keeps each value on one line, even with newlines.
                rows.append((name, json.dumps(value, ensure_ascii=False)))

    collect(answers)
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
        # Changing an earlier Time Machine answer invalidates dependent answers.
        order = ["action", "create", "computer", "version", "major", "manual"]
        step = key.removeprefix("time_machine.")
        if step in order and data["answers"].get(key) != value:
            for later in order[order.index(step) + 1:] + ["result"]:
                data["answers"].pop("time_machine." + later, None)
        data["answers"][key] = value
        save(path, data)
    else:
        raise ValueError("Unknown store operation")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as error:
        sys.exit("Cannot use wizard choices: " + str(error))
