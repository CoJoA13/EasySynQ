# EasySynQ — Project Context

> Orientation for a new session. The **authoritative** detail lives in `docs/` — start with
> `docs/00-overview.md` (front door) and `docs/decisions-register.md` (binding decisions, R1–R62).
> The recurring-patterns catalog + the machine playbook live in `.claude/rules/`; the slice changelog
> + operator/dev reference live in `docs/` (all linked under Deep Dive — read on demand). Keep this
> file lean; new lessons go to **Recent learnings** (below) or `engineering-patterns`, not inline.

## Critical rules — NEVER violate

- **D1 — Self-hosted, single-org.** Org's own server; browser access; data never leaves their infra; admin-controlled backups; no phone-home.
- **D2 — The vault is the source of truth** (PostgreSQL + object storage). Filesystem = a read-only mirror, regenerated from Effective versions only. **⚠ Authority flows vault → mirror, never the reverse.**
- **D3 — ISO 9001:2015 foundation, *architected* (not built) for Part 11 + multi-standard.** Reserved hooks (`signature_event`, `framework_id`, M:N clause mapping) — don't implement in v1, **don't remove**.
- **D4 — Stack is fixed** (see below) — do not substitute components.
- **Deny-by-default; deny-always-wins.** Hybrid RBAC + ABAC; ADMIN sits *outside* the QMS (System Administrator holds **no `document.*`**). System permissions (user/storage/backup/restore/config/import) stay admin-only.
- **⚠ Append-only / WORM invariants are load-bearing** (`audit_event` hash chain, `signature_event`, `capa_stage`/`dcr_stage_event` REVOKE UPDATE,DELETE, MinIO WORM). Any path that deletes object bytes must keep the `blob`-row-iff-bytes invariant — see `engineering-patterns`.
- **⚠ R61 — NEVER commit site-specific operational records.** Real account names, display names, hostnames, FQDNs, IPs/subnets/MACs, customer/org names, security-product or RMM vendors **and versions**, share/bucket names, per-install certificate or key fingerprints, and any "risks accepted" list naming a real site's weaknesses **must not enter this repo** — **regardless of repository visibility** (private repos get shared, forked, cloned and flipped public; visibility is a setting, not a control). A deployment record written honestly *is* a reconnaissance profile. **Sanitize at write time, not after** — removal cannot undo publication (git history, forks, review-tool comments quoting the diff). Commit only the generalized lesson, using placeholders (`<ORG>`, `example.local`, `DC01`, `10.0.0.0/24`, `<edge firewall>`); the concrete worksheet lives in the org's own operational documentation. See `docs/decisions-register.md` **R61**. *This failed silently once — nothing broke, no gate objected, the doc just read as unusually thorough.*
- **Spec/plan before code.** Get approval on a plan before implementing. When a strategic decision is the owner's, **ask** rather than silently pick.

## What this is

EasySynQ is a **self-hosted, browser-based ISO 9001:2015 Quality Management System (QMS)**. It *inverts
authority* so document drift becomes an **enforced invariant**: a managed controlled vault owns the master
copy of every controlled document/record; the on-disk filesystem is only a read-only mirror regenerated
from Effective versions. UI/UX flows the way ISO 9001 flows (clause spine / process map / PDCA) — calm,
modern, progressively disclosed, never overwhelming.

## Repository layout

- `apps/api/` — FastAPI / Python 3.12. Under `src/easysynq_api/`: `api/` (routes) · `services/` (use-cases, txn owners) · `domain/` (pure logic) · `db/models/` (ORM) · `db/seeds/` · `tasks/` (Celery) · `cli/`. Tests in `apps/api/tests/{unit,integration}` (latter via testcontainers).
- `apps/web/` — React/TS + Mantine + Tailwind SPA. It has broad routed coverage of the operational workflow families, but no dedicated Records/Retention or Evidence Pack management route; distinguish an API/worker-complete slice from a standalone browser surface. Stack-free tests: vitest + MSW + jest-axe (`npm test`); under `src/`: `app/shell/` · `features/` · `lib/` · `theme/` · `test/`.
- `migrations/` — Alembic (single tree; current head in **Current status**; `env.py` excludes migration-managed expression/partial indexes).
- `packages/contracts/openapi.yaml` — the living API contract; `scripts/gen-contracts.sh` lints/bundles it and generates the Pydantic server models + TypeScript types. Document new endpoints in-PR.
- `infra/compose/` — Docker Compose (shipped S/M overlays; L is reserved) + Caddy; `just` recipes wrap it. `docs/` — the spec (`00`–`18` + `decisions-register.md`), task-oriented manuals, and operator runbooks. `mockup/easysynq-mockup.html` — owner-approved UI mockup.

