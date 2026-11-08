# Provisioning and temporary rollback

- Except for the manual Time Machine version input (which starts focused in
  its text field), all interactive dialogs must default to Abort, never Select or
  Continue. Use --defaultno for yes/no checkpoints and --default-button cancel
  for menus. Require an explicit choice to proceed; keep Escape as abort.
- Time Machine Abort/Escape must end the wizard cleanly with an explicit
  cancellation message, not dump the shell script as a failed Ansible command.
  Set DIALOG_ERROR separately from Escape so real rendering errors still fail
  with useful guidance. Keep cancelled selections distinct from completed ones.
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
- Run ai-tools_playbook.yml right after dotfiles: install the Claude desktop
  (cask claude) and Codex desktop (cask codex-app) apps, then each CLI only when
  not already available (claude-code, codex), tracked in ai-tools-casks and
  ai-tools-formulae for undo. Then sign in one tool at a time, but only after a
  live check shows it is signed out; never ask or open an app that is already
  signed in. Checks: claude auth status, codex login status (the Codex desktop
  app shares the CLI's sign-in), and for Claude desktop only whether
  oauth:tokenCacheV2 in its config.json is non-empty, never printing or reading
  its value. For a signed-out tool offer Sign in / Skip / Abort (default Abort),
  run claude auth login / codex login in the terminal or open the desktop app,
  and verify afterwards. Never handle credentials or save sign-in state; undo
  removes installs but keeps sign-ins and app data.
- Install the Dia browser right after the AI tools (dia_playbook.yml, cask
  thebrowsercompany-dia, Apple silicon and macOS 14+ only, skipped with a message
  otherwise), tracked in dia-casks and dia-formulae for undo before the AI tools.
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
  when the 1Password agent can perform a local sign/verify test, or the user
  explicitly reuses a saved sign-in confirmation. Otherwise ask
  for sign-in confirmation before SSH configuration. Check agent, GitHub SSH and
  Safari enabled state before showing the setup dialog; show it only if a check
  is not ready or unknown. Never equate process/extension installation with readiness. Verify GitHub SSH repository access before changing origin. These are
  read-only checks/manual app preferences; do not generate/export private keys,
  disable host verification or change app settings before sign-in confirmation.
- After sign-in confirmation, configure the GitHub-specific SSH IdentityAgent
  through Ansible, preserving the original SSH config for undo in every keep
  mode. Leave an already-correct config untouched. Do not edit 1Password private
  settings.json or silently enable Safari permissions; those remain UI steps.
- Detect 1Password macOS AutoFill read-only via pluginkit's credential-provider
  election (+ ready, - not ready, anything else unknown). Never enable it with
  pluginkit -e or other automation; list it in the setup dialog as a manual
  System Settings step. Save its confirmation under onepassword.autofill,
  separate from onepassword.setup, so older saved answers cannot hide it.
- Install dialog early through Ansible. Show incomplete/unverified 1Password
  readiness checkpoints even with --unattended, but skip verified completed
  checkpoints and explicitly reused saved confirmations. Unknown/unreadable state must never be treated as ready. Abort/Escape
  aborts; do not read vault contents or log public keys/account data during probes.
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
  recheck), ignore and proceed, or exit without rollback. Default to Abort; treat
  cancellation as Abort. If dialog/terminal is unavailable or commit fails, stop.
- After tools are installed, show the Finder NAS Time Machine share dialog. Copy the SMB address to
  the clipboard and show Finder/Cmd+K/paste/+/Connect instructions. Confirm
  continues; Abort/Escape stops, even with --unattended. This is a manual step:
  do not silently edit Finder favorites, store NAS credentials, or snapshot the
  previous clipboard. User-created favorites/connections are preserved by undo.
  Check the live smbfs mount at /Volumes/time-machine: show the dialog only
  while it is unmounted and recheck after Confirm. Saved confirmations and a
  reused completed selection must not skip it when a later step reads the share.
- Follow Finder confirmation with a read-only scan of top-level .sparsebundle
  directories in /Volumes/time-machine. Group a trailing space plus numeric
  version under its computer; list unversioned names directly. Show the inventory
  above Create new computer / Restore existing computer menu choices, including
  with --unattended. Abort/Escape stops. Do not enter or modify sparsebundles;
  save each accepted intermediate choice according to the shared-store rule
  below; never persist Abort or invalid input.
- Create opens a second menu with the same computer/version list and the
  subtypes new computer / new version of existing computer. Continue accepts;
  Back returns to the parent to correct the choice; Abort/Escape stops. Abort
  is the default on both screens. Keep Restore documented as TODO until its
  actual implementation is requested; do not restore or alter backups yet.
- Use one reusable computer_menu() renderer for inventory dialogs and the shared
  Python format_computers() formatter for list text. Show bullet points, one
  computer per line and tab-indented version bullets; do not duplicate rendering.
- Keep Restore and completely New computer documented as TODO. Develop the new
  version path first: its next screen repeats the full inventory above a menu
  of unique computer names only (no versions), in reverse alphabetical order.
  Map selections using that same ordered list. Use Continue/Abort/Back on every
  subsequent wizard screen, default Abort, and Back to the immediately previous
  screen while preserving its choice. Persist only completed numbered choices; no backup
  modifications until the corresponding implementation is requested.
