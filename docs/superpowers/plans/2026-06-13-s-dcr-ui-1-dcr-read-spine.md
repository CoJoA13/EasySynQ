# S-dcr-ui-1 — DCR read spine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the already-built DCR (Document Change Request) backend its first SPA surface — a `/dcrs` register + a `?dcr=<id>` read-only drawer (state badge · stage-event timeline · impact panel · resolved target/source references · resulting-version deep-link).

**Architecture:** A new front-end-only module `apps/web/src/features/dcr/` mirroring `features/capa` (board+drawer) and `features/management-review` (no-capabilities-block cockpit). It consumes three existing `changeRequest.read` reads (`GET /dcrs`, `/dcrs/{id}`, `/dcrs/{id}/impact`) plus the existing `GET /documents/{id}` (target-doc label, calm-degrade) and `GET /directory/users` (actor names). No migration, no new key, no new endpoint, no contract change. Gate: `/check-web` only.

**Tech Stack:** React 18 + TypeScript (strict) · Mantine v7 · TanStack Query v5 · react-router v6 · Vitest + Testing Library + MSW + jest-axe.

**Spec:** `docs/superpowers/specs/2026-06-13-s-dcr-ui-1-dcr-read-spine-design.md`.

**Cross-cutting conventions (apply in every task — verified against the codebase this session):**
- **Every test file:** `import { expect, it } from "vitest"` (+ `describe`/`vi` as needed). The bare global `expect` is jest-typed and `tsc` (not vitest) rejects `.toBeInTheDocument` — only the full `/check-web`/`tsc` catches it.
- **Pin every MSW fixture with `satisfies <Type>`** to the real `lib/types` interface (which is itself pinned to `api/dcr.py`). Never hand-type a guess.
- **`renderWithProviders(ui, { route })`** (from `../../test/render`) wraps MantineProvider→QueryClient(`retry:false`)→AuthContext→MemoryRouter. Pass a deep-link via `{ route: "/dcrs?dcr=<id>" }`.
- **MSW** `server` from `../../test/msw/server`; per-test override `server.use(http.get(...))`; `onUnhandledRequest:"error"` means every endpoint a test touches needs a default handler in `handlers.ts` (added in Task 1).
- **Calm-403 contract:** `retry: false` on every hook; `forbidden = query.error instanceof ApiError && query.error.status === 403`.
- **Dates:** `new Date(iso).toISOString().slice(0, 10)` (the codebase idiom; no shared formatter, no tz lib).
- **Actor/user names:** `directory.find(u => u.id === id)?.display_name ?? \`${id.slice(0,8)}…\`` over the `useUserDirectory()` roster; `null` actor → `"system"`.
- **a11y:** one `h1`/`Title order={2}` page title; section titles `order={4}`/`order={5}` (no level jumps — jest-axe enforces); each `Select` a distinct `aria-label`.

---

### Task 1: Data layer — DCR types, hooks, MSW handlers + fixtures

**Files:**
- Modify: `apps/web/src/lib/types.ts` (append a new banner section)
- Create: `apps/web/src/features/dcr/hooks.ts`
- Modify: `apps/web/src/test/msw/handlers.ts` (add DCR default handlers + fixtures; confirm/add `documents/:id` + `directory/users`)

- [ ] **Step 1: Add DCR types to `lib/types.ts`**

Append at the end of `apps/web/src/lib/types.ts` (the file is the single home for all domain types — there is no per-feature `types.ts`):

```ts
// ---- S-dcr-ui-1 (Document Change Request — read spine) — pinned to api/dcr.py serializers ----
// _dcr (api/dcr.py:118-137), _stage_event (:151-160), _impact (:140-148).
// ⚠ The verification sweep's header said "16 fields" but enumerated 15; api/dcr.py:118-137 is
// authoritative — if the live serializer carries a 16th key, add it here (do not invent/omit).
export type DcrChangeType = "REVISE" | "CREATE" | "RETIRE";
export type DcrChangeSignificance = "MAJOR" | "MINOR"; // reused vault ChangeSignificance on the backend
export type DcrReasonClass =
  | "regulatory"
  | "audit_finding"
  | "capa"
  | "process_improvement"
  | "error_correction"
  | "periodic_review"
  | "customer_requirement"
  | "mgmt_review"
  | "other";
export type DcrSourceLinkType = "capa" | "finding" | "mgmt_review" | "risk";
export type DcrState =
  | "Open"
  | "Assessed"
  | "Routed"
  | "InApproval"
  | "Approved"
  | "Implemented"
  | "Closed"
  | "Cancelled"
  | "Rejected";

export interface Dcr {
  id: string;
  identifier: string; // DCR-{YYYY}-{NNNN}
  target_document_id: string | null; // null for CREATE
  change_type: DcrChangeType;
  change_significance: DcrChangeSignificance;
  reason_class: DcrReasonClass;
  reason_text: string;
  source_link_type: DcrSourceLinkType | null;
  source_link_id: string | null; // polymorphic, no FK
  proposed_effective_from: string | null; // ISO datetime
  resulting_version_id: string | null; // set at implement (REVISE/CREATE); null for RETIRE / pre-implement
  state: DcrState;
  decision: string | null; // null until approval/rejection
  created_by: string; // an app_user.id
  created_at: string; // ISO datetime
}

export interface DcrStageEvent {
  id: string;
  from_state: DcrState | null; // null on genesis
  to_state: DcrState;
  actor_id: string | null; // null for system/Beat
  comment: string | null;
  payload: Record<string, unknown> | null; // free JSONB — not rendered in the read spine
  occurred_at: string;
}

export interface DcrDetail extends Dcr {
  stage_events: DcrStageEvent[]; // GET /dcrs/{id} augments _dcr with this
}

export interface DcrList {
  data: Dcr[];
}

export interface DcrImpact {
  id: string;
  dimension: string; // one of 7
  auto_populated: Record<string, unknown> | null; // system facts, e.g. {applicable, processes}
  requester_annotation: string | null;
  created_at: string;
  updated_at: string | null;
}

export interface DcrImpactList {
  data: DcrImpact[];
}
```

