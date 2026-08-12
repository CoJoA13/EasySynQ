# S-url-state-correctness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make query-backed EasySynQ views deep-linkable, history-correct, recoverable, and accessible without treating ordinary filter edits as page navigation.

**Architecture:** Add one pure, typed `classifyEffectiveView` policy at the router boundary. Route chrome and route-error recovery consume that policy, while individual features continue to own their URL parsing, history writes, validation, rendering, and drawer focus. Tests prove the policy separately from feature synchronization and from whole-application navigation.

**Tech Stack:** React 19, TypeScript 6, React Router 7, TanStack Query 5, Mantine 7, Vitest 4, Testing Library, MSW, jest-axe.

## Global Constraints

- Baseline is `88f5fb0`; the approved design/ADR/debt checkpoint is `7e0d188`.
- Binding behavior is `docs/superpowers/specs/2026-08-11-s-url-state-correctness-design.md` and `docs/adr/0001-centralize-effective-url-view-classification.md`.
- Scope is only query-state classification, route chrome/recovery integration, the approved URL-backed pages, browser-history semantics, accessible navigation behavior, and evidence docs.
- `?type=DOC_ACK` is the only material same-path page view in this slice.
- Detail selectors are material to title and recovery but remain feature-focus-owned; ordinary filters, search, sort, pagination, and hash changes never focus main, announce a page, change the base title, or reset route recovery.
- Known tabs, diff modes, and valid comparison pairs reset route recovery but do not change title, focus main, or announce a page.
- Material view and detail opening add a history entry. Ordinary state writes replace history. Explicit detail close replaces history. Browser Back/Forward remain authoritative.
- Unknown query values resolve to a safe default or are ignored; opaque IDs may enter internal recovery keys but never document titles, announcements, or other user-visible chrome.
- Query parameter order does not affect any effective-view key.
- Preserve one operational `QueryClient` identity and lifecycle, route-persistent mutation feedback, auth/setup gates, current 404 behavior, and route-error title/focus ownership.
- Do not change APIs, OpenAPI/generated contracts, dependencies, lockfiles, migrations, database schema, Keycloak, permissions, mutation retry policy, or server behavior.
- Use existing Mantine components and design tokens; add no timer, animation, toast, palette, or dependency.
- Use TDD: add the focused failing proof before each production change, then run the smallest affected suite.
- Read `docs/debt/20260811234807-effective-view-inventory.md` before changing the classifier or files it names. If implementation creates another deferred policy choice, invoke `debt-ops:add` immediately.
- Preserve the primary checkout's owner-owned `.superdesign/`; work only in `/tmp/EasySynQ-url-state-correctness`.
- Run `bash scripts/check-no-site-data.sh` before every documentation handoff and commit no generated artifacts or site data.

---

### Task 1: Hydrate the worktree and add the pure effective-view classifier

**Files:**

- Create: `apps/web/src/lib/effectiveView.ts`
- Create: `apps/web/src/lib/effectiveView.test.ts`
- Read: `docs/debt/20260811234807-effective-view-inventory.md`

**Interfaces:**

- Produces `QueryStateClass`, `EffectiveView`, and `classifyEffectiveView(pathname, searchParams)`.
- Owns route labels plus the approved material/detail/subview/ordinary inventory.
- Has no React, DOM, navigation, feature-data, or network dependency.

- [ ] **Step 1: Hydrate and establish the baseline**

Run `./scripts/doctor.sh contributor`. Because `apps/web/node_modules` is absent and `just setup` can exceed one minute, invoke `codex-process-jobs:start` with the exact argv `just setup` and working directory `/tmp/EasySynQ-url-state-correctness`. End that assigning turn, retrieve its bounded result after completion with `codex-process-jobs:result`, and record the exit status without calling a skipped setup a pass.

Then run:

```bash
npm --prefix apps/web test -- src/lib/routeChrome.test.tsx src/app/shell/AppShell.test.tsx src/lib/registerControls.test.tsx
```

Expected: the existing route-chrome, recovery, and shared control baseline passes before new tests are introduced.

- [ ] **Step 2: Write the classifier RED tests**

Create table-driven tests for:

```ts
expect(classifyEffectiveView("/tasks", new URLSearchParams("type=DOC_ACK"))).toMatchObject({
  title: "EasySynQ — Acknowledgements",
  queryStateClass: "material-view",
  focusOwner: "route-main",
  announcement: "Acknowledgements",
});

expect(classifyEffectiveView("/library", new URLSearchParams("detail=doc-a"))).toMatchObject({
  title: "EasySynQ — Document details",
  queryStateClass: "detail",
  focusOwner: "feature",
  announcement: null,
});

expect(
  classifyEffectiveView("/tasks", new URLSearchParams("type=not-a-view&q=needle")),
).toMatchObject({
  title: "EasySynQ — Tasks",
  queryStateClass: "ordinary",
  focusOwner: "route-main",
  announcement: "Tasks",
});
```

Cover every detail rule and prove the opaque values `secret-dcr-id` and `secret-document-id` are absent from `title` and `announcement`. Cover `/documents/doc-a` tabs `overview`, `history`, `approvals`, `where-used`, and `acks`; document modes `text` and `visual`; non-empty `from`/`to`; and `/dcrs/dcr-a/diff` modes `text` and `visual`.

Assert these key relations explicitly:

