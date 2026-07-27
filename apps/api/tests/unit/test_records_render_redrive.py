"""Focused orchestration proofs for the structured-record rendition redrive."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from easysynq_api.services.records import render

pytestmark = pytest.mark.unit


async def test_redrive_isolates_enqueue_failures_for_next_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = uuid.uuid4(), uuid.uuid4()
    candidates = AsyncMock(return_value=[first, second])
    monkeypatch.setattr(render, "_missing_structured_pdf_ids", candidates)
    session = AsyncMock(spec=AsyncSession)
    attempted: list[uuid.UUID] = []

    def enqueue(record_id: uuid.UUID) -> None:
        attempted.append(record_id)
        if record_id == first:
            raise RuntimeError("broker unavailable")

    summary = await render.redrive_missing_structured_pdfs(session, enqueue=enqueue, limit=2)

    assert summary == {"candidates": 2, "enqueued": 1, "failed": 1}
    assert attempted == [first, second]
    candidates.assert_awaited_once_with(session, limit=2)
    session.rollback.assert_awaited_once()
