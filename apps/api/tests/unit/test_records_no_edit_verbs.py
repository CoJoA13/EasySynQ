"""S-rec-1 named PROOF (doc 06 §1.3 / §10): records are IMMUTABLE — the surface exposes NO way to
edit a record in place. A route-inventory assertion over the records router: no PATCH/PUT anywhere,
and the only DELETE is the evidence-link sub-resource (an audited annotation, never a content edit).
Corrections capture a NEW record (``correction_of``), never mutate the old one. No DB needed."""

from __future__ import annotations

from fastapi.routing import APIRoute

from easysynq_api.api.records import router as records_router


def _routes() -> list[APIRoute]:
    return [r for r in records_router.routes if isinstance(r, APIRoute)]


def test_records_router_has_no_in_place_edit() -> None:
    routes = _routes()
    assert routes, "expected the records router to mount routes"
    for route in routes:
        in_place = route.methods & {"PATCH", "PUT"}
        assert not in_place, f"records route {route.path} exposes a mutate-in-place verb {in_place}"
        if "DELETE" in route.methods:
            # The ONLY permitted DELETE is unlinking evidence (an annotation, not sealed content).
            assert route.path.endswith("/evidence-links/{link_id}"), (
                f"records route {route.path} exposes a content DELETE"
            )


def test_records_router_read_surface_is_get_only() -> None:
    # Non-vacuous: the single-record read + its evidence reads are present and GET-only.
    by_path: dict[str, set[str]] = {}
    for route in _routes():
        by_path.setdefault(route.path, set()).update(route.methods)
    assert by_path.get("/api/v1/records/{record_id}") == {"GET"}
    assert by_path.get("/api/v1/records/{record_id}/evidence/{sha256}/download") == {"GET"}
    assert by_path.get("/api/v1/records/{record_id}/evidence-links") == {"GET", "POST"}
