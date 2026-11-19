# Longhorn stale infrastructure shim recovery — 2026-11-18

> Latest post-reboot state: [19 November reboot record](proxmox2-old-reboot-2026-11-19.md).
> All 48 attached volumes have one running/RW copy on proxmox3; the six
> earlier dual-node copies have not rejoined. Historical snapshots below remain
> chronological evidence, not the current redundancy state.

## Temporary storage decision — 19 November 2026

Operator decision: keep storage on proxmox3 for the time being. Defer rebuilding
redundancy or returning proxmox2-old to storage scheduling. Retain the old
Kubernetes node cordon/maintenance taint and Longhorn node/disk scheduling
restrictions. Do not reduce desired replica counts merely to hide degraded
status; Kuma remains at its existing desired count of one.

This decision does not request eviction/deletion of the six existing RW copies
on proxmox2-old or migration of those volumes. Leave those copies and etcd
membership intact. Most attached volumes currently rely on a single running
copy on proxmox3; this temporary arrangement does not provide host-failure
redundancy. Retain preservation evidence and scheduled backups. Reassess storage
placement separately before enabling rebuilds or retiring the old host.
No live cluster changes were needed or performed for this decision.

## Etcd membership verified — 19 November 14:46 UTC

Operator-run evidence: `/var/tmp/etcd-membership-assessment-20261119T144605444504Z`.
Three non-learner voters confirmed: proxmox2-old (10.0.0.72), proxmox5
(10.0.0.80), and proxmox3 (10.0.0.50). All three endpoints healthy, no
collector errors; all report revision 51060792, term 839, and applied/index
56230083. Current leader is proxmox2-old. This supersedes the pending quorum
check below. Proxmox5 participates in etcd despite absence from the supplied
Kubernetes Node list; this does not make it a storage node.

Snapshot health does not authorize removal/shutdown. Proxmox2-old still hosts
six live volume replicas. Next choose and validate the second storage location;
retain its cordon and Longhorn scheduling restrictions pending that plan.
No cluster settings or membership were changed.

## Redundancy/manager assessment confirmed — 19 November 14:29 UTC

Private evidence:
`/var/tmp/longhorn-redundancy-assessment-20261119T142900138581Z`.
52 volumes total, 48 attached, 41 attached/degraded at desired=2 with one
running/RW replica on proxmox3. Kuma is attached/healthy at desired=1 with one
running/RW replica there. Six other attached volumes have desired=2 and two
running/RW records across proxmox2-old/proxmox3: AdGuard config/data, Scrutiny
config, Piwigo MariaDB, Jenkins data and DuckDNS config. Thus the old node still
participates in live storage. Four detached/unknown volumes are Filebot's three
claims and the historical Homer native-restore claim; their stopped replica
records must be reviewed before any destructive retirement, not treated as loss
or safe deletion from detached status alone.

Both managers are Running and all containers Ready. Live DaemonSet strategy
OnDelete, generation/observedGeneration=4, ready/available=2, updated=1;
old-node manager revision 5844c5f77f and proxmox3 manager revision 6c64847c68.
Template tolerates maintenance=storage-repair:NoSchedule. Different revisions
explain the 1/2 updated condition under maintenance; no manager failure is shown.
Longhorn Bundle is ACTUALLY UNPAUSED and NotReady with desired rollingUpdate
maxUnavailable=100% drift; earlier assumptions of retained pause are superseded.
Kuma Bundle is unpaused and Ready. Do not apply the reported desired strategy or
restart healthy managers merely to clear Fleet's status. No strategy/pause/
replica/scheduling change was performed during this assessment.

Old Kubernetes node remains cordoned with maintenance and unschedulable taints;
Longhorn node AND disk allowScheduling=false. Proxmox3 is untainted/schedulable.
Only these two Kubernetes Nodes appear. Verify current etcd separately before
planning removal/shutdown: prepared `check-etcd-membership.py` (sudo on proxmox3)
uses existing k3s client certificates and etcdctl to list members, cluster
endpoint health/status, with bounded calls and private logs. It never changes
membership and requires three named voters/healthy endpoints for its positive
marker; a negative/incomplete result is not permission to stop any voter.
Syntax checked; live assessment pending. Three earlier voters were historical,
not fresh quorum evidence. Missing proxmox5 Kubernetes registration does not
prove its etcd voter is absent or healthy.

Redundancy requires another validated storage location: either investigate and
return the old storage host or provision replacement storage while retaining
control-plane-only proxmox5's intended role. Operator preference remains pending
from the earlier question. Do not automatically enable old scheduling, change
Kuma replicas 1 -> 2 without a destination, delete existing/stopped replicas,
uninstall Longhorn, or evacuate/shut down a host. Any data-copy migration must
use a fresh cluster-wide maintenance baseline, verified stop and logged/live-
confirmed caps; no such migration has started in this assessment phase.

## Kuma Fleet placement live-validated — 19 November, 14:21:31 UTC

Operator host dry-run passed full schema/render validation at 14:19 UTC, then
execution temporarily paused Kuma at 14:20:10 UTC, natively applied only its
Fleet Bundle, preserved GitRepo ownership/targets and original Bundle UID
b3284191-c92a-4038-8041-e2fa8b9e1586, and unpaused Kuma at 14:20:12.988396 UTC.
Fresh BundleDeployment applied/Ready, rollout, node/PVC/attachment and real
/app/data mount checks passed. Both configured HTTPS ingress hosts returned
302 under verified TLS at 14:21:31 UTC. Final success event:
`kuma_git_placement_live_validated_commit_eligible` at 14:21:31.327284 UTC.

The values file pins amd64/proxmox3, disables autogenerated PVC affinity, and
retains one Recreate replica/existing claim. This changed Fleet bundle has
now been applied/validated as required by fleet/AGENTS.md; review the matching
local diff before committing. No commit/push occurred. Earlier schema/network/
JSON-preview blockers are resolved on the operator host, not silently bypassed.
Exact execution evidence directory was not in the supplied completion tail;
use the path printed by that run or its private placement-validated.json and
actions.jsonl rather than confusing it with a preview directory.

Kuma Bundle is now deliberately unpaused. Longhorn settings/template/replica
count and old-node maintenance controls were not modified; inspect their current
state separately instead of assuming any prior pause persists. Preserve images
and baselines. Remaining redundancy/root-cause/old-node/Traefik and final
schedule checks are separate follow-up, not grounds to repeat recovery.

## Kuma Fleet preview refusal: live Bundle already unpaused

Operator `reconcile-kuma-placement.py` dry run refused at its initial guard:
`Expected Kuma Bundle paused before controlled apply`. This occurred before
render/native apply/unpause; no cluster change was made by that invocation.
The assumption that the maintenance pause remained in place was stale; an
external reconciliation or other action may have changed it, but cause is
not established. Longhorn's current pause must not be inferred from Kuma's.

Updated helper accepts either initial Kuma pause state, recording the original.
For unpaused Bundles it requires a settled deployed Helm release and a single
Ready BundleDeployment whose desired/applied IDs match, plus the actual healthy
single PVC consumer on proxmox3. It no longer requires the live template already
have the Git hostname pin before making that pin persistent; actual consumer
placement/storage guards remain. Dry-run is still read-only. After explicit
execution confirmation, it temporarily pauses ONLY Kuma with UID/resource-version
tests, verifies pause, performs native paused Fleet apply, then verifies labels/
targeting/pause and unpauses Kuma for validated reconciliation. Concurrent pause
or deployment drift blocks. No Longhorn mutation is included or claimed.

Mocked initially-paused and initially-unpaused workflows passed, including the
pause-before-apply ordering and all postchecks. Repeat the same host dry run;
full schema/live validation remains pending, so the Fleet values edit is still
not ready for commit/push under fleet/AGENTS.md. Do not run a direct Helm upgrade
or bypass guards to proceed. Evidence path is now printed before guards so a
refusal can be inspected. Prior Kubernetes/service recovery remains confirmed;
this is declarative reconciliation follow-up, not another PVC migration.

## UI/data and post-restoration pressure confirmed; Git placement prepared

Operator confirmed Kuma shows expected monitors/history. Post-Docker PSI was
low on both hosts: proxmox3 some/full avg10 0.40/0.29; proxmox2-old 0.00/0.00.
Service restoration and visible Kuma data recovery are confirmed. Notification
send/functionality and standalone app end-to-end checks remain separate.

Prepared Kuma Git values: disable chart autogenerated PVC affinity with
podOptions.defaultAffinity=false and pin workload.main.podSpec.nodeSelector to
amd64/proxmox3, retaining one Recreate replica and existing claim. Cached chart
15.1.4 templates confirmed behavior offline. Full schema fetch failed because
this sandbox cannot open network/DNS sockets; offline rendering skipped schema
ONLY for local behavior inspection, not as live validation. No cluster mutation
occurred. The values change remains UNAPPLIED/UNVERIFIED and must not be committed
or pushed until fleet/AGENTS.md's native Fleet application/live checks pass.

