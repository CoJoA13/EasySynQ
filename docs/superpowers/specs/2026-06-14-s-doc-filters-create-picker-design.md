# S-doc-filters — server-side candidate filters on `GET /documents` (CREATE-picker)

> **Status:** DRAFT — awaiting owner approval.
> **Slice id:** `s-doc-filters` · **Branch:** `feat/s-doc-filters-create-picker`
> **Clause/context:** §10→§7.5 (DCR change-control); a v1.x UX-polish residual carried from S-dcr-ui-2a/ui-4.
> **Migration head:** stays `0051` (no migration). **Permission catalog:** stays 100 (no key). **No enum.**

## 1. Why

The DCR **CREATE-implement** picker (`ImplementCreateDcrModal`, ui-4) lists `current_state=Approved`
documents and then **client-side filters** out (a) docs that already have an effective version
(approved *revisions* of existing docs) and (b) managed subtypes (OBJ/MR). Those filtered-out rows
still consume slots in the server's 100-row page (`limit` is clamped `min(max(limit,1),100)` at
`api/documents.py:615`), so the handful of valid "new, never-released" candidates can be **hidden even
below 100 total** when recent Approved docs are dominated by revisions-in-progress. The fix pushes the
two filters **server-side** so every returned row is a valid candidate and the narrowed set sits well
under the clamp.

**This is UX only — not a correctness fix.** `services/dcr/service.py::_resolve_implement_version`
already rejects an invalid CREATE target server-side (`create_target_not_new` when
`current_effective_version_id is not None`; `create_target_managed_subtype` when a `QualityObjective`
/`ManagementReview` row exists), independent of how the client chose it. This slice only stops invalid
candidates from *appearing*; it cannot let an invalid implement through.

## 2. Decisions locked (owner)

- **F1 — Scope: CREATE picker only.** Add the two narrowing filters; rewire `ImplementCreateDcrModal`;
  drop its now-redundant client filters. The **ui-2a target picker** (`DcrRaiseFields`, `Effective`
  docs) is **out of scope** — narrowing does not help it (it *wants* Effective docs; its only limit is
  >100 Effective docs, whose honest fix is server-side typeahead/search — a different, larger
  mechanism). Deferred as a named follow-up (§7).
- **F2 — Managed-subtype exclusion via `NOT EXISTS`** against `quality_objective` + `management_review`
  (correct for shared-PK subtypes; immune to document-type-code renames). NOT the resolve-OBJ/MR-
  `document_type_id` approach (couples to seed codes).
- **F3 — Bracketed grammar, default-off.** Extend the existing `filter[field][op]` allow-list so the
  uniform `unknown_filter` 400 contract holds; keep both filters **omitted-when-unset** in
  `buildDocumentsQuery` so the other `GET /documents` consumers (the Library page + the ui-2a DCR
  target picker `DcrRaiseFields`) are byte-unaffected. (Global search is NOT a consumer — it uses
  OpenSearch `/search`.)

## 3. Scope

**In:** two opt-in server-side filters on `GET /documents`; OpenAPI doc; FE wiring of the CREATE
picker + `DocumentFilters` type + `buildDocumentsQuery`; tests.

**Out / non-goals:** the target-picker typeahead (F1, deferred); true offset/cursor pagination or
infinite-scroll in the Select; any new text-search facet; touching `_LIST_SCAN_CAP`/the clamp; the
Library page UI; any migration/permission key/enum.

## 4. Backend — `apps/api/src/easysynq_api/api/documents.py`

Two new allow-listed `(field, op)` pairs, both `op="eq"`, boolean-valued ("true"/"false"):

| filter | semantics |
|---|---|
| `filter[has_effective_version][eq]=false` | `documented_information.current_effective_version_id IS NULL` (never-released). `=true` → `IS NOT NULL` (symmetry). |
| `filter[managed_subtype][eq]=false` | exclude docs with a `quality_objective` **or** `management_review` row. `=true` → only those (symmetry). |

Changes:
1. Extend `_FILTER_ALLOW` (`documents.py:498`) with `("has_effective_version","eq")` and
   `("managed_subtype","eq")`.
2. Add a small `_parse_filter_bool(field, value) -> bool` helper: `"true"→True`, `"false"→False`,
   else `ProblemException(status=422, code="validation_error", title=f"Invalid {field} filter value")`
   — mirroring the existing enum/UUID 422 branches.
3. Add two branches in `_filter_condition` (`documents.py:521`):
   ```python
   if field == "has_effective_version":
       flag = _parse_filter_bool(field, value)
       col = DocumentedInformation.current_effective_version_id
       return col.is_not(None) if flag else col.is_(None)
   if field == "managed_subtype":
       flag = _parse_filter_bool(field, value)
       is_managed = or_(
           select(1).where(QualityObjective.id == DocumentedInformation.id).exists(),
           select(1).where(ManagementReview.id == DocumentedInformation.id).exists(),
       )
       return is_managed if flag else ~is_managed
   ```
4. Imports: add `or_` (if not already imported from sqlalchemy) and `QualityObjective`,
   `ManagementReview` from `..db.models`. **⚠ Formatter trap:** add each import **with its use in the
   same edit**, or the format hook strips it (bit pack.py/repository.py this project repeatedly).

No change to `list_documents`, `_parse_document_filters` (it already routes any allow-listed pair
through `_filter_condition`), the clamp, or `_LIST_SCAN_CAP`.

