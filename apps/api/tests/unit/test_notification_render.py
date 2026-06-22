"""The logic-free, escaped template renderer (spec §5). No eval/Jinja; whitelisted vars; |date."""

from __future__ import annotations

import datetime

import pytest

from easysynq_api.services.notifications.render import _substitute

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
