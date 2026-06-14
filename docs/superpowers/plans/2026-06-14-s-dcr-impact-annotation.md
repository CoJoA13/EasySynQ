# DCR impact-dimension annotation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a DCR assessor annotate the 7 impact dimensions inline in the drawer, batch-saving only the changed ones through the existing `PUT /dcrs/{id}/impact`.

**Architecture:** Front-end-only. Widen `api.send` to allow `PUT`; add a `useAnnotateImpact` mutation; split `DcrImpactTable` into its existing read-only render + a new `EditableImpactTable` (mounted only when editable, so the read-only path keeps its hook footprint); gate the editable mode in `DcrDrawer` on `impact rows exist && can("changeRequest.assess") && non-terminal state`.

**Tech Stack:** React + TS, Mantine v7, TanStack Query, MSW + Vitest + Testing Library + jest-axe. Spec: `docs/superpowers/specs/2026-06-14-s-dcr-impact-annotation-design.md`.

**Baseline:** 842 web tests green. Gate: `/check-web` only (`cd apps/web; npx vitest run <file>` per task; the full `--pool=forks --poolOptions.forks.singleFork=true` before the PR).

**Conventions (every test file):** `import { expect, it } from "vitest"`; fixtures `satisfies DcrImpact`/`DcrImpactList`; first content assertion uses `waitFor`/`findBy`; per-test MSW overrides via `server.use(...)`; `ApiError.message` = `problem.detail ?? title`.

---

### Task 1: `useAnnotateImpact` mutation + widen `api.send` to `PUT`

**Files:**
- Modify: `apps/web/src/lib/api.ts` (the two `send` method unions)
- Modify: `apps/web/src/features/dcr/mutations.ts` (add the hook)
- Test: `apps/web/src/features/dcr/mutations.test.tsx` (create if absent; else append)

- [ ] **Step 1: Write the failing test** — `apps/web/src/features/dcr/mutations.test.tsx`:

```tsx
import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import { waitFor } from "@testing-library/react";
import { renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { AuthContext } from "../../lib/auth";
import { TEST_AUTH } from "../../test/render";
import { server } from "../../test/msw/server";
import type { DcrImpactList } from "../../lib/types";
import { useAnnotateImpact } from "./mutations";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <MantineProvider>
      <QueryClientProvider client={client}>
        <AuthContext.Provider value={TEST_AUTH}>
          <MemoryRouter>{children}</MemoryRouter>
        </AuthContext.Provider>
      </QueryClientProvider>
    </MantineProvider>
  );
}

it("useAnnotateImpact PUTs the annotations to /dcrs/{id}/impact", async () => {
  let method = "";
  let body: unknown = null;
  const refreshed = {
    data: [
      { id: "i1", dimension: "affected_processes", auto_populated: null, requester_annotation: "Diego to re-validate", created_at: "2026-06-10T10:00:00+00:00", updated_at: "2026-06-11T10:00:00+00:00" },
    ],
  } satisfies DcrImpactList;
  server.use(
    http.put("/api/v1/dcrs/:id/impact", async ({ request }) => {
      method = request.method;
      body = await request.json();
      return HttpResponse.json(refreshed);
    }),
  );
  const { result } = renderHook(() => useAnnotateImpact("dcr1"), { wrapper });
  result.current.mutate({ affected_processes: "Diego to re-validate" });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(method).toBe("PUT");
  expect(body).toEqual({ annotations: { affected_processes: "Diego to re-validate" } });
});
```

- [ ] **Step 2: Run it — expect FAIL**

Run: `cd apps/web; npx vitest run src/features/dcr/mutations.test.tsx`
Expected: FAIL — `useAnnotateImpact` is not exported (and `tsc` would reject `"PUT"`).

- [ ] **Step 3: Widen `api.send`** — in `apps/web/src/lib/api.ts`, change BOTH method unions from `"POST" | "PATCH" | "DELETE"` to include `"PUT"`:

