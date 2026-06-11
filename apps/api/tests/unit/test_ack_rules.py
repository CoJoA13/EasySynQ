"""Unit tests for the S-ack-1 pure rules (domain/ack/rules.py): the R43 last-MAJOR satisfaction
boundary (incl. the no-MAJOR fallback) and the sweep's cancel-before-mint set-algebra."""

from __future__ import annotations

import uuid

import pytest

from easysynq_api.domain.ack.rules import last_major_seq, plan_obligations

pytestmark = pytest.mark.unit

U1, U2, U3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def test_last_major_is_the_newest_major_at_or_below_current() -> None:
    # (seq, is_major): 1.0 MAJOR, 1.1 MINOR, 2.0 MAJOR, 2.1 MINOR — current seq 4.
    versions = [(1, True), (2, False), (3, True), (4, False)]
    assert last_major_seq(versions, current_seq=4) == 3


def test_last_major_ignores_versions_beyond_current() -> None:
    # A scheduled future MAJOR (seq 5) must not move the boundary for the current Effective (4).
    versions = [(1, True), (2, False), (3, True), (4, False), (5, True)]
    assert last_major_seq(versions, current_seq=4) == 3


def test_no_major_falls_back_to_lowest_seq() -> None:
    # _checkin defaults MINOR, so a chain with no MAJOR is real: any-version ack satisfies.
    versions = [(1, False), (2, False)]
    assert last_major_seq(versions, current_seq=2) == 1


def test_phantom_interleaved_major_would_move_boundary_without_state_filter() -> None:
    # The pure function is state-blind by design — the QUERY layer must pre-filter to
    # ever-governed versions (queries.boundary_seq); this pins the division of responsibility.
    versions = [(1, True), (2, True), (3, False)]  # seq 2 = a phantom MAJOR if not filtered out
    assert last_major_seq(versions, current_seq=3) == 2  # blind: includes it
    assert last_major_seq([(1, True), (3, False)], current_seq=3) == 1  # filtered: correct


def test_satisfaction_is_seq_at_or_above_boundary() -> None:
    boundary = last_major_seq([(1, True), (2, False), (3, True)], current_seq=3)
    assert boundary == 3
    # acked Rev 1.1 (seq 2) — below the MAJOR boundary → NOT satisfied; acked seq 3 → satisfied.
    assert not (2 >= boundary)
    assert 3 >= boundary


def test_plan_mints_unsatisfied_audience_without_open_tasks() -> None:
    to_mint, to_cancel = plan_obligations(
        audience={U1, U2}, satisfied=set(), open_tasks={}, last_major=3
    )
    assert to_mint == {U1, U2}
    assert to_cancel == set()


def test_plan_skips_satisfied_and_already_open() -> None:
    to_mint, to_cancel = plan_obligations(
        audience={U1, U2, U3}, satisfied={U1}, open_tasks={U2: 3}, last_major=3
    )
    assert to_mint == {U3}
    assert to_cancel == set()


def test_plan_cancels_left_audience_and_stale_pins_and_remints_in_one_pass() -> None:
    # U1 left the audience; U2's task pins seq 1 < last_major 3 (a MAJOR superseded it);
    # U3's task pins the boundary itself — survives. CANCEL-BEFORE-MINT in ONE pass: the
    # stale-pinned-but-still-in-audience U2 ends the sweep with exactly one fresh task.
    to_mint, to_cancel = plan_obligations(
        audience={U2, U3}, satisfied=set(), open_tasks={U1: 3, U2: 1, U3: 3}, last_major=3
    )
    assert to_cancel == {U1, U2}
    assert to_mint == {U2}


def test_plan_cancels_satisfied_open_tasks_defensively() -> None:
    to_mint, to_cancel = plan_obligations(
        audience={U1}, satisfied={U1}, open_tasks={U1: 3}, last_major=3
    )
    assert to_mint == set()
    assert to_cancel == {U1}


def test_plan_boundary_pinned_eq_last_major_survives() -> None:
    # pinned == last_major: the task is fresh — not stale, not cancelled, not re-minted.
    to_mint, to_cancel = plan_obligations(
        audience={U1}, satisfied=set(), open_tasks={U1: 3}, last_major=3
    )
    assert to_mint == set()
    assert to_cancel == set()
