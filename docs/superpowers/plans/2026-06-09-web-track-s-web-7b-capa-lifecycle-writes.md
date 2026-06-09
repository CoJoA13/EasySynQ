# S-web-7b — CAPA lifecycle writes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended)
> or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax
> for tracking.

**Goal:** Make the CAPA drawer *drive* the lifecycle (raise → containment → root-cause →
action-plan[approved] → implement → verify[signed] → close, with the Verify→RootCause loop + the M4
evidence close gate), with the action-plan approval decided in the existing `/tasks` inbox — over a thin
backend read-enrichment layer (mirroring 7a; no migration, no new permission key).

**Architecture:** Front-end is new code in `apps/web/src/features/capa/` (mutation hooks · per-stage forms ·
a contextual Advance panel · a raise modal · an evidence linker) wired into the existing `CapaDrawer` /
`CapaBoardPage`, plus a subject-aware branch in the S-web-5 `/tasks` `ReviewApprovePage` (+ a generalized
`DecisionCard`). The backend adds three additive reads — `subject_type`/`subject_id` on the `_task` **detail**
serializer, a new `GET /capas/{id}/approval` (gated `capa.read`, the S-web-5 `/documents/{id}/approval`
mirror), and `evidence_links` per stage on the CAPA detail — so the whole CAPA approval path never depends on
`document.read` (the seeded Top-Management approver holds only `capa.read`).

**Tech Stack:** React/TS · Mantine · @tanstack/react-query · react-router-dom · MSW · vitest · jest-axe (web).
FastAPI · SQLAlchemy async · Pydantic (api). Redocly (contract).

**Spec:** `docs/superpowers/specs/2026-06-09-web-track-s-web-7b-capa-lifecycle-writes-design.md`.

**Local gates (native-Windows box):** web = `/check-web` (eslint + strict tsc + build + the full vitest
suite) is the reliable front-end gate. The api **test** suites are **Linux-CI-only** here — for Task 1 the
local gate is `/check-api` (ruff/format/mypy-strict) + `/check-contracts`; the integration tests you write run
red→green in **CI**, not locally. Do not run `pytest` locally.

---

## File structure

**Backend (Task 1):**
- Modify `apps/api/src/easysynq_api/api/workflow.py` — `_task` optional `subject_type`/`subject_id`;
  `get_task_endpoint` loads the instance + passes them.
- Modify `apps/api/src/easysynq_api/api/capa.py` — new `GET /capas/{id}/approval` + `_approval` serializers;
  `_stage` optional `evidence_links`; `get_capa_endpoint` loads per-stage evidence.
- Modify `apps/api/src/easysynq_api/services/capa/repository.py` — `list_stage_evidence`.
- Modify `packages/contracts/openapi.yaml` — `Task.subject_type`/`subject_id`; `CapaStage.evidence_links`;
  `GET /capas/{capa_id}/approval` + the `CapaApproval` schema.
- Test `apps/api/tests/integration/test_capa.py`, `test_workflow.py` (run in CI).

**Front-end (Tasks 2–11), all under `apps/web/src/`:**
- `lib/types.ts` — CAPA request bodies, `EvidenceLink`, `CapaApproval`, `RecordSummary`, `Task.subject_type`/
  `subject_id`, `CapaStage.evidence_links` (Task 2).
- `test/msw/handlers.ts` — write handlers + the approval read + evidence + task subject_type (Task 2).
- `features/capa/hooks.ts` — `useCapaApproval`, `useRecords` (Task 3).
- `features/capa/mutations.ts` (+ `.test.tsx`) — the 8 write hooks (Task 3).
- `features/capa/EvidenceLinker.tsx` (+ `.test.tsx`) (Task 4).
- `features/capa/StageForms.tsx` (+ `.test.tsx`) — the 5 stage forms + the Close action (Task 5).
- `features/capa/AdvancePanel.tsx` (+ `.test.tsx`) (Task 6).
- `features/capa/CloseGateStepper.tsx` (+ `.test.tsx`) — evidence-aware `deriveGate` (Task 7).
- `features/capa/RaiseCapaModal.tsx` (+ `.test.tsx`) (Task 8).
- `features/capa/CapaDrawer.tsx` (+ `.test.tsx`) — mount the Advance panel + per-stage evidence (Task 9).
- `features/capa/CapaBoardPage.tsx` (+ `.test.tsx`) — the Raise button + modal (Task 10).
- `features/review/{hooks.ts,DecisionCard.tsx,ReviewApprovePage.tsx}` + new `CapaApprovalContext.tsx`
  (+ tests) — the approval integration (Task 11).
- Docs + final gate (Task 12).

---

## Task 1: Backend — the thin read-enrichment (task subject, approval read, per-stage evidence)

**Files:**
- Modify: `apps/api/src/easysynq_api/api/workflow.py` (`_task` ~54-65, `get_task_endpoint` ~161-167).
- Modify: `apps/api/src/easysynq_api/api/capa.py` (`_stage` ~126-134, imports, `get_capa_endpoint` ~314-324,
  new endpoint after it).
- Modify: `apps/api/src/easysynq_api/services/capa/repository.py` (add `list_stage_evidence`).
- Modify: `packages/contracts/openapi.yaml`.
- Test: `apps/api/tests/integration/test_capa.py`, `apps/api/tests/integration/test_workflow.py`.

- [ ] **Step 1: `_task` carries the subject discriminator (detail only)**

In `apps/api/src/easysynq_api/api/workflow.py`, replace `_task` (54-65) with the keyword-enriched form (the
list serializer keeps emitting no subject keys — every existing caller passes nothing):

```python
def _task(
    t: Task, *, subject_type: str | None = None, subject_id: str | None = None
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(t.id),
        "instance_id": str(t.instance_id),
        "stage_key": t.stage_key,
        "type": t.type.value,
        "state": t.state.value,
        "assignee_user_id": str(t.assignee_user_id) if t.assignee_user_id else None,
        "candidate_pool": t.candidate_pool,
        "action_expected": t.action_expected,
        "due_at": t.due_at.isoformat() if t.due_at else None,
    }
    if subject_type is not None:
        out["subject_type"] = subject_type
        out["subject_id"] = subject_id
    return out
```

Then update `get_task_endpoint` (161-167) to load the instance and pass the subject:

```python
@router.get("/tasks/{task_id}")
async def get_task_endpoint(
    task_id: uuid.UUID,
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    task = await _own_task(session, caller, task_id)
    instance = await wf_repo.get_instance(session, task.instance_id)
    return _task(
        task,
        subject_type=instance.subject_type.value if instance else None,
        subject_id=str(instance.subject_id) if instance else None,
    )
```

- [ ] **Step 2: `list_stage_evidence` repository accessor**

In `apps/api/src/easysynq_api/services/capa/repository.py`, add the imports (top of file, next to the other
model imports) and the accessor. The evidence target enum is `EvidenceForTargetType` (member `capa_stage`):

```python
from ...db.models._evidence_enums import EvidenceForTargetType
from ...db.models.documented_information import DocumentedInformation
from ...db.models.evidence_for_link import EvidenceForLink
```

```python
async def list_stage_evidence(
    session: AsyncSession, stage_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[dict[str, Any]]]:
    """Evidence links pointing AT each capa_stage (target_type=capa_stage), joined to the linking
    record's identifier — one query for all of a CAPA's stages. The M4 close gate needs ≥1 link on the
    current-cycle Implement + Verify stages; the drawer renders the list per stage."""
    if not stage_ids:
        return {}
    rows = (
        await session.execute(
            select(EvidenceForLink, DocumentedInformation.identifier)
            .join(DocumentedInformation, DocumentedInformation.id == EvidenceForLink.record_id)
            .where(
                EvidenceForLink.target_type == EvidenceForTargetType.capa_stage,
                EvidenceForLink.target_id.in_(stage_ids),
            )
            .order_by(EvidenceForLink.created_at)
        )
    ).all()
    out: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for link, identifier in rows:
        out.setdefault(link.target_id, []).append(
            {
                "id": str(link.id),
                "record_id": str(link.record_id),
                "record_identifier": identifier,
                "link_reason": link.link_reason,
                "created_at": link.created_at.isoformat() if link.created_at else None,
            }
        )
    return out
```

Ensure `from typing import Any` and `import uuid` and `from sqlalchemy import select` are imported at the top
of `repository.py` (they already are — `list_capas` uses them).

- [ ] **Step 3: `_stage` carries `evidence_links`; detail loads it**

In `apps/api/src/easysynq_api/api/capa.py`, replace `_stage` (126-134):

```python
def _stage(s: CapaStage, *, evidence_links: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(s.id),
        "stage": s.stage.value,
        "content_block": s.content_block,
        "cycle_marker": s.cycle_marker,
        "created_by": str(s.created_by),
        "created_at": s.created_at.isoformat(),
    }
    if evidence_links is not None:
        out["evidence_links"] = evidence_links
    return out
```

Update `get_capa_endpoint` (314-324) to load per-stage evidence and pass it:

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
    stage_rows = await capa_repo.list_capa_stages(session, capa_id)
    evidence = await capa_repo.list_stage_evidence(session, [s.id for s in stage_rows])
    stages = [_stage(s, evidence_links=evidence.get(s.id, [])) for s in stage_rows]
    return await _capa_full(session, capa, stages=stages)
```

- [ ] **Step 4: the `GET /capas/{id}/approval` read**

In `apps/api/src/easysynq_api/api/capa.py`, add the workflow imports near the top (next to the existing
`from ..db.models.workflow import ...` if present, else add it):

```python
from ..db.models._workflow_enums import WorkflowSubjectType
from ..db.models.workflow import Task, WorkflowInstance
from ..services.workflow import repository as wf_repo
```

Add the serializers (near the other `_…` serializers, after `_capa_full`):

```python
def _approval_task(t: Task) -> dict[str, Any]:
    return {
        "id": str(t.id),
        "stage_key": t.stage_key,
        "type": t.type.value,
        "state": t.state.value,
        "assignee_user_id": str(t.assignee_user_id) if t.assignee_user_id else None,
        "candidate_pool": t.candidate_pool,
        "action_expected": t.action_expected,
        "due_at": t.due_at.isoformat() if t.due_at else None,
    }


def _approval(instance: WorkflowInstance, tasks: list[Task]) -> dict[str, Any]:
    ctx = instance.context or {}
    return {
        "instance": {
            "id": str(instance.id),
            "current_state": instance.current_state,
            "definition_version": instance.definition_version,
            "subject_type": instance.subject_type.value,
            "subject_id": str(instance.subject_id),
            "tasks": [_approval_task(t) for t in tasks],
        },
        "proposed_action_plan": ctx.get("action_plan"),
    }
```

Add the endpoint immediately after `get_capa_endpoint`:

```python
@router.get("/capas/{capa_id}/approval")
async def get_capa_approval_endpoint(
    capa_id: uuid.UUID,
    caller: AppUser = Depends(_capa_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any] | None:
    """The CAPA's current action-plan approval cycle (the latest CAPA workflow instance + its tasks +
    the proposed action plan from the instance context), or ``null`` when none has opened. Gated
    ``capa.read`` (the S-web-5 ``GET /documents/{id}/approval`` mirror) — so a Top-Management approver,
    who holds only ``capa.read``, can read what they sign without ``document.read``."""
    capa = await capa_repo.get_capa(session, capa_id)
    if capa is None or capa.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="CAPA not found")
    instance = await wf_repo.latest_instance_for_subject(
        session, caller.org_id, WorkflowSubjectType.CAPA, capa.id
    )
    if instance is None:
        return None
    tasks = await wf_repo.list_instance_tasks(session, instance.id)
    return _approval(instance, tasks)
