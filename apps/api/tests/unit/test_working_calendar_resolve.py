"""Unit tests for the pure ``working_days`` validator behind ``resolve_working_calendar``
(S-notify-6, Codex round-2). It must reject every structurally-broken shape so a malformed editor
write can't masquerade as a valid (often weekend-only / shifted) calendar."""

from easysynq_api.services.notifications.calendar_spec import parse_working_days


def test_valid_mon_fri():
    assert parse_working_days([1, 2, 3, 4, 5]) == frozenset({1, 2, 3, 4, 5})


def test_dedupes_and_accepts_full_week():
    assert parse_working_days([1, 1, 2, 7]) == frozenset({1, 2, 7})


def test_rejects_non_list():
    assert parse_working_days("67") is None  # iterable string would wrongly become {6,7}
    assert parse_working_days(5) is None
    assert parse_working_days({"mon": True}) is None
    assert parse_working_days(None) is None


def test_rejects_empty():
    assert parse_working_days([]) is None


def test_rejects_floats():
    assert parse_working_days([1.9, 2]) is None  # int(1.9) would silently coerce to 1


def test_rejects_bools():
    # bool is an int subclass → int(True)==1; must be rejected explicitly.
    assert parse_working_days([True, 2]) is None
    assert parse_working_days([1, False]) is None


def test_rejects_out_of_range():
    assert parse_working_days([0, 1]) is None
    assert parse_working_days([8]) is None
    assert parse_working_days([1, 9]) is None


def test_rejects_string_entries():
    assert parse_working_days(["1", "2"]) is None
