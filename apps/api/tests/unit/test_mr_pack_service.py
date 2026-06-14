from __future__ import annotations

import pytest

from easysynq_api.problems import ProblemException
from easysynq_api.services.mgmt_review.pack import minutes_from_snapshot


def test_minutes_from_snapshot_returns_dict():
    snap = {"mgmt_review_minutes": {"period_label": "FY2026", "inputs": [], "outputs": []}}
    assert minutes_from_snapshot(snap)["period_label"] == "FY2026"


def test_minutes_from_snapshot_409_when_key_absent():
    with pytest.raises(ProblemException) as ei:
        minutes_from_snapshot({"distribution": {}})
    assert ei.value.status == 409
    assert ei.value.code == "pack_unavailable"


def test_minutes_from_snapshot_409_when_not_a_dict():
    with pytest.raises(ProblemException) as ei:
        minutes_from_snapshot({"mgmt_review_minutes": "oops"})
    assert ei.value.status == 409
