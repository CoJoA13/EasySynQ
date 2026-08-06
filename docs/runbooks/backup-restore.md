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

* `db.dump` (`pg_dump -Fc`, including Keycloak's durable `keycloak` schema) + `manifest.json` (the
  **blob inventory**: sha256/size/bucket/object-key metadata, + per-table row counts) + the additional
  **Keycloak realm export** + a **config snapshot** + the latest signed audit checkpoint;
* the whole archive is **AES-256-GCM encrypted** to `…tar.enc` with `BACKUP_ENCRYPTION_KEY` (a
  stolen archive is useless without the key). If a Keycloak outage prevents the realm export, the
  backup still succeeds with `legs.realm_export = "absent"` (logged) — it never blocks.

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
object store into the non-WORM `restore-scratch` bucket. It then runs the integrity triad
(copied-blob SHA-256 re-hash · stored-locator SHA-256 re-hash against the currently configured object
store · per-table row-count parity · `document_version→blob` FK check) and tears the scratch namespace
down. Only a **PASS** satisfies the setup gate. This is a source-dependent integrity check, not proof
of recovery after source-store loss. "Configured but unverified" does not count.

## Restore integrity verification (not a cutover procedure)

`./scripts/easysynq restore <archive.tar.enc> --confirm` decrypts + verifies the archive, restores PG
into a fresh scratch DATABASE, and copies source-store blobs into a fresh non-WORM scratch bucket
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

`./scripts/easysynq upgrade --confirm` enforces **pre-backup → `alembic upgrade head` → readiness health-gate**
and audits `UPGRADE_STARTED`/`UPGRADE_COMPLETED`/`UPGRADE_FAILED`. A failed migration auto-rolls back
its own transaction. The exact pre-upgrade archive pointer is retained in the failure result/audit
row, but that archive is **non-self-contained**: it has the database dump and blob manifest, not
object bytes. It is not a disaster safety net and must not be followed by an archive-only
restore/cutover attempt.

If migration or readiness fails, keep the service closed and preserve the source object store while
the failure is investigated. Production upgrade eligibility remains blocked until a self-contained
recovery generation and source-independent, role-preserving restore/cutover proof pass. The command's
current mechanics do not establish that eligibility.

### ⚠ Stop the writers first when a revision builds an index on `audit_event`

`./scripts/easysynq upgrade` runs on a **one-off worker while api/worker/beat stay up**, so any migration that
locks a hot table contends with live traffic. This is unlike a fresh boot, where the compose
`migrate` one-shot completes *before* those services start.

`0075_audit_scope_ref_index` is the first such revision. Building an index on `audit_event` takes
`ShareLock` on the parent and every partition: **reads keep serving, writes block.** Because nearly
every mutating request writes an `audit_event` in the same transaction, the whole write path convoys
behind it — and since no `lock_timeout` is configured and `migrations/env.py` wraps the run in ONE
transaction, a build queued behind an open writer holds everything until the entire `upgrade head`
commits. Budget roughly **50 MB of index per million audit rows**, with build time to match.

```bash
docker compose -f infra/compose/compose.yml stop api worker beat
./scripts/easysynq upgrade --confirm
docker compose -f infra/compose/compose.yml start api worker beat
```

Check the size of what you are about to index first — on a small install this is seconds and the
window is academic:

```bash
docker compose -f infra/compose/compose.yml exec -T postgres psql -U easysynq -d easysynq -c "SELECT count(*) FROM audit_event;"
```

`roll_partitions` / `ensure_partitions` also block during the build, but both are best-effort with a
daily retry, so they self-heal. Downgrading (`DROP INDEX`) takes a stricter `AccessExclusiveLock`
that blocks reads too, but it is catalog-only and effectively instant.
