# S-records-read-console — Evidence Operations read console

**Date:** 2026-08-14

**Status:** Owner approved on 2026-08-14; ready for an executable implementation plan.

**Programme:** Evidence Operations

**Slice:** First slice — read-only Records register and detail route

## 1. Outcome

An authorized operator can use a dedicated Records console to find a known record by identifier or
title, review readable records by operational state, open a stable record URL, understand the
record's provenance and lifecycle, and download authorized evidence or a ready structured rendition.

Lookup and operational review are equal first-slice goals. The UI therefore combines server-side
search, all five existing structured record filters, cursor pagination, a full-width register, and a
dedicated detail route.

This is a read-only slice. It exposes shipped Records behavior without adding a second source of
truth or weakening any Records invariant.

## 2. Owner-approved decisions

The owner approved the following design calls in sequence:

1. The first Evidence Operations slice is a read-only Records console, not capture or lifecycle
   mutation.
2. Known-record lookup and operational review have equal priority.
3. Search and pagination are server-side; search matches identifier and title only.
4. The UI exposes record type, disposition state, legal hold, source document, and captured-by
   filters with human-readable choices.
5. The register is full width and opens a dedicated `/records/:recordId` detail route.
6. Because the product was only partly deployed and never completed as a supported production
   setup, the existing list contract may be replaced in place and all known consumers migrated
   atomically. A duplicate compatibility endpoint is not retained.
7. Required browser evidence remains deterministic Chromium-only evidence with one worker and zero
   retries.

## 3. Scope boundaries

### 3.1 In scope

- modernize `GET /records` from a bare array to an authorization-correct cursor page;
- add identifier/title search and retain the five current structured filters;
- add display-ready list and detail labels without widening related-object authorization;
- migrate the CAPA evidence picker and every other repository consumer of the old list response;
- add `/records` and `/records/:recordId` SPA routes and permission-aware navigation;
- render record identity, provenance, lifecycle, correction lineage, structured values, evidence
  blobs, evidence-for links, and rendition availability;
- request fresh presigned evidence/rendition download URLs with visible per-action feedback;
- add migration `0086` for deterministic Records paging;
- extend the required responsive Chromium cohort to the Records register and detail route; and
- update repository authority, ADR, debt, current-status, and slice-history records at their proper
  delivery checkpoints.

### 3.2 Out of scope

- record capture, structured-form capture, or file upload;
- corrections or evidence-link creation/removal;
- legal-hold, disposition, retention-policy, or WORM-destroy mutations;
- Evidence Pack or Retention Policy management routes;
- new permission keys, role defaults, or Keycloak behavior;
- general register sorting, bulk selection, density controls, saved views, exports, or dashboards;
- a universal responsive-table abstraction;
- Firefox, WebKit, actual assistive-technology sessions, live object-store/Keycloak acceptance,
  Docker-backed application acceptance, deployment, or disposable Fedora proof.

## 4. Authority and compatibility

R3 deny-always-wins, the Records process-scope rules, correction-chain fallback, tenant boundaries,
immutable-record semantics, source-version pinning, append-only audit, and WORM/source-store
boundaries remain binding.

R65 records the temporary pre-production compatibility posture. Before the first supported
production deployment or external-client compatibility commitment, a breaking cleanup may be
preferred when it materially improves the product, is explicitly designed, migrates all known
consumers atomically, and removes obsolete paths. R65 never authorizes destructive data loss, unsafe
migrations, or relaxation of security and evidence controls.

[ADR 0004](../../adr/0004-modernize-records-list-contract-in-place.md) records the public-interface
choice and alternatives. Its deliberate compatibility obligation is mirrored in
`docs/debt/20260814085943-preproduction-api-compatibility.md`.

## 5. API contract

### 5.1 Request

`GET /records` accepts:

- `limit`: integer `1..100`, default `50`;
- `cursor`: an opaque, versioned page token;
- `q`: trimmed, maximum 200 characters, case-insensitive substring matching over `identifier` and
  `title`; `%`, `_`, and the escape character are matched literally;
- `record_type`;
- `source_document_id`;
- `captured_by`;
- `disposition_state`; and
- `legal_hold`.

The five filter names and value vocabularies remain the current plain query-parameter contract. A
blank `q` is ignored. Invalid enums, UUIDs, limits, overlong search values, malformed cursors,
unsupported cursor versions, and cursor/query mismatches return the canonical problem response with
status `422` and code `validation_error`.

### 5.2 Cursor and ordering

Rows are ordered by `(captured_at DESC, id DESC)`. Migration `0086` and the SQLAlchemy model add the
matching `(org_id, captured_at DESC, id DESC)` index.

The cursor is opaque to clients and is not a secret. Its versioned payload binds the last returned
ordering tuple to a fingerprint of normalized search and filter inputs. Reusing a cursor with a
different query is rejected instead of silently skipping into the wrong result set.

New captures sort before an existing cursor and therefore appear only after the user returns to the
first page or refreshes it. Records are content-immutable; a concurrent disposition or permission
change may legitimately remove a row from a later request. The contract is keyset-consistent, not a
long-lived database snapshot.

### 5.3 Authorization-correct page construction

The endpoint gathers the caller's `record.read` grants once. An empty grant set returns an empty page
without scanning records. Otherwise it queries deterministic candidate batches after applying the
tenant, search, filter, and cursor predicates.

For each batch it reuses the canonical PDP resource tuple, the batched record-process binding loader,
the source-less correction fallback, predicates, and deny-always-wins. It continues until it has
`limit + 1` readable rows or reaches the end. The first `limit` rows are returned; the extra readable
row proves that `next_cursor` is non-null. The cursor boundary is the last returned readable row, so
no readable row is skipped. No total, candidate count, hidden-row count, or hidden-row-derived cursor
is exposed.

This simple-correct-first scan deliberately avoids inventing a second SQL authorization engine. Its
payoff contract is registered in `docs/debt/20260814085951-record-authz-page-scan.md`: once one page
must scan more than 2,000 matching candidates, replace it with a proven SQL candidate predicate or
readable-ID projection that remains equivalent to the canonical PDP.

### 5.4 Response

The old array is replaced by:

```text
RecordPage {
  data: RecordSummary[]
  page: {
    limit: integer
    returned: integer
    next_cursor: string | null
  }
}
```

`RecordSummary` is the list-safe subset required by the register and migrated picker:

- `id`, `identifier`, `kind`, `record_type`, `title`, `classification`, and `framework_id`;
- `captured_at`, `captured_by`, and `captured_by_display_name`;
- `source_document_id`, `source_document_identifier`, `source_document_title`, and
  `source_document_readable`;
- `source_version_id` and `source_version_label`;
- `retention_policy_id` and `retention_policy_name`;
- `disposition_state`, `legal_hold`, and `has_structured_pdf`; and
- `correction_of` and `superseded_by_correction`.

Nullable labels use `null`, never a fabricated identifier. The API loads labels in bounded batches
after authorization; the register does not make per-row requests.

`GET /records/{record_id}` remains the canonical detail endpoint and retains its full record shape,
including evidence blobs, evidence links, form values, hashes, and lifecycle fields. It gains the
same display-label fields as the summary. Evidence links add nullable `target_label` and boolean
`target_readable` fields. The backend never emits SPA paths. When `target_readable` is true and the
target type has an existing route, the SPA maps that type and id to the local route; otherwise it
shows a label without navigation. A restricted or missing target returns `target_readable=false` and
`target_label=null`, and the UI shows a neutral restricted-related-item label without turning the raw
UUID into a navigation affordance. Source-document and correction-lineage links follow the same
independent-read rule.

### 5.5 Downloads

