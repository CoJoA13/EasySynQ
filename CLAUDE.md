# EasySynQ — Project Context

> Orientation for a new session. The **authoritative** detail lives in `docs/` — start with
> `docs/00-overview.md` (front door) and `docs/decisions-register.md` (binding decisions, R1–R46).
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
- `apps/web/` — React/TS + Mantine + Tailwind SPA. The web-UI track is feature-complete (~962 tests; per-slice list in **Current status** + `docs/slice-history.md`). Stack-free tests: vitest + MSW + jest-axe (`npm test`); under `src/`: `app/shell/` · `features/` · `lib/` · `theme/` · `test/`.
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

- **`docs/decisions-register.md`** — AUTHORITATIVE (R1–R46); supersedes conflicting section text. Read before any design call.
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

## Design Context  <!-- pointer for the impeccable design skill; full strategic doc in PRODUCT.md -->

- **Register: `product`** (design serves the QMS task — app-shell, registers, drawers, command palette;
  *not* a marketing surface). Strategic source of truth: **`PRODUCT.md`** (repo root).
- **Personality:** calm, precise, trustworthy — audit-grade seriousness without enterprise heaviness.
- **5 principles:** (1) IA flows the way ISO 9001 flows (clause spine / process map / PDCA); (2) calm
  under compliance — restraint is the default, density is earned; (3) legibility is the feature — the
  employee and the external auditor read the same surface; (4) progressive disclosure, one task per
  screen, inline before modal; (5) status you can't misread — lifecycle + RAG carried by shape/icon/
  label, never colour alone.
- **Anti-references:** legacy enterprise QMS (SAP/SharePoint/Documentum) · playful consumer SaaS ·
  generic Bootstrap admin. **A11y bar:** WCAG 2.2 AA + colour-safe RAG + reduced-motion.
- **Design system:** `apps/web/src/theme/tokens.css` (one token source for Mantine + Tailwind; calm
  indigo `#4f5bd5`, layered surfaces, PDCA hues, light+dark, system-font stack — air-gap-safe).
- Impeccable live mode is pre-wired (`.impeccable/live/config.json` → `apps/web/index.html`).

## Recent learnings  <!-- ONE line per entry; cap ~8, newest first. Full per-slice narrative → docs/slice-history.md; recurring traps → .claude/rules/engineering-patterns.md. -->

