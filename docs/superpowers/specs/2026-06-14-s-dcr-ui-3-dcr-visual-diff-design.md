# S-dcr-ui-3 — DCR page-image visual diff + redline (clause §10→§7.5 change control) — design

- **Date:** 2026-06-14
- **Slice:** `S-dcr-ui-3` — the **last named deferral** of the DCR-UI track. Surfaces, in the SPA, the page-image visual diff **and** the text/metadata redline of a DCR's **resulting version** against the version it supersedes — so a reviewer/auditor can *see* what a REVISE change actually altered. The DCR-UI track is otherwise complete and merged: ui-1 (read spine, #123 `916ce1e`), ui-2a (intake writes, #124 `3c07a0f`), ui-2b (lifecycle cockpit + `/tasks` approval leg + `_dcr_capabilities` enrichment, #127 squash `8ab8a31`, finish-slice docs #128 `168e8b6`).
- **Status:** owner-approved design (brainstorm 2026-06-14; the F1–F6 frame was settled WITH the owner after a five-reader source-verification fan-out — every claim below is checked against code, not slice-history narrative).
- **Significance:** closes the DCR-UI track. A reviewer driving a routed DCR through `assess→route→approve→implement→close` (ui-2b) can now, at Implemented/Closed, open the visual + redline diff of the change. Reuses the S-web-4b document diff machinery **verbatim** — no new backend.
- **Scope decision — 100% FRONT-END-ONLY.** No migration, no new permission key, no new endpoint, no contract change, no enum. Gate: **`/check-web` only**. diff-critic on the branch diff pre-PR; Chrome-MCP live smoke (the owner does the Keycloak login).
- **Doc grounding:** doc-05 (the change-control loop §10→§7.5) · doc-11 §4.6 (the inline "Compare" affordance; calm, progressively-disclosed UI) · R26 (non-renderable renditions → the viewer's `Unavailable` state) · the S-dcr-3b WORKER-ONLY render contract + the transient-rendition rule (`.claude/rules/engineering-patterns.md` "Blob / WORM / lifecycle invariants"). Web SPA testing rules: `.claude/rules/engineering-patterns.md` "Web SPA testing".

---

## s0 · As-built anchors (verified this session — pin MSW fixtures HERE, NOT narrative)

The five-reader fan-out resolved two agent **contradictions** by direct code reads; the resolutions are baked into the decisions below.

**Backend visual-diff (S-web-4b) — reusable for an arbitrary version pair, gated `document.read_draft`:**
- `db/models/visual_diff.py:36` — `VisualDiff` is uniquely keyed by **`UNIQUE(from_version_id, to_version_id)`** *only*; `document_id` is an FK/index, **not** part of the unique key → the cache is **not** document-sequence-bound. `pages` JSONB holds `[{page, changed, from_blob_sha, to_blob_sha, diff_blob_sha}]`; `status` ∈ `Pending|Ready|Failed|Unavailable` (`_dcr_enums.py:94-102`).
- `api/documents.py` — three endpoints, all gated **`document.read_draft`** (`_read_draft`, `:467`):
  - `POST /documents/{document_id}/versions/{version_id}/visual-diff?from={from_version_id}` (`:1690-1719`) — UPSERT the cache row + `.delay()` the worker; 202 Pending / 200 terminal.
  - `GET …/visual-diff` (`:1722-1743`) — side-effect-free poll (no `.delay()`); 404 before first POST, 202 Pending, 200 terminal.
  - `GET …/visual-diff/page/{page}?layer=from|to|diff` (`:1746-1782`) — streams one page's PNG; requires `status=Ready`.
  - `GET …/versions/{version_id}/diff?from={from_version_id}` (`:1658-1675`) — the **text/metadata redline** (frozen-snapshot metadata diff + on-demand Tika text + line-LCS); computed in-request (no cache); **also gated `document.read_draft`** (`:1669`).
  - ⚠ **`_load_version()` (`:1638-1644`) validates `version.document_id == path document_id`** → both `from` and `to` MUST belong to the path document. **For a REVISE DCR this is satisfied**: the resulting version and its predecessor are both versions of `target_document_id`. (Cross-document diffs would 404 — out of scope.)
- `services/diff/visual.py:75-120` — **`_ensure_rendition_pdf` resolves the F2 contradiction.** It cache-hits `rendition_blob_sha256` if set; **else it renders the source TRANSIENTLY** via the sink with `copy_status=version.version_state.value` (the state band in the footer), **no verify QR**, and **deliberately does NOT write `rendition_blob_sha256`** (the explicit comment, `:112-117`) — so a not-yet-Effective version (Draft/Approved, whose `rendition_blob_sha256` is `None`) **renders fine and cannot poison the mirror's controlled-copy cache**. It raises `_VisualUnavailable` **only** for a genuinely non-renderable MIME (R26 structured docs) or a vanished source blob. → **F2 needs no special handling; pre-cutover (Approved) and post-cutover (Effective) previews both work out of the box.**
- Render is WORKER-ONLY: the API holds the no-op `LoggingRenderSink`; `GotenbergRenderSink` is instantiated only in `tasks/visual_diff.py:34` / `tasks/mirror.py` / `cli/mirror.py`. Per-page PNGs cache to the **non-WORM** renditions bucket (`worm_locked=False`, `_cache_png`), regenerable, no purge wiring needed (document version source/rendition blobs are never deleted). **This slice touches none of it.**

**DCR serializer (`api/dcr.py`) — the FE has `resulting_version_id` + `target_document_id`, but NOT the predecessor:**
- `_dcr` (`:119-138`) serializes `resulting_version_id` (`:133`, `str|null`) and `target_document_id` (`:123`, `str|null`). `resulting_version_id` is `null` until **Implemented** (set at `services/dcr/service.py:892` for REVISE/CREATE; `null` for RETIRE). There is **no** predecessor / `from_version_id` field on the serializer.
- FSM (`domain/dcr/fsm.py:32-46`): `…→Approved→Implemented→Closed`. So `resulting_version_id != null` ⟺ DCR state ∈ `{Implemented, Closed}` (REVISE/CREATE only).

**FE — the document diff components are zero-coupling and immediately reusable:**
- `features/document/VisualDiffViewer.tsx` — props **`{documentId, fromVid, toVid}`** only (no route param, no `useDocument`). Drives `useVisualDiff` (POST-on-mount + GET-poll @2500ms while Pending) and streams page PNGs via the **authed-binary** `api.getBlob`→`URL.createObjectURL`/revoke (`lib/api.ts:61-75,91`) — the **only** API-proxied binary in the SPA. Calm 403 (`forbidden`), Failed/Unavailable fallbacks.
- `features/document/RedlineViewer.tsx` — props `{documentId, fromVid, toVid}`; `useVersionDiff` (synchronous GET); renders the metadata table + the inline text redline. Calm 403, unavailable-text fallback.
- `features/document/VersionCompare.tsx` — the document-detail composition: two version `Select`s (URL `?from=&to=`) **defaulting newest-vs-previous**, a `?mode=text|visual` `SegmentedControl`, and the two viewers below. **We reuse the toggle+viewer shape, NOT the picker** (the DCR pair is pinned).
- `features/document/useDocumentVersions.ts` — `useDocumentVersions(documentId, enabled)` → `DocumentVersion[]` (`GET /documents/{id}/versions`, gated `document.read_draft`). `DocumentVersion` (`lib/types.ts:81-96`) carries `version_seq` + `superseded_by_version_id` + `version_state` + `effective_from` — everything needed to resolve the predecessor client-side.
- `features/dcr/DcrDrawer.tsx:112-125` — the existing "Resulting version" `Field` (REVISE shows a "View document" link); this is where the new "View visual diff →" link attaches.
- `App.tsx:120-150` — route convention (`objectives/:id`, `management-reviews/:id`, `audits/:id` are sibling full-page routes); **no `/dcrs/:id` route exists yet** (only `/dcrs` register + `?dcr=` drawer). `DcrsRegisterPage` reopens the drawer from `?dcr=<id>` — the back-link target.

---

## s1 · Owner decisions (this session, 2026-06-14)

1. **F1 — FRONT-END-ONLY (reuse).** The visual-diff cache + the three endpoints + the redline endpoint already accept the `(predecessor, resulting)` pair for a REVISE DCR (both versions of `target_document_id` ⇒ the same-document endpoint validation passes). Reuse `VisualDiffViewer` + `RedlineViewer` + their hooks verbatim. No backend touch.
2. **F2 — no special handling (resolved by code).** The transient render path (`visual.py:112-117`) previews a not-yet-Effective resulting version without polluting `rendition_blob_sha256`. Show the diff whenever `resulting_version_id` is set, regardless of cutover.
3. **F3 — a dedicated full-page route `/dcrs/:id/diff`.** Not an in-drawer section. Rationale: the page-image viewer (changed-page rail + layer toggle + full-page image) is cramped in the 360–640px `DetailDrawer`; full routes drive cleanly in Chrome-MCP live-smoke whereas the drawer is flaky; a deep-linkable diff URL is auditor-friendly. The drawer carries the entry button; this is the first `/dcrs/:id/*` path.
4. **F4 — REVISE + Implemented/Closed.** The affordance + route content require `change_type === 'REVISE' && resulting_version_id != null`. CREATE (no client `version_id→document_id` resolution — the standing deferral) and RETIRE (no resulting version) get no affordance; a direct nav for those shows a calm "no visual diff for this change request".
5. **F5 — both visual + redline (mode toggle).** Reuse `RedlineViewer`/`useVersionDiff` alongside the page-image diff, parity with the document detail page, via a `?mode=text|visual` `SegmentedControl`.
6. **F6 — keep separate (diff-only slice).** The deferred impact-dimension annotation (`PUT /dcrs/{id}/impact`; needs widening `api.send` to add `PUT` + an editable `DcrImpactTable`) is **out of scope** — it's an unrelated lifecycle phase (annotation at ASSESS-time, gate `changeRequest.assess`; the diff is viewed at Implemented/Closed, gate `document.read_draft`) and would drag a global `api.send` change + a write path into a read-only visual-diff PR. Stays a named residual → its own tiny follow-up.

---

## s2 · Module changes — `apps/web/src/features/dcr/`

### `resolvePredecessor.ts` (new — a pure, unit-tested helper)
```
resolvePredecessor(versions: DocumentVersion[], resultingVersionId: string): { from: string; to: string } | null
```
- `to` = the version whose `id === resultingVersionId`. If absent from the list → return `null` (calm "no prior version").
- `from` (predecessor), in order of preference:
  1. **Succession link (exact):** the version with `superseded_by_version_id === resultingVersionId` (set at cutover; present post-cutover).
  2. **Seq predecessor (pre-cutover fallback):** the version with the **largest `version_seq` strictly less than** the resulting version's `version_seq`.
- No predecessor (resulting version is the first version — should not happen for REVISE, but guard it) → return `null`.
- Pure (no hooks, no I/O) → exhaustive unit tests (succession-link hit; seq fallback when the link is unset; resulting-not-in-list; no-predecessor; a later REVISE exists so resulting ≠ newest → still resolves to *its* predecessor, not newest-1).

### `DcrDiffPage.tsx` (new — route `/dcrs/:id/diff`)
- Reads `:id` (`useParams`) → `useDcr(id)`.
- **Header:** the DCR identifier (`dcr.identifier`) · `CHANGE_TYPE_LABEL[change_type]` · `DcrStateBadge` · a **back-link** `Anchor`→`/dcrs?dcr=:id` (reopens the drawer). One `h1` page title.
- **Eligibility gate (client, calm):** if `!dcr` → loading/`isError` calm states (the drawer precedent). If `dcr` is **not** `change_type==='REVISE'` **or** `resulting_version_id == null` → a calm panel "No visual diff for this change request" (CREATE/RETIRE/pre-implement). This guards a direct URL visit; the drawer only links eligible DCRs.
- **Version-pair resolution:** `useDocumentVersions(dcr.target_document_id, enabled = eligible)`. While loading → skeleton. On **403** (`forbidden` — the reviewer lacks `document.read_draft` on the target) → a calm "You don't have access to this document's versions" panel (the ui-1 target-degrade posture). On success → `resolvePredecessor(versions, dcr.resulting_version_id)`; `null` → calm "No prior version to compare against".
- **Diff body (when a pair resolves):** a `?mode=text|visual` `SegmentedControl` (URL state, mirroring `VersionCompare:75-85`; default `text`) over the two viewers, **pinned** to the resolved pair:
  - `mode==='visual'` → `<VisualDiffViewer documentId={target_document_id} fromVid={from} toVid={to} />`
  - else → `<RedlineViewer documentId={target_document_id} fromVid={from} toVid={to} />`
- The viewers self-handle their own Pending/Ready/Failed/Unavailable/403 states — a non-renderable structured-doc REVISE shows `Unavailable` in Visual while Text still works. No extra handling here.
- **a11y:** `h1`→`h2` heading order (jest-axe smoke); decorative `→`/arrows `aria-hidden`; the back-link + `SegmentedControl` carry distinct accessible names.

### `DcrDrawer.tsx` (edit — the "Resulting version" `Field`, `:112-125`)
- For a **REVISE** DCR with `resulting_version_id` set, add a **"View visual diff →"** `Anchor component={Link} to={`/dcrs/${dcr.id}/diff`}` inside the existing "Resulting version" field, alongside the existing "View document" link.
- Show-and-degrade: render the link regardless of the viewer's `document.read_draft` (its PROCESS scope isn't resolvable client-side — the ui-1 target-link posture); the diff page calm-degrades on 403. **Do not** render the link for CREATE (no `target_document_id`) or RETIRE (no `resulting_version_id`).

