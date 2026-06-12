# S-home-1 — PDCA Home dashboard (QMS Health) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended)
> or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax
> for tracking.

**Goal:** Replace the `HomePage.tsx` placeholder with the doc-11 §5.1 four-quadrant PLAN/DO/CHECK/ACT
"QMS Health" wheel — counts + RAG only, each tile composed from an already-shipped read endpoint and
degrading independently and calmly on a forbidden/empty read.

**Architecture:** Front-end-only. A flat `features/home/` folder: pure RAG/count rules in `rag.ts`
(unit-tested, **read** server RAG — never recompute it; N9 status-against-a-rule); presentational
primitives `StatLine` + `QuadrantCard`; four thin per-quadrant components (`PlanCard`/`DoCard`/`CheckCard`/
`ActCard`) each owning its hooks + degrade logic; a `HealthSummary` header band; a `MyTasksRail`. `HomePage`
lays them out. Every signal reads an existing hook (cross-feature import is fine — only the *shell* must
not depend on features). No migration / key / endpoint / contract / route / nav change.

**Tech Stack:** React 18 + TypeScript (strict, `noUncheckedIndexedAccess`), Mantine, `@tanstack/react-query`,
`react-router-dom`, MSW v2, vitest + `@testing-library/react` + jest-axe.

**Spec:** `docs/superpowers/specs/2026-06-11-s-home-1-pdca-dashboard-design.md` (approved). Read §3 (data
contract) before touching a fixture.

**Plan-time decisions (named divergences from the spec):**
- The spec's "QuadrantCard ×4" is realized as one generic `QuadrantCard` frame + four per-quadrant
  components (`PlanCard`/`DoCard`/`CheckCard`/`ActCard`). Each owns its reads and degrade logic.
