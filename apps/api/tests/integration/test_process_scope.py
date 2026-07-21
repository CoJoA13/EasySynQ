"""S-process-scope-1 integration proofs — a bound Process Owner's PROCESS-scoped grant authorizing
END-TO-END across the auth surfaces that build their OWN ResourceContext.

S-owner-assignment-1 migrated ``_document_scope`` (document detail/list/edit) but left four surfaces
resolving a document/record scope WITHOUT ``process_ids`` — so the minted Process-Owner grant
mis-denied them. This slice threads ``process_ids`` (via the shared ``vault_repo`` loader) into:

* the workflow approval-history reads (``GET /workflow-instances/{id}`` + ``/{id}/approval``);
* the unified search row-filter (``/search`` + ``/search/suggest``);
* ``record.read``/list (a record inherits its SOURCE document's process links);

and adds the ``document.create`` write-path: ``DocumentCreate.process_ids`` links the new doc at
creation (atomically), with each link re-authorized against the TARGET process so a Process Owner of
P1 cannot smuggle a link to an unowned P2 (the S-owner-assignment-1 escalation class).

Every assertion is run-scoped (own ids / own tokens) — the shared session DB accumulates rows.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models.authz_grant import PermissionOverride
from easysynq_api.db.models.permission import Permission
from easysynq_api.db.models.scope import Scope
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.domain.authz.types import Effect, ScopeLevel

from . import s5_helpers as s5
from .test_processes import _create_process, _grant, _link_doc_to_process, _user_id
from .test_vault import _auth, _create, _ensure_user

pytestmark = pytest.mark.integration


@pytest.fixture
def subj() -> SimpleNamespace:
    salt = uuid.uuid4().hex[:10]
    return SimpleNamespace(a=f"kc-ps-a-{salt}", b=f"kc-ps-b-{salt}", c=f"kc-ps-c-{salt}")


async def _assign_owner(
    client: AsyncClient, h: dict[str, str], process_id: str, user_id: uuid.UUID
) -> None:
    r = await client.post(
        f"/api/v1/processes/{process_id}/owner", headers=h, json={"user_id": str(user_id)}
    )
    assert r.status_code == 201, r.text


# --- the process landscape row-filter (S-process-scope-2) --------------------------------


async def test_process_owner_list_and_map_narrow_to_owned(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """A bound Process Owner of P1 (not P2) sees ONLY P1 on GET /processes and /map — the P1→P2 edge
    is dropped (P2 hidden) — while the SYSTEM author sees both processes + the edge byte-identical.
    The per-process read-filter (filter-not-403) that unblocks the create-in-process picker."""
    await _grant(subj.a, "process.create")
    await _grant(subj.a, "process.manage")  # to add the edge
    await _grant(subj.a, "process.read")  # SYSTEM read → the author's full-landscape baseline
    await _grant(subj.a, "process.assign_owner")
    ha = _auth(token_factory, subj.a)
    p1 = await _create_process(app_client, ha)
    p2 = await _create_process(app_client, ha)
    edge = await app_client.post(
        f"/api/v1/processes/{p1['id']}/edges", headers=ha, json={"to_process_id": p2["id"]}
    )
    assert edge.status_code == 201, edge.text

    owner_id = await _user_id(subj.b)
    await _assign_owner(app_client, ha, p1["id"], owner_id)
    hb = _auth(token_factory, subj.b)

    # The bound owner's list narrows to P1 only (P2 hidden) — and it is 200, not 403.
    listed = await app_client.get("/api/v1/processes", headers=hb)
    assert listed.status_code == 200, listed.text
    assert {p["id"] for p in listed.json()} == {p1["id"]}

    # The map narrows nodes to P1 AND drops the P1→P2 edge (the dangling-edge guard).
    mp = await app_client.get("/api/v1/processes/map", headers=hb)
    assert mp.status_code == 200, mp.text
    body = mp.json()
    assert {n["id"] for n in body["nodes"]} == {p1["id"]}
    assert not any(
        e["from_process_id"] == p1["id"] and e["to_process_id"] == p2["id"] for e in body["edges"]
    )

    # The SYSTEM author still sees BOTH processes + the edge (byte-identical full landscape).
    author_map = (await app_client.get("/api/v1/processes/map", headers=ha)).json()
    assert {p1["id"], p2["id"]} <= {n["id"] for n in author_map["nodes"]}
    assert any(
        e["from_process_id"] == p1["id"] and e["to_process_id"] == p2["id"]
        for e in author_map["edges"]
    )


async def test_process_owner_list_hides_unreadable_parent_id(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """A bound owner of a CHILD process (not its parent) sees the child row with ``parent_id``
    nulled — the row-filter must not disclose a hidden parent's id (the edge-filter rationale;
    CX-2). The SYSTEM author still sees the real parent linkage."""
    await _grant(subj.a, "process.create")
    await _grant(subj.a, "process.read")
    await _grant(subj.a, "process.assign_owner")
    ha = _auth(token_factory, subj.a)
    parent = await _create_process(app_client, ha)
    child = await _create_process(app_client, ha, parent_id=parent["id"])

    owner_id = await _user_id(subj.b)
    await _assign_owner(app_client, ha, child["id"], owner_id)
    hb = _auth(token_factory, subj.b)

    listed = await app_client.get("/api/v1/processes", headers=hb)
    assert listed.status_code == 200, listed.text
    rows = {p["id"]: p for p in listed.json()}
    assert set(rows) == {child["id"]}  # only the owned child is visible
    assert rows[child["id"]]["parent_id"] is None  # the hidden parent is not disclosed

    # The SYSTEM author sees the real parent linkage (no sanitization on a full-landscape read).
    author_rows = {
        p["id"]: p for p in (await app_client.get("/api/v1/processes", headers=ha)).json()
    }
    assert author_rows[child["id"]]["parent_id"] == parent["id"]


# --- the document.create write-path -----------------------------------------------------


async def test_process_owner_creates_a_document_in_an_owned_process(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """A bound Process Owner can POST /documents with process_ids=[their process]: the doc is
    created AND linked atomically, and the owner can then read it (the link authorizes the read)."""
    await s5.grant_lifecycle(subj.a)
    await _grant(subj.a, "process.create")
    await _grant(subj.a, "process.assign_owner")
    ha = _auth(token_factory, subj.a)
    proc = await _create_process(app_client, ha)
    owner_id = await _user_id(subj.b)
    await _assign_owner(app_client, ha, proc["id"], owner_id)

    hb = _auth(token_factory, subj.b)
    r = await app_client.post(
        "/api/v1/documents",
        headers=hb,
        json={
            "title": "Owner SOP",
            "document_type_id": await s5.type_id("SOP"),
            "process_ids": [proc["id"]],
        },
    )
    assert r.status_code == 201, r.text
    new_id = r.json()["id"]

    # The doc is linked to the process (visible via the owner's own PROCESS-scoped read).
    links = await app_client.get(f"/api/v1/documents/{new_id}/process-links", headers=hb)
    assert links.status_code == 200, links.text
    assert {p["process_id"] for p in links.json()} == {proc["id"]}
    # ...and the owner can read the new doc (the link authorizes the PROCESS-scoped document.read).
    assert (await app_client.get(f"/api/v1/documents/{new_id}", headers=hb)).status_code == 200


async def test_process_owner_creates_with_multiple_owned_processes_deduped(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """A Process Owner of TWO processes can create a doc linked to both; a duplicated id in the body
    is deduped (no UNIQUE trip) and yields exactly the two distinct links."""
    await s5.grant_lifecycle(subj.a)
    await _grant(subj.a, "process.create")
    await _grant(subj.a, "process.assign_owner")
    ha = _auth(token_factory, subj.a)
    p1 = await _create_process(app_client, ha)
    p2 = await _create_process(app_client, ha)
    owner_id = await _user_id(subj.b)
    await _assign_owner(app_client, ha, p1["id"], owner_id)
    await _assign_owner(app_client, ha, p2["id"], owner_id)

    hb = _auth(token_factory, subj.b)
    r = await app_client.post(
        "/api/v1/documents",
        headers=hb,
        json={
            "title": "Two-process SOP",
            "document_type_id": await s5.type_id("SOP"),
            "process_ids": [p1["id"], p1["id"], p2["id"]],  # duplicate p1 → deduped
        },
    )
    assert r.status_code == 201, r.text
    links = await app_client.get(f"/api/v1/documents/{r.json()['id']}/process-links", headers=hb)
    assert links.status_code == 200, links.text
    assert {p["process_id"] for p in links.json()} == {p1["id"], p2["id"]}


async def test_process_owner_cannot_create_without_declaring_an_owned_process(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """A Process Owner's document.create is PROCESS-scoped: an unscoped create (no process_ids)
    carries no process_ids, so the grant matches nothing → 403 (deny-by-default)."""
    await s5.grant_lifecycle(subj.a)
    await _grant(subj.a, "process.create")
    await _grant(subj.a, "process.assign_owner")
    ha = _auth(token_factory, subj.a)
    proc = await _create_process(app_client, ha)
    owner_id = await _user_id(subj.b)
    await _assign_owner(app_client, ha, proc["id"], owner_id)

    hb = _auth(token_factory, subj.b)
    r = await app_client.post(
        "/api/v1/documents",
        headers=hb,
        json={"title": "Orphan", "document_type_id": await s5.type_id("SOP")},
    )
    assert r.status_code == 403, r.text


async def test_process_owner_cannot_link_an_unowned_process_at_create(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """The escalation guard: declaring an OWNED process (so the base document.create gate matches)
    PLUS an UNOWNED one must 403 — each declared link is re-authorized against the target process
    (document.manage_metadata), exactly as the standalone link endpoint does (S-owner-assign-1)."""
    await s5.grant_lifecycle(subj.a)
    await _grant(subj.a, "process.create")
    await _grant(subj.a, "process.assign_owner")
    ha = _auth(token_factory, subj.a)
    owned = await _create_process(app_client, ha)
    unowned = await _create_process(app_client, ha)
    owner_id = await _user_id(subj.b)
    await _assign_owner(app_client, ha, owned["id"], owner_id)

    hb = _auth(token_factory, subj.b)
    r = await app_client.post(
        "/api/v1/documents",
        headers=hb,
        json={
            "title": "Escalation attempt",
            "document_type_id": await s5.type_id("SOP"),
            "process_ids": [owned["id"], unowned["id"]],
        },
    )
    assert r.status_code == 403, r.text


async def test_create_with_unknown_process_is_422(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """A SYSTEM-create holder declaring a non-existent process gets a 422 (process validation runs
    once the base create gate passes)."""
    await s5.grant_lifecycle(subj.a)  # SYSTEM document.create + manage_metadata
    ha = _auth(token_factory, subj.a)
    r = await app_client.post(
        "/api/v1/documents",
        headers=ha,
        json={
            "title": "Bad link",
            "document_type_id": await s5.type_id("SOP"),
            "process_ids": [str(uuid.uuid4())],
        },
    )
    assert r.status_code == 422, r.text
    assert r.json()["errors"][0]["field"] == "process_ids"


async def test_create_holder_without_manage_metadata_cannot_link(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """A caller who can create but lacks document.manage_metadata (an Author) can still create an
    UNLINKED doc (byte-identical to the pre-slice path), but declaring a process 403s — the
    per-process manage_metadata gate (Authors hold no manage_metadata in the seeded role set)."""
    await s5.grant_lifecycle(subj.a)
    await _grant(subj.a, "process.create")
    ha = _auth(token_factory, subj.a)
    proc = await _create_process(app_client, ha)

    # subj.c: ONLY document.create (no manage_metadata).
    await _grant(subj.c, "document.create")
    hc = _auth(token_factory, subj.c)

    unlinked = await app_client.post(
        "/api/v1/documents",
        headers=hc,
        json={"title": "Unlinked", "document_type_id": await s5.type_id("SOP")},
    )
    assert unlinked.status_code == 201, unlinked.text

    with_link = await app_client.post(
        "/api/v1/documents",
        headers=hc,
        json={
            "title": "Wants a link",
            "document_type_id": await s5.type_id("SOP"),
            "process_ids": [proc["id"]],
        },
    )
    assert with_link.status_code == 403, with_link.text


# --- the workflow approval-history reads ------------------------------------------------


async def test_process_owner_reads_approval_history_of_a_linked_doc(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """A bound Process Owner can read the approval cycle (``/documents/{id}/approval``) and the
    workflow instance (``/workflow-instances/{id}``) of a doc linked to their process — both
    previously 403 (they built a doc context without process_ids). An UNLINKED doc stays 403."""
    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.c)  # the approver
    await _grant(subj.a, "process.create")
    await _grant(subj.a, "process.assign_owner")
    ha = _auth(token_factory, subj.a)
    hc = _auth(token_factory, subj.c)
    proc = await _create_process(app_client, ha)

    linked = await s5.drive_to_approved(app_client, ha, hc, await s5.type_id("SOP"), b"wf-linked")
    await _link_doc_to_process(app_client, ha, linked, proc["id"])
    unlinked = await s5.drive_to_approved(
        app_client, ha, hc, await s5.type_id("SOP"), b"wf-unlinked"
    )

    # The author can read the approval cycle → grab the instance id.
    appr = await app_client.get(f"/api/v1/documents/{linked}/approval", headers=ha)
    assert appr.status_code == 200, appr.text
    instance_id = appr.json()["id"]

    owner_id = await _user_id(subj.b)
    hb = _auth(token_factory, subj.b)
    # Before binding: the owner-to-be is denied both reads.
    assert (
        await app_client.get(f"/api/v1/documents/{linked}/approval", headers=hb)
    ).status_code == 403
    await _assign_owner(app_client, ha, proc["id"], owner_id)

    # After binding: both approval-history reads succeed on the LINKED doc.
    assert (
        await app_client.get(f"/api/v1/documents/{linked}/approval", headers=hb)
    ).status_code == 200
    assert (
        await app_client.get(f"/api/v1/workflow-instances/{instance_id}", headers=hb)
    ).status_code == 200
    # ...but the UNLINKED doc stays 403 (AZ-INV-8: the bound scope is narrow).
    assert (
        await app_client.get(f"/api/v1/documents/{unlinked}/approval", headers=hb)
    ).status_code == 403


# --- the search row-filter --------------------------------------------------------------


async def test_process_owner_search_sees_only_linked_effective_docs(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """The unified search row-filter now matches a PROCESS-scoped document.read: a bound Process
    Owner finds the Effective doc linked to their process and NOT an unlinked one (counted in
    ``hidden_by_scope``)."""
    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.c)  # approver + releaser
    await s5.set_approver_release(await s5.default_org_id(), True)  # releaser == approver (SoD-2)
    await _grant(subj.a, "process.create")
    await _grant(subj.a, "process.assign_owner")
    ha = _auth(token_factory, subj.a)
    hc = _auth(token_factory, subj.c)
    proc = await _create_process(app_client, ha)
    token = uuid.uuid4().hex[:8]

    linked = await s5.drive_to_effective(
        app_client, ha, hc, hc, await s5.type_id("SOP"), b"s-linked"
    )
    await _link_doc_to_process(app_client, ha, linked["id"], proc["id"])
    await app_client.patch(
        f"/api/v1/documents/{linked['id']}", headers=ha, json={"title": f"Zeta {token} Linked"}
    )
    unlinked = await s5.drive_to_effective(
        app_client, ha, hc, hc, await s5.type_id("SOP"), b"s-unlinked"
    )
    await app_client.patch(
        f"/api/v1/documents/{unlinked['id']}", headers=ha, json={"title": f"Zeta {token} Unlinked"}
    )

    owner_id = await _user_id(subj.b)
    await _assign_owner(app_client, ha, proc["id"], owner_id)
    hb = _auth(token_factory, subj.b)

    r = await app_client.get(f"/api/v1/search?q={token}", headers=hb)
    assert r.status_code == 200, r.text
    body = r.json()
    found_ids = {hit["id"] for hit in body["results"]}
    assert linked["id"] in found_ids
    assert unlinked["id"] not in found_ids
    assert body["hidden_by_scope"] >= 1


async def _add_override(
    subject: str,
    permission_key: str,
    effect: Effect,
    level: ScopeLevel,
    *,
    selector: dict[str, object] | None = None,
) -> None:
    """Seed a scoped permission override for ``subject`` (the register ``_add_override`` precedent)
    — used here to seed a FRAMEWORK-scoped ``document.read`` DENY."""
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        perm = (
            await s.execute(select(Permission).where(Permission.key == permission_key))
        ).scalar_one()
        scope = Scope(org_id=user.org_id, level=level, selector=selector)
        s.add(scope)
        await s.flush()
        s.add(
            PermissionOverride(
                org_id=user.org_id,
                user_id=user.id,
                permission_id=perm.id,
                effect=effect,
                scope_id=scope.id,
            )
        )
        await s.commit()


async def test_framework_scoped_document_read_deny_wins_on_detail_and_list(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """#333: a FRAMEWORK-scoped ``document.read`` DENY + a broad SYSTEM ALLOW -> 403 on the detail
    gate ``GET /documents/{id}`` (scope resolved via the canonical builder) AND the row is excluded
    from the Library list (the same shared ``resource_from_doc`` builder). Before #333 both surfaces
    left ``framework_id`` unset, so the FRAMEWORK DENY could never match and the SYSTEM ALLOW won
    (200 / visible) — the deny-always-wins gap this closes. ``subj.a`` (SYSTEM ``document.read``, no
    deny) proves the document is otherwise readable, isolating the deny as the cause of the 403."""
    await s5.grant_lifecycle(subj.a)  # creator + a plain SYSTEM document.read holder (control)
    ha = _auth(token_factory, subj.a)
    doc = await _create(app_client, ha, await s5.type_id("SOP"))

    await _add_override(subj.b, "document.read", Effect.ALLOW, ScopeLevel.SYSTEM)
    await _add_override(
        subj.b,
        "document.read",
        Effect.DENY,
        ScopeLevel.FRAMEWORK,
        selector={"framework_id": doc["framework_id"]},
    )
    hb = _auth(token_factory, subj.b)

    # Control: the creator (SYSTEM document.read, no framework deny) reads it fine.
    assert (await app_client.get(f"/api/v1/documents/{doc['id']}", headers=ha)).status_code == 200

    # Detail gate (canonical builder): the FRAMEWORK DENY wins over the broad SYSTEM ALLOW.
    denied = await app_client.get(f"/api/v1/documents/{doc['id']}", headers=hb)
    assert denied.status_code == 403, denied.text

    # List surface (shared row-filter): the framework-denied doc is excluded, not surfaced.
    listed = await app_client.get("/api/v1/documents", headers=hb)
    assert listed.status_code == 200, listed.text
    assert doc["id"] not in {r["id"] for r in listed.json()["data"]}
