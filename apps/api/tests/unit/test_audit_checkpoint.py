"""Unit tests for the signed-checkpoint verification (Batch 7, doc 12 §4.4) — the detection control
that exposes a privileged DB-owner chain rewrite the self-consistent chain walk alone cannot see.

The threat: an owner rewrites the audit payloads AND recomputes prev_hash/row_hash so the chain is
internally consistent. Only the Ed25519 signature on the latest checkpoint — which the attacker
cannot forge and cannot re-sign over the rewritten latest_row_hash — surfaces the tamper."""

from __future__ import annotations

import datetime
from types import SimpleNamespace
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from easysynq_api.services.audit import checkpoint as cp

_ORG = "11111111-1111-1111-1111-111111111111"
_HASH = b"\xaa" * 32
_TS = datetime.datetime(2026, 7, 24, 12, 0, tzinfo=datetime.UTC)


def _patch_settings(monkeypatch: Any, *, public: str, private: str) -> None:
    monkeypatch.setattr(
        cp,
        "get_settings",
        lambda: SimpleNamespace(
            audit_checkpoint_public_key_path=public,
            audit_checkpoint_signing_key_path=private,
        ),
    )


def test_verify_checkpoint_signature_roundtrip_and_tamper() -> None:
    key = Ed25519PrivateKey.generate()
    pub = key.public_key()
    sig = key.sign(cp._payload(_ORG, 42, _HASH, _TS))

    def _verify(**over: Any) -> bool:
        args: dict[str, Any] = {
            "org_id": _ORG,
            "latest_id": 42,
            "latest_row_hash": _HASH,
            "timestamp": _TS,
            "signature": sig,
        }
        args.update(over)
        return cp.verify_checkpoint_signature(pub, **args)

    assert _verify() is True
    # The DB-owner attack: a rewritten latest_row_hash the attacker cannot re-sign.
    assert _verify(latest_row_hash=b"\xbb" * 32) is False
    # A rewritten latest_id / timestamp likewise breaks the signed payload.
    assert _verify(latest_id=99) is False
    assert _verify(timestamp=_TS + datetime.timedelta(seconds=1)) is False
    # A null signature is never attested (fail-closed).
    assert _verify(signature=None) is False
    # A forgery signed by a DIFFERENT key fails against the trusted key.
    other = Ed25519PrivateKey.generate().sign(cp._payload(_ORG, 42, _HASH, _TS))
    assert _verify(signature=other) is False


def test_load_verify_key_uses_public_when_no_private(tmp_path: Any, monkeypatch: Any) -> None:
    key = Ed25519PrivateKey.generate()
    pub_path = tmp_path / "pub.pem"
    pub_path.write_bytes(
        key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    )
    # Private path ABSENT → the api/CLI/off-host case: verify with the public key alone (no secret).
    _patch_settings(monkeypatch, public=str(pub_path), private=str(tmp_path / "absent.pem"))
    loaded = cp.load_verify_key()
    assert loaded is not None
    sig = key.sign(cp._payload(_ORG, 1, _HASH, _TS))
    assert (
        cp.verify_checkpoint_signature(
            loaded, org_id=_ORG, latest_id=1, latest_row_hash=_HASH, timestamp=_TS, signature=sig
        )
        is True
    )


