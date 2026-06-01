"""JWKS fetch + cache for validating Keycloak-signed access tokens.

A ``JWKSCache`` resolves a token's ``kid`` to its RSA public key. It can be seeded
with a static keyset (tests/offline) instead of fetching, so token validation is
unit-testable without a network or a running Keycloak.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from jwt.algorithms import RSAAlgorithm

from ..config import get_settings


class JWKSCache:
    def __init__(self, jwks_url: str, static_jwks: dict[str, Any] | None = None) -> None:
        self._jwks_url = jwks_url
        self._static = static_jwks
        self._keys: dict[str, Any] = {}

    async def _load(self) -> dict[str, Any]:
        if self._static is not None:
            return self._static
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(self._jwks_url)
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            return data

    async def get_public_key(self, kid: str) -> Any:
        """Return the RSA public key for ``kid``, or None if unknown."""
        if kid not in self._keys:
            for jwk in (await self._load()).get("keys", []):
                jwk_kid = jwk.get("kid")
                if jwk_kid:
                    self._keys[jwk_kid] = RSAAlgorithm.from_jwk(json.dumps(jwk))
        return self._keys.get(kid)


_cache: JWKSCache | None = None


def get_jwks_cache() -> JWKSCache:
    """FastAPI dependency — a process-wide cache. Overridable in tests."""
    global _cache
    if _cache is None:
        _cache = JWKSCache(get_settings().oidc_jwks_url)
    return _cache