```ts
expect(ack.chromeKey).not.toBe(tasks.chromeKey);
expect(ack.recoveryKey).not.toBe(tasks.recoveryKey);
expect(libraryDetail.chromeKey).not.toBe(library.chromeKey);
expect(historyTab.chromeKey).toBe(document.chromeKey);
expect(historyTab.recoveryKey).not.toBe(document.recoveryKey);
expect(filtered.chromeKey).toBe(unfiltered.chromeKey);
expect(filtered.recoveryKey).toBe(unfiltered.recoveryKey);
expect(reordered).toEqual(originalOrder);
expect(unknownTab).toEqual(document);
```

Include unmatched-route behavior (`EasySynQ — Page not found`, `focusOwner: "feature"`) and known base routes copied from the current `TITLES` inventory so the extraction cannot regress titles.

- [ ] **Step 3: Run the focused RED test**

Run:

```bash
npm --prefix apps/web test -- src/lib/effectiveView.test.ts
```

Expected: FAIL because `effectiveView.ts` does not exist.

- [ ] **Step 4: Implement the pure policy**

Create the public types exactly:

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

Move the complete title-pattern inventory from `routeChrome.ts` into this module. Define these rule tables:

```ts
const DETAIL_RULES = [
  ["/library", "detail", "Document details"],
  ["/dcrs", "dcr", "Change request details"],
  ["/capa", "capa", "CAPA details"],
  ["/improvement", "initiative", "Improvement details"],
  ["/context", "issue", "Context issue details"],
  ["/interested-parties", "party", "Interested party details"],
  ["/risks", "risk", "Risk details"],
] as const;

const DOCUMENT_TABS = new Set(["overview", "history", "approvals", "where-used", "acks"]);
const ORDINARY_KEYS = new Set([
  "q",
  "sort",
  "dir",
  "offset",
  "size",
  "state",
  "type",
  "owner",
  "clause",
  "eff",
  "ctype",
  "reason",
  "stage",
  "source",
  "status",
  "process",
  "rag",
  "band",
  "rtype",
  "classification",
  "category",
  "party_type",
  "influence",
  "queue",
  "conf",
]);
```

Build keys only from recognized effective state in a fixed order. Treat explicit default values (`tab=overview`, `mode=text`) as the same effective identity as omission. Recognized non-default document tabs/modes and non-empty comparison selectors extend `recoveryKey` only. Exact `type=DOC_ACK` changes title, chrome and recovery keys, focus owner, and announcement. A non-empty detail selector changes title, chrome and recovery keys and assigns feature focus. If no material/detail/subview rule applies, recognized ordinary keys yield `ordinary`; every other query yields `ignored`.

- [ ] **Step 5: Run focused GREEN and extraction regression**

Run:

```bash
npm --prefix apps/web test -- src/lib/effectiveView.test.ts src/lib/routeChrome.test.tsx
npm --prefix apps/web run typecheck
```

Expected: classifier tests pass; the old route-chrome suite still passes before its consumer is changed.

- [ ] **Step 6: Format, inspect, and commit Task 1**

Run:

```bash
cd apps/web
npx prettier --write src/lib/effectiveView.ts src/lib/effectiveView.test.ts
npx eslint src/lib/effectiveView.ts src/lib/effectiveView.test.ts
npx tsc --noEmit
cd ../..
git diff --check -- apps/web/src/lib/effectiveView.ts apps/web/src/lib/effectiveView.test.ts
git diff -- apps/web/src/lib/effectiveView.ts apps/web/src/lib/effectiveView.test.ts
git add apps/web/src/lib/effectiveView.ts apps/web/src/lib/effectiveView.test.ts
git commit -m "feat: classify effective URL views"
```

---

### Task 2: Drive route chrome, live announcements, and recovery from effective views

**Files:**

- Modify: `apps/web/src/lib/routeChrome.ts`
- Modify: `apps/web/src/lib/routeChrome.test.tsx`
- Modify: `apps/web/src/app/shell/AppShell.tsx`
- Modify: `apps/web/src/app/shell/AppShell.test.tsx`

**Interfaces:**

- `RouteChromeProvider` additionally owns a single route-announcement value.
- `RouteAnnouncement` renders one persistent polite live region inside the operational shell.
- `useRouteChrome` consumes `pathname` and `search` through `classifyEffectiveView`.
- `AppShell` resets route errors with `EffectiveView.recoveryKey`, never the raw location string.

- [ ] **Step 1: Add RED tests for material navigation and recovery**

Extend `routeChrome.test.tsx` with a harness that exposes buttons for `navigate("/tasks?type=DOC_ACK")`, `navigate(-1)`, ordinary query changes, and detail/subview changes. Prove:

- an initial `/tasks?type=DOC_ACK` visit sets the title but neither focuses `#main-content` nor publishes a live announcement;
- live `/tasks` to `/tasks?type=DOC_ACK` focuses main once, sets `EasySynQ — Acknowledgements`, and announces `Acknowledgements` once;
- Back restores `EasySynQ — Tasks`, focuses main once, and announces `Tasks` once;
- a `q`, sort, pagination, unknown-param, parameter-order, or hash change does none of those things;
- opening `/library?detail=doc-a` changes the generic title but leaves focus to the feature and emits no global announcement;
- document-tab changes emit no global title/focus/announcement change.

Extend `AppShell.test.tsx` with a throwing child and recovery harness. Count fallback appearances to prove ordinary changes do not reset the route boundary while a material view, detail, recognized tab/mode, or comparison-pair change does. Prove unknown values and hash changes do not reset it. Retain the existing route-error title/focus ownership assertions.

- [ ] **Step 2: Run the focused RED tests**

Run:

```bash
npm --prefix apps/web test -- src/lib/routeChrome.test.tsx src/app/shell/AppShell.test.tsx
```

Expected: FAIL because route chrome sees only pathname and AppShell resets on every raw search/hash change.

- [ ] **Step 3: Add the announcement channel**

Import Mantine `VisuallyHidden`. Add separate value and publisher contexts so rendering the live region does not expose the setter as presentation state:

```tsx
const RouteAnnouncementValueContext = createContext<string | null>(null);
const RouteAnnouncementPublisherContext = createContext<
  ((message: string | null) => void) | null
>(null);
```

Nest both providers inside `RouteChromeProvider`, using the stable `setAnnouncement` state setter as the publisher. Export:

```tsx
export function RouteAnnouncement() {
  const message = useContext(RouteAnnouncementValueContext);
  return (
    <VisuallyHidden role="status" aria-live="polite" aria-atomic="true" aria-label="Page navigation">
      {message ?? ""}
    </VisuallyHidden>
  );
}
```

Keep the region mounted once. Clear it on pathname changes so a later return to the same material label produces a real empty-to-text transition. Do not use a timeout.

- [ ] **Step 4: Replace pathname-only effects with effective-view transitions**

In `useRouteChrome`, read `{ pathname, search }`, memoize or compute:

```ts
const view = classifyEffectiveView(pathname, new URLSearchParams(search));
```

Track the previous `{ pathname, chromeKey, recoveryKey }`. Preserve initial-load non-focus. On live path changes, focus main only for a known `route-main` view. On same-path `chromeKey` changes, focus/announce only when the new view is `route-main`; feature-owned detail selectors get their title but retain drawer/dialog focus. Unknown pages retain 404 focus ownership.

While a route error owns chrome, keep `EasySynQ — Page unavailable` and `#route-error-heading` focus. If an effective transition occurs behind that owner, retain a pending route-main focus/announcement and publish it only after route-error ownership releases. Never let the ordinary query branch take ownership from the error page.

- [ ] **Step 5: Use the effective recovery key in AppShell**

Replace:

```ts
const { pathname, search, hash } = useLocation();
const routeResetKey = `${pathname}${search}${hash}`;
```

with:

```ts
const { pathname, search } = useLocation();
const routeResetKey = classifyEffectiveView(
  pathname,
  new URLSearchParams(search),
).recoveryKey;
```

Mount `<RouteAnnouncement />` once inside the shell and outside `ApplicationErrorBoundary`, beside `MutationFeedbackOutlet`, so route-content recovery cannot unmount either persistent channel.

- [ ] **Step 6: Run focused GREEN, axe, and compatibility tests**

Run:

```bash
npm --prefix apps/web test -- src/lib/effectiveView.test.ts src/lib/routeChrome.test.tsx src/app/shell/AppShell.test.tsx src/app/errors/RouteErrorPage.test.tsx src/app/errors/NotFoundPage.test.tsx src/lib/mutationFeedback.test.tsx
```

Expected: all pass with no unhandled errors, duplicate announcements, or axe violations.

- [ ] **Step 7: Format, inspect, and commit Task 2**

Run:

```bash
cd apps/web
npx prettier --write src/lib/routeChrome.ts src/lib/routeChrome.test.tsx src/app/shell/AppShell.tsx src/app/shell/AppShell.test.tsx
npx eslint src/lib/routeChrome.ts src/lib/routeChrome.test.tsx src/app/shell/AppShell.tsx src/app/shell/AppShell.test.tsx
npx tsc --noEmit
cd ../..
git diff --check -- apps/web/src/lib/routeChrome.ts apps/web/src/lib/routeChrome.test.tsx apps/web/src/app/shell/AppShell.tsx apps/web/src/app/shell/AppShell.test.tsx
git diff -- apps/web/src/lib/routeChrome.ts apps/web/src/app/shell/AppShell.tsx
git add apps/web/src/lib/routeChrome.ts apps/web/src/lib/routeChrome.test.tsx apps/web/src/app/shell/AppShell.tsx apps/web/src/app/shell/AppShell.test.tsx
git commit -m "feat: synchronize effective route chrome"
```

---

### Task 3: Prove the Tasks material-view lifecycle through the real app

**Files:**

- Modify: `apps/web/src/App.test.tsx`
- Modify: `apps/web/src/features/review/TasksInbox.test.tsx`
- Modify only if a failing proof requires it: `apps/web/src/features/review/TasksInbox.tsx`

**Interfaces:**

- `TasksInbox` treats exact `DOC_ACK` as the acknowledgement view and every other value as the general queue.
- The existing operational router, QueryClient, mutation feedback provider, and AppShell remain mounted across the transition.

- [ ] **Step 1: Add whole-app RED integration tests**

In `App.test.tsx`, reuse the current authenticated operational-app harness and MSW fixtures. Navigate live from `/tasks` to `/tasks?type=DOC_ACK`, then Back. Assert exact document titles, `#main-content` focus, and the `Page navigation` live-region messages at each step. Count focus calls or active-element transitions so each material transition occurs once.

Add an initial deep-link test for `/tasks?type=DOC_ACK`: title is `EasySynQ — Acknowledgements`, acknowledgement content renders, but main is not auto-focused and the live region is empty.

Add a query-only lifecycle probe inside the operational providers. Seed a unique `QueryClient` cache value and publish a persistent mutation-feedback item before the transition. Assert the same cache value and feedback remain after moving to acknowledgements and after Back. This proves the router integration did not recreate the client/provider or issue a mutation.

