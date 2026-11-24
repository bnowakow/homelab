#!/bin/bash

name="${1:-duckdns}"
# in $2 there could be do_not_update_helm it'ss crap because it doesn't parse argument it will just pass $2 whenever it's empty or set

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
version_helper="$script_dir/zabbix/lib/helm-current-version-of-chart.sh"
if [ ! -x "$version_helper" ]; then
    version_helper="/etc/zabbix/zabbix_agent2.d/homelab/rancher/zabbix/lib/helm-current-version-of-chart.sh"
fi
if ! version_current="$("$version_helper" "$name" "${2:-}")"; then
    printf '%s: could not determine the available chart version.\n' "$name" >&2
    echo "false,error"
    exit 3
fi
# Release names can differ from chart names (for example homer-without-auth).
# Select the release exactly and read its installed chart from structured output.
if ! installed_chart=$(set -o pipefail
    timeout 5s helm ls --all-namespaces --kubeconfig /etc/rancher/k3s/k3s.yaml -o json |
    jq -r --arg name "$name" '[.[] | select(.name == $name)][0].chart // empty'); then
    printf '%s: could not read installed Helm releases.\n' "$name" >&2
    echo "false,error"
    exit 3
fi
version_local="${installed_chart##*-}"

version_is_numeric() {
    local version="$1"
    local component

    for component in $(echo "$version" | sed 's/.*-//' | tr '.' ' '); do
        case "$component" in
            ''|*[!0-9]*) return 1 ;;
        esac
    done
    return 0
}

if [ -z "$version_current" ] || ! version_is_numeric "$version_current" || \
   { [ -n "$version_local" ] && ! version_is_numeric "$version_local"; }; then
    printf '%s: invalid chart version (available=%q, installed=%q).\n' \
        "$name" "$version_current" "$version_local" >&2
    echo "false,error"
    exit 3
fi

# https://www.truenas.com/community/threads/install-helm-chart-via-command-line.97191/
# https://github.com/k3s-io/k3s/issues/1126
if [ "$version_local" = "$version_current" ]; then
    echo true;
    exit 0
else
    # below is to address 1.9 vs 1.19 where string comparison needs 001.009 vs 001.019
    numerical_version_current=""
    for i in $(echo $version_current | sed 's/.*-//' | tr '.' ' '); do
        numerical_version_current=$numerical_version_current.$(printf "%03d" $i);
    done
    numerical_version_local=""
    for i in $(echo $version_local | sed 's/.*-//' | tr '.' ' '); do
        numerical_version_local=$numerical_version_local.$(printf "%03d" $i);
    done
    if [ "$numerical_version_local" = "" ]; then
        echo "false,not-running"
        # https://discord.com/channels/830763548678291466/1051965552458993787/1052262549132935218
        # in case of "Error: UPGRADE FAILED: another operation (install/upgrade/rollback) is in progress"
        # 1: sudo k3s kubectl describe deploy -n ix-$NAME | grep helm-revision
        # 2: sudo k3s kubectl get secrets -n ix-$NAME
        # for every number in #2 that's higer than #1 do:
        # sudo k3s kubectl delete secret -n ix-$NAME sh.helm.release.v1.$NAME.v$NUMBER
        # Stop the app
        # Start the app
        # Edit the app and save without changes
        # If all works, try to upgrade. If not. reinstall.
        exit
    fi
    if [ $(echo -e "$numerical_version_local\n$numerical_version_current" | sort | tail -1) = $numerical_version_local ]; then
        # local version is greater than current (repo is not keeping up with updates in helm)
        echo "true,newer";
        exit 2
    else
        echo false,$version_local,$version_current;
        exit 1
    fi
fi
