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

## Handoff to Codex on proxmox2-old — 2026-11-18, after 15:50 UTC

The following results were supplied by the operator from proxmox2-old. They
are observations at that time, not a fresh live assessment. The preparation
session made no cluster changes itself. Earlier sections describe historical
state; the observations here supersede their attachment/readiness statements.

### Confirmed maintenance change

The operator ran the dry run and then executed
`sudo python3 allow-longhorn-manager-maintenance.py` with `--execute`.
Backup/action-log directory on proxmox2-old:

`/var/tmp/allow-longhorn-manager-maintenance-20261118T154746147774Z`

The action log confirms:

- 15:47:46.155214 UTC: Fleet pause requested.
- 15:47:46.666452 UTC: Fleet pause confirmed.
- 15:47:46.680621 UTC: manager DaemonSet patch requested.
- 15:47:46.973606 UTC: manager DaemonSet patch confirmed.

Fleet Bundle `fleet-local/longhorn` remains paused; manager DaemonSet strategy
is `OnDelete`. The dry run showed no existing explicit template tolerations;
the patch added only `maintenance=storage-repair:NoSchedule`. Do not assume
that the live template originally contained the dedicated-etcd toleration
from the repository values. Use the saved originals for restoration.

### Recovery observed and remaining failure

- Replacement `longhorn-manager-4msvc` on proxmox2-old became 2/2 Running,
  with one early restart. Manager on proxmox3 remained 2/2 Running.
- CSI on proxmox2-old was 3/3 Running; engine-image pods were Running.
- Homer share-manager `share-manager-pvc-3bc8aad0-e2c6-41d9-9923-1bdc76670b8c`
  on proxmox3 was 1/1 Running. This alone does not validate application I/O.
- No instance-manager pod existed on proxmox2-old. Its Longhorn resource
  `instance-manager-d3fcafc43c01bde60d0358c3be85ac94` remained `error`.
  Resource UID: `03ffe901-37b3-452f-9914-c929153ca44e` (this is the CR UID,
  not the old pod UID in the shim-cleanup allowlist).
- Manager logs repeatedly reported the missing pod and recreation attempts;
  engines/replicas could not sync against that error-state instance manager.
  Its recorded IP `10.42.2.213` was stale status, not proof of a running pod.
- Instance-manager conditions: PodReady=False/NotFound;
  NodeReady=False/Unschedulable with message
  `Instance manager node proxmox2-old is unschedulable`.
  No matching Kubernetes events were returned.
- Longhorn node Ready became True at 15:47:49 UTC; its disk Ready and
  Schedulable became True at 15:48:19 UTC. Node Schedulable remained False,
  reason KubernetesNodeCordoned. Node/disk `spec.allowScheduling` were true;
  evictionRequested was false. This does not establish filesystem integrity.
- The volume listing showed attached volumes on proxmox3 as degraded.
  Kuma volume `pvc-326def38-faba-49f6-8a78-ebf719227220` was `attaching`,
  robustness unknown, with no node shown. Several other volumes were detached
  with robustness unknown. Do not treat this as completed recovery or restart
  Kuma based on the manager's readiness.

### Cordon blocker verified against source

Longhorn v1.13.0's instance-manager controller checks
`IsKubeNodeUnschedulable` before creating the instance-manager pod and returns
without creating it when true. That datastore helper reads Kubernetes
`Node.spec.unschedulable` directly. Changing Longhorn's
`disable-scheduling-on-cordoned-node` setting does not bypass this gate.

Sources checked during this session:

- [v1.13.0 instance-manager controller](https://github.com/longhorn/longhorn-manager/blob/v1.13.0/controller/instance_manager_controller.go)
- [v1.13.0 Kubernetes datastore helper](https://github.com/longhorn/longhorn-manager/blob/v1.13.0/datastore/kubernetes.go)

Observed settings:

| Setting | Value | Applied |
| --- | --- | --- |
| disable-scheduling-on-cordoned-node | true | true |
| detach-manually-attached-volumes-when-cordoned | false | true |
| taint-toleration | dedicated=etcd:NoSchedule | false |
| default-instance-manager-image | docker.io/longhornio/longhorn-instance-manager:v1.13.0 | true |
| guaranteed-instance-manager-cpu | {"v1":"12","v2":"12"} | true |

No separate setting to create instance managers on cordoned nodes appeared in
the supplied settings output. The global taint setting lacks the maintenance
toleration; its `applied: false` reason remains undiagnosed. Do not infer that
changing it is required to clear the cordon gate or that a global change is
safe: it can affect components beyond the recovered manager.

### Last completed check and next work

The operator successfully ran:

```bash
sudo python3 check-maintenance-tolerations.py
```

Output:

```text
Matching current apps-* pods: 0
Read-only check. Controller templates and future pods are not covered.
```

Earlier inline Python commands failed due to paste indentation/line wrapping;
those failures produced no valid inventory and made no cluster changes. Use
script files with short invocation commands for subsequent checks.

The following read-only command was proposed but its execution/result has NOT
been reported:

```bash
sudo kubectl --kubeconfig=/etc/rancher/k3s/k3s.yaml get deployments,statefulsets,daemonsets,jobs,cronjobs -A -o json > /tmp/maintenance-workloads.json
```

Continue on proxmox2-old:

1. Re-read current cluster state and the saved maintenance backups/action log.
   Preserve the current cordon/taint, Fleet pause and Kuma shutdown during
   assessment. No uncordon or global setting change has been performed in this
   conversation.
2. Inspect application controller templates, including CronJob job templates,
   for matching maintenance tolerations. Include applications outside `apps-*`,
   custom controllers/operators and future reconciliation where relevant.
   Current-pod output alone is insufficient. Also inspect explicit `nodeName`
   assignments, which bypass scheduler placement checks.
3. Prepare a controlled recovery plan for the required uncordon while retaining
   `maintenance=storage-repair:NoSchedule`. Verify application exclusion and
   determine how to prevent new Longhorn replica placement/rebuild I/O during
   recovery; uncordoning also removes Longhorn's cordon-based scheduling block.
   Save any additional settings/replica/schedule/reconciliation state before
   mutation. The uncordon is a proposed next action, not an already approved or
   completed maintenance step in this record.
4. Investigate the unapplied taint setting and actual instance-manager creation
   behavior before any global toleration change. Do not remove the maintenance
   taint simply to make infrastructure start. Avoid changing the healthy
   proxmox3 instance manager or restarting storage components indiscriminately.
5. After a reviewed recovery action, verify instance-manager pod creation and
   readiness, Longhorn resource state, engine/replica health, clean attachment,
   old mounts and filesystem/application usability. Log actions and results.
   Preserve the etcd voter; no k3s restart or host shutdown is implied.
6. Restore application service gradually only after validation. Restore saved
   Fleet pause/update-strategy states deliberately, preserving prior pauses
   and applications already at zero. Keep Kuma disabled until its safe mount
   and intended placement can be verified.

No PVC copy migration or transfer-cap change occurred in this conversation.
If subsequent work performs a PVC migration, the cluster-wide application
shutdown, reconciliation controls, saved state, verified stop, transfer-cap
records and gradual restoration in
[the required maintenance procedure](../longhorn/README-5-pvc-migration-maintenance.md)
apply before copying.
