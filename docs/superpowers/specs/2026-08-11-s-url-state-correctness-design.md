# S-url-state-correctness design

**Status:** Owner approved on 2026-08-11

**Programme:** Programme 1 — frontend resilience and accessibility

**Slice:** 5 of 8 — URL-state correctness

**Date:** 2026-08-11

**Baseline:** `88f5fb0` (`main`, squash merge of `S-mutation-feedback`)

## 1. Outcome

EasySynQ must treat a URL query parameter according to the product state it represents. A recognized
parameter that selects a materially different page view, detail surface, tab, or mode becomes part of
that view's effective identity. A search term, facet, sort, or page number remains ordinary working
state. Both categories remain shareable and react to external navigation, but only a meaningful view
transition changes the corresponding chrome, recovery, focus, or announcement behavior.

The canonical transition is `/tasks` to `/tasks?type=DOC_ACK`. The visible content, document title,
focus, and one polite announcement must intentionally change to Acknowledgements. Back must restore the
general Tasks view with the same guarantees. Initial deep links set the correct content and title but do
not steal focus or emit a mount-time navigation announcement.

Raw query values never become document titles, live-region text, headings, accessible names, or error
copy. The slice preserves authentication and setup precedence, the visible 404 contract, route-error
recovery, the original QueryClient/provider identity and lifecycle, and route-persistent mutation
feedback.

## 2. Current behavior and defects

`useRouteChrome` observes only `pathname`. It therefore assigns both `/tasks` and
`/tasks?type=DOC_ACK` the title `EasySynQ — Tasks` and does not treat their live transition as
navigation, although `TasksInbox` swaps between `GeneralTasksInbox` and `AckInbox` on the same mounted
route element.

`AppShell` currently resets its route-content error boundary from the complete
`pathname + search + hash`. Every search, facet, sort, pagination, tab, and drawer edit therefore clears
a captured page failure, even when the operator only changed ordinary list state.

Existing feature behavior is uneven:

- shared debounced search and table sorting already use replacement semantics and adopt external URL
  changes without writing stale local values back;
- Library facets and pagination are URL-backed but currently push a history entry for every edit;
- Library `detail`, Context `issue`, Risk `risk`, and Interested Parties `party` follow both the
  appearance and removal of their URL selector;
- DCR `dcr`, CAPA `capa`, and Improvement `initiative` open when their selector appears but retain stale
  local drawer state when Back or Forward removes it;
- document tabs and document/DCR diff modes react to live search changes, but an unknown document tab
  can select no panel and unknown comparison IDs can reach viewer requests; and
- feature tests prove several cold deep links and ordinary control transitions, but no integrated
  contract distinguishes effective view navigation from working-state edits.

## 3. Scope

This slice includes:

- one pure, typed effective-view classifier for operational routes;
- safe document-title, focus, announcement, and route-recovery policies derived from that classifier;
- intentional `/tasks` versus `/tasks?type=DOC_ACK` live navigation;
- parameter-specific recovery identity for recognized drawers, tabs, modes, and comparison pairs;
- Back/Forward-correct drawer synchronization for every existing URL-backed drawer;
- safe defaults for unknown task types, document tabs, modes, and loaded-version comparison IDs;
- replacement history semantics for ordinary filters, search, sort, pagination, tabs, modes, and
  comparison controls;
- push semantics for intentional material navigation and Library detail opening;
- focused accessibility and preservation tests; and
- fresh affected-suite evidence and authority-document updates.

This slice does not include:

- new routes or migration of query views to path segments;
- a new global URL store or rewrite of existing feature hooks;
- making currently local-only CAPA, DCR, Improvement, Context, Risk, or Interested Party row opens
  URL-backed;
- keyboard-row semantics, responsive data-table work, or the Playwright harness assigned to later
  Programme 1 slices;
- changes to API handlers, OpenAPI/generated contracts, migrations, database models, Keycloak, setup,
  authentication, notification delivery, or mutation retry policy;
- arbitrary titles derived from loaded entity identifiers or query values;
- hash-fragment navigation policy; or
- unrelated residual closure, formatting, dependency, or deployment work.

## 4. Chosen architecture

The owner selected a typed effective-view classifier over page-owned chrome registration or a route-path
migration. The decision is recorded in `docs/adr/0001-centralize-effective-url-view-classification.md`.

### 4.1 Pure classification

A focused `lib/effectiveView.ts` module accepts a pathname and `URLSearchParams` and returns an immutable
view description. It does not read React context, load application data, mutate history, focus DOM
nodes, or normalize the address bar.

The public shape is intentionally small:

```ts
export type QueryStateClass =
  | "material-view"
  | "detail"
  | "subview"
  | "ordinary"
  | "ignored";

export interface EffectiveView {
  title: string;
  chromeKey: string;
  recoveryKey: string;
  queryStateClass: QueryStateClass;
  focusOwner: "route-main" | "feature" | "none";
  announcement: string | null;
}

export function classifyEffectiveView(
  pathname: string,
  searchParams: URLSearchParams,
): EffectiveView;
```

`title` is always fixed application copy. `chromeKey` changes only when title or global live-navigation
policy changes. `recoveryKey` changes for every recognized state that materially replaces route content
and remains stable for ordinary working-state edits. Opaque IDs may participate in the internal recovery
key so switching detail identities clears a stale route error, but they never participate in visible
copy.

Unmatched pathnames return the existing fixed Page not found description. Startup and setup screens keep
their existing precedence; classification does not authorize operational content before those gates.

### 4.2 React ownership

`useRouteChrome` consumes the classifier using `pathname` and `search`. On the first render it sets the
safe title only. It retains the existing rule that a live known-pathname transition focuses
`#main-content`, while adding the same main-focus behavior for the material Tasks/Acknowledgements query
transition.

`RouteChromeProvider` gains a small current-announcement value. `RouteAnnouncement`, mounted once in
`AppShell`, exposes that value through a visually hidden polite, atomic status region. Only a live
material query transition writes `Tasks` or `Acknowledgements` to this region. Initial deep links,
ordinary edits, drawer opens, tab/mode changes, and ignored values write nothing.

Drawers and dialogs remain their own focus and accessible-announcement owners. The classifier changes a
safe document title for a recognized detail URL but does not compete with the feature's focus trap.
Tabs and segmented controls retain native selected-state announcement and do not trigger route-main
focus.

Route-error chrome remains authoritative while a route fallback is mounted. `useRouteChrome` compares
effective recovery identity rather than raw search text so it can restore the appropriate known view
after meaningful navigation without reacting to ordinary filter edits.

`AppShell` passes `EffectiveView.recoveryKey` to `ApplicationErrorBoundary`. It continues to place
`MutationFeedbackOutlet` outside that boundary. No provider or QueryClient is added, removed, remounted,
or replaced.

## 5. Query-state classification

### 5.1 Material page view

Only the recognized task selector is a material query-selected page view in this slice:

| Location | Effective title | Live focus owner | Live announcement |
|---|---|---|---|
| `/tasks` | `EasySynQ — Tasks` | route main | `Tasks` |
| `/tasks?type=DOC_ACK` | `EasySynQ — Acknowledgements` | route main | `Acknowledgements` |

An absent or unknown `type` resolves to the general Tasks view. Unknown values are left in the address
bar for forward compatibility but do not enter any visible copy or effective key.

### 5.2 Detail and drawer selectors

The following non-empty selectors identify a detail surface and contribute their opaque value to the
internal recovery key:

| Route | Parameter | Safe title |
|---|---|---|
| `/library` | `detail` | `EasySynQ — Document details` |
| `/dcrs` | `dcr` | `EasySynQ — Change request details` |
| `/capa` | `capa` | `EasySynQ — CAPA details` |
| `/improvement` | `initiative` | `EasySynQ — Improvement details` |
| `/context` | `issue` | `EasySynQ — Context issue details` |
| `/interested-parties` | `party` | `EasySynQ — Interested party details` |
| `/risks` | `risk` | `EasySynQ — Risk details` |

The feature validates or loads the opaque ID through its existing safe API state. The classifier neither
guesses ID syntax nor deletes the value before authoritative feature data is available. Missing,
forbidden, or invalid entities continue to use fixed feature error copy.

Every listed drawer adopts selector changes and removal while mounted. Synchronization effects depend on
the one selector value, not the complete search object, so an ordinary filter edit does not close a
locally opened drawer. Existing local-only opens remain local-only except Library, whose identifier
action already establishes a shareable detail URL.

### 5.3 Subviews

The following recognized values contribute to `recoveryKey` but retain the base route title and do not
invoke global focus or the route announcer:

- `/documents/:id?tab=`: `overview`, `history`, `approvals`, `where-used`, or `acks`;
- `/documents/:id?mode=`: `text` or `visual` for version comparison;
- `/documents/:id?from=<version>&to=<version>`: non-empty opaque comparison identities; and
- `/dcrs/:id/diff?mode=`: `text` or `visual`.

Unknown enum values resolve to the documented default (`overview` or `text`) without mount-time URL
rewrites. The pure classifier includes non-empty `from` and `to` identities only in the internal recovery
key; it does not claim that they belong to the loaded document. Once document versions are loaded, the
feature resolves an unknown `from` or `to` value to the normal prior-to-newest default pair rather than
issuing viewer work for an unknown ID. The raw URL remains untouched so a temporarily unavailable but
later valid value is not destroyed prematurely. An invalid comparison intent may therefore reset a stale
route failure once, but it cannot change visible chrome or escape feature validation.