### `App.tsx` (edit)
- Add `<Route path="dcrs/:id/diff" element={<DcrDiffPage />} />` after the `dcrs` route (the `objectives/:id` convention). No nav entry (reached via the drawer / direct URL).

> **No new hook for the diff** — `useVisualDiff`/`useVersionDiff` (in `features/document/`) are imported as-is; `DcrDiffPage` only adds the page-level composition + `useDocumentVersions` for the predecessor. No mutation. No `lib/types.ts` change (every type already exists: `Dcr`/`DcrDetail`, `DocumentVersion`, `VisualDiffStatus`, `VersionDiff`).

---

## s3 · Error handling & gating

- **Route content gate:** `document.read_draft` **on the target document** (a *separate* key from `changeRequest.read`). Enforced server-side by `useDocumentVersions` + both diff endpoints. The FE never `can()`-gates the route (the gate's PROCESS scope isn't client-resolvable) — it **calm-degrades** to "no access" on 403, the ui-1 posture. A user who can read the DCR but not the target sees the calm panel, not a crash.
- **Eligibility:** non-REVISE / no resulting version → calm "no visual diff" (not an error).
- **Predecessor:** unresolvable → calm "no prior version".
- **Viewers:** Failed (terminal, non-retryable per the backend) / Unavailable (R26) / Pending (skeleton) all handled inside the reused viewers.
- **Loading:** skeletons; the **first content assertion in every test must `waitFor`** (the skeleton-frame false-PASS) — and the deep-link/navigate flake (`findByTestId("loc")` racing `navigate`) → always `waitFor(() => expect(getByTestId("loc")).toHaveTextContent(...))`.
- **XSS:** the reused viewers already render diff text safely; nothing new here.

