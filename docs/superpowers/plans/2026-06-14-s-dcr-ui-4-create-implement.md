# S-dcr-ui-4 — CREATE-implement + CREATE resulting-doc deep-link — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the last DCR-UI residual — deep-link a CREATE DCR's resulting document and wire CREATE-implement into the cockpit.

**Architecture:** A detail-only `resulting_document_id` enrichment on `GET /dcrs/{id}` (derived from `document_version.document_id` — migration-free) lets the drawer deep-link a CREATE DCR's new document. A new `ImplementCreateDcrModal` (an Approved-document picker → FE-resolved Approved version → the existing `POST /dcrs/{id}/implement` with `resulting_version_id`) wires the cockpit Approved→Implement for CREATE; the picked-doc `document.release`/SoD-2 gate is surfaced calmly (submit-and-show), so no `_dcr_capabilities` change.

**Tech Stack:** FastAPI/Python 3.12 (api) · OpenAPI/redocly (contract) · React/TS + Mantine v7 + React Query + MSW + vitest + jest-axe (web).

**Spec:** `docs/superpowers/specs/2026-06-14-s-dcr-ui-4-create-implement-design.md`. **Gates:** `/check-api` + `/check-contracts` + `/check-web` (NO `/check-migrations` — migration-free; head stays `0051`).

**⚠ Windows reality:** on this box, the API **integration** suite + the full unit suite are **CI-only** (ProactorEventLoop / native crash). So Task 1's integration test is RED/GREEN-verified **in CI**, not locally; locally verify Task 1 via `/check-api` (ruff + mypy-strict + the unit subset) + `/check-contracts`. Web tasks run fully locally (`/check-web`, full vitest via `--pool=forks --poolOptions.forks.singleFork=true`).

---

### Task 1: Backend — `resulting_document_id` enrichment on `GET /dcrs/{id}` + contract

**Files:**
- Modify: `apps/api/src/easysynq_api/api/dcr.py:388-393` (`get_dcr_endpoint`)
- Modify: `packages/contracts/openapi.yaml` (the `Dcr` schema, after `resulting_version_id` ~line 6820-6823)
- Test (integration, CI-verified): `apps/api/tests/integration/test_dcr_implement.py` (extend `test_create_dcr_implement_then_sweep_then_close`)

- [ ] **Step 1: Write the failing assertions** — in `test_create_dcr_implement_then_sweep_then_close`, add a pre-implement null check (right after `dcr_id = await _drive_dcr_to_approved(... change_type="CREATE")`, line ~368) and a post-implement check (right after the `assert r.json()["resulting_version_id"] == rvid` at line ~379):

```python
    # ui-4: pre-implement the DCR has no resulting version → no resulting document.
    pre = await app_client.get(f"/api/v1/dcrs/{dcr_id}", headers=hreq)
    assert pre.status_code == 200, pre.text
    assert pre.json()["resulting_document_id"] is None
```

```python
    # ui-4: after implement, GET /dcrs/{id} surfaces the NEW document's id (detail-only enrichment).
    detail = await app_client.get(f"/api/v1/dcrs/{dcr_id}", headers=hreq)
    assert detail.status_code == 200, detail.text
    assert detail.json()["resulting_document_id"] == did
```

- [ ] **Step 2: Verify it fails (CI)** — locally this integration test cannot run (Windows ProactorEventLoop). Reason about the failure: `detail.json()["resulting_document_id"]` raises `KeyError` because `_dcr`/`get_dcr_endpoint` does not emit the field yet. Push and confirm the `integration` CI job goes RED on this assertion (or, before pushing, trust the failing-first logic — the key is absent). Document the intent in the commit.

- [ ] **Step 3: Implement the enrichment** — in `get_dcr_endpoint`, insert between the capabilities line and `return out`:

```python
    out["capabilities"] = await _dcr_capabilities(session, caller, dcr)
    # ui-4: surface the resulting version's parent document so the SPA can deep-link a CREATE DCR's new
    # document (there is no top-level version→document route, and _dcr can't expose it). Detail-only;
    # derived from the existing document_version.document_id FK (no migration). CREATE → the new doc;
    # REVISE → == target_document_id; RETIRE / pre-implement → None.
    resulting_document_id: str | None = None
    if dcr.resulting_version_id is not None:
        rv = await session.get(DocumentVersion, dcr.resulting_version_id)
        if rv is not None:
            resulting_document_id = str(rv.document_id)
    out["resulting_document_id"] = resulting_document_id
    return out
```

