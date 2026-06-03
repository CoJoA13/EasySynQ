"""Unit proofs for the Compliance Checklist coverage rule (slice S10)."""

from __future__ import annotations

from easysynq_api.services.reports import coverage_status


def test_coverage_status_covered_when_effective() -> None:
    # ≥1 mapped doc has an Effective version → COVERED (Mapped+Effective rule).
    assert coverage_status(mapped=3, effective=1) == "COVERED"
    assert coverage_status(mapped=1, effective=1) == "COVERED"


def test_coverage_status_partial_when_mapped_not_effective() -> None:
    # Mapped but nothing Effective yet (Draft/InReview/Approved) → PARTIAL.
    assert coverage_status(mapped=2, effective=0) == "PARTIAL"
    assert coverage_status(mapped=1, effective=0) == "PARTIAL"


def test_coverage_status_gap_when_unmapped() -> None:
    assert coverage_status(mapped=0, effective=0) == "GAP"
