"""S-dcr-5 integration proofs — DCR implement/close + the obsoletion gate + the CAPA→DCR loop.

DCR-as-orchestrator: ``implement`` drives the vault action for the change_type, atomically with the
FSM flip. REVISE/CREATE schedule the cutover (``effective_from`` set) which the ``release_due``
sweep then performs; RETIRE obsoletes the target behind the doc 05 §7.3 gate. The implement endpoint
enforces ``changeRequest.implement`` AND the underlying ``document.release`` / ``document.obsolete``
(SoD-2), so the author of a revision cannot self-implement it. Assertions are scoped to this run's
own ids (the integration suite shares one session DB).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models._capa_enums import CapaCloseState
from easysynq_api.db.models._vault_enums import DocumentCurrentState, VersionState
from easysynq_api.db.models.capa import Capa
from easysynq_api.db.models.document_version import DocumentVersion
from easysynq_api.db.models.documented_information import DocumentedInformation
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.tasks.lifecycle import release_due_versions

from . import s5_helpers as s5
from .test_capa import _assign_seeded_role, _my_pending_task
from .test_dcr import _auth, _grant, _subject

pytestmark = pytest.mark.integration

_DCR_DRIVER_PERMS = (
    "changeRequest.create",
    "changeRequest.read",
    "changeRequest.assess",
    "changeRequest.route",
    "changeRequest.close",
)


async def _drive_dcr_to_approved(
    client: AsyncClient,
    h_req: dict[str, str],
    h_qms: dict[str, str],
    *,
    change_type: str,
    target_document_id: str | None = None,
) -> str:
    """Raise a MINOR DCR, assess, route, and clear the single QMS-Owner approval → Approved id."""
    payload: dict[str, object] = {
        "change_type": change_type,
        "change_significance": "MINOR",
        "reason_class": "process_improvement",
        "reason_text": f"{change_type} via dcr",
    }
    if target_document_id is not None:
        payload["target_document_id"] = target_document_id
    r = await client.post("/api/v1/dcrs", headers=h_req, json=payload)
    assert r.status_code == 201, r.text
    dcr_id = r.json()["id"]
    assert (await client.post(f"/api/v1/dcrs/{dcr_id}/assess", headers=h_req)).status_code == 200
    iid = (await client.post(f"/api/v1/dcrs/{dcr_id}/route", headers=h_req)).json()[
        "approval_instance"
    ]["id"]
    task_id = await _my_pending_task(client, h_qms, iid)
    dec = await client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=h_qms, json={"outcome": "approve"}
    )
    assert dec.status_code == 200, dec.text
    assert dec.json()["dcr_state"] == "Approved"
    return str(dcr_id)


async def test_revise_implement_then_sweep_then_close(
    app_client: AsyncClient, token_factory: Callable[..., str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(release_due_versions, "delay", lambda *a, **k: None)
    author = _subject("rev-author")
    await s5.grant_lifecycle(author)
    ha = _auth(token_factory, author)
    approver = _subject("rev-approver")
    await s5.grant_role(approver, "Approver")
    hb = _auth(token_factory, approver)
    did = await s5.drive_to_approved(app_client, ha, hb, await s5.type_id("SOP"), b"revise-content")

    req = _subject("rev-req")
    await _grant(req, _DCR_DRIVER_PERMS)
    hreq = _auth(token_factory, req)
    qms = _subject("rev-qms")
    await _assign_seeded_role(qms, "QMS Owner")
    hq = _auth(token_factory, qms)
    dcr_id = await _drive_dcr_to_approved(
        app_client, hreq, hq, change_type="REVISE", target_document_id=did
    )

    # The implementer is a THIRD user holding document.release (lifecycle) + changeRequest.implement
    # — ≠ the version author + not the doc's approver, so SoD-2 passes.
    impl = _subject("rev-impl")
    await s5.grant_lifecycle(impl)
    await _grant(impl, ("changeRequest.implement",))
    himpl = _auth(token_factory, impl)

    r = await app_client.post(f"/api/v1/dcrs/{dcr_id}/implement", headers=himpl, json={})
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "Implemented"
    rv = r.json()["resulting_version_id"]
    assert rv

    # Closing before the cutover sweep runs is blocked — the change is scheduled, not yet Effective.
    early = await app_client.post(f"/api/v1/dcrs/{dcr_id}/close", headers=hreq)
    assert early.status_code == 409, early.text
    assert early.json()["code"] == "dcr_effectivity_pending"

    from easysynq_api.services.vault import release_due

    released = await release_due()
    assert uuid.UUID(rv) in released
    assert (await s5.get_version(rv)).version_state is VersionState.Effective

    c = await app_client.post(f"/api/v1/dcrs/{dcr_id}/close", headers=hreq)
    assert c.status_code == 200, c.text
    assert c.json()["state"] == "Closed"


async def test_revise_implement_by_author_is_sod2_blocked(
    app_client: AsyncClient, token_factory: Callable[..., str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(release_due_versions, "delay", lambda *a, **k: None)
    author = _subject("sod-author")
    await s5.grant_lifecycle(author)  # holds document.release
    await _grant(author, ("changeRequest.implement",))
    ha = _auth(token_factory, author)
    approver = _subject("sod-approver")
    await s5.grant_role(approver, "Approver")
    hb = _auth(token_factory, approver)
    did = await s5.drive_to_approved(app_client, ha, hb, await s5.type_id("SOP"), b"sod-content")

    req = _subject("sod-req")
    await _grant(req, _DCR_DRIVER_PERMS)
    hreq = _auth(token_factory, req)
    qms = _subject("sod-qms")
    await _assign_seeded_role(qms, "QMS Owner")
    hq = _auth(token_factory, qms)
    dcr_id = await _drive_dcr_to_approved(
        app_client, hreq, hq, change_type="REVISE", target_document_id=did
    )

    # The author holds changeRequest.implement AND document.release, but is the version's author →
    # the SoD-2 overlay (fired via document.release) HARD-DENIES the self-release. No DCR side-door.
    r = await app_client.post(f"/api/v1/dcrs/{dcr_id}/implement", headers=ha, json={})
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "sod_violation"
    # The DCR stays Approved (the flip never committed).
    assert (await app_client.get(f"/api/v1/dcrs/{dcr_id}", headers=hreq)).json()["state"] == (
        "Approved"
    )


async def test_retire_implement_obsoletes_and_closes(
    app_client: AsyncClient, token_factory: Callable[..., str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(release_due_versions, "delay", lambda *a, **k: None)
    author = _subject("ret-author")
    await s5.grant_lifecycle(author)
    ha = _auth(token_factory, author)
    approver = _subject("ret-approver")
    await s5.grant_role(approver, "Approver")
    hb = _auth(token_factory, approver)
    # A dedicated releaser (≠ author, ≠ approver) so SoD-2 passes regardless of the org-wide
    # allow_approver_release flag (which other tests toggle in the shared session DB).
    releaser = _subject("ret-releaser")
    await s5.grant_lifecycle(releaser)
    hrel = _auth(token_factory, releaser)
    did = (
        await s5.drive_to_effective(
            app_client, ha, hb, hrel, await s5.type_id("SOP"), b"retire-content"
        )
    )["id"]

    req = _subject("ret-req")
    await _grant(req, _DCR_DRIVER_PERMS)
    hreq = _auth(token_factory, req)
    qms = _subject("ret-qms")
    await _assign_seeded_role(qms, "QMS Owner")
    hq = _auth(token_factory, qms)
    dcr_id = await _drive_dcr_to_approved(
        app_client, hreq, hq, change_type="RETIRE", target_document_id=did
    )

    impl = _subject("ret-impl")
    await s5.grant_lifecycle(impl)  # holds document.obsolete
    await _grant(impl, ("changeRequest.implement",))
    himpl = _auth(token_factory, impl)

    r = await app_client.post(f"/api/v1/dcrs/{dcr_id}/implement", headers=himpl, json={})
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "Implemented"
    async with get_sessionmaker()() as s:
        doc = await s.get(DocumentedInformation, uuid.UUID(did))
        assert doc is not None and doc.current_state is DocumentCurrentState.Obsolete

    c = await app_client.post(f"/api/v1/dcrs/{dcr_id}/close", headers=hreq)
    assert c.status_code == 200, c.text
    assert c.json()["state"] == "Closed"


async def test_direct_obsolete_blocked_by_effective_referencer(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The §7.3 gate now fires on the SHIPPED ``document.obsolete`` endpoint too (S-dcr-5, owner
    decision): an Effective document referencing the target blocks obsoletion unless force_retire +
    a recorded justification."""
    author = _subject("gate-author")
    await s5.grant_lifecycle(author)  # document.obsolete + document.manage_metadata
    ha = _auth(token_factory, author)
    approver = _subject("gate-approver")
    await s5.grant_role(approver, "Approver")
    hb = _auth(token_factory, approver)
    releaser = _subject("gate-releaser")  # ≠ author/approver → SoD-2 passes (flag-independent)
    await s5.grant_lifecycle(releaser)
    hrel = _auth(token_factory, releaser)
    doc_a = (
        await s5.drive_to_effective(app_client, ha, hb, hrel, await s5.type_id("SOP"), b"gate-A")
    )["id"]
    doc_b = (
        await s5.drive_to_effective(app_client, ha, hb, hrel, await s5.type_id("SOP"), b"gate-B")
    )["id"]
    # A (Effective) references B → B is referenced_by an Effective document.
    link = await app_client.post(
        f"/api/v1/documents/{doc_a}/links",
        headers=ha,
        json={"to_document_id": doc_b, "link_type": "references"},
    )
    assert link.status_code == 201, link.text

    blocked = await app_client.post(
        f"/api/v1/documents/{doc_b}/obsolete", headers=ha, json={"reason": "retire B"}
    )
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["code"] == "obsoletion_blocked"
    assert any(e["code"] == "referenced_by_effective" for e in blocked.json().get("errors", []))

    # force_retire without a justification → 422.
    no_just = await app_client.post(
        f"/api/v1/documents/{doc_b}/obsolete",
        headers=ha,
        json={"reason": "retire B", "force_retire": True},
    )
    assert no_just.status_code == 422, no_just.text

    # force_retire + justification → obsoleted.
    forced = await app_client.post(
        f"/api/v1/documents/{doc_b}/obsolete",
        headers=ha,
        json={
            "reason": "retire B",
            "force_retire": True,
            "override_justification": "B replaced out-of-band",
        },
    )
    assert forced.status_code == 200, forced.text


