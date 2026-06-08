# S-web-6 — Global Search + Compliance Checklist — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface the S10 search/reporting backend in the SPA — a ⌘K command palette + a `/search` results page, and a permission-gated `/compliance` Compliance Checklist.

**Architecture:** Front-end only (no migration, no new permission key, no `openapi.yaml` change). Two new feature folders (`features/search/`, `features/compliance/`) over already-contracted reads (`GET /search`, `/search/suggest`, `/reports/compliance-checklist`), plus thin shell wiring (TopBar trigger + AppShell hotkeys + a gated LeftRail entry + two routes). Dependency direction `features/* → app/shell → lib` (acyclic); reuses `StateBadge`, `usePermissions`, `useApi`.

**Tech Stack:** React 18 + Mantine 7 + TanStack Query 5 + react-router 7; tests = vitest + @testing-library/react + MSW + jest-axe. Spec: `docs/superpowers/specs/2026-06-08-web-track-s-web-6-search-and-compliance-design.md`.

**Conventions:** Run a single test file with `npm --prefix apps/web test -- <path-substring>`. Every component test that renders UI also asserts `await axe(...)` has no violations (release gate). Commit after each green task.

---

### Task 1: Types + MSW fixtures & handlers (foundation)

**Files:**
- Modify: `apps/web/src/lib/types.ts` (append)
- Modify: `apps/web/src/test/msw/handlers.ts` (add fixtures + 3 handlers)

- [ ] **Step 1: Append the response types**

Add to the end of `apps/web/src/lib/types.ts`:

```ts
// ---- S-web-6 (Global Search + Compliance Checklist) -------------------------------------

// GET /search → ranked metadata-plane hits (Effective documents only). `snippet` is PostgreSQL
// ts_headline output: matched terms wrapped in literal <b>…</b> (rendered safely, never as HTML).
export interface SearchHit {
  type: string; // "document" (the only indexed type in v1)
  id: string;
  identifier: string;
  title: string;
  current_state: DocumentCurrentState;
  clause_refs: string[];
  snippet: string;
  rank: number;
}

export interface SearchResults {
  query: string;
  results: SearchHit[];
  hidden_by_scope: number; // count of candidate hits the caller's access scope hid
}

// GET /search/suggest → lightweight identifier/title type-ahead.
export interface Suggestion {
  id: string;
  identifier: string;
  title: string;
}

// GET /reports/compliance-checklist — ★ mandatory-clause coverage (hard-gated
// report.compliance_checklist.read; 403 for callers without the key).
export type CoverageStatus = "COVERED" | "PARTIAL" | "GAP";

export interface ChecklistRollup {
  total: number;
  covered: number;
  partial: number;
  gap: number;
}

export interface ChecklistRow {
  clause_id: string;
  number: string;
  title: string;
  pdca_phase: PdcaPhase;
  mapped_count: number;
  effective_count: number;
  status: CoverageStatus;
}

export interface ComplianceChecklist {
  framework: string;
  rollup: ChecklistRollup;
  rows: ChecklistRow[];
}
```

- [ ] **Step 2: Add fixtures + handlers to MSW**

In `apps/web/src/test/msw/handlers.ts`, add these exported fixtures just above `export const handlers = [`:

```ts
// ---- S-web-6 search + compliance fixtures ----
export const searchFixture = {
  query: "supplier",
  results: [
    {
      type: "document",
      id: "11111111-1111-1111-1111-111111111111",
      identifier: "SOP-PUR-014",
      title: "Supplier Selection & Evaluation",
      current_state: "Effective",
      clause_refs: ["8.4"],
      snippet: "…<b>Supplier</b> Selection & Evaluation SOP-PUR-014",
      rank: 0.61,
    },
  ],
  hidden_by_scope: 2,
};

export const suggestFixture = {
  suggestions: [
    {
      id: "11111111-1111-1111-1111-111111111111",
      identifier: "SOP-PUR-014",
      title: "Supplier Selection & Evaluation",
    },
    {
      id: "22222222-2222-2222-2222-222222222222",
      identifier: "SOP-PRD-007",
      title: "Production Control",
    },
  ],
};

export const complianceFixture = {
  framework: "iso9001:2015",
  rollup: { total: 3, covered: 1, partial: 1, gap: 1 },
  rows: [
    { clause_id: "c43", number: "4.3", title: "Scope of the QMS", pdca_phase: "PLAN", mapped_count: 1, effective_count: 1, status: "COVERED" },
    { clause_id: "c62", number: "6.2", title: "Quality objectives", pdca_phase: "PLAN", mapped_count: 1, effective_count: 0, status: "PARTIAL" },
    { clause_id: "c84", number: "8.4", title: "External providers", pdca_phase: "DO", mapped_count: 0, effective_count: 0, status: "GAP" },
  ],
};
```

Then add these three handlers inside the `handlers` array (e.g. right after the `http.get("/api/v1/clauses", …)` line):

```ts
  // ---- S-web-6 search + compliance (default happy-path; per-test overrides for 403/empty) ----
  http.get("/api/v1/search", () => HttpResponse.json(searchFixture)),
  http.get("/api/v1/search/suggest", () => HttpResponse.json(suggestFixture)),
  http.get("/api/v1/reports/compliance-checklist", () => HttpResponse.json(complianceFixture)),
```

- [ ] **Step 3: Verify the whole suite + typecheck still pass**

Run: `npm --prefix apps/web run typecheck && npm --prefix apps/web test`
Expected: PASS (types compile; the existing suite is unaffected — the new handlers are additive and unused so far).

- [ ] **Step 4: Commit**

```bash
git add apps/web/src/lib/types.ts apps/web/src/test/msw/handlers.ts
git commit -m "feat(s-web-6): search/compliance response types + MSW fixtures"
```

---

### Task 2: Search hooks (`useSuggest`, `useSearch`)

