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
- `apps/web/` — React/TS + Mantine + Tailwind SPA. The web-UI track is feature-complete (test count + per-slice list in **Current status** + `docs/slice-history.md`). Stack-free tests: vitest + MSW + jest-axe (`npm test`); under `src/`: `app/shell/` · `features/` · `lib/` · `theme/` · `test/`.
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

- 2026-06-17 — DOCUMENT-BACKED leadership routing (POL §5.2 / OBJ §6.2 / MR §9.3 → Top-Management) is the **additive engine-routed sibling** of S-improvement-4 that the welded single-stage `document_approval` can't do: gate **RELEASE** (not approval) behind an org flag (`leadership_release_requires_top_management_authorization`, default OFF) enforced at the **shared `vault/lifecycle._cutover` chokepoint** (covers direct + OBJ + MR + DCR-implement); the Top-Mgmt sign-off mints `meaning=verify` on the **existing `document_version`** (NOT an own-table stage-event → a SINGLE signature INSERT, **no two-INSERT seam**) + a new `WorkflowSubjectType.LEADERSHIP_AUTHORIZATION`/`EventType.LEADERSHIP_AUTHORIZED` (additive; `verify`+`document_version` reused — R2); authority = candidate-pool membership (**NO new permission key**); welded path byte-identical; migration 0054 = enum ADD VALUEs (`autocommit_block`) + a `system_config` column + a seed reusing the 0038 Top-Mgmt role. ⚠ **Codex found 5 P2/P3 over 2 rounds a CLEAN diff-critic+migration-reviewer missed:** (a) the single-active guard only blocks NON-terminal instances → a 2nd request after a COMPLETED authz (pre-release) mints a DUPLICATE `verify` sig → the `scalar_one_or_none()` replay 500s → guard the request with `has_release_authorization` (409 `already_authorized`; version-scoped, so a NEW Approved version re-requests); (b) **DCR-implement go-live runs the cutover in the async `release_due` sweep which SWALLOWS the gate's 409** → implement commits as Implemented but the version stays Approved (false success) → extract a shared `assert_release_authorized` + **preflight it synchronously in `implement_dcr`**; (c) a new `WorkflowSubjectType` must be added to the openapi `WorkflowInstance.subject_type` enum, the decision result must use the contracted `document_version_id` (not `version_id`) under `additionalProperties:false`, and a decisive reject must set `stage_state=FAILED`. ⚠ **POL is `is_singleton=True` (R25)** → a shared-DB test must NOT release two Effective POLs; put the full release happy-path on NON-singleton **OBJ**. (S-leadership-1, migration 0054, PR #190.)
- 2026-06-17 — A clause-5/6/9/10 leadership artifact's SIGNED engine-routed approval = the CAPA `decide_capa_action_plan` template ported to an **own-table subject**: lock instance → `engine.decide(_commit=False)` → on COMPLETE mint the `signature_event` via the **pre-gen-UUID two-INSERT seam** (never an UPDATE on the REVOKE-immutable stage-event table) → flip the FSM; authority is **candidate-pool membership (NO new permission key)**; `meaning=verify` reuses the closed R2 set; add a `WorkflowSubjectType` + `signed_object_type` (and the FSM is the only real migration). ⚠ **Codex found 9 P2/P3s a CLEAN diff-critic + migration-reviewer + web-test-trap ALL missed:** (a) the generic engine **ANY quorum treats EVERY positive `TaskOutcomeKind`** (approve/complete/acknowledge/verify) as completing → **allow-list the subject's real outcomes** (else `approve` mints a `verify` sig + closes), and place that check **AFTER the authority gate** (a pre-auth 422 leaks the task's existence vs the 404-collapse); (b) an **ANY-quorum decline is NOT decisive** (fails only on all-reject) → force `REJECTED` + skip siblings in the caller (the `decide_dcr_approval` precedent), else a multi-member pool leaves the cycle stuck/non-re-requestable; (c) **guard the PARALLEL unsigned close** (`/transition`) while a non-terminal authz instance exists — the FE suppressing it is not enough; (d) a migration that **USES a just-added enum value in its OWN seed** needs the `autocommit_block` ADD-VALUE to commit first (the first such case). ⚠ shared-DB ANY-quorum tests can't assume a single-member pool (≥2 Top-Mgmt members in CI); the ruff hook strips a just-added import (re-add AFTER the usage). (S-improvement-4, migration 0053, Improvement family COMPLETE.)
- 2026-06-17 — A cross-surface "raise X from an origin" FE affordance reuses the sibling spawn-modal (a bound mutation + a **per-mount `Idempotency-Key`**, `onSuccess`-invalidate — a 200-replay IS success, no FSM race) and gates at the **ORIGIN's** scope (finding → auditee-process `usePermissions(scope)`, the FE mirror of the backend `_finding_scope`; MR-output → SYSTEM-default, IDENTICAL to its Raise-CAPA/DCR siblings). ⚠ **TWO Codex P2s a CLEAN diff-critic + web-test-trap both missed:** (1) a dashboard count over a **filter-not-403** list endpoint (auth-only, empty-200 for a no-grant caller) MUST NOT fold that read's `forbidden` into the tile's `allForbidden` — it's ~never true, so a no-access user sees a stray "0 X" instead of TileNoAccess; keep the informational line **purely additive** (never in `allForbidden`/the RAG fold); (2) **a coverage-gap test written without re-deriving the CORRECT behaviour can CODIFY the bug** — my own "shows the line when all actionable reads forbidden" test (added to close a diff-critic-named gap) entrenched exactly the defect; the reviewer that NAMES a gap ≠ the one that says its RIGHT answer. (S-improvement-3b, FE-only, web 984→1009.)
- 2026-06-17 — The FIRST SPA surface for an own-table workflow object (the R46 initiative) mirrors the **DCR feature-dir** but with TWO carries: (1) its stage-event timeline is a **SEPARATE `GET …/{id}/stage-events`** fetch, NOT embedded in the detail like `DcrDetail`; (2) the serializer has **NO `capabilities` block** → a **per-resource** write affordance gates on **`usePermissions({level:'PROCESS', id: resource.process_id})`** scoped to the row (the CAPA `AdvancePanel` pattern, SYSTEM when unscoped), while **register-level nav/create buttons stay SYSTEM-gated** (the objectives/capa precedent — `usePermissions` can't ask "any-process"). ⚠ **Codex flagged the unscoped SYSTEM `can()` cockpit gate as a P2** (a PROCESS-scoped manager couldn't drive the FSM) that a CLEAN diff-critic + web-test-trap both missed — the per-resource-vs-register-level scoping split is the reconciliation. Also: a **comment-required terminal transition** needs the disable-on-empty the DCR cancel modal lacks; a `?id=`-seeded drawer's `if(id)` open-guard is **load-bearing** (search/filter share `searchParams` — a naive close-on-absent shuts a locally-opened drawer). (S-improvement-3a, FE-only, web 962→984.)
- 2026-06-17 — Spawning an own-table workflow object (the R46 initiative) from an origin is a **1:N + optional-Idempotency-Key recording act**: compose `create_X(_commit=False)` in ONE txn, run the idempotent replay BEFORE the mutable-state gate, mint no signature (R43), `IntegrityError`→rollback+re-query. ⚠ **The replay MUST re-authorize the RETURNED resource's STORED scope** (gate-1 authorizes the request/create scope; gate-2 re-`enforce`s `initiative.process_id` when `not created`) — else a process-B caller reads a process-A row via a known key; **reject a superseded origin** (`Record.superseded_by_correction`) AFTER the replay; **document an unlocked mutable-state read as benign** ONLY when it matches the sibling precedent (the close gate never reads spawned objects). **Codex found all 3 of these P2s a CLEAN diff-critic missed** (S-improvement-2, zero-migration).
- 2026-06-17 — A pre-render scanner over UNTRUSTED Office formats is an inherently-imperfect heuristic: **pure/fail-open/bounded/ReDoS-safe/XXE-closed** (refuse `<!DOCTYPE`), with **false-positive→safe (R26 source-only)** the ONLY acceptable error direction; ⚠ **Codex caught ~17 real edge-cases across 6 review rounds that CLEAN diff-critic passes missed** (the adversarial PR reviewer — not the diff-critic — is the edge-finder on adversarial input) and it does **NOT converge** — past ~round 4 it re-raises owner-decided tradeoffs (fail-open vs fail-closed), so TRIAGE (fix-high-value/defer-rest) + ask the owner to merge, don't loop; key shapes: **parse-and-concat split `<w:instrText>` runs**, **INDEPENDENT per-category zip budgets** (rels-first under a SHARED budget starves the other), **scan BOTH ODF+OOXML when both fingerprints present** (S-render-1, gotenberg 8.34, no migration).
- 2026-06-15 — A clause-10.3 **NON-★** entity (no ★ checklist node to flip) is an **own-table mutable-state workflow object** (the DCR/R22 doctrine), NOT a shared-PK kind=DOCUMENT subtype (that path is OBJ/MR, justified ONLY by a ★ node) and NOT a kind=RECORD → its append-only stage-event FKs MUST be **name-mirrored in the ORM** (bare → `Base.metadata` derives 63+-char names PG truncates → `alembic check` phantom-DROP) and reads gate **PROCESS-scoped via the full `ResourceContext.process_ids`** (the R28 row-filter), never SYSTEM-only (S-improvement-1).
- 2026-06-15 — A per-feature-dir subagent fan-out CANNOT see CROSS-SURFACE consistency: parallel agents mapped the same severity ("Major") to warning in capa but danger in audits → the orchestrator MUST reconcile to ONE app-wide convention (faithful-to-old-hue is the tiebreaker absent an owner design-call); also **StatusBadge couples visible-label + aria from ONE `label`** → keep the primary value in the label + the secondary signal in a sibling caption (never a contradictory `✓ Ambiguous` pill) (S-statusbadge-2).
## Current status

> **MVP COMPLETE** (S0–S11). The **ISO 9001:2015 ★ spine is feature-complete** — every ★ family shipped end-to-end: Records & evidence · Ingestion · Audits/Findings/CAPA · Revision/change-depth (**DCR** — read + write + diff + annotate + CREATE-implement; no residuals) · Drift **D1–D5** · Acknowledgements · Quality Objectives (lifecycle/revision + KPI trend chart) · Management Review (backend + UI + outputs→action-systems + filed-minutes pack) — and the **web-UI track is feature-complete for them** (~1040 web tests). **The non-★ Improvement Initiatives family (clause 10.3, R46) is now COMPLETE end-to-end:** **backend core** (S-improvement-1) + the **OFI-finding / MR-output spawn endpoints** (**S-improvement-2**, ✅ [#182](https://github.com/CoJoA13/EasySynQ/pull/182), zero-migration) + its **SPA** (register + `?initiative=` drawer + FSM cockpit + manual-create — **S-improvement-3a**, ✅ [#184](https://github.com/CoJoA13/EasySynQ/pull/184)) + the cross-surface **raise affordances + Home ACT-card line** (**S-improvement-3b**, ✅ [#186](https://github.com/CoJoA13/EasySynQ/pull/186)) + the opt-in **signed, engine-routed Top-Management authorization** (**S-improvement-4**, ✅ [#188](https://github.com/CoJoA13/EasySynQ/pull/188), migration **0053**) — **no S-improvement residuals remain.** **Render hardening (S-render-1, ✅ [#180](https://github.com/CoJoA13/EasySynQ/pull/180)):** a pre-render scanner marks externally-linked Office/RTF/ODF sources **non-renderable (R26, source-only)** so gotenberg **8.34** (now adopted) can't cache an INCOMPLETE controlled copy — backend-only, **no migration**; bounded-safe v1.x residuals named in slice-history. **Document-backed leadership routing (S-leadership-1, ✅ [#190](https://github.com/CoJoA13/EasySynQ/pull/190), migration `0054`):** an opt-in, additive, engine-routed Top-Management **RELEASE** authorization for the clause-5/6/9 leadership artifacts (POL §5.2 / OBJ §6.2 / MR §9.3) — `meaning=verify` on the `document_version`, gated by an org config flag (default OFF) at the shared `vault/lifecycle._cutover` chokepoint + preflighted on DCR-implement; authority = Top-Mgmt candidate-pool membership (**NO new permission key**); the welded `document_approval` path byte-identical — closing the named-enhancement gap S-improvement-4 deferred. The request/decide **FE shipped** as the S-leadership-1 front-end (✅ [#192](https://github.com/CoJoA13/EasySynQ/pull/192)): a `/tasks` verify/reject arm + a self-suppressing `LeadershipReleaseGate` on POL/OBJ/MR. **Migration head `0054` (next `0055`).** Per-slice changelog + named deferred residuals: `docs/slice-history.md`.
