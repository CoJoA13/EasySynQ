"""Vault audit hook — document/check-out/check-in/lifecycle actions (S3/S4, wired to the DB in S6).

Every vault action emits a ``VaultAuditEvent`` to a ``VaultAuditSink``. In S6 the production sink is
the **in-transaction** ``DbVaultAuditSink``: it ``session.add``s a real ``audit_event`` row to the
caller's session WITHOUT committing, so the audit row commits (or rolls back) **atomically with the
state change it records** (doc 12 §4.4 / AC#6). This mirrors the ``DbSignatureEventSink`` pattern.
``prev_hash``/``row_hash``/``chained_at`` stay NULL at write — the decoupled chain-linker fills them
(R12). The ``record(session, event)`` signature is the only change from S3/S4; the capturing test
sink keeps recording the event (ignoring the session), so the S3-S5 "every action emits a hook"
proofs are unchanged.
"""

from __future__ import annotations

import dataclasses
import datetime
import logging
import uuid
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._audit_enums import ActorType, AuditObjectType, EventType
from ...db.models.audit_event import AuditEvent

logger = logging.getLogger("easysynq.vault")

# Vault object-type strings → audit_object_type (doc 12 §4.2: "version", not "document_version").
_OBJECT_TYPE: dict[str, AuditObjectType] = {
    "document": AuditObjectType.document,
    "document_version": AuditObjectType.version,
}


@dataclasses.dataclass(frozen=True, slots=True)
class VaultAuditEvent:
    occurred_at: datetime.datetime
    event_type: str  # DOCUMENT_CREATED | CHECKOUT | CHECKIN | NO_CHANGE | LOCK_BROKEN | ...
    actor_id: str | None  # uuid string, or the literal "system" for a Beat/system actor
    org_id: str | None
    object_type: str  # document | document_version
    object_id: str
    identifier: str | None = None
    reason: str | None = None
    request_id: str | None = None


def _maybe_uuid(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def to_audit_event(event: VaultAuditEvent) -> AuditEvent:
    """Project a ``VaultAuditEvent`` onto an ``audit_event`` ORM row (hashes NULL until linked)."""
    is_system = event.actor_id == "system"
    return AuditEvent(
        org_id=uuid.UUID(event.org_id) if event.org_id else None,
        occurred_at=event.occurred_at,
        actor_id=None if is_system else _maybe_uuid(event.actor_id),
        actor_type=ActorType.system if is_system else ActorType.user,
        event_type=EventType(event.event_type),
        object_type=_OBJECT_TYPE[event.object_type],
        object_id=_maybe_uuid(event.object_id),
        scope_ref=event.identifier,  # the controlled identifier is the row's scope context
        reason=event.reason,
        request_id=_maybe_uuid(event.request_id),
    )


class VaultAuditSink(Protocol):
    def record(self, session: AsyncSession, event: VaultAuditEvent) -> AuditEvent | None: ...


class DbVaultAuditSink:
    """Production sink (S6) — appends an ``audit_event`` row to the session; no commit. The caller's
    transaction flushes it atomically with the vault state change it records."""

    def record(self, session: AsyncSession, event: VaultAuditEvent) -> AuditEvent | None:
        row = to_audit_event(event)
        session.add(row)
        return row


class LoggingVaultAuditSink:
    """Structured-log sink (kept for non-DB contexts / debugging)."""

    def record(self, session: AsyncSession, event: VaultAuditEvent) -> AuditEvent | None:
        logger.info(
            "vault.event",
            extra={
                "extra_fields": {
                    "event_type": event.event_type,
                    "actor_id": event.actor_id,
                    "org_id": event.org_id,
                    "object_type": event.object_type,
                    "object_id": event.object_id,
                    "identifier": event.identifier,
                    "reason": event.reason,
                }
            },
        )
        return None


class CapturingVaultAuditSink:
    """Test sink — records events in memory (ignores the session)."""

    def __init__(self) -> None:
        self.events: list[VaultAuditEvent] = []

    def record(self, session: AsyncSession, event: VaultAuditEvent) -> AuditEvent | None:
        self.events.append(event)
        return None


_default_sink: VaultAuditSink = DbVaultAuditSink()


def get_vault_audit_sink() -> VaultAuditSink:
    """FastAPI dependency for the vault audit sink — overridden in tests with a capturing sink."""
    return _default_sink
