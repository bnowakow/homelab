# Longhorn stale infrastructure shim recovery — 2026-11-18

All eight application cleanups succeeded at 13:01:23–13:01:59 UTC, zero skipped/failed. Kuma had already been cleaned manually. Old application pods are gone, but volumes remain attached to proxmox2-old and replacement pods await storage.

This incident-specific script allows ONLY these existing pod names and UIDs:
- longhorn-manager-n4f65: fbf58187-d4ee-49d2-b120-64c22e7b9b0d
- longhorn-global-manager-6dc48c46cf-9vqx9: 7524b22f-9af8-455f-b775-4b8b072ff302
- instance-manager-d3fcafc43c01bde60d0358c3be85ac94: 951c0149-19f4-40e1-a207-603059e8b893

## Kuma attachment recovery checkpoint — 2026-11-18, 18:50 CET

Kuma remained stopped, its Fleet Bundle remained paused, and `proxmox2-old`
remained cordoned/tainted with Longhorn node and disk scheduling disabled.
The Kuma volume `pvc-326def38-faba-49f6-8a78-ebf719227220` was found to have
an active engine and iSCSI device on `proxmox2-old`, so it was not treated as
mere stale metadata. No mount or active application consumer was found. Its
surviving replica was on `proxmox3`.

There were no Kubernetes `VolumeSnapshot` resources. A stale Longhorn
`snapshot-controller` attachment ticket was backed up and removed. A normal
Longhorn detach request was then issued. The ticket recreated once, so it was
removed again before repeating the detach request.

Final checkpoint: the volume was detached, the attachment-ticket map was
empty, the old engine and iSCSI device stopped/disappeared, and the replica
CR/data directory remained on `proxmox3`:

`/var/lib/longhorn/replicas/pvc-326def38-faba-49f6-8a78-ebf719227220-3b607b98`

The detached volume reported `robustness=unknown`, which is expected without
an active engine and is not evidence of data loss. No replica deletion, force
detach, filesystem repair, PVC copy, or transfer-cap change was performed.
Do not attach the volume or restart Kuma until a separately reviewed,
controlled validation on `proxmox3` is ready.

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

## Control-plane handoff update

`proxmox5` (`10.0.0.80`) was added as a third embedded-etcd server. Etcd
membership was verified from `proxmox3`: `proxmox2-old`, `proxmox3`, and
`proxmox5` are all started non-learners/voters. This removes the two-voter
quorum blocker for a future `proxmox2-old` maintenance window.

The first proxmox5 k3s starts failed because `egress-selector-mode` differed
from the existing servers. It later started successfully and promoted its
etcd learner, but `proxmox5` still did not appear as a Kubernetes `Node` even
though `k3s.service` was active. It has no Longhorn node or workload pods by
design. Follow-up must inspect local kubelet/agent registration and Ansible
configuration; do not create a Node object manually or remove proxmox2-old
from etcd based only on the healthy member list.

## Continuation after the 18:50 CET detach checkpoint

The next step is fresh read-only evidence for controlled validation on
proxmox3, not another attempt to recreate the old instance manager by
uncordoning proxmox2-old. The latest checkpoint supersedes that earlier
recovery direction for Kuma: its surviving replica is already on proxmox3
and the old attachment has been removed. Other volumes require separate
assessment.

Prepared `collect-kuma-recovery-state.py` in the repository. Run on proxmox3:

```bash
sudo python3 collect-kuma-recovery-state.py
```

It saves timestamped private evidence under `/var/tmp/kuma-recovery-state-*`:
Kuma's Longhorn volume/attachment/engines/replicas, Kubernetes attachments,
Longhorn nodes/instance managers, Kubernetes nodes/pods, Kuma deployments and
Fleet Bundle. It also records this host's matching mountinfo and device-link
presence. It performs only API reads and local evidence-file writes. It does
not attach, mount, copy, repair, restart or change workloads. Exit 2 indicates
incomplete API collection. Review `summary.json` and the saved source objects;
absence of a matching mount/device on one host is not proof of no consumers
elsewhere. Inventory files can contain workload configuration; keep them private.

Before a controlled validation attachment, establish from fresh evidence:

- Kuma replicas remain zero and its Fleet Bundle remains paused; no other
  consumer or attachment ticket requests this volume.
- Volume remains detached with empty requested/current node, old engines
  stopped and old device/mount absent on proxmox2-old.
