"""Contract tests for the pure audit checkpoint v2 codec."""

from __future__ import annotations

import base64
import copy
import dataclasses
import datetime
import hashlib
import json
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import rfc8785
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from easysynq_api.services.audit import checkpoint as legacy_checkpoint
from easysynq_api.services.audit import checkpoint_v2

_SIGNATURE_DOMAIN = b"EasySynQ/AuditCheckpoint/v2/signature\0"
_HASH_DOMAIN = b"EasySynQ/AuditCheckpoint/v2/hash\0"
_PROOF_DOMAIN = b"EasySynQ/AuditCheckpoint/v2/key-transition-proof\0"
_FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent / "fixtures" / "audit_checkpoint_v2_vectors.json"
)
_REFERENCE: dict[str, Any] = json.loads(_FIXTURE_PATH.read_bytes())
_VECTORS: dict[str, dict[str, Any]] = {vector["name"]: vector for vector in _REFERENCE["vectors"]}
_K1 = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
_K2 = Ed25519PrivateKey.from_private_bytes(bytes(range(32, 64)))
_K1_RAW = _K1.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
_K2_RAW = _K2.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

pytestmark = pytest.mark.unit


def _private_key_for(vector: dict[str, Any]) -> Ed25519PrivateKey:
    return _K1 if vector["public_key_hex"] == _K1_RAW.hex() else _K2


def _expected_ids(vector: dict[str, Any]) -> tuple[UUID, UUID]:
    checkpoint = vector["checkpoint"]
    return UUID(checkpoint["org_id"]), UUID(checkpoint["stream_id"])


def _verify(
    encoded: bytes,
    vector: dict[str, Any] | None = None,
    *,
    public_key: Ed25519PublicKey | None = None,
    org_id: UUID | None = None,
    stream_id: UUID | None = None,
) -> checkpoint_v2.VerifiedEnvelope:
    selected = vector or _VECTORS["anchor"]
    expected_org, expected_stream = _expected_ids(selected)
    key = public_key or _private_key_for(selected).public_key()
    return checkpoint_v2.verify_envelope(
        encoded,
        public_key=key,
        org_id=org_id or expected_org,
        stream_id=stream_id or expected_stream,
    )


def _authenticated_envelope(
    checkpoint: dict[str, object],
    *,
    key: Ed25519PrivateKey = _K1,
    signature_domain: bytes = _SIGNATURE_DOMAIN,
) -> bytes:
    canonical = rfc8785.dumps(checkpoint)
    signature = key.sign(signature_domain + canonical)
    anchor_hash = hashlib.sha256(_HASH_DOMAIN + canonical + signature).hexdigest()
    return rfc8785.dumps(
        {
            "checkpoint": checkpoint,
            "signature": base64.b64encode(signature).decode("ascii"),
            "anchor_hash": anchor_hash,
        }
    )


def _assert_invalid(call: Callable[[], object], *, key: bool = False) -> None:
    expected = "invalid checkpoint key" if key else "invalid checkpoint"
    with pytest.raises(checkpoint_v2.CheckpointV2Error, match=f"^{expected}$"):
        call()


def test_reference_anchor_verifies_and_signs_exactly() -> None:
    vector = _VECTORS["anchor"]
    checkpoint = vector["checkpoint"]
    canonical_checkpoint = bytes.fromhex(vector["canonical_checkpoint_hex"])
    signature = bytes.fromhex(vector["signature_hex"])
    encoded = bytes.fromhex(vector["canonical_envelope_hex"])
    key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))

    assert key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw) == bytes.fromhex(
        vector["public_key_hex"]
    )
    assert rfc8785.dumps(checkpoint) == canonical_checkpoint
    key.public_key().verify(signature, _SIGNATURE_DOMAIN + canonical_checkpoint)
    assert (
        hashlib.sha256(_HASH_DOMAIN + canonical_checkpoint + signature).hexdigest()
        == vector["anchor_hash"]
    )
    assert (
        rfc8785.dumps(
            {
                "anchor_hash": vector["anchor_hash"],
                "checkpoint": checkpoint,
                "signature": base64.b64encode(signature).decode("ascii"),
            }
        )
        == encoded
    )

    result = checkpoint_v2.verify_envelope(
        encoded,
        public_key=key.public_key(),
        org_id=UUID(checkpoint["org_id"]),
        stream_id=UUID(checkpoint["stream_id"]),
    )
    assert result.canonical_envelope == encoded
    assert result.anchor_hash == vector["anchor_hash"]
    assert checkpoint_v2.sign_checkpoint(checkpoint, signing_key=key) == encoded


