"""The logic-free, escaped template renderer (spec §5). No eval/Jinja; whitelisted vars; |date."""

from __future__ import annotations

import datetime
import zoneinfo

import pytest

from easysynq_api.services.common.org_clock import using_org_tz
from easysynq_api.services.notifications.render import _fmt_date, _substitute

pytestmark = pytest.mark.unit

_ALLOWED = frozenset({"subject.identifier", "subject.title", "task.due_at", "deep_link"})


def test_substitutes_and_escapes() -> None:
    out = _substitute(
        'Review {{subject.identifier}}: "{{subject.title}}"',
        {"subject.identifier": "SOP-1", "subject.title": "A <b>bold</b> & risky title"},
        _ALLOWED,
    )
    assert out == 'Review SOP-1: "A &lt;b&gt;bold&lt;/b&gt; &amp; risky title"'


def test_missing_var_renders_placeholder_not_raises() -> None:
    out = _substitute("Hi {{subject.title}}", {"subject.identifier": "SOP-1"}, _ALLOWED)
    assert out == "Hi —"  # known-but-absent → em-dash placeholder


def test_unknown_var_is_left_literal_not_substituted() -> None:
    # A token not in the whitelist is NOT a substitution slot (defense-in-depth).
    out = _substitute("{{evil.secret}}", {"evil.secret": "leak"}, _ALLOWED)
    assert out == "{{evil.secret}}"


def test_date_filter_formats_datetime() -> None:
    when = datetime.datetime(2026, 6, 21, 9, 0, tzinfo=datetime.UTC)
    out = _substitute("Due {{task.due_at | date}}", {"task.due_at": when}, _ALLOWED)
    assert out == "Due 2026-06-21"


def test_date_filter_null_is_dash() -> None:
    out = _substitute("Due {{task.due_at | date}}", {"task.due_at": None}, _ALLOWED)
    assert out == "Due —"


# --- _fmt_date tz-reconvert tests (S-orgtz-unify) ---


def test_fmt_date_reconverts_aware_datetime_to_org_tz() -> None:
    # 2026-06-29 00:00 in Asia/Tokyo (UTC+9) is 2026-06-28 15:00 UTC. Rendering the UTC instant
    # under the org tz must show 2026-06-29 (the local date), NOT 2026-06-28 (the UTC date).
    utc_instant = datetime.datetime(2026, 6, 28, 15, 0, tzinfo=datetime.UTC)
    with using_org_tz(zoneinfo.ZoneInfo("Asia/Tokyo")):
        assert _fmt_date(utc_instant) == "2026-06-29"
    # Unset context (UTC fallback) → the UTC date.
    assert _fmt_date(utc_instant) == "2026-06-28"


def test_fmt_date_passes_naive_and_date_through() -> None:
    assert _fmt_date(datetime.datetime(2026, 6, 28, 15, 0)) == "2026-06-28"  # naive: no convert
    assert _fmt_date(datetime.date(2026, 6, 28)) == "2026-06-28"