---

## s4 · Testing

- `resolvePredecessor.test.ts` — pure unit: succession-link hit; seq fallback (link unset); resulting-not-in-list → null; no-predecessor → null; a later REVISE exists (resulting ≠ newest) → resolves to *its* predecessor.
- `DcrDiffPage.test.tsx` (MSW; routed via the test render wrapper with `:id`):
  - REVISE Implemented DCR → resolves the pair, default Text mode renders the redline; toggle Visual renders the page-image viewer (mock POST→Ready + a page PNG blob).
  - Predecessor resolution exercised through the page (succession-link **and** seq-fallback fixtures).
  - Calm states: non-REVISE/RETIRE → "no visual diff"; 403 on `…/versions` → "no access"; unresolvable predecessor → "no prior version".
  - Back-link → `/dcrs?dcr=:id` (assert via a `LocationProbe` + `waitFor`).
  - **jest-axe smoke** (heading order h1→h2 — the recurring real-bug catch).
- `DcrDrawer.test.tsx` (extend) — the "View visual diff" link: present for REVISE+`resulting_version_id`; absent for CREATE (no target) and RETIRE (no resulting version) and pre-implement (null `resulting_version_id`).
- **Conventions (mandatory):** `import { expect, it } from "vitest"` in every component test (the jest-dom×tsc trap); fixtures `satisfies DcrDetail`/`DocumentVersion`/`VisualDiffStatus`/`VersionDiff` pinned to the real serializers; MSW per-test overrides via `server.use(...)`; reuse `test/{render,setup}.tsx` and **mirror the existing `VisualDiffViewer` test's `URL.createObjectURL`/`revokeObjectURL` stub + the authed-PNG blob mock** (check `features/document/VisualDiffViewer.test.tsx` for the exact setup); `{open && <…/>}` only if a modal is involved (this slice is route-based, no modal).
- **Gate:** the **full `/check-web`** (eslint + strict `tsc --noEmit` + build + the whole vitest suite, run `--pool=forks --poolOptions.forks.singleFork=true` for a clean signal) before the PR — strict `noUncheckedIndexedAccess` + cross-file fixture drift are invisible to a per-file run.
- **Estimated delta:** ~18–24 web tests (823 → ~841–847).

