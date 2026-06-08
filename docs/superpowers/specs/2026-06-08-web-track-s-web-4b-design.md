# EasySynQ Web-UI Track — S-web-4b Design (the worker-async visual page-image diff viewer)

> **Status:** Draft for owner review · **Date:** 2026-06-08 · **Owner:** CoJoA13
> **Work-stream:** the web-UI track, slice 4b — the tight follow-up S-web-4 carved out. Builds on **S-web-4**
> (PR #93 — the read-only Document detail page + the synchronous text/metadata redline: `useVersionDiff` +
> `RedlineViewer` + `VersionCompare`). Realizes the doc 11 §4.7 **Side-by-side / page-image (visual) mode** that
> S-web-4 (decision **D-B**) deferred. Like S-web-4 this is a **front-end-only** slice: **no migration, no new
> permission key, no `openapi.yaml` change** — the worker-async backend trio is fully built + already contracted
> (S-dcr-3a/3b). Reuses the `features/document/` components + the SPA architecture verbatim.

## 1. Context & the locked owner decisions

S-web-4 shipped the doc 11 §4.7 redline over the **synchronous** `GET …/diff` (metadata-diff table + inline text
redline), version-pair-selected on the History card, URL-driven (`?from=&to=`), gated `document.read_draft`
(403 → quiet). It explicitly left a clean seam for the **visual** mode (`RedlineViewer.tsx:14`,
`lib/types.ts:276`, `DocumentDetailPage.tsx:45`) and named the follow-up shape in §6.3: "`useVisualDiff` (POST
request + the **GET-poll**) + `VisualDiffViewer` (the from/to/diff PNG layers via `…/visual-diff/page/{n}?layer=`,
a page rail + changed-page markers + layer toggle)." This slice is exactly that. Three scoping decisions were
locked with the owner before this spec (the open calls the §6.3 seam left):

- **D-A — Layer UX = a *single image pane* + a from/to/diff *toggle*** (not side-by-side synced panes, not an
  onion-skin overlay). The page endpoint exposes exactly three discrete server-composed layers per page
  (`layer=from|to|diff`, the contracted enum); a radio/tablist toggle maps **1:1** to that contract with one
  fetch per selection, the smallest surface, and the best alt-text/screen-reader story. doc 11 §4.7 literally
  says "two synced-scroll **PDF.js** panes" for side-by-side, but the web track removed PDF.js (S-web-4 **D-C** —
  "no embedded viewer this slice"), and the **`diff` layer IS the server-composed overlay** (S-dcr-3b: pypdfium2
  rasterize + Pillow `ImageChops`), so the toggle delivers the comparative value of side-by-side/onion-skin
  without re-implementing synced-scroll or client-side canvas blending. The literal two-pane synced-scroll is a
  later enhancement if the owner wants it.
