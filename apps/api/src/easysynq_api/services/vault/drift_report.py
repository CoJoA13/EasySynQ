"""Read-only drift reporting (S-drift-3): the admin status read + the D4 superseded-copies report.

D4 (doc 05 §9.1 / R11): ``EXPORTED``/``PRINTED`` audit events are emitted only for the
THEN-Effective version (``render_dynamic_copy``), so any such event whose version is now
Superseded/Obsolete is by construction an outstanding copy of a superseded rendition. There is no
decrement leg (a paper copy cannot be un-printed): the count is the honest upper bound, and the
S7c verify token is the per-copy resolution. Copies of the CURRENTLY Effective version are
deliberately excluded — they are controlled, not outstanding. Per doc 05 §9.2.1, D4 is the ONLY
detection leg that reaches copies outside the mirror.

The status read is the seam S-drift-2 reserved: the latest ``drift_scan`` per kind rides
``ix_drift_scan_kind_started_at`` (DISTINCT ON), plus the D1 rolling-cursor coverage
(``blob.verified_at``) and the D4 headline. LIVE reads, no persistence, no side effects.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from ...db.models._audit_enums import AuditObjectType, EventType
from ...db.models._drift_enums import DriftScanKind
from ...db.models._vault_enums import VersionState
from ...db.models.audit_event import AuditEvent
from ...db.models.blob import Blob
from ...db.models.document_version import DocumentVersion
from ...db.models.documented_information import DocumentedInformation
from ...db.models.drift_scan import DriftScan

_COPY_EVENTS = (EventType.EXPORTED, EventType.PRINTED)
_OUTSTANDING_STATES = (VersionState.Superseded, VersionState.Obsolete)


def _superseded_base() -> Select[Any]:
    """One grouped row per (now-superseded version that has at least one EXPORTED/PRINTED event),
    with the document's CURRENT effective revision label for the operator's recall list
    (NULL-safe outer join — an obsoleted document has no effective version)."""
    cur = aliased(DocumentVersion)
    return (
        select(
            DocumentVersion.document_id.label("document_id"),
            DocumentedInformation.identifier.label("identifier"),
            DocumentVersion.id.label("version_id"),
            DocumentVersion.revision_label.label("revision_label"),
            DocumentVersion.version_state.label("version_state"),
            cur.revision_label.label("current_revision_label"),
            func.count().filter(AuditEvent.event_type == EventType.EXPORTED).label("exported"),
            func.count().filter(AuditEvent.event_type == EventType.PRINTED).label("printed"),
            func.max(AuditEvent.occurred_at).label("last_copy_at"),
        )
        .join_from(AuditEvent, DocumentVersion, DocumentVersion.id == AuditEvent.object_id)
        .join(
            DocumentedInformation,
            DocumentedInformation.id == DocumentVersion.document_id,
        )
        .outerjoin(cur, cur.id == DocumentedInformation.current_effective_version_id)
        .where(
            AuditEvent.event_type.in_(_COPY_EVENTS),
            AuditEvent.object_type == AuditObjectType.version,
            DocumentVersion.version_state.in_(_OUTSTANDING_STATES),
        )
        .group_by(
            DocumentVersion.document_id,
            DocumentedInformation.identifier,
            DocumentVersion.id,
            DocumentVersion.revision_label,
            DocumentVersion.version_state,
            cur.revision_label,
        )
    )


async def _superseded_totals(session: AsyncSession) -> tuple[int, int]:
    sub = _superseded_base().subquery()
    versions, copies = (
        await session.execute(
            select(
                func.count(), func.coalesce(func.sum(sub.c.exported + sub.c.printed), 0)
            ).select_from(sub)
        )
    ).one()
    return int(versions), int(copies)


async def superseded_copies(
    session: AsyncSession, *, limit: int = 50, offset: int = 0
) -> dict[str, Any]:
    """The D4 report: per-version outstanding-copy rows (newest copy first) + full-set totals
    (computed over the WHOLE filtered set, not the page)."""
    versions, copies = await _superseded_totals(session)
    sub = _superseded_base().subquery()
    rows = (
        await session.execute(
            select(sub)
            .order_by(desc(sub.c.last_copy_at), sub.c.version_id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    items = [
        {
            "document_id": str(r.document_id),
            "identifier": r.identifier,
            "version_id": str(r.version_id),
            "revision_label": r.revision_label,
            "version_state": r.version_state.value,
            "current_revision_label": r.current_revision_label,
            "exported": int(r.exported),
            "printed": int(r.printed),
            "last_copy_at": r.last_copy_at.isoformat(),
        }
        for r in rows
    ]
    return {"total": {"versions": versions, "copies": copies}, "items": items}


async def drift_status(session: AsyncSession) -> dict[str, Any]:
    """The thin admin status read: latest scan per kind (null until that scanner's first run) +
    the D1 coverage block + the D4 headline."""
    latest = (
        (
            await session.execute(
                select(DriftScan)
                .distinct(DriftScan.kind)
                .order_by(DriftScan.kind, DriftScan.started_at.desc())
            )
        )
        .scalars()
        .all()
    )
    scans: dict[str, Any] = {k.value: None for k in DriftScanKind}
    for row in latest:
        scans[row.kind.value] = {
            "status": row.status.value,
            "started_at": row.started_at.isoformat(),
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
            "counts": row.counts,
            "triggered_by": row.triggered_by,
        }
    total, never, failing, oldest = (
        await session.execute(
            select(
                func.count(),
                func.count().filter(Blob.verified_at.is_(None)),
                # The unresolved-finding pins (verify_failed_at) — the direct operator signal
                # behind a DIVERGENT BLOB_REHASH leg; 0 once every alarm is resolved.
                func.count().filter(Blob.verify_failed_at.is_not(None)),
                func.min(Blob.verified_at),
            ).select_from(Blob)
        )
    ).one()
    versions, copies = await _superseded_totals(session)
    return {
        "scans": scans,
        "blob_coverage": {
            "total": int(total),
            "never_verified": int(never),
            "failing": int(failing),
            "oldest_verified_at": oldest.isoformat() if oldest is not None else None,
        },
        "superseded_copies": {"versions": versions, "copies": copies},
    }
