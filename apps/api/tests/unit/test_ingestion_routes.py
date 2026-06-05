"""Route-inventory PROOF (S-ing-1/2/3/4): the ingestion surface exposes EXACTLY the run/scan/review
verbs and **writes nothing to the vault** — no ``/commit`` (that is S-ing-5). S-ing-4 adds review
WRITES (per-file ``/files/{id}/decision``, bulk ``/decisions``, structural ``/merge`` + ``/split``)
and the ``/checklist`` + ``/decisions`` reads. Every review write is a **POST** (the decision log is
append-only; structural ops are commands) — NO PUT/PATCH/DELETE on the staging surface. The exact
POST allow-list (not merely a verb-class ban) catches a future stray ``POST .../commit``. No DB."""

from __future__ import annotations

from fastapi.routing import APIRoute
from starlette.routing import Match

from easysynq_api.api.ingestion import router

_EXPECTED = {
    ("/api/v1/admin/imports", "POST"),
    ("/api/v1/admin/imports", "GET"),
    ("/api/v1/admin/imports/{import_id}", "GET"),
    ("/api/v1/admin/imports/{import_id}/files", "GET"),
    ("/api/v1/admin/imports/{import_id}/files/{file_id}", "GET"),
    ("/api/v1/admin/imports/{import_id}/dupe-clusters", "GET"),
    ("/api/v1/admin/imports/{import_id}/version-families", "GET"),
    ("/api/v1/admin/imports/{import_id}/checklist", "GET"),
    ("/api/v1/admin/imports/{import_id}/decisions", "GET"),
    ("/api/v1/admin/imports/{import_id}/decisions", "POST"),
    ("/api/v1/admin/imports/{import_id}/files/{file_id}/decision", "POST"),
    ("/api/v1/admin/imports/{import_id}/merge", "POST"),
    ("/api/v1/admin/imports/{import_id}/split", "POST"),
    ("/api/v1/admin/imports/{import_id}/cancel", "POST"),
}


def _routes() -> list[APIRoute]:
    return [r for r in router.routes if isinstance(r, APIRoute)]


def test_exact_verb_surface() -> None:
    actual = {(r.path, m) for r in _routes() for m in r.methods}
    assert actual == _EXPECTED


def test_writes_nothing_to_the_vault() -> None:
    for route in _routes():
        assert "/commit" not in route.path, f"{route.path} exposes a vault-commit verb (S-ing-5)"
        # Review staging writes are POST-only (append-only decisions + structural commands) — no
        # in-place mutation verbs anywhere on the import surface.
        assert not ({"PUT", "PATCH", "DELETE"} & route.methods), f"{route.path} mutates in place"
    post_paths = {r.path for r in _routes() if "POST" in r.methods}
    assert post_paths == {
        "/api/v1/admin/imports",
        "/api/v1/admin/imports/{import_id}/decisions",
        "/api/v1/admin/imports/{import_id}/files/{file_id}/decision",
        "/api/v1/admin/imports/{import_id}/merge",
        "/api/v1/admin/imports/{import_id}/split",
        "/api/v1/admin/imports/{import_id}/cancel",
    }


def test_review_writes_gate_import_review_not_execute() -> None:
    # The SoD split (doc 09 §15/§9.4): review writes (decision/decisions/merge/split) gate
    # ``import.review``; only run-mechanics (create/cancel) gate ``import.execute``. The gate is the
    # require()-dependency's permission key, surfaced on the route dependant.
    review_paths = {
        "/api/v1/admin/imports/{import_id}/files/{file_id}/decision",
        "/api/v1/admin/imports/{import_id}/decisions",
        "/api/v1/admin/imports/{import_id}/merge",
        "/api/v1/admin/imports/{import_id}/split",
    }
    execute_paths = {
        "/api/v1/admin/imports",
        "/api/v1/admin/imports/{import_id}/cancel",
    }
    keys_by_path: dict[str, set[str]] = {}
    for route in _routes():
        if "POST" not in route.methods:
            continue
        keys: set[str] = set()
        for dep in route.dependant.dependencies:
            call = getattr(dep, "call", None)
            key = getattr(call, "_easysynq_permission_key", None)
            if key is not None:
                keys.add(key)
        keys_by_path[route.path] = keys
    for p in review_paths:
        assert keys_by_path.get(p) == {"import.review"}, (
            f"{p} must gate import.review, got {keys_by_path.get(p)}"
        )
    for p in execute_paths:
        assert keys_by_path.get(p) == {"import.execute"}, (
            f"{p} must gate import.execute, got {keys_by_path.get(p)}"
        )


def test_collection_route_not_shadowed_by_detail() -> None:
    # GET /api/v1/admin/imports resolves to the LIST route, never the {import_id} detail (the
    # str-path-convertor shadow guard, the S-pack-2 lesson — vacuously safe here since both live in
    # one router with no static literal in the {id} slot, but assert it).
    scope = {"type": "http", "method": "GET", "path": "/api/v1/admin/imports"}
    full = [r.path for r in _routes() if r.matches(scope)[0] == Match.FULL]
    assert full == ["/api/v1/admin/imports"]
