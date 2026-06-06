"""S-aud-1 integration proofs — the internal-audit backend (programmes / plans / audits + FSM), over
HTTP against testcontainer Postgres + MinIO + Redis.

The seeded ``audit.*`` keys are held by the Internal Auditor / QMS Owner roles, but the test actor
has no role assignment, so each test grants the keys it needs via SYSTEM-scope overrides (the
``process.create`` / ``test_processes`` precedent; authz itself is proven in S2). Assertions are
scoped to **this run's own** programme / plan / audit ids — the integration suite shares one session
DB across files, so absolute counts are never asserted.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from easysynq_api.db.models._audit_enums import AuditObjectType, EventType
from easysynq_api.db.models._capa_enums import CapaCloseState
from easysynq_api.db.models._iso_audit_enums import AuditState, FindingType
from easysynq_api.db.models.app_user import AppUser
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.authz_grant import PermissionOverride
from easysynq_api.db.models.capa import Capa
from easysynq_api.db.models.permission import Permission
from easysynq_api.db.models.scope import Scope
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.domain.authz.types import Effect, ScopeLevel
from easysynq_api.problems import ProblemException
from easysynq_api.services.audits import advance_audit, create_finding

from .test_vault import _auth, _ensure_user

pytestmark = pytest.mark.integration

_AUDIT_KEYS = ("audit.read", "audit.plan", "audit.create", "audit.conduct", "audit.close")


def _subject(prefix: str) -> str:
    return f"kc-{prefix}-{uuid.uuid4().hex[:10]}"


async def _grant(subject: str, keys: tuple[str, ...]) -> uuid.UUID:
    """Grant the given permission keys at SYSTEM scope via override (the S2/S9c pattern)."""
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


async def _audit_event_count(object_id: str, event_type: EventType) -> int:
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


async def test_audit_lifecycle_round_trip(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("aud")
    await _grant(subject, _AUDIT_KEYS)
    h = _auth(token_factory, subject)

    # Programme → plan → audit.
    r = await app_client.post(
        "/api/v1/audit-programs",
        headers=h,
        json={"title": "2026 Internal Audit Programme", "period": "2026"},
    )
    assert r.status_code == 201, r.text
    program = r.json()
    assert program["identifier"].startswith("AUDPROG-")
    program_id = program["id"]

    r = await app_client.post(
        f"/api/v1/audit-programs/{program_id}/plans",
        headers=h,
        json={"scheduled_date": "2026-09-01"},
    )
    assert r.status_code == 201, r.text
    plan_id = r.json()["id"]

    r = await app_client.post(
        "/api/v1/audits", headers=h, json={"plan_id": plan_id, "title": "Audit of Purchasing"}
    )
    assert r.status_code == 201, r.text
    audit = r.json()
    audit_id = audit["id"]
    assert audit["state"] == "Scheduled"
    assert audit["plan_id"] == plan_id

    # Walk the full FSM in order.
    steps = [
        ("plan", "Planned"),
        ("conduct", "InProgress"),
        ("draft-findings", "FindingsDraft"),
        ("report", "Reported"),
        ("begin-closing", "Closing"),
        ("close", "Closed"),
    ]
    for action, expected in steps:
        r = await app_client.post(f"/api/v1/audits/{audit_id}/{action}", headers=h)
        assert r.status_code == 200, f"{action}: {r.text}"
        assert r.json()["state"] == expected

    # The created + closed events are recorded against the audit's record id.
    assert await _audit_event_count(audit_id, EventType.AUDIT_CREATED) == 1
    assert await _audit_event_count(audit_id, EventType.AUDIT_CLOSED) == 1
    assert (
        await _audit_event_count(audit_id, EventType.AUDIT_TRANSITIONED) == 5
    )  # plan..begin-closing
    # The programme-created event keys on object_type=audit (not record).
    async with get_sessionmaker()() as s:
        ev = (
            await s.execute(
                select(AuditEvent).where(
                    AuditEvent.object_id == uuid.UUID(program_id),
                    AuditEvent.event_type == EventType.AUDIT_PROGRAM_CREATED,
                )
            )
        ).scalar_one()
    assert ev.object_type == AuditObjectType.audit

    # The audit is now Closed and readable.
    r = await app_client.get(f"/api/v1/audits/{audit_id}", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "Closed"
    assert r.json()["completed_at"] is not None


async def test_invalid_transition_is_409(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("aud-inv")
    await _grant(subject, _AUDIT_KEYS)
    h = _auth(token_factory, subject)

    program_id = (
        await app_client.post("/api/v1/audit-programs", headers=h, json={"title": "P"})
    ).json()["id"]
    plan_id = (
        await app_client.post(f"/api/v1/audit-programs/{program_id}/plans", headers=h, json={})
    ).json()["id"]
    audit_id = (
        await app_client.post("/api/v1/audits", headers=h, json={"plan_id": plan_id})
    ).json()["id"]

    # Scheduled → InProgress is illegal (must go through Planned).
    r = await app_client.post(f"/api/v1/audits/{audit_id}/conduct", headers=h)
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "invalid_audit_transition"


async def test_plan_under_archived_program_is_409(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("aud-arc")
    await _grant(subject, _AUDIT_KEYS)
    h = _auth(token_factory, subject)

    program_id = (
        await app_client.post("/api/v1/audit-programs", headers=h, json={"title": "P"})
    ).json()["id"]
    r = await app_client.patch(
        f"/api/v1/audit-programs/{program_id}", headers=h, json={"archived": True}
    )
    assert r.status_code == 200, r.text
    assert r.json()["archived"] is True

    r = await app_client.post(f"/api/v1/audit-programs/{program_id}/plans", headers=h, json={})
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "program_archived"


async def test_create_audit_unknown_plan_is_404(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("aud-404")
    await _grant(subject, _AUDIT_KEYS)
    h = _auth(token_factory, subject)
    r = await app_client.post("/api/v1/audits", headers=h, json={"plan_id": str(uuid.uuid4())})
    assert r.status_code == 404, r.text


async def test_cross_org_advance_is_denied(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The service layer is the authoritative org boundary (the resolver, like _document_scope, does
    not org-check). An actor from another org cannot advance this org's audit — it 404s before any
    state change. Proven at the service layer to avoid leaving a 2nd Org in the shared DB."""
    subject = _subject("aud-xorg")
    await _grant(subject, _AUDIT_KEYS)
    h = _auth(token_factory, subject)
    program_id = (
        await app_client.post("/api/v1/audit-programs", headers=h, json={"title": "P"})
    ).json()["id"]
    plan_id = (
        await app_client.post(f"/api/v1/audit-programs/{program_id}/plans", headers=h, json={})
    ).json()["id"]
    audit_id = uuid.UUID(
        (await app_client.post("/api/v1/audits", headers=h, json={"plan_id": plan_id})).json()["id"]
    )
    # A fabricated actor from a different org (no row needed — the guard 404s before any write).
    intruder = AppUser(id=uuid.uuid4(), org_id=uuid.uuid4(), keycloak_subject="kc-other-org")
    async with get_sessionmaker()() as s:
        with pytest.raises(ProblemException) as exc:
            await advance_audit(s, intruder, audit_id, AuditState.Planned)
    assert exc.value.status == 404
    # The audit is untouched (still Scheduled) for its own org.
    r = await app_client.get(f"/api/v1/audits/{audit_id}", headers=h)
    assert r.json()["state"] == "Scheduled"