## 5. Contract — `packages/contracts/openapi.yaml`

Under `GET /documents`, add two query params mirroring the existing `filter[...][...]` entries
(`filter[has_effective_version][eq]`, `filter[managed_subtype][eq]`; string enum `["true","false"]`,
not required, with a short description noting they narrow the candidate set). redocly-lint only.

## 6. Frontend

1. **`lib/types.ts` `DocumentFilters`** — add `has_effective_version?: boolean;` and
   `managed_subtype?: boolean;`.
2. **`features/library/useDocuments.ts` `buildDocumentsQuery`** — emit each **only when defined**, with
   the boolean serialized explicitly:
   ```ts
   if (filters.has_effective_version !== undefined)
     p.set("filter[has_effective_version][eq]", String(filters.has_effective_version));
   if (filters.managed_subtype !== undefined)
     p.set("filter[managed_subtype][eq]", String(filters.managed_subtype));
   ```
   ⚠ **The false-emit trap:** use `!== undefined`, never `if (filters.x)` — `false` is falsy and would
   be silently dropped. LibraryPage never sets these → undefined → not emitted → unchanged.
3. **`features/dcr/ImplementCreateDcrModal.tsx`** — pass the filters to `useDocuments`:
   ```ts
   useDocuments(
     { current_state: "Approved", has_effective_version: false, managed_subtype: false },
     { limit: 100, offset: 0 },
   );
   ```
   Then **delete the client-side narrowing**: drop the `useDocumentTypes` import + `managedTypeIds`
   memo + the `.filter(...)` (the `current_effective_version_id===null` + OBJ/MR exclusion + the
   redundant `kind==="DOCUMENT"` — `GET /documents` already returns DOCUMENT-only). `options` becomes a
   plain `.map`. The `_resolve_implement_version` guard remains the submit-time backstop; the existing
   submit-and-show 403/409 handling is unchanged.

## 7. Deferred (named, not faked)

- **Target-picker (ui-2a) large-list typeahead** — `DcrRaiseFields` over `Effective` docs; needs
  server-side text search (a new length-capped ILIKE facet on `/documents`, or OpenSearch `/search`
  reuse). Out of scope per F1; the 100-cap only bites at >100 Effective docs and client-side search
  within the loaded page still works.
- **True paging/infinite-scroll** in either picker — narrowing keeps the CREATE set under the clamp;
  if a narrowed set ever exceeds 100, the clamp still bites (acceptable v1.x; the `_resolve_implement_version`
  guard keeps it safe).

## 8. Tests

- **API unit (native-runnable on this Windows box):** `_parse_filter_bool` / `_filter_condition` raise
  422 `validation_error` on a non-boolean value for each new field; `_parse_document_filters` accepts
  the two new pairs and still 400s an unknown `filter[...]`. (Pure Python, no DB.)
- **API integration (CI-only on this box — write failing-first by reasoning):** seed an org with an
  Effective initial doc, an Approved-new doc (no effective version), an Approved *revision* of an
  Effective doc (effective-bearing), and an OBJ (managed subtype). Assert: `has_effective_version=false`
  returns only the never-released docs (excludes the revision + Effective doc); `managed_subtype=false`
  excludes the OBJ; the two combined return exactly the Approved-new plain doc. Assertions are
  **run-scoped/delta-based** (shared session DB — never assume clean).
- **Web (`npx vitest run --pool=forks --poolOptions.forks.singleFork=true`):**
  - `buildDocumentsQuery` emits the new params when defined (incl. `false`) and **omits** them when
    undefined (LibraryPage-shape input → unchanged query string).
  - `ImplementCreateDcrModal` calls `useDocuments` with the two filters and renders the returned
    candidates with **no client filtering** — MSW fixture returns only valid candidates (e.g. a doc
    whose `current_effective_version_id` is non-null, to prove the client no longer filters it out, is
    simply not in the server response). Fixtures pinned to the real `_document` serializer via
    `satisfies Document`. `import { expect, it } from "vitest"`. jest-axe smoke unaffected.

## 9. Gates & review

`/check-api` (ruff + mypy-strict + unit) · `/check-contracts` · `/check-web` (eslint + tsc + build +
full vitest). No `/check-migrations` (no migration). Then **diff-critic** (branch diff) +
**web-test-trap-reviewer** (FE). Live smoke: the `/dcrs` drawer is a Chrome-MCP wall, so verify the
**backend** filters via a worker heredoc (create the four doc shapes; assert each filter narrows) and
cover the FE rewiring via the web tests; the Library page LIST drives fine to confirm no regression.
PR → green CI → Codex triage (expect it to probe edges the CLEAN reviewers miss).

## 10. Acceptance criteria

- `GET /documents?filter[has_effective_version][eq]=false&filter[managed_subtype][eq]=false` returns
  only never-released, non-OBJ/MR documents; a bad boolean → 422 `validation_error`; an unknown filter
  → 400 `unknown_filter`.
- `GET /documents` with neither param behaves byte-identically to today (LibraryPage unaffected).
- The CREATE picker shows valid candidates only, with the client-side `.filter` + `useDocumentTypes`
  removed; submit-time behavior (guard, 403/409 submit-and-show) unchanged.
- All five gates green; diff-critic + web-test-trap clean; Codex threads resolved.
