# S-report-doc-control — Controlled Document Register (`GET /reports/document-control`)

> Design spec. Survey item **#4** (HIGH value, L effort, low risk). Closes the first of the three
> canonical document-control reports specified in `docs/13 §6` and the endpoint stub in
> `docs/15-api-design.md:695`. **No migration** (the `report.read` key is already seeded by
> `0004`; head stays `0070`). BE + a read-only SPA register page. Export is a named follow-up.

## 1. Goal & context

ISO 9001 §7.5.3 expects an audit-defensible **master list of every controlled Document**. EasySynQ
already exposes the document *lens* (the Library) but not the auditor-facing **register report**: a
complete, filterable, provenance-stamped master list whose integrity is verifiable via a content
hash. This slice ships that report end-to-end.

**What already exists** (do not rebuild):
- `report.read` permission key — seeded `migrations/versions/0004_seed_authz.py` (PROCESS-finest,
  non-SoD). Held at SYSTEM scope by Internal Auditor (`_AUDITOR_READ_KEYS`), Process Owner (the
  line-221 bundle), and QMS Owner (org-wide `*.read`); held at ARTIFACT/read-only by the External
  Auditor guest (`_GUEST_KEYS`).
- The endpoint is spec'd: `docs/15-api-design.md:695` — `GET /reports/document-control` → `report.read`
  → identifier, title, owner, effective revision, state, next review, clause coverage. Exportable.
- The full column set + provenance-header design: `docs/13 §6.1` (the "Controlled Document Register").
- The permission-filtered document-list query pattern: `api/documents.py::list_documents`
  (`apps/api/src/easysynq_api/api/documents.py:661`) — `gather_grants` once + process-scoped per-row
  `authorize("document.read", resource, ctx)` + batched no-N+1 enrichment
  (`vault_repo.clause_numbers_for_docs`, `vault_repo.process_ids_for_docs`, `_effective_from_map`).
- The reporting surface + service split precedent: `api/reports.py` (thin route) →
  `services/reports/checklist.py::compute_checklist` (logic).
