# Zabbix chart-label ownership repair

On 8 November 2026, the chart 8.0.1 upgrade failed because `kubectl-patch`
owned these two fields on `apps-zabbix/zabbix-postgresql`:

- `metadata.labels["helm.sh/chart"]`
- `spec.template.metadata.labels["helm.sh/chart"]`

The previous controller repair had aligned chart labels using a merge patch.
That allowed the earlier upgrade but retained the patch manager's ownership,
which conflicted with a subsequent Helm server-side apply changing the chart
version. Repeatedly patching the version labels can repeat this problem on
future upgrades.

## Interactive repair

`helm-upgrade-apps.sh` handles this exact Zabbix failure before its generic
Fleet retry prompt. It diagnoses the latest failed Helm target and prepares a
server-dry-run-tested metadata patch, then asks **Apply repair / Skip repair**.
The default is Skip; `--yes` does not approve ownership changes. Dry-run mode
never applies them.

After approval, the helper relinquishes only those two `helm.sh/chart` claims
from the `kubectl-patch` manager's `apps/v1` field set. It does not update the
labels' values directly. The normal Fleet retry then lets Helm set and own the
new chart labels. Other managers and ownership claims remain intact.

The helper rejects unrelated managers, additional conflicting fields, other
resources, unexpected Helm ownership, a target chart different from the latest
failed release, or a desired manifest with unexpected version labels.

## Preserved state and validation

- The patch changes only `metadata.managedFields`. Original label values,
  selector, pod template, database image, PVC settings and StatefulSet UID
  remain unchanged during the metadata repair.
- Original snapshots and the exact patch are kept in the existing ignored log
  directory under `zabbix-chart-label-repair-*/`, with restricted permissions.
- UID, resourceVersion and the original managedFields are JSON Patch test
  preconditions. A changed object stops the repair instead of overwriting it.
- Server dry runs run before approval and again immediately before the write.
  The actual response must preserve all live values and object identity.
- No resource is deleted or recreated. There is no whole-object field ownership
  reset, direct Helm install, schema-validation bypass or PVC operation.
- The subsequent normal chart rollout may restart PostgreSQL. The existing
  Fleet retry rebases the Helm revision after repair, requests a fresh
  deployment and validates reconciliation. Existing rollout and HTTP checks
  still gate upgrade success.

Declining or failing preflight leaves cluster state unchanged. A failed write
or retry retains the snapshots for review and leaves the app failed; it is not
reported as a successful upgrade.

## Install and use

After installing this script change, restart `helm-upgrade-apps.sh` so the
running shell loads the new functions. When the matching Zabbix upgrade error
occurs, select **Apply repair** if the proposed scope matches the intended
change. Existing old running script processes do not pick up a file update.

Run offline tests:

```bash
/usr/bin/python3 tests/test_helm_zabbix_label_repair.py
bash -n helm-upgrade-apps.sh
```

Commit the upgrade script, `lib/helm-zabbix-label-repair.py`, its tests and this
document. Do not commit ignored incident snapshots/logs. The implementation
was tested offline; a live repair still requires approval and successful
validation in the user's restarted upgrade run.
