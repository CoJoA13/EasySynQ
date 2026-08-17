"""S8a unit proofs — the bootstrap-secret crypto + the setup event_type values (no DB).

The DB-bound flow (latch, gates, bootstrap-grant, finalize) is proven in
``tests/integration/test_setup.py``; here we pin the pure, security-relevant crypto and the enum
guard (a missing Python EventType member is a runtime crash, not a CI failure — see 0011/0012).
"""

from __future__ import annotations

import pytest

from easysynq_api.db.models._audit_enums import EVENT_TYPE_VALUES, EventType
from easysynq_api.problems import ProblemException
from easysynq_api.services.setup import service as setup_service
from easysynq_api.services.setup.bootstrap import mint_secret, verify_secret


def test_mint_verify_roundtrip() -> None:
    """A freshly minted secret verifies against its stored salted hash."""
    secret, stored = mint_secret()
    assert ":" in stored  # <salt_hex>:<sha256_hex>
    assert secret not in stored  # the plaintext is never embedded in the hash
    assert verify_secret(secret, stored) is True


def test_verify_rejects_wrong_secret() -> None:
    _, stored = mint_secret()
    assert verify_secret("not-the-secret", stored) is False


def test_verify_rejects_malformed_or_absent_hash() -> None:
    """A None / empty / unparseable stored hash never verifies and never raises."""
    secret, _ = mint_secret()
    assert verify_secret(secret, None) is False
    assert verify_secret(secret, "") is False
    assert verify_secret(secret, "no-colon") is False
    assert verify_secret(secret, "zz:not-hex-salt") is False


def test_each_mint_is_unique() -> None:
    """Distinct mints yield distinct secrets + distinct (salted) hashes — no fixed salt."""
    a_secret, a_hash = mint_secret()
    b_secret, b_hash = mint_secret()
    assert a_secret != b_secret
    assert a_hash != b_hash
    assert verify_secret(a_secret, b_hash) is False


def test_setup_event_types_resolve() -> None:
    """The setup labels resolve to Python members AND are in the tuple the migration rebuilds the
    PG type from (a missing member would crash DbVaultAuditSink at write time, not CI). S8a's four
    + S8b's WORM_VERIFIED + the durable first-administrator claim event."""
    for label in (
        "BOOTSTRAP_CONSUMED",
        "ADMIN_BOOTSTRAPPED",
        "ORG_PROFILE_SET",
        "SETUP_FINALIZED",
        "WORM_VERIFIED",
        "BOOTSTRAP_IDENTITY_CLAIMED",
    ):
        assert EventType(label).value == label
        assert label in EVENT_TYPE_VALUES


class _AtomicFailureRedis:
    def __init__(self) -> None:
        self.eval_calls: list[tuple[str, int, tuple[str, ...]]] = []

    async def __aenter__(self) -> _AtomicFailureRedis:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def eval(self, script: str, numkeys: int, *args: str) -> int:
        self.eval_calls.append((script, numkeys, args))
        return 2

    async def incr(self, _key: str) -> int:
        raise AssertionError("failure recording must not expose a split INCR/EXPIRE window")

    async def expire(self, _key: str, _ttl: int) -> bool:
        raise RuntimeError("injected expiry failure")


class _RateLimitRedis:
    def __init__(self, stored_counter: str | None) -> None:
        self.stored_counter = stored_counter

    async def __aenter__(self) -> _RateLimitRedis:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def eval(self, _script: str, _numkeys: int, _key: str, _window: str) -> str | None:
        return self.stored_counter


async def test_failure_counter_uses_one_atomic_redis_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _AtomicFailureRedis()
    monkeypatch.setattr(setup_service, "_redis", lambda: client)

    await setup_service._record_failure()

    assert len(client.eval_calls) == 1
    script, numkeys, args = client.eval_calls[0]
    assert "SET" in script
    assert "EX" in script
    assert numkeys == 1
    assert args == (setup_service._RL_KEY, str(setup_service._RL_WINDOW_SECONDS))


async def test_rate_limit_rejects_negative_counter_as_dependency_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(setup_service, "_redis", lambda: _RateLimitRedis("-1"))

    with pytest.raises(ProblemException) as excinfo:
        await setup_service._check_rate_limit()

    assert excinfo.value.status == 503
    assert excinfo.value.code == "dependency_unavailable"
    assert excinfo.value.title == "Bootstrap rate limiting is unavailable"
