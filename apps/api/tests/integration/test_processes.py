"""S9c integration proofs — the process IA backend (graph + authoring + process-links).

Covers: process create (SEED) + dup-name (409, incl. the concurrent race) + the create-time
validations (parent/owner/supplier existence; outsourced-needs-supplier); the SEED→ACTIVE state
machine (the one-way ratchet; ACTIVE→SEED 409); the edge sub-resource (add / self-loop 409 / dup 409
/ missing-target 422 / delete); the reads (list / detail / map) + the read-permission gate; and the
M:N document↔process ``process_link`` (the clause-mappings shape). The **deferral proof**
(``test_seeded_process_scope_grant_cannot_patch``) documents that the seeded PROCESS grant's
``:assignment_process`` placeholder matches no concrete process yet — S9c authoring rides on SYSTEM
overrides; concrete per-process authoring lands with owner-assignment.

``process.create``/``process.manage`` are seeded but held by no role, so the actor is granted them
via SYSTEM-scope overrides (the ``document.export`` / ``test_export_print`` precedent).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from easysynq_api.db.models._audit_enums import AuditObjectType, EventType
from easysynq_api.db.models._process_enums import SupplierStatus
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.authz_grant import PermissionOverride
from easysynq_api.db.models.org_role import OrgRole
from easysynq_api.db.models.permission import Permission
from easysynq_api.db.models.scope import Scope
from easysynq_api.db.models.supplier import Supplier
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.domain.authz.types import Effect, ScopeLevel

from . import s5_helpers as s5
from .test_vault import _auth, _create, _ensure_user

pytestmark = pytest.mark.integration


@pytest.fixture
def subj() -> SimpleNamespace:
    salt = uuid.uuid4().hex[:10]
    return SimpleNamespace(a=f"kc-pa-{salt}", b=f"kc-pb-{salt}", c=f"kc-pc-{salt}")


async def _grant(
    subject: str, key: str, *, level: str = "SYSTEM", selector: dict | None = None
) -> uuid.UUID:
    """Grant a permission key via an override at the given scope (SYSTEM by default)."""
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        perm = (await s.execute(select(Permission).where(Permission.key == key))).scalar_one()
        scope = Scope(org_id=user.org_id, level=ScopeLevel(level), selector=selector)
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


async def _grant_authoring(subject: str) -> uuid.UUID:
    """The S9c authoring set at SYSTEM (create + manage + read)."""
    uid = await _grant(subject, "process.create")
    await _grant(subject, "process.manage")
    await _grant(subject, "process.read")
    return uid


async def _seed_org_role(created_by: uuid.UUID) -> str:
    org_id = await s5.default_org_id()
    async with get_sessionmaker()() as s:
        role = OrgRole(org_id=org_id, name=f"Owner-{uuid.uuid4().hex[:8]}", created_by=created_by)
        s.add(role)
        await s.flush()
        rid = str(role.id)
        await s.commit()
        return rid


async def _seed_supplier(created_by: uuid.UUID) -> str:
    org_id = await s5.default_org_id()
    async with get_sessionmaker()() as s:
        sup = Supplier(
            org_id=org_id,
            name=f"Acme-{uuid.uuid4().hex[:8]}",
            status=SupplierStatus.ACTIVE,
            created_by=created_by,
        )
        s.add(sup)
        await s.flush()
        sid = str(sup.id)
        await s.commit()
        return sid


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


async def _create_process(
    client: AsyncClient, h: dict[str, str], name: str | None = None, **extra: object
) -> dict:
    body = {"name": name or f"Proc-{uuid.uuid4().hex[:10]}", "pdca_phase": "DO", **extra}
    r = await client.post("/api/v1/processes", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()


# --- create + validations ---------------------------------------------------------------


async def test_create_process_seed_and_audited(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await _grant_authoring(subj.a)
    h = _auth(token_factory, subj.a)
    proc = await _create_process(app_client, h, criteria="on-time delivery")
    assert proc["state"] == "SEED"
    assert proc["pdca_phase"] == "DO"
    assert await _audit_count(EventType.PROCESS_CREATED, proc["id"]) == 1
    row = await _audit_row(EventType.PROCESS_CREATED, proc["id"])
    assert row.object_type == AuditObjectType.process
    assert row.after == {"name": proc["name"]}


async def test_create_dup_name_409(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await _grant_authoring(subj.a)
    h = _auth(token_factory, subj.a)
    name = f"Unique-{uuid.uuid4().hex[:10]}"
    await _create_process(app_client, h, name)
    r = await app_client.post(
        "/api/v1/processes", headers=h, json={"name": name, "pdca_phase": "DO"}
    )
    assert r.status_code == 409, r.text


async def test_create_concurrent_dup_one_wins(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await _grant_authoring(subj.a)
    h = _auth(token_factory, subj.a)
    name = f"Race-{uuid.uuid4().hex[:10]}"
    body = {"name": name, "pdca_phase": "PLAN"}
    r1, r2 = await asyncio.gather(
        app_client.post("/api/v1/processes", headers=h, json=body),
        app_client.post("/api/v1/processes", headers=h, json=body),
        return_exceptions=True,
    )
    codes = sorted(r.status_code for r in (r1, r2) if not isinstance(r, BaseException))
    assert codes == [201, 409], (codes, r1, r2)


async def test_create_validations(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    uid = await _grant_authoring(subj.a)
    h = _auth(token_factory, subj.a)
    missing = str(uuid.uuid4())
    for field, payload in (
        ("parent_id", {"parent_id": missing}),
        ("owner_org_role_id", {"owner_org_role_id": missing}),
        ("outsourced_supplier_id", {"outsourced_supplier_id": missing}),
    ):
        r = await app_client.post(
            "/api/v1/processes",
            headers=h,
            json={"name": f"V-{uuid.uuid4().hex[:8]}", "pdca_phase": "DO", **payload},
        )
        assert r.status_code == 422, (field, r.text)
        assert r.json()["errors"][0]["field"] == field
    # is_outsourced without a supplier → 422.
    r = await app_client.post(
        "/api/v1/processes",
        headers=h,
        json={"name": f"O-{uuid.uuid4().hex[:8]}", "pdca_phase": "DO", "is_outsourced": True},
    )
    assert r.status_code == 422, r.text
    # A valid owner + supplier (seeded directly) → 201.
    role_id = await _seed_org_role(uid)
    supplier_id = await _seed_supplier(uid)
    proc = await _create_process(
        app_client,
        h,
        owner_org_role_id=role_id,
        is_outsourced=True,
        outsourced_supplier_id=supplier_id,
    )
    assert proc["owner_org_role_id"] == role_id
    assert proc["is_outsourced"] is True
    assert proc["outsourced_supplier_id"] == supplier_id


# --- the SEED→ACTIVE state machine ------------------------------------------------------


async def test_patch_metadata_emits_updated(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await _grant_authoring(subj.a)
    h = _auth(token_factory, subj.a)
    proc = await _create_process(app_client, h)
    r = await app_client.patch(
        f"/api/v1/processes/{proc['id']}", headers=h, json={"criteria": "revised"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["criteria"] == "revised"
    assert await _audit_count(EventType.PROCESS_UPDATED, proc["id"]) == 1
    assert await _audit_count(EventType.PROCESS_STATE_CHANGED, proc["id"]) == 0


async def test_patch_seed_to_active(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await _grant_authoring(subj.a)
    h = _auth(token_factory, subj.a)
    proc = await _create_process(app_client, h)
    r = await app_client.patch(
        f"/api/v1/processes/{proc['id']}", headers=h, json={"state": "ACTIVE"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "ACTIVE"
    row = await _audit_row(EventType.PROCESS_STATE_CHANGED, proc["id"])
    assert row.before["state"] == "SEED" and row.after["state"] == "ACTIVE"


async def test_patch_active_to_seed_rejected(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await _grant_authoring(subj.a)
    h = _auth(token_factory, subj.a)
    proc = await _create_process(app_client, h)
    await app_client.patch(f"/api/v1/processes/{proc['id']}", headers=h, json={"state": "ACTIVE"})
    r = await app_client.patch(f"/api/v1/processes/{proc['id']}", headers=h, json={"state": "SEED"})
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "invalid_state_transition"


async def test_patch_unknown_process_404(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await _grant_authoring(subj.a)
    h = _auth(token_factory, subj.a)
    r = await app_client.patch(
        f"/api/v1/processes/{uuid.uuid4()}", headers=h, json={"criteria": "x"}
    )
    assert r.status_code == 404, r.text


# --- edges ------------------------------------------------------------------------------


async def test_edge_add_self_loop_dup_delete(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await _grant_authoring(subj.a)
    h = _auth(token_factory, subj.a)
    a = await _create_process(app_client, h)
    b = await _create_process(app_client, h)

    r = await app_client.post(
        f"/api/v1/processes/{a['id']}/edges",
        headers=h,
        json={"to_process_id": b["id"], "io_label": "feeds"},
    )
    assert r.status_code == 201, r.text
    edge_id = r.json()["id"]
    assert await _audit_count(EventType.PROCESS_EDGE_ADDED, a["id"]) == 1

    # self-loop → 409
    r = await app_client.post(
        f"/api/v1/processes/{a['id']}/edges", headers=h, json={"to_process_id": a["id"]}
    )
    assert r.status_code == 409, r.text
    # duplicate ordered pair → 409
    r = await app_client.post(
        f"/api/v1/processes/{a['id']}/edges", headers=h, json={"to_process_id": b["id"]}
    )
    assert r.status_code == 409, r.text
    # missing target → 422
    r = await app_client.post(
        f"/api/v1/processes/{a['id']}/edges", headers=h, json={"to_process_id": str(uuid.uuid4())}
    )
    assert r.status_code == 422, r.text

    # delete → 204 + PROCESS_EDGE_REMOVED; deleting again → 404
    r = await app_client.delete(f"/api/v1/processes/{a['id']}/edges/{edge_id}", headers=h)
    assert r.status_code == 204, r.text
    assert await _audit_count(EventType.PROCESS_EDGE_REMOVED, a["id"]) == 1
    r = await app_client.delete(f"/api/v1/processes/{a['id']}/edges/{edge_id}", headers=h)
    assert r.status_code == 404, r.text


# --- reads + authz ----------------------------------------------------------------------


async def test_reads_list_detail_map(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await _grant_authoring(subj.a)
    h = _auth(token_factory, subj.a)
    a = await _create_process(app_client, h)
    b = await _create_process(app_client, h)
    await app_client.post(
        f"/api/v1/processes/{a['id']}/edges", headers=h, json={"to_process_id": b["id"]}
    )

    listed = await app_client.get("/api/v1/processes", headers=h)
    assert listed.status_code == 200
    ids = {p["id"] for p in listed.json()}
    assert {a["id"], b["id"]} <= ids

    detail = await app_client.get(f"/api/v1/processes/{a['id']}", headers=h)
    assert detail.status_code == 200 and detail.json()["id"] == a["id"]

    mp = await app_client.get("/api/v1/processes/map", headers=h)
    assert mp.status_code == 200
    body = mp.json()
    assert {a["id"], b["id"]} <= {n["id"] for n in body["nodes"]}
    assert any(
        e["from_process_id"] == a["id"] and e["to_process_id"] == b["id"] for e in body["edges"]
    )


async def test_read_requires_permission(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    # subj.b is a fresh JIT user with no grants → no process.read.
    h = _auth(token_factory, subj.b)
    r = await app_client.get("/api/v1/processes", headers=h)
    assert r.status_code == 403, r.text


async def test_create_requires_permission(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await _grant(subj.b, "process.read")  # read but not create
    h = _auth(token_factory, subj.b)
    r = await app_client.post(
        "/api/v1/processes", headers=h, json={"name": "nope", "pdca_phase": "DO"}
    )
    assert r.status_code == 403, r.text


async def test_seeded_process_scope_grant_cannot_patch(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """The deferral proof: a PROCESS-scoped process.manage grant carrying the seeded
    ``:assignment_process`` placeholder (never substituted in S9c) matches no concrete process, so a
    PATCH is denied — concrete authoring waits on owner-assignment. A second actor with a PROCESS
    override bound to the REAL process id PATCHes it fine (proves _process_scope works)."""
    await _grant_authoring(subj.a)
    ha = _auth(token_factory, subj.a)
    proc = await _create_process(app_client, ha)

    # subj.c: only the placeholder PROCESS grant → denied.
    await _grant(
        subj.c, "process.manage", level="PROCESS", selector={"process_id": ":assignment_process"}
    )
    hc = _auth(token_factory, subj.c)
    denied = await app_client.patch(
        f"/api/v1/processes/{proc['id']}", headers=hc, json={"criteria": "x"}
    )
    assert denied.status_code == 403, denied.text

    # subj.b: a PROCESS grant bound to the real process id → allowed.
    await _grant(subj.b, "process.manage", level="PROCESS", selector={"process_id": proc["id"]})
    hb = _auth(token_factory, subj.b)
    allowed = await app_client.patch(
        f"/api/v1/processes/{proc['id']}", headers=hb, json={"criteria": "ok"}
    )
    assert allowed.status_code == 200, allowed.text


# --- process-links (the M:N document↔process join) --------------------------------------


async def _doc_id(client: AsyncClient, h: dict[str, str]) -> str:
    return (await _create(client, h, await s5.type_id("SOP")))["id"]


async def test_process_link_map_unmap_audited(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    # grant_lifecycle gives document.create/manage_metadata/read; + process.create for the process.
    await s5.grant_lifecycle(subj.a)
    await _grant(subj.a, "process.create")
    h = _auth(token_factory, subj.a)
    did = await _doc_id(app_client, h)
    proc = await _create_process(app_client, h)

    r = await app_client.post(
        f"/api/v1/documents/{did}/process-links", headers=h, json={"process_id": proc["id"]}
    )
    assert r.status_code == 201, r.text
    assert r.json()["process_name"] == proc["name"]
    row = await _audit_row(EventType.PROCESS_LINKED, did)
    assert row.object_type == AuditObjectType.document and str(row.object_id) == did

    listed = await app_client.get(f"/api/v1/documents/{did}/process-links", headers=h)
    assert listed.status_code == 200 and {p["process_id"] for p in listed.json()} == {proc["id"]}

    # duplicate link → 409
    dup = await app_client.post(
        f"/api/v1/documents/{did}/process-links", headers=h, json={"process_id": proc["id"]}
    )
    assert dup.status_code == 409, dup.text

    # unlink → 204 + PROCESS_UNLINKED
    r = await app_client.delete(f"/api/v1/documents/{did}/process-links/{proc['id']}", headers=h)
    assert r.status_code == 204, r.text
    assert await _audit_count(EventType.PROCESS_UNLINKED, did) == 1


async def test_process_link_unknown_process_422(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await s5.grant_lifecycle(subj.a)
    h = _auth(token_factory, subj.a)
    did = await _doc_id(app_client, h)
    r = await app_client.post(
        f"/api/v1/documents/{did}/process-links", headers=h, json={"process_id": str(uuid.uuid4())}
    )
    assert r.status_code == 422, r.text


async def test_process_link_requires_manage_metadata(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    # author a makes the doc + process; actor c (only document.read) can't link.
    await s5.grant_lifecycle(subj.a)
    await _grant(subj.a, "process.create")
    ha = _auth(token_factory, subj.a)
    did = await _doc_id(app_client, ha)
    proc = await _create_process(app_client, ha)

    await _grant(subj.c, "document.read")
    hc = _auth(token_factory, subj.c)
    r = await app_client.post(
        f"/api/v1/documents/{did}/process-links", headers=hc, json={"process_id": proc["id"]}
    )
    assert r.status_code == 403, r.text
