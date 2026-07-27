"""The ``get_current_user`` FastAPI dependency.

Validates the bearer token, then resolves the Keycloak ``sub`` to an ``app_user``
row — JIT-provisioning one (into the single org) on first sight. Rejects inactive
accounts and tokens issued before a ``session_invalidated_at`` watermark, so a
revocation/lock takes effect on the next request rather than at token expiry.
"""

from __future__ import annotations

import uuid

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.app_user import AppUser, UserStatus
from ..db.models.organization import Organization
from ..db.session import get_session
from ..problems import ProblemException
from ..services.common.org_clock import resolve_org_tz, set_request_org_tz
from .jwks import JWKSCache, get_jwks_cache
from .tokens import authenticate

_INACTIVE = {UserStatus.LOCKED, UserStatus.DISABLED, UserStatus.RETIRED}


def _bearer(request: Request) -> str:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ProblemException(status=401, code="unauthenticated", title="Missing bearer token")
    return token


async def _jit_provision_user(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    subject: str,
    display_name: str | None,
    email: str | None,
) -> AppUser:
    """Create the first-login identity once, converging concurrent requests on the winner."""
    candidate_id = uuid.uuid4()
    inserted_id = (
        await session.execute(
            pg_insert(AppUser)
            .values(
                id=candidate_id,
                org_id=org_id,
                keycloak_subject=subject,
                display_name=display_name,
                email=email,
                status=UserStatus.ACTIVE,
                mfa_enrolled=False,
                is_guest=False,
            )
            .on_conflict_do_nothing(constraint="uq_app_user_keycloak_subject")
            .returning(AppUser.id)
        )
    ).scalar_one_or_none()
    await session.commit()

    # PostgreSQL waits for the competing transaction before deciding the conflict, so the
    # winning row is committed and visible by the time a losing INSERT reaches this query.
    user = await session.get(AppUser, inserted_id) if inserted_id is not None else None
    if user is None:
        user = (
            await session.execute(select(AppUser).where(AppUser.keycloak_subject == subject))
        ).scalar_one()
    return user


async def resolve_current_user(
    request: Request,
    jwks: JWKSCache,
    session: AsyncSession,
) -> AppUser:
    """Validate the bearer, resolve/JIT-provision the AppUser, enforce active + revocation.

    Extracted from get_current_user so a streaming endpoint can authenticate with a short-lived
    session (closed BEFORE the StreamingResponse body iterates) — S-notify-5c.
    """
    claims = await authenticate(_bearer(request), jwks)
    sub = str(claims["sub"])

    user = (
        await session.execute(select(AppUser).where(AppUser.keycloak_subject == sub))
    ).scalar_one_or_none()

    if user is None:
        org_id = (
            await session.execute(
                select(Organization.id).order_by(Organization.created_at).limit(1)
            )
        ).scalar_one_or_none()
        if org_id is None:
            raise ProblemException(
                status=403, code="setup_incomplete", title="No organization configured"
            )
        user = await _jit_provision_user(
            session,
            org_id=org_id,
            subject=sub,
            display_name=claims.get("name") or claims.get("preferred_username") or sub,
            email=claims.get("email"),
        )

    if user.status == UserStatus.INVITED:
        # An admin-invited user (S8d): the pre-created INVITED row reconciles to a real ACTIVE
        # account on the subject's first genuine login. This also handles an invitation racing the
        # JIT INSERT: ON CONFLICT returns the pre-created row and this request activates it.
        user.status = UserStatus.ACTIVE
        await session.commit()
        await session.refresh(user)

    if user.status in _INACTIVE:
        raise ProblemException(status=403, code="permission_denied", title="Account is not active")

    invalidated = user.session_invalidated_at
    iat = claims.get("iat")
    if invalidated is not None and iat is not None and float(iat) < invalidated.timestamp():
        raise ProblemException(status=401, code="token_invalid", title="Session was invalidated")

    return user


async def get_current_user(
    request: Request,
    jwks: JWKSCache = Depends(get_jwks_cache),
    session: AsyncSession = Depends(get_session),
) -> AppUser:
    user = await resolve_current_user(request, jwks, session)
    # S-orgtz-unify: pin the caller's canonical org tz for this request task so today_org() /
    # _document review_state / _fmt_date judge dates in the org's frame. Isolated to this request
    # (its context copy is discarded at task end) — no reset needed.
    set_request_org_tz(await resolve_org_tz(session, user.org_id))
    return user
