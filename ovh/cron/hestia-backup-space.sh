#!/bin/bash
# Install a root-owned copy at /usr/local/sbin/hestia-backup-space.
# Root crontab: 55 4 * * * /usr/local/sbin/hestia-backup-space
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin

backup_dir=/backup
backup_user=sup
backup_home=/home/sup
buffer_mib=4096

log() {
    local priority=$1
    shift
    logger -t hestia-backup-space -p "daemon.$priority" -- "$*" || true
}

fail() {
    log err "$*"
    printf '%s\n' "$*" >&2
    exit 1
}

measure_space() {
    local usage blocks block_size
    usage=$(du -sm -- "$backup_home") || fail "Cannot measure $backup_home"
    usage=${usage%%$'\t'*}
    read -r blocks block_size < <(stat -f --format='%a %S' -- "$backup_dir") ||
        fail "Cannot measure available space on $backup_dir"
    # Hestia requires twice its backup size estimate. Use the whole home
    # directory because measuring it here doesn't depend on Hestia internals.
    required_mib=$((2 * usage + buffer_mib))
    available_mib=$((blocks * block_size / 1024 / 1024))
}

measure_space
case "${1:-}" in
    --check)
        printf 'Available: %s MiB. Target: %s MiB.\n' "$available_mib" "$required_mib"
        if (( available_mib >= required_mib )); then
            exit 0
        fi
        exit 1
        ;;
    '') ;;
    *) fail "Usage: $0 [--check]" ;;
esac

(( EUID == 0 )) || fail "Run cleanup as root"
if (( available_mib >= required_mib )); then
    exit 0
fi

log info "Available: ${available_mib} MiB. Target: ${required_mib} MiB. Cleaning APT package cache."
cd /
apt-get clean || log warning "APT cache cleanup failed."
measure_space

if (( available_mib < required_mib )); then
    log info "Available: ${available_mib} MiB. Target: ${required_mib} MiB. Cleaning npm package cache for $backup_user."
    runuser -u "$backup_user" -- env -i PATH="$PATH" HOME="$backup_home" \
        npm --cache "$backup_home/.npm" cache clean --force ||
        log warning "npm cache cleanup failed."
    measure_space
fi

if (( available_mib < required_mib )); then
    fail "Backup space remains low after cleanup: ${available_mib} MiB available, ${required_mib} MiB target."
fi
log info "Space target met: ${available_mib} MiB available, ${required_mib} MiB target."
