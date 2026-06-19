"""S-records-R integration proofs — a bound Process-Owner reading records bound to their process.

The records READ gate now resolves a process-aware ``_record_read_scope`` (a record's leg-A
evidence-for-PROCESS links + leg-B source-doc process links + the R3-1 correction-chain fallback),
DECOUPLED from the per-record WRITE gates (still process-blind) so enabling reads never lets a
Process-Owner mint a binding (the spec's central result). A PROCESS-only ``record.read`` grant is
minted DIRECTLY (no SYSTEM override — a SYSTEM grant masks the gap, pdp.py:76-77). Assertions are
own-id-scoped (the shared session DB accumulates rows).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models.authz_grant import PermissionOverride
from easysynq_api.db.models.permission import Permission
from easysynq_api.db.models.scope import Scope
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.domain.authz.types import Effect, ScopeLevel

from .test_processes import _create_process
from .test_records import _capture, _grant, _subject, _upload_evidence
from .test_vault import _auth, _ensure_user

pytestmark = pytest.mark.integration


async def _grant_process(subject: str, key: str, process_id: str) -> uuid.UUID:
    """Mint a PROCESS-scoped override for ``key`` (no SYSTEM) — the direct-PROCESS-grant precedent
    (test_processes ``:assignment_process`` / ``_assign_role_bound``)."""
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        perm = (await s.execute(select(Permission).where(Permission.key == key))).scalar_one()
        scope = Scope(
            org_id=user.org_id, level=ScopeLevel.PROCESS, selector={"process_id": process_id}
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
        return user.id


async def _capture_evidence(client: AsyncClient, h: dict[str, str]) -> dict:
    """Capture an ad-hoc EVIDENCE record (no source doc → no leg-B binding)."""
    sha = await _upload_evidence(client, h, f"ev-{uuid.uuid4().hex}".encode())
    r = await _capture(
        client,
        h,
        record_type="EVIDENCE",
        title=f"R-{uuid.uuid4().hex[:6]}",
        evidence=[{"sha256": sha, "content_type": "application/pdf"}],
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _link_process(
    client: AsyncClient, h: dict[str, str], record_id: str, process_id: str
) -> None:
    r = await client.post(
        f"/api/v1/records/{record_id}/evidence-links",
        headers=h,
        json={"target_type": "process", "target_id": process_id},
    )
    assert r.status_code == 201, r.text


_AUTHOR_PERMS = ("record.read", "record.create", "process.create")


async def test_process_owner_reads_record_linked_to_owned_process(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A bound Process-Owner (PROCESS ``record.read`` {P1}) reads a record evidence-linked to P1
    (leg A) but 403s one linked to an unowned P2 — the decoupled read scope. A SYSTEM holder reads
    both (byte-identical)."""
    author = _subject("rps-a")
    await _grant(author, _AUTHOR_PERMS)
    ha = _auth(token_factory, author)
    p1 = await _create_process(app_client, ha)
    p2 = await _create_process(app_client, ha)
    r1 = await _capture_evidence(app_client, ha)
    r2 = await _capture_evidence(app_client, ha)
    await _link_process(app_client, ha, r1["id"], p1["id"])
    await _link_process(app_client, ha, r2["id"], p2["id"])

    owner = _subject("rps-b")
    await _grant_process(owner, "record.read", p1["id"])
    hb = _auth(token_factory, owner)

    assert (await app_client.get(f"/api/v1/records/{r1['id']}", headers=hb)).status_code == 200
    assert (await app_client.get(f"/api/v1/records/{r2['id']}", headers=hb)).status_code == 403
    # The SYSTEM author reads both unchanged.
    assert (await app_client.get(f"/api/v1/records/{r1['id']}", headers=ha)).status_code == 200
    assert (await app_client.get(f"/api/v1/records/{r2['id']}", headers=ha)).status_code == 200


