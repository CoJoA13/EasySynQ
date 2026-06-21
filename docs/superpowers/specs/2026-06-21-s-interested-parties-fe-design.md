# S-interested-parties-fe — the clause-4.2 Interested Parties SPA (design record)

> Status: approved (owner, 2026-06-21). FE-only. The LAST residual of the clause-4.2 Interested
> Parties register family (R51) — after this slice R51 is COMPLETE end-to-end. No new R-number
> (R51 covers the family).

## What this is

The `/interested-parties` SPA — a `features/interested-parties/` dir mirroring `features/context/`
(the S-context-fe template), adapted to the clause-4.2 content model. The whole backend already
shipped: CRUD + the register lifecycle (start-revision/publish/release) + the server-computed
`can_release`/`can_manage` caps + `GET /interested-parties/summary` (S-interested-parties-1 / -2).
So this slice is **FE-only**: NO migration (head stays `0061`), NO new permission key (rides
`register.read` / `register.manage` / `document.release`, catalog 102), NO contract change — every
schema (`InterestedParty`, `InterestedPartyRegisterStatus` [+ `can_release`/`can_manage`],
`InterestedPartyRegisterSummary`, the create/update/publish bodies) is already in
`packages/contracts/openapi.yaml`.

## Data-model deltas from `features/context/` (the only real adaptation surface)

| context (template) | interested-parties |
|---|---|
| `classification` — 2-way internal/external (NOT NULL) | `party_type` — 7-way ISO-4.2 spine (customer/regulator/supplier/employee/owner/community/partner; NOT NULL) |
| `description` — one text field | `party_name` (the anchor) **+** `needs_expectations` (the body) — **two** text fields |
| `category` — nullable SWOT (4-way categorical) | `influence` — nullable **ordered** axis (low/medium/high) |
| SWOT 2×2 board + an `uncategorized` overflow strip | **7-card party-type board, NO overflow** (party_type is NOT NULL → every row buckets) |
| `status` active/closed; `last_reviewed_at` | identical |

Org-level (clause 4.2 is strategic/org-wide, like 4.1) → **no process picker**; the SYSTEM-scoped
gating story is byte-identical to context (filter-not-403 list; `register.read`/`register.manage` @
SYSTEM; release over the multi-axis `_register_release_scope` + SoD-2 server-side).

## Owner decisions (AskUserQuestion ×3, 2026-06-21)

1. **Viz analogue → party-type BOARD (7 cards).** Group the live working rows by `party_type` into
   the 7-card ISO-4.2 spine (the SWOT-board analogue, the family's "group by the spine" pattern); each
   chip = party_name + an **influence badge**; empty cards show "none recorded" (a completeness prompt
   — the analogue of SWOT's fixed 4-quadrant frame). Rejected: an influence-banded view (foregrounds
   relevance over the spine) and a table-only view (loses the family-consistent at-a-glance view).
2. **Scope → ONE slice (full SPA incl. the steward console).** The backend is fully ready, so no
   half-built surface (context did exactly this as S-context-fe; not the risk 4b→5 split).
3. **Home PLAN line → active + never-reviewed amber (mirror context).** `N active interested parties`
   (neutral, informational — the strategic picture) + `N interested parties never reviewed` (amber
   when >0, drives the RAG). NOT a high-influence headline (influence is *relevance*, not an alarm —
   it must not read as a false priority cue; it diverges from the context pattern).

## The influence encoding (the one genuinely-new design call)

`influence` is an **ordered** 3-level + unspecified axis, NOT a RAG alarm (a high-influence customer
is not a "problem"). So it is NOT mapped onto the danger/warning RAG tones. Instead a colour-blind-safe
**ordinal glyph ramp** carries the magnitude, the label carries the level, and the tone stays calm:

| influence | glyph | tone | label |
|---|---|---|---|
| high | `●` (filled) | info | High influence |
| medium | `◐` (half) | neutral | Medium influence |
| low | `○` (empty) | neutral | Low influence |
| null | — (dimmed) | neutral | Unspecified |

`●◐○` is a filled/half/empty ramp — legible in greyscale and to a colour-blind reader, distinct from
the canonical RAG glyph set (`✓◔✕●○★`). Status (active/closed) keeps the context mapping
(active = info `●` / closed = neutral `○`, de-emphasized + strikethrough, never deleted).

## The board chip + the DP-5 trap (carried verbatim from S-context-fe)

Each board chip is a clickable `Anchor` with an **explicit `aria-label`**. An explicit aria-label
OVERRIDES descendant content per the ARIA name computation, so a nested influence/closed badge would
be SWALLOWED — leaving the ordinal/closed signal as colour/strikethrough ALONE (a DP-5 / WCAG 2.2 AA
break `axe` can't catch). The fix: fold the grouping axis + the influence + the closed state INTO the
accessible name: `"{Party type}: {party_name} — {influence_label}{ (closed)}"`. The glyph is
`aria-hidden`; the visible influence/closed badges are decorative (the name carries them).

## Surface (the clone map)

`features/interested-parties/`: `labels.ts` · `board.ts` (+`board.test.ts`, golden 7-card order +
`bucketByPartyType`) · `hooks.ts` · `mutations.ts` · `InterestedPartiesRegisterPage.tsx` (+test) ·
`InterestedPartyTypeBoard.tsx` (+test) · `InterestedPartyScorecardBand.tsx` · `InterestedPartyDrawer.tsx`
(+test, no CAPA seam) · `NewPartyModal.tsx` · `EditPartyModal.tsx` (+test) · `RegisterLifecyclePanel.tsx`
· `PublishRegisterModal.tsx` (+`lifecycle.test.tsx`).

Wiring: `lib/types.ts` (the `InterestedParty*` block) · `App.tsx` (`/interested-parties` route) ·
`app/shell/LeftRail.tsx` (an **ungated** PLAN entry — filter-not-403, the risk/context precedent) ·
`features/home/PlanCard.tsx` (`useInterestedPartySummary` → active + never-reviewed amber; folded into
`allForbidden`) · `features/home/useHomeAsOf.ts` (the freshness read) · `test/msw/handlers.ts` (the
fixtures + handlers, static `/summary` + `/register*` before `/:id`, pinned via `satisfies`) + the
PlanCard / HomePage all-forbidden tests gain a `/interested-parties/summary` 403 (the orthogonal-read
trap).

Read-of-record discipline: the PAGE rolls its board + scorecard up CLIENT-SIDE from the live
`useInterestedParties()` working rows; Home reads `GET /interested-parties/summary` (the GOVERNING
controlled read) — distinct BY DESIGN.
