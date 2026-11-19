# Proxmox2-old storage/runtime incident — 2026-11-18

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

## Current checkpoint — restoration started at 20:00:34 UTC

This summary supersedes older status snapshots below. Times in this checkpoint
are UTC; some earlier handoffs explicitly use CET. State is based on operator-
provided output, not independent live monitoring from the preparation sandbox.

- Kuma's original filesystem was repaired and passed a full no-write check.
  A preserved raw image and verified working/extracted recovery data are retained.
- Recovered working SQLite passed integrity_check with WAL included: 9 monitors,
  1 user, 382374 heartbeats, 1 notification. Actual UI/login confirmation is pending.
- Kuma is running at one Ready replica on proxmox3, pod
  `uptime-kuma-77dc9cb569-ns26c`; startup logs show successful DB connection
  and HTTP serving. Manual validation attachment was removed with normal detach
  before CSI handoff. No DB/filesystem errors appeared in supplied startup logs.
- Kubernetes restoration STARTED at 20:00:34.835690 UTC with
  `restore --keep-recovered-kuma --keep-longhorn-paused --execute`.
  Bookstack MariaDB scale 0 -> 1 was confirmed at 20:00:34.985976 UTC;
  last supplied output was waiting for that rollout. Its readiness and final
  restoration completion have NOT been reported. Do not launch a second restore
  instance while the first may still be running.
- Keep recovered Kuma at 1, original Filebot at 0, and Kuma/Longhorn Bundles paused.
  Other saved replicas/schedules are being restored gradually, not yet confirmed
  restored. The original backup schedule is still subject to that restoration.
- Three approved standalone Docker containers on proxmox2-old remain stopped
  with restart disabled, unless a later verified action supersedes this checkpoint.
  Their separate restoration is outstanding.
- Kuma's desired Longhorn replica count is still 1. The old node remains
  cordoned/tainted with Longhorn node/disk scheduling disabled. Underlying cause,
  full storage redundancy, placement persistence and reconciliation review remain
  unresolved. Kuma service recovery is not whole-cluster incident resolution.

Restoration state/action log:
`/var/tmp/kuma-recovery-maintenance-20261118T180953924876Z/maintenance-actions.jsonl`.
Compose baseline: `/var/tmp/kuma-host-compose-20261118T192324749221Z`.
Preservation: `/var/tmp/kuma-preservation-20261118T184540364827Z`.
The detailed [recovery record](longhorn-shim-recovery-2026-11-18.md) records
filesystem/database evidence, copy-cap request/confirmation/abort history,
exceptions and guarded scripts. Do not blindly rerun historical commands below.

## Findings
- Two k3s control-plane/etcd nodes: proxmox3 and proxmox2-old. Both report Ready; Longhorn on proxmox2-old does not. Node Ready is insufficient to establish storage health.
- Longhorn manager failed, later stuck Terminating. Instance manager ContainerStatusUnknown; eviction blocked by PDB. Drain policy block-if-contains-last-replica was retained.
- Numerous Longhorn ext4 mounts on proxmox2-old report emergency_ro, including application configuration, mail and encrypted Piwigo database storage. This establishes write protection after filesystem/storage errors, not a proven hardware fault.
- Kuma device is sdx, major:minor 65:112, matching PVC pvc-326def38-faba-49f6-8a78-ebf719227220. Model VIRTUAL-DISK. Kernel read errors predate drain; several virtual devices had superblock write errors during drain. Investigate underlying storage, networking and engine failures; physical failure is not established.
- NFS 10.43.12.19 is the Longhorn service for Homer Test RWX PVC pvc-3bc8aad0-e2c6-41d9-9923-1bdc76670b8c. Repeated NFS timeouts; replacement share-manager not ready. df hung while querying mounts.
- Containerd list operations succeed, but deletion/stop operations time out. Application tasks were STOPPED, reported PIDs absent; containerd repeatedly replayed TaskExit while failing task deletion and init-process I/O drain. This supports stale shim/task state; initial cause remains unproven.
- Proxmox4 instability not diagnosed: SSH host verification blocked investigation. No verified correlation with PBS load or shutdown.