@pytest.mark.parametrize("name", ["anchor", "transition", "successor", "maximum", "minimum"])
def test_all_golden_vectors_verify_and_sign_exact_bytes(name: str) -> None:
    vector = _VECTORS[name]
    checkpoint = vector["checkpoint"]
    canonical = bytes.fromhex(vector["canonical_checkpoint_hex"])
    signature = bytes.fromhex(vector["signature_hex"])
    encoded = bytes.fromhex(vector["canonical_envelope_hex"])
    key = _private_key_for(vector)

    assert rfc8785.dumps(checkpoint) == canonical
    key.public_key().verify(signature, _SIGNATURE_DOMAIN + canonical)
    assert hashlib.sha256(_HASH_DOMAIN + canonical + signature).hexdigest() == vector["anchor_hash"]

    result = _verify(encoded, vector)
    assert result.format_version == 2
    assert result.kind == checkpoint["kind"]
    assert result.org_id == UUID(checkpoint["org_id"])
    assert result.stream_id == UUID(checkpoint["stream_id"])
    assert result.anchor_id == UUID(checkpoint["anchor_id"])
    assert result.sequence == int(checkpoint["sequence"])
    assert result.previous_anchor_hash == checkpoint["previous_anchor_hash"]
    assert result.key_id == checkpoint["key_id"]
    assert result.key_epoch == int(checkpoint["key_epoch"])
    assert result.latest_id == int(checkpoint["latest_id"])
    assert result.latest_row_hash == checkpoint["latest_row_hash"]
    assert result.timestamp.tzinfo is datetime.UTC
    assert result.canonical_checkpoint == canonical
    assert result.canonical_envelope == encoded
    assert result.signature == signature
    if name != "transition":
        assert result.next_key_id is None
        assert result.next_public_key is None
        assert result.next_key_epoch is None
        assert result.next_key_signature is None

    signer_input = copy.deepcopy(checkpoint)
    next_key = None
    if name == "transition":
        next_key = _K2
        del signer_input["next_key_signature"]
    before = copy.deepcopy(signer_input)
    assert (
        checkpoint_v2.sign_checkpoint(
            signer_input,
            signing_key=key,
            next_signing_key=next_key,
        )
        == encoded
    )
    assert signer_input == before


def test_transition_proof_is_independently_valid_and_returned_as_immutable_bytes() -> None:
    vector = _VECTORS["transition"]
    checkpoint = vector["checkpoint"]
    proof = base64.b64decode(checkpoint["next_key_signature"], validate=True)
    message = bytes.fromhex(vector["proof_message_hex"])
    assert message.startswith(_PROOF_DOMAIN)
    _K2.public_key().verify(proof, message)

    result = _verify(bytes.fromhex(vector["canonical_envelope_hex"]), vector)
    assert result.next_key_id == checkpoint["next_key_id"]
    assert result.next_public_key == _K2_RAW
    assert result.next_key_epoch == 1
    assert result.next_key_signature == proof


