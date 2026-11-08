# Interactive Helm values schema repairs

The upgrade script recognizes the known YouTubeDL Material `fsGroup` placement
error before native Fleet reconciliation. It offers a separate default-No
**Apply repair / Skip repair** prompt, including when upgrades run with
`--yes`. Dry-run mode never edits values or applies this repair.

## Diagnosis and proposed edit

Chart 17.1.0 rejects `securityContext.container.fsGroup`. The group is a pod
filesystem setting. The confirmed values used `fsGroup: 568` in container scope
alongside deliberate root container UID/GID settings. The repair moves only
the group setting:

```yaml
securityContext:
  pod:
    fsGroup: 568
  container:
    runAsUser: 0
    runAsGroup: 0
```

Other container settings, workload replicas/strategy and all PVC/NFS mappings
are preserved. The actual group value is read from the values file; it is not
hardcoded to 568. A conflicting pod-level group, noninteger or negative value
requires manual review and is never guessed or overwritten. A correct file
without container-level `fsGroup` does not trigger a prompt.

## Approval and validation

The prompt explains the move, values path, backup location and possible normal
application restart. No values edit occurs until the user approves. The helper
checks the original SHA-256 again after the prompt, saves the original file,
then writes the corrected YAML. Formatting/comments may be normalized; the
original text remains in the backup.

Existing native Fleet code applies only the affected bundle, with schema
validation enabled. Existing Fleet readiness, rollout and HTTP postchecks
must pass before the repaired values are staged. No direct Helm installation,
validation bypass, PVC migration, storage deletion or credential change is
introduced. Failed reconciliation retains the values and backup for review.

Plans and backups are under the existing ignored log directory as
`schema-repair-youtubedl-material-*/`. Do not commit those runtime artifacts.
Commit `helm-upgrade-apps.sh`, `lib/helm-values-repair.py`, its tests, this
record and successfully validated values changes.

## Use

Restart the upgrade script after installing the implementation. For the
YouTubeDL Material upgrade, select **Apply repair** when the fsGroup prompt
appears. There is no need to apply the earlier one-off values patch first.
If it has already been applied, no repair prompt is needed.

Run offline tests:

```bash
/usr/bin/python3 tests/test_helm_values_repair.py
bash -n helm-upgrade-apps.sh
```

Tests cover exact mapping changes, preserved storage and UID/GID settings,
original-file backup, already-correct values, conflicting and invalid groups,
concurrent edits, tampered plans, decline/dry-run safety with `--yes`, and staging
only after postchecks. The local chart render confirmed pod fsGroup 568 and no
container-level fsGroup; live approval and validation remain the upgrade
script's responsibility.
