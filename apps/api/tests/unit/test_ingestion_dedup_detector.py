"""Unit tests for the in-process DedupDetector seam (doc 09 §7.1/§14): identical-text clustering,
sub-k exclusion, heartbeat-during-compute, and cross-run determinism."""

from __future__ import annotations

import uuid

import pytest

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
    """All three compute passes checkpoint 256 completed inputs and their partial tail."""
    from easysynq_api.services.similarity import detector

    processed = [0, 0, 0]
    snapshots = []
    for index, name in enumerate(("shingles", "signature", "exact_jaccard")):
        original = getattr(detector.minhash, name)

        def observe(*args, _index=index, _original=original):
            processed[_index] += 1
            return _original(*args)

        monkeypatch.setattr(detector.minhash, name, observe)
    items = [NearDupItem(uuid.UUID(int=i + 1), _LONG) for i in range(258)]
    # Bound candidate work to one connected chain; exact comparison and component assembly
    # remain real, so every item still belongs to the independently expected single cluster.
    pairs = [frozenset((items[i].file_id, items[i + 1].file_id)) for i in range(257)]
    monkeypatch.setattr(detector.minhash, "lsh_candidate_pairs", lambda _: pairs)

    async def heartbeat():
        snapshots.append(tuple(processed))

    result = await InProcessMinHashDetector().detect_near(items, heartbeat=heartbeat)
    assert snapshots == [
        (256, 0, 0),
        (258, 0, 0),
        (258, 256, 0),
        (258, 258, 0),
        (258, 258, 256),
        (258, 258, 257),
    ]
    assert len(result) == 1
    assert result[0].member_file_ids == tuple(item.file_id for item in items)
    assert result[0].jaccard == 1.0
    assert result == await InProcessMinHashDetector().detect_near(items)