### 5.4 Ordinary working state

The following parameter families remain shareable working state and do not change title, chrome key,
recovery key, main focus, or route announcements:

- `q` and feature search terms;
- register facets, including state, status, type, owner, clause, effective-date bucket, process, RAG,
  risk band/type, context classification/category, interested-party dimensions, DCR dimensions, and
  improvement dimensions;
- `sort` and `dir`;
- `offset` and `size`; and
- ingestion review `queue`, `conf`, and `offset`.

Each feature continues to own validation against its visible or permission-filtered option universe.
Raw ordinary values may safely fall back, wait for validation, or produce no matches according to the
existing feature contract, but they never become global accessible copy.

All unrecognized parameters are ignored by effective-view classification and preserved in the URL.

## 6. History and external-navigation contract

- Intentional navigation to another path or to the Acknowledgements material view uses normal push
  semantics so Back returns to the prior product view.
- Clicking a Library document identifier pushes `detail=<id>`, making Back close the newly opened
  drawer and restore the prior list URL.
- Explicit drawer Close removes its selector with replacement semantics. It does not add a history
  entry that immediately reopens the drawer.
- Ordinary filters, search, sort, pagination, tabs, modes, and comparison controls use replacement
  semantics. Rapid triage does not create a history step for every keystroke or column click.
- External links and browser Back/Forward remain authoritative. Controlled inputs, result sets,
  selected tabs/modes, comparison pairs, and URL-seeded drawers adopt the resulting location while the
  route stays mounted.
- No component rewrites a newly received external value with stale local state.

The classifier describes current effective state; it does not call `navigate` or dictate whether the
location was reached through push, replace, Back, Forward, or an external deep link.

## 7. Focus and announcement contract

- Initial operational deep links set the correct safe title without route-main focus or a navigation
  announcement.
- A live `/tasks` ↔ `/tasks?type=DOC_ACK` transition focuses `#main-content` once and emits one polite,
  atomic announcement naming the destination view.
- A live pathname transition retains the existing known-route main-focus behavior.
- An ordinary query edit leaves focus in the active search, filter, sort, pagination, or other control
  and emits no page-navigation announcement.
- A detail/drawer URL transition changes the safe title and recovery identity, while the mounted drawer
  owns focus trapping, dialog naming, and focus restoration.
- A tab, diff mode, or comparison change leaves global focus alone; its native control owns selected
  state.
- Route error and not-found presentations retain their existing focused headings and fixed announcement
  semantics. Query values never override them.

## 8. Error recovery and preservation boundaries

The route-content boundary resets when:

- pathname changes;
- the Tasks/Acknowledgements material view changes;
- a recognized detail selector appears, changes identity, or disappears; or
- a recognized tab, mode, or non-empty comparison identity changes.

It does not reset for ordinary working state, unknown query parameters, ignored values, parameter order,
or hash fragments.

Reset continues to remount only the failed route subtree. It calls no QueryClient invalidation,
refetch, reset, removal, cancellation, or clearing method; changes no provider/client identity or
lifecycle; issues no mutation; and does not remove route-persistent mutation feedback. Normal TanStack
Query stale-observer behavior after a legitimate subtree remount remains unchanged.

Authentication startup, setup-state startup, finalization verification, token redirect behavior,
legacy ingestion redirects, known routing, and shell-contained 404 behavior retain their existing
precedence and safe copy.

## 9. Expected file ownership

Expected production surfaces are bounded to:

- `apps/web/src/lib/effectiveView.ts` — pure typed classification and safe fixed labels;
- `apps/web/src/lib/routeChrome.ts` — effective-view title, transition, focus, and announcement use;
- `apps/web/src/app/shell/AppShell.tsx` — effective recovery key and mounted route announcer;
- `apps/web/src/lib/registerControls.ts` — existing ordinary replacement/external-sync contract only if
  a shared helper is needed;
- `apps/web/src/features/review/TasksInbox.tsx` — safe task selector normalization;
- `apps/web/src/features/library/LibraryPage.tsx` — ordinary replace semantics and detail push/close;
- `apps/web/src/features/document/DocumentDetailPage.tsx` and `VersionCompare.tsx` — safe subview
  normalization and replacement semantics;
- `apps/web/src/features/dcr/DcrsRegisterPage.tsx`, `CapaBoardPage.tsx`, and
  `ImprovementRegisterPage.tsx` — parameter-removal synchronization;
- existing Context, Risk, and Interested Parties files only if common drawer-sync tests or a focused
  helper make a change necessary; and
- `apps/web/src/features/dcr/DcrDiffPage.tsx` — replacement semantics for diff mode.

Tests stay beside these units. No broad route-table, shell, register, or drawer refactor is authorized.

