from __future__ import annotations

import uuid
import zoneinfo

from easysynq_api.api.auth import _represent
from easysynq_api.db.models.app_user import AppUser, ColorScheme, UserStatus
from easysynq_api.services.common.org_clock import using_org_tz


def test_me_representation_exposes_the_canonical_request_timezone() -> None:
    user = AppUser(
        id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        keycloak_subject="me-timezone",
        display_name="Mara",
        email="mara@example.test",
        status=UserStatus.ACTIVE,
        is_guest=False,
    )

    with using_org_tz(zoneinfo.ZoneInfo("Asia/Tokyo")):
        assert _represent(user)["org_timezone"] == "Asia/Tokyo"


def _user(**over: object) -> AppUser:
    base: dict[str, object] = {
        "id": uuid.uuid4(),
        "org_id": uuid.uuid4(),
        "keycloak_subject": "me-color-scheme",
        "display_name": "Mara",
        "email": "mara@example.test",
        "status": UserStatus.ACTIVE,
        "is_guest": False,
    }
    base.update(over)
    return AppUser(**base)  # type: ignore[arg-type]


def test_me_representation_exposes_the_stored_color_scheme() -> None:
    """R69: the account is the authority for the colour scheme, so it rides `/me`.

    Pinned per value rather than as a round trip, because the wire form is a plain string while the
    column is a Python enum — asserting `== user.color_scheme` would pass against a representation
    that leaked the enum object and broke every JSON consumer.
    """
    for scheme in ColorScheme:
        assert _represent(_user(color_scheme=scheme))["color_scheme"] == scheme.value


def test_me_representation_of_an_unflushed_user_is_auto_not_a_crash() -> None:
    """A SQLAlchemy column default lands at FLUSH, so a transient AppUser reads None here.

    This is not hypothetical — adding the column broke the timezone test above until `_represent`
    handled it. The column is NOT NULL, so a persisted row can never reach this branch; AUTO is both
    the column default and the pre-R69 behaviour, which is why the fallback is the correct value and
    not a mask. Asserting AUTO rather than merely "does not raise" is what pins that reasoning: a
    fallback to LIGHT or DARK would also not raise, and would silently override the OS.
    """
    user = _user()
    assert user.color_scheme is None  # the precondition — otherwise this test proves nothing
    assert _represent(user)["color_scheme"] == ColorScheme.AUTO.value