> ⚠ Before committing, open `apps/api/src/easysynq_api/api/dcr.py:118-137` and confirm the `_dcr` dict keys match the `Dcr` interface exactly (count + names + null-ability). If `lib/types.ts` already exports a `ChangeSignificance` union of `"MAJOR"|"MINOR"`, reuse it instead of `DcrChangeSignificance` (grep first).

- [ ] **Step 2: Create the hooks**

`apps/web/src/features/dcr/hooks.ts`:

```ts
import { useQuery } from "@tanstack/react-query";
import { ApiError, useApi } from "../../lib/api";
import type { DcrDetail, DcrImpact, DcrImpactList, DcrList, Dcr } from "../../lib/types";

// List — client-side filtering happens in the page (the CAPA precedent), so this takes no args.
export function useDcrs() {
  const api = useApi();
  const query = useQuery({
    queryKey: ["dcrs"],
    queryFn: async (): Promise<Dcr[]> => (await api.get<DcrList>("/api/v1/dcrs")).data,
    retry: false,
  });
  const forbidden = query.error instanceof ApiError && query.error.status === 403;
  return { ...query, forbidden };
}

export function useDcr(id: string | null) {
  const api = useApi();
  const query = useQuery({
    queryKey: ["dcr", id],
    queryFn: () => api.get<DcrDetail>(`/api/v1/dcrs/${id!}`),
    enabled: id !== null,
    retry: false,
  });
  const forbidden = query.error instanceof ApiError && query.error.status === 403;
  return { ...query, forbidden };
}

export function useDcrImpact(id: string | null) {
  const api = useApi();
  return useQuery({
    queryKey: ["dcr-impact", id],
    queryFn: async (): Promise<DcrImpact[]> => (await api.get<DcrImpactList>(`/api/v1/dcrs/${id!}/impact`)).data,
    enabled: id !== null,
    retry: false,
  });
}
```

- [ ] **Step 3: Add DCR default MSW handlers + fixtures**

In `apps/web/src/test/msw/handlers.ts`: import the new types at the top (`import type { Dcr, DcrDetail, DcrImpact, DcrImpactList, DcrList } from "../../lib/types";`), define the fixtures, and add three handlers to the `handlers` array.

```ts
// ---- S-dcr-ui-1 fixtures (pinned to api/dcr.py _dcr/_stage_event/_impact) ----
export const DCR_REVISE_ID = "dcr00001-0001-0001-0001-000000000001";
export const DCR_CREATE_ID = "dcr00002-0002-0002-0002-000000000002";
export const DCR_IMPL_ID = "dcr00003-0003-0003-0003-000000000003";
export const DCR_CANCELLED_ID = "dcr00004-0004-0004-0004-000000000004";
const TARGET_DOC_ID = "doc00001-0001-0001-0001-000000000001";

const dcrListFixture = {
  data: [
    {
      id: DCR_REVISE_ID,
      identifier: "DCR-2026-0001",
      target_document_id: TARGET_DOC_ID,
      change_type: "REVISE",
      change_significance: "MAJOR",
      reason_class: "capa",
      reason_text: "Corrective action requires a procedure revision.",
      source_link_type: "capa",
      source_link_id: "capa0001-0001-0001-0001-000000000001",
      proposed_effective_from: null,
      resulting_version_id: null,
      state: "Open",
      decision: null,
      created_by: "bbbb1111-1111-1111-1111-111111111111",
      created_at: "2026-06-10T09:00:00+00:00",
    },
    {
      id: DCR_CREATE_ID,
      identifier: "DCR-2026-0002",
      target_document_id: null,
      change_type: "CREATE",
      change_significance: "MINOR",
      reason_class: "process_improvement",
      reason_text: "A new work instruction is needed.",
      source_link_type: null,
      source_link_id: null,
      proposed_effective_from: null,
      resulting_version_id: null,
      state: "Assessed",
      decision: null,
      created_by: "bbbb1111-1111-1111-1111-111111111111",
      created_at: "2026-06-09T09:00:00+00:00",
    },
    {
      id: DCR_IMPL_ID,
      identifier: "DCR-2026-0003",
      target_document_id: TARGET_DOC_ID,
      change_type: "REVISE",
      change_significance: "MAJOR",
      reason_class: "audit_finding",
      reason_text: "Audit finding closed via revision.",
      source_link_type: "finding",
      source_link_id: "find0001-0001-0001-0001-000000000001",
      proposed_effective_from: "2026-07-01T00:00:00+00:00",
      resulting_version_id: "ver00001-0001-0001-0001-000000000001",
      state: "Implemented",
      decision: "Approved by the change board.",
      created_by: "bbbb1111-1111-1111-1111-111111111111",
      created_at: "2026-05-01T09:00:00+00:00",
    },
    {
      id: DCR_CANCELLED_ID,
      identifier: "DCR-2026-0004",
      target_document_id: TARGET_DOC_ID,
      change_type: "RETIRE",
      change_significance: "MINOR",
      reason_class: "other",
      reason_text: "Superseded request, withdrawn.",
      source_link_type: null,
      source_link_id: null,
      proposed_effective_from: null,
      resulting_version_id: null,
      state: "Cancelled",
      decision: null,
      created_by: "bbbb1111-1111-1111-1111-111111111111",
      created_at: "2026-04-01T09:00:00+00:00",
    },
  ],
} satisfies DcrList;

const dcrDetailFixture = {
  ...dcrListFixture.data[0]!,
  stage_events: [
    {
      id: "se-1",
      from_state: null,
      to_state: "Open",
      actor_id: "bbbb1111-1111-1111-1111-111111111111",
      comment: "Change request raised.",
      payload: null,
      occurred_at: "2026-06-10T09:00:00+00:00",
    },
  ],
} satisfies DcrDetail;

const dcrImpactFixture = {
  data: [
    {
      id: "imp-1",
      dimension: "processes",
      auto_populated: { applicable: true, processes: ["p1", "p2"] },
      requester_annotation: "Affects the calibration process.",
      created_at: "2026-06-10T10:00:00+00:00",
      updated_at: null,
    },
    {
      id: "imp-2",
      dimension: "training",
      auto_populated: { applicable: false },
      requester_annotation: null,
      created_at: "2026-06-10T10:00:00+00:00",
      updated_at: null,
    },
  ],
} satisfies DcrImpactList;
```