- Surviving replica identity, path, disk/node health and availability on
  proxmox3 match the checkpoint. Preserve that sole surviving data copy.
- Current etcd endpoint health/API readiness is verified, rather than assuming
  a historical three-member list proves current quorum health.

Then prepare the explicit validation attachment/mount procedure for review,
including how to prevent application writes and automatic replica rebuilds,
how to preserve recoverability, and how to detach afterward. A filesystem
mount advertised as read-only may still replay a journal unless appropriate
filesystem-specific options are used; do not improvise filesystem repair or
run fsck against mounted storage. Do not restart Kuma solely to test the data.
Any backup/data copy classified as PVC migration must first meet the existing
cluster-wide application shutdown procedure. No attachment or validation
execution was performed by this preparation session: its API connection to
127.0.0.1:6443 was blocked by the sandbox (`socket: operation not permitted`).
The collector's syntax/help were checked locally; live API behavior has not
been validated in this session.

### 17:58 UTC evidence and validation preparation

Operator-provided API `/readyz?verbose` passed, including etcd readiness.
Fresh evidence `/var/tmp/kuma-recovery-state-20261118T175810408616Z`
confirmed the detached volume, empty tickets/nodes, same sole stopped replica,
and no local device/mount. The preceding 17:56 evidence revealed that Kuma's
Fleet Bundle was NOT paused despite earlier assumptions. The operator then
patched `fleet-local/uptime-kuma` to `spec.paused: true`; verify live pause in
subsequent guards. Kuma deployment remained zero with no pods. Both instance
manager CRs were running. Old-node Longhorn node/disk scheduling was disabled.
Kuma deployment still had required podAffinity and no proxmox3 hostname pin.
Volume desired replica count remained 2, with only one existing replica.

Prepared `prepare-kuma-validation.py` for the next narrow step on proxmox3:

```bash
sudo python3 prepare-kuma-validation.py
sudo python3 prepare-kuma-validation.py --execute
```

Dry-run by default; execution requires default-no dialog or explicit `--yes`.
It checks the exact volume UID, detached/no-ticket/no-Kubernetes-attachment
state, paused Kuma Bundle, zero deployment, no pods using any PVC bound to the
volume, exact single surviving stopped replica on proxmox3, stopped engines,
and no local device/mount. It rechecks before mutation and uses a resource
version test. It saves original objects and logs under private
`/var/tmp/kuma-validation-preparation-*`, then sets desired replica count from
2 to 1 and verifies it while detached. This prevents requesting a second
replica for the forthcoming validation; it does not preserve an independent
backup or prove integrity. No existing replica is selected for deletion because
the guard requires exactly one. It does not attach, mount, copy, repair or start
the application. Keep desired count 1 until a planned rebuild is acceptable;
the saved original desired count is 2. A failure may leave count changed:
inspect the log/live state before retrying. Do not run Python with optimization
(the script rejects it because guards use assertions).

The preparation script's help/syntax were checked; live execution has not yet
been reported. Next work after preparation is a reviewed attachment and
read-only filesystem validation plan, with old-host consumer checks refreshed.
No attachment, PVC copy or transfer-cap change was performed here.

Preparation dry-run initially failed with `flags cannot be placed before plugin
name: --kubeconfig=...`. The helper placed `-A` before the `get` subcommand;
`-A` is a get-specific flag. Fixed it to build `kubectl [global flags] get
RESOURCE [-n NAMESPACE | -A] -o json`. Verified scoped argument construction
and real kubectl client-side help parsing for the three cluster-wide reads.
No mutation was reached by the failed dry run. Re-run the corrected dry run
before execution; live guards are still pending.

### 18:01 UTC preparation confirmed; next attachment step prepared

Operator execution saved originals/logs to
`/var/tmp/kuma-validation-preparation-20261118T180142322968Z` and reported
`replica_count_1_confirmed_volume_still_detached`. Desired replicas are now 1;
original count 2 must be restored only when a rebuild is planned.

Prepared `attach-kuma-for-validation.py`, dry-run by default:

```bash
sudo python3 attach-kuma-for-validation.py
sudo python3 attach-kuma-for-validation.py --execute
```

