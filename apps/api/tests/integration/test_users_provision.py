"""S-user-create integration proofs — in-app Keycloak provisioning and credential issuance.

No live Keycloak (D1): ``api.users._kc_client`` is monkeypatched to build the real
``KeycloakProvisioningClient`` over an ``httpx.MockTransport``, so the endpoint exercises its true
code path against a scripted identity service.

Shared-DB discipline: every assertion is scoped to rows this test created, or is a delta. The suite
shares one session database, so absolute counts are invalid.

Beyond the happy/failure paths, two properties get dedicated proofs because they are the ones a
static read cannot settle: (1) both handlers wrap their FINAL ``USER_CREDENTIAL_ISSUED`` audit
emit+commit in a non-fatal ``try/except`` (the credential is already live in Keycloak, so losing the
response would be strictly worse than losing that one audit row) — ``_break_credential_commit``
forces exactly that write to fail, keyed on the event TYPE rather than call order, so it can never
mistake the EARLIER commit (the durable app_user row + USER_CREATED + ROLE_ASSIGN) for the one under
test; and (2) a role grant during provisioning is fully audited, in an order an auditor can trust
(the user must not appear to be granted a role before they existed).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from easysynq_api.api import users as users_api
from easysynq_api.db.models._audit_enums import EventType
from easysynq_api.db.models.app_user import AppUser, UserStatus
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.authz_grant import PermissionOverride
from easysynq_api.db.models.permission import Permission
from easysynq_api.db.models.role import Role, RoleAssignment
from easysynq_api.db.models.scope import Scope
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.domain.authz.types import Effect, ScopeLevel
from easysynq_api.domain.identity.temp_password import MIN_LENGTH
from easysynq_api.services.keycloak_provisioning import KeycloakProvisioningClient

from . import s5_helpers as s5
from .test_vault import _auth, _ensure_user

pytestmark = pytest.mark.integration

_ADMIN = "System Administrator"

# CONFIRMED production bug, found by these two tests (not a test defect) — see the report for full
# detail. In BOTH handlers' `except Exception: await session.rollback(); logger.warning(...,
# extra={"extra_fields": {"user_id": str(user.id)}})` (api/users.py ~L407-411 in provision_user,
# ~L569-573 in issue_temporary_password), the `.id` touch happens AFTER rollback. SQLAlchemy's
# `Session.rollback()` unconditionally expires every loaded ORM instance's attributes regardless
# of `expire_on_commit=False` (that flag only suppresses expiry-on-COMMIT), so this synchronous
# access triggers an async lazy-load with no greenlet context -> `sqlalchemy.exc.MissingGreenlet`
# propagates UNCAUGHT, turning the intended "log a warning, still return 2xx" branch into an
# unhandled crash the very first time it is genuinely exercised — the opposite of the guarantee
# its own comments describe ("This commit is deliberately NON-FATAL to the response"). Exactly
# the rollback-then-touch-expired-attribute lesson in .claude/rules/engineering-patterns.md ("A
# replay/no-op path that rollback()s must capture any ORM ids it returns BEFORE the rollback").
# Per this task's constraints we do not fix production code; xfail(strict=True) keeps the suite
# green while pinning the defect so a future fix (capture `str(user.id)`/`str(target.id)` into a
# local BEFORE calling rollback) flips this to an unexpected-pass, forcing the marker's removal.
_ROLLBACK_EXPIRY_BUG = (
    "api/users.py's except-block logs `extra={'user_id': str(user.id)}` AFTER "
    "await session.rollback(), which expires the ORM instance regardless of "
    "expire_on_commit=False -> the sync attribute touch lazy-loads with no greenlet -> "
    "MissingGreenlet propagates uncaught, instead of the intended non-fatal degrade. "
    "Confirmed production bug; see module comment."
)


def _sub(prefix: str) -> str:
    return f"kc-{prefix}-{uuid.uuid4().hex[:10]}"


async def _admin(token_factory: Callable[..., str]) -> dict[str, str]:
    sub = _sub("admin")
    await s5.grant_role(sub, _ADMIN)
    return _auth(token_factory, sub)


def _install_kc(
    monkeypatch: pytest.MonkeyPatch,
    *,
    existing: str | None = None,
    create_status: int = 201,
    create_body: dict[str, object] | None = None,
    new_subject: str = "kc-provisioned",
    lookup_status: int = 200,
) -> dict[str, list[object]]:
    """Script the identity service. ``existing`` makes the username resolve to that subject."""
    calls: dict[str, list[object]] = {"password": []}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/openid-connect/token"):
            return httpx.Response(200, json={"access_token": "t"})
        if request.method == "GET" and request.url.path.endswith("/users"):
            if lookup_status != 200:
                return httpx.Response(lookup_status, json={"error": "boom"})
            username = request.url.params["username"]
            if existing is None:
                return httpx.Response(200, json=[])
            return httpx.Response(200, json=[{"id": existing, "username": username}])
        if request.method == "POST" and request.url.path.endswith("/users"):
            if create_status != 201:
                return httpx.Response(create_status, json=create_body or {})
            return httpx.Response(
                201, headers={"Location": f"http://kc/admin/realms/easysynq/users/{new_subject}"}
            )
        if request.method == "PUT" and request.url.path.endswith("/reset-password"):
            calls["password"].append(request.url.path)
            return httpx.Response(204)
        raise AssertionError(f"unexpected: {request.method} {request.url}")

    def factory() -> KeycloakProvisioningClient:
        return KeycloakProvisioningClient(
            base_url="http://kc",
            realm="easysynq",
            admin_user="admin",
            admin_password="secret",
            _transport=httpx.MockTransport(handler),
        )

    monkeypatch.setattr(users_api, "_kc_client", factory)
    return calls


async def _app_user_count() -> int:
    sm = get_sessionmaker()
    async with sm() as s:
        return len((await s.execute(select(AppUser.id))).scalars().all())


async def _role_id(name: str) -> uuid.UUID:
    """A seeded starter role's id (doc 07 §4.2, migration 0004_seed_authz) in the default org."""
    sm = get_sessionmaker()
    async with sm() as s:
        org_id = await s5.default_org_id()
        return (
            await s.execute(select(Role.id).where(Role.org_id == org_id, Role.name == name))
        ).scalar_one()


