# S-dcr-ui-3 — DCR page-image visual diff + redline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give a REVISE DCR (Implemented/Closed) a deep-linkable `/dcrs/:id/diff` route that shows the page-image visual diff **and** the text/metadata redline of its resulting version against the version it supersedes, by reusing the S-web-4b document diff components verbatim.

**Architecture:** 100% front-end. A pure `resolvePredecessor` helper turns `(target document's version list, resulting_version_id)` into a pinned `(from, to)` pair; a new `DcrDiffPage` composes the existing `VisualDiffViewer` / `RedlineViewer` over that pair behind a `?mode=text|visual` toggle; the `DcrDrawer` gains a "View visual diff →" link; `App.tsx` mounts the route. No backend, no contract, no migration, no new key.

**Tech Stack:** React + TS, Mantine v7, React Router v6, TanStack Query, MSW + Vitest + Testing Library + jest-axe. Spec: `docs/superpowers/specs/2026-06-14-s-dcr-ui-3-dcr-visual-diff-design.md`.

**Verification baseline:** 823 web tests green. Gate: **`/check-web` only** (`cd apps/web; npx vitest run <file>` per task; the full `/check-web` before the PR — run vitest with `--pool=forks --poolOptions.forks.singleFork=true` for a clean signal).

**Conventions every test file MUST follow (engineering-patterns "Web SPA testing"):**
- `import { expect, it } from "vitest"` (or `test`) — the bare global `expect` is jest-typed; only `tsc` catches it.
- Pin every fixture via `satisfies <Type>` against the real serializer types in `lib/types.ts`.
- The first content assertion after render uses `await waitFor(...)` / `findBy*` (the skeleton-frame + navigate-flake false-PASS).
- `ApiError.message` is built from `problem.detail ?? title` — error fixtures use `detail`/`title`, not `message`.
- The global `apps/web/src/test/setup.ts` already stubs `URL.createObjectURL` → `"blob:mock"` + `revokeObjectURL` (the `VisualDiffViewer` tests rely on it) — no per-test stub needed.

---

### Task 1: `resolvePredecessor` pure helper

**Files:**
- Create: `apps/web/src/features/dcr/resolvePredecessor.ts`
- Test: `apps/web/src/features/dcr/resolvePredecessor.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// apps/web/src/features/dcr/resolvePredecessor.test.ts
import { describe, expect, it } from "vitest";
import type { DocumentVersion } from "../../lib/types";
import { resolvePredecessor } from "./resolvePredecessor";

function v(over: Partial<DocumentVersion> & { id: string; version_seq: number }): DocumentVersion {
  return {
    document_id: "doc",
    revision_label: `Rev ${over.version_seq}`,
    version_state: "Effective",
    change_significance: "MAJOR",
    change_reason: "",
    source_blob_sha256: "sha",
    metadata_snapshot: null,
    author_user_id: "u",
    effective_from: null,
    effective_to: null,
    superseded_by_version_id: null,
    created_at: null,
    ...over,
  };
}

describe("resolvePredecessor", () => {
  it("prefers the exact succession link (the version whose superseded_by points at the resulting one)", () => {
    const versions = [
      v({ id: "new", version_seq: 2 }),
      v({ id: "old", version_seq: 1, superseded_by_version_id: "new", version_state: "Superseded" }),
    ];
    expect(resolvePredecessor(versions, "new")).toEqual({ from: "old", to: "new" });
  });

  it("falls back to the immediate version_seq predecessor pre-cutover (no succession link yet)", () => {
    const versions = [
      v({ id: "new", version_seq: 2, version_state: "Approved" }), // pre-cutover: no succession link set
      v({ id: "eff", version_seq: 1, version_state: "Effective" }),
    ];
    expect(resolvePredecessor(versions, "new")).toEqual({ from: "eff", to: "new" });
  });

  it("resolves the predecessor of the GIVEN resulting version even when a later revision exists", () => {
    // A later REVISE produced seq 3; the DCR under view produced seq 2 → its predecessor is seq 1, NOT seq 2.
    const versions = [
      v({ id: "newest", version_seq: 3 }),
      v({ id: "mid", version_seq: 2 }),
      v({ id: "first", version_seq: 1 }),
    ];
    expect(resolvePredecessor(versions, "mid")).toEqual({ from: "first", to: "mid" });
  });

  it("returns null when the resulting version is absent from the list", () => {
    expect(resolvePredecessor([v({ id: "a", version_seq: 1 })], "missing")).toBeNull();
  });

  it("returns null when the resulting version has no predecessor", () => {
    expect(resolvePredecessor([v({ id: "only", version_seq: 1 })], "only")).toBeNull();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd apps/web; npx vitest run src/features/dcr/resolvePredecessor.test.ts`
