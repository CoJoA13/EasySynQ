# S-web-7c — Complaint & NCR intake Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface the complaint + NCR intake front doors in the SPA — complaint list/create/idempotent spawn-CAPA and NCR list/create/one-shot disposition — as tabbed sub-routes under `/capa`, over the already-built+contracted backend.

**Architecture:** A thin layout route (`CapaLayout`) wraps the three faces (Board / Complaints / NCRs) with a secondary tab bar + `<Outlet/>`, so the shipped `CapaBoardPage` stays byte-identical. Each new face is its own page composing a create modal + a per-row action (spawn-CAPA / record-disposition). All reads/writes reuse the existing `useApi()` + react-query + `usePermissions()` idioms from 7a/7b. Front-end only — no migration, no new permission key, no contract change.

**Tech Stack:** React 18 + TypeScript (strict) · Mantine v7 · TanStack Query v5 · React Router v6 · MSW + Vitest + jest-axe.

**Spec:** `docs/superpowers/specs/2026-06-09-web-track-s-web-7c-complaint-ncr-intake-design.md`

**Working dir:** `apps/web`. Run a single test file with `npm test -- src/features/capa/<File>.test.tsx`. Lint `npm run lint`; typecheck `npm run typecheck`; full build `npm run build`.

**Branch:** `feat/s-web-7c-complaint-ncr-intake` (already created; the spec commit is on it).

---

## File map

**Create:**
- `apps/web/src/features/capa/intake.ts` — NCR source + disposition label/value constants.
- `apps/web/src/features/capa/intake.test.ts`
- `apps/web/src/features/capa/CapaLayout.tsx` — secondary tab bar + `<Outlet/>`.
- `apps/web/src/features/capa/CapaLayout.test.tsx`
- `apps/web/src/features/capa/ComplaintForm.tsx` — log-complaint modal.
- `apps/web/src/features/capa/ComplaintsPage.tsx` — complaint list + create + spawn/view action.
- `apps/web/src/features/capa/ComplaintsPage.test.tsx`
- `apps/web/src/features/capa/NcrForm.tsx` — raise-NCR modal.
- `apps/web/src/features/capa/DispositionModal.tsx` — one-shot disposition modal.
- `apps/web/src/features/capa/NcrsPage.tsx` — NCR list + create + disposition action.
- `apps/web/src/features/capa/NcrsPage.test.tsx`
- `apps/web/src/features/capa/CapaRouting.test.tsx` — board→complaints→ncrs wiring test.

**Modify:**
- `apps/web/src/lib/types.ts` — add `Complaint`/`Ncr`/unions/request-bodies to the S-web-7 block.
- `apps/web/src/features/capa/hooks.ts` — add `useComplaints`/`useNcrs`.
- `apps/web/src/features/capa/hooks.test.tsx` — add their tests.
- `apps/web/src/features/capa/mutations.ts` — add `useCreateComplaint`/`useSpawnCapa`/`useCreateNcr`/`useNcrDisposition`.
- `apps/web/src/features/capa/mutations.test.tsx` — add their tests.
- `apps/web/src/test/msw/handlers.ts` — add complaint/NCR fixtures + default handlers.
- `apps/web/src/App.tsx` — nest `/capa` under `CapaLayout`.
- `docs/slice-history.md` + `CLAUDE.md` — slice entry + learnings (Task 8).

---

## Task 1: Types + label constants

**Files:**
- Modify: `apps/web/src/lib/types.ts` (append to the `// ---- S-web-7` block, after `EvidenceLinkBody`)
- Create: `apps/web/src/features/capa/intake.ts`
- Test: `apps/web/src/features/capa/intake.test.ts`

- [ ] **Step 1: Add the types** to `lib/types.ts`, immediately after the existing `EvidenceLinkBody` interface (around line 804):

```ts
// ---- S-web-7c (Complaint + NCR intake) ----
export type NcrSource = "audit" | "process" | "complaint" | "internal";
export type NcrDisposition =
  | "use_as_is" | "rework" | "scrap" | "return" | "concession" | "regrade";

// Pinned to the _complaint serializer (api/capa.py:217). identifier may be null (get_identifier).
export interface Complaint {
  id: string;
  identifier: string | null;
  customer: string | null;
  received_at: string | null;
  channel: string | null;
  description: string;
  severity: NcSeverity | null;
  spawned_capa_id: string | null; // set once a CAPA has been spawned (idempotency latch)
}
export interface ComplaintList { data: Complaint[]; }

// Pinned to the _ncr serializer (api/capa.py:230). identifier is NCR-NNN, non-null (ncr.identifier nullable=False).
export interface Ncr {
  id: string;
  identifier: string;
  source: NcrSource;
  description: string;
  severity: NcSeverity;
  process_id: string | null;
  disposition: NcrDisposition | null;
  disposition_authorized_by: string | null;
  disposition_notes: string | null;
  disposed_at: string | null;
  created_at: string;
}
export interface NcrList { data: Ncr[]; }

export interface ComplaintCreateBody {
  description: string;
  customer?: string;
  received_at?: string;
  channel?: string;
  severity?: NcSeverity;
}
export interface SpawnCapaBody { severity?: NcSeverity; process_id?: string; }
export interface NcrCreateBody {
  source: NcrSource;
  description: string;
  severity: NcSeverity;
  process_id?: string;
}
export interface NcrDispositionBody { disposition: NcrDisposition; notes?: string; }
```

- [ ] **Step 2: Write the failing test** `apps/web/src/features/capa/intake.test.ts`:

```ts
import { expect, test } from "vitest";
import { DISPOSITION_LABEL, DISPOSITIONS, NCR_SOURCE_LABEL, NCR_SOURCES } from "./intake";

test("every NCR disposition has a label (and there are exactly the 6 ISO 8.7 tokens)", () => {
  expect(DISPOSITIONS).toEqual(["use_as_is", "rework", "scrap", "return", "concession", "regrade"]);
  for (const d of DISPOSITIONS) expect(DISPOSITION_LABEL[d]).toBeTruthy();
});

test("every NCR source has a label", () => {
  expect(NCR_SOURCES).toEqual(["audit", "process", "complaint", "internal"]);
  for (const s of NCR_SOURCES) expect(NCR_SOURCE_LABEL[s]).toBeTruthy();
});
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `npm test -- src/features/capa/intake.test.ts`
Expected: FAIL — cannot resolve `./intake`.

- [ ] **Step 4: Implement** `apps/web/src/features/capa/intake.ts`:

```ts
import type { NcrDisposition, NcrSource } from "../../lib/types";

// The NCR source vocabulary (NcrSource adds `internal` over CapaSource; no `review_output`).
export const NCR_SOURCES: NcrSource[] = ["audit", "process", "complaint", "internal"];
export const NCR_SOURCE_LABEL: Record<NcrSource, string> = {
  audit: "Audit",
  process: "Process",
  complaint: "Complaint",
  internal: "Internal",
};

