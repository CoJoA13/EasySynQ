# EasySynQ — Project Context

> Orientation for a new session. The **authoritative** detail lives in `docs/` — start with
> `docs/00-overview.md` (front door) and `docs/decisions-register.md` (binding decisions, R1–R40).
> Operating detail, the slice changelog, and the recurring-patterns catalog live in `.claude/rules/`
> (read on demand — see Deep Dive). Keep this file lean; new lessons go to `.claude/rules/`, not here.

## Critical rules — NEVER violate

- **D1 — Self-hosted, single-org.** On the org's own server; browser access; data never leaves their infra; admin-controlled backups; no phone-home.
- **D2 — The vault is the source of truth** (PostgreSQL + object storage). Filesystem = a read-only mirror, regenerated from Released versions only. **Authority flows vault → mirror, never the reverse.**
- **D3 — ISO 9001:2015 foundation, *architected* (not built) for Part 11 + multi-standard.** Reserved hooks (`signature_event`, `framework_id`, M:N clause mapping) — do not implement in v1, **do not remove**.
- **D4 — Stack is fixed** (see below) — do not substitute components.
- **Deny-by-default; deny-always-wins.** Hybrid RBAC + ABAC; ADMIN sits *outside* the QMS. System permissions (user/storage/backup/restore/config/import) stay admin-only.
- **Append-only / WORM invariants are load-bearing** (`audit_event` hash chain, `signature_event`, `capa_stage`/`dcr_stage_event` REVOKE UPDATE,DELETE, MinIO WORM). Any path that deletes object bytes must keep the `blob`-row-iff-bytes invariant — see `.claude/rules/engineering-patterns.md`.
- **Spec/plan before code.** Get approval on a plan before implementing. When a strategic decision is the owner's, **ask** rather than silently pick.

## What this is

EasySynQ is a **self-hosted, browser-based ISO 9001:2015 Quality Management System (QMS)**. It *inverts
authority* so document drift becomes an **enforced invariant**: a managed controlled vault owns the master
copy of every controlled document/record, and the on-disk filesystem is only a read-only mirror regenerated
from Released versions. The UI/UX flows the way ISO 9001 flows (clause spine / process map / PDCA) and must
stay calm, modern, progressively disclosed — never overwhelming.

## Repository layout

- `apps/api/` — FastAPI / Python 3.12. Under `src/easysynq_api/`: `api/` (routes) · `services/` (use-cases, txn owners) · `domain/` (pure logic) · `db/models/` (ORM) · `db/seeds/` · `tasks/` (Celery) · `cli/`. Tests in `apps/api/tests/{unit,integration}` (latter via testcontainers).
- `apps/web/` — React/TS + Mantine + Tailwind SPA. Shipped: the first-run setup wizard, admin stubs, and **S-web-1** (the app shell + design-system token port + a thin Library — the start of the web-UI track). The rest of the feature UI is in progress (S-web-2/3+). Stack-free tests: vitest + MSW + jest-axe (`npm test`); structure under `src/`: `app/shell/` · `features/` · `lib/` · `theme/` · `test/`.
- `migrations/` — Alembic (single tree; current head is in **Current status** below; `env.py` excludes migration-managed expression/partial indexes).
- `packages/contracts/openapi.yaml` — the living API contract (redocly-lint only; **not** codegen). Document new endpoints in-PR.
- `infra/compose/` — Docker Compose (S/M/L profiles) + Caddy; `just` recipes wrap it. `docs/` — the spec (`00`–`18` + `decisions-register.md`) + `runbooks/`. `mockup/easysynq-mockup.html` — owner-approved UI mockup.

## Stack (D4 — fixed)

React/TS + Mantine + Tailwind (SPA) · FastAPI / Python 3.12 · PostgreSQL 16 + MinIO + OpenSearch + Redis ·
Celery workers · Keycloak (auth) · Gotenberg/LibreOffice (rendering) · Caddy (TLS) · Docker Compose (single host).

## Conventions

- **Document lifecycle = 7 canonical states:** `Draft → InReview → Approved → Effective → UnderRevision → Superseded → Obsolete` (the 5-state form is a simplified UI view).
- Permission keys are `domain.action` (catalog in `docs/07`; seed in `docs/14 §3.1`). The catalog is **additive-only** (R38) — no rename/removal; a new capability may add keys with a register entry (ask the owner).
- `signature_event.meaning` (v1): `review, approval, release, obsolete, verify, disposition, import_baseline, review_confirmed`; `authored`/`responsibility` reserved for Part-11.
- 8 personas: Avery (Admin), Mara (Quality Manager), Diego (Process Owner), Priya (Author), Ken (Approver), Ingrid (Internal Auditor), Sam (Employee), Olsen (External Auditor).
- **Stakeholder-locked:** import default = current-version-only (revision-chain reconstruction opt-in per family; kind is always human-confirmed); tamper-evidence requires a mandatory off-host / append-only audit-checkpoint anchor.

## Workflow

- `main` is protected. Slice work on a `feat/sN-*` branch → PR → green CI → squash-merge.
- CI (all five required): `contracts` (redocly), `api` (ruff/mypy-strict/unit), `migrations` (alembic up↔down + `alembic check`), `web` (eslint/tsc/build), `integration` (pytest -m integration).
- Toolchain: `uv` + managed **Python 3.12** at `~/.local/bin/uv` (system `python3` is 3.14). Node 22. Docker v29.
- Run the stack: `just up s` → http://localhost; stop `just down`. ⚠ Point the app at the **non-owner** DB role (`.env` role separation) for S6+ — see `.claude/rules/dev-workflow.md`.
- Apply recurring patterns by default — see `.claude/rules/engineering-patterns.md` before touching migrations, Celery workers, the workflow engine, or authz.