In `TasksInbox.test.tsx`, render `?type=unknown-sentinel`. Assert the general queue renders and `unknown-sentinel` is absent from headings, title, live regions, and accessible body text. Run axe for base, acknowledgement, and unknown states.

- [ ] **Step 2: Run the focused tests**

Run:

```bash
npm --prefix apps/web test -- src/App.test.tsx src/features/review/TasksInbox.test.tsx
```

Expected before any production edit: the whole-app expectations fail until Task 2 is integrated correctly; after Task 2 they may already pass. If exact unknown-value normalization fails, make the smallest dispatcher correction and retain the RED evidence.

- [ ] **Step 3: Make only the evidence-driven dispatcher correction**

Keep the hook-safe thin dispatcher:

```tsx
export function TasksInbox() {
  const [sp] = useSearchParams();
  return sp.get("type") === "DOC_ACK" ? <AckInbox /> : <GeneralTasksInbox />;
}
```

Do not add local title, focus, live-region, history, QueryClient, or mutation-feedback logic to this feature.

- [ ] **Step 4: Run focused GREEN and provider regressions**

Run:

```bash
npm --prefix apps/web test -- src/App.test.tsx src/features/review/TasksInbox.test.tsx src/lib/mutationFeedback.test.tsx src/app/shell/AppShell.test.tsx
```

Expected: material navigation, Back restoration, initial deep link, provider continuity, safe unknown fallback, and axe checks pass.

- [ ] **Step 5: Format, inspect, and commit Task 3**

Run:

```bash
cd apps/web
npx prettier --write src/App.test.tsx src/features/review/TasksInbox.tsx src/features/review/TasksInbox.test.tsx
npx eslint src/App.test.tsx src/features/review/TasksInbox.tsx src/features/review/TasksInbox.test.tsx
npx tsc --noEmit
cd ../..
git diff --check -- apps/web/src/App.test.tsx apps/web/src/features/review/TasksInbox.tsx apps/web/src/features/review/TasksInbox.test.tsx
git diff -- apps/web/src/App.test.tsx apps/web/src/features/review/TasksInbox.tsx apps/web/src/features/review/TasksInbox.test.tsx
git add apps/web/src/App.test.tsx apps/web/src/features/review/TasksInbox.test.tsx
git diff --quiet -- apps/web/src/features/review/TasksInbox.tsx || git add apps/web/src/features/review/TasksInbox.tsx
git commit -m "test: prove material task URL navigation"
```

---

### Task 4: Make ordinary register state replace history and Library detail history coherent

**Files:**

- Modify: `apps/web/src/lib/registerControls.test.tsx`
- Modify: `apps/web/src/features/library/LibraryPage.tsx`
- Modify: `apps/web/src/features/library/LibraryPage.test.tsx`
- Modify: `apps/web/src/features/reports/ReportsRegisterPage.tsx`
- Modify: `apps/web/src/features/reports/ReportsRegisterPage.test.tsx`
- Modify: `apps/web/src/features/ingestion/ReviewCockpit.tsx`
- Modify: `apps/web/src/features/ingestion/ReviewCockpit.test.tsx`

**Interfaces:**

- Existing `useUrlParam`, `useDebouncedSearch`, and `useTableSort` remain the shared ordinary-state implementation.
- Library and Reports filter/clear writes plus Library and ingestion pagination/filter writes replace the current history entry.
- Library detail open pushes; explicit detail close replaces.

- [ ] **Step 1: Add RED history and chrome-neutrality tests**

Extend `registerControls.test.tsx` with a `MemoryRouter` history probe. Starting from `/dcrs?sentinel=keep`, edit `q`, sort, direction, and an enum filter; navigate Back; assert no intermediate ordinary-state entry is restored and unrelated `sentinel=keep` survives. Then update the location externally and prove controls adopt the new URL instead of replaying stale local state.

Extend `LibraryPage.test.tsx` to prove:

- facet change, clear, next/previous page, and page-size change pass through replacement history and preserve unrelated params;
- those ordinary changes do not change `EasySynQ — Library`, focus main, publish a page announcement, or reset a captured route error;
- clicking a document pushes `detail=doc-a`; browser Back closes the drawer and restores the prior register state;
- explicit close removes `detail` with replacement, so one Back does not reopen that drawer;
- changing `detail=doc-a` to `detail=doc-b` while mounted switches drawer content;
- removing `detail` externally closes the drawer.

Extend `ReportsRegisterPage.test.tsx` to prove its state/type/owner/clause/effective/process filter and clear actions replace history and preserve unrelated params. Extend `ReviewCockpit.test.tsx` to prove `queue`, `conf`, and `offset` writes replace history. In both suites, assert representative ordinary changes leave the base title, main focus, route announcement, and recovery boundary untouched.

- [ ] **Step 2: Run focused RED**

Run:

```bash
npm --prefix apps/web test -- src/lib/registerControls.test.tsx src/features/library/LibraryPage.test.tsx src/features/reports/ReportsRegisterPage.test.tsx src/features/ingestion/ReviewCockpit.test.tsx
```

Expected: shared controls pass existing replacement behavior; new Library, Reports, and ingestion history tests fail because their direct `setSearchParams` writes still use default push behavior.

- [ ] **Step 3: Add explicit Library replace semantics**

Pass `{ replace: true }` as the second argument to `setParams` in `patchFilters`, `clearFilters`, `setOffset`, `setSize`, and `closeDetail`. Leave `openDetail` without replacement:

```ts
const openDetail = (id: string) =>
  setParams((p) => {
    p.set("detail", id);
    return p;
  });

const closeDetail = () =>
  setParams(
    (p) => {
      p.delete("detail");
      return p;
    },
    { replace: true },
  );
```

Do not turn row density into URL state and do not change the existing URL-derived `detailId` rendering model.

Add `{ replace: true }` to `ReportsRegisterPage`'s `patchFilters` and `clearFilters` writes; retain its existing replacement write for post-load invalid-facet cleanup. Add `{ replace: true }` to `ReviewCockpit`'s `onQueue`, `onConf`, and `onOffset` writes. Do not change result filtering, selected-row clearing, option validation, or API query construction.

- [ ] **Step 4: Run focused GREEN and adjacent detail regressions**

Run:

```bash
npm --prefix apps/web test -- src/lib/registerControls.test.tsx src/features/library/LibraryPage.test.tsx src/features/document/DocumentDrawer.test.tsx src/features/reports/ReportsRegisterPage.test.tsx src/features/ingestion/ReviewCockpit.test.tsx src/lib/routeChrome.test.tsx src/app/shell/AppShell.test.tsx
```

Expected: ordinary history is compact, drawer Back/close behavior is coherent, query order is irrelevant, and chrome/recovery stay neutral for ordinary changes.

- [ ] **Step 5: Format, inspect, and commit Task 4**

Run:

```bash
cd apps/web
npx prettier --write src/lib/registerControls.test.tsx src/features/library/LibraryPage.tsx src/features/library/LibraryPage.test.tsx src/features/reports/ReportsRegisterPage.tsx src/features/reports/ReportsRegisterPage.test.tsx src/features/ingestion/ReviewCockpit.tsx src/features/ingestion/ReviewCockpit.test.tsx
npx eslint src/lib/registerControls.test.tsx src/features/library/LibraryPage.tsx src/features/library/LibraryPage.test.tsx src/features/reports/ReportsRegisterPage.tsx src/features/reports/ReportsRegisterPage.test.tsx src/features/ingestion/ReviewCockpit.tsx src/features/ingestion/ReviewCockpit.test.tsx
npx tsc --noEmit
cd ../..
git diff --check -- apps/web/src/lib/registerControls.test.tsx apps/web/src/features/library/LibraryPage.tsx apps/web/src/features/library/LibraryPage.test.tsx apps/web/src/features/reports/ReportsRegisterPage.tsx apps/web/src/features/reports/ReportsRegisterPage.test.tsx apps/web/src/features/ingestion/ReviewCockpit.tsx apps/web/src/features/ingestion/ReviewCockpit.test.tsx
git diff -- apps/web/src/lib/registerControls.test.tsx apps/web/src/features/library/LibraryPage.tsx apps/web/src/features/reports/ReportsRegisterPage.tsx apps/web/src/features/ingestion/ReviewCockpit.tsx
git add apps/web/src/lib/registerControls.test.tsx apps/web/src/features/library/LibraryPage.tsx apps/web/src/features/library/LibraryPage.test.tsx apps/web/src/features/reports/ReportsRegisterPage.tsx apps/web/src/features/reports/ReportsRegisterPage.test.tsx apps/web/src/features/ingestion/ReviewCockpit.tsx apps/web/src/features/ingestion/ReviewCockpit.test.tsx
git commit -m "fix: compact register URL history"
```

---

### Task 5: Make every selector-backed drawer follow live URL removal and replacement

**Files:**

- Modify: `apps/web/src/features/dcr/DcrsRegisterPage.tsx`
- Modify: `apps/web/src/features/dcr/DcrsRegisterPage.test.tsx`
- Modify: `apps/web/src/features/capa/CapaBoardPage.tsx`
- Modify: `apps/web/src/features/capa/CapaRouting.test.tsx`
- Modify: `apps/web/src/features/improvement/ImprovementRegisterPage.tsx`
- Modify: `apps/web/src/features/improvement/ImprovementRegisterPage.test.tsx`
- Regression only: `apps/web/src/features/context/ContextRegisterPage.test.tsx`
- Regression only: `apps/web/src/features/interested-parties/InterestedPartiesRegisterPage.test.tsx`
- Regression only: `apps/web/src/features/risk/RisksRegisterPage.test.tsx`

**Interfaces:**

- URL-seeded selectors `dcr`, `capa`, and `initiative` become bidirectionally synchronized while mounted.
- Local-only row/card/modal openings stay local and do not acquire URL writes.
- Existing Context, Interested Party, and Risk selector behavior remains the reference implementation.

- [ ] **Step 1: Add RED synchronization matrices**

For each of DCR, CAPA, and Improvement, add tests that start with selector `a`, externally replace it with `b`, then remove it. Assert drawer A opens, drawer B replaces it without remounting the register, and removal closes it. Add an unrelated filter update while a locally opened drawer is active and assert the local drawer stays open.

Also assert:

- initial selector deep links receive the generic detail title and feature-owned dialog focus;
- raw selector IDs never appear in document title or the route announcement;
- explicit close removes only its selector with replacement and preserves unrelated filters;
- browser Back after a pushed external detail link restores the register rather than creating a close/reopen loop.

- [ ] **Step 2: Run focused RED**

Run:

```bash
npm --prefix apps/web test -- src/features/dcr/DcrsRegisterPage.test.tsx src/features/capa/CapaRouting.test.tsx src/features/improvement/ImprovementRegisterPage.test.tsx
```

Expected: removal tests fail because each effect ignores a null selector.

- [ ] **Step 3: Synchronize each selector by value**

In each page, derive the selector once and make the effect depend on its scalar value:

