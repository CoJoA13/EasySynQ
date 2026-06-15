# S-doc-filters — build plan

> Spec: `docs/superpowers/specs/2026-06-14-s-doc-filters-create-picker-design.md` (owner-approved).
> Branch: `feat/s-doc-filters-create-picker`. Head stays `0051`; no migration/key/enum.

## Task decomposition (file-disjoint → parallel-safe)

### T1 — Backend filter grammar (`apps/api`)
- `api/documents.py`: add `("has_effective_version","eq")` + `("managed_subtype","eq")` to `_FILTER_ALLOW`;
  add `_parse_filter_bool(field, value)` (422 `validation_error` on non-`"true"/"false"`); add the two
  `_filter_condition` branches (col `is_(None)`/`is_not(None)`; `~or_(EXISTS quality_objective, EXISTS
  management_review)`). Imports `or_`, `QualityObjective`, `ManagementReview` **added with their use in
  the same edit** (formatter-strip trap).
- Tests:
  - `tests/unit/` (native): non-boolean value → 422 for each new field; `_parse_document_filters`
    accepts both new pairs and still 400s an unknown `filter[...]`.
  - `tests/integration/test_documents_list.py` (CI-only here): the four-doc narrowing matrix
    (Effective-initial, Approved-new, Approved-revision-of-Effective, OBJ) — run-scoped/delta assertions.
- Agent verifies natively: `uv run ruff check`, `uv run ruff format --check`, `uv run mypy`, and the
  **targeted** new unit test (NOT the full suite — it crashes on this box; integration is CI-only).

### T2 — Contract (`packages/contracts/openapi.yaml`)
- Add `filter[has_effective_version][eq]` + `filter[managed_subtype][eq]` query params under
  `GET /documents`, mirroring the existing `filter[...]` entries (string enum `["true","false"]`,
  optional, short description). Agent verifies via redocly lint.

### T3 — Frontend (`apps/web`)
- `lib/types.ts`: `DocumentFilters += has_effective_version?: boolean; managed_subtype?: boolean;`.
- `features/library/useDocuments.ts`: emit each in `buildDocumentsQuery` **only when `!== undefined`**
  (false-emit trap), `String(...)`-serialized.
- `features/dcr/ImplementCreateDcrModal.tsx`: pass `{has_effective_version:false, managed_subtype:false}`
  to `useDocuments`; delete the `useDocumentTypes` import + `managedTypeIds` memo + the client `.filter`
  (`options` → plain `.map`). Submit path unchanged.
- Tests: `buildDocumentsQuery` (emit incl. `false` / omit when undefined); `ImplementCreateDcrModal`
  renders server-filtered candidates with no client filtering (MSW fixture pinned via `satisfies
  Document`; `import { expect, it } from "vitest"`).
- Agent verifies: `npx eslint`, `npx tsc --noEmit`, targeted `npx vitest run --pool=forks
  --poolOptions.forks.singleFork=true <files>`.

## Execution
1. Branch `feat/s-doc-filters-create-picker`.
2. Workflow: parallel implement (T1/T2/T3) → per-task quality review (spec + traps) → I fold confirmed fixes.
3. Full gates (I run): `/check-api`, `/check-contracts`, `/check-web` (no `/check-migrations`).
4. Adversarial: `diff-critic` (branch diff) + `web-test-trap-reviewer` (FE diff).
5. Live smoke: backend filters via worker heredoc (four doc shapes); Library LIST = no-regression check.
6. PR → green CI → Codex triage → merge on owner OK → `/finish-slice` → finish-slice docs PR.