async def test_capa_spawn_dcr_idempotent_and_one_to_many(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    sub = _subject("capa-spawn")
    await _grant(sub, ("capa.create", "changeRequest.create", "changeRequest.read"))
    h = _auth(token_factory, sub)
    capa = (
        await app_client.post(
            "/api/v1/capas", headers=h, json={"title": "Spawn source", "severity": "Minor"}
        )
    ).json()
    capa_id = capa["id"]

    body = {
        "change_type": "CREATE",
        "change_significance": "MINOR",
        "reason_text": "document the fix",
    }
    key = uuid.uuid4().hex
    hk = {**h, "Idempotency-Key": key}

    first = await app_client.post(f"/api/v1/capas/{capa_id}/raise-dcr", headers=hk, json=body)
    assert first.status_code == 201, first.text
    assert first.json()["source_link_type"] == "capa"
    assert first.json()["source_link_id"] == capa_id
    assert first.json()["reason_class"] == "capa"

    # Same Idempotency-Key → 200 replay, same DCR (no duplicate).
    replay = await app_client.post(f"/api/v1/capas/{capa_id}/raise-dcr", headers=hk, json=body)
    assert replay.status_code == 200, replay.text
    assert replay.json()["id"] == first.json()["id"]

    # No key → a fresh DCR (a CAPA may spawn child DCRs — 1:N).
    again = await app_client.post(f"/api/v1/capas/{capa_id}/raise-dcr", headers=h, json=body)
    assert again.status_code == 201, again.text
    assert again.json()["id"] != first.json()["id"]


async def _revise_to_implemented(
    client: AsyncClient, token_factory: Callable[..., str], prefix: str
) -> tuple[str, str, dict[str, str]]:
    """Author a doc → Approved, raise + approve a REVISE DCR, implement it (scheduled cutover).
    Returns (dcr_id, resulting_version_id, requester-headers); the version is Approved + scheduled,
    NOT yet swept. The caller must have monkeypatched release_due_versions.delay."""
    author = _subject(f"{prefix}-author")
    await s5.grant_lifecycle(author)
    ha = _auth(token_factory, author)
    approver = _subject(f"{prefix}-approver")
    await s5.grant_role(approver, "Approver")
    hb = _auth(token_factory, approver)
    did = await s5.drive_to_approved(
        client, ha, hb, await s5.type_id("SOP"), f"{prefix}-c".encode()
    )
    req = _subject(f"{prefix}-req")
    await _grant(req, _DCR_DRIVER_PERMS)
    hreq = _auth(token_factory, req)
    qms = _subject(f"{prefix}-qms")
    await _assign_seeded_role(qms, "QMS Owner")
    hq = _auth(token_factory, qms)
    dcr_id = await _drive_dcr_to_approved(
        client, hreq, hq, change_type="REVISE", target_document_id=did
    )
    impl = _subject(f"{prefix}-impl")
    await s5.grant_lifecycle(impl)
    await _grant(impl, ("changeRequest.implement",))
    himpl = _auth(token_factory, impl)
    r = await client.post(f"/api/v1/dcrs/{dcr_id}/implement", headers=himpl, json={})
    assert r.status_code == 200, r.text
    return dcr_id, r.json()["resulting_version_id"], hreq


async def test_create_dcr_implement_then_sweep_then_close(
    app_client: AsyncClient, token_factory: Callable[..., str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CREATE branch: the new document is authored out-of-band → Approved; the DCR releases that
    version (no prior Effective → INV-1 trivially holds at the cutover)."""
    monkeypatch.setattr(release_due_versions, "delay", lambda *a, **k: None)
    author = _subject("cre-author")
    await s5.grant_lifecycle(author)
    ha = _auth(token_factory, author)
    approver = _subject("cre-approver")
    await s5.grant_role(approver, "Approver")
    hb = _auth(token_factory, approver)
    did = await s5.drive_to_approved(app_client, ha, hb, await s5.type_id("SOP"), b"create-content")
    async with get_sessionmaker()() as s:
        rvid = str(
            (
                await s.execute(
                    select(DocumentVersion.id)
                    .where(DocumentVersion.document_id == uuid.UUID(did))
                    .order_by(DocumentVersion.version_seq.desc())
                    .limit(1)
                )
            ).scalar_one()
        )

    req = _subject("cre-req")
    await _grant(req, _DCR_DRIVER_PERMS)
    hreq = _auth(token_factory, req)
    qms = _subject("cre-qms")
    await _assign_seeded_role(qms, "QMS Owner")
    hq = _auth(token_factory, qms)
    dcr_id = await _drive_dcr_to_approved(app_client, hreq, hq, change_type="CREATE")

    impl = _subject("cre-impl")
    await s5.grant_lifecycle(impl)
    await _grant(impl, ("changeRequest.implement",))
    himpl = _auth(token_factory, impl)
    r = await app_client.post(
        f"/api/v1/dcrs/{dcr_id}/implement", headers=himpl, json={"resulting_version_id": rvid}
    )
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "Implemented"
    assert r.json()["resulting_version_id"] == rvid
    async with get_sessionmaker()() as s:
        v = await s.get(DocumentVersion, uuid.UUID(rvid))
        assert v is not None and v.dcr_id is not None and str(v.dcr_id) == dcr_id

    from easysynq_api.services.vault import release_due

    assert uuid.UUID(rvid) in await release_due()
    assert (await s5.get_version(rvid)).version_state is VersionState.Effective
    c = await app_client.post(f"/api/v1/dcrs/{dcr_id}/close", headers=hreq)
    assert c.status_code == 200, c.text
    assert c.json()["state"] == "Closed"


async def test_retire_implement_blocked_by_referencer_then_force(
    app_client: AsyncClient, token_factory: Callable[..., str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blocked RETIRE implement is a 409 AND the DCR flip rolls back (stays Approved) — the
    atomic-rollback proof; force_retire + a justification then overrides."""
    monkeypatch.setattr(release_due_versions, "delay", lambda *a, **k: None)
    author = _subject("rb-author")
    await s5.grant_lifecycle(author)
    ha = _auth(token_factory, author)
    approver = _subject("rb-approver")
    await s5.grant_role(approver, "Approver")
    hb = _auth(token_factory, approver)
    releaser = _subject("rb-rel")
    await s5.grant_lifecycle(releaser)
    hrel = _auth(token_factory, releaser)
    doc_a = (
        await s5.drive_to_effective(app_client, ha, hb, hrel, await s5.type_id("SOP"), b"rb-A")
    )["id"]
    doc_b = (
        await s5.drive_to_effective(app_client, ha, hb, hrel, await s5.type_id("SOP"), b"rb-B")
    )["id"]
    link = await app_client.post(
        f"/api/v1/documents/{doc_a}/links",
        headers=ha,
        json={"to_document_id": doc_b, "link_type": "references"},
    )
    assert link.status_code == 201, link.text

    req = _subject("rb-req")
    await _grant(req, _DCR_DRIVER_PERMS)
    hreq = _auth(token_factory, req)
    qms = _subject("rb-qms")
    await _assign_seeded_role(qms, "QMS Owner")
    hq = _auth(token_factory, qms)
    dcr_id = await _drive_dcr_to_approved(
        app_client, hreq, hq, change_type="RETIRE", target_document_id=doc_b
    )
    impl = _subject("rb-impl")
    await s5.grant_lifecycle(impl)
    await _grant(impl, ("changeRequest.implement",))
    himpl = _auth(token_factory, impl)

    blocked = await app_client.post(f"/api/v1/dcrs/{dcr_id}/implement", headers=himpl, json={})
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["code"] == "obsoletion_blocked"
    assert (await app_client.get(f"/api/v1/dcrs/{dcr_id}", headers=hreq)).json()["state"] == (
        "Approved"
    )

    forced = await app_client.post(
        f"/api/v1/dcrs/{dcr_id}/implement",
        headers=himpl,
        json={"force_retire": True, "override_justification": "B replaced out-of-band"},
    )
    assert forced.status_code == 200, forced.text
    assert forced.json()["state"] == "Implemented"
    async with get_sessionmaker()() as s:
        b = await s.get(DocumentedInformation, uuid.UUID(doc_b))
        assert b is not None and b.current_state is DocumentCurrentState.Obsolete


async def test_revise_close_succeeds_after_resulting_version_superseded(
    app_client: AsyncClient, token_factory: Callable[..., str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The close gate accepts a resulting version that went Effective and was THEN superseded by a
    later revision — the change took effect, so the DCR must still be closable (not 409 forever)."""
    monkeypatch.setattr(release_due_versions, "delay", lambda *a, **k: None)
    dcr_id, rv, hreq = await _revise_to_implemented(app_client, token_factory, "sup")
    from easysynq_api.services.vault import release_due

    await release_due()  # the resulting version → Effective
    # A subsequent revision+release would supersede it; set that state directly for the gate test.
    async with get_sessionmaker()() as s:
        v = await s.get(DocumentVersion, uuid.UUID(rv))
        assert v is not None
        v.version_state = VersionState.Superseded
        await s.commit()
    c = await app_client.post(f"/api/v1/dcrs/{dcr_id}/close", headers=hreq)
    assert c.status_code == 200, c.text
    assert c.json()["state"] == "Closed"


async def test_second_revise_dcr_cannot_reclaim_linked_version(
    app_client: AsyncClient, token_factory: Callable[..., str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two REVISE DCRs on one document: the first implement claims the Approved version; the second
    cannot re-link it (409 version_already_linked) — the cross-FK double-claim guard."""
    monkeypatch.setattr(release_due_versions, "delay", lambda *a, **k: None)
    author = _subject("dc-author")
    await s5.grant_lifecycle(author)
    ha = _auth(token_factory, author)
    approver = _subject("dc-approver")
    await s5.grant_role(approver, "Approver")
    hb = _auth(token_factory, approver)
    did = await s5.drive_to_approved(app_client, ha, hb, await s5.type_id("SOP"), b"dc-content")
    req = _subject("dc-req")
    await _grant(req, _DCR_DRIVER_PERMS)
    hreq = _auth(token_factory, req)
    qms = _subject("dc-qms")
    await _assign_seeded_role(qms, "QMS Owner")
    hq = _auth(token_factory, qms)
    dcr1 = await _drive_dcr_to_approved(
        app_client, hreq, hq, change_type="REVISE", target_document_id=did
    )
    dcr2 = await _drive_dcr_to_approved(
        app_client, hreq, hq, change_type="REVISE", target_document_id=did
    )
    impl = _subject("dc-impl")
    await s5.grant_lifecycle(impl)
    await _grant(impl, ("changeRequest.implement",))
    himpl = _auth(token_factory, impl)

    assert (
        await app_client.post(f"/api/v1/dcrs/{dcr1}/implement", headers=himpl, json={})
    ).status_code == 200
    second = await app_client.post(f"/api/v1/dcrs/{dcr2}/implement", headers=himpl, json={})
    assert second.status_code == 409, second.text
    assert second.json()["code"] == "version_already_linked"


async def test_spawn_from_terminal_capa_is_409(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    sub = _subject("term")
    await _grant(sub, ("capa.create", "changeRequest.create", "changeRequest.read"))
    h = _auth(token_factory, sub)
    capa_id = (
        await app_client.post(
            "/api/v1/capas", headers=h, json={"title": "Terminal", "severity": "Minor"}
        )
    ).json()["id"]
    async with get_sessionmaker()() as s:
        capa = await s.get(Capa, uuid.UUID(capa_id))
        assert capa is not None
        capa.close_state = CapaCloseState.Rejected
        await s.commit()
    r = await app_client.post(
        f"/api/v1/capas/{capa_id}/raise-dcr",
        headers=h,
        json={"change_type": "CREATE", "change_significance": "MINOR", "reason_text": "x"},
    )
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "capa_terminal"
