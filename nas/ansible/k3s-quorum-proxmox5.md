# Join proxmox5 as a quorum-only k3s server

The recovery handoff in `../../rancher/doc/proxmox2-old-recovery-2026-11-18.md`
calls for a third embedded-etcd voter. The NAS `provision.sh` configures hosts;
it does not join k3s. The Rancher `ansible/site.yml` bootstrap restarts existing
k3s servers and can reinstall Rancher/Fleet, so do not use it for this join.

`k3s-quorum-proxmox5_playbook.yml` installs proxmox5 as an **agentless,
etcd-only** k3s server. It disables kubelet, containerd, CNI, the API server,
controller-manager, and scheduler. It does not register a Kubernetes Node, so
it cannot host pods or a Longhorn node, disk, or replica. Agentless mode is
experimental; the missing Node resource also means node-based etcd controllers
and upgrade controllers cannot manage this member. Maintain and monitor it
directly. The playbook uses `egress-selector-mode: agent` to match the existing
servers; this setting is cluster critical and must match on all servers.

## Before joining

1. Confirm proxmox5 has stable low-latency storage and LAN connectivity to
   proxmox3 and proxmox2-old, especially TCP 2379-2380 for etcd, TCP 6443 for
   the API, and TCP 9345 for k3s registration. Check clock synchronization,
   free space, hostname `proxmox5`, and absence of old k3s state.
2. On proxmox3, check `sudo k3s kubectl get --raw=/readyz`, the two current
   server nodes, and etcd health. Save a fresh snapshot:

   ```sh
   sudo k3s etcd-snapshot save --name pre-proxmox5-join
   ```

   Keep the server token with the snapshot: encrypted snapshots need that
   token for restoration. Check that proxmox3 has `secrets-encryption: true`.
3. Check the live Longhorn setting and Fleet bundle state. No Longhorn setting
   needs changing for an agentless server because it never becomes a Node.
4. Review the playbook, use a reliable SSH path from the NAS, and arrange the
   `sup` sudo password. Do not run the whole NAS `provision.sh` for this join.

## Join

Run from the NAS repository root. `ANSIBLE_LOCAL_TEMP` and
`ANSIBLE_SSH_CONTROL_PATH_DIR` can be omitted when your normal Ansible cache is
writable. The playbook reads the installed k3s version and join token from
proxmox3, writes the token only to a root-only config file on proxmox5, and
changes no other host.

```sh
cd ansible
ansible-playbook -i inventory/proxmox-vms.yml \
  k3s-quorum-proxmox5_playbook.yml --syntax-check
ansible-playbook -i inventory/proxmox-vms.yml \
  k3s-quorum-proxmox5_playbook.yml --ask-become-pass
```

If the join fails after writing `/etc/rancher/k3s/config.yaml`, do not simply
rerun the playbook: inspect `journalctl -u k3s` and the member list first.
The playbook refuses an existing binary or server database to avoid joining
with stale identity. It does not remove or reset any etcd data. For the
2026-11-18 failed join with an `egress-selector-mode` mismatch, run the
dedicated `k3s-quorum-proxmox5-recover_playbook.yml` after confirming the
reported failure. It changes only proxmox5 and requires that exact old config.

## Verify before any old-node action

From proxmox3, check API readiness and that proxmox5 has no Node or Longhorn
object. On proxmox5, check its service. An etcd-only member does not serve a
local Kubernetes API:

```sh
sudo k3s kubectl get --raw=/readyz
sudo k3s kubectl get nodes -o wide
sudo k3s kubectl get node proxmox5 --ignore-not-found -o name
sudo k3s kubectl -n longhorn-system get nodes.longhorn.io proxmox5 --ignore-not-found -o name
sudo k3s kubectl get pods -A --field-selector spec.nodeName=proxmox5
sudo k3s kubectl get pods -n kube-system -o wide
# On proxmox5:
sudo systemctl is-active k3s
```

`kubectl get nodes` will still list only the existing Kubernetes nodes. This
does **not** mean proxmox5 failed to join: inspect embedded etcd membership
separately. If `etcdctl` is installed on proxmox3, use the k3s client certs:

```sh
sudo env ETCDCTL_API=3 etcdctl \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=/var/lib/rancher/k3s/server/tls/etcd/server-ca.crt \
  --cert=/var/lib/rancher/k3s/server/tls/etcd/client.crt \
  --key=/var/lib/rancher/k3s/server/tls/etcd/client.key \
  member list -w table
sudo env ETCDCTL_API=3 etcdctl \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=/var/lib/rancher/k3s/server/tls/etcd/server-ca.crt \
  --cert=/var/lib/rancher/k3s/server/tls/etcd/client.crt \
  --key=/var/lib/rancher/k3s/server/tls/etcd/client.key \
  endpoint health --cluster -w table
```

Require **three started, healthy etcd members**, including proxmox5, plus
ready APIs before treating quorum as improved. This does not validate the
Longhorn volumes or old host storage. Keep proxmox2-old running and in its
current maintenance state until its storage/runtime recovery is verified and
the old etcd member is removed in a separate planned operation.

## Observed result — 2026-11-18

The initial join failed because `egress-selector-mode: pod` did not match the
existing servers' cluster-critical `agent` setting. The recovery playbook
completed and configured proxmox5 as an agentless, etcd-only server.
Proxmox3 reported `/readyz` as `ok`; `kubectl get nodes` listed only proxmox3
and proxmox2-old, and Longhorn had no proxmox5 node object. `etcdctl member
list` showed proxmox5 (`10.0.0.80`) started and voting (`IS LEARNER=false`)
alongside proxmox3 and proxmox2-old. `etcdctl endpoint health --cluster`
reported all three endpoints healthy: proxmox5 4.716208 ms, proxmox2-old
5.122999 ms, and proxmox3 8.2991 ms. These observations establish the third
voter at the time of the check; they do not establish Longhorn recovery.
