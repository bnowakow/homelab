# Jenkins

Fleet installs TrueCharts Jenkins chart `12.0.4` (Jenkins `2.544`, JDK 17) in
`apps-jenkins`. The app waits for `jenkins-storage`, which creates a 10Gi
Longhorn RWO PVC, `jenkins-data-longhorn-rwo`, with the existing two-replica
StorageClass and NAS backup labels. It is mounted at `/var/jenkins_home`.

Jenkins keeps its whole state in `JENKINS_HOME`, so exactly one replica runs,
with the Recreate strategy. The PVC has `helm.sh/resource-policy: keep`.
Longhorn can expand it live if build history or workspaces outgrow 10Gi;
discard old builds in each job to keep it bounded.

HTTPS endpoints follow the other applications (one Traefik ingress, two hosts,
cert-manager `letsencrypt` issuer, `websecure` only):

- https://jenkins.rancher.tailscale.bnowakowski.pl/
- https://jenkins.rancher.localdomain.bnowakowski.pl/

Authelia is intentionally not in front of Jenkins. Jenkins has its own login,
and webhooks, API tokens and the CLI would break behind an Authelia redirect.

## First start

Unlock Jenkins with the generated admin password, then finish the setup wizard
(plugins, first admin user, and Jenkins URL; use one of the hosts above):

```sh
kubectl -n apps-jenkins exec deploy/jenkins -- cat /var/jenkins_home/secrets/initialAdminPassword
```

The inbound-agent port `50000` is exposed only as the in-cluster Service
`jenkins-agent.apps-jenkins.svc:50000`. Agents outside the cluster would need
a separate TCP route.

Resource limits are 4 CPU / 4Gi memory (requests 50m / 512Mi). Raise the
memory limit together with `-Xmx` (`jenkins.java_opts`) if builds run on the
built-in node.

## Build agents (Kubernetes plugin)

Builds don't run on the controller. The Jenkins `kubernetes` plugin starts a
throwaway pod per build in `apps-jenkins`, from the pod YAML in each
project's `Jenkinsfile`. For this, `values.yaml` creates the ServiceAccount
`jenkins` with a namespaced Role (pods and pods/exec: create, delete, get,
list, patch, update, watch; pods/log and events: read), and mounts its token
into the controller pod (`workload.main.podSpec.automountServiceAccountToken`).
Jenkins cannot create pods outside `apps-jenkins`; this was checked with
`kubectl auth can-i`.

The Jenkins cloud `kubernetes` uses the in-cluster API, namespace
`apps-jenkins`, Jenkins URL `http://jenkins.apps-jenkins.svc:10280`, tunnel
`jenkins-agent.apps-jenkins.svc:50000`, and allows at most 2 build pods at
once. The cloud, plugins (`kubernetes`, `blueocean`) and jobs live in
`JENKINS_HOME` on the PVC, not in Fleet.

Jobs:

- `cozadzban.pl`: Multibranch Pipeline for https://github.com/bnowakow/cozadzban.
  It tests every branch and pushes the Docker image `bnowakow/cozadzban` from
  `main`. Its build pods use a **privileged** Docker-in-Docker sidecar, with
  the daemon bound to 127.0.0.1 only. Full documentation, including
  credentials and gotchas, is in the cozadzban repository:
  `doc/JENKINS_CI.md`.

## Deployment and validation (2026-11-01)

Both bundles were applied with the Fleet CLI and the `rancher-cluster` GitRepo
ownership label before commit:

```sh
cd ~/code/homelab
fleet apply --kubeconfig /etc/rancher/k3s/k3s.yaml --namespace fleet-local \
  --label fleet.cattle.io/repo-name=rancher-cluster \
  jenkins-storage rancher/fleet/infrastructure/jenkins-storage
fleet apply --kubeconfig /etc/rancher/k3s/k3s.yaml --namespace fleet-local \
  --label fleet.cattle.io/repo-name=rancher-cluster \
  jenkins rancher/fleet/apps/jenkins
```

Validated: both BundleDeployments applied their desired deployment IDs with
Ready=True (`jenkins` 6/6 resources, `jenkins-storage` 1/1). The PVC is Bound
(10Gi) and mounted at `/var/jenkins_home`, the pod is 1/1 Running, both
certificates are Ready, and `/login` returns HTTP 200 with a valid certificate
on both hosts.

The upgrade script finds this chart through `fleet.yaml`. `jenkins` is also
listed in `truecharts_apps` in `rancher/helm-repositories.sh`.

References:
- https://truecharts.org/charts/stable/jenkins/
- https://www.jenkins.io/doc/book/installing/kubernetes/

## Upgrade HTTP check

Anonymous requests to `/` return HTTP 403 under the configured Jenkins
permissions. Both HTTPS `/login` pages returned HTTP 200 on 7 November 2026.
The ingress annotation `helm-upgrade.bnowakowski.pl/check-path: /login` in
`values.yaml` directs `helm-upgrade-apps.sh` to check that public login page
for prechecks and postchecks. Jenkins authentication and authorization remain
unchanged; other applications continue to use their existing check paths.
