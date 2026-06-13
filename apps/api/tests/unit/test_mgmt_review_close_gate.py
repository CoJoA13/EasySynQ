"""Unit tests for ``output_blocks_close`` (S-mr-1, clause 9.3 §s5 — the close gate predicate).

Mirrors ``finding_blocks_close`` semantics: type-gate FIRST (a DECISION/IMPROVEMENT never blocks),
then the fail-closed ``is not DONE`` tail (a None / unspawned task-state BLOCKS — never silently
passes)."""

from easysynq_api.db.models._mgmt_review_enums import ReviewOutputType
from easysynq_api.db.models._workflow_enums import TaskState
from easysynq_api.domain.mgmt_review.close_gate import output_blocks_close


def test_decision_never_blocks() -> None:
    # A non-ACTION output is recorded, not tracked — it never blocks, even with no task.
    assert output_blocks_close(ReviewOutputType.DECISION, None) is False


def test_improvement_never_blocks() -> None:
    assert output_blocks_close(ReviewOutputType.IMPROVEMENT, None) is False


def test_action_with_no_task_blocks_fail_closed() -> None:
    # The load-bearing fail-closed leg: an ACTION with NO spawned/linked task (None) BLOCKS.
    assert output_blocks_close(ReviewOutputType.ACTION, None) is True


def test_action_pending_blocks() -> None:
    assert output_blocks_close(ReviewOutputType.ACTION, TaskState.PENDING) is True


def test_action_claimed_blocks() -> None:
    # Any non-DONE task-state blocks (only DONE clears the gate).
    assert output_blocks_close(ReviewOutputType.ACTION, TaskState.CLAIMED) is True


def test_action_done_does_not_block() -> None:
    assert output_blocks_close(ReviewOutputType.ACTION, TaskState.DONE) is False
