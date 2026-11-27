# Piwigo recovery, 27 November 2026

## Current status

Independent host readback after the local-only apply confirms both Bundle and
BundleDeployment at chart 22.3.2, both paused, with the corrected deployment ID
applied and Ready. The Bundle may display WaitApplied while paused; that is
not evidence the working application is down. The selected claim is externalized
from these Fleet objects; actual production mount/data validation remains the
source of truth for the active claim.

Use `finish-restoration.py --verify-only --allow-paused-piwigo` for the chosen
local-only end state. It accepts only the specific Piwigo pause exception,
requires desired/applied chart 22.3.2 and an applied Ready deployment, and lists
the intentional holds in its report. It still verifies unrelated pause states,
approved replica counts, controls, schedules, backup-group selection, retained
claims and local API/k3s stability. Default verification remains strict.
No publication or unpause is required to keep the already-recovered gallery
serving; the holds must remain until published source is safe to reconcile.

User subsequently requested applying without pushing. At 13:40:14 UTC the
native `fleet apply --paused` command reported `updated (bundle):
fleet-local/piwigo` for the local chart 22.3.2/recovered-claim configuration,
using the existing GitRepo ownership label and established relative directory.
No commit or push occurred. GitRepo and Piwigo reconciliation must remain
paused while the published source still selects chart 23.0.0. Direct kubectl
and Fleet dump readback attempts were blocked by this tool session's socket
restriction; this latest Bundle update has a successful CLI confirmation but
no independent live readback yet. The earlier actual gallery recovery was
fully validated as described below; paused Bundle editing starts no database
copy or application rollout.

Piwigo is recovered and validated on chart 22.3.2 with the original pinned
MariaDB 13.1.1 image and encrypted claim
`piwigo-original-copy-longhorn-crypto` on proxmox3. Live checks passed:
44 images, 2 categories, 2 users, login required, ready service endpoints,
and HTTPS 200. The database temporarily has one replica, explicitly authorized
by the user. The original source volume and superseded empty claims are retained.

All 40 unrelated workload entries were restored to saved replica counts at
09:44:54 UTC, including Filebot at zero. Piwigo remains running and validated.
Controller/schedule/pause finalization remains a separate step. GitRepo must remain paused until the validated source
changes are published, to prevent reapplying the broken chart/storage selection.
proxmox2-old remains excluded from recovery storage.

Latest source-publication gate: a live Fleet snapshot shows the same original
Piwigo object UIDs, but the Bundle has reverted to chart **23.0.0** and is paused.
The BundleDeployment remains paused with chart **22.3.2**, its corrected
deployment ID applied, and Ready. The gallery data/login/HTTPS checks pass.
Do not unpause these objects against the stale Bundle. Restoration actions
for other workloads/controls/schedules have completed; safe GitOps source
publication and reconciliation remain outstanding. Ubooquity at zero is an
explicitly approved exception to its original live count of one.

Publish the scoped recovery changes, including the latest helper fixes and
`restoration-overrides.json`, before resuming GitOps. `resume-gitops.py`
requires the local HEAD to match origin's branch head and the committed
Piwigo chart/claim settings to match the tested recovery. It stops and verifies
the Fleet agent before unpausing/refreshing GitRepo, preventing cached chart
23.0.0 from being deployed during the fetch. Applications remain running.
It waits for GitRepo's observed commit, Bundle/BundleDeployment chart 22.3.2,
unpaused states, and the actual downstream `Secret/piwigo` JSON Helm values
to select the recovered claim. This uses the established externalized-values
guard from the Mailpile maintenance tooling and never logs Secret contents.
Only after those checks does it resume the agent and validate Fleet rollout
and the gallery. Failed staging re-pauses GitRepo and leaves the agent stopped;
no copy, database initialization or application scale changes occur.

Successful source verification writes private `gitops-source-verified.json`.
Final read-only verification accepts resumed GitRepo only when its UID and
observed commit match that proof, and still requires Piwigo applied and Ready.
No Git commit or push has been performed by the assistant. Repo source
publication must include the final local changes; prior staging is stale.

Finalization revalidated Piwigo at 09:46:43 UTC and restored Rancher (3 replicas),
Fleet agent/controller, gitjob, helmops, and saved Job/CronJob suspension states.
It then stopped while checking Filebot's applied Helm manifest: kubectl emitted
concatenated JSON documents, not a single JSON document/List. The parser now
supports all three forms, with local checks for each. Retry finalization to
restore remaining Bundle/BundleDeployment pause states and the upgrade
controller. GitRepo stays held until the recovery source is published.

At 12:19:26 UTC the corrected pause restoration completed for unrelated
Bundles/BundleDeployments and the system-upgrade-controller rollout succeeded.
The subsequent final health check found `apps-bookstack/bookstack-mariadb`
not converged. All control/schedule/pause restoration requests had completed,
but overall final health is not yet verified. Inspect current BookStack
deployment/pod/events and database logs before declaring success or changing
its replica count/template. The original baseline had both this Bitnami
MariaDB Deployment and a newer MariaDB StatefulSet running; do not assume
one is disposable or attach their claims to a different server version.

