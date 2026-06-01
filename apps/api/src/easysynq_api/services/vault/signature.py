"""Signature-event emission (slice S5) — the Part-11-shaped record of an approval/release/obsolete.

S4 wired the seam; S5 makes it real. The default sink is now :class:`DbSignatureEventSink`, which
inserts an append-only ``signature_event`` row **into the caller's session without committing** — so
the signature commits atomically with the FSM mutation + ``task_outcome`` in one transaction (and
rolls back with a race loser). The shape mirrors doc 14 §8 / the canonical ``meaning`` enum (R2):
``review, approval, release, obsolete, verify, disposition, import_baseline, review_confirmed``
(``authored`` / ``responsibility`` reserved). v1 emits ``method='SESSION'`` (the value the doc 15
§8.8 decision response returns); the Part-11 columns stay NULL.
"""

from __future__ import annotations

import dataclasses
import datetime
import logging
import uuid
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._signature_enums import SignatureMeaning, SignatureMethod, SignedObjectType
from ...db.models.signature_event import SignatureEvent as SignatureEventRow

logger = logging.getLogger("easysynq.vault")


@dataclasses.dataclass(frozen=True, slots=True)
class SignatureEvent:
    """An approval/release/obsolete decision the sink persists to ``signature_event``."""

    org_id: uuid.UUID
    signed_object_id: uuid.UUID
    meaning: str  # approval | release | obsolete | … (canonical enum, doc 04 §4.2 / R2)
    signer_user_id: uuid.UUID | None = None  # null for a system/Beat release (no human signer)
    signed_object_type: str = "document_version"
    intent: str | None = None
    method: str = "SESSION"  # v1 session-assurance; S5+ adds password_reauth / mfa_* (Part-11)
    content_digest: str | None = None  # binds the signature to the signed bytes
    auth_context: dict[str, Any] | None = None  # OIDC acr/amr
    occurred_at: datetime.datetime | None = None  # None → DB server_default now()
    request_id: str | None = None  # logging only (signature_event has no request_id column)


class SignatureEventSink(Protocol):
    def record(self, session: AsyncSession, event: SignatureEvent) -> SignatureEventRow | None: ...


class DbSignatureEventSink:
    """Default sink — appends a ``signature_event`` row to the session (no commit; the caller's
    transaction flushes it atomically with the rest of the decision/cutover)."""

    def record(self, session: AsyncSession, event: SignatureEvent) -> SignatureEventRow:
        row = SignatureEventRow(
            org_id=event.org_id,
            signer_user_id=event.signer_user_id,
            signed_object_type=SignedObjectType(event.signed_object_type),
            signed_object_id=event.signed_object_id,
            meaning=SignatureMeaning(event.meaning),
            method=SignatureMethod(event.method),
            intent=event.intent,
            content_digest=event.content_digest,
            auth_context=event.auth_context,
        )
        if event.occurred_at is not None:
            row.created_at = event.occurred_at
        session.add(row)
        return row


class LoggingSignatureEventSink:
    """Structured-JSON sink (no persistence) — used where a DB write is not wanted."""

    def record(self, session: AsyncSession, event: SignatureEvent) -> None:
        logger.info(
            "vault.signature_event",
            extra={
                "extra_fields": {
                    "signed_object_id": str(event.signed_object_id),
                    "signer_user_id": str(event.signer_user_id) if event.signer_user_id else None,
                    "meaning": event.meaning,
                    "method": event.method,
                    "org_id": str(event.org_id),
                }
            },
        )


class CapturingSignatureEventSink:
    def __init__(self) -> None:
        self.events: list[SignatureEvent] = []

    def record(self, session: AsyncSession, event: SignatureEvent) -> None:
        self.events.append(event)


_default_sink: SignatureEventSink = DbSignatureEventSink()


def get_vault_signature_sink() -> SignatureEventSink:
    """FastAPI dependency for the signature-event sink — overridden in tests."""
    return _default_sink
