# S-obj-2 — Quality Objectives UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the front-end Quality Objectives surface (clause 6.2) — a PLAN-phase register, an objective detail page (commitment + plans + measurement history), and the create + record-measurement + plan write affordances — consuming the live S-obj-1 backend with no migration/key/endpoint/contract change.

**Architecture:** A new flat feature folder `apps/web/src/features/objectives/` (the CAPA/Audits idiom): typed hooks/mutations over `useApi()` + react-query, calm-403 `forbidden` reads, per-key `usePermissions().can(...)` write gating (SYSTEM fallback in v1), Mantine components. Every MSW fixture is `satisfies <Type>`-pinned to the as-built `objectives.py` serializers (the spec §3 data contract). RAG/attainment/pct are read from the server, never recomputed.

**Tech Stack:** React 18 + TypeScript (strict, `noUncheckedIndexedAccess`), Mantine, `@tanstack/react-query`, react-router-dom, MSW v2, vitest + @testing-library/react + jest-axe.

**Spec:** `docs/superpowers/specs/2026-06-11-s-obj-2-quality-objectives-ui-design.md` (approved). Read §3 (data contract) before any fixture.

**Plan-time decision (divergence from spec §5.2):** the create form **omits the policy field in v1**. The backend requires `policy_id` to equal the Effective Quality Policy singleton or be null, and there is **no** API endpoint to fetch that id (confirmed: no policy read in `apps/api/src/easysynq_api/api`). Rather than build a fragile document-type→documents lookup chain, v1 sends no `policy_id`; "link to Quality Policy on create" is a named deferral pending an Effective-POL lookup endpoint. Everything else in §5.2 stands.

---

## File structure

```
apps/web/src/
  lib/types.ts                         (modify: append the Objective family types — Task 1)
  test/msw/handlers.ts                 (modify: add objective fixtures + 8 handlers — Task 1)
  features/objectives/
    labels.ts                          (create — Task 2)  RAG/attainment/direction maps, fmtValueUnit, bandZones
    labels.test.ts                     (create — Task 2)
    hooks.ts                           (create — Task 3)  useObjectiveScorecard/useObjective/useObjectiveMeasurements/useProcesses
    hooks.test.tsx                     (create — Task 3)
    mutations.ts                       (create — Task 12/13/14) useCreateObjective/useRecordMeasurement/useAddPlan/useRemovePlan
    ObjectiveScorecardBand.tsx         (create — Task 4)
    ObjectiveScorecardBand.test.tsx    (create — Task 4)
    ObjectivesRegisterPage.tsx         (create — Task 5)
    ObjectivesRegisterPage.test.tsx    (create — Task 5)
    CommitmentHero.tsx                 (create — Task 7)
    CommitmentHero.test.tsx            (create — Task 7)
    PlansSection.tsx                   (create — Task 8, extended Task 14)
    PlansSection.test.tsx              (create — Task 8, extended Task 14)
    MeasurementsSection.tsx            (create — Task 9, extended Task 13)
    MeasurementsSection.test.tsx       (create — Task 9, extended Task 13)
    ObjectiveDetailPage.tsx            (create — Task 10)
    ObjectiveDetailPage.test.tsx       (create — Task 10)
    BandPreview.tsx                    (create — Task 11)
    BandPreview.test.tsx               (create — Task 11)
    NewObjectiveModal.tsx              (create — Task 12)
    NewObjectiveModal.test.tsx         (create — Task 12)
    RecordMeasurementModal.tsx         (create — Task 13)
    AddPlanModal.tsx                   (create — Task 14)
  App.tsx                              (modify: 2 routes — Task 15)
  app/shell/LeftRail.tsx               (modify: gated nav entry — Task 15)
```

Constants reused: `DETAIL_ID = "ob000001-0001-0001-0001-000000000001"` (the detail/measurement fixture id) and `TEST_AUTH.sub = "bbbb1111-1111-1111-1111-111111111111"`.

---

## Phase 1 — Foundation

### Task 1: Types + MSW fixtures + handlers

**Files:**
- Modify: `apps/web/src/lib/types.ts` (append after the `RoleSummary` interface at EOF, ~line 1078)
- Modify: `apps/web/src/test/msw/handlers.ts` (add fixtures near the other fixtures; add handlers into the `handlers` array — scorecard BEFORE the `:id` handler)

- [ ] **Step 1: Append the Objective family types to `lib/types.ts`**

```ts
// ---- S-obj-2 Quality Objectives (clause 6.2) — pinned to api/objectives.py serializers ----
export type ObjectiveDirection = "HIGHER_IS_BETTER" | "LOWER_IS_BETTER";
export type ObjectiveRag = "green" | "amber" | "red" | "unmeasured";
export type ObjectiveAttainment = "in_progress" | "met" | "missed";
export type ObjectiveState =
  | "Draft" | "InReview" | "Approved" | "Effective"
  | "UnderRevision" | "Superseded" | "Obsolete";

export interface ObjectivePlan {
  id: string;
  objective_id: string;
  action: string;
  resource: string | null;
  responsible_user_id: string | null;
  due_date: string | null;
}

export interface Objective {
  id: string;
  identifier: string;
  title: string;
  current_state: ObjectiveState;
  target_value: string; // decimal string
  unit: string;
  baseline_value: string | null;
  current_value: string | null;
  direction: ObjectiveDirection;
  at_risk_threshold: string | null;
  due_date: string; // ISO date
  process_id: string | null;
  policy_id: string | null;
  rag: ObjectiveRag;
  pct_toward_target: number | null; // JSON number | null — NOT a string
  attainment: ObjectiveAttainment;
  plans: ObjectivePlan[]; // [] in list/scorecard rows; populated on detail GET
}

export interface Measurement {
  id: string;
  objective_id: string | null;
  record_id: string;
  period: string; // ISO date
  value: string; // decimal string
  target_at_capture: string; // decimal string
  unit: string;
  source: string | null;
  created_at: string; // ISO date-time
}

export interface ObjectiveScorecard {
  total: number;
  on_target: number;
  by_rag: { green: number; amber: number; red: number; unmeasured: number };
  objectives: Objective[];
}

export interface ObjectiveListResponse { data: Objective[] }
export interface MeasurementListResponse { data: Measurement[] }

export interface ObjectiveCreateBody {
  title: string;
  target_value: string;
  unit: string;
  direction: ObjectiveDirection;
  due_date: string;
  baseline_value?: string | null;
  at_risk_threshold?: string | null;
  process_id?: string | null;
  policy_id?: string | null;
}
export interface MeasurementCreateBody {
  period: string;
  value: string;
  unit: string;
  source?: string | null;
}
export interface PlanCreateBody {
  action: string;
  resource?: string | null;
  responsible_user_id?: string | null;
  due_date?: string | null;
}
```

- [ ] **Step 2: Add fixtures + handlers to `handlers.ts`**

Add these fixtures above the `export const handlers = [` line (import the types at the top of the file alongside the existing type imports: `Objective, ObjectivePlan, Measurement, ObjectiveScorecard, ObjectiveListResponse, MeasurementListResponse`):

```ts
const OBJ_DETAIL_ID = "ob000001-0001-0001-0001-000000000001";

const objectiveFixtures: Objective[] = [
  {
    id: OBJ_DETAIL_ID,
    identifier: "OBJ-001",
    title: "On-time delivery rate",
    current_state: "Draft",
    target_value: "95",
    unit: "%",
    baseline_value: "80",
    current_value: "92",
    direction: "HIGHER_IS_BETTER",
    at_risk_threshold: "90",
    due_date: "2026-12-31",
    process_id: "70000000-0000-0000-0000-000000000001",
    policy_id: null,
    rag: "amber",
    pct_toward_target: 0.8,
    attainment: "in_progress",
    plans: [],
  },
  {
    id: "ob000002-0002-0002-0002-000000000002",
    identifier: "OBJ-002",
    title: "Customer complaints per quarter",
    current_state: "Draft",
    target_value: "5",
    unit: "complaints",
    baseline_value: null,
    current_value: "7",
    direction: "LOWER_IS_BETTER",
    at_risk_threshold: null,
    due_date: "2026-12-31",
    process_id: null,
    policy_id: null,
    rag: "red",
    pct_toward_target: null,
    attainment: "in_progress",
    plans: [],
  },
  {
    id: "ob000003-0003-0003-0003-000000000003",
    identifier: "OBJ-003",
    title: "First-pass yield",
    current_state: "Draft",
    target_value: "98",
    unit: "%",
    baseline_value: "90",
    current_value: "99",
    direction: "HIGHER_IS_BETTER",
    at_risk_threshold: "95",
    due_date: "2026-12-31",
    process_id: null,
    policy_id: null,
    rag: "green",
    pct_toward_target: 1.125,
    attainment: "in_progress",
    plans: [],
  },
  {
    id: "ob000004-0004-0004-0004-000000000004",
    identifier: "OBJ-004",
    title: "Supplier defect rate",
    current_state: "Draft",
    target_value: "2",
    unit: "%",
    baseline_value: null,
    current_value: null,
    direction: "LOWER_IS_BETTER",
    at_risk_threshold: null,
    due_date: "2026-12-31",
    process_id: null,
    policy_id: null,
    rag: "unmeasured",
    pct_toward_target: null,
    attainment: "in_progress",
    plans: [],
  },
] satisfies Objective[];

const objectivePlanFixtures: ObjectivePlan[] = [
  {
    id: "pl000001-0001-0001-0001-000000000001",
    objective_id: OBJ_DETAIL_ID,
    action: "Add a second carrier to the south region",
    resource: "Logistics budget",
    responsible_user_id: "bbbb1111-1111-1111-1111-111111111111",
    due_date: "2026-09-30",
  },
] satisfies ObjectivePlan[];

const objectiveDetailFixture: Objective = {
  ...objectiveFixtures[0]!,
  plans: objectivePlanFixtures,
} satisfies Objective;

const measurementFixtures: Measurement[] = [
  {
    id: "me000002-0002-0002-0002-000000000002",
    objective_id: OBJ_DETAIL_ID,
    record_id: "re000002-0002-0002-0002-000000000002",
    period: "2026-04-01",
    value: "92",
    target_at_capture: "95",
    unit: "%",
    source: "Logistics MIS",
    created_at: "2026-06-02T09:00:00+00:00",
  },
  {
    id: "me000001-0001-0001-0001-000000000001",
    objective_id: OBJ_DETAIL_ID,
    record_id: "re000001-0001-0001-0001-000000000001",
    period: "2026-01-01",
    value: "88",
    target_at_capture: "95",
    unit: "%",
    source: "Logistics MIS",
    created_at: "2026-04-04T09:00:00+00:00",
  },
] satisfies Measurement[];
```

