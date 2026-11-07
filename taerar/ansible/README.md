# macOS provisioning

This directory will contain the Ansible setup for provisioning macOS machines,
following the approach used by [`nas/ansible/provision.sh`](../../nas/ansible/provision.sh)
for Linux servers.

This document records the manual preparation required before running Ansible.
`provision.sh` currently creates `~/code`, clones this repository if missing, and
ensures Homebrew and Ansible are installed, then runs local Ansible playbooks
to install 1Password for Mac and Safari, install GitHub CLI, log in through the
browser, and switch the repository
origin to SSH, and install repository dotfiles into the home directory.

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

To keep the GitHub login between development runs:

```sh
./taerar/ansible/undo-provision.sh --keep-gh-login
```

This retains the credentials in GitHub CLI's configuration/macOS keychain and
the original configuration snapshot in `~/.homelab-provision-state/gh-login`.
GitHub CLI itself can still be uninstalled. Provision again to reinstall it and
reuse the login. For the final full test, run undo without `--keep-gh-login`
while `gh` is installed; this logs out the provisioning-created account and
restores the original GitHub CLI and global Git configuration (interactive login
can configure Git's credential helper). Pre-existing valid logins are preserved.
Configuration changes made after the snapshot will be replaced during full undo.

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
dotfiles, restore the original origin URLs, undo the
GitHub login unless retained, remove GitHub CLI and its new dependencies, remove
the newly installed Safari extension, 1Password desktop app and mas dependencies,
then remove Ansible and its newly installed dependencies, uninstall Homebrew using the
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

After bootstrap, `provision.sh` runs `onepassword_playbook.yml` before GitHub
login. It installs the Homebrew `1password` cask and `mas` if missing, then uses
`mas get 1569813296` to install [1Password for Safari](https://apps.apple.com/us/app/1password-for-safari/id1569813296?mt=12).
Existing applications are preserved. The Mac App Store can require Apple Account
or administrator authentication even with `--unattended`.

After installation, sign in to 1Password for Mac and enable **Safari > Settings >
Extensions > 1Password for Safari**, approving website permissions as needed.
These account/OS steps are manual; provisioning prints the instructions but does
not wait for their completion. Undo removes only applications/tools installed
by provisioning. It does not use Homebrew `--zap` or delete vault data, app
preferences, or Apple Account purchase history. `--keep-gh-login` retains only
GitHub authentication, not the 1Password apps.

It then runs `github-cli_playbook.yml` locally, forwarding the `--unattended`
setting. It installs `gh` using Homebrew and checks
`gh auth status --hostname github.com`. If no login exists, it runs
`gh auth login --hostname github.com --web --git-protocol https --skip-ssh-key`.
The device code and browser instructions appear directly in Terminal; browser
authorization still requires your participation with `--unattended`. Existing
credentials that fail validation stop provisioning rather than being overwritten.
It then runs `repository-origin_playbook.yml` to switch `origin` fetch and any
explicit push URLs to `git@github.com:bnowakow/homelab.git`. The original URLs
are saved once and restored by undo, including with `--keep-gh-login`. Other
remote settings are preserved. This step does not create or upload SSH keys;
SSH access requires a key configured on GitHub.

Finally, `dotfiles_playbook.yml` runs `make install-dotfiles` from `~/code/homelab`.
It uses the existing shared file list and hostname-specific `.screenrc` mapping.
During provisioning, changed dotfiles are snapshotted under
`~/.homelab-provision-state/dotfiles` before replacement. Undo restores original
files (including symlinks and permissions) and removes files that were originally
absent. Repeated runs keep the original snapshots. As with other test rollback,
changes you make to installed dotfiles afterward are replaced by undo.
Standalone `make install-dotfiles` retains its usual `~/.homelab-dotfiles-backup.*`
backups and does not participate in provisioning rollback.

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
