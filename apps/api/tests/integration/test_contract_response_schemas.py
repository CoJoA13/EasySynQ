"""Advisory response-schema validation over the published OpenAPI surface.

The sweep is deliberately stateless and response-only: Schemathesis generates one
deterministic positive request per operation, the existing integration fixtures send
it to a disposable testcontainers-backed app, and the published response contract is
checked. Stateful fuzzing belongs in a later ratchet.
"""

from __future__ import annotations

import asyncio
import datetime
import time
import uuid
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import schemathesis
from httpx import AsyncBaseTransport, AsyncClient, Request, Response
from hypothesis import HealthCheck, settings
from schemathesis.specs.openapi.checks import (
    content_type_conformance,
    response_headers_conformance,
    response_schema_conformance,
    status_code_conformance,
)
from sqlalchemy import select

from easysynq_api.db.models.app_user import AppUser, UserStatus
from easysynq_api.db.models.authz_grant import PermissionOverride
from easysynq_api.db.models.organization import Organization
from easysynq_api.db.models.permission import Permission
from easysynq_api.db.models.role import Role, RoleAssignment
from easysynq_api.db.models.scope import Scope
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.domain.authz.types import Effect, ScopeLevel

pytestmark = pytest.mark.contract

_CONTRACT_PATH = Path(__file__).resolve().parents[4] / "packages" / "contracts" / "openapi.yaml"
_BASE_URL = "http://test/api/v1"
_UNVERSIONED_BASE_URL = "http://test"
_UNVERSIONED_PATHS = frozenset({"/healthz", "/readyz"})
_STREAMING_PATHS = frozenset({"/notifications/stream"})
_SUBJECT = "kc-contract-response-validation"
_LOCKED_STATUSES = frozenset({401, 423})

_CONFIG = schemathesis.Config.from_str(
    """
[generation]
mode = "positive"
max-examples = 1
deterministic = true

[phases.examples]
enabled = false

[phases.coverage]
enabled = false
"""
)
_SCHEMA = schemathesis.openapi.from_path(_CONTRACT_PATH, config=_CONFIG)


class _FirstChunkASGITransport(AsyncBaseTransport):
    """Exercise a long-lived ASGI response, then disconnect after its first body frame."""

    def __init__(self, app: Any) -> None:
        self._app = app

    async def handle_async_request(self, request: Request) -> Response:
        started_at = time.perf_counter()
        request_body = await request.aread()
        request_sent = False
        first_chunk_received = asyncio.Event()
        status_code: int | None = None
        response_headers: list[tuple[bytes, bytes]] | None = None
        body_parts: list[bytes] = []

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": request.method,
            "headers": [(key.lower(), value) for key, value in request.headers.raw],
            "scheme": request.url.scheme,
            "path": request.url.path,
            "raw_path": request.url.raw_path.split(b"?")[0],
            "query_string": request.url.query,
            "server": (request.url.host, request.url.port),
            "client": ("127.0.0.1", 123),
            "root_path": "",
        }

        async def receive() -> dict[str, Any]:
            nonlocal request_sent
            if not request_sent:
                request_sent = True
                return {"type": "http.request", "body": request_body, "more_body": False}
            await first_chunk_received.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            nonlocal status_code, response_headers
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_headers = message.get("headers", [])
            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                if body and request.method != "HEAD":
                    body_parts.append(body)
                    first_chunk_received.set()
                elif not message.get("more_body", False):
                    first_chunk_received.set()

        await self._app(scope, receive, send)

        assert status_code is not None
        assert response_headers is not None
        response = Response(status_code, headers=response_headers, content=b"".join(body_parts))
        response.elapsed = datetime.timedelta(seconds=time.perf_counter() - started_at)
        return response


@dataclass
class _RunEvidence:
    statuses: Counter[int] = field(default_factory=Counter)
    operations: set[str] = field(default_factory=set)

    def record(self, operation: str, status_code: int) -> None:
        self.operations.add(operation)
        self.statuses[status_code] += 1

    @property
    def response_count(self) -> int:
        return self.statuses.total()


@pytest.fixture(scope="module")
def contract_run_evidence() -> Iterator[_RunEvidence]:
    evidence = _RunEvidence()
    yield evidence

    expected = _SCHEMA.statistic.operations.selected
    assert len(evidence.operations) == expected, (
        f"contract sweep exercised {len(evidence.operations)} of {expected} selected operations"
    )
    assert evidence.response_count >= expected

    locked = sum(evidence.statuses[status] for status in _LOCKED_STATUSES)
    assert locked * 5 <= evidence.response_count, (
        "contract sweep is validating an unauthenticated or setup-locked app: "
        f"{locked}/{evidence.response_count} responses were 401/423; "
        f"status counts={dict(sorted(evidence.statuses.items()))}"
    )