---

## s5 · Live smoke (Chrome MCP, pre-merge; the owner does the Keycloak login)

1. **Rebuild the `web` image** (`… up -d --build web`) — `vite preview` serves a baked build, no source mount; hard-refresh / Incognito to drop the cached bundle.
2. **Grant `document.read_draft` + `changeRequest.read`** to the LIVE login's `app_user` row (org **AHT**), via the JIT-safe "assign to ALL org-AHT users" pattern (`scripts/grant-overrides.py`) — the live `demo` login JIT-maps to a *specific* `app_user` row.
3. **Pre-create an Implemented/Closed REVISE DCR with an Effective resulting version** — requires a real revision authored→approved→released on a target document, then a DCR raised→assessed→routed→approved→implemented against it (drive the early legs via the ui-2a/2b UI or a service-layer heredoc; complete the approval as the candidate approver). This gives the diff **both** sides (predecessor + resulting). Optionally a second DCR left at Implemented **pre-cutover** (effective-date in the future) to smoke the transient-render path.
4. **Verify:** the drawer's "View visual diff →" link appears on the eligible DCR (and is absent on a CREATE/RETIRE/Open DCR); the link lands on `/dcrs/:id/diff` (full-route nav, clean); the Text redline renders the metadata + text change; toggling Visual renders the page-image diff (changed-page rail + layer toggle); the back-link reopens the drawer; removing `document.read_draft` demonstrates the calm "no access" degrade.

---

## s6 · Out of scope (named, not faked)

- **CREATE-implement visual diff** — a CREATE DCR's `resulting_version_id` is on a *new* document whose id `_dcr` doesn't expose, and there is no top-level `GET /versions/{id}` to resolve `version_id→document_id` (confirmed). Needs a `resulting_document_id` enrichment or a version-lookup endpoint (a backend follow-up). RETIRE has no resulting version → N/A.
- **Impact-dimension annotation** (`PUT /dcrs/{id}/impact`) — F6: widen `api.send` to add `PUT`, add a `useAnnotateImpact` mutation, make `DcrImpactTable`'s annotation column editable (gated `changeRequest.assess`). A self-contained follow-up slice.
- **A `?version=` deep-link / a versions-table on the DCR** — the diff pins to the implemented pair; an arbitrary-version picker is the document-detail page's job, not the DCR's.
- **The `CapaApprovalContext` heading-order a11y fix** — a parked background-task chip from ui-2b (same h2→h4 bug fixed in `DcrApprovalContext`); not this slice.
