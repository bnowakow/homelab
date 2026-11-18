# Proxmox2-old storage/runtime incident — 2026-11-18

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

The third node provides quorum protection; it does not repair the failed
NVMe, `emergency_ro` filesystems, stale container tasks, or degraded Longhorn
volumes on `proxmox2-old`. Do not uncordon or power down `proxmox2-old` merely
because `proxmox5` joined. First recover/validate Longhorn attachments and
replicas, then remove the old etcd member only as a separate planned step.

Provisioning status: **not yet performed in this recovery session**; the
operator plans to run the existing Ansible provisioning from the NAS.

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
