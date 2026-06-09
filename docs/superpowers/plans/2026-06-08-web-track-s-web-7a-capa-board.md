# S-web-7a — CAPA read spine (board + read-only drawer) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended)
> or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax
> for tracking.

**Goal:** Surface the CAPA domain in the SPA as a read-only kanban **board** (columns = lifecycle state) +
an in-context **detail drawer** (closed-loop stage timeline + close-gate stepper), over the existing
`GET /capas` + `GET /capas/{id}`, with one thin backend serializer enrichment so cards carry a human title.

**Architecture:** Front-end is a new `apps/web/src/features/capa/` feature (page + drawer + small focused
components + a `hooks.ts` + a pure `columns.ts`), wired into the react-router table (`App.tsx`) and the
nav (`LeftRail.tsx`), mirroring the S-web-6 compliance feature (calm-403 hook), the shell `DetailDrawer`,
`usePermissions`/`useUserDirectory` primitives, and the MSW+vitest+jest-axe test rig. The only backend
change is enriching the `_capa` serializer (`title`/`created_at` on list+detail, `raised_by` on detail)
via **optional kwargs** (16 call sites stay untouched) plus the matching `Capa` schema fields in the
OpenAPI contract — no migration, no key, no endpoint.

**Tech Stack:** React/TS · Mantine · @tanstack/react-query · react-router-dom · MSW · vitest · jest-axe
(web). FastAPI · SQLAlchemy async · Pydantic (api). Redocly (contract).

**Spec:** `docs/superpowers/specs/2026-06-08-web-track-s-web-7-nc-capa-design.md` (esp. §3 verified shapes,
§4 the enrichment, §5 cross-cutting, §6 the 7a detail).

**Local gates (this native-Windows box):** web = `/check-web` (eslint + strict tsc + build + the full
vitest suite) runs locally and is the reliable front-end gate. The **api test suites are Linux-CI-only**
here — for Task 1, the local gate is `/check-api` (ruff/format/mypy-strict) + `/check-contracts`; the
integration test you write runs red→green in **CI**, not locally. Don't try to run `pytest` locally.

---

## File structure

**Backend (Task 1):**
- Modify `apps/api/src/easysynq_api/services/capa/repository.py` — `list_capas` selects title/created_at;
  add `get_capa_header`.
- Modify `apps/api/src/easysynq_api/api/capa.py` — `_capa` optional kwargs; list + detail callers pass them.
- Modify `packages/contracts/openapi.yaml` — add `title`/`created_at`/`raised_by` to the `Capa` schema.
- Test `apps/api/tests/integration/test_capa.py` — assert the new fields (runs in CI).

**Front-end (Tasks 2–10), all under `apps/web/src/`:**
- `features/capa/columns.ts` (+ `.test.ts`) — pure column model + label maps (Task 2).
- `lib/types.ts` — `Capa`, `CapaStage`, `CapaCloseState`, `CapaSource`, `NcSeverity` (Task 3).
- `features/capa/hooks.ts` (+ `.test.tsx`) — `useCapas`, `useCapa` (Task 3).
- `test/msw/handlers.ts` — `capaListFixture`, `capaDetailFixture`, `capaLoopDetailFixture` + default handlers (Task 3).
- `features/capa/ContentBlock.tsx` (+ `.test.tsx`) — generic free-form JSON renderer (Task 4).
- `features/capa/CapaTimeline.tsx` (+ `.test.tsx`) — the closed-loop thread (Task 5).
- `features/capa/CloseGateStepper.tsx` (+ `.test.tsx`) — the close-gate stepper (Task 6).
- `features/capa/CapaCard.tsx` (+ `.test.tsx`) — a kanban card (Task 7).
- `features/capa/CapaDrawer.tsx` (+ `.test.tsx`) — the read-only detail drawer (Task 8).
- `features/capa/CapaBoardPage.tsx` (+ `.test.tsx`) — the board page (Task 9).
- `app/shell/LeftRail.tsx` + `App.tsx` — nav + route (Task 10).

---

## Task 1: Backend — enrich the `_capa` serializer (title/created_at/raised_by) + contract

**Files:**
- Modify: `apps/api/src/easysynq_api/services/capa/repository.py:105-112` (`list_capas`); add `get_capa_header`.
- Modify: `apps/api/src/easysynq_api/api/capa.py:137-152` (`_capa`), `:273-279` (list), `:282-292` (detail).
- Modify: `packages/contracts/openapi.yaml` (`Capa` schema, ~line 5603).
- Test: `apps/api/tests/integration/test_capa.py` (new test).

- [ ] **Step 1: Write the failing integration test**

Append to `apps/api/tests/integration/test_capa.py`. It reuses the file's existing helpers
(`_subject`/`_grant`/`_CAPA_KEYS`/`_auth`, defined at the top of the file) and the `app_client` +
`token_factory` fixtures — the exact pattern of `test_raise_capa_then_containment` (line 94):

```python
async def test_capa_list_and_detail_carry_title_created_at_raised_by(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("capa")
    await _grant(subject, _CAPA_KEYS)
    h = _auth(token_factory, subject)

    raised = (
        await app_client.post(
            "/api/v1/capas",
            headers=h,
            json={"title": "Torque wrench miscalibration", "severity": "Minor"},
        )
    ).json()
    capa_id = raised["id"]

    # list row carries title + created_at (raised_by is detail-only → null on the list row)
    listing = (await app_client.get("/api/v1/capas", headers=h)).json()
    row = next(r for r in listing["data"] if r["id"] == capa_id)
    assert row["title"] == "Torque wrench miscalibration"
    assert row["created_at"] is not None
    assert row["raised_by"] is None

    # detail carries title + created_at + raised_by (the Raised stage's actor)
    detail = (await app_client.get(f"/api/v1/capas/{capa_id}", headers=h)).json()
    assert detail["title"] == "Torque wrench miscalibration"
    assert detail["created_at"] is not None
    assert detail["raised_by"] == detail["stages"][0]["created_by"]
```

- [ ] **Step 2: (CI-only) note expected failure**

