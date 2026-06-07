# EasySynQ Web-UI Track — S-web-2 Design (Faceted Library + read-only detail drawer)

> **Status:** Draft for owner review · **Date:** 2026-06-07 · **Owner:** CoJoA13
> **Work-stream:** the web-UI track, slice 2. Builds on **S-web-1** (PR #83 — app shell + token port + thin
> Library). Shares the SPA architecture recorded in `docs/superpowers/specs/2026-06-06-web-track-s-web-1-design.md`
> (§3 routing / provider tree / api client; not repeated here). This slice is a **full-stack** slice: a handful
> of minimal backend READ endpoints + the faceted Library front-end + a deep-linkable, tabbed detail drawer.

## 1. Context & the three locked owner decisions

S-web-1 shipped the shell, the design-token port, and a *thin* Library (Identifier · Title · State · Clause chips,
row → a basic overview drawer). The original 3-slice plan called slice 2 **"Library + Document detail"**. Before
planning, a grounded exploration (the `s-web-2-understand` workflow, 2026-06-07) mapped the real API surface and
surfaced that the as-built backend is thinner than both `docs/15` and the mockup. The owner then locked three
scoping decisions:

- **D-A — API fidelity = add minimal read endpoints.** Add the small read endpoints the owner-approved mockup
  Library needs (a document-type list + a low-privilege user-name directory) and the `GET /documents` query
  enhancements (date filter + pagination). **No DB migration** (all tables exist). This touches the `api`,
  `contracts`, and `integration` CI jobs.
- **D-B — split into two PRs.** Slice 2 ships the **Library** (this spec). The standalone **Document detail page**
  becomes **S-web-3**; **Review & Approve** shifts to **S-web-4**. (Slice renumber recorded in §10.)
- **D-C — drawer tabs = Overview + History + Where-used.** The three fully backend-supported read tabs.
  Approvals / Acknowledgements / Audit are deferred (see §9 for the per-tab reason).

## 2. What the API actually serves today (the grounding facts)

The as-built API (read at the handler + serializer level — file cites in the plan) differs from `docs/15`'s
aspirational contract. S-web-2 builds to the **as-built reality**, evolving it only where D-A authorizes:

- `GET /documents` returns a **bare JSON array** (no `{data,page,_links}` envelope, no HATEOAS `_links`),
  **`limit`-only** (default 50, hard cap 100, **no offset/cursor**, always `ORDER BY created_at DESC`), and a
  **5-pair filter allow-list**: `(current_state,eq)`, `(classification,eq)`, `(document_type,eq)` [UUID],
  `(owner_user_id,eq)` [UUID], `(clause_refs,has)` [clause number]. The `limit` is a **pre-authz SQL cap**; the
  `document.read` scope filter then runs **per-row in Python** (`authorize()` over `folder_path` + `document_level`),
  so the returned count can be `< limit` and there is no total. (§5.3 — this shapes how pagination must work.)
- `document_type_id` and `owner_user_id` come back as **bare UUIDs**. There is **no `GET /document-types`** route at
  all. Owner names resolve only via `GET /users` which is **admin-gated** (`user.read`). `GET /me` is identity-only;
  there is **no `/me/permissions`** and the endpoints emit **no `_links`**.
- The read surfaces the drawer needs all exist: `GET /documents/{id}` (`document.read`),
  `GET /documents/{id}/versions` (gated **`document.read_draft`** — it exposes Draft content),
  `GET /documents/{id}/where-used` (`document.read`, already resolves neighbour titles + carries the §7.3
  obsoletion advisory), `GET /clauses` (`clauseMap.read`), `GET /documents/{id}/download` (`document.read`,
  presigned Effective controlled copy).
- `next_review_due` is **not populated** (drift family, deferred) — no "Next review" column/signal (as in S-web-1).

## 3. Scope of S-web-2

**In:** the minimal backend read endpoints (§4) + the faceted Library page (§5) + the deep-linkable tabbed detail
drawer with Overview / History / Where-used (§6) + the shared **artifact header** component (DP-5) the drawer and
the future Document page both reuse + full test + a11y coverage (§7) + the `openapi.yaml` updates.

**Out (deferred, with reasons in §9):** the standalone Document detail page (S-web-3); the Approvals / Acks / Audit
tabs; any write/action affordance (check-out, export, new-revision, decide — these need `/me/permissions` and land
with S-web-4); per-clause document **counts** in the clause tree (an authz-correct count is an aggregation that
risks the §9.3 existence-leak — deferred); the Process-map and PDCA **lens switcher** (no process-map screen yet —
rendering disabled lenses would be a dead control, against DP-6); saved searches; `⌘K` search behaviour.

