"""S11 unit proofs (no DB / MinIO / pg_dump):

* the AES-256-GCM archive envelope (encrypt→decrypt round-trip; wrong-key + tampered-ciphertext both
  fail; an unset/placeholder key refuses to encrypt) — the durable-backup confidentiality leg;
* the Keycloak realm-name parse + graceful-degradation seam;
* the pure ``checkpoint_verdict`` (doc 12 §8.2 / R37) — the headline restore tamper guard, including
  the FALSE-PASS traps: an off-host checkpoint ahead of the restored head must FLAG even when the
  bundled one agrees, and a MISSING off-host checkpoint must FLAG (never a silent PASS).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from easysynq_api.services.backup import crypto, realm_export
from easysynq_api.services.backup.crypto import BackupCryptoError
from easysynq_api.services.backup.restore import checkpoint_verdict

_KEY = "a-real-40-char-ish-backup-encryption-key-1234"


def test_encrypt_decrypt_roundtrip(tmp_path: Path) -> None:
    plain = tmp_path / "archive.tar"
    plain.write_bytes(b"PGDMP-fake + manifest + realm + config" * 100)
    enc = crypto.encrypt_archive(plain, tmp_path / "out.tar.enc", secret=_KEY)
    assert enc.read_bytes()[:6] == b"ESQBKP"  # the envelope header magic
    out = crypto.decrypt_archive(enc, tmp_path / "round.tar", secret=_KEY)
    assert out.read_bytes() == plain.read_bytes()


def test_decrypt_wrong_key_fails(tmp_path: Path) -> None:
    plain = tmp_path / "a.tar"
    plain.write_bytes(b"secret backup bytes")
    enc = crypto.encrypt_archive(plain, tmp_path / "a.tar.enc", secret=_KEY)
    other = "a-different-but-valid-length-key-xyz"
    with pytest.raises(BackupCryptoError):
        crypto.decrypt_archive(enc, tmp_path / "bad.tar", secret=other)


def test_decrypt_tampered_ciphertext_fails(tmp_path: Path) -> None:
    """A flipped byte trips the GCM auth tag → BackupCryptoError (not a silent bad restore)."""
    plain = tmp_path / "a.tar"
    plain.write_bytes(b"secret backup bytes that are long enough to flip" * 4)
    enc = crypto.encrypt_archive(plain, tmp_path / "a.tar.enc", secret=_KEY)
    blob = bytearray(enc.read_bytes())
    blob[-1] ^= 0x01  # corrupt the last ciphertext/tag byte
    enc.write_bytes(bytes(blob))
    with pytest.raises(BackupCryptoError):
        crypto.decrypt_archive(enc, tmp_path / "bad.tar", secret=_KEY)


def test_encrypt_refuses_placeholder_key(tmp_path: Path) -> None:
    plain = tmp_path / "a.tar"
    plain.write_bytes(b"x")
    assert crypto.key_is_configured("CHANGE_ME") is False
    assert crypto.key_is_configured("") is False
    assert crypto.key_is_configured(_KEY) is True
    with pytest.raises(BackupCryptoError):
        crypto.encrypt_archive(plain, tmp_path / "a.tar.enc", secret="CHANGE_ME")


def test_is_encrypted_archive(tmp_path: Path) -> None:
    plain = tmp_path / "p.tar"
    plain.write_bytes(b"not encrypted")
    enc = crypto.encrypt_archive(plain, tmp_path / "p.tar.enc", secret=_KEY)
    assert crypto.is_encrypted_archive(enc) is True
    assert crypto.is_encrypted_archive(plain) is False


def test_realm_name_from_issuer() -> None:
    assert realm_export.realm_name_from_issuer("https://kc/realms/easysynq") == "easysynq"
    assert realm_export.realm_name_from_issuer("http://localhost/realms/acme/") == "acme"
    assert realm_export.realm_name_from_issuer("") == "easysynq"  # default
    assert realm_export.realm_name_from_issuer("https://kc/no-realm-here") == "easysynq"


def test_export_realm_unconfigured_is_absent() -> None:
    """Empty admin creds → None (the realm leg is recorded 'absent'); a Keycloak outage must never
    fail the nightly backup."""
    assert (
        realm_export.export_realm(base_url="", realm="easysynq", admin_user="", admin_password="")
        is None
    )
    assert (
        realm_export.export_realm(
            base_url="http://kc:8080", realm="easysynq", admin_user="admin", admin_password=""
        )
        is None
    )


# --- checkpoint_verdict: the headline restore tamper guard (doc 12 §8.2 / R37) -----------------


def test_checkpoint_verdict_ok_when_both_at_or_before_head() -> None:
    verdict, flags = checkpoint_verdict(restored_head=100, bundled=100, off_host=100)
    assert verdict == "OK"
    assert flags == []
    verdict, _ = checkpoint_verdict(restored_head=100, bundled=90, off_host=80)
    assert verdict == "OK"


def test_checkpoint_verdict_flags_bundled_ahead() -> None:
    verdict, flags = checkpoint_verdict(restored_head=100, bundled=101, off_host=100)
    assert verdict == "FLAGGED"
    assert any("bundled" in f for f in flags)


def test_checkpoint_verdict_offhost_ahead_flags_even_when_bundled_agrees() -> None:
    """THE FALSE-PASS TRAP: a tamperer who truncated the tail to head=100 AND rebuilt a matching
    bundled checkpoint (=100) is STILL caught — the off-host leg compares against the restored head,
    not against the bundled checkpoint."""
    verdict, flags = checkpoint_verdict(restored_head=100, bundled=100, off_host=120)
    assert verdict == "FLAGGED"
    assert any("off-host" in f for f in flags)


def test_checkpoint_verdict_missing_offhost_flags() -> None:
    """No reachable off-host anchor is UNVERIFIABLE → FLAGGED (never a silent PASS); an install with
    no genuine off-host anchor cannot prove the restored chain is complete (R13)."""
    verdict, flags = checkpoint_verdict(restored_head=100, bundled=100, off_host=None)
    assert verdict == "FLAGGED"
    assert any("off-host" in f for f in flags)


def test_checkpoint_verdict_zero_head() -> None:
    """A restore with no audit rows + no checkpoints anywhere is OK only if off_host is also absent-
    of-rows (0); a None off_host still flags (unverifiable)."""
    assert checkpoint_verdict(restored_head=0, bundled=None, off_host=0)[0] == "OK"
    assert checkpoint_verdict(restored_head=0, bundled=None, off_host=None)[0] == "FLAGGED"