## Stack (D4 — fixed)

React/TS + Mantine + Tailwind (SPA) · FastAPI / Python 3.12 · PostgreSQL 16 + MinIO + Redis ·
Celery workers · Keycloak (auth) · Gotenberg/LibreOffice (rendering) · Caddy (TLS) · Docker Compose (single host).
Search currently uses PostgreSQL FTS behind the R34 OpenSearch-ready seam; no shipped S/M overlay deploys OpenSearch.

## Conventions

- **Document lifecycle = 7 canonical states:** `Draft → InReview → Approved → Effective → UnderRevision → Superseded → Obsolete` (the 5-state form is a simplified UI view).
- Permission keys are `domain.action` (catalog in `docs/07`; seed in `docs/14 §3.1`). **Additive-only** (R38) — no rename/removal; a new capability may add keys with a register entry (ask the owner).
- `signature_event.meaning` (v1): `review, approval, release, obsolete, verify, disposition, import_baseline, review_confirmed`; `authored`/`responsibility` reserved for Part-11.
- 8 personas: Avery (Admin), Mara (Quality Manager), Diego (Process Owner), Priya (Author), Ken (Approver), Ingrid (Internal Auditor), Sam (Employee), Olsen (External Auditor).
- **Stakeholder-locked:** import default = current-version-only; revision-chain reconstruction remains an opt-in-per-family design but the current commit path refuses an opted-in family as unsupported; kind is always human-confirmed. Tamper-evidence requires a mandatory off-host / append-only audit-checkpoint anchor.

## Workflow

- `main` has **no enforced protection** (branch protection is unavailable on this free-plan private repo — verified 2026-08-02 via API, `protected: false`; rulesets are paywalled). The five required checks and no-direct-push are **convention/discipline, not enforcement**: slice work on a `feat/sN-*` branch → PR → green CI → squash-merge.
- CI (all five required): `contracts` (redocly), `api` (ruff/mypy-strict/unit), `migrations` (alembic up↔down + `alembic check`), `web` (eslint/tsc/build/test), `integration` (pytest -m integration).
- Toolchain: `uv` + managed **Python 3.12** at `~/.local/bin/uv` (system `python3` is 3.14). Node 22. Docker v29.
- Run the stack: `just up s` → http://localhost; stop `just down`. ⚠ Point the app at the **non-owner** DB role for S6+ — see `docs/dev-workflow.md`.
- Apply recurring patterns by default — see `.claude/rules/engineering-patterns.md` before touching migrations, Celery workers, the workflow engine, or authz.

## Verification (run after changes)

- API: `/check-api` (ruff check + format-check + mypy-strict + pytest unit; `-m integration` needs Docker — available locally on this box via `sg docker -c "…"`, see `docs/dev-workflow.md`).
- Migrations: `/check-migrations` (round-trip alembic up↔down↔`alembic check` on a throwaway PG16).
- Web: `/check-web` (eslint + tsc + build + test).
- Contracts: `/check-contracts` (redocly lint on `packages/contracts/openapi.yaml`).
- Before a PR: run the `diff-critic` agent on the branch diff (see Working preferences).

## Deep Dive — read on demand

