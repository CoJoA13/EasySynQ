"""Audit C1/C2 — per-neighbour authorization on where-used/links, far-end re-auth on link writes.

R57 makes a document's identifier/title/state a ``document.read``-gated surface, and the Library
list / search / detail GET all hide out-of-scope documents from a PROCESS-bound caller — but the
where-used panel and the links listing previously returned every neighbour unfiltered, and the
link create/delete verbs authorized only the PATH document. These proofs drive genuinely SCOPED
callers (PROCESS-bound overrides, no SYSTEM masking — the ``SYSTEM overrides mask this`` false-PASS
pattern) against documents split across two processes. Assertions are scoped to this run's rows.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models._record_enums import RecordType
from easysynq_api.db.models._vault_enums import DocumentCurrentState, DocumentKind
from easysynq_api.db.models.documented_information import DocumentedInformation
from easysynq_api.db.models.framework import Framework
from easysynq_api.db.models.process_link import ProcessLink
from easysynq_api.db.models.record import Record
from easysynq_api.db.models.retention_policy import RetentionPolicy
from easysynq_api.db.session import get_sessionmaker

from .test_dcr import _grant, _grant_process, _seed_process_and_linked_doc, _subject
from .test_vault import _auth, _ensure_user


async def _deny_artifact(subject: str, key: str, artifact_id: str) -> None:
    """An ARTIFACT-scoped DENY override on one document — deny-always-wins must fold through the
    panel row-filter exactly as it does through the detail gate."""
    from easysynq_api.db.models.authz_grant import PermissionOverride
    from easysynq_api.db.models.permission import Permission
    from easysynq_api.db.models.scope import Scope
    from easysynq_api.domain.authz.types import Effect, ScopeLevel

    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        perm = (await s.execute(select(Permission).where(Permission.key == key))).scalar_one()
        scope = Scope(
            org_id=user.org_id, level=ScopeLevel.ARTIFACT, selector={"artifact_id": artifact_id}
        )
        s.add(scope)
        await s.flush()
        s.add(
            PermissionOverride(
                org_id=user.org_id,
                user_id=user.id,
                permission_id=perm.id,
                effect=Effect.DENY,
                scope_id=scope.id,
            )
        )
        await s.commit()


async def _deny_process(subject: str, key: str, process_id: str) -> None:
    """A PROCESS-scoped DENY override — deny-always-wins must fold through doc-scoped
    evaluations exactly as it does through the (U2-scoped) GET /dcrs row filter."""
    from easysynq_api.db.models.authz_grant import PermissionOverride
    from easysynq_api.db.models.permission import Permission
    from easysynq_api.db.models.scope import Scope
    from easysynq_api.domain.authz.types import Effect, ScopeLevel

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
                effect=Effect.DENY,
                scope_id=scope.id,
            )
        )
        await s.commit()


pytestmark = pytest.mark.integration

_ADMIN_KEYS = (
    "document.read",
    "document.manage_metadata",
    "changeRequest.create",
    "changeRequest.read",
    "capa.create",
    "capa.read",
)


async def _seed_doc_linked_to(subject: str, process_id: str) -> str:
    """A fresh controlled DOCUMENT linked to an EXISTING process (the C-in-P1 fixture)."""
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        framework_id = (await s.execute(select(Framework.id).limit(1))).scalar_one()
        di = DocumentedInformation(
            org_id=user.org_id,
            framework_id=framework_id,
            kind=DocumentKind.DOCUMENT,
            identifier=f"WUA-{uuid.uuid4().hex[:10]}",
            title="where-used authz fixture",
            owner_user_id=user.id,
            created_by=user.id,
        )
        s.add(di)
        await s.flush()
        s.add(
            ProcessLink(
                org_id=user.org_id,
                process_id=uuid.UUID(process_id),
                documented_information_id=di.id,
                created_by=user.id,
            )
        )
        await s.commit()
        return str(di.id)


def _bucket_doc_ids(payload: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for key in (
        "child_documents",
        "parent_documents",
        "referenced_by",
        "references_out",
        "forms_templates",
        "supersedes",
        "superseded_by",
    ):
        ids |= {row["document_id"] for row in payload[key]}
    return ids


async def _fixture(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> tuple[dict[str, str], str, str, str, str, str]:
    """admin headers + (P1, A-in-P1, P2, B-in-P2, C-in-P1) with an admin-minted A→B link."""
    admin = _subject("wua-admin")
    await _grant(admin, _ADMIN_KEYS)
    h_admin = _auth(token_factory, admin)
    p1, doc_a = await _seed_process_and_linked_doc(admin)
    p2, doc_b = await _seed_process_and_linked_doc(admin)
    doc_c = await _seed_doc_linked_to(admin, p1)
    r = await app_client.post(
        f"/api/v1/documents/{doc_a}/links",
        headers=h_admin,
        json={"to_document_id": doc_b, "link_type": "references"},
    )
    assert r.status_code == 201, r.text
    return h_admin, p1, doc_a, p2, doc_b, doc_c


async def test_where_used_and_links_hide_out_of_scope_neighbours(
    app_client: AsyncClient, token_factory: Callable[..., str], app_under_test: object
) -> None:
    h_admin, p1, doc_a, _p2, doc_b, doc_c = await _fixture(app_client, token_factory)
    r = await app_client.post(
        f"/api/v1/documents/{doc_a}/links",
        headers=h_admin,
        json={"to_document_id": doc_c, "link_type": "references"},
    )
    assert r.status_code == 201, r.text

    emp = _subject("wua-emp")
    await _grant_process(emp, "document.read", p1)
    h_emp = _auth(token_factory, emp)

    wu = await app_client.get(f"/api/v1/documents/{doc_a}/where-used", headers=h_emp)
    assert wu.status_code == 200, wu.text
    ids = _bucket_doc_ids(wu.json())
    assert doc_b not in ids, "an out-of-scope neighbour's metadata leaked through where-used"
    assert doc_c in ids, "an in-scope neighbour must survive the filter (not a drop-everything)"

    links = await app_client.get(f"/api/v1/documents/{doc_a}/links", headers=h_emp)
    assert links.status_code == 200, links.text
    far_ids = {row["to_document_id"] for row in links.json()} | {
        row["from_document_id"] for row in links.json()
    }
    assert doc_b not in far_ids, "a hidden neighbour's bare id leaked through the links listing"
    assert doc_c in far_ids

    # The SYSTEM-scoped admin still sees both neighbours — filtering is per-caller, not global.
    wu_admin = await app_client.get(f"/api/v1/documents/{doc_a}/where-used", headers=h_admin)
    admin_ids = _bucket_doc_ids(wu_admin.json())
    assert {doc_b, doc_c} <= admin_ids


async def test_link_create_and_delete_require_far_end_authority(
    app_client: AsyncClient, token_factory: Callable[..., str], app_under_test: object
) -> None:
    h_admin, p1, doc_a, _p2, doc_b, doc_c = await _fixture(app_client, token_factory)

    owner = _subject("wua-owner")
    await _grant_process(owner, "document.read", p1)
    await _grant_process(owner, "document.manage_metadata", p1)
    h_owner = _auth(token_factory, owner)

    # Cross-process mint: the caller controls A (P1) but holds no authority over B (P2) → 403.
    r = await app_client.post(
        f"/api/v1/documents/{doc_a}/links",
        headers=h_owner,
        json={"to_document_id": doc_b, "link_type": "references"},
    )
    assert r.status_code == 403, r.text

    # Both ends in the caller's process → allowed.
    r = await app_client.post(
        f"/api/v1/documents/{doc_a}/links",
        headers=h_owner,
        json={"to_document_id": doc_c, "link_type": "references"},
    )
    assert r.status_code == 201, r.text
    in_scope_link = r.json()["id"]

    # Delete is symmetric: the admin-minted A→B edge needs authority over B too.
    links = (await app_client.get(f"/api/v1/documents/{doc_a}/links", headers=h_admin)).json()
    ab_link = next(row for row in links if row["to_document_id"] == doc_b)
    r = await app_client.delete(f"/api/v1/documents/{doc_a}/links/{ab_link['id']}", headers=h_owner)
    assert r.status_code == 403, r.text
    r = await app_client.delete(f"/api/v1/documents/{doc_a}/links/{in_scope_link}", headers=h_owner)
    assert r.status_code == 204, r.text


async def test_records_sample_filtered_by_record_read(
    app_client: AsyncClient, token_factory: Callable[..., str], app_under_test: object
) -> None:
    _h_admin, p1, doc_a, _p2, _doc_b, _doc_c = await _fixture(app_client, token_factory)

    async with get_sessionmaker()() as s:
        admin_user = await _ensure_user(s, _subject("wua-admin"))
        org_id = admin_user.org_id
        framework_id = (await s.execute(select(Framework.id).limit(1))).scalar_one()
        policy_id = (
            await s.execute(
                select(RetentionPolicy.id).where(RetentionPolicy.org_id == org_id).limit(1)
            )
        ).scalar_one()
        rec_di = DocumentedInformation(
            org_id=org_id,
            framework_id=framework_id,
            kind=DocumentKind.RECORD,
            identifier=f"WUA-REC-{uuid.uuid4().hex[:8]}",
            title="where-used record fixture",
            owner_user_id=admin_user.id,
            created_by=admin_user.id,
        )
        s.add(rec_di)
        await s.flush()
        s.add(
            Record(
                id=rec_di.id,
                org_id=org_id,
                record_type=RecordType.EVIDENCE,
                captured_by=admin_user.id,
                retention_policy_id=policy_id,
                source_document_id=uuid.UUID(doc_a),
            )
        )
        await s.commit()
        record_id = str(rec_di.id)

    emp = _subject("wua-rec-emp")
    await _grant_process(emp, "document.read", p1)
    h_emp = _auth(token_factory, emp)
    wu = (await app_client.get(f"/api/v1/documents/{doc_a}/where-used", headers=h_emp)).json()
    produced = wu["records_produced_under"]
    assert produced["count"] >= 1  # the aggregate stays exact
    assert record_id not in {row["id"] for row in produced["sample"]}, (
        "a record identifier leaked to a caller without record.read"
    )

    # A PROCESS-scoped record.read on the SOURCE doc's process makes the row visible (the records
    # listing's exact context recipe — process ids resolve through the source document's links).
    await _grant_process(emp, "record.read", p1)
    wu = (await app_client.get(f"/api/v1/documents/{doc_a}/where-used", headers=h_emp)).json()
    assert record_id in {row["id"] for row in wu["records_produced_under"]["sample"]}


async def test_related_capas_leg_gated_by_change_request_read(
    app_client: AsyncClient, token_factory: Callable[..., str], app_under_test: object
) -> None:
    h_admin, p1, doc_a, _p2, _doc_b, _doc_c = await _fixture(app_client, token_factory)
    capa = await app_client.post(
        "/api/v1/capas", headers=h_admin, json={"title": "wua capa", "severity": "Minor"}
    )
    assert capa.status_code == 201, capa.text
    dcr = await app_client.post(
        "/api/v1/dcrs",
        headers=h_admin,
        json={
            "change_type": "REVISE",
            "change_significance": "MINOR",
            "reason_class": "capa",
            "reason_text": "where-used authz proof",
            "target_document_id": doc_a,
            "source_link_type": "capa",
            "source_link_id": capa.json()["id"],
        },
    )
    assert dcr.status_code == 201, dcr.text
    dcr_id = dcr.json()["id"]

    emp = _subject("wua-dcr-emp")
    await _grant_process(emp, "document.read", p1)
    h_emp = _auth(token_factory, emp)
    wu = (await app_client.get(f"/api/v1/documents/{doc_a}/where-used", headers=h_emp)).json()
    assert wu["related_capas_findings"] == [], (
        "DCR identifiers leaked to a caller without changeRequest.read"
    )

    # A PROCESS-scoped changeRequest.read on the PATH DOC's process reveals the leg — the SAME
    # decision the (U2-scoped) GET /dcrs row filter gives for this DCR. The old SYSTEM-context
    # evaluation could never match this grant (the incomplete-scope-tuple trap). The CAPA
    # close_state stays hidden until capa.read is also held.
    await _grant_process(emp, "changeRequest.read", p1)
    wu = (await app_client.get(f"/api/v1/documents/{doc_a}/where-used", headers=h_emp)).json()
    rows = {row["dcr_id"]: row for row in wu["related_capas_findings"]}
    assert dcr_id in rows
    assert rows[dcr_id]["capa_close_state"] is None

    await _grant(emp, ("capa.read",))
    wu = (await app_client.get(f"/api/v1/documents/{doc_a}/where-used", headers=h_emp)).json()
    rows = {row["dcr_id"]: row for row in wu["related_capas_findings"]}
    assert rows[dcr_id]["capa_close_state"] is not None


async def test_obsoletion_reason_redacts_hidden_referencing_identifiers(
    app_client: AsyncClient, token_factory: Callable[..., str], app_under_test: object
) -> None:
    """The referenced_by_effective advisory embeds referencing documents' identifiers — the same
    R57 fact the bucket filter hides; the panel must not hand it back through the reason detail
    (the authz-reviewer bypass). The blocking gate and assess path keep the full detail."""
    h_admin, p1, doc_a, _p2, doc_b, _doc_c = await _fixture(app_client, token_factory)
    # B (in P2) references A, and B is Effective → the §7.3 advisory fires on A's panel.
    r = await app_client.post(
        f"/api/v1/documents/{doc_b}/links",
        headers=h_admin,
        json={"to_document_id": doc_a, "link_type": "references"},
    )
    assert r.status_code == 201, r.text
    async with get_sessionmaker()() as s:
        row = await s.get(DocumentedInformation, uuid.UUID(doc_b))
        assert row is not None
        row.current_state = DocumentCurrentState.Effective
        await s.commit()
        b_identifier = row.identifier

    emp = _subject("wua-obs-emp")
    await _grant_process(emp, "document.read", p1)
    h_emp = _auth(token_factory, emp)
    wu = (await app_client.get(f"/api/v1/documents/{doc_a}/where-used", headers=h_emp)).json()
    reasons = {r["code"]: r["detail"] for r in wu["obsoletion_safety"]["reasons"]}
    assert "referenced_by_effective" in reasons
    assert b_identifier not in reasons["referenced_by_effective"], (
        "a hidden referencing document's identifier leaked through the obsoletion advisory"
    )
    assert "not visible to your read scope" in reasons["referenced_by_effective"]

    wu_admin = (
        await app_client.get(f"/api/v1/documents/{doc_a}/where-used", headers=h_admin)
    ).json()
    admin_reasons = {r["code"]: r["detail"] for r in wu_admin["obsoletion_safety"]["reasons"]}
    assert b_identifier in admin_reasons["referenced_by_effective"]


async def test_artifact_deny_folds_through_the_panel_filter(
    app_client: AsyncClient, token_factory: Callable[..., str], app_under_test: object
) -> None:
    """Deny-always-wins: an ARTIFACT-scoped document.read DENY on an otherwise-visible in-process
    neighbour must hide it from the panel exactly as the detail gate would."""
    h_admin, p1, doc_a, _p2, _doc_b, doc_c = await _fixture(app_client, token_factory)
    r = await app_client.post(
        f"/api/v1/documents/{doc_a}/links",
        headers=h_admin,
        json={"to_document_id": doc_c, "link_type": "references"},
    )
    assert r.status_code == 201, r.text

    emp = _subject("wua-deny-emp")
    await _grant_process(emp, "document.read", p1)
    h_emp = _auth(token_factory, emp)
    wu = (await app_client.get(f"/api/v1/documents/{doc_a}/where-used", headers=h_emp)).json()
    assert doc_c in _bucket_doc_ids(wu)  # visible via the P1 ALLOW before the DENY lands

    await _deny_artifact(emp, "document.read", doc_c)
    wu = (await app_client.get(f"/api/v1/documents/{doc_a}/where-used", headers=h_emp)).json()
    assert doc_c not in _bucket_doc_ids(wu), "an ARTIFACT DENY did not fold through the filter"


async def test_related_capas_leg_honors_scoped_change_request_deny(
    app_client: AsyncClient, token_factory: Callable[..., str], app_under_test: object
) -> None:
    """[Authz-reviewer catch on U2] A SYSTEM changeRequest.read ALLOW plus a PROCESS-scoped DENY
    on the path doc's process must hide the DCR leg — at the old SYSTEM context the scoped DENY
    silently never matched, so where-used revealed DCR identifiers that GET /dcrs (now scoped)
    hides from the same caller. Both surfaces must give one answer."""
    h_admin, p1, doc_a, _p2, _doc_b, _doc_c = await _fixture(app_client, token_factory)
    capa = await app_client.post(
        "/api/v1/capas", headers=h_admin, json={"title": "wua deny capa", "severity": "Minor"}
    )
    assert capa.status_code == 201, capa.text
    # CAPA-SOURCED, like the sibling test: only source-linked DCRs populate the leg — an
    # unsourced DCR would make the hidden-leg assertion vacuously true (false-PASS).
    dcr = await app_client.post(
        "/api/v1/dcrs",
        headers=h_admin,
        json={
            "change_type": "REVISE",
            "change_significance": "MINOR",
            "reason_class": "capa",
            "reason_text": "deny-direction probe",
            "target_document_id": doc_a,
            "source_link_type": "capa",
            "source_link_id": capa.json()["id"],
        },
    )
    assert dcr.status_code == 201, dcr.text
    dcr_id = dcr.json()["id"]

    # Anti-vacuity: a broad reader DOES see the leg before the DENY subject is exercised.
    baseline = (
        await app_client.get(f"/api/v1/documents/{doc_a}/where-used", headers=h_admin)
    ).json()
    assert any(row["dcr_id"] == dcr_id for row in baseline["related_capas_findings"])

    emp = _subject("wua-dcrdeny")
    await _grant(emp, ("document.read", "changeRequest.read"))  # broad SYSTEM ALLOW
    await _deny_process(emp, "changeRequest.read", p1)  # scoped DENY on the doc's process
    h2 = _auth(token_factory, emp)

    wu = (await app_client.get(f"/api/v1/documents/{doc_a}/where-used", headers=h2)).json()
    assert all(row["dcr_id"] != dcr_id for row in wu["related_capas_findings"]), (
        "a PROCESS-scoped changeRequest.read DENY must hide the DCR leg"
    )
    # Consistency: the (U2-scoped) GET /dcrs hides the same DCR from the same caller.
    lst = (await app_client.get("/api/v1/dcrs", headers=h2)).json()
    assert dcr_id not in [d["id"] for d in lst["data"]]
    assert (await app_client.get(f"/api/v1/dcrs/{dcr_id}", headers=h2)).status_code == 403


async def test_impact_read_filters_neighbours_to_the_callers_scope(
    app_client: AsyncClient, token_factory: Callable[..., str], app_under_test: object
) -> None:
    """[Authz-reviewer Important 1] The impact READ filters the persisted where-used snapshot per
    caller — U2 made the endpoint reachable by scoped readers, and the stored auto_populated
    embeds neighbour identifiers the where-used panel deliberately hides from the same caller.
    The stored rows stay complete (the admin still sees everything)."""
    h_admin, p1, doc_a, _p2, doc_b, _doc_c = await _fixture(app_client, token_factory)
    # The fixture's A→B link is references_out — a bucket the impact projection drops. Mint the
    # REVERSE link so doc_b lands in doc_a's referenced_by (a projected bucket).
    reverse = await app_client.post(
        f"/api/v1/documents/{doc_b}/links",
        headers=h_admin,
        json={"to_document_id": doc_a, "link_type": "references"},
    )
    assert reverse.status_code == 201, reverse.text
    adm = _subject("wua-imp-adm")
    await _grant(
        adm,
        (
            "changeRequest.create",
            "changeRequest.assess",
            "changeRequest.read",
            "document.read",
            "record.read",
        ),
    )
    h_adm = _auth(token_factory, adm)
    dcr = await app_client.post(
        "/api/v1/dcrs",
        headers=h_adm,
        json={
            "change_type": "REVISE",
            "change_significance": "MINOR",
            "reason_class": "error_correction",
            "reason_text": "impact filter probe",
            "target_document_id": doc_a,
        },
    )
    assert dcr.status_code == 201, dcr.text
    dcr_id = dcr.json()["id"]
    assessed = await app_client.post(f"/api/v1/dcrs/{dcr_id}/assess", headers=h_adm)
    assert assessed.status_code == 200, assessed.text

    def _neighbour_ids(payload: dict[str, Any]) -> set[str]:
        dep = next(r for r in payload["data"] if r["dimension"] == "dependent_documents")
        ids: set[str] = set()
        for bucket in ("child_documents", "parent_documents", "referenced_by", "forms_templates"):
            ids |= {row["document_id"] for row in dep["auto_populated"].get(bucket, [])}
        return ids

    # The broad caller sees the A→B neighbour in the persisted snapshot.
    full = (await app_client.get(f"/api/v1/dcrs/{dcr_id}/impact", headers=h_adm)).json()
    assert doc_b in _neighbour_ids(full)

    # A P1-scoped reader reaches the endpoint (U2) but doc_b (in P2) is filtered from the read.
    emp = _subject("wua-imp-emp")
    await _grant_process(emp, "document.read", p1)
    await _grant_process(emp, "changeRequest.read", p1)
    h_emp = _auth(token_factory, emp)
    scoped = await app_client.get(f"/api/v1/dcrs/{dcr_id}/impact", headers=h_emp)
    assert scoped.status_code == 200, scoped.text
    assert doc_b not in _neighbour_ids(scoped.json())

    # The persisted snapshot was NOT shrunk by the scoped read: the admin still sees doc_b.
    again = (await app_client.get(f"/api/v1/dcrs/{dcr_id}/impact", headers=h_adm)).json()
    assert doc_b in _neighbour_ids(again)
