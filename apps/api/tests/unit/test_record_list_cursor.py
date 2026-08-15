from __future__ import annotations

import base64
import dataclasses
import datetime
import json
import uuid

import pytest

from easysynq_api.db.models._record_enums import RecordDispositionState, RecordType
from easysynq_api.services.records.listing import (
    InvalidRecordCursor,
    RecordListCriteria,
    RecordListCursor,
    decode_record_cursor,
    encode_record_cursor,
    escape_ilike_literal,
    normalize_record_search,
)

_BOUNDARY = RecordListCursor(
    captured_at=datetime.datetime(2026, 8, 14, 12, 0, tzinfo=datetime.UTC),
    record_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
)


def _payload_token(payload: object) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def test_cursor_round_trips_and_binds_normalized_query() -> None:
    criteria = RecordListCriteria(q="needle", legal_hold=False)

    token = encode_record_cursor(_BOUNDARY, criteria)

    assert "=" not in token
    assert decode_record_cursor(token, criteria) == _BOUNDARY
    with pytest.raises(InvalidRecordCursor, match="query"):
        decode_record_cursor(token, dataclasses.replace(criteria, q="different"))


def test_cursor_payload_has_opaque_query_fingerprint_and_exact_keys() -> None:
    criteria = RecordListCriteria(
        q="  NeedLe ",
        record_type=RecordType.AUDIT,
        source_document_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        captured_by=uuid.UUID("33333333-3333-3333-3333-333333333333"),
        disposition_state=RecordDispositionState.ACTIVE,
        legal_hold=True,
    )

    token = encode_record_cursor(_BOUNDARY, criteria)
    raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    payload = json.loads(raw)

    assert set(payload) == {"v", "captured_at", "id", "query"}
    assert payload["v"] == 1
    assert payload["query"] != "NeedLe"
    assert len(payload["query"]) == 64


def test_casefolded_and_trimmed_search_has_stable_fingerprint() -> None:
    left = encode_record_cursor(_BOUNDARY, RecordListCriteria(q="  NeedLe "))
    right = encode_record_cursor(_BOUNDARY, RecordListCriteria(q="needle"))

    assert left == right


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, None), ("", None), ("  ", None), ("  foo  ", "foo")],
)
def test_normalize_record_search_trims_and_converts_blank_to_none(
    raw: str | None, expected: str | None
) -> None:
    assert normalize_record_search(raw) == expected


def test_normalize_record_search_accepts_200_trimmed_characters() -> None:
    assert normalize_record_search(f"  {'x' * 200}  ") == "x" * 200


def test_normalize_record_search_rejects_201_trimmed_characters() -> None:
    with pytest.raises(ValueError, match="200"):
        normalize_record_search(f"  {'x' * 201}  ")


def test_search_escape_treats_like_metacharacters_literally() -> None:
    assert escape_ilike_literal(r"50%_done\\") == r"50\%\_done\\\\"


@pytest.mark.parametrize(
    "token",
    [
        "not-base64!!!",
        _payload_token(["not", "an", "object"]),
        _payload_token({"v": 1}),
        _payload_token({"v": 1, "captured_at": "not-json"}),
    ],
)
def test_decode_rejects_malformed_base64_json_and_payload_shape(token: str) -> None:
    with pytest.raises(InvalidRecordCursor, match=r"^invalid cursor$"):
        decode_record_cursor(token, RecordListCriteria())


def test_decode_rejects_extra_payload_keys() -> None:
    token = encode_record_cursor(_BOUNDARY, RecordListCriteria())
    raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    payload = {**json.loads(raw), "extra": True}

    with pytest.raises(InvalidRecordCursor, match=r"^invalid cursor$"):
        decode_record_cursor(_payload_token(payload), RecordListCriteria())


def test_decode_rejects_unsupported_version() -> None:
    payload = {
        "v": 2,
        "captured_at": _BOUNDARY.captured_at.isoformat(),
        "id": str(_BOUNDARY.record_id),
        "query": "0" * 64,
    }

    with pytest.raises(InvalidRecordCursor, match=r"^invalid cursor$"):
        decode_record_cursor(_payload_token(payload), RecordListCriteria())


def test_decode_rejects_naive_timestamp() -> None:
    payload = {
        "v": 1,
        "captured_at": "2026-08-14T12:00:00",
        "id": str(_BOUNDARY.record_id),
        "query": "0" * 64,
    }

    with pytest.raises(InvalidRecordCursor, match=r"^invalid cursor$"):
        decode_record_cursor(_payload_token(payload), RecordListCriteria())


def test_cursor_round_trip_preserves_utc_aware_timestamp() -> None:
    decoded = decode_record_cursor(
        encode_record_cursor(_BOUNDARY, RecordListCriteria()), RecordListCriteria()
    )

    assert decoded.captured_at.tzinfo is not None
    assert decoded.captured_at.utcoffset() == datetime.timedelta(0)
