#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C.utf8

if [[ $# -ne 0 ]]; then
  echo "Usage: $0" >&2
  exit 2
fi

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd "$script_dir"

inventory=inventory/proxmox-vms.yml
bold_blue=
normal=
if [[ -t 1 ]]; then
  bold_blue=$'\033[1;34m'
  normal=$'\033[0m'
fi
temporary_become_password_files=()
selected_become_password_file=

cleanup() {
  local password_file
  for password_file in "${temporary_become_password_files[@]}"; do
    rm -- "$password_file"
  done
}
trap cleanup EXIT

select_become_password_file() {
  local host_group=$1
  local supplied_file=$2
  local become_password

  if [[ -n "$supplied_file" ]]; then
    if [[ ! -r "$supplied_file" ]]; then
      echo "Cannot read password file for $host_group: $supplied_file" >&2
      exit 2
    fi
    selected_become_password_file=$supplied_file
    return
  fi

  umask 077
  read -r -s -p "Sudo password for ${bold_blue}${host_group}${normal}: " become_password
  printf '\n'
  if [[ -z "$become_password" ]]; then
    echo 'A sudo password is required.' >&2
    exit 2
  fi
  selected_become_password_file=$(mktemp /tmp/ansible-become-password.XXXXXX)
  printf '%s\n' "$become_password" >"$selected_become_password_file"
  temporary_become_password_files+=("$selected_become_password_file")
  unset become_password
}

select_become_password_file 'VM and Proxmox hosts' "${ANSIBLE_BECOME_PASSWORD_FILE:-}"
proxmox_become_password_file=$selected_become_password_file
export ANSIBLE_BECOME_PASSWORD_FILE=$proxmox_become_password_file

# The root-only bootstrap is a separate, one-time playbook.
for playbook in initial-config_playbook.yml ssh-private-key_playbook.yml git-config_playbook.yml; do
  ansible-playbook -i "$inventory" "$playbook"
done
ansible-playbook -i "$inventory" zabbix-agent2_playbook.yml
ansible-playbook -i "$inventory" zabbix-update-metadata_playbook.yml
ansible-playbook -i "$inventory" tailscale_playbook.yml
ansible-playbook -i "$inventory" proxmox-post-install_playbook.yml
ansible-playbook -i "$inventory" codex-sudo_playbook.yml
ansible-playbook -i "$inventory" codex-config_playbook.yml
ansible-playbook -i "$inventory" claude-install_playbook.yml
ansible-playbook -i "$inventory" claude-upgrade_playbook.yml

# NAS and OVH are outside the VM inventory and can have different sudo passwords.
select_become_password_file NAS "${NAS_ANSIBLE_BECOME_PASSWORD_FILE:-}"
export ANSIBLE_BECOME_PASSWORD_FILE=$selected_become_password_file
ansible-playbook -i inventory/nas-local.yml github-ssh_playbook.yml
ansible-playbook -i inventory/nas-local.yml ssh-private-key_playbook.yml
ansible-playbook -i inventory/nas-local.yml zabbix-agent2_playbook.yml
ansible-playbook -i inventory/nas-local.yml zabbix-repository-migration_playbook.yml
ansible-playbook -i inventory/nas-local.yml zabbix-update-metadata_playbook.yml
ansible-playbook -i inventory/nas-local.yml codex-sudo_playbook.yml
ansible-playbook -i inventory/nas-local.yml codex-config_playbook.yml
ansible-playbook -i inventory/nas-local.yml claude-install_playbook.yml
ansible-playbook -i inventory/nas-local.yml claude-upgrade_playbook.yml
ansible-playbook -i inventory/nas-local.yml sup-dotfiles_playbook.yml

select_become_password_file OVH "${OVH_ANSIBLE_BECOME_PASSWORD_FILE:-}"
export ANSIBLE_BECOME_PASSWORD_FILE=$selected_become_password_file
ansible-playbook -i inventory/ovh.yml github-ssh_playbook.yml
ansible-playbook -i inventory/ovh.yml ssh-private-key_playbook.yml
ansible-playbook -i inventory/ovh.yml zabbix-agent2_playbook.yml
ansible-playbook -i inventory/ovh.yml zabbix-repository-migration_playbook.yml
ansible-playbook -i inventory/ovh.yml zabbix-update-metadata_playbook.yml
ansible-playbook -i inventory/ovh.yml tailscale_playbook.yml
ansible-playbook -i inventory/ovh.yml codex-sudo_playbook.yml
ansible-playbook -i inventory/ovh.yml codex-config_playbook.yml
ansible-playbook -i inventory/ovh.yml claude-install_playbook.yml
ansible-playbook -i inventory/ovh.yml claude-upgrade_playbook.yml
ansible-playbook -i inventory/ovh.yml sup-dotfiles_playbook.yml
ansible-playbook -i inventory/ovh.yml ovh-hestia-backup-space_playbook.yml

export ANSIBLE_BECOME_PASSWORD_FILE=$proxmox_become_password_file

# Prezto is currently configured only for proxmox5 in its playbook.
ansible-playbook -i "$inventory" proxmox-prezto_playbook.yml

# Dotfiles are for Proxmox hosts, NAS and OVH; non-Proxmox VMs are not targeted.
# Files and the per-host .screenrc mapping: vars/sup-dotfiles.yml.
ansible-playbook -i "$inventory" sup-dotfiles_playbook.yml --limit 'proxmox*'

# Package and firmware upgrades remain separate because apt-upgrade_playbook.yml
# may require a reboot confirmation.
