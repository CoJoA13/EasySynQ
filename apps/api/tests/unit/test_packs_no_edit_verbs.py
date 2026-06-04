"""Named PROOF (doc 06 §7.4): an Evidence Pack is **immutable & self-verifying** — once sealed its
*content* cannot change, and the surface exposes no way to edit a pack in place.

The authenticated packs router has **zero PUT, zero PATCH, zero DELETE** — a sealed pack has no
in-place edit verb. Its POSTs are exactly: create a pack, trigger its build, and (S-pack-2) the
share-link **lifecycle** — ``…/share`` (mint a delivery link) and ``…/share-links/{id}/revoke``
(revoke one). These are a *deliberate* widening (the records→disposition PATCH precedent): they
manage the time-boxed delivery grants, NOT the sealed pack content (the ZIP/manifest stay frozen).

The PUBLIC delivery router (``api/pack_share.py``) is the separate, latch-exempt, **GET-only**,
**no-auth** surface where the signed token IS the authorization — proven here too. No DB needed."""

from __future__ import annotations

from fastapi.routing import APIRoute

from easysynq_api.api.pack_share import router as pack_share_router
from easysynq_api.api.packs import router as packs_router
from easysynq_api.db.session import get_session
from easysynq_api.main import _LATCH_EXEMPT_EXACT

# The exact POST surface: pack creation + build trigger (S-pack-1) + share lifecycle (S-pack-2).
# Revoke is a POST (a timestamp flip on a separate grant row — the records→disposition precedent),
# NOT a DELETE/PATCH on the pack; pack content is never mutated.
_EXPECTED_POSTS = {
    "/api/v1/evidence-packs",
    "/api/v1/evidence-packs/{pack_id}/generate",
    "/api/v1/evidence-packs/{pack_id}/share",
    "/api/v1/evidence-packs/{pack_id}/share-links/{link_id}/revoke",
}

_PUBLIC_PATHS = {
    "/api/v1/evidence-packs/shared",
    "/api/v1/evidence-packs/shared/download",
}


def _routes() -> list[APIRoute]:
    return [r for r in packs_router.routes if isinstance(r, APIRoute)]


def _public_routes() -> list[APIRoute]:
    return [r for r in pack_share_router.routes if isinstance(r, APIRoute)]


def test_packs_router_has_no_mutating_verbs() -> None:
    routes = _routes()
    assert routes, "expected the evidence-packs router to mount routes"
    for route in routes:
        # A sealed pack is immutable — no in-place edit verb is permitted on any pack route.
        for verb in ("PUT", "PATCH", "DELETE"):
            if verb in route.methods:
                raise AssertionError(f"evidence-packs route {route.path} exposes {verb}")


def test_packs_post_surface_is_create_generate_and_share_lifecycle() -> None:
    # Exact whitelist: a stray future mutating POST breaks this and forces a deliberate update.
    post_paths = {r.path for r in _routes() if "POST" in r.methods}
    assert post_paths == _EXPECTED_POSTS


def test_packs_read_surface_is_get_only() -> None:
    # Non-vacuous: list, single-pack poll, download, and the share-link list are all GET-only.
    by_path: dict[str, set[str]] = {}
    for route in _routes():
        by_path.setdefault(route.path, set()).update(route.methods)
    assert by_path.get("/api/v1/evidence-packs/{pack_id}") == {"GET"}
    assert by_path.get("/api/v1/evidence-packs/{pack_id}/download") == {"GET"}
    assert by_path.get("/api/v1/evidence-packs/{pack_id}/share-links") == {"GET"}
    assert "GET" in by_path.get("/api/v1/evidence-packs", set())


def test_public_delivery_router_is_get_only_and_unauthenticated() -> None:
    routes = _public_routes()
    assert {r.path for r in routes} == _PUBLIC_PATHS
    for route in routes:
        assert route.methods == {"GET"}, f"public {route.path} must be GET-only"
        # No auth/PEP dependency: the signed token IS the authorization (the /verify precedent). The
        # ONLY dependency is the DB session — an authenticated route would also carry the PEP
        # ``require(...)`` gate (which loads the current user). A subset check makes that explicit.
        dep_calls = {d.call for d in route.dependant.dependencies}
        assert dep_calls <= {get_session}, f"public {route.path} has an unexpected dependency"


def test_public_delivery_paths_are_latch_exempt() -> None:
    # The setup-latch 423 must NOT gate the public delivery surface (a guest has no setup session),
    # and the entries are BOUNDARY-ANCHORED exact (not a prefix that could un-latch siblings).
    assert _PUBLIC_PATHS <= _LATCH_EXEMPT_EXACT


def test_public_delivery_paths_resolve_to_the_public_no_auth_endpoint() -> None:
    # Regression guard: ``{pack_id}`` uses the str path-convertor (UUIDs validate post-match), so
    # the static ``…/shared`` literals must mount BEFORE the authenticated ``/{pack_id}`` route —
    # else ``/evidence-packs/shared`` resolves to the authenticated get_pack_endpoint (401, not the
    # guest landing). The app-level resolution order is what matters → assert against the wired app.
    from starlette.routing import Match

    from easysynq_api.main import create_app

    app = create_app()
    public_names = {r.endpoint.__name__ for r in _public_routes()}
    for path in _PUBLIC_PATHS:
        winner = next(
            (
                r
                for r in app.router.routes
                if r.matches({"type": "http", "path": path, "method": "GET"})[0] != Match.NONE
            ),
            None,
        )
        assert winner is not None
        assert winner.endpoint.__name__ in public_names, (
            f"{path} resolves to {winner.endpoint.__name__}, not the public delivery endpoint"
        )
