"""S-process-scope-1 integration proofs — a bound Process Owner's PROCESS-scoped grant authorizing
END-TO-END across the auth surfaces that build their OWN ResourceContext.

S-owner-assignment-1 migrated ``_document_scope`` (document detail/list/edit) but left four surfaces
resolving a document/record scope WITHOUT ``process_ids`` — so the minted Process-Owner grant
mis-denied them. This slice threads ``process_ids`` (via the shared ``vault_repo`` loader) into:

* the workflow approval-history reads (``GET /workflow-instances/{id}`` + ``/{id}/approval``);
* the unified search row-filter (``/search`` + ``/search/suggest``);
* ``record.read``/list (a record inherits its SOURCE document's process links);

and adds the ``document.create`` write-path: ``DocumentCreate.process_ids`` links the new doc at
creation (atomically), with each link re-authorized against the TARGET process so a Process Owner of
P1 cannot smuggle a link to an unowned P2 (the S-owner-assignment-1 escalation class).

Every assertion is run-scoped (own ids / own tokens) — the shared session DB accumulates rows.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from types import SimpleNamespace

import pytest
from httpx import AsyncClient

from . import s5_helpers as s5
from .test_processes import _create_process, _grant, _link_doc_to_process, _user_id
from .test_vault import _auth, _create, _upload

pytestmark = pytest.mark.integration


@pytest.fixture
def subj() -> SimpleNamespace:
    salt = uuid.uuid4().hex[:10]
    return SimpleNamespace(a=f"kc-ps-a-{salt}", b=f"kc-ps-b-{salt}", c=f"kc-ps-c-{salt}")


async def _assign_owner(
    client: AsyncClient, h: dict[str, str], process_id: str, user_id: uuid.UUID
) -> None:
    r = await client.post(
        f"/api/v1/processes/{process_id}/owner", headers=h, json={"user_id": str(user_id)}
    )
    assert r.status_code == 201, r.text


async def _doc_id(client: AsyncClient, h: dict[str, str]) -> str:
    return (await _create(client, h, await s5.type_id("SOP")))["id"]


async def _doc_version_linked(
    client: AsyncClient, h: dict[str, str], process_id: str
) -> tuple[str, str]:
    """Create a doc, check in a first version, link it to ``process_id`` → (doc_id, version_id)."""
    did = await _doc_id(client, h)
    await client.post(f"/api/v1/documents/{did}/checkout", headers=h)
    sha = await _upload(client, h, did, f"src-{uuid.uuid4().hex}".encode())
    ci = await client.post(
        f"/api/v1/documents/{did}/checkin",
        headers=h,
        json={"sha256": sha, "change_reason": "v1", "change_significance": "MAJOR"},
    )
    assert ci.status_code == 201, ci.text
    await _link_doc_to_process(client, h, did, process_id)
    return did, ci.json()["id"]


# --- the document.create write-path -----------------------------------------------------


async def test_process_owner_creates_a_document_in_an_owned_process(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """A bound Process Owner can POST /documents with process_ids=[their process]: the doc is
    created AND linked atomically, and the owner can then read it (the link authorizes the read)."""
    await s5.grant_lifecycle(subj.a)
    await _grant(subj.a, "process.create")
    await _grant(subj.a, "process.assign_owner")
    ha = _auth(token_factory, subj.a)
    proc = await _create_process(app_client, ha)
    owner_id = await _user_id(subj.b)
    await _assign_owner(app_client, ha, proc["id"], owner_id)

    hb = _auth(token_factory, subj.b)
    r = await app_client.post(
        "/api/v1/documents",
        headers=hb,
        json={
            "title": "Owner SOP",
            "document_type_id": await s5.type_id("SOP"),
            "process_ids": [proc["id"]],
        },
    )
    assert r.status_code == 201, r.text
    new_id = r.json()["id"]

    # The doc is linked to the process (visible via the owner's own PROCESS-scoped read).
    links = await app_client.get(f"/api/v1/documents/{new_id}/process-links", headers=hb)
    assert links.status_code == 200, links.text
    assert {p["process_id"] for p in links.json()} == {proc["id"]}
    # ...and the owner can read the new doc (the link authorizes the PROCESS-scoped document.read).
    assert (await app_client.get(f"/api/v1/documents/{new_id}", headers=hb)).status_code == 200


async def test_process_owner_creates_with_multiple_owned_processes_deduped(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """A Process Owner of TWO processes can create a doc linked to both; a duplicated id in the body
    is deduped (no UNIQUE trip) and yields exactly the two distinct links."""
    await s5.grant_lifecycle(subj.a)
    await _grant(subj.a, "process.create")
    await _grant(subj.a, "process.assign_owner")
    ha = _auth(token_factory, subj.a)
    p1 = await _create_process(app_client, ha)
    p2 = await _create_process(app_client, ha)
    owner_id = await _user_id(subj.b)
    await _assign_owner(app_client, ha, p1["id"], owner_id)
    await _assign_owner(app_client, ha, p2["id"], owner_id)

    hb = _auth(token_factory, subj.b)
    r = await app_client.post(
        "/api/v1/documents",
        headers=hb,
        json={
            "title": "Two-process SOP",
            "document_type_id": await s5.type_id("SOP"),
            "process_ids": [p1["id"], p1["id"], p2["id"]],  # duplicate p1 → deduped
        },
    )
    assert r.status_code == 201, r.text
    links = await app_client.get(f"/api/v1/documents/{r.json()['id']}/process-links", headers=hb)
    assert links.status_code == 200, links.text
    assert {p["process_id"] for p in links.json()} == {p1["id"], p2["id"]}


async def test_process_owner_cannot_create_without_declaring_an_owned_process(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """A Process Owner's document.create is PROCESS-scoped: an unscoped create (no process_ids)
    carries no process_ids, so the grant matches nothing → 403 (deny-by-default)."""
    await s5.grant_lifecycle(subj.a)
    await _grant(subj.a, "process.create")
    await _grant(subj.a, "process.assign_owner")
    ha = _auth(token_factory, subj.a)
    proc = await _create_process(app_client, ha)
    owner_id = await _user_id(subj.b)
    await _assign_owner(app_client, ha, proc["id"], owner_id)

    hb = _auth(token_factory, subj.b)
    r = await app_client.post(
        "/api/v1/documents",
        headers=hb,
        json={"title": "Orphan", "document_type_id": await s5.type_id("SOP")},
    )
    assert r.status_code == 403, r.text


async def test_process_owner_cannot_link_an_unowned_process_at_create(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """The escalation guard: declaring an OWNED process (so the base document.create gate matches)
    PLUS an UNOWNED one must 403 — each declared link is re-authorized against the target process
    (document.manage_metadata), exactly as the standalone link endpoint does (S-owner-assign-1)."""
    await s5.grant_lifecycle(subj.a)
    await _grant(subj.a, "process.create")
    await _grant(subj.a, "process.assign_owner")
    ha = _auth(token_factory, subj.a)
    owned = await _create_process(app_client, ha)
    unowned = await _create_process(app_client, ha)
    owner_id = await _user_id(subj.b)
    await _assign_owner(app_client, ha, owned["id"], owner_id)

    hb = _auth(token_factory, subj.b)
    r = await app_client.post(
        "/api/v1/documents",
        headers=hb,
        json={
            "title": "Escalation attempt",
            "document_type_id": await s5.type_id("SOP"),
            "process_ids": [owned["id"], unowned["id"]],
        },
    )
    assert r.status_code == 403, r.text


async def test_create_with_unknown_process_is_422(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """A SYSTEM-create holder declaring a non-existent process gets a 422 (process validation runs
    once the base create gate passes)."""
    await s5.grant_lifecycle(subj.a)  # SYSTEM document.create + manage_metadata
    ha = _auth(token_factory, subj.a)
    r = await app_client.post(
        "/api/v1/documents",
        headers=ha,
        json={
            "title": "Bad link",
            "document_type_id": await s5.type_id("SOP"),
            "process_ids": [str(uuid.uuid4())],
        },
    )
    assert r.status_code == 422, r.text
    assert r.json()["errors"][0]["field"] == "process_ids"


async def test_create_holder_without_manage_metadata_cannot_link(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """A caller who can create but lacks document.manage_metadata (an Author) can still create an
    UNLINKED doc (byte-identical to the pre-slice path), but declaring a process 403s — the
    per-process manage_metadata gate (Authors hold no manage_metadata in the seeded role set)."""
    await s5.grant_lifecycle(subj.a)
    await _grant(subj.a, "process.create")
    ha = _auth(token_factory, subj.a)
    proc = await _create_process(app_client, ha)

    # subj.c: ONLY document.create (no manage_metadata).
    await _grant(subj.c, "document.create")
    hc = _auth(token_factory, subj.c)

    unlinked = await app_client.post(
        "/api/v1/documents",
        headers=hc,
        json={"title": "Unlinked", "document_type_id": await s5.type_id("SOP")},
    )
    assert unlinked.status_code == 201, unlinked.text

    with_link = await app_client.post(
        "/api/v1/documents",
        headers=hc,
        json={
            "title": "Wants a link",
            "document_type_id": await s5.type_id("SOP"),
            "process_ids": [proc["id"]],
        },
    )
    assert with_link.status_code == 403, with_link.text


# --- the workflow approval-history reads ------------------------------------------------


async def test_process_owner_reads_approval_history_of_a_linked_doc(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """A bound Process Owner can read the approval cycle (``/documents/{id}/approval``) and the
    workflow instance (``/workflow-instances/{id}``) of a doc linked to their process — both
    previously 403 (they built a doc context without process_ids). An UNLINKED doc stays 403."""
    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.c)  # the approver
    await _grant(subj.a, "process.create")
    await _grant(subj.a, "process.assign_owner")
    ha = _auth(token_factory, subj.a)
    hc = _auth(token_factory, subj.c)
    proc = await _create_process(app_client, ha)

    linked = await s5.drive_to_approved(app_client, ha, hc, await s5.type_id("SOP"), b"wf-linked")
    await _link_doc_to_process(app_client, ha, linked, proc["id"])
    unlinked = await s5.drive_to_approved(
        app_client, ha, hc, await s5.type_id("SOP"), b"wf-unlinked"
    )

    # The author can read the approval cycle → grab the instance id.
    appr = await app_client.get(f"/api/v1/documents/{linked}/approval", headers=ha)
    assert appr.status_code == 200, appr.text
    instance_id = appr.json()["id"]

    owner_id = await _user_id(subj.b)
    hb = _auth(token_factory, subj.b)
    # Before binding: the owner-to-be is denied both reads.
    assert (
        await app_client.get(f"/api/v1/documents/{linked}/approval", headers=hb)
    ).status_code == 403
    await _assign_owner(app_client, ha, proc["id"], owner_id)

    # After binding: both approval-history reads succeed on the LINKED doc.
    assert (
        await app_client.get(f"/api/v1/documents/{linked}/approval", headers=hb)
    ).status_code == 200
    assert (
        await app_client.get(f"/api/v1/workflow-instances/{instance_id}", headers=hb)
    ).status_code == 200
    # ...but the UNLINKED doc stays 403 (AZ-INV-8: the bound scope is narrow).
    assert (
        await app_client.get(f"/api/v1/documents/{unlinked}/approval", headers=hb)
    ).status_code == 403


# --- the search row-filter --------------------------------------------------------------


async def test_process_owner_search_sees_only_linked_effective_docs(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """The unified search row-filter now matches a PROCESS-scoped document.read: a bound Process
    Owner finds the Effective doc linked to their process and NOT an unlinked one (counted in
    ``hidden_by_scope``)."""
    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.c)  # approver + releaser
    await s5.set_approver_release(await s5.default_org_id(), True)  # releaser == approver (SoD-2)
    await _grant(subj.a, "process.create")
    await _grant(subj.a, "process.assign_owner")
    ha = _auth(token_factory, subj.a)
    hc = _auth(token_factory, subj.c)
    proc = await _create_process(app_client, ha)
    token = uuid.uuid4().hex[:8]

    linked = await s5.drive_to_effective(
        app_client, ha, hc, hc, await s5.type_id("SOP"), b"s-linked"
    )
    await _link_doc_to_process(app_client, ha, linked["id"], proc["id"])
    await app_client.patch(
        f"/api/v1/documents/{linked['id']}", headers=ha, json={"title": f"Zeta {token} Linked"}
    )
    unlinked = await s5.drive_to_effective(
        app_client, ha, hc, hc, await s5.type_id("SOP"), b"s-unlinked"
    )
    await app_client.patch(
        f"/api/v1/documents/{unlinked['id']}", headers=ha, json={"title": f"Zeta {token} Unlinked"}
    )

    owner_id = await _user_id(subj.b)
    await _assign_owner(app_client, ha, proc["id"], owner_id)
    hb = _auth(token_factory, subj.b)

    r = await app_client.get(f"/api/v1/search?q={token}", headers=hb)
    assert r.status_code == 200, r.text
    body = r.json()
    found_ids = {hit["id"] for hit in body["results"]}
    assert linked["id"] in found_ids
    assert unlinked["id"] not in found_ids
    assert body["hidden_by_scope"] >= 1


# --- record.read inheriting the source document's process scope -------------------------


async def test_process_owner_reads_records_under_a_linked_source_doc(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """A record inherits its SOURCE document's process links, so a bound Process Owner can read a
    record captured against a doc linked to their process (detail + list) — and NOT an ad-hoc
    record with no source document."""
    await s5.grant_lifecycle(subj.a)
    await _grant(subj.a, "record.create")
    await _grant(subj.a, "record.read")
    await _grant(subj.a, "process.create")
    await _grant(subj.a, "process.assign_owner")
    ha = _auth(token_factory, subj.a)
    proc = await _create_process(app_client, ha)

    # A controlled doc + a checked-in version, linked to the process; then capture a RELEASE record.
    did = await _doc_id(app_client, ha)
    await app_client.post(f"/api/v1/documents/{did}/checkout", headers=ha)

    sha = await _upload(app_client, ha, did, f"src-{uuid.uuid4().hex}".encode())
    ci = await app_client.post(
        f"/api/v1/documents/{did}/checkin",
        headers=ha,
        json={"sha256": sha, "change_reason": "v1", "change_significance": "MAJOR"},
    )
    assert ci.status_code == 201, ci.text
    version_id = ci.json()["id"]
    await _link_doc_to_process(app_client, ha, did, proc["id"])

    linked_rec = await app_client.post(
        "/api/v1/records",
        headers=ha,
        json={
            "record_type": "RELEASE",
            "title": "Linked record",
            "source_document_id": did,
            "source_version_id": version_id,
        },
    )
    assert linked_rec.status_code == 201, linked_rec.text
    linked_rid = linked_rec.json()["id"]

    adhoc_rec = await app_client.post(
        "/api/v1/records",
        headers=ha,
        json={"record_type": "EVIDENCE", "title": "Ad-hoc record"},
    )
    assert adhoc_rec.status_code == 201, adhoc_rec.text
    adhoc_rid = adhoc_rec.json()["id"]

    owner_id = await _user_id(subj.b)
    await _assign_owner(app_client, ha, proc["id"], owner_id)
    hb = _auth(token_factory, subj.b)

    # The owner reads the source-linked record (detail) but not the ad-hoc one.
    assert (await app_client.get(f"/api/v1/records/{linked_rid}", headers=hb)).status_code == 200
    assert (await app_client.get(f"/api/v1/records/{adhoc_rid}", headers=hb)).status_code == 403
    # ...and the list filter shows the linked record, hides the ad-hoc one.
    listed = await app_client.get("/api/v1/records", headers=hb)
    assert listed.status_code == 200, listed.text
    ids = {r["id"] for r in listed.json()}
    assert linked_rid in ids
    assert adhoc_rid not in ids


async def test_process_owner_reads_evidence_for_process_record(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """A source-LESS record bound to a process ONLY via an EvidenceForLink(PROCESS) is readable by
    that process's owner — the records read scope unions evidence-for-process links with source-doc
    links (matching the evidence-pack record.read gate; the S-process-scope-1 review P3)."""
    await s5.grant_lifecycle(subj.a)
    await _grant(subj.a, "record.create")
    await _grant(subj.a, "record.read")
    await _grant(subj.a, "process.create")
    await _grant(subj.a, "process.assign_owner")
    ha = _auth(token_factory, subj.a)
    proc = await _create_process(app_client, ha)

    rec = await app_client.post(
        "/api/v1/records",
        headers=ha,
        json={"record_type": "EVIDENCE", "title": "Evidence for a process"},
    )
    assert rec.status_code == 201, rec.text
    rid = rec.json()["id"]
    link = await app_client.post(
        f"/api/v1/records/{rid}/evidence-links",
        headers=ha,
        json={"target_type": "process", "target_id": proc["id"]},
    )
    assert link.status_code == 201, link.text

    owner_id = await _user_id(subj.b)
    await _assign_owner(app_client, ha, proc["id"], owner_id)
    hb = _auth(token_factory, subj.b)

    assert (await app_client.get(f"/api/v1/records/{rid}", headers=hb)).status_code == 200
    listed = await app_client.get("/api/v1/records", headers=hb)
    assert listed.status_code == 200, listed.text
    assert rid in {r["id"] for r in listed.json()}


async def test_evidence_link_reauthorizes_target_process(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """Binding a record AS EVIDENCE FOR a process makes it record.read-visible to that process — so
    the evidence-link endpoint re-authorizes the TARGET process. A Process Owner of P1+P3 can link a
    record (in their scope) to P3 but NOT to an unowned P2 (the Codex P1 over-grant)."""
    await s5.grant_lifecycle(subj.a)
    await _grant(subj.a, "record.create")
    await _grant(subj.a, "record.read")
    await _grant(subj.a, "process.create")
    await _grant(subj.a, "process.assign_owner")
    ha = _auth(token_factory, subj.a)
    p1 = await _create_process(app_client, ha)
    p2 = await _create_process(app_client, ha)
    p3 = await _create_process(app_client, ha)

    rec = await app_client.post(
        "/api/v1/records", headers=ha, json={"record_type": "EVIDENCE", "title": "Linkable"}
    )
    assert rec.status_code == 201, rec.text
    rid = rec.json()["id"]
    # SYSTEM-seed the record into P1's scope so a P1 owner can subsequently manage its links.
    seed = await app_client.post(
        f"/api/v1/records/{rid}/evidence-links",
        headers=ha,
        json={"target_type": "process", "target_id": p1["id"]},
    )
    assert seed.status_code == 201, seed.text

    owner_id = await _user_id(subj.b)
    await _assign_owner(app_client, ha, p1["id"], owner_id)
    await _assign_owner(app_client, ha, p3["id"], owner_id)
    hb = _auth(token_factory, subj.b)

    # The owner can link the (P1-scoped) record to P3 — a process they own.
    ok = await app_client.post(
        f"/api/v1/records/{rid}/evidence-links",
        headers=hb,
        json={"target_type": "process", "target_id": p3["id"]},
    )
    assert ok.status_code == 201, ok.text
    # ...but NOT to P2, which they do not own → 403 (re-authorized against the target process).
    deny = await app_client.post(
        f"/api/v1/records/{rid}/evidence-links",
        headers=hb,
        json={"target_type": "process", "target_id": p2["id"]},
    )
    assert deny.status_code == 403, deny.text


async def test_correction_reauthorizes_inherited_source_process(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """capture_correction FORCES a source-backed original's own source (the body's is ignored), so
    the correction endpoint re-authorizes the EFFECTIVE source — even when the body omits it. A P1
    owner correcting a record whose inherited source is an unowned P2 is denied until they also own
    P2 (the Codex round-2 P1: inherited-source over-grant)."""
    await s5.grant_lifecycle(subj.a)
    await _grant(subj.a, "record.create")
    await _grant(subj.a, "record.read")
    await _grant(subj.a, "process.create")
    await _grant(subj.a, "process.assign_owner")
    ha = _auth(token_factory, subj.a)
    p1 = await _create_process(app_client, ha)
    p2 = await _create_process(app_client, ha)
    d2, v2 = await _doc_version_linked(app_client, ha, p2["id"])

    # A source-backed original (source = D2, process P2), then evidence-linked to P1 so a P1 owner
    # can reach it via the union scope.
    orig = await app_client.post(
        "/api/v1/records",
        headers=ha,
        json={
            "record_type": "RELEASE",
            "title": "Original (P2 source)",
            "source_document_id": d2,
            "source_version_id": v2,
        },
    )
    assert orig.status_code == 201, orig.text
    rid = orig.json()["id"]
    seed = await app_client.post(
        f"/api/v1/records/{rid}/evidence-links",
        headers=ha,
        json={"target_type": "process", "target_id": p1["id"]},
    )
    assert seed.status_code == 201, seed.text

    owner_id = await _user_id(subj.b)
    await _assign_owner(app_client, ha, p1["id"], owner_id)
    hb = _auth(token_factory, subj.b)

    # subj.b owns P1 (passes _create_scoped via the union), but the body OMITS the source — the
    # inherited D2/P2 source is what capture_correction would pin → 403 (effective-source re-auth).
    deny = await app_client.post(
        f"/api/v1/records/{rid}/correction",
        headers=hb,
        json={"record_type": "RELEASE", "title": "Corrected (inherits P2 source)"},
    )
    assert deny.status_code == 403, deny.text
    # Once subj.b also owns P2 (the inherited source's process), the same correction succeeds.
    await _assign_owner(app_client, ha, p2["id"], owner_id)
    ok = await app_client.post(
        f"/api/v1/records/{rid}/correction",
        headers=hb,
        json={"record_type": "RELEASE", "title": "Corrected (now owns P2)"},
    )
    assert ok.status_code == 201, ok.text


async def test_evidence_link_delete_reauthorizes_target_process(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """Removing a PROCESS evidence link narrows who can record.read the record, so the unlink
    endpoint re-authorizes the link's TARGET process: a P1 owner of a joint P1+P2 record can delete
    the P1 link but NOT the P2 link (the Codex round-2 P1: unlink over-grant)."""
    await s5.grant_lifecycle(subj.a)
    await _grant(subj.a, "record.create")
    await _grant(subj.a, "record.read")
    await _grant(subj.a, "process.create")
    await _grant(subj.a, "process.assign_owner")
    ha = _auth(token_factory, subj.a)
    p1 = await _create_process(app_client, ha)
    p2 = await _create_process(app_client, ha)
    rec = await app_client.post(
        "/api/v1/records", headers=ha, json={"record_type": "EVIDENCE", "title": "Joint"}
    )
    assert rec.status_code == 201, rec.text
    rid = rec.json()["id"]
    l1 = await app_client.post(
        f"/api/v1/records/{rid}/evidence-links",
        headers=ha,
        json={"target_type": "process", "target_id": p1["id"]},
    )
    l2 = await app_client.post(
        f"/api/v1/records/{rid}/evidence-links",
        headers=ha,
        json={"target_type": "process", "target_id": p2["id"]},
    )
    assert l1.status_code == 201 and l2.status_code == 201, (l1.text, l2.text)

    owner_id = await _user_id(subj.b)
    await _assign_owner(app_client, ha, p1["id"], owner_id)
    hb = _auth(token_factory, subj.b)

    # subj.b owns P1 (passes _create_scoped via the union) but cannot delete the P2 evidence link.
    deny = await app_client.delete(
        f"/api/v1/records/{rid}/evidence-links/{l2.json()['id']}", headers=hb
    )
    assert deny.status_code == 403, deny.text
    # ...but can delete the P1 link (their own process) → 204.
    ok = await app_client.delete(
        f"/api/v1/records/{rid}/evidence-links/{l1.json()['id']}", headers=hb
    )
    assert ok.status_code == 204, ok.text
