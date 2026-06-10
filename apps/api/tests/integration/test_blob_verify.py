"""S-drift-3 integration proofs — D1 blob verify end-to-end against the real vault + MinIO.

Synthetic tamper via planted blob ROWS (never fight WORM object-lock): a row whose sha256 doesn't
match the real bytes it points at → HASH_MISMATCH; a row pointing at a nonexistent key →
OBJECT_MISSING. ⚠ Run-scoped/delta assertions only (the shared session DB): every audit/drift_scan
lookup keys on THIS scan's scan_id; planted rows are run-unique and DELETED in finally (a leaked
plant would fail other runs' clean passes). ⚠ The full=True passes here STAMP verified_at on every
clean blob in the shared DB — a future test asserting ``verified_at IS NULL`` on a blob created in
an EARLIER file would silently break (assert only on rows you created this-test). SoD-2: releases
come from the approver, never the author.
"""

from __future__ import annotations

import datetime
import hashlib
import uuid
from collections.abc import Callable
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, func, select, update

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

        # Findings keep verified_at NULL ("last verified OK" stays pure) AND gain the
        # verify_failed_at pin → the rotation head → the NEXT scan re-alarms.
        async with get_sessionmaker()() as s:
            stamps = (
                await s.execute(
                    select(Blob.sha256, Blob.verified_at, Blob.verify_failed_at).where(
                        Blob.sha256.in_([fake_sha, missing_sha])
                    )
                )
            ).all()
        assert all(v is None for _, v, _pin in stamps)
        assert all(pin is not None for _, _v, pin in stamps)

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
    end; the pinned-first/NULLS-FIRST/oldest ordering contract is proven by the compiled-SQL unit
    test (test_sample_stmt_orders_pinned_then_nulls_then_oldest) and the ROLLING latch test below
    — a bare shared-DB ordering assertion would race other tests' rows."""
    async with get_sessionmaker()() as s:
        report = await verify_blobs(s, sample_size=0)
    assert report.status == "CLEAN"
    assert report.counts()["scanned"] == 0


async def _pinned_count() -> int:
    async with get_sessionmaker()() as s:
        return int(
            (
                await s.execute(
                    select(func.count()).select_from(Blob).where(Blob.verify_failed_at.is_not(None))
                )
            ).scalar_one()
        )


async def test_rolling_latch_pins_findings_ahead_of_null_backlog(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
) -> None:
    """The diff-critic MAJOR's regression proof, on the ROLLING path the daily task actually runs:
    a detected finding is verify_failed_at-pinned and stays in the sample ahead of a fresh
    never-verified (NULL) backlog — and a pinned row that re-hashes CLEAN is unpinned + stamped.
    Sample sizes are computed from the LIVE pinned count, so foreign pinned rows (none today)
    cannot flake the proof."""
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    doc = await s5.drive_to_effective(
        app_client, ha, hb, hb, await s5.type_id("SOP"), f"D1-LATCH-{subj.salt}".encode()
    )
    real = await _source_blob_of(doc["id"])
    bad_sha = hashlib.sha256(f"planted-latch-{subj.salt}".encode()).hexdigest()

    async with get_sessionmaker()() as s:
        s.add(
            Blob(
                sha256=bad_sha,
                org_id=real.org_id,
                size_bytes=real.size_bytes,
                mime_type="application/octet-stream",
                bucket=real.bucket,
                object_key=real.object_key,  # real bytes, wrong claimed digest → MISMATCH
            )
        )
        await s.commit()

    try:
        # 1. Detect + pin (full pass: deterministic first detection).
        async with get_sessionmaker()() as s:
            report = await verify_blobs(s, full=True)
            assert await persist_blob_verify(s, report, triggered_by="beat") is True
        assert bad_sha in {f.sha256 for f in report.findings}

        # 2. Grow a fresh NULL backlog (a new doc → a new never-verified blob).
        doc2 = await s5.drive_to_effective(
            app_client, ha, hb, hb, await s5.type_id("SOP"), f"D1-LATCH2-{subj.salt}".encode()
        )
        fresh = await _source_blob_of(doc2["id"])
        assert fresh.verified_at is None

        # 3. A ROLLING scan sized to the pinned set must still contain the pinned finding —
        #    pre-fix, the NULL backlog would crowd it out and the scan would read CLEAN.
        k = await _pinned_count()
        assert k >= 1
        async with get_sessionmaker()() as s:
            rolling = await verify_blobs(s, sample_size=k)
            assert await persist_blob_verify(s, rolling, triggered_by="beat") is True
        assert bad_sha in {f.sha256 for f in rolling.findings}
        assert rolling.status == "DIVERGENT"
        row = await _scan_row(rolling.scan_id)
        assert row is not None and row.status is DriftScanStatus.DIVERGENT

        # 4. Clear-on-pass: pin the REAL (clean) blob by hand, roll a pinned-set-sized sample —
        #    it re-hashes OK → unpinned + verified_at stamped (the operator-restored path).
        async with get_sessionmaker()() as s:
            await s.execute(
                update(Blob)
                .where(Blob.sha256 == real.sha256)
                .values(verify_failed_at=datetime.datetime.now(datetime.UTC))
            )
            await s.commit()
        k = await _pinned_count()
        async with get_sessionmaker()() as s:
            clearing = await verify_blobs(s, sample_size=k)
            assert await persist_blob_verify(s, clearing, triggered_by="beat") is True
        assert real.sha256 in clearing.ok_shas
        async with get_sessionmaker()() as s:
            pin, stamp = (
                await s.execute(
                    select(Blob.verify_failed_at, Blob.verified_at).where(
                        Blob.sha256 == real.sha256
                    )
                )
            ).one()
        assert pin is None and stamp is not None
    finally:
        async with get_sessionmaker()() as s:
            await s.execute(delete(Blob).where(Blob.sha256 == bad_sha))
            await s.commit()
