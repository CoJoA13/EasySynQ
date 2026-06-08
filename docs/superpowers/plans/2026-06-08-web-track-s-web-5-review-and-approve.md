# S-web-5 — Review & Approve Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the reviewer/approver/releaser browser surface that closes UJ-3 (author → review → approve → release) on top of the already-contracted task/decision/release API.

**Architecture:** Front-end + **one** migration-free backend read (`GET /documents/{id}/approval`, gated `document.read`, reusing the existing `find_nonterminal_instance`-style resolver and the `WorkflowInstance` serializer). The SPA adds a `/tasks` inbox, a per-task Review & Approve page (redline + decision card), an Approvals stepper card on the document page, and the Release action. SoD is enforced server-side (HARD_DENY at the PEP); the UI only quiet-absents (DP-6).

**Tech Stack:** FastAPI / SQLAlchemy async (api) · React 18 + Mantine 7 + TanStack Query v5 + react-router 7 (web) · vitest + MSW + jest-axe (web tests) · pytest + testcontainers (api integration) · redocly (contracts).

**Spec:** `docs/superpowers/specs/2026-06-08-web-track-s-web-5-review-and-approve-design.md`. **No migration; no new permission key.**

---

## File structure

**Backend**
- `apps/api/src/easysynq_api/services/workflow/repository.py` — *modify*: add `latest_instance_for_subject`.
- `apps/api/src/easysynq_api/api/workflow.py` — *modify*: add `GET /documents/{document_id}/approval`.
- `packages/contracts/openapi.yaml` — *modify*: add the `/documents/{document_id}/approval` path.
- `apps/api/tests/integration/test_approval.py` — *modify*: add discovery-endpoint tests.

**Frontend — new**
- `apps/web/src/features/document/useDocumentApproval.ts`
- `apps/web/src/features/document/ApprovalStepper.tsx`
- `apps/web/src/features/document/ApprovalsTab.tsx`
- `apps/web/src/features/document/TaskStateBadge.tsx`
- `apps/web/src/features/review/hooks.ts`
- `apps/web/src/features/review/TasksInbox.tsx`
- `apps/web/src/features/review/ReviewApprovePage.tsx`
- `apps/web/src/features/review/DecisionCard.tsx`
- (+ a `*.test.tsx` beside each)

**Frontend — modify**
- `apps/web/src/lib/types.ts` — Task/WorkflowInstance/Decision types.
- `apps/web/src/lib/api.ts` — optional `headers` arg on `send` (for `Idempotency-Key`).
- `apps/web/src/features/authoring/hooks.ts` — `useReleaseDocument` + add `["document-approval"]` to `useInvalidateDocument`.
- `apps/web/src/App.tsx` — swap `tasks/:id`, add `tasks`.
- `apps/web/src/app/shell/LeftRail.tsx` — the "Review & Approve" nav link.
- `apps/web/src/features/document/DocumentDetailPage.tsx` — mount the Approvals card.
- `apps/web/src/test/msw/handlers.ts` — fixtures + default handlers for `/tasks`, `/tasks/:id`, `/tasks/:id/decision`, `/workflow-instances/:id`, `/documents/:id/approval`, `/documents/:id/release`.

---

## Task 1: Backend — the approval-discovery endpoint

**Files:**
- Modify: `apps/api/src/easysynq_api/services/workflow/repository.py`
- Modify: `apps/api/src/easysynq_api/api/workflow.py`
- Modify: `packages/contracts/openapi.yaml`
- Test: `apps/api/tests/integration/test_approval.py`

- [ ] **Step 1: Write the failing integration tests** — append to `apps/api/tests/integration/test_approval.py` (the imports `uuid`, `_auth`, `_create`, `s5`, `get_sessionmaker`, `select`, `WorkflowInstance` are already imported there; add `from .test_vault import _ensure_user`):

```python
async def test_document_approval_returns_instance_with_tasks(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """GET /documents/{id}/approval returns the active instance + its APPROVE task."""
    await s5.grant_lifecycle(subj.a)
    await s5.grant_role(subj.b, "Approver")
    ha = _auth(token_factory, subj.a)
    did = await _to_in_review(app_client, ha, await s5.type_id("SOP"))

    r = await app_client.get(f"/api/v1/documents/{did}/approval", headers=ha)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body is not None
    assert body["subject_id"] == did
    assert body["subject_type"] == "DOCUMENT"
    assert body["current_state"] == "IN_APPROVAL"
    assert len(body["tasks"]) == 1
    assert body["tasks"][0]["type"] == "APPROVE"
    assert body["tasks"][0]["state"] == "PENDING"


async def test_document_approval_null_when_never_submitted(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """A fresh Draft (never submitted) has no cycle → 200 with a null body (calm, not 404)."""
    await s5.grant_lifecycle(subj.a)
    ha = _auth(token_factory, subj.a)
    did = (await _create(app_client, ha, await s5.type_id("SOP")))["id"]

    r = await app_client.get(f"/api/v1/documents/{did}/approval", headers=ha)
    assert r.status_code == 200, r.text
    assert r.json() is None


async def test_document_approval_404_for_unknown_document(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await s5.grant_lifecycle(subj.a)
    ha = _auth(token_factory, subj.a)
    r = await app_client.get(f"/api/v1/documents/{uuid.uuid4()}/approval", headers=ha)
    assert r.status_code == 404, r.text


async def test_document_approval_403_without_document_read(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """A provisioned user with no grants is denied (deny-by-default)."""
    await s5.grant_lifecycle(subj.a)
    await s5.grant_role(subj.b, "Approver")
    ha = _auth(token_factory, subj.a)
    did = await _to_in_review(app_client, ha, await s5.type_id("SOP"))

    stranger = f"kc-stranger-{uuid.uuid4().hex[:8]}"
    async with get_sessionmaker()() as s:
        await _ensure_user(s, stranger)
        await s.commit()
    hs = _auth(token_factory, stranger)
    r = await app_client.get(f"/api/v1/documents/{did}/approval", headers=hs)
    assert r.status_code == 403, r.text


async def test_document_approval_surfaces_needs_attention_instance(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """Submit with NO approver-role holder → empty pool → NEEDS_ATTENTION instance, STILL returned
    (the discovery read is 'latest', not 'non-terminal')."""
    await s5.grant_lifecycle(subj.a)  # author only; nobody holds the Approver role
    ha = _auth(token_factory, subj.a)
    did = await _to_in_review(app_client, ha, await s5.type_id("SOP"))

    r = await app_client.get(f"/api/v1/documents/{did}/approval", headers=ha)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body is not None
    assert body["current_state"] == "NEEDS_ATTENTION"
```

- [ ] **Step 2: Run them to verify they fail** — `cd apps/api && uv run pytest tests/integration/test_approval.py -k document_approval -m integration -q`. Expected: FAIL/404 (route not defined → FastAPI returns 404 for all, so the 404 test passes but the others fail on assertions).

- [ ] **Step 3: Add the repo helper** — in `repository.py`, after `find_nonterminal_instance`:

```python
async def latest_instance_for_subject(
    session: AsyncSession,
    org_id: uuid.UUID,
    subject_type: WorkflowSubjectType,
    subject_id: uuid.UUID,
) -> WorkflowInstance | None:
    """The most recent workflow instance for a subject (ANY state), newest first — the document→
    approval discovery read. Unlike ``find_nonterminal_instance`` it does NOT filter terminal states:
    a released document's instance lingers as ``APPROVED`` (release never closes it) and a
    ``NEEDS_ATTENTION`` (empty-pool) instance must still surface, so 'latest' is the correct lens for
    the approval stepper."""
    return (
        await session.execute(
            select(WorkflowInstance)
            .where(
                WorkflowInstance.org_id == org_id,
                WorkflowInstance.subject_type == subject_type,
                WorkflowInstance.subject_id == subject_id,
            )
            .order_by(WorkflowInstance.started_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
```

