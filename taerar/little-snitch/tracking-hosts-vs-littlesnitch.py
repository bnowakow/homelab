#!/usr/bin/env python3
"""Compare Pi-hole/AdGuard blocklist hosts with Little Snitch coverage.

Downloads the default Pi-hole list (StevenBlack unified hosts), the AdGuard DNS
filter and malware, phishing and scam lists (HaGeZi TIF, DandelionSprout, Prigent, RPiList, URLhaus filter, CERT Polska, Phishing Army, DurableNapkin), then reports which of their hosts Little Snitch does not already block.
Python 3 standard library only.

Little Snitch coverage comes from a model exported with:
    sudo littlesnitch export-model ls-model.json
(needs Little Snitch > Settings > Security > command line tool access enabled).
Only deny rules with remote domains/hosts count as coverage; blocklist
subscriptions keep their contents outside the model, so they are listed by group
and assumed to cover nothing unless --assume-blocklist-covers is given.

Malware and phishing hits are printed in red on a terminal (NO_COLOR disables colors).
Restrict the report to hosts you actually contact with --traffic FILE (CSV from
`sudo littlesnitch log-traffic`, includes the app responsible) or --observed FILE
(one hostname per line); this is the useful "monitoring" view.
"""
import argparse
import csv
import json
import os
import re
import sys
import urllib.request

SOURCES = {  # name: (category, url)
    "pihole-default (StevenBlack)": ("ads/tracking", "https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts"),
    "adguard-dns-filter": ("ads/tracking", "https://adguardteam.github.io/AdGuardSDNSFilter/Filters/filter.txt"),
    "hagezi threat-intelligence": ("malware", "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/wildcard/tif-onlydomains.txt"),
    "dandelionsprout anti-malware": ("malware", "https://raw.githubusercontent.com/DandelionSprout/adfilt/master/Alternate%20versions%20Anti-Malware%20List/AntiMalwareHosts.txt"),
    "prigent malware": ("malware", "https://v.firebog.net/hosts/Prigent-Malware.txt"),
    "rpilist malware": ("malware", "https://raw.githubusercontent.com/RPiList/specials/master/Blocklisten/malware"),
    "urlhaus-filter (malware-filter)": ("malware", "https://malware-filter.gitlab.io/malware-filter/urlhaus-filter-hosts.txt"),
    "cert.pl warning list": ("phishing", "https://hole.cert.pl/domains/v2/domains_hosts.txt"),
    "phishing-army": ("phishing", "https://phishing.army/download/phishing_army_blocklist.txt"),
    "durablenapkin scam": ("scam", "https://raw.githubusercontent.com/durablenapkin/scamblocklist/master/hosts.txt"),
}
IP_RE = re.compile(r"^(\d{1,3}(\.\d{1,3}){3}|[0-9a-f:]*:[0-9a-f:]*)$")
HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9._-]*[a-z0-9])?$")
LOCAL = {"localhost", "localhost.localdomain", "local", "broadcasthost", "ip6-localhost",
         "ip6-loopback", "ip6-localnet", "ip6-mcastprefix", "ip6-allnodes", "ip6-allrouters",
         "ip6-allhosts", "0.0.0.0"}


ALERT = {"malware", "phishing"}  # categories shown in red
RED, RESET = "\033[31m", "\033[0m"
USE_COLOR = sys.stdout.isatty() and "NO_COLOR" not in os.environ


def paint(text, category):
    return f"{RED}{text}{RESET}" if USE_COLOR and category in ALERT else text


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "tracking-hosts-check/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8", "replace")


def parse(text):
    """Return (blocked, allowed) host sets from hosts-file or adblock syntax."""
    blocked, allowed = set(), set()
    for raw in text.splitlines():
        line = raw.strip().lower()
        if not line or line[0] in "#!":
            continue
        fields = line.split("#")[0].split()
        if len(fields) >= 2 and IP_RE.match(fields[0]):  # hosts file: any IP then name
            host = fields[1]
            if host not in LOCAL and HOST_RE.match(host):
                blocked.add(host)
            continue
        if HOST_RE.match(line) and "." in line and line not in LOCAL:  # plain domain list
            blocked.add(line)
            continue
        exception = line.startswith("@@")
        if exception:
            line = line[2:]
        if line.startswith("||") and line.endswith("^"):
            host = line[2:-1]
            if HOST_RE.match(host) and "." in host:
                (allowed if exception else blocked).add(host)
    return blocked, allowed