## 4. Backend — the minimal read endpoints (no migration)

All new/changed endpoints are documented in `packages/contracts/openapi.yaml` in-PR and unit-tested; the
`GET /documents` changes also get integration coverage.

### 4.1 `GET /api/v1/document-types` (new)
Reference data for the friendly **Type** column + the **Type** facet. Org-scoped list of
`{id, code, name, document_level, is_singleton}` ordered by `name`. **Gate: authentication only**
(`get_current_user`), matching how `GET /documents` itself is entry-gated. `document.read` is a CONTENT key
resolved per-resource at ARTIFACT/FOLDER/DOC_CLASS scope, so `require("document.read")` at the default SYSTEM scope
would lock out an ordinary reader whose grant is narrower — and the type catalog is innocuous org reference data
needed to render whatever rows the authenticated, row-filtered list returns. No new permission key. New small router
module `api/document_types.py` (the `api/clauses.py` reference-data precedent).

### 4.2 `GET /api/v1/directory/users` (new — minimal name directory)
Resolves `owner_user_id` → display name for the **Owner** column + the **Owner** facet. Returns **only**
`[{id, display_name}]` for **ACTIVE, non-guest** users, org-scoped, ordered by `display_name`. **Gate:
authentication only** (same reasoning as §4.1 — the list already hands every authenticated caller the
`owner_user_id` UUIDs of the rows they may read; resolving those to colleague display names is the same information
class). Minimal-disclosure: e-mail / keycloak_subject / status / roles are **never** exposed here, unlike the admin
`GET /users`. New `api/directory.py` module. No new permission key.

### 4.3 `GET /api/v1/documents` — pagination envelope + date filter + the Effective column field
- **Pagination (authz-correct).** Because the scope filter runs per-row in Python *after* the SQL `limit`, naïve
  SQL `OFFSET` would page the wrong (pre-authz) set. v1 approach: fetch the filtered candidate set ordered
  `created_at DESC` **capped at a hard ceiling** (`_LIST_SCAN_CAP`, e.g. 2000), authz-filter in Python, then slice
  `[offset : offset+limit]`. Return a **`{data, page}` envelope**: `data` = the page rows (the existing `_document`
  shape, unchanged), `page = {limit, offset, returned, has_more}` (`has_more` = authorized-len > offset+limit,
  accurate up to the cap; no `COUNT(*)` — honours "no exact totals on hot paths"). New query params `limit`
  (default 50, max 100) + `offset` (default 0). **Scaling caveat documented**: for installs whose authorized doc
  count exceeds the cap, the scope filter must move into SQL — reserved behind the same "ship the simple correct
  realization first" posture as OpenSearch/MinHash (R34). *This is a response-shape change from the bare array; the
  only consumers are the web app + tests + `openapi.yaml`, all updated in-PR.*
- **Date facet.** Add `(effective_from, gte)` and `(effective_from, lte)` to the filter allow-list, evaluated via a
  join to the document's `current_effective_version` (`DocumentVersion.effective_from`). A doc with no effective
  version (Draft-only) has no effective date and is correctly excluded by a date bound. The front-end maps the
  mockup's relative buckets (Last 30 / 90 days / 12 months) to a `gte` **date** (YYYY-MM-DD — stable within the day,
  so no refetch loop). (Other filters unchanged.)
- **The Effective column field.** Add `effective_from` to the document row serializer (`_document`), populated on
  the list + detail reads by a batch join (`_effective_from_map`, no N+1) over each row's
  `current_effective_version_id`. Null on Draft-only docs and on create/patch responses. This unblocks the mockup's
  **Effective** column + the artifact-header "Effective since …" date (the same join the date filter uses).

### 4.4 Explicitly NOT added this slice
`/me/permissions` (DP-6 affordance gating) — **not needed** for a read-only Library/drawer that renders no write
controls; it lands with the write actions in S-web-4. A per-clause document-count endpoint — deferred (§9).

## 5. Front-end — the faceted Library

### 5.1 Layout (two-column, matching the mockup `#screen-library`)
- **Page header** — breadcrumb (Home › Library) + "Document Library" title + a result-count caption
  ("Showing N of …" honoured against `page.returned` + `has_more`); the mockup's "New Document" primary action is a
  **write** affordance → **omitted** this slice (DP-6; returns in S-web-4).
