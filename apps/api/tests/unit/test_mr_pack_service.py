from __future__ import annotations

import uuid

import pytest

from easysynq_api.problems import ProblemException
from easysynq_api.services.mgmt_review.pack import _signer_label, minutes_from_snapshot


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


def test_minutes_from_snapshot_409_when_none_snapshot():
    with pytest.raises(ProblemException) as ei:
        minutes_from_snapshot(None)  # type: ignore[arg-type]
    assert ei.value.status == 409


_UID = uuid.UUID("11111111-1111-1111-1111-111111111111")


def test_signer_label_prefers_display_name():
    assert _signer_label("Ken Approver", _UID) == "Ken Approver"


def test_signer_label_falls_back_to_id_for_a_human_without_a_name():
    # A human signer with no display_name → the (non-PII) id; never "system", never email.
    assert _signer_label(None, _UID) == str(_UID)


def test_signer_label_none_only_for_a_true_system_signature():
    # Null signer_user_id = a system/Beat release → None → the render shows "system".
    assert _signer_label(None, None) is None
    assert _signer_label("ignored", None) is None
