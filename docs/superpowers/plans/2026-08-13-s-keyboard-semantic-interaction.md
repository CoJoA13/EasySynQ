# S-keyboard-semantic-interaction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the last two interactive table rows with visible native primary controls and make the shared arrow-row enhancement ignore independent nested actions.

**Architecture:** Keep every `Table.Tr` structural. CAPA List and Audit Programme each gain one Mantine Anchor-button in the identifying cell, while the existing `useRowKeyboardNav` hook remains an optional focus-only enhancement constrained to events from marked primary controls.

**Tech Stack:** React 19, TypeScript 6, React Router 7, Mantine 7, TanStack Query 5, Vitest 4, Testing Library, MSW, jest-axe.

## Global Constraints

- Baseline is `f2d0b56`; the owner-approved design checkpoint is `d03167f` plus its factual local-open correction in this plan checkpoint.
- Binding behavior is `docs/superpowers/specs/2026-08-13-s-keyboard-semantic-interaction-design.md` and `docs/adr/0002-use-native-primary-controls-for-table-actions.md`.
- Read `docs/debt/20260813074918-native-row-control-pattern.md` before changing the shared helper or either feature table.
- A `Table.Tr` remains structural: no `onClick`, `onKeyDown`, `tabIndex`, interactive role, pointer-only activation, stretched overlay, or synthetic Enter/Space handling.
- CAPA List uses a visible native Open CAPA button and keeps card/list opens local-only; externally supplied `?capa=` state retains its shipped live synchronization, removal, conflict, history, and drawer-focus behavior.
- Audit Programme uses a visible native selection button with `aria-pressed`; selection remains local, defaults as it does today, and stays independent from Edit.
- Arrow Up/Down moves focus only between marked primary controls, never activates or selects, and never intercepts an unmarked nested action.
- Keep every primary control in ordinary tab order; do not introduce roving `tabIndex`, grid-widget semantics, Home/End, typeahead, or selection-following-focus.
- Preserve existing visible copy, status badges, permissions, loading/empty/forbidden/error states, QueryClient/provider identity, URL-state classification, route chrome/recovery, mutation-feedback lifetime, auth/setup gates, and 404 behavior.
- Do not change API/OpenAPI/generated contracts, migrations/database, permissions, dependencies/lockfiles, Keycloak, notification behavior, telemetry, deployment, responsive data views, Playwright, theme tokens, or unrelated residuals.
- Use TDD: add each focused failing proof before its production change, run the smallest RED/GREEN command, then the affected selection.
- Use `apply_patch` for edits, the app-owned Prettier binary for formatting, and `bash scripts/check-no-site-data.sh` before every documentation handoff.
- Work only in `/tmp/EasySynQ-keyboard-semantic-interaction`; preserve the primary checkout's owner-owned `.superdesign/` and the unrelated prunable worktree registrations.
- Record the host limitations honestly: contributor doctor reports non-22 Node, missing PostgreSQL client, and unavailable Docker runtime access. Do not describe browser, screen-reader, responsive, Fedora, Docker-backed, API, contract, or integration proofs as passed unless they actually run.

---

### Task 1: Constrain the shared arrow-row enhancement to primary controls

**Files:**

- Modify: `apps/web/src/lib/useRowKeyboardNav.ts`
- Modify: `apps/web/src/lib/useRowKeyboardNav.test.tsx`
- Read: `docs/debt/20260813074918-native-row-control-pattern.md`

**Interfaces:**

- Consumes: elements marked with the existing `data-rownav` attribute inside one container.
- Produces: unchanged public signature `useRowKeyboardNav<E extends HTMLElement>(): { ref: RefObject<E | null>; onKeyDown: (e: KeyboardEvent<E>) => void }` with the narrower event-origin contract.
- Later tasks rely on: Arrow Up/Down is handled only when `e.target === document.activeElement` and that target matches `[data-rownav]`; independent controls are untouched.

- [ ] **Step 1: Extend the harness with activation and an independent action**