- **Left rail (264px) — clause-spine tree** from `GET /clauses`, PDCA-banded (PLAN/DO/CHECK/ACT), `★` mandatory
  marker, click a clause → sets the Clause filter (`filter[clause_refs][has]=<number>`) and shows it on the chip
  shelf. **No per-clause doc counts this slice** (§9). This is the in-page faceting tree (distinct from the global
  S-web-1 LeftRail, which stays navigation).
- **Right column** — the facet bar + chip shelf + results table + pagination footer.

### 5.2 Facets (the filter bar + the removable-chip shelf)
Five facets, each backed end-to-end now that §4 lands: **Type** (`useDocumentTypes`), **Status** (the 7-state
`current_state` enum), **Owner** (`useUserDirectory`), **Clause** (`useClauses`), **Effective date** (relative
buckets → `effective_from[gte]`). Active filters render as **removable chips** above the table + a **Clear all**.
Filter state lives in the **URL query string** (so a filtered view is shareable/back-button-safe), translated to the
bracketed `filter[field][op]` params. An **Advanced ▾** disclosure is reserved but empty this slice.

### 5.3 Results table
Columns (order per the mockup, minus the deferred ones): **Identifier** (mono) · **Title** (+ a secondary meta
line) · **Type** (resolved name) · **Owner** (avatar initial + name, resolved via the directory map) · **Clause**
(chips) · **State** (the 7-state semantic badge — icon + label, never color-only, DP-7) · **Rev** · **Effective**
(date). **No "Next review" column** (`next_review_due` absent). Row click → opens the detail drawer (`?detail=<id>`).
Density toggle (Comfortable / Compact) per the mockup; keyboard-operable rows (tabIndex + Enter/Space, the S-web-1
pattern). **Loading** = skeleton rows; **empty-with-filters** = "No documents match these filters" + Clear filters;
**permission-empty** = "Nothing here is in your access scope" (DP-6; the list filters, never 403s).

### 5.4 Pagination footer
Page-size select (25 / 50 / 100) + a prev/next pager driven by `offset` + `page.has_more`. No exact total
(honest — derived from `has_more`).

## 6. Front-end — the deep-linkable tabbed detail drawer (DP-3 / DP-5)

Extend the S-web-1 `DocumentDrawer` into a **tabbed** drawer (Mantine `Tabs`), deep-linkable via `?detail=<id>`
(+ an optional `&tab=` for the active tab), focus-trapped, ESC/scrim close, resizable (the S-web-1 360–640 handle),
focus returns to the originating row. Tabs **lazy-load** (each is its own TanStack Query, fetched on activation —
opening the drawer never eagerly fetches everything). The **artifact header always renders** even if a tab errors
(per-tab scoped error + Retry, no full-screen takeover).

- **Shared `ArtifactHeader` (DP-5)** — a new, lens-agnostic component: identifier (mono) · title · large state
  badge · owner (resolved) · type (resolved) · clause chips · key dates (effective). Built here, **reused verbatim**
  by the S-web-3 Document page. The "Download controlled copy" action renders only when a `current_effective_version`
  exists (it is `document.read`, which every lister holds — safe to render); the write actions (Export / New
  revision / Check out) and the "⤢ Open full" promotion are **omitted** until their target/permission lands (S-web-3/4).
- **Overview tab** — the artifact header + a control-metadata definition list (identifier, state, revision, owner,
  clause map, classification, folder path, effective date) + the controlled-copy download.
- **History tab** — the immutable version timeline from `useDocumentVersions` (newest first; rev label + state badge
  + date + author + change reason). Gated `document.read_draft`: on 403, the tab shows quiet "no access" (DP-6).
  Read-only — the mockup's per-version "Compare ▾" (redline/diff) is an **S-web-4** affordance, omitted here.
- **Where-used tab** — the §7.2 categories from `useWhereUsed` (processes · child/parent docs · referenced-by ·
  forms · records-produced · clauses), neighbour titles already resolved server-side. Read-only.

## 7. Testing & accessibility (the binding gates)

