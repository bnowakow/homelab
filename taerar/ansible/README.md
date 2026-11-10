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
2. For `--automatically-approve`, run `sudo -v` in the same Terminal first if administrator
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
/bin/bash -c "$(curl -fsSL "https://raw.githubusercontent.com/bnowakow/homelab/master/taerar/ansible/provision.sh?$(date +%s)")" -- --automatically-approve
```

The examples use `--automatically-approve` to automatically proceed past installation
confirmations such as `Do you want to proceed with the installation? [y/n]`.
Omit `--automatically-approve` to keep interactive prompts. The Homebrew installer requires
existing sudo authorization for automatically approved installation; if necessary, run
`sudo -v` first. Passwords and macOS permission dialogs cannot be auto-answered.
The script asks for your sudo password itself, once, right at the start (it installs the
Command Line Tools with `sudo softwareupdate` when missing, and the Homebrew installer
needs authorized sudo), and keeps that authorization alive while it runs. The
`?$(date +%s)` after `provision.sh` makes GitHub return the current script instead of a
cached copy.

To find the command: open [bnowakowski.pl](http://bnowakowski.pl), follow the
GitHub link, open the `homelab` repository, and browse to
`taerar/ansible/provision.sh`.

The script creates `~/code` if needed and clones
`https://github.com/bnowakow/homelab.git` into `~/code/homelab` if that path is
missing. Existing checkouts are reused and fast-forwarded to the latest commit when they have no local changes. After GitHub login, an Ansible playbook changes
`origin` to `git@github.com:bnowakow/homelab.git`. Git must be available to clone; on a fresh
Mac the script first checks for the Command Line Tools and installs them from the
terminal with `sudo softwareupdate` (sudo asks for your password, also with
`--automatically-approve`; it is never auto-answered). If that fails it falls back to Apple's
installer dialog (`xcode-select --install`, click Install) and waits until git works,
then continues by itself. Undo never removes the tools.

From an existing checkout, you can also run from the repository root:

