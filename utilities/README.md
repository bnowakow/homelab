# Utilities

Small helper scripts that do not belong to one of the more specific service
directories in this repository.

## Contents

### `codex-commit.sh`

Interactive helper for committing staged changes with a Codex-generated commit
message.

What it does:

- checks that there are staged changes,
- finds the Codex CLI from `PATH` or common Codex.app install locations,
- asks Codex to propose a concise commit message for the staged diff,
- commits with that message,
- shows the current git status,
- checks the upstream branch before pushing,
- can offer to pull with rebase if the branch is behind,
- prompts before pushing and can show the last commit diff before push.

Use it from the repository root:

```bash
utilities/codex-commit.sh
```

The top-level repository `Makefile` also exposes it as:

```bash
make codex-commit
```

### `png-to-a4-pdf/`

Standalone Python utility for converting one very tall PNG, such as a long
screenshot, into a multi-page A4 PDF.

Files:

- `png-to-a4-pdf.py` contains the converter.
- `requirements.txt` lists Python dependencies.
- `Makefile` creates the local venv, installs requirements, and runs the tool.

What the converter does:

- scales the PNG to match the printable width of A4 paper,
- splits the scaled image vertically across as many A4 pages as needed,
- adds a centered footer on every page in the form `Page x of y`,
- supports margins, page overlap, DPI, and background color,
- expands `~` in input and output paths.

Run it through Make:

```bash
cd utilities/png-to-a4-pdf
make png-to-a4-pdf INPUT=/path/to/input.png OUTPUT=~/Downloads/output.pdf
```

Optional arguments can be passed with `PNG_TO_A4_PDF_ARGS`:

```bash
make png-to-a4-pdf \
  INPUT=~/Downloads/long.png \
  OUTPUT=~/Downloads/long.pdf \
  PNG_TO_A4_PDF_ARGS='--margin-mm 10 --overlap-mm 5'
```

Direct usage through the virtual environment:

```bash
cd utilities/png-to-a4-pdf
make install-requirements
.venv/bin/python png-to-a4-pdf.py /path/to/input.png ~/Downloads/output.pdf
```

Useful options:

```bash
.venv/bin/python png-to-a4-pdf.py input.png output.pdf --margin-mm 10
.venv/bin/python png-to-a4-pdf.py input.png output.pdf --overlap-mm 5
.venv/bin/python png-to-a4-pdf.py input.png output.pdf --dpi 300
.venv/bin/python png-to-a4-pdf.py input.png output.pdf --background white
```

Generated local files:

- `png-to-a4-pdf/.venv` is created by `make venv` or
  `make install-requirements`.
- `.venv` is local machine state and should not be committed.

## Network timeout retries

`make codex-commit` and `make claude-commit` retry Git fetch, pull and push
transport timeouts up to three total attempts, waiting 2 seconds and then
4 seconds. Each retry is announced. Authentication failures, rejected pushes
and merge/rebase conflicts are not retried. Retries do not recreate the commit;
exhausted retries leave the local commit intact and show network/SSH guidance.
