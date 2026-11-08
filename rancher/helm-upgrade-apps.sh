#!/bin/bash

set -u

# Arrow keys are sent as multi-byte escape sequences.  Give ncurses enough
# time to receive the complete sequence so the initial ESC is not mistaken
# for dialog's cancel/abort action.
ESCDELAY=1000
export ESCDELAY

kubeconfig_path="/etc/rancher/k3s/k3s.yaml"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
homelab_root="$(cd "$script_dir/.." && pwd)"
source "$script_dir/helm-repositories.sh"
log_dir="$script_dir/logs"
if [ ! -d "$log_dir" ] && ! mkdir -p "$log_dir" 2>/dev/null; then
  log_dir="${TMPDIR:-/tmp}/helm-upgrade-apps-logs"
  mkdir -p "$log_dir"
elif [ ! -w "$log_dir" ]; then
  log_dir="${TMPDIR:-/tmp}/helm-upgrade-apps-logs"
  mkdir -p "$log_dir"
fi
timestamp="$(date +"%Y%m%d-%H%M%S")"
log_file="$log_dir/helm-upgrade-${timestamp}.log"
yes_mode=0
dry_run=0
# Some charts run a lengthy entrypoint initialization (for example, recursive
# permission fixes) before their startup probe can succeed.  Give rollouts
# enough time to finish that initialization; ROLLOUT_TIMEOUT or the CLI option
# can still override this per run.
rollout_timeout="${ROLLOUT_TIMEOUT:-10m}"
exit_status=0
total_discovered_count=0
excluded_count=0
up_to_date_count=0
skipped_count=0
updated_count=0
staged_fleet_count=0
dry_run_approved_count=0
failed_apps_count=0
failure_events_count=0
version_check_error_apps=()
current_app=""
declare -A schema_repaired_values=()
current_app_failed=0
insecure_hosts=""
check_summary_title=""
check_summary_body=""
check_summary_kind=""
use_color=1
dialog_colors_supported=0
dialog_success_color=2
dialog_success_attr="b"
blue=""
green=""
red=""
yellow=""
nc=""

# Intentional exclusions; plain-resource Fleet bundles are detected separately.
exclude_patterns=(
  '^cattle-'
  '^kube-system$'
  '^cert-manager$'
  '^cloudnative-pg$'
  '^longhorn-crd$'
  '^shinobi$'
  '^intel-device-plugins-operator$'
  '^node-feature-discovery$'
  '^meshcommander$'
  '^plex$'
  '^homer-test.*$'
  '^fleet$'
  '^fleet-agent-local$'
  '^fleet-crd$'
  '^rancher$'
  '^rancher-turtles$'
  '^rancher-webhook$'
  '^system-upgrade-controller$'
  '^traefik$'
  '^traefik-config$'
  '^traefik-crd$'
)

is_up_to_date_helper="$script_dir/zabbix/is-helm-image-up-to-date.sh"
current_version_helper="$script_dir/zabbix/lib/helm-current-version-of-chart.sh"
rancher_upgrade_script="$script_dir/upgrade-k3s-rancher.sh"
rancher_ansible_vars_file="$script_dir/ansible/group_vars/all.yml"
chart_repo_helper="/etc/zabbix/zabbix_agent2.d/homelab/rancher/zabbix/lib/helm-chart-repo-dir-or-helm-repo.sh"
if [ ! -x "$chart_repo_helper" ]; then
  chart_repo_helper="$script_dir/zabbix/lib/helm-chart-repo-dir-or-helm-repo.sh"
fi

# Return the first exact chart match from Helm's configured repositories.
# `helm search repo` can return similarly named charts, so compare the chart
# name (the part after the final slash) rather than accepting the first row.
helm_exact_chart() {
  local app="$1"
  local configured_chart_ref

  configured_chart_ref="$(helm_chart_ref_for_app "$app" 2>/dev/null || true)"
  if [ -n "$configured_chart_ref" ]; then
    printf '%s\n' "$configured_chart_ref"
    return 0
  fi

  helm search repo "$app" --versions 2>/dev/null | awk -v app="$app" \
    'NR > 1 { name=$1; sub(/^.*\//, "", name); if (name == app) { print $1; exit } }'
}

chart_ref_for_app() {
  local app="$1"
  local chart_ref

  chart_ref="$(helm_exact_chart "$app")"
  if [ -n "$chart_ref" ]; then
    printf '%s\n' "$chart_ref"
    return 0
  fi

  # Some legacy/private charts are still only available in the local checkout.
  "$chart_repo_helper" "$app"
}

chart_version_for_app() {
  local app="$1"
  local chart_version
  local configured_chart_ref

  configured_chart_ref="$(helm_chart_ref_for_app "$app" 2>/dev/null || true)"
  if [ -n "$configured_chart_ref" ]; then
    helm show chart "$configured_chart_ref" 2>/dev/null | awk -F': *' '$1 == "version" { print $2; exit }'
    return 0
  fi

  chart_version="$(helm search repo "$app" --versions 2>/dev/null | awk -v app="$app" \
    'NR > 1 { name=$1; sub(/^.*\//, "", name); if (name == app) { print $2; exit } }')"
  if [ -n "$chart_version" ]; then
    printf '%s\n' "$chart_version"
    return 0
  fi

  # Fall back to the local chart only when Helm has no exact repository match.
  "$current_version_helper" "$app" --do-not-update-helm
}

usage() {
  cat <<USAGE
Usage: $(basename "$0") [--yes] [--dry-run] [--rollout-timeout DURATION] [--help]

Options:
  --yes       Auto-approve upgrades and continue prompts; HTTP repairs still ask.
  --dry-run   Do not apply Fleet changes; execute checks and prompts only.
  --rollout-timeout DURATION
              Timeout for each rollout status check (default: 10m).
  --help      Show this help message.

Exit codes:
  0 success/completed
  1 aborted by user or runtime failure
  2 dependency/setup error
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --yes)
      yes_mode=1
      shift
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    --rollout-timeout)
      shift
      if [ "$#" -eq 0 ] || [ -z "$1" ]; then
        echo "Missing value for --rollout-timeout" >&2
        exit 2
      fi
      rollout_timeout="$1"
      shift
      ;;
    --rollout-timeout=*)
      rollout_timeout="${1#*=}"
      if [ -z "$rollout_timeout" ]; then
        echo "Missing value for --rollout-timeout" >&2
        exit 2
      fi
      shift
      ;;
    --help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

log() {
  local message="$1"
  local display_message="${2:-$1}"
  local ts
  ts="$(date +"%Y-%m-%d %H:%M:%S")"
  printf '[%s] %s\n' "$ts" "$message" >>"$log_file"
  printf '[%s] %b\n' "$ts" "$display_message"
}

init_colors() {
  if [ "${TERM:-}" = "dumb" ] || [ -n "${NO_COLOR:-}" ]; then
    use_color=0
  fi

  if [ "$use_color" -eq 1 ]; then
    blue='\033[1;94m'
    green='\033[0;32m'
    red='\033[0;31m'
    yellow='\033[0;33m'
    nc='\033[0m'
  fi
}

init_dialog_colors() {
  dialog_colors_supported=0
  if [ "$use_color" -eq 1 ] && command -v dialog >/dev/null 2>&1; then
    if dialog --help 2>&1 | grep -q -- '--colors'; then
      dialog_colors_supported=1
    fi
  fi
}

color_blue() {
  local text="$1"
  if [ "$use_color" -eq 1 ]; then
    printf '%b%s%b' "$blue" "$text" "$nc"
  else
    printf '%s' "$text"
  fi
}

color_http_code() {
  local code="$1"
  if [ "$code" = "200" ]; then
    if [ "$use_color" -eq 1 ]; then
      printf '%b%s%b' "$green" "$code" "$nc"
    else
      printf '%s' "$code"
    fi
  else
    if [ "$use_color" -eq 1 ]; then
      printf '%b%s%b' "$red" "$code" "$nc"
    else
      printf '%s' "$code"
    fi
  fi
}

dialog_color_app() {
  local text="$1"
  if [ "$use_color" -eq 1 ] && [ "$dialog_colors_supported" -eq 1 ]; then
    printf '\\Zb\\Z4%s\\Z0' "$text"
  else
    printf '%s' "$text"
  fi
}

dialog_color_http_code() {
  local code="$1"
  if [ "$code" = "200" ]; then
    if [ "$use_color" -eq 1 ] && [ "$dialog_colors_supported" -eq 1 ]; then
      local prefix="\\Z${dialog_success_attr}\\Z${dialog_success_color}"
      printf '%s%s\\Z0' "$prefix" "$code"
    else
      printf '%s' "$code"
    fi
  else
    if [ "$use_color" -eq 1 ] && [ "$dialog_colors_supported" -eq 1 ]; then
      printf '\\Z1%s\\Z0' "$code"
    else
      printf '%s' "$code"
    fi
  fi
}

color_bash_return_code() {
  local code="$1"
  if [ "$code" = "0" ]; then
    if [ "$use_color" -eq 1 ]; then
      printf '%b%s%b' "$green" "$code" "$nc"
    else
      printf '%s' "$code"
    fi
  else
    if [ "$use_color" -eq 1 ]; then
      printf '%b%s%b' "$red" "$code" "$nc"
    else
      printf '%s' "$code"
    fi
  fi
}

color_count() {
  local count="$1"
  local color="${2:-}"
  if [ "$use_color" -eq 1 ] && [ -n "$color" ]; then
    printf '%b%s%b' "$color" "$count" "$nc"
  else
    printf '%s' "$count"
  fi
}

color_zero_ok_count() {
  local count="$1"
  if [ "$count" -eq 0 ]; then
    color_count "$count" "$green"
  else
    color_count "$count" "$red"
  fi
}

helper_status_label() {
  local code="$1"
  case "$code" in
    0) printf 'up_to_date' ;;
    1) printf 'update_available' ;;
    2) printf 'local_newer_than_repo' ;;
    *) printf 'unknown' ;;
  esac
}

color_helper_status() {
  local code="$1"
  local label
  label="$(helper_status_label "$code")"
  if [ "$code" = "0" ]; then
    if [ "$use_color" -eq 1 ]; then
      printf '%b%s%b' "$green" "$label" "$nc"
    else
      printf '%s' "$label"
    fi
  elif [ "$code" = "1" ] || [ "$code" = "2" ]; then
    if [ "$use_color" -eq 1 ]; then
      printf '%b%s%b' "$yellow" "$label" "$nc"
    else
      printf '%s' "$label"
    fi
  else
    if [ "$use_color" -eq 1 ]; then
      printf '%b%s%b' "$red" "$label" "$nc"
    else
      printf '%s' "$label"
    fi
  fi
}

color_helper_code() {
  local code="$1"
  if [ "$code" = "0" ]; then
    if [ "$use_color" -eq 1 ]; then
      printf '%b%s%b' "$green" "$code" "$nc"
    else
      printf '%s' "$code"
    fi
  elif [ "$code" = "1" ] || [ "$code" = "2" ]; then
    if [ "$use_color" -eq 1 ]; then
      printf '%b%s%b' "$yellow" "$code" "$nc"
    else
      printf '%s' "$code"
    fi
  else
    if [ "$use_color" -eq 1 ]; then
      printf '%b%s%b' "$red" "$code" "$nc"
    else
      printf '%s' "$code"
    fi
  fi
}

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing required command: $cmd" >&2
    return 1
  fi
  return 0
}

install_fleet_cli() {
  local download_file
  local fleet_url="https://github.com/rancher/fleet/releases/latest/download/fleet-linux-amd64"

  if ! command -v curl >/dev/null 2>&1; then
    echo "Cannot install Fleet CLI automatically because curl is missing." >&2
    return 1
  fi

  if [ "$yes_mode" -eq 1 ]; then
    log "AUTO: installing Fleet CLI from $fleet_url"
  elif ! command -v dialog >/dev/null 2>&1; then
    echo "Cannot ask whether to install Fleet CLI because dialog is missing." >&2
    return 1
  elif ! dialog \
    --clear \
    --title "Fleet CLI is missing" \
    --defaultno \
    --yesno "The Fleet CLI is required but is not installed.\n\nDownload and install it to /usr/local/bin/fleet now?" 12 80; then
    return 1
  fi

  download_file="$(mktemp "${TMPDIR:-/tmp}/fleet.XXXXXX")"
  log "Downloading Fleet CLI from $fleet_url"
  if ! curl -fL --retry 3 --connect-timeout 10 "$fleet_url" -o "$download_file" >>"$log_file" 2>&1; then
    rm -f "$download_file"
    echo "Fleet CLI download failed." >&2
    return 1
  fi
  chmod +x "$download_file"

  log "Installing Fleet CLI to /usr/local/bin/fleet"
  if ! sudo install -m 0755 "$download_file" /usr/local/bin/fleet >>"$log_file" 2>&1; then
    rm -f "$download_file"
    echo "Fleet CLI installation failed. You may need sudo access." >&2
    return 1
  fi
  rm -f "$download_file"

  if ! command -v fleet >/dev/null 2>&1; then
    echo "Fleet CLI installation completed, but fleet is still not in PATH." >&2
    return 1
  fi

  log "Fleet CLI installed: $(fleet --version 2>&1 | head -n 1)"
  return 0
}

fleet_file_for_app() {
  local app="$1"
  local fleet_file
  local fleet_name

  while IFS= read -r fleet_file; do
    fleet_name="$(awk -F': *' '$1 == "name" {print $2; exit}' "$fleet_file")"
    if [ "$fleet_name" = "$app" ]; then
      printf '%s\n' "$fleet_file"
      return 0
    fi
  done < <(find "$script_dir/fleet" -type f -name fleet.yaml -print)

  return 1
}