- **D-B — The Text | Visual switch lives in `VersionCompare`; the two viewers are *siblings*.** A Mantine
  `SegmentedControl` between the version pickers and the viewer swaps the **existing** `RedlineViewer` (Text) and
  a **new** `VisualDiffViewer` (Visual). `RedlineViewer` and its jest-axe-gated test stay **byte-identical** (the
  engineering-patterns "keep the tested path unchanged" discipline; S-web-4 spec §6.2). The mode is **URL-driven**
  (`?mode=visual`, added via `VersionCompare`'s existing param-preserving `setParams`), reusing the **same**
  `?from=&to=` pair — so a visual diff is deep-linkable/shareable exactly like the text redline.
- **D-C — Full viewer (the design-system bar), not a minimal single-layer image.** Ship the **changed-page rail**
  (the §4.7 "change minimap" *and* the §6.2 "navigable side index for screen-reader users", driven by
  `pages[].changed` with a **non-color** glyph+label), the **layer toggle**, **`n`/`p`** next/previous-changed-page
  keyboard nav, the **§4.9 phased async-poll** affordance, and a **jest-axe** gate on the new viewer. A minimal
  paged viewer would ship below §4.7 and fail the WCAG 2.2 AA / keyboard release gate.

## 2. What the API actually serves today (the grounding facts — all already contracted)

The visual-diff trio is built (S-dcr-3a/3b) and every shape is in `packages/contracts/openapi.yaml` **today** — so
**no contract change is needed**. The worker-async contract is the design-critic-mandated shape (the packs/imports
async precedent): **POST-compute (202) → pure GET-poll (no side effect, 404-before-POST) → a separate streaming
page sub-endpoint**. All three are gated **`document.read_draft`** (the diff exposes non-released/Draft content;
plain `document.read` is intentionally insufficient).

- **`POST /documents/{id}/versions/{vid}/visual-diff?from={vid2}`** (`documents.py:1412`; openapi `:1656-1703`) —
  **request/compute**. `status_code` default **202**; loads the document + both versions, calls
  `get_or_create_visual_diff` (`services/diff/visual.py:179` — `pg_insert(VisualDiff)…on_conflict_do_nothing` on
  `UNIQUE(from_version_id, to_version_id)`, the idempotency latch **and** the forever-cache key since versions are
  immutable), `.delay()`s the `easysynq.visual_diff` worker **only when** the row is (still) `Pending`, and flips
  the HTTP code to **200** when the row is already terminal. Returns a **`VisualDiffStatus`** body on both 202 and
  200. **Idempotent**: concurrent/repeat POSTs converge on one row and at most re-enqueue while Pending (re-driving
  a stalled render).
- **`GET /documents/{id}/versions/{vid}/visual-diff?from={vid2}`** (`documents.py:1442`; openapi `:1656-1703`) —
  **poll**. A pure `SELECT` (`scalar_one_or_none`), **never** `.delay`s — zero side effects. **404** (`not_found`,
  "No visual diff requested (POST to compute)") when the row is absent **or** belongs to another org (org
  isolation); **202** while `Pending`; **200** otherwise. Same `VisualDiffStatus` body. **404 here means "not
  requested yet," not "no such document"** — the frontend must POST first.
- **`GET /documents/{id}/versions/{vid}/visual-diff/page/{page}?layer=from|to|diff`** (`documents.py:1466`; openapi
  `:1705-1729`) — **streams the page PNG** (`Response(content=png, media_type="image/png")` — proxied through the
  authenticated API, **not** a presigned URL). `page` is a plain **0-based** integer index. `layer` defaults to
  **`diff`**. Error surface: **422** (`validation_error`, "layer must be from|to|diff") for an invalid layer
  (checked before any DB load); **404** ("Visual-diff page not available") when the row is missing / wrong-org /
  `status != Ready` / `pages` empty / `page` out of `[0, page_count-1]`; **404** ("No image for this page/layer")
  when the requested layer has no image for that page (an **added** page's `from` layer, a **removed** page's `to`
  layer — that side has no PNG). The **`diff`** layer always exists for a changed page.

**`VisualDiffStatus`** (openapi `:4860-4878`; helper `_visual_diff_status` at `documents.py:1397`; enum
`db/models/_dcr_enums.py:93`; model `db/models/visual_diff.py`):

```
{ status: "Pending" | "Ready" | "Failed" | "Unavailable",
  page_count: integer | null,            // null until Ready
  reason:     string  | null,            // null for Pending/Ready; set for Failed/Unavailable
  pages:      { page: integer, changed: boolean }[] | null }   // null until Ready; 0-based `page`
```

The DB row additionally stores `from_blob_sha`/`to_blob_sha`/`diff_blob_sha` per page (used internally by the page
stream); those sha fields are **server-internal**, never surfaced in the status JSON. To render a page the
frontend hits the **page sub-endpoint** per page+layer.

**Terminal-state semantics (what the viewer must handle calmly):**
- **`Ready`** — `page_count` + `pages[]` populated; render the rail + pane.
- **`Unavailable`** — a version is **non-renderable** (R26: encrypted/unsupported/structured-data source), so no
  page images exist. **NOT an error** — a terminal "no visual diff possible" state. The synchronous text/metadata
  redline (S-web-4) still covers the pair; degrade to a calm note + the §4.7 source-download fallback. Do **not**
  retry.
- **`Failed`** — defensive (a `from`/`to` `DocumentVersion` row vanished). **Terminal and NOT re-drivable** —
  `get_or_create_visual_diff` re-enqueues only a `Pending` row (`services/diff/visual.py:211`), so a re-POST of a
  Failed row just returns Failed forever; show a calm banner with `reason` + the source-download fallback and **no
  Retry** (a dead button — DP-6). Re-requesting a stalled render is offered on **`Pending` only**.
- **`Pending`** — render in progress. **⚠ In dev a Gotenberg/renderer outage leaves the row `Pending` forever**
  (a render timeout/503 maps to `RenderResult.pending()`, which the worker swallows by leaving the row Pending —
  it is **not** `Failed`/`Unavailable`). A re-POST re-enqueues a stalled `Pending` row. The viewer must poll
  patiently and offer a manual re-request rather than assume Pending always self-resolves.

**Honest v1 caveat (must surface in the copy):** the diff rasterizes the **watermarked controlled-copy
rendition**; the footer band differs by revision (label/effective-date/state), so the bottom footer region shows as
**"changed" on essentially every page**. The viewer copy must not over-claim page-level change precision —
"page-region differences (the footer/watermark band differs by revision)". Page images are ~144 dpi (scale 2.0);
the diff is **capped at 100 pages**.

## 3. Scope of S-web-4b

**In (front-end only):**
- **`useVisualDiff(documentId, toVid, fromVid, enabled)`** (`features/document/useVisualDiff.ts`) — the
  POST-trigger + GET-poll hook honouring the worker-async contract (§5).
- **A new authed-binary fetch helper** (`lib/api.ts` — `apiGetBlob` + `useApi().getBlob`) so an `<img>` can carry
  the bearer the page endpoint requires (§5).
- **`VisualDiffViewer.tsx`** (`features/document/`) — the single-pane from/to/diff viewer + the changed-page rail
  + the layer toggle + `n`/`p` nav + the §4.9 phased poll + the Failed/Unavailable/403 states (§6).
- **The Text | Visual mode switch in `VersionCompare.tsx`** — a `SegmentedControl` swapping `RedlineViewer` and
  `VisualDiffViewer`, `?mode=`-driven (§6.3). `RedlineViewer` stays untouched.
- **`VisualDiffStatus` (+ `VisualDiffPage`, `VisualDiffLayer`) types** in `lib/types.ts` (replace the `:276` seam
  comment).
- **A `URL.createObjectURL`/`revokeObjectURL` jsdom stub** in `test/setup.ts` (the matchMedia/ResizeObserver
  precedent) + new MSW handlers/fixtures.
- Full vitest + MSW + **jest-axe** coverage; WCAG 2.2 AA is the release gate.

**Out (deferred):** the literal §4.7 **two-pane synced-scroll** side-by-side (D-A — single-pane toggle this
slice); any **client-side onion-skin / canvas blend** (D-A); client-side **zoom/pan** beyond native browser image
zoom; a **Beat reaper** for stuck `Pending` rows (S-dcr-3b deliberately has none — re-POST self-heals; a reaper is
a backend decision, not this slice); raw (un-watermarked) render to kill the footer-band noise (**v1.x**, per
S-dcr-3b).

## 4. No backend change — the slice boundary (explicit)

Identical boundary to S-web-4 — **everything the viewer reads already exists and is already in `openapi.yaml`**:
- **`migrations` CI:** no-op (head stays `0044`).
- **`contracts` CI:** no-op (`/visual-diff`, `/visual-diff/page/{page}`, and `VisualDiffStatus` are documented at
  `openapi.yaml:1656-1703, 1705-1729, 4860-4878`). Run `redocly lint`; expect zero diff.
- **`api` / `integration` CI:** no-op (no Python touched).
- **`web` CI:** the whole slice (eslint / tsc / build / **test** incl. jest-axe).

No new permission key (the endpoints ride the existing `document.read_draft`).

## 5. Front-end — the hook + the authed-binary helper

### 5.1 `apiGetBlob` + `useApi().getBlob` (`lib/api.ts`)
The existing `request<T>` always does `resp.json()` (`api.ts:23-50`), so it cannot fetch the page PNG. Add a tiny
sibling that returns the raw bytes, mirroring the bearer-attach + RFC-9457 error branch:

```ts
export async function apiGetBlob(path: string, token: string | null): Promise<Blob> {
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  const resp = await fetch(path, { headers });
  if (!resp.ok) {                       // 403 → quiet, 404 → "no image for this page/layer", 422 → bad layer
    let problem: { code?: string; title?: string; detail?: string } = {};
    try { problem = await resp.json(); } catch { /* non-JSON */ }
    throw new ApiError(resp.status, problem.code ?? "error", problem.detail ?? problem.title ?? `HTTP ${resp.status}`);
  }
  return await resp.blob();
}
```
Expose it on the token-aware client: `useApi()` gains `getBlob: (path) => apiGetBlob(path, token)` (the token comes
from `useAuth()` exactly as `get`/`send` do — `api.ts:63-73`, `auth.tsx:70`). This is the **only** place the SPA
proxies authenticated binary bytes through the API (every other download is a presigned MinIO URL, D1) — a
deliberate, documented exception forced by the streamed-through-the-API page endpoint.

### 5.2 `useVisualDiff(documentId, toVid, fromVid, enabled)` (`features/document/useVisualDiff.ts`)
Honours the contract: **POST to request, GET to poll, never compute from a GET, never GET before the POST.** A
**mutation (POST trigger) + query (GET poll)** split keeps the side-effecting POST out of the cacheable poll query
(the engineering-patterns "pure GET-poll"):

- **POST trigger** — `useMutation({ mutationFn: () => api.send<VisualDiffStatus>("POST",
  `/api/v1/documents/${documentId}/versions/${toVid}/visual-diff?from=${fromVid}`) })`. Fired **once** per enabled
  distinct pair via a `useEffect` (deps: the three ids + `enabled`), and again on an explicit **Retry**. On
  success it **seeds the poll cache** (`queryClient.setQueryData(key, data)`) so the first poll never races the
  404-before-POST.
- **GET poll** — `useQuery({ queryKey: ["visual-diff", documentId, toVid, fromVid], queryFn: () =>
  api.get<VisualDiffStatus>(`/api/v1/documents/${documentId}/versions/${toVid}/visual-diff?from=${fromVid}`),
  enabled: enabled && <POST has run>, refetchInterval: (q) => q.state.data?.status === "Pending" ? 2500 : false })`
  — polls **only while `Pending`**, stops at any terminal status (the `SetupWizard.tsx:64-108` precedent).
- **Returned shape:** `{ status: VisualDiffStatus | undefined, isLoading, isError, error (ApiError — 403 quiet),
  retry() }`. The displayed status is read **from the pair-keyed poll cache** (`poll.data`, seeded by the POST's
  `onSuccess`) — **not** the unkeyed mutation result — so a version-pair change while the hook stays mounted never
  flashes the prior pair's pages or fires page requests for a not-yet-requested diff (the poll's `enabled` is
  likewise keyed: `active && qc.getQueryData(key)?.status === "Pending"`). `retry()` clears the cache + re-POSTs —
  for the **`Pending` "Re-request render"** affordance only (the dev renderer-off case); a terminal `Failed` row is
  not re-drivable, so retry is not offered there.