Prepared reconcile-kuma-placement.py for host-side full-schema preview and
single-Bundle native Fleet apply, preserving GitRepo ownership labels/targeting
and refusing unrelated prune scope or chart version changes. It applies paused,
unpauses only Kuma, requires a new Ready/applied BundleDeployment, checks rollout,
actual /app/data mount, volume attachment, node/claim and both HTTPS ingress
hosts without insecure TLS flags. Longhorn remains paused and no replica/node
maintenance changes occur. Mocked workflow tests passed; live dry run/execution
pending. The concise quick handoff now consolidates current status rather than
repeating superseded checkpoints; see it for commands and private evidence.

## Latest checkpoint — service restoration completed, 19 November 14:02:54 UTC

Operator closeout watch at
`/var/tmp/kuma-closeout-watch-20261119T140054115534Z` confirmed the backup Job
Complete, no active backup pods, stable active k3s identities and three low-PSI
samples on BOTH hosts at 14:00:54, 14:01:24 and 14:01:54 UTC. Local some avg10
was 0.01 then zero; local full and peer some/full were zero. No services were
started by the watcher.

Operator then restored the approved standalone containers on proxmox2-old from
`/var/tmp/kuma-host-compose-20261118T192324749221Z`:

- PostgreSQL start requested 14:02:40.808281 UTC; health/start confirmed
  14:02:53.253685 UTC.
- Scrutiny start confirmed 14:02:53.623777 UTC.
- Watchdog Spring Boot start confirmed 14:02:53.969841 UTC.
- Original running/restart states confirmed for watchdog at 14:02:54.080345 UTC,
  Scrutiny at 14:02:54.211756 UTC, PostgreSQL at 14:02:54.363684 UTC.
  All three original restart policies were unless-stopped.

Thus Kuma recovery, Kubernetes saved-value restoration and standalone Docker
restoration have completed according to supplied execution results. This
supersedes earlier Docker-pending/running-restoration statements below. No
copy worker/cap is active; verified preservation and private baselines remain
retained. The last both-host low-pressure watch preceded Docker startup;
post-start pressure and application functionality have not yet been reported.
PostgreSQL readiness was checked; watchdog/Scrutiny started state is not a full
end-to-end functional validation.

Remaining closeout/follow-up (not grounds to repeat recovery):

- Confirm Kuma UI/login/history/notification configuration and standalone
  application functionality; check post-start both-host pressure/API stability.
- Keep Kuma/Longhorn Fleet pauses pending deliberate reconciliation review and
  persistent placement/configuration. Preserve Filebot's original zero.
- Verify final StatefulSet/backup schedules/reconciliation and the previously
  unresolved Traefik 1/2 replica issue. Rancher was subsequently confirmed 3/3.
- Review Longhorn redundancy and desired replica count 1 for Kuma separately;
  do not raise it blindly or uncordon the old node. Preserve the old-node
  maintenance taint and Longhorn node/disk scheduling controls as last observed.
- Underlying storage/runtime failure cause remains unproven. Successful service
  recovery, backup completion and low PSI are not proof of hardware repair.
- Preserve private images/baselines securely outside Git, and commit the updated
  scripts/documentation. A Git clone does not include rollback images/data.

Do not rerun shutdown, original repair, copies or completed restore commands
merely for health verification. No storage/global-setting mutation or transfer-
cap change occurred during Docker restoration. Full chronological evidence and
historical command plans follow below; use this checkpoint as the latest state.

## Latest supplied checkpoint: daily backup complete, Docker restore pending

Operator reported `nas-daily-backup-29917440` **Complete 1/1**, duration **82m**,
age 83m. Exact observation/completion wall-clock time was not supplied.
Proxmox3 PSI at that check: some avg10=0.12/avg60=2.11/avg300=3.28;
full avg10=0.01/avg60=1.54/avg300=2.53. Recent local pressure is low; older
averages reflect earlier load. This supersedes prior backup-running statements.
The earlier whole-Job time estimate was unreliable because per-volume progress
did not expose the remaining volume set. Successful backup completion and low
recent PSI do not prove the root cause of previous spikes.

Kuma service recovery and Kubernetes saved-value restoration previously
completed. The remaining intentionally paused host services are the watchdog
application/PostgreSQL and standalone Scrutiny on proxmox2-old. Before restoring
them, verify sustained low pressure on BOTH hosts with the prepared read-only
watcher on proxmox3:

```bash
sudo python3 watch-kuma-closeout.py
```

It checks the exact completed Job UID/status, both-host PSI and active/stable
k3s identity through existing trusted non-root SSH. It requires three consecutive
low-pressure samples, spaced 30 seconds, AFTER Job completion before reporting
success. It does not start services, change schedules or limits, or repair any
storage. Observations/marker are private under `/var/tmp/kuma-closeout-watch-*`.
Default observation window is 30 minutes; `--minutes` may limit observation
without changing pressure thresholds. Actual latest data includes only one
proxmox3 pressure snapshot; watcher success and fresh peer health are not yet
reported. If it refuses, inspect the reason; do not treat timeout as success.

After it passes, restore on proxmox2-old:

```bash
sudo python3 pause-kuma-host-compose.py restore --execute
```

Saved baseline remains `/var/tmp/kuma-host-compose-20261118T192324749221Z`.
Starts originally running PostgreSQL first, waits for health, then Scrutiny and
watchdog and restores saved unless-stopped policies. No Compose volume removal
or rebuild is implied. Report results and check actual application/host health.
This command is authorized by the earlier user approval to pause/restore these
services, but execution is not yet reported. Existing incident-related Fleet
pauses, old-node maintenance controls and desired replica count 1 remain for
separate reviewed follow-up. No new transfer cap, data copy or storage mutation
occurred on this observation; final closeout still awaits these checks/results.

## Newer health observation: Rancher recovered; backup still running

Operator reports all three Rancher pods 1/1 Running on proxmox3, zero restarts;
rancher-webhook and system-upgrade-controller are also Ready. This supersedes
the earlier Rancher 1/3 observation. Daily backup Job nas-daily-backup-29917440
remains Running 0/1 after 8m21s. Exact wall-clock time was not supplied.
Current proxmox3 PSI some avg10=3.54, full avg10=1.97; full avg60=3.00 and
avg300=4.23. Pressure has improved but is close to the full=2 threshold and
has not been demonstrated sustained low. Do not infer that backup completion
or storage stability has already been achieved. Keep standalone Compose paused;
let the progressing backup finish and check several current pressure samples
before adding that load. No storage process, schedule, cap or workload was
changed by this observation. Traefik replica deficit and final schedule/
reconciliation/StatefulSet checks remain outstanding.

## Busy PID mapped to Kuma; recurring backup progressing on other volumes

Operator supplied compact saved snapshot at 06:57:20.946903 UTC from
`/var/tmp/longhorn-io-inspection-20261119T065720946903Z`. PID 1806970 was present,
startTicks 122672604, inside instance-manager-b10f19f12351bcae88e7b0bc894765c7
(pod UID 6ea61008-38f8-4325-9782-7f1f9f0dd8ca). Its argv-derived volume ID is
Kuma `pvc-326def38-faba-49f6-8a78-ebf719227220`; matching replica r-9cdce044 is
running on proxmox3, volume attached there, engine running with sole RW replica
and no active/error operation entries in the compact snapshot. Role was not
recognized by the conservative argv parser; do not invent replica/controller
role from process name. A busy Kuma-volume process is not proof of rebuild,
backup, corruption or a particular SQLite operation. Original-volume application
I/O and snapshot-related work need separate investigation if pressure persists.

Job nas-daily-backup-29917440 was active at this snapshot. Supplied logs show
sequential successful backups, snapshot/retention cleanup and next-volume starts
at concurrency=1, executionCount=63, retention=14 and full-backup-interval=7.
For example pvc-35563078 completed at 06:55:49 UTC, pvc-0562a0af at 06:56:04,
pvc-6cbf25ee at 06:56:29, Homer RWX at 06:56:40, pvc-3d1d3369 at 06:57:05,
and pvc-4d1b252e at 06:58:20. Last supplied log shows pvc-6267e84f backup at
96% at 06:58:36 UTC. Full Job completion has NOT been reported. These logs
identify actual backup traffic on OTHER volumes, not a backup of Kuma at the
time of its busy PID observation; temporal overlap alone does not prove cause.
Concurrency 1 is not a confirmed bytes/s cap. No backup bandwidth/cap setting
was changed, and completed recovery-transfer workers remain idle.

