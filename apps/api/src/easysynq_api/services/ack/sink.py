"""The ack-sweep enqueue seam (slice S-ack-1) — the mirror_sink Protocol/Celery/Logging/Capturing
trio so tests assert fired-exactly-once-post-commit."""

from __future__ import annotations

import logging
from typing import Protocol

logger = logging.getLogger("easysynq.ack")


class AckEnqueueSink(Protocol):
    def enqueue(self, document_id: str | None = None, trigger: str | None = None) -> None: ...


class CeleryAckEnqueueSink:
    """Default sink — dispatches ``easysynq.ack.sweep`` (doc-scoped, trigger-stamped). Broker
    errors are logged and swallowed (the daily Beat sweep is the self-heal). Lazy task import
    (the tasks → services cycle)."""

    def enqueue(self, document_id: str | None = None, trigger: str | None = None) -> None:
        try:
            from ...tasks.ack import ack_sweep

            ack_sweep.delay(document_id, trigger)
        except Exception:  # noqa: BLE001 — best-effort; the daily Beat sweep is the backstop
            logger.warning(
                "ack.enqueue_failed",
                extra={"extra_fields": {"document_id": document_id, "trigger": trigger}},
            )


class LoggingAckEnqueueSink:
    """No-op sink (logs only) — used where dispatching a real Celery task is not wanted."""

    def enqueue(self, document_id: str | None = None, trigger: str | None = None) -> None:
        logger.info(
            "ack.enqueue",
            extra={"extra_fields": {"document_id": document_id, "trigger": trigger}},
        )


class CapturingAckEnqueueSink:
    """Test double — records each enqueue so a test asserts exactly-once, post-commit."""

    def __init__(self) -> None:
        self.calls: list[tuple[str | None, str | None]] = []

    def enqueue(self, document_id: str | None = None, trigger: str | None = None) -> None:
        self.calls.append((document_id, trigger))


_default_sink: AckEnqueueSink = CeleryAckEnqueueSink()


def get_ack_enqueue_sink() -> AckEnqueueSink:
    """The active ack-enqueue sink — overridden in tests via :func:`set_ack_enqueue_sink`."""
    return _default_sink


def set_ack_enqueue_sink(sink: AckEnqueueSink) -> AckEnqueueSink:
    """Swap the process-wide sink (tests inject a Capturing sink). Returns the previous sink."""
    global _default_sink
    previous = _default_sink
    _default_sink = sink
    return previous
