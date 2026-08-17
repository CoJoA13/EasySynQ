# EasySynQ Administrator & IT Manual

## 1. Role of the administrator

The **System Administrator** runs infrastructure, identity, recoverability, and system
configuration. The role deliberately contains no QMS content authority by default.

Keep these responsibilities separate:

| System / IT responsibility | QMS responsibility |
|---|---|
| Host, Docker, TLS, DNS, ports | Document ownership and content |
| Keycloak identities and federation | Clause/process mappings |
| EasySynQ user lifecycle and system grants | Review, approval, release |
| Storage, WORM, backups, restore, upgrade | Audit findings and CAPA decisions |
| SMTP, alarms, health, logs | Register stewardship and compliance interpretation |

If one person must wear both hats, assign the additional QMS role explicitly. Do not turn the
System Administrator bundle into an all-powerful role; the separate assignment and audit trail are
the control.

Start with the [Installation Guide](installation-guide.md) for a new system and the
[Operator Runbook Index](../runbooks/00-index.md) for focused recovery procedures.

## 2. Current system boundary

### Deployment

- One organization per installation.
- One Linux host/VM managed with Docker Compose.
- Shipped profiles: S and M.
- S: one API, worker, and renderer.
- M: two API, worker, and renderer replicas.
- PostgreSQL, MinIO, Redis, Keycloak, Tika, web, proxy, and Beat remain single services; Beat must
  have exactly one replica.
- Both profiles use PostgreSQL full-text search. OpenSearch is not deployed.
- L, Kubernetes/Helm, and the observability overlay are reserved rather than supported artifacts.

### Authoritative data

- PostgreSQL is authoritative for metadata, workflow, authorization, identity schema, and the
  append-only audit trail.
- MinIO is authoritative for immutable blobs and renditions.
- Redis is ephemeral broker/cache/lock state.
- The filesystem mirror is generated read-only output.
- Search state is currently PostgreSQL FTS; no separate index backup/rebuild is required.

Back up PostgreSQL and MinIO together. Never treat the mirror as a recovery source.

### External ports

| Port | Exposure |
|---|---|
| 80 | ACME/redirect as applicable |
| 443 | SPA, API, health, Keycloak realm |
| 9443 | browser-facing presigned S3 operations |

All other service ports stay on the private Compose network.

## 3. Standard Compose context

Run production commands from the repository root. To avoid omitting the profile or production
overlay, define this array for the current shell:

```bash
EASYSYNQ_PROFILE_NAME="$(sed -n 's/^EASYSYNQ_PROFILE=\([^[:space:]#]*\).*/\1/p' .env)"
EASYSYNQ_COMPOSE=(
  docker compose --env-file .env
  -f infra/compose/compose.yml
  -f "infra/compose/compose.${EASYSYNQ_PROFILE_NAME}.yml"
  -f infra/compose/compose.production.yml
)
```

Use `"${EASYSYNQ_COMPOSE[@]}" <command>` in the examples below. The host helper
`./scripts/easysynq` intentionally dispatches supported one-off administration jobs.

## 4. First-run ownership handoff

The browser wizard is six screens:

1. Activate
2. Organization
3. Storage
4. Backup
5. Authentication
6. Finalize

User, role, process-owner, and import work occurs after finalize. The ten-step flow in the design
specification is an onboarding ownership model, not the current screen count.

Before handoff:

- ensure WORM verification and the restore-test gate passed;
- record whether an independent audit witness exists;
- create a separate QMS Owner identity/assignment;
- create a second System Administrator before disabling the bootstrap administrator;
- set the working calendar and timezone;
- test notification delivery/alerts; and
- document backup key and signing-key custody.

## 5. Identity and user lifecycle

### 5.1 Two linked identities

Every person has:

1. a Keycloak identity used to authenticate; and
2. an EasySynQ `app_user` row carrying status, roles, overrides, and scope.

The Admin SPA's one-step **Create user** action (§5.2) creates the sign-in account and the
`app_user` row together. A first successful sign-in can JIT-provision an unprivileged ACTIVE
`app_user` row when none was pre-created. Normal administration is entirely in EasySynQ; operators
do not open Keycloak or handle identity subjects.

### 5.2 Create a user (Admin SPA)

1. Sign in as an administrator holding `user.create` (add `permission.grant` too if you also want
   to assign a role in the same step).
