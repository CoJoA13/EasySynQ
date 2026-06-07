"""S10 + S-web-2 integration proofs — clause_refs in the GET /documents list serializer + the
bracketed filter grammar (doc 15 §2.1/§3.2): filter[clause_refs][has] (exact clause-number match), a
scalar filter, and the 400 unknown_filter / 422 bad-value rejections. S-web-2 adds the {data, page}
pagination envelope (authz-correct offset/has_more) and the effective_from date facet.
"""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Callable
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models.authz_grant import PermissionOverride
from easysynq_api.db.models.clause import Clause
from easysynq_api.db.models.framework import Framework
from easysynq_api.db.models.permission import Permission
from easysynq_api.db.models.scope import Scope
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.domain.authz.types import Effect, ScopeLevel

from . import s5_helpers as s5
from .test_vault import _auth, _create, _ensure_user, _map_clause

pytestmark = pytest.mark.integration


@pytest.fixture
def subj() -> SimpleNamespace:
    salt = uuid.uuid4().hex[:10]
    return SimpleNamespace(a=f"kc-a-{salt}", b=f"kc-b-{salt}")


async def _clause_by_number(number: str) -> str:
    async with get_sessionmaker()() as s:
        return str(
            (
                await s.execute(
                    select(Clause.id)
                    .join(Framework, Clause.framework_id == Framework.id)
                    .where(Framework.code == "iso9001:2015", Clause.number == number)
                )
            ).scalar_one()
        )


async def _map(client: AsyncClient, h: dict[str, str], doc_id: str, clause_id: str) -> None:
    r = await client.post(
        f"/api/v1/documents/{doc_id}/clause-mappings", headers=h, json={"clause_id": clause_id}
    )
    assert r.status_code == 201, r.text


