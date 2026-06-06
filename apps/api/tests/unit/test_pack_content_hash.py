"""Pure tests for the evidence-pack seal (``pack_content_hash``, doc 06 §7.4): domain separation,
order/duplicate independence, and sensitivity to scope / exclusion changes."""

from __future__ import annotations

from easysynq_api.domain.packs.content_hash import (
    PREAMBLE,
    PREAMBLE_V1,
    PREAMBLE_V2,
    pack_content_hash,
)
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


def test_dossier_digest_is_v2_and_backward_compatible() -> None:
    # S-aud-capa-pack: a FINDING/CAPA pack folds a dossier_digest into the seal (v2). CLAUSE/PROCESS
    # packs (dossier_digest omitted OR explicitly None) stay byte-identical to S-pack-1 (v1).
    assert PREAMBLE_V1 == b"easysynq.evidencepack.v1\n"
    assert PREAMBLE_V2 == b"easysynq.evidencepack.v2\n"
    assert PREAMBLE == PREAMBLE_V1  # back-compat alias
    base = _h()
    assert _h(dossier_digest=None) == base  # None is the v1 path — unchanged
    with_dossier = _h(dossier_digest="sha256:deadbeef")
    assert with_dossier != base  # v2 preamble + the digest folded in
    assert with_dossier == _h(dossier_digest="sha256:deadbeef")  # deterministic
    assert with_dossier != _h(dossier_digest="sha256:feedface")  # a different dossier reseal
