# S-web-6 — Global Search + Compliance Checklist — Design

> **Status:** DRAFT for owner review (2026-06-08).
> **Slice:** S-web-6 (web track). Surfaces the **S10 search/reporting backend** (PR #38) in the SPA.
> **Branch:** `feat/s-web-6-search-compliance`. **Migration head:** `0044` → **no new migration, no new
> permission key, no `openapi.yaml` change** — all three endpoints are already shipped + contracted.
> Builds on S-web-1 (shell + the non-functional ⌘K slot + `usePermissions`), S-web-2 (the Library +
> `ClauseTree` clause-filter deep-links + `StateBadge`), and S-web-4 (the `/documents/:id` detail page).
> Authoritative grounding: doc 13 (§2 unified search · §3.1 the Compliance Checklist view · §5.1 the
> ★ coverage center-hub), doc 02 §2.1 (the 20★ mandatory set, R30), doc 07 (the `report.*` gate), and
> the live code (`api/search.py`, `api/reports.py`, `services/search/indexer.py`,
> `services/reports/checklist.py`).

---

## 1. Goal & user journey

S-web-6 makes the **whole vault discoverable** and surfaces the **first honest QMS-health signal** in the
browser. After this slice:

1. **Any user** presses **⌘K** (or `/`) from anywhere → a command palette → types → jumps straight to a
   document, or opens a ranked **search results page** (`/search?q=`). The long-promised ⌘K slot (a
   non-functional stub since S-web-1) is now live.
2. **Mara (Quality Manager) / Ingrid (Internal Auditor)** open the **Compliance Checklist** (`/compliance`)
   → a RAG rollup + the 20★ mandatory-clause coverage table (COVERED / PARTIAL / GAP) → click a clause row
   to drill into the filtered Library.

Both surfaces are **permission-honest by construction**. Search **filters, never 403s** (doc 18 §5.2): a
caller who may read nothing gets `200` with empty results + a `hidden_by_scope` count ("N hidden by your
access scope"). The Compliance Checklist is **hard-gated** (`report.compliance_checklist.read`, SYSTEM) →
its nav entry is hidden for users who lack it, and a direct hit renders a calm no-access panel.

## 2. Decisions baked into this design (owner-approved 2026-06-08)

| # | Decision | Choice |
|---|----------|--------|
| 1 | **Search surface** | **Palette + results page.** A hand-rolled ⌘K command palette (live `/suggest` quick-jump) **and** a `/search?q=` ranked results page. Server facets / saved searches **deferred** (the API serves neither; the Library already does faceted browse). |
| 2 | **Compliance Checklist placement** | **Dedicated `/compliance` route** + a LeftRail entry **gated on `report.compliance_checklist.read`** (hidden if absent; 403-calm on direct hit). Clause rows deep-link to `/library?clause=N`. |
| 3 | **⌘K palette body** | **`/suggest` quick-jump + a "Search '<q>' →" footer action.** Lightweight type-ahead (identifier · title) for jump-to-doc; the footer opens the full results page. The palette does **not** duplicate the results-page row rendering. |

**Net shape:** front-end only. **No migration, no new permission key, no `openapi.yaml` change** — the slice
is pure presentation over already-contracted reads.

## 3. What already exists (the backend is 100% done — S10, PR #38)

All three endpoints are implemented (`apps/api/src/easysynq_api/api/{search,reports}.py`) and contracted in
`packages/contracts/openapi.yaml`. **This slice adds zero backend code.**

### 3.1 `GET /api/v1/search?q=&limit=` (tag `search`)
- **Auth-only entry; filters per-hit on `document.read` (deny-by-default), never 403s.** A stale/over-broad
  index can never over-disclose: candidates are re-validated against PostgreSQL per hit (`api/search.py:36`).
- **Scope of the index (MVP, R34 — Postgres-FTS):** the **metadata plane only** — identifier, title,
  legacy_identifier, area_code — over **`current_state = 'Effective'` DOCUMENTS only** (Records/other types
  and content-plane body text are not indexed). `ts_rank` weights identifier > title > legacy/area.
- **Response:**
  ```jsonc
  {
    "query": "calibration",
    "results": [
      { "type": "document", "id": "<uuid>", "identifier": "SOP-CAL-001",
        "title": "Calibration SOP", "current_state": "Effective",
        "clause_refs": ["7.1.5"], "snippet": "…<b>calibration</b>…", "rank": 0.61 }
    ],
    "hidden_by_scope": 3
  }
  ```
  `snippet` is PostgreSQL `ts_headline` output — matched terms wrapped in literal **`<b>…</b>`** (the FTS
  default). `limit` 1–100 (default 25). `q` `min_length=1`.

### 3.2 `GET /api/v1/search/suggest?q=&limit=` (tag `search`)
- Auth-only; same per-hit `document.read` post-filter (filter-not-403). Case-insensitive **prefix** over
  identifier/title (Effective documents only). `limit` 1–25 (default 10).
- **Response:** `{ "suggestions": [ { "id": "<uuid>", "identifier": "SOP-CAL-001", "title": "Calibration SOP" } ] }`

### 3.3 `GET /api/v1/reports/compliance-checklist` (tag `reports`)
- **Hard-gated `report.compliance_checklist.read`** (SYSTEM scope; held by **QMS Owner + Internal Auditor**
  per the 0021 backfill) → **403 for anyone else, including the demo System Administrator.** Computed from
  the authoritative `clause_mapping` join (never the index).
- **Response:**
  ```jsonc
  {
    "framework": "iso9001:2015",
    "rollup": { "total": 20, "covered": 17, "partial": 2, "gap": 1 },
    "rows": [
      { "clause_id": "<uuid>", "number": "8.4", "title": "External providers",
        "pdca_phase": "DO", "mapped_count": 0, "effective_count": 0, "status": "GAP" }
    ]
  }
  ```
  Per-clause `status` = **COVERED** (≥1 mapped doc has an Effective version) / **PARTIAL** (mapped, none
  Effective) / **GAP** (unmapped). Rows are pre-sorted by clause number. "Status against a rule, never an
  auto-compliance verdict" (doc 13 N9).

## 4. Front-end design (`apps/web`)

New code lives in **two feature folders** — `features/search/` and `features/compliance/` — plus thin shell
wiring. Dependency direction: `features/* → app/shell/* → lib/*` (acyclic; both features reuse
`features/document/StateBadge` and the existing `usePermissions`/`useApi` hooks).

### 4.1 `features/search/hooks.ts`
- `useSuggest(q: string)` → `useQuery(['search-suggest', q], GET /search/suggest?q=&limit=10)`, **`enabled:
  q.trim().length >= 1`**, debounced via a `useDebouncedValue(q, 150)` (`@mantine/hooks`) caller (the §2.7
  suggest P95 ≤ 150 ms budget). Returns `Suggestion[]`.
- `useSearch(q: string)` → `useQuery(['search', q], GET /search?q=&limit=25)`, `enabled: q.trim().length >=
  1`. Returns the full `SearchResults` envelope (results + `hidden_by_scope`).
- No new `lib/api` surface — both use `useApi().get`.

### 4.2 `features/search/CommandPalette.tsx`
- A hand-rolled Mantine **`Modal`** (no `@mantine/spotlight` dependency added) — `Modal` already supplies
  `role="dialog"`, focus-trap, Esc-to-close, and an overlay. Controlled `opened`/`onClose` from `AppShell`.
- A labelled `TextInput` (autofocus on open) bound to a local `q`. Below it, a **listbox** of `useSuggest(q)`
  rows (identifier · title) + a fixed **footer option** "Search '<q>' →".
- **Keyboard:** ↑/↓ move a highlighted index across `[…suggestions, searchAction]`; **Enter** activates the
  highlighted item — a suggestion navigates `/documents/:id`; the footer (or Enter on empty selection)
  navigates `/search?q=<encoded>`. Esc closes. Implemented with `aria-activedescendant` + `role="listbox"`/
  `role="option"` for SR support. Selecting **closes the palette and clears `q`**.
- Empty `q` → a calm hint ("Type to search documents"). `useSuggest` error → no dropdown (the footer still
  works). Loading → a small inline `Loader`.

### 4.3 `features/search/SearchResultsPage.tsx` (route `/search`)
- **URL-driven**: reads `q` from `useSearchParams()` (the S-web-2/3/4 URL-state discipline; a bookmarked/
  shared `/search?q=…` rehydrates). Calls `useSearch(q)`.
- States: **empty `q`** → "Type a query to search" prompt; **loading** → skeleton rows; **0 results** →
  calm "No matching documents" (+ the `hidden_by_scope` note if > 0); **results** → a list of
  `SearchResultRow`. A persistent footer renders `hidden_by_scope` ("N hidden by your access scope") when
  > 0, and a one-line **honesty hint**: "Searches title, identifier & clause refs — Effective documents
  only (body-text search is a later release)."

### 4.4 `features/search/SearchResultRow.tsx` + `Snippet.tsx`
- Row: `identifier` (mono) · `title` (link → `/documents/:id`) · `StateBadge` (reused; always "Effective"
  in v1 but rendered generically) · clause chips (`clause_refs[]` → each a link to `/library?clause=N`) ·
  the snippet.
- **`Snippet.tsx` — XSS-safe highlight.** It **parses** the `ts_headline` string by splitting on the literal
  `<b>` / `</b>` delimiters and renders matched segments inside Mantine `<Mark>` and everything else as plain
  text nodes. **No `dangerouslySetInnerHTML`** — any other markup (or a `<` in a title) renders as literal
  text, so the snippet can never inject HTML. A row with an empty snippet falls back to the title.

### 4.5 `features/compliance/useComplianceChecklist.ts`
- `useComplianceChecklist()` → `useQuery(['compliance-checklist'], GET /reports/compliance-checklist)`.
- A **403** is a first-class non-error outcome (the caller may lack the key): the hook surfaces a
  `forbidden` flag (inspect the thrown status) so `CompliancePage` renders a calm panel, **not** a crash/
  generic error. `retry: false` on 403 (don't hammer a permission denial).

### 4.6 `features/compliance/CompliancePage.tsx` (route `/compliance`)
- **403 / forbidden** → a calm "You don't have access to the Compliance Checklist" panel (DP-6; the nav entry
  is normally hidden, but a direct deep-link or a mid-session permission change can reach it).
- **Loaded:** a header **rollup** — total + COVERED/PARTIAL/GAP counts as a RAG summary (non-color: each
  count carries its own `CoverageBadge`) — then a **table** of the 20★ rows: clause number · title · PDCA
  phase chip · mapped/effective counts · `CoverageBadge`. Each row is a link → `/library?clause=<number>`
  (the §3.1 "each row deep-links into the filtered search"; reuses the existing Library clause filter).
- Loading → skeleton; the (pragma-only) empty-framework case → a calm "No framework configured" line.

### 4.7 `features/compliance/CoverageBadge.tsx`
- Maps COVERED/PARTIAL/GAP → a label + a **non-color glyph** (`✓` / `◔` / `✕`) + a token-driven hue
  (`--es-success` / `--es-warning` / `--es-danger`), mirroring `StateBadge`'s DP-7 discipline (the text
  label + glyph carry the meaning; color is the third, redundant channel). `aria-label="Coverage: <label>"`.

### 4.8 Shell wiring (modified files)
- **`app/shell/AppShell.tsx`** — owns the palette `opened` state, mounts `<CommandPalette>` once, and binds
  the open-hotkeys (`@mantine/hooks` `useHotkeys`). **⌘K / Ctrl-K must fire even while focus is in an input**
  (summon search from the Library box or a form), but **`/` must not hijack typing**. Mantine's `useHotkeys`
  applies a single `tagsToIgnore` to every binding, so these are **two separate `useHotkeys` calls**:
  `useHotkeys([['mod+K', open]], [])` (empty ignore-list → fires anywhere) and `useHotkeys([['/', open]])`
  (default ignore-list `['INPUT','TEXTAREA','SELECT']` + `triggerOnContentEditable=false` → no-ops while
  typing). When the palette is already open, both are harmless (the palette's own `TextInput` swallows the
  keystrokes / Esc closes).
- **`app/shell/TopBar.tsx`** — the disabled "Search (⌘K)" `TextInput` becomes a **button-styled affordance**
  (a read-only input or `UnstyledButton`) that calls `onOpenSearch`; it carries the ⌘K hint and an
  `aria-label`. (No longer `disabled`.)
- **`app/shell/LeftRail.tsx`** — a **gated** "Compliance" `NavLink` (to `/compliance`, ★ glyph), rendered
  only when `usePermissions().can("report.compliance_checklist.read")`. Placed after "Review & Approve",
  above the PDCA clause groups.
- **`App.tsx`** — two new routes under the `AppShell` layout: `/search` → `SearchResultsPage`, `/compliance`
  → `CompliancePage`.
- **`lib/types.ts`** — `SearchResults`, `SearchHit`, `Suggestion`, `ComplianceChecklist`, `ChecklistRow`,
  `ChecklistRollup` (mirroring the contracted shapes above).

## 5. Data flow (one diagram)

```
⌘K / "/"  ──► AppShell.opened=true ──► CommandPalette
   type q ─(debounce 150ms)─► useSuggest(q) ─► /suggest ─► listbox
   ↳ select suggestion ─► navigate(/documents/:id) + close
   ↳ footer / Enter-no-sel ─► navigate(/search?q=q) + close

/search?q=q ──► SearchResultsPage(useSearchParams) ─► useSearch(q) ─► /search
   ─► SearchResultRow[] (+ hidden_by_scope footer + honesty hint)
   ↳ title ─► /documents/:id    ↳ clause chip ─► /library?clause=N

LeftRail (gated) / deep-link ──► /compliance ─► useComplianceChecklist ─► /reports/compliance-checklist
   ─► rollup RAG + ★ rows     ↳ row ─► /library?clause=N     ↳ 403 ─► calm no-access panel
```

## 6. Error handling & edge cases

| Case | Behaviour |
|---|---|
| Search returns 0 results | Calm "No matching documents" + the `hidden_by_scope` note if > 0 (never an error). |
| All hits hidden by scope | `results: []`, `hidden_by_scope > 0` → "No matching documents" + "N hidden by your access scope". |
| Suggest network error | No dropdown; the "Search '<q>' →" footer still navigates. Never blocks typing. |
| Snippet contains markup / `<` | Rendered as **text** (only literal `<b>`/`</b>` become `<Mark>`); no HTML injection. |
| Empty / whitespace `q` | Hooks `enabled: false`; results page shows the "Type a query" prompt; palette shows the hint. |
| Compliance 403 (no key) | Calm no-access panel; nav entry already hidden for these users; `retry: false`. |
| Compliance network error | Standard calm error with a retry (distinct from the 403 no-access copy). |
| `/` pressed while typing | No-op (guarded) — never hijacks an input. |

## 7. Accessibility (WCAG 2.2 AA — release gate)

- **jest-axe `toHaveNoViolations`** on: the open `CommandPalette`, `SearchResultsPage` (with results **and**
  the empty state), and `CompliancePage` (with rows **and** the 403 panel).
- Palette: `Modal` dialog semantics + focus-trap + Esc; `role="listbox"`/`option` + `aria-activedescendant`
  for arrow-key navigation; the input is labelled.
- Non-color status everywhere (`StateBadge` + `CoverageBadge`: glyph + label + color).
- Keyboard: ⌘K / Ctrl-K / `/` open; ↑/↓/Enter/Esc in the palette; every result/clause/checklist row is a
  real link (Tab-reachable, Enter-activated).
- The `hidden_by_scope` count + honesty hint are plain text in the reading order (an `aria-live`-free static
  footer — it's not a live update).

## 8. Testing plan (vitest + MSW + jest-axe; the web CI job runs `npm test`)

- **`hooks.test.tsx`** — `useSuggest` enabled/disabled by `q` length; `useSearch` envelope shape; both call
  the right paths.
- **`CommandPalette.test.tsx`** — ⌘K opens (hotkey); typing populates the suggest list (MSW); select →
  navigates `/documents/:id`; footer / Enter-no-selection → `/search?q=`; Esc closes; arrow-key highlight;
  **axe** on the open palette.
- **`SearchResultsPage.test.tsx`** — URL-driven `q` (rehydrate from `?q=`); rows render; `hidden_by_scope`
  footer; honesty hint; 0-results state; the empty-`q` prompt; **Snippet** highlights `<b>` as `<mark>` and
  **renders a `<script>`-containing title as text** (the XSS guard); **axe** (results + empty).
- **`Snippet.test.tsx`** — parse/segment cases (single match, multiple, none, malformed/odd tags → literal).
- **`CompliancePage.test.tsx`** — rollup counts; ★ rows + `CoverageBadge` per status; row drill-through to
  `/library?clause=N`; **403 → calm no-access** panel (not a crash); **axe** (rows + 403).
- **`CoverageBadge.test.tsx`** — label + glyph + `aria-label` per status.
- **`LeftRail.test.tsx`** (extend) — the Compliance entry **shows** when `can(...)` is true and is **absent**
  when false (MSW `/me/permissions` with/without the key).
- **`TopBar.test.tsx`** (extend) — the search affordance is enabled and calls `onOpenSearch` (not `disabled`).
- **`App.test.tsx`** (extend) — `/search` + `/compliance` routes resolve under the shell.

Existing suites stay green (`StateBadge`, Library deep-links unchanged).

## 9. Honest deferrals (the backend can't yet serve these — stated, not faked)

- **Server-side facets** (the §2.3 six-facet rail: Type/Clause/Process/Status/Owner/Date) + facet counts —
  `/search` takes only `q`+`limit`. Faceted browse already lives in the **Library** (S-web-2). The search
  results page links out to `/library?clause=N` rather than re-implementing facets client-side over a capped
  candidate set (which would mislead).
- **Saved searches** (§2.6), **content-plane / body-text search** (§2.2 — needs the extracted-text pipeline),
  **non-Effective states** in search (needs `read_draft`/`read_obsolete` keys, §2.2), **People/Process**
  result groups, and the **revision-history `versions` index**.
- **Auditor exports** (§7 PDF/Excel) and the **Home center-hub ★ coverage tile** (§5.1 — the Home/PDCA
  dashboard is deferred until its acknowledgement/objective engines exist).
- **Compliance "overdue review" + "linked evidence" legs** (§3.1) — need `next_review_due` (drift family) +
  records; the checklist ships the **mapped/Effective** coverage only (the live backend's exact output).

## 10. Demo precondition & live smoke

- **The Compliance Checklist needs a QMS-Owner or Internal-Auditor login** — `report.compliance_checklist.read`
  is SYSTEM-scoped and **not** held by the demo System Administrator. For the live smoke: grant `demo` a
  **SYSTEM override** of `report.compliance_checklist.read` (the authoring-keys precedent), or sign in as a
  persona holding the QMS Owner / Internal Auditor role.
- **Search** works for any logged-in user (filter-not-403). The dev vault has Effective documents
  (SOP-PUR-002/003/004 from the S-web-5 smoke) to return real hits.
- **Browser-automation note:** in-memory tokens mean a full navigation re-auths to `/`; move between
  `/search` / `/compliance` via client-side nav (the ⌘K palette, LeftRail links) to keep the session.
```