async def test_process_owner_record_list_filters_by_process(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """GET /records narrows to the P1-owner's records (filter-not-403): the P1-linked record is
    shown, the P2-linked one hidden."""
    author = _subject("rpl-a")
    await _grant(author, _AUTHOR_PERMS)
    ha = _auth(token_factory, author)
    p1 = await _create_process(app_client, ha)
    p2 = await _create_process(app_client, ha)
    r1 = await _capture_evidence(app_client, ha)
    r2 = await _capture_evidence(app_client, ha)
    await _link_process(app_client, ha, r1["id"], p1["id"])
    await _link_process(app_client, ha, r2["id"], p2["id"])

    owner = _subject("rpl-b")
    await _grant_process(owner, "record.read", p1["id"])
    hb = _auth(token_factory, owner)

    listed = await app_client.get("/api/v1/records?limit=100", headers=hb)
    assert listed.status_code == 200, listed.text
    ids = {rec["id"] for rec in listed.json()}
    assert r1["id"] in ids
    assert r2["id"] not in ids


async def test_source_less_record_invisible_to_process_owner(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """An ad-hoc EVIDENCE record with NO process binding is invisible to a process-only owner
    (genuine absence / deny-by-default — not a bug)."""
    author = _subject("rsl-a")
    await _grant(author, _AUTHOR_PERMS)
    ha = _auth(token_factory, author)
    p1 = await _create_process(app_client, ha)
    unbound = await _capture_evidence(app_client, ha)  # no link

    owner = _subject("rsl-b")
    await _grant_process(owner, "record.read", p1["id"])
    hb = _auth(token_factory, owner)
    assert (await app_client.get(f"/api/v1/records/{unbound['id']}", headers=hb)).status_code == 403


async def test_correction_chain_keeps_process_visibility(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """R3-1: a source-less correction successor (no own binding) inherits its predecessor's process
    binding via ``correction_of``, so it stays visible to the process that owned the original."""
    author = _subject("rcc-a")
    await _grant(author, _AUTHOR_PERMS)
    ha = _auth(token_factory, author)
    p1 = await _create_process(app_client, ha)
    original = await _capture_evidence(app_client, ha)
    await _link_process(app_client, ha, original["id"], p1["id"])  # leg A → {P1}

    # Correct the original (ad-hoc → the successor copies no evidence link → empty own binding).
    sha = await _upload_evidence(app_client, ha, f"corr-{uuid.uuid4().hex}".encode())
    corr = await app_client.post(
        f"/api/v1/records/{original['id']}/correction",
        headers=ha,
        json={
            "record_type": "EVIDENCE",
            "title": "Corrected",
            "evidence": [{"sha256": sha, "content_type": "application/pdf"}],
        },
    )
    assert corr.status_code == 201, corr.text
    successor_id = corr.json()["id"]

    owner = _subject("rcc-b")
    await _grant_process(owner, "record.read", p1["id"])
    hb = _auth(token_factory, owner)
    # The successor has no OWN binding but inherits P1 via the correction_of walk → visible.
    assert (await app_client.get(f"/api/v1/records/{successor_id}", headers=hb)).status_code == 200


async def test_process_owner_read_does_not_enable_writes(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The decoupling (the spec's central invariant): an owner with BOTH PROCESS ``record.read`` AND
    PROCESS ``record.create`` {P1} can READ a P1-bound record but still 403s a per-record WRITE on
    it — the write gates stay process-blind (``_record_scope``), so enabling reads never enables
    authoring. (Process-Owner record authoring is Slice W.)"""
    author = _subject("rdw-a")
    await _grant(author, _AUTHOR_PERMS)
    ha = _auth(token_factory, author)
    p1 = await _create_process(app_client, ha)
    rec = await _capture_evidence(app_client, ha)
    await _link_process(app_client, ha, rec["id"], p1["id"])

    owner = _subject("rdw-b")
    await _grant_process(owner, "record.read", p1["id"])
    await _grant_process(owner, "record.create", p1["id"])  # PROCESS record.create too
    hb = _auth(token_factory, owner)

    # The read works (the enriched read scope matches the PROCESS grant)...
    assert (await app_client.get(f"/api/v1/records/{rec['id']}", headers=hb)).status_code == 200
    # ...but a per-record WRITE 403s at the process-BLIND _create_scoped gate, even with PROCESS
    # record.create {P1} — proving reads and writes are decoupled (this would 201 if coupled).
    link_attempt = await app_client.post(
        f"/api/v1/records/{rec['id']}/evidence-links",
        headers=hb,
        json={"target_type": "process", "target_id": p1["id"]},
    )
    assert link_attempt.status_code == 403, link_attempt.text