def test_equivalent_json_syntax_returns_the_same_canonical_evidence() -> None:
    vector = _VECTORS["anchor"]
    envelope = {
        "signature": base64.b64encode(bytes.fromhex(vector["signature_hex"])).decode("ascii"),
        "anchor_hash": vector["anchor_hash"],
        "checkpoint": vector["checkpoint"],
    }
    reordered = json.dumps(envelope, indent=2).encode("utf-8")
    escaped = reordered.replace(b'"kind": "anchor"', b'"kind": "\\u0061nchor"')

    for alternate in (reordered, escaped):
        result = _verify(alternate, vector)
        assert result.canonical_checkpoint == bytes.fromhex(vector["canonical_checkpoint_hex"])
        assert result.canonical_envelope == bytes.fromhex(vector["canonical_envelope_hex"])


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("format_version", 3),
        ("kind", "unknown"),
        ("org_id", "00000000-0000-4000-8000-000000000099"),
        ("stream_id", "00000000-0000-4000-8000-000000000099"),
        ("anchor_id", "00000000-0000-4000-8000-000000000099"),
        ("sequence", "9"),
        ("previous_anchor_hash", "4" * 64),
        ("key_id", "ed25519-sha256:" + "4" * 64),
        ("key_epoch", "9"),
        ("latest_id", "43"),
        ("latest_row_hash", "4" * 64),
        ("timestamp", "2026-01-01T00:00:01.123456Z"),
    ],
)
def test_each_common_security_field_tamper_rejects(field: str, replacement: object) -> None:
    vector = _VECTORS["anchor"]
    envelope = json.loads(bytes.fromhex(vector["canonical_envelope_hex"]))
    envelope["checkpoint"][field] = replacement
    _assert_invalid(lambda: _verify(rfc8785.dumps(envelope), vector))


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("next_key_id", "ed25519-sha256:" + "4" * 64),
        ("next_public_key", base64.b64encode(_K1_RAW).decode("ascii")),
        ("next_key_epoch", "2"),
        ("next_key_signature", base64.b64encode(bytes(64)).decode("ascii")),
    ],
)
def test_each_transition_security_field_tamper_rejects(field: str, replacement: str) -> None:
    vector = _VECTORS["transition"]
    envelope = json.loads(bytes.fromhex(vector["canonical_envelope_hex"]))
    envelope["checkpoint"][field] = replacement
    _assert_invalid(lambda: _verify(rfc8785.dumps(envelope), vector))


def test_signature_hash_and_cross_domain_main_signatures_reject() -> None:
    vector = _VECTORS["anchor"]
    envelope = json.loads(bytes.fromhex(vector["canonical_envelope_hex"]))

    bad_signature = copy.deepcopy(envelope)
    bad_signature["signature"] = base64.b64encode(bytes(64)).decode("ascii")
    _assert_invalid(lambda: _verify(rfc8785.dumps(bad_signature), vector))

    bad_hash = copy.deepcopy(envelope)
    bad_hash["anchor_hash"] = "0" * 64
    _assert_invalid(lambda: _verify(rfc8785.dumps(bad_hash), vector))

    wrong_domain = _authenticated_envelope(
        copy.deepcopy(vector["checkpoint"]), signature_domain=_PROOF_DOMAIN
    )
    _assert_invalid(lambda: _verify(wrong_domain, vector))


@pytest.mark.parametrize("proof_kind", ["invalid", "main-domain", "wrong-next-key"])
def test_transition_proof_failure_rejects_with_a_fresh_valid_outer_signature(
    proof_kind: str,
) -> None:
    vector = _VECTORS["transition"]
    checkpoint = copy.deepcopy(vector["checkpoint"])
    proof_payload = dict(checkpoint)
    del proof_payload["next_key_signature"]
    proof_bytes = rfc8785.dumps(proof_payload)
    if proof_kind == "invalid":
        proof = bytes(64)
    elif proof_kind == "main-domain":
        proof = _K2.sign(_SIGNATURE_DOMAIN + proof_bytes)
    else:
        proof = _K1.sign(_PROOF_DOMAIN + proof_bytes)
    checkpoint["next_key_signature"] = base64.b64encode(proof).decode("ascii")

    encoded = _authenticated_envelope(checkpoint)
    _K1.public_key().verify(
        base64.b64decode(json.loads(encoded)["signature"]),
        _SIGNATURE_DOMAIN + rfc8785.dumps(checkpoint),
    )
    _assert_invalid(lambda: _verify(encoded, vector))


def test_fully_current_key_signed_identity_transition_is_rejected() -> None:
    control = _REFERENCE["identity_transition"]
    encoded = bytes.fromhex(control["canonical_envelope_hex"])
    checkpoint = control["envelope"]["checkpoint"]
    _K1.public_key().verify(
        base64.b64decode(control["envelope"]["signature"]),
        _SIGNATURE_DOMAIN + rfc8785.dumps(checkpoint),
    )
    _assert_invalid(lambda: _verify(encoded, public_key=_K1.public_key()), key=True)


