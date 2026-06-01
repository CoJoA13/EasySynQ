"""The authorization audit hook (S2 seam for the S6 audit trail).

Every PDP decision — **allow and deny** — is emitted to an ``AuthzAuditSink``. In S2 the
production sink writes a structured JSON log line; S6 replaces it with the real
append-only, hash-chained ``audit_event`` writer (doc 18 §10 ledger). Tests inject a
``CapturingAuthzAuditSink`` to prove the "every allow+deny emits a hook" invariant.
"""

from __future__ import annotations

import dataclasses
import datetime
import logging
from typing import Protocol

logger = logging.getLogger("easysynq.authz")


@dataclasses.dataclass(frozen=True, slots=True)
class AuthzAuditEvent:
    """A single authorization decision, the shape S6 will persist to ``audit_event``."""

    occurred_at: datetime.datetime
    actor_id: str | None
    permission_key: str
    decision: str  # "allow" | "deny"
    reason: str
    org_id: str | None = None
    scope_ref: str | None = None
    source: str | None = None
    request_id: str | None = None


class AuthzAuditSink(Protocol):
    def record(self, event: AuthzAuditEvent) -> None: ...


class LoggingAuthzAuditSink:
    """Production sink for S2 — a structured log line per decision. Swapped for the
    ``audit_event`` writer in S6 (the call site does not change)."""

    def record(self, event: AuthzAuditEvent) -> None:
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

    def record(self, event: AuthzAuditEvent) -> None:
        self.events.append(event)