```sh
./taerar/ansible/provision.sh --automatically-approve
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

## Logs

Every `provision.sh` run writes one log, `~/Library/Logs/homelab-provision/provision-YYYYmmdd-HHMMSS.log`
(mode 0600; `provision-latest.log` points at the newest). It holds everything Ansible
prints (tasks, results, errors, the recap) plus the messages and rsync progress of the Time
Machine copy helpers. The bootstrap part before Ansible starts (Xcode tools, Homebrew, Ansible
install) and the terminal dialogs themselves are not logged. Logs are never rotated or deleted
automatically, and undo keeps them; rotate them with `newsyslog` or `logrotate` when they grow.

The extension list read from a Time Machine backup for Dia is kept in
`~/.homelab-provision-state/dia-backup-extensions.json`, so a rerun does not attach the backup
again just to list it; delete that file to list it afresh.

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
attempts that may fail partway through. Undo reverses them in order: remove
unchanged Dia settings, apps, settings, user files and home folders copied from a Time Machine backup, remove Makefile dependency packages, restore
the original origin URLs, undo the GitHub login unless retained, remove
the newly created Prezto checkout/runcom links, remove the Dia browser and the Claude/Codex apps and CLIs it installed
(sign-ins are kept), restore dotfiles, remove GitHub
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
`sudo -A mas get 1569813296` (mas 7 needs root; tasks have no terminal, so `files/sudo-askpass.sh` shows a native macOS hidden password dialog that only sudo receives) to install [1Password for Safari](https://apps.apple.com/us/app/1password-for-safari/id1569813296?mt=12).
Existing applications are preserved. The Mac App Store can require Apple Account
or administrator authentication even with `--automatically-approve`.

After installation, sign in to 1Password for Mac and enable **Safari > Settings >
Extensions > 1Password for Safari**, approving website permissions as needed.
Provisioning opens 1Password and first checks whether its SSH agent can perform
a local sign/verify operation (`ssh-add -T`) with an offered key. This may show a
native 1Password authorization prompt. If verified, the sign-in dialog is skipped.
Otherwise it asks you to confirm sign-in and unlock before continuing.
After sign-in is confirmed or verified, Ansible configures a GitHub-only
`IdentityAgent` block in `~/.ssh/config`, unless already configured correctly.
It snapshots the original file and permissions before editing. Next it checks
agent availability, GitHub authentication with `ssh -T git@github.com`,
Safari extension enabled state, and whether 1Password is enabled for macOS
AutoFill. The SSH test recognizes GitHub's authenticated
greeting with exit code 1 as success, allows up to 30 seconds for authorization,
and distinguishes key rejection, host verification, network and timeout failures.
The dialog lists only checks still needing attention; unknown Safari state does
not cause it to ask you to redo working SSH setup. The
second dialog appears only if at least one status is not ready or unknown and
its confirmation was not explicitly reused from saved answers. It asks you
to enable **1Password > Settings > Developer > Use the SSH Agent**, make your SSH
key available, register its public key on GitHub,
enable the Safari extension, and enable macOS AutoFill. It then checks the 1Password agent with
`ssh-add -l` using its socket. If unavailable or empty, provisioning stops with
instructions. Unsaved incomplete/unverified checkpoints still appear with `--automatically-approve`; Abort/Escape
aborts. Safari detection reads extension-state plists for default and discovered
profiles. Unreadable, missing, conflicting or unrecognized state falls back to
manual confirmation; app installation/PlugInKit registration is not proof of
activation.

macOS AutoFill (saving and filling passwords in desktop apps) is detected
read-only with
`pluginkit -m -v -p com.apple.authentication-services-credential-provider-ui`:
a `+` before `com.1password…autofill-extension` means enabled, `-` means
disabled, and a missing or unreadable entry is unknown. If it is not enabled,
the same setup dialog asks you to choose **Set up macOS AutoFill** in 1Password
or enable 1Password in **System Settings > General > AutoFill & Passwords**.
Provisioning never enables it itself (for example with `pluginkit -e use`):
choosing a system password provider is a macOS security setting you must
approve. Its confirmation is saved separately as `onepassword.autofill`, so an
older saved `onepassword.setup` answer does not hide it. Undo leaves the setting
unchanged; removing the 1Password app also removes its AutoFill extension.

Saved confirmations can suppress repeated manual prompts when explicitly reused;
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

Next, `ai-tools_playbook.yml` installs the Claude and Codex tools and signs in
to each, one at a time. It runs right after dotfiles (which must directly
follow 1Password), so 1Password and its SSH setup are already done.

1. **Desktop apps:** the Homebrew casks `claude` (Claude) and `codex-app`
   (Codex) are installed when `Claude.app` / `Codex.app` are missing from
   `/Applications` and `~/Applications`.
2. **CLIs:** a desktop app may already include its CLI, so the CLI is only
   installed when it cannot be found. The Claude CLI is searched on `PATH` and in
   `~/.local/bin` and `~/.claude/local`; the Codex CLI on `PATH`, in
   `Codex.app`, and in the CLI bundled with `ChatGPT.app`. Missing ones are
   installed as the casks `claude-code` and `codex` (after the desktop apps, so
   a Codex CLI bundled in the new app is used instead). Claude Desktop's bundled
   Claude Code lives under `~/Library/Application Support/Claude` and is not on
   `PATH`, so it does not count.
3. **Sign-in, in this order:** Claude desktop, Claude CLI, Codex desktop,
   Codex CLI. Each tool is checked **first**, live on every run, and nothing is
   asked or opened when it is already signed in:
   - Claude CLI: `claude auth status`; Codex CLI: `codex login status`.
   - Claude desktop: whether its `config.json` in
     `~/Library/Application Support/Claude` holds a non-empty
     `oauth:tokenCacheV2` (only "non-empty" is tested; the value is never
     printed or read further).
   - Codex desktop: it shares its sign-in (`~/.codex/auth.json`) with the CLI,
     so `codex login status` covers both.

   Only for a tool that is signed out does a menu offer **Sign in now** /
   **I have signed in**, **Skip** or **Abort** (default). A CLI sign-in runs
   `claude auth login` / `codex login` in the terminal, you approve in the
   browser, and provisioning verifies the result. For a desktop app,
   provisioning opens it and you sign in in its window; if the sign-in is not
   detected afterwards it continues with a warning, since you confirmed it.
   Skipped tools are asked again on the next run. Passwords and tokens are only
   typed in the browser or the app, and nothing is saved in the answers file.

Installed casks and their new dependencies are recorded in
`ai-tools-casks` and `ai-tools-formulae` in `~/.homelab-provision-state` before
installing. Undo removes only those, before restoring dotfiles. It does not sign
you out or delete app data, similar to 1Password preferences. `make-dependencies`
still installs the same CLI casks if they are missing, but normally finds them.

Next, `dia_playbook.yml` installs the [Dia browser](https://www.diabrowser.com/)
with the Homebrew cask `thebrowsercompany-dia`, unless `Dia.app` already exists
in `/Applications` or `~/Applications`. Dia requires Apple silicon and macOS 14
or later; on another Mac provisioning prints a message and skips it. The cask
and its new dependencies are recorded in `dia-casks` and `dia-formulae` in
`~/.homelab-provision-state` before installing. Undo removes Dia before the AI
tools, and keeps its profile and sign-in. Copying its settings from a backup is
asked later, with the Time Machine steps (see below).

Next, `prezto_playbook.yml` reuses [the shared Prezto role](../../ansible/roles/prezto/README.md)
also used by `nas/ansible/proxmox-prezto_playbook.yml`. It installs `~/.zprezto`
with recursive submodules, including Powerlevel10k, then links missing Zsh runcom
files. Dotfiles already installed above select Powerlevel10k and supply its config.
macOS uses `/bin/zsh`; the login shell is unchanged. Existing checkouts and Zsh
files are preserved. Undo removes only the new checkout and runcom links, stopping
if they have since been modified. Use a font with the needed prompt glyphs in
your terminal if they do not display correctly.

It then runs `github-cli_playbook.yml` locally, forwarding the `--automatically-approve`
setting. It installs `gh` using Homebrew and checks
`gh auth status --hostname github.com`. If no login exists, it runs
`gh auth login --hostname github.com --web --git-protocol https --skip-ssh-key`.
The device code and browser instructions appear directly in Terminal; browser
authorization still requires your participation with `--automatically-approve`. Existing
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
the issue is resolved. When port 22 to github.com is blocked (checked with `nc`) but `ssh.github.com:443`
is reachable, provisioning adds a marked `Host github.com` block (`HostName
ssh.github.com`, `Port 443`, `HostKeyAlias github.com`) to `~/.ssh/config`, recorded in
`github-ssh-443` for undo. Before the check it adds GitHub's SSH host keys to
`~/.ssh/known_hosts` only when they match GitHub's published fingerprints (scanned on
port 22, or on 443 as a fallback); the added lines are recorded in `github-known-hosts`
and undo removes exactly those. Host verification is never disabled.

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

`apps_playbook.yml` (runs after `dev-tools_playbook.yml`) installs the applications
listed in `files/apps.json`: Homebrew casks, App Store apps through `mas install`
(mas cannot report the sign-in, so the first install is the check: on failure the App Store opens and you sign in and retry, or skip the store apps; a failed `mas install` is retried with `mas get`, which also fetches free apps the Apple ID never downloaded; mas 7 cannot report or change the account, so every store app has an `"account"` in `apps.json` (`app_store_accounts` lists the order: `sup3r0@gmail.com` first, then the normal `dobrowolski.nowakowski@gmail.com`); the installer asks you to switch the App Store to each account in turn, confirms with you, installs that account's apps and moves on, and after any other account was used it always asks you to switch back to `dobrowolski.nowakowski@gmail.com`, even when no app of that account is left; dialogs also appear under `--automatically-approve`) and, for apps with neither, a final
list of what to install by hand with the reason (for example Camtasia 2, whose
license covers only version 2.x, and DxO PhotoLab 9). Apps already present in
`/Applications` or `~/Applications` are skipped, one failed install never stops
the rest, and casks plus their missing dependencies are recorded in
`apps-casks` / `apps-formulae` first so `undo-provision.sh` removes them. App
Store apps are recorded in `apps-mas` but left installed. In the `TEST`
environment only one representative cask (MacPacker) and one App Store app
(Blackmagic Disk Speed Test), marked `"test"` in the catalog, are installed and the rest of the
plan is only printed.

**Known issue: Logitech G HUB does not start on macOS 27 (Darwin 27.2).** The cask
`logitech-g-hub` (in `files/apps.json`) installs fine: its silent installer puts
`lghub.app` in `/Applications` and the data in `/Library/Application Support/Logi/LGHUB`
(`version.json`, `current.json`, `groups.json`, `installation.json`, `depots`, `cache`). But
`lghub_agent` aborts at once with `mutex lock failed: Invalid argument` (an uncaught
`std::system_error` in `ThreadWatcher::exitThread`; reports in
`~/Library/Logs/DiagnosticReports/lghub_agent-*.ips`), so the window shows "Your G HUB
installation is damaged ... Please reinstall". That message is misleading: the files are
there and match the Time Machine backup (same build 2026.7.1011216), so reinstalling,
`brew reinstall --cask logitech-g-hub`, relogging or restoring files from the backup do not
help. Logitech's download page (`https://www.logitechg.com/pl-pl/software/ghub`) offers no DMG;
its macOS link is the same `lghub_installer.zip` the cask uses.

