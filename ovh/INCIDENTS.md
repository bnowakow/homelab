# OVH incidents

## 2026-11-24 — TrueNAS reported a stale OVH sup backup

TrueNAS reported four days since the last `sup` backup. The last successful archive was from November 20. Daily `admin` archives continued arriving on TrueNAS. Hestia logged that it skipped `sup` on November 24 because it needed 31,892 MiB of free space and had 28,754 MiB. [NAS investigation](../nas/ovh-sup-backup-investigation-2026-11-24.md)

The repository gained an OVH-only pre-backup disk-space check. At the November 24 follow-up, the installed script was still absent and recovery had yet to be verified. [Deployment and recovery](KNOWN-PROBLEMS.md#hestia-backup-space)

## 2026-11-24 — MariaDB image alert compared different release series

Zabbix reported `false,mariadb:10.11,13.0.2`. The running containers were `seafile-db-1` on `10.11` and `shared-mysql` on `13.0.2`. The previous checker compared both to the same newest tag. The repository checker now identifies the outdated containers by name and evaluates Seafile's database against the 10.11 series. Live Zabbix verification was still pending. [Checker](zabbix/is-docker-image-up-to-date.sh)

## 2026-11-14 — OVH /etc backup was four days old

The 00:05 job in `sup`'s crontab failed because `sudo` requested a password in cron. A manual backup on November 10 was the newest archive when the alert fired. The playbook moved the job to root's crontab and produced a fresh archive. Daily local archives were present from November 15 through November 24 when checked on November 24. [OVH investigation](ETC-BACKUP-INVESTIGATION-2026-11-14.md) · [NAS deployment record](../nas/ovh-etc-backup-investigation-2026-11-14.md)