(`DocumentVersion` is already imported at `dcr.py:36` — no new import.)

- [ ] **Step 4: Add the contract field** — in `packages/contracts/openapi.yaml`, add to the `Dcr` schema `properties` immediately after the `resulting_version_id` block (~line 6823):

```yaml
        resulting_document_id:
          type: [string, "null"]
          format: uuid
          description: "The Document the resulting version belongs to (null until implement / for RETIRE). For CREATE this is the new Document; for REVISE it equals target_document_id. Detail-only (GET /dcrs/{id})."
```

(Do NOT add it to `required` — mirrors `resulting_version_id`. No `DcrImplement` change — it already documents `resulting_version_id`.)

- [ ] **Step 5: Verify locally + via CI**

Run: `/check-api` (ruff check + format-check + mypy-strict + unit) — Expected: PASS (no unit regressions; the new code type-checks).
Run: `/check-contracts` (redocly lint) — Expected: PASS.
CI: the `integration` job runs `test_create_dcr_implement_then_sweep_then_close` GREEN with the new assertions.

- [ ] **Step 6: Commit**

```bash
git add apps/api/src/easysynq_api/api/dcr.py packages/contracts/openapi.yaml apps/api/tests/integration/test_dcr_implement.py
git commit -m "feat(s-dcr-ui-4): expose resulting_document_id on GET /dcrs/{id} (detail-only, migration-free)"
```

---

### Task 2: FE — drawer deep-link for an implemented CREATE DCR

**Files:**
- Modify: `apps/web/src/lib/types.ts:1395` (add the field to the `Dcr` interface)
- Modify: `apps/web/src/features/dcr/DcrDrawer.tsx:133-135` (the Resulting-version CREATE branch)
- Test: `apps/web/src/features/dcr/DcrDrawer.test.tsx` (append)

- [ ] **Step 1: Add the FE type field** — in `types.ts`, in the `Dcr` interface, add immediately after the `resulting_version_id` line:

```typescript
  resulting_document_id?: string | null; // ui-4: detail-only (GET /dcrs/{id}); the document the resulting version belongs to
```

- [ ] **Step 2: Write the failing tests** — append to `DcrDrawer.test.tsx`:

```tsx
// ---- ui-4: the CREATE resulting-doc deep-link ----
const CREATE_DCR_ID = "dcrcre01-0001-0001-0001-000000000001";
const NEW_DOC_ID = "newdoc01-0001-0001-0001-000000000001";
function createImplementedDcr(): DcrDetail {
  return {
    id: CREATE_DCR_ID,
    identifier: "DCR-2026-0050",
    target_document_id: null, // CREATE
    change_type: "CREATE",
    change_significance: "MINOR",
    reason_class: "process_improvement",
    reason_text: "New SOP.",
    source_link_type: null,
    source_link_id: null,
    proposed_effective_from: null,
    resulting_version_id: "ver00001-0001-0001-0001-000000000001",
    resulting_document_id: NEW_DOC_ID,
    state: "Implemented",
    decision: null,
    created_by: "bbbb1111-1111-1111-1111-111111111111",
    created_at: "2026-05-01T09:00:00+00:00",
    stage_events: [],
    capabilities: { assess: false, route: false, implement: false, close: true },
  } satisfies DcrDetail;
}

it("deep-links an implemented CREATE DCR to its new document (resulting_document_id)", async () => {
  server.use(http.get("/api/v1/dcrs/:id", () => HttpResponse.json(createImplementedDcr())));
  const screen = renderWithProviders(<DcrDrawer dcrId={CREATE_DCR_ID} onClose={() => {}} />);
  const link = await screen.findByRole("link", { name: "View document" });
  expect(link.getAttribute("href")).toContain(`/documents/${NEW_DOC_ID}`);
  // CREATE has no predecessor → no visual-diff link.
  expect(screen.queryByRole("link", { name: /View visual diff/ })).not.toBeInTheDocument();
});

it("shows the bare resulting-version id for a CREATE DCR without resulting_document_id (defensive)", async () => {
  server.use(
    http.get("/api/v1/dcrs/:id", () =>
      HttpResponse.json({ ...createImplementedDcr(), resulting_document_id: null }),
    ),
  );
  const screen = renderWithProviders(<DcrDrawer dcrId={CREATE_DCR_ID} onClose={() => {}} />);
  await screen.findByText("DCR-2026-0050");
  expect(screen.queryByRole("link", { name: "View document" })).not.toBeInTheDocument();
});
```

