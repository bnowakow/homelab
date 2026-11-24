#!/usr/bin/env bash

set -u
set -o pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
IMAGE_CHECKER="$SCRIPT_DIR/zabbix/is-docker-image-up-to-date.sh"
STARTUP_WAIT_SECONDS=${STARTUP_WAIT_SECONDS:-30}
LOG_FILE=${UPGRADE_DOCKER_IMAGES_LOG:-/tmp/upgrade-docker-images-${UID}.log}
TAG_CACHE_FILE=${UPGRADE_DOCKER_IMAGES_TAG_CACHE:-/tmp/upgrade-docker-images-tags-${UID}.cache}
TAG_CACHE_SECONDS=${UPGRADE_DOCKER_IMAGES_TAG_CACHE_SECONDS:-300}

# Fields: label, compose directory, service, image, container, health check.
# The compose directory is also the working directory used for make upgrade
# and make codex-commit.  Health checks are either https or none.
DEPLOYMENTS=(
  "n2nieruchomosci.pl|/home/sup/docker/n2nieruchomosci.pl|wordpress|wordpress|auto|https"
  "bnowakowski.pl|/home/sup/docker/bnowakowski.pl|wordpress|wordpress|auto|https"
  "mysql|/home/sup/docker/mysql|mysql|mariadb|shared-mysql|none"
)

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

log() {
  local message=$1
  printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$message" >>"$LOG_FILE"
}

: >"$LOG_FILE" || die "Could not write log file: $LOG_FILE"
touch "$TAG_CACHE_FILE" || die "Could not write tag cache file: $TAG_CACHE_FILE"
log "starting script; log=$LOG_FILE"

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "Required command is missing: $1"
}

show_message() {
  local title=$1 message=$2
  dialog --colors --title "$title" --msgbox "$message" 18 90 </dev/tty >/dev/tty
}

confirm() {
  local title=$1 message=$2
  dialog --colors --title "$title" --defaultno --yesno "$message" 22 90 </dev/tty >/dev/tty
}

