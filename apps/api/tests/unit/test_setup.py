"""S8a unit proofs — the bootstrap-secret crypto + the setup event_type values (no DB).

The DB-bound flow (latch, gates, bootstrap-grant, finalize) is proven in
``tests/integration/test_setup.py``; here we pin the pure, security-relevant crypto and the enum
guard (a missing Python EventType member is a runtime crash, not a CI failure — see 0011/0012).
"""

from __future__ import annotations

from easysynq_api.db.models._audit_enums import EVENT_TYPE_VALUES, EventType
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
    + S8b's WORM_VERIFIED."""
    for label in (
        "BOOTSTRAP_CONSUMED",
        "ADMIN_BOOTSTRAPPED",
        "ORG_PROFILE_SET",
        "SETUP_FINALIZED",
        "WORM_VERIFIED",
    ):
        assert EventType(label).value == label
        assert label in EVENT_TYPE_VALUES