In `apiSend` (~line 78):
```ts
export const apiSend = <T>(
  method: "POST" | "PUT" | "PATCH" | "DELETE",
```
In `useApi().send` (~line 93):
```ts
      send: <T>(
        method: "POST" | "PUT" | "PATCH" | "DELETE",
```

- [ ] **Step 4: Add the hook** — append to `apps/web/src/features/dcr/mutations.ts`:

```ts
// Annotate impact dimensions (PUT /dcrs/{id}/impact, gate changeRequest.assess). Partial merge —
// send ONLY the changed dimensions. onSuccess (not onSettled): the server returns the refreshed
// rows and there's no FSM race to self-heal. Invalidate the impact rows + the detail (DCR_UPDATED).
export function useAnnotateImpact(id: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (annotations: Record<string, string>) =>
      api.send<DcrImpactList>("PUT", `/api/v1/dcrs/${id}/impact`, { annotations }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["dcr-impact", id] });
      void qc.invalidateQueries({ queryKey: ["dcr", id] });
    },
  });
}
```
Add `DcrImpactList` to the `lib/types` import at the top of `mutations.ts`.

- [ ] **Step 5: Run it — expect PASS**

Run: `cd apps/web; npx vitest run src/features/dcr/mutations.test.tsx` → PASS. Also `cd apps/web; npx tsc --noEmit` → 0 errors.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/lib/api.ts apps/web/src/features/dcr/mutations.ts apps/web/src/features/dcr/mutations.test.tsx
git commit -m "feat(dcr-impact): useAnnotateImpact mutation + widen api.send to PUT"
```

---

### Task 2: `DcrImpactTable` editable mode (`EditableImpactTable`)

**Files:**
- Modify: `apps/web/src/features/dcr/DcrImpactTable.tsx`
- Test: `apps/web/src/features/dcr/DcrImpactTable.test.tsx` (extend)

- [ ] **Step 1: Write the failing tests** — append to `apps/web/src/features/dcr/DcrImpactTable.test.tsx`. Add to the imports: `import { waitFor } from "@testing-library/react";`, `import userEvent from "@testing-library/user-event";`, `import { axe } from "jest-axe";`, `import { http, HttpResponse } from "msw";`, `import { server } from "../../test/msw/server";`, `import type { DcrImpactList } from "../../lib/types";`. Then:

```tsx
it("editable mode renders a textarea per dimension seeded from the annotation", () => {
  const { getByLabelText } = renderWithProviders(
    <DcrImpactTable impact={impact} editable dcrId="dcr1" />,
  );
  expect(getByLabelText("Annotation for affected_processes")).toHaveValue("Calibration");
  expect(getByLabelText("Annotation for training_awareness")).toHaveValue("");
});

it("Save is disabled until an annotation changes, then PUTs only the changed dimension", async () => {
  let putBody: unknown = null;
  const refreshed = { data: impact } satisfies DcrImpactList;
  server.use(
    http.put("/api/v1/dcrs/:id/impact", async ({ request }) => {
      putBody = await request.json();
      return HttpResponse.json(refreshed);
    }),
  );
  const user = userEvent.setup();
  const { getByRole, getByLabelText } = renderWithProviders(
    <DcrImpactTable impact={impact} editable dcrId="dcr1" />,
  );
  const save = getByRole("button", { name: "Save annotations" });
  expect(save).toBeDisabled();
  await user.type(getByLabelText("Annotation for training_awareness"), "Brief the line leads");
  expect(save).toBeEnabled();
  await user.click(save);
  await waitFor(() =>
    expect(putBody).toEqual({ annotations: { training_awareness: "Brief the line leads" } }),
  );
});

