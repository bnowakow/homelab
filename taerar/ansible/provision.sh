#!/bin/bash
# Bootstrap from master (copy and run in Terminal):
# /bin/bash -c "$(curl -fsSL "https://raw.githubusercontent.com/bnowakow/homelab/master/taerar/ansible/provision.sh?$(date +%s)")" -- --automatically-approve
set -euo pipefail

auto_approve=false
for argument in "$@"; do
  case "$argument" in
    --automatically-approve) auto_approve=true ;;
    --help|-h)
      echo "Usage: $0 [--automatically-approve]"
      echo 'Without --automatically-approve, installation confirmations remain interactive.'
      exit 0
      ;;
    *) echo "Usage: $0 [--automatically-approve]" >&2; exit 2 ;;
  esac
done

# Use each installer's native confirmation controls, never a blanket yes pipe.
if [[ "$auto_approve" == true ]]; then
  export NONINTERACTIVE=1 HOMEBREW_NO_ASK=1
else
  unset NONINTERACTIVE HOMEBREW_NO_ASK
fi

if [[ $(uname -s) != Darwin ]]; then
  echo 'This script requires macOS.' >&2
  exit 1
fi

if [[ $EUID -eq 0 ]]; then
  echo 'Run this script as your normal user, without sudo.' >&2
  exit 1
fi

# A fresh macOS has no git: /usr/bin/git is a stub that only opens Apple's
# Command Line Tools installer and exits. Request the tools and wait for them
# before cloning. This is Apple's own installer (a dialog to click Install in);
# undo never removes the tools.
# Ask for the sudo password once (sudo prompts for it itself; it is never
# auto-answered, also with --automatically-approve) and keep the authorization alive in
# the background while provisioning runs. Homebrew's non-interactive installer
# and later steps (mas) need an already authorized sudo.
sudo_keepalive_pid=""
authorize_sudo() {
  sudo -v || return 1
  if [[ -z "$sudo_keepalive_pid" ]]; then
    local parent=$$
    ( while kill -0 "$parent" 2>/dev/null; do sudo -n true 2>/dev/null || exit 0; sleep 30; done ) &
    sudo_keepalive_pid=$!
    trap 'kill "$sudo_keepalive_pid" 2>/dev/null || true' EXIT
  fi
}

command_line_tools_ready() {
  local developer_dir
  developer_dir=$(xcode-select -p 2>/dev/null) && [[ -x "$developer_dir/usr/bin/git" ]]
}

# Install the tools from the terminal with Apple's own softwareupdate, which
# needs sudo: sudo asks for your password itself (never auto-answered, also
# with --automatically-approve). Returns non-zero when anything fails, so the caller falls
# back to the installer dialog.
install_command_line_tools_with_sudo() {
  local marker=/tmp/.com.apple.dt.CommandLineTools.installondemand.in-progress label
  echo 'Installing the Command Line Tools with softwareupdate; macOS asks for your password (sudo).'
  authorize_sudo || return 1
  # This marker makes softwareupdate list the Command Line Tools.
  touch "$marker"
  label=$(softwareupdate -l 2>/dev/null \
    | sed -n -E 's/^[[:space:]]*\*[[:space:]]+(Label:[[:space:]]+)?(Command Line Tools.*)$/\2/p' \
    | sort -V | tail -n 1)
  if [[ -z "$label" ]]; then
    rm -f "$marker"
    return 1
  fi
  echo "Installing: $label (this can take several minutes)"
  if ! sudo softwareupdate -i "$label"; then
    rm -f "$marker"
    return 1
  fi
  rm -f "$marker"
  sudo xcode-select --switch /Library/Developer/CommandLineTools >/dev/null 2>&1 || true
  command_line_tools_ready
}

if ! command_line_tools_ready; then
  echo 'macOS Command Line Tools (git) are missing.'
  if install_command_line_tools_with_sudo; then
    echo 'Command Line Tools are installed.'
  else
    echo 'Could not install them from the terminal; falling back to the installer dialog.'
  fi
fi