Change `Demo` so each row has a marked primary button plus an unmarked Edit button, and expose activation callbacks:

```tsx
function Demo({
  onPrimary = () => undefined,
  onEdit = () => undefined,
}: {
  onPrimary?: (id: string) => void;
  onEdit?: (id: string) => void;
}) {
  const nav = useRowKeyboardNav<HTMLTableSectionElement>();
  return (
    <MantineProvider>
      <Table>
        <Table.Tbody ref={nav.ref} onKeyDown={nav.onKeyDown}>
          {["r1", "r2", "r3"].map((id) => (
            <Table.Tr key={id}>
              <Table.Td>
                <button
                  type="button"
                  data-rownav
                  data-testid={id}
                  onClick={() => onPrimary(id)}
                >
                  {id}
                </button>
              </Table.Td>
              <Table.Td>
                <button
                  type="button"
                  data-testid={`edit-${id}`}
                  onClick={() => onEdit(id)}
                >
                  Edit {id}
                </button>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </MantineProvider>
  );
}
```

- [ ] **Step 2: Write the helper RED tests**

Add these exact behavior proofs:

```tsx
it("moves focus without activating a primary control", () => {
  const onPrimary = vi.fn();
  const { getByTestId } = render(<Demo onPrimary={onPrimary} />);
  const r1 = getByTestId("r1");
  const r2 = getByTestId("r2");
  r1.focus();
  fireEvent.keyDown(r1, { key: "ArrowDown" });
  expect(r2).toHaveFocus();
  expect(onPrimary).not.toHaveBeenCalled();
});

it("does not intercept arrows from an independent unmarked action", () => {
  const { getByTestId } = render(<Demo />);
  const edit = getByTestId("edit-r2");
  edit.focus();
  const defaultAllowed = fireEvent.keyDown(edit, { key: "ArrowDown" });
  expect(defaultAllowed).toBe(true);
  expect(edit).toHaveFocus();
});
```

Import `vi` from Vitest. Retain the existing clamp and non-arrow tests.

- [ ] **Step 3: Run the focused RED test**

Run:

```bash
npm --prefix apps/web test -- src/lib/useRowKeyboardNav.test.tsx
```

Expected: the unmarked Edit test FAILS because the current helper prevents the arrow event and moves
focus to the first marked control. The activation and existing clamp tests remain green.

- [ ] **Step 4: Add the event-origin guard**

In `useRowKeyboardNav.ts`, keep the public interface and existing clamp algorithm, but add this guard
before querying the container:

```ts
const target = e.target instanceof HTMLElement ? e.target : null;
if (
  target === null ||
  target !== document.activeElement ||
  !target.matches("[data-rownav]")
) {
  return;
}
```

Do not use `closest()`; a keyboard event from a nested independent control must not inherit another
element's primary-action marker.

- [ ] **Step 5: Run focused GREEN and representative helper consumers**

Run:

```bash
npm --prefix apps/web test -- \
  src/lib/useRowKeyboardNav.test.tsx \
  src/features/audits/AuditsListPage.test.tsx \
  src/features/review/TasksInbox.test.tsx \
  src/features/objectives/ObjectivesRegisterPage.test.tsx
npm --prefix apps/web run typecheck
```

Expected: all selected tests and typecheck pass; no primary control is activated by Arrow movement.

- [ ] **Step 6: Format, inspect, and commit Task 1**

Run:

```bash
cd apps/web
npm exec prettier -- --write src/lib/useRowKeyboardNav.ts src/lib/useRowKeyboardNav.test.tsx
npm exec eslint -- src/lib/useRowKeyboardNav.ts src/lib/useRowKeyboardNav.test.tsx
npm exec tsc -- --noEmit
cd ../..
git diff --check -- apps/web/src/lib/useRowKeyboardNav.ts apps/web/src/lib/useRowKeyboardNav.test.tsx
git diff -- apps/web/src/lib/useRowKeyboardNav.ts apps/web/src/lib/useRowKeyboardNav.test.tsx
git add apps/web/src/lib/useRowKeyboardNav.ts apps/web/src/lib/useRowKeyboardNav.test.tsx
git commit -m "fix(web): constrain row keyboard navigation"
```

