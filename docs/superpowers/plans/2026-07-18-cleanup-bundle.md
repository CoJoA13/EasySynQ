# S-cleanup-bundle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship five low-risk, migration-free cleanup wins from the 2026-07-18 improvement survey in one PR: audit-partition runway wiring, requeue-failed-emails, calm loading/error states, per-route title + focus, and register-table scroll/scope.

**Architecture:** Each item is independent and touches no load-bearing invariant (no WORM/audit-chain/authz/mirror change, no schema change, no new permission key, no `ALTER TYPE`). Backend items reuse existing idempotent helpers and the `config.update` gate; web items reuse existing `lib/states` primitives and the react-query hook pattern.

**Tech Stack:** FastAPI / Python 3.12 / SQLAlchemy async · React 19 / TS / Mantine / @tanstack/react-query · pytest (unit + integration/testcontainers) · vitest + MSW + jest-axe · redocly (openapi lint).

## Global Constraints

- **No migration** — the whole bundle is migration-free; the `migrations` CI job stays a no-op round-trip. Do NOT add a migration or `ALTER TYPE`.
- **No new permission key** — #6 reuses `config.update` (the `_config_update` dependency in `api/config.py`).
- **#6 is structured-log only** — no `audit_event`, no WORM touch. Owner decision 2026-07-18.
- **Web test traps** (`.claude/rules/engineering-patterns.md`): in every test file `import { expect, it, describe, vi } from "vitest"` (never the bare globals — `tsc` catches the jest-typed `expect`); pin MSW fixtures to the real serializer shape via `satisfies`; keep `aria-label`s distinct (single-match `getByLabelText`); add any new endpoint to the **base** MSW handlers (`onUnhandledRequest: "error"` fails otherwise).
- **Verification gate before PR:** `/check-api` + `/check-web` + `/check-contracts` + integration suite; then the `diff-critic` and `web-test-trap-reviewer` agents on the branch diff.
- **Build order** (each its own commit): Task 1 (#19) → Task 2 (#17) → Task 3 (#15) → Task 4 (#16) → Task 5 (#6 backend) → Task 6 (#6 web).
- **Branch:** `feat/s-cleanup-bundle` (already created; the spec is committed at `2c4b40a`).

---

## Task 1: #19 — Audit-partition runway (conftest + API lifespan)

**Files:**
- Modify: `apps/api/tests/integration/conftest.py:203-209` (the owner-engine block after `alembic upgrade head`)
- Modify: `apps/api/src/easysynq_api/main.py:90-93` (the `lifespan` startup) + its import block
- Test (create): `apps/api/tests/unit/test_partitions.py`
- Test (create): `apps/api/tests/integration/test_partition_runway.py`

**Interfaces:**
- Consumes: `ensure_partitions(session: AsyncSession, today: date | None = None) -> list[str]` and `upcoming_month_starts(today: date) -> list[date]` from `easysynq_api.services.audit.partitions`; `get_sessionmaker()` from `easysynq_api.db.session`.
- Produces: nothing new for later tasks (self-contained).

- [ ] **Step 1: Write the failing unit test**

Create `apps/api/tests/unit/test_partitions.py`:

```python
import datetime

from easysynq_api.services.audit.partitions import upcoming_month_starts


def test_runway_covers_a_post_august_fresh_install() -> None:
    # Migration 0010 seeds a FIXED 2026-06/07/08 runway; a fresh install after Aug 2026 relies on
    # ensure_partitions()/upcoming_month_starts to cover the current month + the next two.
    starts = upcoming_month_starts(datetime.date(2026, 10, 15))
    assert starts == [
        datetime.date(2026, 10, 1),
        datetime.date(2026, 11, 1),
        datetime.date(2026, 12, 1),
    ]


def test_runway_rolls_across_a_year_boundary() -> None:
    starts = upcoming_month_starts(datetime.date(2026, 12, 3))
    assert starts == [
        datetime.date(2026, 12, 1),
        datetime.date(2027, 1, 1),
        datetime.date(2027, 2, 1),
    ]
```

- [ ] **Step 2: Run it (it should PASS — `upcoming_month_starts` already exists)**

Run: `cd apps/api && uv run pytest tests/unit/test_partitions.py -v`
Expected: PASS (this pins the pure helper's behavior that Task 1's wiring depends on). If it fails, stop — the helper changed.

- [ ] **Step 3: Wire `ensure_partitions` into the integration conftest**

In `apps/api/tests/integration/conftest.py`, the owner-engine block currently reads:

```python
    _owner = _sa.create_engine(_pg)
    with _owner.begin() as conn:
        conn.execute(
            _sa.text("UPDATE system_config SET setup_state='OPERATIONAL', finalized_at=now()")
        )
    _owner.dispose()
```

Replace it with (adds the current+2 partition runway on top of the migration's fixed seed, so June-pinned tests AND real-`now()` tests both stay green past 2026-09-01):

```python
    import datetime as _dt

    from easysynq_api.services.audit.partitions import upcoming_month_starts

    _owner = _sa.create_engine(_pg)
    with _owner.begin() as conn:
        conn.execute(
            _sa.text("UPDATE system_config SET setup_state='OPERATIONAL', finalized_at=now()")
        )
        # Keep the audit_event partition runway current (Beat doesn't run in tests; migration 0010
        # only seeds a fixed 2026-06/07/08). Idempotent; the owner role may call the SECURITY-DEFINER
        # function directly.
        for _start in upcoming_month_starts(_dt.datetime.now(_dt.UTC).date()):
            conn.execute(
                _sa.text("SELECT easysynq_create_audit_partition(:s)"), {"s": _start}
            )
    _owner.dispose()
```

- [ ] **Step 4: Write the failing integration test**

Create `apps/api/tests/integration/test_partition_runway.py`:

```python
import datetime
from typing import Any

from sqlalchemy import text

from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.audit.partitions import upcoming_month_starts


async def test_month_plus_two_partition_exists(app_under_test: Any) -> None:
    # The month+2 partition is NOT in migration 0010's fixed seed — only the conftest ensure-call
    # (and, in prod, the lifespan/Beat) creates it. Its existence proves the runway wiring ran.
    plus_two = upcoming_month_starts(datetime.datetime.now(datetime.UTC).date())[-1]
    name = f"audit_event_{plus_two.strftime('%Y_%m')}"
    async with get_sessionmaker()() as s:
        count = (
            await s.execute(
                text("SELECT count(*) FROM pg_class WHERE relname = :n"), {"n": name}
            )
        ).scalar_one()
    assert count == 1, f"expected partition {name} to exist"
```

- [ ] **Step 5: Run the integration test (needs Docker)**

Run: `cd apps/api && uv run pytest tests/integration/test_partition_runway.py -v`
Expected: PASS. (Mutation check — temporarily comment out the `for _start ...` loop from Step 3: this test should then FAIL because the month+2 partition is absent. Restore the loop.)

- [ ] **Step 6: Wire `ensure_partitions` into the API lifespan startup**

In `apps/api/src/easysynq_api/main.py`, add to the import block (near the other `.services` imports):

```python
import logging

from .services.audit.partitions import ensure_partitions
```

Add a module logger just below the imports (after the existing `from .services.setup import get_setup_state` line):

```python
logger = logging.getLogger("easysynq.startup")
```

Replace the `lifespan` function:

```python
@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Best-effort: keep the audit_event partition runway current on boot so a fresh install after
    # Aug 2026 has a covering month before first-run setup writes audit events. Compose orders the
    # API after `migrate` completes, so the SECURITY-DEFINER function exists. Never block startup —
    # the daily roll_partitions Beat is the steady-state backstop.
    try:
        async with get_sessionmaker()() as _session:
            await ensure_partitions(_session)
    except Exception:  # noqa: BLE001 - best-effort startup hook; a DB hiccup must not block boot
        logger.warning("audit.ensure_partitions_on_startup_failed", exc_info=True)
    yield
    await dispose_engine()
```

- [ ] **Step 7: Run the API fast loop**

Run: `cd apps/api && uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest tests/unit/test_partitions.py -v`
Expected: all PASS (ruff clean, mypy clean, unit green). Note: the `--fix` ruff hook may strip the just-added `ensure_partitions`/`logging` import if it runs before Step 6's usage lands — if so, re-add.

- [ ] **Step 8: Commit**

```bash
git add apps/api/tests/integration/conftest.py apps/api/src/easysynq_api/main.py \
        apps/api/tests/unit/test_partitions.py apps/api/tests/integration/test_partition_runway.py
git commit -m "fix(audit): ensure partition runway in tests + API startup (survey #19)

migration 0010 seeds a fixed 2026-06/07/08 runway; the daily roll_partitions Beat
doesn't run in tests and a fresh install after Aug 2026 has no current-month
partition. Call the idempotent ensure_partitions() in the integration conftest
(fixes the CI red on 2026-09-01) and best-effort in the API lifespan (fixes
fresh-install-fails-at-setup). No migration.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: #17 — Register tables: scroll container + header scope

**Files:**
- Modify: `apps/web/src/features/library/LibraryPage.tsx:159-174` (the `<Table aria-label="Documents">` block)
- Modify: `apps/web/src/features/capa/NcrsPage.tsx:74-84` (the `<Table striped>` block)
- Modify: `apps/web/src/features/compliance/CompliancePage.tsx:65-75` (the `<Table striped>` block)
- Test: `apps/web/src/features/library/LibraryPage.test.tsx`, `apps/web/src/features/capa/NcrsPage.test.tsx`, `apps/web/src/features/compliance/CompliancePage.test.tsx`

**Interfaces:** none exported; presentational-only edits.

- [ ] **Step 1: Add `scope="col"` + a scroll container in LibraryPage**

In `LibraryPage.tsx`, wrap the `<Table … aria-label="Documents">…</Table>` in `<Table.ScrollContainer minWidth={720}>` and add `scope="col"` to each header cell. Result:

```tsx
{!isLoading && !isError && rows.length > 0 && (
  <Table.ScrollContainer minWidth={720}>
    <Table
      highlightOnHover
      stickyHeader
      verticalSpacing={density === "compact" ? "xs" : "sm"}
      aria-label="Documents"
    >
      <Table.Thead>
        <Table.Tr>
          <Table.Th scope="col">Identifier</Table.Th>
          <Table.Th scope="col">Title</Table.Th>
          <Table.Th scope="col">Type</Table.Th>
          <Table.Th scope="col">Owner</Table.Th>
          <Table.Th scope="col">Clause</Table.Th>
          <Table.Th scope="col">State</Table.Th>
          <Table.Th scope="col">Effective</Table.Th>
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {/* … unchanged … */}
      </Table.Tbody>
    </Table>
  </Table.ScrollContainer>
)}
```

(`Table.ScrollContainer` is part of the `Table` compound component already imported; no new import.)

- [ ] **Step 2: Same treatment in NcrsPage**

In `NcrsPage.tsx`, wrap `<Table striped highlightOnHover>…</Table>` in `<Table.ScrollContainer minWidth={640}>` and add `scope="col"` to the five headers (Identifier, Source, Severity, Description, Disposition).

- [ ] **Step 3: Same treatment in CompliancePage**

In `CompliancePage.tsx`, wrap `<Table striped highlightOnHover>…</Table>` in `<Table.ScrollContainer minWidth={720}>` and add `scope="col"` to the seven headers (Clause, Title, Phase, Mapped, Effective, Status, Review).

- [ ] **Step 4: Add a scope/axe assertion to each register test**

Append to `LibraryPage.test.tsx` (inside its `describe`), adjusting the render helper/route to match the file's existing pattern:

```tsx
it("gives register headers a column scope (a11y)", async () => {
  renderWithProviders(<LibraryPage />, { route: "/library" });
  const header = await screen.findByRole("columnheader", { name: "Identifier" });
  expect(header).toHaveAttribute("scope", "col");
});
```

Add the analogous test to `NcrsPage.test.tsx` (header name `"Identifier"`, route `/capa/ncrs`) and `CompliancePage.test.tsx` (header name `"Clause"`, route `/compliance`). Match each file's existing import list and render signature; ensure `import { expect, it } from "vitest"` is present.

- [ ] **Step 5: Run the web tests for the three files**

Run: `cd apps/web && npx vitest run src/features/library/LibraryPage.test.tsx src/features/capa/NcrsPage.test.tsx src/features/compliance/CompliancePage.test.tsx`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/features/library/LibraryPage.tsx apps/web/src/features/capa/NcrsPage.tsx \
        apps/web/src/features/compliance/CompliancePage.tsx \
        apps/web/src/features/library/LibraryPage.test.tsx apps/web/src/features/capa/NcrsPage.test.tsx \
        apps/web/src/features/compliance/CompliancePage.test.tsx
git commit -m "a11y(web): scroll container + scope=col on the bare registers (survey #17)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: #15 — Calm loading/error states in the older admin tabs + DocumentDrawer

**Files:**
- Modify: `apps/web/src/admin/RolesAdmin.tsx:1,24-26`
- Modify: `apps/web/src/admin/UsersAdmin.tsx:1-17,93-95`
- Modify: `apps/web/src/admin/ProcessesAdmin.tsx:1-13,48-59`
- Modify: `apps/web/src/features/document/DocumentDrawer.tsx:7,29,44-46`
- Test: `apps/web/src/admin/ProcessesAdmin.test.tsx` (exists — verify still green), `apps/web/src/features/document/DocumentDrawer.test.tsx` (add an error-branch test)

**Interfaces:**
- Consumes: `LoadingState`, `ErrorState`, `NoAccessState` from `apps/web/src/lib/states` — `LoadingState({label?})`, `ErrorState({title?, message?, onRetry?})`, `NoAccessState({message})`.

- [ ] **Step 1: RolesAdmin — swap bare Loader/Alert for the primitives**

In `RolesAdmin.tsx`, change the import line 1 to drop `Alert`/`Loader` if now unused at the page level (keep `Loader` — line 66 still uses `<Loader size="sm" />` inline) and add the states import:

```tsx
import { Accordion, Badge, Group, Loader, Stack, Table, Text } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "../lib/api";
import { ErrorState, LoadingState } from "../lib/states";
```

Replace lines 24-26:

```tsx
  if (roles.isLoading) return <LoadingState label="Loading roles" />;
  if (roles.isError)
    return <ErrorState title="Couldn't load roles" onRetry={() => void roles.refetch()} />;
```

(`Alert` is no longer used → removing it from the import avoids an eslint no-unused error. The inline `<Loader size="sm" />` at line 66 stays.)

- [ ] **Step 2: UsersAdmin — same swap**

In `UsersAdmin.tsx`, keep the existing `@mantine/core` import but remove `Alert` ONLY if it's unused after this change — note line ~100 still uses `<Alert color="red" title="Action failed" …>` for the inline mutation error, so **keep `Alert` in the import**. Add the states import after line 20:

```tsx
import { ErrorState, LoadingState } from "../lib/states";
```

Replace lines 93-95:

```tsx
  if (users.isLoading) return <LoadingState label="Loading users" />;
  if (users.isError)
    return <ErrorState title="Couldn't load users" onRetry={() => void users.refetch()} />;
```

- [ ] **Step 3: ProcessesAdmin — LoadingState + NoAccessState + ErrorState**

In `ProcessesAdmin.tsx`, add to the states import (after line 18):

```tsx
import { ErrorState, LoadingState, NoAccessState } from "../lib/states";
```

Replace lines 48-59:

```tsx
  if (processes.isLoading) return <LoadingState label="Loading processes" />;
  if (processes.isError) {
    const forbidden = processes.error instanceof ApiError && processes.error.status === 403;
    return forbidden ? (
      <NoAccessState message="You need process.read to manage process owners." />
    ) : (
      <ErrorState title="Could not load processes" onRetry={() => void processes.refetch()} />
    );
  }
```

Remove `Alert` from the `@mantine/core` import at the top ONLY if no other usage remains in the file (grep first: `grep -n "<Alert" apps/web/src/admin/ProcessesAdmin.tsx`). If other `<Alert>` usages remain, keep it.

- [ ] **Step 4: DocumentDrawer — add the error branch (close the blank-on-error dead-end)**

In `DocumentDrawer.tsx` line 7, extend the states import:

```tsx
import { ErrorState, LoadingState } from "../../lib/states";
```

Line 29, destructure `isError` and `refetch`:

```tsx
  const { data: doc, isLoading, isError, refetch } = useDocument(documentId, { enabled: opened, seed });
```

Just after line 46 (`{isLoading && !doc && <LoadingState label="Loading document" />}`), add:

```tsx
      {isError && !doc && (
        <ErrorState title="Couldn't load this document" onRetry={() => void refetch()} />
      )}
```

- [ ] **Step 5: Add a DocumentDrawer error-branch test**

Append to `DocumentDrawer.test.tsx` (match its existing imports + render helper; ensure `import { expect, it } from "vitest"`):

```tsx
it("shows a retryable error (not a blank drawer) when the document load fails", async () => {
  server.use(
    http.get("/api/v1/documents/:id", () =>
      HttpResponse.json({ code: "boom", title: "nope" }, { status: 500 }),
    ),
  );
  renderWithProviders(<DocumentDrawer documentId="00000000-0000-0000-0000-0000000000ff" onClose={() => {}} />);
  expect(await screen.findByText("Couldn't load this document")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
});
```

(If the test file lacks `server`/`http`/`HttpResponse` imports, add: `import { http, HttpResponse } from "msw";` and `import { server } from "../../test/msw/server";`. Do NOT pass a `seed` — a seed populates `doc` and suppresses the error branch by design.)

- [ ] **Step 6: Run the affected web tests**

Run: `cd apps/web && npx vitest run src/admin/ProcessesAdmin.test.tsx src/features/document/DocumentDrawer.test.tsx`
Expected: PASS. ProcessesAdmin.test asserts the "No access" title (preserved by `NoAccessState`) and the same forbidden message string — it should stay green. If it asserted the raw `String(error)` text for the non-forbidden path, update that assertion to `screen.getByText("Could not load processes")`.

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/admin/RolesAdmin.tsx apps/web/src/admin/UsersAdmin.tsx \
        apps/web/src/admin/ProcessesAdmin.tsx apps/web/src/features/document/DocumentDrawer.tsx \
        apps/web/src/features/document/DocumentDrawer.test.tsx
git commit -m "ux(web): calm loading/error states in admin tabs + DocumentDrawer (survey #15)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: #16 — Per-route document.title + route-change focus (WCAG 2.4.2 / 2.4.3)

**Files:**
- Create: `apps/web/src/lib/routeChrome.ts`
- Modify: `apps/web/src/App.tsx` (call `useRouteChrome()` at the top of the `App` component body, before any conditional return)
- Modify: `apps/web/src/app/shell/AppShell.tsx:51` (add `tabIndex={-1}` to `<MantineAppShell.Main id="main-content">`)
- Test: `apps/web/src/lib/routeChrome.test.tsx` (create)

**Interfaces:**
- Produces: `useRouteChrome(): void` — sets `document.title` to `EasySynQ — <label>` and moves focus to `#main-content` on pathname change (skipping the initial mount). Must be called inside a react-router context (App is rendered inside `<BrowserRouter>` per `main.tsx`).

> **Refinement vs spec:** the spec said "centralized in AppShell", but `/admin` and `/setup` are mounted OUTSIDE `AppShell` in `App.tsx`. Calling the hook in `App` (which renders `<Routes>` and is inside the Router) covers ALL routes for the title. Focus targets `#main-content`, which exists in the AppShell subtree; elsewhere the `getElementById` is a guarded no-op.

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/lib/routeChrome.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useNavigate } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { useRouteChrome } from "./routeChrome";

function Harness() {
  useRouteChrome();
  const nav = useNavigate();
  return (
    <>
      <button onClick={() => nav("/library")}>go-library</button>
      <main id="main-content" tabIndex={-1}>
        content
      </main>
    </>
  );
}

describe("useRouteChrome", () => {
  it("sets the document title per route and focuses main on navigation (not initial mount)", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/compliance"]}>
        <Harness />
      </MemoryRouter>,
    );
    // initial route → title set, but focus NOT stolen from the document body
    expect(document.title).toBe("EasySynQ — Compliance");
    expect(document.activeElement).not.toBe(document.getElementById("main-content"));

    await user.click(screen.getByText("go-library"));
    expect(document.title).toBe("EasySynQ — Library");
    expect(document.activeElement).toBe(document.getElementById("main-content"));
  });

  it("falls back to the bare app name for an unmapped route", () => {
    render(
      <MemoryRouter initialEntries={["/totally-unknown"]}>
        <Harness />
      </MemoryRouter>,
    );
    expect(document.title).toBe("EasySynQ");
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd apps/web && npx vitest run src/lib/routeChrome.test.tsx`
Expected: FAIL — `Cannot find module './routeChrome'`.

- [ ] **Step 3: Implement the hook**

Create `apps/web/src/lib/routeChrome.ts`:

```ts
import { useEffect, useRef } from "react";
import { useLocation } from "react-router-dom";

// path-prefix → tab-title. Longest prefix wins (sorted below), so a child path like
// /settings/notifications resolves before a shorter sibling. Unmapped → the bare app name.
const TITLES: readonly (readonly [string, string])[] = [
  ["/admin", "Administration"],
  ["/setup", "Setup"],
  ["/library", "Library"],
  ["/documents", "Document"],
  ["/tasks", "Tasks"],
  ["/settings/notifications", "Notification settings"],
  ["/notifications", "Notifications"],
  ["/search", "Search"],
  ["/compliance", "Compliance"],
  ["/capa", "CAPA"],
  ["/audits", "Audits"],
  ["/ingestion", "Ingestion"],
  ["/drift", "Drift"],
  ["/objectives", "Objectives"],
  ["/management-reviews", "Management reviews"],
  ["/dcrs", "Document change requests"],
  ["/improvement", "Improvement"],
  ["/risks", "Risks"],
  ["/context", "Context"],
  ["/interested-parties", "Interested parties"],
];

const SORTED = [...TITLES].sort((a, b) => b[0].length - a[0].length);

function labelFor(pathname: string): string {
  for (const [prefix, label] of SORTED) {
    if (pathname === prefix || pathname.startsWith(prefix + "/")) return label;
  }
  return "";
}

export function useRouteChrome(): void {
  const { pathname } = useLocation();
  const firstRun = useRef(true);
  useEffect(() => {
    const label = labelFor(pathname);
    document.title = label ? `EasySynQ — ${label}` : "EasySynQ";
    if (firstRun.current) {
      firstRun.current = false;
      return; // don't steal focus from the skip-link on the first load
    }
    document.getElementById("main-content")?.focus();
  }, [pathname]);
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd apps/web && npx vitest run src/lib/routeChrome.test.tsx`
Expected: PASS. (`@testing-library/user-event@^14` is a direct dev dependency — the static `import userEvent from "@testing-library/user-event"` + `userEvent.setup()` idiom is used across the suite.)

- [ ] **Step 5: Call the hook in App + make #main-content focusable**

In `apps/web/src/App.tsx`, add the import near the other `./lib` imports:

```tsx
import { useRouteChrome } from "./lib/routeChrome";
```

Call it as the FIRST statement inside the `App` component body (before any `if (...) return …` early return, so hook order is stable):

```tsx
export function App() {
  useRouteChrome();
  // … existing body …
```

In `apps/web/src/app/shell/AppShell.tsx` line 51, add `tabIndex={-1}`:

```tsx
      <MantineAppShell.Main id="main-content" tabIndex={-1}>
```

- [ ] **Step 6: Run App.test + a full typecheck of the touched area**

Run: `cd apps/web && npx vitest run src/App.test.tsx src/lib/routeChrome.test.tsx && npx tsc --noEmit`
Expected: PASS + no type errors. (App.test may now observe a non-default `document.title`; if it asserts the title, update it — otherwise it's unaffected.)

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/lib/routeChrome.ts apps/web/src/lib/routeChrome.test.tsx \
        apps/web/src/App.tsx apps/web/src/app/shell/AppShell.tsx
git commit -m "a11y(web): per-route document.title + route-change focus (survey #16)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: #6 backend — Requeue failed notification emails (service + route + contract)

**Files:**
- Create: `apps/api/src/easysynq_api/services/notifications/requeue.py`
- Modify: `apps/api/src/easysynq_api/api/config.py` (import + new POST route)
- Modify: `packages/contracts/openapi.yaml` (document the new endpoint)
- Test (create): `apps/api/tests/integration/test_notification_requeue.py`

**Interfaces:**
- Produces: `requeue_failed(session: AsyncSession, org_id: uuid.UUID, *, actor_id: uuid.UUID) -> int` — resets this org's `NotificationEmail` rows with `status=FAILED` to `PENDING` (`attempts=0`, `next_attempt_at/failed_at/last_error` cleared), logs a structured line, returns the count. Does NOT commit.
- Consumes: `NotificationEmailStatus.FAILED/PENDING` from `db.models._notification_enums`; `NotificationEmail` from `db.models.notification`; the `_config_update` dependency + `AppUser`/`get_session` already imported in `api/config.py`.

- [ ] **Step 1: Write the failing integration test**

Create `apps/api/tests/integration/test_notification_requeue.py`:

Create `apps/api/tests/integration/test_notification_requeue.py`. This reuses the exact auth helpers the sibling health-endpoint test uses: `_grant(subject, keys) -> uuid.UUID` (from `test_capa`, mints a user with SYSTEM-scope overrides, returns the user id) and `_auth(token_factory, subject)` (from `test_vault`, builds the bearer header). It stays on the **single shared org** — do NOT create a second `organization` (the `NotificationEmail.org_id` FK + the REVOKE-DELETE / `test_restore` `scalar_one` leaked-org trap). Cross-org isolation is a plain `org_id ==` WHERE clause left to code review + `diff-critic`; the meaningful correctness checks here are the **status filter** (only FAILED flips) and the **field reset**. Assertions are on the specific rows created (by id), so they're delta-safe against the shared DB; `requeued >= 1` because the org-wide action may also sweep other tests' stray FAILED rows.

```python
from __future__ import annotations

import datetime
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models._notification_enums import NotificationEmailKind, NotificationEmailStatus
from easysynq_api.db.models.app_user import AppUser
from easysynq_api.db.models.notification import NotificationEmail
from easysynq_api.db.session import get_sessionmaker

from .test_capa import _grant  # SYSTEM-scope PermissionOverride grant helper → user id
from .test_vault import _auth  # bearer-header builder

pytestmark = pytest.mark.integration


def _email(org_id: uuid.UUID, status: NotificationEmailStatus, **over: Any) -> NotificationEmail:
    return NotificationEmail(
        id=uuid.uuid4(),
        org_id=org_id,
        recipient_email="ops@example.com",
        subject="s",
        body="b",
        status=status,
        attempts=over.get("attempts", 5),
        next_attempt_at=over.get("next_attempt_at"),
        last_error=over.get("last_error", "smtp down"),
        failed_at=over.get("failed_at"),
        email_kind=NotificationEmailKind.SINGLE,
    )


async def test_requeue_resets_failed_and_leaves_other_statuses(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"rq-admin-{uuid.uuid4().hex[:8]}"
    user_id = await _grant(subject, ("config.update",))
    async with get_sessionmaker()() as s:
        caller = await s.get(AppUser, user_id)
        assert caller is not None
        org_id = caller.org_id

    now = datetime.datetime.now(datetime.UTC)
    async with get_sessionmaker()() as s:
        failed = _email(org_id, NotificationEmailStatus.FAILED, failed_at=now, attempts=5)
        sent = _email(org_id, NotificationEmailStatus.SENT)
        suppressed = _email(org_id, NotificationEmailStatus.SUPPRESSED)
        s.add_all([failed, sent, suppressed])
        await s.commit()
        failed_id, sent_id, suppressed_id = failed.id, sent.id, suppressed.id

    resp = await app_client.post(
        "/api/v1/admin/notifications/requeue-failed", headers=_auth(token_factory, subject)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["requeued"] >= 1

    async with get_sessionmaker()() as s:
        row = (
            await s.execute(select(NotificationEmail).where(NotificationEmail.id == failed_id))
        ).scalar_one()
        assert row.status == NotificationEmailStatus.PENDING
        assert row.attempts == 0
        assert row.next_attempt_at is None and row.failed_at is None and row.last_error is None
        # the status filter holds: SENT and SUPPRESSED rows are untouched
        sent_row = (
            await s.execute(select(NotificationEmail).where(NotificationEmail.id == sent_id))
        ).scalar_one()
        supp_row = (
            await s.execute(select(NotificationEmail).where(NotificationEmail.id == suppressed_id))
        ).scalar_one()
        assert sent_row.status == NotificationEmailStatus.SENT
        assert supp_row.status == NotificationEmailStatus.SUPPRESSED


async def test_requeue_forbidden_without_config_update(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"rq-noperm-{uuid.uuid4().hex[:8]}"
    await _grant(subject, ("document.read",))  # exists, but lacks config.update
    resp = await app_client.post(
        "/api/v1/admin/notifications/requeue-failed", headers=_auth(token_factory, subject)
    )
    assert resp.status_code == 403, resp.text
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd apps/api && uv run pytest tests/integration/test_notification_requeue.py -v`
Expected: FAIL — 404 (route doesn't exist yet).

- [ ] **Step 3: Implement the service**

Create `apps/api/src/easysynq_api/services/notifications/requeue.py`:

```python
"""S-cleanup-bundle #6: an admin ops-recovery action — reset this org's FAILED notification-email
rows to PENDING so the outbox drain retries them. Structured-log only (email is advisory; the
/tasks inbox is authoritative) — no audit_event, no WORM touch. Does NOT commit (the route commits)."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._notification_enums import NotificationEmailStatus
from ...db.models.notification import NotificationEmail

logger = logging.getLogger("easysynq.notifications.requeue")


async def requeue_failed(
    session: AsyncSession, org_id: uuid.UUID, *, actor_id: uuid.UUID
) -> int:
    """Reset org's FAILED delivery rows → PENDING (attempts=0 so the drain actually re-sends rather
    than immediately re-failing an already-exhausted row). Returns the number of rows requeued."""
    result = await session.execute(
        update(NotificationEmail)
        .where(
            NotificationEmail.org_id == org_id,
            NotificationEmail.status == NotificationEmailStatus.FAILED,
        )
        .values(
            status=NotificationEmailStatus.PENDING,
            attempts=0,
            next_attempt_at=None,
            failed_at=None,
            last_error=None,
        )
    )
    count = result.rowcount
    logger.info(
        "notifications.requeued",
        extra={"count": count, "org_id": str(org_id), "actor_id": str(actor_id)},
    )
    return count
```

- [ ] **Step 4: Add the route to `api/config.py`**

Add the import near the other `..services.notifications` imports:

```python
from ..services.notifications.requeue import requeue_failed
```

Add the route just after the `get_notification_health_endpoint` (keep it beside its sibling GET):

```python
@router.post("/admin/notifications/requeue-failed")
async def requeue_failed_endpoint(
    caller: AppUser = Depends(_config_update),
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    """Requeue this org's FAILED notification emails → PENDING so the outbox drain retries them.

    Ops-recovery action (structured-log only; email is advisory). Needs ``config.update``."""
    count = await requeue_failed(session, caller.org_id, actor_id=caller.id)
    await session.commit()
    return {"requeued": count}
```

- [ ] **Step 5: Run the integration test to verify it passes**

Run: `cd apps/api && uv run pytest tests/integration/test_notification_requeue.py -v`
Expected: PASS (both the happy path and the 403 negative test).

- [ ] **Step 6: Document the endpoint in openapi.yaml**

In `packages/contracts/openapi.yaml`, find the existing `/admin/notifications/health:` path item and add a sibling `/admin/notifications/requeue-failed:` path (mirror the health path's `tags`, security, and `AppUser`/`config.update` conventions):

```yaml
  /admin/notifications/requeue-failed:
    post:
      tags: [admin]
      summary: Requeue this org's FAILED notification emails
      description: >-
        Resets the caller org's FAILED delivery-ledger rows to PENDING so the outbox drain retries
        them. Ops-recovery action (structured-log only; email is advisory). Requires config.update.
      operationId: requeueFailedNotifications
      responses:
        "200":
          description: The number of delivery rows requeued.
          content:
            application/json:
              schema:
                type: object
                required: [requeued]
                properties:
                  requeued:
                    type: integer
        "403":
          description: Caller lacks config.update.
```

- [ ] **Step 7: Lint the contract**

Run: `npx --yes @redocly/cli lint packages/contracts/openapi.yaml` (or the repo's `/check-contracts` recipe).
Expected: no new errors introduced by the added path.

- [ ] **Step 8: Run the API fast loop**

Run: `cd apps/api && uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: clean.

- [ ] **Step 9: Commit**

```bash
git add apps/api/src/easysynq_api/services/notifications/requeue.py \
        apps/api/src/easysynq_api/api/config.py packages/contracts/openapi.yaml \
        apps/api/tests/integration/test_notification_requeue.py
git commit -m "feat(notifications): requeue failed emails, config.update-gated (survey #6, backend)

POST /admin/notifications/requeue-failed resets an org's FAILED delivery rows to
PENDING (attempts=0) so the drain retries them. Structured-log only, no audit
surface, no migration.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: #6 web — Requeue button + confirm modal on the health panel

**Files:**
- Modify: `apps/web/src/admin/hooks.ts` (add `useRequeueFailed`)
- Modify: `apps/web/src/admin/NotificationHealthPanel.tsx` (button + confirm modal + inline error)
- Modify: `apps/web/src/test/msw/handlers.ts` (base POST handler for the new endpoint)
- Modify: `apps/web/src/admin/NotificationHealthPanel.test.tsx` (button-visibility + confirm→post test)

**Interfaces:**
- Consumes: `requeue_failed` endpoint `POST /api/v1/admin/notifications/requeue-failed` → `{ requeued: number }`; `useApi().send`, `useMutation`, `useQueryClient`; `MutationErrorState` from `lib/states`; `useDisclosure` from `@mantine/hooks`; `Modal` from `@mantine/core`.

- [ ] **Step 1: Add the mutation hook**

Append to `apps/web/src/admin/hooks.ts`:

```ts
export function useRequeueFailed() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api.send<{ requeued: number }>("POST", "/api/v1/admin/notifications/requeue-failed"),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["notification-health"] });
    },
  });
}
```

- [ ] **Step 2: Add the base MSW handler**

In `apps/web/src/test/msw/handlers.ts`, alongside the existing `http.get("/api/v1/admin/notifications/health", …)` handler, add:

```ts
  http.post("/api/v1/admin/notifications/requeue-failed", () =>
    HttpResponse.json({ requeued: 0 }),
  ),
```

(Required so `onUnhandledRequest: "error"` doesn't fail other suites that mount the panel — the S-notify-7 base-handler lesson.)

- [ ] **Step 3: Write the failing panel test**

In `apps/web/src/admin/NotificationHealthPanel.test.tsx`, add (the `health(...)` helper + imports already exist at the top of the file):

Add `import userEvent from "@testing-library/user-event";` to the top of the file (static import, matching the rest of the suite), then add these tests:

```tsx
it("hides the requeue action when there are no failures", async () => {
  health({ email: { ...notificationHealthFixture.email, failed: 0 }, recent_failures: [] });
  renderWithProviders(<NotificationHealthPanel />);
  await screen.findByLabelText("Email delivery failures: 0");
  expect(screen.queryByRole("button", { name: "Requeue failed" })).not.toBeInTheDocument();
});

it("requeues failed emails after confirmation", async () => {
  const user = userEvent.setup();
  let posted = false;
  health({ email: { ...notificationHealthFixture.email, failed: 3 } });
  server.use(
    http.post("/api/v1/admin/notifications/requeue-failed", () => {
      posted = true;
      return HttpResponse.json({ requeued: 3 });
    }),
  );
  renderWithProviders(<NotificationHealthPanel />);
  await user.click(await screen.findByRole("button", { name: "Requeue failed" }));
  // confirm modal
  expect(await screen.findByText(/Requeue 3 failed email/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Requeue" }));
  await waitFor(() => expect(posted).toBe(true));
});
```

Add `waitFor` to the `@testing-library/react` import and ensure `import { expect, it } from "vitest"` is present (both already are — the file imports `describe, expect, it` from vitest).

- [ ] **Step 4: Run it to verify it fails**

Run: `cd apps/web && npx vitest run src/admin/NotificationHealthPanel.test.tsx`
Expected: FAIL — no "Requeue failed" button yet.

- [ ] **Step 5: Implement the button + confirm modal**

In `apps/web/src/admin/NotificationHealthPanel.tsx`:

Update imports:

```tsx
import { Alert, Button, Card, Group, Modal, SimpleGrid, Stack, Table, Text, Title } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { AsOf } from "../lib/AsOf";
import { EmptyState, ErrorState, LoadingState, MutationErrorState } from "../lib/states";
import { TONE_GLYPH } from "../lib/status";
import { formatRelativeTime } from "../lib/time";
import { useNotificationHealth, useRequeueFailed } from "./hooks";
```

Inside the component, after `const h = health.data;` add:

```tsx
  const [confirmOpen, confirm] = useDisclosure(false);
  const requeue = useRequeueFailed();
  const doRequeue = () =>
    requeue.mutate(undefined, { onSuccess: () => confirm.close() });
```

In the header `<Group gap="sm">` (beside Refresh), add the requeue button — render only when `failed > 0`:

```tsx
        <Group gap="sm">
          <AsOf at={health.dataUpdatedAt} prefix="Checked" />
          {failed > 0 && (
            <Button variant="light" size="compact-sm" onClick={confirm.open}>
              Requeue failed
            </Button>
          )}
          <Button
            variant="subtle"
            size="compact-sm"
            onClick={() => void health.refetch()}
            loading={health.isFetching}
          >
            Refresh
          </Button>
        </Group>
```

Just before the closing `</Stack>` of the component, add the confirm modal:

```tsx
      <Modal opened={confirmOpen} onClose={confirm.close} title="Requeue failed emails">
        <Stack gap="md">
          <Text size="sm">
            Requeue {failed} failed email{failed === 1 ? "" : "s"}? They'll be retried on the next
            delivery drain.
          </Text>
          {requeue.isError && <MutationErrorState title="Couldn't requeue" error={requeue.error} />}
          <Group justify="flex-end">
            <Button variant="default" size="sm" onClick={confirm.close}>
              Cancel
            </Button>
            <Button size="sm" onClick={doRequeue} loading={requeue.isPending}>
              Requeue
            </Button>
          </Group>
        </Stack>
      </Modal>
```

- [ ] **Step 6: Run the panel test to verify it passes**

Run: `cd apps/web && npx vitest run src/admin/NotificationHealthPanel.test.tsx`
Expected: PASS (all suites, including the two new ones).

- [ ] **Step 7: Full web loop**

Run: `cd apps/web && npm run lint && npx tsc --noEmit && npm run build && npx vitest run`
Expected: eslint clean, no type errors, build ok, all tests pass. (Strict `tsc` catches `noUncheckedIndexedAccess` nits a per-file run misses.)

- [ ] **Step 8: Commit**

```bash
git add apps/web/src/admin/hooks.ts apps/web/src/admin/NotificationHealthPanel.tsx \
        apps/web/src/test/msw/handlers.ts apps/web/src/admin/NotificationHealthPanel.test.tsx
git commit -m "feat(web): requeue-failed action on the delivery-health panel (survey #6, web)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final verification (before PR)

- [ ] **Backend gate:** `cd apps/api && uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest -m unit` — clean/green.
- [ ] **Migrations gate:** `/check-migrations` — round-trip still green (no migration added; confirms nothing drifted).
- [ ] **Integration:** `cd apps/api && uv run pytest -m integration tests/integration/test_partition_runway.py tests/integration/test_notification_requeue.py -v` — green (full `-m integration` is CI-authoritative on this box; run scoped locally).
- [ ] **Web gate:** `/check-web` (eslint + strict tsc + build + full vitest) — green.
- [ ] **Contract gate:** `/check-contracts` — redocly lint clean.
- [ ] **Adversarial review:** run the `diff-critic` agent on the branch diff, and the `web-test-trap-reviewer` agent on the web diff. Fold only confirmed findings.
- [ ] **Open the PR** via the `/pr` skill. On merge, delete the `audit-partition-runway-deadline` memory (its landmine is closed by Task 1).

## Self-review notes (author)

- **Spec coverage:** #19 → Task 1; #6 → Tasks 5+6; #15 → Task 3; #16 → Task 4; #17 → Task 2. All five covered.
- **No migration** holds across all tasks (confirmed: the `migrations` gate is a no-op round-trip).
- **Deviation from spec, documented:** #16 hook lives in `App` (covers `/admin` + `/setup`, which are outside `AppShell`), not in `AppShell` — a strict improvement on the spec's "centralized, low-touch" intent.
- **Type consistency:** `requeue_failed(session, org_id, *, actor_id)` (Task 5) ↔ `useRequeueFailed()` posting `{requeued:number}` (Task 6) ↔ the openapi `requeued: integer` (Task 5) all agree. `useRouteChrome(): void` (Task 4) is used identically in `App` and the test harness.
```
