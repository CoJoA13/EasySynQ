"""S-dcr-2 integration proofs — document_link CRUD + where-used + assess + impact (over HTTP).

Reuses the test_dcr helpers (SYSTEM-override grants, bare-doc seeding). Assertions are scoped to
this run's own ids — the integration suite shares one session DB across files, so absolute counts /
global coverage are never asserted (the obsoletion sole-★ leg is unit-tested deterministically; here
we prove the governs-active-process leg, which is run-scoped)."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models._vault_enums import DocumentKind
from easysynq_api.db.models.clause import Clause
from easysynq_api.db.session import get_sessionmaker

from .test_dcr import _auth, _grant, _seed_di, _seed_process_and_linked_doc, _subject

pytestmark = pytest.mark.integration

_LINK_KEYS = ("document.read", "document.manage_metadata")
_DCR_KEYS = (
    "changeRequest.create",
    "changeRequest.read",
    "changeRequest.assess",
    "document.read",
    "document.manage_metadata",
)


async def test_document_link_crud(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("dlink")
    await _grant(subject, _LINK_KEYS)
    h = _auth(token_factory, subject)
    doc_a = await _seed_di(subject, DocumentKind.DOCUMENT)
    doc_b = await _seed_di(subject, DocumentKind.DOCUMENT)

    # Create a references link A → B.
    r = await app_client.post(
        f"/api/v1/documents/{doc_a}/links",
        headers=h,
        json={"to_document_id": doc_b, "link_type": "references"},
    )
    assert r.status_code == 201, r.text
    link_id = r.json()["id"]

    # Listing surfaces it (touching A).
    listed = (await app_client.get(f"/api/v1/documents/{doc_a}/links", headers=h)).json()
    assert link_id in [x["id"] for x in listed]

    # Self-link → 422.
    r = await app_client.post(
        f"/api/v1/documents/{doc_a}/links",
        headers=h,
        json={"to_document_id": doc_a, "link_type": "references"},
    )
    assert r.status_code == 422, r.text

    # Duplicate (same from,to,type) → 409.
    r = await app_client.post(
        f"/api/v1/documents/{doc_a}/links",
        headers=h,
        json={"to_document_id": doc_b, "link_type": "references"},
    )
    assert r.status_code == 409, r.text

    # Target a Record → 422 not_a_document.
    rec = await _seed_di(subject, DocumentKind.RECORD)
    r = await app_client.post(
        f"/api/v1/documents/{doc_a}/links",
        headers=h,
        json={"to_document_id": rec, "link_type": "references"},
    )
    assert r.status_code == 422, r.text

    # Delete → 204.
    r = await app_client.delete(f"/api/v1/documents/{doc_a}/links/{link_id}", headers=h)
    assert r.status_code == 204, r.text


async def test_where_used_categories_and_obsoletion_advisory(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("wu")
    await _grant(subject, _LINK_KEYS)
    h = _auth(token_factory, subject)

    # A document that governs a fresh ACTIVE process (+ a child + a referencing doc).
    process_id, doc_a = await _seed_process_and_linked_doc(subject)
    child = await _seed_di(subject, DocumentKind.DOCUMENT)
    referrer = await _seed_di(subject, DocumentKind.DOCUMENT)
    await app_client.post(
        f"/api/v1/documents/{doc_a}/links",
        headers=h,
        json={"to_document_id": child, "link_type": "parent_of"},
    )
    # referrer → A (references); inbound on A = referenced_by.
    await app_client.post(
        f"/api/v1/documents/{referrer}/links",
        headers=h,
        json={"to_document_id": doc_a, "link_type": "references"},
    )

    wu = (await app_client.get(f"/api/v1/documents/{doc_a}/where-used", headers=h)).json()
    assert wu["document_id"] == doc_a
    assert [p["id"] for p in wu["processes"]] == [process_id]
    assert [c["document_id"] for c in wu["child_documents"]] == [child]
    assert [r["document_id"] for r in wu["referenced_by"]] == [referrer]
    assert wu["records_produced_under"]["count"] == 0
    # §7.3 advisory: governing an ACTIVE process blocks obsoletion (the run-scoped leg).
    assert wu["obsoletion_safety"]["blocked"] is True
    codes = {r["code"] for r in wu["obsoletion_safety"]["reasons"]}
    assert "governs_active_process" in codes

    # A document with no links/processes → not blocked.
    lone = await _seed_di(subject, DocumentKind.DOCUMENT)
    wu2 = (await app_client.get(f"/api/v1/documents/{lone}/where-used", headers=h)).json()
    assert wu2["obsoletion_safety"]["blocked"] is False


async def test_assess_auto_populates_impact_then_annotate(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("assess")
    await _grant(subject, _DCR_KEYS)
    h = _auth(token_factory, subject)
    doc_a = await _seed_di(subject, DocumentKind.DOCUMENT)
    # Map a ★ clause so the clause_coverage dimension carries it.
    async with get_sessionmaker()() as s:
        star = (
            await s.execute(select(Clause.id).where(Clause.is_mandatory_star.is_(True)).limit(1))
        ).scalar_one()
    await app_client.post(
        f"/api/v1/documents/{doc_a}/clause-mappings", headers=h, json={"clause_id": str(star)}
    )

    dcr_id = (
        await app_client.post(
            "/api/v1/dcrs",
            headers=h,
            json={
                "change_type": "REVISE",
                "change_significance": "MAJOR",
                "reason_class": "process_improvement",
                "reason_text": "tighten approval",
                "target_document_id": doc_a,
            },
        )
    ).json()["id"]

    # Assess: Open → Assessed + 7 auto-populated impact dimensions.
    r = await app_client.post(f"/api/v1/dcrs/{dcr_id}/assess", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "Assessed"
    dims = {row["dimension"] for row in r.json()["impact_assessment"]}
    assert dims == {
        "affected_processes",
        "dependent_documents",
        "records_produced_under",
        "training_awareness",
        "clause_coverage",
        "effectivity_transition",
        "risk",
    }
    by_dim = {row["dimension"]: row for row in r.json()["impact_assessment"]}
    # MAJOR → re-acknowledge required; risk is N/A in v1.
    assert by_dim["training_awareness"]["auto_populated"]["reacknowledge_required"] is True
    assert by_dim["risk"]["auto_populated"]["applicable"] is False

    # Annotate one dimension; the auto_populated facts are untouched.
    r = await app_client.put(
        f"/api/v1/dcrs/{dcr_id}/impact",
        headers=h,
        json={"annotations": {"affected_processes": "Diego to re-validate Purchasing"}},
    )
    assert r.status_code == 200, r.text
    ann = {row["dimension"]: row["requester_annotation"] for row in r.json()["data"]}
    assert ann["affected_processes"] == "Diego to re-validate Purchasing"

    # An unknown dimension → 422.
    r = await app_client.put(
        f"/api/v1/dcrs/{dcr_id}/impact", headers=h, json={"annotations": {"bogus": "x"}}
    )
    assert r.status_code == 422, r.text


async def test_assess_create_dcr_is_all_na(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("assess-create")
    await _grant(subject, _DCR_KEYS)
    h = _auth(token_factory, subject)
    dcr_id = (
        await app_client.post(
            "/api/v1/dcrs",
            headers=h,
            json={
                "change_type": "CREATE",
                "change_significance": "MINOR",
                "reason_class": "other",
                "reason_text": "new policy",
            },
        )
    ).json()["id"]
    r = await app_client.post(f"/api/v1/dcrs/{dcr_id}/assess", headers=h)
    assert r.status_code == 200, r.text
    # A CREATE DCR has no target → every dimension is N/A.
    assert all(
        row["auto_populated"]["applicable"] is False for row in r.json()["impact_assessment"]
    )

    # Re-assessing an already-Assessed DCR is a 409 (Assessed → Assessed is no transition).
    r = await app_client.post(f"/api/v1/dcrs/{dcr_id}/assess", headers=h)
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "dcr_not_assessable"
