# Interested parties stable register columns implementation plan

> **For agentic workers:** Use `superpowers:executing-plans` to implement this plan task by task. The coordinator dispatches independent plan, implementation, and final reviews; do not create duplicate approval gates or child agents. Steps use checkbox syntax for tracking.

**Goal:** Keep the interested-parties table's column boundaries stable when an operator filters the same register, while preserving readable labels and localized horizontal scrolling.

**Architecture:** Measure the current browser layout before changing it. Give the four compact columns measured widths under `layout="fixed"`, leaving Party to consume the remaining width. Make the change in the page and prove behavior through the existing Chromium harness.

**Tech stack:** React 19, TypeScript 6, Mantine 7, Playwright, Vitest, Node 26, frozen npm lock.

> **Amendment, 2026-10-10:** The original observations, numeric-width instructions, and handoff
> below preserve the 2026-10-02 implementation record. The dated follow-up at the end supersedes
> the pixel allocations and adds enlarged-text and rendering-variation acceptance. Other scope
> and behavior constraints remain in force.

**Spec:** The original `RES-IP-REGISTER-COLUMN-JUMP` closure contract required post-header-change browser measurements, fixed column widths or proven stable floors, and a browser assertion that an edge stays fixed across filter states. Its tested candidate evidence is preserved in [the dated history entry](../../slice-history.md#interested-parties-columns-remain-stable-while-filtering-2026-10-02), with [GitHub #559](https://github.com/CoJoA13/EasySynQ/issues/559) tracking publication. Base: `ddfbf400d1c3025515682ccf56e603570f1e8b6c`; branch: `codex/issue-559-register-column-widths`.

## Global constraints

- Retain exactly one literal `<Table.ScrollContainer minWidth={880}>` around the page's existing semantic table; `responsiveRegisterContract.test.ts` pins that source contract.
- Preserve column order: Party, Type, Influence, Status, Last reviewed. The ledger's Category/Interest wording describes an earlier table; do not add those columns.
- Preserve filters, URL state, sorting, keyboard row navigation, drawer selection, permissions, lifecycle controls, board, and scorecard behavior.
- No shared frame, theme, token, font, badge, toolbar, API, enum, contract, dependency, or lockfile changes.
- Keep party name and expectations on their existing clamped lines; enum labels, glyphs, sortable headers/icons, dates, and Never must remain readable.
- GitHub only. No implementation, dependency installation, commit, push, or PR during the plan phase. The coordinator owns long setup/build/test jobs and publication.

## Review focus

1. Filtering removes the longest influence badge and shifts later columns; measure real boundaries before and after the filter.
2. Fixed widths allow an unclipped badge to paint into its neighbor; prove content containment for every current enum label and null influence.
3. A narrow viewport moves the entire document sideways; prove one table scroll owner and reach the final column at 320px.
4. Long party names or expectations force or escape the Party allocation; cover short, long spaced, and long unbroken content.
5. Sorting, empty results, and restoration remount or alter the table; verify nonempty sort/filter transitions and restoration without expecting a table during the empty state.

## Initial evidence and setup

Read: `AGENTS.md`, the residual, `InterestedPartiesRegisterPage.tsx`, `labels.ts`, `RegisterToolbar.tsx`, `StatusBadge.tsx`, `responsiveRegisterContract.test.ts`, browser API fixtures/configuration, and existing geometry/legibility specs.

The current table uses automatic layout. `SortableTh` already prevents wrapping; the theme permits badges to exceed their cells, so a stable boundary alone does not prove legibility. The fixture currently contains six parties, all three influence labels, null influence, active/closed status, and date/Never.

Phase 1 inspection found Node `v26.8.1`, cached Chromium build `1243`, and generated web schema present. `apps/web/node_modules` is absent. No browser reproduction or width measurement has run; there are deliberately no invented pixel values in this plan.

Both `playwright.config.ts` and the fail-closed API harness require `http://127.0.0.1:4174`. `ss -ltnp 'sport = :4174'` showed no listener at planning time. The coordinator reserves this canonical port exclusively for #559 and serializes any other browser work. Recheck before execution; never reuse or stop another worker's listener. A config-only 5599 override cannot work because the API harness rejects its origin. Do not change the shared harness to solve this issue.

### Browser observations, 2026-10-02

The coordinator captured four valid baseline geometry failures: selecting Low influence moves Last reviewed by 5.46875px at 320/1000/1115px and 6.0625px at 1280px. A separate measurement run passed eight probes. Source/build manifests, probe attachments, and extracted measurements are preserved under `.superpowers/sdd/2026-10-02-issue-559-register-column-widths/`; these portable worktree-relative paths name ignored evidence, not files to commit.

At the 880px floor, after font readiness, cells have 8px padding on each side. The measured compact content is:

| Column | Widest content | Content width | Required with padding | Selected width |
| --- | --- | ---: | ---: | ---: |
| Type | Community badge with glyph | 94.546875px | 110.546875px | 112px |
| Influence | Medium influence badge with glyph | 135.296875px | 151.296875px | 152px |
| Status | Closed badge with glyph | 71.796875px | 87.796875px | 88px |
| Last reviewed | Header button including icon | 111px | 127px | 128px |

Widths round up to the next 8px step and leave Party 400px at the floor. The Unspecified/date/Never text Range widths are 74/75/38px; their block boxes are not intrinsic text minima. All four compact labels/glyphs remain the same measured size at desktop. Subsequent rebuilt GREEN and containment checks passed at all four viewports; both rebuilt mutants failed on their intended assertions, and both rebuilt restorations passed.

## Task 1: Reproduce movement and pin a meaningful browser regression

**Files:** Modify `apps/web/e2e/register-table-legibility.spec.ts`. Read existing synthetic data in `apps/web/src/test/msw/handlers.ts`; do not edit it or `e2e/support/api.ts`.

**Interface:** Add spec-local helpers `measurePartyColumns(page: Page): Promise<{ columns: { left: number; width: number }[]; tableLeft: number; tableWidth: number; scrollLeft: number }>` and `expectPartyContentContained(page: Page): Promise<void>`. Return five column boxes in displayed order with left edges relative to the table. Use DOM geometry, not CSS source checks.

- [x] Coordinator hydrates only the web frozen lock with `npm ci --prefix apps/web`, then builds with `npm --prefix apps/web run build:browser`. Confirm Chromium availability and exclusive port ownership. Use Process Jobs for potentially long commands and report actual exit statuses.
- [x] Add tests named `interested-parties keeps column boundaries through filtering at <width>px`, parameterized at 320, 1000, 1115, and 1280 pixels. Use `installRegisterApi(page, { route: "interested-parties" })`, navigate to `/interested-parties`, await table rows and `document.fonts.ready`, and hold the table scroll offset at zero for paired snapshots.
- [x] Measure every header's `getBoundingClientRect().left - table.getBoundingClientRect().left`, width, table width, and absolute table left. Establish nonempty row counts before each snapshot. Capture unfiltered, influence Low, influence Unspecified, party type Partner, status Closed, and cleared/restored states using the real controls, scoped to the toolbar/main where names repeat. Assert each filtered state's row count and expected party identity; await URL/control state and the rendered row count before measuring.
- [x] For every nonempty paired state, assert each header left edge and width differ by at most 1 CSS pixel, and table width/absolute left differ by at most 1 pixel. Include a Last reviewed assertion explicitly, satisfying the ledger's two-filter-state contract. Keep scroll offset identical so scroll movement cannot impersonate a sizing defect.

  ```ts
  expect(before.columns).toHaveLength(5);
  expect(after.columns).toHaveLength(5);
  expect(after.scrollLeft).toBe(before.scrollLeft);
  for (let i = 0; i < 5; i++) {
    expect(Math.abs(after.columns[i]!.left - before.columns[i]!.left)).toBeLessThanOrEqual(1);
    expect(Math.abs(after.columns[i]!.width - before.columns[i]!.width)).toBeLessThanOrEqual(1);
  }
  expect(Math.abs(after.tableLeft - before.tableLeft)).toBeLessThanOrEqual(1);
  expect(Math.abs(after.tableWidth - before.tableWidth)).toBeLessThanOrEqual(1);
  ```

- [x] Add a search-only state selecting a short-name row, and a no-match query with no table. Clear search, await the original rows, and compare the restored geometry. Debounced search must settle via row assertions, not sleeps. Click `Sort by Last reviewed` and verify `aria-sort` plus stable geometry for both directions.
- [x] Run `(cd apps/web && npm exec -- playwright test register-table-legibility.spec.ts --grep 'interested-parties keeps column boundaries')`. Expected: a reproducible geometry assertion failure against the unchanged page. Record measured vectors, viewport, font readiness, rows, and failure in the implementation evidence; a setup failure is not a valid red proof.
- [x] If existing data produces no movement, add spec-local synthetic rows with distinct short/long names and all enum labels by overriding only GET `/api/v1/interested-parties` after installing the default harness. Import and clone its typed fixture, preserving valid response shape. Reproduce the automatic-layout defect before choosing widths. Do not accept a green pre-change regression as closure evidence. Observed: the existing six-row fixture reproduced the defect, so this fallback was unnecessary.

## Task 2: Apply measured widths and prove stable, legible layout

**Files:** Modify `apps/web/src/features/interested-parties/InterestedPartiesRegisterPage.tsx`; extend the same browser spec. No new public interface.

**Interface:** The existing table remains the sole interactive table. A local `colgroup` precedes its header; Party has no fixed width, and Type, Influence, Status, Last reviewed have numeric widths. Existing component props and row handlers remain on their current elements.

- [x] In the unchanged real browser, harvest the four compact column widths after fonts settle at the 880px table floor and at desktop width. Include every type from `PARTY_TYPE_SINGULAR`, every influence from `INFLUENCE_LABEL` plus Unspecified, both `STATUS_LABEL` values, date/Never, and sortable header labels/icons. Use spec-local typed row overrides for missing type labels, not DOM text replacement.
- [x] Choose whole-pixel widths from the measured floor layout, increasing only allocations whose widest header/button or badge/date does not fit. Record the actual widths, observed content extents, and cell padding in evidence. Validate that their sum leaves Party enough space to display the existing short fixture name `Acme Manufacturing` without truncation at 880px; use the smallest measured allocations that pass containment. If compact-column requirements cannot meet this condition, return measured evidence to the coordinator before changing the 880px contract.
- [x] Add `layout="fixed"` and the local `colgroup` with those four measured widths. Explain briefly beside the widths which content controls them; browser coverage of the current label inventory protects against future changes. Prefer this bounded approach over content-sensitive minimum floors, which leave allocation dependent on the filtered Party text.
- [x] If the fixed layout reveals that the Party button escapes its cell, constrain that existing button locally to the available cell width; keep its accessible name, click target, text clamp, and `data-rownav`. Make no speculative styling changes elsewhere.
- [x] Add `interested-parties keeps all current labels inside their columns at <width>px` at the same four viewports, using seven type rows covering all influences/statuses, null influence, date/Never, short names, long spaced names/expectations, and unbroken strings. Include 4000-character party names and expectations, the limits in `InterestedPartyCreate`/`InterestedPartyUpdate` in `packages/contracts/openapi.yaml`. Import the label maps to assert that the test covers the whole current enum inventory rather than a handpicked longest value.
- [x] In `expectPartyContentContained`, measure header buttons including chevrons, badge roots including glyphs, and their label text with DOM Ranges. Assert each lies within its owning cell's horizontal content bounds (1px tolerance), the label has one rendered line, and its text extents fit the available label area. Assert date/Never is single-line and within its cell. For clamped Party content, assert the button/text box fits the Party cell and cannot overlap Type; full free-text display is not required.
- [x] At 320 and 1115px, assert actual overflow before checking the horizontal scrollbar; poll its visible state as the existing tests do. Scroll the table viewport to its far edge and prove Last reviewed is inside the scrollport. At every viewport assert `document.scrollWidth <= document.clientWidth + 1` and use `measureRegister` with the interested-parties manifest to prove one localized horizontal overflow owner.
- [x] After the final source and spec edits, run `npm --prefix apps/web run build:browser` and require exit 0 before running `(cd apps/web && npm exec -- playwright test register-table-legibility.spec.ts --grep interested-parties)`. The preview serves static `.playwright-dist`, so source edits alone do not change the application exercised. Save before/after vectors and screenshots at 320, 1115, and 1280px outside Git; label this evidence as the freshly rebuilt final source. Expected: stable columns, contained readable compact content, and the final header reachable by table scrolling.
- [x] Remove `layout="fixed"` and the width allocations together in a disposable copy or a locally reverted mutation. Run `npm --prefix apps/web run build:browser` and require exit 0, then run the focused stability case and require a geometry failure for the original movement. Label the evidence with this rebuilt mutation. Restore the final source, rebuild with the same command and require exit 0, then rerun the focused stability case and require GREEN before continuing.
- [x] Separately undersize Influence enough to create real intrusion. Run `npm --prefix apps/web run build:browser` and require exit 0, then run the focused containment case and require its containment failure. Label the evidence with this rebuilt mutation. Restore the final source, rebuild with the same command and require exit 0, then rerun the focused geometry and containment cases and require restored GREEN. Each mutation and each restoration needs its own fresh build; a later successful build cannot validate an earlier mutation run.

## Task 3: Verify and prepare reviewed closure evidence

**Files:** No broader production changes. Coordinator serializes closure updates to `docs/open-residuals.md`, `docs/current-status.md` if needed, and dated `docs/slice-history.md`; workers must not race on those shared authority files.

- [x] Run the existing component/source contracts from the worktree:

  ```bash
  npm --prefix apps/web test -- src/features/interested-parties/InterestedPartiesRegisterPage.test.tsx src/lib/responsiveRegisterContract.test.ts
  ```

  Expected: all pass, including table containment, filter/URL/selection behavior, keyboard behavior where covered, and the exact 880px source literal. These checks supplement browser evidence.

- [x] Run the complete legibility spec plus affected shared browser suites, with exclusive 4174 ownership:

  ```bash
  npm --prefix apps/web run build:browser
  (cd apps/web && npm exec -- playwright test register-table-legibility.spec.ts)
  (cd apps/web && npm exec -- playwright test register-geometry.spec.ts register-accessibility.spec.ts register-header-geometry.spec.ts register-rhythm.spec.ts --grep interested-parties)
  ```

  Expected: exit 0, all selected Chromium cases pass without retries. The first command checks browser TypeScript; rebuilding is required after restoring mutations. Observed: the full legibility spec passed 13 cases; the neighboring selection passed four geometry/rhythm cases and selected no accessibility/header-geometry cases.

- [x] Run `npm --prefix apps/web run lint`, `npm --prefix apps/web run build`, `bash scripts/check-no-site-data.sh`, and `git diff --check`. Expected: exit 0. Supply the coordinator exact commands for full web-suite execution and any additional CI-required gates; record unavailable checks honestly. Application-candidate checks and full web tests passed; the coordinator will rerun closure-documentation guards after this documentation edit.
- [ ] Coordinator obtains independent implementation review, resolves findings, and obtains independent final review before opening a PR. Review the final diff for changes outside the page, spec, and this plan. No dependency or shared style changes belong in it. Independent implementation review approved both axes with no findings; final review and publication are pending.
- [x] Send the coordinator fresh command results, browser vectors, selected widths with measurements, screenshot paths, mutation failures, and any limitations. Only after verified behavior, remove the residual with linked GitHub closure evidence and preserve its dated history; coordinate the issue/PR linkage and documentation edit before publication.
- [ ] If #563 subsequently changes fonts or #561 changes the register scaffold, repeat the final geometry and legibility acceptance on the integrated tree before claiming the combined result stable.

## Execution handoff

The coordinator confirmed dependency hydration completed with all eleven setup commands exiting 0 and tracked locks unchanged. Baseline reproduction and eight measurement probes subsequently ran; widths above are selected from their actual results. The application/test patch is frozen after rebuilt final GREEN (13 cases), intended fluid-column/undersized-Influence failures (four each), rebuilt restorations (nine each), component/source-contract tests (33), neighboring geometry/rhythm browser cases (four), and full web Vitest (2360 tests in 283 files). Lint, browser/production builds, application-candidate site-data/authority guards, and diff checks passed. The neighboring selection included no accessibility or header-geometry cases. Independent implementation review approved both axes with no findings. Narrow closure documentation was then changed. The closure authority guard identified the remaining live current-status residual reference; its sentence now preserves the original deferral and links the dated tested candidate, with CI/merge pending. Current-status frontmatter and baseline counts are unchanged. Final review, refreshed closure guards/hooks, and PR publication remain pending.

The coordinator runs these finite workloads from the assigned worktree, in order:

```bash
bash .superpowers/sdd/2026-10-02-issue-559-register-column-widths/capture-stage.sh final-green
bash .superpowers/sdd/2026-10-02-issue-559-register-column-widths/capture-mutations.sh
```

`capture-stage.sh` compiles the named source state, hashes source and static output, runs browser checks, and records JSON/attachments under that stage's ignored directory. `capture-mutations.sh` requires the initial GREEN, then captures fluid-column RED, restored GREEN, undersized-Influence RED, and restored GREEN with a separate build at every step. Inspect both mutation failure assertions; exit 1 alone does not prove the intended defect. The original baseline artifacts are retained unchanged. The temporary probe has been archived as ignored evidence and removed from the ordinary test tree.

## Follow-up amendment and evidence, 2026-10-10

Independent plan review approved the bounded follow-up for [PR #630](https://github.com/CoJoA13/EasySynQ/pull/630).
Hosted Chromium exposed four complete-inventory containment failures (85 other cases passed):
Type/Influence/Status badges were 97.234375/137.984375/74.484375px, exactly 2.6875px wider than the
earlier local measurements. The cause of that environment difference remains unproven. External
review also established that fixed pixel columns do not grow with the existing rem typography.

This amendment supersedes Task 2's numeric/whole-pixel width instructions. The compact columns now
use `7.5rem / 10rem / 6rem / 8rem` (120/160/96/128px at a 16px root). The first three allocations leave
6.765625/6.015625/5.515625px beyond the observed CI badges and cell padding. Last reviewed keeps its
normal-root allocation and scales with text. Preserve fixed layout, flexible Party, existing clamps,
and the unchanged `minWidth={880}` literal; Mantine converts that floor to 55rem. No shared style,
font, badge, API, harness, dependency, or lock changes belong in this follow-up.

- [x] Add tests before changing production widths. At 320/1280px, two inventory cases verify
  a 20px root, 17.5px body text, 12.5px badge text and the 1100px floor. Two separate 16px-root
  inventory cases add 0.25rem to each left badge-section margin and verify actual 4px margin/width
  growth. Treat this as synthetic rendering allowance, without assuming a font cause. Keep all
  current labels/glyphs, the 1px containment tolerance, and the four default inventory cases.
- [x] Rebuild the unchanged production page and obtain exactly four physical-containment failures,
  without setup errors, skips, retries, or flaky results. Retain source/build identities and raw
  evidence under the coordinator's ignored `630-regression-red` directory.
- [x] Apply only the four rem widths and sizing comment, then rebuild. Add filtering cases with
  a 20px root at 320/1280px. Check scrollbar visibility whenever the table overflows, including
  at 1280px; preserve document containment, short-name readability, and final-column reachability.
- [x] Pass all 19 legibility cases and the complete 95-case Chromium suite, with zero failures,
  skips, retries, or flaky results (147.298 seconds wall time). Inspect table screenshots with a 20px root
  at 320/1280px and measured font/floor/gap evidence. Web lint, production/browser TypeScript builds,
  and 33 affected page/responsive-contract tests also pass on the same source/build identities.
- [x] Diagnose the initial full run's two harness meta-test failures: a coordinator reporter
  environment variable redirected nested JSON output. Correct only report capture and rerun the
  unchanged 95-case suite. The initial 93-pass/two-failure result remains recorded; no repository
  or harness changes were used to obtain the final complete pass.
- [ ] Complete independent implementation/final reviews, refreshed documentation guards, and
  current required GitHub CI before merge. A shared `source-map-js` security dependency gate is
  being repaired separately; local browser proof does not satisfy that outstanding gate.

The updated source preserves integrated main commit `e0531c9a5501f8aacb9d81fc417e59ef82718279`.
Ignored coordinator evidence is under `.superpowers/sdd/2026-10-10-approved-integration/`, with
`630-verification-green` for builds/component checks and `630-browser-capture-corrected` for the
complete browser pass. These results establish the local candidate, not a merged or deployed
outcome. Other browsers, locales, live data, and arbitrary text-size settings remain unverified.