def test_all_frozen_public_key_admissibility_controls() -> None:
    for case in _REFERENCE["key_admissibility"]:
        raw = bytes.fromhex(case["public_key_hex"])
        if len(raw) != 32:
            with pytest.raises(ValueError):
                Ed25519PublicKey.from_public_bytes(raw)
            continue
        public_key = Ed25519PublicKey.from_public_bytes(raw)
        if case["admissible"]:
            assert checkpoint_v2.public_key_id(public_key) == (
                "ed25519-sha256:" + hashlib.sha256(raw).hexdigest()
            )
        else:
            _assert_invalid(
                lambda public_key=public_key: checkpoint_v2.public_key_id(public_key),
                key=True,
            )

    rfc_raw = bytes.fromhex(_REFERENCE["rfc8032_public_key_hex"])
    rfc_key = Ed25519PublicKey.from_public_bytes(rfc_raw)
    assert checkpoint_v2.public_key_id(rfc_key) == (
        "ed25519-sha256:" + hashlib.sha256(rfc_raw).hexdigest()
    )


def test_invalid_current_and_embedded_next_keys_reject_at_public_seams() -> None:
    anchor = _VECTORS["anchor"]
    anchor_encoded = bytes.fromhex(anchor["canonical_envelope_hex"])
    transition = _VECTORS["transition"]
    for case in _REFERENCE["key_admissibility"]:
        if case["admissible"]:
            continue
        raw = bytes.fromhex(case["public_key_hex"])
        if len(raw) == 32:
            public_key = Ed25519PublicKey.from_public_bytes(raw)
            _assert_invalid(
                lambda public_key=public_key: _verify(
                    anchor_encoded, anchor, public_key=public_key
                ),
                key=True,
            )

        checkpoint = copy.deepcopy(transition["checkpoint"])
        checkpoint["next_public_key"] = base64.b64encode(raw).decode("ascii")
        checkpoint["next_key_id"] = "ed25519-sha256:" + hashlib.sha256(raw).hexdigest()
        encoded = _authenticated_envelope(checkpoint)
        _assert_invalid(
            lambda encoded=encoded: _verify(encoded, transition),
            key=len(raw) == 32,
        )


def test_wrong_keys_expected_identity_and_transition_signer_contract_reject() -> None:
    anchor = _VECTORS["anchor"]
    anchor_encoded = bytes.fromhex(anchor["canonical_envelope_hex"])
    transition = _VECTORS["transition"]
    transition_input = copy.deepcopy(transition["checkpoint"])
    del transition_input["next_key_signature"]

    _assert_invalid(lambda: _verify(anchor_encoded, anchor, public_key=_K2.public_key()))
    _assert_invalid(
        lambda: _verify(
            anchor_encoded,
            anchor,
            org_id=UUID("00000000-0000-4000-8000-000000000099"),
        )
    )
    _assert_invalid(
        lambda: checkpoint_v2.verify_envelope(
            anchor_encoded,
            public_key=_K1.public_key(),
            org_id=str(_expected_ids(anchor)[0]),  # type: ignore[arg-type]
            stream_id=_expected_ids(anchor)[1],
        )
    )
    _assert_invalid(
        lambda: _verify(
            anchor_encoded,
            anchor,
            stream_id=UUID("00000000-0000-4000-8000-000000000099"),
        )
    )
    _assert_invalid(lambda: checkpoint_v2.sign_checkpoint(anchor["checkpoint"], signing_key=_K2))
    _assert_invalid(
        lambda: checkpoint_v2.sign_checkpoint(
            anchor["checkpoint"], signing_key=_K1, next_signing_key=_K2
        )
    )
    _assert_invalid(lambda: checkpoint_v2.sign_checkpoint(transition_input, signing_key=_K1))
    _assert_invalid(
        lambda: checkpoint_v2.sign_checkpoint(
            transition_input, signing_key=_K1, next_signing_key=_K1
        )
    )
    _assert_invalid(
        lambda: checkpoint_v2.sign_checkpoint(
            transition["checkpoint"], signing_key=_K1, next_signing_key=_K2
        )
    )


