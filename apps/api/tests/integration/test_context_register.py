"""S-context-1 integration proofs — the Context register's org-level CRUD + authz + reservation.

Clause 4.1 is ORG-LEVEL: the register rides ``register.read`` / ``register.manage`` at the SYSTEM
scope (no ``process_id`` on the satellite). ``GET /context`` is filter-not-403 (a no-grant caller
sees an empty list, never 403); ``GET /context/{id}`` enforces ``register.read`` @ SYSTEM; writes
enforce ``register.manage`` @ SYSTEM — so a PROCESS-bound owner (whose 0058 ``register.manage``
grant
is PROCESS-scoped) cannot create context issues. The CTX head is reserved from the generic document
mutations (the D-3b fold, now covering CTX).

Assertions are run-scoped (membership over this run's own ids) — the integration suite shares one
session DB across files, so absolute counts are never asserted (the no-grant EMPTY list is a
per-caller
filter guarantee, not a clean-DB assumption). These tests add rows / hit pre-mutation rejects only,
so
they keep the shared head editable (non-polluting); the lifecycle test owns the head-advancing path.
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


@pytest.fixture
def subj() -> SimpleNamespace:
    salt = uuid.uuid4().hex[:10]
    return SimpleNamespace(a=f"kc-ctx-a-{salt}", b=f"kc-ctx-b-{salt}", o=f"kc-ctx-o-{salt}")


async def _create_issue(
    client: AsyncClient,
    h: dict[str, str],
    *,
    classification: str = "internal",
    description: str = "C",
    category: str | None = None,
    last_reviewed_at: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"classification": classification, "description": description}
    if category is not None:
        body["category"] = category
    if last_reviewed_at is not None:
        body["last_reviewed_at"] = last_reviewed_at
    r = await client.post("/api/v1/context", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def _ids(client: AsyncClient, h: dict[str, str]) -> set[str]:
    r = await client.get("/api/v1/context", headers=h)
    assert r.status_code == 200, r.text
    return {str(x["id"]) for x in r.json()["data"]}


async def test_create_list_get_patch_happy_path(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """A register.manage @ SYSTEM steward creates → the issue defaults active, carries the SWOT
    category, lists + gets, and a PATCH closes it + sets last_reviewed_at + clears the category."""
    await _grant(subj.a, "register.manage")
    await _grant(subj.a, "register.read")
    h = _auth(token_factory, subj.a)

    created = await _create_issue(
        app_client, h, classification="external", description="market shift", category="threat"
    )
    assert created["classification"] == "external"
    assert created["category"] == "threat"
    assert created["status"] == "active"  # always active on create
    assert created["row_version"] == 1
    iid = created["id"]

    assert iid in await _ids(app_client, h)  # run-scoped membership, not an absolute count

    got = await app_client.get(f"/api/v1/context/{iid}", headers=h)
    assert got.status_code == 200, got.text
    assert got.json()["description"] == "market shift"

    patched = await app_client.patch(
        f"/api/v1/context/{iid}",
        headers=h,
        json={"status": "closed", "last_reviewed_at": "2026-06-01T00:00:00Z", "category": None},
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["status"] == "closed"
    assert body["category"] is None  # explicit null cleared the nullable SWOT axis
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
    created = await _create_issue(app_client, ha, description="org-level row")
    iid = created["id"]

    # a no-grant caller: empty list (filter-not-403), and 403 on the enforced surfaces.
    hn = _auth(token_factory, subj.b)  # subj.b granted nothing
    empty = await app_client.get("/api/v1/context", headers=hn)
    assert empty.status_code == 200, empty.text
    assert empty.json()["data"] == []  # a per-caller filter guarantee (no rows leak), never 403
    assert (await app_client.get(f"/api/v1/context/{iid}", headers=hn)).status_code == 403
    assert (
        await app_client.post(
            "/api/v1/context", headers=hn, json={"classification": "internal", "description": "x"}
        )
    ).status_code == 403
    assert (
        await app_client.patch(f"/api/v1/context/{iid}", headers=hn, json={"status": "closed"})
    ).status_code == 403

    # a register.read-only caller: reads the steward's row, but cannot create (manage required).
    await _grant(subj.o, "register.read")
    hr = _auth(token_factory, subj.o)
    assert iid in await _ids(app_client, hr)
    assert (await app_client.get(f"/api/v1/context/{iid}", headers=hr)).status_code == 200
    post = await app_client.post(
        "/api/v1/context", headers=hr, json={"classification": "internal", "description": "x"}
    )
    assert post.status_code == 403, post.text


async def test_patch_null_and_unknown_field_rules(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """A null on a NOT-NULL field (classification/status/description) 422s; a null on a nullable
    field (category) clears it; an unknown field 422s (extra=forbid)."""
    await _grant(subj.a, "register.manage")
    h = _auth(token_factory, subj.a)
    iid = (await _create_issue(app_client, h, category="strength"))["id"]

    bad = await app_client.patch(f"/api/v1/context/{iid}", headers=h, json={"classification": None})
    assert bad.status_code == 422, bad.text

    unknown = await app_client.patch(f"/api/v1/context/{iid}", headers=h, json={"nope": 1})
    assert unknown.status_code == 422, unknown.text

    cleared = await app_client.patch(f"/api/v1/context/{iid}", headers=h, json={"category": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["category"] is None


async def test_ctx_head_reserved_from_generic_mutations(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """D-3b (CTX): the Context register head is reserved from the generic metadata-PATCH, obsolete,
    link-target, and DCR-target mutations — a SYSTEM document.* holder gets 422
    context_register_managed_via_context. Pre-mutation rejects (head stays editable,
    non-polluting)."""
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
    head_id = (await _create_issue(app_client, h))["register_doc_id"]

    meta = await app_client.patch(f"/api/v1/documents/{head_id}", headers=h, json={"title": "x"})
    assert meta.status_code == 422, meta.text
    assert meta.json()["errors"][0]["code"] == "context_register_managed_via_context"

    obs = await app_client.post(
        f"/api/v1/documents/{head_id}/obsolete", headers=h, json={"reason": "retire it"}
    )
    assert obs.status_code == 422, obs.text
    assert obs.json()["errors"][0]["code"] == "context_register_managed_via_context"

    # the CTX head is reserved as a link TARGET (a link from a normal doc TO it).
    sop = await _create(app_client, h, await _sop_type_id())
    link_to = await app_client.post(
        f"/api/v1/documents/{sop['id']}/links",
        headers=h,
        json={"to_document_id": head_id, "link_type": "references"},
    )
    assert link_to.status_code == 422, link_to.text
    assert link_to.json()["errors"][0]["code"] == "context_register_managed_via_context"

    # a RETIRE DCR targeting the CTX head 422s at _resolve_target (the obsolete-chokepoint mirror).
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
    assert dcr.json()["errors"][0]["code"] == "context_register_managed_via_context"


async def test_process_bound_register_manage_cannot_create_context(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """Org-level SYSTEM gate: a PROCESS-bound Process Owner — who holds register.manage @ PROCESS
    via
    the 0058 owner-assignment binding — is 403'd on POST /context (the require dependency fires at
    SYSTEM scope; a PROCESS grant does not match the org-level register). Head-state independent."""
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
        "/api/v1/context", headers=ho, json={"classification": "internal", "description": "x"}
    )
    assert blocked.status_code == 403, blocked.text


async def test_ctx_register_head_not_generically_creatable(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """The CTX register head is system-managed (zero ProcessLinks, single head): the generic
    POST /documents path reserves the CTX type too (Codex P1) — even a SYSTEM document.create holder
    cannot mint a process-linked / second head that context find_head would adopt. Mirrors the RSK
    create-path reservation; the create-time guard is reject_managed_register_creation({RSK,
    CTX})."""
    await _grant(subj.a, "document.create")
    await _grant(subj.a, "process.create")
    ha = _auth(token_factory, subj.a)
    p1 = await _create_process(app_client, ha)
    async with get_sessionmaker()() as s:
        ctx_type_id = (
            (await s.execute(select(DocumentType.id).where(DocumentType.code == "CTX")))
            .scalars()
            .first()
        )
    assert ctx_type_id is not None, "CTX document_type must be seeded by migration 0060"
    r = await app_client.post(
        "/api/v1/documents",
        headers=ha,
        json={
            "title": "Sneaky context register head",
            "document_type_id": str(ctx_type_id),
            "process_ids": [p1["id"]],
        },
    )
    assert r.status_code == 422, r.text
