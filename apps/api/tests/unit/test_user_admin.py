"""S8d unit proof — the new user-lifecycle event_type values (no DB).

The DB-bound admin flow (roster / invite / enable-disable / last-admin guard) is proven in
``tests/integration/test_users_admin.py``; here we pin the enum guard (a missing Python EventType
member is a runtime crash on insert, not a CI failure — see 0011-0016).
"""

from __future__ import annotations

from easysynq_api.db.models._audit_enums import EVENT_TYPE_VALUES, EventType


def test_new_user_event_types_present() -> None:
    for name in ("USER_CREATED", "USER_STATUS_CHANGED"):
        assert EventType(name).value == name
        assert name in EVENT_TYPE_VALUES
