"""S-capa-raise-process integration proofs — a bound Process Owner reaching + raising on the CAPA
board via PROCESS-scoped ``capa.read``/``capa.create``.

The new process-scoped raise from the board is only usable if the board's own reads admit the same
persona. ``GET /capas`` row-filters per-process (filter-not-403, the ``_readable_processes``
precedent) and the single reads (``GET /capas/{id}`` + ``/approval``) enforce at the CAPA's PROCESS
scope — so a bound Process Owner sees + opens only their owned-process CAPAs, while a SYSTEM
``capa.read`` holder is byte-identical. The write side (``POST /capas``) was already PROCESS-scoped;
these tests pin that a bound owner can raise ONLY in an owned process (the FE required-picker's
server backing).

Assertions are run-scoped (membership over this run's own ids) — the integration suite shares one
session DB across files, so absolute counts/empty-lists are never asserted.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from types import SimpleNamespace

import pytest
from httpx import AsyncClient

from .test_processes import _create_process, _grant, _user_id
from .test_vault import _auth

pytestmark = pytest.mark.integration


@pytest.fixture
def subj() -> SimpleNamespace:
    salt = uuid.uuid4().hex[:10]
    return SimpleNamespace(a=f"kc-cps-a-{salt}", b=f"kc-cps-b-{salt}", c=f"kc-cps-c-{salt}")


async def _assign_owner(
    client: AsyncClient, h: dict[str, str], process_id: str, user_id: uuid.UUID
) -> None:
    r = await client.post(
        f"/api/v1/processes/{process_id}/owner", headers=h, json={"user_id": str(user_id)}
    )
    assert r.status_code == 201, r.text


async def _raise_capa(
    client: AsyncClient, h: dict[str, str], *, process_id: str | None = None
) -> str:
    body: dict[str, object] = {"title": "P", "severity": "Minor"}
    if process_id is not None:
        body["process_id"] = process_id
    r = await client.post("/api/v1/capas", headers=h, json=body)
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def _capa_ids(client: AsyncClient, h: dict[str, str]) -> set[str]:
    r = await client.get("/api/v1/capas", headers=h)
    assert r.status_code == 200, r.text
    return {str(c["id"]) for c in r.json()["data"]}


async def test_process_owner_capa_list_narrows_to_owned(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """A bound Process Owner of P1 (not P2) sees ONLY the P1 CAPA on GET /capas — the P2 CAPA and a
    process-less (ad-hoc/SYSTEM) CAPA are hidden — while the SYSTEM author sees all three. The
    row-filter (filter-not-403) that lets the board render for the persona the raise targets."""
    await _grant(subj.a, "capa.create")
    await _grant(subj.a, "capa.read")  # SYSTEM read → the author's full-list baseline
    await _grant(subj.a, "process.create")
    await _grant(subj.a, "process.assign_owner")
    ha = _auth(token_factory, subj.a)
    p1 = await _create_process(app_client, ha)
    p2 = await _create_process(app_client, ha)
    capa_p1 = await _raise_capa(app_client, ha, process_id=p1["id"])
    capa_p2 = await _raise_capa(app_client, ha, process_id=p2["id"])
    capa_sys = await _raise_capa(app_client, ha)  # process-less

    owner_id = await _user_id(subj.b)
    await _assign_owner(app_client, ha, p1["id"], owner_id)
    hb = _auth(token_factory, subj.b)

    owned = await _capa_ids(app_client, hb)
    assert capa_p1 in owned
    assert capa_p2 not in owned
    assert capa_sys not in owned

    # The SYSTEM author still sees all three (byte-identical full list).
    assert {capa_p1, capa_p2, capa_sys} <= await _capa_ids(app_client, ha)


async def test_process_owner_capa_detail_and_approval_enforce_process_scope(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """The single reads enforce at the CAPA's PROCESS scope: a bound owner of P1 reads the P1 CAPA's
    detail + approval cycle, but the unowned P2 CAPA 403s on both (AZ-INV-8 — the bound scope is
    narrow). The approval read is the /tasks + drawer mirror that must still admit a SYSTEM
    holder."""
    await _grant(subj.a, "capa.create")
    await _grant(subj.a, "process.create")
    await _grant(subj.a, "process.assign_owner")
    ha = _auth(token_factory, subj.a)
    p1 = await _create_process(app_client, ha)
    p2 = await _create_process(app_client, ha)
    capa_p1 = await _raise_capa(app_client, ha, process_id=p1["id"])
    capa_p2 = await _raise_capa(app_client, ha, process_id=p2["id"])

    owner_id = await _user_id(subj.b)
    await _assign_owner(app_client, ha, p1["id"], owner_id)
    hb = _auth(token_factory, subj.b)

    assert (await app_client.get(f"/api/v1/capas/{capa_p1}", headers=hb)).status_code == 200
    assert (
        await app_client.get(f"/api/v1/capas/{capa_p1}/approval", headers=hb)
    ).status_code == 200
    assert (await app_client.get(f"/api/v1/capas/{capa_p2}", headers=hb)).status_code == 403
    assert (
        await app_client.get(f"/api/v1/capas/{capa_p2}/approval", headers=hb)
    ).status_code == 403


async def test_capa_list_no_grant_is_empty_not_403(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """A no-grant caller gets 200 + a list with none of this run's CAPAs — filter-not-403 (doc 18
    §5.2), matching the process landscape read, NOT the pre-slice SYSTEM-enforce 403."""
    await _grant(subj.a, "capa.create")
    await _grant(subj.a, "process.create")
    ha = _auth(token_factory, subj.a)
    p1 = await _create_process(app_client, ha)
    capa_p1 = await _raise_capa(app_client, ha, process_id=p1["id"])

    await _user_id(subj.c)  # JIT-create the no-grant user so get_current_user resolves it
    hc = _auth(token_factory, subj.c)
    listed = await app_client.get("/api/v1/capas", headers=hc)
    assert listed.status_code == 200, listed.text
    assert capa_p1 not in {str(c["id"]) for c in listed.json()["data"]}


async def test_process_owner_raises_capa_only_in_owned_process(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """The end-to-end feature: a bound owner of P1 raises a CAPA in P1 (201, then visible to them),
    but a process-less raise 403s (SYSTEM-scope enforce — the FE required-picker's server backing)
    and a raise into the unowned P2 403s (no smuggling). The owner's PROCESS-scoped capa.create is
    minted by the owner-assignment binding."""
    await _grant(subj.a, "process.create")
    await _grant(subj.a, "process.assign_owner")
    ha = _auth(token_factory, subj.a)
    p1 = await _create_process(app_client, ha)
    p2 = await _create_process(app_client, ha)

    owner_id = await _user_id(subj.b)
    await _assign_owner(app_client, ha, p1["id"], owner_id)
    hb = _auth(token_factory, subj.b)

    new_id = await _raise_capa(app_client, hb, process_id=p1["id"])
    assert new_id in await _capa_ids(app_client, hb)

    rless = await app_client.post(
        "/api/v1/capas", headers=hb, json={"title": "Ad-hoc", "severity": "Minor"}
    )
    assert rless.status_code == 403, rless.text
    rp2 = await app_client.post(
        "/api/v1/capas",
        headers=hb,
        json={"title": "Smuggle", "severity": "Minor", "process_id": p2["id"]},
    )
    assert rp2.status_code == 403, rp2.text