def test_public_apis_reject_non_ed25519_key_objects_with_controlled_errors() -> None:
    x25519 = X25519PrivateKey.generate()
    anchor = _VECTORS["anchor"]
    encoded = bytes.fromhex(anchor["canonical_envelope_hex"])

    _assert_invalid(
        lambda: checkpoint_v2.public_key_id(x25519.public_key()),  # type: ignore[arg-type]
        key=True,
    )
    _assert_invalid(
        lambda: checkpoint_v2.verify_envelope(
            encoded,
            public_key=x25519.public_key(),  # type: ignore[arg-type]
            org_id=_expected_ids(anchor)[0],
            stream_id=_expected_ids(anchor)[1],
        ),
        key=True,
    )
    _assert_invalid(
        lambda: checkpoint_v2.sign_checkpoint(
            anchor["checkpoint"],
            signing_key=x25519,  # type: ignore[arg-type]
        ),
        key=True,
    )


def test_same_key_transition_epoch_skip_and_epoch_overflow_reject() -> None:
    vector = _VECTORS["transition"]

    same_key = copy.deepcopy(vector["checkpoint"])
    same_key["next_public_key"] = base64.b64encode(_K1_RAW).decode("ascii")
    same_key["next_key_id"] = same_key["key_id"]
    _assert_invalid(lambda: _verify(_authenticated_envelope(same_key), vector))

    skipped = copy.deepcopy(vector["checkpoint"])
    skipped["next_key_epoch"] = "2"
    _assert_invalid(lambda: _verify(_authenticated_envelope(skipped), vector))

    overflow = copy.deepcopy(vector["checkpoint"])
    overflow["key_epoch"] = "9223372036854775807"
    overflow["next_key_epoch"] = "9223372036854775807"
    _assert_invalid(lambda: _verify(_authenticated_envelope(overflow), vector))


@pytest.mark.parametrize("field", ["sequence", "latest_id"])
@pytest.mark.parametrize(
    "value",
    [
        "0",
        "01",
        "+1",
        "-1",
        "1 ",
        " 1",
        "1.0",
        "1e0",
        "\u0661",
        "9223372036854775808",
        1,
        True,
        1.0,
    ],
)
def test_positive_decimal_fields_reject_ambiguous_types_and_spellings(
    field: str, value: object
) -> None:
    checkpoint = copy.deepcopy(_VECTORS["anchor"]["checkpoint"])
    checkpoint[field] = value
    _assert_invalid(lambda: checkpoint_v2.sign_checkpoint(checkpoint, signing_key=_K1))


@pytest.mark.parametrize(
    "value",
    ["00", "+0", "-0", "0 ", "1.0", "1e0", "\u0661", "9223372036854775808", 0, True, 0.0],
)
def test_epoch_rejects_ambiguous_types_spellings_and_overflow(value: object) -> None:
    checkpoint = copy.deepcopy(_VECTORS["anchor"]["checkpoint"])
    checkpoint["key_epoch"] = value
    _assert_invalid(lambda: checkpoint_v2.sign_checkpoint(checkpoint, signing_key=_K1))


@pytest.mark.parametrize("value", [True, False, 2.0, "2", 3, None])
def test_format_version_must_be_the_actual_integer_two(value: object) -> None:
    checkpoint = copy.deepcopy(_VECTORS["anchor"]["checkpoint"])
    checkpoint["format_version"] = value
    _assert_invalid(lambda: checkpoint_v2.sign_checkpoint(checkpoint, signing_key=_K1))


@pytest.mark.parametrize(
    "value",
    [
        "2026-01-01T00:00:00Z",
        "2026-01-01T00:00:00.12345Z",
        "2026-01-01T00:00:00.123456+00:00",
        "2026-01-01t00:00:00.123456Z",
        "2026-02-29T00:00:00.000000Z",
        "2024-02-30T00:00:00.000000Z",
        "2024-01-01T24:00:00.000000Z",
        "2024-01-01T23:59:60.000000Z",
        "0000-01-01T00:00:00.000000Z",
    ],
)
def test_timestamp_requires_an_exact_real_utc_instant(value: str) -> None:
    checkpoint = copy.deepcopy(_VECTORS["anchor"]["checkpoint"])
    checkpoint["timestamp"] = value
    _assert_invalid(lambda: checkpoint_v2.sign_checkpoint(checkpoint, signing_key=_K1))


