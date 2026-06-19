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

from . import s5_helpers as s5
from .test_processes import _create_process, _link_doc_to_process
from .test_records import _capture, _grant, _subject, _upload_evidence
from .test_vault import _auth, _create, _ensure_user

pytestmark = pytest.mark.integration


async def _grant_scoped(subject: str, key: str, *, level: ScopeLevel, selector: dict) -> uuid.UUID:
    """Mint a scoped permission override (no SYSTEM) — the direct-grant precedent (test_processes
    ``:assignment_process`` / ``_assign_role_bound``)."""
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        perm = (await s.execute(select(Permission).where(Permission.key == key))).scalar_one()
        scope = Scope(org_id=user.org_id, level=level, selector=selector)
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


async def _grant_process(subject: str, key: str, *process_ids: str) -> uuid.UUID:
    """A PROCESS-scoped override over the given process ids."""
    return await _grant_scoped(
        subject, key, level=ScopeLevel.PROCESS, selector={"process_ids": list(process_ids)}
    )


async def _grant_artifact(subject: str, key: str, artifact_id: str) -> uuid.UUID:
    """An ARTIFACT-scoped override on one record (for the AZ-INV-8 not-over-blocked proof)."""
    return await _grant_scoped(
        subject, key, level=ScopeLevel.ARTIFACT, selector={"artifact_id": artifact_id}
    )


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