Add to the `handlers` array:

```ts
http.get("/api/v1/dcrs", () => HttpResponse.json(dcrListFixture)),
http.get("/api/v1/dcrs/:id/impact", () => HttpResponse.json(dcrImpactFixture)),
http.get("/api/v1/dcrs/:id", () => HttpResponse.json(dcrDetailFixture)),
```

> ⚠ Register `:id/impact` BEFORE `:id` in the array is NOT required for MSW (it matches by full path, not prefix), but keep them adjacent for readability.

> ⚠ Confirm `handlers.ts` already has default handlers for `GET /api/v1/documents/:id` (returns a `DocumentSummary` with `identifier`+`title`) and `GET /api/v1/directory/users` (returns `DirectoryUser[]`). The CAPA/MR tests use both, so they almost certainly exist. If `documents/:id` is absent, add:
> ```ts
> http.get("/api/v1/documents/:id", () => HttpResponse.json({ id: TARGET_DOC_ID, identifier: "SOP-001", title: "Calibration procedure", current_state: "Effective", kind: "DOCUMENT" })),
> ```
> (pin it `satisfies DocumentSummary` if the type is exported). If `directory/users` is absent, add a handler returning `[{ id: "bbbb1111-1111-1111-1111-111111111111", display_name: "Priya Author" }] satisfies DirectoryUser[]`.

- [ ] **Step 4: Verify the data layer compiles (`tsc` is the test here)**

Run: `cd apps/web && npx tsc --noEmit`
Expected: PASS (the `satisfies` pins enforce the fixture shapes against the new types; any field mismatch errors here).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/lib/types.ts apps/web/src/features/dcr/hooks.ts apps/web/src/test/msw/handlers.ts
git commit -m "feat(s-dcr-ui-1): DCR types, read hooks, MSW handlers+fixtures"
```

---

### Task 2: `DcrStateBadge`

**Files:**
- Create: `apps/web/src/features/dcr/DcrStateBadge.tsx`
- Test: `apps/web/src/features/dcr/DcrStateBadge.test.tsx`

- [ ] **Step 1: Write the failing test**

`DcrStateBadge.test.tsx`:

```tsx
import { expect, it } from "vitest";
import { renderWithProviders } from "../../test/render";
import { DcrStateBadge } from "./DcrStateBadge";

it("renders the human label and a non-color aria-label", () => {
  const { getByLabelText, getByText } = renderWithProviders(<DcrStateBadge state="InApproval" />);
  expect(getByLabelText("State: In approval")).toBeInTheDocument();
  expect(getByText("In approval")).toBeInTheDocument();
});