2. Open Account → **Administration → Users** and select **Create user**.
3. Enter a **username** (required). Display name, email, first name, and last name are optional. A
   **Roles** picker appears only for a caller holding `permission.grant` — pick zero or more seeded
   roles to assign immediately, or leave it and assign roles later from **Manage**.
4. Select **Create**.

Submitting creates the Keycloak sign-in account and the EasySynQ `app_user` row together, in one
call, and displays a generated **temporary password once**. It is not stored anywhere and cannot be
shown again — copy it or hand it to the person directly before closing the dialog. EasySynQ does
not email it, or an account invitation, to them: realm SMTP is not configured on this install. The
row starts `INVITED`; Keycloak forces the person to choose their own password at first sign-in, and
EasySynQ flips the row `INVITED` → `ACTIVE` on that first successful sign-in.

If the username already exists in Keycloak but is not yet linked to an EasySynQ user — for example,
an account created directly in the Keycloak console, or via §5.3's fallback below — the form offers
**Link the existing account** instead of failing. Linking binds the existing Keycloak account to a
new `app_user` row without creating a second Keycloak account and without touching its password,
but it does **not** assign any roles picked in step 3 above — assign those afterward from
**Manage**.

Forgot a password, or need to reissue one later? Open **Manage** on the user's row and select
**Issue new temp password** — it generates and displays a fresh show-once temporary password the
same way, and is now the normal password-reset path.

Never use a shared generic login for approval or acknowledgement work.

### 5.3 Break-glass and orphan adoption

These are exceptional recovery tools, not an installation or normal user-creation path. If an
identity was created outside the application or a prior provision was interrupted, the subject-based
`POST /users` endpoint adopts that orphan into an EasySynQ `app_user`. It requires `user.create` and
is used only by a controlled recovery procedure.

`scripts/new-keycloak-user.sh <username> [email] [FirstName] [LastName]` is likewise a
break-glass identity-recovery tool. It can create or reset an external sign-in account and exposes a
subject for the recovery operator; it is never used for the first administrator or ordinary users.
Use `POST /users` to adopt the resulting orphan, then open **Administration → Users → Manage** and
assign only the required seeded role(s).

`./scripts/easysynq grant-role <sub> "System Administrator" --org <short-code>` is an even narrower
break-glass grant: it bypasses the wizard and PEP, JIT-creates the `app_user`, and must be recorded
in the organization's independent change or incident record. Use it only to recover a known orphan,
not to seed a normal installation.

If an unrelated System Administrator assignment already blocks the public first-administrator flow,
use this host-only recovery while the organization is still exactly `UNINITIALIZED`:

```bash
./scripts/easysynq setup release-administrator-blocker \
  --subject <keycloak-subject> --org <short-code>
```

The command locks the setup singleton before the organization's administrator set, refuses
`IN_SETUP` and completed installations, and refuses the user linked to an active bootstrap claim.
It removes only the exact named user's System Administrator assignment. It does not call Keycloak,
delete or disable the identity or `app_user`, remove another role or historical attribution, change
setup or claim state, consume a setup proof, or grant replacement access. An absent user or already
absent assignment is a safe no-op. After a successful release, retain or mint a valid setup secret
and resume the normal `/setup` browser flow.

This pre-authentication host action cannot use the normal application audit actor. Before running
it, open an independent incident or change record; record the command, operator, reason, exact
subject, organization, and time. Retain the command result with that record.

### 5.4 Disable, re-enable, and retire access

- **Disable** prevents application access without deleting historical attribution.
- **Enable** returns a disabled user to active use.
- Keycloak should also be disabled/removed according to the organization's identity process.
- Do not delete historical user rows or replace identifiers: audit/signature attribution depends on
  stable principals.
- The service refuses disabling the last active administrator. Maintain at least two.
- External Auditor access should be narrowly scoped and time-boxed.

For urgent recovery, use the controlled break-glass/orphan-adoption procedure in §5.3. It writes the
assignment outside the normal API audit path, so record the command, operator, reason, subject, and
time in the organization's independent change/incident record.

## 6. Roles, overrides, and process ownership

### Seeded role bundles

The current Roles tab is read-only and shows the seeded role grants. Common bundles include:

- System Administrator
- QMS Owner
- Process Owner
- Author
- Approver
- Internal Auditor
- Employee (Read-only)
- External Auditor (Guest)
- Top Management
- Register Steward

Custom role creation/editing is not available in the current UI.

### Assign and revoke roles

Administration → Users → Manage shows the user's role assignments. Assign or revoke a bundle there.
Changes are immediately subject to the centralized policy engine.

