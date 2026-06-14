# S-dcr-ui-2a — DCR Intake & Early-State Writes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the read-only DCR cockpit its create + early-edit affordances — Raise (3 seams: standalone `/dcrs`, CAPA-drawer, MR-output) + edit-while-Open + cancel — so a DCR can be raised from every built backend seam and edited/cancelled early in its lifecycle.

**Architecture:** Front-end-only (apps/web). Every endpoint already exists and is single-gated on a `changeRequest.*` key that `usePermissions().can()` answers correctly — no migration/key/endpoint/contract. New components live under `features/dcr/`; three existing surfaces (`DcrsRegisterPage`, `DcrDrawer`, `CapaDrawer`, `ReviewOutputsSection`) gain a gated button. Mutations are **never optimistic**; invalidators mirror the read keys (`["dcrs"]`, `["dcr",id]`, `["dcr-impact",id]`). Edit/Cancel invalidate `onSettled` so the drawer self-heals past a racing 409.

**Tech Stack:** React 18 + TypeScript (strict) + Mantine v7 + TanStack Query v5 + react-router-dom. Tests: vitest + @testing-library/react + MSW + jest-axe. Spec: `docs/superpowers/specs/2026-06-13-s-dcr-ui-2a-dcr-intake-writes-design.md`.

**Gate:** `/check-web` only (eslint + strict `tsc --noEmit` + build + the full vitest suite). No backend gates.

---

## File Structure

**New files (`apps/web/src/features/dcr/`):**
- `mutations.ts` — 5 write hooks (`useRaiseDcr`, `usePatchDcr`, `useCancelDcr`, `useRaiseDcrFromCapa`, `useRaiseDcrFromMrOutput`) + the `useDcrInvalidator` helper + `SpawnDcrVars`.
- `DcrRaiseFields.tsx` — shared controlled form core (change_type SegmentedControl, conditional target picker, significance, reason_text, optional date) + `DcrFieldsValue`/`EMPTY_DCR_FIELDS`/`isDcrFieldsValid`/`proposedEffectiveIso`.
- `RaiseDcrModal.tsx` — standalone create (`DcrRaiseFields` + a `reason_class` Select).
- `SpawnDcrModal.tsx` — parameterized spawn for CAPA + MR-output (deep-link-on-success).
- `EditDcrModal.tsx` — PATCH while Open.
- `CancelDcrModal.tsx` — cancel with optional comment.
- `DcrAdvancePanel.tsx` — Edit + Cancel affordances, gated per key + state (ui-2b extends this).
- `*.test.tsx` colocated per component.

**Modified files:**
- `apps/web/src/lib/types.ts` — add `DcrCreateBody`, `DcrPatchBody`, `DcrCancelBody`, `DcrSpawnBody`.
- `apps/web/src/features/dcr/DcrsRegisterPage.tsx` — header "Raise DCR" button + modal mount.
- `apps/web/src/features/dcr/DcrDrawer.tsx` — render `<DcrAdvancePanel>`.
- `apps/web/src/features/capa/CapaDrawer.tsx` — "Raise change request" button + `SpawnDcrModal`.
- `apps/web/src/features/management-review/ReviewOutputsSection.tsx` — "Raise DCR" on ACTION rows + `SpawnDcrModal`.
- `apps/web/src/test/msw/handlers.ts` — 5 write handlers + a `dcrCreatedFixture`.

---

## Conventions all tasks follow

- **Every test file** starts with `import { expect, it } from "vitest";` (the jest-dom × tsc trap — the bare global `expect` is jest-typed; only `tsc` catches it, not a per-file vitest run).
- **Render** via `renderWithProviders(ui, { route? })` from `../../test/render` (wraps MantineProvider + QueryClient[retry:false] + AuthContext + MemoryRouter; `TEST_AUTH.user.profile.sub === "bbbb1111-1111-1111-1111-111111111111"`).
- **Grant a permission in a test** by overriding `/me/permissions` (default fixture grants NONE):
  ```tsx
  function grant(...keys: string[]) {
    server.use(
      http.get("/api/v1/me/permissions", () =>
        HttpResponse.json({
          scope: { level: "SYSTEM", selector: null },
          permissions: keys.map((key) => ({ key, effect: "ALLOW" })),
        }),
      ),
    );
  }
  ```
- **Assert navigation** with a `LocationProbe`:
  ```tsx
  function LocationProbe() {
    const loc = useLocation();
    return <div data-testid="loc">{loc.pathname + loc.search}</div>;
  }
  ```
- **Mantine v7 Select trap:** an input + a listbox share the `aria-label`/label → `getByLabelText` throws; use `getAllByLabelText(name)[0]`.
- Run the **full** `/check-web` before the PR (Task 10) — the per-file vitest run is blind to the jest-dom×tsc trap and `noUncheckedIndexedAccess`.

---

### Task 1: Foundation — request body types, mutations, MSW write handlers

**Files:**
- Modify: `apps/web/src/lib/types.ts` (append the four body interfaces)
- Create: `apps/web/src/features/dcr/mutations.ts`
- Modify: `apps/web/src/test/msw/handlers.ts:1338-1344` (add a fixture + 5 write handlers; add `Dcr` to the DCR type import)

> This is infrastructure. Per the house pattern (`features/capa/mutations.ts` has no standalone test), the mutations are verified by the component tests in Tasks 3/5/7/8/9. No standalone test here.

- [ ] **Step 1: Confirm `ChangeSignificance` is exported from `lib/types.ts`**

