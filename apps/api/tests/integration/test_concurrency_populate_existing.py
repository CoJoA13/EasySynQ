"""Batch 1 (2026-07-22 review) — the S-drift-1 populate_existing fixes on the FOR UPDATE repo loads.

Each test primes a row into a request session's identity map (as the authz resolver's session.get
does), commits a change from a SEPARATE session, then calls the repo's ``for_update`` loader on the
primed session and asserts it returns the FRESH value — the two-session proof from
``test_periodic_review.test_locked_load_sees_concurrent_commit``. Every assertion mutation-verifies:
without ``.execution_options(populate_existing=True)`` the locked load hands back the stale
identity-map row, so the FSM / one-shot guard re-checks pre-lock state.

Assertions are scoped to this run's own rows; the integration suite shares one DB across files.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models._capa_enums import CapaCloseState, NcrDisposition
from easysynq_api.db.models._iso_audit_enums import AuditState
from easysynq_api.db.models._vault_enums import DocumentCurrentState
from easysynq_api.db.models.app_user import AppUser
from easysynq_api.db.models.audit import Audit
from easysynq_api.db.models.audit_finding import AuditFinding
from easysynq_api.db.models.capa import Capa
from easysynq_api.db.models.documented_information import DocumentedInformation
from easysynq_api.db.models.management_review import ManagementReview
from easysynq_api.db.models.ncr import Ncr
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.problems import ProblemException

from . import s5_helpers as s5
from .test_capa import _CAPA_KEYS, _grant, _subject
from .test_vault import _auth

pytestmark = pytest.mark.integration

_AUDIT_KEYS = (
    "audit.read",
    "audit.plan",
    "audit.create",
    "audit.conduct",
    "audit.close",
    "finding.create",
    "finding.read",
    "capa.read",
    "record.create",
)
_MR_KEYS = ("mgmtReview.create", "mgmtReview.read", "mgmtReview.record_outputs")


async def test_get_capa_for_update_sees_concurrent_commit(
    app_client: AsyncClient, token_factory: Callable[..., str], app_under_test: object
) -> None:
    """get_capa(for_update=True): the CAPA FSM one-shot guard must read the DB-current close_state,
    not the stale identity-map snapshot (else a racing verify writes a duplicate signed stage)."""
    from easysynq_api.services.capa.repository import get_capa

    subject = _subject("capa-drift")
    await _grant(subject, _CAPA_KEYS)
    h = _auth(token_factory, subject)
    r = await app_client.post(
        "/api/v1/capas", headers=h, json={"title": "drift", "severity": "Minor"}
    )
    assert r.status_code == 201, r.text
    capa_id = uuid.UUID(r.json()["id"])

    async with get_sessionmaker()() as session_s:
        cached = await session_s.get(Capa, capa_id)  # prime the identity map (the authz resolver)
        assert cached is not None and cached.close_state is CapaCloseState.Raised
        async with get_sessionmaker()() as session_b:  # a concurrent committed transition
            capa_b = await session_b.get(Capa, capa_id)
            assert capa_b is not None
            capa_b.close_state = CapaCloseState.Containment
            await session_b.commit()
        locked = await get_capa(session_s, capa_id, for_update=True)

    assert locked is not None and locked.close_state is CapaCloseState.Containment, (
        "get_capa(for_update=True) returned a stale close_state from the identity-map cache "
        "instead of the committed Containment; populate_existing is required."
    )


async def test_get_ncr_for_update_sees_concurrent_commit(
    app_client: AsyncClient, token_factory: Callable[..., str], app_under_test: object
) -> None:
    """get_ncr(for_update=True): the one-shot 8.7 disposition gate must read the locked row, not a
    stale None from the identity map (else a second disposition overwrites the first)."""
    from easysynq_api.services.capa.repository import get_ncr

    subject = _subject("ncr-drift")
    await _grant(subject, _CAPA_KEYS)
    h = _auth(token_factory, subject)
    r = await app_client.post(
        "/api/v1/ncrs",
        headers=h,
        json={"source": "process", "description": "drift", "severity": "Minor"},
    )
    assert r.status_code == 201, r.text
    ncr_id = uuid.UUID(r.json()["id"])

    async with get_sessionmaker()() as session_s:
        cached = await session_s.get(Ncr, ncr_id)
        assert cached is not None and cached.disposition is None
        async with get_sessionmaker()() as session_b:
            ncr_b = await session_b.get(Ncr, ncr_id)
            assert ncr_b is not None
            ncr_b.disposition = NcrDisposition.rework
            await session_b.commit()
        locked = await get_ncr(session_s, ncr_id, for_update=True)

    assert locked is not None and locked.disposition is NcrDisposition.rework, (
        "get_ncr(for_update=True) returned a stale disposition from the identity-map cache "
        "instead of the committed rework; populate_existing is required."
    )


async def _new_audit(app_client: AsyncClient, h: dict[str, str]) -> uuid.UUID:
    """program -> plan -> audit (state=Scheduled); returns the audit id."""
    program_id = (
        await app_client.post("/api/v1/audit-programs", headers=h, json={"title": "P"})
    ).json()["id"]
    plan_id = (
        await app_client.post(f"/api/v1/audit-programs/{program_id}/plans", headers=h, json={})
    ).json()["id"]
    r = await app_client.post("/api/v1/audits", headers=h, json={"plan_id": plan_id})
    assert r.status_code == 201, r.text
    return uuid.UUID(r.json()["id"])


async def test_get_audit_for_update_sees_concurrent_commit(
    app_client: AsyncClient, token_factory: Callable[..., str], app_under_test: object
) -> None:
    """get_audit(for_update=True): a racing close must validate against the DB-current state, not
    the stale identity-map snapshot (else a finding lands in a just-closed audit)."""
    from easysynq_api.services.audits.repository import get_audit

    subject = _subject("audit-drift")
    await _grant(subject, _AUDIT_KEYS)
    h = _auth(token_factory, subject)
    audit_id = await _new_audit(app_client, h)

    async with get_sessionmaker()() as session_s:
        cached = await session_s.get(Audit, audit_id)
        assert cached is not None and cached.state is AuditState.Scheduled
        async with get_sessionmaker()() as session_b:
            audit_b = await session_b.get(Audit, audit_id)
            assert audit_b is not None
            audit_b.state = AuditState.Planned
            await session_b.commit()
        locked = await get_audit(session_s, audit_id, for_update=True)

    assert locked is not None and locked.state is AuditState.Planned, (
        "get_audit(for_update=True) returned a stale state from the identity-map cache instead of "
        "the committed Planned; populate_existing is required."
    )


async def test_get_finding_for_update_sees_concurrent_commit(
    app_client: AsyncClient, token_factory: Callable[..., str], app_under_test: object
) -> None:
    """get_finding(for_update=True): the same S-drift-1 fix on the finding FSM load."""
    from easysynq_api.services.audits.repository import get_finding

    subject = _subject("finding-drift")
    await _grant(subject, _AUDIT_KEYS)
    h = _auth(token_factory, subject)
    audit_id = await _new_audit(app_client, h)
    # Walk Scheduled -> Planned -> InProgress so a finding can be recorded.
    await app_client.post(f"/api/v1/audits/{audit_id}/plan", headers=h)
    await app_client.post(f"/api/v1/audits/{audit_id}/conduct", headers=h)
    r = await app_client.post(
        f"/api/v1/audits/{audit_id}/findings", headers=h, json={"finding_type": "OBSERVATION"}
    )
    assert r.status_code == 201, r.text
    finding_id = uuid.UUID(r.json()["id"])

    async with get_sessionmaker()() as session_s:
        cached = await session_s.get(AuditFinding, finding_id)
        assert cached is not None and cached.clause_ref is None
        async with get_sessionmaker()() as session_b:
            f_b = await session_b.get(AuditFinding, finding_id)
            assert f_b is not None
            f_b.clause_ref = "9.2"
            await session_b.commit()
        locked = await get_finding(session_s, finding_id, for_update=True)

    assert locked is not None and locked.clause_ref == "9.2", (
        "get_finding(for_update=True) returned a stale clause_ref from the identity-map cache "
        "instead of the committed value; populate_existing is required."
    )


async def test_compile_inputs_locks_against_concurrent_submit_freeze(
    app_client: AsyncClient, token_factory: Callable[..., str], app_under_test: object
) -> None:
    """compile_inputs must re-read the MR doc state UNDER the FOR UPDATE lock: if a concurrent
    submit-freeze moved it out of Draft after the request session cached Draft, compile must 409 and
    not replace the frozen review_input set that approvers signed. Mutation-verify: without the
    locked populate_existing load it reads stale Draft from the identity map and re-compiles.
    """
    from easysynq_api.services.management_review.compile import compile_inputs

    subject = _subject("mr-drift")
    user_id = await _grant(subject, _MR_KEYS)
    h = _auth(token_factory, subject)
    r = await app_client.post(
        "/api/v1/management-reviews",
        headers=h,
        json={"title": "drift review", "period_label": "2026 Annual"},
    )
    assert r.status_code == 201, r.text
    mr_id = uuid.UUID(r.json()["id"])

    try:
        async with get_sessionmaker()() as session_s:
            # Prime the request session's identity map with the Draft doc (the authz resolver).
            doc = await session_s.get(DocumentedInformation, mr_id)
            assert doc is not None and doc.current_state is DocumentCurrentState.Draft
            review = await session_s.get(ManagementReview, mr_id)
            owner = await session_s.get(AppUser, user_id)
            assert review is not None and owner is not None

            # A concurrent transition freezes the minutes (Draft -> InReview) and commits.
            async with get_sessionmaker()() as session_b:
                doc_b = await session_b.get(DocumentedInformation, mr_id)
                assert doc_b is not None
                doc_b.current_state = DocumentCurrentState.InReview
                await session_b.commit()

            with pytest.raises(ProblemException) as exc:
                await compile_inputs(session_s, review, owner, owner)

        assert exc.value.status == 409, exc.value.title
        assert "Draft" in exc.value.title
    finally:
        # Neutralize the fabricated open review so the org-scoped cadence open-review guard ignores
        # it (Obsolete is not in _OPEN_STATES) — keep the shared integration DB free of a dangling
        # open MR for later cadence tests.
        async with get_sessionmaker()() as cleanup:
            stranded = await cleanup.get(DocumentedInformation, mr_id)
            if stranded is not None:
                stranded.current_state = DocumentCurrentState.Obsolete
                await cleanup.commit()


async def test_get_dcr_for_update_sees_concurrent_commit(
    app_client: AsyncClient, token_factory: Callable[..., str], app_under_test: object
) -> None:
    """get_dcr(for_update=True): every DCR FSM writer re-checks state under the lock — it must read
    the DB-current state, not the stale identity-map snapshot (else a racing assess resurrects a
    just-committed Cancelled DCR as Assessed)."""
    from easysynq_api.db.models._dcr_enums import DcrState
    from easysynq_api.db.models.dcr import Dcr
    from easysynq_api.services.dcr.repository import get_dcr

    from .test_dcr import _DCR_KEYS

    subject = _subject("dcr-drift")
    await _grant(subject, _DCR_KEYS)
    h = _auth(token_factory, subject)
    r = await app_client.post(
        "/api/v1/dcrs",
        headers=h,
        json={
            "change_type": "CREATE",
            "change_significance": "MINOR",
            "reason_class": "other",
            "reason_text": "populate_existing drift proof",
        },
    )
    assert r.status_code == 201, r.text
    dcr_id = uuid.UUID(r.json()["id"])

    async with get_sessionmaker()() as session_s:
        cached = await session_s.get(Dcr, dcr_id)  # prime the identity map (the authz resolver)
        assert cached is not None and cached.state is DcrState.Open
        async with get_sessionmaker()() as session_b:  # a concurrent committed cancel
            dcr_b = await session_b.get(Dcr, dcr_id)
            assert dcr_b is not None
            dcr_b.state = DcrState.Cancelled
            await session_b.commit()
        locked = await get_dcr(session_s, dcr_id, for_update=True)

    assert locked is not None and locked.state is DcrState.Cancelled, (
        "get_dcr(for_update=True) returned a stale state from the identity-map cache instead of "
        "the committed Cancelled; populate_existing is required."
    )


async def test_get_run_for_update_sees_concurrent_commit(
    app_client: AsyncClient, token_factory: Callable[..., str], app_under_test: object
) -> None:
    """get_run(for_update=True): the stage workers' "a late cancel won the race — respect it" guard
    must observe a concurrently committed CANCELLED, not the stale identity-map status the worker
    bound at stage entry (else the finalize commit resurrects a cancelled run)."""
    from easysynq_api.db.models._ingestion_enums import ImportRunStatus
    from easysynq_api.db.models.app_user import AppUser as AppUserRow
    from easysynq_api.db.models.import_run import ImportRun
    from easysynq_api.services.ingestion.repository import get_run

    user_id = await _grant(_subject("run-drift"), ("capa.read",))
    async with get_sessionmaker()() as seed:
        user = await seed.get(AppUserRow, user_id)
        assert user is not None
        run = ImportRun(
            org_id=user.org_id,
            source_root=f"drift-proof-{uuid.uuid4().hex}",
            source_root_hash=uuid.uuid4().hex,
            status=ImportRunStatus.DEDUPING,
            created_by=user.id,
        )
        seed.add(run)
        await seed.commit()
        run_id = run.id

    async with get_sessionmaker()() as session_s:
        cached = await session_s.get(ImportRun, run_id)  # the worker's stage-entry load
        assert cached is not None and cached.status is ImportRunStatus.DEDUPING
        async with get_sessionmaker()() as session_b:  # the operator's committed cancel
            run_b = await session_b.get(ImportRun, run_id)
            assert run_b is not None
            run_b.status = ImportRunStatus.CANCELLED
            await session_b.commit()
        locked = await get_run(session_s, run_id, for_update=True)

    assert locked is not None and locked.status is ImportRunStatus.CANCELLED, (
        "get_run(for_update=True) returned a stale status from the identity-map cache instead of "
        "the committed CANCELLED; populate_existing is required — the late-cancel guard can never "
        "fire without it."
    )


async def test_decide_locked_task_load_sees_concurrent_decision(
    app_client: AsyncClient, token_factory: Callable[..., str], app_under_test: object
) -> None:
    """decide()'s inline FOR UPDATE task load: a concurrent loser must observe the winner's
    committed DONE and 409, not re-check the stale PENDING from the request session's identity map
    (else two racing approvers both record a decision)."""
    from easysynq_api.db.models._workflow_enums import TaskState
    from easysynq_api.db.models.app_user import AppUser as AppUserRow
    from easysynq_api.db.models.workflow import Task, WorkflowInstance
    from easysynq_api.services.vault.audit import DbVaultAuditSink
    from easysynq_api.services.vault.signature import DbSignatureEventSink
    from easysynq_api.services.workflow.service import decide

    from .test_approval import _to_in_review

    salt = uuid.uuid4().hex[:10]
    author, approver = f"kc-drift-author-{salt}", f"kc-drift-approver-{salt}"
    await s5.grant_lifecycle(author)
    approver_id = await s5.grant_role(approver, "Approver")
    ha = _auth(token_factory, author)
    did = await _to_in_review(app_client, ha, await s5.type_id("SOP"))

    async with get_sessionmaker()() as lookup:
        instance = (
            await lookup.execute(
                select(WorkflowInstance).where(WorkflowInstance.subject_id == uuid.UUID(did))
            )
        ).scalar_one()
        task_id = (
            await lookup.execute(select(Task.id).where(Task.instance_id == instance.id))
        ).scalar_one()

    async with get_sessionmaker()() as session_s:
        cached = await session_s.get(Task, task_id)  # the endpoint's pre-decision load
        assert cached is not None and cached.state is TaskState.PENDING
        actor = await session_s.get(AppUserRow, approver_id)
        assert actor is not None
        async with get_sessionmaker()() as session_b:  # the winner's committed decision
            task_b = await session_b.get(Task, task_id)
            assert task_b is not None
            task_b.state = TaskState.DONE
            await session_b.commit()
        with pytest.raises(ProblemException) as exc:
            await decide(
                session_s,
                cached,
                actor,
                outcome="approve",
                comment=None,
                effective_from=None,
                idempotency_key=None,
                sig_sink=DbSignatureEventSink(),
                audit_sink=DbVaultAuditSink(),
            )

    assert exc.value.status == 409 and exc.value.title == "Task already decided", (
        "decide()'s locked task load read the stale PENDING from the identity-map cache instead of "
        "the committed DONE; populate_existing is required."
    )
