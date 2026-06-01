"""Vault audit hook (S6 seam) — document/check-out/check-in actions.

Mirrors the S2 authz sink: every vault action emits a ``VaultAuditEvent`` to an
``VaultAuditSink``. In S3 the production sink logs structured JSON; S6 swaps it for the real
append-only ``audit_event`` writer. The break-lock proof asserts a ``LOCK_BROKEN`` event via a
capturing sink.
"""

from __future__ import annotations

import dataclasses
import datetime
import logging
from typing import Protocol

logger = logging.getLogger("easysynq.vault")


@dataclasses.dataclass(frozen=True, slots=True)
class VaultAuditEvent:
    occurred_at: datetime.datetime
    event_type: str  # DOCUMENT_CREATED | CHECKOUT | CHECKIN | NO_CHANGE | LOCK_BROKEN
    actor_id: str | None
    org_id: str | None
    object_type: str  # document | document_version
    object_id: str
    identifier: str | None = None
    reason: str | None = None
    request_id: str | None = None


class VaultAuditSink(Protocol):
    def record(self, event: VaultAuditEvent) -> None: ...


class LoggingVaultAuditSink:
    def record(self, event: VaultAuditEvent) -> None:
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


class CapturingVaultAuditSink:
    def __init__(self) -> None:
        self.events: list[VaultAuditEvent] = []

    def record(self, event: VaultAuditEvent) -> None:
        self.events.append(event)


_default_sink: VaultAuditSink = LoggingVaultAuditSink()


def get_vault_audit_sink() -> VaultAuditSink:
    """FastAPI dependency for the vault audit sink — overridden in tests with a capturing sink."""
    return _default_sink
