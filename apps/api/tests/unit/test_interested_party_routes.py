"""Route-resolution unit tests for the Interested Parties register surface (S-interested-parties-1).

The ``/interested-parties/register*`` lifecycle routes must mount BEFORE
``/interested-parties/{party_id}`` (the str path-convertor shadow — the S-pack-2 / S-context
lesson): FastAPI matches ``{party_id}`` with the str convertor and validates the UUID *after*
matching, so a
wrong mount order resolves ``register`` to ``get_interested_party_endpoint`` and 422s on the bad
UUID. ``register`` does not parse as a UUID, so the order is *safe but wrong* (a 422), which a
no-grant 403 test could mask — assert the app-level resolution order directly. A real UUID still
reaches the row route.

(``GET /interested-parties/summary`` lands in S-interested-parties-2 — not asserted here.)"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI

_Resolve = Callable[[FastAPI, str, str], str | None]


def test_register_resolves_before_party_id(resolve_route_endpoint: _Resolve) -> None:
    """GET /interested-parties/register must resolve to get_register_endpoint, NOT the {party_id}
    str-convertor route (the static-before-{id} shadow guard)."""
    from easysynq_api.main import create_app

    path = "/api/v1/interested-parties/register"
    name = resolve_route_endpoint(create_app(), path, "GET")
    assert name == "get_register_endpoint", f"{path} resolves to {name}, not get_register_endpoint"


def test_register_publish_resolves_before_party_id(resolve_route_endpoint: _Resolve) -> None:
    """POST /interested-parties/register/publish resolves to publish_register_endpoint (the nested
    static route is unambiguous, but pin it alongside the bare /register route)."""
    from easysynq_api.main import create_app

    path = "/api/v1/interested-parties/register/publish"
    name = resolve_route_endpoint(create_app(), path, "POST")
    assert name == "publish_register_endpoint", f"{path} resolves to {name}"


def test_register_start_revision_resolves_before_party_id(resolve_route_endpoint: _Resolve) -> None:
    """POST /interested-parties/register/start-revision resolves to start_register_revision_endpoint
    (pin the nested static route alongside the bare /register route)."""
    from easysynq_api.main import create_app

    path = "/api/v1/interested-parties/register/start-revision"
    name = resolve_route_endpoint(create_app(), path, "POST")
    assert name == "start_register_revision_endpoint", f"{path} resolves to {name}"


def test_real_uuid_resolves_to_get_interested_party(resolve_route_endpoint: _Resolve) -> None:
    """A real UUID still reaches the row route (the literal static routes never shadow a UUID)."""
    from easysynq_api.main import create_app

    path = "/api/v1/interested-parties/00000000-0000-0000-0000-000000000001"
    name = resolve_route_endpoint(create_app(), path, "GET")
    assert name == "get_interested_party_endpoint", f"{path} resolves to {name}"