The saved follow-up snapshot shows BookStack frontend, original MariaDB
Deployment and newer MariaDB StatefulSet all available with one replica.
The original database became ready at 12:22:37 UTC; its log confirms persisted
data reused and MariaDB 13.1.1 accepting connections. This supports a transient
post-reconciliation rollout, not evidence of database corruption. No BookStack
template, replica, claim or database modification was made during investigation.

`finish-restoration.py --verify-only --directory BASELINE_DIRECTORY` now
performs only read-only cluster checks plus private report/log writes: saved
application/control replica readiness, CronJob suspension states, original
Fleet pause states, Piwigo applied/Ready, production backup-group enrollment,
retained claims, active-node API/k3s stability and live Piwigo data/login/HTTPS.
It reports other Fleet deployments not Ready instead of claiming the entire
Fleet estate is healthy. It never replays scaling, schedule or pause mutations.

Verification next detected Ubooquity count drift after reconciliation resumed:
the saved live maintenance baseline was one replica, but the repository
explicitly declares zero at `fleet/apps/ubooquity/values.yaml`. Do not silently
rewrite the saved baseline or fight Fleet with repeated scale patches. User
choice is pending on keeping the declared zero versus restoring one and
updating/applying its Fleet configuration. Verification now lists all count
and readiness mismatches in one audit, rather than failing on only the first.

Resolved by explicit user instruction "0 is good": keep Ubooquity at the
declared zero. `restoration-overrides.json` records saved count 1, approved
count 0, original workload UID and reason. The saved baseline remains intact.
Restoration and verification apply this exception only to that namespace,
name, original UID and saved count; other workloads remain checked against
their original counts. The final report lists the approved exception.

At 12:10 UTC a retry restored AdGuard's pause ownership but failed on
Audiobookshelf with only a generic API "request is invalid" response, so
the diagnostic did not establish an explicit resourceVersion conflict.
The saved baseline has an absent `spec.paused` in 139 of 140 Bundle/BD
objects; only `fleet-agent-local` Bundle explicitly saved false. Finalization
now restores that exact field presence: remove the maintenance-added pause
field under UID/value tests when originally absent, or restore the explicit
saved value. It no longer rewrites managedFields for these pause flags.
Other fields and ownership remain untouched. Checks cover originally absent
and explicitly false states. This is a restoration fix, not permission to
force ownership or discard the saved baseline.

The subsequent finalization resumed schedules and began unpausing other Bundles.
It then stopped after unpausing AdGuard's Bundle when a controller update raced
the resourceVersion-guarded metadata handoff. Field handoff now saves stderr
privately and refetches/retries recognized optimistic-lock/test conflicts up
to five attempts. It still checks the UID and preserves all other field
ownership and the spec; permission/schema errors fail closed rather than being
forced. Local checks cover one conflict followed by success and a forbidden
operation rejected without retry. Final restoration report remains pending.

## Lessons for future upgrades and maintenance

- Treat major chart changes as storage migrations when dependency value keys,
  database service names, images or data paths change. Compare rendered PVC
  references and actual mounted claims before accepting an upgrade.
- Confirm the database server version, not just image/chart tag names. Here
  a pinned image tagged `latest` was MariaDB 13.1.1, while the replacement was
  11.8.6. Never attach newer physical database files to an older server.
- Ready pods/deployments are insufficient: test actual application queries,
  expected counts, anonymous login behavior, endpoints and ingress. The empty
  replacement database initially passed readiness while the gallery failed.
- Preserve source claims/backups. Inspect attachment tickets and snapshot UIDs
  before removing a confirmed orphan. Do not uncordon a storage-maintenance
  node merely to make a blocked attachment start.
- For protected ext4 inspection use read-only mounts with journal replay
  disabled (`ro,noload`, shown live as `ro,norecovery`). Start the original
  database image on a checksum-verified copy, never on the original source.
- Freeze reconciliation and schedules before scaling applications across the
  cluster to zero. Restore saved UIDs/counts, databases before frontends,
  preserve original zeros and check I/O between workloads. A scale patch is
  neither shutdown verification nor rollout validation.
- Maintenance patches take server-side field ownership. After restoring a
  field, relinquish only that maintenance-owned field so Fleet can apply it;
  do not clear unrelated managed fields or broadly force ownership.
- Use Fleet-native generation from the established homelab working directory
  with relative paths. Preserve GitRepo labels/targeting. Large embedded-chart
  Bundles exceed client-side apply annotation limits; use guarded updates.
- Fleet content/values representations vary: this CLI emitted `base64+gz`
  chart resources and structured Helm values, while older applied bundles
  externalized values. Validate selected claims in structured values or an
  unchanged applied release manifest, not an assumed resource layout.
