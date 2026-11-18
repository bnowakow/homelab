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
