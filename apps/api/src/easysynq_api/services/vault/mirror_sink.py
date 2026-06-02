"""The mirror-sync enqueue seam (slice S7) — the post-commit hook that asks the worker to
regenerate the read-only filesystem mirror after a release/supersession/obsolescence.

doc 15 §8.5: ``POST /documents/{id}/release`` "enqueues the read-only FS mirror rewrite". The
lifecycle FSM owns its transaction; this sink is invoked **after** that transaction commits (never
inside the SERIALIZABLE cutover — a concurrent-release loser rolls back and must NOT enqueue), so
the mirror only ever reflects committed Effective state.

The enqueue is **best-effort**: the mirror is fully regenerable and the nightly Beat reconcile
(doc 04 §10.4) re-converges regardless, so a transient broker hiccup must never fail a release. The
default sink therefore swallows + logs broker errors. The Celery task is a full, idempotent rebuild,
so a duplicate enqueue is harmless. The shape mirrors ``get_vault_signature_sink`` (S5)."""

from __future__ import annotations

import logging
from typing import Protocol

logger = logging.getLogger("easysynq.vault")


class MirrorEnqueueSink(Protocol):
    def enqueue(self, reason: str | None = None) -> None: ...


class CeleryMirrorEnqueueSink:
    """Default sink — dispatches the ``easysynq.mirror.sync`` Celery task (a full, idempotent
    rebuild + atomic swap). Broker errors are logged and swallowed (the nightly reconcile recovers).
    The task import is lazy to avoid an import cycle (tasks → services.vault → mirror_sink)."""

    def enqueue(self, reason: str | None = None) -> None:
        try:
            from ...tasks.mirror import mirror_sync

            mirror_sync.delay()
        except Exception:  # noqa: BLE001 — best-effort; the nightly Beat reconcile is the backstop
            logger.warning(
                "vault.mirror.enqueue_failed", extra={"extra_fields": {"reason": reason}}
            )


class LoggingMirrorEnqueueSink:
    """No-op sink (logs only) — used where dispatching a real Celery task is not wanted."""

    def enqueue(self, reason: str | None = None) -> None:
        logger.info("vault.mirror.enqueue", extra={"extra_fields": {"reason": reason}})


class CapturingMirrorEnqueueSink:
    """Test double — records each enqueue call so a test can assert it fired exactly once,
    post-commit."""

    def __init__(self) -> None:
        self.reasons: list[str | None] = []

    def enqueue(self, reason: str | None = None) -> None:
        self.reasons.append(reason)


_default_sink: MirrorEnqueueSink = CeleryMirrorEnqueueSink()


def get_mirror_enqueue_sink() -> MirrorEnqueueSink:
    """The active mirror-enqueue sink — overridden in tests via :func:`set_mirror_enqueue_sink`."""
    return _default_sink


def set_mirror_enqueue_sink(sink: MirrorEnqueueSink) -> MirrorEnqueueSink:
    """Swap the process-wide sink (tests inject a :class:`CapturingMirrorEnqueueSink`). Returns the
    previous sink so the caller can restore it."""
    global _default_sink
    previous = _default_sink
    _default_sink = sink
    return previous
