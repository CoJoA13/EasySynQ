# S-mr-2 — Management Review UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface the S-mr-1 Management Review backend (clause 9.3) in the SPA — the `/management-reviews` register + detail/lifecycle cockpit, the `/tasks` MR_INPUT/MR_ACTION legs, and the Home "next review" widget — plus three migration-free backend touches the UI needs (a cadence read, an honest MR_INPUT lifecycle, a de-duplicated scorecard).

**Architecture:** The MR is a `kind=DOCUMENT` subtype, so its FE register/detail/lifecycle mirror `features/objectives/` and reuse the shared `features/document/ApprovalStepper`. Backend-first: three small touches land + their tests, then the FE pins MSW fixtures to the as-built serializers. No migration (head stays `0050`), no new permission key (catalog stays 100), no new enum.

**Tech Stack:** FastAPI / Python 3.12 (api) · React/TS + Mantine + @tanstack/react-query (web) · MSW + vitest + jest-axe (web tests) · pytest + testcontainers (api integration) · redocly (contracts).

**Gate:** `/check-api` + `/check-web` + `/check-contracts` (no `/check-migrations` — no migration). diff-critic on the branch diff pre-PR. Live smoke via Chrome MCP pre-merge.

**Spec:** `docs/superpowers/specs/2026-06-12-s-mr-2-management-review-ui-design.md` (R45; the as-built serializers at `apps/api/src/easysynq_api/api/mgmt_review.py:106-177`).

**Branch:** `feat/s-mr-2-management-review-ui` (already created; the spec is committed at `500fd5c`).

---

## File map

**Backend (modify):**
- `apps/api/src/easysynq_api/services/objectives/queries.py` — add `compute_scorecard` (Task 1)
- `apps/api/src/easysynq_api/services/objectives/__init__.py` — export `compute_scorecard` (Task 1)
- `apps/api/src/easysynq_api/api/objectives.py` — `scorecard_endpoint` routes through `compute_scorecard` (Task 1)
- `apps/api/src/easysynq_api/services/mgmt_review/compile.py` — `_objectives_scorecard` → `compute_scorecard`; drop dead imports (Task 1)
- `apps/api/src/easysynq_api/services/mgmt_review/cadence.py` — add `read_cadence` + `mr_review_state` + `MR_REVIEW_LEAD_DAYS`; refactor the sweep to use `read_cadence` (Task 2)
- `apps/api/src/easysynq_api/api/mgmt_review.py` — add `GET /management-reviews/next-due` before `/{review_id}` (Task 2)
- `packages/contracts/openapi.yaml` — add the `next-due` path + `ManagementReviewNextDue` schema (Task 2)
- `apps/api/src/easysynq_api/services/mgmt_review/service.py` — MR_INPUT auto-resolve in `submit_review_for_review` (Task 3)

**Backend (create — tests):**
- `apps/api/tests/unit/test_mgmt_review_routes.py` — the `/next-due` route-ordering proof (Task 2)
- (extend) `apps/api/tests/unit/test_mgmt_review_cadence.py` — `mr_review_state` unit math (Task 2)
- (extend) `apps/api/tests/integration/test_mgmt_review.py` — the `next-due` endpoint + the MR_INPUT auto-resolve (Tasks 2, 3)

**Web (create):** `apps/web/src/features/management-review/{hooks.ts,mutations.ts,labels.ts,ManagementReviewsRegisterPage.tsx,ManagementReviewDetailPage.tsx,ReviewInputsSection.tsx,ReviewOutputsSection.tsx,NewManagementReviewModal.tsx,AddOutputModal.tsx}` + colocated `*.test.tsx`; `apps/web/src/features/review/{MgmtReviewContext.tsx,MrActionCard.tsx,mrTaskHooks.ts}`; `apps/web/src/features/home/NextReviewLine.tsx`.
**Web (modify):** `apps/web/src/lib/types.ts` · `apps/web/src/test/msw/handlers.ts` · `apps/web/src/app/shell/LeftRail.tsx` · `apps/web/src/App.tsx` · `apps/web/src/features/review/ReviewApprovePage.tsx` · `apps/web/src/features/home/CheckCard.tsx`.

---

## Phase A — Backend (three touches)

### Task 1: shared `compute_scorecard` (de-dup the objectives endpoint + the MR compiler)

**Files:**
- Modify: `apps/api/src/easysynq_api/services/objectives/queries.py`
- Modify: `apps/api/src/easysynq_api/services/objectives/__init__.py`
- Modify: `apps/api/src/easysynq_api/api/objectives.py:452-474`
- Modify: `apps/api/src/easysynq_api/services/mgmt_review/compile.py:110-136,165-169,45-51`
- Test: `apps/api/tests/integration/test_quality_objectives.py::test_scorecard_rollup_counts_by_rag` (must stay green) + `apps/api/tests/integration/test_mgmt_review.py::test_compile_inputs_writes_all_twelve_rows` (must stay green)

**The authz invariant (load-bearing — verify in review):** `compute_scorecard` performs **no** authz. The objectives endpoint keeps its `Depends(_objective_read)` PEP gate; the MR compiler keeps its `_owner_holds(...)` owner-DIRECT-PDP gate, both *outside* `compute_scorecard`. The new fn only reads + grades.

- [ ] **Step 1: Run both preservation tests to capture the green baseline**

Run: `cd apps/api && uv run pytest -m integration tests/integration/test_quality_objectives.py::test_scorecard_rollup_counts_by_rag tests/integration/test_mgmt_review.py::test_compile_inputs_writes_all_twelve_rows -v` (needs Docker)
Expected: PASS (both). These are the behaviour-preservation backstops — they must stay PASS after the refactor.

- [ ] **Step 2: Add `compute_scorecard` to `services/objectives/queries.py`**

At the top of `queries.py` add the two domain imports (neither is imported there today):
```python
from ...domain.objectives.commitment import resolve_commitment
from ...domain.objectives.rules import rag_status
```
Append the fn (mirrors `compile.py::_objectives_scorecard:110-136` exactly, plus it returns the graded `rows` so the endpoint serializes from them without a second query):
```python
async def compute_scorecard(
    session: AsyncSession, org_id: uuid.UUID, *, process_id: uuid.UUID | None = None
) -> dict[str, Any]:
    """Grade every objective off its GOVERNING frozen commitment (resolve_commitment) → tally by
    RAG. Returns {total, on_target, by_rag:{green,amber,red,unmeasured}, rows:[ObjectiveRow]}.

    AUTHZ-AGNOSTIC: the caller MUST gate the read (the endpoint via require('objective.read'); the
    MR compiler via _owner_holds). This fn performs NO authz — keep require/enforce/gather_grants
    out of it (the S-mr-2 #1 risk)."""
    rows = await list_objectives(session, org_id, process_id=process_id)
    by_rag: dict[str, int] = {"green": 0, "amber": 0, "red": 0, "unmeasured": 0}
    for qo, _ident, _title, _state, governing in rows:
        commitment = resolve_commitment(
            governing,
            target_value=qo.target_value,
            unit=qo.unit,
            direction=qo.direction,
            due_date=qo.due_date,
            at_risk_threshold=qo.at_risk_threshold,
            baseline_value=qo.baseline_value,
            policy_id=qo.policy_id,
        )
        rag = rag_status(
            current=qo.current_value,
            target=commitment.target_value,
            direction=commitment.direction,
            at_risk_threshold=commitment.at_risk_threshold,
        )
        by_rag[rag] += 1  # rag is always one of the 4 keys (rules.py:36); KeyError loudly if drift
    return {
        "total": sum(by_rag.values()),
        "on_target": by_rag["green"],
        "by_rag": by_rag,
        "rows": rows,
    }
```
(`Any` and `uuid` are already imported in `queries.py`; verify and add if ruff flags.)

- [ ] **Step 3: Export it from `services/objectives/__init__.py`**

Add `compute_scorecard` to the `.queries` import block and to `__all__` (alphabetical — between `create_objective` and `current_effective_policy`):
```python
from .queries import (
    compute_scorecard,
    get_objective,
    list_measurements,
    list_objectives,
    list_plans,
)
```
and in `__all__`, insert `"compute_scorecard",` after `"create_objective",`.

- [ ] **Step 4: Route `scorecard_endpoint` through it (keep the gate + the serialized `objectives` list)**

Replace `api/objectives.py:452-474` body with (the `Depends(_objective_read)` gate stays):
```python
@router.get("/objectives/scorecard")
async def scorecard_endpoint(
    process_id: uuid.UUID | None = None,
    caller: AppUser = Depends(_objective_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    band = await compute_scorecard(session, caller.org_id, process_id=process_id)
    today = _today()
    serialized = [
        _objective(qo, identifier=i, title=t, current_state=s, today=today, governing=g)
        for qo, i, t, s, g in band["rows"]
    ]
    return {
        "total": band["total"],
        "on_target": band["on_target"],
        "by_rag": band["by_rag"],
        "objectives": serialized,
    }
```
Update the import: `from ..services.objectives import ... , compute_scorecard, ...` (it already imports `list_objectives` from `..services.objectives`; add `compute_scorecard`). Verify `list_objectives` is still used elsewhere in `objectives.py` — if not, ruff F401 will flag it; keep it if other endpoints use it (they do).

- [ ] **Step 5: Route the MR compiler through it**

In `compile.py`, the OBJECTIVES_STATUS leg (`:165-169`) — the `_owner_holds` gate STAYS; only the scorecard call changes:
```python
    if input_type is ReviewInputType.OBJECTIVES_STATUS:
        if not await _owner_holds(session, owner, _KEY_OBJECTIVES):
            return _source_ref(available=False, summary=None, reason=_REASON_NO_ACCESS, now=now)
        summary = summarize_scorecard(await compute_scorecard(session, org_id))
        return _source_ref(available=True, summary=summary, reason=None, now=now)
```
Delete the `_objectives_scorecard` fn (`:110-136`). Update imports (`:45-51`): remove the now-unused `from ...domain.objectives.commitment import resolve_commitment` and `from ...domain.objectives.rules import rag_status`; change `from ..objectives import list_objectives` to `from ..objectives import compute_scorecard` (verify `list_objectives` is no longer referenced in `compile.py` — if it is, import both). `ruff check` + `mypy` will catch any leftover.

- [ ] **Step 6: Run the api fast loop (lint/format/mypy/unit) + the two preservation tests**