```ts
const selectedDcrParam = params.get("dcr");
useEffect(() => setSelected(selectedDcrParam), [selectedDcrParam]);
```

Use corresponding `selectedCapaParam` and `selectedInitiativeParam` names in their features. This allows selector replacement and removal while preventing unrelated `URLSearchParams` identity changes from overwriting a local-only drawer selection. Retain current close behavior and comments, updating comments that still describe a non-null guard.

- [ ] **Step 4: Run focused GREEN and all selector-register regressions**

Run:

```bash
npm --prefix apps/web test -- src/features/dcr/DcrsRegisterPage.test.tsx src/features/capa/CapaRouting.test.tsx src/features/improvement/ImprovementRegisterPage.test.tsx src/features/context/ContextRegisterPage.test.tsx src/features/interested-parties/InterestedPartiesRegisterPage.test.tsx src/features/risk/RisksRegisterPage.test.tsx src/lib/routeChrome.test.tsx
```

Expected: all seven detail-selector routes respond to live URL changes, local openings survive unrelated query edits, and generic feature-owned chrome remains correct.

- [ ] **Step 5: Format, inspect, and commit Task 5**

Run:

```bash
cd apps/web
npx prettier --write src/features/dcr/DcrsRegisterPage.tsx src/features/dcr/DcrsRegisterPage.test.tsx src/features/capa/CapaBoardPage.tsx src/features/capa/CapaRouting.test.tsx src/features/improvement/ImprovementRegisterPage.tsx src/features/improvement/ImprovementRegisterPage.test.tsx
npx eslint src/features/dcr/DcrsRegisterPage.tsx src/features/dcr/DcrsRegisterPage.test.tsx src/features/capa/CapaBoardPage.tsx src/features/capa/CapaRouting.test.tsx src/features/improvement/ImprovementRegisterPage.tsx src/features/improvement/ImprovementRegisterPage.test.tsx
npx tsc --noEmit
cd ../..
git diff --check -- apps/web/src/features/dcr/DcrsRegisterPage.tsx apps/web/src/features/dcr/DcrsRegisterPage.test.tsx apps/web/src/features/capa/CapaBoardPage.tsx apps/web/src/features/capa/CapaRouting.test.tsx apps/web/src/features/improvement/ImprovementRegisterPage.tsx apps/web/src/features/improvement/ImprovementRegisterPage.test.tsx
git diff -- apps/web/src/features/dcr/DcrsRegisterPage.tsx apps/web/src/features/capa/CapaBoardPage.tsx apps/web/src/features/improvement/ImprovementRegisterPage.tsx
git add apps/web/src/features/dcr/DcrsRegisterPage.tsx apps/web/src/features/dcr/DcrsRegisterPage.test.tsx apps/web/src/features/capa/CapaBoardPage.tsx apps/web/src/features/capa/CapaRouting.test.tsx apps/web/src/features/improvement/ImprovementRegisterPage.tsx apps/web/src/features/improvement/ImprovementRegisterPage.test.tsx
git commit -m "fix: synchronize URL-backed drawers"
```

---

### Task 6: Normalize document tabs and validate comparison state

**Files:**

- Modify: `apps/web/src/features/document/DocumentDetailPage.tsx`
- Modify: `apps/web/src/features/document/DocumentDetailPage.test.tsx`
- Modify: `apps/web/src/features/document/VersionCompare.tsx`
- Modify: `apps/web/src/features/document/VersionCompare.test.tsx`
- Modify: `apps/web/src/features/dcr/DcrDiffPage.tsx`
- Modify: `apps/web/src/features/dcr/DcrDiffPage.test.tsx`

**Interfaces:**

- Document tabs accept only `overview`, `history`, `approvals`, `where-used`, and `acks`; omission or unknown values render Overview.
- Comparison IDs are valid only when present in the loaded version set; an invalid or incomplete pair falls back to prior-to-newest.
- Document tab, comparison selector, and diff-mode control writes replace history.

- [ ] **Step 1: Add RED tab and comparison tests**

In `DocumentDetailPage.test.tsx`, cover cold deep links and live external changes for every recognized tab, tab removal, and `tab=unknown-sentinel`. Assert unknown/removal renders Overview, raw text does not leak, and tab changes replace history without global title/focus/announcement changes. Include axe for Overview and one non-default tab.

In `VersionCompare.test.tsx`, provide versions `version-old` and `version-new`. Cover valid cold/live `from`/`to`, missing one side, an invalid `unknown-version`, equal IDs, parameter removal, and text/visual mode changes. Assert invalid values fall back to old-to-new and no MSW request URL or viewer props contain `unknown-version`. Prove control writes replace history.

In `DcrDiffPage.test.tsx`, prove unknown mode renders text, live mode changes update the viewer, parameter removal returns to text, and the mode control replaces history without title/focus/announcement changes.

- [ ] **Step 2: Run focused RED**

Run:

```bash
npm --prefix apps/web test -- src/features/document/DocumentDetailPage.test.tsx src/features/document/VersionCompare.test.tsx src/features/dcr/DcrDiffPage.test.tsx
```

Expected: unknown document tabs can select no panel, raw unknown version IDs reach the comparison viewer, and comparison/DCR mode writes still push history.

- [ ] **Step 3: Normalize document tabs**

Add a local parser:

```ts
const DOCUMENT_TABS = ["overview", "history", "approvals", "where-used", "acks"] as const;
type DocumentTab = (typeof DOCUMENT_TABS)[number];

function parseDocumentTab(value: string | null): DocumentTab {
  return DOCUMENT_TABS.includes(value as DocumentTab) ? (value as DocumentTab) : "overview";
}
```

