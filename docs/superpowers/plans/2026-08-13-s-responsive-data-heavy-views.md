# Responsive Data-Heavy Views Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the nine approved shared-register routes usable at a 320 CSS-pixel viewport through bounded toolbar controls and page-owned horizontal table containment without changing desktop presentation, semantics, state, or interaction ownership.

**Architecture:** Keep one semantic and interactive tree. `RegisterToolbar` receives sub-`sm` sizing and filter-lane containment without changing its public props, while each approved route directly wraps its existing table in Mantine `Table.ScrollContainer` with the owner-approved route-specific minimum width. Tests pin rendered containment, the exact source inventory, native controls, column order, and preserved state behavior; real browser layout evidence remains slice 8 work.

**Tech Stack:** React 19, TypeScript 6, Mantine 7, React Router 7, TanStack Query 5, Vitest 4, React Testing Library, MSW, jest-axe, Vite 8, Prettier 3.

## Global Constraints

- Work only in the isolated worktree for `codex/responsive-data-heavy-views`.
- The implementation baseline is design commit `5bced6c`; preserve primary-checkout `.superdesign/` and both named prunable registrations.
- The responsive contract is usable at 320 CSS pixels and uses Mantine's existing `sm` breakpoint at 48 em; add no custom breakpoint.
- At `sm` and above, preserve the current 260 px default search width, table columns, toolbar order, and desktop presentation.
- Keep every record field and action available exactly once in its current DOM and keyboard order; add no cards, hidden columns, duplicated mobile tree, sticky action overlay, or global overflow suppression.
- Preserve structural `Table.Tr`, native primary links/buttons, accessible names, `aria-sort`, existing `aria-pressed`, `data-rownav`, URL/history semantics, drawer ownership, QueryClient/provider identity, mutation feedback, auth/setup gates, permissions, and error/404 boundaries.
- Edit only the nine approved routes plus `RegisterToolbar`, their tests, the focused responsive test helper/guard, and final evidence documents.
- Do not edit CAPA List, Complaints, Audit Programme, ingestion triage, superseded-copy, admin, document-detail, API, OpenAPI/generated contracts, migrations/database, dependencies/locks, permissions, telemetry, notifications, or deployment.
- Use app-owned Prettier. Do not mass-format `docs/slice-history.md`.
- Start every behavior task with a focused failing proof, finish with fresh GREEN evidence, inspect the task diff, then require requirements review followed by code-quality review.
- Unit tests may inspect responsive style inputs but must not be described as real viewport, clipping, scrolling, focus-ring, forced-colors, or screen-reader proof.

## File Ownership Map

- `apps/web/src/lib/RegisterToolbar.tsx` and test — shared sub-`sm` search sizing and bounded filter-lane presentation.
- `apps/web/src/test/responsiveTable.ts` — test-only rendered contract for one table inside one Mantine minimum-width owner.
- `apps/web/src/lib/responsiveRegisterContract.test.ts` — exact source inventory and rejection of a second or visibility-switched table presentation.
- Tasks, Audits, Objectives, and Management Reviews production/tests — linked-register task only.
- DCR and Improvement production/tests — wide drawer-register task only.
- Risk, Context, and Interested Parties production/tests — contextual drawer-register task only.
- `docs/current-status.md` and `docs/slice-history.md` — final fresh evidence after implementation review.

---

### Task 1: Make the shared register toolbar narrow-safe

**Files:**

- Modify: `apps/web/src/lib/RegisterToolbar.tsx:6-48`
- Modify: `apps/web/src/lib/RegisterToolbar.test.tsx:1-35`

**Interfaces:**

- Consumes: existing `RegisterToolbar` props `q`, `onQ`, `placeholder`, `count`, `countNoun`, `searchWidth`, and `children`.
- Produces: the same public props; one search input with base width `100%`, `miw={0}`, and desktop width `searchWidth`; one optional filter lane with inline `overflowX: "auto"`, `minWidth: 0`, and `maxWidth: "100%"`.

- [ ] **Step 1: Write the failing responsive toolbar test**

Add `SegmentedControl` to the Mantine test import and add inside `describe("RegisterToolbar")`:

```tsx
it("keeps one ordered search and filter tree inside the narrow toolbar", () => {
  wrap(
    <RegisterToolbar q="" onQ={() => undefined} count={3} countNoun="items">
      <SegmentedControl
        aria-label="Filter by state"
        value="all"
        onChange={() => undefined}
        data={[
          { value: "all", label: "All" },
          { value: "active", label: "Active" },
        ]}
      />
    </RegisterToolbar>,
  );

  const search = screen.getByRole("textbox", { name: "Search" });
  const searchRoot = search.closest<HTMLElement>(".mantine-TextInput-root");
  expect(searchRoot).not.toBeNull();
  expect(searchRoot).toHaveStyle({ minWidth: "0rem" });
  const responsiveClass = [...searchRoot!.classList].find((name) =>
    name.startsWith("__m__"),
  );
  expect(responsiveClass).toBeDefined();
  const inlineRules = [
    ...document.querySelectorAll('style[data-mantine-styles="inline"]'),
  ]
    .map((style) => style.textContent ?? "")
    .join("\n");
  expect(inlineRules).toContain(`.${responsiveClass}{width:100%;}`);
  expect(inlineRules).toContain("@media(min-width: 48em)");
  expect(inlineRules).toContain("width:calc(16.25rem * var(--mantine-scale))");

  const filter = screen.getByRole("radio", { name: "All" });
  const filterLane = filter.closest<HTMLElement>('[style*="overflow-x"]');
  expect(filterLane).not.toBeNull();
  expect(filterLane).toHaveStyle({
    overflowX: "auto",
    minWidth: "0rem",
    maxWidth: "100%",
  });
  expect(
    search.compareDocumentPosition(filter) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(screen.getAllByRole("textbox", { name: "Search" })).toHaveLength(1);
  expect(screen.getAllByRole("radio")).toHaveLength(2);
});
```

- [ ] **Step 2: Run the RED proof**

```bash
npm --prefix apps/web test -- src/lib/RegisterToolbar.test.tsx
```

Expected: FAIL because the current search root has neither the zero minimum width nor responsive rule, and the filter has no bounded overflow ancestor.

- [ ] **Step 3: Implement the minimal responsive structure**

Add `Box` to the Mantine import. Replace the current left-hand `Group` body with:

```tsx
<Group
  align="flex-end"
  wrap="wrap"
  gap="sm"
  w={{ base: "100%", sm: "auto" }}
  style={{ flex: "1 1 auto", minWidth: 0, maxWidth: "100%" }}
>
  <TextInput
    value={q}
    onChange={(e) => onQ(e.currentTarget.value)}
    placeholder={placeholder ?? "Search…"}
    aria-label="Search"
    leftSection={<IconSearch size={16} />}
    w={{ base: "100%", sm: searchWidth }}
    miw={0}
  />
  {children && (
    <Box maw="100%" miw={0} style={{ overflowX: "auto" }}>
      <Group align="flex-end" wrap="wrap" gap="sm">
        {children}
      </Group>
    </Box>
  )}
</Group>
```

Leave the outer count block unchanged. Do not change the prop type, search label, child order, count copy, or polite live region.

- [ ] **Step 4: Run GREEN and static checks**

```bash
npm --prefix apps/web test -- src/lib/RegisterToolbar.test.tsx
npm --prefix apps/web run typecheck
npm --prefix apps/web exec -- eslint src/lib/RegisterToolbar.tsx src/lib/RegisterToolbar.test.tsx
apps/web/node_modules/.bin/prettier --check apps/web/src/lib/RegisterToolbar.tsx apps/web/src/lib/RegisterToolbar.test.tsx
git diff --check -- apps/web/src/lib/RegisterToolbar.tsx apps/web/src/lib/RegisterToolbar.test.tsx
```

Expected: all commands exit 0 and the test proves one search/filter tree.

- [ ] **Step 5: Commit and review**

```bash
git add apps/web/src/lib/RegisterToolbar.tsx apps/web/src/lib/RegisterToolbar.test.tsx
git commit -m "feat: contain register controls on narrow screens"
```