### Overrides

The current Users UI creates **SYSTEM-scoped** direct ALLOW or DENY overrides from an exact
permission key. Finer process/folder/document scopes are API-level operations.

Controls:

- validate permission keys against the current catalog;
- use roles before one-off ALLOWs;
- use DENY sparingly and document why;
- remember that DENY always wins, including over a role ALLOW;
- never grant SYSTEM permissions through the content-authority tier; and
- review self-grants and broad overrides as privileged changes.

The permission catalog currently contains 102 additive keys. Do not invent or rename a key.

### Process owners

Administration → Processes lists existing processes. **Manage owners** assigns/revokes the
accountable owner. Assignment records the Clause 5.3 relationship and mints the Process Owner
permission set scoped to that process.

The tab does not create process definitions; it manages ownership of existing process rows.

## 7. Organization configuration

Administration → Config contains:

- **Email delivery (organization-wide)** — enable only after SMTP is configured and tested;
- **Escalation pierces quiet hours** — controls critical/escalation delivery behavior;
- **Working Calendar** — working weekdays/holidays/timezone used by due dates and reminders; and
- **Notification health** — current delivery/queue health.

Individual users control email cadence, digest hour/timezone, and quiet hours under Account →
Notification settings. The in-app bell remains immediate.

Relevant `.env` groups:

- `SMTP_*` — application email relay;
- `OPS_ALERT_CHANNELS` — comma-separated `syslog,smtp,webhook`;
- `OPS_ALERT_SMTP_TO`, `OPS_ALERT_WEBHOOK_*`, `OPS_ALERT_SYSLOG_ADDRESS`;
- `AUDIT_WITNESS_REQUIRED` and `AUDIT_WITNESS_GRACE_HOURS`;
- `BACKUP_PATH` and encryption/signing-key paths; and
- browser origins, which must stay a coherent FQDN tuple.

After changing `.env`, recreate/restart only the affected services and re-check readiness.

## 8. Health, logs, and monitoring

### Health endpoints

```bash
curl -fsS "https://<host>/healthz"
curl -fsS "https://<host>/readyz"
```

`/healthz` proves the API process is alive. `/readyz` returns HTTP 200 only when these current checks
are ready:

- PostgreSQL;
- Redis;
- MinIO;
- Keycloak/JWKS; and
- the database's Alembic revision equals the application head.

OpenSearch is intentionally not checked because it is not deployed.

### Compose state and logs

```bash
"${EASYSYNQ_COMPOSE[@]}" ps
"${EASYSYNQ_COMPOSE[@]}" logs --tail=200 api
"${EASYSYNQ_COMPOSE[@]}" logs --tail=200 worker beat
"${EASYSYNQ_COMPOSE[@]}" logs --tail=200 keycloak postgres minio
```

Use `-f` only while actively watching; retain relevant logs according to the organization's incident
policy. Operational logs are separate from the compliance audit trail.

There is no bundled Prometheus/Grafana/Loki overlay. Forward stdout/health into the organization's
monitor with a local, reviewed override if automatic paging is required.

### Alarm path

Backup and integrity failures try in-app/admin notification plus configured out-of-band channels.
Do not rely on in-app delivery alone: a PostgreSQL outage prevents recipient lookup and audit-row
creation.

After configuring a channel, force a controlled test and confirm the
`ops_alert.dispatched` log reports the expected result. UDP syslog “sent” means emitted, not
confirmed delivered.

## 9. Backups and restore drills

The backup-critical stores are PostgreSQL and MinIO. Keycloak's durable schema lives in PostgreSQL.
Every durable archive includes the DB dump and blob locator/hash manifest; it contains **no MinIO
object bytes**. Realm export, config snapshot, and latest signed audit checkpoint are best-effort
legs and may be absent. With a configured backup key the archive is encrypted; with an
unset/placeholder key it is plaintext and omits the secret-bearing realm/config legs. Inspect the
command result and a newly written manifest's `encrypted`/`legs` fields before relying on an
artifact; older manifest-v2 artifacts can omit `encrypted` and require envelope inspection. G-C
does not prove durable encryption, key viability, or optional-leg presence.

### Commands

```bash
./scripts/easysynq backup run
./scripts/easysynq backup restore-test
```

The restore-test:

- restores to a throwaway database and non-WORM scratch bucket;
- re-hashes blobs;
- compares table counts;
- verifies document-version/blob references; and
- tears down the scratch namespace.

Run it after backup-target, credential, storage, database, or release changes—not only at initial
setup.