it("renders a terminal state", () => {
  const { getByLabelText } = renderWithProviders(<DcrStateBadge state="Rejected" />);
  expect(getByLabelText("State: Rejected")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npx vitest run src/features/dcr/DcrStateBadge.test.tsx`
Expected: FAIL — `Cannot find module "./DcrStateBadge"`.

- [ ] **Step 3: Write the component**

`DcrStateBadge.tsx` (mirrors `features/document/StateBadge.tsx`: token-driven color map, a non-color `mark` glyph in `leftSection` (`aria-hidden`), an `aria-label` carrying the meaning — never color-only):

```tsx
import { Badge, type MantineSize } from "@mantine/core";
import type { DcrState } from "../../lib/types";

const META: Record<DcrState, { label: string; mark: string; color: string }> = {
  Open: { label: "Open", mark: "✎", color: "var(--es-info)" },
  Assessed: { label: "Assessed", mark: "◔", color: "var(--es-info)" },
  Routed: { label: "Routed", mark: "→", color: "var(--es-info)" },
  InApproval: { label: "In approval", mark: "◔", color: "var(--es-warning)" },
  Approved: { label: "Approved", mark: "✓", color: "var(--es-info)" },
  Implemented: { label: "Implemented", mark: "★", color: "var(--es-success)" },
  Closed: { label: "Closed", mark: "✓", color: "var(--es-success)" },
  Cancelled: { label: "Cancelled", mark: "⊘", color: "var(--es-text-muted)" },
  Rejected: { label: "Rejected", mark: "⊘", color: "red" },
};

export function DcrStateBadge({ state, size = "sm" }: { state: DcrState; size?: MantineSize }) {
  const { label, mark, color } = META[state];
  return (
    <Badge
      variant="light"
      color={color}
      size={size}
      leftSection={<span aria-hidden="true">{mark}</span>}
      aria-label={`State: ${label}`}
    >
      {label}
    </Badge>
  );
}
```

> ⚠ `--es-info`/`--es-warning`/`--es-success`/`--es-text-muted` are the confirmed StateBadge tokens. For Rejected, this uses Mantine's named `"red"` (guaranteed) rather than guessing a `--es-danger` token; if a red `--es-*` token exists in `theme/` or the mockup CSS, swap it in for consistency. The test asserts label + aria-label only, never color.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npx vitest run src/features/dcr/DcrStateBadge.test.tsx`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/dcr/DcrStateBadge.tsx apps/web/src/features/dcr/DcrStateBadge.test.tsx
git commit -m "feat(s-dcr-ui-1): DcrStateBadge"
```

---

### Task 3: `DcrStageTimeline`

**Files:**
- Create: `apps/web/src/features/dcr/DcrStageTimeline.tsx`
- Test: `apps/web/src/features/dcr/DcrStageTimeline.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { expect, it } from "vitest";
import { renderWithProviders } from "../../test/render";
import { DcrStageTimeline } from "./DcrStageTimeline";
import type { DcrStageEvent, DirectoryUser } from "../../lib/types";

const directory: DirectoryUser[] = [{ id: "u-1", display_name: "Priya Author" }];

it("renders genesis as the to-state only, and a transition as from→to with the resolved actor", () => {
  const events: DcrStageEvent[] = [
    { id: "e1", from_state: null, to_state: "Open", actor_id: "u-1", comment: "Raised", payload: null, occurred_at: "2026-06-10T09:00:00+00:00" },
    { id: "e2", from_state: "Open", to_state: "Assessed", actor_id: null, comment: null, payload: null, occurred_at: "2026-06-11T09:00:00+00:00" },
  ];
  const { getByText } = renderWithProviders(<DcrStageTimeline events={events} directory={directory} />);
  expect(getByText("Open")).toBeInTheDocument();
  expect(getByText("Open → Assessed")).toBeInTheDocument();
  expect(getByText(/Priya Author/)).toBeInTheDocument(); // resolved actor
  expect(getByText(/system/)).toBeInTheDocument(); // null actor → "system"
  expect(getByText("Raised")).toBeInTheDocument(); // comment
});

it("shows an empty state when there are no events", () => {
  const { getByText } = renderWithProviders(<DcrStageTimeline events={[]} directory={directory} />);
  expect(getByText("No history yet.")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npx vitest run src/features/dcr/DcrStageTimeline.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the component**

`DcrStageTimeline.tsx` (mirrors `CapaTimeline`: a Mantine `<Timeline>`, actor+timestamp on a dimmed sub-line, directory passed in as a prop):

```tsx
import { Text, Timeline } from "@mantine/core";
import type { DcrStageEvent, DirectoryUser } from "../../lib/types";

function actorLabel(actorId: string | null, directory: DirectoryUser[]): string {
  if (!actorId) return "system";
  const hit = directory.find((u) => u.id === actorId);
  return hit?.display_name ?? `${actorId.slice(0, 8)}…`;
}

function formatDate(iso: string): string {
  return new Date(iso).toISOString().slice(0, 10);
}

export function DcrStageTimeline({
  events,
  directory,
}: {
  events: DcrStageEvent[];
  directory: DirectoryUser[];
}) {
  if (events.length === 0) {
    return (
      <Text size="sm" c="dimmed">
        No history yet.
      </Text>
    );
  }
  return (
    <Timeline active={events.length} bulletSize={16} lineWidth={2}>
      {events.map((e) => (
        <Timeline.Item
          key={e.id}
          title={
            <Text span fw={600}>
              {e.from_state ? `${e.from_state} → ${e.to_state}` : e.to_state}
            </Text>
          }
        >
          <Text size="xs" c="dimmed" mb={4}>
            {formatDate(e.occurred_at)} · {actorLabel(e.actor_id, directory)}
          </Text>
          {e.comment ? <Text size="sm">{e.comment}</Text> : null}
        </Timeline.Item>
      ))}
    </Timeline>
  );
}
```

> The `payload` JSONB is intentionally NOT rendered (it is internal system data; rendering it would invite an XSS surface for no read-spine value).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npx vitest run src/features/dcr/DcrStageTimeline.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/dcr/DcrStageTimeline.tsx apps/web/src/features/dcr/DcrStageTimeline.test.tsx
git commit -m "feat(s-dcr-ui-1): DcrStageTimeline"
```

---

### Task 4: `DcrImpactTable`

**Files:**
- Create: `apps/web/src/features/dcr/DcrImpactTable.tsx`
- Test: `apps/web/src/features/dcr/DcrImpactTable.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { expect, it } from "vitest";
import { renderWithProviders } from "../../test/render";
import { DcrImpactTable } from "./DcrImpactTable";
import type { DcrImpact } from "../../lib/types";

const impact: DcrImpact[] = [
  { id: "i1", dimension: "processes", auto_populated: { applicable: true, processes: ["p1", "p2"] }, requester_annotation: "Calibration", created_at: "2026-06-10T10:00:00+00:00", updated_at: null },
  { id: "i2", dimension: "training", auto_populated: { applicable: false }, requester_annotation: null, created_at: "2026-06-10T10:00:00+00:00", updated_at: null },
];

it("renders each dimension with a generic system-facts summary and the annotation or a dash", () => {
  const { getByText } = renderWithProviders(<DcrImpactTable impact={impact} />);
  expect(getByText("processes")).toBeInTheDocument();
  expect(getByText("Applicable · 2 processes")).toBeInTheDocument();
  expect(getByText("Calibration")).toBeInTheDocument();
  expect(getByText("Not applicable")).toBeInTheDocument();
  expect(getByText("—")).toBeInTheDocument(); // null annotation
});

it("shows a not-yet-assessed empty state", () => {
  const { getByText } = renderWithProviders(<DcrImpactTable impact={[]} />);
  expect(getByText("Not yet assessed.")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npx vitest run src/features/dcr/DcrImpactTable.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the component**

`DcrImpactTable.tsx` (renders `auto_populated` as generic TEXT — never raw HTML):

```tsx
import { Table, Text } from "@mantine/core";
import type { DcrImpact } from "../../lib/types";

function summarizeAuto(auto: Record<string, unknown> | null): string {
  if (!auto) return "—";
  if (auto.applicable === false) return "Not applicable";
  const processes = Array.isArray(auto.processes) ? auto.processes.length : null;
  if (processes !== null) return `Applicable · ${processes} process${processes === 1 ? "" : "es"}`;
  return "Applicable";
}

export function DcrImpactTable({ impact }: { impact: DcrImpact[] }) {
  if (impact.length === 0) {
    return (
      <Text size="sm" c="dimmed">
        Not yet assessed.
      </Text>
    );
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npx vitest run src/features/dcr/DcrImpactTable.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/dcr/DcrImpactTable.tsx apps/web/src/features/dcr/DcrImpactTable.test.tsx
git commit -m "feat(s-dcr-ui-1): DcrImpactTable"
```

---

### Task 5: `DcrDrawer`

**Files:**
- Create: `apps/web/src/features/dcr/DcrDrawer.tsx`
- Test: `apps/web/src/features/dcr/DcrDrawer.test.tsx`

Depends on Tasks 1–4. Uses the shared `DetailDrawer` shell (do NOT re-implement Mantine `<Drawer>`), `useDocument` for the target label (calm-degrade), `useUserDirectory` for names.

- [ ] **Step 1: Write the failing test**

```tsx
import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { DcrDrawer } from "./DcrDrawer";
import { DCR_REVISE_ID } from "../../test/msw/handlers";

function renderDrawer(id: string | null = DCR_REVISE_ID) {
  return renderWithProviders(<DcrDrawer dcrId={id} onClose={() => {}} />);
}

it("shows the identifier, state badge, reason, target link, source CAPA link, impact and timeline", async () => {
  const { findByText, getByText, findByRole } = renderDrawer();
  expect(await findByText("DCR-2026-0001")).toBeInTheDocument();
  expect(getByText("State: Open", { exact: false })).toBeInTheDocument; // badge aria-label via getByLabelText below
  expect(await findByText(/Corrective action requires/)).toBeInTheDocument();
  // target resolved via GET /documents/:id (default handler → SOP-001):
  expect(await findByRole("link", { name: /SOP-001/ })).toBeInTheDocument();
  // source CAPA deep-link:
  expect(await findByRole("link", { name: "CAPA" })).toHaveAttribute("href", expect.stringContaining("/capa?capa="));
  // impact + timeline:
  expect(await findByText("Applicable · 2 processes")).toBeInTheDocument();
  expect(await findByText("Change request raised.")).toBeInTheDocument();
});

it("calm-degrades the target link to the bare id when documents read is forbidden", async () => {
  server.use(
    http.get("/api/v1/documents/:id", () => HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 })),
  );
  const { findByRole } = renderDrawer();
  // still a link, but labeled by the bare id (no SOP-001 title resolved):
  const link = await findByRole("link", { name: /doc00001/ });
  expect(link).toHaveAttribute("href", expect.stringContaining("/documents/doc00001"));
});

it("renders nothing-but-the-shell title when dcrId is null", () => {
  const { getByText, queryByText } = renderDrawer(null);
  expect(getByText("Change request")).toBeInTheDocument(); // fallback title
  expect(queryByText("DCR-2026-0001")).not.toBeInTheDocument();
});

it("shows a calm error body on a failed detail load", async () => {
  server.use(
    http.get("/api/v1/dcrs/:id", () => HttpResponse.json({ code: "not_found", title: "Not found" }, { status: 404 })),
  );
  const { findByText } = renderDrawer();
  expect(await findByText("Couldn't load this change request")).toBeInTheDocument();
});
```

> Note the badge state assertion is better written as `expect(getByLabelText("State: Open")).toBeInTheDocument()` — adjust the import to include `getByLabelText` from the render result. The CAPA-link `href` assertion uses `expect.stringContaining`; if your jest-dom version rejects an asymmetric matcher in `toHaveAttribute`, switch to reading `.getAttribute("href")` and asserting `.toContain(...)`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npx vitest run src/features/dcr/DcrDrawer.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the component**

`DcrDrawer.tsx`:

```tsx
import { Alert, Anchor, Badge, Group, Loader, Stack, Text, Title } from "@mantine/core";
import { Link } from "react-router-dom";
import { DetailDrawer } from "../../app/shell/DetailDrawer";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import { useDocument } from "../document/useDocument";
import type { DcrReasonClass, DcrSourceLinkType, DirectoryUser } from "../../lib/types";
import { DcrImpactTable } from "./DcrImpactTable";
import { DcrStageTimeline } from "./DcrStageTimeline";
import { DcrStateBadge } from "./DcrStateBadge";
import { useDcr, useDcrImpact } from "./hooks";

const CHANGE_TYPE_LABEL: Record<string, string> = {
  REVISE: "Revise",
  CREATE: "Create",
  RETIRE: "Retire",
};
const REASON_LABEL: Record<DcrReasonClass, string> = {
  regulatory: "Regulatory",
  audit_finding: "Audit finding",
  capa: "CAPA",
  process_improvement: "Process improvement",
  error_correction: "Error correction",
  periodic_review: "Periodic review",
  customer_requirement: "Customer requirement",
  mgmt_review: "Management review",
  other: "Other",
};
const SOURCE_LABEL: Record<DcrSourceLinkType, string> = {
  capa: "CAPA",
  finding: "Audit finding",
  mgmt_review: "Management-review output",
  risk: "Risk",
};

function nameOf(userId: string, directory: DirectoryUser[]): string {
  return directory.find((u) => u.id === userId)?.display_name ?? `${userId.slice(0, 8)}…`;
}
function formatDate(iso: string): string {
  return new Date(iso).toISOString().slice(0, 10);
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <Text size="xs" c="dimmed">
        {label}
      </Text>
      {typeof children === "string" ? <Text size="sm">{children}</Text> : children}
    </div>
  );
}

export function DcrDrawer({ dcrId, onClose }: { dcrId: string | null; onClose: () => void }) {
  const { data: dcr, isLoading, isError } = useDcr(dcrId);
  const { data: impact } = useDcrImpact(dcrId);
  const { data: directoryData } = useUserDirectory();
  const directory = directoryData ?? [];
  const targetId = dcr?.target_document_id ?? null;
  const { data: targetDoc } = useDocument(targetId, { enabled: targetId !== null, retry: false });

  return (
    <DetailDrawer
      opened={dcrId !== null}
      onClose={onClose}
      title={
        dcr && !isError ? (
          <Stack gap={2}>
            <Text size="xs" c="dimmed">
              {dcr.identifier}
            </Text>
            <Title order={4}>{CHANGE_TYPE_LABEL[dcr.change_type] ?? dcr.change_type}</Title>
          </Stack>
        ) : (
          "Change request"
        )
      }
    >
      {isLoading ? (
        <Loader />
      ) : isError || !dcr ? (
        <Alert color="red" title="Couldn't load this change request">
          It may have been removed, or you may not have access. Close this panel and try again.
        </Alert>
      ) : (
        <Stack gap="lg">
          <Group gap="xs">
            <DcrStateBadge state={dcr.state} />
            <Badge variant="light" color="gray">
              {CHANGE_TYPE_LABEL[dcr.change_type] ?? dcr.change_type}
            </Badge>
            <Badge variant="light" color="gray">
              {dcr.change_significance}
            </Badge>
            <Badge variant="light" color="gray">
              {REASON_LABEL[dcr.reason_class] ?? dcr.reason_class}
            </Badge>
          </Group>

          <Field label="Reason">{dcr.reason_text}</Field>

          <Field label="Target document">
            {dcr.target_document_id ? (
              <Anchor component={Link} to={`/documents/${dcr.target_document_id}`}>
                {targetDoc ? `${targetDoc.identifier} — ${targetDoc.title}` : dcr.target_document_id}
              </Anchor>
            ) : (
              <Text size="sm">New document (no target)</Text>
            )}
          </Field>

          {dcr.source_link_type ? (
            <Field label="Source">
              {dcr.source_link_type === "capa" && dcr.source_link_id ? (
                <Anchor component={Link} to={`/capa?capa=${dcr.source_link_id}`}>
                  {SOURCE_LABEL.capa}
                </Anchor>
              ) : (
                <Text size="sm">
                  {SOURCE_LABEL[dcr.source_link_type]}
                  {dcr.source_link_id ? ` · ${dcr.source_link_id.slice(0, 8)}…` : ""}
                </Text>
              )}
            </Field>
          ) : null}

          {dcr.resulting_version_id ? (
            <Field label="Resulting version">
              {dcr.target_document_id ? (
                <Anchor component={Link} to={`/documents/${dcr.target_document_id}`}>
                  View document
                </Anchor>
              ) : (
                // CREATE: the new document's id is not exposed by _dcr and cannot be resolved
                // from a bare version_id client-side (verified) — show the id, no link.
                <Text size="sm">{dcr.resulting_version_id.slice(0, 8)}… (new document)</Text>
              )}
            </Field>
          ) : null}

          {dcr.proposed_effective_from ? (
            <Field label="Proposed effective from">{formatDate(dcr.proposed_effective_from)}</Field>
          ) : null}

          {dcr.decision ? <Field label="Decision">{dcr.decision}</Field> : null}

          <Field label="Raised by">
            {`${nameOf(dcr.created_by, directory)} · ${formatDate(dcr.created_at)}`}
          </Field>

          <div>
            <Title order={5} mb="xs">
              Impact assessment
            </Title>
            <DcrImpactTable impact={impact ?? []} />
          </div>

          <div>
            <Title order={5} mb="xs">
              History
            </Title>
            <DcrStageTimeline events={dcr.stage_events} directory={directory} />
          </div>
        </Stack>
      )}
    </DetailDrawer>
  );
}
```

> ⚠ Verify `useDocument`'s exact signature against `features/document/useDocument.ts` — it is `useDocument(documentId, { enabled, seed?, retry? })`. If the option key differs, match it. `DocumentSummary` exposes `identifier` + `title`.
> ⚠ If `React.ReactNode` errors without an import, add `import type { ReactNode } from "react"` and use `ReactNode` in `Field`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npx vitest run src/features/dcr/DcrDrawer.test.tsx`
Expected: PASS (adjust the badge assertion to `getByLabelText("State: Open")` per the test note).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/dcr/DcrDrawer.tsx apps/web/src/features/dcr/DcrDrawer.test.tsx
git commit -m "feat(s-dcr-ui-1): DcrDrawer (read-only detail)"
```

---

### Task 6: `DcrsRegisterPage`

**Files:**
- Create: `apps/web/src/features/dcr/DcrsRegisterPage.tsx`
- Test: `apps/web/src/features/dcr/DcrsRegisterPage.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { http, HttpResponse } from "msw";
import { axe } from "jest-axe";
import { expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { DcrsRegisterPage } from "./DcrsRegisterPage";

it("lists change requests and opens the drawer when an identifier is clicked", async () => {
  renderWithProviders(<DcrsRegisterPage />);
  // first content assertion waits for the skeleton to resolve:
  expect(await screen.findByText("DCR-2026-0001")).toBeInTheDocument();
  expect(screen.getByText("DCR-2026-0002")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "DCR-2026-0001" }));
  // the drawer detail loads (reason text is detail-only content):
  expect(await screen.findByText(/Corrective action requires/)).toBeInTheDocument();
});

it("opens the drawer on a ?dcr=<id> deep-link", async () => {
  renderWithProviders(<DcrsRegisterPage />, { route: "/dcrs?dcr=dcr00001-0001-0001-0001-000000000001" });
  expect(await screen.findByText(/Corrective action requires/)).toBeInTheDocument();
});

it("filters by state", async () => {
  renderWithProviders(<DcrsRegisterPage />);
  expect(await screen.findByText("DCR-2026-0001")).toBeInTheDocument();
  const stateSelect = screen.getByLabelText("State");
  await userEvent.click(stateSelect);
  await userEvent.click(await screen.findByRole("option", { name: "Cancelled" }));
  await waitFor(() => expect(screen.queryByText("DCR-2026-0001")).not.toBeInTheDocument());
  expect(screen.getByText("DCR-2026-0004")).toBeInTheDocument();
});

it("shows a calm no-access panel on a 403", async () => {
  server.use(http.get("/api/v1/dcrs", () => HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 })));
  renderWithProviders(<DcrsRegisterPage />);
  expect(await screen.findByText("No access")).toBeInTheDocument();
});

it("shows an empty state when there are no DCRs", async () => {
  server.use(http.get("/api/v1/dcrs", () => HttpResponse.json({ data: [] })));
  renderWithProviders(<DcrsRegisterPage />);
  expect(await screen.findByText("No change requests yet.")).toBeInTheDocument();
});

it("has no accessibility violations", async () => {
  const { container } = renderWithProviders(<DcrsRegisterPage />);
  await screen.findByText("DCR-2026-0001");
  expect(await axe(container)).toHaveNoViolations();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npx vitest run src/features/dcr/DcrsRegisterPage.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the component**

`DcrsRegisterPage.tsx` (mirrors `CapaBoardPage`: `?dcr=<id>` deep-link seam — local opens via `setSelected`, only `closeDrawer` writes the URL `{ replace: true }` guarded on `params.has("dcr")`; filters are local `useState` + `useMemo`; the Identifier is the one interactive element per row → clean a11y, no nested-interactive):

```tsx
import { Alert, Anchor, Container, Group, Loader, Select, Table, Text, Title } from "@mantine/core";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { DcrChangeType, DcrReasonClass, DcrState } from "../../lib/types";
import { DcrDrawer } from "./DcrDrawer";
import { DcrStateBadge } from "./DcrStateBadge";
import { useDcrs } from "./hooks";

const STATES: DcrState[] = [
  "Open", "Assessed", "Routed", "InApproval", "Approved", "Implemented", "Closed", "Cancelled", "Rejected",
];
const CHANGE_TYPES: DcrChangeType[] = ["REVISE", "CREATE", "RETIRE"];
const REASON_CLASSES: DcrReasonClass[] = [
  "regulatory", "audit_finding", "capa", "process_improvement", "error_correction",
  "periodic_review", "customer_requirement", "mgmt_review", "other",
];

function formatDate(iso: string): string {
  return new Date(iso).toISOString().slice(0, 10);
}

export function DcrsRegisterPage() {
  const { data, isLoading, isError, forbidden } = useDcrs();
  const [params, setParams] = useSearchParams();
  const [selected, setSelected] = useState<string | null>(() => params.get("dcr"));
  const [state, setState] = useState<DcrState | "">("");
  const [changeType, setChangeType] = useState<DcrChangeType | "">("");
  const [reason, setReason] = useState<DcrReasonClass | "">("");

  // Open the drawer for ?dcr=<id> on mount + whenever the param changes (a deep-link while mounted).
  // Guarded on a non-null id so clearing the param on close never re-opens the drawer.
  useEffect(() => {
    const dcr = params.get("dcr");
    if (dcr) setSelected(dcr);
  }, [params]);

  function closeDrawer() {
    setSelected(null);
    if (params.has("dcr")) {
      setParams(
        (p) => {
          p.delete("dcr");
          return p;
        },
        { replace: true },
      );
    }
  }

  const rows = data ?? [];
  const filtered = useMemo(
    () =>
      rows.filter(
        (d) =>
          (state === "" || d.state === state) &&
          (changeType === "" || d.change_type === changeType) &&
          (reason === "" || d.reason_class === reason),
      ),
    [rows, state, changeType, reason],
  );

  if (forbidden) {
    return (
      <Container size="md" py="md">
        <Title order={2} mb="md">
          Change requests
        </Title>
        <Alert color="gray" title="No access">
          You don't have access to the change-request register. It's available to roles holding the
          change-request read permission.
        </Alert>
      </Container>
    );
  }
  if (isLoading) {
    return (
      <Container size="md" py="md">
        <Loader />
      </Container>
    );
  }
  if (isError) {
    return (
      <Container size="md" py="md">
        <Title order={2} mb="md">
          Change requests
        </Title>
        <Alert color="red" title="Couldn't load change requests">
          Please try again.
        </Alert>
      </Container>
    );
  }

  return (
    <Container size="xl" py="md">
      <Title order={2} mb="md">
        Change requests
      </Title>
      <Group mb="md" gap="sm">
        <Select
          aria-label="State"
          placeholder="All states"
          clearable
          value={state || null}
          onChange={(v) => setState((v as DcrState) ?? "")}
          data={STATES.map((s) => ({ value: s, label: s }))}
        />
        <Select
          aria-label="Change type"
          placeholder="All change types"
          clearable
          value={changeType || null}
          onChange={(v) => setChangeType((v as DcrChangeType) ?? "")}
          data={CHANGE_TYPES.map((s) => ({ value: s, label: s }))}
        />
        <Select
          aria-label="Reason"
          placeholder="All reasons"
          clearable
          value={reason || null}
          onChange={(v) => setReason((v as DcrReasonClass) ?? "")}
          data={REASON_CLASSES.map((s) => ({ value: s, label: s }))}
        />
      </Group>

      {filtered.length === 0 ? (
        <Text c="dimmed">No change requests yet.</Text>
      ) : (
        <Table highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Identifier</Table.Th>
              <Table.Th>Type</Table.Th>
              <Table.Th>Significance</Table.Th>
              <Table.Th>Reason</Table.Th>
              <Table.Th>Target</Table.Th>
              <Table.Th>State</Table.Th>
              <Table.Th>Created</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {filtered.map((d) => (
              <Table.Tr key={d.id}>
                <Table.Td>
                  <Anchor component="button" type="button" onClick={() => setSelected(d.id)}>
                    {d.identifier}
                  </Anchor>
                </Table.Td>
                <Table.Td>{d.change_type}</Table.Td>
                <Table.Td>{d.change_significance}</Table.Td>
                <Table.Td>{d.reason_class}</Table.Td>
                <Table.Td>{d.target_document_id ? "Document" : "—"}</Table.Td>
                <Table.Td>
                  <DcrStateBadge state={d.state} />
                </Table.Td>
                <Table.Td>{formatDate(d.created_at)}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}

      <DcrDrawer dcrId={selected} onClose={closeDrawer} />
    </Container>
  );
}
```

> The Target column shows "Document"/"—" (non-link) — the real `/documents/{id}` deep-link lives in the drawer, keeping the register row free of nested-interactive elements (jest-axe clean). The Identifier `Anchor component="button"` is the single interactive element per row.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npx vitest run src/features/dcr/DcrsRegisterPage.test.tsx`
Expected: PASS (all six tests). If the `Select` option-click flow differs from your Mantine version, mirror the exact pattern used in an existing CAPA/MR register test.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/dcr/DcrsRegisterPage.tsx apps/web/src/features/dcr/DcrsRegisterPage.test.tsx
git commit -m "feat(s-dcr-ui-1): DcrsRegisterPage (register + filters + drawer)"
```

---

### Task 7: Wire the route + the gated nav entry

**Files:**
- Modify: `apps/web/src/App.tsx` (import + one route)
- Modify: `apps/web/src/app/shell/LeftRail.tsx` (one gated NavLink)

This is a wiring task — verified by `tsc` + build (no new unit test; the page is already proven in Task 6, and the live smoke confirms the nav entry).

- [ ] **Step 1: Add the route to `App.tsx`**

Add the import near the other feature-page imports (pages are imported directly — no lazy/Suspense):

```tsx
import { DcrsRegisterPage } from "./features/dcr/DcrsRegisterPage";
```

Add the route inside the `<Route path="/" element={<AppShell />}>` block, after the `management-reviews` route (~line 147). The drawer is `?dcr=<id>`, so there is NO `/dcrs/:id` route:

```tsx
<Route path="dcrs" element={<DcrsRegisterPage />} />
```

- [ ] **Step 2: Add the gated nav entry to `LeftRail.tsx`**

Insert in the flat top-level entries, after the **Internal Audit** entry (`to="/audits"`), mirroring the objective/mgmtReview gated pattern:

```tsx
{can("changeRequest.read") && (
  // S-dcr-ui-1: gated — changeRequest.read; the change-control (DCR) register.
  <NavLink
    component={Link}
    to="/dcrs"
    label="Change requests"
    active={pathname.startsWith("/dcrs")}
  />
)}
```

> `changeRequest.read` is the confirmed read key (doc-07 catalog, PROCESS finest-scope; the backend `GET /dcrs` gate). `can`, `Link`, `NavLink`, `pathname` are already in scope in `LeftRail.tsx`.

- [ ] **Step 3: Verify it compiles + builds**

Run: `cd apps/web && npx tsc --noEmit && npm run build`
Expected: PASS (tsc clean; vite build succeeds).

- [ ] **Step 4: Commit**

```bash
git add apps/web/src/App.tsx apps/web/src/app/shell/LeftRail.tsx
git commit -m "feat(s-dcr-ui-1): wire /dcrs route + changeRequest.read-gated nav entry"
```

---

### Task 8: Full gate + docs

**Files:**
- Modify: `docs/slice-history.md` (append the S-dcr-ui-1 narrative)
- Modify: `CLAUDE.md` (a Recent-learnings line + the Current-status pointer)

- [ ] **Step 1: Run the full web gate**

Run: `cd apps/web && npm run lint && npx tsc --noEmit && npm run build && npx vitest run`
(Equivalently, invoke the `/check-web` skill.)
Expected: ALL PASS. Strict `noUncheckedIndexedAccess` + the full suite catch cross-file drift the per-file runs miss. Fix any failure before proceeding.

> If `vitest run` mass-fails with "document is not defined" (the known full-parallel-run thrash), re-run with `npx vitest run --pool=forks --poolOptions.forks.singleFork=true` for a clean signal (per the web-vitest-full-run-thrash memory note).

- [ ] **Step 2: Append the slice-history narrative**

Add an `### S-dcr-ui-1 — DCR read spine` entry to `docs/slice-history.md` summarizing: front-end-only, the `features/dcr/` module (register + `?dcr=<id>` drawer + state badge + stage-event timeline + impact table), the three consumed reads + the calm-degrading target/source resolution, the deferrals (writes/spawn-seams → ui-2; visual diff → ui-3; the CREATE resulting-doc link), and the test delta (761 → ~78x).

- [ ] **Step 3: Update CLAUDE.md**

Add a `## Recent learnings` line (newest first) and update the Current-status pointer to note S-dcr-ui-1 ✅ (the DCR domain now has a read spine; lifecycle writes + diff deferred to ui-2/-3). Keep it to one dense line per the file's style.

- [ ] **Step 4: Commit**

```bash
git add docs/slice-history.md CLAUDE.md
git commit -m "docs(s-dcr-ui-1): slice-history + CLAUDE.md learnings"
```

---

## Post-plan (outside the task loop)

1. **diff-critic** on the branch diff (`Agent`, `subagent_type: diff-critic`) — fold only confirmed findings.
2. **Live smoke** (Chrome MCP): rebuild the `web` image (`… up -d --build web`); grant `changeRequest.read` (+ `document.read`) SYSTEM overrides on the LIVE `demo` `app_user` row (org AHT) **before login**; seed a few DCRs across states via the worker heredoc; verify nav → register → filters → drawer (incl. the target-degrade path with `document.read` removed) → timeline/impact/resulting-version link.
3. **PR** via the `/pr` skill after the full local gate is green; squash-merge after green CI on the owner's OK.
4. **Codex triage** after CI — disregard D1-moot multi-tenant framing; verify each claim vs code (Codex repeatedly catches real gaps the diff-critic misses).

---

## Self-review (writing-plans)

**Spec coverage:** register table + filters (Task 6) ✓ · `?dcr=<id>` drawer (Tasks 5–6) ✓ · state badge (Task 2) ✓ · stage-event timeline (Task 3) ✓ · impact panel (Task 4) ✓ · target reference + calm-degrade (Task 5) ✓ · source reference incl. CAPA deep-link (Task 5) ✓ · resulting-version deep-link + CREATE-edge deferral (Task 5) ✓ · raised-by/user resolution via `useUserDirectory` (Tasks 3,5) ✓ · nav entry gated on `changeRequest.read` (Task 7) ✓ · calm-403 page + drawer (Tasks 5–6) ✓ · XSS-safe generic rendering (Tasks 3–4) ✓ · jest-axe smoke (Task 6) ✓ · strictly front-end, `/check-web`-only gate (Task 8) ✓. No spec requirement is unmapped.

**Placeholder scan:** every code step carries complete code; the two "verify against the file" notes (the `_dcr` field count; `useDocument` signature) are deliberate guardrails against the as-built file, not TODOs.

**Type consistency:** `Dcr`/`DcrDetail`/`DcrList`/`DcrStageEvent`/`DcrImpact`/`DcrImpactList` + the 5 enums are defined once (Task 1) and used verbatim across Tasks 2–7; hook names (`useDcrs`/`useDcr`/`useDcrImpact`) and query keys (`["dcrs"]`/`["dcr",id]`/`["dcr-impact",id]`) are consistent; component prop names (`dcrId`, `events`, `impact`, `directory`, `state`) match between definition and call sites.
