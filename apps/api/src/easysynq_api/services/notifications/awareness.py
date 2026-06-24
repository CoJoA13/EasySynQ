"""Awareness-event emit (slice S-notify-5a, doc 10 §9.2).

Writes ONE awareness_event outbox row inside the caller's release txn, best-effort via a SAVEPOINT —
so a non-serialization failure rolls back only the awareness row and the release still commits (R53:
awareness must never block a transition). CRITICAL: the caller (_cutover) runs SERIALIZABLE, NOT
Read-Committed. A 40001/40P01/23505 raised by the INSERT poisons the whole txn and must NOT be
swallowed — it is re-raised so _cutover's race-loss path produces the clean 409 (spec §6). The
expensive fan-out is off the hot path (services/notifications/fanout.py).
"""

from __future__ import annotations

import datetime
import logging
import uuid

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models.awareness_event import AwarenessEvent

logger = logging.getLogger("easysynq.notifications.awareness")

# SQLSTATEs that poison a SERIALIZABLE txn (serialization_failure / deadlock_detected /
# unique_violation) — re-raise so the cutover's race-loss handler adjudicates a clean 409.
_SERIALIZATION_SQLSTATES = {"40001", "40P01", "23505"}


def _is_serialization_error(exc: DBAPIError) -> bool:
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return sqlstate in _SERIALIZATION_SQLSTATES


async def record_awareness_event(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    event_key: str,
    subject_type: str,
    subject_id: uuid.UUID,
    subject_version_id: uuid.UUID | None,
    actor_user_id: uuid.UUID | None,
    occurred_at: datetime.datetime,
    context: dict[str, object],
) -> None:
    """Best-effort, SERIALIZABLE-aware single-row emit. Never raises except on a serialization error
    (which must propagate to the caller's race-loss path)."""
    try:
        async with session.begin_nested():
            session.add(
                AwarenessEvent(
                    org_id=org_id,
                    event_key=event_key,
                    subject_type=subject_type,
                    subject_id=subject_id,
                    subject_version_id=subject_version_id,
                    actor_user_id=actor_user_id,
                    context=context,
                    occurred_at=occurred_at,
                )
            )
    except DBAPIError as exc:
        if _is_serialization_error(exc):
            raise  # SERIALIZABLE conflict — let _cutover's _is_race_loss produce the clean 409
        logger.warning("awareness.record_failed", exc_info=True, extra={"event_key": event_key})
    except Exception:  # noqa: BLE001 — best-effort: awareness must never block a release
        logger.warning("awareness.record_failed", exc_info=True, extra={"event_key": event_key})
