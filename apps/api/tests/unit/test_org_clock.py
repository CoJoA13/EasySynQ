import zoneinfo

from easysynq_api.services.common import org_clock


def test_pick_tz_prefers_calendar_then_org():
    assert org_clock.pick_tz("America/Chicago", "America/Denver") == zoneinfo.ZoneInfo(
        "America/Chicago"
    )
    assert org_clock.pick_tz(None, "America/Denver") == zoneinfo.ZoneInfo("America/Denver")


def test_pick_tz_skips_invalid_falls_through():
    # Invalid calendar tz → org tz; invalid both → env default (UTC in tests) → UTC.
    assert org_clock.pick_tz("Not/AZone", "Europe/Paris") == zoneinfo.ZoneInfo("Europe/Paris")
    assert org_clock.pick_tz("Not/AZone", "Also/Bad") == zoneinfo.ZoneInfo("UTC")
    assert org_clock.pick_tz(None, None) == zoneinfo.ZoneInfo("UTC")


def test_current_org_tz_unset_is_env_fallback():
    # No contextvar set → env easysynq_org_timezone (UTC in tests).
    assert org_clock.current_org_tz() == zoneinfo.ZoneInfo("UTC")


def test_using_org_tz_sets_and_resets():
    tokyo = zoneinfo.ZoneInfo("Asia/Tokyo")
    assert org_clock.current_org_tz() == zoneinfo.ZoneInfo("UTC")
    with org_clock.using_org_tz(tokyo):
        assert org_clock.current_org_tz() == tokyo
    assert org_clock.current_org_tz() == zoneinfo.ZoneInfo("UTC")


def test_set_request_org_tz_no_reset_within_call():
    org_clock.using_org_tz  # keep import used  # noqa: B018
    tok = zoneinfo.ZoneInfo("Asia/Tokyo")
    # set_request_org_tz mutates the current context (a request task) without a token.
    org_clock.set_request_org_tz(tok)
    assert org_clock.current_org_tz() == tok
    # Reset for test isolation (the next test runs in the same pytest task context).
    with org_clock.using_org_tz(zoneinfo.ZoneInfo("UTC")):
        assert org_clock.current_org_tz() == zoneinfo.ZoneInfo("UTC")