Evidence and rendition bytes continue to come from the existing presigned-GET endpoints. The API
does not proxy object bytes. Each activation requests a fresh short-lived URL. The SPA opens that URL
in a new tab with `noopener,noreferrer` and never sends an EasySynQ bearer token to the object store.

A rendition `409 rendition_pending` is a normal not-ready state. `403`, `404`, and storage/presign
failures remain distinct canonical problem responses.

## 6. Migration `0086`

Migration `0086` adds the deterministic page-order index to `record` and nothing else. It creates no
rows, rewrites no record content, changes no enum, and does not alter retention or WORM state. The
downgrade removes only that index. Model metadata, Alembic heads, migration policy tests, and a fresh
upgrade/downgrade proof must agree.

Before implementation writes the migration, `uv run alembic heads` is authoritative. The prose
snapshot naming `0086` is a planned identifier, not permission to fork a changed migration head.

## 7. Web architecture

### 7.1 Routes and units

The SPA adds:

- `RecordsPage` at `/records` — URL parsing, register query, state composition, and page navigation;
- `RecordFilters` — the five filter controls and active-filter chips;
- `RecordsTable` — one semantic table whose identifier link is the only row-primary action;
- `RecordDetailPage` at `/records/:recordId` — detail query and section composition;
- focused provenance, lifecycle, evidence, evidence-link, structured-values, and correction-lineage
  panels; and
- typed `useRecords`/`useRecord` query hooks and pure query-string/cursor-state helpers.

Each unit owns one concern and receives typed data. Route components orchestrate; they do not contain
API serialization, download transport, or low-level table interaction logic.

Navigation shows Records only when `record.read` is effectively available. Direct route access still
relies on the API as the security boundary.

### 7.2 URL state and navigation

The register URL owns `q`, `record_type`, `disposition_state`, `legal_hold`, `source_document_id`,
`captured_by`, and `cursor`. The UI uses the API default page size of 50; adjustable page size is out
of scope.

Search is debounced. Search and filter edits use history replacement and clear `cursor`. Advancing a
page writes the returned cursor through an ordinary history entry. The identifier is a native link to
the dedicated detail route, so browser Back returns to the exact filtered page request. The detail
page's “Back to filtered Records” action uses the originating location when present and falls back to
`/records` for direct bookmarks.

A malformed or query-mismatched cursor deep link receives an invalid-page message with a native
“Return to first page” action that removes only the cursor.

### 7.3 Register

The register header reports only the returned readable count, for example “Showing 50 records”; it
does not imply a total. The toolbar contains identifier/title search, record type, disposition,
legal-hold, source-document, and captured-by controls, plus removable chips and Clear all.

The source-document picker uses the row-filtered Documents typeahead and displays identifier plus
title. The captured-by picker uses the safe directory endpoint. Both serialize UUID filters while
showing human labels. A deep-linked value that cannot be resolved receives a neutral selected-value
fallback without widening read authority.

The table columns are Identifier, Title, Type, Captured by, Captured, and State. The State cell conveys
disposition and legal hold without relying on color alone. Identifier is a visible native link with a
record-specific accessible name. Rows stay structural: no row click handler, role, `tabIndex`,
synthetic keyboard activation, stretched overlay, or duplicate mobile control.

The page has explicit loading, unfiltered-empty, filtered-empty, invalid-page, and retryable-error
states. A server failure preserves the URL and controls and never presents stale data as fresh.

### 7.4 Detail

The detail header shows identifier, title, type, classification, disposition, and legal hold. The
body groups:

1. provenance — capture time and actor, source document/version, framework, and seal version/hash;
2. lifecycle — retention policy/basis date, disposition, legal hold, and correction lineage;
3. evidence — filename, content type, byte size, SHA-256, original flag, timestamp, and Download;
4. structured values — rendered only when present, using safe key/value presentation; and
5. evidence-for links — type, authorized label/link or neutral restricted fallback, link reason, and
   timestamp.

