# macOS provisioning

This directory will contain the Ansible setup for provisioning macOS machines,
following the approach used by [`nas/ansible/provision.sh`](../../nas/ansible/provision.sh)
for Linux servers.

This document records the manual preparation required before running Ansible.
`provision.sh` currently creates `~/code`, clones this repository if missing, and
ensures Homebrew and Ansible are installed, then runs local Ansible playbooks
to install 1Password for Mac and Safari, apply dotfiles, install GitHub CLI,
install Prezto/Powerlevel10k, log in through the browser, and switch origin to SSH.

## Manual steps before running Ansible

1. Open the Mac App Store and sign in to your Apple Account. This is required
   for the Safari extension installation; confirm your account is shown in the
   App Store before running provisioning.
2. For `--unattended`, run `sudo -v` in the same Terminal first if administrator
   authorization is not already cached. App Store/Touch ID/password prompts
   still require you to approve them.

Additional manual prerequisites will be added here as they are identified.

For each step, document:

- The action to perform, including any commands or macOS settings paths.
- Any required choices or values.
- How to verify that the step is complete.

## Running provisioning

On a new Mac, run as your normal user, without `sudo`. This downloads and executes
the script from the `master` branch:

```sh
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/bnowakow/homelab/master/taerar/ansible/provision.sh)" -- --unattended
```

The examples use `--unattended` to automatically proceed past installation
confirmations such as `Do you want to proceed with the installation? [y/n]`.
Omit `--unattended` to keep interactive prompts. The Homebrew installer requires
existing sudo authorization for unattended installation; if necessary, run
`sudo -v` first. Passwords and macOS permission dialogs cannot be auto-answered.

