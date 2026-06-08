# EasySynQ Web-UI Track — S-web-4 Design (read-only Document detail page + the redline)

> **Status:** Draft for owner review · **Date:** 2026-06-08 · **Owner:** CoJoA13
> **Work-stream:** the web-UI track, slice 4. Builds on **S-web-1** (PR #83 — app shell + token port),
> **S-web-2** (PR #86 — faceted Library + read-only tabbed drawer + the minimal read endpoints +
> `{data,page}` envelope) and **S-web-3** (PR #88 — Document Authoring: the New-Document wizard +
> `AuthorActions` + `GET /me/permissions` + the per-document `capabilities` block). Shares the SPA
> architecture in `docs/superpowers/specs/2026-06-06-web-track-s-web-1-design.md` (§3 routing / provider
> tree / api client) and **reuses the S-web-2/3 `features/document/` + `features/authoring/` components
> verbatim**. This is a **front-end-only** slice: **no migration, no new permission key, no `openapi.yaml`
> change** — every read it needs already exists and is already contracted.

## 1. Context & the locked owner decisions

S-web-2 shipped a read-only Library + a tabbed *drawer*; S-web-3 shipped the author's-half write surface
(wizard + `AuthorActions`) and re-sequenced **S-web-4 = the standalone read-only Document detail page**.
This is that slice: the owner-approved mockup `mockup/easysynq-mockup.html → mockup/screens/document.html`
(doc 11 §5.3 / §4.6 timeline / §4.7 redline) ported into the live app at the `/documents/:id` route
(currently a `Reserved` stub — `apps/web/src/App.tsx:101`). Three scoping decisions were locked with the
owner before this spec (the questions the re-sequence + the mockup left open):

- **D-A — The page reuses `AuthorActions`, capability-gated (not strictly read-only).** The re-sequence
  labelled S-web-4 "read-only," but the mockup header shows *Check out / New revision / Export*. The page
  mounts the **existing** S-web-3 `AuthorActions` (`features/authoring/AuthorActions.tsx`) — which already
  renders **quiet-absence** for anyone who can't act (DP-6) — so a reader sees a pure read-only page and a
  content-author sees the same Check out / Start revision / Submit cluster the drawer offers. "Read-only" in
  the re-sequence meant *no new approve/release write flow* (those are SoD-1-blocked → S-web-5), **not**
  forbidding the existing check-out affordance. The mockup's **"New revision (DCR)"** wires to the existing
  **plain `start-revision`** (the governed doc-05 DCR UI stays deferred); **"Export controlled copy"** is
  gated on the coarse `document.export` (held by no seeded role → quiet-absent by default).
- **D-B — Diff scope = the *text* redline + metadata-diff this slice; the *visual* page-image diff →
  S-web-4b.** The diff backend is **fully built + already contracted** (S-dcr-3a/3b). S-web-4 ships the doc
  11 §4.7 viewer over the **synchronous** `GET …/diff` (metadata-diff table + inline text redline), version-
  pair-selected from the History timeline, gated `document.read_draft` (403 → quiet). The **worker-async
  visual page-image diff** (`POST …/visual-diff` → poll → PNG layers) is carved into a tight follow-up
  **S-web-4b** where the async-polling + image-layer + canvas surface gets full test + a11y coverage. Highest
  value, synchronous, lower-risk — and isolates the meatiest sub-project.
- **D-C — The rendition preview = a card with open/download links, not an embedded PDF.js viewer.** The
  mockup's large left-column watermarked preview is realized as a **rendition-state card**: it surfaces the
  rendition state (`controlled_copy` vs *still-rendering / non-renderable* `source`) + **Open controlled copy**
  (presigned `GET /documents/{id}/download` → new tab, the existing `OverviewTab.tsx:34-46` idiom) +
  **Download (logged)**. No heavy PDF.js dependency, no new browser-PDF fetch/CORS surface this slice;
  honest `no_controlled_rendition` state for non-Effective / R26. (Embedded PDF.js is a later enhancement.)

## 2. What the API actually serves today (the grounding facts — all already contracted)

The page is a pure front-end composition over endpoints shipped by S-web-2/S-web-3/S-dcr. Every shape below
is in `packages/contracts/openapi.yaml` **today** (line refs cited) — so **no contract change is needed**.

- **`GET /documents/{id}`** (detail) → the `Document` schema (`openapi.yaml:4650-4690`): `identifier`, `kind`,
  `title`, `current_state` (7 states), `classification`, `owner_user_id`, `folder_path`, `clause_refs[]`,
  `current_effective_version_id`, `effective_from`, **and the S-web-3 `capabilities` block**
  (`checkout/edit/manage_metadata/submit/release/obsolete/read_draft` — `:4692-4707`, detail-only). **No
  `next_review_due`, no acknowledgement data** (confirms those deferrals). The SPA type is
  `DocumentSummary` (`lib/types.ts:5-37`); `useDocument(id, {enabled, seed})` already fetches it
  (`features/document/useDocument.ts`, key `["document", id]`).
- **`GET /documents/{id}/versions`** → `DocumentVersion[]` (`:4770-4799`; bare list): `version_seq`,
  `revision_label` ("Rev A"), `version_state`, `change_significance`, `change_reason`, `author_user_id`,
  `effective_from/to`, `superseded_by_version_id`. Gate **`document.read_draft`** → a reader without it gets
  403 (rendered as quiet "no access" — `HistoryTab.tsx:11-18`). Hook `useDocumentVersions(id, active)`
  (key `["document-versions", id]`).
- **`GET /documents/{id}/versions/{vid}/diff?from={vid2}`** (`:1630-1654`) → `VersionDiff` (`:4826-4858`):
  `{document_id, from: DiffProvenance, to: DiffProvenance, metadata_diff: [{field, from, to, changed}],
  text_diff: {status: "ok"|"unavailable", reason?, hunks: [{op: "equal"|"insert"|"delete", text}]}}`.
  **Line-level LCS hunks** (not word-level — honour the backend), **synchronous**. `DiffProvenance`
  (`:4801-4825`) carries each version's `revision_label`, `change_reason`, `author_user_id`, `signatures`.
  Gate **`document.read_draft`** (the diff exposes non-released content) → 403 quiet.
- **`GET /documents/{id}/download`** (`:1523-1547`) → `DocumentDownload` (`:5005-5015`): `{download_url,
  content_type, rendition: "controlled_copy"|"source"}`. Gate **`document.read`** (broadly held — every reader).
  Presigned, browser-direct (the #90 `s3_public_endpoint` work). This is the rendition card's open/download.
- **`GET /documents/{id}/where-used`** (`:1267-1279`) → `WhereUsed` (`:5809-5816` + `lib/types.ts:110-124`):
  `processes`, `child_documents`, `parent_documents`, `referenced_by`, `references_out`, `forms_templates`,
  `supersedes`, `superseded_by`, `records_produced_under{count,sample}`, `clauses`, `obsoletion_safety`.
  Neighbour titles resolved server-side. Hook `useWhereUsed(id, active)` (key `["where-used", id]`).
- **`GET /document-types`** + **`GET /directory/users`** (S-web-2, auth-only) — resolve `document_type_id`
  → friendly name and `owner_user_id` → `display_name` (`useDocumentTypes`, `useUserDirectory`; cached maps).
- **The visual-diff trio (built + contracted — but DEFERRED to S-web-4b):** `POST …/visual-diff` (202/200 +
  `VisualDiffStatus{status: Pending|Ready|Failed|Unavailable, page_count, pages[{page,changed}]}` —
  `:1656-1703, 4860-4878`) → `GET …/visual-diff` (poll; 404-before-POST, no side effect) → `GET
  …/visual-diff/page/{n}?layer=from|to|diff` (PNG, `:1705-1729`). **Not consumed this slice.**

**The only existing in-app gap is UI, not API.** `HistoryTab` lists versions but offers **no Compare**; the
diff endpoints have **no consumer**; there is **no standalone page** (the route is a `Reserved` stub). S-web-4
fills exactly those three UI gaps. **No backend code, no migration (head stays `0044`), no new key, no
`openapi.yaml` change.**

## 3. Scope of S-web-4

**In (front-end only):**
- **The standalone page** `features/document/DocumentDetailPage.tsx` at `/documents/:id` (swap the `App.tsx:101`
  `Reserved` stub) — composing, top-to-bottom: the **shell breadcrumb** (enhanced to show the identifier, not
  the raw UUID); a **page header** reusing `ArtifactHeader` (identifier · `StateBadge` · title · type · owner ·
  effective-since · clause chips) + an **actions cluster** = `AuthorActions` (D-A, gated) + an Export button
  (gated `document.export`); a reduced **metric-tile strip** (Governing revision · Mapped clauses ·
  Versions-retained — the *Acknowledged* + *Days-to-review* tiles are deferred); a responsive **two-column
  body** of cards reusing the existing panels.
- **The rendition card** `RenditionCard.tsx` (D-C) — rendition-state + Open/Download (`GET …/download`).
- **The redline/diff viewer** (D-B) — `useVersionDiff` + `RedlineViewer` (metadata-diff table + inline text
  redline, keyboard `n`/`p` + a screen-reader change index) + a **version-pair Compare** control on the
  History card, **URL-driven** (`?from=<vid>&to=<vid>` on the page route, deep-linkable; the S-web-2/3 URL-state
  discipline), gated `document.read_draft` (403 → quiet).
- **Reuse, laid out as page cards:** the version-history timeline (`HistoryTab` content), where-used
  (`WhereUsedTab`), control-metadata (extracted `ControlMetadata` — see §7), `StateBadge`.
- **A drawer → page seam:** an **"⤢ Open full page"** affordance in `DocumentDrawer` linking to `/documents/:id`
  (doc 11 §4.3 "promote to full page"); the Library keeps opening the drawer on row-click (unchanged).
- Full vitest + MSW + **jest-axe** coverage; WCAG 2.2 AA is the release gate.

**Out (deferred, reasons in §10):** the **visual page-image diff** → **S-web-4b**; the **Approvals tab/stepper**
(needs a document→workflow-instance lookup that does not exist) → **S-web-5**; **Acknowledgements** (drift
family — no data); the **Audit tab** (`system.audit_log.read`, SYSTEM-gated); the **Next-review / Days-to-review**
tile (`next_review_due` — drift family); the **Process-map lens** switcher; the **DCR-orchestrated** "New
revision (DCR)" governed flow (the page uses the plain `start-revision`).

## 4. No backend change — the slice boundary (explicit)

This is the cleanest possible slice boundary: **everything the page reads already exists and is already in
`openapi.yaml`.** Therefore:
- **`migrations` CI:** no-op (head stays `0044`).
- **`contracts` CI:** no-op (diff/visual-diff/capabilities/download/where-used/versions are all already
  documented — `:1267,1523,1630,1656,1705,4650,4826,4860,5005,5809`). Run `redocly lint` to confirm; expect
  zero diff.
- **`api` / `integration` CI:** no-op (no Python touched). The detail endpoint already returns `capabilities`
  (S-web-3) and the download already returns `rendition` (S7d) — re-verified against the live stack in the smoke,
  not re-tested here.
- **`web` CI:** the whole slice (eslint / tsc / build / **test** incl. jest-axe).

If the live smoke surfaces a genuinely missing read (none is expected), it would follow the S-web-2/3 own-data
pattern — a migration-free read endpoint, **no new permission key** — but the grounding in §2 shows none is needed.

## 5. Front-end — the page composition (`DocumentDetailPage.tsx`)

A routed page (read `:id` via `useParams`) rendered inside the existing `AppShell` `<Outlet>` (`AppShell.tsx:41-44`,
which already renders `<Breadcrumb/>` above the outlet). Layout follows doc 11 §5.3 + `document.html`, responsive
per §6.3 (desktop two-column; stacks ≤ md).

1. **Header (reuse `ArtifactHeader` + an actions row).** `ArtifactHeader({doc, typeName, ownerName})`
   (`features/document/ArtifactHeader.tsx`) already renders identifier · `StateBadge` (lg) · title · type · owner ·
   effective-since · clause chips — its own comment says it is "reused verbatim by … the full Document page." The
   page resolves `typeName`/`ownerName` from the cached `useDocumentTypes`/`useUserDirectory` maps (the
   `DocumentDrawer.tsx:37-40` pattern). Below it, an **actions row**:
   - **`<AuthorActions doc={doc} />`** (D-A) — verbatim from `features/authoring/AuthorActions.tsx`; capability +
     state + lock gated; quiet-absence for readers. It owns Check out (→ `CheckInPanel`) / clause-map (→
     `ClauseMapper`) / Submit (Draft/UnderRevision), Start revision (Effective), and the "Awaiting review" notice
     (InReview). **No new import cycle** — `AuthorActions` imports nothing from `features/document/` (verified);
     the page importing it mirrors `DocumentDrawer.tsx:7`.
   - **Export controlled copy** — a button gated on `usePermissions().can("document.export")` (coarse SYSTEM;
     quiet-absent since no seeded role holds it), hitting `GET /documents/{id}/export`. Optional/low-priority;
     DP-6 quiet-absence keeps it honest.
2. **Metric tiles (reduced, honest).** A `MetricTiles`/inline strip of Mantine cards:
   - **Governing revision** — the `revision_label` of the version whose `id === current_effective_version_id`
     (found in `useDocumentVersions`); sub = "Effective `effective_from`". Degrades to the state badge when the
     reader lacks `read_draft` (no versions) — never a raw id.
   - **Mapped clauses** — `clause_refs` chips/count; sub = mandatory ★ when any mapped clause is starred
     (from `where-used.clauses[].is_mandatory_star`).
   - **Versions retained** — the `useDocumentVersions` count (read_draft holders only; omitted otherwise).
   - *(Deferred tiles: **Acknowledged** — acks/drift; **Days-to-review** — `next_review_due`/drift. Omitted, not
     faked.)*
3. **Two-column body (Mantine `Grid`, base 12 / md ~7:5; stacks ≤ md):**
   - **Left:** the **`RenditionCard`** (§6.1) + the **Where-used** card (reuse `WhereUsedTab` content; pass
     `active` = true since a dedicated page eagerly loads).
   - **Right:** the **Version history** card (reuse `HistoryTab` content, `active` = true) **+ the Compare
     control** (§6.2) + the **Control metadata** card (the extracted `ControlMetadata`, §7).
4. **States (doc 11 §4.9 / §6.1).** Page-load: header + card **skeletons** (the `LibraryPage.tsx:142-148`
   idiom). `useDocument` **404** → a calm "Document not found" empty state + a Library link. **403** on the
   document itself → "You don't have access to this document." Per-card errors stay **scoped** (the existing
   `HistoryTab`/`WhereUsedTab` 403→quiet / error-text behaviour) — **the header always renders once the doc
   loads** (§6.1). All async tabs/cards on a dedicated page load eagerly (no lazy-tab gate needed).
5. **Breadcrumb (shell, enhanced).** `app/shell/Breadcrumb.tsx` is path-driven and would render the trailing
   `documents/:id` segment as a **raw UUID**. Enhance it minimally: for a segment whose parent is `documents`,
   resolve the label from the React Query cache (`queryClient.getQueryData(["document", id])?.identifier`),
   degrading to the generic "Document" label when not cached. One small, well-contained change; keeps a single
   shell breadcrumb (no double-breadcrumb) and avoids a UUID in the chrome.

## 6. Front-end — the rendition card + the redline viewer (the new surfaces)

### 6.1 `RenditionCard.tsx` (D-C)
A Mantine card (left column). Reads `doc`:
- **Effective (has `current_effective_version_id`):** "Controlled rendition — read-only, watermarked on every
  copy" + **Open controlled copy** / **Download (logged)** → `api.get<DocumentDownload>("/api/v1/documents/{id}/download")`
  → `window.open(download_url, "_blank", "noopener,noreferrer")` (the `OverviewTab.tsx:34-46` pattern, extracted —
  see §7). Show the `rendition` flag: `controlled_copy` = the watermarked PDF; `source` = a calm "controlled PDF
  still rendering / non-renderable format — opening the source" note (honest `no_controlled_rendition`).
- **No governing version (Draft/etc.):** a calm "No governing rendition yet" empty state (no download button).
- A transient presign failure is non-fatal (quiet retry — the existing `OverviewTab` `catch {}`).

### 6.2 The redline viewer (`useVersionDiff` + `RedlineViewer` + the Compare control)
The doc 11 §4.7 **Text-redline + Metadata-diff** modes (Side-by-side / visual = S-web-4b). URL-driven on the page
route so it is deep-linkable and shareable.

- **`useVersionDiff(documentId, toVid, fromVid, enabled)`** (`features/document/useVersionDiff.ts`) — a TanStack
  query over `GET /documents/{id}/versions/{toVid}/diff?from={fromVid}`, key `["version-diff", documentId, toVid,
  fromVid]`, `enabled` only when both ids are set. Returns `VersionDiff`. A **403** is an `ApiError(status=403)`
  → the viewer renders quiet "no access to the redline" (DP-6, the `HistoryTab` precedent), **never** an error
  banner.
- **The Compare control** (on the History card) — two version pickers (Mantine `Select`) populated from
  `useDocumentVersions`, defaulting **from = the prior version, to = the governing/newest** (the most common
  "what changed in the current rev"). Choosing a pair writes `?from=<vid>&to=<vid>` to the page URL
  (`useSearchParams`, the `LibraryPage` idiom); same-version or empty → no diff. doc 11 §4.6 "inline Compare."
- **`RedlineViewer.tsx`** (rendered when `from`+`to` are present; an in-page panel or Mantine `Modal` titled
  "Rev X → Rev Y"):
  - **Header** pins `from.revision_label → to.revision_label`, the **newer's `change_reason`**, and the
    author/state (from the `DiffProvenance` blocks). doc 11 §4.7.
  - **Metadata diff** — a table of the `metadata_diff` rows where `changed === true` (`field` · before → after),
    rendering `null`/absent as "—". (doc 11 §4.7 "Metadata diff" mode.)
  - **Text redline** — when `text_diff.status === "ok"`: render the `hunks` in order — `equal` = muted context,
    `insert` = additive (token color `--es-success` + underline + a leading **`+`** marker), `delete` =
    removed (`--es-error` + strike-through + a leading **`−`** marker). **Not color-only** (DP-7: the `+`/`−`
    markers + `ins`/`del` element semantics carry the meaning). When `status === "unavailable"`: a calm
    "Text redline unavailable — {reason}" with a **fallback** to per-version source download links (the §4.7
    side-by-side degrade; the visual diff that would cover binary docs is S-web-4b).
  - **Keyboard / a11y** (doc 11 §4.7 + §6.2): `n`/`p` jump to next/previous changed hunk; a navigable change
    **index** (a labelled list of changes) for screen-reader users; the viewer is a labelled region; focus
    managed on open; `prefers-reduced-motion` respected.
- **Scope of the entry:** the diff lives on the **page only** (the drawer's `HistoryTab` stays byte-identical —
  the engineering-patterns "keep the tested path unchanged" discipline). The drawer reaches it via §5's
  "⤢ Open full page."

### 6.3 The S-web-4b seam (visual page-image diff — not built here)
`RedlineViewer` leaves a clean seam for the deferred visual mode: a disabled/"Visual diff (coming soon)" slot or
simply its absence. S-web-4b adds `useVisualDiff` (POST request + the **GET-poll**, honouring the
"no `.delay` from a GET / 404-before-request" contract — engineering-patterns) + `VisualDiffViewer` (the from/to/diff
PNG layers via `…/visual-diff/page/{n}?layer=`, a page rail + changed-page markers + layer toggle). The backend
trio is built + contracted (§2) — S-web-4b is purely the async-polling + image-layer + canvas front-end.

## 7. Reused components & hooks — the reuse map (build on, don't rebuild)

| Existing (file) | Props / signature | Reuse in S-web-4 |
|---|---|---|
| `ArtifactHeader` (`features/document/ArtifactHeader.tsx`) | `{doc, typeName?, ownerName?}` | **Page header**, verbatim. |
| `StateBadge` (`…/StateBadge.tsx`) | `{state, size?}` | Header + tiles + history, verbatim. |
| `HistoryTab` (`…/HistoryTab.tsx`) | `{documentId, active}` | **Version-history card** content (`active=true`); **unchanged**. |
| `WhereUsedTab` (`…/WhereUsedTab.tsx`) | `{documentId, active}` | **Where-used card** content (`active=true`); unchanged. |
| `OverviewTab` (`…/OverviewTab.tsx`) | `{doc, typeName, ownerName}` | Source of the **`ControlMetadata`** extraction + the download idiom (see below); drawer keeps using it unchanged. |
| `AuthorActions` (`features/authoring/AuthorActions.tsx`) | `{doc}` | **Page header actions** (D-A), verbatim. |
| `useDocument` / `useDocumentVersions` / `useWhereUsed` | `(id, …)` | Page data, verbatim (same query keys). |
| `useDocumentTypes` / `useUserDirectory` / `usePermissions` | — | Name resolution + `can("document.export")`. |
| `useApi` / `ApiError` (`lib/api.ts`) | `.get/.send`, `status/code` | The diff/download fetches + error branching. |
| `DetailDrawer` (`app/shell/DetailDrawer.tsx`) | — | Gains the "⤢ Open full page" link (via `DocumentDrawer`). |
| `renderWithProviders` (`test/render.tsx`) | `{route, auth}` | Test harness (MemoryRouter + the `route` param). |

**One small refactor (test-backed parity):** extract the control-metadata table from `OverviewTab` into a
presentational **`ControlMetadata({doc, typeName, ownerName})`**, and extract the controlled-copy download into a
tiny shared helper/hook (`downloadControlledCopy(api, id)` or `useControlledCopyDownload`). `OverviewTab` then
renders `<ControlMetadata/>` + the download button — **byte-identical rendered output**, so its drawer behaviour
is unchanged (its existing tests are the regression backstop). The page reuses `ControlMetadata` in the metadata
card and the download helper in `RenditionCard`. (The engineering-patterns "build a new module, keep the old path
identical, prove parity" discipline applied at component scope.)

## 8. Testing & accessibility (the binding gates)

**Front-end (stack-free):** vitest + @testing-library/react + **MSW** + **jest-axe** (the S-web-1/2/3 idiom —
`renderWithProviders`, co-located `<Name>.test.tsx`, `onUnhandledRequest:"error"`). A routed page is tested by
rendering `<Routes><Route path="documents/:id" element={<DocumentDetailPage/>}/></Routes>` with
`renderWithProviders(…, {route: "/documents/<id>"})` (the `MemoryRouter` harness, `test/render.tsx:28`).

New **MSW handlers + fixtures** (`test/msw/handlers.ts` — extend, don't break existing tests): the existing
`GET /documents/:id` handler (`handlers.ts:268`) must return a fixture **with `capabilities`** (add to
`docFixture` or a detail variant); `GET /documents/:id/versions/:vid/diff` → a new `diffFixture` (a `text_diff.status:"ok"`
hunks case **and** an `"unavailable"` case **and** a 403 variant); `GET /documents/:id/download` → `{download_url,
content_type, rendition:"controlled_copy"}` (+ a `"source"` variant). Versions/where-used handlers already exist
(`:266-267`). Full-UUID ids, ISO timestamps, in lockstep with `lib/types.ts`.

**Cover:**
- **The page** (`DocumentDetailPage.test.tsx`): header renders identifier/title/state; tiles (governing rev,
  clauses); the rendition card; the two-column body; **loading skeleton**; **404 not-found**; **403 no-access**;
  `AuthorActions` gating (caps present → action cluster; `capabilities` absent / reader → none); the Export button
  hidden without `document.export`.
- **The redline** (`RedlineViewer.test.tsx` + `useVersionDiff.test.tsx`): the metadata-diff table (only `changed`
  rows); the text redline (insert = `+`/green/underline, delete = `−`/red/strike, equal = context — assert the
  **non-color markers**, not just color); the `text_diff:"unavailable"` fallback; the **403 → quiet** path; the
  Compare control writing `?from=&to=`; `n`/`p` keyboard nav + the change index.
- **The rendition card** (`RenditionCard.test.tsx`): `controlled_copy` open/download (assert `window.open` with
  the presigned URL); the `source` note; the no-effective empty state.
- **Parity:** `OverviewTab`'s existing test stays green after the `ControlMetadata` extraction; a `ControlMetadata`
  test asserts the field rows.
- **Breadcrumb:** `/documents/:id` renders the identifier (seeded via the query cache), not the UUID.
- **The drawer seam:** the "⤢ Open full page" link points at `/documents/:id`.

**jest-axe `toHaveNoViolations` is a release gate** (WCAG 2.2 AA) on: the **full page** (with and without the
author cluster), the **`RedlineViewer`** (the redline is a bespoke widget — labelled region, non-color-only
ins/del, keyboard nav, change index — doc 11 §6.2), and the **`RenditionCard`**. Status never color-only (the
`+`/`−` markers + `StateBadge` glyphs); visible non-obscured focus; target size ≥ 24px; `prefers-reduced-motion`.

**CI:** all five jobs green — `web` (eslint/tsc/build/test) does the real work; `contracts`/`api`/`integration`/
`migrations` are **no-ops** (run them to confirm zero drift; head stays `0044`).

## 9. Data flow & errors

```
route /documents/:id ──useParams──▶ DocumentDetailPage
  useDocument(id)            → header (ArtifactHeader) + AuthorActions(doc.capabilities) + metadata + tiles
  useDocumentTypes/Directory → friendly type/owner
  useDocumentVersions(id)    → history card + governing-rev tile + the Compare pickers   (read_draft; 403→quiet)
  useWhereUsed(id)           → where-used card
  GET /documents/{id}/download (rendition) → RenditionCard open/download                  (document.read)
  ?from&to (URL) → useVersionDiff(id, to, from) → RedlineViewer                           (read_draft; 403→quiet)
```
- **Read errors** branch on `ApiError.status`/`.code` (`lib/api.ts:7-15,44`): **403** on versions/diff →
  quiet "no access" (DP-6, never a dead UI); **404** on the document → "not found" empty state; a transient
  presign/diff failure → a scoped, recoverable inline message (doc 11 §4.9), the header still rendered.
- **No write calls are added by this slice** beyond what `AuthorActions` already issues (its errors are already
  handled inside it — `AuthorActions.tsx:13-15,63-67`).

## 10. Out of scope — and why (honesty over mockup-completeness)

- **Visual page-image diff** (the mockup's side-by-side / "Open full redline ⤢" visual mode) → **S-web-4b**
  (D-B). Backend built + contracted; the front-end is the async-polling + PNG-layer + canvas surface, isolated
  for full coverage.
- **Approvals tab + the approval stepper** (mockup) → **S-web-5**. A document → its active workflow-instance
  lookup does **not** exist (`submit-review` returns only the document; the version's `signatures` are reachable
  only via the diff's `DiffProvenance`, not a per-document approval timeline — `services/workflow/repository.py`
  is unexposed). The page renders the doc's own state (`InReview` "Awaiting review" via `AuthorActions`); a
  discovery endpoint + the stepper land with Review & Approve.
- **Acknowledgements** (mockup ring + Remind) → drift family (no acknowledgement data exists). The "Acknowledged"
  metric tile is omitted, not faked.
- **Audit tab** (mockup) → `system.audit_log.read` is SYSTEM-gated (admin, outside the QMS); a per-document
  audit-trail surface for ordinary readers is a separate decision.
- **Next-review / "Days to review" tile** → `next_review_due` is the drift family (not populated). Tile omitted.
- **Process-map lens switcher** ("View in Process Map") → the process IA UI is a later slice.
- **DCR-orchestrated "New revision (DCR)"** → the page uses the plain `start-revision` (via `AuthorActions`); the
  governed doc-05 DCR UI is later.
- **Embedded PDF.js rendition viewer** → D-C ships the card + open/download; inline rendering is a later enhancement.

## 11. Decisions log

- **D-A** The page **reuses `AuthorActions`** (capability + state + lock gated, DP-6 quiet-absence) for the Check
  out / Start revision / Submit / "Awaiting review" cluster — read-only for readers, authoring for authors. "New
  revision (DCR)" → plain `start-revision` (DCR UI deferred); "Export controlled copy" gated on coarse
  `document.export` (quiet-absent by default). Not strictly read-only — "read-only" meant no new approve/release flow.
- **D-B** Diff scope = the **synchronous text redline + metadata-diff** (`GET …/diff`), version-pair-selected,
  `document.read_draft` (403→quiet), URL-driven. The **worker-async visual page-image diff** → **S-web-4b**.
- **D-C** The rendition preview = a **card + Open/Download links** (`GET …/download`, `rendition` flag), **not**
  an embedded PDF.js viewer.
- **Re-sequence:** **S-web-4 = read-only Document detail page + the text/metadata redline** (this) · **S-web-4b =
  the visual page-image diff viewer** · **S-web-5 = Review & Approve (closes UJ-3)**.
- **Slice boundary:** **front-end only — no migration (head `0044`), no new permission key, no `openapi.yaml`
  change.** Every read exists and is already contracted; only the `web` CI job does real work.
- New code lives in **`features/document/`** (the page, `RenditionCard`, `ControlMetadata`, `useVersionDiff`,
  `RedlineViewer`); it reuses `features/authoring/AuthorActions` exactly as the drawer does (no new import cycle).
  One small test-backed `ControlMetadata`/download extraction from `OverviewTab` keeps the drawer byte-identical.
