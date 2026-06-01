"""Read-only audit-trail surface (slice S6, doc 15 §8.13).

The auditor's primary evidence surface over the append-only, hash-chained ``audit_event`` journal.
**No write verbs ever** — append-only + hash-chained is a *system invariant*, not an API capability
(the app DB role lacks UPDATE/DELETE on the table). All routes are gated by
``system.audit_log.read`` (the purpose-built SYSTEM-domain key seeded in 0004 — the catalog's name
for the immutable trail; doc 15 §8.13's ``audit.read`` is the pre-catalog-split spelling). The
export endpoint (doc 15 §8.13) is deferred — it needs the async-job pattern (D-9); its schema
stays in ``openapi.yaml`` but is not mounted.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models._audit_enums import ActorType, AuditObjectType, EventType
from ..db.models.app_user import AppUser
from ..db.models.audit_checkpoint_sink import AuditCheckpointSink
from ..db.models.audit_event import AuditEvent
from ..db.models.documented_information import DocumentedInformation
from ..db.session import get_session
from ..problems import ProblemException
from ..services.audit.checkpoint import tamper_evidence_attested
from ..services.audit.verify import verify_chain
from ..services.authz import require

router = APIRouter(prefix="/api/v1", tags=["audit"])

# All audit reads require the SYSTEM-domain audit-log permission (deny-by-default).
_audit_read = require("system.audit_log.read")

_PAGE_DEFAULT = 50
_PAGE_MAX = 200


def _hex(value: bytes | None) -> str | None:
    return value.hex() if value is not None else None


def _event(event: AuditEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "org_id": str(event.org_id),
        "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
        "actor_id": str(event.actor_id) if event.actor_id else None,
        "actor_type": event.actor_type.value,
        "event_type": event.event_type.value,
        "object_type": event.object_type.value,
        "object_id": str(event.object_id) if event.object_id else None,
        "scope_ref": event.scope_ref,
        "reason": event.reason,
        "before": event.before,
        "after": event.after,
        "request_id": str(event.request_id) if event.request_id else None,
        "client_ip": event.client_ip,
        "user_agent": event.user_agent,
        "auth_context": event.auth_context,
        "prev_hash": _hex(event.prev_hash),
        "row_hash": _hex(event.row_hash),
        "chained_at": event.chained_at.isoformat() if event.chained_at else None,
        "signature_event_id": (str(event.signature_event_id) if event.signature_event_id else None),
    }


@router.get("/audit-events")
async def list_audit_events(
    caller: AppUser = Depends(_audit_read),
    session: AsyncSession = Depends(get_session),
    cursor: int | None = Query(None, description="page rows with id < cursor (keyset, desc)"),
    limit: int = Query(_PAGE_DEFAULT, ge=1, le=_PAGE_MAX),
    actor_id: uuid.UUID | None = None,
    actor_type: str | None = None,
    event_type: str | None = None,
    object_type: str | None = None,
    object_id: uuid.UUID | None = None,
    occurred_from: datetime.datetime | None = None,
    occurred_to: datetime.datetime | None = None,
) -> dict[str, Any]:
    """Org-wide trail, newest first (keyset-paginated on ``id``). Filters per doc 15 §8.13."""
    try:
        actor_type_enum = ActorType(actor_type) if actor_type else None
        event_type_enum = EventType(event_type) if event_type else None
        object_type_enum = AuditObjectType(object_type) if object_type else None
    except ValueError as exc:
        raise ProblemException(
            status=422, code="validation_error", title="Invalid audit filter value"
        ) from exc
    stmt = select(AuditEvent).where(AuditEvent.org_id == caller.org_id)
    if cursor is not None:
        stmt = stmt.where(AuditEvent.id < cursor)
    if actor_id is not None:
        stmt = stmt.where(AuditEvent.actor_id == actor_id)
    if actor_type_enum is not None:
        stmt = stmt.where(AuditEvent.actor_type == actor_type_enum)
    if event_type_enum is not None:
        stmt = stmt.where(AuditEvent.event_type == event_type_enum)
    if object_type_enum is not None:
        stmt = stmt.where(AuditEvent.object_type == object_type_enum)
    if object_id is not None:
        stmt = stmt.where(AuditEvent.object_id == object_id)
    if occurred_from is not None:
        stmt = stmt.where(AuditEvent.occurred_at >= occurred_from)
    if occurred_to is not None:
        stmt = stmt.where(AuditEvent.occurred_at <= occurred_to)
    stmt = stmt.order_by(AuditEvent.id.desc()).limit(limit)
    events = (await session.execute(stmt)).scalars().all()
    next_cursor = events[-1].id if len(events) == limit else None
    return {"events": [_event(e) for e in events], "next_cursor": next_cursor}


@router.get("/audit-events/verify-chain")
async def verify_chain_endpoint(
    caller: AppUser = Depends(_audit_read),
    session: AsyncSession = Depends(get_session),
    from_id: int | None = Query(None, alias="from"),
    to_id: int | None = Query(None, alias="to"),
) -> dict[str, Any]:
    """On-demand hash-chain verification (tamper-evidence). The nightly Beat job runs this too.
    Scoped to the caller's org (the chain is per-org — doc 12 §4.3)."""
    result = await verify_chain(session, caller.org_id, from_id=from_id, to_id=to_id)
    return {
        "verified": result.verified,
        "checked": result.checked,
        "pending": result.pending,
        "breaks": [{"at_id": b.at_id, "reason": b.reason} for b in result.breaks],
    }