- **Front-end (stack-free):** vitest + @testing-library/react + **MSW** + **jest-axe**, the S-web-1 idiom
  (`renderWithProviders`, co-located `<Name>.test.tsx`). New MSW handlers + fixtures for `GET /document-types`,
  `GET /directory/users`, the `{data,page}` documents envelope, `GET /documents/{id}`, `/versions`, `/where-used`.
  Cover: facet apply/remove + chip shelf + URL sync; the results table (resolved Type/Owner, state badges);
  pagination (offset/has_more); drawer open/deep-link/tab-switch/lazy-load + the read_draft-403 quiet-absence path.
  **A jest-axe `toHaveNoViolations` on the Library page and the open drawer is a release gate** (WCAG 2.2 AA).
- **Backend:** `api` unit tests for the two new endpoints (gate, org-scoping, shape, minimal-disclosure of the
  directory) + the `GET /documents` envelope/offset/`has_more`/date-filter; an **integration** test for the
  authz-correct pagination (a scoped user pages correctly under the per-row filter — the demo-admin-sees-all path
  is not sufficient) and the `effective_from` date filter. Assertions **run-scoped / delta-based** (the shared
  integration DB — the S-ing-4 lesson).
- **CI:** all five jobs green — `contracts` (openapi.yaml), `api`, `integration`, `web` (eslint/tsc/build/test),
  `migrations` (unaffected — no migration).

## 8. Data flow & errors

```
AuthProvider (OIDC) ─token─▶ useApi() ─▶ React Query hooks ─▶ Library page / drawer tabs
GET /document-types, /directory/users  → cached id→name maps (Type & Owner columns + facets)
GET /documents?filter[...]&limit&offset → {data, page}  (URL-driven filter + pager)
GET /documents/{id} · /versions · /where-used → lazy drawer tabs
```
- **403** on a tab (e.g. `/versions` without `document.read_draft`) → quiet "no access" in that tab (DP-6), header
  persists. **List** never 403s — it filters (§9.3). RFC 9457 `ApiError.code`/`.status` branching (never `.message`).

## 9. Out of scope — and why (honesty over mockup-completeness)

- **Standalone Document detail page** → S-web-3 (D-B). The drawer + the shared `ArtifactHeader` are built so the
  page is largely a layout reflow of the same components.
- **Approvals tab** → needs a workflow-instance **discovery** route for a document (none exists — only
  `GET /workflow-instances/{id}` by id), and signatures surface only inside the version-diff provenance. Lands with
  S-web-4 (Review & Approve), which also adds the decision write + `/me/permissions`.
- **Acknowledgements tab/gauge** → **no v1 endpoint** (R15 new-joiner acks deferred to the drift family).
- **Audit tab** → `GET /documents/{id}/audit-events` is gated on the SYSTEM key `system.audit_log.read`
  (auditor/admin only); a privileged-only Audit tab is deferred to keep this slice's surface uniform.
- **Per-clause doc counts** in the tree → an authz-correct count is a grouped aggregation that would either ignore
  the per-row scope filter (over-reporting → a §9.3 existence leak) or require pushing authz into SQL; deferred.
- **Lens switcher (Process map / PDCA)** → no process-map screen yet; disabled lenses = dead controls (DP-6).
- **Write affordances** (New Document / Export / New revision / Check out / Compare-redline / Open-full) → S-web-3/4.
- **`next_review_due` / overdue** → not populated until the drift family.
- **True at-scale pagination** (authz in SQL) → reserved behind the `_LIST_SCAN_CAP` (R34 posture).

## 10. Decisions log

- **D-A** API fidelity = add minimal read endpoints (document-types · low-priv directory · documents
  pagination+date-filter); no migration; `/me/permissions` explicitly deferred to S-web-4.
- **D-B** split slice 2 → **S-web-2 = Library** (this), **S-web-3 = Document detail page**, **S-web-4 = Review &
  Approve** (renumber from the S-web-1 plan's 3-slice list).
- **D-C** drawer tabs = Overview + History + Where-used.
- Pagination is an authz-correct **fetch→filter→slice** with a documented scan cap + a `{data, page}` envelope
  (response-shape change, all consumers updated in-PR), not naïve SQL OFFSET.
- Date facet = `effective_from` (via the current-effective-version join), matching the mockup's "Effective" label;
  `effective_from` is also added to the document row (the Effective column + header date).
- The two new reference-data endpoints are **authenticated-only** (not `document.read`-gated — that key resolves at
  SYSTEM scope and would exclude ordinary readers); the directory exposes display-name only (minimal disclosure).
- The clause tree ships without per-clause counts; the in-page lens switcher is omitted.
