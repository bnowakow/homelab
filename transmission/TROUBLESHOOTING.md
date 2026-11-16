# Intermittent download stall — 2026-11-16

Downloads worked after reboot and a manual `fix-config.sh`, stalled, and then
resumed without an intervention from this investigation (reported by the user).
No VPN, firewall, or daemon settings were changed.

## Evidence

The readable Transmission log ends at 20:19:09 CET in the investigation's
filesystem view. It does not include a verified recovery event or transfer-rate
history. Between 19:00 and that endpoint it contains 938 IPv4 DHT announce
failures and 566 magnet-status messages. Near 20:16–20:19, four magnets repeatedly
failed DHT announces, with `metainfo parsed 0`. DHT node counts increased from
roughly 408 to 428, which is evidence of some peer-discovery activity, not proof
of a healthy download path. IPv6 peer connections also failed with errno 99
(`Cannot assign requested address`). A separate torrent received a tracker
warning that its info hash was not authorized.

These observations are consistent with peer/metadata discovery delays. They do
not establish a VPN outage, a kill-switch fault, or the cause of all stalled
transfers. The DHT error text includes misleading-looking errno strings such as
`Success`; those strings alone do not diagnose the firewall. IPv6 failures do
not demonstrate that IPv4 downloads are broken.

The investigation environment cannot reach the host system bus, NordVPN socket,
or network interfaces, cannot elevate with sudo, and cannot read the system
journal, syslog, or live settings.json. Live VPN/firewall state and recovery
therefore remain unverified. Existing logging is already enabled through
`--logfile /var/log/transmission-daemon.log` in the installed daemon unit.

## Capture the next stall

Before restarting anything, run on the actual host:

```bash
sudo bash ./capture-diagnostics.sh
```

Run again after recovery so VPN status, routes, firewall counters, journal,
mounts, and recent Transmission messages can be compared. Output directories
are private under `/tmp`; copy them to durable storage if needed before reboot.
When invoked with sudo, the script gives the invoking user ownership of the
capture directory and files, retaining private permissions.
The script only reads state, makes no outbound connectivity probes, and limits
each command to 20 seconds. A failed command is recorded in its output file.
Captures can contain torrent names, IP addresses, and firewall details.

Also record whether all downloads stalled or only particular magnets, the UI's
peer counts and tracker errors, and the stall/recovery times. This separates a
host-wide tunnel failure from a torrent with no useful peers. If VPN status is
disconnected, retain the kill switch and inspect the NordVPN journal before
reconnecting. If VPN is connected, check tracker/peer state and storage errors
before restarting Transmission. Do not disable the kill switch as a test.

## Separate issues found in startup/recovery code

These deserve a maintenance change, but are not proven causes of this stall:

- `fix-config.sh` overwrites settings from the NFS copy and restarts the daemon;
  it has no root check or fail-fast handling if a stop/copy fails.
- `startup.sh` continues after `nord-run.sh` fails, since its exit status is not
  checked. The keepalive checks service status only, not transfer or VPN health.
- `nord-run.sh` uses `return 1` outside a function in its connection loop.
- The installed Transmission unit uses `ExecStop=/bin/kill -s STOP $MAINPID`.
  SIGSTOP suspends a process rather than allowing graceful shutdown. Inspect
  stop/restart journal entries before correcting this unit on the host.
- The startup unit declares neither required NFS mounts nor explicit VPN
  readiness dependencies. Transmission can also start independently through its
  own `multi-user.target` enablement; actual enablement was not accessible here.

## Follow-up at 21:10 CET

The log view refreshed through 21:10:42. From 20:50 onward there were 1,073
DHT announce failures across six torrent names, 107 magnet-status messages, and
155 unavailable IPv6 connection errors. Metadata was still reported as unparsed
near 21:10. This is ongoing discovery trouble, but does not prove a host-wide
network failure or a lack of seeders.

At 20:57:00, :02, :04, and :16, another torrent logged checksum failures for
newly downloaded piece 891. This indicates receipt of piece data around 20:57,
though that data failed validation. The log does not identify whether the cause
was a bad peer, local data corruption, or another fault. It does not establish
that transfers remained possible at 21:10.