- **Enabled gating** matches `useVersionDiff`: only when `documentId && toVid && fromVid && toVid !== fromVid`
  **and** the visual mode is active (so switching to Visual is what triggers the POST — a Text-mode view never
  enqueues a render).

> Note `useVersionDiff`'s arg order is `(documentId, toVid, fromVid)` — TO before FROM even though the URL reads
> `?from=`. `useVisualDiff` mirrors that arg order for consistency.

## 6. Front-end — the `VisualDiffViewer` + the `VersionCompare` mode switch

### 6.1 `VisualDiffViewer.tsx` (`features/document/`) — props `{ documentId, fromVid, toVid }`
Uses `useVisualDiff`. State machine, all states calm (doc 11 §4.9):

- **403** (`ApiError.status === 403`) → quiet `Text c="dimmed"` "You don't have access to the visual diff." (DP-6,
  the `RedlineViewer.tsx:40-46` precedent). Never an error banner.
- **Pending** (`status.status === "Pending"`, or the POST in flight) → the **§4.9 long-op affordance**: skeletons
  **matching the final layout** (a rail skeleton column + a pane skeleton), a phase label ("Rendering page
  images…"), and an `aria-live="polite"` region announcing the phase — **never a frozen UI**; the Text/Metadata
  tab and the rest of the page stay interactive. The poll continues underneath. A manual "Re-request render"
  affordance covers the dev renderer-off "Pending forever" case.
- **Failed** (`status.status === "Failed"`) → a calm Mantine `Alert color="red"` inside the pane (not a takeover)
  stating `reason` + the **source-download fallback** — **no Retry** (a terminal Failed row can't be re-driven by a
  re-POST; only a `Pending` row re-enqueues). (§4.9 Error row, minus the dead action.)
- **Selected page resets on a pair change** — `picked` (the rail selection) is cleared in a `useEffect` keyed on
  `(documentId, fromVid, toVid)`, so a stale high page from a longer diff never points past the new pair's page
  list (an out-of-range request → a misleading 404).
- **Unavailable** (`status.status === "Unavailable"`) → a calm `Alert` (neutral/yellow) "Visual diff unavailable —
  {reason}" + the **source-download fallback** (per-version `…/versions/{vid}/download` → `window.open`, the
  `RedlineViewer.tsx:74-83` `openSource` idiom) — **not** an error. The text redline (other tab) still works.
