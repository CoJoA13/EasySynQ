"""Email-eligibility gate (spec §4): org-enabled AND user opt-in AND email present AND not DOC_ACK.

DOC_ACK email is deferred to slice 3 — in-app only in slice 1.
"""

from __future__ import annotations

import pytest

from easysynq_api.services.notifications.dispatch import _email_eligible

pytestmark = pytest.mark.unit


def test_email_eligible_happy() -> None:
    assert _email_eligible(
        org_enabled=True, email="a@x.test", user_opt_in=True, subject_type="DOCUMENT"
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "org_enabled": False,
            "email": "a@x.test",
            "user_opt_in": True,
            "subject_type": "DOCUMENT",
        },
        {"org_enabled": True, "email": None, "user_opt_in": True, "subject_type": "DOCUMENT"},
        {
            "org_enabled": True,
            "email": "a@x.test",
            "user_opt_in": False,
            "subject_type": "DOCUMENT",
        },
        {
            "org_enabled": True,
            "email": "a@x.test",
            "user_opt_in": True,
            "subject_type": "DOC_ACK",
        },
    ],
)
def test_email_suppressed(kwargs: dict) -> None:
    assert not _email_eligible(**kwargs)
