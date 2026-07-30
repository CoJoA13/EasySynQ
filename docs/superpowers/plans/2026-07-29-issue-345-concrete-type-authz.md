# Issue #345 — Concrete-Type Authorization Implementation Plan

**Goal:** Make the existing DOC_CLASS `concrete_type` selector enforceable on every document gate
by resolving it canonically from `DocumentType.code`.

**Approved architecture:**
`docs/superpowers/specs/2026-07-29-issue-345-concrete-type-authz-design.md`.
The owner approved the exact-code source, canonical paired derivation, no-migration posture, and
mutation-backed verification before implementation.

## Constraints

- No migration; head remains `0083_pack_build_principal`.
- No new permission key, role grant, endpoint, request field, response field, or frontend change.
- `concrete_type` is exact and case-sensitive; display names never authorize.
- Existing DOC_CLASS grants without `concrete_type` remain byte-identical.
- Missing catalog rows fail narrow: neither DOC_CLASS attribute is invented.
- Explicit DENY always wins on every document authorization surface.

## Task 1 — owner decision and source boundary

- [x] Choose `DocumentType.code` as the canonical `concrete_type`.
- [x] Reject a new root column, display-name source, and satellite-derived taxonomy.
- [x] Lock paired `document_level` + `concrete_type` derivation in the canonical helper.
- [x] Lock search projections and pre-create scopes to the same catalog values.

## Task 2 — mutation-distinguishing regression proofs

- [x] Update the pure builder test to use a real detached `DocumentType`.
- [x] Prove concrete-type DENY wins and blanking only that field flips DENY to ALLOW.
- [x] Prove the async builder resolves the seeded catalog code.
- [x] Prove detail/list/search/suggest hide the matching type while preserving another type.
- [x] Prove the pre-create gate rejects a matching concrete-type DENY.

## Task 3 — canonical implementation

- [x] Make `resource_from_doc` derive both DOC_CLASS values from `DocumentType | None`.
- [x] Route every row-backed call site through the paired catalog source.
- [x] Carry the code in search and suggestion candidate projections.
- [x] Populate both pre-create authorization scopes.
- [x] Confirm non-document record fallback scopes remain unchanged.

## Task 4 — authoritative docs and contract

- [x] Add R60 and bump authoritative R-number ranges.
- [x] Align docs 07 and 14 plus the OpenAPI selector description.
- [x] Close #345 in slice history and remove stale deferred wording.
- [x] Record the reusable paired catalog-resolution pattern.
- [x] Add a concise repository orientation/current-status pointer.

## Task 5 — gates and adversarial inspection

- [x] Run focused unit tests and mutation proof.
- [x] Run affected Docker-backed integration tests.
- [x] Run API Ruff, format, mypy, and unit tests.
- [x] Run Redocly contract lint.
- [x] Run `git diff --check` and inspect every document authorization producer.

## Task 6 — publication

- [ ] Commit intentionally and push the issue branch.
- [ ] Open a ready PR closing Issue #345.
- [ ] Monitor all CI checks and fresh Codex review.
- [ ] Respond to and resolve every addressed review thread; do not merge.
