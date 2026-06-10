"""S-drift-3 integration proofs — D1 blob verify end-to-end against the real vault + MinIO.

Synthetic tamper via planted blob ROWS (never fight WORM object-lock): a row whose sha256 doesn't
match the real bytes it points at → HASH_MISMATCH; a row pointing at a nonexistent key →
OBJECT_MISSING. ⚠ Run-scoped/delta assertions only (the shared session DB): every audit/drift_scan
lookup keys on THIS scan's scan_id; planted rows are run-unique and DELETED in finally (a leaked
plant would fail other runs' clean passes). SoD-2: releases come from the approver, never the
author.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select

from easysynq_api.db.models._audit_enums import EventType
from easysynq_api.db.models._drift_enums import DriftScanKind, DriftScanStatus
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.blob import Blob
from easysynq_api.db.models.document_version import DocumentVersion
from easysynq_api.db.models.drift_scan import DriftScan
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.vault.blob_verify import (
    CLASS_MISMATCH,
    CLASS_MISSING,
    persist_blob_verify,
    verify_blobs,
)

from . import s5_helpers as s5
from .test_mirror import _grant_release_actors
from .test_vault import _auth

pytestmark = pytest.mark.integration


@pytest.fixture
def subj() -> SimpleNamespace:
    salt = uuid.uuid4().hex[:10]
    return SimpleNamespace(a=f"kc-author-{salt}", b=f"kc-approver-{salt}", salt=salt)


async def _source_blob_of(document_id: str) -> Blob:
    async with get_sessionmaker()() as s:
        v1 = (
            await s.execute(
                select(DocumentVersion)
                .where(DocumentVersion.document_id == uuid.UUID(document_id))
                .order_by(DocumentVersion.version_seq)
                .limit(1)
            )
        ).scalar_one()
        return (
            await s.execute(select(Blob).where(Blob.sha256 == v1.source_blob_sha256))
        ).scalar_one()


async def _events_for_scan(scan_id: uuid.UUID) -> list[AuditEvent]:
    async with get_sessionmaker()() as s:
        return list(
            (
                await s.execute(
                    select(AuditEvent).where(AuditEvent.after["scan_id"].astext == str(scan_id))
                )
            )
            .scalars()
            .all()
        )


async def _scan_row(scan_id: uuid.UUID) -> DriftScan | None:
    async with get_sessionmaker()() as s:
        return (
            await s.execute(
                select(DriftScan).where(DriftScan.counts["scan_id"].astext == str(scan_id))
            )
        ).scalar_one_or_none()


async def test_clean_pass_stamps_verified_at_and_writes_clean_row(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
) -> None:
    """A legit blob hashes clean → verified_at stamped, NO audit event, a summary row."""
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    doc = await s5.drive_to_effective(
        app_client, ha, hb, hb, await s5.type_id("SOP"), f"D1-CLEAN-{subj.salt}".encode()
    )
    real = await _source_blob_of(doc["id"])
    assert real.verified_at is None  # never verified yet

    async with get_sessionmaker()() as s:
        report = await verify_blobs(s, full=True)
        assert await persist_blob_verify(s, report, triggered_by="cli") is True

    # Run-scoped: OUR blob was stamped and is not a finding; no event carries our sha.
    assert real.sha256 in report.ok_shas
    assert not [f for f in report.findings if f.sha256 == real.sha256]
    async with get_sessionmaker()() as s:
        stamped = (
            await s.execute(select(Blob.verified_at).where(Blob.sha256 == real.sha256))
        ).scalar_one()
    assert stamped is not None
    row = await _scan_row(report.scan_id)
    assert row is not None and row.kind is DriftScanKind.BLOB_REHASH
    assert row.triggered_by == "cli"
    events = await _events_for_scan(report.scan_id)
    assert not [e for e in events if (e.after or {}).get("sha256") == real.sha256]


async def test_planted_tamper_alarms_and_realarm_until_resolved(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
) -> None:
    """A wrong-sha row over real bytes → HASH_MISMATCH; a row with no object → OBJECT_MISSING.
    Both alarm BLOB_INTEGRITY_FAILED, stay UNSTAMPED, and re-alarm on the next scan (the
    persistent-alarm contract — no auto-correction exists for blobs)."""
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    doc = await s5.drive_to_effective(
        app_client, ha, hb, hb, await s5.type_id("SOP"), f"D1-TAMPER-{subj.salt}".encode()
    )
    real = await _source_blob_of(doc["id"])
    fake_sha = hashlib.sha256(f"planted-mismatch-{subj.salt}".encode()).hexdigest()
    missing_sha = hashlib.sha256(f"planted-missing-{subj.salt}".encode()).hexdigest()

    async with get_sessionmaker()() as s:
        s.add(
            Blob(
                sha256=fake_sha,
                org_id=real.org_id,
                size_bytes=real.size_bytes,
                mime_type="application/octet-stream",
                bucket=real.bucket,
                object_key=real.object_key,  # real bytes, wrong claimed digest → MISMATCH
            )
        )
        s.add(
            Blob(
                sha256=missing_sha,
                org_id=real.org_id,
                size_bytes=3,
                mime_type="application/octet-stream",
                bucket=real.bucket,
                object_key=f"nonexistent/{subj.salt}",  # no bytes → MISSING
            )
        )
        await s.commit()

    try:
        async with get_sessionmaker()() as s:
            report = await verify_blobs(s, full=True)
            assert await persist_blob_verify(s, report, triggered_by="beat") is True

        mine = {f.sha256: f for f in report.findings if f.sha256 in (fake_sha, missing_sha)}
        assert mine[fake_sha].classification == CLASS_MISMATCH
        assert mine[fake_sha].found_sha256 == real.sha256  # the real bytes' actual digest
        assert mine[missing_sha].classification == CLASS_MISSING
        assert report.status == "DIVERGENT"

        events = await _events_for_scan(report.scan_id)
        by_sha = {(e.after or {}).get("sha256"): e for e in events}
        assert by_sha[fake_sha].event_type is EventType.BLOB_INTEGRITY_FAILED
        assert by_sha[missing_sha].event_type is EventType.BLOB_INTEGRITY_FAILED
        assert (by_sha[fake_sha].after or {})["classification"] == CLASS_MISMATCH

        row = await _scan_row(report.scan_id)
        assert row is not None and row.status is DriftScanStatus.DIVERGENT

        # Findings are NOT stamped → still at the rotation head → the NEXT scan re-alarms.
        async with get_sessionmaker()() as s:
            stamps = (
                await s.execute(
                    select(Blob.sha256, Blob.verified_at).where(
                        Blob.sha256.in_([fake_sha, missing_sha])
                    )
                )
            ).all()
        assert all(v is None for _, v in stamps)

        async with get_sessionmaker()() as s:
            report2 = await verify_blobs(s, full=True)
            assert await persist_blob_verify(s, report2, triggered_by="beat") is True
        again = {f.sha256 for f in report2.findings}
        assert {fake_sha, missing_sha} <= again
        assert len(await _events_for_scan(report2.scan_id)) >= 2  # re-audited under a NEW scan id
    finally:
        async with get_sessionmaker()() as s:
            await s.execute(delete(Blob).where(Blob.sha256.in_([fake_sha, missing_sha])))
            await s.commit()


async def test_rolling_sample_orders_by_verified_at_nulls_first(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
) -> None:
    """sample_size=0 → empty sample (CLEAN, nothing scanned) proves the LIMIT is honored end to
    end; the NULLS-FIRST/oldest ordering contract is proven by the compiled-SQL unit test
    (test_sample_stmt_orders_nulls_first_then_oldest) — a shared-DB ordering assertion would race
    other tests' rows."""
    async with get_sessionmaker()() as s:
        report = await verify_blobs(s, sample_size=0)
    assert report.status == "CLEAN"
    assert report.counts()["scanned"] == 0
