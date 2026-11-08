# Provisioning and temporary rollback

- All interactive dialogs must default to Abort/Stop/Exit, never Select or
  Continue. Use --defaultno for yes/no checkpoints and --default-button cancel
  for menus. Require an explicit choice to proceed; keep Escape as abort.
- Show `--unattended` in the bootstrap command at the top of `provision.sh` and
  in both `taerar/README.md` and this directory's README. The switch must select
  the installation-progressing answer for confirmation prompts using each tool's
  supported noninteractive options. Without it, preserve interactive prompts.
  Apply this rule to every future bootstrap command and Ansible playbook task;
  pass the selected mode into playbooks when they are introduced. Do not pipe
  blanket `yes` into commands or attempt to auto-answer passwords or OS dialogs.
- Keep shell provisioning limited to bootstrap: creating the code directory,
  cloning the repository, and installing Homebrew and Ansible. After Ansible is
  installed, implement all subsequent provisioning actions as Ansible playbook
  tasks, invoked by `provision.sh`. This includes the planned dotfile installation
  (`make install-dotfiles`); do not add it as a direct shell provisioning step.
- Set `ansible_python_interpreter: "{{ ansible_playbook_python }}"` for local
  playbooks so tasks use the same Python environment as Ansible, without system
  interpreter discovery or hardcoded Homebrew version paths.
- Keep `make-dependencies_playbook.yml` and its README dependency table aligned
  with the root Makefile and the scripts its targets invoke. Install runtime
  dependencies through Ansible, preserve existing tools, and track new formulae,
  casks and dependencies for undo. Do not run privileged/server Make targets or
  start unrelated account logins as part of installing their dependencies.
- Whenever `provision.sh` gains or changes a persistent action, update
  `undo-provision.sh` in the same change to reverse it in the opposite order.
- Run `dotfiles_playbook.yml` immediately after 1Password setup and before
  GitHub CLI installation/login. Both stages can change ~/.gitconfig: preserve
  its pre-dotfiles baseline in the GitHub snapshot and handle older rollback
  snapshots without restoring a later copy over the original file.
- Reuse `ansible/roles/prezto` for Linux and macOS. Keep clone/submodule/link
  logic shared, and package/login-shell handling in platform wrappers. Default
  target paths to `~`. Run the Mac Prezto playbook after dotfiles; retain existing
  checkouts and files, tracking only newly created resources for undo.
- Use `undo-provision.sh --keep JSON` for retention. Supported entries:
  `"gh":"preferences"`; `"1password":"app"` or `"preferences"`. Validate JSON
  before mutations, rejecting malformed input, duplicate keys, unknown keys and
  unsupported values. Never evaluate JSON as shell code.
- gh=preferences retains credentials and their rollback snapshot while allowing
  gh/Homebrew removal. Reprovisioning must reuse the login; full undo removes
  only the provisioning-created login. Never log or copy tokens into the repo.
- 1password=app retains both apps, preferences, mas, rollback records and
  provisioning-installed Homebrew so the cask remains managed. Full undo must
  still remove tracked installs later. Always preserve preferences/vault data;
  1password=preferences explicitly selects app removal with data retained.
- Open 1Password after installation, recording whether provisioning launched it.
  Only quit a provisioning-opened app during undo, unless the app is retained.
- After opening 1Password, check readiness before prompting. Skip sign-in only
  when the 1Password agent can perform a local sign/verify test. Otherwise ask
  for sign-in confirmation before SSH configuration. Check agent, GitHub SSH and
  Safari enabled state before showing the setup dialog; show it only if a check
  is not ready or unknown. Never equate process/extension installation with readiness. Verify GitHub SSH repository access before changing origin. These are
  read-only checks/manual app preferences; do not generate/export private keys,
  disable host verification or change app settings before sign-in confirmation.
- After sign-in confirmation, configure the GitHub-specific SSH IdentityAgent
  through Ansible, preserving the original SSH config for undo in every keep
  mode. Leave an already-correct config untouched. Do not edit 1Password private
  settings.json or silently enable Safari permissions; those remain UI steps.
- Install dialog early through Ansible. Show incomplete/unverified 1Password
  readiness checkpoints even with --unattended, but skip verified completed
  checkpoints. Unknown/unreadable state must never be treated as ready. No/Escape
  stops; do not read vault contents or log public keys/account data during probes.
- Before the setup prompt, test `ssh -T git@github.com` with the 1Password agent
  and strict host verification. GitHub's authenticated greeting plus exit code 1
  means success. Distinguish authentication, host-key, network and timeout errors;
  never tell the user to redo a verified completed step merely because Safari
  state is unknown. Keep the later repository-specific SSH access check too.
- Repository SSH failures must stop before origin changes and show plain-language
  guidance. Distinguish network failures from key rejection and host verification;
  do not blame credentials before authentication. Explain that earlier steps
  remain applied and provisioning can be rerun after resolving the failure.
- Before any rollback, check the checkout for staged, unstaged and untracked
  files. If dirty, show a dialog offering stage-all + make codex-commit (then
  recheck), ignore and proceed, or exit without rollback. Default to Exit; treat
  cancellation as Exit. If dialog/terminal is unavailable or commit fails, stop.
- After tools are installed, show the Finder NAS Time Machine share dialog. Copy the SMB address to
  the clipboard and show Finder/Cmd+K/paste/+/Connect instructions. Confirm
  continues; Abort/Escape stops, even with --unattended. This is a manual step:
  do not silently edit Finder favorites, store NAS credentials, or snapshot the
  previous clipboard. User-created favorites/connections are preserved by undo.
- Follow Finder confirmation with a read-only scan of top-level .sparsebundle
  directories in /Volumes/time-machine. Group a trailing space plus numeric
  version under its computer; list unversioned names directly. Show the inventory
  above Create new computer / Restore existing computer menu choices, including
  with --unattended. Abort/Escape stops. Do not enter or modify sparsebundles;
  keep the action as an in-memory Ansible fact until later steps are specified.
- Create opens a second menu with the same computer/version list and the
  subtypes new computer / new version of existing computer. Continue accepts;
  Back returns to the parent to correct the choice; Abort/Escape stops. Abort
  is the default on both screens. Keep Restore documented as TODO until its
  actual implementation is requested; do not restore or alter backups yet.
- Use one reusable computer_menu() renderer for inventory dialogs and the shared
  Python format_computers() formatter for list text. Show bullet points, one
  computer per line and tab-indented version bullets; do not duplicate rendering.
- Record changes in `~/.homelab-provision-state` outside the checkout. Track only
  resources created or modified by provisioning; preserve pre-existing resources.
  Record intent before operations that can partially fail and keep markers until
  rollback succeeds. Repeated provisioning runs must retain earlier markers.
- Keep rollback usable after a partial provisioning failure and when its own
  checkout is removed. Update the README with behavior and rollback limitations.
- Validate both scripts with `bash -n` and exercise changed branches using mocks
  or an isolated test environment. Do not install/uninstall real software or
  delete the working checkout just to test these scripts.
