"""Route-inventory PROOF (S-ing-1): the ingestion surface exposes EXACTLY the five run/scan verbs
and
**writes nothing to the vault** — no ``/commit`` (slice 5), no ``/decision`` (slice 4). The exact
POST allow-list (not merely a PUT/PATCH/DELETE ban) catches a future stray ``POST …/commit`` or
``POST …/files/{id}/decision`` — both POSTs that a verb-class ban would miss. No DB needed."""

from __future__ import annotations

from fastapi.routing import APIRoute
from starlette.routing import Match

from easysynq_api.api.ingestion import router

_EXPECTED = {
    ("/api/v1/admin/imports", "POST"),
    ("/api/v1/admin/imports", "GET"),
    ("/api/v1/admin/imports/{import_id}", "GET"),
    ("/api/v1/admin/imports/{import_id}/files", "GET"),
    ("/api/v1/admin/imports/{import_id}/cancel", "POST"),
}


def _routes() -> list[APIRoute]:
    return [r for r in router.routes if isinstance(r, APIRoute)]


def test_exact_verb_surface() -> None:
    actual = {(r.path, m) for r in _routes() for m in r.methods}
    assert actual == _EXPECTED


def test_writes_nothing_to_the_vault() -> None:
    for route in _routes():
        assert "/commit" not in route.path, f"{route.path} exposes a vault-commit verb"
        assert "/decision" not in route.path, f"{route.path} exposes a review-decision verb"
        assert not ({"PUT", "PATCH", "DELETE"} & route.methods), f"{route.path} mutates in place"
    post_paths = {r.path for r in _routes() if "POST" in r.methods}
    assert post_paths == {
        "/api/v1/admin/imports",
        "/api/v1/admin/imports/{import_id}/cancel",
    }


def test_collection_route_not_shadowed_by_detail() -> None:
    # GET /api/v1/admin/imports resolves to the LIST route, never the {import_id} detail (the
    # str-path-convertor shadow guard, the S-pack-2 lesson — vacuously safe here since both live in
    # one router with no static literal in the {id} slot, but assert it).
    scope = {"type": "http", "method": "GET", "path": "/api/v1/admin/imports"}
    full = [r.path for r in _routes() if r.matches(scope)[0] == Match.FULL]
    assert full == ["/api/v1/admin/imports"]