---

### Task 2: Replace CAPA list row activation with a native primary button

**Files:**

- Modify: `apps/web/src/features/capa/CapaBoardPage.tsx`
- Modify: `apps/web/src/features/capa/CapaBoardPage.test.tsx`
- Test: `apps/web/src/features/capa/CapaRouting.test.tsx`
- Test: `apps/web/src/features/capa/CapaCard.test.tsx`

**Interfaces:**

- Consumes: Task 1's unchanged `useRowKeyboardNav<HTMLTableSectionElement>()` interface.
- Produces: one `Anchor component="button"` per CAPA list row, marked `data-rownav`, with accessible name `Open CAPA <displayed identifier>: <displayed title>`.
- Preserves: `setSelected(capa.id)` local open, `CapaDrawer`, and all shipped URL-selector behavior.

- [ ] **Step 1: Replace the old row-keyboard test with native-control RED tests**

In `CapaBoardPage.test.tsx`, replace `a list-view row opens the drawer via keyboard (Enter), not
mouse-only` and add these tests. Use `userEvent.setup()` and switch to List before querying buttons:

```tsx
test("CAPA list rows are structural and expose a named native primary button", async () => {
  const u = userEvent.setup();
  renderWithProviders(<CapaBoardPage />, { route: "/capa" });
  await u.click(await screen.findByRole("radio", { name: "List" }));
  const row = screen.getByRole("row", { name: /REC-000031/ });
  expect(row).not.toHaveAttribute("tabindex");
  expect(
    within(row).getByRole("button", {
      name: "Open CAPA REC-000031: Supplier re-evaluation overdue for 2 vendors",
    }),
  ).toBeInTheDocument();
});

test.each(["{Enter}", " "])(
  "the native CAPA control opens the drawer with %s",
  async (key) => {
    const u = userEvent.setup();
    renderWithProviders(<CapaBoardPage />, { route: "/capa" });
    await u.click(await screen.findByRole("radio", { name: "List" }));
    const open = screen.getByRole("button", { name: /^Open CAPA REC-000031:/ });
    open.focus();
    await u.keyboard(key);
    expect(await screen.findByText("Closed-loop thread")).toBeInTheDocument();
  },
);

test("ArrowDown moves to the next CAPA control without opening it", async () => {
  const u = userEvent.setup();
  renderWithProviders(<CapaBoardPage />, { route: "/capa" });
  await u.click(await screen.findByRole("radio", { name: "List" }));
  const controls = screen.getAllByRole("button", { name: /^Open CAPA / });
  controls[0]!.focus();
  await u.keyboard("{ArrowDown}");
  expect(controls[1]).toHaveFocus();
  expect(screen.queryByText("Closed-loop thread")).toBeNull();
});

test("clicking ordinary CAPA cell content does not open the drawer", async () => {
  const u = userEvent.setup();
  renderWithProviders(<CapaBoardPage />, { route: "/capa" });
  await u.click(await screen.findByRole("radio", { name: "List" }));
  const row = screen.getByRole("row", { name: /REC-000031/ });
  await u.click(within(row).getByText("Audit"));
  expect(screen.queryByText("Closed-loop thread")).toBeNull();
});
```

Add a List-specific axe test by switching the SegmentedControl before calling `axe(container)`.

- [ ] **Step 2: Run the CAPA RED test**

Run:

```bash
npm --prefix apps/web test -- src/features/capa/CapaBoardPage.test.tsx
```

Expected: FAIL because the row is still focusable/interactive, there is no `Open CAPA …` list button,
and arrows are handled by the row's handwritten key handler rather than the shared helper.

- [ ] **Step 3: Implement the native CAPA primary control**

Add `Anchor` to the Mantine imports and import the helper:

```ts
import { useRowKeyboardNav } from "../../lib/useRowKeyboardNav";
```

