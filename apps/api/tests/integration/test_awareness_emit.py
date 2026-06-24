"""S-notify-5a integration proofs — ``doc.released`` awareness_event emitted from ``_cutover``.

Two proofs:
1. A normal release writes exactly ONE ``awareness_event`` with the expected fields
   (subject_type, subject_version_id, context["version.label"], fanned_out_at IS NULL).
2. A concurrent release (two SERIALIZABLE ``_cutover`` calls racing via asyncio.gather) leaves
   exactly ONE awareness_event and the loser gets a clean 409 (no 500, no phantom row).

Pattern mirrors ``test_lifecycle.py::test_two_effective_impossible`` for the concurrent case.
"""

from __future__ import annotations

import asyncio
import datetime
import uuid
from collections.abc import Callable
from types import SimpleNamespace

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models._vault_enums import DocumentCurrentState, VersionState
from easysynq_api.db.models.awareness_event import AwarenessEvent
from easysynq_api.db.models.document_version import DocumentVersion
from easysynq_api.db.models.documented_information import DocumentedInformation
from easysynq_api.db.session import get_sessionmaker

from . import s5_helpers as s5
from .test_vault import _auth, _checkin, _create, _upload

pytestmark = pytest.mark.integration


@pytest.fixture
def subj() -> SimpleNamespace:
    salt = uuid.uuid4().hex[:10]
    return SimpleNamespace(a=f"kc-ae-author-{salt}", b=f"kc-ae-rel-{salt}")


async def _awareness_rows(doc_id: str) -> list[AwarenessEvent]:
    """Return all doc.released awareness_event rows for the given document id."""
    async with get_sessionmaker()() as session:
        return (
            (
                await session.execute(
                    select(AwarenessEvent).where(
                        AwarenessEvent.subject_id == uuid.UUID(doc_id),
                        AwarenessEvent.event_key == "doc.released",
                    )
                )
            )
            .scalars()
            .all()
        )


async def test_release_emits_one_awareness_event(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """Releasing an Approved doc writes exactly one doc.released awareness_event (subject_version_id
    + version.label captured), with fanned_out_at IS NULL."""
    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    await s5.set_approver_release(await s5.default_org_id(), True)  # b approves AND releases
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)

    # Drive to Approved, then release.
    type_id = await s5.type_id("SOP")
    did = await s5.drive_to_approved(app_client, ha, hb, type_id, f"ae-v1-{subj.a}".encode())
    rel = await app_client.post(f"/api/v1/documents/{did}/release", headers=hb, json={})
    assert rel.status_code == 200, rel.text
    released_version_id = rel.json()["current_effective_version_id"]
    assert released_version_id is not None

    # Look up the version's revision_label from the DB.
    async with get_sessionmaker()() as session:
        ver = (
            await session.execute(
                select(DocumentVersion).where(DocumentVersion.id == uuid.UUID(released_version_id))
            )
        ).scalar_one()
        expected_label = ver.revision_label

    # Exactly one awareness_event row.
    rows = await _awareness_rows(did)
    assert len(rows) == 1, f"Expected 1 awareness_event, got {len(rows)}"
    ev = rows[0]
    assert ev.subject_type == "DOCUMENT"
    assert ev.subject_version_id == uuid.UUID(released_version_id)
    assert ev.context.get("version.label") == expected_label, (
        f"context['version.label'] = {ev.context.get('version.label')!r}, "
        f"expected {expected_label!r}"
    )
    assert ev.fanned_out_at is None


async def test_concurrent_release_emits_exactly_one_event_loser_409(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """A concurrent-release race (two SERIALIZABLE _cutover calls) leaves exactly ONE
    awareness_event and the loser gets a clean 409 (not a 500, not a phantom row).

    Mirrors test_lifecycle.test_two_effective_impossible: two Approved versions for one doc, both
    targeted by concurrent asyncio.gather POST /release calls. The SERIALIZABLE adjudication
    ensures one wins and one loses; the awareness_event savepoint rolls back with the loser's txn.
    """
    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    ha = _auth(token_factory, subj.a)
    hb = _auth(token_factory, subj.b)

    # Create a document; mint two Approved versions (bypassing the FSM so they genuinely race).
    did = (await _create(app_client, ha, await s5.type_id("SOP")))["id"]

    await app_client.post(f"/api/v1/documents/{did}/checkout", headers=ha)
    sha1 = await _upload(app_client, ha, did, f"ae-concurrent-v1-{subj.a}".encode())
    v1 = (
        await _checkin(app_client, ha, did, sha1, change_reason="v1", change_significance="MAJOR")
    ).json()

    await app_client.post(f"/api/v1/documents/{did}/checkout", headers=ha)
    sha2 = await _upload(app_client, ha, did, f"ae-concurrent-v2-{subj.a}".encode())
    v2 = (
        await _checkin(app_client, ha, did, sha2, change_reason="v2", change_significance="MAJOR")
    ).json()

    # Seed both versions Approved + due (bypassing the FSM) — mirrors test_two_effective_impossible.
    now = datetime.datetime.now(datetime.UTC)
    async with get_sessionmaker()() as session:
        for vid in (v1["id"], v2["id"]):
            ver = (
                await session.execute(
                    select(DocumentVersion).where(DocumentVersion.id == uuid.UUID(vid))
                )
            ).scalar_one()
            ver.version_state = VersionState.Approved
            ver.effective_from = now
        d = (
            await session.execute(
                select(DocumentedInformation).where(DocumentedInformation.id == uuid.UUID(did))
            )
        ).scalar_one()
        d.current_state = DocumentCurrentState.Approved
        await session.commit()

    # Race: two concurrent POST /release calls targeting the two distinct Approved versions. Pair
    # each response with the version_id it targeted so the 200 (the winner) ties to a specific id.
    r1, r2 = await asyncio.gather(
        app_client.post(
            f"/api/v1/documents/{did}/release", headers=hb, json={"version_id": v1["id"]}
        ),
        app_client.post(
            f"/api/v1/documents/{did}/release", headers=hb, json={"version_id": v2["id"]}
        ),
        return_exceptions=True,
    )
    by_version = ((r1, v1["id"]), (r2, v2["id"]))
    statuses = sorted(r.status_code for r, _ in by_version if isinstance(r, httpx.Response))
    assert statuses == [200, 409], (
        f"Expected one 200 + one 409 from the concurrent release race, got statuses={statuses}"
    )
    # The request that returned 200 is the cutover winner — capture the version id it promoted.
    winning_version_id = next(
        vid for r, vid in by_version if isinstance(r, httpx.Response) and r.status_code == 200
    )

    # Exactly one awareness_event — the loser's savepoint rolled back with its txn.
    rows = await _awareness_rows(did)
    assert len(rows) == 1, (
        f"Expected exactly 1 awareness_event (loser's savepoint must roll back), got {len(rows)}"
    )
    ev = rows[0]
    assert ev.subject_type == "DOCUMENT"
    assert ev.fanned_out_at is None
    # The surviving event must carry the WINNER's version id — a race-win that emitted the wrong
    # version's awareness row would otherwise pass the count/status assertions above.
    assert ev.subject_version_id == uuid.UUID(winning_version_id), (
        f"surviving awareness_event subject_version_id={ev.subject_version_id} must equal the "
        f"winner's version id {winning_version_id}"
    )
