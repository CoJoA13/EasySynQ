# S-statusbadge-2 — StatusBadge Phase 2 (whole-app critique #1, P1)

> Status: **APPROVED, ready to build.** Owner design-calls resolved (below). **Front-end only** — no migration / key / endpoint / contract.
> Phase 1 (the canonical system) shipped as PR [#142](https://github.com/CoJoA13/EasySynQ/pull/142) (`feat/s-statusbadge-1`).
> Critique source: `.impeccable/critique/2026-06-15T06-38-01Z__apps-web.md` (P1 #1 + the [P2] badge-contrast item).

## Goal

Migrate the ~11 remaining per-feature badge components onto the ONE canonical status system, removing the
last of the ~12 drifting per-feature glyph/colour maps. Closes the single highest-reach P1 from the
2026-06-15 whole-app design critique (two non-interoperable colour conventions + a drifting glyph language)
for the badges Phase 1 didn't reach — and, as side effects, closes the **[P2] badge-contrast AA risk** and the
named clarify deferral (the Objectives-register RAG badge has no glyph).

## The canon (already built — Phase 1 / #142)

- `apps/web/src/lib/status.ts` — `type Tone = success | warning | danger | info | neutral | emphasisSuccess`;
  `TONE_GLYPH` = ✓ / ◔ / ✕ / ● / ○ / ★ (`▲` is **retired** — it was the core glyph drift).
- `apps/web/src/lib/StatusBadge.tsx` — `<StatusBadge tone label glyph? kind? size? />`; renders
  `variant="status"` + `color={tone}` + an aria-hidden glyph (`glyph ?? TONE_GLYPH[tone]`) + aria-label `${kind}: ${label}`.
- `apps/web/src/theme/mantine.ts` — `statusVariantColorResolver`: `variant="status"` → the AA-tuned
  `--es-${tone}-soft` (fill) / `--es-${tone}-text` (fg) pair (light+dark free via `tokens.css`); **falls through
  to `defaultVariantColorsResolver` for every other variant** — pinned by a fall-through test, do NOT break it.

## Migration recipe (the `features/document/StateBadge.tsx` exemplar)

Each component declares a **feature-local** `META: Record<DomainState, { label: string; tone: Tone }>` that imports
`Tone` from `lib/status` + `StatusBadge` from `lib/StatusBadge`, then renders
`<StatusBadge tone={meta.tone} label={meta.label} kind="…" size={size} />`. Reuse any existing domain label
map; **delete** the local colour map + ad-hoc glyph. Per-domain `state→tone` maps stay FEATURE-LOCAL (do not
centralise — the #142 owner decision; only the `Tone` + glyphs are shared).

## Targets

| Component (path under `apps/web/src/`) | Maps to migrate |
|---|---|
| `features/document/TaskStateBadge.tsx`, `ReviewStateBadge.tsx` | task / review state → tone |
| `features/dcr/DcrStateBadge.tsx` | 9 DCR states → tone |
| `features/ingestion/ImportStatusBadge.tsx` | run status → tone |
| `features/ingestion/ConfidenceCell.tsx` | HIGH / MED / LOW / AMBIGUOUS → tone (see design-call) |
| `features/compliance/CoverageBadge.tsx` | Covered / Partial / Gap / Overdue → tone |
| `features/audits/badges.tsx` | audit state + finding severity → tone |
| `features/capa/*` (`SEVERITY_COLOR` in `columns.ts`; `CapaCard`, board consumers) | severity → tone |
| `features/objectives/*` (`RAG_COLOR`, `ObjectiveScorecardBand`, the register RAG badge) | RAG → tone **+ add the missing glyph to the register badge** |
| `features/management-review/ReviewInputsSection.tsx` | duplicate RAG map + the `close_state` badge → tone |
| `features/drift/DriftStatusPage.tsx` | scan-status META → tone (FAILED = danger — see design-call) |
| `features/ingestion/KindCell.tsx` | **LEAVE** (bespoke kind badge — see design-call) |

## Resolved owner design-calls

- **Drift "Failing" / FAILED → tone `danger` (✕).** It's a genuine integrity failure (failed blob-verify /
  mirror-tamper); strongest signal for Olsen on a greyscale audit export.
- **`ConfidenceCell`: LOW and AMBIGUOUS both → `danger` (✕)**, disambiguated by the text label. Faithful 1:1
  migration; the LOW-vs-AMBIGUOUS semantic split (give AMBIGUOUS its own tone) is **deferred to Phase 3**.
- **`KindCell`: LEAVE it as a bespoke kind indicator.** It shows Document/Record with the `IconDocument` /
  `IconRecord` SVGs (already de-emoji'd in #4) — a domain icon, not a tone glyph — so it does NOT route through
  `StatusBadge`. *Optional low-priority touch:* swap its `variant="filled" color=var(--es-info/success)` for the
  AA-tuned `-soft/-text` pair via the styles API, keeping the SVG.

## Scope-out (→ later P2/P3 slices)

- **Phase 3 (DP-5 colour-alone SHAPES, genuine design work):** `ObjectiveTrendChart` RAG marker shapes
  (●▲■ — survives greyscale), `BandPreview` zone borders/labels, the Drift "Failing" count status channel,
  MR `ObjectivesBand`.
- **Shared loading/error/empty primitives** (`<PageLoading>` / `<ErrorState onRetry>` / `<EmptyState>` + the
  `useAckCount` silent-zero fix) — Slice 3.
- **PDCA-grouped rail + glyph legend + domain-term tooltips** — Slice 4 (depends on this slice's canonical map).

## What it closes

- P1 #1 (the highest-reach critique defect) for the badges Phase 1 didn't reach.
- [P2] badge-contrast AA risk — every migrated badge moves off Mantine's `variant="light"` raw-hue math onto the
  hand-tuned `-soft/-text` pairs.
- The named clarify deferral: the Objectives register RAG badge gains a glyph.

## Verify

- FE-only → no `/check-api` / `/check-migrations` / `/check-contracts`. Run `/check-web`
  (eslint + strict `tsc --noEmit` + build + vitest) → `web-test-trap-reviewer` → `diff-critic` →
  live-smoke 2–3 surfaces (computed-style read beats screenshots per `chrome-mcp-live-smoke-mechanics`).
- Update each migrated component's test: assert the StatusBadge label + glyph + aria-label, and KEEP the
  colour-safe-channel assertions (label + non-colour glyph present). Watch the recurring web traps:
  `import { expect, it } from "vitest"`; regex `getByLabelText(/…/)` on Mantine required fields;
  duplicate-aria-label `getByLabelText` → `getAllByLabelText(...)[0]`; pin MSW fixtures with `satisfies`.

## Mechanics

- Branch: `feat/s-statusbadge-2` off `main`.
- Set **`ECC_GATEGUARD=off`** for the session (avoids per-file fact-forcing on the ~22 file writes).
- Run web tests via `--pool=forks --poolOptions.forks.singleFork=true` for a clean signal (the documented full-run thrash).
- ~11 component migrations + their test files; homogeneous → a good fit for a subagent fan-out partitioned by
  feature directory (each agent owns disjoint files; only `lib/status.ts`/`StatusBadge.tsx` are shared READ-only imports).