Expected: FAIL — `resolvePredecessor` is not exported / module not found.

- [ ] **Step 3: Write the implementation**

```ts
// apps/web/src/features/dcr/resolvePredecessor.ts
import type { DocumentVersion } from "../../lib/types";

// S-dcr-ui-3: pin a DCR's diff to (predecessor → resulting). The resulting version's predecessor is
// the version it supersedes — known exactly post-cutover via superseded_by_version_id, and resolvable
// pre-cutover (resulting still Approved) as the immediate version_seq predecessor. Returns null when
// the resulting version isn't in the list or has no predecessor (a non-REVISE / first version).
export function resolvePredecessor(
  versions: DocumentVersion[],
  resultingVersionId: string,
): { from: string; to: string } | null {
  const resulting = versions.find((v) => v.id === resultingVersionId);
  if (!resulting) return null;

  // Exact succession link (set at cutover): the version this one supersedes.
  const bySuccession = versions.find((v) => v.superseded_by_version_id === resultingVersionId);
  if (bySuccession) return { from: bySuccession.id, to: resulting.id };

  // Pre-cutover fallback: the highest version_seq strictly below the resulting version's seq.
  const earlier = versions.filter((v) => v.version_seq < resulting.version_seq);
  if (earlier.length === 0) return null;
  const prev = earlier.reduce((a, b) => (b.version_seq > a.version_seq ? b : a));
  return { from: prev.id, to: resulting.id };
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd apps/web; npx vitest run src/features/dcr/resolvePredecessor.test.ts`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/dcr/resolvePredecessor.ts apps/web/src/features/dcr/resolvePredecessor.test.ts
git commit -m "feat(s-dcr-ui-3): resolvePredecessor — pin a DCR diff to (predecessor, resulting)"
```

---

### Task 2: `DcrDiffPage` — the route page (eligibility + redline default + visual toggle)

**Files:**
- Create: `apps/web/src/features/dcr/DcrDiffPage.tsx`
- Test: `apps/web/src/features/dcr/DcrDiffPage.test.tsx`

**Reused (read these to confirm the shapes):** `useDcr` (`features/dcr/hooks.ts` → `{data: DcrDetail, isLoading, isError, forbidden}`); `useDocumentVersions(documentId, enabled)` (`features/document/useDocumentVersions.ts` → `DocumentVersion[]`); `VisualDiffViewer` / `RedlineViewer` (`features/document/`, props `{documentId, fromVid, toVid}`); `DcrStateBadge`, `CHANGE_TYPE_LABEL` (`features/dcr/`). MSW: the default `GET /documents/:id/versions` → `versionFixture` (handlers.ts:2269), `…/versions/:vid/diff` → `diffFixture` (2272), `…/visual-diff` (POST+GET) → `visualDiffFixture` (2282/2285), `…/visual-diff/page/:page` → PNG (2289) all serve any id/vid, so a target_document_id of `11111111…` + a resulting version id present in `versionFixture` lines everything up.

- [ ] **Step 1: Write the failing test**

```tsx
// apps/web/src/features/dcr/DcrDiffPage.test.tsx
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import { Route, Routes } from "react-router-dom";
import type { DcrDetail } from "../../lib/types";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { DcrDiffPage } from "./DcrDiffPage";

const DCR_DIFF_ID = "dcrdiff1-0001-0001-0001-000000000001";