## Verification (run after changes)

- API: `cd apps/api && uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest` (unit always; `-m integration` needs Docker). Or `/check-api`.
- Migrations: round-trip alembic up↔down↔`alembic check` on a throwaway PG16. Or `/check-migrations`.
- Web: `cd apps/web && npm run lint && npm run typecheck && npm run build`. Or `/check-web`.

## Deep Dive — read on demand

- **`docs/decisions-register.md`** — AUTHORITATIVE (R1–R40); supersedes any conflicting section text. Read before any design call or when reconciling a conflict.
- **`docs/14-data-model.md`** (ERD) — schema source of truth; read before a migration or ORM change.
- **`docs/15-api-design.md`** — endpoints + gates; read before adding/changing an endpoint (and update `openapi.yaml` in-PR).
- **`docs/07-authorization-model.md`** — 96-key catalog, RBAC+ABAC scoping, deny-wins; read before authz work.
- **`docs/03-architecture-and-stack.md`** — vault→mirror authority; read for cross-cutting changes.
- **`docs/18-mvp-implementation-plan.md`** — slice plan + §1 canon corrections; read when planning a slice.
- Section docs `00`–`17` cover vision/domain/doc-control/revision/records/setup/ingestion/workflows/UI/security/search/roadmap/gaps. Operator runbooks in `docs/runbooks/`.
- **`.claude/rules/engineering-patterns.md`** — the recurring-patterns catalog (migrations · blob/WORM · workers · workflow engine · authz · testing).
- **`.claude/rules/dev-workflow.md`** — operator/`.env` detail + the per-feature API quick-reference.
- **`.claude/rules/slice-history.md`** — the shipped-slice changelog (MVP S0–S11 + the v1 families).
- **`.claude/rules/windows-wsl-dev.md`** — running EasySynQ on this owner's Windows 11 + WSL2 box: localhost-only auth (PKCE secure-context), `OIDC_DISCOVERY_URL`, ephemeral Keycloak (`just demo-user`), and driving WSL from a Windows-native Claude session. Read when on this machine.

## Current status (as of 2026-06-07)

**MVP COMPLETE** (S0–S11). **v1 in progress.** Families shipped: **Records & evidence** (S-rec-1..4 + Evidence
Packs) ✅ · **Ingestion** (S-ing-1..5) ✅ · **Audits/Findings/CAPA** (S-aud-1/2 + S-wf-engine + S-capa-1/2/3 +
S-aud-capa-pack) ✅ · **Revision & change depth (DCR family, doc 05, R40)** ✅. The DCR family: S-dcr-1 (core +
intake, mig `0040`), S-dcr-2 (where-used/impact + assess, `0041`), S-dcr-3a (metadata + text redline diff,
zero-migration), S-dcr-3b (worker-async visual page-image diff via pypdfium2+Pillow, `0042`), S-dcr-4 (DCR routing +
approval via the declarative engine — `dcr_approval` workflow [ROUTER on significance], per-approver signatures, `0043`),
**S-dcr-5 (implement/close + the shared-path §7.3 obsoletion gate + the CAPA→DCR loop + the deferred cross-FK, `0044`) —
CLOSES the DCR family.** DCR-as-orchestrator: `POST /dcrs/{id}/implement` drives the vault action atomically (REVISE/CREATE
schedule the `release_due` cutover; RETIRE obsoletes) + enforces the underlying `document.release`/`document.obsolete`
(SoD-2, no side-door); the §7.3 gate now fires on the SHARED `document.obsolete` too (`force_retire`+justification);
`POST /capas/{id}/raise-dcr` (1:N, Idempotency-Key).
**v1 web-UI track in progress** (`apps/web`, React/Mantine SPA). **S-web-1** ✅ (app shell + design-system token port +
thin Library). **S-web-2** ✅ — faceted Library + a read-only tabbed detail drawer (Overview/History/Where-used);
**full-stack but NO migration**: minimal authenticated-only read endpoints (`GET /document-types`, `GET /directory/users`
[display-name only]) + a `{data,page}` **authz-correct** pagination envelope + an `effective_from` date filter/column on
`GET /documents`. The 3-slice plan was **split**, so: **Next:** S-web-3 (standalone Document detail page) · S-web-4
(Review & Approve — closes UJ-3). Also open: the v1.x drift family (scheduled re-review D5 + drift detection D1–D4).
**Migration head `0044` (next `0045`).** Full narrative + deferred v1/v1.x residuals: **`.claude/rules/slice-history.md`**.

## Working preferences

- The owner used `/effort ultracode` (multi-agent Workflow orchestration) for the heavy spec/mockup work; `/effort` is per-session — re-enable it to use that approach again.
- `.claude/agents/diff-critic.md` — a read-only adversarial reviewer pre-loaded with the load-bearing invariants (WORM/append-only · mirror cache · alembic-check traps · run-scoped integration assertions · deny-wins authz). Run it on the branch diff before each PR (`Agent` tool, `subagent_type: diff-critic`).
- Persistent memory: `~/.claude/projects/<project-key>/memory/` (MEMORY.md index) — the key is **derived from the project's absolute path**, so it differs per machine/OS (e.g. `-var-home-cojoa13-Documents-EasySynQ` on this bazzite box; `-home-…` on the prior Ubuntu box; a `C--Users-…`-style key on Windows). The `easysynq-project.md` memory owns the running per-slice log; keep this file's Current-status to a short pointer.
- View the mockup: `xdg-open mockup/easysynq-mockup.html` (this laptop has no headless browser, so no auto PNG screenshots).