if ! command_line_tools_ready; then
  echo 'Requesting the installation...'
  xcode-select --install >/dev/null 2>&1 || true
  echo 'Click "Install" in the dialog that appeared and accept the license; this can take'
  echo 'several minutes. Provisioning continues by itself when the installation finishes.'
  waited=0
  until command_line_tools_ready; do
    sleep 5
    waited=$((waited + 5))
    if (( waited % 120 == 0 )); then
      echo "Still waiting for the Command Line Tools installation (${waited}s)..."
      # Reopen the dialog if it was dismissed.
      xcode-select --install >/dev/null 2>&1 || true
    fi
  done
  echo 'Command Line Tools are installed.'
fi

# Keep test rollback state outside the checkout, which undo may remove.
state_dir="$HOME/.homelab-provision-state"
umask 077
mkdir -p "$state_dir"

if [[ ! -e "$HOME/code" ]]; then
  touch "$state_dir/code-created"
  mkdir "$HOME/code"
fi

repo_dir="$HOME/code/homelab"
if [[ ! -e "$repo_dir" ]]; then
  echo "Cloning homelab into $repo_dir..."
  # Record intent first so a failed/interrupted clone can also be undone.
  touch "$state_dir/repo-created"
  git clone https://github.com/bnowakow/homelab.git "$repo_dir"
elif [[ ! -e "$repo_dir/.git" ]]; then
  echo "$repo_dir already exists but is not a Git checkout. Resolve this before continuing." >&2
  exit 1
else
  echo "Reusing homelab checkout: $repo_dir"
  # Playbooks run from this checkout, so bring it up to date when that cannot
  # lose anything: a clean tree on a branch that fast-forwards. Never fatal.
  if [[ -z "$(git -C "$repo_dir" status --porcelain 2>/dev/null)" ]]; then
    if git -C "$repo_dir" pull --ff-only --quiet 2>/dev/null; then
      echo 'Updated the checkout to the latest commit.'
    else
      echo 'Could not update the checkout (offline or diverged); continuing with the existing files.'
    fi
  else
    echo 'The checkout has local changes; not updating it.'
  fi
fi

find_brew() {
  if command -v brew >/dev/null 2>&1; then
    command -v brew
  elif [[ -x /opt/homebrew/bin/brew ]]; then
    echo /opt/homebrew/bin/brew
  elif [[ -x /usr/local/bin/brew ]]; then
    echo /usr/local/bin/brew
  else
    return 1
  fi
}

if brew_bin=$(find_brew); then
  echo "Homebrew is already installed: $brew_bin"
else
  echo 'Installing Homebrew; macOS asks for your password (sudo) if it is not authorized yet.'
  authorize_sudo || { echo 'Homebrew needs sudo access (an administrator account). Run the command again.' >&2; exit 1; }
  installer=$(/usr/bin/curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)
  touch "$state_dir/homebrew-installed"
  /bin/bash -c "$installer"
  unset installer
  if ! brew_bin=$(find_brew); then
    echo 'Homebrew installation finished, but brew could not be found.' >&2
    exit 1
  fi
fi

# Make Homebrew available to subsequent provisioning steps in this process.
eval "$("$brew_bin" shellenv)"
brew --version

if command -v ansible-playbook >/dev/null 2>&1; then
  echo "Ansible is already installed: $(command -v ansible-playbook)"
else
  echo 'Installing Ansible with Homebrew...'
  # Record missing formulae before installation, including partial failures.
  # Retain the original list across repeated runs until undo completes.
  if [[ ! -f "$state_dir/ansible-formulae" ]]; then
    missing_dependencies=$(brew deps --missing --formula ansible)
    installed_formulae=$(brew list --formula)
    {
      if [[ $'\n'"$installed_formulae"$'\n' != *$'\nansible\n'* ]]; then
        echo ansible
      fi
      if [[ -n "$missing_dependencies" ]]; then
        printf '%s\n' "$missing_dependencies"
      fi
    } > "$state_dir/ansible-formulae.tmp"
    mv "$state_dir/ansible-formulae.tmp" "$state_dir/ansible-formulae"
  fi
  HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALL_CLEANUP=1 \
    HOMEBREW_NO_INSTALL_UPGRADE=1 HOMEBREW_NO_INSTALLED_DEPENDENTS_CHECK=1 \
    brew install --formula ansible
