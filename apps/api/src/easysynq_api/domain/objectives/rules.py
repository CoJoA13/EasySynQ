"""Pure clause-6.2 objective math (S-obj-1) — no I/O, total, deterministic. RAG is computed at read
(N9: against a rule, never an auto-compliance verdict; N6: no SPC/forecast)."""

from __future__ import annotations

import datetime
from decimal import Decimal

from easysynq_api.db.models._objective_enums import ObjectiveDirection

Numeric = Decimal


def _on_or_better(current: Numeric, target: Numeric, direction: ObjectiveDirection) -> bool:
    if direction is ObjectiveDirection.HIGHER_IS_BETTER:
        return current >= target
    return current <= target


def _within_amber(
    current: Numeric, target: Numeric, threshold: Numeric, direction: ObjectiveDirection
) -> bool:
    """Between the at-risk threshold and the target (exclusive of green, inclusive of threshold)."""
    if direction is ObjectiveDirection.HIGHER_IS_BETTER:
        return threshold <= current < target
    return target < current <= threshold


def rag_status(
    *,
    current: Numeric | None,
    target: Numeric,
    direction: ObjectiveDirection,
    at_risk_threshold: Numeric | None,
) -> str:
    """'green' | 'amber' | 'red' | 'unmeasured'."""
    if current is None:
        return "unmeasured"
    if _on_or_better(current, target, direction):
        return "green"
    if at_risk_threshold is not None and _within_amber(
        current, target, at_risk_threshold, direction
    ):
        return "amber"
    return "red"


def pct_toward_target(
    *, current: Numeric | None, target: Numeric, baseline: Numeric | None
) -> float | None:
    """Fraction of the way from baseline (or 0) to target. None when unmeasured or zero span."""
    if current is None:
        return None
    base = baseline if baseline is not None else Decimal(0)
    span = target - base
    if span == 0:
        return None
    return float((current - base) / span)


def attainment(
    *,
    current: Numeric | None,
    target: Numeric,
    direction: ObjectiveDirection,
    due_date: datetime.date,
    today: datetime.date,
) -> str:
    """'in_progress' before the due date; at/after, 'met' iff the target is reached, else
    'missed'."""
    if today < due_date:
        return "in_progress"
    if current is None:
        return "missed"
    return "met" if _on_or_better(current, target, direction) else "missed"