- [ ] **Step 4: Add the endpoint** — in `api/workflow.py`, after `get_instance_endpoint` (all needed imports — `vault_repo`, `DocumentType`, `ResourceContext`, `enforce`, `get_authz_audit_sink`, `WorkflowSubjectType`, `get_current_user`, `get_session`, `Request` — already exist in the module):

```python
@router.get("/documents/{document_id}/approval", tags=["documents"])
async def get_document_approval_endpoint(
    document_id: uuid.UUID,
    request: Request,
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    authz_sink: AuthzAuditSink = Depends(get_authz_audit_sink),
) -> dict[str, Any] | None:
    """The document's current approval cycle — the LATEST workflow instance for the document + its
    tasks, or ``null`` when it was never submitted. Gated ``document.read`` on the subject (the closed
    catalog has no ``task.*``/``workflow.*`` key — same gate as ``GET /workflow-instances/{id}``)."""
    doc = await vault_repo.get_document(session, document_id)
    if doc is None or doc.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="Document not found")
    level: str | None = None
    if doc.document_type_id:
        dt = await session.get(DocumentType, doc.document_type_id)
        level = dt.document_level.value if dt else None
    resource = ResourceContext(
        artifact_id=str(doc.id), folder_path=doc.folder_path, document_level=level
    )
    await enforce(session, authz_sink, request, caller, "document.read", resource)
    instance = await wf_repo.latest_instance_for_subject(
        session, caller.org_id, WorkflowSubjectType.DOCUMENT, doc.id
    )
    if instance is None:
        return None
    tasks = await wf_repo.list_instance_tasks(session, instance.id)
    return _instance(instance, tasks)
```

- [ ] **Step 5: Add the OpenAPI path** — in `packages/contracts/openapi.yaml`, in the `paths:` block (e.g. right after `/documents/{document_id}/release`):

```yaml
  /documents/{document_id}/approval:
    get:
      tags: [documents]
      operationId: getDocumentApproval
      summary: "The document's current approval cycle (latest instance + tasks), or null if none. Gated document.read."
      parameters:
        - { name: document_id, in: path, required: true, schema: { type: string, format: uuid } }
      responses:
        "200":
          description: "The latest workflow instance for the document (tasks expanded), or null."
          content:
            application/json:
              schema:
                oneOf:
                  - { $ref: "#/components/schemas/WorkflowInstance" }
                  - { type: "null" }
        "403": { $ref: "#/components/responses/ProblemResponse" }
        "404": { $ref: "#/components/responses/ProblemResponse" }
```

- [ ] **Step 6: Run the tests + contracts + lint** — `uv run pytest tests/integration/test_approval.py -k document_approval -m integration -q` (Expected: PASS), then `uv run ruff check . && uv run ruff format --check . && uv run mypy src` (Expected: clean), then `/check-contracts` (redocly lint — Expected: clean).

- [ ] **Step 7: Commit**

```bash
git add apps/api/src/easysynq_api/services/workflow/repository.py apps/api/src/easysynq_api/api/workflow.py packages/contracts/openapi.yaml apps/api/tests/integration/test_approval.py
git commit -m "feat(s-web-5): document approval-discovery read (GET /documents/{id}/approval)"
```

---

## Task 2: Frontend types + the `Idempotency-Key`-capable api client

**Files:**
- Modify: `apps/web/src/lib/types.ts`
- Modify: `apps/web/src/lib/api.ts`

- [ ] **Step 1: Extend `types.ts`** — append:

```ts
// ---- S-web-5 (Review & Approve) ---------------------------------------------------------
export type TaskState = "PENDING" | "CLAIMED" | "DONE" | "SKIPPED" | "ESCALATED" | "EXPIRED";
export type TaskType =
  | "APPROVE" | "REVIEW" | "PERIODIC_REVIEW" | "AUDIT_TASK" | "FINDING_ACK"
  | "CAPA_STAGE" | "CAPA_ACTION" | "VERIFY" | "MR_INPUT" | "MR_ACTION" | "DCR_TRIAGE";

export interface Task {
  id: string;
  instance_id: string;
  stage_key: string;
  type: TaskType;
  state: TaskState;
  assignee_user_id: string | null;
  candidate_pool: string[] | null;
  action_expected: string | null;
  due_at: string | null;
}

// current_state is free-form Text server-side — keep it an open string, do NOT enum-validate.
export type WorkflowInstanceState =
  | "IN_APPROVAL" | "APPROVED" | "REJECTED_TO_DRAFT" | "NEEDS_ATTENTION" | (string & {});

export interface WorkflowInstance {
  id: string;
  definition_id: string;
  definition_version: number;
  subject_type: string;
  subject_id: string;
  current_state: WorkflowInstanceState;
  started_at: string | null;
  revision: number;
  tasks?: Task[];
}

export type DecisionOutcome = "approve" | "changes_requested" | "reject";
export interface DecisionBody {
  outcome: DecisionOutcome;
  comment?: string;
  effective_from?: string;
}
export interface SignatureEventSummary {
  id: string;
  meaning: string;
  method: string;
  content_digest: string | null;
  auth_context: Record<string, unknown> | null;
  reauth_at: string | null;
  crypto_signature: string | null;
}
export interface DecisionResult {
  task_id: string;
  instance_id: string;
  stage_key: string;
  outcome: DecisionOutcome;
  decided_at: string | null;
  decided_by: string;
  signature_event: SignatureEventSummary | null;
  comment: string | null;
}
```

- [ ] **Step 2: Add an optional `headers` arg to the api client** — in `lib/api.ts`, thread `extraHeaders` through `request`/`apiSend`/`useApi().send` (backward compatible — existing callers pass nothing):

```ts
async function request<T>(
  method: string,
  path: string,
  token: string | null,
  body?: unknown,
  extraHeaders?: Record<string, string>,
): Promise<T> {
  const headers: Record<string, string> = { ...(extraHeaders ?? {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  // …unchanged body…
}

export const apiSend = <T>(
  method: "POST" | "PATCH" | "DELETE",
  path: string,
  token: string | null,
  body?: unknown,
  headers?: Record<string, string>,
): Promise<T> => request<T>(method, path, token, body, headers);

// in useApi():
send: <T>(method: "POST" | "PATCH" | "DELETE", path: string, body?: unknown, headers?: Record<string, string>): Promise<T> =>
  apiSend<T>(method, path, token, body, headers),
```

