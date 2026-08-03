"""User-lifecycle admin surface (slice S8d, doc 08 §10/§11): the roster + invite + enable/disable.

The per-user role/override *grants* live in ``api/authz.py`` (shipped in S2, reused unchanged); this
router owns the ``app_user`` lifecycle. All routes are PEP-gated + org-scoped + audited pre-commit.

**Invite** pre-creates an ``INVITED`` ``app_user`` bound to a Keycloak ``sub`` the operator supplies
(the operator creates the Keycloak account out-of-band — in-app Keycloak admin-API provisioning is a
v1 convenience, D1/no-Keycloak-in-CI). The row reconciles with the existing JIT lookup on that
subject's first login (``auth/dependencies.py`` matches on ``keycloak_subject`` and flips
INVITED→ACTIVE). **Enable/disable** toggles ``status`` between ACTIVE and DISABLED; disabling the
sole active System Administrator is refused (the break-glass principle, doc 08 §9.1).
"""

from __future__ import annotations

import datetime
import logging
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models._audit_enums import ActorType, AuditObjectType, EventType
from ..db.models.app_user import AppUser, UserStatus
from ..db.models.audit_event import AuditEvent
from ..db.models.role import Role, RoleAssignment
from ..db.session import get_session
from ..domain.authz.types import ResourceContext
from ..domain.identity.temp_password import generate_temporary_password
from ..logging import request_id_var
from ..problems import ProblemException
from ..services.authz import (
    AuthzAuditSink,
    assert_can_assign_role,
    disable_removes_last_admin,
    enforce,
    get_authz_audit_sink,
    invalidate_user_permissions,
    require,
)
from ..services.backup.realm_export import realm_name_from_issuer
from ..services.keycloak_provisioning import (
    KeycloakConflict,
    KeycloakNotConfigured,
    KeycloakProvisioningClient,
    KeycloakUnavailable,
)

# Reuse the established ROLE_ASSIGN audit shape (object_type=permission, scope_ref names the
# target user) so a role minted through this endpoint is visible to the same audit queries as one
# minted via POST /users/{id}/roles — api/authz.py imports only services/db models, never
# api/users.py, so this is not a cycle (the .dcr/.improvement precedent in management_review.py).
from .authz import _audit_authz_change

logger = logging.getLogger("easysynq.users")
router = APIRouter(prefix="/api/v1", tags=["users"])

# Dependency singletons — a require(...) call must not sit in an argument default (ruff B008).
_user_read = require("user.read")
_user_create = require("user.create")
_user_deactivate = require("user.deactivate")


