"""Signature-event seam (S5 hook) — the Part-11-shaped record of an approval/release/obsolete.

S4 establishes the seam but **does not emit** signature events: the FSM records the transition + a
vault audit event only. S5 (Approval + SoD) wires the real emission and creates the
``signature_event`` table; it swaps the default logging sink for an append-only DB writer the same
way S6 will for audit. The shape mirrors doc 14 §4.2 / the canonical ``meaning`` enum (doc 04 §4.2,
register R2): ``review, approval, release, obsolete, verify, disposition, import_baseline,
review_confirmed`` (``authored`` / ``responsibility`` reserved for the Part-11 phase).
"""

from __future__ import annotations

import dataclasses
import datetime
import logging
from typing import Protocol

logger = logging.getLogger("easysynq.vault")


@dataclasses.dataclass(frozen=True, slots=True)
class SignatureEvent:
    """An approval/release/obsolete decision — the shape S5 will persist to ``signature_event``."""

    occurred_at: datetime.datetime
    document_version_id: str
    org_id: str | None
    signer_user_id: str
    meaning: str  # approval | release | obsolete | … (canonical enum, doc 04 §4.2 / R2)
    intent: str | None = None
    method: str = "app_click"  # v1 fixed; S5+ adds password_reauth / mfa_totp / mfa_webauthn
    request_id: str | None = None


class SignatureEventSink(Protocol):
    def record(self, event: SignatureEvent) -> None: ...


class LoggingSignatureEventSink:
    """Default sink — structured JSON. S5 swaps it for the append-only DB writer."""

    def record(self, event: SignatureEvent) -> None:
        logger.info(
            "vault.signature_event",
            extra={
                "extra_fields": {
                    "document_version_id": event.document_version_id,
                    "signer_user_id": event.signer_user_id,
                    "meaning": event.meaning,
                    "method": event.method,
                    "org_id": event.org_id,
                }
            },
        )


class CapturingSignatureEventSink:
    def __init__(self) -> None:
        self.events: list[SignatureEvent] = []

    def record(self, event: SignatureEvent) -> None:
        self.events.append(event)


_default_sink: SignatureEventSink = LoggingSignatureEventSink()


def get_vault_signature_sink() -> SignatureEventSink:
    """FastAPI dependency for the signature-event sink — overridden in tests/S5."""
    return _default_sink
