# Issue #406 — Version-History Authorization Implementation Plan

**Goal:** Make every immutable version-history and diff surface enforce the owner-approved
state-to-key matrix while retaining `document.read` as the mandatory live-Document boundary.

**Approved architecture:**
`docs/superpowers/specs/2026-07-29-issue-406-version-history-authorization-design.md`.
The owner approved the matrix, authorized-subset list posture, and both-sides diff rule before
publication.

## Constraints

- No migration; head remains `0083_pack_build_principal`.
- No new key, role grant, endpoint, request field, response field, or frontend behavior branch.
- R57 metadata surfaces remain byte-identical.
- Specialized checks must retain the canonical Document scope and use each immutable version's
  state plus the live request clock/source IP.
- Explicit DENY always wins; detail/download/diffs retain audited 403 behavior.
- Every text/visual diff surface authorizes both sides before content/cache access.

## Task 1 — owner decision and regression boundary

- [x] Lock Effective → base-only, Draft/InReview/Approved → `document.read_draft`, and
      Superseded/Obsolete → `document.read_obsolete`.
- [x] Lock `document.read` as the non-substitutable base requirement.
- [x] Lock collection behavior to an authorized subset.
- [x] Lock mixed-state diffs to the union of both sides' required keys.
- [x] Demonstrate the pre-fix failure: a base reader receives 403 from the all-`read_draft`
      collection dependency.

## Task 2 — mutation-distinguishing integration proofs

- [x] Seed all six immutable version states over one real Document/blob.
- [x] Prove list, detail, and download for base, draft, obsolete, and full readers.
- [x] Prove specialized-only readers cannot substitute for `document.read`.
- [x] Prove ARTIFACT scope, version lifecycle, matching `ip_allow`, and an explicit scoped DENY.
- [x] Prove mixed text/visual diffs enforce both sides and preserve Pending/404 behavior after
      authorization.

## Task 3 — shared authorization implementation

- [x] Add one state-to-key selector and version-resource projection.
- [x] Filter list rows with current grants/request context.
- [x] Route detail/download through the audited single-version enforcement path.
- [x] Route text diff plus visual request/poll/page through the same two-version path.
- [x] Preserve missing/cross-document and visual availability behavior.
- [x] Run the focused three-test Docker-backed matrix.

## Task 4 — authoritative docs and contract

- [x] Add R59 and align docs 07/15.
- [x] Update every affected OpenAPI operation summary without changing schemas.
- [x] Close the #406 residual in slice history; confirm the remediation tracker has no stale entry.
- [x] Record the canonical version-resource projection pattern.
- [x] Reconcile stale frontend implementation comments that claim every version read needs
      `document.read_draft`.

## Task 5 — gates and adversarial review

- [x] Run affected version/diff/vault/metadata integration suites.
- [x] Run API Ruff, format, mypy, and unit tests.
- [x] Run Redocly contract lint and contract-response coverage.
- [x] Run the relevant web static/test gate for comment-only frontend alignment.
- [x] Run `git diff --check` and inspect the complete branch diff against each acceptance criterion.

## Task 6 — publication

- [x] Commit intentionally and push the issue branch.
- [x] Open a ready PR closing Issue #406.
- [ ] Monitor all CI checks and fresh Codex review.
- [ ] Respond to and resolve every addressed review thread; do not merge.
