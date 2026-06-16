"""S-improvement-1 integration proofs — Improvement Initiatives (ISO 10.3, R46) over HTTP against
testcontainer Postgres.

An initiative is an **own-table mutable-state workflow object** (R22/R46), NOT a
``documented_information`` record subtype: the mutable ``stage`` is the headline; the append-only
``improvement_initiative_stage_event`` trail is the immutable history. The two additive (R38)
``improvement.read`` / ``improvement.manage`` keys are seeded in 0052 (PROCESS finest-scope) but the
test actor has no role assignment, so each test grants the keys it needs — usually via SYSTEM-scope
overrides (the ``test_capa`` / ``test_dcr`` precedent; a SYSTEM grant matches any resource context).
The row-filter test (test 4) deliberately grants ONLY a PROCESS-scoped read so the R28 regression is
exercised, not masked by a SYSTEM override (the S-pack-1 lesson). Assertions are scoped to **this
run's own** initiative ids — the integration suite shares one session DB across files, so absolute
counts are never asserted.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from easysynq_api.db.models._audit_enums import AuditObjectType, EventType
from easysynq_api.db.models._clause_enums import PdcaPhase
from easysynq_api.db.models._process_enums import ProcessState
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.authz_grant import PermissionOverride
from easysynq_api.db.models.improvement_initiative_stage_event import (
    ImprovementInitiativeStageEvent,
)
from easysynq_api.db.models.permission import Permission
from easysynq_api.db.models.process import Process
from easysynq_api.db.models.scope import Scope
from easysynq_api.db.models.signature_event import SignatureEvent
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.domain.authz.types import Effect, ScopeLevel

from .test_vault import _auth, _ensure_user

pytestmark = pytest.mark.integration

_IMP_KEYS = ("improvement.read", "improvement.manage")


def _subject(prefix: str) -> str:
    return f"kc-{prefix}-{uuid.uuid4().hex[:10]}"


async def _grant(subject: str, keys: tuple[str, ...]) -> uuid.UUID:
    """Grant the given permission keys at SYSTEM scope via override (the test_capa pattern; a SYSTEM
    grant matches any resource context)."""
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        for key in keys:
            perm = (await s.execute(select(Permission).where(Permission.key == key))).scalar_one()
            scope = Scope(org_id=user.org_id, level=ScopeLevel.SYSTEM)
            s.add(scope)
            await s.flush()
            s.add(
                PermissionOverride(
                    org_id=user.org_id,
                    user_id=user.id,
                    permission_id=perm.id,
                    effect=Effect.ALLOW,
                    scope_id=scope.id,
                )
            )
        await s.commit()
        return user.id


async def _grant_process(subject: str, key: str, process_id: str) -> uuid.UUID:
    """Grant one key at PROCESS scope bound to a concrete process_id (NOT a SYSTEM override) — the
    PDP PROCESS branch matches only when ``selector.process_id ∈ resource.process_ids`` (the
    test_dcr ``_grant_process`` precedent). Exercises the R28 row-filter regression unmasked."""
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        perm = (await s.execute(select(Permission).where(Permission.key == key))).scalar_one()
        scope = Scope(
            org_id=user.org_id, level=ScopeLevel.PROCESS, selector={"process_id": process_id}
        )
        s.add(scope)
        await s.flush()
        s.add(
            PermissionOverride(
                org_id=user.org_id,
                user_id=user.id,
                permission_id=perm.id,
                effect=Effect.ALLOW,
                scope_id=scope.id,
            )
        )
        await s.commit()
        return user.id


async def _seed_process(subject: str) -> str:
    """Insert an ACTIVE Process in the actor's org; return its id (the test_dcr / test_packs
    precedent for a PROCESS-scope authz seam)."""
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        proc = Process(
            org_id=user.org_id,
            name=f"P-{uuid.uuid4().hex[:8]}",
            pdca_phase=PdcaPhase.ACT,
            state=ProcessState.ACTIVE,
            created_by=user.id,
        )
        s.add(proc)
        await s.commit()
        return str(proc.id)


async def _event_count(object_id: str, event_type: EventType) -> int:
    async with get_sessionmaker()() as s:
        return (
            await s.execute(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.object_id == uuid.UUID(object_id),
                    AuditEvent.event_type == event_type,
                )
            )
        ).scalar_one()


async def _create(client: AsyncClient, headers: dict[str, str], **body: object) -> Any:
    payload: dict[str, object] = {"title": "Reduce scrap rate"}
    payload.update(body)
    r = await client.post("/api/v1/improvement-initiatives", headers=headers, json=payload)
    assert r.status_code == 201, r.text
    return r.json()


async def _transition(
    client: AsyncClient, headers: dict[str, str], initiative_id: str, **body: object
) -> Any:
    r = await client.post(
        f"/api/v1/improvement-initiatives/{initiative_id}/transition", headers=headers, json=body
    )
    assert r.status_code == 200, r.text
    return r.json()


# --- 1. Lifecycle happy path ------------------------------------------------------------------


async def test_lifecycle_happy_path_open_to_closed(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A manual initiative raises at Open with an IMP-YYYY-NNNN identifier, then walks the full FSM
    Open→InProgress→Completed→Closed. The stage-events trail is the ordered append-only history
    (genesis from_state=NULL→Open, then one row per move); the Closed move sets closed_at and folds
    its ``outcome`` into the sealed stage_event payload."""
    subject = _subject("imp-happy")
    await _grant(subject, _IMP_KEYS)
    h = _auth(token_factory, subject)

    created = await _create(
        app_client,
        h,
        description="Too much rework on line 3",
        target_outcome="Scrap below 2%",
    )
    initiative_id = str(created["id"])
    assert created["source"] == "manual"
    assert created["stage"] == "Open"
    assert created["closed_at"] is None
    identifier = str(created["identifier"])
    assert identifier.startswith("IMP-"), identifier

    in_progress = await _transition(app_client, h, initiative_id, to_state="InProgress")
    assert in_progress["stage"] == "InProgress"
    assert in_progress["closed_at"] is None

    completed = await _transition(app_client, h, initiative_id, to_state="Completed")
    assert completed["stage"] == "Completed"

    closed = await _transition(
        app_client,
        h,
        initiative_id,
        to_state="Closed",
        comment="Filed — target met",
        outcome="Scrap fell to 1.6% over Q2",
    )
    assert closed["stage"] == "Closed"
    assert closed["closed_at"] is not None

    # The ordered append-only trail: genesis (NULL→Open) then each move, oldest→newest.
    events = (
        await app_client.get(
            f"/api/v1/improvement-initiatives/{initiative_id}/stage-events", headers=h
        )
    ).json()["data"]
    transitions = [(e["from_state"], e["to_state"]) for e in events]
    assert transitions == [
        (None, "Open"),
        ("Open", "InProgress"),
        ("InProgress", "Completed"),
        ("Completed", "Closed"),
    ]
    # The Closed move's outcome is folded into the sealed stage_event payload (the 10.3 evidence).
    assert events[-1]["payload"] == {"outcome": "Scrap fell to 1.6% over Q2"}
    assert events[-1]["comment"] == "Filed — target met"

    # The final detail view reflects the terminal stage + closed_at.
    detail = (
        await app_client.get(f"/api/v1/improvement-initiatives/{initiative_id}", headers=h)
    ).json()
    assert detail["stage"] == "Closed"
    assert detail["closed_at"] is not None

    assert await _event_count(initiative_id, EventType.INITIATIVE_RAISED) == 1
    assert await _event_count(initiative_id, EventType.INITIATIVE_TRANSITIONED) == 3
    # The audit events key on object_type=improvement_initiative (own table), not record.
    async with get_sessionmaker()() as s:
        ev = (
            await s.execute(
                select(AuditEvent).where(
                    AuditEvent.object_id == uuid.UUID(initiative_id),
                    AuditEvent.event_type == EventType.INITIATIVE_RAISED,
                )
            )
        ).scalar_one()
    assert ev.object_type == AuditObjectType.improvement_initiative
    assert ev.scope_ref == identifier  # scope_ref=identifier (the dcr/capa precedent)