**Files:**
- Create: `apps/web/src/features/search/hooks.ts`
- Test: `apps/web/src/features/search/hooks.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/features/search/hooks.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { expect, test } from "vitest";
import { AuthContext } from "../../lib/auth";
import { TEST_AUTH } from "../../test/render";
import { useSearch, useSuggest } from "./hooks";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <AuthContext.Provider value={TEST_AUTH}>{children}</AuthContext.Provider>
    </QueryClientProvider>
  );
}

test("useSearch returns the {results, hidden_by_scope} envelope", async () => {
  const { result } = renderHook(() => useSearch("supplier"), { wrapper });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data?.results).toHaveLength(1);
  expect(result.current.data?.results[0]?.identifier).toBe("SOP-PUR-014");
  expect(result.current.data?.hidden_by_scope).toBe(2);
});

test("useSearch is disabled for an empty/whitespace query", () => {
  const { result } = renderHook(() => useSearch("   "), { wrapper });
  expect(result.current.fetchStatus).toBe("idle");
});

test("useSuggest returns the suggestion list when q is non-empty", async () => {
  const { result } = renderHook(() => useSuggest("sop"), { wrapper });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data?.suggestions).toHaveLength(2);
});

test("useSuggest is disabled for an empty query", () => {
  const { result } = renderHook(() => useSuggest(""), { wrapper });
  expect(result.current.fetchStatus).toBe("idle");
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm --prefix apps/web test -- src/features/search/hooks.test.tsx`
Expected: FAIL — `./hooks` does not exist.

- [ ] **Step 3: Implement the hooks**

Create `apps/web/src/features/search/hooks.ts`:

```ts
import { useQuery } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type { SearchResults, Suggestion } from "../../lib/types";

// GET /search — ranked metadata-plane hits (Effective documents only). Filter-not-403: a caller
// who may read nothing gets results:[] + hidden_by_scope > 0, never an error.
export function useSearch(q: string) {
  const api = useApi();
  const term = q.trim();
  return useQuery({
    queryKey: ["search", term],
    queryFn: () => api.get<SearchResults>(`/api/v1/search?q=${encodeURIComponent(term)}&limit=25`),
    enabled: term.length >= 1,
  });
}

// GET /search/suggest — lightweight identifier/title type-ahead for the ⌘K palette.
export function useSuggest(q: string) {
  const api = useApi();
  const term = q.trim();
  return useQuery({
    queryKey: ["search-suggest", term],
    queryFn: () =>
      api.get<{ suggestions: Suggestion[] }>(
        `/api/v1/search/suggest?q=${encodeURIComponent(term)}&limit=10`,
      ),
    enabled: term.length >= 1,
  });
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `npm --prefix apps/web test -- src/features/search/hooks.test.tsx`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/search/hooks.ts apps/web/src/features/search/hooks.test.tsx
git commit -m "feat(s-web-6): useSearch + useSuggest hooks"
```

---

### Task 3: `Snippet` — XSS-safe highlight

**Files:**
- Create: `apps/web/src/features/search/Snippet.tsx`
- Test: `apps/web/src/features/search/Snippet.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/features/search/Snippet.test.tsx`:

```tsx
import { expect, test } from "vitest";
import { renderWithProviders } from "../../test/render";
import { Snippet } from "./Snippet";

test("wraps <b>…</b> segments in a <mark> and leaves the rest as text", () => {
  const { container } = renderWithProviders(<Snippet text="…<b>Supplier</b> Selection" />);
  const mark = container.querySelector("mark");
  expect(mark?.textContent).toBe("Supplier");
  expect(container.textContent).toContain("Selection");
});

test("renders embedded markup as literal text — no HTML injection", () => {
  const { container } = renderWithProviders(
    <Snippet text="Title <script>alert(1)</script> <b>hit</b>" />,
  );
  // The <script> is text, not a real element — so no <script> node was created.
  expect(container.querySelector("script")).toBeNull();
  expect(container.textContent).toContain("<script>alert(1)</script>");
  expect(container.querySelector("mark")?.textContent).toBe("hit");
});

test("renders nothing for an empty snippet", () => {
  const { container } = renderWithProviders(<Snippet text="" />);
  expect(container.textContent).toBe("");
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm --prefix apps/web test -- src/features/search/Snippet.test.tsx`
Expected: FAIL — `./Snippet` does not exist.

- [ ] **Step 3: Implement `Snippet`**

Create `apps/web/src/features/search/Snippet.tsx`:

```tsx
import { Mark, Text } from "@mantine/core";
import { Fragment } from "react";

// PostgreSQL ts_headline wraps matched terms in literal <b>…</b>. We split on those exact tokens and
// render every segment as a React TEXT node — interpreting ONLY <b>/</b> as highlight boundaries.
// Any other "<" (e.g. a "<script>" in a title) renders as a literal character, so the snippet can
// never inject HTML. No dangerouslySetInnerHTML.
export function Snippet({ text }: { text: string }) {
  if (!text) return null;
  const parts = text.split(/(<b>|<\/b>)/);
  let on = false;
  return (
    <Text span size="sm" c="dimmed">
      {parts.map((p, i) => {
        if (p === "<b>") {
          on = true;
          return null;
        }
        if (p === "</b>") {
          on = false;
          return null;
        }
        if (p === "") return null;
        return on ? <Mark key={i}>{p}</Mark> : <Fragment key={i}>{p}</Fragment>;
      })}
    </Text>
  );
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `npm --prefix apps/web test -- src/features/search/Snippet.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/search/Snippet.tsx apps/web/src/features/search/Snippet.test.tsx
git commit -m "feat(s-web-6): XSS-safe search snippet highlighter"
```

---

### Task 4: `SearchResultRow`

**Files:**
- Create: `apps/web/src/features/search/SearchResultRow.tsx`
- Test: `apps/web/src/features/search/SearchResultRow.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/features/search/SearchResultRow.test.tsx`:

```tsx
import { expect, test } from "vitest";
import type { SearchHit } from "../../lib/types";
import { renderWithProviders } from "../../test/render";
import { SearchResultRow } from "./SearchResultRow";

const hit: SearchHit = {
  type: "document",
  id: "11111111-1111-1111-1111-111111111111",
  identifier: "SOP-PUR-014",
  title: "Supplier Selection",
  current_state: "Effective",
  clause_refs: ["8.4"],
  snippet: "…<b>Supplier</b> Selection",
  rank: 0.6,
};

test("renders identifier, a title link to the document, and a clause chip link", () => {
  renderWithProviders(<SearchResultRow hit={hit} />);
  expect(screen.getByText("SOP-PUR-014")).toBeInTheDocument();
  const title = screen.getByRole("link", { name: "Supplier Selection" });
  expect(title).toHaveAttribute("href", "/documents/11111111-1111-1111-1111-111111111111");
  const clause = screen.getByRole("link", { name: /Clause 8.4/ });
  expect(clause).toHaveAttribute("href", "/library?clause=8.4");
});