it("editable mode has no axe violations", async () => {
  const { container } = renderWithProviders(
    <DcrImpactTable impact={impact} editable dcrId="dcr1" />,
  );
  expect(await axe(container)).toHaveNoViolations();
});
```

(The existing read-only + empty-state tests must still pass unchanged.)

- [ ] **Step 2: Run — expect the new tests FAIL** (no `editable`/`dcrId` props yet; no textareas)

Run: `cd apps/web; npx vitest run src/features/dcr/DcrImpactTable.test.tsx`
Expected: the 3 new tests FAIL; the 2 existing tests PASS.

- [ ] **Step 3: Implement** — replace `apps/web/src/features/dcr/DcrImpactTable.tsx` with:

```tsx
import { Button, Stack, Table, Text, Textarea } from "@mantine/core";
import { useEffect, useMemo, useState } from "react";
import type { DcrImpact } from "../../lib/types";
import { useAnnotateImpact } from "./mutations";

function summarizeAuto(auto: Record<string, unknown> | null): string {
  if (!auto) return "—";
  if (auto.applicable === false) return "Not applicable";
  const processes = Array.isArray(auto.processes) ? auto.processes.length : null;
  if (processes !== null) return `Applicable · ${processes} process${processes === 1 ? "" : "es"}`;
  return "Applicable";
}