It invokes the existing live preparation guards again, requires desired count
1, then proposes the named Longhorn attachment ticket
`longhorn-api-kuma-validation-20261118`, type `longhorn-api`, node `proxmox3`,
frontend enabled. Confirm old-host Kuma writers/device/mount remain absent
before execution; this script does not remotely inspect proxmox2-old.
Execution asks with default-no dialog (or explicit `--yes`), saves originals
and action logs under `/var/tmp/kuma-validation-attach-*`, atomically tests the
attachment resource version/empty ticket map, adds only its ticket, and waits
up to 120 seconds for attached-on-proxmox3 state. It then runs only a bounded
`blkid -p` signature probe. It never mounts, repairs, copies data or starts
Kuma. Storage-process startup can update storage metadata; this is not an
immutable backup of the sole surviving replica.

On success leave the device unmounted and inspect output before designing the
filesystem check. On timeout/error its ticket may remain: inspect live state
and the action log rather than retrying or force-detaching. Later normal
detachment must remove only this exact ticket, after confirming no mounted
filesystem/consumer, then verify stopped engine/device disappearance. Preserve
unrelated tickets if any appear. No automatic detach or replica-count
restoration occurs. Source format verified against Longhorn v1.13.0
`manager/volume.go` Attach and `k8s/pkg/apis/longhorn/v1beta2/volumeattachment.go`.
Mocked dry-run and successful-execution checks passed, including named-ticket
patch shape and post-attachment probe; live execution is not yet reported.

### 18:04 UTC attachment confirmed; read-only filesystem check prepared

Operator execution log at
`/var/tmp/kuma-validation-attach-20261118T180412775232Z` confirmed attachment
to proxmox3, followed by a successful block signature probe:
`TYPE=ext4`, UUID `cb578054-4075-47b7-afe3-c1f8c31381da`, size 268435456.
The validation ticket remains. No filesystem was mounted or repaired; Kuma
was not started. A recognized signature is not filesystem validation.

Next, on proxmox3:

```bash
sudo python3 check-kuma-filesystem.py
```

The new script performs only API reads, block signature probing and
`e2fsck -f -n` (read-only, answer no; no repair flags). It checks exact
volume/filesystem identity, attached-on-proxmox3 state, desired replica count
1, only the named validation ticket, paused Bundle, zero deployment, absence
of PVC consumer pods, and no mount matching the device major:minor in visible
process mount namespaces or block holders. Mount checks repeat immediately
before and after e2fsck; inaccessible namespaces fail closed. These checks do
not lock out a concurrent administrator or future mount. Keep reconciliation
paused and avoid concurrent volume operations.

The check is bounded to 60 seconds and saves output/exit status under private
`/var/tmp/kuma-filesystem-check-*`. Nonzero e2fsck status is reported unchanged;
it may indicate uncorrected inconsistencies, a journal needing recovery or an
operational error. Interpret the complete output before any action. Leave the
device unmounted and application stopped. No copy/migration occurs. Syntax
was checked; live filesystem execution is pending.