- The spec's per-quadrant RAG functions (`planObjectivesRag`/`coverageRag`/`overdueRag`/`driftRag`/`actRag`)
  are realized as composable primitives: each RAG-bearing signal computes its own line RAG
  (`planObjectivesRag`/`overdueRag`/`coverageRag`/`driftRag`, or `countRag` for the ACT counts) and the
  **tile RAG = `worstRag` of the visible RAG-bearing signals**. This subsumes the spec's `actRag` (so
  `actRag` is NOT a separate export) and makes per-signal forbidden-omit fall out for free (a forbidden
  signal contributes no RAG, so it can't drag a tile to red).
- The My-Tasks rail rows show **task type + action + absolute due date** (`due_at.slice(0,10)`), not the
  document name (the `/tasks` LIST omits `subject_*`) and not a relative date (non-deterministic to test).

## File structure

```
apps/web/src/features/home/
  rag.ts                # T1: Rag type, RAG_META, pure rules + count helpers (no React)
  rag.test.ts           # T1
  hooks.ts              # T2: useMyTasks()
  hooks.test.tsx         # T2
  StatLine.tsx          # T3: glyph + optional value + label (DP-7)
  StatLine.test.tsx     # T3
  QuadrantCard.tsx      # T4: PDCA chip + RAG badge + body + one Open action; + TileNoAccess/TileSkeleton
  QuadrantCard.test.tsx # T4
  HealthSummary.tsx     # T5: header ★-coverage band (useComplianceChecklist)
  HealthSummary.test.tsx# T5
  PlanCard.tsx          # T6: objectives + overdue reviews
  PlanCard.test.tsx     # T6
  DoCard.tsx            # T7: drift integrity + superseded copies + acks
  DoCard.test.tsx       # T7
  CheckCard.tsx         # T8: open audits + mandatory coverage
  CheckCard.test.tsx    # T8
  ActCard.tsx           # T9: CAPAs open + NCRs awaiting + complaints awaiting
  ActCard.test.tsx      # T9
  MyTasksRail.tsx       # T10: my-tasks preview (useMyTasks)
  MyTasksRail.test.tsx  # T10
  HomePage.tsx          # T11: REWRITE — Container/Stack: HealthSummary + SimpleGrid(4 cards) + MyTasksRail
  HomePage.test.tsx     # T11: REWRITE — heading + happy integration + demo-shape degrade + axe
```

**Constants reused:** `renderWithProviders` + `TEST_AUTH` (`src/test/render.tsx`, `sub` =
`bbbb1111-1111-1111-1111-111111111111`); `server` + `http`/`HttpResponse` (`src/test/msw/server`, `msw`);
the default happy-path handlers in `src/test/msw/handlers.ts` (override per-test with `server.use`). All
fixtures `satisfies` the real `lib/types.ts` types. **Every test file imports `{ expect, it }` from
`"vitest"`** (the jest-dom × vitest `expect` trap — only `tsc` catches it).

---

## Phase 1 — Foundation

### Task 1: `rag.ts` — pure RAG/count rules

**Files:**
- Create: `apps/web/src/features/home/rag.ts`
- Test: `apps/web/src/features/home/rag.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
import { describe, expect, it } from "vitest";
import type { Audit, Capa, Complaint, DriftStatus, Ncr } from "../../lib/types";
import {
  capasOpenCount, complaintsAwaitingCount, countRag, coverageRag, driftRag, driftStatusText,
  ncrsAwaitingCount, openAuditsCount, overdueRag, planObjectivesRag, RAG_META, worstRag,
} from "./rag";

const drift = (over: Partial<DriftStatus> = {}): DriftStatus => ({
  scans: { MIRROR: null, BLOB_REHASH: null },
  blob_coverage: { total: 10, never_verified: 0, failing: 0, oldest_verified_at: null },
  superseded_copies: { versions: 0, copies: 0 },
  ...over,
});
const cleanScan = { started_at: "x", finished_at: "y", counts: {}, triggered_by: "beat" as const };

describe("rag rules", () => {
  it("planObjectivesRag is worst-wins (red > amber > green > neutral)", () => {
    expect(planObjectivesRag({ green: 3, amber: 0, red: 1, unmeasured: 0 })).toBe("red");
    expect(planObjectivesRag({ green: 3, amber: 1, red: 0, unmeasured: 0 })).toBe("amber");
    expect(planObjectivesRag({ green: 3, amber: 0, red: 0, unmeasured: 1 })).toBe("green");
    expect(planObjectivesRag({ green: 0, amber: 0, red: 0, unmeasured: 0 })).toBe("neutral");
  });

  it("coverageRag: gap→red, undercovered→amber, full→green", () => {
    expect(coverageRag({ total: 20, covered: 18, gap: 1 })).toBe("red");
    expect(coverageRag({ total: 20, covered: 18, gap: 0 })).toBe("amber");
    expect(coverageRag({ total: 20, covered: 20, gap: 0 })).toBe("green");
  });

  it("overdueRag + countRag", () => {
    expect(overdueRag(2)).toBe("amber");
    expect(overdueRag(0)).toBe("green");
    expect(countRag(1, "red")).toBe("red");
    expect(countRag(0, "red")).toBe("green");
  });

  it("driftRag: failing pin → red; FAILED → amber; all CLEAN → green; unscanned → neutral", () => {
    expect(driftRag(drift({ blob_coverage: { total: 1, never_verified: 0, failing: 2, oldest_verified_at: null } }))).toBe("red");
    expect(driftRag(drift({ scans: { MIRROR: { status: "DIVERGENT", ...cleanScan }, BLOB_REHASH: null } }))).toBe("red");
    expect(driftRag(drift({ scans: { MIRROR: { status: "FAILED", ...cleanScan }, BLOB_REHASH: null } }))).toBe("amber");
    expect(driftRag(drift({ scans: { MIRROR: { status: "CLEAN", ...cleanScan }, BLOB_REHASH: { status: "CLEAN", ...cleanScan } } }))).toBe("green");
    expect(driftRag(drift())).toBe("neutral");
  });

  it("driftStatusText", () => {
    expect(driftStatusText(drift({ scans: { MIRROR: { status: "CLEAN", ...cleanScan }, BLOB_REHASH: { status: "CLEAN", ...cleanScan } } }))).toBe("clean");
    expect(driftStatusText(drift({ blob_coverage: { total: 1, never_verified: 0, failing: 1, oldest_verified_at: null } }))).toBe("1 integrity issue");
  });

  it("worstRag picks the worst; empty → neutral", () => {
    expect(worstRag(["green", "red", "amber"])).toBe("red");
    expect(worstRag(["green", "neutral"])).toBe("green");
    expect(worstRag([])).toBe("neutral");
  });

  it("count helpers filter open/awaiting rows", () => {
    const audits = [{ state: "Closed" }, { state: "InProgress" }, { state: "Scheduled" }] as Audit[];
    expect(openAuditsCount(audits)).toBe(2);
    const capas = [{ close_state: "Closed" }, { close_state: "Rejected" }, { close_state: "Verify" }] as Capa[];
    expect(capasOpenCount(capas)).toBe(1);
    const ncrs = [{ disposition: null }, { disposition: "scrap" }] as Ncr[];
    expect(ncrsAwaitingCount(ncrs)).toBe(1);
    const complaints = [{ spawned_capa_id: null }, { spawned_capa_id: "x" }] as Complaint[];
    expect(complaintsAwaitingCount(complaints)).toBe(1);
  });

  it("RAG_META carries a distinct glyph + Mantine colour per RAG (DP-7)", () => {
    expect(RAG_META.green.color).toBe("green");
    expect(RAG_META.amber.color).toBe("yellow");
    expect(RAG_META.red.color).toBe("red");
    expect(new Set(Object.values(RAG_META).map((m) => m.glyph)).size).toBe(4);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npx vitest run src/features/home/rag.test.ts`
Expected: FAIL (`Failed to resolve import "./rag"`).

- [ ] **Step 3: Write `rag.ts`**

```ts
import type { Audit, Capa, Complaint, DriftStatus, Ncr } from "../../lib/types";

// The dashboard's RAG vocabulary. `neutral` = an informational/unscored signal (NOT objectives'
// `unmeasured`, which maps to neutral). N9: every value is status against a coded rule, read at
// render — never an asserted compliance verdict, never stored.
export type Rag = "green" | "amber" | "red" | "neutral";

// DP-7: status is never colour-only — each RAG carries a distinct glyph + label + Mantine colour.
export const RAG_META: Record<Rag, { color: string; glyph: string; label: string }> = {
  green: { color: "green", glyph: "✓", label: "Green" },
  amber: { color: "yellow", glyph: "▲", label: "Amber" },
  red: { color: "red", glyph: "✕", label: "Red" },
  neutral: { color: "gray", glyph: "•", label: "—" },
};

const ORDER: Record<Rag, number> = { neutral: 0, green: 1, amber: 2, red: 3 };

// The worst (most severe) RAG among the visible signals; an empty list (all signals hidden) → neutral.
export function worstRag(rags: Rag[]): Rag {
  return rags.reduce<Rag>((acc, r) => (ORDER[r] > ORDER[acc] ? r : acc), "neutral");
}

// Objectives: read the SERVER-computed by_rag verbatim, roll up worst-wins. Never recompute a row's rag.
export function planObjectivesRag(b: { green: number; amber: number; red: number; unmeasured: number }): Rag {
  if (b.red > 0) return "red";
  if (b.amber > 0) return "amber";
  if (b.green > 0) return "green";
  return "neutral";
}

export function coverageRag(r: { total: number; covered: number; gap: number }): Rag {
  if (r.gap > 0) return "red";
  if (r.covered < r.total) return "amber";
  return "green";
}

export const overdueRag = (n: number): Rag => (n > 0 ? "amber" : "green");

// A count's RAG: green when zero, otherwise the given severity (amber for CAPAs/complaints, red for NCRs).
export const countRag = (n: number, positive: Rag): Rag => (n > 0 ? positive : "green");

export function driftRag(s: DriftStatus): Rag {
  const statuses = [s.scans.MIRROR?.status, s.scans.BLOB_REHASH?.status];
  if (s.blob_coverage.failing > 0 || statuses.includes("DIVERGENT")) return "red";
  if (statuses.includes("FAILED")) return "amber";
  const present = statuses.filter((x): x is NonNullable<typeof x> => x != null);
  if (present.length > 0 && present.every((x) => x === "CLEAN")) return "green";
  return "neutral";
}

export function driftStatusText(s: DriftStatus): string {
  const rag = driftRag(s);
  if (rag === "green") return "clean";
  if (rag === "amber") return "scan needs attention";
  if (rag === "neutral") return "not yet scanned";
  const f = s.blob_coverage.failing;
  return f > 0 ? `${f} integrity issue${f === 1 ? "" : "s"}` : "divergence detected";
}

export const openAuditsCount = (a: Audit[]): number => a.filter((x) => x.state !== "Closed").length;
export const capasOpenCount = (c: Capa[]): number =>
  c.filter((x) => x.close_state !== "Closed" && x.close_state !== "Rejected").length;
export const ncrsAwaitingCount = (n: Ncr[]): number => n.filter((x) => x.disposition === null).length;
export const complaintsAwaitingCount = (c: Complaint[]): number =>
  c.filter((x) => x.spawned_capa_id === null).length;
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npx vitest run src/features/home/rag.test.ts`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/home/rag.ts apps/web/src/features/home/rag.test.ts
git commit -m "feat(s-home-1): pure RAG/count rules for the PDCA dashboard"
```

### Task 2: `useMyTasks` hook

**Files:**
- Create: `apps/web/src/features/home/hooks.ts`
- Test: `apps/web/src/features/home/hooks.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { AuthContext } from "../../lib/auth";
import { server } from "../../test/msw/server";
import { TEST_AUTH } from "../../test/render";
import { expect, it } from "vitest";
import type { Task } from "../../lib/types";
import { useMyTasks } from "./hooks";

// A PRODUCTION-defaults QueryClient (retry enabled by default) proves the hook's own retry:false — the
// shared test client hardcodes retry:false, so it would mask a missing retry:false (the S-web-8 trap).
function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient();
  return (
    <QueryClientProvider client={client}>
      <AuthContext.Provider value={TEST_AUTH}>{children}</AuthContext.Provider>
    </QueryClientProvider>
  );
}

const taskFixture: Task[] = [
  { id: "t1", instance_id: "i1", stage_key: "review", type: "REVIEW", state: "PENDING",
    assignee_user_id: null, candidate_pool: null, action_expected: "Review", due_at: "2026-06-13T00:00:00+00:00" },
];

it("useMyTasks reads the self-scoped pending tasks", async () => {
  server.use(http.get("/api/v1/tasks", () => HttpResponse.json(taskFixture)));
  const { result } = renderHook(() => useMyTasks(), { wrapper });
  await waitFor(() => expect(result.current.data).toBeDefined());
  expect(result.current.data).toHaveLength(1);
  expect(result.current.forbidden).toBe(false);
});