Requirements review compares the commit to design sections 4.1-4.2 and rejects a new public prop, duplicate control, custom breakpoint, or desktop-width change. Code-quality review inspects responsive assertions, accessible names, DOM order, YAGNI, and the scoped diff. Resolve findings through a focused RED/GREEN loop before Task 2.

---

### Task 2: Contain the four linked registers

**Files:**

- Create: `apps/web/src/test/responsiveTable.ts`
- Create: `apps/web/src/lib/responsiveRegisterContract.test.ts`
- Modify: `apps/web/src/features/review/TasksInbox.tsx` and test
- Modify: `apps/web/src/features/audits/AuditsListPage.tsx` and test
- Modify: `apps/web/src/features/objectives/ObjectivesRegisterPage.tsx` and test
- Modify: `apps/web/src/features/management-review/ManagementReviewsRegisterPage.tsx` and test

**Interfaces:**

- Consumes: Task 1's unchanged toolbar API and each page's existing single table.
- Produces: `expectResponsiveTable(minWidth: number): HTMLTableElement`; source/rendered contracts for Tasks 720, Audits 800, Objectives 720, and Management Reviews 800.

- [ ] **Step 1: Create the rendered test helper**

Create `apps/web/src/test/responsiveTable.ts`:

```ts
import { screen } from "@testing-library/react";
import { expect } from "vitest";

export function expectResponsiveTable(minWidth: number): HTMLTableElement {
  const tables = screen.getAllByRole("table");
  expect(tables).toHaveLength(1);
  const table = tables[0]!;
  const owners = [
    ...document.querySelectorAll<HTMLElement>('[style*="--table-min-width"]'),
  ].filter((node) => node.contains(table));
  expect(owners).toHaveLength(1);
  expect(owners[0]!.style.getPropertyValue("--table-min-width")).toBe(
    `${minWidth / 16}rem`,
  );
  return table;
}
```

- [ ] **Step 2: Add four failing rendered contracts**

Import `expectResponsiveTable` and add these exact tests, adding `within` where absent:

```tsx
test("contains the complete task table in one 720 px scroll region", async () => {
  renderWithProviders(<TasksInbox />, { route: "/tasks" });
  await screen.findByRole("link", { name: /SOP-PUR-014/ });
  const table = expectResponsiveTable(720);
  expect(within(table).getAllByRole("columnheader")).toHaveLength(5);
  expect(
    within(table).getAllByRole("link", { name: /SOP-PUR-014/ }),
  ).toHaveLength(1);
});

test("contains the complete audit table in one 800 px scroll region", async () => {
  renderWithProviders(<AuditsListPage />, { route: "/audits" });
  await screen.findByText("REC-000061");
  const table = expectResponsiveTable(800);
  expect(within(table).getAllByRole("columnheader")).toHaveLength(5);
  expect(
    within(table).getAllByRole("link", { name: "REC-000061" }),
  ).toHaveLength(1);
});

it("contains the complete objective table in one 720 px scroll region", async () => {
  renderWithProviders(<ObjectivesRegisterPage />, { route: "/objectives" });
  await waitFor(() => expect(screen.getByText("OBJ-001")).toBeInTheDocument());
  const table = expectResponsiveTable(720);
  expect(within(table).getAllByRole("columnheader")).toHaveLength(5);
  expect(within(table).getAllByRole("link", { name: "OBJ-001" })).toHaveLength(
    1,
  );
});

it("contains the complete management-review table in one 800 px scroll region", async () => {
  renderWithProviders(<ManagementReviewsRegisterPage />, {
    route: "/management-reviews",
  });
  await waitFor(() => expect(screen.getByText("MR-001")).toBeInTheDocument());
  const table = expectResponsiveTable(800);
  expect(within(table).getAllByRole("columnheader")).toHaveLength(5);
  expect(within(table).getAllByRole("link", { name: "MR-001" })).toHaveLength(
    1,
  );
});
```

Place each test in its matching page suite; do not combine page renders in one test.

- [ ] **Step 3: Create the first source-inventory guard**

Create `apps/web/src/lib/responsiveRegisterContract.test.ts`:

```ts
import { describe, expect, it } from "vitest";

const sources = import.meta.glob(
  [
    "../features/review/TasksInbox.tsx",
    "../features/audits/AuditsListPage.tsx",
    "../features/objectives/ObjectivesRegisterPage.tsx",
    "../features/management-review/ManagementReviewsRegisterPage.tsx",
  ],
  { eager: true, query: "?raw", import: "default" },
) as Record<string, string>;

const contracts = [
  ["features/review/TasksInbox.tsx", 720],
  ["features/audits/AuditsListPage.tsx", 800],
  ["features/objectives/ObjectivesRegisterPage.tsx", 720],
  ["features/management-review/ManagementReviewsRegisterPage.tsx", 800],
] as const;

function sourceFor(path: string): string {
  const source = Object.entries(sources).find(([key]) =>
    key.endsWith(path),
  )?.[1];
  if (typeof source !== "string")
    throw new Error(`Missing responsive-register source: ${path}`);
  return source;
}

describe("responsive shared-register source contract", () => {
  it.each(contracts)(
    "keeps one %s table in its %i px owner",
    (path, minWidth) => {
      const source = sourceFor(path);
      expect(source.match(/<Table\.ScrollContainer/g)).toHaveLength(1);
      expect(source).toContain(
        `<Table.ScrollContainer minWidth={${minWidth}}>`,
      );
      expect(source.match(/<Table(?:\s|>)/g)).toHaveLength(1);
      expect(source).not.toMatch(/visibleFrom=|hiddenFrom=/);
    },
  );
});
```

- [ ] **Step 4: Run RED**

```bash
npm --prefix apps/web test -- src/lib/responsiveRegisterContract.test.ts src/features/review/TasksInbox.test.tsx src/features/audits/AuditsListPage.test.tsx src/features/objectives/ObjectivesRegisterPage.test.tsx src/features/management-review/ManagementReviewsRegisterPage.test.tsx
```

Expected: all new cases fail for missing scroll owners.

- [ ] **Step 5: Add the four route-owned wrappers**

Insert `<Table.ScrollContainer minWidth={720}>` immediately before the existing Tasks and Objectives tables, and close it immediately after each existing `</Table>`. Insert `<Table.ScrollContainer minWidth={800}>` around the existing Audits and Management Reviews tables in the same way. Keep all props, headers, bodies, refs, key handlers, and controls on their current elements.

- [ ] **Step 6: Run GREEN, static checks, commit, and review**

```bash
npm --prefix apps/web test -- src/lib/responsiveRegisterContract.test.ts src/features/review/TasksInbox.test.tsx src/features/audits/AuditsListPage.test.tsx src/features/objectives/ObjectivesRegisterPage.test.tsx src/features/management-review/ManagementReviewsRegisterPage.test.tsx src/lib/useRowKeyboardNav.test.tsx src/lib/RegisterToolbar.test.tsx
npm --prefix apps/web run typecheck
npm --prefix apps/web exec -- eslint src/test/responsiveTable.ts src/lib/responsiveRegisterContract.test.ts src/features/review/TasksInbox.tsx src/features/review/TasksInbox.test.tsx src/features/audits/AuditsListPage.tsx src/features/audits/AuditsListPage.test.tsx src/features/objectives/ObjectivesRegisterPage.tsx src/features/objectives/ObjectivesRegisterPage.test.tsx src/features/management-review/ManagementReviewsRegisterPage.tsx src/features/management-review/ManagementReviewsRegisterPage.test.tsx
git diff --check
git add apps/web/src/test/responsiveTable.ts apps/web/src/lib/responsiveRegisterContract.test.ts apps/web/src/features/review/TasksInbox.tsx apps/web/src/features/review/TasksInbox.test.tsx apps/web/src/features/audits/AuditsListPage.tsx apps/web/src/features/audits/AuditsListPage.test.tsx apps/web/src/features/objectives/ObjectivesRegisterPage.tsx apps/web/src/features/objectives/ObjectivesRegisterPage.test.tsx apps/web/src/features/management-review/ManagementReviewsRegisterPage.tsx apps/web/src/features/management-review/ManagementReviewsRegisterPage.test.tsx
git commit -m "feat: contain linked register tables"
```

