"""Unit cover for the Batch 11 operator alarms: the seeded templates, their variable whitelists, and
the out-of-band channel.

The load-bearing test here is ``test_seeded_templates_have_no_unsubstituted_slots``. The renderer
leaves a slot whose name is NOT in ``VARIABLE_WHITELIST`` as literal ``{{text}}`` in the delivered
notification, and it fills a whitelisted-but-unsupplied slot with an em dash. Both are silent: a
count-based test ("one notification was created") passes while the admin receives a body reading
"The scheduled backup to {{destination}} failed". So this renders the ACTUAL migration text against
the ACTUAL emitter context and fails on either symptom.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
import uuid
from typing import Any

import pytest

from easysynq_api.config import Settings
from easysynq_api.services.notifications.constants import (
    EVENT_BACKUP_FAILED,
    EVENT_INTEGRITY_ALARM,
    VARIABLE_WHITELIST,
)
from easysynq_api.services.notifications.ops_channel import (
    FAILED,
    SENT,
    SKIPPED,
    OperatorAlert,
    selected_channels,
    send_operator_alert,
)
from easysynq_api.services.notifications.render import _PLACEHOLDER, _substitute

_VERSIONS = pathlib.Path(__file__).resolve().parents[4] / "migrations" / "versions"
_SLOT = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*(?:\|\s*date\s*)?\}\}")


def _load_migration() -> Any:
    """Import the migration module by path so the test reads the SEEDED strings rather than a
    hand-copied duplicate that can drift away from what is actually in the database. Globbed, not
    hard-numbered, so a renumber during a rebase does not break the test."""
    matches = sorted(_VERSIONS.glob("*_operator_alarms.py"))
    assert len(matches) == 1, f"expected exactly one operator-alarms migration, found {matches}"
    spec = importlib.util.spec_from_file_location("m_operator_alarms", matches[0])
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The variables each emitter actually supplies (ops_events._emit_admin_alert merges these with
# recipient.first_name + prefs_link). Kept in step with emit_backup_failed / emit_integrity_alarm.
_EMITTER_CONTEXT: dict[str, dict[str, object]] = {
    EVENT_BACKUP_FAILED: {
        "recipient.first_name": "Avery",
        "prefs_link": "http://localhost/settings/notifications",
        "destination": "/mnt/backup",
        "error": "destination not writable: [Errno 30] Read-only file system",
        "failed_at": "2026-07-26T02:00:00+00:00",
    },
    EVENT_INTEGRITY_ALARM: {
        "recipient.first_name": "Avery",
        "prefs_link": "http://localhost/settings/notifications",
        "check": "chain_verify",
        "reason_summary": "row 4711: prev_hash mismatch",
        "break_count": 1,
        "detected_at": "2026-07-26T02:15:00+00:00",
    },
}


def test_seeded_template_slots_are_all_whitelisted() -> None:
    """Every ``{{slot}}`` in the migration's seeded bodies must appear in that event's whitelist —
    otherwise the renderer emits it literally (silent-failure #2 in the wiring checklist)."""
    module = _load_migration()
    for event_key, in_app_title, in_app_body, email_subject, email_body in module._SEEDS:
        allowed = VARIABLE_WHITELIST[event_key]
        for text in (in_app_title, in_app_body, email_subject, email_body):
            for slot in _SLOT.findall(text):
                assert slot in allowed, (
                    f"{event_key}: template uses {{{{{slot}}}}}, not whitelisted"
                )


def test_whitelist_has_no_slots_the_templates_never_use() -> None:
    """The inverse direction: a whitelist entry naming a variable no template renders is dead
    config, and — worse — an invitation to leak it into a future template. Both new events are
    operational-only, so this also pins that no ``subject.*`` slot creeps in (admins hold no
    ``document.*``; the ``system.email_delivery_failed`` precedent)."""
    module = _load_migration()
    for event_key, *texts in module._SEEDS:
        used = {slot for text in texts for slot in _SLOT.findall(text)}
        assert VARIABLE_WHITELIST[event_key] == used, f"{event_key}: whitelist ≠ slots used"
        assert not any(v.startswith("subject.") for v in used), (
            f"{event_key} leaks subject metadata"
        )


def test_seeded_templates_have_no_unsubstituted_slots() -> None:
    """Render the real seeded text with the real emitter context: no ``{{`` may survive, and no
    slot may fall back to the em-dash placeholder (which would mean the emitter never supplies it).
    This is the test that fails if someone adds a slot to a template and forgets the whitelist, or
    adds a whitelist entry the emitter never populates."""
    module = _load_migration()
    for event_key, *texts in module._SEEDS:
        allowed = VARIABLE_WHITELIST[event_key]
        variables = _EMITTER_CONTEXT[event_key]
        for text in texts:
            rendered = _substitute(text, variables, allowed)
            assert "{{" not in rendered, f"{event_key}: unsubstituted slot in {rendered!r}"
            # Check the placeholder per SLOT, not over the whole body — the em dash is also
            # ordinary prose punctuation in these templates, so a body-wide scan false-positives.
            for slot in _SLOT.findall(text):
                one = _substitute("{{" + slot + "}}", variables, allowed)
                assert one != _PLACEHOLDER, (
                    f"{event_key}: {{{{{slot}}}}} renders as the placeholder "
                    "— the emitter never supplies it"
                )


def test_backup_template_names_the_operational_consequence() -> None:
    """A backup-failure alert that only says 'a job failed' does not convey the harm the finding
    describes (the newest restorable archive silently ages). Pin that the email says so."""
    module = _load_migration()
    body = dict((s[0], s[4]) for s in module._SEEDS)[EVENT_BACKUP_FAILED]
    assert "restorable archive" in body


# --- the out-of-band channel -------------------------------------------------------------------


def _settings(**kw: object) -> Settings:
    return Settings(**kw)  # type: ignore[arg-type]


def test_selected_channels_parses_and_drops_unknown() -> None:
    assert selected_channels(_settings(ops_alert_channels="")) == []
    assert selected_channels(_settings(ops_alert_channels="syslog, webhook")) == [
        "syslog",
        "webhook",
    ]
    # A typo must not take the whole alert path down — the valid siblings still fire.
    assert selected_channels(_settings(ops_alert_channels="slack,smtp")) == ["smtp"]
    # De-duplicated, order preserved.
    assert selected_channels(_settings(ops_alert_channels="smtp,smtp,syslog")) == ["smtp", "syslog"]


@pytest.mark.asyncio
async def test_no_channel_configured_returns_empty_and_never_raises() -> None:
    """The default posture. The alert still reaches the container log (asserted by the caller's
    logging), and crucially the call does NOT raise — every caller is already handling a failure."""
    result = await send_operator_alert(
        _settings(),
        OperatorAlert(event="system.backup_failed", severity="error", summary="x"),
    )
    assert result == {}


def test_syslog_address_defaults_to_empty_not_dev_log() -> None:
    """⚠ The worker/beat images are python:3.12-slim-bookworm with no syslog daemon, and the shipped
    Compose services bind-mount no host socket — so a ``/dev/log`` default would look configured and
    reliably fail, leaving a database-outage alarm in the very container log it was meant to escape
    (Codex P2). Empty is honest: the channel reports ``skipped``, and the runbook gives the two ways
    to make it real (a reachable collector, or mounting the socket into worker AND beat)."""
    assert _settings().ops_alert_syslog_address == ""


@pytest.mark.asyncio
async def test_syslog_with_no_address_is_skipped_not_failed() -> None:
    result = await send_operator_alert(
        _settings(ops_alert_channels="syslog"),
        OperatorAlert(event="system.backup_failed", severity="error", summary="x"),
    )
    assert result == {"syslog": SKIPPED}


@pytest.mark.asyncio
async def test_syslog_reports_failed_on_a_dead_socket_not_sent() -> None:
    """The whole point of the channel is that it cannot silently no-op. ``SysLogHandler.emit`` sends
    every exception to ``handleError``, which by default prints to stderr and returns — so without
    the substituted ``handleError`` an absent socket would report ``sent``. This is the exact case
    Codex flagged: the shipped worker/beat containers have no ``/dev/log``."""
    result = await send_operator_alert(
        _settings(ops_alert_channels="syslog", ops_alert_syslog_address="/nonexistent/dev/log"),
        OperatorAlert(event="integrity.alarm", severity="critical", summary="x"),
    )
    assert result == {"syslog": FAILED}


@pytest.mark.asyncio
async def test_syslog_delivers_the_payload_over_a_live_socket() -> None:
    """The positive half — otherwise "not erroring" could pass for "delivering". Binds a real
    AF_UNIX datagram socket and asserts the alert bytes actually arrive."""
    import socket
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        path = str(pathlib.Path(d) / "log")
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        srv.bind(path)
        try:
            result = await send_operator_alert(
                _settings(ops_alert_channels="syslog", ops_alert_syslog_address=path),
                OperatorAlert(
                    event="integrity.alarm", severity="critical", summary="chain verify failed"
                ),
            )
            assert result == {"syslog": SENT}
            srv.settimeout(2)
            payload = srv.recv(65535)
        finally:
            srv.close()
    assert b"integrity.alarm" in payload
    assert b"chain verify failed" in payload


def test_seed_ids_are_deterministic_and_distinct() -> None:
    """The downgrade deletes by seed id so an operator-authored template and any inactive historical
    version survive it (Codex P2). That only holds if the id is a pure function of the event key —
    a fresh uuid4 per run would make the delete unmatchable."""
    module = _load_migration()
    first = [module._seed_id(s[0]) for s in module._SEEDS]
    second = [module._seed_id(s[0]) for s in module._SEEDS]
    assert first == second, "seed ids must be deterministic across calls"
    assert len(set(first)) == len(first), "each event key needs its own seed id"
    # And the ids the upgrade INSERTs are the ones the downgrade targets.
    assert all(isinstance(i, uuid.UUID) for i in first)


@pytest.mark.asyncio
async def test_selected_but_unconfigured_channel_reports_skipped_not_sent() -> None:
    """An operator who names ``smtp`` but sets no ``ops_alert_smtp_to`` must not be told the alert
    was delivered — that is the same silent-no-op class this batch exists to remove."""
    result = await send_operator_alert(
        _settings(ops_alert_channels="smtp,webhook"),
        OperatorAlert(event="integrity.alarm", severity="critical", summary="x"),
    )
    assert result == {"smtp": SKIPPED, "webhook": SKIPPED}


@pytest.mark.asyncio
async def test_one_dead_channel_does_not_suppress_the_others(monkeypatch: Any) -> None:
    """Channel isolation: an unreachable webhook must not stop the syslog line. Without the
    per-channel try/except a single raise would abort the fan-out at the first failure."""
    import easysynq_api.services.notifications.ops_channel as oc

    sent: list[str] = []

    async def _boom(settings: Settings, alert: OperatorAlert) -> str:
        raise RuntimeError("receiver unreachable")

    async def _ok(settings: Settings, alert: OperatorAlert) -> str:
        sent.append("syslog")
        return SENT

    monkeypatch.setattr(oc, "_send_webhook", _boom)
    monkeypatch.setattr(oc, "_send_syslog", _ok)
    result = await oc.send_operator_alert(
        _settings(ops_alert_channels="webhook,syslog", ops_alert_webhook_url="http://x"),
        OperatorAlert(event="integrity.alarm", severity="critical", summary="x"),
    )
    assert result == {"webhook": FAILED, "syslog": SENT}
    assert sent == ["syslog"]


@pytest.mark.asyncio
async def test_webhook_posts_the_alert_payload(monkeypatch: Any) -> None:
    import easysynq_api.services.notifications.ops_channel as oc

    posted: dict[str, Any] = {}

    class _Response:
        def raise_for_status(self) -> None:
            return None

    class _Client:
        def __init__(self, **kw: Any) -> None:
            posted["timeout"] = kw.get("timeout")

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def post(self, url: str, json: dict[str, Any], headers: dict[str, str]) -> _Response:
            posted.update({"url": url, "json": json, "headers": headers})
            return _Response()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    result = await oc.send_operator_alert(
        _settings(
            ops_alert_channels="webhook",
            ops_alert_webhook_url="https://ops.example/hook",
            ops_alert_webhook_token="tok",
        ),
        OperatorAlert(
            event="system.backup_failed",
            severity="error",
            summary="scheduled EasySynQ backup failed",
            detail={"destination": "/mnt/backup"},
            org_id="11111111-1111-1111-1111-111111111111",
        ),
    )
    assert result == {"webhook": SENT}
    assert posted["url"] == "https://ops.example/hook"
    assert posted["headers"]["authorization"] == "Bearer tok"
    assert posted["json"]["event"] == "system.backup_failed"
    assert posted["json"]["detail"] == {"destination": "/mnt/backup"}
    assert posted["json"]["org_id"] == "11111111-1111-1111-1111-111111111111"


def test_alert_text_is_plain_and_carries_the_detail() -> None:
    alert = OperatorAlert(
        event="integrity.alarm",
        severity="critical",
        summary="audit-trail integrity alarm",
        detail={"check": "chain_verify", "break_count": 2},
        org_id="org-1",
    )
    text = alert.as_text()
    assert text.startswith("audit-trail integrity alarm")
    assert "check: chain_verify" in text
    assert "break_count: 2" in text
    assert "org:      org-1" in text
