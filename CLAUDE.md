# EasySynQ — Project Context

> Orientation for a new session. The **authoritative** detail lives in `docs/` — start with
> `docs/00-overview.md` (front door) and `docs/decisions-register.md` (binding decisions, R1–R40).
> The recurring-patterns catalog + the machine playbook live in `.claude/rules/`; the slice changelog
> + operator/dev reference live in `docs/` (all linked under Deep Dive — read on demand). Keep this
> file lean; new lessons go to **Recent learnings** (below) or `engineering-patterns`, not inline.

## Critical rules — NEVER violate

- **D1 — Self-hosted, single-org.** Org's own server; browser access; data never leaves their infra; admin-controlled backups; no phone-home.
- **D2 — The vault is the source of truth** (PostgreSQL + object storage). Filesystem = a read-only mirror, regenerated from Released versions only. **⚠ Authority flows vault → mirror, never the reverse.**
- **D3 — ISO 9001:2015 foundation, *architected* (not built) for Part 11 + multi-standard.** Reserved hooks (`signature_event`, `framework_id`, M:N clause mapping) — don't implement in v1, **don't remove**.
- **D4 — Stack is fixed** (see below) — do not substitute components.
- **Deny-by-default; deny-always-wins.** Hybrid RBAC + ABAC; ADMIN sits *outside* the QMS (System Administrator holds **no `document.*`**). System permissions (user/storage/backup/restore/config/import) stay admin-only.
- **⚠ Append-only / WORM invariants are load-bearing** (`audit_event` hash chain, `signature_event`, `capa_stage`/`dcr_stage_event` REVOKE UPDATE,DELETE, MinIO WORM). Any path that deletes object bytes must keep the `blob`-row-iff-bytes invariant — see `engineering-patterns`.
- **Spec/plan before code.** Get approval on a plan before implementing. When a strategic decision is the owner's, **ask** rather than silently pick.

## What this is

EasySynQ is a **self-hosted, browser-based ISO 9001:2015 Quality Management System (QMS)**. It *inverts
authority* so document drift becomes an **enforced invariant**: a managed controlled vault owns the master
copy of every controlled document/record; the on-disk filesystem is only a read-only mirror regenerated
from Released versions. UI/UX flows the way ISO 9001 flows (clause spine / process map / PDCA) — calm,
modern, progressively disclosed, never overwhelming.

## Repository layout

- `apps/api/` — FastAPI / Python 3.12. Under `src/easysynq_api/`: `api/` (routes) · `services/` (use-cases, txn owners) · `domain/` (pure logic) · `db/models/` (ORM) · `db/seeds/` · `tasks/` (Celery) · `cli/`. Tests in `apps/api/tests/{unit,integration}` (latter via testcontainers).
- `apps/web/` — React/TS + Mantine + Tailwind SPA. Web track S-web-1…7c shipped (the live list is in **Current status** + `docs/slice-history.md`). Stack-free tests: vitest + MSW + jest-axe (`npm test`); under `src/`: `app/shell/` · `features/` · `lib/` · `theme/` · `test/`.
- `migrations/` — Alembic (single tree; current head in **Current status**; `env.py` excludes migration-managed expression/partial indexes).
- `packages/contracts/openapi.yaml` — the living API contract (redocly-lint only; **not** codegen). Document new endpoints in-PR.
- `infra/compose/` — Docker Compose (S/M/L) + Caddy; `just` recipes wrap it. `docs/` — the spec (`00`–`18` + `decisions-register.md`) + `runbooks/`. `mockup/easysynq-mockup.html` — owner-approved UI mockup.

## Stack (D4 — fixed)

React/TS + Mantine + Tailwind (SPA) · FastAPI / Python 3.12 · PostgreSQL 16 + MinIO + OpenSearch + Redis ·
Celery workers · Keycloak (auth) · Gotenberg/LibreOffice (rendering) · Caddy (TLS) · Docker Compose (single host).

## Conventions

