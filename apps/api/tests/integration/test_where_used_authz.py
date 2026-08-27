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

    # changeRequest.read (SYSTEM, matching the GET /dcrs gate) reveals the leg — with the CAPA
    # close_state still hidden until capa.read is also held.
    await _grant(emp, ("changeRequest.read",))
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