To find the command: open [bnowakowski.pl](http://bnowakowski.pl), follow the
GitHub link, open the `homelab` repository, and browse to
`taerar/ansible/provision.sh`.

The script creates `~/code` if needed and clones
`https://github.com/bnowakow/homelab.git` into `~/code/homelab` if that path is
missing. Existing checkouts are reused. After GitHub login, an Ansible playbook changes
`origin` to `git@github.com:bnowakow/homelab.git`. Git must be available to clone; on a fresh
Mac, complete any macOS Command Line Tools installation prompt and rerun the
script if needed.

From an existing checkout, you can also run from the repository root:

```sh
./taerar/ansible/provision.sh --unattended
```

The script reuses Homebrew if it is on `PATH` or in either standard macOS
installation location (`/opt/homebrew` or `/usr/local`). Otherwise, it runs the
official Homebrew installer, which may prompt for your password and confirmation.
It loads Homebrew's environment for subsequent steps in the script.

Next it reuses an existing `ansible-playbook` command, or installs the `ansible`
Homebrew formula and verifies it with `ansible-playbook --version`. Missing
formulae are recorded before installation so partial installs can be undone.
Local playbooks explicitly use `ansible_playbook_python`, the interpreter running
Ansible, rather than discovering `/usr/bin/python3`. No separate Python manager
or additional Python installation is required for these playbooks.

## Undoing provisioning during testing

Use a JSON object with `--keep` to select what survives undo:

```sh
./taerar/ansible/undo-provision.sh --keep '{"gh":"preferences","1password":"app"}'
```

| Entry | Behavior |
| --- | --- |
| `"gh":"preferences"` | Retain GitHub authentication/configuration and its rollback snapshot; remove gh if provisioning installed it. |
| `"1password":"app"` | Retain both 1Password apps, preferences/vault data, mas, rollback records, and provisioning-installed Homebrew so the cask stays managed. |
| `"1password":"preferences"` | Remove provisioning-installed apps while preserving preferences and vault data (also the default 1Password behavior). |

To remove both applications but retain their account information:

```sh
./taerar/ansible/undo-provision.sh --keep '{"gh":"preferences","1password":"preferences"}'
```

Omitted entries use normal undo behavior. An empty object is equivalent to
omitting `--keep`. Malformed JSON, duplicate keys, unknown keys and unsupported
values are rejected before undo begins. Python 3 parses JSON before any packages
are removed; it is supplied by Command Line Tools or Homebrew.

Rerun provisioning to reinstall removed tools and reuse retained credentials.
For a full test, omit `--keep` while gh is installed. Undo removes only the
provisioning-created login and restores the original GitHub CLI/global Git
configuration; changes after that snapshot are replaced. Pre-existing valid
logins are preserved. 1Password preferences and vault data are always preserved.
Undo quits 1Password only if provisioning opened it, unless the app is retained.

Before any rollback, undo checks `~/code/homelab` for staged, unstaged and
untracked files. If found, a dialog offers:

- **Commit:** stage all changes with `git add -A`, run `make codex-commit`, then
  recheck. Its normal commit/push prompts remain interactive.
- **Ignore:** continue undo, accepting that local changes may be deleted.
- **Abort:** end without rollback. Escape also aborts; Abort is the default choice.

If the commit command fails, undo stops. If it leaves changes, the dialog appears
again. When dialog or an interactive terminal is unavailable, undo stops safely.
Commit and push work you want to preserve before deleting a provisioning-created
checkout; making a local commit alone does not preserve it outside that checkout.

Run as your normal user, without `sudo`:

```sh
./taerar/ansible/undo-provision.sh
```

Or download and run the version from `master`:

```sh
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/bnowakow/homelab/master/taerar/ansible/undo-provision.sh)"
```

Provisioning records its changes in `~/.homelab-provision-state`, including
attempts that may fail partway through. Undo reverses them in order: remove Makefile dependency packages, restore
the original origin URLs, undo the GitHub login unless retained, remove
the newly created Prezto checkout/runcom links, restore dotfiles, remove GitHub
CLI and its new dependencies, remove
the newly installed Safari extension, 1Password desktop app and mas dependencies,
remove early dialog dependencies, then remove Ansible and its newly installed dependencies, uninstall Homebrew using the
official uninstaller without confirmation, remove the cloned
`~/code/homelab` checkout **including any local changes**, then remove `~/code`
only if empty. Resources that existed before provisioning are preserved. State
is retained when an undo step fails so you can rerun it. Do not run provisioning
and undo concurrently.

Runs made before tracking was added cannot be automatically undone. Homebrew's
installer may install Apple's Command Line Tools; undo does not remove those.
The Homebrew uninstaller may also report leftover files requiring manual cleanup.
Environment changes made inside the provisioning process expire when it exits.
Ansible rollback preserves formulae present before installation; it does not
restore upgraded dependency versions. If another package now requires a tracked
formula, Homebrew may refuse removal; resolve that dependency before retrying.

## Running Ansible

After bootstrap, `terminal-ui_playbook.yml` installs `dialog` and records its
new dependencies for undo. Other Makefile dependencies still run later.
Then `provision.sh` runs `onepassword_playbook.yml` before GitHub
login. It installs the Homebrew `1password` cask and `mas` if missing, then uses
`mas get 1569813296` to install [1Password for Safari](https://apps.apple.com/us/app/1password-for-safari/id1569813296?mt=12).
Existing applications are preserved. The Mac App Store can require Apple Account
or administrator authentication even with `--unattended`.

After installation, sign in to 1Password for Mac and enable **Safari > Settings >
Extensions > 1Password for Safari**, approving website permissions as needed.
Provisioning opens 1Password and first checks whether its SSH agent can perform
a local sign/verify operation (`ssh-add -T`) with an offered key. This may show a
native 1Password authorization prompt. If verified, the sign-in dialog is skipped.
Otherwise it asks you to confirm sign-in and unlock before continuing.
After sign-in is confirmed or verified, Ansible configures a GitHub-only
`IdentityAgent` block in `~/.ssh/config`, unless already configured correctly.
It snapshots the original file and permissions before editing. Next it checks
agent availability, GitHub authentication with `ssh -T git@github.com`, and
Safari extension enabled state. The SSH test recognizes GitHub's authenticated
greeting with exit code 1 as success, allows up to 30 seconds for authorization,
and distinguishes key rejection, host verification, network and timeout failures.
The dialog lists only checks still needing attention; unknown Safari state does
not cause it to ask you to redo working SSH setup. The
second dialog appears only if at least one status is not ready or unknown and
its confirmation was not explicitly reused from saved answers. It asks you
to enable **1Password > Settings > Developer > Use the SSH Agent**, make your SSH
key available, register its public key on GitHub,
and enable the Safari extension. It then checks the 1Password agent with
`ssh-add -l` using its socket. If unavailable or empty, provisioning stops with
instructions. Unsaved incomplete/unverified checkpoints still appear with `--unattended`; Abort/Escape
aborts. Safari detection reads extension-state plists for default and discovered
profiles. Unreadable, missing, conflicting or unrecognized state falls back to
manual confirmation; app installation/PlugInKit registration is not proof of
activation. Saved confirmations can suppress repeated manual prompts when explicitly reused;
live checks still run and the final agent/repository checks must pass.
No app settings are changed before the sign-in confirmation. Undo removes only applications/tools installed
by provisioning. It does not use Homebrew `--zap` or delete vault data, app
preferences, or Apple Account purchase history. `--keep` with `"gh":"preferences"` retains
GitHub authentication; include `"1password":"app"` to retain both apps.

Next, `dotfiles_playbook.yml` runs `make install-dotfiles` from `~/code/homelab`.
It uses the existing shared file list and hostname-specific `.screenrc` mapping.
During provisioning, changed dotfiles are snapshotted under
`~/.homelab-provision-state/dotfiles` before replacement. Undo restores original
files (including symlinks and permissions) and removes files that were originally
absent. Repeated runs keep the original snapshots. As with other test rollback,
changes you make to installed dotfiles afterward are replaced by undo.
GitHub rollback also preserves the pre-dotfiles `.gitconfig` baseline so keeping
GitHub preferences through one undo does not reinstall dotfiles on a later full
undo. Older snapshots are handled when restoring this shared file.
Standalone `make install-dotfiles` retains its usual `~/.homelab-dotfiles-backup.*`
backups and does not participate in provisioning rollback.

Next, `prezto_playbook.yml` reuses [the shared Prezto role](../../ansible/roles/prezto/README.md)
also used by `nas/ansible/proxmox-prezto_playbook.yml`. It installs `~/.zprezto`
with recursive submodules, including Powerlevel10k, then links missing Zsh runcom
files. Dotfiles already installed above select Powerlevel10k and supply its config.
macOS uses `/bin/zsh`; the login shell is unchanged. Existing checkouts and Zsh
files are preserved. Undo removes only the new checkout and runcom links, stopping
if they have since been modified. Use a font with the needed prompt glyphs in
your terminal if they do not display correctly.

It then runs `github-cli_playbook.yml` locally, forwarding the `--unattended`
setting. It installs `gh` using Homebrew and checks
`gh auth status --hostname github.com`. If no login exists, it runs
`gh auth login --hostname github.com --web --git-protocol https --skip-ssh-key`.
The device code and browser instructions appear directly in Terminal; browser
authorization still requires your participation with `--unattended`. Existing
credentials that fail validation stop provisioning rather than being overwritten.
It then runs `repository-origin_playbook.yml` to switch `origin` fetch and any
explicit push URLs to `git@github.com:bnowakow/homelab.git`. The original URLs
are saved once and restored by undo, including when `--keep` retains GitHub preferences. Other
remote settings are preserved. This step does not create or upload SSH keys;
Before changing origin, a read-only `git ls-remote` verifies repository access
over SSH with strict host verification. Authentication failure leaves origin
unchanged. If GitHub is not yet a trusted SSH host, verify its host key by running
`ssh -T git@github.com` in Terminal, then rerun provisioning. A valid key must be
registered on GitHub and SSH must be configured to use the agent, as described
in [1Password SSH setup](https://www.1password.dev/ssh/get-started).
SSH repository check failures stop with a short, actionable explanation.
Connection timeouts and DNS/network errors are reported separately from rejected
keys and unverified or changed host keys. Origin remains unchanged by the failing
run, earlier successful steps remain applied, and provisioning can be rerun after
the issue is resolved. A port-22 timeout points to GitHub's documented SSH-over-443
option; no network/SSH configuration is changed automatically.

The checks are read-only. Undo restores the original SSH config (or removes a
newly created file/directory when empty) in every `--keep` mode. Later SSH config
edits are replaced by that snapshot. Existing correct configurations are left
untouched. Config files linked to another file are not automatically rewritten;
configure the source file if needed. Manually enabled agent settings remain in
1Password preferences and are preserved by undo.

1Password protects sensitive settings with signatures and does not support
arbitrary direct settings-file edits. Enabling its SSH agent and approving Safari
extension permissions remain interactive UI steps; the playbook does not change
those settings using private file formats.

Finally, `make-dependencies_playbook.yml` installs missing runtime tools used by
the root Makefile targets. It preserves tools already installed through Homebrew
or available through another supported installation path.

| Make target | Dependencies |
| --- | --- |
| `help`, `install-dotfiles` | macOS/Command Line Tools: make, shell and standard utilities |
| `install-git-hooks` | Git and Homebrew `ggshield` (so the pipx/pip installation fallback is unnecessary) |
| `codex-commit`, `claude-commit` | Codex CLI and Claude Code casks; `dialog` for the terminal UI; system `infocmp` and `clear` |
| `sync-dotfiles` | Git, standard utilities and `dialog` (the alternative whiptail UI is unnecessary) |
| `cron-install-rancher` and its cron script | Homebrew Bash (for `mapfile`), `kubernetes-cli`, `jq`, `flock`, `logrotate`; system sudo/crontab |

The Rancher cron target still requires a Linux server layout (`/etc/logrotate.d`,
`/etc/rancher/k3s/k3s.yaml` and its fixed Linux PATH). Installing tools on a Mac
does not make that target portable or configure cluster access. Provisioning does
not run the Makefile targets other than `install-dotfiles`, install cron jobs,
enable hooks, or authenticate ggshield/Codex/Claude. Complete those account logins
before using their targets.

New formulae, their missing dependencies, and new casks are recorded before
installation. Undo removes those packages before restoring dotfiles and origin,
preserving packages that existed beforehand. Package upgrades are not rolled back.

After installing the tools, `finder-archive_playbook.yml` copies
`smb://nas.localdomain.bnowakowski.pl/time-machine` to the clipboard and displays a
dialog with these manual steps:

1. Open Finder.
2. Press **Cmd+K** (**Go > Connect to Server**).
3. Paste the copied address with **Cmd+V**.
4. Press **+** to save the server, then **Connect** and complete any NAS login.
5. Choose **Confirm** when done, or **Abort** to stop provisioning.

This checkpoint also waits with `--unattended`; Escape aborts. The script does
not modify Finder favorites or store NAS credentials itself. Undo leaves the
manually added favorite and SMB connection unchanged. The clipboard is replaced
with the URL; previous clipboard contents are not stored or restored.

After Finder confirmation, `time-machine_playbook.yml` scans the top-level
`.sparsebundle` directories in `/Volumes/time-machine`, without opening bundle
contents or following symlinks. A trailing space followed by a numeric version
(e.g. `Taerar 7.5.sparsebundle`) groups the bundle under that computer. Names
without that suffix (e.g. `Jakku.sparsebundle`) are unversioned. Versions sort
numerically, so 7.10 follows 7.5. If both exist, an unversioned entry is listed
alongside the versions.

The first dialog shows the complete computer/version list above its options.
The reusable `computer_menu()` function keeps the layout consistent on all
screens, using the shared Python `format_computers()` formatter for both the
full inventory and the selected-computer view. Computer names and versions have bullets, with each version on its
own tab-indented line beneath its computer, for example:

```text
- Jakku
- Taerar
	- 7.5
```

1. Create a new computer (new computer or new version of existing computer).
2. Restore existing computer (doesn't create a new version of existing computer)
   (**TODO: implement later**).

Abort is the default button; Enter without changing focus aborts. Abort/Escape
ends the wizard cleanly with a cancellation message, without a failed-command
dump. No Time Machine data is changed; earlier provisioning steps remain applied.
Real dialog rendering/terminal errors still fail with concise guidance. To
proceed, explicitly focus Continue. This manual choice is also required with
`--unattended`. An absent or unreadable share stops with connection instructions.
Choosing **Create a new computer** opens a second dialog with the same computer
and version list, followed by:

1. New version of existing computer (exists in the list above).
2. New computer (not existing in the list above) (**TODO: implement later**).

Its buttons are **Continue**, **Abort**, and **Back**. Back returns to the first
screen so the previous choice can be corrected. Abort remains the default on
all screens; Escape also aborts. The parent selection remains on Create when
returning, but the Abort button still has default focus.

**TODO: Restore existing computer and New computer will be implemented later.**
The first implemented path will be **New version of existing computer**. Selecting Restore currently only displays a TODO
notice; it does not perform a restore.

Choosing **New version of existing computer** opens a third screen. The full
computer/version list remains at the top, while selectable rows contain only
unique computer names in reverse alphabetical order, for example **Taerar** then
**Jakku**, without versions. The full inventory above keeps its original order.
It uses **Continue**, **Abort**, and **Back**, defaulting to Abort. Back returns
to the create-subtype screen with New version still selected; its own Back
returns to the first screen. If no computers exist, you can go Back or Abort.

After choosing a computer, the next screen shows only that computer and its
versions, with these choices:

1. New minor version (e.g. new macOS version or existing computer from scratch)
   (**TODO: backup creation**).
2. New major version (e.g. newly bought computer).

Minor comes first because reinstalling or upgrading macOS is more common than
replacing the computer.

Continue records the choice; Back returns to the computer picker with the
previous computer selected. Minor is initially highlighted, while Abort remains
the default button. Both paths continue to version-number selection. Minor backup creation remains
TODO; selecting and saving its number is supported.

Choosing either version type opens a screen showing the selected computer and
its versions, with two options:

1. Use the proposed version. For Major, use the highest existing major number
   plus one with minor `.0`: `1.1`, `3.2`, and `3.4` propose `4.0`. For Minor,
   increment the minor part of the highest existing version: `7.5` proposes `7.6`.
   A computer with only an unversioned backup starts at `1.0`.
2. Specify a version manually in an input dialog, prefilled with the proposal.
   Use digits, a dot, then digits: at least one digit before and after the dot
   (`^[0-9]+\.[0-9]+$`). Leading zeros are accepted; spaces, signs, letters,
   and extra dots are rejected. Existing
   versions are rejected and the input dialog explains how to correct the value.
   The version input initially has keyboard focus, so typing and Backspace work
   immediately. Press **Tab** to focus Continue, then **Enter** to accept.

Both screens have **Continue**, **Abort**, and **Back**. The proposal menu
defaults to Abort; the manual-entry screen starts with focus in the input field.
Back returns to the immediately preceding screen; returning to the version-type
screen retains the previously selected Minor or Major row. Manual entry permits an unused version
other than the proposal; it need not exceed the existing maximum. The chosen
number is held in `time_machine_version` (null for other paths or cancellation).
No backup is created or renamed yet.

The selected mode (`time_machine_action`: `create` or `restore`), create subtype
(`time_machine_create_type`: `new_computer`, `new_version`, or null for restore),
the selected existing name (`time_machine_computer`, null for other paths),
version type (`time_machine_version_type`: `major`, `minor`, or null for other
paths and cancellation),
and inventory are held as Ansible facts for subsequent tasks in this playbook. This step does not create computers,
restore data, or modify backup bundles. Actual create/restore steps will be added
separately.

Wizard answers are saved immediately after each accepted step to
`taerar/ansible/time-machine-choices.json` (permissions `0600`, ignored by Git).
Despite its original name, this is now the shared store for all provisioning
wizards: 1Password sign-in confirmation, SSH-agent/Safari setup confirmation,
Finder connection confirmation, and each Time Machine choice. Abort and invalid
input are never saved as accepted answers; previously accepted steps survive
later cancellation or failure. Choosing a TODO path records that choice too.

After bootstrap installs Ansible and `dialog`, `provision.sh` shows all saved
answers in aligned key/value columns (one answer per line) and the full JSON path **before 1Password and the other wizard stages**.
**Use previous answers** skips answered questions and continues at the first
unanswered one. **Pick new values** deletes the JSON immediately and asks again;
subsequent accepted answers start a new file. **Abort** preserves the file and
ends provisioning. Abort remains the default, including with `--unattended`.
The manual version input retains its documented text-focus exception.

Saved confirmations suppress repeat prompts; they do not bypass live readiness,
SSH-agent, or repository-access checks, installation checks, or account login
verification. Passwords, tokens and private keys are never saved. Finder is not
prompted again when its confirmation is reused; the Time Machine inventory is
still read if its selection is incomplete. A completed Time Machine selection
restores its facts without rescanning or recomputing its version proposal.
Changing an earlier Time Machine choice clears dependent saved choices. Computer
selections use stable names rather than inventory indexes.

The JSON uses `schema_version: 2` and an extensible `answers` object keyed by
stable question IDs, such as `onepassword.setup`, `finder.time_machine_connected`,
and `time_machine.computer`; `time_machine.result` stores completed named choices.
The previous schema-1 Time Machine file is accepted and migrated on the next
write. Malformed or unsupported files stop with an error. Writes merge answers
and replace the file atomically. `undo-provision.sh` removes it.

For future questions, use a new stable question ID, ask only when its saved answer
is absent, validate the answer, and save it immediately through
`tasks/save-wizard-answer.yml` (or the same `files/wizard_choices.py` helper inside
a wizard). Missing new IDs must still prompt when older answers are reused.
Place future questions after the existing wizard in a subsequent play/task stage
rather than inside the completed-Time-Machine skip. Do not cache account login
credentials or turn saved confirmations into proof of live readiness.

Saved-choice summaries use readable question labels and descriptions rather than
menu positions or raw button names. Choices are stored as stable semantic values
(for example `create`, `new_version`, `major`). The version-source answer stores
both `source` (`suggested` or `manual`) and the actual version number; a proposed
`8.0` is displayed as **Suggested version: 8.0**. Existing numeric answers are
mapped automatically when read. An old file that never recorded a proposed
number is marked as such rather than inventing one.

The summary also includes a separate **Version number** row for the literal
chosen number (for example `8.0`). If the saved run has not chosen a number yet,
it shows **Not selected yet**; the version type alone does not determine it.

Both suggested and manual version acceptance atomically save the literal version
in `answers["time_machine.number"]`, for example `"8.1"`, together with the source.
This dedicated value drives the summary; reusing a saved suggestion retains its
accepted number rather than recalculating it from the share.

Both **New minor version** and **New major version** now require a concrete
version number through the shared suggestion/manual screen. Minor suggests the
highest existing version with its minor number incremented (for example `7.5`
becomes `7.6`); major suggests the next major with `.0`. Both save the literal
number and completed choices. Actual minor-version backup creation remains TODO.
Reusing an older file containing only a minor/major type resumes at number
selection; no previously unrecorded number is fabricated.
