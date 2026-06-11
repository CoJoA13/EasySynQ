# S-ack-2 — Acknowledgements UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended)
> or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Ship the four Acknowledgements UI surfaces (the per-task DOC_ACK attestation, the dedicated
bulk-ack inbox, the doc-page Acks tab + Acknowledged tile + distribution editor, and the TopBar ack bell)
over S-ack-1's existing endpoints — front-end only, no migration/key/endpoint/contract change.

**Architecture:** React/TS + Mantine SPA in `apps/web`. The attestation path is a *dedicated*
`AttestationCard` + `useAcknowledgeTask` (the shared `DecisionCard`/`DecisionSubjectType` stay untouched →
zero regression to the DOCUMENT/CAPA/PERIODIC_REVIEW legs). The doc page is refactored to a real Mantine
`Tabs`. Every read is calm-403-safe; every fixture is pinned to the real S-ack-1 serializer shapes.

**Tech Stack:** React 18, TypeScript (strict, `noUncheckedIndexedAccess`), Mantine v7, TanStack Query v5,
react-router-dom v6, MSW v2, Vitest, jest-axe. Spec:
`docs/superpowers/specs/2026-06-11-s-ack-2-acknowledgements-ui-design.md`.

**Conventions (read once):**
- Tests: `renderWithProviders(ui, { route })` + `TEST_AUTH` (sub/`/me`.id = `bbbb1111-1111-1111-1111-111111111111`)
  from `src/test/render.tsx`; MSW server `src/test/msw/server.ts`; fixtures + `handlers` in
  `src/test/msw/handlers.ts` (per-test override = `server.use(http.<verb>(...))`).
- Calm-403: a read where 403 is EXPECTED sets `retry: false` and surfaces a `forbidden` flag
  (`error instanceof ApiError && error.status === 403`) → a dimmed/gray panel, never a red crash.
- API client: `useApi()` → `{ get, getBlob, send }` (token implicit). `ApiError` carries `.status` + `.code`.
- Run a single web test file: `cd apps/web && npx vitest run src/path/to/file.test.tsx`
- Full gate (Task 14): `/check-web`; full vitest with a clean signal:
  `cd apps/web && npx vitest run --pool=forks --poolOptions.forks.singleFork=true`
- Commit after each task. Branch is `feat/s-ack-2-acknowledgements-ui` (already created).

---

## File structure

**New files**
- `apps/web/src/features/document/AckCoverageRing.tsx` — shared coverage `RingProgress` + counts (tile & tab)
- `apps/web/src/features/document/ackHooks.ts` — `useDistribution`, `useAcknowledgements`, `useUpdateDistribution`, `useDeleteDistributionEntry`
- `apps/web/src/features/document/AcknowledgementsTab.tsx` — the Acks tab (coverage zone + matrix zone)
- `apps/web/src/features/document/DistributionEditor.tsx` — `document.distribute`-gated issuance editor
- `apps/web/src/features/review/ackHooks.ts` — `useAcknowledgeTask`, `useBulkAcknowledge`
- `apps/web/src/features/review/DocAckContext.tsx` — best-effort doc context for the task leg
- `apps/web/src/features/review/AttestationCard.tsx` — the one-click attestation card
- `apps/web/src/features/review/AckInbox.tsx` — the bulk-ack inbox (+ an internal `AckInboxRow`)
- `apps/web/src/app/shell/useAckCount.ts` — the bell-count query (kept in shell to avoid a shell→feature dep)
- `*.test.tsx` siblings for each component/hook above

**Modified files**
- `apps/web/src/lib/types.ts` — additive ack types; `+ "DOC_ACK"` on `TaskType`
- `apps/web/src/test/msw/handlers.ts` — ack fixtures + handlers
- `apps/web/src/features/review/ReviewApprovePage.tsx` — the 4th `subject_type` branch
- `apps/web/src/features/review/TasksInbox.tsx` — route `?type=DOC_ACK` → `AckInbox`
- `apps/web/src/features/document/DocumentDetailPage.tsx` — tabs refactor + Acknowledged tile + Acks tab
- `apps/web/src/features/document/DocumentDetailPage.test.tsx` — adapt to tab-gated sections
- `apps/web/src/app/shell/TopBar.tsx` — wire the ack bell
- `docs/slice-history.md` — the missing S-ack-1 entry + the S-ack-2 entry + head bump
- `CLAUDE.md` — a Recent-learnings line (on merge)

---

## Task 1: Types + fixtures + MSW handlers (the ground truth)

**Files:**
- Modify: `apps/web/src/lib/types.ts`
- Modify: `apps/web/src/test/msw/handlers.ts`

- [ ] **Step 1: Add the ack types to `lib/types.ts`** (append after the S-web-8 drift block at EOF)

```ts
// ---- S-ack-2 (Acknowledgements UI) ------------------------------------------------------
// All shapes pinned to S-ack-1: api/documents.py (_distribution_payload, DistributionUpdate),
// services/ack/queries.py (coverage_counts/coverage_matrix), services/ack/decide.py.

export type DistributionTargetType = "user" | "org_role" | "process" | "folder";

export interface DistributionEntry {
  id: string;
  target_type: DistributionTargetType;
  target_id: string;
  ack_required: boolean;
  created_at: string;
}

// coverage is null when the doc has no Effective version (queries.coverage_counts).
export interface Coverage {
  required: number;
  acknowledged: number;
  pending: number;
  overdue: number;
}

export interface DistributionPayload {
  acknowledgement_required: boolean;
  entries: DistributionEntry[];
  coverage: Coverage | null;
}

export type AckStatus = "acknowledged" | "pending" | "overdue";

export interface AckMatrixRow {
  user_id: string;
  display_name: string | null;
  status: AckStatus;
  acknowledged_at: string | null;
  acknowledged_revision_label: string | null;
  due_at: string | null;
}

// POST /documents/{id}/distribution body. add_entries items: ack_required defaults true server-side.
export interface DistributionEntryCreate {
  target_type: "user" | "org_role"; // process/folder are 422 (target_kind_deferred) — never sent
  target_id: string;
  ack_required?: boolean;
}
export interface DistributionUpdateBody {
  acknowledgement_required?: boolean | null;
  add_entries?: DistributionEntryCreate[];
}

// POST /tasks/{id}/decision (DOC_ACK) → the engine result + the three ack fields (services/ack/decide.py).
export interface AckDecisionResult {
  task_id: string;
  instance_id: string;
  stage_key: string;
  outcome: string | null;
  decided_at: string | null;
  decided_by: string;
  stage_state: string;
  current_state: string;
  signature_spec: Record<string, unknown> | null;
  comment: string | null;
  replayed: boolean;
  document_id: string;
  document_version_id: string | null;
  acknowledgement_id: string | null;
}

// GET /roles (authz.py; role.read — QMS Owner + admin hold it). The editor's org_role picker source.
export interface RoleSummary {
  id: string;
  name: string;
  description: string | null;
  is_reserved: boolean;
}
```

- [ ] **Step 2: Add `"DOC_ACK"` to the `TaskType` union** (`lib/types.ts`, the existing `TaskType`)

```ts
export type TaskType =
  | "APPROVE"
  | "REVIEW"
  | "PERIODIC_REVIEW"
  | "DOC_ACK"
  | "AUDIT_TASK"
  | "FINDING_ACK"
  | "CAPA_STAGE"
  | "CAPA_ACTION"
  | "VERIFY"
  | "MR_INPUT"
  | "MR_ACTION"
  | "DCR_TRIAGE";
```

Do **not** touch `DecisionSubjectType` (adding DOC_ACK would force exhaustive `DecisionCard` records;
the attestation path is separate).

- [ ] **Step 3: Add the ack fixtures to `handlers.ts`** (place after the S-web-8 drift fixtures, before
  `export const handlers = [`). Note the import line at top must gain the new types used by `satisfies`.

```ts
// at the top import: add DistributionPayload, AckMatrixRow, AckDecisionResult to the type import list.

// ---- S-ack-2 acknowledgements fixtures (pinned to the S-ack-1 serializers) ----
// The doc-detail document (docFixture[0], SOP-PUR-014) is flag-on with a fuller audience.
export const distributionFixture = {
  acknowledgement_required: true,
  entries: [
    { id: "de000001-0001-0001-0001-000000000001", target_type: "user", target_id: "bbbb1111-1111-1111-1111-111111111111", ack_required: true, created_at: "2026-03-15T09:00:00+00:00" },
    { id: "de000002-0002-0002-0002-000000000002", target_type: "org_role", target_id: "ro000001-0001-0001-0001-000000000001", ack_required: true, created_at: "2026-03-15T09:05:00+00:00" },
  ],
  coverage: { required: 47, acknowledged: 41, pending: 6, overdue: 2 },
} satisfies DistributionPayload;

// Flag ON but no Effective version → coverage null (queries.coverage_counts boundary None).
export const distributionNoEffectiveFixture = {
  acknowledgement_required: true,
  entries: [],
  coverage: null,
} satisfies DistributionPayload;

// Flag OFF but an Effective version exists → honest zeros, not null.
export const distributionFlagOffFixture = {
  acknowledgement_required: false,
  entries: [],
  coverage: { required: 0, acknowledged: 0, pending: 0, overdue: 0 },
} satisfies DistributionPayload;

export const ackMatrixFixture = [
  { user_id: "bbbb1111-1111-1111-1111-111111111111", display_name: "Mara Quality", status: "acknowledged", acknowledged_at: "2026-03-16T10:00:00+00:00", acknowledged_revision_label: "Rev B", due_at: null },
  { user_id: "bbbb2222-2222-2222-2222-222222222222", display_name: "Diego Owner", status: "pending", acknowledged_at: null, acknowledged_revision_label: null, due_at: "2026-03-30T00:00:00+00:00" },
  { user_id: "bbbb3333-3333-3333-3333-333333333333", display_name: "Sam Patel", status: "overdue", acknowledged_at: null, acknowledged_revision_label: null, due_at: "2026-03-20T00:00:00+00:00" },
] satisfies AckMatrixRow[];

// A DOC_ACK task detail (GET /tasks/{id}) — subject_type/subject_id are DETAIL-ONLY (the list omits them).
export const docAckTask = {
  id: "tkak1111-1111-1111-1111-111111111111",
  instance_id: "wfak1111-1111-1111-1111-111111111111",
  stage_key: "acknowledge",
  type: "DOC_ACK",
  state: "PENDING",
  assignee_user_id: "bbbb1111-1111-1111-1111-111111111111",
  candidate_pool: ["bbbb1111-1111-1111-1111-111111111111"],
  action_expected: "acknowledge",
  due_at: "2026-03-30T00:00:00+00:00",
  subject_type: "DOC_ACK",
  subject_id: "11111111-1111-1111-1111-111111111111",
};
// The list row (GET /tasks?type=DOC_ACK) — subject_type/subject_id STRIPPED (matches _task without them).
export const docAckListRow = {
  id: docAckTask.id, instance_id: docAckTask.instance_id, stage_key: docAckTask.stage_key,
  type: "DOC_ACK", state: "PENDING", assignee_user_id: docAckTask.assignee_user_id,
  candidate_pool: docAckTask.candidate_pool, action_expected: "acknowledge", due_at: docAckTask.due_at,
};

export const ackDecisionResultFixture = {
  task_id: docAckTask.id,
  instance_id: docAckTask.instance_id,
  stage_key: "acknowledge",
  outcome: "acknowledge",
  decided_at: "2026-06-11T10:00:00+00:00",
  decided_by: "bbbb1111-1111-1111-1111-111111111111",
  stage_state: "COMPLETED",
  current_state: "ACKNOWLEDGED",
  signature_spec: null,
  comment: null,
  replayed: false,
  document_id: "11111111-1111-1111-1111-111111111111",
  document_version_id: "dddd1111-1111-1111-1111-111111111111",
  acknowledgement_id: "ack00001-0001-0001-0001-000000000001",
} satisfies AckDecisionResult;

export const rolesFixture = [
  { id: "ro000001-0001-0001-0001-000000000001", name: "Employee", description: "All staff", is_reserved: true },
  { id: "ro000002-0002-0002-0002-000000000002", name: "Process Owner", description: null, is_reserved: true },
];
```