```

- [ ] **Step 5: contract — `Task`, `CapaStage`, the new path + `CapaApproval` schema**

In `packages/contracts/openapi.yaml`:
- Find the `Task` schema (grep `^    Task:` or `    Task:` under `components/schemas`). Add to its
  `properties` (nullable — absent on the list):
  ```yaml
        subject_type: { type: [string, "null"] }
        subject_id: { type: [string, "null"], format: uuid }
  ```
- Find the `CapaStage` schema. Add to its `properties`:
  ```yaml
        evidence_links:
          type: array
          items:
            type: object
            properties:
              id: { type: string, format: uuid }
              record_id: { type: string, format: uuid }
              record_identifier: { type: [string, "null"] }
              link_reason: { type: [string, "null"] }
              created_at: { type: [string, "null"], format: date-time }
  ```
- Add a `CapaApproval` schema (near `Capa`):
  ```yaml
      CapaApproval:
        type: [object, "null"]
        properties:
          instance:
            type: object
            properties:
              id: { type: string, format: uuid }
              current_state: { type: string }
              definition_version: { type: integer }
              subject_type: { type: string }
              subject_id: { type: string, format: uuid }
              tasks: { type: array, items: { type: object, additionalProperties: true } }
          proposed_action_plan: { type: [object, "null"], additionalProperties: true }
  ```
- Add the path (near the other `/capas/{capa_id}…` paths):
  ```yaml
    /capas/{capa_id}/approval:
      get:
        tags: [capa]
        summary: The CAPA's current action-plan approval cycle (or null)
        parameters:
          - { name: capa_id, in: path, required: true, schema: { type: string, format: uuid } }
        responses:
          "200":
            description: The latest approval instance + proposed plan, or null
            content:
              application/json:
                schema: { $ref: "#/components/schemas/CapaApproval" }
          "404": { description: CAPA not found }
  ```

- [ ] **Step 6: the integration tests (run in CI)**

Append to `apps/api/tests/integration/test_capa.py` (reuses the file's `_subject`/`_grant`/`_CAPA_KEYS`/
`_auth` helpers + `app_client`/`token_factory` — the existing pattern):

```python
async def test_capa_detail_stage_carries_evidence_links(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("capaev")
    await _grant(subject, _CAPA_KEYS)
    h = _auth(token_factory, subject)
    raised = (
        await app_client.post(
            "/api/v1/capas", headers=h, json={"title": "Evidence shape", "severity": "Minor"}
        )
    ).json()
    detail = (await app_client.get(f"/api/v1/capas/{raised['id']}", headers=h)).json()
    # every stage now carries an evidence_links array (empty until a record is linked)
    assert all("evidence_links" in s for s in detail["stages"])
    assert detail["stages"][0]["evidence_links"] == []


async def test_capa_approval_read_null_then_pending(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("capaap")
    await _grant(subject, _CAPA_KEYS)
    h = _auth(token_factory, subject)
    raised = (
        await app_client.post(
            "/api/v1/capas", headers=h, json={"title": "Approval read", "severity": "Minor"}
        )
    ).json()
    cid = raised["id"]
    # no cycle yet → null
    assert (await app_client.get(f"/api/v1/capas/{cid}/approval", headers=h)).json() is None
    # walk to RootCause then propose an action plan → a non-null approval with the proposed plan
    await app_client.post(f"/api/v1/capas/{cid}/containment", headers=h, json={"content_block": {"correction": "x"}})
    await app_client.post(f"/api/v1/capas/{cid}/root-cause", headers=h, json={"content_block": {"root_cause": "y"}})
    await app_client.post(
        f"/api/v1/capas/{cid}/action-plan",
        headers=h,
        json={"content_block": {"action_items": ["fix it"]}},
    )
    approval = (await app_client.get(f"/api/v1/capas/{cid}/approval", headers=h)).json()
    assert approval is not None
    assert approval["instance"]["subject_id"] == cid
    assert approval["proposed_action_plan"] == {"action_items": ["fix it"]}
```

Append to `apps/api/tests/integration/test_workflow.py` a `subject_type` assertion on the task detail. Use the
file's existing CAPA-approval or document-approval setup (find a test that creates a task and calls
`GET /tasks/{id}`); the new assertion is simply:

```python
    # the single-task read now carries the subject discriminator (subject_type/subject_id)
    detail = (await app_client.get(f"/api/v1/tasks/{task_id}", headers=h)).json()
    assert detail["subject_type"] in {"DOCUMENT", "CAPA", "DCR"}
    assert detail["subject_id"]
```

> If `test_workflow.py` has no convenient task-creating fixture to extend, instead add the `subject_type`
> assertion inside the existing CAPA action-plan approval test in `test_capa.py` right after it fetches the
> approver's task — whichever is the smaller diff. The behavior under test is identical.

- [ ] **Step 7: local gates**

Run: `/check-api` → ruff + format-check + mypy-strict clean (the new imports, the `dict[uuid, list]` return,
the optional kwargs all type-check).
Run: `/check-contracts` → redocly lint passes on `openapi.yaml`.

- [ ] **Step 8: Commit**

```bash
git add apps/api/src/easysynq_api/api/workflow.py apps/api/src/easysynq_api/api/capa.py apps/api/src/easysynq_api/services/capa/repository.py packages/contracts/openapi.yaml apps/api/tests/integration/test_capa.py apps/api/tests/integration/test_workflow.py
git commit -m "feat(s-web-7b): thin CAPA read-enrichment (task subject, /approval, stage evidence)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Front-end types + MSW fixtures/handlers

**Files:**
- Modify: `apps/web/src/lib/types.ts` (append to the S-web-7 section; extend `Task`/`CapaStage`).
- Modify: `apps/web/src/test/msw/handlers.ts` (fixtures + write/approval/evidence handlers).

- [ ] **Step 1: extend the types**

In `apps/web/src/lib/types.ts`, add to the `Task` interface (after `due_at`):

```ts
  subject_type?: string; // detail-only (GET /tasks/{id}); "DOCUMENT" | "CAPA" | "DCR"
  subject_id?: string;
```

Add `evidence_links` to `CapaStage` (after `created_at`):

```ts
  evidence_links?: EvidenceLink[]; // detail-only; links pointing AT this stage (target_type=capa_stage)
```

Append to the end of the S-web-7 section:

```ts
// An evidence-for link pointing at a capa_stage (from POST /records/{id}/evidence-links).
export interface EvidenceLink {
  id: string;
  record_id: string;
  record_identifier: string | null;
  link_reason: string | null;
  created_at: string | null;
}

// GET /capas/{id}/approval — the latest action-plan approval cycle, or null (no cycle opened).
export interface CapaApproval {
  instance: {
    id: string;
    current_state: string; // a stage key while running; COMPLETED | REJECTED | NEEDS_ATTENTION terminal
    definition_version: number;
    subject_type: string;
    subject_id: string;
    tasks: Task[];
  };
  proposed_action_plan: Record<string, unknown> | null;
}

// GET /records (a bare array; filter-not-403) — the evidence picker's source. Minimal shape.
export interface RecordSummary {
  id: string;
  identifier: string | null;
  title: string;
  record_type: string;
}

// ---- request bodies (CAPA writes) ----
export interface CapaRaiseBody {
  title: string;
  severity: NcSeverity;
  source?: CapaSource;
  process_id?: string;
  problem?: string;
}
export interface StageBlockBody {
  content_block: Record<string, unknown>;
}
export interface CapaVerifyBody {
  decision: "effective" | "not_effective";
  content_block: Record<string, unknown>;
}
export interface EvidenceLinkBody {
  target_type: "capa_stage";
  target_id: string;
  link_reason?: string;
}
```

- [ ] **Step 2: add the import + fixtures in MSW handlers**

In `apps/web/src/test/msw/handlers.ts`, the `Capa` type is already imported (used by the `satisfies`). Add
`evidence_links` to one Implement-bearing stage so close-gate tests have a real link. Replace
`capaLoopDetailFixture`'s `lp000004` ActionPlan line is fine; we need an Implement+evidence path — add a
`capaImplementDetailFixture` (a CAPA at Verify, cycle 0, with a current-cycle Implement+Verify that BOTH carry
evidence — the close-ready case) and a CAPA-approval fixture. Add after `capaLoopDetailFixture` (~line 460):

```ts
// A close-READY CAPA: at Verify (cycle 0) with a current-cycle Implement + an effective Verify, BOTH
// carrying an evidence link → the honest close gate is satisfied (close succeeds).
export const capaCloseReadyFixture = {
  id: "ca000008-0008-0008-0008-000000000008",
  identifier: "REC-000040",
  title: "Press guard interlock bypass",
  source: "audit",
  severity: "Major",
  process_id: "pr000001-0001-0001-0001-000000000001",
  close_state: "Verify",
  cycle_marker: 0,
  origin_finding_id: null,
  raised_by: "bbbb1111-1111-1111-1111-111111111111",
  created_at: "2026-05-25T09:00:00+00:00",
  stages: [
    { id: "cr000001-0001-0001-0001-000000000001", stage: "RootCause", content_block: { root_cause: "Interlock unmaintained." }, cycle_marker: 0, created_by: "bbbb1111-1111-1111-1111-111111111111", created_at: "2026-05-26T09:00:00+00:00", evidence_links: [] },
    { id: "cr000002-0002-0002-0002-000000000002", stage: "Implement", content_block: { actions_done: "Replaced + scheduled PM." }, cycle_marker: 0, created_by: "bbbb1111-1111-1111-1111-111111111111", created_at: "2026-05-27T09:00:00+00:00", evidence_links: [{ id: "el1", record_id: "re000001-0001-0001-0001-000000000001", record_identifier: "REC-000041", link_reason: "PM schedule", created_at: "2026-05-27T09:10:00+00:00" }] },
    { id: "cr000003-0003-0003-0003-000000000003", stage: "Verify", content_block: { decision: "effective", narrative: "No recurrence in 30 days." }, cycle_marker: 0, created_by: "bbbb1111-1111-1111-1111-111111111111", created_at: "2026-05-28T09:00:00+00:00", evidence_links: [{ id: "el2", record_id: "re000002-0002-0002-0002-000000000002", record_identifier: "REC-000042", link_reason: "audit re-check", created_at: "2026-05-28T09:10:00+00:00" }] },
  ],
} satisfies Capa;

// GET /capas/{id}/approval — a pending action-plan approval (the proposer's drawer + the approver's page).
export const capaApprovalFixture = {
  instance: {
    id: "wfca1111-1111-1111-1111-111111111111",
    current_state: "qm_approval",
    definition_version: 1,
    subject_type: "CAPA",
    subject_id: "ca000001-0001-0001-0001-000000000001",
    tasks: [
      { id: "tkca1111-1111-1111-1111-111111111111", stage_key: "qm_approval", type: "APPROVE", state: "PENDING", assignee_user_id: null, candidate_pool: ["bbbb1111-1111-1111-1111-111111111111"], action_expected: "approve_capa_action_plan", due_at: null },
    ],
  },
  proposed_action_plan: { action_items: ["Schedule supplier re-evaluations", "Add a calendar reminder"] },
};

// A CAPA-subject task detail (GET /tasks/{id}) — the approver routes through ReviewApprovePage's CAPA branch.
export const capaApprovalTask = {
  id: "tkca1111-1111-1111-1111-111111111111",
  instance_id: "wfca1111-1111-1111-1111-111111111111",
  stage_key: "qm_approval",
  type: "APPROVE",
  state: "PENDING",
  assignee_user_id: null,
  candidate_pool: ["bbbb1111-1111-1111-1111-111111111111"],
  action_expected: "approve_capa_action_plan",
  due_at: null,
  subject_type: "CAPA",
  subject_id: "ca000001-0001-0001-0001-000000000001",
};

// GET /records — the evidence picker source (a bare array).
export const recordsFixture = [
  { id: "re000001-0001-0001-0001-000000000001", identifier: "REC-000041", title: "Preventive-maintenance schedule", record_type: "EVIDENCE" },
  { id: "re000002-0002-0002-0002-000000000002", identifier: "REC-000042", title: "Audit re-check checklist", record_type: "EVIDENCE" },
];
```

Add `subject_type`/`subject_id` to the existing `approveTask` (the document task) so the document branch keeps
working with the new field — edit `approveTask` (lines 357-367) to add, after `due_at: null,`:

```ts
  subject_type: "DOCUMENT",
  subject_id: "11111111-1111-1111-1111-111111111111",
```

Also add `evidence_links: []` to each stage of `capaDetailFixture` and `capaLoopDetailFixture` (the detail
read now always includes the array) — append `, evidence_links: [] ` inside each stage object literal.

- [ ] **Step 3: add the default handlers**

In the `handlers` array, replace the existing CAPA detail handler block (`http.get("/api/v1/capas/:id", …)`,
~742-748) and add the write + approval + records handlers right after it:

```ts
  http.get("/api/v1/capas", () => HttpResponse.json(capaListFixture)),
  http.get("/api/v1/capas/:id", ({ params }) => {
    if (params.id === "ca000005-0005-0005-0005-000000000005") return HttpResponse.json(capaLoopDetailFixture);
    if (params.id === "ca000008-0008-0008-0008-000000000008") return HttpResponse.json(capaCloseReadyFixture);
    return HttpResponse.json({ ...capaDetailFixture, id: String(params.id) });
  }),
  // S-web-7b writes (default happy-path; per-test overrides for the 409s). Each returns a CAPA-ish body
  // the UI ignores (it invalidates + refetches).
  http.post("/api/v1/capas", () => HttpResponse.json({ ...capaDetailFixture, id: "ca-new-0000-0000-0000-000000000000" }, { status: 201 })),
  http.post("/api/v1/capas/:id/containment", ({ params }) => HttpResponse.json({ ...capaDetailFixture, id: String(params.id) })),
  http.post("/api/v1/capas/:id/root-cause", ({ params }) => HttpResponse.json({ ...capaDetailFixture, id: String(params.id) })),
  http.post("/api/v1/capas/:id/action-plan", ({ params }) => HttpResponse.json({ ...capaDetailFixture, id: String(params.id), approval_instance: { id: "wfca1111-1111-1111-1111-111111111111", current_state: "qm_approval", definition_version: 1 } })),
  http.post("/api/v1/capas/:id/implement", ({ params }) => HttpResponse.json({ ...capaDetailFixture, id: String(params.id) })),
  http.post("/api/v1/capas/:id/verify", ({ params }) => HttpResponse.json({ ...capaDetailFixture, id: String(params.id) })),
  http.post("/api/v1/capas/:id/close", ({ params }) => HttpResponse.json({ ...capaDetailFixture, id: String(params.id), close_state: "Closed" })),
  http.get("/api/v1/capas/:id/approval", () => HttpResponse.json(null)),
  http.get("/api/v1/records", () => HttpResponse.json(recordsFixture)),
  http.post("/api/v1/records/:id/evidence-links", () => HttpResponse.json({ id: "el-new", record_id: "re000001-0001-0001-0001-000000000001", record_identifier: "REC-000041", link_reason: null, created_at: "2026-06-09T09:00:00+00:00" }, { status: 201 })),
```

> The default `/approval` returns `null` (most CAPAs have no cycle) — the approval-flow tests `server.use` the
> `capaApprovalFixture`. The default `/me/permissions` returns no permissions (existing handler), so write
> affordances are hidden by default; gating tests `server.use` the keys.

- [ ] **Step 4: typecheck the fixtures**

Run (from `apps/web`): `npx tsc --noEmit`
Expected: PASS (the `satisfies Capa` on `capaCloseReadyFixture` confirms the evidence_links shape; no test runs
yet — this just type-checks the new fixtures/types).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/lib/types.ts apps/web/src/test/msw/handlers.ts
git commit -m "feat(s-web-7b): CAPA write types + MSW fixtures/handlers

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Data hooks — mutations + approval + records

**Files:**
- Modify: `apps/web/src/features/capa/hooks.ts` (add `useCapaApproval`, `useRecords`).
- Create: `apps/web/src/features/capa/mutations.ts`
- Test: `apps/web/src/features/capa/mutations.test.tsx`

- [ ] **Step 1: add the query hooks**

Append to `apps/web/src/features/capa/hooks.ts`:

```ts
import type { Capa, CapaApproval, CapaList, RecordSummary } from "../../lib/types";

// GET /capas/{id}/approval — the action-plan approval cycle (or null). Gated capa.read (the Top-Mgmt
// approver holds only capa.read). Enabled only when we want it (e.g. a RootCause CAPA, or the approval page).
export function useCapaApproval(id: string | null) {
  const api = useApi();
  return useQuery({
    queryKey: ["capa-approval", id],
    queryFn: () => api.get<CapaApproval | null>(`/api/v1/capas/${id!}/approval`),
    enabled: id !== null,
    retry: false,
  });
}

// GET /records — the evidence picker source (filter-not-403; a bare array). limit 100.
export function useRecords() {
  const api = useApi();
  return useQuery({
    queryKey: ["records", "for-evidence"],
    queryFn: () => api.get<RecordSummary[]>("/api/v1/records?limit=100"),
    retry: false,
  });
}
```

(Adjust the existing top-of-file `import type { Capa, CapaList } from "../../lib/types";` to the combined import
above, and keep the existing `import { ApiError, useApi } from "../../lib/api";` + `useQuery` import.)

- [ ] **Step 2: write the failing mutations test**

```tsx
// apps/web/src/features/capa/mutations.test.tsx
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { expect, test } from "vitest";
import { AuthContext } from "../../lib/auth";
import { TEST_AUTH } from "../../test/render";
import {
  useCapaClose,
  useCapaContainment,
  useLinkEvidence,
  useRaiseCapa,
} from "./mutations";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <AuthContext.Provider value={TEST_AUTH}>{children}</AuthContext.Provider>
    </QueryClientProvider>
  );
}

test("useRaiseCapa POSTs and resolves the created CAPA", async () => {
  const { result } = renderHook(() => useRaiseCapa(), { wrapper });
  const capa = await result.current.mutateAsync({ title: "New NC", severity: "Minor" });
  expect(capa.id).toBeDefined();
});

test("useCapaContainment POSTs the content_block for a CAPA", async () => {
  const { result } = renderHook(() => useCapaContainment("ca000001-0001-0001-0001-000000000001"), {
    wrapper,
  });
  await result.current.mutateAsync({ content_block: { correction: "froze POs" } });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
});

test("useCapaClose POSTs with no body", async () => {
  const { result } = renderHook(() => useCapaClose("ca000008-0008-0008-0008-000000000008"), { wrapper });
  await result.current.mutateAsync();
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
});

test("useLinkEvidence POSTs an evidence-link to a capa_stage", async () => {
  const { result } = renderHook(() => useLinkEvidence("ca000008-0008-0008-0008-000000000008"), { wrapper });
  await result.current.mutateAsync({
    recordId: "re000001-0001-0001-0001-000000000001",
    targetId: "cr000002-0002-0002-0002-000000000002",
    linkReason: "PM schedule",
  });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
});
```

- [ ] **Step 3: run it to verify it fails**

Run: `npm test -- mutations.test.tsx`
Expected: FAIL — `Cannot find module './mutations'`.

- [ ] **Step 4: implement the mutations**

```ts
// apps/web/src/features/capa/mutations.ts
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type { Capa, CapaRaiseBody, CapaVerifyBody, StageBlockBody } from "../../lib/types";

// After any CAPA write, invalidate the detail + the board (+ the approval read for the action-plan).
// We never reshape optimistically — the server is the source of truth (close-gate, SoD, signatures).
function useCapaInvalidator(capaId: string) {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: ["capa", capaId] });
    void qc.invalidateQueries({ queryKey: ["capas"] });
    void qc.invalidateQueries({ queryKey: ["capa-approval", capaId] });
  };
}

export function useRaiseCapa() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CapaRaiseBody) => api.send<Capa>("POST", "/api/v1/capas", body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["capas"] }),
  });
}

function useStageMutation(capaId: string, path: string) {
  const api = useApi();
  const invalidate = useCapaInvalidator(capaId);
  return useMutation({
    mutationFn: (body: StageBlockBody) =>
      api.send<Capa>("POST", `/api/v1/capas/${capaId}/${path}`, body),
    onSuccess: invalidate,
  });
}

export const useCapaContainment = (id: string) => useStageMutation(id, "containment");
export const useCapaRootCause = (id: string) => useStageMutation(id, "root-cause");
export const useCapaActionPlan = (id: string) => useStageMutation(id, "action-plan");
export const useCapaImplement = (id: string) => useStageMutation(id, "implement");

export function useCapaVerify(capaId: string) {
  const api = useApi();
  const invalidate = useCapaInvalidator(capaId);
  return useMutation({
    mutationFn: (body: CapaVerifyBody) =>
      api.send<Capa>("POST", `/api/v1/capas/${capaId}/verify`, body),
    onSuccess: invalidate,
  });
}

export function useCapaClose(capaId: string) {
  const api = useApi();
  const invalidate = useCapaInvalidator(capaId);
  return useMutation({
    mutationFn: () => api.send<Capa>("POST", `/api/v1/capas/${capaId}/close`),
    onSuccess: invalidate,
  });
}

export function useLinkEvidence(capaId: string) {
  const api = useApi();
  const invalidate = useCapaInvalidator(capaId);
  return useMutation({
    mutationFn: ({
      recordId,
      targetId,
      linkReason,
    }: {
      recordId: string;
      targetId: string;
      linkReason?: string;
    }) =>
      api.send("POST", `/api/v1/records/${recordId}/evidence-links`, {
        target_type: "capa_stage",
        target_id: targetId,
        link_reason: linkReason,
      }),
    onSuccess: invalidate,
  });
}
```

- [ ] **Step 5: run it to verify it passes**

Run: `npm test -- mutations.test.tsx`
Expected: PASS (all four).

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/features/capa/hooks.ts apps/web/src/features/capa/mutations.ts apps/web/src/features/capa/mutations.test.tsx
git commit -m "feat(s-web-7b): CAPA mutation hooks + approval/records queries

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: `EvidenceLinker` — link an existing record to a stage

**Files:**
- Create: `apps/web/src/features/capa/EvidenceLinker.tsx`
- Test: `apps/web/src/features/capa/EvidenceLinker.test.tsx`

- [ ] **Step 1: write the failing test**

```tsx
// apps/web/src/features/capa/EvidenceLinker.test.tsx
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { expect, test } from "vitest";
import { AuthContext } from "../../lib/auth";
import { theme } from "../../theme/mantine";
import { TEST_AUTH } from "../../test/render";
import { EvidenceLinker } from "./EvidenceLinker";

function wrap(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MantineProvider theme={theme}>
      <QueryClientProvider client={client}>
        <AuthContext.Provider value={TEST_AUTH}>{node}</AuthContext.Provider>
      </QueryClientProvider>
    </MantineProvider>,
  );
}

test("links a selected record as evidence for the stage", async () => {
  const u = userEvent.setup();
  wrap(
    <EvidenceLinker capaId="ca000008-0008-0008-0008-000000000008" stageId="cr000002-0002-0002-0002-000000000002" />,
  );
  await u.click(await screen.findByLabelText("Record"));
  await u.click(await screen.findByRole("option", { name: /REC-000041/ }));
  const link = screen.getByRole("button", { name: /Link evidence/ });
  await u.click(link);
  await waitFor(() => expect(screen.getByText(/Linked/)).toBeInTheDocument());
});
```

- [ ] **Step 2: run it to verify it fails**

Run: `npm test -- EvidenceLinker.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: implement the linker**

```tsx
// apps/web/src/features/capa/EvidenceLinker.tsx
import { Alert, Button, Group, Select, Text, TextInput } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import { useRecords } from "./hooks";
import { useLinkEvidence } from "./mutations";

// Light "link an existing record as evidence" affordance (epic §7: no net-new upload). The picked record
// is linked to THIS capa_stage (target_type=capa_stage) — the M4 close gate needs ≥1 link on the
// current-cycle Implement + Verify stages.
export function EvidenceLinker({ capaId, stageId }: { capaId: string; stageId: string }) {
  const { data: records } = useRecords();
  const link = useLinkEvidence(capaId);
  const [recordId, setRecordId] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  async function submit() {
    if (!recordId) return;
    setError(null);
    try {
      await link.mutateAsync({ recordId, targetId: stageId, linkReason: reason.trim() || undefined });
      setDone(true);
      setRecordId(null);
      setReason("");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not link the record.");
    }
  }

  return (
    <div>
      {error && (
        <Alert color="red" mb="xs" withCloseButton onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      {done && (
        <Text size="xs" c="teal" mb="xs">
          Linked.
        </Text>
      )}
      <Group align="flex-end" gap="xs">
        <Select
          aria-label="Record"
          placeholder="Pick a record"
          searchable
          value={recordId}
          onChange={setRecordId}
          data={(records ?? []).map((r) => ({
            value: r.id,
            label: `${r.identifier ?? r.id} — ${r.title}`,
          }))}
        />
        <TextInput
          aria-label="Link reason"
          placeholder="Reason (optional)"
          value={reason}
          onChange={(e) => setReason(e.currentTarget.value)}
        />
        <Button onClick={() => void submit()} loading={link.isPending} disabled={!recordId}>
          Link evidence
        </Button>
      </Group>
    </div>
  );
}
```

- [ ] **Step 4: run it to verify it passes**

Run: `npm test -- EvidenceLinker.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/capa/EvidenceLinker.tsx apps/web/src/features/capa/EvidenceLinker.test.tsx
git commit -m "feat(s-web-7b): evidence linker (link an existing record to a stage)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: `StageForms` — the per-stage write forms + the close action

**Files:**
- Create: `apps/web/src/features/capa/StageForms.tsx`
- Test: `apps/web/src/features/capa/StageForms.test.tsx`

Each form takes the `capa`, builds the right `content_block`, submits via its mutation, and surfaces a calm
409. The Implement + Verify forms render the `EvidenceLinker` for the just-created stage AFTER the stage
exists (i.e. they advise linking evidence once the stage is recorded — see the note below). The Close action
reads the current-cycle Verify decision + the close gate.

> **Evidence timing:** a stage's id only exists after it's POSTed. So the Implement/Verify forms submit
> first; the EvidenceLinker for that stage is rendered by the **drawer timeline** (Task 9) on the now-existing
> stage. The forms themselves carry an inline reminder ("Link effectiveness evidence to this stage after
> recording it"). This keeps the form simple and the linker bound to a real `stage.id`.

- [ ] **Step 1: write the failing test**

```tsx
// apps/web/src/features/capa/StageForms.test.tsx
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { expect, test } from "vitest";
import { AuthContext } from "../../lib/auth";
import type { Capa } from "../../lib/types";
import { theme } from "../../theme/mantine";
import { server } from "../../test/msw/server";
import { TEST_AUTH } from "../../test/render";
import { CloseAction, ContainmentForm, RootCauseForm, VerifyForm } from "./StageForms";

const capa = (over: Partial<Capa> = {}): Capa => ({
  id: "ca000008-0008-0008-0008-000000000008",
  identifier: "REC-000040",
  title: "T",
  source: "audit",
  severity: "Major",
  process_id: null,
  close_state: "Raised",
  cycle_marker: 0,
  origin_finding_id: null,
  raised_by: null,
  created_at: null,
  stages: [],
  ...over,
});

function wrap(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MantineProvider theme={theme}>
      <QueryClientProvider client={client}>
        <AuthContext.Provider value={TEST_AUTH}>{node}</AuthContext.Provider>
      </QueryClientProvider>
    </MantineProvider>,
  );
}

test("ContainmentForm submits the correction content_block", async () => {
  const u = userEvent.setup();
  wrap(<ContainmentForm capa={capa()} />);
  await u.type(screen.getByLabelText("Correction taken"), "Froze POs");
  await u.click(screen.getByRole("button", { name: /Record correction/ }));
  await waitFor(() => expect(screen.getByText(/Recorded/)).toBeInTheDocument());
});

test("RootCauseForm requires a non-empty root cause", async () => {
  wrap(<RootCauseForm capa={capa({ close_state: "Containment" })} />);
  expect(screen.getByRole("button", { name: /Record root cause/ })).toBeDisabled();
});

test("VerifyForm sends decision + narrative and shows the signing confirmation", async () => {
  const u = userEvent.setup();
  wrap(<VerifyForm capa={capa({ close_state: "Implement" })} />);
  await u.click(screen.getByLabelText("Effective"));
  await u.type(screen.getByLabelText("Verification narrative"), "No recurrence");
  // signing checkbox gates submit
  const submit = screen.getByRole("button", { name: /Record verification/ });
  expect(submit).toBeDisabled();
  await u.click(screen.getByLabelText(/Signing as/));
  expect(submit).toBeEnabled();
});

test("CloseAction surfaces a 409 capa_close_incomplete calmly", async () => {
  server.use(
    http.post("/api/v1/capas/:id/close", () =>
      HttpResponse.json({ code: "capa_close_incomplete", title: "Missing evidence" }, { status: 409 }),
    ),
  );
  const u = userEvent.setup();
  // The Close button is always enabled (server-authoritative gate); an effective-Verify CAPA whose close
  // 409s shows the server's message calmly.
  const atVerify = capa({
    close_state: "Verify",
    stages: [
      { id: "vf", stage: "Verify", content_block: { decision: "effective" }, cycle_marker: 0, created_by: "u", created_at: "x", evidence_links: [] },
    ],
  });
  wrap(<CloseAction capa={atVerify} />);
  await u.click(screen.getByRole("button", { name: /Close CAPA/ }));
  expect(await screen.findByText(/Missing evidence/)).toBeInTheDocument();
});

test("CloseAction at a not_effective Verify offers 'Return to root cause'", () => {
  const looped = capa({
    close_state: "Verify",
    stages: [
      { id: "vf", stage: "Verify", content_block: { decision: "not_effective" }, cycle_marker: 0, created_by: "u", created_at: "x", evidence_links: [] },
    ],
  });
  wrap(<CloseAction capa={looped} />);
  expect(screen.getByRole("button", { name: /Return to root cause/ })).toBeInTheDocument();
});
```

- [ ] **Step 2: run it to verify it fails**

Run: `npm test -- StageForms.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: implement the forms**

```tsx
// apps/web/src/features/capa/StageForms.tsx
import {
  Alert,
  Button,
  Checkbox,
  Group,
  Radio,
  Stack,
  Text,
  Textarea,
  TextInput,
} from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import type { Capa } from "../../lib/types";
import {
  useCapaActionPlan,
  useCapaClose,
  useCapaContainment,
  useCapaImplement,
  useCapaRootCause,
  useCapaVerify,
} from "./mutations";

function errText(e: unknown): string {
  return e instanceof ApiError ? e.message : "Something went wrong. Please retry.";
}

// A compact submit row + calm error/success line, shared by the stage forms.
function FormShell({
  error,
  done,
  doneLabel,
  children,
}: {
  error: string | null;
  done: boolean;
  doneLabel: string;
  children: React.ReactNode;
}) {
  return (
    <Stack gap="xs">
      {error && <Alert color="red">{error}</Alert>}
      {done && (
        <Text size="sm" c="teal">
          {doneLabel}
        </Text>
      )}
      {children}
    </Stack>
  );
}

export function ContainmentForm({ capa }: { capa: Capa }) {
  const m = useCapaContainment(capa.id);
  const [correction, setCorrection] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  async function submit() {
    setError(null);
    try {
      await m.mutateAsync({ content_block: { correction, evidence_note: note || undefined } });
      setDone(true);
    } catch (e) {
      setError(errText(e));
    }
  }
  return (
    <FormShell error={error} done={done} doneLabel="Recorded.">
      <Textarea
        label="Correction taken"
        value={correction}
        onChange={(e) => setCorrection(e.currentTarget.value)}
        autosize
        minRows={2}
      />
      <TextInput label="Evidence note (optional)" value={note} onChange={(e) => setNote(e.currentTarget.value)} />
      <Group justify="flex-end">
        <Button onClick={() => void submit()} loading={m.isPending} disabled={correction.trim().length === 0}>
          Record correction
        </Button>
      </Group>
    </FormShell>
  );
}

export function RootCauseForm({ capa }: { capa: Capa }) {
  const m = useCapaRootCause(capa.id);
  const [rootCause, setRootCause] = useState("");
  const [method, setMethod] = useState("5-whys");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  async function submit() {
    setError(null);
    try {
      await m.mutateAsync({ content_block: { root_cause: rootCause, method } });
      setDone(true);
    } catch (e) {
      setError(errText(e));
    }
  }
  return (
    <FormShell error={error} done={done} doneLabel="Recorded.">
      <Textarea
        label="Root cause"
        value={rootCause}
        onChange={(e) => setRootCause(e.currentTarget.value)}
        autosize
        minRows={2}
      />
      <Radio.Group label="Method" value={method} onChange={setMethod}>
        <Group gap="md" mt={4}>
          <Radio value="5-whys" label="5-Whys" />
          <Radio value="fishbone" label="Fishbone" />
          <Radio value="other" label="Other" />
        </Group>
      </Radio.Group>
      <Group justify="flex-end">
        <Button onClick={() => void submit()} loading={m.isPending} disabled={rootCause.trim().length === 0}>
          Record root cause
        </Button>
      </Group>
    </FormShell>
  );
}

export function ActionPlanForm({ capa }: { capa: Capa }) {
  const m = useCapaActionPlan(capa.id);
  const [items, setItems] = useState<string[]>([""]);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const valid = items.some((i) => i.trim().length > 0);
  async function submit() {
    setError(null);
    try {
      await m.mutateAsync({ content_block: { action_items: items.filter((i) => i.trim().length > 0) } });
      setDone(true);
    } catch (e) {
      setError(errText(e));
    }
  }
  return (
    <FormShell error={error} done={done} doneLabel="Action plan proposed — awaiting approval.">
      <Text size="sm" fw={500}>
        Action items
      </Text>
      {items.map((it, i) => (
        <TextInput
          key={i}
          aria-label={`Action item ${i + 1}`}
          value={it}
          onChange={(e) =>
            setItems((prev) => prev.map((p, j) => (j === i ? e.currentTarget.value : p)))
          }
        />
      ))}
      <Group justify="space-between">
        <Button variant="subtle" size="xs" onClick={() => setItems((p) => [...p, ""])}>
          + Add item
        </Button>
        <Button onClick={() => void submit()} loading={m.isPending} disabled={!valid}>
          Propose action plan
        </Button>
      </Group>
    </FormShell>
  );
}

export function ImplementForm({ capa }: { capa: Capa }) {
  const m = useCapaImplement(capa.id);
  const [actionsDone, setActionsDone] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  async function submit() {
    setError(null);
    try {
      await m.mutateAsync({ content_block: { actions_done: actionsDone } });
      setDone(true);
    } catch (e) {
      setError(errText(e));
    }
  }
  return (
    <FormShell error={error} done={done} doneLabel="Recorded — link completion evidence on the stage below.">
      <Textarea
        label="Actions completed"
        value={actionsDone}
        onChange={(e) => setActionsDone(e.currentTarget.value)}
        autosize
        minRows={2}
      />
      <Text size="xs" c="dimmed">
        After recording, link completion evidence to the new Implement stage (required to close).
      </Text>
      <Group justify="flex-end">
        <Button onClick={() => void submit()} loading={m.isPending} disabled={actionsDone.trim().length === 0}>
          Record implementation
        </Button>
      </Group>
    </FormShell>
  );
}

export function VerifyForm({ capa }: { capa: Capa }) {
  const { user } = useAuth();
  const m = useCapaVerify(capa.id);
  const [decision, setDecision] = useState<"effective" | "not_effective" | "">("");
  const [narrative, setNarrative] = useState("");
  const [signed, setSigned] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const who = user?.profile?.name ?? user?.profile?.preferred_username ?? "you";
  const disabled = decision === "" || narrative.trim().length === 0 || !signed || m.isPending;
  async function submit() {
    setError(null);
    if (decision === "") return;
    try {
      await m.mutateAsync({ decision, content_block: { narrative } });
      setDone(true);
    } catch (e) {
      // SoD-4 (verifier ≠ implementer) is a server-only truth → surface 409 sod_self_verify calmly.
      if (e instanceof ApiError && e.status === 409 && e.code === "sod_self_verify")
        setError("You can't verify this CAPA — its action implementer may not verify it (SoD-4).");
      else setError(errText(e));
    }
  }
  return (
    <FormShell error={error} done={done} doneLabel="Verification recorded.">
      <Radio.Group
        label="Effectiveness decision"
        value={decision}
        onChange={(v) => setDecision(v as "effective" | "not_effective")}
        withAsterisk
      >
        <Stack gap="xs" mt={4}>
          <Radio value="effective" label="Effective" />
          <Radio value="not_effective" label="Not effective (loops back to root cause)" />
        </Stack>
      </Radio.Group>
      <Textarea
        label="Verification narrative"
        value={narrative}
        onChange={(e) => setNarrative(e.currentTarget.value)}
        autosize
        minRows={2}
        withAsterisk
      />
      <Text size="xs" c="dimmed">
        Link effectiveness evidence to the new Verify stage below (required to close).
      </Text>
      <Checkbox
        checked={signed}
        onChange={(e) => setSigned(e.currentTarget.checked)}
        label={`Signing as ${who} — meaning: verify`}
      />
      <Group justify="flex-end">
        <Button onClick={() => void submit()} loading={m.isPending} disabled={disabled}>
          Record verification
        </Button>
      </Group>
    </FormShell>
  );
}

// CloseAction does NOT gate the button on a client-derived readiness — the close gate is a SERVER-only
// truth (close_capa). The drawer renders the honest CloseGateStepper (Task 7) right above this panel, so
// the requirements are already visible; here we just submit and surface the server's 409 calmly (its
// message lists exactly what's missing). This avoids any client/server gate drift AND any dependency on
// deriveGate's shape/ordering.
export function CloseAction({ capa }: { capa: Capa }) {
  const m = useCapaClose(capa.id);
  const [error, setError] = useState<string | null>(null);
  const stages = capa.stages ?? [];
  const currentVerify = stages
    .filter((s) => s.stage === "Verify" && s.cycle_marker === capa.cycle_marker)
    .slice(-1)[0];
  const notEffective = currentVerify?.content_block?.decision === "not_effective";

  async function submit() {
    setError(null);
    try {
      await m.mutateAsync();
    } catch (e) {
      // 409 capa_close_incomplete / capa_not_verified — the server's authoritative word (lists missing).
      if (e instanceof ApiError && e.status === 409) setError(e.message);
      else setError(errText(e));
    }
  }

  if (notEffective) {
    return (
      <Stack gap="xs">
        {error && <Alert color="red">{error}</Alert>}
        <Text size="sm" c="dimmed">
          Verification was <b>not effective</b> — closing returns this CAPA to root cause for a revised plan.
        </Text>
        <Group justify="flex-end">
          <Button color="orange" onClick={() => void submit()} loading={m.isPending}>
            Return to root cause
          </Button>
        </Group>
      </Stack>
    );
  }
  return (
    <Stack gap="xs">
      {error && <Alert color="red">{error}</Alert>}
      <Text size="sm" c="dimmed">
        Closing requires root cause + a current-cycle action and effectiveness evidence (see the close gate
        above). The server confirms the gate and reports anything missing.
      </Text>
      <Group justify="flex-end">
        <Button onClick={() => void submit()} loading={m.isPending}>
          Close CAPA
        </Button>
      </Group>
    </Stack>
  );
}
```

> **`CloseAction` no longer imports `deriveGate`** — so `StageForms` has no dependency on the (Task 7)
> evidence-aware `deriveGate` change, and the StageForms suite passes in numeric order. The honest close-gate
> stepper lives only in the drawer (Task 7 + Task 9).

- [ ] **Step 4: run it to verify it passes**

Run: `npm test -- StageForms.test.tsx`
Expected: PASS (all five).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/capa/StageForms.tsx apps/web/src/features/capa/StageForms.test.tsx
git commit -m "feat(s-web-7b): per-stage write forms + close action (calm SoD/M4 409s)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: `AdvancePanel` — the contextual next-step panel (gated)

**Files:**
- Create: `apps/web/src/features/capa/AdvancePanel.tsx`
- Test: `apps/web/src/features/capa/AdvancePanel.test.tsx`

- [ ] **Step 1: write the failing test**

```tsx
// apps/web/src/features/capa/AdvancePanel.test.tsx
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { expect, test } from "vitest";
import { AuthContext } from "../../lib/auth";
import type { Capa } from "../../lib/types";
import { theme } from "../../theme/mantine";
import { server } from "../../test/msw/server";
import { TEST_AUTH } from "../../test/render";
import { AdvancePanel } from "./AdvancePanel";
import { capaApprovalFixture } from "../../test/msw/handlers";

const capa = (over: Partial<Capa> = {}): Capa => ({
  id: "ca000001-0001-0001-0001-000000000001",
  identifier: "REC-000031",
  title: "T",
  source: "audit",
  severity: "Major",
  process_id: "pr000001-0001-0001-0001-000000000001",
  close_state: "Raised",
  cycle_marker: 0,
  origin_finding_id: null,
  raised_by: null,
  created_at: null,
  stages: [],
  ...over,
});

function grant(...keys: string[]) {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "PROCESS", selector: null },
        permissions: keys.map((key) => ({ key, effect: "ALLOW", source: null })),
      }),
    ),
  );
}

