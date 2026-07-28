"""Regression coverage for bounded-memory audit-chain verification."""

from __future__ import annotations

import datetime
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from easysynq_api.db.models._audit_enums import ActorType, AuditObjectType, EventType
from easysynq_api.services.audit import verify as verify_mod
from easysynq_api.services.audit.canonical import (
    GENESIS_HASH,
    audit_row_from_orm,
    compute_row_hash,
)

pytestmark = pytest.mark.unit


def _event(row_id: int, org_id: uuid.UUID, prev_hash: bytes) -> SimpleNamespace:
    event = SimpleNamespace(
        id=row_id,
        org_id=org_id,
        occurred_at=datetime.datetime(2026, 7, 27, row_id, tzinfo=datetime.UTC),
        actor_id=None,
        actor_type=ActorType.system,
        event_type=EventType.CHAIN_VERIFY_PASS,
        object_type=AuditObjectType.audit,
        object_id=None,
        scope_ref=None,
        reason=None,
        before=None,
        after=None,
        request_id=None,
        client_ip=None,
        user_agent=None,
        auth_context=None,
        signature_event_id=None,
        prev_hash=prev_hash,
        row_hash=b"",
        chained_at=datetime.datetime(2026, 7, 27, row_id, 1, tzinfo=datetime.UTC),
    )
    event.row_hash = compute_row_hash(audit_row_from_orm(event), prev_hash)
    return event


class _StreamingRows:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self._rows = rows
        self.closed = False

    def __aiter__(self) -> Any:
        return self._iterate()

    async def _iterate(self) -> Any:
        for row in self._rows:
            yield row

    async def close(self) -> None:
        self.closed = True


class _CountResult:
    def __init__(self, value: int) -> None:
        self._value = value

    def scalar_one(self) -> int:
        return self._value


class _OptionalScalarResult:
    def __init__(self, value: bytes | None) -> None:
        self._value = value

    def scalar_one_or_none(self) -> bytes | None:
        return self._value


class _StreamingSession:
    def __init__(self, rows: list[SimpleNamespace], pending: int) -> None:
        self.rows = _StreamingRows(rows)
        self.pending = pending
        self.stream_calls = 0
        self.execute_calls = 0
        self.calls: list[str] = []

    async def stream_scalars(self, statement: Any) -> _StreamingRows:
        self.stream_calls += 1
        self.calls.append("stream")
        assert statement.get_execution_options()["yield_per"] == verify_mod._VERIFY_BATCH_SIZE
        return self.rows

    async def execute(self, _statement: Any) -> _CountResult:
        self.execute_calls += 1
        self.calls.append("execute")
        return _CountResult(self.pending)


class _BoundedStreamingSession(_StreamingSession):
    def __init__(
        self, rows: list[SimpleNamespace], pending: int, predecessor_hash: bytes | None
    ) -> None:
        super().__init__(rows, pending)
        self.predecessor_hash = predecessor_hash

    async def execute(self, _statement: Any) -> Any:
        self.execute_calls += 1
        self.calls.append("execute")
        if self.execute_calls == 1:
            return _OptionalScalarResult(self.predecessor_hash)
        return _CountResult(self.pending)


async def test_verify_chain_streams_rows_in_bounded_batches() -> None:
    """The full walk must never materialize the organization's ORM row set with ``.all()``."""
    org_id = uuid.uuid4()
    first = _event(1, org_id, GENESIS_HASH)
    second = _event(2, org_id, first.row_hash)
    session = _StreamingSession([first, second], pending=3)

    result = await verify_mod.verify_chain(session, org_id)  # type: ignore[arg-type]

    assert result.verified is True
    assert result.checked == 2
    assert result.pending == 3
    assert result.breaks == []
    assert session.stream_calls == 1
    assert session.execute_calls == 1  # only the independent pending-tail count
    assert session.rows.closed is True


async def test_bounded_stream_seeds_from_the_preceding_link() -> None:
    """A valid window beginning mid-chain must not be mistaken for a deletion."""
    org_id = uuid.uuid4()
    predecessor = _event(4, org_id, GENESIS_HASH)
    first_in_window = _event(10, org_id, predecessor.row_hash)
    session = _BoundedStreamingSession(
        [first_in_window], pending=0, predecessor_hash=predecessor.row_hash
    )

    result = await verify_mod.verify_chain(  # type: ignore[arg-type]
        session, org_id, from_id=10, to_id=10
    )

    assert result.verified is True
    assert result.checked == 1
    assert result.breaks == []
    assert session.stream_calls == 1
    assert session.execute_calls == 2  # predecessor seed + pending-tail count
    assert session.calls == ["stream", "execute", "execute"]
    assert session.rows.closed is True
