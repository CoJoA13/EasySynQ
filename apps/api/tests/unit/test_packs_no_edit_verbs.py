"""Named PROOF (doc 06 §7.4): an Evidence Pack is **immutable & self-verifying** — once sealed its
content cannot change, and the surface exposes no way to edit a pack in place.

Unlike records (which have a sanctioned disposition state-advance PATCH), S-pack-1 packs have **no**
mutable state machine on this router: the only writes are creating a pack (POST) and triggering its
build (POST …/generate — a sanctioned build trigger, not a content edit). So the proof is the strong
form: **zero PUT, zero PATCH, zero DELETE**, and the POST surface is exactly the two creation/build
routes. The S-pack-2 external-delivery revoke will deliberately widen this whitelist (the records→
disposition precedent). No DB needed."""

from __future__ import annotations

from fastapi.routing import APIRoute

from easysynq_api.api.packs import router as packs_router

_EXPECTED_POSTS = {
    "/api/v1/evidence-packs",
    "/api/v1/evidence-packs/{pack_id}/generate",
}


def _routes() -> list[APIRoute]:
    return [r for r in packs_router.routes if isinstance(r, APIRoute)]


def test_packs_router_has_no_mutating_verbs() -> None:
    routes = _routes()
    assert routes, "expected the evidence-packs router to mount routes"
    for route in routes:
        # A sealed pack is immutable — no in-place edit verb is permitted on any pack route.
        for verb in ("PUT", "PATCH", "DELETE"):
            if verb in route.methods:
                raise AssertionError(f"evidence-packs route {route.path} exposes {verb}")


def test_packs_post_surface_is_exactly_create_and_generate() -> None:
    # Exact whitelist: the only POSTs are pack creation and the build trigger — a stray future POST
    # (e.g. an S-pack-2 deliver/revoke landing early) breaks this and forces a deliberate update.
    post_paths = {r.path for r in _routes() if "POST" in r.methods}
    assert post_paths == _EXPECTED_POSTS


def test_packs_read_surface_is_get_only() -> None:
    # Non-vacuous: the list, the single-pack poll, and the download are present and GET-only.
    by_path: dict[str, set[str]] = {}
    for route in _routes():
        by_path.setdefault(route.path, set()).update(route.methods)
    assert by_path.get("/api/v1/evidence-packs/{pack_id}") == {"GET"}
    assert by_path.get("/api/v1/evidence-packs/{pack_id}/download") == {"GET"}
    assert "GET" in by_path.get("/api/v1/evidence-packs", set())
