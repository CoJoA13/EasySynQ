"""S-pack-2 unit proofs — the Ed25519 evidence-pack share token (doc 06 §7.4).

Mint/verify round-trip, determinism, unforgeability, **fail-closed minting**, and — the load-bearing
one — **domain separation** from the S7c verify token even when they share the same signing key.
"""

from __future__ import annotations

import hashlib
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from easysynq_api.services.packs import share_token as st
from easysynq_api.services.vault import verify_token as vt


def _args() -> tuple[uuid.UUID, uuid.UUID, int]:
    return uuid.uuid4(), uuid.uuid4(), 1_900_000_000


def test_mint_verify_round_trip() -> None:
    key = Ed25519PrivateKey.generate()
    pid, lid, exp = _args()
    claims = st.verify(st.mint(pid, lid, exp, key=key), key=key)
    assert claims is not None
    assert claims.pack_id == pid
    assert claims.share_link_id == lid
    assert claims.expires_at_epoch == exp


def test_token_is_deterministic_and_105_bytes() -> None:
    key = Ed25519PrivateKey.generate()
    pid, lid, exp = _args()
    a = st.mint(pid, lid, exp, key=key)
    assert a == st.mint(pid, lid, exp, key=key)
    # 105 raw bytes (1+16+16+8+64) → distinct length from the verify token's 128 (domain sep).
    import base64

    assert len(base64.urlsafe_b64decode(a + "=" * (-len(a) % 4))) == 105


def test_wrong_key_and_tamper_rejected() -> None:
    key = Ed25519PrivateKey.generate()
    pid, lid, exp = _args()
    token = st.mint(pid, lid, exp, key=key)
    assert st.verify(token, key=Ed25519PrivateKey.generate()) is None
    flipped = token[:-3] + ("AAA" if token[-3:] != "AAA" else "BBB")
    assert st.verify(flipped, key=key) is None
    assert st.verify("not-a-token", key=key) is None
    assert st.verify("", key=key) is None


def test_domain_separation_same_key_cannot_cross_verify() -> None:
    """The decisive proof: with the SAME signing key, a verify token never validates as a share
    token and vice versa — the distinct length + the ``easysynq.packshare.v1`` preamble keep the
    domains disjoint, so possessing one token type can never forge the other."""
    key = Ed25519PrivateKey.generate()
    pid, lid, exp = _args()
    did, vid, dig = uuid.uuid4(), uuid.uuid4(), hashlib.sha256(b"c").hexdigest()

    share = st.mint(pid, lid, exp, key=key)
    verify_tok = vt.mint(did, vid, dig, key=key)

    assert vt.verify(share, key=key) is None  # a share token is NOT a valid verify token
    assert st.verify(verify_tok, key=key) is None  # a verify token is NOT a valid share token


def test_mint_fails_closed_without_a_persisted_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The API path (no explicit key) refuses to mint an unverifiable, ephemeral-key share link."""
    # load returns an in-memory key but it is NOT durably persisted (read-only path) → fail closed.
    monkeypatch.setattr(
        st.verify_token, "load_verify_signing_key", lambda: Ed25519PrivateKey.generate()
    )
    monkeypatch.setattr(st.verify_token, "signing_key_is_persisted", lambda: False)
    pid, lid, exp = _args()
    with pytest.raises(st.SigningKeyUnavailable):
        st.mint(pid, lid, exp)


def test_verify_returns_none_when_no_key_persisted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(st.verify_token, "_read_key", lambda: None)
    key = Ed25519PrivateKey.generate()
    pid, lid, exp = _args()
    token = st.mint(pid, lid, exp, key=key)
    assert st.verify(token) is None  # no key on the verifier → UNKNOWN