- **`docs/decisions-register.md`** — AUTHORITATIVE (R1–R62); supersedes conflicting section text. Read before any design call.
- **`docs/14-data-model.md`** (ERD) — schema source of truth; read before a migration/ORM change.
- **`docs/15-api-design.md`** — endpoints + gates; read before adding/changing an endpoint (update `openapi.yaml` in-PR).
- **`docs/07-authorization-model.md`** — permission catalog, RBAC+ABAC scoping, deny-wins; read before authz work.
- **`docs/03-architecture-and-stack.md`** — vault→mirror authority; read for cross-cutting changes.
- **`docs/18-mvp-implementation-plan.md`** — MVP slice plan + §1 canon corrections (current head in Current status).
- Section docs `00`–`17` + operator runbooks in `docs/runbooks/`. Web-UI design specs/plans in `docs/superpowers/{specs,plans}/`.
- **`.claude/rules/engineering-patterns.md`** — recurring-patterns catalog (migrations · blob/WORM · workers · workflow engine · authz · testing). Read before touching those.
- **`.claude/rules/windows-dev.md`** — this owner's native Windows 11 + Git Bash box (Docker Desktop, localhost-only auth, `just up s`/`demo-user`; no WSL). Read when on this machine.
- **`docs/slice-history.md`** — the shipped-slice changelog (MVP S0–S11 + the v1 families + the web track). **Its FIRST section is `⚠ OPEN RESIDUALS`** — named, owner-acknowledged work that is deliberately deferred (unimplemented R10 reconstruction, the audit checkpoint-lineage gap, the CAPA reject-quorum wedge, …). **Read it before planning new work, and clear an entry only when the work actually ships.**
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
  Brand assets (favicon/mark/logo from the owner's kit, teal/blue — logo colours, NOT UI palette;
  owner ratified keeping the indigo UI) live in `apps/web/public/`; a vitest guards their existence.
- Impeccable live mode is pre-wired (`.impeccable/live/config.json` → `apps/web/index.html`).

## Recent learnings  <!-- ONE line per entry; cap ~8, newest first. Full per-slice narrative → docs/slice-history.md; recurring traps → .claude/rules/engineering-patterns.md. -->

