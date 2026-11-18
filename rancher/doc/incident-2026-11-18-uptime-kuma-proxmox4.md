# Uptime Kuma / Proxmox instability investigation — 2026-11-18

## Scope and status

The reported symptom is that Uptime Kuma has a web outage while its pod is on
`proxmox2-old`, and that the pod cannot mount its Longhorn volume. The reported
mount failures have allegedly repeated for more than five hours, with other
applications and Longhorn components on that node unhealthy.

The first collector run completed on `proxmox2-old` at
`2026-11-18T08:52:42Z`. It confirms the quoted failure:

- Kuma has been Pending on `proxmox2-old` for more than five hours; its Service
  has no ready Endpoints.
- Kubelet reports `format of disk ... failed` because the Longhorn device is
  already mounted: `... is mounted; will not make a filesystem here!` This is
  a mount/CSI state failure, not evidence that the filesystem should be
  formatted.
- Longhorn reports `proxmox2-old READY=False`; its instance-manager is
  `ContainerStatusUnknown`, its Longhorn manager is `Error`, and its
  share-manager is `Error`.
- Many Longhorn volumes are `degraded`; several are detached/unknown.
- The kernel log contains approximately 1,076 reads failing on `sdx` sector
  311304, additional `sdx` read failures, and approximately 1,085 NFS timeout
  messages for `10.43.12.19`.

The Kubernetes node itself still reports Ready with no Kubernetes
DiskPressure, and the API `/readyz` check passed. This narrows the immediate
incident to node storage/Longhorn and CSI state rather than a whole-node
Kubernetes outage. The disk/NFS errors explain why other Longhorn components
and applications are unstable, but the exact identity of `sdx` and the cause
of its read failures still require host-level device/ZFS/Proxmox inspection.

## Likely failure chain to verify

1. The Kuma pod is scheduled on `proxmox2-old`.
2. Its Longhorn RWO volume is not attached/published, or the CSI node/plugin
   on that node cannot reach the Longhorn backend.
3. The pod remains Pending/ContainerCreating or repeatedly restarts, so the
   Service has no ready endpoints and Kuma's web page is unavailable.
4. The wider node symptoms may be caused by the same node/Longhorn health
   problem, I/O pressure, or a networking regression; they must not be
   collapsed into one cause without the snapshot evidence.

The historical 2026-11-02 bridge/netfilter incident is a separate important
check: `net.bridge.bridge-nf-call-iptables` and `ip6tables` must remain `1`.
That failure breaks same-node Service traffic and can look like random
application instability. The historical 2026-10-25 incident also records
Longhorn backup activity, I/O pressure, etcd latency, and k3s instability as
separate concerns. Proxmox Backup Server load may worsen pressure, but stopping
its VM is a mitigation/test, not proof of root cause or storage repair.

## Evidence collection

```sh
sudo ./monitor-uptime-kuma-longhorn.sh /var/tmp/kuma-incident-$(date -u +%Y%m%dT%H%M%SZ)
```

Prioritise these files:

- `kuma-describe`, `kuma-events`, `kuma-pvc`: exact mount/attach errors;
- `kuma-service`: whether ready endpoints exist;
- `longhorn-nodes`, `longhorn-pods`, `longhorn-volumes`, `longhorn-replicas`:
  node and volume health/attachment;
- `recent-k3s-journal`, `kernel-storage-journal`, `pressure`, `vmstat`: API,
  storage and I/O evidence;
- `bridge-netfilter`: same-node Service-path regression check.

If the volume is healthy but Kuma has no endpoint, inspect readiness probes and
the application log. If the volume is faulted/detached, preserve the evidence
and do not force-attach or delete replicas before checking backups and the
Longhorn maintenance procedure.

## Change record

No cluster workload, VM, PVC, Longhorn setting, or transfer cap was changed by
this investigation. The requested Proxmox Backup Server VM shutdown is outside
this sandbox and should be recorded with its time, VM ID, host, observed I/O
pressure before/after, and whether Kuma/Longhorn recovered.

## Future monitoring

Schedule the collector from a privileged administrative host or run it from
Uptime Kuma/Prometheus when available. Alert on: Kuma pod not Ready; Kuma
Service with zero ready endpoints; non-healthy Kuma volume/replica; a
non-Ready/unschedulable Longhorn node; CSI mount errors; and sustained I/O PSI.
Keep snapshots timestamped and retain the raw event/journal output for
correlation with Proxmox task logs.

For the confirmed `emergency_ro` condition, run the read-only host assessment:

```sh
sudo ./assess-longhorn-emergency-ro.sh
```

It captures ext4 remount errors, NVMe/SMART health, ZFS status, Longhorn
mounts and resources, container runtime logs, k3s readiness, and I/O pressure.
It does not run `fsck`, unmount volumes, reboot, delete pods, scale workloads,
or modify Longhorn replicas.

The 2026-11-18 09:01 CET assessment found **37 Longhorn mount paths with
`emergency_ro`**. The NVMe SMART/NVMe report showed `Critical Warning: 0x04`,
`Percentage Used: 100%`, temperature 51 C with sensor 1 at 67 C, and 8,058
minutes above the warning-temperature threshold. It reported no media/data
integrity errors and ZFS remained `ONLINE`, but those results do not clear the
NVMe reliability/endurance warning. Repeated virtual-disk read errors and NFS
timeouts continued. Treat `proxmox2-old` storage as unsafe for application
writes; prioritize backups, workload shutdown, and NVMe replacement or a
planned storage migration. Do not attempt filesystem repair while these
Longhorn volumes are mounted.

## Guarded recovery helper

`recover-uptime-kuma-longhorn.sh` collects the same evidence plus host block,
ZFS, mount, and kernel-error checks. It is read-only by default:

```sh
sudo ./recover-uptime-kuma-longhorn.sh
```

Only after storage/NFS errors are absent, an operator may explicitly recreate
the affected Longhorn manager and CSI pods on `proxmox2-old`:

```sh
sudo CONFIRM_LONGHORN_RECOVERY=1 ./recover-uptime-kuma-longhorn.sh --restart-csi
```

This does not format, force-attach, delete replicas, change application
replicas, or reboot the node. If the mount error returns, stop retrying and
investigate the backing device and NFS path.

For pods stuck in `Terminating` or `ContainerStatusUnknown`, run the
read-only diagnostic collector before considering force deletion:

```sh
sudo ./diagnose-stuck-longhorn.sh
```

It records pod YAML/finalizers, descriptions, events, k3s/containerd/kubelet
journals, mounts, processes, pressure, and recent kernel storage errors. It
does not delete pods or modify finalizers.
