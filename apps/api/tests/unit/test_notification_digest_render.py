"""Pure unit tests for render_digest_items (no DB required)."""

from __future__ import annotations

import uuid

from easysynq_api.db.models.notification import Notification
from easysynq_api.services.notifications.digest import render_digest_items


def _note(
    title: str,
    link: str,
    event_key: str = "task.assigned",
    subject_id: uuid.UUID | None = None,
) -> Notification:
    return Notification(
        id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        recipient_user_id=uuid.uuid4(),
        event_key=event_key,
        subject_type="DOCUMENT",
        subject_id=subject_id or uuid.uuid4(),
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
    sid = uuid.uuid4()
    block, count = render_digest_items(
        [
            _note("Released DOC-9", "https://x/documents/9", "doc.released", sid),
            _note("Released DOC-9", "https://x/documents/9", "doc.released", sid),
        ]
    )
    assert count == 2  # item_count is the raw row count
    assert "x2" in block  # collapsed to one line with a count
