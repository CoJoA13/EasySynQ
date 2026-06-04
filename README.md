# EasySynQ

A **self-hosted, browser-based ISO 9001:2015 Quality Management System (QMS)**. A managed **controlled vault**
(PostgreSQL + MinIO WORM) owns the master copy of every controlled document and record; the on-disk filesystem
is a **read-only mirror** regenerated from Released versions only — so document drift becomes an *enforced
invariant* rather than a discipline problem.

## Status

- **Specification:** complete and internally reconciled — see [`docs/`](docs/) (start at
  [`docs/00-overview.md`](docs/00-overview.md); [`docs/decisions-register.md`](docs/decisions-register.md) is
  authoritative).
- **Implementation plan:** [`docs/18-mvp-implementation-plan.md`](docs/18-mvp-implementation-plan.md) (approved).
- **Code: the MVP is complete** — all 11 vertical slices (S0–S11) shipped to protected `main` (each via a PR with
  green CI), all six acceptance proofs in, the mirror epic + both information-architecture backends complete, and the
  doc-18 §12 exit checklist closed.
  - **S0 — walking skeleton** ✅ — Compose stack, `/healthz`+`/readyz`, reversible Alembic baseline, OpenAPI→client pipeline.
  - **S1 — authentication** ✅ — Keycloak OIDC/PKCE, JWT-vs-JWKS validation, `app_user` + JIT provisioning, `/me`.
  - **S2 — authorization** ✅ — deny-wins PDP/PEP, the closed permission catalog + 8 seeded roles, two-tier grant guard.
  - **S3 — vault** ✅ — document create + the check-out → presigned CAS upload → immutable check-in cycle (MinIO WORM, Redis lock).
  - **S4 — lifecycle** ✅ — the document FSM (Draft→…→Effective) + the atomic single-Effective cutover (SERIALIZABLE + INV-1), 6 named lifecycle actions, R25 singleton index, future-dated Beat sweep.
  - **S5 — approval + SoD** ✅ — the task/decision approval workflow (`POST /tasks/{id}/decision` writes `signature_event`+`task_outcome`+audit in one txn; tasks-canonical), append-only `signature_event` emission on approve/release/obsolete, and the deny-wins separation-of-duties gate (SoD-1 no self-approval, SoD-2 no self-release, SoD-3 auditor independence).
  - **S6 — audit** ✅ — the append-only, monthly-partitioned, hash-chained `audit_event` trail behind DB **role separation** (a non-owner `easysynq_app` role with INSERT/SELECT-only on `audit_event`+`signature_event`, so append-only is structurally enforced — AC#6a); the in-transaction audit writer; the decoupled chain-linker (a dedicated `easysynq_linker` role, advisory-locked, bounded-lag alarm) with a frozen `canonical_serialize` + golden vector; `verify-chain` (detects a mutated row as the first broken link — AC#6b); the off-host `worm_bucket` checkpoint anchor with an honest tamper-evidence soft-gate (R13); and the read-only `/audit-events` API.
  - **S7 — mirror** ✅ — the read-only, Effective-only filesystem mirror, regenerated from the vault via an atomic symlink-repoint swap, mounted RO (AC#2). **S7b** watermarked-PDF rendering (Gotenberg + a deterministic reportlab/pypdf §11.3 band, cached non-WORM rendition); **S7c** the Ed25519 verify-token + QR + the public `GET /verify` (CURRENT/SUPERSEDED/UNKNOWN); **S7d** the per-request export/print stamp.
  - **S8 — first-run setup** ✅ — the 423 setup-latch + the five blocking gates, each shipped as its own slice: **S8a** the bootstrap-of-trust spine (mint-secret → first System Administrator) + org profile + finalize (G-A/G-E); **S8b** the WORM-verify probe (G-B); **S8b2** the backup→restore-into-scratch drill + durable backup (G-C / AC#5); **S8c** auth-config + a non-bootstrap login proof (G-D) + the client router; **S8d** the Users & Roles admin (roster / invite / enable-disable + role/override management).
  - **S9 — clause IA + `clause_mapping`** ✅ — the read-only ISO 9001:2015 clause spine (an 83-clause seeded catalog, the 20 ★ mandatory documented-information items) + the M:N document↔clause mapping (`GET /clauses`, flat `…/clause-mappings` sub-resources) + the lifecycle submit gate (a document needs ≥1 clause mapping before review).
  - **S9b — clause-aligned mirror tree** ✅ — the §10.3 `PLAN/DO/CHECK/ACT → {NN}-{clause}/` tree: each Effective doc lives once under its numerically-lowest mapped clause and is reached from every other mapped clause via a relative symlink (clause 7 splits PLAN/DO); an unmapped (pre-S9 upgrade) doc lands in `_unmapped/`.
  - **S9c — process IA backend** ✅ — the Clause 4.4 process graph (`process`/`process_edge`/`process_link` + `org_role`/`supplier` FK targets) with `GET /processes(/{id})(/map)` + `SEED→ACTIVE` authoring (`POST`/`PATCH /processes`, `…/edges`) + the M:N `…/process-links` sub-resource, all audited (`process.create`/`assign_owner` seeded-but-ungranted → grant via override until the role UI).
  - **S9d — by-process mirror index** ✅ — the §10.3 secondary `current/by-process/{name}/` tree of relative symlinks into the clause-tree doc folders (always-on; bytes never duplicated). **Completes the mirror epic.**
  - **S10 — search + the Compliance Checklist** ✅ — the org-wide checklist (`GET /reports/compliance-checklist`: the 20 ★ mandatory clauses with COVERED/PARTIAL/GAP coverage), Postgres-FTS `GET /search`(+`/suggest`) behind an engine-agnostic `Indexer` seam (Effective-only, filter-not-403), and `clause_refs` + bracketed `filter[field][op]` on `GET /documents` (the audit-read API proven write-verb-free, co-proving AC#6).
  - **S11 — the MVP exit slice** ✅ — `easysynq restore` (WORM-aware **restore-to-verified-target**: fresh scratch DB + fresh non-WORM bucket, the locked vault read-never-written, integrity triad → checkpoint-not-ahead tamper guard → restored-chain re-verify → a documented operator cutover) + `easysynq upgrade` (pre-backup → migrate → health-gate); backup archive v2 (AES-256-GCM envelope + Keycloak realm export + config snapshot, the G-C drill kept plaintext-internal so AC#5 isn't regressed); Caddy strict static CSP + TLS 1.2 floor + air-gap internal-issuer wiring; a server-side NFR P95 smoke; and the operator [`docs/runbooks/`](docs/runbooks/). **The MVP is done.**

  See [`CLAUDE.md`](CLAUDE.md) for the per-slice detail and the v1/v1.x deferrals.

  Run it: `just up s`, then open **http://localhost** (dev login `demo` / `Demo-Password-1`).
- **v1 phase: in progress** 🟡 — the **Records & evidence** slice family ([`docs/06`](docs/06-records-and-evidence.md))
  is shipping depth-first on `main`:
  - **S-rec-1** (capture + evidence-linking + correction): immutable upload capture (base + WORM-sealed evidence in a
    dedicated `records` bucket + a `content_hash` seal), the `evidence_for_link` evidence-for sub-resource
    (record→clause/process/document), `correction_of` (correct, don't change), `source_version_id` pinning (R21), and
    **retention-policy-as-data** (a 5-tier resolver + the snapshot-at-capture ratchet).
  - **S-rec-2** (retention/disposition lifecycle): the disposition state machine + a daily Beat **retention sweep**
    (auto-disposes low-risk policies, flags the rest for human approval), the `DISPOSED` **tombstone** (metadata +
    audit survive the bytes), **legal hold**, and the R27 **dual-control WORM-destroy-under-legal-order** escape hatch
    (two distinct authorizers, fail-closed physical purge, GDPR refused-with-reason logging).
  - **S-pack-1** (Evidence Packs / UJ-7 — scope resolution + immutable build/seal): the auditor-facing headline. An
    on-demand, scope-limited (clause/process + date overlay), **immutable, self-verifying** bundle of records + their
    evidence + a traceability manifest, assembled by the worker and sealed as a `RETAIN_PERMANENT` EVIDENCE Record. The
    build is **R28-honest** — every in-scope record is classified `INCLUDED` / `EXCLUDED_PERMISSION` (the generator
    couldn't read it) / `EXCLUDED_ABSENCE` (its evidence was destroyed), so nothing is ever silently dropped, plus a gap
    report of in-scope ★ clauses lacking evidence.
  - **S-pack-2** (Evidence Packs / UJ-7 — external delivery + PDF portfolio, completes UJ-7): hand a sealed pack to an
    external auditor via a **time-boxed, revocable Ed25519 share link** — a signed token validated *outside* the PEP
    (domain-separated from the S7c verify token, fails closed if the key isn't provisioned) backed by a `pack_share_link`
    record. A **public, no-auth, latch-exempt** guest landing + `format=zip|pdf` download re-checks the revocable DB row
    on every access (revoke is immediate), audits each view, and streams the bytes through the API (no presigned URL
    outlives a revoke). The `format=pdf` **portfolio** is a printable cover + traceability index + the §11.3-stamped
    controlled-document renditions, built best-effort at seal so a renderer outage never blocks the canonical pack. (The
    heavier `guest_grant`/ABAC/Keycloak-guest path stays v1.x.)
  - **S-rec-3** (Mode-B structured-form capture — completes the records family): fill a controlled **Form/Template** to
    capture a record. A Form/Template is a controlled document (`FRM`) carrying a `field_schema` (a small, dependency-free
    field-list DSL); the schema is versioned through the normal document lifecycle (its check-in's WORM source blob **is**
    the canonical-serialized schema, also pinned into the version snapshot). Mode-B capture resolves the template's
    **Effective** version, validates the submitted `form_field_values` against **that version's pinned schema** (so
    already-captured records keep showing the edition that was in force), and pins `source_version_id`. A best-effort PDF
    rendition of the fielded data builds after capture; an org toggle (`PATCH /admin/config`, admin-only) optionally allows
    capturing against a pre-release draft for controlled migrations.
  - **S-rec-4** (records-family close-out): **`/retention-policies` CRUD + soft-archive** (a hard delete is impossible —
    three RESTRICT FKs — so retirement is a soft archive; PATCH is *extend-forward only* while records are pinned, and you
    shorten future retention by archiving a policy and creating a shorter one), plus the **creator≠disposer SoD-6** — a
    record's own capturer may not dispose it (`409 sod_self_disposition`) unless the org sets `allow_self_disposition`. Its
    two keys (`retention.read`/`retention.manage`) are the first **additive** permission-catalog extension
    (decisions-register **R38** refines "closed at v1" to "no rename/removal; additive growth allowed").

  The v1 **records family is complete** (capture → retention/disposition + management → evidence packs → structured forms).

- **v1 Ingestion engine: STARTED** 🟡 — the on-ramp that imports an existing QMS file tree into the controlled vault
  ([`docs/09`](docs/09-ingestion-engine.md), UJ-2). A depth-first family; **S-ing-1** (run + scan/inventory foundation)
  shipped: point the worker at a **read-only** mounted source tree → `POST /api/v1/admin/imports` → an idempotent,
  crash-safe scan inventories every file (size/mtime/mime/sha256 + a §4.2 filters/quarantine verdict), content-addresses
  included bytes into a non-WORM staging bucket, and produces a calm summary — all transient `import_*` rows; **it writes
  nothing to the vault** (extract/classify · dedup/propose · review · commit are slices 2–5). Migration head `0029`.

## Repository layout

```
packages/contracts/   OpenAPI-first source of truth (openapi.yaml → generated server models + TS client)
apps/api/             FastAPI / Python 3.12 (the vault, lifecycle, PDP/PEP, audit)
apps/web/             React/TS + Mantine + Tailwind SPA
migrations/           Alembic (single tree)
infra/compose/        Docker Compose stack (S/M profiles) + Caddy / Keycloak / MinIO config
scripts/              install.sh, easysynq (admin CLI), gen-contracts.sh
docs/                 the specification + the implementation plan
```

## Quick start (developer)

Requires Docker Compose v2, [uv](https://docs.astral.sh/uv/), Node 20+, and [just](https://github.com/casey/just).

```bash
just setup            # install deps, pre-commit, generate the contract
just up s             # bring up the stack (S profile)
# the API is reachable behind Caddy; /healthz and /readyz report status
```

> The four locked foundational decisions (D1–D4) and the Decisions Register (R1–R37) govern everything; see
> [`docs/00-overview.md`](docs/00-overview.md). Data never leaves the org boundary; there is no phone-home.
