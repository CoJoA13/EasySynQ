# S-web-8 — Drift-family UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface the completed drift family in the SPA: a `drift.read`-gated `/drift` surface (status + D4 superseded-copies), the `PERIODIC_REVIEW` task leg in `/tasks`, doc-detail review fields with a `manage_metadata`-gated period editor, and the compliance checklist's overdue-review leg.

**Architecture:** Front-end-only (no migration/key/endpoint/contract). New `features/drift/` feature; three existing features extended (`review`, `document`, `compliance`). Every MSW fixture pins to the real serializers (spec §2 of `docs/superpowers/specs/2026-06-10-s-web-8-drift-review-ui-design.md` — read it first).

**Tech Stack:** React 18 + TS strict, Mantine, @tanstack/react-query, react-router, vitest + MSW + jest-axe.

**Branch:** `feat/sweb8-drift-review-ui` (already created; the spec commit is on it).

**Verification commands** (run from `apps/web/`):
- Single file: `npx vitest run src/features/drift/DriftStatusPage.test.tsx`
- Full suite (thrash-safe): `npx vitest run --pool=forks --poolOptions.forks.singleFork=true`
- Full gate (repo root): `/check-web` (eslint + tsc --noEmit + build + tests)

**Standing traps that apply to every task** (engineering-patterns "Web SPA testing"):
fixtures-from-real-serializers; `/me`.id never `profile.sub`; the DOCUMENT/CAPA paths stay byte-identical; conditional modal rendering (close unmounts); explicit `null` to clear on PATCH; distinct aria-labels; calm-403 via a `forbidden` flag; `satisfies <Type>` on fixtures so strict tsc enforces shape.

---

### Task 1: Types + fixtures + MSW defaults (the shape ground-truth)

**Files:**
- Modify: `apps/web/src/lib/types.ts`
- Modify: `apps/web/src/test/msw/handlers.ts`

- [ ] **Step 1: Extend `lib/types.ts`**

Add to `DocumentSummary` (after `created_at`, before `clause_refs`) — these four are ALWAYS emitted by `_document` (`api/documents.py:168-171`), so they are required, not optional:

```ts
  // S-drift-1 review scheduling (always emitted). review_state is server-derived (org tz, 30-day
  // due_soon window) — the client never recomputes it.
  review_period_months: number | null;
  next_review_due: string | null; // a DATE — "YYYY-MM-DD"
  last_reviewed_at: string | null;
  review_state: ReviewState | null;
```

Add near the top (before `DocumentSummary`):

```ts
export type ReviewState = "current" | "due_soon" | "overdue";
```

Extend the checklist types:

```ts
export interface ChecklistRollup {
  total: number;
  covered: number;
  partial: number;
  gap: number;
  overdue_review: number; // S-drift-1: count of rows with overdue_review=true
}

export interface ChecklistRow {
  clause_id: string;
  number: string;
  title: string;
  pdca_phase: PdcaPhase;
  mapped_count: number;
  effective_count: number;
  status: CoverageStatus;
  overdue_review: boolean; // orthogonal to status — never a fourth coverage state
}
```

Extend the decision types (keep `DecisionResult` untouched — the engine paths still return it):

```ts
export type DecisionOutcome = "approve" | "changes_requested" | "reject" | "complete";

export type DecisionSubjectType = "DOCUMENT" | "CAPA" | "PERIODIC_REVIEW";

// POST /tasks/{id}/decision for a PERIODIC_REVIEW subject returns the wf-engine dict, NOT
// DecisionResult (services/vault/review.py:245-380). The UI ignores the body (invalidate+refetch).
export interface PeriodicReviewDecisionResult {
  current_state: string;
  replayed: boolean;
  document_id?: string;
  next_review_due?: string | null;
  signature_event_id?: string | null;
}
```

Add the drift types (new section at the end, `// ---- S-web-8 drift surface ----`):

```ts
export type DriftScanStatusValue = "CLEAN" | "DIVERGENT" | "FAILED";

// One drift_scan row (openapi DriftScanSummary). counts is an OPEN bag — unknown keys are
// additive (S-drift-3 §10a); render generically, never destructure a closed set.
export interface DriftScanSummary {
  status: DriftScanStatusValue;
  started_at: string;
  finished_at: string | null;
  counts: Record<string, unknown>;
  triggered_by: "beat" | "sync" | "cli";
}

export interface DriftStatus {
  scans: { MIRROR: DriftScanSummary | null; BLOB_REHASH: DriftScanSummary | null };
  blob_coverage: {
    total: number;
    never_verified: number;
    failing: number; // unresolved verify_failed_at pins — the live alarm count
    oldest_verified_at: string | null;
  };
  superseded_copies: { versions: number; copies: number };
}

export interface SupersededCopyRow {
  document_id: string;
  identifier: string;
  version_id: string;
  revision_label: string;
  version_state: "Superseded" | "Obsolete";
  current_revision_label: string | null; // null when the document is obsoleted
  exported: number;
  printed: number;
  last_copy_at: string;
}

export interface SupersededCopies {
  total: { versions: number; copies: number }; // FULL-set totals, not the page
  items: SupersededCopyRow[];
}
```

- [ ] **Step 2: Update the fixtures in `test/msw/handlers.ts`**

Both `docFixture` entries + `createdDocFixture` gain the four review fields (the serializer always emits them):

```ts
// docFixture[0] (SOP-PUR-014, Effective) — after created_at:
    review_period_months: 24,
    next_review_due: "2027-03-14",
    last_reviewed_at: null,
    review_state: "current",
// docFixture[1] (SOP-PRD-007, Draft) and createdDocFixture — after created_at:
    review_period_months: null,
    next_review_due: null,
    last_reviewed_at: null,
    review_state: null,
```

Replace `complianceFixture` (the real serializer has carried these since S-drift-1; the COVERED row being overdue proves orthogonality):

```ts
export const complianceFixture = {
  framework: "iso9001:2015",
  rollup: { total: 3, covered: 1, partial: 1, gap: 1, overdue_review: 1 },
  rows: [
    { clause_id: "c43", number: "4.3", title: "Scope of the QMS", pdca_phase: "PLAN", mapped_count: 1, effective_count: 1, status: "COVERED", overdue_review: true },
    { clause_id: "c62", number: "6.2", title: "Quality objectives", pdca_phase: "PLAN", mapped_count: 1, effective_count: 0, status: "PARTIAL", overdue_review: false },
    { clause_id: "c84", number: "8.4", title: "External providers", pdca_phase: "DO", mapped_count: 0, effective_count: 0, status: "GAP", overdue_review: false },
  ],
};
```

Add the drift fixtures (new section `// ---- S-web-8 drift fixtures (pinned to drift_report.py + the openapi getDriftStatus example) ----`). Import the types at the top of handlers.ts: add `DriftStatus, SupersededCopies` to the existing `import type` from `"../../lib/types"`:

```ts
export const driftStatusFixture = {
  scans: {
    MIRROR: {
      status: "CLEAN",
      started_at: "2026-06-10T03:00:00+00:00",
      finished_at: "2026-06-10T03:00:04+00:00",
      counts: { scanned: 41, ok: 41, stale: 0, tampered: 0, rebuild_triggered: false },
      triggered_by: "beat",
    },
    BLOB_REHASH: {
      status: "DIVERGENT",
      started_at: "2026-06-10T04:00:00+00:00",
      finished_at: "2026-06-10T04:01:10+00:00",
      counts: { scanned: 500, ok: 498, mismatched: 1, missing: 1, read_errors: 0, stamped: 498, full: false, sample_size: 500, total_blobs: 1240 },
      triggered_by: "beat",
    },
  },
  blob_coverage: { total: 1240, never_verified: 612, failing: 2, oldest_verified_at: "2026-06-01T04:00:00+00:00" },
  superseded_copies: { versions: 2, copies: 5 },
} satisfies DriftStatus;

export const supersededCopiesFixture = {
  total: { versions: 2, copies: 5 },
  items: [
    { document_id: "11111111-1111-1111-1111-111111111111", identifier: "SOP-PUR-014", version_id: "eeee1111-1111-1111-1111-111111111111", revision_label: "Rev A", version_state: "Superseded", current_revision_label: "Rev B", exported: 2, printed: 1, last_copy_at: "2026-05-30T14:22:00+00:00" },
    { document_id: "99999999-9999-9999-9999-999999999999", identifier: "SOP-OBS-001", version_id: "ffff1111-1111-1111-1111-111111111111", revision_label: "Rev C", version_state: "Obsolete", current_revision_label: null, exported: 0, printed: 2, last_copy_at: "2026-05-12T08:00:00+00:00" },
  ],
} satisfies SupersededCopies;
```

Add the periodic-review task fixture (after `capaApprovalTask`) — pinned to the sweep-minted row (mig 0045 definition: `stage_key="review"`, `action_expected="periodic_review"`; pool = the owner's `app_user.id` = TEST_AUTH's `bbbb1111…`; `subject_id` = SOP-PUR-014):

```ts
// A PERIODIC_REVIEW task detail (GET /tasks/{id}) — S-web-8 routes it via ReviewApprovePage's
// periodic branch. due_at = org-midnight of next_review_due (the sweep's anchor).
export const periodicReviewTask = {
  id: "tkpr1111-1111-1111-1111-111111111111",
  instance_id: "wfpr1111-1111-1111-1111-111111111111",
  stage_key: "review",
  type: "PERIODIC_REVIEW",
  state: "PENDING",
  assignee_user_id: null,
  candidate_pool: ["bbbb1111-1111-1111-1111-111111111111"],
  action_expected: "periodic_review",
  due_at: "2026-06-10T00:00:00-05:00",
  subject_type: "PERIODIC_REVIEW",
  subject_id: "11111111-1111-1111-1111-111111111111",
};
```

