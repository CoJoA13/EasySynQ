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
- `migrations/` — Alembic (single tree; head **`0027`**; `env.py` excludes migration-managed expression/partial indexes).
- `packages/contracts/openapi.yaml` — the living API contract (redocly-lint only; **not** codegen — server/web aren't generated from it).
- `infra/compose/` — Docker Compose (S/M/L profiles) + Caddy; `just` recipes wrap it.
- `docs/` — the authoritative spec (`00`–`18` + `decisions-register.md`); `mockup/` — the owner-approved HTML UI mockup.

## Current status (as of 2026-06-05)

**MVP COMPLETE** (all 11 ordered slices S0–S11 shipped to `main` via PR, all CI green, validated on the real
Docker stack; the exit slice S11 is PR #41). All six MVP acceptance proofs are in; the mirror epic + both IA
backends are complete; the doc-18 §12 exit checklist is closed. The design was locked first (spec-before-code);
v1/v1.x residuals are listed at the end of this section.

**v1 phase: STARTED.** The owner chose (AskUserQuestion) the **v1 feature** track → **Records & evidence
(doc 06)** as the slice family (over the web track + the v1.x backend residuals); **S-rec-1**, **S-rec-2**,
the **Evidence Packs (UJ-7)** family (**S-pack-1** build/seal, **S-pack-2** external delivery + PDF portfolio),
**S-rec-3** (Mode-B structured-form capture), then **S-rec-4** (the records-family close-out:
`/retention-policies` CRUD + creator≠disposer SoD-6) shipped depth-first — completing UJ-7 **and** the records
family. The owner then chose (AskUserQuestion) **Ingestion (doc 09, UJ-2)** as the next family — depth-first slices
**S-ing-1** (run + scan/inventory foundation) + **S-ing-2** (extract + classify) + **S-ing-3** (dedup + version-families +
proposal) + **S-ing-4** (the human-in-the-loop review: decisions + merge/split + the pre-commit checklist) + **S-ing-5** (the
COMMIT — finally writes the confirmed set into the vault: per-item Effective Rev A documents + immutable Records +
`import_baseline` + provenance + the Import Report + mirror) shipped depth-first — **the Ingestion family (UJ-2) is now
COMPLETE**. The family dependency posture is **full-fidelity** (Tesseract + Tika + OpenSearch) — Tika+Tesseract landed at
S-ing-2; **near-dup at S-ing-3 ships as in-process MinHash** (the doc 09 §14 path) behind a `DedupDetector` seam, with the
OpenSearch container itself deferred (R34 honest: OpenSearch stays absent in MVP/v1; the OpenSearch-backed detector/indexer
are the reserved drop-ins). **Migration head is now `0033`.**

**v1 RECORDS & evidence family (UJ-7 + records) — COMPLETE** ✅ (one line each; per-slice non-obvious
decisions live in the squash-merge commits + the `easysynq-project.md` memory; operating detail in the
dev-workflow quick-reference below):

- **S-rec-1** Records capture + evidence-linking + correction (PR #43, `0023`) — atomic WORM-sealed
  immutable capture (`documented_information[kind=RECORD]` + `record` subtype + domain-separated
  `content_hash`), polymorphic `evidence_for_link`, correction-via-new-record, retention-policy-as-data
  (5-tier resolver + per-org *System Default* + snapshot-at-capture ratchet).
- **S-rec-2** Records retention/disposition lifecycle (PR #46, `0024`) — the disposition state machine +
  daily Beat sweep + legal-hold + the R27 dual-control WORM-destroy-under-legal-order hatch (fail-closed,
  purge-FIRST, `RECORD_ERASURE_REFUSED` 409); `disposition_event` tombstone + `worm_destroy_request`.
- **S-pack-1** Evidence Packs build/seal (PR #48, `0025`) — an immutable scope-limited (CLAUSE/PROCESS +
  date) bundle sealed as a RETAIN_PERMANENT EVIDENCE Record; R28-honest classification (`pack_item` IS the
  exclusion report) + gap report; an idempotent `.delay` worker build + a stalled-build reaper.
- **S-pack-2** Evidence Packs external delivery + PDF portfolio — **completes UJ-7** (PR #50, `0026`) — an
  Ed25519 signed share-token outside the PEP + a DB-backed revocable `pack_share_link`; a public no-auth
  latch-exempt guest surface (HTML landing + a streamed `zip|pdf` download re-checked per access,
  `PACK_DOWNLOADED` audited); the PDF portfolio is a best-effort seal Stage 2.
- **S-rec-3** Mode-B structured-form capture (PR #52, `0027`) — a Form/Template is an `FRM` DOCUMENT + a
  `form_template` shared-PK subtype holding a dependency-free field-list DSL (no regex → no ReDoS), frozen
  into `document_version.metadata_snapshot` at check-in; Mode-B capture validates `form_field_values`
  against the pinned schema.
- **S-rec-4** Records-family close-out: `/retention-policies` CRUD + creator≠disposer SoD-6 (PR #54, `0028`,
  **R38**) — opens the catalog ADDITIVELY (R38 refines R5: closed = no rename/removal; additive growth is
  allowed) with `retention.read`/`.manage`; CRUD + soft-archive (extend-forward-only PATCH); SoD-6 is a
  service-layer gate (`advance_disposition`, NOT the PDP), relaxed only by `allow_self_disposition`.

**v1 INGESTION engine family (doc 09, UJ-2) — COMPLETE** ✅:

- **S-ing-1** run + scan/inventory foundation (`0029`) — the transient `import_*` staging layer
  (`import_run` state machine + `import_file` inventory, UNIQUE(run,rel_path)); an idempotent fail-closed
  scan worker walks a `:ro` source root (NG3 confinement), content-addresses included bytes into
  `import-staging`, under a Redis source-root lock + a stalled-run reaper. Writes nothing to the vault.
- **S-ing-2** extract + classify (PR #57, `0030`) — auto-chains scan→extract→classify; an Apache Tika
  `-full` sidecar (extract + OCR over HTTP) + a pure `RuleHeuristicClassifier` over a versioned YAML
  rule-pack (capped weighted sum; bands High/Med/Low; kind scored-only, UNKNOWN below a floor — confirmed
  at S-ing-4, R10). The source-root lock is held continuously across the stages.
- **S-ing-3** dedup + version-families + proposal (PR #58, `0031`) — auto-chains to Proposed; in-process
  MinHash behind a `DedupDetector` seam (the §14 path; the OpenSearch container is NOT added — R34), the
  §7.1 exact→near→family cascade with a provably-total canonical pick, and the §8 per-keep-item proposal
  (identifier preserve-verbatim-else-`{type}-<new>`; never consumes `NumberingCounter`).
- **S-ing-4** human-in-the-loop review (PR #60, `0032`) — append-only `import_decision` rows folded at read
  (`review.fold_file_decisions`, the single commit-gate source: `commit_ready ⇔ included AND
  kind∈{DOCUMENT,RECORD}`; the R10 kind-confirm rides `after.kind`, NEVER on the engine classification) +
  live-mutating merge/split + the §9.3 pre-commit checklist. The lock-free `Reviewing` rest-state.
- **S-ing-5** the COMMIT — writes the confirmed set into the vault (PR #62, `0033`) — `commit_ready`
  keep-items → Effective **Rev A** documents + immutable Records, per-item txn + idempotent (the
  `import_commit_result` ledger CLAIM = single-flight) + resumable (PartiallyCommitted → re-POST resumes);
  the import-baseline cutover (Effective-directly, no SERIALIZABLE cutover, a single
  `signature_event(meaning=import_baseline)`, R2); `import_provenance` fold (doc 14 §5.1); the §12.1 Import
  Report (a RETAIN_PERMANENT EVIDENCE Record + the mirror `_ImportReport/` export); per-doc audit
  (`scope_ref=identifier`, AC#6); `reap_stalled_commits`.

**v1 AUDITS/FINDINGS/CAPA family (doc 02 Cl 9.2/10.2, doc 10 §5-6, UJ-5/UJ-6) — STARTED** (owner decisions R39:
+declarative-routing posture · severity-aware SoD-4 · block-until-corrected audit close · `audit_program` own-table):

- **S-aud-1** audit programmes/plans/audits + lifecycle FSM (`0034`) — `audit_program`+`audit_plan` own-table
  scheduling containers + `audit` as a `kind=RECORD` shared-PK subtype (captured via `capture_record(_commit=False)`,
  REC-shared identifier, mutable `state`); the linear FSM Scheduled→…→Closing→Closed (FOR-UPDATE + audited-then-commit;
  Closing→Closed close-gate is a **no-op stub** until S-aud-2 wires the live-NC-findings check); `/audit-programs`+
  `/audit-plans`+`/audits`+6 flat-action transitions (gates `audit.{plan,create,conduct,close,read}` — all pre-seeded,
  PROCESS conduct/close via an `_audit_scope` resolver w/ SYSTEM fallback); programme/plan events reuse
  `audit_object_type=audit`, the audit record's reuse `record`. **Migration head is now `0034`.**

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

- **S8a–S8d** first-run setup + admin (PRs #16/#18/#20/#22/#24) — the **423 setup-latch** +
  **bootstrap-of-trust** (`easysynq setup mint-bootstrap` → the first System Administrator) + the
  extensible **gate registry** (G-A admin · G-B WORM-probe · G-C backup→restore-drill [AC#5] · G-D
  auth-proof · G-E org-profile) → the one-way `UNINITIALIZED→IN_SETUP→OPERATIONAL` finalize; then Users &
  Roles admin + invite/enable-disable (R35 two-tier guard; last-admin guard). A Mantine `<Stepper>` wizard.
- **S9/S9b/S9c/S9d** the two IA backends + the mirror tree (PRs #27/#31/#32/#33) — the read-only ISO 9001
  **clause spine** (83-clause/20★ catalog, `db/seeds/iso9001_clauses.py`) + M:N `clause_mapping` + the
  submit-needs-≥1-mapping gate (`0017`/`0018`); the **process graph** (`process`/`process_edge`/
  `process_link`, `0019`); the §10.3 mirror **clause tree** (`{PLAN|DO|CHECK|ACT}/{NN-Name}/`) + a
  `by-process/` index (pure `mirror.py`, no migration). Authoring rides SYSTEM overrides until owner-assignment.
- **OpenAPI catch-up** (PR #35) — `packages/contracts/openapi.yaml` is redocly-lint ONLY (no codegen);
  **document new endpoints in-PR going forward**.
- **S10** search/reporting backend (PR #38, backend only) — the org-wide **Compliance Checklist**
  `GET /reports/compliance-checklist` (the 20★ clauses → COVERED/PARTIAL/GAP + rollup) + Postgres-FTS
  `GET /search(/suggest)` behind the `Indexer` seam (OpenSearch the v1 drop-in, R34; Effective-only +
  filter-not-403) + `clause_refs` + the doc-15 bracketed `filter[field][op]` grammar on `GET /documents` (`0020`).
- **S11** the MVP EXIT slice (PR #41) — operator-grade `easysynq restore` (WORM-aware
  restore-to-VERIFIED-TARGET) + `easysynq upgrade` (pre-backup → migrate → health-gate) + backup archive v2
  (AES-256-GCM, only-if-encrypted) + a strict static Caddy CSP + 9 operator runbooks (`docs/runbooks/`) (`0022`).

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
- **Feature quick-reference (all API/data only, NO web UI yet).** Endpoints + gates are in `docs/15` +
  `packages/contracts/openapi.yaml`; per-feature operating depth is in the `easysynq-project.md` memory + the
  squash-merge commits. Most v1 feature keys reach no concrete object at their seeded scope, so **ride a
  SYSTEM override** until the role/UI lands (the `document.export`/`record.*` precedent) — EXCEPT `import.*`
  (SYSTEM-scope, held by the System Administrator bundle, no override dance).
  - **Users & Roles admin (S8d):** as a System Administrator → `/admin/users` to invite (paste the Keycloak
    `sub`; `INVITED→ACTIVE` on first login), assign/revoke seeded roles, add overrides (R35 two-tier guard),
    enable/disable (last-active-admin guarded). `/admin/roles` is read-only.
  - **Clause IA + mapping (S9):** `GET /clauses` (gate `clauseMap.read`); a doc needs ≥1 clause before
    `submit-review` (422 else) — `POST /documents/{id}/clause-mappings` / `DELETE …`.
  - **Process IA (S9c):** `GET /processes(/{id})(/map)` (gate `process.read`); authoring on
    `process.create`/`.manage` (SEED→ACTIVE) + `document.manage_metadata` for links. **S9d** mirrors links
    under `current/by-process/{name}/`.
  - **Search + Compliance Checklist (S10):** `GET /reports/compliance-checklist`
    (gate `report.compliance_checklist.read`) + `GET /search(/suggest)` (authenticated; filter-not-403;
    Effective-only). `GET /documents` takes `filter[field][op]` (e.g. `filter[clause_refs][has]=8.4`).
  - **Records (S-rec-1..4):** capture via `POST /records:init-upload` → `POST /records`
    (`{record_type,title,evidence:[{sha256}],source_document_id?,…}`; R21 pins `source_version_id` under a
    controlled doc); correct via `POST …/correction`; link via `…/evidence-links`. Disposition:
    `PATCH …/{id}/disposition`, `POST …/legal-hold`, the R27 `POST …/worm-destroy-requests` + a distinct
    approver. Retention: `/retention-policies` CRUD (extend-forward-only; System Default protected). **SoD-6:**
    a record's `captured_by` cannot self-dispose unless an admin flips `allow_self_disposition`
    (`PATCH /admin/config`) — ⚠ a single-operator install must flip it. Evidence must be freshly WORM-sealed
    in the `records` bucket (a foreign-bucket sha is 423).
  - **Evidence Packs (S-pack-1/2):** `POST /evidence-packs` (DRAFT + R28 preview) → `POST …/generate` (202,
    worker build) → poll SEALED → `GET …/download`. Deliver: `POST …/share` (revocable Ed25519 link, raw
    token returned once) → public `GET /evidence-packs/shared?t=…` + `…/shared/download?format=zip|pdf`.
  - **Mode-B forms (S-rec-3):** create an `FRM` doc → `PUT /documents/{id}/form-schema` →
    `POST …/form-schema:checkin` → map a clause → release Effective; then `POST /records {source_document_id:
    <the FRM doc>, form_field_values}` validates against the pinned schema. Pre-release capture:
    `PATCH /admin/config {capture_pre_release_templates:true}`.
  - **Ingestion (S-ing-1..5):** point the worker at a source tree (`IMPORT_SOURCE_PATH` → a `:ro` mount at
    `/srv/import/source`); bring up the Tika `-full` sidecar (`TIKA_URL`). `POST /admin/imports {source_root,
    ocr_enabled?}` (gate `import.execute`) auto-chains scan→extract→classify→dedup→**Proposed**. Review (gate
    `import.review`): `…/files/{id}/decision`, bulk `…/decisions`, `…/merge`/`…/split`, `GET …/checklist` →
    **Reviewing**. **Commit** (gate `import.commit`): `POST …/{id}/commit` → Committing → **Completed /
    PartiallyCommitted** (re-POST resumes); writes Effective Rev A docs + Records + the §12.1 Import Report;
    per-doc audit at `GET /documents/{id}/audit-events`. Crashes self-recover via
    `reap_stalled_runs`/`reap_stalled_commits`. No new service container; commit holds NO source-root lock.
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
- **A new model module MUST be imported in `db/models/__init__.py`** (+ added to `__all__`) — that file is the sole place
  `Base.metadata` is populated; a CREATEd table whose model isn't imported makes `alembic check` report a phantom-DROP and
  the `migrations` CI job goes red (the 0027 `form_template` lesson; the `tasks/__init__.py` registration precedent).
- **Backup/restore drills run as the OWNER role** (`DATABASE_URL_SYNC`; the app role can't `pg_dump`/`CREATE DATABASE`)
  and **never raise** — a missing binary/crash is an honest FAIL, never a 500.
- **Run the FULL integration suite for mirror/symlink work** — Py3.12 `rglob` follows symlinks, so dir-finders must filter
  `not is_symlink()` and byte-scans use `os.walk(followlinks=False)`; cross-file test pollution only surfaces in the full run.
- **Keep the `blob`-row-iff-bytes invariant** (the S-rec-2 lesson, found by CI not local since the restore tests are
  pg_dump-gated): any path that physically deletes object bytes (the WORM-destroy / sweep DESTROY) MUST also drop the
  `blob` row + its `evidence_blob` links — else the backup manifest + restore drill (`_copy_blobs`/`_rehash`) iterate
  **all** `blob` rows and crash `NoSuchKey` on the dead one (after the first disposal, every backup/restore breaks). A
  destroyed record's tombstone is the `disposition_event` + the record `content_hash`, not a dangling `blob` row.
  **Corollary (S-rec-3):** a NEW per-record derived-rendition `blob` row reachable only by a plain-Text pointer (e.g.
  `record.structured_pdf_blob_sha256`, NOT an `evidence_blob`) is invisible to the evidence purge loop — wire its purge into
  the **shared** `_purge_record_evidence` (so ALL three DESTROY paths cover it), drop the row + bytes + null the pointer.
  Fold the record id into the rendered bytes (per-record sha) so the purge needs no liveness guard.
- **Versioned "content-as-data" via the document lifecycle (S-rec-3):** when a thing's controlled content is structured
  data (a form schema), make it the version's source blob (canonical-serialize → server-side staging-PUT →
  `finalize_worm`, NO client upload) AND snapshot it into `document_version.metadata_snapshot` in ONE txn from the SAME
  in-memory object — never branch the shared `_snapshot(doc)` (keep ordinary docs untouched). Read it back from the
  **version snapshot** (immutable), never the mutable working row, so the pin survives a revision. Mark such a structured
  source blob non-renderable in the mirror (S-rec-3 added `application/json`/`xml` to
  `render_gotenberg._NON_RENDERABLE_PREFIXES` → the FRM template version lands `no_controlled_rendition` (R26),
  source-bytes-only, never a garbage CONTROLLED COPY — else a JSON schema blob would route to LibreOffice).
- **Review rhythm:** N adversarial lenses → per-finding verify → fold only confirmed. Prefer hunting the *false-PASS*
  direction on any gate/proof.
- **Authz for not-yet-UI'd domains:** seed the permission keys but expect them to reach no concrete object at their seeded
  scope → ride on **SYSTEM overrides** until the role/UI lands (the `document.export`/`process.create`/`record.*` precedent).
- **The permission catalog is ADDITIVE-only (R38), not frozen.** "Closed at v1" (R5) means **no rename/removal** — but a
  genuinely new capability MAY add keys with a decisions-register entry (S-rec-4's `retention.read`/`retention.manage` were
  the first). Prefer riding an existing key when one fits; open the catalog only when none does (it's a register-level call —
  ask the owner). New keys: seed via `pg_insert(...).on_conflict_do_nothing(["key"])` mirroring `0004`'s
  `(key, is_system_domain, sod_sensitive, sig_hook, finest_scope)` shape; an **org-level** resource uses `finest_scope=SYSTEM`
  + `require(...)`'s default `_system_scope` (the `config.update` mechanic); a downgrade deletes **role_grant before
  permission** (the RESTRICT FK); bump the catalog-count assertion in `test_authz.py`.
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
- **A multi-stage worker pipeline (S-ing-2 scan→extract→classify) holds ONE lock continuously across stages + a
  lock-liveness reaper** — NOT a per-stage release/re-acquire (a between-stages window a liveness reaper misreads as
  stalled) and NOT an age-based reaper (false-fails a legitimately long OCR stage). Each stage `.delay`-chains the next
  best-effort after its commit (the `_enqueue_structured_pdf` precedent); the lock heartbeats per batch and frees only at
  the terminal rest; the reaper checks **whether the Redis lock key still exists** (a missing lock on an in-progress run =
  dead worker) + a generous absolute backstop. A dropped chain-enqueue self-heals to FAILED once the TTL lapses (operator
  re-runs; resume is cheap via the `WHERE NOT EXISTS(<stage row>)` batch query). The earlier-stage's terminal state stops
  being terminal (Scanned → cancellable) — update its `_TERMINAL` tuple + the cancel/active-run checks + the existing test.
- **A classifier rule pack is a versioned YAML resource, not buried code** (S-ing-2, doc 09 §6.3): matchers/weights/
  explanations load + schema-validate at startup (`domain/ingestion/rule_pack.py`); calibrate weights against the spec's
  worked examples (a capped weighted sum `min(100, Σ fired)` reproduces doc 09 §6.5's 92/96/90/88). **ReDoS confinement:**
  allow regex ONLY on length-capped targets (filename/header), use substring keywords on content/path, and reject
  nested-quantifier patterns at load (untrusted org-override loading stays deferred). A *measured* accuracy band (R10) ships
  as a labeled hold-out corpus + a harness that IS the validation test, published **INTERIM-synthetic** (real-corpus is v1.x).
- **A lock-free, human-paced rest-state must be kept OUT of the reaper's in-progress/active sets** (the S-ing-4 `Reviewing`
  lesson — the #1 trap). The S-ing lock-liveness reaper FAILs any run in `service._IN_PROGRESS` whose Redis source-root lock has
  lapsed; the lock is freed at `Proposed`, so a state a human dwells in (review) is lock-free — putting it in `_IN_PROGRESS`
  (or `repository._ACTIVE_STATES`) makes the reaper kill a run mid-review. Add such a state to NEITHER set (and not `_TERMINAL`,
  so cancel still works) — gate the new writes on a separate `_REVIEWABLE` tuple instead. The additive-enum ADD VALUE still applies.
- **Human dimensional intent folds at read; structural intent is materialized** (the S-ing-4 review model). Per-item dimensional
  decisions (kind-confirm/type/clause/owner/identifier + accept/exclude/defer) live ONLY in an **append-only** decision log and
  are folded newest-wins at read (the single `fold_*` used by the checklist + the commit gate) — the **R10 kind-confirm rides the
  decision's `after.kind`, NEVER written back to the immutable engine classification**. Structural reshaping (merge/split) DOES
  mutate the materialized grouping rows, because the keep-set derivation reads them. When mutating one grouping row, **preserve
  every OTHER group's opt-in flags** (targeted ORM edits + read-current-carry-forward; a naïve full DELETE-then-INSERT replace
  resets a default-false flag like `reconstruct_revision_chain`), **recompute + persist the canonical/effective member BEFORE**
  re-deriving the downstream nodes (the keep-set reads `effective_file_id`/`canonical_file_id` — a stale one silently drops
  files), **delete a group that drops <2 members** (a 1-member group with a dangling canonical drops its survivor), and let the
  **exclude/defer fold win over** structural membership everywhere readiness/commit is computed. Reassign ARRAY columns (not
  in-place `.append`/`.remove` — SQLAlchemy doesn't track in-place mutation of a plain ARRAY).
- **Integration assertions must be delta-based / run-scoped, never assume a clean shared DB** (the S-ing-4 lesson — it
  passed the targeted-subset local run but failed the full CI suite). The `-m integration` suite shares ONE session DB across
  all files; earlier files leave vault docs/orgs behind. So a test that asserts an absolute (`documented_information == 0`, or a
  checklist's global `ready is True`) breaks once another file has run first. Assert a **delta** (capture counts before → assert
  unchanged after) or scope to **this run's** rows / the **specific** entity you created (e.g. "the duplicate-identifier conflict
  I introduced appears/disappears", not the global `ready` — a prior test's vault doc may collide). Reproduce locally by running
  a doc-creating file BEFORE the touched file (`pytest -m integration tests/integration/test_vault.py <touched>.py`).
- **A replay/no-op path that `rollback()`s must capture any ORM ids it returns BEFORE the rollback** (the S-ing-4
  Idempotency-Key lesson). `session.rollback()` expires every loaded instance; a subsequent `str(row.id)` (or any attr access)
  triggers a lazy refresh whose I/O, on an async session, surfaces later as a `MissingGreenlet` at connection-pool close — a
  confusing teardown crash, not a clean error. Read what you need into locals first, then rollback, then return a plain dict.
  For a bulk op keyed by one `Idempotency-Key`, stamp the key on a SINGLE row (a partial-UNIQUE `(run_id, key)` forbids N rows).
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
- **A worker that makes MANY independent transactions (the S-ing-5 per-item commit) opens a FRESH session PER unit, not one
  reused session.** Reusing one `AsyncSession` across commit→exception→rollback→commit cycles trips a `MissingGreenlet` at the
  *pool teardown* (a pre-ping on a connection returned in a post-exception state runs outside the greenlet) — invisible to a
  green local run, fatal in the suite. The worker task hands the body a **sessionmaker** (`_with_sessionmaker`, the fresh-engine
  precedent) and each item does `async with sm() as s: … await s.commit()`; a failed item's ledger write + the terminal flip
  each open their own session. Per-item isolation also means an exception in one item never poisons the next.
- **Cross-process single-flight without a lock = an atomic ledger CLAIM** (the S-ing-5 commit, over a per-run advisory lock —
  which can't span per-item commits + tripped the teardown bug above). `INSERT … ON CONFLICT(run,file) DO UPDATE SET …
  WHERE result='failed' RETURNING id` as the LAST write in the per-item txn makes two concurrent workers (a reaper re-enqueue
  alongside a slow worker) commit each item exactly-once: the loser's INSERT blocks on the winner's uncommitted row, then the
  `WHERE result='failed'` guard no-ops its DO UPDATE (no row returned) → it rolls its half-built rows back. For an allocated
  ({TYPE}-{AREA}-{SEQ}) doc the loser's `allocate_seq` increment rolls back with the txn (no counter leak); for a preserved
  identifier the `documented_information` UNIQUE is the backstop.
- **Importing a pre-existing controlled doc is its OWN lifecycle path — Effective-directly, NOT the authoring FSM** (S-ing-5):
  a brand-new imported version is created at `version_state=Effective` + `current_state=Effective` in one per-item txn (INV-1
  trivially holds — no prior Effective to supersede, so no SERIALIZABLE `_cutover` needed) with a single
  `signature_event(meaning=import_baseline)` (R2). Do NOT route it through `create_document`/`checkin`/`release` (they commit
  internally, walk Draft→Approved→Effective, require the ≥1-clause submit gate, and emit approval/release signatures). Its
  per-doc audit is `IMPORT_ITEM_COMMITTED` (object_type=document, **scope_ref=identifier** so `GET /documents/{id}/audit-events`
  surfaces it), a deliberate divergence from the authored `DOCUMENT_CREATED`/`RELEASED` shape.
- **`mirror._write` must be parent-safe** (`path.parent.mkdir(parents=True, exist_ok=True)`): a new two-level mirror section
  (S-ing-5's `current/_ImportReport/<run>/`) whose parent isn't pre-`mkdir`'d crashes the WHOLE `build_tree`/`sync_mirror` with
  `FileNotFoundError` — and since `_write` runs after `atomic_swap`-prep, it freezes the published tree. A unit test that drives
  `build_tree` with a non-None session + a monkeypatched `fetch_import_reports` row exercises the path no other test reaches
  (the diff-critic's CRITICAL catch — production-only, green-suite-invisible).

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
