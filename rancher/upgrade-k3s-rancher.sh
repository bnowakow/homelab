#!/bin/bash

set -Eeuo pipefail

kubeconfig_path="/etc/rancher/k3s/k3s.yaml"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
homelab_root="$(cd "$script_dir/.." && pwd)"
ansible_group_vars_file="$script_dir/ansible/group_vars/all.yml"
cert_manager_fleet_file="$script_dir/fleet/infrastructure/cert-manager/fleet.yaml"
log_dir="$script_dir/logs"
timestamp="$(date +"%Y%m%d-%H%M%S")"
log_file="$log_dir/upgrade-k3s-rancher-${timestamp}.log"
metadata_cache_dir="/var/cache/homelab/upgrade-k3s-rancher"
rancher_matrix_index_url="https://www.suse.com/suse-rancher/support-matrix/all-supported-versions/"
rancher_matrix_current_url="https://www.suse.com/suse-rancher/support-matrix"
rancher_chart_index_url="https://releases.rancher.com/server-charts/stable/index.yaml"
k3s_releases_api_url="https://api.github.com/repos/k3s-io/k3s/releases?per_page=100"
cert_manager_releases_api_url="https://api.github.com/repos/cert-manager/cert-manager/releases?per_page=100"
rollout_timeout="${ROLLOUT_TIMEOUT:-10m}"
pending_rancher_upgrade_max_age_seconds="${PENDING_RANCHER_UPGRADE_MAX_AGE_SECONDS:-1800}"
color_blue=""
color_red=""
color_yellow=""
color_green=""
color_bold=""
color_reset=""
staged_repo_files=0

usage() {
  cat <<USAGE
Usage: $(basename "$0") [--help] [--rancher-only]

Upgrades Rancher first, then the current k3s server node, then cert-manager.

  --rancher-only   Upgrade only the Rancher Helm release. Refuses to run when
                   the installed k3s minor is outside the selected Rancher
                   version's supported k3s range (run without this option).

After each component is upgraded and verified, the repository file that pins
its version (ansible/group_vars/all.yml, or the cert-manager fleet.yaml) is
staged with git add. Before exiting, the script asks whether to run
make codex-commit in the homelab repository.

The script discovers current compatible versions from SUSE/GitHub, prints the
selected versions and source URLs, attempts a pre-upgrade k3s snapshot, and then
waits for confirmation before making upgrade changes. Successful public release
metadata is cached under /var/cache/homelab/upgrade-k3s-rancher. If a live
request fails, cached data is used only after an explicit prompt showing its age.

Environment:
  ROLLOUT_TIMEOUT   kubectl rollout timeout for Rancher deployment (default: 10m)
  PENDING_RANCHER_UPGRADE_MAX_AGE_SECONDS
                   Treat pending-upgrade as stale after this many seconds
                   (default: 1800 / 30 minutes).
USAGE
}

log() {
  local message="$1"
  local ts
  ts="$(date +"%Y-%m-%d %H:%M:%S")"
  printf '[%s] %b\n' "$ts" "$message"
  printf '[%s] %b\n' "$ts" "$message" | sed -E $'s/\x1B\\[[0-9;]*[[:alpha:]]//g' >>"$log_file"
}

init_colors() {
  if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    color_blue=$'\033[1;94m'
    color_red=$'\033[31m'
    color_yellow=$'\033[33m'
    color_green=$'\033[32m'
    color_bold=$'\033[1m'
    color_reset=$'\033[0m'
  fi
}

color_text() {
  local color="$1"
  local text="$2"
  printf '%s%s%s' "$color" "$text" "$color_reset"
}

component_text() {
  color_text "$color_blue" "$1"
}

version_text() {
  color_text "$color_bold" "$1"
}

version_transition_text() {
  local current="$1"
  local target="$2"

  if versions_match "$current" "$target"; then
    printf '%s (%s)' "$(version_text "$target")" "$(success_text "already up to date")"
    return 0
  fi

  if [ -n "$current" ] && ! target_version_is_newer "$current" "$target"; then
    printf '%s -> %s (%s)' "$(version_text "$current")" "$(version_text "$target")" "$(warning_text "target is not newer; will skip")"
    return 0
  fi

  warning_text "$(printf '%s -> %s' "$(version_text "${current:-not detected}")" "$(version_text "$target")")"
}

success_text() {
  color_text "$color_green" "$1"
}

warning_text() {
  color_text "$color_yellow" "$1"
}

error_text() {
  color_text "$color_red" "$1"
}

log_success() {
  log "$(success_text "$1")"
}

log_warning() {
  log "$(warning_text "$1")"
}

log_error() {
  log "$(error_text "$1")"
}

run_cmd() {
  log "+ $*"
  "$@" 2>&1 | tee -a "$log_file"
}

require_root() {
  if [ "${EUID:-$(id -u)}" -ne 0 ]; then
    printf '%b\n' "$(error_text "This script must be run as root.")" >&2
    exit 2
  fi
}

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    printf '%b\n' "$(error_text "Missing required command: $cmd")" >&2
    return 1
  fi
}

require_dependencies() {
  local missing=0
  local cmd
  for cmd in awk base64 chmod chown cp curl date git grep helm hostname kubectl make mkdir mktemp mv rm sed sha256sum sleep sort stat sudo systemctl tail tee tr; do
    if ! require_cmd "$cmd"; then
      missing=1
    fi
  done

  if [ "$missing" -ne 0 ]; then
    exit 2
  fi
  if [ -f "$cert_manager_fleet_file" ]; then
    require_cmd fleet
    require_cmd jq
    require_cmd cmp
  fi
}

# The script runs as root, but the repository belongs to a normal user. Run git
# and make as that user, so the index stays theirs and codex-commit uses their
# home directory, credentials and git config.
as_repo_owner() {
  local owner
  owner="$(stat -c %U "$homelab_root")"
  if [ "${EUID:-$(id -u)}" -eq 0 ] && [ "$owner" != "root" ]; then
    sudo -u "$owner" -H -- "$@"
  else
    "$@"
  fi
}

# Stage a version file after its component was upgraded and verified.
stage_repo_file() {
  local file="$1"
  local relative_file="${file#"$homelab_root/"}"

  if [ ! -f "$file" ]; then
    return 0
  fi
  if as_repo_owner git -C "$homelab_root" diff --quiet -- "$relative_file" 2>>"$log_file"; then
    return 0
  fi
  if ! as_repo_owner git -C "$homelab_root" add -- "$relative_file" >>"$log_file" 2>&1; then
    log_warning "Could not stage $relative_file with git add; stage it by hand."
    return 0
  fi
  staged_repo_files=$((staged_repo_files + 1))
  log_success "Staged $relative_file."
}

