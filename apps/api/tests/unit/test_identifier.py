"""S3 unit proofs for pure identifier + revision-label formatting (doc 04 §7)."""

from __future__ import annotations

import pytest

from easysynq_api.domain.vault import format_identifier, revision_label

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("type_code", "seq", "area", "expected"),
    [
        ("SOP", 14, "PUR", "SOP-PUR-014"),
        ("POL", 1, None, "POL-001"),
        ("WI", 7, "PRD", "WI-PRD-007"),
        ("FRM", 123, "QA", "FRM-QA-123"),
        ("SOP", 1000, "X", "SOP-X-1000"),  # pad is a minimum, not a truncation
    ],
)
def test_format_identifier(type_code: str, seq: int, area: str | None, expected: str) -> None:
    assert format_identifier(type_code, seq, area) == expected


def test_identifier_has_no_revision() -> None:
    # REV is version metadata, never part of the identifier (doc 04 §7).
    assert "Rev" not in format_identifier("SOP", 3, "PUR")


@pytest.mark.parametrize(
    ("seq", "expected"),
    [(1, "Rev A"), (2, "Rev B"), (26, "Rev Z"), (27, "Rev AA"), (52, "Rev AZ"), (53, "Rev BA")],
)
def test_revision_label_letter(seq: int, expected: str) -> None:
    assert revision_label(seq) == expected


def test_revision_label_numeric() -> None:
    assert revision_label(3, style="numeric") == "3"
