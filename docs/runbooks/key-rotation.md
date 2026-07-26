# Key rotation

All secrets live in the `0600` `.env` / Docker secrets — never in images, VCS, or the backup
archive. Each is rotatable; rotation is an audited admin action. Back up each key out-of-band with
the same custody as the host disk-encryption key.

| Key | Env / path | Rotation |
|---|---|---|
| **App master KEK** | `APP_MASTER_KEK` (0600 .env) | Re-wrap the column DEKs with the new KEK (envelope: no bulk re-encryption of data). MVP has no plaintext DB secret columns (federation lives in Keycloak), so this is a forward-seam — rotate by updating the value and restarting. |
| **Backup key** | `BACKUP_ENCRYPTION_KEY` (0600 .env) | **Separate custody from the KEK.** New `…tar.enc` archives use the new key. **Keep the OLD key as long as any archive sealed with it must remain restorable** (the manifest records `encryption_key_ref`). Losing it makes those archives unrecoverable. |
| **Audit-checkpoint signing key** | `AUDIT_CHECKPOINT_SIGNING_KEY_PATH` (Ed25519, beat-only) | New checkpoints sign with the new key. Keep the old public key to verify pre-rotation checkpoints. |
| **Verify-token signing key** | `VERIFY_TOKEN_SIGNING_KEY_PATH` (Ed25519, shared api↔worker via the `secrets` volume) | After rotating, force a full mirror re-render so renditions carry a footer token signed with the new key: `easysynq mirror rebuild`. |
| **Off-host sink credential** | `AUDIT_SINK_ACCESS_KEY` / `AUDIT_SINK_SECRET_KEY` | Held in **separate custody** from the KEK/backup key (D-8); rotate at the sink + in `.env`. |
| **Keycloak admin / client secret** | `KEYCLOAK_ADMIN_PASSWORD`, client secrets | Rotate in Keycloak; update `.env` so the worker's realm-export admin login keeps working. JWKS key rotation is automatic (the API re-fetches on `kid` change). |
| **DB / MinIO root** | `*_PASSWORD`, `S3_*` | Rotate at the service + in `.env`; restart the stack. |

**After any rotation:** restart the affected containers, confirm `/readyz` is green, and run
`easysynq backup run` so the next archive is sealed with the current key set. Secrets are redacted
from logs / audit `before`/`after` / error responses by the allowlist serializer.

## Declaring the off-host witness (`integrity.alarm`)

The nightly `easysynq.audit.verify_chain` job raises **`integrity.alarm`** to System Administrators
(in-app + email, CRITICAL so it pierces quiet hours) and to the out-of-band `OPS_ALERT_CHANNELS`
(see [backup-restore.md](backup-restore.md)) when it detects a chain break, a checkpoint that fails
signature verification, or an off-host witness that fails its independent read-back.

Two settings govern the *absence* of a witness, which is otherwise unreportable:

| Setting | Default | What it does |
|---|---|---|
| `AUDIT_WITNESS_REQUIRED` | `false` | Declares that this install **requires** an off-host witness. With it true, a nightly verify that finds no enabled off-host `audit_checkpoint_sink` raises `integrity.alarm` instead of quietly deferring to the R13 soft-gate. |
| `AUDIT_WITNESS_GRACE_HOURS` | `24` | How long a sink may be enabled without ever anchoring before it is treated as a dead witness. Inside the window a fresh sink is benign; past it, it alarms. |

> **Why `AUDIT_WITNESS_REQUIRED` lives in the environment and not the database.** The row a
> privileged DB owner would delete in order to go dark *is* `audit_checkpoint_sink`. An in-DB "a
> witness is required" flag would be deleted alongside the very thing it guards, and the nightly
> verify would fall silent exactly when it should shout. Asserting the requirement from outside the
> database is the whole point — keep it in the `0600` `.env` with the same custody as the other keys.

**Turn it on only once a witness is actually configured**, or every night alarms. After enabling a
new sink, confirm it anchors within `AUDIT_WITNESS_GRACE_HOURS` (`easysynq audit verify-offhost`).

⚠ `audit_checkpoint_sink.enabled_at` — the grace-window anchor — is set at **row creation**. v1 has
no in-app create/enable surface (provisioning is a direct operator INSERT), so if you later toggle a
sink `enabled` false→true, bump `enabled_at` too or the window is measured from creation.
