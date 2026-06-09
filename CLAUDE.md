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
- `apps/web/` — React/TS + Mantine + Tailwind SPA. Shipped: first-run wizard, admin stubs, **S-web-1** (shell + token port), **S-web-2** (faceted Library + read-only detail drawer), **S-web-3** (Document Authoring), **S-web-4** (Document detail page + text/metadata redline). Feature UI ongoing (next: S-web-4b visual page-image diff, then S-web-5 Review & Approve). Stack-free tests: vitest + MSW + jest-axe (`npm test`); under `src/`: `app/shell/` · `features/` · `lib/` · `theme/` · `test/`.
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

- 2026-06-09 — **S-web-7c (Complaint & NCR intake) is FRONT-END ONLY** (no migration [head stays `0044`]/key/contract) — surfaces the already-built `/complaints*` + `/ncrs*` surface (`api/capa.py:493-617`) as **tabbed sub-routes under `/capa`**: a thin **`CapaLayout`** (Mantine `Tabs` Board·Complaints·NCRs + `<Outlet/>`, active from `useLocation().pathname`) wraps three child routes — `/capa` index keeps **`CapaBoardPage` byte-identical** (its own title; the layout adds NO title), `/capa/complaints`, `/capa/ncrs`. **Per-key gating DIVERGES from the board:** complaints ride `record.read`/`record.create`/spawn=`capa.create`; NCRs ride `ncr.read`/`ncr.create`/`ncr.record_correction` — the **`demo` admin holds NONE** (both tabs calm-403), and **`ncr.create`+`ncr.record_correction` are seeded but granted to NO role** (0004) → **SYSTEM-override-only in v1** (gate each affordance on its OWN key at SYSTEM scope; never assume the admin sees it). **Spawn-CAPA idempotency is surfaced as STATE, not the HTTP status:** `api.send` discards the status (201-new vs 200-replay), so the `_complaint` row's **`spawned_capa_id`** drives the "Spawn CAPA"→"View CAPA" flip (invalidate `["complaints"]`+`["capas"]`); a racing replay just resolves. **Disposition is one-shot:** a disposed NCR row is **read-only with NO action button** (structural in the UI) + `409 ncr_already_dispositioned` is calm. Spawn inherits the complaint's severity (no process picker — the 7b deferred Raise-scope decision); filters/tiles deferred (YAGNI). Fixtures pinned to the real `_complaint`/`_ncr` serializers (NOT the mockup). Live smoke: grant `demo` SYSTEM overrides of `record.read record.create capa.create ncr.read ncr.create ncr.record_correction` (org **AHT**) — one admin drives the loop (no SoD). **+30 web → 425 web tests** green.
- 2026-06-09 — **S-web-7b (CAPA lifecycle writes) CLOSES the ACT-phase write loop** (raise→containment→root-cause→action-plan[approved]→implement→verify[SIGNED]→close + the Verify→RootCause loop + the M4 evidence close gate). **NOT pure front-end** (the epic said so, but the approval loop needs it): a **thin read-enrichment** mirroring 7a — `subject_type`/`subject_id` on the `_task` **detail** serializer, a new **`GET /capas/{id}/approval`** (gated `capa.read`, the `/documents/{id}/approval` mirror, returns `{instance,proposed_action_plan}`|null), and `evidence_links` per `_stage` (no migration, no new key). ⚠ The seeded **Top-Management** approver of a Critical CAPA holds **only `capa.read`** (0038), so the document-subject `GET /workflow-instances/{id}` (`document.read`-gated) **403s them** → the whole CAPA approval path must avoid `document.read`: route via `task.subject_type`+the capa.read approval read, never the instance read or `GET /documents/{capa_id}`. The `/tasks` `ReviewApprovePage` was **document-only** (blindly `GET /documents/{capa_id}`→404) → branch on `subject_type`; `DecisionCard` generalized to `{subjectType,subjectId}` (the decision POST already dispatches CAPA→`decide_capa_action_plan` server-side); keep the DOCUMENT pane `decidable && docId`-gated (byte-identical). **Close gate = `close_capa` EXACTLY**: root_cause cycle-AGNOSTIC; implemented-action + effectiveness CURRENT-cycle **with ≥1 linked evidence** → `deriveGate(stages, cycleMarker)` evidence-aware (the 7a stage-presence guess is gone); `CloseAction` does NOT gate on it (server-authoritative; the stepper shows it). Evidence = link an EXISTING record (`POST /records/{id}/evidence-links target_type=capa_stage`, no upload); render the linker **only on the CURRENT cycle's** Implement/Verify (a looped CAPA's prior-cycle linkers both label "Record (Verify)" → the S-web-6 duplicate-getByLabelText trap). SoD-4 (`409 sod_self_verify`)+M4 (`409 capa_close_incomplete`) are server-only→calm; `review_output` source is reserved (422)→omit from Raise. Added a global jsdom `Element.scrollIntoView` stub (Mantine Combobox left open throws on its scroll timer otherwise).
- 2026-06-08 — **S-ing-4b (Ingestion Review UI) CLOSES UJ-2 — FRONT-END ONLY** (no migration/key/contract; surfaces the S-ing-1..5 `/admin/imports/*` surface). **⚠ `run.counts` is a FLAT top-level-merged bag** (`by_band.HIGH`, top-level `quarantine`, `proposal`, `commit`) — there is **NO `classify`/`queues`/`review` namespace; the folded review stats (`undecided`/`kind_confirmed`/`keep_items`) live on the **checklist** endpoint, not the run (a fabricated MSW fixture hid this → tiles/tabs/pager read **0** in prod; diff-critic CRITICAL — pin web fixtures to a REAL backend response). **The `demo` admin holds all three import keys** (`import.execute/review/commit` ∈ `_SYSTEM_KEYS` → the System-Administrator bundle, `0004`), so it drives the whole loop with no personas — UNLIKE the compliance checklist. **Scale needs NO virtualization** — server `offset`/`limit` pagination bounds the DOM to one page; whole-bucket bulk actions use the server `selector` (never load all rows). **Commit gate** = checklist `blocking[]` (`ready && commit_ready≥1 && can(import.commit)`); **unconfirmed-kind is ADVISORY, not a hard block** (commit the ready subset; the button reads "Commit N confirmed"). **R10 kind-confirm = a separate human act** (Bulk-accept-High must NOT set `after.kind`). **Merge/split server-authoritative** (submit→invalidate, never optimistic reshape; the row list carries no cluster/family membership → join the `/dupe-clusters`+`/version-families` lists client-side). The `/files` endpoint filters only `band/kind/disposition/review_status`.
- 2026-06-08 — **S-web-6 (Global Search + Compliance Checklist) is FRONT-END ONLY** — no migration/key/contract; surfaces the **S10** backend (`GET /search` · `/search/suggest` · `/reports/compliance-checklist`, all already contracted, PR #38). **Search filters-not-403** (200 + `hidden_by_scope`, never 403); **Compliance is HARD-gated** `report.compliance_checklist.read` (SYSTEM; QMS-Owner + Internal-Auditor only → **403 for the demo admin** — grant a SYSTEM override or use a persona for the smoke; the hook surfaces a `forbidden` flag → a calm no-access panel). The **⌘K palette is hand-rolled** (Mantine `Modal`, **NO `@mantine/spotlight` dep**); ⌘K needs **two** `useHotkeys` — `mod+K` with **empty `tagsToIgnore`** (so it fires even inside an input) + `/` with the default ignore-list (so it won't hijack typing). The `ts_headline` `snippet` carries literal `<b>…</b>` → **render via a split-into-`<Mark>` parser, NEVER `dangerouslySetInnerHTML`** (a `<script>` in a title then renders as literal text). A **duplicate `aria-label`** across a summary badge + a table-row badge breaks `getByLabelText` (single-match) → the rollup uses a plain glyph+label legend, `CoverageBadge` only in the rows. ⚠ `tsc --noEmit` (strict `noUncheckedIndexedAccess`) catches array-index nits the per-file `vitest` run does NOT — run the full `/check-web` (lint+tsc+build+test) before the PR.
- 2026-06-08 — **S-web-5 (Review & Approve) CLOSES UJ-3** — full-stack but THIN: ONE migration-free read (`GET /documents/{id}/approval`, gate `document.read`, **NO new key**) + the front-end; every write + most reads were already contracted. The discovery read returns the **latest** `workflow_instance` (NOT `find_nonterminal_instance` — `release` never closes the instance + `NEEDS_ATTENTION` must surface) or **`null`** (calm; React-Query needs non-`undefined`). **DP-6 gating:** approve/reject = **task candidate-pool membership** (task visibility — there is NO `capabilities.approve`, approve is task-routed) · release = `capabilities.release` (already SoD-2-enriched). **⚠ diff-critic CRITICAL (fixed):** a task-membership check in the SPA must compare **`/me`.id (the `app_user.id`)**, NEVER `user.profile.sub` (the Keycloak subject) — `candidate_pool`/`assignee_user_id` are `app_user.id`s; collapsing the two in a fixture is a false-PASS (new `useMe()` hook).
- 2026-06-08 — **Native-Windows dev gotchas (this box):** the FIRST `uv sync`/`uv run` (which must resolve the managed Python to BUILD the `.venv`) **fails via Git Bash** — MSYS rewrites uv's managed-Python version-link target to `/c/...` → `error: Missing expected target directory for Python minor version link`. **Run that first sync via PowerShell** (`uv run …` synced 122 pkgs + Python 3.12.13 cleanly); **afterwards `uv run` works via Git Bash too** (it uses the existing `.venv` without re-resolving the link), so `just check`/`/pr` are fine once the venv exists. **`-m integration` CANNOT run on native Windows** — psycopg-async rejects the Windows `ProactorEventLoop`, so EVERY integration test fails at the DB connect → integration is a **Linux-CI-only gate** here. The **api `-m unit` suite also crashes** on this box — a native access-violation (`<no Python frame>`) at `test_ingestion_helpers.py::test_sniff_mime` (a libmagic-style MIME sniff), Windows-only, pre-existing (every test before it passes). So BOTH api test gates are Linux-CI-only here; the reliable local gates are **web (`npm`)** + the **api static checks** (ruff/format/mypy). mypy also flags `os.O_NOFOLLOW` (Unix-only) in `ingestion/source.py` on Windows — pre-existing, green on Linux.
- 2026-06-08 — **S-web-4b (worker-async visual page-image diff viewer) is FRONT-END ONLY** — no migration/key/contract; the S-dcr-3b backend trio was already built+contracted (`POST/GET …/visual-diff?from=` + `GET …/visual-diff/page/{n}?from=&layer=`, **0-based** page, gated `document.read_draft`). `useVisualDiff` = a **POST-trigger that seeds the poll cache** so the GET poll never races the 404-before-request + `refetchInterval` only while `Pending` (halts at any terminal status); `VisualDiffViewer` = a single pane + Before/After/Diff toggle + a changed-page rail + `n`/`p`, wired as a `?mode=visual` `SegmentedControl` in `VersionCompare` (**RedlineViewer byte-identical**). The page-PNG endpoint is **authed, NOT presigned** → a bare `<img src>` 403s; new `apiGetBlob`/`useApi().getBlob` fetches with the bearer → `objectURL` (revoked on change/unmount — the **only** API-proxied binary in the SPA). `Unavailable` / a page-404 / dev-renderer-off-`Pending` are all **calm**, not errors.
- 2026-06-08 — **S-web-4 (read-only Document detail page + redline) is FRONT-END ONLY** — no migration/key/contract; every read already existed + was contracted (`GET /documents/{id}` `capabilities` · `…/versions` · `…/versions/{vid}/diff?from=` · `…/where-used` · `…/download`). The `/documents/:id` page reuses `ArtifactHeader`/`AuthorActions` verbatim (gated, DP-6); the redline is **synchronous** text+metadata (`useVersionDiff`, `read_draft` 403→quiet, `<ins>`/`<del>` + `+`/`−` non-color markers, `n`/`p` nav), **URL-driven** (`?from=&to=`); the worker-async **visual page-image diff** (POST→poll→PNG layers, already contracted) is carved to **S-web-4b** (PR #93).
- 2026-06-07 — **Web SPA tokens are in-memory only** (`lib/auth`, never persisted) → every reload starts logged-out; an operational, token-less app now auto-bounces to Keycloak to re-auth (PR #91). "All API calls 401 right after a reload" = re-auth in flight or an expired SSO session (sign in again: `demo`/`Demo-Password-1`), NOT a backend bug.
- 2026-06-07 — **Browser upload/download** (authoring presigned PUT, controlled-copy GET) need MinIO browser-reachable: set `S3_PUBLIC_ENDPOINT=http://localhost:9000` in `.env` (the `s` profile publishes `9000`). Presigning SIGNS AGAINST that host (SigV4 signs the host) — never rewrite the URL host post-signing (PR #90).
- 2026-06-07 — **`just seed-personas`** seeds the SoD-correct author/approver/releaser logins+grants (`priya`/`ken`/`mara`, all `Demo-Password-1`) — the S-web-5 fixture. Re-run after `just down` (Keycloak is volumeless). The full create→approve→release loop needs **3 DISTINCT** users (SoD-1/2 are non-overridable).
- 2026-06-07 — Admin ≠ content author: `demo` (System Administrator) holds **no `document.*`**. To author, grant **SYSTEM overrides** of the authoring keys (the integration-test pattern), NOT `grant-role "QMS Owner"` (that role is **reads-only**). No api restart needed (grants resolve per-request). ⚠ This install's org short_code is **`AHT`** → `grant-role` needs `--org AHT`.
- 2026-06-07 — **CI runs from source, not the built image** → a new CLI module or a `storage.py`/Dockerfile change needs `docker compose … build api` (or `up -d --build`) before the running container picks it up. Green CI ≠ deployed.

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
— no migration/key); **S-web-7c** ✅ (this PR) = Complaint & **NCR intake** — front-end-only **tabbed sub-routes under
`/capa`** (a thin `CapaLayout` Board·Complaints·NCRs over the byte-identical board) = complaints list/log/**idempotent
spawn-CAPA** + NCRs list/raise/**one-shot ISO 8.7 disposition**, per-key calm-403 gating (the demo admin holds none;
`ncr.create`/`ncr.record_correction` are SYSTEM-override-only in v1). **425 web tests**; subagent-driven TDD (8 tasks,
per-task spec→quality review). Still open: **S-web-7d** (audits/findings) of the epic; the
v1.x drift family (D1–D5); the PDCA dashboard (deferred until acks/objectives land). **Migration head `0044` (next `0045`)
— 7b + 7c added no migration.** Full per-slice narrative + deferred residuals: `docs/slice-history.md`.