def test_valid_leap_day_and_year_boundaries_are_accepted() -> None:
    for name in ("minimum", "maximum"):
        vector = _VECTORS[name]
        _verify(bytes.fromhex(vector["canonical_envelope_hex"]), vector)

    checkpoint = copy.deepcopy(_VECTORS["anchor"]["checkpoint"])
    checkpoint["timestamp"] = "2024-02-29T23:59:59.000001Z"
    encoded = checkpoint_v2.sign_checkpoint(checkpoint, signing_key=_K1)
    result = _verify(encoded)
    assert result.timestamp == datetime.datetime(2024, 2, 29, 23, 59, 59, 1, tzinfo=datetime.UTC)


@pytest.mark.parametrize(
    "value",
    [
        "00000000000040008000000000000001",
        "{00000000-0000-4000-8000-000000000001}",
        "00000000-0000-4000-8000-00000000000A",
        "00000000-0000-4000-8000-00000000001",
        "not-a-uuid",
    ],
)
def test_uuid_fields_require_canonical_lowercase_hyphenated_spelling(value: str) -> None:
    checkpoint = copy.deepcopy(_VECTORS["anchor"]["checkpoint"])
    checkpoint["anchor_id"] = value
    _assert_invalid(lambda: checkpoint_v2.sign_checkpoint(checkpoint, signing_key=_K1))


@pytest.mark.parametrize("field", ["previous_anchor_hash", "latest_row_hash"])
@pytest.mark.parametrize("value", ["a" * 63, "a" * 65, "A" * 64, "g" * 64, "a" * 63 + " "])
def test_digest_fields_require_exact_lowercase_hex(field: str, value: str) -> None:
    checkpoint = copy.deepcopy(_VECTORS["anchor"]["checkpoint"])
    checkpoint[field] = value
    _assert_invalid(lambda: checkpoint_v2.sign_checkpoint(checkpoint, signing_key=_K1))


@pytest.mark.parametrize(
    "value",
    [
        "ed25519-sha256:" + "a" * 63,
        "ed25519-sha256:" + "A" * 64,
        "ED25519-sha256:" + "a" * 64,
        "sha256:" + "a" * 64,
    ],
)
def test_key_id_requires_exact_grammar_and_material_identity(value: str) -> None:
    checkpoint = copy.deepcopy(_VECTORS["anchor"]["checkpoint"])
    checkpoint["key_id"] = value
    _assert_invalid(lambda: checkpoint_v2.sign_checkpoint(checkpoint, signing_key=_K1))


@pytest.mark.parametrize(
    "value",
    [
        "",
        "*" * 88,
        base64.urlsafe_b64encode(b"\xfb" * 64).decode("ascii"),
        base64.b64encode(bytes(63)).decode("ascii"),
        base64.b64encode(bytes(64)).decode("ascii").rstrip("="),
        base64.b64encode(bytes(64)).decode("ascii")[:-3] + "B==",
    ],
)
def test_signature_base64_requires_standard_padding_length_and_zero_pad_bits(value: str) -> None:
    vector = _VECTORS["anchor"]
    envelope = json.loads(bytes.fromhex(vector["canonical_envelope_hex"]))
    envelope["signature"] = value
    _assert_invalid(lambda: _verify(json.dumps(envelope).encode("utf-8"), vector))


@pytest.mark.parametrize("field", ["next_public_key", "next_key_signature"])
@pytest.mark.parametrize("value", ["*", "AAAA", "A" * 129])
def test_transition_base64_fields_reject_malformed_or_oversized_text(
    field: str, value: str
) -> None:
    vector = _VECTORS["transition"]
    checkpoint = copy.deepcopy(vector["checkpoint"])
    checkpoint[field] = value
    _assert_invalid(lambda: _verify(_authenticated_envelope(checkpoint), vector))


@pytest.mark.parametrize("value", ["a" * 63, "a" * 65, "A" * 64, "g" * 64])
def test_envelope_hash_requires_exact_lowercase_hex(value: str) -> None:
    vector = _VECTORS["anchor"]
    envelope = json.loads(bytes.fromhex(vector["canonical_envelope_hex"]))
    envelope["anchor_hash"] = value
    _assert_invalid(lambda: _verify(json.dumps(envelope).encode(), vector))


