# Longhorn / Uptime Kuma incident — 18–19 November 2026

## Start here

- [Current handoff and temporary storage decision](docs/kuma-recovery-handoff-2026-11-19.md)
- [Problems and investigation findings](docs/proxmox2-old-recovery-2026-11-18.md)
- [Detailed recovery chronology and evidence](docs/longhorn-shim-recovery-2026-11-18.md)

The incident included read-only ext4 filesystems, stale container runtime tasks,
Longhorn attachment problems and severe I/O pressure. The underlying cause remains
unknown. Kuma was preserved, recovered on a disposable image, filesystem/database
validated, repaired on the original volume, and restored through Fleet.
Applications and standalone Docker services were restored afterward.

## Current disposition

Keep proxmox3 as the temporary storage location. After the old-host reboot,
all 48 attached volumes have one running/RW copy on proxmox3: 47 degraded at
desired count two, Kuma healthy at desired count one. The six previously running
old-node copies have not rejoined in the supplied snapshot; do not delete their
records or lower desired counts. Proxmox2-old is Ready but cordoned, with Longhorn
node/disk scheduling disabled. Etcd and standalone Docker returned successfully.
Storage redundancy and the underlying incident cause remain unresolved.

See the [reboot evidence, timeline and checks](docs/proxmox2-old-reboot-2026-11-19.md).

## Scripts

All incident helpers are in `scripts/`. Run from the repository root, for example:

```bash
sudo python3 incidents/2026-11-18-longhorn-kuma/scripts/assess-longhorn-redundancy.py
sudo python3 incidents/2026-11-18-longhorn-kuma/scripts/check-etcd-membership.py
```

Historical transcripts in the docs use the former repository-root script paths.
Use the new prefix above for those commands. Keep helpers together: several load
sibling scripts. The Fleet placement helper resolves the repository root from
its new location.

| Purpose | Helpers |
| --- | --- |
| Investigation | `collect-kuma-recovery-state.py`, `summarize-kuma-recovery-state.py`, `diagnose-kuma-io-pressure.py`, `inspect-longhorn-io.py`, `assess-longhorn-redundancy.py`, `check-etcd-membership.py` |
| Runtime and attachment recovery | `recover-stale-longhorn-shims.py`, `allow-longhorn-manager-maintenance.py`, `check-maintenance-tolerations.py`, `prepare-kuma-validation.py`, `attach-kuma-for-validation.py`, `check-kuma-filesystem.py` |
| Maintenance | `plan-kuma-recovery-maintenance.py`, `kuma-recovery-maintenance.py`, `pause-kuma-host-compose.py`, `check-kuma-copy-readiness.py` |
| Preservation and validation | `preserve-kuma-recovery-image.py`, `recover-kuma-working-journal.py`, `repair-kuma-working-filesystem.py`, `validate-kuma-working-database.py` |
| Repair and closeout | `repair-kuma-original-filesystem.py`, `restore-kuma-service.py`, `watch-kuma-closeout.py`, `reconcile-kuma-placement.py` |

These are incident-specific helpers, with historical resource identities, paths
and baselines. Read each script and its dry-run output before reuse. This directory
is an investigation archive and guarded toolkit, not a command sequence to replay.
Original-volume repair and runtime cleanup require particular care.

For any later PVC copying/migration, follow the standing
[cluster-wide maintenance procedure](../../longhorn/README-5-pvc-migration-maintenance.md),
including saved states, reconciliation controls, verified shutdown and transfer
cap records. Shutdown mitigates observed I/O risk; it is not a proven storage fix.

Private images, databases, credentials and raw evidence remain outside Git under
`/var/tmp`; documentation records their locations. Keep the preservation image
unchanged. Fleet configuration remains in `fleet/apps/uptime-kuma/values.yaml`.

## Watching etcd after the old-host reboot

Run `sudo python3 incidents/2026-11-18-longhorn-kuma/scripts/watch-etcd-return.py`
on proxmox3. This read-only watcher requires three consecutive successful checks
of the existing three-member cluster, including the old host endpoint, and stops
after 15 minutes. Each assessment saves private evidence under `/var/tmp`.
It does not verify Docker, storage or Kubernetes node recovery.
