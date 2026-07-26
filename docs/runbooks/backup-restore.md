# Backup, restore-test drill, restore & upgrade

Only **PostgreSQL + MinIO** are backup-critical; OpenSearch + the filesystem mirror are regenerable
(D-6 / R11). The backup/restore/upgrade CLIs run on the **worker** (it carries `postgresql-client`
+ the OWNER `DATABASE_URL_SYNC`). MVP scope = nightly `pg_dump` + WORM-aware restore-to-verified-
target; **continuous WAL/PITR, retention pruning, and S3 destinations are v1.x** (D-6).

## The durable backup archive

`easysynq backup run` (and the nightly Beat job `easysynq.backup.run`) writes one timestamped,
checksum-verified archive per configured policy to `BACKUP_PATH` (or the policy's destination):

* `db.dump` (`pg_dump -Fc`) + `manifest.json` (the **blob snapshot**: sha256/size/bucket per
  position, + per-table row counts) + the **Keycloak realm export** + a **config snapshot** + the
  latest signed audit checkpoint;
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

`easysynq backup restore-test` runs a real backup → restore into a throwaway scratch DATABASE →
copies the manifested blobs into the non-WORM `restore-scratch` bucket → runs the integrity triad
(blob SHA-256 re-hash · per-table row-count parity · `document_version→blob` FK check) and tears the
scratch namespace down. Only a **PASS** satisfies the setup gate. "Configured but unverified" does
not count.

## Live restore (WORM-aware, to a VERIFIED TARGET)

`easysynq restore <archive.tar.enc> --confirm` decrypts + verifies the archive, restores PG into a
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
(automated in-place live cutover is a tracked hardening item). To cut over: stop the app/worker,
repoint `DATABASE_URL`/`DATABASE_URL_SYNC` at the restored DB (or `pg_dump`/`createdb` it into the
production name), repoint MinIO at (or copy the blobs into) a fresh **object-lock-enabled** vault
bucket — **never the old locked one** — then run `easysynq mirror rebuild` + a reindex, and restart.
Discard an unused target with `easysynq restore --discard <scratch_db>`.

## Upgrade

`easysynq upgrade --confirm` enforces **pre-backup → `alembic upgrade head` → readiness health-gate**
and audits `UPGRADE_STARTED`/`UPGRADE_COMPLETED`/`UPGRADE_FAILED`. The pre-backup archive is the
disaster safety net (named in `UPGRADE_FAILED.after`): a failed migration auto-rolls-back its own
transaction; if the health-gate fails, recover with `easysynq restore <pre-backup>` + cutover.