Next read current Job status, current PSI and Rancher pods. Do not cancel a
progressing backup or kill storage processes merely from overlap; if pressure
continues, inspect recent Kuma logs/current process/volume evidence for its
separate I/O. Keep standalone Docker paused until load settles and readiness
is assessed. Rancher 1/3 and Traefik 1/2 from the last health listing remain
unresolved until newer evidence supersedes them. No new storage mutation or
source repair is proposed by these observations.

## Active Longhorn backup Job found in supplied inspection

The operator's pasted inspector tail shows active Job
`longhorn-system/nas-daily-backup-29917440` (active=1). The pasted engine subset
has empty rebuild/backup lists and idle/completed restore/purge entries; this
is not evidence of ongoing rebuilds or purges, nor proof that no backup exists
on an omitted engine. The PID-mapping header and earlier engine entries were
not supplied. Normal engine/replica RW activity alone does not identify cause.
The backup Job is a plausible contributor after schedules were restored, but
its actual operation/progress and relation to PID 1806970 require logs/current
evidence. Do not stop an instance manager or kill the Longhorn process blindly.

The inspector formerly printed every engine because idle restore/purge entries
made their lists nonempty. Corrected compact output to retain only PID-matched
volumes, active operations or errors, with completed/idle entries omitted.
`sudo python3 inspect-longhorn-io.py --saved-summary` reads the latest saved
snapshot without API calls, recovering the omitted mapping header and active
Job list. Tests covered active/error retention and completed/idle filtering.
Next inspect that compact summary and the active Job's bounded logs. Docker
remains paused pending pressure assessment; no data copy, storage mutation,
backup cancellation, transfer-cap change or new shutdown has been performed.

## Storage activity inspection pending — 19 November, 06:54–06:55 UTC

Latest operator diagnostics:
proxmox3 `/var/tmp/kuma-io-diagnostic-proxmox3-20261119T065424819414Z`;
proxmox2-old `/var/tmp/kuma-io-diagnostic-proxmox2-old-20261119T065501732706Z`.
Both APIs passed readiness and k3s counters remained 139/114 respectively,
with original start identities unchanged. Peer PSI some/full avg10 was zero.
Proxmox3 pressure declined from the earlier peak but rose again during this
sample: full avg10 1.71 -> 1.40 -> 1.51 -> 2.14 -> 2.66 -> 3.26;
some avg10 ended at 3.92. This still exceeds the full=2 maintenance threshold;
do not resume Docker/other new work solely because readyz passes.

Largest sampled proxmox3 process was Longhorn PID 1806970, reading 100818944
and writing 26288128 bytes over roughly ten seconds, pod cgroup UID
`6ea61008-38f8-4325-9782-7f1f9f0dd8ca` (container prefix 0c575eb7218b).
ZFS kernel threads also performed I/O; counters can overlap across storage
layers and must not be added as independent physical bandwidth. The sample
shows a busy storage process, not proof of rebuild, backup, hardware failure
or the workload that caused it. Later pool iostat intervals sampled lower
traffic and are not concurrent proof that the earlier pressure disappeared.

Prepared read-only PID/volume/operation mapper, on proxmox3:

```bash
sudo python3 inspect-longhorn-io.py 1806970
```

It reads current PID identity/cgroup and extracts only volume IDs and known
role names, never full argv/environment; maps the pod UID; collects current
Longhorn engine operation status and matching volume/replica state plus active
Jobs. Saves private details under `/var/tmp/longhorn-io-inspection-*` and prints
a limited summary. If the PID exited it reports that limitation rather than
attributing current operations to an absent task. Historical completed engine
operation entries may still appear; inspect their state/progress before calling
them active. Syntax/help checked; live inspection pending. Do not kill a storage
process or change global replica/backup settings without identifying the
operation. No data copy/cap change/replica mutation/restart occurs in this check.
Standalone Docker remains paused pending pressure resolution.

## Post-restore health check — high I/O pressure, closeout blocked

Operator supplied deployment/API/PSI output after the 06:52:04 UTC restore
completion (exact observation time was not supplied). All listed application
Deployments are Ready at intended live counts, including Kuma 1/1, Homer Test
2/2 and Filebot 0/0. The StatefulSet PostgreSQL is not covered by this deployment
listing and requires its own final check. Fleet/CAPI/Turtles/CloudCasa and other
listed controllers were Ready except Rancher 1/3 (3 up-to-date, 1 available).
Traefik remained 1/2 (1 up-to-date, 1 available), matching the earlier unresolved
replica issue; the current reason still requires fresh pod/events assessment.
Longhorn listed deployments were Ready; this does not prove volume redundancy
or absence of replica rebuild/backup activity.

API readyz passed, including etcd. However proxmox3 I/O PSI was HIGH:
some avg10=55.53/avg60=41.49/avg300=14.27;
full avg10=18.67/avg60=23.26/avg300=8.61. API readiness and ready application
pods do not waive this pressure condition or establish stable storage health.
The restore script completed earlier; no bulk copy worker/cap is active.

Do NOT restore standalone Docker yet, uncordon proxmox2-old, raise replica
counts, restart storage/control-plane daemons, or repeat shutdown/restore
blindly. Keep current services/ownership stable while collecting read-only
host/process/pool/kernel/Longhorn evidence on both storage hosts:

```bash
sudo python3 diagnose-kuma-io-pressure.py --restoring
```

Inspect the resulting evidence for current I/O processes, recent errors,
Longhorn rebuild/backup activity and readiness changes. Post-start application
work or resumed schedules may contribute, but no cause has been established.
Identify the actor before choosing a targeted, saved-state mitigation.
Kubernetes saved-value restoration is complete; incident recovery/health and
standalone Compose restoration remain incomplete.

> **Current handoff:** [19 November quick handoff](kuma-recovery-handoff-2026-11-19.md).
> Kubernetes restore completed at 06:52:04 UTC on 19 November. Controller
> readiness, final health and standalone Docker restoration remain unverified.
> Do not rerun the completed restore for health checks. Sections below retain
> historical states; use the quick handoff for current status and evidence paths.

## Latest supplied state — 19 November: YouTubeDL Ready after restoration timeout

The operator resumed Kubernetes restoration at 01:07:29 UTC after both-host
pressure settled and API readiness/restart counts remained stable. Ready events
were supplied through Radarr, Scrutiny, Smokeping, Sonarr, Ubooquity and Whisparr;
Kuma's deliberate recovered one-replica state was preserved. YouTubeDL Material
replicas 0 -> 1 was confirmed at 01:17:04.145965 UTC, but its rollout exceeded
the executor's 120-second wait and stopped the restore process.

Subsequent operator diagnostics show BOTH YouTubeDL Material and MongoDB 1/1
Running on proxmox3, zero restarts, deployment desired/updated/available all 1,
Progressing/Available True, and no current deployment events. Logs show MongoDB
ping ok, verified Python 3.12.16 installation, startup permission handling,
PM2 online and the app serving on 10253 at 01:23:13 UTC. Those initialization
steps apparently exceeded the earlier wait; the exact duration of each step
was not measured. No persistent rollout/storage/database failure is established
by this supplied evidence. Mongosh history-directory warnings did not prevent
a successful ping/startup. Do not restart or change this now-ready workload.

The restoration process has exited and must be resumed, not run concurrently:

```bash
sudo python3 kuma-recovery-maintenance.py restore --keep-recovered-kuma --keep-longhorn-paused --execute
```

It rechecks already-restored readiness/counts, retains the pressure guard, and
continues remaining workloads/schedules/controllers. No full restoration-
completion event has been reported. Standalone Compose restoration, final
cluster health, UI confirmation and incident reconciliation/storage review
remain outstanding. Older checkpoints below are historical and superseded by
this report. The original maintenance baseline must not be replaced; no copy
or transfer-cap change occurs during restoration.

## Latest reported checkpoint — restoration stopped after 20:08:16 UTC, 18 November

The later operator report supersedes the in-progress checkpoint below.
Restoration did NOT finish: it stopped on local I/O PSI some avg10=8.18,
full avg10=7.63 (full exceeds the limit of 2). Supplied output confirms Ready
rollouts for Bazarr, Bindery, Bookstack, Calibre, Calibre-web, Kavita, Cleanuparr,
DDClient, Docker-mailserver, DuckDNS and Flaresolverr. Last reported ready event
was Flaresolverr at 20:08:16.720364 UTC. Earlier phases are recorded in the
private action log; do not infer remaining application/controller/schedule
restoration from this partial tail.

The restore process exited; previously restored services remain running. Do
not run shutdown again, bypass the pressure guard or restart all workloads.
No copy worker or transfer cap was active, so this was a restoration pause,
not another transfer-cap reduction. Kuma remains deliberately recovered at 1;
Filebot's original zero, incident pauses and the standalone Compose pause
remain as last observed. Fresh state is needed because this report was supplied
on 19 November for an 18 November run.

