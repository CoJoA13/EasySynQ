"""Route-inventory PROOF (S-ing-1..5): the ingestion surface exposes EXACTLY the run / scan / review
/ commit verbs. S-ing-5 adds the ONE vault-writing verb — ``POST .../commit`` — gated on the SoD
``import.commit`` tier; it is POST-only + immutable (no PUT/PATCH/DELETE), so commit is still a
command, never an in-place mutation. S-ing-4 added review WRITES (per-file ``/files/{id}/decision``,
bulk ``/decisions``, structural ``/merge`` + ``/split``) + the ``/checklist`` + ``/decisions``
reads. Every staging write is a **POST** (the decision log is append-only; structural + commit ops
are commands) — NO PUT/PATCH/DELETE anywhere. The exact POST allow-list pins the surface. No DB."""

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
    ("/api/v1/admin/imports/{import_id}/commit", "POST"),
    ("/api/v1/admin/imports/{import_id}/cancel", "POST"),
}


def _routes() -> list[APIRoute]:
    return [r for r in router.routes if isinstance(r, APIRoute)]


def test_exact_verb_surface() -> None:
    actual = {(r.path, m) for r in _routes() for m in r.methods}
    assert actual == _EXPECTED


def test_commit_is_post_only_no_in_place_mutation() -> None:
    # S-ing-5: commit is the only vault-writing verb, but it is still a command — POST-only, never
    # PUT/PATCH/DELETE (an imported Effective doc is immutable; you re-POST to resume, never edit).
    for route in _routes():
        assert not ({"PUT", "PATCH", "DELETE"} & route.methods), f"{route.path} mutates in place"
    post_paths = {r.path for r in _routes() if "POST" in r.methods}
    assert post_paths == {
        "/api/v1/admin/imports",
        "/api/v1/admin/imports/{import_id}/decisions",
        "/api/v1/admin/imports/{import_id}/files/{file_id}/decision",
        "/api/v1/admin/imports/{import_id}/merge",
        "/api/v1/admin/imports/{import_id}/split",
        "/api/v1/admin/imports/{import_id}/commit",
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
    commit_paths = {
        "/api/v1/admin/imports/{import_id}/commit",
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
    for p in commit_paths:
        assert keys_by_path.get(p) == {"import.commit"}, (
            f"{p} must gate import.commit, got {keys_by_path.get(p)}"
        )


def test_collection_route_not_shadowed_by_detail() -> None:
    # GET /api/v1/admin/imports resolves to the LIST route, never the {import_id} detail (the
    # str-path-convertor shadow guard, the S-pack-2 lesson — vacuously safe here since both live in
    # one router with no static literal in the {id} slot, but assert it).
    scope = {"type": "http", "method": "GET", "path": "/api/v1/admin/imports"}
    full = [r.path for r in _routes() if r.matches(scope)[0] == Match.FULL]
    assert full == ["/api/v1/admin/imports"]
