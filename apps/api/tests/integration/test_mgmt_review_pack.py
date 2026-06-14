"""S-mr-pack integration: ``GET /management-reviews/{review_id}/pack`` (CI-only on this Windows
box — native ``-m integration`` does not run here; see CLAUDE.md).

The pack renders the released MR's FROZEN ``metadata_snapshot["mgmt_review_minutes"]`` (NOT the live
``review_output`` rows), so a post-release mutation of a live output row must NOT change the bytes
(the frozen-snapshot proof). The harness mirrors ``test_mgmt_review.py`` verbatim: ``_auth`` (bearer
header by subject), ``_grant`` (SYSTEM-scope PermissionOverride → ``app_user.id``), the
``mgmtReview.*`` key tuple, and the ``_drive_review_to_release`` end-to-end helper (create → output
→ submit → approve in ``/tasks`` → release; SoD-2 author ≠ approver ≠ releaser, the releaser holding
``document.release``).
"""

from __future__ import annotations

import io
import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from pypdf import PdfReader
from sqlalchemy import update

from easysynq_api.db.models.document_version import DocumentVersion
from easysynq_api.db.models.documented_information import DocumentedInformation
from easysynq_api.db.models.management_review import ManagementReview
from easysynq_api.db.models.organization import Organization
from easysynq_api.db.models.review_output import ReviewOutput
from easysynq_api.db.session import get_sessionmaker

from . import s5_helpers as s5
from .test_mgmt_review import _MR_KEYS, _auth, _create_review, _drive_review_to_release, _grant

pytestmark = pytest.mark.integration


def _pdf_text(pdf: bytes) -> str:
    """Extract the concatenated text of every page (mirrors test_mr_pack_render._text)."""
    return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf)).pages)


async def _identifier(rid: str) -> str:
    async with get_sessionmaker()() as s:
        doc = await s.get(DocumentedInformation, uuid.UUID(rid))
        assert doc is not None
        return doc.identifier