**Resolved (2026-11-10): Blackmagic RAW Player is not an App Store app on this setup.** `mas install` failed with
`No downloads initiated`, and the `mas get` fallback opened Apple's "Sign-In Required" sheet and ended with
`ISErrorDomain Code=4` even after signing in. The old Mac's backup shows why: `Blackmagic RAW Player.app` lived in
`/Applications/Blackmagic RAW/` with no `_MASReceipt`, so it came from Blackmagic's own installer. The catalog entry is now a
vendor download with `"folder": "Blackmagic RAW"` (the app sits in a folder not named after it; the installed check in
`install_apps.py`, `vendor-downloads_playbook.yml` and the preference copy honor `folder`). The `mas get` sheet can still appear for other free
App Store apps this Apple ID never downloaded; the account check cannot see an expired store session.

**TODO (App Store sign-in sheet during `mas get`):** if Apple's "Sign-In Required" sheet shows up again for another free
App Store app this Apple ID never downloaded (Blackmagic RAW Player was fixed by making it a vendor download, so it is not a
live case): (1) check whether the app is really an App Store app, as the old Mac's backup shows (no `_MASReceipt` in the bundle means
it came from the vendor, so make it a `vendor` entry in `files/apps.json`, with `"folder"` when it installs into a folder not
named after it); (2) otherwise sign out of the App Store and back in, then rerun; (3) otherwise warn before the `mas get` fallback in
`files/install_apps.py` (`install_store_group`) that Apple's confirmation sheet will open and must be completed, and record such
failures as "needs confirmation in the App Store" instead of a generic `mas exit 1`. The signed-in check
(`ensure_store_signed_in`, reads `Accounts4.sqlite`) cannot see an expired store session, so it will not catch this.

**Dock folder tiles in cloud storage.** A Dock folder tile in `~/Library/CloudStorage` (e.g. `Dropbox/Zrzuty ekranu`) shown as a
Folder (`displayas` 1) renders as a "?" icon (it still opens); shown as a Stack (`displayas` 0) it shows previews of its files, which is what you get
after dragging it out and back in. Restoring the Dock settings from a backup can bring the Folder style back, and the Dock is restarted
before Dropbox has synced. `provision.sh` therefore runs `files/refresh_dock_cloud_tiles.py` after the playbooks: it sets Folder-style cloud
tiles to Stack, then restarts the Dock once when the folder exists by then. If it does not exist yet (Dropbox not signed in or still
syncing), it prints a note; run `killall Dock` once the folder appears.

**TODO (G HUB):** wait for a G HUB build that runs on this macOS, then check
`brew upgrade --cask logitech-g-hub` and rerun provisioning; report the crash to Logitech with
the `.ips` reports and the macOS version; after a working build, decide whether to copy G HUB
settings from the backup (the data is under `/Library/Application Support/Logi/LGHUB`, root
owned, so it needs sudo through the askpass dialog) and whether `apps_playbook.yml` should skip
or flag G HUB on macOS versions where it cannot run.

