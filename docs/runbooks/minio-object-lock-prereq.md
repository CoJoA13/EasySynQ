# MinIO object-lock (WORM) prerequisite

The controlled vault's integrity rests on **object-lock**: the `documents` **and** `records` buckets
must each be created **object-lock-enabled at creation time** — it **cannot be retro-added** (R37).
Setup gate **G-B** verifies the **`documents`** bucket (the WORM probe — `verify_storage()` → `worm_probe()` defaults to it) and refuses to finalize otherwise. The **`records`** bucket is **not** auto-probed at setup, so provision it object-lock-enabled here (below); its WORM guarantee is enforced when record evidence is first promoted (a non-locked `records` bucket fails then with `worm_required`).

## Provision the vault buckets (object-lock + GOVERNANCE)
The Compose `minio-init` provisions these for the dev stack. For a production/external MinIO/S3:
```bash
# object lock MUST be enabled when the bucket is created
mc mb --with-lock myminio/documents
mc mb --with-lock myminio/records
mc retention set --default GOVERNANCE 30d myminio/documents   # tune the retention to your policy
mc retention set --default GOVERNANCE 30d myminio/records     # same default for the records bucket
```
* **GOVERNANCE** (default, D-7) keeps the R37 fresh-bucket restore and the R27 dual-control
  destroy-under-legal-order escape hatch buildable.
* **COMPLIANCE** is a hardened opt-in that is **irreversible** — it forecloses fresh-bucket restore
  AND GDPR destroy-under-legal-order (immutable even to root). The setup wizard records the mode and
  warns; use GOVERNANCE unless a regulation mandates COMPLIANCE.

## Restore implication
**Never restore into the locked `documents`/`records` buckets.** `easysynq restore` always targets the
plain non-WORM `restore-scratch` bucket (and the drill does too). Cutover lands the verified blobs into
**fresh** object-lock-enabled buckets — see [backup-restore.md](backup-restore.md). The off-host
audit-checkpoint anchor bucket (`audit-checkpoints`) is likewise object-lock-enabled, with **separate
credentials** from the vault root (D-8) so one operator can't rewrite both the chain and its anchor.

## Other buckets (non-WORM by design)
`staging` (presigned uploads), `renditions` (watermarked PDFs), `restore-scratch` (drill/restore
target) are plain buckets — do not enable object-lock on them.