Call the hook unconditionally with the other hooks, before every early return:

```ts
const nav = useRowKeyboardNav<HTMLTableSectionElement>();
```

Attach it to the list body and replace the interactive row with this shape:

```tsx
<Table.Tbody ref={nav.ref} onKeyDown={nav.onKeyDown}>
  {filtered.map((c: Capa) => {
    const identifier = c.identifier ?? "—";
    const title = c.title ?? "(untitled)";
    return (
      <Table.Tr key={c.id}>
        <Table.Td>
          <Anchor
            component="button"
            type="button"
            data-rownav
            onClick={() => setSelected(c.id)}
            aria-label={`Open CAPA ${identifier}: ${title}`}
          >
            {identifier}
          </Anchor>
        </Table.Td>
        <Table.Td>{title}</Table.Td>
        <Table.Td>{SEVERITY_LABEL[c.severity]}</Table.Td>
        <Table.Td>{SOURCE_LABEL[c.source]}</Table.Td>
        <Table.Td>{CLOSE_STATE_LABEL[c.close_state]}</Table.Td>
      </Table.Tr>
    );
  })}
</Table.Tbody>
```

Delete the row `tabIndex`, cursor style, `onClick`, `onKeyDown`, and synthetic Enter/Space comment. Do
not write `capa` to the URL from this button.

- [ ] **Step 4: Run focused GREEN and URL/focus preservation tests**

Run:

```bash
npm --prefix apps/web test -- \
  src/lib/useRowKeyboardNav.test.tsx \
  src/features/capa/CapaBoardPage.test.tsx \
  src/features/capa/CapaRouting.test.tsx \
  src/features/capa/CapaCard.test.tsx \
  src/features/capa/CapaDrawer.test.tsx
```

Expected: native list activation, Arrow focus, local-only opens, deep-link/remove/conflict behavior,
Escape close, drawer focus restoration, board cards, and axe coverage all pass.

- [ ] **Step 5: Prove no CAPA row-level activation remains**

Run:

```bash
rg -n -U -P '(?s)<Table\.Tr(?:(?!>).)*(?:onClick|onKeyDown|tabIndex|role\s*=)[^>]*>' \
  apps/web/src/features/capa/CapaBoardPage.tsx
```

Expected: exit 1 with no matches. Then run:

```bash
npm --prefix apps/web run typecheck
```

Expected: PASS.

- [ ] **Step 6: Format, inspect, and commit Task 2**

Run:

```bash
cd apps/web
npm exec prettier -- --write \
  src/features/capa/CapaBoardPage.tsx \
  src/features/capa/CapaBoardPage.test.tsx
npm exec eslint -- \
  src/features/capa/CapaBoardPage.tsx \
  src/features/capa/CapaBoardPage.test.tsx
npm exec tsc -- --noEmit
cd ../..
git diff --check -- \
  apps/web/src/features/capa/CapaBoardPage.tsx \
  apps/web/src/features/capa/CapaBoardPage.test.tsx
git diff -- \
  apps/web/src/features/capa/CapaBoardPage.tsx \
  apps/web/src/features/capa/CapaBoardPage.test.tsx
git add \
  apps/web/src/features/capa/CapaBoardPage.tsx \
  apps/web/src/features/capa/CapaBoardPage.test.tsx
git commit -m "fix(web): use native CAPA list actions"
```

---

### Task 3: Replace Audit Programme row selection with a native pressed button

**Files:**

- Modify: `apps/web/src/features/audits/ProgrammePage.tsx`
- Modify: `apps/web/src/features/audits/ProgrammePage.test.tsx`
- Test: `apps/web/src/features/audits/hooks.test.tsx`

**Interfaces:**

- Consumes: Task 1's guarded `useRowKeyboardNav<HTMLTableSectionElement>()` interface.
- Produces: one marked Anchor-button per programme with accessible name `Select programme <identifier>: <title>` and `aria-pressed={selected?.id === programme.id}`.
- Preserves: local `selectedId`, first-row default, `useAuditPlans(selected?.id)`, selected-row presentation, and independent permission-gated Edit.