async def _seed_contract_principal(subject: str) -> uuid.UUID:
    """Give one disposable principal every permission and seeded role at SYSTEM scope."""
    async with get_sessionmaker()() as session:
        user = (
            await session.execute(select(AppUser).where(AppUser.keycloak_subject == subject))
        ).scalar_one_or_none()
        if user is None:
            org_id = (
                await session.execute(
                    select(Organization.id).order_by(Organization.created_at).limit(1)
                )
            ).scalar_one()
            user = AppUser(
                org_id=org_id,
                keycloak_subject=subject,
                display_name="Contract Response Validator",
                email="contract-validator@example.test",
                status=UserStatus.ACTIVE,
            )
            session.add(user)
            await session.flush()
        else:
            # A generated user-admin request must not poison later operations in this same sweep.
            user.status = UserStatus.ACTIVE

        system_scope = (
            await session.execute(
                select(Scope)
                .join(PermissionOverride, PermissionOverride.scope_id == Scope.id)
                .where(
                    PermissionOverride.user_id == user.id,
                    Scope.level == ScopeLevel.SYSTEM,
                    Scope.selector.is_(None),
                    Scope.predicates.is_(None),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if system_scope is None:
            system_scope = Scope(
                org_id=user.org_id,
                level=ScopeLevel.SYSTEM,
                selector=None,
                predicates=None,
            )
            session.add(system_scope)
            await session.flush()

        permissions = list((await session.execute(select(Permission))).scalars())
        existing_permission_ids = set(
            (
                await session.execute(
                    select(PermissionOverride.permission_id).where(
                        PermissionOverride.user_id == user.id,
                        PermissionOverride.scope_id == system_scope.id,
                        PermissionOverride.effect == Effect.ALLOW,
                    )
                )
            ).scalars()
        )
        session.add_all(
            PermissionOverride(
                org_id=user.org_id,
                user_id=user.id,
                permission_id=permission.id,
                effect=Effect.ALLOW,
                scope_id=system_scope.id,
                require_reason=False,
                reason="contract response validation fixture",
                created_by=user.id,
            )
            for permission in permissions
            if permission.id not in existing_permission_ids
        )

        roles = list(
            (
                await session.execute(
                    select(Role).where(Role.org_id == user.org_id).order_by(Role.name)
                )
            ).scalars()
        )
        existing_role_ids = set(
            (
                await session.execute(
                    select(RoleAssignment.role_id).where(RoleAssignment.user_id == user.id)
                )
            ).scalars()
        )
        session.add_all(
            RoleAssignment(
                org_id=user.org_id,
                user_id=user.id,
                role_id=role.id,
                bound_scope=None,
            )
            for role in roles
            if role.id not in existing_role_ids
        )
        await session.commit()
        return user.id


@pytest.fixture
async def contract_headers(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
) -> dict[str, str]:
    await _seed_contract_principal(_SUBJECT)
    headers = {"Authorization": f"Bearer {token_factory(_SUBJECT)}"}

    identity = await app_client.get("/api/v1/me", headers=headers)
    assert identity.status_code == 200, identity.text

    # /me is latch-exempt; this second probe proves the fixture is genuinely OPERATIONAL.
    protected = await app_client.get("/api/v1/me/permissions", headers=headers)
    assert protected.status_code == 200, protected.text
    return headers


@_SCHEMA.parametrize()
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
async def test_published_response_conforms(
    case: schemathesis.Case,
    app_under_test: Any,
    app_client: AsyncClient,
    contract_headers: dict[str, str],
    contract_run_evidence: _RunEvidence,
) -> None:
    base_url = _UNVERSIONED_BASE_URL if case.path in _UNVERSIONED_PATHS else _BASE_URL
    request_kwargs: dict[str, Any] = case.as_transport_kwargs(base_url=base_url)
    headers = dict(request_kwargs.get("headers") or {})
    headers.update(contract_headers)
    request_kwargs["headers"] = headers
    if not request_kwargs.get("cookies"):
        request_kwargs.pop("cookies", None)

    if case.path in _STREAMING_PATHS:
        async with AsyncClient(
            transport=_FirstChunkASGITransport(app_under_test),
            base_url=_UNVERSIONED_BASE_URL,
        ) as streaming_client:
            response = await streaming_client.request(**request_kwargs)
    else:
        response = await app_client.request(**request_kwargs)
    contract_run_evidence.record(case.operation.label, response.status_code)
    case.validate_response(
        response,
        checks=[
            status_code_conformance,
            content_type_conformance,
            response_headers_conformance,
            response_schema_conformance,
        ],
        headers=headers,
        transport_kwargs=request_kwargs,
    )
