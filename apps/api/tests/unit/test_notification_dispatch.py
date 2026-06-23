"""Email-eligibility gate (spec §4): org-enabled AND user opt-in AND email present.

S-notify-3a: DOC_ACK email is now enabled (the slice-1 subject_type suppression is removed).
The gate is now purely org-flag x email-present x user-opt-in. Per-class digest mode
(immediate/daily/off) is a separate concern handled in dispatch._enqueue_one.
"""

from __future__ import annotations

import pytest

from easysynq_api.services.notifications.dispatch import _email_eligible

pytestmark = pytest.mark.unit


def test_email_eligible_happy() -> None:
    assert _email_eligible(org_enabled=True, email="a@x.test", user_opt_in=True)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"org_enabled": False, "email": "a@x.test", "user_opt_in": True},
        {"org_enabled": True, "email": None, "user_opt_in": True},
        {"org_enabled": True, "email": "a@x.test", "user_opt_in": False},
    ],
)
def test_email_suppressed(kwargs: dict) -> None:
    assert not _email_eligible(**kwargs)