- 2026-08-03 — **S-clause7-ia = clause 7 (Support) is wholly DO (R62; handoff §2B — seed flip + data-only migration `0084` [head `0083→0084`] + rail-dropdown removal + ClauseTree auto-height/hanging-indent + collapse-to-selected + Home chip `Cl 4–6`; NO new key [catalog 102]):** `clause.pdca_phase` is uniform per top-level clause (PLAN 4–6 · DO 7–8 · CHECK 9 · ACT 10). ⚠ **A seed-coupled data migration is CI-blind on fresh chains** — 0018 imports `CLAUSES` live, so a fresh DB seeds the NEW values and 0084's upgrade no-ops; only the populated coherence stanza's head→downgrade→upgrade cycle exercises the flip, and it must assert STRUCTURALLY over all 17 clause-7 rows (a spot set leaves an `_FLIPPED` omission green everywhere while a live install stays PLAN forever). Source the migration's forward value from the seed module (0018/0010 precedent); freeze only the historical downgrade text. ⚠ The flip genuinely breaks split-exercising tests (the mirror cross-phase proofs re-fixtured 7.2+7.5 → 6.2+7.5). ⚠ **A DOM-negative guard test must be settle-aware:** the first no-clause-links rail guard synced on a synchronously-rendered heading while the OLD rail's links mounted only after the async `useClauses()` fetch → passed against reverted code; fix = `waitFor(isFetching()===0)` + `getQueryState(["clauses"])===undefined` (query never REGISTERED = deterministic revert-RED), then mutation-verify against the old component from `main`. Codex P2 (stale `import_proposal_node.target_ia_path` preview mid-upgrade) deferred-on-thread: display-only, zero import runs, self-heals via `rebuild_proposals`, downgrade not invertible. (api unit 1250→1251; web 1427→1431; PR #426 squash `9bef384`.)
- 2026-07-29 — **Issue #345 = the dormant DOC_CLASS `concrete_type` selector is now sourced canonically from exact, case-sensitive `DocumentType.code` (R60; API + OpenAPI/docs + tests; NO migration [head 0083]; NO new key [catalog 102]):** `resource_from_doc` receives the resolved catalog row and derives level + code together; batched Library/register callers retain the row, search/suggest project `dt.code`, and pre-create scopes use the validated row. A matching concrete-type DENY can no longer be silently dropped; mutation proof blanks only the field and flips DENY→ALLOW. (Closes #345; PR #416.)
- 2026-07-29 — **Issue #363 = an async Evidence Pack build is authorized and attributed as the current attempt generator, never the DRAFT creator (R58; API + migration `0083` + integration + docs; NO new permission key [catalog 102]):** `generate` persists caller + accepted source IP atomically with `BUILDING`, Celery carries only `pack_id`, and the locked-row worker evaluates current grants/time with only that IP replayed for `ip_allow`; it rechecks the shared FINDING/CAPA subject-read graph immediately before dossier serialization and uses the initiator for R28 classification, sealed-Record capture, and worker success/failure events. A missing or non-active initiator fails closed. Populated migration coverage proves non-DRAFT compatibility backfill and downgrade/re-upgrade. (Closes #363; PR #414.)
- 2026-07-29 — **Issue #361 = R27 legal erasure now follows every sealed Evidence Pack derivative (API + OpenAPI + migration `0082` + integration + docs; NO new permission key [catalog 102]):** affected member/dossier/pack-record dependencies and exact artifact aliases move to terminal `UNAVAILABLE`, revoke shares, clear ZIP/portfolio pointers, dispose the pack Record through one source-event hop, and use the existing authority-bound purge/reaper path; an org-scoped shared(build)/exclusive(R27) transaction advisory lock closes the last-copy race while ordinary retention remains unchanged. A populated downgrade proved the invalidation CHECK must be dropped before `UNAVAILABLE→FAILED`; empty migration round-trips cannot catch that ordering defect. (Closes #361; PR #413.)
- 2026-07-27 — **Minor Batch M1 = harden four authentication/public-diagnostic boundaries and turn the 104 MINORs into an exact implementation queue (API + OpenAPI + docs; NO migration [head 0075]; NO new key [catalog 102]):** `GET /setup` now needs existing SYSTEM `config.read` while public state/bootstrap keep first-run viable; concurrent first-login JIT inserts converge through PostgreSQL `ON CONFLICT`; JWKS keys rotate under a bounded TTL with single-flight/cooldown and sanitized 503 failures (an unknown-kid outage cannot poison an unexpired known key); and `/readyz` strips raw dependency diagnostics while internal upgrade checks retain them. The new tracker maps all 104 source locations exactly once: 1 preclosed, 4 in M1, 99 queued for revalidation. (API unit 1161→1168; setup integration 24 + auth/JIT selection 5; PR #381.)
- 2026-07-26 — **Batch 17 = reconcile authoritative permission/route prose with shipped behavior (docs + OpenAPI prose only; NO runtime/schema change; NO migration [head 0075]; NO new key [catalog 102]):** docs/07 now includes the already-seeded `document.distribute` definition plus `document`/`retention`/`drift` summary coverage; docs/15 now names the live metadata, evidence-download, NCR-disposition, notification, audit-FSM and audit/finding permission surfaces without promising idempotent replay on the six audit transitions; the `/plan` OpenAPI summary now names its live `audit.conduct` dependency. Every correction was source-traced to a FastAPI dependency or seed migration; executable code and OpenAPI schemas are unchanged. (PR #380.)
- 2026-07-26 — **Batch 16 = close two shard-order false-PASS/leak seams (apps/api integration tests only; NO production change; NO migration [head 0075]; NO new key [catalog 102]):** the cross-org notification-recipient test now deletes its temporary org's user rows and org in `finally`; the cross-org management-review pack test records and restores both the base document and shared-PK satellite org ids before deleting its temporary org. Both assert the test-owned org is absent. This restores the single-org invariant for later `Organization.scalar_one()` consumers regardless of `.test_durations` shard rebalancing. (Batch 16, api unit unchanged at 1161; targeted shared-DB order 3 passed; full touched-file order 11 passed; Ruff/format/mypy 426 + unit green; PR #379.)
- 2026-07-26 — **Batch 15 = web accessibility/polish plus the smallest truthful org-date contract (API contract + apps/api + apps/web; NO migration [head 0075]; NO new key [catalog 102]):** Mantine `Modal`/`Drawer` theme defaults name their close controls while portal axe tests audit `document.body`; the notification popover traps focus and returns it to the bell; `GET /me` exposes the canonical auth-resolved `org_timezone` and one shared formatter replaces UTC truncation across task/effective-date surfaces; working-calendar updates invalidate `/me`; and all five scorecard/commitment backgrounds use `--es-surface-2`. Boundary tests cover Tokyo/Chicago date crossings, keyboard containment/return, named portal controls and dark-safe surfaces. (Batch 15, api unit 1160→1161; web 1355→1363; full API/web/contract gates + 236 web files/1363 tests; PR #378.)
- 2026-07-26 — **Batch 14 = web correctness at four state/contract/error boundaries (apps/web only; NO migration [head 0075]; NO new key [catalog 102]):** `CheckInPanel` is keyed by document id so a cached doc switch cannot check A's file/reason into B; `AuthProvider` consumes `addUserLoaded` and swaps the renewed bearer in place without redirecting/unmounting; ingestion ★ coverage reads the real `rollup`/`projected_rollup` serializer shape; and every review mutation now surfaces the RFC problem detail at the active cockpit/drawer/commit/popover surface. Mutation-distinguishing tests cover the wrong-document scenario, renewal without `signinRedirect`, projected/live coverage, and file/bulk/split/commit/merge failures. (Batch 14, web 1342→1355; full lint/tsc/build + 235 files/1355 tests; PR #377.)
- 2026-07-26 — **Batch 13 = browser-edge + identity-store deploy hardening (infra+scripts+docs; NO migration [head 0075]; NO new key [catalog 102]) — `compose.dev.yml` is now the only MinIO host publish (`127.0.0.1:9000`), while `compose.production.yml` requires one complete HTTPS browser tuple and serves presigned S3 through Caddy on `https://<host>:9443`; installer + appliance first boot/reconfigure propagate app, QR/share, notification, Keycloak and OIDC origins together. Keycloak now runs an optimized 26.7 build against a least-privilege `keycloak` PostgreSQL schema in durable `pgdata`; the import volume is seed-only.** ⚠ **The upgrade seam is load-bearing:** `migrate-keycloak-h2.sh` must run before the first Compose recreation, stop the legacy container, export offline with `--users realm_file`, validate the staged realm, and restart the untouched H2 service on failure. It is wired into installer/`just up`/appliance helper; raw upgrades must invoke it explicitly. Live proofs retained exact user/client/credential IDs, old-password authentication and a redirect edit across recreation and H2→PG migration. (Batch 13, api unit 1150, web 1342, PR #372.)
- 2026-07-22 — **S-register-hardening = Controlled Document Register report.read scope/provenance/tz edge-hardening (closes #335, deferred from #329; BE+web; NO migration [head 0070]; NO new key [catalog 102]) — 4 fixes: (1) `provenance.excluded_processes` [+ a web "Excludes processes:" banner line] records the processes an unconditional PROCESS `report.read` DENY removes, so a SYSTEM-ALLOW+PROCESS-DENY register reads "org-wide EXCEPT B" not org-wide; (2) split `pdp._predicates_pass` → `_context_predicates_pass` (valid_from/until/read_only/ip_allow) so the surface ADMITS a lifecycle-narrowed `report.read` ALLOW [was 403] + narrows per-row — the SYSTEM-DENY revocation stays full-against-`system()` so a resource-scoped DENY is row-scoped, not a surface 403; (3) org-scope `_resolve_process_names` (`Process.org_id==org_id`); (4) `resolve_org_tz` INSIDE the REPEATABLE-READ snapshot, not the pre-txn contextvar.** ⚠ **The admission-correctness class (3 Codex rounds) is CLOSED by one `report_read_resource_satisfiable(grant)`: admit an ALLOW only if its resource predicates can match ≥1 real doc — reject `requirement_source` by PRESENCE (`is not None`, NOT truthiness: a falsy `""` slips a truthiness guard; it's v1-unimplemented like concrete_type/#345), require `lifecycle_state` non-empty ∩ real `DocumentCurrentState`. Those are the ONLY 2 resource predicates → no 3rd to slip.** ⚠ `content_hash` stays row-data-only (the provenance authz fields aren't hashed → no drift). Owner-deferred v1.x (not faked): `process.read`-gating provenance NAMES (D1: honest own-boundary naming beats opaque UUIDs) · a `lifecycle_scope` provenance field. (S-register-hardening, api unit 1090→1092, web 1340→1342 [+2 register-banner tests], PR #347 squash `d3fbb01`.)
- 2026-07-21 — **S-scope-tuple = complete the document ResourceContext scope tuple (closes #333; BE; NO migration [head 0070]; NO new key [catalog 102]) — the canonical `build_document_resource_context` omitted `framework_id`+`kind`, so `pdp._matches_scope` silently DROPPED a FRAMEWORK/kind-scoped DENY (deny-always-wins R3 violated on all document surfaces); #329 had fixed only the register's inline copy. Factored one pure `resource_from_doc(...)` (full tuple) + routed EVERY document-scope builder through it (canonical + register/library filters + objective release + workflow instance/approval read + the task-decision gate + search/suggest + dcr + ack).** ⚠ **Completing the FRAMEWORK selector ACTIVATED a family of latent deny-wins holes — 5 Codex rounds: inline builders → record over-grant [`capa.id→record.id→documented_information.id` kind=RECORD fed a record to the `document.read` gate; guard `doc.kind is DocumentKind.DOCUMENT`] → empty `process_ids` [`frozenset()` dropped a PROCESS DENY while the framework ALLOW matched] → the objective satellite [an objective binds its process on `quality_objective.process_id`, NOT a ProcessLink; made `process_ids_for_docs` union it — `QualityObjective.id==doc.id`] → `list_process_links` holdouts [DCR target + record capture/correction floor]. CONVERGENCE = ONE canonical satellite-aware loader every gate routes through.** ⚠ `quality_objective` is the ONLY shared-PK `documented_information` subtype with a satellite process (risk is a register-HEAD model — `RiskOpportunity.id` is never a doc id → no union); `list_process_links`≡`process_ids_for_docs` for a normal doc (`ProcessLink.process_id` NOT NULL+RESTRICT). `concrete_type` deferred (#345). (S-scope-tuple, api unit 1078→1090, +integration [FRAMEWORK-deny hides detail/list/search; objective-release deny-wins on the satellite process], PR #346 squash `26360bb`.)
## Current status

> **MVP COMPLETE (S0–S11)**; the **ISO 9001:2015 ★ spine and the routed operational web-UI track are substantially delivered**.
> Records/Retention and Evidence Packs are API/worker-complete but have no dedicated SPA management route. All three
> register families — **R49 Risk (6.1) · R50 Context (4.1) · R51 Interested Parties (4.2)** — are done end-to-end
> (core · lifecycle · governing summary + MR consumer · SPA + steward console), and the whole **Notification
> family (S-notify-1 → 5c)** is complete, with **R29 fully closed** (manager-graph · business-day offsets · admin
> editor · due_at-snap, **R55**). The **org timezone is now unified onto ONE canonical DB-resolved source**
> (`org_clock.resolve_org_tz`: cal.tz → org.tz → env → UTC; **R56, S-orgtz-unify**, PR #294 squash `0008b08`) —
> OVERDUE is `now_is_working`-gated and a `next_review_due` backfill CLI lands. The escalation timer-sweep's
> recurring **claim-threshold tautology** (the Codex P2) is now closed — `_due_task_ids` is policy-aware
> (**S-claim-filter**, PR #296 squash `3be2716`; query-only, no migration). A distinct **second (final) reminder**
> now fires 1 business day before due under `task.due_final` (**S-remind2**, PR #298 squash `e5b0d13`; migration
> `0068`). A distinct **second (final) escalation tier** now fires 3 business days after due to the
> **Top Management** role (→ QMS Owner floor) under `task.escalated_final` (**S-escalate2**, PR #300 squash
> `374c9ef`; migration `0069`; closes the named `escalate_2` residual). The **last class-mapped notification
> event** is now wired — an open CAPA past its **severity-defaulted, editable target-completion date** notifies
> the **QMS Owner** role via a daily task-less Beat sweep (`capa.overdue`; + a `capa.update`-gated
> `PATCH /capas/{id}` editor, a server-computed `overdue` serializer field, a backfill CLI, and the CAPA-drawer
> FE) — **S-capa-overdue**, PR #302 squash `bdd4fd5`; migration `0070`. ✅ The **1-click Hyper-V appliance**
> (VHDX + seed ISO + PS installer, boot-proven under QEMU/KVM; fresh installs now get real **S6 DB role
> separation** via the fixed `install.sh`) landed as **S-appliance** — the on-prem install path for the AHT
> dogfood import (PR #313 squash `2f4be28`; no migration). Also merged 2026-07-02: **React 19** (#310) ·
> **ESLint 10** (#311) · **brand assets + nav-rail cleanup** (#312) — all web-only, no migration. Then the
> **systemic-issue backlog** was opened: **#333 → S-scope-tuple** completed the document `ResourceContext`
> scope tuple (framework + kind + a satellite-aware `process_ids_for_docs` unioning `quality_objective.process_id`)
> so a FRAMEWORK/kind/PROCESS-scoped DENY can't be silently dropped (deny-always-wins, R3) — PR #346 squash
> `26360bb`; and **#335 → S-register-hardening** closed the Controlled Document Register's `report.read`
> scope/provenance/tz edges (a new `provenance.excluded_processes` + its web banner line, context-only surface
> admission consolidated in `report_read_resource_satisfiable`, an org-scoped process-name lookup, in-snapshot
> `resolve_org_tz`) — PR #347 squash `d3fbb01`; and **#345 → R60** now resolves the dormant
> DOC_CLASS `concrete_type` from exact `DocumentType.code` across canonical, batched, projected,
> and pre-create document gates. These authorization hardenings require no new permission key;
> #333/#335/#345 require no migration.
> ✅ **S-clause7-ia** — the first product slice after the ops arc (the LAB handoff §2B queue): clause 7
> (Support) is wholly DO (**R62**, migration `0084`), the nav-rail clause dropdowns are gone (every
> exact-match top-level filter returned zero documents), the Library ClauseTree auto-grows wrapped
> rows with a hanging indent + collapses sub-clauses to the selected top-level clause, and the Home
> PLAN chip reads `Cl 4–6` (PR #426 squash `9bef384`). Its sibling **PR 2 — the `?clause=`
> subtree-rollup filter (handoff §2C) — is approved but not started.**
> **Migration head `0084`** (next `0085`) — the 2026-07-22 remediation batches moved it past `0070`
> (`0071` audit-chain cursor · `0072` disposition append-only · `0073` pending-blob-purge · `0074`
> operator alarms · `0075` audit `scope_ref` index · `0076` import-owner snapshot · `0077` sealed-pack
> retention · `0078` record content-hash version · `0079` operational-install coherence · `0080`
> schema/index design · `0081` pending-purge authority binding · `0082` pack legal-erasure lineage ·
> `0083` pack build-attempt principal/context · `0084` clause-7 PDCA flip to DO [S-clause7-ia]).
> Check `cd apps/api && uv run alembic heads`
> before writing a migration — NOT `ls migrations/versions/ | tail -1`, which returns `__pycache__`;
> this line has gone stale before.
>
> The authoritative, per-slice narrative — shipped slices, the migration-by-slice ledger, and every named deferred
> residual — lives in **`docs/slice-history.md`**. Keep this section a short pointer (see Working preferences); land
> new lessons in **Recent learnings** above or `.claude/rules/engineering-patterns.md`, not here.