// The ISO 9001 §8.7 disposition tokens (R20). The canonical token for the Python `RETURN_` member is `return`.
export const DISPOSITIONS: NcrDisposition[] = [
  "use_as_is", "rework", "scrap", "return", "concession", "regrade",
];
export const DISPOSITION_LABEL: Record<NcrDisposition, string> = {
  use_as_is: "Use as-is",
  rework: "Rework",
  scrap: "Scrap",
  return: "Return to supplier",
  concession: "Concession",
  regrade: "Regrade",
};
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `npm test -- src/features/capa/intake.test.ts`
Expected: PASS (2 tests).

- [ ] **Step 6: Typecheck** (the new types are only compile-verified until consumed)

Run: `npm run typecheck`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/lib/types.ts apps/web/src/features/capa/intake.ts apps/web/src/features/capa/intake.test.ts
git commit -m "feat(s-web-7c): complaint/NCR types + intake label constants"
```

---

## Task 2: MSW fixtures + handlers + read hooks

**Files:**
- Modify: `apps/web/src/test/msw/handlers.ts`
- Modify: `apps/web/src/features/capa/hooks.ts`
- Test: `apps/web/src/features/capa/hooks.test.tsx`

- [ ] **Step 1: Add the fixtures + default handlers** to `handlers.ts`.

First extend the top import to pull the new types:

```ts
import type { Capa, Complaint, Ncr } from "../../lib/types";
```

Add these fixtures right after the `recordsFixture` block (around line 522):

```ts
// ---- S-web-7c complaint + NCR fixtures (pinned to the _complaint / _ncr serializers) ----
export const complaintListFixture = {
  data: [
    { id: "cm000001-0001-0001-0001-000000000001", identifier: "CMP-000007", customer: "Northwind Foods Ltd.", received_at: "2026-06-02T09:00:00+00:00", channel: "email", description: "Delivered batch missing CoA documents.", severity: "Critical", spawned_capa_id: null },
    { id: "cm000002-0002-0002-0002-000000000002", identifier: "CMP-000006", customer: "Acme Pharma", received_at: "2026-05-30T09:00:00+00:00", channel: "phone", description: "Late delivery on PO-44821.", severity: "Minor", spawned_capa_id: "ca000002-0002-0002-0002-000000000002" },
  ],
} satisfies { data: Complaint[] };

export const ncrListFixture = {
  data: [
    { id: "nc000001-0001-0001-0001-000000000001", identifier: "NCR-000052", source: "process", description: "Nonconforming output: torque out of spec on Line 2.", severity: "Major", process_id: null, disposition: null, disposition_authorized_by: null, disposition_notes: null, disposed_at: null, created_at: "2026-06-03T09:00:00+00:00" },
    { id: "nc000002-0002-0002-0002-000000000002", identifier: "NCR-000049", source: "audit", description: "Mislabelled retain samples.", severity: "Minor", process_id: null, disposition: "rework", disposition_authorized_by: "bbbb1111-1111-1111-1111-111111111111", disposition_notes: "Re-labelled + re-inspected.", disposed_at: "2026-06-04T09:00:00+00:00", created_at: "2026-06-01T09:00:00+00:00" },
  ],
} satisfies { data: Ncr[] };
```

Add these handlers into the `handlers` array, right after the `http.get("/api/v1/records", …)` handler (around line 819):

```ts
// ---- S-web-7c complaints + NCRs (default happy-path; per-test overrides for 403/empty/error) ----
http.get("/api/v1/complaints", () => HttpResponse.json(complaintListFixture)),
http.post("/api/v1/complaints", () =>
  HttpResponse.json({ ...complaintListFixture.data[0]!, id: "cm-new-0000-0000-0000-000000000000", spawned_capa_id: null }, { status: 201 }),
),
http.post("/api/v1/complaints/:id/spawn-capa", () =>
  HttpResponse.json({ ...capaDetailFixture, id: "ca-spawn-0000-0000-0000-000000000000", source: "complaint" }, { status: 201 }),
),
http.get("/api/v1/ncrs", () => HttpResponse.json(ncrListFixture)),
http.post("/api/v1/ncrs", () =>
  HttpResponse.json({ ...ncrListFixture.data[0]!, id: "nc-new-0000-0000-0000-000000000000" }, { status: 201 }),
),
http.patch("/api/v1/ncrs/:id/disposition", ({ params }) =>
  HttpResponse.json({ ...ncrListFixture.data[0]!, id: String(params.id), disposition: "rework", disposition_authorized_by: "bbbb1111-1111-1111-1111-111111111111", disposed_at: "2026-06-09T09:00:00+00:00" }),
),
```

- [ ] **Step 2: Write the failing hook tests** — append to `apps/web/src/features/capa/hooks.test.tsx`. Extend the existing import on line 9:

```ts
import { useCapa, useCapas, useComplaints, useNcrs } from "./hooks";
```

Append after the last test:

```ts
test("useComplaints returns the {data} rows", async () => {
  const { result } = renderHook(() => useComplaints(), { wrapper });
  await waitFor(() => expect(result.current.data).toBeDefined());
  expect(result.current.data!.length).toBeGreaterThan(0);
  expect(result.current.forbidden).toBe(false);
});

