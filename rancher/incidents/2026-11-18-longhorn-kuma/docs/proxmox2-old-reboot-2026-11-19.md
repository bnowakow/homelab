# Proxmox2-old controlled reboot — 19 November 2026

## Outcome and current state

Operator-run checks confirm the host returned, all three etcd endpoints were
healthy on three consecutive checks, Kubernetes reports Ready,SchedulingDisabled,
and both Longhorn managers are Ready. Standalone watchdog and PostgreSQL containers
returned healthy; Scrutiny returned Running (no health check result supplied).
Old-host I/O PSI avg10 some/full was 0.00/0.00 after return.

All 48 attached volumes now have one running/RW replica on proxmox3. Of those,
47 are degraded at desired count two; Kuma is healthy at desired count one.
Before reboot, six volumes had RW replicas on both hosts. Their old-host copies
have not rejoined in the supplied post-reboot snapshot. This does not establish
that replica data was deleted or lost; the cause of non-rejoin remains unchecked.
The six volumes are AdGuard config/data, Scrutiny config, Piwigo MariaDB, Jenkins
data and DuckDNS config. Four other volumes remain detached/unknown.

Keep proxmox3 as the temporary storage location per operator decision. Retain
proxmox2-old's cordon and maintenance taint, with Longhorn node AND disk scheduling
disabled. Retain stopped replica records and desired replica counts. No eviction,
forced drain, volume migration, replica rebuild or membership change was performed.
Do not reboot proxmox3 while the attached volumes depend on its sole running copies.
Storage redundancy and the underlying I/O/filesystem incident remain unresolved.

## Pre-reboot evidence and decision

At 18:35 UTC, the operator ran the quorum and redundancy collectors on proxmox3:

- `/var/tmp/etcd-membership-assessment-20261119T183519364948Z`
- `/var/tmp/longhorn-redundancy-assessment-20261119T183519539997Z`

Etcd had three non-learner voters: proxmox2-old (10.0.0.72), proxmox5
(10.0.0.80), proxmox3 (10.0.0.50). All endpoints were healthy, reporting revision
51182199, term 839 and applied index 56362754. Proxmox2-old was leader before
reboot; no claim about the post-reboot leader is made here. Proxmox5's absence
from the Kubernetes Node list does not negate its etcd membership.

All attached volumes had RW copies on proxmox3. No application pods were listed
on proxmox2-old; Fleet agent, system upgrade controller, service load balancer
pods and Longhorn components remained. The supplied Job list showed historical
failed jobs, not a running backup. `qm list` and `pct list` supplied no guest rows.
Docker listed watchdog, PostgreSQL and Scrutiny, whose temporary interruption
was accounted for. Upgrades were instructed to remain stopped during the reboot.

A fourth voter was not added: three and four voting members each tolerate one
unavailable voter; four require three for quorum. The authorized operation was
a reboot of the existing member, not retirement or replacement. Operator ran
`sudo systemctl reboot` on proxmox2-old.

## Observed return

Direct monitoring from the agent environment was blocked by network restrictions
and sudo's no-new-privileges restriction. The operator instead ran the read-only
`watch-etcd-return.py` helper on proxmox3. During reboot it reported connection
refused/no route to the old endpoint. All three endpoints then passed without
collector errors at 18:42:03, 18:42:13 and 18:42:24 UTC. Final quorum evidence:
`/var/tmp/etcd-membership-assessment-20261119T184223954704Z`.

Post-reboot storage evidence:
`/var/tmp/longhorn-redundancy-assessment-20261119T184341637917Z`.
Old-host Longhorn Ready transition was 18:42:18 UTC. Both manager pods were Ready;
the old manager container restart count increased from one to two and its
pre-pull container from zero to one. Proxmox3 counts remained ten/six in the
supplied snapshots. The existing OnDelete strategy, mixed revisions (1/2 updated)
and unpaused/NotReady Longhorn Fleet Bundle persisted. Kuma Fleet remained Ready.
No manager rollout was forced to clear that status.

Docker containers reported Up 2 minutes; watchdog and PostgreSQL were healthy.
PSI on the old host was some avg10=0.00 avg60=0.23 avg300=0.26;
full avg10=0.00 avg60=0.12 avg300=0.14. This is a snapshot, not a storage repair
or long-term stability finding. Live filesystem integrity and notification
transport were not retested by these reboot checks.

## Repeatable read-only checks

From the repository's `rancher` directory on proxmox3:

```bash
sudo python3 incidents/2026-11-18-longhorn-kuma/scripts/watch-etcd-return.py
sudo kubectl --kubeconfig=/etc/rancher/k3s/k3s.yaml get nodes
sudo python3 incidents/2026-11-18-longhorn-kuma/scripts/assess-longhorn-redundancy.py
```

On proxmox2-old:

```bash
sudo docker ps --format 'table {{.Names}}\t{{.Status}}'
cat /proc/pressure/io
```

The watcher waits for three healthy checks and times out after 15 minutes.
Its ten-second pause occurs after each collector invocation; failed collector
calls can make the effective sampling interval longer. It does not authorize
another reboot or establish Docker/storage health. Keep private evidence outside
Git. Any later PVC copying still requires the standing cluster-wide maintenance
and transfer-cap records described in the [maintenance procedure](../../../longhorn/README-5-pvc-migration-maintenance.md).
