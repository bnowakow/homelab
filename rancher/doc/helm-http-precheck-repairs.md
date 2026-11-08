# Interactive HTTP precheck repairs

`helm-upgrade-apps.sh` can offer a repair after an HTTP precheck fails for
Prowlarr, Radarr, Sonarr or Jenkins. This is a separate, default-No confirmation before
any repair writes, including when the upgrade script runs with `--yes`.
`--dry-run` does not apply repairs. Unknown failures retain the existing
Continue/Abort flow.

## Confirmed ARR proxy failure

On 7 November 2026, both Prowlarr HTTPS root checks ended in HTTP 404. The
Service (`10.43.127.246:9696` in the captured inspection) directly returned
HTTP 200 and `/config/config.xml` had an empty
`TrustedNetworks`. The HTTPS request first reached Prowlarr, which redirected
the login request to HTTP; Traefik then returned 404. The same pattern had been
confirmed for Radarr on 6 November.

The diagnostic offers this repair only when all of these checks pass:

- The app is Radarr, Prowlarr or Sonarr, with a release-owned TLS Ingress whose health
  check uses `/`.
- Each HTTPS root redirects to the same hostname's **HTTP login** URL.
- The expected Service port returns a healthy response directly.
- Exactly one running application container is found, using Forms or Basic
  authentication, without an existing configured or environment-based proxy
  trust policy.
- Running Traefik pods use pod networking and their addresses belong to the
  observed node pod CIDRs. Host-networked or unmatched proxies require manual
  investigation.

The prompt lists the actual CIDRs to trust and explains that local clients
will also require authentication. On approval, only these environment settings
are added to `workload.main.podSpec.containers.main.env`:

```yaml
PROWLARR__SERVER__TRUSTEDNETWORKS: "<observed node pod CIDRs>"
PROWLARR__AUTH__REQUIRED: Enabled
```

Radarr uses the corresponding `RADARR__` prefix; Sonarr uses `SONARR__`
and its expected backend port 8989. Radarr and Sonarr API authentication
checks use `/api/v3/system/status`; Prowlarr uses `/api/v1/system/status`. Existing trust policies are
not overwritten. The repair does not disable authentication, accept all
networks, change CORS routes or alter storage definitions. Trusting these CIDRs
covers cluster pods, rather than identifying Traefik by workload identity;
review the policy if the cluster network or proxy topology changes.

## Confirmed Jenkins protected root

Anonymous `/` requests can legitimately return 403. A repair is offered only
when Jenkins identifies itself through its response headers, all HTTPS root
checks return 403, and the HTTPS login pages return 200.

The approved edit sets:

```yaml
ingress:
  main:
    annotations:
      helm-upgrade.bnowakowski.pl/check-path: /login
```

Authentication and authorization remain unchanged. The HTTP checker continues
to require 200; this does not globally accept 403 for other applications.

## Approval, application and failure handling

1. The helper diagnoses the live HTTP responses and Kubernetes objects without
   cluster writes. It reads only the necessary config properties and never
   includes the application's API key in a plan.
2. The script presents the diagnosis, exact values path, configured chart
   version, restart impact, authentication policy and backup location. The
   user selects **Apply repair** or **Skip repair**.
3. After approval, the helper checks that the values file still matches its
   diagnosed SHA-256. It saves the original text before writing the planned
   YAML edit. YAML formatting/comments may be normalized; the original is in
   the backup.
4. Existing native Fleet application logic applies only the affected bundle
   at its configured chart version. A repair may restart the app.
5. The script requires successful Fleet reconciliation, workload rollout and
   HTTP checks. Additional verification prohibits HTTP downgrade redirects and
   confirms unauthenticated ARR API calls remain rejected. Jenkins keeps its
   protected root response.
6. Only after validation succeeds are the repaired values staged and the
   normal upgrade approval flow resumed. The upgrade itself still follows its
   existing confirmation policy.

Declining a repair leaves values and the cluster unchanged. A failed approved
repair stops that app's upgrade and retains the edited values and backup for
review. It does not silently roll back a live deployment or continue as if
healthy. Retrying starts with fresh diagnosis; an existing policy that needs
adjustment should be inspected manually.

Plans, original values and edit records are stored beneath the existing
ignored upgrade log directory (`logs/http-repair-APP-*/`, or the script's
fallback temporary log directory), with restricted permissions. They are local
incident evidence and should not be committed.

## Files and tests

- `lib/helm-http-repair.py`: read-only diagnosis, guarded values editing and
  post-repair HTTPS/authentication verification. Requires system Python 3 and
  PyYAML, plus existing `kubectl` and `curl` tools.
- `helm-upgrade-apps.sh`: explicit prompt and existing Fleet/validation flow.
- `tests/test_helm_http_repair.py`: offline tests for exact diagnosis, rejected
  generic failures, preserved storage values, existing-policy and changed-file
  guards, authentication verification and declined/dry-run behavior under
  `--yes`.

Run tests from `rancher`:

```bash
/usr/bin/python3 tests/test_helm_http_repair.py
bash -n helm-upgrade-apps.sh
```

The implementation was validated offline before handoff. Live Prowlarr repair
is pending the user's approval in a restarted upgrade script; documentation
of the diagnosis is not a claim that the repair has been applied.

## Temporary inspection files

`prowlarr-http-inspection.json` is a temporary diagnostic capture, not desired
configuration. Its findings are recorded above, so it can be deleted before
commit. Commit the implementation, tests, this document and any values changes
that the user approved and the script successfully validated.

Sonarr support was added after a reported HTTPS 404 precheck failure. The
helper must still confirm the downgrade redirect, empty trust policy, healthy
backend and matching proxy networks before offering a fix. A 404 alone does
not identify the cause. The repair prompt appears before the generic
Continue/Abort failure dialog. Approval is still required before values or
cluster state change.
