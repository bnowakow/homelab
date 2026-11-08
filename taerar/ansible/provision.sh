#!/bin/bash
# Bootstrap from master (copy and run in Terminal):
# /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/bnowakow/homelab/master/taerar/ansible/provision.sh)" -- --unattended
set -euo pipefail

unattended=false
for argument in "$@"; do
  case "$argument" in
    --unattended) unattended=true ;;
    --help|-h)
      echo "Usage: $0 [--unattended]"
      echo 'Without --unattended, installation confirmations remain interactive.'
      exit 0
      ;;
    *) echo "Usage: $0 [--unattended]" >&2; exit 2 ;;
  esac
done

# Use each installer's native confirmation controls, never a blanket yes pipe.
if [[ "$unattended" == true ]]; then
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
  echo 'Installing Homebrew...'
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
ansible-playbook -i localhost, -c local \
  "$repo_dir/taerar/ansible/terminal-ui_playbook.yml" \
  --extra-vars "{\"provision_unattended\": $unattended}"
ansible-playbook -i localhost, -c local \
  "$repo_dir/taerar/ansible/provision_playbook.yml" \
  --extra-vars "{\"provision_unattended\": $unattended}"