async def _create_in_folder(
    client: AsyncClient, h: dict[str, str], type_id: str, folder_path: str
) -> dict:
    r = await client.post(
        "/api/v1/documents",
        headers=h,
        json={
            "title": "T",
            "document_type_id": type_id,
            "area_code": "PUR",
            "folder_path": folder_path,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _grant_read_folder(subject: str, folder_path: str) -> None:
    """Grant document.read at FOLDER scope (a SUBSET grant), so the row-filter drops out-of-folder
    docs — proving offset slices the POST-authz set, not a pre-authz SQL OFFSET."""
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        perm = (
            await s.execute(select(Permission).where(Permission.key == "document.read"))
        ).scalar_one()
        scope = Scope(
            org_id=user.org_id, level=ScopeLevel.FOLDER, selector={"folder_path": folder_path}
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


async def test_clause_refs_in_list_serializer(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await s5.grant_lifecycle(subj.a)
    ha = _auth(token_factory, subj.a)
    did = (await _create(app_client, ha, await s5.type_id("SOP")))["id"]
    await _map_clause(app_client, ha, did)  # maps to the lowest iso clause ("10")
    r = await app_client.get("/api/v1/documents?limit=100", headers=ha)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"data", "page"}  # S-web-2 envelope
    assert set(body["page"]) == {"limit", "offset", "returned", "has_more"}
    row = next(d for d in body["data"] if d["id"] == did)
    assert "clause_refs" in row
    assert "10" in row["clause_refs"]


async def test_filter_clause_refs_has(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await s5.grant_lifecycle(subj.a)
    ha = _auth(token_factory, subj.a)
    type_id = await s5.type_id("SOP")
    did_84 = (await _create(app_client, ha, type_id))["id"]
    await _map(app_client, ha, did_84, await _clause_by_number("8.4"))
    did_87 = (await _create(app_client, ha, type_id))["id"]
    await _map(app_client, ha, did_87, await _clause_by_number("8.7"))

    r = await app_client.get("/api/v1/documents?limit=100&filter[clause_refs][has]=8.4", headers=ha)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    ids = [d["id"] for d in data]
    assert did_84 in ids
    assert did_87 not in ids  # mapped to 8.7, not 8.4 — excluded by the exact-number filter
    for d in data:
        assert "8.4" in d["clause_refs"]  # every returned doc maps to 8.4


async def test_scalar_filter_current_state(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await s5.grant_lifecycle(subj.a)
    ha = _auth(token_factory, subj.a)
    did = (await _create(app_client, ha, await s5.type_id("SOP")))["id"]  # Draft
    r = await app_client.get(
        "/api/v1/documents?limit=100&filter[current_state][eq]=Draft", headers=ha
    )
    assert r.status_code == 200, r.text
    rows = r.json()["data"]
    assert any(d["id"] == did for d in rows)
    assert all(d["current_state"] == "Draft" for d in rows)


async def test_unknown_filter_field_400(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await s5.grant_lifecycle(subj.a)
    ha = _auth(token_factory, subj.a)
    r = await app_client.get("/api/v1/documents?filter[bogus][eq]=x", headers=ha)
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "unknown_filter"


async def test_unknown_filter_op_400(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await s5.grant_lifecycle(subj.a)
    ha = _auth(token_factory, subj.a)
    # current_state is filterable, but only with the eq op; "has" is not allowed for it.
    r = await app_client.get("/api/v1/documents?filter[current_state][has]=Draft", headers=ha)
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "unknown_filter"


async def test_bad_enum_value_422(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await s5.grant_lifecycle(subj.a)
    ha = _auth(token_factory, subj.a)
    r = await app_client.get("/api/v1/documents?filter[current_state][eq]=Bogus", headers=ha)
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "validation_error"


async def test_pagination_offset_and_has_more(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """The {data, page} envelope pages correctly: offset/limit slice the AUTHZ-FILTERED set (after
    the per-row document.read filter, NOT a pre-authz SQL OFFSET). Scoped to this run's docs via the
    owner_user_id filter (subj.a created exactly these)."""
    await s5.grant_lifecycle(subj.a)
    ha = _auth(token_factory, subj.a)
    type_id = await s5.type_id("SOP")
    created = [(await _create(app_client, ha, type_id))["id"] for _ in range(5)]
    owner = (await app_client.get(f"/api/v1/documents/{created[0]}", headers=ha)).json()[
        "owner_user_id"
    ]
    base = f"/api/v1/documents?filter[owner_user_id][eq]={owner}&limit=2"

    p1 = (await app_client.get(f"{base}&offset=0", headers=ha)).json()
    p2 = (await app_client.get(f"{base}&offset=2", headers=ha)).json()
    p3 = (await app_client.get(f"{base}&offset=4", headers=ha)).json()

    assert p1["page"] == {"limit": 2, "offset": 0, "returned": 2, "has_more": True}
    assert p2["page"] == {"limit": 2, "offset": 2, "returned": 2, "has_more": True}
    assert p3["page"] == {"limit": 2, "offset": 4, "returned": 1, "has_more": False}

    paged = [d["id"] for d in p1["data"] + p2["data"] + p3["data"]]
    assert len(paged) == 5  # no overlap across pages
    assert set(paged) == set(created)  # complete, gap-free coverage of this run's docs


async def test_filter_effective_from(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """The effective_from facet bounds on the CURRENT effective version's effective_from: a doc made
    Effective is in-range for a past gte and out-of-range for a future gte; a Draft (no effective
    version) is excluded by any bound."""
    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    await s5.set_approver_release(await s5.default_org_id(), True)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    type_id = await s5.type_id("SOP")

    eff = await s5.drive_to_effective(app_client, ha, hb, hb, type_id, f"eff-{subj.a}".encode())
    eff_id = eff["id"]
    draft_id = (await _create(app_client, ha, type_id))["id"]  # no effective version
    owner = (await app_client.get(f"/api/v1/documents/{eff_id}", headers=ha)).json()[
        "owner_user_id"
    ]

    now = datetime.datetime.now(datetime.UTC)
    # URL-encode: a raw "+00:00" in a query string decodes to a space (the +→space rule).
    past = quote((now - datetime.timedelta(days=1)).isoformat())
    future = quote((now + datetime.timedelta(days=1)).isoformat())
    base = f"/api/v1/documents?limit=100&filter[owner_user_id][eq]={owner}"

    in_range = (
        await app_client.get(f"{base}&filter[effective_from][gte]={past}", headers=ha)
    ).json()
    in_rows = {d["id"]: d for d in in_range["data"]}
    assert eff_id in in_rows  # effective_from ≈ now ≥ yesterday
    assert in_rows[eff_id]["effective_from"] is not None  # the row carries the date (S-web-2)
    assert draft_id not in in_rows  # Draft has no effective version → excluded by the join

    out_range = (
        await app_client.get(f"{base}&filter[effective_from][gte]={future}", headers=ha)
    ).json()
    assert eff_id not in {d["id"] for d in out_range["data"]}  # effective_from < tomorrow


async def test_pagination_slices_the_post_authz_set(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """The load-bearing pagination property: offset/limit slice the AUTHZ-FILTERED set, not a
    pre-authz SQL OFFSET. Proven with a SCOPED reader (subj.b reads only ONE folder) over a
    candidate window that interleaves readable + denied docs, so a pre-authz offset would skip a
    denied row and gap/leak. (The SYSTEM-grant test above can't catch this — it sees every row.)"""
    await s5.grant_lifecycle(subj.a)  # author/owner — creates the docs
    ha = _auth(token_factory, subj.a)
    type_id = await s5.type_id("SOP")
    salt = uuid.uuid4().hex[:8]
    pur, prd = f"sbw2{salt}.pur", f"sbw2{salt}.prd"

    # Interleave readable (pur) + denied (prd) folders so a pre-authz offset would hit a prd row.
    plan = [pur, prd, pur, prd, pur]  # 3 readable, 2 denied
    pur_ids: list[str] = []
    for folder in plan:
        doc = await _create_in_folder(app_client, ha, type_id, folder)
        if folder == pur:
            pur_ids.append(doc["id"])
    owner = (await app_client.get(f"/api/v1/documents/{pur_ids[0]}", headers=ha)).json()[
        "owner_user_id"
    ]

    # subj.b may read ONLY the `pur` folder → the row-filter keeps this run's 3 readable docs.
    await _grant_read_folder(subj.b, pur)
    hb = _auth(token_factory, subj.b)
    base = f"/api/v1/documents?filter[owner_user_id][eq]={owner}&limit=2"

    p1 = (await app_client.get(f"{base}&offset=0", headers=hb)).json()
    p2 = (await app_client.get(f"{base}&offset=2", headers=hb)).json()

    assert p1["page"] == {"limit": 2, "offset": 0, "returned": 2, "has_more": True}
    assert p2["page"] == {"limit": 2, "offset": 2, "returned": 1, "has_more": False}

    paged = p1["data"] + p2["data"]
    assert {d["id"] for d in paged} == set(pur_ids)  # exactly the 3 readable docs, gap-free
    assert all(d["folder_path"] == pur for d in paged)  # no denied (prd) doc leaked across pages