Reference: [e2fsck manual](https://man7.org/linux/man-pages/man8/e2fsck.8.html),
particularly `-n`, mounted-filesystem limitations and exit codes.

The first filesystem-check invocation refused before running e2fsck because
`/proc/3919609/mountinfo` returned EINVAL. Updated the mount scan to tolerate
that error only if `/proc/PID/ns/mnt` is also absent (a task without a mount
namespace, such as a kernel thread or exiting task). EINVAL with an existing
mount namespace and permission errors still block the check. Regression
checks covered normal/vanished tasks, both EINVAL cases and permission denial.
Re-run the corrected script; no filesystem result exists from the failed run.

### 18:06 UTC read-only filesystem check result

Operator ran the corrected checker successfully. Logs:
`/var/tmp/kuma-filesystem-check-20261118T180642954797Z`.
e2fsck 1.47.2 ran all five passes with `-f -n`, skipped journal recovery,
reported `Free blocks count wrong (41283, counted=41282)`, and answered
`Fix? no`. Reported 17/65536 files and 24253/65536 blocks; exit code was 0.
No filesystem repair or mount occurred. Treat the textual warning as an
unresolved inconsistency/journal-dependent observation despite exit 0; do not
label the filesystem clean or infer severe corruption from this alone.
Application database integrity and current recoverable state remain unknown.
Keep Kuma stopped, Fleet paused, desired replicas 1, and validation attachment
unchanged. Do not replay the journal or repair the sole replica as an automatic
next step. A default-read-only debugfs root directory listing can inspect file
names without a filesystem mount or journal replay; that view may omit pending
journal updates. No extraction/copy is implied or authorized by that listing.

### Database files found; transition to cluster-wide copy maintenance

Read-only debugfs listing showed `kuma.db` (61292544 bytes), `kuma.db-wal`
(4136512 bytes), `kuma.db-shm` (32768 bytes), and docker-tls, screenshots and
upload directories. Preserve the database/WAL/SHM together. The nonempty WAL
may contain committed transactions absent from the main file; do not discard
it or use immutable SQLite validation of only the main database. No database
integrity result has been obtained. The listing does not replay ext4's pending
journal, so it is not a guarantee of the final recovered filesystem view.

The operator requested continuation toward preserving a recovery copy and
validating SQLite on that copy. Per the standing PVC migration instruction,
no copy begins until applications across the cluster are stopped and the
required maintenance readiness checks pass. Do not execute the historical
Mailpile shutdown plan; it contains stale workload identities and different
restoration behavior. Keep original source data unchanged through preparation.

Prepared a fresh read-only baseline collector:

```bash
sudo python3 plan-kuma-recovery-maintenance.py
```

It saves full inventories privately under `/var/tmp/kuma-recovery-maintenance-*`
and prints `shutdown-baseline.json`. Coverage includes replica workloads,
DaemonSets, CronJobs, Jobs, HPAs, pods, Fleet Bundles/GitRepos, k3s HelmCharts,
Longhorn recurring jobs/storage state and PVCs. Review all workloads outside
apps-* for classification; essential control-plane, DNS/networking, admission,
Longhorn/CSI and required helpers must remain running. Document any essential
application exception such as AdGuard DNS. Handle operators/custom controllers,
active backups, unmanaged pods and autoscaling explicitly rather than assuming
namespace classification covers them. Inventories with failed API reads have
no completion marker and must not be used for shutdown.

Next preparation: classify fresh inventory, save/pause all relevant
reconciliation and schedules, implement UID-checked shutdown and gradual
restoration from the saved baseline, then verify actual termination. Before
copying, check current API/etcd health, both hosts' I/O PSI, k3s restart
stability and backing-pool headroom. Design the preservation copy and recovery
workspace without journal replay or repair on the only source; validate a
recoverable copy including WAL before any application startup. Use bounded
copy throughput and record every requested/confirmed cap change, observed I/O
pressure and outcome in action and cap-change logs. No transfer cap has yet
been set and no data copied. The collector's read-only calls and partial-
inventory refusal were tested with mocked responses; live execution is pending.

The operator's maintenance inventory completed, but the pasted output omitted
its workload list. The supplied tail shows active applications throughout
apps-* and CloudCasa outside it. Kuma Bundle is paused; Longhorn Bundle and
all other listed Bundles are unpaused, superseding the earlier assumed
Longhorn pause. Reconciliation changes must be planned from this fresh state.
No application shutdown or data copy has been reported.

Added a compact saved-inventory view to avoid another large paste:

```bash
sudo python3 plan-kuma-recovery-maintenance.py --summary
```

This reads the latest complete local baseline without API calls, lists
workload replica/schedule/autoscaling state, incomplete Jobs, paused Bundles,
GitRepos, HelmCharts and Longhorn recurring-job controls, and unmanaged pods.
Review that workload list before implementing shutdown; do not treat the
partial pasted inventory as sufficient classification evidence. Summary tests
covered incomplete-inventory rejection and preservation of zeros/pauses.

### Fresh shutdown baseline classified; maintenance executor prepared

Reviewed compact baseline
`/var/tmp/kuma-recovery-maintenance-20261118T180953924876Z`.
Applications include Bookstack/Piwigo MariaDB, YouTubeDL MongoDB and Zabbix
PostgreSQL. Filebot and Kuma are already zero and must remain zero.
Only Kuma's Bundle is paused. No HPAs or application DaemonSets were listed;
application/backup Jobs and six CronJobs require suspension, including
Longhorn's generated daily backup schedule. CloudCasa runs outside apps-*.

Prepared `kuma-recovery-maintenance.py` with this exact baseline as default:

```bash
sudo python3 kuma-recovery-maintenance.py plan
sudo python3 kuma-recovery-maintenance.py stop --execute
sudo python3 kuma-recovery-maintenance.py verify
```

Plan mode writes a private `execution-plan.json` beside the baseline but does
not change the cluster. Stop is dry-run unless `--execute`; execution uses
default-no dialog or explicit `--yes`. It validates saved UIDs and current
values, stops Rancher/provisioning/Fleet reconciliation controllers first,
pauses all saved Bundles, suspends CronJobs and incomplete Jobs, scales
application frontends/backup agents to zero, waits for their replica statuses
to stop, and then stops their databases. Selected non-app workloads include
CloudCasa, Rancher, system-upgrade-controller, CAPI/Turtles and Fleet
controllers/agent. These are maintenance controls, not storage daemons.
Full originals are in the immutable-by-convention inventory; changed values,
requested changes and confirmations are appended to `maintenance-actions.jsonl`.
Partial failure is not automatically rolled back: preserve source ownership,
inspect logs and resolve the failed guard before resuming.

Essential Kubernetes, CoreDNS/networking/ingress, rancher-webhook,
cert-manager/webhooks, storage provisioners and all Longhorn components remain
running. AdGuard DNS and its service load balancer are the documented essential
DNS exception, retained conservatively to preserve the existing DNS path
(Tailscale DNS forwarding dependencies have not been independently established).
Do not describe this exception as proof of any storage repair. Confirm usable
DNS and dependency classification before copying. Service load balancer
DaemonSets in kube-system remain as networking infrastructure.

Verification checks saved maintenance fields, zero actual replica statuses,
remaining application/control pods, new application/control workload identities,
HPAs/DaemonSets, all live CronJob suspensions, active Jobs and surviving Job
pods across namespaces. Unknown baseline workload types or application
DaemonSets/HPAs block the script rather than being silently skipped. A new
operator/custom resource, direct host process or failed pod with stale runtime
state may still require separate assessment. Kubernetes verification is not
proof of runtime shutdown on every host; investigate stale records/tasks and
confirm source writers are absent before copying. The script never force
deletes pods, bypasses PDBs, restarts k3s, mounts, detaches or copies data.

Longhorn v1.13.0 recurring-job reconciliation compares generated spec against
its last-applied annotation before updating the CronJob; a manual suspension
is not continually overwritten while that generated spec is unchanged. This
is not a permanent scheduling control: settings changes or reconciliation can
reset it. Keep Fleet stopped/paused, verify suspension again immediately before
copying and monitor during transfer. Source checked:
[recurring_job_controller.go](https://github.com/longhorn/longhorn-manager/blob/v1.13.0/controller/recurring_job_controller.go).

Successful stop ends with `shutdown_verified_not_yet_copy_ready`. Before any
copy, still verify both storage hosts' I/O PSI, actual backing-pool headroom,
k3s restart stability, API/etcd readiness and source runtime exclusion. Prepare
a limited worker with a logged and live-confirmed cap plus cap-change record;
no cap has been set and no data copied. Preserve source, WAL and SHM before
repair/replay; plan recovery validation on a separate copy.

Restoration is prepared for after copy/validation or a deliberate abort:

```bash
sudo python3 kuma-recovery-maintenance.py restore
sudo python3 kuma-recovery-maintenance.py restore --execute
```

Review intended restoration and both-host I/O pressure first. It restores saved
database replicas before applications, checks local PSI (some avg10 <=10,
full avg10 <=2) and waits for rollout with five-second spacing. Original zeros
stay zero. Schedules, incomplete Job suspension states and Bundle pauses are
restored afterward; maintenance controllers are restored last. Existing pauses
(including Kuma) remain paused. Because restoration may re-enable retries and
GitOps/Helm reconciliation, review pending changes and the earlier Longhorn
manager OnDelete incident state before this step. Never restore while a copy
or validation helper is still active. Local PSI is not peer PSI; the operator
must check proxmox2-old separately. Final controller health, replica counts,
schedules and reconciliation require separate verification after restoration.

Mocked execution tests covered controller/frontend/database ordering, UID and
resource-version guarded patches, retained DNS, preserved zero replicas and
Kuma Bundle pause, action logs and final verification. Live shutdown execution
has not yet been reported. No cluster changes were made by this preparation
session.

### Cluster-wide shutdown completed — 18:18:14 UTC

Operator ran the reviewed maintenance shutdown. Supplied log shows frontend
replica requests/confirmations through 18:16:59 UTC, database requests at
18:18:00 UTC (Bookstack/Piwigo MariaDB, YouTubeDL MongoDB, Zabbix PostgreSQL),
then a wait for four remaining pods. At 18:18:14.236859 UTC the executor logged
`shutdown_verified_not_yet_copy_ready`. This is the shutdown executor's
Kubernetes verification from operator output, not an independent runtime/host
inspection by this sandbox. The baseline, execution plan and action log remain
under `/var/tmp/kuma-recovery-maintenance-20261118T180953924876Z`.

No copying has started. Keep application shutdown/reconciliation controls in
place; do not restore while preparing copy/verification. Prepared remaining
readiness collector, to run separately on BOTH storage hosts:

```bash
sudo python3 check-kuma-copy-readiness.py
```

It checks API readiness; on proxmox3 it also runs maintenance verification.
It samples local I/O PSI three times over 20 seconds and compares k3s restart
count/start identity/active state, collects exact-filesystem capacity and
mount backing, block layout, available LVM/ZFS pool reports, recent kernel
journal and Longhorn state. All reads are bounded; logs are private under
`/var/tmp/kuma-copy-readiness-HOST-*`. Thresholds some avg10 >10 or full avg10
>2 block, as do required-command failures. Physical backing-pool/thin-pool
headroom, kernel errors, remote stale writers and live storage health still
require review; the script does not automatically approve capacity or copying.
Check underlying hypervisor/pool separately if it is not visible from k3s
hosts. No transfer cap has been requested/applied; no cap change or copy has
occurred. PSI parsing was checked; live collection on both hosts is pending.

Direct monitoring in this preparation sandbox remains unavailable: the root-
only action log is unreadable, sudo is blocked by no-new-privileges, and API
sockets are denied. Operator-provided logs are the observed progress source.

### 18:19–18:20 UTC proxmox3 readiness result: blocked

Operator collector result at
`/var/tmp/kuma-copy-readiness-proxmox3-20261118T181953378556Z`:
all three local PSI avg10 samples some/full=0; k3s NRestarts=139 and main
start timestamp unchanged during the short sample, ActiveState active.
Longhorn ext4 on /dev/zd0 was rw, 47% used, 80579706880 available bytes;
/var/tmp backing filesystem had 274106941440 available bytes. These are mounted
filesystem figures, not proof of physical ZFS pool headroom.

Readiness FAILED: `maintenance-verify: command failed`. Its exact cause has
not yet been supplied. Do not copy or restore automatically; inspect saved
`maintenance-verify.json` or run `sudo python3 kuma-recovery-maintenance.py verify`
to expose the failed guard. Peer readiness and physical pool/kernel-log review
are also pending. Updated readiness collector to surface verifier stderr in
console/summary and allow its many sequential reads up to 180 seconds rather
than 25; this does not waive any guard. No cap requested/applied or copy begun.

### Daily backup maintenance drift identified; controller-level fix prepared

The operator's standalone verification refused with
`Maintenance state drift: nas-daily-backup`. The generated backup CronJob's
suspension was not stable under reconciliation; do not waive this gate.
The earlier assumption that an unchanged recurring spec would preserve manual
suspension was insufficient for observed cluster behavior.

Updated the maintenance executor to also save/control Longhorn RecurringJob
`spec.cron`, using the original `recurringjobs.longhorn.io.json` baseline.
The temporary valid schedule is `0 0 1 1 *` (January 1, outside this November
maintenance window), not a permanent backup change. The incident-specific
script refuses this control in December/January rather than assuming it is
safe near that scheduled time. The prior execution plan is retained as
`execution-plan-before-recurring-schedule.json`; only baseline-derived recurring
controls are appended. Existing workload/schedule/pause restoration values
are unchanged. If the plan has unexpected differences it refuses to overwrite.

Resume with the corrected script:

```bash
sudo python3 kuma-recovery-maintenance.py stop --execute
sudo python3 kuma-recovery-maintenance.py verify
```

Stop is resumable with original or maintenance values. It keeps Bundles paused,
patches the recurring schedule with UID/resource-version checks, waits for
the generated CronJob schedule to match, and only then suspends that CronJob.
Verification now checks both parent cron and generated schedule/suspension.
Restoration first restores the original recurring cron and confirms generated
schedule propagation before restoring saved suspension states. All changes
are logged as requested/confirmed in the same maintenance action log. No
transfer-cap change occurs here. Tests modeled a controller resetting suspend
when it applies the generated schedule and verified suspension happens after
that reset. Live execution of this fix is pending. Applications remain in
maintenance; no copy or automatic restoration should occur on this failure.
