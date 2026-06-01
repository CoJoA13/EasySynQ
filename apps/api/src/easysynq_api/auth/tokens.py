"""Access-token validation: verify a Keycloak RS256 JWT against the JWKS.

EasySynQ is an OAuth2 resource server — it issues no tokens and reads permissions
server-side, never from the token (those land in S2). Here we only establish *who*
the caller is: signature, issuer, audience, expiry, and a usable ``sub``.
"""

from __future__ import annotations

from typing import Any

import jwt

from ..config import get_settings
from ..problems import ProblemException
from .jwks import JWKSCache


def _unauthorized(code: str, title: str, detail: str | None = None) -> ProblemException:
    return ProblemException(status=401, code=code, title=title, detail=detail)


async def authenticate(token: str, jwks: JWKSCache) -> dict[str, Any]:
    settings = get_settings()
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise _unauthorized("token_invalid", "Malformed token", str(exc)) from exc

    kid = header.get("kid")
    if not kid:
        raise _unauthorized("token_invalid", "Token header has no kid")

    key = await jwks.get_public_key(kid)
    if key is None:
        raise _unauthorized("token_invalid", "Unknown signing key")

    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=settings.oidc_audience,
            issuer=settings.oidc_issuer,
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise _unauthorized("token_expired", "Access token expired") from exc
    except jwt.PyJWTError as exc:
        raise _unauthorized("token_invalid", "Token validation failed", str(exc)) from exc
    return claims
