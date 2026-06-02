# EasySynQ — Project Context

> Read this first. It orients a new session. The **authoritative** detail lives in `docs/` —
> start with `docs/00-overview.md` (front door) and `docs/decisions-register.md` (the binding decisions).

## What this is

EasySynQ is a **self-hosted, browser-based ISO 9001:2015 Quality Management System (QMS)**. Its
core idea is to *invert authority* so document drift becomes an **enforced invariant** rather than a
discipline problem: a managed **controlled vault** (PostgreSQL + MinIO WORM) owns the master copy of
every controlled document and record, and the on-disk filesystem is only a **read-only, organized
mirror** regenerated from Released versions. It is built to prevent document drift, track revision
changes, manage documented evidence/records, and keep an organization audit-ready by default. The
UI/UX flows the way ISO 9001 flows (clause spine / process map / PDCA) and must stay calm, modern,
and progressively disclosed — never overwhelming.

## Current status (as of 2026-06-02)

**Spec complete + MVP build underway** (foundation-first, against the approved plan). The design is locked;
we are now writing code.

- **Specification** in `docs/` (00–17 + `decisions-register.md`) — complete, adversarially audited, reconciled
  (Register R1–R37 back-propagated). The Register is authoritative.
- **Approved implementation plan:** `docs/18-mvp-implementation-plan.md` — repo/tooling, Compose dev stack, the
  Alembic schema from doc 14, the FastAPI/OpenAPI surface from doc 15, and **11 ordered vertical slices S0–S11**,
  each mapped to the six MVP acceptance proofs. §1 records the canon corrections an adversarial pass forced
  (two state enums `version_state`/`current_state`; `audit_event` identity-gap is the tamper signal — **no `seq`
  col**; `framework_id` only on `documented_information`/`clause`/`clause_mapping`/`scope`; doc-07 permission keys
  verbatim; doc-15 flat action sub-resources + approval via `POST /tasks/{id}/decision`).
- **HTML UI mockup** at `mockup/easysynq-mockup.html` (owner-approved).

