# OVH `/etc` backup investigation — 2026-11-14

Host: `ovh.bnowakowski.pl` (Europe/Warsaw). Investigated at approximately 10:36–10:45 CET on 2026-11-14. This report concerns the Zabbix item **“Number of days since last backup - etc”**.

## Result

The local `/etc` backup has not run successfully on its daily schedule since 2026-11-05. The scheduled job invokes `sudo` from `sup`'s crontab; starting on 2026-11-06, `sudo` requests a password in that noninteractive context and exits before running the backup script. A manual root run produced the newest archive on 2026-11-10 at 06:55. Thus the Zabbix value of **4 days** on 2026-11-14 reflects a real stale local archive, not an observed NAS download failure.

## Evidence from OVH

- `zabbix/number-of-days-since-last-etc-backup.sh` reads the modification date of the newest `cron/etc-backup/*.tar.gz` file. Running it on 2026-11-14 returned `4` with exit status 0. It checks the OVH copy only; it has no NAS check.
- Archives were created at 00:05 daily through 2026-11-05. The next two files are `etc-2026-11-06_09-33.tar.gz` and `etc-2026-11-10_06-55.tar.gz`; there are no later archives as of this investigation. Both manual-run archives passed `gzip -t` and `tar -tzf` listing.
- User-visible journal entries at 00:05 on **every day from November 6 through November 14** show `pam_unix(sudo:auth): conversation failed` and `auth could not identify password for [sup]`. On November 4 and 5, the same slot instead shows a successful root session for `/home/sup/code/bash_configs/ovh/cron/etc-backup.sh`.
- The old `/home/sup/code/bash_configs` path no longer exists. The repository was renamed to `homelab` on November 5 (commit `02679f3`). The exact cron entry is not readable from this restricted session, but the matching 00:05 failures and the NAS playbook's recorded old entry support the conclusion above. Whether a path-specific `NOPASSWD` sudoers rule was lost during the rename remains unverified because `/etc/sudoers*` is unreadable here.
- At 2026-11-10 06:55, the journal records an interactive `sudo /home/sup/code/homelab/ovh/cron/etc-backup.sh` root session. Its timestamp matches the newest archive. This manual success shows the script can create an archive when it has root privileges.
- Disk capacity is not the immediate blocker: the filesystem had about 33 GB free and 8% inode use.

## Existing proposed fix and deployment status

The NAS repository contains `nas/ansible/ovh-etc-backup-cron_playbook.yml` (commit `9458eff`, 2026-11-10). It installs a root-owned copy at `/usr/local/sbin/ovh-etc-backup`, schedules it in root's crontab at 00:05 with `/var/log/ovh-etc-backup.log`, removes the `sup` cron entry, runs a missed backup, and checks that today's archive is nonempty and readable.

On OVH, **neither `/usr/local/sbin/ovh-etc-backup` nor `/var/log/ovh-etc-backup.log` exists**. The playbook appears not to have been applied successfully to this host. Its remote state and exact failure, if attempted, need to be checked from NAS.

## NAS handoff

1. Check whether `nas/ansible/ovh-etc-backup-cron_playbook.yml` was run, and apply or repair it as appropriate. Confirm the root cron entry exists, the old `sup` entry is gone, a new dated OVH archive is created, `tar -tzf` succeeds, and the next scheduled run succeeds without a password prompt. Check `/var/log/ovh-etc-backup.log` after the run.
2. Investigate the **separate** NAS download pipeline on NAS: its cron execution, logs, recent local archive timestamps, and SSH/rsync exit codes. The visible `nas/cron/ovh_backup_download.sh` downloads `/backup/sup*.tar` and `/backup/admin*.tar` to `/mnt/MargokPool/archive/Backups`; it does **not** download `cron/etc-backup/*.tar.gz`. Its `--remove-source-files` option removes successfully transferred source `.tar` files, so their absence under `/backup` alone is not evidence of failure.
3. If the intended policy is to copy the `/etc` `.tar.gz` archives to NAS as well, locate the actual transfer job or add one. No such transfer appears in the NAS repository search performed here. Independently monitor the age and integrity of the NAS copy; the current OVH Zabbix item only checks local creation.

## Scope and limits

This investigation used read-only host checks and repository inspection. The restricted session cannot read `sup`'s crontab, root's crontab, `/etc/sudoers*`, or NAS runtime logs. No cron entry, backup script, or host service was changed. The unrelated pre-existing untracked file in this repository was left untouched.
