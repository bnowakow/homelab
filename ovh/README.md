# OVH

## Hestia backup disk-space check

Hestia stopped creating the `sup` archive on November 24, 2026 because its backup requires about twice the estimated size of the account in free space. It reported 28,754 MiB available against a requirement of 31,892 MiB. TrueNAS reported a four-day-old backup after the last successful archive on November 20.

[The disk-space script](cron/hestia-backup-space.sh) uses twice the size of `/home/sup` plus 4 GiB as its target on `/backup`. If space is low, it cleans the APT download cache, measures again, and cleans `sup`'s npm download cache if needed. It logs to syslog using the `hestia-backup-space` tag and exits with an error if free space stays below the target.

The [OVH Ansible playbook](../nas/ansible/ovh-hestia-backup-space_playbook.yml) runs from the OVH section of [NAS provisioning](../nas/ansible/provision.sh). It installs a root-owned copy at `/usr/local/sbin/hestia-backup-space` and adds the root crontab entry at 04:55 if missing, ahead of the 05:10 Hestia backup.

To check disk space without cleaning caches, run this on OVH after deployment:

```bash
sudo /usr/local/sbin/hestia-backup-space --check
```

Hestia's backup results are in `/var/log/hestia/backup.log`. Check the TrueNAS **Number of days since last backup - ovh sup** item after the 06:01 download to verify that the new archive arrived.

## Zabbix Docker image checks

The [image checker](zabbix/is-docker-image-up-to-date.sh) checks each running container for the requested image repository. It compares the running tag with the highest plain `x.y.z` tag it finds on Docker Hub. The check returns `true` when all matching containers use that tag. Otherwise, it returns `false,<outdated containers>,<newest tag>`, with each outdated container shown as `name=image:tag`. An empty middle field means there were no matching running containers.

For example, `false,seafile-db-1=mariadb:10.11,13.0.2` identifies the Seafile database container as running the `10.11` tag. This check compares tags across release series. Checking whether a container on a floating tag such as `10.11` has the latest image for that series requires a digest comparison.

The [OVH agent configuration](zabbix/zabbix-ovh.conf) runs the checker from the agent's checkout at `/etc/zabbix/zabbix_agent2.d/homelab`. Update that checkout to deploy checker changes.