- [ ] **Step 1: Add Audit Programme RED tests for structural rows and pressed state**

Add these tests to `ProgrammePage.test.tsx`:

```tsx
test("programme rows are structural and expose one pressed native selection control", async () => {
  renderWithProviders(<ProgrammePage />, { route: "/audits/programme" });
  const firstRow = await screen.findByRole("row", { name: /AUDPROG-000001/ });
  const secondRow = screen.getByRole("row", { name: /AUDPROG-000002/ });
  expect(firstRow).not.toHaveAttribute("tabindex");
  expect(
    within(firstRow).getByRole("button", {
      name: "Select programme AUDPROG-000001: 2026 Internal Audit Programme",
      pressed: true,
    }),
  ).toBeInTheDocument();
  expect(
    within(secondRow).getByRole("button", {
      name: "Select programme AUDPROG-000002: 2025 Programme",
      pressed: false,
    }),
  ).toBeInTheDocument();
});

test("programme arrow navigation changes focus without changing selection", async () => {
  const u = userEvent.setup();
  renderWithProviders(<ProgrammePage />, { route: "/audits/programme" });
  const first = await screen.findByRole("button", {
    name: /^Select programme AUDPROG-000001:/,
  });
  const second = screen.getByRole("button", {
    name: /^Select programme AUDPROG-000002:/,
  });
  first.focus();
  await u.keyboard("{ArrowDown}");
  expect(second).toHaveFocus();
  expect(first).toHaveAttribute("aria-pressed", "true");
  expect(second).toHaveAttribute("aria-pressed", "false");
  expect(screen.getByText("Plans — AUDPROG-000001")).toBeInTheDocument();
});

test.each(["{Enter}", " "])(
  "the native programme control selects with %s",
  async (key) => {
    const u = userEvent.setup();
    renderWithProviders(<ProgrammePage />, { route: "/audits/programme" });
    const second = await screen.findByRole("button", {
      name: /^Select programme AUDPROG-000002:/,
    });
    second.focus();
    await u.keyboard(key);
    expect(second).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("Plans — AUDPROG-000002")).toBeInTheDocument();
  },
);
```

- [ ] **Step 2: Add RED tests for cell and Edit isolation**

Use the existing `grant(["audit.plan"])` helper:

```tsx
test("ordinary cells and Edit do not change programme selection", async () => {
  grant(["audit.plan"]);
  const u = userEvent.setup();
  renderWithProviders(<ProgrammePage />, { route: "/audits/programme" });
  const secondRow = await screen.findByRole("row", { name: /AUDPROG-000002/ });
  await u.click(within(secondRow).getByText("2025"));
  expect(screen.getByText("Plans — AUDPROG-000001")).toBeInTheDocument();

  const edit = within(secondRow).getByRole("button", { name: "Edit" });
  edit.focus();
  await u.keyboard("{ArrowUp}");
  expect(edit).toHaveFocus();
  await u.click(edit);
  expect(await screen.findByRole("dialog")).toBeInTheDocument();
  expect(screen.getByText("Plans — AUDPROG-000001")).toBeInTheDocument();
});
```

Update existing tests whose intent is programme selection to click the named `Select programme …`
button rather than bare identifier text.

- [ ] **Step 3: Run the Audit Programme RED test**

Run:

```bash
npm --prefix apps/web test -- src/features/audits/ProgrammePage.test.tsx
```

Expected: FAIL because selection is still row-click-only, buttons/pressed state do not exist, ordinary
cell clicks select, and the table does not use the shared Arrow helper.

- [ ] **Step 4: Implement the native programme selection control**

Add `Anchor` to the Mantine imports and import the helper:

```ts
import { useRowKeyboardNav } from "../../lib/useRowKeyboardNav";
```

Call it with the other unconditional hooks:

```ts
const nav = useRowKeyboardNav<HTMLTableSectionElement>();
```