## Actions already performed
- Explicit kubeconfig /etc/rancher/k3s/k3s.yaml works; default kubectl endpoint returned NotFound.
- Kuma scaled from 1 to 0 and Fleet Bundle fleet-local/uptime-kuma paused to stop notifications; restoration TODO stored in apps-uptime-kuma/uptime-kuma-maintenance-todo.
- proxmox2-old cordoned and tainted maintenance=storage-repair:NoSchedule. Drain used normal eviction API with ignore-daemonsets, delete-emptydir-data, force (for unmanaged old test pod), timeout 60s. No force deletion/PDB bypass. Many infrastructure replicas recovered on proxmox3; drain incomplete.
- Nine old app pods plus infrastructure records were stuck terminating; DaemonSets remain by design, Fleet agent recreated with tolerations. Node remains an etcd voter; draining does not make shutdown safe.
- Removed three abandoned Jenkins agents (builds 48,49,50), whose jnlp connections ended November 14. Confirmed pods gone: released 4200m CPU and 11712Mi requested memory. proxmox3 already exposes all eight physical i7-9700 cores.
- Removed podAffinity from 24 blocked app deployments; changed calibre, calibre-web and kavita hostname selectors from proxmox2-old to proxmox3. Paused 21 affected Fleet Bundles; saved originals in kube-system/drain-placement-backup-20261118. Git persistence and later unpause remain TODOs. Shared Calibre RWO users must stay colocated.
- Traefik second replica remains blocked by host-port collision. No unsafe port or replica adjustment performed.
- No PVC copy migration or transfer-cap change was performed.

## Third control-plane node plan — proxmox5

To make evacuation of `proxmox2-old` safe, prepare `proxmox5` as a third
k3s server with embedded etcd, provisioned by Ansible from the NAS. This node
does **not** need a Longhorn data disk and should be dedicated to
control-plane/etcd duties. It must have reliable low-latency connectivity to
the existing k3s servers and a compatible k3s configuration.

Keep application workloads and Longhorn replicas off the new node. Before
joining it, prepare the intended taint/labels and verify that Longhorn's node
and disk scheduling rules will not create a disk or replica there. After it
joins, verify three healthy etcd voters and API readiness before taking any
action against `proxmox2-old`:

```sh
kubectl get nodes -o wide
kubectl get pods -n kube-system -o wide
kubectl get --raw='/readyz?verbose'
```

The third node provides quorum protection; it does not resolve the storage/
runtime incident, `emergency_ro` filesystems, stale container tasks, or degraded Longhorn
volumes on `proxmox2-old`. Do not uncordon or power down `proxmox2-old` merely
because `proxmox5` joined. First recover/validate Longhorn attachments and
replicas, then remove the old etcd member only as a separate planned step.

Provisioning status: **not yet performed in this recovery session**; the
operator plans to run the existing Ansible provisioning from the NAS.

## Proxmox5 handoff findings — 2026-11-18

The operator provisioned `proxmox5` at `10.0.0.80` and verified etcd from
`proxmox3`. The cluster currently has three **started, non-learner etcd
members**:

| Name | Peer | Client | State |
| --- | --- | --- | --- |
| `proxmox2-old-42c02ed9` | `10.0.0.72:2380` | `10.0.0.72:2379` | started voter |
| `proxmox3-cdccb8cb` | `10.0.0.50:2380` | `10.0.0.50:2379` | started voter |
| `proxmox5-a5d14641` | `10.0.0.80:2380` | `10.0.0.80:2379` | started voter |

This satisfies the etcd quorum-protection requirement for later maintenance
of `proxmox2-old`. It does not by itself repair Longhorn or authorize shutting
down the old node.

### Proxmox5 k3s registration issue

The first `proxmox5` k3s starts failed repeatedly with:

```text
critical configuration mismatched: egress-selector-mode
failed to bootstrap cluster data: failed to validate server configuration
```

Later, at approximately 17:41:44 CET, k3s started successfully, received an
etcd snapshot, promoted `proxmox5` from learner to voter at approximately
17:42:00, and reported `ETCD server is now running` / `k3s is up and running`.
The service command is `/usr/local/bin/k3s server`; the inspected systemd
environment files were empty and no `--disable-agent` option was found in the
reported service arguments.

Despite the successful server startup, `proxmox5` still does not appear in
the Kubernetes `Node` list. `sudo k3s kubectl get node proxmox5` returns
`NotFound`, while the service is active. No Longhorn node or pod is scheduled
there, which is intentional. The unresolved follow-up is local kubelet/agent
registration, not etcd membership. Inspect the proxmox5 k3s journal for
kubelet/node-registration errors and verify the presence of the agent
kubeconfigs and kubelet process before changing cluster state:

```sh
sudo pgrep -af 'kubelet|k3s agent|k3s server'
sudo ls -l /var/lib/rancher/k3s/agent/
sudo journalctl -u k3s --since '17:42:00' --no-pager | \
  grep -Ei 'kubelet|node|register|forbidden|unauthorized|error|failed'
sudo test -s /var/lib/rancher/k3s/agent/kubelet.kubeconfig && \
  echo kubelet-config-present
```

Do not create a Kubernetes Node object manually. Resolve the registration
through the Ansible/k3s configuration in the separate proxmox5 session.

## Confirmed Kuma recovery
1. Application task STOPPED with absent PID. Exact sandbox shim 2456872 matched full sandbox ID 33699980d0a00f76e82a499cc0a21fbd732eb5eb1b64d846572581bede456107; child tree contained only pause 2456965 and shim threads.
2. Guarded SIGTERM to that shim removed shim and pause. No k3s restart or SIGKILL.
3. Normal crictl rm, stopp and rmp then succeeded.
4. Verified Kubernetes pod absent, deployment still zero; user verified no Kuma mount in host mountinfo.
5. Latest Longhorn check still reports Kuma attached to proxmox2-old, healthy. This is not verified migration to proxmox3; Kuma was NOT restarted. Longhorn status alone cannot validate filesystem usability.

## Script operation
Export recover-stale-app-shims.py from this ConfigMap. Run sudo python3 recover-stale-app-shims.py first. Review eligible/skipped entries, then sudo python3 recover-stale-app-shims.py --execute. Execution uses default-no dialog; if unavailable use --execute --yes only as explicit confirmation.
- Only terminating apps-* pods assigned to proxmox2-old; never infrastructure namespaces.
- Rechecks pod UID, exact sandbox/shim identity and process start time, stopped/absent application tasks, pause-only child tree and absence of other processes in pod cgroups.
- SIGTERM only; sequential normal CRI cleanup; bounded waits. No forced pod deletion, mount/unmount, filesystem repair, detach, k3s restart, replica changes, or automated Kuma startup.
- Dry run still writes local inventory/actions to /var/tmp/stale-app-shims-TIMESTAMP (mode 0700). Errors are logged and skipped; exit 2 means at least one pod was skipped/failed. Script is deliberately conservative and will refuse mixed/live/ambiguous cases.
- Runtime cleanup does not prove mount removal, attachment release, replica health, or filesystem integrity. Check these separately before restoration.
- Do not kill all shims, remove containerd databases, force-format volumes, fsck mounted filesystems or bypass etcd quorum gates.
- Designed around observed Kuma recovery; live execution of the multi-app script has not been tested.

