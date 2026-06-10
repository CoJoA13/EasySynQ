"""S-drift-3 integration proofs — the D4 superseded-copies report + the drift status read.

D4 events are PLANTED directly (render_dynamic_copy 409s without a real Gotenberg rendition in
this env; the emitter is covered by the S7d tests — the report's contract is the audit-trail
shape: event_type EXPORTED/PRINTED, object_type=version, object_id=version_id). ⚠ Run-scoped
assertions only: every lookup filters to THIS test's document/identifier; totals are asserted as
deltas, never absolutes. SoD-2: the approver (subj.b) releases, never the author.
"""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Callable
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models._audit_enums import ActorType, AuditObjectType, EventType
from easysynq_api.db.models._vault_enums import VersionState
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.document_version import DocumentVersion
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.vault.blob_verify import persist_blob_verify, verify_blobs
from easysynq_api.services.vault.drift_report import drift_status, superseded_copies

from . import s5_helpers as s5
from .test_mirror import _grant_release_actors
from .test_vault import _auth, _checkin, _upload

pytestmark = pytest.mark.integration


@pytest.fixture
def subj() -> SimpleNamespace:
    salt = uuid.uuid4().hex[:10]
    return SimpleNamespace(a=f"kc-author-{salt}", b=f"kc-approver-{salt}", salt=salt)


async def _versions_of(document_id: str) -> list[DocumentVersion]:
    async with get_sessionmaker()() as s:
        return list(
            (
                await s.execute(
                    select(DocumentVersion)
                    .where(DocumentVersion.document_id == uuid.UUID(document_id))
                    .order_by(DocumentVersion.version_seq)
                )
            )
            .scalars()
            .all()
        )


async def _plant_copy_event(version_id: uuid.UUID, event_type: EventType) -> None:
    """Plant the exact row render_dynamic_copy emits (system actor: simplest FK-safe shape)."""
    async with get_sessionmaker()() as s:
        org_id = await s5.default_org_id()
        s.add(
            AuditEvent(
                org_id=org_id,
                occurred_at=datetime.datetime.now(datetime.UTC),
                actor_id=None,
                actor_type=ActorType.system,
                event_type=event_type,
                object_type=AuditObjectType.version,
                object_id=version_id,
            )
        )
        await s.commit()


async def _supersede(
    app_client: AsyncClient,
    ha: dict[str, str],
    hb: dict[str, str],
    document_id: str,
    content: bytes,
) -> None:
    """The test_mirror_scan supersession recipe: revise → approve → release (v_prev → Superseded).

    Drives the document from Effective to Superseded by authoring and releasing a v2.
    """
    await app_client.post(f"/api/v1/documents/{document_id}/start-revision", headers=ha)
    sha2 = await _upload(app_client, ha, document_id, content)
    await _checkin(
        app_client, ha, document_id, sha2, change_reason="v2", change_significance="MINOR"
    )
    await app_client.post(f"/api/v1/documents/{document_id}/submit-review", headers=ha)
    task_id = await s5.task_for_doc(document_id)
    await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hb, json={"outcome": "approve"}
    )
    rel = await app_client.post(f"/api/v1/documents/{document_id}/release", headers=hb, json={})
    assert rel.status_code == 200, rel.text


async def test_superseded_copies_counts_only_non_effective_versions(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
) -> None:
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    doc = await s5.drive_to_effective(
        app_client, ha, hb, hb, await s5.type_id("SOP"), f"D4-V1-{subj.salt}".encode()
    )
    did = doc["id"]
    v1 = (await _versions_of(did))[0]
    # Copies made while v1 governed (the only window render_dynamic_copy serves it).
    await _plant_copy_event(v1.id, EventType.EXPORTED)
    await _plant_copy_event(v1.id, EventType.PRINTED)

    # While v1 is still Effective the report must NOT count it (controlled, not outstanding).
    async with get_sessionmaker()() as s:
        before = await superseded_copies(s, limit=500)
    assert not [i for i in before["items"] if i["document_id"] == did]

    await _supersede(app_client, ha, hb, did, f"D4-V2-{subj.salt}".encode())
    versions = await _versions_of(did)
    v1_after, v2 = versions[0], versions[1]
    assert v1_after.version_state in (VersionState.Superseded, VersionState.Obsolete)
    # A copy of the NEW Effective version stays excluded.
    await _plant_copy_event(v2.id, EventType.EXPORTED)

    async with get_sessionmaker()() as s:
        after = await superseded_copies(s, limit=500)
    mine = [i for i in after["items"] if i["document_id"] == did]
    assert len(mine) == 1
    row = mine[0]
    assert row["version_id"] == str(v1.id)
    assert row["exported"] == 1 and row["printed"] == 1
    assert row["identifier"] == doc["identifier"]
    assert row["current_revision_label"] == v2.revision_label
    assert row["last_copy_at"] is not None
    # Delta-based totals: ours added exactly one version and two copies.
    assert after["total"]["versions"] >= before["total"]["versions"] + 1
    assert after["total"]["copies"] >= before["total"]["copies"] + 2


async def test_drift_status_shape_and_blob_rehash_leg(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
) -> None:
    """After a verify run, the BLOB_REHASH leg is non-null with the summary shape; coverage and
    headline blocks are present (run-scoped: ≥ / non-null, never absolutes on the shared DB)."""
    async with get_sessionmaker()() as s:
        report = await verify_blobs(s, sample_size=1)
        assert await persist_blob_verify(s, report, triggered_by="cli") is True
        status = await drift_status(s)

    assert set(status) == {"scans", "blob_coverage", "superseded_copies"}
    assert set(status["scans"]) == {"MIRROR", "BLOB_REHASH"}
    leg = status["scans"]["BLOB_REHASH"]
    assert leg is not None
    assert leg["status"] in ("CLEAN", "DIVERGENT", "FAILED")
    assert leg["triggered_by"] in ("beat", "sync", "cli")
    assert "scan_id" in leg["counts"]
    cov = status["blob_coverage"]
    assert cov["total"] >= 0 and cov["never_verified"] >= 0
    sc = status["superseded_copies"]
    assert sc["versions"] >= 0 and sc["copies"] >= 0
