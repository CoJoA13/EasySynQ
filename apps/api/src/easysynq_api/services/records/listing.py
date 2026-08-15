"""Pure criteria normalization and opaque cursor helpers for the records list."""

from __future__ import annotations

import base64
import binascii
import dataclasses
import datetime
import hashlib
import hmac
import json
import string
import uuid

from ...db.models._record_enums import RecordDispositionState, RecordType

_CURSOR_VERSION = 1
_CURSOR_KEYS = frozenset({"v", "captured_at", "id", "query"})
_MAX_RECORD_SEARCH_LENGTH = 200
_URLSAFE_BASE64_ALPHABET = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
)


@dataclasses.dataclass(frozen=True, slots=True)
class RecordListCriteria:
    q: str | None = None
    record_type: RecordType | None = None
    source_document_id: uuid.UUID | None = None
    captured_by: uuid.UUID | None = None
    disposition_state: RecordDispositionState | None = None
    legal_hold: bool | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class RecordListCursor:
    captured_at: datetime.datetime
    record_id: uuid.UUID


class InvalidRecordCursor(ValueError):
    """Raised when a cursor is malformed or was issued for different criteria."""


def normalize_record_search(q: str | None) -> str | None:
    """Trim a records search string, treating whitespace-only input as absent."""
    if q is None:
        return None
    normalized = q.strip()
    if not normalized:
        return None
    if len(normalized) > _MAX_RECORD_SEARCH_LENGTH:
        raise ValueError("record search must be at most 200 characters")
    return normalized


def escape_ilike_literal(value: str) -> str:
    """Escape PostgreSQL ILIKE wildcard characters while retaining literal semantics."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _criteria_fingerprint(criteria: RecordListCriteria) -> str:
    search = normalize_record_search(criteria.q)
    normalized = {
        "captured_by": str(criteria.captured_by) if criteria.captured_by is not None else None,
        "disposition_state": (
            criteria.disposition_state.value if criteria.disposition_state is not None else None
        ),
        "legal_hold": criteria.legal_hold,
        # Cursor identity follows the exact trimmed Unicode query. PostgreSQL ILIKE may match the
        # same rows for some case variants, but that does not make distinct client criteria
        # interchangeable; casefold can also expand Unicode (for example, ß → ss).
        "q": search,
        "record_type": criteria.record_type.value if criteria.record_type is not None else None,
        "source_document_id": (
            str(criteria.source_document_id) if criteria.source_document_id is not None else None
        ),
    }
    serialized = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def encode_record_cursor(boundary: RecordListCursor, criteria: RecordListCriteria) -> str:
    """Encode a keyset boundary and criteria fingerprint as unpadded URL-safe base64."""
    if boundary.captured_at.tzinfo is None or boundary.captured_at.utcoffset() is None:
        raise ValueError("cursor captured_at must be timezone-aware")
    payload = {
        "v": _CURSOR_VERSION,
        "captured_at": boundary.captured_at.isoformat(),
        "id": str(boundary.record_id),
        "query": _criteria_fingerprint(criteria),
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return base64.urlsafe_b64encode(serialized.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_payload(token: str) -> object:
    if not isinstance(token, str) or not token or not token.isascii():
        raise InvalidRecordCursor("invalid cursor")
    if any(character not in _URLSAFE_BASE64_ALPHABET for character in token):
        raise InvalidRecordCursor("invalid cursor")
    if len(token) % 4 == 1:
        raise InvalidRecordCursor("invalid cursor")
    try:
        raw = base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True)
        return json.loads(raw.decode("utf-8"))
    except (binascii.Error, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise InvalidRecordCursor("invalid cursor") from exc


def decode_record_cursor(token: str, criteria: RecordListCriteria) -> RecordListCursor:
    """Decode and validate a cursor, binding it to the supplied normalized criteria."""
    payload = _decode_payload(token)
    try:
        if not isinstance(payload, dict) or set(payload) != _CURSOR_KEYS:
            raise ValueError("unexpected cursor payload keys")
        version = payload["v"]
        if type(version) is not int or version != _CURSOR_VERSION:
            raise ValueError("unsupported cursor version")

        captured_at_value = payload["captured_at"]
        record_id_value = payload["id"]
        query = payload["query"]
        if not isinstance(captured_at_value, str) or not isinstance(record_id_value, str):
            raise ValueError("invalid cursor field types")
        if not isinstance(query, str):
            raise ValueError("invalid cursor query")
        if len(query) != 64 or any(character not in string.hexdigits for character in query):
            raise ValueError("invalid cursor query")

        captured_at = datetime.datetime.fromisoformat(captured_at_value)
        if captured_at.tzinfo is None or captured_at.utcoffset() is None:
            raise ValueError("cursor timestamp must be timezone-aware")
        record_id = uuid.UUID(record_id_value)
        expected_query = _criteria_fingerprint(criteria)
        if not hmac.compare_digest(query, expected_query):
            raise InvalidRecordCursor("cursor does not match query")
        return RecordListCursor(captured_at=captured_at, record_id=record_id)
    except InvalidRecordCursor:
        raise
    except (AttributeError, TypeError, ValueError, OverflowError) as exc:
        raise InvalidRecordCursor("invalid cursor") from exc
