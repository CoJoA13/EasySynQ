# S-web-7c — Complaint & NCR intake (slice design)

> **Status:** approved (owner, 2026-06-09). **Track:** web-UI. **Epic:** S-web-7 Nonconformity & CAPA
> front door (`docs/superpowers/specs/2026-06-08-web-track-s-web-7-nc-capa-design.md`), PR 3 of 4.
> **Depends on:** 7a (read spine, #101) + 7b (lifecycle writes, #102) — both shipped. **Self-contained:**
> no audit dependency. **Closes:** the complaint/NCR intake front doors that feed the 7a CAPA board.

## 1. Why / what

The CAPA board (7a/7b) is the ACT-phase destination, but two of its three real-world inlets have no UI:
**customer complaints** (R16 — a lightweight record subtype that can spawn a CAPA) and **NCRs**
(ISO 9001 §8.7 nonconforming output, with a one-shot disposition decision). The backend for both is
already built **and contracted** (`apps/api/.../api/capa.py:493-617`; `packages/contracts/openapi.yaml`
`/complaints*` + `/ncrs*`). This slice surfaces them in the SPA:

- **Complaints** — list · log (create) · **one-click spawn-CAPA** (idempotent; the spawned CAPA lands on
  the 7a board).
- **NCRs** — list · raise (create) · **record disposition** (one-shot ISO 8.7 decision).

**Front-end only.** No migration, no new permission key, no contract change. The four endpoints, their
request bodies, and the `Complaint`/`Ncr` response schemas all already exist.

## 2. Architecture — a tabbed front door under `/capa`

Keep the single "Nonconformity & CAPA" nav entry; add a **layout route** that wraps the three faces so
the shipped board stays byte-identical:

```
<Route path="capa" element={<CapaLayout/>}>          # renders <CapaTabs/> + <Outlet/>
  <Route index             element={<CapaBoardPage/>}/>     # UNCHANGED (file + tests untouched)
  <Route path="complaints" element={<ComplaintsPage/>}/>
  <Route path="ncrs"       element={<NcrsPage/>}/>
</Route>
```

- `CapaLayout` renders a **secondary tab strip** (Board · Complaints · NCRs; active tab derived from
  `useLocation().pathname`) above `<Outlet/>`. It renders **no title** — each page keeps its own — so
  `CapaBoardPage.tsx` and `CapaBoardPage.test.tsx` are not touched (the board keeps its "Nonconformity &
  CAPA" title + Raise/Board-List chrome verbatim).
- `LeftRail` is unchanged: one entry → `/capa`. Each tab is deep-linkable (`/capa/complaints`,
  `/capa/ncrs`).
- Tabs link via `react-router` `Link`/`NavLink` (or Mantine `Tabs` wired to `useNavigate`); active state
  from the pathname so a deep-link or a back/forward lands on the right tab.

**Rejected alternatives** (owner picked tabbed sub-routes): local-state tabs on a single `/capa` route
(not deep-linkable; restructures the board page to host the tabs), and separate top-level routes +
LeftRail entries (`/complaints`, `/ncrs` — diverges from the mockup's single front door).

## 3. Verified backend surface (pin every fixture to THESE shapes)

All citations `apps/api/src/easysynq_api/api/capa.py` unless noted. The serializers are the runtime truth
— pin MSW fixtures to them, never the mockup (the #1 false-PASS lesson from S-web-6 / S-ing-4b / 7b).

### 3.1 Complaints

- **`GET /complaints`** (`:514`) → **`{"data": Complaint[]}`** — flat, org-scoped, no server filters.
  Gate `record.read` (`_complaint_read`, `:294`; default SYSTEM scope + org query).
- **`POST /complaints`** (`:496`) ← `ComplaintCreate {description* (1..4000), customer? (≤300),
  received_at? (datetime), channel? (≤100), severity? (NcSeverity)}` → 201 + `Complaint`. Gate
  `record.create` (`_complaint_create`, `:295`; **default SYSTEM scope** — a complaint is an ad-hoc
  record, no process).
- **`POST /complaints/{id}/spawn-capa`** (`:535`) ← `SpawnCapa {severity? (NcSeverity), process_id? (uuid)}`
  → **201 (new) / 200 (idempotent replay)**, body = the full CAPA (`_capa_full`: `id · identifier ·
  title · …`, no `stages`). Gate `capa.create` (in-handler `enforce`, scope from body `process_id`,
  SYSTEM fallback). Idempotent via the complaint's `spawned_capa_id` latch.
- **`_complaint` serializer** (`:217`): `id · identifier (string|null — REC-style, may be null) · customer
  (string|null) · received_at (string|null, ISO) · channel (string|null) · description (string) ·
  severity (NcSeverity|null) · spawned_capa_id (string|null)`.

### 3.2 NCRs

- **`GET /ncrs`** (`:583`) → **`{"data": Ncr[]}`** — flat, org-scoped, no server filters. Gate `ncr.read`
  (`_ncr_read`, `:293`).
- **`POST /ncrs`** (`:561`) ← `NcrCreate {source* (NcrSource), description* (1..4000), severity*
  (NcSeverity), process_id? (uuid)}` → 201 + `Ncr`. Gate `ncr.create` (in-handler `enforce`, scope from
  body `process_id`, SYSTEM fallback).
- **`PATCH /ncrs/{id}/disposition`** (`:604`) ← `NcrDispositionBody {disposition* (NcrDisposition),
  notes? (≤2000)}` → 200 + `Ncr`. Gate `ncr.record_correction` (`_ncr_disposition`, `:302`; scope from
  the NCR row's `process_id`, SYSTEM fallback). **One-shot** — `409 ncr_already_dispositioned` if a
  disposition is already set (`openapi.yaml:3997`).
- **`_ncr` serializer** (`:230`): `id · identifier (string, NCR-NNN — `ncr.identifier` is `nullable=False`)
  · source (NcrSource) · description (string) · severity (NcSeverity) · process_id (string|null) ·
  disposition (NcrDisposition|null) · disposition_authorized_by (string|null, user id) · disposition_notes
  (string|null) · disposed_at (string|null, ISO) · created_at (string, ISO)`.

### 3.3 Enums (from `db/models/_capa_enums.py`)

- `NcSeverity` = `Critical | Major | Minor` (title-case).
- `NcrSource` = `audit | process | complaint | internal` (note: NCR adds `internal`, has no
  `review_output`).
- `NcrDisposition` = `use_as_is | rework | scrap | return | concession | regrade` (lowercase; canonical
  token for the `RETURN_` Python member is `return`).

### 3.4 Permission keys + who holds them (`migrations/versions/0004_seed_authz.py`)

The load-bearing gating facts (verified against `_SYSTEM_KEYS` + the role tuples):

| Surface | Read key | Write key(s) | `demo` (System Admin) holds? | Seeded role holders |
|---------|----------|--------------|------------------------------|---------------------|
| Complaints | `record.read` | create=`record.create`, spawn=`capa.create` | **No** (none of these) | `record.read`: QMS-Owner, Process-Owner, Auditor, Employee; `record.create`: Process-Owner, Author; `capa.create`: Process-Owner |
| NCRs | `ncr.read` | create=`ncr.create`, dispose=`ncr.record_correction` | **No** | `ncr.read`: QMS-Owner, Internal-Auditor; **`ncr.create` + `ncr.record_correction`: NO seeded role** (SYSTEM-override-only in v1) |

Consequences:
- **`demo` sees a calm no-access panel on BOTH tabs** (it holds none of these keys — the S-web-6
  compliance/calm-403 case, like the 7a/7b board). For the live smoke, grant `demo` SYSTEM overrides of
  `record.read record.create capa.create ncr.read ncr.create ncr.record_correction` (org `AHT`) — one
  admin drives the whole loop (no SoD on complaints/NCRs/spawn).
- `ncr.create` / `ncr.record_correction` reach no concrete object in v1 (no role binds them) — the
  "Authz for not-yet-UI'd domains: ride SYSTEM overrides" pattern.

## 4. Components (all new, under `apps/web/src/features/capa/`)

| File | Role |
|------|------|
| `CapaLayout.tsx` | secondary tab strip (Board · Complaints · NCRs) + `<Outlet/>`; active from pathname |
| `ComplaintsPage.tsx` | title + "Log complaint" btn (gated `record.create`) + complaints table; calm-403 on `record.read` deny; per-row spawn/view-CAPA action |
| `ComplaintForm.tsx` | create modal: `description*` · customer · received_at · channel · severity |
| `NcrsPage.tsx` | title + "Raise NCR" btn (gated `ncr.create`) + NCR table; calm-403 on `ncr.read` deny; per-row disposition action (gated `ncr.record_correction`) |
| `NcrForm.tsx` | create modal: `source*` · `description*` · `severity*` (process_id omitted — owner-assignment deferred, the 7b Raise-scope decision) |
| `DispositionModal.tsx` | one-shot PATCH modal: `disposition*` (6 labelled values) · notes |

Hooks + mutations extend the existing `features/capa/hooks.ts` and `mutations.ts` (same
`useQuery`+`forbidden`-flag and `useMutation`+invalidate idioms as 7a/7b).

### 4.1 Hooks (`hooks.ts`)

- `useComplaints()` → `GET /complaints` → `{data}`; `forbidden` flag (403 → calm); `retry:false`.
- `useNcrs()` → `GET /ncrs` → `{data}`; `forbidden` flag; `retry:false`.
- No detail hooks — `GET /{id}` returns the same shape as the list row, so list rows are complete.

### 4.2 Mutations (`mutations.ts`)

- `useCreateComplaint()` → `POST /complaints`; invalidate `["complaints"]`.
- `useSpawnCapa()` → `POST /complaints/{id}/spawn-capa` `{severity?}`; invalidate `["complaints"]` +
  `["capas"]`; returns the CAPA (201/200 both resolve).
- `useCreateNcr()` → `POST /ncrs`; invalidate `["ncrs"]`.
- `useNcrDisposition(id)` → `PATCH /ncrs/{id}/disposition` `{disposition, notes?}`; invalidate `["ncrs"]`.

## 5. The two write flows

### 5.1 Spawn-CAPA (idempotent — surfaced as state, never an error)

The `_complaint` row carries `spawned_capa_id`. The per-row action reads:
- **"Spawn CAPA"** when `spawned_capa_id == null` → calls `useSpawnCapa()` (inheriting the complaint's
  severity; no process picker). On success: invalidate, and surface the returned CAPA identifier inline
  ("Created CAPA-NNN").
- **"View CAPA"** (→ `/capa`) when `spawned_capa_id != null`.

A racing `200` replay (double-submit) resolves normally — we never read the HTTP status (`api.send`
discards it and we are not changing the api lib; `spawned_capa_id` is the source of truth). After a
spawn, the invalidated list row shows `spawned_capa_id` set → the button flips to "View CAPA". "View
CAPA" navigates to `/capa` (the board has no URL-driven drawer in v1, so we don't deep-link a card; the
new CAPA is the newest row).

### 5.2 NCR disposition (one-shot ISO 8.7)

- An **undisposed** NCR (`disposition == null`) shows a "Record disposition" action (gated
  `ncr.record_correction`) → `DispositionModal`: a `Select` over the 6 dispositions + an optional notes
  `Textarea` → PATCH.
- A **disposed** NCR renders its disposition + authorizer + notes + `disposed_at` **read-only**, with NO
  action button (the one-shot is structural in the UI, not just server-enforced).
- A `409 ncr_already_dispositioned` (a race) → a calm inline `Alert` in the modal + a refetch, never a
  crash.

## 6. Cross-cutting decisions (inherited from the epic §5)

1. **Fixtures pinned to §3.** `{data:[]}` envelopes; the exact `_complaint`/`_ncr` fields; enum unions per
   §3.3. Verified against `apps/api`, not the mockup.
2. **Gating** via `usePermissions().can(key)` at **SYSTEM** scope (default) — the 7b precedent; correct
   because v1 grants for these keys are SYSTEM-overrides (process binding deferred). Don't render a write
   affordance the caller can't exercise.
3. **One-shot / idempotency are server truths, surfaced calmly** — `409 ncr_already_dispositioned`
   inline; spawn replay silent.
4. **Calm-403 / no-access** per face (the compliance-checklist precedent): the read hook's `forbidden`
   flag → a calm panel naming the missing key/roles; the tab strip + nav entry stay discoverable.
5. **Honest affordances only.** No faked filters/tiles without backing data. **Filters and summary
   counts are DEFERRED** for this slice (YAGNI): intake lists are org-scoped + bounded and the tables are
   scannable; a filter/tile bar adds surface without a demonstrated need. Revisit if volume grows. (This
   keeps the two pages to: title + create button + table + row actions.)
6. **Free-form / user text rendered as text** — XSS-safe via Mantine `Text` (no
   `dangerouslySetInnerHTML`); a description containing HTML renders literally (the S-web-6 lesson).

## 7. Types (`apps/web/src/lib/types.ts`) — extend the S-web-7 block

```ts
export type NcrSource = "audit" | "process" | "complaint" | "internal";
export type NcrDisposition =
  | "use_as_is" | "rework" | "scrap" | "return" | "concession" | "regrade";

export interface Complaint {
  id: string;
  identifier: string | null;
  customer: string | null;
  received_at: string | null;
  channel: string | null;
  description: string;
  severity: NcSeverity | null;
  spawned_capa_id: string | null;
}
export interface ComplaintList { data: Complaint[]; }

export interface Ncr {
  id: string;
  identifier: string;            // NCR-NNN, non-null (ncr.identifier nullable=False)
  source: NcrSource;
  description: string;
  severity: NcSeverity;
  process_id: string | null;
  disposition: NcrDisposition | null;
  disposition_authorized_by: string | null;
  disposition_notes: string | null;
  disposed_at: string | null;
  created_at: string;
}
export interface NcrList { data: Ncr[]; }

// request bodies
export interface ComplaintCreateBody {
  description: string; customer?: string; received_at?: string;
  channel?: string; severity?: NcSeverity;
}
export interface SpawnCapaBody { severity?: NcSeverity; process_id?: string; }
export interface NcrCreateBody {
  source: NcrSource; description: string; severity: NcSeverity; process_id?: string;
}
export interface NcrDispositionBody { disposition: NcrDisposition; notes?: string; }
```

Plus display-label maps (e.g. `NCR_SOURCE_LABEL`, `DISPOSITION_LABEL` = `{use_as_is:"Use as-is",
rework:"Rework", scrap:"Scrap", return:"Return to supplier", concession:"Concession",
regrade:"Regrade"}`).

## 8. Testing (vitest + MSW + jest-axe; fixtures pinned to §3)

- **Tab nav** — `CapaLayout` renders 3 tabs; active reflects the route; navigating switches the `<Outlet/>`
  content (board / complaints / NCRs).
- **Complaints** — list renders rows from `{data}`; "Log complaint" shown/hidden by `record.create`;
  create modal POSTs + invalidates; spawn button shows only when `spawned_capa_id == null`, flips to
  "View CAPA" after spawn; calm-403 panel when `record.read` denied; jest-axe.
- **NCRs** — list renders + source/disposition friendly labels; "Raise NCR" shown/hidden by `ncr.create`;
  create modal POSTs; disposition modal PATCHes; a **disposed** row is read-only (no action); a `409
  ncr_already_dispositioned` surfaces calmly; calm-403 when `ncr.read` denied; jest-axe.
- **XSS-safe** — a complaint/NCR `description` containing `<script>`/`<b>` renders as literal text.
- Run the full **`/check-web`** (eslint + strict `tsc --noEmit` + build + the whole vitest suite) before
  the PR — the full run catches cross-file drift the per-file runs miss (the `noUncheckedIndexedAccess`
  lesson).

## 9. Out of scope

- Complaint/NCR **detail** drawers/pages (the `GET /{id}` endpoints add nothing over the list row).
- Net-new **evidence upload / record capture** from these surfaces (records surface owns that).
- The board header's **cross-counts** ("Complaint · 3 / NCR · 2" in the mockup) — would force the board
  to fetch complaints/NCRs under different keys (and 403 differently), coupling + un-byte-identical-ing
  the board. Dropped.
- Any backend change (migration / key / endpoint / contract).
- Process picker on create/spawn (owner-assignment binding deferred — the 7b Raise-scope decision).

## 10. Risks / watch-items

- **Wrong-shape fixture** (the recurring false-PASS) → §3 pins to the serializers; verify vs `apps/api`.
- **Per-key gating divergence** → complaints and NCRs ride DIFFERENT keys than the board; `demo` holds
  none → both calm-403. Gate each affordance on its own key at SYSTEM scope; don't assume "admin sees it".
- **Spawn idempotency** → drive the button from `spawned_capa_id`, not the HTTP status (which `api.send`
  discards); replay is silent.
- **Board byte-identical** → the layout route must not alter `CapaBoardPage` or its tests (the tab strip
  lives in `CapaLayout`, the board keeps its own title).
