"""Unit tests for the in-process DedupDetector seam (doc 09 §7.1/§14): identical-text clustering,
sub-k exclusion, heartbeat-during-compute, and cross-run determinism."""

from __future__ import annotations

import builtins
import uuid

import pytest

from easysynq_api.services.ingestion.progress import PipelineProgressStopped
from easysynq_api.services.similarity import InProcessMinHashDetector, NearDupItem

_LONG = " ".join(f"word{i}" for i in range(80))
_OTHER = " ".join(f"alt{i}" for i in range(80))


async def test_identical_text_clusters_with_jaccard_one() -> None:
    det = InProcessMinHashDetector()
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    items = [NearDupItem(a, _LONG), NearDupItem(b, _LONG), NearDupItem(c, _OTHER)]
    clusters = await det.detect_near(items)
    assert len(clusters) == 1
    assert set(clusters[0].member_file_ids) == {a, b}
    assert clusters[0].jaccard == 1.0


async def test_subk_and_empty_text_excluded() -> None:
    det = InProcessMinHashDetector()
    items = [
        NearDupItem(uuid.uuid4(), "too short here"),  # < k tokens
        NearDupItem(uuid.uuid4(), "too short here"),
        NearDupItem(uuid.uuid4(), ""),
    ]
    assert await det.detect_near(items) == []


async def test_heartbeat_called_during_compute() -> None:
    det = InProcessMinHashDetector()
    calls = 0

    async def hb() -> None:
        nonlocal calls
        calls += 1

    items = [NearDupItem(uuid.uuid4(), " ".join(f"w{i}x{j}" for i in range(8))) for j in range(600)]
    await det.detect_near(items, heartbeat=hb)
    assert calls >= 2  # heartbeated inside the >256-item shingle/signature loops


async def test_clusters_are_deterministic_across_runs() -> None:
    det = InProcessMinHashDetector()
    a, b = uuid.uuid4(), uuid.uuid4()
    items = [NearDupItem(a, _LONG), NearDupItem(b, _LONG)]
    r1 = await det.detect_near(items)
    r2 = await det.detect_near(items)
    assert [c.member_file_ids for c in r1] == [c.member_file_ids for c in r2]


@pytest.mark.parametrize("count,expected", [(0, []), (1, [1]), (256, [256]), (257, [256, 257])])
async def test_skipped_inputs_heartbeat_only_after_completed_batches(monkeypatch, count, expected):
    """An early-first callback and a missing tail both violate completed-work cadence."""
    from easysynq_api.services.similarity import detector

    processed = 0
    snapshots = []
    shingles = detector.minhash.shingles

    def observed(text):
        nonlocal processed
        processed += 1
        return shingles(text)

    async def heartbeat():
        snapshots.append(processed)

    monkeypatch.setattr(detector.minhash, "shingles", observed)
    items = [NearDupItem(uuid.UUID(int=i + 1), "") for i in range(count)]
    assert await InProcessMinHashDetector().detect_near(items, heartbeat=heartbeat) == []
    assert snapshots == expected


async def test_near_detector_full_batches_and_partial_tails(monkeypatch):
    """All four compute passes checkpoint 256 completed operations and their partial tail."""
    from easysynq_api.services.similarity import detector

    processed = [0, 0, 0, 0]
    snapshots = []
    for index, name in ((0, "shingles"), (1, "signature"), (3, "exact_jaccard")):
        original = getattr(detector.minhash, name)

        def observe(*args, _index=index, _original=original):
            processed[_index] += 1
            return _original(*args)

        monkeypatch.setattr(detector.minhash, name, observe)
    items = [NearDupItem(uuid.UUID(int=i + 1), _LONG) for i in range(258)]
    # Bound candidate work to one connected chain; exact comparison and component assembly
    # remain real, so every item still belongs to the independently expected single cluster.
    pairs = [frozenset((items[i].file_id, items[i + 1].file_id)) for i in range(257)]
    monkeypatch.setattr(detector.minhash, "lsh_candidate_pairs", lambda _: set(pairs))

    def candidate_work(_):
        for pair in pairs:
            processed[2] += 1
            yield pair

    monkeypatch.setattr(detector.minhash, "iter_lsh_candidate_work", candidate_work)

    async def heartbeat():
        snapshots.append(tuple(processed))

    result = await InProcessMinHashDetector().detect_near(items, heartbeat=heartbeat)
    assert snapshots == [
        (256, 0, 0, 0),
        (258, 0, 0, 0),
        (258, 256, 0, 0),
        (258, 258, 0, 0),
        (258, 258, 256, 0),
        (258, 258, 257, 0),
        (258, 258, 257, 256),
        (258, 258, 257, 257),
    ]
    assert len(result) == 1
    assert result[0].member_file_ids == tuple(item.file_id for item in items)
    assert result[0].jaccard == 1.0
    assert result == await InProcessMinHashDetector().detect_near(items)


