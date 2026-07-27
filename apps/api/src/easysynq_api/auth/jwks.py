"""JWKS fetch + cache for validating Keycloak-signed access tokens.

A ``JWKSCache`` resolves a token's ``kid`` to its RSA public key. It can be seeded
with a static keyset (tests/offline) instead of fetching, so token validation is
unit-testable without a network or a running Keycloak.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from typing import Any

import httpx
from jwt.algorithms import RSAAlgorithm

from ..config import get_settings


class JWKSUnavailable(RuntimeError):
    """The issuer's signing-key set could not be refreshed safely."""


class JWKSCache:
    def __init__(
        self,
        jwks_url: str,
        static_jwks: dict[str, Any] | None = None,
        *,
        ttl_seconds: float = 300.0,
        refresh_cooldown_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._jwks_url = jwks_url
        self._static = static_jwks
        self._ttl_seconds = ttl_seconds
        self._refresh_cooldown_seconds = refresh_cooldown_seconds
        self._clock = clock
        self._keys: dict[str, Any] = {}
        self._expires_at = 0.0
        self._refresh_not_before = 0.0
        self._unavailable_until = 0.0
        self._refresh_lock = asyncio.Lock()

    async def _load(self) -> dict[str, Any]:
        if self._static is not None:
            return self._static
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(self._jwks_url)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict):
                raise ValueError("JWKS document must be an object")
            return data

    async def _refresh(self, now: float) -> None:
        # Set the retry floor on both success and failure. Unknown/random kids can otherwise make
        # every request fetch the issuer, and an outage can become a request-amplification loop.
        self._refresh_not_before = now + self._refresh_cooldown_seconds
        try:
            document = await self._load()
            raw_keys = document.get("keys")
            if not isinstance(raw_keys, list):
                raise ValueError("JWKS document has no keys array")
            keys: dict[str, Any] = {}
            for jwk in raw_keys:
                if not isinstance(jwk, dict):
                    raise ValueError("JWKS key must be an object")
                kid = jwk.get("kid")
                if isinstance(kid, str) and kid:
                    keys[kid] = RSAAlgorithm.from_jwk(json.dumps(jwk))
        except Exception as exc:  # External dependency boundary: normalize and fail closed.
            self._unavailable_until = self._refresh_not_before
            raise JWKSUnavailable("Identity-provider signing keys are unavailable") from exc

        # Replace rather than merge: once the bounded TTL elapses, rotated-out keys must stop being
        # trusted. A successful empty keyset is valid but cannot authenticate any token.
        self._keys = keys
        self._expires_at = now + self._ttl_seconds
        self._unavailable_until = 0.0

    async def get_public_key(self, kid: str) -> Any:
        """Return the RSA public key for ``kid``, or None if unknown."""
        now = self._clock()
        key = self._keys.get(kid)
        if key is not None and now < self._expires_at:
            # An opportunistic refresh for a previously-unknown kid may fail while this bounded
            # cache is still usable. Do not let random-kid traffic poison known, unexpired keys.
            return key
        if now < self._unavailable_until:
            raise JWKSUnavailable("Identity-provider signing keys are unavailable")

        cache_expired = now >= self._expires_at
        unknown_refresh_due = key is None and now >= self._refresh_not_before
        if not (cache_expired or unknown_refresh_due):
            return key

        # A process-wide single-flight prevents a cold cache or a new kid from producing one issuer
        # request per concurrent API request. Re-check every predicate after acquiring the lock.
        async with self._refresh_lock:
            now = self._clock()
            key = self._keys.get(kid)
            if key is not None and now < self._expires_at:
                return key
            if now < self._unavailable_until:
                raise JWKSUnavailable("Identity-provider signing keys are unavailable")
            cache_expired = now >= self._expires_at
            unknown_refresh_due = key is None and now >= self._refresh_not_before
            if cache_expired or unknown_refresh_due:
                await self._refresh(now)
        return self._keys.get(kid)


_cache: JWKSCache | None = None


def get_jwks_cache() -> JWKSCache:
    """FastAPI dependency — a process-wide cache. Overridable in tests."""
    global _cache
    if _cache is None:
        _cache = JWKSCache(get_settings().oidc_jwks_url)
    return _cache