# Runs on every exit, so files staged for components that succeeded can still
# be committed when a later step fails.
ask_run_codex_commit() {
  local exit_status=$?
  trap - EXIT

  if [ "$staged_repo_files" -gt 0 ]; then
    if [ "$exit_status" -ne 0 ]; then
      log_warning "The run did not finish (exit status $exit_status). Only files of components that were upgraded and verified are staged."
    fi
    if confirm "Upgraded version files are staged. Run make -C $homelab_root codex-commit now?"; then
      log "+ make -C $homelab_root codex-commit"
      if as_repo_owner make -C "$homelab_root" codex-commit; then
        log_success "make codex-commit completed successfully."
      else
        log_error "make codex-commit failed; the staged files were left in place."
      fi
    else
      log "User chose not to run make codex-commit; the staged files were left in place."
    fi
  fi

  exit "$exit_status"
}

confirm() {
  local prompt="$1"
  local answer
  if command -v dialog >/dev/null 2>&1 && [ -r /dev/tty ] && [ -w /dev/tty ]; then
    dialog --clear --begin 2 10 --title "Confirmation required" --defaultno \
      --yes-label "Yes" --no-label "No" --yesno "$prompt" 14 100 \
      </dev/tty >/dev/tty 2>&1
    return $?
  fi

  # Keep a safe interactive fallback for hosts without ncurses dialog.
  if [ -r /dev/tty ] && [ -w /dev/tty ]; then
    printf '%s [y/N] ' "$prompt" >/dev/tty
    read -r answer </dev/tty || return 1
  else
    log_warning "No interactive dialog or terminal is available; defaulting to No." >&2
    return 1
  fi
  case "$answer" in
    y|Y|yes|YES)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

metadata_cache_key() {
  printf '%s' "$1" | sha256sum | awk '{print $1}'
}

cache_metadata_response() {
  local url="$1" source_file="$2" key data_file stamp_file data_tmp stamp_tmp
  key="$(metadata_cache_key "$url")" || return 1
  data_file="$metadata_cache_dir/$key.data"
  stamp_file="$metadata_cache_dir/$key.timestamp"
  data_tmp="$(mktemp "$metadata_cache_dir/.metadata.XXXXXX")" || return 1
  stamp_tmp="${data_tmp}.timestamp"
  if ! cp -- "$source_file" "$data_tmp"; then
    rm -f "$data_tmp"
    return 1
  fi
  chmod 600 "$data_tmp"
  if ! printf '%s\n' "$(date +%s)" >"$stamp_tmp"; then
    rm -f "$data_tmp" "$stamp_tmp"
    return 1
  fi
  chmod 600 "$stamp_tmp"
  mv -f -- "$data_tmp" "$data_file" && mv -f -- "$stamp_tmp" "$stamp_file"
}

use_cached_metadata_if_approved() {
  local url="$1" key data_file stamp_file cached_at now age age_text cached_date
  key="$(metadata_cache_key "$url")" || return 1
  data_file="$metadata_cache_dir/$key.data"
  stamp_file="$metadata_cache_dir/$key.timestamp"
  if [ ! -s "$data_file" ] || [ ! -s "$stamp_file" ]; then
    return 1
  fi
  cached_at="$(cat "$stamp_file" 2>/dev/null || true)"
  now="$(date +%s)"
  if [[ "$cached_at" =~ ^[0-9]+$ ]]; then
    age=$((now - cached_at))
    [ "$age" -ge 0 ] || age=0
    age_text="$((age / 86400))d $(((age % 86400) / 3600))h ago"
    cached_date="$(date -d "@$cached_at" '+%Y-%m-%d %H:%M:%S %Z' 2>/dev/null || printf 'unknown time')"
  else
    age_text="age unknown"
    cached_date="unknown time"
  fi
  if [ ! -r /dev/tty ] || [ ! -w /dev/tty ]; then
    log_warning "Cached release metadata exists ($cached_date; $age_text), but no interactive terminal is available to approve using it." >&2
    return 1
  fi
  if ! confirm "Live release data is unavailable for $url. Use cached data from $cached_date ($age_text)? It may omit newer releases."; then
    log_warning "User declined cached release metadata for $url." >&2
    return 1
  fi
  log_warning "Using user-approved cached release metadata for $url from $cached_date ($age_text)." >&2
  cat -- "$data_file"
}

fetch_retry_approved() {
  local url="$1"
  if [ ! -r /dev/tty ] || [ ! -w /dev/tty ]; then
    log_warning "Network fetch failed for $url; no interactive terminal is available for an approved retry." >&2
    return 1
  fi
  if confirm "Fetching release metadata failed for $url. Curl exhausted its configured retries. Retry this read-only request once? No upgrades have started."; then
    return 0
  fi
  log_warning "User declined another fetch attempt for $url; no upgrades have started." >&2
  return 1
}

fetch_url() {
  local url="$1" response_file
  response_file="$(mktemp "${TMPDIR:-/tmp}/upgrade-metadata.XXXXXX")" || return 1
  if curl -fsSL -o "$response_file" "$url" 2>>"$log_file"; then
    if ! cache_metadata_response "$url" "$response_file"; then
      log_warning "Could not update metadata cache for $url." >&2
    fi
    cat -- "$response_file"
    rm -f "$response_file"
    return 0
  fi
  log_warning "Release metadata fetch failed for $url; curl details are in $log_file." >&2
  if use_cached_metadata_if_approved "$url"; then
    rm -f "$response_file"
    return 0
  fi
  if fetch_retry_approved "$url"; then
    if curl -fsSL -o "$response_file" "$url" 2>>"$log_file"; then
      if ! cache_metadata_response "$url" "$response_file"; then
        log_warning "Could not update metadata cache for $url." >&2
      fi
      cat -- "$response_file"
      rm -f "$response_file"
      return 0
    fi
  fi
  rm -f "$response_file"
  log_error "Release metadata is still unavailable from $url. No upgrade changes have been made; rerun after connectivity recovers." >&2
  return 1
}

fetch_effective_url() {
  local url="$1" response_file
  response_file="$(mktemp "${TMPDIR:-/tmp}/upgrade-metadata.XXXXXX")" || return 1
  if curl -fsSL -o /dev/null -w '%{url_effective}' "$url" >"$response_file" 2>>"$log_file"; then
    if ! cache_metadata_response "$url" "$response_file"; then
      log_warning "Could not update metadata cache for $url." >&2
    fi
    cat -- "$response_file"
    rm -f "$response_file"
    return 0
  fi
  log_warning "Release metadata URL lookup failed for $url; curl details are in $log_file." >&2
  if use_cached_metadata_if_approved "$url"; then
    rm -f "$response_file"
    return 0
  fi
  if fetch_retry_approved "$url"; then
    if curl -fsSL -o /dev/null -w '%{url_effective}' "$url" >"$response_file" 2>>"$log_file"; then
      if ! cache_metadata_response "$url" "$response_file"; then
        log_warning "Could not update metadata cache for $url." >&2
      fi
      cat -- "$response_file"
      rm -f "$response_file"
      return 0
    fi
  fi
  rm -f "$response_file"
  log_error "Release metadata is still unavailable from $url. No upgrade changes have been made; rerun after connectivity recovers." >&2
  return 1
}

strip_html_tags() {
  sed -E 's/<[^>]*>/ /g; s/&nbsp;/ /g; s/&#160;/ /g; s/&amp;/\&/g'
}