**Code lives on GitHub:** https://github.com/CoJoA13/EasySynQ (`main`, protected — PR + green CI required;
admin-bypass on for the solo owner). **Shipped so far (each merged via PR, all CI green, validated on the real
Docker stack):**
- **S0 — walking skeleton** ✅ — Compose stack, `/healthz`+`/readyz`, reversible Alembic baseline, OpenAPI→client pipeline
- **S1 — AuthN** ✅ — Keycloak OIDC/PKCE, RS256 JWT validation vs JWKS, `app_user` + JIT provisioning, `GET /me`, `/auth/config`
- **S2 — AuthZ** ✅ — reduced doc-14 RBAC/ABAC schema (`permission`/`role`/`role_grant`/`role_assignment`/`scope`/
  `permission_override`/`sod_constraint`), the closed doc-07 96-key catalog + 8 seeded roles, a pure deny-wins **PDP**
  (register R3), a FastAPI **PEP** that audits allow+deny, the two-tier grant guard (422, R35), and `/permissions` +
  `/roles` + `/users/{id}/{roles,overrides,effective-permissions}`. Proofs: `test_per_user_deny_beats_role_allow` [AC#3],
  `test_admin_system_star_denied_content` [AC#4], `test_two_tier_violation`, `test_specificity_allow_only`.

- **S3 — Vault** ✅ — the controlled-vault spine (D2): `framework`/`document_type`/`blob`/`documented_information`/
  `document_version`/`working_draft`/`numbering_counter` schema; atomic `{TYPE}-{AREA}-{SEQ}` identifiers; the
  check-out (Redis 8h lock + heartbeat) → presigned MinIO upload (staging) → server-side WORM copy → immutable
  `document_version` check-in cycle; content-addressed dedup; INV-3; break-lock (scratch preserved); 13 `/documents`
  endpoints; PEP async resource-scope resolvers. Proofs: re-checkin-identical-bytes=no-new-version, INV-3 422,
  double-checkout 409 lock_conflict, break-lock-preserves-scratch + LOCK_BROKEN, WORM-before-version, presigned I/O.

- **S4 — Lifecycle [AC#1]** ✅ — the document FSM + the single-Effective invariant. Pure FSM (`domain/vault/lifecycle.py`,
  doc-state-keyed) for T1–T4/T6/T7/T9–T12 (T5/T8 deferred); 6 named POST actions (submit-review/approve/request-changes/
  release/start-revision/obsolete, doc-07 keys, never PATCH status=); the **atomic release cutover** in a dedicated
  SERIALIZABLE session (`SELECT … FOR UPDATE` + flush-prior-before-promote + the INV-1 partial unique index → 409 on the
  concurrent loser); `0007_lifecycle` wires the lifecycle FKs + the INV-1 and R25 partial indexes (enum-cast predicates,
  `alembic check` clean); a minimal Celery **Beat sweep** activates future-dated releases. Seams kept clean: `signature_event`
  emission + SoD → **S5** (no-op `SignatureEventSink` wired); the ≥1 `clause_mapping` submit gate → **S9** (`# S9:` seam);
  `audit_event` writer → **S6**. Proofs: `test_release_supersedes` [AC#1a], `test_two_effective_impossible` [AC#1b, real
  concurrent connections], + the pure-FSM unit suite, illegal-transition 409, future-dated+Beat, start-revision, obsolete, R25 singleton.

- **S5 — Approval + SoD [AC#1 re-driven]** ✅ — the task/decision approval workflow + the deny-wins SoD gate.
  `0008` builds the minimal workflow cluster (`workflow_definition`/`workflow_stage`/`workflow_instance`/`task`/
  `task_outcome`), the append-only `signature_event` (polymorphic `signed_object_type`/`signed_object_id` per doc 14 §8,
  Part-11 cols NULL), and the `record` shared-PK subtype; `0009` seeds the `document_approval` workflow + the SoD-1/SoD-2
  constraints; `system_config.allow_approver_release`. **`POST /tasks/{id}/decision`** is the canonical approval/review
  trigger — writes `task_outcome` + `signature_event` + audit in ONE txn (`SELECT … FOR UPDATE` + `UNIQUE(task_outcome.
  task_id)` + `Idempotency-Key` replay); `submit-review` instantiates the instance + APPROVE task; the **direct
  `/approve`+`/request-changes` endpoints were removed** (tasks-canonical, C7). Signature emission on approve (decision txn),
  release (manual + Beat, inside the SERIALIZABLE cutover; nullable system signer) and obsolete. The **SoD gate** in the PDP
  `_evaluate_sod` (deny-overlay on a would-be ALLOW): SoD-1 (author≠approver, non-overridable) + SoD-2 (author never
  self-releases; approver-release behind `allow_approver_release`) read the immutable `document_version.author_user_id` +
  prior approval signatures → **403 `sod_violation`** + `conflicting_duty`; SoD-3 = the Internal Auditor role's structural
  exclusion (RBAC). Reconciliations: 403 over doc-18 §7's 409 (doc 15 §8.8 governs); polymorphic `signed_object_id` over
  doc-18 §15.4's typed FKs (doc 14 §8); `record` brought forward from S21 per owner scope. Proofs: SoD-1/2/3, one-txn +
  rollback atomicity, idempotency, My-Tasks, `test_release_supersedes` [AC#1a] + `test_two_effective_impossible` [AC#1b]
  re-driven multi-actor through the task flow.

- **S6 — Audit [AC#6]** ✅ — the append-only, hash-chained, tamper-evident trail. `0010` introduces **DB role
  separation** (the decisive AC#6a foundation: the app/worker/beat run as the non-owner `easysynq_app` role with
  INSERT/SELECT-only on `audit_event`+`signature_event` — so the REVOKE actually bites; the migrate service stays the
  owner; a dedicated `easysynq_linker` role holds the only `UPDATE(prev_hash,row_hash,chained_at)` grant) + the
  monthly RANGE-partitioned `audit_event` (`bigint GENERATED ALWAYS AS IDENTITY`, PK `(id,occurred_at)`, BRIN+btree,
  reserved `signature_event_id` FK; a SECURITY-DEFINER partition factory the non-owner Beat calls) + `audit_checkpoint`/
  `audit_checkpoint_sink`. The **in-transaction audit writer** swaps the logging sinks for `DbVaultAuditSink`
  (`record(session,event)`, mirrors the signature sink) and `DbAuthzAuditSink` (own short txn; persists denies +
  state-changes, allows log-only per §4.1) — every vault/lifecycle emit moved **pre-commit** (the cutover RELEASED/
  SUPERSEDED rows now roll back with a race loser — no phantom). `canonical_serialize` v1 is **frozen** (length-prefixed
  TLV over the doc-12 §4.3 fields, RFC-8785 JCS for jsonb, 32-zero genesis) + a committed golden vector (D-4). The
  decoupled chain-linker (`easysynq_linker` DSN, `pg_try_advisory_lock`, bounded-lag alarm, R12), `verify-chain`
  (first-broken-link detection), the signed off-host `worm_bucket` checkpoint anchor + the honest `tamper_evidence_attested`
  soft-gate (false on a same-host sink — R13), and Beat tasks (link ~30s, verify nightly, anchor ~15m, roll-partitions
  daily) + `easysynq audit {ensure-partitions,verify-chain}` CLI. Read-only `/audit-events` API (list/detail/per-document/
  verify-chain/status, `system.audit_log.read`, no write verbs). Deferred with seams: Keycloak SPI, `/audit-events/export`
  (D-9), content-access auditing. Reconciliations back-propagated (doc 15 §8.13 perm key, doc 12 §4.2 extensible `event_type`,
  doc 14 §12 D-8 credential). Proofs: `test_ac6a_*` (every gated step → a row; app-role UPDATE/DELETE on `audit_event`+
  `signature_event` rejected with SQLSTATE 42501, incl. a partitioned row; no write verbs), `test_ac6b_*` (linker chains +
  is idempotent; verify matches; a tampered row is the first broken link; checkpoint push + soft-gate), golden vector.

- **S7 — Mirror [AC#2]** ✅ — the read-only, Effective-only filesystem mirror (D2: authority flows vault→mirror).
  A deliberately minimal, **zero-migration** slice (the sync only SELECTs `document_version`/`blob` + writes the
  filesystem; `0010_audit` stays head). `services/vault/mirror.py` enumerates Effective versions (gate on
  `version_state`, not `current_state`), pulls **source bytes** via a new `storage.fetch_bytes` (worker server-side
  GET; the api still only presigns), and lays out a **flat** tree `current/{identifier}_{revision_label}/` (source
  file + `metadata.json` + `CHANGELOG.md`) + top-level `INDEX.md` + `_meta/manifest.json` (generated artifact only —
  no scan/diff). The **atomic swap is symlink-repoint**: build a fresh `.builds/<uuid>/`, then `os.replace` a temp
  symlink onto `current` (rename-over-symlink is atomic on one fs) — sidesteps the `os.replace`-onto-non-empty-dir
  failure that would break AC#2's second sync. Triggers: a post-commit `MirrorEnqueueSink` from release/release_due/
  obsolete (never inside the SERIALIZABLE cutover — the race loser must not enqueue; best-effort + nightly Beat
  backstop), the `easysynq.mirror.sync` Beat job (daily), and `python -m easysynq_api.cli.mirror sync` (under
  `LOCK_MIRROR_SYNC`). Compose: the api mounts the `mirror` volume **`:ro`** (R11 contract's missing half; worker
  stays rw; Caddy must NOT file_server it). **Rendering deferred to S7b** (owner decision): a no-op `RenderSink`
  (`render.py`) so the mirror writes source bytes + `render_status:"pending"` — *not* R26's `no_controlled_rendition`
  (reserved for genuinely non-renderable formats). Layout is flat because the clause/process IA tree (doc 04 §10.3)
  needs `clause_mapping`, an **S9** seam; drift scan/quarantine/`MIRROR_DRIFT_DETECTED` stay **v1** (D-6). Proofs:
  `test_ro_mirror_autocorrect` [AC#2] (edited file + stray file both corrected from the vault on re-sync), effective-
  only-excludes-drafts, supersession/obsolete prune, post-commit enqueue-once, atomic-swap-no-partial-tree, render-
  pending marker, metadata/INDEX/manifest, byte-idempotent rebuild, advisory-lock serialization.

- **S7b — Watermarked-PDF rendering** ✅ — made the S7 `RenderSink` real (zero-migration). `render_gotenberg.py`
  `GotenbergRenderSink` (a **pure** convert+overlay; no DB/MinIO) routes on mime_type → Gotenberg
  `/forms/libreoffice/convert` (office) / `/forms/chromium/...` (html) / **passthrough** (pdf); a non-renderable
  allowlist short-circuits. `watermark.py` `stamp_controlled_copy` (reportlab+pypdf, **BSD-only**, NO PyMuPDF/AGPL)
  draws the §11.3 band (header `{id} — {title} {classification}`; footer `Rev · Effective · Owner / Controlled in
  EasySynQ · {copy_status} · Page n of N / Verify…`) + the diagonal `{copy_status}` watermark onto **every page**,
  **byte-deterministic** (reportlab `invariant=1` + a pinned pypdf `/ID`) so the rendition content-addresses.
  `render()` is now **async + three-way `RenderResult`** (RENDERED / NON_RENDERABLE=R26 / PENDING) + `set_render_sink`.
  **`build_tree` owns caching** (the sink stays pure + testable): cache-hit fetch by `eff.rendition_blob_sha256`,
  else render → RENDERED caches (`storage.put_bytes` to the **non-WORM** renditions bucket + a derived `Blob` row +
  set the FK, under the mirror's advisory-locked session) → next sync is a cache hit (no Gotenberg). `metadata.json`
  gains `render_status` (rendered/pending/unrenderable) + `no_controlled_rendition` (R26 only). The **worker** renders
  for real (`tasks/mirror.py` constructs `GotenbergRenderSink`); the **api never renders** (it presigns the cache).
  New `GET /documents/{id}/download` (doc 15 §8.5) presigns the Effective version's controlled-copy rendition
  (fallback `rendition:"source"`). **Latent bug fixed:** check-in now captures the real `Content-Type` from MinIO
  (`ObjectHead.content_type` via `finalize_worm`'s head) into `blob.mime_type` — previously always `octet-stream`,
  which would have routed everything to R26; this is what makes render routing correct. Compose: pinned
  `gotenberg/gotenberg:8.33` + `worker depends_on renderer` (no healthcheck — gotenberg bundles no http client and
  rendering is resilient: a renderer outage → `pending` → self-heals). Deps: `reportlab`+`pypdf` (+ a uv.lock license
  guard). **Owner decisions:** (1) defer the **verify-token + QR + public `GET /verify`** entirely to **S7c** (open
  spec + dead-ink QR); (2) ship the download endpoint. Proofs: `test_watermark_band_carries_rev_effective_copystatus`
  + obsolete/superseded-stamp + determinism + Gotenberg 200/5xx-R26/503-pending/transport-pending + encrypted-pdf-R26
  + three-way build_tree branch + license guard (unit, mocked Gotenberg — no container); `test_released_mirror_pdf_
  carries_band` [HEADLINE] + R26-no_controlled_rendition + rendition-cached-skips-render + download-controlled_copy/
  source (integration, PDF-passthrough — the LibreOffice path is validated on the real stack). Full suite 171 passed.

- **S7c — Verify-token + QR + public `/verify`** ✅ — the controlled-rendition verify token (doc 05 §6.4,
  zero-migration). `services/vault/verify_token.py` mints a compact Ed25519-signed token =
  `base64url(doc_id[16] ‖ version_id[16] ‖ source_digest[32] ‖ sig[64])` (~171 chars), reusing the `checkpoint.py`
  key pattern with its **own** dedicated key (`verify_token_signing_key_path`); `verify()` returns claims|None
  (None on bad/forged/tampered). **Pure-sink discipline kept:** `build_tree` mints the token (it has the doc
  context — `EffectiveDoc` gained `document_id`) and passes `verify_url = {public_base_url}/api/v1/verify?t=…` into
  `RenderRequest.verify_url`; `watermark.py` draws a `segno` QR (deterministic PNG) of whatever URL it's given +
  the scan hint (no signing knowledge → still pure/testable). **Deterministic** (Ed25519 + immutable claims) so the
  rendition stays content-addressed (S7b cache/idempotency invariants hold). `api/verify.py` = a **public**
  (`security: []`, no auth) `GET /verify` returning a minimal **HTMLResponse**: verify token → load the version →
  digest match → CURRENT iff it's the doc's `current_effective_version_id` & `version_state==Effective`, else
  SUPERSEDED; bad token → UNKNOWN. Minimal disclosure (status + identifier + current rev/date); each hit logged
  (`vault.verify`). `easysynq mirror rebuild` now **force-clears `rendition_blob_sha256`** then re-renders (so a
  template change like the QR reaches existing renditions; `sync` keeps the cache). The verify key is **shared
  api↔worker via a new `secrets` volume** (worker mints, api verifies — they MUST agree). Deps: `segno>=1.6`
  (BSD). **D3:** this is an integrity/currency token, NOT a Part-11 e-signature (signs a currency claim, not an
  approval) — the `signature_event` path stays reserved. **Owner decisions:** (1) `/verify` is **public** (the
  whole point — an external auditor scans a printout without an account; the signed token prevents enumeration);
  (2) scope = verify-token+QR+/verify only, the per-intent **export/print stamp** ("UNCONTROLLED IF PRINTED" +
  printed-by/ts + `export_event`/`print_event` audit, a non-cached per-request rendition) defers to **S7d**.
  Proofs: token mint/verify round-trip + wrong-key/tampered/garbage + determinism (unit); watermark embeds the QR
  (unit); `/verify` CURRENT/SUPERSEDED/UNKNOWN + mirror-rendition-carries-QR + `rebuild --force`-re-renders
  (integration). Full suite 184 passed.

- **S7d — In-app export/print stamp [AC closes the rendering epic]** ✅ — the per-request, non-cached export/print
  rendition (doc 04 §11.2, R26). Two **authenticated** endpoints serve a FRESH stamped PDF of the Effective version,
  distinct from `/download`'s cached controlled-copy presign: **`GET /documents/{id}/export`** ("UNCONTROLLED WHEN
  PRINTED — valid as of {date}" + "Exported {ts} by {user}", attachment, gate `document.export` sod_sensitive,
  `EXPORTED` audit) and **`GET /documents/{id}/print`** ("CONTROLLED COPY — valid on {date} only" + "Printed {ts} by
  {user}", inline, gate `document.print_controlled`, `PRINTED` audit). **Owner-chosen design:** the api NEVER converts
  via Gotenberg — `services/vault/render_dynamic_copy` reuses the worker's **already-cached** controlled-copy PDF as the
  base and `watermark.stamp_per_request_copy` overlays ONLY a banner + footer note (reportlab/pypdf, no second band/QR),
  run in `asyncio.to_thread`, then streams the bytes (the narrow "api reads+overlays+streams rendition bytes" softening
  of D1, documented in the docstrings). The rendition carries a timestamp+user → **intentionally non-deterministic →
  NEVER content-addressed/cached** (`rendition_blob_sha256` stays the cached controlled copy). **`0011`** is the repo's
  **first additive-enum migration**: `ALTER TYPE event_type ADD VALUE 'EXPORTED'/'PRINTED'` (PG16 in-txn-safe — no row
  uses the value; **irreversible → no-op `downgrade`**, safe under CI's round-trip because `0010` drops the type
  wholesale) + the matching Python `EventType` members (mandatory — a fresh `upgrade head` rebuilds from
  `EVENT_TYPE_VALUES`). `canonical_serialize` v1 **untouched** (new values hash as their string; golden vector passes);
  the intent/copy disposition ride in the already-hashed `after` JSONB (added to `VaultAuditEvent`/`_emit`, **no new
  hashed column**). 404 when no Effective version; **`409 no_controlled_rendition`** (pending or R26 — the version row
  carries no render-status to distinguish them) carries a **user-facing "uncontrolled when printed" notice** + a
  source-download pointer (the click-through UI is the SPA's job, deferred). The `Content-Disposition` filename is
  reduced to a strict ASCII token (`_safe_pdf_filename`) — closes a header/parameter-injection vector (the identifier
  embeds the request-supplied `area_code`). **Both keys already in the closed 96-key catalog (no catalog change);
  `document.export` is granted to NO seeded role by design** (deliberate, sod_sensitive — grant via override/custom role
  pre-S8; `document.print_controlled` is in the Employee bundle). Adversarially reviewed (5 lenses → per-finding verify):
  folded filename sanitization, the event-loop offload, the R26 notice, and two negative tests. Proofs: watermark
  banner/footer-every-page + dual-marking + determinism + per-request variance, `EventType` resolution + `after`-mapping
  + filename sanitizer (unit); export/print stamp+audit (headline), 403-without-export, 403-without-print_controlled,
  404-no-effective, 409-no-rendition+notice (integration, PDF-passthrough + mirror sync — no Gotenberg). 126 unit + 73
  integration passed.

- **S8a — Setup spine (latch + bootstrap-of-trust + first admin + org profile + finalize)** ✅ — PR #16. The first-run
  foundation (doc 08) that stands a fresh install up **self-service + latch-protected**, without the `grant-role` CLI.
  An owner-approved **decomposition** of S8 (G-B WORM-verify, G-C/AC#5 backup+restore-drill CLI, G-D auth-config, wizard
  steps 6-9, the client-side router, in-app Keycloak provisioning + MFA all deferred to **S8b/S8c**). **The 423 latch**
  is an ASGI middleware in `create_app()`: `/api/v1/*` → 423 `setup_incomplete` while `setup_state != OPERATIONAL`, with
  **boundary-anchored** exemptions (the `/setup` tree + exact `/auth/config`, `/me`, `/verify`, `/openapi.json`, `/docs`
  — a `startswith`-collision review fix) so a future sibling route can't be silently un-latched; no cache (per-request
  indexed PK lookup — isolation-safe; the conftest defaults the shared test DB to OPERATIONAL so non-setup tests aren't
  latched). **Bootstrap-of-trust:** `easysynq setup mint-bootstrap` (a new `cli/setup.py`) stores a 256-bit single-use,
  TTL'd, **salted-SHA256** secret on `system_config`; the **public** `POST /setup/bootstrap` (Keycloak-authenticated but
  **outside the PEP** — the secret, not a grant, authorizes it) verifies it constant-time + grants the caller the seeded
  System Administrator role → breaks the deny-by-default chicken-and-egg. Best-effort Redis rate-limit (5/15min, degrades
  if Redis is down). `grant-role` stays **break-glass**. **Endpoints** (`api/setup.py`): `GET /setup/state` (public, SPA
  routing), `GET /setup` (auth), `POST /setup/bootstrap`, `PATCH /setup/org-profile` + `POST /setup/finalize`
  (`config.update`). An **extensible gate registry** (`services/setup/service.py GATES`): S8a checks **G-A** (admin) +
  **G-E** (org `short_code != 'DEFAULT'`); finalize flips the one-way `UNINITIALIZED→IN_SETUP→OPERATIONAL` + emits
  `SETUP_FINALIZED` (its `after` carries the full `{gate: bool}` snapshot — a `sorted(dict)`-drops-bools review fix).
  Setup `audit_event` rows (object types `config`/`user`) commit atomically; `canonical_serialize` v1 untouched.
  **Migration `0012`**: `ALTER TYPE event_type ADD VALUE` for the 4 setup events (the `0011` pattern; no-op downgrade) +
  Python `EventType` members; `organization.timezone` (R8); the bootstrap columns; and it **seeds the never-before-created
  `system_config` row** — `OPERATIONAL` iff a `role_assignment` already exists (so upgrading a **running** install isn't
  bricked by the new latch), else `UNINITIALIZED`; downgrade deletes the seeded row (the org FK would block `0002`).
  **Web (minimal, no router):** `App` branches on `/setup/state` — a Mantine `<Stepper>` wizard (sign-in + bootstrap
  secret → org-profile form → finalize) vs the normal shell; a bearer-fetch helper; **no new deps**. Adversarially
  reviewed (5 lenses → 15-agent verify); the `0012` OPERATIONAL-upgrade branch verified on a throwaway PG. Proofs:
  secret mint/verify + EventType (unit); latch-423-until-operational, bootstrap-grants-admin+audits, wrong/replay/
  expired/no-secret rejected, rate-limit-lockout, org-profile authz+validation, finalize-gates→OPERATIONAL+latch-lifts,
  exemption-boundary, grant-role break-glass (integration). 131 unit + 84 integration.

- **S8b — Setup gate G-B (WORM-verify) + `storage_config`** ✅ — PR #18. An owner-approved **split** of S8b (the
  backup/restore CLI + the AC#5 restore-into-scratch drill + gate G-C are **S8b2** — disjoint risk profiles). Lands
  gate **G-B** (doc 08 §7): **`storage.worm_probe`** PUTs a tiny probe to the object-locked `documents` bucket →
  confirms a future `retain-until` → attempts to delete **that version** with no bypass and expects a **denial** (the
  honest §7.2 proof — deletes the *version*, not a delete marker; a non-versioned/non-locked bucket → no VersionId →
  **not verified**, so no false-PASS). Short boto3 timeouts + a guarded `put` so a missing/unreachable bucket is a clean
  422, not a 500/hang (review fix). **`POST /api/v1/setup/verify-storage`** (gate `storage.manage`, latch-exempt) → PASS
  upserts `storage_config` (`worm_verified_at` + the `object_lock_mode` choice) + emits `WORM_VERIFIED` + commits
  (serialized on the `system_config` singleton lock — a review fix for the same-org check-then-insert race); FAIL → 422
  `worm_not_enforced` (signal stays null → no gate false-pass). **D-7:** the object-lock mode is **recorded** (default
  GOVERNANCE); COMPLIANCE is not plumbed (a hardened v1.x opt-in). `Gate("G-B", _gate_worm_verified)` appended to the
  registry — finalize now requires **G-A + G-E + G-B** (live re-check, zero finalize-code change). **`0013`**: `ALTER
  TYPE event_type ADD VALUE 'WORM_VERIFIED'` (+ Python member) + a **minimal** `storage_config` (id PK, org_id unique,
  `worm_verified_at`, `object_lock_mode`; no seed — null reads as G-B-unsatisfied; no brick risk — upgraded OPERATIONAL
  installs never re-finalize; doc-14's backup/bucket/mirror columns land in S8b2). **Web:** a "Storage" `<Stepper>` step
  (GOVERNANCE/COMPLIANCE mode + a Verify button) between Organization and Finalize. Adversarially reviewed (4 lenses →
  verify) — the probe lens hunted the dangerous **false-PASS** direction and all three findings independently confirmed
  there is none; folded the concurrency lock + the guarded-probe-put/timeout + a re-run/UPDATE-in-place test. Proofs:
  probe verifies the locked bucket + correctly reports plain `staging` as non-WORM, verify-storage sets G-B +
  `WORM_VERIFIED` audit + requires `storage.manage` (403 else), finalize-blocked-on-G-B-then-passes, re-run UPDATEs in
  place. 131 unit + 89 integration.

- **S8b2 — Setup gate G-C (backup/restore-into-scratch drill) + durable backup [AC#5]** ✅ — PR #20. The last blocking
  setup gate + a named MVP acceptance proof: finalize is **blocked until a real backup→restore-into-scratch drill
  PASSES** the integrity triad (**blob SHA-256 re-hash · per-table row-count parity · `document_version→blob` FK
  check**); "configured but unverified" does **not** satisfy G-C (doc 08 §8, doc 18 §7). **Owner forks:** real
  `pg_dump`→`pg_restore` (a faithful artifact round-trip, NOT a logical copy — the thing G-C exists to catch) ·
  restore into a scratch **DATABASE** (pg_restore's natural unit; doc 08 §8.2's "temporary PG schema" **reconciled**
  as "an isolated namespace", noted in `drill.py` + back-propagated to doc 08 §8.2) · **durable archive + drill** (a
  real `easysynq backup` + nightly Beat, alongside the gating drill). `services/backup/`: `dsn` (SQLAlchemy URL→libpq
  env, password via env not argv), `archive` (`pg_dump -Fc`/`pg_restore` subprocess + tar + `.sha256` pack/verify),
  `drill` (scratch-DB createdb→`pg_restore`→teardown; blob copy into the **non-WORM** `restore-scratch` bucket under a
  per-drill prefix; the triad on the **restored** copy; **race-free row-count parity** via `pg_export_snapshot()` +
  `pg_dump --snapshot`; composable steps + an `after_restore` fault seam; **never raises** — a missing binary/crash is
  an honest FAIL, never a 500), `service` (async orchestration: `LOCK_RESTORE_DRILL`=7710004, persist
  `last_restore_test_result`, emit `RESTORE_TEST_*` + commit). Runs as the **OWNER** role (`sync_dsn`) — the
  `easysynq_app` role can neither `pg_dump` the whole DB nor `CREATE DATABASE`. `Gate("G-C", _gate_restore_test_passed)`
  appended to `GATES` (keys on `result=='PASS'`, not just `_at`) → finalize now needs **G-A+G-E+G-B+G-C**, zero
  finalize-code change; the off-host audit anchor stays a **soft gate** (surfaced in `GET /setup` via S6's
  `tamper_evidence_attested`; never blocks; R13). **`0014`**: `ALTER TYPE event_type ADD VALUE` ×3
  (`BACKUP_CONFIGURED`/`RESTORE_TEST_PASSED`/`RESTORE_TEST_FAILED`, the 0012/0013 pattern; Python `EventType` members
  added too) + `backup_policy` (doc 14 §2 columns; retention as **counts** 7/4/6; `wal_pitr_enabled` a recorded
  forward-seam — `configure-backup` **rejects `true`** as D-6 scope). Endpoints (latch-exempt): `POST
  /setup/configure-backup` (`backup.configure` + live destination writability check) + `POST /setup/run-restore-test`
  (`restore.run`, enqueues the worker task — 202). `tasks/backup.py` + the nightly `easysynq.backup.run` Beat job +
  `cli/backup.py` (`run`/`restore-test`) wired into `scripts/easysynq`. **Dockerfile**: `postgresql-client-16` via the
  PGDG repo (matches `postgres:16`; build-time only → air-gapped *installs* unaffected). Compose: a `backup` volume on
  the worker; minio-init + the integration conftest add the plain `restore-scratch` bucket (R37 — object-lock can't be
  retro-added, never restore into the WORM vault bucket). **Web:** a "Backup" `<Stepper>` step (configure + run-restore-
  test with poll-to-green) + the not-tamper-evident soft-gate warning. Adversarially reviewed (5 lenses → 24 raw → 14
  verified; the false-PASS lens found no way for the drill to PASS without a real restore): folded the **headline
  coverage gap** — the blob-dependent legs (re-hash + FK) were only vacuously exercised at 0-blob IN_SETUP, so added two
  deterministic OPERATIONAL-state tests over a real Effective document (`test_drill_passes_over_real_blobs` asserts
  `details.blobs ≥ 1`; `test_drill_fails_on_corrupted_restored_blob` proves the re-hash leg catches a corrupted restored
  blob) + the WAL/PITR-reject test + the §8.2 deviation/skip docstrings. Also fixed a real bug review surfaced via the
  full-suite (with prior tests' blobs): teardown's multi-delete `delete_objects` → MinIO `MissingContentMD5` → switched
  to per-object `delete_object`. Proofs: `test_setup_finalize_requires_restore_pass` **[AC#5]** + negative
  drill→FAIL→finalize-blocked, configure/run authz (403), destination/cron/wal-pitr validation, durable archive,
  scratch teardown (no orphan DB/objects), real-blob PASS + corrupted-blob FAIL; archive-checksum + dsn + EventType
  unit. **139 unit + 101 integration** (the real-drill path validated locally with `postgresql-client-16` + in CI).

**Next slice: S8c — auth-config gate G-D + the fuller wizard / client router** — the Keycloak in-app provisioning + MFA
+ a proven non-bootstrap login (gate G-D, doc 08 §9), plus wizard steps 6–9 (org roles/users, QMS scope/process map,
import hand-off) and the client-side router. Then **S9** (clause/process IA + `clause_mapping` — also unblocks the
mirror's clause/process tree + the lifecycle submit ≥1-`clause_mapping` gate). The gate registry + latch extend by just
appending gates. **Deferred from S8b2 (S11 / v1.x, D-6 / R37):** the operator-grade *live* WORM-aware restore + cutover,
PITR↔blob-snapshot alignment, checkpoint-not-ahead, WAL/PITR, retention *pruning* execution, Keycloak realm export,
archive envelope encryption, S3-destination, `easysynq restore`/`upgrade`; the non-drill-critical `storage_config`
mirror/bucket columns were also deferred (config-redundant with env, no G-C proof value). S6/S7 seams still open
(Keycloak auth-event SPI, `/audit-events/export`, the clause/process IA mirror tree). Pre-existing hardening noted:
`area_code` is unconstrained `Text` at the S3 create boundary.

## Building the MVP (dev workflow)

- **Branch + PR flow:** `main` is protected. Do slice work on a `feat/sN-*` branch → open a PR → green CI →
  squash-merge. CI jobs: `contracts` (redocly), `api` (ruff/mypy-strict/unit), `migrations` (alembic up↔down +
  `alembic check`), `web` (eslint/tsc/build), `integration` (pytest -m integration via testcontainers). All five
  are required checks.
- **Toolchain (this machine):** `uv` + a managed **Python 3.12** at `~/.local/bin/uv` (system `python3` is 3.14;
  `pip` needs `--break-system-packages`). Node 22 + npm. Docker v29.x. Lockfiles committed (`uv.lock`,
  `package-lock.json`); CI uses `uv sync --frozen` / `npm ci`.
  - **Docker socket:** the user is in the `docker` group, so a fresh login session (e.g. after a reboot) should
    use Docker directly. If a shell still gets "permission denied", re-run `sudo chmod 666 /var/run/docker.sock`
    (personal, non-shared device).
- **Local loops** (fast; no commit needed to iterate):
  - API: `cd apps/api && uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest`
    (unit always; `-m integration` needs Docker for testcontainers).
  - Web: `cd apps/web && npm run lint && npm run typecheck && npm run build`.
- **Run the stack:** `just up s` (or `docker compose -f infra/compose/compose.yml -f infra/compose/compose.s.yml
  up -d --build`). Open **http://localhost**. Stop with `just down`. A gitignored `.env` holds dev secrets +
  `OIDC_ISSUER=http://localhost/realms/easysynq`. OpenSearch + gotenberg are intentionally not run in MVP dev
  (R34 / not needed until S7).
- **⚠ S6 `.env` role separation (do this before bringing the stack up for S6+):** `0010` adds DB role separation, so
  the gitignored `.env` must now point the app at the **non-owner** role (else the running stack still connects as the
  owner and the append-only grant is a no-op — though CI proves AC#6a regardless). Set
  `DATABASE_URL=postgresql+psycopg://easysynq_app:<APP_DB_PASSWORD>@postgres:5432/easysynq`, keep
  `DATABASE_URL_SYNC` on the **owner** `easysynq` (alembic CREATEs the roles), and add
  `AUDIT_LINKER_DATABASE_URL` (the `easysynq_linker` DSN) + `APP_DB_PASSWORD`/`LINKER_DB_PASSWORD` (matching the
  DSNs) + `S3_BUCKET_AUDIT_CHECKPOINTS`/`AUDIT_SINK_ACCESS_KEY`/`AUDIT_SINK_SECRET_KEY` — see `.env.example`. Then
  `just up s --build` (the `migrate` service runs `0010` as the owner → creates `easysynq_app`/`easysynq_linker`
  before `api`/`worker`/`beat` start as the app role). `minio-init.sh` provisions the `audit-checkpoints` bucket +
  the scoped `audit-sink` user. The `worker`/`beat` containers now run real tasks (the S6 chain-linker/verify/
  checkpoint/roll-partitions Beat jobs + the **S7 mirror reconcile**).
- **S7/S7b/S7c/S7d mirror + rendering + verify + export/print (operator):** the `worker` writes the read-only mirror to
  the `mirror` volume **rw**; `api` mounts it **`:ro`** — the whole R11 contract for the single-host MVP (Caddy must NOT
  `file_server` it; the in-app view route stays the presigned-MinIO `GET /documents/{id}/download`, while **S7d**'s
  `GET /documents/{id}/export` (gate `document.export`) + `GET /documents/{id}/print` (gate `document.print_controlled`)
  **stream** a fresh per-request stamped PDF from the api — `document.export` is granted to no seeded role, so grant it
  via override/custom role until S8's role UI).
  On a network share, validate `root_squash`/UID mapping (runbook caveat). The mirror is **regenerable, never
  backup-critical**, rebuilt on every release/obsolete (post-commit) + a nightly Beat reconcile. Browse it at
  `${MIRROR_PATH}/current/` — now **watermarked controlled-copy PDFs** (S7b: gotenberg `renderer` is live; office→PDF +
  the §11.3 band + a verify QR) with each footer carrying a signed verify token. **S7c `.env` additions (already in
  `.env.example`):** `VERIFY_TOKEN_SIGNING_KEY_PATH=/run/secrets/verify_token_key` + `PUBLIC_BASE_URL=http://localhost`;
  the verify key is **shared api↔worker via the `secrets` volume** (worker mints, api verifies). The public verify page
  is `GET /api/v1/verify?t=…` → CURRENT/SUPERSEDED/UNKNOWN. **After upgrading an existing stack** (so S7b/S7c renditions
  carry the new template/QR), force a full re-render: `docker compose … exec worker python -m easysynq_api.cli.mirror
  rebuild` (clears `rendition_blob_sha256` + re-renders; plain `sync` keeps the cache). The `worker`/`beat` now run the
  S6 audit jobs + the S7 mirror reconcile, and the `renderer` (gotenberg:8.33) must be up for real rendering (a
  renderer outage degrades to `render_status:"pending"` and self-heals on the next reconcile).
- **Dev login:** `demo` / `Demo-Password-1` (created at runtime in Keycloak, **not committed**; realm policy
  requires ≥12-char passwords). After a Keycloak container reset, recreate with `kcadm.sh` (`create users -r
  easysynq -s username=demo -s enabled=true` then `set-password`).
- **First-run setup (S8a) — the primary path now:** a fresh install boots `UNINITIALIZED`, so the **whole `/api/v1/*`
  QMS surface is 423 `setup_incomplete`** until setup finalizes (the latch). Stand it up self-service: (1) operator runs
  **`easysynq setup mint-bootstrap`** (prints a one-time secret); (2) open **`/setup`** in the browser, sign in via
  Keycloak, paste the secret → you become the first **System Administrator** (`setup_state → IN_SETUP`); (3) the wizard
  sets the org profile (legal name / short code / timezone); (3.5 — **S8b**) **Verify storage** (the WORM probe, G-B);
  (3.6 — **S8b2**) **Backup**: set a backup destination, then **Run backup + restore-test drill** — finalize is blocked
  until it PASSES (G-C / AC#5); (4) **Finalize** flips `→ OPERATIONAL` and the latch lifts. After an **upgrade of a
  running install**, `0012` seeds `OPERATIONAL` automatically (a `role_assignment` already exists) — no wizard, no
  lock-out. **NB the operator must point the app at the non-owner DB role for the latch UPDATE to work** (same `.env`
  role-separation as S6).
- **⚠ S8b2 backup/restore drill (operator):** the drill + `pg_dump` run as the **OWNER** role, so the **worker** must
  see `DATABASE_URL_SYNC` (the owner `easysynq` DSN — the same one Alembic uses; already set for S6) in addition to the
  non-owner `DATABASE_URL`. New `.env`/compose: `BACKUP_PATH` (default destination, a mounted `backup` volume on the
  worker) + `S3_BUCKET_RESTORE_SCRATCH=restore-scratch` (a plain non-WORM scratch bucket minio-init provisions). The
  worker image now carries `postgresql-client-16`. Operator CLI (host-side): `easysynq backup run` (write a durable
  archive now) and `easysynq backup restore-test` (run the gating drill; exits non-zero on FAIL) — both dispatch to the
  worker container. The nightly `easysynq.backup.run` Beat job writes durable archives (pg_dump + a MinIO blob
  manifest); the operator-grade **live** WORM-aware restore stays S11.
- **Authz break-glass (`grant-role`):** still available to assign a seeded role directly, bypassing the wizard +
  PEP — `easysynq grant-role <keycloak-subject> ["Role Name"]` (default "System Administrator"; idempotent;
  JIT-creates the `app_user`; runs `easysynq_api.cli.grant_role` as the DB owner). Use it to recover a botched
  bootstrap or to seed an admin without the UI.
- **No Docker?** Every slice is still buildable + unit-testable on the uv/3.12 loop; CI runs the stack-dependent
  proofs.

## The four LOCKED foundational decisions (never contradict)

| # | Decision |
|---|---|
| **D1** | **Self-hosted web app.** On the org's own server; browser access; data never leaves their infra; admin-controlled backups; single-organization per install; no phone-home. |
| **D2** | **Managed controlled vault** is the source of truth (PostgreSQL + object storage). Filesystem = read-only mirror, regenerated from Released versions only. Authority flows vault → mirror, never the reverse. |
| **D3** | **ISO 9001:2015 foundation**, *architected* (not built) to extend cleanly to 21 CFR Part 11 e-signatures and multi-standard frameworks (ISO 13485/14001/45001/IATF). Reserved hooks exist (`signature_event`, `framework_id`, M:N clause mapping) — do not implement them in v1, do not remove them. |
| **D4** | **Stack:** React/TS + Mantine + Tailwind (SPA) · FastAPI / Python 3.12 (API) · PostgreSQL 16 + MinIO + OpenSearch + Redis · Celery workers · Keycloak (auth) · Gotenberg/LibreOffice (rendering) · Caddy (TLS) · Docker Compose (single host; S/M/L profiles). |

**Permission philosophy (locked):** hybrid **RBAC + ABAC** — granular `domain.action` permissions,
bundled into org-defined roles, scopable to system/process/folder/document, with per-user overrides
and explicit deny. **Deny-by-default; deny-always-wins.** ADMIN sits *outside* the QMS with full
system permissions. Per a stakeholder decision, the **Quality Manager may hold `permission.grant`
scoped to content domains within QMS scope**; system permissions (user/storage/backup/restore/config/
import) stay admin-only.

## Other stakeholder decisions made this session

- **Import default = current-version-only** (older copies archived as provenance); revision-chain
  reconstruction is opt-in per family; Document-vs-Record *kind* is always human-confirmed.
- **Tamper-evidence requires a mandatory off-host / append-only audit-checkpoint anchor.**
- The full reconcile+harden pass was completed (see `docs/decisions-register.md`).

## Document map (`docs/`)

`decisions-register.md` is **AUTHORITATIVE** — it resolves R1–R37 and **supersedes any conflicting
text** in the section docs. If two docs disagree, the Register wins; otherwise the more specific
section governs (00 §7 explains authority precedence).

- `00-overview.md` — front door: summary, locked decisions, TOC, cross-cutting map, persona×feature matrix
- `01` vision/personas/glossary · `02` ISO domain model & information architecture · `03` architecture & stack
- `04` document control & vault · `05` revision & drift · `06` records & evidence · `07` authorization model
- `08` setup & onboarding · `09` ingestion engine · `10` workflows & notifications · `11` UI/UX design system
- `12` security & audit · `13` search & reporting · **`14` data model (ERD)** · **`15` API design**
- `16` roadmap (MVP → v1 → v1.x → Future) · `17` gaps & open-questions (with per-finding resolution status)

## Conventions used throughout the spec

- **Document lifecycle = 7 canonical states:** `Draft → InReview → Approved → Effective →
  UnderRevision → Superseded → Obsolete` (the 5-state form is a simplified UI view).
- Permission keys are `domain.action` (canonical catalog in `docs/07`; data-model seed in `docs/14 §3.1`).
- 8 canonical personas: Avery (Admin), Mara (Quality Manager), Diego (Process Owner), Priya (Author),
  Ken (Approver), Ingrid (Internal Auditor), Sam (Employee), Olsen (External Auditor).
- `signature_event.meaning` enum (v1): `review, approval, release, obsolete, verify, disposition,
  import_baseline, review_confirmed`; `authored`/`responsibility` reserved for the Part-11 phase.

## Working preferences

- **Spec/plan before code.** Produce and get approval on a plan before implementing.
- The owner used **`/effort ultracode`** (multi-agent Workflow orchestration) for the heavy
  spec/mockup work; `/effort` is per-session, so re-enable it if you want that approach again.
- When a genuinely strategic decision is the owner's to make, ask rather than silently pick.
- Persistent memory: `~/.claude/projects/-home-cojoa13-Documents-EasySynQ/memory/` (MEMORY.md index).

## How to view the mockup

`mockup/easysynq-mockup.html` — open in a browser (e.g. `xdg-open mockup/easysynq-mockup.html`).
This laptop has **no headless browser**, so PNG screenshots can't be auto-generated here; install one
(e.g. `chromium-browser`) if static images are wanted.