- Just-shipped web patterns to reuse: calm `LoadingState`/`ErrorState`/`NoAccessState` (#15), the
  `useRouteChrome` per-route title/focus (#16), `Table.ScrollContainer` + `scope="col"` headers (#17),
  and `RegisterToolbar` facet filters.

## 2. Permission model — two-layer (decided: Option A)

The endpoint gates in **two layers**, honoring both `docs/15:695` (gate = `report.read`) and
`docs/13 §6.1` ("all Documents the requester may see"):

1. **Surface gate** — `require("report.read")` at the default SYSTEM scope (the checklist
   `require(...)` precedent). A caller without a SYSTEM `report.read` grant gets **403** before any
   query. This admits Internal Auditor, Process Owner, QMS Owner, and Admin-via-SYSTEM-override; it
   excludes an ordinary Employee and the ARTIFACT-scoped External Auditor guest (guests use Evidence
   Packs, not the org register).
2. **Row filter** — reuse `list_documents`' exact loop: `gather_grants(session, caller.id,
   caller.org_id, "document.read")` once, then per candidate row build a `ResourceContext`
   (artifact_id, folder_path, document_level, process_ids) and keep it iff
   `authorize(grants, "document.read", resource, ctx).allow`. A SYSTEM `document.read` holder sees
   everything; a PROCESS-scoped holder sees only their linked documents (a 200 + a smaller register,
   never a leak). `source_ip` is threaded into the `RequestContext` so an `ip_allow` grant evaluates
   as it does on the document list.

**Deny-by-default is preserved end-to-end:** the surface gate is the "may you pull reports at all"
answer; the row filter is the "which documents" answer. Neither substitutes for the other (this is
the S-process-scope discipline — a row filter must not be the sole gate, and a surface gate must not
skip per-row filtering).

## 3. Completeness — full register, no pagination (decided)

The report returns the **entire permission-filtered register** in one response (no `limit`/`offset`).
Rationale: a master list is inherently complete, and the `content_hash` is only auditor-defensible if
it covers the whole as-of set — a hash over a paginated subset is meaningless. Facet filters narrow
the set deterministically; the provenance header echoes the applied filters so the hash is
reproducible given the same filters + as-of.

- The candidate scan is **all org `kind=DOCUMENT` rows** matching the applied filters (no
  `_LIST_SCAN_CAP` — the register intentionally covers the full set, unlike the typeahead-bounded
  Library list). Bounded by single-org on-prem scale (hundreds–low-thousands of documents), one
  ordered query + batched enrichment.
- Enrichment is **batched over the visible set** (post-authz-filter), never per-row: clause refs,
  process links, effective version (revision label + effective_from + blob hash), and approval
  signatures each load in one grouped query. No N+1.

## 4. Response shape

```jsonc
{
  "provenance": {
    "report_name": "Controlled Document Register",
    "generated_by": "<display_name> (<username>)",   // the caller
    "generated_at": "<ISO-8601, org tz>",
    "as_of": "<same instant as generated_at>",
    "filters": { /* echo of the applied filter[...] params, normalized */ },
    "scope": "org:<short_code>",
    "app_version": "<get_settings().version>",
    "row_count": <n>,
    "content_hash": "sha256:<hex>"                     // over the canonical row set (see §6)
  },
  "rows": [
    {
      "id": "<documented_information.id>",
      "identifier": "<identifier>",
      "title": "<title>",
      "document_type_id": "<uuid|null>",
      "document_type": "<type name|null>",
      "current_state": "<lifecycle state>",
      "owner_user_id": "<uuid>",
      "owner_display": "<display_name|username>",
      "effective_revision_label": "<e.g. 'Rev C'|null>",   // effective version's revision_label
      "effective_from": "<ISO date|null>",
      "blob_sha256": "<effective source_blob_sha256|null>",  // integrity/immutability proof
      "clause_refs": [ { "clause": "7.5.3", "starred": true }, ... ],  // ★ = clause.is_mandatory_star (R30)
      "process_links": [ "<process_id>", ... ],
      "approved_by": "<display_name|null>",   // signer of the effective version's approval/release signature
      "approved_on": "<ISO|null>",
      "next_review_due": "<ISO date|null>",
      "review_state": "<RAG: OK|DUE_SOON|OVERDUE|null>"     // review_state(next_review_due, today_org())
    }
  ]
}
```

Column sourcing (grounded against the models):
- `revision_label`, `effective_from`, `source_blob_sha256` — `document_version` (the row pointed to by
  `documented_information.current_effective_version_id`; null for Draft-only docs).
- `owner_display` — join `app_user` on `owner_user_id` (display_name, fallback username).
- `clause_refs` — `clause_mapping` join `clause`; `starred` = `clause.is_mandatory_star` (the R30
  ★ mandatory documented-information marker; the same source the checklist scores against in
  `services/reports/checklist.py`).
- `process_links` — `vault_repo.process_ids_for_docs` (already batched in `list_documents`).
- `approved_by`/`approved_on` — the `signature_event` on the effective version with
  `meaning ∈ {approval, release}` (latest wins); null when none (e.g. import_baseline-only or Draft).
- `document_type` — batch-load `document_type` names by id (the `levels` map precedent).

**Deferred columns (named, not faked):** `owner`'s **OrgRole** and **retention-of-superseded**
(doc-13 §6.1's fullest set) have no direct source on `documented_information` in v1 → omitted this
slice, tracked with the fuller design intent.

## 5. Service split

New module `apps/api/src/easysynq_api/services/reports/document_control.py`:

```python
@dataclass(frozen=True)
class RegisterRow: ...        # one serialized register row (dict-ready)

@dataclass(frozen=True)
class RegisterResult:
    rows: list[dict[str, Any]]
    content_hash: str
    row_count: int

async def compute_document_control_register(
    session: AsyncSession,
    caller: AppUser,
    *,
    filters: list[ColumnElement[bool]],
    source_ip: str | None,
) -> RegisterResult: ...
```

- Does the candidate query + per-row `document.read` authz filter (reusing `gather_grants`/`authorize`
  from `services.authz`/`services.pdp`) + batched enrichment + row assembly + content hash.
- The **content-hash + provenance assembly** is a **pure helper** (`_register_content_hash(rows)` +
  `build_provenance(...)`), unit-testable with no DB — the design's isolation goal.
- `api/reports.py` stays thin: parse filters (reuse `documents._parse_document_filters` — extract to a
  shared spot or import), call the service, assemble the provenance dict with the caller/version/tz,
  return `{provenance, rows}`. Mirrors the checklist route.

## 6. Content hash — determinism

`content_hash = "sha256:" + sha256(canonical_json).hexdigest()` where `canonical_json` is the row list
**sorted by `identifier`** (stable, case-sensitive byte order), each row serialized with
`json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)`, joined deterministically. The
hash covers the **row data only** (not the provenance block — which carries a wall-clock
`generated_at` that would make every hash unique). Properties the unit tests pin:
- **Deterministic:** same rows (any input order) → same hash.
- **Filter-sensitive:** a different filtered row set → different hash.
- **Order-independent of DB return order** (the sort makes it canonical).

## 7. SPA — read-only Reports register page

New `apps/web/src/features/reports/`:
- `useDocumentControlRegister.ts` — a `useApi()` query hook hitting `/reports/document-control`
  (+ facet-filter params), `forbidden` flag on 403 (the calm no-access pattern).
- `ReportsRegisterPage.tsx` — routed at `/reports/document-control`:
  - `useRouteChrome("Controlled Document Register", ...)` (#16 per-route title + focus).
  - **Provenance banner** — calm, audit-grade: report name, generated-by, generated-at (org tz),
    scope, app version, row count, and the content hash (monospace, truncated with a copy affordance).
  - **Register table** — `Table.ScrollContainer` with `scope="col"` headers (#17); columns per §4;
    RAG next-review carried by **shape + icon + label** (never colour alone — the A11y bar); clause
    refs as ★-flagged chips; `<Mark>`-free plain text rendering (no `dangerouslySetInnerHTML`).
  - `RegisterToolbar` facet filters (type/status/owner/clause/process) mapped to the endpoint's
    `filter[...]` params.
  - `LoadingState`/`ErrorState`/`NoAccessState` (#15) for the three states; 403 → `NoAccessState`.
- Nav entry in `LeftRail` gated on `can("report.read")` (the `/drift`·`/objectives` gated-entry
  precedent) — hidden entirely without the key. Placed in the document-control / reporting group.

## 8. Contracts

`packages/contracts/openapi.yaml`: add `GET /reports/document-control` under the existing `reports`
tag — the two-layer gate (report.read + document.read row-filter), the `{provenance, rows}` schema,
and the facet-filter query params. redocly-lint only (no codegen).

## 9. Testing

**API unit** (`tests/unit/`):
- `_register_content_hash` determinism (input-order-independent) + filter-sensitivity + provenance
  excludes the hash from its own input.
- `build_provenance` shape (caller display, version, tz, scope, filters echo).

**API integration** (`tests/integration/`, run-scoped/delta assertions — shared-DB discipline):
- SYSTEM `report.read` holder → 200 with the register.
- Employee (no `report.read`) → **403** at the surface gate.
- PROCESS-scoped `document.read` holder → 200 with only their process-linked rows (row-filter proof;
  mutation-distinguishing — a SYSTEM grant sees strictly more).
- Guest (ARTIFACT `report.read`) → 403 (excluded from the org register).
- Content-hash covers the full visible set (add a doc → hash changes; the register is complete, not
  paginated).
- Audit-partition safety: any test writing an `audit_event` pins `occurred_at` to a seeded month
  (this endpoint is read-only and emits **no** audit_event — confirm no write path).

**Web** (`vitest` + MSW + jest-axe):
- MSW fixtures pinned via `satisfies` to the real serializer shape (no fabricated shapes).
- Provenance banner renders the header fields + content hash.
- Register table renders rows; RAG legend is shape/label distinguishable (distinct `aria-label`s).
- 403 → `NoAccessState` (calm, not a crash).
- jest-axe smoke (heading order, table semantics).

## 10. Non-goals (named, not faked)

- **CSV/XLSX export** — worker-async (the render/export doctrine); its own follow-up (likely an
  export-job row + migration). No export button this slice.
- **Revision-History (§6.2)** and **Distribution-and-Acknowledgement (§6.3)** reports — the other two
  canonical reports; separate slices.
- **`org_role` / retention-of-superseded** columns — no direct v1 source; deferred with doc-13's
  fuller column intent.
- **Saved report layouts, PDF signed-look, in-app layout authoring** (doc-13 N4).

## 11. Invariants touched

**None of the load-bearing ones.** Read-only endpoint: no WORM/append-only writes, no blob
mutation, no migration, no new permission key, no audit_event emission, no lifecycle transition. The
only new authority surface is a report **read**, gated deny-by-default two-layer. Head stays `0070`.