- [ ] **Step 4: Add the handlers** (inside the `handlers` array — place near the S-web-5 task handlers).
  The DOC_ACK task-detail + decision + distribution + roles handlers, and make `GET /tasks` honour `type`.

```ts
  // ---- S-ack-2 acknowledgements (default happy-path; per-test overrides for 403/409/null-coverage) ----
  http.get("/api/v1/documents/:id/distribution", () => HttpResponse.json(distributionFixture)),
  http.post("/api/v1/documents/:id/distribution", () => HttpResponse.json(distributionFixture)),
  http.delete(
    "/api/v1/documents/:id/distribution/:entryId",
    () => new HttpResponse(null, { status: 204 }),
  ),
  http.get("/api/v1/documents/:id/acknowledgements", () => HttpResponse.json(ackMatrixFixture)),
  http.get("/api/v1/roles", () => HttpResponse.json(rolesFixture)),
```

  Then REPLACE the existing `GET /tasks` + `GET /tasks/:id` handlers (S-web-5 block) with type-aware ones:

```ts
  http.get("/api/v1/tasks", ({ request }) => {
    const type = new URL(request.url).searchParams.get("type");
    if (type === "DOC_ACK") return HttpResponse.json([docAckListRow]);
    return HttpResponse.json(taskFixture);
  }),
  http.get("/api/v1/tasks/:id", ({ params }) => {
    if (params.id === periodicReviewTask.id) return HttpResponse.json(periodicReviewTask);
    if (params.id === docAckTask.id) return HttpResponse.json(docAckTask);
    return HttpResponse.json(approveTask);
  }),
```

  And make the decision handler branch on the outcome (so an `acknowledge` POST returns the ack result):

```ts
  http.post("/api/v1/tasks/:id/decision", async ({ request }) => {
    const body = (await request.json()) as { outcome?: string };
    if (body.outcome === "acknowledge") return HttpResponse.json(ackDecisionResultFixture);
    return HttpResponse.json({
      task_id: approveTask.id, instance_id: approvalFixture.id, stage_key: "quality_approval",
      outcome: "approve", decided_at: "2026-06-08T10:00:00+00:00",
      decided_by: "bbbb1111-1111-1111-1111-111111111111", signature_event: null, comment: null,
    });
  }),
```

