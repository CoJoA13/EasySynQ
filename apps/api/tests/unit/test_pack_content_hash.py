"""Pure tests for the evidence-pack seal (``pack_content_hash``, doc 06 §7.4): domain separation,
order/duplicate independence, and sensitivity to scope / exclusion changes."""

from __future__ import annotations

from easysynq_api.domain.packs.content_hash import PREAMBLE, pack_content_hash
from easysynq_api.domain.records.content_hash import record_content_hash


def _h(**overrides: object) -> str:
    base: dict[str, object] = {
        "scope_kind": "CLAUSE",
        "scope_selector": {"clause_ids": ["c1", "c2"]},
        "period_start": None,
        "period_end": None,
        "included_record_ids": ["r1", "r2"],
        "pinned_version_ids": ["v1"],
        "evidence_sha256s": ["ab", "cd"],
        "excluded_permission_record_ids": [],
        "excluded_absence_record_ids": [],
    }
    base.update(overrides)
    return pack_content_hash(**base)  # type: ignore[arg-type]


def test_prefix_and_preamble() -> None:
    assert PREAMBLE == b"easysynq.evidencepack.v1\n"
    assert _h().startswith("sha256:")


def test_order_and_duplicate_independent() -> None:
    a = _h(
        scope_selector={"clause_ids": ["c2", "c1"]},
        included_record_ids=["r2", "r1"],
        evidence_sha256s=["CD", "ab", "ab"],  # different order, a dup, mixed case
    )
    assert a == _h()


def test_domain_separated_from_record_hash() -> None:
    # The same evidence manifest under the pack preamble must never collide with a record digest.
    p = _h(
        scope_selector={},
        included_record_ids=[],
        pinned_version_ids=[],
        evidence_sha256s=["ab"],
    )
    r = record_content_hash(
        record_type="EVIDENCE",
        source_version_id=None,
        form_field_values=None,
        evidence_sha256s=["ab"],
    )
    assert p != r


def test_scope_and_exclusion_change_the_seal() -> None:
    base = _h()
    assert _h(scope_selector={"clause_ids": ["c1"]}) != base
    assert _h(excluded_permission_record_ids=["rX"]) != base
    assert _h(excluded_absence_record_ids=["rY"]) != base
    assert _h(period_start="2025-01-01") != base