function wrap(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MantineProvider theme={theme}>
      <QueryClientProvider client={client}>
        <AuthContext.Provider value={TEST_AUTH}>{node}</AuthContext.Provider>
      </QueryClientProvider>
    </MantineProvider>,
  );
}

test("shows the containment form at Raised when the caller holds capa.update", async () => {
  grant("capa.update");
  wrap(<AdvancePanel capa={capa()} />);
  expect(await screen.findByRole("button", { name: /Record correction/ })).toBeInTheDocument();
});

test("shows a read-only line (no form) when the caller lacks the stage key", async () => {
  grant(); // no keys
  wrap(<AdvancePanel capa={capa()} />);
  expect(await screen.findByText(/don't hold the permission/i)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Record correction/ })).toBeNull();
});

test("at RootCause with a pending approval, shows 'awaiting approval' not the propose form", async () => {
  grant("capa.plan_action");
  server.use(http.get("/api/v1/capas/:id/approval", () => HttpResponse.json(capaApprovalFixture)));
  wrap(<AdvancePanel capa={capa({ close_state: "RootCause" })} />);
  expect(await screen.findByText(/awaiting approval/i)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Propose action plan/ })).toBeNull();
});

test("at RootCause with no approval, shows the propose form", async () => {
  grant("capa.plan_action");
  server.use(http.get("/api/v1/capas/:id/approval", () => HttpResponse.json(null)));
  wrap(<AdvancePanel capa={capa({ close_state: "RootCause" })} />);
  expect(await screen.findByRole("button", { name: /Propose action plan/ })).toBeInTheDocument();
});

