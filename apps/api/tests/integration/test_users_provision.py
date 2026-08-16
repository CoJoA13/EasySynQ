"""S-user-create integration proofs — in-app Keycloak provisioning and credential issuance.

No live Keycloak (D1): the shared ``identity_provisioning.keycloak_client`` factory is monkeypatched
to build the real ``KeycloakProvisioningClient`` over an ``httpx.MockTransport``, so the endpoint
exercises its true code path against a scripted identity service.

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

import json
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from sqlalchemy import delete, select

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
from easysynq_api.services.identity import provisioning as identity_provisioning
from easysynq_api.services.keycloak_provisioning import KeycloakProvisioningClient

from . import s5_helpers as s5
from .test_vault import _auth, _ensure_user

pytestmark = pytest.mark.integration

_ADMIN = "System Administrator"
_RECONCILED_USER_PROFILE = {
    "attributes": [{"name": "email"}, {"name": "firstName"}, {"name": "lastName"}]
}

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
# FIXED in this same change: both handlers now capture `str(user.id)`/`str(target.id)` into a
# local BEFORE the try block that contains the rollback, and log that local instead of touching
# the ORM instance afterward. The two tests that pinned this defect (below) are therefore no
# longer `xfail` — they are ordinary passing tests proving the non-fatal degrade actually holds.


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
    password_status: int = 204,
) -> dict[str, list[object]]:
    """Script the identity service. ``existing`` makes the username resolve to that subject.
    ``password_status`` scripts the ``reset-password`` leg — a non-204 fails it AFTER the account
    lookup/create has already succeeded, to exercise a post-commit Keycloak failure."""
    calls: dict[str, list[object]] = {"password": [], "created": []}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/openid-connect/token"):
            return httpx.Response(200, json={"access_token": "t"})
        if request.method == "GET" and request.url.path.endswith("/users/profile"):
            return httpx.Response(200, json=_RECONCILED_USER_PROFILE)
        if request.method == "GET" and request.url.path.endswith("/users"):
            if lookup_status != 200:
                return httpx.Response(lookup_status, json={"error": "boom"})
            username = request.url.params["username"]
            if existing is None:
                return httpx.Response(200, json=[])
            return httpx.Response(200, json=[{"id": existing, "username": username}])
        if request.method == "POST" and request.url.path.endswith("/users"):
            calls["created"].append(json.loads(request.content))
            if create_status != 201:
                return httpx.Response(create_status, json=create_body or {})
            return httpx.Response(
                201, headers={"Location": f"http://kc/admin/realms/easysynq/users/{new_subject}"}
            )
        if request.method == "PUT" and request.url.path.endswith("/reset-password"):
            calls["password"].append(request.url.path)
            if password_status != 204:
                return httpx.Response(password_status, json={"error": "boom"})
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

    monkeypatch.setattr(identity_provisioning, "keycloak_client", factory)
    return calls


def _install_kc_race(monkeypatch: pytest.MonkeyPatch, *, winner_subject: str) -> None:
    """Script the identity service for the CREATE-CONFLICT RACE (FIX 2): our own precheck GET
    (lookup call #1) reports the username absent, our POST create then 409s (another request won
    the race and created it in between), and the conflict-classification re-read GET (lookup call
    #2) now finds ``winner_subject``. Distinct from ``_install_kc``'s ``existing=`` param, which
    makes EVERY lookup resolve the same way and so can only script the PRE-check-finds-it case,
    never this race where the two lookups genuinely disagree."""
    calls = {"lookup": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/openid-connect/token"):
            return httpx.Response(200, json={"access_token": "t"})
        if request.method == "GET" and request.url.path.endswith("/users/profile"):
            return httpx.Response(200, json=_RECONCILED_USER_PROFILE)
        if request.method == "GET" and request.url.path.endswith("/users"):
            calls["lookup"] += 1
            username = request.url.params["username"]
            if calls["lookup"] == 1:
                return httpx.Response(200, json=[])
            return httpx.Response(200, json=[{"id": winner_subject, "username": username}])
        if request.method == "POST" and request.url.path.endswith("/users"):
            return httpx.Response(409, json={"errorMessage": "User exists with same username"})
        raise AssertionError(f"unexpected: {request.method} {request.url}")

    def factory() -> KeycloakProvisioningClient:
        return KeycloakProvisioningClient(
            base_url="http://kc",
            realm="easysynq",
            admin_user="admin",
            admin_password="secret",
            _transport=httpx.MockTransport(handler),
        )

    monkeypatch.setattr(identity_provisioning, "keycloak_client", factory)


def _install_kc_classifier_known_race_then_lookup_fails(
    monkeypatch: pytest.MonkeyPatch, *, winner_subject: str
) -> None:
    """The Keycloak transport's conflict classifier finds the exact subject, then a later lookup
    fails. Ordinary provisioning must use the classified subject instead of creating this third
    lookup failure path; bootstrap still re-reads to validate its marker."""
    calls = {"lookup": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/openid-connect/token"):
            return httpx.Response(200, json={"access_token": "t"})
        if request.method == "GET" and request.url.path.endswith("/users/profile"):
            return httpx.Response(200, json=_RECONCILED_USER_PROFILE)
        if request.method == "GET" and request.url.path.endswith("/users"):
            calls["lookup"] += 1
            username = request.url.params["username"]
            if calls["lookup"] == 1:
                return httpx.Response(200, json=[])
            if calls["lookup"] == 2:
                return httpx.Response(200, json=[{"id": winner_subject, "username": username}])
            return httpx.Response(503, json={"error": "transient lookup failure"})
        if request.method == "POST" and request.url.path.endswith("/users"):
            return httpx.Response(409, json={"errorMessage": "User exists with same username"})
        raise AssertionError(f"unexpected: {request.method} {request.url}")

    def factory() -> KeycloakProvisioningClient:
        return KeycloakProvisioningClient(
            base_url="http://kc",
            realm="easysynq",
            admin_user="admin",
            admin_password="secret",
            _transport=httpx.MockTransport(handler),
        )

    monkeypatch.setattr(identity_provisioning, "keycloak_client", factory)


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
    at the DB layer. ``keycloak_subject`` is UNIQUE, so an already-present "" row (e.g. a crash
    before a prior run's cleanup could complete) is reused rather than risking a duplicate-key
    error; the sole caller deletes the row in a ``finally`` once it is done with it, so this
    fallback should normally be dead code, not the steady-state path.
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
    event) were NOT superseded-because-broken: before the fix, they hit the exact same downstream
    ``sqlalchemy.exc.MissingGreenlet``, for the same reason — the caller's own
    ``except Exception: await session.rollback()`` touched an ORM attribute on the now-expired
    instance and crashed, regardless of which injection technique raised the fault. Both handlers
    now capture the id into a local BEFORE the rollback (see ``api/users.py``), so this helper's
    choice of injection point is unchanged but the crash is gone — these tests now pass for real.
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


async def test_ordinary_provisioning_never_sends_a_bootstrap_marker(
    app_client: httpx.AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ordinary user creation is never eligible for bootstrap recovery ownership."""
    calls = _install_kc(monkeypatch, new_subject=_sub("no-bootstrap-marker"))
    headers = await _admin(token_factory)
    username = f"ordinary-{uuid.uuid4().hex[:6]}"

    resp = await app_client.post(
        "/api/v1/users/provision",
        headers=headers,
        json={"username": username},
    )

    assert resp.status_code == 201, resp.text
    assert calls["created"] == [
        {
            "username": username,
            "enabled": True,
        }
    ]


async def test_ordinary_provisioning_canonicalizes_mixed_case_username(
    app_client: httpx.AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_kc(monkeypatch, new_subject=_sub("canonical-username"))
    headers = await _admin(token_factory)
    submitted = f"  Mixed.User-{uuid.uuid4().hex[:6]}  "
    display_name = "Mixed Case Display"

    response = await app_client.post(
        "/api/v1/users/provision",
        headers=headers,
        json={"username": submitted, "display_name": display_name},
    )

    assert response.status_code == 201, response.text
    assert calls["created"][0]["username"] == submitted.strip().lower()
    assert response.json()["user"]["display_name"] == display_name


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


async def test_set_password_failure_after_commit_leaves_the_row_but_no_credential_audit(
    app_client: httpx.AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The single most load-bearing failure path in the slice, and the counterpart to the
    pre-commit failure above: the Keycloak account + ``app_user`` row are ALREADY committed by the
    time ``set_temporary_password`` runs, so a failure there must not be treated like the pre-commit
    case. The row must PERSIST (never retry create — it would only collide on the now-existing
    username), the caller gets 502 ``keycloak_unavailable`` with guidance to reissue instead of
    retrying create, and — critically — no ``USER_CREDENTIAL_ISSUED`` audit row may exist, because
    no credential was actually issued; the trail must never claim otherwise."""
    subject = _sub("pwfail")
    _install_kc(monkeypatch, new_subject=subject, password_status=500)
    headers = await _admin(token_factory)

    resp = await app_client.post(
        "/api/v1/users/provision",
        headers=headers,
        json={"username": f"pwfail-{uuid.uuid4().hex[:6]}"},
    )

    assert resp.status_code == 502, resp.text
    assert resp.json()["code"] == "keycloak_unavailable"

    sm = get_sessionmaker()
    async with sm() as s:
        user = await s.scalar(select(AppUser).where(AppUser.keycloak_subject == subject))
        assert user is not None  # the app_user row survives a post-commit Keycloak failure
        issued = await s.scalar(
            select(AuditEvent).where(
                AuditEvent.object_id == user.id,
                AuditEvent.event_type == EventType.USER_CREDENTIAL_ISSUED,
            )
        )
        assert issued is None  # no credential was ever actually issued for this user


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


async def test_create_conflict_race_finds_unlinked_subject_offers_the_link_path(
    app_client: httpx.AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FIX 2 — the race where another request creates the SAME username between our precheck
    (reports absent) and our POST (409s). ``_classify_create_conflict``'s re-read now FINDS the
    colliding subject, and the handler must not reduce that to a bare `user_exists`: it must
    classify it exactly as the precheck-found path already does. Unlinked case: 409
    `keycloak_username_exists_unlinked` carrying `keycloak_subject`, so the "link the existing
    account" affordance is still offered even though OUR precheck never saw the collision."""
    winner = _sub("racewinner")
    _install_kc_race(monkeypatch, winner_subject=winner)
    headers = await _admin(token_factory)

    resp = await app_client.post(
        "/api/v1/users/provision", headers=headers, json={"username": "racey-unlinked"}
    )

    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["code"] == "keycloak_username_exists_unlinked"
    assert body["keycloak_subject"] == winner


async def test_create_conflict_race_finds_linked_subject_returns_user_exists(
    app_client: httpx.AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """As above, but the winning subject is ALREADY bound to an `app_user` row (a concurrent
    provision that ran all the way through) — the race re-read must classify this as
    `user_exists`, not the unlinked link-path, exactly mirroring
    `test_already_linked_username_collision_returns_user_exists` for the precheck-found case."""
    subject = _sub("racelinked")
    _install_kc(monkeypatch, new_subject=subject)
    headers = await _admin(token_factory)
    first = await app_client.post(
        "/api/v1/users/provision",
        headers=headers,
        json={"username": f"racelinked-{uuid.uuid4().hex[:6]}"},
    )
    assert first.status_code == 201, first.text

    _install_kc_race(monkeypatch, winner_subject=subject)
    resp = await app_client.post(
        "/api/v1/users/provision", headers=headers, json={"username": "someone-else-race"}
    )

    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["code"] == "user_exists"
    assert "keycloak_subject" not in body


@pytest.mark.parametrize(
    ("linked", "expected_code"),
    [
        (False, "keycloak_username_exists_unlinked"),
        (True, "user_exists"),
    ],
)
async def test_classifier_known_conflict_preserves_the_linked_or_unlinked_affordance(
    app_client: httpx.AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
    linked: bool,
    expected_code: str,
) -> None:
    subject = _sub("classifier-known")
    if linked:
        async with get_sessionmaker()() as session:
            await _ensure_user(session, subject)
            await session.commit()
    _install_kc_classifier_known_race_then_lookup_fails(monkeypatch, winner_subject=subject)
    headers = await _admin(token_factory)

    resp = await app_client.post(
        "/api/v1/users/provision",
        headers=headers,
        json={"username": f"classifier-known-{uuid.uuid4().hex[:6]}"},
    )

    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["code"] == expected_code
    if linked:
        assert "keycloak_subject" not in body
    else:
        assert body["keycloak_subject"] == subject


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
        json={"username": f"dupemail-{uuid.uuid4().hex[:6]}", "email": "dup@example.com"},
    )

    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "keycloak_email_exists"


async def test_keycloak_400_on_create_returns_422_validation_error(
    app_client: httpx.AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Keycloak 400 (an invalid email, or a value the realm's user-profile validation refuses)
    is a client error, not an outage: the dependency IS reachable and retrying the identical form
    cannot succeed. It must surface as 422 validation_error (never 502 keycloak_unavailable), and
    — like every other pre-commit Keycloak failure — must create no app_user row."""
    _install_kc(
        monkeypatch, create_status=400, create_body={"errorMessage": "Invalid email address."}
    )
    headers = await _admin(token_factory)
    before = await _app_user_count()

    resp = await app_client.post(
        "/api/v1/users/provision",
        headers=headers,
        json={"username": f"bademail-{uuid.uuid4().hex[:6]}", "email": "not-an-email"},
    )

    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "validation_error"
    assert await _app_user_count() == before


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
    try:
        resp = await app_client.post(
            f"/api/v1/users/{unlinked_id}/temporary-password", headers=headers
        )

        assert resp.status_code == 409, resp.text
        assert resp.json()["code"] == "user_not_linked"
    finally:
        # `keycloak_subject` is UNIQUE — never leave the "" sentinel row for the next run to trip
        # over (shared-DB discipline, .claude/rules/engineering-patterns.md).
        sm = get_sessionmaker()
        async with sm() as s:
            await s.execute(delete(AppUser).where(AppUser.id == unlinked_id))
            await s.commit()


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
        # The injected failure raises INSIDE _emit_user_event itself, before the row is ever
        # staged, so there is nothing here for session.rollback() to undo. This only confirms no
        # USER_CREDENTIAL_ISSUED row exists despite the forced failure — paired with the asserts
        # above, which confirm the EARLIER commit (the app_user row + USER_CREATED) is untouched.
        assert issued is None


async def test_reissue_survives_a_failed_credential_audit_commit(
    app_client: httpx.AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """As above, for the reissue endpoint's own non-fatal commit."""
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


# --- two-tier credential-reset guard (R35/R64 rule 5) — CONFIRMED account-takeover fix (P1) ----
#
# `issue_temporary_password` used to gate on `user.create` alone. That permission can be granted
# INDEPENDENTLY of `permission.grant` (see `_grant_user_create_only` above), so a caller holding
# only `user.create` could reset the Keycloak password of ANY user in the org — including a
# System Administrator — and sign in as them (the realm enforces no MFA).
#
# An intermediate fix mirrored the existing R35 two-tier guard by inspecting the TARGET: a caller
# could reset a credential if system-tier, OR if the target held no *system-domain* permission
# (`user_system_domain_keys`, since removed). That was still a hole: an Approver's, Process
# Owner's, or Register Steward's authority is entirely CONTENT-domain (approving/releasing
# regulated documents), invisible to a system-domain test — a caller holding only `user.create`
# could still mint a fresh credential for an Approver and sign in as them, forging an
# approval/release signature. The guard now TIGHTENS instead of enumerating "privileged" content
# roles: resetting ANOTHER user's credential always requires a system-tier caller, full stop, with
# no inspection of the target at all.


async def _plain_user_id(subject: str) -> uuid.UUID:
    """A linked user with NO grants at all — the credential-reset guard's "ordinary, unprivileged
    user" baseline. Under the tightened R64 rule 5 the guard never inspects the target at all, so
    this baseline now proves the OPPOSITE of what it once did: a non-system-tier caller is refused
    even here (see `test_reset_credential_of_an_unprivileged_target_still_requires_system_tier`)."""
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        await s.commit()
        return user.id


async def _grant_system_domain_override(subject: str, key: str) -> uuid.UUID:
    """A target privileged in a system-domain permission ONLY via a per-user
    ``PermissionOverride`` — never a role. Mirrors ``_grant_user_create_only`` above (which mints
    this exact principal shape for the CALLER); this mints it for the TARGET. Originally isolated
    the override leg of the now-removed ``user_system_domain_keys``; kept as regression coverage
    proving the tightened guard refuses this shape too, for the same reason it refuses every other
    shape — it no longer inspects the target's grants at all."""
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        perm = (await s.execute(select(Permission).where(Permission.key == key))).scalar_one()
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


async def test_reset_credential_of_an_unprivileged_target_still_requires_system_tier(
    app_client: httpx.AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RETARGETED (R64 rule 5, tightened): a content-tier caller holding ONLY `user.create`
    (never `permission.grant`) may NOT reset another user's credential — not even an ordinary,
    unprivileged target. An earlier round of this guard permitted exactly this case (a target
    holding no system-domain permission needed no system-tier caller); that test asserted a 200
    here. It is retargeted to assert refusal because the narrower rule could not see CONTENT-domain
    authority (an Approver's `document.approve`/`document.release`) as privilege worth protecting
    — the guard no longer inspects the target at all, so resetting ANY other user's credential
    demands a system-tier caller, full stop."""
    calls = _install_kc(monkeypatch)
    caller_sub = _sub("resetcaller")
    await _grant_user_create_only(caller_sub)
    headers = _auth(token_factory, caller_sub)
    target_id = await _plain_user_id(_sub("resettarget"))

    resp = await app_client.post(f"/api/v1/users/{target_id}/temporary-password", headers=headers)

    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "two_tier_violation"
    assert calls["password"] == []

    sm = get_sessionmaker()
    async with sm() as s:
        event = await s.scalar(
            select(AuditEvent).where(
                AuditEvent.event_type == EventType.TWO_TIER_VIOLATION,
                AuditEvent.scope_ref == f"user:{target_id}",
            )
        )
        assert event is not None
        # FIX 3: the denial must name the RESET operation, never the unrelated `permission.grant`
        # — this caller never attempted a grant, and recording it as one would misfile the trail.
        assert event.after["permission_key"] == "user.reset_credential"
        assert event.after["permission_key"] != "permission.grant"


async def test_reset_guard_refuses_user_create_only_caller_regardless_of_target_roles(
    app_client: httpx.AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R64 gates the caller's tier, so a target's content role cannot relax the refusal."""
    calls = _install_kc(monkeypatch)
    caller_sub = _sub("resetcaller-content-target")
    await _grant_user_create_only(caller_sub)
    headers = _auth(token_factory, caller_sub)
    target_id = await s5.grant_role(_sub("reset-content-target"), "Approver")

    resp = await app_client.post(f"/api/v1/users/{target_id}/temporary-password", headers=headers)

    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "two_tier_violation"
    assert calls["password"] == []


async def test_reset_credential_of_a_system_domain_target_requires_system_tier(
    app_client: httpx.AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SAME content-tier caller as above must NOT be able to reset the credential of a user
    who holds a system-domain permission — a fresh credential (no realm MFA) would let them sign
    in AS that user; this is the account-takeover scenario the guard exists to stop. Under the
    tightened R64 rule 5 this is just one instance of the blanket "caller must be system-tier"
    rule (see the unprivileged-target test above for the general case), kept as its own test
    because it is the sharpest illustration of the stakes. The guard must refuse BEFORE any
    Keycloak call is made: `_install_kc`'s handler raises `AssertionError` on any call its
    scripted branches don't recognize, so a bypassed guard would surface loudly, not silently
    pass; asserting `calls["password"] == []` additionally proves the specific reset-password call
    never happened."""
    calls = _install_kc(monkeypatch)
    caller_sub = _sub("resetcaller2")
    await _grant_user_create_only(caller_sub)
    headers = _auth(token_factory, caller_sub)
    target_id = await s5.grant_role(_sub("resettarget2"), _ADMIN)

    resp = await app_client.post(f"/api/v1/users/{target_id}/temporary-password", headers=headers)

    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "two_tier_violation"
    assert calls["password"] == []


async def test_system_tier_caller_can_reset_a_privileged_targets_credential(
    app_client: httpx.AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Positive control (no over-restriction): a system-tier caller (System Administrator) MAY
    reset a privileged target's credential — the guard gates the content tier, not the
    capability itself. This is the path the product actually uses (an Admin resetting any user's
    credential), so it must keep working end-to-end under the tightened R64 rule 5."""
    calls = _install_kc(monkeypatch)
    headers = await _admin(token_factory)
    target_id = await s5.grant_role(_sub("resettarget3"), _ADMIN)

    resp = await app_client.post(f"/api/v1/users/{target_id}/temporary-password", headers=headers)

    assert resp.status_code == 200, resp.text
    assert len(resp.json()["temporary_password"]) >= MIN_LENGTH
    assert len(calls["password"]) == 1


async def test_reset_credential_of_an_override_only_privileged_target_requires_system_tier(
    app_client: httpx.AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression coverage for a target privileged ONLY via a per-user `PermissionOverride` —
    never a role. This test used to isolate the override leg of `user_system_domain_keys`
    (`services/authz/repository.py`), which the R64 rule-5 tightening removed entirely: the guard
    no longer inspects the target's grants AT ALL, role-derived, override-derived, or otherwise.
    Kept — retargeted rather than deleted — to prove no future change can quietly resurrect
    target inspection for exactly this shape: the content-tier caller must still be refused,
    before any Keycloak call, for the same reason it is refused against every other target."""
    calls = _install_kc(monkeypatch)
    caller_sub = _sub("resetcaller4")
    await _grant_user_create_only(caller_sub)
    headers = _auth(token_factory, caller_sub)
    target_id = await _grant_system_domain_override(_sub("resettarget4"), "backup.run")

    resp = await app_client.post(f"/api/v1/users/{target_id}/temporary-password", headers=headers)

    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "two_tier_violation"
    assert calls["password"] == []