latest_tag() {
  local image=$1 result newest checker_status response_file next_url page tag_count candidate

  log "checking image=$image with checker=$IMAGE_CHECKER"
  result=$($IMAGE_CHECKER "$image" 2>>"$LOG_FILE") || {
    checker_status=$?
    log "checker failed image=$image status=$checker_status"
    return 1
  }
  log "checker output image=$image result=$(printf '%q' "$result")"
  if [[ "$result" != true ]]; then
    # The checker format is false,<outdated-containers>,<newest-tag>.
    newest=$(printf '%s\n' "$result" | awk -F, '{ value=$NF; gsub(/[[:space:]]/, "", value); print value }')
    if [[ "$newest" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
      if docker manifest inspect "${image}:${newest}" >/dev/null 2>>"$LOG_FILE"; then
        printf '%s\n' "$newest"
        return 0
      fi
      log "checker selected tag without a pullable manifest image=$image tag=$newest; falling back to Docker Hub tag list"
    fi
  fi

  # The checker returns only "true" when every matching container is current;
  # obtain the tag value from Docker Hub in that case.
  response_file=$(mktemp)
  next_url="https://registry.hub.docker.com/v2/repositories/library/${image}/tags?page_size=100"
  page=0
  : >"$response_file"
  while [[ -n "$next_url" ]]; do
    page=$((page + 1))
    if ! curl --fail --silent --show-error --location --max-time 30 "$next_url" \
        >"${response_file}.page" 2>>"$LOG_FILE"; then
      log "Docker Hub pagination stopped image=$image page=$page url=$next_url; using tags collected from $((page - 1)) pages"
      break
    fi
    tag_count=$(jq '.results | length' "${response_file}.page" 2>>"$LOG_FILE") || {
      log "invalid JSON from Docker Hub image=$image page=$page"
      rm -f "$response_file" "${response_file}.page"
      return 1
    }
    log "Docker Hub page image=$image page=$page tags=$tag_count"
    jq -r '.results[]?.name' "${response_file}.page" >>"$response_file"
    next_url=$(jq -r '.next // empty' "${response_file}.page")
  done
  newest=""
  while IFS= read -r candidate; do
    if docker manifest inspect "${image}:${candidate}" >/dev/null 2>>"$LOG_FILE"; then
      newest=$candidate
      break
    fi
    log "skipping tag without a pullable manifest image=$image tag=$candidate"
  done < <(sed -nE 's/^([0-9]+\.[0-9]+\.[0-9]+)(-.+)?$/\1/p' "$response_file" |
    sort -Vu | sort -Vr)
  if [[ -z "$newest" ]]; then
    log "could not find a pullable versioned tag across $page Docker Hub pages image=$image"
    rm -f "$response_file" "${response_file}.page"
    return 1
  fi
  log "selected newest base tag image=$image tag=$newest pages=$page"
  rm -f "$response_file" "${response_file}.page"
  [[ -n "$newest" ]] || return 1
  [[ "$newest" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || return 1
  printf '%s\n' "$newest"
}

running_tag() {
  local container=$1 image_tag
  image_tag=$(docker inspect --format '{{.Config.Image}}' "$container" 2>/dev/null || true)
  [[ "$image_tag" == *:* ]] || return 1
  printf '%s\n' "${image_tag##*:}"
}

newer_tag() {
  printf '%s\n' "$@" | sort -V | tail -n 1
}

require_command docker
require_command curl
require_command make
require_command jq
require_command dialog
require_command git
if [[ ! -t 0 && ! -t 1 ]]; then
  die "This script must be run from an interactive terminal because it uses dialog."
fi
[[ -x "$IMAGE_CHECKER" ]] || die "Image checker is not executable: $IMAGE_CHECKER"

declare -A newest_tag_cache
updates=()
for deployment in "${DEPLOYMENTS[@]}"; do
  IFS='|' read -r directory project_dir service image container health_check <<< "$deployment"
  compose_file="$project_dir/compose.yml"
  env_file="$project_dir/.env"
  [[ -f "$compose_file" && -f "$env_file" ]] || die "Missing compose or .env in $directory"

  current_tag=$(sed -n "s/^[[:space:]]*image:[[:space:]]*${image}:\([^[:space:]]*\).*/\1/p" "$compose_file" | head -n 1)
  [[ -n "$current_tag" ]] || die "Could not find ${image} image tag in $compose_file"
  domain=$(sed -n 's/^DOMAIN=//p' "$env_file" | head -n 1)
  if [[ "$health_check" == https ]]; then
    [[ -n "$domain" ]] || die "DOMAIN is missing in $env_file"
  fi

  if [[ "$container" == auto ]]; then
    [[ -n "$domain" ]] || die "DOMAIN is missing in $env_file"
    container="${domain}-${service}"
  fi
  running_image_tag=$(running_tag "$container" || true)
  check_status=0
  if [[ ${newest_tag_cache[$image]+_} ]]; then
    newest_tag=${newest_tag_cache[$image]}
    log "using cached newest tag image=$image tag=$newest_tag"
  else
    cached_tag=$(awk -F '\t' -v image="$image" -v now="$(date +%s)" -v ttl="$TAG_CACHE_SECONDS" \
      '$1 == image && now - $2 < ttl { print $3; exit }' "$TAG_CACHE_FILE")
    if [[ -n "$cached_tag" ]]; then
      newest_tag=$cached_tag
      log "using persistent cached newest tag image=$image tag=$newest_tag"
    else
      newest_tag=$(latest_tag "$image") || check_status=$?
    fi
    if [[ $check_status -eq 0 ]]; then
      newest_tag_cache[$image]=$newest_tag
      if [[ -n "$cached_tag" ]]; then
        :
      else
        printf '%s\t%s\t%s\n' "$image" "$(date +%s)" "$newest_tag" >>"$TAG_CACHE_FILE"
      fi
    fi
  fi
  if [[ $check_status -eq 2 ]]; then
    newest_tag=${running_image_tag:-$current_tag}
  elif [[ $check_status -ne 0 ]]; then
    log "fatal: could not determine newest tag image=$image; see $LOG_FILE"
    die "Could not determine the newest tag for $image (see $LOG_FILE)"
  fi

  if docker manifest inspect "${image}:${current_tag}" >/dev/null 2>>"$LOG_FILE"; then
    desired_tag=$(newer_tag "$current_tag" "$newest_tag" "$running_image_tag")
  else
    log "configured tag has no pullable manifest image=$image tag=$current_tag; allowing fallback"
    desired_tag=$(newer_tag "$newest_tag" "$running_image_tag")
  fi
  if [[ "$current_tag" != "$desired_tag" || ( -n "$running_image_tag" && "$running_image_tag" != "$current_tag" ) ]]; then
    updates+=("$directory|$project_dir|$compose_file|$service|$image|$current_tag|$desired_tag|$running_image_tag|$container|$health_check")
  fi
done

if ((${#updates[@]} == 0)); then
  show_message "Docker image updates" "All configured Docker images are up to date."
  exit 0
fi

summary=$'\ZbAvailable updates:\Zn\n\n'
for update in "${updates[@]}"; do
  IFS='|' read -r directory _ _ service image current_tag newest_tag running_image_tag <<< "$update"
  summary+=$'\Zb\t'"$directory ($service)"$'\Zn\n'
  summary+=$'\t\tCompose:  '\Z3'\Zb'"$image:$current_tag"$'\Zn\n'
  if [[ -n "$running_image_tag" ]]; then
    summary+=$'\t\tRunning:  '\Z3'\Zb'"$image:$running_image_tag"$'\Zn\n'
  fi
  summary+=$'\t\tUpgrade:  '\Z2'\Zb'"$image:$newest_tag"$'\Zn\n\n'
done
summary+=$'Start the upgrade now?'
confirm "Docker image updates" "$summary" || exit 0

completed_summary=$'\ZbUpgraded successfully:\Zn\n\n'
for update in "${updates[@]}"; do
  IFS='|' read -r directory project_dir compose_file service image old_tag new_tag running_image_tag container health_check <<< "$update"
  env_file="$project_dir/.env"
  domain=$(sed -n 's/^DOMAIN=//p' "$env_file" | head -n 1)

  sed -i "s#^\([[:space:]]*image:[[:space:]]*${image}:\)[^[:space:]]*#\1${new_tag}#" "$compose_file"
  if ! sudo make -C "$project_dir" upgrade; then
    show_message "Upgrade failed" "$directory: make upgrade failed. The compose file contains the new tag; inspect the deployment before retrying."
    exit 1
  fi

  if [[ "$health_check" == https ]]; then
    sleep "$STARTUP_WAIT_SECONDS"
    if ! curl --fail --silent --show-error --location --max-time 30 "https://${domain}/" >/dev/null; then
      show_message "WordPress health check failed" "$directory did not respond successfully at https://${domain}/ after the upgrade. The old image was not deleted and no commit was made."
      exit 1
    fi
  else
    log "skipping HTTP health check directory=$directory health_check=$health_check"
  fi

  if [[ "$old_tag" != "$new_tag" ]]; then
    old_image="${image}:${old_tag}"
    if docker ps --format '{{.Image}}' | grep -Fxq "$old_image"; then
      log "keeping old image still in use directory=$directory image=$old_image"
    else
      docker image rm "$old_image" || die "Could not delete unused old image $old_image"
    fi
  fi

  completed_summary+=$'\Zb\t'"$directory ($service)"$'\Zn\n'
  completed_summary+=$'\t\t\Z3\Zb'"$image:$old_tag"$'\Zn -> \Z2\Zb'"$image:$new_tag"$'\Zn\n\n'
done

declare -A dirty_repositories
dirty_summary=$'\ZbUncommitted changes:\Zn\n\n'
for deployment in "${DEPLOYMENTS[@]}"; do
  IFS='|' read -r directory project_dir _ _ _ _ <<< "$deployment"
  [[ ${dirty_repositories[$project_dir]+_} ]] && continue
  status=$(git -C "$project_dir" status --short 2>/dev/null || true)
  if [[ -n "$status" ]]; then
    dirty_repositories[$project_dir]=$directory
    dirty_summary+=$'\Zb\t'"$directory"$'\Zn\n'
    while IFS= read -r status_line; do
      dirty_summary+=$'\t\t'"$status_line"$'\n'
    done <<< "$status"
    dirty_summary+=$'\n'
  fi
done

if ((${#dirty_repositories[@]} > 0)); then
  if confirm "Commit and push Docker repository changes" "$completed_summary"$'\n'"$dirty_summary"$'Commit and push all listed changes now?'; then
    for project_dir in "${!dirty_repositories[@]}"; do
      directory=${dirty_repositories[$project_dir]}
      git -C "$project_dir" add -A || die "Could not stage changes in $directory"
      if make -C "$project_dir" -p 2>/dev/null | grep -q '^codex-commit:'; then
        make -C "$project_dir" codex-commit || die "codex-commit failed in $directory"
      else
        log "codex-commit target missing directory=$directory; using git commit"
        git -C "$project_dir" commit -m "Update Docker deployment in $directory" || die "git commit failed in $directory"
      fi
      git -C "$project_dir" push || die "git push failed in $directory"
    done
  else
    show_message "Docker image upgrade" "$completed_summary"$'\nRepository changes were not committed.'
    exit 0
  fi
fi

show_message "Docker image upgrade" "$completed_summary"
