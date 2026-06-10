"""S-dcr-3a unit proofs — the pure version-diff domain (``domain/diff``). No I/O."""

from __future__ import annotations

import pytest

from easysynq_api.domain.diff import SNAPSHOT_FIELDS, diff_metadata, redline

pytestmark = pytest.mark.unit


# --- metadata diff ----------------------------------------------------------------------------


def test_diff_metadata_changed_and_unchanged() -> None:
    old = {"identifier": "SOP-PUR-001", "title": "Purchasing", "classification": "Internal"}
    new = {"identifier": "SOP-PUR-001", "title": "Purchasing v2", "classification": "Internal"}
    deltas = {d.field: d for d in diff_metadata(old, new)}
    assert deltas["title"].changed is True
    assert deltas["title"].from_value == "Purchasing"
    assert deltas["title"].to_value == "Purchasing v2"
    assert deltas["identifier"].changed is False
    assert deltas["classification"].changed is False


def test_diff_metadata_added_and_removed_field_reads_as_none() -> None:
    # field_schema present only on one side (a Form/Template pin appearing/disappearing).
    deltas = {d.field: d for d in diff_metadata({}, {"field_schema": {"a": 1}})}
    assert deltas["field_schema"].from_value is None
    assert deltas["field_schema"].to_value == {"a": 1}
    assert deltas["field_schema"].changed is True


def test_diff_metadata_only_emits_snapshot_fields() -> None:
    # A non-snapshot key (e.g. a version column accidentally in the dict) is NOT diffed.
    deltas = diff_metadata({"title": "A", "change_significance": "MINOR"}, {"title": "A"})
    fields = {d.field for d in deltas}
    assert "change_significance" not in fields
    assert fields <= set(SNAPSHOT_FIELDS)


def test_diff_metadata_review_period_months() -> None:
    # S-drift-1: review_period_months is now a snapshot field.
    # Old snapshot lacking the key → new snapshot with 24: reads old as None, diffs as None→24.
    old: dict = {"identifier": "SOP-GEN-001", "title": "T"}
    new: dict = {"identifier": "SOP-GEN-001", "title": "T", "review_period_months": 24}
    deltas = {d.field: d for d in diff_metadata(old, new)}
    assert "review_period_months" in deltas
    assert deltas["review_period_months"].from_value is None
    assert deltas["review_period_months"].to_value == 24
    assert deltas["review_period_months"].changed is True

    # 24 → 12 diffs as a change.
    old2: dict = {"review_period_months": 24}
    new2: dict = {"review_period_months": 12}
    deltas2 = {d.field: d for d in diff_metadata(old2, new2)}
    assert deltas2["review_period_months"].from_value == 24
    assert deltas2["review_period_months"].to_value == 12
    assert deltas2["review_period_months"].changed is True


# --- text redline -----------------------------------------------------------------------------


def test_redline_insert_delete_equal() -> None:
    hunks = redline("line one\nline two", "line one\nline two CHANGED")
    ops = [(h.op, h.text) for h in hunks]
    assert ("equal", "line one") in ops
    assert ("delete", "line two") in ops
    assert ("insert", "line two CHANGED") in ops


def test_redline_identical_is_all_equal() -> None:
    hunks = redline("a\nb\nc", "a\nb\nc")
    assert [h.op for h in hunks] == ["equal"]
    assert hunks[0].text == "a\nb\nc"


def test_redline_pure_insertion() -> None:
    hunks = redline("a", "a\nb")
    assert ("equal", "a") in [(h.op, h.text) for h in hunks]
    assert ("insert", "b") in [(h.op, h.text) for h in hunks]


def test_redline_empty_to_content_is_insert() -> None:
    hunks = redline("", "new line")
    assert [(h.op, h.text) for h in hunks] == [("insert", "new line")]