Requirements review verifies exact routes/floors, one action tree, and unchanged links/history. Quality review inspects helper/guard robustness, wrappers, tests, formatting, and diff. Resolve findings before Task 3.

---

### Task 3: Contain the two wide drawer registers

**Files:**

- Modify: `apps/web/src/lib/responsiveRegisterContract.test.ts`
- Modify: `apps/web/src/features/dcr/DcrsRegisterPage.tsx` and test
- Modify: `apps/web/src/features/improvement/ImprovementRegisterPage.tsx` and test

**Interfaces:**

- Consumes: Task 2's helper and source guard.
- Produces: DCR 1040 and Improvement 920 contracts with unchanged drawer and row-navigation ownership.

- [ ] **Step 1: Add failing rendered/source contracts**

```tsx
test("contains the complete DCR table in one 1040 px scroll region", async () => {
  renderWithProviders(<DcrsRegisterPage />, { route: "/dcrs" });
  await screen.findByText("DCR-2026-0001");
  const table = expectResponsiveTable(1040);
  expect(within(table).getAllByRole("columnheader")).toHaveLength(7);
  expect(
    within(table).getAllByRole("button", { name: "DCR-2026-0001" }),
  ).toHaveLength(1);
});

test("contains the complete improvement table in one 920 px scroll region", async () => {
  renderWithProviders(<ImprovementRegisterPage />, { route: "/improvement" });
  await screen.findByText("IMP-2026-0001");
  const table = expectResponsiveTable(920);
  expect(within(table).getAllByRole("columnheader")).toHaveLength(6);
  expect(
    within(table).getAllByRole("button", { name: "IMP-2026-0001" }),
  ).toHaveLength(1);
});
```

Add both paths to the glob array and these entries to `contracts`:

```ts
["features/dcr/DcrsRegisterPage.tsx", 1040],
["features/improvement/ImprovementRegisterPage.tsx", 920],
```

- [ ] **Step 2: Run RED**

```bash
npm --prefix apps/web test -- src/lib/responsiveRegisterContract.test.ts src/features/dcr/DcrsRegisterPage.test.tsx src/features/improvement/ImprovementRegisterPage.test.tsx
```

Expected: only the new DCR and Improvement cases fail for missing wrappers.

- [ ] **Step 3: Implement the two wrappers**

Wrap the existing DCR table directly in `<Table.ScrollContainer minWidth={1040}>` and the existing Improvement table directly in `<Table.ScrollContainer minWidth={920}>`. Do not move toolbars, body refs/key handlers, buttons, filters, or drawers.

- [ ] **Step 4: Run GREEN, static checks, commit, and review**

```bash
npm --prefix apps/web test -- src/lib/responsiveRegisterContract.test.ts src/features/dcr/DcrsRegisterPage.test.tsx src/features/improvement/ImprovementRegisterPage.test.tsx src/lib/useRowKeyboardNav.test.tsx src/lib/RegisterToolbar.test.tsx
npm --prefix apps/web run typecheck
npm --prefix apps/web exec -- eslint src/lib/responsiveRegisterContract.test.ts src/features/dcr/DcrsRegisterPage.tsx src/features/dcr/DcrsRegisterPage.test.tsx src/features/improvement/ImprovementRegisterPage.tsx src/features/improvement/ImprovementRegisterPage.test.tsx
git diff --check
git add apps/web/src/lib/responsiveRegisterContract.test.ts apps/web/src/features/dcr/DcrsRegisterPage.tsx apps/web/src/features/dcr/DcrsRegisterPage.test.tsx apps/web/src/features/improvement/ImprovementRegisterPage.tsx apps/web/src/features/improvement/ImprovementRegisterPage.test.tsx
git commit -m "feat: contain wide drawer registers"
```

Requirements review verifies floors and unchanged local/deep-linked/conflicting-selector drawer behavior. Quality review inspects wrapper placement, one-action assertions, source inventory, tests, and diff. Resolve findings before Task 4.

