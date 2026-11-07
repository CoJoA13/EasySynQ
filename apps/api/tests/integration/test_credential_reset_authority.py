"""R64 self-reset remains an administrative operation with truthful denial and audit.

Reuse the established provisioning transport at its external boundary; this file does not alter
the shared helper semantics owned by the credential-error workstream.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
from sqlalchemy import select

from easysynq_api.db.models._audit_enums import EventType
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.session import get_sessionmaker

from .test_users_provision import _grant_user_create_only, _install_kc, _sub
from .test_vault import _auth

pytestmark = pytest.mark.integration


async def test_user_create_only_caller_cannot_reset_own_credential(
    app_client: httpx.AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_kc(monkeypatch)
    subject = _sub("self-reset-authority")
    caller_id = await _grant_user_create_only(subject)

    response = await app_client.post(
        f"/api/v1/users/{caller_id}/temporary-password", headers=_auth(token_factory, subject)
    )

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "two_tier_violation"
    assert calls["password"] == []
    async with get_sessionmaker()() as session:
        denial = await session.scalar(
            select(AuditEvent).where(
                AuditEvent.event_type == EventType.TWO_TIER_VIOLATION,
                AuditEvent.actor_id == caller_id,
                AuditEvent.scope_ref == f"user:{caller_id}",
            )
        )
        assert denial is not None
        assert denial.after["permission_key"] == "user.reset_credential"
    assert response.json()["detail"] == (
        "credential reset always requires a system-tier caller, "
        "including for the caller's own account"
    )
