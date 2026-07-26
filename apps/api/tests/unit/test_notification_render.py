"""The logic-free, escaped template renderer (spec §5). No eval/Jinja; whitelisted vars; |date."""

from __future__ import annotations

import datetime
import zoneinfo

import pytest

from easysynq_api.services.common.org_clock import using_org_tz
from easysynq_api.services.notifications.render import _fmt_date, _substitute

pytestmark = pytest.mark.unit

_ALLOWED = frozenset({"subject.identifier", "subject.title", "task.due_at", "deep_link"})


def test_substitutes_verbatim_into_the_plain_text_sinks() -> None:
    """[Batch 11] Values substitute VERBATIM — both sinks are plain text (mail.py set_content() =
    text/plain; the SPA renders a React text node). HTML-escaping here garbled ordinary titles in
    email ("Q&amp;A" for "Q&A") and double-escaped them in the SPA. Any future HTML sink must escape
    at the sink."""
    out = _substitute(
        'Review {{subject.identifier}}: "{{subject.title}}"',
        {"subject.identifier": "SOP-1", "subject.title": "A <b>bold</b> & risky title"},
        _ALLOWED,
    )
    assert out == 'Review SOP-1: "A <b>bold</b> & risky title"'


def test_a_value_containing_a_slot_is_never_re_substituted() -> None:
    """Dropping the escape must not open slot injection: re.sub walks the ORIGINAL string, so a
    value that itself looks like a token is emitted literally, never re-scanned."""
    out = _substitute(
        "Title: {{subject.title}}",
        {"subject.title": "{{deep_link}}", "deep_link": "https://evil.example"},
        _ALLOWED,
    )
    assert out == "Title: {{deep_link}}"


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
