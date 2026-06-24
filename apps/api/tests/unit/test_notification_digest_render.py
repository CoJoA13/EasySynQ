"""Pure unit tests for render_digest_items (no DB required)."""

from __future__ import annotations

import uuid

import pytest

from easysynq_api.db.models.notification import Notification
from easysynq_api.services.notifications.digest import render_digest_items

pytestmark = pytest.mark.unit


def _note(
    title: str,
    link: str,
    event_key: str = "task.assigned",
    subject_id: uuid.UUID | None = None,
    subject_version_id: uuid.UUID | None = None,
) -> Notification:
    return Notification(
        id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        recipient_user_id=uuid.uuid4(),
        event_key=event_key,
        subject_type="DOCUMENT",
        subject_id=subject_id or uuid.uuid4(),
        subject_version_id=subject_version_id,
        title=title,
        body="",
        deep_link=link,
    )


def test_lists_each_item_with_title_and_link() -> None:
    block, count = render_digest_items(
        [
            _note("Review SOP-1", "https://x/documents/1"),
            _note("Ack POL-2", "https://x/documents/2"),
        ]
    )
    assert count == 2
    assert "Review SOP-1" in block and "https://x/documents/1" in block
    assert "Ack POL-2" in block


def test_collapses_identical_events() -> None:
    """Task rows (subject_version_id=NULL) with the same (event_key, subject_id) collapse."""
    sid = uuid.uuid4()
    block, count = render_digest_items(
        [
            _note("Released DOC-9", "https://x/documents/9", "doc.released", sid),
            _note("Released DOC-9", "https://x/documents/9", "doc.released", sid),
        ]
    )
    assert count == 2  # item_count is the raw row count
    assert "x2" in block  # collapsed to one line with a count


def test_awareness_distinct_versions_render_as_separate_lines() -> None:
    """Two awareness rows for the SAME doc but DIFFERENT subject_version_id: two distinct lines.

    Codex P2: without the version discriminator, both rows would collapse into one 'x2' line,
    silently omitting the second version's title from the digest.
    """
    sid = uuid.uuid4()
    vid1 = uuid.uuid4()
    vid2 = uuid.uuid4()
    block, count = render_digest_items(
        [
            _note("Rev A is now Effective", "/documents/d?v=1", "doc.released", sid, vid1),
            _note("Rev B is now Effective", "/documents/d?v=2", "doc.released", sid, vid2),
        ]
    )
    assert count == 2
    # Must appear as two distinct lines, not one "x2" line
    assert "x2" not in block
    assert "Rev A is now Effective" in block
    assert "Rev B is now Effective" in block


def test_task_rows_null_version_still_collapse() -> None:
    """Task rows with subject_version_id=NULL and same (event_key, subject_id) still group."""
    sid = uuid.uuid4()
    block, count = render_digest_items(
        [
            _note("Task: Review SOP-1", "https://x/tasks/1", "task.assigned", sid, None),
            _note("Task: Review SOP-1", "https://x/tasks/1", "task.assigned", sid, None),
        ]
    )
    assert count == 2
    assert "x2" in block  # two NULL-version task rows still collapse