# --- 2. FSM 409 -------------------------------------------------------------------------------


async def test_illegal_transition_is_409_and_stage_unchanged(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """An illegal jump (Open→Completed) is a 409 ``improvement_transition_invalid`` and leaves the
    stage untouched; a move out of a terminal state (Closed→anything) is equally rejected."""
    subject = _subject("imp-fsm")
    await _grant(subject, _IMP_KEYS)
    h = _auth(token_factory, subject)
    initiative_id = str((await _create(app_client, h))["id"])

    # Open cannot jump straight to Completed (must go via InProgress).
    bad = await app_client.post(
        f"/api/v1/improvement-initiatives/{initiative_id}/transition",
        headers=h,
        json={"to_state": "Completed"},
    )
    assert bad.status_code == 409, bad.text
    assert bad.json()["code"] == "improvement_transition_invalid"
    # The stage is unchanged.
    detail = (
        await app_client.get(f"/api/v1/improvement-initiatives/{initiative_id}", headers=h)
    ).json()
    assert detail["stage"] == "Open"

    # Drive it to a terminal Closed, then prove a terminal state has no outgoing edge.
    await _transition(app_client, h, initiative_id, to_state="InProgress")
    await _transition(app_client, h, initiative_id, to_state="Completed")
    await _transition(app_client, h, initiative_id, to_state="Closed", comment="done")
    reopen = await app_client.post(
        f"/api/v1/improvement-initiatives/{initiative_id}/transition",
        headers=h,
        json={"to_state": "InProgress"},
    )
    assert reopen.status_code == 409, reopen.text
    assert reopen.json()["code"] == "improvement_transition_invalid"
    assert (
        await app_client.get(f"/api/v1/improvement-initiatives/{initiative_id}", headers=h)
    ).json()["stage"] == "Closed"


# --- 3. Cancel --------------------------------------------------------------------------------


async def test_cancel_from_open_and_in_progress_but_not_completed(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Cancelled is reachable only from the pre-completion states {Open, InProgress} (closed_at
    set); a Completed initiative is filed (Closed), never cancelled — Completed→Cancelled is a
    409."""
    subject = _subject("imp-cancel")
    await _grant(subject, _IMP_KEYS)
    h = _auth(token_factory, subject)

    # Open → Cancelled (closed_at set).
    open_one = str((await _create(app_client, h))["id"])
    cancelled = await _transition(
        app_client, h, open_one, to_state="Cancelled", comment="duplicate of IMP-2026-0001"
    )
    assert cancelled["stage"] == "Cancelled"
    assert cancelled["closed_at"] is not None

    # InProgress → Cancelled (closed_at set).
    in_prog = str((await _create(app_client, h))["id"])
    await _transition(app_client, h, in_prog, to_state="InProgress")
    cancelled2 = await _transition(
        app_client, h, in_prog, to_state="Cancelled", comment="no longer relevant"
    )
    assert cancelled2["stage"] == "Cancelled"
    assert cancelled2["closed_at"] is not None

    # Completed → Cancelled is rejected (past the cancel window).
    completed = str((await _create(app_client, h))["id"])
    await _transition(app_client, h, completed, to_state="InProgress")
    await _transition(app_client, h, completed, to_state="Completed")
    blocked = await app_client.post(
        f"/api/v1/improvement-initiatives/{completed}/transition",
        headers=h,
        json={"to_state": "Cancelled", "comment": "too late"},
    )
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["code"] == "improvement_transition_invalid"
    assert (await app_client.get(f"/api/v1/improvement-initiatives/{completed}", headers=h)).json()[
        "stage"
    ] == "Completed"


# --- 4. PROCESS-scoped row-filter authz (the R28 / S-pack-1 regression) -----------------------


async def test_process_scoped_read_filters_to_granted_process(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """THE key regression test for the just-fixed MAJOR: a caller granted ``improvement.read`` ONLY
    at PROCESS scope for process P (NOT a SYSTEM override — that would MASK the bug, per the
    S-pack-1 R28 lesson) sees ONLY the P initiative in the listing, reads the P initiative (200), is
    denied the Q initiative (403 via the per-resource _initiative_scope gate). The full
    ResourceContext (process_ids) must be populated by the row-filter or a PROCESS-scoped grant
    mis-denies everything.

    The two initiatives are created by a SYSTEM-grant author (the data-setup actor); the assertions
    run as the PROCESS-only reader.
    """
    author_subj = _subject("imp-author")
    await _grant(author_subj, _IMP_KEYS)
    ha = _auth(token_factory, author_subj)

    process_p = await _seed_process(author_subj)
    process_q = await _seed_process(author_subj)
    p_id = str((await _create(app_client, ha, title="In P", process_id=process_p))["id"])
    q_id = str((await _create(app_client, ha, title="In Q", process_id=process_q))["id"])

    # The reader holds ONLY a PROCESS-P improvement.read grant (no SYSTEM override).
    reader_subj = _subject("imp-reader")
    await _grant_process(reader_subj, "improvement.read", process_p)
    hr = _auth(token_factory, reader_subj)

    # The list row-filters to ONLY the P initiative (200, never a hard 403).
    listing = await app_client.get("/api/v1/improvement-initiatives", headers=hr)
    assert listing.status_code == 200, listing.text
    visible_ids = {row["id"] for row in listing.json()["data"]}
    assert p_id in visible_ids
    assert q_id not in visible_ids

    # The single P read is authorized; the Q read is denied (the _initiative_scope PROCESS gate).
    assert (
        await app_client.get(f"/api/v1/improvement-initiatives/{p_id}", headers=hr)
    ).status_code == 200
    denied = await app_client.get(f"/api/v1/improvement-initiatives/{q_id}", headers=hr)
    assert denied.status_code == 403, denied.text


async def test_read_without_grant_lists_empty_not_500(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A caller with NO improvement.read grant gets a calm EMPTY list (filter-not-403, doc 15 §9.3),
    never a 500 — and definitely cannot see other callers' initiatives."""
    # Seed at least one visible initiative (by a granted author) so an empty result is meaningful.
    author_subj = _subject("imp-seed")
    await _grant(author_subj, _IMP_KEYS)
    await _create(app_client, _auth(token_factory, author_subj), title="Some initiative")

    nobody_subj = _subject("imp-nobody")
    async with get_sessionmaker()() as s:
        await _ensure_user(s, nobody_subj)
        await s.commit()
    hn = _auth(token_factory, nobody_subj)
    r = await app_client.get("/api/v1/improvement-initiatives", headers=hn)
    assert r.status_code == 200, r.text
    assert r.json()["data"] == []


async def test_manage_required_for_create_and_transition(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A caller holding only ``improvement.read`` (not ``improvement.manage``) is denied the create
    and the transition (deny-by-default) — the write keys gate at SYSTEM/the initiative's scope."""
    subject = _subject("imp-readonly")
    await _grant(subject, ("improvement.read",))  # read only — no manage
    h = _auth(token_factory, subject)

    blocked_create = await app_client.post(
        "/api/v1/improvement-initiatives", headers=h, json={"title": "Cannot create"}
    )
    assert blocked_create.status_code == 403, blocked_create.text

    # An initiative created by a manager; the read-only caller cannot transition it.
    mgr_subj = _subject("imp-mgr")
    await _grant(mgr_subj, _IMP_KEYS)
    initiative_id = str((await _create(app_client, _auth(token_factory, mgr_subj)))["id"])
    blocked_move = await app_client.post(
        f"/api/v1/improvement-initiatives/{initiative_id}/transition",
        headers=h,
        json={"to_state": "InProgress"},
    )
    assert blocked_move.status_code == 403, blocked_move.text


# --- 5. Append-only REVOKE --------------------------------------------------------------------


async def test_stage_event_is_append_only(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The running app connects as the NON-OWNER easysynq_app role → the
    improvement_initiative_stage_event REVOKE UPDATE,DELETE bites (SQLSTATE 42501). The transition
    trail is structurally immutable, not conventional (the capa_stage / dcr_stage_event
    precedent)."""
    subject = _subject("imp-ao")
    await _grant(subject, _IMP_KEYS)
    h = _auth(token_factory, subject)
    initiative_id = str((await _create(app_client, h))["id"])

    async with get_sessionmaker()() as s:
        ev_id = (
            await s.execute(
                select(ImprovementInitiativeStageEvent.id).where(
                    ImprovementInitiativeStageEvent.initiative_id == uuid.UUID(initiative_id)
                )
            )
        ).scalar_one()
    for stmt in (
        "UPDATE improvement_initiative_stage_event SET to_state = 'Closed' WHERE id = :id",
        "DELETE FROM improvement_initiative_stage_event WHERE id = :id",
    ):
        async with get_sessionmaker()() as s:
            with pytest.raises(DBAPIError) as exc:
                await s.execute(text(stmt), {"id": ev_id})
                await s.commit()
            assert getattr(exc.value.orig, "sqlstate", None) == "42501", stmt


# --- 6. No signature_event (R43 — a recording act mints no signature) -------------------------


async def test_create_and_transitions_mint_no_signature_event(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """R43: an improvement initiative is unsigned in v1.x (clause 10.3 mandates no per-initiative
    sign-off; SignatureMeaning/SignedObjectType stay closed). After a create + several transitions,
    NO signature_event references the initiative or any of its stage-events, and every stage event's
    reserved ``signed_event_id`` hook stays NULL (the build_capa/raise_dcr 'no signature' style)."""
    subject = _subject("imp-nosig")
    await _grant(subject, _IMP_KEYS)
    h = _auth(token_factory, subject)
    initiative_id = str((await _create(app_client, h))["id"])
    await _transition(app_client, h, initiative_id, to_state="InProgress")
    await _transition(app_client, h, initiative_id, to_state="Completed")
    await _transition(
        app_client, h, initiative_id, to_state="Closed", comment="filed", outcome="benefit realized"
    )

    async with get_sessionmaker()() as s:
        stage_event_ids = list(
            (
                await s.execute(
                    select(ImprovementInitiativeStageEvent.id).where(
                        ImprovementInitiativeStageEvent.initiative_id == uuid.UUID(initiative_id)
                    )
                )
            )
            .scalars()
            .all()
        )
        # No signature_event points at the initiative or any of its stage-events as its subject.
        candidate_ids = [uuid.UUID(initiative_id), *stage_event_ids]
        sig_count = (
            await s.execute(
                select(func.count())
                .select_from(SignatureEvent)
                .where(SignatureEvent.signed_object_id.in_(candidate_ids))
            )
        ).scalar_one()
        assert sig_count == 0
        # The reserved per-stage Part-11 hook stays NULL/unsigned in v1.x.
        signed_hooks = (
            await s.execute(
                select(func.count())
                .select_from(ImprovementInitiativeStageEvent)
                .where(
                    ImprovementInitiativeStageEvent.initiative_id == uuid.UUID(initiative_id),
                    ImprovementInitiativeStageEvent.signed_event_id.isnot(None),
                )
            )
        ).scalar_one()
        assert signed_hooks == 0
