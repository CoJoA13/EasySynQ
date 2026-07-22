"""Unit proofs for the chain-linker safe-prefix watermark (CR-2). The pure function is where ALL the
correctness lives (boundary / in-flight-gap / rollback-gap / dense cases) — no DB, no Docker. The
DB→snapshot wiring + the live reorder race are proven in the integration suite."""

from __future__ import annotations

from easysynq_api.services.audit.watermark import WatermarkState, advance_watermark


def _st(
    watermark: int, stall_xmax: int | None = None, stall_ceiling: int | None = None
) -> WatermarkState:
    return WatermarkState(watermark=watermark, stall_xmax=stall_xmax, stall_ceiling=stall_ceiling)


def test_no_rows_above_watermark_holds() -> None:
    step = advance_watermark(_st(100), ids_above=[], snap_xmin=50, snap_xmax=50)
    assert step.link_up_to == 100
    assert step.state == _st(100)


def test_fully_dense_window_advances_to_max_and_clears_stall() -> None:
    # 101..105 contiguous from w+1 → link all; the pending gap committed → stall clears.
    step = advance_watermark(
        _st(100, stall_xmax=10, stall_ceiling=104),
        ids_above=[101, 102, 103, 104, 105],
        snap_xmin=5,
        snap_xmax=20,
    )
    assert step.link_up_to == 105
    assert step.state == _st(105)


def test_gap_first_seen_records_stall_and_links_dense_prefix() -> None:
    # 101,102 dense; 103 missing (in-flight); 104 visible → gap at 103. No prior stall → record one.
    step = advance_watermark(_st(100), ids_above=[101, 102, 104], snap_xmin=30, snap_xmax=42)
    assert step.link_up_to == 102  # dense prefix only — never link 104 ahead of the missing 103
    assert step.state == _st(102, stall_xmax=42, stall_ceiling=104)


def test_gap_waiting_keeps_stall_unchanged() -> None:
    # Gap still at 103; proof not yet (xmin 30 < stall_xmax 42). KEEP the same stall (resetting xmax
    # each tick would move the goalpost so xmin never catches it → wait forever).
    prior = _st(102, stall_xmax=42, stall_ceiling=104)
    step = advance_watermark(prior, ids_above=[104], snap_xmin=30, snap_xmax=60)
    assert step.link_up_to == 102
    assert step.state == prior


def test_gap_proof_holds_skips_rollback_batch_to_ceiling() -> None:
    # xmin 42 ≥ stall_xmax 42 → the gap's owner txn has ended and 103 is still absent → rollback →
    # skip the batch to the recorded ceiling and clear the stall.
    prior = _st(102, stall_xmax=42, stall_ceiling=104)
    step = advance_watermark(prior, ids_above=[104], snap_xmin=42, snap_xmax=90)
    assert step.link_up_to == 104
    assert step.state == _st(104)


def test_gap_immediately_at_watermark_plus_one() -> None:
    # w+1 (101) missing, 102 visible → gap right at the frontier; dense_end stays at w.
    step = advance_watermark(_st(100), ids_above=[102, 103], snap_xmin=30, snap_xmax=42)
    assert step.link_up_to == 100
    assert step.state == _st(100, stall_xmax=42, stall_ceiling=103)


def test_in_flight_gap_commits_then_clears_stall() -> None:
    # Was waiting on gap 103; now 103 committed → 103,104 dense from w+1 → clear stall, link all.
    prior = _st(102, stall_xmax=42, stall_ceiling=104)
    step = advance_watermark(prior, ids_above=[103, 104], snap_xmin=30, snap_xmax=99)
    assert step.link_up_to == 104
    assert step.state == _st(104)


def test_gap_above_prior_ceiling_records_fresh_stall() -> None:
    # The old gap (≤104) has committed (dense reached 105); a NEW gap appears at 106 (> old ceiling
    # 104), and the old observation's proof does not hold (xmin 50 < stall_xmax 200) → re-record a
    # fresh stall for the new gap rather than reuse the stale ceiling.
    prior = _st(104, stall_xmax=200, stall_ceiling=104)
    step = advance_watermark(prior, ids_above=[105, 107], snap_xmin=50, snap_xmax=120)
    assert step.link_up_to == 105
    assert step.state == _st(105, stall_xmax=120, stall_ceiling=107)


def test_proof_holds_with_grown_dense_run_takes_ceiling() -> None:
    # Between stall and proof some in-flight ids committed, growing the dense run, but 103 remains a
    # rollback ≤ ceiling → advance to the recorded ceiling (the whole decided batch), skipping 103.
    prior = _st(100, stall_xmax=42, stall_ceiling=110)
    step = advance_watermark(
        prior,
        ids_above=[101, 102, 104, 105, 106, 107, 108, 109, 110],
        snap_xmin=42,
        snap_xmax=200,
    )
    assert step.link_up_to == 110
    assert step.state == _st(110)


def test_classic_reorder_scenario_end_to_end() -> None:
    """The bug the fix prevents: a sweep holds ids 1000-1049 uncommitted while id 1050 commits.
    W must not pass 999 until the sweep commits, then links 1000.. in order (never 1050 first)."""
    # Tick 1: only 1050 visible above w=999 (1000-1049 in-flight in the sweep). Gap at 1000.
    step = advance_watermark(_st(999), ids_above=[1050], snap_xmin=500, snap_xmax=700)
    assert step.link_up_to == 999
    assert step.state == _st(999, stall_xmax=700, stall_ceiling=1050)

    # Tick 2: sweep still open (xmin 600 < 700) → hold, keep the stall.
    step = advance_watermark(step.state, ids_above=[1050], snap_xmin=600, snap_xmax=900)
    assert step.link_up_to == 999
    assert step.state == _st(999, stall_xmax=700, stall_ceiling=1050)

    # Tick 3: sweep COMMITTED → 1000..1050 all visible + dense → link the whole range in id order.
    step = advance_watermark(
        step.state, ids_above=list(range(1000, 1051)), snap_xmin=800, snap_xmax=1000
    )
    assert step.link_up_to == 1050
    assert step.state == _st(1050)