highest_semver_tag() {
  sort -t. -k1,1n -k2,2n -k3,3n -k4,4n | tail -n 1
}

parse_latest_rancher_url() {
  local effective_url="$1"
  local index_html="$2"
  local candidate

  candidate="$(
    {
      printf '%s\n' "$effective_url"
      printf '%s\n' "$index_html"
    } |
      grep -Eo 'https://www\.suse\.com/suse-rancher/support-matrix/all-supported-versions/rancher-v[0-9]+-[0-9]+-[0-9]+/?|/suse-rancher/support-matrix/all-supported-versions/rancher-v[0-9]+-[0-9]+-[0-9]+/?' |
      sed -E 's#^/suse-rancher#https://www.suse.com/suse-rancher#; s#/$##' |
      sed -E 's#.*rancher-v([0-9]+)-([0-9]+)-([0-9]+).*#\1.\2.\3 &#' |
      sort -u |
      sort -t. -k1,1n -k2,2n -k3,3n |
      tail -n 1 |
      awk '{print $2}'
  )"

  if [ -z "$candidate" ]; then
    return 1
  fi

  printf '%s/\n' "$candidate"
}

parse_rancher_version_from_url() {
  local url="$1"
  printf '%s\n' "$url" | sed -E 's#.*rancher-v([0-9]+)-([0-9]+)-([0-9]+)/?.*#v\1.\2.\3#'
}

rancher_matrix_url_for_version() {
  local version="$1"
  local version_path
  version_path="$(printf '%s\n' "${version#v}" | tr . -)"
  printf '%srancher-v%s/\n' "$rancher_matrix_index_url" "$version_path"
}

select_rancher_chart_version() {
  local index_yaml="$1"
  local max_version="$2"
  local selected

  selected="$(
    printf '%s\n' "$index_yaml" |
      sed -nE 's/^[[:space:]]+version:[[:space:]]+([0-9]+\.[0-9]+\.[0-9]+)$/\1/p' |
      sort -Vu |
      awk -v max="${max_version#v}" '
        BEGIN { split(max, max_parts, ".") }
        {
          split($0, parts, ".")
          if (parts[1] < max_parts[1] ||
              (parts[1] == max_parts[1] && parts[2] < max_parts[2]) ||
              (parts[1] == max_parts[1] && parts[2] == max_parts[2] && parts[3] <= max_parts[3])) {
            selected = $0
          }
        }
        END { if (selected != "") print "v" selected }
      '
  )"

  if [ -z "$selected" ]; then
    return 1
  fi

  printf '%s\n' "$selected"
}

parse_supported_k3s_minor() {
  local matrix_html="$1"
  local text
  text="$(printf '%s\n' "$matrix_html" | tr '\n' ' ' | strip_html_tags | tr -s ' ')"

  printf '%s\n' "$text" |
    grep -Eoi 'k3s[[:space:]]+v[0-9]+\.[0-9]+[[:space:]]+v[0-9]+\.[0-9]+' |
    head -n 1 |
    awk '{print $3}'
}

# Print "<min> <max>" k3s minors from a Rancher support-matrix page row such as
# "k3s v1.34 v1.36".
parse_supported_k3s_minor_range() {
  local matrix_html="$1"
  local text
  text="$(printf '%s\n' "$matrix_html" | tr '\n' ' ' | strip_html_tags | tr -s ' ')"

  printf '%s\n' "$text" |
    grep -Eoi 'k3s[[:space:]]+v[0-9]+\.[0-9]+[[:space:]]+v[0-9]+\.[0-9]+' |
    head -n 1 |
    awk '{print $2, $3}'
}

k3s_minor_in_range() {
  local current="$1"
  local min="$2"
  local max="$3"

  [ "$(printf '%s\n%s\n' "$min" "$current" | sort -V | head -n 1)" = "$min" ] &&
    [ "$(printf '%s\n%s\n' "$current" "$max" | sort -V | tail -n 1)" = "$max" ]
}

json_release_tags() {
  grep -Eo '"tag_name"[[:space:]]*:[[:space:]]*"[^"]+"' |
    sed -E 's/.*"tag_name"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/'
}

select_k3s_release() {
  local releases_json="$1"
  local supported_minor="$2"
  local minor_without_v="${supported_minor#v}"
  local selected

  selected="$(
    printf '%s\n' "$releases_json" |
      json_release_tags |
      grep -E "^v${minor_without_v//./\\.}\\.[0-9]+\\+k3s[0-9]+$" |
      sed -E 's#^v([0-9]+\.[0-9]+\.[0-9]+)\+k3s([0-9]+)$#\1.\2 v\1+k3s\2#' |
      highest_semver_tag |
      awk '{print $2}'
  )"

  if [ -z "$selected" ]; then
    return 1
  fi

  printf '%s\n' "$selected"
}

select_cert_manager_release() {
  local releases_json="$1"
  local selected

  selected="$(
    printf '%s\n' "$releases_json" |
      json_release_tags |
      grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' |
      sed -E 's#^v([0-9]+\.[0-9]+\.[0-9]+)$#\1 v\1#' |
      highest_semver_tag |
      awk '{print $2}'
  )"

  if [ -z "$selected" ]; then
    return 1
  fi

  printf '%s\n' "$selected"
}

url_encode_k3s_version() {
  local version="$1"
  printf '%s\n' "${version/+/%2B}"
}

show_current_status() {
  log "Current status before upgrade:"

  if command -v k3s >/dev/null 2>&1; then
    run_cmd k3s --version || true
  else
    log_warning "$(component_text "k3s") command not found; this may be a fresh install or PATH issue."
  fi

  if [ -f "$kubeconfig_path" ]; then
    run_cmd kubectl --kubeconfig "$kubeconfig_path" get nodes || true
    run_cmd kubectl --kubeconfig "$kubeconfig_path" get pods --namespace cert-manager || true
    run_cmd kubectl --kubeconfig "$kubeconfig_path" get pods --namespace cattle-system || true
    run_cmd helm --kubeconfig "$kubeconfig_path" list --all --namespace cert-manager || true
    run_cmd helm --kubeconfig "$kubeconfig_path" list --all --namespace cattle-system || true
  else
    log_warning "Kubeconfig does not exist yet: $kubeconfig_path"
  fi
}

save_snapshot_or_confirm() {
  if ! command -v k3s >/dev/null 2>&1; then
    log_warning "Skipping $(component_text "k3s") snapshot because k3s command is not available."
    confirm "Continue without a pre-upgrade k3s snapshot?" || exit 1
    return 0
  fi

  if run_cmd k3s etcd-snapshot save --name "pre-rancher-upgrade-${timestamp}"; then
    log_success "Pre-upgrade $(component_text "k3s") snapshot completed."
    return 0
  fi

  log_warning "Pre-upgrade $(component_text "k3s") snapshot failed or is unavailable for this datastore."
  confirm "Continue without a successful pre-upgrade k3s snapshot?" || exit 1
}