test("renders nothing for a terminal CAPA", () => {
  grant("capa.close");
  const { container } = wrap(<AdvancePanel capa={capa({ close_state: "Closed" })} />);
  expect(container.querySelector("button")).toBeNull();
});
```

- [ ] **Step 2: run it to verify it fails**

Run: `npm test -- AdvancePanel.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: implement the panel**

```tsx
// apps/web/src/features/capa/AdvancePanel.tsx
import { Alert, Loader, Text } from "@mantine/core";
import { usePermissions } from "../../app/shell/usePermissions";
import type { Capa } from "../../lib/types";
import { ContentBlock } from "./ContentBlock";
import { useCapaApproval } from "./hooks";
import {
  ActionPlanForm,
  CloseAction,
  ContainmentForm,
  ImplementForm,
  RootCauseForm,
  VerifyForm,
} from "./StageForms";

// An approval instance is "pending" until it reaches a terminal sentinel. NEEDS_ATTENTION = an
// abandoned fail-closed instance (no approver assigned) → re-propose is allowed after assigning one.
const APPROVAL_TERMINAL = ["COMPLETED", "REJECTED", "NEEDS_ATTENTION"];

export function AdvancePanel({ capa }: { capa: Capa }) {
  const scope = capa.process_id
    ? { level: "PROCESS", id: capa.process_id }
    : { level: "SYSTEM" };
  const perms = usePermissions(scope);
  // Only the RootCause state needs the approval read (to distinguish propose-vs-awaiting).
  const approval = useCapaApproval(capa.close_state === "RootCause" ? capa.id : null);

  function gated(key: string, node: React.ReactNode) {
    if (perms.isLoading) return <Loader size="sm" />;
    if (!perms.can(key))
      return (
        <Text size="sm" c="dimmed">
          You don't hold the permission to advance this CAPA.
        </Text>
      );
    return node;
  }

  switch (capa.close_state) {
    case "Raised":
      return gated("capa.update", <ContainmentForm capa={capa} />);
    case "Containment":
      return gated("capa.record_rca", <RootCauseForm capa={capa} />);
    case "RootCause": {
      const inst = approval.data?.instance;
      if (inst && inst.current_state === "NEEDS_ATTENTION")
        return (
          <Alert color="yellow" title="No approver assigned">
            Assign a QMS Owner / Top Management approver, then propose again.
          </Alert>
        );
      if (inst && !APPROVAL_TERMINAL.includes(inst.current_state))
        return (
          <Alert color="blue" title="Action plan awaiting approval">
            <Text size="sm" mb="xs">
              Decided in <b>My Tasks</b> by the assigned approver.
            </Text>
            <ContentBlock block={approval.data?.proposed_action_plan ?? {}} />
          </Alert>
        );
      return gated("capa.plan_action", <ActionPlanForm capa={capa} />);
    }
    case "ActionPlan":
      return gated("capa.capture_effectiveness", <ImplementForm capa={capa} />);
    case "Implement":
      return gated("capa.verify", <VerifyForm capa={capa} />);
    case "Verify":
      return gated("capa.close", <CloseAction capa={capa} />);
    default:
      return null; // Closed / Rejected — terminal
  }
}
```