def test_missing_extra_and_nested_signer_fields_reject_without_mutating_input() -> None:
    anchor = _VECTORS["anchor"]["checkpoint"]
    for field in anchor:
        checkpoint = copy.deepcopy(anchor)
        del checkpoint[field]
        _assert_invalid(
            lambda checkpoint=checkpoint: checkpoint_v2.sign_checkpoint(checkpoint, signing_key=_K1)
        )

    extra = copy.deepcopy(anchor)
    extra["extra"] = "value"
    _assert_invalid(lambda: checkpoint_v2.sign_checkpoint(extra, signing_key=_K1))

    nested = copy.deepcopy(anchor)
    nested["timestamp"] = {"secret": "SYNTHETIC"}
    _assert_invalid(lambda: checkpoint_v2.sign_checkpoint(nested, signing_key=_K1))

    class DictSubclass(dict[str, object]):
        pass

    _assert_invalid(lambda: checkpoint_v2.sign_checkpoint(DictSubclass(anchor), signing_key=_K1))


def test_signer_bounds_names_values_and_member_count_before_key_work() -> None:
    anchor = copy.deepcopy(_VECTORS["anchor"]["checkpoint"])
    overlong_value = copy.deepcopy(anchor)
    overlong_value["timestamp"] = "S" * 129
    _assert_invalid(
        lambda: checkpoint_v2.sign_checkpoint(
            overlong_value,
            signing_key=X25519PrivateKey.generate(),  # type: ignore[arg-type]
        )
    )

    overlong_name = copy.deepcopy(anchor)
    overlong_name["x" * 33] = overlong_name.pop("timestamp")
    _assert_invalid(lambda: checkpoint_v2.sign_checkpoint(overlong_name, signing_key=_K1))

    non_ascii_name = copy.deepcopy(anchor)
    non_ascii_name["timestämp"] = non_ascii_name.pop("timestamp")
    _assert_invalid(lambda: checkpoint_v2.sign_checkpoint(non_ascii_name, signing_key=_K1))

    too_many = {f"x{index}": "x" for index in range(17)}
    _assert_invalid(lambda: checkpoint_v2.sign_checkpoint(too_many, signing_key=_K1))


def test_missing_extra_and_duplicate_wire_members_reject_at_both_levels() -> None:
    vector = _VECTORS["anchor"]
    envelope = json.loads(bytes.fromhex(vector["canonical_envelope_hex"]))
    for field in ("checkpoint", "signature", "anchor_hash"):
        missing = copy.deepcopy(envelope)
        del missing[field]
        _assert_invalid(lambda missing=missing: _verify(json.dumps(missing).encode(), vector))

    extra = copy.deepcopy(envelope)
    extra["extra"] = "value"
    _assert_invalid(lambda: _verify(json.dumps(extra).encode(), vector))

    for field in vector["checkpoint"]:
        missing_checkpoint = copy.deepcopy(envelope)
        del missing_checkpoint["checkpoint"][field]
        _assert_invalid(
            lambda missing_checkpoint=missing_checkpoint: _verify(
                json.dumps(missing_checkpoint).encode(), vector
            )
        )

    checkpoint_json = json.dumps(vector["checkpoint"], separators=(",", ":"))
    signature_json = json.dumps(envelope["signature"])
    hash_json = json.dumps(envelope["anchor_hash"])
    duplicate_outer = (
        f'{{"checkpoint":{checkpoint_json},"signature":{signature_json},'
        f'"anchor_hash":{hash_json},"sign\\u0061ture":{signature_json}}}'
    ).encode()
    _assert_invalid(lambda: _verify(duplicate_outer, vector))

    anchor_value = json.dumps(vector["checkpoint"]["anchor_id"])
    duplicate_checkpoint = checkpoint_json.replace(
        '"anchor_id":' + anchor_value,
        '"anchor_id":' + anchor_value + ',"\\u0061nchor_id":' + anchor_value,
        1,
    )
    duplicate_inner = (
        f'{{"checkpoint":{duplicate_checkpoint},"signature":{signature_json},'
        f'"anchor_hash":{hash_json}}}'
    ).encode()
    _assert_invalid(lambda: _verify(duplicate_inner, vector))


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"null",
        b"[]",
        b"{",
        b"{}{}",
        b"\xff",
        b'{"checkpoint":NaN}',
        b'{"checkpoint":Infinity}',
        b'{"checkpoint":1.5}',
        b'{"checkpoint":[1]}',
        b'{"checkpoint":{"x":{"y":"z"}}}',
        b'{"checkpoint":{"kind":"\\ud800"}}',
        b'{"checkpoint":{"format_version":222222222222222222222}}',
    ],
)
def test_transport_parser_rejects_malformed_ambiguous_or_deep_json(data: bytes) -> None:
    _assert_invalid(lambda: _verify(data))


