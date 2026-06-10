"""The D1 blob integrity verify (S-drift-3, doc 03 §8.2, doc 05 §9.1 row D1).

Re-hash vault blobs against their content-addressed identity (``blob.sha256`` IS the PK) and alarm
on divergence — the only detector for bit-rot, storage-layer tamper, or a broken
blob-row-iff-bytes invariant. **Rolling sample:** each run verifies the K least-recently-verified
rows (``verified_at NULLS FIRST → oldest``), so rotation covers every HEALTHY blob within ⌈N/K⌉
runs in the all-clean steady state (doc 03 §8.2's "rolling sample + full set periodically";
``full=True`` is the on-demand complete pass). M unresolved findings occupy M sample slots every
run (deliberately — see stamp-on-OK-only below), stretching healthy coverage toward
⌈(N-M)/(K-M)⌉; M ≥ K means the vault is on fire and every scan covering the bad set is correct.

**Stamp-on-OK-only is load-bearing:** a finding leaves the blob at the rotation head, so every
subsequent scan re-detects and re-alarms until the operator restores the object — there is no
auto-correction here (unlike the mirror's vault-wins rebuild), and stamping a bad blob would let
the next run's clean sample mask an unresolved corruption as CLEAN on the latest-per-kind status
read. A transient READ_ERROR self-clears the same way (unstamped → re-verified next run).

Posture mirrors ``mirror_scan``: the scan NEVER raises — an object-scoped error is a finding, an
infrastructure-class failure (MinIO/PG down) is an honest FAILED report that salvages the findings
collected so far and mints NO noise findings for unreached rows. ``persist_blob_verify`` writes
the per-finding ``BLOB_INTEGRITY_FAILED`` audit events (``object_type=config`` keyed on the org —
a deduplicated blob has no single owning document; the ``after`` payload carries the sha256), the
``verified_at`` stamps, and the ``drift_scan`` ``kind=BLOB_REHASH`` summary row in ONE
transaction — a persist failure stamps nothing, so the next run redoes the same sample
(self-healing, no ledger). Reads go through the INTERNAL client (``storage.hash_object``) — never
presign (D1 is a worker read, not a browser read).
"""

from __future__ import annotations

import dataclasses
import datetime
import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from botocore.exceptions import ClientError
from sqlalchemy import Select, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...db.models._audit_enums import ActorType, AuditObjectType, EventType
from ...db.models._drift_enums import DriftScanKind, DriftScanStatus
from ...db.models.audit_event import AuditEvent
from ...db.models.blob import Blob
from ...db.models.drift_scan import DriftScan
from ..common.org import get_single_org_id
from . import storage

logger = logging.getLogger("easysynq.vault")

# The D1 classification set (one BLOB_INTEGRITY_FAILED event type — owner fork; the classification
# rides the audit payload). All three are equally alarm-worthy.
CLASS_MISMATCH = "HASH_MISMATCH"
CLASS_MISSING = "OBJECT_MISSING"
CLASS_READ_ERROR = "READ_ERROR"

# S3 error codes that mean "the object is gone" (tamper or a broken blob-row-iff-bytes invariant).
_MISSING_CODES = frozenset({"NoSuchKey", "404", "NoSuchBucket"})

Hasher = Callable[[str, str], Awaitable[str]]


@dataclasses.dataclass(frozen=True, slots=True)
class BlobFinding:
    sha256: str
    bucket: str
    object_key: str
    size_bytes: int
    classification: str  # CLASS_MISMATCH | CLASS_MISSING | CLASS_READ_ERROR
    found_sha256: str | None = None
    note: str | None = None


@dataclasses.dataclass
class BlobVerifyReport:
    scan_id: uuid.UUID
    started_at: datetime.datetime
    status: str  # CLEAN | DIVERGENT | FAILED
    findings: list[BlobFinding]
    ok_shas: list[str]
    total_blobs: int
    sample_limit: int | None  # None = the full set
    error: str | None = None

    def counts(self) -> dict[str, object]:
        by = {CLASS_MISMATCH: 0, CLASS_MISSING: 0, CLASS_READ_ERROR: 0}
        for f in self.findings:
            by[f.classification] += 1
        out: dict[str, object] = {
            "scanned": len(self.ok_shas) + len(self.findings),
            "ok": len(self.ok_shas),
            "mismatched": by[CLASS_MISMATCH],
            "missing": by[CLASS_MISSING],
            "read_errors": by[CLASS_READ_ERROR],
            "stamped": len(self.ok_shas),
            "total_blobs": self.total_blobs,
            "sample_limit": self.sample_limit,
            "full": self.sample_limit is None,
            "scan_id": str(self.scan_id),
        }
        if self.error:
            out["error"] = self.error
        return out


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


async def _default_hasher(object_key: str, bucket: str) -> str:
    return await storage.hash_object(object_key, bucket=bucket)


def build_report(
    *,
    findings: list[BlobFinding],
    ok_shas: list[str],
    total_blobs: int,
    sample_limit: int | None,
    error: str | None = None,
    scan_id: uuid.UUID | None = None,
    started_at: datetime.datetime | None = None,
) -> BlobVerifyReport:
    """FAILED beats DIVERGENT beats CLEAN: an aborted pass is never reported clean, and salvaged
    findings ride the FAILED report (they were real observations)."""
    status = "FAILED" if error else ("DIVERGENT" if findings else "CLEAN")
    return BlobVerifyReport(
        scan_id=scan_id or uuid.uuid4(),
        started_at=started_at or _now(),
        status=status,
        findings=findings,
        ok_shas=ok_shas,
        total_blobs=total_blobs,
        sample_limit=sample_limit,
        error=error,
    )