// Read-only: the auto-populated facts + the (frozen) requester annotation. Editing is EditableImpactTable.
export function DcrImpactTable({
  impact,
  editable = false,
  dcrId,
}: {
  impact: DcrImpact[];
  editable?: boolean;
  dcrId?: string;
}) {
  if (impact.length === 0) {
    return (
      <Text size="sm" c="dimmed">
        Not yet assessed.
      </Text>
    );
  }
  if (editable && dcrId) {
    return <EditableImpactTable impact={impact} dcrId={dcrId} />;
  }
  return (
    <Table>
      <Table.Thead>
        <Table.Tr>
          <Table.Th>Dimension</Table.Th>
          <Table.Th>System facts</Table.Th>
          <Table.Th>Annotation</Table.Th>
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {impact.map((i) => (
          <Table.Tr key={i.id}>
            <Table.Td>{i.dimension}</Table.Td>
            <Table.Td>{summarizeAuto(i.auto_populated)}</Table.Td>
            <Table.Td>{i.requester_annotation ?? "—"}</Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  );
}

// Inline-editable Annotation column + one batch Save (gate is the caller's — changeRequest.assess +
// rows-exist + non-terminal). Sends ONLY the changed dimensions (the backend partial merge). The
// draft re-seeds from the rows on every refetch, so a successful save resets it to the saved values.
function EditableImpactTable({ impact, dcrId }: { impact: DcrImpact[]; dcrId: string }) {
  const annotate = useAnnotateImpact(dcrId);
  const original = useMemo(
    () => Object.fromEntries(impact.map((i) => [i.dimension, i.requester_annotation ?? ""])),
    [impact],
  );
  const [draft, setDraft] = useState<Record<string, string>>(original);
  useEffect(() => setDraft(original), [original]);

  const changed = Object.fromEntries(
    Object.entries(draft).filter(([dim, v]) => v !== (original[dim] ?? "")),
  );
  const hasChanges = Object.keys(changed).length > 0;

  return (
    <Stack gap="sm">
      <Table>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Dimension</Table.Th>
            <Table.Th>System facts</Table.Th>
            <Table.Th>Annotation</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {impact.map((i) => (
            <Table.Tr key={i.id}>
              <Table.Td>{i.dimension}</Table.Td>
              <Table.Td>{summarizeAuto(i.auto_populated)}</Table.Td>
              <Table.Td>
                <Textarea
                  aria-label={`Annotation for ${i.dimension}`}
                  value={draft[i.dimension] ?? ""}
                  onChange={(e) =>
                    setDraft((d) => ({ ...d, [i.dimension]: e.currentTarget.value }))
                  }
                  autosize
                  minRows={1}
                />
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      {annotate.isError && (
        <Text size="sm" c="red">
          Couldn't save the annotations. Please try again.
        </Text>
      )}
      <Button
        w="fit-content"
        loading={annotate.isPending}
        disabled={!hasChanges}
        onClick={() => annotate.mutate(changed)}
      >
        Save annotations
      </Button>
    </Stack>
  );
}
```

- [ ] **Step 4: Run — expect PASS** (5 tests: 2 existing + 3 new)

Run: `cd apps/web; npx vitest run src/features/dcr/DcrImpactTable.test.tsx` → PASS. `cd apps/web; npx tsc --noEmit` → 0 errors.

> NOTE: if `getByLabelText("Annotation for …")` resolves more than one node (Mantine `Textarea` can label both the wrapper + the textarea), switch to `getByRole("textbox", { name: "Annotation for affected_processes" })`. Try `getByLabelText` first — it's the lighter query.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/dcr/DcrImpactTable.tsx apps/web/src/features/dcr/DcrImpactTable.test.tsx
git commit -m "feat(dcr-impact): inline-editable annotation column + batch Save (changed dims only)"
```

---

### Task 3: gate the editable mode in `DcrDrawer`

**Files:**
- Modify: `apps/web/src/features/dcr/DcrDrawer.tsx`
- Test: `apps/web/src/features/dcr/DcrDrawer.test.tsx` (extend)

- [ ] **Step 1: Write the failing tests** — append to `apps/web/src/features/dcr/DcrDrawer.test.tsx`. Reuse its existing imports (`http`, `HttpResponse`, `server`, `renderWithProviders`, `DcrDrawer`, `type DcrDetail`); add `import { screen } from "@testing-library/react";` ONLY if the file doesn't already capture the render return as `screen` (this file uses `const screen = renderWithProviders(...)` — keep that idiom). Add helpers + tests:

```tsx
// ---- impact-annotation editable gating ----
const ANNO_ID = "dcr00077-0077-0077-0077-000000000077";
function annoDcr(state: string): DcrDetail {
  return {
    id: ANNO_ID,
    identifier: "DCR-2026-0077",
    target_document_id: "11111111-1111-1111-1111-111111111111",
    change_type: "REVISE",
    change_significance: "MAJOR",
    reason_class: "audit_finding",
    reason_text: "Revision.",
    source_link_type: null,
    source_link_id: null,
    proposed_effective_from: null,
    resulting_version_id: null,
    state,
    decision: null,
    created_by: "bbbb1111-1111-1111-1111-111111111111",
    created_at: "2026-05-01T09:00:00+00:00",
    stage_events: [],
    capabilities: { assess: true, route: false, implement: false, close: false },
  } as DcrDetail;
}
const annoImpact = {
  data: [
    { id: "ai1", dimension: "affected_processes", auto_populated: { applicable: true, processes: ["p1"] }, requester_annotation: "x", created_at: "2026-06-10T10:00:00+00:00", updated_at: null },
  ],
};
function grantAssess() {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: [{ key: "changeRequest.assess", effect: "ALLOW", source: "SYSTEM" }],
      }),
    ),
  );
}
function serveAnno(state: string) {
  server.use(
    http.get("/api/v1/dcrs/:id", () => HttpResponse.json(annoDcr(state))),
    http.get("/api/v1/dcrs/:id/impact", () => HttpResponse.json(annoImpact)),
  );
}

it("shows the editable annotation column for an Assessed DCR with changeRequest.assess", async () => {
  serveAnno("Assessed");
  grantAssess();
  const screen = renderWithProviders(<DcrDrawer dcrId={ANNO_ID} onClose={() => {}} />);
  expect(await screen.findByLabelText("Annotation for affected_processes")).toBeInTheDocument();
});

it("keeps the annotation column read-only without changeRequest.assess", async () => {
  serveAnno("Assessed"); // default /me/permissions = empty grant set
  const screen = renderWithProviders(<DcrDrawer dcrId={ANNO_ID} onClose={() => {}} />);
  await screen.findByText("DCR-2026-0077");
  expect(screen.queryByLabelText("Annotation for affected_processes")).not.toBeInTheDocument();
});

it("keeps the annotation column read-only in a terminal state even with the permission", async () => {
  serveAnno("Closed");
  grantAssess();
  const screen = renderWithProviders(<DcrDrawer dcrId={ANNO_ID} onClose={() => {}} />);
  await screen.findByText("DCR-2026-0077");
  expect(screen.queryByLabelText("Annotation for affected_processes")).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run — expect the editable test FAIL** (the drawer doesn't pass `editable` yet)

Run: `cd apps/web; npx vitest run src/features/dcr/DcrDrawer.test.tsx`
Expected: the "shows the editable…" test FAILS (no textarea); the two read-only tests pass (no textarea is the current behavior).

- [ ] **Step 3: Implement** — in `apps/web/src/features/dcr/DcrDrawer.tsx`:

Add the import:
```tsx
import { usePermissions } from "../../app/shell/usePermissions";
```
Inside the component, after the existing hooks (e.g. after `const { data: impact } = useDcrImpact(dcrId);`):
```tsx
  const { can } = usePermissions();
```
Replace the impact section's render so the table is gated. Find:
```tsx
            <DcrImpactTable impact={impact ?? []} />
```
and replace with:
```tsx
            <DcrImpactTable
              impact={impact ?? []}
              editable={
                !!dcr &&
                (impact?.length ?? 0) > 0 &&
                can("changeRequest.assess") &&
                !["Closed", "Cancelled", "Rejected"].includes(dcr.state)
              }
              dcrId={dcr?.id}
            />
```
(`dcr` is the `useDcr` data already in scope in the rendered branch; if the `DcrImpactTable` render site is inside the `dcr && !isError` body, `dcr` is non-null there — use `dcr.state`/`dcr.id` directly and drop the `!!dcr`/`dcr?.` guards to match the surrounding code.)

- [ ] **Step 4: Run — expect PASS**

Run: `cd apps/web; npx vitest run src/features/dcr/DcrDrawer.test.tsx` → all PASS. `cd apps/web; npx tsc --noEmit` → 0 errors.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/dcr/DcrDrawer.tsx apps/web/src/features/dcr/DcrDrawer.test.tsx
git commit -m "feat(dcr-impact): gate the editable annotation column (assess + rows + non-terminal)"
```

---

### Task 4: full gate

**Files:** none (verification)

- [ ] **Step 1: Run the full web gate**

Run: `cd apps/web; npx eslint . ; npx tsc --noEmit ; npx vitest run --pool=forks --poolOptions.forks.singleFork=true`
Expected: eslint clean, tsc 0 errors, vitest all green (842 + the new tests, ~+8 → ~850).

- [ ] **Step 2: (if anything fails) fix + re-run.** Likely culprits: the Mantine `Textarea` label query (see the Task 2 NOTE); `noUncheckedIndexedAccess` on `draft[dim]` (the code uses `?? ""` guards). No commit needed if Step 1 is clean.

---

## Self-Review

**Spec coverage:** F1 editable window → Task 3 gate (`assess + rows + non-terminal`). F2 inline cells + batch Save → Task 2. `api.send` PUT widen + `useAnnotateImpact` → Task 1. Partial-merge (changed only) → Task 2's `changed` + the asserting test. Re-seed on refetch → Task 2's `useEffect(() => setDraft(original), [original])`. Read-only path unchanged → Task 2 keeps the original render in the non-editable branch. ✓

**Placeholder scan:** none — full code in every step; the one query-ambiguity (Mantine label) has an explicit fallback NOTE. ✓

**Type consistency:** `useAnnotateImpact(id)` takes `Record<string,string>` and is consumed identically in Task 2 (`annotate.mutate(changed)`); `DcrImpactTable` props `{impact, editable?, dcrId?}` match Task 3's usage; `DcrImpactList` used in Tasks 1 + 2 fixtures. ✓

## Out of scope (named, not faked)
CREATE deep-link/implement (residual C); annotating before assess (no rows); editing `auto_populated` (system-derived).