---

### Task 4: Contain the three contextual drawer registers

**Files:**

- Modify: `apps/web/src/lib/responsiveRegisterContract.test.ts`
- Modify: `apps/web/src/features/risk/RisksRegisterPage.tsx` and test
- Modify: `apps/web/src/features/context/ContextRegisterPage.tsx` and test
- Modify: `apps/web/src/features/interested-parties/InterestedPartiesRegisterPage.tsx` and test

**Interfaces:**

- Consumes: Task 2's helper and Tasks 2-3's six-entry guard.
- Produces: Risk 720, Context 880, Interested Parties 880, and the exact nine-route source inventory.

- [ ] **Step 1: Add failing rendered/source contracts**

```tsx
it("contains the complete risk table in one 720 px scroll region", async () => {
  renderWithProviders(<RisksRegisterPage />, { route: "/risks" });
  await waitFor(() =>
    expect(
      screen.getByText("Supplier single point of failure"),
    ).toBeInTheDocument(),
  );
  const table = expectResponsiveTable(720);
  expect(within(table).getAllByRole("columnheader")).toHaveLength(5);
  expect(
    within(table).getAllByRole("button", {
      name: "Supplier single point of failure",
    }),
  ).toHaveLength(1);
});

it("contains the complete context table in one 880 px scroll region", async () => {
  renderWithProviders(<ContextRegisterPage />, { route: "/context" });
  await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
  const table = expectResponsiveTable(880);
  expect(within(table).getAllByRole("columnheader")).toHaveLength(5);
  expect(
    within(table).getAllByRole("button", {
      name: "Skilled and certified QA team",
    }),
  ).toHaveLength(1);
});

it("contains the complete interested-party table in one 880 px scroll region", async () => {
  renderWithProviders(<InterestedPartiesRegisterPage />, {
    route: "/interested-parties",
  });
  await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
  const table = expectResponsiveTable(880);
  expect(within(table).getAllByRole("columnheader")).toHaveLength(5);
  expect(
    within(table).getAllByRole("button", { name: "Acme Manufacturing" }),
  ).toHaveLength(1);
});
```

Add all three paths to the glob and these contracts:

```ts
["features/risk/RisksRegisterPage.tsx", 720],
["features/context/ContextRegisterPage.tsx", 880],
["features/interested-parties/InterestedPartiesRegisterPage.tsx", 880],
```

Add `it("covers the exact owner-approved nine-route cohort", () => expect(contracts).toHaveLength(9));`.

- [ ] **Step 2: Run RED**

```bash
npm --prefix apps/web test -- src/lib/responsiveRegisterContract.test.ts src/features/risk/RisksRegisterPage.test.tsx src/features/context/ContextRegisterPage.test.tsx src/features/interested-parties/InterestedPartiesRegisterPage.test.tsx
```

Expected: the three new cases fail while the first six source contracts pass.

- [ ] **Step 3: Implement the three wrappers**

Wrap only the existing Risk table in `<Table.ScrollContainer minWidth={720}>`; wrap only the existing Context and Interested Parties tables in `<Table.ScrollContainer minWidth={880}>`. Keep boards, matrices, scorecards, lifecycle panels, toolbars, refs/key handlers, filters, modals, and drawers outside the scroll region.

- [ ] **Step 4: Run GREEN, static checks, commit, and review**

```bash
npm --prefix apps/web test -- src/lib/responsiveRegisterContract.test.ts src/features/risk/RisksRegisterPage.test.tsx src/features/context/ContextRegisterPage.test.tsx src/features/interested-parties/InterestedPartiesRegisterPage.test.tsx src/lib/useRowKeyboardNav.test.tsx src/lib/RegisterToolbar.test.tsx
npm --prefix apps/web run typecheck
npm --prefix apps/web exec -- eslint src/lib/responsiveRegisterContract.test.ts src/features/risk/RisksRegisterPage.tsx src/features/risk/RisksRegisterPage.test.tsx src/features/context/ContextRegisterPage.tsx src/features/context/ContextRegisterPage.test.tsx src/features/interested-parties/InterestedPartiesRegisterPage.tsx src/features/interested-parties/InterestedPartiesRegisterPage.test.tsx
git diff --check
git add apps/web/src/lib/responsiveRegisterContract.test.ts apps/web/src/features/risk/RisksRegisterPage.tsx apps/web/src/features/risk/RisksRegisterPage.test.tsx apps/web/src/features/context/ContextRegisterPage.tsx apps/web/src/features/context/ContextRegisterPage.test.tsx apps/web/src/features/interested-parties/InterestedPartiesRegisterPage.tsx apps/web/src/features/interested-parties/InterestedPartiesRegisterPage.test.tsx
git commit -m "feat: contain contextual register tables"
```

