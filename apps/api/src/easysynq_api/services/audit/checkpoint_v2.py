"""Pure codec for authenticated version 2 audit-checkpoint envelopes.

This module authenticates one envelope against an explicitly supplied key and expected
organization/stream.  It does not establish checkpoint lineage, key activation, witness delivery,
freshness, or restore eligibility.
"""

from __future__ import annotations

import base64
import binascii
import dataclasses
import datetime
import hashlib
import hmac
import json
import re
from typing import Literal, NoReturn, cast
from uuid import UUID

import rfc8785
from Crypto.Signature import eddsa
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

_SIGNATURE_DOMAIN = b"EasySynQ/AuditCheckpoint/v2/signature\0"
_HASH_DOMAIN = b"EasySynQ/AuditCheckpoint/v2/hash\0"
_TRANSITION_PROOF_DOMAIN = b"EasySynQ/AuditCheckpoint/v2/key-transition-proof\0"
_ED25519_ORDER = 2**252 + 27742317777372353535851937790883648493
_MAX_TRANSPORT_BYTES = 65_536
_MAX_BIGINT = 9_223_372_036_854_775_807
_KEY_ID_PREFIX = "ed25519-sha256:"

type _JSONValue = bool | int | float | str | list[_JSONValue] | dict[str, _JSONValue] | None

_COMMON_FIELDS = frozenset(
    {
        "format_version",
        "kind",
        "org_id",
        "stream_id",
        "anchor_id",
        "sequence",
        "previous_anchor_hash",
        "key_id",
        "key_epoch",
        "latest_id",
        "latest_row_hash",
        "timestamp",
    }
)
_TRANSITION_FIELDS = frozenset(
    {"next_key_id", "next_public_key", "next_key_epoch", "next_key_signature"}
)
_ENVELOPE_FIELDS = frozenset({"checkpoint", "signature", "anchor_hash"})

_HEX_64 = re.compile(r"[0-9a-f]{64}\Z")
_KEY_ID = re.compile(r"ed25519-sha256:[0-9a-f]{64}\Z")
_DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
_TIMESTAMP = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})T"
    r"([0-9]{2}):([0-9]{2}):([0-9]{2})\.([0-9]{6})Z\Z"
)


class CheckpointV2Error(ValueError):
    """Controlled codec failure whose public text never includes supplied data."""


@dataclasses.dataclass(frozen=True, slots=True)
class VerifiedEnvelope:
    """Authenticated envelope fields; this record makes no checkpoint-lineage claim."""

    format_version: Literal[2]
    kind: Literal["anchor", "key_transition"]
    org_id: UUID
    stream_id: UUID
    anchor_id: UUID
    sequence: int
    previous_anchor_hash: str
    key_id: str
    key_epoch: int
    latest_id: int
    latest_row_hash: str
    timestamp: datetime.datetime
    next_key_id: str | None
    next_public_key: bytes | None
    next_key_epoch: int | None
    next_key_signature: bytes | None
    signature: bytes
    anchor_hash: str
    canonical_checkpoint: bytes
    canonical_envelope: bytes


@dataclasses.dataclass(frozen=True, slots=True)
class _ValidatedCheckpoint:
    kind: Literal["anchor", "key_transition"]
    org_id: UUID
    stream_id: UUID
    anchor_id: UUID
    sequence: int
    previous_anchor_hash: str
    key_id: str
    key_epoch: int
    latest_id: int
    latest_row_hash: str
    timestamp: datetime.datetime
    next_key_id: str | None
    next_public_key: bytes | None
    next_key_epoch: int | None
    next_key_signature: bytes | None


class _DecodeRejected(ValueError):
    pass


def _checkpoint_error() -> NoReturn:
    raise CheckpointV2Error("invalid checkpoint") from None


def _key_error() -> NoReturn:
    raise CheckpointV2Error("invalid checkpoint key") from None


def _raw_public_key(public_key: Ed25519PublicKey) -> bytes:
    if not isinstance(public_key, Ed25519PublicKey):
        _key_error()
    try:
        raw = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
    except (AttributeError, TypeError, ValueError):
        _key_error()
    _validate_raw_public_key(raw)
    return raw


def _private_public_key(signing_key: Ed25519PrivateKey) -> bytes:
    if not isinstance(signing_key, Ed25519PrivateKey):
        _key_error()
    try:
        public_key = signing_key.public_key()
    except (AttributeError, TypeError, ValueError):
        _key_error()
    return _raw_public_key(public_key)