- [ ] **Step 3: Add the MSW default handlers**

In the `handlers` array, add a drift section (before the S-web-6 section). The superseded handler honors `limit`/`offset` so pagination tests are real:

```ts
  // ---- S-web-8 drift surface (default happy-path; per-test overrides for 403/null-scans) ----
  http.get("/api/v1/admin/drift/status", () => HttpResponse.json(driftStatusFixture)),
  http.get("/api/v1/admin/drift/superseded-copies", ({ request }) => {
    const sp = new URL(request.url).searchParams;
    const limit = Number(sp.get("limit") ?? "50");
    const offset = Number(sp.get("offset") ?? "0");
    return HttpResponse.json({
      total: supersededCopiesFixture.total,
      items: supersededCopiesFixture.items.slice(offset, offset + limit),
    });
  }),
```

Change the `GET /api/v1/tasks/:id` default to branch on the periodic fixture id (every other id keeps returning `approveTask` — existing tests unchanged):

```ts
  http.get("/api/v1/tasks/:id", ({ params }) =>
    HttpResponse.json(params.id === periodicReviewTask.id ? periodicReviewTask : approveTask),
  ),
```

Add the metadata-PATCH default (none exists today). It pins the honest response shape: `effective_from` is null on PATCH responses (read-paths only), which is WHY the UI must invalidate instead of consuming the body:

```ts
  http.patch("/api/v1/documents/:id", async ({ params, request }) => {
    const body = (await request.json()) as { review_period_months?: number | null };
    const doc = docFixture.find((d) => d.id === params.id) ?? docFixture[0]!;
    const months = body.review_period_months ?? null;
    return HttpResponse.json({
      ...doc,
      effective_from: null,
      review_period_months: months,
      next_review_due: months === null ? null : "2028-03-14",
      review_state: months === null ? null : "current",
    });
  }),
```

- [ ] **Step 4: Verify the suite still compiles and passes**