- [ ] **Step 5: Verify the project still typechecks + the existing suite is green** (the fixtures
  `satisfies` the new types; the handler edits don't regress)

Run: `cd apps/web && npx tsc --noEmit && npx vitest run src/features/review src/features/document --pool=forks --poolOptions.forks.singleFork=true`
Expected: tsc clean; the existing review/document tests still PASS (the `GET /tasks` no-type path returns `taskFixture` unchanged).

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/lib/types.ts apps/web/src/test/msw/handlers.ts
git commit -m "feat(s-ack-2): ack types + MSW fixtures/handlers pinned to S-ack-1 serializers"
```

---

## Task 2: `AckCoverageRing` (shared coverage widget)

**Files:**
- Create: `apps/web/src/features/document/AckCoverageRing.tsx`
- Test: `apps/web/src/features/document/AckCoverageRing.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { renderWithProviders } from "../../test/render";
import { AckCoverageRing } from "./AckCoverageRing";

describe("AckCoverageRing", () => {
  test("renders the acknowledged/required ratio and percent", () => {
    renderWithProviders(<AckCoverageRing coverage={{ required: 47, acknowledged: 41, pending: 6, overdue: 2 }} />);
    expect(screen.getByText("41 / 47")).toBeInTheDocument();
    expect(screen.getByText("87%")).toBeInTheDocument(); // round(41/47*100)
    expect(screen.getByText(/6 pending/)).toBeInTheDocument();
  });

  test("null coverage → an honest dash, no ring", () => {
    renderWithProviders(<AckCoverageRing coverage={null} />);
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.getByText(/Not yet effective/)).toBeInTheDocument();
  });

  test("flag-off zeros → not-distributed copy", () => {
    renderWithProviders(<AckCoverageRing coverage={{ required: 0, acknowledged: 0, pending: 0, overdue: 0 }} />);
    expect(screen.getByText(/Not distributed for acknowledgement/)).toBeInTheDocument();
  });

  test("100% renders without dividing by anything odd", () => {
    renderWithProviders(<AckCoverageRing coverage={{ required: 1, acknowledged: 1, pending: 0, overdue: 0 }} />);
    expect(screen.getByText("100%")).toBeInTheDocument();
    expect(screen.getByText("1 / 1")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run it — expect FAIL** (`AckCoverageRing` not defined)

Run: `cd apps/web && npx vitest run src/features/document/AckCoverageRing.test.tsx`

- [ ] **Step 3: Implement**

```tsx
import { Group, RingProgress, Stack, Text } from "@mantine/core";
import type { Coverage } from "../../lib/types";

// S-ack-2: the shared read-and-understood coverage widget (the Acknowledged tile + the Acks tab).
// Rides the document.read-gated distribution GET; coverage is null when there is no Effective version,
// and all-zeros when the ack flag is off (an Effective version exists but no obligations).
export function AckCoverageRing({ coverage, size = 88 }: { coverage: Coverage | null; size?: number }) {
  if (coverage === null) {
    return (
      <Stack gap={2}>
        <Text size="xl" fw={700}>—</Text>
        <Text size="xs" c="dimmed">Not yet effective</Text>
      </Stack>
    );
  }
  if (coverage.required === 0) {
    return (
      <Stack gap={2}>
        <Text size="xl" fw={700}>—</Text>
        <Text size="xs" c="dimmed">Not distributed for acknowledgement</Text>
      </Stack>
    );
  }
  const pct = Math.round((coverage.acknowledged / coverage.required) * 100);
  return (
    <Group gap="md" wrap="nowrap" align="center">
      <RingProgress
        size={size}
        thickness={8}
        roundCaps
        sections={[{ value: pct, color: "green" }]}
        label={<Text ta="center" size="sm" fw={700}>{pct}%</Text>}
        aria-label={`Acknowledgement coverage ${pct} percent`}
      />
      <Stack gap={2}>
        <Text size="xl" fw={700}>{coverage.acknowledged} / {coverage.required}</Text>
        <Text size="xs" c="dimmed">
          {coverage.pending} pending{coverage.overdue > 0 ? ` · ${coverage.overdue} overdue` : ""}
        </Text>
      </Stack>
    </Group>
  );
}
```

- [ ] **Step 4: Run it — expect PASS.** Run: `cd apps/web && npx vitest run src/features/document/AckCoverageRing.test.tsx`

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/document/AckCoverageRing.tsx apps/web/src/features/document/AckCoverageRing.test.tsx
git commit -m "feat(s-ack-2): AckCoverageRing — shared coverage ring + counts"
```

---

## Task 3: Doc-page ack hooks (`features/document/ackHooks.ts`)

**Files:**
- Create: `apps/web/src/features/document/ackHooks.ts`
- Test: `apps/web/src/features/document/ackHooks.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { describe, expect, test } from "vitest";
import { AuthContext } from "../../lib/auth";
import { server } from "../../test/msw/server";
import { distributionFixture, ackMatrixFixture } from "../../test/msw/handlers";
import { TEST_AUTH } from "../../test/render";
import { theme } from "../../theme/mantine";
import { useDistribution, useAcknowledgements } from "./ackHooks";

const DOC = "11111111-1111-1111-1111-111111111111";

// A PRODUCTION-defaults QueryClient (NO retry:false override) — proves the hook's own retry:false
// stops the deny from being re-hammered (the S-web-8 lesson; the test wrapper would otherwise mask it).
function prodWrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient();
  return (
    <MantineProvider theme={theme}>
      <QueryClientProvider client={client}>
        <AuthContext.Provider value={TEST_AUTH}>{children}</AuthContext.Provider>
      </QueryClientProvider>
    </MantineProvider>
  );
}

describe("ack hooks", () => {
  test("useDistribution returns the payload", async () => {
    const { result } = renderHook(() => useDistribution(DOC), { wrapper: prodWrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(distributionFixture);
  });

  test("useAcknowledgements returns the matrix", async () => {
    const { result } = renderHook(() => useAcknowledgements(DOC, true), { wrapper: prodWrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(ackMatrixFixture);
  });

  test("useAcknowledgements flags a 403 as forbidden WITHOUT retry-hammering (production defaults)", async () => {
    let calls = 0;
    server.use(
      http.get("/api/v1/documents/:id/acknowledgements", () => {
        calls += 1;
        return HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 });
      }),
    );
    const { result } = renderHook(() => useAcknowledgements(DOC, true), { wrapper: prodWrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.forbidden).toBe(true);
    expect(calls).toBe(1); // retry:false → exactly one call, no 3× backoff hammer
  });

  test("useAcknowledgements does not fetch when enabled=false (flag off)", async () => {
    const { result } = renderHook(() => useAcknowledgements(DOC, false), { wrapper: prodWrapper });
    await new Promise((r) => setTimeout(r, 20));
    expect(result.current.fetchStatus).toBe("idle");
  });
});
```

- [ ] **Step 2: Run it — expect FAIL.** Run: `cd apps/web && npx vitest run src/features/document/ackHooks.test.tsx`

- [ ] **Step 3: Implement**

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, useApi } from "../../lib/api";
import type { AckMatrixRow, DistributionPayload, DistributionUpdateBody } from "../../lib/types";

// S-ack-2: the doc-page ack reads + writes. The distribution GET is document.read (counts for any
// reader); the named matrix + the writes are document.distribute (the Acks tab gates them per-key).

export function useDistribution(documentId: string) {
  const api = useApi();
  const query = useQuery({
    queryKey: ["distribution", documentId],
    queryFn: () => api.get<DistributionPayload>(`/api/v1/documents/${documentId}/distribution`),
  });
  const forbidden = query.error instanceof ApiError && query.error.status === 403;
  return { ...query, forbidden };
}

// The named matrix is document.distribute-gated → a 403 is the EXPECTED no-access outcome for a plain
// reader. retry:false + the forbidden flag (the drift/compliance pattern). `enabled` is the ack flag:
// the matrix is empty/meaningless when acknowledgement is not required.
export function useAcknowledgements(documentId: string, enabled: boolean) {
  const api = useApi();
  const query = useQuery({
    queryKey: ["acknowledgements", documentId],
    queryFn: () => api.get<AckMatrixRow[]>(`/api/v1/documents/${documentId}/acknowledgements`),
    enabled,
    retry: false,
  });
  const forbidden = query.error instanceof ApiError && query.error.status === 403;
  return { ...query, forbidden };
}

export function useUpdateDistribution(documentId: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: DistributionUpdateBody) =>
      api.send<DistributionPayload>("POST", `/api/v1/documents/${documentId}/distribution`, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["distribution", documentId] });
      void qc.invalidateQueries({ queryKey: ["acknowledgements", documentId] });
      void qc.invalidateQueries({ queryKey: ["document", documentId] });
    },
  });
}

export function useDeleteDistributionEntry(documentId: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (entryId: string) =>
      api.send<void>("DELETE", `/api/v1/documents/${documentId}/distribution/${entryId}`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["distribution", documentId] });
      void qc.invalidateQueries({ queryKey: ["acknowledgements", documentId] });
    },
  });
}
```

- [ ] **Step 4: Run it — expect PASS.** Run: `cd apps/web && npx vitest run src/features/document/ackHooks.test.tsx`

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/document/ackHooks.ts apps/web/src/features/document/ackHooks.test.tsx
git commit -m "feat(s-ack-2): doc-page ack hooks (distribution read/write + matrix, calm-403)"
```

---

## Task 4: `AcknowledgementsTab`

**Files:**
- Create: `apps/web/src/features/document/AcknowledgementsTab.tsx`
- Test: `apps/web/src/features/document/AcknowledgementsTab.test.tsx`

`usePermissions` lives at `apps/web/src/app/shell/usePermissions.ts` (`usePermissions({level,id}).can(key)`).
The default `/me/permissions` handler returns `permissions: []` → `can(...)` is false (the reader path).
Override per-test to grant `document.distribute`.

- [ ] **Step 1: Write the failing test**

```tsx
import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, test } from "vitest";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { AcknowledgementsTab } from "./AcknowledgementsTab";

const DOC = "11111111-1111-1111-1111-111111111111";

function grantDistribute() {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "DOC", selector: { id: DOC } },
        permissions: [{ key: "document.distribute", effect: "ALLOW", source: "system_override" }],
      }),
    ),
  );
}

describe("AcknowledgementsTab", () => {
  test("a plain reader sees the coverage ring + counts but NOT the named matrix", async () => {
    renderWithProviders(<AcknowledgementsTab documentId={DOC} active />);
    expect(await screen.findByText("41 / 47")).toBeInTheDocument();
    // The matrix names are document.distribute-gated → absent for the reader.
    expect(screen.queryByText("Sam Patel")).not.toBeInTheDocument();
    expect(screen.getByText(/can view coverage but not the named/i)).toBeInTheDocument();
  });

  test("a distributor sees the named matrix with status badges + the pending avatar stack", async () => {
    grantDistribute();
    renderWithProviders(<AcknowledgementsTab documentId={DOC} active />);
    expect(await screen.findByText("Sam Patel")).toBeInTheDocument();
    const row = screen.getByText("Sam Patel").closest("tr")!;
    expect(within(row).getByText("overdue")).toBeInTheDocument();
    expect(screen.getByText("Mara Quality")).toBeInTheDocument();
  });

  test("no Remind button anywhere (R43 omitted-not-faked)", async () => {
    grantDistribute();
    renderWithProviders(<AcknowledgementsTab documentId={DOC} active />);
    await screen.findByText("Sam Patel");
    expect(screen.queryByRole("button", { name: /remind/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/last reminded/i)).not.toBeInTheDocument();
  });

  test("does not fetch until active", async () => {
    renderWithProviders(<AcknowledgementsTab documentId={DOC} active={false} />);
    expect(screen.queryByText("41 / 47")).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run it — expect FAIL.** Run: `cd apps/web && npx vitest run src/features/document/AcknowledgementsTab.test.tsx`

- [ ] **Step 3: Implement** (the matrix zone + editor render only with `document.distribute`. The
  `DistributionEditor` is built in Task 5 — create a minimal stub now [see end of this step] so this file
  compiles; Task 5 replaces the stub with the real component + its tests. The distribution hook doesn't
  take an `enabled` arg, so gate the whole tab by short-circuiting render on `!active`.)

```tsx
import { Alert, Avatar, Badge, Card, Group, Stack, Table, Text } from "@mantine/core";
import { usePermissions } from "../../app/shell/usePermissions";
import type { AckStatus } from "../../lib/types";
import { AckCoverageRing } from "./AckCoverageRing";
import { DistributionEditor } from "./DistributionEditor";
import { useAcknowledgements, useDistribution } from "./ackHooks";

const STATUS_COLOR: Record<AckStatus, string> = {
  acknowledged: "green",
  pending: "gray",
  overdue: "red",
};

function initials(name: string | null): string {
  if (!name) return "?";
  return name.split(/\s+/).slice(0, 2).map((p) => p[0]?.toUpperCase() ?? "").join("") || "?";
}

export function AcknowledgementsTab({ documentId, active }: { documentId: string; active: boolean }) {
  const perms = usePermissions({ level: "DOC", id: documentId });
  const canManage = perms.can("document.distribute");
  const dist = useDistribution(documentId);
  const flagOn = dist.data?.acknowledgement_required ?? false;
  const matrix = useAcknowledgements(documentId, active && canManage && flagOn);

  if (!active) return null;
  if (dist.isLoading) return <Text c="dimmed">Loading acknowledgement coverage…</Text>;
  if (dist.isError) {
    return dist.forbidden ? (
      <Text size="sm" c="dimmed">You don't have access to acknowledgement coverage.</Text>
    ) : (
      <Text size="sm" c="red">Could not load acknowledgement coverage.</Text>
    );
  }

  const pending = (matrix.data ?? []).filter((r) => r.status !== "acknowledged");

  return (
    <Stack gap="lg">
      <Card withBorder>
        <Stack gap="sm">
          <Text fw={600}>Acknowledgement coverage</Text>
          <Text size="sm" c="dimmed">Read-and-understood coverage of the governing revision (Cl 7.3 awareness).</Text>
          <AckCoverageRing coverage={dist.data?.coverage ?? null} />
        </Stack>
      </Card>

      {!canManage ? (
        <Alert color="gray" title="Limited view">
          You can view coverage but not the named acknowledgement matrix or distribution settings.
        </Alert>
      ) : (
        <>
          <Card withBorder>
            <Stack gap="sm">
              <Group justify="space-between">
                <Text fw={600}>Who's acknowledged</Text>
                {pending.length > 0 && (
                  <Group gap="xs" align="center">
                    <Avatar.Group>
                      {pending.slice(0, 4).map((r) => (
                        <Avatar key={r.user_id} radius="xl" size="sm">{initials(r.display_name)}</Avatar>
                      ))}
                      {pending.length > 4 && <Avatar radius="xl" size="sm">+{pending.length - 4}</Avatar>}
                    </Avatar.Group>
                    <Text size="xs" c="dimmed">awaiting acknowledgement</Text>
                  </Group>
                )}
              </Group>
              {matrix.isLoading ? (
                <Text c="dimmed">Loading the matrix…</Text>
              ) : matrix.isError ? (
                <Text size="sm" c="dimmed">
                  {matrix.forbidden ? "You don't have access to the named matrix." : "Could not load the matrix."}
                </Text>
              ) : (matrix.data ?? []).length === 0 ? (
                <Text size="sm" c="dimmed">No one is distributed for acknowledgement yet.</Text>
              ) : (
                <Table aria-label="Acknowledgement matrix" striped>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th scope="col">Person</Table.Th>
                      <Table.Th scope="col">Status</Table.Th>
                      <Table.Th scope="col">Acknowledged rev</Table.Th>
                      <Table.Th scope="col">Due</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {(matrix.data ?? []).map((r) => (
                      <Table.Tr key={r.user_id}>
                        <Table.Td>{r.display_name ?? r.user_id}</Table.Td>
                        <Table.Td><Badge color={STATUS_COLOR[r.status]} variant="light">{r.status}</Badge></Table.Td>
                        <Table.Td>{r.acknowledged_revision_label ?? "—"}</Table.Td>
                        <Table.Td>{r.due_at ? r.due_at.slice(0, 10) : "—"}</Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              )}
            </Stack>
          </Card>
          <DistributionEditor documentId={documentId} payload={dist.data!} />
        </>
      )}
    </Stack>
  );
}
```

  Because Task 5 builds `DistributionEditor`, create a minimal stub now so this file compiles:
  add `apps/web/src/features/document/DistributionEditor.tsx` with
  `export function DistributionEditor(_: { documentId: string; payload: import("../../lib/types").DistributionPayload }) { return null; }`
  (Task 5 replaces the stub with the real implementation + its tests).

- [ ] **Step 4: Run it — expect PASS.** Run: `cd apps/web && npx vitest run src/features/document/AcknowledgementsTab.test.tsx`

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/document/AcknowledgementsTab.tsx apps/web/src/features/document/AcknowledgementsTab.test.tsx apps/web/src/features/document/DistributionEditor.tsx
git commit -m "feat(s-ack-2): AcknowledgementsTab — reader coverage + distributor matrix (calm-403, no Remind)"
```

---

## Task 5: `DistributionEditor` (document.distribute-gated)

**Files:**
- Modify: `apps/web/src/features/document/DistributionEditor.tsx` (replace the Task-4 stub)
- Test: `apps/web/src/features/document/DistributionEditor.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, test, vi } from "vitest";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { distributionFixture } from "../../test/msw/handlers";
import { DistributionEditor } from "./DistributionEditor";

const DOC = "11111111-1111-1111-1111-111111111111";

describe("DistributionEditor", () => {
  test("toggling the ack-required flag POSTs acknowledgement_required", async () => {
    let body: unknown = null;
    server.use(
      http.post("/api/v1/documents/:id/distribution", async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(distributionFixture);
      }),
    );
    renderWithProviders(<DistributionEditor documentId={DOC} payload={distributionFixture} />);
    await userEvent.click(screen.getByLabelText(/require acknowledgement/i));
    await waitFor(() => expect(body).toEqual({ acknowledgement_required: false }));
  });

  test("lists existing entries and deletes one", async () => {
    let deleted: string | null = null;
    server.use(
      http.delete("/api/v1/documents/:id/distribution/:entryId", ({ params }) => {
        deleted = String(params.entryId);
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderWithProviders(<DistributionEditor documentId={DOC} payload={distributionFixture} />);
    const row = screen.getByText("Mara Quality").closest("tr")!;
    await userEvent.click(within(row).getByRole("button", { name: /remove/i }));
    await waitFor(() => expect(deleted).toBe("de000001-0001-0001-0001-000000000001"));
  });

  test("adds a user entry → POSTs add_entries with the user target", async () => {
    let body: { add_entries?: { target_type: string; target_id: string }[] } | null = null;
    server.use(
      http.post("/api/v1/documents/:id/distribution", async ({ request }) => {
        body = (await request.json()) as typeof body;
        return HttpResponse.json(distributionFixture);
      }),
    );
    renderWithProviders(<DistributionEditor documentId={DOC} payload={distributionFixture} />);
    // pick a user from the directory select (Diego), then Add.
    await userEvent.click(screen.getByLabelText(/add recipient/i));
    await userEvent.click(await screen.findByText("Diego Owner"));
    await userEvent.click(screen.getByRole("button", { name: /^add$/i }));
    await waitFor(() =>
      expect(body?.add_entries?.[0]).toMatchObject({
        target_type: "user",
        target_id: "bbbb2222-2222-2222-2222-222222222222",
      }),
    );
  });

  test("a 422 target_kind_deferred never happens — process/folder are not offered", async () => {
    renderWithProviders(<DistributionEditor documentId={DOC} payload={distributionFixture} />);
    await userEvent.click(screen.getByLabelText(/add recipient/i));
    // the target-type control offers only user + role.
    expect(screen.getByRole("radio", { name: /user/i })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /role/i })).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: /process/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: /folder/i })).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run it — expect FAIL.** Run: `cd apps/web && npx vitest run src/features/document/DistributionEditor.test.tsx`

- [ ] **Step 3: Implement** (uses `useUserDirectory` from `app/shell` for user targets and a `useRoles`
  query for org_role targets; both reachable by a `document.distribute` holder. Add a tiny `useRoles` in
  this file or `ackHooks.ts` — see below.)

  First add `useRoles` to `apps/web/src/features/document/ackHooks.ts`:

```ts
import type { RoleSummary } from "../../lib/types";
// ... existing imports/hooks ...
export function useRoles() {
  const api = useApi();
  const query = useQuery({ queryKey: ["roles"], queryFn: () => api.get<RoleSummary[]>("/api/v1/roles"), retry: false });
  const forbidden = query.error instanceof ApiError && query.error.status === 403;
  return { ...query, forbidden };
}
```

  Then `DistributionEditor.tsx`:

```tsx
import { Alert, Button, Card, Group, SegmentedControl, Select, Stack, Switch, Table, Text } from "@mantine/core";
import { useMemo, useState } from "react";
import { ApiError } from "../../lib/api";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import type { DistributionEntry, DistributionPayload } from "../../lib/types";
import { useDeleteDistributionEntry, useRoles, useUpdateDistribution } from "./ackHooks";

// S-ack-2: the document.distribute-gated issuance editor — the doc-level ack flag, an add-recipient form
// (user | org_role only; process/folder are R43-deferred and never offered), and the entries list with a
// per-entry remove. No PATCH on an entry — change is delete + re-add. No Remind (R43).
export function DistributionEditor({ documentId, payload }: { documentId: string; payload: DistributionPayload }) {
  const update = useUpdateDistribution(documentId);
  const del = useDeleteDistributionEntry(documentId);
  const directory = useUserDirectory();
  const roles = useRoles();
  const [adding, setAdding] = useState(false);
  const [kind, setKind] = useState<"user" | "org_role">("user");
  const [targetId, setTargetId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const nameFor = useMemo(() => {
    const u = new Map((directory.data ?? []).map((d) => [d.id, d.display_name ?? d.id] as const));
    const r = new Map((roles.data ?? []).map((x) => [x.id, x.name] as const));
    return (e: DistributionEntry) =>
      e.target_type === "user" ? u.get(e.target_id) ?? e.target_id : r.get(e.target_id) ?? e.target_id;
  }, [directory.data, roles.data]);

  const options =
    kind === "user"
      ? (directory.data ?? []).map((d) => ({ value: d.id, label: d.display_name ?? d.id }))
      : (roles.data ?? []).map((r) => ({ value: r.id, label: r.name }));

  async function add() {
    setError(null);
    if (!targetId) return;
    try {
      await update.mutateAsync({ add_entries: [{ target_type: kind, target_id: targetId }] });
      setAdding(false);
      setTargetId(null);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setError("That recipient is already on the list.");
      else if (e instanceof ApiError && e.status === 404) setError("That recipient no longer exists.");
      else setError(e instanceof Error ? e.message : "Could not add the recipient.");
    }
  }

  return (
    <Card withBorder>
      <Stack gap="md">
        <Text fw={600}>Distribution</Text>
        <Switch
          label="Require acknowledgement of this document"
          checked={payload.acknowledgement_required}
          onChange={(ev) => update.mutate({ acknowledgement_required: ev.currentTarget.checked })}
        />
        {payload.entries.length === 0 ? (
          <Text size="sm" c="dimmed">No recipients yet.</Text>
        ) : (
          <Table aria-label="Distribution entries">
            <Table.Thead>
              <Table.Tr>
                <Table.Th scope="col">Recipient</Table.Th>
                <Table.Th scope="col">Kind</Table.Th>
                <Table.Th scope="col">Ack required</Table.Th>
                <Table.Th scope="col" />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {payload.entries.map((e) => (
                <Table.Tr key={e.id}>
                  <Table.Td>{nameFor(e)}</Table.Td>
                  <Table.Td>{e.target_type === "org_role" ? "Role" : "User"}</Table.Td>
                  <Table.Td>{e.ack_required ? "Yes" : "No"}</Table.Td>
                  <Table.Td>
                    <Button variant="subtle" color="red" size="xs" aria-label={`Remove ${nameFor(e)}`} onClick={() => del.mutate(e.id)} loading={del.isPending}>
                      Remove
                    </Button>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
        {error && <Alert color="red" withCloseButton onClose={() => setError(null)}>{error}</Alert>}
        {!adding ? (
          <Group>
            <Button variant="light" size="xs" aria-label="Add recipient" onClick={() => setAdding(true)}>
              Add recipient
            </Button>
          </Group>
        ) : (
          <Stack gap="sm">
            <SegmentedControl
              value={kind}
              onChange={(v) => { setKind(v as "user" | "org_role"); setTargetId(null); }}
              data={[{ label: "User", value: "user" }, { label: "Role", value: "org_role" }]}
              aria-label="Recipient kind"
            />
            <Select
              label="Recipient"
              placeholder={kind === "user" ? "Pick a person" : "Pick a role"}
              data={options}
              value={targetId}
              onChange={setTargetId}
              searchable
            />
            <Group>
              <Button size="xs" onClick={() => void add()} loading={update.isPending} disabled={!targetId}>Add</Button>
              <Button size="xs" variant="subtle" onClick={() => { setAdding(false); setTargetId(null); }}>Cancel</Button>
            </Group>
          </Stack>
        )}
      </Stack>
    </Card>
  );
}
```

  Note: Mantine `SegmentedControl` renders radio inputs with accessible names "User"/"Role" — the test's
  `getByRole("radio", {name:/user/i})` matches. The `Select` renders options as clickable items
  (`screen.findByText("Diego Owner")`); the global `scrollIntoView` stub in `test/setup.ts` keeps it from
  throwing on open.

- [ ] **Step 4: Run it — expect PASS.** Run: `cd apps/web && npx vitest run src/features/document/DistributionEditor.test.tsx`

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/document/DistributionEditor.tsx apps/web/src/features/document/DistributionEditor.test.tsx apps/web/src/features/document/ackHooks.ts
git commit -m "feat(s-ack-2): DistributionEditor — flag toggle + user/org_role add + entries list"
```

---

## Task 6: DocumentDetailPage — tabs refactor + Acknowledged tile + Acks tab

**Files:**
- Modify: `apps/web/src/features/document/DocumentDetailPage.tsx`
- Modify: `apps/web/src/features/document/DocumentDetailPage.test.tsx` (adapt to tab-gated sections)

- [ ] **Step 1: Write the failing test additions** (append to `DocumentDetailPage.test.tsx`)

```tsx
// S-ack-2: the Acknowledged tile + the Acks tab.
test("renders the Acknowledged tile from the distribution coverage", async () => {
  renderWithProviders(<DocumentDetailPage />, { route: "/documents/11111111-1111-1111-1111-111111111111" });
  // the metric tile (persistent, above the tabs) shows the ratio.
  expect(await screen.findByText("Acknowledged")).toBeInTheDocument();
  expect(await screen.findByText("41 / 47")).toBeInTheDocument();
});

test("the Acks tab shows coverage; deep-link via ?tab=acks", async () => {
  renderWithProviders(<DocumentDetailPage />, {
    route: "/documents/11111111-1111-1111-1111-111111111111?tab=acks",
  });
  // coverage ring is in the panel too (87% appears).
  expect(await screen.findByText("87%")).toBeInTheDocument();
});

test("clicking the Acks tab activates it", async () => {
  renderWithProviders(<DocumentDetailPage />, { route: "/documents/11111111-1111-1111-1111-111111111111" });
  await screen.findByText("Acknowledged"); // page loaded
  await userEvent.click(screen.getByRole("tab", { name: /acknowledgements/i }));
  expect(await screen.findByText(/Read-and-understood coverage/)).toBeInTheDocument();
});
```

  You will also need `import userEvent from "@testing-library/user-event";` at the top if absent, and a
  route param. **Update the existing tests** that asserted Approvals/Where-used/History render together:
  those sections are now behind tabs (default tab = Overview). For each such assertion, first
  `await userEvent.click(screen.getByRole("tab", { name: /<tab>/i }))` before asserting the section's
  content. Read the current test file and adapt minimally — the section components are unchanged; only
  their visibility is now tab-gated.

- [ ] **Step 2: Run it — expect FAIL** (no Acks tab / tile yet; some existing tests now fail on tab-gating)

Run: `cd apps/web && npx vitest run src/features/document/DocumentDetailPage.test.tsx`

- [ ] **Step 3: Refactor `DocumentDetailPage.tsx`** — keep `ArtifactHeader` + `AuthorActions` + the tiles
  row persistent; move the section cards into a `Tabs`, add the Acknowledged tile + the Acks tab.

```tsx
import { Alert, Anchor, Card, SimpleGrid, Skeleton, Stack, Tabs, Text } from "@mantine/core";
import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useDocumentTypes } from "../../app/shell/useDocumentTypes";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import { ApiError } from "../../lib/api";
import { AuthorActions } from "../authoring/AuthorActions";
import { AckCoverageRing } from "./AckCoverageRing";
import { AcknowledgementsTab } from "./AcknowledgementsTab";
import { ApprovalsTab } from "./ApprovalsTab";
import { ArtifactHeader } from "./ArtifactHeader";
import { ControlMetadata } from "./ControlMetadata";
import { HistoryTab } from "./HistoryTab";
import { RenditionCard } from "./RenditionCard";
import { ReviewPeriodModal } from "./ReviewPeriodModal";
import { ReviewStateBadge } from "./ReviewStateBadge";
import { VersionCompare } from "./VersionCompare";
import { WhereUsedTab } from "./WhereUsedTab";
import { useDistribution } from "./ackHooks";
import { daysUntil } from "./reviewDates";
import { useDocument } from "./useDocument";
import { useDocumentVersions } from "./useDocumentVersions";

function Tile({ label, value, sub }: { label: string; value: React.ReactNode; sub?: React.ReactNode }) {
  return (
    <Card withBorder padding="sm">
      <Text size="xs" c="dimmed" tt="uppercase" fw={700}>{label}</Text>
      <Text size="xl" fw={700}>{value}</Text>
      {sub && <Text size="xs" c="dimmed" component="div">{sub}</Text>}
    </Card>
  );
}

export function DocumentDetailPage() {
  const { id = null } = useParams();
  const [sp, setSp] = useSearchParams();
  const tab = sp.get("tab") ?? "overview";
  const setTab = (v: string | null) =>
    setSp((prev) => { prev.set("tab", v ?? "overview"); return prev; }, { replace: true });

  const { data: doc, isLoading, isError, error } = useDocument(id, { enabled: id !== null });
  const { data: types } = useDocumentTypes();
  const { data: directory } = useUserDirectory();
  const { data: versions } = useDocumentVersions(id, id !== null);
  const dist = useDistribution(id ?? "");
  const [reviewEditOpen, setReviewEditOpen] = useState(false);

  if (isLoading && !doc) {
    return (
      <Stack gap="md" aria-label="Loading document">
        <Skeleton height={40} width="60%" />
        <Skeleton height={20} width="40%" />
        <SimpleGrid cols={{ base: 1, sm: 2, md: 5 }}>
          {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} height={72} />)}
        </SimpleGrid>
        <Skeleton height={240} />
      </Stack>
    );
  }
  if (isError || !doc) {
    const status = error instanceof ApiError ? error.status : 0;
    const msg = status === 403 ? "You don't have access to this document." : status === 404 ? "This document does not exist." : "Could not load this document.";
    return (
      <Alert color={status === 403 ? "yellow" : "red"} title="Document unavailable">
        <Stack gap="xs" align="flex-start">
          <Text size="sm">{msg}</Text>
          <Anchor component={Link} to="/library">← Back to the Library</Anchor>
        </Stack>
      </Alert>
    );
  }

  const typeName = types?.find((t) => t.id === doc.document_type_id)?.name;
  const ownerName = directory?.find((u) => u.id === doc.owner_user_id)?.display_name ?? undefined;
  const versionList = versions ?? [];
  const governingRev = versionList.find((v) => v.id === doc.current_effective_version_id)?.revision_label;
  const effectiveDate = doc.effective_from ? doc.effective_from.slice(0, 10) : null;
  const reviewDays = doc.next_review_due ? daysUntil(doc.next_review_due) : null;
  const cov = dist.data?.coverage ?? null;

  return (
    <Stack gap="lg">
      <ArtifactHeader doc={doc} typeName={typeName} ownerName={ownerName} />
      <AuthorActions doc={doc} />

      <SimpleGrid cols={{ base: 1, sm: 2, md: 5 }}>
        <Tile label="Governing revision" value={governingRev ?? (doc.current_effective_version_id ? "Effective" : "—")} sub={effectiveDate ? `Effective ${effectiveDate}` : "Not yet effective"} />
        <Tile label="Mapped clauses" value={(doc.clause_refs ?? []).join(", ") || "—"} sub="ISO 9001:2015" />
        <Tile label="Versions" value={versionList.length || "—"} sub={versionList.length ? "retained · newest first" : "history not in scope"} />
        <Tile label="Next review" value={reviewDays === null ? "—" : reviewDays >= 0 ? `${reviewDays} days` : `${-reviewDays} days overdue`} sub={doc.next_review_due ? (<>{doc.next_review_due} <ReviewStateBadge state={doc.review_state} /></>) : "No scheduled review"} />
        <Tile
          label="Acknowledged"
          value={cov === null ? "—" : cov.required === 0 ? "—" : `${cov.acknowledged} / ${cov.required}`}
          sub={cov === null ? "Not yet effective" : cov.required === 0 ? "Not distributed" : `${cov.pending} pending`}
        />
      </SimpleGrid>

      <Tabs value={tab} onChange={setTab} keepMounted={false}>
        <Tabs.List>
          <Tabs.Tab value="overview">Overview</Tabs.Tab>
          <Tabs.Tab value="history">History</Tabs.Tab>
          <Tabs.Tab value="approvals">Approvals</Tabs.Tab>
          <Tabs.Tab value="where-used">Where-used</Tabs.Tab>
          <Tabs.Tab value="acks">Acknowledgements</Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="overview" pt="md">
          <Stack gap="lg">
            <RenditionCard doc={doc} />
            <Card withBorder>
              <Stack gap="sm">
                <Text fw={600}>Control metadata</Text>
                <ControlMetadata doc={doc} typeName={typeName} ownerName={ownerName} onEditReviewPeriod={doc.capabilities?.manage_metadata ? () => setReviewEditOpen(true) : undefined} />
              </Stack>
            </Card>
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="history" pt="md">
          <Card withBorder>
            <Stack gap="md">
              <Text fw={600}>Version history</Text>
              <HistoryTab documentId={id} active={tab === "history"} />
              <VersionCompare documentId={doc.id} versions={versionList} />
            </Stack>
          </Card>
        </Tabs.Panel>

        <Tabs.Panel value="approvals" pt="md">
          <Card withBorder>
            <Stack gap="sm">
              <Text fw={600}>Approvals</Text>
              <ApprovalsTab doc={doc} />
            </Stack>
          </Card>
        </Tabs.Panel>

        <Tabs.Panel value="where-used" pt="md">
          <Card withBorder>
            <Stack gap="sm">
              <Text fw={600}>Where-used</Text>
              <WhereUsedTab documentId={id} active={tab === "where-used"} />
            </Stack>
          </Card>
        </Tabs.Panel>

        <Tabs.Panel value="acks" pt="md">
          <AcknowledgementsTab documentId={doc.id} active={tab === "acks"} />
        </Tabs.Panel>
      </Tabs>

      {reviewEditOpen && <ReviewPeriodModal doc={doc} opened onClose={() => setReviewEditOpen(false)} />}
    </Stack>
  );
}
```

  Note `keepMounted={false}` so inactive panels unmount (the `active` prop also gates fetches). The
  distribution GET fires on page load (for the tile) regardless of tab — that's intended (document.read,
  cheap, drives the persistent tile).

- [ ] **Step 4: Run it — expect PASS** (new + adapted existing tests)

Run: `cd apps/web && npx vitest run src/features/document/DocumentDetailPage.test.tsx`

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/document/DocumentDetailPage.tsx apps/web/src/features/document/DocumentDetailPage.test.tsx
git commit -m "feat(s-ack-2): doc page → Mantine Tabs + Acknowledged tile + Acks tab"
```

---

## Task 7: Review ack mutation hooks (`features/review/ackHooks.ts`)

**Files:**
- Create: `apps/web/src/features/review/ackHooks.ts`
- Test: `apps/web/src/features/review/ackHooks.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { describe, expect, test } from "vitest";
import { AuthContext } from "../../lib/auth";
import { server } from "../../test/msw/server";
import { TEST_AUTH } from "../../test/render";
import { theme } from "../../theme/mantine";
import { useAcknowledgeTask, useBulkAcknowledge } from "./ackHooks";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <MantineProvider theme={theme}>
      <QueryClientProvider client={client}>
        <AuthContext.Provider value={TEST_AUTH}>{children}</AuthContext.Provider>
      </QueryClientProvider>
    </MantineProvider>
  );
}

describe("review ack hooks", () => {
  test("useAcknowledgeTask POSTs outcome=acknowledge with an Idempotency-Key", async () => {
    let outcome: string | null = null;
    let hadKey = false;
    server.use(
      http.post("/api/v1/tasks/:id/decision", async ({ request }) => {
        outcome = ((await request.json()) as { outcome: string }).outcome;
        hadKey = request.headers.has("Idempotency-Key");
        return HttpResponse.json({ document_id: "d", document_version_id: null, acknowledgement_id: "a", replayed: false });
      }),
    );
    const { result } = renderHook(() => useAcknowledgeTask(), { wrapper });
    await result.current.mutateAsync({ taskId: "tkak1111-1111-1111-1111-111111111111", documentId: "11111111-1111-1111-1111-111111111111" });
    expect(outcome).toBe("acknowledge");
    expect(hadKey).toBe(true);
  });

  test("useBulkAcknowledge reports per-task success/failure (allSettled)", async () => {
    server.use(
      http.post("/api/v1/tasks/:id/decision", ({ params }) => {
        if (params.id === "bad") return HttpResponse.json({ code: "ack_superseded", title: "superseded" }, { status: 409 });
        return HttpResponse.json({ document_id: "d", acknowledgement_id: "a", replayed: false });
      }),
    );
    const { result } = renderHook(() => useBulkAcknowledge(), { wrapper });
    const out = await result.current.run(["ok1", "ok2", "bad"]);
    expect(out.ok).toEqual(["ok1", "ok2"]);
    expect(out.failed).toEqual([{ taskId: "bad", code: "ack_superseded" }]);
  });
});
```

- [ ] **Step 2: Run it — expect FAIL.** Run: `cd apps/web && npx vitest run src/features/review/ackHooks.test.tsx`

- [ ] **Step 3: Implement**

```ts
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ApiError, useApi } from "../../lib/api";
import type { AckDecisionResult } from "../../lib/types";

// S-ack-2: the DOC_ACK attestation mutations. Separate from useDecideTask (the attestation is
// acknowledge-only, no signature, no DecisionSubjectType) so the shared decision path stays untouched.

function newKey(): string {
  return crypto.randomUUID();
}

export interface AckInput {
  taskId: string;
  documentId?: string; // for the per-doc cache invalidation (known on the single-task page)
}

export function useAcknowledgeTask() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ taskId }: AckInput) =>
      api.send<AckDecisionResult>("POST", `/api/v1/tasks/${taskId}/decision`, { outcome: "acknowledge" }, { "Idempotency-Key": newKey() }),
    onSuccess: (_d, { taskId, documentId }) => {
      void qc.invalidateQueries({ queryKey: ["task", taskId] });
      void qc.invalidateQueries({ queryKey: ["tasks"] });
      void qc.invalidateQueries({ queryKey: ["ack-count"] });
      void qc.invalidateQueries({ queryKey: ["documents"] });
      if (documentId) void qc.invalidateQueries({ queryKey: ["document", documentId] });
    },
  });
}

export interface BulkResult {
  ok: string[];
  failed: { taskId: string; code: string }[];
}

// The doc 10 §8.2 sanctioned bulk action — loop the per-task POST (one Idempotency-Key each), report
// per-task. allSettled so a single lapsed/superseded obligation never aborts the rest.
export function useBulkAcknowledge() {
  const api = useApi();
  const qc = useQueryClient();
  async function run(taskIds: string[]): Promise<BulkResult> {
    const settled = await Promise.allSettled(
      taskIds.map((taskId) =>
        api
          .send<AckDecisionResult>("POST", `/api/v1/tasks/${taskId}/decision`, { outcome: "acknowledge" }, { "Idempotency-Key": newKey() })
          .then(() => taskId),
      ),
    );
    const ok: string[] = [];
    const failed: { taskId: string; code: string }[] = [];
    settled.forEach((r, i) => {
      const taskId = taskIds[i]!;
      if (r.status === "fulfilled") ok.push(taskId);
      else failed.push({ taskId, code: r.reason instanceof ApiError ? r.reason.code : "error" });
    });
    void qc.invalidateQueries({ queryKey: ["tasks"] });
    void qc.invalidateQueries({ queryKey: ["ack-count"] });
    void qc.invalidateQueries({ queryKey: ["documents"] });
    return { ok, failed };
  }
  return { run };
}
```

- [ ] **Step 4: Run it — expect PASS.** Run: `cd apps/web && npx vitest run src/features/review/ackHooks.test.tsx`

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/review/ackHooks.ts apps/web/src/features/review/ackHooks.test.tsx
git commit -m "feat(s-ack-2): useAcknowledgeTask + useBulkAcknowledge (allSettled, per-task report)"
```

---

## Task 8: `DocAckContext` (best-effort doc context for the task leg)

**Files:**
- Create: `apps/web/src/features/review/DocAckContext.tsx`
- Test: `apps/web/src/features/review/DocAckContext.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, test } from "vitest";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { DocAckContext } from "./DocAckContext";

const DOC = "11111111-1111-1111-1111-111111111111";

describe("DocAckContext", () => {
  test("shows the document identifier + title", async () => {
    renderWithProviders(<DocAckContext documentId={DOC} />);
    expect(await screen.findByText("SOP-PUR-014")).toBeInTheDocument();
    expect(screen.getByText("Supplier Selection & Evaluation")).toBeInTheDocument();
  });

  test("a 403 degrades calmly (the card still renders elsewhere)", async () => {
    server.use(
      http.get("/api/v1/documents/:id", () => HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 })),
    );
    renderWithProviders(<DocAckContext documentId={DOC} />);
    expect(await screen.findByText(/Document details aren't visible to you/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run it — expect FAIL.** Run: `cd apps/web && npx vitest run src/features/review/DocAckContext.test.tsx`

- [ ] **Step 3: Implement** (mirror `PeriodicReviewContext` — best-effort `useDocument` with `retry:false`)

```tsx
import { Alert, Anchor, Card, Stack, Table, Text } from "@mantine/core";
import { Link } from "react-router-dom";
import { ApiError } from "../../lib/api";
import { useDocument } from "../document/useDocument";

// S-ack-2: the DOC_ACK task's left column — the document to read, loaded BEST-EFFORT via document.read.
// A 403 degrades calmly and never blocks the attestation card (the obligation stands regardless of read).
export function DocAckContext({ documentId }: { documentId: string }) {
  const { data: doc, isLoading, isError, error } = useDocument(documentId, { enabled: true, retry: false });
  if (isLoading && !doc) return <Text c="dimmed">Loading the document to acknowledge…</Text>;
  if (isError || !doc) {
    const status = error instanceof ApiError ? error.status : 0;
    return (
      <Alert color="yellow" title="Document details aren't visible to you">
        <Text size="sm">
          {status === 403
            ? "You can acknowledge this document, but reading it isn't granted to you here."
            : "Could not load the document to acknowledge."}
        </Text>
      </Alert>
    );
  }
  const governingRev = doc.current_effective_version_id ? "the current Effective revision" : "—";
  return (
    <Card withBorder>
      <Stack gap="sm">
        <div>
          <Text ff="monospace" size="sm">{doc.identifier}</Text>
          <Text fw={600}>{doc.title}</Text>
        </div>
        <Table withRowBorders={false} aria-label="Document context">
          <Table.Tbody>
            <Table.Tr>
              <Table.Td><Text size="sm" c="dimmed">State</Text></Table.Td>
              <Table.Td>{doc.current_state}</Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Td><Text size="sm" c="dimmed">Governing</Text></Table.Td>
              <Table.Td>{governingRev}</Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Td><Text size="sm" c="dimmed">Effective</Text></Table.Td>
              <Table.Td>{doc.effective_from ? doc.effective_from.slice(0, 10) : "—"}</Table.Td>
            </Table.Tr>
          </Table.Tbody>
        </Table>
        <Anchor component={Link} to={`/documents/${doc.id}`} size="sm">Open the document page →</Anchor>
      </Stack>
    </Card>
  );
}
```

- [ ] **Step 4: Run it — expect PASS.** Run: `cd apps/web && npx vitest run src/features/review/DocAckContext.test.tsx`

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/review/DocAckContext.tsx apps/web/src/features/review/DocAckContext.test.tsx
git commit -m "feat(s-ack-2): DocAckContext — best-effort doc context, calm-403"
```

---

## Task 9: `AttestationCard` (the one-click attestation)

**Files:**
- Create: `apps/web/src/features/review/AttestationCard.tsx`
- Test: `apps/web/src/features/review/AttestationCard.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, test } from "vitest";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";

import { AttestationCard } from "./AttestationCard";

const TASK = "tkak1111-1111-1111-1111-111111111111";
const DOC = "11111111-1111-1111-1111-111111111111";

describe("AttestationCard", () => {
  test("one click acknowledges and navigates to /tasks", async () => {
    let outcome: string | null = null;
    server.use(
      http.post("/api/v1/tasks/:id/decision", async ({ request }) => {
        outcome = ((await request.json()) as { outcome: string }).outcome;
        return HttpResponse.json({ document_id: DOC, acknowledgement_id: "a", replayed: false });
      }),
    );
    renderWithProviders(<AttestationCard taskId={TASK} documentId={DOC} />, { route: "/tasks/" + TASK });
    await userEvent.click(screen.getByRole("button", { name: /i have read & understood/i }));
    await waitFor(() => expect(outcome).toBe("acknowledge"));
  });

  test("a 409 ack_superseded shows the supersede copy, not a crash", async () => {
    server.use(
      http.post("/api/v1/tasks/:id/decision", () => HttpResponse.json({ code: "ack_superseded", title: "x" }, { status: 409 })),
    );
    renderWithProviders(<AttestationCard taskId={TASK} documentId={DOC} />);
    await userEvent.click(screen.getByRole("button", { name: /i have read & understood/i }));
    expect(await screen.findByText(/newer major revision was released/i)).toBeInTheDocument();
  });

  test("a 409 ack_obligation_lapsed shows the lapsed copy", async () => {
    server.use(
      http.post("/api/v1/tasks/:id/decision", () => HttpResponse.json({ code: "ack_obligation_lapsed", title: "x" }, { status: 409 })),
    );
    renderWithProviders(<AttestationCard taskId={TASK} documentId={DOC} />);
    await userEvent.click(screen.getByRole("button", { name: /i have read & understood/i }));
    expect(await screen.findByText(/no longer requires your acknowledgement/i)).toBeInTheDocument();
  });

  test("no signature checkbox and no outcome radio (acknowledge-only, R43)", () => {
    renderWithProviders(<AttestationCard taskId={TASK} documentId={DOC} />);
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    expect(screen.queryByText(/signing as/i)).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run it — expect FAIL.** Run: `cd apps/web && npx vitest run src/features/review/AttestationCard.test.tsx`

- [ ] **Step 3: Implement**

```tsx
import { Alert, Button, Card, Group, Stack, Text } from "@mantine/core";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../../lib/api";
import { useDocument } from "../document/useDocument";
import { useAcknowledgeTask } from "./ackHooks";

const CODE_COPY: Record<string, string> = {
  ack_obligation_lapsed: "This document no longer requires your acknowledgement — it may be under revision or obsoleted.",
  ack_superseded: "A newer major revision was released — acknowledge the current version instead.",
  conflict: "You've already acknowledged this.",
};

// S-ack-2: the DOC_ACK attestation. Acknowledge-only, NO signature (R43 — an ack is append-only
// evidence, never a signature_event), so this is NOT a DecisionCard: prominent copy + one button.
export function AttestationCard({ taskId, documentId }: { taskId: string; documentId: string }) {
  const ack = useAcknowledgeTask();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  // best-effort label for the copy (the obligation stands regardless of read).
  const { data: doc } = useDocument(documentId, { enabled: true, retry: false });
  const label = doc ? `${doc.identifier}${doc.current_effective_version_id ? "" : ""}` : "this document";

  async function submit() {
    setError(null);
    try {
      await ack.mutateAsync({ taskId, documentId });
      navigate("/tasks");
    } catch (e) {
      if (e instanceof ApiError) setError(CODE_COPY[e.code] ?? e.message);
      else setError("Something went wrong. Please retry.");
    }
  }

  return (
    <Card withBorder>
      <Stack gap="md">
        <Text fw={600}>I have read &amp; understood</Text>
        <Text size="sm">
          By acknowledging, you confirm you have read and understood <b>{label}</b>.
        </Text>
        {error && <Alert color="red" withCloseButton onClose={() => setError(null)}>{error}</Alert>}
        <Group justify="flex-end">
          <Button variant="subtle" onClick={() => navigate("/tasks")}>Cancel</Button>
          <Button onClick={() => void submit()} loading={ack.isPending}>I have read &amp; understood</Button>
        </Group>
      </Stack>
    </Card>
  );
}
```

- [ ] **Step 4: Run it — expect PASS.** Run: `cd apps/web && npx vitest run src/features/review/AttestationCard.test.tsx`

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/review/AttestationCard.tsx apps/web/src/features/review/AttestationCard.test.tsx
git commit -m "feat(s-ack-2): AttestationCard — one-click read & understood, 409-code copy"
```

---

## Task 10: `ReviewApprovePage` — the 4th DOC_ACK branch

**Files:**
- Modify: `apps/web/src/features/review/ReviewApprovePage.tsx`
- Test: `apps/web/src/features/review/ReviewApprovePage.test.tsx` (add a DOC_ACK case; create the file if absent)

- [ ] **Step 1: Write the failing test** (append, or new file)

```tsx
import { screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { Route, Routes } from "react-router-dom";
import { renderWithProviders } from "../../test/render";
import { ReviewApprovePage } from "./ReviewApprovePage";

function renderAt(taskId: string) {
  return renderWithProviders(
    <Routes><Route path="/tasks/:id" element={<ReviewApprovePage />} /></Routes>,
    { route: `/tasks/${taskId}` },
  );
}

describe("ReviewApprovePage DOC_ACK branch", () => {
  test("a DOC_ACK task renders the attestation card + the doc context, no signature", async () => {
    renderAt("tkak1111-1111-1111-1111-111111111111");
    expect(await screen.findByText("Document acknowledgement")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /i have read & understood/i })).toBeInTheDocument();
    expect(screen.queryByRole("radio")).not.toBeInTheDocument(); // not a DecisionCard
    // doc context (best-effort) shows the identifier
    expect(await screen.findByText("SOP-PUR-014")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run it — expect FAIL.** Run: `cd apps/web && npx vitest run src/features/review/ReviewApprovePage.test.tsx`

- [ ] **Step 3: Edit `ReviewApprovePage.tsx`** — add the imports + the `isDocAck` branch.

  Add imports at top:
```tsx
import { AttestationCard } from "./AttestationCard";
import { DocAckContext } from "./DocAckContext";
```

  Change the subject-type guards (after `const isPeriodic = ...`):
```tsx
  const isDocAck = task?.subject_type === "DOC_ACK";
  const { data: instance } = useWorkflowInstance(!isCapa && !isPeriodic && !isDocAck && task ? task.instance_id : null);
  const docId = !isCapa && !isPeriodic && !isDocAck ? (instance?.subject_id ?? null) : null;
```

  Add the branch BEFORE the final DOCUMENT `return` (after the `isPeriodic` block):
```tsx
  if (isDocAck) {
    return (
      <Stack gap="lg">
        <Title order={2}>Document acknowledgement</Title>
        <Grid gutter="lg" align="flex-start">
          <Grid.Col span={{ base: 12, md: 7 }}>
            <DocAckContext documentId={task.subject_id!} />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 5 }}>
            {decidable ? (
              <AttestationCard taskId={task.id} documentId={task.subject_id!} />
            ) : (
              decidedAlert
            )}
          </Grid.Col>
        </Grid>
      </Stack>
    );
  }
```

- [ ] **Step 4: Run it — expect PASS** (and the existing ReviewApprovePage tests still pass — the
  DOCUMENT/CAPA/PERIODIC branches are unchanged).

Run: `cd apps/web && npx vitest run src/features/review/ReviewApprovePage.test.tsx`

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/review/ReviewApprovePage.tsx apps/web/src/features/review/ReviewApprovePage.test.tsx
git commit -m "feat(s-ack-2): ReviewApprovePage — 4th DOC_ACK branch (attestation + context)"
```

---

## Task 11: `AckInbox` (the bulk-ack inbox)

**Files:**
- Create: `apps/web/src/features/review/AckInbox.tsx`
- Test: `apps/web/src/features/review/AckInbox.test.tsx`

**Data note:** `GET /tasks?type=DOC_ACK` rows DO NOT carry `subject_id` (detail-only). So each row fetches
its task detail (`useTask`) to learn the document id, then `useDocument` (best-effort) for the name.

- [ ] **Step 1: Write the failing test**

```tsx
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, test } from "vitest";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { AckInbox } from "./AckInbox";

describe("AckInbox", () => {
  test("lists my pending DOC_ACK tasks with the document name (via detail → doc)", async () => {
    renderWithProviders(<AckInbox />);
    // the row resolves the doc name best-effort (task detail → useDocument).
    expect(await screen.findByText("SOP-PUR-014")).toBeInTheDocument();
  });

  test("select-all + Acknowledge selected loops the POST", async () => {
    let posts = 0;
    server.use(
      http.post("/api/v1/tasks/:id/decision", () => {
        posts += 1;
        return HttpResponse.json({ document_id: "d", acknowledgement_id: "a", replayed: false });
      }),
    );
    renderWithProviders(<AckInbox />);
    await screen.findByText("SOP-PUR-014");
    await userEvent.click(screen.getByLabelText(/select all/i));
    await userEvent.click(screen.getByRole("button", { name: /acknowledge 1 selected/i }));
    await waitFor(() => expect(posts).toBe(1));
    expect(await screen.findByText(/1 acknowledged/i)).toBeInTheDocument();
  });

  test("empty queue shows the calm empty state", async () => {
    server.use(http.get("/api/v1/tasks", () => HttpResponse.json([])));
    renderWithProviders(<AckInbox />);
    expect(await screen.findByText(/No documents awaiting your acknowledgement/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run it — expect FAIL.** Run: `cd apps/web && npx vitest run src/features/review/AckInbox.test.tsx`

- [ ] **Step 3: Implement**

```tsx
import { Alert, Button, Checkbox, Group, Loader, Stack, Table, Text, Title } from "@mantine/core";
import { useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../../lib/api";
import { useDocument } from "../document/useDocument";
import type { Task } from "../../lib/types";
import { useBulkAcknowledge } from "./ackHooks";
import { useTask, useTasks } from "./hooks";

// One inbox row — resolves the doc name best-effort (the list row has no subject_id, so fetch the
// task detail → its document). Selection is controlled by the parent via taskId.
function AckInboxRow({ task, selected, onToggle }: { task: Task; selected: boolean; onToggle: (id: string) => void }) {
  const detail = useTask(task.id); // gives subject_id (detail-only)
  const docId = detail.data?.subject_id ?? null;
  const doc = useDocument(docId, { enabled: docId !== null, retry: false });
  const name = doc.data ? `${doc.data.identifier} — ${doc.data.title}` : docId ? "Document" : "…";
  return (
    <Table.Tr>
      <Table.Td>
        <Checkbox aria-label={`Select ${name}`} checked={selected} onChange={() => onToggle(task.id)} />
      </Table.Td>
      <Table.Td>{docId ? <Link to={`/tasks/${task.id}`}>{name}</Link> : name}</Table.Td>
      <Table.Td>{task.due_at ? task.due_at.slice(0, 10) : "—"}</Table.Td>
    </Table.Tr>
  );
}

// S-ack-2: the dedicated DOC_ACK bulk-ack view (the bell's destination, /tasks?type=DOC_ACK). Multi-select
// loops the per-task decision POST (doc 10 §8.2). Partial failures are reported, never thrown.
export function AckInbox() {
  const { data: tasks, isLoading, isError, error } = useTasks({ state: "PENDING", type: "DOC_ACK" });
  const bulk = useBulkAcknowledge();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [summary, setSummary] = useState<string | null>(null);

  if (isLoading) return <Loader aria-label="Loading acknowledgements" />;
  if (isError) {
    if (error instanceof ApiError && error.status === 403)
      return <Text c="dimmed">You don't have access to the acknowledgement queue.</Text>;
    return <Text c="red">Could not load your acknowledgements.</Text>;
  }
  const rows = tasks ?? [];
  const allSelected = rows.length > 0 && rows.every((t) => selected.has(t.id));

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }
  function toggleAll() {
    setSelected(allSelected ? new Set() : new Set(rows.map((t) => t.id)));
  }
  async function acknowledgeSelected() {
    setSummary(null);
    const ids = rows.map((t) => t.id).filter((id) => selected.has(id));
    const out = await bulk.run(ids);
    setSelected(new Set());
    const failedNote = out.failed.length ? ` · ${out.failed.length} could not be acknowledged (refresh)` : "";
    setSummary(`${out.ok.length} acknowledged${failedNote}`);
  }

  return (
    <Stack gap="md">
      <Title order={2}>Acknowledgements</Title>
      {summary && <Alert color={summary.includes("could not") ? "yellow" : "green"} withCloseButton onClose={() => setSummary(null)}>{summary}</Alert>}
      {rows.length === 0 ? (
        <Text c="dimmed">No documents awaiting your acknowledgement.</Text>
      ) : (
        <>
          <Group>
            <Button onClick={() => void acknowledgeSelected()} disabled={selected.size === 0}>
              Acknowledge {selected.size} selected
            </Button>
          </Group>
          <Table aria-label="Documents to acknowledge" striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th scope="col"><Checkbox aria-label="Select all" checked={allSelected} onChange={toggleAll} /></Table.Th>
                <Table.Th scope="col">Document</Table.Th>
                <Table.Th scope="col">Due</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {rows.map((t) => (
                <AckInboxRow key={t.id} task={t} selected={selected.has(t.id)} onToggle={toggle} />
              ))}
            </Table.Tbody>
          </Table>
        </>
      )}
    </Stack>
  );
}
```

- [ ] **Step 4: Run it — expect PASS.** Run: `cd apps/web && npx vitest run src/features/review/AckInbox.test.tsx`

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/review/AckInbox.tsx apps/web/src/features/review/AckInbox.test.tsx
git commit -m "feat(s-ack-2): AckInbox — multi-select bulk-ack (per-row detail→doc resolution)"
```

---

## Task 12: `TasksInbox` routes `?type=DOC_ACK` → `AckInbox`

**Files:**
- Modify: `apps/web/src/features/review/TasksInbox.tsx`
- Test: `apps/web/src/features/review/TasksInbox.test.tsx` (add a routing case; create if absent)

- [ ] **Step 1: Write the failing test**

```tsx
import { screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { renderWithProviders } from "../../test/render";
import { TasksInbox } from "./TasksInbox";

describe("TasksInbox routing", () => {
  test("?type=DOC_ACK renders the AckInbox", async () => {
    renderWithProviders(<TasksInbox />, { route: "/tasks?type=DOC_ACK" });
    expect(await screen.findByRole("heading", { name: "Acknowledgements" })).toBeInTheDocument();
  });

  test("no type param renders the default review queue", async () => {
    renderWithProviders(<TasksInbox />, { route: "/tasks" });
    expect(await screen.findByText("Review & Approve")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run it — expect FAIL.** Run: `cd apps/web && npx vitest run src/features/review/TasksInbox.test.tsx`

- [ ] **Step 3: Edit `TasksInbox.tsx`** — branch on `?type` at the top.

  Add imports:
```tsx
import { useSearchParams } from "react-router-dom";
import { AckInbox } from "./AckInbox";
```

  At the very top of the `TasksInbox` function body:
```tsx
  const [sp] = useSearchParams();
  if (sp.get("type") === "DOC_ACK") return <AckInbox />;
```

  (Leave the rest of the existing component untouched.)

- [ ] **Step 4: Run it — expect PASS.** Run: `cd apps/web && npx vitest run src/features/review/TasksInbox.test.tsx`

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/review/TasksInbox.tsx apps/web/src/features/review/TasksInbox.test.tsx
git commit -m "feat(s-ack-2): TasksInbox routes ?type=DOC_ACK to the bulk AckInbox"
```

---

## Task 13: TopBar ack bell + `useAckCount`

**Files:**
- Create: `apps/web/src/app/shell/useAckCount.ts`
- Modify: `apps/web/src/app/shell/TopBar.tsx`
- Test: `apps/web/src/app/shell/TopBar.test.tsx` (add bell cases; create if absent)

- [ ] **Step 1: Write the failing test**

```tsx
import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, test } from "vitest";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { TopBar } from "./TopBar";

function renderBar() {
  return renderWithProviders(<TopBar navOpened={false} onToggleNav={() => {}} onOpenSearch={() => {}} />, { route: "/" });
}

describe("TopBar ack bell", () => {
  test("the bell links to the filtered DOC_ACK inbox and keeps a distinct aria-label", async () => {
    renderBar();
    const link = await screen.findByRole("link", { name: "Acknowledgements" });
    expect(link).toHaveAttribute("href", "/tasks?type=DOC_ACK&state=PENDING");
    expect(screen.getByLabelText("Tasks")).toBeInTheDocument(); // sibling untouched, distinct label
  });

  test("shows the open-DOC_ACK count badge", async () => {
    server.use(
      http.get("/api/v1/tasks", ({ request }) => {
        const type = new URL(request.url).searchParams.get("type");
        return HttpResponse.json(type === "DOC_ACK" ? [{ id: "a" }, { id: "b" }, { id: "c" }] : []);
      }),
    );
    renderBar();
    expect(await screen.findByText("3")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run it — expect FAIL.** Run: `cd apps/web && npx vitest run src/app/shell/TopBar.test.tsx`

- [ ] **Step 3: Implement `useAckCount.ts`** (in app/shell — no features/ import)

```ts
import { useQuery } from "@tanstack/react-query";
import { useApi } from "../../lib/api";

// S-ack-2: the TopBar ack-bell count — the caller's open DOC_ACK tasks. Kept in app/shell (not
// features/review) so the shell never depends on a feature module. Self-scoped server-side.
export function useAckCount() {
  const api = useApi();
  const query = useQuery({
    queryKey: ["ack-count"],
    queryFn: () => api.get<{ id: string }[]>("/api/v1/tasks?assignee=me&state=PENDING&type=DOC_ACK"),
    retry: false,
  });
  return query.data?.length ?? 0;
}
```

- [ ] **Step 4: Edit `TopBar.tsx`** — replace the disabled Acknowledgements Indicator.

  Add imports:
```tsx
import { Link } from "react-router-dom";
import { useAckCount } from "./useAckCount";
```

  In the component body (before the `return`): `const ackCount = useAckCount();`

  Replace the Acknowledgements `<Indicator disabled>...` block with:
```tsx
        <Indicator label={ackCount} size={16} disabled={ackCount === 0} aria-label={`${ackCount} open acknowledgements`}>
          <ActionIcon component={Link} to="/tasks?type=DOC_ACK&state=PENDING" variant="subtle" aria-label="Acknowledgements">
            &#128276;
          </ActionIcon>
        </Indicator>
```

  Leave the "Tasks" Indicator exactly as-is.

- [ ] **Step 5: Run it — expect PASS.** Run: `cd apps/web && npx vitest run src/app/shell/TopBar.test.tsx`

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/app/shell/useAckCount.ts apps/web/src/app/shell/TopBar.tsx apps/web/src/app/shell/TopBar.test.tsx
git commit -m "feat(s-ack-2): TopBar ack bell — open-DOC_ACK count + filtered-inbox link"
```

---

## Task 14: Full gate (`/check-web`)

**Files:** none (verification)

- [ ] **Step 1: Run eslint + strict tsc + build + the full vitest suite**

Run: `/check-web` (or manually: `cd apps/web && npm run lint && npx tsc --noEmit && npm run build && npx vitest run --pool=forks --poolOptions.forks.singleFork=true`)
Expected: all green. Web test count up from the 551 baseline. `noUncheckedIndexedAccess` clean (watch
array-index access in the new code — use `?? ""`/`!` deliberately, as the snippets do).

- [ ] **Step 2: Fix any failures** (cross-file drift the per-file runs missed — e.g. an existing test
  that depended on the doc-page card layout, or a fixture-shape `satisfies` error). Re-run until green.

- [ ] **Step 3: Commit** (only if fixes were needed)

```bash
git add -A && git commit -m "test(s-ack-2): full /check-web green — fixes after the cross-file run"
```

---

## Task 15: Docs — the missing S-ack-1 entry + the S-ack-2 entry

**Files:**
- Modify: `docs/slice-history.md`

- [ ] **Step 1: Add the S-ack-1 narrative entry** (it was never written when the backend merged). Place
  it in the v1-families/ack section, matching the house style (bold slice name · mig ref · ⚠ traps ·
  Spec pointer). Source: the PR #113 body + R42/R43. Draft:

```markdown
- **S-ack-1** the Acknowledgements backend — opens the S-ack family (mig `0048`; **R42** `document.distribute`
  [catalog 99→100, QMS Owner] + **R43** the family model). Sam's read-and-understood flow; the acks half of
  the PDCA-dashboard unblock. `distribution_entry` (editable issuance config, SELECT/INSERT/DELETE) +
  append-only `acknowledgement` (REVOKE UPDATE,DELETE — the capa_stage house style; UNIQUE(user, version),
  XFF `client_ip` Text, `created_reason` enum) + `documented_information.acknowledgement_required` + the
  additive `DOC_ACK` task/subject enum + `DOCUMENT_ACKNOWLEDGED`/`DISTRIBUTION_UPDATED` events + the
  seeded single-stage `doc_acknowledgement` workflow (quorum ANY, **no signature** — R2 untouched,
  `document.acknowledge` stays sig_hook=false). ⚠ **Re-ack is MAJOR-only with carry-forward** (`domain/ack/rules.py`):
  satisfied iff acked `version_seq ≥ last-MAJOR-boundary`, walked over **ever-governed versions only** (a
  phantom never-Effective MAJOR draft cannot re-arm the audience — the diff-critic MAJOR); MINOR carries
  coverage forward, no-MAJOR chains fall back to the lowest governed seq. ⚠ **In-force = `current_effective_version_id IS NOT NULL`,
  NOT `current_state == Effective`** (an UnderRevision doc still governs — keying on state would mass-cancel
  the moment a revision opens). The sweep (`services/ack/sweep.py`, daily Beat `easysynq.ack.sweep` +
  post-commit enqueues via the `AckEnqueueSink` seam) is ONE universal mint covering release / R15 target
  entry / flag flips / entry changes; cancel-before-mint; **fail-closed on a missing definition** (no
  mass-cancel on broken config); `populate_existing` locked loads (the S-drift-1 trap). The DOC_ACK decide
  leg (the fourth `/tasks/{id}/decision` dispatch): membership 404-collapse → `document.acknowledge` enforced
  at the doc scope (the key's first consumer) → outcome whitelist `{acknowledge}` → 409 `ack_obligation_lapsed`/`ack_superseded`
  → the immutable ack row + audit in ONE txn; Idempotency-Key replay parity. Endpoints: GET/POST/DELETE
  `/documents/{id}/distribution` + the R42-gated named matrix `/documents/{id}/acknowledgements`; counts-only
  coverage under `document.read`. ⚠ Two pre-existing infra bugs surfaced + fixed on the first real integration
  run: a `pg_advisory_lock` stranded on pooled engines (now a dedicated held connection) and a ~19.5s Redis
  retry storm per `.delay()` in CI (the harness swaps both enqueue sinks to Logging doubles). 11 unit + 13
  integration tests. Deferred (R43): Remind + reminder history, the §6.3 report, process/folder targets, the
  org-wide PDCA rollup, bulk re-ack, the every-release config flag, the delegation carve-out, ack retention/GDPR.
  **S-ack-2 (the UI tail) follows.** Spec: `docs/superpowers/specs/2026-06-10-s-ack-acknowledgements-design.md`.
```

- [ ] **Step 2: Add the S-ack-2 entry** (just below S-ack-1):

```markdown
- **S-ack-2** the Acknowledgements UI — **CLOSES the S-ack family; FRONT-END ONLY** (no migration/key/endpoint/contract).
  Four surfaces over S-ack-1's endpoints: (1) the `/tasks` **DOC_ACK leg** — a 4th `subject_type` branch in
  `ReviewApprovePage` rendering a *dedicated* `AttestationCard` ("I have read & understood", one click, **no
  signature, no radio** — separate from `DecisionCard` so the DOCUMENT/CAPA/PERIODIC legs stay byte-untouched)
  + `DocAckContext` (best-effort doc, calm-403 never blocks the card); 409 `ack_obligation_lapsed`/`ack_superseded`
  mapped to calm copy; (2) the dedicated **bulk-ack inbox** at `/tasks?type=DOC_ACK` (`AckInbox`, multi-select
  looping the decision POST via `useBulkAcknowledge`/allSettled — doc 10 §8.2; ⚠ the list omits `subject_id`,
  so each row fetches its task detail → doc for the name); (3) the **doc page** refactored to real Mantine
  `Tabs` (Overview/History/Approvals/Where-used/**Acks**, `?tab=` deep-linkable) + the **Acknowledged tile**
  (restores the S-web-4 omission) + the **Acks tab** (`AckCoverageRing` + counts for any `document.read` reader;
  the named matrix + avatar stack + the `DistributionEditor` only with `document.distribute`); (4) the **TopBar
  ack bell** (open-DOC_ACK count via `useAckCount` in app/shell, links to the filtered inbox; the "Tasks" stub
  untouched, labels distinct). ⚠ **Remind stays OMITTED-NOT-FAKED** (the mockup shows it; R43 notifications-family
  deferral) — and the **doc-level Audit tab is out of the initial release** (the admin audit-log sibling is parked).
  ⚠ QMS Owner holds `role.read` (`0004_seed_authz.py`) so the editor's `org_role` picker is viable for the
  distribute holder. Calm-403 reads pin a production-defaults QueryClient (the S-web-8 lesson). Spec:
  `docs/superpowers/specs/2026-06-11-s-ack-2-acknowledgements-ui-design.md`.
```

- [ ] **Step 3: Bump the migration-head line** in `docs/slice-history.md` (if it still reads `0047`/`next 0048`):
  set it to head `0048` (next `0049`). (Grep the file for the head line and update it.)

- [ ] **Step 4: Commit**

```bash
git add docs/slice-history.md
git commit -m "docs(s-ack): backfill the missing S-ack-1 slice-history entry + add S-ack-2 + bump head to 0048"
```

---

## Task 16: diff-critic, live smoke, PR

**Files:** none (review + PR), then a CLAUDE.md learnings line.

- [ ] **Step 1: Run the diff-critic agent on the branch diff**

Use the `Agent` tool, `subagent_type: diff-critic`, pointed at the branch diff vs `main`. Fold ONLY
confirmed findings. Likely hunting grounds: a fixture shape drifting from the real serializer; a
calm-403 that re-hammers (production-defaults pin); the `?tab=`/`?type=` URL handling; the AckInbox
detail→doc cascade; a duplicate aria-label; `noUncheckedIndexedAccess` array nits.

- [ ] **Step 2: Live smoke via Chrome MCP** (localhost only, client-side nav, text-first verification)
  - Rebuild the web image if needed (`just up s` serves a baked build): `docker compose ... up -d --build web`, hard-refresh / Incognito.
  - Resolve which of the two demo `app_user` rows the live Keycloak `demo` subject JIT-maps to (kcadm), and
    confirm `document.distribute` + `document.acknowledge` SYSTEM overrides sit on THAT row (the S-web-8 trap;
    the "Demo (Admin)" `e3964e7a…` row already carries them from the S-ack-1 smoke).
  - Verify: the doc page Acks tab on `SOP-PUR-002` (live-seeded flag-on, coverage 1/1/0/0) shows the ring +
    the named matrix + the editor; toggling the flag / adding a user entry round-trips; the TopBar bell shows
    a count and routes to `/tasks?type=DOC_ACK`; an attestation acknowledges and the count drops.

- [ ] **Step 3: Add a CLAUDE.md Recent-learnings line** (newest first, cap ~12)

```markdown
- 2026-06-11 — **S-ack-2 (Acknowledgements UI) CLOSES the S-ack family — FRONT-END ONLY** (no migration/key/endpoint/contract):
  the 4th `/tasks` DOC_ACK leg (a *dedicated* `AttestationCard` — acknowledge-only, NO signature/radio, separate
  from `DecisionCard` so the DOCUMENT/CAPA/PERIODIC legs stay byte-identical), the dedicated `/tasks?type=DOC_ACK`
  bulk-ack inbox, the doc page **refactored to real Mantine Tabs** + Acknowledged tile + Acks tab (reader sees
  ring+counts; `document.distribute` sees the named matrix + `DistributionEditor`), the TopBar ack bell.
  ⚠ `GET /tasks` (list) OMITS `subject_type`/`subject_id` (detail-only) → the bulk rows fetch task-detail→doc for
  the name. ⚠ QMS Owner holds `role.read` (the org_role picker is viable). ⚠ Remind omitted-not-faked + the
  doc-level Audit tab out-of-initial-release (both deferred). Narrative: `docs/slice-history.md`.
```

  Commit: `git add CLAUDE.md && git commit -m "docs(s-ack-2): CLAUDE.md recent-learnings line"`

- [ ] **Step 4: Open the PR** (use the `/pr` skill or `gh`; body file via PowerShell `@'…'@` → `--body-file`)

Title: `feat(s-ack-2): acknowledgements UI — DOC_ACK leg, bulk inbox, doc-page Acks tab + tile, ack bell`
Body: the four surfaces, front-end-only, the 3 owner forks, the Audit-tab deferral, the S-ack-1
slice-history backfill, test count delta, the diff-critic + live-smoke trail.

---

## Self-review (run before dispatching Task 1)

- **Spec coverage:** §3 doc page → Tasks 2,3,4,5,6; §4 task leg → Tasks 7,8,9,10; §5 bulk inbox →
  Tasks 11,12; §6 bell → Task 13; §2 shapes → Task 1; §9 gate → Task 14; §10 chore → Task 15; §11
  deferrals honoured (no Remind, no Audit tab, no process/folder offered). ✓
- **Placeholders:** none — every step carries concrete code or an exact command. The one intentional
  stub (DistributionEditor in Task 4) is replaced with full code in Task 5. ✓
- **Type consistency:** `Coverage`/`DistributionPayload`/`AckMatrixRow`/`AckDecisionResult`/`DistributionUpdateBody`
  (Task 1) are used verbatim in Tasks 2–6,9; `AckInput`/`BulkResult` (Task 7) in Tasks 9,11; `useAckCount`
  returns `number` (Task 13). Query keys consistent: `["distribution",id]`, `["acknowledgements",id]`,
  `["ack-count"]`, `["document",id]`, `["tasks"]`, `["roles"]`. ✓