> Note the `usePermissions` `scope` literal: its type is `{ level: string; id?: string }`. The SYSTEM branch
> omits `id`. TypeScript infers `{ level: string }` vs `{ level: string; id: string }` — both satisfy the
> optional-`id` param. If `tsc` narrows `level` to a string-literal union complaint, annotate:
> `const scope: { level: string; id?: string } = capa.process_id ? … : { level: "SYSTEM" }`.

- [ ] **Step 4: run it to verify it passes**

Run: `npm test -- AdvancePanel.test.tsx`
Expected: PASS (all five). (The CloseAction `gated` path renders for the Verify case; the StageForms close
tests run separately.)

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/capa/AdvancePanel.tsx apps/web/src/features/capa/AdvancePanel.test.tsx
git commit -m "feat(s-web-7b): contextual Advance panel (per-stage, gated, awaiting-approval)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: `CloseGateStepper` — make `deriveGate` evidence-aware

**Files:**
- Modify: `apps/web/src/features/capa/CloseGateStepper.tsx`
- Modify: `apps/web/src/features/capa/CloseGateStepper.test.tsx`

- [ ] **Step 1: update the test to require evidence**

Replace `apps/web/src/features/capa/CloseGateStepper.test.tsx` entirely:

```tsx
import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { expect, test } from "vitest";
import { theme } from "../../theme/mantine";
import type { CapaStage, EvidenceLink } from "../../lib/types";
import { CloseGateStepper, deriveGate } from "./CloseGateStepper";

const ev = (): EvidenceLink => ({
  id: "e", record_id: "r", record_identifier: "REC-1", link_reason: null, created_at: null,
});

const mk = (
  stage: CapaStage["stage"],
  block: Record<string, unknown> = {},
  cycle = 0,
  evidence = 0,
): CapaStage => ({
  id: `${stage}-${cycle}`, stage, content_block: block, cycle_marker: cycle, created_by: "u",
  created_at: "2026-05-20T09:00:00+00:00", evidence_links: Array.from({ length: evidence }, ev),
});

test("deriveGate: root cause is cycle-agnostic; action/effectiveness need current-cycle evidence", () => {
  expect(deriveGate([mk("Raised")], 0)).toEqual({
    rootCause: false, action: false, effectiveness: false,
  });
  // an Implement WITHOUT evidence does NOT satisfy the action step (the M4 gate needs a linked record)
  expect(deriveGate([mk("RootCause"), mk("Implement")], 0)).toEqual({
    rootCause: true, action: false, effectiveness: false,
  });
  // with a current-cycle Implement + an effective Verify, BOTH carrying evidence → all met
  expect(
    deriveGate(
      [mk("RootCause"), mk("Implement", {}, 0, 1), mk("Verify", { decision: "effective" }, 0, 1)],
      0,
    ),
  ).toEqual({ rootCause: true, action: true, effectiveness: true });
  // a not_effective Verify (even with evidence) does NOT mark effectiveness
  expect(
    deriveGate([mk("Verify", { decision: "not_effective" }, 0, 1)], 0).effectiveness,
  ).toBe(false);
});

test("after a not_effective loop: root-cause carries forward, a prior-cycle action does not", () => {
  expect(
    deriveGate(
      [mk("RootCause", {}, 0), mk("Implement", {}, 0, 1), mk("Verify", { decision: "not_effective" }, 0, 1)],
      1,
    ),
  ).toEqual({ rootCause: true, action: false, effectiveness: false });
});

function wrap(stages: CapaStage[], cycleMarker = 0) {
  return render(
    <MantineProvider theme={theme}>
      <CloseGateStepper stages={stages} cycleMarker={cycleMarker} />
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

- [ ] **Step 2: run it to verify it fails**

Run: `npm test -- CloseGateStepper.test.tsx`
Expected: FAIL — the shipped `deriveGate` marks `action` true for an evidence-less Implement and ties
`effectiveness` to `closeState==="Closed"`.

- [ ] **Step 3: make `deriveGate` evidence-aware**

Replace `apps/web/src/features/capa/CloseGateStepper.tsx` with the **2-arg, evidence-aware** version below.
`deriveGate(stages, cycleMarker)` drops the old `closeState` param (no longer needed — effectiveness now
derives from a current-cycle effective Verify **with evidence**, not from `closeState === "Closed"`), and the
`CloseGateStepper` component drops its `closeState` prop. Its only consumers are this file's `deriveGate` (used
internally) and the `CapaDrawer` (Task 9, which passes `stages` + `cycleMarker`) — `StageForms.CloseAction` no
longer imports `deriveGate`, so there is no other call site to update. The full file:

```tsx
import { List, ThemeIcon } from "@mantine/core";
import type { CapaStage } from "../../lib/types";