- [ ] **Step 3: Verify the build typechecks** — `cd apps/web && npm run typecheck` (Expected: clean). No new test (pure types + a backward-compatible signature; covered by Task 4's hook test).

- [ ] **Step 4: Commit**

```bash
git add apps/web/src/lib/types.ts apps/web/src/lib/api.ts
git commit -m "feat(s-web-5): task/instance/decision types + Idempotency-Key header support"
```

---

## Task 3: `useDocumentApproval` hook

**Files:**
- Create: `apps/web/src/features/document/useDocumentApproval.ts`
- Test: `apps/web/src/features/document/useDocumentApproval.test.tsx`

- [ ] **Step 1: Write the failing test** — mirror `useVersionDiff.test.tsx`'s wrapper:

```tsx
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { AuthContext } from "../../lib/auth";
import { TEST_AUTH } from "../../test/render";
import { server } from "../../test/msw/server";
import { theme } from "../../theme/mantine";
import { useDocumentApproval } from "./useDocumentApproval";

const DOC = "11111111-1111-1111-1111-111111111111";
function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <MantineProvider theme={theme}>
      <QueryClientProvider client={client}>
        <AuthContext.Provider value={TEST_AUTH}>{children}</AuthContext.Provider>
      </QueryClientProvider>
    </MantineProvider>
  );
}

test("useDocumentApproval returns the instance with tasks", async () => {
  const { result } = renderHook(() => useDocumentApproval(DOC), { wrapper });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data?.current_state).toBe("IN_APPROVAL");
  expect(result.current.data?.tasks?.[0]?.type).toBe("APPROVE");
});

test("useDocumentApproval returns null when there is no cycle", async () => {
  server.use(http.get("/api/v1/documents/:id/approval", () => HttpResponse.json(null)));
  const { result } = renderHook(() => useDocumentApproval(DOC), { wrapper });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data).toBeNull();
});
```

- [ ] **Step 2: Add the default MSW handler** — in `test/msw/handlers.ts`, add the approval fixture + handler (see Task 10 for the inbox/decision handlers; add this one now):

```ts
export const approvalFixture = {
  id: "wf11-1111-1111-1111-111111111111",
  definition_id: "def1-1111-1111-1111-111111111111",
  definition_version: 1,
  subject_type: "DOCUMENT",
  subject_id: "11111111-1111-1111-1111-111111111111",
  current_state: "IN_APPROVAL",
  started_at: "2026-06-08T09:00:00+00:00",
  revision: 0,
  tasks: [
    {
      id: "task-1111-1111-1111-111111111111",
      instance_id: "wf11-1111-1111-1111-111111111111",
      stage_key: "quality_approval",
      type: "APPROVE",
      state: "PENDING",
      assignee_user_id: null,
      candidate_pool: ["bbbb1111-1111-1111-1111-111111111111"],
      action_expected: "approve",
      due_at: null,
    },
  ],
};
// add to `handlers`:
http.get("/api/v1/documents/:id/approval", () => HttpResponse.json(approvalFixture)),
```

- [ ] **Step 3: Run to verify fail** — `npm test -- useDocumentApproval` (Expected: FAIL — module not found).

- [ ] **Step 4: Implement the hook**

```ts
import { useQuery } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type { WorkflowInstance } from "../../lib/types";

// S-web-5: the document's current approval cycle (latest workflow instance + tasks), or null when it
// was never submitted. Gated document.read server-side; a 403 surfaces as an ApiError → quiet (DP-6).
export function useDocumentApproval(documentId: string | null, enabled = true) {
  const api = useApi();
  return useQuery({
    queryKey: ["document-approval", documentId],
    queryFn: () => api.get<WorkflowInstance | null>(`/api/v1/documents/${documentId}/approval`),
    enabled: enabled && documentId !== null,
  });
}
```

- [ ] **Step 5: Run to verify pass** — `npm test -- useDocumentApproval` (Expected: PASS).

- [ ] **Step 6: Commit** — `git add … && git commit -m "feat(s-web-5): useDocumentApproval hook"`

---

## Task 4: Review hooks (`useTasks`, `useTask`, `useWorkflowInstance`, `useDecideTask`)

**Files:**
- Create: `apps/web/src/features/review/hooks.ts`
- Test: `apps/web/src/features/review/hooks.test.tsx`

- [ ] **Step 1: Write the failing test** (same wrapper as Task 3):

```tsx
test("useTasks lists the caller's pending tasks", async () => {
  const { result } = renderHook(() => useTasks({ state: "PENDING" }), { wrapper });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data?.[0]?.type).toBe("APPROVE");
});

test("useDecideTask posts a decision with an Idempotency-Key", async () => {
  let sentKey: string | null = null;
  server.use(
    http.post("/api/v1/tasks/:id/decision", ({ request }) => {
      sentKey = request.headers.get("Idempotency-Key");
      return HttpResponse.json({
        task_id: "task-1111-1111-1111-111111111111",
        instance_id: "wf11-1111-1111-1111-111111111111",
        stage_key: "quality_approval", outcome: "approve",
        decided_at: "2026-06-08T10:00:00+00:00",
        decided_by: "bbbb1111-1111-1111-1111-111111111111",
        signature_event: null, comment: null,
      });
    }),
  );
  const { result } = renderHook(() => useDecideTask(), { wrapper });
  await result.current.mutateAsync({
    taskId: "task-1111-1111-1111-111111111111",
    documentId: "11111111-1111-1111-1111-111111111111",
    idempotencyKey: "key-123", body: { outcome: "approve" },
  });
  expect(sentKey).toBe("key-123");
});
```
(Import `useTasks`, `useDecideTask` from `./hooks`; add the default `/tasks` + `/tasks/:id/decision` handlers from Task 10 to `handlers.ts` first, or `server.use` them inline.)

- [ ] **Step 2: Run to verify fail** — `npm test -- review/hooks` (Expected: FAIL).

- [ ] **Step 3: Implement** `features/review/hooks.ts`:

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type {
  DecisionBody, DecisionResult, Task, TaskState, WorkflowInstance,
} from "../../lib/types";

export function useTasks(filters: { state?: TaskState; type?: string } = {}) {
  const api = useApi();
  return useQuery({
    queryKey: ["tasks", filters],
    queryFn: () => {
      const qs = new URLSearchParams({ assignee: "me" });
      if (filters.state) qs.set("state", filters.state);
      if (filters.type) qs.set("type", filters.type);
      return api.get<Task[]>(`/api/v1/tasks?${qs.toString()}`);
    },
  });
}

export function useTask(taskId: string | null) {
  const api = useApi();
  return useQuery({
    queryKey: ["task", taskId],
    queryFn: () => api.get<Task>(`/api/v1/tasks/${taskId}`),
    enabled: taskId !== null,
  });
}

export function useWorkflowInstance(instanceId: string | null) {
  const api = useApi();
  return useQuery({
    queryKey: ["workflow-instance", instanceId],
    queryFn: () => api.get<WorkflowInstance>(`/api/v1/workflow-instances/${instanceId}?expand=tasks`),
    enabled: instanceId !== null,
  });
}

export interface DecideInput {
  taskId: string;
  documentId: string;
  idempotencyKey: string;
  body: DecisionBody;
}
export function useDecideTask() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ taskId, body, idempotencyKey }: DecideInput) =>
      api.send<DecisionResult>("POST", `/api/v1/tasks/${taskId}/decision`, body, {
        "Idempotency-Key": idempotencyKey,
      }),
    onSuccess: (_d, { taskId, documentId }) => {
      void qc.invalidateQueries({ queryKey: ["task", taskId] });
      void qc.invalidateQueries({ queryKey: ["tasks"] });
      void qc.invalidateQueries({ queryKey: ["document", documentId] });
      void qc.invalidateQueries({ queryKey: ["document-approval", documentId] });
      void qc.invalidateQueries({ queryKey: ["document-versions", documentId] });
    },
  });
}
```

- [ ] **Step 4: Run to verify pass** — `npm test -- review/hooks` (Expected: PASS).
- [ ] **Step 5: Commit** — `git commit -m "feat(s-web-5): review/approve query+mutation hooks"`

---

## Task 5: `useReleaseDocument` + approval cache invalidation

**Files:**
- Modify: `apps/web/src/features/authoring/hooks.ts`
- Test: `apps/web/src/features/authoring/hooks.test.tsx` (create if absent, or add a focused test)

- [ ] **Step 1: Write the failing test** — a release mutation calls the endpoint and returns the doc:

```tsx
test("useReleaseDocument posts release and returns the document", async () => {
  server.use(
    http.post("/api/v1/documents/:id/release", ({ params }) =>
      HttpResponse.json({ ...createdDocFixture, id: String(params.id), current_state: "Effective" }),
    ),
  );
  const { result } = renderHook(() => useReleaseDocument(), { wrapper });
  const doc = await result.current.mutateAsync("11111111-1111-1111-1111-111111111111");
  expect(doc.current_state).toBe("Effective");
});
```

- [ ] **Step 2: Run to verify fail** — `npm test -- authoring/hooks` (Expected: FAIL — export missing).

- [ ] **Step 3: Implement** — in `authoring/hooks.ts`, (a) add `["document-approval", documentId]` to `useInvalidateDocument`, (b) add `useReleaseDocument`:

```ts
// inside useInvalidateDocument(), add:
void qc.invalidateQueries({ queryKey: ["document-approval", documentId] });

