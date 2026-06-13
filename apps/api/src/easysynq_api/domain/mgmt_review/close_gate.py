"""output_blocks_close — pure close-gate predicate for a Management Review (S-mr-1, clause 9.3 §s5;
the ``domain.audits.finding_blocks_close`` shape).

The Management Review close gate (ActionsTracked → Closed) is **block-until-done**: a review cannot
close while any ``ACTION`` output's spawned ``MR_ACTION`` task is absent or not yet ``DONE``. A
``DECISION``/``IMPROVEMENT`` output is recorded, not tracked — it never blocks (the OBSERVATION/OFI
parallel). The tail is ``task_state is not TaskState.DONE`` so a ``None`` task-state (an ACTION whose
task was never spawned/linked) BLOCKS **fail-closed** rather than silently passing — mirroring
``finding_blocks_close``'s ``auto_capa_close_state is not CapaCloseState.Closed`` (NOT ``is X``,
which would fail OPEN on a missing task).

The service-layer gate loads ``(output_type, task_state)`` per output (LEFT JOIN on
``spawned_task_id`` so an unlinked ACTION yields ``None``) and filters with this single predicate, so
the rule has one source of truth (unit-tested alone)."""

from __future__ import annotations

from ...db.models._mgmt_review_enums import ReviewOutputType
from ...db.models._workflow_enums import TaskState


def output_blocks_close(output_type: ReviewOutputType, task_state: TaskState | None) -> bool:
    """True iff this output blocks the review close: an ``ACTION`` whose spawned ``MR_ACTION`` task
    is absent (``None``) or not yet ``DONE``. ``DECISION``/``IMPROVEMENT`` outputs never block."""
    if output_type is not ReviewOutputType.ACTION:
        return False
    return task_state is not TaskState.DONE
