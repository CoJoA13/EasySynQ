# S-responsive-data-heavy-views design

**Status:** Owner approved on 2026-08-13

**Programme:** Programme 1 — frontend resilience and accessibility

**Slice:** 7 of 8 — responsive data-heavy views

**Date:** 2026-08-13

**Baseline:** `082ba310` (`main`, squash merge of `S-keyboard-semantic-interaction`)

## 1. Outcome

EasySynQ's shared register cohort must remain usable at a 320 CSS-pixel viewport without making the
document itself horizontally scrollable. Wide record comparisons stay semantic tables and scroll only
inside page-owned table containers. Search and filter controls remain reachable inside a bounded toolbar
and retain their existing order, accessible names, state, and history behavior.

The approved cohort is `/tasks`, `/audits`, `/dcrs`, `/objectives`, `/management-reviews`,
`/improvement`, `/risks`, `/context`, and `/interested-parties`. These routes share the register toolbar
and a five-to-seven-column table, but none currently owns a table scroll boundary. The slice updates that
coherent family directly rather than treating every Mantine table as one problem.

At Mantine's existing `sm` breakpoint and above, the current table presentation, columns, toolbar layout,
and 260 px search width remain visually unchanged. Below `sm`, the search control can use the available
width, filters stay within a bounded lane, and each table retains a route-specific content floor inside a
localized horizontal scroll container. No card alternative, hidden column, reordered value, or duplicate
interactive presentation is introduced.

## 2. Current behavior and audit evidence

The baseline source inventory contains roughly 32 production TSX files rendering Mantine tables. Only
four routed data-heavy pages already wrap their primary table in `Table.ScrollContainer`:

- `/library` at a 720 px minimum;
- `/compliance` at a 720 px minimum;
- `/capa/ncrs` at a 640 px minimum; and
- `/reports/document-control` at a 1500 px minimum.

The shared register cohort has a distinct, repeated shape:

| Route                 | Primary table shape | Primary interaction                      |
| --------------------- | ------------------- | ---------------------------------------- |
| `/tasks`              | Five columns        | Native subject link                      |
| `/audits`             | Five columns        | Native audit link                        |
| `/dcrs`               | Seven columns       | Native drawer-opening identifier button  |
| `/objectives`         | Five columns        | Native objective link                    |
| `/management-reviews` | Five columns        | Native review link                       |
| `/improvement`        | Six columns         | Native drawer-opening identifier button  |
| `/risks`              | Five columns        | Native drawer-opening description button |
| `/context`            | Five columns        | Native drawer-opening issue button       |
| `/interested-parties` | Five columns        | Native drawer-opening party button       |

Each route uses `RegisterToolbar`, sortable header buttons, or both. `RegisterToolbar` already permits
groups to wrap, but its search input is always 260 px wide. Wide segmented controls on Objectives, Risk,
Context, and Interested Parties create additional pressure below `sm`. The tables themselves have no
localized overflow owner, so their complete column set can widen the page or become clipped by an ancestor.

Specialized routed surfaces do not form the same safe implementation unit:

- CAPA List, Complaints, and Audit Programme combine table navigation or selection with feature-specific
  actions and, for Programme, a second dependent table;
- ingestion triage has nine columns, a sticky header, and several review actions;
- superseded-copy, admin, and document-detail tables have different containers and workflow ownership; and
- CAPA board already uses a feature-owned horizontal `ScrollArea`.

Those surfaces need their own action-discoverability and presentation decisions. They are not pulled into
this slice merely because they render a table.

Fresh setup from the committed locks installed CPython 3.12.13, 455 web packages, and 34 contract packages
with zero npm vulnerabilities. The representative baseline covering `RegisterToolbar`, AppShell,
DetailDrawer, and the four existing scroll-container routes passed seven files and 90 tests. The contributor
doctor still reports Node 26 rather than 22, no PostgreSQL client, and unavailable Docker runtime access.
No unavailable browser, Docker-backed, API, contract, integration, migration, or Fedora proof may be
described as passed.

## 3. Scope

This slice includes:

- localized horizontal table containment on the nine approved routes;
- a sub-`sm` responsive width contract for the shared register search control;
- a bounded filter lane that contains oversized segmented controls without duplicating them;
- direct, route-owned content-width floors;
- focused RED/GREEN tests for the shared toolbar and all nine route contracts;
- preservation coverage for sorting, URL-backed controls, native primary actions, row navigation, drawer
  ownership, linked navigation, and loaded-state accessibility;
- regression coverage for the four existing scroll-container routes; and
- fresh affected-suite, complete-web, static, authority, site-data, diff, and review evidence.

This slice does not include:

- CAPA List, Complaints, Audit Programme, ingestion triage, superseded-copy, admin, or document-detail
  table changes;
- cards, stacked records, priority-column hiding, column reordering, sticky action overlays, or a switcher
  between desktop and mobile presentations;