def _validate_raw_public_key(raw_public_key: bytes) -> None:
    if type(raw_public_key) is not bytes or len(raw_public_key) != 32:
        _key_error()
    try:
        parsed_key = eddsa.import_public_key(raw_public_key)
        if parsed_key.export_key(format="raw") != raw_public_key:
            _key_error()
        point = parsed_key.pointQ
        if tuple(map(int, point.xy)) == (0, 1):
            _key_error()
        if tuple(map(int, (point * _ED25519_ORDER).xy)) != (0, 1):
            _key_error()
    except CheckpointV2Error:
        raise
    except (IndexError, TypeError, ValueError):
        _key_error()


def public_key_id(public_key: Ed25519PublicKey) -> str:
    """Return the material-derived ID for an admissible Ed25519 public key."""

    raw_public_key = _raw_public_key(public_key)
    return _KEY_ID_PREFIX + hashlib.sha256(raw_public_key).hexdigest()


def _scan_transport(data: bytes) -> None:
    if type(data) is not bytes or not data or len(data) > _MAX_TRANSPORT_BYTES:
        _checkpoint_error()

    depth = 0
    in_string = False
    escaped = False
    for byte in data:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:
                escaped = True
            elif byte == 0x22:
                in_string = False
            continue

        if byte == 0x22:
            in_string = True
        elif byte in (0x5B, 0x5D):
            _checkpoint_error()
        elif byte == 0x7B:
            depth += 1
            if depth > 2:
                _checkpoint_error()
        elif byte == 0x7D:
            depth -= 1
            if depth < 0:
                _checkpoint_error()

    if in_string or escaped or depth != 0:
        _checkpoint_error()


def _pairs_to_dict(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise _DecodeRejected
        result[name] = value
    return result


def _parse_integer(token: str) -> int:
    if len(token) > 20:
        raise _DecodeRejected
    return int(token)


def _reject_token(_token: str) -> NoReturn:
    raise _DecodeRejected


def _decode_envelope(data: bytes) -> dict[str, object]:
    _scan_transport(data)
    try:
        text = data.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_pairs_to_dict,
            parse_int=_parse_integer,
            parse_float=_reject_token,
            parse_constant=_reject_token,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, _DecodeRejected):
        _checkpoint_error()
    if type(value) is not dict:
        _checkpoint_error()
    return cast(dict[str, object], value)


def _check_member_names(value: dict[str, object], *, maximum: int) -> None:
    if type(value) is not dict or len(value) > maximum:
        _checkpoint_error()
    for name in value:
        if type(name) is not str or not name or len(name) > 32 or not name.isascii():
            _checkpoint_error()


def _check_checkpoint_scalars(checkpoint: dict[str, object]) -> None:
    _check_member_names(checkpoint, maximum=16)
    for value in checkpoint.values():
        if type(value) is int:
            if value != 2:
                _checkpoint_error()
        elif type(value) is str:
            if len(value) > 128 or not value.isascii():
                _checkpoint_error()
        else:
            _checkpoint_error()


def _require_string(checkpoint: dict[str, object], field: str) -> str:
    value = checkpoint[field]
    if type(value) is not str:
        _checkpoint_error()
    return value


def _parse_uuid(checkpoint: dict[str, object], field: str) -> UUID:
    value = _require_string(checkpoint, field)
    if _UUID.fullmatch(value) is None:
        _checkpoint_error()
    try:
        parsed = UUID(value)
    except (AttributeError, ValueError):
        _checkpoint_error()
    if str(parsed) != value:
        _checkpoint_error()
    return parsed


def _parse_decimal(checkpoint: dict[str, object], field: str, *, minimum: int) -> int:
    value = _require_string(checkpoint, field)
    if len(value) > 19 or _DECIMAL.fullmatch(value) is None:
        _checkpoint_error()
    parsed = int(value)
    if parsed < minimum or parsed > _MAX_BIGINT:
        _checkpoint_error()
    return parsed


def _parse_digest(checkpoint: dict[str, object], field: str) -> str:
    value = _require_string(checkpoint, field)
    if _HEX_64.fullmatch(value) is None:
        _checkpoint_error()
    return value


def _parse_key_id(checkpoint: dict[str, object], field: str) -> str:
    value = _require_string(checkpoint, field)
    if _KEY_ID.fullmatch(value) is None:
        _checkpoint_error()
    return value