Run: `grep -n "export type ChangeSignificance\|export interface ChangeSignificance" apps/web/src/lib/types.ts`
Expected: one match (it's reused by the `Dcr` interface). If it is NOT exported, add `export type ChangeSignificance = "MAJOR" | "MINOR";` near the other DCR enums before proceeding.

- [ ] **Step 2: Add the write-body types to `lib/types.ts`**

Append after the existing DCR types block (after `DcrImpactList`, ~line 1353):

```ts
// ---- S-dcr-ui-2a write bodies (pinned to api/dcr.py DcrCreate/DcrPatch/DcrCancel + the two spawn bodies) ----
export interface DcrCreateBody {
  change_type: DcrChangeType;
  change_significance: ChangeSignificance;
  reason_class: DcrReasonClass;
  reason_text: string;
  target_document_id?: string | null;
  source_link_type?: DcrSourceLinkType | null;
  source_link_id?: string | null;
  proposed_effective_from?: string | null;
}
// PATCH while Open — every field optional; null/absent = unchanged (cannot clear a field, mirrors the backend).
export interface DcrPatchBody {
  reason_text?: string;
  reason_class?: DcrReasonClass;
  change_significance?: ChangeSignificance;
  proposed_effective_from?: string | null;
}
export interface DcrCancelBody {
  comment?: string;
}
// Shared by both spawn endpoints (CAPA defaults reason_class=capa, MR forces mgmt_review — neither carries it).
export interface DcrSpawnBody {
  change_type: DcrChangeType;
  change_significance: ChangeSignificance;
  reason_text: string;
  target_document_id?: string | null;
  proposed_effective_from?: string | null;
}
```

- [ ] **Step 3: Create `features/dcr/mutations.ts`**

```ts
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type { Dcr, DcrCancelBody, DcrCreateBody, DcrPatchBody, DcrSpawnBody } from "../../lib/types";

export interface SpawnDcrVars {
  body: DcrSpawnBody;
  idempotencyKey: string;
}

// After any DCR write, re-read the server (NEVER optimistic — FSM/SoD/effectivity truth is server-only).
function useDcrInvalidator(id?: string) {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: ["dcrs"] });
    if (id) {
      void qc.invalidateQueries({ queryKey: ["dcr", id] });
      void qc.invalidateQueries({ queryKey: ["dcr-impact", id] });
    }
  };
}

// Standalone create (POST /dcrs) — 201, no Idempotency-Key. Returns the new Dcr (caller opens its drawer).
export function useRaiseDcr() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: DcrCreateBody) => api.send<Dcr>("POST", "/api/v1/dcrs", body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["dcrs"] }),
  });
}

// Edit while Open (PATCH). Invalidate on SETTLE: a 409 dcr_not_editable means a concurrent advance, so the
// drawer must refetch to show the real state behind the calm error (the useNcrDisposition precedent).
export function usePatchDcr(id: string) {
  const api = useApi();
  const invalidate = useDcrInvalidator(id);
  return useMutation({
    mutationFn: (body: DcrPatchBody) => api.send<Dcr>("PATCH", `/api/v1/dcrs/${id}`, body),
    onSettled: invalidate,
  });
}

// Cancel (POST /cancel). Same onSettled rationale (409 dcr_not_cancellable = concurrent advance).
export function useCancelDcr(id: string) {
  const api = useApi();
  const invalidate = useDcrInvalidator(id);
  return useMutation({
    mutationFn: (body: DcrCancelBody) => api.send<Dcr>("POST", `/api/v1/dcrs/${id}/cancel`, body),
    onSettled: invalidate,
  });
}

// CAPA → DCR spawn (1:N idempotent — 201 new / 200 replay both resolve to a Dcr here, NO status branching).
// The modal generates a per-mount Idempotency-Key. reason_class defaults to "capa" server-side.
export function useRaiseDcrFromCapa(capaId: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ body, idempotencyKey }: SpawnDcrVars) =>
      api.send<Dcr>("POST", `/api/v1/capas/${capaId}/raise-dcr`, body, {
        "Idempotency-Key": idempotencyKey,
      }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["dcrs"] }),
  });
}

// MR ACTION-output → DCR spawn (1:N idempotent). reason_class is FORCED to mgmt_review server-side.
export function useRaiseDcrFromMrOutput(reviewId: string, outputId: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ body, idempotencyKey }: SpawnDcrVars) =>
      api.send<Dcr>(
        "POST",
        `/api/v1/management-reviews/${reviewId}/outputs/${outputId}/raise-dcr`,
        body,
        { "Idempotency-Key": idempotencyKey },
      ),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["dcrs"] }),
  });
}
```

- [ ] **Step 4: Add the MSW write handlers + fixture**

In `apps/web/src/test/msw/handlers.ts`: (a) add `Dcr` to the existing DCR type import (the file imports `DcrList`, `DcrDetail`, `DcrImpactList` — add `Dcr`); (b) add `dcrCreatedFixture` after `dcrImpactFixture` (~line 1337):

```ts
const dcrCreatedFixture = {
  ...dcrListFixture.data[0]!,
  id: "dcrNEW01-0001-0001-0001-000000000099",
  identifier: "DCR-2026-0099",
  state: "Open",
  decision: null,
  resulting_version_id: null,
} satisfies Dcr;
```

(c) add the 5 write handlers inside the `handlers` array, right after the three DCR GET handlers (after line 1344):

```ts
  // ---- S-dcr-ui-2a DCR write handlers (defaults; per-test overrides for 409/422) ----
  http.post("/api/v1/dcrs", () => HttpResponse.json(dcrCreatedFixture, { status: 201 })),
  http.patch("/api/v1/dcrs/:id", async ({ request }) => {
    const body = (await request.json()) as Partial<Dcr>;
    return HttpResponse.json({ ...dcrDetailFixture, ...body });
  }),
  http.post("/api/v1/dcrs/:id/cancel", () =>
    HttpResponse.json({ ...dcrDetailFixture, state: "Cancelled" }),
  ),
  http.post("/api/v1/capas/:id/raise-dcr", () =>
    HttpResponse.json({ ...dcrCreatedFixture, source_link_type: "capa" }, { status: 201 }),
  ),
  http.post("/api/v1/management-reviews/:rid/outputs/:oid/raise-dcr", () =>
    HttpResponse.json(
      { ...dcrCreatedFixture, source_link_type: "mgmt_review", reason_class: "mgmt_review" },
      { status: 201 },
    ),
  ),
```

- [ ] **Step 5: Typecheck**

Run: `cd apps/web && npx tsc --noEmit`
Expected: PASS (no errors). If `ChangeSignificance` or `Dcr` import errors appear, fix the imports per Steps 1/4.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/lib/types.ts apps/web/src/features/dcr/mutations.ts apps/web/src/test/msw/handlers.ts
git commit -m "feat(s-dcr-ui-2a): DCR write mutations + body types + MSW write handlers"
```

---

### Task 2: `DcrRaiseFields` — the shared form core

**Files:**
- Create: `apps/web/src/features/dcr/DcrRaiseFields.tsx`
- Test: `apps/web/src/features/dcr/DcrRaiseFields.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import { useState } from "react";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { DcrRaiseFields, EMPTY_DCR_FIELDS, type DcrFieldsValue } from "./DcrRaiseFields";

const DOC = {
  id: "doc00001-0001-0001-0001-000000000001",
  identifier: "SOP-PUR-014",
  kind: "DOCUMENT",
  title: "Purchasing procedure",
  document_type_id: null,
  area_code: null,
  folder_path: null,
  current_state: "Effective",
  classification: "Internal",
  is_singleton: false,
  owner_user_id: "bbbb1111-1111-1111-1111-111111111111",
  framework_id: "f1",
  current_effective_version_id: null,
  effective_from: null,
};

function Harness() {
  const [v, setV] = useState<DcrFieldsValue>(EMPTY_DCR_FIELDS);
  return (
    <>
      <DcrRaiseFields value={v} onChange={setV} />
      <div data-testid="target">{v.target_document_id ?? "none"}</div>
      <div data-testid="ct">{v.change_type}</div>
    </>
  );
}

it("shows the target picker for REVISE and hides it for CREATE, clearing the target on switch", async () => {
  server.use(http.get("/api/v1/documents", () => HttpResponse.json({ data: [DOC], page: { limit: 200, offset: 0, returned: 1, total: 1 } })));
  renderWithProviders(<Harness />);
  // REVISE (default) shows the target picker
  expect(screen.getByLabelText("Target document")).toBeInTheDocument();
  // pick a target
  await userEvent.click(screen.getByLabelText("Target document"));
  await userEvent.click(await screen.findByRole("option", { name: /SOP-PUR-014/ }));
  expect(screen.getByTestId("target")).toHaveTextContent("doc00001-0001-0001-0001-000000000001");
  // switch to CREATE → picker hidden AND target cleared
  await userEvent.click(screen.getByRole("radio", { name: "Create" }));
  await waitFor(() => expect(screen.queryByLabelText("Target document")).toBeNull());
  expect(screen.getByTestId("target")).toHaveTextContent("none");
});
```

- [ ] **Step 2: Run it — verify it fails**

Run: `cd apps/web && npx vitest run src/features/dcr/DcrRaiseFields.test.tsx`
Expected: FAIL — `DcrRaiseFields` does not exist.

- [ ] **Step 3: Implement `DcrRaiseFields.tsx`**

```tsx
import { SegmentedControl, Select, Stack, Text, Textarea, TextInput } from "@mantine/core";
import { useDocuments } from "../library/useDocuments";
import type { ChangeSignificance, DcrChangeType } from "../../lib/types";
import { CHANGE_TYPE_LABEL } from "./labels";

export interface DcrFieldsValue {
  change_type: DcrChangeType;
  change_significance: ChangeSignificance;
  reason_text: string;
  target_document_id: string | null;
  proposed_effective_from: string | null; // YYYY-MM-DD (native date input) | null
}

export const EMPTY_DCR_FIELDS: DcrFieldsValue = {
  change_type: "REVISE",
  change_significance: "MINOR",
  reason_text: "",
  target_document_id: null,
  proposed_effective_from: null,
};

// reason non-empty AND (CREATE has no target | REVISE/RETIRE has its target) — mirrors the backend
// CREATE⟺no-target biconditional so create_has_target/target_required are unreachable from the UI.
export function isDcrFieldsValid(v: DcrFieldsValue): boolean {
  return v.reason_text.trim().length > 0 && (v.change_type === "CREATE" || v.target_document_id !== null);
}

// A native date (YYYY-MM-DD) → a local-midnight ISO timestamp (R8); null when unset.
export function proposedEffectiveIso(date: string | null): string | null {
  return date ? `${date}T00:00:00+00:00` : null;
}

export function DcrRaiseFields({
  value,
  onChange,
}: {
  value: DcrFieldsValue;
  onChange: (v: DcrFieldsValue) => void;
}) {
  // The target lists Effective controlled Documents (the revise/retire target). useDocuments has no
  // free-text filter, so the Select is `searchable` (client-side label filter) over a generous page.
  const { data: docsPage } = useDocuments({ current_state: "Effective" }, { limit: 200, offset: 0 });
  const targetOptions = (docsPage?.data ?? [])
    .filter((d) => d.kind === "DOCUMENT")
    .map((d) => ({ value: d.id, label: `${d.identifier} — ${d.title}` }));
  const showTarget = value.change_type !== "CREATE";

  return (
    <Stack gap="sm">
      <div>
        <Text size="sm" fw={500} mb={4}>
          Change type
        </Text>
        <SegmentedControl
          fullWidth
          value={value.change_type}
          onChange={(v) =>
            onChange({
              ...value,
              change_type: v as DcrChangeType,
              // switching to CREATE clears the target so the body never carries a CREATE-with-target
              target_document_id: v === "CREATE" ? null : value.target_document_id,
            })
          }
          data={(Object.entries(CHANGE_TYPE_LABEL) as [DcrChangeType, string][]).map(([val, label]) => ({
            value: val,
            label,
          }))}
        />
      </div>

      {showTarget && (
        <Select
          label="Target document"
          required
          searchable
          placeholder="Pick the document to revise or retire"
          value={value.target_document_id}
          onChange={(v) => onChange({ ...value, target_document_id: v })}
          data={targetOptions}
          nothingFoundMessage="No matching documents"
          comboboxProps={{ keepMounted: false }}
        />
      )}

      <div>
        <Text size="sm" fw={500} mb={4}>
          Significance
        </Text>
        <SegmentedControl
          value={value.change_significance}
          onChange={(v) => onChange({ ...value, change_significance: v as ChangeSignificance })}
          data={[
            { value: "MINOR", label: "Minor" },
            { value: "MAJOR", label: "Major" },
          ]}
        />
      </div>

      <Textarea
        label="Reason for change"
        required
        autosize
        minRows={2}
        value={value.reason_text}
        onChange={(e) => onChange({ ...value, reason_text: e.currentTarget.value })}
      />

      <TextInput
        type="date"
        label="Proposed effective from (optional)"
        value={value.proposed_effective_from ?? ""}
        onChange={(e) => onChange({ ...value, proposed_effective_from: e.currentTarget.value || null })}
      />
    </Stack>
  );
}
```

- [ ] **Step 4: Run the test — verify it passes**

Run: `cd apps/web && npx vitest run src/features/dcr/DcrRaiseFields.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/dcr/DcrRaiseFields.tsx apps/web/src/features/dcr/DcrRaiseFields.test.tsx
git commit -m "feat(s-dcr-ui-2a): DcrRaiseFields — shared raise form core with CREATE<->target toggle"
```

---

### Task 3: `RaiseDcrModal` — the standalone create modal

**Files:**
- Create: `apps/web/src/features/dcr/RaiseDcrModal.tsx`
- Test: `apps/web/src/features/dcr/RaiseDcrModal.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { http, HttpResponse } from "msw";
import { expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { RaiseDcrModal } from "./RaiseDcrModal";

it("raises a CREATE DCR and calls onCreated with the new id", async () => {
  const onCreated = vi.fn();
  const onClose = vi.fn();
  renderWithProviders(<RaiseDcrModal onClose={onClose} onCreated={onCreated} />);
  // CREATE needs no target
  await userEvent.click(screen.getByRole("radio", { name: "Create" }));
  await userEvent.type(screen.getByLabelText("Reason for change"), "A new work instruction is needed.");
  await userEvent.click(screen.getByLabelText("Reason class"));
  await userEvent.click(await screen.findByRole("option", { name: "Process improvement" }));
  await userEvent.click(screen.getByRole("button", { name: "Raise" }));
  await vi.waitFor(() => expect(onCreated).toHaveBeenCalledWith("dcrNEW01-0001-0001-0001-000000000099"));
  expect(onClose).toHaveBeenCalled();
});

it("surfaces a 422 from the server calmly", async () => {
  server.use(
    http.post("/api/v1/dcrs", () =>
      HttpResponse.json(
        { code: "validation_error", title: "Invalid", detail: "A CREATE DCR must not target a document" },
        { status: 422 },
      ),
    ),
  );
  renderWithProviders(<RaiseDcrModal onClose={vi.fn()} onCreated={vi.fn()} />);
  await userEvent.click(screen.getByRole("radio", { name: "Create" }));
  await userEvent.type(screen.getByLabelText("Reason for change"), "x");
  await userEvent.click(screen.getByLabelText("Reason class"));
  await userEvent.click(await screen.findByRole("option", { name: "Other" }));
  await userEvent.click(screen.getByRole("button", { name: "Raise" }));
  expect(await screen.findByText("A CREATE DCR must not target a document")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run it — verify it fails**

Run: `cd apps/web && npx vitest run src/features/dcr/RaiseDcrModal.test.tsx`
Expected: FAIL — `RaiseDcrModal` does not exist.

- [ ] **Step 3: Implement `RaiseDcrModal.tsx`**

```tsx
import { Alert, Button, Group, Modal, Select, Stack } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import type { DcrCreateBody, DcrReasonClass } from "../../lib/types";
import {
  DcrRaiseFields,
  EMPTY_DCR_FIELDS,
  isDcrFieldsValid,
  proposedEffectiveIso,
  type DcrFieldsValue,
} from "./DcrRaiseFields";
import { REASON_LABEL } from "./labels";
import { useRaiseDcr } from "./mutations";

// Conditionally mounted by the parent ({raising && <RaiseDcrModal/>}) so close unmounts + resets state.
export function RaiseDcrModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const m = useRaiseDcr();
  const [fields, setFields] = useState<DcrFieldsValue>(EMPTY_DCR_FIELDS);
  const [reasonClass, setReasonClass] = useState<DcrReasonClass | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setError(null);
    if (!reasonClass || !isDcrFieldsValid(fields)) return;
    const body: DcrCreateBody = {
      change_type: fields.change_type,
      change_significance: fields.change_significance,
      reason_class: reasonClass,
      reason_text: fields.reason_text.trim(),
      target_document_id: fields.change_type === "CREATE" ? null : fields.target_document_id,
      proposed_effective_from: proposedEffectiveIso(fields.proposed_effective_from),
    };
    try {
      const dcr = await m.mutateAsync(body);
      onCreated(dcr.id);
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not raise the change request.");
    }
  }

  return (
    <Modal opened onClose={onClose} title="Raise change request" size="lg">
      <Stack gap="sm">
        {error && <Alert color="red">{error}</Alert>}
        <DcrRaiseFields value={fields} onChange={setFields} />
        <Select
          label="Reason class"
          required
          placeholder="Pick a reason"
          value={reasonClass}
          onChange={(v) => setReasonClass(v as DcrReasonClass)}
          data={(Object.entries(REASON_LABEL) as [DcrReasonClass, string][]).map(([value, label]) => ({
            value,
            label,
          }))}
          comboboxProps={{ keepMounted: false }}
        />
        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={() => void submit()}
            loading={m.isPending}
            disabled={!reasonClass || !isDcrFieldsValid(fields)}
          >
            Raise
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
```

- [ ] **Step 4: Run the test — verify it passes**

Run: `cd apps/web && npx vitest run src/features/dcr/RaiseDcrModal.test.tsx`
Expected: PASS (both cases).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/dcr/RaiseDcrModal.tsx apps/web/src/features/dcr/RaiseDcrModal.test.tsx
git commit -m "feat(s-dcr-ui-2a): RaiseDcrModal — standalone create with calm 422"
```

---

### Task 4: Wire `RaiseDcrModal` into `DcrsRegisterPage`

**Files:**
- Modify: `apps/web/src/features/dcr/DcrsRegisterPage.tsx`
- Modify (append tests): `apps/web/src/features/dcr/DcrsRegisterPage.test.tsx`

- [ ] **Step 1: Write the failing tests (append to the existing file)**

```tsx
import { RaiseDcrModal } from "./RaiseDcrModal"; // ensure no unused-import lint; remove if not referenced

function grant(...keys: string[]) {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: keys.map((key) => ({ key, effect: "ALLOW" })),
      }),
    ),
  );
}

it("hides the Raise DCR button without changeRequest.create", async () => {
  renderWithProviders(<DcrsRegisterPage />);
  await screen.findByText("DCR-2026-0001");
  expect(screen.queryByRole("button", { name: "Raise DCR" })).toBeNull();
});

it("raises a DCR and opens the new request's drawer", async () => {
  grant("changeRequest.create");
  renderWithProviders(<DcrsRegisterPage />);
  await screen.findByText("DCR-2026-0001");
  await userEvent.click(screen.getByRole("button", { name: "Raise DCR" }));
  await userEvent.click(await screen.findByRole("radio", { name: "Create" }));
  await userEvent.type(screen.getByLabelText("Reason for change"), "New WI.");
  await userEvent.click(screen.getByLabelText("Reason class"));
  await userEvent.click(await screen.findByRole("option", { name: "Other" }));
  await userEvent.click(screen.getByRole("button", { name: "Raise" }));
  // the new DCR's drawer opens (the default detail handler resolves dcrDetailFixture)
  expect(await screen.findByText(/Corrective action requires/)).toBeInTheDocument();
});
```

> Remove the `RaiseDcrModal` import line above if eslint flags it as unused — the page module imports it, the test does not need it directly.

- [ ] **Step 2: Run — verify the "Raise DCR" tests fail**

Run: `cd apps/web && npx vitest run src/features/dcr/DcrsRegisterPage.test.tsx`
Expected: the two new tests FAIL (no "Raise DCR" button yet); the existing tests still pass.

- [ ] **Step 3: Modify `DcrsRegisterPage.tsx`**

(a) Add to the `@mantine/core` import: `Button`. Add new imports:

```tsx
import { usePermissions } from "../../app/shell/usePermissions";
import { RaiseDcrModal } from "./RaiseDcrModal";
```

(b) Add hooks at the top of the component (with the others, BEFORE the early returns so hook order is stable):

```tsx
  const { can } = usePermissions();
  const [raising, setRaising] = useState(false);
```

(c) Replace the main-return title (`<Title order={2} mb="md">Change requests</Title>` at ~line 103) with a header row carrying the gated button:

```tsx
      <Group justify="space-between" mb="md">
        <Title order={2}>Change requests</Title>
        {can("changeRequest.create") && (
          <Button onClick={() => setRaising(true)}>Raise DCR</Button>
        )}
      </Group>
```

(d) Mount the modal just before the closing `</Container>` (after `<DcrDrawer ... />`):

```tsx
      {raising && (
        <RaiseDcrModal
          onClose={() => setRaising(false)}
          onCreated={(id) => setSelected(id)}
        />
      )}
```

- [ ] **Step 4: Run the page tests — verify all pass**

Run: `cd apps/web && npx vitest run src/features/dcr/DcrsRegisterPage.test.tsx`
Expected: PASS (existing + the two new tests). The existing jest-axe smoke still passes (the gated button is hidden by default, so the axe tree is unchanged unless a test grants the key).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/dcr/DcrsRegisterPage.tsx apps/web/src/features/dcr/DcrsRegisterPage.test.tsx
git commit -m "feat(s-dcr-ui-2a): wire the standalone Raise DCR into the register"
```

---

### Task 5: `EditDcrModal` + `CancelDcrModal`

**Files:**
- Create: `apps/web/src/features/dcr/EditDcrModal.tsx`
- Create: `apps/web/src/features/dcr/CancelDcrModal.tsx`
- Test: `apps/web/src/features/dcr/EditCancelDcr.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { http, HttpResponse } from "msw";
import { expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import type { DcrDetail } from "../../lib/types";
import { EditDcrModal } from "./EditDcrModal";
import { CancelDcrModal } from "./CancelDcrModal";

const DCR: DcrDetail = {
  id: "dcr00001-0001-0001-0001-000000000001",
  identifier: "DCR-2026-0001",
  target_document_id: "doc00001-0001-0001-0001-000000000001",
  change_type: "REVISE",
  change_significance: "MAJOR",
  reason_class: "capa",
  reason_text: "Original reason.",
  source_link_type: "capa",
  source_link_id: "capa0001-0001-0001-0001-000000000001",
  proposed_effective_from: null,
  resulting_version_id: null,
  state: "Open",
  decision: null,
  created_by: "bbbb1111-1111-1111-1111-111111111111",
  created_at: "2026-06-10T09:00:00+00:00",
  stage_events: [],
};

it("edits a DCR's reason and closes on success", async () => {
  const onClose = vi.fn();
  renderWithProviders(<EditDcrModal dcr={DCR} onClose={onClose} />);
  const reason = screen.getByLabelText("Reason for change");
  await userEvent.clear(reason);
  await userEvent.type(reason, "Updated reason.");
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  await vi.waitFor(() => expect(onClose).toHaveBeenCalled());
});

it("surfaces a 409 dcr_not_editable calmly", async () => {
  server.use(
    http.patch("/api/v1/dcrs/:id", () =>
      HttpResponse.json(
        { code: "dcr_not_editable", title: "Conflict", detail: "A DCR can only be edited while Open" },
        { status: 409 },
      ),
    ),
  );
  renderWithProviders(<EditDcrModal dcr={DCR} onClose={vi.fn()} />);
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText("A DCR can only be edited while Open")).toBeInTheDocument();
});

it("cancels a DCR with an optional comment", async () => {
  const onClose = vi.fn();
  renderWithProviders(<CancelDcrModal dcr={DCR} onClose={onClose} />);
  await userEvent.type(screen.getByLabelText("Comment (optional)"), "Withdrawn.");
  await userEvent.click(screen.getByRole("button", { name: "Cancel change request" }));
  await vi.waitFor(() => expect(onClose).toHaveBeenCalled());
});
```

- [ ] **Step 2: Run — verify it fails**

Run: `cd apps/web && npx vitest run src/features/dcr/EditCancelDcr.test.tsx`
Expected: FAIL — the modals don't exist.

- [ ] **Step 3: Implement `EditDcrModal.tsx`**

```tsx
import { Alert, Button, Group, Modal, SegmentedControl, Select, Stack, Text, Textarea, TextInput } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import type { ChangeSignificance, DcrDetail, DcrPatchBody, DcrReasonClass } from "../../lib/types";
import { proposedEffectiveIso } from "./DcrRaiseFields";
import { REASON_LABEL } from "./labels";
import { usePatchDcr } from "./mutations";

// Conditionally mounted by DcrAdvancePanel; seeded from the current dcr. Open-only at the call site.
export function EditDcrModal({ dcr, onClose }: { dcr: DcrDetail; onClose: () => void }) {
  const m = usePatchDcr(dcr.id);
  const [reasonText, setReasonText] = useState(dcr.reason_text);
  const [reasonClass, setReasonClass] = useState<DcrReasonClass>(dcr.reason_class);
  const [significance, setSignificance] = useState<ChangeSignificance>(dcr.change_significance);
  const [effectiveFrom, setEffectiveFrom] = useState(
    dcr.proposed_effective_from ? dcr.proposed_effective_from.slice(0, 10) : "",
  );
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setError(null);
    if (reasonText.trim().length === 0) return;
    const body: DcrPatchBody = {
      reason_text: reasonText.trim(),
      reason_class: reasonClass,
      change_significance: significance,
      proposed_effective_from: proposedEffectiveIso(effectiveFrom || null),
    };
    try {
      await m.mutateAsync(body);
      onClose();
    } catch (e) {
      // 409 dcr_not_editable (concurrent advance) — surface the server word; the onSettled invalidate
      // refreshes the drawer to the real state behind this calm error.
      setError(e instanceof ApiError ? e.message : "Could not save the change request.");
    }
  }

  return (
    <Modal opened onClose={onClose} title="Edit change request" size="lg">
      <Stack gap="sm">
        {error && <Alert color="red">{error}</Alert>}
        <Textarea
          label="Reason for change"
          required
          autosize
          minRows={2}
          value={reasonText}
          onChange={(e) => setReasonText(e.currentTarget.value)}
        />
        <Select
          label="Reason class"
          required
          value={reasonClass}
          onChange={(v) => v && setReasonClass(v as DcrReasonClass)}
          data={(Object.entries(REASON_LABEL) as [DcrReasonClass, string][]).map(([value, label]) => ({
            value,
            label,
          }))}
          comboboxProps={{ keepMounted: false }}
        />
        <div>
          <Text size="sm" fw={500} mb={4}>
            Significance
          </Text>
          <SegmentedControl
            value={significance}
            onChange={(v) => setSignificance(v as ChangeSignificance)}
            data={[
              { value: "MINOR", label: "Minor" },
              { value: "MAJOR", label: "Major" },
            ]}
          />
        </div>
        <TextInput
          type="date"
          label="Proposed effective from (optional)"
          value={effectiveFrom}
          onChange={(e) => setEffectiveFrom(e.currentTarget.value)}
        />
        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} loading={m.isPending} disabled={reasonText.trim().length === 0}>
            Save
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
```

- [ ] **Step 4: Implement `CancelDcrModal.tsx`**

```tsx
import { Alert, Button, Group, Modal, Stack, Text, Textarea } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import type { DcrDetail } from "../../lib/types";
import { useCancelDcr } from "./mutations";

export function CancelDcrModal({ dcr, onClose }: { dcr: DcrDetail; onClose: () => void }) {
  const m = useCancelDcr(dcr.id);
  const [comment, setComment] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setError(null);
    try {
      await m.mutateAsync({ comment: comment.trim() || undefined });
      onClose();
    } catch (e) {
      // 409 dcr_not_cancellable (concurrent advance) — calm; the onSettled invalidate refreshes the drawer.
      setError(e instanceof ApiError ? e.message : "Could not cancel the change request.");
    }
  }

  return (
    <Modal opened onClose={onClose} title="Cancel change request">
      <Stack gap="sm">
        {error && <Alert color="red">{error}</Alert>}
        <Text size="sm">This withdraws {dcr.identifier}. It can't be undone.</Text>
        <Textarea
          label="Comment (optional)"
          autosize
          minRows={2}
          value={comment}
          onChange={(e) => setComment(e.currentTarget.value)}
        />
        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose}>
            Keep open
          </Button>
          <Button color="red" onClick={() => void submit()} loading={m.isPending}>
            Cancel change request
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
```

- [ ] **Step 5: Run the test — verify it passes**

Run: `cd apps/web && npx vitest run src/features/dcr/EditCancelDcr.test.tsx`
Expected: PASS (all three cases).

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/features/dcr/EditDcrModal.tsx apps/web/src/features/dcr/CancelDcrModal.tsx apps/web/src/features/dcr/EditCancelDcr.test.tsx
git commit -m "feat(s-dcr-ui-2a): EditDcrModal + CancelDcrModal with calm 409 self-heal"
```

---

### Task 6: `DcrAdvancePanel` + wire into `DcrDrawer`

**Files:**
- Create: `apps/web/src/features/dcr/DcrAdvancePanel.tsx`
- Test: `apps/web/src/features/dcr/DcrAdvancePanel.test.tsx`
- Modify: `apps/web/src/features/dcr/DcrDrawer.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import type { DcrDetail } from "../../lib/types";
import { DcrAdvancePanel } from "./DcrAdvancePanel";

function grant(...keys: string[]) {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: keys.map((key) => ({ key, effect: "ALLOW" })),
      }),
    ),
  );
}