Add these handlers INSIDE the `handlers` array (place the two GET `/objectives/scorecard` + `/objectives` BEFORE the GET `/objectives/:id` so the literal scorecard path isn't captured by `:id`):

```ts
  // ---- S-obj-2 Quality Objectives (default happy-path; per-test overrides for 403/empty/error) ----
  http.get("/api/v1/objectives/scorecard", ({ request }) => {
    const pid = new URL(request.url).searchParams.get("process_id");
    const rows = pid ? objectiveFixtures.filter((o) => o.process_id === pid) : objectiveFixtures;
    const by_rag = { green: 0, amber: 0, red: 0, unmeasured: 0 };
    for (const o of rows) by_rag[o.rag] += 1;
    return HttpResponse.json({
      total: rows.length,
      on_target: by_rag.green,
      by_rag,
      objectives: rows,
    } satisfies ObjectiveScorecard);
  }),
  http.get("/api/v1/objectives", ({ request }) => {
    const pid = new URL(request.url).searchParams.get("process_id");
    const rows = pid ? objectiveFixtures.filter((o) => o.process_id === pid) : objectiveFixtures;
    return HttpResponse.json({ data: rows } satisfies ObjectiveListResponse);
  }),
  http.get("/api/v1/objectives/:id", () => HttpResponse.json(objectiveDetailFixture)),
  http.get("/api/v1/objectives/:id/measurements", () =>
    HttpResponse.json({ data: measurementFixtures } satisfies MeasurementListResponse),
  ),
  http.post("/api/v1/objectives", () => HttpResponse.json(objectiveDetailFixture, { status: 201 })),
  http.post("/api/v1/objectives/:id/measurements", () =>
    HttpResponse.json(measurementFixtures[0]!, { status: 201 }),
  ),
  http.post("/api/v1/objectives/:id/plans", () =>
    HttpResponse.json(objectivePlanFixtures[0]!, { status: 201 }),
  ),
  http.delete("/api/v1/objectives/:id/plans/:planId", () => new HttpResponse(null, { status: 204 })),
```

- [ ] **Step 3: Run typecheck to verify the fixtures satisfy the types**

Run: `cd apps/web && npx tsc --noEmit`
Expected: PASS (no type errors). The `satisfies` clauses prove the fixtures match the serializer shapes.

- [ ] **Step 4: Run the existing suite to confirm no regression**

Run: `cd apps/web && npx vitest run`
Expected: PASS — the new default handlers don't affect existing tests; capture the baseline test count (printed as "Tests N passed").

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/lib/types.ts apps/web/src/test/msw/handlers.ts
git commit -m "feat(s-obj-2): objective types + MSW fixtures/handlers pinned to objectives.py"
```

---

## Phase 1 — Foundation (cont.)

### Task 2: `labels.ts` — maps, formatters, and the pure `bandZones`

**Files:**
- Create: `apps/web/src/features/objectives/labels.ts`
- Test: `apps/web/src/features/objectives/labels.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
import { describe, expect, it } from "vitest";
import { bandZones, fmtValueUnit, RAG_COLOR, RAG_LABEL } from "./labels";

describe("fmtValueUnit", () => {
  it("renders a value and unit, or an em dash when null", () => {
    expect(fmtValueUnit("92", "%")).toBe("92 %");
    expect(fmtValueUnit(null, "%")).toBe("—");
  });
});

describe("RAG maps", () => {
  it("maps every rag to a Mantine colour and a label", () => {
    expect(RAG_COLOR.amber).toBe("yellow");
    expect(RAG_LABEL.unmeasured).toBe("Unmeasured");
  });
});

describe("bandZones", () => {
  it("HIGHER with a valid threshold below target → red|amber|green, no warning", () => {
    const m = bandZones({ target: 95, threshold: 90, direction: "HIGHER_IS_BETTER" });
    expect(m.zones).toEqual(["red", "amber", "green"]);
    expect(m.hasAmber).toBe(true);
    expect(m.warn).toBeNull();
  });

  it("HIGHER with no threshold → red|green and no amber", () => {
    const m = bandZones({ target: 95, threshold: null, direction: "HIGHER_IS_BETTER" });
    expect(m.zones).toEqual(["red", "green"]);
    expect(m.hasAmber).toBe(false);
    expect(m.warn).toBeNull();
  });

  it("HIGHER with a threshold at/above target → soft warning, no amber", () => {
    const m = bandZones({ target: 95, threshold: 96, direction: "HIGHER_IS_BETTER" });
    expect(m.zones).toEqual(["red", "green"]);
    expect(m.hasAmber).toBe(false);
    expect(m.warn).toMatch(/below the target/i);
  });

  it("LOWER with a valid threshold above target → green|amber|red", () => {
    const m = bandZones({ target: 5, threshold: 8, direction: "LOWER_IS_BETTER" });
    expect(m.zones).toEqual(["green", "amber", "red"]);
    expect(m.hasAmber).toBe(true);
    expect(m.warn).toBeNull();
  });

  it("LOWER with a threshold at/below target → soft warning, no amber", () => {
    const m = bandZones({ target: 5, threshold: 4, direction: "LOWER_IS_BETTER" });
    expect(m.zones).toEqual(["green", "red"]);
    expect(m.warn).toMatch(/above the target/i);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npx vitest run src/features/objectives/labels.test.ts`
Expected: FAIL ("Failed to resolve import ./labels").

- [ ] **Step 3: Write `labels.ts`**

```ts
import type { ObjectiveAttainment, ObjectiveDirection, ObjectiveRag } from "../../lib/types";

export const RAG_COLOR: Record<ObjectiveRag, string> = {
  green: "green",
  amber: "yellow",
  red: "red",
  unmeasured: "gray",
};

export const RAG_LABEL: Record<ObjectiveRag, string> = {
  green: "Green",
  amber: "Amber",
  red: "Red",
  unmeasured: "Unmeasured",
};

export const ATTAINMENT_LABEL: Record<ObjectiveAttainment, string> = {
  in_progress: "In progress",
  met: "Met",
  missed: "Missed",
};

export const DIRECTION_LABEL: Record<ObjectiveDirection, string> = {
  HIGHER_IS_BETTER: "Higher is better",
  LOWER_IS_BETTER: "Lower is better",
};

// Decimal-string value + unit, or an em dash when unmeasured.
export function fmtValueUnit(value: string | null, unit: string): string {
  if (value === null) return "—";
  return `${value} ${unit}`.trim();
}

export type RagZone = "red" | "amber" | "green";

export interface BandModel {
  zones: RagZone[]; // left→right display order
  hasAmber: boolean; // a valid amber band exists
  warn: string | null; // a soft, non-blocking warning when the threshold is on the wrong side
}

// Pure: derive the green/amber/red display zones + a soft warning from the target, the optional
// at-risk threshold, and the direction. The amber band only exists when the threshold sits on the
// correct side of the target (below, for higher-is-better; above, for lower-is-better). A backwards
// threshold collapses to red on the server, so we warn (never block) the author.
export function bandZones(args: {
  target: number;
  threshold: number | null;
  direction: ObjectiveDirection;
}): BandModel {
  const { target, threshold, direction } = args;
  if (direction === "HIGHER_IS_BETTER") {
    const validAmber = threshold !== null && threshold < target;
    const warn =
      threshold !== null && threshold >= target
        ? "The at-risk threshold should be below the target for a “higher is better” objective."
        : null;
    return { zones: validAmber ? ["red", "amber", "green"] : ["red", "green"], hasAmber: validAmber, warn };
  }
  const validAmber = threshold !== null && threshold > target;
  const warn =
    threshold !== null && threshold <= target
      ? "The at-risk threshold should be above the target for a “lower is better” objective."
      : null;
  return { zones: validAmber ? ["green", "amber", "red"] : ["green", "red"], hasAmber: validAmber, warn };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npx vitest run src/features/objectives/labels.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/objectives/labels.ts apps/web/src/features/objectives/labels.test.ts
git commit -m "feat(s-obj-2): RAG/attainment labels + pure bandZones (direction-aware amber)"
```

---

## Phase 2 — Register

### Task 3: Read hooks

**Files:**
- Create: `apps/web/src/features/objectives/hooks.ts`
- Test: `apps/web/src/features/objectives/hooks.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { AuthContext } from "../../lib/auth";
import { TEST_AUTH } from "../../test/render";
import { server } from "../../test/msw/server";
import { useObjective, useObjectiveScorecard } from "./hooks";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <AuthContext.Provider value={TEST_AUTH}>{children}</AuthContext.Provider>
    </QueryClientProvider>
  );
}

it("useObjectiveScorecard returns the rollup + rows", async () => {
  const { result } = renderHook(() => useObjectiveScorecard(), { wrapper });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data?.total).toBe(4);
  expect(result.current.data?.by_rag.green).toBe(1);
  expect(result.current.forbidden).toBe(false);
});

it("useObjectiveScorecard sets forbidden on a 403", async () => {
  server.use(
    http.get("/api/v1/objectives/scorecard", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  const { result } = renderHook(() => useObjectiveScorecard(), { wrapper });
  await waitFor(() => expect(result.current.isError).toBe(true));
  expect(result.current.forbidden).toBe(true);
});

it("useObjective loads a single objective with plans", async () => {
  const { result } = renderHook(() => useObjective("ob000001-0001-0001-0001-000000000001"), { wrapper });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data?.plans).toHaveLength(1);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npx vitest run src/features/objectives/hooks.test.tsx`
Expected: FAIL ("Failed to resolve import ./hooks").

- [ ] **Step 3: Write `hooks.ts`**

```ts
import { useQuery } from "@tanstack/react-query";
import { ApiError, useApi } from "../../lib/api";
import type {
  MeasurementListResponse, Objective, ObjectiveScorecard, ProcessRow,
} from "../../lib/types";

function forbiddenOf(error: unknown): boolean {
  return error instanceof ApiError && error.status === 403;
}

export function useObjectiveScorecard(processId: string | null = null) {
  const api = useApi();
  const qs = processId ? `?process_id=${encodeURIComponent(processId)}` : "";
  const query = useQuery({
    queryKey: ["objectives-scorecard", processId],
    queryFn: () => api.get<ObjectiveScorecard>(`/api/v1/objectives/scorecard${qs}`),
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}

export function useObjective(id: string | null) {
  const api = useApi();
  const query = useQuery({
    queryKey: ["objective", id],
    queryFn: () => api.get<Objective>(`/api/v1/objectives/${id!}`),
    enabled: id !== null,
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}

export function useObjectiveMeasurements(id: string | null) {
  const api = useApi();
  const query = useQuery({
    queryKey: ["objective-measurements", id],
    queryFn: async () =>
      (await api.get<MeasurementListResponse>(`/api/v1/objectives/${id!}/measurements`)).data,
    enabled: id !== null,
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}

// GET /processes (bare array) — the optional process picker/filter source. Degrade (omit) on a 403.
export function useProcesses() {
  const api = useApi();
  const query = useQuery({
    queryKey: ["processes"],
    queryFn: () => api.get<ProcessRow[]>("/api/v1/processes"),
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npx vitest run src/features/objectives/hooks.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/objectives/hooks.ts apps/web/src/features/objectives/hooks.test.tsx
git commit -m "feat(s-obj-2): objective read hooks (scorecard/objective/measurements/processes)"
```

---

### Task 4: `ObjectiveScorecardBand`

**Files:**
- Create: `apps/web/src/features/objectives/ObjectiveScorecardBand.tsx`
- Test: `apps/web/src/features/objectives/ObjectiveScorecardBand.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { renderWithProviders } from "../../test/render";
import { ObjectiveScorecardBand } from "./ObjectiveScorecardBand";

const BY_RAG = { green: 1, amber: 1, red: 1, unmeasured: 1 };

it("renders the on-target headline and each RAG count, accessibly", async () => {
  const { container } = renderWithProviders(
    <ObjectiveScorecardBand total={4} onTarget={1} byRag={BY_RAG} />,
  );
  expect(screen.getByText(/1\s*\/\s*4 on target/i)).toBeInTheDocument();
  expect(screen.getByText("1 green")).toBeInTheDocument();
  expect(screen.getByText("1 amber")).toBeInTheDocument();
  expect(screen.getByText("1 red")).toBeInTheDocument();
  expect(screen.getByText("1 unmeasured")).toBeInTheDocument();
  expect(await axe(container)).toHaveNoViolations();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npx vitest run src/features/objectives/ObjectiveScorecardBand.test.tsx`
Expected: FAIL ("Failed to resolve import ./ObjectiveScorecardBand").

- [ ] **Step 3: Write `ObjectiveScorecardBand.tsx`**

```tsx
import { Badge, Group, Paper, Text } from "@mantine/core";
import type { ObjectiveScorecard } from "../../lib/types";

interface Props {
  total: number;
  onTarget: number;
  byRag: ObjectiveScorecard["by_rag"];
}

const CHIPS: { key: keyof ObjectiveScorecard["by_rag"]; color: string }[] = [
  { key: "green", color: "green" },
  { key: "amber", color: "yellow" },
  { key: "red", color: "red" },
  { key: "unmeasured", color: "gray" },
];

export function ObjectiveScorecardBand({ total, onTarget, byRag }: Props) {
  return (
    <Paper withBorder p="md" radius="md" bg="var(--mantine-color-gray-0)">
      <Group justify="space-between" wrap="wrap">
        <Text>
          <Text span fw={600} fz="xl">
            {onTarget}
          </Text>{" "}
          / {total} on target
        </Text>
        <Group gap="xs">
          {CHIPS.map((c) => (
            <Badge key={c.key} color={c.color} variant="light">
              {byRag[c.key]} {c.key}
            </Badge>
          ))}
        </Group>
      </Group>
    </Paper>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npx vitest run src/features/objectives/ObjectiveScorecardBand.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/objectives/ObjectiveScorecardBand.tsx apps/web/src/features/objectives/ObjectiveScorecardBand.test.tsx
git commit -m "feat(s-obj-2): ObjectiveScorecardBand (on-target headline + RAG chips)"
```

---

### Task 5: `ObjectivesRegisterPage` (band + table + filter + empty/forbidden)

**Files:**
- Create: `apps/web/src/features/objectives/ObjectivesRegisterPage.tsx`
- Test: `apps/web/src/features/objectives/ObjectivesRegisterPage.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { axe } from "jest-axe";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { ObjectivesRegisterPage } from "./ObjectivesRegisterPage";

it("renders the band and a row per objective with a RAG status badge", async () => {
  const { container } = renderWithProviders(<ObjectivesRegisterPage />, { route: "/objectives" });
  await waitFor(() => expect(screen.getByText("OBJ-001")).toBeInTheDocument());
  expect(screen.getByText(/1\s*\/\s*4 on target/i)).toBeInTheDocument();
  const row = screen.getByText("On-time delivery rate").closest("tr")!;
  expect(within(row).getByText("Amber")).toBeInTheDocument();
  expect(within(row).getByText("92 / 95 %")).toBeInTheDocument();
  // unmeasured row shows an em dash for the current value
  const unmeasured = screen.getByText("Supplier defect rate").closest("tr")!;
  expect(within(unmeasured).getByText("— / 2 %")).toBeInTheDocument();
  expect(await axe(container)).toHaveNoViolations();
});

it("shows a calm no-access panel on a 403", async () => {
  server.use(
    http.get("/api/v1/objectives/scorecard", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  renderWithProviders(<ObjectivesRegisterPage />, { route: "/objectives" });
  await waitFor(() =>
    expect(screen.getByText(/don't have access to quality objectives/i)).toBeInTheDocument(),
  );
});

it("shows an empty state when there are no objectives", async () => {
  server.use(
    http.get("/api/v1/objectives/scorecard", () =>
      HttpResponse.json({ total: 0, on_target: 0, by_rag: { green: 0, amber: 0, red: 0, unmeasured: 0 }, objectives: [] }),
    ),
  );
  renderWithProviders(<ObjectivesRegisterPage />, { route: "/objectives" });
  await waitFor(() => expect(screen.getByText(/no quality objectives yet/i)).toBeInTheDocument());
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npx vitest run src/features/objectives/ObjectivesRegisterPage.test.tsx`
Expected: FAIL (import).

- [ ] **Step 3: Write `ObjectivesRegisterPage.tsx`**

```tsx
import { Alert, Anchor, Badge, Container, Group, Loader, Table, Text, Title } from "@mantine/core";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import type { Objective, ObjectiveRag } from "../../lib/types";
import { usePermissions } from "../../app/shell/usePermissions";
import { useObjectiveScorecard } from "./hooks";
import { fmtValueUnit, RAG_COLOR, RAG_LABEL } from "./labels";
import { ObjectiveScorecardBand } from "./ObjectiveScorecardBand";

function currentOverTarget(o: Objective): string {
  return `${fmtValueUnit(o.current_value, "").trim() || "—"} / ${o.target_value} ${o.unit}`.trim();
}

export function ObjectivesRegisterPage() {
  const { data, isLoading, forbidden } = useObjectiveScorecard();
  const { can } = usePermissions();
  const [rag, setRag] = useState<ObjectiveRag | "">("");

  const rows = useMemo(
    () => (data?.objectives ?? []).filter((o) => rag === "" || o.rag === rag),
    [data, rag],
  );

  if (forbidden) {
    return (
      <Container size="lg" py="md">
        <Title order={2} mb="md">Quality objectives</Title>
        <Alert color="gray" title="No access">
          You don't have access to Quality Objectives. It's available to the Quality Manager and
          Process Owner roles.
        </Alert>
      </Container>
    );
  }

  if (isLoading || !data) {
    return (
      <Container size="lg" py="md">
        <Loader />
      </Container>
    );
  }

  return (
    <Container size="lg" py="md">
      <Group justify="space-between" mb="md">
        <Title order={2}>Quality objectives</Title>
        {/* The New-objective button (gated objective.manage) is wired in Task 12. */}
      </Group>

      <ObjectiveScorecardBand total={data.total} onTarget={data.on_target} byRag={data.by_rag} />

      {data.objectives.length === 0 ? (
        <Alert color="gray" title="No quality objectives yet" mt="md">
          {can("objective.manage")
            ? "Create the first objective to start tracking progress against target."
            : "No objectives have been set up yet."}
        </Alert>
      ) : (
        <Table striped highlightOnHover mt="md">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Ref</Table.Th>
              <Table.Th>Objective</Table.Th>
              <Table.Th>Current / target</Table.Th>
              <Table.Th>Status</Table.Th>
              <Table.Th>Due</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((o) => (
              <Table.Tr key={o.id}>
                <Table.Td>
                  <Anchor component={Link} to={`/objectives/${o.id}`}>
                    {o.identifier}
                  </Anchor>
                </Table.Td>
                <Table.Td>
                  <Text lineClamp={1}>{o.title}</Text>
                </Table.Td>
                <Table.Td>{currentOverTarget(o)}</Table.Td>
                <Table.Td>
                  <Badge color={RAG_COLOR[o.rag]} variant="light">
                    {RAG_LABEL[o.rag]}
                  </Badge>
                </Table.Td>
                <Table.Td>{o.due_date}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
    </Container>
  );
}
```

> Note: `currentOverTarget` yields e.g. `92 / 95 %` and `— / 2 %`. The RAG filter `setRag` is wired but the chip UI is intentionally minimal in v1; the executor MAY add filter chips, but keep one accessible name per control (a duplicate `aria-label` breaks `getByLabelText` — engineering-patterns).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npx vitest run src/features/objectives/ObjectivesRegisterPage.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/objectives/ObjectivesRegisterPage.tsx apps/web/src/features/objectives/ObjectivesRegisterPage.test.tsx
git commit -m "feat(s-obj-2): ObjectivesRegisterPage (band + register table + empty/forbidden)"
```

---

## Phase 3 — Detail (read)

### Task 6: (covered by Task 3) — `useObjective` / `useObjectiveMeasurements` already exist

No new work; the detail-read hooks were created in Task 3. Proceed to the detail components.

### Task 7: `CommitmentHero`

**Files:**
- Create: `apps/web/src/features/objectives/CommitmentHero.tsx`
- Test: `apps/web/src/features/objectives/CommitmentHero.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { renderWithProviders } from "../../test/render";
import type { Objective } from "../../lib/types";
import { CommitmentHero } from "./CommitmentHero";

const OBJ: Objective = {
  id: "x", identifier: "OBJ-001", title: "On-time delivery rate", current_state: "Draft",
  target_value: "95", unit: "%", baseline_value: "80", current_value: "92",
  direction: "HIGHER_IS_BETTER", at_risk_threshold: "90", due_date: "2026-12-31",
  process_id: null, policy_id: null, rag: "amber", pct_toward_target: 0.8,
  attainment: "in_progress", plans: [],
};

it("shows current vs target, the RAG and attainment badges, and the meta", async () => {
  const { container } = renderWithProviders(<CommitmentHero objective={OBJ} />);
  expect(screen.getByText("92")).toBeInTheDocument();
  expect(screen.getByText(/target 95\s*%/i)).toBeInTheDocument();
  expect(screen.getByText("Amber")).toBeInTheDocument();
  expect(screen.getByText("In progress")).toBeInTheDocument();
  expect(screen.getByText("Higher is better")).toBeInTheDocument();
  expect(await axe(container)).toHaveNoViolations();
});

it("renders an em dash and no progress bar when unmeasured", () => {
  renderWithProviders(
    <CommitmentHero objective={{ ...OBJ, current_value: null, rag: "unmeasured", pct_toward_target: null }} />,
  );
  expect(screen.getByText("—")).toBeInTheDocument();
  expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npx vitest run src/features/objectives/CommitmentHero.test.tsx`
Expected: FAIL (import).

- [ ] **Step 3: Write `CommitmentHero.tsx`**

```tsx
import { Badge, Group, Paper, Progress, SimpleGrid, Stack, Text } from "@mantine/core";
import type { Objective } from "../../lib/types";
import { ATTAINMENT_LABEL, DIRECTION_LABEL, RAG_COLOR, RAG_LABEL } from "./labels";

function clampPct(pct: number | null): number | null {
  if (pct === null) return null;
  return Math.max(0, Math.min(100, Math.round(pct * 100)));
}

function MetaRow({ label, value }: { label: string; value: string }) {
  return (
    <Group justify="space-between">
      <Text c="dimmed" size="sm">{label}</Text>
      <Text size="sm">{value}</Text>
    </Group>
  );
}

export function CommitmentHero({ objective: o }: { objective: Objective }) {
  const pct = clampPct(o.pct_toward_target);
  const baselineToRisk =
    o.baseline_value || o.at_risk_threshold
      ? `${o.baseline_value ?? "—"} → ${o.at_risk_threshold ?? "—"}`
      : "—";
  return (
    <Paper withBorder p="md" radius="md" bg="var(--mantine-color-gray-0)">
      <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="lg">
        <Stack gap="xs">
          <Group align="baseline" gap={6}>
            <Text fw={600} fz={32}>{o.current_value ?? "—"}</Text>
            <Text c="dimmed">{o.current_value ? o.unit : ""} · target {o.target_value} {o.unit}</Text>
          </Group>
          {pct !== null && <Progress value={pct} color={RAG_COLOR[o.rag]} aria-label="Progress toward target" />}
          <Group gap="xs">
            <Badge color={RAG_COLOR[o.rag]} variant="light">{RAG_LABEL[o.rag]}</Badge>
            <Badge color="gray" variant="light">{ATTAINMENT_LABEL[o.attainment]}</Badge>
          </Group>
        </Stack>
        <Stack gap={4}>
          <MetaRow label="Direction" value={DIRECTION_LABEL[o.direction]} />
          <MetaRow label="Baseline → at-risk" value={baselineToRisk} />
          <MetaRow label="Due" value={o.due_date} />
        </Stack>
      </SimpleGrid>
    </Paper>
  );
}
```

> Process/policy links are omitted from the hero in v1 (process names need `useUserDirectory`/`useProcesses` resolution and policy linkage is deferred — see the plan header). The executor MAY add a process-name row using `useProcesses()` if `process.read` is available; degrade to the raw id or omit otherwise.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npx vitest run src/features/objectives/CommitmentHero.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/objectives/CommitmentHero.tsx apps/web/src/features/objectives/CommitmentHero.test.tsx
git commit -m "feat(s-obj-2): CommitmentHero (current vs target, RAG + attainment, meta)"
```

---

### Task 8: `PlansSection` (read-only list first)

**Files:**
- Create: `apps/web/src/features/objectives/PlansSection.tsx`
- Test: `apps/web/src/features/objectives/PlansSection.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../../test/render";
import type { ObjectivePlan } from "../../lib/types";
import { PlansSection } from "./PlansSection";

const PLANS: ObjectivePlan[] = [
  { id: "p1", objective_id: "o1", action: "Add a second carrier", resource: "Logistics budget",
    responsible_user_id: "bbbb1111-1111-1111-1111-111111111111", due_date: "2026-09-30" },
];

it("lists each plan's action and due date", () => {
  renderWithProviders(<PlansSection objectiveId="o1" plans={PLANS} />);
  expect(screen.getByText("Add a second carrier")).toBeInTheDocument();
  expect(screen.getByText(/2026-09-30/)).toBeInTheDocument();
});

it("shows an empty hint when there are no plans", () => {
  renderWithProviders(<PlansSection objectiveId="o1" plans={[]} />);
  expect(screen.getByText(/no plans yet/i)).toBeInTheDocument();
});

it("does not render add/remove affordances without objective.manage", () => {
  renderWithProviders(<PlansSection objectiveId="o1" plans={PLANS} />);
  expect(screen.queryByRole("button", { name: /add plan/i })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /remove plan/i })).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npx vitest run src/features/objectives/PlansSection.test.tsx`
Expected: FAIL (import).

- [ ] **Step 3: Write `PlansSection.tsx` (read-only; the manage affordances arrive in Task 14)**

```tsx
import { Card, Group, Stack, Text, Title } from "@mantine/core";
import type { ObjectivePlan } from "../../lib/types";
import { useUserDirectory } from "../../app/shell/useUserDirectory";

function nameOf(userId: string | null, dir: { id: string; display_name: string | null }[]): string {
  if (!userId) return "no owner";
  return dir.find((u) => u.id === userId)?.display_name ?? `${userId.slice(0, 8)}…`;
}

export function PlansSection({ objectiveId, plans }: { objectiveId: string; plans: ObjectivePlan[] }) {
  const { data: directory } = useUserDirectory();
  void objectiveId; // used by the manage affordances in Task 14
  return (
    <Stack gap="sm">
      <Title order={4}>Plans</Title>
      {plans.length === 0 ? (
        <Text c="dimmed" size="sm">No plans yet.</Text>
      ) : (
        plans.map((p) => (
          <Card key={p.id} withBorder padding="sm" radius="md">
            <Group justify="space-between">
              <div>
                <Text>{p.action}</Text>
                <Text c="dimmed" size="xs">
                  {nameOf(p.responsible_user_id, directory ?? [])}
                  {p.due_date ? ` · due ${p.due_date}` : " · no due date"}
                </Text>
              </div>
            </Group>
          </Card>
        ))
      )}
    </Stack>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npx vitest run src/features/objectives/PlansSection.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/objectives/PlansSection.tsx apps/web/src/features/objectives/PlansSection.test.tsx
git commit -m "feat(s-obj-2): PlansSection read-only list (name resolution via directory)"
```

---

### Task 9: `MeasurementsSection` (read-only table; calm 403 within)

**Files:**
- Create: `apps/web/src/features/objectives/MeasurementsSection.tsx`
- Test: `apps/web/src/features/objectives/MeasurementsSection.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { MeasurementsSection } from "./MeasurementsSection";

const ID = "ob000001-0001-0001-0001-000000000001";

it("renders a row per reading with period, value, and target-at-capture", async () => {
  renderWithProviders(<MeasurementsSection objectiveId={ID} unit="%" />);
  await waitFor(() => expect(screen.getByText("2026-04-01")).toBeInTheDocument());
  expect(screen.getByText("Logistics MIS")).toBeInTheDocument();
  // both the live target column and a historic target_at_capture are shown
  expect(screen.getAllByText("95 %").length).toBeGreaterThan(0);
});

it("shows a calm no-access panel when kpi.read is denied", async () => {
  server.use(
    http.get("/api/v1/objectives/:id/measurements", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  renderWithProviders(<MeasurementsSection objectiveId={ID} unit="%" />);
  await waitFor(() =>
    expect(screen.getByText(/don't have access to the measurement history/i)).toBeInTheDocument(),
  );
});

it("shows an empty hint when there are no readings", async () => {
  server.use(
    http.get("/api/v1/objectives/:id/measurements", () => HttpResponse.json({ data: [] })),
  );
  renderWithProviders(<MeasurementsSection objectiveId={ID} unit="%" />);
  await waitFor(() => expect(screen.getByText(/no measurements recorded yet/i)).toBeInTheDocument());
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npx vitest run src/features/objectives/MeasurementsSection.test.tsx`
Expected: FAIL (import).

- [ ] **Step 3: Write `MeasurementsSection.tsx` (read-only; the Record button arrives in Task 13)**

```tsx
import { Alert, Group, Loader, Stack, Table, Text, Title } from "@mantine/core";
import { useObjectiveMeasurements } from "./hooks";

export function MeasurementsSection({ objectiveId, unit }: { objectiveId: string; unit: string }) {
  const { data, isLoading, forbidden } = useObjectiveMeasurements(objectiveId);
  void unit; // available for the Record modal in Task 13
  return (
    <Stack gap="sm">
      <Group justify="space-between">
        <Title order={4}>Measurement history</Title>
        {/* The Record-measurement button (gated kpi.record) is wired in Task 13. */}
      </Group>
      {forbidden ? (
        <Alert color="gray" title="No access">
          You don't have access to the measurement history for this objective.
        </Alert>
      ) : isLoading ? (
        <Loader />
      ) : (data ?? []).length === 0 ? (
        <Text c="dimmed" size="sm">No measurements recorded yet.</Text>
      ) : (
        <>
          <Table>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Period</Table.Th>
                <Table.Th>Value</Table.Th>
                <Table.Th>Target then</Table.Th>
                <Table.Th>Source</Table.Th>
                <Table.Th>Recorded</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {(data ?? []).map((m) => (
                <Table.Tr key={m.id}>
                  <Table.Td>{m.period}</Table.Td>
                  <Table.Td>{m.value} {m.unit}</Table.Td>
                  <Table.Td c="dimmed">{m.target_at_capture} {m.unit}</Table.Td>
                  <Table.Td c="dimmed">{m.source ?? "—"}</Table.Td>
                  <Table.Td c="dimmed">{m.created_at.slice(0, 10)}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          <Text c="dimmed" size="xs">Readings are append-only. Trend charts arrive in a later release.</Text>
        </>
      )}
    </Stack>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npx vitest run src/features/objectives/MeasurementsSection.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/objectives/MeasurementsSection.tsx apps/web/src/features/objectives/MeasurementsSection.test.tsx
git commit -m "feat(s-obj-2): MeasurementsSection read-only table (calm kpi.read 403)"
```

---

### Task 10: `ObjectiveDetailPage` (compose header + hero + plans + measurements; 404)

**Files:**
- Create: `apps/web/src/features/objectives/ObjectiveDetailPage.tsx`
- Test: `apps/web/src/features/objectives/ObjectiveDetailPage.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { axe } from "jest-axe";
import { Route, Routes } from "react-router-dom";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { ObjectiveDetailPage } from "./ObjectiveDetailPage";

const ID = "ob000001-0001-0001-0001-000000000001";

function renderAt(id: string) {
  return renderWithProviders(
    <Routes>
      <Route path="/objectives/:id" element={<ObjectiveDetailPage />} />
    </Routes>,
    { route: `/objectives/${id}` },
  );
}

it("renders the header, commitment, plans and measurements", async () => {
  const { container } = renderAt(ID);
  await waitFor(() => expect(screen.getByText("OBJ-001")).toBeInTheDocument());
  expect(screen.getByRole("heading", { name: "On-time delivery rate" })).toBeInTheDocument();
  expect(screen.getByText("Draft")).toBeInTheDocument();
  expect(screen.getByText("Add a second carrier to the south region")).toBeInTheDocument();
  await waitFor(() => expect(screen.getByText("2026-04-01")).toBeInTheDocument());
  expect(await axe(container)).toHaveNoViolations();
});

it("shows a not-found alert on a 404", async () => {
  server.use(
    http.get("/api/v1/objectives/:id", () =>
      HttpResponse.json({ code: "not_found", title: "Objective not found" }, { status: 404 }),
    ),
  );
  renderAt(ID);
  await waitFor(() => expect(screen.getByText(/couldn't load this objective/i)).toBeInTheDocument());
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npx vitest run src/features/objectives/ObjectiveDetailPage.test.tsx`
Expected: FAIL (import).

- [ ] **Step 3: Write `ObjectiveDetailPage.tsx`**

```tsx
import { Alert, Badge, Container, Group, Loader, Stack, Title } from "@mantine/core";
import { useParams } from "react-router-dom";
import { useObjective } from "./hooks";
import { CommitmentHero } from "./CommitmentHero";
import { PlansSection } from "./PlansSection";
import { MeasurementsSection } from "./MeasurementsSection";

export function ObjectiveDetailPage() {
  const { id = null } = useParams();
  const { data: o, isLoading, isError, forbidden } = useObjective(id);

  if (isLoading) {
    return (
      <Container size="lg" py="md">
        <Loader />
      </Container>
    );
  }

  if (isError || !o) {
    return (
      <Container size="lg" py="md">
        <Alert color={forbidden ? "gray" : "red"} title="Couldn't load this objective">
          {forbidden
            ? "You don't have access to this objective."
            : "It may have been removed, or you may not have access."}
        </Alert>
      </Container>
    );
  }

  return (
    <Container size="lg" py="md">
      <Stack gap="lg">
        <div>
          <Group gap="xs" mb={4}>
            <Title order={5} c="dimmed">{o.identifier}</Title>
            <Badge color="gray" variant="light">{o.current_state}</Badge>
          </Group>
          <Title order={2}>{o.title}</Title>
        </div>
        <CommitmentHero objective={o} />
        <PlansSection objectiveId={o.id} plans={o.plans} />
        <MeasurementsSection objectiveId={o.id} unit={o.unit} />
      </Stack>
    </Container>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npx vitest run src/features/objectives/ObjectiveDetailPage.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/objectives/ObjectiveDetailPage.tsx apps/web/src/features/objectives/ObjectiveDetailPage.test.tsx
git commit -m "feat(s-obj-2): ObjectiveDetailPage (header + hero + plans + measurements; 404)"
```

---

## Phase 4 — Writes

### Task 11: `BandPreview` (renders the zones from `bandZones`)

**Files:**
- Create: `apps/web/src/features/objectives/BandPreview.tsx`
- Test: `apps/web/src/features/objectives/BandPreview.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../../test/render";
import { BandPreview } from "./BandPreview";

it("draws a three-zone preview and the threshold/target labels for a valid HIGHER band", () => {
  renderWithProviders(
    <BandPreview target="95" threshold="90" direction="HIGHER_IS_BETTER" />,
  );
  expect(screen.getByText(/90 at-risk/i)).toBeInTheDocument();
  expect(screen.getByText(/95 target/i)).toBeInTheDocument();
  // a labelled meter for screen readers
  expect(screen.getByRole("img", { name: /green.*amber.*red|status band/i })).toBeInTheDocument();
});

it("shows the soft warning when the threshold is on the wrong side", () => {
  renderWithProviders(
    <BandPreview target="95" threshold="96" direction="HIGHER_IS_BETTER" />,
  );
  expect(screen.getByText(/below the target/i)).toBeInTheDocument();
});

it("renders nothing structural when the target is not yet a number", () => {
  const { container } = renderWithProviders(
    <BandPreview target="" threshold="" direction="HIGHER_IS_BETTER" />,
  );
  expect(container).toBeEmptyDOMElement();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npx vitest run src/features/objectives/BandPreview.test.tsx`
Expected: FAIL (import).

- [ ] **Step 3: Write `BandPreview.tsx`**

```tsx
import { Box, Group, Stack, Text } from "@mantine/core";
import { bandZones, type RagZone } from "./labels";
import type { ObjectiveDirection } from "../../lib/types";

const ZONE_COLOR: Record<RagZone, string> = {
  red: "var(--mantine-color-red-6)",
  amber: "var(--mantine-color-yellow-6)",
  green: "var(--mantine-color-green-6)",
};

export function BandPreview({
  target, threshold, direction,
}: { target: string; threshold: string; direction: ObjectiveDirection }) {
  const t = Number(target);
  if (target.trim() === "" || Number.isNaN(t)) return null;
  const thr = threshold.trim() === "" || Number.isNaN(Number(threshold)) ? null : Number(threshold);
  const model = bandZones({ target: t, threshold: thr, direction });

  return (
    <Stack gap={4}>
      <Box
        role="img"
        aria-label={`Status band: ${model.zones.join(", ")} from worse to better`}
        style={{ display: "flex", height: 14, borderRadius: 4, overflow: "hidden" }}
      >
        {model.zones.map((z) => (
          <Box key={z} style={{ flex: 1, background: ZONE_COLOR[z] }} />
        ))}
      </Box>
      <Group justify="space-between">
        {thr !== null && <Text size="xs" c="dimmed">{thr} at-risk</Text>}
        <Text size="xs" c="dimmed">{t} target ✓</Text>
      </Group>
      {model.warn && (
        <Text size="xs" c="yellow.8">{model.warn}</Text>
      )}
    </Stack>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npx vitest run src/features/objectives/BandPreview.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/objectives/BandPreview.tsx apps/web/src/features/objectives/BandPreview.test.tsx
git commit -m "feat(s-obj-2): BandPreview (zones + threshold/target labels + soft warn)"
```

---

### Task 12: `NewObjectiveModal` + `useCreateObjective` + register button

**Files:**
- Create: `apps/web/src/features/objectives/mutations.ts`
- Create: `apps/web/src/features/objectives/NewObjectiveModal.tsx`
- Test: `apps/web/src/features/objectives/NewObjectiveModal.test.tsx`
- Modify: `apps/web/src/features/objectives/ObjectivesRegisterPage.tsx` (add the gated button + modal mount)

- [ ] **Step 1: Write the failing test**

```tsx
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { NewObjectiveModal } from "./NewObjectiveModal";

function fill(label: RegExp, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

it("creates an objective from the required fields", async () => {
  let body: Record<string, unknown> | null = null;
  server.use(
    http.post("/api/v1/objectives", async ({ request }) => {
      body = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json({ id: "new" }, { status: 201 });
    }),
  );
  const onCreated = vi.fn();
  renderWithProviders(<NewObjectiveModal opened onClose={() => {}} onCreated={onCreated} />);

  fill(/objective/i, "On-time delivery rate");
  fill(/target/i, "95");
  fill(/unit/i, "%");
  fill(/due date/i, "2026-12-31");
  fireEvent.click(screen.getByRole("button", { name: /create objective/i }));

  await waitFor(() => expect(onCreated).toHaveBeenCalledWith("new"));
  expect(body).toMatchObject({
    title: "On-time delivery rate", target_value: "95", unit: "%",
    direction: "HIGHER_IS_BETTER", due_date: "2026-12-31",
  });
});

it("surfaces a 422 inline", async () => {
  server.use(
    http.post("/api/v1/objectives", () =>
      HttpResponse.json({ code: "validation_error", title: "Unknown process_id" }, { status: 422 }),
    ),
  );
  renderWithProviders(<NewObjectiveModal opened onClose={() => {}} onCreated={() => {}} />);
  fill(/objective/i, "X");
  fill(/target/i, "95");
  fill(/unit/i, "%");
  fill(/due date/i, "2026-12-31");
  fireEvent.click(screen.getByRole("button", { name: /create objective/i }));
  await waitFor(() => expect(screen.getByText(/unknown process_id/i)).toBeInTheDocument());
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npx vitest run src/features/objectives/NewObjectiveModal.test.tsx`
Expected: FAIL (import).

- [ ] **Step 3: Write `mutations.ts`**

```ts
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type {
  Measurement, MeasurementCreateBody, Objective, ObjectiveCreateBody, ObjectivePlan, PlanCreateBody,
} from "../../lib/types";

export function useCreateObjective() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ObjectiveCreateBody) => api.send<Objective>("POST", "/api/v1/objectives", body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["objectives-scorecard"] }),
  });
}

export function useRecordMeasurement(objectiveId: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: MeasurementCreateBody) =>
      api.send<Measurement>("POST", `/api/v1/objectives/${objectiveId}/measurements`, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["objective", objectiveId] });
      void qc.invalidateQueries({ queryKey: ["objective-measurements", objectiveId] });
      void qc.invalidateQueries({ queryKey: ["objectives-scorecard"] });
    },
  });
}

export function useAddPlan(objectiveId: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: PlanCreateBody) =>
      api.send<ObjectivePlan>("POST", `/api/v1/objectives/${objectiveId}/plans`, body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["objective", objectiveId] }),
  });
}

export function useRemovePlan(objectiveId: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (planId: string) =>
      api.send<void>("DELETE", `/api/v1/objectives/${objectiveId}/plans/${planId}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["objective", objectiveId] }),
  });
}
```

- [ ] **Step 4: Write `NewObjectiveModal.tsx`**

```tsx
import {
  Alert, Button, Collapse, Group, Modal, SegmentedControl, Select, Stack, TextInput, UnstyledButton,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import type { ObjectiveCreateBody, ObjectiveDirection } from "../../lib/types";
import { useCreateObjective, } from "./mutations";
import { useProcesses } from "./hooks";
import { BandPreview } from "./BandPreview";

interface Props {
  opened: boolean;
  onClose: () => void;
  onCreated: (id: string) => void;
}

export function NewObjectiveModal({ opened, onClose, onCreated }: Props) {
  const create = useCreateObjective();
  const { data: processes } = useProcesses();
  const [advanced, advancedC] = useDisclosure(false);
  const [error, setError] = useState<string | null>(null);

  const [title, setTitle] = useState("");
  const [target, setTarget] = useState("");
  const [unit, setUnit] = useState("");
  const [direction, setDirection] = useState<ObjectiveDirection>("HIGHER_IS_BETTER");
  const [dueDate, setDueDate] = useState("");
  const [baseline, setBaseline] = useState("");
  const [threshold, setThreshold] = useState("");
  const [processId, setProcessId] = useState<string | null>(null);

  const targetIsNumber = target.trim() !== "" && !Number.isNaN(Number(target));
  const canSubmit = title.trim() !== "" && targetIsNumber && unit.trim() !== "" && dueDate !== "";

  async function submit() {
    setError(null);
    const body: ObjectiveCreateBody = {
      title: title.trim(),
      target_value: target.trim(),
      unit: unit.trim(),
      direction,
      due_date: dueDate,
      baseline_value: baseline.trim() === "" ? null : baseline.trim(),
      at_risk_threshold: threshold.trim() === "" ? null : threshold.trim(),
      process_id: processId,
    };
    try {
      const created = await create.mutateAsync(body);
      onCreated(created.id);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong creating the objective.");
    }
  }

  return (
    <Modal opened={opened} onClose={onClose} title="New quality objective">
      <Stack gap="sm">
        {error && <Alert color="red">{error}</Alert>}
        <TextInput label="Objective" required value={title} onChange={(e) => setTitle(e.currentTarget.value)} />
        <Group grow>
          <TextInput label="Target" required value={target} onChange={(e) => setTarget(e.currentTarget.value)} />
          <TextInput label="Unit" required value={unit} onChange={(e) => setUnit(e.currentTarget.value)} />
        </Group>
        <div>
          <label style={{ fontSize: 13, color: "var(--mantine-color-dimmed)" }}>Direction</label>
          <SegmentedControl
            fullWidth
            value={direction}
            onChange={(v) => setDirection(v as ObjectiveDirection)}
            data={[
              { value: "HIGHER_IS_BETTER", label: "Higher is better" },
              { value: "LOWER_IS_BETTER", label: "Lower is better" },
            ]}
          />
        </div>
        <TextInput
          type="date" label="Due date" required value={dueDate}
          onChange={(e) => setDueDate(e.currentTarget.value)}
        />
        <UnstyledButton onClick={advancedC.toggle} c="dimmed" fz="sm">
          {advanced ? "▾" : "▸"} Amber "at-risk" band &amp; baseline (optional)
        </UnstyledButton>
        <Collapse in={advanced}>
          <Stack gap="sm">
            <Group grow>
              <TextInput label="Baseline" value={baseline} onChange={(e) => setBaseline(e.currentTarget.value)} />
              <TextInput label="At-risk threshold" value={threshold} onChange={(e) => setThreshold(e.currentTarget.value)} />
            </Group>
            <BandPreview target={target} threshold={threshold} direction={direction} />
            {processes && processes.length > 0 && (
              <Select
                label="Process (optional)"
                clearable
                value={processId}
                onChange={setProcessId}
                data={processes.map((p) => ({ value: p.id, label: p.name }))}
                comboboxProps={{ keepMounted: false }}
              />
            )}
          </Stack>
        </Collapse>
        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose}>Cancel</Button>
          <Button onClick={() => void submit()} loading={create.isPending} disabled={!canSubmit}>
            Create objective
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
```

- [ ] **Step 5: Wire the gated button + modal into `ObjectivesRegisterPage.tsx`**

Add imports: `import { useState } from "react";` (already present), `import { Button } from "@mantine/core";`, `import { useNavigate } from "react-router-dom";`, `import { NewObjectiveModal } from "./NewObjectiveModal";`. Add `const navigate = useNavigate();` and `const [createOpen, setCreateOpen] = useState(false);`. Replace the comment in the header `<Group>` with:

```tsx
{can("objective.manage") && (
  <Button onClick={() => setCreateOpen(true)}>New objective</Button>
)}
```

And before the closing `</Container>` add:

```tsx
<NewObjectiveModal
  opened={createOpen}
  onClose={() => setCreateOpen(false)}
  onCreated={(id) => {
    setCreateOpen(false);
    navigate(`/objectives/${id}`);
  }}
/>
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd apps/web && npx vitest run src/features/objectives/NewObjectiveModal.test.tsx src/features/objectives/ObjectivesRegisterPage.test.tsx`
Expected: PASS (the register page test still passes — the button only renders with `objective.manage`, which the default permissions handler doesn't grant).

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/features/objectives/mutations.ts apps/web/src/features/objectives/NewObjectiveModal.tsx apps/web/src/features/objectives/NewObjectiveModal.test.tsx apps/web/src/features/objectives/ObjectivesRegisterPage.tsx
git commit -m "feat(s-obj-2): create-objective modal (band preview + soft warn) + gated button"
```

---

### Task 13: `RecordMeasurementModal` + `useRecordMeasurement` + Record button

**Files:**
- Create: `apps/web/src/features/objectives/RecordMeasurementModal.tsx`
- Test: `apps/web/src/features/objectives/RecordMeasurementModal.test.tsx`
- Modify: `apps/web/src/features/objectives/MeasurementsSection.tsx` (gated Record button + modal)

- [ ] **Step 1: Write the failing test**

```tsx
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { RecordMeasurementModal } from "./RecordMeasurementModal";

const ID = "ob000001-0001-0001-0001-000000000001";

it("sends the objective's unit verbatim (locked) with the value and period", async () => {
  let body: Record<string, unknown> | null = null;
  server.use(
    http.post("/api/v1/objectives/:id/measurements", async ({ request }) => {
      body = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json({ id: "m" }, { status: 201 });
    }),
  );
  const onDone = vi.fn();
  renderWithProviders(
    <RecordMeasurementModal opened objectiveId={ID} unit="%" onClose={() => {}} onRecorded={onDone} />,
  );
  fireEvent.change(screen.getByLabelText(/period/i), { target: { value: "2026-07-01" } });
  fireEvent.change(screen.getByLabelText(/value/i), { target: { value: "94" } });
  fireEvent.click(screen.getByRole("button", { name: /record/i }));
  await waitFor(() => expect(onDone).toHaveBeenCalled());
  expect(body).toMatchObject({ period: "2026-07-01", value: "94", unit: "%" });
});

it("surfaces a 422 unit-mismatch inline", async () => {
  server.use(
    http.post("/api/v1/objectives/:id/measurements", () =>
      HttpResponse.json({ code: "validation_error", title: "unit must match" }, { status: 422 }),
    ),
  );
  renderWithProviders(
    <RecordMeasurementModal opened objectiveId={ID} unit="%" onClose={() => {}} onRecorded={() => {}} />,
  );
  fireEvent.change(screen.getByLabelText(/period/i), { target: { value: "2026-07-01" } });
  fireEvent.change(screen.getByLabelText(/value/i), { target: { value: "94" } });
  fireEvent.click(screen.getByRole("button", { name: /record/i }));
  await waitFor(() => expect(screen.getByText(/unit must match/i)).toBeInTheDocument());
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npx vitest run src/features/objectives/RecordMeasurementModal.test.tsx`
Expected: FAIL (import).

- [ ] **Step 3: Write `RecordMeasurementModal.tsx`**

```tsx
import { Alert, Button, Group, Modal, Stack, TextInput } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import type { MeasurementCreateBody } from "../../lib/types";
import { useRecordMeasurement } from "./mutations";

interface Props {
  opened: boolean;
  objectiveId: string;
  unit: string;
  onClose: () => void;
  onRecorded: () => void;
}

export function RecordMeasurementModal({ opened, objectiveId, unit, onClose, onRecorded }: Props) {
  const record = useRecordMeasurement(objectiveId);
  const [period, setPeriod] = useState("");
  const [value, setValue] = useState("");
  const [source, setSource] = useState("");
  const [error, setError] = useState<string | null>(null);

  const valueIsNumber = value.trim() !== "" && !Number.isNaN(Number(value));
  const canSubmit = period !== "" && valueIsNumber;

  async function submit() {
    setError(null);
    const body: MeasurementCreateBody = {
      period,
      value: value.trim(),
      unit, // LOCKED to the objective's unit — can never diverge → never trips the 422
      source: source.trim() === "" ? null : source.trim(),
    };
    try {
      await record.mutateAsync(body);
      onRecorded();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong recording the measurement.");
    }
  }

  return (
    <Modal opened={opened} onClose={onClose} title="Record measurement">
      <Stack gap="sm">
        {error && <Alert color="red">{error}</Alert>}
        <TextInput type="date" label="Period" required value={period} onChange={(e) => setPeriod(e.currentTarget.value)} />
        <TextInput
          label="Value" required value={value} onChange={(e) => setValue(e.currentTarget.value)}
          rightSection={<span style={{ paddingRight: 8, color: "var(--mantine-color-dimmed)" }}>{unit}</span>}
          rightSectionWidth={Math.max(28, unit.length * 9 + 16)}
        />
        <TextInput label="Source (optional)" value={source} onChange={(e) => setSource(e.currentTarget.value)} />
        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose}>Cancel</Button>
          <Button onClick={() => void submit()} loading={record.isPending} disabled={!canSubmit}>
            Record
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
```

- [ ] **Step 4: Wire the gated Record button into `MeasurementsSection.tsx`**

Add imports: `import { Button } from "@mantine/core";` (extend the existing import), `import { useState } from "react";`, `import { usePermissions } from "../../app/shell/usePermissions";`, `import { RecordMeasurementModal } from "./RecordMeasurementModal";`. Add `const { can } = usePermissions();` and `const [open, setOpen] = useState(false);`. Replace the header comment with:

```tsx
{can("kpi.record") && <Button size="xs" onClick={() => setOpen(true)}>Record measurement</Button>}
```

Remove the `void unit;` line (now consumed). Before the closing `</Stack>` add:

```tsx
<RecordMeasurementModal
  opened={open}
  objectiveId={objectiveId}
  unit={unit}
  onClose={() => setOpen(false)}
  onRecorded={() => setOpen(false)}
/>
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd apps/web && npx vitest run src/features/objectives/RecordMeasurementModal.test.tsx src/features/objectives/MeasurementsSection.test.tsx`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/features/objectives/RecordMeasurementModal.tsx apps/web/src/features/objectives/RecordMeasurementModal.test.tsx apps/web/src/features/objectives/MeasurementsSection.tsx
git commit -m "feat(s-obj-2): record-measurement modal (locked unit) + gated Record button"
```

---

### Task 14: `AddPlanModal` + plan add/remove wired into `PlansSection`

**Files:**
- Create: `apps/web/src/features/objectives/AddPlanModal.tsx`
- Modify: `apps/web/src/features/objectives/PlansSection.tsx` (gated Add + per-row Remove)
- Modify: `apps/web/src/features/objectives/PlansSection.test.tsx` (gated-affordance tests)

- [ ] **Step 1: Write the failing test (append to `PlansSection.test.tsx`)**

```tsx
import { fireEvent, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "../../test/msw/server";

function grantManage() {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: [{ key: "objective.manage", effect: "ALLOW", source: "test" }],
      }),
    ),
  );
}

it("shows Add and Remove when objective.manage is granted", async () => {
  grantManage();
  renderWithProviders(<PlansSection objectiveId="o1" plans={PLANS} />);
  await waitFor(() => expect(screen.getByRole("button", { name: /add plan/i })).toBeInTheDocument());
  expect(screen.getByRole("button", { name: /remove plan/i })).toBeInTheDocument();
});

it("removes a plan via DELETE", async () => {
  grantManage();
  let deleted = false;
  server.use(
    http.delete("/api/v1/objectives/:id/plans/:planId", () => {
      deleted = true;
      return new HttpResponse(null, { status: 204 });
    }),
  );
  renderWithProviders(<PlansSection objectiveId="o1" plans={PLANS} />);
  await waitFor(() => screen.getByRole("button", { name: /remove plan/i }));
  fireEvent.click(screen.getByRole("button", { name: /remove plan/i }));
  await waitFor(() => expect(deleted).toBe(true));
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npx vitest run src/features/objectives/PlansSection.test.tsx`
Expected: FAIL (no Add/Remove buttons yet).

- [ ] **Step 3: Write `AddPlanModal.tsx`**

```tsx
import { Alert, Button, Group, Modal, Stack, TextInput, Textarea } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import type { PlanCreateBody } from "../../lib/types";
import { useAddPlan } from "./mutations";

interface Props {
  opened: boolean;
  objectiveId: string;
  onClose: () => void;
}

export function AddPlanModal({ opened, objectiveId, onClose }: Props) {
  const add = useAddPlan(objectiveId);
  const [action, setAction] = useState("");
  const [resource, setResource] = useState("");
  const [dueDate, setDueDate] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setError(null);
    const body: PlanCreateBody = {
      action: action.trim(),
      resource: resource.trim() === "" ? null : resource.trim(),
      due_date: dueDate === "" ? null : dueDate,
    };
    try {
      await add.mutateAsync(body);
      setAction(""); setResource(""); setDueDate("");
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong adding the plan.");
    }
  }

  return (
    <Modal opened={opened} onClose={onClose} title="Add plan">
      <Stack gap="sm">
        {error && <Alert color="red">{error}</Alert>}
        <Textarea label="Action" required autosize minRows={2} value={action} onChange={(e) => setAction(e.currentTarget.value)} />
        <TextInput label="Resource (optional)" value={resource} onChange={(e) => setResource(e.currentTarget.value)} />
        <TextInput type="date" label="Due date (optional)" value={dueDate} onChange={(e) => setDueDate(e.currentTarget.value)} />
        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose}>Cancel</Button>
          <Button onClick={() => void submit()} loading={add.isPending} disabled={action.trim() === ""}>
            Add plan
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
```

- [ ] **Step 4: Extend `PlansSection.tsx` with the gated Add + per-row Remove**

Add imports: `import { ActionIcon, Button } from "@mantine/core";` (merge into the existing `@mantine/core` import), `import { useState } from "react";`, `import { usePermissions } from "../../app/shell/usePermissions";`, `import { useRemovePlan } from "./mutations";`, `import { AddPlanModal } from "./AddPlanModal";`. Replace the component body's return with the manage-aware version:

```tsx
export function PlansSection({ objectiveId, plans }: { objectiveId: string; plans: ObjectivePlan[] }) {
  const { data: directory } = useUserDirectory();
  const { can } = usePermissions();
  const manage = can("objective.manage");
  const remove = useRemovePlan(objectiveId);
  const [addOpen, setAddOpen] = useState(false);

  return (
    <Stack gap="sm">
      <Group justify="space-between">
        <Title order={4}>Plans</Title>
        {manage && <Button size="xs" onClick={() => setAddOpen(true)}>Add plan</Button>}
      </Group>
      {plans.length === 0 ? (
        <Text c="dimmed" size="sm">No plans yet.</Text>
      ) : (
        plans.map((p) => (
          <Card key={p.id} withBorder padding="sm" radius="md">
            <Group justify="space-between">
              <div>
                <Text>{p.action}</Text>
                <Text c="dimmed" size="xs">
                  {nameOf(p.responsible_user_id, directory ?? [])}
                  {p.due_date ? ` · due ${p.due_date}` : " · no due date"}
                </Text>
              </div>
              {manage && (
                <ActionIcon
                  variant="subtle" color="gray" aria-label="Remove plan"
                  loading={remove.isPending && remove.variables === p.id}
                  onClick={() => remove.mutate(p.id)}
                >
                  ✕
                </ActionIcon>
              )}
            </Group>
          </Card>
        ))
      )}
      <AddPlanModal opened={addOpen} objectiveId={objectiveId} onClose={() => setAddOpen(false)} />
    </Stack>
  );
}
```

(Remove the now-unused `void objectiveId;` line.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd apps/web && npx vitest run src/features/objectives/PlansSection.test.tsx`
Expected: PASS (including the original no-affordance test, which uses the default empty-permissions handler).

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/features/objectives/AddPlanModal.tsx apps/web/src/features/objectives/PlansSection.tsx apps/web/src/features/objectives/PlansSection.test.tsx
git commit -m "feat(s-obj-2): add/remove objective plans (gated objective.manage)"
```

---

## Phase 5 — Wire + close

### Task 15: Routing + gated nav entry

**Files:**
- Modify: `apps/web/src/App.tsx` (import + 2 routes)
- Modify: `apps/web/src/app/shell/LeftRail.tsx` (gated nav entry after Drift)
- Test: `apps/web/src/app/shell/LeftRail.test.tsx` (if present — add a nav-gating case; else create a focused test)

- [ ] **Step 1: Write the failing nav-gating test**

Create/extend `apps/web/src/app/shell/LeftRail.test.tsx`:

```tsx
import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { LeftRail } from "./LeftRail";

it("shows the Objectives entry only with objective.read", async () => {
  renderWithProviders(<LeftRail />);
  // default permissions handler grants nothing → no entry
  await waitFor(() => expect(screen.getByText("Home")).toBeInTheDocument());
  expect(screen.queryByText("Objectives")).not.toBeInTheDocument();

  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: [{ key: "objective.read", effect: "ALLOW", source: "test" }],
      }),
    ),
  );
  renderWithProviders(<LeftRail />);
  await waitFor(() => expect(screen.getByText("Objectives")).toBeInTheDocument());
});
```

> If a `LeftRail.test.tsx` already exists, add only the second assertion block as a new `it(...)` and reuse its imports.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npx vitest run src/app/shell/LeftRail.test.tsx`
Expected: FAIL ("Objectives" never appears).

- [ ] **Step 3: Add the gated nav entry to `LeftRail.tsx`**

Insert immediately after the Drift `{can("drift.read") && ( … )}` block (before the `{PHASES.map(...)}`):

```tsx
{can("objective.read") && (
  // S-obj-2: gated — objective.read (PROCESS finest-scope, SYSTEM fallback in v1); the PLAN-phase
  // register (clause 6.2). Mirrors the drift.read entry.
  <NavLink
    component={Link}
    to="/objectives"
    label="Objectives"
    active={pathname.startsWith("/objectives")}
  />
)}
```

- [ ] **Step 4: Add the routes to `App.tsx`**

Add imports near the other feature imports:

```tsx
import { ObjectivesRegisterPage } from "./features/objectives/ObjectivesRegisterPage";
import { ObjectiveDetailPage } from "./features/objectives/ObjectiveDetailPage";
```

Add inside the `<Route path="/" element={…AppShell…}>` block (e.g. after the `drift` routes, before the closing `</Route>`):

```tsx
<Route path="objectives" element={<ObjectivesRegisterPage />} />
<Route path="objectives/:id" element={<ObjectiveDetailPage />} />
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd apps/web && npx vitest run src/app/shell/LeftRail.test.tsx`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/App.tsx apps/web/src/app/shell/LeftRail.tsx apps/web/src/app/shell/LeftRail.test.tsx
git commit -m "feat(s-obj-2): wire /objectives routes + gated nav entry (objective.read)"
```

---

### Task 16: Full gate, diff-critic, live smoke, docs, PR

- [ ] **Step 1: Run the full web gate**

Run: `cd apps/web && npm run lint && npx tsc --noEmit && npm run build && npx vitest run`
(Or the project `/check-web` skill.)
Expected: all green. `noUncheckedIndexedAccess` catches array-index nits (e.g. `objectiveFixtures[0]!` non-null assertions in fixtures are fine; production array indexing must guard). Record the new total test count and compute the delta from the Task 1 baseline (~589 post S-ack-2).

- [ ] **Step 2: Run the diff-critic agent on the branch diff**

Dispatch the `diff-critic` subagent (read-only) on the `feat/s-obj-2-objectives-ui` diff. Focus the false-PASS hunt on: fixture/serializer shape drift (the decimal-string vs number `pct_toward_target`; the `{data:[…]}` wrapping; the scorecard shape), calm-403 `retry:false` (no hammering), the locked-unit invariant on the measure form, and duplicate `aria-label`/`getByLabelText` traps. Fold only confirmed findings.

- [ ] **Step 3: Live smoke (Chrome MCP, localhost only)**

```bash
just up s --build   # pick up merged 0049 + the objectives router
just demo-user      # if Keycloak was recreated
```

Grant the live `demo` app_user row SYSTEM overrides for `objective.read`, `objective.manage`, `kpi.read`, `kpi.record` (org short_code `AHT`) so the surface is reachable. Then, find-then-click in separate batches, text-first verification, client-side nav only: load `/objectives` (band + table render), open the create modal and create an objective, open its detail page, add a plan, record a measurement (confirm the rollup `current_value` updates), remove the plan. Confirm a calm no-access panel by revoking one key. Note: `demo` holds none of these keys natively — without the overrides every surface is a calm panel (the expected default).

- [ ] **Step 4: Docs — slice-history + CLAUDE.md learning**

Add an `S-obj-2` entry to `docs/slice-history.md` (front-end-only; closes the Quality Objectives family; lists the register/detail/create/measure surfaces, the named deferrals incl. the v1 policy-field omission and the lifecycle/6.2-★ gap, and the web-test delta). Add a one-line `CLAUDE.md` Recent-learnings entry (newest first; demote the oldest if >12) and update the Current-status pointer. Note migration head is unchanged (`0049`).

- [ ] **Step 5: Open the PR**

Run the `pr` skill (or `gh pr create`) against protected `main` from `feat/s-obj-2-objectives-ui`. Body: front-end-only, no migration/key/endpoint/contract; the approved UX; the deferrals; the web-test delta; the live-smoke evidence. After green CI + owner OK, squash-merge. Triage Codex review for legitimacy; disregard the multi-tenant nitpicks (moot under D1 single-org — see the codex-multitenant-false-positives memory), but fix any genuine non-tenant bug riding along.

- [ ] **Step 6: Final commit (docs)**

```bash
git add docs/slice-history.md CLAUDE.md
git commit -m "docs(s-obj-2): slice-history entry + CLAUDE.md learning (Quality Objectives UI)"
```

---

## Self-review

**1. Spec coverage:**
- §3 data contract → Task 1 (types + fixtures + handlers, `satisfies`-pinned). ✓
- §4 gating → calm-403 hooks (Tasks 3/5/9/10), per-key write gating (Tasks 12/13/14), nav gating (Task 15). ✓
- §5.1 register (layout A) → Tasks 4/5. ✓
- §5.2 create (band preview + soft warn) → Tasks 2/11/12. Policy field → **omitted in v1** (plan header divergence, justified; named deferral). ✓
- §5.3 detail (single-scroll) → Tasks 7/8/9/10. ✓
- §5.4 record measurement (locked unit) → Task 13. ✓
- §6 file layout → matches the File structure section. ✓
- §7 query keys → hooks (Task 3) + mutations (Task 12) use `["objectives-scorecard"]`/`["objective", id]`/`["objective-measurements", id]`. ✓
- §8 error handling → 403 forbidden panels, 404 detail alert, 422 inline (create/measure). ✓
- §9 testing → per-task tests + jest-axe + the full gate (Task 16). ✓
- §10 build sequence → Tasks 1–16 across the 5 phases. ✓

**2. Placeholder scan:** every code step carries complete code; no TBD/TODO. The only deliberately-deferred UI (process-name row in the hero, RAG filter chips) is marked optional with a degrade path, not a placeholder.

**3. Type consistency:** body types are `ObjectiveCreateBody`/`MeasurementCreateBody`/`PlanCreateBody` (request) vs the response `Objective`/`Measurement`/`ObjectivePlan` — used consistently across Tasks 1/12/13/14. Hook query keys (`["objectives-scorecard", processId]`, `["objective", id]`, `["objective-measurements", id]`) match the mutation invalidations. `bandZones` signature (`{target, threshold, direction}` → `{zones, hasAmber, warn}`) is identical in Task 2 (def), Task 11 (BandPreview), and Task 12 (modal soft-warn via BandPreview). `RAG_COLOR`/`RAG_LABEL`/`fmtValueUnit` used in Tasks 4/5/7 match the Task 2 definitions.