Attach it to the programme body and replace the row activation with:

```tsx
<Table.Tbody ref={nav.ref} onKeyDown={nav.onKeyDown}>
  {rows.map((p) => (
    <Table.Tr key={p.id} data-selected={selected?.id === p.id || undefined}>
      <Table.Td>
        <Anchor
          component="button"
          type="button"
          data-rownav
          onClick={() => setSelectedId(p.id)}
          aria-pressed={selected?.id === p.id}
          aria-label={`Select programme ${p.identifier}: ${p.title}`}
        >
          {p.identifier}
        </Anchor>
      </Table.Td>
      <Table.Td>
        <Text lineClamp={1}>{p.title}</Text>
      </Table.Td>
      {/* || not ??: a cleared period arrives as "" — render the same em-dash as null. */}
      <Table.Td>{p.period || "—"}</Table.Td>
      <Table.Td>
        <StatusBadge
          tone={p.archived ? "neutral" : "success"}
          label={p.archived ? "Archived" : "Active"}
          kind="Programme status"
        />
      </Table.Td>
      <Table.Td>
        {can("audit.plan") && (
          <Button size="xs" variant="subtle" onClick={() => setEditing(p)}>
            Edit
          </Button>
        )}
      </Table.Td>
    </Table.Tr>
  ))}
</Table.Tbody>
```

Delete the row `onClick`, pointer cursor, and Edit `stopPropagation()`.

- [ ] **Step 5: Run focused GREEN and Audit preservation tests**

Run:

```bash
npm --prefix apps/web test -- \
  src/lib/useRowKeyboardNav.test.tsx \
  src/features/audits/ProgrammePage.test.tsx \
  src/features/audits/hooks.test.tsx
npm --prefix apps/web run typecheck
```

Expected: structural-row, pressed-state, native activation, Arrow focus-only, ordinary-cell, Edit
isolation, default selection, Plans, archive, form, forbidden, and axe tests all pass.

- [ ] **Step 6: Prove no Audit Programme row-level activation remains**

Run:

```bash
rg -n -U -P '(?s)<Table\.Tr(?:(?!>).)*(?:onClick|onKeyDown|tabIndex|role\s*=)[^>]*>' \
  apps/web/src/features/audits/ProgrammePage.tsx
```

Expected: exit 1 with no matches.

- [ ] **Step 7: Format, inspect, and commit Task 3**

Run:

```bash
cd apps/web
npm exec prettier -- --write \
  src/features/audits/ProgrammePage.tsx \
  src/features/audits/ProgrammePage.test.tsx
npm exec eslint -- \
  src/features/audits/ProgrammePage.tsx \
  src/features/audits/ProgrammePage.test.tsx
npm exec tsc -- --noEmit
cd ../..
git diff --check -- \
  apps/web/src/features/audits/ProgrammePage.tsx \
  apps/web/src/features/audits/ProgrammePage.test.tsx
git diff -- \
  apps/web/src/features/audits/ProgrammePage.tsx \
  apps/web/src/features/audits/ProgrammePage.test.tsx
git add \
  apps/web/src/features/audits/ProgrammePage.tsx \
  apps/web/src/features/audits/ProgrammePage.test.tsx
git commit -m "fix(web): use native programme selection"
```

---

### Task 4: Complete verification, independent review, and authority evidence

**Files:**

- Modify: `docs/current-status.md`
- Modify: `docs/slice-history.md`
- Modify only for review corrections: `docs/superpowers/specs/2026-08-13-s-keyboard-semantic-interaction-design.md`
- Modify only for review corrections: `docs/superpowers/plans/2026-08-13-s-keyboard-semantic-interaction.md`
- Read: `docs/adr/0002-use-native-primary-controls-for-table-actions.md`
- Read: `docs/debt/20260813074918-native-row-control-pattern.md`

**Interfaces:**

- Consumes: reviewed commits from Tasks 1–3.
- Produces: fresh whole-branch evidence and dated repository-authority updates; no additional product behavior.

