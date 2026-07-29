# Issue #363 — Pack Generator Context Implementation Plan

**Goal:** Make every Evidence Pack worker authorization and attribution use the user who initiated
the current build attempt, including a request-IP snapshot for `ip_allow`, and fail the seal if
Finding/CAPA subject access is revoked before dossier construction.

**Approved architecture:**
`docs/superpowers/specs/2026-07-29-issue-363-pack-generator-context-design.md`.
The owner approved this plan before production implementation.

## Constraints

- Verified migration head is `0082_pack_legal_erasure`; this slice adds `0083`.
- The locked pack row is authoritative; Celery continues to receive only `pack_id`.
- Current grants/time are evaluated at the worker; only the generate request source IP is replayed.
- Preserve deny-always-wins, authorization auditing, R28 exclusion honesty, acks-late idempotency,
  the R27 build lock, and pack retention/share behavior.
- No new permission key, endpoint, response field, or frontend change.
- Integration assertions must be run-scoped on PostgreSQL 16 + object storage.

## Task 1 — migration and attempt-context model

- [x] Add `0083` with nullable `build_requested_by` and `build_source_ip`.
- [x] Backfill existing non-DRAFT rows to `created_by`.
- [x] Add the named RESTRICT FK and matching ORM metadata.
- [x] Prove upgrade, populated backfill, downgrade/re-upgrade, app-role access, and clean
      `alembic check`.

## Task 2 — shared authorization seam

- [x] Factor the PEP's audited evaluation so a worker can supply an explicit `RequestContext`
      without fabricating a FastAPI `Request`.
- [x] Consolidate Finding/CAPA dossier permission/resource questions into one resolver used by
      create, generate, and build.
- [x] Preserve request-path 403 behavior and allow/deny audit hooks.

## Task 3 — regression proofs

- [x] Prove a retrying generator cannot borrow the original creator's `record.read`.
- [x] Prove a grant revoked after generate but before dossier construction fails the build.
- [x] Prove matching `ip_allow` works across request → persisted context → worker.
- [x] Prove changed DENY/expiry still blocks under the current worker clock/grants.
- [x] Prove sealed Record and pack audit attribution use the initiating generator.
- [x] Pin task arguments to `pack_id` only and the row-authoritative retry behavior.

## Task 4 — worker and request implementation

- [x] Persist caller/IP atomically with the `BUILDING` transition.
- [x] Thread request IP through preview `record.read` classification.
- [x] Load and validate the initiating generator under the worker's pack row lock.
- [x] Classify evidence with that generator and persisted source IP.
- [x] Re-authorize subjects immediately before `build_dossier`; audit and fail on denial.
- [x] Attribute capture/generation/worker-failure events to the initiating generator.

## Task 5 — authoritative docs

- [x] Add R58 and update docs 06/14/15.
- [x] Add the persisted async-principal engineering pattern.
- [x] Close Issue #363 in slice history and update remediation tracking.
- [x] Keep migration-head pointers accurate.

## Task 6 — gates and publication

- [x] Focused unit/integration regression tests.
- [x] Full affected pack integration suites.
- [x] Ruff lint/format, mypy, and full API unit suite.
- [x] Migration round trip + populated coherence + `alembic check`.
- [x] Redocly contract lint and response-contract checks are not applicable: no OpenAPI contract
      changed.
- [x] `git diff --check` and adversarial branch-diff review.
- [ ] Commit intentionally, push, and open a ready (not draft) PR closing Issue #363.
- [ ] Respond to and resolve addressed review threads.
- [ ] Monitor all PR checks and a fresh Codex review through completion; do not merge.
