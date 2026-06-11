"""S9 integration proofs — the clause spine, the document↔clause mapping, and the submit gate.

Headline: ``test_submit_requires_clause_mapping`` fills the S4 ``# S9:`` seam — a document cannot
enter review until it is mapped to ≥1 ISO clause (doc 15 §8.5 / doc 04 §6.1). Plus: the read-only
clause catalog (GET /clauses), the audited map/unmap round-trip, duplicate-map and cross-framework
rejections, and the two authz gates (clauseMap.read for the spine, document.manage_metadata to map).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from easysynq_api.db.models._audit_enums import EventType
from easysynq_api.db.models._clause_enums import PdcaPhase
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.authz_grant import PermissionOverride
from easysynq_api.db.models.clause import Clause
from easysynq_api.db.models.framework import Framework
from easysynq_api.db.models.organization import Organization
from easysynq_api.db.models.permission import Permission
from easysynq_api.db.models.scope import Scope
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.domain.authz.types import Effect, ScopeLevel

from . import s5_helpers as s5
from .test_vault import (
    _auth,
    _checkin,
    _create,
    _ensure_user,
    _first_clause_id,
    _map_clause,
    _upload,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def subj() -> SimpleNamespace:
    salt = uuid.uuid4().hex[:10]
    return SimpleNamespace(a=f"kc-a-{salt}", b=f"kc-b-{salt}", c=f"kc-c-{salt}")


async def _grant(subject: str, keys: tuple[str, ...]) -> uuid.UUID:
    """Grant an arbitrary permission set at SYSTEM scope via override (mirrors test_vault)."""
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


async def _audit_count(event_type: EventType, object_id: str) -> int:
    async with get_sessionmaker()() as s:
        return (
            await s.execute(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.event_type == event_type,
                    AuditEvent.object_id == uuid.UUID(object_id),
                )
            )
        ).scalar_one()


async def _audit_row(event_type: EventType, object_id: str) -> AuditEvent:
    async with get_sessionmaker()() as s:
        return (
            await s.execute(
                select(AuditEvent)
                .where(
                    AuditEvent.event_type == event_type,
                    AuditEvent.object_id == uuid.UUID(object_id),
                )
                .order_by(AuditEvent.occurred_at.desc())
                .limit(1)
            )
        ).scalar_one()


async def _make_foreign_clause() -> str:
    """A clause under a *different* framework (for the cross-framework rejection)."""
    code = f"isoX-{uuid.uuid4().hex[:8]}:2015"
    async with get_sessionmaker()() as s:
        org_id = (
            await s.execute(select(Organization.id).order_by(Organization.created_at).limit(1))
        ).scalar_one()
        fw = Framework(org_id=org_id, code=code, name="Foreign Standard", is_active=True)
        s.add(fw)
        await s.flush()
        clause = Clause(
            framework_id=fw.id,
            number="1",
            title="Foreign clause",
            intent_text="not iso9001",
            is_mandatory_star=False,
            pdca_phase=PdcaPhase.PLAN,
            requirement_node=True,
        )
        s.add(clause)
        await s.flush()
        cid = str(clause.id)
        await s.commit()
        return cid


async def _to_checked_in_draft(client: AsyncClient, h: dict[str, str]) -> str:
    """create → checkout → upload → checkin (a Draft version, NOT yet mapped or submitted)."""
    did = (await _create(client, h, await s5.type_id("SOP")))["id"]
    await client.post(f"/api/v1/documents/{did}/checkout", headers=h)
    sha = await _upload(client, h, did, f"clause-gate-{did}".encode())
    ci = await _checkin(client, h, did, sha, change_reason="v1", change_significance="MAJOR")
    assert ci.status_code == 201, ci.text
    return did


# --- the headline: the submit-review ≥1-clause_mapping gate (S4 # S9: seam) ---------------


async def test_submit_requires_clause_mapping(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """[S9] Zero clause mappings -> submit 422; mapping one clause lets the T2 submit succeed."""
    await s5.grant_lifecycle(subj.a)
    ha = _auth(token_factory, subj.a)
    did = await _to_checked_in_draft(app_client, ha)

    r = await app_client.post(f"/api/v1/documents/{did}/submit-review", headers=ha)
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["code"] == "validation_error"
    assert body["errors"][0]["field"] == "clause_mappings"

    await _map_clause(app_client, ha, did)
    r2 = await app_client.post(f"/api/v1/documents/{did}/submit-review", headers=ha)
    assert r2.status_code == 200, r2.text
    assert r2.json()["current_state"] == "InReview"


# --- the read-only clause spine ----------------------------------------------------------


async def test_list_clauses_returns_seeded_spine(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await _grant(subj.c, ("clauseMap.read",))
    hc = _auth(token_factory, subj.c)
    r = await app_client.get("/api/v1/clauses", headers=hc)
    assert r.status_code == 200, r.text
    clauses = r.json()
    by_number = {c["number"]: c for c in clauses}
    assert len(clauses) == 83
    # Hierarchy: a top-level clause has no parent; a sub-clause points at its parent.
    assert by_number["4"]["parent_id"] is None
    assert by_number["8.5.6"]["parent_id"] == by_number["8.5"]["id"]
    # ★ flags + PDCA reach the wire.
    assert by_number["4.3"]["is_mandatory_star"] is True
    assert by_number["8.5.6"]["is_mandatory_star"] is True
    assert by_number["4.1"]["is_mandatory_star"] is False
    assert by_number["9.2"]["pdca_phase"] == "CHECK"


async def test_list_clauses_requires_clausemap_read(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await _grant(subj.b, ("document.read",))  # has *a* permission, but not clauseMap.read
    hb = _auth(token_factory, subj.b)
    r = await app_client.get("/api/v1/clauses", headers=hb)
    assert r.status_code == 403, r.text


# --- map / unmap round-trip + audit ------------------------------------------------------


async def test_map_unmap_roundtrip_is_audited(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await s5.grant_lifecycle(subj.a)
    await _grant(subj.a, ("system.audit_log.read",))
    ha = _auth(token_factory, subj.a)
    did = await _to_checked_in_draft(app_client, ha)
    clause_id = await _first_clause_id()

    m = await app_client.post(
        f"/api/v1/documents/{did}/clause-mappings",
        headers=ha,
        json={"clause_id": clause_id, "is_requirement_level": True},
    )
    assert m.status_code == 201, m.text
    assert m.json()["clause_id"] == clause_id
    assert m.json()["is_requirement_level"] is True
    assert await _audit_count(EventType.CLAUSE_MAPPED, did) == 1
    mapped = await _audit_row(EventType.CLAUSE_MAPPED, did)
    assert mapped.after == {
        "clause_id": clause_id,
        "clause_number": m.json()["clause_number"],
        "is_requirement_level": True,
    }
    assert mapped.before is None

    # The per-document trail endpoint must surface the CLAUSE_MAPPED event (scope_ref pin).
    trail = await app_client.get(f"/api/v1/documents/{did}/audit-events", headers=ha)
    assert trail.status_code == 200, trail.text
    trail_types = [e["event_type"] for e in trail.json()["events"]]
    assert "CLAUSE_MAPPED" in trail_types, f"CLAUSE_MAPPED missing from trail: {trail_types}"

    listed = await app_client.get(f"/api/v1/documents/{did}/clause-mappings", headers=ha)
    assert listed.status_code == 200, listed.text
    assert [row["clause_id"] for row in listed.json()] == [clause_id]

    d = await app_client.delete(f"/api/v1/documents/{did}/clause-mappings/{clause_id}", headers=ha)
    assert d.status_code == 204, d.text
    assert await _audit_count(EventType.CLAUSE_UNMAPPED, did) == 1
    unmapped = await _audit_row(EventType.CLAUSE_UNMAPPED, did)
    assert unmapped.before == {"clause_id": clause_id, "clause_number": m.json()["clause_number"]}
    assert unmapped.after is None
    after = await app_client.get(f"/api/v1/documents/{did}/clause-mappings", headers=ha)
    assert after.json() == []


async def test_duplicate_map_conflicts(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await s5.grant_lifecycle(subj.a)
    ha = _auth(token_factory, subj.a)
    did = await _to_checked_in_draft(app_client, ha)
    clause_id = await _first_clause_id()
    first = await app_client.post(
        f"/api/v1/documents/{did}/clause-mappings", headers=ha, json={"clause_id": clause_id}
    )
    assert first.status_code == 201, first.text
    assert first.json()["is_requirement_level"] is False  # the default when omitted
    dup = await app_client.post(
        f"/api/v1/documents/{did}/clause-mappings", headers=ha, json={"clause_id": clause_id}
    )
    assert dup.status_code == 409, dup.text
    assert dup.json()["code"] == "conflict"


async def test_cross_framework_clause_rejected(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await s5.grant_lifecycle(subj.a)
    ha = _auth(token_factory, subj.a)
    did = await _to_checked_in_draft(app_client, ha)
    foreign = await _make_foreign_clause()
    r = await app_client.post(
        f"/api/v1/documents/{did}/clause-mappings", headers=ha, json={"clause_id": foreign}
    )
    assert r.status_code == 422, r.text
    assert r.json()["errors"][0]["code"] == "framework_mismatch"


async def test_map_requires_manage_metadata(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    # a (full lifecycle) creates the doc; b holds only document.read → cannot map.
    await s5.grant_lifecycle(subj.a)
    await _grant(subj.b, ("document.read",))
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    did = await _to_checked_in_draft(app_client, ha)
    clause_id = await _first_clause_id()
    r = await app_client.post(
        f"/api/v1/documents/{did}/clause-mappings", headers=hb, json={"clause_id": clause_id}
    )
    assert r.status_code == 403, r.text


async def test_concurrent_duplicate_map_one_conflicts(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """Two identical concurrent maps → exactly one 201, one 409 (the UNIQUE IntegrityError backstop
    in map_clause_endpoint, past the pre-check)."""
    await s5.grant_lifecycle(subj.a)
    ha = _auth(token_factory, subj.a)
    did = await _to_checked_in_draft(app_client, ha)
    clause_id = await _first_clause_id()
    body = {"clause_id": clause_id}
    r1, r2 = await asyncio.gather(
        app_client.post(f"/api/v1/documents/{did}/clause-mappings", headers=ha, json=body),
        app_client.post(f"/api/v1/documents/{did}/clause-mappings", headers=ha, json=body),
    )
    assert sorted([r1.status_code, r2.status_code]) == [201, 409]
    loser = r1 if r1.status_code == 409 else r2
    assert loser.json()["code"] == "conflict"


async def test_t9_revision_submit_requires_clause_mapping(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """[S9] The ≥1-clause_mapping gate fires on T9 (UnderRevision→InReview) too, and a revision
    inherits the document's mappings (clause_mapping is keyed to the document, not the version)."""
    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    await s5.set_approver_release(await s5.default_org_id(), True)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    doc = await s5.drive_to_effective(
        app_client, ha, hb, hb, await s5.type_id("SOP"), b"t9-clause-gate-v1"
    )
    did = doc["id"]
    # The revision inherits v1's mapping (the document-keyed invariant).
    mapped = await app_client.get(f"/api/v1/documents/{did}/clause-mappings", headers=ha)
    assert len(mapped.json()) == 1
    clause_id = mapped.json()[0]["clause_id"]

    await app_client.post(f"/api/v1/documents/{did}/start-revision", headers=ha)
    sha2 = await _upload(app_client, ha, did, b"t9-clause-gate-v2")
    await _checkin(app_client, ha, did, sha2, change_reason="v2", change_significance="MINOR")

    # Remove the inherited mapping → the T9 submit is gated.
    await app_client.delete(f"/api/v1/documents/{did}/clause-mappings/{clause_id}", headers=ha)
    blocked = await app_client.post(f"/api/v1/documents/{did}/submit-review", headers=ha)
    assert blocked.status_code == 422, blocked.text
    assert blocked.json()["errors"][0]["field"] == "clause_mappings"

    # Re-map → T9 submit succeeds.
    await app_client.post(
        f"/api/v1/documents/{did}/clause-mappings", headers=ha, json={"clause_id": clause_id}
    )
    ok = await app_client.post(f"/api/v1/documents/{did}/submit-review", headers=ha)
    assert ok.status_code == 200, ok.text
    assert ok.json()["current_state"] == "InReview"