test("useComplaints surfaces a 403 as forbidden", async () => {
  server.use(
    http.get("/api/v1/complaints", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  const { result } = renderHook(() => useComplaints(), { wrapper });
  await waitFor(() => expect(result.current.forbidden).toBe(true));
});

test("useNcrs returns the {data} rows", async () => {
  const { result } = renderHook(() => useNcrs(), { wrapper });
  await waitFor(() => expect(result.current.data).toBeDefined());
  expect(result.current.data!.length).toBeGreaterThan(0);
});

test("useNcrs surfaces a 403 as forbidden", async () => {
  server.use(
    http.get("/api/v1/ncrs", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  const { result } = renderHook(() => useNcrs(), { wrapper });
  await waitFor(() => expect(result.current.forbidden).toBe(true));
});
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `npm test -- src/features/capa/hooks.test.tsx`
Expected: FAIL — `useComplaints`/`useNcrs` are not exported from `./hooks`.

- [ ] **Step 4: Implement the hooks** — append to `apps/web/src/features/capa/hooks.ts`. Extend the type import on line 3:

```ts
import type { Capa, CapaApproval, CapaList, ComplaintList, NcrList, RecordSummary } from "../../lib/types";
```

Append the hooks:

```ts
// GET /complaints — gated record.read; the demo admin holds none of these keys (calm-403). retry:false.
export function useComplaints() {
  const api = useApi();
  const query = useQuery({
    queryKey: ["complaints"],
    queryFn: async () => (await api.get<ComplaintList>("/api/v1/complaints")).data,
    retry: false,
  });
  const forbidden = query.error instanceof ApiError && query.error.status === 403;
  return { ...query, forbidden };
}

// GET /ncrs — gated ncr.read (QMS-Owner / Internal-Auditor only; SYSTEM-override for the demo admin).
export function useNcrs() {
  const api = useApi();
  const query = useQuery({
    queryKey: ["ncrs"],
    queryFn: async () => (await api.get<NcrList>("/api/v1/ncrs")).data,
    retry: false,
  });
  const forbidden = query.error instanceof ApiError && query.error.status === 403;
  return { ...query, forbidden };
}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `npm test -- src/features/capa/hooks.test.tsx`
Expected: PASS (all tests, including the 4 new ones).

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/test/msw/handlers.ts apps/web/src/features/capa/hooks.ts apps/web/src/features/capa/hooks.test.tsx
git commit -m "feat(s-web-7c): complaint/NCR MSW fixtures + read hooks (useComplaints/useNcrs)"
```

---

## Task 3: Write mutations

**Files:**
- Modify: `apps/web/src/features/capa/mutations.ts`
- Test: `apps/web/src/features/capa/mutations.test.tsx`

- [ ] **Step 1: Write the failing tests** — append to `apps/web/src/features/capa/mutations.test.tsx`. Extend the import on lines 8-13:

```ts
import {
  useCapaClose,
  useCapaContainment,
  useCreateComplaint,
  useCreateNcr,
  useLinkEvidence,
  useNcrDisposition,
  useRaiseCapa,
  useSpawnCapa,
} from "./mutations";
```

Append after the last test:

```ts
test("useCreateComplaint POSTs /complaints", async () => {
  const { result } = renderHook(() => useCreateComplaint(), { wrapper });
  const c = await result.current.mutateAsync({ description: "missing CoA" });
  expect(c.id).toBeDefined();
});

test("useSpawnCapa POSTs to the complaint's spawn-capa path", async () => {
  const { result } = renderHook(() => useSpawnCapa(), { wrapper });
  const capa = await result.current.mutateAsync({
    complaintId: "cm000001-0001-0001-0001-000000000001",
    severity: "Critical",
  });
  expect(capa.id).toBeDefined();
});

test("useCreateNcr POSTs /ncrs", async () => {
  const { result } = renderHook(() => useCreateNcr(), { wrapper });
  const n = await result.current.mutateAsync({ source: "process", description: "out of spec", severity: "Major" });
  expect(n.id).toBeDefined();
});

test("useNcrDisposition PATCHes the NCR's disposition path", async () => {
  const { result } = renderHook(
    () => useNcrDisposition("nc000001-0001-0001-0001-000000000001"),
    { wrapper },
  );
  await result.current.mutateAsync({ disposition: "rework", notes: "re-inspected" });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npm test -- src/features/capa/mutations.test.tsx`
Expected: FAIL — the four mutation hooks are not exported.

- [ ] **Step 3: Implement the mutations** — append to `apps/web/src/features/capa/mutations.ts`. Extend the type import on line 4:

```ts
import type {
  Capa,
  CapaRaiseBody,
  CapaVerifyBody,
  Complaint,
  ComplaintCreateBody,
  Ncr,
  NcrCreateBody,
  NcrDispositionBody,
  NcSeverity,
  SpawnCapaBody,
  StageBlockBody,
} from "../../lib/types";
```

Append the hooks:

```ts
// --- S-web-7c complaint + NCR intake writes -------------------------------------------------

export function useCreateComplaint() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ComplaintCreateBody) =>
      api.send<Complaint>("POST", "/api/v1/complaints", body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["complaints"] }),
  });
}

// Idempotent server-side (201 new / 200 replay both resolve here). We invalidate the complaint list
// (its spawned_capa_id flips) + the CAPA board (the new CAPA appears).
export function useSpawnCapa() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ complaintId, severity }: { complaintId: string; severity?: NcSeverity }) =>
      api.send<Capa>(
        "POST",
        `/api/v1/complaints/${complaintId}/spawn-capa`,
        { severity } satisfies SpawnCapaBody,
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["complaints"] });
      void qc.invalidateQueries({ queryKey: ["capas"] });
    },
  });
}

export function useCreateNcr() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: NcrCreateBody) => api.send<Ncr>("POST", "/api/v1/ncrs", body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["ncrs"] }),
  });
}

// One-shot ISO 8.7 disposition (409 ncr_already_dispositioned if already set — the caller surfaces it calmly).
export function useNcrDisposition(ncrId: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: NcrDispositionBody) =>
      api.send<Ncr>("PATCH", `/api/v1/ncrs/${ncrId}/disposition`, body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["ncrs"] }),
  });
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `npm test -- src/features/capa/mutations.test.tsx`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/capa/mutations.ts apps/web/src/features/capa/mutations.test.tsx
git commit -m "feat(s-web-7c): complaint/NCR write mutations (create/spawn/disposition)"
```

---

## Task 4: CapaLayout (the secondary tab bar)

**Files:**
- Create: `apps/web/src/features/capa/CapaLayout.tsx`
- Test: `apps/web/src/features/capa/CapaLayout.test.tsx`

- [ ] **Step 1: Write the failing test** `apps/web/src/features/capa/CapaLayout.test.tsx`:

```tsx
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { expect, test } from "vitest";
import { renderWithProviders } from "../../test/render";
import { CapaLayout } from "./CapaLayout";

function tree() {
  return (
    <Routes>
      <Route path="capa" element={<CapaLayout />}>
        <Route index element={<div>BOARD FACE</div>} />
        <Route path="complaints" element={<div>COMPLAINTS FACE</div>} />
        <Route path="ncrs" element={<div>NCRS FACE</div>} />
      </Route>
    </Routes>
  );
}

test("renders the board face + three tabs at /capa", async () => {
  renderWithProviders(tree(), { route: "/capa" });
  expect(await screen.findByText("BOARD FACE")).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Board" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Complaints" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "NCRs" })).toBeInTheDocument();
});

test("the active tab follows the deep-linked route", async () => {
  renderWithProviders(tree(), { route: "/capa/ncrs" });
  expect(await screen.findByText("NCRS FACE")).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "NCRs" })).toHaveAttribute("aria-selected", "true");
});

test("clicking a tab navigates to that face", async () => {
  const u = userEvent.setup();
  renderWithProviders(tree(), { route: "/capa" });
  await screen.findByText("BOARD FACE");
  await u.click(screen.getByRole("tab", { name: "Complaints" }));
  expect(await screen.findByText("COMPLAINTS FACE")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm test -- src/features/capa/CapaLayout.test.tsx`
Expected: FAIL — cannot resolve `./CapaLayout`.

- [ ] **Step 3: Implement** `apps/web/src/features/capa/CapaLayout.tsx`:

```tsx
import { Container, Tabs } from "@mantine/core";
import { Outlet, useLocation, useNavigate } from "react-router-dom";

// The Nonconformity & CAPA front door's secondary nav (S-web-7c). The board lives at the index route
// and is UNCHANGED — this layout only adds the tab strip + <Outlet/>. No <Title> here, so each face
// (incl. the byte-identical CapaBoardPage) keeps its own.
const TABS = [
  { value: "board", label: "Board", path: "/capa" },
  { value: "complaints", label: "Complaints", path: "/capa/complaints" },
  { value: "ncrs", label: "NCRs", path: "/capa/ncrs" },
] as const;

function activeTab(pathname: string): string {
  if (pathname.startsWith("/capa/complaints")) return "complaints";
  if (pathname.startsWith("/capa/ncrs")) return "ncrs";
  return "board";
}

export function CapaLayout() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  return (
    <>
      <Container size="xl" pt="md" pb={0}>
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

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm test -- src/features/capa/CapaLayout.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/capa/CapaLayout.tsx apps/web/src/features/capa/CapaLayout.test.tsx
git commit -m "feat(s-web-7c): CapaLayout secondary tab bar (Board/Complaints/NCRs)"
```

---

## Task 5: Complaints surface (form + page)

**Files:**
- Create: `apps/web/src/features/capa/ComplaintForm.tsx`
- Create: `apps/web/src/features/capa/ComplaintsPage.tsx`
- Test: `apps/web/src/features/capa/ComplaintsPage.test.tsx`

- [ ] **Step 1: Write the failing test** `apps/web/src/features/capa/ComplaintsPage.test.tsx`:

```tsx
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { ComplaintsPage } from "./ComplaintsPage";

function grant(keys: string[]) {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: keys.map((key) => ({ key, effect: "ALLOW", source: null })),
      }),
    ),
  );
}

test("lists complaints from {data}", async () => {
  renderWithProviders(<ComplaintsPage />, { route: "/capa/complaints" });
  expect(await screen.findByText("CMP-000007")).toBeInTheDocument();
  expect(screen.getByText(/Delivered batch missing CoA/)).toBeInTheDocument();
});

test("hides 'Log complaint' without record.create; shows + opens it with the key", async () => {
  grant(["record.create"]);
  const u = userEvent.setup();
  renderWithProviders(<ComplaintsPage />, { route: "/capa/complaints" });
  const btn = await screen.findByRole("button", { name: /Log complaint/ });
  await u.click(btn);
  expect(await screen.findByLabelText(/Description/)).toBeInTheDocument();
});

test("logging a complaint POSTs /complaints and closes the modal", async () => {
  grant(["record.create"]);
  let posted = false;
  server.use(
    http.post("/api/v1/complaints", () => {
      posted = true;
      return HttpResponse.json(
        { id: "x", identifier: "CMP-x", customer: null, received_at: null, channel: null, description: "x", severity: null, spawned_capa_id: null },
        { status: 201 },
      );
    }),
  );
  const u = userEvent.setup();
  renderWithProviders(<ComplaintsPage />, { route: "/capa/complaints" });
  await u.click(await screen.findByRole("button", { name: /Log complaint/ }));
  await u.type(await screen.findByLabelText(/Description/), "Customer reported a missing CoA");
  await u.click(screen.getByRole("button", { name: /^Log complaint$/, hidden: false }));
  await waitFor(() => expect(posted).toBe(true));
});

test("shows 'Spawn CAPA' for an unspawned complaint and POSTs the spawn", async () => {
  grant(["capa.create"]);
  let spawned = false;
  server.use(
    http.post("/api/v1/complaints/:id/spawn-capa", () => {
      spawned = true;
      return HttpResponse.json({ id: "ca-x" }, { status: 201 });
    }),
  );
  const u = userEvent.setup();
  renderWithProviders(<ComplaintsPage />, { route: "/capa/complaints" });
  const row = await screen.findByRole("row", { name: /CMP-000007/ });
  await u.click(within(row).getByRole("button", { name: /Spawn CAPA/ }));
  await waitFor(() => expect(spawned).toBe(true));
});

test("shows 'View CAPA' (not Spawn) for an already-spawned complaint", async () => {
  grant(["capa.create"]);
  renderWithProviders(<ComplaintsPage />, { route: "/capa/complaints" });
  const row = await screen.findByRole("row", { name: /CMP-000006/ });
  expect(within(row).getByRole("link", { name: /View CAPA/ })).toBeInTheDocument();
  expect(within(row).queryByRole("button", { name: /Spawn CAPA/ })).toBeNull();
});

test("renders a calm no-access panel on a 403", async () => {
  server.use(
    http.get("/api/v1/complaints", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  renderWithProviders(<ComplaintsPage />, { route: "/capa/complaints" });
  expect(await screen.findByText(/don't have access to complaints/)).toBeInTheDocument();
});

test("no axe violations", async () => {
  const { container } = renderWithProviders(<ComplaintsPage />, { route: "/capa/complaints" });
  await screen.findByText("CMP-000007");
  expect(await axe(container)).toHaveNoViolations();
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm test -- src/features/capa/ComplaintsPage.test.tsx`
Expected: FAIL — cannot resolve `./ComplaintsPage`.

- [ ] **Step 3: Implement** `apps/web/src/features/capa/ComplaintForm.tsx`:

```tsx
import { Alert, Button, Group, Modal, Select, Stack, Textarea, TextInput } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import type { NcSeverity } from "../../lib/types";
import { useCreateComplaint } from "./mutations";

export function ComplaintForm({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  const m = useCreateComplaint();
  const [description, setDescription] = useState("");
  const [customer, setCustomer] = useState("");
  const [channel, setChannel] = useState("");
  const [severity, setSeverity] = useState<NcSeverity | null>(null);
  const [error, setError] = useState<string | null>(null);

  function reset() {
    setDescription("");
    setCustomer("");
    setChannel("");
    setSeverity(null);
    setError(null);
  }
  async function submit() {
    setError(null);
    try {
      await m.mutateAsync({
        description,
        customer: customer.trim() || undefined,
        channel: channel.trim() || undefined,
        severity: severity ?? undefined,
      });
      reset();
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not log the complaint.");
    }
  }
  return (
    <Modal opened={opened} onClose={onClose} title="Log a complaint">
      <Stack gap="sm">
        {error && <Alert color="red">{error}</Alert>}
        <Textarea
          label="Description"
          required
          autosize
          minRows={3}
          value={description}
          onChange={(e) => setDescription(e.currentTarget.value)}
        />
        <TextInput label="Customer" value={customer} onChange={(e) => setCustomer(e.currentTarget.value)} />
        <TextInput
          label="Channel"
          placeholder="email, phone, portal…"
          value={channel}
          onChange={(e) => setChannel(e.currentTarget.value)}
        />
        <Select
          label="Severity"
          placeholder="Optional"
          clearable
          value={severity}
          onChange={(v) => setSeverity(v as NcSeverity | null)}
          data={["Critical", "Major", "Minor"]}
          comboboxProps={{ keepMounted: false }}
        />
        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} loading={m.isPending} disabled={description.trim().length === 0}>
            Log complaint
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
```

- [ ] **Step 4: Implement** `apps/web/src/features/capa/ComplaintsPage.tsx`:

```tsx
import { Alert, Anchor, Button, Container, Group, Loader, Table, Text, Title } from "@mantine/core";
import { useState } from "react";
import { Link } from "react-router-dom";
import { usePermissions } from "../../app/shell/usePermissions";
import { SEVERITY_LABEL } from "./columns";
import { ComplaintForm } from "./ComplaintForm";
import { useComplaints } from "./hooks";
import { useSpawnCapa } from "./mutations";

export function ComplaintsPage() {
  const { data, isLoading, isError, forbidden } = useComplaints();
  const { can } = usePermissions();
  const spawn = useSpawnCapa();
  const [formOpen, setFormOpen] = useState(false);

  if (forbidden) {
    return (
      <Container size="lg" py="md">
        <Title order={3} mb="md">
          Complaints
        </Title>
        <Alert color="gray" title="No access">
          You don't have access to complaints. They're available to roles holding <code>record.read</code>.
        </Alert>
      </Container>
    );
  }
  if (isLoading) {
    return (
      <Container size="lg" py="md">
        <Loader />
      </Container>
    );
  }
  if (isError) {
    return (
      <Container size="lg" py="md">
        <Title order={3} mb="md">
          Complaints
        </Title>
        <Alert color="red" title="Couldn't load complaints">
          Please try again.
        </Alert>
      </Container>
    );
  }

  const rows = data ?? [];
  return (
    <Container size="lg" py="md">
      <Group justify="space-between" mb="md">
        <Title order={3}>Complaints</Title>
        {can("record.create") && <Button onClick={() => setFormOpen(true)}>＋ Log complaint</Button>}
      </Group>
      {spawn.isError && (
        <Alert color="red" mb="sm">
          Could not spawn a CAPA. Please try again.
        </Alert>
      )}
      {rows.length === 0 ? (
        <Text c="dimmed">No complaints logged yet.</Text>
      ) : (
        <Table striped highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Identifier</Table.Th>
              <Table.Th>Customer</Table.Th>
              <Table.Th>Channel</Table.Th>
              <Table.Th>Severity</Table.Th>
              <Table.Th>Description</Table.Th>
              <Table.Th>CAPA</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((c) => (
              <Table.Tr key={c.id}>
                <Table.Td>{c.identifier ?? "—"}</Table.Td>
                <Table.Td>{c.customer ?? "—"}</Table.Td>
                <Table.Td>{c.channel ?? "—"}</Table.Td>
                <Table.Td>{c.severity ? SEVERITY_LABEL[c.severity] : "—"}</Table.Td>
                <Table.Td>
                  <Text lineClamp={2}>{c.description}</Text>
                </Table.Td>
                <Table.Td>
                  {c.spawned_capa_id ? (
                    <Anchor component={Link} to="/capa">
                      View CAPA
                    </Anchor>
                  ) : can("capa.create") ? (
                    <Button
                      size="xs"
                      variant="light"
                      loading={spawn.isPending && spawn.variables?.complaintId === c.id}
                      onClick={() => spawn.mutate({ complaintId: c.id, severity: c.severity ?? undefined })}
                    >
                      Spawn CAPA
                    </Button>
                  ) : (
                    <Text c="dimmed" size="sm">
                      —
                    </Text>
                  )}
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
      <ComplaintForm opened={formOpen} onClose={() => setFormOpen(false)} />
    </Container>
  );
}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `npm test -- src/features/capa/ComplaintsPage.test.tsx`
Expected: PASS (7 tests).
Note: if the modal-submit assertion (`/^Log complaint$/`) is ambiguous with the header button, prefer scoping to the dialog: `within(screen.getByRole("dialog")).getByRole("button", { name: /Log complaint/ })`. Adjust the test if needed — do not weaken the assertion.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/features/capa/ComplaintForm.tsx apps/web/src/features/capa/ComplaintsPage.tsx apps/web/src/features/capa/ComplaintsPage.test.tsx
git commit -m "feat(s-web-7c): Complaints page — list, log, idempotent spawn-CAPA"
```

---

## Task 6: NCR surface (form + disposition modal + page)

**Files:**
- Create: `apps/web/src/features/capa/NcrForm.tsx`
- Create: `apps/web/src/features/capa/DispositionModal.tsx`
- Create: `apps/web/src/features/capa/NcrsPage.tsx`
- Test: `apps/web/src/features/capa/NcrsPage.test.tsx`

- [ ] **Step 1: Write the failing test** `apps/web/src/features/capa/NcrsPage.test.tsx`:

```tsx
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { NcrsPage } from "./NcrsPage";

function grant(keys: string[]) {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: keys.map((key) => ({ key, effect: "ALLOW", source: null })),
      }),
    ),
  );
}

test("lists NCRs from {data} with friendly source labels", async () => {
  renderWithProviders(<NcrsPage />, { route: "/capa/ncrs" });
  expect(await screen.findByText("NCR-000052")).toBeInTheDocument();
  const row = screen.getByRole("row", { name: /NCR-000052/ });
  expect(within(row).getByText("Process")).toBeInTheDocument();
});

test("hides 'Raise NCR' without ncr.create; shows + opens it with the key", async () => {
  grant(["ncr.create"]);
  const u = userEvent.setup();
  renderWithProviders(<NcrsPage />, { route: "/capa/ncrs" });
  await u.click(await screen.findByRole("button", { name: /Raise NCR/ }));
  expect(await screen.findByLabelText(/^Source/)).toBeInTheDocument();
});

test("a disposed NCR shows its disposition read-only (no action button)", async () => {
  grant(["ncr.record_correction"]);
  renderWithProviders(<NcrsPage />, { route: "/capa/ncrs" });
  const row = await screen.findByRole("row", { name: /NCR-000049/ });
  expect(within(row).getByText("Rework")).toBeInTheDocument();
  expect(within(row).queryByRole("button", { name: /Record disposition/ })).toBeNull();
});

test("records a disposition (PATCH) for an undisposed NCR", async () => {
  grant(["ncr.record_correction"]);
  let patched = false;
  server.use(
    http.patch("/api/v1/ncrs/:id/disposition", () => {
      patched = true;
      return HttpResponse.json({
        id: "nc000001-0001-0001-0001-000000000001", identifier: "NCR-000052", source: "process",
        description: "x", severity: "Major", process_id: null, disposition: "scrap",
        disposition_authorized_by: null, disposition_notes: null, disposed_at: "2026-06-09T00:00:00+00:00",
        created_at: "2026-06-03T09:00:00+00:00",
      });
    }),
  );
  const u = userEvent.setup();
  renderWithProviders(<NcrsPage />, { route: "/capa/ncrs" });
  const row = await screen.findByRole("row", { name: /NCR-000052/ });
  await u.click(within(row).getByRole("button", { name: /Record disposition/ }));
  const dialog = screen.getByRole("dialog");
  const [dispInput] = within(dialog).getAllByLabelText(/Disposition/);
  await u.click(dispInput!);
  await u.click(await screen.findByRole("option", { name: "Scrap" }));
  await u.click(within(dialog).getByRole("button", { name: /Record disposition/ }));
  await waitFor(() => expect(patched).toBe(true));
});

test("a one-shot 409 (already dispositioned) is surfaced calmly", async () => {
  grant(["ncr.record_correction"]);
  server.use(
    http.patch("/api/v1/ncrs/:id/disposition", () =>
      HttpResponse.json({ code: "ncr_already_dispositioned", title: "Already dispositioned" }, { status: 409 }),
    ),
  );
  const u = userEvent.setup();
  renderWithProviders(<NcrsPage />, { route: "/capa/ncrs" });
  const row = await screen.findByRole("row", { name: /NCR-000052/ });
  await u.click(within(row).getByRole("button", { name: /Record disposition/ }));
  const dialog = screen.getByRole("dialog");
  const [dispInput] = within(dialog).getAllByLabelText(/Disposition/);
  await u.click(dispInput!);
  await u.click(await screen.findByRole("option", { name: "Scrap" }));
  await u.click(within(dialog).getByRole("button", { name: /Record disposition/ }));
  expect(await screen.findByText(/Already dispositioned/)).toBeInTheDocument();
});

test("renders a calm no-access panel on a 403", async () => {
  server.use(
    http.get("/api/v1/ncrs", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  renderWithProviders(<NcrsPage />, { route: "/capa/ncrs" });
  expect(await screen.findByText(/don't have access to NCRs/)).toBeInTheDocument();
});

test("no axe violations", async () => {
  const { container } = renderWithProviders(<NcrsPage />, { route: "/capa/ncrs" });
  await screen.findByText("NCR-000052");
  expect(await axe(container)).toHaveNoViolations();
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm test -- src/features/capa/NcrsPage.test.tsx`
Expected: FAIL — cannot resolve `./NcrsPage`.

- [ ] **Step 3: Implement** `apps/web/src/features/capa/NcrForm.tsx`:

```tsx
import { Alert, Button, Group, Modal, Select, Stack, Textarea } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import type { NcrSource, NcSeverity } from "../../lib/types";
import { NCR_SOURCE_LABEL, NCR_SOURCES } from "./intake";
import { useCreateNcr } from "./mutations";

export function NcrForm({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  const m = useCreateNcr();
  const [source, setSource] = useState<NcrSource | null>(null);
  const [severity, setSeverity] = useState<NcSeverity | null>(null);
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);

  function reset() {
    setSource(null);
    setSeverity(null);
    setDescription("");
    setError(null);
  }
  async function submit() {
    setError(null);
    if (!source || !severity) return;
    try {
      await m.mutateAsync({ source, severity, description });
      reset();
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not raise the NCR.");
    }
  }
  return (
    <Modal opened={opened} onClose={onClose} title="Raise an NCR">
      <Stack gap="sm">
        {error && <Alert color="red">{error}</Alert>}
        <Select
          label="Source"
          required
          placeholder="Pick a source"
          value={source}
          onChange={(v) => setSource(v as NcrSource | null)}
          data={NCR_SOURCES.map((s) => ({ value: s, label: NCR_SOURCE_LABEL[s] }))}
          comboboxProps={{ keepMounted: false }}
        />
        <Select
          label="Severity"
          required
          placeholder="Pick a severity"
          value={severity}
          onChange={(v) => setSeverity(v as NcSeverity | null)}
          data={["Critical", "Major", "Minor"]}
          comboboxProps={{ keepMounted: false }}
        />
        <Textarea
          label="Description"
          required
          autosize
          minRows={3}
          value={description}
          onChange={(e) => setDescription(e.currentTarget.value)}
        />
        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={() => void submit()}
            loading={m.isPending}
            disabled={!source || !severity || description.trim().length === 0}
          >
            Raise NCR
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
```

- [ ] **Step 4: Implement** `apps/web/src/features/capa/DispositionModal.tsx`:

```tsx
import { Alert, Button, Group, Modal, Select, Stack, Textarea } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import type { Ncr, NcrDisposition } from "../../lib/types";
import { DISPOSITION_LABEL, DISPOSITIONS } from "./intake";
import { useNcrDisposition } from "./mutations";

export function DispositionModal({
  ncr,
  opened,
  onClose,
}: {
  ncr: Ncr;
  opened: boolean;
  onClose: () => void;
}) {
  const m = useNcrDisposition(ncr.id);
  const [disposition, setDisposition] = useState<NcrDisposition | null>(null);
  const [notes, setNotes] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setError(null);
    if (!disposition) return;
    try {
      await m.mutateAsync({ disposition, notes: notes.trim() || undefined });
      setDisposition(null);
      setNotes("");
      onClose();
    } catch (e) {
      // 409 ncr_already_dispositioned (a race) lands here — surface the server message calmly.
      setError(e instanceof ApiError ? e.message : "Could not record the disposition.");
    }
  }
  return (
    <Modal opened={opened} onClose={onClose} title={`Record disposition — ${ncr.identifier}`}>
      <Stack gap="sm">
        {error && <Alert color="orange">{error}</Alert>}
        <Select
          label="Disposition (ISO 9001 §8.7)"
          required
          placeholder="Pick a disposition"
          value={disposition}
          onChange={(v) => setDisposition(v as NcrDisposition | null)}
          data={DISPOSITIONS.map((d) => ({ value: d, label: DISPOSITION_LABEL[d] }))}
          comboboxProps={{ keepMounted: false }}
        />
        <Textarea
          label="Notes"
          autosize
          minRows={2}
          value={notes}
          onChange={(e) => setNotes(e.currentTarget.value)}
        />
        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} loading={m.isPending} disabled={!disposition}>
            Record disposition
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
```

- [ ] **Step 5: Implement** `apps/web/src/features/capa/NcrsPage.tsx`:

```tsx
import { Alert, Badge, Button, Container, Group, Loader, Table, Text, Title } from "@mantine/core";
import { useState } from "react";
import { usePermissions } from "../../app/shell/usePermissions";
import type { Ncr } from "../../lib/types";
import { SEVERITY_LABEL } from "./columns";
import { DispositionModal } from "./DispositionModal";
import { useNcrs } from "./hooks";
import { DISPOSITION_LABEL, NCR_SOURCE_LABEL } from "./intake";
import { NcrForm } from "./NcrForm";

export function NcrsPage() {
  const { data, isLoading, isError, forbidden } = useNcrs();
  const { can } = usePermissions();
  const [formOpen, setFormOpen] = useState(false);
  const [disposeNcr, setDisposeNcr] = useState<Ncr | null>(null);

  if (forbidden) {
    return (
      <Container size="lg" py="md">
        <Title order={3} mb="md">
          Nonconforming Output (NCR)
        </Title>
        <Alert color="gray" title="No access">
          You don't have access to NCRs. They're available to roles holding <code>ncr.read</code>.
        </Alert>
      </Container>
    );
  }
  if (isLoading) {
    return (
      <Container size="lg" py="md">
        <Loader />
      </Container>
    );
  }
  if (isError) {
    return (
      <Container size="lg" py="md">
        <Title order={3} mb="md">
          Nonconforming Output (NCR)
        </Title>
        <Alert color="red" title="Couldn't load NCRs">
          Please try again.
        </Alert>
      </Container>
    );
  }

  const rows = data ?? [];
  return (
    <Container size="lg" py="md">
      <Group justify="space-between" mb="md">
        <Title order={3}>Nonconforming Output (NCR)</Title>
        {can("ncr.create") && <Button onClick={() => setFormOpen(true)}>＋ Raise NCR</Button>}
      </Group>
      {rows.length === 0 ? (
        <Text c="dimmed">No NCRs raised yet.</Text>
      ) : (
        <Table striped highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Identifier</Table.Th>
              <Table.Th>Source</Table.Th>
              <Table.Th>Severity</Table.Th>
              <Table.Th>Description</Table.Th>
              <Table.Th>Disposition</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((n) => (
              <Table.Tr key={n.id}>
                <Table.Td>{n.identifier}</Table.Td>
                <Table.Td>{NCR_SOURCE_LABEL[n.source]}</Table.Td>
                <Table.Td>{SEVERITY_LABEL[n.severity]}</Table.Td>
                <Table.Td>
                  <Text lineClamp={2}>{n.description}</Text>
                </Table.Td>
                <Table.Td>
                  {n.disposition ? (
                    <Group gap="xs">
                      <Badge variant="light" color="gray">
                        {DISPOSITION_LABEL[n.disposition]}
                      </Badge>
                      {n.disposition_notes && (
                        <Text size="sm" c="dimmed">
                          {n.disposition_notes}
                        </Text>
                      )}
                    </Group>
                  ) : can("ncr.record_correction") ? (
                    <Button size="xs" variant="light" onClick={() => setDisposeNcr(n)}>
                      Record disposition
                    </Button>
                  ) : (
                    <Text c="dimmed" size="sm">
                      Pending
                    </Text>
                  )}
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
      <NcrForm opened={formOpen} onClose={() => setFormOpen(false)} />
      {disposeNcr && <DispositionModal ncr={disposeNcr} opened onClose={() => setDisposeNcr(null)} />}
    </Container>
  );
}
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `npm test -- src/features/capa/NcrsPage.test.tsx`
Expected: PASS (7 tests).

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/features/capa/NcrForm.tsx apps/web/src/features/capa/DispositionModal.tsx apps/web/src/features/capa/NcrsPage.tsx apps/web/src/features/capa/NcrsPage.test.tsx
git commit -m "feat(s-web-7c): NCR page — list, raise, one-shot ISO 8.7 disposition"
```

---

## Task 7: Wire the nested route in App.tsx

**Files:**
- Modify: `apps/web/src/App.tsx`
- Test: `apps/web/src/features/capa/CapaRouting.test.tsx`

- [ ] **Step 1: Write the failing test** `apps/web/src/features/capa/CapaRouting.test.tsx` (a real-page wiring test through the layout):

```tsx
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { expect, test } from "vitest";
import { renderWithProviders } from "../../test/render";
import { CapaBoardPage } from "./CapaBoardPage";
import { CapaLayout } from "./CapaLayout";
import { ComplaintsPage } from "./ComplaintsPage";
import { NcrsPage } from "./NcrsPage";

function tree() {
  return (
    <Routes>
      <Route path="capa" element={<CapaLayout />}>
        <Route index element={<CapaBoardPage />} />
        <Route path="complaints" element={<ComplaintsPage />} />
        <Route path="ncrs" element={<NcrsPage />} />
      </Route>
    </Routes>
  );
}

test("navigates board → complaints → ncrs through the tab bar", async () => {
  const u = userEvent.setup();
  renderWithProviders(tree(), { route: "/capa" });
  // the board face (its own title) renders at the index route
  expect(await screen.findByText("Nonconformity & CAPA")).toBeInTheDocument();
  await u.click(screen.getByRole("tab", { name: "Complaints" }));
  expect(await screen.findByText("CMP-000007")).toBeInTheDocument();
  await u.click(screen.getByRole("tab", { name: "NCRs" }));
  expect(await screen.findByText("NCR-000052")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm test -- src/features/capa/CapaRouting.test.tsx`
Expected: FAIL — the tree compiles, but the test should pass once the components exist (they do, from Tasks 4-6). If it already passes, that's fine — it's the regression backstop for the App.tsx wiring. Proceed to wire App.tsx so the real app uses the same structure.

- [ ] **Step 3: Wire `App.tsx`.** Open `apps/web/src/App.tsx`. Find the imports for the CAPA page and the standalone `/capa` route (line ~114: `<Route path="capa" element={<CapaBoardPage />} />`).

Replace the single CAPA-page import with the three:

```tsx
import { CapaBoardPage } from "./features/capa/CapaBoardPage";
import { CapaLayout } from "./features/capa/CapaLayout";
import { ComplaintsPage } from "./features/capa/ComplaintsPage";
import { NcrsPage } from "./features/capa/NcrsPage";
```

(Keep the existing `CapaBoardPage` import path; just add the other three lines. If the existing import groups differently, match the surrounding style.)

Replace the standalone route:

```tsx
        <Route path="capa" element={<CapaBoardPage />} />
```

with the nested layout route:

```tsx
        <Route path="capa" element={<CapaLayout />}>
          <Route index element={<CapaBoardPage />} />
          <Route path="complaints" element={<ComplaintsPage />} />
          <Route path="ncrs" element={<NcrsPage />} />
        </Route>
```

- [ ] **Step 4: Run the routing test + typecheck to verify**

Run: `npm test -- src/features/capa/CapaRouting.test.tsx`
Expected: PASS.
Run: `npm run typecheck`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/App.tsx apps/web/src/features/capa/CapaRouting.test.tsx
git commit -m "feat(s-web-7c): nest /capa under CapaLayout (board index + complaints + ncrs)"
```

---

## Task 8: Full gate + docs

**Files:**
- Modify: `docs/slice-history.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Run the full web gate** (the `/check-web` equivalent — lint + strict tsc + build + the whole vitest suite):

Run (from `apps/web`):
```
npm run lint
npm run typecheck
npm test
npm run build
```
Expected: all green. The full `npm test` run catches cross-file drift (the `noUncheckedIndexedAccess` lesson) that the per-file runs miss. Fix any failures before continuing — do not proceed with a red gate.

- [ ] **Step 2: Add the slice entry** to `docs/slice-history.md` (mirror the existing S-web-7b entry's depth + placement — newest first in the web-track section):

> **S-web-7c — Complaint & NCR intake (#TBD)** — the 3rd PR of the S-web-7 epic; **front-end only** (no
> migration/key/contract) over the already-built `/complaints*` + `/ncrs*` surface. Tabbed sub-routes
> under `/capa` via a thin `CapaLayout` (Board/Complaints/NCRs tab strip + `<Outlet/>`); the shipped
> `CapaBoardPage` stays **byte-identical** (board index route, its own title). Complaints: list +
> log-modal (`record.create`) + **idempotent spawn-CAPA** (the row's `spawned_capa_id` drives a
> "Spawn CAPA"→"View CAPA" flip; 201/200 both resolve, never reading the HTTP status `api.send`
> discards). NCRs: list + raise-modal (`ncr.create`) + **one-shot ISO 8.7 disposition** (a disposed
> row is read-only; `409 ncr_already_dispositioned` surfaced calmly). Per-key calm-403: the `demo`
> admin holds **none** of these keys (both tabs calm-403, like the board); `ncr.create` /
> `ncr.record_correction` are seeded but **granted to no role** (SYSTEM-override-only in v1). Fixtures
> pinned to the real `_complaint`/`_ncr` serializers. (Replace `#TBD` with the PR number once opened.)

- [ ] **Step 3: Add a Recent-learnings entry** to `CLAUDE.md` (top of the "Recent learnings" list, newest first; cap ~12 — demote the oldest if needed) and update **Current status**:

Learnings entry:
> - 2026-06-09 — **S-web-7c (Complaint & NCR intake) is FRONT-END ONLY** (no migration/key/contract) —
>   surfaces the already-built `/complaints*` + `/ncrs*` surface as **tabbed sub-routes under `/capa`**
>   (a thin `CapaLayout` = tab strip + `<Outlet/>`; `CapaBoardPage` **byte-identical**, board index +
>   its own title). **Per-key gating diverges from the board:** complaints ride `record.read` /
>   `record.create` / spawn=`capa.create`; NCRs ride `ncr.read` / `ncr.create` / `ncr.record_correction`
>   — the **`demo` admin holds NONE** (both tabs calm-403), and **`ncr.create` / `ncr.record_correction`
>   are seeded but granted to NO role** (SYSTEM-override-only in v1). **Spawn idempotency is surfaced as
>   STATE, not the HTTP status:** `api.send` discards the status (201-new vs 200-replay), so the row's
>   `spawned_capa_id` drives the "Spawn CAPA"→"View CAPA" flip; a replay just resolves. Disposition is
>   one-shot → a disposed row is read-only + `409 ncr_already_dispositioned` is calm. Fixtures pinned to
>   the real `_complaint`/`_ncr` serializers (NOT the mockup). Smoke needs SYSTEM overrides of all six
>   keys on `demo`.

Current-status update: mark **S-web-7c ✅** in the epic line; note the remaining S-web-7d (audits/findings).

- [ ] **Step 4: Commit**

```bash
git add docs/slice-history.md CLAUDE.md
git commit -m "docs(s-web-7c): slice-history + CLAUDE learnings for Complaint & NCR intake"
```

---

## Post-plan (handled outside the task loop)

After all tasks: run the `diff-critic` agent on the branch diff (per the project's pre-PR rhythm), address findings, then a pre-merge live smoke (rebuild api+web images; grant `demo` SYSTEM overrides of `record.read record.create capa.create ncr.read ncr.create ncr.record_correction` on org `AHT`; drive log-complaint → spawn-CAPA → board, and raise-NCR → disposition), then open the PR.

---

## Self-Review

**1. Spec coverage** (each spec section → task):
- §2 tabbed front door / `CapaLayout` / board byte-identical → Task 4 (+ Task 7 wiring, + Task 8 verifies via build).
- §3.1/3.2 reads (`useComplaints`/`useNcrs`, `{data}`, forbidden) → Task 2.
- §3.3 enums / §7 types → Task 1.
- §4.2 mutations (create/spawn/create-ncr/disposition) → Task 3.
- §5.1 spawn idempotency (state-driven flip, no status read) → Task 5.
- §5.2 disposition one-shot (read-only disposed row, 409 calm) → Task 6.
- §3.4 / §6.2 per-key gating + calm-403 → Tasks 5 & 6 (gates + 403 tests).
- §6.6 XSS-safe text → Mantine `Text` (description cells) in Tasks 5 & 6; the lineClamp `Text` renders literally. (No `dangerouslySetInnerHTML` anywhere — confirmed by inspection; no separate test needed beyond the rendered-as-text assertions, but add one if a reviewer wants it.)
- §6.5 filters/tiles DEFERRED → not built (correct).
- §8 testing (tab nav, lists, gated create, spawn flip, disposition one-shot+409, calm-403, axe, full check-web) → Tasks 4-8.
- §9 out-of-scope (no detail drawer, no upload, no board cross-counts, no backend change) → respected.

**2. Placeholder scan:** the only `#TBD` is the PR number in the slice-history entry (filled when the PR opens) — acceptable. No "add error handling"/"similar to"/"write tests for the above" — all code is complete.

**3. Type/name consistency:** `useComplaints`/`useNcrs` (Task 2) match their uses in Tasks 5/6; `useCreateComplaint`/`useSpawnCapa`/`useCreateNcr`/`useNcrDisposition` (Task 3) match Tasks 5/6; `Complaint`/`Ncr`/`NcrSource`/`NcrDisposition`/`ComplaintCreateBody`/`SpawnCapaBody`/`NcrCreateBody`/`NcrDispositionBody` (Task 1) match all consumers; `NCR_SOURCES`/`NCR_SOURCE_LABEL`/`DISPOSITIONS`/`DISPOSITION_LABEL` (Task 1) match Tasks 6's forms; `SEVERITY_LABEL` reused from the existing `columns.ts`; `spawn.variables?.complaintId` matches the `useSpawnCapa` variables shape `{complaintId, severity?}`.

**Note for the implementer:** if a `getByRole("button", { name: /Log complaint|Record disposition/ })` is ambiguous between a header/row button and a modal submit button, scope the query to `within(screen.getByRole("dialog"))` rather than weakening the matcher.
