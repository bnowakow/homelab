# OVH `/etc` backup: NAS-side investigation (2026-11-14)

Follow-up to `ovh/ETC-BACKUP-INVESTIGATION-2026-11-14.md`. Zabbix item: **"Number of days since last backup - etc"** (value 4 on 2026-11-14).

## Conclusion

The root cause is on OVH. The NAS download pipeline is healthy.

## NAS download pipeline

- Cron entry `01 06 * * *` runs `nas/cron/ovh_backup_download.sh`.
- It last ran 2026-11-14 at 06:03 and downloaded `sup.2026-11-14_05-11-14.tar` and the `admin` tar. New `sup` tars arrived daily through 2026-11-14 in `/mnt/MargokPool/archive/Backups/ovh-sup`.
- `/backup` on OVH holds only `sup.log` and `admin.log` afterwards. That is expected, because `--remove-source-files` deletes transferred tars.
- The NAS does not fetch `etc-*.tar.gz` directly. Those files live in `~sup/code/homelab/ovh/cron/etc-backup/` on OVH and reach the NAS only inside the `sup` tar (`user_dir/code.tar.zst`), as in the 2024 archive layout. I did not unpack `code.tar.zst` to confirm it contains the latest archive.
- The NAS copy of `/etc` is therefore only as fresh as the newest archive on OVH.

## Root cause (OVH)

- `sup`'s crontab runs `5 00 * * * sudo /home/sup/code/homelab/ovh/cron/etc-backup.sh`.
- Since 2026-11-06, `sudo` asks for a password in cron, so the job fails every night.
- Probable trigger: the repo rename `bash_configs` -> `homelab` (commit `02679f3`, 2026-11-05) broke a path-specific NOPASSWD sudoers rule. Unverified, because `/etc/sudoers*` needs a password to read.
- Newest archives on OVH: `etc-2026-11-06_09-33` and `etc-2026-11-10_06-55`, both made by manual runs.

## Fix

`nas/ansible/ovh-etc-backup-cron_playbook.yml` (commit `9458eff`) installs a root-owned copy at `/usr/local/sbin/ovh-etc-backup`, schedules it in root's crontab at 00:05, removes the old `sup` entry, runs a missed backup and verifies the archive.

As of 2026-11-14 it had not been applied: `/usr/local/sbin/ovh-etc-backup` and `/var/log/ovh-etc-backup.log` do not exist on OVH. It needs the sudo password, so run it interactively:

```
cd ~/code/homelab/nas/ansible && ansible-playbook -i inventory/ovh.yml ovh-etc-backup-cron_playbook.yml -K
```

Verify afterwards: `/var/log/ovh-etc-backup.log` exists, a new dated archive is on OVH, and the next 00:05 run succeeds without a password prompt.

## Notes

- A manual one-off from the NAS (`sudo .../etc-backup.sh`) fails with "command not found" and would back up the NAS's own `/etc`. It must run on OVH.
- The Zabbix item checks only archive age on OVH. Nothing monitors the NAS copy of the `/etc` archives.

## Deployment log

- 2026-11-14, first attempt: `ansible-playbook ovh-etc-backup-cron_playbook.yml -K` (no `-i`) did nothing. Ansible found no inventory, so `ovh.bnowakowski.pl` did not match and the play was skipped ("no hosts matched", empty recap). The host is defined in `inventory/ovh.yml`, so the playbook needs `-i inventory/ovh.yml`.
- 2026-11-14 10:50 CET, check on OVH after that attempt: still not applied. `/usr/local/sbin/ovh-etc-backup` and `/var/log/ovh-etc-backup.log` are missing, `sup`'s crontab still has the old `sudo` line, and the newest archive is still `etc-2026-11-10_06-55.tar.gz`.
- 2026-11-14 ~10:51 CET, rerun with `-i inventory/ovh.yml -K`: `ok=12 changed=4 failed=0`. Changed tasks: root-owned script install, root cron entry, removal of the old `sup` cron line, and the missed backup run.
- Verified on OVH right after: `/usr/local/sbin/ovh-etc-backup` exists (root, 0700), `sup`'s crontab no longer has the `etc-backup.sh` line, `etc-2026-11-14_10-51.tar.gz` was created (10.9 MB), and the Zabbix script returns `0`.
- `/var/log/ovh-etc-backup.log` does not exist yet. The playbook's catch-up run calls the script directly, without the cron redirect, so the file will only appear after the first scheduled run.
- Still to confirm: after the 00:05 run on 2026-11-15, check that `etc-2026-11-15_00-05.tar.gz` exists and `/var/log/ovh-etc-backup.log` is created without errors.