Run on BOTH storage hosts:

```bash
sudo python3 diagnose-kuma-io-pressure.py --restoring
```

This mode collects current host/process/storage/API evidence without treating
expected running restored applications as a failed all-zero maintenance gate.
It does not approve restoration/copying or mutate workloads. Review both-host
pressure and saved kernel/Longhorn evidence before resuming the same restore
command with both recovery-preservation flags. Resume is designed to retain
already-restored counts and original zeros; do not replace the baseline.
Final restoration, Compose restart, UI confirmation and reconciliation/storage
follow-up remain outstanding. The earlier current-status blocks below are
historical and superseded by this stop result.

## Latest status — 20:00:34 UTC, restoration in progress

Original Kuma filesystem recovery, working SQLite/WAL integrity validation and
normal detach/CSI handoff succeeded. Kuma is Ready on proxmox3 at one replica;
startup logs show database connection and serving, with no supplied DB errors.
UI/login/monitor-data confirmation is still pending.

The operator started Kubernetes restoration with
`restore --keep-recovered-kuma --keep-longhorn-paused --execute`:
`restore_begin` at 20:00:34.835690 UTC; Bookstack MariaDB replicas 0 -> 1
confirmed at 20:00:34.985976 UTC. Last supplied output was waiting for that
rollout. No rollout-ready or full restoration-complete result has been supplied.
Do not start another restore worker while this one may still run. On timeout,
pressure or drift, inspect the exact error and saved actions before resuming.

Recovery baselines and logs remain private under the paths recorded below.
Kuma and Longhorn Bundles remain deliberately paused; Filebot remains an
original zero. Other applications/schedules are not yet confirmed restored.
Standalone Docker restoration on proxmox2-old, UI confirmation, desired placement
persistence/reconciliation, old-node assessment and Longhorn redundancy remain
outstanding. All transfer workers finished/stopped; no bulk copy is active.
The last confirmed extraction cap was 1 MiB/s -> 0 at 19:49:51 UTC.

The following sections are historical checkpoints and incident-specific command
plans. Later results supersede earlier pending/stopped/attached statements;
do not execute old commands without reviewing current state and attempt markers.

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
[the required maintenance procedure](../../../longhorn/README-5-pvc-migration-maintenance.md)
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

### 18:34–18:35 UTC both-host readiness; recurring suspension resets again

Operator supplied both readiness summaries:

- proxmox2-old `/var/tmp/kuma-copy-readiness-proxmox2-old-20261118T183408660129Z`:
  PSI some/full avg10 zero in all three samples, k3s NRestarts=114 unchanged,
  active service/start identity stable. /var/lib/longhorn rw, 81618059264 bytes
  available; /var/tmp backing filesystem 46852734976 bytes available. No
  required collector failures; maintenance verification is intentionally only
  run on proxmox3.
- proxmox3 `/var/tmp/kuma-copy-readiness-proxmox3-20261118T183440655311Z`:
  PSI avg10 some/full <=0.01, k3s NRestarts=139 unchanged, active/start stable.
  /var/lib/longhorn rw with 80579694592 available bytes; /var/tmp backing
  filesystem 274101960704 available bytes. Verification failed again because
  generated `nas-daily-backup.spec.suspend` reverted to false.

The prior schedule-plus-suspend fix did not make suspension persistent. Do not
keep reapplying a transient flag or claim it is confirmed indefinitely.
Updated verification to accept scheduling exclusion through the deferred
annual schedule ONLY for a known baseline Longhorn-generated CronJob whose
ownerReference points to the exact baseline RecurringJob UID, whose parent
`spec.cron` matches `0 0 1 1 *`, and whose generated `spec.schedule` matches it
as well. The November-only maintenance date gate remains; all other CronJobs
must remain suspended. Active Jobs/Job pods and unrelated drift still block.
This is scheduling deferral, not a confirmed persistent suspension. It records
`backup_suspension_not_persistent_deferred_schedule_confirmed` with requested
suspend=true, actual suspend value and live confirmed schedule. Original
schedule/suspension restoration remains unchanged.

Run the corrected verifier rather than repeating shutdown or full host probes:

```bash
sudo python3 kuma-recovery-maintenance.py verify
```

Tests confirmed acceptance of a reset suspend flag only when deferred parent,
generated schedule and ownership agree, and refusal for drift in any of those.
Physical pool headroom/health, recent kernel-log review and source exclusion
remain separate gates before transfer. Both hosts' filesystem free space is
not a physical pool assessment. No data copied and no cap requested/applied.

### 18:38 UTC maintenance passed; preservation-image worker prepared

Operator verification confirmed deferred backup schedule with actual suspend
false at 18:38:19.601391 UTC, then
`shutdown_verified_not_yet_copy_ready` at 18:38:25.190685 UTC. This confirms
the explicit schedule-based guard; it does not claim persistent suspension.

Operator pool reports: proxmox2-old rpool ONLINE, all pools healthy,
54696001536 physical bytes free, 78% allocated; proxmox3 rpool ONLINE,
289796747264 physical bytes free, 42% allocated. These satisfy the observed
headroom requirement for a 268435456-byte preservation image plus working
copy, but do not diagnose or repair the original storage errors. Host readiness
results and maintenance baseline remain linked above.

Prepared `preserve-kuma-recovery-image.py` on proxmox3:

```bash
sudo python3 preserve-kuma-recovery-image.py
sudo python3 preserve-kuma-recovery-image.py --execute
```

Dry-run performs live guards without copying data. It requires all earlier
helpers alongside it, root on proxmox3, live maintenance verification, exact
volume UID/size/ext4 UUID, desired replicas 1, only the named validation ticket,
exactly the surviving running replica on proxmox3, exactly one running engine
there with sole RW replica, no matching device mounts in visible process
namespaces or holders, sufficient local destination space, and active k3s/low
I/O PSI on both hosts. Peer monitoring uses existing trusted noninteractive
SSH, default `sup@10.0.0.72`, and fixed read-only Python/systemctl/proc reads.
Override account with `--peer-ssh` only for an existing authorized account on
proxmox2-old; host identity is checked. It never disables SSH host verification,
automatically enrolls keys, uses remote sudo or silently skips a failed peer.
If authentication/trust blocks, resolve the existing access rather than
starting an unmonitored copy.

Execute asks with default-no dialog (or explicit `--yes`) and rechecks guards.
It reads the source device only with rb/O_RDONLY, writes a new private
`/var/tmp/kuma-preservation-TIMESTAMP/source-preserved.img`, computes SHA-256
while copying, then rereads the image and compares SHA-256. Fixed cap is
1048576 bytes/s with 65536-byte chunks, applied to BOTH copy and checksum
verification. No catch-up bursts after delayed guard calls. Expected bulk
read time is at least 512 seconds plus API/SSH/monitor overhead; total workflow
has a 30-minute deadline checked between chunks/guards. Kernel-blocked reads
cannot be guaranteed interruptible by this userspace deadline.

It logs `cap_requested` separately from `cap_confirmed` with worker PID/chunk
pacing before first data read. `actions.jsonl` and `cap-changes.jsonl` retain
timestamps, previous/new bytes/s, reason, observed pressure and outcome.
No automatic rate reductions: high pressure stops the worker, with the
confirmed transition to zero logged after handles close. Both-host PSI/k3s
identity checks run between chunks about every five seconds; source attachment/
mount/engine guards about every 15 seconds and full maintenance verification
about every 30 seconds. Guard execution pauses copying. On failure/interrupt,
partial image is retained without `verified.json`; do not use it for recovery
or resume blindly. No copy has yet been reported and no live transfer cap
has yet been requested/applied by an executed worker.

After a complete matching checksum, image is chmod 0400 and `verified.json`
records bytes/hash/source identity. This is a local preservation copy on the
same physical host/pool, not protection against loss of that pool or an
independently validated database backup. Keep it unchanged; journal replay,
filesystem repair, database/WAL recovery and integrity checks belong on a
separate working image/files after a capped copy of this preservation image.
The source remains attached, unmounted, and application stopped throughout.
No extraction/mount/source repair/application start occurs in this script.

Tests checked fixed-cap pacing with a simulated clock, exact copied bytes and
hash, premature EOF rejection and monitor failure before first copy. Dry-run
and live execution remain pending. API/SSH and storage reads are bounded where
possible; source read consistency still depends on continued source-writer
exclusion and no concurrent administrator operations. Preserve old-host source
exclusion, review recent storage errors, and keep maintenance active throughout
copying AND database validation. Restore only after workers are stopped and
validation is complete or deliberately aborted.

