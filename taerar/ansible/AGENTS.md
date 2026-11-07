# Provisioning and temporary rollback

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
- After opening 1Password, first show a sign-in confirmation dialog. Only after
  confirmation show SSH agent setup instructions, then verify the agent offers
  a key. Verify GitHub SSH repository access before changing origin. These are
  read-only checks/manual app preferences; do not generate/export private keys,
  disable host verification or change app settings before sign-in confirmation.
- After sign-in confirmation, configure the GitHub-specific SSH IdentityAgent
  through Ansible, preserving the original SSH config for undo in every keep
  mode. Leave an already-correct config untouched. Do not edit 1Password private
  settings.json or silently enable Safari permissions; those remain UI steps.
- Install dialog early through Ansible. Always show the 1Password readiness
  checkpoint before GitHub installation, including with --unattended; this is an
  explicit exception to automatic installation confirmations. No/Escape stops.
- Before any rollback, check the checkout for staged, unstaged and untracked
  files. If dirty, show a dialog offering stage-all + make codex-commit (then
  recheck), ignore and proceed, or exit without rollback. Default to Exit; treat
  cancellation as Exit. If dialog/terminal is unavailable or commit fails, stop.
- Record changes in `~/.homelab-provision-state` outside the checkout. Track only
  resources created or modified by provisioning; preserve pre-existing resources.
  Record intent before operations that can partially fail and keep markers until
  rollback succeeds. Repeated provisioning runs must retain earlier markers.
- Keep rollback usable after a partial provisioning failure and when its own
  checkout is removed. Update the README with behavior and rollback limitations.
- Validate both scripts with `bash -n` and exercise changed branches using mocks
  or an isolated test environment. Do not install/uninstall real software or
  delete the working checkout just to test these scripts.