## 10. Test contract

Implementation starts with focused failing proofs.

### 10.1 Classifier and route chrome

- classification ignores parameter order and unrelated values;
- Tasks and Acknowledgements have distinct safe chrome/recovery keys and exact titles;
- unknown task types resolve to Tasks without echoing the value;
- every detail selector gets a generic safe title and distinct recovery identity;
- recognized subviews change recovery identity without changing title/focus policy;
- ordinary state leaves chrome and recovery identity stable;
- initial deep links do not focus or announce;
- live Tasks/Acknowledgements navigation updates content/title, focuses main once, and announces once;
- Back restores the general view with the same behavior; and
- loaded Tasks and Acknowledgements states pass axe.

### 10.2 Ordinary state and recovery

- search, sort, representative facets, and pagination update URL/results without focusing main,
  changing the base title, announcing navigation, or clearing a captured route error;
- external URL changes update the debounced search input and effective results without stale writeback;
- a material/detail/subview transition clears a captured route error;
- QueryClient identity/lifecycle, cached data, normal stale-observer behavior, and mutation feedback
  remain intact across query-only transitions; and
- unknown parameters and values never appear in titles, headings, live regions, breadcrumbs, or error
  copy.

### 10.3 Feature deep links

- Library detail opens directly, opens through a pushed identifier action, closes through Back, and
  closes explicitly without an extra reopen step;
- DCR, CAPA, and Improvement drawers close when their selector is externally removed and switch when it
  changes;
- Context, Risk, and Interested Parties retain their already-correct removal behavior and remain open
  during unrelated filter edits;
- document tabs and document/DCR diff modes open directly and adopt live external changes/removal;
- invalid document tab/mode values render their safe default;
- invalid loaded-version comparison IDs do not issue viewer work for the invalid identity; and
- representative loaded drawer, tab, and diff states pass axe.

## 11. Verification and documentation

Run the smallest focused RED and GREEN command for each behavior, followed by the complete affected web
selection, web typecheck, lint, scoped Prettier, and production build. Run the full web suite as a durable
job when it remains longer than the interactive window. Run repository authority, Claude compatibility,
site-data, and diff guards before handoff.

The final branch receives an independent requirements and quality review. Every material finding is
fixed through a new failing proof and focused green run before final evidence is collected.

Update `docs/current-status.md` and `docs/slice-history.md` only from fresh final evidence. Preserve the
existing API, contract, integration, migration, and CI counts unless their complete gates run. Retain the
known Vite large-chunk and Node localStorage warnings if they recur, and report Fedora, Docker, or browser
limitations without converting unavailable evidence into a pass.

## 12. Acceptance criteria

1. `/tasks` and `/tasks?type=DOC_ACK` render distinct correct views and safe titles.
2. A live transition in either direction focuses main once and emits one polite destination
   announcement; an initial deep link does neither.
3. Ordinary filter, search, sort, and pagination edits remain shareable, adopt external navigation, and
   do not focus main, announce a page, change the base title, or reset route recovery.
4. Recognized detail selectors open the correct surface, use generic safe titles, and follow selector
   change/removal under Back/Forward.
5. Recognized tabs, modes, and comparison pairs deep-link and follow external changes without global
   focus or title churn.
6. Unknown enum values and loaded-invalid comparison IDs resolve safely without raw-value leakage or
   premature destructive URL normalization.
7. Route recovery resets only for effective pathname/material/detail/subview identity changes and
   remains stable for ordinary and ignored state.
8. QueryClient/provider identity and lifecycle, cache continuity, normal stale-observer behavior,
   mutation-feedback lifetime, setup/auth behavior, legacy redirects, and 404 behavior remain intact.
9. Affected loaded and representative query-selected states are axe-clean.
10. No API, OpenAPI/generated contract, migration, database, Keycloak, dependency, keyboard-row,
    responsive-data-view, Playwright, telemetry, deployment, or unrelated residual change enters the
    slice.
11. Focused, affected, static, build, authority, site-data, and diff verification is green before
    handoff, with unavailable environment proofs reported honestly.

## 13. Alternatives considered

### Page-owned chrome registration

Each feature could register its title and focus policy after parsing its own query state. This would keep
classification near feature knowledge, but it would spread global chrome ownership across many mounted
components, introduce registration ordering and loading-state races, and make route-error identity a
second independently maintained contract.

### Query-view path migration

Material views could become routes such as `/tasks/acknowledgements`, with query URLs retained as aliases.
This would eventually simplify chrome matching, but it expands the slice into public URL migration,
redirect and bookmark compatibility, and route-table changes without solving ordinary filter and drawer
classification on its own.

The centralized classifier is the smallest architecture that gives title, focus, announcement, and
recovery one shared effective identity while leaving feature state at its current owners.
