from __future__ import annotations

import uuid
import zoneinfo

from easysynq_api.api.auth import _represent
from easysynq_api.db.models.app_user import AppUser, UserStatus
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
