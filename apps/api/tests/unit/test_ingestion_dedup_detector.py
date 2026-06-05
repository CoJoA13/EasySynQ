"""Unit tests for the in-process DedupDetector seam (doc 09 §7.1/§14): identical-text clustering,
sub-k exclusion, heartbeat-during-compute, and cross-run determinism."""

from __future__ import annotations

import uuid

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