def test_transport_parser_enforces_exact_64kib_bound() -> None:
    vector = _VECTORS["anchor"]
    encoded = bytes.fromhex(vector["canonical_envelope_hex"])
    at_limit = b" " * (65_536 - len(encoded)) + encoded
    assert len(at_limit) == 65_536
    assert _verify(at_limit, vector).canonical_envelope == encoded
    _assert_invalid(lambda: _verify(b" " + at_limit, vector))
    deep_but_sub_limit = b'{"checkpoint":{"x":{"y":"' + b"z" * 60_000 + b'"}}}'
    assert len(deep_but_sub_limit) < 65_536
    _assert_invalid(lambda: _verify(deep_but_sub_limit))


def test_brackets_inside_json_strings_are_data_not_transport_structure() -> None:
    vector = _VECTORS["anchor"]
    envelope = json.loads(bytes.fromhex(vector["canonical_envelope_hex"]))
    envelope["checkpoint"]["previous_anchor_hash"] = "[" + "a" * 63
    data = json.dumps(envelope).encode()
    _assert_invalid(lambda: _verify(data, vector))


def test_verified_record_is_frozen_detached_and_contains_only_immutable_evidence() -> None:
    vector = _VECTORS["anchor"]
    source = copy.deepcopy(vector["checkpoint"])
    encoded = checkpoint_v2.sign_checkpoint(source, signing_key=_K1)
    assert source == vector["checkpoint"]
    result = _verify(encoded, vector)

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.sequence = 999  # type: ignore[misc]
    assert not hasattr(result, "__dict__")
    with pytest.raises(TypeError):
        result.signature[0] = 0  # type: ignore[index]
    source["sequence"] = "99"
    assert result.sequence == 1
    assert result.canonical_envelope == encoded


def test_controlled_errors_redact_input_and_suppress_parser_context() -> None:
    marker = "SYNTHETIC-SENSITIVE-MARKER-9f17"
    malformed = ('{"checkpoint":"' + marker + '",}').encode()
    with pytest.raises(checkpoint_v2.CheckpointV2Error) as caught:
        _verify(malformed)
    rendered = "".join(traceback.format_exception(caught.value))
    assert str(caught.value) == "invalid checkpoint"
    assert marker not in rendered
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True

    oversized = copy.deepcopy(_VECTORS["anchor"]["checkpoint"])
    oversized["timestamp"] = marker * 8
    with pytest.raises(checkpoint_v2.CheckpointV2Error) as signer_caught:
        checkpoint_v2.sign_checkpoint(oversized, signing_key=_K1)
    assert str(signer_caught.value) == "invalid checkpoint"
    assert marker not in "".join(traceback.format_exception(signer_caught.value))


def test_legacy_payload_and_signature_remain_exact_and_v2_never_falls_back() -> None:
    legacy = _REFERENCE["legacy"]
    checkpoint = legacy["checkpoint"]
    timestamp = datetime.datetime.fromisoformat(checkpoint["timestamp"])
    canonical = bytes.fromhex(legacy["canonical_checkpoint_hex"])
    signature = bytes.fromhex(legacy["signature_hex"])

    assert (
        legacy_checkpoint._payload(
            checkpoint["org_id"],
            checkpoint["latest_id"],
            bytes.fromhex(checkpoint["latest_row_hash"]),
            timestamp,
        )
        == canonical
    )
    _K1.public_key().verify(signature, canonical)
    assert legacy_checkpoint.verify_checkpoint_signature(
        _K1.public_key(),
        org_id=checkpoint["org_id"],
        latest_id=checkpoint["latest_id"],
        latest_row_hash=bytes.fromhex(checkpoint["latest_row_hash"]),
        timestamp=timestamp,
        signature=signature,
    )
    with pytest.raises(InvalidSignature):
        _K1.public_key().verify(signature, _SIGNATURE_DOMAIN + canonical)
    _assert_invalid(lambda: _verify(canonical))