- a universal responsive-table abstraction or a second shared table API;
- new breakpoints, dependencies, theme tokens, or global overflow suppression;
- Playwright, viewport automation, request interception, real focus-ring inspection, forced-colors
  inspection, or screen-reader-oriented browser evidence;
- API, OpenAPI/generated contract, migration, database, permission, authentication/setup, QueryClient,
  mutation-feedback, notification, telemetry, deployment, or lockfile changes; or
- unrelated refactoring, formatting, worktree cleanup, or residual closure.

## 4. Responsive architecture

### 4.1 Existing breakpoint vocabulary

The narrow contract is usable at 320 CSS pixels. Responsive changes activate below Mantine's existing
`sm` breakpoint. The slice adds no media-query value or parallel breakpoint vocabulary.

At `sm` and above, the shared search input remains 260 px wide and the existing flex layout remains in
effect. The scroll containers are inert when their available width exceeds the table floor, so desktop
content retains its present geometry and order.

### 4.2 Shared register toolbar

`RegisterToolbar` keeps its current props and callers. Its search input gains a responsive width:

- below `sm`, width is 100% and minimum width is zero so it can shrink within the page canvas; and
- at `sm` and above, width is the existing `searchWidth` value, defaulting to 260 px.

The search input remains first in DOM and keyboard order. The children filter lane remains after it,
wraps where individual controls permit wrapping, and owns localized horizontal containment when a single
segmented control is wider than the available canvas. The count remains last and retains its polite live
region. No child is cloned, conditionally hidden, portaled into a second presentation, or removed from the
accessibility tree.

### 4.3 Page-owned table containment

Each approved route directly wraps its existing table with Mantine `Table.ScrollContainer`. This is a
presentation boundary only: the existing `Table`, `Table.Thead`, `Table.Tbody`, structural rows, headers,
cells, and controls remain the sole semantic and interaction tree.

The approved content floors are:

| Routes                            | Table minimum width |
| --------------------------------- | ------------------: |
| `/tasks`, `/objectives`, `/risks` |              720 px |
| `/audits`, `/management-reviews`  |              800 px |
| `/context`, `/interested-parties` |              880 px |
| `/improvement`                    |              920 px |
| `/dcrs`                           |             1040 px |

These floors reflect the existing number and content density of complete columns. They are deliberately
route-owned rather than inferred by a new abstraction. The direct implementation and its deferred payoff
trigger are registered in
[`20260813144730-responsive-register-cohort`](../../debt/20260813144730-responsive-register-cohort.md).

## 5. Interaction and data flow

Responsive layout does not create a second data or state path.

1. Existing query hooks continue to load the same records and expose the same loading, forbidden, error,
   empty, and loaded states.
2. Existing URL-backed search, filters, and sort controls continue to update the same parameters with the
   shipped replacement-history policy.
3. Existing derived rows continue to feed one table body.
4. The page-owned scroll container affects only horizontal presentation when the table floor is wider than
   the available content area.
5. Native primary links and buttons continue to own activation. Drawer-backed routes keep one local/URL
   synchronization owner; linked routes keep their existing destinations.

All columns remain available in their current order. Horizontal panning is the only narrow-screen access
mechanism for later columns. Tab and Shift+Tab retain DOM order. `data-rownav` primary controls retain their
Arrow Up/Down focus-only enhancement, and containment does not install a competing keyboard handler.

Sortable headers retain their native buttons and `aria-sort`. Audit Programme's `aria-pressed` behavior is
outside the edited cohort and remains unchanged. No row becomes interactive, no ordinary cell gains an
action, and no hidden duplicate control remains in the accessibility tree.

## 6. Failure and compatibility boundaries

No asynchronous operation, retry path, or failure state is added. The responsive boundary cannot swallow
or replace feature-owned errors, empty states, permission refusals, mutation feedback, route recovery, or
404 behavior.

The document itself must not gain horizontal overflow because of an in-scope register at 320 px. Wide
table content scrolls within its table container, and oversized filter content remains inside the toolbar's
bounded lane. The implementation must not use a global `overflow-x: hidden` workaround because that would
clip unrelated content and conceal failures.

The following shipped contracts remain invariant:

- structural `Table.Tr` elements and visible native primary controls;
- complete accessible names and `aria-sort` state;
- Arrow Up/Down focus movement only from actively focused `data-rownav` controls;
- independent row actions and inert ordinary cells;
- effective-view classification and URL/history semantics;
- route chrome, route recovery, and feature-owned drawer focus behavior;
- operational QueryClient/provider identity and mutation-feedback lifetime;
- authentication/setup gates, permission checks, error boundaries, and operational 404 behavior; and
- API, contract, database, deployment, and dependency behavior.

## 7. Test design

Implementation starts with focused failing proofs before production edits.

### 7.1 Shared toolbar

Extend `RegisterToolbar.test.tsx` to prove:

- the search input has the approved full-width, zero-minimum-width narrow contract;
- the existing `searchWidth` remains the desktop width contract;
- filter content has one bounded lane and retains child order;
- the search label and result-count live region remain unchanged; and
- no duplicate search or filter control is rendered.

