# S-keyboard-semantic-interaction design

**Status:** Owner approved on 2026-08-13

**Programme:** Programme 1 — frontend resilience and accessibility

**Slice:** 6 of 8 — keyboard and semantic interaction

**Date:** 2026-08-13

**Baseline:** `f2d0b56` (`main`, squash merge of `S-url-state-correctness`)

## 1. Outcome

EasySynQ table rows must remain structural table content. When a row exposes one primary action, a
visible native link or button inside that row owns the action, its accessible name, focus state, and
keyboard activation. The row itself must not simulate a control with `onClick`, `tabIndex`, an
interactive ARIA role, or handwritten Enter/Space handling.

At this baseline, most high-traffic registers already follow that rule and optionally enhance their
primary controls with `useRowKeyboardNav`. The remaining exceptions are the CAPA list view, whose
`Table.Tr` opens a detail drawer, and the Audit Programme table, whose `Table.Tr` selects the programme
that feeds the Plans section. This slice converts those two exceptions and hardens the shared arrow-row
helper around the Audit Programme row's independent Edit action.

The resulting interactions are keyboard-completable with ordinary browser semantics: Tab and Shift+Tab
reach real controls, Enter and Space activate buttons natively, focus remains visibly attributable to
the control, and Arrow Up/Down remains an optional enhancement between primary row controls. Pointer
users activate the visible primary control rather than an invisible row-sized target.

## 2. Current behavior and evidence

The repository-wide source audit at baseline found exactly two `Table.Tr` elements with direct click
handlers:

- `features/capa/CapaBoardPage.tsx` makes each CAPA list row focusable, assigns a pointer cursor, opens
  the drawer from `onClick`, and duplicates button activation in a row-level `onKeyDown` handler; and
- `features/audits/ProgrammePage.tsx` selects a programme from a row-level `onClick`. The row is not
  keyboard focusable, so keyboard users can reach its nested Edit button but cannot select the row to
  change the Plans section.

The rest of the audited interaction inventory already uses native controls:

- Audits, Tasks, Objectives, Management Reviews, and similar page-navigation tables use real links;
- Library, DCR, Improvement, Context, Interested Party, and Risk registers use visible buttons to open
  their drawers;
- board-style Context and Interested Party chips and CAPA cards are native buttons; and
- sortable headers use native buttons with `aria-sort` on the table header.

`useRowKeyboardNav` currently moves focus between `[data-rownav]` elements on Arrow Up/Down. If an arrow
event originates from another focusable descendant in the same body, such as Audit Programme's Edit
button, the helper treats that control as an unknown starting point and moves focus to the first primary
control. That behavior must be narrowed before the Audit Programme table adopts the helper.

The fresh representative baseline passed eight files and 80 tests:

```text
npm --prefix apps/web test -- \
  src/lib/useRowKeyboardNav.test.tsx \
  src/features/capa/CapaBoardPage.test.tsx \
  src/features/audits/ProgrammePage.test.tsx \
  src/features/context/ContextSwotBoard.test.tsx \
  src/features/interested-parties/InterestedPartyTypeBoard.test.tsx \
  src/features/library/LibraryPage.test.tsx \
  src/features/dcr/DcrsRegisterPage.test.tsx \
  src/features/review/TasksInbox.test.tsx
```

The baseline is green because CAPA manually duplicates keyboard activation and the Audit Programme suite
does not assert keyboard selection. Axe alone does not reject a click handler on a structural row or
prove that the intended action is reachable.

## 3. Scope

This slice includes:

- replacing CAPA list row activation with one visible native primary button;
- replacing Audit Programme row activation with one visible native selection button;
- exposing the Audit Programme selection state programmatically;
- adopting the existing arrow-row navigation helper in both tables;
- hardening the helper so it handles arrows only from a marked primary control and never hijacks an
  independent nested action;
- focused RED/GREEN tests for semantics, accessible names, native activation, selection state, arrow
  movement, and nested-action isolation;
- regression coverage for existing drawer URL behavior and loaded-state accessibility; and
- fresh affected-suite, full-web, static, authority, site-data, and diff evidence.

This slice does not include:

- preserving whole-row pointer activation through an overlay, stretched link, delegated click handler,
  interactive row role, or synthetic keyboard handler;
- rewriting tables that already expose a correct native primary control;
- changing the CAPA board-card presentation or Context and Interested Party board chips;
- adding Home/End, Page Up/Page Down, typeahead, selection-following-focus, or grid-widget semantics;
- turning the Audit Programme selection into a route or URL parameter;
- responsive table/card work assigned to Programme 1 slice 7;
- Playwright, browser, forced-colors, screen-reader, or viewport automation assigned to Programme 1
  slice 8;
- API, OpenAPI/generated contract, migration, database, authentication, setup, permission, QueryClient,
  mutation-feedback, notification, dependency, telemetry, deployment, or theme changes; or
- unrelated refactoring, formatting, worktree cleanup, or residual closure.

## 4. Chosen interaction model

