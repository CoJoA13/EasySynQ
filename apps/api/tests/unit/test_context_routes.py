"""Route-resolution unit tests for the Context register surface (S-context-1; ``/context/summary``
added in S-context-2).

The ``/context/summary`` read + the ``/context/register*`` lifecycle routes must mount BEFORE
``/context/{issue_id}`` (the str path-convertor shadow — the S-pack-2 / S-risk lesson): FastAPI
matches ``{issue_id}`` with the str convertor and validates the UUID *after* matching, so a wrong
mount order resolves ``summary``/``register`` to ``get_context_issue_endpoint`` and 422s on the bad
UUID. Neither literal parses as a UUID, so the order is *safe but wrong* (a 422), which a no-grant
403 test could mask — assert the app-level resolution order directly. A real UUID still reaches the
row route."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI

_Resolve = Callable[[FastAPI, str, str], str | None]


def test_summary_resolves_before_issue_id(resolve_route_endpoint: _Resolve) -> None:
    """GET /context/summary must resolve to context_summary_endpoint, NOT the {issue_id}
    str-convertor route (the S-pack-2 / S-risk-4a shadow guard; S-context-2)."""
    from easysynq_api.main import create_app

    path = "/api/v1/context/summary"
    expected = "context_summary_endpoint"
    name = resolve_route_endpoint(create_app(), path, "GET")
    assert name == expected, f"{path} resolves to {name}, not {expected}"


def test_register_resolves_before_issue_id(resolve_route_endpoint: _Resolve) -> None:
    """GET /context/register must resolve to get_register_endpoint, NOT the {issue_id} str-convertor
    route (the static-before-{id} shadow guard)."""
    from easysynq_api.main import create_app

    path = "/api/v1/context/register"
    name = resolve_route_endpoint(create_app(), path, "GET")
    assert name == "get_register_endpoint", f"{path} resolves to {name}, not get_register_endpoint"


def test_register_publish_resolves_before_issue_id(resolve_route_endpoint: _Resolve) -> None:
    """POST /context/register/publish resolves to publish_register_endpoint (the nested static route
    is unambiguous, but pin it alongside the bare /register route)."""
    from easysynq_api.main import create_app

    path = "/api/v1/context/register/publish"
    name = resolve_route_endpoint(create_app(), path, "POST")
    assert name == "publish_register_endpoint", f"{path} resolves to {name}"


def test_real_uuid_resolves_to_get_context_issue(resolve_route_endpoint: _Resolve) -> None:
    """A real UUID still reaches the row route (the literal static routes never shadow a UUID)."""
    from easysynq_api.main import create_app

    path = "/api/v1/context/00000000-0000-0000-0000-000000000001"
    name = resolve_route_endpoint(create_app(), path, "GET")
    assert name == "get_context_issue_endpoint", f"{path} resolves to {name}"