async def _grant_user_create_only(subject: str) -> uuid.UUID:
    """A caller holding ONLY ``user.create`` — never ``permission.grant``.

    No seeded starter role has this shape: System Administrator is the sole bundle carrying
    ``user.create``, and it carries ``permission.grant`` too (both live in the same
    ``_SYSTEM_KEYS`` tuple in migration 0004). So the role leg's own authority (R35's two-tier
    guard sits behind ``permission.grant``, not ``user.create``) can only be isolated by minting a
    single-permission override directly — mirrors ``s5_helpers.grant_lifecycle``.
    """
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        perm = (
            await s.execute(select(Permission).where(Permission.key == "user.create"))
        ).scalar_one()
        scope = Scope(org_id=user.org_id, level=ScopeLevel.SYSTEM)
        s.add(scope)
        await s.flush()
        s.add(
            PermissionOverride(
                org_id=user.org_id,
                user_id=user.id,
                permission_id=perm.id,
                effect=Effect.ALLOW,
                scope_id=scope.id,
            )
        )
        await s.commit()
        return user.id


async def _subjectless_user_id() -> uuid.UUID:
    """A user with an EMPTY ``keycloak_subject`` — the ``issue_temporary_password`` 409
    ``user_not_linked`` branch is otherwise unreachable via the API (``invite_user`` and
    ``provision_user`` both reject an empty/blank subject), so this inserts the edge case directly
    at the DB layer. ``keycloak_subject`` is UNIQUE, so an already-present "" row (e.g. a prior run
    of this same test against this DB) is reused rather than risking a duplicate-key error.
    """
    sm = get_sessionmaker()
    async with sm() as s:
        existing_id = await s.scalar(select(AppUser.id).where(AppUser.keycloak_subject == ""))
    if existing_id is not None:
        return existing_id
    org_id = await s5.default_org_id()
    async with sm() as s:
        user = AppUser(
            org_id=org_id,
            keycloak_subject="",
            display_name="No Keycloak Link",
            status=UserStatus.INVITED,
        )
        s.add(user)
        await s.commit()
        return user.id