- [ ] **Step 1: Run the complete focused semantic-interaction selection**

Run:

```bash
npm --prefix apps/web test -- \
  src/lib/useRowKeyboardNav.test.tsx \
  src/features/capa/CapaBoardPage.test.tsx \
  src/features/capa/CapaRouting.test.tsx \
  src/features/capa/CapaCard.test.tsx \
  src/features/capa/CapaDrawer.test.tsx \
  src/features/audits/ProgrammePage.test.tsx \
  src/features/audits/hooks.test.tsx \
  src/features/audits/AuditsListPage.test.tsx \
  src/features/context/ContextSwotBoard.test.tsx \
  src/features/context/ContextRegisterPage.test.tsx \
  src/features/interested-parties/InterestedPartyTypeBoard.test.tsx \
  src/features/interested-parties/InterestedPartiesRegisterPage.test.tsx \
  src/features/library/LibraryPage.test.tsx \
  src/features/dcr/DcrsRegisterPage.test.tsx \
  src/features/improvement/ImprovementRegisterPage.test.tsx \
  src/features/objectives/ObjectivesRegisterPage.test.tsx \
  src/features/management-review/ManagementReviewsRegisterPage.test.tsx \
  src/features/review/TasksInbox.test.tsx
```

Expected: PASS without unhandled exceptions, hook-order warnings, duplicate activation, invalid nested
interactive semantics, inaccessible names, or axe violations.

- [ ] **Step 2: Run the whole-source structural-row guard**

Run:

```bash
if rg -n -U -P '(?s)<Table\.Tr(?:(?!>).)*(?:onClick|onKeyDown|tabIndex|role\s*=)[^>]*>' \
  apps/web/src --glob '*.tsx'; then
  echo 'interactive Table.Tr props remain' >&2
  exit 1
fi
```

Expected: PASS with no matching production or test `Table.Tr` opening tag. Manually inspect the Task
diff to confirm primary action is a visible link/button and ordinary cells remain inert.

- [ ] **Step 3: Run static web gates**

Run:

```bash
npm --prefix apps/web run lint
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
```

Expected: PASS from the committed lockfile. Record the module count, asset sizes, and any advisory
without treating an advisory as a failure or suppressing it.

- [ ] **Step 4: Run the full web suite as a durable job**

Invoke `codex-process-jobs:start` with exact argv:

```text
npm --prefix apps/web test
```

and working directory:

```text
/tmp/EasySynQ-keyboard-semantic-interaction
```

End the assigning turn. After completion delivery, use `codex-process-jobs:result <job-id> --peek` and
record the exit status, exact file/test totals, Vitest duration, warnings, and any unhandled error. Do
not poll or convert an unavailable run into a pass.

- [ ] **Step 5: Run authority, compatibility, formatting, site-data, and diff guards**

Run:

```bash
bash scripts/tests/test-agent-authority.sh
bash scripts/tests/test-claude-hooks.sh
./scripts/check-repo-authority.sh
bash scripts/tests/test-check-no-site-data.sh
./scripts/check-no-site-data.sh
apps/web/node_modules/.bin/prettier --check \
  docs/superpowers/specs/2026-08-13-s-keyboard-semantic-interaction-design.md \
  docs/superpowers/plans/2026-08-13-s-keyboard-semantic-interaction.md \
  docs/adr/0002-use-native-primary-controls-for-table-actions.md \
  docs/debt/20260813074918-native-row-control-pattern.md \
  docs/current-status.md
git diff --check f2d0b56..HEAD
git status --short --branch
```

Check `docs/slice-history.md` separately against its exact baseline before making any formatting claim;
do not mass-format historical entries.

- [ ] **Step 6: Perform independent requirements and quality review**

Invoke `superpowers:requesting-code-review` and `codex-engineering-guardrails:code-verification`. Review
the complete range `f2d0b56..HEAD` against every approved design acceptance criterion, with special
attention to:

- source-wide absence of interactive `Table.Tr` props;
- native Enter/Space behavior without duplicate synthetic handlers;
- accessible names and label-in-name;
- pressed selection versus focus-only Arrow movement;
- Edit and ordinary-cell isolation;
- CAPA local-only open plus external URL synchronization and drawer focus restoration;
- helper compatibility across existing link/button consumers; and
- unchanged QueryClient, route, mutation-feedback, auth/setup, and error boundaries.

Classify findings as Critical, Important, or Minor. Fix every in-scope Critical or Important finding
with a focused failing proof, rerun affected checks, and repeat scoped review until none remains. Invoke
`debt-ops:add` immediately for any newly deferred material decision.

- [ ] **Step 7: Update current status and slice history from fresh evidence**

In `docs/current-status.md`, update only:

- `as_of` to the final evidence date;
- `baseline_commit` to the final implementation evidence commit, not a future squash SHA;
- `last_shipped_slice` to `S-keyboard-semantic-interaction`;
- `web_test_files` and `web_tests` from the fresh complete web suite; and
- the concise shipped boundary and verification narrative.

Leave API, contract, integration, migration, and CI totals unchanged unless their complete gates were
actually refreshed.

Append a dated `S-keyboard-semantic-interaction` entry to `docs/slice-history.md` recording:

- the native-primary-control decision, ADR, rejected whole-row alternatives, and debt payoff trigger;
- the two baseline defects and exact behavior changes;
- structural rows, safe accessible names, native Enter/Space, pressed selection, Arrow focus-only, and
  Edit/cell isolation;
- CAPA URL/drawer and application-boundary preservation;
- exact RED/GREEN, focused, static, full-suite, authority, site-data, and review evidence;
- unchanged API/schema/auth/dependency/responsive/Playwright boundaries; and
- every unavailable proof without describing it as passed.

- [ ] **Step 8: Re-run final guards after evidence edits**

Run the complete focused selection from Step 1, the source guard from Step 2, lint, typecheck, build,
scoped documentation Prettier, authority, site-data, and diff guards again. The earlier full suite remains
valid because only authority prose changes after it; if any production source or test changes after the
full suite, rerun the full suite through a new durable job.

- [ ] **Step 9: Commit final evidence**

Run:

```bash
git add docs/current-status.md docs/slice-history.md
git diff --quiet -- docs/superpowers/specs/2026-08-13-s-keyboard-semantic-interaction-design.md || \
  git add docs/superpowers/specs/2026-08-13-s-keyboard-semantic-interaction-design.md
git diff --quiet -- docs/superpowers/plans/2026-08-13-s-keyboard-semantic-interaction.md || \
  git add docs/superpowers/plans/2026-08-13-s-keyboard-semantic-interaction.md
git commit -m "docs: record keyboard semantic evidence"
git status --short --branch
git log --oneline --decorate f2d0b56..HEAD
```

Expected: clean isolated worktree with reviewable scoped commits. Do not push, open a pull request, merge,
or remove the worktree until the owner selects the publication path.

## Plan self-review

- **Spec coverage:** Task 1 owns the shared Arrow event-origin boundary; Task 2 owns CAPA's structural row,
  native control, local URL behavior, focus, and axe proofs; Task 3 owns Audit Programme's structural row,
  pressed selection, Plans/Edit isolation, and axe proofs; Task 4 owns whole-source enforcement,
  preservation suites, independent review, and evidence.
- **Scope:** The plan touches two production feature files, one existing helper, their tests, and direct
  authority/evidence documents. It creates no component abstraction, route, provider, dependency, API,
  migration, responsive strategy, or Playwright harness.
- **Types and names:** Both tasks consume the existing generic helper signature. The marker is consistently
  `data-rownav`; accessible-name prefixes are exactly `Open CAPA` and `Select programme`; selection uses
  boolean `aria-pressed`.
- **No placeholders:** Every code-producing step names exact files, interfaces, test bodies, implementation
  shape, expected RED cause, GREEN command, and commit.