- **Document lifecycle = 7 canonical states:** `Draft → InReview → Approved → Effective → UnderRevision → Superseded → Obsolete` (the 5-state form is a simplified UI view).
- Permission keys are `domain.action` (catalog in `docs/07`; seed in `docs/14 §3.1`). **Additive-only** (R38) — no rename/removal; a new capability may add keys with a register entry (ask the owner).
- `signature_event.meaning` (v1): `review, approval, release, obsolete, verify, disposition, import_baseline, review_confirmed`; `authored`/`responsibility` reserved for Part-11.
- 8 personas: Avery (Admin), Mara (Quality Manager), Diego (Process Owner), Priya (Author), Ken (Approver), Ingrid (Internal Auditor), Sam (Employee), Olsen (External Auditor).
- **Stakeholder-locked:** import default = current-version-only (revision-chain reconstruction opt-in per family; kind always human-confirmed); tamper-evidence requires a mandatory off-host / append-only audit-checkpoint anchor.

## Workflow

- `main` is protected. Slice work on a `feat/sN-*` branch → PR → green CI → squash-merge.
- CI (all five required): `contracts` (redocly), `api` (ruff/mypy-strict/unit), `migrations` (alembic up↔down + `alembic check`), `web` (eslint/tsc/build/test), `integration` (pytest -m integration).
- Toolchain: `uv` + managed **Python 3.12** at `~/.local/bin/uv` (system `python3` is 3.14). Node 22. Docker v29.
- Run the stack: `just up s` → http://localhost; stop `just down`. ⚠ Point the app at the **non-owner** DB role for S6+ — see `docs/dev-workflow.md`.
- Apply recurring patterns by default — see `.claude/rules/engineering-patterns.md` before touching migrations, Celery workers, the workflow engine, or authz.

## Verification (run after changes)

- API: `/check-api` (ruff check + format-check + mypy-strict + pytest unit; `-m integration` needs Docker).
- Migrations: `/check-migrations` (round-trip alembic up↔down↔`alembic check` on a throwaway PG16).
- Web: `/check-web` (eslint + tsc + build + test).
- Contracts: `/check-contracts` (redocly lint on `packages/contracts/openapi.yaml`).
- Before a PR: run the `diff-critic` agent on the branch diff (see Working preferences).

## Deep Dive — read on demand

- **`docs/decisions-register.md`** — AUTHORITATIVE (R1–R40); supersedes conflicting section text. Read before any design call.
- **`docs/14-data-model.md`** (ERD) — schema source of truth; read before a migration/ORM change.
- **`docs/15-api-design.md`** — endpoints + gates; read before adding/changing an endpoint (update `openapi.yaml` in-PR).
- **`docs/07-authorization-model.md`** — permission catalog, RBAC+ABAC scoping, deny-wins; read before authz work.
- **`docs/03-architecture-and-stack.md`** — vault→mirror authority; read for cross-cutting changes.
- **`docs/18-mvp-implementation-plan.md`** — MVP slice plan + §1 canon corrections (current head in Current status).
- Section docs `00`–`17` + operator runbooks in `docs/runbooks/`. Web-UI design specs/plans in `docs/superpowers/{specs,plans}/`.
- **`.claude/rules/engineering-patterns.md`** — recurring-patterns catalog (migrations · blob/WORM · workers · workflow engine · authz · testing). Read before touching those.
- **`.claude/rules/windows-dev.md`** — this owner's native Windows 11 + Git Bash box (Docker Desktop, localhost-only auth, `just up s`/`demo-user`; no WSL). Read when on this machine.
- **`docs/slice-history.md`** — the shipped-slice changelog (MVP S0–S11 + the v1 families + the web track).
- **`docs/dev-workflow.md`** — operator/`.env` detail + the per-feature API quick-reference.

## Working preferences

- `/effort ultracode` (multi-agent Workflow orchestration) is per-session — re-enable it for heavy spec/build work.
- `.claude/agents/diff-critic.md` — a read-only adversarial reviewer pre-loaded with the load-bearing invariants. Run it on the branch diff before each PR (`Agent` tool, `subagent_type: diff-critic`).
- Persistent memory: `~/.claude/projects/<path-derived-key>/memory/` (MEMORY.md index) — the key differs per machine/OS. Keep this file's Current-status to a short pointer; the per-slice narrative lives in `docs/slice-history.md`.
- View the mockup: open `mockup/easysynq-mockup.html` in a browser.

## Recent learnings  <!-- capped ~12, newest first; demote stale ones to engineering-patterns -->

