import datetime
import uuid

import rfc8785

from easysynq_api.domain.mgmt_review.minutes import build_minutes


def test_build_minutes_is_json_safe_and_rfc8785_serializable() -> None:
    m = build_minutes(
        period_label="2026 Annual",
        review_date=datetime.date(2026, 6, 12),
        attendees=[{"name": "Mara", "role": "Quality Manager"}],
        inputs=[{"input_type": "AUDIT_RESULTS", "available": True, "summary": {"open": 2}}],
        outputs=[
            {
                "output_type": "ACTION",
                "description": "Tighten X",
                "owner_user_id": str(uuid.uuid4()),
            }
        ],
        compiled_at=datetime.datetime(2026, 6, 12, 9, 0, tzinfo=datetime.UTC),
    )
    assert m["review_date"] == "2026-06-12"
    assert m["compiled_at"].startswith("2026-06-12T09:00")
    # rfc8785 raises on non-JSON-safe leaves; this proves every leaf is a primitive:
    assert isinstance(rfc8785.dumps(m), bytes)
