"""The authorization audit hook (S2 seam, wired to the DB in S6).

Every PDP decision — allow and deny — is emitted to an ``AuthzAuditSink``. Unlike the vault sink,
the authz sink owns its **own short dedicated transaction**: a deny is a 403 that aborts the
request, so there is no host transaction to be atomic with (and an authz decision is a terminal
event with no rollback risk — doc 12 §4.4 governs *state changes*, not decisions). Hence
``record`` is async and the ``DbAuthzAuditSink`` opens a fresh session, INSERTs one row, commits,
and disposes — off the request's primary session.

By default only **denied-access attempts** are persisted (doc 12 §4.1: "denied-access attempts
(configurable verbosity)"); routine allows are log-only — persisting an ``audit_event`` per read
request would balloon the hash-chained table. ``settings.audit_persist_allows`` turns on the full
verbosity. Tests inject a ``CapturingAuthzAuditSink`` to prove the "every allow+deny emits a hook"
invariant; its ``record`` is async too.
"""

from __future__ import annotations

import dataclasses
import datetime
import logging
import uuid
from typing import Protocol

from ...config import get_settings
from ...db.models._audit_enums import ActorType, AuditObjectType, EventType
from ...db.models.audit_event import AuditEvent
from ...db.session import get_sessionmaker

logger = logging.getLogger("easysynq.authz")


@dataclasses.dataclass(frozen=True, slots=True)
class AuthzAuditEvent:
    """A single authorization decision, the shape S6 persists to ``audit_event``."""

    occurred_at: datetime.datetime
    actor_id: str | None
    permission_key: str
    decision: str  # "allow" | "deny"
    reason: str
    org_id: str | None = None
    scope_ref: str | None = None
    source: str | None = None
    request_id: str | None = None


def _maybe_uuid(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def to_audit_event(event: AuthzAuditEvent) -> AuditEvent:
    """Project an authz decision onto an ``audit_event`` row (object_type=permission)."""
    if event.decision == "deny":
        event_type = (
            EventType.TWO_TIER_VIOLATION
            if event.reason == "two_tier_violation"
            else EventType.ACCESS_DENIED
        )
    else:
        event_type = EventType.ACCESS_ALLOWED
    return AuditEvent(
        org_id=_maybe_uuid(event.org_id),
        occurred_at=event.occurred_at,
        actor_id=_maybe_uuid(event.actor_id),
        actor_type=ActorType.user,
        event_type=event_type,
        object_type=AuditObjectType.permission,
        scope_ref=event.scope_ref,
        reason=event.reason,
        request_id=_maybe_uuid(event.request_id),
        after={
            "permission_key": event.permission_key,
            "decision": event.decision,
            "source": event.source,
        },
    )


class AuthzAuditSink(Protocol):
    async def record(self, event: AuthzAuditEvent) -> None: ...


class DbAuthzAuditSink:
    """Production sink (S6) — persists denies (and, if configured, allows) to ``audit_event`` in
    their own short transaction, off the request's primary session."""

    async def record(self, event: AuthzAuditEvent) -> None:
        if event.decision != "deny" and not get_settings().audit_persist_allows:
            logger.debug("authz.allow %s", event.permission_key)
            return
        async with get_sessionmaker()() as session:
            session.add(to_audit_event(event))
            await session.commit()


class LoggingAuthzAuditSink:
    """Structured-log sink (kept for non-DB contexts / debugging)."""

    async def record(self, event: AuthzAuditEvent) -> None:
        logger.info(
            "authz.decision",
            extra={
                "extra_fields": {
                    "event_type": "authz.decision",
                    "org_id": event.org_id,
                    "actor_id": event.actor_id,
                    "permission_key": event.permission_key,
                    "decision": event.decision,
                    "reason": event.reason,
                    "scope_ref": event.scope_ref,
                    "source": event.source,
                }
            },
        )


class CapturingAuthzAuditSink:
    """Test sink — records decisions in memory so a test can assert allow + deny emit."""

    def __init__(self) -> None:
        self.events: list[AuthzAuditEvent] = []

    async def record(self, event: AuthzAuditEvent) -> None:
        self.events.append(event)