def _observe_lsh_operations(monkeypatch):
    """Delegate real hashing/pair construction; exclude shingle frozensets from pair counts."""
    from easysynq_api.services.similarity import detector

    processed = [0, 0, 0]  # Band keys (placements), pair attempts, exact verification.
    original_band_key = detector.minhash._band_key
    original_jaccard = detector.minhash.exact_jaccard

    def band_key(band):
        result = original_band_key(band)
        processed[0] += 1
        return result

    def pair_set(values=()):
        result = builtins.frozenset(values)
        if isinstance(values, tuple) and len(values) == 2:
            if all(isinstance(value, uuid.UUID) for value in values):
                processed[1] += 1
        return result

    def jaccard(*args):
        result = original_jaccard(*args)
        processed[2] += 1
        return result

    monkeypatch.setattr(detector.minhash, "_band_key", band_key)
    monkeypatch.setattr(detector.minhash, "frozenset", pair_set, raising=False)
    monkeypatch.setattr(detector.minhash, "exact_jaccard", jaccard)
    return processed


async def test_real_lsh_checkpoints_count_duplicate_attempts_before_verification(monkeypatch):
    """The public detector must checkpoint real candidate work, even after membership saturates."""
    processed = _observe_lsh_operations(monkeypatch)
    snapshots = []
    items = [NearDupItem(uuid.UUID(int=i), _LONG) for i in range(1, 7)]

    async def heartbeat():
        if processed[0] and processed[2] == 0:
            snapshots.append(tuple(processed))

    result = await InProcessMinHashDetector().detect_near(items, heartbeat=heartbeat)
    # 32*6 placements + 32*C(6,2) attempts = 672 operations, but only 15 unique pairs.
    assert snapshots == [(192, 64, 0), (192, 320, 0), (192, 480, 0)]
    assert processed == [192, 480, 15]
    assert [(cluster.member_file_ids, cluster.jaccard) for cluster in result] == [
        (tuple(item.file_id for item in items), 1.0)
    ]
    assert result == await InProcessMinHashDetector().detect_near(items)


async def test_real_lsh_bucket_only_work_checkpoints_without_candidates(monkeypatch):
    """Counting pairs alone must not conceal completed placements in singleton buckets."""
    from easysynq_api.services.similarity import detector

    processed = _observe_lsh_operations(monkeypatch)
    snapshots = []
    # Control signatures to make every band's nine buckets disjoint; LSH itself stays real.
    signatures = iter([(i,) * 128 for i in range(1, 10)])
    monkeypatch.setattr(detector.minhash, "signature", lambda _: next(signatures))

    async def heartbeat():
        if processed[0]:
            snapshots.append(tuple(processed))

    items = [NearDupItem(uuid.UUID(int=i), _LONG) for i in range(1, 10)]
    assert await InProcessMinHashDetector().detect_near(items, heartbeat=heartbeat) == []
    assert snapshots == [(256, 0, 0), (288, 0, 0)]
    assert processed == [288, 0, 0]


@pytest.mark.parametrize(
    "count,expected", [(256, [(256, 1, 0)]), (257, [(256, 1, 0), (257, 2, 0)])]
)
async def test_candidate_batch_boundaries_follow_completed_insertions(monkeypatch, count, expected):
    """Exact multiples have no extra tail; duplicate attempts are inserted before callbacks."""
    from easysynq_api.services.similarity import detector

    processed = [0, 0, 0]  # Work yielded, pair objects inserted at least once, verification.
    snapshots = []
    ids = (uuid.UUID(int=1), uuid.UUID(int=2))
    original_jaccard = detector.minhash.exact_jaccard

    class ObservedPair(frozenset):
        inserted = False

        def __hash__(self):
            if not self.inserted:
                self.inserted = True
                processed[1] += 1
            return super().__hash__()

    def candidate_work(_):
        for index in range(count):
            processed[0] += 1
            yield None if index < 255 else ObservedPair(ids)

    def jaccard(*args):
        processed[2] += 1
        return original_jaccard(*args)

    def unexpected_wrapper(_):
        raise AssertionError("callback path must consume shared candidate work")

    async def heartbeat():
        if processed[0] and processed[2] == 0:
            snapshots.append(tuple(processed))

    monkeypatch.setattr(detector.minhash, "iter_lsh_candidate_work", candidate_work)
    monkeypatch.setattr(detector.minhash, "lsh_candidate_pairs", unexpected_wrapper)
    monkeypatch.setattr(detector.minhash, "exact_jaccard", jaccard)
    result = await InProcessMinHashDetector().detect_near(
        [NearDupItem(file_id, _LONG) for file_id in ids], heartbeat=heartbeat
    )
    assert snapshots == expected
    assert processed == [count, count - 255, 1]
    assert [(cluster.member_file_ids, cluster.jaccard) for cluster in result] == [(ids, 1.0)]


