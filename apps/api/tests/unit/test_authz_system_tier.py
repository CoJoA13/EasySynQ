"""Characterize the tier rule reused by reset enforcement and the self affordance probe.

Only grant gathering and the clock are synthetic; scope/predicate/deny resolution uses the real PDP.
The same public tier helper serves the enforcement guards and the self affordance response.
"""

from __future__ import annotations

import datetime
import uuid
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from easysynq_api.db.models.app_user import AppUser
from easysynq_api.domain.authz.types import Effect, ResolvedGrant, ScopeLevel
from easysynq_api.services.authz import pep

NOW = datetime.datetime(2026, 10, 10, 12, tzinfo=datetime.UTC)
SYSTEM_ALLOW = ResolvedGrant(Effect.ALLOW, ScopeLevel.SYSTEM, {}, {}, "user_override")
CONTENT_ALLOW = ResolvedGrant(
    Effect.ALLOW, ScopeLevel.SYSTEM, {}, {"content_only": True}, "role:QMS Owner"
)
SYSTEM_DENY = ResolvedGrant(Effect.DENY, ScopeLevel.SYSTEM, {}, {}, "user_override")


@pytest.mark.parametrize(
    ("grants", "expected"),
    [
        pytest.param([], False, id="no-grants"),
        pytest.param([CONTENT_ALLOW], False, id="content-only"),
        pytest.param([SYSTEM_ALLOW], True, id="system-allow"),
        pytest.param([CONTENT_ALLOW, SYSTEM_ALLOW], True, id="content-then-system"),
        pytest.param([SYSTEM_ALLOW, CONTENT_ALLOW], True, id="system-then-content"),
        pytest.param([SYSTEM_ALLOW, SYSTEM_DENY], False, id="applicable-deny-wins"),
        pytest.param(
            [
                SYSTEM_ALLOW,
                ResolvedGrant(
                    Effect.DENY, ScopeLevel.SYSTEM, {}, {"content_only": True}, "user_override"
                ),
            ],
            True,
            id="content-only-deny-remains-filtered",
        ),
        pytest.param(
            [
                ResolvedGrant(
                    Effect.ALLOW,
                    ScopeLevel.SYSTEM,
                    {},
                    {"valid_until": NOW - datetime.timedelta(days=1)},
                    "user_override",
                )
            ],
            False,
            id="expired",
        ),
        pytest.param(
            [
                ResolvedGrant(
                    Effect.ALLOW,
                    ScopeLevel.SYSTEM,
                    {},
                    {"valid_from": NOW + datetime.timedelta(days=1)},
                    "user_override",
                )
            ],
            False,
            id="not-yet-valid",
        ),
        pytest.param(
            [ResolvedGrant(Effect.ALLOW, ScopeLevel.FOLDER, {"folder_path": "sample"}, {}, "role")],
            False,
            id="folder-scope",
        ),
        pytest.param(
            [
                ResolvedGrant(
                    Effect.ALLOW,
                    ScopeLevel.SYSTEM,
                    {},
                    {"ip_allow": ["192.0.2.1"]},
                    "user_override",
                )
            ],
            False,
            id="ip-restricted-without-request-ip",
        ),
    ],
)
async def test_system_tier_uses_effective_non_content_only_grants(
    grants: list[ResolvedGrant], expected: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    caller = AppUser(id=uuid.uuid4(), org_id=uuid.uuid4(), keycloak_subject="synthetic-tier")
    session = cast(AsyncSession, object())
    gathered: list[tuple[AsyncSession, uuid.UUID, uuid.UUID, str]] = []

    async def gather(
        actual_session: AsyncSession, user_id: uuid.UUID, org_id: uuid.UUID, key: str
    ) -> list[ResolvedGrant]:
        gathered.append((actual_session, user_id, org_id, key))
        return grants

    monkeypatch.setattr(pep, "gather_grants", gather)
    monkeypatch.setattr(pep, "_now", lambda: NOW)

    assert await pep.is_system_tier(session, caller) is expected
    assert gathered == [(session, caller.id, caller.org_id, "permission.grant")]
