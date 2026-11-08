# Piwigo (cycuszki)

Private photo gallery for sensitive photos: TrueCharts `piwigo` 22.0.12
(Piwigo 16.4.0, linuxserver.io image) with its MariaDB, in `apps-piwigo`.

- https://cycuszki.rancher.tailscale.bnowakowski.pl/
- https://cycuszki.rancher.localdomain.bnowakowski.pl/

## Encryption: where photo data is stored

| Data | Location | Storage |
| --- | --- | --- |
| Original photos (`upload/`, `galleries/`) | `/gallery` | PVC `piwigo-gallery-longhorn-crypto`, 10Gi, encrypted |
| Thumbnails and resized copies (`www/_data`), config, plugins | `/config` | PVC `piwigo-config-longhorn-crypto`, 5Gi, encrypted |
| Database: titles, tags, EXIF incl. GPS, users | MariaDB | PVC `piwigo-mariadb-data-longhorn-crypto`, 2Gi, encrypted |
| Uploads in transit (PHP upload temp, sessions, nginx body buffers) | `/tmp` | RAM (`emptyDir` medium Memory, 1Gi) |

The PVCs come from `infrastructure/piwigo-storage` and use
`longhorn-crypto-2replicas` (see `infrastructure/longhorn-crypto-storage`).
Their Longhorn NAS backups are encrypted too.

Not covered: CloudCasa. If its backup policy includes `apps-piwigo` and makes
file-level copies, it would upload decrypted photos. Keep `apps-piwigo`
excluded in the CloudCasa portal.

## Login required

`custom-init/90-require-login.sh` (a ConfigMap mounted at
`/custom-cont-init.d`) sets `$conf['guest_access'] = false;` in
`/config/www/local/config/config.inc.php` on every start. Every page then
redirects to the login page, and anonymous visitors see nothing.
Self-registration is switched off in the database (`allow_user_registration`).
Authelia is intentionally not in front.

## Security considerations and open TODOs

See [`doc/piwigo-security.md`](../../../doc/piwigo-security.md): where every
kind of data is stored, what the encryption does and doesn't protect against,
CloudCasa, keys, and the checklist for each Piwigo upgrade.

## Initial admin login (Secret, not in git)

The chart installs Piwigo on first start with the admin account from the
values Secret `apps-piwigo/piwigo-values`. On 2026-11-03 it was created with a
random 24-character password (user `sup`) that was never printed. Read it with:

```sh
kubectl -n apps-piwigo get secret piwigo-values \
  -o jsonpath='{.data.values\.yaml}' | base64 -d
```

The disk-encryption passphrase is read the same way (store it in a password
manager; it is needed to restore the volumes or their NAS backups):

```sh
kubectl -n longhorn-system get secret longhorn-crypto \
  -o jsonpath='{.data.CRYPTO_KEY_VALUE}' | base64 -d; echo
```

To recreate the values Secret by hand, e.g. for a reinstall:

```sh
read -rs 'P?Piwigo admin password: '; echo   # zsh; in bash: read -rsp 'Piwigo admin password: ' P
kubectl -n apps-piwigo create secret generic piwigo-values \
  --from-file=values.yaml=<(printf 'piwigo:\n  admin_user: "sup"\n  admin_pass: "%s"\n  admin_email: "dobrowolski.nowakowski@gmail.com"\n' "$P")
unset P
```

The chart's installer passes this password in the Deployment's `postStart`
command, and Deployments are not encrypted at rest (only Secrets are). So
treat it as a temporary password and change it in Piwigo (Profile) after the
first login. The installer runs only once (`/config/POSTSETUP`); changing the
Secret afterwards does not change the Piwigo password.

## Deployment and validation (2026-11-03)

Applied with `fleet apply` (commands below), not pushed. Validated:

- PVCs Bound, Longhorn volumes `encrypted=true`; in the pods `/gallery`,
  `/config` and the MariaDB data dir are `/dev/mapper/pvc-…` (dm-crypt), `/tmp`
  is tmpfs.
- `[require-login] $conf['guest_access'] = false;` in the pod log; anonymous
  requests to `/`, `/picture.php` and `/register.php` get only the login form,
  `ws.php` API returns 401.
- `allow_user_registration` set to `false` in `piwigo_config` (one-time SQL;
  stored on the encrypted database volume).
- Both certificates Ready.

Gotchas:

- Fleet renders `${...}` in `values.yaml` as its own template syntax, so the
  init script uses `$line`, not `${line}` (the bundle failed with
  `function "line" not defined`).
- Fleet marked `piwigo-storage` not ready while the PVCs were still being
  provisioned and did not re-check, which blocked `piwigo` (`dependent
  bundle(s) are not ready`). Bumping the bundle's `spec.forceSyncGeneration`
  made it re-check.

## Apply

```sh
cd ~/code/homelab
fleet apply --kubeconfig /etc/rancher/k3s/k3s.yaml --namespace fleet-local \
  --label fleet.cattle.io/repo-name=rancher-cluster \
  longhorn-crypto-storage rancher/fleet/infrastructure/longhorn-crypto-storage
fleet apply --kubeconfig /etc/rancher/k3s/k3s.yaml --namespace fleet-local \
  --label fleet.cattle.io/repo-name=rancher-cluster \
  piwigo-storage rancher/fleet/infrastructure/piwigo-storage
fleet apply --kubeconfig /etc/rancher/k3s/k3s.yaml --namespace fleet-local \
  --label fleet.cattle.io/repo-name=rancher-cluster \
  piwigo rancher/fleet/apps/piwigo
```

## RAM-backed temporary storage schema

Chart 22.1.0 references a common schema whose built-in `persistence.tmp`
entry does not allow `size`. Keep the built-in entry disabled and use the
custom `persistence.photo-tmp` volume at `/tmp`: `emptyDir`, `medium: Memory`,
`size: 1Gi`, and `targetSelectAll: true`. Custom volumes support `size`, which
renders Kubernetes `emptyDir.sizeLimit`. Preserve RAM backing and the 1Gi
limit so temporary photo uploads never fall back to unencrypted node storage.
Do not remove the size cap or bypass schema validation to handle this error.
