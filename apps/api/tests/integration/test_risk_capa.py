"""S-risk-3 integration proofs — the Risk → CAPA treatment-spawn seam (clause 6.1 §7).

One-click "treat this risk → spawn a CAPA": idempotent under a FOR UPDATE lock on the risk row
(``linked_capa_id`` is the latch — the lock, NOT a UNIQUE, is the idempotency guard; two concurrent
spawns mint exactly ONE CAPA that the loser replays). The spawn is gated ``capa.create`` at the
risk's OWN process scope (re-authorized under the lock — the TOCTOU close), inherits the risk's
``process_id`` + a band-derived severity, sets ``source=risk``, and emits a ``RISK_SPAWNED_CAPA`` on
the register head (+ the CAPA's own ``CAPA_RAISED`` on the record side).

Run-scoped assertions (the shared session DB). These tests only ADD risks + spawn (no register
lifecycle drive) → the head stays editable, non-polluting. The spawn-while-Effective operational
proof lives in ``test_risk_lifecycle.py`` (with the ``restore_register_head`` teardown).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models._audit_enums import EventType
from easysynq_api.db.models._capa_enums import CapaSource
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.capa import Capa
from easysynq_api.db.session import get_sessionmaker

from .test_processes import _create_process, _grant, _user_id
from .test_vault import _auth

pytestmark = pytest.mark.integration


@pytest.fixture
def subj() -> SimpleNamespace:
    salt = uuid.uuid4().hex[:10]
    return SimpleNamespace(a=f"kc-rcap-a-{salt}", b=f"kc-rcap-b-{salt}", c=f"kc-rcap-c-{salt}")


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
        "description": "treatable risk",
        "likelihood": likelihood,
        "severity": severity,
    }
    if process_id is not None:
        body["process_id"] = process_id
    r = await client.post("/api/v1/risks", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def test_spawn_capa_for_risk_idempotent(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """The happy path: a SYSTEM register.manage + capa.create holder spawns a CAPA to treat a
    process-scoped risk — 201, source=risk, severity band-derived (4x5=20 critical → Critical),
    process inherited, ``linked_capa_id`` latched on the live row. A second spawn returns the SAME
    CAPA, 200 (the latch + FOR UPDATE lock idempotency). A RISK_SPAWNED_CAPA trails on the register
    head."""
    await _grant(subj.a, "register.manage")
    await _grant(subj.a, "register.read")
    await _grant(subj.a, "capa.create")
    await _grant(subj.a, "capa.read")
    await _grant(subj.a, "process.create")
    ha = _auth(token_factory, subj.a)
    p1 = await _create_process(app_client, ha)
    row = await _create_risk(app_client, ha, process_id=p1["id"], likelihood=4, severity=5)
    assert row["band"] == "critical"
    assert row["linked_capa_id"] is None

    first = await app_client.post(f"/api/v1/risks/{row['id']}/capa", headers=ha)
    assert first.status_code == 201, first.text
    capa = first.json()
    assert capa["source"] == "risk"
    assert capa["severity"] == "Critical"  # band critical → CAPA Critical (the two-tier routing)
    assert capa["process_id"] == p1["id"]  # inherits the risk's process
    capa_id = capa["id"]

    # the live risk now carries the latch (operational display).
    detail = await app_client.get(f"/api/v1/risks/{row['id']}", headers=ha)
    assert detail.status_code == 200, detail.text
    assert detail.json()["linked_capa_id"] == capa_id

    # idempotent replay — the SAME CAPA, 200, latch unchanged.
    again = await app_client.post(f"/api/v1/risks/{row['id']}/capa", headers=ha)
    assert again.status_code == 200, again.text
    assert again.json()["id"] == capa_id

    async with get_sessionmaker()() as s:
        spawned = (
            (
                await s.execute(
                    select(AuditEvent).where(
                        AuditEvent.event_type == EventType.RISK_SPAWNED_CAPA,
                        AuditEvent.object_id == uuid.UUID(row["register_doc_id"]),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert any((e.after or {}).get("risk_id") == row["id"] for e in spawned)
        capa_row = await s.get(Capa, uuid.UUID(capa_id))
        assert capa_row is not None and capa_row.source is CapaSource.risk


async def test_spawn_capa_race_serializes_to_one(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """Codex L4: the FOR UPDATE lock on the risk row — NOT a UNIQUE on the latch — is the
    idempotency guard. Two CONCURRENT spawns (asyncio.gather over two HTTP POSTs, each its own
    request session) serialize to ONE CAPA: the loser blocks on the row lock, then sees the latch
    set (populate_existing) and replays the existing CAPA (200). Without the lock both would mint a
    distinct CAPA → two ids → the single-id assertion fails."""
    await _grant(subj.a, "register.manage")
    await _grant(subj.a, "register.read")
    await _grant(subj.a, "capa.create")
    await _grant(subj.a, "capa.read")
    await _grant(subj.a, "process.create")
    ha = _auth(token_factory, subj.a)
    p1 = await _create_process(app_client, ha)
    row = await _create_risk(app_client, ha, process_id=p1["id"])

    r1, r2 = await asyncio.gather(
        app_client.post(f"/api/v1/risks/{row['id']}/capa", headers=ha),
        app_client.post(f"/api/v1/risks/{row['id']}/capa", headers=ha),
    )
    assert sorted([r1.status_code, r2.status_code]) == [200, 201], (
        r1.status_code,
        r2.status_code,
        r1.text,
        r2.text,
    )
    assert r1.json()["id"] == r2.json()["id"]  # exactly ONE CAPA — the lock serialized the latch


async def test_spawn_capa_authz_escalation(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """The spawn is gated capa.create at the risk's OWN process: a bound Process Owner of P1 (who
    holds capa.create @ P1 via the seeded bundle + the owner-assignment binding) spawns for the P1
    risk (201) but is 403'd spawning for the unowned P2 risk AND an org-level (process-less) risk
    (SYSTEM scope) — the spawned CAPA inherits the risk's process, so capa.create over the WRONG
    process is the escalation boundary (the body-scope re-auth, mirroring create_risk)."""
    await _grant(subj.a, "register.manage")
    await _grant(subj.a, "register.read")
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
    hb = _auth(token_factory, subj.b)  # bound Process Owner of P1 — capa.create @ P1 only

    spawned = await app_client.post(f"/api/v1/risks/{risk_p1}/capa", headers=hb)
    assert spawned.status_code == 201, spawned.text
    assert spawned.json()["source"] == "risk"

    denied_p2 = await app_client.post(f"/api/v1/risks/{risk_p2}/capa", headers=hb)
    assert denied_p2.status_code == 403, denied_p2.text  # no capa.create @ P2
    denied_sys = await app_client.post(f"/api/v1/risks/{risk_sys}/capa", headers=hb)
    assert denied_sys.status_code == 403, denied_sys.text  # SYSTEM scope → bound owner denied


async def test_spawn_replay_denied_for_reassigned_risk_cross_process(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """Codex P1: the latched CAPA keeps its ORIGINAL process when the risk is later reassigned. The
    SYSTEM author creates a P1 risk, spawns a CAPA (@ P1), then reassigns the risk to P2. A bound
    owner of P2 (full P2 bundle incl. capa.read, but NOTHING on P1) then hits the replay path and is
    403'd — the replay re-authorizes capa.read over the CAPA's OWN process (P1), which they do not
    own, so the cross-process CAPA is never disclosed to them."""
    await _grant(subj.a, "register.manage")
    await _grant(subj.a, "register.read")
    await _grant(subj.a, "capa.create")
    await _grant(subj.a, "capa.read")
    await _grant(subj.a, "process.create")
    await _grant(subj.a, "process.assign_owner")
    ha = _auth(token_factory, subj.a)
    p1 = await _create_process(app_client, ha)
    p2 = await _create_process(app_client, ha)
    row = await _create_risk(app_client, ha, process_id=p1["id"])
    risk_id = row["id"]

    spawn = await app_client.post(f"/api/v1/risks/{risk_id}/capa", headers=ha)  # CAPA @ P1
    assert spawn.status_code == 201, spawn.text
    # reassign the risk P1 → P2 (the head is editable in the default state; register.manage @ P2).
    reassign = await app_client.patch(
        f"/api/v1/risks/{risk_id}", headers=ha, json={"process_id": p2["id"]}
    )
    assert reassign.status_code == 200, reassign.text

    owner_id = await _user_id(subj.b)
    await _assign_owner(app_client, ha, p2["id"], owner_id)
    hb = _auth(token_factory, subj.b)  # owns P2 only (capa.create + register.read @ P2, not @ P1)

    denied = await app_client.post(f"/api/v1/risks/{risk_id}/capa", headers=hb)
    assert denied.status_code == 403, (
        denied.text
    )  # the latched CAPA's OWN process (P1) is re-checked


async def test_spawn_replay_requires_capa_read(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """Codex round 2: the replay RETURNS the existing CAPA's details, so it is gated capa.read over
    the CAPA's process (the GET /capas/{id} authority), not merely capa.create. A caller with
    register.read + capa.create but NO capa.read reaches the spawn but is 403'd on the replay — they
    cannot receive a CAPA they could not fetch directly."""
    await _grant(subj.a, "register.manage")
    await _grant(subj.a, "register.read")
    await _grant(subj.a, "capa.create")
    await _grant(subj.a, "capa.read")
    ha = _auth(token_factory, subj.a)
    row = await _create_risk(app_client, ha)  # org-level (SYSTEM scopes)
    spawn = await app_client.post(f"/api/v1/risks/{row['id']}/capa", headers=ha)
    assert spawn.status_code == 201, spawn.text  # CAPA spawned by subj.a

    await _grant(subj.c, "register.read")
    await _grant(subj.c, "capa.create")  # ...but deliberately NOT capa.read
    hc = _auth(token_factory, subj.c)
    denied = await app_client.post(f"/api/v1/risks/{row['id']}/capa", headers=hc)
    assert denied.status_code == 403, (
        denied.text
    )  # the replay returns a CAPA read → capa.read gated


async def test_spawn_rejects_opportunity_rows(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """Codex round 2: a CAPA is a corrective/preventive action — only a ``risk`` row is treated by
    one. A ``type=opportunity`` row is rejected (422); opportunities are pursued via improvement
    initiatives, never stamped as a source=risk corrective CAPA."""
    await _grant(subj.a, "register.manage")
    await _grant(subj.a, "register.read")
    await _grant(subj.a, "capa.create")
    await _grant(subj.a, "capa.read")
    await _grant(subj.a, "process.create")
    ha = _auth(token_factory, subj.a)
    p1 = await _create_process(app_client, ha)
    opp = await app_client.post(
        "/api/v1/risks",
        headers=ha,
        json={
            "type": "opportunity",
            "description": "an opportunity to pursue",
            "likelihood": 2,
            "severity": 2,
            "process_id": p1["id"],
        },
    )
    assert opp.status_code == 201, opp.text
    rejected = await app_client.post(f"/api/v1/risks/{opp.json()['id']}/capa", headers=ha)
    assert rejected.status_code == 422, rejected.text


async def test_direct_capa_raise_rejects_risk_source(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """Codex P2: ``risk`` is a spawn-only origin tag. A direct POST /capas with source=risk is
    rejected (422) so a risk-originated CAPA can only be minted through the spawn endpoint (with its
    linked_capa_id latch + RISK_SPAWNED_CAPA audit + a real risk back-pointer). A normal source
    still raises."""
    await _grant(subj.a, "capa.create")
    ha = _auth(token_factory, subj.a)
    rejected = await app_client.post(
        "/api/v1/capas", headers=ha, json={"title": "sneaky", "severity": "Major", "source": "risk"}
    )
    assert rejected.status_code == 422, rejected.text
    ok = await app_client.post(
        "/api/v1/capas",
        headers=ha,
        json={"title": "legit", "severity": "Minor", "source": "process"},
    )
    assert ok.status_code == 201, ok.text
