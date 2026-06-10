# Blob integrity verify (D1) — operator runbook

**What it is.** A daily Beat task (`easysynq.blob.verify`, `BLOB_VERIFY_INTERVAL_SECONDS` default
86400) re-hashes the `BLOB_VERIFY_SAMPLE_SIZE` (default 500) least-recently-verified vault blobs
against their content-addressed identity (`blob.sha256`). Rotation covers the FULL set every
⌈N/sample⌉ days. `blob.verified_at` is stamped on a passing re-hash ONLY — a failing blob stays at
the rotation head and **re-alarms on every run until you resolve it**. Status:
`GET /admin/drift/status` (`drift.read`).

**On a `BLOB_INTEGRITY_FAILED` audit event** (`after.classification`):

- `HASH_MISMATCH` — the stored bytes no longer hash to the blob's identity (bit-rot or
  storage-layer tamper; WORM object-lock blocks legitimate overwrite, so treat as a security
  signal). `OBJECT_MISSING` — the object is GONE (storage tamper, or a blob row whose bytes were
  destroyed outside the app — a broken blob-row-iff-bytes invariant; either way alarm-worthy).
  `READ_ERROR` — an object-scoped read failure (e.g. ACL damage); transient ones self-clear on the
  next run.
- **Do NOT touch the mirror or the bucket in place.** Blobs are WORM-locked; there is no
  auto-correction. Restore the affected object(s) from a verified backup to a fresh/verified
  target per the backup-restore runbook (R37 — never mutate the locked bucket in place).
- After the restore, run `MSYS_NO_PATHCONV=1 docker compose --env-file .env -f
  infra/compose/compose.yml exec worker python -m easysynq_api.cli.blob verify --full` and
  confirm the re-hash passes (the alarm clears: the blob is stamped and leaves the rotation head).
- A `FAILED` scan status (not a finding) means infrastructure trouble (MinIO/PG unreachable) — the
  scan aborts honestly instead of minting noise findings; check `/readyz` and the worker logs.

**The D4 superseded-copies report** (`GET /admin/drift/superseded-copies`): the recall list —
which superseded revisions still have exported/printed copies in circulation, with the current
effective revision to quote. A reported copy is resolved per-copy via its printed verify token
(the public `/verify` page); the count never decrements (a paper copy cannot be un-printed).
