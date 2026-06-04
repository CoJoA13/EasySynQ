"""The controlled-rendition verify token (slice S7c, doc 05 §6.4).

A compact, Ed25519-signed token embedded (as a QR + plaintext) in every controlled-copy footer. It
encodes ``{document_id, version_id, content_digest}`` so anyone holding a printout/export can hit
the public ``/verify`` page and learn whether that exact revision is still **CURRENT**, has been
**SUPERSEDED**, or is **UNKNOWN** — the drift-detection backstop for copies that left the building
(R11 boundary).

This is an INTEGRITY / currency token, **NOT** a Part-11 e-signature (it signs a currency claim, not
an approval) — D3's ``signature_event`` path stays reserved. The signing key is a dedicated
dev-grade Ed25519 PEM (custody separate from the audit-checkpoint key), generated + persisted on
first use, mirroring ``services/audit/checkpoint.py``. Ed25519 is deterministic, so a given (key,
claims) always mints the same token → the QR/footer/rendition stay byte-reproducible (S7b).
"""

from __future__ import annotations

import base64
import binascii
import dataclasses
import logging
import os
import uuid
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    load_pem_private_key,
)

from ...config import get_settings

logger = logging.getLogger("easysynq.vault")

_PREFIX_LEN = 16 + 16 + 32  # document_id + version_id + content_digest(sha256)
_SIG_LEN = 64  # Ed25519 signature


@dataclasses.dataclass(frozen=True, slots=True)
class VerifyClaims:
    document_id: uuid.UUID
    version_id: uuid.UUID
    content_digest: str  # lowercase hex sha256 (the version's immutable source bytes)


_cached_key: Ed25519PrivateKey | None = None
# True once :func:`load_verify_signing_key` had to fall back to an in-memory ephemeral key (a
# read-only key path) — i.e. the active key is NOT durably persisted. S-pack-2's share-link mint
# reads this (``signing_key_is_persisted``) to fail closed rather than hand an outsider a token that
# would stop verifying after a restart.
_ephemeral_fallback: bool = False


def signing_key_is_persisted() -> bool:
    """``False`` only when the active signing key is a non-persisted ephemeral fallback (a read-only
    key path). Meaningful after a load (mint paths call ``load_verify_signing_key`` first)."""
    return not _ephemeral_fallback


def _read_key() -> Ed25519PrivateKey | None:
    """Read the persisted key (None if absent). Memoized once present; the absent case is NOT
    cached, so a later read picks up the key the minter generates. Used by the **verifier** (api) —
    it never generates, so it can't create a key that diverges from the minter's (no key race)."""
    global _cached_key
    if _cached_key is not None:
        return _cached_key
    path = Path(get_settings().verify_token_signing_key_path)
    if not path.exists():
        return None
    loaded = load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(loaded, Ed25519PrivateKey):  # pragma: no cover - defensive
        raise TypeError("verify-token signing key is not an Ed25519 private key")
    _cached_key = loaded
    return loaded


def load_verify_signing_key() -> Ed25519PrivateKey:
    """Return the signing key, generating + atomically persisting a dev key if absent. Only the
    **minter** (the worker Beat task + the `easysynq mirror` CLI) calls this, and the mirror lock
    serializes minters — so generation has a single owner and there is no key race; the api verifier
    uses :func:`_read_key` (read-only)."""
    global _cached_key, _ephemeral_fallback
    key = _read_key()
    if key is not None:
        return key
    key = Ed25519PrivateKey.generate()
    path = Path(get_settings().verify_token_signing_key_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
        tmp.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
        os.replace(tmp, path)  # atomic publish — a concurrent winner's file wins for everyone
        _ephemeral_fallback = False  # durably persisted
        return _read_key() or key
    except OSError:  # pragma: no cover - read-only mount fallback
        logger.warning(
            "verify-token key path not writable; using an ephemeral dev key "
            "(cross-process verifies will read UNKNOWN until a persisted key is available)"
        )
        _cached_key = key
        _ephemeral_fallback = True
        return key


def mint(
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    content_digest: str,
    *,
    key: Ed25519PrivateKey | None = None,
) -> str:
    """A URL-safe base64 token = base64url(doc_id[16] ‖ version_id[16] ‖ digest[32] ‖ sig[64]),
    the signature over the 64-byte claims prefix. Deterministic for a fixed (key, claims)."""
    digest_bytes = bytes.fromhex(content_digest)
    if len(digest_bytes) != 32:  # pragma: no cover - guards a malformed source digest at build time
        raise ValueError("content_digest must be a 32-byte (sha256) hex string")
    prefix = document_id.bytes + version_id.bytes + digest_bytes
    signing_key = key or load_verify_signing_key()
    signature = signing_key.sign(prefix)
    return base64.urlsafe_b64encode(prefix + signature).decode().rstrip("=")


def verify(token: str, *, key: Ed25519PrivateKey | None = None) -> VerifyClaims | None:
    """Decode + Ed25519-verify a token; ``None`` on any failure (bad base64, wrong length, forged or
    tampered signature) — the /verify endpoint maps ``None`` → UNKNOWN."""
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except (binascii.Error, ValueError):
        return None
    if len(raw) != _PREFIX_LEN + _SIG_LEN:
        return None
    prefix, signature = raw[:_PREFIX_LEN], raw[_PREFIX_LEN:]
    # The verifier (api) reads the key read-only — never generates — so it can't diverge from the
    # minter. If no key is persisted yet, no valid token can exist → UNKNOWN.
    signing_key = key or _read_key()
    if signing_key is None:
        return None
    try:
        signing_key.public_key().verify(signature, prefix)
    except InvalidSignature:
        return None
    return VerifyClaims(
        document_id=uuid.UUID(bytes=prefix[:16]),
        version_id=uuid.UUID(bytes=prefix[16:32]),
        content_digest=prefix[32:64].hex(),
    )
