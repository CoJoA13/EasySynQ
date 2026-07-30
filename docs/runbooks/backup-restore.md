# Backup, restore-test drill, restore & upgrade

Only **PostgreSQL + MinIO** are backup-critical; the filesystem mirror is regenerable
(D-6 / R11). OpenSearch is also designed as a derived store, but is not deployed by the shipped
S/M profiles. The backup/restore/upgrade CLIs run on the **worker** (it carries `postgresql-client`
+ the OWNER `DATABASE_URL_SYNC`). MVP scope = nightly `pg_dump` + WORM-aware restore-to-verified-
target; **continuous WAL/PITR, retention pruning, and S3 destinations are v1.x** (D-6).

## The durable backup archive

`./scripts/easysynq backup run` (and the nightly Beat job `easysynq.backup.run`) writes one timestamped,
checksum-verified archive per configured policy to `BACKUP_PATH` (or the policy's destination):

* `db.dump` (`pg_dump -Fc`, including Keycloak's durable `keycloak` schema) + `manifest.json` (the
  **blob snapshot**: sha256/size/bucket per position, + per-table row counts) + the additional
  **Keycloak realm export** + a **config snapshot** + the latest signed audit checkpoint;
* the whole archive is **AES-256-GCM encrypted** to `…tar.enc` with `BACKUP_ENCRYPTION_KEY` (a
  stolen archive is useless without the key). If a Keycloak outage prevents the realm export, the
  backup still succeeds with `legs.realm_export = "absent"` (logged) — it never blocks.

> **Key custody (critical):** `BACKUP_ENCRYPTION_KEY` lives ONLY in the `0600` `.env` / a Docker
> secret — never in the archive. **Lose it and every `.tar.enc` is unrecoverable.** Back it up
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

`./scripts/easysynq backup restore-test` runs a real backup → restore into a throwaway scratch DATABASE →
copies the manifested blobs into the non-WORM `restore-scratch` bucket → runs the integrity triad
(blob SHA-256 re-hash · per-table row-count parity · `document_version→blob` FK check) and tears the
scratch namespace down. Only a **PASS** satisfies the setup gate. "Configured but unverified" does
not count.

## Live restore (WORM-aware, to a VERIFIED TARGET)

`./scripts/easysynq restore <archive.tar.enc> --confirm` decrypts + verifies the archive, restores PG into a
fresh scratch DATABASE, copies blobs into the fresh non-WORM bucket (the locked vault is **read**,
never written), runs the triad, the **checkpoint-not-ahead** tamper check, and a **restored-chain
re-verify** — then **leaves the verified target standing** for you to cut over to. It exits:

* **0 (PASS)** — a verified, ready-to-cutover target (`db=restore_easysynq_… bucket=restore-scratch`).
* **3 (FLAGGED)** — the audit checkpoint is **ahead** of the restored head (the backup is older than
  the last anchored checkpoint, a deliberate point-in-time target, **or** a truncated/tampered tail).
  Re-run with `--audit-checkpoint-ack` to proceed; the acknowledgement is **audited**
  (`RESTORE_CHECKPOINT_ACK`). Never auto-proceeds.
* **1 (FAIL)** — archive/restore/triad/chain failure; the scratch target is torn down.

### Cut over (manual operator step)
The MVP produces a verified target; **cutover is a documented operator action, not automated**
(automated in-place live cutover is a tracked hardening item). To cut over:

1. Stop `api`, `worker`, `beat`, and `keycloak`.
2. Repoint `DATABASE_URL`, `DATABASE_URL_SYNC`, and `AUDIT_LINKER_DATABASE_URL` at the restored
   database (or `pg_dump`/`createdb` it into the production name). Set `KEYCLOAK_DB_NAME` to that
   same database name so the restored identity/client state moves with the application.
3. **Choose the Keycloak recovery path before starting anything:**

   - A Batch-13-or-newer target contains the `keycloak` PostgreSQL schema. No realm re-import is
     needed; `KEYCLOAK_DB_NAME` selects that durable identity state.
   - A pre-Batch-13 target has no `keycloak` schema because its identities lived in H2. For this
     case, `KEYCLOAK_DB_NAME` alone does **not** recover identities. Confirm the archive manifest
     records `legs.realm_export = "present"`, decrypt/extract its `realm.json` leg into a controlled
     `0600` temporary file, and stage it as `easysynq-realm.json` in this installation's
     project-scoped `<compose-project>_keycloakimport` volume **before the first Keycloak start**.
     This is the same offline import path used by `scripts/migrate-keycloak-h2.sh`; do not let
     `keycloak-init` substitute the committed stock seed. If the realm leg is absent, stop: that
     archive cannot recover the legacy Keycloak users/credentials, so use the separately retained
     identity backup instead.

4. Repoint MinIO at (or copy the blobs into) a fresh **object-lock-enabled** vault bucket — **never
   the old locked one**.
5. Start the stack. For a PostgreSQL-backed archive, the `keycloak-init` one-shot transfers
   restored `keycloak` schema objects from the `pg_restore --no-owner` role back to
   `easysynq_keycloak`. For a legacy archive, it creates the empty schema while preserving the
   staged realm for Keycloak's offline import. Verify a restored account and the `easysynq-web`
   client before reopening access.
6. Run `./scripts/easysynq mirror rebuild`. Shipped S/M search uses PostgreSQL FTS and needs no separate
   reindex operation.

Discard an unused target with `./scripts/easysynq restore --discard <scratch_db>`.

## Upgrade

`./scripts/easysynq upgrade --confirm` enforces **pre-backup → `alembic upgrade head` → readiness health-gate**
and audits `UPGRADE_STARTED`/`UPGRADE_COMPLETED`/`UPGRADE_FAILED`. The pre-backup archive is the
disaster safety net (named in `UPGRADE_FAILED.after`): a failed migration auto-rolls-back its own
transaction; if the health-gate fails, recover with `./scripts/easysynq restore <pre-backup>` + cutover.

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
