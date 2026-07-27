"""Bounded JWKS caching: rotation, miss throttling, and fetch-failure backoff."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from easysynq_api.auth.jwks import JWKSCache, JWKSUnavailable


@dataclass
class _Clock:
    now: float = 0.0

    def __call__(self) -> float:
        return self.now


def _jwk(kid: str) -> dict[str, object]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk: dict[str, object] = json.loads(RSAAlgorithm.to_jwk(key.public_key()))
    jwk["kid"] = kid
    jwk["alg"] = "RS256"
    return jwk


@pytest.mark.unit
async def test_expired_refresh_evicts_rotated_out_keys() -> None:
    clock = _Clock()
    document = {"keys": [_jwk("old")]}
    cache = JWKSCache(
        "",
        static_jwks=document,
        ttl_seconds=10,
        refresh_cooldown_seconds=2,
        clock=clock,
    )

    assert await cache.get_public_key("old") is not None
    document["keys"] = [_jwk("new")]

    clock.now = 9
    assert await cache.get_public_key("old") is not None

    clock.now = 11
    assert await cache.get_public_key("old") is None
    assert await cache.get_public_key("new") is not None


@pytest.mark.unit
async def test_unknown_kids_share_one_bounded_refresh_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    cache = JWKSCache(
        "https://kc.test/jwks",
        ttl_seconds=60,
        refresh_cooldown_seconds=5,
        clock=clock,
    )
    loads = 0

    async def _load() -> dict[str, object]:
        nonlocal loads
        loads += 1
        return {"keys": []}

    monkeypatch.setattr(cache, "_load", _load)

    assert await cache.get_public_key("unknown-a") is None
    clock.now = 1
    assert await cache.get_public_key("unknown-b") is None
    assert loads == 1

    clock.now = 6
    assert await cache.get_public_key("unknown-b") is None
    assert loads == 2


@pytest.mark.unit
async def test_concurrent_cold_misses_single_flight_the_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = JWKSCache("https://kc.test/jwks")
    started = asyncio.Event()
    release = asyncio.Event()
    loads = 0

    async def _load() -> dict[str, object]:
        nonlocal loads
        loads += 1
        started.set()
        await release.wait()
        return {"keys": []}

    monkeypatch.setattr(cache, "_load", _load)

    first = asyncio.create_task(cache.get_public_key("unknown"))
    second = asyncio.create_task(cache.get_public_key("unknown"))
    await started.wait()
    release.set()

    assert await asyncio.gather(first, second) == [None, None]
    assert loads == 1


@pytest.mark.unit
async def test_failed_refresh_is_backed_off(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _Clock()
    cache = JWKSCache(
        "https://kc.test/jwks",
        refresh_cooldown_seconds=5,
        clock=clock,
    )
    loads = 0

    async def _load() -> dict[str, object]:
        nonlocal loads
        loads += 1
        raise OSError("temporary network failure")

    monkeypatch.setattr(cache, "_load", _load)

    with pytest.raises(JWKSUnavailable):
        await cache.get_public_key("unknown")
    clock.now = 1
    with pytest.raises(JWKSUnavailable):
        await cache.get_public_key("unknown")
    assert loads == 1

    clock.now = 6
    with pytest.raises(JWKSUnavailable):
        await cache.get_public_key("unknown")
    assert loads == 2


@pytest.mark.unit
async def test_failed_unknown_kid_refresh_does_not_poison_a_cached_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    document = {"keys": [_jwk("known")]}
    cache = JWKSCache(
        "",
        static_jwks=document,
        ttl_seconds=60,
        refresh_cooldown_seconds=5,
        clock=clock,
    )
    known = await cache.get_public_key("known")
    clock.now = 6

    async def _load() -> dict[str, object]:
        raise OSError("temporary network failure")

    monkeypatch.setattr(cache, "_load", _load)

    with pytest.raises(JWKSUnavailable):
        await cache.get_public_key("unknown")
    assert await cache.get_public_key("known") is known