- [ ] **Step 3: Run to verify they fail**

Run: `cd apps/web && npx vitest run src/features/dcr/DcrDrawer.test.tsx`
Expected: FAIL — the first test finds no "View document" link (the CREATE branch renders bare text).

- [ ] **Step 4: Implement the drawer branch** — in `DcrDrawer.tsx`, replace the CREATE fallback (the `) : (` + bare-id `<Text>` at lines 133-135) with:

```tsx
              ) : dcr.resulting_document_id ? (
                // ui-4: CREATE — _dcr now exposes the new document's id (resulting_document_id,
                // detail-only) → deep-link it. No visual diff (a new doc's first version has no
                // predecessor). Calm-degrades on a document.read 403 via the existing doc page.
                <Anchor component={Link} to={`/documents/${dcr.resulting_document_id}`}>
                  View document
                </Anchor>
              ) : (
                <Text size="sm">{dcr.resulting_version_id.slice(0, 8)}… (new document)</Text>
              )}
```

(The REVISE/RETIRE branch — `dcr.target_document_id ? (...)` — is unchanged.)

- [ ] **Step 5: Run to verify they pass**

Run: `cd apps/web && npx vitest run src/features/dcr/DcrDrawer.test.tsx`
Expected: PASS (all tests, including the existing CREATE/RETIRE visual-diff-hidden tests — unaffected since their fixtures set no `resulting_document_id`).

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/lib/types.ts apps/web/src/features/dcr/DcrDrawer.tsx apps/web/src/features/dcr/DcrDrawer.test.tsx
git commit -m "feat(s-dcr-ui-4): drawer deep-link to a CREATE DCR's new document"
```

---

### Task 3: FE — `ImplementCreateDcrModal` (Approved-document picker → resolve version → implement)

**Files:**
- Create: `apps/web/src/features/dcr/ImplementCreateDcrModal.tsx`
- Test: `apps/web/src/features/dcr/ImplementCreateDcrModal.test.tsx`

- [ ] **Step 1: Write the failing test** — create `ImplementCreateDcrModal.test.tsx`:

```tsx
import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import type { DocumentsPage, DocumentVersion } from "../../lib/types";
import { ImplementCreateDcrModal } from "./ImplementCreateDcrModal";

const DCR_ID = "dcr00001-0001-0001-0001-000000000001";
const DOC_ID = "doc00001-0001-0001-0001-000000000001";
const VER_ID = "ver00001-0001-0001-0001-000000000001";

const docsPage: DocumentsPage = {
  data: [
    {
      id: DOC_ID,
      identifier: "SOP-NEW-001",
      kind: "DOCUMENT",
      title: "New procedure",
      document_type_id: null,
      area_code: null,
      folder_path: null,
      current_state: "Approved",
      classification: "Internal",
      is_singleton: false,
      owner_user_id: "u1",
      framework_id: "f1",
      current_effective_version_id: null,
      effective_from: null,
      created_at: null,
      review_period_months: null,
      next_review_due: null,
      last_reviewed_at: null,
      review_state: null,
    },
  ],
  page: { limit: 200, offset: 0, returned: 1, has_more: false },
};

const versions: DocumentVersion[] = [
  {
    id: VER_ID,
    document_id: DOC_ID,
    version_seq: 1,
    revision_label: "1.0",
    version_state: "Approved",
    change_significance: "MINOR",
    change_reason: "initial",
    source_blob_sha256: "x",
    metadata_snapshot: null,
    author_user_id: "u1",
    effective_from: null,
    effective_to: null,
    superseded_by_version_id: null,
    created_at: null,
  },
];

