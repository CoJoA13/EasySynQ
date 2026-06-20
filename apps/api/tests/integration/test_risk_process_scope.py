"""S-risk-1 integration proofs — the Risk & Opportunity register's process-scope authz.

A bound Process Owner reaches + authors ONLY their owned-process risk rows: ``GET /risks``
row-filters per-process (filter-not-403), the single read enforces at the row's PROCESS scope, and
the writes re-authorize the target process (the Slice-W escalation guards). The org-wide ``RSK``
head
carries ZERO ProcessLinks, so a bound owner cannot act on it. ``risk_rating`` is server-derived +
re-derived on every write. A second risk attaches to the SAME single non-Obsolete head.

Assertions are run-scoped (membership over this run's own ids) — the integration suite shares one
session DB across files, so absolute counts / empty-lists are never asserted.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models._audit_enums import EventType
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.session import get_sessionmaker

from .test_processes import _create_process, _grant, _user_id
from .test_vault import _auth

pytestmark = pytest.mark.integration


@pytest.fixture
def subj() -> SimpleNamespace:
    salt = uuid.uuid4().hex[:10]
    return SimpleNamespace(a=f"kc-rps-a-{salt}", b=f"kc-rps-b-{salt}", c=f"kc-rps-c-{salt}")


async def _assign_owner(
    client: AsyncClient, h: dict[str, str], process_id: str, user_id: uuid.UUID
) -> None:
    r = await client.post(
        f"/api/v1/processes/{process_id}/owner", headers=h, json={"user_id": str(user_id)}
    )
    assert r.status_code == 201, r.text


async def _create_risk(
    client: AsyncClient,
    h: dict[str, str],
    *,
    process_id: str | None = None,
    likelihood: int = 4,
    severity: int = 5,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": "risk",
        "description": "R",
        "likelihood": likelihood,
        "severity": severity,
    }
    if process_id is not None:
        body["process_id"] = process_id
    r = await client.post("/api/v1/risks", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def _risk_ids(client: AsyncClient, h: dict[str, str]) -> set[str]:
    r = await client.get("/api/v1/risks", headers=h)
    assert r.status_code == 200, r.text
    return {str(x["id"]) for x in r.json()["data"]}


async def test_process_owner_risk_list_narrows_to_owned(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """A bound Process Owner of P1 (not P2) sees ONLY the P1 risk on GET /risks — the P2 risk and a
    process-less (org-level/SYSTEM) risk are hidden — while the SYSTEM author sees all three
    (filter-not-403, the _readable_capas precedent)."""
    await _grant(subj.a, "register.manage")
    await _grant(subj.a, "register.read")  # SYSTEM read → the author's full-list baseline
    await _grant(subj.a, "process.create")
    await _grant(subj.a, "process.assign_owner")
    ha = _auth(token_factory, subj.a)
    p1 = await _create_process(app_client, ha)
    p2 = await _create_process(app_client, ha)
    risk_p1 = (await _create_risk(app_client, ha, process_id=p1["id"]))["id"]
    risk_p2 = (await _create_risk(app_client, ha, process_id=p2["id"]))["id"]
    risk_sys = (await _create_risk(app_client, ha))["id"]  # org-level (process-less)

    owner_id = await _user_id(subj.b)
    await _assign_owner(app_client, ha, p1["id"], owner_id)
    hb = _auth(token_factory, subj.b)

    owned = await _risk_ids(app_client, hb)
    assert risk_p1 in owned
    assert risk_p2 not in owned
    assert risk_sys not in owned
    assert {risk_p1, risk_p2, risk_sys} <= await _risk_ids(app_client, ha)


async def test_process_owner_risk_detail_enforces_process_scope(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """The single read enforces at the row's PROCESS scope: a bound owner of P1 reads the P1 risk
    (200) but the unowned P2 risk 403s (AZ-INV-8 — the bound scope is narrow)."""
    await _grant(subj.a, "register.manage")
    await _grant(subj.a, "process.create")
    await _grant(subj.a, "process.assign_owner")
    ha = _auth(token_factory, subj.a)
    p1 = await _create_process(app_client, ha)
    p2 = await _create_process(app_client, ha)
    risk_p1 = (await _create_risk(app_client, ha, process_id=p1["id"]))["id"]
    risk_p2 = (await _create_risk(app_client, ha, process_id=p2["id"]))["id"]

    owner_id = await _user_id(subj.b)
    await _assign_owner(app_client, ha, p1["id"], owner_id)
    hb = _auth(token_factory, subj.b)

    assert (await app_client.get(f"/api/v1/risks/{risk_p1}", headers=hb)).status_code == 200
    assert (await app_client.get(f"/api/v1/risks/{risk_p2}", headers=hb)).status_code == 403


async def test_system_register_read_holder_reads_process_scoped_risk(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """A SYSTEM ``register.read`` holder (the QMS Owner / Internal Auditor shape) reads a
    PROCESS-scoped risk's detail + sees it in the list — the SYSTEM grant satisfies the
    PROCESS-resolved resource, byte-identical to the org-wide view."""
    await _grant(subj.a, "register.manage")
    await _grant(subj.a, "process.create")
    ha = _auth(token_factory, subj.a)
    p1 = await _create_process(app_client, ha)
    risk_p1 = (await _create_risk(app_client, ha, process_id=p1["id"]))["id"]

    await _grant(subj.c, "register.read")  # SYSTEM register.read only — no process binding
    hc = _auth(token_factory, subj.c)
    assert (await app_client.get(f"/api/v1/risks/{risk_p1}", headers=hc)).status_code == 200
    assert risk_p1 in await _risk_ids(app_client, hc)


async def test_risk_list_no_grant_is_empty_not_403(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """A no-grant caller gets 200 + a list with none of this run's risks — filter-not-403 (doc 18
    §5.2), not a SYSTEM-enforce 403."""
    await _grant(subj.a, "register.manage")
    await _grant(subj.a, "process.create")
    ha = _auth(token_factory, subj.a)
    p1 = await _create_process(app_client, ha)
    risk_p1 = (await _create_risk(app_client, ha, process_id=p1["id"]))["id"]

    await _user_id(subj.c)  # JIT-create the no-grant user so get_current_user resolves it
    hc = _auth(token_factory, subj.c)
    listed = await app_client.get("/api/v1/risks", headers=hc)
    assert listed.status_code == 200, listed.text
    assert risk_p1 not in {str(x["id"]) for x in listed.json()["data"]}


async def test_process_owner_writes_only_in_owned_process(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """The Slice-W escalation guards: a bound owner of P1 authors a risk in P1 (201, then visible),
    but a process-less raise 403s (SYSTEM-scope enforce), a raise into the unowned P2 403s, AND a
    PATCH reassigning their P1 row to P2 403s (the _enforce_target_process guard re-auths the NEW
    target). The owner's PROCESS-scoped register.manage is minted by the owner-assignment
    binding."""
    await _grant(subj.a, "process.create")
    await _grant(subj.a, "process.assign_owner")
    ha = _auth(token_factory, subj.a)
    p1 = await _create_process(app_client, ha)
    p2 = await _create_process(app_client, ha)

    owner_id = await _user_id(subj.b)
    await _assign_owner(app_client, ha, p1["id"], owner_id)
    hb = _auth(token_factory, subj.b)

    row = await _create_risk(app_client, hb, process_id=p1["id"])
    assert row["id"] in await _risk_ids(app_client, hb)

    # process-less (org-level) raise → SYSTEM enforce → 403
    rless = await app_client.post(
        "/api/v1/risks",
        headers=hb,
        json={"type": "risk", "description": "Org-level", "likelihood": 2, "severity": 2},
    )
    assert rless.status_code == 403, rless.text
    # raise into the unowned P2 → 403
    rp2 = await app_client.post(
        "/api/v1/risks",
        headers=hb,
        json={
            "type": "risk",
            "description": "Smuggle",
            "likelihood": 2,
            "severity": 2,
            "process_id": p2["id"],
        },
    )
    assert rp2.status_code == 403, rp2.text
    # PATCH reassigning the owned P1 row's process_id to the unowned P2 → 403 (re-auth NEW target)
    reassign = await app_client.patch(
        f"/api/v1/risks/{row['id']}", headers=hb, json={"process_id": p2["id"]}
    )
    assert reassign.status_code == 403, reassign.text


async def test_risk_head_carries_no_process_links(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """The org-wide RSK head carries ZERO ProcessLinks (L1-MAJOR): a bound Process Owner — who holds
    PROCESS-scoped document.checkout — is 403'd checking out the head, because the head resolves to
    an empty process set (a ProcessLink would let a bound owner control the org register)."""
    await _grant(subj.a, "register.manage")
    await _grant(subj.a, "process.create")
    await _grant(subj.a, "process.assign_owner")
    ha = _auth(token_factory, subj.a)
    p1 = await _create_process(app_client, ha)
    row = await _create_risk(app_client, ha, process_id=p1["id"])
    head_id = row["register_doc_id"]

    owner_id = await _user_id(subj.b)
    await _assign_owner(app_client, ha, p1["id"], owner_id)
    hb = _auth(token_factory, subj.b)

    # The bound owner's PROCESS document.checkout does not match the link-less org head → 403.
    co = await app_client.post(f"/api/v1/documents/{head_id}/checkout", headers=hb)
    assert co.status_code == 403, co.text


async def test_single_non_obsolete_head(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """Two risk creates attach to the SAME single non-Obsolete RSK head (the service-level
    get-or-create guard) — never a second head."""
    await _grant(subj.a, "register.manage")
    await _grant(subj.a, "process.create")
    ha = _auth(token_factory, subj.a)
    p1 = await _create_process(app_client, ha)
    r1 = await _create_risk(app_client, ha, process_id=p1["id"])
    r2 = await _create_risk(app_client, ha, process_id=p1["id"])
    assert r1["register_doc_id"] == r2["register_doc_id"]


async def test_risk_rating_is_server_derived_and_re_derived(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """``risk_rating`` is derived from likelihood x severity at create (4x5 = 20 → Critical) and
    re-derived on every PATCH that changes them (2x5 = 10 → Medium); a client-sent rating is ignored
    (the field is read-only)."""
    await _grant(subj.a, "register.manage")
    await _grant(subj.a, "process.create")
    ha = _auth(token_factory, subj.a)
    p1 = await _create_process(app_client, ha)
    row = await _create_risk(app_client, ha, process_id=p1["id"], likelihood=4, severity=5)
    assert row["risk_rating"] == 20
    assert row["band"] == "critical"

    patched = await app_client.patch(
        f"/api/v1/risks/{row['id']}", headers=ha, json={"likelihood": 2}
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["risk_rating"] == 10
    assert body["band"] == "medium"

    # the re-score emitted a RISK_RESCORED audit keyed on the register head (run-scoped: this row).
    async with get_sessionmaker()() as s:
        events = (
            (
                await s.execute(
                    select(AuditEvent).where(
                        AuditEvent.event_type == EventType.RISK_RESCORED,
                        AuditEvent.object_id == uuid.UUID(row["register_doc_id"]),
                    )
                )
            )
            .scalars()
            .all()
        )
    assert any((e.after or {}).get("risk_id") == row["id"] for e in events)