The combined checklist in `time-machine-copy-question_playbook.yml` asks whether to copy apps and preferences
from the selected backup, and `time-machine-apps_playbook.yml` does it after the
Dia step and before the final home folders copy. It copies the `from_backup` apps
of `files/apps.json` that this Mac lacks (ForkLift, Camtasia 2, OpenEmu,
Transmission Remote GUI, Better Dock, CoreNodeReader, megacleaner, Tailscale-package,
Vorssaint, and the vendor-download apps FileZilla, DxO PhotoLab 9, ON1 Photo RAW 2026, Luminar Neo,
Blackmagic Camera Setup, OM Capture, Pazu StreamGet and PRO Speed Test; an app the backup lacks is
reported as "not found in the backup" and has to be downloaded by hand). Blackmagic RAW Player is a vendor download (the Blackmagic RAW installer puts it in
`/Applications/Blackmagic RAW/`; its App Store listing cannot be downloaded). Copied apps go into `/Applications` (or `~/Applications` where they
lived), and the preferences plist (`~/Library/Preferences/<bundle id>.plist`, or
the sandbox container's copy) of every catalog app installed here. Never
overwrites; apps that are running (for example the terminal) are skipped with a
note; copied bundles keep the old signatures, so App Store apps such as ForkLift 2
may need the same Apple ID. Uses the same Homebrew rsync staging, manifest
(`time-machine-apps.jsonl`) and undo as the other backup copies; `TEST` copies one
plist and no app.

`vendor-downloads_playbook.yml` is the very last step (after the home folders copy), asked only
when apps and preferences are copied from a backup; it asks its questions with
`--automatically-approve` too, since they need your choice. The vendor
downloads are the `files/apps.json` entries marked `"vendor": true`: FileZilla, DxO PhotoLab 9,
ON1 Photo RAW 2026, Luminar Neo, Blackmagic Camera Setup, OM Capture, Pazu StreamGet and
PRO Speed Test (no Homebrew cask, no usable App Store listing). It lists them with their vendor
site and whether each is installed here, with **Continue / Skip / Abort** (Abort is the default);
install the ones you need while the dialog waits, then Continue. A checklist then shows the apps;
those **installed here whose preferences were not copied before** are marked by default (recorded
per app as `vendor-prefs:<name>` in `copy-status.json`); the preferences of the marked apps are copied from the backup
(`copy_time_machine_downloads.py --vendor-prefs`, preferences only, never app bundles, never
overwriting; manifest `time-machine-vendor-prefs.jsonl`, removed by undo like the other copies).
An app that is not installed here, or that the backup has no preferences for, is reported in the notes.

The combined checklist in `time-machine-copy-question_playbook.yml` asks whether to copy user files, and
`time-machine-user-files_playbook.yml` does it after the apps copy: `~/.ssh`
(including private keys; the folder is set to mode 700 and agent/ControlMaster
sockets are skipped), `~/Library/Fonts`, `~/Library/LaunchAgents` (copied but not
loaded, so nothing runs until the next login), `~/Library/Services` and
`~/Library/Scripts`, plus the dotfiles and folders that `make install-dotfiles` does
not install: `.zshenv`, `.zprofile`, `.zlogin`, `.zlogout`, `.zsh_history`, `.viminfo`,
`.config`, `.docker`, `.kube`, `.cursor`, `.vscode`, `.codex`, `.claude`, `.claude.json`,
`.gradle`, `.m2`, `.aws`, `.gnupg` (mode 700), `.screenrc`, `.npmrc` and similar (some hold tokens).
Caches, installed tools and Ollama models are not copied. Files are copied with the same Homebrew rsync staging, never
overwritten, and removed by undo while unchanged (`time-machine-user-files.jsonl`).
The Finder sidebar favorites (`com.apple.LSSharedFileList.FavoriteItems.sfl*`) are
the one exception (also copied with rsync): macOS already has them on a new Mac, so they are replaced after
saving the original in `user-files-backup`, and undo restores it; the sidebar
bookmarks may point at folders that do not exist here. `TEST` copies one small file.
`~/.gitconfig`, `~/.zshrc`, `~/.vimrc`, `~/.p10k.zsh` and `~/.zpreztorc` are not read
from the backup: they are repository dotfiles installed by `make install-dotfiles`.

The combined checklist in `time-machine-copy-question_playbook.yml` asks whether to copy `~/Library/Keychains`, Safari
(`~/Library/Safari` and its container), Messages (`~/Library/Messages`: chat database
and attachments) Mail (`~/Library/Mail` and the Mail containers) and Internet Accounts (`~/Library/Accounts`), and
`time-machine-private-data_playbook.yml` does it right after the user files. Safari,
Mail and Messages must be closed (the playbook asks you to quit them and re-checks).
It uses the same rsync staging and never overwrites: a keychain, database or Mail
account this Mac already created is kept, so only missing files are added (for
example the `login.keychain-db` of a fresh account stays, and a Safari that was
already opened keeps its own `History.db`). Mail account sign-ins live in the system
accounts database (`~/Library/Accounts/Accounts4.sqlite`). It is in the list, but a Mac
that has finished setup already has one (it holds the Apple ID and iCloud sign-in), so
in practice it is kept and not copied; add the other accounts in Mail or Internet
Accounts and the copied messages are used. Undo removes unchanged copies
(`time-machine-private-data.jsonl`). `TEST` copies one file.

#### Internet Accounts (`~/Library/Accounts`): not restored from the backup

Decision: the accounts database is not replaced from the backup, and deleting it on a
set-up Mac is not supported. It holds more than Internet Accounts: the Apple ID,
iCloud, IDMS, CloudKit, Device Locator, Find My Friends, Game Center, Messages and
iTunes Store accounts, the CalDAV/CardDAV/IMAP/SMTP accounts, the apps' authorizations
and references to Keychain items. Accounts4.sqlite is held open by `accountsd`.

A restored copy does not give working accounts: passwords and tokens are Keychain
items (this Mac's `login.keychain-db` is kept, see above), and the Apple ID, iCloud and
Messages entries carry tokens issued to the old Mac. Expect sign-in prompts for all of
them and a possibly broken iCloud state. Add the accounts again in System Settings >
Internet Accounts instead.

Not implemented (a possible future option, with undo): sign out of iCloud, quit Mail,
Calendar, Contacts and Messages, move `~/Library/Accounts` aside (saved for undo), copy
the folder from the backup, then restart. Even then re-authentication is expected, and
the only account it would help with is a plain IMAP/SMTP one.

The settings merge below also covers the `com.apple.Music`, `com.apple.mail`,
`com.apple.iCal` and `com.apple.Notes` preferences (account setup and the mail or
message data itself are not copied).

The combined checklist in `time-machine-copy-question_playbook.yml` asks
whether to merge Finder and system settings from the backup;
`time-machine-settings_playbook.yml` does it after the apps copy. For these domains:
global preferences, Finder, desktop services, Dock, Spaces, WindowManager,
trackpad and mouse, input sources, symbolic hotkeys, screenshot, menu-bar clock,
Control Center, accessibility and System Settings; it reads
`~/Library/Preferences/<domain>.plist` in the backup and merges the keys with
`defaults export`/`import` (never by replacing files, which cfprefsd would
overwrite). Keys from the backup win and other keys stay; volatile keys (recent
folders, window frames) are skipped. The current domain is exported to
`~/.homelab-provision-state/system-settings-backup` and recorded in
`time-machine-settings.jsonl` first, so `undo-provision.sh` restores it. Finder,
Dock and the menu bar restart. Dock entries of apps not installed show a
question mark. `TEST` merges only the Finder domain. Not copied: Keychain,
Safari, Mail and other app data, and ByHost (per-machine) preferences.

When Xcode.app is the active developer directory but its license is not accepted, git, make
and Homebrew refuse to run. `tasks/xcode-license.yml` (run at the start of the 1Password, dotfiles,
GitHub CLI and dev-tools steps, and after the app installs) detects this with `xcodebuild -license check`, asks first in a
dialog (default Abort) and then runs `sudo -A xcodebuild -license
accept` through the askpass dialog. With `--automatically-approve` it skips the question and accepts
the license (the sudo password dialog still appears). Nothing changes when the license is already accepted or
only the Command Line Tools are in use.

`dev-tools_playbook.yml`, which runs right after `make-dependencies_playbook.yml`
(below), installs with Homebrew, when missing: SDKMAN (`sdkman/tap/sdkman-cli`),
screen, wget, curl, gnu-sed (installed as `gsed`; the BSD `sed` in `/usr/bin` is untouched), git (Homebrew's, which `.zshrc` puts ahead of Apple's `/usr/bin/git`), ffmpeg, ggshield, mole, mtr, node, ollama, pipx, pyenv,
pyenv-virtualenv, the current `python`, readline, ruby, sshpass, tree, yt-dlp, ansible, dialog and the
casks rar and znotch (`zkondor/dist/znotch`). Homebrew 7 refuses untrusted taps,
so only the SDKMAN formula and the znotch cask are trusted, with
`brew trust`. Taps, trusted items, formulae and casks are recorded in
`~/.homelab-provision-state/dev-tools-{taps,trusted,formulae,casks}` before
installing; `undo-provision.sh` removes them, untrusts and untaps.

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
`smb://nas.localdomain.bnowakowski.pl/time-machine` (`smb://nas.tailscale.bnowakowski.pl/time-machine`
when `HOMELAB_LOCATION=remote`, see Environment below) to the clipboard and displays a
dialog with these manual steps:

1. Open Finder.
2. Press **Cmd+K** (**Go > Connect to Server**).
3. Paste the copied address with **Cmd+V**.
4. Press **+** to save the server, then **Connect** and complete any NAS login.
5. Choose **Confirm** when done, or **Abort** to stop provisioning.

The playbook first tries to mount the share **automatically** (`open smb://...`,
so Finder mounts it with the sign-in saved in your Keychain, or macOS asks for the
NAS password itself; this script never types or sees it) and waits up to 90
seconds. Only when the share is still missing does the manual dialog appear, only
while the share is **not mounted** at
`/Volumes/time-machine` (an `smbfs` mount), and Confirm rechecks: if the share
is still missing, the dialog returns with a note (for example when Finder
mounted it as `time-machine-1`). An already-mounted share skips the dialog. A
saved Finder confirmation or a reused completed Time Machine selection does not
skip it when the share is unmounted and a later step still needs it (copying
a home folder, Dia, apps, settings or user files copy not declined); when nothing needs the share, it is not requested.
Abort stops with a short message.

This checkpoint also waits with `--automatically-approve`; Escape aborts. The script does
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
`--automatically-approve`. An absent or unreadable share stops with connection instructions.
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

After the version number is accepted (suggested or manual), a last screen asks
whether to copy data into the new version from an existing backup. It shows
the selected computer and its versions above these choices:

1. Do not copy data (start with an empty backup).
2. One **Copy data from** row for each existing version of that computer,
   including an unversioned backup if one exists (for example **Copy data from
   Taerar 7.5**).

The newest existing version is highlighted, but Abort remains the default
button. **Back** returns to the screen where the version number was accepted
(the proposal menu or the manual input). The choice is held in
`time_machine_copy_from`: `none`, `unversioned` or a version such as `7.5`
(null for other paths and cancellation). It is saved as
`answers["time_machine.copy"]` and in `time_machine.result.copy_from`. If a
saved source no longer exists on the share, the question is asked again.
**TODO: copying the data is not implemented yet**; this step only records the
choice and does not read, copy or modify any backup.

### Which copies are done

Every copy from the backup records its state in `~/.homelab-provision-state/copy-status.json`
(mode 0600; removed by undo), one entry per step: `apps`, `user-files`, `private-data`,
`system-settings`, `dia`, `dia-extensions` and `folder:<Name>`. The state is `started` (written
before the backup is attached; it stays that way after Ctrl-C or a kill), `done` (finished
without error, even if nothing new had to be copied), `failed` (with the error) or `skipped`
(Skip copy in a quit dialog), plus the time, the bundle, the snapshot and counts. Declined
questions are in the saved answers (`time-machine-choices.json`, `time_machine.*`). Show it with:

```sh
python3 taerar/ansible/files/copy_status.py show
```

One checklist (`time-machine-copy-question_playbook.yml`, asked up front after the wizard) lists every copy: the home
folders, `apps`, `user-files`, `private-data` and `system-settings`, each with its last status. A copy that finished for the same bundle and
mode is unchecked; one that was interrupted (`started`) or failed is checked; one never made (or `skipped`) uses its default
(Desktop, `go` and `Public` off, everything else on). Nothing is saved: the defaults follow `copy-status.json` on every run, and Abort (the default)
or Esc stops provisioning. The older per-step `time_machine.apps`, `.settings`, `.user_files`, `.private_data` and `.folders` answers are ignored. While the bundle attaches, a reminder is printed every
two minutes.

### Copying home folders from the selected backup

After the wizard, when a backup to copy data from was selected (not **Do not
copy data**), `time-machine-copy-question_playbook.yml` shows the one checklist (Space
toggles) described above. Its home folders are copied from the **latest** backup inside that bundle (for
example `Taerar 7.5.sparsebundle`): Documents, Downloads, Movies, Music,
Pictures, `code`, `Sync`, Desktop, `go` and `Public`. Music includes the Music app library.

**The copy is the very last provisioning step**, because it can take a long
time. All the questions (this one, then the apps, user files, settings and Dia
questions) are asked up front, right after the wizard, so you can answer them and
walk away; everything else is set up first, and `time-machine-folders_playbook.yml`
copies the selected folders at the end, one after another
(`tasks/copy-backup-folder.yml`, then `copy-backup-folder-run.yml`), each with its own rsync progress line and
manifest. If the
question is skipped (no copy source, cancelled, or no Full Disk Access) or
answered `skip`, the final step does nothing.

**`~/code` and repositories that already exist here.** In `PROD`, `~/code` is copied with one
rule on top of the usual never-overwrite behavior: a repository that has a `.git` both in the
backup and on this Mac (for example `~/code/homelab`, cloned by provisioning) is not copied
over. Its `.git` and tracked files stay as they are, and from the backup only (1) the
untracked, non-ignored files (`git ls-files --others --exclude-standard`, read-only, with
`safe.directory=*` and optional locks off) are copied, and (2) its uncommitted changes to
tracked files (`git diff HEAD --binary`, staged and unstaged) are applied to the working tree
here with `git apply`, only when they apply cleanly (the index is not touched, existing edits
are never overwritten). Anything else is reported and the patch is saved in
`~/.homelab-provision-state/time-machine-folder-code-work/patches/<repo>.patch`: when the
backup's HEAD commit is not in the clone here, when the patch conflicts, or when git cannot be
read. Stashes in the backup and ignored files (for example `node_modules`) are not copied; the
number of stashes is reported. A repository that exists only in the backup is copied as it is,
`.git` included, and an interrupted copy of it resumes in full on the next run because its
`.git` is recorded in the manifest. Each applied patch is recorded in
`time-machine-folder-code.jsonl`; undo reverses it with `git apply -R` only if nothing was
edited on top, and a rerun reports `already applied`.

`files/copy_time_machine_downloads.py` then:

1. Attaches the bundle read-only with `hdiutil` to a temporary mount point. provision.sh keeps it attached
   between the copy steps (attaching over the network is slow) and asks at the end of
   every run, also with `--automatically-approve`, whether to detach it; Keep attached is
   the default, and `copy_time_machine_downloads.py --detach` detaches it later. If the volume is encrypted and stays locked, it
   attaches without mounting, runs `diskutil apfs unlockVolume`, which asks for
   the Time Machine backup password in the terminal (never stored), and mounts it
   read-only. If Time Machine is currently using the bundle, attaching fails;
   rerun after that backup finishes. Detaching falls back to
   `diskutil unmountDisk force` and `diskutil eject` when `hdiutil` reports the
   image as busy.
2. Finds the newest completed backup: APFS Time Machine backups are volume
   snapshots, so it lists them with `diskutil apfs listSnapshots`, picks the
   newest `com.apple.TimeMachine.<date>.backup`, and mounts only that snapshot
   with `mount_apfs -o rdonly,nobrowse,noowners`. A `<date>.backup` folder at
   the volume root without a snapshot is an unfinished backup and is ignored.
   `noowners` matters because the same user can have a different numeric ID
   on each Mac (for example `sup` is 501 on Taerar and 502 on Jakku). `tmutil`
   is deliberately not used: it mounts every snapshot through a root helper,
   which enforces the source Mac's IDs and keeps the image busy. HFS+ backups
   use `Backups.backupdb/*/Latest`. It then finds `Users/$USER/Downloads` in the
   backed-up volume, preferring the `… - Data` volume (the snapshot holds
   `<date>.backup/<Mac> HD - Data/Users/...`). A missing folder stops with the
   user names found in the backup.
3. Checks free space (copy size plus 1 GiB headroom, minus what an interrupted
   earlier run already staged), then copies the files that do not exist yet
   with **Homebrew `rsync` 3.x** in one run (`rsync -lptX --append
   --files-from`), never overwriting existing files. It keeps symlinks as
   links, permissions, modification times and extended attributes such as
   Finder tags and "Where from"; owners are not copied, because user IDs differ
   between Macs. `.DS_Store`, `.localized` and empty folders are skipped.
   macOS's own `/usr/bin/rsync` is `openrsync` (rsync 2.6.9 compatible), which
   has no `-X` and would drop that metadata, so it is never used. Right before
   copying, the playbook installs the `rsync` formula if it is missing and
   records it and its new dependencies in
   `~/.homelab-provision-state/time-machine-formulae` for undo.

**Full Disk Access is required:** macOS denies reading Time Machine backup
contents otherwise. Enable the terminal app in **System Settings > Privacy &
Security > Full Disk Access**, quit and reopen it, then rerun. Before the slow
attach, the helper checks access by listing
`/Library/Application Support/com.apple.TCC` (readable only with Full Disk
Access) and names the app macOS checks: the outermost `.app` above the
process. When run from an embedded terminal, such as the Claude app's, that
app needs access, not Terminal (iTerm, Terminal and Claude each need their
own). Provisioning only explains this; it never changes privacy settings.

The same check also runs **early**, in `full-disk-access_playbook.yml` right
after the saved-choices review and before 1Password, whenever the copy may
still happen (it is skipped when the copy was declined or a reused selection
has no copy source). If access is missing, a dialog names the app and the
steps. **Open Settings** opens System Settings directly at Privacy & Security >
Full Disk Access (`x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles`)
and shows the dialog again; you still turn the app on yourself. macOS applies
Full Disk Access only after the app restarts, which ends the run, so the
choices are **Abort** (default: enable it, quit and reopen the app, rerun) or
**Continue without**, which runs the other steps and skips the
home folders copy with a message instead of failing at the end. Access is
checked live on every run and never saved. The Time Machine share must
be connected in Finder (a reused Finder confirmation does not reconnect it).
Failures stop with the helper's message, keep saved answers, and can be rerun.

**Location:** `HOMELAB_LOCATION` is `local` (default, on the home network) or `remote` (away from it,
for example when the home internet is down). It is read like `HOMELAB_PROVISION_ENV` below (exported
variable, otherwise `.env`) and passed to playbooks as `provision_location` and `nas_domain`
(`localdomain.bnowakowski.pl` or `tailscale.bnowakowski.pl`). `remote` makes every NAS address use the
Tailscale name, so Tailscale must be connected. `../mount-nas-via-sshfs.sh` follows it too.

**Environment:** `HOMELAB_PROVISION_ENV` selects `PROD` (default) or `TEST`.
`PROD` copies the selected folders and Dia's configuration. `TEST` copies only the
first visible top-level file of each selected folder by name and only Dia's `Preferences`,
for trying provisioning on a Mac with little free space.
`provision.sh` reads it from an exported variable, otherwise from
`taerar/ansible/.env` (Git-ignored), otherwise uses `PROD`. Copy
[`.env.sample`](.env.sample) to `.env` to change it; the file is parsed as
`KEY=VALUE` text, never sourced, and other values stop provisioning.

Every created file and folder is recorded in
`~/.homelab-provision-state/time-machine-folder-<Name>.jsonl` (one per folder) **before** it is
created, before `rsync` starts.

**Interrupted copies resume.** `rsync` first copies into a staging folder,
`~/.homelab-provision-state/time-machine-folder-<Name>-work/stage/<snapshot>`, on the
same volume as the destination, using `--append`. When every file is complete,
each one is moved into place with an atomic rename, so a half-written file never
appears in the folder. After any interruption (Ctrl-C, a closed lid, a crash, a
lost network connection or a power loss), rerunning provisioning continues each
partial file right after the bytes already staged and sends only the rest: a
test that killed a 40 MB copy at 26 MB re-sent 15 MB, not 42 MB. Finished files
are skipped, and nothing reaches the destination until the whole copy is
complete. `--append` trusts the staged bytes, which is safe because the source is
a read-only snapshot, so staged data belongs to one snapshot and is discarded if
the latest backup changed between runs. (A temp folder with `--partial-dir` was
tried first and does not survive a hard kill, and `--inplace` into the
destination would leave truncated files that `--ignore-existing` skips.) The
attach itself is repeated on each run and can take a couple of minutes over
SMB. Undo
removes copied files only when their size and modification time are
unchanged, removes created folders only when empty, deletes that work folder,
and then uninstalls the Homebrew `rsync` it installed (a pre-existing `rsync`
is kept). Edited files and files that existed before are kept.

### Copying Dia settings from the selected backup

After the folders question, when a backup to copy data from was selected and Dia
is installed, `time-machine-dia_playbook.yml` asks whether to copy Dia's
configuration from the **latest** backup in that same bundle. Abort is the
default; the answer (`copy` or `skip`) is saved as `time_machine.dia` and is
cleared when an earlier Time Machine answer changes. It reuses the home-folder
machinery: Full Disk Access, the read-only snapshot mount, Homebrew `rsync`
(installed on demand by the shared `tasks/install-rsync.yml`), and the same
resumable staging, so files that already exist are never overwritten.

Dia is Chromium based, so only plain settings are copied (from
`Library/Application Support/Dia` in the home folder inside the backup, plus
`Library/Preferences/company.thebrowser.dia.plist`):

- `User Data/Local State`, `User Data/Default/Preferences` and `Bookmarks`
- `Local Extension Settings` and `Sync Extension Settings` (storage keyed by
  extension ID, kept for when an extension is reinstalled)

**Not copied, and why:**

- **Extensions.** The browser keeps its list of installed extensions in
  `Secure Preferences`, signed for the Mac that wrote it, and deletes extension
  folders that are not on that list when it starts. Copying their folders only
  gets them deleted (this was observed), so the extension code, `Extension
  State/Rules/Scripts`, `Managed Extension Settings` and `Secure Preferences`
  are left out; see the next section for reinstalling them.
- **Tabs and spaces.** Dia keeps them in `tabs.db`, one of Dia's own databases
  (also the skills and key-value stores), which are encrypted (their files do not
  have a plain SQLite header). An encrypted browser profile is normally tied to a
  key held in the Mac's Keychain, so they cannot be read on another Mac and Dia
  resets them. Moving that key would mean handling a secret from the old Mac,
  which provisioning does not do. If Dia syncs these for your account, signing in
  on the new Mac is the way to get them back; this is not verified.
- History, cookies, saved logins, autofill, site data and caches.

Because Dia creates these files on first launch, copy **before opening Dia**: a
file Dia already created is kept, not replaced. The step checks whether Dia is
running first (`tasks/wait-for-app-quit.yml`): if it is, a dialog asks you to quit
it (Cmd+Q) and confirm, then checks again, repeating until Dia is closed. Abort
(the default), no usable terminal, or ten failed checks stop with your answers
saved, since a browser rewrites its settings when it quits. **Skip copy** (the fourth button,
also in the Safari/Mail/Messages quit dialogs of the private-data copy) leaves that copy out
for this run without stopping provisioning; the saved answer is kept, so it is asked again
next time.

### Reinstalling Dia extensions from the backup list

After the settings question, `time-machine-dia_playbook.yml` asks whether to open
the Chrome Web Store page in Dia for each extension that was installed in Dia in
the same backup (answer saved as `time_machine.dia_extensions`, `open` or `skip`,
asked even when the settings copy is declined). It reads only each extension's
`manifest.json` (and the default-language messages for a localized name) from the
read-only snapshot; the same helper run that copies the settings reports the list,
and declining the settings copy runs it once with `--dia-extensions` (a second
attach). Extensions this Dia already has registered (from its `Secure
Preferences`/`Preferences`) are skipped. For each remaining one it runs
`open -a Dia https://chromewebstore.google.com/detail/<id>`; you choose **Add to
Dia** on each page, and nothing is installed without your click. It needs Full
Disk Access, and Dia may be running (only the settings copy needs it closed). Run
again later and it opens only the ones still missing. `TEST` opens only the
first page.

`TEST` copies only `Preferences`. Copied files and created folders are recorded
before `rsync` starts in `~/.homelab-provision-state/time-machine-dia.jsonl`
(work files in `time-machine-dia-work`), and undo removes only the files that
are still unchanged, like for the home folders. The helper is the same
`files/copy_time_machine_downloads.py` with `--dia`.

**TODO:** when **New computer** is implemented, ask the same copy question,
but first pick a computer from the share and then one of that computer's
versions (or no copy). Only the **New version of existing computer** path asks
it today.

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
macOS AutoFill confirmation, Finder connection confirmation, and each Time
Machine choice. Abort and invalid
input are never saved as accepted answers; previously accepted steps survive
later cancellation or failure. Choosing a TODO path records that choice too.

After bootstrap installs Ansible and `dialog`, `provision.sh` shows all saved
answers in aligned key/value columns (one answer per line) and the full JSON path **before 1Password and the other wizard stages**.
**Use previous answers** skips answered questions and continues at the first
unanswered one. **Pick new values** deletes the JSON immediately and asks again;
subsequent accepted answers start a new file. **Abort** preserves the file and
ends provisioning. Abort remains the default, including with `--automatically-approve`.
The manual version input retains its documented text-focus exception.

Saved confirmations suppress repeat prompts; they do not bypass live readiness,
SSH-agent, or repository-access checks, installation checks, or account login
verification. Passwords, tokens and private keys are never saved. Finder is
prompted again only if the share is not actually mounted; the Time Machine inventory is
still read if its selection is incomplete. A completed Time Machine selection
restores its facts without rescanning or recomputing its version proposal.
A completed selection saved before the copy question existed has no
`copy_from`; reusing it reruns the wizard, replays the saved answers, and asks
only the copy question.
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