// An Implemented REVISE whose target_document_id + resulting_version_id ALIGN with versionFixture
// (doc 11111111…, versions dddd1111 [seq 2] superseded-from eeee1111 [seq 1]) so the diff handlers
// (which ignore the path ids) serve a coherent pair. Pinned to DcrDetail.
const reviseImplemented = {
  id: DCR_DIFF_ID,
  identifier: "DCR-2026-0010",
  target_document_id: "11111111-1111-1111-1111-111111111111",
  change_type: "REVISE",
  change_significance: "MAJOR",
  reason_class: "audit_finding",
  reason_text: "Audit finding closed via revision.",
  source_link_type: "finding",
  source_link_id: "find0001-0001-0001-0001-000000000001",
  proposed_effective_from: "2026-07-01T00:00:00+00:00",
  resulting_version_id: "dddd1111-1111-1111-1111-111111111111",
  state: "Implemented",
  decision: "Approved by the change board.",
  created_by: "bbbb1111-1111-1111-1111-111111111111",
  created_at: "2026-05-01T09:00:00+00:00",
  stage_events: [],
  capabilities: { assess: false, route: false, implement: false, close: true },
} satisfies DcrDetail;

function serveDcr(dcr: DcrDetail) {
  server.use(http.get("/api/v1/dcrs/:id", () => HttpResponse.json(dcr)));
}

function renderAt(id: string) {
  return renderWithProviders(
    <Routes>
      <Route path="/dcrs/:id/diff" element={<DcrDiffPage />} />
    </Routes>,
    { route: `/dcrs/${id}/diff` },
  );
}

it("renders the header and the text redline by default for a REVISE Implemented DCR", async () => {
  serveDcr(reviseImplemented);
  renderAt(DCR_DIFF_ID);

  // Header (waitFor — the page renders before useDcr resolves).
  await waitFor(() => expect(screen.getByText("DCR-2026-0010")).toBeInTheDocument());
  expect(screen.getByLabelText("State: Implemented")).toBeInTheDocument();

  // Default mode = Text → the RedlineViewer renders the metadata + text diff (from diffFixture).
  await waitFor(() =>
    expect(screen.getByText("Control-metadata changes")).toBeInTheDocument(),
  );
  expect(screen.getByText("Text redline")).toBeInTheDocument();
});