def test_load_verify_key_prefers_private_over_stale_public(tmp_path: Any, monkeypatch: Any) -> None:
    # The beat holds the private key AND a STALE public key (a prior key's public) is at the export
    # path. Deriving from the private key must WIN, so the beat verifies the ACTUAL signer — else a
    # stale exported public key would false-alarm CHAIN_VERIFY_FAIL every night.
    signer = Ed25519PrivateKey.generate()
    stale = Ed25519PrivateKey.generate()
    priv_path = tmp_path / "priv.pem"
    priv_path.write_bytes(signer.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    pub_path = tmp_path / "stale_pub.pem"
    pub_path.write_bytes(
        stale.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    )
    _patch_settings(monkeypatch, public=str(pub_path), private=str(priv_path))
    loaded = cp.load_verify_key()
    assert loaded is not None
    sig = signer.sign(cp._payload(_ORG, 1, _HASH, _TS))  # signed by the ACTUAL signer
    assert (
        cp.verify_checkpoint_signature(
            loaded, org_id=_ORG, latest_id=1, latest_row_hash=_HASH, timestamp=_TS, signature=sig
        )
        is True
    )


def test_load_verify_key_derives_from_private_when_no_public(
    tmp_path: Any, monkeypatch: Any
) -> None:
    key = Ed25519PrivateKey.generate()
    priv_path = tmp_path / "priv.pem"
    priv_path.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    # No public key exported → the beat case: derive the verify key from the private signing key.
    _patch_settings(monkeypatch, public=str(tmp_path / "absent_pub.pem"), private=str(priv_path))
    assert cp.load_verify_key() is not None


def test_load_verify_key_none_when_neither_available(tmp_path: Any, monkeypatch: Any) -> None:
    _patch_settings(monkeypatch, public=str(tmp_path / "np.pem"), private=str(tmp_path / "nk.pem"))
    assert cp.load_verify_key() is None  # an api/CLI process with no key → walk-only, cannot attest


def test_load_verify_key_fails_closed_on_malformed_public(tmp_path: Any, monkeypatch: Any) -> None:
    # A truncated / invalid exported PEM must FAIL CLOSED to None (degrade the verify to a walk),
    # never raise — a raise would abort the nightly detection task and 500 the API verify endpoint.
    pub_path = tmp_path / "pub.pem"
    pub_path.write_bytes(b"-----BEGIN PUBLIC KEY-----\nnot-a-valid-pem\n-----END PUBLIC KEY-----\n")
    _patch_settings(monkeypatch, public=str(pub_path), private=str(tmp_path / "absent.pem"))
    assert cp.load_verify_key() is None


def test_load_verify_key_fails_closed_on_malformed_private(tmp_path: Any, monkeypatch: Any) -> None:
    priv_path = tmp_path / "priv.pem"
    priv_path.write_bytes(b"-----BEGIN PRIVATE KEY-----\ngarbage\n-----END PRIVATE KEY-----\n")
    _patch_settings(monkeypatch, public=str(tmp_path / "absent_pub.pem"), private=str(priv_path))
    assert cp.load_verify_key() is None


def test_should_alarm_offhost_decision_table() -> None:
    """The nightly beat's off-host alarm decision. A wipe leaves a readable off-host object that
    FAILS attestation (attest_failures>0) → ALARM; a read failure (unreachable witness) → ALARM;
    but a not-yet-anchored empty sink — even alongside a healthy one (sinks_read>0) — stays quiet,
    since the decision is keyed on real attestation failures, not the global read count. Pins both
    the wipe alarm (the fail-open diff-critic caught) and the multi-sink false-positive fix."""
    from easysynq_api.services.audit.checkpoint import OffHostCheckpointResult as R
    from easysynq_api.tasks.audit import _should_alarm_offhost

    # No off-host witness configured → defer to the R13 soft-gate, never a nightly alarm.
    assert _should_alarm_offhost(R(False, 0, False, ["unavailable"])) is False
    # Configured + attested → healthy.
    assert _should_alarm_offhost(R(True, 1, True, [])) is False
    # Configured but nothing anchored yet (fresh org) → quiet, defers to the soft-gate.
    assert _should_alarm_offhost(R(True, 0, False, ["no object found"])) is False
    # A healthy sink PLUS a freshly-added empty sibling (read=1 for the healthy one, a "no object"
    # reason for the new one, NO attestation failure) must NOT alarm — keyed on attest_failures,
    # not the global sinks_read, so a second witness added before its first anchor stays quiet.
    assert _should_alarm_offhost(R(True, 1, False, ["sink B: no object found"])) is False
    # THE WIPE: an object was read back and REJECTED (references a now-missing chain row) → ALARM.
    assert _should_alarm_offhost(R(True, 1, False, ["deletion"], attest_failures=1)) is True
    # A read failure (unreachable witness) → fail-closed ALARM even though nothing was read back.
    assert _should_alarm_offhost(R(True, 0, False, ["read failed"], read_failed=True)) is True


def test_load_signing_key_exports_the_public_half(tmp_path: Any, monkeypatch: Any) -> None:
    priv_path = tmp_path / "priv.pem"
    pub_path = tmp_path / "pub.pem"
    _patch_settings(monkeypatch, public=str(pub_path), private=str(priv_path))
    key = cp.load_signing_key()
    assert priv_path.exists() and pub_path.exists()  # both persisted on first use
    # The exported public key attests a signature made by the signing key.
    loaded_pub = cp.load_verify_key()
    assert loaded_pub is not None
    sig = key.sign(cp._payload(_ORG, 7, _HASH, _TS))
    assert (
        cp.verify_checkpoint_signature(
            loaded_pub,
            org_id=_ORG,
            latest_id=7,
            latest_row_hash=_HASH,
            timestamp=_TS,
            signature=sig,
        )
        is True
    )