wait_for_kubectl() {
  local attempt
  for attempt in $(seq 1 60); do
    if kubectl --kubeconfig "$kubeconfig_path" get nodes >/dev/null 2>&1; then
      run_cmd kubectl --kubeconfig "$kubeconfig_path" get nodes
      return 0
    fi
    log "Waiting for Kubernetes API to become reachable ($attempt/60)..."
    sleep 5
  done

  log_error "Kubernetes API did not become reachable in time."
  return 1
}

wait_for_cert_manager_startup_check() {
  local job_name
  if ! job_name="$(kubectl --kubeconfig "$kubeconfig_path" --namespace cert-manager \
    get job cert-manager-startupapicheck --ignore-not-found -o name 2>>"$log_file")"; then
    log_error "Could not inspect the cert-manager startup API check Job."
    return 1
  fi
  if [ -z "$job_name" ]; then
    log "No startup API check Job remains (post-install hook); verifying the live API instead."
    return 0
  fi
  log "Waiting for the cert-manager startup API check to complete."
  if run_cmd kubectl --kubeconfig "$kubeconfig_path" --namespace cert-manager \
    wait --for=condition=complete "$job_name" --timeout="$rollout_timeout"; then
    return 0
  fi
  # A successful hook can be deleted between inspection and kubectl wait.
  if ! job_name="$(kubectl --kubeconfig "$kubeconfig_path" --namespace cert-manager \
    get job cert-manager-startupapicheck --ignore-not-found -o name 2>>"$log_file")"; then
    log_error "Could not recheck the cert-manager startup API check Job."
    return 1
  fi
  if [ -n "$job_name" ]; then
    log_error "The cert-manager startup API check did not complete."
    return 1
  fi
  log "Startup API check Job was removed; verifying the live API instead."
}

verify_cert_manager_api() {
  local attempt
  log "Checking cert-manager API/webhook with a server-side dry run; no Certificate is persisted."
  for attempt in $(seq 1 12); do
    if run_cmd kubectl --kubeconfig "$kubeconfig_path" --namespace cert-manager \
      --request-timeout=15s create --dry-run=server -f - <<'CERT_MANAGER_API_CHECK'
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  generateName: cert-manager-api-healthcheck-
spec:
  secretName: cert-manager-api-healthcheck
  dnsNames:
    - cert-manager-api-healthcheck.invalid
  issuerRef:
    name: cert-manager-api-healthcheck
    kind: Issuer
CERT_MANAGER_API_CHECK
    then
      log_success "cert-manager API/webhook dry-run check passed."
      return 0
    fi
    if [ "$attempt" -lt 12 ]; then
      log "Waiting for cert-manager API/webhook readiness ($attempt/12); retrying in 5s."
      sleep 5
    fi
  done
  log_error "cert-manager API/webhook dry-run check failed. See $log_file."
  return 1
}

verify_cert_manager_health() {
  local deployment

  log "Checking $(component_text "cert-manager") health after upgrade."

  for deployment in cert-manager cert-manager-cainjector cert-manager-webhook; do
    run_cmd kubectl --kubeconfig "$kubeconfig_path" \
      --namespace cert-manager \
      rollout status "deployment/$deployment" \
      --timeout="$rollout_timeout"
  done

  wait_for_cert_manager_startup_check
  verify_cert_manager_api

  run_cmd kubectl --kubeconfig "$kubeconfig_path" get pods --namespace cert-manager -o wide
  run_cmd kubectl --kubeconfig "$kubeconfig_path" get issuers,clusterissuers,certificaterequests,certificates --all-namespaces
  log_success "$(component_text "cert-manager") health checks passed."
  # Every caller syncs the Fleet version first, so it is verified at this point.
  stage_repo_file "$cert_manager_fleet_file"
}

normalize_leading_v() {
  local version="$1"
  printf 'v%s\n' "${version#v}"
}

require_equal_version() {
  local component="$1"
  local expected="$2"
  local actual="$3"

  if [ "$(normalize_leading_v "$actual")" != "$(normalize_leading_v "$expected")" ]; then
    log_error "$(component_text "$component") version check failed: expected $(version_text "$expected"), got $(version_text "${actual:-unknown}")."
    return 1
  fi

  log_success "$(component_text "$component") version check passed: $(version_text "$actual")"
}

versions_match() {
  local actual="$1"
  local expected="$2"

  [ -n "$actual" ] && [ "$(normalize_leading_v "$actual")" = "$(normalize_leading_v "$expected")" ]
}

target_version_is_newer() {
  local current="$1"
  local target="$2"
  local newest

  if [ -z "$current" ]; then
    return 0
  fi

  if versions_match "$current" "$target"; then
    return 1
  fi

  newest="$(printf '%s\n%s\n' "$(normalize_leading_v "$current")" "$(normalize_leading_v "$target")" | sort -V | tail -n 1)"
  [ "$newest" = "$(normalize_leading_v "$target")" ]
}

helm_release_field() {
  local release="$1"
  local namespace="$2"
  local field="$3"

  helm --kubeconfig "$kubeconfig_path" list \
    --namespace "$namespace" \
    --all \
    --filter "^${release}$" \
    --output json |
    sed -nE "s/.*\"$field\"[[:space:]]*:[[:space:]]*\"([^\"]+)\".*/\1/p" |
    head -n 1
}

helm_release_status() {
  local release="$1"
  local namespace="$2"

  helm --kubeconfig "$kubeconfig_path" list \
    --namespace "$namespace" \
    --all \
    --filter "^${release}$" \
    --output json |
    sed -nE 's/.*"status"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/p' |
    head -n 1
}

recover_pending_rancher_upgrade() {
  local status="$1"
  local previous_deployed_revision
  local pending_upgrade_started
  local pending_upgrade_epoch
  local pending_upgrade_age

  if [ "$status" != "pending-upgrade" ]; then
    return 0
  fi

  pending_upgrade_started="$(helm --kubeconfig "$kubeconfig_path" history rancher \
    --namespace cattle-system --max 20 2>/dev/null |
    awk 'NR > 1 && $7 == "pending-upgrade" { print $2, $3, $4, $5, $6; exit }')"
  pending_upgrade_epoch="$(date -d "$pending_upgrade_started" +%s 2>/dev/null || true)"
  if [ -z "$pending_upgrade_epoch" ]; then
    log_error "Unable to determine when Rancher entered pending-upgrade. Refusing automatic recovery."
    return 1
  fi
  pending_upgrade_age=$(( $(date +%s) - pending_upgrade_epoch ))
  if [ "$pending_upgrade_age" -lt 0 ]; then
    pending_upgrade_age=0
  fi
  if [ "$pending_upgrade_age" -lt "$pending_rancher_upgrade_max_age_seconds" ]; then
    log_warning "Rancher pending-upgrade started ${pending_upgrade_age}s ago; waiting until it is older than ${pending_rancher_upgrade_max_age_seconds}s before offering rollback."
    return 1
  fi

  previous_deployed_revision="$(helm --kubeconfig "$kubeconfig_path" history rancher \
    --namespace cattle-system --max 20 2>/dev/null |
    awk 'NR > 1 && $7 == "deployed" { revision = $1 } END { print revision }')"
  if [ -z "$previous_deployed_revision" ]; then
    log_error "Rancher is pending-upgrade, but no previous deployed Helm revision was found."
    return 1
  fi

  log_warning "Rancher Helm release is stuck in pending-upgrade. The latest deployed revision is $previous_deployed_revision."
  if ! confirm "Roll Rancher back to deployed revision $previous_deployed_revision and wait for readiness?"; then
    log_error "Pending Rancher upgrade was not repaired; aborting before upgrade changes."
    return 1
  fi

  log "Rolling Rancher back to Helm revision $previous_deployed_revision."
  if ! run_cmd helm --kubeconfig "$kubeconfig_path" rollback rancher "$previous_deployed_revision" \
    --namespace cattle-system \
    --wait \
    --timeout "$rollout_timeout"; then
    log_error "Rancher rollback to revision $previous_deployed_revision failed."
    return 1
  fi

  status="$(helm_release_status rancher cattle-system || true)"
  if [ "$status" != "deployed" ]; then
    log_error "Rancher rollback completed but Helm status is ${status:-unknown}, not deployed."
    return 1
  fi

  log_success "Rancher pending-upgrade state repaired; Helm status is deployed."
  return 0
}

