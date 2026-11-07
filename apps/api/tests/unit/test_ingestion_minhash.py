"""Unit tests for the S-ing-3 deterministic MinHash (doc 09 §7.1): signature stability (a pinned
value catches a hash()/seed regression), the exact-Jaccard cluster decision, sub-k/empty edge cases,
and LSH candidate recall at the 0.85 threshold."""

from __future__ import annotations

import uuid

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
    # The pure-domain §7.1 default and the Settings field DEFAULT must stay in sync. config.py can't
    # import the domain module (wrong-direction dependency), so this guard pins them; if either the
    # minhash constant or the import_near_dup_threshold default is changed, the other must follow.
    # Compare the DECLARED field default, NOT ``Settings()`` — an instance reads an env/.env
    # override (IMPORT_NEAR_DUP_THRESHOLD) and would fail while the default still matches.
    from easysynq_api.config import Settings

    assert Settings.model_fields["import_near_dup_threshold"].default == mh.NEAR_DUP_THRESHOLD


@pytest.mark.parametrize(
    "keys",
    [
        ("a", "b", "c", "d", "e"),
        (1, 2, 3, 4, 5),
        tuple(uuid.UUID(int=i) for i in range(1, 6)),
        ((1,), (2,), (3,), (4,), (5,)),
    ],
    ids=["strings", "integers", "uuids", "tuples"],
)
def test_lsh_work_and_public_pairs_match_explicit_collision_oracle(keys) -> None:
    """Dropping a band, adding cross-band/self pairs, or retaining duplicates breaks this oracle."""
    a, b, c, d, _e = keys
    sigs = {key: [value] * 128 for key, value in zip(keys, (10, 10, 20, 30, 40), strict=True)}
    # A/B share every band; C joins their first band; C/D share only the second.
    # E is a singleton in every band. Equal values in different bands cannot collide.
    sigs[c][:4] = [10] * 4
    sigs[d][4:8] = [20] * 4
    expected = {frozenset((a, b)), frozenset((a, c)), frozenset((b, c)), frozenset((c, d))}

    public = mh.lsh_candidate_pairs(sigs)
    work = list(mh.iter_lsh_candidate_work(sigs))
    attempts = [pair for pair in work if pair is not None]
    assert isinstance(public, set)
    assert public == set(attempts) == expected
    assert work[:160] == [None] * 160  # 5 inputs * 32 completed bucket placements.
    assert all(pair is not None for pair in work[160:])
    assert len(attempts) == 35  # 32 A/B attempts plus A/C, B/C, C/D.
    assert attempts.count(frozenset((a, b))) == 32
    assert all(len(pair) == 2 for pair in attempts)


@pytest.mark.parametrize("count,placements", [(0, 0), (1, 32), (8, 256), (9, 288)])
def test_lsh_work_includes_bucket_placements_without_pairs(monkeypatch, count, placements) -> None:
    """Singleton buckets are completed work even when no candidate can be emitted."""
    sigs = {key: [key + 1] * 128 for key in range(count)}
    band_keys = 0
    original = mh._band_key

    def observe(band):
        nonlocal band_keys
        band_keys += 1
        return original(band)

    monkeypatch.setattr(mh, "_band_key", observe)
    observed = []
    for marker in mh.iter_lsh_candidate_work(sigs):
        assert marker is None
        observed.append(band_keys)
    assert observed == list(range(1, placements + 1))
    assert mh.lsh_candidate_pairs(sigs) == set()


def test_public_lsh_wrapper_consumes_shared_work_and_preserves_none_key(monkeypatch) -> None:
    """A separate wrapper algorithm would ignore this shared producer and lose its membership."""
    pair = frozenset((None, "key"))
    sigs = {None: [1] * 128, "key": [1] * 128}

    def work(actual):
        assert actual is sigs
        yield None
        yield pair
        yield pair

    monkeypatch.setattr(mh, "iter_lsh_candidate_work", work)
    assert mh.lsh_candidate_pairs(sigs) == {pair}
