# Longhorn stale infrastructure shim recovery — 2026-11-18

All eight application cleanups succeeded at 13:01:23–13:01:59 UTC, zero skipped/failed. Kuma had already been cleaned manually. Old application pods are gone, but volumes remain attached to proxmox2-old and replacement pods await storage.

This incident-specific script allows ONLY these existing pod names and UIDs:
- longhorn-manager-n4f65: fbf58187-d4ee-49d2-b120-64c22e7b9b0d
- longhorn-global-manager-6dc48c46cf-9vqx9: 7524b22f-9af8-455f-b775-4b8b072ff302
- instance-manager-d3fcafc43c01bde60d0358c3be85ac94: 951c0149-19f4-40e1-a207-603059e8b893

The two manager pods must already be terminating. The instance manager may be nonterminating because its PDB prevents eviction. The script does not evict it or alter its PDB: this is recovery of confirmed dead runtime tasks, not normal node eviction. Every application/container task must be STOPPED with absent PID, or absent with an Unknown/Exited CRI record. Exact shim identity and start time are checked; it must own ONLY pause, with no descendants or other live pod-cgroup processes. Any live engine/replica or ready container blocks action. It excludes all CSI, engine-image, etcd, networking and newly recreated infrastructure pods.

Usage on proxmox2-old:
```bash
sudo python3 recover-stale-longhorn-shims.py
sudo python3 recover-stale-longhorn-shims.py --execute
# Explicit confirmation when dialog is unavailable:
sudo python3 recover-stale-longhorn-shims.py --execute --yes
```

Dry-run by default. Execution asks with default-no dialog unless --yes is supplied. Logs/inventory in /var/tmp/stale-longhorn-shims-TIMESTAMP (0700). Exit 2 indicates skipped/failed candidates. SIGTERM only, then bounded normal crictl rm/stopp/rmp; no SIGKILL, force delete, PDB changes, unmount, detach, filesystem repair, replica changes or k3s restart. Each target is checked again before signalling. If guards fail, inspect the reason rather than bypassing them. The allowlist intentionally becomes obsolete after pod replacement.

Afterward verify Longhorn manager and instance-manager readiness, node/disk health, engines/replicas, old mounts and clean volume reattachment to proxmox3. Runtime cleanup alone does not validate emergency_ro filesystems. Never format them or force-attach while old writers may exist. Keep proxmox2-old cordoned/tainted and running as an etcd voter.

Kuma remains disabled: its latest volume attachment is still proxmox2-old. Restore only after relocation and safe mount on proxmox3 can be validated. Full incident findings are in doc/proxmox2-old-recovery-2026-11-18.md exported from ConfigMap stale-app-shim-recovery-20261118.

Any future PVC data-copy migration requires the cluster-wide application shutdown procedure in longhorn/README-5-pvc-migration-maintenance.md, saved replicas/schedules, blocked reconciliation, verified stop, logged cap changes and gradual restoration. This script performs no data copy or transfer-cap changes.

The underlying method worked for Kuma and eight apps; live execution of this infrastructure variant is NOT yet validated.