Run: `cd apps/api && uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: clean (no F401, no unused import).
Run: `cd apps/api && uv run pytest -m integration tests/integration/test_quality_objectives.py::test_scorecard_rollup_counts_by_rag tests/integration/test_mgmt_review.py::test_compile_inputs_writes_all_twelve_rows -v`
Expected: PASS (both — behaviour byte-preserved).

- [ ] **Step 7: Commit**

```bash
git add apps/api/src/easysynq_api/services/objectives apps/api/src/easysynq_api/api/objectives.py apps/api/src/easysynq_api/services/mgmt_review/compile.py
git commit -m "refactor(s-mr-2): extract shared compute_scorecard (objectives endpoint + MR compiler)"
```

---

### Task 2: `GET /management-reviews/next-due` (the Home widget's read)

**Files:**
- Modify: `apps/api/src/easysynq_api/services/mgmt_review/cadence.py`
- Modify: `apps/api/src/easysynq_api/api/mgmt_review.py` (insert between `:259` and `:262`)
- Modify: `packages/contracts/openapi.yaml` (path before line 4943; schema after line 7924)
- Create: `apps/api/tests/unit/test_mgmt_review_routes.py`
- Test: `apps/api/tests/unit/test_mgmt_review_cadence.py` (extend, `mr_review_state` math) + `apps/api/tests/integration/test_mgmt_review.py` (extend, the endpoint)

**Route-ordering invariant:** `/management-reviews/next-due` MUST be declared before `/management-reviews/{review_id}` (the str-convertor shadow — S-pack-2). "next-due" never parses as a UUID, so the reverse order is *safe but wrong* (a 422). The unit test in Step 6 is the regression guard.

- [ ] **Step 1: Write the failing unit test for `mr_review_state`**

Add to `apps/api/tests/unit/test_mgmt_review_cadence.py`:
```python
import datetime

from easysynq_api.services.mgmt_review.cadence import MR_REVIEW_LEAD_DAYS, mr_review_state


def test_mr_review_state_buckets() -> None:
    due = datetime.date(2026, 9, 1)
    assert mr_review_state(None, datetime.date(2026, 6, 1)) is None  # not scheduled
    assert mr_review_state(due, datetime.date(2026, 9, 1)) == "overdue"  # today == due
    assert mr_review_state(due, datetime.date(2026, 9, 2)) == "overdue"  # past due
    lead = due - datetime.timedelta(days=MR_REVIEW_LEAD_DAYS)
    assert mr_review_state(due, lead) == "due_soon"  # exactly on the lead boundary
    assert mr_review_state(due, lead - datetime.timedelta(days=1)) == "current"  # before the lead
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd apps/api && uv run pytest tests/unit/test_mgmt_review_cadence.py -k mr_review_state -v`
Expected: FAIL with `ImportError: cannot import name 'mr_review_state'`.

- [ ] **Step 3: Add `read_cadence`, `MR_REVIEW_LEAD_DAYS`, `mr_review_state` to `cadence.py`; refactor the sweep**

Add a small typed result + the helpers near `next_mr_due` (after `:71`):
```python
import dataclasses

MR_REVIEW_LEAD_DAYS = 30  # the MR-specific due_soon window; org-config is a v1.x deferral.
                          # NOT review.REVIEW_LEAD_DAYS — an annual cadence is independently tuned.


@dataclasses.dataclass(frozen=True)
class CadenceStatus:
    cadence_months: int
    owner_user_id: uuid.UUID | None
    last_review_effective_from: datetime.date | None
    next_review_due: datetime.date | None


async def read_cadence(session: AsyncSession, org_id: uuid.UUID) -> CadenceStatus | None:
    """The one cadence rule shared by the daily sweep AND the GET /management-reviews/next-due read
    (so the widget and the sweep can't desync). None when no system_config row exists for the org
    (seeded at setup → unreachable operationally; the caller degrades, never 500s)."""
    config = (
        await session.execute(select(SystemConfig).where(SystemConfig.org_id == org_id))
    ).scalar_one_or_none()
    if config is None:
        return None
    anchor = await _last_released_effective_from(session, org_id)
    return CadenceStatus(
        cadence_months=config.mgmt_review_cadence_months,
        owner_user_id=config.mgmt_review_owner_user_id,
        last_review_effective_from=anchor,
        next_review_due=next_mr_due(anchor, config.mgmt_review_cadence_months),
    )


def mr_review_state(next_due: datetime.date | None, today: datetime.date) -> str | None:
    """current | due_soon | overdue (None = not scheduled). Mirrors vault.review.review_state with
    an MR-specific lead window (MR_REVIEW_LEAD_DAYS)."""
    if next_due is None:
        return None
    if today >= next_due:
        return "overdue"
    if today >= next_due - datetime.timedelta(days=MR_REVIEW_LEAD_DAYS):
        return "due_soon"
    return "current"
```
Refactor `sweep_mgmt_reviews` (`:121-171`) to call `read_cadence` instead of its inline config/anchor/due reads. Replace the `config = (...) ...` lookup + the later `anchor = await _last_released_effective_from(...)` / `due = next_mr_due(...)` with:
```python
        cad = await read_cadence(session, org_id)
        if cad is None:  # pragma: no cover — system_config is seeded at setup
            logger.error("mgmt_review_sweep: no system_config row — cannot sweep")
            return {**_ZERO_SUMMARY, "skipped_lock_held": 0}

        owner_id = cad.owner_user_id
        if owner_id is None:
            logger.info(
                "mgmt_review_sweep: mgmt_review_owner_user_id is unset — no review minted "
                "(set system_config.mgmt_review_owner_user_id to enable the cadence)"
            )
            return {**_ZERO_SUMMARY, "skipped_lock_held": 0}
