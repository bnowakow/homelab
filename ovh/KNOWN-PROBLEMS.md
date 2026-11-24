# OVH known problems

## Hestia backup space

Hestia needs about twice its estimated backup size in free space on `/backup`. The OVH playbook installs a 04:55 preflight check, but `/usr/local/sbin/hestia-backup-space` was absent at the November 24 follow-up. The script uses twice the full home directory size plus 4 GiB as an approximation. It can still leave the backup short of space when disposable package caches are small. A failed check writes to syslog and stderr. [Space check](cron/hestia-backup-space.sh) · [Playbook](../nas/ansible/ovh-hestia-backup-space_playbook.yml)

To verify recovery after deployment, run the space check on OVH, confirm a successful `sup` archive in `/var/log/hestia/backup.log`, then check that the TrueNAS backup age resets after transfer. The downloader removes transferred source archives, so an empty `/backup` alone cannot establish whether a run succeeded.

## Docker image check limits

The checker scans up to eight Docker Hub tag pages and considers plain numeric release tags. Results depend on which tags appear in those pages. It compares versions and cannot detect image rebuilds with the same version tag. When a generic MariaDB check reports several containers across different release series, the final field is the generic target. [Checker](zabbix/is-docker-image-up-to-date.sh)

## Docker volume removal

The helper attempts deletion immediately. Its mount parser can concatenate names when a container has multiple named volumes, which can make attached volumes appear as deletion candidates. Verify candidates independently before using the helper. [Volume inventory](list-docker-volumes-by-container.sh) · [Volume removal helper](remove-docker-volumes-not-used-by-containsers.sh)

## Temporary root SSH login

The legacy script rewrites root's authorized-keys comments and sets a fixed final `PermitRootLogin` value. It can alter the original configuration even on completion, and interruption can leave the temporary state in effect. [Root SSH helper](enableRootLoginTemporairly.sh)
