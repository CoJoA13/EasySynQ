"""Completed-work cadence outside the near detector; no database or new-module import needed."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, datetime

import pytest

from easysynq_api.db.models.import_file import ImportFile


def _file(i, *, sha=None, filename=None):
    return ImportFile(
        id=uuid.UUID(int=i + 1),
        org_id=uuid.UUID(int=10000),
        run_id=uuid.UUID(int=10001),
        rel_path=f"source/{i}.txt",
        filename=filename or f"unrelated-{i}.txt",
        ext=".txt",
        mtime=datetime(2026, 1, 1, tzinfo=UTC),
        size_bytes=10,
        included_candidate=True,
        sha256=sha,
        scan_flags={"disposition": "included"},
    )


@pytest.mark.parametrize(
    "case,count,expected,expected_clusters,expected_families",
    [
        ("empty", 0, [], 0, 0),
        (
            "exact",
            256,
            [
                (256, 256, 0, 0, 0, 0),
                (256, 256, 1, 0, 0, 0),
                (256, 256, 1, 256, 1, 0),
                (256, 256, 1, 256, 1, 1),
            ],
            1,
            0,
        ),
        (
            "exact",
            257,
            [
                (256, 256, 0, 0, 0, 0),
                (257, 257, 0, 0, 0, 0),
                (257, 257, 1, 0, 0, 0),
                (257, 257, 1, 256, 1, 0),
                (257, 257, 1, 257, 1, 0),
                (257, 257, 1, 257, 1, 1),
            ],
            1,
            0,
        ),
        (
            "skipped",
            256,
            [
                (256, 256, 0, 0, 0, 0),
                (256, 256, 256, 0, 0, 0),
                (256, 256, 256, 256, 256, 0),
                (256, 256, 256, 256, 256, 256),
            ],
            0,
            0,
        ),
        (
            "skipped",
            257,
            [
                (256, 256, 0, 0, 0, 0),
                (257, 257, 0, 0, 0, 0),
                (257, 257, 256, 0, 0, 0),
                (257, 257, 257, 0, 0, 0),
                (257, 257, 257, 256, 256, 0),
                (257, 257, 257, 257, 257, 0),
                (257, 257, 257, 257, 257, 256),
                (257, 257, 257, 257, 257, 257),
            ],
            0,
            0,
        ),
        (
            "family",
            256,
            [
                (256, 0, 0, 0, 0, 0),
                (256, 0, 0, 256, 256, 0),
                (256, 0, 0, 256, 256, 1),
            ],
            0,
            1,
        ),
        (
            "family",
            257,
            [
                (256, 0, 0, 0, 0, 0),
                (257, 0, 0, 0, 0, 0),
                (257, 0, 0, 256, 256, 0),
                (257, 0, 0, 257, 257, 0),
                (257, 0, 0, 257, 257, 1),
            ],
            0,
            1,
        ),
    ],
)
async def test_dedup_outer_loops_checkpoint_completed_work(
    monkeypatch, case, count, expected, expected_clusters, expected_families
):
    from easysynq_api.services.ingestion import dedup

    class EmptyDetector:
        async def detect_near(self, items, *, threshold, heartbeat):
            assert items == []
            return []

    monkeypatch.setattr(dedup, "get_dedup_detector", EmptyDetector)
    rows = []
    for i in range(count):
        sha = "a" * 64 if case == "exact" else f"{i:064x}" if case == "skipped" else None
        filename = f"SOP-PUR-002 Purchasing v{i + 1}.txt" if case == "family" else None
        rows.append((_file(i, sha=sha, filename=filename), None, None))

    async def noop():
        pass

    without_callbacks = await dedup._compute(rows, 0.85, hb=noop)

    # Observe real reads and completed group consumption, not the production loop index.
    # Snapshots are (exact inputs inspected, IDs actually grouped by SHA, exact groups
    # consumed/skipped, family inputs inspected, IDs actually grouped by family, family
    # groups consumed/skipped). The literal rows above reject first-item callbacks,
    # callbacks before appending/consuming work, skipped-entry omissions, and duplicate tails.
    phase = ""
    exact_inputs, family_inputs = set(), set()
    exact_groups, family_groups = set(), set()
    groupings = []
    snapshots = []

    class ObservedFile:
        def __init__(self, value):
            self.value = value

        def __getattr__(self, name):
            return getattr(self.value, name)

        @property
        def sha256(self):
            if phase == "exact-inputs":
                exact_inputs.add(self.value.id)
            return self.value.sha256

        @property
        def id(self):
            if phase == "family-inputs":
                family_inputs.add(self.value.id)
            return self.value.id

    class ObservedMembers(list):
        def __init__(self, values, key, completed):
            super().__init__(values)
            self.key = key
            self.completed = completed

        def __len__(self):
            size = super().__len__()
            if size < 2:
                self.completed.add(self.key)  # singleton group has completed its skip check
            return size

        def __iter__(self):
            yield from super().__iter__()
            self.completed.add(self.key)  # all members have reached the real canonical picker

    class ObservedGrouping(defaultdict):
        def values(self):
            nonlocal phase
            phase = "exact-groups"
            for key, values in super().items():
                yield ObservedMembers(values, key, exact_groups)

        def items(self):
            nonlocal phase
            phase = "family-groups"
            for key, values in super().items():
                yield key, ObservedMembers(values, key, family_groups)

    def observe_grouping(factory):
        nonlocal phase
        phase = "exact-inputs" if not groupings else "family-inputs"
        result = ObservedGrouping(factory)
        groupings.append(result)
        return result

    async def heartbeat():
        # Bypass observational .values(): reading the oracle must not alter its phase.
        exact_placed = sum(map(len, dict.values(groupings[0])))
        family_placed = sum(map(len, dict.values(groupings[1]))) if len(groupings) > 1 else 0
        snapshots.append(
            (
                len(exact_inputs),
                exact_placed,
                len(exact_groups),
                len(family_inputs),
                family_placed,
                len(family_groups),
            )
        )

    monkeypatch.setattr(dedup, "defaultdict", observe_grouping)
    observed_rows = [(ObservedFile(f), ext, cls) for f, ext, cls in rows]
    result = await dedup._compute(observed_rows, 0.85, hb=heartbeat)
    assert snapshots == expected
    assert len(result[0]) == expected_clusters
    assert len(result[1]) == expected_families
    assert result == without_callbacks
    if case == "exact":
        assert set(result[0][0]["member_file_ids"]) == {row[0].id for row in rows}
    if case == "family":
        assert set(result[1][0]["ordered_member_file_ids"]) == {row[0].id for row in rows}


@pytest.mark.parametrize("count,expected", [(0, []), (1, [1]), (256, [256]), (257, [256, 257])])
async def test_proposal_callbacks_follow_completed_keep_items(monkeypatch, count, expected):
    from easysynq_api.services.ingestion import propose

    rows = [(_file(i), None, None) for i in range(count)]
    captured = []
    snapshots = []
    processed = 0
    original_owner_hint = propose._owner_hint

    async def context(*args):
        return rows

    async def empty_list(*args):
        return []

    async def empty_map(*args):
        return {}

    async def replace(session, run_id, *, org_id, nodes):
        captured.append(nodes)

    async def conflicts(*args):
        pass

    def owner_hint(ext):
        nonlocal processed
        processed += 1
        return original_owner_hint(ext)

    async def heartbeat():
        snapshots.append(processed)

    monkeypatch.setattr(propose.repo, "included_files_with_context", context)
    monkeypatch.setattr(propose.repo, "list_dupe_clusters", empty_list)
    monkeypatch.setattr(propose.repo, "list_version_families", empty_list)
    monkeypatch.setattr(propose.repo, "replace_proposals", replace)
    monkeypatch.setattr(propose.repo, "compute_proposal_counts", empty_map)
    monkeypatch.setattr(propose, "_clause_ref_map", empty_map)
    monkeypatch.setattr(propose, "fetch_top_words", empty_map)
    monkeypatch.setattr(propose, "_detect_conflicts", conflicts)
    monkeypatch.setattr(propose, "_owner_hint", owner_hint)
    rid, org = uuid.uuid4(), uuid.uuid4()
    await propose.rebuild_proposals(None, rid, org_id=org, version=None, heartbeat=heartbeat)
    assert snapshots == expected
    assert len(captured[0]) == count
    assert [node["file_id"] for node in captured[0]] == [row[0].id for row in rows]
    assert all(node["target_ia_path"] == "_unmapped" for node in captured[0])
    await propose.rebuild_proposals(None, rid, org_id=org, version=None)
    assert captured[0] == captured[1]