- Kubectl conversion of multi-document YAML can emit concatenated JSON
  objects. Handle JSON streams as well as Lists and single objects.
- Honor each workload's configured startup-probe budget. YouTubeDL Material's
  permission scan needed longer than the generic three-minute rollout wait;
  wait for its existing pod rather than restarting the scan or weakening probes.
- Record every requested and live-worker-confirmed transfer cap, including
  automatic safety pauses/resumes and final checksum outcome. Restoration
  I/O holds after worker exit are separate from transfer-cap changes.
- Recovered production volumes need recurring-backup group enrollment; backup
  labels on the historical source do not protect new writes. Enrollment is
  separate from a completed, restore-verified new backup.
- Keep GitRepo paused until the validated source is published. Otherwise an
  old remote chart/claim setting can undo a successful local recovery.

Detailed evidence, times, cap history and limitations follow. The original
I/O-spike cause remains unknown; maintenance and throttling observations do
not establish a hardware/storage repair. The one-replica database exception
is temporary and redundancy is not restored until two distinct-node RW
replicas are confirmed after another eligible storage node becomes available.

Resolved hold: restoration initially reached 34 of 40 unrelated workload entries.
YouTubeDL Material was requested at 09:34:27 UTC but its rollout timed out
at 09:37:27. The helper exited and did not start the later Zabbix/CloudCasa
workloads. Application diagnostics are required before retrying. Local I/O
PSI rose as high as some=39.83%, full=34.59% avg10 during this interval;
memory PSI was zero, about 13GiB available, and no swap configured. A brief
diskstats sample observed nvme0n1 around 90% busy with about 23MiB/s writes;
these are observations, not a proven cause. There was no active migration
copy worker and no transfer cap changed during this restoration hold.

## Confirmed failure

