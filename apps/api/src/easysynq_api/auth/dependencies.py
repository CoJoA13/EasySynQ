"""The ``get_current_user`` FastAPI dependency.

Validates the bearer token, then resolves the Keycloak ``sub`` to an ``app_user``
row — JIT-provisioning one (into the single org) on first sight. Rejects inactive
accounts and tokens issued before a ``session_invalidated_at`` watermark, so a
revocation/lock takes effect on the next request rather than at token expiry.
"""

from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.app_user import AppUser, UserStatus
from ..db.models.organization import Organization
from ..db.session import get_session
from ..problems import ProblemException
from .jwks import JWKSCache, get_jwks_cache
from .tokens import authenticate

_INACTIVE = {UserStatus.LOCKED, UserStatus.DISABLED, UserStatus.RETIRED}


def _bearer(request: Request) -> str:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ProblemException(status=401, code="unauthenticated", title="Missing bearer token")
    return token


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
        user = AppUser(
            org_id=org_id,
            keycloak_subject=sub,
            display_name=claims.get("name") or claims.get("preferred_username") or sub,
            email=claims.get("email"),
            status=UserStatus.ACTIVE,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
    elif user.status == UserStatus.INVITED:
        # An admin-invited user (S8d): the pre-created INVITED row reconciles to a real ACTIVE
        # account on the subject's first genuine login. One-time write (only while INVITED).
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
    return await resolve_current_user(request, jwks, session)
