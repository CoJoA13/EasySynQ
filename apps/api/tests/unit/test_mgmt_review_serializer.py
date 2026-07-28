"""The Management Review serializer adds capabilities only when supplied (detail-only)."""

import datetime
import uuid

from easysynq_api.api.management_review import _management_review
from easysynq_api.db.models._mgmt_review_enums import ManagementReviewCloseState
from easysynq_api.db.models.management_review import ManagementReview


def _mr() -> ManagementReview:
    mr = ManagementReview(
        id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        period_label="2026 Annual",
        review_date=None,
        attendees=None,
        close_state=ManagementReviewCloseState.ActionsTracked,
        closed_at=None,
    )
    mr.created_at = datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC)
    return mr


def test_capabilities_absent_when_none() -> None:
    out = _management_review(_mr(), identifier="MR-001", title="x", current_state="Effective")
    assert "capabilities" not in out


def test_capabilities_present_when_passed() -> None:
    out = _management_review(
        _mr(),
        identifier="MR-001",
        title="x",
        current_state="Approved",
        capabilities={"release": False},
    )
    assert out["capabilities"] == {"release": False}
