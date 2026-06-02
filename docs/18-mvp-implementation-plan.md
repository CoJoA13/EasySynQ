# EasySynQ — MVP Implementation Plan (for approval)

> **Status: APPROVED (2026-05-31) and IN BUILD.** Slices **S0–S7 + S7b + S7c are shipped to `main`** (each via PR,
> all CI green, validated on the real Docker stack); **S7d (in-app export/print stamp) then S8 (setup wizard) are
> next.** **S7c** added the controlled-rendition verify token (doc 05 §6.4, zero-migration): an Ed25519-signed
> `{document_id, version_id, content_digest}` token (reusing the `checkpoint.py` pattern) + a `segno` QR in the
> watermark footer, and a **public `GET /verify`** (unauthenticated, minimal disclosure; edge rate-limiting is a v1 add)
> returning **CURRENT / SUPERSEDED / UNKNOWN** — so any printout/export's currency is checkable without an account
> (R11 boundary). The token is deterministic (Ed25519 + immutable claims) so the rendition stays content-addressed;
> `easysynq mirror rebuild` force-re-renders existing renditions to carry it; the verify key is shared api↔worker via
> a `secrets` volume. (It is an integrity token, **not** a Part-11 e-signature — D3 stays reserved.) Earlier:
> S7 shipped the read-only Effective-only filesystem mirror (AC#2, zero-migration): full-rebuild +
> symlink-repoint atomic swap (`current → .builds/<uuid>`), post-commit release/obsolete enqueue + nightly Beat
> reconcile + `python -m easysynq_api.cli.mirror sync`, a flat layout (the clause/process IA tree defers to **S9**
> with `clause_mapping`), and the api `:ro` mount completing the R11 contract. **S7b** then made the `RenderSink`
> real (zero-migration): Gotenberg office→PDF (LibreOffice/Chromium; PDF passthrough) + a deterministic
> reportlab+pypdf overlay stamps the §11.3 non-removable band (Rev + Effective + copy_status) + the diagonal
> CONTROLLED COPY watermark, cached content-addressed in the non-WORM renditions bucket (`document_version.
> rendition_blob_sha256`) — rendered Effective-only inside the mirror sink, three-way `RenderResult`
> (RENDERED / R26 `no_controlled_rendition` / PENDING self-heal), license-safe BSD-only (NO PyMuPDF/AGPL), plus
> `GET /documents/{id}/download`. **Deferred to S7c** (owner decision): the verify-token + QR + public `GET /verify`
> (CURRENT/SUPERSEDED/UNKNOWN) + the per-intent export/print stamp. The drift scan/quarantine/`MIRROR_DRIFT_DETECTED`
> alarm stay **v1** (D-6). This document is the build guide; the
> Decisions Register remains authoritative where they differ, and a few canon reconciliations were made during the
> build (see CLAUDE.md "Current status" and the per-slice memory for the exact decisions, e.g. `documented_information`
> collapse, `role_grant`/`role_assignment` naming, the INV-1/R25 partial indexes (built in S4 with `::enum`-cast
> predicates to keep `alembic check` clean), folder_path-as-text, the S4 permission-key reconciliation noted in §1,
> and the **S6 DB role separation** — the app/worker/beat run as a non-owner `easysynq_app` role + a dedicated
> `easysynq_linker`, the structural foundation that makes the append-only audit grant actually bite (AC#6a)).
> This turns the `16-roadmap.md` **MVP ("The Controlled Vault, Proven")** into a concrete build plan:
> repo/monorepo layout + tooling, the Docker Compose dev stack, the Alembic schema derived from
> `14-data-model.md`, the FastAPI/OpenAPI surface from `15-api-design.md`, the ordered vertical
> slices, and a definition-of-done per slice. It stays strictly inside the four locked decisions
> (D1–D4) and the **Decisions Register (R1–R37)**, which supersedes any conflicting section text.
>
> **How it was produced.** A fan-out of deep readers over the MVP-critical sections (03/04/07/08/11/12,
> plus first-hand reads of 14/15/16) → eight per-area sub-plans → three adversarial critics
> (token/decision consistency, completeness, consolidated decisions). The critic pass found and this
> plan corrects a cluster of canonical-token and endpoint-shape errors before they could harden into
> the wrong schema/enums. Where the build-time "canon" diverged from the authoritative docs, **doc 14
> (data model) and doc 15 (API) win** per the authority-precedence rule.

---

## 0. Reading guide & authority

- **§1** records the canon corrections the adversarial pass forced (so a future reader trusts the tokens).
- **§2** repo/tooling · **§3** Compose dev stack · **§4** Alembic schema/migrations · **§5** FastAPI/OpenAPI ·
  **§6** frontend · **§7** ordered vertical slices + DoD (the centerpiece) · **§8** testing/CI ·
  **§9** ops foundations · **§10** reserved-hooks ledger · **§11** decisions for the owner · **§12** exit checklist.
- Canonical tokens (lifecycle states, `signature_event.meaning`, permission keys, scope levels, field/entity
  names) are **verbatim** from the Decisions Register and docs 14/07/15. Do not soften or rename them.

---

## 1. Canon corrections applied (verified against docs 14 / 15 / 07)

These were mis-stated in the working drafts and are fixed throughout this plan. They matter because the
vault ships with **FK `ON DELETE RESTRICT` and no soft-delete**, so a wrong token frozen into the Alembic
seed or the OpenAPI enums becomes a *destructive* migration later.

| # | Wrong (draft) | Correct (authoritative) | Source |
|---|---|---|---|
| C1 | One `document_state` enum for both version and document | **Two enums.** `version_state` = 6 values `Draft, InReview, Approved, Effective, Superseded, Obsolete`; document-level `current_state` = 7-value superset adding **`UnderRevision`** (a *derived* document state — a new Draft version exists while an Effective version still governs). A *version* is never literally `UnderRevision`. | doc 14 §5.2/§5.3; R1 |
| C2 | `document_version.state`; index `ON document_version(documented_information_id)`; `version_label`; `document_lock(scratch_blob_id, expires_at)`; `clause_map`/`process_links`/`process_clause` | `document_version.version_state`; INV-1 index `ON document_version(document_id) WHERE version_state='Effective'`; `revision_label`; check-out entity is **`working_draft`** (`scratch_blob_ref`, `lock_ttl`); joins are **`clause_mapping`** / **`process_link`** | doc 14 §5.2–5.6, §15.1 |
| C3 | Singleton index labelled "INV-2" | **INV-1** = single-Effective partial unique index. **INV-2** = released-versions-immutable (enforced by stripped UPDATE/DELETE grants, *not* an index). The **singleton** rule is **R25** via `is_singleton` — not numbered INV-2. | doc 14 §5.4 |
| C4 | Per-org monotonic `seq` column + advisory-lock BEFORE-INSERT trigger on `audit_event` | **No `seq` column, no trigger.** PK is `bigint GENERATED ALWAYS AS IDENTITY` only; the **gap in the identity is itself the tamper signal**. The advisory-lock trigger would re-introduce the exact write-path contention R12's decoupled chain was built to avoid. | doc 14 §12; doc 12 §4.2; R7/R12 |
| C5 | `framework_id NOT NULL` on **every** table | `org_id NOT NULL` on every table (correct). `framework_id NOT NULL` **only** on `documented_information`, `clause`, `clause_mapping`, and the authz `scope` predicate. On `audit_event`/`blob`/`permission`/`role`/`working_draft` it is meaningless and absent. | doc 14 §15.3 |
| C6 | Permission keys `audit.{plan,conduct,finding,read}`, `evidencepack.{generate,read}`, `record.link_evidence` | Doc 07 catalog: **`audit.{read,plan,conduct,close}`** + a **separate** `finding.{create,read,link_capa}` namespace; the evidence-pack key is **`report.evidence_pack.generate`**; there is **no** `record.link_evidence` (linking uses the `evidence_for_link` entity). | doc 07 §3.4/§3.5/§3.8/§3.10 |
| C7 | A `/documents/{id}/transitions/{name}` endpoint family; standalone `document.approve` transition route | Doc 15 uses **flat action sub-resources** (`POST /documents/{id}/{checkout,checkin,submit-review,release,start-revision,obsolete}`, `POST /documents/{id}/versions:init-upload`). **Approval/review is routed through `POST /tasks/{id}/decision`** (which writes the `signature_event` + `task_outcome` + `audit_event` in one txn). MVP therefore must ship the *minimal* `workflow_definition`/`workflow_stage`/`workflow_instance`/`task`/`task_outcome` cluster. | doc 15 §8.5–8.8 |
| C8 | Permission catalog treated as "unsettled / reconcile a superset"; invented keys `document.force_checkin/revise/release_now` | The v1 catalog is **CLOSED and already fixed** by doc 07 §3.1 + doc 14 §3.1: `document.{read,read_draft,read_obsolete,create,checkout,edit,submit,review,approve,release,obsolete,delete_draft,manage_metadata,acknowledge,print_controlled,export}`. Check-out **and** check-in are both gated by `document.checkout`; "edit/check-in a draft" by `document.edit`; break-lock by `document.checkout` (or one explicit new key via migration). Map legacy spellings at seed time per R5. | doc 07 §3.1/§3.10; R5 |
| C9 | Authz as a single flat `permission_grant` table | Doc 14 §3 specifies a **richer, already-reconciled** model: `role` + `role_grant` + `role_assignment` + `permission` + `permission_override` (carries `valid_from/valid_until/predicates/require_reason`) + `scope` (ABAC predicates) + `delegation` + `guest_grant` + `sod_constraint`. The "missing time-box columns" the drafts flagged already exist here. **Build the doc-14 model** (see §11 D-2 for the MVP reduction). | doc 14 §3 |
| C10 | `change_reason` the only mandatory check-in field | **INV-3** requires **both** non-empty `change_reason` **and** `change_significance` (enum `MAJOR`/`MINOR`) at check-in, else 422. | doc 14 §5.3; doc 15 line 337 |
| C11 | docs/11 cited as UI source-of-truth | **docs/11 is the one section NOT back-propagated by the reconcile pass** (per docs/17): it still carries the old 8-step wizard, 24h lock, 5-state palette. Use **R4+doc 08** (10-step wizard), **R24** (8h lock), **R1** (7-state). The Register supersedes docs/11 wherever they conflict. | docs/17; R1/R4/R24 |
| C12 | doc-15 §8.5 lists `document.edit` for `submit-review` and `document.checkout + document.revise` for `start-revision` | **Surfaced + fixed in the S4 build.** `submit-review` uses **`document.submit`** (doc-07 has the dedicated key; doc-04 T2/T9 uses it — doc-15 §8.5 corrected). `start-revision` uses **`document.edit`** — there is **no `document.revise`** key in the closed catalog (per C8), and the lock is acquired mechanically. The six S4 actions therefore use doc-07-catalog keys verbatim: submit/approve/review/release/edit/obsolete. | doc 07 §3.1; doc 04 T2/T7/T9; doc 15 §8.5 (corrected) |

---

## 2. Repository layout & tooling

A single Git **monorepo** (single-org product, D1). The **contract is the spine**: `openapi.yaml` is hand-authored
(OpenAPI 3.1) and both the FastAPI server models and the TS client are generated from it; CI fails on drift (D4, doc 15).

```
easysynq/
├── justfile  .pre-commit-config.yaml  .env.example  VERSION  CHANGELOG.md
├── packages/contracts/            # OpenAPI-first source of truth (doc 15)
│   ├── openapi.yaml               # hand-authored 3.1 root
│   ├── paths/                     # split per resource: setup, session, users, roles,
│   │                              #   permissions, documents, versions, locks, content,
│   │                              #   tasks, clauses, processes, audit-events, search
│   ├── components/{schemas,responses,parameters}/   # incl. RFC9457 problem catalog
│   ├── redocly.yaml               # lint + bundle
│   ├── dist/openapi.json          # GENERATED bundle (gitignored)
│   └── .contract.lock             # sha256 of dist/openapi.json (the CI drift gate)
├── apps/
│   ├── api/                       # FastAPI / Python 3.12 (uv)
│   │   ├── pyproject.toml  uv.lock  Dockerfile  alembic.ini
│   │   └── src/easysynq_api/{main,config}.py
│   │       ├── api/v1/  api/setup/         # routers (thin); setup/* pre-finalize only
│   │       ├── _generated/                 # GENERATED Pydantic models (gitignored)
│   │       ├── domain/                     # FSM, PDP (pure fn), supersession txn
│   │       ├── services/                   # use-case layer; PEP lives here
│   │       ├── db/  auth/  audit/  storage/  tasks/
│   │   └── tests/{unit,integration,contract}/
│   └── web/                       # React/TS + Mantine + Tailwind (Vite, pnpm)
│       └── src/{theme,api/_generated,lib,components,features,routes}/
├── infra/
│   ├── compose/{compose.yml, compose.s.yml, compose.m.yml, compose.airgap.yml, caddy/}
│   ├── keycloak/realm-export.json   minio/bucket-policy.json   images.lock
├── migrations/                    # Alembic — single tree for the whole app
│   └── versions/
├── scripts/{install.sh, easysynq, gen-contracts.sh, seed_*.py, airgap-bundle.sh}
├── seed/iso9001_clauses.yaml      # reviewable clause + ★-mandatory catalog (feeds M19)
├── tests/e2e/                     # Playwright acceptance #1..#6 + axe
└── docs/                          # EXISTING spec (00–17 + decisions-register + this 18)
```

**Toolchain (recommended defaults — see §11 for the few that are genuinely the owner's call):**

| Layer | Choice | Notes |
|---|---|---|
| Backend | Python 3.12 · **uv** (pkg/lock/venv) · **ruff** (lint+format) · **mypy --strict** · **pytest** + testcontainers · SQLAlchemy 2.x + Alembic · Uvicorn-under-Gunicorn | uv chosen for the air-gapped offline-wheel bundle (D1) |
| Storage client | **boto3** (S3 API) | keeps the D2/D4 swap to AWS-S3/Azure zero-code; boto3 fully supports object-lock/WORM |
| Frontend | **Vite** + React 18 + TS(strict) · **Mantine v7** · **Tailwind v3** · TanStack Query · oidc-client-ts (PKCE) · ESLint(+jsx-a11y) · Vitest · axe-core | Tailwind v3 (not v4) to de-risk Mantine interop |
| Contract codegen | **Redocly** (lint+bundle) → **datamodel-code-generator** (Pydantic v2) + **openapi-typescript**/**openapi-fetch** (TS) → **schemathesis** (runtime conformance) | spec-first, not code-first |
| Theming | **single CSS-variable token source** consumed by *both* Mantine and Tailwind (doc 11 §3) — never two palettes |
| Task runner / secrets | **just** (dev) + bash `install.sh`/`easysynq` (host) · `.env` (0600, gitignored) · **gitleaks** pre-commit · `pydantic-settings` 12-factor | no secrets in git; no outbound except customer-designated systems |
| CI | lint+type → unit → integration(testcontainers) → **contract drift gate** → web → migrations(up/down) → e2e+axe | generated trees gitignored; CI asserts `.contract.lock` checksum |
| Versioning | SemVer in `/VERSION`; API base `/api/v1`; Alembic linear history gated in CI; **trunk-based** + protected `main`; `easysynq upgrade` enforces backup-before-upgrade + rollback | branch first, never commit to `main` directly |

---

## 3. Docker Compose dev stack (S + M profiles)

Single host, Compose v2, all images **pinned by digest** in `infra/images.lock`. MVP ships the **S** and **M**
profiles; **OpenSearch is omitted from both in MVP** (R34) — search is Postgres-FTS behind an `Indexer` interface,
so OpenSearch is a clean v1 drop-in and `/readyz` does not check it.

| Service | Image (pin by digest) | Role | S | M |
|---|---|---|---|---|
| `proxy` (Caddy) | caddy:2 | TLS 1.2+ (prefer 1.3), HTTP/2, static SPA, `/api` routing, security headers, coarse auth throttle | ✓ | ✓ |
| `api` (FastAPI) | built | sync domain logic, PEP, lifecycle, audit writer, presign | 1 | 2 |
| `worker` (Celery) | built | render, index, mirror-sync, chain-linker, checkpoint, backup | 1 | 2 |
| `beat` (Celery Beat) | built | scheduler — **exactly 1** (two would double-fire) | 1 | 1 |
| `keycloak` | keycloak:26 | local + LDAP/AD + OIDC/SAML broker; realm import; admin MFA | 1 | 1 |
| `postgres` | postgres:16 | vault metadata/lifecycle/audit; `ltree` extension | 1 | 1 |
| `minio` | minio (recent RELEASE) | content-addressed WORM blobs; object-lock + SSE | 1 | 1 |
| `redis` | redis:7 | broker, cache, check-out locks, rate-limit buckets, permissions-epoch | 1 | 1 |
| `renderer` (Gotenberg) | gotenberg:8 | Office→PDF + thumbnails (wraps LibreOffice/Chromium) | 1 | 2 |
| `mailpit` *(dev only)* | axllent/mailpit | catches best-effort SMTP in dev | ✓ | ✓ |
| `opensearch` | — | **omitted in MVP** (v1; present-but-off optional on M) | — | — |

Compose specifics to build:
- **Profiles** via overlay files (`compose.s.yml` / `compose.m.yml`) selecting replica counts; `compose.airgap.yml` forces self-signed/admin-supplied TLS (ACME impossible offline).
- **MinIO init** sidecar: create `documents`/`renditions`/`records`/`staging` buckets; enable **object-lock** on `documents`+`records` (GOVERNANCE mode default — see §11 D-7) and **SSE**; the WORM probe at setup (G-B) verifies it.
- **Postgres init**: `CREATE EXTENSION ltree;` (+`pgcrypto`); the `audit_event` app role gets `INSERT`/`SELECT` only.
- **Keycloak realm bootstrap**: `realm-export.json` defines a public **SPA PKCE client** + a confidential **API audience** client, password policy, brute-force lockout (5 fails → 15 min), and admin MFA; a Keycloak **event-listener SPI** ships audit/login events into `audit_event`.
- **Read-only mirror volume**: a host volume **mounted `:ro` to users/`api`, read-write only to the `worker` UID** (R11); document the NFS/SMB `root_squash`/UID-mapping caveat in the runbook.
- **Health**: every service exposes `/healthz`; `api` `/readyz` checks PG+MinIO+Redis+Keycloak (+renderer) reachability and Alembic head.
- `install.sh` generates secrets → writes `.env` (0600) → selects profile → `docker compose up -d` → blocks on `/readyz` green.

---

## 4. Alembic schema & migration plan

**Conventions.** SQLAlchemy 2.x declarative `Mapped[...]`; **sync `psycopg` engine for migrations** (the app runtime is async, migrations are not coupled to the event loop); deterministic constraint naming (`MetaData(naming_convention=…)`); `alembic upgrade head` **and** `alembic check` (drift) gated in CI; first-run G-B asserts PG is at head.

**Autogenerate cannot model these — hand-author them:** `CREATE EXTENSION ltree`; all `CREATE TYPE` enums; the **INV-1 partial unique index**; the **singleton (R25) partial unique index**; the **GiST** index on `folder_path`; the **monthly RANGE partitioning** of `audit_event`; BRIN on `audit_event(occurred_at)`; the `audit_event`/`signature_event` role `GRANT/REVOKE`.

**Global invariants at the schema layer:** `org_id NOT NULL` (FK→organization, RESTRICT) on **every** table; `framework_id NOT NULL` (default ISO-9001 row) **only** on `documented_information`/`clause`/`clause_mapping`/`scope` (C5); **all FKs `ON DELETE RESTRICT`; no soft-delete** (users are state-retired, never hard-deleted); `audit_event`/`signature_event` strip UPDATE/DELETE from the app role.

### 4.1 Ordered migration chain (single linear head)

| # | Slug | Creates |
|---|---|---|
| M01 | `extensions` | `CREATE EXTENSION ltree` (+`pgcrypto`). |
| M02 | `enums` | all `CREATE TYPE` (see §4.2) — hand-authored, `create_type=False` references everywhere. |
| M03 | `organization` | `organization` (PK = the `org_id` everyone FKs); `short_code` unique. |
| M04 | `framework` | `framework` (`code='iso9001:2015'`); seeded in M20. |
| M05 | `system_config` + `working_calendar` | `system_config` (the `setup_state` one-way latch; `canonical_serialize` version pin); `working_calendar`. |
| M06 | `document_type` | `document_type` (`code`, **`document_level`** L1..L4, `default_retention_policy_id` NULL, `is_singleton`). |
| M07 | `retention_policy` | table created so FKs resolve (rows are v1 — §11 D-1). |
| M08 | `authz_core` | `app_user` (`keycloak_subject` UNIQUE, `manager_id` self-FK reserved), `role`, `permission` (`key` UNIQUE, **`is_system_domain`**), `user_role`, `role_permission`. |
| M09 | `authz_grants` | **doc-14 model (C9):** `role_grant`, `role_assignment`, `permission_override` (effect ALLOW/DENY, `scope_id`, `predicates jsonb`, **`valid_from`/`valid_until`**, `require_reason`), `scope` (`level`, `selector jsonb`, `predicates jsonb`), `sod_constraint`. *(delegation/guest_grant deferred to v1.x — §11 D-2.)* |
| M10 | `ia_clause` | `clause` (`is_mandatory_documented`, `framework_id`, self-parent), **`clause_mapping`** (M:N doc↔clause). |
| M11 | `ia_process` | `process` (`is_outsourced`, `outsourced_supplier_id` NULL, `owner_*`), `process_edge`, **`process_link`** (M:N), `process_clause`. |
| M12 | `blob` | `blob` (`sha256`, `worm_locked`, `worm_retain_until`) + **UNIQUE `(org_id, sha256)`** (dedup). |
| M13 | `documented_information` | base (`kind`, `document_type_id`, `identifier` UNIQUE-per-org, **`current_state`** 7-state enum, **`folder_path` ltree NULL**, `is_singleton`, `current_effective_version_id` NULL, `framework_id`, `legacy_identifier`) + **GiST(folder_path)** + **R25 singleton partial unique index**. |
| M14 | `document_version` | `document_version` (**`version_state`** 6-state enum, `revision_label`, `change_significance` MAJOR/MINOR, `change_reason`, `blob_id`, `rendition_blob_id` NULL, `effective_from`/`effective_to` timestamptz, `superseded_by_version_id`, `is_working_draft`, `author_user_id`) + **INV-1 partial unique index**. |
| M15 | `working_draft` | **`working_draft`** (`document_id` UNIQUE — ≤1 active, `checked_out_by`, `source_version_id`, **`scratch_blob_ref`**, **`lock_ttl`**). *(Redis holds the runtime lock; these columns are display/recovery mirror.)* |
| M16 | `workflow_task` | minimal approval machinery (C7): `workflow_definition`, `workflow_stage`, `workflow_instance` (pins `definition_version`), `task`, `task_outcome`. Backs `POST /tasks/{id}/decision` + **My Tasks**. |
| M17 | `numbering_identity` | `numbering_scheme` (`{TYPE}-{AREA}-{SEQ}` + per-(type,area) PG sequences); `identity_provider_config`; `instance_config` (`sizing_profile`, `feature_flags` off). |
| M18 | `audit_event` | **RANGE-partitioned-by-month** parent; PK `bigint GENERATED ALWAYS AS IDENTITY` **(no `seq` column, C4)**; cols per doc 12 §4.2; `prev_hash`/`row_hash`/`chained_at` **NULL-until-linked**; BRIN(occurred_at)+btree(object_id/actor_id/event_type); first partitions; app role `INSERT/SELECT` only. |
| M19 | `signature_event` + `audit_anchor` | `signature_event` (`meaning` R2 enum, `signed_object_type`, subject FKs `document_version_id`/`record_id`/`capa_stage_id` NULL, Part-11 reserved cols NULL, `voided_*`); `audit_checkpoint`; `audit_checkpoint_sink` (`kind`, `connection jsonb` enc, `enabled`). Add reserved FK `audit_event.signature_event_id → signature_event` here. |
| M20 | `seed_reference` | **idempotent data migration** (§4.3): framework row, clause catalog + ★ set (incl. 8.5.6), full permission catalog, 8 seeded roles + `role_permission` bundles, default `document_type`s. |
| M21 | `v1_entity_tables` | **empty-but-present** v1 tables (recommended, §11 D-3): `record`, `dcr`/`dcr_event`, `capa`/`capa_stage`, `audit_program`/`audit_plan`/`audit_finding`, `ncr`, `risk_opportunity`, `complaint`, `supplier`, `acknowledgement`, `document_link`. PEPs/routers unbuilt; schema final → v1 purely additive. |

### 4.2 Enum inventory (M02)

`version_state` `{Draft,InReview,Approved,Effective,Superseded,Obsolete}` · `document_current_state` `{Draft,InReview,Approved,Effective,UnderRevision,Superseded,Obsolete}` · `signature_meaning` `{review,approval,release,obsolete,verify,disposition,import_baseline,review_confirmed, authored,responsibility}` *(last two reserved, never emitted)* · `signature_method` `{app_click,SESSION, password_reauth,mfa_totp,mfa_webauthn}` *(last three reserved)* · `signed_object_type` `{document_version,record,capa_stage}` · `document_level` `{L1_POLICY,L2_PROCEDURE,L3_WORK_INSTRUCTION,L4_FORM}` · `scope_level` `{SYSTEM,FRAMEWORK,PROCESS,FOLDER,DOC_CLASS,ARTIFACT}` · `grant_effect` `{ALLOW,DENY}` · `setup_state` `{UNINITIALIZED,IN_SETUP,OPERATIONAL}` · `actor_type` `{user,system,external_auditor,admin}` · `sink_kind` `{worm_bucket,external_object_store,append_only_syslog}` · plus v1 enums created now (cheap, forward-compatible): `dcr_state`, `finding_type`, `finding_severity`, `capa_stage_type`, `ncr_disposition`.

### 4.3 Load-bearing DDL sketches (illustrative)

```sql
-- INV-1: at most one Effective version per document (M14) — hard concurrency backstop for AC#1
CREATE UNIQUE INDEX uq_document_version_one_effective
  ON document_version (document_id) WHERE version_state = 'Effective';

-- R25 singleton: one Effective instance per (org, document_type) AT A TIME (M13)
CREATE UNIQUE INDEX uq_doc_info_singleton_effective
  ON documented_information (org_id, document_type_id)
  WHERE current_state = 'Effective' AND is_singleton = true;

-- FOLDER scope selector (M13): PDP match = resource.folder_path <@ :scope_ref
ALTER TABLE documented_information ADD COLUMN folder_path ltree NULL;
CREATE INDEX ix_doc_info_folder_path_gist ON documented_information USING GIST (folder_path);

-- content-addressed dedup (M12): re-upload of identical bytes -> "no change detected"
CREATE UNIQUE INDEX uq_blob_org_sha256 ON blob (org_id, sha256);

-- audit_event (M18): partitioned, identity PK, NO seq column, hash cols null-until-linked
CREATE TABLE audit_event (
  id bigint GENERATED ALWAYS AS IDENTITY,          -- gap = tamper signal (C4)
  org_id uuid NOT NULL REFERENCES organization(id) ON DELETE RESTRICT,
  occurred_at timestamptz NOT NULL,
  -- actor_id, actor_type, event_type, object_type, object_id, scope_ref, reason,
  -- before jsonb, after jsonb, request_id, client_ip, user_agent, auth_context,
  -- signature_event_id (reserved FK), on_behalf_of (reserved)
  prev_hash bytea NULL, row_hash bytea NULL, chained_at timestamptz NULL,  -- R12
  PRIMARY KEY (id, occurred_at)
) PARTITION BY RANGE (occurred_at);
CREATE INDEX brin_audit_event_ts ON audit_event USING BRIN (occurred_at);
-- a Beat task rolls next-month partitions ahead of time; CLI fallback for the Beat-down window.
```

---

## 5. FastAPI / OpenAPI surface

**Spec-first.** `openapi.yaml` (OpenAPI 3.1) is hand-authored and reviewable — it is the natural *approval artifact*.
Pydantic models and the TS client are generated from it; the running server is proven to conform via schemathesis.
**Lifecycle transitions are named action sub-resources, never `PATCH status=`** (doc 15 §1.3).

### 5.1 MVP router set (doc 15)

| Group | Endpoints (MVP) | Notes |
|---|---|---|
| Health | `GET /healthz`, `GET /readyz` | public; no OpenSearch check (MVP) |
| Setup | `GET\|PATCH /admin/setup`, `…/config/storage(+:test)`, `…/config/identity`, `…/config/numbering`, `/admin/org` | **pre-finalize only**; one-way latch; while `≠ OPERATIONAL`, `/api/v1/*` QMS routes → **423 setup_incomplete** |
| Auth/session | `GET /auth/config`, `POST /auth/session`, `/auth/refresh`, `/auth/logout`, `/auth/step-up`; `GET /me`, `/me/permissions`, `/me/actions` | Keycloak OIDC+PKCE; API validates JWT vs JWKS; permissions resolved **server-side**, never from token; `/me/actions` = My Tasks query |
| Users/Roles/Perms | `/users`, `/roles`, `/permissions` (catalog read), `/users/{id}/roles`, `/users/{id}/overrides`, `/users/{id}/effective-permissions` | grants enforce the **two-tier guard** (422 `two_tier_violation`) |
| Documents | `GET\|POST /documents`, `GET\|PATCH /documents/{id}` (metadata only), `POST /documents/{id}/{checkout,checkin,submit-review,release,start-revision,obsolete}`, `/documents/{id}/lock`(+break), `GET /documents/{id}/download` | **flat sub-resources (C7)**; `release` = serializable supersession |
| Versions/content | `GET /documents/{id}/versions`, `POST …/versions:init-upload`, `GET …/versions/{vid}(/download)` | **two-step presigned PUT** then finalize; API never proxies bytes |
| Tasks (approval) | `GET /tasks`, `GET /tasks/{id}`, `POST /tasks/{id}/claim`, **`POST /tasks/{id}/decision`** | the decision writes `signature_event`+`task_outcome`+`audit_event` in one txn; SoD enforced → 403 `sod_violation` |
| IA | `GET /clauses`, `GET /processes`(/map) | clauses read-only seed |
| Audit | `GET /audit-events`, `/audit-events/{id}`, `/{resource}/{id}/audit-events`, `/audit-events/verify-chain` | **read-only — no write verbs ever** |
| Search | `GET /search`, `/search/suggest` | Postgres-FTS; results post-filtered by permission |
| Stub | `POST /admin/export` → **501** | the single live stub doc 15 mandates (whole-vault export = v1.7) |

**v1 endpoints** (records, changeRequest, capa, audits, risk, complaints, evidence-packs, import) — **omit from the mounted router but keep their schemas in `openapi.yaml`** so the contract is stable and v1 is additive (§11 D-9).

### 5.2 Cross-cutting mechanisms

- **PEP/PDP.** A FastAPI dependency on every authenticated route declares `(required permission, scope-resolver)`; the **PDP is a pure function** implementing R3 verbatim: deny-by-default → gather grants in scope → **any DENY ⇒ DENY** → else any ALLOW ⇒ ALLOW → specificity breaks only ALLOW-vs-ALLOW ties → SoD against immutable history → sig-hook step-up gate. **Every allow *and* deny writes an `audit_event`.** Effective-permission cache in Redis keyed by `(user, permissions_epoch)`; any grant change bumps the epoch (revoke takes effect next request). List endpoints **filter, not 403**; sensitive types collapse permitted-but-absent vs forbidden to **404** (doc 15 §9.5).
- **Supersession (`release`).** One **SERIALIZABLE** txn: prior Effective→`Superseded` (`effective_to=now`), this→`Effective` (`effective_from`), set `current_effective_version_id`, append audit RELEASED+SUPERSEDED, write `signature_event(meaning='release')`; the INV-1 index makes a second concurrent release fail → rollback/retry. Future-dated `effective_from` stays `Approved` until the Beat cutover sweep (lazy-read `effective_from <= now()` guard; R8 tz rule).
- **Audit + signature emission.** A helper writes the `audit_event` in the **same txn** as the change; `signature_event` is written where the §8.16 matrix requires (approve/release/obsolete/review). Login/MFA/logout originate in Keycloak → ingested via the event-listener SPI (the same-txn rule doesn't apply to those).
- **Conventions.** `Idempotency-Key` on mutating POSTs (Redis dedupe); `ETag`/`If-Match` optimistic lock (412); cursor pagination over UUID v7; **RFC 9457 problem+json** with canonical `code`s (`permission_denied`/`invalid_state_transition`/`conflict`/`lock_conflict`/`etag_mismatch`/`worm_required`/`two_tier_violation`/`validation_error`/`not_found`/`rate_limited`/`sod_violation`/`step_up_required`/`setup_incomplete`); **429 + Retry-After** Redis token bucket (per-user + per-IP) at app, coarse at Caddy.

---

## 6. Frontend SPA plan

> **Source-of-truth caveat (C11):** docs/11 is *not* reconciled — for wizard steps use **R4+doc 08** (10-step), lock TTL **R24 (8h)**, lifecycle **R1 (7-state)**, task inbox label **My Tasks (R23)**, search shortcut **Cmd-K/Ctrl-K + `/` (R23)**. The Decisions Register supersedes docs/11.

- **Shell & tokens.** Vite/React/TS; a **single CSS-variable token source** feeds *both* Mantine theme and Tailwind (light/dark); calm app shell (top bar, PLAN/DO/CHECK/ACT clause-spine rail, deep-linkable detail drawer). **i18n string externalization from day one** (English ships; framework ready). **WCAG 2.2 AA** is a release gate: axe-core in CI + manual NVDA/VoiceOver + keyboard-only pass; contrast ≥4.5:1; target ≥24×24; focus never removed; status never color-only.
- **Auth.** OIDC Auth-Code + PKCE; access token **in memory only** (never localStorage); session-refresh model reconciled to one path (§11 D-8). The SPA **hides controls it believes denied (DP-6)** but the server is the sole enforcer.
- **MVP screens.** (1) **First-run setup wizard** (10 steps; WORM-verify, backup+restore-test gate, audit-sink soft gate, finalize latch); (2) **Clause-spine nav + Library** (density-adaptive table: Identifier mono / Title / State chip / Clause / Rev / Owner; filters; cursor pagination; aria-sort); (3) **Document landing + version timeline** (Effective ★ dominant; lifecycle buttons PDP-gated; 8h check-out lock UI + break-lock confirm; typed confirm on release/obsolete); (4) **Metadata editor** (folder_path picker, `clause_map` ≥1 required, process_links, numbering display); (5) **Review/Approve task** two-pane with a single-factor **signature slot** region (pluggable for Part-11) → `POST /tasks/{id}/decision`; (6) **Audit-log view** (per-artifact tab + standalone Admin screen; filters; before/after diff; verify-chain); (7) **Users/roles/grants admin** (two-tier-aware). Record-status *tokens* exist but record screens, Process Map, Where-used, Acks bell are v1.

---

## 7. Ordered vertical slices + Definition of Done (the centerpiece)

Each slice is **end-to-end** (migration → API → UI → test), independently demoable, leaves reserved hooks live
(never dead debt), and carries a **named automated proof** where one exists. Ordering burns down the hardest
guarantees first within the roadmap dependency chain **install+auth → authz → vault → lifecycle → audit → mirror**.

```
S0 walking skeleton ─┬─ S1 AuthN ── S2 AuthZ[AC#3,4] ── S3 Vault ── S4 Lifecycle[AC#1]
                     │                                      ├─ S5 Approval+SoD
                     │                                      └─ S6 Audit+chain[AC#6] ── S7 Mirror[AC#2]
                     ├─ S8 Setup wizard + WORM/restore gates [AC#5]   (parallel after S3)
                     ├─ S9 IA/nav + Library + Document UI   (token track starts at S0)
                     ├─ S10 Search (FTS) + Audit-log view
                     └─ S11 Backup/restore CLI + hardening (exit slice)
```

| Slice | Goal | Definition of Done (incl. **[PROOF]** = the load-bearing automated test) |
|---|---|---|
| **S0 Walking skeleton** | Compose up, health green, reversible migration, OpenAPI→client round-trips | Compose S/M bring all services up (digest-pinned); `/healthz`+`/readyz` green; `install.sh` blocks on ready; **[PROOF]** `alembic upgrade head && downgrade base`; **[PROOF]** contract regen produces zero drift; `beat`=exactly 1; structured JSON logs w/ `request_id`. |
| **S1 AuthN** | Keycloak PKCE login; JWT→`app_user` | SPA login works (token in memory only); **[PROOF]** API rejects tampered/expired JWT (401), accepts valid, maps `sub→app_user`; realm exportable (S11 backup); `/auth/step-up` seam present (no v1 enforcement); **≥1 federation mode (LDAP or OIDC) configurable + proven via live round-trip** (not local-only); Keycloak brute-force lockout (5/15min). |
| **S2 AuthZ [AC#3,4]** | Catalog seed, 8 roles, PDP/PEP, deny-wins — before any vault write | Closed doc-07 catalog seeded (legacy spellings normalized); ADMIN holds **no** content perms; Approver lacks `document.edit`; PDP is a pure unit-tested function; **[PROOF AC#3]** per-user `DENY @DOCUMENT` beats role `ALLOW @PROCESS`; **[PROOF AC#4]** `system.*` holder → `document.approve` = **DENY**; **[PROOF]** content-tier `permission.grant` of a system key → **422 two_tier_violation**; **[PROOF]** specificity never overrides a DENY; every allow+deny emits an audit hook. |
| **S3 Vault** | Create doc, check-out (Redis lock), upload CAS blob, check-in immutable version | Identifier `{TYPE}-{AREA}-{SEQ}` allocated atomically (REV not in identifier); **[PROOF]** re-checkin identical bytes → "no change detected", no new version; **[PROOF]** empty `change_reason` **or** missing `change_significance` → **422** (INV-3, C10); **[PROOF]** double check-out → **409 lock_conflict**; **[PROOF]** break-lock **preserves scratch** (R9) + `LOCK_BROKEN` audit; blobs WORM-written before version marked complete; lock TTL **8h** (R24) with heartbeat; content I/O **presigned** (asserted: API never proxies bytes). |
| **S4 Lifecycle [AC#1]** | FSM + atomic single-Effective cutover | FSM enforced server-side (illegal → 409); **7-state tokens verbatim** in DB; **[PROOF AC#1a]** `Draft→InReview→Approved→Effective` with recorded approval; prior Effective atomically → `Superseded`; **[PROOF AC#1b]** two parallel `release` txns → exactly one Effective (loser hits INV-1, rolls back) under real concurrent connections; future-dated release fires via Beat at the stored UTC instant; **submit-review requires ≥1 `clause_mapping` else 422**. MVP ships T1–T4, T6, T7, T9–T12; defers T5/T8 (§11 D-5). |
| **S5 Approval + SoD** ✅ | Approve/release persist append-only `signature_event`; SoD blocks self-approval | Approval/review routes through `POST /tasks/{id}/decision` (writes `signature_event`+`task_outcome`+audit in one txn; tasks-canonical, C7 — direct `/approve`+`/request-changes` removed); `signature_event` append-only (rescind via `voided_*`), **polymorphic `signed_object_type`/`signed_object_id`** (doc 14 §8 governs over §15.4's typed-FK form), only v1 meanings emitted, `content_digest`+`auth_context` captured, Part-11 cols NULL; **[PROOF]** **SoD-1** (author of the version cannot approve it) → **403 `sod_violation`** (doc 15 §8.8 governs the error shape — reconciled from the originally-stated 409); **SoD-2** (author never releases own edit; approver-release behind `allow_approver_release`) and **SoD-3** (auditor independence — Internal Auditor role hard-excludes edit/approve/release, RBAC) covered, evaluated against **immutable version/signature history** (INV-4), not a single current field. |
| **S6 Audit + chain-linker + sink [AC#6]** | Append-only partitioned trail; decoupled hash-chain; off-host checkpoint | `audit_event` monthly-partitioned; app role has **no UPDATE/DELETE** on `audit_event` **and** `signature_event`; row written in same txn as its change; **[PROOF AC#6a]** UPDATE/DELETE rejected; every gated step produces a row; **[PROOF AC#6b]** chain-linker sets `prev_hash/row_hash/chained_at`; `verify-chain` recomputes & matches; a mutated row is **detected** as the first broken link; linker is **exactly-one** (advisory lock) with a **bounded-lag alarm**; off-host `audit_checkpoint_sink` push works; absent sink → persistent **"NOT tamper-evident"** UI warning (R13 soft gate). `canonical_serialize` frozen as a normative spec + **golden-vector test** (§11 D-4). |
| **S7 Mirror [AC#2]** | RO mirror of Effective-only, watermarked; auto-correct by regeneration | Mirror contains **only** Effective versions (drafts provably excluded); written to temp tree then **atomic swap**; mounted **read-only to users** (R11 mount contract); **[PROOF AC#2]** an edited mirror file is **overwritten from the vault on next sync**; watermark band carries Rev+EffectiveDate+copy_status (non-removable); Obsolete/Superseded stamps non-suppressible; non-renderable formats stored as controlled source + `no_controlled_rendition` (R26); mirror regenerable, **not** backed up. *MVP = RO-mount + regeneration; the SHA-256 drift scan / quarantine / `MIRROR_DRIFT_DETECTED` alarm are v1 (C-correction; §11 D-6).* |
| **S8 Setup wizard + gates [AC#5]** | 10-step latch; WORM-verify + tested-restore hard gates | `setup_state` latch enforced; `/api/v1/*` → **423** until OPERATIONAL; canonical step order (org profile before storage; backup+restore-test before auth); bootstrap secret single-use, salted-hashed; **[PROOF G-B]** WORM probe: object-locked probe early-delete **denied**; **[PROOF AC#5]** finalize **blocked** until a backup→restore-into-scratch drill **passes** integrity assertions (blob SHA-256 re-hash, row counts, FK checks) — "configured but unverified" does **not** satisfy G-C; off-host sink absent → loud not-tamper-evident warning (never blocks); finalize is one transactional commit that arms Beat jobs + writes `SETUP_FINALIZED`; Avery→Mara handoff (System Administrator bundle = no content caps; Mara gets QualityManager). |
| **S9 IA/nav + Library + Document UI** | Calm shell + Library + document landing/timeline | single token source feeds Mantine+Tailwind; clause spine by PDCA; Library + landing + version timeline functional; **[PROOF]** axe-core CI gate green + manual SR/keyboard pass; **[PROOF DP-6]** Avery sees **no enabled** approve control; responsive to tablet; signature slot present single-factor. *(Token foundation is a day-one parallel track feeding S3–S8.)* |
| **S10 Search + Audit-log view** | Postgres-FTS search + Admin audit-log screen | search permission-filtered server-side ("N hidden by your access scope" footer); `Indexer` interface keeps OpenSearch a v1 drop-in; Admin Audit-Log screen (filters, before/after diff, verify-chain); **[PROOF]** audit read API exposes **no** write verbs (route inventory assertion). |
| **S11 Backup/restore CLI + hardening (exit)** | `easysynq backup/restore/upgrade`; WORM-aware restore; air-gap; NFR/security | CLIs work; backup = PG dump + MinIO manifest + Keycloak realm + encrypted config (OpenSearch/mirror excluded); **[PROOF]** WORM-aware restore targets a **fresh bucket** (never mutates the locked one), PITR↔blob-snapshot aligned, **checkpoint-not-ahead** check flags a tamper event needing operator ack, restored chain re-verified (R37); air-gapped bundle installs offline (digest-pinned); sensitive columns envelope-encrypted; Caddy nonce-CSP+HSTS+frame-ancestors-none, TLS 1.2+; **[PROOF]** NFR smoke (metadata P95 ≤300ms; cached watermarked PDF first page ≤2s; interactive P95 ≤1.5s); `easysynq upgrade` enforces pre-upgrade backup + rollback; **operator runbooks delivered** (install guide online+air-gapped, restore-drill, Keycloak/Beat SPOF fast-restart per R14, key-rotation, NFS `root_squash` mirror caveat, MinIO object-lock prereq). |

### 7.1 Acceptance-criteria → slice traceability

| # | MVP acceptance criterion | Proven in | Test |
|---|---|---|---|
| 1 | Draft→…→Effective; prior atomically Superseded; two Effective impossible | **S4** (+S5) | `test_release_supersedes` + `test_two_effective_impossible` (concurrent serializable releases vs INV-1) |
| 2 | Edit to RO mirror file overwritten from vault on next sync | **S7** | `test_ro_mirror_autocorrect` |
| 3 | `document.checkin@process:X` + per-user deny on a doc → DENIED | **S2** | `test_per_user_deny_beats_role_allow` |
| 4 | Avery `system.*` denied `document.approve` by default | **S2** | `test_admin_system_star_denied_content` |
| 5 | Setup completes only after a tested restore passes | **S8** | `test_setup_finalize_requires_restore_pass` |
| 6 | Every step in an append-only, non-editable audit trail | **S6** (+S10) | `test_audit_append_only` + `test_hash_chain_verify` |

---

## 8. Testing, CI & the six invariant proofs

- **Pyramid.** Unit (PDP, FSM, supersession, `canonical_serialize` golden-vector) → integration (testcontainers PG16+MinIO+Redis) → **contract** (schemathesis vs `openapi.yaml`) → **e2e** (Playwright acceptance #1–#6) → **a11y** (axe-core gate).
- **The six proofs** are MVP acceptance gates (table §7.1). The two highest-risk run under *real* concurrency/role connections, not mocks: `test_two_effective_impossible` and `test_per_user_deny_beats_role_allow`.
- **Extra gates:** append-only proven on **both** `audit_event` and `signature_event`; hash-chain tamper-detection (break a row → verify reports the first broken link); WORM-verify gate; migration up/down + `alembic check` drift; contract checksum drift; seed-data correctness; rate-limit smoke (429); NFR P95 smoke; security-header scan.
- **CI stages:** `contracts` → `api` (ruff/mypy/pytest) → `api-contract` (schemathesis) → `web` (eslint/tsc/vitest) → `migrations` (up from empty + autogen-diff) → `e2e+axe`. CI also builds and smoke-tests the Compose bundle (self-hosted product).

---

## 9. Ops foundations (shipped in MVP)

- **Backup/restore (R37).** `easysynq backup` = single timestamped, checksummed, optionally-encrypted archive (pg_dump + MinIO manifest with **blob-snapshot id per position** + Keycloak realm + encrypted config; brief consistency quiesce). `easysynq restore` is **WORM-aware**: restore blobs into a **fresh/cleared/versioned bucket** then cut MinIO over (never mutate the locked bucket); pair the PITR target with the **aligned** blob snapshot (not the latest mirror); **verify the audit checkpoint is not ahead** of a mid-chain target (else flag a tamper event requiring audited operator ack); re-verify the chain; trigger reindex + mirror-sync. **MVP = nightly pg_dump + WORM-aware cutover + alignment + checkpoint check**; continuous WAL/PITR is v1.x (§11 D-6). The tested-restore drill is the S8 G-C gate.
- **Audit chain-linker (R12)** — single-threaded under a PG advisory lock (or a dedicated Beat job), bounded written-but-not-yet-chained lag, alarmed above threshold; sets `prev_hash/row_hash/chained_at` once. **Off-host checkpoint sink (R13)** — three kinds (`worm_bucket`/`external_object_store`/`append_only_syslog`); credential held in **separate custody** from the app KEK/backup key (§11 D-8).
- **Read-only mirror writer (R11)** — regenerate Effective-only on Release/Supersede/Obsolete (incremental) + nightly full reconcile; atomic swap; RO mount contract.
- **Beat schedule** — effectivity-cutover sweep (~5 min), periodic-review-due sweep (daily, light in MVP), chain-linker cadence, nightly backup, monthly audit-partition roll, blob-integrity re-hash. `beat` is a **documented SPOF** with a fast-restart runbook (R14); availability target **99.0%/month** single-host (incl. Keycloak+Beat) — **not** 99.5% (HA path only).
- **Security/secrets** — sensitive columns envelope-encrypted (AES-256-GCM, KEK-sealed); secrets via Docker secrets/.env 0600, rotatable, log-redacted; Caddy nonce-CSP/HSTS/frame-ancestors-none; PII-vs-WORM uses **object-lock GOVERNANCE** so the R27 dual-control destroy-under-legal-order escape hatch stays buildable (§11 D-7).

---

## 10. Reserved-hooks ledger (present from creation, never removed, never built early)

| Hook | Created in | MVP state |
|---|---|---|
| `org_id` on every table | S0 + each slice | populated (single org) |
| `framework_id` on `documented_information`/`clause`/`clause_mapping`/`scope`; FRAMEWORK scope level | S2/S3 | `iso9001:2015` only |
| `signature_event` Part-11 columns + reserved methods + meanings `authored`/`responsibility` | S5 | present/declared, never emitted |
| `signature_event` subject (`record`/`capa_stage`) | S5/S19 | polymorphic `signed_object_type`/`signed_object_id` (doc 14 §8) — the `record`/`capa_stage` enum values exist; their target tables fill in later (record S5, capa_stage S19) |
| `audit_event.signature_event_id`, `on_behalf_of` | S6 | present, NULL |
| `app_user.manager_id`; `/auth/step-up` + acr/amr seam | S1 | present, no logic |
| `permission_override.valid_from/valid_until/predicates`; `scope.predicates` | S2 | live (time-box/ABAC ready); `delegation`/`guest_grant` v1.x |
| v1 entity tables (`record`/`dcr`/`capa`/`audit_*`/`ncr`/`risk_opportunity`/`complaint`/`supplier`/`acknowledgement`/`document_link`) | S21 | empty-but-present (§11 D-3) |
| `Indexer` interface (Postgres-FTS now) | S0/S10 | OpenSearch is a v1 drop-in |
| MinIO S3 API | S0/S3 | swap to AWS-S3/Azure = no code change |

---

## 11. Decisions for the owner (flagged; recommendation in **bold**)

Most build choices are settled by the Register/docs and are baked into this plan. The following are the genuinely
open ones; **none contradict D1–D4 or R1–R37.** The first three are the strategic ones worth an explicit call.

| # | Decision | Options | Recommendation |
|---|---|---|---|
| **D-3** | v1 entity tables: create empty now vs defer | (a) empty-but-present now · (b) defer to v1 migration · (c) hybrid (only FK-targets now) | **(a) Create empty now.** FK RESTRICT + no soft-delete against a live WORM/hash-chained dataset makes a later destructive migration risky; keeps the reserved `signature_event` subject FKs real; MVP→v1 stays purely additive. |
| **D-2** | Authz model depth in MVP | (a) full doc-14 model (override+scope+delegation+guest_grant+sod_constraint) · (b) reduced subset · (c) flat table | **(b) Reduced doc-14 subset:** build `role`/`role_grant`/`role_assignment`/`permission`/`permission_override`/`scope`/`sod_constraint`; **defer `delegation` + `guest_grant` to v1.x** (external-auditor time-box is a v1 evidence-pack concern). Do **not** flatten to a single table (would contradict doc 14). |
| **D-7** | MinIO object-lock mode for documents/records buckets | GOVERNANCE vs COMPLIANCE | **GOVERNANCE in prod (long, record-retention-aligned retention) + COMPLIANCE as a hardened opt-in in the setup wizard; dev = GOVERNANCE + 30-day.** GOVERNANCE keeps R37 fresh-bucket restore and the R27 dual-control destroy escape hatch buildable; COMPLIANCE forecloses both (immutable even to root). |
| D-1 | Retention-default rows seeded in MVP? | seed now vs defer | **Defer rows to v1** (records/retention is v1); create the `retention_policy` *table* now so FKs resolve. |
| D-4 | `canonical_serialize` byte-spec for the hash chain | freeze now vs let implementer pick | **Freeze now** as a one-page normative spec (RFC 8785 JCS for `before`/`after` jsonb; UTC-microsecond ISO-8601 `occurred_at`; lowercase-hex/`bytea` `row_hash`; fixed genesis constant) + a **golden-vector test**. Field order is already pinned by doc 12 §4.3; only these four items are open. Cheap now, irreversible-expensive later. |
| D-5 | Which FSM transitions ship in MVP | all 12 vs subset | **T1–T4, T6, T7, T9–T12; defer T5 (rescind) + T8 (discard-draft).** T7/T9 already produce the second version that proves supersession. |
| D-6 | Mirror drift posture; PITR scope | — | **MVP = R11 RO-mount + nightly regeneration/overwrite (satisfies AC#2); drift *detection* (scan/quarantine/alarm) is v1.** **MVP backup = nightly dump + WORM-aware cutover; continuous WAL/PITR is v1.x.** |
| D-8 | Off-host sink credential custody; SPA session-refresh model | — | **Sink credential via a separate Docker secret / external secret ref (genuine separate custody per R13), not envelope-in-DB.** **Session: API-brokered httpOnly refresh cookie per doc 15** (SPA does not hold the refresh token) — reconcile the frontend auth module to this. |
| D-9 | v1-endpoint posture | omit-with-schema vs 501 stubs | **Omit v1 routers but keep their schemas in `openapi.yaml`; ship only `/admin/export` as a live 501 stub** (per doc 15). |
| D-10 | Tooling: uv/just/pnpm/boto3/Tailwind-v3/spec-first/trunk-based/GH-Actions | adopt vs team preference | **Adopt the recommended set.** Load-bearing ones to lock: **boto3** (zero-code storage swap) and **gitignored-generated-code + checksum lock** (keeps OpenAPI-first honest). The rest are low-risk defaults. |
| D-11 | Spec-hygiene reconciliations (back-propagate into the docs) | — | Add `permission_override` time-box columns to doc 14 §3 if not already; add `rate_limited`/`sod_violation`/`setup_incomplete` problem types to doc 15; freeze the ★-mandatory clause list (incl. 8.5.6) in `seed/iso9001_clauses.yaml`. **Low-risk doc edits**, do alongside S0/S2. |

---

## 12. MVP exit checklist

- [ ] **All 6 acceptance proofs green** (§7.1).
- [ ] **D1** no outbound except customer-designated systems; no telemetry; admin-controlled backups.
- [ ] **D2** authority vault→mirror only; mirror RO; only Effective in mirror.
- [ ] **D3** `org_id` everywhere + `framework_id` where doc 14 specifies; all Part-11/multi-standard hooks present-but-unbuilt and **not removed**.
- [ ] **D4** full stack on Compose S+M; OpenAPI-first; deny-by-default; 12-factor; `beat` exactly 1.
- [ ] R3 deny-wins PDP pure + unit-tested · R12 decoupled chain-linker with bounded-lag alarm · R13 off-host sink configurable (+not-tamper-evident warning when absent) · R37 WORM-aware restore proven.
- [ ] WCAG 2.2 AA (axe + manual); NFR P95 budgets met; migrations reversible + CI-gated; air-gapped bundle installs; security headers + TLS 1.2+; secrets rotatable.
- [ ] `easysynq backup/restore/upgrade` proven; tested-restore drill passes; WORM-verify hard gate enforced; operator runbooks delivered.
- [ ] Avery→Mara handoff demoable.

---

*End of MVP implementation plan. On approval, the recommended first step is **Slice S0 (walking skeleton)** — Compose
stack, health, the reversible-migration runner, and the OpenAPI→client codegen pipeline — which unblocks every
subsequent slice.*
