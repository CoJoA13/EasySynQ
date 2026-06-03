"""AES-256-GCM archive envelope encryption (slice S11, doc 12 §6.2 / §8.1).

The durable backup archive is a single AES-256-GCM ciphertext over the plaintext tar (db.dump +
manifest.json + realm.json + config.json + audit_checkpoint.json). The key is derived from
``BACKUP_ENCRYPTION_KEY`` — install.sh-generated, held in the 0600 .env / a Docker secret in
SEPARATE custody from the app KEK, and **never** written into the archive or VCS. A stolen
``.tar.enc`` is useless without the key (doc 12 §6.2). Uses ``cryptography`` (already a dependency
for the Ed25519 checkpoints) — no new package.

The restore-into-scratch DRILL (gate G-C) stays plaintext-internal (it writes + reads the archive
in one process); only the on-disk DURABLE archive is encrypted. ``decrypt_archive`` is the seam the
S11 ``easysynq restore`` calls before unpacking.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Versioned binary header so restore knows the format + key-derivation scheme without guessing.
# layout: MAGIC(6) | FMT_VERSION(1) | KEYREF_LEN(1) | KEYREF(n) | NONCE(12) | GCM-CIPHERTEXT+tag
_MAGIC = b"ESQBKP"
_FMT_VERSION = 1
_NONCE_LEN = 12  # 96-bit GCM nonce (NIST-recommended); random per archive, stored in the header

# Recorded in the manifest + backup_policy.encryption_key_ref so restore re-derives the key
# identically; a future raw-32-byte-base64 scheme would get a distinct ref (e.g. ``:b64-v2``).
ENCRYPTION_KEY_REF = "BACKUP_ENCRYPTION_KEY:sha256-v1"
_PLACEHOLDER = "CHANGE_ME"


class BackupCryptoError(Exception):
    """Encryption/decryption failed: an unset/placeholder key, or a GCM auth-tag mismatch (a wrong
    key or a tampered ciphertext). The restore path turns this into an honest FAIL, never a 500."""


def key_is_configured(secret: str | None) -> bool:
    """True iff ``secret`` is a real configured key (not empty / the install.sh placeholder)."""
    return bool(secret) and secret != _PLACEHOLDER


def derive_key(secret: str) -> bytes:
    """A 32-byte AES-256 key. ``BACKUP_ENCRYPTION_KEY`` is install.sh's ~40-char alnum string (not
    32 raw bytes), so SHA-256 it to a fixed 32-byte key — deterministic, single install-wide key,
    recorded by ref (``sha256-v1``) so restore re-derives identically."""
    if not key_is_configured(secret):
        raise BackupCryptoError(
            "BACKUP_ENCRYPTION_KEY is unset/placeholder — cannot encrypt backup"
        )
    return hashlib.sha256(secret.encode("utf-8")).digest()


def _aad(key_ref: str) -> bytes:
    """Authenticate the magic + key_ref as associated data so the header cannot be swapped."""
    return _MAGIC + key_ref.encode("utf-8")


def encrypt_archive(
    plaintext_tar: Path, dest_enc: Path, *, secret: str, key_ref: str = ENCRYPTION_KEY_REF
) -> Path:
    """Encrypt ``plaintext_tar`` → ``dest_enc`` (a ``.tar.enc``) with AES-256-GCM, a fresh random
    96-bit nonce stored in the header. Returns ``dest_enc``. (Whole archive in memory — MVP-scale;
    chunked/streaming AEAD is a v1.x optimization.)"""
    key = derive_key(secret)
    nonce = os.urandom(_NONCE_LEN)
    data = plaintext_tar.read_bytes()
    ct = AESGCM(key).encrypt(nonce, data, _aad(key_ref))  # 16-byte tag appended by AESGCM
    kr = key_ref.encode("utf-8")
    header = _MAGIC + bytes([_FMT_VERSION, len(kr)]) + kr + nonce
    dest_enc.write_bytes(header + ct)
    return dest_enc


def decrypt_archive(enc_path: Path, dest_tar: Path, *, secret: str) -> Path:
    """Decrypt a ``.tar.enc`` → the plaintext tar at ``dest_tar``. Raises :class:`BackupCryptoError`
    on a wrong key or a tampered ciphertext (the GCM auth-tag check) — the restore treats that as a
    hard FAIL (never a silent accept)."""
    blob = enc_path.read_bytes()
    if blob[:6] != _MAGIC:
        raise BackupCryptoError("not an EasySynQ encrypted backup (bad magic)")
    fmt, kr_len = blob[6], blob[7]
    if fmt != _FMT_VERSION:
        raise BackupCryptoError(f"unsupported envelope format version {fmt}")
    off = 8
    key_ref = blob[off : off + kr_len].decode("utf-8")
    off += kr_len
    nonce = blob[off : off + _NONCE_LEN]
    off += _NONCE_LEN
    ct = blob[off:]
    try:
        pt = AESGCM(derive_key(secret)).decrypt(nonce, ct, _aad(key_ref))
    except BackupCryptoError:
        raise
    except Exception as exc:
        raise BackupCryptoError("decryption failed (wrong key or corrupted archive)") from exc
    dest_tar.write_bytes(pt)
    return dest_tar


def is_encrypted_archive(path: Path) -> bool:
    """True iff ``path`` looks like a ``.tar.enc`` envelope (by extension or magic header)."""
    if path.suffix == ".enc":
        return True
    try:
        with path.open("rb") as f:
            return f.read(6) == _MAGIC
    except OSError:
        return False
