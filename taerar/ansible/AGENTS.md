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
- Keep `undo-provision.sh --keep-gh-login` usable for repeated development tests:
  retain GitHub CLI credentials and their rollback snapshot, even when removing
  gh/Homebrew. A later normal provisioning run must reuse the login, and a full
  undo without the switch must remove only the login created by provisioning.
  Never print or copy tokens into logs, playbook output, or the repository.
- Record changes in `~/.homelab-provision-state` outside the checkout. Track only
  resources created or modified by provisioning; preserve pre-existing resources.
  Record intent before operations that can partially fail and keep markers until
  rollback succeeds. Repeated provisioning runs must retain earlier markers.
- Keep rollback usable after a partial provisioning failure and when its own
  checkout is removed. Update the README with behavior and rollback limitations.
- Validate both scripts with `bash -n` and exercise changed branches using mocks
  or an isolated test environment. Do not install/uninstall real software or
  delete the working checkout just to test these scripts.