test("renders the state badge and the highlighted snippet", () => {
  const { container } = renderWithProviders(<SearchResultRow hit={hit} />);
  expect(screen.getByLabelText("State: Effective")).toBeInTheDocument();
  expect(container.querySelector("mark")?.textContent).toBe("Supplier");
});
```

Add this import line at the top of the test (so `screen` resolves):

```tsx
import { screen } from "@testing-library/react";
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm --prefix apps/web test -- src/features/search/SearchResultRow.test.tsx`
Expected: FAIL — `./SearchResultRow` does not exist.

- [ ] **Step 3: Implement `SearchResultRow`**

Create `apps/web/src/features/search/SearchResultRow.tsx`:

```tsx
import { Anchor, Badge, Group, Stack, Text } from "@mantine/core";
import { Link } from "react-router-dom";
import type { SearchHit } from "../../lib/types";
import { StateBadge } from "../document/StateBadge";
import { Snippet } from "./Snippet";

export function SearchResultRow({ hit }: { hit: SearchHit }) {
  return (
    <Stack gap={4} py="xs" style={{ borderBottom: "1px solid var(--es-border)" }}>
      <Group gap="sm" wrap="nowrap">
        <Text ff="monospace" size="sm" c="dimmed">
          {hit.identifier}
        </Text>
        <Anchor component={Link} to={`/documents/${hit.id}`} fw={600}>
          {hit.title}
        </Anchor>
        <StateBadge state={hit.current_state} />
      </Group>
      {hit.clause_refs.length > 0 && (
        <Group gap={4}>
          {hit.clause_refs.map((c) => (
            <Anchor key={c} component={Link} to={`/library?clause=${encodeURIComponent(c)}`} underline="never">
              <Badge variant="light" size="sm">
                Clause {c}
              </Badge>
            </Anchor>
          ))}
        </Group>
      )}
      <Snippet text={hit.snippet} />
    </Stack>
  );
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `npm --prefix apps/web test -- src/features/search/SearchResultRow.test.tsx`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/search/SearchResultRow.tsx apps/web/src/features/search/SearchResultRow.test.tsx
git commit -m "feat(s-web-6): SearchResultRow (identifier · title · state · clause chips · snippet)"
```

---

### Task 5: `SearchResultsPage` (route `/search`)

**Files:**
- Create: `apps/web/src/features/search/SearchResultsPage.tsx`
- Test: `apps/web/src/features/search/SearchResultsPage.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/features/search/SearchResultsPage.test.tsx`:

```tsx
import { screen, waitFor } from "@testing-library/react";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { SearchResultsPage } from "./SearchResultsPage";

test("renders ranked rows + the hidden_by_scope footer for ?q=", async () => {
  renderWithProviders(<SearchResultsPage />, { route: "/search?q=supplier" });
  expect(await screen.findByRole("link", { name: "Supplier Selection & Evaluation" })).toBeInTheDocument();
  expect(screen.getByText(/2 hidden by your access scope/)).toBeInTheDocument();
  expect(screen.getByText(/Effective documents only/)).toBeInTheDocument();
});

test("prompts to type when ?q= is empty", () => {
  renderWithProviders(<SearchResultsPage />, { route: "/search" });
  expect(screen.getByText(/Type a query to search/)).toBeInTheDocument();
});

test("shows a calm no-results state", async () => {
  server.use(
    http.get("/api/v1/search", () =>
      HttpResponse.json({ query: "zzz", results: [], hidden_by_scope: 0 }),
    ),
  );
  renderWithProviders(<SearchResultsPage />, { route: "/search?q=zzz" });
  expect(await screen.findByText("No matching documents.")).toBeInTheDocument();
});

test("has no axe violations (results + empty)", async () => {
  const withResults = renderWithProviders(<SearchResultsPage />, { route: "/search?q=supplier" });
  await screen.findByRole("link", { name: "Supplier Selection & Evaluation" });
  expect(await axe(withResults.container)).toHaveNoViolations();
  withResults.unmount();

  const empty = renderWithProviders(<SearchResultsPage />, { route: "/search" });
  expect(await axe(empty.container)).toHaveNoViolations();
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm --prefix apps/web test -- src/features/search/SearchResultsPage.test.tsx`
Expected: FAIL — `./SearchResultsPage` does not exist.

- [ ] **Step 3: Implement `SearchResultsPage`**

Create `apps/web/src/features/search/SearchResultsPage.tsx`:

```tsx
import { Container, Loader, Stack, Text, Title } from "@mantine/core";
import { useSearchParams } from "react-router-dom";
import { useSearch } from "./hooks";
import { SearchResultRow } from "./SearchResultRow";

export function SearchResultsPage() {
  const [params] = useSearchParams();
  const q = params.get("q") ?? "";
  const term = q.trim();
  const { data, isLoading } = useSearch(q);

  return (
    <Container size="lg" py="md">
      <Title order={2} mb="md">
        Search
      </Title>
      {term.length === 0 ? (
        <Text c="dimmed">Type a query to search documents.</Text>
      ) : isLoading ? (
        <Loader />
      ) : (
        <Stack gap="xs">
          <Text c="dimmed" size="sm">
            Searches title, identifier &amp; clause refs — Effective documents only.
          </Text>
          {data && data.results.length === 0 ? (
            <Text>No matching documents.</Text>
          ) : (
            data?.results.map((hit) => <SearchResultRow key={hit.id} hit={hit} />)
          )}
          {data && data.hidden_by_scope > 0 && (
            <Text c="dimmed" size="sm">
              {data.hidden_by_scope} hidden by your access scope.
            </Text>
          )}
        </Stack>
      )}
    </Container>
  );
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `npm --prefix apps/web test -- src/features/search/SearchResultsPage.test.tsx`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/search/SearchResultsPage.tsx apps/web/src/features/search/SearchResultsPage.test.tsx
git commit -m "feat(s-web-6): /search results page (URL-driven, hidden_by_scope, a11y)"
```

---

### Task 6: `CommandPalette` (⌘K modal)

**Files:**
- Create: `apps/web/src/features/search/CommandPalette.tsx`
- Test: `apps/web/src/features/search/CommandPalette.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/features/search/CommandPalette.test.tsx`:

```tsx
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { useLocation } from "react-router-dom";
import { expect, test } from "vitest";
import { renderWithProviders } from "../../test/render";
import { CommandPalette } from "./CommandPalette";

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="loc">{loc.pathname + loc.search}</div>;
}

function open() {
  return renderWithProviders(
    <>
      <CommandPalette opened onClose={() => {}} />
      <LocationProbe />
    </>,
  );
}

test("typing shows /suggest results; selecting one navigates to the document", async () => {
  const user = userEvent.setup();
  open();
  await user.type(screen.getByLabelText("Search query"), "sop");
  const option = await screen.findByText("Supplier Selection & Evaluation");
  await user.click(option);
  await waitFor(() =>
    expect(screen.getByTestId("loc")).toHaveTextContent(
      "/documents/11111111-1111-1111-1111-111111111111",
    ),
  );
});

test("the footer action opens the full /search results page", async () => {
  const user = userEvent.setup();
  open();
  await user.type(screen.getByLabelText("Search query"), "calibration");
  await user.click(screen.getByText(/Search “calibration” →/));
  await waitFor(() =>
    expect(screen.getByTestId("loc")).toHaveTextContent("/search?q=calibration"),
  );
});

test("Enter with no selection runs the full search", async () => {
  const user = userEvent.setup();
  open();
  await user.type(screen.getByLabelText("Search query"), "pump{Enter}");
  await waitFor(() => expect(screen.getByTestId("loc")).toHaveTextContent("/search?q=pump"));
});

test("has no axe violations when open", async () => {
  open();
  await screen.findByLabelText("Search query");
  expect(await axe(document.body)).toHaveNoViolations();
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm --prefix apps/web test -- src/features/search/CommandPalette.test.tsx`
Expected: FAIL — `./CommandPalette` does not exist.

- [ ] **Step 3: Implement `CommandPalette`**

Create `apps/web/src/features/search/CommandPalette.tsx`:

```tsx
import { Loader, Modal, Stack, Text, TextInput, UnstyledButton } from "@mantine/core";
import { useState } from "react";
import type { KeyboardEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useSuggest } from "./hooks";

// A hand-rolled ⌘K command palette (no @mantine/spotlight dependency). The Mantine Modal supplies
// dialog semantics + focus-trap + Esc. Live /suggest rows give jump-to-doc; a fixed footer action
// opens the full /search results page. Keyboard: ↑/↓ move the active option (combobox +
// aria-activedescendant), Enter activates it. Selecting closes the palette and clears the query.
export function CommandPalette({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const navigate = useNavigate();
  const term = q.trim();
  const { data, isFetching } = useSuggest(q);
  const suggestions = data?.suggestions ?? [];
  // Option indices: 0..n-1 = suggestions, n = the "Search …" footer action.
  const optionCount = suggestions.length + 1;

  function close() {
    setQ("");
    setActive(0);
    onClose();
  }
  function goDoc(id: string) {
    close();
    navigate(`/documents/${id}`);
  }
  function goSearch() {
    if (term.length === 0) return;
    close();
    navigate(`/search?q=${encodeURIComponent(term)}`);
  }
  function activate(i: number) {
    if (i < suggestions.length) goDoc(suggestions[i].id);
    else goSearch();
  }
  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => Math.min(a + 1, optionCount - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(a - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      activate(active);
    }
  }
  const optionStyle = (selected: boolean) => ({
    padding: "8px 12px",
    width: "100%",
    textAlign: "left" as const,
    borderRadius: "var(--es-radius-sm)",
    background: selected ? "var(--es-surface-3)" : undefined,
  });

  return (
    <Modal opened={opened} onClose={close} title="Search documents" size="lg">
      <TextInput
        data-autofocus
        placeholder="Search by identifier or title…"
        aria-label="Search query"
        role="combobox"
        aria-expanded={optionCount > 0}
        aria-controls="cmdk-listbox"
        aria-activedescendant={`cmdk-opt-${active}`}
        value={q}
        onChange={(e) => {
          setQ(e.currentTarget.value);
          setActive(0);
        }}
        onKeyDown={onKeyDown}
        rightSection={isFetching ? <Loader size="xs" /> : null}
      />
      <Stack gap={2} mt="xs" id="cmdk-listbox" role="listbox" aria-label="Search results">
        {suggestions.map((s, i) => (
          <UnstyledButton
            key={s.id}
            id={`cmdk-opt-${i}`}
            role="option"
            aria-selected={active === i}
            onMouseEnter={() => setActive(i)}
            onClick={() => goDoc(s.id)}
            style={optionStyle(active === i)}
          >
            <Text span ff="monospace" size="sm" c="dimmed" mr="sm">
              {s.identifier}
            </Text>
            <Text span>{s.title}</Text>
          </UnstyledButton>
        ))}
        <UnstyledButton
          id={`cmdk-opt-${suggestions.length}`}
          role="option"
          aria-selected={active === suggestions.length}
          aria-disabled={term.length === 0}
          onMouseEnter={() => setActive(suggestions.length)}
          onClick={goSearch}
          style={optionStyle(active === suggestions.length)}
        >
          {term.length === 0 ? (
            <Text span c="dimmed">
              Type to search documents
            </Text>
          ) : (
            <Text span>Search “{term}” →</Text>
          )}
        </UnstyledButton>
      </Stack>
    </Modal>
  );
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `npm --prefix apps/web test -- src/features/search/CommandPalette.test.tsx`
Expected: PASS (4 tests). Note: Mantine respects `data-autofocus` inside a Modal to focus the input on open.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/search/CommandPalette.tsx apps/web/src/features/search/CommandPalette.test.tsx
git commit -m "feat(s-web-6): ⌘K command palette (suggest quick-jump + search footer)"
```

---

### Task 7: Shell wiring — TopBar trigger + AppShell hotkeys

**Files:**
- Modify: `apps/web/src/app/shell/TopBar.tsx`
- Modify: `apps/web/src/app/shell/AppShell.tsx`
- Test: `apps/web/src/app/shell/AppShell.test.tsx` (add a case)

- [ ] **Step 1: Write the failing test**

Add this test to `apps/web/src/app/shell/AppShell.test.tsx` (append; keep existing imports — add `userEvent` if not already imported):

```tsx
import userEvent from "@testing-library/user-event";

test("⌘K opens the command palette", async () => {
  const user = userEvent.setup();
  renderWithProviders(<AppShell />, { route: "/" });
  expect(screen.queryByLabelText("Search query")).not.toBeInTheDocument();
  await user.keyboard("{Meta>}k{/Meta}");
  expect(await screen.findByLabelText("Search query")).toBeInTheDocument();
});

test("clicking the TopBar search box opens the palette", async () => {
  const user = userEvent.setup();
  renderWithProviders(<AppShell />, { route: "/" });
  await user.click(screen.getByLabelText("Open search"));
  expect(await screen.findByLabelText("Search query")).toBeInTheDocument();
});
```

(If `screen` / `renderWithProviders` / `AppShell` aren't already imported in that file, add: `import { screen } from "@testing-library/react";`, `import { renderWithProviders } from "../../test/render";`, `import { AppShell } from "./AppShell";`.)

- [ ] **Step 2: Run it to verify it fails**

Run: `npm --prefix apps/web test -- src/app/shell/AppShell.test.tsx`
Expected: FAIL — no palette opens (TopBar box is still `disabled`; no hotkey bound).

- [ ] **Step 3: Update `TopBar` to trigger the palette**

Replace the search `TextInput` line and the props signature in `apps/web/src/app/shell/TopBar.tsx`. New file:

```tsx
import { ActionIcon, Burger, Group, Indicator, Menu, Text, TextInput } from "@mantine/core";
import type { KeyboardEvent } from "react";
import { useAuth } from "../../lib/auth";

// S-web-6: the ⌘K box is now live — it opens the command palette (AppShell owns the modal state).
export function TopBar({
  navOpened,
  onToggleNav,
  onOpenSearch,
}: {
  navOpened: boolean;
  onToggleNav: () => void;
  onOpenSearch: () => void;
}) {
  const { logout } = useAuth();
  function onSearchKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onOpenSearch();
    }
  }
  return (
    <Group h="100%" px="md" justify="space-between" wrap="nowrap">
      <Group gap="sm" wrap="nowrap">
        <Burger
          opened={navOpened}
          onClick={onToggleNav}
          hiddenFrom="md"
          size="sm"
          aria-label="Toggle navigation"
        />
        <Text fw={700}>EasySynQ</Text>
      </Group>
      <TextInput
        placeholder="Search (⌘K)"
        w={280}
        aria-label="Open search"
        readOnly
        onClick={onOpenSearch}
        onKeyDown={onSearchKeyDown}
        visibleFrom="sm"
        styles={{ input: { cursor: "pointer" } }}
      />
      <Group gap="xs" wrap="nowrap">
        <Indicator disabled>
          <ActionIcon variant="subtle" aria-label="Tasks">
            &#9684;
          </ActionIcon>
        </Indicator>
        <Indicator disabled>
          <ActionIcon variant="subtle" aria-label="Acknowledgements">
            &#128276;
          </ActionIcon>
        </Indicator>
        <Menu position="bottom-end">
          <Menu.Target>
            <ActionIcon variant="subtle" aria-label="Account">
              &#128100;
            </ActionIcon>
          </Menu.Target>
          <Menu.Dropdown>
            <Menu.Item onClick={logout}>Sign out</Menu.Item>
          </Menu.Dropdown>
        </Menu>
      </Group>
    </Group>
  );
}
```

- [ ] **Step 4: Wire the palette + hotkeys into `AppShell`**

Edit `apps/web/src/app/shell/AppShell.tsx`. Change the imports + body. The new file:

```tsx
import { AppShell as MantineAppShell } from "@mantine/core";
import { useDisclosure, useHotkeys } from "@mantine/hooks";
import { Outlet } from "react-router-dom";
import { CommandPalette } from "../../features/search/CommandPalette";
import { Breadcrumb } from "./Breadcrumb";
import { LeftRail } from "./LeftRail";
import { TopBar } from "./TopBar";

export function AppShell() {
  const [navOpened, { toggle: toggleNav }] = useDisclosure(false);
  const [searchOpened, { open: openSearch, close: closeSearch }] = useDisclosure(false);
  // ⌘K / Ctrl-K must fire even while focus is in an input (empty tagsToIgnore); "/" must NOT hijack
  // typing (the default ignore-list covers INPUT/TEXTAREA/SELECT). Hence two separate bindings.
  useHotkeys([["mod+K", openSearch]], []);
  useHotkeys([["/", openSearch]]);
  return (
    <MantineAppShell
      header={{ height: 60 }}
      navbar={{ width: 256, breakpoint: "md", collapsed: { mobile: !navOpened } }}
      padding="md"
    >
      {/* Skip-link: zIndex above the Mantine header (z-index 100) so keyboard focus isn't
          obscured by the sticky header (WCAG 2.2 Focus Not Obscured). */}
      <a
        href="#main-content"
        style={{
          position: "absolute",
          left: -9999,
          top: 8,
          zIndex: 9999,
          padding: "8px 12px",
          background: "var(--es-surface)",
          border: "1px solid var(--es-border)",
          borderRadius: "var(--es-radius-sm)",
        }}
        onFocus={(e) => (e.currentTarget.style.left = "8px")}
        onBlur={(e) => (e.currentTarget.style.left = "-9999px")}
      >
        Skip to content
      </a>
      <MantineAppShell.Header>
        <TopBar navOpened={navOpened} onToggleNav={toggleNav} onOpenSearch={openSearch} />
      </MantineAppShell.Header>
      <MantineAppShell.Navbar>
        <LeftRail />
      </MantineAppShell.Navbar>
      <MantineAppShell.Main id="main-content">
        <Breadcrumb />
        <Outlet />
      </MantineAppShell.Main>
      <CommandPalette opened={searchOpened} onClose={closeSearch} />
    </MantineAppShell>
  );
}
```

- [ ] **Step 5: Run it to verify it passes**

Run: `npm --prefix apps/web test -- src/app/shell/AppShell.test.tsx`
Expected: PASS (existing AppShell cases + the 2 new palette cases).

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/app/shell/TopBar.tsx apps/web/src/app/shell/AppShell.tsx apps/web/src/app/shell/AppShell.test.tsx
git commit -m "feat(s-web-6): wire ⌘K palette into the shell (TopBar trigger + hotkeys)"
```

---

### Task 8: `CoverageBadge`

**Files:**
- Create: `apps/web/src/features/compliance/CoverageBadge.tsx`
- Test: `apps/web/src/features/compliance/CoverageBadge.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/features/compliance/CoverageBadge.test.tsx`:

```tsx
import { screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { renderWithProviders } from "../../test/render";
import { CoverageBadge } from "./CoverageBadge";

test("renders a non-color label + glyph + aria-label per status", () => {
  renderWithProviders(
    <>
      <CoverageBadge status="COVERED" />
      <CoverageBadge status="PARTIAL" />
      <CoverageBadge status="GAP" />
    </>,
  );
  expect(screen.getByLabelText("Coverage: Covered")).toHaveTextContent("Covered");
  expect(screen.getByLabelText("Coverage: Partial")).toHaveTextContent("Partial");
  expect(screen.getByLabelText("Coverage: Gap")).toHaveTextContent("Gap");
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm --prefix apps/web test -- src/features/compliance/CoverageBadge.test.tsx`
Expected: FAIL — `./CoverageBadge` does not exist.

- [ ] **Step 3: Implement `CoverageBadge`**

Create `apps/web/src/features/compliance/CoverageBadge.tsx`:

```tsx
import { Badge } from "@mantine/core";
import type { CoverageStatus } from "../../lib/types";

// DP-7: status is never color-only — the label + glyph carry the meaning, color is the third
// redundant channel (mirrors StateBadge). Tokens from theme/tokens.css.
const META: Record<CoverageStatus, { label: string; mark: string; color: string }> = {
  COVERED: { label: "Covered", mark: "✓", color: "var(--es-success)" },
  PARTIAL: { label: "Partial", mark: "◔", color: "var(--es-warning)" },
  GAP: { label: "Gap", mark: "✕", color: "var(--es-danger)" },
};

export function CoverageBadge({ status }: { status: CoverageStatus }) {
  const { label, mark, color } = META[status];
  return (
    <Badge
      variant="light"
      color={color}
      leftSection={<span aria-hidden="true">{mark}</span>}
      aria-label={`Coverage: ${label}`}
    >
      {label}
    </Badge>
  );
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `npm --prefix apps/web test -- src/features/compliance/CoverageBadge.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/compliance/CoverageBadge.tsx apps/web/src/features/compliance/CoverageBadge.test.tsx
git commit -m "feat(s-web-6): CoverageBadge (COVERED/PARTIAL/GAP, non-color DP-7)"
```

---

### Task 9: `useComplianceChecklist` (403 = forbidden flag)

**Files:**
- Create: `apps/web/src/features/compliance/useComplianceChecklist.ts`
- Test: `apps/web/src/features/compliance/useComplianceChecklist.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/features/compliance/useComplianceChecklist.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { expect, test } from "vitest";
import { AuthContext } from "../../lib/auth";
import { server } from "../../test/msw/server";
import { TEST_AUTH } from "../../test/render";
import { useComplianceChecklist } from "./useComplianceChecklist";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <AuthContext.Provider value={TEST_AUTH}>{children}</AuthContext.Provider>
    </QueryClientProvider>
  );
}

test("returns the checklist rollup + rows on success", async () => {
  const { result } = renderHook(() => useComplianceChecklist(), { wrapper });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data?.rollup.total).toBe(3);
  expect(result.current.data?.rows).toHaveLength(3);
  expect(result.current.forbidden).toBe(false);
});

test("flags forbidden on a 403 (caller lacks report.compliance_checklist.read)", async () => {
  server.use(
    http.get("/api/v1/reports/compliance-checklist", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  const { result } = renderHook(() => useComplianceChecklist(), { wrapper });
  await waitFor(() => expect(result.current.forbidden).toBe(true));
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm --prefix apps/web test -- src/features/compliance/useComplianceChecklist.test.tsx`
Expected: FAIL — `./useComplianceChecklist` does not exist.

- [ ] **Step 3: Implement the hook**

Create `apps/web/src/features/compliance/useComplianceChecklist.ts`:

```ts
import { useQuery } from "@tanstack/react-query";
import { ApiError, useApi } from "../../lib/api";
import type { ComplianceChecklist } from "../../lib/types";

// GET /reports/compliance-checklist is hard-gated (report.compliance_checklist.read). A 403 is a
// first-class non-error outcome (the caller may simply lack the key) → surface a `forbidden` flag so
// the page renders a calm no-access panel instead of a generic error. retry:false (don't hammer a
// permission denial).
export function useComplianceChecklist() {
  const api = useApi();
  const query = useQuery({
    queryKey: ["compliance-checklist"],
    queryFn: () => api.get<ComplianceChecklist>("/api/v1/reports/compliance-checklist"),
    retry: false,
  });
  const forbidden = query.error instanceof ApiError && query.error.status === 403;
  return { ...query, forbidden };
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `npm --prefix apps/web test -- src/features/compliance/useComplianceChecklist.test.tsx`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/compliance/useComplianceChecklist.ts apps/web/src/features/compliance/useComplianceChecklist.test.tsx
git commit -m "feat(s-web-6): useComplianceChecklist (403 → forbidden flag, no retry)"
```

---

### Task 10: `CompliancePage` (route `/compliance`)

**Files:**
- Create: `apps/web/src/features/compliance/CompliancePage.tsx`
- Test: `apps/web/src/features/compliance/CompliancePage.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/features/compliance/CompliancePage.test.tsx`:

```tsx
import { screen, within } from "@testing-library/react";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { CompliancePage } from "./CompliancePage";

test("renders the rollup + ★ rows with a clause drill-through link", async () => {
  renderWithProviders(<CompliancePage />, { route: "/compliance" });
  expect(await screen.findByText("External providers")).toBeInTheDocument();
  // the 8.4 GAP row's clause cell links to the filtered Library
  const link = screen.getByRole("link", { name: /8.4/ });
  expect(link).toHaveAttribute("href", "/library?clause=8.4");
  // a GAP badge is present
  expect(screen.getByLabelText("Coverage: Gap")).toBeInTheDocument();
});

test("renders a calm no-access panel on a 403 (not a crash)", async () => {
  server.use(
    http.get("/api/v1/reports/compliance-checklist", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  renderWithProviders(<CompliancePage />, { route: "/compliance" });
  expect(await screen.findByText(/don’t have access/)).toBeInTheDocument();
});

test("has no axe violations (rows + 403)", async () => {
  const ok = renderWithProviders(<CompliancePage />, { route: "/compliance" });
  await screen.findByText("External providers");
  expect(await axe(ok.container)).toHaveNoViolations();
  ok.unmount();

  server.use(
    http.get("/api/v1/reports/compliance-checklist", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  const forbidden = renderWithProviders(<CompliancePage />, { route: "/compliance" });
  await screen.findByText(/don’t have access/);
  expect(await axe(forbidden.container)).toHaveNoViolations();
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm --prefix apps/web test -- src/features/compliance/CompliancePage.test.tsx`
Expected: FAIL — `./CompliancePage` does not exist.

- [ ] **Step 3: Implement `CompliancePage`**

Create `apps/web/src/features/compliance/CompliancePage.tsx`:

```tsx
import { Alert, Anchor, Container, Group, Loader, Table, Text, Title } from "@mantine/core";
import { Link } from "react-router-dom";
import { CoverageBadge } from "./CoverageBadge";
import { useComplianceChecklist } from "./useComplianceChecklist";

export function CompliancePage() {
  const { data, isLoading, isError, forbidden } = useComplianceChecklist();

  if (forbidden) {
    return (
      <Container size="md" py="md">
        <Title order={2} mb="md">
          Compliance Checklist
        </Title>
        <Alert color="gray" title="No access">
          You don’t have access to the Compliance Checklist. It’s available to the Quality Manager and
          Internal Auditor roles.
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
  if (isError || !data) {
    return (
      <Container size="md" py="md">
        <Title order={2} mb="md">
          Compliance Checklist
        </Title>
        <Alert color="red" title="Couldn’t load the checklist">
          Please try again.
        </Alert>
      </Container>
    );
  }

  const { rollup, rows } = data;
  return (
    <Container size="lg" py="md">
      <Title order={2} mb="xs">
        Compliance Checklist
      </Title>
      <Text c="dimmed" size="sm" mb="md">
        ★ mandatory-clause coverage ({data.framework}). Status against a rule — not a compliance verdict.
      </Text>
      <Group gap="sm" mb="md">
        <Text fw={600}>{rollup.total} mandatory items:</Text>
        <CoverageBadge status="COVERED" />
        <Text>{rollup.covered}</Text>
        <CoverageBadge status="PARTIAL" />
        <Text>{rollup.partial}</Text>
        <CoverageBadge status="GAP" />
        <Text>{rollup.gap}</Text>
      </Group>
      <Table striped highlightOnHover>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Clause</Table.Th>
            <Table.Th>Title</Table.Th>
            <Table.Th>Phase</Table.Th>
            <Table.Th>Mapped</Table.Th>
            <Table.Th>Effective</Table.Th>
            <Table.Th>Status</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {rows.map((r) => (
            <Table.Tr key={r.clause_id}>
              <Table.Td>
                <Anchor component={Link} to={`/library?clause=${encodeURIComponent(r.number)}`}>
                  ★ {r.number}
                </Anchor>
              </Table.Td>
              <Table.Td>{r.title}</Table.Td>
              <Table.Td>{r.pdca_phase}</Table.Td>
              <Table.Td>{r.mapped_count}</Table.Td>
              <Table.Td>{r.effective_count}</Table.Td>
              <Table.Td>
                <CoverageBadge status={r.status} />
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Container>
  );
}
```

(Note: the `within` import in the test is unused if you keep the asserts above — remove it if the linter flags it.)

- [ ] **Step 4: Run it to verify it passes**

Run: `npm --prefix apps/web test -- src/features/compliance/CompliancePage.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/compliance/CompliancePage.tsx apps/web/src/features/compliance/CompliancePage.test.tsx
git commit -m "feat(s-web-6): /compliance Checklist page (rollup, ★ rows, 403-calm, a11y)"
```

---

### Task 11: Gated LeftRail entry + routes

**Files:**
- Modify: `apps/web/src/app/shell/LeftRail.tsx`
- Modify: `apps/web/src/app/shell/LeftRail.test.tsx` (add 2 cases)
- Modify: `apps/web/src/App.tsx` (2 routes)
- Modify: `apps/web/src/App.test.tsx` (add 2 cases)

- [ ] **Step 1: Write the failing LeftRail tests**

Add to `apps/web/src/app/shell/LeftRail.test.tsx` (keep existing imports; add `http`, `HttpResponse`, `server` imports if absent):

```tsx
import { http, HttpResponse } from "msw";
import { server } from "../../test/msw/server";

test("hides the Compliance entry when the caller lacks report.compliance_checklist.read", async () => {
  // default MSW /me/permissions returns [] → no key
  renderWithProviders(<LeftRail />, { route: "/" });
  // let usePermissions settle
  await screen.findByText("Library");
  expect(screen.queryByText("Compliance")).not.toBeInTheDocument();
});

test("shows the gated Compliance entry when the caller holds the key", async () => {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: [{ key: "report.compliance_checklist.read", effect: "ALLOW", source: "role" }],
      }),
    ),
  );
  renderWithProviders(<LeftRail />, { route: "/" });
  expect(await screen.findByText("Compliance")).toBeInTheDocument();
});
```

(If `screen` / `renderWithProviders` / `LeftRail` aren't imported in that file, add them.)

- [ ] **Step 2: Run it to verify it fails**

Run: `npm --prefix apps/web test -- src/app/shell/LeftRail.test.tsx`
Expected: FAIL — the "shows" case can't find "Compliance".

- [ ] **Step 3: Add the gated entry to `LeftRail`**

In `apps/web/src/app/shell/LeftRail.tsx`, add the import and the gated NavLink. New file:

```tsx
import { Box, NavLink, Stack, Text } from "@mantine/core";
import { Link, useLocation } from "react-router-dom";
import type { PdcaPhase } from "../../lib/types";
import { usePermissions } from "./usePermissions";
import { useClauses } from "./useClauses";

const PHASES: PdcaPhase[] = ["PLAN", "DO", "CHECK", "ACT"];

export function LeftRail() {
  const { pathname } = useLocation();
  const { data: clauses } = useClauses();
  const { can } = usePermissions();
  return (
    <Stack gap="xs" p="sm">
      <NavLink component={Link} to="/" label="Home" active={pathname === "/"} />
      <NavLink
        component={Link}
        to="/library"
        label="Library"
        active={pathname.startsWith("/library")}
      />
      <NavLink
        component={Link}
        to="/tasks"
        label="Review & Approve"
        active={pathname.startsWith("/tasks")}
      />
      {can("report.compliance_checklist.read") && (
        // S-web-6: gated — only QMS Owner / Internal Auditor hold the SYSTEM report key.
        <NavLink
          component={Link}
          to="/compliance"
          label="Compliance"
          active={pathname.startsWith("/compliance")}
        />
      )}
      {PHASES.map((phase) => {
        const top = (clauses ?? []).filter((c) => c.pdca_phase === phase && c.parent_id === null);
        if (top.length === 0) return null;
        return (
          <Box key={phase} mt="sm">
            <Text size="xs" fw={700} c="dimmed" tt="uppercase" px="xs">
              {phase}
            </Text>
            {top.map((c) => (
              // S-web-2: a clause link filters the Library by that exact clause number.
              <NavLink
                key={c.id}
                component={Link}
                to={`/library?clause=${encodeURIComponent(c.number)}`}
                label={`${c.number} ${c.title}`}
              />
            ))}
          </Box>
        );
      })}
    </Stack>
  );
}
```

- [ ] **Step 4: Add the routes to `App.tsx`**

In `apps/web/src/App.tsx`, add the imports and the two routes under the `/` (AppShell) `<Route>`:

Add imports near the other feature imports:

```tsx
import { SearchResultsPage } from "./features/search/SearchResultsPage";
import { CompliancePage } from "./features/compliance/CompliancePage";
```

Add these two `<Route>` lines inside the `<Route path="/" element={…AppShell…}>` block (e.g. after the `tasks/:id` route):

```tsx
        <Route path="search" element={<SearchResultsPage />} />
        <Route path="compliance" element={<CompliancePage />} />
```

- [ ] **Step 5: Write the failing App route tests**

Add to `apps/web/src/App.test.tsx` (match the file's existing render helper — it renders `<App />` within providers; reuse that pattern):

```tsx
test("the /search route renders the results page", async () => {
  renderWithProviders(<App />, { route: "/search?q=supplier" });
  expect(await screen.findByRole("heading", { name: "Search" })).toBeInTheDocument();
});

test("the /compliance route renders the checklist", async () => {
  renderWithProviders(<App />, { route: "/compliance" });
  expect(await screen.findByRole("heading", { name: "Compliance Checklist" })).toBeInTheDocument();
});
```

(If the file uses a local render helper instead of `renderWithProviders`, use that one. Ensure `screen` + `App` are imported.)

- [ ] **Step 6: Run the suites to verify they pass**

Run: `npm --prefix apps/web test -- src/app/shell/LeftRail.test.tsx src/App.test.tsx`
Expected: PASS (gated entry shown/hidden; both routes resolve under the shell).

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/app/shell/LeftRail.tsx apps/web/src/app/shell/LeftRail.test.tsx apps/web/src/App.tsx apps/web/src/App.test.tsx
git commit -m "feat(s-web-6): gated Compliance nav entry + /search + /compliance routes"
```

---

### Task 12: Full web gate + docs + handoff

**Files:**
- Modify: `docs/slice-history.md` (append the S-web-6 entry)
- Modify: `CLAUDE.md` (refresh **Recent learnings** + **Current status**; also correct the stale "S-web-5 PR open" → MERGED)

- [ ] **Step 1: Run the full web CI loop**

Run: `npm --prefix apps/web run lint && npm --prefix apps/web run typecheck && npm --prefix apps/web run build && npm --prefix apps/web test`
Expected: all green (eslint clean, tsc clean, build OK, the full vitest suite incl. the new ~25–30 S-web-6 tests + jest-axe). Fix any lint/type nits inline (e.g. remove the unused `within`/`waitFor` import if flagged) and re-run.

- [ ] **Step 2: Add the slice-history entry**

Append an `- **S-web-6**` bullet under the "v1 WEB UI track" section of `docs/slice-history.md` summarizing: front-end only (no migration / no key / no openapi change — the S10 endpoints were already contracted); the ⌘K command palette (suggest quick-jump + "Search →" footer) + the `/search` URL-driven results page (ranked, hidden_by_scope footer, XSS-safe snippet, honesty hint, Effective-only/metadata-plane); the gated `/compliance` Checklist (RAG rollup + 20★ table + clause drill-through, 403-calm); the owner forks (palette+page · dedicated gated route · suggest quick-jump); honest deferrals (server facets, saved searches, content-plane search, exports, the Home center-hub tile); and the demo precondition (Checklist needs a QMS-Owner/Auditor login). Bump the test count.

- [ ] **Step 3: Refresh CLAUDE.md**

Add a `2026-06-08 — **S-web-6 …**` bullet to **Recent learnings** (newest first; demote the oldest if over ~12). Update **Current status**: mark S-web-5 **MERGED (#97)** (correcting the stale "PR open"), add S-web-6 as the new web-track head, and note migration head stays `0044`.

- [ ] **Step 4: Commit the docs**

```bash
git add docs/slice-history.md CLAUDE.md
git commit -m "docs(s-web-6): slice-history + CLAUDE.md (and mark S-web-5 merged)"
```

- [ ] **Step 5: Handoff for review (orchestrator, outside this plan)**

This is front-end only, so the relevant local gate is **web** (the api/migration/integration jobs are unaffected and Linux-CI-only on this box). Then: run the **diff-critic** agent on the branch diff (`Agent`, `subagent_type: diff-critic`), fold any confirmed findings, and open the PR with `/pr` (5 CI jobs; only `web` is materially exercised here — the other four should be no-ops/green). After green CI + diff-critic, squash-merge.

---

## Self-review notes (author)

- **Spec coverage:** palette (Task 6) + results page (Task 5) + row/snippet (Tasks 3–4) + hooks (Task 2) = spec §4.1–4.4; compliance hook/page/badge (Tasks 8–10) = §4.5–4.7; shell wiring + gated nav + routes (Tasks 7, 11) = §4.8; types/MSW (Task 1). Error/edge (§6) covered by the no-results, 403, XSS, and empty-q tests. A11y (§7) by the axe assertions in Tasks 5/6/10. Deferrals (§9) are not built — no tasks, by design.
- **No new backend / contract / migration** — consistent with the spec's "front-end only".
- **Type consistency:** `SearchHit`/`SearchResults`/`Suggestion`/`CoverageStatus`/`ChecklistRow`/`ComplianceChecklist` defined once (Task 1) and consumed verbatim; `forbidden` flag named consistently across Task 9 hook and Task 10 page; `onOpenSearch` prop name consistent across TopBar (Task 7) and AppShell (Task 7).
- **Known execution nits to watch:** remove any unused test imports the linter flags (`within`, `waitFor`); confirm the existing `App.test.tsx` render helper name before reusing it; Mantine Modal autofocus uses `data-autofocus` (already set).