async def _correct(client: AsyncClient, h: dict[str, str], record_id: str) -> str:
    """Capture a source-less correction of ``record_id``; returns the successor id."""
    sha = await _upload_evidence(client, h, f"corr-{uuid.uuid4().hex}".encode())
    r = await client.post(
        f"/api/v1/records/{record_id}/correction",
        headers=h,
        json={
            "record_type": "EVIDENCE",
            "title": "Corrected",
            "evidence": [{"sha256": sha, "content_type": "application/pdf"}],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_correction_chain_two_hops_keeps_visibility(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The R3-1 walk recovers process visibility across MULTIPLE hops (a correction of a correction)
    — the visited-set walk has no arbitrary hop cap that would drop it (Codex CX-3)."""
    author = _subject("rc2-a")
    await _grant(author, _AUTHOR_PERMS)
    ha = _auth(token_factory, author)
    p1 = await _create_process(app_client, ha)
    original = await _capture_evidence(app_client, ha)
    await _link_process(app_client, ha, original["id"], p1["id"])  # leg A → {P1}
    s1 = await _correct(app_client, ha, original["id"])  # source-less successor (empty own union)
    s2 = await _correct(app_client, ha, s1)  # correction of the correction (2 hops to {P1})

    owner = _subject("rc2-b")
    await _grant_process(owner, "record.read", p1["id"])
    hb = _auth(token_factory, owner)
    assert (await app_client.get(f"/api/v1/records/{s2}", headers=hb)).status_code == 200


# --- Slice W: Process-Owner record authoring (write-enable) + target re-auth -------------


async def test_process_owner_evidence_link_reauths_target(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """S-records-W: a Process-Owner of {P1, P1b} reaches the evidence-link write on a record bound
    to P1 and CAN link it to another OWNED process (P1b), but the target re-auth DENIES linking it
    to an unowned P2. A SYSTEM author links to P2 unchanged (byte-identical)."""
    author = _subject("wel-a")
    await _grant(author, _AUTHOR_PERMS)
    ha = _auth(token_factory, author)
    p1 = await _create_process(app_client, ha)
    p1b = await _create_process(app_client, ha)
    p2 = await _create_process(app_client, ha)
    rec = await _capture_evidence(app_client, ha)
    await _link_process(app_client, ha, rec["id"], p1["id"])  # bind R to P1 (author, leg A)

    owner = _subject("wel-b")
    await _grant_process(owner, "record.create", p1["id"], p1b["id"])  # owns P1, P1b — NOT P2
    hb = _auth(token_factory, owner)

    owned = await app_client.post(
        f"/api/v1/records/{rec['id']}/evidence-links",
        headers=hb,
        json={"target_type": "process", "target_id": p1b["id"]},
    )
    assert owned.status_code == 201, owned.text
    unowned = await app_client.post(
        f"/api/v1/records/{rec['id']}/evidence-links",
        headers=hb,
        json={"target_type": "process", "target_id": p2["id"]},
    )
    assert unowned.status_code == 403, unowned.text
    sys_link = await app_client.post(
        f"/api/v1/records/{rec['id']}/evidence-links",
        headers=ha,
        json={"target_type": "process", "target_id": p2["id"]},
    )
    assert sys_link.status_code == 201, sys_link.text


async def test_artifact_holder_not_over_blocked_on_link(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """AZ-INV-8 / R2-1: an ARTIFACT-scoped ``record.create`` holder of the record can evidence-link
    it to ANY process — the re-auth preserves the record's ``artifact_id``, so the artifact grant
    still matches after ``process_ids`` is replaced with the target (not over-blocked)."""
    author = _subject("wart-a")
    await _grant(author, _AUTHOR_PERMS)
    ha = _auth(token_factory, author)
    p2 = await _create_process(app_client, ha)
    rec = await _capture_evidence(app_client, ha)

    holder = _subject("wart-b")
    await _grant_artifact(holder, "record.create", rec["id"])  # ARTIFACT-scoped on this record
    hh = _auth(token_factory, holder)
    linked = await app_client.post(
        f"/api/v1/records/{rec['id']}/evidence-links",
        headers=hh,
        json={"target_type": "process", "target_id": p2["id"]},
    )
    assert linked.status_code == 201, linked.text


async def test_process_owner_cannot_unlink_unowned_process(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """S-records-W: the evidence-link DELETE re-auths the link's TARGET — a Process-Owner of P1 on a
    record bound to P1+P2 CANNOT remove its link to the unowned P2 (403), but CAN remove its link to
    P1 (204)."""
    author = _subject("wdl-a")
    await _grant(author, _AUTHOR_PERMS)
    ha = _auth(token_factory, author)
    p1 = await _create_process(app_client, ha)
    p2 = await _create_process(app_client, ha)
    rec = await _capture_evidence(app_client, ha)
    await _link_process(app_client, ha, rec["id"], p1["id"])
    await _link_process(app_client, ha, rec["id"], p2["id"])  # R bound to P1 AND P2
    links = (await app_client.get(f"/api/v1/records/{rec['id']}/evidence-links", headers=ha)).json()
    link_p1 = next(link["id"] for link in links if link["target_id"] == p1["id"])
    link_p2 = next(link["id"] for link in links if link["target_id"] == p2["id"])

    owner = _subject("wdl-b")
    await _grant_process(owner, "record.create", p1["id"])  # owns P1 only
    hb = _auth(token_factory, owner)
    deny = await app_client.delete(
        f"/api/v1/records/{rec['id']}/evidence-links/{link_p2}", headers=hb
    )
    assert deny.status_code == 403, deny.text
    allow = await app_client.delete(
        f"/api/v1/records/{rec['id']}/evidence-links/{link_p1}", headers=hb
    )
    assert allow.status_code == 204, allow.text


async def test_process_owner_correction_cannot_introduce_unowned_source(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """S-records-W / R2-3: correcting a source-LESS record re-auths the body source PER-PROCESS, so
    a Process-Owner of P1 cannot introduce a source linked to P1+P2 (the per-process loop denies P2
    even though the intersection-match passes on P1 — W-CX-1) NOR a source with NO process links
    (the empty re-auth denies a process-only holder, like a fresh capture — W-CX-2). 403 before
    ``capture_correction`` runs."""
    author = _subject("wcorr-a")
    await _grant(author, _AUTHOR_PERMS)
    await s5.grant_lifecycle(author)  # document.create + manage_metadata for the source docs
    ha = _auth(token_factory, author)
    p1 = await _create_process(app_client, ha)
    p2 = await _create_process(app_client, ha)
    original = await _capture_evidence(app_client, ha)  # source-LESS
    await _link_process(app_client, ha, original["id"], p1["id"])  # owner reaches the correction
    shared = await _create(app_client, ha, await s5.type_id("SOP"))  # source spanning P1 + P2
    await _link_doc_to_process(app_client, ha, shared["id"], p1["id"])
    await _link_doc_to_process(app_client, ha, shared["id"], p2["id"])
    processless = await _create(app_client, ha, await s5.type_id("SOP"))  # source with NO links

    owner = _subject("wcorr-b")
    await _grant_process(owner, "record.create", p1["id"])  # owns P1, NOT P2
    hb = _auth(token_factory, owner)
    # The author stages evidence (init-upload is SYSTEM record.create); the re-auth 403s first.
    sha = await _upload_evidence(app_client, ha, f"corr-{uuid.uuid4().hex}".encode())
    for source_id, label in ((shared["id"], "P1+P2 source"), (processless["id"], "process-less")):
        deny = await app_client.post(
            f"/api/v1/records/{original['id']}/correction",
            headers=hb,
            json={
                "record_type": "EVIDENCE",
                "title": "smuggled source",
                "source_document_id": source_id,
                "evidence": [{"sha256": sha, "content_type": "application/pdf"}],
            },
        )
        assert deny.status_code == 403, f"{label}: {deny.text}"


async def test_process_owner_cannot_capture_under_unowned_shared_doc(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """S-records-W (spec §6/§8): capture re-auths EACH of the source doc's processes — a
    Process-Owner of P1 cannot capture a record under a doc linked to P1+P2 (the successor would
    inherit the unowned P2 via leg B). The per-process loop 403s on P2 even though the base
    intersection-match passes on P1 (this would 201 without the loop)."""
    author = _subject("wcap-a")
    await _grant(author, _AUTHOR_PERMS)
    await s5.grant_lifecycle(author)  # document.create + manage_metadata for the shared source doc
    ha = _auth(token_factory, author)
    p1 = await _create_process(app_client, ha)
    p2 = await _create_process(app_client, ha)
    doc = await _create(app_client, ha, await s5.type_id("SOP"))
    await _link_doc_to_process(app_client, ha, doc["id"], p1["id"])
    await _link_doc_to_process(app_client, ha, doc["id"], p2["id"])  # D spans P1 + P2
    sha = await _upload_evidence(app_client, ha, f"cap-{uuid.uuid4().hex}".encode())

    owner = _subject("wcap-b")
    await _grant_process(owner, "record.create", p1["id"])  # owns P1, NOT P2
    hb = _auth(token_factory, owner)
    deny = await app_client.post(
        "/api/v1/records",
        headers=hb,
        json={
            "record_type": "EVIDENCE",
            "title": "shared-doc capture",
            "source_document_id": doc["id"],
            "evidence": [{"sha256": sha, "content_type": "application/pdf"}],
        },
    )
    assert deny.status_code == 403, deny.text
