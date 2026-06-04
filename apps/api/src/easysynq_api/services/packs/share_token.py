"""The evidence-pack delivery token (slice S-pack-2, doc 06 §7.4, UJ-7).

A compact, **time-boxed**, Ed25519-signed bearer token an external auditor (Olsen) carries in a
share-link URL to fetch a sealed pack — without an account — through the public
``/api/v1/evidence-packs/shared`` endpoints (outside the PEP, the S7c ``/verify`` precedent). The
token is the bearer credential; the authoritative, **revocable** state lives in the
``pack_share_link`` row the public endpoint consults.

It reuses the S7c verify-token Ed25519 key (``verify_token.load_verify_signing_key``/``_read_key``)
but is **domain-separated** so a verify token can't be replayed as a share token (or the reverse):

* the signed message is prefixed with a distinct ``PREAMBLE`` (``easysynq.packshare.v1``), and
* the payload starts with a ``VERSION`` byte + has a **different length** (105 vs the verify's 128),

so cross-decoding fails on the length check, and even a same-length forgery fails the signature (the
preamble makes the signed messages disjoint). Both directions are locked by unit tests.

Layout: ``token = base64url( VERSION(1) | pack_id(16) | link_id(16) | exp_epoch(8 BE) | sig(64) )``;
the Ed25519 signature is over ``PREAMBLE + VERSION | pack_id | link_id | exp_epoch``.

``mint`` **fails closed** (raises) if the signing key is not durably persisted — an ephemeral-key
token would be unverifiable after a restart, and these links go to outsiders. The API both mints (at
``POST …/share``) and verifies (the public endpoint) share tokens, so there is no cross-process key
divergence on this path.
"""

from __future__ import annotations

import base64
import binascii
import dataclasses
import struct
import uuid

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ..vault import verify_token

PREAMBLE = b"easysynq.packshare.v1"
_VERSION = 0x01
_PAYLOAD_LEN = 1 + 16 + 16 + 8  # version + pack_id + share_link_id + exp_epoch
_SIG_LEN = 64
_TOKEN_LEN = _PAYLOAD_LEN + _SIG_LEN  # 105 bytes (vs the verify token's 128 — domain separation)


class SigningKeyUnavailable(RuntimeError):
    """The Ed25519 signing key is not durably persisted — minting a share link would yield an
    unverifiable token. The API maps this to 503 (the operator must provision the key)."""


@dataclasses.dataclass(frozen=True, slots=True)
class ShareClaims:
    pack_id: uuid.UUID
    share_link_id: uuid.UUID
    expires_at_epoch: int


def _payload(pack_id: uuid.UUID, share_link_id: uuid.UUID, exp_epoch: int) -> bytes:
    return bytes([_VERSION]) + pack_id.bytes + share_link_id.bytes + struct.pack(">Q", exp_epoch)


def mint(
    pack_id: uuid.UUID,
    share_link_id: uuid.UUID,
    expires_at_epoch: int,
    *,
    key: Ed25519PrivateKey | None = None,
) -> str:
    """Sign a share token. With no ``key`` (the API path) it **fails closed**
    (``SigningKeyUnavailable``) unless the verify-token key is persisted to disk — never an
    ephemeral fallback key (an outsider's link must survive a restart). ``key`` is a test seam."""
    if key is None:
        key = verify_token.load_verify_signing_key()
        if not verify_token.signing_key_is_persisted():
            raise SigningKeyUnavailable(
                "share-link signing key is not durably persisted; provision the verify-token key"
            )
    payload = _payload(pack_id, share_link_id, expires_at_epoch)
    signature = key.sign(PREAMBLE + payload)
    return base64.urlsafe_b64encode(payload + signature).decode().rstrip("=")


def verify(token: str, *, key: Ed25519PrivateKey | None = None) -> ShareClaims | None:
    """Decode + Ed25519-verify a share token (signature only — NOT expiry/revocation, which the DB
    row owns). ``None`` on any failure (bad base64, wrong length, wrong version, forged/tampered
    sig, or no persisted key). Domain-separated from the verify token by length + preamble."""
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except (binascii.Error, ValueError):
        return None
    if len(raw) != _TOKEN_LEN or raw[0] != _VERSION:
        return None
    payload, signature = raw[:_PAYLOAD_LEN], raw[_PAYLOAD_LEN:]
    if key is None:
        key = verify_token._read_key()
    if key is None:
        return None
    try:
        key.public_key().verify(signature, PREAMBLE + payload)
    except InvalidSignature:
        return None
    return ShareClaims(
        pack_id=uuid.UUID(bytes=payload[1:17]),
        share_link_id=uuid.UUID(bytes=payload[17:33]),
        expires_at_epoch=struct.unpack(">Q", payload[33:41])[0],
    )