async def test_pack_streams_pdf_for_released_review(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A released MR streams its filed-minutes pack: 200 application/pdf, ``%PDF`` magic, and a
    ``{identifier}-minutes.pdf`` Content-Disposition (the reader holds ``mgmtReview.read``)."""
    salt = uuid.uuid4().hex[:8]
    owner_subject = f"mr-pk-own-{salt}"
    owner_id = await _grant(owner_subject, ())
    rid = await _drive_review_to_release(
        app_client,
        token_factory,
        salt,
        action_owner_subject=owner_subject,
        action_owner_id=owner_id,
    )

    reader = f"mr-pk-rdr-{salt}"
    hr = _auth(token_factory, reader)
    await _grant(reader, ("mgmtReview.read",))

    r = await app_client.get(f"/api/v1/management-reviews/{rid}/pack", headers=hr)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/pdf"), r.headers
    assert r.content[:4] == b"%PDF"

    identifier = await _identifier(rid)
    assert f"{identifier}-minutes.pdf" in r.headers["content-disposition"], r.headers


async def test_pack_409_before_release(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A never-released (Draft) MR has no current_effective_version_id → 409 pack_unavailable."""
    subject = f"mr-pk-draft-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _MR_KEYS)  # create + read; the doc stays Draft (no release)
    rid = await _create_review(app_client, h, "Unreleased pack review")

    r = await app_client.get(f"/api/v1/management-reviews/{rid}/pack", headers=h)
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "pack_unavailable", r.text


async def test_pack_404_cross_org(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A pack request by a reader whose org does not own the MR is a 404 (``_load_review`` org
    check). Single-org install: create a real second org, then move the MR's base doc + satellite
    into it — the org-A reader (who holds ``mgmtReview.read`` so the dependency passes) then 404s on
    the org mismatch, never leaking the review's existence."""
    salt = uuid.uuid4().hex[:8]
    subject = f"mr-pk-xo-{salt}"
    h = _auth(token_factory, subject)
    await _grant(subject, _MR_KEYS)  # the caller is a permitted reader in org A
    rid = await _create_review(app_client, h, "Cross-org pack review")

    async with get_sessionmaker()() as s:
        other_org = Organization(
            legal_name=f"Other Org {salt}", short_code=f"OTHER-{salt[:6].upper()}"
        )
        s.add(other_org)
        await s.flush()
        await s.execute(
            update(DocumentedInformation)
            .where(DocumentedInformation.id == uuid.UUID(rid))
            .values(org_id=other_org.id)
        )
        await s.execute(
            update(ManagementReview)
            .where(ManagementReview.id == uuid.UUID(rid))
            .values(org_id=other_org.id)
        )
        await s.commit()

    r = await app_client.get(f"/api/v1/management-reviews/{rid}/pack", headers=h)
    assert r.status_code == 404, r.text


async def test_pack_403_without_read(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The pack is gated ``mgmtReview.read`` — a JIT'd caller without that key gets 403."""
    salt = uuid.uuid4().hex[:8]
    owner_subject = f"mr-pk-403-own-{salt}"
    owner_id = await _grant(owner_subject, ())
    rid = await _drive_review_to_release(
        app_client,
        token_factory,
        salt,
        action_owner_subject=owner_subject,
        action_owner_id=owner_id,
    )

    nobody = f"mr-pk-403-{salt}"
    hn = _auth(token_factory, nobody)
    await _grant(nobody, ())  # JIT a real, org-matched user holding NO permission keys

    r = await app_client.get(f"/api/v1/management-reviews/{rid}/pack", headers=hn)
    assert r.status_code == 403, r.text


async def test_pack_reflects_frozen_snapshot_not_live_rows(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """THE thesis: the pack renders the released version's FROZEN minutes snapshot, NOT the live
    ``review_output`` rows. Drive an MR to Effective with a known ACTION description, fetch the pack
    bytes, then directly TAMPER the live output row (bypassing the FSM) + commit; the next pack is
    byte-identical (deterministic + frozen-sourced); its text shows the original, not the tamper."""
    salt = uuid.uuid4().hex[:8]
    submitter, approver, releaser = f"mr-frz-sm-{salt}", f"mr-frz-ap-{salt}", f"mr-frz-rl-{salt}"
    hs = _auth(token_factory, submitter)
    hap = _auth(token_factory, approver)
    hrl = _auth(token_factory, releaser)
    owner_id = await _grant(submitter, _MR_KEYS)
    await s5.grant_role(approver, "Approver")
    await _grant(releaser, ("document.release", "document.read", "document.read_draft"))

    rid = await _create_review(app_client, hs, f"Frozen-snapshot review {salt}")
    out = await app_client.post(
        f"/api/v1/management-reviews/{rid}/outputs",
        headers=hs,
        json={
            "output_type": "ACTION",
            "description": "ORIGINAL ACTION",
            "owner_user_id": str(owner_id),
            "due_date": "2026-12-31",
        },
    )
    assert out.status_code == 201, out.text
    output_id = out.json()["id"]

    submitted = await app_client.post(f"/api/v1/management-reviews/{rid}/submit-review", headers=hs)
    assert submitted.status_code == 200, submitted.text
    task_id = await s5.task_for_doc(rid)
    dec = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hap, json={"outcome": "approve"}
    )
    assert dec.status_code == 200, dec.text
    rel = await app_client.post(f"/api/v1/management-reviews/{rid}/release", headers=hrl)
    assert rel.status_code == 200, rel.text
    assert rel.json()["current_state"] == "Effective"

    # the reader who fetches the pack
    reader = f"mr-frz-rdr-{salt}"
    hr = _auth(token_factory, reader)
    await _grant(reader, ("mgmtReview.read",))

    before_resp = await app_client.get(f"/api/v1/management-reviews/{rid}/pack", headers=hr)
    assert before_resp.status_code == 200, before_resp.text
    before = before_resp.content
    assert before[:4] == b"%PDF"

    # TAMPER the live output row directly (bypassing the FSM) — the snapshot is the WORM authority
    async with get_sessionmaker()() as s:
        await s.execute(
            update(ReviewOutput)
            .where(ReviewOutput.id == uuid.UUID(output_id))
            .values(description="TAMPERED")
        )
        await s.commit()
        # sanity: the live row really did change
        tampered = await s.get(ReviewOutput, uuid.UUID(output_id))
        assert tampered is not None and tampered.description == "TAMPERED"

    after_resp = await app_client.get(f"/api/v1/management-reviews/{rid}/pack", headers=hr)
    assert after_resp.status_code == 200, after_resp.text
    after = after_resp.content

    # deterministic + frozen-sourced: the bytes are identical despite the live-row tamper
    assert before == after, (
        "pack bytes changed after a live-row tamper — it is NOT reading the frozen snapshot"
    )

    text = _pdf_text(after)
    assert "ORIGINAL ACTION" in text, "the frozen ACTION description is missing from the pack"
    assert "TAMPERED" not in text, "the tampered live-row value leaked into the pack"

    # the frozen snapshot still carries the original (the WORM source of the render)
    async with get_sessionmaker()() as s:
        doc = await s.get(DocumentedInformation, uuid.UUID(rid))
        assert doc is not None and doc.current_effective_version_id is not None
        version = await s.get(DocumentVersion, doc.current_effective_version_id)
        assert version is not None
        minutes = (version.metadata_snapshot or {}).get("mgmt_review_minutes")
        assert minutes is not None
        descriptions = [o["description"] for o in minutes["outputs"]]
        assert "ORIGINAL ACTION" in descriptions
        assert "TAMPERED" not in descriptions
