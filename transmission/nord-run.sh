#!/bin/bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

# Some setters return failure when the requested value is already configured.
# Accept only that explicit single-line response; propagate other failures.
set_vpn() {
    local output status
    if output=$(nordvpn set "$@" 2>&1); then
        printf '%s\n' "$output"
        return 0
    else
        status=$?
    fi
    printf '%s\n' "$output"
    if [[ "$output" != *$'\n'* && "$output" == *' is already set to '* ]]; then
        return 0
    fi
    return "$status"
}

set_vpn analytics off
# Avoid re-authenticating an already logged-in daemon on every startup.
if ! nordvpn account >/dev/null 2>&1; then
    if [[ ! -s "$script_dir/.nord_token" ]]; then
        echo "Missing or empty NordVPN token: $script_dir/.nord_token" >&2
        exit 1
    fi
    nordvpn login --token "$(cat "$script_dir/.nord_token")"
fi

# Enable the kill switch before changing connection settings.
set_vpn killswitch on
set_vpn autoconnect on Czech_Republic
set_vpn lan-discovery enabled
set_vpn protocol openvpn_tcp
set_vpn protection off

# NordVPN 6 removed the P2P group; standard locations support P2P.
connected=false
for attempt in 1 2 3 4 5; do
    echo "Connecting VPN (attempt $attempt/5)"
    if timeout 60s nordvpn connect Czech_Republic; then
        connected=true
        break
    fi
    if (( attempt < 5 )); then
        sleep 10
    fi
done
if [[ "$connected" != true ]]; then
    echo 'VPN connection failed after 5 attempts; kill switch remains enabled.' >&2
    exit 1
fi
nordvpn status

# Fail closed if the independent check fails or returns invalid JSON.
if curl --fail --silent --show-error --max-time 10 \
    https://api.nordvpn.com/v1/helpers/ips/insights | jq -e '.protected == true' >/dev/null; then
    echo 'VPN is connected and protection verified.'
else
    echo 'VPN protection could not be verified; refusing to continue startup.' >&2
    exit 1
fi