function mockReads() {
  server.use(
    http.get("/api/v1/documents", () => HttpResponse.json(docsPage)),
    http.get(`/api/v1/documents/${DOC_ID}/versions`, () => HttpResponse.json(versions)),
  );
}

it("picks an approved document, resolves its Approved version, and POSTs resulting_version_id", async () => {
  mockReads();
  let body: unknown;
  server.use(
    http.post(`/api/v1/dcrs/${DCR_ID}/implement`, async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ id: DCR_ID, state: "Implemented" });
    }),
  );
  let closed = false;
  renderWithProviders(<ImplementCreateDcrModal dcrId={DCR_ID} onClose={() => (closed = true)} />);
  await userEvent.click(await screen.findByLabelText("New document"));
  await userEvent.click(await screen.findByText("SOP-NEW-001 — New procedure"));
  const submit = await screen.findByRole("button", { name: "Implement" });
  await waitFor(() => expect(submit).toBeEnabled()); // wait for the version fetch to resolve
  await userEvent.click(submit);
  await waitFor(() => expect(closed).toBe(true));
  expect(body).toEqual({ resulting_version_id: VER_ID });
});

it("shows an empty-candidate state when no approved documents exist", async () => {
  server.use(
    http.get("/api/v1/documents", () =>
      HttpResponse.json({ ...docsPage, data: [] } satisfies DocumentsPage),
    ),
  );
  renderWithProviders(<ImplementCreateDcrModal dcrId={DCR_ID} onClose={() => {}} />);
  expect(
    await screen.findByText(/Author the new document in the workspace first/),
  ).toBeInTheDocument();
});

it("surfaces a release/SoD-2 403 calmly (submit-and-show)", async () => {
  mockReads();
  server.use(
    http.post(`/api/v1/dcrs/${DCR_ID}/implement`, () =>
      HttpResponse.json(
        {
          code: "sod_violation",
          detail: "You authored this version; another approver must release it.",
        },
        { status: 403 },
      ),
    ),
  );
  renderWithProviders(<ImplementCreateDcrModal dcrId={DCR_ID} onClose={() => {}} />);
  await userEvent.click(await screen.findByLabelText("New document"));
  await userEvent.click(await screen.findByText("SOP-NEW-001 — New procedure"));
  const submit = await screen.findByRole("button", { name: "Implement" });
  await waitFor(() => expect(submit).toBeEnabled());
  await userEvent.click(submit);
  expect(await screen.findByText(/another approver must release it/)).toBeInTheDocument();
});