fleet_app_is_plain_resources() {
  local app="$1"
  local fleet_file
  local fleet_dir

  # An unknown release is not evidence of a plain-resource bundle.
  fleet_file="$(fleet_file_for_app "$app" 2>/dev/null || true)"
  [ -n "$fleet_file" ] || return 1
  fleet_dir="$(dirname "$fleet_file")"

  if awk '
    /^helm:[[:space:]]*$/ { in_helm=1; next }
    /^[^[:space:]#]/ { in_helm=0 }
    in_helm && /^[[:space:]]+chart:[[:space:]]*/ {
      value=$0
      sub(/^[[:space:]]+chart:[[:space:]]*/, "", value)
      sub(/[[:space:]]+#.*/, "", value)
      gsub(/[\042\047[:space:]]/, "", value)
      if (value != "" && value != "null" && value !~ /^#/) found=1
    }
    END { exit !found }
  ' "$fleet_file"; then
    return 1
  fi

  # Fleet also supports charts stored directly in the bundle directory.
  [ -z "$(find "$fleet_dir" -type f -name Chart.yaml -print -quit)" ]
}

fleet_app_is_scaled_to_zero() {
  local app="$1"
  local fleet_file
  local fleet_dir

  fleet_file="$(fleet_file_for_app "$app" 2>/dev/null || true)"
  [ -n "$fleet_file" ] || return 1
  fleet_dir="$(dirname "$fleet_file")"

  find "$fleet_dir" -type f \( -name '*.yaml' -o -name '*.yml' \) -print0 |
    xargs -0 grep -Eq '^[[:space:]]*replicas:[[:space:]]*0([[:space:]]*(#.*)?)?$'
}

fleet_version_from_file() {
  local fleet_file="$1"
  awk -F': *' '$1 == "version" {print $2; exit}' "$fleet_file"
}

update_fleet_version() {
  local fleet_file="$1"
  local target_version="$2"
  local temporary_file

  temporary_file="$(mktemp "${TMPDIR:-/tmp}/fleet-version.XXXXXX")"
  if ! awk -v target_version="$target_version" '
    BEGIN { updated = 0 }
    !updated && $0 ~ /^[[:space:]]*version:[[:space:]]*/ {
      sub(/version:[[:space:]]*.*/, "version: " target_version)
      updated = 1
    }
    { print }
    END { if (!updated) exit 1 }
  ' "$fleet_file" >"$temporary_file"; then
    rm -f "$temporary_file"
    return 1
  fi

  mv "$temporary_file" "$fleet_file"
}

fleet_bundle_name_for_app() {
  local app="$1"
  local candidate="$app"
  local repo_name

  if ! kubectl get bundle -n fleet-local --kubeconfig "$kubeconfig_path" "$candidate" >/dev/null 2>&1; then
    return 1
  fi

  repo_name="$(kubectl get bundle -n fleet-local --kubeconfig "$kubeconfig_path" "$candidate" \
    -o jsonpath='{.metadata.labels.fleet\.cattle\.io/repo-name}' 2>/dev/null || true)"
  if [ "$repo_name" = "rancher-cluster" ]; then
    printf '%s\n' "$candidate"
    return 0
  fi

  return 1
}

render_chart_for_pvc_preparation() {
  local app="$1"
  shift
  local error_file
  local render_error
  local recovery_message
  local attempt

  error_file="$(mktemp "${TMPDIR:-/tmp}/helm-render-error.XXXXXX")" || return 1
  for attempt in 1 2; do
    if helm template "$@" 2>"$error_file"; then
      cat "$error_file" >>"$log_file"
      rm -f "$error_file"
      return 0
    fi
    render_error="$(cat "$error_file")"
    printf '%s\n' "$render_error" >>"$log_file"
    if [ "$attempt" -eq 2 ]; then
      break
    fi
    recovery_message="Helm could not render the target chart.\n\n${render_error:0:1800}\n\nProposed recovery: refresh the configured Helm repository indexes and retry the same pinned chart once. This does not change PVCs or the chart version.\n\nRefresh and retry?"
    if [ "$yes_mode" -eq 1 ]; then
      log "$app: automatically refreshing Helm repositories and retrying chart rendering" >&2
    elif ! dialog --clear --title "Repair chart rendering ($app)" \
      --yes-label "Refresh and retry" --no-label "Skip repair" --defaultno \
      --yesno "$recovery_message" 22 110; then
      break
    fi
    # Keep recovery output out of the captured manifest stream.
    helm repo update >>"$log_file" 2>&1 || true
  done
  rm -f "$error_file"
  record_failure_and_maybe_abort "Unable to render PVC labels ($app)" \
    "Could not render the pinned target chart before PVC preparation.\n\nHelm error:\n${render_error:0:1800}\n\nCheck the chart repository, pinned version, network access and values file. Full output: $log_file" >&2
  return 1
}

apply_pvc_version_labels() {
  local namespace="$1"
  local pvc_name="$2"
  shift 2
  local manifest

  manifest="$(jq -n --arg namespace "$namespace" --arg name "$pvc_name" \
    --args '
      {apiVersion: "v1", kind: "PersistentVolumeClaim", metadata: {
        name: $name, namespace: $namespace,
        labels: ($ARGS.positional | map(capture("^(?<key>[^=]+)=(?<value>.*)$")) | from_entries)
      }}
    ' "$@")" || return 1

  # Use a dedicated Apply manager, never the full-chart Helm manager: omitted
  # fields previously applied by that manager could otherwise be removed.
  # Force ownership only for these rendered version labels. Fleet can then
  # apply the same values without conflicting with legacy Helm Update owners.
  printf '%s\n' "$manifest" | kubectl apply \
    --kubeconfig "$kubeconfig_path" --namespace "$namespace" \
    --server-side --force-conflicts --field-manager=helm-pvc-version-labels -f -
}

extract_pvc_manifests() {
  # Helm charts may quote YAML kind values. Match the scalar value rather
  # than requiring the exact spelling "kind: PersistentVolumeClaim".
  awk '
    BEGIN { RS="---\n" }
    {
      count=split($0, lines, "\n")
      for (i=1; i<=count; i++) {
        if (lines[i] ~ /^kind:[[:space:]]*/) {
          kind=lines[i]
          sub(/^kind:[[:space:]]*/, "", kind)
          sub(/[[:space:]]+#.*/, "", kind)
          gsub(/[\042\047[:space:]]/, "", kind)
          if (kind == "PersistentVolumeClaim") printf "---\n%s", $0
          break
        }
      }
    }
  '
}

prepare_helm_upgrade_pvcs() {
  local app="$1"
  local namespace="$2"
  local target_version="$3"
  local fleet_file="$4"
  local chart_name
  local chart_repo
  local target_app_label
  local target_app_version
  local release_name
  local target_chart_label
  local pvc_names
  local pvc_name
  local storage_request
  local objectset_id
  local values_file
  local external_claims
  local rendered_pvcs=""
  local rendered_chart
  local render_file
  local pvc_labels
  local pvc_label
  local render_args=()
  local pvc_version_labels=()

  chart_name="$(sed -n 's/^[[:space:]]*chart:[[:space:]]*//p' "$fleet_file" | head -1)"
  chart_repo="$(sed -n 's/^[[:space:]]*repo:[[:space:]]*//p' "$fleet_file" | head -1)"
  release_name="$(awk -F': *' '$1 == "  releaseName" {print $2; exit}' "$fleet_file")"
  release_name="${release_name:-$app}"
  if [ -z "$chart_name" ]; then
    return 0
  fi

  values_file="$(dirname "$fleet_file")/values.yaml"
  external_claims=""
  if [ -f "$values_file" ]; then
    external_claims="$(awk -F': *' '$1 ~ /existingClaim$/ {print $2}' "$values_file")"
  fi

  objectset_id="default-${release_name}-cattle-fleet-local-system"
  pvc_names="$(kubectl get pvc --kubeconfig "$kubeconfig_path" --namespace "$namespace" \
    -l "app.kubernetes.io/instance=$release_name" \
    -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>>"$log_file" || true)"

  while IFS= read -r pvc_name; do
    [ -z "$pvc_name" ] && continue
    if printf '%s\n' "$external_claims" | grep -Fxq "$pvc_name"; then
      if ! kubectl annotate pvc "$pvc_name" \
        --kubeconfig "$kubeconfig_path" --namespace "$namespace" \
        helm.sh/resource-policy=keep --overwrite >>"$log_file" 2>&1; then
        record_failure_and_maybe_abort "Unable to protect external PVC ($app)" \
          "Could not set Helm resource-policy=keep on restored PVC $namespace/$pvc_name."
        return 1
      fi
      log "$app: leaving externally restored existingClaim PVC $pvc_name unmanaged" \
        "$(color_blue "$app"): leaving restored PVC $pvc_name unmanaged"
      continue
    fi
    # Use each PVC's rendered labels, including dependency charts such as
    # MongoDB. Parent chart versions are not valid labels for dependency PVCs.
    if [ -z "$rendered_pvcs" ]; then
      render_args=("$release_name" "$chart_name" --version "$target_version" --namespace "$namespace")
      if [ -n "$chart_repo" ]; then
        render_args+=(--repo "$chart_repo")
      fi
      if [ -f "$values_file" ]; then
        render_args+=(-f "$values_file")
      fi
      render_file="$(mktemp "${TMPDIR:-/tmp}/helm-pvc-manifests.XXXXXX")" || return 1
      if ! render_chart_for_pvc_preparation "$app" "${render_args[@]}" >"$render_file"; then
        rm -f "$render_file"
        return 1
      fi
      rendered_chart="$(extract_pvc_manifests < "$render_file")"
      rm -f "$render_file"
      if [ -z "$rendered_chart" ]; then
        log "$app: target chart renders no PVCs; leaving existing PVCs unmanaged"
        return 0
      fi
      if ! rendered_pvcs="$(set -o pipefail
        printf '%s\n' "$rendered_chart" |
          kubectl create --kubeconfig "$kubeconfig_path" --namespace "$namespace" \
            --dry-run=client --validate=false -f - \
            -o go-template='{{define "pvc"}}{{.metadata.name}}{{"|"}}{{index .metadata.labels "helm.sh/chart"}}{{"|"}}{{index .metadata.labels "app"}}{{"|"}}{{index .metadata.labels "app.kubernetes.io/version"}}{{"\n"}}{{end}}{{if .items}}{{range .items}}{{template "pvc" .}}{{end}}{{else}}{{template "pvc" .}}{{end}}' 2>>"$log_file")"; then
        record_failure_and_maybe_abort "Unable to read PVC labels ($app)" \
          "Could not read PVC metadata from the rendered target chart."
        return 1
      fi
    fi
    pvc_labels="$(printf '%s\n' "$rendered_pvcs" | awk -F'|' -v name="$pvc_name" '$1 == name {print; exit}')"
    if [ -z "$pvc_labels" ]; then
      log "$app: PVC $pvc_name is absent from the target chart; leaving it unmanaged"
      continue
    fi
    IFS='|' read -r pvc_label target_chart_label target_app_label target_app_version <<< "$pvc_labels"
    pvc_version_labels=()
    [ -z "$target_chart_label" ] || [ "$target_chart_label" = '<no value>' ] || pvc_version_labels+=("helm.sh/chart=$target_chart_label")
    [ -z "$target_app_label" ] || [ "$target_app_label" = '<no value>' ] || pvc_version_labels+=("app=$target_app_label")
    [ -z "$target_app_version" ] || [ "$target_app_version" = '<no value>' ] || pvc_version_labels+=("app.kubernetes.io/version=$target_app_version")
    # A metadata-only label update preserves immutable bound-PVC spec fields.
    if [ "${#pvc_version_labels[@]}" -gt 0 ]; then
      log "$app: aligning PVC $pvc_name with its rendered target labels"
      if ! apply_pvc_version_labels "$namespace" "$pvc_name" \
        "${pvc_version_labels[@]}" >>"$log_file" 2>&1; then
        record_failure_and_maybe_abort "Unable to prepare PVC labels ($app)" \
          "Could not align target labels on PVC $namespace/$pvc_name."
        return 1
      fi
    fi

    # Fleet's Helm driver uses server-side apply.  A PVC created by an older
    # Helm release can still own the storage request field, which makes the
    # next Fleet reconciliation fail with a conflict such as:
    #   conflict with "helm" using v1: .spec.resources.requests.storage
    # Transfer only that mutable field to the same `helm` field manager.  Do
    # not apply the complete PVC object, since most PVC fields are immutable.
    storage_request="$(kubectl get pvc "$pvc_name" \
      --kubeconfig "$kubeconfig_path" --namespace "$namespace" \
      -o jsonpath='{.spec.resources.requests.storage}' 2>>"$log_file" || true)"
    if [ -n "$storage_request" ]; then
      log "$app: aligning PVC $pvc_name storage ownership for Fleet Helm" \
        "$(color_blue "$app"): aligning PVC storage ownership before Fleet upgrade"
      if ! kubectl patch pvc "$pvc_name" \
        --kubeconfig "$kubeconfig_path" --namespace "$namespace" \
        --type=merge \
        -p "{\"spec\":{\"resources\":{\"requests\":{\"storage\":\"$storage_request\"}}}}" \
        >>"$log_file" 2>&1; then
        record_failure_and_maybe_abort "Unable to prepare PVC storage ownership ($app)" \
          "Could not transfer PVC $namespace/$pvc_name storage ownership to Fleet's Helm manager."
        return 1
      fi
    fi

    # Restored PVCs can retain Helm metadata while losing Fleet's objectset
    # ownership marker.  Fleet's Helm driver then refuses to manage the PVC
    # with "is not owned by us".  Restore only ownership metadata; never
    # delete or recreate the data-bearing PVC.
    log "$app: aligning PVC $pvc_name Fleet ownership metadata" \
      "$(color_blue "$app"): aligning PVC Fleet ownership metadata"
    if ! kubectl annotate pvc "$pvc_name" \
      --kubeconfig "$kubeconfig_path" --namespace "$namespace" \
      "objectset.rio.cattle.io/id=$objectset_id" \
      "meta.helm.sh/release-name=$release_name" \
      "meta.helm.sh/release-namespace=$namespace" \
      --overwrite >>"$log_file" 2>&1; then
      record_failure_and_maybe_abort "Unable to prepare PVC ownership ($app)" \
        "Could not restore Fleet ownership metadata on PVC $namespace/$pvc_name. The PVC was not deleted."
      return 1
    fi
    if ! kubectl label pvc "$pvc_name" \
      --kubeconfig "$kubeconfig_path" --namespace "$namespace" \
      app.kubernetes.io/managed-by=Helm --overwrite \
      --field-manager=helm >>"$log_file" 2>&1; then
      record_failure_and_maybe_abort "Unable to prepare PVC labels ($app)" \
        "Could not restore Helm ownership labels on PVC $namespace/$pvc_name. The PVC was not deleted."
      return 1
    fi
  done <<< "$pvc_names"
}

fleet_bundledeployment_failure_message() {
  local bundledeployment_namespace="$1"
  local bundledeployment_name="$2"
  local since="${3:-}"
  local message

  message="$(timeout 20s kubectl --request-timeout=15s get bundledeployment "$bundledeployment_name" \
    --kubeconfig "$kubeconfig_path" --namespace "$bundledeployment_namespace" \
    -o json 2>>"$log_file" | jq -r --arg since "$since" '
      # A chart upgrade can briefly leave PVC version labels behind. Wait
      # for Ready rather than treating that metadata-only drift as fatal.
      def pvc_label_drift:
        .type == "Ready" and
        ((try (
          .message
          | capture("^persistentvolumeclaim[.]v1 [^ ]+ modified\\s+(?<patch>\\{[\\s\\S]*\\})$")
          | .patch | fromjson
          | (keys == ["metadata"] and
             (.metadata | keys) == ["labels"] and
             (.metadata.labels | type) == "object")
        ) catch false) // false);
      . as $deployment |
      (.status.conditions[]? |
        select(.status == "False") |
        select(
          if .type == "Deployed" and
             ((.message // "") | contains("dependent bundle(s) are not ready"))
          then
            .message as $message |
            any($deployment.spec.dependsOn[]?;
              .name as $name | $message | contains($name))
          else true end
        ) |
        # A dependency blocker remains applicable even when its timestamp
        # predates this upgrade; Fleet can retain it across retries.
        select($since == "" or (.lastUpdateTime // "") >= $since or
          (.type == "Deployed" and
           ((.message // "") | contains("dependent bundle(s) are not ready")))) |
        select(pvc_label_drift | not) |
        "\(.type): \(.reason): \(.message)"),
      (if $since == "" then (.status.display.message // empty) else empty end)
    ' 2>>"$log_file" || true)"
  # Fleet reports a transient Ready error while a Deployment is replacing its
  # old pod, for example: "progressing ... minimum availability, Replicas:
  # 0/1".  That is not a failed Helm reconciliation; allow the readiness loop
  # to wait for the rollout.  Persistent conflicts and other actual errors are
  # still returned immediately.
  if printf '%s' "$message" | grep -Eiq 'failed|failure|error|conflict|unable|forbidden|invalid' && \
    ! printf '%s' "$message" | grep -Eiq 'progressing|does not have minimum availability|replicas:[[:space:]]*[0-9]+/[1-9]|job in progress|status:[[:space:]]*inprogress|post-upgrade hooks failed.*context deadline exceeded'; then
    printf '%s\n' "$message"
  fi
}

helm_release_failure_message() {
  local app="$1"
  local namespace="$2"
  local message

  message="$(timeout 20s helm status "$app" \
    --kubeconfig "$kubeconfig_path" \
    --namespace "$namespace" \
    -o json 2>>"$log_file" | \
    jq -r '.info.description // .info.status // empty' 2>>"$log_file" || true)"
  printf '%s' "$message"
}

ask_attempt_fleet_repair() {
  local app="$1"
  local namespace="$2"

  if [ "$yes_mode" -eq 1 ]; then
    log "AUTO: attempting one Fleet reconciliation retry for failed Helm release $app"
    return 0
  fi

  dialog \
    --clear \
    --begin 2 10 \
    --title "Attempt recovery for $app?" \
    --defaultno \
    --yesno "Helm release $namespace/$app is failed, but Fleet still has the previous deployment applied.\n\nThe old deployment should remain available. Attempt one fresh Fleet reconciliation before marking this app failed?" 18 110
}

# Relinquish only the self-updater's ownership on the three objects reported
# by this known CloudCasa conflict. Preserve live values and other managers.
repair_cloudcasa_field_ownership() {
  local resource name object patch
  if ! dialog --clear --begin 2 10 --defaultno \
    --title "Attempt CloudCasa ownership repair?" \
    --yesno "CloudCasa's self-updater owns fields needed by the Helm upgrade.\n\nRelease the cloudcasa-kubeagent-manager field ownership on ServiceAccount cloudcasa-io, ClusterRoleBinding cloudcasa-io, and Deployment cloudcasa-kubeagent-manager, then retry Fleet once?\n\nLive values remain unchanged during ownership repair. The subsequent upgrade may replace the agent image and restart it, interrupting an active backup. Other managers and PVCs are preserved.\n\nAttempt this repair?" 24 110; then
    log "cloudcasa: user declined ownership repair"
    return 1
  fi
  log "cloudcasa: user approved scoped self-updater ownership repair"
  for resource in serviceaccount clusterrolebinding deployment; do
    name=cloudcasa-io
    [ "$resource" != deployment ] || name=cloudcasa-kubeagent-manager
    object="$(timeout 20s kubectl --request-timeout=15s get "$resource" "$name" \
      --namespace cloudcasa-io --kubeconfig "$kubeconfig_path" \
      --show-managed-fields -o json 2>>"$log_file")" || return 1
    patch="$(printf '%s' "$object" | jq -c '
      (.metadata.managedFields // []) as $fields |
      if any($fields[]; .manager == "cloudcasa-kubeagent-manager") then
        [{op:"test", path:"/metadata/resourceVersion", value:.metadata.resourceVersion},
         {op:"test", path:"/metadata/managedFields", value:$fields},
         {op:"replace", path:"/metadata/managedFields",
          value:[$fields[] | select(.manager != "cloudcasa-kubeagent-manager")]}]
      else [] end')" || return 1
    if [ "$patch" = '[]' ]; then
      log "cloudcasa: $resource/$name already has no self-updater ownership"
      continue
    fi
    printf '%s\n' "$object" | jq '{kind, metadata: {name:.metadata.name, namespace:.metadata.namespace, resourceVersion:.metadata.resourceVersion, managedFields:.metadata.managedFields}}' \
      > "$log_dir/cloudcasa-ownership-$resource-$(date +%Y%m%d-%H%M%S).json"
    timeout 20s kubectl --request-timeout=15s patch "$resource" "$name" \
      --namespace cloudcasa-io --kubeconfig "$kubeconfig_path" \
      --type=json --patch "$patch" >>"$log_file" 2>&1 || return 1
    log "cloudcasa: released self-updater ownership on $resource/$name"
  done
}

request_fleet_reconciliation_retry() {
  local bundle_name="$1"
  local bundledeployment_namespace="$2"
  local bundledeployment_name="$3"
  local previous_desired_deployment_id="$4"
  local force_sync_generation
  local bundle_generation
  local retry_attempts=0
  local current_desired_deployment_id

  force_sync_generation="$(kubectl get bundle "$bundle_name" \
    --kubeconfig "$kubeconfig_path" --namespace fleet-local \
    -o jsonpath='{.spec.forceSyncGeneration}' 2>>"$log_file" || true)"
  if ! [[ "$force_sync_generation" =~ ^[0-9]+$ ]]; then
    force_sync_generation=0
  fi
  force_sync_generation=$((force_sync_generation + 1))

  log "$bundle_name: requesting one fresh Fleet reconciliation after failed Helm release"
  if ! bundle_generation="$(kubectl patch bundle "$bundle_name" \
    --kubeconfig "$kubeconfig_path" --namespace fleet-local \
    --type=merge \
    --patch "{\"spec\":{\"forceSyncGeneration\":$force_sync_generation}}" \
    -o jsonpath='{.metadata.generation}' 2>>"$log_file")"; then
    log "$bundle_name: could not request Fleet reconciliation retry"
    return 1
  fi

  while [ "$retry_attempts" -lt 120 ]; do
    if kubectl wait \
      --kubeconfig "$kubeconfig_path" \
      --namespace fleet-local \
      --timeout=2s \
      --for="jsonpath={.status.observedGeneration}=$bundle_generation" \
      "bundle/$bundle_name" >>"$log_file" 2>&1; then
      break
    fi
    retry_attempts=$((retry_attempts + 1))
    sleep 1
  done

  retry_attempts=0
  while [ "$retry_attempts" -lt 120 ]; do
    current_desired_deployment_id="$(kubectl -n "$bundledeployment_namespace" get bundledeployment "$bundledeployment_name" \
      --kubeconfig "$kubeconfig_path" -o jsonpath='{.spec.deploymentID}' 2>>"$log_file" || true)"
    if [ -n "$current_desired_deployment_id" ] && \
      [ "$current_desired_deployment_id" != "$previous_desired_deployment_id" ]; then
      desired_deployment_id="$current_desired_deployment_id"
      bundledeployment_wait_attempts=0
      return 0
    fi
    retry_attempts=$((retry_attempts + 1))
    sleep 1
  done

  log "$bundle_name: Fleet did not publish a new deployment for the retry"
  return 1
}

apply_fleet_bundle_with_repair() {
  local app="$1"
  shift
  local error_file
  local apply_error
  local recovery_message
  local webhook_unavailable=0
  local attempt
  local endpoint_attempt
  local webhook_endpoints=""

  error_file="$(mktemp "${TMPDIR:-/tmp}/fleet-apply-error.XXXXXX")" || return 1
  for attempt in 1 2; do
    if (cd "$homelab_root" && fleet apply "$@") >"$error_file" 2>&1; then
      cat "$error_file" >>"$log_file"
      rm -f "$error_file"
      return 0
    fi
    apply_error="$(cat "$error_file")"
    cat "$error_file" >>"$log_file"
    [ "$attempt" -eq 1 ] || break

    if [[ "$apply_error" == *'failed calling webhook'* &&
          "$apply_error" == *'no endpoints available for service "rancher-webhook"'* ]]; then
      webhook_unavailable=1
      recovery_message="Fleet apply failed because rancher-webhook has no available endpoints.\n\n${apply_error:0:1800}\n\nAttempt repair by restarting deployment cattle-system/rancher-webhook, waiting for its rollout and service endpoint, then retrying Fleet apply once?"
    else
      recovery_message="Fleet apply failed.\n\n${apply_error:0:1800}\n\nAttempt recovery by retrying the same Fleet apply once?"
    fi
    if [ "$yes_mode" -eq 1 ]; then
      log "$app: automatically attempting recovery after Fleet apply failure"
    elif ! dialog --clear --title "Repair Fleet Bundle apply ($app)" \
      --yes-label "Attempt repair" --no-label "Skip repair" --defaultno \
      --yesno "$recovery_message" 24 110; then
      break
    fi

    if [ "$webhook_unavailable" -eq 1 ]; then
      log "$app: restarting rancher-webhook to restore admission webhook endpoints"
      if ! kubectl --kubeconfig "$kubeconfig_path" --namespace cattle-system \
        rollout restart deployment/rancher-webhook >>"$log_file" 2>&1 || \
        ! kubectl --kubeconfig "$kubeconfig_path" --namespace cattle-system \
        rollout status deployment/rancher-webhook --timeout=180s >>"$log_file" 2>&1; then
        apply_error="$apply_error"$'\n\n'"Repair failed: rancher-webhook could not restart or become ready."
        break
      fi
      for ((endpoint_attempt=0; endpoint_attempt<30; endpoint_attempt++)); do
        webhook_endpoints="$(kubectl --kubeconfig "$kubeconfig_path" --namespace cattle-system \
          get endpoints rancher-webhook -o jsonpath='{.subsets[*].addresses[*].ip}' 2>>"$log_file" || true)"
        [ -z "$webhook_endpoints" ] || break
        sleep 2
      done
      if [ -z "$webhook_endpoints" ]; then
        apply_error="$apply_error"$'\n\n'"Repair failed: rancher-webhook still has no ready service endpoints."
        break
      fi
    fi
    log "$app: retrying Fleet Bundle apply once"
  done
  rm -f "$error_file"
  record_failure_and_maybe_abort "Fleet Bundle apply failed ($app)" \
    "Fleet could not update the Bundle and its Helm values Secret.\n\n${apply_error:0:2200}\n\nFull output: $log_file"
  return 1
}

generate_fleet_bundle() {
  local app="$1" bundle_name="$2" relative_fleet_dir="$3" output_file="$4"
  local error_file generation_error attempt
  error_file="$(mktemp "${TMPDIR:-/tmp}/fleet-generation.XXXXXX.log")" || return 1
  for attempt in 1 2 3; do
    if (cd "$homelab_root" && fleet apply \
      --kubeconfig "$kubeconfig_path" --namespace fleet-local \
      --output "$output_file" "$bundle_name" "$relative_fleet_dir") >"$error_file" 2>&1; then
      cat "$error_file" >>"$log_file"
      rm -f "$error_file"
      return 0
    fi
    cat "$error_file" >>"$log_file"
    if [ "$attempt" -lt 3 ] && grep -Eqi \
      'connection refused|connection reset|i/o timeout|TLS handshake timeout|context deadline exceeded|temporary failure|temporary error|no such host|unexpected EOF|status[^[:digit:]]*(429|500|502|503|504)' "$error_file"; then
      log "$app: transient Fleet generation failure (attempt $attempt/3); retrying in $((attempt * 2))s"
      : >"$output_file"
      sleep "$((attempt * 2))"
    else
      break
    fi
  done
  generation_error="$(tail -c 2200 "$error_file")"
  rm -f "$error_file"
  record_failure_and_maybe_abort "Fleet apply failed ($app)" \
    "fleet apply could not generate bundle $bundle_name after $attempt attempt(s). No cluster resources were changed.\n\n$generation_error\n\nFull output: $log_file"
  return 1
}

# Returns 0 for no repair, 2 for an approved edit, 1 to stop this app.
# This known invalid Kubernetes field can be diagnosed before Helm/Fleet fails.
maybe_repair_values_schema() {
  local app="$1" fleet_dir="$2"
  local helper="$script_dir/lib/helm-values-repair.py"
  local repair_dir repair_plan description diagnostic_status=0
  [ "$app" = youtubedl-material ] || return 0
  [ "$dry_run" -eq 0 ] || return 0
  if [ ! -f "$helper" ]; then
    return 0
  fi
  repair_dir="$(mktemp -d "$log_dir/schema-repair-${app}-XXXXXX")" || return 1
  chmod 700 "$repair_dir"
  repair_plan="$repair_dir/plan.json"
  description="$(/usr/bin/python3 "$helper" --values "$fleet_dir/values.yaml" \
      --plan "$repair_plan" 2>>"$log_file")" || diagnostic_status=$?
  [ "$diagnostic_status" -ne 1 ] || return 0
  if [ "$diagnostic_status" -ne 0 ]; then
    record_failure_and_maybe_abort "Schema repair requires review ($app)" \
      "The fsGroup values are invalid or conflicting. See $log_file. No repair was applied."
    return 1
  fi
  # Explicit approval is required even under --yes.
  if ! dialog --clear --begin 2 10 --defaultno \
      --title "Repair invalid fsGroup placement ($app)?" \
      --yes-label "Apply repair" --no-label "Skip repair" \
      --yesno "$description\n\nBack up and edit $fleet_dir/values.yaml, then continue native Fleet reconciliation with schema validation enabled? The normal chart rollout may restart the application. Values will be staged only after rollout and HTTP postchecks.\n\nBackup and plan: $repair_dir\n\nApply this repair?" 23 112; then
    log "$app: user declined fsGroup schema repair; no values were changed"
    return 1
  fi
  log "$app: user approved fsGroup schema repair; plan=$repair_plan"
  if ! /usr/bin/python3 "$helper" --apply "$repair_plan" >>"$log_file" 2>&1; then
    record_failure_and_maybe_abort "Schema repair failed ($app)" \
      "Values could not be changed safely. Review $repair_dir and $log_file."
    return 1
  fi
  schema_repaired_values["$app"]="$fleet_dir/values.yaml"
  log "$app: moved fsGroup to pod scope; original values=$repair_dir/values-before.yaml"
  return 2
}

apply_fleet_bundle() {
  local app="$1"
  local fleet_dir="$2"
  local namespace="$3"
  local target_version="$4"
  local values_schema_repair_status=0
  maybe_repair_values_schema "$app" "$fleet_dir" || values_schema_repair_status=$?
  [ "$values_schema_repair_status" -ne 1 ] || return 1
  local bundle_name
  local relative_fleet_dir
  local generated_bundle_file
  local bundle_generation
  local bundle_force_sync_generation
  local previous_applied_deployment_id
  local bundledeployment_namespace
  local bundledeployment_name
  local desired_deployment_id
  local current_desired_deployment_id
  local bundledeployment_lookup_attempts=0
  local bundledeployment_wait_attempts=0
  local deployment_deadline
  local next_progress_at
  local applied_deployment_id
  local bundledeployment_ready
  local failure_message
  local helm_status
  local helm_failure_message
  local previous_helm_revision
  local current_helm_revision
  local helm_status_json
  local reconciliation_started_at
  local native_apply_labels=()
  local existing_label
  local prune_candidates
  local dependency_repair_attempted=0
  local fleet_repair_attempted=0
  local fleet_status_retry=0

  bundle_name="$(fleet_bundle_name_for_app "$app")" || {
    record_failure_and_maybe_abort "Fleet bundle not found ($app)" \
      "Expected GitRepo-managed bundle named $app in namespace fleet-local."
    return 1
  }

  relative_fleet_dir="${fleet_dir#"$homelab_root/"}"
  if [ "$relative_fleet_dir" = "$fleet_dir" ]; then
    record_failure_and_maybe_abort "Fleet path is outside repository ($app)" \
      "Fleet CLI requires a repository-relative path for $fleet_dir."
    return 1
  fi

  generated_bundle_file="$(mktemp "${TMPDIR:-/tmp}/fleet-bundle.XXXXXX.yaml")"
  log "$app: generating local Fleet Bundle from $relative_fleet_dir" \
    "$(color_blue "$app"): generating local Fleet Bundle"
  if ! generate_fleet_bundle "$app" "$bundle_name" "$relative_fleet_dir" "$generated_bundle_file"; then
    rm -f "$generated_bundle_file"
    return 1
  fi

  if ! prepare_helm_upgrade_pvcs "$app" "$namespace" "$target_version" "$fleet_dir/fleet.yaml"; then
    rm -f "$generated_bundle_file"
    return 1
  fi

  previous_applied_deployment_id="$(kubectl get bundledeployment -A \
    --kubeconfig "$kubeconfig_path" \
    -l "fleet.cattle.io/bundle-name=$bundle_name" \
    -o jsonpath='{.items[0].status.appliedDeploymentID}' 2>>"$log_file" || true)"

  log "$app: applying generated Fleet Bundle $bundle_name" \
    "$(color_blue "$app"): applying generated Fleet Bundle $bundle_name"
  previous_helm_revision="$(helm status "$app" --kubeconfig "$kubeconfig_path" \
    --namespace "$namespace" -o json 2>>"$log_file" | jq -r '.version // 0' 2>>"$log_file" || true)"
  [[ "$previous_helm_revision" =~ ^[0-9]+$ ]] || previous_helm_revision=0
  reconciliation_started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  # Offline output does not refresh Fleet's separate Helm values Secret.
  # Native apply updates the Bundle, values hash, and Secret together.
  # Its pruning scope is the positional repo name: refuse any unrelated bundle
  # in that scope, and preserve the existing GitRepo ownership labels.
  prune_candidates="$(kubectl get bundles --kubeconfig "$kubeconfig_path" \
    --namespace fleet-local -l "fleet.cattle.io/repo-name=$bundle_name" \
    -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>>"$log_file")" || return 1
  while IFS= read -r existing_label; do
    [ -z "$existing_label" ] || [ "$existing_label" = "$bundle_name" ] || {
      rm -f "$generated_bundle_file"
      record_failure_and_maybe_abort "Unsafe Fleet apply scope ($app)" \
        "Native apply could prune unrelated bundle $existing_label; refusing to proceed."
      return 1
    }
  done <<< "$prune_candidates"
  while IFS= read -r existing_label; do
    [ -z "$existing_label" ] || native_apply_labels+=(--label "$existing_label")
  done < <(kubectl get bundle "$bundle_name" --kubeconfig "$kubeconfig_path" \
    --namespace fleet-local -o json 2>>"$log_file" | jq -r \
    '.metadata.labels | to_entries[] | select(.key != "fleet.cattle.io/commit") | "\(.key)=\(.value)"')
  if ! apply_fleet_bundle_with_repair "$app" --kubeconfig "$kubeconfig_path" \
    --namespace fleet-local "${native_apply_labels[@]}" \
    "$bundle_name" "$relative_fleet_dir"; then
    rm -f "$generated_bundle_file"
    return 1
  fi

  # Applying the same chart version can leave the Bundle generation unchanged
  # (for example after a failed retry).  An annotation does not change
  # metadata.generation, so increment Fleet's forceSyncGeneration spec field
  # to request a real reconciliation and avoid reading stale status.
  bundle_force_sync_generation="$(kubectl get bundle "$bundle_name" \
    --kubeconfig "$kubeconfig_path" --namespace fleet-local \
    -o jsonpath='{.spec.forceSyncGeneration}' 2>>"$log_file" || true)"
  if ! [[ "$bundle_force_sync_generation" =~ ^[0-9]+$ ]]; then
    bundle_force_sync_generation=0
  fi
  bundle_force_sync_generation=$((bundle_force_sync_generation + 1))
  if ! bundle_generation="$(kubectl patch bundle "$bundle_name" \
    --kubeconfig "$kubeconfig_path" --namespace fleet-local \
    --type=merge \
    --patch "{\"spec\":{\"forceSyncGeneration\":$bundle_force_sync_generation}}" \
    -o jsonpath='{.metadata.generation}' 2>>"$log_file")"; then
    record_failure_and_maybe_abort "Fleet reconciliation request failed ($app)" \
      "Could not request a fresh reconciliation for bundle $bundle_name."
    return 1
  fi
  rm -f "$generated_bundle_file"

  log "$app: waiting for Fleet to observe bundle generation $bundle_generation" \
    "$(color_blue "$app"): waiting for Fleet to observe bundle generation $bundle_generation"
  if ! kubectl wait \
    --kubeconfig "$kubeconfig_path" \
    --namespace fleet-local \
    --timeout=10m \
    --for="jsonpath={.status.observedGeneration}=$bundle_generation" \
    "bundle/$bundle_name" >>"$log_file" 2>&1; then
    record_failure_and_maybe_abort "Fleet reconciliation did not start ($app)" \
      "Fleet did not observe generation $bundle_generation for bundle $bundle_name."
    return 1
  fi

  bundledeployment_namespace=""
  bundledeployment_name=""
  while { [ -z "$bundledeployment_namespace" ] || [ -z "$bundledeployment_name" ]; } && \
    [ "$bundledeployment_lookup_attempts" -lt 600 ]; do
    bundledeployment_namespace="$(kubectl get bundledeployment -A \
      --kubeconfig "$kubeconfig_path" \
      -l "fleet.cattle.io/bundle-name=$bundle_name" \
      -o jsonpath='{.items[0].metadata.namespace}' 2>>"$log_file" || true)"
    bundledeployment_name="$(kubectl get bundledeployment -A \
      --kubeconfig "$kubeconfig_path" \
      -l "fleet.cattle.io/bundle-name=$bundle_name" \
      -o jsonpath='{.items[0].metadata.name}' 2>>"$log_file" || true)"
    if [ -n "$bundledeployment_namespace" ] && [ -n "$bundledeployment_name" ]; then
      break
    fi
    bundledeployment_lookup_attempts=$((bundledeployment_lookup_attempts + 1))
    sleep 1
  done

  if [ -z "$bundledeployment_namespace" ] || [ -z "$bundledeployment_name" ]; then
    record_failure_and_maybe_abort "Fleet BundleDeployment not found ($app)" \
      "No BundleDeployment appeared for bundle $bundle_name within 10 minutes."
    return 1
  fi

  desired_deployment_id="$(kubectl -n "$bundledeployment_namespace" get bundledeployment "$bundledeployment_name" \
    --kubeconfig "$kubeconfig_path" -o jsonpath='{.spec.deploymentID}')"

  # Do not consume the previous deployment just because it is still exposed
  # while Fleet is processing the forced Bundle generation.  Wait until Fleet
  # publishes a different deployment ID before evaluating readiness/errors.
  if [ -n "$previous_applied_deployment_id" ]; then
    bundledeployment_wait_attempts=0
    while [ "$desired_deployment_id" = "$previous_applied_deployment_id" ] && \
      [ "$bundledeployment_wait_attempts" -lt 120 ]; do
      sleep 1
      current_desired_deployment_id="$(kubectl -n "$bundledeployment_namespace" get bundledeployment "$bundledeployment_name" \
        --kubeconfig "$kubeconfig_path" -o jsonpath='{.spec.deploymentID}' 2>>"$log_file" || true)"
      [ -n "$current_desired_deployment_id" ] && desired_deployment_id="$current_desired_deployment_id"
      bundledeployment_wait_attempts=$((bundledeployment_wait_attempts + 1))
    done
  fi

  if [ "$desired_deployment_id" = "$previous_applied_deployment_id" ]; then
    record_failure_and_maybe_abort "Fleet did not create a new deployment ($app)" \
      "Fleet kept the previous deployment ID after the reconciliation request."
    return 1
  fi

  log "$app: waiting for Fleet BundleDeployment $bundledeployment_name to apply deployment $desired_deployment_id" \
    "$(color_blue "$app"): waiting for Fleet BundleDeployment to apply the new deployment"

  # `kubectl wait` cannot express "wait for appliedDeploymentID unless the
  # BundleDeployment has already failed".  Poll both states so a failed Helm
  # revision is reported immediately instead of leaving the script frozen for
  # ten minutes.
  deployment_deadline=$((SECONDS + 600))
  next_progress_at=$SECONDS
  while [ "$SECONDS" -lt "$deployment_deadline" ]; do
    applied_deployment_id="$(timeout 20s kubectl --request-timeout=15s -n "$bundledeployment_namespace" get bundledeployment "$bundledeployment_name" \
      --kubeconfig "$kubeconfig_path" -o jsonpath='{.status.appliedDeploymentID}' 2>>"$log_file" || true)"
    bundledeployment_ready="$(timeout 20s kubectl --request-timeout=15s -n "$bundledeployment_namespace" get bundledeployment "$bundledeployment_name" \
      --kubeconfig "$kubeconfig_path" -o jsonpath='{.status.ready}' 2>>"$log_file" || true)"
    failure_message="$(fleet_bundledeployment_failure_message "$bundledeployment_namespace" "$bundledeployment_name" "$reconciliation_started_at")"

    # Fleet can leave the previous deployment applied while Helm has already
    # failed the new revision, without publishing Ready=False on the
    # BundleDeployment. Detect that state directly so this loop cannot wait
    # indefinitely for an appliedDeploymentID that will never arrive.
    helm_status_json="$(timeout 20s helm status "$app" --kubeconfig "$kubeconfig_path" \
      --namespace "$namespace" -o json 2>>"$log_file" || true)"
    helm_status="$(printf '%s' "$helm_status_json" | jq -r '.info.status // empty' 2>>"$log_file" || true)"
    current_helm_revision="$(printf '%s' "$helm_status_json" | jq -r '.version // 0' 2>>"$log_file" || true)"
    [[ "$current_helm_revision" =~ ^[0-9]+$ ]] || current_helm_revision=0
    if [ "$SECONDS" -ge "$next_progress_at" ]; then
      log "$app: Fleet apply pending: desired=$desired_deployment_id applied=${applied_deployment_id:-unknown} ready=${bundledeployment_ready:-unknown} helm=${helm_status:-unavailable} revision=$current_helm_revision"
      next_progress_at=$((SECONDS + 30))
    fi
    # A failed revision predating this reconciliation is not its outcome.
    # Wait for Fleet to create a newer Helm revision before reporting failure.
    if [ "$helm_status" = "failed" ] && [ "$current_helm_revision" -gt "$previous_helm_revision" ]; then
      helm_failure_message="$(helm_release_failure_message "$app" "$namespace")"
      if [ -z "$helm_failure_message" ]; then
        helm_failure_message="No Helm failure description was reported. See $log_file for command output."
      fi
      log "$app: Helm revision $current_helm_revision is failed (baseline=$previous_helm_revision): $helm_failure_message" \
        "$(color_blue "$app"): Helm release failed: $helm_failure_message"
      if [ "$fleet_repair_attempted" -eq 0 ]; then
        fleet_repair_attempted=1
        local repair_approved=0
        if [ "$app" = cloudcasa ] && [ "$namespace" = cloudcasa-io ] && \
          [[ "$helm_failure_message" == *'conflict with "cloudcasa-kubeagent-manager"'* ||
             "$helm_failure_message" == *'conflicts with "cloudcasa-kubeagent-manager"'* ]]; then
          repair_cloudcasa_field_ownership && repair_approved=1
        elif ask_attempt_fleet_repair "$app" "$namespace"; then
          repair_approved=1
        fi
        if [ "$repair_approved" -eq 1 ]; then
          # Fleet may create another failed revision while the repair dialog
          # is open. Baseline the latest revision after repair, not the one
          # observed before asking the user.
          previous_helm_revision="$(timeout 20s helm status "$app" \
            --kubeconfig "$kubeconfig_path" --namespace "$namespace" -o json \
            2>>"$log_file" | jq -er '.version | select(type == "number")' \
            2>>"$log_file")" || {
            log "$app: could not establish post-repair Helm revision; refusing an ambiguous retry"
            return 1
          }
          log "$app: recovery baseline Helm revision=$previous_helm_revision; waiting for a newer revision"
          deployment_deadline=$((SECONDS + 600))
          reconciliation_started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
          if request_fleet_reconciliation_retry \
            "$bundle_name" "$bundledeployment_namespace" "$bundledeployment_name" "$desired_deployment_id"; then
            log "$app: Fleet published a new deployment after the requested repair; resuming readiness checks" \
              "$(color_blue "$app"): Fleet retry started; resuming readiness checks"
            continue
          fi
          log "$app: requested Fleet repair did not start successfully"
        fi
      fi
      record_failure_and_maybe_abort "Helm upgrade failed ($app)" \
        "Helm release $namespace/$app is failed while Fleet still reports the previous deployment as applied.\n\nHelm reason: $helm_failure_message"
      return 1
    fi

    # Ignore a failure belonging to the deployment that existed before this
    # run.  Fleet may briefly expose that status while creating the new one.
    if [ -n "$failure_message" ] && \
      { [ -z "$previous_applied_deployment_id" ] || \
        [ "$desired_deployment_id" != "$previous_applied_deployment_id" ]; }; then
      if [[ "$failure_message" == *"dependent bundle(s) are not ready"* ]] && \
        [ "$dependency_repair_attempted" -eq 0 ]; then
        dependency_repair_attempted=1
        # Ask explicitly even in --yes mode: this is a separate repair action.
        if dialog --clear --begin 2 10 --defaultno \
          --title "Attempt Fleet dependency recovery ($app)?" \
          --yesno "Fleet reports:\n$failure_message\n\nReapply this app's local Fleet configuration and request one fresh reconciliation? This can clear an obsolete dependency after a storage migration. Dependencies still declared locally must be repaired separately.\n\nThis action does not edit storage definitions or delete PVCs. Attempt recovery?" 22 110; then
          log "$app: user approved Fleet dependency recovery; reapplying local bundle"
          if apply_fleet_bundle_with_repair "$app" --kubeconfig "$kubeconfig_path" \
            --namespace fleet-local "${native_apply_labels[@]}" \
            "$bundle_name" "$relative_fleet_dir" && \
            request_fleet_reconciliation_retry "$bundle_name" "$bundledeployment_namespace" \
              "$bundledeployment_name" "$desired_deployment_id"; then
            reconciliation_started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
            deployment_deadline=$((SECONDS + 600))
            log "$app: dependency recovery requested; checking the new deployment"
            continue
          fi
          log "$app: Fleet dependency recovery did not start successfully"
        else
          log "$app: user declined Fleet dependency recovery"
        fi
      fi
      log "$app: Fleet BundleDeployment reported failure: $failure_message" \
        "$(color_blue "$app"): Fleet Helm reconciliation failed"
      record_failure_and_maybe_abort "Fleet Helm reconciliation failed ($app)" \
        "BundleDeployment $bundledeployment_name reported:\n$failure_message"
      return 1
    fi

    if [ "$applied_deployment_id" = "$desired_deployment_id" ]; then
      break
    fi

    bundledeployment_wait_attempts=$((bundledeployment_wait_attempts + 1))
    sleep 1
  done

  if [ "$applied_deployment_id" != "$desired_deployment_id" ]; then
    log "$app: Fleet apply deadline exceeded; collecting BundleDeployment, Helm and pod diagnostics"
    timeout 20s kubectl --request-timeout=15s -n "$bundledeployment_namespace" get bundledeployment "$bundledeployment_name" -o yaml \
      --kubeconfig "$kubeconfig_path" >>"$log_file" 2>&1 || true
    timeout 20s helm status "$app" --namespace "$namespace" --kubeconfig "$kubeconfig_path" >>"$log_file" 2>&1 || true
    timeout 20s kubectl --request-timeout=15s -n "$namespace" get pods -o wide \
      --kubeconfig "$kubeconfig_path" >>"$log_file" 2>&1 || true
    record_failure_and_maybe_abort "Fleet BundleDeployment did not apply ($app)" \
      "Fleet did not apply deployment $desired_deployment_id for bundle $bundle_name."
    return 1
  fi

  log "$app: waiting for Fleet BundleDeployment $bundledeployment_name to become Ready" \
    "$(color_blue "$app"): waiting for Fleet BundleDeployment to become Ready"
  bundledeployment_wait_attempts=0
  while [ "$bundledeployment_wait_attempts" -lt 600 ]; do
    bundledeployment_ready="$(kubectl -n "$bundledeployment_namespace" get bundledeployment "$bundledeployment_name" \
      --kubeconfig "$kubeconfig_path" -o jsonpath='{.status.ready}' 2>>"$log_file" || true)"
    failure_message="$(fleet_bundledeployment_failure_message "$bundledeployment_namespace" "$bundledeployment_name" "$reconciliation_started_at")"
    if [ -n "$failure_message" ] && \
      { [ -z "$previous_applied_deployment_id" ] || \
        [ "$desired_deployment_id" != "$previous_applied_deployment_id" ]; }; then
      log "$app: Fleet BundleDeployment reported failure: $failure_message" \
        "$(color_blue "$app"): Fleet Helm reconciliation failed"
      record_failure_and_maybe_abort "Fleet Helm reconciliation failed ($app)" \
        "BundleDeployment $bundledeployment_name reported:\n$failure_message"
      return 1
    fi
    if [ "$bundledeployment_ready" = "true" ]; then
      break
    fi
    bundledeployment_wait_attempts=$((bundledeployment_wait_attempts + 1))
    sleep 1
  done

  # Fleet v0.16 can retain a stale Ready=False snapshot when the Deployment
  # becomes available immediately after the BundleDeployment is applied.  A
  # restarted local agent rebuilds its resource watches and reports the
  # current workload status upstream.  Retry once before declaring failure.
  if [ "$bundledeployment_ready" != "true" ] && [ "$fleet_status_retry" -eq 0 ]; then
    fleet_status_retry=1
    log "$app: Fleet readiness timed out; restarting local Fleet agent before retry" \
      "$(color_blue "$app"): Fleet readiness timed out; restarting Fleet agent and retrying"
    if ! kubectl -n cattle-fleet-local-system rollout restart deployment/fleet-agent \
      --kubeconfig "$kubeconfig_path" >>"$log_file" 2>&1; then
      record_failure_and_maybe_abort "Fleet agent restart failed ($app)" \
        "Could not restart deployment fleet-agent in namespace cattle-fleet-local-system."
      return 1
    fi
    if ! kubectl -n cattle-fleet-local-system rollout status deployment/fleet-agent \
      --kubeconfig "$kubeconfig_path" --timeout=120s >>"$log_file" 2>&1; then
      record_failure_and_maybe_abort "Fleet agent did not restart ($app)" \
        "Fleet agent did not become Ready within 120 seconds."
      return 1
    fi

    log "$app: retrying Fleet BundleDeployment readiness after Fleet agent restart" \
      "$(color_blue "$app"): retrying Fleet BundleDeployment readiness"
    bundledeployment_wait_attempts=0
    bundledeployment_ready=""
    while [ "$bundledeployment_wait_attempts" -lt 600 ]; do
      bundledeployment_ready="$(kubectl -n "$bundledeployment_namespace" get bundledeployment "$bundledeployment_name" \
        --kubeconfig "$kubeconfig_path" -o jsonpath='{.status.ready}' 2>>"$log_file" || true)"
      failure_message="$(fleet_bundledeployment_failure_message "$bundledeployment_namespace" "$bundledeployment_name" "$reconciliation_started_at")"
      if [ -n "$failure_message" ] && \
        { [ -z "$previous_applied_deployment_id" ] || \
          [ "$desired_deployment_id" != "$previous_applied_deployment_id" ]; }; then
        log "$app: Fleet BundleDeployment reported failure after retry: $failure_message" \
          "$(color_blue "$app"): Fleet Helm reconciliation failed after retry"
        record_failure_and_maybe_abort "Fleet Helm reconciliation failed ($app)" \
          "BundleDeployment $bundledeployment_name reported:\n$failure_message"
        return 1
      fi
      if [ "$bundledeployment_ready" = "true" ]; then
        break
      fi
      bundledeployment_wait_attempts=$((bundledeployment_wait_attempts + 1))
      sleep 1
    done
  fi

  if [ "$bundledeployment_ready" != "true" ]; then
    record_failure_and_maybe_abort "Fleet BundleDeployment failed ($app)" \
      "Fleet BundleDeployment $bundledeployment_name did not become Ready."
    return 1
  fi

  # Read the counts separately: Fleet keeps BundleDeployment ready=true while
  # a resource has drifted ("modified"), so desiredReady != ready alone does
  # not mean that nothing is ready.
  local fleet_counts_json fleet_desired fleet_ready fleet_modified
  local fleet_not_ready fleet_missing fleet_modified_list fleet_counts_summary
  local drift_repair_attempted=0 drift_wait
  while :; do
    fleet_counts_json="$(timeout 20s kubectl --request-timeout=15s \
      -n "$bundledeployment_namespace" get bundledeployment "$bundledeployment_name" \
      --kubeconfig "$kubeconfig_path" -o json 2>>"$log_file" || true)"
    read -r fleet_desired fleet_ready fleet_modified fleet_not_ready fleet_missing < <(
      printf '%s' "$fleet_counts_json" | jq -r '.status.resourceCounts // {} |
        [.desiredReady // 0, .ready // 0, .modified // 0, .notReady // 0, .missing // 0] | @tsv' \
        2>>"$log_file" || echo "0 0 0 0 0")
    fleet_modified_list="$(printf '%s' "$fleet_counts_json" | jq -r '
      .status.modifiedStatus // [] | .[] |
      "\(.kind) \(.namespace // "-")/\(.name)\(if .missing then " (missing)" elif .delete then " (extra)" else " (modified)" end)"' \
      2>>"$log_file" || true)"
    fleet_counts_summary="desiredReady=${fleet_desired:-0} ready=${fleet_ready:-0} modified=${fleet_modified:-0} notReady=${fleet_not_ready:-0} missing=${fleet_missing:-0}"

    if [ "${fleet_desired:-0}" -gt 0 ] && [ "$fleet_desired" = "$fleet_ready" ]; then
      break
    fi

    # Drift-only state: every resource exists and none is NotReady, but some
    # differ from the chart. Offer one Fleet redeploy to reset them.
    if [ "$drift_repair_attempted" -eq 0 ] && [ "${fleet_modified:-0}" -gt 0 ] && \
      [ "${fleet_not_ready:-0}" -eq 0 ] && [ "${fleet_missing:-0}" -eq 0 ]; then
      drift_repair_attempted=1
      local drift_note=""
      if [ "$app" = cloudcasa ]; then
        drift_note="\n\nCloudCasa's agent updates its own image (e.g. 3.1.0-prod -> 3.1.0-production.NN). A redeploy resets it to the chart image and restarts the agent, which can interrupt an active backup, and the agent may update itself again shortly afterwards. To stop this permanently, add a diff.comparePatches entry for the image and KUBEMOVER_IMAGE fields to fleet/infrastructure/cloudcasa/fleet.yaml."
      fi
      log "$app: Fleet reports drifted resources ($fleet_counts_summary): $(printf '%s' "$fleet_modified_list" | tr '\n' ';')"
      # Ask explicitly even in --yes mode: this is a separate repair action.
      if dialog --clear --begin 2 10 --defaultno \
        --title "Attempt Fleet drift repair ($app)?" \
        --yes-label "Attempt repair" --no-label "Skip repair" \
        --yesno "Bundle $bundle_name is applied, but Fleet reports resources that differ from the chart:\n\n${fleet_modified_list:-unknown}\n\nCounts: $fleet_counts_summary\n\nRequest one fresh Fleet reconciliation (forceSyncGeneration) so Helm reapplies the chart and resets the drifted fields?${drift_note}\n\nNo PVCs or storage definitions are changed. Attempt repair?" 26 116; then
        log "$app: user approved Fleet drift repair"
        if request_fleet_reconciliation_retry "$bundle_name" "$bundledeployment_namespace" \
          "$bundledeployment_name" "$desired_deployment_id"; then
          log "$app: Fleet drift repair requested; waiting for deployment $desired_deployment_id" \
            "$(color_blue "$app"): Fleet drift repair requested; waiting for reapply"
          drift_wait=0
          while [ "$drift_wait" -lt 300 ]; do
            applied_deployment_id="$(timeout 20s kubectl --request-timeout=15s -n "$bundledeployment_namespace" \
              get bundledeployment "$bundledeployment_name" --kubeconfig "$kubeconfig_path" \
              -o jsonpath='{.status.appliedDeploymentID} {.status.ready}' 2>>"$log_file" || true)"
            [ "$applied_deployment_id" = "$desired_deployment_id true" ] && break
            drift_wait=$((drift_wait + 2))
            sleep 2
          done
          # Give the agent a moment to refresh resourceCounts after apply.
          sleep 10
          continue
        fi
        log "$app: Fleet drift repair did not start successfully"
      else
        log "$app: user declined Fleet drift repair"
      fi
    fi

    if [ "${fleet_modified:-0}" -gt 0 ] && [ "${fleet_not_ready:-0}" -eq 0 ] && \
      [ "${fleet_missing:-0}" -eq 0 ]; then
      record_failure_and_maybe_abort "Fleet resources drifted ($app)" \
        "Bundle $bundle_name has resources that differ from the chart.\n\n${fleet_modified_list:-unknown}\n\nCounts: $fleet_counts_summary"
    else
      record_failure_and_maybe_abort "Fleet resources not ready ($app)" \
        "Bundle $bundle_name reported resource counts: $fleet_counts_summary.${fleet_modified_list:+\n\n$fleet_modified_list}"
    fi
    return 1
  done

  log "$app: Fleet bundle $bundle_name is Ready" \
    "$(color_blue "$app"): Fleet bundle $bundle_name is Ready"
  return 0
}

verify_fleet_upgrade_version() {
  local app="$1"
  local namespace="$2"
  local target_version="$3"
  local installed_chart
  local installed_version
  local bundle_name
  local fleet_version

  installed_chart="$(helm list \
    --kubeconfig "$kubeconfig_path" \
    --namespace "$namespace" \
    --filter "^${app}$" 2>>"$log_file" | awk 'NR > 1 && $1 != "" {print $9; exit}')"
  installed_version="${installed_chart#"$app-"}"
  bundle_name="$(fleet_bundle_name_for_app "$app" 2>/dev/null || true)"
  fleet_version=""
  if [ -n "$bundle_name" ]; then
    fleet_version="$(kubectl get bundle "$bundle_name" \
      --kubeconfig "$kubeconfig_path" --namespace fleet-local \
      -o jsonpath='{.spec.helm.version}' 2>>"$log_file" || true)"
  fi

  log "$app: verifying live versions after Fleet upgrade (helm=${installed_version:-unknown}, fleet=${fleet_version:-unknown}, target=$target_version)" \
    "$(color_blue "$app"): verifying live Helm and Fleet versions"
  if [ "$installed_version" != "$target_version" ] || [ "$fleet_version" != "$target_version" ]; then
    record_failure_and_maybe_abort "Fleet upgrade version mismatch ($app)" \
      "Requested chart version $target_version, but live Helm is ${installed_version:-unknown} and Fleet Bundle is ${fleet_version:-unknown}. The upgrade may have been reverted; the Fleet file was not staged."
    return 1
  fi
  return 0
}

stage_fleet_file() {
  local fleet_file="$1"
  local relative_file

  relative_file="${fleet_file#"$homelab_root/"}"
  if ! git -C "$homelab_root" add -- "$relative_file" >>"$log_file" 2>&1; then
    record_failure_and_maybe_abort "Unable to stage Fleet file" "git add failed for $relative_file."
    return 1
  fi
  log "Staged $relative_file after successful Fleet and ingress checks."
  return 0
}

cleanup() {
  # Ensure cursor/screen is restored if dialog was used.
  if command -v dialog >/dev/null 2>&1; then
    dialog --clear >/dev/null 2>&1 || true
  fi
}

print_colored_summary() {
  printf '%b\n' "$(color_blue "Run Summary")"
  printf 'Discovered: %b\n' "$(color_count "$total_discovered_count" "$blue")"
  printf 'Excluded: %b\n' "$(color_count "$excluded_count" "$yellow")"
  printf 'Up-to-date: %b\n' "$(color_count "$up_to_date_count" "$green")"
  printf 'Skipped: %b\n' "$(color_count "$skipped_count" "$yellow")"
  printf 'Updated: %b\n' "$(color_count "$updated_count" "$green")"
  printf 'Staged Fleet files: %b\n' "$(color_count "$staged_fleet_count" "$green")"

  if [ "$dry_run" -eq 1 ]; then
    printf 'Dry-run approved (not executed): %b\n' "$(color_count "$dry_run_approved_count" "$yellow")"
  fi

  printf 'Failed apps: %b\n' "$(color_zero_ok_count "$failed_apps_count")"
  printf 'Failure events: %b\n' "$(color_zero_ok_count "$failure_events_count")"
  if [ "${#version_check_error_apps[@]}" -gt 0 ]; then
    printf 'Helm version check errors: %s\n' "${version_check_error_apps[*]}"
  fi
  printf 'Exit status: %b\n' "$(color_bash_return_code "$exit_status")"
  printf 'Log file: %s\n' "$log_file"
}

show_summary_modal() {
  local summary
  summary="Discovered: $total_discovered_count
Excluded: $excluded_count
Up-to-date: $up_to_date_count
Skipped: $skipped_count
Updated: $updated_count
Staged Fleet files: $staged_fleet_count"

  if [ "$dry_run" -eq 1 ]; then
    summary="${summary}
Dry-run approved (not executed): $dry_run_approved_count"
  fi

  summary="${summary}
Failed apps: $failed_apps_count
Failure events: $failure_events_count"

  if [ "${#version_check_error_apps[@]}" -gt 0 ]; then
    summary="${summary}
Helm version check errors: ${version_check_error_apps[*]}"
  fi

  summary="${summary}
Exit status: $exit_status
Log file: $log_file"

  if [ "$updated_count" -eq 0 ]; then
    local ts
    ts="$(date +"%Y-%m-%d %H:%M:%S")"
    cleanup
    trap - EXIT
    print_colored_summary
    printf '[%s] Summary: discovered=%s excluded=%s up_to_date=%s skipped=%s updated=%s dry_run_approved=%s failed_apps=%s failure_events=%s exit_status=%s\n' "$ts" "$total_discovered_count" "$excluded_count" "$up_to_date_count" "$skipped_count" "$updated_count" "$dry_run_approved_count" "$failed_apps_count" "$failure_events_count" "$exit_status" >>"$log_file"
    return 0
  fi

  if [ "$yes_mode" -eq 1 ]; then
    log "Summary: discovered=$total_discovered_count excluded=$excluded_count up_to_date=$up_to_date_count skipped=$skipped_count updated=$updated_count dry_run_approved=$dry_run_approved_count failed_apps=$failed_apps_count failure_events=$failure_events_count exit_status=$exit_status"
    return 0
  fi

  dialog \
    --clear \
    --title "Run Summary" \
    --msgbox "$summary" 16 100
}

show_app_modal() {
  local app="$1"
  local namespace="$2"
  local local_version="$3"
  local target_version="$4"
  local summary_title="$5"
  local summary_body="$6"
  local dialog_color_flag=()
  local dialog_color_flag_yesno=()
  local app_display

  if [ "$yes_mode" -eq 1 ]; then
    log "AUTO: approving upgrade for $app"
    return 0
  fi

  if [ "$dialog_colors_supported" -eq 1 ]; then
    dialog_color_flag=(--colors)
    dialog_color_flag_yesno=(--colors)
  fi

  app_display="$(dialog_color_app "$app")"
  summary_body="${summary_body}\\Z0"

  dialog \
    "${dialog_color_flag[@]}" \
    --clear \
    "${dialog_color_flag_yesno[@]}" \
    --begin 2 10 \
    --title "Upgrade $app?" \
    --defaultno \
    --yesno "App: $app_display\nNamespace: $namespace\nInstalled chart: ${local_version:-unknown}\nTarget chart: ${target_version:-unknown}\n${summary_title}:\n$summary_body\\Z0\n\nProceed with upgrade?" 24 120
}

show_postcheck_log_review_modal() {
  local app="$1"
  local namespace="$2"
  local summary_title="$3"
  local summary_body="$4"

  if [ "$yes_mode" -eq 1 ]; then
    log "AUTO: accepting postcheck pod log review for $app"
    return 0
  fi

  dialog \
    --clear \
    --begin 2 10 \
    --title "Post-upgrade review for $app" \
    --yes-label "Looks good" \
    --no-label "Does not" \
    --defaultno \
    --yesno "Namespace: $namespace\n${summary_title}:\n$summary_body\n\nDo these logs look good after the upgrade?" 24 120
}

ask_on_failure() {
  local title="$1"
  local message="$2"

  if [ "$yes_mode" -eq 1 ]; then
    log "AUTO: failure encountered, continuing because --yes is set ($title)"
    return 0
  fi

  dialog \
    --clear \
    --begin 2 10 \
    --title "$title" \
    --defaultno \
    --yesno "$message\n\nContinue with next app?" 20 120
}

last_deployed_revision() {
  local app="$1"
  local namespace="$2"

  helm history "$app" \
    --kubeconfig "$kubeconfig_path" \
    --max 20 \
    --namespace "$namespace" 2>>"$log_file" \
    | awk 'NR > 1 && $1 ~ /^[0-9]+$/ && $3 == "deployed" { revision = $1 } END { if (revision != "") print revision }'
}

ask_on_helm_upgrade_failure() {
  local app="$1"
  local namespace="$2"
  local revision="$3"
  local choice_file
  local choice

  if [ "$yes_mode" -eq 1 ] || [ -z "$revision" ]; then
    if [ -z "$revision" ]; then
      log "$app: no previously deployed Helm revision found; rollback is unavailable"
    fi
    ask_on_failure "Helm upgrade failed ($app)" "helm upgrade command failed (return_code=1)."
    return $?
  fi

  choice_file="$(mktemp "${TMPDIR:-/tmp}/helm-upgrade-failure-choice.XXXXXX")"
  if dialog \
    --clear \
    --begin 2 10 \
    --title "Helm upgrade failed ($app)" \
    --default-item 1 \
    --menu "helm upgrade command failed (return_code=1).\n\nA previously deployed revision is available. What should happen next?" 16 110 3 \
    1 "Rollback to revision $revision" \
    2 "Continue with next app" \
    3 "Abort run" \
    2>"$choice_file"; then
    choice="$(<"$choice_file")"
  else
    choice=3
  fi
  rm -f "$choice_file"

  case "$choice" in
    1)
      log "$app: rolling back to previously deployed Helm revision $revision" "$(color_blue "$app"): rolling back to previously deployed Helm revision $revision"
      if helm rollback \
        --kubeconfig "$kubeconfig_path" \
        --namespace "$namespace" \
        --timeout=10m0s \
        --wait=true \
        "$app" "$revision" >>"$log_file" 2>&1; then
        log "$app: rollback to Helm revision $revision completed (return_code=0)" "$(color_blue "$app"): rollback to Helm revision $revision completed (return_code=$(color_bash_return_code 0))"
        return 0
      fi
      log "$app: rollback to Helm revision $revision failed (return_code=1)" "$(color_blue "$app"): rollback to Helm revision $revision failed (return_code=$(color_bash_return_code 1))"
      if ask_on_failure "Helm rollback failed ($app)" "helm rollback to revision $revision failed (return_code=1)."; then
        log "User chose to continue after rollback failure."
        return 0
      fi
      log "User aborted run after rollback failure."
      cleanup
      exit 1
      ;;
    2)
      log "User chose to continue after Helm upgrade failure."
      return 0
      ;;
    *)
      log "User aborted run after Helm upgrade failure."
      cleanup
      exit 1
      ;;
  esac
}

host_uses_insecure() {
  local host="$1"
  printf '%s\n' "$insecure_hosts" | grep -F -x -q "$host"
}

add_insecure_host() {
  local host="$1"
  if ! host_uses_insecure "$host"; then
    if [ -n "$insecure_hosts" ]; then
      insecure_hosts="${insecure_hosts}
$host"
    else
      insecure_hosts="$host"
    fi
  fi
}

curl_error_looks_like_certificate_validity_issue() {
  local curl_return_code="$1"
  local curl_error_output="$2"
  if printf '%s' "$curl_error_output" | grep -E -i -q 'certificate has expired|certificate.*not yet valid|SSL certificate problem|peer certificate|certificate verify failed|x509:|tlsv1|ssl routines|ssl.*certificate|tls.*certificate'; then
    return 0
  fi

  case "$curl_return_code" in
    35|51|58|59|60|77|83|90)
      return 0
      ;;
  esac

  if [ "$curl_return_code" = "60" ]; then
    return 0
  fi

  return 1
}

ask_retry_curl_with_insecure() {
  local scheme="$1"
  local host="$2"
  local curl_error_output="$3"
  local path="${4:-/}"

  if [ "$yes_mode" -eq 1 ]; then
    log "AUTO: not retrying curl with --insecure for $host because --yes is set"
    return 1
  fi

  dialog \
    --clear \
    --begin 2 10 \
    --title "Certificate Validity Issue" \
    --defaultno \
    --yesno "curl failed for ${scheme}://${host}${path} and it looks like the certificate may be out of date.\n\nError:\n$curl_error_output\n\nRetry this host with --insecure to skip certificate validity checks?" 18 100
}

run_curl_http_check() {
  local scheme="$1"
  local host="$2"
  local path="${3:-/}"
  local curl_error_file
  local curl_args=(-L -sS -o /dev/null -w "%{http_code}")

  curl_http_code="000"
  curl_return_code=1
  curl_used_insecure=0
  curl_error_output=""

  if host_uses_insecure "$host"; then
    curl_args+=(--insecure)
    curl_used_insecure=1
  fi

  curl_error_file="$(mktemp)"
  if curl_http_code="$(curl "${curl_args[@]}" "${scheme}://${host}${path}" 2>"$curl_error_file")"; then
    curl_return_code=0
    curl_error_output=""
    rm -f "$curl_error_file"
    return 0
  fi

  curl_return_code=$?
  curl_error_output="$(cat "$curl_error_file")"
  rm -f "$curl_error_file"

  if [ "$curl_used_insecure" -eq 0 ] && curl_error_looks_like_certificate_validity_issue "$curl_return_code" "$curl_error_output"; then
    log "curl for ${scheme}://${host}${path} failed due to certificate validity issue (return_code=$curl_return_code)"
    if ask_retry_curl_with_insecure "$scheme" "$host" "$curl_error_output" "$path"; then
      add_insecure_host "$host"
      curl_used_insecure=1
      curl_error_file="$(mktemp)"
      if curl_http_code="$(curl -L -sS --insecure -o /dev/null -w "%{http_code}" "${scheme}://${host}${path}" 2>"$curl_error_file")"; then
        curl_return_code=0
        curl_error_output=""
        rm -f "$curl_error_file"
        log "curl for ${scheme}://${host}${path} succeeded after retry with --insecure"
        return 0
      fi
      curl_return_code=$?
      curl_error_output="$(cat "$curl_error_file")"
      rm -f "$curl_error_file"
    fi
  fi

  return 1
}

record_failure_and_maybe_abort() {
  local title="$1"
  local message="$2"
  exit_status=1
  failure_events_count=$((failure_events_count + 1))
  if [ -n "$current_app" ] && [ "$current_app_failed" -eq 0 ]; then
    current_app_failed=1
    failed_apps_count=$((failed_apps_count + 1))
  fi
  log "FAILURE: $title - $message"
  if ask_on_failure "$title" "$message"; then
    log "User chose to continue after failure."
    return 0
  fi
  log "User aborted run after failure."
  cleanup
  exit 1
}

should_exclude_app() {
  local app="$1"
  local pattern
  for pattern in "${exclude_patterns[@]}"; do
    if [[ "$app" =~ $pattern ]]; then
      return 0
    fi
  done
  return 1
}

list_candidate_apps() {
  helm ls --all-namespaces --kubeconfig "$kubeconfig_path" \
    | awk 'NR>1 {print $1}'
}

get_namespace_for_app() {
  local app="$1"
  helm ls --all-namespaces --kubeconfig "$kubeconfig_path" \
    | awk -v app="$app" '$1==app {print $2; exit}'
}

get_local_chart_version() {
  local app="$1"
  helm ls --all-namespaces --kubeconfig "$kubeconfig_path" \
    | awk -v app="$app" '$1==app {print $9; exit}'
}

get_ingress_targets() {
  local namespace="$1"
  local app="$2"
  local ingress
  local entrypoints
  local hosts
  local tls_hosts
  local host
  local scheme
  local check_path

  while IFS= read -r ingress; do
    [ -z "$ingress" ] && continue
    entrypoints="$(kubectl get "$ingress" -n "$namespace" --kubeconfig "$kubeconfig_path" \
      -o jsonpath='{.metadata.annotations.traefik\.ingress\.kubernetes\.io/router\.entrypoints}' 2>/dev/null || true)"
    check_path="$(kubectl get "$ingress" -n "$namespace" --kubeconfig "$kubeconfig_path" \
      -o jsonpath='{.metadata.annotations.helm-upgrade\.bnowakowski\.pl/check-path}' 2>/dev/null || true)"
    check_path="${check_path:-/}"
    hosts="$(kubectl get "$ingress" -n "$namespace" --kubeconfig "$kubeconfig_path" \
      -o jsonpath='{range .spec.rules[*]}{.host}{"\n"}{end}' 2>/dev/null || true)"
    tls_hosts="$(kubectl get "$ingress" -n "$namespace" --kubeconfig "$kubeconfig_path" \
      -o jsonpath='{range .spec.tls[*].hosts[*]}{.}{"\n"}{end}' 2>/dev/null || true)"

    while IFS= read -r host; do
      [ -z "$host" ] && continue
      if printf '%s' "$entrypoints" | grep -F -q 'websecure'; then
        scheme="https"
      elif printf '%s' "$entrypoints" | grep -F -q 'web'; then
        scheme="http"
      elif printf '%s\n' "$tls_hosts" | grep -F -x -q "$host"; then
        scheme="https"
      else
        scheme="http"
      fi
      printf '%s %s %s\n' "$scheme" "$host" "$check_path"
    done <<< "$hosts"
  done < <(kubectl get ingress -n "$namespace" --kubeconfig "$kubeconfig_path" \
    -o json 2>/dev/null | jq -r --arg app "$app" '
      .items[]
      | select((.metadata.annotations["meta.helm.sh/release-name"]
          // .metadata.labels["app.kubernetes.io/instance"]) == $app)
      | "ingress/" + .metadata.name
    ' | sort -u)
}

# Follow controller ownership because some charts omit instance labels on pods.
get_release_resources() {
  local app="$1"
  local namespace="$2"
  local kinds="$3"
  local resources
  resources="$(kubectl get pods,deployment,statefulset,daemonset,replicaset,job,cronjob \
    -n "$namespace" --kubeconfig "$kubeconfig_path" -o json)" || return 1
  jq -r --arg app "$app" --arg kinds "$kinds" '
    .items as $items
    | def owned:
        (.metadata.annotations["meta.helm.sh/release-name"] == $app)
        or (.metadata.labels["app.kubernetes.io/instance"] == $app)
        or (.metadata.labels.release == $app)
        or any(.metadata.ownerReferences[]?;
          .uid as $uid | any($items[];
            .metadata.uid == $uid and owned));
      $items[]
      | select(.kind as $kind | $kinds | split(",") | index($kind))
      | select(owned)
      | if .kind == "Pod" then .metadata.name
        else (.kind | ascii_downcase) + "/" + .metadata.name end
  ' <<< "$resources" | sort -u
}

get_release_pods() {
  get_release_resources "$1" "$2" "Pod"
}

collect_no_ingress_pod_log_summary() {
  local app="$1"
  local namespace="$2"
  local phase="$3"
  local pods
  local summary=""
  local pod
  local pod_logs
  local line

  check_summary_title="Pod logs (last 10 lines per pod)"
  check_summary_kind="pod-logs"

  if ! pods="$(get_release_pods "$app" "$namespace")"; then
    check_summary_body="Unable to discover release pods in namespace $namespace."
    log "$app [$phase]: $check_summary_body"
    return 1
  fi
  if [ -z "$pods" ]; then
    check_summary_body="No pods found belonging to Helm release $app; log review is unavailable."
    log "$app [$phase]: no ingress hosts found and no release pods were found for pod log review" "$(color_blue "$app") [$phase]: no ingress hosts found and no release pods were found for pod log review"
    return 1
  fi

  while IFS= read -r pod; do
    [ -z "$pod" ] && continue
    log "$app [$phase]: collecting last 10 log lines for pod $pod" "$(color_blue "$app") [$phase]: collecting last 10 log lines for pod $pod"
    pod_logs="$(kubectl logs -n "$namespace" --kubeconfig "$kubeconfig_path" --all-containers=true --tail=10 "$pod" 2>&1 || true)"
    summary="${summary}Pod: $pod"$'\n'
    if [ -n "$pod_logs" ]; then
      while IFS= read -r line; do
        summary="${summary}  $line"$'\n'
      done <<<"$pod_logs"
    else
      summary="${summary}  <no log output>"$'\n'
    fi
    summary="${summary}"$'\n'
  done <<EOF_PODS
$pods
EOF_PODS

  check_summary_body="${summary%$'\n'}"
  log "$app [$phase]: no ingress hosts found, using pod log review instead" "$(color_blue "$app") [$phase]: no ingress hosts found, using pod log review instead"
  return 0
}

check_ingress_http_codes() {
  local app="$1"
  local namespace="$2"
  local phase="$3"

  check_summary_title="HTTP checks"
  check_summary_body="No ingress hosts found"
  check_summary_kind="http"
  check_failed_ingress_summary=""

  local ingress_targets
  ingress_targets="$(get_ingress_targets "$namespace" "$app" | sort -u)"

  if [ -z "$ingress_targets" ]; then
    collect_no_ingress_pod_log_summary "$app" "$namespace" "$phase"
    return $?
  fi

  local fail=0
  local summary=""
  local scheme
  local host
  local path
  local http_code
  local dialog_code
  local insecure_suffix
  local failed_summary=""
  while IFS=' ' read -r scheme host path; do
    [ -z "$scheme" ] && continue
    [ -z "$host" ] && continue
    path="${path:-/}"
    run_curl_http_check "$scheme" "$host" "$path"
    http_code="$curl_http_code"
    dialog_code="$(dialog_color_http_code "$http_code")"
    insecure_suffix=""
    if [ "$curl_used_insecure" -eq 1 ]; then
      insecure_suffix=" (with --insecure)"
    fi
    summary="${summary}\\Z0${scheme}://${host}${path} -> ${dialog_code}${insecure_suffix}\\Z0"$'\n'
    log "$app [$phase]: ingress ${scheme}://${host}${path} returned $http_code${insecure_suffix}" "$(color_blue "$app") [$phase]: ingress ${scheme}://${host}${path} returned $(color_http_code "$http_code")${insecure_suffix}"
    if [ "$curl_return_code" != "0" ]; then
      log "$app [$phase]: curl failed for ${scheme}://${host}${path} (return_code=$curl_return_code) error=$curl_error_output" "$(color_blue "$app") [$phase]: curl failed for ${scheme}://${host}${path} (return_code=$(color_bash_return_code "$curl_return_code"))"
      failed_summary="${failed_summary}${scheme}://${host}${path} -> ${http_code} (curl failed, return code ${curl_return_code})"$'\n'
      fail=1
    elif [ "$http_code" != "200" ]; then
      failed_summary="${failed_summary}${scheme}://${host}${path} -> ${http_code}"$'\n'
      fail=1
    fi
  done <<EOF_HOSTS
$ingress_targets
EOF_HOSTS

  check_summary_body="${summary%$'\n'}"
  check_failed_ingress_summary="${failed_summary%$'\n'}"

  if [ "$fail" -eq 1 ]; then
    return 1
  fi
  return 0
}

check_rollout_ready() {
  local app="$1"
  local namespace="$2"
  local phase="$3"

  local resources
  resources="$(get_release_resources "$app" "$namespace" "Deployment,StatefulSet,DaemonSet")" || return 1

  if [ -z "$resources" ]; then
    log "$app [$phase]: no release-labeled deployment/statefulset resources found for app.kubernetes.io/instance=$app" "$(color_blue "$app") [$phase]: no release-labeled deployment/statefulset resources found for app.kubernetes.io/instance=$app"
    return 0
  fi

  local resource
  while IFS= read -r resource; do
    [ -z "$resource" ] && continue
    log "$app [$phase]: waiting for rollout status: $resource (timeout=$rollout_timeout)" "$(color_blue "$app") [$phase]: waiting for rollout status: $resource (timeout=$rollout_timeout)"
    if ! kubectl rollout status -n "$namespace" --kubeconfig "$kubeconfig_path" --timeout="$rollout_timeout" "$resource" >>"$log_file" 2>&1; then
      log "$app [$phase]: rollout failed for $resource (return_code=1)" "$(color_blue "$app") [$phase]: rollout failed for $resource (return_code=$(color_bash_return_code 1))"
      return 1
    fi
  done <<EOF_ROLLOUT
$resources
EOF_ROLLOUT

  log "$app [$phase]: rollout checks passed (return_code=0)" "$(color_blue "$app") [$phase]: rollout checks passed (return_code=$(color_bash_return_code 0))"
  return 0
}

# Diagnose known failures without writes, then ask separately from upgrade approval.
# Repairs always require confirmation, including in --yes mode.
maybe_repair_http_precheck() {
  local app="$1" namespace="$2"
  local repair_helper="$script_dir/lib/helm-http-repair.py"
  local repair_fleet_file repair_values repair_dir repair_plan repair_description
  local repair_version repair_status
  [ "$dry_run" -eq 0 ] || return 1
  case "$app" in radarr|prowlarr|sonarr|jenkins) ;; *) return 1 ;; esac
  [ -f "$repair_helper" ] && [ -x /usr/bin/python3 ] || return 1
  repair_fleet_file="$(fleet_file_for_app "$app" 2>/dev/null)" || return 1
  repair_values="$(dirname "$repair_fleet_file")/values.yaml"
  [ -f "$repair_values" ] || return 1
  repair_dir="$(mktemp -d "$log_dir/http-repair-${app}-XXXXXX")" || return 1
  chmod 700 "$repair_dir"
  repair_plan="$repair_dir/plan.json"
  if ! repair_description="$(/usr/bin/python3 "$repair_helper" \
      --app "$app" --namespace "$namespace" --values "$repair_values" \
      --kubeconfig "$kubeconfig_path" --plan "$repair_plan" 2>>"$log_file")"; then
    log "$app: HTTP repair diagnosis did not identify a supported repair"
    return 1
  fi
  repair_version="$(fleet_version_from_file "$repair_fleet_file")"
  if ! dialog --clear --begin 2 10 --defaultno \
      --title "Repair HTTP precheck ($app)?" \
      --yes-label "Apply repair" --no-label "Skip repair" \
      --yesno "$repair_description\n\nBack up and update:\n$repair_values\n\nApply only this Fleet bundle at configured chart version $repair_version, then validate its rollout and HTTPS checks. Values will be staged only after validation. Backups and the repair plan are under:\n$repair_dir\n\nApply this repair?" 25 114; then
    log "$app: user declined HTTP precheck repair"
    return 1
  fi
  log "$app: user approved HTTP precheck repair; plan=$repair_plan"
  if ! /usr/bin/python3 "$repair_helper" --apply "$repair_plan" >>"$log_file" 2>&1; then
    record_failure_and_maybe_abort "HTTP repair failed ($app)" \
      "Unable to update values safely. Inspect $log_file and $repair_dir."
    return 2
  fi
  if ! apply_fleet_bundle "$app" "$(dirname "$repair_fleet_file")" "$namespace" "$repair_version"; then
    log "$app: HTTP repair values retained for inspection; backup=$repair_dir/values-before.yaml"
    return 2
  fi
  if ! check_rollout_ready "$app" "$namespace" "HTTP repair" || \
     ! check_ingress_http_codes "$app" "$namespace" "HTTP repair"; then
    record_failure_and_maybe_abort "HTTP repair validation failed ($app)" \
      "Repair applied, but rollout/HTTP validation failed. Review $repair_dir and $log_file before retrying."
    return 2
  fi
  if ! /usr/bin/python3 "$repair_helper" --verify "$repair_plan" \
      --kubeconfig "$kubeconfig_path" >>"$log_file" 2>&1; then
    record_failure_and_maybe_abort "HTTP repair verification failed ($app)" \
      "The repair did not preserve the expected HTTPS/authentication behavior. See $log_file."
    return 2
  fi
  if ! stage_fleet_file "$repair_values"; then
    return 2
  fi
  log "$app: HTTP precheck repair applied, validated and staged; resuming upgrade flow"
  return 0
}

run_prechecks() {
  local app="$1"
  local namespace="$2"

  if ! check_rollout_ready "$app" "$namespace" "precheck"; then
    record_failure_and_maybe_abort "Precheck rollout failed ($app)" "Workload rollout status failed in namespace $namespace."
    return 1
  fi

  if ! check_ingress_http_codes "$app" "$namespace" "precheck"; then
    local http_repair_status
    if maybe_repair_http_precheck "$app" "$namespace"; then
      return 0
    else
      http_repair_status=$?
    fi
    # Approved repairs report their own failures; declined/unknown cases keep
    # the existing Continue/Abort failure flow.
    [ "$http_repair_status" -ne 2 ] || return 1
    record_failure_and_maybe_abort "Precheck HTTP failed ($app)" "Health checks failed for $app:\n$check_summary_body"
    return 1
  fi

  return 0
}

run_postchecks() {
  local app="$1"
  local namespace="$2"

  if ! check_rollout_ready "$app" "$namespace" "postcheck"; then
    record_failure_and_maybe_abort "Postcheck rollout failed ($app)" "Workload rollout status failed in namespace $namespace."
    return 1
  fi

  if ! check_ingress_http_codes "$app" "$namespace" "postcheck"; then
    record_failure_and_maybe_abort "Postcheck HTTP failed ($app)" "Health checks failed for $app after upgrade:\n$check_summary_body"
    return 1
  fi

  if [ "$check_summary_kind" = "pod-logs" ]; then
    if ! show_postcheck_log_review_modal "$app" "$namespace" "$check_summary_title" "$check_summary_body"; then
      record_failure_and_maybe_abort "Postcheck pod log review failed ($app)" "Pod logs did not look good after upgrade in namespace $namespace."
      return 1
    fi
    log "$app [postcheck]: pod log review accepted" "$(color_blue "$app") [postcheck]: pod log review accepted"
  fi

  if [ -n "${schema_repaired_values[$app]:-}" ]; then
    stage_fleet_file "${schema_repaired_values[$app]}" || return 1
    unset 'schema_repaired_values[$app]'
  fi
  return 0
}

perform_upgrade() {
  local app="$1"
  local namespace="$2"
  local target_version="$3"
  local fleet_file="$4"

  if [ "$dry_run" -eq 1 ]; then
    log "$app: DRY RUN enabled, skipping Fleet apply" "$(color_blue "$app"): DRY RUN enabled, skipping Fleet apply"
    return 0
  fi

  if [ -z "$fleet_file" ]; then
    record_failure_and_maybe_abort "Fleet file not found ($app)" "No matching fleet.yaml was found under $script_dir/fleet."
    return 1
  fi

  log "$app: updating Fleet version to $target_version in $fleet_file" \
    "$(color_blue "$app"): updating Fleet version to $target_version"
  if ! update_fleet_version "$fleet_file" "$target_version"; then
    record_failure_and_maybe_abort "Unable to update Fleet file ($app)" \
      "Could not replace the version field in $fleet_file."
    return 1
  fi

  apply_fleet_bundle "$app" "$(dirname "$fleet_file")" "$namespace" "$target_version"
}

# Rancher is installed directly with Helm, not through Fleet.  Delegate to
# upgrade-k3s-rancher.sh --rancher-only so the target comes from the SUSE
# support matrix and its k3s compatibility check, etcd snapshot and
# pending-upgrade recovery apply.  That script prompts on the terminal itself.
process_rancher_upgrade() {
  local app="$1"
  local namespace="$2"
  local local_version="$3"
  local new_version
  local release_status
  local cmd

  if [ "$dry_run" -eq 1 ]; then
    log "$app: DRY RUN enabled, skipping $rancher_upgrade_script --rancher-only" \
      "$(color_blue "$app"): DRY RUN enabled, skipping Rancher upgrade"
    dry_run_approved_count=$((dry_run_approved_count + 1))
    return 0
  fi

  if [ ! -x "$rancher_upgrade_script" ]; then
    record_failure_and_maybe_abort "Rancher upgrade script unavailable" \
      "Expected executable $rancher_upgrade_script."
    return 1
  fi

  cmd=(env "ROLLOUT_TIMEOUT=$rollout_timeout" "$rancher_upgrade_script" --rancher-only)
  if [ "${EUID:-$(id -u)}" -ne 0 ]; then
    cmd=(sudo "${cmd[@]}")
  fi

  log "$app: running ${cmd[*]}" "$(color_blue "$app"): running upgrade-k3s-rancher.sh --rancher-only"
  cleanup
  clear 2>/dev/null || true
  if ! "${cmd[@]}"; then
    record_failure_and_maybe_abort "Rancher upgrade failed ($app)" \
      "upgrade-k3s-rancher.sh --rancher-only failed or was declined. See $script_dir/logs/upgrade-k3s-rancher-*.log."
    return 1
  fi

  new_version="$(get_local_chart_version "$app")"
  release_status="$(helm list --kubeconfig "$kubeconfig_path" --namespace "$namespace" --all \
    --filter '^rancher$' -o json 2>>"$log_file" | jq -r '.[0].status // empty' 2>>"$log_file" || true)"
  if [ "$release_status" != deployed ]; then
    record_failure_and_maybe_abort "Rancher release not deployed ($app)" \
      "Helm release $namespace/$app has status ${release_status:-unknown} after upgrade."
    return 1
  fi
  if [ "$new_version" = "$local_version" ]; then
    log "$app: chart unchanged at $local_version (support-matrix target is not newer)" \
      "$(color_blue "$app"): $(color_count "chart unchanged; support-matrix target is not newer" "$yellow")"
    skipped_count=$((skipped_count + 1))
    return 0
  fi
  log "$app: upgraded $local_version -> $new_version" \
    "$(color_blue "$app"): upgraded $local_version -> $new_version"
  updated_count=$((updated_count + 1))

  if ! run_postchecks "$app" "$namespace"; then
    log "$app: postchecks did not pass; Ansible vars were not staged"
    return 1
  fi
  if ! git -C "$homelab_root" diff --quiet -- "$rancher_ansible_vars_file" 2>>"$log_file"; then
    if stage_fleet_file "$rancher_ansible_vars_file"; then
      staged_fleet_count=$((staged_fleet_count + 1))
    fi
  fi
  return 0
}

ask_run_codex_commit() {
  if [ "$staged_fleet_count" -eq 0 ]; then
    return 0
  fi

  if [ "$yes_mode" -eq 1 ]; then
    log "AUTO: running make codex-commit because --yes is set"
  elif ! dialog \
    --clear \
    --title "Commit Fleet upgrades" \
    --defaultno \
    --yesno "Fleet upgrades passed their rollout and ingress checks and were staged.\n\nRun:\nmake -C $homelab_root codex-commit\n\nRun it now?" 14 90; then
    log "User chose not to run make codex-commit; staged Fleet files were left in place."
    return 0
  fi

  log "Running make -C $homelab_root codex-commit"
  if ! make -C "$homelab_root" codex-commit >>"$log_file" 2>&1; then
    record_failure_and_maybe_abort "codex-commit failed" "make codex-commit returned a failure."
    return 1
  fi
  log "make codex-commit completed successfully."
  return 0
}

trap cleanup EXIT

mkdir -p "$log_dir"
: >"$log_file"

if ! command -v fleet >/dev/null 2>&1; then
  if ! install_fleet_cli; then
    cat >&2 <<'FLEET_MISSING'
Missing required command: fleet

Install the Fleet CLI on Debian with:
  curl -L -o /tmp/fleet https://github.com/rancher/fleet/releases/latest/download/fleet-linux-amd64
  chmod +x /tmp/fleet
  sudo install -m 0755 /tmp/fleet /usr/local/bin/fleet
  fleet --version

The Fleet CLI is separate from the Fleet controllers already running in Rancher.
FLEET_MISSING
    exit 2
  fi
fi

if ! require_cmd dialog || ! require_cmd helm || ! require_cmd kubectl || ! require_cmd curl || ! require_cmd git || ! require_cmd make; then
  echo "Setup error: required dependency is missing." >&2
  exit 2
fi
if [ ! -x "$is_up_to_date_helper" ] || [ ! -x "$current_version_helper" ] || [ ! -x "$chart_repo_helper" ]; then
  echo "Setup error: one or more helper scripts are missing or not executable." >&2
  exit 2
fi

init_colors
init_dialog_colors

log "Run started. log_file=$log_file yes_mode=$yes_mode dry_run=$dry_run rollout_timeout=$rollout_timeout"
repo_sync_script="$script_dir/cron/git-pull.sh"
log "Refreshing chart repositories with $repo_sync_script"
if [ ! -x "$repo_sync_script" ]; then
  record_failure_and_maybe_abort "Repository refresh script unavailable" \
    "Expected executable repository refresh script at $repo_sync_script."
else
  if ! "$repo_sync_script" >>"$log_file" 2>&1; then
    record_failure_and_maybe_abort "Repository refresh failed" \
      "$repo_sync_script failed. See $log_file for details."
  else
    log "Repository refresh completed (return_code=0)" \
      "$(color_blue "repositories"): repository refresh completed"
  fi
fi

log "Refreshing helm repos"
if ! helm repo update >>"$log_file" 2>&1; then
  record_failure_and_maybe_abort "helm repo update failed" "Unable to refresh helm repos."
else
  log "helm repo update completed (return_code=0)" "helm repo update completed (return_code=$(color_bash_return_code 0))"
fi

apps="$(list_candidate_apps)"
if [ -z "$apps" ]; then
  log "No helm apps discovered."
  exit "$exit_status"
fi

for app in $apps; do
  total_discovered_count=$((total_discovered_count + 1))
  current_app="$app"
  current_app_failed=0

  if fleet_app_is_plain_resources "$app"; then
    log "$app: excluded because Fleet bundle contains plain resources without a Helm chart" \
      "$(color_blue "$app"): excluded because Fleet bundle contains plain resources without a Helm chart"
    excluded_count=$((excluded_count + 1))
    current_app=""
    continue
  fi

  if should_exclude_app "$app"; then
  log "$app: excluded by pattern" "$(color_blue "$app"): excluded by pattern"
    excluded_count=$((excluded_count + 1))
    current_app=""
    continue
  fi

  if fleet_app_is_scaled_to_zero "$app"; then
    log "$app: skipped because Fleet config sets replicas to 0" \
      "$(color_blue "$app"): $(color_count "skipped because Fleet config sets replicas to 0" "$yellow")"
    skipped_count=$((skipped_count + 1))
    current_app=""
    continue
  fi

  log "$app: checking if update is available" "$(color_blue "$app"): checking if update is available"
  "$is_up_to_date_helper" "$app" --do-not-update-helm >>"$log_file" 2>&1
  update_check_return_code=$?
  helper_status="$(helper_status_label "$update_check_return_code")"

  repair_failed_release=0
  if [ "$update_check_return_code" -eq 0 ] || [ "$update_check_return_code" -eq 2 ]; then
    namespace="$(get_namespace_for_app "$app")"
    release_status="$(helm list --kubeconfig "$kubeconfig_path" \
      --namespace "$namespace" --all --filter "^${app}$" \
      -o json 2>>"$log_file" | jq -r '.[0].status // empty' 2>>"$log_file" || true)"
    if [ "$release_status" = failed ]; then
      log "$app: target version is current but Helm release failed; retrying Fleet reconciliation"
      repair_failed_release=1
      update_check_return_code=1
    fi
  fi

  if [ "$update_check_return_code" -eq 0 ] || [ "$update_check_return_code" -eq 2 ]; then
    log "$app: no update needed (helper status: $helper_status, return_code: $update_check_return_code)" "$(color_blue "$app"): no update needed (helper status: $(color_helper_status "$update_check_return_code"), return_code: $(color_helper_code "$update_check_return_code"))"
    up_to_date_count=$((up_to_date_count + 1))
    current_app=""
    continue
  fi
  if [ "$update_check_return_code" -ne 1 ]; then
    if [ "$update_check_return_code" -eq 3 ]; then
      version_check_error_apps+=("$app")
    fi
    log "$app: unexpected helper status: $helper_status (return_code: $update_check_return_code)" "$(color_blue "$app"): unexpected helper status: $(color_helper_status "$update_check_return_code") (return_code: $(color_helper_code "$update_check_return_code"))"
    record_failure_and_maybe_abort "Update check failed ($app)" "is-helm-image-up-to-date.sh returned unexpected return_code $update_check_return_code."
    current_app=""
    continue
  fi
  log "$app: update available (helper status: $helper_status)" "$(color_blue "$app"): update available (helper status: $(color_helper_status "$update_check_return_code"))"

  namespace="$(get_namespace_for_app "$app")"
  if [ -z "$namespace" ]; then
    record_failure_and_maybe_abort "Namespace not found ($app)" "Unable to resolve namespace from helm ls output."
    current_app=""
    continue
  fi

  local_version="$(get_local_chart_version "$app")"
  target_version="$(chart_version_for_app "$app" 2>>"$log_file")"
  fleet_file="$(fleet_file_for_app "$app" 2>/dev/null || true)"

  if [ -z "$target_version" ]; then
    record_failure_and_maybe_abort "Version lookup failed ($app)" "Current chart version helper returned empty version."
    current_app=""
    continue
  fi

  # The image checker can report an application-image update even when the
  # configured Helm chart repository has no newer chart.  Never reconcile a
  # no-op chart version through Fleet; it can create a new Helm revision and
  # unnecessarily reprocess PVCs and other resources.
  installed_chart_version="${local_version#"$app-"}"
  if [ "$repair_failed_release" -eq 0 ] && [ -n "$installed_chart_version" ] && [ "$installed_chart_version" = "$target_version" ]; then
    log "$app: no chart update needed (installed=$installed_chart_version target=$target_version)" \
      "$(color_blue "$app"): no chart update needed (installed=$installed_chart_version target=$target_version)"
    up_to_date_count=$((up_to_date_count + 1))
    current_app=""
    continue
  fi

  if ! run_prechecks "$app" "$namespace"; then
    current_app=""
    continue
  fi

  modal_summary_body="$check_summary_body"
  if [ "$app" = rancher ]; then
    modal_summary_body="${modal_summary_body}\Z0\n\nRancher is not Fleet-managed: this runs upgrade-k3s-rancher.sh --rancher-only, which picks the target from the SUSE support matrix, checks k3s compatibility, takes an etcd snapshot and asks for confirmation in the terminal."
  fi
  if show_app_modal "$app" "$namespace" "$local_version" "$target_version" "$check_summary_title" "$modal_summary_body"; then
    log "$app: upgrade approved" "$(color_blue "$app"): upgrade approved"
  else
    log "$app: upgrade skipped by user" "$(color_blue "$app"): upgrade skipped by user"
    skipped_count=$((skipped_count + 1))
    current_app=""
    continue
  fi

  if [ "$app" = rancher ]; then
    process_rancher_upgrade "$app" "$namespace" "$local_version" || true
    log "$app: processing complete" "$(color_blue "$app"): processing complete"
    current_app=""
    continue
  fi

  if ! perform_upgrade "$app" "$namespace" "$target_version" "$fleet_file"; then
    current_app=""
    continue
  fi

  if [ "$dry_run" -eq 1 ]; then
    dry_run_approved_count=$((dry_run_approved_count + 1))
  else
    if ! verify_fleet_upgrade_version "$app" "$namespace" "$target_version"; then
      current_app=""
      continue
    fi
    updated_count=$((updated_count + 1))
  fi

  if run_postchecks "$app" "$namespace"; then
    if [ "$dry_run" -eq 0 ]; then
      if stage_fleet_file "$fleet_file"; then
        staged_fleet_count=$((staged_fleet_count + 1))
      fi
    fi
  else
    log "$app: postchecks did not pass; Fleet file was not staged" \
      "$(color_blue "$app"): postchecks did not pass; Fleet file was not staged"
  fi
  log "$app: processing complete" "$(color_blue "$app"): processing complete"
  current_app=""
done

log "Run finished with exit status $exit_status"
show_summary_modal
if [ "$exit_status" -eq 0 ]; then
  ask_run_codex_commit || true
fi
exit "$exit_status"
