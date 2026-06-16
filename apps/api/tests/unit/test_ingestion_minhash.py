"""Unit tests for the S-ing-3 deterministic MinHash (doc 09 §7.1): signature stability (a pinned
value catches a hash()/seed regression), the exact-Jaccard cluster decision, sub-k/empty edge cases,
and LSH candidate recall at the 0.85 threshold."""

from __future__ import annotations

import pytest

from easysynq_api.domain.ingestion import minhash as mh

# Constructed shingle sets with known exact Jaccard: J(A,B)=0.818 (<0.85), J(A,C)=0.869 (>=0.85),
# J(B,C)=0.841 (<0.85) — so only A↔C clusters after the exact re-verify.
_A = frozenset(f"s{i}" for i in range(100))
_B = (_A - {f"s{i}" for i in range(10)}) | {f"t{i}" for i in range(10)}
_C = (_A - {f"s{i}" for i in range(7)}) | {f"u{i}" for i in range(7)}


def test_signature_is_deterministic_and_pinned() -> None:
    s = frozenset(["a b c d e", "b c d e f", "c d e f g"])
    assert mh.signature(s) == mh.signature(s)
    assert len(mh.signature(s)) == mh.NUM_PERM
    # PINNED — a Python hash() (PYTHONHASHSEED-salted) or an unseeded-perm regression fails here.
    assert mh.signature(s)[:6] == (
        366255246,
        1152496855,
        1245474047,
        1076038033,
        219095610,
        1368867285,
    )


def test_signature_rejects_empty() -> None:
    with pytest.raises(ValueError):
        mh.signature(frozenset())


def test_shingles_below_k_is_empty() -> None:
    assert mh.shingles("one two three four") == frozenset()  # 4 tokens < k=5
    assert len(mh.shingles("one two three four five six")) == 2  # 6 tokens → 2 shingles


def test_exact_jaccard_bounds() -> None:
    assert mh.exact_jaccard(frozenset(), frozenset()) == 0.0  # empty union, never ZeroDivision
    assert mh.exact_jaccard(_A, _A) == 1.0
    assert abs(mh.exact_jaccard(_A, _B) - 0.8182) < 0.001
    assert abs(mh.exact_jaccard(_A, _C) - 0.8692) < 0.001


def test_lsh_surfaces_the_above_threshold_pair() -> None:
    sigs = {"A": mh.signature(_A), "C": mh.signature(_C)}
    pairs = {frozenset(p) for p in mh.lsh_candidate_pairs(sigs)}
    assert frozenset({"A", "C"}) in pairs  # ~0.87 → recall ≈ 1.0 (the 0.42 knee over-emits)


def test_clustering_decision_is_exact_jaccard_threshold() -> None:
    sets = {"A": _A, "B": _B, "C": _C}
    sigs = {k: mh.signature(v) for k, v in sets.items()}
    confirmed = [
        p
        for p in mh.lsh_candidate_pairs(sigs)
        if mh.exact_jaccard(*(sets[k] for k in p)) >= mh.NEAR_DUP_THRESHOLD
    ]
    comps = [sorted(c) for c in mh.connected_components(list(sets), confirmed)]
    assert comps == [["A", "C"]]  # only the 0.869 pair clusters; 0.818 + 0.841 excluded


def test_connected_components_drops_singletons() -> None:
    assert mh.connected_components(["x", "y", "z"], []) == []  # no edges → no clusters


def test_near_dup_threshold_mirrors_settings_default() -> None:
    # The pure-domain §7.1 default and the runtime Settings knob must stay in sync. config.py cannot
    # import the domain module (wrong-direction dependency), so this guard pins them; if either the
    # minhash constant or settings.import_near_dup_threshold is changed, the other must follow.
    from easysynq_api.config import Settings

    assert Settings().import_near_dup_threshold == mh.NEAR_DUP_THRESHOLD