update_ansible_rancher_chart_version() {
  local chart_version="$1"
  local file="$ansible_group_vars_file"
  local temp_file
  local matching_lines

  if [ ! -f "$file" ]; then
    log_error "Ansible group vars file not found: $file"
    return 1
  fi

  matching_lines="$(grep -Ec '^[[:space:]]*rancher_chart_version:' "$file" || true)"
  if [ "$matching_lines" -ne 1 ]; then
    log_error "Expected exactly one rancher_chart_version entry in $file; found $matching_lines."
    return 1
  fi

  temp_file="$(mktemp "${file}.XXXXXX")"
  if ! sed -E "s#^([[:space:]]*rancher_chart_version:[[:space:]]*).*\$#\1\"${chart_version#v}\"#" "$file" >"$temp_file"; then
    rm -f "$temp_file"
    log_error "Unable to update Rancher chart version in $file"
    return 1
  fi

  chmod --reference="$file" "$temp_file"
  chown --reference="$file" "$temp_file"

  if ! mv "$temp_file" "$file"; then
    rm -f "$temp_file"
    log_error "Unable to replace Ansible group vars file: $file"
    return 1
  fi

  log_success "Updated Ansible rancher_chart_version to $(version_text "${chart_version#v}") in $file."
}

update_ansible_k3s_version() {
  local k3s_version="$1"
  local file="$ansible_group_vars_file"
  local temp_file
  local matching_lines

  if [ ! -f "$file" ]; then
    log_error "Ansible group vars file not found: $file"
    return 1
  fi

  matching_lines="$(grep -Ec '^[[:space:]]*k3s_version:' "$file" || true)"
  if [ "$matching_lines" -ne 1 ]; then
    log_error "Expected exactly one k3s_version entry in $file; found $matching_lines."
    return 1
  fi

  temp_file="$(mktemp "${file}.XXXXXX")"
  if ! sed -E "s#^([[:space:]]*k3s_version:[[:space:]]*).*\$#\1${k3s_version}#" "$file" >"$temp_file"; then
    rm -f "$temp_file"
    log_error "Unable to update K3s version in $file"
    return 1
  fi

  chmod --reference="$file" "$temp_file"
  chown --reference="$file" "$temp_file"

  if ! mv "$temp_file" "$file"; then
    rm -f "$temp_file"
    log_error "Unable to replace Ansible group vars file: $file"
    return 1
  fi

  log_success "Updated Ansible k3s_version to $(version_text "$k3s_version") in $file."
}

sync_cert_manager_fleet_version() {
  local version="$1" work current labels_entry attempt deployments
  local root="${script_dir%/*}" relative_dir
  local labels=()
  if [ ! -f "$cert_manager_fleet_file" ]; then
    log "No cert-manager Fleet configuration found; retaining standalone Helm management."
    return 0
  fi
  if [[ ! "$version" =~ ^v?[0-9]+\.[0-9]+\.[0-9]+([+-][0-9A-Za-z.-]+)?$ ]]; then
    log_error "Invalid cert-manager version for Fleet synchronization: $version"
    return 1
  fi
  version="v${version#v}"
  if [ "$(grep -Ec '^  version:' "$cert_manager_fleet_file" || true)" -ne 1 ]; then
    log_error "Expected exactly one Helm version in $cert_manager_fleet_file."
    return 1
  fi
  work="$(mktemp -d)" || return 1
  if ! sed -E "s#^(  version:).*#\1 $version#" "$cert_manager_fleet_file" >"$work/fleet.yaml"; then
    rm -rf "$work"
    return 1
  fi
  if ! cmp -s "$work/fleet.yaml" "$cert_manager_fleet_file"; then
    # Preserve the repository file's ownership when this script runs as root.
    if ! cat "$work/fleet.yaml" >"$cert_manager_fleet_file"; then
      log_error "Could not update $cert_manager_fleet_file."
      rm -rf "$work"
      return 1
    fi
    log_success "Updated cert-manager Fleet version to $version in $cert_manager_fleet_file."
  fi
  # Fleet prunes by its CLI bundle-name scope. Never let that scope include
  # another bundle, and preserve the existing GitRepo labels and targeting.
  if ! current="$(kubectl --kubeconfig "$kubeconfig_path" --namespace fleet-local \
    get bundles -l fleet.cattle.io/repo-name=cert-manager -o json)"; then
    rm -rf "$work"
    return 1
  fi
  if ! printf '%s' "$current" | jq -e 'all(.items[]; .metadata.name == "cert-manager")' >/dev/null; then
    log_error "Refusing Fleet apply: cert-manager prune scope contains other bundles."
    rm -rf "$work"
    return 1
  fi
  if ! current="$(kubectl --kubeconfig "$kubeconfig_path" --namespace fleet-local \
    get bundle cert-manager --ignore-not-found -o json)"; then
    rm -rf "$work"
    return 1
  fi
  if [ -n "$current" ]; then
    while IFS= read -r labels_entry; do labels+=(--label "$labels_entry"); done < <(
      printf '%s' "$current" | jq -r '.metadata.labels // {} | to_entries[] | select(.key != "fleet.cattle.io/commit") | "\(.key)=\(.value)"'
    )
    printf '%s' "$current" | jq '{targets:.spec.targets,targetRestrictions:(.spec.targetRestrictions // [])}' >"$work/targets.json"
  else
    labels=(--label fleet.cattle.io/repo-name=rancher-cluster)
    printf '%s\n' '{"targets":[{"name":"default","clusterGroup":"default"}]}' >"$work/targets.json"
  fi
  relative_dir="${cert_manager_fleet_file#"$root/"}"
  relative_dir="${relative_dir%/*}"
  log "Applying cert-manager $version through Fleet, preserving GitRepo ownership and targets."
  if ! (cd "$root" && run_cmd fleet apply --kubeconfig "$kubeconfig_path" \
    --namespace fleet-local "${labels[@]}" --targets-file "$work/targets.json" \
    cert-manager "$relative_dir"); then
    log_error "cert-manager Fleet apply failed; repository version remains staged at $version."
    rm -rf "$work"
    return 1
  fi
  rm -rf "$work"
  log "Waiting for Fleet cert-manager $version to be applied and Ready."
  for attempt in $(seq 1 120); do
    if deployments="$(kubectl --kubeconfig "$kubeconfig_path" --request-timeout=15s \
      get bundledeployments --all-namespaces -o json 2>>"$log_file")" &&
      printf '%s' "$deployments" | jq -e --arg version "${version#v}" '
        [.items[] | select(.metadata.name == "cert-manager")] |
        length > 0 and all(.[];
          (.spec.options.helm.version | ltrimstr("v")) == $version and
          .status.appliedDeploymentID == .spec.deploymentID and
          any(.status.conditions[]?; .type == "Ready" and .status == "True"))
      ' >/dev/null; then
      log_success "Fleet cert-manager $version is applied and Ready."
      return 0
    fi
    sleep 5
  done
  log_error "Fleet cert-manager $version did not become Ready; inspect BundleDeployment status."
  return 1
}

installed_k3s_version() {
  if ! command -v k3s >/dev/null 2>&1; then
    return 1
  fi

  k3s --version | awk 'NR == 1 {print $3}'
}

skip_if_already_current() {
  local expected_k3s_version="$1"
  local expected_cert_manager_version="$2"
  local expected_rancher_version="$3"
  local actual_k3s_version="${4:-}"
  local actual_cert_manager_version="${5:-}"
  local actual_rancher_version="${6:-}"

  log "Checking whether installed versions already match selected upgrade targets."

  if [ "$#" -lt 6 ]; then
    actual_k3s_version="$(installed_k3s_version || true)"
    actual_cert_manager_version="$(helm_release_field cert-manager cert-manager app_version || true)"
    actual_rancher_version="$(helm_release_field rancher cattle-system app_version || true)"
  fi

  log "Installed $(component_text "k3s") version: $(version_text "${actual_k3s_version:-not detected}"); target: $(version_text "$expected_k3s_version")"
  log "Installed $(component_text "cert-manager") app_version: $(version_text "${actual_cert_manager_version:-not detected}"); target: $(version_text "$expected_cert_manager_version")"
  log "Installed $(component_text "Rancher") app_version: $(version_text "${actual_rancher_version:-not detected}"); target: $(version_text "$expected_rancher_version")"

  if versions_match "$actual_k3s_version" "$expected_k3s_version" &&
     versions_match "$actual_cert_manager_version" "$expected_cert_manager_version" &&
     versions_match "$actual_rancher_version" "$expected_rancher_version"; then
    sync_cert_manager_fleet_version "$actual_cert_manager_version"
    verify_cert_manager_health
    log_success "All selected upgrade targets are already installed. No upgrade needed."
    exit 0
  fi
}

upgrade_rancher_release() {
  local rancher_version="$1"
  local rancher_chart_version="$2"
  local hostname_fqdn="$3"
  local current_rancher_version="$4"
  local setup_password

  log "Upgrading $(component_text "Rancher") from $(version_text "${current_rancher_version:-not detected}") to $(version_text "$rancher_version")."
  run_cmd helm --kubeconfig "$kubeconfig_path" upgrade --install rancher rancher-stable/rancher \
    --namespace cattle-system \
    --create-namespace \
    --version "${rancher_version#v}" \
    --set "hostname=$hostname_fqdn" \
    --set bootstrapPassword=admin \
    --set ingress.tls.source=letsEncrypt \
    --set letsEncrypt.email=dobrowolski.nowakowski@gmail.com

  run_cmd kubectl --kubeconfig "$kubeconfig_path" -n cattle-system rollout status deploy/rancher --timeout="$rollout_timeout"
  run_cmd kubectl --kubeconfig "$kubeconfig_path" -n cattle-system get deploy rancher

  update_ansible_rancher_chart_version "$rancher_chart_version"
  stage_repo_file "$ansible_group_vars_file"

  setup_password="$(kubectl --kubeconfig "$kubeconfig_path" get secret --namespace cattle-system bootstrap-secret -o go-template='{{.data.bootstrapPassword|base64decode}}')"
  log_success "$(component_text "Rancher") setup URL: https://$hostname_fqdn/dashboard/?setup=$setup_password"

  run_cmd "$script_dir/rancher_add_cluster_repos.sh"
}

# Upgrade only the Rancher release; k3s and cert-manager are left untouched.
run_rancher_only_upgrade() {
  local rancher_version="$1"
  local rancher_chart_version="$2"
  local rancher_matrix_url="$3"
  local rancher_matrix_html="$4"
  local hostname_fqdn
  local k3s_min=""
  local k3s_max=""
  local current_k3s_version
  local current_k3s_minor
  local current_rancher_version
  local current_rancher_status
  local actual_rancher_version

  hostname_fqdn="$(hostname).tailscale.bnowakowski.pl"
  read -r k3s_min k3s_max <<<"$(parse_supported_k3s_minor_range "$rancher_matrix_html")" || true
  current_k3s_version="$(installed_k3s_version || true)"
  current_k3s_minor="$(printf '%s\n' "$current_k3s_version" | sed -nE 's/^v?([0-9]+\.[0-9]+).*/v\1/p')"

  log "Selected $(component_text "Rancher") version: $(version_text "$rancher_version")"
  log "$(component_text "Rancher") support matrix URL: $rancher_matrix_url"
  log "Supported $(component_text "k3s") minors: $(version_text "${k3s_min:-unknown}") - $(version_text "${k3s_max:-unknown}"); installed $(component_text "k3s"): $(version_text "${current_k3s_version:-not detected}")"

  if [ -z "$k3s_min" ] || [ -z "$k3s_max" ] || [ -z "$current_k3s_minor" ]; then
    log_error "Unable to confirm that installed $(component_text "k3s") is supported by $(component_text "Rancher") $(version_text "$rancher_version"). Refusing Rancher-only upgrade."
    return 1
  fi
  if ! k3s_minor_in_range "$current_k3s_minor" "$k3s_min" "$k3s_max"; then
    log_error "$(component_text "Rancher") $(version_text "$rancher_version") supports $(component_text "k3s") $k3s_min-$k3s_max, but $(version_text "$current_k3s_version") is installed. Run $(basename "$0") without --rancher-only to upgrade Rancher and k3s together."
    return 1
  fi

  current_rancher_version="$(helm_release_field rancher cattle-system app_version || true)"
  current_rancher_status="$(helm_release_status rancher cattle-system || true)"
  if [ -n "$current_rancher_status" ] && [ "$current_rancher_status" != "deployed" ]; then
    log_warning "Rancher Helm release is present at $(version_text "${current_rancher_version:-unknown}") but has status $(version_text "$current_rancher_status")."
  fi
  if ! recover_pending_rancher_upgrade "$current_rancher_status"; then
    return 1
  fi
  current_rancher_version="$(helm_release_field rancher cattle-system app_version || true)"

  if ! target_version_is_newer "$current_rancher_version" "$rancher_version"; then
    log_success "Skipping $(component_text "Rancher"); installed version is $(version_text "${current_rancher_version:-not detected}") and target $(version_text "$rancher_version") is not newer."
    return 0
  fi

  show_current_status
  save_snapshot_or_confirm

  printf '%b\n' "

About to run the Rancher-only upgrade with:
  $(component_text "Rancher"):       $(version_transition_text "$current_rancher_version" "$rancher_version")
  $(component_text "Rancher") URL:   $rancher_matrix_url
  $(component_text "k3s"):           $(version_text "$current_k3s_version") (unchanged; supported $k3s_min-$k3s_max)
  hostname:      $hostname_fqdn
  log file:      $log_file

"

  confirm "Do these versions and settings look good?" || return 1

  upgrade_rancher_release "$rancher_version" "$rancher_chart_version" "$hostname_fqdn" "$current_rancher_version"

  actual_rancher_version="$(helm_release_field rancher cattle-system app_version)"
  run_cmd helm --kubeconfig "$kubeconfig_path" list --all --namespace cattle-system --filter '^rancher$'
  require_equal_version "Rancher app_version" "$rancher_version" "$actual_rancher_version"
  log_success "Rancher-only upgrade completed."
}

verify_final_versions() {
  local expected_k3s_version="$1"
  local expected_cert_manager_version="$2"
  local expected_rancher_version="$3"
  local actual_k3s_version
  local actual_cert_manager_version
  local actual_rancher_version
  local cert_manager_chart
  local rancher_chart

  log "Verifying installed versions after upgrade."

  actual_k3s_version="$(installed_k3s_version)"
  run_cmd k3s --version
  require_equal_version "k3s" "$expected_k3s_version" "$actual_k3s_version"

  actual_cert_manager_version="$(helm_release_field cert-manager cert-manager app_version)"
  cert_manager_chart="$(helm_release_field cert-manager cert-manager chart)"
  run_cmd helm --kubeconfig "$kubeconfig_path" list --all --namespace cert-manager --filter '^cert-manager$'
  require_equal_version "cert-manager app_version" "$expected_cert_manager_version" "$actual_cert_manager_version"
  log "$(component_text "cert-manager") chart version reported by Helm: $(version_text "${cert_manager_chart:-unknown}")"

  actual_rancher_version="$(helm_release_field rancher cattle-system app_version)"
  rancher_chart="$(helm_release_field rancher cattle-system chart)"
  run_cmd helm --kubeconfig "$kubeconfig_path" list --all --namespace cattle-system --filter '^rancher$'
  require_equal_version "Rancher app_version" "$expected_rancher_version" "$actual_rancher_version"
  log "$(component_text "Rancher") chart version reported by Helm: $(version_text "${rancher_chart:-unknown}")"

  log_success "All final version checks passed."
}

ensure_helm_repo() {
  local name="$1"
  local url="$2"

  if helm repo list 2>/dev/null | awk '{print $1}' | grep -Fxq "$name"; then
    log_success "Helm repo already exists: $name"
    return 0
  fi

  run_cmd helm repo add "$name" "$url"
}

main() {
  init_colors

  local rancher_only=0

  case "${1:-}" in
    --help)
      usage
      exit 0
      ;;
    --rancher-only)
      rancher_only=1
      shift
      ;;
  esac

  if [ "$#" -gt 0 ]; then
    printf '%b\n' "$(error_text "Unknown argument: $1")" >&2
    usage >&2
    exit 2
  fi

  require_root
  mkdir -p "$log_dir"
  if mkdir -p "$metadata_cache_dir" && chmod 700 "$metadata_cache_dir"; then
    :
  else
    log_warning "Could not initialize metadata cache at $metadata_cache_dir; live lookups will still work."
  fi
  trap ask_run_codex_commit EXIT
  require_dependencies
  export KUBECONFIG="$kubeconfig_path"

  log "Run started. log_file=$log_file"
  ensure_helm_repo "rancher-stable" "https://releases.rancher.com/server-charts/stable"
  if [ "$rancher_only" -eq 0 ]; then
    ensure_helm_repo "jetstack" "https://charts.jetstack.io"
  fi
  run_cmd helm repo update

  log "Discovering latest $(component_text "Rancher"), $(component_text "k3s"), and $(component_text "cert-manager") versions."

  local rancher_effective_url
  local rancher_index_html
  local rancher_matrix_url
  local rancher_matrix_html
  local rancher_version
  local rancher_chart_index_yaml
  local rancher_chart_version
  local supported_k3s_minor
  local k3s_releases_json
  local k3s_version
  local k3s_install_version
  local cert_manager_releases_json
  local cert_manager_version
  local current_k3s_version
  local current_cert_manager_version
  local current_rancher_version
  local current_rancher_status
  local upgrade_k3s
  local upgrade_cert_manager
  local upgrade_rancher
  local final_k3s_version
  local final_cert_manager_version
  local final_rancher_version
  local hostname_fqdn

  rancher_effective_url="$(fetch_effective_url "$rancher_matrix_current_url")"
  rancher_index_html="$(fetch_url "$rancher_matrix_index_url" || true)"
  rancher_matrix_url="$(parse_latest_rancher_url "$rancher_effective_url" "$rancher_index_html")"
  rancher_version="$(parse_rancher_version_from_url "$rancher_matrix_url")"
  rancher_chart_index_yaml="$(fetch_url "$rancher_chart_index_url")"
  rancher_chart_version="$(select_rancher_chart_version "$rancher_chart_index_yaml" "$rancher_version")"
  if [ -z "$rancher_version" ] || [ -z "$rancher_chart_version" ]; then
    log_error "Unable to discover $(component_text "Rancher") version from SUSE support matrix or Rancher Helm chart index."
    exit 1
  fi

  if ! versions_match "$rancher_chart_version" "$rancher_version"; then
    log_warning "Latest SUSE support matrix is $(version_text "$rancher_version"), but rancher-stable Helm only has $(version_text "$rancher_chart_version"); selecting the newest installable chart."
    rancher_version="$rancher_chart_version"
    rancher_matrix_url="$(rancher_matrix_url_for_version "$rancher_version")"
  fi

  rancher_matrix_html="$(fetch_url "$rancher_matrix_url")"

  if [ "$rancher_only" -eq 1 ]; then
    run_rancher_only_upgrade "$rancher_version" "$rancher_chart_version" "$rancher_matrix_url" "$rancher_matrix_html"
    exit $?
  fi

  supported_k3s_minor="$(parse_supported_k3s_minor "$rancher_matrix_html")"

  if [ -z "$rancher_version" ] || [ -z "$supported_k3s_minor" ]; then
    log_error "Unable to discover $(component_text "Rancher") version or supported $(component_text "k3s") minor from SUSE support matrix."
    exit 1
  fi

  k3s_releases_json="$(fetch_url "$k3s_releases_api_url")"
  k3s_version="$(select_k3s_release "$k3s_releases_json" "$supported_k3s_minor")"
  cert_manager_releases_json="$(fetch_url "$cert_manager_releases_api_url")"
  cert_manager_version="$(select_cert_manager_release "$cert_manager_releases_json")"

  if [ -z "$k3s_version" ] || [ -z "$cert_manager_version" ]; then
    log_error "Unable to select $(component_text "k3s") or $(component_text "cert-manager") release from GitHub."
    exit 1
  fi

  k3s_install_version="$(url_encode_k3s_version "$k3s_version")"
  hostname_fqdn="$(hostname).tailscale.bnowakowski.pl"

  log "Selected $(component_text "Rancher") version: $(version_text "$rancher_version")"
  log "$(component_text "Rancher") support matrix URL: $rancher_matrix_url"
  log "Supported $(component_text "k3s") Kubernetes minor: $(version_text "$supported_k3s_minor")"
  log "Selected $(component_text "k3s") release: $(version_text "$k3s_version")"
  log "$(component_text "k3s") releases URL: https://github.com/k3s-io/k3s/releases"
  log "Selected $(component_text "cert-manager") release: $(version_text "$cert_manager_version")"
  log "$(component_text "cert-manager") releases URL: https://github.com/cert-manager/cert-manager/releases"
  log "$(component_text "Rancher") hostname: $hostname_fqdn"

  current_k3s_version="$(installed_k3s_version || true)"
  current_cert_manager_version="$(helm_release_field cert-manager cert-manager app_version || true)"
  current_rancher_version="$(helm_release_field rancher cattle-system app_version || true)"
  current_rancher_status="$(helm_release_status rancher cattle-system || true)"
  if [ -n "$current_rancher_status" ] && [ "$current_rancher_status" != "deployed" ]; then
    log_warning "Rancher Helm release is present at $(version_text "${current_rancher_version:-unknown}") but has status $(version_text "$current_rancher_status")."
  fi
  if ! recover_pending_rancher_upgrade "$current_rancher_status"; then
    exit 1
  fi
  if [ "$current_rancher_status" = "pending-upgrade" ]; then
    current_rancher_status="$(helm_release_status rancher cattle-system || true)"
    current_rancher_version="$(helm_release_field rancher cattle-system app_version || true)"
  fi
  upgrade_k3s=0
  upgrade_cert_manager=0
  upgrade_rancher=0

  if target_version_is_newer "$current_k3s_version" "$k3s_version"; then
    upgrade_k3s=1
  fi

  if target_version_is_newer "$current_cert_manager_version" "$cert_manager_version"; then
    upgrade_cert_manager=1
  fi

  if target_version_is_newer "$current_rancher_version" "$rancher_version"; then
    upgrade_rancher=1
  fi

  final_k3s_version="$current_k3s_version"
  final_cert_manager_version="$current_cert_manager_version"
  final_rancher_version="$current_rancher_version"

  if [ "$upgrade_k3s" -eq 1 ]; then
    final_k3s_version="$k3s_version"
  fi

  if [ "$upgrade_cert_manager" -eq 1 ]; then
    final_cert_manager_version="$cert_manager_version"
  fi

  if [ "$upgrade_rancher" -eq 1 ]; then
    final_rancher_version="$rancher_version"
  fi

  skip_if_already_current \
    "$k3s_version" \
    "$cert_manager_version" \
    "$rancher_version" \
    "$current_k3s_version" \
    "$current_cert_manager_version" \
    "$current_rancher_version"

  if [ "$upgrade_k3s" -eq 0 ] && [ "$upgrade_cert_manager" -eq 0 ] && [ "$upgrade_rancher" -eq 0 ]; then
    sync_cert_manager_fleet_version "$current_cert_manager_version"
    verify_cert_manager_health
    log_success "No selected target version is newer than the installed version. No upgrade needed."
    exit 0
  fi

  show_current_status
  save_snapshot_or_confirm

  printf '%b\n' "

