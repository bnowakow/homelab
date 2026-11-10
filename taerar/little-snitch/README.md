# Little Snitch tracking-host monitoring

[`tracking-hosts-vs-littlesnitch.py`](tracking-hosts-vs-littlesnitch.py) checks whether the hosts that Pi-hole and
AdGuard Home block are also blocked in [Little Snitch](https://obdev.at/littlesnitch)
on this Mac, and which of them this Mac actually contacts. It requires Python 3,
with no packages, and downloads the lists on every run. More features will be
added to this tool; this section is the reference for all of them.

## Prerequisites

Little Snitch's command line tool is bundled but not on `PATH`. Link it once:

```sh
sudo ln -s "/Applications/Little Snitch.app/Contents/Components/littlesnitch" /usr/local/bin/littlesnitch
```

The tool refuses to change or export anything until you enable it in Little Snitch >
Settings > Security (command line tool access). That is a security setting, so
turn it on yourself, and consider turning it off again afterwards. Both commands
below also need `sudo`.

## Inputs

Both files contain private data (your full rule set and connection history). They are
listed in `.gitignore`; keep them out of commits.

```sh
# Rules, groups and blocklist subscriptions (JSON).
sudo littlesnitch export-model taerar/little-snitch/ls-model.json

# Connection history (CSV): date, direction, remoteHostname, executable, counts.
sudo littlesnitch log-traffic -b "$(date -v-1d '+%Y-%m-%d %H:%M:%S')" > taerar/little-snitch/ls-traffic-sample.txt
```

`log-traffic` only prints history when `-b` is given. Many rows have no
`remoteHostname` (bare IP addresses), and those cannot be matched against host lists.

## Usage

From this directory, `make run` runs the tool with the default inputs
(`ls-model.json` and `ls-traffic-sample.txt` next to the script). Override with
`make run MODEL=other.json TRAFFIC=other.txt ARGS='--out uncovered.tsv'`.

Or run the script directly, from the repository root (the `ls-model.json` and `ls-traffic-sample.txt` files below live next to the script):

```sh
# Coverage only: which listed hosts are not blocked in Little Snitch.
./taerar/little-snitch/tracking-hosts-vs-littlesnitch.py taerar/little-snitch/ls-model.json

# Only hosts this Mac contacted, with the app responsible, most connections first.
./taerar/little-snitch/tracking-hosts-vs-littlesnitch.py taerar/little-snitch/ls-model.json --traffic taerar/little-snitch/ls-traffic-sample.txt

# Plain list of hostnames (one per line) instead of a traffic CSV.
./taerar/little-snitch/tracking-hosts-vs-littlesnitch.py taerar/little-snitch/ls-model.json --observed hosts.txt

# Write the uncovered hosts as `category<TAB>host`.
./taerar/little-snitch/tracking-hosts-vs-littlesnitch.py taerar/little-snitch/ls-model.json --out uncovered.tsv
```

| Option | Meaning |
| --- | --- |
| `model` | Little Snitch `export-model` JSON (required). |
| `--traffic FILE` | `log-traffic` CSV; report matches with the responsible app. |
| `--observed FILE` | Hostnames, one per line; limit the report to these. |
| `--out FILE` | Write uncovered hosts, sorted, as `category<TAB>host`. |
| `--assume-blocklist-covers` | Treat hosts as covered when a subscribed blocklist group is active. |

## Sources

| Source | Category | Format |
| --- | --- | --- |
| [StevenBlack unified hosts](https://github.com/StevenBlack/hosts) (Pi-hole default) | ads/tracking | hosts file |
| [AdGuard DNS filter](https://adguard-dns.io/kb/general/dns-filters/) | ads/tracking | adblock `\|\|domain^` |
| [HaGeZi Threat Intelligence Feeds](https://github.com/hagezi/dns-blocklists) (`wildcard/tif-onlydomains.txt`) | malware | plain domains (subdomains implied) |
| [DandelionSprout Anti-Malware](https://github.com/DandelionSprout/adfilt) | malware | hosts file |
| [Prigent Malware](https://dsi.ut-capitole.fr/blacklists/) via [Firebog](https://firebog.net) | malware | hosts file |
| [RPiList malware](https://github.com/RPiList/specials) | malware | hosts file |
| [URLhaus filter](https://gitlab.com/malware-filter/urlhaus-filter) (malware-filter) | malware | hosts file |
| [CERT Polska warning list](https://cert.pl/lista-ostrzezen/) | phishing | hosts file (real IPs, not 0.0.0.0) |
| [Phishing Army](https://phishing.army/) | phishing | plain domains |
| [DurableNapkin scam blocklist](https://github.com/durablenapkin/scamblocklist) | scam | hosts file |

A host in several lists takes the stronger category (malware, phishing or scam over
ads/tracking). AdGuard `@@` exceptions are removed from the combined set. The
abuse.ch URLhaus and ThreatFox hostfiles were dropped because their URLs return HTTP 410; the
URLhaus filter above is a maintained derivative.
Add a source by editing `SOURCES` in the script.

## How coverage is decided

- A host counts as blocked only when Little Snitch has an enabled deny rule whose
  remote domains or remote hosts match it or a parent domain.
- Subscribed blocklists keep their contents outside the exported model, so they are
  listed by group but assumed to cover nothing unless `--assume-blocklist-covers`.
- Allow rules, "ask" rules and Apple rule groups never count as coverage.

## Current findings (2026-11-10)

This Mac's Little Snitch has 175 allow rules and 1 ask rule, with no deny rules and no
blocklist subscriptions, so all ~1.86M listed hosts are unblocked. A 33-minute traffic
sample contacted 9 listed hosts, all ads/tracking, none malware or phishing. Most
were Epic Games and Dia telemetry. Some telemetry may be needed by its app, so check
before denying.

## Limitations and ideas

- Listed hosts that appear only as IP addresses in the traffic log are not detected.
- Coverage ignores wildcard or regex rules and Little Snitch profiles.
- Ideas: generate Little Snitch deny rules or a blocklist file from the report,
  run on a schedule and alert on new hits, and show hits per app over time.
