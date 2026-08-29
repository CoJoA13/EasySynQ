# S-ui-signal-board — Option C interface direction, Archivo, and the muted-text remediation

> **Status:** in progress. **S-ui-1 through S-ui-4 implemented 2026-08-29** (#505 foundation, #506
> shell, #507 Home, and the register pattern in this slice); S-ui-5 onward not started. All four §7
> owner decisions are resolved. Owner selected **Option C (Signal board)** from
> the four-artboard comparison on 2026-08-29 and asked for Archivo to be adopted and the contrast
> remediation folded into the same programme.
> **Corrections made during implementation** are marked inline in §2.2, §4 and §5 — the §4 remap as
> originally written was inert in the light scheme, and the §2.2 palette had an untested surface.
> **Design reference:** the published canvas (four artboards: `Current`, Option A, Option B,
> Option C, plus accessibility and font notes). Artboard sources are working files, not repo
> content; the token table in §2 is the authority for implementation.
> **Scope decision:** surface + layout rework. Routes and information architecture are UNCHANGED —
> the PDCA rail grouping and clause ranges stay exactly as they are.

---

## 1. Why this work exists

The complaint was "generic and unbranded" plus "flat and lifeless". Investigation found three
distinct causes, only one of which is a matter of taste.

**1.1 The token system is aspirational.** `apps/web/src/theme/tokens.css` defines a complete design
system — a tuned type ramp, an 8px spacing scale, a five-step elevation ladder, layout dimensions,
PDCA hues. Measured uses outside the token file and the Mantine theme:

| Token group | Uses in components |
|---|---|
| Typography `--es-fs-*` / `--es-lh-*` | **0** |
| Spacing `--es-space-*` | **0** |
| Elevation `--es-shadow-*` | **0** |
| Layout `--es-content-max` / `--es-sidebar-w` / `--es-topbar-h` | **0** |
| Status colours | ~120 |

What renders is therefore stock Mantine 7 with an indigo primary. `AppShell.tsx` hardcodes
`header={{ height: 60 }}` and `navbar={{ width: 256 }}` rather than reading the tokens that exist
for exactly that purpose. Flatness follows from uniformity: every card is the same card at the same
elevation with the same rhythm, so nothing is louder than anything else.

**1.2 There are no icons.** Zero. The 17-item PDCA rail is a wall of text, and there is no icon
dependency in `package.json`.

**1.3 The brand and the interface disagree.** `public/easysynq-mark.svg` is **teal `#0EA394`** with a
**blue `#1565C0`** check. The interface accent is **indigo `#4f5bd5`**. The one piece of real brand
equity the product owns is not expressed anywhere in the UI. The wordmark SVG additionally sets its
text in Inter, which is not loaded — it renders in a fallback.

---

## 2. The design system

### 2.1 Accent — reconciled to the brand mark

This is the substantive branding decision. The accent moves from indigo to the mark's teal, so the
logo and the interface finally agree. The raw brand teal is too light for text on paper (2.9:1), so
it is darkened for the light scheme; in the dark scheme the **exact brand hex passes as-is**.

| Token | Light (now) | Light (new) | Dark (now) | Dark (new) | Contrast |
|---|---|---|---|---|---|
| `--es-accent` | `#4f5bd5` | `#0a7a6f` | `#7682f0` | `#0ea394` | white on light fill **5.22:1** |
| `--es-accent-text` | `#3a44ab` | `#097167` | `#aab2f7` | `#3fcfbe` | 4.70–5.88:1 on all surfaces |
| `--es-accent-soft` | `#ecedfb` | `#e2f2f0` | `#1f2238` | `#0f2a27` | tint fill |

**PDCA hues do not become the accent.** They stay as category markers (§2.3). Indigo remains the
PLAN hue, so nothing about the clause spine changes meaning.

### 2.2 Surfaces — warm neutral

Option C's ground is warm rather than cool. This is most of what separates it from the current build
at a glance.

| Token | Now | New |
|---|---|---|
| `--es-bg` | `#f7f8fa` | `#f6f4f1` |
| `--es-surface` | `#ffffff` | `#ffffff` |
| `--es-surface-2` | `#f2f3f6` | `#f2efea` |
| `--es-surface-3` | `#eaecf1` | `#f0ece5` |
| `--es-sidebar` | `#fbfbfc` | `#ffffff` |
| `--es-border` | `#e4e6ec` | `#e6e2db` |
| `--es-text` | `#1a1d27` | `#1c1b18` |
| `--es-text-2` | `#565b6b` | `#5c5850` |
| `--es-text-muted` | `#8a909e` | `#6a655c` (see §4) |

Dark-scheme surfaces are unchanged — they already work and are already AA.

> **Correction, applied during S-ui-1 implementation.** The §4 audit below tested `bg`, `surface`,
> `surface-2` and `sidebar` — it never tested **`--es-surface-3`**, and three of the values
> originally specified here fail on it. `surface-3` is not hypothetical: `CommandPalette.tsx`
> renders `c="dimmed"` directly on it for the selected row.
>
> - `--es-surface-3` was specified as `#eae5de`. That is not merely warmer than `#eaecf1`, it is
>   **darker** (luminance 0.788 vs 0.838), which pushed `--es-warning-text` from a passing 4.58:1
>   to a failing 4.32:1 — a regression the slice would have introduced. `#f0ece5` keeps the warm
>   hue at the original luminance band.
> - `--es-text-muted` moved `#6f6a61 → #6a655c` and `--es-accent-text` split from `--es-accent`
>   (`#097167` vs `#0a7a6f`) so both clear 4.5:1 on `surface-3` too.
>
> With these three values the **full 12 × 5 cartesian product** of foreground tokens against
> surface tokens clears 4.5:1 in both schemes, so the regression test needs no exclusion list.
>
> A fourth value moved for the same reason: **`--es-accent-soft-2`** (`#c5e4df → #cfe9e5`). The warm
> accent tint put `--es-accent-text` at **4.35:1**, down from a passing 6.26:1 before the slice —
> and the first version of the regression test had no pair covering it, so its own "no exclusion
> list" claim was untrue. The gate now derives the tint list from the token file and fails when any
> `*-soft*` / `*-bg` token has no foreground assigned, which makes the claim mechanical.
>
> Two accepted residuals, both measured: the **focus ring** was a 32%-alpha wash compositing to
> **1.59:1** against every light surface (WCAG 2.2 SC 1.4.11 wants 3:1) — this predated the slice
> (the indigo ring measured the same) and is corrected here to a solid token at 3.69:1 / 7.87:1.
> And the shipped Archivo subsets contain no glyph for the status vocabulary (`✓ ◔ ✕ ● ○ ★`) or for
> `←`/`→`, so those render from the fallback stack; the vocabulary falls back *uniformly*, so the
> non-colour status channel stays internally consistent.

### 2.3 PDCA quadrant treatment

Option C's defining move: each quadrant is a card with a **tinted header bar** carrying the phase
name, its clause range, and **that quadrant's worst current signal** as a glyph plus a short phrase.
All header text was verified at ≥4.5:1 on its tint.

| Phase | Tint | Heading text | Clause text |
|---|---|---|---|
| PLAN | `#eceafb` | `#343ca6` (7.6:1) | `#5b62bc` (4.5:1) |
| DO | `#e2f0fa` | `#16628f` (5.7:1) | `#387198` (4.5:1) |
| CHECK | `#fbeecd` | `#8a5a05` (5.1:1) | `#856830` (4.5:1) |
| ACT | `#dff2e7` | `#116139` (6.4:1) | `#3d7758` (4.5:1) |

**The signal must be derived, never asserted.** During review, the ACT header read "✓ on track"
above six open CAPAs. A green tick over that data is precisely the "compliance verdict" the product
is careful never to imply. Rule: the header states an observed count and its threshold status, in
the same vocabulary the tile below uses, and it always carries the non-colour glyph (`✓◔✕`) so the
signal survives without colour.

### 2.4 Shape, depth, density

- Card radius **16px** (`--es-radius-xl` exists at 16px — use it), nav items **8px**, badges/pills
  **999px**.
- Elevation stays flat-with-borders on cards; the shadow ladder is reserved for overlays
  (drawer, modal, popover, command palette). Do not put shadows on the quadrant cards.
- Rail **244px**, top bar **58px** — and these come from `--es-sidebar-w` / `--es-topbar-h`, read by
  `AppShell`, not hardcoded.

### 2.5 Icons

New dependency, or a hand-rolled set. Requirements: stroke-based, 16/20/24 grid, 1.8 stroke,
tree-shakeable, **self-hosted SVG (not an icon font)** — the CSP is `font-src 'self'` and the product
ships air-gapped. One icon per rail destination (17), plus the shell set (search, bell, chevron,
close, external, filter, sort).

---

## 3. Typography — Archivo, self-hosted

**Google Fonts is not an option and not merely undesirable.** The Caddy CSP sets `font-src 'self'`,
so a `fonts.gstatic.com` request is blocked outright, and the air-gap bundle has no egress at all.

- **Ship** `Archivo` variable (wght 400–700), latin + latin-ext subset, `.woff2`, into
  `apps/web/public/fonts/`. Variable keeps it to roughly one 40–60 KB file instead of four static
  cuts. Archivo is **SIL Open Font License 1.1** — bundling is permitted; ship `OFL.txt` alongside.
- **Declare** `@font-face` in `tokens.css` (or a sibling `fonts.css` imported before it) with
  `font-display: swap` and a `unicode-range`, then set
  `--es-font-sans: "Archivo", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif`.
  Mantine and Tailwind both already read that variable — no further wiring.
- **Do not** touch `--es-font-mono`; the mono stack stays system.
- **`src/test/public-assets.test.ts` pins every root-absolute public asset.** Font files are
  referenced from CSS rather than by root-absolute URL, so they fall outside that test's current
  contract — add a sibling assertion that the woff2 exists, or the font can silently 404 with the
  whole gate green.
- **Re-set the wordmark.** `easysynq-logo-light.svg` / `-dark.svg` set their text in Inter. Convert
  the text to outlines in Archivo so the mark matches the interface and stops depending on a font
  that is not loaded.
  > **Not done in S-ui-1, and deliberately so.** Both wordmark files are referenced **nowhere** in
  > the application — `index.html` and the SPA use `easysynq-mark.svg` / `easysynq-mark-simple.svg`,
  > which are pure geometry with no text and therefore no font dependency. The Inter reference is
  > inert. Note also that an SVG loaded through `<img src>` is an isolated document that cannot
  > inherit `@font-face` from the host page, so "set it in Archivo" would require embedding the
  > font or outlining the glyphs either way. Left alone; the two files are candidates for deletion
  > or for reuse on a future marketing/login surface, which is a separate decision.

---

## 4. The muted-text remediation

Filed originally as a one-token contrast fix. It is not — investigation changed its shape entirely.

**A full audit of `tokens.css` found exactly one failing token**, and it fails in both schemes on
every surface:

| | Light | Dark |
|---|---|---|
| `--es-text-muted` on `--es-bg` | 3.01:1 | 4.10:1 |
| on `--es-surface` | 3.20:1 | 3.75:1 |
| on `--es-surface-2` | 2.88:1 | 3.47:1 |
| on `--es-sidebar` | 3.09:1 | 3.98:1 |

Every other pair — `text`, `text-2`, `accent`, `accent-text`, all ten status and PDCA tint pairs, in
both schemes — passes comfortably.

**But fixing the token alone fixes almost nothing.** Components reach muted text through Mantine, not
through the token:

- `c="dimmed"` — **331 uses**
- `--es-text-muted` directly — **4 uses**
- The Mantine theme does **not** remap `dimmed` onto the token.

So what those 331 call sites actually render is Mantine's own `--mantine-color-dimmed`:
`gray-6 #868e96` in light (**2.99–3.32:1 — fails on every surface**) and `dark-2 #828282` in dark
(4.27:1 on `surface-2` — fails there). This is the real defect, and it covers every rail section
header, breadcrumb, timestamp, caption, and the "not a compliance verdict" caveat.

**The fix is two lines plus a token change** — but ⚠ **not these two lines.** The version below was
specified during planning and is **inert in the light scheme**; it is recorded here only because the
reason is worth keeping:

```css
/* WRONG — do not use. The first line loses on specificity; see below. */
:root { --mantine-color-dimmed: var(--es-text-muted); }
:root[data-mantine-color-scheme="dark"] { --mantine-color-dimmed: var(--es-text-muted); }
```

Mantine declares this variable under `:root[data-mantine-color-scheme='light']` **and**
`...='dark'` — both specificity **(0,2,0)**. A bare `:root` is **(0,1,0)** and loses to them
regardless of source order. So the dark line works, and the light line does nothing: measured in
the built bundle, light still resolved to Mantine's `gray-6` at **3.03:1**. The fix would have
shipped with every gate green and the light scheme — the default — still failing at all 331 call
sites, which is precisely the defect it was written to remove. What ships instead matches Mantine's
specificity on both schemes and wins on source order:

```css
:root,
:root[data-mantine-color-scheme="light"],
:root[data-mantine-color-scheme="dark"] {
  --mantine-color-dimmed: var(--es-text-muted);
}
```

with `--es-text-muted` corrected to **`#6a655c`** (light, 4.6–5.8:1 on the new warm surfaces,
including `surface-3`) and **`#8b909d`** (dark, 4.5–6.1:1). One change, 331 call sites, no component
edits.

**Make it durable.** Add a unit test that parses `tokens.css`, computes WCAG contrast for every
foreground/surface and text-on-tint pair in both schemes, and fails under 4.5:1 (3:1 for tokens
documented as large-text only). Without it this rots the first time someone adjusts a grey. The
audit script written during planning is the basis for it.

---

## 5. Implementation slices

One slice = one branch = one PR = green CI, per the repository working agreement. Each slice below
is independently shippable and leaves the app coherent.

### S-ui-1 · Foundation (no page recomposition)
Tokens (§2.1, §2.2), Archivo self-hosted (§3), the `dimmed` remap and contrast test (§4), and the
Mantine theme wired to the token scales — `fontSizes`, `lineHeights`, `headings`, `radius`,
`spacing`, `shadows` all sourced from `--es-*` instead of Mantine defaults. `AppShell` reads
`--es-topbar-h` / `--es-sidebar-w`.

*This slice alone changes the entire application's appearance*, because every Mantine component
resolves through the theme. Expect broad visual change with near-zero component edits — and expect
the `toHaveStyle` assertions (47) to be where breakage concentrates.

**Risk:** highest blast radius of any slice, lowest code churn. Land it alone and look at every
route before moving on.

> **Implementation note (2) — the Mantine theme is wider than it looks.** Three traps, all found by
> adversarial review after the first implementation pass and all verified in the browser:
>
> - **`primaryShade` is THEME-GLOBAL.** Mantine re-derives `-filled` for *every* entry of
>   `theme.colors`, not just the primary. Moving off Mantine's defaults (light 6 / dark 8) with the
>   default forced-white label pushed `color="red"` from 4.51:1 to **3.84:1** in dark — reaching
>   `ConfirmDestructive`, the shared confirm for every irreversible act. Fixed with
>   `autoContrast: true` + `luminanceThreshold: 0.179`, which also repairs four palettes that were
>   *already* failing (teal 3.95, orange 3.58, green 3.45, yellow 2.48).
> - **A per-scheme `primaryShade` split is unsafe.** `parseThemeColor` resolves a shade-less theme
>   colour with `colorScheme || "light"` and the variant resolver is never handed the real scheme,
>   so the autoContrast label is *always* judged from the LIGHT shade. `{ light: 7, dark: 6 }`
>   therefore put a white label on the dark fill at **3.14:1**. Use ONE shade for both schemes.
> - **The token ramp must be rem, not px.** Mantine's scales were `calc(Xrem * …)` before this
>   slice, so sourcing them from px tokens silently froze all type against the reader's browser
>   font-size setting. Converted; verified at a 20px root (`size="sm"` → 17.5px).
>
> **Implementation note (1).** Map the token ramp onto Mantine's scale **semantically, not
> positionally.** Mantine's t-shirt keys size *components*; `--es-fs-*` describes *document type*.
> `sm` is Mantine's default for the large majority of components (402 call sites), so it must carry
> **body (14px)**, not the smaller `small` row. A positional mapping shrinks the dominant text
> 14 → 13px and — because Mantine derives input description/error text as `font-size-sm − 2px` —
> drops every validation message to **11px**. Likewise the `lineHeights` scale must be **unitless
> ratios** (`--es-lhr-*`), never the px `--es-lh-*` values: `--mantine-line-height` is inherited by
> elements that set their own font-size, and a fixed px value crowds them (the show-once credential
> in `ShowOncePassword.tsx` is the visible case). Headings keep px pairs, since size and leading are
> set together there.

### S-ui-2 · Shell
`TopBar`, `LeftRail`, `Breadcrumb`, `DetailDrawer`. Introduces the icon set, the 244px rail, the
per-phase colour markers, and nav count badges. The `GlyphLegend` moves under a rule at the rail
foot.

**Trap:** the rail is covered by `LeftRail.test.tsx` (179 lines) which asserts labels and active
state. Adding an icon inside the same element changes accessible names — keep icons
`aria-hidden="true"` so `getByRole("link", { name: … })` keeps matching.

### S-ui-3 · Home / QMS health
The signal-board dashboard: `HealthSummary` hero with proportion bar, four quadrant cards with
tinted signal headers, `MyTasksRail` as a bordered list with typed action chips.

**Requires** a derived-signal helper per quadrant (§2.3) — new pure logic, unit-testable, and the
place the "never assert a verdict" rule is enforced. `rag.ts` already exists and is the right home.

### S-ui-4 · Register pattern

> **Three corrections, all verified against the code on 2026-08-29 before implementation.** The
> paragraph as originally written was wrong in each of its three claims, and is kept below with the
> corrections inline because two of the errors would have caused real rework.
>
> 1. **CAPA / Risk / Audit do not share `RegisterLifecyclePanel`.** They share
>    `features/registers/RegisterFilterBar.tsx` (the created-date window, from #502). The
>    triplicated `RegisterLifecyclePanel` (148 lines ×3) is in `features/risk/`,
>    `features/context/` and `features/interested-parties/` — a different trio. The plan conflated
>    the two.
> 2. **"Accepted under U28" is real but uncitable by that token.** The acceptance is
>    `docs/decisions-register.md` → "Accepted duplication: the risk / context / interested-parties
>    register stacks", added by `6c60160` (#502). The section never uses the string "U28", which is
>    why grepping for it finds only this plan.
> 3. **Most of the "shared register composition" already exists.** `lib/states.tsx` supplies
>    Loading/Error/NoAccess/Empty/Inline/Skeleton/MutationError; `lib/RegisterToolbar.tsx` supplies
>    the search toolbar, `SortableTh` and `SubjectCell`; `lib/registerControls.ts` and
>    `lib/useRowKeyboardNav.ts` supply the hooks. Rebuilding them would have been rework. The
>    genuinely missing piece was the **page header**.

**What the slice actually did.** Built `lib/RegisterPageHeader.tsx` (title, permission-gated
action, freshness stamp) and adopted it on eleven register pages across all three of their
loaded / forbidden / error branches — collapsing, among others, the three divergent header forms
`CapaBoardPage` carried. Built `lib/ScorecardBandShell.tsx` for the four scorecard bands, which is
the one limb of the accepted-duplication entry whose revisit trigger fires. Did **not** rebuild the
filter bar, the state primitives, or a table wrapper.

**Deliberately not built: a `RegisterPageFrame`** (the four-branch forbidden/loading/error/loaded
scaffold). Adversarial review found three blockers worth recording: an always-taken return destroys
TypeScript control-flow narrowing on the five pages that use a narrowing early return; rendering
the title during loading breaks `AuditsListPage.test.tsx`'s heading-based load gate and
`DcrsRegisterPage.test.tsx`'s container-width contract, both of which identify the loaded state by
the heading alone; and `lib/responsiveRegisterContract.test.ts` is a **source-text** contract over
nine page files, so a shared table wrapper is not merely undesirable but presently impossible.

**The U28 decision, taken by the owner on 2026-08-29 against the measured diff:** collapse the
scorecard band; leave the lifecycle panel and publish modal accepted. Reasoning and corrected
numbers are in the decisions register.

### S-ui-5..n · Remaining registers and detail surfaces
Documents/Library, Records, Objectives, Context, Interested parties, Improvement, DCR, Management
review, Compliance, Audit detail, Document detail, Drift, Ingestion. Group by shared composition,
roughly three or four slices. Sequence by traffic: Library and Records first.

### S-ui-last · Sweep
Remove superseded inline styles, confirm no `--es-*` token is unused, run the a11y pass across every
route, and record the design system in `docs/11-*` so the tokens stop being folklore.

---

## 6. Test strategy

Current surface: **271 test files**, 1118 `getByRole`, 859 `getByText`, 517 `getByLabelText`, 47
`toHaveStyle`, 93 jest-axe references.

The repository's recurring web traps all apply here, and three are near-certain:

1. **`getByLabelText` is single-match.** Duplicating an accessible name across a legend glyph and a
   row badge, or across a repeated component, breaks it. Scope with `within(...)`.
2. **jest-dom matchers need `import { expect } from "vitest"`.** The bare global resolves to
   `@types/jest`, so `vitest run` is green while `tsc` fails. Only the full `/check-web` catches it.
3. **`toHaveStyle` against token values.** Assertions that hardcode `#4f5bd5` or `rgb(79, 91, 213)`
   break in S-ui-1. Prefer asserting the CSS variable name, or delete the assertion — a colour
   literal in a test is a second source of truth for the palette.

Add, don't just repair:
- the contrast regression test (§4);
- a token-adoption test asserting `AppShell` reads the layout tokens rather than literals;
- per-slice jest-axe coverage on the recomposed pages.

Run the **full** `/check-web` before every PR — `noUncheckedIndexedAccess` and cross-file drift only
surface there, and vitest takes about six minutes.

---

## 7. Open decisions for the owner — **all four resolved 2026-08-29**

| # | Decision | Owner's answer |
|---|---|---|
| 1 | Accent → brand teal | **Adopted.** Light `#0a7a6f`, dark `#0ea394`. Shipped in S-ui-1. |
| 2 | U28 revisited at S-ui-4 | **Resolved 2026-08-29 against the measured diff: collapse the scorecard band only.** The panel/modal acceptance stands on corrected numbers (96.5–98.3%, not the recorded 74%). See the decisions register. |
| 3 | Icon set | **Hand-rolled (~25 SVGs).** No lockfile or air-gap-bundle addition. |
| 4 | Dark-scheme parity | **In scope for S-ui-3.** Dark quadrant tints to be designed and owner-reviewed before build, not derived. |

The original framing of each is kept below for the reasoning.


1. **Accent → brand teal (§2.1).** Recommended, and the reason the product stops looking unbranded.
   Rejecting it means the mark and the UI stay in disagreement; that is a legitimate choice but it
   forecloses most of the "unbranded" complaint.
2. **U28 revisited at S-ui-4.** The register triplication was accepted deliberately. Rebuilding the
   register surface is the natural moment to collapse it — or to re-affirm the acceptance.
3. **Icon set: dependency or hand-rolled.** A dependency is faster and more consistent; hand-rolled
   is ~25 icons and adds nothing to the lockfile or the air-gap bundle.
4. **Dark scheme parity.** Option C was designed light. The dark tokens exist and pass AA, but the
   signal-board treatment (tinted headers) needs dark equivalents designed, not derived. Decide
   whether dark is in scope for S-ui-3 or deferred.

## 8. Explicitly out of scope

Routes, information architecture, the PDCA rail grouping, the API, and any permission or gating
behaviour. If a slice finds itself changing what a page *does* rather than how it looks, stop and
re-scope.
