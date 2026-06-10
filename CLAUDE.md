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

- 2026-06-09 — **S-drift-1 (D5 scheduled re-review) opens the drift family** (mig `0045`). ⚠ **Org-lookup trap in migrations:** `scalar_one` on `short_code='DEFAULT'` ABORTs an operational upgrade (setup G-E renames the code); use `scalar_one_or_none` + a `SELECT the only org` fallback — 0038/0043 only ran clean because they pre-dated setup. ⚠ **psycopg3 cannot load month-bearing PG intervals into `timedelta`** → store review period as plain `INTEGER` months (not `interval`). ⚠ **`decide()` accepts ONLY `PENDING`** (engine.py:390) — the escalation sweep emits a `REVIEW_OVERDUE` audit event but NEVER flips task state; an `ESCALATED` task would be undecidable. Decision posture = **404-COLLAPSE non-membership** (never 403) + **live-owner re-check** (`doc.owner_user_id` NOW, `FOR UPDATE`) on every `PERIODIC_REVIEW` decide. T2 **auto-defaults** 24 months at submit (not a 422) so a legacy doc never strands pre-S-web-8. Narrative: `docs/slice-history.md`.
- 2026-06-09 — **S-web-7d (Audits & findings) COMPLETES the S-web-7 epic** — `/audits` (Audits·Programme tabs) + `/audits/:id`; ONE thin read-enrichment (`_audit` +identifier/title/created_at via `_audit_full` on ALL single-audit responses; `_finding` +title) — no migration/key/endpoint. ⚠ Traps confirmed live: a **persistently-mounted modal keeps its post-success state across reopens** (conditionally render so close unmounts it — the suite missed it because no test REOPENED the modal); an **omitted optional field on a correction/PATCH inherits server-side** (send an explicit `""` to express a clear); a smoke override grant must land on the app_user row matching the **LIVE login's Keycloak subject** (re-created Keycloak users mint new JIT rows). Demo smoke = `audit.*`+`finding.*`+`capa.read` SYSTEM overrides (org AHT). Narrative: `docs/slice-history.md`.
- 2026-06-09 — **S-web-7c (Complaint & NCR intake) — FRONT-END ONLY** (no migration/key/contract): the `/complaints*`+`/ncrs*` surface as **tabbed sub-routes under `/capa`** (a thin `CapaLayout`; the board stays unchanged bar a `?capa=<id>` deep-link seam). Per-key gating diverges from the board — `demo` holds none → calm-403; `ncr.create`/`ncr.record_correction` are **SYSTEM-override-only in v1**. **Spawn-CAPA REQUIRES a severity** (the backend 422s without one) → a `SpawnCapaModal` confirm; a silent inherit dead-ended in the live smoke. Disposition is one-shot. Narrative: `docs/slice-history.md`; recurring SPA patterns now in engineering-patterns "Web SPA testing".
- 2026-06-09 — **S-web-7b (CAPA lifecycle writes) CLOSES the ACT-phase CAPA write loop** (raise→…→close + the M4 evidence close gate). Thin read-enrichment (no migration/key): `_task.subject_type`/`subject_id`, **`GET /capas/{id}/approval`**, `_stage.evidence_links`. ⚠ The seeded **Top-Mgmt CAPA approver holds ONLY `capa.read`** → the approval path must avoid `document.read` entirely (route via `task.subject_type` + the capa.read approval read, never the instance read / `GET /documents/{capa_id}`). Details: `docs/slice-history.md`.
- 2026-06-08 — **S-ing-4b (Ingestion Review UI) CLOSES UJ-2 — FRONT-END ONLY**. ⚠ `run.counts` is a **FLAT top-level-merged bag** (`by_band.HIGH`, top-level `quarantine`/`proposal`/`commit` — **NO `classify`/`queues`/`review` namespace**; folded review stats live on the **checklist** endpoint) — a fabricated fixture hid it (diff-critic CRITICAL → the "pin fixtures to the real serializer" rule is now in engineering-patterns). `demo` holds all 3 import keys (drives the loop, no personas). Scale needs **NO virtualization** (server `offset`/`limit`). Details: `docs/slice-history.md`.
- 2026-06-08 — **S-web-6 (Global Search + Compliance Checklist) — FRONT-END ONLY** over S10. Search **filters-not-403** (`hidden_by_scope`); Compliance hard-gated `report.compliance_checklist.read` (403 for demo → calm panel). Hand-rolled **⌘K palette** (no `@mantine/spotlight`). The recurring snippet-XSS / duplicate-`aria-label` / ⌘K-hotkeys patterns are now in engineering-patterns "Web SPA testing". Details: `docs/slice-history.md`.
- 2026-06-08 — **S-web-5 (Review & Approve) CLOSES UJ-3** — one migration-free read (`GET /documents/{id}/approval`) + the `/tasks` inbox; approve = task candidate-pool membership, release = `capabilities.release`. ⚠ A task-membership check compares **`/me`.id (`app_user.id`)**, NEVER `user.profile.sub` (diff-critic CRITICAL — pattern now in engineering-patterns). Details: `docs/slice-history.md`.
- 2026-06-08 — **S-web-4 / S-web-4b (read-only doc detail + text redline; the worker-async visual page-image diff) — FRONT-END ONLY**. The visual-diff page PNG is **authed, NOT presigned** → fetched via `apiGetBlob` (the only API-proxied binary in the SPA; pattern in engineering-patterns). Details: `docs/slice-history.md`.
- 2026-06-08 — **Native-Windows test gates (this box):** BOTH api test suites are **Linux-CI-only** here — `-m integration` (psycopg-async rejects the Windows `ProactorEventLoop`) AND `-m unit` (a native access-violation on the libmagic MIME sniff in `test_ingestion_helpers.py`). Reliable local gates = **web (`npm`)** + the **api static checks** (ruff/format/mypy). The FIRST `uv sync` must run via **PowerShell** (MSYS mangles uv's managed-Python link). Full detail: `.claude/rules/windows-dev.md`.

## Current status (as of 2026-06-09)

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
**499 web tests**; subagent-driven TDD (16 tasks, per-task spec→quality review). Still open: the
v1.x drift family (D1–D4 remain); the PDCA dashboard (deferred until acks/objectives land). **S-drift-1** ✅ (pending merge) = D5 **scheduled re-review** — mig `0045` review columns (`review_period_months` int, `next_review_due` date STORED, `last_reviewed_at`), the seeded `periodic_review` workflow, the daily Beat sweep + owner `PERIODIC_REVIEW` tasks, the `review_confirmed` signature + clock reset, the checklist overdue leg; 24 unit + 17 integration tests. **Migration head `0045` (next `0046`) — S-drift-1 added `0045`.** Full per-slice narrative + deferred residuals: `docs/slice-history.md`.
