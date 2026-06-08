"""S5 integration proofs — the task/approval workflow + the one-transaction decision.

submit-review instantiates the approval ``workflow_instance`` + an APPROVE ``task`` (visible in the
role-assigned approver's My-Tasks); ``POST /tasks/{id}/decision`` writes a ``signature_event`` +
``task_outcome`` + the FSM transition atomically, is idempotent via ``Idempotency-Key``, and rolls
back as a unit on failure. Approval routes only through the task — there is no direct /approve.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from easysynq_api.db.models._signature_enums import SignatureMeaning
from easysynq_api.db.models._vault_enums import DocumentCurrentState, VersionState
from easysynq_api.db.models._workflow_enums import TaskState
from easysynq_api.db.models.documented_information import DocumentedInformation
from easysynq_api.db.models.signature_event import SignatureEvent as SignatureEventRow
from easysynq_api.db.models.workflow import Task, TaskOutcome, WorkflowInstance
from easysynq_api.db.session import get_sessionmaker

from . import s5_helpers as s5
from .test_vault import _auth, _checkin, _create, _ensure_user, _map_clause, _upload

pytestmark = pytest.mark.integration


@pytest.fixture
def subj() -> SimpleNamespace:
    salt = uuid.uuid4().hex[:10]
    return SimpleNamespace(a=f"kc-author-{salt}", b=f"kc-approver-{salt}")


async def _to_in_review(client: AsyncClient, h_author: dict[str, str], type_id: str) -> str:
    """author: create → checkout → upload → checkin → submit-review. Returns the document id."""
    did = (await _create(client, h_author, type_id))["id"]
    await client.post(f"/api/v1/documents/{did}/checkout", headers=h_author)
    sha = await _upload(client, h_author, did, f"approval-{did}".encode())
    ci = await _checkin(client, h_author, did, sha, change_reason="v1", change_significance="MAJOR")
    assert ci.status_code == 201, ci.text
    await _map_clause(client, h_author, did)  # S9: submit-review needs ≥1 clause_mapping
    sr = await client.post(f"/api/v1/documents/{did}/submit-review", headers=h_author)
    assert sr.status_code == 200, sr.text
    return did


async def _signature_count(version_id: uuid.UUID, meaning: SignatureMeaning) -> int:
    async with get_sessionmaker()() as s:
        return (
            await s.execute(
                select(func.count())
                .select_from(SignatureEventRow)
                .where(
                    SignatureEventRow.signed_object_id == version_id,
                    SignatureEventRow.meaning == meaning,
                )
            )
        ).scalar_one()


async def _latest_version_id(did: str) -> uuid.UUID:
    async with get_sessionmaker()() as s:
        from easysynq_api.db.models.document_version import DocumentVersion

        return (
            await s.execute(
                select(DocumentVersion.id)
                .where(DocumentVersion.document_id == uuid.UUID(did))
                .order_by(DocumentVersion.version_seq.desc())
                .limit(1)
            )
        ).scalar_one()


async def test_submit_review_instantiates_workflow_and_task_in_my_tasks(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """submit-review opens an IN_APPROVAL instance + a PENDING APPROVE task; the role-assigned
    approver sees it in My-Tasks (candidate_pool membership)."""
    await s5.grant_lifecycle(subj.a)
    await s5.grant_role(subj.b, "Approver")
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)

    did = await _to_in_review(app_client, ha, await s5.type_id("SOP"))

    async with get_sessionmaker()() as s:
        instance = (
            await s.execute(
                select(WorkflowInstance).where(WorkflowInstance.subject_id == uuid.UUID(did))
            )
        ).scalar_one()
        assert instance.current_state == "IN_APPROVAL"
        task = (await s.execute(select(Task).where(Task.instance_id == instance.id))).scalar_one()
        assert task.state is TaskState.PENDING

    mine = await app_client.get("/api/v1/tasks?assignee=me&state=PENDING", headers=hb)
    assert mine.status_code == 200, mine.text
    ids = {t["id"] for t in mine.json()}
    assert str(task.id) in ids


async def test_decision_approve_one_transaction(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """approve → 200 with a persisted signature_event(approval) + task_outcome + DONE task +
    Approved document, all in one transaction."""
    await s5.grant_lifecycle(subj.a)
    await s5.grant_role(subj.b, "Approver")
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)

    did = await _to_in_review(app_client, ha, await s5.type_id("SOP"))
    version_id = await _latest_version_id(did)
    task_id = await s5.task_for_doc(did)

    dec = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hb, json={"outcome": "approve"}
    )
    assert dec.status_code == 200, dec.text
    body = dec.json()
    assert body["outcome"] == "approve"
    assert body["signature_event"]["meaning"] == "approval"
    assert body["signature_event"]["method"] == "SESSION"

    assert await _signature_count(version_id, SignatureMeaning.approval) == 1
    async with get_sessionmaker()() as s:
        task = await s.get(Task, uuid.UUID(task_id))
        assert task.state is TaskState.DONE
        assert task.assignee_user_id is not None
        outcomes = (
            (await s.execute(select(TaskOutcome).where(TaskOutcome.task_id == task.id)))
            .scalars()
            .all()
        )
        assert len(outcomes) == 1
        doc = await s.get(DocumentedInformation, uuid.UUID(did))
        assert doc.current_state is DocumentCurrentState.Approved


async def test_decision_idempotency(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """Same Idempotency-Key replays the outcome (200); a different second decision 409s."""
    await s5.grant_lifecycle(subj.a)
    await s5.grant_role(subj.b, "Approver")
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)

    did = await _to_in_review(app_client, ha, await s5.type_id("SOP"))
    version_id = await _latest_version_id(did)
    task_id = await s5.task_for_doc(did)
    key = uuid.uuid4().hex

    first = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision",
        headers={**hb, "Idempotency-Key": key},
        json={"outcome": "approve"},
    )
    assert first.status_code == 200, first.text
    replay = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision",
        headers={**hb, "Idempotency-Key": key},
        json={"outcome": "approve"},
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["task_id"] == first.json()["task_id"]
    # Exactly one signature_event despite the replay — no double-write.
    assert await _signature_count(version_id, SignatureMeaning.approval) == 1

    # A second decision with NO key on the now-DONE task conflicts.
    conflict = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hb, json={"outcome": "approve"}
    )
    assert conflict.status_code == 409, conflict.text
    # A second decision with a DIFFERENT key also conflicts (it is not a replay of the original).
    other = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision",
        headers={**hb, "Idempotency-Key": uuid.uuid4().hex},
        json={"outcome": "approve"},
    )
    assert other.status_code == 409, other.text
    # Still exactly one signature — neither conflicting retry wrote a second.
    assert await _signature_count(version_id, SignatureMeaning.approval) == 1


async def test_changes_requested_returns_to_draft_without_signature(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await s5.grant_lifecycle(subj.a)
    await s5.grant_role(subj.b, "Approver")
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)

    did = await _to_in_review(app_client, ha, await s5.type_id("SOP"))
    version_id = await _latest_version_id(did)
    task_id = await s5.task_for_doc(did)

    dec = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision",
        headers=hb,
        json={"outcome": "changes_requested", "comment": "fix clause 8.4"},
    )
    assert dec.status_code == 200, dec.text
    assert dec.json()["signature_event"] is None

    assert await _signature_count(version_id, SignatureMeaning.approval) == 0
    async with get_sessionmaker()() as s:
        doc = await s.get(DocumentedInformation, uuid.UUID(did))
        assert doc.current_state is DocumentCurrentState.Draft


async def test_changes_requested_requires_comment(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await s5.grant_lifecycle(subj.a)
    await s5.grant_role(subj.b, "Approver")
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    did = await _to_in_review(app_client, ha, await s5.type_id("SOP"))
    task_id = await s5.task_for_doc(did)
    r = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hb, json={"outcome": "changes_requested"}
    )
    assert r.status_code == 422, r.text


async def test_document_approval_returns_instance_with_tasks(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """GET /documents/{id}/approval returns the active instance + its APPROVE task (S-web-5)."""
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


async def test_decision_rolls_back_as_one_unit(
    app_client: AsyncClient,
    app_under_test: object,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
) -> None:
    """A fault raised after the signature_event is staged (and after approve() mutated the version
    in-session) but before commit rolls the unit back. The load-bearing assertions: the staged
    signature_event did NOT persist and the version is still InReview (the FSM mutation reverted).
    The task_outcome/PENDING checks corroborate (those writes had not run at fault time)."""
    from easysynq_api.services.vault import DbSignatureEventSink, get_vault_signature_sink

    class _FaultSink:
        def __init__(self) -> None:
            self._delegate = DbSignatureEventSink()

        def record(self, session: object, event: object) -> object:
            self._delegate.record(session, event)  # type: ignore[arg-type]
            raise RuntimeError("injected fault after signature add")

    app_under_test.dependency_overrides[get_vault_signature_sink] = lambda: _FaultSink()  # type: ignore[attr-defined]
    try:
        await s5.grant_lifecycle(subj.a)
        await s5.grant_role(subj.b, "Approver")
        ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
        did = await _to_in_review(app_client, ha, await s5.type_id("SOP"))
        version_id = await _latest_version_id(did)
        task_id = await s5.task_for_doc(did)

        with pytest.raises(Exception):  # noqa: B017 — the injected fault propagates
            await app_client.post(
                f"/api/v1/tasks/{task_id}/decision", headers=hb, json={"outcome": "approve"}
            )

        assert await _signature_count(version_id, SignatureMeaning.approval) == 0
        async with get_sessionmaker()() as s:
            from easysynq_api.db.models.document_version import DocumentVersion

            assert (
                await s.execute(
                    select(func.count())
                    .select_from(TaskOutcome)
                    .where(TaskOutcome.task_id == uuid.UUID(task_id))
                )
            ).scalar_one() == 0
            task = await s.get(Task, uuid.UUID(task_id))
            assert task.state is TaskState.PENDING
            version = await s.get(DocumentVersion, version_id)
            assert version.version_state is VersionState.InReview
    finally:
        app_under_test.dependency_overrides.pop(get_vault_signature_sink, None)  # type: ignore[attr-defined]
