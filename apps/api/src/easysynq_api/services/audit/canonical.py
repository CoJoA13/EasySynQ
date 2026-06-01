"""``canonical_serialize`` v1 — the FROZEN normative byte-spec for the audit hash chain.

This is the single most safety-critical serializer in the system: ``row_hash`` is computed over its
output, so ANY change to these bytes silently breaks every downstream chain link. It is therefore
pinned by ``system_config.canonical_serialize_version = 1`` (R12/D-4) and guarded by a committed
golden-vector test (``tests/unit/test_audit_canonical.py``). Do not change the encoding without
bumping the version and writing a v2 alongside v1.

Construction (D-4 + doc 12 §4.3):

    row_hash = SHA-256( PREAMBLE + concat( TLV(field) for field in ORDER ) )

* PREAMBLE = ``b"easysynq.audit.v1\n"`` — domain separation; binds the version into the hash so a
  future v2 can never collide with a v1 digest.
* ORDER = the 18 items verbatim from doc 12 §4.3: ``id, org_id, occurred_at, actor_id, actor_type,
  event_type, object_type, object_id, scope_ref, reason, before, after, request_id, client_ip,
  user_agent, auth_context, signature_event_id, prev_hash``. ``on_behalf_of`` (reserved-empty in
  v1), ``row_hash`` (the output) and ``chained_at`` (link metadata) are NOT hashed.
* TLV = ``<1 type-tag byte><8-byte big-endian length><value bytes>``. Tags: ``0x00`` NULL (length 0,
  no value — distinct from an empty string ``0x01`` len 0), ``0x01`` UTF-8 text, ``0x02`` raw bytes.
  The fixed-width length prefix makes field boundaries unforgeable (stronger than any delimiter).
* Per-type value bytes: bigint → ASCII decimal; uuid → lowercase 8-4-4-4-12; ``occurred_at`` → UTC
  ``YYYY-MM-DDTHH:MM:SS.ffffffZ`` (fixed 6-digit, literal ``Z``); enum → its string value; text →
  stored UTF-8 verbatim (no NFC); ``client_ip`` → canonical inet text (host ``/32``/``/128`` suffix
  stripped); ``before``/``after``/``auth_context`` → RFC 8785 JCS (the ``rfc8785`` package) of the
  value; ``prev_hash`` → 32 raw bytes (``0x02``). The genesis ``prev_hash`` is 32 zero bytes.
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import uuid
from typing import Any

import rfc8785

CANONICAL_SERIALIZE_VERSION = 1
PREAMBLE = b"easysynq.audit.v1\n"
GENESIS_HASH = bytes(32)  # 32 zero bytes — the first row's prev_hash in an org chain

_TAG_NULL = 0x00
_TAG_TEXT = 0x01
_TAG_BYTES = 0x02
_NULL_TLV = bytes([_TAG_NULL]) + (0).to_bytes(8, "big")


@dataclasses.dataclass(frozen=True, slots=True)
class AuditRow:
    """The exact field projection that is hashed (the 17 §4.3 data fields; ``prev_hash`` is passed
    separately by the linker). Enum fields are their string values. Decoupled from the ORM so the
    serializer is a pure, DB-free, golden-vector-testable function."""

    id: int
    org_id: uuid.UUID
    occurred_at: datetime.datetime
    actor_id: uuid.UUID | None
    actor_type: str
    event_type: str
    object_type: str
    object_id: uuid.UUID | None
    scope_ref: str | None
    reason: str | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    request_id: uuid.UUID | None
    client_ip: str | None
    user_agent: str | None
    auth_context: dict[str, Any] | None
    signature_event_id: uuid.UUID | None


def _tlv(tag: int, value: bytes) -> bytes:
    return bytes([tag]) + len(value).to_bytes(8, "big") + value


def _text(value: str | None) -> bytes:
    return _NULL_TLV if value is None else _tlv(_TAG_TEXT, value.encode("utf-8"))


def _raw(value: bytes) -> bytes:
    return _tlv(_TAG_BYTES, value)


def _uuid(value: uuid.UUID | None) -> bytes:
    return _text(str(value).lower() if value is not None else None)


def _bigint(value: int) -> bytes:
    return _text(str(value))


def _timestamp(value: datetime.datetime) -> bytes:
    dt = value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.UTC)
    dt = dt.astimezone(datetime.UTC)
    return _text(dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z")


def _json(value: dict[str, Any] | None) -> bytes:
    return _NULL_TLV if value is None else _tlv(_TAG_TEXT, rfc8785.dumps(value))


def _canonical_inet(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value)
    if text.endswith("/32") or text.endswith("/128"):
        text = text.rsplit("/", 1)[0]
    return text


def canonical_serialize(row: AuditRow, prev_hash: bytes, *, version: int = 1) -> bytes:
    """Return the canonical byte string hashed to produce ``row_hash``. ``prev_hash`` is the prior
    row's ``row_hash`` (or :data:`GENESIS_HASH` for the first row in the org chain)."""
    if version != CANONICAL_SERIALIZE_VERSION:
        raise ValueError(f"unsupported canonical_serialize version {version}")
    return PREAMBLE + b"".join(
        (
            _bigint(row.id),
            _uuid(row.org_id),
            _timestamp(row.occurred_at),
            _uuid(row.actor_id),
            _text(row.actor_type),
            _text(row.event_type),
            _text(row.object_type),
            _uuid(row.object_id),
            _text(row.scope_ref),
            _text(row.reason),
            _json(row.before),
            _json(row.after),
            _uuid(row.request_id),
            _text(_canonical_inet(row.client_ip)),
            _text(row.user_agent),
            _json(row.auth_context),
            _uuid(row.signature_event_id),
            _raw(prev_hash),
        )
    )


def compute_row_hash(row: AuditRow, prev_hash: bytes, *, version: int = 1) -> bytes:
    """SHA-256 of :func:`canonical_serialize` — the 32-byte ``row_hash`` stored as ``bytea``."""
    return hashlib.sha256(canonical_serialize(row, prev_hash, version=version)).digest()


def audit_row_from_orm(event: Any) -> AuditRow:
    """Project an ``AuditEvent`` ORM row onto the hashed field set (enum → ``.value``)."""
    return AuditRow(
        id=event.id,
        org_id=event.org_id,
        occurred_at=event.occurred_at,
        actor_id=event.actor_id,
        actor_type=event.actor_type.value,
        event_type=event.event_type.value,
        object_type=event.object_type.value,
        object_id=event.object_id,
        scope_ref=event.scope_ref,
        reason=event.reason,
        before=event.before,
        after=event.after,
        request_id=event.request_id,
        client_ip=event.client_ip,
        user_agent=event.user_agent,
        auth_context=event.auth_context,
        signature_event_id=event.signature_event_id,
    )