The structured PDF action appears only when `has_structured_pdf` is true. A not-ready rendition
response is explained without treating the record as failed. Empty optional sections are omitted or
receive concise “None recorded” copy; raw JSON is not dumped as the primary presentation.

## 8. Failure and security behavior

- List errors retain search/filter controls and expose a focused Retry action.
- Detail `403` explains that access is unavailable; `404` reports that the record cannot be found.
  Neither state renders cached record content.
- Each evidence/rendition request owns its loading and visible retryable error state; one failed file
  does not disable unrelated downloads.
- Client requests remain cancellable through the existing query lifecycle; late responses cannot
  overwrite a newer URL state.
- Related display labels never become an authority grant. Direct links require the related object's
  own read decision.
- No list, picker, problem response, log, fixture, screenshot, or design example introduces real
  installation/site data.

## 9. Accessibility and responsive contract

The Records register adopts the existing shared-register contract rather than creating a second
cohort or universal table abstraction:

- at 320 CSS pixels, toolbar controls stack, search owns the available width, and no control escapes
  the document;
- one `Table.ScrollContainer` owns localized horizontal reachability for one semantic table;
- the detail's paired sections collapse to one column without horizontal page overflow;
- interactive targets meet the existing 44 CSS-pixel contract where applicable;
- focus is visible in normal and forced-colors modes;
- status and error meaning is available through text, not color alone;
- reduced-motion and theme behavior remain inherited from the application shell; and
- axe has no serious or critical violations on the register and representative detail states.

Adding Records to browser evidence triggers the existing browser debts. The slice must:

1. close `browser-probe-hardening` by terminating timed-out child probes and normalizing leading
   `./` path components before protected-root comparison;
2. close `forced-colors-containment-proof` by making the forced-colors case assert active-element
   containment directly; and
3. reassess ADR 0003's focused-cohort trigger. The owner-approved result is to keep the dedicated
   authenticated test entry, central fail-closed fixtures, Chromium-only engine, one worker, and zero
   retries for this slice.

`responsive-register-cohort` remains open: this route uses the same non-action-heavy contract and
does not create the second cohort or specialized action-heavy surface named by its payoff trigger.

## 10. Verification contract

### 10.1 API and authorization

Focused RED/GREEN tests prove:

- response envelope and default/max limits;
- stable tuple ordering, tie-breaking, cursor round-trip, query fingerprinting, and invalid cursors;
- trimmed case-insensitive identifier/title search with escaped wildcard characters;
- each filter and representative filter combinations;
- empty grants, system grants, process grants, explicit denies, predicates, correction fallback,
  hidden candidates between visible rows, exact-page endings, and no-readable-row endings;
- batched label hydration and restricted related-link fallbacks; and
- unchanged detail/download gates and problem responses.

### 10.2 Contracts, migration, and consumers

- OpenAPI describes the request, `RecordSummary`, `RecordPage`, nullable label fields, and problems.
- Generated server/web artifacts and their checksum change only through the repository generator.
- Contract-response tests validate list, detail, evidence-link, and download responses.
- Migration tests prove `0086` is the single head, upgrade/downgrade cleanly, and model/index metadata
  agree.
- CAPA picker tests prove it consumes `data` from the page and retains its selection behavior.

### 10.3 Web and browser

Focused component/route tests prove:

- permission-aware navigation and direct-route behavior;
- URL parsing, debounce, filter serialization, cursor reset, next-page history, and return from detail;
- loading, empty, filtered-empty, invalid-page, `403`, `404`, retry, and pending-rendition states;
- semantic table/link behavior, keyboard access, focus restoration, accessible names, and axe;
- conditional detail sections and per-file download progress/error isolation; and
- no bearer token is sent to a presigned object-store URL.

Required Playwright Chromium evidence proves representative register and detail fixtures at desktop
and 320-pixel widths, localized table reachability, absence of document-level overflow, keyboard
focus, forced-colors containment, fail-closed traffic interception, and route recovery. It remains
one worker with zero retries.