it("toggles to the visual page-image diff", async () => {
  serveDcr(reviseImplemented);
  const user = userEvent.setup();
  renderAt(DCR_DIFF_ID);

  await screen.findByText("Control-metadata changes"); // Text mode first
  // Click the SegmentedControl option by its text label — the proven house pattern
  // (VisualDiffViewer.test clicks getByText("After") on its layer SegmentedControl).
  await user.click(screen.getByText("Visual"));
  // The VisualDiffViewer streams the page image (visualDiffFixture: 3 pages, 1 & 2 changed).
  await screen.findByAltText("Page 2 of 3 — Diff layer (changed)");
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd apps/web; npx vitest run src/features/dcr/DcrDiffPage.test.tsx`
Expected: FAIL — `DcrDiffPage` not found.

- [ ] **Step 3: Write the implementation**

```tsx
// apps/web/src/features/dcr/DcrDiffPage.tsx
import { Alert, Anchor, Group, Loader, SegmentedControl, Stack, Text, Title } from "@mantine/core";
import { useMemo } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { ApiError } from "../../lib/api";
import { RedlineViewer } from "../document/RedlineViewer";
import { VisualDiffViewer } from "../document/VisualDiffViewer";
import { useDocumentVersions } from "../document/useDocumentVersions";
import { DcrStateBadge } from "./DcrStateBadge";
import { CHANGE_TYPE_LABEL } from "./labels";
import { useDcr } from "./hooks";
import { resolvePredecessor } from "./resolvePredecessor";

// S-dcr-ui-3: the page-image visual diff + text/metadata redline of a REVISE DCR's resulting version
// against the version it supersedes. Reuses the S-web-4b document diff components verbatim, pinned to
// the (predecessor → resulting) pair. The diff content is gated document.read_draft on the TARGET
// document (a separate key from changeRequest.read) — a reviewer without it sees a calm "no access".
export function DcrDiffPage() {
  const { id } = useParams();
  const dcrId = id ?? null;
  const { data: dcr, isLoading, isError } = useDcr(dcrId);
  const [params, setParams] = useSearchParams();
  const mode = params.get("mode") === "visual" ? "visual" : "text";

  const eligible =
    !!dcr &&
    dcr.change_type === "REVISE" &&
    dcr.resulting_version_id !== null &&
    dcr.target_document_id !== null;

  const versionsQ = useDocumentVersions(dcr?.target_document_id ?? null, eligible);
  const pair = useMemo(
    () =>
      eligible && versionsQ.data
        ? resolvePredecessor(versionsQ.data, dcr.resulting_version_id as string)
        : null,
    [eligible, versionsQ.data, dcr],
  );
  const versionsForbidden =
    versionsQ.error instanceof ApiError && versionsQ.error.status === 403;

  function setMode(value: string) {
    setParams((p) => {
      p.set("mode", value);
      return p;
    });
  }

  const back = dcrId ? `/dcrs?dcr=${dcrId}` : "/dcrs";

  if (isLoading) return <Loader />;
  if (isError || !dcr) {
    return (
      <Alert color="red" title="Couldn't load this change request">
        It may have been removed, or you may not have access.{" "}
        <Anchor component={Link} to="/dcrs">
          Back to change requests
        </Anchor>
      </Alert>
    );
  }

  return (
    <Stack gap="lg">
      <div>
        <Anchor component={Link} to={back} size="sm">
          <span aria-hidden="true">← </span>Back to change request
        </Anchor>
      </div>
      <Group gap="sm" align="center">
        <Title order={2}>{dcr.identifier}</Title>
        <Text c="dimmed">{CHANGE_TYPE_LABEL[dcr.change_type] ?? dcr.change_type}</Text>
        <DcrStateBadge state={dcr.state} />
      </Group>

      {!eligible ? (
        <Text c="dimmed">
          No visual diff for this change request. A visual diff is available only for a Revise change
          once it has been implemented.
        </Text>
      ) : versionsQ.isLoading ? (
        <Loader size="sm" />
      ) : versionsForbidden ? (
        <Text c="dimmed">You don't have access to this document's versions.</Text>
      ) : versionsQ.isError ? (
        <Text c="red">Couldn't load the document's versions.</Text>
      ) : !pair ? (
        <Text c="dimmed">No prior version to compare against.</Text>
      ) : (
        <Stack gap="sm">
          <SegmentedControl
            size="xs"
            aria-label="Diff mode"
            value={mode}
            onChange={setMode}
            data={[
              { value: "text", label: "Text" },
              { value: "visual", label: "Visual" },
            ]}
            w="fit-content"
          />
          {mode === "visual" ? (
            <VisualDiffViewer
              documentId={dcr.target_document_id as string}
              fromVid={pair.from}
              toVid={pair.to}
            />
          ) : (
            <RedlineViewer
              documentId={dcr.target_document_id as string}
              fromVid={pair.from}
              toVid={pair.to}
            />
          )}
        </Stack>
      )}
    </Stack>
  );
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd apps/web; npx vitest run src/features/dcr/DcrDiffPage.test.tsx`
Expected: PASS (2 tests). If the toggle selector role is wrong, fix per the Step-1 NOTE and re-run.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/dcr/DcrDiffPage.tsx apps/web/src/features/dcr/DcrDiffPage.test.tsx
git commit -m "feat(s-dcr-ui-3): DcrDiffPage — REVISE diff route (redline default + visual toggle)"
```

---

### Task 3: `DcrDiffPage` calm states + back-link + a11y

**Files:**
- Modify: `apps/web/src/features/dcr/DcrDiffPage.test.tsx` (append tests; the page from Task 2 already implements every branch — if a branch is missing, add it to `DcrDiffPage.tsx`)

- [ ] **Step 1: Write the failing tests (append to the file; reuse `reviseImplemented`, `serveDcr`, `renderAt`, `DCR_DIFF_ID`)**

Add these imports at the top of the test file if not already present: `import { axe } from "jest-axe";` · `import { useLocation } from "react-router-dom";`.

```tsx
const CREATE_DCR = { ...reviseImplemented, change_type: "CREATE", target_document_id: null } satisfies DcrDetail;
const OPEN_REVISE = { ...reviseImplemented, state: "Open", resulting_version_id: null } satisfies DcrDetail;

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="loc">{loc.pathname + loc.search}</div>;
}

it("shows a calm 'no visual diff' note for a non-REVISE change request", async () => {
  serveDcr(CREATE_DCR);
  renderAt(DCR_DIFF_ID);
  await waitFor(() => expect(screen.getByText(/No visual diff for this change request/)).toBeInTheDocument());
});

it("shows a calm 'no visual diff' note before a REVISE is implemented (no resulting version)", async () => {
  serveDcr(OPEN_REVISE);
  renderAt(DCR_DIFF_ID);
  await waitFor(() => expect(screen.getByText(/No visual diff for this change request/)).toBeInTheDocument());
});

it("calm-degrades to a no-access note when the viewer lacks document.read_draft on the target", async () => {
  serveDcr(reviseImplemented);
  server.use(
    http.get("/api/v1/documents/:id/versions", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  renderAt(DCR_DIFF_ID);
  await waitFor(() =>
    expect(screen.getByText("You don't have access to this document's versions.")).toBeInTheDocument(),
  );
});

it("shows 'no prior version' when the resulting version has no predecessor", async () => {
  serveDcr(reviseImplemented);
  server.use(
    // Only the resulting version exists in the list → resolvePredecessor returns null.
    http.get("/api/v1/documents/:id/versions", () =>
      HttpResponse.json([
        {
          id: "dddd1111-1111-1111-1111-111111111111",
          document_id: "11111111-1111-1111-1111-111111111111",
          version_seq: 1,
          revision_label: "Rev A",
          version_state: "Effective",
          change_significance: "MAJOR",
          change_reason: "Initial release",
          source_blob_sha256: "sha",
          metadata_snapshot: null,
          author_user_id: "bbbb1111-1111-1111-1111-111111111111",
          effective_from: null,
          effective_to: null,
          superseded_by_version_id: null,
          created_at: null,
        },
      ]),
    ),
  );
  renderAt(DCR_DIFF_ID);
  await waitFor(() =>
    expect(screen.getByText("No prior version to compare against.")).toBeInTheDocument(),
  );
});

it("the back-link returns to the register with the DCR drawer re-opened", async () => {
  serveDcr(reviseImplemented);
  const user = userEvent.setup();
  renderWithProviders(
    <Routes>
      <Route path="/dcrs/:id/diff" element={<DcrDiffPage />} />
      <Route path="/dcrs" element={<LocationProbe />} />
    </Routes>,
    { route: `/dcrs/${DCR_DIFF_ID}/diff` },
  );
  await screen.findByText("DCR-2026-0010");
  await user.click(screen.getByRole("link", { name: /Back to change request/ }));
  await waitFor(() =>
    expect(screen.getByTestId("loc")).toHaveTextContent(`/dcrs?dcr=${DCR_DIFF_ID}`),
  );
});

it("has no a11y violations (heading order)", async () => {
  serveDcr(reviseImplemented);
  const { container } = renderAt(DCR_DIFF_ID);
  await screen.findByText("Control-metadata changes");
  expect(await axe(container)).toHaveNoViolations();
});
```

- [ ] **Step 2: Run the tests**

Run: `cd apps/web; npx vitest run src/features/dcr/DcrDiffPage.test.tsx`
Expected: all PASS (Task 2's two + these six). If the a11y test reports a heading-order violation, adjust the page's `<Title order={...}>` so levels don't skip (the page should have a single `order={2}` heading; the reused viewers use `Text`/`Card`, not headings). If a calm branch is missing, add it to `DcrDiffPage.tsx` to match the spec.

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/features/dcr/DcrDiffPage.test.tsx apps/web/src/features/dcr/DcrDiffPage.tsx
git commit -m "test(s-dcr-ui-3): DcrDiffPage calm states, back-link, and a11y smoke"
```

---

### Task 4: `DcrDrawer` — the "View visual diff" link

**Files:**
- Modify: `apps/web/src/features/dcr/DcrDrawer.tsx` (the "Resulting version" `Field`, lines 112-125)
- Test: `apps/web/src/features/dcr/DcrDrawer.test.tsx` (extend the existing ui-1 test; if absent, create it mirroring `ManagementReviewDetailPage.test.tsx`'s render/MSW pattern with the existing default DCR handlers)

- [ ] **Step 1: Write the failing tests**

Add to `DcrDrawer.test.tsx`. The drawer fetches `GET /dcrs/:id` via `useDcr`; override it per case. (`DcrDrawer` takes `{ dcrId, onClose }` and renders inside a `DetailDrawer` — render it directly with `renderWithProviders(<DcrDrawer dcrId={...} onClose={() => {}} />)`; confirm the existing test's render call and reuse it.)

```tsx
import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import type { DcrDetail } from "../../lib/types";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { DcrDrawer } from "./DcrDrawer";

const ID = "dcr00009-0009-0009-0009-000000000009";
function base(): DcrDetail {
  return {
    id: ID,
    identifier: "DCR-2026-0009",
    target_document_id: "11111111-1111-1111-1111-111111111111",
    change_type: "REVISE",
    change_significance: "MAJOR",
    reason_class: "audit_finding",
    reason_text: "Revision needed.",
    source_link_type: null,
    source_link_id: null,
    proposed_effective_from: null,
    resulting_version_id: "dddd1111-1111-1111-1111-111111111111",
    state: "Implemented",
    decision: null,
    created_by: "bbbb1111-1111-1111-1111-111111111111",
    created_at: "2026-05-01T09:00:00+00:00",
    stage_events: [],
    capabilities: { assess: false, route: false, implement: false, close: true },
  };
}
function serve(dcr: DcrDetail) {
  server.use(http.get("/api/v1/dcrs/:id", () => HttpResponse.json(dcr)));
}

it("shows a 'View visual diff' link for an implemented REVISE", async () => {
  serve(base());
  renderWithProviders(<DcrDrawer dcrId={ID} onClose={() => {}} />);
  const link = await screen.findByRole("link", { name: /View visual diff/ });
  expect(link).toHaveAttribute("href", `/dcrs/${ID}/diff`);
});

it("hides the visual-diff link for a CREATE change request (no target document)", async () => {
  serve({ ...base(), change_type: "CREATE", target_document_id: null });
  renderWithProviders(<DcrDrawer dcrId={ID} onClose={() => {}} />);
  await screen.findByText("DCR-2026-0009"); // drawer loaded
  expect(screen.queryByRole("link", { name: /View visual diff/ })).not.toBeInTheDocument();
});

it("hides the visual-diff link for a RETIRE change request (no resulting version)", async () => {
  serve({ ...base(), change_type: "RETIRE", resulting_version_id: null });
  renderWithProviders(<DcrDrawer dcrId={ID} onClose={() => {}} />);
  await screen.findByText("DCR-2026-0009");
  expect(screen.queryByRole("link", { name: /View visual diff/ })).not.toBeInTheDocument();
});
```

> If `DcrDrawer.test.tsx` already exists, append only the three `it(...)` blocks + the `base()`/`serve()` helpers (avoid duplicate imports). Confirm the existing test's `renderWithProviders(<DcrDrawer .../>)` call shape and identifier-fixture and reuse them.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/web; npx vitest run src/features/dcr/DcrDrawer.test.tsx`
Expected: the first test FAILS — no "View visual diff" link yet. (The hide tests pass trivially.)

- [ ] **Step 3: Edit `DcrDrawer.tsx` — the "Resulting version" Field**

Replace the existing block (lines ~112-125):

```tsx
          {dcr.resulting_version_id ? (
            <Field label="Resulting version">
              {/* Links to the document, not the version: there is no SPA version route and a bare
                  version_id can't be resolved to its document_id client-side (verified). For CREATE
                  (no target_document_id) the new doc's id isn't exposed by _dcr → show the id, no link. */}
              {dcr.target_document_id ? (
                <Anchor component={Link} to={`/documents/${dcr.target_document_id}`}>
                  View document
                </Anchor>
              ) : (
                <Text size="sm">{dcr.resulting_version_id.slice(0, 8)}… (new document)</Text>
              )}
            </Field>
          ) : null}
```

with:

```tsx
          {dcr.resulting_version_id ? (
            <Field label="Resulting version">
              {/* Links to the document, not the version: there is no SPA version route and a bare
                  version_id can't be resolved to its document_id client-side (verified). For CREATE
                  (no target_document_id) the new doc's id isn't exposed by _dcr → show the id, no link. */}
              {dcr.target_document_id ? (
                <Group gap="md">
                  <Anchor component={Link} to={`/documents/${dcr.target_document_id}`}>
                    View document
                  </Anchor>
                  {/* S-dcr-ui-3: the page-image visual diff + redline of the resulting version vs its
                      predecessor — REVISE only (CREATE has no client version→doc resolution; RETIRE
                      has no resulting version). Gated document.read_draft on the target → calm-degrades. */}
                  {dcr.change_type === "REVISE" ? (
                    <Anchor component={Link} to={`/dcrs/${dcr.id}/diff`}>
                      View visual diff <span aria-hidden="true">→</span>
                    </Anchor>
                  ) : null}
                </Group>
              ) : (
                <Text size="sm">{dcr.resulting_version_id.slice(0, 8)}… (new document)</Text>
              )}
            </Field>
          ) : null}
```

(`Group` is already imported from `@mantine/core` in `DcrDrawer.tsx`; `Anchor`, `Link`, `Text` likewise. No new imports.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd apps/web; npx vitest run src/features/dcr/DcrDrawer.test.tsx`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/dcr/DcrDrawer.tsx apps/web/src/features/dcr/DcrDrawer.test.tsx
git commit -m "feat(s-dcr-ui-3): DcrDrawer — 'View visual diff' link for an implemented REVISE"
```

---

### Task 5: Mount the route + full gate

**Files:**
- Modify: `apps/web/src/App.tsx` (add the route after the `dcrs` register route, line ~149)

- [ ] **Step 1: Add the import**

In `App.tsx`, beside the existing DCR import (`import { DcrsRegisterPage } from "./features/dcr/DcrsRegisterPage";`) add:

```tsx
import { DcrDiffPage } from "./features/dcr/DcrDiffPage";
```

- [ ] **Step 2: Add the route**

After `<Route path="dcrs" element={<DcrsRegisterPage />} />` (line ~149), add:

```tsx
        <Route path="dcrs/:id/diff" element={<DcrDiffPage />} />
```

- [ ] **Step 3: Run the full web gate**

Run: `cd apps/web; npx eslint . ; npx tsc --noEmit ; npx vitest run --pool=forks --poolOptions.forks.singleFork=true`
Expected: eslint clean, `tsc --noEmit` clean (0 errors), vitest all green (823 baseline + the new tests, ~841–847 total). If `tsc` flags `noUncheckedIndexedAccess` on any new array access, guard it (the plan's helpers already avoid raw indexed access).

> If you prefer the project skill: run `/check-web` (eslint + tsc + build + test).

- [ ] **Step 4: Commit**

```bash
git add apps/web/src/App.tsx
git commit -m "feat(s-dcr-ui-3): mount /dcrs/:id/diff route"
```

---

## Self-review (run before handing off)

**Spec coverage:**
- F1 FE-only reuse → Tasks 2/4/5 (no backend). ✓
- F2 transient render → no code (backend already safe); the page shows the diff whenever `resulting_version_id` is set. ✓
- F3 full route `/dcrs/:id/diff` → Tasks 2/5. ✓
- F4 REVISE + Implemented/Closed → eligibility gate (Task 2) + drawer gating (Task 4). ✓
- F5 visual + redline via mode toggle → Task 2. ✓
- F6 impact-annotation out of scope → not touched. ✓
- Version-pair resolution → Task 1 (`resolvePredecessor`) + Task 2 wiring. ✓
- Calm-degrade (eligibility / 403 / no-predecessor) → Task 3. ✓
- a11y smoke + back-link → Task 3. ✓

**Type consistency:** `resolvePredecessor(versions, resultingVersionId): {from, to} | null` is used identically in Task 1 (def) and Task 2 (`pair = resolvePredecessor(versionsQ.data, dcr.resulting_version_id as string)`). `useDcr` → `{data: DcrDetail}`. `useDocumentVersions(documentId, enabled)` → `DocumentVersion[]`. Viewer props `{documentId, fromVid, toVid}` match Tasks 2's usage. ✓

**Placeholder scan:** none — every step has full code. The only flagged ambiguity (the SegmentedControl ARIA role for the toggle) carries an explicit resolve-one-way NOTE in Task 2. ✓

## Out of scope (named, not faked) — do NOT implement here
- CREATE-implement visual diff (no client `version_id→document_id`); impact-dimension annotation (`PUT /dcrs/{id}/impact` + `api.send` PUT widen); the `CapaApprovalContext` heading-order a11y fix (separate parked chip).