Preservation dry run initially failed before any copying: root's SSH context
had no trusted ED25519 key for 10.0.0.72. Existing sup known_hosts contains
matching entries for both 10.0.0.72 and proxmox2-old. Updated the worker to run
ONLY the fixed remote monitoring SSH command via `runuser -u SUDO_USER -- ssh`
(default sup when no SUDO_USER); root remains responsible for local device
reads. `--peer-local-user` can select an existing non-root local account.
This uses that account's existing trust/authentication rather than creating
root trust entries or bypassing checking. BatchMode and StrictHostKeyChecking
remain enabled, and no keys are enrolled/copied. Authentication still requires
working existing credentials; any failure blocks the dry run before copying.
Re-run the same dry-run command. No live cap was requested/applied and no data
was copied by the failed attempt.

### Preservation worker running — copy complete, checksum verification pending

Operator supplied live output from
`/var/tmp/kuma-preservation-20261118T184540364827Z`.
At 18:45:40.365263 UTC cap 0 -> 1048576 bytes/s was requested; at
18:45:49.640159 UTC worker PID 1510424 confirmed live fixed pacing with
65536-byte chunks before first source read. Both-host some/full avg10 PSI was
zero at cap request/confirmation and every supplied observation through
18:51:59 UTC. No cap adjustment or automatic reduction was reported.
The worker's `actions.jsonl` and `cap-changes.jsonl` are the cap records.

At 18:51:46.750248 UTC `copy_complete` recorded 268435456 bytes over
357.09955961303785 seconds, source-stream SHA-256
`3470b1fd2130d7b2ed136725941695177146a753f9593436af8460ce8d1666a1`.
The image reread/checksum-verification phase then began under the same cap.
This is NOT yet a verified preservation image: wait for `image_verified` and
`verified.json` plus the final idle/cap-zero confirmation. Keep applications
stopped and do not alter source/image or start a second worker meanwhile.

### 18:57:54 UTC preservation verified; working-copy mode prepared

Operator completion output confirms `image_verified` at 18:57:54.356026 UTC:
268435456 bytes, SHA-256
`3470b1fd2130d7b2ed136725941695177146a753f9593436af8460ce8d1666a1`,
source UUID `cb578054-4075-47b7-afe3-c1f8c31381da`, sourceWritten=false,
mountOrRepairAttempted=false. Image and marker are under
`/var/tmp/kuma-preservation-20261118T184540364827Z`.
At 18:57:54.358181 UTC cap 1048576 -> 0 was confirmed with handles closed,
worker idle, local/peer PSI zero. The supplied late verification observations
had a brief local some/full avg10 rise to 0.14 at 18:57:05 UTC, then declined
to zero; peer stayed zero. No rate change/reduction occurred before final stop.
The preservation worker is finished. Database integrity is still unverified.

Added `--working-copy` to the same capped worker:

```bash
sudo python3 preserve-kuma-recovery-image.py --working-copy
sudo python3 preserve-kuma-recovery-image.py --working-copy --execute
```

This selects the latest verified `kuma-preservation-*` directory, requires a
regular 256 MiB preservation image without write permission bits, verifies its
marker matches the exact reported UUID/size/SHA-256 checkpoint, and reads only
that file for the copy. It still requires live maintenance/host/attachment
monitoring. It creates a NEW private `kuma-working-TIMESTAMP/working.img`,
checks copied content hash against the confirmed preservation hash, rereads
and verifies the new file, and leaves the working image mode 0600 for later
explicit recovery operations. The preservation image stays read-only and is
never the target of journal replay or repair. All pacing and cap-change logs
remain as for preservation; the new worker starts with a separate requested/
confirmed 0 -> 1 MiB/s cap and returns to zero when idle. No working-copy live
cap or data transfer has yet been reported.

This is a full capped copy, not a hard link or a silent unthrottled fallback.
Expect roughly the same 12-minute copy-plus-verification duration as the first
worker, including monitoring overhead. Keep applications stopped throughout.
The initial dry run only reads guards/metadata. Tests covered source checkpoint
selection, rejection of writable or wrong-hash preservation metadata, and exact
capped-copy bytes/hash/pacing. Live working-copy execution is pending. No
filesystem repair, journal replay, mount, SQLite WAL recovery or app start has
yet been performed; those will use the verified working image, never the
preservation image or original device.

### Working-copy worker stopped on pressure — 19:11:42 UTC

Operator supplied failed working-copy execution under
`/var/tmp/kuma-working-20261118T190817882610Z`.
Cap requested at 19:08:17.885482 UTC, 0 -> 1048576 bytes/s, local/peer PSI
zero. Worker PID 1604871 confirmed live pacing at 19:08:27.156759 UTC, chunk
65536 bytes, before reading. Local full PSI briefly rose to 0.45 at
19:09:59 UTC and declined; from 19:11 UTC pressure increased on both hosts.
At 19:11:42.066986 UTC observed local some=2.68/full=2.25 and peer
some=1.04/full=0.97 (avg10). Local full exceeded the 2 threshold.
`transfer_failed` at 19:11:42.068959 UTC reported high I/O pressure;
at 19:11:42.070927 UTC cap 1048576 -> 0 was confirmed, handles closed and no
further reads. There was no live reduction to a lower nonzero cap. The worker's
`actions.jsonl` and `cap-changes.jsonl` retain request/confirmation, observed
pressure, stop reason and outcome. The working image is partial and NOT
verified; do not mount, repair, recover SQLite from it or resume blindly.

The verified preservation image remains unchanged under
`/var/tmp/kuma-preservation-20261118T184540364827Z`. The original device was
not the source of this working-image copy and no source filesystem repair or
mount occurred. No proof that this small local copy caused the cross-host I/O
pressure, or that reducing throughput would resolve it, has been established.
Keep applications in maintenance and investigate before choosing a retry.

Prepared read-only diagnostic, to run on BOTH storage hosts:

```bash
sudo python3 diagnose-kuma-io-pressure.py
```

It samples PSI and /proc process I/O deltas over ten seconds, reports the
largest observed readers/writers with process names/cgroups (not command-line
arguments), and collects bounded ZFS/device I/O reports when tools exist,
recent kernel logs, k3s status, API readiness, pod and Longhorn engine/replica/
volume state. On proxmox3 it re-verifies maintenance. Evidence is private
under `/var/tmp/kuma-io-diagnostic-HOST-*`. Process counters may miss exited
or newly created tasks and cannot by themselves identify underlying ZFS or
hardware causes. No data copying, cleanup, cap change, filesystem change,
application restoration or restart occurs. Syntax checked; live collection is
pending. Review summaries plus kernel/Longhorn evidence before resuming any
worker. Preserve the partial directory/logs as failed-attempt evidence.

### Post-abort diagnostics: pressure settled; standalone Docker writers discovered

Operator diagnostics:

- proxmox3 `/var/tmp/kuma-io-diagnostic-proxmox3-20261118T191317456306Z`:
  PSI avg10 zero across the sample; k3s NRestarts=139 unchanged, API readiness
  and Kubernetes maintenance verification passed. Largest sampled I/O was a
  kernel worker and k3s; this post-event sample does not identify the earlier
  pressure source. Later pool intervals were low I/O. The first zpool iostat
  row is a historical average, not current interval activity.
- proxmox2-old `/var/tmp/kuma-io-diagnostic-proxmox2-old-20261118T191642246593Z`:
  PSI avg10 zero, k3s NRestarts=114 unchanged and API readiness passed. However,
  process I/O includes PostgreSQL PID 3572997 in standalone Docker scope
  `docker-efd0325fc503fb26be5449e811ae8af4416b29b9d3a4c16ea7c23fdb95fd0e6b.scope`
  (1220608 bytes written during the sample) and Java PID 3572727 in
  `docker-ff5164114c53e2fc55d52a6de2eb14cd0087d592c5e33c81016f993298b870f7.scope`
  (8192 bytes written). These processes were not covered by Kubernetes-only
  shutdown verification. Container identities/dependencies remain unknown;
  they are not yet established as the cause of the pressure.

Do not retry copy just because current PSI is zero. Identify standalone Docker
containers on proxmox2-old (and check the other storage host for this coverage
class), determine whether they are application or essential-infrastructure
services, save current running/restart/orchestration state, and handle relevant
application shutdown/restoration deliberately before another attempt. Do not
stop unnamed database/Java processes or all Docker containers blindly. Existing
Kubernetes maintenance state stays in place. No new cap reduction/change or
retry has been requested/applied. The verified preservation image remains
unchanged and the aborted working image remains unverified.

Next read-only identification on proxmox2-old:

```bash
sudo docker ps --format 'table {{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Status}}'
```

Do not publish full Docker inspect output: environment/configuration may
contain credentials. Inspect only selected identity/state/restart-policy,
compose/orchestration labels and mount fields when planning further actions.

### Standalone Compose scope identified; user authorizes temporary pause

Operator identified the non-k3s containers on proxmox2-old:

- `home-assistant-watchdog-springboot-1`, ff5164114c53, home-assistant-watchdog:local;
- `home-assistant-watchdog-postgres-1`, efd0325fc503, postgres:latest;
- `scrutiny`, 01af255a85bb, ghcr.io/analogj/scrutiny:master-omnibus.

These are separate Docker Compose services, not missed k3s pods. Their I/O
is relevant to the host, but no causal link to the spike is established.
The user explicitly said they can be stopped if that helps. Prepared the
incident-specific `pause-kuma-host-compose.py` on proxmox2-old:

```bash
sudo python3 pause-kuma-host-compose.py plan
sudo python3 pause-kuma-host-compose.py stop --execute
sudo python3 pause-kuma-host-compose.py verify
```

It saves only selected non-secret identity/running/restart-policy/Compose-label
fields under private `/var/tmp/kuma-host-compose-TIMESTAMP/baseline.json`.
The baseline is reused rather than overwritten; exact full IDs are checked
against the original incident prefixes. Auto-remove containers, replacement
IDs or unexpected policy changes block. Stop disables Docker auto-restart on
all three, confirms that change, then gracefully stops watchdog application,
Scrutiny and PostgreSQL in that order. It uses Docker's configured stop signal
and timeout -1 so Docker does not escalate to SIGKILL; the client call is
bounded to 140 seconds and refuses to proceed if termination is not confirmed.
Configured kill signals are rejected. Container records, volumes and data are
retained. Logs distinguish requested changes from observed stops/policies.
External Compose/systemd/operator start actions are not disabled: do not run
Compose up or restart these stacks during maintenance, and investigate any
reappearance before transfer. No blanket Docker shutdown occurs.

Restoration for after recovery validation or deliberate abort:

```bash
sudo python3 pause-kuma-host-compose.py restore --execute
```

It starts originally running PostgreSQL first, waits for its Docker health
check to be healthy, then starts Scrutiny and watchdog, preserving containers
originally stopped. Finally restores exact saved restart policies and verifies
running/policy state. Missing database health check blocks automatic watchdog
startup until separately assessed. The operator must also verify application
functionality afterward. This is separate from Kubernetes restoration; both
restoration baselines must be preserved. Default-no dialog is used for mutation,
with explicit `--yes` fallback. No new copy/cap change is triggered.