### Key custody

`BACKUP_ENCRYPTION_KEY` is not stored in the archive. Losing it makes every archive sealed with that
key undecryptable. Retain old backup keys for as long as their archives must remain decryptable and
available for integrity verification.
Store keys independently from both the host and its backup destination.

### Restore integrity verification

```bash
./scripts/easysynq restore <archive.tar.enc> --confirm
```

The command leaves an integrity-verification target standing for inspection/discard. PASS proves
the flattened scratch copy re-hashes and the restored database's locators resolve against the
currently configured source object store. It is **not cutover-ready**. Do not repoint a service at
the scratch database or bucket. A checkpoint-ahead result exits flagged and requires an explicit
`--audit-checkpoint-ack`, which must be investigated and documented. Follow the complete
[backup/restore runbook](../runbooks/backup-restore.md) for the current constraints.

A future supported recovery must use a self-contained object-byte generation, fresh role-preserving
object-store targets, atomic database/object-store configuration switching, and closed-service read
verification. Mirror rebuild is a post-recovery requirement only after that proof exists; this is
not a current CLI procedure.

## 10. Upgrades

Plan a maintenance window, stage a reviewed release, confirm recovery keys, and run a fresh backup
and restore test.

Normal helper:

```bash
./scripts/easysynq upgrade --confirm
```

It performs a pre-backup, Alembic upgrade, and readiness gate. It does not make an unsafe migration
lock disappear. The pre-upgrade archive has a database dump and blob manifest but no object bytes;
it is not a disaster safety net. Production upgrade eligibility remains blocked until
source-independent recovery generation and role-preserving restore/cutover are implemented and
proven. The command's existence is not production authorization.

For any release whose migration builds an index on `audit_event`, stop writers first because the
current migration environment has no `lock_timeout`:

```bash
"${EASYSYNQ_COMPOSE[@]}" stop api worker beat
./scripts/easysynq upgrade --confirm
"${EASYSYNQ_COMPOSE[@]}" start api worker beat
curl -fsS "https://<host>/readyz"
```

Before the first upgrade from a legacy H2-backed Keycloak install, run:

```bash
./scripts/migrate-keycloak-h2.sh
```

`install.sh` invokes it automatically; raw Compose recreation does not. Follow the
[upgrade section](../runbooks/backup-restore.md) for lock sizing and recovery.

## 11. Integrity, mirror, and audit witness

### Blob integrity

```bash
./scripts/easysynq blob verify
./scripts/easysynq blob verify --full
```

The rolling scan is routine; use full verification after a separately validated direct repair, a
future supported recovery that restores the live source, or a suspected storage event. Current
source-dependent restore does not repair the live vault. Follow
[Blob integrity verification](../runbooks/blob-integrity-verify.md) on any mismatch.

### Mirror

```bash
./scripts/easysynq mirror rebuild
```

The mirror is vault-derived. Investigate a drift alarm, preserve evidence, and let the controlled
sync/rebuild restore it. Never ingest a modified mirror file as authoritative. See
[Mirror drift scan](../runbooks/mirror-drift-scan.md).

### Audit chain and off-host witness

An append-only on-host chain alone cannot prove integrity against a privileged host owner. Configure
a genuinely separate append-only/WORM sink before setting:

```dotenv
AUDIT_WITNESS_REQUIRED=true
```

Do not turn it on first; that intentionally produces nightly alarms. After provisioning the sink,
verify independent read-back:

```bash
"${EASYSYNQ_COMPOSE[@]}" run --rm worker \
  uv run python -m easysynq_api.cli.audit verify-offhost
```

The current verifier checks the newest off-host checkpoint, not the complete checkpoint lineage.
Signing-key history across audit-checkpoint rotation is also not modeled. Preserve old public keys
and follow [Key rotation](../runbooks/key-rotation.md).

## 12. Security operations

### Secrets

- Keep `.env` mode `0600` and out of version control.
- Never copy plaintext secrets into tickets, chat, logs, or backup manifests.
- Separate custody for app KEK, backup key, audit signing key, verify-token key, and witness
  credentials.
- Restart affected containers and run a new backup after rotation.
- Retain old verification/backup material for the retention period it must validate.

### TLS and hostname

Changing the hostname changes the OIDC issuer and signs users out. Update the entire browser-origin
tuple together: site, public/app base URLs, Keycloak hostname/issuer, SPA callback, and S3 public
endpoint. Use the appliance's `easysynq-reconfigure` helper where applicable.