def _parse_base64(checkpoint: dict[str, object], field: str, *, length: int) -> bytes:
    value = _require_string(checkpoint, field)
    encoded_length = 4 * ((length + 2) // 3)
    if len(value) != encoded_length:
        _checkpoint_error()
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        _checkpoint_error()
    if len(decoded) != length or base64.b64encode(decoded).decode("ascii") != value:
        _checkpoint_error()
    return decoded


def _parse_timestamp(checkpoint: dict[str, object]) -> datetime.datetime:
    value = _require_string(checkpoint, "timestamp")
    match = _TIMESTAMP.fullmatch(value)
    if match is None:
        _checkpoint_error()
    year, month, day, hour, minute, second, microsecond = map(int, match.groups())
    try:
        return datetime.datetime(
            year,
            month,
            day,
            hour,
            minute,
            second,
            microsecond,
            tzinfo=datetime.UTC,
        )
    except ValueError:
        _checkpoint_error()


def _derived_key_id(raw_public_key: bytes) -> str:
    return _KEY_ID_PREFIX + hashlib.sha256(raw_public_key).hexdigest()


def _validate_checkpoint(
    checkpoint: dict[str, object],
    *,
    current_public_key: bytes,
    proof_required: bool,
) -> _ValidatedCheckpoint:
    _check_checkpoint_scalars(checkpoint)
    if checkpoint.get("format_version") != 2 or type(checkpoint.get("format_version")) is not int:
        _checkpoint_error()

    kind_value = checkpoint.get("kind")
    if kind_value == "anchor":
        kind: Literal["anchor", "key_transition"] = "anchor"
        expected_fields = _COMMON_FIELDS
    elif kind_value == "key_transition":
        kind = "key_transition"
        expected_fields = _COMMON_FIELDS | (
            _TRANSITION_FIELDS if proof_required else _TRANSITION_FIELDS - {"next_key_signature"}
        )
    else:
        _checkpoint_error()
    if checkpoint.keys() != expected_fields:
        _checkpoint_error()

    key_id = _parse_key_id(checkpoint, "key_id")
    if key_id != _derived_key_id(current_public_key):
        _checkpoint_error()

    org_id = _parse_uuid(checkpoint, "org_id")
    stream_id = _parse_uuid(checkpoint, "stream_id")
    anchor_id = _parse_uuid(checkpoint, "anchor_id")
    sequence = _parse_decimal(checkpoint, "sequence", minimum=1)
    previous_anchor_hash = _parse_digest(checkpoint, "previous_anchor_hash")
    key_epoch = _parse_decimal(checkpoint, "key_epoch", minimum=0)
    latest_id = _parse_decimal(checkpoint, "latest_id", minimum=1)
    latest_row_hash = _parse_digest(checkpoint, "latest_row_hash")
    timestamp = _parse_timestamp(checkpoint)

    next_key_id: str | None = None
    next_public_key: bytes | None = None
    next_key_epoch: int | None = None
    next_key_signature: bytes | None = None
    if kind == "key_transition":
        next_key_id = _parse_key_id(checkpoint, "next_key_id")
        next_public_key = _parse_base64(checkpoint, "next_public_key", length=32)
        _validate_raw_public_key(next_public_key)
        if next_key_id != _derived_key_id(next_public_key) or next_key_id == key_id:
            _checkpoint_error()
        next_key_epoch = _parse_decimal(checkpoint, "next_key_epoch", minimum=0)
        if key_epoch == _MAX_BIGINT or next_key_epoch != key_epoch + 1:
            _checkpoint_error()
        if proof_required:
            next_key_signature = _parse_base64(checkpoint, "next_key_signature", length=64)

    return _ValidatedCheckpoint(
        kind=kind,
        org_id=org_id,
        stream_id=stream_id,
        anchor_id=anchor_id,
        sequence=sequence,
        previous_anchor_hash=previous_anchor_hash,
        key_id=key_id,
        key_epoch=key_epoch,
        latest_id=latest_id,
        latest_row_hash=latest_row_hash,
        timestamp=timestamp,
        next_key_id=next_key_id,
        next_public_key=next_public_key,
        next_key_epoch=next_key_epoch,
        next_key_signature=next_key_signature,
    )


def _canonicalize(value: dict[str, object]) -> bytes:
    try:
        return rfc8785.dumps(cast(dict[str, _JSONValue], value))
    except (TypeError, ValueError):
        _checkpoint_error()


def sign_checkpoint(
    checkpoint: dict[str, object],
    *,
    signing_key: Ed25519PrivateKey,
    next_signing_key: Ed25519PrivateKey | None = None,
) -> bytes:
    """Validate and sign one complete wire-shaped checkpoint payload."""

    if type(checkpoint) is not dict:
        _checkpoint_error()
    _check_checkpoint_scalars(checkpoint)
    current_raw = _private_public_key(signing_key)
    working = dict(checkpoint)
    validated = _validate_checkpoint(
        working,
        current_public_key=current_raw,
        proof_required=False,
    )

    if validated.kind == "anchor":
        if next_signing_key is not None:
            _checkpoint_error()
    else:
        if next_signing_key is None:
            _checkpoint_error()
        next_raw = _private_public_key(next_signing_key)
        if next_raw != validated.next_public_key:
            _checkpoint_error()
        proof_payload = _canonicalize(working)
        try:
            proof = next_signing_key.sign(_TRANSITION_PROOF_DOMAIN + proof_payload)
        except (TypeError, ValueError):
            _checkpoint_error()
        working["next_key_signature"] = base64.b64encode(proof).decode("ascii")
        _validate_checkpoint(
            working,
            current_public_key=current_raw,
            proof_required=True,
        )

    canonical_checkpoint = _canonicalize(working)
    try:
        signature = signing_key.sign(_SIGNATURE_DOMAIN + canonical_checkpoint)
    except (TypeError, ValueError):
        _checkpoint_error()
    anchor_hash = hashlib.sha256(_HASH_DOMAIN + canonical_checkpoint + signature).hexdigest()
    envelope: dict[str, object] = {
        "checkpoint": working,
        "signature": base64.b64encode(signature).decode("ascii"),
        "anchor_hash": anchor_hash,
    }
    return _canonicalize(envelope)


def verify_envelope(
    data: bytes,
    *,
    public_key: Ed25519PublicKey,
    org_id: UUID,
    stream_id: UUID,
) -> VerifiedEnvelope:
    """Authenticate one v2 envelope without making a lineage or key-activation claim."""

    if type(org_id) is not UUID or type(stream_id) is not UUID:
        _checkpoint_error()
    envelope = _decode_envelope(data)
    _check_member_names(envelope, maximum=3)
    if envelope.keys() != _ENVELOPE_FIELDS:
        _checkpoint_error()
    checkpoint_value = envelope["checkpoint"]
    if type(checkpoint_value) is not dict:
        _checkpoint_error()
    checkpoint = cast(dict[str, object], checkpoint_value)
    signature = _parse_base64(envelope, "signature", length=64)
    anchor_hash = _parse_digest(envelope, "anchor_hash")

    current_raw = _raw_public_key(public_key)
    validated = _validate_checkpoint(
        checkpoint,
        current_public_key=current_raw,
        proof_required=True,
    )
    if validated.org_id != org_id or validated.stream_id != stream_id:
        _checkpoint_error()

    canonical_checkpoint = _canonicalize(checkpoint)
    if validated.kind == "key_transition":
        if validated.next_public_key is None or validated.next_key_signature is None:
            _checkpoint_error()
        proof_checkpoint = dict(checkpoint)
        del proof_checkpoint["next_key_signature"]
        proof_payload = _canonicalize(proof_checkpoint)
        try:
            next_public_key = Ed25519PublicKey.from_public_bytes(validated.next_public_key)
            next_public_key.verify(
                validated.next_key_signature,
                _TRANSITION_PROOF_DOMAIN + proof_payload,
            )
        except (InvalidSignature, TypeError, ValueError):
            _checkpoint_error()

    try:
        public_key.verify(signature, _SIGNATURE_DOMAIN + canonical_checkpoint)
    except (InvalidSignature, TypeError, ValueError):
        _checkpoint_error()
    derived_hash = hashlib.sha256(_HASH_DOMAIN + canonical_checkpoint + signature).hexdigest()
    if not hmac.compare_digest(anchor_hash, derived_hash):
        _checkpoint_error()

    canonical_envelope = _canonicalize(
        {
            "checkpoint": checkpoint,
            "signature": base64.b64encode(signature).decode("ascii"),
            "anchor_hash": anchor_hash,
        }
    )
    return VerifiedEnvelope(
        format_version=2,
        kind=validated.kind,
        org_id=validated.org_id,
        stream_id=validated.stream_id,
        anchor_id=validated.anchor_id,
        sequence=validated.sequence,
        previous_anchor_hash=validated.previous_anchor_hash,
        key_id=validated.key_id,
        key_epoch=validated.key_epoch,
        latest_id=validated.latest_id,
        latest_row_hash=validated.latest_row_hash,
        timestamp=validated.timestamp,
        next_key_id=validated.next_key_id,
        next_public_key=validated.next_public_key,
        next_key_epoch=validated.next_key_epoch,
        next_key_signature=validated.next_key_signature,
        signature=signature,
        anchor_hash=anchor_hash,
        canonical_checkpoint=canonical_checkpoint,
        canonical_envelope=canonical_envelope,
    )
