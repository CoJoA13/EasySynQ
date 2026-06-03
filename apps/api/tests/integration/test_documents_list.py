"""S10 integration proofs — clause_refs in the GET /documents list serializer + the bracketed
filter grammar (doc 15 §2.1/§3.2): filter[clause_refs][has] (exact clause-number match), a scalar
filter, and the 400 unknown_filter / 422 bad-value rejections.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models.clause import Clause
from easysynq_api.db.models.framework import Framework
from easysynq_api.db.session import get_sessionmaker

from . import s5_helpers as s5
from .test_vault import _auth, _create, _map_clause

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


async def test_clause_refs_in_list_serializer(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await s5.grant_lifecycle(subj.a)
    ha = _auth(token_factory, subj.a)
    did = (await _create(app_client, ha, await s5.type_id("SOP")))["id"]
    await _map_clause(app_client, ha, did)  # maps to the lowest iso clause ("10")
    r = await app_client.get("/api/v1/documents?limit=100", headers=ha)
    assert r.status_code == 200, r.text
    row = next(d for d in r.json() if d["id"] == did)
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
    ids = [d["id"] for d in r.json()]
    assert did_84 in ids
    assert did_87 not in ids  # mapped to 8.7, not 8.4 — excluded by the exact-number filter
    for d in r.json():
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
    rows = r.json()
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
