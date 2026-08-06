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
* **GOVERNANCE** (default, D-7) keeps future role-preserving recovery design and the R27 dual-control
  destroy-under-legal-order escape hatch buildable.
* **COMPLIANCE** is a hardened opt-in that is **irreversible** — it constrains future recovery-target
  AND GDPR destroy-under-legal-order (immutable even to root). The setup wizard records the mode and
  warns; use GOVERNANCE unless a regulation mandates COMPLIANCE.

## Restore implication
**Never restore into the locked `documents`/`records` buckets.** `./scripts/easysynq restore` always targets the
plain non-WORM `restore-scratch` bucket (and the drill does too), but that is an
**integrity-verification target, not a cutover target**. Its copied layout is flattened and does not
match the restored database's role-bucket locators. A future supported cutover must use fresh,
role-preserving object-lock-enabled document/record targets and a fresh plain rendition target — see
[backup-restore.md](backup-restore.md). The off-host
audit-checkpoint anchor bucket (`audit-checkpoints`) is likewise object-lock-enabled, with **separate
credentials** from the vault root (D-8) so one operator can't rewrite both the chain and its anchor.

## Other buckets (non-WORM by design)
`staging` (browser presigned uploads), `import-staging` (import source bytes), `renditions`
(watermarked PDFs), and `restore-scratch` (drill/verification target) are non-WORM buckets — do not
enable object-lock on them. The two staging buckets are nevertheless **versioned** and must remain so:

```bash
mc version enable myminio/staging
mc version enable myminio/import-staging
mc version info myminio/staging
mc version info myminio/import-staging
```

Both `mc version info` commands must report `Enabled`; `/readyz` also fails its MinIO dependency when
either bucket is missing, inaccessible, suspended, or not exactly `Enabled`. There is deliberately no
blanket current/noncurrent expiry. Exact rejected uploads and scanner temporary versions are deleted
explicitly after durable evidence; valid abandoned scratch and long-running import reviews remain
visible storage debt until a reference-aware lifecycle policy exists.

## Browser PUT CORS and VersionId exposure

Owner-approved Task 2 Community MinIO waiver: the pinned Community image rejects the per-bucket CORS
configuration needed here, so Compose sets exactly
`MINIO_API_CORS_ALLOW_ORIGIN=<PUBLIC_BASE_URL>`. Community MinIO applies that response policy across
the entire MinIO API origin, not only `staging`. This is a browser response-visibility control, **not**
an access boundary: S3 IAM and the signature, method, key, and expiry on each presigned request remain
the authorization boundary. CORS does not grant listing, reading, writing, or broader bucket access.

Operational expectations:

- a preflight from the exact `PUBLIC_BASE_URL` origin for the signed `PUT` is allowed;
- a preflight from any other origin is not allowed by the response policy;
- the successful browser PUT response exposes `x-amz-version-id`, and the SPA refuses to finalize a
  non-dedup upload when that header is absent; and
- `import-staging` is worker-only, but shares the global MinIO-origin response policy because the
  pinned Community image cannot scope it per bucket.

Validate those expectations at the browser-facing MinIO origin after provisioning. Keep the
application origin exact (no wildcard, comma-list, path, query, fragment, or credentials). See
[upload-identity-rollback.md](upload-identity-rollback.md) before rolling back application code: bucket
versioning and this CORS configuration remain in place.
