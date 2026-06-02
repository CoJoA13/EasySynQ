"""S7c unit proofs — the Ed25519 verify token (mint/verify, deterministic, unforgeable)."""

from __future__ import annotations

import hashlib
import uuid

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from easysynq_api.services.vault import verify_token as vt


def _claims() -> tuple[uuid.UUID, uuid.UUID, str]:
    return uuid.uuid4(), uuid.uuid4(), hashlib.sha256(b"content").hexdigest()


def test_mint_verify_round_trip() -> None:
    key = Ed25519PrivateKey.generate()
    did, vid, dig = _claims()
    claims = vt.verify(vt.mint(did, vid, dig, key=key), key=key)
    assert claims is not None
    assert claims.document_id == did
    assert claims.version_id == vid
    assert claims.content_digest == dig


def test_token_is_deterministic() -> None:
    key = Ed25519PrivateKey.generate()
    did, vid, dig = _claims()
    assert vt.mint(did, vid, dig, key=key) == vt.mint(did, vid, dig, key=key)


def test_wrong_key_rejected() -> None:
    did, vid, dig = _claims()
    token = vt.mint(did, vid, dig, key=Ed25519PrivateKey.generate())
    assert vt.verify(token, key=Ed25519PrivateKey.generate()) is None


def test_tampered_token_rejected() -> None:
    key = Ed25519PrivateKey.generate()
    did, vid, dig = _claims()
    token = vt.mint(did, vid, dig, key=key)
    flipped = token[:-3] + ("AAA" if token[-3:] != "AAA" else "BBB")
    assert vt.verify(flipped, key=key) is None


def test_garbage_rejected() -> None:
    key = Ed25519PrivateKey.generate()
    assert vt.verify("not-a-real-token", key=key) is None
    assert vt.verify("", key=key) is None
    assert vt.verify("####", key=key) is None