def _break_credential_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force ONLY the trailing ``USER_CREDENTIAL_ISSUED`` audit write to fail — never the EARLIER
    commit that durably persists the app_user row + USER_CREATED (+ ROLE_ASSIGN).

    Both handlers stage the credential-issued event via ``_emit_user_event(...)`` then
    ``await session.commit()``, in the SAME try block. This patches ``_emit_user_event`` itself
    (keyed on ``event_type``, so it can never be confused with the earlier USER_CREATED call and
    needs no call-counting) — the simplest of three mechanisms tried, and the only one exercised
    below; the other two (replacing ``AsyncSession.commit`` outright, and a ``before_commit`` ORM
    event) are NOT superseded-because-broken, they hit the exact same downstream
    ``sqlalchemy.exc.MissingGreenlet`` this one does, for the SAME reason: it is a confirmed
    production bug (``_ROLLBACK_EXPIRY_BUG``, module comment above), not an artifact of any one
    injection technique. Whichever way the fault lands in this try block, the caller's own
    ``except Exception: await session.rollback(); logger.warning(..., extra={"user_id":
    str(user.id)})`` touches the now-expired ``.id`` attribute and crashes the same way.
    """
    real_emit = users_api._emit_user_event

    def _guard(
        session: Any, actor: Any, event_type: EventType, target_user_id: Any, **kwargs: Any
    ) -> None:
        if event_type is EventType.USER_CREDENTIAL_ISSUED:
            raise RuntimeError("forced failure: USER_CREDENTIAL_ISSUED emit")
        real_emit(session, actor, event_type, target_user_id, **kwargs)

    monkeypatch.setattr(users_api, "_emit_user_event", _guard)


async def test_provision_creates_the_account_the_row_and_the_credential(
    app_client: httpx.AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject = _sub("new")
    calls = _install_kc(monkeypatch, new_subject=subject)
    headers = await _admin(token_factory)

    resp = await app_client.post(
        "/api/v1/users/provision",
        headers=headers,
        json={"username": f"jdoe-{uuid.uuid4().hex[:6]}", "display_name": "J. Doe"},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert len(body["temporary_password"]) >= MIN_LENGTH
    assert body["password_delivery"] == "shown_once"
    assert body["user"]["status"] == UserStatus.INVITED.value
    # The credential is set only AFTER the row commits.
    assert len(calls["password"]) == 1

    sm = get_sessionmaker()
    async with sm() as s:
        user = await s.scalar(select(AppUser).where(AppUser.keycloak_subject == subject))
        assert user is not None and user.status is UserStatus.INVITED
        event = await s.scalar(
            select(AuditEvent).where(
                AuditEvent.object_id == user.id, AuditEvent.event_type == EventType.USER_CREATED
            )
        )
        assert event is not None
        # Secrets hygiene: the password must never reach an audit payload.
        assert body["temporary_password"] not in str(event.after)


async def test_unlinked_username_collision_returns_the_subject_for_the_link_path(
    app_client: httpx.AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orphan = _sub("orphan")
    _install_kc(monkeypatch, existing=orphan)
    headers = await _admin(token_factory)

    resp = await app_client.post(
        "/api/v1/users/provision", headers=headers, json={"username": "already-there"}
    )

    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "keycloak_username_exists_unlinked"
    # This member is what drives the SPA's "Link the existing account" button.
    assert body["keycloak_subject"] == orphan


async def test_keycloak_failure_creates_no_app_user(
    app_client: httpx.AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_kc(monkeypatch, lookup_status=503)
    headers = await _admin(token_factory)
    before = await _app_user_count()

    resp = await app_client.post(
        "/api/v1/users/provision", headers=headers, json={"username": "never-created"}
    )

    assert resp.status_code == 502
    assert resp.json()["code"] == "keycloak_unavailable"
    assert await _app_user_count() == before  # delta-based, never an absolute count


async def test_provision_with_roles_assigns_them_and_audits_in_order(
    app_client: httpx.AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A privilege grant must not be invisible to audit: each assigned role gets its own
    ROLE_ASSIGN row, and USER_CREATED must not occur AFTER any of them — a user can't be granted a
    role before they exist."""
    subject = _sub("roled")
    _install_kc(monkeypatch, new_subject=subject)
    headers = await _admin(token_factory)
    author_id = await _role_id("Author")
    approver_id = await _role_id("Approver")

    resp = await app_client.post(
        "/api/v1/users/provision",
        headers=headers,
        json={
            "username": f"roled-{uuid.uuid4().hex[:6]}",
            "role_ids": [str(author_id), str(approver_id)],
        },
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert set(body["user"]["roles"]) == {"Author", "Approver"}
    user_id = uuid.UUID(body["user"]["id"])

    sm = get_sessionmaker()
    async with sm() as s:
        assignments = (
            (await s.execute(select(RoleAssignment).where(RoleAssignment.user_id == user_id)))
            .scalars()
            .all()
        )
        assert {a.role_id for a in assignments} == {author_id, approver_id}

        created = await s.scalar(
            select(AuditEvent).where(
                AuditEvent.object_id == user_id, AuditEvent.event_type == EventType.USER_CREATED
            )
        )
        assert created is not None

        role_assign_events = (
            (
                await s.execute(
                    select(AuditEvent).where(
                        AuditEvent.event_type == EventType.ROLE_ASSIGN,
                        AuditEvent.scope_ref == f"user:{user_id}",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(role_assign_events) == 2
        assert {e.after["role_id"] for e in role_assign_events} == {
            str(author_id),
            str(approver_id),
        }
        # A user must not appear to have been granted a role before they existed.
        assert all(created.occurred_at <= e.occurred_at for e in role_assign_events)
        assert body["temporary_password"] not in str([e.after for e in role_assign_events])


async def test_unknown_role_id_returns_422_and_creates_no_user(
    app_client: httpx.AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_kc(monkeypatch)
    headers = await _admin(token_factory)
    before = await _app_user_count()

    resp = await app_client.post(
        "/api/v1/users/provision",
        headers=headers,
        json={"username": "ghost-role", "role_ids": [str(uuid.uuid4())]},
    )

    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "validation_error"
    assert await _app_user_count() == before


async def test_already_linked_username_collision_returns_user_exists(
    app_client: httpx.AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject = _sub("linked")
    _install_kc(monkeypatch, new_subject=subject)
    headers = await _admin(token_factory)
    first = await app_client.post(
        "/api/v1/users/provision",
        headers=headers,
        json={"username": f"linked-{uuid.uuid4().hex[:6]}"},
    )
    assert first.status_code == 201, first.text

    # Re-script Keycloak so a SECOND provision's username lookup resolves to the SAME subject —
    # this time one already bound to an app_user row, unlike the unlinked-orphan case above.
    _install_kc(monkeypatch, existing=subject)
    resp = await app_client.post(
        "/api/v1/users/provision", headers=headers, json={"username": "someone-else"}
    )

    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["code"] == "user_exists"
    assert "keycloak_subject" not in body


async def test_duplicate_email_returns_keycloak_email_exists(
    app_client: httpx.AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_kc(
        monkeypatch, create_status=409, create_body={"errorMessage": "User exists with same email"}
    )
    headers = await _admin(token_factory)

    resp = await app_client.post(
        "/api/v1/users/provision",
        headers=headers,
        json={"username": f"dupemail-{uuid.uuid4().hex[:6]}", "email": "dup@example.io"},
    )

    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "keycloak_email_exists"


async def test_role_leg_requires_permission_grant(
    app_client: httpx.AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``role_ids`` is a distinct authority gated on ``permission.grant`` — holding ``user.create``
    alone (the bare minimum to reach the handler at all) must not be enough."""
    _install_kc(monkeypatch)
    sub = _sub("nopermgrant")
    await _grant_user_create_only(sub)
    headers = _auth(token_factory, sub)
    before = await _app_user_count()
    author_id = await _role_id("Author")

    resp = await app_client.post(
        "/api/v1/users/provision",
        headers=headers,
        json={"username": "blocked-role-grant", "role_ids": [str(author_id)]},
    )

    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "permission_denied"
    assert await _app_user_count() == before


async def test_credential_reissue_emits_audit_event(
    app_client: httpx.AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject = _sub("reissue")
    calls = _install_kc(monkeypatch, new_subject=subject)
    headers = await _admin(token_factory)

    provisioned = await app_client.post(
        "/api/v1/users/provision",
        headers=headers,
        json={"username": f"reissue-{uuid.uuid4().hex[:6]}"},
    )
    assert provisioned.status_code == 201, provisioned.text
    user_id = provisioned.json()["user"]["id"]

    resp = await app_client.post(f"/api/v1/users/{user_id}/temporary-password", headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["temporary_password"]) >= MIN_LENGTH
    assert body["password_delivery"] == "shown_once"
    # Both the initial provision AND the explicit reissue set a live credential.
    assert len(calls["password"]) == 2

    sm = get_sessionmaker()
    async with sm() as s:
        events = (
            (
                await s.execute(
                    select(AuditEvent).where(
                        AuditEvent.object_id == uuid.UUID(user_id),
                        AuditEvent.event_type == EventType.USER_CREDENTIAL_ISSUED,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(events) == 2  # one from provision, one from this explicit reissue
    assert body["temporary_password"] not in str([e.after for e in events])


async def test_reissue_without_linked_subject_returns_409(
    app_client: httpx.AsyncClient,
    token_factory: Callable[..., str],
) -> None:
    headers = await _admin(token_factory)
    unlinked_id = await _subjectless_user_id()

    resp = await app_client.post(f"/api/v1/users/{unlinked_id}/temporary-password", headers=headers)

    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "user_not_linked"


@pytest.mark.xfail(strict=True, reason=_ROLLBACK_EXPIRY_BUG)
async def test_provision_survives_a_failed_credential_audit_commit(
    app_client: httpx.AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The USER_CREDENTIAL_ISSUED commit is deliberately non-fatal: the credential is already live
    in Keycloak and the generated password exists only in the response at that point, so losing the
    response would be strictly worse than losing this one audit row. Force ONLY that commit to fail
    and assert the caller still gets a 2xx carrying the temporary password, while the EARLIER
    commit (the app_user row + USER_CREATED) is provably untouched.

    XFAIL (strict, confirmed production bug — see ``_ROLLBACK_EXPIRY_BUG``): this is what the
    handler is DOCUMENTED to do, but is not what it currently does.
    """
    subject = _sub("auditfail")
    _install_kc(monkeypatch, new_subject=subject)
    headers = await _admin(token_factory)
    _break_credential_commit(monkeypatch)

    resp = await app_client.post(
        "/api/v1/users/provision",
        headers=headers,
        json={"username": f"auditfail-{uuid.uuid4().hex[:6]}"},
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert len(body["temporary_password"]) >= MIN_LENGTH

    sm = get_sessionmaker()
    async with sm() as s:
        user = await s.scalar(select(AppUser).where(AppUser.keycloak_subject == subject))
        assert user is not None  # the earlier commit was NOT rolled back
        created = await s.scalar(
            select(AuditEvent).where(
                AuditEvent.object_id == user.id, AuditEvent.event_type == EventType.USER_CREATED
            )
        )
        assert created is not None  # ditto
        issued = await s.scalar(
            select(AuditEvent).where(
                AuditEvent.object_id == user.id,
                AuditEvent.event_type == EventType.USER_CREDENTIAL_ISSUED,
            )
        )
        assert issued is None  # proves the forced failure really did roll back ONLY this row


@pytest.mark.xfail(strict=True, reason=_ROLLBACK_EXPIRY_BUG)
async def test_reissue_survives_a_failed_credential_audit_commit(
    app_client: httpx.AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """As above, for the reissue endpoint's own non-fatal commit.

    XFAIL (strict, confirmed production bug — see ``_ROLLBACK_EXPIRY_BUG``).
    """
    subject = _sub("auditfail2")
    _install_kc(monkeypatch, new_subject=subject)
    headers = await _admin(token_factory)
    provisioned = await app_client.post(
        "/api/v1/users/provision",
        headers=headers,
        json={"username": f"auditfail2-{uuid.uuid4().hex[:6]}"},
    )
    assert provisioned.status_code == 201, provisioned.text
    user_id = uuid.UUID(provisioned.json()["user"]["id"])

    sm = get_sessionmaker()

    async def _issued_count() -> int:
        async with sm() as s:
            rows = (
                (
                    await s.execute(
                        select(AuditEvent.id).where(
                            AuditEvent.object_id == user_id,
                            AuditEvent.event_type == EventType.USER_CREDENTIAL_ISSUED,
                        )
                    )
                )
                .scalars()
                .all()
            )
            return len(rows)

    before = await _issued_count()
    assert before == 1  # from the provision above

    _break_credential_commit(monkeypatch)
    resp = await app_client.post(f"/api/v1/users/{user_id}/temporary-password", headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["temporary_password"]) >= MIN_LENGTH
    assert await _issued_count() == before  # the reissue's own commit rolled back; no new row
