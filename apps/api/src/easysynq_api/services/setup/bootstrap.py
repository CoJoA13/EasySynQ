"""The bootstrap secret — pure crypto for the first-run install secret (slice S8a, doc 08 §4).

The operator mints a high-entropy single-use secret (``easysynq setup mint-bootstrap``); its
**salted** hash is stored on ``system_config`` and the plaintext is shown once. The public
``/setup/bootstrap`` endpoint verifies a presented secret against that hash. No DB here — these are
pure, unit-testable helpers; storage + TTL + single-use live in ``service.py``.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

_SALT_BYTES = 16
_SECRET_BYTES = 32  # ~256 bits → brute-force over any TTL is infeasible


def _digest(salt: bytes, secret: str) -> str:
    return hashlib.sha256(salt + secret.encode()).hexdigest()


def mint_secret() -> tuple[str, str]:
    """Return ``(plaintext_secret, stored_hash)``. ``stored_hash`` is ``<salt_hex>:<sha256_hex>``
    (salted); the plaintext is returned ONCE for the operator and never persisted."""
    secret = secrets.token_urlsafe(_SECRET_BYTES)
    salt = secrets.token_bytes(_SALT_BYTES)
    return secret, f"{salt.hex()}:{_digest(salt, secret)}"


def verify_secret(secret: str, stored_hash: str | None) -> bool:
    """Constant-time check of ``secret`` against a ``<salt_hex>:<sha256_hex>`` ``stored_hash``.
    ``False`` for any malformed/absent hash (never raises)."""
    if not stored_hash:
        return False
    salt_hex, _, expected = stored_hash.partition(":")
    if not expected:
        return False
    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    return hmac.compare_digest(_digest(salt, secret), expected)
