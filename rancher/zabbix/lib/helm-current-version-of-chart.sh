#!/bin/bash

name="${1:-duckdns}"
# TODO below is crap because it doesn't parse argument, it will just check if second argument is not empty
do_not_update_helm="${2:-}"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
repositories_file="$script_dir/helm-repositories.sh"

# Zabbix is published in the community HTTP Helm repository, not in the
# configured TrueCharts OCI registry. Refresh only its exact repository before
# searching; a stale checkout or cached index must not hide a newer release.
if [ "$name" = "zabbix" ]; then
    if [ -z "$do_not_update_helm" ]; then
        if ! timeout 15s helm repo update zabbix-community \
            --fail-on-repo-update-fail >/dev/null 2>&1; then
            exit 1
        fi
    fi
    repository_charts="$(helm search repo zabbix-community/zabbix \
        --versions --output json 2>/dev/null)" || exit 1
    printf '%s\n' "$repository_charts" | jq -er '
        [.[] | select(.name == "zabbix-community/zabbix") |
         .version | select(test("^[0-9]+\\.[0-9]+\\.[0-9]+$"))][0] // empty'
    exit $?
fi

# OCI charts are not returned by `helm search repo`; resolve their configured
# reference directly and ask Helm for the chart metadata instead.
if [ -f "$repositories_file" ]; then
    # shellcheck source=/dev/null
    source "$repositories_file"

    chart_repo_resolver="$script_dir/zabbix/lib/helm-chart-repo-dir.sh"
    if [ ! -x "$chart_repo_resolver" ]; then
        chart_repo_resolver="/etc/zabbix/zabbix_agent2.d/homelab/rancher/zabbix/lib/helm-chart-repo-dir.sh"
    fi
    chart_repo_dir="$($chart_repo_resolver "$name" 2>/dev/null || true)"

    # Prefer a checked-out chart repository over the vendored Fleet copy.
    if [ -n "$chart_repo_dir" ] && [ -f "$chart_repo_dir/Chart.yaml" ]; then
        awk -F': *' '$1 == "version" { sub(/[[:space:]]+#.*/, "", $2); gsub(/[[:space:]]+$/, "", $2); print $2; exit }' "$chart_repo_dir/Chart.yaml"
        exit 0
    fi

    # Fleet can package a chart directly in the application directory instead
    # of pulling it from a Helm repository.  Use this only when no checked-out
    # chart repository has a matching chart.
    local_chart_file="$script_dir/fleet/apps/$name/charts/$name/Chart.yaml"
    if [ -f "$local_chart_file" ]; then
        awk -F': *' '$1 == "version" { sub(/[[:space:]]+#.*/, "", $2); gsub(/[[:space:]]+$/, "", $2); print $2; exit }' "$local_chart_file"
        exit 0
    fi

    chart_ref="$(helm_chart_ref_for_app "$name" 2>/dev/null || true)"
    if [ -n "$chart_ref" ]; then
        # Registry tags contain chart versions. Avoid downloading the chart
        # archive on each Zabbix poll (the agent has a 30-second timeout).
        if [ -f "$script_dir/zabbix/lib/oci-chart-version.py" ] && command -v python3 >/dev/null 2>&1; then
            timeout 20s python3 "$script_dir/zabbix/lib/oci-chart-version.py" "$chart_ref"
            exit $?
        fi
        metadata_error="$(mktemp)" || exit 1
        for attempt in 1 2 3; do
            chart_metadata="$(timeout 30s helm show chart "$chart_ref" 2>"$metadata_error")"
            helm_status=$?
            if [ "$helm_status" -eq 0 ] && [ -n "$chart_metadata" ]; then
                break
            fi
            printf '%s: chart metadata lookup failed (attempt %s/3, status %s):\n' \
                "$name" "$attempt" "$helm_status" >&2
            cat "$metadata_error" >&2
            if [ "$attempt" -eq 3 ] || { [ "$helm_status" -ne 124 ] &&
                ! grep -Eqi 'timeout|timed out|connection refused|connection reset|temporary failure|no such host|unexpected EOF|429|500|502|503|504' "$metadata_error"; }; then
                rm -f "$metadata_error"
                exit 1
            fi
            sleep "$attempt"
        done
        rm -f "$metadata_error"
        printf '%s\n' "$chart_metadata" |
            awk -F': *' '$1 == "version" { print $2; exit }'
        exit 0
    fi
fi

chart_repo_dir=$(/etc/zabbix/zabbix_agent2.d/homelab/rancher/zabbix/lib/helm-chart-repo-dir.sh $name) 

if [ "$chart_repo_dir" == "" ]; then
    # there's no repo on disk, so we'll be checking helm repos
    if helm search repo $name | grep "No results found" > /dev/null; then
        # no results was find in helm repo
        exit 1
    else
        if [ -z "${do_not_update_helm}" ]; then
            helm repo update > /dev/null
        fi
        # return only first result. elasticsearch is present in elastic helm repo and bitnami. adding zz-prefixes to get expected one as first. could be problematic in future
        helm search repo "$name" --versions 2>/dev/null | awk -v app="$name" '
            NR > 1 {
                chart=$1
                sub(/^.*\//, "", chart)
                if (chart == app || chart == "helm-" app) {
                    print $2
                    exit
                }
            }'
        exit
    fi
else
    # there is a repo on disk, so we'll be checking it
    cd $chart_repo_dir
    #git reset --hard 2>/dev/null >/dev/null 
    #git clean -f -d -x 2>/dev/null >/dev/null
    #git pull 2>/dev/null >/dev/null # &
    #if [ $? -gt 0 ]; then 
    #    echo false,git-pull-fail; 
    #fi
    # TODO check git status -uno if branch is not behind origin, downside would be if it takes too long

    file_path=Chart.yaml
    if [ -f $file_path ]; then 
        helm dependency build . > /dev/null 2>&1
        awk -F': *' '$1 == "version" { sub(/[[:space:]]+#.*/, "", $2); gsub(/[[:space:]]+$/, "", $2); print $2; exit }' "$file_path"
        exit
    fi

    if ls *.tgz > /dev/null 2>&1; then
        latest_tarbal=$(ls -1 *tgz | tail -1)
        # TODO | sed 's/.*: //' | sed 's/\ .*//' part is repeated with above
        tar -Oxvf $latest_tarbal $name/Chart.yaml 2> /dev/null | grep  ^version | sed 's/.*: //' | sed 's/\ .*//'
        exit
    fi
fi

exit 1
