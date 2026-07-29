# Issue #361 — Pack Legal-Erasure Implementation Plan

**Goal:** Make an R27 legal-order destruction erase every sealed Evidence Pack copy of the Record,
including the WORM ZIP and derived portfolio, while preserving crash recovery, audit lineage, and
ordinary-retention semantics.

**Approved architecture:** R27-only full invalidation from
`docs/superpowers/specs/2026-07-29-issue-361-pack-legal-erasure-design.md`.
The owner approved this plan before production implementation.

## Constraints

- Verified migration head is `0081_pending_purge_authority`; this slice adds `0082`.
- No new permission key or endpoint.
- OpenAPI changes only for the additive `UNAVAILABLE` pack status and its semantics.
- Preserve two-person R27 authorization and authority-bound marker recovery.
- Preserve purge-after-commit, exact-object locking, and blob-row-iff-bytes.
- Ordinary retention DESTROY must not inherit governance-bypass authority.
- Integration assertions must be run-scoped and deterministic on PostgreSQL 16 + object storage.

## Task 1 — migration and lineage model

- [x] Add `PackStatus.UNAVAILABLE` and migration `0082`.
- [x] Add pack invalidation time/source-event fields and matching ORM constraints.
- [x] Add the `DispositionEvent` source-event self-FK for derivative R27 tombstones.
- [x] Add `PACK_INVALIDATED` to the audit enum and migration.
- [x] Mirror every FK/CHECK name in ORM metadata and preserve app-role privileges.
- [x] Prove up/down/up, populated-data compatibility, and clean `alembic check`.

## Task 2 — pack-build / legal-erasure exclusion

- [x] Add one organization-scoped advisory read/write lock.
- [x] Acquire shared mode before both pack build stages lock or write a pack.
- [x] Acquire exclusive mode before R27 approval locks its request/source Record.
- [x] Pin lock order and transaction lifetime in code comments and engineering patterns.
- [x] Add a deterministic two-session race that fails without either side of the lock.

## Task 3 — complete pack dependency discovery

- [x] Consolidate one dependency resolver for serve checks and invalidation.
- [x] Cover INCLUDED Records, pack Record, dossier subjects/cross-references, and correction ids.
- [x] Re-check dependencies under stable pack-row locks.
- [x] Expand exact artifact-SHA aliases so byte-identical pack copies converge.
- [x] Keep DRAFT/FAILED behavior unchanged and prevent BUILDING from crossing R27.

## Task 4 — transactional invalidation

- [x] In the R27 transaction, mark every affected pack `UNAVAILABLE`.
- [x] Record invalidation source/time and clear ZIP/portfolio pointers.
- [x] Revoke every live share link with actor, reason, and per-link audit event.
- [x] Emit one `PACK_INVALIDATED` audit event per pack.
- [x] Dispose the pack Record with an immutable event derived from the source R27 event.
- [x] Reuse the source event directly when the destroyed Record is the pack Record.

## Task 5 — artifact markers and reaper authority

- [x] Mark ZIP evidence through the shared Record purge path.
- [x] Add portfolio-pointer liveness and marker creation.
- [x] Bind target markers to the pack Record/event plus original executed request.
- [x] Extend reaper validation through the one-level source-event lineage.
- [x] Preserve marker-row → physical-object lock order and exact-object ownership.
- [x] Keep storage failure post-commit and reaper-recoverable.

## Task 6 — routes, contract, and authoritative docs

- [x] Return the truthful terminal status from pack list/detail polling.
- [x] Refuse pack download/share uniformly on `UNAVAILABLE`.
- [x] Prove the generic pack-record evidence route loses its attachment.
- [x] Update OpenAPI `EvidencePack.status` and route prose.
- [x] Amend R27, docs 06/14/15, engineering patterns, remediation tracking, and slice history.

## Task 7 — mutation-distinguishing regression suite

- [x] End-to-end R27 member destroy with ZIP + portfolio + share link.
- [x] Fetch an authenticated and generic presigned URL before destruction; prove both fail after.
- [x] Assert pack status/source, link revocation, pack-record tombstone lineage, pointer/blob
      removal, marker cleanup, and physical-byte absence.
- [x] Storage-failure → durable derived markers → successful reaper completion.
- [x] Forged/mismatched lineage refusal without storage deletion.
- [x] Ordinary retention DESTROY preservation test.
- [x] Dossier dependency, direct pack-record, shared-artifact, and build-race tests.
- [x] Mutation-verify dependency discovery, both advisory-lock sides, lineage validation, and
      liveness guards.

## Task 8 — gates and publication

- [x] Focused unit and integration tests.
- [x] Full touched pack + disposition integration files in shard-safe order.
- [x] Ruff lint/format, mypy, and full API unit suite.
- [x] Migration round trip + populated smoke + `alembic check`.
- [x] Redocly contract lint and response-contract sweep.
- [x] `git diff --check` and adversarial branch-diff review; fold only verified findings.
- [x] Commit intentionally, push, and open a ready (not draft) PR closing Issue #361.
- [ ] Respond to and resolve addressed review threads.
- [ ] Monitor all PR checks and a fresh Codex review through completion; do not merge.
