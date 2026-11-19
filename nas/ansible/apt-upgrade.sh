#!/usr/bin/env -S LC_ALL=C.utf8 bash
set -euo pipefail
export LC_ALL=C.utf8

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd "$script_dir"

usage() {
  printf 'Usage: %s [--limit HOST_PATTERN]\n' "$0" >&2
}

limit_pattern=all
if [[ $# -gt 0 ]]; then
  if [[ $# -ne 2 || $1 != --limit || -z $2 ]]; then
    usage
    exit 2
  fi
  limit_pattern=$2
fi

inventory_files=(inventory/proxmox-vms.yml inventory/ovh.yml)
matched_hosts_text=$(ansible -i "${inventory_files[0]}" -i "${inventory_files[1]}" \
  all --list-hosts --limit "$limit_pattern")
selected_hosts=()
while IFS= read -r line; do
  [[ $line == '    '* ]] && selected_hosts+=("${line#    }")
done <<< "$matched_hosts_text"
if (( ${#selected_hosts[@]} == 0 )); then
  printf 'No hosts match %s.\n' "$limit_pattern" >&2
  exit 2
fi

log_dir="$script_dir/logs"
mkdir -p -m 700 -- "$log_dir"
log_file=$(umask 077; mktemp "$log_dir/apt-upgrade-$(date +%Y%m%d-%H%M%S).XXXXXXXX.log")
export ANSIBLE_LOG_PATH="$log_file"
skipped_hosts_file=$(umask 077; mktemp "$log_dir/apt-upgrade-skipped.XXXXXXXX")
export APT_UPGRADE_SKIPPED_HOSTS_FILE="$skipped_hosts_file"
printf 'Ansible log: %s\n' "$log_file"
trap 'printf "Ansible log: %s\n" "$log_file" >&2' EXIT

declare -A selected_host_set=()
for host in "${selected_hosts[@]}"; do
  selected_host_set["$host"]=1
done

bold_blue=
normal=
ansible_force_color=false
if [[ -t 1 ]]; then
  bold_blue=$'\033[1;34m'
  normal=$'\033[0m'
  ansible_force_color=true
fi

run_ubuntu_repo_playbook() {
  local host=$1
  local mode=$2
  local series=$3

  printf '\nSudo password for %s%s%s (Ubuntu repository %s):\n' \
    "$bold_blue" "$host" "$normal" "$mode"
  ansible-playbook -i inventory/proxmox-vms.yml ubuntu-release-repositories_playbook.yml \
    --limit "$host" -e "ubuntu_repo_mode=$mode" -e "ubuntu_target_series=$series" \
    --ask-become-pass
}

for inventory_file in "${inventory_files[@]}"; do
  inventory_hosts_text=$(ansible-inventory -i "$inventory_file" --list |
    jq -r '[.[] | objects | .hosts[]?] | unique | .[]')
  inventory_targets=()
  while IFS= read -r host; do
    if [[ -n $host && -n ${selected_host_set[$host]:-} ]]; then
      inventory_targets+=("$host")
    fi
  done <<< "$inventory_hosts_text"
  (( ${#inventory_targets[@]} > 0 )) || continue
  inventory_limit=$(IFS=,; printf '%s' "${inventory_targets[*]}")
  printf '\nSudo password for hosts in %s%s%s:\n' \
    "$bold_blue" "$inventory_file" "$normal"
  printf '\t%s\n' "${inventory_targets[@]}"
  APT_UPGRADE_HOST_COLOR="$bold_blue" APT_UPGRADE_HOST_RESET="$normal" \
    ANSIBLE_FORCE_COLOR="$ansible_force_color" \
    ansible-playbook -i "$inventory_file" apt-upgrade_playbook.yml \
    --limit "$inventory_limit" --ask-become-pass

  remaining_targets=()
  for host in "${inventory_targets[@]}"; do
    if ! grep -qxF -- "$host" "$skipped_hosts_file"; then
      remaining_targets+=("$host")
    fi
  done
  (( ${#remaining_targets[@]} > 0 )) || continue
  inventory_limit=$(IFS=,; printf '%s' "${remaining_targets[*]}")
  printf '\nUpgrading Codex for sup on hosts in %s%s%s: %s\n' \
    "$bold_blue" "$inventory_file" "$normal" "$inventory_limit"
  ansible-playbook -i "$inventory_file" codex-upgrade_playbook.yml \
    --limit "$inventory_limit"
  printf '\nUpgrading Claude Code for sup on hosts in %s%s%s: %s\n' \
    "$bold_blue" "$inventory_file" "$normal" "$inventory_limit"
  ansible-playbook -i "$inventory_file" claude-upgrade_playbook.yml \
    --limit "$inventory_limit"
done

# Ubuntu release upgrades are interactive and apply only to opted-in hosts.
# Debian and Proxmox release upgrades require their own procedures.
ubuntu_hosts_text=$(
  ansible-inventory -i inventory/proxmox-vms.yml --list |
    jq -r '.ubuntu_release_upgrade.hosts[]?'
)
ubuntu_hosts=()
if [[ -n "$ubuntu_hosts_text" ]]; then
  mapfile -t ubuntu_hosts <<< "$ubuntu_hosts_text"
fi

for host in "${ubuntu_hosts[@]}"; do
  [[ -n ${selected_host_set[$host]:-} ]] || continue
  if grep -qxF -- "$host" "$skipped_hosts_file"; then
    printf 'Skipping Ubuntu release upgrade on %s: NordVPN reboot was declined.\n' "$host"
    continue
  fi
  if ! ssh -o BatchMode=yes -o ConnectTimeout=10 "$host" \
    'test "$(. /etc/os-release; printf %s "$ID")" = ubuntu'; then
    printf 'Skipping Ubuntu release check on %s: host is unreachable or is not Ubuntu.\n' "$host" >&2
    continue
  fi

  current_series=$(ssh -o BatchMode=yes -o ConnectTimeout=10 "$host" \
    '. /etc/os-release; printf %s "$VERSION_CODENAME"')
  [[ "$current_series" =~ ^[a-z]+$ ]] || {
    printf 'Invalid installed Ubuntu series for %s: %s\n' "$host" "$current_series" >&2
    exit 1
  }
  run_ubuntu_repo_playbook "$host" resume "$current_series"

  if ! release_check=$(ssh -o BatchMode=yes -o ConnectTimeout=10 "$host" 'do-release-upgrade -c' 2>&1); then
    printf '%s: no Ubuntu release upgrade is offered (or the check failed):\n%s\n' "$host" "$release_check"
    continue
  fi
  printf '%s:\n%s\n' "$host" "$release_check"

  target_series=$(ssh -o BatchMode=yes -o ConnectTimeout=10 "$host" 'python3 - target' \
    < files/ubuntu_release_repos.py)
  [[ "$target_series" =~ ^[a-z]+$ ]] || {
    printf 'Invalid Ubuntu target series for %s: %s\n' "$host" "$target_series" >&2
    exit 1
  }

  run_ubuntu_repo_playbook "$host" plan "$target_series"

  if [[ ! -t 0 || ! -t 1 ]]; then
    printf 'An interactive terminal is required to approve and run the Ubuntu release upgrade.\n' >&2
    continue
  fi
  read -r -p "Upgrade $host to Ubuntu $target_series now? [y/N] " answer
  case "$answer" in
    y|Y|yes|YES|Yes) ;;
    *) printf 'Skipped Ubuntu release upgrade on %s.\n' "$host"; continue ;;
  esac

  run_ubuntu_repo_playbook "$host" quiet "$target_series"

  upgrade_result=0
  printf '\nSudo password for %s%s%s (interactive Ubuntu release upgrade):\n' \
    "$bold_blue" "$host" "$normal"
  ssh -tt -o BatchMode=yes -o ConnectTimeout=10 "$host" 'sudo do-release-upgrade' || upgrade_result=$?

  current_series=
  for attempt in {1..90}; do
    current_series=$(ssh -o BatchMode=yes -o ConnectTimeout=5 "$host" \
      '. /etc/os-release; printf %s "$VERSION_CODENAME"' 2>/dev/null) || current_series=
    [[ "$current_series" == "$target_series" ]] && break
    if (( upgrade_result != 255 )) && [[ -n "$current_series" ]]; then
      break
    fi
    sleep 10
  done
  if [[ "$current_series" != "$target_series" ]]; then
    printf 'Ubuntu release upgrade did not complete on %s; saved repository plan was not applied.\n' "$host" >&2
    exit 1
  fi

  run_ubuntu_repo_playbook "$host" apply "$target_series"
done
