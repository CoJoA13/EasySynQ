"""Focused unit proofs for the records capture/purge advisory-lock helpers."""

from __future__ import annotations

from typing import Any

from easysynq_api.services.records import repository as repo


class _RecordingSession:
    def __init__(self, resolved_hash_keys: list[int]) -> None:
        self._resolved_hash_keys = iter(resolved_hash_keys)
        self.acquired_keys: list[int] = []

    async def scalar(self, _statement: object) -> int:
        return next(self._resolved_hash_keys)

    async def execute(self, statement: Any) -> None:
        params = statement.compile().params
        self.acquired_keys.append(int(next(iter(params.values()))))


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
