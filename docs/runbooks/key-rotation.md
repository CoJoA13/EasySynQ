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