- 2026-06-17 — Spawning an own-table workflow object (the R46 initiative) from an origin is a **1:N + optional-Idempotency-Key recording act**: compose `create_X(_commit=False)` in ONE txn, run the idempotent replay BEFORE the mutable-state gate, mint no signature (R43), `IntegrityError`→rollback+re-query. ⚠ **The replay MUST re-authorize the RETURNED resource's STORED scope** (gate-1 authorizes the request/create scope; gate-2 re-`enforce`s `initiative.process_id` when `not created`) — else a process-B caller reads a process-A row via a known key; **reject a superseded origin** (`Record.superseded_by_correction`) AFTER the replay; **document an unlocked mutable-state read as benign** ONLY when it matches the sibling precedent (the close gate never reads spawned objects). **Codex found all 3 of these P2s a CLEAN diff-critic missed** (S-improvement-2, zero-migration).
- 2026-06-17 — A pre-render scanner over UNTRUSTED Office formats is an inherently-imperfect heuristic: **pure/fail-open/bounded/ReDoS-safe/XXE-closed** (refuse `<!DOCTYPE`), with **false-positive→safe (R26 source-only)** the ONLY acceptable error direction; ⚠ **Codex caught ~17 real edge-cases across 6 review rounds that CLEAN diff-critic passes missed** (the adversarial PR reviewer — not the diff-critic — is the edge-finder on adversarial input) and it does **NOT converge** — past ~round 4 it re-raises owner-decided tradeoffs (fail-open vs fail-closed), so TRIAGE (fix-high-value/defer-rest) + ask the owner to merge, don't loop; key shapes: **parse-and-concat split `<w:instrText>` runs**, **INDEPENDENT per-category zip budgets** (rels-first under a SHARED budget starves the other), **scan BOTH ODF+OOXML when both fingerprints present** (S-render-1, gotenberg 8.34, no migration).
- 2026-06-15 — A clause-10.3 **NON-★** entity (no ★ checklist node to flip) is an **own-table mutable-state workflow object** (the DCR/R22 doctrine), NOT a shared-PK kind=DOCUMENT subtype (that path is OBJ/MR, justified ONLY by a ★ node) and NOT a kind=RECORD → its append-only stage-event FKs MUST be **name-mirrored in the ORM** (bare → `Base.metadata` derives 63+-char names PG truncates → `alembic check` phantom-DROP) and reads gate **PROCESS-scoped via the full `ResourceContext.process_ids`** (the R28 row-filter), never SYSTEM-only (S-improvement-1).
- 2026-06-15 — A per-feature-dir subagent fan-out CANNOT see CROSS-SURFACE consistency: parallel agents mapped the same severity ("Major") to warning in capa but danger in audits → the orchestrator MUST reconcile to ONE app-wide convention (faithful-to-old-hue is the tiebreaker absent an owner design-call); also **StatusBadge couples visible-label + aria from ONE `label`** → keep the primary value in the label + the secondary signal in a sibling caption (never a contradictory `✓ Ambiguous` pill) (S-statusbadge-2).
- 2026-06-15 — react-router `setSearchParams` is **NOT** referentially stable — key a URL-write effect on the resolved `debounced` value (+ `ref` the setter), never the raw `?q` param, or an external param change re-fires the stale write (S-optimize-1).
- 2026-06-15 — A "frozen verdict" is only as frozen as the columns you actually froze: a value-vs-target RAG snapshot **re-grades** if `direction`/`threshold` stay live → treat per-reading RAG as **descriptive**, not immutable; and **never round a displayed reading** (`toFixed` can contradict its own RAG point) (S-obj-charts).
- 2026-06-14 — A boolean default-off query filter MUST emit on `!== undefined`, never `if (filters.x)` (else `false` is silently dropped); exclude a shared-PK managed subtype (OBJ/MR) via `NOT EXISTS`, never a doc-type-code exclude (S-doc-filters).
- 2026-06-14 — A derived compliance PDF renders **only** frozen/immutable facts (snapshot title not live `doc.title`, no mutable `close_state`, no email PII → `display_name`→id); ⚠ an MSW handler for a binary endpoint must return a **`Uint8Array`** not a `Blob` (CI node lacks `Blob.stream`) (S-mr-pack).
## Current status

> **MVP COMPLETE** (S0–S11). The **ISO 9001:2015 ★ spine is feature-complete** — every ★ family shipped end-to-end: Records & evidence · Ingestion · Audits/Findings/CAPA · Revision/change-depth (**DCR** — read + write + diff + annotate + CREATE-implement; no residuals) · Drift **D1–D5** · Acknowledgements · Quality Objectives (lifecycle/revision + KPI trend chart) · Management Review (backend + UI + outputs→action-systems + filed-minutes pack) — and the **web-UI track is feature-complete for them** (~962 web tests). **PARTIAL (the one in-flight family):** the **non-★ Improvement Initiatives** (clause 10.3, R46) has shipped its **backend core** (S-improvement-1) + the **OFI-finding / MR-output spawn endpoints** (**S-improvement-2**, ✅ [#182](https://github.com/CoJoA13/EasySynQ/pull/182) — `POST /findings/{id}/raise-initiative` + `POST /management-reviews/{id}/outputs/{oid}/raise-initiative`, zero-migration) — still to come: the register/drawer **SPA** (**S-improvement-3**) and the opt-in verified-benefit stage (**S-improvement-4**); it has **no SPA surface yet**. **Render hardening (S-render-1, ✅ [#180](https://github.com/CoJoA13/EasySynQ/pull/180)):** a pre-render scanner marks externally-linked Office/RTF/ODF sources **non-renderable (R26, source-only)** so gotenberg **8.34** (now adopted) can't cache an INCOMPLETE controlled copy — backend-only, **no migration**; bounded-safe v1.x residuals named in slice-history. **Migration head `0052` (next `0053`).** Per-slice changelog + named deferred residuals: `docs/slice-history.md`.
