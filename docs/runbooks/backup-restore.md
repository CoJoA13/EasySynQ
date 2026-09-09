# Backup, restore-test drill, restore & upgrade

Only **PostgreSQL + MinIO** are backup-critical; the filesystem mirror is regenerable
(D-6 / R11). OpenSearch is also designed as a derived store, but is not deployed by the shipped
S/M profiles. The backup/restore/upgrade CLIs run on the **worker** (it carries `postgresql-client`
+ the OWNER `DATABASE_URL_SYNC`). Current scope = nightly `pg_dump` + blob-manifest archives and a
source-store-dependent integrity-verification target. **The current CLI does not produce a
self-contained recovery generation or a cutover-ready target.** Continuous WAL/PITR, retention
pruning, and S3 destinations are also unshipped (D-6).

## The durable backup archive

`./scripts/easysynq backup run` (and the nightly Beat job `easysynq.backup.run`) writes one timestamped,
checksum-verified archive per configured policy to `BACKUP_PATH` (or the policy's destination):

> A configured path is not a certified mount. Setup rejects blank, relative, and URI-looking values
> and performs a preliminary probe in the API process. The mandatory drill proves current worker
> access, but neither check proves approved persistent/off-host backing or survival across
> restart/recreation. Verify those properties independently before relying on the destination.

* `db.dump` (`pg_dump -Fc`, including Keycloak's durable `keycloak` schema) + `manifest.json` (the
  **blob inventory**: sha256/size/bucket/object-key metadata, + per-table row counts) are the only
  mandatory contents;
* the Keycloak realm export, config snapshot, and latest signed audit checkpoint are independent
  best-effort legs. Any can be `"absent"`; their failure does not fail the DB/manifest archive;
* with a real `BACKUP_ENCRYPTION_KEY`, the archive is AES-256-GCM encrypted to `…tar.enc` and the
  secret-bearing realm/config legs are attempted. With the key unset or still a placeholder, the
  archive is a **plaintext** `.tar` and those secret-bearing legs are deliberately omitted.

Read the `backup run` result and `manifest.json` before relying on an artifact: newly written
manifests record `encrypted`; inspect it and every `legs` value (`realm_export`, `config_snapshot`,
`audit_checkpoint`). Older manifest-v2 artifacts can omit `encrypted`, so identify their envelope
from the artifact format/magic and do not infer encryption from field absence. Gate G-C establishes
none of these properties; it uses a separate transient plaintext tar.

> ⚠ **Not a self-contained recovery set:** the archive records blob locators and hashes, but contains
> **no MinIO object bytes**. Restore verification reads those bytes from the currently configured
> source object store. Preserve that store. Until a source-independent recovery generation and the
> role-preserving restore-target work are implemented and proven, do not treat any durable or
> pre-upgrade archive as a disaster safety net.

> **Key custody (critical):** `BACKUP_ENCRYPTION_KEY` lives ONLY in the `0600` `.env` / a Docker
> secret — never in the archive. **Lose it and every `.tar.enc` is undecryptable and unusable.** Back it up
> out-of-band with the same custody as the host disk-encryption key. See [key-rotation.md](key-rotation.md).

## When a backup fails — the operator alarm

A failed nightly backup is not silent. Each failure writes a durable **`BACKUP_FAILED`** audit row
and sends **`system.backup_failed`** to every System Administrator (in-app + email, subject to the
org email flag and the recipient's own preferences).

> ⚠ **Configure at least one out-of-band channel.** The in-app path needs the database, and the
> failure that hurts most is the one where PostgreSQL is *down*: the nightly job cannot read
> `backup_policy`, resolve an admin, insert a notification or append an audit row. Set
> `OPS_ALERT_CHANNELS` to a comma-separated subset of `syslog,smtp,webhook` (see `.env.example`):
>
> * `syslog` → `OPS_ALERT_SYSLOG_ADDRESS`. ⚠ **Empty by default, and there is no working `/dev/log`
>   in the shipped Compose deployment** — `worker` and `beat` run `python:3.12-slim-bookworm` with no
>   syslog daemon, and neither bind-mounts the host socket, so a `/dev/log` value would look
>   configured and reliably fail. Either point it at a collector reachable from the container
>   (`syslog.internal:514`), or mount the host journald socket into **both** `worker` and `beat` —
>   the two services that run the nightly jobs — via a compose override, then set `/dev/log`:
>
>   ```yaml
>   services:
>     worker: { volumes: ["/dev/log:/dev/log"] }
>     beat:   { volumes: ["/dev/log:/dev/log"] }
>   ```
>
>   Linux hosts with journald only (there is no host `/dev/log` under Docker Desktop). Mounted, this
>   is the air-gap-friendly choice under D1 — no network egress at all.
>
>   ⚠ The two forms report differently. A **unix socket** surfaces an absent or dead socket as
>   `failed`. A **`host:port`** address is UDP and fire-and-forget, so a closed collector port still
>   reports `sent` — the datagram reached the kernel and nothing comes back. Read `sent` on the UDP
>   form as "emitted", not "delivered", and pair it with a second channel where confirmation matters.
> * `smtp` → `OPS_ALERT_SMTP_TO`, an operator mailbox reached over the existing `SMTP_*` relay with
>   no recipient lookup.
> * `webhook` → `OPS_ALERT_WEBHOOK_URL` (+ optional `OPS_ALERT_WEBHOOK_TOKEN`), an off-host receiver
>   the org controls. It carries operational metadata only, never document or record content.
>
> With none configured the alarm still reaches the container log, and nothing else. A channel that
> is *named* but not configured (e.g. `smtp` with no `OPS_ALERT_SMTP_TO`) reports `skipped`, not
> `sent` — check the `ops_alert.dispatched` log line after a test.

The same channel carries **`integrity.alarm`** from the nightly chain verification — see
[key-rotation.md](key-rotation.md) for the witness settings (`AUDIT_WITNESS_REQUIRED`,
`AUDIT_WITNESS_GRACE_HOURS`).

## The restore-test drill (gate G-C / AC#5)

`./scripts/easysynq backup restore-test` writes a `pg_dump`/manifest test archive, restores the
database into a throwaway scratch DATABASE, and copies referenced bytes from the configured source
object store into the configured `restore-scratch` bucket. Operators must provision that bucket as
distinct and non-WORM. The shared guard below checks the destination before any scratch copy.
It then runs the integrity triad
(copied-blob SHA-256 re-hash · stored-locator SHA-256 re-hash against the currently configured object
store · per-table row-count parity · `document_version→blob` FK check) and tears the scratch namespace
down. Only a **PASS** satisfies the setup gate. This is a source-dependent integrity check, not proof
of recovery after source-store loss. "Configured but unverified" does not count.

## Scratch destination safety

The fresh drill, retained-backup verifier and operator restore reject a scratch destination that
matches the configured documents, records or audit-checkpoint role, any source bucket in the complete
archive manifest, or any `worm_bucket` checkpoint destination declared in the restored database.
Custom declarations remain protected when disabled or assigned to another organization. Names are
compared conservatively even across different endpoints; choose a distinct scratch name if this
rejects an otherwise unrelated destination.

The restore principal also needs `s3:GetBucketObjectLockConfiguration` on the scratch bucket. The
guard permits only a recognized response that Object Lock is not configured. An enabled lock,
unreadable or missing bucket, unsupported operation, ambiguous metadata, or unreadable/malformed
restored checkpoint catalog fails verification before copying. A legacy manifest without table
counts still works when its restored database has the required checkpoint-sink schema; a missing
sink table is not treated as an empty set of protected roles.

A rejected target is never eligible for object cleanup. After an allowed copy starts, cleanup checks
the destination again before listing or deleting this run's prefix. A failed safety check can leave
scratch objects for operator investigation. Discard reads protected roles and source locations from
the standing database before dropping it; if those reads or safety checks fail, database removal
retains its existing behavior while object cleanup is skipped. No retention bypass is used.

Custom-role discovery describes the archive's snapshot, while Settings and Object Lock metadata
describe the present destination. It cannot discover a newly declared custom sink that exists only
in the live database and is also incorrectly provisioned without Object Lock. Provision and keep
scratch separate from every live source/checkpoint destination. This check does not prevent a
privileged operator from changing the store between validation and copying, and does not establish
source-independent recovery.

## Restore integrity verification (not a cutover procedure)

`./scripts/easysynq restore <archive.tar.enc> --confirm` decrypts when needed and verifies the archive,
restores PG into a fresh scratch DATABASE, and copies source-store blobs into a unique prefix in the
configured shared scratch bucket
(the locked vault is **read**, never written). It then runs the triad, the **checkpoint-not-ahead**
tamper check, and a **restored-chain re-verify**. The target remains standing only for inspection or
explicit discard. It exits:

* **0 (PASS)** — integrity verification passed. The copied scratch bytes re-hash correctly, and the
  restored database's stored locators resolve against the **currently configured source object
  store**. This is source-store-dependent and **not cutover-ready**.
* **3 (FLAGGED)** — the audit checkpoint is **ahead** of the restored head (the backup is older than
  the last anchored checkpoint, a deliberate point-in-time target, **or** a truncated/tampered tail).
  Re-run with `--audit-checkpoint-ack` to proceed; the acknowledgement is **audited**
  (`RESTORE_CHECKPOINT_ACK`). Never auto-proceeds.
* **1 (FAIL)** — archive/restore/triad/chain failure; the scratch target is torn down.

### Production recovery/cutover is not currently supported

**Do not cut over to today's CLI scratch target.** Scratch copies are flattened under a verification
prefix, while restored `blob` rows retain their original object keys and role-bucket locators. The
CLI neither maps those locators to fresh role buckets nor switches the matching database and object
store configuration. A PASS can therefore coexist with a scratch target that cannot serve the
restored application's reads.

A future supported cutover must satisfy all of these requirements before any operator procedure is
published:

* restore from a self-contained generation whose object bytes remain available when source-store
  reads are denied;
* create fresh object-lock-enabled document and record roles plus a fresh plain rendition role;
* preserve each stored object key, reject unknown legacy bucket roles, and map restored bucket
  fields to their matching fresh roles only after every copy succeeds;
* atomically switch the restored database and all matching object-store settings, including the
  application DSNs and `KEYCLOAK_DB_NAME`, so application and identity state move together;
* recover the matching identity/config state. A PostgreSQL-backed generation must repair ownership
  of restored `keycloak` schema objects. A legacy generation must prove
  `legs.realm_export = "present"`, stage it through `<compose-project>_keycloakimport`, and complete
  that import before the first Keycloak start. A future recovery tool must automate and validate
  these branches; they are not manual instructions for today's archive;
* boot with external access still closed and prove document, record, sealed-pack, and rendition
  reads from the fresh target; and
* rebuild the filesystem mirror only after that closed-service verification passes.

This is a requirements list, **not executable recovery instructions**. No current command performs
those steps. Preserve the source object store and keep the service closed during a recovery event.
Discard an unused verification target with
`./scripts/easysynq restore --discard <scratch_db>`.

## Upgrade

`./scripts/easysynq upgrade --confirm` enforces **pre-backup → `alembic upgrade head` → readiness
health-gate** and audits `UPGRADE_STARTED`/`UPGRADE_COMPLETED`/`UPGRADE_FAILED`. Online Alembic
connections have a fixed **five-second timeout per lock acquisition** (R74), including boot,
upgrade and downgrade commands. A timeout stops the command, rolls back its active transactional
segment and prevents readiness/completion. Earlier migration segments committed by existing
autocommit blocks remain applied; the command does not automatically retry, downgrade or restore.

The exact pre-upgrade archive pointer is retained in the failure result and audit row, but that
archive is **non-self-contained**: it has the database dump and blob manifest, not object bytes.
It is not a disaster safety net and must not be followed by an archive-only restore/cutover attempt.
If migration or readiness fails, keep the service closed and preserve the source object store
while the failure and any earlier committed migration state are investigated. Production upgrade
eligibility remains blocked until a self-contained recovery generation and source-independent,
role-preserving restore/cutover proof pass. The command's current mechanics do not establish that
eligibility.

The timeout applies only to online Alembic connections and preserves ordinary application
connection settings. Offline SQL generation is unchanged; executing generated SQL separately
does not acquire this protection from `env.py`.

### ⚠ Stop the writers first when a revision builds an index on `audit_event`

`./scripts/easysynq upgrade` runs on a **one-off worker while api/worker/beat stay up**, so a migration
that locks a hot table contends with live traffic. On fresh boot, the Compose `migrate` one-shot
completes before those services start.

`0075_audit_scope_ref_index` builds an index on `audit_event`, taking `ShareLock` on the parent
and every partition: **reads keep serving, writes block.** Because nearly every mutating request
writes an `audit_event` in the same transaction, writers can queue behind the index build. The
five-second limit aborts a migration that cannot acquire a lock. It does not limit index-build
duration or release locks after they have been acquired, so a maintenance window remains
necessary. Budget roughly **50 MB of index per million audit rows**, with build time to match.

```bash
if docker compose -f infra/compose/compose.yml stop api worker beat &&
  ./scripts/easysynq upgrade --confirm; then
  docker compose -f infra/compose/compose.yml start api worker beat
else
  echo "Stop or upgrade failed; keep services closed and investigate before proceeding." >&2
  false
fi
```

Check the size of what you are about to index first:

```bash
docker compose -f infra/compose/compose.yml exec -T postgres psql -U easysynq -d easysynq -c "SELECT count(*) FROM audit_event;"
```

`roll_partitions` / `ensure_partitions` can also block during a build; their existing best-effort
daily retry behavior is unchanged. Downgrading with `DROP INDEX` requires a stricter
`AccessExclusiveLock` that also blocks reads. The online timeout bounds its lock acquisition,
not the duration of subsequent work.
