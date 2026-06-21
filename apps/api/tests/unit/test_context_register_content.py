"""S-context-1 unit proofs — the pure Context register content (build/needs-freeze), the frozen-row
shape, and the GOLDEN enum-value pins.

The golden pins exist so an in-place edit to an enum's value tuple FAILS CI — forcing the
mint-a-new-value (append-only) path: the SWOT ``category`` taxonomy and the ``classification`` spine
are frozen into published register versions, so re-lettering a value would silently re-interpret
every frozen row (the R49/R50 derive-and-freeze discipline, applied to a categorical axis)."""

from __future__ import annotations

import datetime
import uuid

import pytest

from easysynq_api.db.models._context_enums import (
    CONTEXT_CATEGORY_VALUES,
    CONTEXT_CLASSIFICATION_VALUES,
    CONTEXT_ISSUE_STATUS_VALUES,
    ContextCategory,
    ContextClassification,
    ContextIssueStatus,
)
from easysynq_api.db.models._vault_enums import VersionState
from easysynq_api.db.models.context_issue import ContextIssue
from easysynq_api.domain.context.register_content import build_register, register_needs_freeze
from easysynq_api.services.context.lifecycle import _frozen_row

pytestmark = pytest.mark.unit


# --- golden enum-value pins (append-only; mint a new value, never re-letter) ---
def test_context_classification_values_are_golden_pinned() -> None:
    # ⚠ The ISO clause-4.1 spine. internal/external is fixed by the standard; changing a value
    # in place re-interprets every frozen row. Append a NEW value if ever extended.
    assert CONTEXT_CLASSIFICATION_VALUES == ("internal", "external")


def test_context_category_values_are_golden_pinned() -> None:
    # ⚠ The SWOT taxonomy (R50). Append-only — a PESTLE extension mints NEW values, never
    # re-letters.
    assert CONTEXT_CATEGORY_VALUES == ("strength", "weakness", "opportunity", "threat")


def test_context_issue_status_values_are_golden_pinned() -> None:
    assert CONTEXT_ISSUE_STATUS_VALUES == ("active", "closed")


# --- build_register (canonical content; rows only — no scoring criteria) ---
def _row(rid: str) -> dict[str, object]:
    return {"id": rid, "classification": "internal", "status": "active", "description": "x"}


def test_build_register_sorts_rows_by_id_and_has_no_criteria() -> None:
    reg = build_register(rows=[_row("ccc"), _row("aaa"), _row("bbb")])
    assert [r["id"] for r in reg["rows"]] == ["aaa", "bbb", "ccc"]  # stable, reproducible order
    assert "criteria" not in reg  # clause 4.1 has no computed/graded axis (unlike risk)
    assert set(reg) == {"rows"}


def test_build_register_is_reproducible_regardless_of_input_order() -> None:
    a = build_register(rows=[_row("b"), _row("a")])
    b = build_register(rows=[_row("a"), _row("b")])
    assert a == b  # the bytes (rfc8785 over this) must be identical → the freeze dedups


# --- register_needs_freeze (the publish freeze-or-skip switch) ---
def test_needs_freeze_on_first_publish() -> None:
    assert (
        register_needs_freeze(latest_version_state=None, latest_register=None, working=_w()) is True
    )


def test_needs_freeze_when_latest_is_not_draft() -> None:
    # The governing Effective version carries a register; a revision always re-freezes.
    assert (
        register_needs_freeze(
            latest_version_state=VersionState.Effective, latest_register=_w(), working=_w()
        )
        is True
    )


def test_skips_freeze_on_unchanged_draft_republish() -> None:
    # request_changes → re-publish with NO edits: the latest Draft frozen register == working → skip
    # (no redundant version). Both sides come from build_register (canonicalization-stable).
    same = _w()
    assert (
        register_needs_freeze(
            latest_version_state=VersionState.Draft, latest_register=same, working=same
        )
        is False
    )


def test_needs_freeze_when_draft_changed() -> None:
    assert (
        register_needs_freeze(
            latest_version_state=VersionState.Draft,
            latest_register=build_register(rows=[_row("a")]),
            working=build_register(rows=[_row("a"), _row("b")]),
        )
        is True
    )


def _w() -> dict[str, object]:
    return build_register(rows=[_row("a")])


# --- _frozen_row (the version's WORM body per row) ---
def test_frozen_row_carries_content_excludes_bookkeeping() -> None:
    row = ContextIssue(
        id=uuid.UUID("00000000-0000-0000-0000-0000000000aa"),
        register_doc_id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        classification=ContextClassification.external,
        category=ContextCategory.threat,
        status=ContextIssueStatus.active,
        description="a market shift",
        last_reviewed_at=datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC),
        row_version=3,
        created_by=uuid.uuid4(),
    )
    frozen = _frozen_row(row)
    assert frozen == {
        "id": "00000000-0000-0000-0000-0000000000aa",
        "classification": "external",
        "category": "threat",
        "status": "active",
        "description": "a market shift",
        "last_reviewed_at": "2026-06-01T00:00:00+00:00",
        "row_version": 3,
    }
    # bookkeeping + head-implied fields are NOT in the frozen body (non-content/non-reproducible).
    assert "created_by" not in frozen and "org_id" not in frozen and "register_doc_id" not in frozen


def test_frozen_row_nullable_category_and_review() -> None:
    row = ContextIssue(
        id=uuid.UUID("00000000-0000-0000-0000-0000000000bb"),
        register_doc_id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        classification=ContextClassification.internal,
        category=None,
        status=ContextIssueStatus.closed,
        description="aging workforce",
        last_reviewed_at=None,
        row_version=1,
        created_by=uuid.uuid4(),
    )
    frozen = _frozen_row(row)
    assert frozen["category"] is None
    assert frozen["last_reviewed_at"] is None
    assert frozen["status"] == "closed"
