# OVH

- [Incidents](INCIDENTS.md) records dated alerts, their causes, and recovery status.
- [Known problems](KNOWN-PROBLEMS.md) covers outstanding work and limitations in the OVH tooling.

## Hestia backup disk-space check

[The disk-space script](cron/hestia-backup-space.sh) uses twice the size of `/home/sup` plus 4 GiB as its target on `/backup`. If space is low, it cleans the APT download cache, measures again, and cleans `sup`'s npm download cache if needed. It logs to syslog using the `hestia-backup-space` tag and exits with an error if free space stays below the target.

The [OVH Ansible playbook](../nas/ansible/ovh-hestia-backup-space_playbook.yml) runs from the OVH section of [NAS provisioning](../nas/ansible/provision.sh). It installs a root-owned copy at `/usr/local/sbin/hestia-backup-space` and adds the root crontab entry at 04:55 if missing, ahead of the 05:10 Hestia backup.

To check disk space without cleaning caches, run this on OVH after deployment:

```bash
sudo /usr/local/sbin/hestia-backup-space --check
```

## OVH /etc archives and NAS transfer

The [local /etc backup](cron/etc-backup.sh) creates dated gzip archives in `cron/etc-backup/`. The directory holds generated backup data and is Git-ignored. The script trims older archives before creating the next one, so a successful run can leave 11 files. The [OVH /etc cron playbook](../nas/ansible/ovh-etc-backup-cron_playbook.yml) installs a root-owned copy at `/usr/local/sbin/ovh-etc-backup`, schedules it for 00:05, and sends cron output to `/var/log/ovh-etc-backup.log`. The November 14 investigations record the original failure and the deployment of the fix. [OVH investigation](ETC-BACKUP-INVESTIGATION-2026-11-14.md) · [NAS deployment record](../nas/ovh-etc-backup-investigation-2026-11-14.md)

The [NAS downloader](../nas/cron/ovh_backup_download.sh) runs at 06:01 and pulls the Hestia `sup` and `admin` archives from OVH's `/backup` into separate directories under `/mnt/MargokPool/archive/Backups`. After a successful transfer, rsync removes the source archive. The transfer fetches Hestia tar files. The local `/etc` archives can travel inside the `sup` backup as part of its home directory.

## Zabbix Docker image checks

The [image checker](zabbix/is-docker-image-up-to-date.sh) checks each running container for the requested image repository. It compares the running tag with the highest plain `x.y.z` tag it finds in up to eight pages of Docker Hub results. The check returns `true` when all matching containers use that tag. Otherwise, it returns `false,<outdated containers>,<newest tag>`, with each outdated container shown as `name=image:tag`. An empty middle field means there were no matching running containers.

The checker accepts optional container name and release series arguments. It checks the Seafile MariaDB Compose service against 10.11 and reads the running patch version from `MARIADB_VERSION` for the floating `10.11` tag.

The [OVH agent configuration](zabbix/zabbix-ovh.conf) runs the checker from the agent's checkout at `/etc/zabbix/zabbix_agent2.d/homelab`. Update that checkout to deploy checker changes.

## Other Zabbix checks

| Check | What it measures |
| --- | --- |
| [APT updates](zabbix/is-apt-up-to-date.sh) | Refreshes package metadata and simulates an upgrade using noninteractive sudo. Reports pending and held-back packages, or an error if either command fails. |
| [MySQL dump age](zabbix/number-of-days-since-last-mysql-backup.sh) | Calendar days since the newest matching local SQL dump in `/home/sup/docker/mysql/data/mysql-dumps`. The filename prefix defaults to `wordpress`. |
| [/etc archive age](zabbix/number-of-days-since-last-etc-backup.sh) | Calendar days since the newest local `cron/etc-backup/*.tar.gz` archive. |

## Docker maintenance and diagnostics

- [Interactive image upgrades](upgrade-docker-images.sh) cover the WordPress deployments for `bnowakowski.pl` and `n2nieruchomosci.pl`, plus `shared-mysql`. The script shows Compose and running tags, asks before editing Compose and running `sudo make upgrade`, and checks the WordPress sites over HTTPS. It asks separately before staging, committing, and pushing all listed deployment changes. The upgrade log defaults to `/tmp/upgrade-docker-images-<uid>.log`.
- The [Docker resource monitor](docker-resource-monitor/README.md) records host and container metrics every 30 seconds and captures container lifecycle events in `/var/log/docker-resource-monitor/`. Its report shows the ten minutes before and after an incident timestamp, including relevant kernel messages. The bundled logrotate configuration keeps 21 daily rotations.
- The [volume inventory script](list-docker-volumes-by-container.sh) lists mounts for running and stopped containers. The [volume removal script](remove-docker-volumes-not-used-by-containsers.sh) attempts to delete volumes it considers unused, with no preview. See [known problems](KNOWN-PROBLEMS.md#docker-volume-removal) before using it.

## Host maintenance and troubleshooting

| Helper | Purpose and effects |
| --- | --- |
| [Freeze diagnostics](doolToDebugSystemFreezes.sh) | Runs the installed `dool` command with sudo, taking a sample every five seconds and writing dated CSV files under `dool/`. That directory holds historical captures and the upstream `dool/dool` Git submodule. |
| [Syslog around reboot](showSyslogBeforeReboot.sh) | Shows lines around kernel boot markers in `/var/log/syslog` using `less`. |
| [Swap file setup](turn-on-swap-as-file.sh) | As root, creates and activates a roughly 4 GB swap file at `/var/swap.img`. It prints the entry for `/etc/fstab` so an operator can make it persistent. |
| [APT keyring refresh](update-apt-signed-by.sh) | Downloads the Sury Apache, Sury PHP, and Hestia archive keys, updates their keyrings under `/usr/share/keyrings`, and refreshes APT metadata. |
| [Temporary root SSH login](enableRootLoginTemporairly.sh) | Legacy manual helper. Changes `PermitRootLogin` and rewrites root's authorized-keys comments. Its effective delay is five seconds, after which it sets root login to `no` and comments all key lines. See [known problems](KNOWN-PROBLEMS.md#temporary-root-ssh-login). |
