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
from easysynq_api.db.models._iso_audit_enums import AuditState
from easysynq_api.db.models.app_user import AppUser
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.authz_grant import PermissionOverride
from easysynq_api.db.models.permission import Permission
from easysynq_api.db.models.scope import Scope
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.domain.authz.types import Effect, ScopeLevel
from easysynq_api.problems import ProblemException
from easysynq_api.services.audits import advance_audit

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