- After picking an existing computer, show only that computer and its versions
  above Minor (new macOS or fresh installation) / Major (new hardware) choices,
  in that order because minor versions are more common.
  Reuse the shared formatter and renderer, retain Continue/Abort/Back with Abort
  default, and return Back to the computer picker preserving its selection.
  Keep minor backup creation documented as TODO; both types collect a number.
  Major proposes max(existing major numbers) + 1 with .0, or 1.0 if no numbered
  versions exist. Offer the proposal or manual major.minor entry, reject malformed
  or existing manual versions, and retain the chosen number as an Ansible fact
  and in the saved-choice JSON.
  Manual format must match ^[0-9]+\.[0-9]+$: at least one digit on each side
  of exactly one dot; allow leading zeros. Explain invalid input and let users retry.
  Keep all screens Continue/Abort/Back with Abort default; do not modify backups.
  Exception: the manual version input starts with text-field focus; omit
  --default-button cancel there. Explain that Tab after editing focuses Continue.
- Record changes in `~/.homelab-provision-state` outside the checkout. Track only
  resources created or modified by provisioning; preserve pre-existing resources.
  Record intent before operations that can partially fail and keep markers until
  rollback succeeds. Repeated provisioning runs must retain earlier markers.
- Keep rollback usable after a partial provisioning failure and when its own
  checkout is removed. Update the README with behavior and rollback limitations.
- Validate both scripts with `bash -n` and exercise changed branches using mocks
  or an isolated test environment. Do not install/uninstall real software or
  delete the working checkout just to test these scripts.

- Persist accepted answers from all provisioning wizards immediately in the
  Git-ignored time-machine-choices.json shared store (schema 2, answers by stable
  question ID, 0600, atomic merged writes). Record partial runs, never Abort or
  invalid input. Review before 1Password; reuse skips answered questions only,
  missing/new IDs still prompt. New selection deletes the file; undo removes it.
  Use tasks/save-wizard-answer.yml or files/wizard_choices.py for future questions.
  Changing parent choices invalidates dependent answers. Keep computer names
  stable; accept schema-1 files. Saved confirmations never replace live readiness
  or authentication checks; never store credentials. Future questions belong in
  a stage outside the completed-Time-Machine skip so reuse cannot hide them.
- Label every ncurses cancellation/exit choice Abort, never Stop or Exit.
- HOMELAB_PROVISION_ENV is PROD (default) or TEST: exported value, else the
  Git-ignored taerar/ansible/.env (parsed as text, never sourced), else PROD.
  Pass it to playbooks as provision_env; keep .env.sample at PROD. TEST must
  keep data copies minimal (e.g. one file) for small test machines.
- After the wizard, if a copy source was chosen, ask (saved as
  time_machine.downloads) whether to copy ~/Downloads from the latest backup in
  that bundle. Attach read-only and always detach; unlock encrypted volumes only
  through an interactive password prompt and never store it. Never overwrite
  existing files; check free space first. Record each created file/folder in
  the state directory before creating it; undo removes only unchanged copies and
  empty created folders. Explain Full Disk Access; never change privacy settings.
  Pick the newest com.apple.TimeMachine.*.backup snapshot via diskutil and mount
  only it read-only with noowners; ignore root-level folders without a snapshot.
  Do not use tmutil to list/mount backups (root-owned mounts, wrong owners, busy
  image). Unmount the snapshot before detaching; fall back to diskutil eject.
- After the Downloads step, ask (saved as time_machine.dia) whether to copy Dia
  configuration from the same selected backup. Copy only the plain settings
  listed in DIA_CONFIG (never cookies, logins, history, caches or Dia's encrypted
  databases), only while Dia is not running, never over existing files, in TEST
  only Preferences. Skip when Dia is not installed. Share the Full Disk Access and
  share-mounted conditions with Downloads: skip only when both copies are declined.
- Copy any files from Time Machine backups with Homebrew rsync 3.x (-lptX,
  --append, --files-from), never macOS openrsync or Python copies, which drop
  extended attributes. Install the rsync formula through Ansible only when a
  copy runs, tracked in time-machine-formulae for undo. Copy into a staging
  folder in the state folder (per snapshot, same volume as the destination) and
  move finished files into place with an atomic rename, so interrupted copies
  resume after any kill and no partial file ever reaches the destination. Never
  use --ignore-existing or --inplace against the destination, and never reuse
  staged data from another snapshot.
- Check Full Disk Access early (full-disk-access_playbook.yml, right after the
  saved-choices review) whenever the Downloads copy may still happen. Name the
  outermost .app; offer Abort (default), Open Settings (opens the Full Disk
  Access pane, then re-shows the dialog) or Continue without, which skips the
  copy with a message. Never grant access or save this state; it requires an
  app restart.
- After a new version number is accepted, ask last whether to copy data from an
  existing version of the selected computer: "none" or one row per existing
  version (including unversioned). Store the semantic value in time_machine.copy
  and result.copy_from; re-ask if the saved source no longer exists. Back returns
  to the proposal or manual screen. Treat a saved result without copy_from as
  incomplete so reuse still asks it. Copying itself remains TODO; never read or
  modify bundles. TODO for New computer: ask the same question after picking a
  source computer, then one of its versions.

- Display saved-answer summaries as aligned key/value columns, one answer per
  line. Use the shared format_summary() helper, including for future question IDs.
- Store semantic wizard values, never menu positions. Keep menu-to-value mappings
  in wizard_choices.py; summaries must use readable labels and answer meanings.
  Save a suggested/manual version source together with its actual version number.
  Migrate older numeric answers on read and preserve compatibility when resuming.
- Show a separate Version number row in the saved summary, using the completed,
  suggested, or manual number. If none was accepted, show Not selected yet.
- Both suggested and manual version acceptance must atomically persist the
  actual number in time_machine.number; reuse the exact accepted number.
- Both minor and major paths must ask for a concrete version via suggestion or
  manual input and save it. Minor suggests highest existing minor + 1; major
  suggests highest major + 1 with .0. Minor backup creation remains TODO.