const base: DcrDetail = {
  id: "dcr00001-0001-0001-0001-000000000001",
  identifier: "DCR-2026-0001",
  target_document_id: "doc1",
  change_type: "REVISE",
  change_significance: "MAJOR",
  reason_class: "capa",
  reason_text: "r",
  source_link_type: null,
  source_link_id: null,
  proposed_effective_from: null,
  resulting_version_id: null,
  state: "Open",
  decision: null,
  created_by: "bbbb1111-1111-1111-1111-111111111111",
  created_at: "2026-06-10T09:00:00+00:00",
  stage_events: [],
};

it("shows Edit + Cancel for an Open DCR with both keys", async () => {
  grant("changeRequest.assess", "changeRequest.close");
  renderWithProviders(<DcrAdvancePanel dcr={base} />);
  expect(await screen.findByRole("button", { name: "Edit details" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
});

it("hides Edit once past Open but keeps Cancel through Routed", async () => {
  grant("changeRequest.assess", "changeRequest.close");
  renderWithProviders(<DcrAdvancePanel dcr={{ ...base, state: "Routed" }} />);
  expect(await screen.findByRole("button", { name: "Cancel" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Edit details" })).toBeNull();
});

it("renders nothing in a terminal state", async () => {
  grant("changeRequest.assess", "changeRequest.close");
  const { container } = renderWithProviders(<DcrAdvancePanel dcr={{ ...base, state: "Closed" }} />);
  // give the permissions query a tick to resolve, then assert no action buttons
  expect(await screen.findByTestId("probe")).toBeInTheDocument(); // see harness note
  expect(screen.queryByRole("button", { name: "Edit details" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
});
```

> For the terminal-state test, wrap the panel so there's a stable probe to await:
> `renderWithProviders(<><div data-testid="probe" /><DcrAdvancePanel dcr={{ ...base, state: "Closed" }} /></>)`. Adjust the third test to render that fragment.

- [ ] **Step 2: Run — verify it fails**

Run: `cd apps/web && npx vitest run src/features/dcr/DcrAdvancePanel.test.tsx`
Expected: FAIL — `DcrAdvancePanel` does not exist.

- [ ] **Step 3: Implement `DcrAdvancePanel.tsx`**

```tsx
import { Button, Group, Loader } from "@mantine/core";
import { useState } from "react";
import { usePermissions } from "../../app/shell/usePermissions";
import type { DcrDetail } from "../../lib/types";
import { CancelDcrModal } from "./CancelDcrModal";
import { EditDcrModal } from "./EditDcrModal";

const CANCELLABLE = ["Open", "Assessed", "Routed"];

// ui-2a: the early-state write affordances (Edit while Open, Cancel while not-yet-implemented). ui-2b
// grows this into the full assess/route/implement/close panel. DCR gating is SYSTEM-scoped — the _dcr
// serializer carries no process_id, so the FE can't resolve the PROCESS scope (the read-spine precedent;
// a PROCESS-only grant-holder rides the v1 SYSTEM override).
export function DcrAdvancePanel({ dcr }: { dcr: DcrDetail }) {
  const { can, isLoading } = usePermissions();
  const [editing, setEditing] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  if (isLoading) return <Loader size="sm" />;
  const canEdit = can("changeRequest.assess") && dcr.state === "Open";
  const canCancel = can("changeRequest.close") && CANCELLABLE.includes(dcr.state);
  if (!canEdit && !canCancel) return null;
  return (
    <Group gap="xs">
      {canEdit && (
        <Button size="xs" variant="light" onClick={() => setEditing(true)}>
          Edit details
        </Button>
      )}
      {canCancel && (
        <Button size="xs" variant="subtle" color="red" onClick={() => setCancelling(true)}>
          Cancel
        </Button>
      )}
      {editing && <EditDcrModal dcr={dcr} onClose={() => setEditing(false)} />}
      {cancelling && <CancelDcrModal dcr={dcr} onClose={() => setCancelling(false)} />}
    </Group>
  );
}
```

- [ ] **Step 4: Wire into `DcrDrawer.tsx`**

(a) Add the import: `import { DcrAdvancePanel } from "./DcrAdvancePanel";`
(b) Render the panel immediately after the badges `</Group>` (the one closing at ~line 80, before `<Field label="Reason">`):

```tsx
          <DcrAdvancePanel dcr={dcr} />
```

- [ ] **Step 5: Run the panel test + the drawer test — verify all pass**

Run: `cd apps/web && npx vitest run src/features/dcr/DcrAdvancePanel.test.tsx src/features/dcr/DcrDrawer.test.tsx`
Expected: PASS. (The existing `DcrDrawer.test.tsx` still passes — the panel renders `null` when the default no-permissions fixture is in effect.)

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/features/dcr/DcrAdvancePanel.tsx apps/web/src/features/dcr/DcrAdvancePanel.test.tsx apps/web/src/features/dcr/DcrDrawer.tsx
git commit -m "feat(s-dcr-ui-2a): DcrAdvancePanel (Edit + Cancel) wired into the drawer"
```

---

### Task 7: `SpawnDcrModal` — the parameterized spawn modal

**Files:**
- Create: `apps/web/src/features/dcr/SpawnDcrModal.tsx`
- Test: `apps/web/src/features/dcr/SpawnDcrModal.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { expect, it } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLocation } from "react-router-dom";
import { renderWithProviders } from "../../test/render";
import { SpawnDcrModal } from "./SpawnDcrModal";
import { useRaiseDcrFromCapa } from "./mutations";

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="loc">{loc.pathname + loc.search}</div>;
}

// A tiny host that creates the mutation (hooks must be top-level) and mounts the modal.
function Host() {
  const m = useRaiseDcrFromCapa("capa0001-0001-0001-0001-000000000001");
  return (
    <>
      <SpawnDcrModal title="Raise from CAPA" mutation={m} onClose={() => {}} />
      <LocationProbe />
    </>
  );
}

it("spawns a CREATE DCR from a CAPA and deep-links to the new DCR", async () => {
  renderWithProviders(<Host />);
  await userEvent.click(screen.getByRole("radio", { name: "Create" }));
  await userEvent.type(screen.getByLabelText("Reason for change"), "Spawned from a CAPA.");
  await userEvent.click(screen.getByRole("button", { name: "Raise change request" }));
  expect(await screen.findByTestId("loc")).toHaveTextContent("/dcrs?dcr=dcrNEW01-0001-0001-0001-000000000099");
});
```

- [ ] **Step 2: Run — verify it fails**

Run: `cd apps/web && npx vitest run src/features/dcr/SpawnDcrModal.test.tsx`
Expected: FAIL — `SpawnDcrModal` does not exist.

- [ ] **Step 3: Implement `SpawnDcrModal.tsx`**

```tsx
import { Alert, Button, Group, Modal, Stack } from "@mantine/core";
import type { UseMutationResult } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../../lib/api";
import type { Dcr, DcrSpawnBody } from "../../lib/types";
import {
  DcrRaiseFields,
  EMPTY_DCR_FIELDS,
  isDcrFieldsValid,
  proposedEffectiveIso,
  type DcrFieldsValue,
} from "./DcrRaiseFields";
import type { SpawnDcrVars } from "./mutations";

// Parameterized for both spawn seams (CAPA + MR-output). The parent calls the hook (top-level) and passes
// the mutation; both resolve a Dcr identically for 201-new / 200-replay (no status branching). Conditionally
// mounted by the parent so close unmounts + resets. A fresh per-mount Idempotency-Key dedups a double-submit.
export function SpawnDcrModal({
  title,
  mutation,
  onClose,
}: {
  title: string;
  mutation: UseMutationResult<Dcr, Error, SpawnDcrVars>;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const [fields, setFields] = useState<DcrFieldsValue>(EMPTY_DCR_FIELDS);
  const [idempotencyKey] = useState(() => crypto.randomUUID());
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setError(null);
    if (!isDcrFieldsValid(fields)) return;
    const body: DcrSpawnBody = {
      change_type: fields.change_type,
      change_significance: fields.change_significance,
      reason_text: fields.reason_text.trim(),
      target_document_id: fields.change_type === "CREATE" ? null : fields.target_document_id,
      proposed_effective_from: proposedEffectiveIso(fields.proposed_effective_from),
    };
    try {
      const dcr = await mutation.mutateAsync({ body, idempotencyKey });
      navigate(`/dcrs?dcr=${dcr.id}`);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not raise the change request.");
    }
  }

  return (
    <Modal opened onClose={onClose} title={title} size="lg">
      <Stack gap="sm">
        {error && <Alert color="red">{error}</Alert>}
        <DcrRaiseFields value={fields} onChange={setFields} />
        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} loading={mutation.isPending} disabled={!isDcrFieldsValid(fields)}>
            Raise change request
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
```

- [ ] **Step 4: Run the test — verify it passes**

Run: `cd apps/web && npx vitest run src/features/dcr/SpawnDcrModal.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/dcr/SpawnDcrModal.tsx apps/web/src/features/dcr/SpawnDcrModal.test.tsx
git commit -m "feat(s-dcr-ui-2a): SpawnDcrModal — parameterized spawn with deep-link-on-success"
```

---

### Task 8: Wire `SpawnDcrModal` into `CapaDrawer` (CAPA → DCR seam)

**Files:**
- Modify: `apps/web/src/features/capa/CapaDrawer.tsx`
- Test: `apps/web/src/features/capa/CapaDrawerRaiseDcr.test.tsx`

> Check whether `CapaDrawer.test.tsx` exists; if it does, append these cases to it instead of a new file. The new-file path is given for safety.

- [ ] **Step 1: Write the failing test**

```tsx
import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLocation } from "react-router-dom";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { CapaDrawer } from "./CapaDrawer";

function grant(...keys: string[]) {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: keys.map((key) => ({ key, effect: "ALLOW" })),
      }),
    ),
  );
}
function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="loc">{loc.pathname + loc.search}</div>;
}

const CAPA_ID = "capa0001-0001-0001-0001-000000000001";

it("hides Raise change request without changeRequest.create", async () => {
  renderWithProviders(<CapaDrawer capaId={CAPA_ID} onClose={() => {}} />);
  // wait for the drawer body to load (a known fixture field), then assert the button is absent
  await screen.findByText(/Close gate/i);
  expect(screen.queryByRole("button", { name: "Raise change request" })).toBeNull();
});

it("raises a DCR from the CAPA and deep-links to it", async () => {
  grant("changeRequest.create");
  renderWithProviders(
    <>
      <CapaDrawer capaId={CAPA_ID} onClose={() => {}} />
      <LocationProbe />
    </>,
  );
  await userEvent.click(await screen.findByRole("button", { name: "Raise change request" }));
  await userEvent.click(await screen.findByRole("radio", { name: "Create" }));
  await userEvent.type(screen.getByLabelText("Reason for change"), "From this CAPA.");
  await userEvent.click(screen.getByRole("button", { name: /Raise change request/i }));
  expect(await screen.findByTestId("loc")).toHaveTextContent("/dcrs?dcr=dcrNEW01");
});
```

> The default `GET /capas/:id` MSW handler must resolve a CAPA whose `close_state` shows the drawer body (the existing CAPA tests rely on it). If the drawer needs a specific fixture state, mirror the existing `CapaDrawer`/`CapaBoardPage` test setup. Adjust the `findByText(/Close gate/i)` anchor to whatever the existing CAPA drawer test awaits.

- [ ] **Step 2: Run — verify it fails**

Run: `cd apps/web && npx vitest run src/features/capa/CapaDrawerRaiseDcr.test.tsx`
Expected: FAIL — no "Raise change request" button.

- [ ] **Step 3: Modify `CapaDrawer.tsx`**

(a) Imports — add `Button` to the `@mantine/core` import, and:

```tsx
import { useState } from "react";
import { usePermissions } from "../../app/shell/usePermissions";
import { SpawnDcrModal } from "../dcr/SpawnDcrModal";
import { useRaiseDcrFromCapa } from "../dcr/mutations";
```

(b) Hooks at the top of `CapaDrawer` (with `useCapa`/`useUserDirectory`):

```tsx
  const { can } = usePermissions();
  const raiseDcr = useRaiseDcrFromCapa(capaId ?? "");
  const [raisingDcr, setRaisingDcr] = useState(false);
```

(c) Inside the loaded body (the `else` branch, after the badges `</Group>` at ~line 56), add a gated button:

```tsx
          {can("changeRequest.create") && (
            <Button
              size="xs"
              variant="light"
              style={{ alignSelf: "flex-start" }}
              onClick={() => setRaisingDcr(true)}
            >
              Raise change request
            </Button>
          )}
```

(d) Mount the modal inside the body `<Stack>` (it can sit at the end, before `</Stack>`):

```tsx
          {raisingDcr && (
            <SpawnDcrModal
              title="Raise a change request from this CAPA"
              mutation={raiseDcr}
              onClose={() => setRaisingDcr(false)}
            />
          )}
```

- [ ] **Step 4: Run the test — verify it passes**

Run: `cd apps/web && npx vitest run src/features/capa/CapaDrawerRaiseDcr.test.tsx`
Expected: PASS (both cases).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/capa/CapaDrawer.tsx apps/web/src/features/capa/CapaDrawerRaiseDcr.test.tsx
git commit -m "feat(s-dcr-ui-2a): Raise change request from a CAPA (the CAPA->DCR seam)"
```

---

### Task 9: Wire `SpawnDcrModal` into `ReviewOutputsSection` (MR-output → DCR seam)

**Files:**
- Modify: `apps/web/src/features/management-review/ReviewOutputsSection.tsx`
- Test: `apps/web/src/features/management-review/ReviewOutputsRaiseDcr.test.tsx`

> This closes the S-mr-1/-2/-3 named "MR→DCR FE" deferral.

- [ ] **Step 1: Write the failing test**

```tsx
import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLocation } from "react-router-dom";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import type { ReviewOutput } from "../../lib/types";
import { ReviewOutputsSection } from "./ReviewOutputsSection";

function grant(...keys: string[]) {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: keys.map((key) => ({ key, effect: "ALLOW" })),
      }),
    ),
  );
}
function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="loc">{loc.pathname + loc.search}</div>;
}

const REVIEW_ID = "mr000001-0001-0001-0001-000000000001";
const action: ReviewOutput = {
  id: "out00001-0001-0001-0001-000000000001",
  output_type: "ACTION",
  description: "Revise the calibration SOP.",
  owner_user_id: "bbbb1111-1111-1111-1111-111111111111",
  due_date: null,
  spawned_task_id: null,
  spawned_capa_id: null,
} as ReviewOutput; // mirror the real ReviewOutput fields the section reads

it("raises a DCR from a tracked ACTION output and deep-links to it", async () => {
  grant("changeRequest.create");
  renderWithProviders(
    <>
      <ReviewOutputsSection reviewId={REVIEW_ID} outputs={[action]} editable={false} tracking />
      <LocationProbe />
    </>,
  );
  await userEvent.click(await screen.findByRole("button", { name: "Raise DCR" }));
  await userEvent.click(await screen.findByRole("radio", { name: "Create" }));
  await userEvent.type(screen.getByLabelText("Reason for change"), "From this MR action.");
  await userEvent.click(screen.getByRole("button", { name: "Raise change request" }));
  expect(await screen.findByTestId("loc")).toHaveTextContent("/dcrs?dcr=dcrNEW01");
});

it("does not show Raise DCR when the review is not tracking", async () => {
  grant("changeRequest.create");
  renderWithProviders(
    <ReviewOutputsSection reviewId={REVIEW_ID} outputs={[action]} editable={false} tracking={false} />,
  );
  await screen.findByText("Revise the calibration SOP.");
  expect(screen.queryByRole("button", { name: "Raise DCR" })).toBeNull();
});
```

> Pin the `action` fixture's fields to the real `ReviewOutput` type in `lib/types.ts` (use `satisfies ReviewOutput` if all required fields are present; the `as ReviewOutput` cast above is a fallback — replace it with the exact shape). The `ActionRow` subcomponent calls `useTask(output.spawned_task_id ?? null, { retry: false })`; with `spawned_task_id: null` the query is disabled, so no extra MSW handler is needed.

- [ ] **Step 2: Run — verify it fails**

Run: `cd apps/web && npx vitest run src/features/management-review/ReviewOutputsRaiseDcr.test.tsx`
Expected: FAIL — no "Raise DCR" button.

- [ ] **Step 3: Modify `ReviewOutputsSection.tsx`**

(a) Add imports:

```tsx
import { SpawnDcrModal } from "../dcr/SpawnDcrModal";
import { useRaiseDcrFromMrOutput } from "../dcr/mutations";
```

(b) Add state + a derived gate + the mutation (top-level hook re-parameterized by the selected output id):

```tsx
  const [raiseDcrFor, setRaiseDcrFor] = useState<string | null>(null);
  const raiseDcr = useRaiseDcrFromMrOutput(reviewId, raiseDcrFor ?? "");
  const canRaiseDcr = tracking && can("changeRequest.create");
```

(c) Inside the ACTION-row affordance `<Group gap="xs" wrap="nowrap">` (after the existing `t === "ACTION"` CAPA block, before the `canEdit` Remove block), add the Raise DCR button (Raise-only — there's no `spawned_dcr_id` latch, the spawn is 1:N):

```tsx
                    {t === "ACTION" && canRaiseDcr && (
                      <Button size="compact-xs" variant="light" onClick={() => setRaiseDcrFor(o.id)}>
                        Raise DCR
                      </Button>
                    )}
```

(d) Mount the modal at the end (after the existing `{raiseFor && <RaiseMrCapaModal .../>}` block):

```tsx
      {raiseDcrFor && (
        <SpawnDcrModal
          title="Raise a change request from this action"
          mutation={raiseDcr}
          onClose={() => setRaiseDcrFor(null)}
        />
      )}
```

- [ ] **Step 4: Run the test — verify it passes**

Run: `cd apps/web && npx vitest run src/features/management-review/ReviewOutputsRaiseDcr.test.tsx`
Expected: PASS (both cases). Run the existing MR section test too if one exists.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/management-review/ReviewOutputsSection.tsx apps/web/src/features/management-review/ReviewOutputsRaiseDcr.test.tsx
git commit -m "feat(s-dcr-ui-2a): Raise DCR from an MR ACTION output (closes the MR->DCR FE deferral)"
```

---

### Task 10: Full `/check-web` gate + a11y smoke verification

**Files:** none (verification + any fixups surfaced by the strict gate).

- [ ] **Step 1: Run the full web gate**

Run: `cd apps/web && npm run lint && npx tsc --noEmit && npm run build && npx vitest run`
(Or invoke the project `/check-web` skill.)
Expected: ALL green. Watch for:
- `noUncheckedIndexedAccess` nits (array indexing) the per-file run missed.
- The jest-dom × tsc trap — every new `*.test.tsx` must `import { expect, it } from "vitest"`.
- Unused-import lint in `DcrsRegisterPage.test.tsx` (remove the stray `RaiseDcrModal` import if present).

- [ ] **Step 2: Confirm a jest-axe smoke covers a modal-bearing render**

Verify `DcrsRegisterPage.test.tsx`'s existing `axe(container)` smoke still passes, and add one axe smoke that renders the page WITH `changeRequest.create` granted (so the Raise button is in the tree):

```tsx
it("has no a11y violations with the Raise button visible", async () => {
  grant("changeRequest.create");
  const { container } = renderWithProviders(<DcrsRegisterPage />);
  await screen.findByText("DCR-2026-0001");
  expect(await axe(container)).toHaveNoViolations();
});
```

Run: `cd apps/web && npx vitest run src/features/dcr/DcrsRegisterPage.test.tsx`
Expected: PASS.

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "test(s-dcr-ui-2a): full /check-web green + a11y smoke with Raise visible"
```

---

## Self-Review (completed by the plan author)

**1. Spec coverage** — every spec §3 component + §3.6 edit maps to a task: mutations.ts/types (T1), DcrRaiseFields (T2), RaiseDcrModal (T3), DcrsRegisterPage wiring (T4), EditDcrModal+CancelDcrModal (T5), DcrAdvancePanel+DcrDrawer (T6), SpawnDcrModal (T7), CapaDrawer (T8), ReviewOutputsSection (T9), full gate (T10). The spec's deferrals (assess/route/implement/close, the approval leg, capabilities.implement, the prose fix, the visual diff) are explicitly NOT tasks here — they are ui-2b/ui-3.

**2. Placeholder scan** — no "TBD"/"add error handling"/"similar to Task N". Every code step shows full code; every test step shows a real test. Two honest call-outs are flagged inline (the `ReviewOutput` fixture shape to pin in T9; the CapaDrawer body anchor in T8) because they depend on existing fixtures the implementer can read directly.

**3. Type consistency** — `DcrFieldsValue`/`EMPTY_DCR_FIELDS`/`isDcrFieldsValid`/`proposedEffectiveIso` (T2) are imported unchanged by T3/T5/T7. `SpawnDcrVars` (T1) is the mutation variable type used by both spawn hooks (T1) and the `SpawnDcrModal` prop (T7). `DcrCreateBody`/`DcrPatchBody`/`DcrCancelBody`/`DcrSpawnBody` (T1) are the exact bodies the modals build (T3/T5/T7). Query keys `["dcrs"]`/`["dcr",id]`/`["dcr-impact",id]` (T1 invalidators) mirror the read hooks verbatim. `usePermissions().can(key)` + the `grant()` test helper are consistent across T4/T6/T8/T9.

**Risks carried into execution:** confirm `ChangeSignificance` is exported (T1 S1); pin the `ReviewOutput` test fixture to the real type (T9); match the CapaDrawer test's body-load anchor to the existing CAPA drawer test (T8).
