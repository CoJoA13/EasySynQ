import datetime

from easysynq_api.services.audit.partitions import upcoming_month_starts


def test_runway_covers_a_post_august_fresh_install() -> None:
    # Migration 0010 seeds a FIXED 2026-06/07/08 runway; a fresh install after Aug 2026 relies on
    # ensure_partitions()/upcoming_month_starts to cover the current month + the next two.
    starts = upcoming_month_starts(datetime.date(2026, 10, 15))
    assert starts == [
        datetime.date(2026, 10, 1),
        datetime.date(2026, 11, 1),
        datetime.date(2026, 12, 1),
    ]


def test_runway_rolls_across_a_year_boundary() -> None:
    starts = upcoming_month_starts(datetime.date(2026, 12, 3))
    assert starts == [
        datetime.date(2026, 12, 1),
        datetime.date(2027, 1, 1),
        datetime.date(2027, 2, 1),
    ]