### WORM

`GOVERNANCE` supports controlled privileged retention handling; `COMPLIANCE` cannot be bypassed even
by root before expiry. Confirm policy/legal requirements before choosing COMPLIANCE. Current
restores use the configured shared scratch bucket with a unique per-run prefix; they do not
provision a new bucket or establish a production recovery target.

### Access review

At least quarterly:

- review active/disabled/guest accounts;
- inspect broad SYSTEM overrides and explicit DENYs;
- confirm process owners and Top Management membership;
- verify the second administrator;
- test guest expiry/scope;
- review Keycloak MFA/password/federation policy; and
- reconcile role assignments with employment/organizational changes.

## 13. Routine operations schedule

| Cadence | Minimum action |
|---|---|
| Daily | Check `/readyz`, Compose health, disk pressure, backup/integrity alarms, and worker/Beat health. |
| Nightly automated | Backup, audit-chain verification, blob sample, due-date/review/retention sweeps. |
| Weekly | Review failed jobs/notifications, mirror/blob findings, backup archive arrival, and capacity trend. |
| Monthly | Run/confirm a restore test, verify off-host witness read-back, review admin/guest access, patch host. |
| Quarterly | Full blob verify, key/access review, recovery tabletop, certificate/retention review. |
| Before upgrade | Reviewed release, migration review, maintenance plan, and a proven self-contained recovery path. Current restore PASS alone is insufficient; production eligibility remains blocked. |
| After upgrade | Readiness, login, upload/download on 9443, task worker, backup, and critical QMS smoke checks. |

Adjust cadence to the organization's risk and retention policy.

## 14. Incident response quick map

| Incident | First response |
|---|---|
| Keycloak down / login failure | Preserve logs; restart Keycloak; verify `/readyz`; use [SPOF fast restart](../runbooks/spof-fast-restart.md). |
| Beat down / scheduled work stopped | Restart Beat only; ensure exactly one replica; verify next idempotent sweep. |
| PostgreSQL down | Stop writes/restarts that churn; restore database service; use out-of-band alarm path. |
| MinIO/WORM unavailable | Stop content mutations, preserve logs, restore service; do not redirect to an unlocked bucket. |
| Backup failed | Investigate destination, credentials, space, and `pg_dump`; run backup + restore test after repair. |
| Blob hash mismatch | Preserve affected object/evidence; follow the blob-integrity runbook; do not overwrite blindly. |
| Mirror drift | Quarantine/record the mismatch and run the controlled scan/rebuild procedure. |
| Audit-chain/witness alarm | Preserve host, DB, checkpoint, and logs; restrict privileged access; verify independently before repair. |
| Certificate expiry/trust | Restore valid certificate/CA trust without changing issuer hostname unless intentionally reconfigured. |
| Failed migration/readiness | Keep service closed; preserve the source object store; retain the exact non-self-contained archive pointer; do not cut over from the scratch verification target. |

## 15. Known limitations and residuals

Operational planning must account for:

- no L profile, OpenSearch service, or bundled observability stack;
- no in-app custom-role editor;
- no supported live restore cutover; the current scratch target is verification-only and
  source-store-dependent;
- no self-contained object-byte backup generation, so durable/pre-upgrade archives are not disaster
  safety nets;
- no migration `lock_timeout`;
- no mounted T5 approval-rescind/reschedule or T8 revision-draft discard transition; monitor Beat
  because scheduled effectivity is applied by the five-minute release sweep, not by reads;
- newest-checkpoint-only off-host verification and no audit public-key history;
- import revision-chain reconstruction refused as unsupported;
- known ingestion progress/retry edges around reaping and PartiallyCommitted runs; and
- a known CAPA multi-approver reject/changes-requested wedge/coverage gap.

The authoritative residual ledger is [`slice-history.md`](../slice-history.md). Zero open GitHub
issues does not supersede that ledger.

## 16. Destructive-operation warning

These are materially destructive and require an approved recovery/retention decision:

- `docker compose down -v`;
- deleting PostgreSQL/MinIO named volumes;
- deleting or replacing `.env` without retained secrets;
- changing WORM retention or removing buckets;
- reusing an old database with mismatched blob storage; or
- force-removing audit/identity rows.

Do not improvise recovery from the current scratch verification target. A future supported
fresh-target recovery must satisfy the role-preserving, source-independent requirements in the
backup/restore runbook. Record every production restore, key rotation, hostname change, and
break-glass grant in the organization's change-control system.
