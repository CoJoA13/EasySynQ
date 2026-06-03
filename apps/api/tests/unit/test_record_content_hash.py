"""S-rec-1 unit proofs — the pure record content-hash seal (doc 06 §4.4). No DB."""

from __future__ import annotations

import hashlib
import uuid

import rfc8785

from easysynq_api.domain.records.content_hash import PREAMBLE, record_content_hash

_VID = uuid.UUID("11111111-1111-1111-1111-111111111111")


def test_hash_is_deterministic() -> None:
    kw = dict(
        record_type="CALIBRATION",
        source_version_id=_VID,
        form_field_values={"a": 1, "b": 2},
        evidence_sha256s=["aa" * 32, "bb" * 32],
    )
    assert record_content_hash(**kw) == record_content_hash(**kw)  # type: ignore[arg-type]


def test_manifest_order_and_duplicate_independent() -> None:
    # sorted(set(lower(...))) → re-attaching the same blobs in any order, with a dup + mixed case,
    # yields the identical seal.
    a = record_content_hash(
        record_type="EVIDENCE",
        source_version_id=None,
        form_field_values=None,
        evidence_sha256s=["BB" * 32, "aa" * 32, "aa" * 32],
    )
    b = record_content_hash(
        record_type="EVIDENCE",
        source_version_id=None,
        form_field_values=None,
        evidence_sha256s=["aa" * 32, "bb" * 32],
    )
    assert a == b


def test_form_field_values_key_order_independent() -> None:
    # RFC 8785 JCS canonicalises key order → insertion order doesn't change the seal.
    a = record_content_hash(
        record_type="FILLED_FORM",
        source_version_id=None,
        form_field_values={"z": 1, "a": 2, "m": 3},
        evidence_sha256s=[],
    )
    b = record_content_hash(
        record_type="FILLED_FORM",
        source_version_id=None,
        form_field_values={"a": 2, "m": 3, "z": 1},
        evidence_sha256s=[],
    )
    assert a == b


def test_null_vs_set_source_version_differ() -> None:
    base = dict(record_type="RELEASE", form_field_values=None, evidence_sha256s=[])
    null = record_content_hash(source_version_id=None, **base)  # type: ignore[arg-type]
    setv = record_content_hash(source_version_id=_VID, **base)  # type: ignore[arg-type]
    assert null != setv


def test_record_type_binds_into_seal() -> None:
    a = record_content_hash(
        record_type="CALIBRATION",
        source_version_id=None,
        form_field_values=None,
        evidence_sha256s=[],
    )
    b = record_content_hash(
        record_type="COMPETENCE",
        source_version_id=None,
        form_field_values=None,
        evidence_sha256s=[],
    )
    assert a != b


def test_preamble_domain_separation() -> None:
    # The seal must differ from a bare JCS hash WITHOUT the preamble → a record digest can never
    # collide with an audit digest or a future v2 (the canonical.py domain-separation precedent).
    obj = {
        "v": 1,
        "record_type": "EVIDENCE",
        "source_version_id": None,
        "form_field_values": None,
        "evidence_manifest": [],
    }
    bare = "sha256:" + hashlib.sha256(rfc8785.dumps(obj)).hexdigest()
    sealed = record_content_hash(
        record_type="EVIDENCE", source_version_id=None, form_field_values=None, evidence_sha256s=[]
    )
    assert sealed != bare
    assert PREAMBLE == b"easysynq.record.v1\n"


def test_golden_vector() -> None:
    # Pins the v1 byte spec — any change to the serialization (preamble, JCS, field set, manifest
    # normalisation) breaks this on purpose. Regenerate ONLY with a deliberate version bump.
    assert (
        record_content_hash(
            record_type="CALIBRATION",
            source_version_id=_VID,
            form_field_values={"b": 2, "a": 1},
            evidence_sha256s=["BB" * 32, "aa" * 32, "aa" * 32],
        )
        == "sha256:37dced8f371cc53b54aadee98f70e6fd2c844cfd59cf4a3c6d2f49e326b961fc"
    )
