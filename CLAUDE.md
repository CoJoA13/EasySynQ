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

## Repository layout

- `apps/api/` — FastAPI / Python 3.12 backend. Under `src/easysynq_api/`: `api/` (routes) · `services/`
  (use-cases, transaction owners) · `domain/` (pure logic) · `db/models/` (ORM) · `db/seeds/` (seed data,
  e.g. the ISO clause catalog) · `tasks/` (Celery worker/beat) · `cli/` (operator commands). Tests in
  `apps/api/tests/{unit,integration}` (the latter via testcontainers).
- `apps/web/` — React/TS + Mantine SPA (currently the setup wizard + admin stubs; the rest of the UI is deferred).
- `migrations/` — Alembic (single tree; head **`0026`**; `env.py` excludes migration-managed expression/partial indexes).
- `packages/contracts/openapi.yaml` — the living API contract (redocly-lint only; **not** codegen — server/web aren't generated from it).
- `infra/compose/` — Docker Compose (S/M/L profiles) + Caddy; `just` recipes wrap it.
- `docs/` — the authoritative spec (`00`–`18` + `decisions-register.md`); `mockup/` — the owner-approved HTML UI mockup.

## Current status (as of 2026-06-04)

**MVP COMPLETE** (all 11 ordered slices S0–S11 shipped to `main` via PR, all CI green, validated on the real
Docker stack; the exit slice S11 is PR #41). All six MVP acceptance proofs are in; the mirror epic + both IA
backends are complete; the doc-18 §12 exit checklist is closed. The design was locked first (spec-before-code);
v1/v1.x residuals are listed at the end of this section.

**v1 phase: STARTED.** The owner chose (AskUserQuestion) the **v1 feature** track → **Records & evidence
(doc 06)** as the slice family (over the web track + the v1.x backend residuals); **S-rec-1**, **S-rec-2**,
then the **Evidence Packs (UJ-7)** family (**S-pack-1** build/seal, **S-pack-2** external delivery + PDF portfolio) shipped
depth-first, completing UJ-7. **Migration head is now `0026`.**

- **S-rec-1 — Records: capture + evidence-linking + correction** ✅ — PR #43, migration `0023` (the full
  non-obvious decisions live in the squash-merge commit + the project memory). Turned the inert `record`
  scaffolding ON: atomic immutable capture (base `documented_information[kind=RECORD]` + `record` subtype +
  WORM-sealed evidence in the dedicated `records` bucket + a domain-separated `content_hash` seal, one commit) ·
  the polymorphic `evidence_for_link` evidence-for sub-resource · correction-via-new-record (records are immutable —
  a route-inventory proof) · retention-policy-as-data (a 5-tier resolver + a seeded per-org *System Default* + the
  snapshot-at-capture ratchet) · a kind-scoping fix so Records don't leak into `GET /documents`/search · records ride
  **SYSTEM `record.create`/`.read` overrides** (catalog CLOSED).

- **S-rec-2 — Records: retention/disposition lifecycle** ✅. Turns the inert disposition scaffolding (the
  `RecordDispositionState` enum, `record.disposition_state`/`legal_hold` cols, the sweep index, the
  `retention_policy.{disposition_action,review_required,worm_lock_period}` fields — all shipped dead in 0023) into a
  working end-of-life subsystem (doc 06 §5; doc 14 §10; R5/R27). **Owner forks (AskUserQuestion):** full scope incl. R27
  in **one PR** · DESTROY = **physically delete the WORM bytes, fail-closed** · legal-hold on **`record.dispose`**
  (catalog CLOSED) · sweep **auto-disposes low-risk** (`review_required=false`). **Migration `0024`**: `disposition_event`
  (immutable tombstone — doc-14 cols + the R27 `is_worm_destroy`/`requested_by`/`legal_basis` + nullable `policy_id`) +
  `worm_destroy_request` (the dual-control two-step, state from nullable timestamps — the `dcr` R22 precedent — with a
  `CHECK(approved_by<>requested_by)` + a **partial `UNIQUE(record_id) WHERE open`** authored as raw DDL + excluded in
  `env.py` [the 0020 lesson]) + **9 additive `RECORD_*` `event_type`** values + **explicit `easysynq_app` GRANTs** on the
  two tables (belt-and-suspenders over 0010's ALTER DEFAULT PRIVILEGES). **Pure domain**: `retention_until` (dependency-free
  ISO-8601 duration parser, day-clamp, `PERMANENT`/None-basis→None) + a `legal_disposition_transition` table. **Service
  `services/records/disposition.py`**: `advance_disposition` (PATCH state machine) · `place/release_legal_hold` ·
  `request/approve/cancel_worm_destroy` (R27) · `sweep_due_records` (Beat). **Fail-closed, purge-FIRST** ordering (idempotent
  `storage.purge_object` runs *before* the DISPOSED flip — never a tombstone over live bytes); a **pre-purge guard** logs
  `RECORD_ERASURE_REFUSED` + 409 when blocked by unexpired WORM / legal_hold / COMPLIANCE (the GDPR refused-with-reason);
  **dual-control** = `approver != requester` (409 + DB CHECK) + a `FOR UPDATE` re-check; only this path passes
  `BypassGovernanceRetention` (GOVERNANCE-only). New `storage.purge_object` (lists + deletes every version + delete-marker,
  off-loop). New **system-actor** `emit_record_event_system` (actor_id NULL — `canonical_serialize` already NULL-handles it,
  the `upgrade.py`/`backup` precedent). **Beat** `easysynq.records.retention_sweep` (daily, on the non-owner `database_url`
  role; `FOR UPDATE SKIP LOCKED`; per-record SAVEPOINTs; skips `RETAIN_PERMANENT`/null-basis/WORM-unexpired). **API** (all
  under `/records`, **immutability preserved**): `GET`+`PATCH /disposition`, `POST /legal-hold`, `GET`+`POST
  /worm-destroy-requests` + `…/{req_id}/{approve,cancel}`. The **route-inventory proof reframed** ("record *content*
  immutable; the disposition state machine advances" — whitelists `PATCH /disposition` like the evidence-link DELETE).
  Adversarially pressure-tested **before coding** (3-critic Workflow → folded: purge-first ordering, COMPLIANCE pre-check,
  DB CHECK + `FOR UPDATE`, delete-marker handling, partial-index alembic exclusion, populated-DB downgrade deletes, the
  shared-DB sweep test-isolation contract). **256 unit + 10 disposition integration green** (the 14 pg_dump-absent
  backup/restore tests stay environmental, green on CI); `0024` round-trips up↔down↔check **+ a populated-DB downgrade** on
  PG16; OpenAPI caught up in-PR (redocly green). **Deferred:** **S-rec-3** (Mode-B `form_template` structured capture),
  the `/retention-policies` CRUD (its `retention.*` keys aren't in the closed catalog), the
  `event:*` basis-date backfill (source HR/CAPA domains don't exist), ordinary creator≠disposer SoD; then the rest of v1
  (ingestion doc 09, workflows + notifications doc 10, CAPA/audit/finding/complaint/NCR entities, the rest of doc-13).

- **S-pack-1 — Evidence Packs (UJ-7): scope resolution + immutable build/seal** ✅ — PR #48, migration `0025` (the
  full non-obvious decisions live in the squash-merge commit + the project memory). The first of **two** pack PRs
  (owner forks via AskUserQuestion: **two PRs** · pack-specific **Celery worker job + status poll** · **first-class
  `evidence_pack` + `pack_item` tables** · Ed25519 time-boxed delivery → **S-pack-2**). A pack is an on-demand,
  scope-limited, **immutable, self-verifying** bundle of records + their evidence + a traceability manifest, sealed
  and registered as a **`RETAIN_PERMANENT` EVIDENCE Record**. **Migration `0025`**: `evidence_pack` header +
  `pack_item` membership + 4 enums + additive `PACK_GENERATED`/`PACK_BUILD_FAILED` `event_type` + an `evidence_pack`
  `audit_object_type` (both b-tree indexes → no `env.py` change). **Scope** = CLAUSE/PROCESS (+ DATE overlay), each a
  **UNION of two legs** (`evidence_for_link` AND records under a clause-mapped/process-linked source doc — records
  don't inherit their source-doc clause mappings); Finding/CAPA scope deferred (no entities). **R28 honesty** (load-bearing):
  every candidate runs the generator's deny-by-default `record.read` (**full `ResourceContext`** — process_ids +
  framework, so a PROCESS-scoped grant is honored) → `INCLUDED`/`EXCLUDED_PERMISSION`/`EXCLUDED_ABSENCE`; the
  `pack_item` table IS the exclusion report; **absence = a DESTROY/WORM-destroy disposition tombstone** (never "no
  evidence_blob rows" → a valid form-only record stays INCLUDED); the **gap** report reuses `compute_checklist`
  (org-wide rule, distinct), a PROCESS pack deriving its clauses transitively. **Build worker** (`services/packs/build.py`,
  `.delay`-triggered): single-txn, **idempotent** (`FOR UPDATE` + early-return if `pack_record_id` set — `acks_late`
  re-delivery safe), **fail-closed**; re-resolves + re-classifies and **atomically replaces** the preview `pack_item`
  rows (TOCTOU), seals over the content list with a domain-separated `pack_content_hash` (preamble
  `easysynq.evidencepack.v1`, NOT the ZIP bytes), writes the ZIP to the WORM `records` bucket + registers it via
  `capture_record`. **`evidence_pack` has NO FK to `blob`** (the ZIP is reached via `pack_record_id → evidence_blob`) so
  the pack's R27 WORM-destroy hatch never aborts. A daily **reaper** flips stalled `BUILDING` → `FAILED`. **Routes**
  (`api/packs.py`, gate `report.evidence_pack.generate` via SYSTEM override; download `report.export`): `POST
  /evidence-packs` (preview) · `GET` (list/poll) · `POST …/generate` (202) · `GET …/download`; **immutable** — the
  route-inventory proof asserts **zero PUT/PATCH/DELETE**. Pinned governing versions ride their cached rendition blob
  (else source bytes — a version always has one), so **no renderer dependency** in S-pack-1. Adversarially
  pressure-tested **before coding** (3-critic Workflow → folded: the blob-FK/`RETAIN_PERMANENT` pin, the full
  `ResourceContext`, TOCTOU replace-don't-merge, `acks_late` idempotency, tombstone-not-empty-blobs absence, transitive
  process→clause gap, the `evidence_pack` audit object_type). **268 unit + 4 pack integration green** (the 14
  pg_dump-absent backup/restore tests stay environmental, green on CI — they're also where the pack ZIP blob's
  blob-row-iff-bytes is truly exercised); `0025` round-trips up↔down↔check **+ a populated-DB downgrade** on PG16;
  OpenAPI caught up in-PR (redocly green). **Deferred (S-pack-2 / later):** Ed25519 time-boxed external delivery +
  revoke, ZIP/PDF export-format variants + live §11.3 stamping, Finding/CAPA scope.

- **S-pack-2 — Evidence Packs (UJ-7): external delivery + PDF portfolio (completes UJ-7)** ✅ — migration `0026` (the full
  non-obvious decisions live in the squash-merge commit + the project memory). **Owner forks (AskUserQuestion):** **Full
  UJ-7** scope (delivery + PDF portfolio + per-request live §11.3 stamping) · a **DB-backed `pack_share_link`** record for
  revocation/audit (NOT pure-stateless; the heavier `guest_grant` + `scope.evidence_pack_id` ABAC + Keycloak-guest identity
  stays **v1.x**). The **Ed25519 signed-token-outside-the-PEP** path (reuses `services/vault/verify_token.py`,
  **domain-separated** by a `easysynq.packshare.v1` preamble + a distinct 105-byte length so a verify token can never
  cross-validate as a share token, locked by a unit test; **fails closed** at mint via `verify_token.signing_key_is_persisted()`).
  **Migration `0026`**: `pack_share_link` (token_digest UNIQUE — the raw token is never stored; recipient/expires_at/
  revoked_at+by+reason/download_count; state DERIVED from nullable timestamps, the 0024 `worm_destroy_request` precedent) +
  a nullable `evidence_pack.portfolio_blob_sha256` (a derived rendition pointer, plain Text NO FK — the `zip_blob_sha256`
  R27 precedent) + additive `PACK_SHARED`/`PACK_DOWNLOADED`/`PACK_SHARE_REVOKED` `event_type` (reuse `evidence_pack`
  object_type) + explicit `easysynq_app` GRANTs. **Authenticated** (`api/packs.py`, gate `report.evidence_pack.generate` —
  the pack-management authority, catalog CLOSED): `POST …/share` (mint a link, raw token returned ONCE), `GET …/share-links`,
  `POST …/share-links/{id}/revoke`. **Public** (`api/pack_share.py`, **no auth, GET-only**, latch-exempt EXACT, mounted
  **before** the `/{pack_id}` router — the str-path-convertor lesson): `GET …/shared?t=` (an HTML landing surfacing the R28
  gap/exclusion summary) + `GET …/shared/download?t=&format=zip|pdf` — re-checks the **revocable** DB row on every access
  (revoke is immediate), audits `PACK_DOWNLOADED` (system-actor), and **streams** the bytes through the API (no presigned URL
  outlives a revoke) + `Referrer-Policy: no-referrer`, digest-only logging. **PDF portfolio** = build **Stage 2**
  (`services/packs/portfolio.py`, a SEPARATE txn after the seal commits, idempotent on `portfolio_blob_sha256`, best-effort
  so Gotenberg never blocks the seal): cover + traceability index + gap/exclusion pages + each version's **cached** §11.3-stamped
  rendition (a truthful pure-pypdf "no longer governs" overlay if its state changed; an honest placeholder if uncached — so
  the **API never invokes Gotenberg**), content-addressed into the non-WORM renditions bucket (blob-row-iff-bytes); the
  per-request guest stamp is `watermark.stamp_per_request_copy` (the S7d precedent). Adversarially pressure-tested **before
  coding** (4-lens Workflow → folded: seal-must-not-block-on-render → Stage-2-separate-txn, content-address-by-output-not-input,
  stream-not-presign for immediate revoke + per-access audit, landing surfaces R28, Referrer-Policy + digest-only logging,
  mint-fails-closed, domain-separation tests, the route-order shadow; rejected the "add `guest.*` keys / build guest_grant now"
  findings as contrary to the closed-catalog discipline + the owner's Q2). **281 unit + 7 pack integration green** (the 14
  pg_dump-absent backup/restore tests stay environmental, green on CI); `0026` round-trips up↔down↔check **+ a populated-DB
  downgrade** on PG16; OpenAPI caught up in-PR (redocly green). **Deferred (later):** the `guest_grant`/ABAC/Keycloak-guest
  path (v1.x), Finding/CAPA pack scope, `ip_allow` binding, app-layer rate-limiting (a Caddy edge concern). **Migration head
  is now `0026`.**

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

**S0–S7d — foundation + the mirror/rendering epic** ✅ (one line each; the full per-slice "non-obvious decisions" live in the squash-merge commits + the project memory `easysynq-project.md`):

- **S0** walking skeleton · **S1** AuthN (Keycloak OIDC/PKCE, JWT↔JWKS, `app_user` JIT, `GET /me`) · **S2** AuthZ (deny-wins PDP/PEP, the closed doc-07 96-key catalog + 8 seeded roles, the R35 two-tier grant guard).
- **S3** Vault (check-out → presigned CAS upload → immutable check-in; MinIO WORM + Redis lock; atomic `{TYPE}-{AREA}-{SEQ}` numbering) · **S4** Lifecycle **[AC#1]** (the doc FSM + the atomic SERIALIZABLE single-Effective cutover + the INV-1 partial-unique index) · **S5** Approval + SoD (`POST /tasks/{id}/decision` one-txn + append-only `signature_event` + the deny-wins SoD-1/2/3 gate).
- **S6** Audit **[AC#6]** (append-only, monthly-partitioned, hash-chained `audit_event` behind DB **role separation** [non-owner `easysynq_app`] + the decoupled chain-linker + frozen `canonical_serialize` + the off-host checkpoint anchor) · **S7** Mirror **[AC#2]** (RO Effective-only filesystem mirror, atomic symlink-repoint swap, mounted `:ro`) + **S7b/c/d** (watermarked-PDF rendering via Gotenberg + a deterministic reportlab/pypdf §11.3 band · Ed25519 verify-token + QR + public `GET /verify` · the per-request export/print stamp).

- **S8a–S8d — first-run setup + admin** ✅ (PRs #16/#18/#20/#22/#24): the **423 setup-latch** (ASGI middleware, boundary-anchored
  exemptions) + **bootstrap-of-trust** (`easysynq setup mint-bootstrap` → a 256-bit salted single-use secret → public
  `POST /setup/bootstrap` *outside* the PEP grants the first System Administrator, breaking the deny-by-default chicken-and-egg)
  + the extensible **gate registry** (`services/setup/service.py GATES`: G-A admin · G-B WORM-probe · G-C backup→restore-drill
  **[AC#5]** · G-D non-bootstrap-auth-proof · G-E org-profile) → the one-way `UNINITIALIZED→IN_SETUP→OPERATIONAL` finalize
  (migrations `0012`–`0016` seed `system_config` OPERATIONAL-iff-`role_assignment`-exists so upgrades aren't bricked). Then
  **Users & Roles admin** + invite/enable-disable (reuses the S2 authz-admin write API + R35 two-tier guard; last-admin
  lock-out guard; `INVITED→ACTIVE` JIT reconcile). `grant-role` stays break-glass. A Mantine `<Stepper>` wizard fronts it;
  S8c added the first `react-router-dom`.

- **S9/S9b/S9c/S9d — the two IA backends + the mirror tree** ✅ (PRs #27/#31/#32/#33): the read-only ISO 9001 **clause
  spine** (the **83-clause / 20★** catalog in `db/seeds/iso9001_clauses.py`, drafted+adversarially-verified against doc 02)
  + M:N `clause_mapping` + the headline **submit-needs-≥1-mapping gate** (`submit_review` → 422, counted on the DOCUMENT so
  a T9 revision inherits mappings) via `0017`/`0018`; the **process graph** (`process`/`process_edge`/`process_link` +
  empty-but-present `org_role`/`supplier` FK targets, SEED→ACTIVE one-way ratchet, `0019`); and the §10.3 mirror rebuilt
  into the `{PLAN|DO|CHECK|ACT}/{NN-Name}/` **clause tree** + a `by-process/` secondary index (pure `services/vault/mirror.py`,
  relative symlinks so real bytes live **once**, no migration — head stays `0019`). **Authz reality:** the seeded
  `process.create`/`.read`/`.manage` grants reach no *concrete* process (an unsubstituted `:assignment_process`
  placeholder), so authoring rides on **SYSTEM overrides** until owner-assignment (the `document.export` precedent).
- **OpenAPI catch-up** ✅ (PR #35) — `packages/contracts/openapi.yaml` caught up through S9c (the `contracts` CI is
  redocly-lint only — no codegen, server+web not generated from it); **document new endpoints in-PR going forward**.

- **S10 — search/reporting backend** ✅ (PR #38, owner-scoped to backend, NO web): the org-wide **Compliance Checklist**
  `GET /reports/compliance-checklist` (the 20★ clauses → per-clause COVERED/PARTIAL/GAP + rollup, one grouped query, PG-only)
  + Postgres-FTS **search** `GET /search(/suggest)` behind an engine-agnostic `Indexer` seam (OpenSearch is the v1 drop-in,
  R34; **Effective-docs-only** + **filter-not-403** post-filter with a `hidden_by_scope` footer) + `clause_refs` and the
  doc-15 bracketed `filter[field][op]` grammar on `GET /documents` (`0020` functional GIN index). `0021` backfills the
  checklist read onto Internal Auditor. **PROOF:** the audit read API exposes no write verbs (route-inventory test, co-proves **AC#6**).
- **S11 — the MVP EXIT slice** ✅ (PR #41): operator-grade **`easysynq restore`** (`services/backup/restore.py`, runs as
  OWNER, never raises) — WORM-aware **restore-to-VERIFIED-TARGET** (fresh scratch DB + fresh non-WORM `restore-scratch`
  bucket; the locked vault is READ-never-written) → integrity triad → checkpoint-not-ahead vs the *restored* head → chain
  re-verify → leaves a standing target for a **documented manual cutover**; **`easysynq upgrade`** (pre-backup → migrate →
  health-gate); **backup archive v2** (AES-256-GCM `.tar.enc` + Keycloak realm export, both **only-if-encrypted** so the
  G-C drill stays plaintext and AC#5 isn't regressed); strict static **Caddy CSP** scoped to the SPA `handle{}` + default
  TLS 1.2 floor; 9 operator runbooks (`docs/runbooks/`); a `conftest.py` dir auto-marker closing a real `-m unit` CI gap.
  `0022` adds 8 `RESTORE_*`/`UPGRADE_*` `event_type` values (`canonical_serialize` v1 untouched).

**MVP EXIT: complete.** All 11 ordered slices (S0–S11) shipped; all six acceptance proofs in; the mirror epic + both IA
backends complete; the exit checklist (doc 18 §12) closed. **Deferred (S8e / v1 / Part-11):** the doc-14 `storage_config.mirror_layout` toggle (with its config UI);
**owner-assignment** (`org_role_assignment` + concrete PROCESS-scope grants → real Process-Owner authoring) +
`/org-roles`/`/suppliers` authoring (v1); the **web** Compliance-Checklist + Admin Audit-Log screens + clause-spine nav +
mapping UI + process-map UI; the rest of doc-13 search/reporting (faceted facet-rail, saved searches, dashboards, the
canonical reports, evidence packs, find-where-used, content-plane/body-text FTS, the `{data,page,_links}` cursor envelope,
subtree clause rollup, the checklist's "overdue review"/"linked evidence" legs [need `next_review_due`/records], R31
scope-conditional coverage); wizard Step 8 (scope/process-map seed → SEED nodes) + Step 9 (import → the v1 ingestion
epic); custom-role create/update/delete + bulk-CSV invite + the effective-permissions explorer (v1); in-app Keycloak
admin-API provisioning (v1); MFA *enforcement* + `acr`/step-up (Part-11, D3); the §10.4 self-grant friction +
`ADMIN_SELF_GRANTED_QMS_CAP` event (v1). **Deferred (v1.x, D-6 / R37) — the residuals S11 explicitly did NOT ship:**
continuous **PITR/WAL**, retention **pruning**, **S3/cloud backup destination**, **automated in-place live cutover**
(restore-to-verified-target + a documented manual cutover ships; automation is the hardening TODO noted in
`restore.py`/`upgrade.py`), **per-request nonce-CSP** (strict static CSP ships; nonce needs SPA HTML-nonce injection —
web track), **COMPLIANCE object-lock mode** (GOVERNANCE ships, D-7). S6/S7 seams still open (Keycloak auth-event SPI,
`/audit-events/export` async-export job). Pre-existing hardening noted: `area_code` is unconstrained `Text` at the S3
create boundary.

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
  `${MIRROR_PATH}/current/` — **S9b** organizes it as the doc 04 §10.3 **`{PLAN|DO|CHECK|ACT}/{NN-Name}/`** clause tree
  (a doc lives once under its numerically-lowest mapped clause + a relative symlink from every other mapped clause folder;
  a zero-mapping upgrade artifact lands in `_unmapped/`). Plain `sync` rebuilds the whole tree, so the flat→tree
  migration needs no `rebuild` (which only forces re-render). The files are **watermarked controlled-copy PDFs** (S7b:
  gotenberg `renderer` is live; office→PDF + the §11.3 band + a verify QR) with each footer carrying a signed verify token. **S7c `.env` additions (already in
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
  until it PASSES (G-C / AC#5); (3.7 — **S8c**) **Authentication**: pick a login method + ack MFA, then **Verify
  authentication** (G-D — a non-bootstrap login proof + an OIDC-issuer reachability probe); (4) **Finalize** flips
  `→ OPERATIONAL` and the latch lifts (all five gates G-A…G-E now satisfied). After an **upgrade of a
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
- **Users & Roles admin (S8d) — the primary in-app path now:** once OPERATIONAL, sign in as a System Administrator
  and open **`/admin/users`** to invite users (paste their Keycloak `sub` — create the Keycloak account out-of-band
  first; they go `INVITED`→`ACTIVE` on first login), assign/revoke the seeded roles, add/remove per-user overrides
  (the R35 two-tier guard applies), and enable/disable accounts (the last active admin can't be disabled). `/admin/roles`
  is a read-only view of the seeded bundles. (Custom-role authoring, bulk-CSV invite, and in-app Keycloak provisioning
  are v1.)
- **Clause IA + mapping (S9) — no UI yet (API/data only):** a fresh/upgraded install now carries the read-only
  ISO 9001:2015 clause spine (seeded by `0018`; **no operator action**). `GET /api/v1/clauses` lists it (gate
  `clauseMap.read`, held by QMS Owner + Internal Auditor — grant it via override for others until the clause-nav UI
  lands). A document must be mapped to **≥1 clause before `submit-review`** (else **422**) — map via
  `POST /api/v1/documents/{id}/clause-mappings {clause_id}` (gate `document.manage_metadata`, held by the lifecycle
  actors), unmap via `DELETE …/clause-mappings/{clause_id}`. Both audited (`CLAUSE_MAPPED`/`CLAUSE_UNMAPPED`). The
  clause-spine nav + mapping UI are deferred (web).
- **Process IA (S9c) — API/data only, no UI:** `GET /api/v1/processes(/{id})(/map)` read the Clause 4.4 process graph
  (gate `process.read`, held at SYSTEM by QMS Owner + Internal Auditor). Authoring — `POST`/`PATCH /processes` (confirm
  `SEED→ACTIVE`), `POST`/`DELETE /processes/{id}/edges`, and `POST`/`DELETE /documents/{id}/process-links` — is gated on
  `process.create`/`process.manage` (the first **held by no seeded role** → grant via override until the role UI, like
  `document.export`) and `document.manage_metadata` for links. `org_role`/`supplier` tables exist but have no authoring
  endpoint yet (owner-assignment + supplier population are deferred). **S9d** then mirrors the links: a process-linked
  Effective doc shows up under `${MIRROR_PATH}/current/by-process/{ProcessName}/` (relative symlinks into the clause tree;
  plain `mirror sync` builds it).
- **Search + Compliance Checklist (S10) — API/data only, no UI:** the org-wide **Compliance Checklist** is
  `GET /api/v1/reports/compliance-checklist` (gate `report.compliance_checklist.read`, now held by **QMS Owner +
  Internal Auditor** after `0021`) — the 20 ★ mandatory clauses with per-clause **COVERED/PARTIAL/GAP** coverage + a
  rollup, computed from PostgreSQL. **Search** is `GET /api/v1/search?q=…` + `GET /api/v1/search/suggest?q=…`
  (authenticated; **filter-not-403** — results post-filtered by `document.read`, with a `hidden_by_scope` count; **over
  Effective documents only**, doc 13's "Effective only" default). Postgres-FTS behind the `Indexer` seam — **OpenSearch
  stays omitted in MVP dev** (R34); `/readyz` must not probe it. `GET /api/v1/documents` now carries `clause_refs` and
  accepts the doc-15 bracketed filters (`filter[clause_refs][has]=8.4`, `filter[current_state][eq]=…`, etc.; unknown →
  400 `unknown_filter`). The web Checklist dashboard + Admin Audit-Log screen + the rest of doc-13 (facets, saved
  searches, dashboards, reports, evidence packs) are deferred.
- **Records & evidence (S-rec-1) — API/data only, no UI:** capture an **immutable** record with
  `POST /api/v1/records:init-upload` (presign evidence to the WORM `records` bucket) → `POST /api/v1/records`
  (`{record_type, title, evidence:[{sha256}], source_document_id?, source_version_id?, …}`, gate `record.create`).
  All 16 `record_type` values are accepted. A record produced under a controlled document **must** pin
  `source_version_id` (R21 → 422 `source_version_required`); ad-hoc `EVIDENCE` leaves both source fields null. Read
  with `GET /api/v1/records(/{id})` (gate `record.read`, row-filtered) + `GET …/{id}/evidence/{sha}/download`.
  **Correct** (never edit) via `POST …/{id}/correction` (a new record `correction_of`→old; 409 if already
  superseded). **Link** as evidence-for a clause/process/document via `POST/GET/DELETE …/{id}/evidence-links` (gate
  `record.create`). **Authz:** the `record.*` write keys are seeded but reach no folderless/processless record at
  their seeded scope → **grant `record.create`/`record.read` via a SYSTEM override** until a role/UI wires them (the
  `process.create` precedent). Evidence bytes that already exist in another bucket (a rendition, or the documents
  vault) are **rejected 423** — a record's evidence must be freshly WORM-sealed in the `records` bucket (or link to
  that document instead). Retention is **policy-as-data** (a seeded per-org *System Default* + a 5-tier resolver +
  the snapshot-at-capture ratchet). **No web.**

- **Records disposition lifecycle (S-rec-2) — API/data only, no UI:** the retention end-of-life. `GET
  /api/v1/records/{id}/disposition` (gate `record.read`) shows state + `retention_until` + `legal_hold` + the open
  destroy request + the tombstone history. **Advance** the state machine with `PATCH …/{id}/disposition
  {to_state,reason?}` (gate `record.dispose`; `ACTIVE↔DUE_FOR_REVIEW↔DISPOSED`; a DESTROY physically removes the WORM
  bytes **fail-closed**, blocked 409 + audited `RECORD_ERASURE_REFUSED` while the lock is unexpired or a hold is on).
  **Legal hold** via `POST …/{id}/legal-hold {action:place|release, reason}` (gate `record.dispose`; reason mandatory;
  overrides expiry). The **R27 dual-control destroy-under-legal-order**: `POST …/{id}/worm-destroy-requests
  {legal_basis}` (step 1) → `POST …/{req_id}/approve` by a **distinct** second actor (step 2 — governance-bypass purge;
  409 `dual_control_same_actor`, 409 `compliance_mode_denies_destroy`) or `…/cancel`. The **Beat** sweep
  (`easysynq.records.retention_sweep`, daily) flips due `ACTIVE`→`DUE_FOR_REVIEW` and **auto-disposes** low-risk
  (`review_required=false`) policies once the WORM lock allows; `review_required=true` waits for a human. Records stay
  **immutable** — `PATCH /disposition` is the only PATCH (a state advance, not a content edit; the route-inventory proof
  whitelists it). Authz: ride on a **SYSTEM `record.dispose` override** (catalog CLOSED — legal-hold + dual-control both
  map onto `record.dispose`).
- **Evidence Packs (S-pack-1) — API/data only, no UI:** assemble an immutable audit bundle. `POST
  /api/v1/evidence-packs {title, scope_kind:CLAUSE|PROCESS, clause_ids|process_ids, period_start?, period_end?}` (gate
  `report.evidence_pack.generate`) creates a **DRAFT** pack + computes its preview synchronously (resolve candidates +
  R28-classify `INCLUDED`/`EXCLUDED_PERMISSION`/`EXCLUDED_ABSENCE` + gap/exclusion summaries). `POST
  /evidence-packs/{id}/generate` (202) flips `→ BUILDING` and enqueues the worker build; **poll** `GET
  /evidence-packs(/{id})` for `SEALED` (or `FAILED`). `GET /evidence-packs/{id}/download` (gate `report.export`) presigns
  the sealed ZIP (409 until SEALED). The sealed pack is a **`RETAIN_PERMANENT` EVIDENCE Record**; the pack is immutable
  (**no PUT/PATCH/DELETE** — the route-inventory proof). **Authz:** ride a **SYSTEM `report.evidence_pack.generate`
  override** until the role UI (the `record.*` precedent; catalog CLOSED — no new key needed). The build runs on the
  **worker** (the `build_evidence_pack` Celery task; a daily `easysynq.packs.reap_stalled_builds` Beat reaper recovers a
  stalled `BUILDING`).
- **Evidence Packs delivery (S-pack-2) — API/data only, no UI:** deliver a sealed pack to an external auditor.
  `POST /api/v1/evidence-packs/{id}/share {ttl_days?|expires_at?, recipient?}` (gate `report.evidence_pack.generate`;
  pack must be SEALED → 409 else; 503 if the verify-token signing key isn't provisioned) mints a time-boxed Ed25519
  **share link** and returns the raw token + `share_url` **once** (only its SHA-256 digest is stored). `GET …/share-links`
  lists them (management view, digest prefix only); `POST …/share-links/{link_id}/revoke {reason}` is immediate (409 if
  already revoked). The **public, no-auth, latch-exempt** guest surface: `GET /api/v1/evidence-packs/shared?t=<token>`
  (an HTML landing surfacing the R28 gap/exclusion summary + download links) and
  `GET …/shared/download?t=<token>&format=zip|pdf` — re-checks the **revocable** DB row on every access (revoke is
  immediate), audits `PACK_DOWNLOADED` (system-actor), and **streams** the bytes through the API (`Referrer-Policy:
  no-referrer`; the raw token is never logged). `format=pdf` is the live-stamped **PDF portfolio** (built at seal Stage 2,
  best-effort; 409 if unavailable). The pack **content** stays immutable (no PUT/PATCH/DELETE; the route-inventory proof
  whitelists share/revoke as delivery-grant lifecycle). The verify signing key is shared with S7c
  (`VERIFY_TOKEN_SIGNING_KEY_PATH` on the secrets volume — already provisioned for the mirror QR). The heavier
  `guest_grant`/ABAC/Keycloak-guest path stays **v1.x**; Finding/CAPA scope + `ip_allow` + app-rate-limiting deferred.
  Migration head is now `0026` (next `0027`).
- **⚠ S11 restore + upgrade + encrypted backup (operator):** the durable archive (`easysynq backup run` / the nightly
  Beat job) is now **AES-256-GCM `.tar.enc`** sealed with `BACKUP_ENCRYPTION_KEY` (install.sh generates it into the
  0600 `.env`; **lose it → those archives are unrecoverable** — back it up out-of-band) and bundles the live Keycloak
  realm export (worker → Keycloak Admin REST; degrades to `absent` on a Keycloak outage) + a config snapshot **only when
  encrypted**. `easysynq restore <archive> --confirm` does a WORM-aware **restore-to-VERIFIED-TARGET** (fresh scratch DB +
  fresh `restore-scratch` bucket; **never touches the locked vault**) + the checkpoint-not-ahead tamper check + a chain
  re-verify, then **leaves a standing target** — the production **cutover is a documented manual step**
  (`docs/runbooks/backup-restore.md`); exit 3 = FLAGGED (re-run with `--audit-checkpoint-ack`, audited). `easysynq
  restore --discard <db>` reclaims a target (both DB + blobs). `easysynq upgrade --confirm` = pre-backup → migrate →
  health-gate. Both run on the **worker** (OWNER `DATABASE_URL_SYNC` + pg client). Caddy now sets a strict static CSP +
  the default TLS 1.2 floor; the air-gap overlay sets `CADDY_TLS_DIRECTIVE="tls internal"` + a hostname `SITE_ADDRESS`.
  Operator runbooks live in **`docs/runbooks/`**. The full operator-grade live cutover (auto-repoint) + PITR/WAL +
  retention pruning + S3 destinations are the explicit **v1.x** residuals.
- **Authz break-glass (`grant-role`):** still available to assign a seeded role directly, bypassing the wizard +
  PEP — `easysynq grant-role <keycloak-subject> ["Role Name"]` (default "System Administrator"; idempotent;
  JIT-creates the `app_user`; runs `easysynq_api.cli.grant_role` as the DB owner). Use it to recover a botched
  bootstrap or to seed the first admin before the UI is reachable.
- **No Docker?** Every slice is still buildable + unit-testable on the uv/3.12 loop; CI runs the stack-dependent
  proofs.

## Recurring engineering patterns (learned across slices)

> The deep per-slice rationale lives in the squash-merge commits + the `easysynq-project.md` memory. These are the
> patterns that keep recurring — apply them by default on the next slice.

- **Extending an enum** (`event_type`, `audit_object_type`): `ALTER TYPE … ADD VALUE` is the additive pattern (no-op
  downgrade), since 0011. Add the matching Python member. **Source the migration's enum tuples from the ORM `*_VALUES`**
  (the 0010 precedent), not a hand-retyped list.
- **Guard a downgrade seed-delete with `NOT EXISTS(<child>)`** when a child FK is `RESTRICT` — else the downgrade aborts
  on a *populated* DB (a fresh-DB CI blind spot; the 0023 lesson).
- **Name join-table FKs explicitly** — the convention default can exceed **PG's 63-char identifier limit** (clause_mapping/process_link).
- **`alembic check` must be clean.** This Alembic version **does reflect expression/functional indexes**, so exclude them
  from autogenerate in `migrations/env.py._include_object` (the 0020 GIN-index lesson). Round-trip up↔down↔check on a throwaway PG16.
- **Backup/restore drills run as the OWNER role** (`DATABASE_URL_SYNC`; the app role can't `pg_dump`/`CREATE DATABASE`)
  and **never raise** — a missing binary/crash is an honest FAIL, never a 500.
- **Run the FULL integration suite for mirror/symlink work** — Py3.12 `rglob` follows symlinks, so dir-finders must filter
  `not is_symlink()` and byte-scans use `os.walk(followlinks=False)`; cross-file test pollution only surfaces in the full run.
- **Keep the `blob`-row-iff-bytes invariant** (the S-rec-2 lesson, found by CI not local since the restore tests are
  pg_dump-gated): any path that physically deletes object bytes (the WORM-destroy / sweep DESTROY) MUST also drop the
  `blob` row + its `evidence_blob` links — else the backup manifest + restore drill (`_copy_blobs`/`_rehash`) iterate
  **all** `blob` rows and crash `NoSuchKey` on the dead one (after the first disposal, every backup/restore breaks). A
  destroyed record's tombstone is the `disposition_event` + the record `content_hash`, not a dangling `blob` row.
- **Review rhythm:** N adversarial lenses → per-finding verify → fold only confirmed. Prefer hunting the *false-PASS*
  direction on any gate/proof.
- **Authz for not-yet-UI'd domains:** seed the permission keys but expect them to reach no concrete object at their seeded
  scope → ride on **SYSTEM overrides** until the role/UI lands (the `document.export`/`process.create`/`record.*` precedent).
- **Reusing the row-filter for a new permission-gated listing** (`gather_grants` + `authorize`, the search/records
  pattern): populate the **FULL `ResourceContext`** the resource is actually granted on (process_ids + framework, not just
  artifact_id + folder_path), or a genuinely PROCESS/FOLDER-scoped grant silently mis-denies everything (the S-pack-1 R28
  lesson). SYSTEM overrides mask this — the EXCLUSION/visibility fact must be correct regardless.
- **A blob registered under a record that can later be disposed must NOT carry a RESTRICT FK from a sibling row to that
  `blob`** — the R27 WORM-destroy / sweep purge calls `delete_blob_and_links`, and a RESTRICT FK aborts the legal erasure
  (a 500, not the refused-with-reason). Reach the bytes via `…_record_id → evidence_blob → blob` instead (the S-pack-1
  `evidence_pack.zip_blob_sha256`-is-plain-Text lesson). Pin a never-disposed artifact (e.g. a sealed pack) `RETAIN_PERMANENT`.
- **A `.delay`-triggered Celery build must be idempotent** (`task_acks_late=True` re-delivers on a worker kill): `FOR UPDATE`
  + early-return if the terminal pointer is already set, do the whole build in ONE transaction (a crash before commit
  leaves zero PG side effects; content-addressed writes dedup on re-run), and add a Beat **reaper** for a hard-killed
  `BUILDING` row (no self-healing set-sweep like records). Register the task module in `tasks/__init__.py` (+ a unit test
  asserting it's in `app.tasks`) or `.delay` publishes to a name no worker handles and the row hangs forever.
- **A static route alongside a `/{id}` route MUST be mounted FIRST** (the S-pack-2 lesson): FastAPI compiles a path param
  like `{pack_id}` with the **str** path-convertor and validates the UUID *after* matching — so `/evidence-packs/shared`
  resolves to the authenticated `/{pack_id}` route (→ 401) unless the public `/shared` router is `include_router`'d **before**
  the `/{pack_id}` router. A real UUID never matches the `shared` literal, so ordering is safe. Add a resolution unit test
  (`app.router.routes` + `route.matches(...)`) — a route-inventory test on a single router won't catch a cross-router shadow.
- **A public, no-auth bearer-token route** (a signed token outside the PEP — the S7c `/verify` + S-pack-2 share-link pattern):
  put it in its own router (GET-only, **no `get_current_user` dependency** — proven by a unit test), add its EXACT path to
  `main.py::_LATCH_EXEMPT_EXACT` (boundary-anchored, never a prefix), **never log the raw token** (digest only), set
  `Referrer-Policy: no-referrer`, and **stream** revocable content through the API (a presigned URL outlives a revoke).
  Revocation needs server state (a self-contained token can't be un-issued) — a DB row checked on every access is the audit-
  first answer. Reuse the Ed25519 key but **domain-separate** (a distinct preamble + a distinct token length) and **fail
  closed** at mint if the key isn't durably persisted (`verify_token.signing_key_is_persisted()` — an ephemeral-key token
  stops verifying after a restart).

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

## Stakeholder decisions (locked)

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