Restoration timeout correction: saved YouTubeDL Material diagnostics show
MongoDB ready, both app init containers completed, and the application running
without restarts but still at its entrypoint permission-setup message, with
no HTTP listener yet. The upstream
[entrypoint](https://github.com/Tzahi12345/YoutubeDL-Material/blob/master/backend/entrypoint.sh)
uses a recursive ownership scan. This is a possible I/O contributor, not
a proven explanation of the full node pressure. The workload already allows
180 startup failures at ten-second intervals plus a 30-second initial delay;
the generic restoration helper had incorrectly timed out after 180 seconds.
It now derives waits from the actual startup-probe budget (1890 seconds with
grace here). If the saved count is already requested, it waits for that existing
rollout without patching or restarting it. Low I/O is still required before
starting additional workloads. Budget checks passed locally. No UID change,
ownership-scan bypass, probe weakening or app configuration change was made.

Helm revision 35 (chart 22.3.2) used the encrypted claim
`piwigo-mariadb-data-longhorn-crypto`, a Bitnami MariaDB image pinned to
`sha256:687181819d6e3f04124a21aec1aeef1d8a5d6a924c0d8e8f0cf66b8f0ce15a7c`,
and `/bitnami/mariadb`.

Revision 36 (chart 23.0.0) changed to Containerforge MariaDB 11.8.6,
service `piwigo-mariadb-main`, and a new local-path claim
`piwigo-mariadb-data`. The legacy `mariadb.persistence.data.existingClaim`
setting no longer selected the database storage. The effective replacement
setting is `dependencies.mariadb.persistence.data.existingClaim`.

The saved Piwigo database hostname was changed from `piwigo-mariadb` to
`piwigo-mariadb-main` during diagnosis. The app then reached the replacement
database, but authenticated `SHOW TABLES` returned no tables. Kubernetes
readiness passed despite the gallery still failing; readiness is insufficient
for application validation.

## Source and backup

Original volume: `pvc-6cbf25ee-1394-4a06-94ff-4fc9fcca03ae` (2Gi, encrypted).
At diagnosis, one running replica remained on proxmox3 with no recorded failure,
while its stopped engine targeted proxmox2-old. That node's instance manager
could not be recreated while cordoned. An unsatisfied snapshot attachment
ticket also targeted that node and its UID matched no current Snapshot.
That confirmed orphan was subsequently removed under guards. The source was
mounted read-only without journal replay, copied and checksum-verified, then
detached before the recovered copy became production. The original volume
configuration/replica count and data remain preserved.

Newest listed completed backup: `backup-5ef9df32921046e2`, 24 November 2026
00:20:42 UTC. Backup contents have not been verified. Preserve both existing
claims and the backup. Do not run the gallery installer or open the original
data directory with the new database image.

## Current authorization and health gate

User instruction: do not use proxmox2-old for now. Preserve its cordon,
storage-repair taint, and disabled Longhorn node/disk scheduling.

Only proxmox3 is eligible for new replicas. The user subsequently explicitly
authorized temporarily changing the two-replica requirement for this recovery
("change it temporairly"). This is a Piwigo recovery exception, not a global
Longhorn default change or permission to use proxmox2-old. Recovery volumes
may temporarily use one healthy replica. Preserve the original source
volume's configuration and data. This exception did not waive cluster-wide
application shutdown: shutdown was verified before copying. The copy and
Piwigo cutover are now complete; unrelated restoration is in progress.

`recovery-storage.yaml` defines a separate encrypted, Retain StorageClass
with one replica and a new empty 2Gi target claim. It does not modify the
shared two-replica StorageClass or either original claim. Provisioning an
empty target does not copy or initialize a database. Check live Longhorn
node scheduling before applying: proxmox2-old must remain disabled at both
node and disk level. Verify the allocated replica is on proxmox3, encryption
is enabled, and the live volume has the requested replica count; manifest
creation alone does not confirm any of these properties.

After another eligible storage node becomes available, explicitly increase
the recovered volume to two replicas and wait for two running RW replicas
on distinct nodes and healthy robustness. Update production claim ownership
through Fleet, remove recovery-only resources when no longer referenced,
and record completion of the temporary exception. Do not claim redundancy
has been restored until the live volume confirms it.

Observed on proxmox3: rpool ONLINE, 472G total, 253G free, 46% allocated.
Local I/O PSI avg10 and avg60 were zero at preparation time. These are
point-in-time observations, not evidence of repaired storage. Subsequent
guarded preflights checked the active node's API readiness/restart stability
and target engine/replica health. The excluded peer API was not directly
probed during those preflights; do not describe that as both APIs verified.

## Preparation artifacts

Private host inventory and manifests are in
`/tmp/piwigo-recovery-uEl689wx` (temporary, not a durable backup). Never commit
these files: manifests and effective values may contain credentials.

The candidate `recovery-storage-override.json` renders the new database onto
`piwigo-mariadb-recovered-longhorn-crypto`. The host Helm render completed
with schema validation; inspection found the intended claim reference and
no newly generated PVC. This claim was created empty but remains unused.
The override was not applied: discovery that the original database was newer
than the replacement led to the previous-chart recovery path described above.

`maintenance.py plan --directory NEW_PRIVATE_DIRECTORY` obtains a fresh
inventory and saves UIDs, replica counts, GitRepo pause states, active jobs,
and CronJob suspension states. The plan mode is read-only and refuses to
overwrite a baseline. Review workloads
outside `apps-*` separately; namespace naming is not a complete application
classification. No standalone active pods were returned during diagnosis.

The fresh baseline is at `/tmp/piwigo-recovery-uEl689wx/maintenance-baseline`.
`maintenance.py stop --directory BASELINE_DIRECTORY` verifies saved UIDs,
checks local I/O pressure and excluded-node scheduling protection, freezes
Fleet and Rancher controllers, pauses GitRepo reconciliation, suspends all
six schedules and active jobs (including newly started jobs), stops application
frontends, waits for termination, then stops databases and verifies shutdown.
It logs requested and API-confirmed mutations privately in `actions.log`.
Essential infrastructure and AdGuard remain running. Failure does not trigger
an automatic restart: inspect the action log and live state before proceeding.

`maintenance.py restore-apps --directory BASELINE_DIRECTORY` provides staged
restoration of unrelated applications, databases first, using saved counts
with UID checks, rollout checks and local I/O checks. It intentionally holds
Piwigo, GitOps controllers and schedules until a separately verified cutover
or rollback is staged. It is not a full restoration command; finish controller,
GitRepo, Job and CronJob restoration from saved states after that gate. Do not
run it during copying. Filebot remains at zero.

Known maintenance considerations: preserve Filebot at zero; keep AdGuard
for DNS; freeze Fleet reconciliation before application scaling; suspend
the active Calibre maintenance job and scheduled jobs; coordinate Longhorn
backup jobs. Keep networking, admission and Longhorn/CSI running.

Follow [the cluster maintenance procedure](../../README-5-pvc-migration-maintenance.md)
before any copy, restore or verification transfer. Any future adoption of
the new database image needs a validated logical migration; never open newer
physical database files with an older server. Prepare throttling, monitoring,
live-worker confirmation and restoration before executing. Validate gallery login, albums/photos,
database table/row counts, actual mounts, replicas, Fleet and ingress;
do not use deployment readiness alone. Restore saved applications gradually
and keep original zero counts at zero.

## Action and transfer-cap record

Gradual unrelated-application restoration started at 09:07:16 UTC. Database
rollouts are confirmed before frontend restoration. Each successful workload
restores its saved count and relinquishes only maintenance ownership of that
replica field. All restoration progress remains in private `actions.log`;
do not infer the whole cluster is restored from individual confirmations.

At 09:13:41 UTC, 15 of 40 unrelated workload entries had confirmed restoration
and replica-field handoff, most recently Docker Mailserver. The log then stopped
advancing; terminal output is needed to identify the exit reason. Local I/O
pressure subsequently returned to zero. Do not claim the remaining applications
are restored or assume an unrecorded failure was an I/O event.
The helper now logs uncaught failures, skips already-converged workloads, and
waits for low I/O across two samples before starting another workload rather
than exiting immediately on a transient high-pressure sample. A continuous
five-minute high-pressure hold still fails closed with a recorded reason.

The user confirmed the first restoration stopped on the I/O gate after
Docker Mailserver: some=1.82%, full=1.25% avg10. No rollout failure was reported.
The resumed helper skipped the already-converged entries, restored DuckDNS
and FlareSolverr, and preserved Filebot at zero. At 09:25:56 UTC it logged a
temporary restoration wait at some=1.34%, full=1.03%, then continued at 09:26:09
after pressure settled. This is a workload-start hold, not a transfer-cap
change: the copy worker had already exited successfully and no transfer is
active during application restoration.

Prepared final step, after `restore-apps` completes:
`finish-restoration.py --directory BASELINE_DIRECTORY`. It first requires all
unrelated application replica counts and readiness to match the saved baseline
and revalidates Piwigo. It restores the recovered production claim/volume's
NAS recurring-backup group selection (the original source's backup labels
are insufficient for the new production volume). This labels the new volume
for future backups; it does not claim a new backup has run or been verified.
It restores remaining control workloads and original Job/CronJob suspension
states, verifies unrelated Fleet deployment IDs did not change during the
hold, verifies Filebot's desired Fleet count is zero, then restores saved
Bundle/BundleDeployment pause states. The upgrade controller resumes last.
All field restorations retain UID guards, local I/O checks and field-scoped
ownership handoff. GitRepo remains explicitly paused pending publication of
the recovery source. Final report records that exception instead of claiming
the complete original reconciliation state has been restored.

### Production recovery validated

2026-11-27 09:03:31 UTC: resumed Fleet rollout applied the corrected deployment
ID and passed live application validation. Piwigo authenticated using its stored
configuration and returned counts of 44 images, 2 categories and 2 users.
`guest_access=false` was confirmed; anonymous local HTTP returned the login form
with status 200; HTTPS ingress with the real hostname/SNI returned 200.
Ready service endpoints were present. The actual database pod mounted
`piwigo-original-copy-longhorn-crypto` on proxmox3; its Longhorn volume was
encrypted, healthy, and configured with the explicitly authorized temporary
one replica. The private validation report is
`maintenance-baseline/piwigo-validated.json`. Source and superseded empty
claims are preserved. Local I/O PSI avg10 was zero after validation.

Unrelated applications, remaining controls and schedules are still held.
`maintenance.py restore-apps` now requires the validation marker, restores
saved counts gradually with rollout/I/O checks, then relinquishes only the
maintenance replica-field ownership to avoid a later Fleet apply conflict.
It excludes production Piwigo and reconciliation controls; saved Filebot zero
is preserved. Complete control/schedule/pause restoration separately.

The GitRepo must remain paused until the recovery source changes are present
in its configured revision: resuming it against the previous remote source
could reinstall chart 23.0.0 and select the empty database again. No commit
or push has been authorized or performed. Other bundle pause states can be
restored independently after saved application replicas are restored.

Fleet staging succeeded: corrected Piwigo BundleDeployment selects chart
22.3.2 and deployment ID
`s-40237667f3e33ac7ec585f9ed2fee2efb2087c10c903a193d57aa2a41130f:347500a7df20250a68ffc2503f9e31931380212e60a933e23470249aa307f466`.
The Fleet controller is restored to its saved count; the agent is still stopped.
Other Bundles/BundleDeployments are paused with their original states preserved.

Pending activation: `activate-piwigo.py --directory BASELINE_DIRECTORY`
checks staged identity, corrected live Bundle values, pause controls,
application/job shutdown, source detachment, API/k3s stability and I/O.
It adds `helm.sh/resource-policy=keep` to the superseded empty local-path
claim and Retain to its PV, saving original objects privately. Helm checks
live resource annotations when deleting resources removed by an upgrade
([Helm client implementation](https://github.com/helm/helm/blob/v3.19.0/pkg/kube/client.go#L400-L413)).
This protects that empty claim during the chart rollback; do not delete it
as part of this recovery.

Activation gracefully shuts down the socket-only scratch database, waits
for a successful process exit, deletes its pod with a UID precondition, then
resumes the saved Fleet agent count with only Piwigo unpaused. It waits for
the corrected deployment ID to be applied and Fleet Ready before checking
live app credentials against recovered row counts, guest access disabled,
anonymous HTTP login form, ready EndpointSlices, actual recovered database
mount/node and encrypted healthy temporary-one-replica volume. HTTPS ingress
is tested with the real hostname/SNI and active-node IP, independent of local
DNS. No gallery page contents are written to host storage; the login-page
body is checked in the app's RAM-backed `/tmp`.

On successful checks a private `piwigo-validated.json` marker is saved.
Other apps/schedules remain stopped until subsequent gradual restoration.
If activation fails after agent resume, preserve target ownership and do not
restart the old source. `--validate-only` repeats application checks without
repeating the handoff. After production writes start, the immutable original
is historical rollback data; do not blindly make it active again.

First activation attempt stopped at the administrative shutdown command,
before agent resume. Empty-claim keep/PV retention protections were applied.
The exact shutdown error was not captured by that first implementation;
the helper now uses the already-tested `mariadb` client/socket to issue SQL
`SHUTDOWN`, saves command stdout/stderr privately and accepts an already
cleanly exited recovery pod. It still requires successful pod completion
before deleting the helper or starting production. No forceful shutdown is used.

Activation then confirmed the scratch database had exited cleanly, removed
its pod, and resumed the agent at 08:50:24 UTC. Fleet did not apply the new
deployment because server-side apply conflicted with maintenance's
`kubectl-patch` ownership of `apps-piwigo/piwigo.spec.replicas`. This is an
apply ownership conflict, not a storage or recovered-data error.

`release-replica-ownership.py --directory BASELINE_DIRECTORY` verifies the
corrected Bundle and other pause controls, saves the live deployment with
managed fields, checks its original UID, and relinquishes only the
`kubectl-patch` replica-field ownership under a resourceVersion guard.
It preserves other managers/fields and the deployment spec, then verifies
the ownership was relinquished. It does not force Fleet ownership of other
fields or restart any workload. Field-scope checks passed locally. Keep the
activation monitor running for Fleet retry; if its ten-minute wait expires,
use read-only validation after the corrected deployment is applied rather
than repeating the scratch-database handoff.

At 09:01:56 UTC the host helper confirmed maintenance replica ownership was
released without changing the Deployment spec. The original activation wait
had expired before this handoff. `activate-piwigo.py --resume --directory
BASELINE_DIRECTORY` rechecks the corrected Bundle, staged deployment identity
and pause controls, then only resumes waiting/validation; it never repeats
database shutdown or pod deletion. No current rollout success is inferred
from the previously Ready status for the old deployment ID.

Host cutover render completed successfully and was inspected: only the recovered
scratch database claim and existing encrypted config/gallery claims are selected;
the database image exactly matches the original tested digest, the recovery
hostname init script is present, and no replacement MariaDB StatefulSet renders.

First staging attempt failed during Fleet generation before any cluster mutation:
Fleet rejected the absolute bundle directory with "no resource found". Fixed
the helper to run from the homelab root with relative `rancher/fleet/apps/piwigo`,
matching the repository's established apply procedure. Local generation with
the corrected command succeeded. Generation errors are now saved privately
instead of losing captured stderr. Live staging remains pending on retry.

Second attempt stopped before mutation because the validation helper assumed
`base64+gzip` resource encoding; this Fleet version emits `base64+gz`. More
fundamentally, Fleet places selected values in `spec.helm.values`, not in the
compressed chart resources. Validation now checks the exact structured chart
version and database claim in `spec.helm.values` and does not decode chart
resources. Checks accept compressed chart content and reject a wrong claim.

Subsequent inspection found the original pause baseline had already been
saved and other Bundle/BundleDeployment pause mutations were confirmed in
the action log at 08:29 UTC. The baseline contains 70 Bundles and 70
BundleDeployments, all originally unpaused. Preserve it; do not overwrite
or delete it on retries. Staging now verifies current object UID sets against
that saved baseline, resumes with it, and skips already-paused objects.
The generated Bundle is about 813KB; client-side apply would exceed the
last-applied annotation limit. Applying the Fleet-generated Bundle now uses
a live-resourceVersion-guarded replacement preserving metadata/ownership,
without a client-side apply annotation. No direct Helm installation is used.

Pending staging: `stage-cutover.py --directory BASELINE_DIRECTORY` verifies
shutdown with only the isolated recovery database excluded, source detachment,
render selection, and installed Fleet pause-field support. It generates the
changed bundle with Fleet, preserves current GitRepo ownership labels and cluster
targeting, saves all Bundle/BundleDeployment pause baselines privately, and pauses
every other bundle and deployment. It applies only the changed Piwigo bundle,
restores the saved Fleet controller count, and waits for chart 22.3.2 to appear
in Piwigo's staged BundleDeployment. The Fleet agent remains stopped; the
socket-only recovery database remains running on scratch. On staging failure,
do not enable the agent; inspect saved objects and action log. Resume other
Bundle/BundleDeployment pause states from this new baseline during restoration.

Scratch database check succeeded: original pinned image reports
`13.1.1-MariaDB`; Piwigo has 36 tables, 99 configuration rows, 44 images,
2 categories and 2 users. The original source detached before the recovery
database started; only encrypted scratch is mounted by that socket-only
recovery instance. The replacement image is 11.8.6, so do not attach the
13.1.1 physical datadir to it.

Prepared production recovery: revert the Piwigo bundle to known chart 22.3.2,
which pins the successfully tested original database image, and select the
verified `piwigo-original-copy-longhorn-crypto` claim. The new empty
`piwigo-mariadb-recovered-longhorn-crypto` target remains preserved but unused.
A recovery init script changes only the obsolete `piwigo-mariadb-main` hostname
back to the previous chart's `piwigo-mariadb` service name. Original encrypted
source PVC and backup remain rollback data. Schema-validating render and
Fleet-native application/BundleDeployment validation are required before
resuming the agent. Do not directly install Helm or resume all bundles at once.

Before enabling the agent, save then pause other Bundles and their
BundleDeployments, keeping the GitRepo paused. Resume only the Fleet controller
needed to stage the corrected Piwigo BundleDeployment. Stop the socket-only
scratch database gracefully and delete its pod before the production database
mounts the same claim. Validate Piwigo data, login restrictions, mounts and
Fleet rollout before gradually restoring other saved replicas. Later unpause
other bundles individually, checking that Filebot's saved zero is preserved.

Pending database inspection: `check-database.py --directory BASELINE_DIRECTORY`
requires the completed physical-copy event and matching encrypted-scratch
manifest. It checks shutdown excluding only the known sleep-only copier UID,
API/k3s stability and I/O, removes that helper with a UID precondition and
waits for the original source to detach. It then creates a recovery pod pinned
to proxmox3 using the exact original Bitnami image digest, mounting only the
scratch claim. The original and production target are not mounted.

The recovery database daemon bypasses image initialization scripts and starts
directly as UID/GID 568 with an explicit datadir, bounded memory, in-memory
temporary files, no binlog, `--skip-networking` and `--skip-grant-tables`.
Grant bypass is limited to the isolated pod's Unix socket; no Service or
TCP listener is configured. This is a temporary recovery instance and must
not become the production deployment. The helper queries version and counts
for tables/config/images/categories/users, records results privately, and
does not display gallery content or credentials. Database startup can perform
recovery writes on scratch; the immutable original remains the rollback source.
Successful counts are not equivalent to complete logical-export validation.

Source inspection completed on the host: `/source` is the original encrypted
device mounted `ext4 ro,relatime,norecovery`; `/scratch` is a separate encrypted
device mounted read-write. The original contains `data/piwigo`. No tables have
yet been opened by MariaDB. PHP and tar are present in the helper; Python and
pv are not.

Pending `copy-original.py --directory BASELINE_DIRECTORY`: lint the PHP worker
inside the existing helper before mutation; verify shutdown while excluding
only the exact inspected helper UID; check encrypted source/scratch attachments
on proxmox3, running engine RW replica maps, healthy one-replica scratch,
API readiness, k3s restart stability and I/O pressure. Replace the sleep-only
inspection pod with a sleep-only copier pod pinned to proxmox3, adding only
file-copy DAC/ownership/mode capabilities. Source mount stays read-only with
journal replay disabled. The worker checks that live mount again before writes.

The pending copier reads `/source/data` into the empty `/scratch/data`, preserves
file ownership/modes/timestamps, syncs each copied file and verifies its SHA256
against the source bytes. It refuses symlinks, special files, existing destination
data, or failed reads/checksums; partial scratch is preserved on failure. It does
not start MariaDB or touch the production recovery target. The encrypted scratch
completion manifest records file sizes and hashes. A physical checksum match
is not a database consistency or schema-validation check.

Requested initial cap: 1,048,576 bytes/s, covering source reads and scratch
checksum reads; writes add I/O beyond that read-rate bound. Every chunk is paced.
The worker samples I/O PSI before reads, confirms a zero-cap safety pause above
some=5% or full=1% avg10, and resumes at the same conservative cap only below
some=1% and full=0.2%. A five-minute continuous safety pause fails closed. Every
requested and worker-confirmed cap transition includes UTC time, previous/new
limits, reason and observed PSI, recorded in private `cap-changes.jsonl`,
`copy-worker.log` and the action log. Worker confirmation and successful final
checksum outcome remain pending until host execution. No automatic cap change
should be described as a hardware/storage repair.

Pending inspection: `inspect-source.py --directory BASELINE_DIRECTORY`
repeats the source-access preflight, saves the original source PV object,
and sets source mount options to `ro,noload` while detached. This prevents
ext4 journal replay on the original filesystem. It creates a separate empty
encrypted 2Gi scratch claim with the authorized temporary one-replica class,
and a sleep-only inspection pod pinned to proxmox3. Source is read-only at
both PVC and container mount levels. No image entrypoint/database initialization
runs. The helper records live source/scratch mount flags and directory metadata
privately, and rejects an unconfirmed read-only/no-journal source mount.
The pending scratch is `piwigo-original-copy-longhorn-crypto`; the production
recovery target remains separate and empty. After the source helper is removed
and source volume detached, restore the saved source PV mount options if the
original claim will be reused. Never restore writable options while an
inspection/copy pod still mounts the source.

`source-access.py --directory BASELINE_DIRECTORY` is the next guarded host
step. It rechecks shutdown, active jobs, source claim consumers, CSI attachments,
excluded-node scheduling, local I/O PSI, local API readiness, and k3s restart
stability over ten seconds. It requires the source engine to be stopped and
the sole attachment ticket to be the known snapshot-controller request whose
owner UID is absent from current Snapshot resources. It saves the source
objects privately and removes only that orphaned ticket with resource-version
and ticket-content tests, then waits for normal controller-driven detachment.
It does not force detach, modify replica counts, mount data, start MariaDB,
or copy data. Any failed guard stops the action. Syntax and mocked checks
passed, including rejection of a ticket with a live snapshot owner. Live
execution remains pending until the host command runs.

Preparation only: inventory, manifest comparison, candidate render, and
read-only storage inspection. No application shutdown, storage mutation,
copy, restore, or cutover performed by this recovery workflow.

Subsequent host action: temporary recovery StorageClass and empty target PVC
created by the user. PVC Bound to `pvc-bf36b606-0aab-4fad-bd8e-6031c0606a08`;
saved live inspection confirms `encrypted=true`, `numberOfReplicas=1`, and
one replica allocated on proxmox3. Volume detached, replica stopped pending
first mount; running engine/replica health is not yet verified. No database
has been copied. Temporary one-replica exception is confirmed at volume-spec
level; this is separate from a transfer-bandwidth cap change.

No transfer worker exists and no transfer caps have been requested or
changed. Record UTC time, old/new limit, reason, observed I/O PSI, confirmed
live-worker limit, and outcome here and in the execution action log for
every subsequent cap change, including automatic reductions.

### Verified maintenance shutdown

### Protected physical copy completed

2026-11-27 05:37:23 UTC: worker completed the original-to-encrypted-scratch
copy and verified SHA256 for all 314 copied files. Total read bytes including
scratch verification: 300,953,042 (150,476,521 bytes of file contents copied).
Original source stayed read-only with journal replay disabled. No MariaDB
process has been started, database/schema integrity remains unverified, and
the production recovery target is still empty. Applications and reconciliation
remain stopped. The complete per-file manifest is on encrypted scratch at
`/scratch/original-copy-complete.json`; private host worker/action logs record
the successful outcome.

Confirmed cap history (UTC, bytes/s; all changes emitted and acknowledged
by the live worker, mirrored to `cap-changes.jsonl` and `actions.log`):

| Time | Previous | New | Reason / avg10 I/O PSI | Observed outcome |
| --- | ---: | ---: | --- | --- |
| 05:29:30 | no worker | 1,048,576 | Initial conservative read/verification cap; some=0%, full=0% | Copy advanced with zero observed pressure initially |
| 05:35:13 | 1,048,576 | 0 | Automatic safety pause; some=1.63%, full=1.63% | Worker paused; pressure declined |
| 05:35:45 | 0 | 1,048,576 | Resume after pressure settled; some=0.18%, full=0.18% | Copy resumed after 32 seconds; all checksums passed at 05:37:23 with some=0%, full=0% |

At successful worker exit, no transfer worker/cap remained active. There
were no other cap changes. These observations do not identify the cause
of pressure or demonstrate a storage repair.

Next confirmed action, 2026-11-27 05:22:38 UTC: removed only the verified
orphaned snapshot-controller attachment ticket. Preflight confirmed no
source pod/CSI consumers, stopped source engine, API readiness, local k3s
active with NRestarts stable at 2 across the sample, and zero avg10 I/O PSI.
At 05:22:43 UTC the saved live Volume object confirmed `state=detached`,
empty requested/current node IDs, and unchanged source `numberOfReplicas=2`.
No replica/PVC/backup deletion, database start, or data copying occurred.
Private before/after source objects and action logs remain in the baseline.

2026-11-27 05:18:55 UTC: host helper began shutdown. Saved replica counts,
UIDs, pause states and schedule states remain in the private baseline.
Fleet/Rancher reconcilers were stopped, the GitRepo paused, six CronJobs and
the active Calibre job suspended, then frontends stopped before databases.

2026-11-27 05:20:12 UTC: helper confirmed desired/current workload counts
at zero, application pods exited, and no active Jobs remained. AdGuard and
essential Kubernetes/networking/admission/Longhorn services were preserved.
The user independently reported the successful helper completion. Applications,
databases, reconciliation and schedules remain in maintenance; restoration
has not started. No copying or attachment changes have occurred.

Local I/O PSI avg10 rose during shutdown (observed some=11.03%, full=8.40%)
then fell to some=1.42%, full=0.65% at verified completion and some=0.64%,
full=0.29% at the next observation. These observations do not prove the cause
of storage pressure or any repair. Recheck pressure, k3s stability, source
writers and storage health before the next storage operation.