The test pins component structure and responsive styling inputs. jsdom does not prove browser layout or a
media query's rendered geometry.

### 7.2 Route contracts

Extend each approved page suite to require exactly one page-owned scroll container around the existing
table with the approved minimum width. Representative assertions prove:

- one table and one ordered header set remain;
- each representative native primary action appears exactly once;
- sort, filter, and URL behavior remains unchanged;
- drawer-backed and linked activation keeps its existing owner and destination;
- `data-rownav` controls and keyboard order are not replaced; and
- loaded representative states remain axe-clean.

A source-level guard covers the exact nine-route inventory so an approved table cannot silently lose its
containment or acquire a parallel mobile presentation without updating the explicit contract.

### 7.3 Preservation selection

The affected selection includes:

- `RegisterToolbar` and `useRowKeyboardNav`;
- the nine approved route suites;
- URL/history and drawer tests adjacent to the edited routes; and
- Library, Compliance, NCR, and Document Control Report suites as existing-scroll preservation controls.

## 8. Acceptance criteria

The slice is complete only when all of the following are true:

1. Each approved route renders one semantic table inside one localized table scroll container with its
   approved minimum width.
2. At 320 px, the source and style contract gives the search input the available width and bounds oversized
   filter content without adding a second control presentation.
3. At `sm` and above, the search width, table columns, toolbar order, and desktop presentation remain
   unchanged.
4. Every column and representative row action remains available once, in its original order, with its
   original accessible name and state.
5. Sorting, URL-backed search and filters, replacement-history behavior, native activation, drawer state,
   linked navigation, and row-keyboard behavior remain unchanged.
6. Structural rows stay inert and native controls remain visibly attributable.
7. Loaded representative states pass axe with no violation.
8. The four existing scroll-container routes retain their shipped behavior.
9. Specialized out-of-scope tables receive no production change.
10. The application has no new global overflow suppression, responsive-table abstraction, dependency,
    duplicate state owner, or hidden duplicate interactive control.
11. The operational QueryClient/provider identity, URL-state classifier, route recovery, mutation-feedback
    lifetime, auth/setup gates, permissions, and error/404 boundaries remain unchanged.
12. No API, contract, migration, database, lockfile, telemetry, notification, or deployment change ships.

## 9. Verification and evidence

The implementation handoff records exact fresh commands and results for:

- focused RED/GREEN toolbar and per-route responsive contract tests;
- the affected preservation selection;
- web lint, typecheck, scoped Prettier, and production build;
- the complete web suite through a durable process job if it remains longer than one minute;
- repository authority and Claude compatibility fixtures;
- site-data fixtures and direct scan;
- range and working-tree-inclusive diff guards; and
- task-level and whole-branch review with every in-scope Important finding resolved.

Slice 7 may set a simulated `window.innerWidth` to exercise component choices, but it records only a unit
contract. It must not describe jsdom as viewport, clipping, scrolling, or visual proof.

Programme 1 slice 8 exclusively owns Playwright, real browser viewport measurement, actual clipping and
scroll reachability, request-intercepted failures, focus-ring and forced-colors inspection, and
screen-reader-oriented browser evidence. The current host limitations also prevent Docker-backed, API,
contract, integration, migration, and Fedora acceptance from being claimed here.

## 10. Rejected alternatives

### 10.1 Operational hotspot cohort

CAPA, Complaints, Audit Programme, Tasks, and ingestion triage would target important workflows, but their
actions, selection, dependent content, and sticky triage behavior require different responsive decisions.
Combining scrolling and stacked records in one slice would increase interaction and accessibility risk
without establishing a coherent reusable contract.

### 10.2 Broad scroll retrofit

Wrapping every routed table would be mechanically simple but would substitute one overflow treatment for
intentional per-surface design. It would also place final-column actions offscreen on specialized workflows
without resolving discoverability. The source inventory is evidence for selection, not approval to edit
every table.

### 10.3 Cards or priority columns for the shared cohort

Cards or hidden low-priority columns could reduce horizontal panning, but they would either duplicate the
record/control tree or replace table comparison semantics. The shared cohort benefits from cross-row and
cross-column comparison, and the owner chose complete table content with localized scrolling.

### 10.4 Universal responsive-table abstraction

A wrapper could centralize breakpoints and widths, but the repository has only one approved routed cohort
and several specialized surfaces with incompatible action models. Direct Mantine containers make ownership
explicit now. The debt payoff trigger requires reconsideration when a second cohort needs the same contract
or a specialized surface can adopt it safely.

## 11. Follow-on boundary

Slice 8 will exercise the resulting single semantic/control tree in real browsers at representative narrow
and desktop viewports. It will measure page and container overflow, verify that later columns and actions are
reachable, inspect real focus indication and forced colors, intercept failures, and collect
screen-reader-oriented evidence. Slice 7 supplies the intentional responsive structure and unit-level
preservation contract; it does not pre-claim that browser evidence.
