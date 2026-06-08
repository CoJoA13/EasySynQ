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

- 2026-06-08 — **S-web-5 (Review & Approve) CLOSES UJ-3** — full-stack but THIN: ONE migration-free read (`GET /documents/{id}/approval`, gate `document.read`, **NO new key**) + the front-end; every write + most reads were already contracted. The discovery read returns the **latest** `workflow_instance` (NOT `find_nonterminal_instance` — `release` never closes the instance + `NEEDS_ATTENTION` must surface) or **`null`** (calm; React-Query needs non-`undefined`). **DP-6 gating:** approve/reject = **task candidate-pool membership** (task visibility — there is NO `capabilities.approve`, approve is task-routed) · release = `capabilities.release` (already SoD-2-enriched). **⚠ diff-critic CRITICAL (fixed):** a task-membership check in the SPA must compare **`/me`.id (the `app_user.id`)**, NEVER `user.profile.sub` (the Keycloak subject) — `candidate_pool`/`assignee_user_id` are `app_user.id`s; collapsing the two in a fixture is a false-PASS (new `useMe()` hook).
- 2026-06-08 — **Native-Windows dev gotchas (this box):** the FIRST `uv sync`/`uv run` (which must resolve the managed Python to BUILD the `.venv`) **fails via Git Bash** — MSYS rewrites uv's managed-Python version-link target to `/c/...` → `error: Missing expected target directory for Python minor version link`. **Run that first sync via PowerShell** (`uv run …` synced 122 pkgs + Python 3.12.13 cleanly); **afterwards `uv run` works via Git Bash too** (it uses the existing `.venv` without re-resolving the link), so `just check`/`/pr` are fine once the venv exists. **`-m integration` CANNOT run on native Windows** — psycopg-async rejects the Windows `ProactorEventLoop`, so EVERY integration test fails at the DB connect → integration is a **Linux-CI-only gate** here (`just check` fast-loops is the local gate, by design). mypy also flags `os.O_NOFOLLOW` (Unix-only) in `ingestion/source.py` on Windows — pre-existing, green on Linux.
- 2026-06-08 — **S-web-4b (worker-async visual page-image diff viewer) is FRONT-END ONLY** — no migration/key/contract; the S-dcr-3b backend trio was already built+contracted (`POST/GET …/visual-diff?from=` + `GET …/visual-diff/page/{n}?from=&layer=`, **0-based** page, gated `document.read_draft`). `useVisualDiff` = a **POST-trigger that seeds the poll cache** so the GET poll never races the 404-before-request + `refetchInterval` only while `Pending` (halts at any terminal status); `VisualDiffViewer` = a single pane + Before/After/Diff toggle + a changed-page rail + `n`/`p`, wired as a `?mode=visual` `SegmentedControl` in `VersionCompare` (**RedlineViewer byte-identical**). The page-PNG endpoint is **authed, NOT presigned** → a bare `<img src>` 403s; new `apiGetBlob`/`useApi().getBlob` fetches with the bearer → `objectURL` (revoked on change/unmount — the **only** API-proxied binary in the SPA). `Unavailable` / a page-404 / dev-renderer-off-`Pending` are all **calm**, not errors.
- 2026-06-08 — **S-web-4 (read-only Document detail page + redline) is FRONT-END ONLY** — no migration/key/contract; every read already existed + was contracted (`GET /documents/{id}` `capabilities` · `…/versions` · `…/versions/{vid}/diff?from=` · `…/where-used` · `…/download`). The `/documents/:id` page reuses `ArtifactHeader`/`AuthorActions` verbatim (gated, DP-6); the redline is **synchronous** text+metadata (`useVersionDiff`, `read_draft` 403→quiet, `<ins>`/`<del>` + `+`/`−` non-color markers, `n`/`p` nav), **URL-driven** (`?from=&to=`); the worker-async **visual page-image diff** (POST→poll→PNG layers, already contracted) is carved to **S-web-4b** (PR #93).
- 2026-06-07 — **Web SPA tokens are in-memory only** (`lib/auth`, never persisted) → every reload starts logged-out; an operational, token-less app now auto-bounces to Keycloak to re-auth (PR #91). "All API calls 401 right after a reload" = re-auth in flight or an expired SSO session (sign in again: `demo`/`Demo-Password-1`), NOT a backend bug.
- 2026-06-07 — **Browser upload/download** (authoring presigned PUT, controlled-copy GET) need MinIO browser-reachable: set `S3_PUBLIC_ENDPOINT=http://localhost:9000` in `.env` (the `s` profile publishes `9000`). Presigning SIGNS AGAINST that host (SigV4 signs the host) — never rewrite the URL host post-signing (PR #90).
- 2026-06-07 — **`just seed-personas`** seeds the SoD-correct author/approver/releaser logins+grants (`priya`/`ken`/`mara`, all `Demo-Password-1`) — the S-web-5 fixture. Re-run after `just down` (Keycloak is volumeless). The full create→approve→release loop needs **3 DISTINCT** users (SoD-1/2 are non-overridable).
- 2026-06-07 — Admin ≠ content author: `demo` (System Administrator) holds **no `document.*`**. To author, grant **SYSTEM overrides** of the authoring keys (the integration-test pattern), NOT `grant-role "QMS Owner"` (that role is **reads-only**). No api restart needed (grants resolve per-request). ⚠ This install's org short_code is **`AHT`** → `grant-role` needs `--org AHT`.
- 2026-06-07 — **CI runs from source, not the built image** → a new CLI module or a `storage.py`/Dockerfile change needs `docker compose … build api` (or `up -d --build`) before the running container picks it up. Green CI ≠ deployed.
- 2026-06-07 — `GET /documents` returns a **`{data, page}` envelope** (S-web-2), not a bare array — read `.data`. `GET /documents/{id}` carries a per-doc **`capabilities`** block (S-web-3, detail-only) for DP-6 button gating.
- 2026-06-07 — SoD is enforced at the PEP, not the vault services — a single actor can drive the lifecycle at the **service layer** (server-side demo seeds; no token needed).
- 2026-06-07 — Local `-m integration` failures in `test_backup`/`test_restore` are an env gap (no `pg_dump`), **not** regressions; CI passes them. A `testcontainers-ryuk … 404 containers/create` failure is a runner flake → re-run the shard.

## Current status (as of 2026-06-08)

**MVP COMPLETE** (S0–S11). **v1 in progress** — families ✅: Records & evidence · Ingestion · Audits/Findings/CAPA ·
Revision & change depth (DCR). **Web-UI track:** S-web-1 ✅, S-web-2 ✅ (faceted Library + read-only drawer), S-web-3
✅ = Document Authoring (+ follow-ups #89 `seed-personas` · #90 browser-reachable presigned MinIO · #91 reload re-auth),
**S-web-4** ✅ (read-only Document detail page + the text/metadata redline; #93), **S-web-4b** ✅ (the worker-async
**visual page-image diff viewer**; #95 + native-Windows fix #96). **S-web-5 = Review & Approve — CLOSES UJ-3**
(author→review→approve→release) **implemented — PR open**: full-stack but THIN — ONE migration-free read
(`GET /documents/{id}/approval`, gate `document.read`, **no migration/key**) + the front-end (`/tasks` inbox · per-task
Review & Approve page [redline + decision card] · the doc-page **Approvals stepper** · **Release**). 135 web tests +
5 api integration; **diff-critic 1 CRITICAL fixed** (gate task membership on `/me`.id, not `profile.sub`). Also open:
the v1.x drift family (D1–D5). **Migration head `0044` (next `0045`).** Full per-slice narrative + deferred residuals:
`docs/slice-history.md`.