def littlesnitch_denied(model):
    """Collect domains/hosts from active deny rules; return (domains, hosts)."""
    domains, hosts = set(), set()
    for rule in model.get("rules", []):
        if rule.get("action") != "deny" or rule.get("disabled"):
            continue
        for key, target in (("remote-domains", domains), ("remote-hosts", hosts)):
            for item in re.split(r"[,\s]+", rule.get(key, "")):
                if item:
                    target.add(item.lower().lstrip("."))
    return domains, hosts


def is_covered(host, domains, hosts):
    if host in hosts:
        return True
    parts = host.split(".")
    return any(".".join(parts[i:]) in domains for i in range(len(parts)))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("model", help="Little Snitch export-model JSON file")
    ap.add_argument("--observed", help="file with hostnames this Mac contacted; limit report to these")
    ap.add_argument("--traffic", help="CSV from `sudo littlesnitch log-traffic -b 'YYYY-MM-DD HH:MM:SS' > file`; "
                    "report listed hosts this Mac contacted, with the app that did")
    ap.add_argument("--out", help="write uncovered hosts (sorted) to this file")
    ap.add_argument("--assume-blocklist-covers", action="store_true",
                    help="treat hosts as covered when any blocklist group is active")
    args = ap.parse_args()

    with open(args.model) as fh:
        model = json.load(fh)
    domains, hosts = littlesnitch_denied(model)
    blocklist_groups = {gid: g for gid, g in model.get("groups", {}).items()
                        if g.get("isActive") and g.get("type", "") not in
                        ("builtinMacOSServices", "builtinICloudServices", "loadedFromFile")}
    print(f"Little Snitch: {len(domains)} denied domains, {len(hosts)} denied hosts, "
          f"{len(blocklist_groups)} active subscribed blocklist group(s)")
    if args.assume_blocklist_covers and blocklist_groups:
        print("  assuming active blocklist groups cover the lists below", file=sys.stderr)

    blocked, allowed, category = set(), set(), {}
    for name, (cat, url) in SOURCES.items():
        try:
            b, a = parse(fetch(url))
        except OSError as err:
            print(f"{name}: FAILED ({err})", file=sys.stderr)
            continue
        print(f"{name} [{cat}]: {len(b)} hosts ({len(a)} exceptions)")
        blocked |= b
        allowed |= a
        for h in b:  # malware/phishing outrank ads/tracking in the report
            if category.get(h) in (None, "ads/tracking"):
                category[h] = cat
    blocked -= allowed
    print(f"Unique tracking/ad hosts after exceptions: {len(blocked)}")

    contacts = {}  # host -> [connect count, set of app names]
    if args.traffic:
        with open(args.traffic, newline="") as fh:
            for row in csv.DictReader(fh):
                host = row["remoteHostname"].strip().lower()
                if row["direction"] != "out" or not host:
                    continue
                app = row["parentAppExecutable"] or row["connectingExecutable"]
                entry = contacts.setdefault(host, [0, set()])
                entry[0] += int(row["connectCount"] or 0)
                entry[1].add(os.path.basename(app))
    if args.observed or args.traffic:
        seen = set(contacts)
        if args.observed:
            with open(args.observed) as fh:
                seen |= {l.strip().lower() for l in fh if l.strip()}
        blocked = {h for h in blocked if any(s == h or s.endswith("." + h) for s in seen)}
        print(f"Of those, contacted by this Mac: {len(blocked)}")

    if args.assume_blocklist_covers and blocklist_groups:
        uncovered = set()
    else:
        uncovered = {h for h in blocked if not is_covered(h, domains, hosts)}
    print(f"Not blocked by Little Snitch: {len(uncovered)}")
    for cat in sorted({category[h] for h in uncovered}):
        print(paint(f"  {cat}: {sum(1 for h in uncovered if category[h] == cat)}", cat))
    if contacts:
        rows = []
        for seen_host, (count, apps) in contacts.items():
            parts = seen_host.split(".")
            match = next((".".join(parts[i:]) for i in range(len(parts))
                          if ".".join(parts[i:]) in uncovered), None)
            if match:
                rows.append((count, seen_host, category[match], sorted(apps)))
        print(f"\nContacted listed hosts ({len(rows)}), most connections first:")
        for count, seen_host, cat, apps in sorted(rows, reverse=True):
            print(paint(f"  {count:6d}  {cat:<12} {seen_host}  <- {', '.join(apps)}", cat))
    if args.out:
        with open(args.out, "w") as fh:
            fh.write("".join(f"{category[h]}\t{h}\n" for h in sorted(uncovered)))
        print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