it("has no axe violations", async () => {
  mockReads();
  const { container } = renderWithProviders(
    <ImplementCreateDcrModal dcrId={DCR_ID} onClose={() => {}} />,
  );
  expect(await axe(container)).toHaveNoViolations();
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd apps/web && npx vitest run src/features/dcr/ImplementCreateDcrModal.test.tsx`
Expected: FAIL — module `./ImplementCreateDcrModal` does not exist.

- [ ] **Step 3: Implement the modal** — create `ImplementCreateDcrModal.tsx`:

```tsx
import { Alert, Button, Group, Loader, Modal, Select, Stack, Text } from "@mantine/core";
import { useMemo, useState } from "react";
import { ApiError } from "../../lib/api";
import { useDocumentVersions } from "../document/useDocumentVersions";
import { useDocuments } from "../library/useDocuments";
import { useImplementDcr } from "./mutations";

// CREATE-implement (ui-4): a CREATE DCR is the change-control record for a NEW controlled document
// authored out-of-band in the document workspace (Draft→Approved). Implement RELEASES that Approved
// version, so the user picks the approved document this DCR creates; we resolve its Approved version
// client-side and POST resulting_version_id. The capability (changeRequest.implement) can't know the
// picked doc's document.release/SoD-2 scope, so any 403/409 is surfaced calmly (submit-and-show).
export function ImplementCreateDcrModal({
  dcrId,
  onClose,
}: {
  dcrId: string;
  onClose: () => void;
}) {
  const m = useImplementDcr(dcrId);
  const [docId, setDocId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { data: docsPage, isError: docsError } = useDocuments(
    { current_state: "Approved" },
    { limit: 200, offset: 0 },
  );
  const options = useMemo(
    () =>
      (docsPage?.data ?? [])
        .filter((d) => d.kind === "DOCUMENT")
        .map((d) => ({ value: d.id, label: `${d.identifier} — ${d.title}` })),
    [docsPage],
  );

  const versions = useDocumentVersions(docId, docId !== null, { retry: false });
  const approvedVersion = (versions.data ?? []).find((v) => v.version_state === "Approved");
  const noApproved =
    docId !== null && !versions.isLoading && !versions.isError && approvedVersion === undefined;

  async function submit() {
    if (approvedVersion === undefined) return;
    setError(null);
    try {
      await m.mutateAsync({ resulting_version_id: approvedVersion.id });
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not implement the change request.");
    }
  }

  return (
    <Modal opened onClose={onClose} title="Implement new-document change request" size="lg">
      <Stack gap="sm">
        {error && <Alert color="red">{error}</Alert>}
        <Text size="sm">
          Pick the approved document authored to fulfil this change request. Implementing releases its
          approved version and links it here.
        </Text>
        {docsError ? (
          <Alert color="red">Couldn't load documents — you may not have access.</Alert>
        ) : options.length === 0 ? (
          <Text size="sm" c="dimmed">
            No approved documents to link. Author the new document in the workspace first, then return
            here to implement.
          </Text>
        ) : (
          <Select
            label="New document"
            required
            searchable
            placeholder="Pick the approved document this change request creates"
            value={docId}
            onChange={setDocId}
            data={options}
            nothingFoundMessage="No matching documents"
            comboboxProps={{ keepMounted: false }}
          />
        )}
        {docId !== null && versions.isLoading && <Loader size="sm" />}
        {noApproved && (
          <Text size="sm" c="red">
            That document has no approved version to release. Approve it first.
          </Text>
        )}
        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose}>
            Not yet
          </Button>
          <Button
            onClick={() => void submit()}
            loading={m.isPending}
            disabled={approvedVersion === undefined || m.isPending}
          >
            Implement
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd apps/web && npx vitest run src/features/dcr/ImplementCreateDcrModal.test.tsx`
Expected: PASS (all 4 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/dcr/ImplementCreateDcrModal.tsx apps/web/src/features/dcr/ImplementCreateDcrModal.test.tsx
git commit -m "feat(s-dcr-ui-4): ImplementCreateDcrModal — approved-doc picker + resolve version + submit-and-show"
```

---

### Task 4: FE — wire `DcrAdvancePanel` for CREATE-implement

**Files:**
- Modify: `apps/web/src/features/dcr/DcrAdvancePanel.tsx` (the `canImplement` gate, the CREATE note, the mounted modal, the import)
- Test: `apps/web/src/features/dcr/DcrAdvancePanel.test.tsx` (replace the old "Approved CREATE" test + add imports)

- [ ] **Step 1: Update the test** — in `DcrAdvancePanel.test.tsx`, add MSW imports at the top:

```tsx
import { http, HttpResponse } from "msw";
import userEvent from "@testing-library/user-event";
import { server } from "../../test/msw/server";
import type { DocumentsPage } from "../../lib/types";

const EMPTY_DOCS_PAGE: DocumentsPage = {
  data: [],
  page: { limit: 200, offset: 0, returned: 0, has_more: false },
};
```

Then REPLACE the existing "Approved CREATE: no Implement button — a workspace note instead" test (lines 60-68) with:

```tsx
it("Approved CREATE: shows Implement change + opens the new-document picker (no workspace dead-end)", async () => {
  server.use(http.get("/api/v1/documents", () => HttpResponse.json(EMPTY_DOCS_PAGE)));
  renderWithProviders(
    <DcrAdvancePanel
      dcr={dcr({ state: "Approved", change_type: "CREATE", target_document_id: null })}
    />,
  );
  // The old dead-end note is gone.
  expect(screen.queryByText(/document workspace/)).toBeNull();
  // The affordance now opens the CREATE picker modal.
  await userEvent.click(await screen.findByRole("button", { name: "Implement change" }));
  expect(
    await screen.findByText("Implement new-document change request"),
  ).toBeInTheDocument();
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd apps/web && npx vitest run src/features/dcr/DcrAdvancePanel.test.tsx`
Expected: FAIL — on old code, CREATE@Approved renders the workspace note and no "Implement change" button.

- [ ] **Step 3: Implement the wiring** — in `DcrAdvancePanel.tsx`:

(a) add the import (with the other `./` imports):

```tsx
import { ImplementCreateDcrModal } from "./ImplementCreateDcrModal";
```

(b) drop the CREATE exclusion on `canImplement` (line 31-32):

```tsx
  const canImplement = caps?.implement === true && dcr.state === "Approved";
```

(c) DELETE the CREATE workspace-note block (lines 56-60):

```tsx
      {caps?.implement === true && dcr.state === "Approved" && dcr.change_type === "CREATE" && (
        <Text size="sm" c="dimmed">
          New-document change requests are implemented from the document workspace.
        </Text>
      )}
```

(d) replace the mounted implement modal (lines 103-109) with a change-type branch:

```tsx
      {implementing &&
        (dcr.change_type === "CREATE" ? (
          <ImplementCreateDcrModal dcrId={dcr.id} onClose={() => setImplementing(false)} />
        ) : (
          <ImplementDcrModal
            dcrId={dcr.id}
            changeType={dcr.change_type}
            onClose={() => setImplementing(false)}
          />
        ))}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd apps/web && npx vitest run src/features/dcr/DcrAdvancePanel.test.tsx`
Expected: PASS (the updated CREATE test + all unchanged REVISE/RETIRE/terminal tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/dcr/DcrAdvancePanel.tsx apps/web/src/features/dcr/DcrAdvancePanel.test.tsx
git commit -m "feat(s-dcr-ui-4): wire the cockpit Approved->Implement for CREATE"
```

---

## Definition of done / wrap-up (not TDD tasks)

- [ ] **Carry-forward test (the parked stash):** apply `git stash@{0}` (the `DcrImpactTable.test.tsx` no-cross-DCR-leak pin from #132) onto this branch, run `npx vitest run src/features/dcr/DcrImpactTable.test.tsx` (expect PASS — the `${dcrId}` key already guarantees it), and commit as `test(dcr): carry-forward the no-cross-DCR-leak pin (from #132)`. If it no longer applies cleanly, drop it (the behavior is already covered by the keying).
- [ ] **Full gates:** `/check-web` (eslint + strict `tsc --noEmit` + build + full vitest via `--pool=forks --poolOptions.forks.singleFork=true`) — confirm the web count delta (≈ +8–10: DcrDrawer +2, ImplementCreateDcrModal +4, DcrAdvancePanel net +0/-… the CREATE test is replaced, +0; the carry-forward +1). `/check-api` + `/check-contracts` green locally.
- [ ] **Reviews:** run the `diff-critic` agent on the branch diff (migration-reviewer N/A — no migration). Run the `web-test-trap-reviewer` on the web diff. Fold only confirmed findings.
- [ ] **Live smoke (Chrome MCP; owner does the Keycloak login):** per spec s5 — build a CREATE DCR to Approved + author a new doc to Approved via service heredocs (grant overrides incl. `changeRequest.*` + `document.release`/`read`/`read_draft` to ALL org-AHT users); drive the cockpit Implement picker → release → Closed; verify the drawer deep-links to the new doc; confirm a SoD-conflict picked-doc surfaces the calm submit-and-show error.
- [ ] **PR → green CI → Codex triage** (poll reviews AND reactions after CI; verify each finding vs code; expect 2–5 rounds) → squash-merge on owner OK → `/finish-slice`.

## Self-review (done)
- **Spec coverage:** F1 enrichment → Task 1; F2 deep-link → Task 2; F2 CREATE-implement → Tasks 3+4; submit-and-show + no `_dcr_capabilities` change → Task 3 (`ApiError` calm error) + Task 4 (gate drops only the CREATE exclusion). All spec sections map to a task.
- **Type consistency:** `resulting_document_id?: string | null` on `Dcr` (Task 1 contract / Task 2 type) used in Task 2 drawer; `useDocuments(filters, page)` / `useDocumentVersions(documentId, enabled, opts?)` / `useImplementDcr(id).mutateAsync(DcrImplementBody)` signatures match the as-built code; `DocumentSummary`/`DocumentVersion`/`DocumentsPage` fixture fields match `types.ts`.
- **No placeholders:** every step has complete code or an exact command + expected result.