fi
ansible-playbook --version

# PROD is the default. TEST (e.g. from a local, Git-ignored .env) limits data
# copies so provisioning can be tried on a machine with little free space.
# An exported HOMELAB_PROVISION_ENV wins over .env. The file is parsed as
# KEY=VALUE text and never sourced.
env_file="$repo_dir/taerar/ansible/.env"
if [[ -f "$env_file" ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    line=${line%$'\r'}
    if [[ -z "${HOMELAB_PROVISION_ENV:-}" && "$line" =~ ^[[:space:]]*HOMELAB_PROVISION_ENV[[:space:]]*=[[:space:]]*[\"\']?([A-Za-z]*)[\"\']?[[:space:]]*$ ]]; then
      HOMELAB_PROVISION_ENV=${BASH_REMATCH[1]}
    fi
    if [[ -z "${HOMELAB_LOCATION:-}" && "$line" =~ ^[[:space:]]*HOMELAB_LOCATION[[:space:]]*=[[:space:]]*[\"\']?([A-Za-z]*)[\"\']?[[:space:]]*$ ]]; then
      HOMELAB_LOCATION=${BASH_REMATCH[1]}
    fi
  done < "$env_file"
fi
provision_env=${HOMELAB_PROVISION_ENV:-PROD}
case "$provision_env" in
  PROD|TEST) ;;
  *) echo "HOMELAB_PROVISION_ENV must be PROD or TEST, not '$provision_env' (check $env_file)." >&2; exit 2 ;;
esac
# local (default): on the home network. remote: away from it, over Tailscale; every
# *.localdomain.bnowakowski.pl address becomes its *.tailscale.bnowakowski.pl twin.
provision_location=${HOMELAB_LOCATION:-local}
case "$provision_location" in
  local) nas_domain=localdomain.bnowakowski.pl ;;
  remote) nas_domain=tailscale.bnowakowski.pl ;;
  *) echo "HOMELAB_LOCATION must be local or remote, not '$provision_location' (check $env_file)." >&2; exit 2 ;;
esac
echo "Provisioning environment: $provision_env; location: $provision_location (NAS domain $nas_domain)"
# Tells the backup helper which NAS address a kept attachment belongs to.
export HOMELAB_NAS_DOMAIN="$nas_domain"

# All subsequent provisioning actions belong in Ansible playbooks.
# Ansible workers do not retain a controlling terminal; pass its device path
# explicitly so confirmations and the browser device code remain visible.
terminal_name=$(ps -p $$ -o tty=)
terminal_name=${terminal_name//[[:space:]]/}
provision_tty="/dev/${terminal_name#/dev/}"
if [[ -z "$terminal_name" || "$terminal_name" == '??' || ! -c "$provision_tty" ]]; then
  echo 'Run provisioning from an interactive Terminal for GitHub browser login.' >&2
  exit 1
fi
export HOMELAB_PROVISION_TTY="$provision_tty"

# One log per run in ~/Library/Logs/homelab-provision (newest: provision-latest.log).
# Ansible writes everything it prints there (tasks, results, errors); the copy
# helpers append their own messages and rsync progress. Nothing is rotated or
# deleted automatically; rotate with newsyslog/logrotate when the logs grow.
log_dir="$HOME/Library/Logs/homelab-provision"
( umask 077; mkdir -p "$log_dir" )
HOMELAB_PROVISION_LOG="$log_dir/provision-$(date +%Y%m%d-%H%M%S).log"
( umask 077; : > "$HOMELAB_PROVISION_LOG" )
ln -sfn "$HOMELAB_PROVISION_LOG" "$log_dir/provision-latest.log"
export HOMELAB_PROVISION_LOG ANSIBLE_LOG_PATH="$HOMELAB_PROVISION_LOG"
# Apps that failed to install are appended here and listed once the run ends.
HOMELAB_FAILED_APPS="$log_dir/failed-apps-$(date +%Y%m%d-%H%M%S).txt"
( umask 077; : > "$HOMELAB_FAILED_APPS" )
export HOMELAB_FAILED_APPS
# The Time Machine backup is attached once (slow over the network) and reused by
# every copy step; the question at the end of the run decides if it stays attached.
export HOMELAB_KEEP_BACKUP_ATTACHED=true
echo "Logging to $HOMELAB_PROVISION_LOG"
ansible-playbook -i localhost, -c local \
  "$repo_dir/taerar/ansible/terminal-ui_playbook.yml" \
  --extra-vars "{\"provision_auto_approve\": $auto_approve, \"provision_env\": \"$provision_env\", \"provision_location\": \"$provision_location\", \"nas_domain\": \"$nas_domain\"}"
playbook_status=0
ansible-playbook -i localhost, -c local \
  "$repo_dir/taerar/ansible/provision_playbook.yml" \
  --extra-vars "{\"provision_auto_approve\": $auto_approve, \"provision_env\": \"$provision_env\", \"provision_location\": \"$provision_location\", \"nas_domain\": \"$nas_domain\"}" \
  || playbook_status=$?

# The Dock was restarted when its settings were restored, before Dropbox synced its folders,
# so a cloud folder tile (e.g. Zrzuty ekranu) shows "?" until the Dock restarts once more.
python3 -I "$repo_dir/taerar/ansible/files/refresh_dock_cloud_tiles.py" || true

backup_attachment="$HOME/.homelab-provision-state/backup-attachment.json"
if [[ -f "$backup_attachment" ]]; then
  keep_backup=false
  # Always ask, also with --automatically-approve: leaving the backup attached
  # is the user's call. "Keep attached" is the default; only an explicit "Detach it" detaches.
  backup_question='The Time Machine backup is still attached read-only (attaching over the network is slow).

Keep it attached? Keep attached (default): a rerun of provisioning skips the slow attach. Downsides: it stays mounted until you detach it or reboot, it keeps the NAS share busy (the share cannot be ejected and the backup cannot be used for backups meanwhile), and Finder or apps can hang if the NAS goes offline.'
  if command -v dialog >/dev/null 2>&1; then
    dialog_status=0
    dialog --clear --title 'Time Machine backup' --yes-label 'Detach it' --no-label 'Keep attached' --default-button no \
      --yesno "${backup_question//$'\n'/\\n}" 16 78 \
      < "$provision_tty" > "$provision_tty" 2>&1 || dialog_status=$?
    if [[ "$dialog_status" != 0 ]]; then keep_backup=true; fi
  else
    # No dialog: plain prompt; anything but an explicit "d" keeps it attached.
    printf '%s\nDetach it now? [d = detach / Enter = keep attached] ' "$backup_question" > "$provision_tty"
    answer=''
    read -r answer < "$provision_tty" || true
    case "$answer" in [dD]*) ;; *) keep_backup=true ;; esac
  fi
  if [[ "$keep_backup" == true ]]; then
    echo "Time Machine backup left attached. Detach it later: python3 $repo_dir/taerar/ansible/files/copy_time_machine_downloads.py --detach"
  else
    echo 'Detaching the Time Machine backup...'
    python3 "$repo_dir/taerar/ansible/files/copy_time_machine_downloads.py" --detach > /dev/null || echo 'Could not detach it; run hdiutil info and eject it.' >&2
  fi
fi

if [[ -s "$HOMELAB_FAILED_APPS" ]]; then
  echo
  echo "Apps that failed to install ($(wc -l < "$HOMELAB_FAILED_APPS" | tr -d ' ')):"
  sed 's/^/  - /' "$HOMELAB_FAILED_APPS"
  echo "List saved in $HOMELAB_FAILED_APPS; details in $HOMELAB_PROVISION_LOG"
fi
exit "$playbook_status"