Requirements review verifies the exact nine-route inventory, floors, and separation of non-table content. Quality review inspects uniqueness, drawer/URL preservation, guard completeness, tests, and diff. Resolve findings before Task 5.

---

### Task 5: Verify the integrated cohort and complete whole-branch review

**Files:** Inspect every file changed by Tasks 1-4; modify only when a focused failing proof demonstrates an in-scope defect.

**Interfaces:** Consumes all task commits; produces a review-clean implementation head for the expensive final suite.

- [ ] **Step 1: Run the exact affected preservation selection**

```bash
npm --prefix apps/web test -- \
  src/lib/RegisterToolbar.test.tsx \
  src/lib/responsiveRegisterContract.test.ts \
  src/lib/useRowKeyboardNav.test.tsx \
  src/features/review/TasksInbox.test.tsx \
  src/features/audits/AuditsListPage.test.tsx \
  src/features/dcr/DcrsRegisterPage.test.tsx \
  src/features/objectives/ObjectivesRegisterPage.test.tsx \
  src/features/management-review/ManagementReviewsRegisterPage.test.tsx \
  src/features/improvement/ImprovementRegisterPage.test.tsx \
  src/features/risk/RisksRegisterPage.test.tsx \
  src/features/context/ContextRegisterPage.test.tsx \
  src/features/interested-parties/InterestedPartiesRegisterPage.test.tsx \
  src/features/library/LibraryPage.test.tsx \
  src/features/compliance/CompliancePage.test.tsx \
  src/features/capa/NcrsPage.test.tsx \
  src/features/reports/ReportsRegisterPage.test.tsx
```

Expected: all 16 files pass without unhandled errors, hook-order warnings, duplicate actions, URL/history regressions, drawer-owner regressions, keyboard regressions, or axe violations.

- [ ] **Step 2: Run static, build, formatting, and structural guards**

```bash
npm --prefix apps/web run lint
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
git diff --check 5bced6c..HEAD
git diff --check
rg -n -U '<Table\.Tr[^>]*(onClick|onKeyDown|tabIndex|role=)' apps/web/src --glob '*.tsx'
```

Run app-owned Prettier on every changed TS/TSX file before its check. Expected: all gates exit 0; the structural-row guard has no match. Record transformed-module/assets and retain only the existing chunk advisory if unchanged.

- [ ] **Step 3: Run independent requirements review**

Review `082ba310..HEAD` against all design acceptance criteria: exact route/floor matrix; 320 px source/style contract; existing 48 em breakpoint and desktop width; one semantic/control tree; full columns/native actions; URL/history/drawers/application boundaries; and exclusion of slice-8 claims/out-of-scope surfaces. For a finding, write the smallest failing test, prove RED, fix, rerun affected checks, and commit `fix: address responsive cohort review`.

- [ ] **Step 4: Run independent code-quality review**

Inspect wrapper placement, brittle selectors, false layout claims, duplicate helpers, raw-source matching, hidden controls, accessibility, accidental formatting, unrelated edits, and site data. Resolve every in-scope Critical or Important through RED/GREEN proof, then rerun Steps 1-2.

- [ ] **Step 5: Record the implementation evidence head**

```bash
git status --short --branch
git rev-parse --short HEAD
git log --oneline 082ba310..HEAD
```

Expected: clean worktree. Use this exact short SHA in Task 6; do not substitute `082ba310` or the later documentation-only evidence commit.

---

### Task 6: Run final evidence and close repository authority

