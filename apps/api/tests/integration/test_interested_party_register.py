"""S-interested-parties-1 integration proofs — the Interested Parties register's org-level CRUD +
authz + reservation.

Clause 4.2 is ORG-LEVEL: the register rides ``register.read`` / ``register.manage`` at the SYSTEM
scope (no ``process_id`` on the satellite). ``GET /interested-parties`` is filter-not-403 (a
no-grant caller sees an empty list, never 403); ``GET /interested-parties/{id}`` enforces
``register.read`` @
SYSTEM; writes enforce ``register.manage`` @ SYSTEM — so a PROCESS-bound owner (whose 0058
``register.manage`` grant is PROCESS-scoped) cannot create interested parties. The IPR head is
reserved from the generic document mutations (the D-3b fold, now covering IPR — the create /
mutate / DCR-implement / import quad; the S-context-1 Codex P1/P2 class).

Assertions are run-scoped (membership over this run's own ids) — the integration suite shares one
session DB across files, so absolute counts are never asserted. These tests add rows / hit
pre-mutation rejects only, so they keep the shared head editable (non-polluting); the lifecycle test
owns the head-advancing path.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models.document_type import DocumentType
from easysynq_api.db.session import get_sessionmaker

from .test_processes import _create_process, _grant, _user_id
from .test_vault import _auth, _create, _sop_type_id

pytestmark = pytest.mark.integration

_MANAGED = "interested_parties_register_managed_via_interested_parties"


@pytest.fixture
def subj() -> SimpleNamespace:
    salt = uuid.uuid4().hex[:10]
    return SimpleNamespace(a=f"kc-ip-a-{salt}", b=f"kc-ip-b-{salt}", o=f"kc-ip-o-{salt}")


async def _create_party(
    client: AsyncClient,
    h: dict[str, str],
    *,
    party_type: str = "customer",
    party_name: str = "Acme",
    needs_expectations: str = "fair pricing",
    influence: str | None = None,
    last_reviewed_at: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "party_type": party_type,
        "party_name": party_name,
        "needs_expectations": needs_expectations,
    }
    if influence is not None:
        body["influence"] = influence
    if last_reviewed_at is not None:
        body["last_reviewed_at"] = last_reviewed_at
    r = await client.post("/api/v1/interested-parties", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def _ids(client: AsyncClient, h: dict[str, str]) -> set[str]:
    r = await client.get("/api/v1/interested-parties", headers=h)
    assert r.status_code == 200, r.text
    return {str(x["id"]) for x in r.json()["data"]}


async def test_create_list_get_patch_happy_path(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """A register.manage @ SYSTEM steward creates → the party defaults active, carries the influence
    axis, lists + gets, and a PATCH closes it + sets last_reviewed_at + clears the influence."""
    await _grant(subj.a, "register.manage")
    await _grant(subj.a, "register.read")
    h = _auth(token_factory, subj.a)

    created = await _create_party(
        app_client,
        h,
        party_type="regulator",
        party_name="EU notified body",
        needs_expectations="conformity",
        influence="high",
    )
    assert created["party_type"] == "regulator"
    assert created["influence"] == "high"
    assert created["status"] == "active"  # always active on create
    assert created["row_version"] == 1
    pid = created["id"]

    assert pid in await _ids(app_client, h)  # run-scoped membership, not an absolute count

    got = await app_client.get(f"/api/v1/interested-parties/{pid}", headers=h)
    assert got.status_code == 200, got.text
    assert got.json()["needs_expectations"] == "conformity"

    patched = await app_client.patch(
        f"/api/v1/interested-parties/{pid}",
        headers=h,
        json={"status": "closed", "last_reviewed_at": "2026-06-01T00:00:00Z", "influence": None},
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["status"] == "closed"
    assert body["influence"] is None  # explicit null cleared the nullable influence axis
    assert body["last_reviewed_at"] is not None
    assert body["row_version"] == 2  # bumped on edit


async def test_org_level_authz_filter_and_enforce(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """Org-level authz: a register.manage steward's row is visible to a register.read holder; a
    no-grant caller's list is EMPTY (filter-not-403, never a 403) and its single-GET/POST/PATCH 403;
    a register.read-only caller can read but NOT create (manage required)."""
    await _grant(subj.a, "register.manage")
    await _grant(subj.a, "register.read")
    ha = _auth(token_factory, subj.a)
    created = await _create_party(app_client, ha, needs_expectations="org-level row")
    pid = created["id"]

    # a no-grant caller: empty list (filter-not-403), and 403 on the enforced surfaces.
    hn = _auth(token_factory, subj.b)  # subj.b granted nothing
    empty = await app_client.get("/api/v1/interested-parties", headers=hn)
    assert empty.status_code == 200, empty.text
    assert empty.json()["data"] == []  # a per-caller filter guarantee (no rows leak), never 403
    assert (
        await app_client.get(f"/api/v1/interested-parties/{pid}", headers=hn)
    ).status_code == 403
    assert (
        await app_client.post(
            "/api/v1/interested-parties",
            headers=hn,
            json={"party_type": "customer", "party_name": "x", "needs_expectations": "x"},
        )
    ).status_code == 403
    assert (
        await app_client.patch(
            f"/api/v1/interested-parties/{pid}", headers=hn, json={"status": "closed"}
        )
    ).status_code == 403

    # a register.read-only caller: reads the steward's row, but cannot create (manage required).
    await _grant(subj.o, "register.read")
    hr = _auth(token_factory, subj.o)
    assert pid in await _ids(app_client, hr)
    assert (
        await app_client.get(f"/api/v1/interested-parties/{pid}", headers=hr)
    ).status_code == 200
    post = await app_client.post(
        "/api/v1/interested-parties",
        headers=hr,
        json={"party_type": "customer", "party_name": "x", "needs_expectations": "x"},
    )
    assert post.status_code == 403, post.text


async def test_patch_null_and_unknown_field_rules(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """A null on a NOT-NULL field (party_type/party_name/needs_expectations/status) 422s; a null on
    a nullable field (influence) clears it; an unknown field 422s (extra=forbid)."""
    await _grant(subj.a, "register.manage")
    h = _auth(token_factory, subj.a)
    pid = (await _create_party(app_client, h, influence="low"))["id"]

    bad = await app_client.patch(
        f"/api/v1/interested-parties/{pid}", headers=h, json={"party_type": None}
    )
    assert bad.status_code == 422, bad.text

    unknown = await app_client.patch(
        f"/api/v1/interested-parties/{pid}", headers=h, json={"nope": 1}
    )
    assert unknown.status_code == 422, unknown.text

    cleared = await app_client.patch(
        f"/api/v1/interested-parties/{pid}", headers=h, json={"influence": None}
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["influence"] is None


async def test_ipr_head_reserved_from_generic_mutations(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """D-3b (IPR): the Interested Parties register head is reserved from the generic metadata-PATCH,
    obsolete, link-target, and DCR-target mutations — a SYSTEM document.* holder gets 422
    interested_parties_register_managed_via_interested_parties. Pre-mutation rejects (head stays
    editable, non-polluting)."""
    await _grant(subj.a, "register.manage")
    for key in (
        "document.read",
        "document.create",
        "document.manage_metadata",
        "document.obsolete",
        "changeRequest.create",
    ):
        await _grant(subj.a, key)
    h = _auth(token_factory, subj.a)
    head_id = (await _create_party(app_client, h))["register_doc_id"]

    meta = await app_client.patch(f"/api/v1/documents/{head_id}", headers=h, json={"title": "x"})
    assert meta.status_code == 422, meta.text
    assert meta.json()["errors"][0]["code"] == _MANAGED

    obs = await app_client.post(
        f"/api/v1/documents/{head_id}/obsolete", headers=h, json={"reason": "retire it"}
    )
    assert obs.status_code == 422, obs.text
    assert obs.json()["errors"][0]["code"] == _MANAGED

    # the IPR head is reserved as a link TARGET (a link from a normal doc TO it).
    sop = await _create(app_client, h, await _sop_type_id())
    link_to = await app_client.post(
        f"/api/v1/documents/{sop['id']}/links",
        headers=h,
        json={"to_document_id": head_id, "link_type": "references"},
    )
    assert link_to.status_code == 422, link_to.text
    assert link_to.json()["errors"][0]["code"] == _MANAGED

    # a RETIRE DCR targeting the IPR head 422s at _resolve_target (the obsolete-chokepoint mirror).
    dcr = await app_client.post(
        "/api/v1/dcrs",
        headers=h,
        json={
            "change_type": "RETIRE",
            "change_significance": "MAJOR",
            "reason_class": "other",
            "reason_text": "retire the register via DCR",
            "target_document_id": head_id,
        },
    )
    assert dcr.status_code == 422, dcr.text
    assert dcr.json()["errors"][0]["code"] == _MANAGED


async def test_process_bound_register_manage_cannot_create_party(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """Org-level SYSTEM gate: a PROCESS-bound Process Owner — who holds register.manage @ PROCESS
    via the 0058 owner-assignment binding — is 403'd on POST /interested-parties (the require
    dependency
    fires at SYSTEM scope; a PROCESS grant does not match the org-level register). Head-state
    independent."""
    await _grant(subj.a, "register.manage")
    await _grant(subj.a, "process.create")
    await _grant(subj.a, "process.assign_owner")
    ha = _auth(token_factory, subj.a)
    p1 = await _create_process(app_client, ha)
    owner_id = await _user_id(subj.o)
    r = await app_client.post(
        f"/api/v1/processes/{p1['id']}/owner", headers=ha, json={"user_id": str(owner_id)}
    )
    assert r.status_code == 201, r.text
    ho = _auth(token_factory, subj.o)  # PROCESS register.manage only

    blocked = await app_client.post(
        "/api/v1/interested-parties",
        headers=ho,
        json={"party_type": "customer", "party_name": "x", "needs_expectations": "x"},
    )
    assert blocked.status_code == 403, blocked.text


async def test_ipr_register_head_not_generically_creatable(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """The IPR register head is system-managed (zero ProcessLinks, single head): the generic
    POST /documents path reserves the IPR type too (Codex P1) — even a SYSTEM document.create holder
    cannot mint a process-linked / second head that the interested-parties find_head would adopt.
    Mirrors the RSK/CTX create-path reservation; the create-time guard is
    reject_managed_register_creation({RSK, CTX, IPR})."""
    await _grant(subj.a, "document.create")
    await _grant(subj.a, "process.create")
    ha = _auth(token_factory, subj.a)
    p1 = await _create_process(app_client, ha)
    async with get_sessionmaker()() as s:
        ipr_type_id = (
            (await s.execute(select(DocumentType.id).where(DocumentType.code == "IPR")))
            .scalars()
            .first()
        )
    assert ipr_type_id is not None, "IPR document_type must be seeded by migration 0061"
    r = await app_client.post(
        "/api/v1/documents",
        headers=ha,
        json={
            "title": "Sneaky interested-parties register head",
            "document_type_id": str(ipr_type_id),
            "process_ids": [p1["id"]],
        },
    )
    assert r.status_code == 422, r.text