Run: `npx tsc --noEmit` then `npx vitest run --pool=forks --poolOptions.forks.singleFork=true`
Expected: tsc clean (fix any test that inlines a `DocumentSummary` literal by adding the four fields); all 499 tests pass. (`CompliancePage`/doc-detail tests read fixtures, so the additive fields don't break them.)

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/lib/types.ts apps/web/src/test/msw/handlers.ts
git commit -m "feat(s-web-8): types + serializer-pinned fixtures for drift status, review fields, checklist overdue"
```

---

### Task 2: `ReviewStateBadge` + `daysUntil`

**Files:**
- Create: `apps/web/src/features/document/ReviewStateBadge.tsx`
- Create: `apps/web/src/features/document/reviewDates.ts`
- Create: `apps/web/src/features/document/ReviewStateBadge.test.tsx`
- Create: `apps/web/src/features/document/reviewDates.test.ts`

- [ ] **Step 1: Write the failing tests**

`ReviewStateBadge.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { describe, expect, test } from "vitest";
import { theme } from "../../theme";
import { ReviewStateBadge } from "./ReviewStateBadge";

function renderBadge(state: "current" | "due_soon" | "overdue" | null) {
  return render(
    <MantineProvider theme={theme}>
      <ReviewStateBadge state={state} />
    </MantineProvider>,
  );
}

describe("ReviewStateBadge", () => {
  test("renders the three states with their labels", () => {
    renderBadge("current");
    expect(screen.getByText("Current")).toBeInTheDocument();
  });
  test("due_soon renders Due soon", () => {
    renderBadge("due_soon");
    expect(screen.getByText("Due soon")).toBeInTheDocument();
  });
  test("overdue renders Overdue", () => {
    renderBadge("overdue");
    expect(screen.getByText("Overdue")).toBeInTheDocument();
  });
  test("null (not scheduled) renders nothing", () => {
    const { container } = renderBadge(null);
    expect(container).toBeEmptyDOMElement();
  });
});
```

`reviewDates.test.ts`:

```ts
import { describe, expect, test } from "vitest";
import { daysUntil } from "./reviewDates";

describe("daysUntil", () => {
  const now = new Date(2026, 5, 10, 15, 30); // 2026-06-10 local
  test("future date counts whole days", () => {
    expect(daysUntil("2026-06-15", now)).toBe(5);
  });
  test("today is 0", () => {
    expect(daysUntil("2026-06-10", now)).toBe(0);
  });
  test("past date is negative", () => {
    expect(daysUntil("2026-06-07", now)).toBe(-3);
  });
  test("crosses month/year boundaries", () => {
    expect(daysUntil("2027-01-01", new Date(2026, 11, 31))).toBe(1);
  });
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `npx vitest run src/features/document/ReviewStateBadge.test.tsx src/features/document/reviewDates.test.ts`
Expected: FAIL — modules not found.

- [ ] **Step 3: Implement**

`ReviewStateBadge.tsx`:

```tsx
import { Badge } from "@mantine/core";
import type { ReviewState } from "../../lib/types";

const META: Record<ReviewState, { color: string; label: string }> = {
  current: { color: "green", label: "Current" },
  due_soon: { color: "yellow", label: "Due soon" },
  overdue: { color: "red", label: "Overdue" },
};

// S-web-8: the derived review-currency badge. review_state is SERVER-computed (org tz, 30-day
// due_soon window) — null means "no scheduled review" and renders nothing.
export function ReviewStateBadge({ state }: { state: ReviewState | null }) {
  if (state === null) return null;
  const m = META[state];
  return (
    <Badge color={m.color} variant="light">
      {m.label}
    </Badge>
  );
}
```

`reviewDates.ts`:

```ts
// Whole-day distance to a DATE-only next_review_due, in the BROWSER's timezone — display sugar for
// the "Days to review" tile. The server's review_state is the org-tz-authoritative signal; the two
// can differ by ±1 day across timezones, which is why the badge always accompanies the number.
export function daysUntil(dateIso: string, now: Date = new Date()): number {
  const [y, m, d] = dateIso.split("-").map(Number);
  const due = new Date(y ?? 0, (m ?? 1) - 1, d ?? 1).getTime();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  return Math.round((due - today) / 86_400_000);
}
```

- [ ] **Step 4: Run to verify they pass**

Run: `npx vitest run src/features/document/ReviewStateBadge.test.tsx src/features/document/reviewDates.test.ts`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/document/ReviewStateBadge.tsx apps/web/src/features/document/reviewDates.ts apps/web/src/features/document/ReviewStateBadge.test.tsx apps/web/src/features/document/reviewDates.test.ts
git commit -m "feat(s-web-8): ReviewStateBadge + daysUntil helper"
```

---

### Task 3: Drift hooks

**Files:**
- Create: `apps/web/src/features/drift/hooks.ts`
- Create: `apps/web/src/features/drift/hooks.test.tsx`

- [ ] **Step 1: Write the failing tests**

`hooks.test.tsx` (follow `features/review/hooks.test.tsx` for the renderHook wrapper shape — QueryClientProvider + AuthContext with TEST_AUTH):

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { describe, expect, test } from "vitest";
import { AuthContext, TEST_AUTH } from "../../lib/auth";
import { server } from "../../test/msw/server";
import { driftStatusFixture } from "../../test/msw/handlers";
import { SUPERSEDED_PAGE_SIZE, useDriftStatus, useSupersededCopies } from "./hooks";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <AuthContext.Provider value={TEST_AUTH}>{children}</AuthContext.Provider>
    </QueryClientProvider>
  );
}

describe("drift hooks", () => {
  test("useDriftStatus returns the status snapshot", async () => {
    const { result } = renderHook(() => useDriftStatus(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(driftStatusFixture);
    expect(result.current.forbidden).toBe(false);
  });

  test("useDriftStatus flags a 403 as forbidden", async () => {
    server.use(
      http.get("/api/v1/admin/drift/status", () =>
        HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
      ),
    );
    const { result } = renderHook(() => useDriftStatus(), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.forbidden).toBe(true);
  });

  test("useSupersededCopies sends limit + offset to the server", async () => {
    let seen: string | null = null;
    server.use(
      http.get("/api/v1/admin/drift/superseded-copies", ({ request }) => {
        seen = new URL(request.url).search;
        return HttpResponse.json({ total: { versions: 0, copies: 0 }, items: [] });
      }),
    );
    const { result } = renderHook(() => useSupersededCopies(SUPERSEDED_PAGE_SIZE), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(seen).toBe(`?limit=${SUPERSEDED_PAGE_SIZE}&offset=${SUPERSEDED_PAGE_SIZE}`);
  });
});
```

(If `TEST_AUTH` is not exported from `lib/auth`, copy the auth-value construction used by `features/review/hooks.test.tsx` verbatim instead.)

- [ ] **Step 2: Run to verify they fail**

Run: `npx vitest run src/features/drift/hooks.test.tsx`
Expected: FAIL — `./hooks` not found.

- [ ] **Step 3: Implement `hooks.ts`**

```ts
import { useQuery } from "@tanstack/react-query";
import { ApiError, useApi } from "../../lib/api";
import type { DriftStatus, SupersededCopies } from "../../lib/types";

// S-web-8: the two drift.read-gated admin reads (S-drift-3, R41). retry:false + the forbidden
// flag — the compliance/audits calm-403 pattern. Pure reads; no scan is ever triggered.

export const SUPERSEDED_PAGE_SIZE = 50;

export function useDriftStatus() {
  const api = useApi();
  const query = useQuery({
    queryKey: ["drift-status"],
    queryFn: () => api.get<DriftStatus>("/api/v1/admin/drift/status"),
    retry: false,
  });
  const forbidden = query.error instanceof ApiError && query.error.status === 403;
  return { ...query, forbidden };
}

export function useSupersededCopies(offset: number) {
  const api = useApi();
  const query = useQuery({
    queryKey: ["drift-superseded", offset],
    queryFn: () =>
      api.get<SupersededCopies>(
        `/api/v1/admin/drift/superseded-copies?limit=${SUPERSEDED_PAGE_SIZE}&offset=${offset}`,
      ),
    retry: false,
  });
  const forbidden = query.error instanceof ApiError && query.error.status === 403;
  return { ...query, forbidden };
}
```

- [ ] **Step 4: Run to verify they pass**

Run: `npx vitest run src/features/drift/hooks.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/drift/hooks.ts apps/web/src/features/drift/hooks.test.tsx
git commit -m "feat(s-web-8): drift status + superseded-copies hooks"
```

---

### Task 4: `DriftStatusPage`

**Files:**
- Create: `apps/web/src/features/drift/DriftStatusPage.tsx`
- Create: `apps/web/src/features/drift/DriftStatusPage.test.tsx`

- [ ] **Step 1: Write the failing tests**

```tsx
import { screen, waitFor } from "@testing-library/react";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { describe, expect, test } from "vitest";
import { renderWithProviders } from "../../test/render";
import { driftStatusFixture } from "../../test/msw/handlers";
import { server } from "../../test/msw/server";
import { DriftStatusPage } from "./DriftStatusPage";

describe("DriftStatusPage", () => {
  test("renders both scan cards with status badges and the counts bag", async () => {
    renderWithProviders(<DriftStatusPage />);
    expect(await screen.findByText("Mirror scan")).toBeInTheDocument();
    expect(screen.getByText("Blob integrity")).toBeInTheDocument();
    expect(screen.getByText("CLEAN")).toBeInTheDocument();
    expect(screen.getByText("DIVERGENT")).toBeInTheDocument();
    // counts render generically — a MIRROR key and a BLOB_REHASH key both appear
    expect(screen.getByText("rebuild_triggered")).toBeInTheDocument();
    expect(screen.getByText("sample_size")).toBeInTheDocument();
  });

  test("treats counts as an OPEN bag — an unknown key still renders", async () => {
    server.use(
      http.get("/api/v1/admin/drift/status", () =>
        HttpResponse.json({
          ...driftStatusFixture,
          scans: {
            ...driftStatusFixture.scans,
            MIRROR: {
              ...driftStatusFixture.scans.MIRROR!,
              counts: { ...driftStatusFixture.scans.MIRROR!.counts, brand_new_key: 7 },
            },
          },
        }),
      ),
    );
    renderWithProviders(<DriftStatusPage />);
    expect(await screen.findByText("brand_new_key")).toBeInTheDocument();
    expect(screen.getByText("7")).toBeInTheDocument();
  });

  test("a never-run kind renders an honest empty card, not a crash", async () => {
    server.use(
      http.get("/api/v1/admin/drift/status", () =>
        HttpResponse.json({ ...driftStatusFixture, scans: { MIRROR: null, BLOB_REHASH: null } }),
      ),
    );
    renderWithProviders(<DriftStatusPage />);
    expect(await screen.findAllByText("Never run yet.")).toHaveLength(2);
  });

  test("failing > 0 surfaces the unresolved-findings alarm", async () => {
    renderWithProviders(<DriftStatusPage />);
    expect(
      await screen.findByText(/2 unresolved integrity findings — re-alarming until restored/),
    ).toBeInTheDocument();
  });

  test("failing = 0 shows no alarm", async () => {
    server.use(
      http.get("/api/v1/admin/drift/status", () =>
        HttpResponse.json({
          ...driftStatusFixture,
          blob_coverage: { ...driftStatusFixture.blob_coverage, failing: 0 },
        }),
      ),
    );
    renderWithProviders(<DriftStatusPage />);
    await screen.findByText("Mirror scan");
    expect(screen.queryByText(/unresolved integrity findings/)).not.toBeInTheDocument();
  });

  test("D4 headline links to the superseded-copies tab", async () => {
    renderWithProviders(<DriftStatusPage />);
    const link = await screen.findByRole("link", { name: /view the report/i });
    expect(link).toHaveAttribute("href", "/drift/superseded-copies");
  });

  test("403 renders the calm no-access panel", async () => {
    server.use(
      http.get("/api/v1/admin/drift/status", () =>
        HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
      ),
    );
    renderWithProviders(<DriftStatusPage />);
    expect(await screen.findByText("No access")).toBeInTheDocument();
  });

  test("has no axe violations", async () => {
    const { container } = renderWithProviders(<DriftStatusPage />);
    await screen.findByText("Mirror scan");
    expect(await axe(container)).toHaveNoViolations();
  });
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `npx vitest run src/features/drift/DriftStatusPage.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `DriftStatusPage.tsx`**

```tsx
import {
  Alert,
  Anchor,
  Badge,
  Card,
  Container,
  Group,
  Loader,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title,
} from "@mantine/core";
import { Link } from "react-router-dom";
import type { DriftScanSummary } from "../../lib/types";
import { useDriftStatus } from "./hooks";

// S-web-8: the admin drift-status read (S-drift-3 / doc 05 §9.1). Pure read — no scan trigger.

const STATUS_COLOR: Record<DriftScanSummary["status"], string> = {
  CLEAN: "green",
  DIVERGENT: "red",
  FAILED: "orange",
};

function fmt(iso: string): string {
  return iso.slice(0, 16).replace("T", " ");
}

// counts is an OPEN bag (S-drift-3 §10a): render every key generically, sorted — unknown keys are
// additive by contract, so the UI must never destructure a closed set.
function ScanCard({ title, scan }: { title: string; scan: DriftScanSummary | null }) {
  if (!scan) {
    return (
      <Card withBorder>
        <Text fw={600}>{title}</Text>
        <Text size="sm" c="dimmed">
          Never run yet.
        </Text>
      </Card>
    );
  }
  return (
    <Card withBorder>
      <Stack gap="xs">
        <Group justify="space-between">
          <Text fw={600}>{title}</Text>
          <Badge color={STATUS_COLOR[scan.status]} variant="light">
            {scan.status}
          </Badge>
        </Group>
        <Text size="xs" c="dimmed">
          Started {fmt(scan.started_at)} ·{" "}
          {scan.finished_at ? `finished ${fmt(scan.finished_at)}` : "not finished"} · by{" "}
          {scan.triggered_by}
        </Text>
        <Table withRowBorders={false} verticalSpacing={2}>
          <Table.Tbody>
            {Object.entries(scan.counts)
              .sort(([a], [b]) => a.localeCompare(b))
              .map(([k, v]) => (
                <Table.Tr key={k}>
                  <Table.Td>
                    <Text size="xs" c="dimmed" ff="monospace">
                      {k}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size="xs">{String(v)}</Text>
                  </Table.Td>
                </Table.Tr>
              ))}
          </Table.Tbody>
        </Table>
      </Stack>
    </Card>
  );
}

export function DriftStatusPage() {
  const { data, isLoading, isError, forbidden } = useDriftStatus();

  if (forbidden) {
    return (
      <Container size="lg" py="md">
        <Title order={2} mb="md">
          Drift status
        </Title>
        <Alert color="gray" title="No access">
          You don&rsquo;t have access to the drift status surface. It requires the drift.read
          permission (System Administrator).
        </Alert>
      </Container>
    );
  }
  if (isLoading) {
    return (
      <Container size="lg" py="md">
        <Loader aria-label="Loading drift status" />
      </Container>
    );
  }
  if (isError || !data) {
    return (
      <Container size="lg" py="md">
        <Alert color="red" title="Couldn't load the drift status">
          Please try again.
        </Alert>
      </Container>
    );
  }

  const cov = data.blob_coverage;
  const d4 = data.superseded_copies;
  return (
    <Container size="lg" py="md">
      <Stack gap="lg">
        <div>
          <Title order={2}>Drift status</Title>
          <Text c="dimmed" size="sm">
            The vault&rsquo;s integrity scanners (D1–D4). The vault is the source of truth — these
            are detection reads, not corrections.
          </Text>
        </div>
        <SimpleGrid cols={{ base: 1, md: 2 }}>
          <ScanCard title="Mirror scan" scan={data.scans.MIRROR} />
          <ScanCard title="Blob integrity" scan={data.scans.BLOB_REHASH} />
        </SimpleGrid>
        <Card withBorder>
          <Stack gap="xs">
            <Text fw={600}>Blob verification coverage</Text>
            {cov.failing > 0 && (
              <Alert color="red" title="Integrity findings open">
                {cov.failing} unresolved integrity findings — re-alarming until restored. See the
                runbook (restore from backup, then re-run the verify).
              </Alert>
            )}
            <Group gap="lg">
              <Text size="sm">Total blobs: {cov.total}</Text>
              <Text size="sm">Never verified: {cov.never_verified}</Text>
              <Text size="sm">Failing: {cov.failing}</Text>
              <Text size="sm" c="dimmed">
                Oldest stamp: {cov.oldest_verified_at ? fmt(cov.oldest_verified_at) : "—"}
              </Text>
            </Group>
          </Stack>
        </Card>
        <Card withBorder>
          <Stack gap="xs">
            <Text fw={600}>Outstanding copies of superseded versions</Text>
            <Text size="sm">
              {d4.versions} versions · {d4.copies} exported/printed copies still in circulation.
            </Text>
            <Anchor component={Link} to="/drift/superseded-copies" size="sm">
              View the report →
            </Anchor>
          </Stack>
        </Card>
      </Stack>
    </Container>
  );
}
```

- [ ] **Step 4: Run to verify they pass**

Run: `npx vitest run src/features/drift/DriftStatusPage.test.tsx`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/drift/DriftStatusPage.tsx apps/web/src/features/drift/DriftStatusPage.test.tsx
git commit -m "feat(s-web-8): drift status page — scan cards, blob coverage alarm, D4 headline"
```

---

### Task 5: `SupersededCopiesPage`

**Files:**
- Create: `apps/web/src/features/drift/SupersededCopiesPage.tsx`
- Create: `apps/web/src/features/drift/SupersededCopiesPage.test.tsx`

- [ ] **Step 1: Write the failing tests**

```tsx
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, test } from "vitest";
import { renderWithProviders } from "../../test/render";
import { supersededCopiesFixture } from "../../test/msw/handlers";
import { server } from "../../test/msw/server";
import { SupersededCopiesPage } from "./SupersededCopiesPage";

describe("SupersededCopiesPage", () => {
  test("renders the totals headline and one row per version", async () => {
    renderWithProviders(<SupersededCopiesPage />);
    expect(await screen.findByText(/2 versions · 5 copies/)).toBeInTheDocument();
    expect(screen.getByText("SOP-PUR-014")).toBeInTheDocument();
    expect(screen.getByText("SOP-OBS-001")).toBeInTheDocument();
    // the obsoleted document has no current revision
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  test("identifier links to the document page", async () => {
    renderWithProviders(<SupersededCopiesPage />);
    const link = await screen.findByRole("link", { name: "SOP-PUR-014" });
    expect(link).toHaveAttribute("href", "/documents/11111111-1111-1111-1111-111111111111");
  });

  test("pagination drives the server offset", async () => {
    const offsets: string[] = [];
    server.use(
      http.get("/api/v1/admin/drift/superseded-copies", ({ request }) => {
        const sp = new URL(request.url).searchParams;
        offsets.push(sp.get("offset") ?? "?");
        return HttpResponse.json({
          total: { versions: 120, copies: 300 },
          items: supersededCopiesFixture.items,
        });
      }),
    );
    renderWithProviders(<SupersededCopiesPage />);
    await screen.findByText("SOP-PUR-014");
    await userEvent.click(screen.getByRole("button", { name: "2" })); // page 2 of 3 (120/50)
    await waitFor(() => expect(offsets).toContain("50"));
  });

  test("empty set renders the calm empty state", async () => {
    server.use(
      http.get("/api/v1/admin/drift/superseded-copies", () =>
        HttpResponse.json({ total: { versions: 0, copies: 0 }, items: [] }),
      ),
    );
    renderWithProviders(<SupersededCopiesPage />);
    expect(
      await screen.findByText("No outstanding copies of superseded versions."),
    ).toBeInTheDocument();
  });

  test("403 renders the calm no-access panel", async () => {
    server.use(
      http.get("/api/v1/admin/drift/superseded-copies", () =>
        HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
      ),
    );
    renderWithProviders(<SupersededCopiesPage />);
    expect(await screen.findByText("No access")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `npx vitest run src/features/drift/SupersededCopiesPage.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `SupersededCopiesPage.tsx`**

```tsx
import {
  Alert,
  Anchor,
  Container,
  Group,
  Loader,
  Pagination,
  Stack,
  Table,
  Text,
  Title,
} from "@mantine/core";
import { useState } from "react";
import { Link } from "react-router-dom";
import { SUPERSEDED_PAGE_SIZE, useSupersededCopies } from "./hooks";

// S-web-8: the D4 recall list (doc 05 §9.1 / R11) — outstanding EXPORTED/PRINTED copies of
// now-superseded versions. No decrement leg exists (a paper copy can't be un-printed): the count is
// the honest upper bound; the /verify token is the per-copy resolution. Server-side pagination
// (offset/limit) — no virtualization (the S-ing-4b rule).
export function SupersededCopiesPage() {
  const [page, setPage] = useState(1);
  const { data, isLoading, isError, forbidden } = useSupersededCopies(
    (page - 1) * SUPERSEDED_PAGE_SIZE,
  );

  if (forbidden) {
    return (
      <Container size="lg" py="md">
        <Title order={2} mb="md">
          Superseded copies
        </Title>
        <Alert color="gray" title="No access">
          You don&rsquo;t have access to the drift status surface. It requires the drift.read
          permission (System Administrator).
        </Alert>
      </Container>
    );
  }
  if (isLoading) {
    return (
      <Container size="lg" py="md">
        <Loader aria-label="Loading superseded copies" />
      </Container>
    );
  }
  if (isError || !data) {
    return (
      <Container size="lg" py="md">
        <Alert color="red" title="Couldn't load the report">
          Please try again.
        </Alert>
      </Container>
    );
  }

  const pages = Math.max(1, Math.ceil(data.total.versions / SUPERSEDED_PAGE_SIZE));
  return (
    <Container size="lg" py="md">
      <Stack gap="md">
        <div>
          <Title order={2}>Superseded copies</Title>
          <Text c="dimmed" size="sm">
            Exported/printed copies of versions that have since been superseded or obsoleted —{" "}
            {data.total.versions} versions · {data.total.copies} copies outstanding. Use this as the
            recall list; each paper copy resolves via its verify QR.
          </Text>
        </div>
        {data.items.length === 0 ? (
          <Text c="dimmed">No outstanding copies of superseded versions.</Text>
        ) : (
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Identifier</Table.Th>
                <Table.Th>Copied revision</Table.Th>
                <Table.Th>State</Table.Th>
                <Table.Th>Current revision</Table.Th>
                <Table.Th>Exported</Table.Th>
                <Table.Th>Printed</Table.Th>
                <Table.Th>Last copy</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {data.items.map((r) => (
                <Table.Tr key={r.version_id}>
                  <Table.Td>
                    <Anchor component={Link} to={`/documents/${r.document_id}`} ff="monospace" size="sm">
                      {r.identifier}
                    </Anchor>
                  </Table.Td>
                  <Table.Td>{r.revision_label}</Table.Td>
                  <Table.Td>{r.version_state}</Table.Td>
                  <Table.Td>{r.current_revision_label ?? "—"}</Table.Td>
                  <Table.Td>{r.exported}</Table.Td>
                  <Table.Td>{r.printed}</Table.Td>
                  <Table.Td>{r.last_copy_at.slice(0, 16).replace("T", " ")}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
        {pages > 1 && (
          <Group justify="center">
            <Pagination value={page} onChange={setPage} total={pages} />
          </Group>
        )}
      </Stack>
    </Container>
  );
}
```

- [ ] **Step 4: Run to verify they pass**

Run: `npx vitest run src/features/drift/SupersededCopiesPage.test.tsx`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/drift/SupersededCopiesPage.tsx apps/web/src/features/drift/SupersededCopiesPage.test.tsx
git commit -m "feat(s-web-8): D4 superseded-copies report page with server pagination"
```

---

### Task 6: `DriftLayout` + routes + LeftRail entry

**Files:**
- Create: `apps/web/src/features/drift/DriftLayout.tsx`
- Create: `apps/web/src/features/drift/DriftLayout.test.tsx`
- Modify: `apps/web/src/App.tsx`
- Modify: `apps/web/src/app/shell/LeftRail.tsx`

- [ ] **Step 1: Write the failing tests**

`DriftLayout.test.tsx` (mirror the CapaLayout test shape if one exists — tab strip + outlet routing; plus the LeftRail gating cases here since they ship together):

```tsx
import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { Route, Routes } from "react-router-dom";
import { describe, expect, test } from "vitest";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { LeftRail } from "../../app/shell/LeftRail";
import { DriftLayout } from "./DriftLayout";

function renderAt(route: string) {
  return renderWithProviders(
    <Routes>
      <Route path="/drift" element={<DriftLayout />}>
        <Route index element={<div>STATUS-FACE</div>} />
        <Route path="superseded-copies" element={<div>D4-FACE</div>} />
      </Route>
    </Routes>,
    { route },
  );
}

describe("DriftLayout", () => {
  test("index route shows the Status tab content", () => {
    renderAt("/drift");
    expect(screen.getByText("STATUS-FACE")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Status" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Superseded copies" })).toBeInTheDocument();
  });
  test("superseded-copies route shows the D4 tab content", () => {
    renderAt("/drift/superseded-copies");
    expect(screen.getByText("D4-FACE")).toBeInTheDocument();
  });
});

describe("LeftRail drift gating", () => {
  test("no drift.read → no Drift entry", async () => {
    renderWithProviders(<LeftRail />);
    expect(await screen.findByText("Library")).toBeInTheDocument();
    expect(screen.queryByText("Drift")).not.toBeInTheDocument();
  });
  test("drift.read → the Drift entry renders", async () => {
    server.use(
      http.get("/api/v1/me/permissions", () =>
        HttpResponse.json({
          scope: { level: "SYSTEM", selector: null },
          permissions: [{ key: "drift.read", effect: "ALLOW" }],
        }),
      ),
    );
    renderWithProviders(<LeftRail />);
    expect(await screen.findByText("Drift")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `npx vitest run src/features/drift/DriftLayout.test.tsx`
Expected: FAIL — `./DriftLayout` not found.

- [ ] **Step 3: Implement**

`DriftLayout.tsx` (the CapaLayout/AuditsLayout tabbed sub-route precedent):

```tsx
import { Container, Tabs } from "@mantine/core";
import { Outlet, useLocation, useNavigate } from "react-router-dom";

// S-web-8: the drift surface's secondary nav. Both faces are drift.read-gated server-side; the
// layout itself renders for anyone (each page shows its own calm-403).
const TABS = [
  { value: "status", label: "Status", path: "/drift" },
  { value: "superseded", label: "Superseded copies", path: "/drift/superseded-copies" },
] as const;

function activeTab(pathname: string): string {
  return pathname.startsWith("/drift/superseded-copies") ? "superseded" : "status";
}

export function DriftLayout() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  return (
    <>
      <Container size="lg" pt="md" pb={0}>
        <Tabs
          value={activeTab(pathname)}
          onChange={(v) => {
            const tab = TABS.find((t) => t.value === v);
            if (tab) navigate(tab.path);
          }}
        >
          <Tabs.List>
            {TABS.map((t) => (
              <Tabs.Tab key={t.value} value={t.value}>
                {t.label}
              </Tabs.Tab>
            ))}
          </Tabs.List>
        </Tabs>
      </Container>
      <Outlet />
    </>
  );
}
```

`App.tsx` — add imports and routes (after the `ingestion` routes, inside the `AppShell` branch):

```tsx
import { DriftLayout } from "./features/drift/DriftLayout";
import { DriftStatusPage } from "./features/drift/DriftStatusPage";
import { SupersededCopiesPage } from "./features/drift/SupersededCopiesPage";
// …
        <Route path="drift" element={<DriftLayout />}>
          <Route index element={<DriftStatusPage />} />
          <Route path="superseded-copies" element={<SupersededCopiesPage />} />
        </Route>
```

`LeftRail.tsx` — after the `import.review` block:

```tsx
      {can("drift.read") && (
        // S-web-8: gated — drift.read is the admin-side SYSTEM key (R41); System Administrator
        // holds it natively (seeded 0047).
        <NavLink
          component={Link}
          to="/drift"
          label="Drift"
          active={pathname.startsWith("/drift")}
        />
      )}
```

- [ ] **Step 4: Run to verify they pass**

Run: `npx vitest run src/features/drift/DriftLayout.test.tsx`
Expected: PASS (4 tests). Also run `npx vitest run src/app` (any existing LeftRail/shell tests must stay green — the default permissions fixture is empty, so no existing snapshot gains a Drift entry).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/drift/DriftLayout.tsx apps/web/src/features/drift/DriftLayout.test.tsx apps/web/src/App.tsx apps/web/src/app/shell/LeftRail.tsx
git commit -m "feat(s-web-8): /drift routes, tab layout, gated LeftRail entry"
```

---

### Task 7: `ControlMetadata` review rows

**Files:**
- Modify: `apps/web/src/features/document/ControlMetadata.tsx`
- Modify (or create if absent): `apps/web/src/features/document/ControlMetadata.test.tsx`

- [ ] **Step 1: Write the failing tests** (append to the existing test file if one exists; otherwise create with this wrapper)

```tsx
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";
import { renderWithProviders } from "../../test/render";
import { docFixture } from "../../test/msw/handlers";
import type { DocumentSummary } from "../../lib/types";
import { ControlMetadata } from "./ControlMetadata";

const doc = docFixture[0] as unknown as DocumentSummary;

describe("ControlMetadata review rows", () => {
  test("renders period, next review with badge, last reviewed", () => {
    renderWithProviders(<ControlMetadata doc={doc} />);
    expect(screen.getByText("Review period")).toBeInTheDocument();
    expect(screen.getByText("24 months")).toBeInTheDocument();
    expect(screen.getByText("Next review")).toBeInTheDocument();
    expect(screen.getByText("2027-03-14")).toBeInTheDocument();
    expect(screen.getByText("Current")).toBeInTheDocument(); // the ReviewStateBadge
    expect(screen.getByText("Last reviewed")).toBeInTheDocument();
  });

  test("unscheduled doc renders em-dashes and no badge", () => {
    renderWithProviders(
      <ControlMetadata
        doc={{ ...doc, review_period_months: null, next_review_due: null, last_reviewed_at: null, review_state: null }}
      />,
    );
    expect(screen.getByText("Review period")).toBeInTheDocument();
    expect(screen.queryByText("Current")).not.toBeInTheDocument();
  });

  test("the Edit affordance renders ONLY when onEditReviewPeriod is passed, and fires it", async () => {
    const { rerender } = renderWithProviders(<ControlMetadata doc={doc} />);
    expect(screen.queryByRole("button", { name: "Edit review period" })).not.toBeInTheDocument();
    const onEdit = vi.fn();
    rerender(<ControlMetadata doc={doc} onEditReviewPeriod={onEdit} />);
    await userEvent.click(screen.getByRole("button", { name: "Edit review period" }));
    expect(onEdit).toHaveBeenCalledOnce();
  });
});
```

(If `renderWithProviders` doesn't return `rerender` with the wrapper applied, render twice instead — once without and once with the prop.)

- [ ] **Step 2: Run to verify the new cases fail**

Run: `npx vitest run src/features/document/ControlMetadata.test.tsx`
Expected: FAIL — "Review period" not found.

- [ ] **Step 3: Implement** — in `ControlMetadata.tsx`, add imports and three rows after the `Effective` row:

```tsx
import { Anchor, Group, Table, Text } from "@mantine/core";
import { ReviewStateBadge } from "./ReviewStateBadge";
// …after the Effective row:
        <Row
          label="Review period"
          value={
            <Group gap="xs">
              <Text size="sm">
                {doc.review_period_months !== null ? `${doc.review_period_months} months` : "—"}
              </Text>
              {onEditReviewPeriod && (
                <Anchor component="button" type="button" size="sm" aria-label="Edit review period" onClick={onEditReviewPeriod}>
                  Edit
                </Anchor>
              )}
            </Group>
          }
        />
        <Row
          label="Next review"
          value={
            doc.next_review_due ? (
              <Group gap="xs">
                <Text size="sm">{doc.next_review_due}</Text>
                <ReviewStateBadge state={doc.review_state} />
              </Group>
            ) : (
              "—"
            )
          }
        />
        <Row
          label="Last reviewed"
          value={doc.last_reviewed_at ? doc.last_reviewed_at.slice(0, 10) : "—"}
        />
```

And extend the props (the component stays presentational — the S-web-2 drawer passes nothing and just gains the read rows):

```tsx
export function ControlMetadata({
  doc,
  typeName,
  ownerName,
  onEditReviewPeriod,
}: {
  doc: DocumentSummary;
  typeName?: string;
  ownerName?: string;
  // S-web-8: the detail page passes this iff doc.capabilities.manage_metadata (capabilities are
  // detail-only, so the drawer never renders the affordance).
  onEditReviewPeriod?: () => void;
}) {
```

- [ ] **Step 4: Run to verify they pass**

Run: `npx vitest run src/features/document/ControlMetadata.test.tsx` — PASS. Then `npx vitest run src/features/document src/features/library` (drawer/overview tests must stay green).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/document/ControlMetadata.tsx apps/web/src/features/document/ControlMetadata.test.tsx
git commit -m "feat(s-web-8): review rows in the control-metadata card + gated edit affordance"
```

---

### Task 8: `ReviewPeriodModal` + the doc-detail tile

**Files:**
- Create: `apps/web/src/features/document/ReviewPeriodModal.tsx`
- Create: `apps/web/src/features/document/ReviewPeriodModal.test.tsx`
- Modify: `apps/web/src/features/document/DocumentDetailPage.tsx`
- Modify: `apps/web/src/features/document/DocumentDetailPage.test.tsx` (append cases)

- [ ] **Step 1: Write the failing tests**

`ReviewPeriodModal.test.tsx`:

```tsx
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, test } from "vitest";
import { renderWithProviders } from "../../test/render";
import { docFixture } from "../../test/msw/handlers";
import { server } from "../../test/msw/server";
import type { DocumentSummary } from "../../lib/types";
import { ReviewPeriodModal } from "./ReviewPeriodModal";

const doc = docFixture[0] as unknown as DocumentSummary;

function capturePatch() {
  const bodies: unknown[] = [];
  server.use(
    http.patch("/api/v1/documents/:id", async ({ request }) => {
      bodies.push(await request.json());
      return HttpResponse.json({ ...doc, effective_from: null });
    }),
  );
  return bodies;
}

describe("ReviewPeriodModal", () => {
  test("saves a changed period — the body carries the explicit number", async () => {
    const bodies = capturePatch();
    renderWithProviders(<ReviewPeriodModal doc={doc} opened onClose={() => {}} />);
    const input = screen.getByLabelText("Review period (months)");
    await userEvent.clear(input);
    await userEvent.type(input, "36");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(bodies).toEqual([{ review_period_months: 36 }]));
  });

  test("clearing sends an EXPLICIT null (an omitted key would inherit server-side)", async () => {
    const bodies = capturePatch();
    renderWithProviders(<ReviewPeriodModal doc={doc} opened onClose={() => {}} />);
    await userEvent.click(screen.getByLabelText("No scheduled review"));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(bodies).toEqual([{ review_period_months: null }]));
  });

  test("out-of-bounds input disables Save", async () => {
    renderWithProviders(<ReviewPeriodModal doc={doc} opened onClose={() => {}} />);
    const input = screen.getByLabelText("Review period (months)");
    await userEvent.clear(input);
    await userEvent.type(input, "121");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  test("a PATCH failure surfaces an error and keeps the modal open", async () => {
    server.use(
      http.patch("/api/v1/documents/:id", () =>
        HttpResponse.json({ code: "validation_error", title: "Invalid" }, { status: 422 }),
      ),
    );
    let closed = false;
    renderWithProviders(<ReviewPeriodModal doc={doc} opened onClose={() => (closed = true)} />);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/Invalid/)).toBeInTheDocument();
    expect(closed).toBe(false);
  });
});
```

Append to `DocumentDetailPage.test.tsx`:

```tsx
import { detailCapabilities } from "../../test/msw/handlers";

describe("S-web-8 review surfaces", () => {
  test("renders the Next-review tile with days + badge", async () => {
    renderWithProviders(<DocumentDetailPage />, {
      route: "/documents/11111111-1111-1111-1111-111111111111",
    });
    // route-param render: wrap in the same Routes scaffolding the existing tests in this file use.
    expect(await screen.findByText("Next review")).toBeInTheDocument();
    expect(screen.getByText(/days/)).toBeInTheDocument();
    expect(screen.getAllByText("Current").length).toBeGreaterThan(0);
  });

  test("no manage_metadata → no edit affordance", async () => {
    renderWithProviders(<DocumentDetailPage />, {
      route: "/documents/11111111-1111-1111-1111-111111111111",
    });
    await screen.findByText("Next review");
    expect(screen.queryByRole("button", { name: "Edit review period" })).not.toBeInTheDocument();
  });

  test("manage_metadata → the modal opens, saves, and a REOPEN is pristine", async () => {
    server.use(
      http.get("/api/v1/documents/:id", () =>
        HttpResponse.json({
          ...docFixture[0],
          capabilities: { ...detailCapabilities, manage_metadata: true },
        }),
      ),
    );
    renderWithProviders(<DocumentDetailPage />, {
      route: "/documents/11111111-1111-1111-1111-111111111111",
    });
    await userEvent.click(await screen.findByRole("button", { name: "Edit review period" }));
    expect(await screen.findByLabelText("Review period (months)")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    // Reopen — conditional render means a fresh mount (the S-web-7d reopen trap)
    await userEvent.click(screen.getByRole("button", { name: "Edit review period" }));
    expect(await screen.findByLabelText("Review period (months)")).toHaveValue("24");
  });
});
```

(Adapt the render scaffolding — route param + `<Routes>` — to exactly match the existing cases in `DocumentDetailPage.test.tsx`; assert with the file's established helpers.)

- [ ] **Step 2: Run to verify they fail**

Run: `npx vitest run src/features/document/ReviewPeriodModal.test.tsx src/features/document/DocumentDetailPage.test.tsx`
Expected: new cases FAIL.

- [ ] **Step 3: Implement**

`ReviewPeriodModal.tsx`:

```tsx
import { Alert, Button, Group, Modal, NumberInput, Stack, Switch, Text } from "@mantine/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, useApi } from "../../lib/api";
import type { DocumentSummary } from "../../lib/types";

// S-web-8: edit the D5 review cadence (PATCH /documents/{id}, document.manage_metadata). Clearing
// sends an EXPLICIT null — the PATCH consumes model_fields_set, so an omitted key inherits. The
// response is NOT consumed (its effective_from is null on write paths) — invalidate + refetch.
// Parents must render this conditionally ({open && <ReviewPeriodModal …>}) so close unmounts it.
export function ReviewPeriodModal({
  doc,
  opened,
  onClose,
}: {
  doc: DocumentSummary;
  opened: boolean;
  onClose: () => void;
}) {
  const api = useApi();
  const qc = useQueryClient();
  const [months, setMonths] = useState<number | string>(doc.review_period_months ?? 24);
  const [clear, setClear] = useState(doc.review_period_months === null);
  const [error, setError] = useState<string | null>(null);

  const update = useMutation({
    mutationFn: (review_period_months: number | null) =>
      api.send<DocumentSummary>("PATCH", `/api/v1/documents/${doc.id}`, { review_period_months }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["document", doc.id] });
      onClose();
    },
    onError: (e) =>
      setError(e instanceof ApiError ? e.message : "Something went wrong. Please retry."),
  });

  const n = typeof months === "number" ? months : Number(months);
  const invalid = !clear && (!Number.isInteger(n) || n < 1 || n > 120);

  return (
    <Modal opened={opened} onClose={onClose} title={`Review period — ${doc.identifier}`}>
      <Stack gap="sm">
        {error && (
          <Alert color="red" withCloseButton onClose={() => setError(null)}>
            {error}
          </Alert>
        )}
        <Switch
          checked={clear}
          onChange={(e) => setClear(e.currentTarget.checked)}
          label="No scheduled review"
        />
        {!clear && (
          <NumberInput
            label="Review period (months)"
            min={1}
            max={120}
            value={months}
            onChange={setMonths}
            clampBehavior="none"
          />
        )}
        <Text size="xs" c="dimmed">
          The next review date is recomputed by the server — anchored on the later of the last
          review and the effective date.
        </Text>
        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={() => update.mutate(clear ? null : n)}
            loading={update.isPending}
            disabled={invalid}
          >
            Save
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
```

`DocumentDetailPage.tsx` — add imports + state + the tile + the wired metadata card:

```tsx
import { useState } from "react";
import { ReviewPeriodModal } from "./ReviewPeriodModal";
import { ReviewStateBadge } from "./ReviewStateBadge";
import { daysUntil } from "./reviewDates";
// inside the component:
  const [reviewEditOpen, setReviewEditOpen] = useState(false);
// after effectiveDate:
  const reviewDays = doc.next_review_due ? daysUntil(doc.next_review_due) : null;
// the SimpleGrid gains a 4th tile and goes 4-up:
      <SimpleGrid cols={{ base: 1, sm: 2, md: 4 }}>
        {/* …the three existing tiles unchanged… */}
        <Tile
          label="Next review"
          value={
            reviewDays === null
              ? "—"
              : reviewDays >= 0
                ? `${reviewDays} days`
                : `${-reviewDays} days overdue`
          }
          sub={
            doc.next_review_due ? (
              <>
                {doc.next_review_due} <ReviewStateBadge state={doc.review_state} />
              </>
            ) : (
              "No scheduled review"
            )
          }
        />
      </SimpleGrid>
// the ControlMetadata call gains the gated callback:
                <ControlMetadata
                  doc={doc}
                  typeName={typeName}
                  ownerName={ownerName}
                  onEditReviewPeriod={
                    doc.capabilities?.manage_metadata ? () => setReviewEditOpen(true) : undefined
                  }
                />
// before the closing </Stack> — conditionally rendered so close UNMOUNTS it (the S-web-7d trap):
      {reviewEditOpen && (
        <ReviewPeriodModal doc={doc} opened onClose={() => setReviewEditOpen(false)} />
      )}
```

Also update the loading Skeleton grid (`length: 3` → `length: 4`, `cols={{ base: 1, sm: 2, md: 4 }}`) so the skeleton matches.

- [ ] **Step 4: Run to verify they pass**

Run: `npx vitest run src/features/document`
Expected: all PASS (new + existing).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/document
git commit -m "feat(s-web-8): Next-review tile + review-period edit modal (manage_metadata-gated, explicit-null clear)"
```

---

### Task 9: `DecisionCard` PERIODIC_REVIEW variant + decide-hook extension

**Files:**
- Modify: `apps/web/src/features/review/DecisionCard.tsx`
- Modify: `apps/web/src/features/review/hooks.ts`
- Modify: `apps/web/src/features/review/DecisionCard.test.tsx` (append cases)

- [ ] **Step 1: Write the failing tests** (append; reuse the file's render helper)

```tsx
describe("DecisionCard — PERIODIC_REVIEW", () => {
  test("offers complete + changes_requested only", () => {
    renderCard({ subjectType: "PERIODIC_REVIEW" });
    expect(screen.getByLabelText("Confirm — no change needed")).toBeInTheDocument();
    expect(screen.getByLabelText("Changes needed — a revision is required")).toBeInTheDocument();
    expect(screen.queryByLabelText("Approve")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Reject")).not.toBeInTheDocument();
  });

  test("complete requires the review-confirmed signature and posts outcome=complete", async () => {
    const bodies: unknown[] = [];
    server.use(
      http.post("/api/v1/tasks/:id/decision", async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json({
          current_state: "COMPLETED",
          replayed: false,
          document_id: "11111111-1111-1111-1111-111111111111",
          next_review_due: "2028-06-10",
          signature_event_id: "se111111-1111-1111-1111-111111111111",
        });
      }),
    );
    renderCard({ subjectType: "PERIODIC_REVIEW" });
    await userEvent.click(screen.getByLabelText("Confirm — no change needed"));
    expect(screen.getByRole("button", { name: "Submit decision" })).toBeDisabled();
    expect(screen.getByText(/meaning: review confirmed/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox"));
    await userEvent.click(screen.getByRole("button", { name: "Submit decision" }));
    await waitFor(() => expect(bodies).toEqual([{ outcome: "complete" }]));
  });

  test("changes_requested requires a comment", async () => {
    renderCard({ subjectType: "PERIODIC_REVIEW" });
    await userEvent.click(screen.getByLabelText("Changes needed — a revision is required"));
    expect(screen.getByRole("button", { name: "Submit decision" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/Comment/), "Out of date — supplier tiers changed");
    expect(screen.getByRole("button", { name: "Submit decision" })).toBeEnabled();
  });

  test("a 409 renders the no-Effective-version copy, not 'already decided'", async () => {
    server.use(
      http.post("/api/v1/tasks/:id/decision", () =>
        HttpResponse.json(
          { code: "conflict", title: "Document no longer has an Effective version to confirm" },
          { status: 409 },
        ),
      ),
    );
    renderCard({ subjectType: "PERIODIC_REVIEW" });
    await userEvent.click(screen.getByLabelText("Confirm — no change needed"));
    await userEvent.click(screen.getByRole("checkbox"));
    await userEvent.click(screen.getByRole("button", { name: "Submit decision" }));
    expect(
      await screen.findByText(/no longer has an Effective version to confirm/),
    ).toBeInTheDocument();
    expect(screen.queryByText("This task was already decided.")).not.toBeInTheDocument();
  });

  test("DOCUMENT card is unchanged (regression pin)", () => {
    renderCard({ subjectType: "DOCUMENT" });
    expect(screen.getByLabelText("Approve")).toBeInTheDocument();
    expect(screen.getByLabelText("Request changes")).toBeInTheDocument();
    expect(screen.getByLabelText("Reject")).toBeInTheDocument();
  });
});
```

(`renderCard` = the file's existing helper; extend its props to accept `subjectType`.)

- [ ] **Step 2: Run to verify the new cases fail**

Run: `npx vitest run src/features/review/DecisionCard.test.tsx`
Expected: new cases FAIL (TS error on subjectType / missing radios).

- [ ] **Step 3: Implement**

`hooks.ts` — widen the union, the result type, and add the invalidation branch (DOCUMENT and CAPA branches byte-identical):

```ts
import type {
  DecisionBody,
  DecisionResult,
  DecisionSubjectType,
  PeriodicReviewDecisionResult,
  Task,
  TaskState,
  WorkflowInstance,
} from "../../lib/types";
// …
export interface DecideInput {
  taskId: string;
  subjectType: DecisionSubjectType;
  subjectId: string; // the document id or capa id — for cache invalidation
  idempotencyKey: string;
  body: DecisionBody;
}

export function useDecideTask() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ taskId, body, idempotencyKey }: DecideInput) =>
      api.send<DecisionResult | PeriodicReviewDecisionResult>(
        "POST",
        `/api/v1/tasks/${taskId}/decision`,
        body,
        { "Idempotency-Key": idempotencyKey },
      ),
    onSuccess: (_d, { taskId, subjectType, subjectId }) => {
      void qc.invalidateQueries({ queryKey: ["task", taskId] });
      void qc.invalidateQueries({ queryKey: ["tasks"] });
      if (subjectType === "DOCUMENT") {
        void qc.invalidateQueries({ queryKey: ["document", subjectId] });
        void qc.invalidateQueries({ queryKey: ["document-approval", subjectId] });
        void qc.invalidateQueries({ queryKey: ["document-versions", subjectId] });
      } else if (subjectType === "PERIODIC_REVIEW") {
        // subjectId IS the document id — the clock reset must show on the doc page + library.
        void qc.invalidateQueries({ queryKey: ["document", subjectId] });
        void qc.invalidateQueries({ queryKey: ["documents"] });
      } else {
        void qc.invalidateQueries({ queryKey: ["capa", subjectId] });
        void qc.invalidateQueries({ queryKey: ["capas"] });
        void qc.invalidateQueries({ queryKey: ["capa-approval", subjectId] });
      }
    },
  });
}
```

`DecisionCard.tsx` — outcome sets per subject (DOCUMENT/CAPA labels byte-identical to today):

```tsx
import type { DecisionOutcome, DecisionSubjectType } from "../../lib/types";

const NEEDS_COMMENT: DecisionOutcome[] = ["changes_requested", "reject"];

// Per-subject legal outcome sets. PERIODIC_REVIEW accepts ONLY complete | changes_requested
// (services/vault/review.py — approve/reject 422). DOCUMENT/CAPA stay byte-identical.
const OUTCOMES: Record<DecisionSubjectType, { value: DecisionOutcome; label: string }[]> = {
  DOCUMENT: [
    { value: "approve", label: "Approve" },
    { value: "changes_requested", label: "Request changes" },
    { value: "reject", label: "Reject" },
  ],
  CAPA: [
    { value: "approve", label: "Approve" },
    { value: "changes_requested", label: "Request changes" },
    { value: "reject", label: "Reject" },
  ],
  PERIODIC_REVIEW: [
    { value: "complete", label: "Confirm — no change needed" },
    { value: "changes_requested", label: "Changes needed — a revision is required" },
  ],
};
const SIGN_OUTCOME: Record<DecisionSubjectType, DecisionOutcome> = {
  DOCUMENT: "approve",
  CAPA: "approve",
  PERIODIC_REVIEW: "complete",
};
const SIGN_MEANING: Record<DecisionSubjectType, string> = {
  DOCUMENT: "approval",
  CAPA: "approval",
  PERIODIC_REVIEW: "review confirmed",
};
```

In the component: `subjectType: DecisionSubjectType` prop type; `const needsSig = outcome === SIGN_OUTCOME[subjectType];`; the radios map over `OUTCOMES[subjectType]`:

```tsx
          <Stack gap="xs" mt="xs">
            {OUTCOMES[subjectType].map((o) => (
              <Radio key={o.value} value={o.value} label={o.label} />
            ))}
          </Stack>
```

Signature label: `` label={`Signing as ${who} — meaning: ${SIGN_MEANING[subjectType]}`} ``. The 409 branch:

```tsx
        else if (e.status === 409)
          setError(
            subjectType === "PERIODIC_REVIEW"
              ? "The document no longer has an Effective version to confirm — it may have been obsoleted or be under revision."
              : "This task was already decided.",
          );
```

- [ ] **Step 4: Run to verify they pass**

Run: `npx vitest run src/features/review src/features/capa`
Expected: all PASS — the appended cases plus every existing DOCUMENT/CAPA decision test (the byte-identical pin).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/review/DecisionCard.tsx apps/web/src/features/review/hooks.ts apps/web/src/features/review/DecisionCard.test.tsx
git commit -m "feat(s-web-8): PERIODIC_REVIEW decision variant — complete/changes_requested, review-confirmed signature, honest 409"
```

---

### Task 10: `PeriodicReviewContext` + the `ReviewApprovePage` branch

**Files:**
- Create: `apps/web/src/features/review/PeriodicReviewContext.tsx`
- Modify: `apps/web/src/features/review/ReviewApprovePage.tsx`
- Modify: `apps/web/src/features/review/ReviewApprovePage.test.tsx` (append cases)

- [ ] **Step 1: Write the failing tests** (append; the route scaffolding mirrors the file's existing cases — `/tasks/:id` route + `periodicReviewTask.id`)

```tsx
import { periodicReviewTask } from "../../test/msw/handlers";

describe("ReviewApprovePage — PERIODIC_REVIEW", () => {
  test("renders the doc context + the periodic decision card, and never reads the workflow instance", async () => {
    let instanceHit = false;
    server.use(
      http.get("/api/v1/workflow-instances/:id", () => {
        instanceHit = true;
        return HttpResponse.json(approvalFixture);
      }),
    );
    renderAtTask(periodicReviewTask.id);
    expect(await screen.findByText("Periodic review")).toBeInTheDocument();
    expect(await screen.findByText("SOP-PUR-014")).toBeInTheDocument();
    expect(screen.getByText("Supplier Selection & Evaluation")).toBeInTheDocument();
    expect(screen.getByLabelText("Confirm — no change needed")).toBeInTheDocument();
    // the obsolete path is a LINK to the doc page, not a task outcome
    expect(screen.getByText(/Obsolete it from the document page/)).toBeInTheDocument();
    expect(instanceHit).toBe(false);
  });

  test("a document-read 403 degrades calmly — the decision card still renders", async () => {
    server.use(
      http.get("/api/v1/documents/:id", () =>
        HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
      ),
    );
    renderAtTask(periodicReviewTask.id);
    expect(await screen.findByText("Document details not visible to you")).toBeInTheDocument();
    expect(screen.getByLabelText("Confirm — no change needed")).toBeInTheDocument();
  });

  test("a decided task shows the Decided alert instead of the card", async () => {
    server.use(
      http.get("/api/v1/tasks/:id", () =>
        HttpResponse.json({ ...periodicReviewTask, state: "DONE" }),
      ),
    );
    renderAtTask(periodicReviewTask.id);
    expect(await screen.findByText("This task has already been decided.")).toBeInTheDocument();
    expect(screen.queryByLabelText("Confirm — no change needed")).not.toBeInTheDocument();
  });
});
```

(`renderAtTask(id)` = the file's existing route-scaffolding helper; reuse it verbatim.)

- [ ] **Step 2: Run to verify they fail**

Run: `npx vitest run src/features/review/ReviewApprovePage.test.tsx`
Expected: new cases FAIL (no periodic branch yet — the page falls into the DOCUMENT branch).

- [ ] **Step 3: Implement**

`PeriodicReviewContext.tsx`:

```tsx
import { Alert, Anchor, Card, Group, Stack, Table, Text } from "@mantine/core";
import { Link } from "react-router-dom";
import { ApiError } from "../../lib/api";
import { ReviewStateBadge } from "../document/ReviewStateBadge";
import { useDocument } from "../document/useDocument";

// S-web-8: the PERIODIC_REVIEW task's left column — the document under review, loaded BEST-EFFORT
// via document.read. A 403 degrades calmly and never blocks the decision card: the decision
// authority is server-side ownership (live re-check), not this read.
export function PeriodicReviewContext({ documentId }: { documentId: string }) {
  const { data: doc, isLoading, isError, error } = useDocument(documentId, { enabled: true });

  if (isLoading && !doc) return <Text c="dimmed">Loading the document under review…</Text>;
  if (isError || !doc) {
    const status = error instanceof ApiError ? error.status : 0;
    return (
      <Alert color="yellow" title="Document details not visible to you">
        <Text size="sm">
          {status === 403
            ? "You can decide this review, but reading the document isn't granted to you."
            : "Could not load the document under review."}
        </Text>
      </Alert>
    );
  }
  return (
    <Card withBorder>
      <Stack gap="sm">
        <Group justify="space-between" align="flex-start">
          <div>
            <Text ff="monospace" size="sm">
              {doc.identifier}
            </Text>
            <Text fw={600}>{doc.title}</Text>
          </div>
          <ReviewStateBadge state={doc.review_state} />
        </Group>
        <Table withRowBorders={false}>
          <Table.Tbody>
            <Table.Tr>
              <Table.Td>
                <Text size="sm" c="dimmed">
                  State
                </Text>
              </Table.Td>
              <Table.Td>{doc.current_state}</Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Td>
                <Text size="sm" c="dimmed">
                  Effective
                </Text>
              </Table.Td>
              <Table.Td>{doc.effective_from ? doc.effective_from.slice(0, 10) : "—"}</Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Td>
                <Text size="sm" c="dimmed">
                  Review period
                </Text>
              </Table.Td>
              <Table.Td>
                {doc.review_period_months !== null ? `${doc.review_period_months} months` : "—"}
              </Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Td>
                <Text size="sm" c="dimmed">
                  Last reviewed
                </Text>
              </Table.Td>
              <Table.Td>{doc.last_reviewed_at ? doc.last_reviewed_at.slice(0, 10) : "—"}</Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Td>
                <Text size="sm" c="dimmed">
                  Next review due
                </Text>
              </Table.Td>
              <Table.Td>{doc.next_review_due ?? "—"}</Table.Td>
            </Table.Tr>
          </Table.Tbody>
        </Table>
        <Anchor component={Link} to={`/documents/${doc.id}`} size="sm">
          Open the document page →
        </Anchor>
        <Text size="xs" c="dimmed">
          Decided it should be retired instead? Obsolete it from the document page — that is not a
          review outcome.
        </Text>
      </Stack>
    </Card>
  );
}
```

`ReviewApprovePage.tsx` — three edits, DOCUMENT/CAPA blocks untouched:

```tsx
import { PeriodicReviewContext } from "./PeriodicReviewContext";
// after isCapa:
  const isPeriodic = task?.subject_type === "PERIODIC_REVIEW";
  // Document branch (unchanged): resolve the subject doc via the instance. Disabled for CAPA AND
  // periodic tasks (a periodic task's subject id is on the task itself — no document.read-gated
  // instance read; the deciding owner may hold no workflow read at all).
  const { data: instance } = useWorkflowInstance(
    !isCapa && !isPeriodic && task ? task.instance_id : null,
  );
  const docId = !isCapa && !isPeriodic ? (instance?.subject_id ?? null) : null;
// after the isCapa return block, before the document return:
  if (isPeriodic) {
    // The subject id is on the task (S-web-7b's detail enrichment) → always present here.
    return (
      <Stack gap="lg">
        <Title order={2}>Periodic review</Title>
        <Grid gutter="lg" align="flex-start">
          <Grid.Col span={{ base: 12, md: 7 }}>
            <PeriodicReviewContext documentId={task.subject_id!} />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 5 }}>
            {decidable ? (
              <DecisionCard
                taskId={task.id}
                subjectType="PERIODIC_REVIEW"
                subjectId={task.subject_id!}
              />
            ) : (
              decidedAlert
            )}
          </Grid.Col>
        </Grid>
      </Stack>
    );
  }
```

- [ ] **Step 4: Run to verify they pass**

Run: `npx vitest run src/features/review`
Expected: all PASS (new + existing S-web-5/7b cases — the byte-identical pin).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/review/PeriodicReviewContext.tsx apps/web/src/features/review/ReviewApprovePage.tsx apps/web/src/features/review/ReviewApprovePage.test.tsx
git commit -m "feat(s-web-8): PERIODIC_REVIEW task page — best-effort doc context, no instance read, obsolete-is-a-link"
```

---

### Task 11: Compliance checklist overdue leg

**Files:**
- Modify: `apps/web/src/features/compliance/CompliancePage.tsx`
- Modify: `apps/web/src/features/compliance/CompliancePage.test.tsx` (append cases)

- [ ] **Step 1: Write the failing tests** (append)

```tsx
describe("overdue-review leg (S-web-8)", () => {
  test("rollup shows the overdue-review counter", async () => {
    renderWithProviders(<CompliancePage />);
    expect(await screen.findByText(/Review overdue: 1/)).toBeInTheDocument();
  });

  test("an overdue row gets the badge; others render a dash", async () => {
    renderWithProviders(<CompliancePage />);
    await screen.findByText("4.3");
    const overdueRow = screen.getByText("4.3").closest("tr")!;
    expect(within(overdueRow).getByText("Overdue")).toBeInTheDocument();
    const cleanRow = screen.getByText("6.2").closest("tr")!;
    expect(within(cleanRow).queryByText("Overdue")).not.toBeInTheDocument();
  });

  test("overdue is orthogonal — the 4.3 row is still COVERED", async () => {
    renderWithProviders(<CompliancePage />);
    await screen.findByText("4.3");
    const row = screen.getByText("4.3").closest("tr")!;
    expect(within(row).getByText(/covered/i)).toBeInTheDocument();
    expect(within(row).getByText("Overdue")).toBeInTheDocument();
  });
});
```

(Import `within` from `@testing-library/react`; match the CoverageBadge's actual rendered text for the COVERED assertion — check the existing tests in this file.)

- [ ] **Step 2: Run to verify they fail**

Run: `npx vitest run src/features/compliance`
Expected: new cases FAIL.

- [ ] **Step 3: Implement** — in `CompliancePage.tsx`:

The rollup group gains one counter (plain text — no aria-label, the S-web-6 collision trap):

```tsx
        <Text>⏰ Review overdue: {rollup.overdue_review}</Text>
```

The table head gains `<Table.Th>Review</Table.Th>` after Status; each row gains:

```tsx
              <Table.Td>
                {r.overdue_review ? (
                  <Badge color="red" variant="light">
                    Overdue
                  </Badge>
                ) : (
                  <Text c="dimmed" size="sm">
                    —
                  </Text>
                )}
              </Table.Td>
```

(Add `Badge` to the Mantine import.)

- [ ] **Step 4: Run to verify they pass**

Run: `npx vitest run src/features/compliance`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/compliance
git commit -m "feat(s-web-8): checklist overdue-review rollup counter + per-row badge"
```

---

### Task 12: Full gates + docs

**Files:**
- Modify: `docs/slice-history.md` (new S-web-8 entry, top of the web track — follow the S-web-7d entry's shape: what shipped, the traps hit, test counts)
- Modify: `CLAUDE.md` (Current status: S-web-8 ✅ one-liner pointing at slice-history; drop "the trailing S-web-8 UI remains" from the drift-family line)

- [ ] **Step 1: Full verification**

Run from repo root: `/check-web` (eslint + tsc + build + the full vitest suite). For the vitest leg, if the parallel run thrashes ("document is not defined" mass-fail), use:
`cd apps/web; npx vitest run --pool=forks --poolOptions.forks.singleFork=true`
Expected: 0 eslint errors, tsc clean, build OK, ~540 tests green (499 baseline + the new ones).

- [ ] **Step 2: Write the docs**

`docs/slice-history.md`: add the S-web-8 entry (front-end-only; the four surfaces; the settled design calls; pinned-shape notes — scans-nullable/counts-open-bag, explicit-null clear, periodic-skips-instance-read).
`CLAUDE.md`: update the Current-status block only (keep it a pointer).

- [ ] **Step 3: Commit**

```bash
git add docs/slice-history.md CLAUDE.md
git commit -m "docs(s-web-8): slice-history entry + status pointer"
```

---

### Task 13: diff-critic + live smoke + PR

- [ ] **Step 1: diff-critic** — run the `diff-critic` agent on `git diff main...feat/sweb8-drift-review-ui`; fold confirmed findings (fix → re-run the touched tests → commit).

- [ ] **Step 2: Live smoke (Chrome MCP, http://localhost)** — per spec §9:
1. `docker compose --env-file .env -f infra/compose/compose.yml -f infra/compose/compose.s.yml up -d --build web`, hard-refresh/Incognito.
2. Login `demo` / `Demo-Password-1`. **Inherited S-drift-3 obligation:** Drift appears in the LeftRail (native `drift.read`); `/drift` renders REAL `MIRROR`/`BLOB_REHASH` scan rows (the authed-200 leg of `GET /admin/drift/status`); the Superseded-copies tab renders (the authed-200 leg of the second endpoint). If the D4 set is empty, mint a row via the worker heredoc (create→release→`render_dynamic_copy` export→revise+release, signing as the approver — SoD-2) and reload.
3. D5 loop: grant demo the SYSTEM document overrides + `report.compliance_checklist.read` (LIVE login's app_user row, org `AHT` — re-created Keycloak users mint new JIT rows); make demo's app_user the `owner_user_id` of a test doc; set the period via the modal (PATCH leg); backdate `next_review_due` via heredoc; run the sweep; `/tasks` shows the task; `/compliance` shows the overdue counter+badge; decide `complete` with signature; the doc page shows the reset clock; `/compliance` clears.
- [ ] **Step 3: PR** — push the branch, open the PR against `main` (the `/pr` skill or `gh pr create`), body: what shipped, the four surfaces, front-end-only note, smoke evidence, the test delta.

---

## Self-review (done at planning time)

- **Spec coverage:** §0–§6 → Tasks 1–11; §7 error cases are embedded as test cases; §8 → per-task TDD + Task 12; §9 → Task 13; §10/§11 → Tasks 12–13. No gaps.
- **Placeholders:** none — every code step carries the code.
- **Type consistency:** `DecisionSubjectType` defined in Task 1, consumed in Tasks 9–10; `ReviewState` in Task 1, consumed in Tasks 2/7/10; `SUPERSEDED_PAGE_SIZE` defined in Task 3, consumed in Task 5; fixture names (`driftStatusFixture`, `supersededCopiesFixture`, `periodicReviewTask`) defined in Task 1, consumed in Tasks 3–6 and 10.
