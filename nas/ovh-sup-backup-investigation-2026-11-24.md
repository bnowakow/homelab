# OVH `sup` backup stale on NAS (2026-11-24)

The TrueNAS Zabbix item **“Number of days since last backup - ovh sup”** reported **4 days** on 2026-11-24. The item runs `zabbix/number-of-days-since-last-backup.sh ovh-sup` and measures the calendar age of the newest local tar, not the age of an OVH `/etc` archive.

## Evidence from the NAS

- The newest `/mnt/MargokPool/archive/Backups/ovh-sup/sup*.tar` is `sup.2026-11-20_05-11-14.tar` (10,316,554,240 bytes; local mtime 2026-11-20 06:04:55 CET). Running the item script returned `4` on 2026-11-24.
- `ovh-admin` has a tar for every day through 2026-11-24. The November 21–24 files have local mtimes around 06:01 each day. This shows the NAS download job continued reaching OVH and retrieving the other account's backup.
- `cron/ovh_backup_download.sh` checks `/backup/sup*.tar` first, then `/backup/admin*.tar`. If the remote `sup` glob has no match and the local file is older than 26 hours, the script reports an error and continues immediately to `admin`. If `sup` rsync fails, it retries three times with five-minute waits before proceeding. The 06:01 `admin` arrivals fit the missing-remote-tar branch much better than the rsync-failure branch.
- The NAS archive filesystem had 4.1 TiB free and ample free inodes at investigation time.

**Assessment:** OVH failing to create or expose the `sup` tar is the strongest lead. The specific OVH failure is **unconfirmed**. This session could not inspect OVH: outbound SSH returned `socket: Operation not permitted`. NAS cron and job logs were also unreadable from this restricted session. A separate NAS-side condition affecting only `sup` cannot be completely excluded without those logs and a direct OVH check.

## What to check on OVH

1. Inspect `/backup/sup*.tar`, `/backup/sup.log`, the backup job schedule, and logs for November 21–24. If a `sup` tar exists there, check whether it is complete and whether NAS rsync reported an error.
2. Inspect the OVH backup process for a stuck or failed `sup` backup. The NAS download script has a historical comment that a failed VestaCP backup may remain unfinished and block later runs (commit `6352f70`, 2022). Confirm from OVH logs before repairing or clearing any state.
3. After fixing the source issue, run the normal NAS download job and verify a new `sup` tar appears locally and the Zabbix item returns `0` or `1` as expected for the date. The download uses `rsync --remove-source-files`, so a successfully transferred tar will disappear from `/backup` on OVH.

This is distinct from the OVH `/etc` backup failure documented in `ovh-etc-backup-investigation-2026-11-14.md`; that issue concerns an archive nested inside the `sup` backup.