async def test_audit_read_requires_grant(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    # A user with no audit.* grant is denied the list (deny-by-default).
    subject = _subject("aud-nogrant")
    async with get_sessionmaker()() as s:
        await _ensure_user(s, subject)
        await s.commit()
    h = _auth(token_factory, subject)
    r = await app_client.get("/api/v1/audit-programs", headers=h)
    assert r.status_code == 403, r.text


# --- S-aud-2: findings + the NC→CAPA auto-link + the close gate --------------------------------

_FINDING_KEYS = (
    *_AUDIT_KEYS,
    "finding.create",
    "finding.read",
    "capa.read",
    "record.create",
)


async def _new_audit(client: AsyncClient, h: dict[str, str]) -> str:
    program_id = (
        await client.post("/api/v1/audit-programs", headers=h, json={"title": "P"})
    ).json()["id"]
    plan_id = (
        await client.post(f"/api/v1/audit-programs/{program_id}/plans", headers=h, json={})
    ).json()["id"]
    return (await client.post("/api/v1/audits", headers=h, json={"plan_id": plan_id})).json()["id"]


async def _walk(client: AsyncClient, h: dict[str, str], audit_id: str, *steps: str) -> None:
    for action in steps:
        r = await client.post(f"/api/v1/audits/{audit_id}/{action}", headers=h)
        assert r.status_code == 200, f"{action}: {r.text}"


async def _set_capa_state(capa_id: str, state: CapaCloseState) -> None:
    """Force a CAPA's close_state directly (the CAPA service only wires Raised→Containment in
    S-capa-1; reaching Closed/Rejected is S-capa-3). The close gate only READS close_state."""
    async with get_sessionmaker()() as s:
        capa = await s.get(Capa, uuid.UUID(capa_id))
        assert capa is not None
        capa.close_state = state
        await s.commit()


async def test_nc_finding_auto_creates_capa(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("find-nc")
    await _grant(subject, _FINDING_KEYS)
    h = _auth(token_factory, subject)
    audit_id = await _new_audit(app_client, h)
    await _walk(app_client, h, audit_id, "plan", "conduct")  # InProgress

    r = await app_client.post(
        f"/api/v1/audits/{audit_id}/findings",
        headers=h,
        json={"finding_type": "NC", "severity": "Major", "clause_ref": "8.4"},
    )
    assert r.status_code == 201, r.text
    f = r.json()
    assert f["finding_type"] == "NC"
    assert f["severity"] == "Major"
    assert f["clause_ref"] == "8.4"
    assert f["identifier"].startswith("REC-")
    assert f["auto_capa_id"] is not None

    # The auto-created CAPA: source=audit, the reverse link set, at Raised.
    capa = (await app_client.get(f"/api/v1/capas/{f['auto_capa_id']}", headers=h)).json()
    assert capa["source"] == "audit"
    assert capa["severity"] == "Major"
    assert capa["close_state"] == "Raised"
    assert capa["origin_finding_id"] == f["id"]

    # Events: the finding-created (object_type=record) + the CAPA_RAISED on the auto-CAPA.
    assert await _audit_event_count(f["id"], EventType.AUDIT_FINDING_CREATED) == 1
    assert await _audit_event_count(f["auto_capa_id"], EventType.CAPA_RAISED) == 1


async def test_observation_finding_creates_no_capa(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("find-obs")
    await _grant(subject, _FINDING_KEYS)
    h = _auth(token_factory, subject)
    audit_id = await _new_audit(app_client, h)
    await _walk(app_client, h, audit_id, "plan", "conduct")
    r = await app_client.post(
        f"/api/v1/audits/{audit_id}/findings", headers=h, json={"finding_type": "OBSERVATION"}
    )
    assert r.status_code == 201, r.text
    assert r.json()["auto_capa_id"] is None
    assert r.json()["severity"] is None


async def test_nc_without_severity_is_422(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("find-422")
    await _grant(subject, _FINDING_KEYS)
    h = _auth(token_factory, subject)
    audit_id = await _new_audit(app_client, h)
    await _walk(app_client, h, audit_id, "plan", "conduct")
    r = await app_client.post(
        f"/api/v1/audits/{audit_id}/findings", headers=h, json={"finding_type": "NC"}
    )
    assert r.status_code == 422, r.text


async def test_close_gate_blocks_until_capa_closed(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("find-gate")
    await _grant(subject, _FINDING_KEYS)
    h = _auth(token_factory, subject)
    audit_id = await _new_audit(app_client, h)
    await _walk(app_client, h, audit_id, "plan", "conduct")
    capa_id = (
        await app_client.post(
            f"/api/v1/audits/{audit_id}/findings",
            headers=h,
            json={"finding_type": "NC", "severity": "Critical"},
        )
    ).json()["auto_capa_id"]
    await _walk(app_client, h, audit_id, "draft-findings", "report", "begin-closing")  # Closing

    # The CAPA is at Raised → the audit cannot close.
    r = await app_client.post(f"/api/v1/audits/{audit_id}/close", headers=h)
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "audit_close_blocked"

    # Close the CAPA → the gate passes.
    await _set_capa_state(capa_id, CapaCloseState.Closed)
    r = await app_client.post(f"/api/v1/audits/{audit_id}/close", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "Closed"


async def test_audit_closes_after_auto_capa_driven_to_closed(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The family headline (S-capa-3): an audit with a live NC cannot close until the NC's auto-CAPA
    is driven to Closed via the REAL S-capa-3 path (containment → RCA → approved plan → implement →
    verify → close), NOT a direct-set. This is the production proof that the S-aud-2
    block-until-corrected gate is satisfiable end-to-end."""
    from .test_capa import (
        _ACTION_PLAN,
        _assign_seeded_role,
        _latest_stage_id,
        _link_stage_evidence,
        _my_pending_task,
    )

    driver_subj = _subject("fam-drv")
    await _grant(
        driver_subj,
        (
            *_FINDING_KEYS,
            "capa.update",
            "capa.record_rca",
            "capa.plan_action",
            "capa.capture_effectiveness",
        ),
    )
    h = _auth(token_factory, driver_subj)
    qm_subj = _subject("fam-qm")
    await _assign_seeded_role(qm_subj, "QMS Owner")
    hqm = _auth(token_factory, qm_subj)
    ver_subj = _subject("fam-ver")
    await _grant(ver_subj, ("capa.read", "capa.verify", "capa.close", "record.create"))
    hver = _auth(token_factory, ver_subj)

    audit_id = await _new_audit(app_client, h)
    await _walk(app_client, h, audit_id, "plan", "conduct")
    capa_id = (
        await app_client.post(
            f"/api/v1/audits/{audit_id}/findings",
            headers=h,
            json={"finding_type": "NC", "severity": "Minor"},
        )
    ).json()["auto_capa_id"]
    await _walk(app_client, h, audit_id, "draft-findings", "report", "begin-closing")

    blocked = await app_client.post(f"/api/v1/audits/{audit_id}/close", headers=h)
    assert blocked.status_code == 409 and blocked.json()["code"] == "audit_close_blocked", (
        blocked.text
    )

    # Drive the auto-CAPA Raised → Closed via the real endpoints (distinct implementer / verifier).
    await app_client.post(
        f"/api/v1/capas/{capa_id}/containment",
        headers=h,
        json={"content_block": {"correction": "contain"}},
    )
    await app_client.post(
        f"/api/v1/capas/{capa_id}/root-cause",
        headers=h,
        json={"content_block": {"root_cause": "rc"}},
    )
    iid = (
        await app_client.post(
            f"/api/v1/capas/{capa_id}/action-plan", headers=h, json={"content_block": _ACTION_PLAN}
        )
    ).json()["approval_instance"]["id"]
    task_id = await _my_pending_task(app_client, hqm, iid)
    await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hqm, json={"outcome": "approve"}
    )
    await app_client.post(
        f"/api/v1/capas/{capa_id}/implement", headers=h, json={"content_block": {"done": "x"}}
    )
    impl_stage = await _latest_stage_id(app_client, h, capa_id, "Implement")
    await _link_stage_evidence(app_client, h, capa_id, impl_stage)
    await app_client.post(
        f"/api/v1/capas/{capa_id}/verify",
        headers=hver,
        json={"decision": "effective", "content_block": {"c": "x"}},
    )
    ver_stage = await _latest_stage_id(app_client, hver, capa_id, "Verify")
    await _link_stage_evidence(app_client, hver, capa_id, ver_stage)
    closed = await app_client.post(f"/api/v1/capas/{capa_id}/close", headers=hver)
    assert closed.status_code == 200 and closed.json()["close_state"] == "Closed", closed.text

    # The live NC now has a Closed CAPA → the audit closes.
    r = await app_client.post(f"/api/v1/audits/{audit_id}/close", headers=h)
    assert r.status_code == 200 and r.json()["state"] == "Closed", r.text


async def test_block_until_corrected_with_rejected_capa(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A Rejected NC-CAPA does NOT satisfy the gate (R39); the auditor must correct the finding
    (NC→OBSERVATION), which supersedes it out of the live-NC set, before the audit can close."""
    subject = _subject("find-corr")
    await _grant(subject, _FINDING_KEYS)
    h = _auth(token_factory, subject)
    audit_id = await _new_audit(app_client, h)
    await _walk(app_client, h, audit_id, "plan", "conduct")
    f = (
        await app_client.post(
            f"/api/v1/audits/{audit_id}/findings",
            headers=h,
            json={"finding_type": "NC", "severity": "Minor"},
        )
    ).json()
    await _set_capa_state(f["auto_capa_id"], CapaCloseState.Rejected)
    await _walk(app_client, h, audit_id, "draft-findings", "report", "begin-closing")

    # Rejected CAPA still blocks.
    r = await app_client.post(f"/api/v1/audits/{audit_id}/close", headers=h)
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "audit_close_blocked"

    # Correct the NC → OBSERVATION (declassify). The original is superseded out of the live set.
    r = await app_client.post(
        f"/api/v1/findings/{f['id']}/correction",
        headers=h,
        json={"finding_type": "OBSERVATION", "reason": "reclassified on review"},
    )
    assert r.status_code == 201, r.text
    successor = r.json()
    assert successor["finding_type"] == "OBSERVATION"
    assert successor["auto_capa_id"] is None
    # The original now points to its successor.
    orig = (await app_client.get(f"/api/v1/findings/{f['id']}", headers=h)).json()
    assert orig["superseded_by_correction"] == successor["id"]

    # Now the audit closes.
    r = await app_client.post(f"/api/v1/audits/{audit_id}/close", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "Closed"


async def test_general_retype_observation_to_nc_spawns_capa(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """General retype (fork A): an Observation can be corrected UP to an NC, which auto-creates its
    mandatory CAPA on the successor."""
    subject = _subject("find-up")
    await _grant(subject, _FINDING_KEYS)
    h = _auth(token_factory, subject)
    audit_id = await _new_audit(app_client, h)
    await _walk(app_client, h, audit_id, "plan", "conduct")
    obs = (
        await app_client.post(
            f"/api/v1/audits/{audit_id}/findings", headers=h, json={"finding_type": "OBSERVATION"}
        )
    ).json()
    assert obs["auto_capa_id"] is None

    r = await app_client.post(
        f"/api/v1/findings/{obs['id']}/correction",
        headers=h,
        json={"finding_type": "NC", "severity": "Major"},
    )
    assert r.status_code == 201, r.text
    successor = r.json()
    assert successor["finding_type"] == "NC"
    assert successor["auto_capa_id"] is not None
    assert successor["correction_of"] == obs["id"]
    capa = (await app_client.get(f"/api/v1/capas/{successor['auto_capa_id']}", headers=h)).json()
    assert capa["origin_finding_id"] == successor["id"]


async def test_finding_on_closed_audit_is_409(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("find-closed")
    await _grant(subject, _FINDING_KEYS)
    h = _auth(token_factory, subject)
    audit_id = await _new_audit(app_client, h)
    # No findings → the close gate passes; walk to Closed.
    await _walk(
        app_client,
        h,
        audit_id,
        "plan",
        "conduct",
        "draft-findings",
        "report",
        "begin-closing",
        "close",
    )
    r = await app_client.post(
        f"/api/v1/audits/{audit_id}/findings",
        headers=h,
        json={"finding_type": "OBSERVATION"},
    )
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "audit_finding_audit_closed"


async def test_double_correction_is_409(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("find-dc")
    await _grant(subject, _FINDING_KEYS)
    h = _auth(token_factory, subject)
    audit_id = await _new_audit(app_client, h)
    await _walk(app_client, h, audit_id, "plan", "conduct")
    f = (
        await app_client.post(
            f"/api/v1/audits/{audit_id}/findings", headers=h, json={"finding_type": "OFI"}
        )
    ).json()
    r1 = await app_client.post(
        f"/api/v1/findings/{f['id']}/correction", headers=h, json={"finding_type": "OBSERVATION"}
    )
    assert r1.status_code == 201, r1.text
    # Correcting the SAME original again is rejected (it is already superseded).
    r2 = await app_client.post(
        f"/api/v1/findings/{f['id']}/correction", headers=h, json={"finding_type": "OFI"}
    )
    assert r2.status_code == 409, r2.text
    assert r2.json()["code"] == "finding_already_corrected"


async def test_evidence_link_to_finding_and_capa_stage(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """S-aud-2 enabled the reserved evidence_for_link FINDING / CAPA_STAGE targets (was 422)."""
    subject = _subject("find-evid")
    await _grant(subject, _FINDING_KEYS)
    h = _auth(token_factory, subject)
    audit_id = await _new_audit(app_client, h)
    await _walk(app_client, h, audit_id, "plan", "conduct")
    f = (
        await app_client.post(
            f"/api/v1/audits/{audit_id}/findings",
            headers=h,
            json={"finding_type": "NC", "severity": "Major"},
        )
    ).json()
    capa_id = f["auto_capa_id"]

    # Link the CAPA record (its id IS a record id) as evidence for the finding.
    r = await app_client.post(
        f"/api/v1/records/{capa_id}/evidence-links",
        headers=h,
        json={"target_type": "finding", "target_id": f["id"]},
    )
    assert r.status_code == 201, r.text

    # Link the finding record as evidence for the CAPA's Raised stage block.
    stage_id = (await app_client.get(f"/api/v1/capas/{capa_id}", headers=h)).json()["stages"][0][
        "id"
    ]
    r = await app_client.post(
        f"/api/v1/records/{f['id']}/evidence-links",
        headers=h,
        json={"target_type": "capa_stage", "target_id": stage_id},
    )
    assert r.status_code == 201, r.text

    # A nonexistent finding target is rejected.
    r = await app_client.post(
        f"/api/v1/records/{capa_id}/evidence-links",
        headers=h,
        json={"target_type": "finding", "target_id": str(uuid.uuid4())},
    )
    assert r.status_code == 422, r.text


async def test_cross_org_create_finding_is_denied(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The service layer is the org boundary (the resolver does not org-check). A cross-org actor
    cannot log a finding against this org's audit (404 before any write)."""
    subject = _subject("find-xorg")
    await _grant(subject, _FINDING_KEYS)
    h = _auth(token_factory, subject)
    audit_id = uuid.UUID(await _new_audit(app_client, h))
    await _walk(app_client, h, str(audit_id), "plan", "conduct")
    intruder = AppUser(id=uuid.uuid4(), org_id=uuid.uuid4(), keycloak_subject="kc-find-other-org")
    async with get_sessionmaker()() as s:
        with pytest.raises(ProblemException) as exc:
            await create_finding(s, intruder, audit_id, finding_type=FindingType.OBSERVATION)
    assert exc.value.status == 404