@pytest.mark.parametrize("failure", ["generic", "stopped"])
async def test_real_lsh_callback_failure_stops_candidate_work_immediately(monkeypatch, failure):
    """Rejected progress must escape unchanged before later attempts or exact verification."""
    processed = _observe_lsh_operations(monkeypatch)
    error = (
        RuntimeError("candidate progress failed")
        if failure == "generic"
        else PipelineProgressStopped()
    )
    snapshots = []

    async def heartbeat():
        if processed[0] and processed[2] == 0:
            snapshots.append(tuple(processed))
            raise error

    items = [NearDupItem(uuid.UUID(int=i), _LONG) for i in range(1, 7)]
    with pytest.raises(type(error)) as caught:
        await InProcessMinHashDetector().detect_near(items, heartbeat=heartbeat)
    assert caught.value is error
    assert snapshots == [(192, 64, 0)]
    assert processed == [192, 64, 0]


@pytest.mark.parametrize(
    "threshold,expected",
    [(0.97, [((1, 2, 3), 76 / 78), ((4, 5), 1.0)]), (0.99, [((4, 5), 1.0)])],
)
async def test_callback_and_public_paths_preserve_expected_clusters(threshold, expected):
    """Both paths preserve sorted components and minimum exact edge scores at custom thresholds."""
    texts = [
        _LONG,
        _LONG + " extra",
        _LONG + " extra more",
        _OTHER,
        _OTHER,
        "short",
        "",
        "unique tokens for lone item",
    ]
    items = [NearDupItem(uuid.UUID(int=i), text) for i, text in enumerate(texts, start=1)]

    async def heartbeat():
        pass

    detector = InProcessMinHashDetector()
    plain = await detector.detect_near(list(reversed(items)), threshold=threshold)
    progress = await detector.detect_near(items, threshold=threshold, heartbeat=heartbeat)
    assert plain == progress
    assert [
        (tuple(file_id.int for file_id in cluster.member_file_ids), cluster.jaccard)
        for cluster in progress
    ] == expected


async def test_detector_candidate_paths_match_explicit_collision_oracle(monkeypatch):
    """Both consumers must re-verify exactly the hand-authored collision pairs, once each."""
    from easysynq_api.services.similarity import detector

    items = [NearDupItem(uuid.UUID(int=i), f"item{i} one two three four") for i in range(1, 6)]
    signatures = [
        (10,) * 128,
        (10,) * 128,
        (10,) * 4 + (20,) * 124,
        (30,) * 4 + (20,) * 4 + (30,) * 120,
        (40,) * 128,
    ]
    # Each fixture text has exactly one distinct shingle, so every true Jaccard is zero.
    ids_by_set = {frozenset((item.text,)): item.file_id for item in items}
    signatures_by_set = dict(zip(ids_by_set, signatures, strict=True))
    expected = {frozenset(pair) for pair in ((1, 2), (1, 3), (2, 3), (3, 4))}
    verified = []
    original = detector.minhash.exact_jaccard

    def jaccard(left, right):
        verified.append(frozenset((ids_by_set[left].int, ids_by_set[right].int)))
        return original(left, right)

    async def heartbeat():
        pass

    monkeypatch.setattr(detector.minhash, "signature", signatures_by_set.__getitem__)
    monkeypatch.setattr(detector.minhash, "exact_jaccard", jaccard)
    for callback in (None, heartbeat):
        verified.clear()
        assert await InProcessMinHashDetector().detect_near(items, heartbeat=callback) == []
        assert set(verified) == expected
        assert len(verified) == 4