it("useMyTasks surfaces a forbidden flag on 403 without retrying", async () => {
  server.use(http.get("/api/v1/tasks", () => HttpResponse.json({ code: "forbidden" }, { status: 403 })));
  const { result } = renderHook(() => useMyTasks(), { wrapper });
  await waitFor(() => expect(result.current.forbidden).toBe(true));
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npx vitest run src/features/home/hooks.test.tsx`
Expected: FAIL (`Failed to resolve import "./hooks"`).

- [ ] **Step 3: Write `hooks.ts`**

```ts
import { useQuery } from "@tanstack/react-query";
import { ApiError, useApi } from "../../lib/api";
import type { Task } from "../../lib/types";

// The My-Tasks rail source — the caller's open tasks across all types (self-scoped server-side, no
// permission key). The TopBar bell's useAckCount is the DOC_ACK-only sibling. retry:false + a forbidden
// flag for symmetry (self-scoped → 403 is not expected, but never crash if policy changes).
export function useMyTasks() {
  const api = useApi();
  const query = useQuery({
    queryKey: ["my-tasks"],
    queryFn: () => api.get<Task[]>("/api/v1/tasks?assignee=me&state=PENDING"),
    retry: false,
  });
  const forbidden = query.error instanceof ApiError && query.error.status === 403;
  return { ...query, forbidden };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npx vitest run src/features/home/hooks.test.tsx`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/home/hooks.ts apps/web/src/features/home/hooks.test.tsx
git commit -m "feat(s-home-1): useMyTasks hook for the My-Tasks rail"
```

---

## Phase 2 — Presentational primitives

### Task 3: `StatLine`

**Files:**
- Create: `apps/web/src/features/home/StatLine.tsx`
- Test: `apps/web/src/features/home/StatLine.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { MantineProvider } from "@mantine/core";
import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { StatLine } from "./StatLine";

import type { ReactElement } from "react";
const wrap = (ui: ReactElement) => render(<MantineProvider>{ui}</MantineProvider>);

it("renders a value + label with a tone glyph and an accessible name", () => {
  wrap(<StatLine value="6 / 8" label="objectives on target" tone="green" />);
  const line = screen.getByLabelText("6 / 8 objectives on target");
  expect(line).toHaveTextContent("6 / 8");
  expect(line).toHaveTextContent("objectives on target");
});

it("renders a label-only status line (no value)", () => {
  wrap(<StatLine label="Mirror & blob integrity — clean" tone="green" />);
  expect(screen.getByLabelText("Mirror & blob integrity — clean")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npx vitest run src/features/home/StatLine.test.tsx`
Expected: FAIL (`Failed to resolve import "./StatLine"`).

- [ ] **Step 3: Write `StatLine.tsx`**

```tsx
import { Group, Text } from "@mantine/core";
import type { Rag } from "./rag";
import { RAG_META } from "./rag";

// One dashboard signal: a tone glyph (DP-7 redundant channel) + an optional bold tabular value + a
// label. Count lines pass a value ("6 / 8"); status lines fold the status into the label and omit value.
export function StatLine({ value, label, tone = "neutral" }: {
  value?: string | number;
  label: string;
  tone?: Rag;
}) {
  const hasValue = value !== undefined && value !== "";
  const name = hasValue ? `${value} ${label}` : label;
  return (
    <Group gap={8} wrap="nowrap" aria-label={name}>
      <Text span c={RAG_META[tone].color} aria-hidden style={{ lineHeight: 1 }}>
        {RAG_META[tone].glyph}
      </Text>
      <Text size="sm">
        {hasValue && (
          <Text span fw={500} style={{ fontVariantNumeric: "tabular-nums" }}>
            {value}
          </Text>
        )}
        {hasValue ? " " : ""}
        {label}
      </Text>
    </Group>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npx vitest run src/features/home/StatLine.test.tsx`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/home/StatLine.tsx apps/web/src/features/home/StatLine.test.tsx
git commit -m "feat(s-home-1): StatLine signal primitive"
```

### Task 4: `QuadrantCard` (+ `TileNoAccess`/`TileSkeleton`)

**Files:**
- Create: `apps/web/src/features/home/QuadrantCard.tsx`
- Test: `apps/web/src/features/home/QuadrantCard.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { screen, within } from "@testing-library/react";
import { expect, it } from "vitest";
import { renderWithProviders } from "../../test/render";
import { QuadrantCard, TileNoAccess } from "./QuadrantCard";
import { StatLine } from "./StatLine";

it("renders the PDCA chip, the RAG badge and a single Open link", () => {
  renderWithProviders(
    <QuadrantCard phase="PLAN" clauseLabel="Cl 4–7" rag="amber" openTo="/objectives" openLabel="Open objectives">
      <StatLine value="6 / 8" label="objectives on target" tone="green" />
    </QuadrantCard>,
  );
  const card = screen.getByRole("group", { name: /plan quadrant/i });
  expect(within(card).getByText(/PLAN · Cl 4–7/)).toBeInTheDocument();
  expect(within(card).getByLabelText(/status: amber/i)).toBeInTheDocument();
  const open = within(card).getByRole("link", { name: /open objectives/i });
  expect(open).toHaveAttribute("href", "/objectives");
});

it("omits the RAG badge when rag is null (loading / no-access)", () => {
  renderWithProviders(
    <QuadrantCard phase="ACT" clauseLabel="Cl 10" rag={null} openTo="/capa" openLabel="Open CAPA">
      <TileNoAccess />
    </QuadrantCard>,
  );
  expect(screen.queryByLabelText(/status:/i)).not.toBeInTheDocument();
  expect(screen.getByText(/no access to this section/i)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npx vitest run src/features/home/QuadrantCard.test.tsx`
Expected: FAIL (`Failed to resolve import "./QuadrantCard"`).

- [ ] **Step 3: Write `QuadrantCard.tsx`**

```tsx
import { Anchor, Badge, Group, Paper, Skeleton, Stack, Text } from "@mantine/core";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import type { PdcaPhase } from "../../lib/types";
import type { Rag } from "./rag";
import { RAG_META } from "./rag";

const PHASE_TOKEN: Record<PdcaPhase, string> = { PLAN: "plan", DO: "do", CHECK: "check", ACT: "act" };

// A calm no-access body (the whole tile's reads were forbidden) and a two-line skeleton (still loading).
export const TileNoAccess = () => (
  <Text size="sm" c="dimmed">No access to this section&apos;s data.</Text>
);
export const TileSkeleton = () => (
  <Stack gap={6}>
    <Skeleton height={14} width="80%" />
    <Skeleton height={14} width="55%" />
  </Stack>
);

// One PDCA region (doc-11 §5.1 "nav of four labeled regions"): an accent label chip + the headline RAG
// badge (omitted when rag is null) + the signal body + exactly one accent Open action (DP-2).
export function QuadrantCard({ phase, clauseLabel, rag, openTo, openLabel, children }: {
  phase: PdcaPhase;
  clauseLabel: string;
  rag: Rag | null;
  openTo: string;
  openLabel: string;
  children: ReactNode;
}) {
  const tok = PHASE_TOKEN[phase];
  return (
    <Paper withBorder radius="md" p="md" role="group" aria-label={`${phase} quadrant`}>
      <Stack gap="sm" h="100%">
        <Group justify="space-between" align="center" wrap="nowrap">
          <Text
            span
            fw={500}
            style={{
              background: `var(--es-${tok}-soft)`,
              color: `var(--es-${tok}-text)`,
              borderRadius: 8,
              padding: "2px 10px",
              fontSize: 13,
            }}
          >
            {phase} · {clauseLabel}
          </Text>
          {rag && (
            <Badge
              variant="light"
              color={RAG_META[rag].color}
              leftSection={<span aria-hidden>{RAG_META[rag].glyph}</span>}
              aria-label={`Status: ${RAG_META[rag].label}`}
            >
              {RAG_META[rag].label}
            </Badge>
          )}
        </Group>
        <Stack gap={6} style={{ flex: 1 }}>
          {children}
        </Stack>
        <Anchor component={Link} to={openTo} size="sm">
          {openLabel} <span aria-hidden="true">→</span>
        </Anchor>
      </Stack>
    </Paper>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npx vitest run src/features/home/QuadrantCard.test.tsx`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/home/QuadrantCard.tsx apps/web/src/features/home/QuadrantCard.test.tsx
git commit -m "feat(s-home-1): QuadrantCard frame + tile no-access/skeleton"
```

---

## Phase 3 — Header, quadrants, rail

### Task 5: `HealthSummary` (header ★-coverage band)

**Files:**
- Create: `apps/web/src/features/home/HealthSummary.tsx`
- Test: `apps/web/src/features/home/HealthSummary.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import type { ComplianceChecklist } from "../../lib/types";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { HealthSummary } from "./HealthSummary";

const checklist: ComplianceChecklist = {
  framework: "iso9001:2015",
  rollup: { total: 20, covered: 18, partial: 1, gap: 1, overdue_review: 2 },
  rows: [],
};

it("shows the mandatory-coverage status with the N9 microcopy, linking to /compliance", async () => {
  server.use(http.get("/api/v1/reports/compliance-checklist", () => HttpResponse.json(checklist)));
  renderWithProviders(<HealthSummary />);
  await waitFor(() => expect(screen.getByText(/18 \/ 20 mandatory items current/i)).toBeInTheDocument());
  expect(screen.getByText(/not a compliance verdict/i)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /coverage/i })).toHaveAttribute("href", "/compliance");
});

it("degrades calmly when coverage is forbidden", async () => {
  server.use(http.get("/api/v1/reports/compliance-checklist", () => HttpResponse.json({ code: "forbidden" }, { status: 403 })));
  renderWithProviders(<HealthSummary />);
  await waitFor(() => expect(screen.getByText(/coverage scoped to your access/i)).toBeInTheDocument());
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npx vitest run src/features/home/HealthSummary.test.tsx`
Expected: FAIL (`Failed to resolve import "./HealthSummary"`).

- [ ] **Step 3: Write `HealthSummary.tsx`**

```tsx
import { Anchor, Badge, Group, Paper, Skeleton, Text } from "@mantine/core";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useComplianceChecklist } from "../compliance/useComplianceChecklist";
import { coverageRag, RAG_META } from "./rag";

// The header health summary: the ★ mandatory-clause coverage as a single status (N9 — status against a
// configured rule, never a "you are compliant" verdict). The band drills to /compliance.
export function HealthSummary() {
  const { data, isLoading, isError, forbidden } = useComplianceChecklist();

  let body: ReactNode;
  if (forbidden) {
    body = <Text size="sm" c="dimmed">Coverage scoped to your access.</Text>;
  } else if (isLoading) {
    body = <Skeleton height={20} width={240} />;
  } else if (isError || !data) {
    body = <Text size="sm" c="dimmed">Couldn&apos;t load coverage.</Text>;
  } else {
    const rag = coverageRag(data.rollup);
    body = (
      <Group gap="sm" align="center" wrap="wrap">
        <Text fw={500}>
          {data.rollup.covered} / {data.rollup.total} mandatory items current
        </Text>
        <Badge
          variant="light"
          color={RAG_META[rag].color}
          leftSection={<span aria-hidden>{RAG_META[rag].glyph}</span>}
          aria-label={`Coverage status: ${RAG_META[rag].label}`}
        >
          {RAG_META[rag].label}
        </Badge>
        <Text size="xs" c="dimmed">
          status against configured thresholds — not a compliance verdict
        </Text>
      </Group>
    );
  }

  // Anchor(component={Link}) is the codebase's established polymorphic-link idiom (QuadrantCard/TopBar);
  // it wraps the Paper so the whole band is the /compliance drill-through with one discernible name.
  return (
    <Anchor
      component={Link}
      to="/compliance"
      underline="never"
      aria-label="QMS coverage summary; open the compliance checklist"
      style={{ display: "block", color: "inherit" }}
    >
      <Paper withBorder radius="md" p="md">
        {body}
      </Paper>
    </Anchor>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npx vitest run src/features/home/HealthSummary.test.tsx`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/home/HealthSummary.tsx apps/web/src/features/home/HealthSummary.test.tsx
git commit -m "feat(s-home-1): HealthSummary header coverage band"
```

### Task 6: `PlanCard`

**Files:**
- Create: `apps/web/src/features/home/PlanCard.tsx`
- Test: `apps/web/src/features/home/PlanCard.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import type { ComplianceChecklist, ObjectiveScorecard } from "../../lib/types";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { PlanCard } from "./PlanCard";

const scorecard = (over: Partial<ObjectiveScorecard> = {}): ObjectiveScorecard => ({
  total: 8, on_target: 6, by_rag: { green: 6, amber: 1, red: 1, unmeasured: 0 }, objectives: [], ...over,
});
const checklist = (overdue: number): ComplianceChecklist => ({
  framework: "iso9001:2015", rollup: { total: 20, covered: 18, partial: 1, gap: 1, overdue_review: overdue }, rows: [],
});

it("shows objectives on target + overdue reviews, RAG red when an objective is red", async () => {
  server.use(
    http.get("/api/v1/objectives/scorecard", () => HttpResponse.json(scorecard())),
    http.get("/api/v1/reports/compliance-checklist", () => HttpResponse.json(checklist(2))),
  );
  renderWithProviders(<PlanCard />);
  const card = await screen.findByRole("group", { name: /plan quadrant/i });
  // findByRole resolves on the immediately-rendered card frame while the data line is still a skeleton —
  // so the first content assertion must wait for the query to settle.
  await waitFor(() => expect(within(card).getByLabelText("6 / 8 objectives on target")).toBeInTheDocument());
  expect(within(card).getByLabelText("2 document reviews overdue")).toBeInTheDocument();
  await waitFor(() => expect(within(card).getByLabelText(/status: red/i)).toBeInTheDocument());
});

it("omits the overdue line when the checklist read is forbidden", async () => {
  server.use(
    http.get("/api/v1/objectives/scorecard", () => HttpResponse.json(scorecard({ by_rag: { green: 8, amber: 0, red: 0, unmeasured: 0 }, on_target: 8 }))),
    http.get("/api/v1/reports/compliance-checklist", () => HttpResponse.json({ code: "forbidden" }, { status: 403 })),
  );
  renderWithProviders(<PlanCard />);
  const card = await screen.findByRole("group", { name: /plan quadrant/i });
  await waitFor(() => expect(within(card).getByLabelText("8 / 8 objectives on target")).toBeInTheDocument());
  expect(within(card).queryByText(/reviews overdue/i)).not.toBeInTheDocument();
});

it("renders no-access when both reads are forbidden", async () => {
  server.use(
    http.get("/api/v1/objectives/scorecard", () => HttpResponse.json({ code: "forbidden" }, { status: 403 })),
    http.get("/api/v1/reports/compliance-checklist", () => HttpResponse.json({ code: "forbidden" }, { status: 403 })),
  );
  renderWithProviders(<PlanCard />);
  const card = await screen.findByRole("group", { name: /plan quadrant/i });
  await waitFor(() => expect(within(card).getByText(/no access to this section/i)).toBeInTheDocument());
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npx vitest run src/features/home/PlanCard.test.tsx`
Expected: FAIL (`Failed to resolve import "./PlanCard"`).

- [ ] **Step 3: Write `PlanCard.tsx`**

```tsx
import type { ReactNode } from "react";
import { useObjectiveScorecard } from "../objectives/hooks";
import { useComplianceChecklist } from "../compliance/useComplianceChecklist";
import { QuadrantCard, TileNoAccess, TileSkeleton } from "./QuadrantCard";
import { StatLine } from "./StatLine";
import { overdueRag, planObjectivesRag, worstRag, type Rag } from "./rag";

// PLAN (Cl 4–7): Quality Objectives on target (server by_rag, read verbatim) + overdue document reviews.
export function PlanCard() {
  const sc = useObjectiveScorecard();
  const cl = useComplianceChecklist();

  const lines: ReactNode[] = [];
  const rags: Rag[] = [];

  if (!sc.forbidden && !sc.isError && sc.data) {
    const rag = planObjectivesRag(sc.data.by_rag);
    rags.push(rag);
    lines.push(
      <StatLine key="obj" value={`${sc.data.on_target} / ${sc.data.total}`} label="objectives on target" tone={rag} />,
    );
  }
  if (!cl.forbidden && !cl.isError && cl.data) {
    const n = cl.data.rollup.overdue_review;
    const rag = overdueRag(n);
    rags.push(rag);
    lines.push(<StatLine key="rev" value={n} label="document reviews overdue" tone={rag} />);
  }

  const allForbidden = sc.forbidden && cl.forbidden;
  const loading = sc.isLoading || cl.isLoading;

  return (
    <QuadrantCard
      phase="PLAN"
      clauseLabel="Cl 4–7"
      rag={rags.length ? worstRag(rags) : null}
      openTo="/objectives"
      openLabel="Open objectives"
    >
      {allForbidden ? (
        <TileNoAccess />
      ) : lines.length ? (
        lines
      ) : loading ? (
        <TileSkeleton />
      ) : (
        <StatLine label="Couldn't load this section." tone="neutral" />
      )}
    </QuadrantCard>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npx vitest run src/features/home/PlanCard.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/home/PlanCard.tsx apps/web/src/features/home/PlanCard.test.tsx
git commit -m "feat(s-home-1): PLAN quadrant card"
```

### Task 7: `DoCard`

**Files:**
- Create: `apps/web/src/features/home/DoCard.tsx`
- Test: `apps/web/src/features/home/DoCard.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import type { DriftStatus } from "../../lib/types";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { DoCard } from "./DoCard";

const clean = { status: "CLEAN" as const, started_at: "x", finished_at: "y", counts: {}, triggered_by: "beat" as const };
const drift: DriftStatus = {
  scans: { MIRROR: clean, BLOB_REHASH: clean },
  blob_coverage: { total: 10, never_verified: 0, failing: 0, oldest_verified_at: null },
  superseded_copies: { versions: 2, copies: 3 },
};

it("shows clean integrity, superseded copies and the ack count", async () => {
  server.use(
    http.get("/api/v1/admin/drift/status", () => HttpResponse.json(drift)),
    http.get("/api/v1/tasks", () => HttpResponse.json([{ id: "a1" }, { id: "a2" }])),
  );
  renderWithProviders(<DoCard />);
  const card = await screen.findByRole("group", { name: /do quadrant/i });
  await waitFor(() => expect(within(card).getByLabelText(/mirror & blob integrity — clean/i)).toBeInTheDocument());
  expect(within(card).getByLabelText("3 superseded copies in circulation")).toBeInTheDocument();
  expect(within(card).getByLabelText("2 acknowledgements awaiting you")).toBeInTheDocument();
  await waitFor(() => expect(within(card).getByLabelText(/status: green/i)).toBeInTheDocument());
});

it("stays visible via the self-scoped ack count even when drift is forbidden", async () => {
  server.use(
    http.get("/api/v1/admin/drift/status", () => HttpResponse.json({ code: "forbidden" }, { status: 403 })),
    http.get("/api/v1/tasks", () => HttpResponse.json([{ id: "a1" }])),
  );
  renderWithProviders(<DoCard />);
  const card = await screen.findByRole("group", { name: /do quadrant/i });
  await waitFor(() => expect(within(card).getByLabelText("1 acknowledgements awaiting you")).toBeInTheDocument());
  expect(within(card).queryByText(/no access to this section/i)).not.toBeInTheDocument();
});

it("shows a neutral couldn't-load (not green) when drift errors with no acks", async () => {
  server.use(
    http.get("/api/v1/admin/drift/status", () => HttpResponse.json({ code: "error" }, { status: 500 })),
    http.get("/api/v1/tasks", () => HttpResponse.json([])),
  );
  renderWithProviders(<DoCard />);
  const card = await screen.findByRole("group", { name: /do quadrant/i });
  await waitFor(() => expect(within(card).getByText(/couldn't load this section/i)).toBeInTheDocument());
  expect(within(card).queryByText(/all caught up/i)).not.toBeInTheDocument();
  expect(within(card).queryByLabelText(/status: green/i)).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npx vitest run src/features/home/DoCard.test.tsx`
Expected: FAIL (`Failed to resolve import "./DoCard"`).

- [ ] **Step 3: Write `DoCard.tsx`**

```tsx
import type { ReactNode } from "react";
import { useAckCount } from "../../app/shell/useAckCount";
import { useDriftStatus } from "../drift/hooks";
import { QuadrantCard, TileNoAccess, TileSkeleton } from "./QuadrantCard";
import { StatLine } from "./StatLine";
import { driftRag, driftStatusText, worstRag, type Rag } from "./rag";

// DO (Cl 7–8): controlled-document integrity (mirror + blob drift) + superseded copies still in
// circulation + the caller's acknowledgements (self-scoped — DO stays visible to everyone via acks).
export function DoCard() {
  const dr = useDriftStatus();
  const ackCount = useAckCount();

  const lines: ReactNode[] = [];
  const rags: Rag[] = [];

  if (!dr.forbidden && !dr.isError && dr.data) {
    const rag = driftRag(dr.data);
    rags.push(rag);
    lines.push(<StatLine key="int" label={`Mirror & blob integrity — ${driftStatusText(dr.data)}`} tone={rag} />);
    if (dr.data.superseded_copies.copies > 0) {
      lines.push(
        <StatLine key="sc" value={dr.data.superseded_copies.copies} label="superseded copies in circulation" tone="neutral" />,
      );
    }
  }
  if (ackCount > 0) {
    lines.push(<StatLine key="ack" value={ackCount} label="acknowledgements awaiting you" tone="neutral" />);
  }

  const allForbidden = dr.forbidden && ackCount === 0;
  const loading = dr.isLoading;

  return (
    <QuadrantCard
      phase="DO"
      clauseLabel="Cl 7–8"
      rag={rags.length ? worstRag(rags) : null}
      openTo="/drift"
      openLabel="Open drift status"
    >
      {allForbidden ? (
        <TileNoAccess />
      ) : lines.length ? (
        lines
      ) : loading ? (
        <TileSkeleton />
      ) : (
        <StatLine label="Couldn't load this section." tone="neutral" />
      )}
    </QuadrantCard>
  );
}
```

> ⚠ The final fallback is reachable ONLY when the drift read errors (non-403) with zero acks — a
> successful drift read always pushes the integrity line, and a forbidden read with zero acks hits
> `TileNoAccess`. So it must read as a neutral "couldn't load", NOT a green "all caught up" (which would
> paint a green health state over an errored integrity read — the diff-critic finding). It matches the
> three sibling cards' fallback.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npx vitest run src/features/home/DoCard.test.tsx`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/home/DoCard.tsx apps/web/src/features/home/DoCard.test.tsx
git commit -m "feat(s-home-1): DO quadrant card"
```

### Task 8: `CheckCard`

**Files:**
- Create: `apps/web/src/features/home/CheckCard.tsx`
- Test: `apps/web/src/features/home/CheckCard.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import type { AuditList, ComplianceChecklist } from "../../lib/types";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { CheckCard } from "./CheckCard";

const audits: AuditList = {
  data: [
    { id: "a1", identifier: "REC-1", title: "Q2 audit", plan_id: "p1", lead_auditor_user_id: null, state: "InProgress", started_at: null, completed_at: null, result_summary: null, created_at: null },
    { id: "a2", identifier: "REC-2", title: "Q1 audit", plan_id: "p2", lead_auditor_user_id: null, state: "Closed", started_at: null, completed_at: null, result_summary: null, created_at: null },
  ],
};
const checklist: ComplianceChecklist = {
  framework: "iso9001:2015", rollup: { total: 20, covered: 18, partial: 1, gap: 1, overdue_review: 0 }, rows: [],
};

it("shows open audits + coverage, RAG red on a gap", async () => {
  server.use(
    http.get("/api/v1/audits", () => HttpResponse.json(audits)),
    http.get("/api/v1/reports/compliance-checklist", () => HttpResponse.json(checklist)),
  );
  renderWithProviders(<CheckCard />);
  const card = await screen.findByRole("group", { name: /check quadrant/i });
  // The first content assertion must wait for the query to settle (the card frame renders immediately).
  await waitFor(() => expect(within(card).getByLabelText("1 open audits")).toBeInTheDocument());
  expect(within(card).getByLabelText("18 / 20 mandatory clauses covered")).toBeInTheDocument();
  await waitFor(() => expect(within(card).getByLabelText(/status: red/i)).toBeInTheDocument());
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npx vitest run src/features/home/CheckCard.test.tsx`
Expected: FAIL (`Failed to resolve import "./CheckCard"`).

- [ ] **Step 3: Write `CheckCard.tsx`**

```tsx
import type { ReactNode } from "react";
import { useAudits } from "../audits/hooks";
import { useComplianceChecklist } from "../compliance/useComplianceChecklist";
import { QuadrantCard, TileNoAccess, TileSkeleton } from "./QuadrantCard";
import { StatLine } from "./StatLine";
import { coverageRag, openAuditsCount, worstRag, type Rag } from "./rag";

// CHECK (Cl 9): open internal audits (informational count) + ★ mandatory-clause coverage (the RAG signal).
// Open-NC findings are deferred (no org-wide findings endpoint; spec §2).
export function CheckCard() {
  const au = useAudits();
  const cl = useComplianceChecklist();

  const lines: ReactNode[] = [];
  const rags: Rag[] = [];

  if (!au.forbidden && !au.isError && au.data) {
    lines.push(<StatLine key="aud" value={openAuditsCount(au.data)} label="open audits" tone="neutral" />);
  }
  if (!cl.forbidden && !cl.isError && cl.data) {
    const rag = coverageRag(cl.data.rollup);
    rags.push(rag);
    lines.push(
      <StatLine key="cov" value={`${cl.data.rollup.covered} / ${cl.data.rollup.total}`} label="mandatory clauses covered" tone={rag} />,
    );
  }

  const allForbidden = au.forbidden && cl.forbidden;
  const loading = au.isLoading || cl.isLoading;

  return (
    <QuadrantCard
      phase="CHECK"
      clauseLabel="Cl 9"
      rag={rags.length ? worstRag(rags) : null}
      openTo="/audits"
      openLabel="Open audits"
    >
      {allForbidden ? (
        <TileNoAccess />
      ) : lines.length ? (
        lines
      ) : loading ? (
        <TileSkeleton />
      ) : (
        <StatLine label="Couldn't load this section." tone="neutral" />
      )}
    </QuadrantCard>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npx vitest run src/features/home/CheckCard.test.tsx`
Expected: PASS (1 test).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/home/CheckCard.tsx apps/web/src/features/home/CheckCard.test.tsx
git commit -m "feat(s-home-1): CHECK quadrant card"
```

### Task 9: `ActCard`

**Files:**
- Create: `apps/web/src/features/home/ActCard.tsx`
- Test: `apps/web/src/features/home/ActCard.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import type { CapaList, ComplaintList, NcrList } from "../../lib/types";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { ActCard } from "./ActCard";

const capas: CapaList = {
  data: [
    { id: "c1", identifier: "REC-1", title: "x", source: "audit", severity: "Major", process_id: null, close_state: "Verify", cycle_marker: 0, origin_finding_id: null, raised_by: null, created_at: null },
    { id: "c2", identifier: "REC-2", title: "y", source: "audit", severity: "Minor", process_id: null, close_state: "Closed", cycle_marker: 0, origin_finding_id: null, raised_by: null, created_at: null },
  ],
};
const ncrs: NcrList = {
  data: [
    { id: "n1", identifier: "NCR-1", source: "internal", description: "d", severity: "Major", process_id: null, disposition: null, disposition_authorized_by: null, disposition_notes: null, disposed_at: null, created_at: "x" },
  ],
};
const complaints: ComplaintList = {
  data: [
    { id: "k1", identifier: "REC-3", customer: "ACME", received_at: null, channel: null, description: "d", severity: null, spawned_capa_id: null },
  ],
};

it("shows open CAPAs, awaiting NCRs and complaints, RAG red on an awaiting NCR", async () => {
  server.use(
    http.get("/api/v1/capas", () => HttpResponse.json(capas)),
    http.get("/api/v1/ncrs", () => HttpResponse.json(ncrs)),
    http.get("/api/v1/complaints", () => HttpResponse.json(complaints)),
  );
  renderWithProviders(<ActCard />);
  const card = await screen.findByRole("group", { name: /act quadrant/i });
  // The first content assertion must wait for the query to settle (the card frame renders immediately).
  await waitFor(() => expect(within(card).getByLabelText("1 CAPAs open")).toBeInTheDocument());
  expect(within(card).getByLabelText("1 NCRs awaiting disposition")).toBeInTheDocument();
  expect(within(card).getByLabelText("1 complaints awaiting triage")).toBeInTheDocument();
  await waitFor(() => expect(within(card).getByLabelText(/status: red/i)).toBeInTheDocument());
});

it("renders no-access when all three reads are forbidden", async () => {
  const forbid = () => HttpResponse.json({ code: "forbidden" }, { status: 403 });
  server.use(
    http.get("/api/v1/capas", forbid),
    http.get("/api/v1/ncrs", forbid),
    http.get("/api/v1/complaints", forbid),
  );
  renderWithProviders(<ActCard />);
  const card = await screen.findByRole("group", { name: /act quadrant/i });
  await waitFor(() => expect(within(card).getByText(/no access to this section/i)).toBeInTheDocument());
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npx vitest run src/features/home/ActCard.test.tsx`
Expected: FAIL (`Failed to resolve import "./ActCard"`).

- [ ] **Step 3: Write `ActCard.tsx`**

```tsx
import type { ReactNode } from "react";
import { useCapas, useComplaints, useNcrs } from "../capa/hooks";
import { QuadrantCard, TileNoAccess, TileSkeleton } from "./QuadrantCard";
import { StatLine } from "./StatLine";
import { capasOpenCount, complaintsAwaitingCount, countRag, ncrsAwaitingCount, worstRag, type Rag } from "./rag";

// ACT (Cl 10): open CAPAs (amber when >0) + NCRs awaiting disposition (red when >0) + complaints awaiting
// triage (amber when >0). Tile RAG = worst of the visible signals (subsumes the spec's actRag).
export function ActCard() {
  const ca = useCapas();
  const nc = useNcrs();
  const co = useComplaints();

  const lines: ReactNode[] = [];
  const rags: Rag[] = [];

  if (!ca.forbidden && !ca.isError && ca.data) {
    const n = capasOpenCount(ca.data);
    const rag = countRag(n, "amber");
    rags.push(rag);
    lines.push(<StatLine key="capa" value={n} label="CAPAs open" tone={rag} />);
  }
  if (!nc.forbidden && !nc.isError && nc.data) {
    const n = ncrsAwaitingCount(nc.data);
    const rag = countRag(n, "red");
    rags.push(rag);
    lines.push(<StatLine key="ncr" value={n} label="NCRs awaiting disposition" tone={rag} />);
  }
  if (!co.forbidden && !co.isError && co.data) {
    const n = complaintsAwaitingCount(co.data);
    const rag = countRag(n, "amber");
    rags.push(rag);
    lines.push(<StatLine key="comp" value={n} label="complaints awaiting triage" tone={rag} />);
  }

  const allForbidden = ca.forbidden && nc.forbidden && co.forbidden;
  const loading = ca.isLoading || nc.isLoading || co.isLoading;

  return (
    <QuadrantCard
      phase="ACT"
      clauseLabel="Cl 10"
      rag={rags.length ? worstRag(rags) : null}
      openTo="/capa"
      openLabel="Open CAPA & NCR"
    >
      {allForbidden ? (
        <TileNoAccess />
      ) : lines.length ? (
        lines
      ) : loading ? (
        <TileSkeleton />
      ) : (
        <StatLine label="Couldn't load this section." tone="neutral" />
      )}
    </QuadrantCard>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npx vitest run src/features/home/ActCard.test.tsx`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/home/ActCard.tsx apps/web/src/features/home/ActCard.test.tsx
git commit -m "feat(s-home-1): ACT quadrant card"
```

### Task 10: `MyTasksRail`

**Files:**
- Create: `apps/web/src/features/home/MyTasksRail.tsx`
- Test: `apps/web/src/features/home/MyTasksRail.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import type { Task } from "../../lib/types";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { MyTasksRail } from "./MyTasksRail";

const task = (id: string, type: Task["type"], due: string | null): Task => ({
  id, instance_id: `i-${id}`, stage_key: "s", type, state: "PENDING",
  assignee_user_id: null, candidate_pool: null, action_expected: null, due_at: due,
});

it("shows the count, the soonest-due first, and a see-all link", async () => {
  server.use(http.get("/api/v1/tasks", () => HttpResponse.json([
    task("a", "REVIEW", "2026-06-20T00:00:00+00:00"),
    task("b", "DOC_ACK", "2026-06-12T00:00:00+00:00"),
    task("c", "CAPA_ACTION", null),
    task("d", "APPROVE", "2026-06-15T00:00:00+00:00"),
  ])));
  renderWithProviders(<MyTasksRail />);
  await waitFor(() => expect(screen.getByText(/my tasks \(4\)/i)).toBeInTheDocument());
  // top 3 by due date asc (b 12th, d 15th, a 20th); the null-due "c" is pushed out of the top 3
  const rows = screen.getAllByText(/due 2026-/);
  expect(rows[0]).toHaveTextContent("due 2026-06-12");
  expect(screen.getByRole("link", { name: /see all my tasks/i })).toHaveAttribute("href", "/tasks");
});

it("shows a calm caught-up state when there are no tasks", async () => {
  server.use(http.get("/api/v1/tasks", () => HttpResponse.json([])));
  renderWithProviders(<MyTasksRail />);
  await waitFor(() => expect(screen.getByText(/you're all caught up/i)).toBeInTheDocument());
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npx vitest run src/features/home/MyTasksRail.test.tsx`
Expected: FAIL (`Failed to resolve import "./MyTasksRail"`).

- [ ] **Step 3: Write `MyTasksRail.tsx`**

```tsx
import { Anchor, Group, Paper, Skeleton, Stack, Text } from "@mantine/core";
import { Link } from "react-router-dom";
import type { Task, TaskType } from "../../lib/types";
import { useMyTasks } from "./hooks";

// Friendly labels for the task types that reach a personal inbox (doc 10 §8). Unmapped types fall back
// to the raw token.
const TASK_LABEL: Record<TaskType, string> = {
  APPROVE: "Approval", REVIEW: "Document review", PERIODIC_REVIEW: "Periodic review",
  DOC_ACK: "Acknowledgement", AUDIT_TASK: "Audit task", FINDING_ACK: "Finding acknowledgement",
  CAPA_STAGE: "CAPA stage", CAPA_ACTION: "CAPA action", VERIFY: "Verification",
  MR_INPUT: "Management-review input", MR_ACTION: "Management-review action", DCR_TRIAGE: "Change-request triage",
};

// Soonest-due first; a null due_at sorts last (ISO strings compare lexically).
function sortByDue(tasks: Task[]): Task[] {
  return [...tasks].sort((a, b) => {
    if (a.due_at === b.due_at) return 0;
    if (a.due_at === null) return 1;
    if (b.due_at === null) return -1;
    return a.due_at < b.due_at ? -1 : 1;
  });
}

// ⚠ The /tasks LIST omits subject_type/subject_id, so rows show task type + action + due, NOT the doc
// name — names live one click deeper at /tasks (DP-3). Self-scoped (no permission key); always visible.
export function MyTasksRail() {
  const { data, isLoading, isError } = useMyTasks();
  const tasks = data ?? [];
  const top = sortByDue(tasks).slice(0, 3);

  return (
    <Paper withBorder radius="md" p="md">
      <Group justify="space-between" align="center" mb="sm">
        <Text fw={500}>My tasks{tasks.length ? ` (${tasks.length})` : ""}</Text>
        <Anchor component={Link} to="/tasks" size="sm">See all my tasks <span aria-hidden="true">→</span></Anchor>
      </Group>
      {isLoading ? (
        <Skeleton height={16} width="70%" />
      ) : isError ? (
        <Text size="sm" c="dimmed">Couldn&apos;t load your tasks.</Text>
      ) : tasks.length === 0 ? (
        <Text size="sm" c="dimmed">You&apos;re all caught up.</Text>
      ) : (
        <Stack gap={6}>
          {top.map((t) => (
            <Text key={t.id} size="sm">
              <Text span fw={500}>{TASK_LABEL[t.type]}</Text>
              {t.action_expected ? ` · ${t.action_expected}` : ""}
              {t.due_at ? ` · due ${t.due_at.slice(0, 10)}` : ""}
            </Text>
          ))}
        </Stack>
      )}
    </Paper>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npx vitest run src/features/home/MyTasksRail.test.tsx`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/home/MyTasksRail.tsx apps/web/src/features/home/MyTasksRail.test.tsx
git commit -m "feat(s-home-1): My-Tasks rail"
```

---

## Phase 4 — Composition

### Task 11: `HomePage` rewrite

**Files:**
- Modify (REWRITE): `apps/web/src/features/home/HomePage.tsx`
- Modify (REWRITE): `apps/web/src/features/home/HomePage.test.tsx`

- [ ] **Step 1: Write the failing test (rewrite the existing test)**

```tsx
import { axe } from "jest-axe";
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { HomePage } from "./HomePage";

const forbid = () => HttpResponse.json({ code: "forbidden" }, { status: 403 });

it("renders the QMS health heading and the four PDCA quadrants, accessibly", async () => {
  const { container } = renderWithProviders(<HomePage />);
  expect(screen.getByRole("heading", { name: /qms health/i })).toBeInTheDocument();
  for (const name of [/plan quadrant/i, /do quadrant/i, /check quadrant/i, /act quadrant/i]) {
    expect(await screen.findByRole("group", { name })).toBeInTheDocument();
  }
  expect(await axe(container)).toHaveNoViolations();
});

it("under the bare-demo shape, DO + My-Tasks render while content quadrants show no-access", async () => {
  // demo holds only drift.read + self-scoped tasks; every content read 403s.
  server.use(
    http.get("/api/v1/objectives/scorecard", forbid),
    http.get("/api/v1/reports/compliance-checklist", forbid),
    http.get("/api/v1/audits", forbid),
    http.get("/api/v1/capas", forbid),
    http.get("/api/v1/ncrs", forbid),
    http.get("/api/v1/complaints", forbid),
    http.get("/api/v1/admin/drift/status", () => HttpResponse.json({
      scans: { MIRROR: { status: "CLEAN", started_at: "x", finished_at: "y", counts: {}, triggered_by: "beat" }, BLOB_REHASH: { status: "CLEAN", started_at: "x", finished_at: "y", counts: {}, triggered_by: "beat" } },
      blob_coverage: { total: 5, never_verified: 0, failing: 0, oldest_verified_at: null },
      superseded_copies: { versions: 0, copies: 0 },
    })),
    http.get("/api/v1/tasks", () => HttpResponse.json([{ id: "t1", instance_id: "i1", stage_key: "s", type: "DOC_ACK", state: "PENDING", assignee_user_id: null, candidate_pool: null, action_expected: null, due_at: null }])),
  );
  renderWithProviders(<HomePage />);
  const planCard = await screen.findByRole("group", { name: /plan quadrant/i });
  await waitFor(() => expect(within(planCard).getByText(/no access to this section/i)).toBeInTheDocument());
  const doCard = screen.getByRole("group", { name: /do quadrant/i });
  await waitFor(() => expect(within(doCard).getByLabelText(/mirror & blob integrity — clean/i)).toBeInTheDocument());
  expect(screen.getByText(/my tasks/i)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npx vitest run src/features/home/HomePage.test.tsx`
Expected: FAIL (the new quadrant `group`s don't exist yet — the old `HomePage` renders only the placeholder).

- [ ] **Step 3: Rewrite `HomePage.tsx`**

```tsx
import { Container, SimpleGrid, Stack, Title } from "@mantine/core";
import { ActCard } from "./ActCard";
import { CheckCard } from "./CheckCard";
import { DoCard } from "./DoCard";
import { HealthSummary } from "./HealthSummary";
import { MyTasksRail } from "./MyTasksRail";
import { PlanCard } from "./PlanCard";

// The QMS Health home dashboard (doc 11 §5.1): a calm four-quadrant PDCA wheel — counts + RAG only,
// each tile composed from an already-shipped read and degrading independently (spec §5). Ungated (the
// landing page); gating lives on the tiles. N9: status against a rule, never an auto-compliance verdict.
export function HomePage() {
  return (
    <Container size="lg" py="md">
      <Stack gap="lg">
        <Title order={1}>QMS health</Title>
        <HealthSummary />
        <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
          <PlanCard />
          <DoCard />
          <CheckCard />
          <ActCard />
        </SimpleGrid>
        <MyTasksRail />
      </Stack>
    </Container>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npx vitest run src/features/home/HomePage.test.tsx`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/home/HomePage.tsx apps/web/src/features/home/HomePage.test.tsx
git commit -m "feat(s-home-1): compose the PDCA Home dashboard"
```

---

## Phase 5 — Wire + close

### Task 12: Full gate, review, smoke, docs, PR

- [ ] **Step 1: Run the full web gate**

Run: `cd apps/web && npx eslint src && npx tsc --noEmit && npm run build && npx vitest run`
Expected: all green. ⚠ `tsc --noEmit` is the gate that catches the jest-dom × vitest `expect` trap and
the `noUncheckedIndexedAccess` array-index nits a per-file vitest run misses. Capture the new test count
and confirm the delta from the **627** baseline: 28 new (rag 8 + hooks 2 + StatLine 2 + QuadrantCard 2 +
HealthSummary 2 + PlanCard 3 + DoCard 2 + CheckCard 1 + ActCard 2 + MyTasksRail 2 + HomePage 2) − 1
removed placeholder = **net +27 → 654**.

If anything fails: fix inline, re-run the specific file, then re-run the full gate. Do NOT proceed until green.

- [ ] **Step 2: Confirm the contract is untouched**

Run: `git status --short packages/contracts/openapi.yaml`
Expected: no output (no change — front-end-only; the `contracts` CI job stays green with no diff).

- [ ] **Step 3: Run the diff-critic agent on the branch diff**

Use the `Agent` tool, `subagent_type: diff-critic`, pointed at the branch diff vs `main`. Focus the
review on: every MSW fixture matching the real serializer (the `satisfies` shapes in §3), the per-signal
and whole-tile forbidden-degrade paths (a forbidden signal must NOT drag a tile to red and must NOT
crash), the `worstRag`/`driftRag` rules, and the My-Tasks sort (null-due last). Fold only confirmed
findings; fix them with a follow-up commit + a regression test.

- [ ] **Step 4: Chrome-MCP live smoke**

Bring the stack up if needed (`just up s`). The web image serves a baked build — rebuild + hard-refresh
after the merge build, but for the dev smoke run `npm run dev` or rely on the running stack. Smoke in two
passes (find-then-click in separate batches, text-first verification, client-side nav only):
1. **Bare demo** (`demo` / `Demo-Password-1`, System Administrator): load `/`. Expect the DO quadrant
   populated (drift.read held) + the My-Tasks rail; PLAN/CHECK/ACT show "No access to this section's data";
   the header shows "Coverage scoped to your access".
2. **Full wheel:** grant SYSTEM overrides on the **live demo `app_user` row** (org short_code **AHT**) for
   `objective.read`, `report.compliance_checklist.read`, `audit.read`, `capa.read`, `ncr.read`,
   `record.read` (the established override mechanic — NOT `grant-role`). Reload `/`: expect all four
   quadrants + the header coverage to populate with live counts. Click each `Open ▸` and the header band;
   confirm each lands on `/objectives`, `/drift`, `/audits`, `/capa`, `/compliance` respectively.

- [ ] **Step 5: Write the slice-history entry + the CLAUDE.md learning + the web-test delta**

- Add an `S-home-1` entry to `docs/slice-history.md` (the front-end-only trailing-slice shape; the
  composed-many-serializers twist; the named deferrals; the +N web-test delta; "CLOSES the v1 web track").
- Add one `Recent learnings` line to `CLAUDE.md` (newest first; demote the oldest if over ~12) — capture:
  front-end-only Home wheel composing 6 existing reads; the per-signal/whole-tile forbidden-degrade rule
  (worst-of-visible RAG, forbidden never drags a tile); the My-Tasks rail shows type+due not the doc name
  (LIST omits subject_*); demo lights up only DO + rail without overrides; any diff-critic/Codex finding.
- Update the `Current status` web-test count (627 → new total).

- [ ] **Step 6: Commit the docs + open the PR**

```bash
git add docs/slice-history.md CLAUDE.md
git commit -m "docs(s-home-1): slice-history + learnings for the PDCA Home dashboard"
```

Then open the PR against `main` (use a `--body-file`, never an inline `@'…'@` body on this box — the
PS 5.1 de-tokenization trap). Title: `feat(s-home-1): PDCA Home dashboard (QMS Health) — the v1 web-track finale (#NNN)`.

- [ ] **Step 7: Triage Codex's PR review**

Disregard multi-tenant / cross-org nitpicks (moot under D1 single-org). FIX any genuine non-tenant bug
(last slice Codex caught 2 real error-state bugs the diff-critic missed) — add a regression test, re-run
`/check-web`, push. Squash-merge after green CI on the owner's OK.

---

## Self-review notes (author)

- **Spec coverage:** §3 data contract → T1 (rules) + every card's fixtures pinned to `lib/types.ts`; §4
  gating → the per-card forbidden-degrade (T6–T9) + the bare-demo HomePage test (T11) + the smoke override
  list (T12); §5.1 header → T5; §5.2 quadrants → T4 + T6–T9; §5.3 rail → T10; §5.4 degradation → the
  forbidden/empty/loading branches in every card + T11; §9 testing → every task; §2 deferrals → carried in
  the docs (T12-5) and reflected by the absence of open-NC/risk/mgmt-review code.
- **No new key/endpoint/contract/route/nav** — confirmed: only `features/home/` files change + the docs.
  `App.tsx`/`LeftRail.tsx` untouched (index route + unconditional Home already exist).
- **Type consistency:** `Rag` (rag.ts) used identically across StatLine/QuadrantCard/all cards; `RAG_META`
  keys = the four `Rag` values; the count helpers' names match their call sites; `worstRag` is the single
  tile-RAG roller everywhere; `TileNoAccess`/`TileSkeleton` exported from QuadrantCard and imported by all
  four cards.