### 10.4 Gates and claims

Run the smallest focused test first, then the affected API, contract, migration, web, integration,
Chromium, authority, and site-data gates. A genuinely long complete suite runs as a durable background
job. The handoff records exact commands and results.

Do not update test counts, CI topology, or coverage claims unless the corresponding complete gate was
freshly verified. Do not rewrite `docs/current-status.md`'s `baseline_commit` merely because this
branch started from current main.

Browser evidence does not claim Firefox/WebKit, actual assistive-technology sessions, live
backend/database/object-store/Keycloak acceptance, Docker-backed acceptance, deployment, migration
round-trip beyond the executed local gate, or disposable Fedora proof.

## 11. Acceptance criteria

The slice is complete only when:

1. an authorized user can search identifier/title across more than the old 100-record cap;
2. all five selected filters work alone and in representative combinations;
3. pagination is deterministic and no hidden record appears or distorts a readable page;
4. the old bare-array response and every obsolete repository consumer assumption are removed;
5. the CAPA picker still lists and selects readable records;
6. the register and detail routes are deep-linkable and Back restores the filtered cursor page;
7. the register uses one semantic table with a native identifier link;
8. the detail explains provenance, lifecycle, lineage, structured values, evidence, and links without
   authorization widening;
9. evidence and rendition actions request fresh presigns, omit bearer credentials to the object
   store, and show visible isolated failures;
10. migration `0086`, OpenAPI, generated artifacts, and model metadata agree;
11. focused and affected test gates pass with fresh evidence;
12. responsive Chromium evidence includes Records and closes the two triggered browser-proof debts;
13. repository authority and site-data gates pass; and
14. every unverified environment boundary is stated without being converted into a pass claim.

## 12. Alternatives considered

### Add a dedicated register read endpoint

This would preserve the bare array, but it would create two list contracts and two drift surfaces.
The owner rejected compatibility duplication for an incomplete deployment.

### Retain the array and carry the cursor in headers

This preserves the response body but makes pagination awkward for generated clients and leaves label
hydration fragmented.

### Browser-only filtering

This is smallest but searches only loaded records and cannot satisfy reliable lookup past the old cap.

### Master-detail or slide-over UI

Both retain register context, but they compress long evidence/lifecycle content and add responsive,
focus, and history complexity. The owner selected the dedicated detail route.

## 13. Implementation ruling addendum — 2026-08-15

This dated addendum records the owner-approved final-review rulings that govern the shipped slice. It
does not rewrite the historical design process above.

- The Records rail entry is unconditional. A SYSTEM permission inventory cannot observe a caller's
  PROCESS-scoped `record.read`; the row-filtered Records API remains the security boundary, and a caller
  with no applicable grant receives the calm empty register.
- R59 applies independently to a source document label and its pinned immutable version label. The
  document must first pass canonical `document.read`. Effective versions need no additional key;
  Draft, InReview, and Approved require `document.read_draft`; Superseded and Obsolete require
  `document.read_obsolete`. Specialized decisions retain the canonical Document tuple and project the
  immutable version lifecycle/identity fields, with DENY precedence and no fabricated label.
- Cursor fingerprints preserve the exact trimmed Unicode search string. They do not case-fold it:
  PostgreSQL `ILIKE` behavior does not make Unicode case-fold expansions equivalent cursor criteria.
- The web register recognizes an invalid-page condition only for a cursor-bearing canonical
  `422 validation_error` whose problem title is `Invalid records cursor`. Other validation failures,
  including an invalid enum combined with a cursor, remain ordinary load errors.

Final review also confirmed that an empty cursor is invalid at both the route and OpenAPI boundaries,
and that dynamic search/evidence labels retain their full accessible names while bounded visually at
320 CSS pixels. These points refine the earlier verification contract without changing the read-only
slice boundary.
