"""Focused orchestration proofs for the structured-record rendition redrive."""

from __future__ import annotations

import datetime
import uuid
from unittest.mock import AsyncMock, call

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from easysynq_api.db.models.document_version import DocumentVersion
from easysynq_api.services.records import render

pytestmark = pytest.mark.unit


async def test_candidate_scan_advances_past_a_full_invalid_schema_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed legacy pins cannot monopolize the oldest bounded redrive page."""
    bad_ids = [uuid.uuid4(), uuid.uuid4()]
    valid_id = uuid.uuid4()
    captured = datetime.datetime(2026, 7, 27, tzinfo=datetime.UTC)
    bad_version = DocumentVersion(
        metadata_snapshot={"field_schema": {"fields": [{"key": "", "type": "string"}]}}
    )
    valid_version = DocumentVersion(
        metadata_snapshot={"field_schema": {"fields": [{"key": "result", "type": "string"}]}}
    )
    first_page = [
        (bad_ids[0], captured, bad_version),
        (bad_ids[1], captured + datetime.timedelta(seconds=1), bad_version),
    ]
    second_page = [
        (valid_id, captured + datetime.timedelta(seconds=2), valid_version),
    ]
    pages = AsyncMock(side_effect=[first_page, second_page])
    monkeypatch.setattr(render, "_missing_structured_pdf_page", pages)
    session = AsyncMock(spec=AsyncSession)

    assert await render._missing_structured_pdf_ids(session, limit=2) == [valid_id]
    assert pages.await_args_list == [
        call(session, limit=2, after=None),
        call(
            session,
            limit=2,
            after=(captured + datetime.timedelta(seconds=1), bad_ids[1]),
        ),
    ]


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
