"""Focused unit proofs for the records capture/purge advisory-lock helpers."""

from __future__ import annotations

from typing import Any

from easysynq_api.services.records import repository as repo
from easysynq_api.services.vault.worm import WormObjectLocator


class _RecordingSession:
    def __init__(self, resolved_hash_keys: list[int]) -> None:
        self._resolved_hash_keys = iter(resolved_hash_keys)
        self.acquired_keys: list[int] = []

    async def scalar(self, _statement: object) -> int:
        return next(self._resolved_hash_keys)

    async def execute(self, statement: Any) -> None:
        params = statement.compile().params
        self.acquired_keys.append(int(next(iter(params.values()))))


class _RecordingTupleSession:
    def __init__(self) -> None:
        self.acquired_keys: list[tuple[int, int]] = []

    async def execute(self, statement: Any) -> None:
        params = tuple(int(value) for value in statement.compile().params.values())
        assert len(params) == 2
        self.acquired_keys.append(params)


async def test_physical_object_locks_sort_and_dedupe_resolved_hash_keys() -> None:
    """Raw A<C<D ordering is unsafe when A and D collide; actual keys must drive acquisition."""
    session = _RecordingSession([9, -4, 9])

    await repo.lock_physical_objects(  # type: ignore[arg-type]
        session,
        [
            ("records", "a"),
            ("records", "c"),
            ("records", "d"),
        ],
    )

    assert session.acquired_keys == [-4, 9]


def test_worm_lock_key_derivation_has_pinned_namespaces_and_stable_golden_values() -> None:
    destination = repo.destination_allocation_lock_key("records", "records/" + "1" * 64)
    exact = repo.exact_version_lock_key(
        WormObjectLocator(
            "records",
            "records/" + "1" * 64,
            "version-1",
        )
    )

    assert destination == (0x4553414C, 766663298)  # ESAL
    assert exact == (0x45535756, -348856920)  # ESWV
    assert destination[0] != exact[0]


def test_destination_allocation_identity_is_global_bucket_and_key_only() -> None:
    first_org = repo.destination_allocation_lock_key("records", "records/shared-sha")
    second_org = repo.destination_allocation_lock_key("records", "records/shared-sha")

    assert first_org == second_org


async def test_exact_version_locks_sort_and_dedupe_by_actual_advisory_tuple() -> None:
    lexical_first = WormObjectLocator("records", "a", "v1")
    lexical_second = WormObjectLocator("records", "b", "v1")
    forward = _RecordingTupleSession()
    inverted = _RecordingTupleSession()

    await repo.lock_exact_worm_objects(
        forward,  # type: ignore[arg-type]
        [lexical_first, lexical_second, lexical_first],
    )
    await repo.lock_exact_worm_objects(
        inverted,  # type: ignore[arg-type]
        [lexical_second, lexical_first],
    )

    expected = [
        (0x45535756, -916266495),
        (0x45535756, 1092209910),
    ]
    assert forward.acquired_keys == expected
    assert inverted.acquired_keys == expected


async def test_destination_batches_sort_and_dedupe_global_actual_advisory_tuples() -> None:
    forward = _RecordingTupleSession()
    inverted = _RecordingTupleSession()

    await repo.lock_destination_allocations(
        forward,  # type: ignore[arg-type]
        [("records", "a"), ("records", "g"), ("records", "a")],
    )
    await repo.lock_destination_allocations(
        inverted,  # type: ignore[arg-type]
        [("records", "g"), ("records", "a")],
    )

    expected = [
        (0x4553414C, -1949875122),
        (0x4553414C, 1938071098),
    ]
    assert forward.acquired_keys == expected
    assert inverted.acquired_keys == expected
