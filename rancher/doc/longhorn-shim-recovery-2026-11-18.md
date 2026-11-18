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

## Allow manager scheduling during maintenance

`allow-longhorn-manager-maintenance.py` prepares the narrow manager-template
change inferred from this incident: tolerate
`maintenance=storage-repair:NoSchedule` while proxmox2-old stays cordoned and
tainted. The earlier sudo-session discussion was unavailable when preparing
this script; its exact agreement has not been verified.

```bash
sudo python3 allow-longhorn-manager-maintenance.py
sudo python3 allow-longhorn-manager-maintenance.py --execute
# Only for explicit confirmation without an interactive dialog terminal:
sudo python3 allow-longhorn-manager-maintenance.py --execute --yes
```

The script requires the expected node cordon/taint, DaemonSet
`longhorn-system/longhorn-manager`, and Fleet Bundle `fleet-local/longhorn`.
Review the dry-run patch first. It preserves existing tolerations, pauses the
Bundle and atomically changes the DaemonSet strategy to `OnDelete` with the
new toleration, avoiding an automatic rollout of healthy managers. Resource
version tests reject concurrent edits. Fleet pause does not cancel an already
running Helm operation; inspect active reconciliation before execution.

Before mutation, originals and the patch are saved in a private
`/var/tmp/allow-longhorn-manager-maintenance-TIMESTAMP` directory. The action
log distinguishes requested and verified changes. On failure, partial changes
may remain; consult the backups/log before retrying. Repeated execution is a
no-op when the exact toleration already exists; this does not establish Fleet
pause or manager readiness.

This changes only the manager template, not Longhorn's global taint-toleration
setting or the instance-manager/CSI templates. Existing manager pods are not
patched or deleted. A missing manager pod can be created from the new template;
a stale or terminating old pod may still block replacement and needs separate
guarded recovery. Check the replacement pod's tolerations, placement and
readiness, manager logs, Longhorn node/disk status and volume state afterward.
A confirmed template patch is not proof of a ready manager or repaired storage.

Fleet remains paused and the strategy remains `OnDelete` deliberately. After
recovery, compare `bundle-before.json` and `daemonset-before.json` against live
state. Restore the saved strategy only when a possible rollout is acceptable;
restore the prior Bundle pause state only after reviewing intended Git/Helm
values and their reconciliation effects. Preserve a Bundle that was already
paused. This temporary script does not modify Git values, uncordon the node,
restart k3s, detach volumes, copy PVC data or change transfer caps.
