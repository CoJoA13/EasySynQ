"""Golden-vector test for ``canonical_serialize`` v1 (D-4 freeze, AC#6b).

These constants are the IMMUTABLE witness of the audit hash-chain byte format. They were generated
by the first implementation and reviewed field-by-field against the doc 12 §4.3 / D-4 spec before
freezing (the circularity break). If ``canonical_serialize`` ever drifts by a single byte, this test
fails — which is the point: the chain format is a tested invariant no refactor can silently change.
Bumping the format requires ``canonical_serialize_version = 2`` and a new vector alongside this one.
"""

from __future__ import annotations

import dataclasses
import datetime
import uuid

import pytest
import rfc8785

from easysynq_api.services.audit.canonical import (
    GENESIS_HASH,
    PREAMBLE,
    AuditRow,
    canonical_serialize,
    compute_row_hash,
)

# The frozen golden row — every field set to a known constant (before = SQL NULL,
# signature_event_id = SQL NULL, prev_hash = genesis).
_GOLDEN_ROW = AuditRow(
    id=1,
    org_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
    occurred_at=datetime.datetime(2026, 6, 1, 0, 0, 0, tzinfo=datetime.UTC),
    actor_id=uuid.UUID("00000000-0000-0000-0000-0000000000a1"),
    actor_type="user",
    event_type="APPROVED",
    object_type="version",
    object_id=uuid.UUID("00000000-0000-0000-0000-0000000000b2"),
    scope_ref="/quality/sop",
    reason="golden vector freeze",
    before=None,
    after={"state": "Effective", "version_seq": 3},
    request_id=uuid.UUID("00000000-0000-0000-0000-0000000000c3"),
    client_ip="203.0.113.7",
    user_agent="pytest",
    auth_context={"acr": "SESSION", "amr": ["pwd"]},
    signature_event_id=None,
)

_GOLDEN_PAYLOAD_HEX = (
    "6561737973796e712e61756469742e76310a0100000000000000013101000000000000002430303030303030"
    "302d303030302d303030302d303030302d30303030303030303030303101000000000000001b323032362d30"
    "362d30315430303a30303a30302e3030303030305a01000000000000002430303030303030302d303030302d"
    "303030302d303030302d30303030303030303030613101000000000000000475736572010000000000000008"
    "415050524f56454401000000000000000776657273696f6e01000000000000002430303030303030302d3030"
    "30302d303030302d303030302d30303030303030303030623201000000000000000c2f7175616c6974792f73"
    "6f70010000000000000014676f6c64656e20766563746f7220667265657a6500000000000000000001000000"
    "00000000257b227374617465223a22456666656374697665222c2276657273696f6e5f736571223a337d0100"
    "0000000000002430303030303030302d303030302d303030302d303030302d30303030303030303030633301"
    "000000000000000b3230332e302e3131332e3701000000000000000670797465737401000000000000001f7b"
    "22616372223a2253455353494f4e222c22616d72223a5b22707764225d7d0000000000000000000200000000"
    "000000200000000000000000000000000000000000000000000000000000000000000000"
)
_GOLDEN_ROW_HASH_HEX = "f2f32d26e954b11889073d3c2c7f3e54d52586c9c2914fc135e7150e8655952e"


def test_golden_payload_bytes_are_frozen() -> None:
    assert canonical_serialize(_GOLDEN_ROW, GENESIS_HASH).hex() == _GOLDEN_PAYLOAD_HEX


def test_golden_row_hash_is_frozen() -> None:
    assert compute_row_hash(_GOLDEN_ROW, GENESIS_HASH).hex() == _GOLDEN_ROW_HASH_HEX


def test_jcs_subvector_pins_the_encoder() -> None:
    # Pin the rfc8785 JCS output (sorted keys, compact) for the two jsonb fields in the golden
    # row, so a JCS library/version change that altered the bytes is caught on its own.
    assert (
        rfc8785.dumps({"state": "Effective", "version_seq": 3})
        == b'{"state":"Effective","version_seq":3}'
    )
    assert rfc8785.dumps({"acr": "SESSION", "amr": ["pwd"]}) == b'{"acr":"SESSION","amr":["pwd"]}'


def test_preamble_and_genesis_are_frozen() -> None:
    assert PREAMBLE == b"easysynq.audit.v1\n"
    assert GENESIS_HASH == bytes(32)


def test_null_distinct_from_empty_string() -> None:
    # A NULL field (tag 0x00) and an empty-string field (tag 0x01, len 0) MUST hash differently.
    null_row = dataclasses.replace(_GOLDEN_ROW, reason=None)
    empty_row = dataclasses.replace(_GOLDEN_ROW, reason="")
    assert compute_row_hash(null_row, GENESIS_HASH) != compute_row_hash(empty_row, GENESIS_HASH)


def test_prev_hash_threads_the_chain() -> None:
    # Same row, different prev_hash → different row_hash (the chain link is load-bearing).
    h1 = compute_row_hash(_GOLDEN_ROW, GENESIS_HASH)
    h2 = compute_row_hash(_GOLDEN_ROW, b"\x11" * 32)
    assert h1 != h2


def test_occurred_at_naive_treated_as_utc() -> None:
    naive = dataclasses.replace(_GOLDEN_ROW, occurred_at=datetime.datetime(2026, 6, 1, 0, 0, 0))
    assert compute_row_hash(naive, GENESIS_HASH).hex() == _GOLDEN_ROW_HASH_HEX


def test_unsupported_version_rejected() -> None:
    with pytest.raises(ValueError, match="version"):
        canonical_serialize(_GOLDEN_ROW, GENESIS_HASH, version=2)