Use `parseDocumentTab(sp.get("tab"))`. Keep the existing `{ replace: true }` setter, deleting `tab` for the default Overview value so omission and explicit default converge:

```ts
if (!v || v === "overview") prev.delete("tab");
else prev.set("tab", v);
```

- [ ] **Step 4: Validate comparison IDs after versions load**

Sort versions newest-first as today, build a `Set` of valid IDs, and compute:

```ts
const defaultFrom = ordered[1]?.id ?? null;
const defaultTo = ordered[0]?.id ?? null;
const rawFrom = params.get("from");
const rawTo = params.get("to");
const pairIsValid =
  rawFrom !== null && rawTo !== null && validIds.has(rawFrom) && validIds.has(rawTo);
const from = pairIsValid ? rawFrom : defaultFrom;
const to = pairIsValid ? rawTo : defaultTo;
```

Do not rewrite an invalid cold URL automatically; render the safe effective pair and let the next explicit control edit replace the URL. Add `{ replace: true }` to the shared comparison setter. Continue to suppress a viewer for equal effective IDs.

- [ ] **Step 5: Replace DCR diff-mode history writes**

Keep the safe read parser and change `setMode` to:

```ts
setParams(
  (p) => {
    if (value === "text") p.delete("mode");
    else p.set("mode", value);
    return p;
  },
  { replace: true },
);
```

- [ ] **Step 6: Run focused GREEN and recovery integration**

Run:

```bash
npm --prefix apps/web test -- src/features/document/DocumentDetailPage.test.tsx src/features/document/VersionCompare.test.tsx src/features/dcr/DcrDiffPage.test.tsx src/lib/effectiveView.test.ts src/lib/routeChrome.test.tsx src/app/shell/AppShell.test.tsx
```

Expected: every recognized subview is deep-linkable and recovery-significant, every unknown value is safe, control history is compact, and global chrome stays neutral.

- [ ] **Step 7: Format, inspect, and commit Task 6**

Run:

```bash
cd apps/web
npx prettier --write src/features/document/DocumentDetailPage.tsx src/features/document/DocumentDetailPage.test.tsx src/features/document/VersionCompare.tsx src/features/document/VersionCompare.test.tsx src/features/dcr/DcrDiffPage.tsx src/features/dcr/DcrDiffPage.test.tsx
npx eslint src/features/document/DocumentDetailPage.tsx src/features/document/DocumentDetailPage.test.tsx src/features/document/VersionCompare.tsx src/features/document/VersionCompare.test.tsx src/features/dcr/DcrDiffPage.tsx src/features/dcr/DcrDiffPage.test.tsx
npx tsc --noEmit
cd ../..
git diff --check -- apps/web/src/features/document/DocumentDetailPage.tsx apps/web/src/features/document/DocumentDetailPage.test.tsx apps/web/src/features/document/VersionCompare.tsx apps/web/src/features/document/VersionCompare.test.tsx apps/web/src/features/dcr/DcrDiffPage.tsx apps/web/src/features/dcr/DcrDiffPage.test.tsx
git diff -- apps/web/src/features/document/DocumentDetailPage.tsx apps/web/src/features/document/VersionCompare.tsx apps/web/src/features/dcr/DcrDiffPage.tsx
git add apps/web/src/features/document/DocumentDetailPage.tsx apps/web/src/features/document/DocumentDetailPage.test.tsx apps/web/src/features/document/VersionCompare.tsx apps/web/src/features/document/VersionCompare.test.tsx apps/web/src/features/dcr/DcrDiffPage.tsx apps/web/src/features/dcr/DcrDiffPage.test.tsx
git commit -m "fix: validate URL-backed document subviews"
```

---

### Task 7: Complete verification, independent review, and authority evidence

**Files:**

- Modify: `docs/current-status.md`
- Modify: `docs/slice-history.md`
- Modify only for corrections found in review: `docs/superpowers/specs/2026-08-11-s-url-state-correctness-design.md`
- Modify only for corrections found in review: `docs/superpowers/plans/2026-08-11-s-url-state-correctness.md`

- [ ] **Step 1: Run the complete focused URL-state suite**

Run:

```bash
npm --prefix apps/web test -- src/lib/effectiveView.test.ts src/lib/routeChrome.test.tsx src/app/shell/AppShell.test.tsx src/App.test.tsx src/lib/registerControls.test.tsx src/features/review/TasksInbox.test.tsx src/features/library/LibraryPage.test.tsx src/features/reports/ReportsRegisterPage.test.tsx src/features/ingestion/ReviewCockpit.test.tsx src/features/dcr/DcrsRegisterPage.test.tsx src/features/capa/CapaRouting.test.tsx src/features/improvement/ImprovementRegisterPage.test.tsx src/features/context/ContextRegisterPage.test.tsx src/features/interested-parties/InterestedPartiesRegisterPage.test.tsx src/features/risk/RisksRegisterPage.test.tsx src/features/document/DocumentDetailPage.test.tsx src/features/document/VersionCompare.test.tsx src/features/dcr/DcrDiffPage.test.tsx src/lib/mutationFeedback.test.tsx src/app/errors/RouteErrorPage.test.tsx src/app/errors/NotFoundPage.test.tsx
```

Expected: PASS with no unhandled exceptions, React hook-order warnings, duplicate announcements, unsafe raw-value leakage, or axe violations.

- [ ] **Step 2: Run static web gates**

Run:

```bash
npm --prefix apps/web run lint
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
```

Expected: PASS using the committed lockfile and no generated dependency changes.

- [ ] **Step 3: Run the full web suite as a durable job**

Invoke `codex-process-jobs:start` with exact argv `npm --prefix apps/web test` and working directory `/tmp/EasySynQ-url-state-correctness`. End the assigning turn. After the completion notification, invoke `codex-process-jobs:result` and record exact file/test totals, duration, warnings, unhandled errors, and exit status. Do not poll the job in its launch turn and do not convert an unavailable run into a pass.

- [ ] **Step 4: Run repository authority, compatibility, site-data, formatting, and diff guards**

Run:

```bash
bash scripts/tests/test-agent-authority.sh
bash scripts/tests/test-claude-hooks.sh
./scripts/check-repo-authority.sh
bash scripts/tests/test-check-no-site-data.sh
./scripts/check-no-site-data.sh
apps/web/node_modules/.bin/prettier --check docs/superpowers/specs/2026-08-11-s-url-state-correctness-design.md docs/superpowers/plans/2026-08-11-s-url-state-correctness.md docs/adr/0001-centralize-effective-url-view-classification.md docs/debt/20260811234807-effective-view-inventory.md docs/current-status.md
git diff --check 88f5fb0..HEAD
git status --short --branch
```

Check `docs/slice-history.md` separately against its exact baseline before making a formatting claim; do not mass-format historical entries.

- [ ] **Step 5: Perform independent requirements and quality review**

Invoke `superpowers:requesting-code-review` and `codex-engineering-guardrails:code-verification`. Review the complete range `88f5fb0..HEAD` against every approved design acceptance criterion, with special attention to:

- initial versus live focus/announcement behavior;
- route-error ownership and effective recovery boundaries;
- parameter-order independence and unknown-value fallback;
- feature-owned dialog focus;
- history push/replace/Back semantics;
- comparison ID validation before requests/rendering;
- QueryClient and mutation-feedback continuity; and
- accessibility and raw-value non-disclosure.

Classify findings as Critical, Important, or Minor. Fix every in-scope Critical or Important finding with a focused failing proof, rerun affected checks, and repeat review until none remains. Register any deliberately deferred material decision through `debt-ops:add`.

- [ ] **Step 6: Update current status and slice history from fresh evidence**

In `docs/current-status.md`, update the shipped slice, baseline commit, date, web test totals, and concise runtime summary only from the final successful evidence. Do not alter unrelated API counts or CI topology.

Append a dated `S-url-state-correctness` entry to `docs/slice-history.md` recording:

- the typed effective-view classifier and ADR decision;
- observable Tasks material-view title/focus/announcement and Back behavior;
- detail, ordinary, and subview history/recovery semantics;
- drawer live-removal fixes and comparison validation;
- exact RED/GREEN, static, full-suite, authority, and site-data commands/results;
- independent review findings and fixes;
- unchanged API/schema/auth/setup/QueryClient/mutation-feedback boundaries; and
- every skipped or unavailable proof without describing it as passed.

- [ ] **Step 7: Re-run final guards and commit evidence**

Run the affected focused tests, static gates, scoped documentation Prettier, authority check, site-data check, and diff check again after evidence edits. Then run:

```bash
git add docs/current-status.md docs/slice-history.md
git diff --quiet -- docs/superpowers/specs/2026-08-11-s-url-state-correctness-design.md || git add docs/superpowers/specs/2026-08-11-s-url-state-correctness-design.md
git diff --quiet -- docs/superpowers/plans/2026-08-11-s-url-state-correctness.md || git add docs/superpowers/plans/2026-08-11-s-url-state-correctness.md
git commit -m "docs: record URL-state correctness evidence"
git status --short --branch
git log --oneline --decorate 88f5fb0..HEAD
```

Expected: clean isolated worktree, reviewable scoped commits, no push or pull request until the owner chooses publication.

## Plan self-review

- **Spec coverage:** Tasks 1–2 implement the typed policy, chrome, announcement, and recovery contract; Task 3 proves the only material same-path view through the real app; Task 4 covers shared, Library, Reports, and ingestion ordinary history; Tasks 5–6 cover detail, drawer, tab, mode, and comparison behavior; Task 7 covers final evidence and review.
- **Inventory coverage:** detail selectors cover Library, DCR, CAPA, Improvement, Context, Interested Party, and Risk; subviews cover document tabs/comparison and DCR diff mode; ordinary keys cover shared search/sort plus current register, report, audit, and ingestion filters/pagination.
- **Ownership consistency:** router owns generic title/recovery/route-main announcements; features own parsing, rendering, validation, writes, and detail focus; route errors and 404 retain their existing focus/title priority.
- **History consistency:** Tasks material view and Library detail open push; ordinary controls and subview controls replace; explicit detail close replaces; external Back/Forward updates are always consumed.
- **Lifetime consistency:** no task recreates the operational QueryClient, router, AppShell, mutation-feedback provider, or auth/setup gate during query-only transitions.
- **Security consistency:** opaque selector/comparison values are restricted to internal keys or validated feature state and never enter visible chrome; site-data and authority guards remain mandatory.
- **Type consistency:** `QueryStateClass`, `EffectiveView`, `classifyEffectiveView`, `RouteAnnouncement`, `chromeKey`, and `recoveryKey` have one spelling and ownership throughout.
- **Placeholder scan:** the plan contains no deferred implementation markers; commands, file paths, interfaces, expected failures, expected passes, commits, and evidence updates are explicit.
