"""Route-resolution unit tests for the Risk & Opportunity register surface (S-risk-4a).

The literal ``/risks/summary`` (and the ``/risks/register*`` lifecycle routes) must mount BEFORE
``/risks/{risk_id}`` (the str path-convertor shadow — the S-pack-2 lesson): FastAPI matches
``{risk_id}`` with the str convertor and validates the UUID *after* matching, so a wrong mount order
resolves ``summary`` to ``get_risk_endpoint`` and 422s on the bad UUID. ``summary`` never parses as
a UUID, so the order is *safe but wrong* (a 422), which a no-grant 403 test could mask — assert the
app-level resolution order directly. A real UUID still reaches the row route."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI

_Resolve = Callable[[FastAPI, str, str], str | None]


def test_summary_resolves_before_risk_id(resolve_route_endpoint: _Resolve) -> None:
    """GET /risks/summary must resolve to risk_summary_endpoint, NOT the {risk_id} str-convertor
    route (the S-pack-2 shadow guard)."""
    from easysynq_api.main import create_app

    path = "/api/v1/risks/summary"
    name = resolve_route_endpoint(create_app(), path, "GET")
    assert name == "risk_summary_endpoint", f"{path} resolves to {name}, not risk_summary_endpoint"


def test_register_resolves_before_risk_id(resolve_route_endpoint: _Resolve) -> None:
    """GET /risks/register resolves to get_register_endpoint (the pre-existing S-risk-1b ordering,
    now pinned alongside the new summary route)."""
    from easysynq_api.main import create_app

    path = "/api/v1/risks/register"
    name = resolve_route_endpoint(create_app(), path, "GET")
    assert name == "get_register_endpoint", f"{path} resolves to {name}, not get_register_endpoint"


def test_real_uuid_resolves_to_get_risk(resolve_route_endpoint: _Resolve) -> None:
    """A real UUID still reaches the row route (the literal static routes never shadow a UUID)."""
    from easysynq_api.main import create_app

    path = "/api/v1/risks/00000000-0000-0000-0000-000000000001"
    name = resolve_route_endpoint(create_app(), path, "GET")
    assert name == "get_risk_endpoint", f"{path} resolves to {name}, not get_risk_endpoint"