About to run the upgrade with:
  $(component_text "Rancher"):       $(version_transition_text "$current_rancher_version" "$rancher_version")
  $(component_text "Rancher") URL:   $rancher_matrix_url
  $(component_text "k3s") minor:     $(version_text "$supported_k3s_minor")
  $(component_text "k3s") release:   $(version_transition_text "$current_k3s_version" "$k3s_version")
  $(component_text "cert-manager"):  $(version_transition_text "$current_cert_manager_version" "$cert_manager_version")
  order:         Rancher -> k3s -> cert-manager
  hostname:      $hostname_fqdn
  log file:      $log_file

"

  confirm "Do these versions and settings look good?" || exit 1

  if [ "$upgrade_rancher" -eq 1 ]; then
    upgrade_rancher_release "$rancher_version" "$rancher_chart_version" "$hostname_fqdn" "$current_rancher_version"
  else
    log_success "Skipping $(component_text "Rancher"); installed version is $(version_text "${current_rancher_version:-not detected}") and target $(version_text "$rancher_version") is not newer."
  fi

  if [ "$upgrade_k3s" -eq 1 ]; then
    log "Upgrading $(component_text "k3s") from $(version_text "${current_k3s_version:-not detected}") to $(version_text "$k3s_version") after $(component_text "Rancher") is on $(version_text "$final_rancher_version")."
    log "+ curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION=\"$k3s_install_version\" sh -s - server"
    curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION="$k3s_install_version" sh -s - server 2>&1 | tee -a "$log_file"

    # The installer restarts k3s and leaves its existing configuration intact.
    # Preserve node-specific settings and permissions managed outside this script.
    log "Preserving existing k3s configuration in /etc/rancher/k3s."
    wait_for_kubectl
    update_ansible_k3s_version "$k3s_version"
    stage_repo_file "$ansible_group_vars_file"
    log_error "IMPORTANT: Run Ansible to check and upgrade K3s on the other cluster nodes."
  else
    log_success "Skipping $(component_text "k3s"); installed version is $(version_text "${current_k3s_version:-not detected}") and target $(version_text "$k3s_version") is not newer."
  fi

  if [ "$upgrade_cert_manager" -eq 1 ]; then
    log "Upgrading $(component_text "cert-manager") from $(version_text "${current_cert_manager_version:-not detected}") to $(version_text "$cert_manager_version")."
    if [ -f "$cert_manager_fleet_file" ]; then
      sync_cert_manager_fleet_version "$cert_manager_version"
    else
      run_cmd helm --kubeconfig "$kubeconfig_path" upgrade --install cert-manager jetstack/cert-manager \
        --namespace cert-manager \
        --create-namespace \
        --version "$cert_manager_version" \
        --set startupapicheck.timeout=5m \
        --set crds.enabled=true

      run_cmd kubectl --kubeconfig "$kubeconfig_path" apply --validate=false -f "https://github.com/jetstack/cert-manager/releases/download/$cert_manager_version/cert-manager.crds.yaml"
    fi
    verify_cert_manager_health
  else
    log_success "Skipping $(component_text "cert-manager"); installed version is $(version_text "${current_cert_manager_version:-not detected}") and target $(version_text "$cert_manager_version") is not newer."
    sync_cert_manager_fleet_version "$current_cert_manager_version"
    verify_cert_manager_health
  fi

  verify_final_versions "$final_k3s_version" "$final_cert_manager_version" "$final_rancher_version"

  log_success "Upgrade completed."
}

main "$@"