- **Ready** (`status.status === "Ready"`) → the viewer:
  - **Changed-page rail** — a labelled, keyboard-navigable list (`role="listbox"`/buttons) of `pages[]`. Each
    entry shows the **1-based page label** ("Page 3", over the 0-based `page` index) and, when `changed`, a
    **non-color** marker (a glyph + the text "changed", per §6.2 "status never color-only (icon + label +
    position)"). This **single component doubles** as the §4.7 change minimap and the §4.7/§6.2 screen-reader
    change index. Selecting an entry sets the current page (local state).
  - **Pane** — an `<img>` of the **current page + current layer**, sourced via the §5.1 authed-binary helper:
    `getBlob(pageUrl).then(URL.createObjectURL)` → `<img src={objectUrl}>`, **revoking** the prior objectURL on
    page/layer change + unmount (no leak). Meaningful **alt text**: e.g. "Page 3 of 12 — diff layer (changed)".
    If the helper 404s ("No image for this page/layer" — an added page's `from`, a removed page's `to`), show a
    calm "No image on this side for this page" note rather than a broken image.
  - **Layer toggle** — a real radio/tablist (Mantine `SegmentedControl`) `from | to | diff`, **default `diff`**
    (the server-composed overlay). Roving focus, ≥24×24 px targets, visible focus (§6.2 Operable).
  - **Keyboard** — `n`/`p` jump to the **next/previous *changed* page** (the visual analogue of the text
    redline's `n`/`p` over changed hunks); the rail + toggle are fully keyboard operable; `prefers-reduced-motion`
    respected.
  - **Live region** — status transitions (Pending → Ready/Failed) and the current page/layer announced via an
    `aria-live` region (§6.2 Robust "live regions for async results").
  - **Honest copy** — a small note that footer/watermark-band differences may show as changed (the v1 caveat).

The page/layer selection is **local component view state**, not URL — the shareable unit is the version-pair +
mode (already in the URL); per-page/layer deep-linking is low value and would churn history. The mode itself is
URL-driven (§6.2).

### 6.2 The `VersionCompare` mode switch (`VersionCompare.tsx`)
`VersionCompare` already owns the `?from=&to=` pair and renders `<RedlineViewer>` when a distinct pair is selected
(`VersionCompare.tsx:39,69`). Add a `mode` param (`params.get("mode") ?? "text"`) and, **inside the `showViewer`
block**, a Mantine `SegmentedControl` `[{value:"text",label:"Text"},{value:"visual",label:"Visual"}]` above the
viewer, then branch:

```tsx
{showViewer && (
  <>
    <SegmentedControl value={mode} onChange={(m) => set("mode", m)} data={[…]} aria-label="Diff mode" />
    {mode === "visual"
      ? <VisualDiffViewer documentId={documentId} fromVid={from} toVid={to} />
      : <RedlineViewer    documentId={documentId} fromVid={from} toVid={to} />}
  </>
)}
```
`set("mode", value)` reuses the existing param-preserving functional `setParams` (`VersionCompare.tsx:31-37`), so
`?mode=` composes with `?from=&to=` without clobbering. `RedlineViewer` is imported + rendered exactly as today —
**byte-identical**; its test is the regression backstop. (The drawer's `HistoryTab` stays untouched — the diff is
page-only, the S-web-4 §6.2 discipline.)

## 7. Reused components & hooks — the reuse map (build on, don't rebuild)

| Existing (file) | Reuse in S-web-4b |
|---|---|
| `VersionCompare` (`features/document/VersionCompare.tsx`) | **Host the Text\|Visual switch** (`?mode=`); render `RedlineViewer`/`VisualDiffViewer` as siblings. |
| `RedlineViewer` (`…/RedlineViewer.tsx`) | The **Text** mode — **unchanged** (byte-identical; its test is the backstop). |
| `useVersionDiff` (`…/useVersionDiff.ts`) | The **pattern** `useVisualDiff` mirrors (arg order `(id, to, from)`, `enabled`, ApiError-403-quiet). |
| `useApi` / `ApiError` (`lib/api.ts`) | Extended with **`getBlob`** (§5.1); the POST/GET/page fetches + error branching. |
| `useAuth` (`lib/auth.tsx`) | The bearer for the authed-binary `<img>` fetch (token = `user?.access_token`). |
| `openSource` idiom (`RedlineViewer.tsx:74-83`) | The **Unavailable** source-download fallback (per-version `…/download` → `window.open`). |
| `SetupWizard` poll (`SetupWizard.tsx:64-108`) | The **`refetchInterval`-while-Pending, stop-at-terminal** poll pattern. |
| `StateBadge` / Mantine `SegmentedControl`/`Skeleton`/`Alert`/`Loader` | The mode switch, the rail markers, the phased-poll skeleton, the Failed/Unavailable banners. |
| `renderWithProviders` (`test/render.tsx`) | The test harness (MemoryRouter `route` + `TEST_AUTH` token `test-token`). |

**No new import cycle:** `VisualDiffViewer`/`useVisualDiff` live in `features/document/`; `VersionCompare` already
imports `RedlineViewer` from there. Nothing new is imported from `features/authoring/`.

## 8. Testing & accessibility (the binding gates)

**Front-end (stack-free):** vitest + @testing-library/react + **MSW** + **jest-axe** (the S-web-1…4 idiom —
`renderWithProviders`, co-located `<Name>.test.tsx`, `onUnhandledRequest:"error"`).

**Test-harness additions:**
- **`test/setup.ts`** — stub `URL.createObjectURL` (→ a fixed `"blob:mock"`) + `URL.revokeObjectURL` (no-op); jsdom
  implements neither, and the `<img>` viewer needs them (alongside the existing matchMedia/ResizeObserver/Blob
  stubs — `setup.ts:7-42`).
- **`test/msw/handlers.ts`** — extend (keep existing tests green). Add a `visualDiffFixture` (a `Ready`
  `VisualDiffStatus` with `page_count` + `pages[{page,changed}]`) and handlers: `POST
  /api/v1/documents/:id/versions/:vid/visual-diff` (200 `Ready` by default — tests override for the
  Pending→Ready, Failed, Unavailable, 403 cases), `GET …/visual-diff` (poll → `Ready`), and `GET
  …/visual-diff/page/:page` (a tiny 1×1 PNG `ArrayBuffer` via `new HttpResponse(bytes, {headers:{'Content-Type':
  'image/png'}})`; the handler can assert the `Authorization` header carried the bearer; an override returns 404
  for the added/removed-page no-image case). Full-UUID ids, in lockstep with `lib/types.ts`.

**Cover:**
- **`useVisualDiff.test.tsx`** — disabled (no POST) when the pair is missing/equal or mode≠visual; the
  **POST-then-poll** reaching `Ready` (POST returns Pending, GET poll returns Ready — assert it stops polling);
  the **Failed** path; the **Unavailable** path; the **403 → ApiError(403)**; **`retry()` re-POSTs** (assert a
  second POST after a Failed). renderHook with the MantineProvider+QueryClient+AuthContext wrapper (the
  `useVersionDiff.test.tsx` precedent).
- **`VisualDiffViewer.test.tsx`** — `Ready` renders the **rail** (changed pages carry the **non-color** marker,
  not just color) + the **pane `<img>`** (assert the authed `getBlob` fetch carried `Authorization`, the
  objectURL `<img src>`, and the **alt text**); the **layer toggle** switches the layer (assert the page endpoint
  re-fetched with `?layer=to`/`?layer=from`); the **no-image** note on a 404 layer; **`n`/`p`** jumps changed
  pages; the **Pending** skeleton + the `aria-live` phase label (a frozen UI is a fail); the **Failed** scoped
  banner + **Retry**; the **Unavailable** calm fallback (source-download `window.open`); the **403 → quiet**;
  **jest-axe** `toHaveNoViolations` on the **Ready**, **Pending**, **Failed**, and **Unavailable** renders.
- **`VersionCompare.test.tsx`** (extend, don't break) — the **Text | Visual** `SegmentedControl` renders when a
  distinct pair is selected; default `mode` = **text** (renders `RedlineViewer`); `?mode=visual` (and toggling)
  swaps to `VisualDiffViewer` and writes `?mode=` without clobbering `?from=&to=`; **jest-axe** with each mode.
- **`RedlineViewer.test.tsx`** — **unchanged** and **still green** (the byte-identity backstop).

**jest-axe `toHaveNoViolations` is a release gate** (WCAG 2.2 AA) on the **`VisualDiffViewer`** (each state) and
the extended **`VersionCompare`**. The bespoke widgets carry ARIA: the rail is a labelled list/listbox (the SR
change index), the layer toggle a labelled radio/tablist, every page `<img>` a meaningful `alt`, status
transitions announced via an `aria-live` region; status is **never color-only** (glyph + label + position);
visible non-obscured focus; targets ≥24×24 px; `prefers-reduced-motion` (§6.2). `eslint-jsx-a11y` also runs in the
`web` job.

**CI:** all five jobs green — `web` (eslint/tsc/build/test) does the real work; `contracts`/`api`/`integration`/
`migrations` are **no-ops** (run them to confirm zero drift; head stays `0044`).

## 9. Data flow & errors

```
VersionCompare (?from,&to,&mode)
  mode=text   → RedlineViewer (S-web-4, unchanged)                                   GET …/diff           (read_draft; 403→quiet)
  mode=visual → VisualDiffViewer
                  useVisualDiff(id, to, from, enabled)
                    POST …/visual-diff?from=  (idempotent; 202 Pending / 200 terminal)   ── trigger (once + Retry)
                    GET  …/visual-diff?from=  (poll; refetchInterval while Pending)        ── 404-before-POST guarded
                  per page+layer:  getBlob(GET …/visual-diff/page/{n}?layer=)  → blob → objectURL → <img>   (image/png)
```
- **Status branch** is on the JSON `status` field, **not** just the HTTP code (POST 202 vs 200 both carry the same
  body). **403** (versions/diff/visual-diff) → quiet "no access" (DP-6); **404** on the page endpoint with code
  "No image…" → the per-layer no-image note (a 404 here is **normal** for an added/removed page side); **422** (bad
  layer) is a programming error the UI never triggers (the toggle only emits `from|to|diff`). `Failed` →
  a calm source-download fallback (no Retry — a terminal Failed row isn't re-drivable); `Unavailable` → calm source-download fallback; `Pending` → the phased poll (with a manual
  re-request for the dev renderer-off case).
- **No write calls** beyond the idempotent `POST …/visual-diff` (a compute trigger, not a mutation of vault state).

## 10. Out of scope — and why

- **Two-pane synced-scroll side-by-side** (the literal §4.7 "PDF.js panes") → D-A ships the single-pane toggle;
  the web track removed PDF.js (S-web-4 D-C). A later enhancement if the owner wants literal side-by-side.
- **Onion-skin / canvas blend** → D-A; the `diff` layer is the server-composed overlay, so no client canvas is
  needed; canvas also forfeits honest per-layer alt text.
- **Client zoom/pan** beyond native browser image zoom → later polish.
- **A Beat reaper for stuck `Pending` rows** → S-dcr-3b deliberately has none (re-POST self-heals); a reaper is a
  backend decision, out of this front-end slice. The viewer offers a manual re-request instead.
- **Raw (un-watermarked) render** to remove the footer-band false-positive → **v1.x** (S-dcr-3b). This slice
  surfaces the caveat honestly in copy.

## 11. Decisions log

- **D-A** Layer UX = **single image pane + a from/to/diff toggle** (radio/tablist, default `diff`); render each
  layer via the **authed `fetch → blob → objectURL → <img>`** path (forced — the page endpoint is authenticated,
  not presigned, so a bare `<img src>` would 403). **Not** side-by-side synced-scroll (defers the literal §4.7
  PDF.js intent — PDF.js is out of the track), **not** onion-skin (no client canvas).
- **D-B** The **Text | Visual `SegmentedControl` lives in `VersionCompare`**; `RedlineViewer` (Text) and the new
  `VisualDiffViewer` (Visual) are **siblings**; `RedlineViewer` + its test stay **byte-identical**. Mode is
  **URL-driven** (`?mode=visual`), reusing the same `?from=&to=` pair. Page/layer selection is **local** view
  state.
- **D-C** **Full viewer** — changed-page rail (the §4.7 minimap **and** §6.2 SR change index, non-color
  glyph+label from `pages[].changed`) + layer toggle + `n`/`p` keyboard nav + the §4.9 phased poll + a jest-axe
  gate on the new viewer. Not a minimal single-layer image (below §4.7; would fail the a11y/keyboard gate).
- **Slice boundary:** **front-end only — no migration (head `0044`), no new permission key, no `openapi.yaml`
  change.** The worker-async trio (S-dcr-3a/3b) + the contract (`openapi.yaml:1656-1729, 4860-4878`) are fully
  built; only the `web` CI job does real work.
- New code lives in **`features/document/`** (`useVisualDiff`, `VisualDiffViewer`, the `VersionCompare` switch) +
  one **`lib/api.ts`** helper (`getBlob`) + the `lib/types.ts` `VisualDiffStatus` types + the `test/setup.ts`
  objectURL stub. No backend, no contract, no migration.
- **Post-review (Codex P2 ×3, PR #95):** (1) **No dead Retry on `Failed`** — the backend re-enqueues only a
  `Pending` row (`get_or_create_visual_diff` returns `should_enqueue = row.status is Pending`), so a terminal
  `Failed` row is not re-drivable; the viewer renders `Failed` as a calm terminal with the source-download fallback
  and the re-request affordance lives on `Pending` only. (2) **`useVisualDiff` is strictly pair-keyed** — `status`
  and the poll's `enabled` read the pair-keyed poll cache (seeded by the POST), never the unkeyed mutation result,
  so a version-pair change never flashes the prior pair's pages or fires page requests for a not-yet-requested
  diff. (3) **The viewer resets `picked`** in a `useEffect` keyed on the pair, so a stale page index from a longer
  diff never requests an out-of-range page (a misleading 404) on the new pair.