async def verify_rows(
    rows: Sequence[tuple[str, str, str, int]],
    hasher: Hasher,
) -> tuple[list[BlobFinding], list[str], str | None]:
    """Hash each ``(sha256, bucket, object_key, size_bytes)`` row. Returns
    ``(findings, ok_shas, error)``; a non-None error means an infrastructure-class failure aborted
    the pass (remaining rows NOT reached — no noise findings for them) and the caller reports
    FAILED, salvaging what was collected. NEVER raises."""
    findings: list[BlobFinding] = []
    ok: list[str] = []
    for sha, bucket, key, size in rows:
        try:
            found = await hasher(key, bucket)
        except ClientError as exc:
            code = str((exc.response.get("Error") or {}).get("Code", ""))
            classification = CLASS_MISSING if code in _MISSING_CODES else CLASS_READ_ERROR
            findings.append(
                BlobFinding(sha, bucket, key, size, classification, note=code or type(exc).__name__)
            )
            continue
        except Exception as exc:  # noqa: BLE001 — connection-class/unexpected: abort + salvage, never raise
            return findings, ok, f"{type(exc).__name__}: {exc}"
        if found != sha:
            findings.append(BlobFinding(sha, bucket, key, size, CLASS_MISMATCH, found_sha256=found))
        else:
            ok.append(sha)
    return findings, ok, None


def _sample_stmt(*, limit: int | None) -> Select[Any]:
    """The rotation sample: never-verified rows first (NULLS FIRST), then the oldest stamps, with
    a deterministic sha tiebreak. Column-select, never entities (identity-map hygiene). ``None``
    = the full set."""
    stmt = select(Blob.sha256, Blob.bucket, Blob.object_key, Blob.size_bytes).order_by(
        Blob.verified_at.asc().nulls_first(), Blob.sha256
    )
    return stmt if limit is None else stmt.limit(limit)


async def verify_blobs(
    session: AsyncSession,
    *,
    sample_size: int | None = None,
    full: bool = False,
    hasher: Hasher | None = None,
) -> BlobVerifyReport:
    """The D1 scan: select the rotation sample and re-hash it. DB-read-only;
    ``persist_blob_verify`` is the single writer. NEVER raises (an SQL/infra failure → an honest
    FAILED report)."""
    started = _now()
    scan_id = uuid.uuid4()
    limit: int | None = None
    if not full:
        limit = sample_size if sample_size is not None else get_settings().blob_verify_sample_size
    try:
        total = (await session.execute(select(func.count()).select_from(Blob))).scalar_one()
        rows = [
            (str(r[0]), str(r[1]), str(r[2]), int(r[3]))
            for r in (await session.execute(_sample_stmt(limit=limit))).all()
        ]
        findings, ok, error = await verify_rows(rows, hasher or _default_hasher)
        return build_report(
            findings=findings,
            ok_shas=ok,
            total_blobs=total,
            sample_limit=limit,
            error=error,
            scan_id=scan_id,
            started_at=started,
        )
    except Exception as exc:  # the scan never raises (the mirror_scan posture)
        logger.exception("blob.verify: scan infrastructure failure")
        return build_report(
            findings=[],
            ok_shas=[],
            total_blobs=0,
            sample_limit=limit,
            error=f"{type(exc).__name__}: {exc}",
            scan_id=scan_id,
            started_at=started,
        )


async def persist_blob_verify(
    session: AsyncSession, report: BlobVerifyReport, *, triggered_by: str
) -> bool:
    """ONE txn: a ``BLOB_INTEGRITY_FAILED`` audit event per finding + the verified_at stamps
    (OK rows only) + the ``drift_scan`` BLOB_REHASH summary row. Returns success: a failure is
    logged, never raised, and stamps nothing — the next run redoes the same sample (self-healing).
    NO per-clean-scan audit event (the hourly-CLEAN-spam rule); EVERY scan gets its summary row
    (the row-per-scan contract)."""
    if report.status == "FAILED":
        await session.rollback()  # the failed scan may have poisoned the txn
    try:
        org_id = await get_single_org_id(session)
        if org_id is None:
            logger.warning("blob.verify: no organization yet; results not persisted")
            return False
        finished_at = _now()
        for f in report.findings:
            after: dict[str, object] = {
                "sha256": f.sha256,
                "bucket": f.bucket,
                "object_key": f.object_key,
                "classification": f.classification,
                "found_sha256": f.found_sha256,
                "size_bytes": f.size_bytes,
                "scan_id": str(report.scan_id),
            }
            if f.note:
                after["note"] = f.note
            session.add(
                AuditEvent(
                    org_id=org_id,
                    occurred_at=finished_at,
                    actor_id=None,
                    actor_type=ActorType.system,
                    event_type=EventType.BLOB_INTEGRITY_FAILED,
                    object_type=AuditObjectType.config,
                    object_id=org_id,
                    after=after,
                )
            )
        if report.ok_shas:
            await session.execute(
                update(Blob).where(Blob.sha256.in_(report.ok_shas)).values(verified_at=func.now())
            )
        session.add(
            DriftScan(
                org_id=org_id,
                kind=DriftScanKind.BLOB_REHASH,
                started_at=report.started_at,
                finished_at=finished_at,
                status=DriftScanStatus(report.status),
                counts=report.counts(),
                triggered_by=triggered_by,
            )
        )
        await session.commit()
        return True
    except Exception:  # persistence must never raise into the pipeline
        logger.exception("blob.verify: failed to persist results")
        await session.rollback()
        return False