**Files:**

- Modify: `docs/current-status.md`
- Modify: `docs/slice-history.md`
- Verify: `docs/open-residuals.md` remains unchanged unless implementation discovers a current residual with a concrete closure contract

**Interfaces:** Consumes Task 5's reviewed head and exact outputs; produces honest current/historical evidence while retaining inherited non-web facts.

- [ ] **Step 1: Run the complete web suite as a durable process job**

Use the process-jobs start workflow from the isolated worktree with direct argv `npm --prefix apps/web test`. Retrieve the finished result only through the supported continuation/result workflow. Expected: exit 0 with every file/test passing and no unhandled error. Record exact file/test counts, duration, job id, and retained Node `localStorage` warning. Do not rerun merely to erase a failure; classify and reproduce it first.

- [ ] **Step 2: Update `docs/current-status.md` from fresh evidence only**

Set `baseline_commit` to Task 5's exact implementation SHA, `last_shipped_slice` to `S-responsive-data-heavy-views`, and web counts to Step 1's exact totals. Leave API, contract, integration, migration, and CI counts unchanged. Replace shipped/verification prose with the responsive outcome, exact checks/reviews, warnings, and unavailable-proof disclaimer. Preserve the intentional main-squash versus implementation-evidence distinction.

- [ ] **Step 3: Add the Programme 1 history entry**

Insert `### S-responsive-data-heavy-views — localized shared-register containment` below the Programme 1 heading in `docs/slice-history.md`. Record the nine routes/floors, toolbar contract, single-tree preservation, RED/GREEN/reviews, exact focused/static/full results, later authority results, and honest slice-8/host boundary. Link design, plan, and debt record. Do not rewrite older history.

- [ ] **Step 4: Format scoped documentation**

```bash
apps/web/node_modules/.bin/prettier --write docs/current-status.md docs/superpowers/specs/2026-08-13-s-responsive-data-heavy-views-design.md docs/superpowers/plans/2026-08-13-s-responsive-data-heavy-views.md docs/debt/20260813144730-responsive-register-cohort.md
apps/web/node_modules/.bin/prettier --check docs/current-status.md docs/superpowers/specs/2026-08-13-s-responsive-data-heavy-views-design.md docs/superpowers/plans/2026-08-13-s-responsive-data-heavy-views.md docs/debt/20260813144730-responsive-register-cohort.md
```

Expected: exit 0. Do not whole-file format `docs/slice-history.md`.

- [ ] **Step 5: Run authority, site-data, and diff gates**

```bash
bash scripts/tests/test-agent-authority.sh
bash scripts/tests/test-claude-hooks.sh
./scripts/check-repo-authority.sh
bash scripts/tests/test-check-no-site-data.sh
./scripts/check-no-site-data.sh
git diff --check 082ba310..HEAD
git diff --check
```

Expected: 91/91 authority fixtures, seven compatibility assertions, `AUTHORITY_OK`, 13/13 site-data fixtures, clean direct scan, and clean diffs.

- [ ] **Step 6: Inspect scope, commit evidence, and rerun final guards**

```bash
git diff --stat 082ba310..HEAD
git diff --name-status 082ba310..HEAD
git status --short --branch
git log --oneline 082ba310..HEAD
```

Confirm no out-of-scope change and that primary-checkout `.superdesign/` plus both prunable registrations remain untouched. Then:

```bash
git add docs/current-status.md docs/slice-history.md
git commit -m "docs: record responsive register evidence"
git diff --check 082ba310..HEAD
git status --short --branch
./scripts/check-repo-authority.sh
```

Expected: clean branch, clean range, and `AUTHORITY_OK`.

- [ ] **Step 7: Hand off the branch**

Report outcome, files, commits, exact checks, desktop/compatibility decisions, warnings, and unverified layers. State that Playwright, real viewport/clipping/scroll reachability, request-intercepted failures, focus-ring/forced-colors, screen-reader, Docker-backed, API/contract/integration/migration, deployment, and Fedora proofs did not run. Use the finishing-development-branch workflow to offer local integration, push/PR, or preservation; never push directly to `main`.