export interface GateState {
  rootCause: boolean;
  action: boolean;
  effectiveness: boolean;
}

export function deriveGate(stages: CapaStage[], cycleMarker: number): GateState {
  const hasAnyRootCause = stages.some((s) => s.stage === "RootCause");
  const currentWithEvidence = (stage: CapaStage["stage"], extra?: (s: CapaStage) => boolean) =>
    stages.some(
      (s) =>
        s.stage === stage &&
        s.cycle_marker === cycleMarker &&
        (s.evidence_links?.length ?? 0) > 0 &&
        (extra ? extra(s) : true),
    );
  return {
    rootCause: hasAnyRootCause,
    action: currentWithEvidence("Implement"),
    effectiveness: currentWithEvidence("Verify", (s) => s.content_block?.decision === "effective"),
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

export function CloseGateStepper({
  stages,
  cycleMarker,
}: {
  stages: CapaStage[];
  cycleMarker: number;
}) {
  const gate = deriveGate(stages, cycleMarker);
  return (
    <List spacing="xs" size="sm" center>
      <Step done={gate.rootCause} label="Root cause documented" />
      <Step done={gate.action} label="Corrective action defined" />
      <Step done={gate.effectiveness} label="Effectiveness evidence" />
    </List>
  );
}
```

(The Step-1 test above already uses the 2-arg `deriveGate(stages, cycleMarker)` + the propless
`<CloseGateStepper stages={} cycleMarker={} />`.)

- [ ] **Step 4: run it to verify it passes**

Run: `npm test -- CloseGateStepper.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/capa/CloseGateStepper.tsx apps/web/src/features/capa/CloseGateStepper.test.tsx
git commit -m "feat(s-web-7b): evidence-aware close-gate (current-cycle Implement/Verify with evidence)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: `RaiseCapaModal` — board-level create

**Files:**
- Create: `apps/web/src/features/capa/RaiseCapaModal.tsx`
- Test: `apps/web/src/features/capa/RaiseCapaModal.test.tsx`

- [ ] **Step 1: write the failing test**

```tsx
// apps/web/src/features/capa/RaiseCapaModal.test.tsx
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { expect, test, vi } from "vitest";
import { AuthContext } from "../../lib/auth";
import { theme } from "../../theme/mantine";
import { TEST_AUTH } from "../../test/render";
import { RaiseCapaModal } from "./RaiseCapaModal";

function wrap(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MantineProvider theme={theme}>
      <QueryClientProvider client={client}>
        <AuthContext.Provider value={TEST_AUTH}>{node}</AuthContext.Provider>
      </QueryClientProvider>
    </MantineProvider>,
  );
}

test("creates a CAPA and calls onCreated with the new id", async () => {
  const u = userEvent.setup();
  const onCreated = vi.fn();
  wrap(<RaiseCapaModal opened onClose={vi.fn()} onCreated={onCreated} />);
  await u.type(screen.getByLabelText("Title"), "Torque wrench miscalibration");
  await u.click(screen.getByLabelText("Severity"));
  await u.click(await screen.findByRole("option", { name: "Minor" }));
  await u.click(screen.getByRole("button", { name: /Raise CAPA/ }));
  await vi.waitFor(() => expect(onCreated).toHaveBeenCalled());
});

test("does not offer review_output as a source", async () => {
  const u = userEvent.setup();
  wrap(<RaiseCapaModal opened onClose={vi.fn()} onCreated={vi.fn()} />);
  await u.click(screen.getByLabelText("Source"));
  expect(screen.queryByRole("option", { name: /Mgmt review/ })).toBeNull();
});
```

- [ ] **Step 2: run it to verify it fails**

Run: `npm test -- RaiseCapaModal.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: implement the modal**

```tsx
// apps/web/src/features/capa/RaiseCapaModal.tsx
import { Alert, Button, Group, Modal, Select, Stack, Textarea, TextInput } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import type { CapaSource, NcSeverity } from "../../lib/types";
import { useRaiseCapa } from "./mutations";

// source omits review_output (reserved for the Management-Review family — the API 422s it).
const SOURCES: { value: CapaSource; label: string }[] = [
  { value: "audit", label: "Audit" },
  { value: "process", label: "Process" },
  { value: "complaint", label: "Complaint" },
];

export function RaiseCapaModal({
  opened,
  onClose,
  onCreated,
}: {
  opened: boolean;
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const m = useRaiseCapa();
  const [title, setTitle] = useState("");
  const [severity, setSeverity] = useState<NcSeverity | null>(null);
  const [source, setSource] = useState<CapaSource>("process");
  const [problem, setProblem] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setError(null);
    if (!severity) return;
    try {
      const capa = await m.mutateAsync({
        title,
        severity,
        source,
        problem: problem.trim() || undefined,
      });
      onCreated(capa.id);
      setTitle("");
      setSeverity(null);
      setProblem("");
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not raise the CAPA.");
    }
  }

  return (
    <Modal opened={opened} onClose={onClose} title="Raise CAPA">
      <Stack gap="sm">
        {error && <Alert color="red">{error}</Alert>}
        <TextInput label="Title" required value={title} onChange={(e) => setTitle(e.currentTarget.value)} />
        <Select
          label="Severity"
          required
          placeholder="Pick a severity"
          value={severity}
          onChange={(v) => setSeverity(v as NcSeverity)}
          data={["Critical", "Major", "Minor"]}
        />
        <Select
          label="Source"
          value={source}
          onChange={(v) => setSource((v as CapaSource) ?? "process")}
          data={SOURCES}
        />
        <Textarea
          label="Problem (optional)"
          value={problem}
          onChange={(e) => setProblem(e.currentTarget.value)}
          autosize
          minRows={2}
        />
        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={() => void submit()}
            loading={m.isPending}
            disabled={title.trim().length === 0 || !severity}
          >
            Raise CAPA
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
```

- [ ] **Step 4: run it to verify it passes**

Run: `npm test -- RaiseCapaModal.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/capa/RaiseCapaModal.tsx apps/web/src/features/capa/RaiseCapaModal.test.tsx
git commit -m "feat(s-web-7b): Raise CAPA modal (board-level create)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Wire the drawer — Advance panel + per-stage evidence + 2-arg stepper

**Files:**
- Modify: `apps/web/src/features/capa/CapaDrawer.tsx`
- Modify: `apps/web/src/features/capa/CapaTimeline.tsx` (render per-stage evidence + the linker)
- Modify: `apps/web/src/features/capa/CapaDrawer.test.tsx` (add Advance + evidence tests)

- [ ] **Step 1: render evidence + the linker in the timeline**

Replace `apps/web/src/features/capa/CapaTimeline.tsx` (add the linked-evidence list + the EvidenceLinker on
Implement/Verify stages; the timeline now needs the `capaId` to bind the linker):

```tsx
import { Anchor, Group, Text, Timeline } from "@mantine/core";
import type { CapaStage, DirectoryUser } from "../../lib/types";
import { ContentBlock } from "./ContentBlock";
import { EvidenceLinker } from "./EvidenceLinker";

function actorLabel(userId: string, directory: DirectoryUser[]): string {
  const hit = directory.find((u) => u.id === userId);
  return hit?.display_name ?? `${userId.slice(0, 8)}…`;
}

function formatDate(iso: string): string {
  return new Date(iso).toISOString().slice(0, 10);
}

const EVIDENCE_STAGES = new Set(["Implement", "Verify"]);

export function CapaTimeline({
  stages,
  directory,
  capaId,
}: {
  stages: CapaStage[];
  directory: DirectoryUser[];
  capaId: string;
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
                  &middot; Cycle {s.cycle_marker + 1}
                </Text>
              ) : null}
            </Text>
          }
        >
          <Text size="xs" c="dimmed" mb={4}>
            {formatDate(s.created_at)} &middot; {actorLabel(s.created_by, directory)}
          </Text>
          <ContentBlock block={s.content_block} />
          {(s.evidence_links?.length ?? 0) > 0 && (
            <Group gap="xs" mt={4}>
              <Text size="xs" fw={600} c="dimmed">
                Linked records:
              </Text>
              {s.evidence_links!.map((l) => (
                <Anchor key={l.id} size="xs" component="span">
                  {l.record_identifier ?? l.record_id}
                </Anchor>
              ))}
            </Group>
          )}
          {EVIDENCE_STAGES.has(s.stage) && (
            <div style={{ marginTop: 6 }}>
              <EvidenceLinker capaId={capaId} stageId={s.id} />
            </div>
          )}
        </Timeline.Item>
      ))}
    </Timeline>
  );
}
```

> This updates the 7a `CapaTimeline` signature (adds `capaId`). The 7a `CapaTimeline.test.tsx` passes
> `<CapaTimeline stages={} directory={} />` — add `capaId="ca1"` to each of its `wrap(...)` render calls so it
> still compiles. (Open `CapaTimeline.test.tsx`, change the `wrap` helper to pass `capaId="ca1"`.)

- [ ] **Step 2: wire the drawer**

Replace the body of `apps/web/src/features/capa/CapaDrawer.tsx` (mount the `AdvancePanel`, pass `capaId` to the
timeline, switch the stepper to the 2-arg `cycleMarker` prop):

```tsx
import { Alert, Badge, Group, Loader, Stack, Text, Title } from "@mantine/core";
import { DetailDrawer } from "../../app/shell/DetailDrawer";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import { AdvancePanel } from "./AdvancePanel";
import { SEVERITY_COLOR, SEVERITY_LABEL, SOURCE_LABEL } from "./columns";
import { CapaTimeline } from "./CapaTimeline";
import { CloseGateStepper } from "./CloseGateStepper";
import { useCapa } from "./hooks";

export function CapaDrawer({ capaId, onClose }: { capaId: string | null; onClose: () => void }) {
  const { data: capa, isLoading, isError } = useCapa(capaId);
  const { data: directory } = useUserDirectory();

  return (
    <DetailDrawer
      opened={capaId !== null}
      onClose={onClose}
      title={
        capa && !isError ? (
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
      {isLoading ? (
        <Loader />
      ) : isError || !capa ? (
        <Alert color="red" title="Couldn't load this CAPA">
          It may have been removed, or you may not have access. Close this panel and try again.
        </Alert>
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
            <CapaTimeline stages={capa.stages ?? []} directory={directory ?? []} capaId={capa.id} />
          </div>

          <div>
            <Title order={5} mb="sm">
              Close gate
            </Title>
            <CloseGateStepper stages={capa.stages ?? []} cycleMarker={capa.cycle_marker} />
          </div>

          <div>
            <Title order={5} mb="sm">
              Next step
            </Title>
            <AdvancePanel capa={capa} />
          </div>
        </Stack>
      )}
    </DetailDrawer>
  );
}
```

- [ ] **Step 3: add the drawer tests**

Append to `apps/web/src/features/capa/CapaDrawer.test.tsx` (it uses `renderWithProviders`; grant the stage key
so the Advance form renders):

```tsx
import { http, HttpResponse } from "msw";
import { server } from "../../test/msw/server";

test("renders the Advance panel form for the caller's permitted stage", async () => {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "PROCESS", selector: null },
        permissions: [{ key: "capa.record_rca", effect: "ALLOW", source: null }],
      }),
    ),
  );
  // capaDetailFixture is at close_state RootCause? No — it's RootCause's source is Containment. The
  // default detail (ca000001…) is close_state "RootCause"; its Advance is the action-plan propose form.
  // Use a Containment-state CAPA to exercise the root-cause form:
  server.use(
    http.get("/api/v1/capas/:id", () =>
      HttpResponse.json({
        id: "ca000002-0002-0002-0002-000000000002",
        identifier: "REC-000034",
        title: "Containment-state CAPA",
        source: "complaint",
        severity: "Critical",
        process_id: "pr1",
        close_state: "Containment",
        cycle_marker: 0,
        origin_finding_id: null,
        raised_by: "bbbb1111-1111-1111-1111-111111111111",
        created_at: "2026-05-28T09:00:00+00:00",
        stages: [
          { id: "s1", stage: "Raised", content_block: { problem: "x" }, cycle_marker: 0, created_by: "bbbb1111-1111-1111-1111-111111111111", created_at: "2026-05-28T09:00:00+00:00", evidence_links: [] },
        ],
      }),
    ),
  );
  renderWithProviders(<CapaDrawer capaId="ca000002-0002-0002-0002-000000000002" onClose={() => {}} />);
  expect(await screen.findByRole("button", { name: /Record root cause/ })).toBeInTheDocument();
});
```

(Ensure `screen` is imported in the test file; the 7a test already imports from `@testing-library/react`.)

- [ ] **Step 4: run the drawer + timeline tests**

Run: `npm test -- CapaDrawer.test.tsx CapaTimeline.test.tsx`
Expected: PASS (the 7a tests still pass with the added `capaId` prop; the new Advance test passes).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/capa/CapaDrawer.tsx apps/web/src/features/capa/CapaTimeline.tsx apps/web/src/features/capa/CapaDrawer.test.tsx apps/web/src/features/capa/CapaTimeline.test.tsx
git commit -m "feat(s-web-7b): drawer drives the lifecycle (Advance panel + per-stage evidence)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Wire the board — the Raise CAPA button

**Files:**
- Modify: `apps/web/src/features/capa/CapaBoardPage.tsx`
- Modify: `apps/web/src/features/capa/CapaBoardPage.test.tsx`

- [ ] **Step 1: add the Raise button + modal to the board**

In `apps/web/src/features/capa/CapaBoardPage.tsx`:
- Add imports:
  ```tsx
  import { Button } from "@mantine/core";
  import { usePermissions } from "../../app/shell/usePermissions";
  import { RaiseCapaModal } from "./RaiseCapaModal";
  ```
- Add state near the other `useState`s (line ~33):
  ```tsx
  const [raiseOpen, setRaiseOpen] = useState(false);
  const perms = usePermissions();
  ```
- In the header `Group` (lines 87-97), add a Raise button before the `SegmentedControl` (only when the caller
  can create):
  ```tsx
      <Group justify="space-between" mb="md">
        <Title order={2}>Nonconformity &amp; CAPA</Title>
        <Group gap="sm">
          {perms.can("capa.create") && (
            <Button onClick={() => setRaiseOpen(true)}>＋ Raise CAPA</Button>
          )}
          <SegmentedControl
            value={view}
            onChange={(v) => setView(v as "board" | "list")}
            data={[
              { value: "board", label: "Board" },
              { value: "list", label: "List" },
            ]}
          />
        </Group>
      </Group>
  ```
- Before the closing `</Container>` (after the `<CapaDrawer .../>`), mount the modal (open the new CAPA's
  drawer on create):
  ```tsx
      <RaiseCapaModal
        opened={raiseOpen}
        onClose={() => setRaiseOpen(false)}
        onCreated={(id) => setSelected(id)}
      />
  ```

- [ ] **Step 2: add the board test**

Append to `apps/web/src/features/capa/CapaBoardPage.test.tsx`:

```tsx
test("shows the Raise CAPA button when the caller holds capa.create and opens the modal", async () => {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: [{ key: "capa.create", effect: "ALLOW", source: null }],
      }),
    ),
  );
  const u = userEvent.setup();
  renderWithProviders(<CapaBoardPage />, { route: "/capa" });
  const raise = await screen.findByRole("button", { name: /Raise CAPA/ });
  await u.click(raise);
  expect(await screen.findByLabelText("Title")).toBeInTheDocument();
});
```

(Ensure `http`, `HttpResponse`, `server`, `userEvent` are imported in the test file — the 7a board test
already imports `http`/`HttpResponse`/`server`/`userEvent`.)

- [ ] **Step 3: run the board test**

Run: `npm test -- CapaBoardPage.test.tsx`
Expected: PASS (the 7a tests still pass — the button is hidden by default since the default `/me/permissions`
returns no keys).

- [ ] **Step 4: Commit**

```bash
git add apps/web/src/features/capa/CapaBoardPage.tsx apps/web/src/features/capa/CapaBoardPage.test.tsx
git commit -m "feat(s-web-7b): board Raise CAPA button + modal

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: The action-plan approval integration (the /tasks branch)

**Files:**
- Modify: `apps/web/src/features/review/hooks.ts` (subject-agnostic `useDecideTask`).
- Modify: `apps/web/src/features/review/DecisionCard.tsx` (subject props).
- Modify: `apps/web/src/features/review/DecisionCard.test.tsx` (new props).
- Create: `apps/web/src/features/review/CapaApprovalContext.tsx`
- Test: `apps/web/src/features/review/CapaApprovalContext.test.tsx`
- Modify: `apps/web/src/features/review/ReviewApprovePage.tsx` (subject branch).
- Modify: `apps/web/src/features/review/ReviewApprovePage.test.tsx` (a CAPA-task case).

- [ ] **Step 1: generalize `useDecideTask`**

In `apps/web/src/features/review/hooks.ts`, replace `DecideInput` + `useDecideTask`:

```ts
export interface DecideInput {
  taskId: string;
  subjectType: "DOCUMENT" | "CAPA";
  subjectId: string; // the document id or capa id — for cache invalidation
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
    onSuccess: (_d, { taskId, subjectType, subjectId }) => {
      void qc.invalidateQueries({ queryKey: ["task", taskId] });
      void qc.invalidateQueries({ queryKey: ["tasks"] });
      if (subjectType === "DOCUMENT") {
        void qc.invalidateQueries({ queryKey: ["document", subjectId] });
        void qc.invalidateQueries({ queryKey: ["document-approval", subjectId] });
        void qc.invalidateQueries({ queryKey: ["document-versions", subjectId] });
      } else {
        void qc.invalidateQueries({ queryKey: ["capa", subjectId] });
        void qc.invalidateQueries({ queryKey: ["capas"] });
        void qc.invalidateQueries({ queryKey: ["capa-approval", subjectId] });
      }
    },
  });
}
```

- [ ] **Step 2: generalize `DecisionCard`**

In `apps/web/src/features/review/DecisionCard.tsx`, change the props + the `mutateAsync` call:
- Signature: `export function DecisionCard({ taskId, subjectType, subjectId }: { taskId: string; subjectType: "DOCUMENT" | "CAPA"; subjectId: string }) {`
- In `submit`, change the mutate payload from `{ taskId, documentId, idempotencyKey: idemKey, body: … }` to:
  ```tsx
      await decide.mutateAsync({
        taskId,
        subjectType,
        subjectId,
        idempotencyKey: idemKey,
        body: { outcome, comment: comment.trim() || undefined },
      });
  ```
- The signing-checkbox label/meaning stay "approval" (the engine signs the ActionPlan stage with meaning
  `approval` for a CAPA — same word).

- [ ] **Step 3: update `DecisionCard.test.tsx`**

In `apps/web/src/features/review/DecisionCard.test.tsx`, change every `<DecisionCard taskId={TASK}
documentId={DOC} />` (4 occurrences) to `<DecisionCard taskId={TASK} subjectType="DOCUMENT" subjectId={DOC} />`.

- [ ] **Step 4: write the failing `CapaApprovalContext` test**

```tsx
// apps/web/src/features/review/CapaApprovalContext.test.tsx
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { capaApprovalFixture } from "../../test/msw/handlers";
import { CapaApprovalContext } from "./CapaApprovalContext";

test("shows the CAPA identity + the proposed action plan being approved", async () => {
  server.use(http.get("/api/v1/capas/:id/approval", () => HttpResponse.json(capaApprovalFixture)));
  renderWithProviders(
    <CapaApprovalContext capaId="ca000001-0001-0001-0001-000000000001" />,
    { route: "/tasks/x" },
  );
  expect(await screen.findByText(/REC-000031/)).toBeInTheDocument();
  expect(await screen.findByText(/Proposed action plan/)).toBeInTheDocument();
  expect(await screen.findByText(/Schedule supplier re-evaluations/)).toBeInTheDocument();
});
```

Add `import { screen } from "@testing-library/react";` at the top.

- [ ] **Step 5: run it to verify it fails**

Run: `npm test -- CapaApprovalContext.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 6: implement `CapaApprovalContext`**

```tsx
// apps/web/src/features/review/CapaApprovalContext.tsx
import { Badge, Group, Loader, Stack, Text, Title } from "@mantine/core";
import { ContentBlock } from "../capa/ContentBlock";
import { SEVERITY_COLOR, SEVERITY_LABEL, SOURCE_LABEL } from "../capa/columns";
import { useCapa, useCapaApproval } from "../capa/hooks";

// The CAPA-subject context on the /tasks decision page: identity + the proposed action plan the approver
// is signing. Both reads are gated capa.read (NOT document.read), so a Top-Management approver works.
export function CapaApprovalContext({ capaId }: { capaId: string }) {
  const { data: capa, isLoading } = useCapa(capaId);
  const { data: approval } = useCapaApproval(capaId);
  if (isLoading || !capa) return <Loader aria-label="Loading CAPA" />;
  return (
    <Stack gap="md">
      <div>
        <Text size="xs" c="dimmed">
          {capa.identifier ?? "CAPA"}
        </Text>
        <Title order={4}>{capa.title ?? "(untitled)"}</Title>
      </div>
      <Group gap="xs">
        <Badge color={SEVERITY_COLOR[capa.severity]} variant="light">
          {SEVERITY_LABEL[capa.severity]}
        </Badge>
        <Badge variant="outline" color="gray">
          {SOURCE_LABEL[capa.source]}
        </Badge>
      </Group>
      <div>
        <Title order={5} mb="xs">
          Proposed action plan
        </Title>
        {approval?.proposed_action_plan ? (
          <ContentBlock block={approval.proposed_action_plan} />
        ) : (
          <Text size="sm" c="dimmed">
            No action plan is attached to this approval.
          </Text>
        )}
      </div>
    </Stack>
  );
}
```

- [ ] **Step 7: run it to verify it passes**

Run: `npm test -- CapaApprovalContext.test.tsx`
Expected: PASS.

- [ ] **Step 8: branch `ReviewApprovePage` on subject type**

Replace `apps/web/src/features/review/ReviewApprovePage.tsx`:

```tsx
import { Alert, Anchor, Grid, Loader, Stack, Text, Title } from "@mantine/core";
import { Link, useParams } from "react-router-dom";
import { ApiError } from "../../lib/api";
import { useDocument } from "../document/useDocument";
import { useDocumentVersions } from "../document/useDocumentVersions";
import { VersionCompare } from "../document/VersionCompare";
import { CapaApprovalContext } from "./CapaApprovalContext";
import { DecisionCard } from "./DecisionCard";
import { useTask, useWorkflowInstance } from "./hooks";

// S-web-5 + S-web-7b: the per-task focus page. Branches on the task's subject type:
//  - DOCUMENT → instance → document → redline + the decision card (unchanged).
//  - CAPA → the CAPA approval context (identity + proposed plan, gated capa.read) + the decision card.
// The decision POST dispatches on subject type server-side, so the same DecisionCard drives both.
export function ReviewApprovePage() {
  const { id: taskId = null } = useParams();
  const { data: task, isLoading, isError, error } = useTask(taskId);
  const isCapa = task?.subject_type === "CAPA";
  // Document branch (unchanged): resolve the subject doc via the instance. Disabled for a CAPA task.
  const { data: instance } = useWorkflowInstance(!isCapa && task ? task.instance_id : null);
  const docId = !isCapa ? (instance?.subject_id ?? null) : null;
  const { data: doc } = useDocument(docId, { enabled: docId !== null });
  const { data: versions } = useDocumentVersions(docId, docId !== null);

  if (isLoading) return <Loader aria-label="Loading task" />;
  if (isError || !task) {
    const status = error instanceof ApiError ? error.status : 0;
    return (
      <Alert color={status === 404 ? "yellow" : "red"} title="Task unavailable">
        <Stack gap="xs" align="flex-start">
          <Text size="sm">
            {status === 404
              ? "This task doesn't exist or isn't assigned to you."
              : "Could not load this task."}
          </Text>
          <Anchor component={Link} to="/tasks">
            ← Back to your tasks
          </Anchor>
        </Stack>
      </Alert>
    );
  }

  const decidable = task.state === "PENDING";
  const decisionPane = decidable ? (
    <DecisionCard
      taskId={task.id}
      subjectType={isCapa ? "CAPA" : "DOCUMENT"}
      subjectId={(isCapa ? task.subject_id : docId) ?? ""}
    />
  ) : (
    <Alert color="blue" title="Decided">
      This task has already been decided.
    </Alert>
  );

  if (isCapa) {
    return (
      <Stack gap="lg">
        <Title order={2}>Review &amp; Approve — Action plan</Title>
        <Grid gutter="lg" align="flex-start">
          <Grid.Col span={{ base: 12, md: 7 }}>
            <CapaApprovalContext capaId={task.subject_id!} />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 5 }}>{decisionPane}</Grid.Col>
        </Grid>
      </Stack>
    );
  }

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
        <Grid.Col span={{ base: 12, md: 5 }}>{decisionPane}</Grid.Col>
      </Grid>
    </Stack>
  );
}
```

- [ ] **Step 9: add the CAPA-task case to `ReviewApprovePage.test.tsx`**

Append to `apps/web/src/features/review/ReviewApprovePage.test.tsx` (reuse the `mount` helper + the
`capaApprovalTask`/`capaApprovalFixture` fixtures):

```tsx
import { capaApprovalFixture, capaApprovalTask } from "../../test/msw/handlers";

test("a CAPA action-plan task renders the proposed plan + a working decision card", async () => {
  server.use(
    http.get("/api/v1/tasks/:id", () => HttpResponse.json(capaApprovalTask)),
    http.get("/api/v1/capas/:id/approval", () => HttpResponse.json(capaApprovalFixture)),
  );
  const { findByText, findByRole } = mount("/tasks/tkca1111-1111-1111-1111-111111111111");
  expect(await findByText(/Schedule supplier re-evaluations/)).toBeInTheDocument();
  expect(await findByRole("button", { name: "Submit decision" })).toBeInTheDocument();
});
```

- [ ] **Step 10: run the review suite**

Run: `npm test -- DecisionCard.test.tsx ReviewApprovePage.test.tsx CapaApprovalContext.test.tsx`
Expected: PASS (the document tests unchanged; the CAPA-task test green).

- [ ] **Step 11: Commit**

```bash
git add apps/web/src/features/review/hooks.ts apps/web/src/features/review/DecisionCard.tsx apps/web/src/features/review/DecisionCard.test.tsx apps/web/src/features/review/CapaApprovalContext.tsx apps/web/src/features/review/CapaApprovalContext.test.tsx apps/web/src/features/review/ReviewApprovePage.tsx apps/web/src/features/review/ReviewApprovePage.test.tsx
git commit -m "feat(s-web-7b): subject-aware /tasks approval (CAPA action-plan branch)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: Full gate + diff-critic + docs

**Files:**
- Modify: `CLAUDE.md` (Recent learnings + Current status).
- Modify: `docs/slice-history.md` (the S-web-7b entry).

- [ ] **Step 1: full web gate**

Run: `/check-web` (eslint + strict `tsc` + build + the whole vitest suite).
Expected: ALL PASS. Fix any `noUncheckedIndexedAccess` array-index nits the per-file runs missed (e.g. the
`evidence_links![0]` access, the `slice(-1)[0]` in `CloseAction`). The `currentVerify` access in `CloseAction`
is already guarded with optional chaining.

- [ ] **Step 2: contracts gate**

Run: `/check-contracts`.
Expected: redocly lint passes (re-confirms the Task 1 contract edits).

- [ ] **Step 3: run the diff-critic agent**

Dispatch the `diff-critic` agent (Agent tool, `subagent_type: diff-critic`) on the branch diff
(`feat/s-web-7b-capa-lifecycle-writes` vs `main`). Fold only confirmed findings; re-run `/check-web` after any
fix. Focus the critic on: the close-gate derivation matching `close_capa` (cycle-agnostic root-cause vs
current-cycle-with-evidence), the MSW fixtures matching the real serializers (`_stage.evidence_links`,
`CapaApproval`, `Task.subject_type`), the DOCUMENT approval path staying byte-identical, and no write button
rendering for a caller who'd 403.

- [ ] **Step 4: update `CLAUDE.md`**

Add a Recent-learnings bullet (newest first; demote the oldest if >12). Draft:

```markdown
- 2026-06-09 — **S-web-7b (CAPA lifecycle writes) CLOSES the ACT-phase write loop** — the drawer drives
  raise→containment→root-cause→action-plan[approved]→implement→verify[SIGNED]→close + the Verify→RootCause
  loop + the M4 evidence close gate. **NOT pure front-end** (the epic said so, but the approval loop needs it):
  a **thin read-enrichment** mirroring 7a — `subject_type`/`subject_id` on the `_task` **detail** serializer, a
  new **`GET /capas/{id}/approval`** (gated `capa.read`, the `/documents/{id}/approval` mirror), and
  `evidence_links` per `_stage` (no migration, no new key). ⚠ The seeded **Top-Management** approver of a
  Critical CAPA holds **only `capa.read`** (0038), so the document-subject `GET /workflow-instances/{id}`
  (`document.read`-gated) **403s them** — the whole CAPA approval path must avoid `document.read` (route via
  `task.subject_type` + the capa.read approval read, never the instance read). The `/tasks` `ReviewApprovePage`
  was **document-only** (blindly `GET /documents/{capa_id}` → 404) → branch it on `subject_type`; `DecisionCard`
  generalized to `{subjectType, subjectId}` (the decision POST already dispatches CAPA→`decide_capa_action_plan`
  server-side). **Close gate = `close_capa` EXACTLY**: root_cause cycle-AGNOSTIC; implemented-action +
  effectiveness CURRENT-cycle **with ≥1 linked evidence record** — so `deriveGate(stages, cycleMarker)` is
  evidence-aware (the 7a stage-presence guess is gone). Evidence = link an EXISTING record
  (`POST /records/{id}/evidence-links target_type=capa_stage`), no upload. SoD-4 (`409 sod_self_verify`) + M4
  (`409 capa_close_incomplete`) are server-only → surfaced calmly. `review_output` source is reserved (422) →
  omit it from the Raise modal.
```

Update the **Current status** block: change "S-ing-4b … (PR open)" to merged (#102/#103 as assigned) and add
the S-web-7b line; bump the web-test count; note migration head is **still 0044** (7b added no migration). Keep
it to the existing terse style.

- [ ] **Step 5: update `docs/slice-history.md`**

Add an `S-web-7b` entry in the web-track section mirroring the S-web-7a entry's format: the scope (the 6 stage
forms + raise + the approval branch + evidence + close), the thin backend (the three reads, no migration/key),
the close-gate semantics, the Top-Management/`document.read` finding, and the new files.

- [ ] **Step 6: Commit the docs**

```bash
git add CLAUDE.md docs/slice-history.md
git commit -m "docs(s-web-7b): record the CAPA lifecycle-writes slice + learnings

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 7: open the PR**

Use `/pr` (or `gh pr create`) against `main` with a summary covering: the closed ACT-phase write loop, the
thin backend read-enrichment (no migration/key), the Top-Management/`document.read` integration fix, and the
close-gate semantics. Address the Codex bot on every thread (reply + resolve via `gh api` — note the
leading-slash MSYS gotcha: use the path WITHOUT a leading slash); it re-reviews per push and finds real issues.

---

## Self-review notes (for the executor)

- **`deriveGate` is 2-arg** `(stages, cycleMarker)` after Task 7 and used **only** inside `CloseGateStepper`
  (the drawer renders the stepper). `StageForms.CloseAction` does NOT import it (it relies on the drawer stepper
  + the server 409). The `closeState` prop is removed from `CloseGateStepper` (its one caller is `CapaDrawer`).
- **`CapaTimeline` gained `capaId`** (Task 9) — the 7a `CapaTimeline.test.tsx` must pass it.
- **`DecisionCard` props changed** `documentId` → `{subjectType, subjectId}` (Task 11) — `DecisionCard.test.tsx`
  + `ReviewApprovePage` (both branches) updated.
- **The document approval path stays byte-identical** — the only change is the new `isCapa` branch + the
  `subjectType`/`subjectId` plumb-through; the existing `ReviewApprovePage` document tests are the regression
  backstop, keep them green.
- **Every write affordance is gated** at the CAPA's PROCESS scope (`AdvancePanel`) or SYSTEM (`Raise`); the
  default MSW `/me/permissions` returns no keys, so gating tests must `server.use` the keys.
- **Fixtures are pinned to Task 1's serializers** — `_stage.evidence_links`, the `CapaApproval`
  `{instance, proposed_action_plan}` shape, `Task.subject_type`/`subject_id`. Don't invent shapes.
```