The api suites can't run on this Windows box. In CI the `integration` job runs it red: `KeyError: 'title'`
(the serializer doesn't emit it yet). Locally, proceed — Steps 5–6 are the local gate.

- [ ] **Step 3: Enrich the repository query + add a header accessor**

In `apps/api/src/easysynq_api/services/capa/repository.py`, change `list_capas` to also select title +
created_at from the already-joined `DocumentedInformation`, and add a one-query header accessor for detail:

```python
async def list_capas(
    session: AsyncSession, org_id: uuid.UUID
) -> Sequence[tuple[Capa, str | None, str | None, datetime | None]]:
    rows = await session.execute(
        select(
            Capa,
            DocumentedInformation.identifier,
            DocumentedInformation.title,
            DocumentedInformation.created_at,
        )
        .join(DocumentedInformation, DocumentedInformation.id == Capa.id)
        .where(Capa.org_id == org_id)
        .order_by(DocumentedInformation.created_at.desc())
    )
    return [(c, ident, title, created) for c, ident, title, created in rows.all()]


async def get_capa_header(
    session: AsyncSession, capa_id: uuid.UUID
) -> tuple[str | None, str | None, datetime | None] | None:
    """(identifier, title, created_at) for a CAPA's record — one row, for the detail serializer."""
    row = (
        await session.execute(
            select(
                DocumentedInformation.identifier,
                DocumentedInformation.title,
                DocumentedInformation.created_at,
            ).where(DocumentedInformation.id == capa_id)
        )
    ).first()
    return (row[0], row[1], row[2]) if row else None
```

Add `from datetime import datetime` if not already imported at the top of `repository.py`.

- [ ] **Step 4: Add the optional kwargs to `_capa` and pass them from list + detail only**

In `apps/api/src/easysynq_api/api/capa.py`, replace the `_capa` serializer (137-152) with the keyword-only
enriched form (every existing call site keeps working — the new args default to `None`):

```python
def _capa(
    c: Capa,
    identifier: str | None,
    *,
    title: str | None = None,
    created_at: datetime | None = None,
    raised_by: str | None = None,
    stages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(c.id),
        "identifier": identifier,
        "title": title,
        "source": c.source.value,
        "severity": c.severity.value,
        "process_id": str(c.process_id) if c.process_id else None,
        "close_state": c.close_state.value,
        "cycle_marker": c.cycle_marker,
        "origin_finding_id": str(c.origin_finding_id) if c.origin_finding_id else None,
        "raised_by": raised_by,
        "created_at": created_at.isoformat() if created_at else None,
    }
    if stages is not None:
        out["stages"] = stages
    return out
```

Add `from datetime import datetime` to `capa.py`'s imports if absent.

Update the **list** endpoint (`list_capas_endpoint`, ~273) to pass the new tuple fields:

```python
@router.get("/capas")
async def list_capas_endpoint(
    caller: AppUser = Depends(_capa_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    rows = await capa_repo.list_capas(session, caller.org_id)
    return {"data": [_capa(c, ident, title=title, created_at=created) for c, ident, title, created in rows]}
```

Update the **detail** endpoint (`get_capa_endpoint`, ~282) to pass title/created_at + raised_by:

```python
@router.get("/capas/{capa_id}")
async def get_capa_endpoint(
    capa_id: uuid.UUID,
    caller: AppUser = Depends(_capa_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    capa = await capa_repo.get_capa(session, capa_id)
    if capa is None or capa.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="CAPA not found")
    stages = [_stage(s) for s in await capa_repo.list_capa_stages(session, capa_id)]
    header = await capa_repo.get_capa_header(session, capa_id)
    ident = header[0] if header else None
    raised_by = stages[0]["created_by"] if stages else None
    return _capa(
        capa,
        ident,
        title=header[1] if header else None,
        created_at=header[2] if header else None,
        raised_by=raised_by,
        stages=stages,
    )
```

> The write endpoints (containment/root-cause/.../close, list of 14) still call `_capa(capa, identifier)`
> — leave them exactly as-is; they correctly return `title=null` (the UI invalidates + refetches after a
> write in 7b; it never reads title from a write response). `get_identifier` stays for those callers.

- [ ] **Step 5: Add the three fields to the `Capa` schema in the contract**

In `packages/contracts/openapi.yaml`, the `Capa` schema (~5603). Add to its `properties` (keep them
nullable — they're null on write responses):

```yaml
        title: { type: [string, "null"] }
        created_at: { type: [string, "null"], format: date-time }
        raised_by: { type: [string, "null"], format: uuid }
```

- [ ] **Step 6: Run the local gates**

Run: `/check-api`  → Expected: ruff + format-check + mypy-strict all clean (the `datetime` import + the
new tuple unpacking type-check).
Run: `/check-contracts`  → Expected: redocly lint passes on `openapi.yaml`.

- [ ] **Step 7: Commit**

```bash
git add apps/api/src/easysynq_api/services/capa/repository.py apps/api/src/easysynq_api/api/capa.py packages/contracts/openapi.yaml apps/api/tests/integration/test_capa.py
git commit -m "feat(s-web-7a): enrich Capa serializer with title/created_at/raised_by"
```

---

## Task 2: CAPA column model + label maps (pure)

**Files:**
- Create: `apps/web/src/features/capa/columns.ts`
- Test: `apps/web/src/features/capa/columns.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// apps/web/src/features/capa/columns.test.ts
import { describe, expect, test } from "vitest";
import { CAPA_COLUMNS, columnKeyFor, SEVERITY_LABEL, SOURCE_LABEL } from "./columns";

describe("columnKeyFor", () => {
  test("maps each lifecycle state to its board column", () => {
    expect(columnKeyFor("Raised")).toBe("open");
    expect(columnKeyFor("Containment")).toBe("correction");
    expect(columnKeyFor("RootCause")).toBe("rootcause");
    expect(columnKeyFor("ActionPlan")).toBe("action");
    expect(columnKeyFor("Implement")).toBe("action"); // ActionPlan + Implement merge into one column
    expect(columnKeyFor("Verify")).toBe("verify");
    expect(columnKeyFor("Closed")).toBe("closed");
    expect(columnKeyFor("Rejected")).toBe("closed"); // Rejected folds into the Closed tail
  });
});

test("CAPA_COLUMNS lists the six columns in lifecycle order", () => {
  expect(CAPA_COLUMNS.map((c) => c.key)).toEqual([
    "open", "correction", "rootcause", "action", "verify", "closed",
  ]);
});

test("severity + source labels are humanized", () => {
  expect(SEVERITY_LABEL.Critical).toBe("Critical");
  expect(SOURCE_LABEL.review_output).toBe("Mgmt review");
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm test -- columns.test.ts` (in `apps/web`)
Expected: FAIL — `Cannot find module './columns'`.

- [ ] **Step 3: Write the implementation**

```ts
// apps/web/src/features/capa/columns.ts
import type { CapaCloseState, CapaSource, NcSeverity } from "../../lib/types";

export type CapaColumnKey =
  | "open" | "correction" | "rootcause" | "action" | "verify" | "closed";

export const CAPA_COLUMNS: { key: CapaColumnKey; label: string }[] = [
  { key: "open", label: "Open / NC" },
  { key: "correction", label: "Correction" },
  { key: "rootcause", label: "Root Cause" },
  { key: "action", label: "Action" },
  { key: "verify", label: "Verify" },
  { key: "closed", label: "Closed" },
];

const STATE_TO_COLUMN: Record<CapaCloseState, CapaColumnKey> = {
  Raised: "open",
  Containment: "correction",
  RootCause: "rootcause",
  ActionPlan: "action",
  Implement: "action",
  Verify: "verify",
  Closed: "closed",
  Rejected: "closed",
};

export function columnKeyFor(state: CapaCloseState): CapaColumnKey {
  return STATE_TO_COLUMN[state];
}

export const SEVERITY_LABEL: Record<NcSeverity, string> = {
  Critical: "Critical",
  Major: "Major",
  Minor: "Minor",
};

// Mantine badge color per severity (Critical red, Major orange, Minor gray).
export const SEVERITY_COLOR: Record<NcSeverity, string> = {
  Critical: "red",
  Major: "orange",
  Minor: "gray",
};

export const SOURCE_LABEL: Record<CapaSource, string> = {
  audit: "Audit",
  process: "Process",
  complaint: "Complaint",
  review_output: "Mgmt review",
};
```

- [ ] **Step 4: Run it to verify it passes**

Run: `npm test -- columns.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/capa/columns.ts apps/web/src/features/capa/columns.test.ts
git commit -m "feat(s-web-7a): CAPA board column model + label maps"
```

---

## Task 3: Types + data hooks (`useCapas`, `useCapa`) + MSW fixtures

**Files:**
- Modify: `apps/web/src/lib/types.ts` (append a CAPA section)
- Create: `apps/web/src/features/capa/hooks.ts`
- Test: `apps/web/src/features/capa/hooks.test.tsx`
- Modify: `apps/web/src/test/msw/handlers.ts` (fixtures + default handlers)

- [ ] **Step 1: Add the CAPA types**

Append to `apps/web/src/lib/types.ts`:

```ts
// ---- S-web-7 (Nonconformity & CAPA) -----------------------------------------------------
// GET /capas (list, {data}) · GET /capas/{id} (detail, + stages[]). Pinned to api/capa.py:_capa /
// _stage. The list omits stages; raised_by is null on the list row (detail-only). content_block is
// FREE-FORM per stage (no fixed schema in v1) → render generically, never typed fields.
export type NcSeverity = "Critical" | "Major" | "Minor";
export type CapaSource = "audit" | "process" | "complaint" | "review_output";
export type CapaCloseState =
  | "Raised" | "Containment" | "RootCause" | "ActionPlan" | "Implement" | "Verify"
  | "Closed" | "Rejected";

export interface Capa {
  id: string;
  identifier: string | null; // the record identifier, e.g. "REC-000031"
  title: string | null;
  source: CapaSource;
  severity: NcSeverity;
  process_id: string | null;
  close_state: CapaCloseState;
  cycle_marker: number; // effectiveness-loop counter; >0 => the Verify→RootCause loop ran
  origin_finding_id: string | null; // NULL in v1
  raised_by: string | null; // detail-only (the Raised stage's actor); null on list rows
  created_at: string | null;
  stages?: CapaStage[]; // detail-only
}

export interface CapaStage {
  id: string;
  stage: CapaCloseState;
  content_block: Record<string, unknown>; // free-form
  cycle_marker: number;
  created_by: string; // an app_user id; resolve via the user directory
  created_at: string;
}

export interface CapaList {
  data: Capa[];
}
```

- [ ] **Step 2: Write the failing hooks test**

```tsx
// apps/web/src/features/capa/hooks.test.tsx
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { expect, test } from "vitest";
import { AuthContext } from "../../lib/auth";
import { TEST_AUTH } from "../../test/render";
import { server } from "../../test/msw/server";
import { useCapa, useCapas } from "./hooks";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <AuthContext.Provider value={TEST_AUTH}>{children}</AuthContext.Provider>
    </QueryClientProvider>
  );
}

test("useCapas returns the {data} rows", async () => {
  const { result } = renderHook(() => useCapas(), { wrapper });
  await waitFor(() => expect(result.current.data).toBeDefined());
  expect(result.current.data!.length).toBeGreaterThan(0);
  expect(result.current.forbidden).toBe(false);
});

test("useCapas surfaces a 403 as forbidden (not an error throw)", async () => {
  server.use(
    http.get("/api/v1/capas", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  const { result } = renderHook(() => useCapas(), { wrapper });
  await waitFor(() => expect(result.current.forbidden).toBe(true));
});

test("useCapa returns the detail with stages", async () => {
  const { result } = renderHook(() => useCapa("ca000001-0001-0001-0001-000000000001"), { wrapper });
  await waitFor(() => expect(result.current.data).toBeDefined());
  expect(result.current.data!.stages!.length).toBeGreaterThan(0);
});

test("useCapa is disabled when id is null", () => {
  const { result } = renderHook(() => useCapa(null), { wrapper });
  expect(result.current.fetchStatus).toBe("idle");
});
```

- [ ] **Step 3: Run it to verify it fails**

Run: `npm test -- hooks.test.tsx` (from `apps/web`)
Expected: FAIL — `Cannot find module './hooks'` (and no `/capas` handler yet).

- [ ] **Step 4: Implement the hooks**

```ts
// apps/web/src/features/capa/hooks.ts
import { useQuery } from "@tanstack/react-query";
import { ApiError, useApi } from "../../lib/api";
import type { Capa, CapaList } from "../../lib/types";

// GET /capas is gated capa.read; the demo admin holds NO capa.* (S-web-6 calm-403 case, NOT S-ing-4b).
// Surface a `forbidden` flag so the page renders a calm no-access panel. retry:false — don't hammer a
// permission denial.
export function useCapas() {
  const api = useApi();
  const query = useQuery({
    queryKey: ["capas"],
    queryFn: async () => (await api.get<CapaList>("/api/v1/capas")).data,
    retry: false,
  });
  const forbidden = query.error instanceof ApiError && query.error.status === 403;
  return { ...query, forbidden };
}

// GET /capas/{id} — the detail (+ stages[]). Disabled until a card is selected.
export function useCapa(id: string | null) {
  const api = useApi();
  return useQuery({
    queryKey: ["capa", id],
    queryFn: () => api.get<Capa>(`/api/v1/capas/${id}`),
    enabled: id !== null,
  });
}
```

- [ ] **Step 5: Add the MSW fixtures + default handlers**

In `apps/web/src/test/msw/handlers.ts`, add fixtures near the other `export const …Fixture` blocks (e.g.
after `complianceFixture`, ~line 422). Note `created_by` ids: `bbbb1111-…` is in `directoryFixture`
(resolves to a name), `bbbb9999-…` is NOT (the degrade-to-id case in Task 5):

```ts
export const capaListFixture = {
  data: [
    { id: "ca000001-0001-0001-0001-000000000001", identifier: "REC-000031", title: "Supplier re-evaluation overdue for 2 vendors", source: "audit", severity: "Major", process_id: "pr000001-0001-0001-0001-000000000001", close_state: "RootCause", cycle_marker: 0, origin_finding_id: null, raised_by: null, created_at: "2026-05-20T09:00:00+00:00" },
    { id: "ca000002-0002-0002-0002-000000000002", identifier: "REC-000034", title: "Delivered batch missing CoA documents", source: "complaint", severity: "Critical", process_id: null, close_state: "Containment", cycle_marker: 0, origin_finding_id: null, raised_by: null, created_at: "2026-05-28T09:00:00+00:00" },
    { id: "ca000003-0003-0003-0003-000000000003", identifier: "REC-000035", title: "Calibration label missing on torque wrench", source: "process", severity: "Minor", process_id: null, close_state: "Raised", cycle_marker: 0, origin_finding_id: null, raised_by: null, created_at: "2026-06-01T09:00:00+00:00" },
    { id: "ca000004-0004-0004-0004-000000000004", identifier: "REC-000028", title: "Scrap-rate spike on Line 2", source: "process", severity: "Major", process_id: null, close_state: "Implement", cycle_marker: 0, origin_finding_id: null, raised_by: null, created_at: "2026-05-15T09:00:00+00:00" },
    { id: "ca000005-0005-0005-0005-000000000005", identifier: "REC-000025", title: "Recurring late deliveries", source: "audit", severity: "Major", process_id: null, close_state: "Verify", cycle_marker: 1, origin_finding_id: null, raised_by: null, created_at: "2026-05-10T09:00:00+00:00" },
    { id: "ca000006-0006-0006-0006-000000000006", identifier: "REC-000019", title: "Document control numbering gap", source: "audit", severity: "Minor", process_id: null, close_state: "Closed", cycle_marker: 0, origin_finding_id: null, raised_by: null, created_at: "2026-04-30T09:00:00+00:00" },
    { id: "ca000007-0007-0007-0007-000000000007", identifier: "REC-000012", title: "Duplicate complaint — withdrawn", source: "complaint", severity: "Minor", process_id: null, close_state: "Rejected", cycle_marker: 0, origin_finding_id: null, raised_by: null, created_at: "2026-04-20T09:00:00+00:00" },
  ],
};

export const capaDetailFixture = {
  ...capaListFixture.data[0],
  raised_by: "bbbb1111-1111-1111-1111-111111111111",
  stages: [
    { id: "st000001-0001-0001-0001-000000000001", stage: "Raised", content_block: { problem: "Two approved vendors past their re-evaluation date.", source: "audit", severity: "Major" }, cycle_marker: 0, created_by: "bbbb1111-1111-1111-1111-111111111111", created_at: "2026-05-20T09:00:00+00:00" },
    { id: "st000002-0002-0002-0002-000000000002", stage: "Containment", content_block: { correction: "Froze new POs to both vendors pending review." }, cycle_marker: 0, created_by: "bbbb9999-9999-9999-9999-999999999999", created_at: "2026-05-21T09:00:00+00:00" },
    { id: "st000003-0003-0003-0003-000000000003", stage: "RootCause", content_block: { root_cause: "Re-eval reminders never scheduled.", method: "5-whys" }, cycle_marker: 0, created_by: "bbbb1111-1111-1111-1111-111111111111", created_at: "2026-05-22T09:00:00+00:00" },
  ],
};

// A cycle_marker>0 detail: the Verify→RootCause loop ran once (two RootCause + a not_effective Verify).
export const capaLoopDetailFixture = {
  ...capaListFixture.data[4],
  raised_by: "bbbb1111-1111-1111-1111-111111111111",
  stages: [
    { id: "lp000001-0001-0001-0001-000000000001", stage: "RootCause", content_block: { root_cause: "Planning hand-off undefined." }, cycle_marker: 0, created_by: "bbbb1111-1111-1111-1111-111111111111", created_at: "2026-05-11T09:00:00+00:00" },
    { id: "lp000002-0002-0002-0002-000000000002", stage: "Verify", content_block: { decision: "not_effective", narrative: "Late deliveries recurred." }, cycle_marker: 0, created_by: "bbbb1111-1111-1111-1111-111111111111", created_at: "2026-05-18T09:00:00+00:00" },
    { id: "lp000003-0003-0003-0003-000000000003", stage: "RootCause", content_block: { root_cause: "Capacity model wrong." }, cycle_marker: 1, created_by: "bbbb1111-1111-1111-1111-111111111111", created_at: "2026-05-19T09:00:00+00:00" },
  ],
};
```

Add default handlers inside the `handlers` array (near the search/compliance block, ~line 704). The
detail handler returns the loop fixture for that one id, else the standard detail:

```ts
  // ---- S-web-7 CAPA (default happy-path; per-test overrides for 403/empty/error) ----
  http.get("/api/v1/capas", () => HttpResponse.json(capaListFixture)),
  http.get("/api/v1/capas/:id", ({ params }) => {
    if (params.id === "ca000005-0005-0005-0005-000000000005") {
      return HttpResponse.json(capaLoopDetailFixture);
    }
    return HttpResponse.json({ ...capaDetailFixture, id: String(params.id) });
  }),
```

- [ ] **Step 6: Run the hooks test to verify it passes**

Run: `npm test -- hooks.test.tsx`
Expected: PASS (all four).

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/lib/types.ts apps/web/src/features/capa/hooks.ts apps/web/src/features/capa/hooks.test.tsx apps/web/src/test/msw/handlers.ts
git commit -m "feat(s-web-7a): CAPA types, useCapas/useCapa hooks, MSW fixtures"
```

---

## Task 4: Generic `content_block` renderer (XSS-safe)

**Files:**
- Create: `apps/web/src/features/capa/ContentBlock.tsx`
- Test: `apps/web/src/features/capa/ContentBlock.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// apps/web/src/features/capa/ContentBlock.test.tsx
import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { expect, test } from "vitest";
import { theme } from "../../theme/mantine";
import { ContentBlock } from "./ContentBlock";

function wrap(block: Record<string, unknown>) {
  return render(
    <MantineProvider theme={theme}>
      <ContentBlock block={block} />
    </MantineProvider>,
  );
}

test("renders each key as a humanized label with its value", () => {
  wrap({ root_cause: "Reminders never scheduled", method: "5-whys" });
  expect(screen.getByText("Root cause")).toBeInTheDocument();
  expect(screen.getByText("Reminders never scheduled")).toBeInTheDocument();
  expect(screen.getByText("Method")).toBeInTheDocument();
});

test("renders an array value as a list", () => {
  wrap({ action_items: ["Schedule reminders", "Train planner"] });
  expect(screen.getByText("Schedule reminders")).toBeInTheDocument();
  expect(screen.getByText("Train planner")).toBeInTheDocument();
});

test("renders an HTML-looking string value as literal text (no XSS)", () => {
  const { container } = wrap({ note: "<img src=x onerror=alert(1)>" });
  expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
  expect(container.querySelector("img")).toBeNull();
});

test("renders an empty block calmly", () => {
  wrap({});
  expect(screen.getByText(/no details/i)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm test -- ContentBlock.test.tsx`
Expected: FAIL — `Cannot find module './ContentBlock'`.

- [ ] **Step 3: Implement the renderer**

```tsx
// apps/web/src/features/capa/ContentBlock.tsx
import { List, Stack, Text } from "@mantine/core";

// Free-form per-stage content_block (no fixed v1 schema). Render generically as labeled key/value —
// React escapes all interpolated strings, so an HTML-looking value is shown as literal text (never
// dangerouslySetInnerHTML). Humanize snake_case keys for display only.
function humanize(key: string): string {
  const s = key.replace(/_/g, " ").trim();
  return s.length ? s[0]!.toUpperCase() + s.slice(1) : key;
}

function renderValue(value: unknown) {
  if (Array.isArray(value)) {
    return (
      <List size="sm" withPadding>
        {value.map((v, i) => (
          <List.Item key={i}>{typeof v === "object" ? JSON.stringify(v) : String(v)}</List.Item>
        ))}
      </List>
    );
  }
  if (value !== null && typeof value === "object") {
    return <Text size="sm">{JSON.stringify(value)}</Text>;
  }
  return <Text size="sm">{String(value)}</Text>;
}

export function ContentBlock({ block }: { block: Record<string, unknown> }) {
  const entries = Object.entries(block ?? {});
  if (entries.length === 0) {
    return (
      <Text size="sm" c="dimmed">
        No details recorded.
      </Text>
    );
  }
  return (
    <Stack gap={4}>
      {entries.map(([key, value]) => (
        <div key={key}>
          <Text size="xs" fw={600} c="dimmed">
            {humanize(key)}
          </Text>
          {renderValue(value)}
        </div>
      ))}
    </Stack>
  );
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `npm test -- ContentBlock.test.tsx`
Expected: PASS (all four).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/capa/ContentBlock.tsx apps/web/src/features/capa/ContentBlock.test.tsx
git commit -m "feat(s-web-7a): generic XSS-safe content_block renderer"
```

---

## Task 5: `CapaTimeline` — the closed-loop thread

**Files:**
- Create: `apps/web/src/features/capa/CapaTimeline.tsx`
- Test: `apps/web/src/features/capa/CapaTimeline.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// apps/web/src/features/capa/CapaTimeline.test.tsx
import { render, screen, within } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { expect, test } from "vitest";
import { theme } from "../../theme/mantine";
import type { CapaStage } from "../../lib/types";
import { CapaTimeline } from "./CapaTimeline";

const directory = [
  { id: "bbbb1111-1111-1111-1111-111111111111", display_name: "Mara Quality" },
];

function wrap(stages: CapaStage[]) {
  return render(
    <MantineProvider theme={theme}>
      <CapaTimeline stages={stages} directory={directory} />
    </MantineProvider>,
  );
}

const baseStages: CapaStage[] = [
  { id: "s1", stage: "Raised", content_block: { problem: "X" }, cycle_marker: 0, created_by: "bbbb1111-1111-1111-1111-111111111111", created_at: "2026-05-20T09:00:00+00:00" },
  { id: "s2", stage: "Containment", content_block: { correction: "Y" }, cycle_marker: 0, created_by: "bbbb9999-9999-9999-9999-999999999999", created_at: "2026-05-21T09:00:00+00:00" },
];

test("renders one timeline item per stage with its label + actor", () => {
  wrap(baseStages);
  expect(screen.getByText("Raised")).toBeInTheDocument();
  expect(screen.getByText("Containment")).toBeInTheDocument();
  // resolved name
  expect(screen.getByText(/Mara Quality/)).toBeInTheDocument();
});

test("degrades to the raw id when the actor is not in the directory", () => {
  wrap(baseStages);
  // bbbb9999… isn't in the directory → shown truncated id, not a crash
  expect(screen.getByText(/bbbb9999/)).toBeInTheDocument();
});

test("marks the effectiveness loop when a stage has cycle_marker > 0", () => {
  wrap([
    ...baseStages,
    { id: "s3", stage: "RootCause", content_block: { root_cause: "Z" }, cycle_marker: 1, created_by: "bbbb1111-1111-1111-1111-111111111111", created_at: "2026-05-22T09:00:00+00:00" },
  ]);
  expect(screen.getByText(/Cycle 2/)).toBeInTheDocument(); // cycle_marker 1 => "Cycle 2"
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm test -- CapaTimeline.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the timeline**

```tsx
// apps/web/src/features/capa/CapaTimeline.tsx
import { Text, Timeline } from "@mantine/core";
import type { CapaStage, DirectoryUser } from "../../lib/types";
import { ContentBlock } from "./ContentBlock";

function actorLabel(userId: string, directory: DirectoryUser[]): string {
  const hit = directory.find((u) => u.id === userId);
  return hit?.display_name ?? `${userId.slice(0, 8)}…`;
}

function formatDate(iso: string): string {
  return new Date(iso).toISOString().slice(0, 10);
}

export function CapaTimeline({
  stages,
  directory,
}: {
  stages: CapaStage[];
  directory: DirectoryUser[];
}) {
  if (stages.length === 0) {
    return (
      <Text size="sm" c="dimmed">
        No stages yet.
      </Text>
    );
  }
  return (
    <Timeline active={stages.length} bulletSize={16} lineWidth={2}>
      {stages.map((s) => (
        <Timeline.Item
          key={s.id}
          title={
            <Text span fw={600}>
              {s.stage}
              {s.cycle_marker > 0 ? (
                <Text span size="xs" c="dimmed">
                  {" "}
                  · Cycle {s.cycle_marker + 1}
                </Text>
              ) : null}
            </Text>
          }
        >
          <Text size="xs" c="dimmed" mb={4}>
            {formatDate(s.created_at)} · {actorLabel(s.created_by, directory)}
          </Text>
          <ContentBlock block={s.content_block} />
        </Timeline.Item>
      ))}
    </Timeline>
  );
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `npm test -- CapaTimeline.test.tsx`
Expected: PASS (all three).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/capa/CapaTimeline.tsx apps/web/src/features/capa/CapaTimeline.test.tsx
git commit -m "feat(s-web-7a): CAPA closed-loop timeline (actor resolve + loop marker)"
```

---

## Task 6: `CloseGateStepper` — the close-gate readout

**Files:**
- Create: `apps/web/src/features/capa/CloseGateStepper.tsx`
- Test: `apps/web/src/features/capa/CloseGateStepper.test.tsx`

The M4 gate's three requirements (root cause documented · corrective action defined · effectiveness
evidence). In 7a we derive each step's done-ness from **stage presence** (informational; evidence-link
completeness is wired in 7b). A RootCause stage ⇒ step 1 done; an ActionPlan or Implement stage ⇒ step 2
done; a Verify stage with `content_block.decision === "effective"` ⇒ step 3 done.

- [ ] **Step 1: Write the failing test**

```tsx
// apps/web/src/features/capa/CloseGateStepper.test.tsx
import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { expect, test } from "vitest";
import { theme } from "../../theme/mantine";
import type { CapaStage } from "../../lib/types";
import { CloseGateStepper, deriveGate } from "./CloseGateStepper";

const mk = (stage: CapaStage["stage"], block: Record<string, unknown> = {}): CapaStage => ({
  id: stage, stage, content_block: block, cycle_marker: 0, created_by: "u", created_at: "2026-05-20T09:00:00+00:00",
});

test("deriveGate reflects which requirements are met from stage presence", () => {
  expect(deriveGate([mk("Raised")])).toEqual({ rootCause: false, action: false, effectiveness: false });
  expect(deriveGate([mk("RootCause"), mk("Implement")])).toEqual({
    rootCause: true, action: true, effectiveness: false,
  });
  expect(deriveGate([mk("Verify", { decision: "effective" })])).toMatchObject({ effectiveness: true });
  expect(deriveGate([mk("Verify", { decision: "not_effective" })])).toMatchObject({ effectiveness: false });
});

function wrap(stages: CapaStage[]) {
  return render(
    <MantineProvider theme={theme}>
      <CloseGateStepper stages={stages} />
    </MantineProvider>,
  );
}

test("renders the three close-gate requirements", () => {
  wrap([mk("Raised")]);
  expect(screen.getByText(/Root cause documented/)).toBeInTheDocument();
  expect(screen.getByText(/Corrective action defined/)).toBeInTheDocument();
  expect(screen.getByText(/Effectiveness evidence/)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm test -- CloseGateStepper.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the stepper**

```tsx
// apps/web/src/features/capa/CloseGateStepper.tsx
import { List, ThemeIcon } from "@mantine/core";
import type { CapaStage } from "../../lib/types";

export interface GateState {
  rootCause: boolean;
  action: boolean;
  effectiveness: boolean;
}

export function deriveGate(stages: CapaStage[]): GateState {
  const has = (s: CapaStage["stage"]) => stages.some((x) => x.stage === s);
  const effective = stages.some(
    (x) => x.stage === "Verify" && x.content_block?.decision === "effective",
  );
  return {
    rootCause: has("RootCause"),
    action: has("ActionPlan") || has("Implement"),
    effectiveness: effective,
  };
}

function Step({ done, label }: { done: boolean; label: string }) {
  return (
    <List.Item
      icon={
        <ThemeIcon color={done ? "teal" : "gray"} size={18} radius="xl">
          {done ? "✓" : "•"}
        </ThemeIcon>
      }
    >
      {label} {done ? "" : "— required"}
    </List.Item>
  );
}

export function CloseGateStepper({ stages }: { stages: CapaStage[] }) {
  const gate = deriveGate(stages);
  return (
    <List spacing="xs" size="sm" center>
      <Step done={gate.rootCause} label="Root cause documented" />
      <Step done={gate.action} label="Corrective action defined" />
      <Step done={gate.effectiveness} label="Effectiveness evidence" />
    </List>
  );
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `npm test -- CloseGateStepper.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/capa/CloseGateStepper.tsx apps/web/src/features/capa/CloseGateStepper.test.tsx
git commit -m "feat(s-web-7a): CAPA close-gate stepper (derived from stage presence)"
```

---

## Task 7: `CapaCard` — a kanban card

**Files:**
- Create: `apps/web/src/features/capa/CapaCard.tsx`
- Test: `apps/web/src/features/capa/CapaCard.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// apps/web/src/features/capa/CapaCard.test.tsx
import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";
import { theme } from "../../theme/mantine";
import type { Capa } from "../../lib/types";
import { CapaCard } from "./CapaCard";

const capa: Capa = {
  id: "ca1", identifier: "REC-000031", title: "Supplier re-evaluation overdue", source: "audit",
  severity: "Major", process_id: null, close_state: "RootCause", cycle_marker: 0,
  origin_finding_id: null, raised_by: null, created_at: "2026-05-20T09:00:00+00:00",
};

function wrap(c: Capa, onOpen = vi.fn()) {
  render(
    <MantineProvider theme={theme}>
      <CapaCard capa={c} onOpen={onOpen} />
    </MantineProvider>,
  );
  return onOpen;
}

test("shows identifier, title, severity and source", () => {
  wrap(capa);
  expect(screen.getByText("REC-000031")).toBeInTheDocument();
  expect(screen.getByText("Supplier re-evaluation overdue")).toBeInTheDocument();
  expect(screen.getByText("Major")).toBeInTheDocument();
  expect(screen.getByText("Audit")).toBeInTheDocument();
});

test("calls onOpen with the capa id when activated", async () => {
  const onOpen = wrap(capa);
  await userEvent.click(screen.getByRole("button", { name: /REC-000031/ }));
  expect(onOpen).toHaveBeenCalledWith("ca1");
});

test("a Rejected card is visually muted (marked)", () => {
  wrap({ ...capa, close_state: "Rejected" });
  expect(screen.getByText("Rejected")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm test -- CapaCard.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the card**

```tsx
// apps/web/src/features/capa/CapaCard.tsx
import { Badge, Card, Group, Stack, Text, UnstyledButton } from "@mantine/core";
import type { Capa } from "../../lib/types";
import { SEVERITY_COLOR, SEVERITY_LABEL, SOURCE_LABEL } from "./columns";

export function CapaCard({ capa, onOpen }: { capa: Capa; onOpen: (id: string) => void }) {
  const muted = capa.close_state === "Rejected" || capa.close_state === "Closed";
  return (
    <UnstyledButton
      onClick={() => onOpen(capa.id)}
      aria-label={`${capa.identifier ?? capa.id} ${capa.title ?? ""}`}
      style={{ display: "block", width: "100%" }}
    >
      <Card withBorder padding="sm" radius="md" opacity={muted ? 0.7 : 1}>
        <Stack gap={6}>
          <Group justify="space-between" wrap="nowrap">
            <Text size="xs" c="dimmed" fw={600}>
              {capa.identifier ?? "—"}
            </Text>
            <Badge size="sm" color={SEVERITY_COLOR[capa.severity]} variant="light">
              {SEVERITY_LABEL[capa.severity]}
            </Badge>
          </Group>
          <Text size="sm" fw={500} lineClamp={2}>
            {capa.title ?? "(untitled)"}
          </Text>
          <Group gap="xs">
            <Badge size="xs" variant="outline" color="gray">
              {SOURCE_LABEL[capa.source]}
            </Badge>
            {capa.close_state === "Rejected" ? (
              <Badge size="xs" variant="light" color="gray">
                Rejected
              </Badge>
            ) : null}
          </Group>
        </Stack>
      </Card>
    </UnstyledButton>
  );
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `npm test -- CapaCard.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/capa/CapaCard.tsx apps/web/src/features/capa/CapaCard.test.tsx
git commit -m "feat(s-web-7a): CAPA kanban card"
```

---

## Task 8: `CapaDrawer` — the read-only detail drawer

**Files:**
- Create: `apps/web/src/features/capa/CapaDrawer.tsx`
- Test: `apps/web/src/features/capa/CapaDrawer.test.tsx`

The drawer takes a `capaId | null` + `onClose`, fetches `useCapa(capaId)` + `useUserDirectory()`, and
composes the shell `DetailDrawer` with header meta + `CapaTimeline` + `CloseGateStepper`.

- [ ] **Step 1: Write the failing test**

```tsx
// apps/web/src/features/capa/CapaDrawer.test.tsx
import { screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { expect, test, vi } from "vitest";
import { renderWithProviders } from "../../test/render";
import { CapaDrawer } from "./CapaDrawer";

test("renders the title, the closed-loop thread and the close gate", async () => {
  renderWithProviders(<CapaDrawer capaId="ca000001-0001-0001-0001-000000000001" onClose={vi.fn()} />);
  expect(await screen.findByText(/Supplier re-evaluation overdue/)).toBeInTheDocument();
  expect(screen.getByText("Closed-loop thread")).toBeInTheDocument();
  expect(screen.getByText("Raised")).toBeInTheDocument();
  expect(screen.getByText("Containment")).toBeInTheDocument();
  expect(screen.getByText(/Root cause documented/)).toBeInTheDocument();
});

test("renders the Verify→RootCause loop honestly (cycle_marker>0)", async () => {
  renderWithProviders(<CapaDrawer capaId="ca000005-0005-0005-0005-000000000005" onClose={vi.fn()} />);
  expect(await screen.findByText(/Cycle 2/)).toBeInTheDocument();
});

test("is closed (renders nothing) when capaId is null", () => {
  const { container } = renderWithProviders(<CapaDrawer capaId={null} onClose={vi.fn()} />);
  expect(container.querySelector('[role="dialog"]')).toBeNull();
});

test("no axe violations when open", async () => {
  const { container } = renderWithProviders(
    <CapaDrawer capaId="ca000001-0001-0001-0001-000000000001" onClose={vi.fn()} />,
  );
  await screen.findByText(/Supplier re-evaluation overdue/);
  expect(await axe(container)).toHaveNoViolations();
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm test -- CapaDrawer.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the drawer**

```tsx
// apps/web/src/features/capa/CapaDrawer.tsx
import { Badge, Group, Loader, Stack, Text, Title } from "@mantine/core";
import { DetailDrawer } from "../../app/shell/DetailDrawer";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import { SEVERITY_COLOR, SEVERITY_LABEL, SOURCE_LABEL } from "./columns";
import { CapaTimeline } from "./CapaTimeline";
import { CloseGateStepper } from "./CloseGateStepper";
import { useCapa } from "./hooks";

export function CapaDrawer({ capaId, onClose }: { capaId: string | null; onClose: () => void }) {
  const { data: capa, isLoading } = useCapa(capaId);
  const { data: directory } = useUserDirectory();

  return (
    <DetailDrawer
      opened={capaId !== null}
      onClose={onClose}
      title={
        capa ? (
          <Stack gap={2}>
            <Text size="xs" c="dimmed">
              {capa.identifier ?? "CAPA"}
            </Text>
            <Title order={4}>{capa.title ?? "(untitled)"}</Title>
          </Stack>
        ) : (
          "CAPA"
        )
      }
    >
      {isLoading || !capa ? (
        <Loader />
      ) : (
        <Stack gap="lg">
          <Group gap="xs">
            <Badge color={SEVERITY_COLOR[capa.severity]} variant="light">
              {SEVERITY_LABEL[capa.severity]}
            </Badge>
            <Badge variant="outline" color="gray">
              {SOURCE_LABEL[capa.source]}
            </Badge>
            <Badge variant="light" color="blue">
              {capa.close_state}
            </Badge>
            {capa.cycle_marker > 0 ? (
              <Badge variant="light" color="grape">
                Loop ×{capa.cycle_marker}
              </Badge>
            ) : null}
          </Group>

          <div>
            <Title order={5} mb="sm">
              Closed-loop thread
            </Title>
            <CapaTimeline stages={capa.stages ?? []} directory={directory ?? []} />
          </div>

          <div>
            <Title order={5} mb="sm">
              Close gate
            </Title>
            <CloseGateStepper stages={capa.stages ?? []} />
          </div>
        </Stack>
      )}
    </DetailDrawer>
  );
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `npm test -- CapaDrawer.test.tsx`
Expected: PASS (all four).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/capa/CapaDrawer.tsx apps/web/src/features/capa/CapaDrawer.test.tsx
git commit -m "feat(s-web-7a): CAPA read-only detail drawer"
```

---

## Task 9: `CapaBoardPage` — the board (kanban, list, tiles, filters, calm-403)

**Files:**
- Create: `apps/web/src/features/capa/CapaBoardPage.tsx`
- Test: `apps/web/src/features/capa/CapaBoardPage.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// apps/web/src/features/capa/CapaBoardPage.test.tsx
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { CapaBoardPage } from "./CapaBoardPage";

test("groups CAPAs into lifecycle columns (ActionPlan+Implement merge; Rejected in Closed)", async () => {
  renderWithProviders(<CapaBoardPage />, { route: "/capa" });
  // the Implement-state CAPA appears under the Action column
  const action = (await screen.findByRole("group", { name: "Action" }));
  expect(within(action).getByText(/Scrap-rate spike/)).toBeInTheDocument();
  // the Rejected CAPA appears under Closed
  const closed = screen.getByRole("group", { name: "Closed" });
  expect(within(closed).getByText(/Duplicate complaint/)).toBeInTheDocument();
});

test("the Open tile counts non-terminal CAPAs and by-source breaks down", async () => {
  renderWithProviders(<CapaBoardPage />, { route: "/capa" });
  // 5 of the 7 fixture rows are non-terminal (not Closed/Rejected); the tile renders exactly "5"
  expect(await screen.findByText("5")).toBeInTheDocument();
  // the by-source badge carries the count (the only "Audit · N" text; card/filter labels are bare "Audit")
  expect(screen.getByText("Audit · 3")).toBeInTheDocument();
});

test("filtering by severity narrows the cards", async () => {
  renderWithProviders(<CapaBoardPage />, { route: "/capa" });
  await screen.findByText(/Supplier re-evaluation/);
  await userEvent.click(screen.getByLabelText("Severity"));
  await userEvent.click(await screen.findByRole("option", { name: "Critical" }));
  expect(screen.getByText(/Delivered batch missing CoA/)).toBeInTheDocument();
  expect(screen.queryByText(/Supplier re-evaluation/)).toBeNull();
});

test("opening a card shows the detail drawer", async () => {
  renderWithProviders(<CapaBoardPage />, { route: "/capa" });
  await userEvent.click(await screen.findByRole("button", { name: /REC-000031/ }));
  expect(await screen.findByText("Closed-loop thread")).toBeInTheDocument();
});

test("renders a calm no-access panel on a 403", async () => {
  server.use(
    http.get("/api/v1/capas", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  renderWithProviders(<CapaBoardPage />, { route: "/capa" });
  expect(await screen.findByText(/don’t have access/)).toBeInTheDocument();
});

test("no axe violations", async () => {
  const { container } = renderWithProviders(<CapaBoardPage />, { route: "/capa" });
  await screen.findByText(/Supplier re-evaluation/);
  expect(await axe(container)).toHaveNoViolations();
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm test -- CapaBoardPage.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the board page**

```tsx
// apps/web/src/features/capa/CapaBoardPage.tsx
import {
  Alert, Badge, Box, Card, Container, Group, Loader, ScrollArea, SegmentedControl, Select,
  SimpleGrid, Stack, Table, Text, Title,
} from "@mantine/core";
import { useMemo, useState } from "react";
import type { Capa, CapaSource, CapaCloseState, NcSeverity } from "../../lib/types";
import { CapaCard } from "./CapaCard";
import { CapaDrawer } from "./CapaDrawer";
import { CAPA_COLUMNS, columnKeyFor, SEVERITY_LABEL, SOURCE_LABEL } from "./columns";
import { useCapas } from "./hooks";

const TERMINAL: CapaCloseState[] = ["Closed", "Rejected"];

export function CapaBoardPage() {
  const { data, isLoading, isError, forbidden } = useCapas();
  const [view, setView] = useState<"board" | "list">("board");
  const [source, setSource] = useState<CapaSource | "">("");
  const [severity, setSeverity] = useState<NcSeverity | "">("");
  const [state, setState] = useState<CapaCloseState | "">("");
  const [selected, setSelected] = useState<string | null>(null);

  const rows = data ?? [];
  const filtered = useMemo(
    () =>
      rows.filter(
        (c) =>
          (source === "" || c.source === source) &&
          (severity === "" || c.severity === severity) &&
          (state === "" || c.close_state === state),
      ),
    [rows, source, severity, state],
  );

  if (forbidden) {
    return (
      <Container size="md" py="md">
        <Title order={2} mb="md">
          Nonconformity &amp; CAPA
        </Title>
        <Alert color="gray" title="No access">
          You don&rsquo;t have access to the CAPA board. It&rsquo;s available to the Quality Manager,
          Process Owner and Internal Auditor roles.
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
          Nonconformity &amp; CAPA
        </Title>
        <Alert color="red" title="Couldn't load CAPAs">
          Please try again.
        </Alert>
      </Container>
    );
  }

  const openCount = rows.filter((c) => !TERMINAL.includes(c.close_state)).length;
  const bySource = (["audit", "complaint", "process", "review_output"] as CapaSource[])
    .map((s) => ({ source: s, n: rows.filter((c) => c.source === s).length }))
    .filter((x) => x.n > 0);

  return (
    <Container size="xl" py="md">
      <Group justify="space-between" mb="md">
        <Title order={2}>Nonconformity &amp; CAPA</Title>
        <SegmentedControl
          value={view}
          onChange={(v) => setView(v as "board" | "list")}
          data={[
            { value: "board", label: "Board" },
            { value: "list", label: "List" },
          ]}
        />
      </Group>

      <SimpleGrid cols={{ base: 1, sm: 2 }} mb="md">
        <Card withBorder padding="sm">
          <Text size="xs" c="dimmed">
            Open CAPAs
          </Text>
          <Text fz="xl" fw={700}>
            {openCount}
          </Text>
        </Card>
        <Card withBorder padding="sm">
          <Text size="xs" c="dimmed" mb={4}>
            By source
          </Text>
          <Group gap="xs">
            {bySource.map((x) => (
              <Badge key={x.source} variant="light" color="gray">
                {SOURCE_LABEL[x.source]} · {x.n}
              </Badge>
            ))}
          </Group>
        </Card>
      </SimpleGrid>

      <Group mb="md" gap="sm">
        <Select
          aria-label="Source"
          placeholder="All sources"
          clearable
          value={source || null}
          onChange={(v) => setSource((v as CapaSource) ?? "")}
          data={Object.entries(SOURCE_LABEL).map(([value, label]) => ({ value, label }))}
        />
        <Select
          aria-label="Severity"
          placeholder="All severities"
          clearable
          value={severity || null}
          onChange={(v) => setSeverity((v as NcSeverity) ?? "")}
          data={Object.entries(SEVERITY_LABEL).map(([value, label]) => ({ value, label }))}
        />
        <Select
          aria-label="State"
          placeholder="All states"
          clearable
          value={state || null}
          onChange={(v) => setState((v as CapaCloseState) ?? "")}
          data={(
            ["Raised", "Containment", "RootCause", "ActionPlan", "Implement", "Verify", "Closed", "Rejected"] as CapaCloseState[]
          ).map((s) => ({ value: s, label: s }))}
        />
      </Group>

      {filtered.length === 0 ? (
        <Text c="dimmed">No CAPAs match.</Text>
      ) : view === "board" ? (
        <ScrollArea>
          <Group align="flex-start" wrap="nowrap" gap="md">
            {CAPA_COLUMNS.map((col) => {
              const cards = filtered.filter((c) => columnKeyFor(c.close_state) === col.key);
              return (
                <Box key={col.key} role="group" aria-label={col.label} miw={260} w={260}>
                  <Group justify="space-between" mb="xs">
                    <Text fw={600} size="sm">
                      {col.label}
                    </Text>
                    <Badge variant="light" color="gray">
                      {cards.length}
                    </Badge>
                  </Group>
                  <Stack gap="xs">
                    {cards.map((c) => (
                      <CapaCard key={c.id} capa={c} onOpen={setSelected} />
                    ))}
                  </Stack>
                </Box>
              );
            })}
          </Group>
        </ScrollArea>
      ) : (
        <Table striped highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Identifier</Table.Th>
              <Table.Th>Title</Table.Th>
              <Table.Th>Severity</Table.Th>
              <Table.Th>Source</Table.Th>
              <Table.Th>State</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {filtered.map((c: Capa) => (
              <Table.Tr
                key={c.id}
                style={{ cursor: "pointer" }}
                onClick={() => setSelected(c.id)}
              >
                <Table.Td>{c.identifier ?? "—"}</Table.Td>
                <Table.Td>{c.title ?? "(untitled)"}</Table.Td>
                <Table.Td>{SEVERITY_LABEL[c.severity]}</Table.Td>
                <Table.Td>{SOURCE_LABEL[c.source]}</Table.Td>
                <Table.Td>{c.close_state}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}

      <CapaDrawer capaId={selected} onClose={() => setSelected(null)} />
    </Container>
  );
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `npm test -- CapaBoardPage.test.tsx`
Expected: PASS (all six). If the `role="group"` query is brittle, confirm the `Box` carries
`role="group"` + `aria-label`.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/capa/CapaBoardPage.tsx apps/web/src/features/capa/CapaBoardPage.test.tsx
git commit -m "feat(s-web-7a): CAPA board page (kanban + list + tiles + filters + calm-403)"
```

---

## Task 10: Wire the nav + route, then run the full gate + slice history

**Files:**
- Modify: `apps/web/src/App.tsx` (import + route)
- Modify: `apps/web/src/app/shell/LeftRail.tsx` (nav entry)
- Modify: `apps/web/src/app/shell/LeftRail.test.tsx` (assert the entry is always present)
- Modify: `docs/slice-history.md` (the S-web-7a entry)

- [ ] **Step 1: Add the route in `App.tsx`**

Import near the other feature pages (after the `CompliancePage` import, line 17):

```tsx
import { CapaBoardPage } from "./features/capa/CapaBoardPage";
```

Add the route inside the `/` AppShell `<Route>` block (after the `compliance` route, line 112):

```tsx
        <Route path="capa" element={<CapaBoardPage />} />
```

- [ ] **Step 2: Add the nav entry in `LeftRail.tsx`**

Unlike Compliance/Import (key-gated), the CAPA entry is **unconditional + discoverable** (per spec §5.4 —
a core ACT-phase area; the page itself shows a calm no-access panel for callers without `capa.read`). Add
after the Compliance `NavLink` block (line 36), before the `import.review` block:

```tsx
      <NavLink
        component={Link}
        to="/capa"
        label="Nonconformity & CAPA"
        active={pathname.startsWith("/capa")}
      />
```

- [ ] **Step 3: Update the LeftRail test to assert the entry is always present**

In `apps/web/src/app/shell/LeftRail.test.tsx`, add a test (mirror the file's existing render setup —
copy the imports + the `renderWithProviders`/`server.use` pattern already there):

```tsx
test("the Nonconformity & CAPA entry is always shown (discoverable; page handles 403)", async () => {
  // default /me/permissions grants no key → still present (unlike the gated Compliance/Import entries)
  renderWithProviders(<LeftRail />, { route: "/" });
  expect(await screen.findByText("Nonconformity & CAPA")).toBeInTheDocument();
});
```

> Read the top of `LeftRail.test.tsx` first and match its exact import list + render helper. If it asserts
> an exhaustive nav-entry list anywhere, add "Nonconformity & CAPA" to that expectation too.

- [ ] **Step 4: Run the route + nav tests**

Run: `npm test -- LeftRail.test.tsx`
Expected: PASS (existing gated-entry tests + the new always-present test).

- [ ] **Step 5: Run the FULL web gate**

Run: `/check-web`  (eslint + strict tsc `--noEmit` + vite build + the WHOLE vitest suite)
Expected: all green. The full run catches cross-task drift the per-file runs miss (strict
`noUncheckedIndexedAccess` in particular — e.g. the `s[0]!` non-null in `humanize`, the tuple unpacks).
Fix any strict-tsc nits inline (they won't show in a single-file `npm test`).

- [ ] **Step 6: Add the slice-history entry**

In `docs/slice-history.md`, add an `S-web-7a` entry to the web-track section (mirror the existing S-web-6 /
S-ing-4b entry style): one paragraph — front-end CAPA read spine (board + read-only drawer) over
`GET /capas` + `/capas/{id}`; the one thin backend enrichment (`title`/`created_at`/`raised_by` on the
`_capa` serializer, no migration); demo holds no `capa.*` → calm-403 (S-web-6 case); part of the S-web-7
NC & CAPA front-door epic (7a of 7a–7d).

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/App.tsx apps/web/src/app/shell/LeftRail.tsx apps/web/src/app/shell/LeftRail.test.tsx docs/slice-history.md
git commit -m "feat(s-web-7a): wire CAPA board route + nav; slice history"
```

---

## Pre-PR (after all tasks)

- [ ] Run the `diff-critic` agent on the branch diff (`Agent`, `subagent_type: diff-critic`) — hunt the
  false-PASS direction, especially **fixture-vs-real-backend shape** (the #1 recurring bug): confirm the
  `Capa`/`CapaStage` fixtures match `api/capa.py:_capa`/`_stage` + the Task 1 enrichment exactly.
- [ ] `/pr` to open the PR against `main`; ensure all five CI jobs are green (note: the **integration**
  job is where Task 1's api test actually runs).
- [ ] Address the Codex review bot on every thread (reply + resolve via `gh api`); it re-reviews on each push.

---

## Self-review notes (author)

- **Spec coverage:** §4 enrichment → Task 1. §6.2 board/columns/tiles/filters/calm-403 → Tasks 2,9. Drawer
  timeline + generic content_block + close-gate stepper + loop → Tasks 4,5,6,8. Gating model: 7a is
  read-only so `usePermissions` write-gating is **not** used here (it lands in 7b) — the only authz surface
  in 7a is the list 403 → calm panel (Tasks 3,9). Nav/route → Task 10. ✓
- **Honest-tiles:** only Open + by-source tiles (Task 9) — overdue/cycle-time intentionally absent. ✓
- **Type consistency:** `Capa`/`CapaStage`/`CapaCloseState`/`CapaSource`/`NcSeverity` defined in Task 3 are
  the exact names used in Tasks 2,4–9. `columnKeyFor`/`CAPA_COLUMNS`/`SEVERITY_*`/`SOURCE_LABEL` (Task 2)
  are consumed unchanged in Tasks 7,8,9. `deriveGate`/`GateState` (Task 6) used only within Task 6. ✓
- **api-tests-are-CI-only** is called out in Task 1 (local gate = `/check-api` static + `/check-contracts`).