@router.get("/audit/status")
async def audit_status(
    caller: AppUser = Depends(_audit_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """The tamper-evidence soft-gate the SPA reads (R13). ``tamper_evidence_attested`` is false
    until a genuinely off-host anchor exists — drives the persistent 'NOT tamper-evident' banner."""
    attested = await tamper_evidence_attested(session, caller.org_id)
    sink_enabled = (
        await session.execute(
            select(func.count())
            .select_from(AuditCheckpointSink)
            .where(
                AuditCheckpointSink.org_id == caller.org_id,
                AuditCheckpointSink.enabled.is_(True),
            )
        )
    ).scalar_one()
    oldest_unchained = (
        await session.execute(
            select(func.min(AuditEvent.occurred_at)).where(
                AuditEvent.org_id == caller.org_id, AuditEvent.chained_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    lag = 0.0
    if oldest_unchained is not None:
        if oldest_unchained.tzinfo is None:
            oldest_unchained = oldest_unchained.replace(tzinfo=datetime.UTC)
        lag = max(0.0, (datetime.datetime.now(datetime.UTC) - oldest_unchained).total_seconds())
    return {
        "tamper_evidence_attested": attested,
        "sink_enabled": int(sink_enabled) > 0,
        "chain_lag_seconds": lag,
    }


@router.get("/audit-events/{event_id}")
async def get_audit_event(
    event_id: int,
    caller: AppUser = Depends(_audit_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """A single event incl. before/after diff, reason, auth_context, and prev_hash/row_hash."""
    event = (
        await session.execute(
            select(AuditEvent).where(AuditEvent.id == event_id, AuditEvent.org_id == caller.org_id)
        )
    ).scalar_one_or_none()
    if event is None:
        raise ProblemException(status=404, code="not_found", title="Audit event not found")
    return _event(event)


@router.get("/documents/{document_id}/audit-events")
async def document_audit_events(
    document_id: uuid.UUID,
    caller: AppUser = Depends(_audit_read),
    session: AsyncSession = Depends(get_session),
    cursor: int | None = Query(None),
    limit: int = Query(_PAGE_DEFAULT, ge=1, le=_PAGE_MAX),
) -> dict[str, Any]:
    """One document's full trail (doc + version events). Every vault/lifecycle row carries the
    controlled identifier in ``scope_ref``, so the history is ``scope_ref == identifier``."""
    doc = await session.get(DocumentedInformation, document_id)
    if doc is None or doc.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="Document not found")
    stmt = select(AuditEvent).where(
        AuditEvent.org_id == caller.org_id, AuditEvent.scope_ref == doc.identifier
    )
    if cursor is not None:
        stmt = stmt.where(AuditEvent.id < cursor)
    stmt = stmt.order_by(AuditEvent.id.desc()).limit(limit)
    events = (await session.execute(stmt)).scalars().all()
    next_cursor = events[-1].id if len(events) == limit else None
    return {"events": [_event(e) for e in events], "next_cursor": next_cursor}