- 2026-06-10 — **S-web-8 (drift-family UI) closes the family's UI tail — FRONT-END ONLY** (no migration/key/endpoint/contract): `/drift` (Status · Superseded-copies tabs, a `drift.read`-gated rail entry — the `/ingestion` placement precedent), the `PERIODIC_REVIEW` task leg (best-effort doc context whose calm-403 NEVER blocks the decision card; **no `workflow-instances` read** — spy-proven), the doc-page Next-review tile + the `manage_metadata`-gated period modal (**explicit-null clear**), the checklist overdue leg. ⚠ Traps confirmed: an explicit `retry: undefined` key **CLOBBERS QueryClient defaultOptions** (spread the option conditionally); a reopen-pristine modal test is a false-PASS unless the field is **dirtied before Cancel**; an expected-403 hook needs `retry:false` or production re-hammers the deny 3× (the global test wrapper's `retry:false` masks it — pin with a production-defaults QueryClient test); scan `counts` is an OPEN bag (live MIRROR counts carried keys no fixture had) but PIN the asserted keys to the real serializer (`sample_limit`, not the spec's `sample_size`); a re-created Keycloak `demo` JIT row holds **no ROLE** (the System Administrator assignment sat on the OLD bootstrap row — check `role_assignment`, not just overrides, against the LIVE subject's row). Narrative: `docs/slice-history.md`.
- 2026-06-10 — **S-drift-3 (D1+D4+admin drift surface) COMPLETES the drift family** (mig `0047`). ⚠ **The alarm latch is an explicit PIN, not the absence of a stamp** (the diff-critic MAJOR): `blob.verified_at` = "last verified OK" only; a finding sets `blob.verify_failed_at`, which sorts FIRST in every rolling sample — without the pin, a once-stamped-then-corrupted blob sorts BEHIND every never-verified row, so a NULL-cursor influx > sample size (a bulk import) crowds a DETECTED corruption out of the sample and the latest-per-kind status read goes CLEAN over it (re-alarm proofs must use the ROLLING path, never only `full=True`). Pin cleared on a pass; `blob_coverage.failing` is the live count. ⚠ An `OBJECT_MISSING` finding whose ROW is gone at persist = a legal R27 disposal racing the scan → prune, don't mint a permanent false alarm. ⚠ Chunk bulk IN-list UPDATEs (psycopg caps binds at 65,535 — a `--full` stamp over a big vault rolls the whole persist back). ⚠ Infra-class failure ABORTS the scan as FAILED salvaging findings — per-object errors are findings, connection errors are not. D4 is a LIVE read (EXPORTED/PRINTED × now-Superseded/Obsolete; `render_dynamic_copy` only ever served the then-Effective version). R41 = `drift.read` (catalog 98→99). Narrative: `docs/slice-history.md`.
- 2026-06-10 — **S-drift-2 (D2+D3 mirror tamper/staleness scan) is the drift family's THESIS slice** (mig `0046`: `mirror_build` baseline + `drift_scan` summary + `MIRROR_STALE`/`MIRROR_TAMPER`). ⚠ **The mirror is never trusted as truth** — the scan's expected state is the **PG-persisted `mirror_build` manifest**, NOT the on-disk `_meta/manifest.json` (byte-verified against the stored `manifest_sha256`; its non-deterministic `generated_at` is what makes recompute impossible). ⚠ **Pointer integrity is the load-bearing fold:** the `current` symlink is verified against the registry — `resolve_pointer` matches it to the newest *swapped* row, and only the relative `.builds/<name>` shape `atomic_swap` writes is accepted (`_parse_current_target`; an absolute/out-of-tree/**basename-colliding** target is `foreign → MIRROR_TAMPER`, raw string as evidence, NEVER resolved against the filesystem — the out-of-tree-twin bypass). A **destroyed or symlink-swapped served `.builds/<name>` tree is TAMPER, not CLEAN** (a bare `is_dir()` guard follows symlinks + reports CLEAN — the C1 catch). ⚠ **R11 quarantine-before-overwrite is concrete because `_prune_builds` rmtree's the old tree every swap** → scan-first: copy/move divergent bytes to `<mirror>/.quarantine/` (0o700, re-hashed) BEFORE the rebuild. ⚠ **Scan never raises (FAILED report, salvages findings); persist returns bool and a persist-failure-with-findings DEFERS the rebuild** (it would erase the evidence the next scan needs); after FAILED/unpersisted, **re-verify `holds_advisory_lock` before rebuilding** (a mid-scan connection loss frees the session-level lock → a lockless rebuild races a concurrent sync's prune). One scan-first pipeline = every `sync_mirror` + an hourly `easysynq.mirror.scan` (`MIRROR_SCAN_INTERVAL_SECONDS`, default 3600), both under `LOCK_MIRROR_SYNC`. STALE excludes the expected version's own digests. Narrative: `docs/slice-history.md`.
- 2026-06-09 — **S-drift-1 (D5 scheduled re-review) opens the drift family** (mig `0045`). ⚠ **Org-lookup trap in migrations:** `scalar_one` on `short_code='DEFAULT'` ABORTs an operational upgrade (setup G-E renames the code); use `scalar_one_or_none` + a `SELECT the only org` fallback — 0038/0043 only ran clean because they pre-dated setup. ⚠ **psycopg3 cannot load month-bearing PG intervals into `timedelta`** → store review period as plain `INTEGER` months (not `interval`). ⚠ **`decide()` accepts ONLY `PENDING`** (engine.py:390) — the escalation sweep emits a `REVIEW_OVERDUE` audit event but NEVER flips task state; an `ESCALATED` task would be undecidable. Decision posture = **404-COLLAPSE non-membership** (never 403) + **live-owner re-check** (`doc.owner_user_id` NOW, `FOR UPDATE`) on every `PERIODIC_REVIEW` decide. T2 **auto-defaults** 24 months at submit (not a 422) so a legacy doc never strands pre-S-web-8. ⚠ A `with_for_update` SELECT on a row the **authz scope-resolver already `session.get`-loaded** returns the STALE identity-map attributes (the lock without the freshness — diff-critic MAJOR) → add `.execution_options(populate_existing=True)`; full pattern in engineering-patterns. Narrative: `docs/slice-history.md`.
- 2026-06-09 — **S-web-7d (Audits & findings) COMPLETES the S-web-7 epic** — `/audits` (Audits·Programme tabs) + `/audits/:id`; ONE thin read-enrichment (`_audit` +identifier/title/created_at via `_audit_full` on ALL single-audit responses; `_finding` +title) — no migration/key/endpoint. ⚠ Traps confirmed live: a **persistently-mounted modal keeps its post-success state across reopens** (conditionally render so close unmounts it — the suite missed it because no test REOPENED the modal); an **omitted optional field on a correction/PATCH inherits server-side** (send an explicit `""` to express a clear); a smoke override grant must land on the app_user row matching the **LIVE login's Keycloak subject** (re-created Keycloak users mint new JIT rows). Demo smoke = `audit.*`+`finding.*`+`capa.read` SYSTEM overrides (org AHT). Narrative: `docs/slice-history.md`.
- 2026-06-09 — **S-web-7c (Complaint & NCR intake) — FRONT-END ONLY** (no migration/key/contract): the `/complaints*`+`/ncrs*` surface as **tabbed sub-routes under `/capa`** (a thin `CapaLayout`; the board stays unchanged bar a `?capa=<id>` deep-link seam). Per-key gating diverges from the board — `demo` holds none → calm-403; `ncr.create`/`ncr.record_correction` are **SYSTEM-override-only in v1**. **Spawn-CAPA REQUIRES a severity** (the backend 422s without one) → a `SpawnCapaModal` confirm; a silent inherit dead-ended in the live smoke. Disposition is one-shot. Narrative: `docs/slice-history.md`; recurring SPA patterns now in engineering-patterns "Web SPA testing".
- 2026-06-09 — **S-web-7b (CAPA lifecycle writes) CLOSES the ACT-phase CAPA write loop** (raise→…→close + the M4 evidence close gate). Thin read-enrichment (no migration/key): `_task.subject_type`/`subject_id`, **`GET /capas/{id}/approval`**, `_stage.evidence_links`. ⚠ The seeded **Top-Mgmt CAPA approver holds ONLY `capa.read`** → the approval path must avoid `document.read` entirely (route via `task.subject_type` + the capa.read approval read, never the instance read / `GET /documents/{capa_id}`). Details: `docs/slice-history.md`.
- 2026-06-08 — **S-ing-4b (Ingestion Review UI) CLOSES UJ-2 — FRONT-END ONLY**. ⚠ `run.counts` is a **FLAT top-level-merged bag** (`by_band.HIGH`, top-level `quarantine`/`proposal`/`commit` — **NO `classify`/`queues`/`review` namespace**; folded review stats live on the **checklist** endpoint) — a fabricated fixture hid it (diff-critic CRITICAL → the "pin fixtures to the real serializer" rule is now in engineering-patterns). `demo` holds all 3 import keys (drives the loop, no personas). Scale needs **NO virtualization** (server `offset`/`limit`). Details: `docs/slice-history.md`.
- 2026-06-08 — **S-web-6 (Global Search + Compliance Checklist) — FRONT-END ONLY** over S10. Search **filters-not-403** (`hidden_by_scope`); Compliance hard-gated `report.compliance_checklist.read` (403 for demo → calm panel). Hand-rolled **⌘K palette** (no `@mantine/spotlight`). The recurring snippet-XSS / duplicate-`aria-label` / ⌘K-hotkeys patterns are now in engineering-patterns "Web SPA testing". Details: `docs/slice-history.md`.
- 2026-06-08 — **S-web-5 (Review & Approve) CLOSES UJ-3** — one migration-free read (`GET /documents/{id}/approval`) + the `/tasks` inbox; approve = task candidate-pool membership, release = `capabilities.release`. ⚠ A task-membership check compares **`/me`.id (`app_user.id`)**, NEVER `user.profile.sub` (diff-critic CRITICAL — pattern now in engineering-patterns). Details: `docs/slice-history.md`.
- 2026-06-08 — **S-web-4 / S-web-4b (read-only doc detail + text redline; the worker-async visual page-image diff) — FRONT-END ONLY**. The visual-diff page PNG is **authed, NOT presigned** → fetched via `apiGetBlob` (the only API-proxied binary in the SPA; pattern in engineering-patterns). Details: `docs/slice-history.md`.
- 2026-06-08 — **Native-Windows test gates (this box):** BOTH api test suites are **Linux-CI-only** here — `-m integration` (psycopg-async rejects the Windows `ProactorEventLoop`) AND `-m unit` (a native access-violation on the libmagic MIME sniff in `test_ingestion_helpers.py`). Reliable local gates = **web (`npm`)** + the **api static checks** (ruff/format/mypy). The FIRST `uv sync` must run via **PowerShell** (MSYS mangles uv's managed-Python link). Full detail: `.claude/rules/windows-dev.md`.

## Current status (as of 2026-06-10)

**MVP COMPLETE** (S0–S11). **v1 in progress** — families ✅: Records & evidence · Ingestion · Audits/Findings/CAPA ·
Revision & change depth (DCR). **Web-UI track:** S-web-1 ✅, S-web-2 ✅ (faceted Library + read-only drawer), S-web-3
✅ = Document Authoring (+ follow-ups #89 `seed-personas` · #90 browser-reachable presigned MinIO · #91 reload re-auth),
**S-web-4** ✅ (read-only Document detail page + the text/metadata redline; #93), **S-web-4b** ✅ (the worker-async
**visual page-image diff viewer**; #95 + native-Windows fix #96), **S-web-5** ✅ (#97) = Review & Approve — **CLOSES UJ-3**
(author→review→approve→release; ONE migration-free read `GET /documents/{id}/approval` + the `/tasks` inbox · per-task
Review & Approve page [redline + decision card] · the doc-page **Approvals stepper** · **Release**). **S-web-6** ✅ (#98/#99)
= Global Search + Compliance Checklist (hand-rolled **⌘K palette** + `/search?q=` ranked results [XSS-safe snippet ·
`hidden_by_scope` · filters-not-403] + a gated `/compliance` 20★ Checklist). **S-ing-4b** ✅ (#100) = Ingestion Review UI —
**CLOSES UJ-2** (front-end-only four-faces run page + review cockpit over the `/admin/imports/*` surface). **S-web-7** epic
(Nonconformity & CAPA front door) in progress: **S-web-7a** ✅ (#101) = the CAPA **read** spine (kanban board + read-only
drawer + the `_capa` `title`/`created_at`/`raised_by` enrichment); **S-web-7b** ✅ (#102) = CAPA **lifecycle writes** —
**CLOSES the ACT-phase write loop** (the six stage forms in the drawer, the contextual Advance panel, the action-plan
approval decided in `/tasks` via a subject-aware `ReviewApprovePage`, the evidence linker + the honest M4 close gate, the
board Raise modal) over a thin read-enrichment (`_task.subject_type` · `GET /capas/{id}/approval` · `_stage.evidence_links`
— no migration/key); **S-web-7c** ✅ (#103) = Complaint & **NCR intake** — front-end-only **tabbed sub-routes under
`/capa`** (a thin `CapaLayout` Board·Complaints·NCRs; the board gained a `?capa=<id>` deep-link seam) = complaints
list/log/**idempotent spawn-CAPA** + NCRs list/raise/**one-shot ISO 8.7 disposition**, per-key calm-403 gating (the demo
admin holds none; `ncr.create`/`ncr.record_correction` are SYSTEM-override-only in v1); **S-web-7d** ✅ = Audits &
**findings** — **COMPLETES the epic** (the CHECK-phase internal-audit module over ONE thin read-enrichment [`_audit`
+identifier/title/created_at · `_finding` +title — no migration/key/endpoint]: `/audits` Audits·Programme tabs + the
`/audits/:id` page = programmes/plans upkeep · the New-audit cascade · the 7-node lifecycle stepper with ONE legal
Advance [conduct→close gate swap] · findings log/correct with the NC→**auto-CAPA** deep-linking the board drawer ·
the **R39 close gate** surfaced calmly + an honest client close-readiness note mirroring `finding_blocks_close`).
**551 web tests**; subagent-driven TDD (per-task spec→quality review). Still open:
the PDCA dashboard (deferred until acks/objectives land). **S-drift-1** ✅ (#106) = D5 **scheduled re-review** — mig `0045` review columns (`review_period_months` int, `next_review_due` date STORED, `last_reviewed_at`), the seeded `periodic_review` workflow, the daily Beat sweep + owner `PERIODIC_REVIEW` tasks, the `review_confirmed` signature + clock reset, the checklist overdue leg; 24 unit + 17 integration tests. **S-drift-2** ✅ (pending merge) = D2+D3 **mirror tamper/staleness scan** — the family's THESIS slice: mig `0046` (`mirror_build` vault-side baseline + `drift_scan` summary + `MIRROR_STALE`/`MIRROR_TAMPER`); the scanner `services/vault/mirror_scan.py` (PG manifest is authority, the on-disk one is byte-verified never trusted; **`current`-pointer integrity** verified against the registry; STALE excludes the expected version's own digests; quarantine-before-rebuild [copy/move, 0o700, re-hashed]; scan-first composed into every `sync_mirror` + an hourly `easysynq.mirror.scan` [`MIRROR_SCAN_INTERVAL_SECONDS` def 3600] under `LOCK_MIRROR_SYNC`; scan never raises/FAILED-salvages, persist-failure-with-findings defers the rebuild + re-verifies the lock); CLI `mirror scan`/`sync`/`rebuild`; 19 unit + 12 integration tests; no key/endpoint/contract change. **S-drift-3** ✅ = D1 blob verify + D4 superseded-copies + the admin drift-status surface (mig `0047`: BLOB_REHASH kind + BLOB_INTEGRITY_FAILED + the R41 `drift.read` key; `services/vault/blob_verify.py` daily rolling re-hash [verified_at on pass + the `verify_failed_at` PIN sorted first → persistent re-alarm; `LOCK_BLOB_VERIFY`; `easysynq.blob.verify` + CLI `blob verify --full`] + `services/vault/drift_report.py` live D4 read + `GET /admin/drift/status` + `/admin/drift/superseded-copies`). **S-web-8** ✅ = the **drift-family UI** — front-end-only (no migration/key/endpoint/contract): `/drift` (Status + Superseded-copies tabs, `drift.read`-gated rail entry) · the `PERIODIC_REVIEW` task leg in `/tasks` (best-effort doc context, the complete/changes_requested decision variant + review-confirmed signature) · the doc-page Next-review tile + `manage_metadata`-gated period modal · the checklist overdue-review leg; +52 web tests; full live smoke incl. the inherited authed-200 leg of both drift endpoints. **The drift family (D1–D5) is COMPLETE, UI included.** **Migration head `0047` (next `0048`) — S-drift-3 added `0047`.** Full per-slice narrative + deferred residuals: `docs/slice-history.md`.