The owner selected visible native primary controls over shared interactive-row abstraction or stretched
row controls.

### 4.1 Structural rows and primary controls

A `Table.Tr` represents a row only. It may retain non-interactive visual state such as striping,
hover highlighting, or `data-selected`, but it does not receive focus or activation handlers.

The first identifying cell contains the primary control:

- CAPA List uses a button styled through the existing Mantine `Anchor component="button"` pattern. Its
  visible text is the CAPA identifier (or the existing calm fallback), and its accessible name is fixed
  action copy plus the safe displayed identifier and title, for example
  `Open CAPA REC-000031: Supplier re-evaluation`.
- Audit Programme uses the same native button pattern around the programme identifier. Its accessible
  name is fixed selection copy plus the displayed identifier and title, for example
  `Select programme AUDPROG-000001: 2026 Internal Audit Programme`.

Both names preserve label-in-name by including the visible identifier. No database UUID, raw exception,
permission detail, or other hidden implementation value enters the accessible name.

The Audit Programme button exposes `aria-pressed="true"` for the programme currently feeding the Plans
section and `false` for the others. Activation selects that programme; focus movement alone does not.
The row may continue to expose its existing visual `data-selected` state.

### 4.2 Native activation and focus

Buttons keep browser-native activation:

- Tab and Shift+Tab traverse the primary control and any independent row action;
- Enter and Space activate the focused button without a component-level key handler;
- mouse or touch activation targets the visible button; and
- the existing Mantine focus-visible treatment remains intact because this slice adds no outline or
  focus-style override.

Removing whole-row activation is intentional. It makes the target discoverable, prevents nested action
collisions, and avoids an interactive descendant inside a simulated interactive row. No click on an
ordinary data cell opens or selects an item.

### 4.3 Arrow-row enhancement

Both primary buttons carry `data-rownav`. Their `Table.Tbody` elements attach the existing
`useRowKeyboardNav` ref and key handler.

Arrow Down moves focus from a marked primary control to the next marked primary control, clamped at the
last row. Arrow Up moves to the previous primary control, clamped at the first row. Arrow movement does
not activate the destination or change Audit Programme selection.

The helper first verifies that the event originated from the currently focused marked primary control.
If focus is on Edit or another unmarked descendant, Arrow Up/Down is ignored and its default behavior is
not prevented. Other keys remain untouched. This contract applies to all existing helper consumers and
prevents the new Audit Programme integration from changing independent control behavior.

No roving `tabIndex` is introduced: every primary link or button remains in ordinary document tab order.
The helper is an enhancement, not a composite grid widget.

## 5. Feature behavior

### 5.1 CAPA list

Board view remains unchanged: each `CapaCard` is already one native button.

In List view:

1. the row renders as structural table content;
2. the identifier cell renders the primary Open CAPA button;
3. activating that button calls the existing `setSelected(capa.id)` path;
4. the list open remains local-only and leaves the URL untouched, while externally supplied `?capa=`
   state retains the shipped live synchronization and removal semantics; and
5. `CapaDrawer` retains its existing dialog focus, Escape close, selector-removal, conflict, and focus
   restoration ownership.

No request, permission gate, drawer component, filter, view selector, URL policy, or card behavior
changes.

### 5.2 Audit Programme

The programme table retains the existing default selection, status badge, permission-gated Edit button,
and selected-row presentation.

1. the identifier cell renders the primary Select programme button;
2. activating it updates `selectedId` through the existing state path;
3. the Plans query and `Plans — <identifier>` section follow the selected programme exactly as before;
4. the selected primary button reports `aria-pressed="true"`; and
5. Edit remains an independent native button whose activation opens the form without changing the
   selected programme through event bubbling.

The slice does not make selection shareable or persistent. Page reload continues to select the existing
default programme.

## 6. Failure and state boundaries

No new asynchronous operation or error surface is introduced.

- CAPA list, drawer, and permission failures keep their current feature-owned states.
- Audit programme list, Plans query, form mutation, empty, forbidden, and archived-programme behavior
  remains unchanged.
- A missing identifier continues to use the existing visible fallback; the accessible name uses the
  same safe displayed fallback and title rather than the opaque ID.
- Filtering or refetching may remove the focused control under the existing React lifecycle. This slice
  does not invent post-removal focus restoration; drawer close retains its feature-owned restoration
  contract.
- Native button activation is not duplicated by row bubbling, so one user action produces one state
  transition.

## 7. Test design

Implementation starts with focused failing tests before production edits.

### 7.1 Shared helper

Extend `useRowKeyboardNav.test.tsx` with a table containing marked primary buttons and an unmarked nested
Edit button. Prove:

- Arrow Down/Up continues to move and clamp between marked controls;
- arrow movement does not click or select a destination;
- arrows from Edit neither move focus nor call `preventDefault`; and
- Enter and Space remain outside the helper's behavior.

### 7.2 CAPA

Extend `CapaBoardPage.test.tsx` to prove:

- List rows have no `tabindex`, interactive role, or row activation handler observable through pointer
  behavior;
- the visible primary button has the expected Open CAPA name;
- Enter and Space each open the drawer through native button activation;
- Arrow Down moves focus to the next CAPA primary button without opening it;
- clicking a non-action data cell does not open a drawer;
- the existing `?capa=` write, deep link, Escape close, and external selector synchronization remain
  unchanged; and
- the loaded List state remains axe-clean.

### 7.3 Audit Programme

Extend `ProgrammePage.test.tsx` to prove:

- programme rows remain structural and are not focus targets;
- selection buttons have useful names and exactly one reports pressed at a time;
- Enter and Space select another programme and update the Plans heading/data;
- Arrow movement changes focus but not selection;
- activating Edit opens the form while leaving programme selection unchanged;
- pointer activation of an ordinary cell does not select the programme; and
- the loaded table and selected Plans state remain axe-clean.

Existing tests that click bare identifier text must be updated to activate the corresponding named
button when the intent is selection. Tests whose intent is only to assert visible data continue to query
the visible text.

### 7.4 Preservation selection

Run focused adjacent suites for native-control registers and board chips so the shared helper change does
not regress established behavior. The minimum selection includes:

- `useRowKeyboardNav.test.tsx`;
- `CapaBoardPage.test.tsx` and CAPA routing tests;
- `ProgrammePage.test.tsx`;
- representative linked and drawer-backed registers; and
- Context, Interested Party, and CAPA board-card tests.

## 8. Acceptance criteria

The slice is complete only when all of the following are true:

1. No production `Table.Tr` owns `onClick`, `tabIndex`, an interactive role, or handwritten activation.
2. CAPA List exposes one visible native drawer-opening control per row with a safe, useful accessible
   name.
3. Audit Programme exposes one visible native selection control per row and programmatically identifies
   the selected programme.
4. Enter and Space activate both primary button families natively.
5. Arrow Up/Down moves focus only between marked primary controls and never activates them.
6. Arrow keys on Audit Programme's Edit button are not intercepted by row navigation.
7. Ordinary cell clicks do not activate a row.
8. CAPA drawer URL synchronization, focus ownership, and Back/Forward behavior remain unchanged.
9. Audit default selection, Plans loading, archived behavior, and Edit flow remain unchanged.
10. Loaded representative states pass axe with no violation.
11. The operational QueryClient/provider identity, URL-state classifier, route chrome/recovery,
    mutation-feedback lifetime, auth/setup gates, and 404 behavior remain unchanged.
12. No API, contract, migration, permission, dependency, lockfile, or deployment change ships.

## 9. Verification and evidence

The implementation handoff records exact fresh commands and results for:

- focused RED/GREEN tests for the helper, CAPA List, and Audit Programme;
- the affected adjacent web selection;
- web lint, typecheck, scoped Prettier, and production build;
- the complete web suite through a durable process job if its runtime remains above one minute;
- repository authority and Claude compatibility fixtures;
- site-data fixtures and direct scan;
- diff and clean-worktree guards; and
- independent task and whole-branch review with every in-scope Important finding resolved.

The current host's contributor doctor reports Node is not major 22, PostgreSQL client is absent, and
Docker runtime access fails. `just setup` nevertheless completed from the committed locks with CPython
3.12.13, 455 web packages and 34 contract packages, zero npm vulnerabilities, unchanged generated
contracts, and only the existing MSW install-script, uv cross-filesystem-copy, and generator-deprecation
warnings. No unavailable browser, screen-reader, responsive, Fedora, Docker-backed, API, contract, or
integration proof may be described as passed.

## 10. Rejected alternatives

### 10.1 Shared primary-row component

A shared component could standardize the two buttons, but the action copy, state, cell content, and
side effects differ. Two call sites do not justify a new abstraction. The existing Mantine Anchor-button
and `data-rownav` conventions already provide the reusable vocabulary.

### 10.2 Stretched or overlaid whole-row control

A stretched link/button would preserve the large pointer target, but the Audit Programme row also owns
an independent Edit action. Overlay stacking, click exclusion, focus indication, and accessible hit-area
behavior become fragile. The visible primary control is the clearer boundary.

### 10.3 Interactive row with ARIA and synthetic keys

Adding `role="button"`, `tabIndex`, Enter/Space handling, and nested-control event suppression to the row
would reproduce browser semantics incompletely and retain an invalid conceptual mix of table structure
and control behavior. CAPA's current implementation demonstrates this duplication, and Audit Programme's
current implementation demonstrates the keyboard gap. The owner rejected this approach.

## 11. Follow-on boundary

Programme 1 slice 7 owns intentional narrow-screen strategies for data-heavy routes. Programme 1 slice 8
owns Playwright, request-intercepted failure proofs, real focus-ring/forced-colors inspection, viewport
coverage, and screen-reader-oriented browser evidence. This slice supplies native semantic controls that
those later browser proofs can exercise; it does not absorb either follow-on.
