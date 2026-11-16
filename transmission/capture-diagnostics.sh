#!/bin/bash
# Read-only host diagnostics; does not restart services or change VPN/firewall rules.
set -eu
umask 077
if (( EUID != 0 )); then
    echo 'Run on the host with sudo bash ./capture-diagnostics.sh [output-directory]' >&2
    exit 1
fi
out=${1:-/tmp/transmission-diagnostics-$(date +%Y%m%d-%H%M%S)}
mkdir -m 700 -p -- "$out"
out=$(cd -- "$out" && pwd)
capture() {
    local name=$1
    shift
    timeout 20s "$@" >"$out/$name.txt" 2>&1 || printf '\nCommand failed or timed out: %s\n' "$*" >>"$out/$name.txt"
}
capture time date --iso-8601=seconds
capture services systemctl status nordvpnd nordvpn-and-transmission transmission-daemon --no-pager
capture vpn-status nordvpn status
capture vpn-settings nordvpn settings
capture addresses ip -brief address
capture routes ip route show table all
capture rules ip rule show
capture sockets ss -s
capture firewall iptables-save -c
capture firewall-ipv6 ip6tables-save -c
capture nftables nft list ruleset
capture mounts findmnt -t nfs,nfs4
capture storage df -h /mnt/PlexPool/plex
capture journal journalctl -b --since '-30 minutes' -u nordvpnd -u nordvpn-and-transmission -u transmission-daemon --no-pager
capture kernel journalctl -b -k --since '-30 minutes' --no-pager
capture transmission tail -n 1500 /var/log/transmission-daemon.log
capture resolver cat /etc/resolv.conf
printf 'Saved diagnostics to %s\nFiles can contain IP addresses and torrent names; review before sharing.\n' "$out"