```
...keep the definition lookup + `open_review_exists` guard as-is, then use `due = cad.next_review_due` (delete the now-redundant `anchor`/`due` lines). Keep all existing sweep behaviour byte-identical (the cadence integration tests are the backstop).

- [ ] **Step 4: Run the unit test — green**

Run: `cd apps/api && uv run pytest tests/unit/test_mgmt_review_cadence.py -k mr_review_state -v`
Expected: PASS. Then run the existing cadence integration tests to confirm the sweep refactor is behaviour-preserving:
Run: `cd apps/api && uv run pytest -m integration tests/integration/test_mgmt_review_cadence.py -v`
Expected: PASS (all).

- [ ] **Step 5: Add the endpoint (before `/{review_id}`)**

In `api/mgmt_review.py`, add the imports near the top:
```python
from ..services.mgmt_review.cadence import mr_review_state, read_cadence
from ..services.vault.review import today_org
```
Insert this handler **between** `list_reviews_endpoint` (ends `:259`) and `get_review_endpoint` (`:262`):
```python
@router.get("/management-reviews/next-due")
async def next_due_endpoint(
    caller: AppUser = Depends(_mr_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    # The cadence read backing the Home "next review in N days" widget. mgmtReview.read-gated.
    # Declared BEFORE /{review_id} so the literal isn't shadowed by the str-convertor (S-pack-2).
    cad = await read_cadence(session, caller.org_id)
    if cad is None:  # pragma: no cover — system_config is seeded at setup; never 500 a dashboard read
        return {
            "cadence_months": 12,
            "last_review_effective_from": None,
            "next_review_due": None,
            "review_state": None,
            "owner_configured": False,
        }
    return {
        "cadence_months": cad.cadence_months,
        "last_review_effective_from": (
            cad.last_review_effective_from.isoformat()
            if cad.last_review_effective_from
            else None
        ),
        "next_review_due": cad.next_review_due.isoformat() if cad.next_review_due else None,
        "review_state": mr_review_state(cad.next_review_due, today_org()),
        "owner_configured": cad.owner_user_id is not None,
    }
```

- [ ] **Step 6: Write the route-ordering unit test**

Create `apps/api/tests/unit/test_mgmt_review_routes.py` (mirrors `test_packs_no_edit_verbs.py:91-115`):
```python
def test_next_due_resolves_before_review_id() -> None:
    """GET /management-reviews/next-due must resolve to next_due_endpoint, NOT the {review_id}
    str-convertor route (the S-pack-2 shadow guard). 'next-due' never parses as a UUID, so a
    wrong mount order fails 422-shaped, not 404 — assert the app-level resolution order."""
    from starlette.routing import Match

    from easysynq_api.main import create_app

    app = create_app()
    path = "/api/v1/management-reviews/next-due"
    winner = next(
        (
            r
            for r in app.router.routes
            if r.matches({"type": "http", "path": path, "method": "GET"})[0] != Match.NONE
        ),
        None,
    )
    assert winner is not None
    assert winner.endpoint.__name__ == "next_due_endpoint", (
        f"{path} resolves to {winner.endpoint.__name__}, not next_due_endpoint"
    )
```

- [ ] **Step 7: Write the integration test for the endpoint**

Add to `apps/api/tests/integration/test_mgmt_review.py` (a holder of `mgmtReview.read` gets a well-shaped payload; never 500). Mirror the file's `_auth`/`_grant`/`_MR_KEYS` helpers:
```python
async def test_next_due_endpoint_shape(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"mr-nd-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _MR_KEYS)
    r = await app_client.get("/api/v1/management-reviews/next-due", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {
        "cadence_months",
        "last_review_effective_from",
        "next_review_due",
        "review_state",
        "owner_configured",
    }
    assert isinstance(body["cadence_months"], int)
    assert isinstance(body["owner_configured"], bool)
    assert body["review_state"] in (None, "current", "due_soon", "overdue")
```

- [ ] **Step 8: Run the new tests**

Run: `cd apps/api && uv run pytest tests/unit/test_mgmt_review_routes.py -v`
Expected: PASS.
Run: `cd apps/api && uv run pytest -m integration tests/integration/test_mgmt_review.py -k next_due -v`
Expected: PASS.

- [ ] **Step 9: Document the endpoint in the contract**

In `packages/contracts/openapi.yaml`, insert the path at line 4942 (after `listManagementReviews`, before the S-pack-2 NOTE at 4943):
```yaml
  /management-reviews/next-due:
    get:
      tags: [management-reviews]
      operationId: getManagementReviewNextDue
      summary: "The next Management Review cadence status (clause 9.3) — cadence, anchor, next-due date, derived currency. Gated mgmtReview.read. Declared BEFORE /management-reviews/{review_id} (S-pack-2)."
      responses:
        "200":
          description: "The cadence status for the Home next-review widget."
          content:
            application/json:
              schema: { $ref: "#/components/schemas/ManagementReviewNextDue" }
        "403": { $ref: "#/components/responses/ProblemResponse" }
```
Insert the schema after `ManagementReviewDetail` (after line 7924, before `ManagementReviewCreate`):
```yaml
    ManagementReviewNextDue:
      type: object
      additionalProperties: false
      required: [cadence_months, last_review_effective_from, next_review_due, review_state, owner_configured]
      description: >-
        The Management Review cadence status (S-mr-2). Gated mgmtReview.read.
      properties:
        cadence_months: { type: integer }
        last_review_effective_from: { type: [string, "null"], format: date }
        next_review_due: { type: [string, "null"], format: date }
        review_state:
          type: [string, "null"]
          enum: [current, due_soon, overdue, null]
        owner_configured: { type: boolean }
```

- [ ] **Step 10: Lint the contract + the api fast loop**

Run the `/check-contracts` skill (redocly lint on `packages/contracts/openapi.yaml`). Expected: clean.
Run: `cd apps/api && uv run ruff check . && uv run ruff format --check . && uv run mypy src`. Expected: clean.

- [ ] **Step 11: Commit**

```bash
git add apps/api/src/easysynq_api/services/mgmt_review/cadence.py apps/api/src/easysynq_api/api/mgmt_review.py apps/api/tests/unit/test_mgmt_review_routes.py apps/api/tests/unit/test_mgmt_review_cadence.py apps/api/tests/integration/test_mgmt_review.py packages/contracts/openapi.yaml
git commit -m "feat(s-mr-2): GET /management-reviews/next-due (shared read_cadence + mr_review_state)"
```

---

### Task 3: MR_INPUT auto-resolve at submit

**Files:**
- Modify: `apps/api/src/easysynq_api/services/mgmt_review/service.py` (the seam is **between** `:219` `audit_transition(...)` and `:220` `await session.commit()` in `submit_review_for_review`)
- Test: `apps/api/tests/integration/test_mgmt_review.py` (extend)

**Invariant:** the flip targets ONLY the `MGMT_REVIEW` container instance's PENDING `MR_INPUT` tasks (found via `find_nonterminal_instance(..., MGMT_REVIEW, mr.id, ...)`), **not** the DOCUMENT approval instance `instantiate_approval` creates at `:218`. It is a plain `task.state = DONE` mutation (task state is not WORM) — **no** `TaskOutcome`, **no** signature (R43). A review created via `POST /management-reviews` (all existing submit tests) has no MR_INPUT → the flip must no-op gracefully.

- [ ] **Step 1: Write the failing integration test**

Add to `apps/api/tests/integration/test_mgmt_review.py`. It mints an MR_INPUT-bearing review via the cadence sweep (the only minter), then submits it and asserts the MR_INPUT flipped PENDING→DONE. Reuse the cadence-test helpers (`_set_owner`, `_run_sweep`, `_open_mr_doc_count`) — import them from `test_mgmt_review_cadence` or copy:
```python
async def test_submit_resolves_pending_mr_input_task(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A cadence-minted MR carries a PENDING MR_INPUT prepare-task. Submitting the review flips it
    to DONE in the submit txn (so it stops lingering in My-Tasks)."""
    from .test_mgmt_review_cadence import _open_mr_doc_count, _run_sweep, _set_owner

    owner_subject = f"mr-air-{uuid.uuid4()}"
    owner_id = await _grant(owner_subject, _MR_KEYS)  # the owner needs the submit keys here
    org_id = await _set_owner(owner_id)
    if await _open_mr_doc_count(org_id) > 0:
        pytest.skip("an MR is already open in the shared DB — covered elsewhere")

    summary = await _run_sweep()
    assert summary["mgmt_reviews_opened"] == 1, summary

    h = _auth(token_factory, owner_subject)
    async with get_sessionmaker()() as s:
        mr_doc = (
            await s.execute(
                select(DocumentedInformation)
                .join(ManagementReview, ManagementReview.id == DocumentedInformation.id)
                .where(ManagementReview.org_id == org_id)
                .order_by(DocumentedInformation.created_at.desc())
                .limit(1)
            )
        ).scalar_one()
        rid = str(mr_doc.id)
        instance = (
            await s.execute(
                select(WorkflowInstance).where(
                    WorkflowInstance.subject_type == WorkflowSubjectType.MGMT_REVIEW,
                    WorkflowInstance.subject_id == mr_doc.id,
                )
            )
        ).scalar_one()
        pre = (
            await s.execute(select(Task).where(Task.instance_id == instance.id))
        ).scalars().all()
        assert all(t.state is TaskState.PENDING for t in pre if t.type is TaskType.MR_INPUT)

    r = await app_client.post(f"/api/v1/management-reviews/{rid}/submit-review", headers=h)
    assert r.status_code == 200, r.text

    async with get_sessionmaker()() as s:
        post = (
            await s.execute(
                select(Task).where(
                    Task.instance_id == instance.id, Task.type == TaskType.MR_INPUT
                )
            )
        ).scalars().all()
        assert post and all(t.state is TaskState.DONE for t in post)
```
Ensure the imports `TaskState`, `TaskType`, `WorkflowInstance`, `WorkflowSubjectType`, `Task` are present at the top of the test file (some may already be there).

- [ ] **Step 2: Run it to verify it fails**

Run: `cd apps/api && uv run pytest -m integration tests/integration/test_mgmt_review.py -k resolves_pending_mr_input -v`
Expected: FAIL — the final assertion fails (`MR_INPUT` is still PENDING after submit).

- [ ] **Step 3: Add the resolver + wire it before the commit**

In `service.py`, add the imports (they exist in `cadence.py`, not yet in `service.py`):
```python
from ...db.models._workflow_enums import TaskState, TaskType, WorkflowSubjectType
from ..workflow import repository as wf_repo
```
Add the helper near the other module-private fns:
```python
async def _resolve_prepare_tasks(
    session: AsyncSession, org_id: uuid.UUID, review_id: uuid.UUID
) -> None:
    """Flip the cadence-minted MR_INPUT prepare-task(s) PENDING→DONE at submit (the prep is done
    once the minutes freeze). Targets the MGMT_REVIEW container instance (NOT the DOCUMENT approval
    instance). A manually-created MR has no container → no-op. No TaskOutcome, no signature (R43)."""
    instance = await wf_repo.find_nonterminal_instance(
        session, org_id, WorkflowSubjectType.MGMT_REVIEW, review_id, terminal_states=()
    )
    if instance is None:
        return
    for task in await wf_repo.list_instance_tasks(session, instance.id):
        if task.type is TaskType.MR_INPUT and task.state is TaskState.PENDING:
            task.state = TaskState.DONE
```
In `submit_review_for_review`, insert the call between `audit_transition(...)` (`:219`) and `await session.commit()` (`:220`):
```python
    audit_transition(session, vault_sink, result, actor)
    await _resolve_prepare_tasks(session, actor.org_id, mr.id)
    await session.commit()
    return result.doc
```

- [ ] **Step 4: Run the new test — green; and the existing submit tests — still green**

Run: `cd apps/api && uv run pytest -m integration tests/integration/test_mgmt_review.py -k "resolves_pending_mr_input or submit" -v`
Expected: PASS (the new test + `test_submit_freezes_minutes_and_enters_review` + `test_submit_twice_is_a_conflict` — the latter two have no MR_INPUT, proving the graceful no-op).

- [ ] **Step 5: api fast loop**

Run: `cd apps/api && uv run ruff check . && uv run ruff format --check . && uv run mypy src`. Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add apps/api/src/easysynq_api/services/mgmt_review/service.py apps/api/tests/integration/test_mgmt_review.py
git commit -m "feat(s-mr-2): auto-resolve the MR_INPUT prepare-task at submit"
```

---

## Phase B — FE foundation

### Task 4: MR types in `lib/types.ts`

**Files:** Modify `apps/web/src/lib/types.ts`. No test (types only — `tsc` is the gate).

- [ ] **Step 1: Add the MR types**

Append (pinned to `api/mgmt_review.py` serializers; reuse the existing `DocumentCurrentState`):
```typescript
// ---- S-mr-2 Management Reviews (clause 9.3) — pinned to api/mgmt_review.py serializers ----
export type MgmtReviewCloseState = "ActionsTracked" | "Closed";
export type ReviewInputType =
  | "PRIOR_ACTIONS" | "CONTEXT_CHANGES" | "CUSTOMER_SATISFACTION" | "OBJECTIVES_STATUS"
  | "PROCESS_PERFORMANCE" | "NONCONFORMITIES_CAPA" | "MONITORING_RESULTS" | "AUDIT_RESULTS"
  | "SUPPLIER_PERFORMANCE" | "RESOURCE_ADEQUACY" | "RISK_OPPORTUNITY_ACTIONS" | "IMPROVEMENT_OPPORTUNITIES";
export type ReviewOutputType = "DECISION" | "ACTION" | "IMPROVEMENT";
export type MgmtReviewState = "current" | "due_soon" | "overdue";

export interface AttendeeRow { name: string; role?: string; user_id?: string; }

// source_ref is free-form per input_type: an available row carries `summary`, a gap row `reason`.
export interface ReviewInputSourceRef {
  available: boolean;
  generated_at: string;
  summary?: Record<string, unknown>;
  reason?: string;
}
export interface ReviewInput {
  id: string;
  management_review_id: string;
  input_type: ReviewInputType;
  available: boolean;
  source_ref: ReviewInputSourceRef;
  position: number;
}
export interface ReviewOutput {
  id: string;
  management_review_id: string;
  output_type: ReviewOutputType;
  description: string;
  owner_user_id: string | null;
  due_date: string | null;
  spawned_task_id: string | null;
}
export interface MgmtReview {
  id: string;
  identifier: string;
  title: string;
  current_state: DocumentCurrentState;
  period_label: string | null;
  review_date: string | null;
  attendees: AttendeeRow[] | null;
  close_state: MgmtReviewCloseState | null;
  closed_at: string | null;
  created_at: string;
}
export interface MgmtReviewDetail extends MgmtReview {
  inputs: ReviewInput[];
  outputs: ReviewOutput[];
}
export interface MgmtReviewListResponse { data: MgmtReview[]; }
export interface MgmtReviewNextDue {
  cadence_months: number;
  last_review_effective_from: string | null;
  next_review_due: string | null;
  review_state: MgmtReviewState | null;
  owner_configured: boolean;
}
export interface MgmtReviewCreateBody { title: string; period_label?: string; review_date?: string; }
export interface MgmtReviewMetaBody {
  period_label?: string | null;
  review_date?: string | null;
  attendees?: AttendeeRow[] | null;
}
export interface ReviewOutputCreateBody {
  output_type: ReviewOutputType;
  description: string;
  owner_user_id?: string | null;
  due_date?: string | null;
}
export interface ReviewOutputUpdateBody {
  output_type?: ReviewOutputType;
  description?: string;
  owner_user_id?: string | null;
  due_date?: string | null;
}
```
Confirm `DocumentCurrentState` already exists in `types.ts` (it is the `ApprovalStepper` `docState` type) — if its literal name differs, use the existing one.

- [ ] **Step 2: Typecheck**

Run: `cd apps/web && npx tsc --noEmit`. Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/lib/types.ts
git commit -m "feat(s-mr-2): MR types pinned to the api/mgmt_review.py serializers"
```

---

### Task 5: MSW fixtures + handlers

**Files:** Modify `apps/web/src/test/msw/handlers.ts` (the `handlers` array + the line-2 type import). No standalone test — consumed by Tasks 6-12.

**Pin to the as-built serializers + the real `source_ref` summary shapes** (Task D extraction): OBJECTIVES_STATUS `{total,on_target,by_rag:{green,amber,red,unmeasured}}`; AUDIT_RESULTS `{total,open,closed}`; NONCONFORMITIES_CAPA `{open_ncrs,open_capas,complaints,by_close_state:{...}}`; MONITORING_RESULTS `{readings,objectives_measured}`; PROCESS_PERFORMANCE `{star_coverage:{total,covered,partial,gap},overdue_reviews,integrity:{blobs,failing,superseded_copies}|null}`. Gap `reason` strings (verbatim): `"not available (insufficient access)"`, `"not available (no structured source)"`, `"not available (no prior released review)"`.

- [ ] **Step 1: Extend the type import (line 2) and add fixtures + handlers**

Add the MR types to the `import type { ... } from "../../lib/types"` line: `MgmtReview, MgmtReviewDetail, MgmtReviewListResponse, MgmtReviewNextDue, ReviewInput, ReviewOutput`.
Add fixtures near the other family fixtures:
```typescript
const mgmtReviewListFixture = {
  data: [
    {
      id: "mr-0001-0001-0001-000000000001",
      identifier: "MR-001",
      title: "2026 Annual Management Review",
      current_state: "Draft",
      period_label: "2026 Annual",
      review_date: "2026-06-12",
      attendees: [{ name: "Mara", role: "QM" }],
      close_state: null,
      closed_at: null,
      created_at: "2026-06-01T09:00:00+00:00",
    } satisfies MgmtReview,
  ],
} satisfies MgmtReviewListResponse;

const mgmtReviewDetailFixture = {
  ...mgmtReviewListFixture.data[0],
  inputs: [
    {
      id: "ri-3", management_review_id: "mr-0001-0001-0001-000000000001",
      input_type: "OBJECTIVES_STATUS", available: true, position: 3,
      source_ref: { available: true, generated_at: "2026-06-01T09:00:00+00:00",
        summary: { total: 5, on_target: 3, by_rag: { green: 3, amber: 1, red: 1, unmeasured: 0 } } },
    },
    {
      id: "ri-7", management_review_id: "mr-0001-0001-0001-000000000001",
      input_type: "AUDIT_RESULTS", available: true, position: 7,
      source_ref: { available: true, generated_at: "2026-06-01T09:00:00+00:00",
        summary: { total: 4, open: 1, closed: 3 } },
    },
    {
      id: "ri-0", management_review_id: "mr-0001-0001-0001-000000000001",
      input_type: "PRIOR_ACTIONS", available: false, position: 0,
      source_ref: { available: false, generated_at: "2026-06-01T09:00:00+00:00",
        reason: "not available (no prior released review)" },
    },
    {
      id: "ri-1", management_review_id: "mr-0001-0001-0001-000000000001",
      input_type: "CONTEXT_CHANGES", available: false, position: 1,
      source_ref: { available: false, generated_at: "2026-06-01T09:00:00+00:00",
        reason: "not available (no structured source)" },
    },
  ] satisfies ReviewInput[],
  outputs: [
    {
      id: "ro-1", management_review_id: "mr-0001-0001-0001-000000000001",
      output_type: "DECISION", description: "Approve the objectives for 2026",
      owner_user_id: null, due_date: null, spawned_task_id: null,
    },
    {
      id: "ro-2", management_review_id: "mr-0001-0001-0001-000000000001",
      output_type: "ACTION", description: "Refresh the supplier evaluation register",
      owner_user_id: "user-mara", due_date: "2026-09-01", spawned_task_id: null,
    },
  ] satisfies ReviewOutput[],
} satisfies MgmtReviewDetail;

const mgmtReviewApprovalFixture = null; // pre-submit; per-test override injects an instance

const mgmtReviewNextDueFixture = {
  cadence_months: 12,
  last_review_effective_from: "2025-06-01",
  next_review_due: "2026-06-01",
  review_state: "due_soon",
  owner_configured: true,
} satisfies MgmtReviewNextDue;
```
Add handlers inside the `handlers` array under a `// ---- S-mr-2 management reviews ----` banner. **Order: the literal `next-due` BEFORE `:id`:**
```typescript
  http.get("/api/v1/management-reviews/next-due", () => HttpResponse.json(mgmtReviewNextDueFixture)),
  http.get("/api/v1/management-reviews", () => HttpResponse.json(mgmtReviewListFixture)),
  http.get("/api/v1/management-reviews/:id", ({ params }) =>
    params.id === mgmtReviewDetailFixture.id
      ? HttpResponse.json(mgmtReviewDetailFixture)
      : HttpResponse.json({ code: "not_found", title: "Management Review not found" }, { status: 404 }),
  ),
  http.get("/api/v1/management-reviews/:id/approval", () => HttpResponse.json(mgmtReviewApprovalFixture)),
  http.post("/api/v1/management-reviews", () => HttpResponse.json(mgmtReviewListFixture.data[0], { status: 201 })),
  http.post("/api/v1/management-reviews/:id/compile-inputs", () => HttpResponse.json(mgmtReviewDetailFixture)),
  http.post("/api/v1/management-reviews/:id/outputs", () => HttpResponse.json(mgmtReviewDetailFixture.outputs[1], { status: 201 })),
  http.patch("/api/v1/management-reviews/:id/outputs/:oid", () => HttpResponse.json(mgmtReviewDetailFixture.outputs[1])),
  http.delete("/api/v1/management-reviews/:id/outputs/:oid", () => new HttpResponse(null, { status: 204 })),
  http.patch("/api/v1/management-reviews/:id", () => HttpResponse.json(mgmtReviewDetailFixture)),
  http.post("/api/v1/management-reviews/:id/submit-review", () =>
    HttpResponse.json({ ...mgmtReviewListFixture.data[0], current_state: "InReview" })),
  http.post("/api/v1/management-reviews/:id/release", () =>
    HttpResponse.json({ ...mgmtReviewListFixture.data[0], current_state: "Effective", close_state: "ActionsTracked" })),
  http.post("/api/v1/management-reviews/:id/close", () =>
    HttpResponse.json({ ...mgmtReviewListFixture.data[0], current_state: "Effective", close_state: "Closed", closed_at: "2026-09-02T09:00:00+00:00" })),
```
Also add MR_INPUT/MR_ACTION rows to the existing `my-tasks` fixture if the Home/rail tests assert them.

- [ ] **Step 2: Typecheck (the `satisfies` shapes)**

Run: `cd apps/web && npx tsc --noEmit`. Expected: clean (any wrong field name fails here).

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/test/msw/handlers.ts
git commit -m "test(s-mr-2): MSW fixtures + handlers pinned to the MR serializers"
```

---

### Task 6: `hooks.ts` + `mutations.ts`

**Files:** Create `apps/web/src/features/management-review/hooks.ts`, `mutations.ts`, and `hooks.test.tsx`.

- [ ] **Step 1: Write the failing hook test**

Create `apps/web/src/features/management-review/hooks.test.tsx` (mirror an objectives hook test: `renderHook` with a `QueryClientProvider(retry:false)` + `AuthContext` wrapper). Assert `useMgmtReviews` returns the list `data` and `useMgmtReview(id)` the detail with `inputs`/`outputs`, and that a 403 sets `forbidden`. Import `{ expect, it, describe } from "vitest"`.

- [ ] **Step 2: Run it — fails (module missing)**

Run: `cd apps/web && npx vitest run src/features/management-review/hooks.test.tsx`
Expected: FAIL (cannot resolve `./hooks`).

- [ ] **Step 3: Write `hooks.ts`** (mirrors `features/objectives/hooks.ts`):
```typescript
import { useQuery } from "@tanstack/react-query";
import { ApiError, useApi } from "../../lib/api";
import type {
  MgmtReviewDetail, MgmtReviewListResponse, MgmtReviewNextDue, WorkflowInstance,
} from "../../lib/types";

function forbiddenOf(error: unknown): boolean {
  return error instanceof ApiError && error.status === 403;
}

export function useMgmtReviews() {
  const api = useApi();
  const query = useQuery({
    queryKey: ["management-reviews"],
    queryFn: () => api.get<MgmtReviewListResponse>("/api/v1/management-reviews"),
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}

export function useMgmtReview(id: string | null) {
  const api = useApi();
  const query = useQuery({
    queryKey: ["management-review", id],
    queryFn: () => api.get<MgmtReviewDetail>(`/api/v1/management-reviews/${id!}`),
    enabled: id !== null,
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}

export function useMgmtReviewApproval(id: string | null) {
  const api = useApi();
  const query = useQuery({
    queryKey: ["management-review-approval", id],
    queryFn: () => api.get<WorkflowInstance | null>(`/api/v1/management-reviews/${id!}/approval`),
    enabled: id !== null,
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}

export function useMgmtReviewNextDue() {
  const api = useApi();
  const query = useQuery({
    queryKey: ["management-review-next-due"],
    queryFn: () => api.get<MgmtReviewNextDue>("/api/v1/management-reviews/next-due"),
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}
```

- [ ] **Step 4: Write `mutations.ts`** (mirrors `features/objectives/mutations.ts`; the shared invalidator also hits `["my-tasks"]` so the Home rail refreshes):
```typescript
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type {
  MgmtReview, MgmtReviewCreateBody, MgmtReviewDetail, MgmtReviewMetaBody,
  ReviewOutput, ReviewOutputCreateBody, ReviewOutputUpdateBody,
} from "../../lib/types";

function useInvalidateReview(): (id: string) => void {
  const qc = useQueryClient();
  return (id: string) => {
    void qc.invalidateQueries({ queryKey: ["management-review", id] });
    void qc.invalidateQueries({ queryKey: ["management-review-approval", id] });
    void qc.invalidateQueries({ queryKey: ["management-reviews"] });
    void qc.invalidateQueries({ queryKey: ["my-tasks"] });
  };
}

export function useCreateReview() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: MgmtReviewCreateBody) =>
      api.send<MgmtReview>("POST", "/api/v1/management-reviews", body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["management-reviews"] }),
  });
}

export function useCompileInputs() {
  const api = useApi();
  const invalidate = useInvalidateReview();
  return useMutation({
    mutationFn: (id: string) =>
      api.send<MgmtReviewDetail>("POST", `/api/v1/management-reviews/${id}/compile-inputs`, {}),
    onSuccess: (_d, id) => invalidate(id),
  });
}

export function useAddOutput() {
  const api = useApi();
  const invalidate = useInvalidateReview();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: ReviewOutputCreateBody }) =>
      api.send<ReviewOutput>("POST", `/api/v1/management-reviews/${id}/outputs`, body),
    onSuccess: (_d, { id }) => invalidate(id),
  });
}

export function usePatchOutput() {
  const api = useApi();
  const invalidate = useInvalidateReview();
  return useMutation({
    mutationFn: ({ id, oid, body }: { id: string; oid: string; body: ReviewOutputUpdateBody }) =>
      api.send<ReviewOutput>("PATCH", `/api/v1/management-reviews/${id}/outputs/${oid}`, body),
    onSuccess: (_d, { id }) => invalidate(id),
  });
}

export function useDeleteOutput() {
  const api = useApi();
  const invalidate = useInvalidateReview();
  return useMutation({
    mutationFn: ({ id, oid }: { id: string; oid: string }) =>
      api.send<void>("DELETE", `/api/v1/management-reviews/${id}/outputs/${oid}`),
    onSuccess: (_d, { id }) => invalidate(id),
  });
}

export function usePatchMeta() {
  const api = useApi();
  const invalidate = useInvalidateReview();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: MgmtReviewMetaBody }) =>
      api.send<MgmtReview>("PATCH", `/api/v1/management-reviews/${id}`, body),
    onSuccess: (_d, { id }) => invalidate(id),
  });
}

export function useSubmitReview() {
  const api = useApi();
  const invalidate = useInvalidateReview();
  return useMutation({
    mutationFn: (id: string) =>
      api.send<MgmtReview>("POST", `/api/v1/management-reviews/${id}/submit-review`, {}),
    onSuccess: (_d, id) => invalidate(id),
  });
}

export function useReleaseReview() {
  const api = useApi();
  const invalidate = useInvalidateReview();
  return useMutation({
    mutationFn: (id: string) =>
      api.send<MgmtReview>("POST", `/api/v1/management-reviews/${id}/release`, {}),
    onSuccess: (_d, id) => invalidate(id),
  });
}

export function useCloseReview() {
  const api = useApi();
  const invalidate = useInvalidateReview();
  return useMutation({
    mutationFn: (id: string) =>
      api.send<MgmtReview>("POST", `/api/v1/management-reviews/${id}/close`, {}),
    onSuccess: (_d, id) => invalidate(id),
  });
}
```

- [ ] **Step 4b: Run the hook test — green**

Run: `cd apps/web && npx vitest run src/features/management-review/hooks.test.tsx`. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/management-review/hooks.ts apps/web/src/features/management-review/mutations.ts apps/web/src/features/management-review/hooks.test.tsx
git commit -m "feat(s-mr-2): management-review FE hooks + mutations"
```

---

### Task 7: register page + nav entry + routes

**Files:** Create `ManagementReviewsRegisterPage.tsx` + `.test.tsx`; modify `LeftRail.tsx`, `App.tsx`.

- [ ] **Step 1: Write the failing register test**

`ManagementReviewsRegisterPage.test.tsx`: render with the default MSW; `waitFor` the first row (`MR-001`); assert a `New management review` button shows when `mgmtReview.create` is granted (override `me-permissions`) and not otherwise; assert the gray "No access" panel on a 403 (`server.use(http.get("/api/v1/management-reviews", () => HttpResponse.json({}, {status:403})))`). `import { expect, it, describe } from "vitest"`. The **first content assertion must `waitFor`** (the card renders before the row resolves).

- [ ] **Step 2: Run — fails**

Run: `cd apps/web && npx vitest run src/features/management-review/ManagementReviewsRegisterPage.test.tsx`. Expected: FAIL (module missing).

- [ ] **Step 3: Write `ManagementReviewsRegisterPage.tsx`** — mirror `features/objectives/ObjectivesRegisterPage.tsx` (the forbidden/error/loading ladder + the `{createOpen && <Modal/>}` + the Table). Adaptation: list rows are `data.data` (not `data.objectives`); columns are Ref (Anchor→`/management-reviews/:id` + `<StateBadge>` for non-Effective) · Title · Period · Review date · Close state; the create button gates `can("mgmtReview.create")`; the empty-state branches on the same. Full component:
```typescript
import { Alert, Anchor, Badge, Button, Container, Group, Loader, Table, Text, Title } from "@mantine/core";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { usePermissions } from "../../app/shell/usePermissions";
import { StateBadge } from "../document/StateBadge";
import { useMgmtReviews } from "./hooks";
import { NewManagementReviewModal } from "./NewManagementReviewModal";

export function ManagementReviewsRegisterPage() {
  const { data, isLoading, isError, forbidden } = useMgmtReviews();
  const { can } = usePermissions();
  const navigate = useNavigate();
  const [createOpen, setCreateOpen] = useState(false);

  if (forbidden) {
    return (
      <Container size="lg" py="md">
        <Title order={2} mb="md">Management reviews</Title>
        <Alert color="gray" title="No access">
          You don't have access to Management Reviews. It's available to the Quality Manager.
        </Alert>
      </Container>
    );
  }
  if (isError) {
    return (
      <Container size="lg" py="md">
        <Title order={2} mb="md">Management reviews</Title>
        <Alert color="red" title="Couldn't load management reviews">Please try again.</Alert>
      </Container>
    );
  }
  if (isLoading || !data) {
    return <Container size="lg" py="md"><Loader /></Container>;
  }
  return (
    <Container size="lg" py="md">
      <Group justify="space-between" mb="md">
        <Title order={2}>Management reviews</Title>
        {can("mgmtReview.create") && (
          <Button onClick={() => setCreateOpen(true)}>New management review</Button>
        )}
      </Group>
      {data.data.length === 0 ? (
        <Alert color="gray" title="No management reviews yet" mt="md">
          {can("mgmtReview.create")
            ? "Convene the first management review to record clause 9.3 minutes."
            : "No management reviews have been convened yet."}
        </Alert>
      ) : (
        <Table striped highlightOnHover mt="md">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Ref</Table.Th><Table.Th>Review</Table.Th><Table.Th>Period</Table.Th>
              <Table.Th>Review date</Table.Th><Table.Th>Status</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {data.data.map((mr) => (
              <Table.Tr key={mr.id}>
                <Table.Td>
                  <Group gap="xs" wrap="nowrap">
                    <Anchor component={Link} to={`/management-reviews/${mr.id}`}>{mr.identifier}</Anchor>
                    {mr.current_state !== "Effective" && <StateBadge state={mr.current_state} size="xs" />}
                  </Group>
                </Table.Td>
                <Table.Td><Text lineClamp={1}>{mr.title}</Text></Table.Td>
                <Table.Td>{mr.period_label ?? "—"}</Table.Td>
                <Table.Td>{mr.review_date ?? "—"}</Table.Td>
                <Table.Td>
                  {mr.close_state ? (
                    <Badge variant="light" color={mr.close_state === "Closed" ? "gray" : "blue"}>
                      {mr.close_state === "Closed" ? "Closed" : "Actions tracked"}
                    </Badge>
                  ) : "—"}
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
      {createOpen && (
        <NewManagementReviewModal
          opened
          onClose={() => setCreateOpen(false)}
          onCreated={(id) => { setCreateOpen(false); navigate(`/management-reviews/${id}`); }}
        />
      )}
    </Container>
  );
}
```
(`NewManagementReviewModal` lands in Task 10; create a minimal stub now or sequence Task 10's modal before this test — the subagent may stub it to make the test compile, then fill it in Task 10.)

- [ ] **Step 4: Wire the nav entry** — in `LeftRail.tsx`, after the objectives block (`:77`) and before `{PHASES.map(...)}` (`:78`):
```tsx
      {can("mgmtReview.read") && (
        // S-mr-2: gated — mgmtReview.read (SYSTEM finest-scope); the CHECK-phase clause-9.3 register.
        <NavLink
          component={Link}
          to="/management-reviews"
          label="Management reviews"
          active={pathname.startsWith("/management-reviews")}
        />
      )}
```

- [ ] **Step 5: Wire the routes** — in `App.tsx`, import the pages alongside the objectives imports and add the route pair after the objectives routes (`:142-143`):
```tsx
        <Route path="management-reviews" element={<ManagementReviewsRegisterPage />} />
        <Route path="management-reviews/:id" element={<ManagementReviewDetailPage />} />
```

- [ ] **Step 6: Run the register test — green; typecheck**

Run: `cd apps/web && npx vitest run src/features/management-review/ManagementReviewsRegisterPage.test.tsx && npx tsc --noEmit`. Expected: PASS + clean.

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/features/management-review/ManagementReviewsRegisterPage.tsx apps/web/src/features/management-review/ManagementReviewsRegisterPage.test.tsx apps/web/src/app/shell/LeftRail.tsx apps/web/src/App.tsx
git commit -m "feat(s-mr-2): management-reviews register page + nav + routes"
```

---

## Phase C — FE detail / lifecycle

### Task 8: `labels.ts` + `ReviewInputsSection` (the 9.3.2 input tables)

**Files:** Create `labels.ts`, `ReviewInputsSection.tsx`, `ReviewInputsSection.test.tsx`.

**The generic source_ref renderer (F3, N6/N9):** per `review_input` ordered by `position`. A live row (`available && source_ref.summary`) renders its summary as a calm key/value table; OBJECTIVES_STATUS additionally shows a RAG band (the only input with backend RAG — every other summary is plain counts, the FE adds no fabricated RAG). A gap row renders a calm "Not available — `{reason}`" line. Render all values as React text nodes — no `dangerouslySetInnerHTML`.

- [ ] **Step 1: Write the failing test**

`ReviewInputsSection.test.tsx`: pass the `mgmtReviewDetailFixture.inputs`; assert the OBJECTIVES_STATUS card shows "3 / 5 on target" and a green/amber/red RAG chip set; the AUDIT_RESULTS card shows open/closed counts; the PRIOR_ACTIONS card shows "Not available" + the reason; `import { expect, it } from "vitest"`.

- [ ] **Step 2: Run — fails.** Run: `cd apps/web && npx vitest run src/features/management-review/ReviewInputsSection.test.tsx`. Expected: FAIL.

- [ ] **Step 3: Write `labels.ts`**:
```typescript
import type { ReviewInputType, ReviewOutputType } from "../../lib/types";

export const INPUT_LABEL: Record<ReviewInputType, string> = {
  PRIOR_ACTIONS: "Status of actions from prior reviews",
  CONTEXT_CHANGES: "Changes in context & interested parties",
  CUSTOMER_SATISFACTION: "Customer satisfaction & feedback",
  OBJECTIVES_STATUS: "Quality objectives status",
  PROCESS_PERFORMANCE: "Process performance & conformity",
  NONCONFORMITIES_CAPA: "Nonconformities & corrective actions",
  MONITORING_RESULTS: "Monitoring & measurement results",
  AUDIT_RESULTS: "Audit results",
  SUPPLIER_PERFORMANCE: "External provider performance",
  RESOURCE_ADEQUACY: "Adequacy of resources",
  RISK_OPPORTUNITY_ACTIONS: "Effectiveness of actions on risks & opportunities",
  IMPROVEMENT_OPPORTUNITIES: "Opportunities for improvement",
};

export const OUTPUT_LABEL: Record<ReviewOutputType, string> = {
  DECISION: "Decision", ACTION: "Action", IMPROVEMENT: "Improvement opportunity",
};
```

- [ ] **Step 4: Write `ReviewInputsSection.tsx`** — a per-input card list. The summary renderer is generic with an OBJECTIVES_STATUS special case for the RAG band:
```typescript
import { Alert, Badge, Card, Group, Stack, Table, Text, Title } from "@mantine/core";
import type { ReviewInput } from "../../lib/types";
import { INPUT_LABEL } from "./labels";

const RAG_COLOR: Record<string, string> = { green: "green", amber: "yellow", red: "red", unmeasured: "gray" };

function ObjectivesBand({ summary }: { summary: Record<string, unknown> }) {
  const byRag = (summary.by_rag ?? {}) as Record<string, number>;
  return (
    <Stack gap="xs">
      <Text size="sm">
        <Text span fw={600}>{String(summary.on_target ?? 0)}</Text> / {String(summary.total ?? 0)} objectives on target
      </Text>
      <Group gap="xs">
        {(["green", "amber", "red", "unmeasured"] as const).map((k) => (
          <Badge key={k} variant="light" color={RAG_COLOR[k]}>{`${byRag[k] ?? 0} ${k}`}</Badge>
        ))}
      </Group>
    </Stack>
  );
}

function SummaryTable({ summary }: { summary: Record<string, unknown> }) {
  // Generic calm key/value table for plain-count summaries (audits, ncrs/capas, kpis, process perf).
  const entries = Object.entries(summary).filter(([, v]) => typeof v === "number" || typeof v === "string");
  const nested = Object.entries(summary).filter(([, v]) => v !== null && typeof v === "object");
  return (
    <Stack gap="xs">
      <Table withRowBorders={false}>
        <Table.Tbody>
          {entries.map(([k, v]) => (
            <Table.Tr key={k}>
              <Table.Td><Text size="sm" c="dimmed">{k.replace(/_/g, " ")}</Text></Table.Td>
              <Table.Td><Text size="sm" fw={500}>{String(v)}</Text></Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      {nested.map(([k, v]) => (
        <Group key={k} gap="xs">
          <Text size="xs" c="dimmed">{k.replace(/_/g, " ")}:</Text>
          {Object.entries(v as Record<string, unknown>).map(([nk, nv]) => (
            <Badge key={nk} variant="light" color="gray">{`${nk.replace(/_/g, " ")} ${String(nv)}`}</Badge>
          ))}
        </Group>
      ))}
    </Stack>
  );
}

function InputCard({ input }: { input: ReviewInput }) {
  const ref = input.source_ref;
  return (
    <Card withBorder>
      <Stack gap="xs">
        <Text fw={600}>{INPUT_LABEL[input.input_type]}</Text>
        {input.available && ref.summary ? (
          input.input_type === "OBJECTIVES_STATUS" ? (
            <ObjectivesBand summary={ref.summary} />
          ) : (
            <SummaryTable summary={ref.summary} />
          )
        ) : (
          <Alert color="gray" variant="light">
            Not available{ref.reason ? ` — ${ref.reason}` : ""}
          </Alert>
        )}
      </Stack>
    </Card>
  );
}

export function ReviewInputsSection({ inputs }: { inputs: ReviewInput[] }) {
  const ordered = [...inputs].sort((a, b) => a.position - b.position);
  return (
    <Stack gap="sm">
      <Title order={4}>Review inputs (9.3.2)</Title>
      {ordered.map((i) => <InputCard key={i.id} input={i} />)}
    </Stack>
  );
}
```

- [ ] **Step 5: Run the test — green.** Run: `cd apps/web && npx vitest run src/features/management-review/ReviewInputsSection.test.tsx`. Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/features/management-review/labels.ts apps/web/src/features/management-review/ReviewInputsSection.tsx apps/web/src/features/management-review/ReviewInputsSection.test.tsx
git commit -m "feat(s-mr-2): 9.3.2 review-inputs section (calm RAG tables, N6/N9)"
```

---

### Task 9: `ReviewOutputsSection` + `AddOutputModal` (the 9.3.3 outputs + editor)

**Files:** Create `ReviewOutputsSection.tsx`, `AddOutputModal.tsx`, `ReviewOutputsSection.test.tsx`.

Outputs grouped DECISION / ACTION / IMPROVEMENT. An ACTION shows owner (`useUserDirectory` → name) + due + a best-effort spawned-task state via `useTask` (`features/review/hooks`). Draft-only editing: add (`AddOutputModal`), delete (gated `mgmtReview.record_outputs`). An ACTION requires `owner_user_id` (enforce in the modal).

- [ ] **Step 1: Write the failing test** — `ReviewOutputsSection.test.tsx`: assert the DECISION + ACTION outputs render under their group headings, the ACTION shows the owner name + due; when `editable` is true and `mgmtReview.record_outputs` is granted, an "Add output" button shows. `import { expect, it } from "vitest"`.

- [ ] **Step 2: Run — fails.**

- [ ] **Step 3: Write `AddOutputModal.tsx`** — `{open && <Modal/>}` conditional-rendered by the parent. Fields: a `SegmentedControl` for `output_type` (via `Input.Wrapper` for a11y), a `Textarea` for `description`, and — when `output_type === "ACTION"` — a `Select` owner picker (from `useUserDirectory`) + a `date` input for `due_date`. `canSave = description.trim() && (output_type !== "ACTION" || owner_user_id)`. `useAddOutput().mutateAsync({id, body})` in try/catch → `onClose`.

- [ ] **Step 4: Write `ReviewOutputsSection.tsx`**:
```typescript
import { Badge, Button, Card, Group, Stack, Text, Title } from "@mantine/core";
import { useState } from "react";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import { usePermissions } from "../../app/shell/usePermissions";
import { useTask } from "../review/hooks";
import { TaskStateBadge } from "../review/TaskStateBadge";
import type { ReviewOutput } from "../../lib/types";
import { OUTPUT_LABEL } from "./labels";
import { useDeleteOutput } from "./mutations";
import { AddOutputModal } from "./AddOutputModal";

function ActionRow({ output, nameOf }: { output: ReviewOutput; nameOf: (id: string | null) => string }) {
  const { data: task } = useTask(output.spawned_task_id ?? null); // best-effort; 404 if not the owner
  return (
    <Group gap="xs" wrap="nowrap">
      <Text size="sm">{output.description}</Text>
      <Text size="xs" c="dimmed">· {nameOf(output.owner_user_id)}{output.due_date ? ` · due ${output.due_date}` : ""}</Text>
      {output.spawned_task_id && task && <TaskStateBadge state={task.state} />}
    </Group>
  );
}

export function ReviewOutputsSection({ reviewId, outputs, editable }: {
  reviewId: string; outputs: ReviewOutput[]; editable: boolean;
}) {
  const { can } = usePermissions();
  const { data: directory } = useUserDirectory();
  const del = useDeleteOutput();
  const [addOpen, setAddOpen] = useState(false);
  const nameOf = (id: string | null) =>
    id ? (directory?.find((u) => u.id === id)?.display_name ?? "a user") : "—";
  const byType = (t: ReviewOutput["output_type"]) => outputs.filter((o) => o.output_type === t);
  const canEdit = editable && can("mgmtReview.record_outputs");

  return (
    <Stack gap="sm">
      <Group justify="space-between">
        <Title order={4}>Review outputs (9.3.3)</Title>
        {canEdit && <Button size="xs" variant="light" onClick={() => setAddOpen(true)}>Add output</Button>}
      </Group>
      {(["DECISION", "ACTION", "IMPROVEMENT"] as const).map((t) => {
        const rows = byType(t);
        if (rows.length === 0) return null;
        return (
          <Card key={t} withBorder>
            <Stack gap="xs">
              <Group justify="space-between">
                <Text fw={600}>{OUTPUT_LABEL[t]}</Text>
                <Badge variant="light">{rows.length}</Badge>
              </Group>
              {rows.map((o) => (
                <Group key={o.id} justify="space-between" wrap="nowrap">
                  {t === "ACTION" ? <ActionRow output={o} nameOf={nameOf} />
                    : <Text size="sm">{o.description}</Text>}
                  {canEdit && (
                    <Button size="compact-xs" variant="subtle" color="red"
                      onClick={() => void del.mutateAsync({ id: reviewId, oid: o.id })}>Remove</Button>
                  )}
                </Group>
              ))}
            </Stack>
          </Card>
        );
      })}
      {outputs.length === 0 && <Text size="sm" c="dimmed">No outputs recorded yet.</Text>}
      {addOpen && <AddOutputModal opened reviewId={reviewId} onClose={() => setAddOpen(false)} />}
    </Stack>
  );
}
```
(`TaskStateBadge` already exists in `features/review/` — `TasksInbox.tsx` imports it. `useTask` is in `features/review/hooks.ts`.)

- [ ] **Step 5: Run the test — green.**

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/features/management-review/ReviewOutputsSection.tsx apps/web/src/features/management-review/AddOutputModal.tsx apps/web/src/features/management-review/ReviewOutputsSection.test.tsx
git commit -m "feat(s-mr-2): 9.3.3 review-outputs section + add-output editor"
```

---

### Task 10: detail page (header + Lifecycle card) + `NewManagementReviewModal`

**Files:** Create `ManagementReviewDetailPage.tsx`, `NewManagementReviewModal.tsx`, `ManagementReviewDetailPage.test.tsx`.

The detail orchestrates: header (identifier + `StateBadge` + title + period/review_date/attendees) → `<ReviewInputsSection>` → `<ReviewOutputsSection editable={draft}>` → a **Lifecycle card** reusing `<ApprovalStepper>` with the Compile-inputs / Submit / Release / Close actions + the close-readiness note. Affordances derive from `current_state` + `usePermissions().can(...)` (no server `capabilities` block on the MR serializer).

- [ ] **Step 1: Write the failing detail test** — render at `/management-reviews/:id`; `waitFor` the title; assert the inputs + outputs sections render; with `mgmtReview.record_outputs` granted and `current_state==="Draft"`, a "Compile inputs" + "Submit for review" button show; assert the close-gate alert maps `review_close_blocked`. `import { expect, it } from "vitest"`; use `renderWithProviders(ui, { route: "/management-reviews/<id>" })`.

- [ ] **Step 2: Run — fails.**

- [ ] **Step 3: Write `NewManagementReviewModal.tsx`** (mirror `NewObjectiveModal` shape; `{open && <Modal/>}` is enforced by the parent):
```typescript
import { Alert, Button, Group, Modal, Stack, TextInput } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import { useCreateReview } from "./mutations";

export function NewManagementReviewModal({ opened, onClose, onCreated }: {
  opened: boolean; onClose: () => void; onCreated: (id: string) => void;
}) {
  const create = useCreateReview();
  const [title, setTitle] = useState("");
  const [period, setPeriod] = useState("");
  const [error, setError] = useState<string | null>(null);
  const canSave = title.trim().length > 0;

  async function submit() {
    setError(null);
    try {
      const mr = await create.mutateAsync({ title: title.trim(), period_label: period.trim() || undefined });
      onCreated(mr.id);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong. Please retry.");
    }
  }
  return (
    <Modal opened={opened} onClose={onClose} title="New management review">
      <Stack gap="sm">
        <TextInput label="Title" required value={title} onChange={(e) => setTitle(e.currentTarget.value)} />
        <TextInput label="Period" placeholder="2026 Annual" value={period} onChange={(e) => setPeriod(e.currentTarget.value)} />
        {error && <Alert color="red" withCloseButton onClose={() => setError(null)}>{error}</Alert>}
        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose}>Cancel</Button>
          <Button disabled={!canSave} loading={create.isPending} onClick={() => void submit()}>Create</Button>
        </Group>
      </Stack>
    </Modal>
  );
}
```

- [ ] **Step 4: Write `ManagementReviewDetailPage.tsx`** — mirror `ObjectiveDetailPage` (the error/forbidden guard, `nameOf`, the Lifecycle `<Card>`). Key adaptations: affordances from `current_state` + `can(...)`; `ApprovalStepper` gets `effectiveFrom={null}` (the MR serializer has no `effective_from`); the close-gate maps the as-built codes:
```typescript
import { Alert, Button, Card, Container, Group, Loader, Stack, Text, Title } from "@mantine/core";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import { usePermissions } from "../../app/shell/usePermissions";
import { ApiError } from "../../lib/api";
import { ApprovalStepper } from "../document/ApprovalStepper";
import { StateBadge } from "../document/StateBadge";
import { useMgmtReview, useMgmtReviewApproval } from "./hooks";
import { useCloseReview, useCompileInputs, useReleaseReview, useSubmitReview } from "./mutations";
import { ReviewInputsSection } from "./ReviewInputsSection";
import { ReviewOutputsSection } from "./ReviewOutputsSection";

const CLOSE_CODE_COPY: Record<string, string> = {
  review_close_blocked: "Close is blocked — an action output's task isn't complete yet.",
  review_not_open_to_close: "This review isn't open to close yet (release it first).",
};
function errMsg(e: unknown): string {
  if (e instanceof ApiError) return CLOSE_CODE_COPY[e.code] ?? e.message;
  return "Something went wrong. Please retry.";
}

export function ManagementReviewDetailPage() {
  const { id = null } = useParams();
  const { data: mr, isLoading, isError, forbidden } = useMgmtReview(id);
  const { data: instance } = useMgmtReviewApproval(id);
  const { data: directory } = useUserDirectory();
  const { can } = usePermissions();
  const compile = useCompileInputs();
  const submit = useSubmitReview();
  const release = useReleaseReview();
  const close = useCloseReview();
  const [actionError, setActionError] = useState<string | null>(null);

  if (isError || !mr) {
    if (isLoading) return <Container size="lg" py="md"><Loader /></Container>;
    return (
      <Container size="lg" py="md">
        <Alert color={forbidden ? "gray" : "red"} title="Couldn't load this review">
          {forbidden ? "You don't have access to this management review." : "It may have been removed, or you may not have access."}
        </Alert>
      </Container>
    );
  }
  const nameOf = (uid: string | null) =>
    uid ? (directory?.find((u) => u.id === uid)?.display_name ?? "a user") : "—";
  const isDraft = mr.current_state === "Draft";
  const canRecord = can("mgmtReview.record_outputs");
  const canCompile = canRecord && isDraft;
  const canSubmit = canRecord && isDraft;
  const canRelease = can("document.release") && mr.current_state === "Approved";
  const canClose = canRecord && mr.close_state === "ActionsTracked";

  async function run(fn: () => Promise<unknown>) {
    setActionError(null);
    try { await fn(); } catch (e) { setActionError(errMsg(e)); }
  }

  return (
    <Container size="lg" py="md">
      <Stack gap="lg">
        <div>
          <Group gap="xs" mb={4}>
            <Text c="dimmed" size="sm" fw={500}>{mr.identifier}</Text>
            <StateBadge state={mr.current_state} />
          </Group>
          <Title order={2}>{mr.title}</Title>
          <Text size="sm" c="dimmed">
            {mr.period_label ?? "—"}{mr.review_date ? ` · ${mr.review_date}` : ""}
            {mr.attendees?.length ? ` · ${mr.attendees.map((a) => a.name).join(", ")}` : ""}
          </Text>
        </div>

        <ReviewInputsSection inputs={mr.inputs} />
        <ReviewOutputsSection reviewId={mr.id} outputs={mr.outputs} editable={isDraft} />

        {(canCompile || canSubmit || canRelease || canClose || instance) && (
          <Card withBorder>
            <Stack gap="sm">
              <Text fw={600}>Lifecycle</Text>
              {instance && (
                <ApprovalStepper
                  instance={instance}
                  docState={mr.current_state}
                  effectiveFrom={null}
                  nameOf={nameOf}
                />
              )}
              {actionError && (
                <Alert color="red" withCloseButton onClose={() => setActionError(null)}>{actionError}</Alert>
              )}
              {canCompile && (
                <Group>
                  <Button variant="light" loading={compile.isPending}
                    onClick={() => void run(() => compile.mutateAsync(mr.id))}>Compile inputs</Button>
                  <Text size="xs" c="dimmed">Re-compiles the 9.3.2 inputs as-of now (Draft only).</Text>
                </Group>
              )}
              {canSubmit && (
                <Group>
                  <Button color="teal" loading={submit.isPending}
                    onClick={() => void run(() => submit.mutateAsync(mr.id))}>Submit for review</Button>
                  <Text size="xs" c="dimmed">Freezes the minutes and starts approval.</Text>
                </Group>
              )}
              {canRelease && (
                <Group>
                  <Button color="teal" loading={release.isPending}
                    onClick={() => void run(() => release.mutateAsync(mr.id))}>Release</Button>
                  <Text size="xs" c="dimmed">Releases the review → Effective (flips the 9.3 ★) and spawns action tasks.</Text>
                </Group>
              )}
              {canClose && (
                <Group>
                  <Button loading={close.isPending}
                    onClick={() => void run(() => close.mutateAsync(mr.id))}>Close review</Button>
                  <Text size="xs" c="dimmed">Closes once every action output's task is complete.</Text>
                </Group>
              )}
            </Stack>
          </Card>
        )}
      </Stack>
    </Container>
  );
}
```

- [ ] **Step 5: Run the detail test + the full management-review folder + typecheck**

Run: `cd apps/web && npx vitest run src/features/management-review && npx tsc --noEmit`. Expected: PASS + clean.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/features/management-review/ManagementReviewDetailPage.tsx apps/web/src/features/management-review/NewManagementReviewModal.tsx apps/web/src/features/management-review/ManagementReviewDetailPage.test.tsx
git commit -m "feat(s-mr-2): management-review detail page + lifecycle cockpit + create modal"
```

---

## Phase D — FE /tasks legs + Home widget

### Task 11: the `MGMT_REVIEW` arm (MR_INPUT nav + MR_ACTION complete-only)

**Files:** Create `features/review/MgmtReviewContext.tsx`, `MrActionCard.tsx`, `mrTaskHooks.ts`, `MrActionCard.test.tsx`; modify `features/review/ReviewApprovePage.tsx`.

**Invariants:** add `!isMgmtReview` to the document-branch disablers (`ReviewApprovePage.tsx:29-30`) so the MR task doesn't resolve a subject document. Branch on `task.type` inside the arm — MR_INPUT renders nav-only (no decision affordance; `decide_mr_task` doesn't gate on type, so the FE enforces non-decidability). MR_ACTION uses a dedicated `MrActionCard` (one-click `complete`, no signature) — mirror `AttestationCard`, **not** `DecisionCard`.

- [ ] **Step 1: Write the failing `MrActionCard` test** — render with the default MSW; assert one "Mark action complete" button, no sign checkbox; clicking posts `/api/v1/tasks/:id/decision` with `outcome:"complete"` (assert via a `server.use` spy or a fulfilled mutation → navigate). `import { expect, it } from "vitest"`.

- [ ] **Step 2: Run — fails.**

- [ ] **Step 3: Write `mrTaskHooks.ts`** — `useMgmtReview` context read (best-effort, gated mgmtReview.read) reuses `features/management-review/hooks::useMgmtReview` (re-export or import directly). Add `useDecideMrTask` (mgmt-review invalidations + `my-tasks`):
```typescript
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useApi } from "../../lib/api";

export function useDecideMrTask() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ taskId, idempotencyKey }: { taskId: string; idempotencyKey: string }) =>
      api.send<{ current_state: string }>("POST", `/api/v1/tasks/${taskId}/decision`,
        { outcome: "complete" }, { "Idempotency-Key": idempotencyKey }),
    onSuccess: (_d, { taskId }) => {
      void qc.invalidateQueries({ queryKey: ["task", taskId] });
      void qc.invalidateQueries({ queryKey: ["tasks"] });
      void qc.invalidateQueries({ queryKey: ["my-tasks"] });
      void qc.invalidateQueries({ queryKey: ["management-reviews"] });
    },
  });
}
```

- [ ] **Step 4: Write `MgmtReviewContext.tsx`** — mirror `PeriodicReviewContext` (best-effort, `retry:false`, yellow-Alert forbidden-degrade) but read the review via `useMgmtReview(reviewId)`; show the review identifier/title/state. Never block the card.

- [ ] **Step 5: Write `MrActionCard.tsx`** — mirror `AttestationCard` (per-mount `crypto.randomUUID` idemKey → `mutateAsync` → `navigate("/tasks")`; map `validation_error`/`not_found` codes to calm copy):
```typescript
import { Alert, Button, Card, Group, Stack, Text } from "@mantine/core";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../../lib/api";
import { useDecideMrTask } from "./mrTaskHooks";

const CODE_COPY: Record<string, string> = {
  validation_error: "This action only supports being marked complete.",
  not_found: "This task is no longer assigned to you.",
};

export function MrActionCard({ taskId }: { taskId: string }) {
  const decide = useDecideMrTask();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const [idemKey] = useState(() => crypto.randomUUID());

  async function submit() {
    setError(null);
    try {
      await decide.mutateAsync({ taskId, idempotencyKey: idemKey });
      navigate("/tasks");
    } catch (e) {
      setError(e instanceof ApiError ? (CODE_COPY[e.code] ?? e.message) : "Something went wrong. Please retry.");
    }
  }
  return (
    <Card withBorder>
      <Stack gap="md">
        <Text fw={600}>Mark this management-review action complete</Text>
        <Text size="sm">Completing confirms the tracked action from the review is done.</Text>
        {error && <Alert color="red" withCloseButton onClose={() => setError(null)}>{error}</Alert>}
        <Group justify="flex-end">
          <Button variant="subtle" onClick={() => navigate("/tasks")}>Cancel</Button>
          <Button onClick={() => void submit()} loading={decide.isPending}>Mark action complete</Button>
        </Group>
      </Stack>
    </Card>
  );
}
```

- [ ] **Step 6: Wire the arm in `ReviewApprovePage.tsx`** — add the flag at `:25` and the disabler guards at `:29-30`:
```tsx
  const isMgmtReview = task?.subject_type === "MGMT_REVIEW";
  const { data: instance } = useWorkflowInstance(!isCapa && !isPeriodic && !isDocAck && !isMgmtReview && task ? task.instance_id : null);
  const docId = !isCapa && !isPeriodic && !isDocAck && !isMgmtReview ? (instance?.subject_id ?? null) : null;
```
Add the arm after the `isDocAck` block (`:159`), before the DOCUMENT fallthrough (`:161`):
```tsx
  if (isMgmtReview) {
    const title = task.type === "MR_INPUT" ? "Prepare management review" : "Management review action";
    return (
      <Stack gap="lg">
        <Title order={2}>{title}</Title>
        <Grid gutter="lg" align="flex-start">
          <Grid.Col span={{ base: 12, md: 7 }}>
            <MgmtReviewContext reviewId={task.subject_id!} />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 5 }}>
            {task.type === "MR_INPUT" ? (
              <Card withBorder>
                <Stack gap="sm">
                  <Text fw={600}>Prepare this review</Text>
                  <Text size="sm">Compile the inputs and record the outputs, then submit it for review.</Text>
                  <Button component={Link} to={`/management-reviews/${task.subject_id!}`}>Open the review →</Button>
                </Stack>
              </Card>
            ) : decidable ? (
              <MrActionCard taskId={task.id} />
            ) : (
              decidedAlert
            )}
          </Grid.Col>
        </Grid>
      </Stack>
    );
  }
```
(Import `Card`, `Button`, `Link`, `MgmtReviewContext`, `MrActionCard` at the top of the file.)

- [ ] **Step 7: Run the review tests + typecheck**

Run: `cd apps/web && npx vitest run src/features/review && npx tsc --noEmit`. Expected: PASS + clean (the DOCUMENT/CAPA/PERIODIC arms unchanged).

- [ ] **Step 8: Commit**

```bash
git add apps/web/src/features/review/MgmtReviewContext.tsx apps/web/src/features/review/MrActionCard.tsx apps/web/src/features/review/mrTaskHooks.ts apps/web/src/features/review/MrActionCard.test.tsx apps/web/src/features/review/ReviewApprovePage.tsx
git commit -m "feat(s-mr-2): /tasks MGMT_REVIEW arm (MR_INPUT nav + MR_ACTION complete-only card)"
```

---

### Task 12: Home next-review widget

**Files:** Create `features/home/NextReviewLine.tsx` + test; modify `features/home/CheckCard.tsx`.

A `StatLine` in `CheckCard` (clause 9 houses 9.3) off `useMgmtReviewNextDue()`: `forbidden`/`isError` → no line (never drags the tile red); `owner_configured:false` → "Review cadence not configured"; `review_state` → `overdue`→red, `due_soon`→amber, `current`→green, `null`→neutral "no review released yet".

- [ ] **Step 1: Write the failing test** — `NextReviewLine.test.tsx`: with the default fixture (`due_soon`, `2026-06-01`), assert "Next review due 2026-06-01" + an amber tone; override to `owner_configured:false` → "Review cadence not configured"; override to a 403 → renders nothing. `import { expect, it } from "vitest"`.

- [ ] **Step 2: Run — fails.**

- [ ] **Step 3: Write `NextReviewLine.tsx`**:
```typescript
import { useMgmtReviewNextDue } from "../management-review/hooks";
import { StatLine } from "./StatLine";
import type { Rag } from "./rag";

const STATE_TONE: Record<string, Rag> = { overdue: "red", due_soon: "amber", current: "green" };

export function NextReviewLine() {
  const { data, forbidden, isError } = useMgmtReviewNextDue();
  if (forbidden || isError || !data) return null;
  if (!data.owner_configured) {
    return <StatLine label="Review cadence not configured" tone="neutral" />;
  }
  if (!data.next_review_due || !data.review_state) {
    return <StatLine label="No management review released yet" tone="neutral" />;
  }
  const tone = STATE_TONE[data.review_state] ?? "neutral";
  const label = data.review_state === "overdue"
    ? `Management review overdue (was due ${data.next_review_due})`
    : `Next management review due ${data.next_review_due}`;
  return <StatLine label={label} tone={tone} />;
}
```

- [ ] **Step 4: Wire it into `CheckCard.tsx`** — push the line + (when the state is RAG-bearing) contribute its RAG to the tile. Add to the `lines`/`rags` assembly:
```tsx
  const nd = useMgmtReviewNextDue();
  // ... existing au/cl signals ...
  if (!nd.forbidden && !nd.isError && nd.data) {
    lines.push(<NextReviewLine key="nextrev" />);
    if (nd.data.review_state) {
      const t: Rag = nd.data.review_state === "overdue" ? "red" : nd.data.review_state === "due_soon" ? "amber" : "green";
      rags.push(t);
    }
  }
  const allForbidden = au.forbidden && cl.forbidden && nd.forbidden;
  const loading = au.isLoading || cl.isLoading || nd.isLoading;
```
(Import `useMgmtReviewNextDue`, `NextReviewLine`, and the `Rag` type. Render `NextReviewLine` directly in `lines` rather than re-reading — or inline the StatLine here; pick one to avoid a double read. Simplest: build the StatLine inline in CheckCard from `nd.data` and drop the separate component's read — keep `NextReviewLine` as the standalone-tested unit and have CheckCard reuse the same logic via a shared helper. The subagent picks the cleaner of: (a) CheckCard renders `<NextReviewLine/>` and separately reads `nd` for the RAG, or (b) a `nextReviewSignal(data)` helper returns `{line, rag}`.)

- [ ] **Step 5: Run the home tests + typecheck**

Run: `cd apps/web && npx vitest run src/features/home && npx tsc --noEmit`. Expected: PASS + clean.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/features/home/NextReviewLine.tsx apps/web/src/features/home/NextReviewLine.test.tsx apps/web/src/features/home/CheckCard.tsx
git commit -m "feat(s-mr-2): Home CHECK next-review widget (N9 status-against-a-rule)"
```

---

## Phase E — reconciliations, gates, wrap

### Task 13: doc reconciliations + full gate + diff-critic + slice-history + CLAUDE.md

**Files:** Modify the stale S-mr-1 spec note + R45/close-code text; `docs/slice-history.md`; `CLAUDE.md` (Recent learnings + Current status).

- [ ] **Step 1: Reconcile the route name + the close-gate code in the docs**

In `docs/superpowers/specs/2026-06-12-s-mr-1-management-review-backend-design.md` §s7, change the stale `/mgmt-reviews` reference to `/management-reviews`. In `docs/decisions-register.md` (R45) and the S-mr-1 spec, correct `mgmt_review_close_blocked` → `review_close_blocked` (and note `review_not_open_to_close`) — the as-built code is authoritative.

- [ ] **Step 2: Run the full local gate**

Run the `/check-api` skill — expected: ruff + format + mypy-strict + unit all clean.
Run the `/check-web` skill (eslint + strict `tsc --noEmit` + build + the full vitest suite) — expected: clean (the full run catches `noUncheckedIndexedAccess` + the jest-dom×tsc trap the per-file runs miss).
Run the `/check-contracts` skill — expected: redocly clean.
Run the api integration suite for the touched files: `cd apps/api && uv run pytest -m integration tests/integration/test_mgmt_review.py tests/integration/test_mgmt_review_cadence.py tests/integration/test_quality_objectives.py -v` — expected: PASS.

- [ ] **Step 3: Run the diff-critic agent on the branch diff**

Dispatch the `diff-critic` subagent (`Agent` tool, `subagent_type: diff-critic`) against the branch diff. Triage every finding: verify against code before fixing; fix confirmed issues inline + re-run the relevant gate.

- [ ] **Step 4: Update `docs/slice-history.md`**

Add the S-mr-2 entry: the FE module (register/detail/lifecycle/outputs editor), the /tasks MGMT_REVIEW arm (MR_INPUT nav + MR_ACTION complete-only `MrActionCard`), the Home next-review widget; the three backend touches (next-due read + shared `read_cadence`/`mr_review_state`, MR_INPUT auto-resolve at submit, shared `compute_scorecard`); the reconciliations; the named deferrals (s11 of the spec); the test deltas. Note: **closes the Management Review family; the ISO 9001:2015 ★ spine is now usable end-to-end in the SPA.**

- [ ] **Step 5: Update `CLAUDE.md`**

Add a Recent-learnings line (newest first; demote the oldest if over ~12) capturing the load-bearing S-mr-2 facts: the next-due read needed because no GET exposes the cadence; the MR_INPUT auto-resolve seam (before `session.commit` in `submit_review_for_review`, MGMT_REVIEW container not the DOCUMENT approval instance, plain `state=DONE` no-signature); `compute_scorecard` authz-agnostic (gates stay at the call sites); the FE MrActionCard (complete-only, not DecisionCard) + the `!isMgmtReview` disabler; the `source_ref` summary shapes; the route-ordering (`next-due` before `/{review_id}`). Update the **Current status** pointer (S-mr-2 ✅, family closed) + the web-test delta.

- [ ] **Step 6: Commit the wrap**

```bash
git add docs CLAUDE.md
git commit -m "docs(s-mr-2): reconcile route/close-code, slice-history, learnings"
```

- [ ] **Step 7: Pre-merge live smoke (owner-driven)**

Rebuild the web image (`docker compose --env-file .env -f infra/compose/compose.yml -f infra/compose/compose.s.yml up -d --build web`) + the api/worker if the backend changed. Grant SYSTEM overrides for `mgmtReview.read` / `mgmtReview.create` / `mgmtReview.record_outputs` / `document.release` on the **live** demo `app_user` row (org AHT). Owner does the Keycloak login. Via Chrome MCP: create a review → compile-inputs → author a DECISION + an ACTION (owner=demo) → submit (the MR_INPUT leaves My-Tasks) → approve → release (the 9.3 ★ flips, the MR_ACTION spawns) → complete the MR_ACTION in `/tasks` → close; plus the Home CHECK next-review line and the `/management-reviews` register.

---

## Self-review (run before opening the PR)

- **Spec coverage:** s2 (next-due) → Task 2; s3 (MR_INPUT) → Task 3; s4 (compute_scorecard) → Task 1; s5 (module) → Tasks 4-10; s6 (/tasks legs) → Task 11; s7 (Home widget) → Task 12; s8 (gating/smoke) → Tasks 7/13; s9 (reconciliations) → Task 13. All covered.
- **No placeholders:** every code step shows the code; the two "subagent picks the cleaner of (a)/(b)" notes (CheckCard wiring, the NewManagementReviewModal sequencing) are genuine local judgment calls with both options spelled out, not gaps.
- **Type consistency:** `useMgmtReview`/`useMgmtReviewApproval`/`useMgmtReviewNextDue`, `useCompileInputs`/`useSubmitReview`/`useReleaseReview`/`useCloseReview`/`useAddOutput`/`useDeleteOutput`/`usePatchMeta`, `useDecideMrTask`, `read_cadence`/`mr_review_state`/`CadenceStatus`/`MR_REVIEW_LEAD_DAYS`, `compute_scorecard`, `_resolve_prepare_tasks` — names used consistently across tasks.
- **Backend-first ordering:** Tasks 1-3 land + tested before the FE (Tasks 4-12) pins fixtures to the serializers.