Mocked tests covered saving baseline, restart disable before stops, application
before database stop, and absence of kill/deletion commands. Docker's documented
negative stop timeout avoids automatic SIGKILL:
[Docker stop documentation](https://docs.docker.com/reference/cli/docker/container/stop/).
Live execution is pending. After verified pause, sample pressure/host readiness
again before deciding on a new working-copy attempt; keep the verified
preservation image and failed partial attempt unchanged.

### Standalone Compose pause confirmed — 19:23:46 UTC

Operator plan saved originals at
`/var/tmp/kuma-host-compose-20261118T192324749221Z`: all three containers were
running with `unless-stopped` restart policy. Execution disabled and verified
restart policies from 19:23:41.023139–19:23:41.329308 UTC. Graceful stops were
confirmed for watchdog at 19:23:42.233999 UTC, Scrutiny at 19:23:45.581605 UTC,
and PostgreSQL at 19:23:46.152762 UTC. At 19:23:46.235045 UTC the script
confirmed all three stopped/restart-disabled. No forced kill, container/volume
removal or database repair was reported. Preserve this separate restoration
baseline; restore after recovery validation/abort using the documented host
Compose restore mode, with database health before watchdog startup.

Added a peer-monitor guard to the preservation/working-copy worker: the existing
non-root SSH command now reads populated `/sys/fs/cgroup/system.slice/docker-*.scope/cgroup.procs`
and refuses if standalone Docker tasks reappear. This matches the cgroup layout
actually observed on proxmox2-old; it is not general coverage of rootless Docker
or all possible container runtimes. Read errors block rather than disabling
monitoring. It requires no Docker-socket privileges or remote sudo. Future
rootless/new runtime services still require manual scope review. Code syntax
checked; live peer check pending.

Next step is another working-copy dry run with the same fixed 1 MiB/s cap and
all current maintenance/host/storage guards. It copies from the verified
preservation image into a NEW directory and does not resume or overwrite the
failed partial image. No new transfer or cap has been requested/applied by this
preparation change. Low pressure and successful guards are required before
choosing execution. Pausing Docker is a mitigation, not proof of the spike's
cause or a storage repair.

### Working-image retry verified — 19:37:43 UTC

Operator completion confirms successful working copy under
`/var/tmp/kuma-working-20261118T192530502463Z/working.img`.
At 19:37:43.173498 UTC `image_verified` recorded 268435456 bytes and the
same SHA-256 `3470b1fd2130d7b2ed136725941695177146a753f9593436af8460ce8d1666a1`,
purpose working-copy, sourceWritten=false, mountOrRepairAttempted=false,
source preservation image path unchanged. At 19:37:43.175296 UTC the worker
confirmed cap 1048576 -> 0, handles closed/worker idle, local/peer PSI zero.
All supplied late-phase observations from 19:36:29 UTC showed PSI zero.
Exact initial request/confirmation times and full phase observations remain
in this retry directory's `actions.jsonl` and `cap-changes.jsonl`; they were
not included in the supplied tail. No intervening cap change was reported.
The earlier failed working-copy directory remains unverified and unused.

Successful retry after pausing Docker does not prove those applications caused
the earlier spike, and no hardware/storage repair has been established.
Kubernetes maintenance and standalone Compose pause must remain in place
through remaining filesystem/database validation.

Prepared `recover-kuma-working-journal.py` for this EXACT verified working
image, with no configurable source-device target:

```bash
sudo python3 recover-kuma-working-journal.py
sudo python3 recover-kuma-working-journal.py --execute
```

Dry-run validates regular file/size/single hardlink, recorded checkpoint hash/
identity/purpose/path, separation from preservation file, no symlink and no
loop-device association; prints the two planned commands. Execution uses
default-no dialog, rechecks Kubernetes maintenance and local low PSI, validates
identity again, and writes a durable attempt marker before any operation.
It runs `e2fsck -p -E journal_only working.img` to replay journal ONLY on the
working file, then `e2fsck -f -n working.img` for full no-write validation.
Each command is bounded to 60 seconds. It does not use -y or automatically
escalate beyond journal-only recovery; unacceptable status or textual warnings
block. Original device and preservation image are never opened for writing.
No filesystem mount, application start, file extraction or SQLite operation
occurs. No migration-transfer cap change is made: this is a local filesystem
operation, not another copy. It has no guaranteed filesystem-operation I/O
bandwidth limit; assess pressure separately and do not run concurrent workers.

Logs/attempt/result markers are written inside the exact working directory.
After any attempted journal replay, do not trust the original pre-replay
working-file checksum as its current content hash. A rerun refuses when an
attempt marker exists; inspect outcomes rather than repeating blindly. A
successful `filesystem-validated.json` means only the working-image filesystem
check passed; database/WAL integrity remains to be tested on extracted working
files. Preservation image and source remain rollback material.

Mocked guard checks covered accepted exact working image, prior-attempt refusal
and loop-device refusal. An actual synthetic ext4 command test could not run
in this sandbox because mke2fs/e2fsck are unavailable; live dry run/execution
on proxmox3 is pending. Do not claim the journal has been replayed yet.

### Working journal replay completed; free-block counter still inconsistent

Operator executed `recover-kuma-working-journal.py --execute` against the exact
verified working image. Journal-only command reported `recovering journal`.
Subsequent `e2fsck -f -n` completed five passes but reported
`Free blocks count wrong (41283, counted=41281)` and answered `Fix? no`.
The wrapper refused validation because textual inconsistency remained despite
an acceptable numeric exit status. No filesystem-validated marker was written.
This is a two-block free-count discrepancy in the current working-image view;
it does not establish database corruption, data loss or safety to start Kuma.
Original device/preservation image were unchanged. Working-image attempt,
command output and result files are inside the working directory.

Prepared `repair-kuma-working-filesystem.py` for a separate reviewed operation:

```bash
sudo python3 repair-kuma-working-filesystem.py
sudo python3 repair-kuma-working-filesystem.py --execute
```

This targets only the same exact working image after validating its prior
journal-replay/check results and previous free-count warning. It refuses an
existing repair-attempt or filesystem-validated marker. Commands are
`e2fsck -f -p working.img` (force check, automatic safe repairs only) followed
by `e2fsck -f -n working.img`. There is no -y fallback. Automatic-repair exit
0/1 is accepted; uncorrectable/operational results stop, and the subsequent
read-only check must exit 0 without unresolved warnings. It checks maintenance,
local PSI, file identity/single link/no loop association and records an attempt
before changes. Each e2fsck command has a 60-second bound. Original block
device and preservation image are never repair targets; only the disposable
working file may change. No mount, copy, cap change or app startup occurs.

Logs `working-repair.txt`, `filesystem-after-repair.txt`, result files and a
successful `filesystem-validated.json` stay inside the working directory.
Do not repeat after any attempted failure; assess logs. Database/WAL recovery
and SQLite validation remain separate later steps. Mocked command tests
verified forced automatic repair addresses only working.img, accepts corrected
exit 1, and blocks validation on uncorrectable exit 4. Live execution pending.

### Working-image filesystem repaired and verified

Operator ran `repair-kuma-working-filesystem.py --execute` on the disposable
working image. Forced automatic repair completed; subsequent forced no-write
check completed all five passes with no free-count warning: 17/65536 files,
24255/65536 blocks. Wrapper reported working-image filesystem validation
passed and wrote `filesystem-validated.json`. No original-device/preservation
repair occurred. SQLite/WAL integrity remains untested.

Prepared next stage:

```bash
sudo python3 validate-kuma-working-database.py
sudo python3 validate-kuma-working-database.py --execute
```

Dry-run verifies the exact working-image path/size/single-link, filesystem-
validated marker and no current loop attachment, and prints the plan. Execution
asks default-no dialog or explicit --yes, checks Kubernetes maintenance,
both-host PSI/k3s identity and peer standalone-Docker exclusion, then mounts
ONLY this working image as `loop,ro,noload`. It verifies a read-only ext4 mount.
Original device/preservation image are not opened. The image is mounted only
for source-file extraction, not for SQLite recovery.

It extracts exactly `kuma.db`, `kuma.db-wal` and `kuma.db-shm` into a new private
`/var/tmp/kuma-database-validation-TIMESTAMP/database` directory. All must be
regular non-symlink files; database/WAL must be nonempty. Copy and per-component
checksum reread use the same 1 MiB/s/64 KiB pacing, with requested/confirmed
cap records, both-host I/O/k3s/Docker checks about every five seconds and
maintenance verification about every 30 seconds. Failure closes files, logs
confirmed cap zero, retains partial evidence and attempts normal unmount; no
force-unmount or copy retry. Existing source-component sizes/hashes are saved.
The plan is not yet a requested/applied live cap.

After hash verification it unmounts the working image and confirms copy cap
zero. Only then opens the extracted database with ordinary SQLite `mode=rw`,
NOT immutable mode, with WAL/SHM present. SQLite can recover/checkpoint/recreate
these disposable working files; it cannot modify the mounted image (already
unmounted), original device or preservation. It sets query_only for SQL,
runs full `PRAGMA integrity_check`, requires exactly `ok`, and reports only
safe counts for monitor/user/heartbeat/notification tables and table count.
No monitor URLs, credentials, notification settings or user records are printed.
A 60-second SQLite progress deadline applies; filesystem/SQLite integrity and
application data recency/behavior are distinct checks.

Output/logs stay private in the validation directory. `database-validated.json`
means only this recovered/extracted working database passed SQLite integrity;
it does not mean the original volume was repaired or cut over, all committed
application history was established, or Kuma may safely restart there. Keep
all applications stopped through recovery/cutover planning. SQLite checks are
bounded local filesystem operations rather than transfer-cap-governed bulk
copy; pressure/maintenance are checked before/after them, not continuously
inside SQLite. Extraction is the monitored capped phase.

A real synthetic SQLite test verified that copying database/WAL/SHM and opening
normally includes a committed WAL-only row missed by immutable main-database
access; integrity_check returned ok. Help/syntax checked. Live extraction and
validation pending; no new transfer cap or extraction has yet been executed.

### SQLite/WAL working-copy validation passed — 19:50:08 UTC

Operator extraction/validation directory:
`/var/tmp/kuma-database-validation-20261118T194659989258Z`.
Working image mounted read-only at 19:47:08.558997 UTC, kernel options
`ro,relatime,norecovery` (/dev/loop0). At 19:47:08.561682 UTC copy cap 0 ->
1048576 bytes/s requested, confirmed live by PID 1765297 at
19:47:08.565292 UTC, chunk 65536 bytes, both-host PSI zero.
Extracted/checksummed components:

| Component | Bytes | SHA-256 |
| --- | ---: | --- |
| kuma.db | 61296640 | 2e1f2d941896e4b9d27dee8a3f055fc302583a2e4db691568ec0506de3ab22a5 |
| kuma.db-wal | 4136512 | 8641f002e7031de230d0a22ac99ae34fb03f2bf01825352fdb263a95e267ff80 |
| kuma.db-shm | 32768 | aec4e681569c18b73a6519bee0e98f6cdaca427bccfb319699a3dc8d30c4e4fb |

These component hashes describe extraction BEFORE SQLite may recover/checkpoint
its disposable files; do not assume they are current hashes after SQLite close.
The working-image database size grew 4096 bytes relative to the pre-journal
listing; the listed/hash-verified files here came from the recovered filesystem.
At 19:49:51.071433 UTC the working image was unmounted. At
19:49:51.073656 UTC cap 1048576 -> 0 was confirmed, handles closed/copy idle.
Both-host PSI was zero at each supplied observation/transition; no cap
reduction/change occurred during extraction except final stop. Full cap/action
records remain in the validation directory.

SQLite 3.46.1 opened normally with WAL supplied (immutable=false). At
19:50:08.651915 UTC `sqlite_integrity_passed`: integrityCheck=ok, 23 tables,
monitor=9, user=1, heartbeat=382374, notification=1. Original device and
preservation untouched. This establishes recovered working-database structural
integrity, not original-volume repair, application behavior or complete data
recency. The live original remains attached/unmounted and Kuma remains zero.

### Next original-volume repair prepared, NOT yet executed

Prepared `repair-kuma-original-filesystem.py`, dry-run by default:

```bash
sudo python3 repair-kuma-original-filesystem.py
sudo python3 repair-kuma-original-filesystem.py --execute
```

Unlike prior working-image commands, this operation WRITES filesystem metadata
to the original unmounted Kuma block device on proxmox3. It is gated on the
exact verified preservation checkpoint, no write bits on its image, successful
working filesystem and SQLite/WAL validation, exact original volume UID/UUID,
attachment only to proxmox3 with the known ticket/count 1, paused Kuma/zero
replicas, no PVC consumer pods, no device mounts in visible namespaces/holders,
cluster maintenance, peer Docker pause and both-host low PSI/active stable k3s.
The filesystem checker helper now exposes `source_guard()`; its normal CLI
continues to perform the same read-only check.

Dry-run prints the exact resolved original-device commands. Execution requires
default-no confirmation (or explicit --yes): journal-only recovery, forced
automatic repair (`-f -p`), then forced no-write check (`-f -n`). These are the
operations tested successfully on the working image; there is no -y/format/
forced attachment. Each is bounded to 60 seconds with unmounted/host health
rechecks between phases. Automatic safe-repair exit 0/1 is accepted; the final
check must be exit 0 without warning text. Pressure is checked BETWEEN commands,
not continuously inside e2fsck, and filesystem operations have no guaranteed
bandwidth cap. No bulk copy or transfer cap change is part of this step.

It records `repair-started.json`, phase outputs/status/pressure and a successful
`original-filesystem-validated.json` under private `/var/tmp/kuma-original-repair-*`.
Any previous original repair attempt blocks a blind retry. Partial failure must
be assessed using those logs; preserve the image and source ownership. It never
mounts, starts Kuma, changes placement, or detaches. After success leave the
source unmounted and Kuma stopped for controlled manual-ticket/CSI handoff,
placement on proxmox3 and later readiness/data validation. No restore-to-new-PVC
or database-file replacement is performed: proposed recovery is filesystem
repair in place using the proven copy outcome, with the verified preservation
image retained for rollback.

Mocked execution checked preservation/working-validation gates, exact original
target and phase order, approved flags only, and completion marker after all
checks. Live dry run/execution of original repair pending; do not describe the
original filesystem as repaired yet. All Kubernetes and approved standalone
Docker maintenance states remain in effect.

### Original Kuma filesystem repaired and validated — 19:53 UTC

Operator executed original-volume repair with evidence at
`/var/tmp/kuma-original-repair-20261118T195344656712Z`.
Journal replay reported recovery; forced automatic repair and subsequent
forced no-write check completed successfully, all five passes and no warning:
17/65536 files, 24255/65536 blocks. `original-filesystem-validated.json` was
written. Original remains unmounted and Kuma stopped. This is filesystem
recovery, not repair of the underlying host/storage incident or restored
replica redundancy. Preservation image remains unchanged. No copy/cap change
occurred during the repair.

Prepared `restore-kuma-service.py`:

```bash
sudo python3 restore-kuma-service.py
sudo python3 restore-kuma-service.py --execute
```

Dry-run requires exact original repair marker and current original-volume
source guards. It prints placement/ticket/start changes. Execution uses default-
no dialog (or explicit --yes), verifies maintenance and source guards again,
saves exact deployment and attachment originals under private
`/var/tmp/kuma-service-restore-*`, and logs requested/confirmed handoff steps.
It preserves existing nodeSelectors while adding hostname proxmox3, removes
only podAffinity (retaining other affinity), keeps zero replicas during placement,
and removes ONLY `longhorn-api-kuma-validation-20261118` from the attachment.
It waits for normal detach with empty tickets/node references and device
removal; any other ticket blocks, and no force-detach is used. Then it verifies
Kuma Bundle still paused, starts one replica, waits up to 180 seconds for
rollout, and confirms a single intended-PVC consumer on proxmox3 and volume
attached there. Success marker `service-started.json` is readiness/placement
only: inspect application logs, login/data and notifications separately.

Kuma startup resumes normal configured monitoring/notifications while other
applications are still stopped; this is explicit in plan/confirmation. It
never changes database files, starts another application, unpauses Fleet,
uncordons the old node or changes desired Longhorn replica count 1. A previous
handoff attempt blocks blind rerun. Partial failure must be assessed from saved
state/logs. Do not automatically restart an old-source instance.

Kubernetes restoration now supports optional `--keep-recovered-kuma` ONLY for
restore mode: preserves a deliberate one-replica recovered Kuma instead of
requiring its original temporary zero count. It requires the separate verified
startup marker, actual ready replica 1 pinned to proxmox3 and live attachment
there; original baseline is unchanged. Other originally zero workloads stay
zero. Use this only AFTER separate Kuma service validation:

```bash
sudo python3 kuma-recovery-maintenance.py restore --keep-recovered-kuma
sudo python3 kuma-recovery-maintenance.py restore --keep-recovered-kuma --execute
```

Do not restore unrelated apps yet: first execute/review Kuma handoff and
validate actual service/data. Restore standalone Docker separately from its
saved baseline after Kubernetes/application recovery as appropriate. Persistent
Kuma placement and Fleet reconciliation remain follow-up; live placement patch
is not Git persistence. Do not unpause Kuma until intended chart values are
reviewed. Mocked handoff tests verified selector/other-affinity preservation,
exact named-ticket removal, normal detach before start, success marker and
retained Fleet pause; existing maintenance regression tests still pass.
Live handoff execution pending.

### Kuma service recovered on proxmox3 — 19:57:52 UTC

Operator handoff evidence:
`/var/tmp/kuma-service-restore-20261118T195711594510Z`.
Placement confirmed at 19:57:11.665262 UTC; normal detach/device removal at
19:57:15.620661 UTC after removing only the validation ticket; one-replica
startup requested at 19:57:15.744604 UTC. Rollout succeeded and
`kuma_ready_on_proxmox3` was logged at 19:57:52.026455 UTC for
`uptime-kuma-77dc9cb569-ns26c`.

Operator startup logs: Kuma 1.23.17 connected successfully to SQLite in WAL
mode, schema version 10 matched latest and no patch was needed, server listening
on 3001. No database/filesystem error appears in supplied startup output.
`Failed to start nscd` did not prevent serving/readiness. Rancher monitor 503
is consistent with Rancher still stopped in maintenance. Proxmox2-old port 443
ECONNREFUSED is a separate endpoint/service finding, not evidence of renewed
SQLite corruption; do not attribute or repair it without inspection.
Actual UI login/visible monitors/notification configuration still require
operator confirmation; working-copy counts (9 monitors, 1 user) are recorded
above. Configured monitors/notifications have resumed; unrelated apps remain
stopped.

Next restore unrelated Kubernetes workloads gradually while preserving deliberate
Kuma startup AND Longhorn incident pause:

```bash
sudo python3 kuma-recovery-maintenance.py restore --keep-recovered-kuma --keep-longhorn-paused --execute
```

Added `--keep-longhorn-paused` for restore mode only. It retains the saved
maintenance pause of fleet-local/longhorn and logs the intentional difference
from its original unpaused baseline, pending review of manager maintenance
toleration/OnDelete and chart reconciliation effects. It does not modify the
saved original. Kuma remains paused from its pre-maintenance baseline. Other
schedules/pauses/counts restore from the saved baseline; Filebot stays zero.
This avoids immediately reconciling away incident manager changes. Restore
databases before frontends and wait for rollout; abort on high local pressure
or failures, and inspect rather than bypass guards. No copying is active.
Peer I/O still requires separate observation during restoration.

Regression tests covered database-before-frontend restoration, explicit
marker/ready/placement/storage validation for keeping Kuma at 1, retained
Longhorn pause, and restored other saved replicas. Live Kubernetes restoration
not yet reported. Standalone Docker remains stopped/restart-disabled until
separate restoration. Placement persistence in Git, deliberate reconciliation,
Longhorn redundancy and underlying incident diagnosis remain follow-up work.


## Kubernetes restore completion — 19 November, 06:52:04 UTC

Operator final event at 06:52:04.382382 UTC:
`saved_values_restored_reconciliation_and_final_health_require_verification`.
The executor finished saved-value restoration, preserving recovered Kuma,
original Filebot zero and deliberate Kuma/Longhorn pauses. Last mutations
confirmed CAPI/Fleet/gitjob/helmops/system-upgrade/Turtles/agent desired replicas
1 and Rancher 3. These controller mutations did not include readiness waits;
check current deployments, API readiness and pressure before proceeding.
Standalone Compose remains stopped as last observed and still needs separate
restoration. Do not describe the full incident as resolved: redundancy,
underlying cause, placement persistence and deliberate reconciliation remain
open. No copying or transfer-cap change occurred during this completion.
The linked quick handoff is updated; older running/paused restoration statements
are historical snapshots, superseded by this completion event.

### Kuma Fleet dry-run JSON conversion fix — 19 November, 14:17 UTC

Operator dry run created evidence directory
`/var/tmp/kuma-fleet-placement-20261119T141729437081Z` then failed with
`Extra data: line 37 column 1 (char 1046)`. The preview converter incorrectly
assumed kubectl multi-document `create --dry-run=client -o json` emits a single
JSON document. It can emit consecutive resource objects. Updated both chart-
render and Fleet-preview conversions with strict streaming JSON decoding and
Kubernetes List flattening. Single objects/Lists remain supported; malformed,
empty or trailing non-JSON output still fails. No schema validation was disabled.
No cluster mutation occurs in this dry-run path; native apply remains pending.
Tests covered concatenated resource objects, Lists, single objects and malformed
input plus the existing mocked Fleet workflow. Re-run the same dry run before
execution; Fleet values remain not yet eligible for commit/push.

### Post-Kuma storage assessment — operator node/volume/Fleet snapshot

New operator output confirms Kuma Bundle 1/1 Ready. Longhorn Bundle is 0/1
NotReady, reporting manager DaemonSet progressing Updated 1/2 and updateStrategy
modification involving rollingUpdate.maxUnavailable=100%. Do not apply that
reported strategy or restart/delete managers blindly; temporary OnDelete/
maintenance toleration changes need comparison with live strategy/pod revisions.
A Fleet strategy/rollout warning alone does not establish volume failure.

Kubernetes lists only proxmox2-old (Ready,SchedulingDisabled, v1.36.4+k3s1) and
proxmox3 (Ready, v1.36.6+k3s1). Proxmox5 is absent as a Kubernetes Node; this
neither disproves its earlier etcd-voter membership nor proves current three-
voter health. Fresh etcd membership/endpoint health is required before any old-
node shutdown/removal; no shutdown is authorized by the Node list.

Both Longhorn nodes report Ready, but only proxmox3 allows/is eligible for new
replica scheduling. Many attached volumes remain degraded; some are healthy.
Kuma is attached/healthy on proxmox3, but desired replicas remain 1 as last
explicitly set; healthy relative to that count is not redundancy. Other healthy
volumes may still have existing replicas on the old node: scheduling disabled
does not remove or invalidate those existing replicas. Detached/unknown entries
are not evidence of loss without examining desired state/consumers/replicas.
No replica deletion/count increase/scheduling change or PVC migration occurred.

Prepared read-only `assess-longhorn-redundancy.py` on proxmox3. It saves full
node/volume/replica/engine/manager/pod/Bundle evidence privately and prints compact
per-PVC desired/running/RW counts and nodes, live manager strategy/revisions,
node controls and actual Bundle pause state. Running/RW records do not prove
filesystem health. Test covered explicit distinction between desired-one healthy
and replicated data. Live execution pending. Root asked whether the next plan
should retire proxmox2-old, return it after investigation, or assess first;
answer remains pending. Proxmox5 remains planned control-plane only, not an
assumed storage target. Any future migration follows fresh cluster-wide shutdown
and cap-record requirements; the current assessment performs reads only.
