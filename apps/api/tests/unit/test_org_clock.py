import uuid
import zoneinfo

import pytest

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
    tok = zoneinfo.ZoneInfo("Asia/Tokyo")
    # Wrap the mutating body in using_org_tz so the reset restores the pre-test state (env
    # fallback) — set_request_org_tz captures no token, so without this the contextvar would leak
    # Tokyo to later test files that read current_org_tz() bare.
    with org_clock.using_org_tz(zoneinfo.ZoneInfo("UTC")):
        # set_request_org_tz mutates the current context (a request task) without a token.
        org_clock.set_request_org_tz(tok)
        assert org_clock.current_org_tz() == tok
    # The using_org_tz reset restored the pre-block value (the env fallback).
    assert org_clock.current_org_tz() == zoneinfo.ZoneInfo("UTC")


async def test_get_current_user_sets_org_tz_contextvar(monkeypatch: pytest.MonkeyPatch) -> None:
    """The auth boundary wires the canonical org tz into the request context (S-orgtz-unify).

    Deterministic + Docker-free complement to the end-to-end HTTP propagation test (which is only
    mutation-distinguishing ~14/24 hours): monkeypatch the user-resolution + tz-resolution that
    get_current_user calls, then assert get_current_user set the contextvar to the resolved tz.
    Fails if get_current_user omits the set_request_org_tz call (mutation-distinguishing).
    """
    from easysynq_api.auth import dependencies as deps

    org_id = uuid.uuid4()
    tz = zoneinfo.ZoneInfo("Pacific/Kiritimati")

    class _User:
        def __init__(self, oid: uuid.UUID) -> None:
            self.org_id = oid

    async def _fake_resolve_user(request: object, jwks: object, session: object) -> object:
        return _User(org_id)

    async def _fake_resolve_org_tz(session: object, oid: uuid.UUID) -> zoneinfo.ZoneInfo:
        assert oid == org_id  # get_current_user must pass the resolved user's own org_id
        return tz

    monkeypatch.setattr(deps, "resolve_current_user", _fake_resolve_user)
    monkeypatch.setattr(deps, "resolve_org_tz", _fake_resolve_org_tz)

    # using_org_tz(UTC) isolates + auto-resets so this test never leaks the contextvar.
    with org_clock.using_org_tz(zoneinfo.ZoneInfo("UTC")):
        user = await deps.get_current_user(None, None, None)  # type: ignore[arg-type]
        assert user.org_id == org_id
        assert org_clock.current_org_tz() == tz