## Remaining work
- Run/inspect script locally; review each result. Infrastructure Longhorn stale shims are excluded and require separate assessment.
- Verify old mounts disappear, clean volume detach/attach, usable replicas, filesystem and application readiness; never force-attach while old writers may exist.
- Start Kuma only after attachment and mount on proxmox3 and healthy readiness can be established; pin placement first if necessary and restore one replica, then deliberately reconcile Fleet.
- Persist intended placement and pause states in Git; repository writes were blocked in this session. Saved originals are in the in-cluster maintenance ConfigMaps.
- Restore Fleet reconciliation carefully, preserving previously disabled applications. Do not uncordon proxmox2-old until repair is validated.
- Establish reliable monitoring/log rotation and investigate original storage/runtime outage and Proxmox4 separately.
- Any future PVC copy migration MUST follow longhorn/README-5-pvc-migration-maintenance.md: save replicas/schedules, pause reconciliation, stop applications cluster-wide, verify stopped, retain essential infrastructure, log copying and every requested/confirmed cap change, validate and restore gradually. This is mitigation for prior severe I/O incidents, not proof of hardware repair.

## Follow-up handoff — 2026-11-18 after 15:50 UTC

The operator applied the manager-only maintenance toleration with
`allow-longhorn-manager-maintenance.py`. Fleet's Longhorn Bundle is paused and
manager strategy is OnDelete; replacement manager on proxmox2-old is 2/2 Ready.
The instance-manager pod is still absent: its CR reports error and explicitly
identifies the Kubernetes cordon as the creation blocker. Attached volumes on
proxmox3 report degraded; Kuma's volume reports attaching/unknown. Recovery is
incomplete. Current apps-* pods have zero matching maintenance tolerations;
controller templates and other placement paths remain to be assessed before
any controlled uncordon. No uncordon, global taint setting change or Kuma
restart was reported.

