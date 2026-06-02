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

## Current status (as of 2026-06-01)

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

**Next slice: S7c — verify-token + in-app export stamp** (the deferred verify path: the Ed25519-signed
`{document_id, version_id, content_digest}` token + QR embedded in the footer + the public `GET /verify` →
CURRENT/SUPERSEDED/UNKNOWN, reusing the `checkpoint.py` signing pattern; plus the per-intent export/print stamp
"UNCONTROLLED IF PRINTED" + printed-by/ts + `export_event`/`print_event` audit; widen `RenderSink.render` to a
three-way so non-renderable ≠ pending in S7b's downstream). Then **S8 — setup wizard** (per docs/18). S6/S7 seams
still open: the `event_type` enum reserves the Keycloak auth-event values (SPI ships later), `/audit-events/export`
keeps its `openapi.yaml` schema unmounted, and the clause/process IA mirror tree awaits `clause_mapping` in **S9**.

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
- **S7 mirror (R11 mount contract):** the `worker` writes the read-only mirror to the `mirror` volume **rw**; `api`
  mounts it **`:ro`** — that is the whole contract for the single-host MVP (Caddy must NOT `file_server` it; the
  user-facing content route stays the presigned-MinIO download). On a network share, validate `root_squash`/UID
  mapping so a client cannot write back (runbook caveat). The mirror is **regenerable, never backup-critical**. It is
  rebuilt on every release/obsolete (post-commit) + a nightly Beat reconcile; force one with `docker compose … exec
  worker python -m easysynq_api.cli.mirror sync`. Browse it at `${MIRROR_PATH}/current/` (an Effective-only flat tree
  of source bytes — watermarked PDFs arrive with S7b). No new `.env` keys (`MIRROR_PATH`/`GOTENBERG_URL` already in
  `.env.example`).
- **Dev login:** `demo` / `Demo-Password-1` (created at runtime in Keycloak, **not committed**; realm policy
  requires ≥12-char passwords). After a Keycloak container reset, recreate with `kcadm.sh` (`create users -r
  easysynq -s username=demo -s enabled=true` then `set-password`).
- **Authz bootstrap (pre-S8):** since the first-run wizard that grants the first admin is S8, the authz admin API is
  deny-by-default for everyone until a role is assigned. Bootstrap with `easysynq grant-role <keycloak-subject>
  ["Role Name"]` (default "System Administrator"; idempotent; JIT-creates the `app_user`). It runs
  `easysynq_api.cli.grant_role` inside the api container — an explicit operator action, not an app-logic auto-grant.
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
