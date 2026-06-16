"""Route-resolution unit tests for the management-review surface (S-mr-2).

The ``/management-reviews/next-due`` literal must mount BEFORE ``/management-reviews/{review_id}``
(the str path-convertor shadow — the S-pack-2 lesson): FastAPI matches ``{review_id}`` with the str
convertor and validates the UUID *after* matching, so a wrong mount order resolves ``next-due`` to
``get_review_endpoint`` and 422s on the bad UUID. ``next-due`` never parses as a UUID, so the order
is *safe but wrong* (a 422) — assert the app-level resolution order is right."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI

_Resolve = Callable[[FastAPI, str, str], str | None]


def test_next_due_resolves_before_review_id(resolve_route_endpoint: _Resolve) -> None:
    """GET /management-reviews/next-due must resolve to next_due_endpoint, NOT the {review_id}
    str-convertor route (the S-pack-2 shadow guard). 'next-due' never parses as a UUID, so a
    wrong mount order fails 422-shaped, not 404 — assert the app-level resolution order."""
    from easysynq_api.main import create_app

    path = "/api/v1/management-reviews/next-due"
    name = resolve_route_endpoint(create_app(), path, "GET")
    assert name == "next_due_endpoint", f"{path} resolves to {name}, not next_due_endpoint"


def test_raise_capa_route_resolves(resolve_route_endpoint: _Resolve) -> None:
    """POST /management-reviews/{id}/outputs/{oid}/raise-capa resolves to the spawn endpoint (not a
    shadow). Distinct suffix from /outputs/{oid}, so no str-convertor collision — but pin it."""
    from easysynq_api.main import create_app

    path = "/api/v1/management-reviews/r/outputs/o/raise-capa"
    name = resolve_route_endpoint(create_app(), path, "POST")
    assert name == "raise_output_capa_endpoint", f"{path} resolves to {name}"


def test_raise_dcr_route_resolves(resolve_route_endpoint: _Resolve) -> None:
    """POST /management-reviews/{id}/outputs/{oid}/raise-dcr resolves to the DCR spawn endpoint."""
    from easysynq_api.main import create_app

    path = "/api/v1/management-reviews/r/outputs/o/raise-dcr"
    name = resolve_route_endpoint(create_app(), path, "POST")
    assert name == "raise_output_dcr_endpoint", f"{path} resolves to {name}"