See [the detailed handoff](longhorn-shim-recovery-2026-11-18.md#handoff-to-codex-on-proxmox2-old--2026-11-18-after-1550-utc)
for exact backup/log location, timestamps, observed settings, source-confirmed
cordon behavior and next checks. These observations supersede earlier status
snapshots above; do not assume the cluster still has that exact state.

## Kuma volume detach checkpoint — 2026-11-18, 18:38–18:50 CET

Kuma remained scaled to zero and its Fleet Bundle remained paused. The
`proxmox2-old` node remained cordoned and tainted with
`maintenance=storage-repair:NoSchedule`; Longhorn node and disk scheduling
were both disabled.

The Kuma volume
`pvc-326def38-faba-49f6-8a78-ebf719227220` initially showed an apparently
stale attachment, but inspection proved that it still had a live Longhorn
engine and iSCSI device on `proxmox2-old`. Its only surviving replica was on
`proxmox3`:

- engine: `...-e-0`, running on `proxmox2-old`;
- replica: `...-r-9cdce044`, on `proxmox3`;
- device: `/dev/longhorn/pvc-326def38-faba-49f6-8a78-ebf719227220`;
- no filesystem mount or active application consumer was found;
- no Kubernetes `VolumeSnapshot` or `VolumeSnapshotContent` existed.

The Longhorn `VolumeAttachment` contained a stale
`snapshot-controller` attachment ticket. After backing up the volume and
attachment YAML under `/var/tmp/kuma-volume-recovery-*`, the ticket was
removed and the Longhorn volume was requested to detach by setting its
`spec.nodeID` to empty. The first detach was immediately reattached by the
stale ticket; the ticket was removed again and the detach request repeated.

The final observed state was:

- volume `detached`, with empty `nodeID` and `currentNodeID`;
- Longhorn attachment tickets `{}`;
- old engine stopped;
- old iSCSI device removed;
- replica CR still present on `proxmox3`, stopped, with data directory
  `/var/lib/longhorn/replicas/pvc-326def38-faba-49f6-8a78-ebf719227220-3b607b98`;
- no PVC data copy, filesystem repair, force detach, replica deletion, or
  transfer-cap change performed.

The volume reported `robustness=unknown` while detached because no engine was
running. This is not evidence of data loss or filesystem integrity. Do not
delete the stopped engine/replica, attach the volume, or restart Kuma until a
separate controlled validation on `proxmox3` is planned. Any PVC migration or
data copy must first follow
`longhorn/README-5-pvc-migration-maintenance.md`, including cluster-wide
application shutdown, reconciliation control, saved state, verified stop and
cap-change logging.

## Kuma validated recovery-copy checkpoint — 2026-11-18, 19:50 UTC

Detailed action/cap history and scripts are in
[the continuing recovery record](longhorn-shim-recovery-2026-11-18.md).
Applications were stopped cluster-wide using saved baseline
`/var/tmp/kuma-recovery-maintenance-20261118T180953924876Z`, preserving AdGuard
DNS/essential infrastructure. Longhorn backup uses a saved temporary January 1
schedule because generated CronJob suspension does not persist. Three approved
standalone Compose containers on proxmox2-old were also paused with saved
restart policies under `/var/tmp/kuma-host-compose-20261118T192324749221Z`.

Verified raw preservation image:
`/var/tmp/kuma-preservation-20261118T184540364827Z/source-preserved.img`.
One working-copy attempt stopped at high PSI; it remains unverified/unused.
The retry `/var/tmp/kuma-working-20261118T192530502463Z/working.img` matched
preservation SHA-256, then underwent journal replay and automatic filesystem
repair. Read-only filesystem validation passed. Database/WAL/SHM were extracted
at a logged fixed 1 MiB/s, verified and unmounted. SQLite working-copy integrity
passed at 19:50:08 UTC: 9 monitors, 1 user, 382374 heartbeats, 1 notification.
Validation evidence: `/var/tmp/kuma-database-validation-20261118T194659989258Z`.

Original Kuma volume remains unmounted on proxmox3, desired replicas 1, Kuma
zero and Fleet paused. It has NOT yet been repaired or restarted. Guarded
`repair-kuma-original-filesystem.py` is prepared for a separately reviewed
original-volume filesystem repair, based on the tested working-image sequence.
Keep maintenance active until source/application validation or deliberate abort;
restore Kubernetes and Compose from their separate saved baselines afterward.
Physical pool health and low pressure are observations, not proof of a repair
to the underlying incident cause or of restored Longhorn redundancy.

## Original Kuma repair checkpoint — 19:53 UTC

Original filesystem recovery was executed on the unmounted Kuma block device
on proxmox3 after successful preservation, working-filesystem and SQLite/WAL
validation. Journal replay, automatic safe repair and full read-only check
passed without warnings. Evidence:
`/var/tmp/kuma-original-repair-20261118T195344656712Z`.
Kuma is still stopped; `restore-kuma-service.py` is prepared for reviewed
placement/normal detach/manual-ticket handoff and startup on proxmox3 with Fleet
paused. Do not treat this filesystem repair as a repair of the incident's
underlying cause or as restored Longhorn redundancy. See the detailed record
for prerequisites, rollback evidence and separate Kubernetes/Compose restore
commands. Live Kuma startup has not yet been reported.

## Kuma service recovered — 19:57:52 UTC

Normal validation-ticket detach/CSI handoff succeeded, Kuma was pinned to
proxmox3 and started at one replica. Pod `uptime-kuma-77dc9cb569-ns26c` became
Ready; startup logs show successful SQLite WAL connection, schema current and
HTTP serving. Evidence: `/var/tmp/kuma-service-restore-20261118T195711594510Z`.
No DB/filesystem error appears in supplied logs. Operator UI/data confirmation
is still pending. Rancher 503 is expected while Rancher remains stopped;
Proxmox2-old HTTPS refusal is separate and undiagnosed.

Gradual Kubernetes restoration is prepared with `--keep-recovered-kuma
--keep-longhorn-paused`; both Kuma and Longhorn Bundles stay paused until
reconciliation/incident settings are reviewed. Preserve Filebot's original zero
and restore other replicas/schedules from the maintenance baseline. Standalone
Compose restoration remains separate. Do not claim the whole incident resolved:
Longhorn desired count is still 1 for Kuma, old node is cordoned/tainted, and
underlying storage/runtime cause is unproven.


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