def _rid() -> uuid.UUID | None:
    raw = request_id_var.get()
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def _emit_user_event(
    session: AsyncSession,
    actor: AppUser,
    event_type: EventType,
    target_user_id: uuid.UUID,
    *,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    """Append a user-lifecycle ``audit_event`` (object_type=user) BEFORE commit, so the change + its
    audit row commit atomically (mirrors ``authz._audit_authz_change``). Hashes stay NULL (R12)."""
    session.add(
        AuditEvent(
            org_id=actor.org_id,
            occurred_at=datetime.datetime.now(datetime.UTC),
            actor_id=actor.id,
            actor_type=ActorType.user,
            event_type=event_type,
            object_type=AuditObjectType.user,
            object_id=target_user_id,
            before=before,
            after=after,
            request_id=_rid(),
        )
    )


class UserInvite(BaseModel):
    keycloak_subject: str
    display_name: str | None = None
    email: str | None = None


class UserProvision(BaseModel):
    username: str
    display_name: str | None = None
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    role_ids: list[uuid.UUID] = []


class UserStatusUpdate(BaseModel):
    status: Literal["ACTIVE", "DISABLED"]


def _represent(user: AppUser, role_names: list[str]) -> dict[str, Any]:
    return {
        "id": str(user.id),
        "keycloak_subject": user.keycloak_subject,
        "display_name": user.display_name,
        "email": user.email,
        "status": user.status.value,
        "mfa_enrolled": user.mfa_enrolled,
        "is_guest": user.is_guest,
        "roles": role_names,
    }


async def _role_names_by_user(
    session: AsyncSession, org_id: uuid.UUID, user_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[str]]:
    if not user_ids:
        return {}
    rows = (
        await session.execute(
            select(RoleAssignment.user_id, Role.name)
            .join(Role, Role.id == RoleAssignment.role_id)
            .where(RoleAssignment.org_id == org_id, RoleAssignment.user_id.in_(user_ids))
            .order_by(Role.name)
        )
    ).all()
    out: dict[uuid.UUID, list[str]] = {}
    for user_id, name in rows:
        out.setdefault(user_id, []).append(name)
    return out


async def _get_user(session: AsyncSession, user_id: uuid.UUID, org_id: uuid.UUID) -> AppUser:
    # Org-scoped lookup; a cross-org target reads as not-found (no existence leak), keeping the
    # surface tenant-safe for the additive multi-org path (matches authz.py).
    user = await session.get(AppUser, user_id)
    if user is None or user.org_id != org_id:
        raise ProblemException(status=404, code="not_found", title="User not found")
    return user


@router.get("/users")
async def list_users(
    status_filter: str | None = None,
    is_guest: bool | None = None,
    caller: AppUser = Depends(_user_read),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    """The user roster (org-scoped). Optional ``?status_filter=`` / ``?is_guest=``."""
    stmt = select(AppUser).where(AppUser.org_id == caller.org_id)
    if status_filter is not None:
        try:
            stmt = stmt.where(AppUser.status == UserStatus(status_filter))
        except ValueError as exc:
            raise ProblemException(
                status=422, code="validation_error", title=f"Unknown status: {status_filter!r}"
            ) from exc
    if is_guest is not None:
        stmt = stmt.where(AppUser.is_guest == is_guest)
    users = list((await session.execute(stmt.order_by(AppUser.display_name))).scalars().all())
    names = await _role_names_by_user(session, caller.org_id, [u.id for u in users])
    return [_represent(u, names.get(u.id, [])) for u in users]


def _kc_client() -> KeycloakProvisioningClient:
    settings = get_settings()
    return KeycloakProvisioningClient(
        base_url=settings.keycloak_admin_url,
        realm=realm_name_from_issuer(settings.oidc_issuer),
        admin_user=settings.keycloak_admin_user,
        admin_password=settings.keycloak_admin_password,
    )


@router.post("/users/provision", status_code=status.HTTP_201_CREATED)
async def provision_user(
    request: Request,
    body: UserProvision,
    caller: AppUser = Depends(_user_create),
    session: AsyncSession = Depends(get_session),
    sink: AuthzAuditSink = Depends(get_authz_audit_sink),
) -> dict[str, Any]:
    """Create the Keycloak account AND the ``app_user`` row in one call, returning a generated
    temporary password shown once (slice S-user-create).

    Ordering is deliberate: the Keycloak account is created WITHOUT a credential, the PostgreSQL row
    commits, and only then is the password set. A failed write therefore leaves an unusable orphan
    that ``POST /users`` adopts via the ``keycloak_username_exists_unlinked`` link path — EasySynQ
    never deletes a Keycloak account.

    The Keycloak calls are split across TWO try/except legs, one on each side of the commit — not
    one try spanning lookup through set-password — so ``user``/``subject`` are only ever read on the
    single fall-through path where both are already bound (every other branch raises first, so
    neither name is ever actually unbound where it's used), and so a post-commit
    ``KeycloakUnavailable`` gets its own detail: the account is real and just missing a credential,
    and the fix is to reissue a password, never to retry create (it would only collide on the
    username) and never to delete the Keycloak account.
    """
    username = body.username.strip()
    if not username:
        raise ProblemException(
            status=422, code="validation_error", title="username must not be empty"
        )

    # De-duplicate while preserving order: role_ids: [X, X] must not insert two identical
    # RoleAssignment rows — there is no uniqueness constraint on that table.
    role_ids: list[uuid.UUID] = list(dict.fromkeys(body.role_ids))

    # Roles are a distinct authority with their own key and SoD guard; only demand it when asked
    # for, so a plain create still works for a caller holding only `user.create`. `enforce` is the
    # in-handler equivalent of `require(...)` for a check whose need comes from the request body.
    role_names: dict[uuid.UUID, str] = {}
    if role_ids:
        await enforce(session, sink, request, caller, "permission.grant", ResourceContext.system())
        # Validate BEFORE touching Keycloak: an unknown role id would otherwise reach the INSERT and
        # raise an FK violation as a 500, after an account had already been created in Keycloak.
        role_rows = (
            await session.execute(
                select(Role.id, Role.name).where(
                    Role.org_id == caller.org_id, Role.id.in_(role_ids)
                )
            )
        ).all()
        role_names = {rid: rname for rid, rname in role_rows}
        unknown = [str(r) for r in role_ids if r not in role_names]
        if unknown:
            raise ProblemException(
                status=422,
                code="validation_error",
                title="Unknown role id(s)",
                detail=", ".join(unknown),
            )
        for role_id in role_ids:
            await assert_can_assign_role(session, sink, caller, role_id)

    password = generate_temporary_password(username)

    async with _kc_client() as kc:
        try:
            lookup = await kc.find_user_by_username(username)
            if lookup.found:
                assert lookup.subject is not None  # noqa: S101 — found=True only with a subject
                linked = await session.scalar(
                    select(AppUser.id).where(AppUser.keycloak_subject == lookup.subject)
                )
                if linked is not None:
                    raise ProblemException(
                        status=409,
                        code="user_exists",
                        title="That username already has an EasySynQ user",
                    )
                raise ProblemException(
                    status=409,
                    code="keycloak_username_exists_unlinked",
                    title="A sign-in account with that username already exists",
                    detail="Link the existing account instead of creating a new one.",
                    members={"keycloak_subject": lookup.subject},
                )

            subject = await kc.create_user(
                username=username,
                email=body.email,
                first_name=body.first_name,
                last_name=body.last_name,
            )
        except KeycloakNotConfigured as exc:
            raise ProblemException(
                status=503,
                code="keycloak_not_configured",
                title="Keycloak admin access is not configured on this install",
            ) from exc
        except KeycloakConflict as exc:
            code = "keycloak_email_exists" if exc.field == "email" else "user_exists"
            title = (
                "That email is already used by another sign-in account"
                if exc.field == "email"
                else "That username already exists"
            )
            raise ProblemException(status=409, code=code, title=title) from exc
        except KeycloakUnavailable as exc:
            raise ProblemException(
                status=502, code="keycloak_unavailable", title="Keycloak could not be reached"
            ) from exc

        user = AppUser(
            org_id=caller.org_id,
            keycloak_subject=subject,
            display_name=body.display_name or username,
            email=body.email,
            status=UserStatus.INVITED,
        )
        session.add(user)
        await session.flush()
        # Emitted BEFORE the role-assignment loop so an auditor reading the trail by `occurred_at`
        # sees the user created before any role is granted to them (each event stamps its own
        # timestamp at call time via _emit_user_event/_audit_authz_change) — a role can't precede
        # the user it's granted to. Still the SAME transaction as the loop below: both flush/commit
        # together, never split across the commit below; only the audit-row insertion ORDER changed.
        _emit_user_event(
            session,
            caller,
            EventType.USER_CREATED,
            user.id,
            after={
                "status": UserStatus.INVITED.value,
                "email": body.email,
                "provisioning": "keycloak_created",
            },
        )
        # Each assignment gets its own ROLE_ASSIGN audit row — the established shape from
        # api/authz.py's assign_user_role/_audit_authz_change (object_type=permission, scope_ref
        # names the target user) — so a privilege grant minted here is visible to the same audit
        # queries as one minted via POST /users/{id}/roles. Part of the SAME transaction as
        # USER_CREATED above: both flush/commit together, never split across the commit below.
        for role_id in role_ids:
            assignment = RoleAssignment(org_id=caller.org_id, user_id=user.id, role_id=role_id)
            session.add(assignment)
            await session.flush()  # populate assignment.id for the audit row's object_id
            _audit_authz_change(
                session,
                caller,
                EventType.ROLE_ASSIGN,
                assignment.id,
                user.id,
                after={
                    "role_id": str(role_id),
                    "role_name": role_names.get(role_id),
                    "bound_scope": None,
                },
            )
        await session.commit()

        # Read everything the response needs NOW, before set_temporary_password: both are reads, and
        # running them after the credential goes live would mean a failure here (pool exhaustion,
        # connection reset) 500s the caller while a live, credentialed account sits unreported —
        # exactly the state the ordering property forbids.
        await session.refresh(user)
        names = await _role_names_by_user(session, caller.org_id, [user.id])
        response: dict[str, Any] = {
            "user": _represent(user, names.get(user.id, [])),
            "temporary_password": password,
            "password_delivery": "shown_once",
        }

        # Only now does the account become usable. A failure here leaves a real app_user whose
        # account has no credential — repaired by POST /users/{id}/temporary-password, never by
        # retrying create (which would just collide on the now-existing username) and never by
        # deleting the Keycloak account.
        try:
            await kc.set_temporary_password(subject=subject, password=password)
        except KeycloakUnavailable as exc:
            raise ProblemException(
                status=502,
                code="keycloak_unavailable",
                title="User created but the temporary password could not be set",
                detail=(
                    "The Keycloak account and EasySynQ user were created, but Keycloak could not "
                    "be reached to set the temporary password. Do not retry create — reissue a "
                    "temporary password for this user instead."
                ),
            ) from exc

        # The credential is now live — record that truthfully as its own event, separate from
        # USER_CREATED (whose `after` above no longer claims one exists): audit_event is append-only
        # and hash-chained (R12), so USER_CREATED must never assert this before it's actually true.
        # This commit is deliberately NON-FATAL to the response: the credential is already live in
        # Keycloak and the generated password exists only in `response` at this point, so losing the
        # response to an unhandled 500 here is strictly worse than losing this one audit row. The
        # user's creation and role grants are already durably audited by the FIRST commit above
        # (USER_CREATED + ROLE_ASSIGN); only this credential-issuance event is at risk here, and an
        # audit UNDER-claim is the safe direction for an append-only, hash-chained trail (R12) — the
        # opposite direction (claiming a credential was issued when it was not) is exactly what
        # emitting this event separately from, and after, USER_CREATED was introduced to prevent.
        # Capture the id as a plain `str` now, while `user` is still a live, attached instance:
        # `session.rollback()` below unconditionally expires every loaded ORM instance regardless
        # of `expire_on_commit=False`, and a subsequent attribute access on an expired instance
        # triggers a lazy refresh whose I/O raises `sqlalchemy.exc.MissingGreenlet` on an async
        # session (the S-ing-4 lesson, `.claude/rules/engineering-patterns.md`) — never touch
        # `user.*` after rollback.
        user_id_str = str(user.id)
        try:
            _emit_user_event(
                session,
                caller,
                EventType.USER_CREDENTIAL_ISSUED,
                user.id,
                after={"credential_issued": True},
            )
            await session.commit()
        except Exception:
            await session.rollback()
            logger.warning(
                "users.credential_issued_audit_failed",
                exc_info=True,
                extra={"extra_fields": {"user_id": user_id_str}},
            )

    return response


@router.get("/users/{user_id}")
async def get_user(
    user_id: uuid.UUID,
    caller: AppUser = Depends(_user_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    user = await _get_user(session, user_id, caller.org_id)
    names = await _role_names_by_user(session, caller.org_id, [user.id])
    return _represent(user, names.get(user.id, []))


@router.post("/users", status_code=status.HTTP_201_CREATED)
async def invite_user(
    body: UserInvite,
    caller: AppUser = Depends(_user_create),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Pre-create an INVITED user bound to a Keycloak subject (the operator creates the Keycloak
    account out-of-band; in-app provisioning is v1). Reconciles with JIT on first login. 409 if the
    subject already has an ``app_user`` row."""
    subject = body.keycloak_subject.strip()
    if not subject:
        raise ProblemException(
            status=422, code="validation_error", title="keycloak_subject must not be empty"
        )
    existing = await session.scalar(select(AppUser.id).where(AppUser.keycloak_subject == subject))
    if existing is not None:
        raise ProblemException(
            status=409, code="user_exists", title="A user with that subject already exists"
        )
    user = AppUser(
        org_id=caller.org_id,
        keycloak_subject=subject,
        display_name=body.display_name,
        email=body.email,
        status=UserStatus.INVITED,
    )
    session.add(user)
    await session.flush()  # populate user.id for the audit row
    _emit_user_event(
        session,
        caller,
        EventType.USER_CREATED,
        user.id,
        after={"status": UserStatus.INVITED.value, "email": body.email},
    )
    await session.commit()
    await session.refresh(user)
    return _represent(user, [])


@router.patch("/users/{user_id}")
async def update_user_status(
    user_id: uuid.UUID,
    body: UserStatusUpdate,
    caller: AppUser = Depends(_user_deactivate),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Enable/disable a user. Refuses to disable the **sole active System Administrator** (the
    break-glass principle, doc 08 §9.1) → 409 ``last_admin``."""
    target = await _get_user(session, user_id, caller.org_id)
    new_status = UserStatus(body.status)
    old_status = target.status
    if new_status == old_status:
        names = await _role_names_by_user(session, caller.org_id, [target.id])
        return _represent(target, names.get(target.id, []))

    # Break-glass (doc 08 §9.1): never disable the org's last active System Administrator. The check
    # and the status write run under one org-scoped lock (shared with the role-revocation path via
    # ``disable_removes_last_admin``) so a concurrent revoke+disable can't both win → lockout.
    if new_status == UserStatus.DISABLED and await disable_removes_last_admin(
        session, caller.org_id, target.id
    ):
        raise ProblemException(
            status=409,
            code="last_admin",
            title="Cannot disable the only active System Administrator",
        )

    target.status = new_status
    _emit_user_event(
        session,
        caller,
        EventType.USER_STATUS_CHANGED,
        target.id,
        before={"status": old_status.value},
        after={"status": new_status.value},
    )
    await session.commit()
    # ``get_current_user`` rejects a DISABLED status on the target's next request (403); bump the
    # perm epoch too so any cached effective-permission set for them is dropped immediately.
    await invalidate_user_permissions(target.id)
    await session.refresh(target)
    names = await _role_names_by_user(session, caller.org_id, [target.id])
    return _represent(target, names.get(target.id, []))


@router.post("/users/{user_id}/temporary-password")
async def issue_temporary_password(
    user_id: uuid.UUID,
    caller: AppUser = Depends(_user_create),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Issue a fresh temporary password for an existing linked user (slice S-user-create).

    Two jobs: it repairs a provision that committed the row but failed to set the credential, and it
    removes the last operational reason to run ``scripts/new-keycloak-user.sh``. Gated on
    ``user.create`` — issuing a credential is the same authority as creating the account.
    """
    target = await _get_user(session, user_id, caller.org_id)
    if not target.keycloak_subject:
        raise ProblemException(
            status=409,
            code="user_not_linked",
            title="That user has no linked sign-in account",
        )
    # `app_user` does not store the Keycloak username, so the closest identifier we hold is passed
    # to the policy guard. The generated value is 20 random characters and cannot collide with any
    # username in practice — the check is a belt-and-braces guard, not the primary defence.
    password = generate_temporary_password(target.display_name or "")
    try:
        async with _kc_client() as kc:
            await kc.set_temporary_password(subject=target.keycloak_subject, password=password)
    except KeycloakNotConfigured as exc:
        raise ProblemException(
            status=503,
            code="keycloak_not_configured",
            title="Keycloak admin access is not configured on this install",
        ) from exc
    except KeycloakUnavailable as exc:
        raise ProblemException(
            status=502, code="keycloak_unavailable", title="Keycloak could not be reached"
        ) from exc

    # Build the response before the try block so it can still be returned on failure.
    response: dict[str, Any] = {"temporary_password": password, "password_delivery": "shown_once"}

    # Records THAT a credential was issued — never its value. This commit is deliberately NON-FATAL
    # to the response: the credential is already live in Keycloak and the generated password exists
    # only in `response` at this point, so losing the response to an unhandled 500 here is strictly
    # worse than losing this one audit row. The credential is already live, so an audit UNDER-claim
    # is the safe direction for an append-only, hash-chained trail (R12).
    # Capture the id as a plain `str` now, while `target` is still a live, attached instance:
    # `session.rollback()` below unconditionally expires every loaded ORM instance regardless of
    # `expire_on_commit=False`, and a subsequent attribute access on an expired instance triggers
    # a lazy refresh whose I/O raises `sqlalchemy.exc.MissingGreenlet` on an async session (the
    # S-ing-4 lesson, `.claude/rules/engineering-patterns.md`) — never touch `target.*` after
    # rollback.
    target_id_str = str(target.id)
    try:
        _emit_user_event(
            session,
            caller,
            EventType.USER_CREDENTIAL_ISSUED,
            target.id,
            after={"credential_issued": True},
        )
        await session.commit()
    except Exception:
        await session.rollback()
        logger.warning(
            "users.credential_issued_audit_failed",
            exc_info=True,
            extra={"extra_fields": {"user_id": target_id_str}},
        )

    return response