Diagnostic directories exist for 20:21, 20:23, and 21:10, but their private
permissions prevent this investigation session from reading them. The latest
capture needs to be made readable by the host user before VPN state can be
correlated with these errors.

## Diagnostic capture reviewed after ownership change

The 21:10:06 capture confirms NordVPN was connected to Poland #293 over
OpenVPN TCP, with 1h22m uptime and the kill switch enabled. Routing table 205
has its default route through `nordtun`. Native nftables rules in `inet nordvpn`
apply default-drop input/output policies and permit tunnel traffic. The empty
iptables output alone must not be interpreted as a missing kill switch.
Transmission was running and its service status reported downloading 20.16 KB/s
and uploading 13.58 KB/s. NFS mounts were present, storage had 3 TB available,
and the captured kernel journal had no entries in the requested time window.
These are point-in-time observations, not a full connectivity/leak test.

A definite startup defect is now confirmed: the startup service was still
inside `nord-run.sh` after 1h22m. NordVPN rejects
`nordvpn connect --group p2p Czech_Republic` with
`P2P group is no longer available. All servers now support P2P.`
The subsequent `return 1` is invalid outside a function, and the loop immediately
retries without delay. The startup service had used almost 23 minutes of CPU;
the captured half-hour journal was about 18 MB. This loop prevents startup from
reaching its mount/config/keepalive steps and adds unnecessary load. It explains
why manual `fix-config.sh` was needed, but does not prove the cause of individual
torrent stalls. The connected Poland server differs from the script's requested
Czech server.

The repair should remove the obsolete P2P group from both connect and
autoconnect commands, replace the invalid retry logic with bounded attempts and
a delay, and make `startup.sh` stop if VPN setup fails. Enable the kill switch
before connecting. Do not restart the current startup service until repaired:
its successful continuation runs `fix-config.sh`, which restarts Transmission.
No running host services were changed during this review.

## Script repair applied in the workspace

`nord-run.sh` now targets NordVPN 6 (the captured host runs 6.0.2):

- Removed the P2P group from both connect and auto-connect commands, retaining
  Czech_Republic as the desired country.
- Replaced `set protocol tcp` plus removed `set technology openvpn` with
  `set protocol openvpn_tcp`.
- Replaced removed `set cybersec off` with `set protection off`. This setting
  controls real-time threat protection; the kill switch remains enabled.
- Enabled the kill switch before connection configuration and attempts.
- Replaced the invalid `return` loop with five attempts, each limited to 60
  seconds, separated by ten seconds. Exhaustion exits unsuccessfully.
- Added strict error handling, resolved the token relative to the script, and
  skipped login when `nordvpn account` succeeds. Removed the HOME override.
- Kept the independent IP protection check, now rejecting HTTP failures,
  malformed JSON, and any response without `protected: true`.

`startup.sh` now fails on unsuccessful VPN setup, exits unsuccessfully when run
without root, resolves its directory from the script path, and skips mounting
already-mounted NFS paths. Other failures also stop startup. `fix-config.sh`
itself still needs separate fail-fast improvements.

NordVPN documents these breaking changes in its
[Linux 6.0 announcement](https://nordvpn.com/blog/nordvpn-linux-6-0/).
Installed CLI help also confirmed `openvpn_tcp`, `protection`, and country-based
`autoconnect` syntax.

Validation: Bash syntax checks passed for all changed scripts. Mocked execution
passed successful connection, five failed attempts, unprotected response,
failed HTTP check, and malformed JSON scenarios. Tests confirmed the kill switch
is enabled before connecting and obsolete commands are absent. No live VPN
connection or service restart was performed.

### Apply on the host

The changes are in `/home/sup/code/homelab/transmission`. They do not change the
already-running shell loop. From that directory on the host, run:

```bash
sudo systemctl restart nordvpn-and-transmission.service
sudo journalctl -u nordvpn-and-transmission.service -n 60 --no-pager
nordvpn status
nordvpn settings
```

Restarting the startup service stops its old loop and executes the repaired
script. A successful startup also runs `fix-config.sh`, which copies the NFS
settings and restarts Transmission, briefly interrupting transfers. On failure,
inspect the journal; the script leaves the kill switch enabled. Confirm that
connection retries no longer flood the journal and that VPN protection succeeds.
The NFS copy of the repository is separate; these edits do not synchronize it.