// new export:
export function useReleaseDocument() {
  const api = useApi();
  const invalidate = useInvalidateDocument();
  return useMutation({
    mutationFn: (documentId: string) =>
      api.send<DocumentSummary>("POST", `/api/v1/documents/${documentId}/release`, {}),
    onSuccess: (_d, documentId) => invalidate(documentId),
  });
}
```

- [ ] **Step 4: Run to verify pass** — `npm test -- authoring/hooks` (Expected: PASS).
- [ ] **Step 5: Commit** — `git commit -m "feat(s-web-5): useReleaseDocument + approval cache invalidation"`

---

## Task 6: `TaskStateBadge`

**Files:**
- Create: `apps/web/src/features/document/TaskStateBadge.tsx`
- Test: `apps/web/src/features/document/TaskStateBadge.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
import { expect, test } from "vitest";
import { renderWithProviders } from "../../test/render";
import { TaskStateBadge } from "./TaskStateBadge";

test("TaskStateBadge labels the state with text + a non-color glyph (DP-7)", () => {
  const { getByLabelText } = renderWithProviders(<TaskStateBadge state="PENDING" />);
  expect(getByLabelText("Task state: Pending")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run to verify fail** — `npm test -- TaskStateBadge` (Expected: FAIL).

- [ ] **Step 3: Implement** (mirrors `StateBadge.tsx`):

```tsx
import { Badge, type MantineSize } from "@mantine/core";
import type { TaskState } from "../../lib/types";

const META: Record<string, { label: string; mark: string; color: string }> = {
  PENDING: { label: "Pending", mark: "◔", color: "var(--es-warning)" },
  CLAIMED: { label: "Claimed", mark: "◑", color: "var(--es-info)" },
  DONE: { label: "Done", mark: "✓", color: "var(--es-success)" },
  SKIPPED: { label: "Skipped", mark: "⊘", color: "var(--es-text-muted)" },
  ESCALATED: { label: "Escalated", mark: "▲", color: "var(--es-danger)" },
  EXPIRED: { label: "Expired", mark: "⊗", color: "var(--es-text-muted)" },
};

export function TaskStateBadge({ state, size = "sm" }: { state: TaskState; size?: MantineSize }) {
  const meta = META[state] ?? { label: state, mark: "•", color: "var(--es-text-muted)" };
  return (
    <Badge variant="light" color={meta.color} size={size}
      leftSection={<span aria-hidden="true">{meta.mark}</span>}
      aria-label={`Task state: ${meta.label}`}>
      {meta.label}
    </Badge>
  );
}
```

- [ ] **Step 4: Run to verify pass** — `npm test -- TaskStateBadge` (Expected: PASS).
- [ ] **Step 5: Commit** — `git commit -m "feat(s-web-5): TaskStateBadge (DP-7)"`

---

## Task 7: `ApprovalStepper` (+ the pure `buildApprovalNodes`)

**Files:**
- Create: `apps/web/src/features/document/ApprovalStepper.tsx`
- Test: `apps/web/src/features/document/ApprovalStepper.test.tsx`

- [ ] **Step 1: Write the failing test** — exercise node derivation per state + a11y:

```tsx
import { expect, test } from "vitest";
import { renderWithProviders } from "../../test/render";
import { ApprovalStepper, buildApprovalNodes } from "./ApprovalStepper";
import type { WorkflowInstance } from "../../lib/types";

const base: WorkflowInstance = {
  id: "wf", definition_id: "d", definition_version: 1, subject_type: "DOCUMENT",
  subject_id: "doc", current_state: "IN_APPROVAL", started_at: "2026-06-08T09:00:00+00:00",
  revision: 0,
  tasks: [{
    id: "t", instance_id: "wf", stage_key: "quality_approval", type: "APPROVE",
    state: "PENDING", assignee_user_id: null, candidate_pool: ["u1"], action_expected: "approve", due_at: null,
  }],
};
const nameOf = (id: string | null) => (id === "u1" ? "Ken" : id ?? "—");

test("in-approval → approval node is current, release pending", () => {
  const nodes = buildApprovalNodes(base, "InReview", null, nameOf);
  expect(nodes.map((n) => n.status)).toEqual(["done", "current", "pending"]);
});

test("effective → all done", () => {
  const inst = { ...base, current_state: "APPROVED",
    tasks: [{ ...base.tasks![0], state: "DONE" as const, assignee_user_id: "u1" }] };
  const nodes = buildApprovalNodes(inst, "Effective", "2026-06-09T00:00:00+00:00", nameOf);
  expect(nodes.map((n) => n.status)).toEqual(["done", "done", "done"]);
  expect(nodes[1].sub).toContain("Ken");
});

test("rejected → approval node is rejected", () => {
  const inst = { ...base, current_state: "REJECTED_TO_DRAFT",
    tasks: [{ ...base.tasks![0], state: "DONE" as const, assignee_user_id: "u1" }] };
  const nodes = buildApprovalNodes(inst, "Draft", null, nameOf);
  expect(nodes[1].status).toBe("rejected");
});

test("stepper has an accessible label and marks the current step", () => {
  const { getByLabelText, container } = renderWithProviders(
    <ApprovalStepper instance={base} docState="InReview" effectiveFrom={null} nameOf={nameOf} />,
  );
  expect(getByLabelText("Approval progress")).toBeInTheDocument();
  expect(container.querySelector('[aria-current="step"]')).not.toBeNull();
});
```

- [ ] **Step 2: Run to verify fail** — `npm test -- ApprovalStepper` (Expected: FAIL).

- [ ] **Step 3: Implement**

```tsx
import { Box, Stack, Text } from "@mantine/core";
import type { DocumentCurrentState, Task, WorkflowInstance } from "../../lib/types";

type NodeStatus = "done" | "current" | "pending" | "rejected";
export interface StepNode { key: string; title: string; sub: string; status: NodeStatus; }

const MARK: Record<NodeStatus, string> = { done: "✓", current: "◉", pending: "·", rejected: "✕" };
const COLOR: Record<NodeStatus, string> = {
  done: "var(--es-success)", current: "var(--es-info)",
  pending: "var(--es-text-muted)", rejected: "var(--es-danger)",
};

function approvalNode(
  instance: WorkflowInstance, task: Task | null, nameOf: (id: string | null) => string,
): StepNode {
  if (instance.current_state === "REJECTED_TO_DRAFT") {
    return { key: "approval", title: "Changes requested",
      sub: task?.assignee_user_id ? `By ${nameOf(task.assignee_user_id)}` : "Returned to the author",
      status: "rejected" };
  }
  if (task && task.state === "DONE") {
    return { key: "approval", title: "Approved",
      sub: task.assignee_user_id ? `By ${nameOf(task.assignee_user_id)}` : "Approved", status: "done" };
  }
  const pool = task?.candidate_pool ?? [];
  const sub = instance.current_state === "NEEDS_ATTENTION"
    ? "Awaiting an approver — none assigned"
    : pool.length ? `Awaiting ${pool.map(nameOf).join(", ")}` : "Awaiting approval";
  return { key: "approval", title: "Quality approval", sub, status: "current" };
}

export function buildApprovalNodes(
  instance: WorkflowInstance,
  docState: DocumentCurrentState,
  effectiveFrom: string | null,
  nameOf: (id: string | null) => string,
): StepNode[] {
  const started = instance.started_at ? instance.started_at.slice(0, 10) : "";
  const submitted: StepNode = { key: "submitted", title: "Submitted for review",
    sub: started ? `Submitted · ${started}` : "Submitted", status: "done" };
  const approveTask = (instance.tasks ?? []).find((t) => t.type === "APPROVE") ?? null;
  const release: StepNode = {
    key: "release", title: "Released to effective",
    sub: docState === "Effective"
      ? (effectiveFrom ? `Effective · ${effectiveFrom.slice(0, 10)}` : "Effective")
      : docState === "Approved" ? "Awaiting release" : "Not yet released",
    status: docState === "Effective" ? "done" : docState === "Approved" ? "current" : "pending",
  };
  return [submitted, approvalNode(instance, approveTask, nameOf), release];
}

export function ApprovalStepper(props: {
  instance: WorkflowInstance;
  docState: DocumentCurrentState;
  effectiveFrom: string | null;
  nameOf: (id: string | null) => string;
}) {
  const nodes = buildApprovalNodes(props.instance, props.docState, props.effectiveFrom, props.nameOf);
  return (
    <Stack gap={0} component="ol" aria-label="Approval progress"
      style={{ listStyle: "none", padding: 0, margin: 0 }}>
      {nodes.map((n, i) => (
        <Box component="li" key={n.key}
          aria-current={n.status === "current" ? "step" : undefined}
          style={{ display: "flex", gap: 12, paddingBottom: i < nodes.length - 1 ? 16 : 0 }}>
          <Box aria-hidden="true" style={{
            width: 22, height: 22, borderRadius: "50%", flexShrink: 0, color: "#fff",
            display: "flex", alignItems: "center", justifyContent: "center", background: COLOR[n.status],
          }}>{MARK[n.status]}</Box>
          <Box>
            <Text fw={600} size="sm">{n.title}</Text>
            <Text size="xs" c="dimmed">{n.sub}</Text>
          </Box>
        </Box>
      ))}
    </Stack>
  );
}
```

- [ ] **Step 4: Run to verify pass** — `npm test -- ApprovalStepper` (Expected: PASS).
- [ ] **Step 5: Commit** — `git commit -m "feat(s-web-5): ApprovalStepper + buildApprovalNodes"`

---

## Task 8: `ApprovalsTab` (doc-page card: stepper + release + review CTA)

**Files:**
- Create: `apps/web/src/features/document/ApprovalsTab.tsx`
- Test: `apps/web/src/features/document/ApprovalsTab.test.tsx`

- [ ] **Step 1: Write the failing test** — release shown only when capability+state allow; "Review & approve" link when the caller is the candidate (TEST_AUTH.sub = `bbbb1111…`, which is the approvalFixture's `candidate_pool`):

```tsx
import { expect, test } from "vitest";
import { renderWithProviders } from "../../test/render";
import { ApprovalsTab } from "./ApprovalsTab";
import type { DocumentSummary } from "../../lib/types";

const doc = (over: Partial<DocumentSummary> = {}): DocumentSummary => ({
  id: "11111111-1111-1111-1111-111111111111", identifier: "SOP-PUR-014", kind: "DOCUMENT",
  title: "Supplier Selection", document_type_id: null, area_code: "PUR", folder_path: "/SOPs",
  current_state: "InReview", classification: "Internal", is_singleton: false,
  owner_user_id: "x", framework_id: "f", current_effective_version_id: null,
  effective_from: null, created_at: null,
  capabilities: { checkout: false, edit: false, manage_metadata: false, submit: false,
    release: false, obsolete: false, read_draft: true }, ...over,
});

test("shows the stepper + the Review & approve CTA for a candidate", async () => {
  const { findByText, findByRole } = renderWithProviders(<ApprovalsTab doc={doc()} />);
  expect(await findByText("Quality approval")).toBeInTheDocument();
  expect(await findByRole("link", { name: /review & approve/i })).toBeInTheDocument();
});

test("shows Release only when capability + Approved state", async () => {
  const { findByRole, queryByRole } = renderWithProviders(
    <ApprovalsTab doc={doc({ current_state: "Approved",
      capabilities: { checkout: false, edit: false, manage_metadata: false, submit: false,
        release: true, obsolete: false, read_draft: true } })} />,
  );
  expect(await findByRole("button", { name: "Release" })).toBeInTheDocument();
  // and absent when the capability is false:
  const { queryByRole: q2 } = renderWithProviders(<ApprovalsTab doc={doc({ current_state: "Approved" })} />);
  expect(q2("button", { name: "Release" })).toBeNull();
  expect(queryByRole).toBeDefined();
});
```

- [ ] **Step 2: Run to verify fail** — `npm test -- ApprovalsTab` (Expected: FAIL).

- [ ] **Step 3: Implement**

```tsx
import { Alert, Anchor, Button, Group, Stack, Text } from "@mantine/core";
import { useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import type { DocumentSummary } from "../../lib/types";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import { useReleaseDocument } from "../authoring/hooks";
import { ApprovalStepper } from "./ApprovalStepper";
import { useDocumentApproval } from "./useDocumentApproval";

export function ApprovalsTab({ doc }: { doc: DocumentSummary }) {
  const { user } = useAuth();
  const myId = user?.profile?.sub ?? null;
  const { data: instance, isLoading, isError, error } = useDocumentApproval(doc.id);
  const { data: directory } = useUserDirectory();
  const release = useReleaseDocument();
  const [relErr, setRelErr] = useState<string | null>(null);

  const nameOf = (id: string | null) =>
    (id ? directory?.find((u) => u.id === id)?.display_name ?? "a user" : "—");

  if (isLoading) return <Text size="sm" c="dimmed">Loading approvals…</Text>;
  if (isError && error instanceof ApiError && error.status === 403)
    return <Text size="sm" c="dimmed">You don't have access to the approval history.</Text>;
  if (isError) return <Text size="sm" c="red">Could not load approvals.</Text>;
  if (!instance) return <Text size="sm" c="dimmed">No approval activity yet.</Text>;

  const myOpenTask = (instance.tasks ?? []).find(
    (t) => t.state === "PENDING" &&
      (t.assignee_user_id === myId || (t.candidate_pool ?? []).includes(myId ?? "")),
  );
  const canRelease = doc.capabilities?.release === true && doc.current_state === "Approved";

  async function doRelease() {
    setRelErr(null);
    try { await release.mutateAsync(doc.id); }
    catch (e) { setRelErr(e instanceof ApiError ? e.message : "Release failed. Please retry."); }
  }

  return (
    <Stack gap="md">
      <ApprovalStepper instance={instance} docState={doc.current_state}
        effectiveFrom={doc.effective_from} nameOf={nameOf} />
      {myOpenTask && (
        <Anchor component={Link} to={`/tasks/${myOpenTask.id}`}>Review &amp; approve →</Anchor>
      )}
      {relErr && <Alert color="red" withCloseButton onClose={() => setRelErr(null)}>{relErr}</Alert>}
      {canRelease && (
        <Group>
          <Button color="teal" loading={release.isPending} onClick={() => void doRelease()}>Release</Button>
          <Text size="xs" c="dimmed">Releases the Approved version → Effective.</Text>
        </Group>
      )}
    </Stack>
  );
}
```

- [ ] **Step 4: Run to verify pass** — `npm test -- ApprovalsTab` (Expected: PASS).
- [ ] **Step 5: Commit** — `git commit -m "feat(s-web-5): ApprovalsTab (stepper + release + review CTA)"`

---

## Task 9: `DecisionCard`

**Files:**
- Create: `apps/web/src/features/review/DecisionCard.tsx`
- Test: `apps/web/src/features/review/DecisionCard.test.tsx`

- [ ] **Step 1: Write the failing test** — conditional-required comment, SoD 403, and a11y:

```tsx
import { expect, test } from "vitest";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { axe } from "jest-axe";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { DecisionCard } from "./DecisionCard";

const TASK = "task-1111-1111-1111-111111111111";
const DOC = "11111111-1111-1111-1111-111111111111";

test("Submit is disabled until a valid decision (comment required to reject)", async () => {
  const u = userEvent.setup();
  const { getByRole, getByLabelText } = renderWithProviders(
    <DecisionCard taskId={TASK} documentId={DOC} />, { route: `/tasks/${TASK}` },
  );
  const submit = getByRole("button", { name: "Submit decision" });
  expect(submit).toBeDisabled();
  await u.click(getByLabelText("Reject"));
  expect(submit).toBeDisabled(); // comment still required
  await u.type(getByLabelText(/Comment/), "missing risk section");
  expect(submit).toBeEnabled();
});

test("surfaces a 403 sod_violation calmly", async () => {
  server.use(
    http.post("/api/v1/tasks/:id/decision", () =>
      HttpResponse.json({ code: "sod_violation", title: "Forbidden" }, { status: 403 }),
    ),
  );
  const u = userEvent.setup();
  const { getByRole, getByLabelText, findByText } = renderWithProviders(
    <DecisionCard taskId={TASK} documentId={DOC} />, { route: `/tasks/${TASK}` },
  );
  await u.click(getByLabelText("Approve"));
  await u.click(getByLabelText(/Signing as/));
  await u.click(getByRole("button", { name: "Submit decision" }));
  expect(await findByText(/separation of duties/i)).toBeInTheDocument();
});

test("has no a11y violations", async () => {
  const { container } = renderWithProviders(<DecisionCard taskId={TASK} documentId={DOC} />, {
    route: `/tasks/${TASK}`,
  });
  expect(await axe(container)).toHaveNoViolations();
});
```
(Add a default `http.post("/api/v1/tasks/:id/decision", …)` happy handler to `handlers.ts` — see Task 10.)

- [ ] **Step 2: Run to verify fail** — `npm test -- DecisionCard` (Expected: FAIL).

- [ ] **Step 3: Implement**

```tsx
import { Alert, Button, Card, Checkbox, Group, Radio, Stack, Text, Textarea } from "@mantine/core";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import type { DecisionOutcome } from "../../lib/types";
import { useDecideTask } from "./hooks";

const NEEDS_COMMENT: DecisionOutcome[] = ["changes_requested", "reject"];

export function DecisionCard({ taskId, documentId }: { taskId: string; documentId: string }) {
  const { user } = useAuth();
  const decide = useDecideTask();
  const navigate = useNavigate();
  const [outcome, setOutcome] = useState<DecisionOutcome | "">("");
  const [comment, setComment] = useState("");
  const [signed, setSigned] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [idemKey] = useState(() => crypto.randomUUID());

  const commentRequired = NEEDS_COMMENT.includes(outcome as DecisionOutcome);
  const commentMissing = commentRequired && comment.trim().length === 0;
  const needsSig = outcome === "approve";
  const disabled = outcome === "" || commentMissing || (needsSig && !signed) || decide.isPending;
  const who = user?.profile?.name ?? user?.profile?.preferred_username ?? "you";

  async function submit() {
    setError(null);
    if (outcome === "") return;
    try {
      await decide.mutateAsync({
        taskId, documentId, idempotencyKey: idemKey,
        body: { outcome, comment: comment.trim() || undefined },
      });
      navigate("/tasks");
    } catch (e) {
      if (e instanceof ApiError) {
        if (e.status === 403 && e.code === "sod_violation")
          setError("You can't approve this version (separation of duties).");
        else if (e.status === 409) setError("This task was already decided.");
        else if (e.status === 403 && e.code === "step_up_required")
          setError("Re-authentication is required to sign.");
        else setError(e.message);
      } else setError("Something went wrong. Please retry.");
    }
  }

  return (
    <Card withBorder>
      <Stack gap="md">
        <Text fw={600}>Decision</Text>
        {error && <Alert color="red" withCloseButton onClose={() => setError(null)}>{error}</Alert>}
        <Radio.Group value={outcome} onChange={(v) => setOutcome(v as DecisionOutcome)}
          label="Your decision" withAsterisk>
          <Stack gap="xs" mt="xs">
            <Radio value="approve" label="Approve" />
            <Radio value="changes_requested" label="Request changes" />
            <Radio value="reject" label="Reject" />
          </Stack>
        </Radio.Group>
        <Textarea label="Comment" value={comment}
          onChange={(e) => setComment(e.currentTarget.value)}
          required={commentRequired} withAsterisk={commentRequired}
          aria-describedby="decision-comment-rule"
          error={commentMissing ? "A comment is required to request changes or reject." : undefined} />
        <Text id="decision-comment-rule" size="xs" c="dimmed">
          Required when requesting changes or rejecting.
        </Text>
        {needsSig && (
          <Stack gap={4}>
            <Checkbox checked={signed} onChange={(e) => setSigned(e.currentTarget.checked)}
              label={`Signing as ${who} — meaning: approval`} />
            <Text size="xs" c="dimmed">v1 — single-factor logged confirmation.</Text>
          </Stack>
        )}
        <Group justify="flex-end">
          <Button variant="subtle" onClick={() => navigate("/tasks")}>Cancel</Button>
          <Button onClick={() => void submit()} loading={decide.isPending} disabled={disabled}>
            Submit decision
          </Button>
        </Group>
      </Stack>
    </Card>
  );
}
```

- [ ] **Step 4: Run to verify pass** — `npm test -- DecisionCard` (Expected: PASS).
- [ ] **Step 5: Commit** — `git commit -m "feat(s-web-5): DecisionCard (approve/reject, conditional comment, sig slot)"`

---

## Task 10: `TasksInbox` + the shared MSW task handlers

**Files:**
- Create: `apps/web/src/features/review/TasksInbox.tsx`
- Test: `apps/web/src/features/review/TasksInbox.test.tsx`
- Modify: `apps/web/src/test/msw/handlers.ts` (add the default task/decision/instance/release handlers used across Tasks 4/8/9/11/12)

- [ ] **Step 1: Add the default MSW handlers** — append to `handlers.ts` (`taskFixture` reuses the `approvalFixture.tasks[0]`):

```ts
export const taskFixture = approvalFixture.tasks;
// add to `handlers`:
http.get("/api/v1/tasks", () => HttpResponse.json(taskFixture)),
http.get("/api/v1/tasks/:id", () => HttpResponse.json(taskFixture[0])),
http.get("/api/v1/workflow-instances/:id", () => HttpResponse.json(approvalFixture)),
http.post("/api/v1/tasks/:id/decision", () =>
  HttpResponse.json({
    task_id: taskFixture[0].id, instance_id: approvalFixture.id, stage_key: "quality_approval",
    outcome: "approve", decided_at: "2026-06-08T10:00:00+00:00",
    decided_by: "bbbb1111-1111-1111-1111-111111111111", signature_event: null, comment: null,
  }),
),
http.post("/api/v1/documents/:id/release", ({ params }) =>
  HttpResponse.json({ ...docFixture[0], id: String(params.id), current_state: "Effective" }),
),
```

- [ ] **Step 2: Write the failing test**

```tsx
import { expect, test } from "vitest";
import { http, HttpResponse } from "msw";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { TasksInbox } from "./TasksInbox";

test("lists pending tasks with a link to the review page", async () => {
  const { findByRole } = renderWithProviders(<TasksInbox />, { route: "/tasks" });
  expect(await findByRole("link", { name: /approve/i })).toBeInTheDocument();
});

test("shows a calm empty state", async () => {
  server.use(http.get("/api/v1/tasks", () => HttpResponse.json([])));
  const { findByText } = renderWithProviders(<TasksInbox />, { route: "/tasks" });
  expect(await findByText("No tasks in your queue.")).toBeInTheDocument();
});
```

- [ ] **Step 3: Run to verify fail** — `npm test -- TasksInbox` (Expected: FAIL).

- [ ] **Step 4: Implement**

```tsx
import { Loader, Stack, Table, Text, Title } from "@mantine/core";
import { Link } from "react-router-dom";
import { ApiError } from "../../lib/api";
import { TaskStateBadge } from "../document/TaskStateBadge";
import { useTasks } from "./hooks";

// S-web-5: the self-scoped reviewer/approver work queue (GET /tasks). The document identity is shown
// on the review page (one click away) — the Document column is a deferred enhancement (needs an
// instance→doc resolution that would N+1 the list).
export function TasksInbox() {
  const { data: tasks, isLoading, isError, error } = useTasks({ state: "PENDING" });

  if (isLoading) return <Loader aria-label="Loading tasks" />;
  if (isError) {
    if (error instanceof ApiError && error.status === 403)
      return <Text c="dimmed">You don't have access to the task queue.</Text>;
    return <Text c="red">Could not load your tasks.</Text>;
  }
  return (
    <Stack gap="md">
      <Title order={2}>Review &amp; Approve</Title>
      {!tasks || tasks.length === 0 ? (
        <Text c="dimmed">No tasks in your queue.</Text>
      ) : (
        <Table aria-label="My tasks" striped highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th scope="col">Task</Table.Th>
              <Table.Th scope="col">Stage</Table.Th>
              <Table.Th scope="col">State</Table.Th>
              <Table.Th scope="col">Due</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {tasks.map((t) => (
              <Table.Tr key={t.id}>
                <Table.Td><Link to={`/tasks/${t.id}`}>{t.action_expected ?? t.type}</Link></Table.Td>
                <Table.Td>{t.stage_key}</Table.Td>
                <Table.Td><TaskStateBadge state={t.state} /></Table.Td>
                <Table.Td>{t.due_at ? t.due_at.slice(0, 10) : "—"}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
    </Stack>
  );
}
```

- [ ] **Step 5: Run to verify pass** — `npm test -- TasksInbox` (Expected: PASS).
- [ ] **Step 6: Commit** — `git commit -m "feat(s-web-5): TasksInbox + shared task MSW handlers"`

---

## Task 11: `ReviewApprovePage`

**Files:**
- Create: `apps/web/src/features/review/ReviewApprovePage.tsx`
- Test: `apps/web/src/features/review/ReviewApprovePage.test.tsx`

- [ ] **Step 1: Write the failing test** — renders the doc context + the DecisionCard for a PENDING task; a 404 task is calm:

```tsx
import { expect, test } from "vitest";
import { http, HttpResponse } from "msw";
import { Route, Routes } from "react-router-dom";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { ReviewApprovePage } from "./ReviewApprovePage";

function mount(route: string) {
  return renderWithProviders(
    <Routes><Route path="tasks/:id" element={<ReviewApprovePage />} /></Routes>, { route },
  );
}

test("renders the document context + the decision card for a pending task", async () => {
  const { findByText, findByRole } = mount("/tasks/task-1111-1111-1111-111111111111");
  expect(await findByText(/Supplier Selection/)).toBeInTheDocument();
  expect(await findByRole("button", { name: "Submit decision" })).toBeInTheDocument();
});

test("a 404 task is calm with a back link", async () => {
  server.use(http.get("/api/v1/tasks/:id", () =>
    HttpResponse.json({ code: "not_found", title: "Not found" }, { status: 404 })));
  const { findByText, findByRole } = mount("/tasks/nope");
  expect(await findByText(/isn't assigned to you/)).toBeInTheDocument();
  expect(await findByRole("link", { name: /Back to your tasks/ })).toBeInTheDocument();
});
```
(The default handlers from Task 10 return `taskFixture[0]` (PENDING, instance → subject_id = `11111111…`), so `useDocument` resolves `docFixture[0]` "Supplier Selection".)

- [ ] **Step 2: Run to verify fail** — `npm test -- ReviewApprovePage` (Expected: FAIL).

- [ ] **Step 3: Implement**

```tsx
import { Alert, Anchor, Grid, Loader, Stack, Text, Title } from "@mantine/core";
import { Link, useParams } from "react-router-dom";
import { ApiError } from "../../lib/api";
import { useDocument } from "../document/useDocument";
import { useDocumentVersions } from "../document/useDocumentVersions";
import { VersionCompare } from "../document/VersionCompare";
import { DecisionCard } from "./DecisionCard";
import { useTask, useWorkflowInstance } from "./hooks";

// S-web-5: the per-task focus page. Task → instance → subject document → the redline of what changed
// + the decision card (only for a PENDING task the caller can see; the API 404-collapses otherwise).
export function ReviewApprovePage() {
  const { id: taskId = null } = useParams();
  const { data: task, isLoading, isError, error } = useTask(taskId);
  const { data: instance } = useWorkflowInstance(task?.instance_id ?? null);
  const docId = instance?.subject_id ?? null;
  const { data: doc } = useDocument(docId, { enabled: docId !== null });
  const { data: versions } = useDocumentVersions(docId, docId !== null);

  if (isLoading) return <Loader aria-label="Loading task" />;
  if (isError || !task) {
    const status = error instanceof ApiError ? error.status : 0;
    return (
      <Alert color={status === 404 ? "yellow" : "red"} title="Task unavailable">
        <Stack gap="xs" align="flex-start">
          <Text size="sm">
            {status === 404 ? "This task doesn't exist or isn't assigned to you." : "Could not load this task."}
          </Text>
          <Anchor component={Link} to="/tasks">← Back to your tasks</Anchor>
        </Stack>
      </Alert>
    );
  }

  const decidable = task.state === "PENDING";
  return (
    <Stack gap="lg">
      <Title order={2}>Review &amp; Approve{doc ? ` — ${doc.identifier}` : ""}</Title>
      <Grid gutter="lg" align="flex-start">
        <Grid.Col span={{ base: 12, md: 7 }}>
          <Stack gap="md">
            {doc && <Text fw={600}>{doc.title}</Text>}
            {docId && <VersionCompare documentId={docId} versions={versions ?? []} />}
          </Stack>
        </Grid.Col>
        <Grid.Col span={{ base: 12, md: 5 }}>
          {decidable && docId ? (
            <DecisionCard taskId={task.id} documentId={docId} />
          ) : (
            <Alert color="blue" title="Decided">This task has already been decided.</Alert>
          )}
        </Grid.Col>
      </Grid>
    </Stack>
  );
}
```

- [ ] **Step 4: Run to verify pass** — `npm test -- ReviewApprovePage` (Expected: PASS).
- [ ] **Step 5: Commit** — `git commit -m "feat(s-web-5): ReviewApprovePage (redline + decision)"`

---

## Task 12: Routing + nav

**Files:**
- Modify: `apps/web/src/App.tsx`
- Modify: `apps/web/src/app/shell/LeftRail.tsx`
- Test: `apps/web/src/app/shell/LeftRail.test.tsx` (add a case), plus an `App` routing assertion if an App test exists; otherwise rely on the component tests.

- [ ] **Step 1: Wire the routes** — in `App.tsx`: import `TasksInbox` + `ReviewApprovePage`, delete the local `Reserved` function (now unused), and replace the tasks route:

```tsx
import { ReviewApprovePage } from "./features/review/ReviewApprovePage";
import { TasksInbox } from "./features/review/TasksInbox";
// …
<Route path="tasks" element={<TasksInbox />} />
<Route path="tasks/:id" element={<ReviewApprovePage />} />
```

- [ ] **Step 2: Add the nav entry** — in `LeftRail.tsx`, after the Library `NavLink`:

```tsx
<NavLink
  component={Link}
  to="/tasks"
  label="Review & Approve"
  active={pathname.startsWith("/tasks")}
/>
```

- [ ] **Step 3: Test the nav link** — in `LeftRail.test.tsx`:

```tsx
test("renders the Review & Approve nav link", () => {
  const { getByRole } = renderWithProviders(<LeftRail />);
  expect(getByRole("link", { name: "Review & Approve" })).toHaveAttribute("href", "/tasks");
});
```

- [ ] **Step 4: Run** — `npm test -- LeftRail` and `npm run typecheck` (Expected: PASS / clean; the removed `Reserved` must not be referenced).
- [ ] **Step 5: Commit** — `git commit -m "feat(s-web-5): /tasks routes + Review & Approve nav"`

---

## Task 13: Mount the Approvals card on the document page

**Files:**
- Modify: `apps/web/src/features/document/DocumentDetailPage.tsx`
- Test: `apps/web/src/features/document/DocumentDetailPage.test.tsx` (add a case)

- [ ] **Step 1: Mount the card** — import `ApprovalsTab` and insert it as the first card in the right column `Stack`:

```tsx
import { ApprovalsTab } from "./ApprovalsTab";
// …inside the right Grid.Col <Stack gap="lg">, before the Version history card:
<Card withBorder>
  <Stack gap="sm">
    <Text fw={600}>Approvals</Text>
    <ApprovalsTab doc={doc} />
  </Stack>
</Card>
```

- [ ] **Step 2: Add the test** — the detail page now shows the stepper for an in-approval doc. Use a doc whose `/approval` returns the fixture (default handler). Add:

```tsx
test("renders the Approvals stepper on the detail page", async () => {
  const { findByText } = renderWithProviders(
    <Routes><Route path="documents/:id" element={<DocumentDetailPage />} /></Routes>,
    { route: "/documents/11111111-1111-1111-1111-111111111111" },
  );
  expect(await findByText("Quality approval")).toBeInTheDocument();
});
```
(`docFixture[0]` is `Effective`; the default `/approval` handler returns `IN_APPROVAL` with a PENDING task → the stepper renders "Quality approval". If the existing detail test asserts no extra network, add the `/approval` handler is already global so it's covered.)

- [ ] **Step 3: Run** — `npm test -- DocumentDetailPage` (Expected: PASS).
- [ ] **Step 4: Commit** — `git commit -m "feat(s-web-5): Approvals card on the document detail page"`

---

## Task 14: Docs, full gates, diff-critic, PR

**Files:**
- Modify: `docs/slice-history.md` (add the S-web-5 entry), `CLAUDE.md` (Recent learnings + Current status), `docs/15-api-design.md` (note the discovery read is implemented, gate `document.read`).

- [ ] **Step 1: Update the docs** — add a concise S-web-5 entry to `docs/slice-history.md` (front-end + the one discovery read; no migration/key); prepend a Recent-learnings bullet + flip Current status in `CLAUDE.md`; note the implemented gate in `docs/15-api-design.md` §8.8.

- [ ] **Step 2: Full local gates**

```
/check-web        # eslint + tsc + build + vitest (incl. jest-axe)
/check-api        # ruff + format-check + mypy-strict + pytest unit
/check-contracts  # redocly lint
/check-migrations # round-trip (no migration this slice, but must stay green)
```
Then the integration shard: `cd apps/api && uv run pytest tests/integration/test_approval.py -m integration -q` (run a doc-creating file first to catch shared-DB pollution: `uv run pytest -m integration tests/integration/test_vault.py tests/integration/test_approval.py -q`).

- [ ] **Step 3: diff-critic** — run the `diff-critic` agent on the branch diff (`Agent` tool, `subagent_type: diff-critic`); fold only confirmed findings.

- [ ] **Step 4: Commit docs + open the PR**

```bash
git add docs/ CLAUDE.md
git commit -m "docs(s-web-5): slice-history + learnings + api-design note"
```
Then `/pr` (full local gate → PR against protected `main`). PR body: closes UJ-3; front-end + one migration-free read; no new key; SoD server-enforced; 5 CI jobs green.

---

## Self-review (spec coverage)

- **Discovery endpoint** (spec §4) → Task 1 (helper + route + OpenAPI + 5 integration tests incl. null/404/403/NEEDS_ATTENTION).
- **Types** (§5.1) → Task 2. **Hooks** (§5.2) → Tasks 3/4/5. **Routing/nav** (§5.3) → Task 12.
- **TasksInbox** (§5.4) → Task 10 (Document column deferred — noted). **ReviewApprovePage** → Task 11. **DecisionCard** → Task 9. **ApprovalStepper** → Task 7. **ApprovalsTab** → Task 8. **TaskStateBadge** → Task 6. **Doc-page mount** → Task 13.
- **SoD gating** (§6) → approve via task visibility (TasksInbox/ReviewApprovePage only render the DecisionCard for a visible PENDING task) + release via `capabilities.release` (Task 8); server 403 handled (Task 9).
- **a11y** (§8) → jest-axe in Tasks 7/9 (+ the radiogroup/aria-describedby/aria-current built into the components).
- **Calm states** (§7) → 403/404/409/null handled in Tasks 3/8/9/10/11.
- **Deferrals** (§10) honored — no multi-stage, no claim/reassign, no Acks/Audit tabs, no new key, the inbox Document column + nav count are explicit deferrals.

**Type consistency check:** `WorkflowInstance`/`Task`/`DecisionResult`/`DecisionBody`/`DecisionOutcome` defined in Task 2 are used consistently in Tasks 3/4/7/8/9/11. `useDocumentApproval` queryKey `["document-approval", id]` matches the invalidations in Tasks 4/5. `buildApprovalNodes(instance, docState, effectiveFrom, nameOf)` signature matches its call in `ApprovalStepper` and the Task 7 tests.
