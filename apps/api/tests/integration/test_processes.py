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
from sqlalchemy import delete, func, select

from easysynq_api.db.models._audit_enums import AuditObjectType, EventType
from easysynq_api.db.models._clause_enums import PdcaPhase
from easysynq_api.db.models._process_enums import SupplierStatus
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.authz_grant import PermissionOverride
from easysynq_api.db.models.org_role import OrgRole
from easysynq_api.db.models.organization import Organization
from easysynq_api.db.models.permission import Permission
from easysynq_api.db.models.process import Process
from easysynq_api.db.models.role import Role, RoleAssignment
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


async def test_patch_active_metadata_emits_updated(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """A confirmed (ACTIVE) process is still editable — metadata PATCH → PROCESS_UPDATED, not a
    state event, and the state stays ACTIVE."""
    await _grant_authoring(subj.a)
    h = _auth(token_factory, subj.a)
    proc = await _create_process(app_client, h)
    await app_client.patch(f"/api/v1/processes/{proc['id']}", headers=h, json={"state": "ACTIVE"})
    r = await app_client.patch(
        f"/api/v1/processes/{proc['id']}", headers=h, json={"criteria": "post-active"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["criteria"] == "post-active" and r.json()["state"] == "ACTIVE"
    assert await _audit_count(EventType.PROCESS_UPDATED, proc["id"]) == 1


async def test_patch_null_on_required_field_422(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """An explicit null on a non-nullable column (name) is a 422, not a 500 on flush."""
    await _grant_authoring(subj.a)
    h = _auth(token_factory, subj.a)
    proc = await _create_process(app_client, h)
    r = await app_client.patch(f"/api/v1/processes/{proc['id']}", headers=h, json={"name": None})
    assert r.status_code == 422, r.text
    assert r.json()["errors"][0]["field"] == "name"


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

    # A SYSTEM process.read holder still gets 404 (not 403) on a nonexistent id — the detail
    # endpoint's _read_scoped move stays byte-identical for SYSTEM callers (S-process-scope-2).
    missing = await app_client.get(f"/api/v1/processes/{uuid.uuid4()}", headers=h)
    assert missing.status_code == 404, missing.text

    mp = await app_client.get("/api/v1/processes/map", headers=h)
    assert mp.status_code == 200
    body = mp.json()
    assert {a["id"], b["id"]} <= {n["id"] for n in body["nodes"]}
    assert any(
        e["from_process_id"] == a["id"] and e["to_process_id"] == b["id"] for e in body["edges"]
    )


async def test_list_filters_not_403_for_no_grant(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """A list surface FILTERS, never 403s (doc 18 §5.2, S-process-scope-2): a no-grant caller gets
    200 + an empty list (and empty map), disclosing nothing. The single-resource DETAIL still 403s
    (authz-before-existence)."""
    # subj.b is a fresh JIT user with no grants → no process.read.
    h = _auth(token_factory, subj.b)
    listed = await app_client.get("/api/v1/processes", headers=h)
    assert listed.status_code == 200, listed.text
    assert listed.json() == []
    mp = await app_client.get("/api/v1/processes/map", headers=h)
    assert mp.status_code == 200, mp.text
    assert mp.json() == {"nodes": [], "edges": []}
    # The detail of an arbitrary process still 403s (single-resource, not a list).
    detail = await app_client.get(f"/api/v1/processes/{uuid.uuid4()}", headers=h)
    assert detail.status_code == 403, detail.text


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


# --- owner-assignment (S-owner-assignment-1) --------------------------------------------


async def _user_id(subject: str) -> uuid.UUID:
    """The app_user.id for a kc subject (JIT-create the row so the assign endpoint resolves it)."""
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        await s.flush()
        uid = user.id
        await s.commit()
        return uid


async def _po_role_assignments(user_id: uuid.UUID) -> list[RoleAssignment]:
    async with get_sessionmaker()() as s:
        org_id = await s5.default_org_id()
        role = (
            await s.execute(select(Role).where(Role.org_id == org_id, Role.name == "Process Owner"))
        ).scalar_one()
        return list(
            (
                await s.execute(
                    select(RoleAssignment).where(
                        RoleAssignment.user_id == user_id, RoleAssignment.role_id == role.id
                    )
                )
            )
            .scalars()
            .all()
        )


async def _owner_role_process_ids(user_id: uuid.UUID) -> list[str] | None:
    """The concrete process_ids on the user's owner-assignment-managed 'Process Owner'
    role_assignment (the row carrying the ``managed_by`` marker), or None when none exists (the
    last-process drop). Filtering on the marker ignores any admin-granted SYSTEM/PROCESS row."""
    for ra in await _po_role_assignments(user_id):
        bs = ra.bound_scope or {}
        if bs.get("managed_by") == "owner_assignment":
            return sorted(bs.get("selector", {}).get("process_ids", []))
    return None


async def _assign_role_bound(subject: str, role_name: str, bound_scope: dict) -> uuid.UUID:
    """Directly create a role_assignment with a caller-chosen bound_scope (no managed_by marker) —
    the admin/CLI path, used to prove owner-assignment never touches a non-managed grant."""
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        role = (
            await s.execute(select(Role).where(Role.org_id == user.org_id, Role.name == role_name))
        ).scalar_one()
        s.add(
            RoleAssignment(
                org_id=user.org_id, user_id=user.id, role_id=role.id, bound_scope=bound_scope
            )
        )
        uid = user.id
        await s.commit()
        return uid


async def _link_doc_to_process(
    client: AsyncClient, h: dict[str, str], did: str, process_id: str
) -> None:
    r = await client.post(
        f"/api/v1/documents/{did}/process-links", headers=h, json={"process_id": process_id}
    )
    assert r.status_code == 201, r.text


async def test_assign_owner_mints_working_process_grant(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """Assigning a process owner mints a concrete PROCESS-scoped 'Process Owner' grant that
    authorizes document.read on a process-linked doc — flipping a previously-403 read to 200 with
    NO SYSTEM override. Proves both halves of the slice: the binding mint AND the _document_scope
    process_ids migration."""
    await s5.grant_lifecycle(subj.a)
    await _grant(subj.a, "process.create")
    await _grant(subj.a, "process.assign_owner")
    ha = _auth(token_factory, subj.a)
    did = await _doc_id(app_client, ha)
    proc = await _create_process(app_client, ha)
    await _link_doc_to_process(app_client, ha, did, proc["id"])

    # subj.b: the owner-to-be — a fresh user with no grants. Before binding: 403 on the linked doc.
    owner_id = await _user_id(subj.b)
    hb = _auth(token_factory, subj.b)
    before = await app_client.get(f"/api/v1/documents/{did}", headers=hb)
    assert before.status_code == 403, before.text

    r = await app_client.post(
        f"/api/v1/processes/{proc['id']}/owner", headers=ha, json={"user_id": str(owner_id)}
    )
    assert r.status_code == 201, r.text
    assert r.json()["bound_scope"]["selector"]["process_ids"] == [proc["id"]]

    # After binding: subj.b can read the process-linked doc via the bound PROCESS grant.
    after = await app_client.get(f"/api/v1/documents/{did}", headers=hb)
    assert after.status_code == 200, after.text

    # A doc the owner's process is NOT linked to stays 403 (the bound scope is narrow, AZ-INV-8).
    other = await _doc_id(app_client, ha)
    other_read = await app_client.get(f"/api/v1/documents/{other}", headers=hb)
    assert other_read.status_code == 403, other_read.text


async def test_assign_owner_records_raci_and_unions_processes(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """The RACI org_role_assignment row lands + is audited; a second process unions into the SAME
    role_assignment's process_ids set (not a second assignment row); re-assign is idempotent."""
    await _grant(subj.a, "process.create")
    await _grant(subj.a, "process.assign_owner")
    await _grant(subj.a, "process.read")  # GET /owners is gated process.read (the roster lens)
    ha = _auth(token_factory, subj.a)
    p1 = await _create_process(app_client, ha)
    p2 = await _create_process(app_client, ha)
    owner_id = await _user_id(subj.b)

    r1 = await app_client.post(
        f"/api/v1/processes/{p1['id']}/owner", headers=ha, json={"user_id": str(owner_id)}
    )
    assert r1.status_code == 201, r1.text
    assert r1.json()["bound_scope"]["selector"]["process_ids"] == [p1["id"]]
    assert await _audit_count(EventType.PROCESS_OWNER_ASSIGNED, p1["id"]) >= 1
    row = await _audit_row(EventType.PROCESS_OWNER_ASSIGNED, p1["id"])
    assert row.object_type == AuditObjectType.process

    owners = await app_client.get(f"/api/v1/processes/{p1['id']}/owners", headers=ha)
    assert owners.status_code == 200, owners.text
    listed = owners.json()
    assert any(o["user_id"] == str(owner_id) for o in listed)
    assert all(o["org_role_name"] == "Process Owner" for o in listed)

    # A second process unions into the SAME role_assignment (one growing row, not two).
    r2 = await app_client.post(
        f"/api/v1/processes/{p2['id']}/owner", headers=ha, json={"user_id": str(owner_id)}
    )
    assert r2.status_code == 201, r2.text
    assert sorted(r2.json()["bound_scope"]["selector"]["process_ids"]) == sorted(
        [p1["id"], p2["id"]]
    )
    assert r2.json()["role_assignment_id"] == r1.json()["role_assignment_id"]
    assert await _owner_role_process_ids(owner_id) == sorted([p1["id"], p2["id"]])

    # Re-assigning p1 is idempotent — no duplicate process id, same set, AND no phantom audit row
    # (a fully no-op retry writes no PROCESS_OWNER_ASSIGNED event — the Codex finding).
    assert await _audit_count(EventType.PROCESS_OWNER_ASSIGNED, p1["id"]) == 1
    r3 = await app_client.post(
        f"/api/v1/processes/{p1['id']}/owner", headers=ha, json={"user_id": str(owner_id)}
    )
    assert r3.status_code == 201, r3.text
    assert await _owner_role_process_ids(owner_id) == sorted([p1["id"], p2["id"]])
    assert await _audit_count(EventType.PROCESS_OWNER_ASSIGNED, p1["id"]) == 1


async def test_revoke_owner_narrows_then_drops(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """Revoking one of two owned processes narrows the bound_scope to the remaining process;
    revoking the last drops the role_assignment entirely; a re-revoke of a non-owner is 404."""
    await _grant(subj.a, "process.create")
    await _grant(subj.a, "process.assign_owner")
    await _grant(subj.a, "process.read")  # GET /owners is gated process.read (the roster lens)
    ha = _auth(token_factory, subj.a)
    p1 = await _create_process(app_client, ha)
    p2 = await _create_process(app_client, ha)
    owner_id = await _user_id(subj.b)
    await app_client.post(
        f"/api/v1/processes/{p1['id']}/owner", headers=ha, json={"user_id": str(owner_id)}
    )
    await app_client.post(
        f"/api/v1/processes/{p2['id']}/owner", headers=ha, json={"user_id": str(owner_id)}
    )

    r = await app_client.delete(f"/api/v1/processes/{p1['id']}/owner/{owner_id}", headers=ha)
    assert r.status_code == 204, r.text
    assert await _owner_role_process_ids(owner_id) == [p2["id"]]
    assert await _audit_count(EventType.PROCESS_OWNER_REVOKED, p1["id"]) >= 1
    owners1 = await app_client.get(f"/api/v1/processes/{p1['id']}/owners", headers=ha)
    assert owners1.status_code == 200, owners1.text
    assert all(o["user_id"] != str(owner_id) for o in owners1.json())

    # Revoking the last owned process drops the role_assignment (no inert empty PROCESS grant).
    r2 = await app_client.delete(f"/api/v1/processes/{p2['id']}/owner/{owner_id}", headers=ha)
    assert r2.status_code == 204, r2.text
    assert await _owner_role_process_ids(owner_id) is None

    # Re-revoking a non-owner → 404.
    r3 = await app_client.delete(f"/api/v1/processes/{p2['id']}/owner/{owner_id}", headers=ha)
    assert r3.status_code == 404, r3.text


async def test_assign_owner_requires_permission(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """process.assign_owner gates the bind — a caller with only process.read is denied (403)."""
    await _grant(subj.a, "process.create")
    ha = _auth(token_factory, subj.a)
    proc = await _create_process(app_client, ha)
    owner_id = await _user_id(subj.b)

    await _grant(subj.c, "process.read")
    hc = _auth(token_factory, subj.c)
    r = await app_client.post(
        f"/api/v1/processes/{proc['id']}/owner", headers=hc, json={"user_id": str(owner_id)}
    )
    assert r.status_code == 403, r.text


async def test_assign_owner_unknown_user_404(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await _grant(subj.a, "process.create")
    await _grant(subj.a, "process.assign_owner")
    ha = _auth(token_factory, subj.a)
    proc = await _create_process(app_client, ha)
    r = await app_client.post(
        f"/api/v1/processes/{proc['id']}/owner",
        headers=ha,
        json={"user_id": str(uuid.uuid4())},
    )
    assert r.status_code == 404, r.text


async def test_owner_assignment_does_not_clobber_admin_system_grant(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """An admin's separately-granted SYSTEM-scoped 'Process Owner' role_assignment survives an owner
    assign + last-process revoke — owner-assignment only ever manages its own PROCESS-scoped row, so
    it never clobbers (assign) nor deletes (revoke) the admin grant (the diff-critic finding)."""
    await _grant(subj.a, "process.create")
    await _grant(subj.a, "process.assign_owner")
    ha = _auth(token_factory, subj.a)
    proc = await _create_process(app_client, ha)
    owner_id = await _user_id(subj.b)

    # An admin grants the Process Owner PERMISSION-role org-wide (a SYSTEM bound_scope).
    await s5.grant_role(subj.b, "Process Owner")
    before = await _po_role_assignments(owner_id)
    assert len(before) == 1
    assert (before[0].bound_scope or {}).get("level") == "SYSTEM"

    # Owner-assign creates a SEPARATE PROCESS-scoped row; the SYSTEM grant is untouched.
    r = await app_client.post(
        f"/api/v1/processes/{proc['id']}/owner", headers=ha, json={"user_id": str(owner_id)}
    )
    assert r.status_code == 201, r.text
    levels = sorted(
        (ra.bound_scope or {}).get("level") for ra in await _po_role_assignments(owner_id)
    )
    assert levels == ["PROCESS", "SYSTEM"]
    assert await _owner_role_process_ids(owner_id) == [proc["id"]]

    # Revoking the last owned process drops ONLY the PROCESS row; the admin SYSTEM grant survives.
    rev = await app_client.delete(f"/api/v1/processes/{proc['id']}/owner/{owner_id}", headers=ha)
    assert rev.status_code == 204, rev.text
    remaining = await _po_role_assignments(owner_id)
    assert len(remaining) == 1
    assert (remaining[0].bound_scope or {}).get("level") == "SYSTEM"
    assert await _owner_role_process_ids(owner_id) is None


async def test_owner_assignment_does_not_confer_workflow_candidacy(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """An owner-assignment mint confers the PROCESS permission set but NOT org-wide workflow
    candidacy — users_with_roles excludes the managed_by-marked assignment, so a per-process owner
    is not flooded into every 'Process Owner'-named stage (the Codex P1). A deliberate org-wide
    'Process Owner' role assignment (no marker) stays a candidate."""
    from easysynq_api.services.workflow import repository as wf_repo

    await _grant(subj.a, "process.create")
    await _grant(subj.a, "process.assign_owner")
    ha = _auth(token_factory, subj.a)
    proc = await _create_process(app_client, ha)
    owner_id = await _user_id(subj.b)
    r = await app_client.post(
        f"/api/v1/processes/{proc['id']}/owner", headers=ha, json={"user_id": str(owner_id)}
    )
    assert r.status_code == 201, r.text

    # A deliberate org-wide Process Owner (SYSTEM-bound, no marker) for contrast.
    org_wide_id = await s5.grant_role(subj.c, "Process Owner")

    org_id = await s5.default_org_id()
    async with get_sessionmaker()() as s:
        pool = await wf_repo.users_with_roles(s, org_id, ["Process Owner"])
    assert owner_id not in pool  # the per-process owner is NOT a workflow candidate
    assert org_wide_id in pool  # the org-wide Process Owner IS


async def test_owner_assignment_does_not_clobber_admin_process_grant(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """An admin's separately-granted PROCESS-scoped (unmarked) 'Process Owner' assignment is neither
    extended on assign nor deleted on the last-process revoke — owner-assignment only ever touches
    its own managed_by-marked row (the Codex finding, the PROCESS variant of the SYSTEM case)."""
    await _grant(subj.a, "process.create")
    await _grant(subj.a, "process.assign_owner")
    ha = _auth(token_factory, subj.a)
    proc = await _create_process(app_client, ha)
    owner_id = await _user_id(subj.b)

    # An admin grants the Process Owner permission-role bound to a DIFFERENT process (unmarked).
    other_process_id = str(uuid.uuid4())
    await _assign_role_bound(
        subj.b, "Process Owner", {"level": "PROCESS", "selector": {"process_id": other_process_id}}
    )

    await app_client.post(
        f"/api/v1/processes/{proc['id']}/owner", headers=ha, json={"user_id": str(owner_id)}
    )
    # Two rows now: the admin's unmarked PROCESS row + the owner-assignment marked row.
    assert len(await _po_role_assignments(owner_id)) == 2
    assert await _owner_role_process_ids(owner_id) == [proc["id"]]

    # Revoking the last owned process drops ONLY the marked row; the admin's unmarked row survives.
    rev = await app_client.delete(f"/api/v1/processes/{proc['id']}/owner/{owner_id}", headers=ha)
    assert rev.status_code == 204, rev.text
    remaining = await _po_role_assignments(owner_id)
    assert len(remaining) == 1
    bs = remaining[0].bound_scope or {}
    assert bs.get("managed_by") is None
    assert bs.get("selector", {}).get("process_id") == other_process_id


async def test_managed_owner_grant_not_revocable_via_generic_role_surface(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """An owner-assignment-managed grant is hidden from the generic per-user role list and a direct
    revoke there is refused (409) — it must be revoked via owner-assignment, which also drops the
    org_role_assignment RACI row (else the roster says owner while gates 403 — the Codex P2)."""
    await _grant(subj.a, "process.create")
    await _grant(subj.a, "process.assign_owner")
    await _grant(subj.a, "permission.grant")  # the generic role-revoke gate
    await _grant(subj.a, "user.read")
    ha = _auth(token_factory, subj.a)
    proc = await _create_process(app_client, ha)
    owner_id = await _user_id(subj.b)
    await app_client.post(
        f"/api/v1/processes/{proc['id']}/owner", headers=ha, json={"user_id": str(owner_id)}
    )

    # The managed Process-Owner grant is HIDDEN from the generic per-user role list.
    listed = await app_client.get(f"/api/v1/users/{owner_id}/roles", headers=ha)
    assert listed.status_code == 200, listed.text
    assert all(a["role_name"] != "Process Owner" for a in listed.json())

    # A direct revoke via the generic surface is refused (409).
    managed = [
        ra
        for ra in await _po_role_assignments(owner_id)
        if (ra.bound_scope or {}).get("managed_by")
    ]
    assert len(managed) == 1
    revoke = await app_client.delete(f"/api/v1/users/{owner_id}/roles/{managed[0].id}", headers=ha)
    assert revoke.status_code == 409, revoke.text


async def test_process_owner_cannot_mutate_links_for_unowned_process(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """A Process Owner (owner-assignment) of process A on a doc linked to A+B cannot remove the link
    to B (a process they don't own), but CAN remove the link to A — the link endpoints re-authorize
    document.manage_metadata against the TARGET process (the Codex P2 from the _document_scope
    widening)."""
    await s5.grant_lifecycle(subj.a)
    await _grant(subj.a, "process.create")
    await _grant(subj.a, "process.assign_owner")
    ha = _auth(token_factory, subj.a)
    did = await _doc_id(app_client, ha)
    proc_a = await _create_process(app_client, ha)
    proc_b = await _create_process(app_client, ha)
    await _link_doc_to_process(app_client, ha, did, proc_a["id"])
    await _link_doc_to_process(app_client, ha, did, proc_b["id"])

    owner_id = await _user_id(subj.b)
    await app_client.post(
        f"/api/v1/processes/{proc_a['id']}/owner", headers=ha, json={"user_id": str(owner_id)}
    )
    hb = _auth(token_factory, subj.b)

    # The A-owner CANNOT unlink the doc from B (unowned) → 403.
    deny = await app_client.delete(
        f"/api/v1/documents/{did}/process-links/{proc_b['id']}", headers=hb
    )
    assert deny.status_code == 403, deny.text
    # ...but CAN unlink it from A (their own process) → 204.
    allow = await app_client.delete(
        f"/api/v1/documents/{did}/process-links/{proc_a['id']}", headers=hb
    )
    assert allow.status_code == 204, allow.text


async def test_generic_role_assign_strips_managed_marker(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """A generic POST /users/{id}/roles cannot forge the owner-assignment ``managed_by`` marker — it
    is stripped, so the assignment stays visible on the role list and revocable there (the Codex P2:
    otherwise a caller could mint an un-revocable role assignment)."""
    await _grant(subj.a, "permission.grant")
    await _grant(subj.a, "user.read")
    ha = _auth(token_factory, subj.a)
    target_id = await _user_id(subj.b)

    r = await app_client.post(
        f"/api/v1/users/{target_id}/roles",
        headers=ha,
        json={
            "role_name": "Process Owner",
            "bound_scope": {"level": "SYSTEM", "managed_by": "owner_assignment"},
        },
    )
    assert r.status_code == 201, r.text
    assert (r.json()["bound_scope"] or {}).get("managed_by") is None  # the marker was stripped

    listed = await app_client.get(f"/api/v1/users/{target_id}/roles", headers=ha)
    assert any(a["role_name"] == "Process Owner" for a in listed.json())  # not hidden

    revoke = await app_client.delete(
        f"/api/v1/users/{target_id}/roles/{r.json()['id']}", headers=ha
    )
    assert revoke.status_code == 204, revoke.text  # not blocked


async def _seed_foreign_process(created_by: uuid.UUID) -> tuple[str, str]:
    """A process under a throwaway SECOND org (returns (org_id, process_id))."""
    async with get_sessionmaker()() as s:
        org = Organization(
            legal_name="Other Co", short_code=f"OTHER-{uuid.uuid4().hex[:6].upper()}"
        )
        s.add(org)
        await s.flush()
        proc = Process(
            org_id=org.id,
            name=f"ForeignProc-{uuid.uuid4().hex[:8]}",
            pdca_phase=PdcaPhase.DO,
            created_by=created_by,
        )
        s.add(proc)
        await s.flush()
        ids = (str(org.id), str(proc.id))
        await s.commit()
        return ids


async def _drop_foreign(org_id: str, process_id: str) -> None:
    async with get_sessionmaker()() as s:
        await s.execute(delete(Process).where(Process.id == uuid.UUID(process_id)))
        await s.execute(delete(Organization).where(Organization.id == uuid.UUID(org_id)))
        await s.commit()


async def test_process_link_cross_org_rejected(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """Linking a document to a process from ANOTHER org is rejected (422) — the org-isolation guard
    (documents.link_process_endpoint). Single-org per install (D1) makes this impossible in
    production; the test seeds a throwaway second org + process and **cleans them up in finally** so
    the shared session-scoped DB stays single-org (else test_setup's ``Organization.scalar_one``
    would break)."""
    uid = await s5.grant_lifecycle(subj.a)
    h = _auth(token_factory, subj.a)
    did = await _doc_id(app_client, h)
    org_id, proc_id = await _seed_foreign_process(uid)
    try:
        r = await app_client.post(
            f"/api/v1/documents/{did}/process-links", headers=h, json={"process_id": proc_id}
        )
        assert r.status_code == 422, r.text
    finally:
        await _drop_foreign(org_id, proc_id)
