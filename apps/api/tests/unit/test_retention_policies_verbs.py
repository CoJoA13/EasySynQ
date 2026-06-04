"""S-rec-4 route-inventory proof: the /retention-policies surface is GET/POST/PATCH + the
soft-archive POST actions only — NO PUT, NO DELETE (a hard delete is blocked by 3 RESTRICT FKs;
retirement is the soft ``/archive``). Mounted on a SEPARATE router from records, so the
records-immutability proof (``test_records_no_edit_verbs``) stays tight — verified here too."""

from __future__ import annotations

from fastapi.routing import APIRoute

from easysynq_api.api.records import router as records_router
from easysynq_api.api.retention_policies import router as retention_router


def _routes() -> list[APIRoute]:
    return [r for r in retention_router.routes if isinstance(r, APIRoute)]


def test_retention_policies_has_no_put_or_delete() -> None:
    routes = _routes()
    assert routes, "expected the retention-policies router to mount routes"
    for route in routes:
        assert "PUT" not in route.methods, f"retention route {route.path} exposes PUT"
        assert "DELETE" not in route.methods, f"retention route {route.path} exposes DELETE"


def test_retention_policies_surface_is_complete() -> None:
    by_path: dict[str, set[str]] = {}
    for route in _routes():
        by_path.setdefault(route.path, set()).update(route.methods)
    assert by_path.get("/api/v1/retention-policies") == {"GET", "POST"}
    assert by_path.get("/api/v1/retention-policies/{policy_id}") == {"GET", "PATCH"}
    assert by_path.get("/api/v1/retention-policies/{policy_id}/archive") == {"POST"}
    assert by_path.get("/api/v1/retention-policies/{policy_id}/unarchive") == {"POST"}


def test_retention_router_does_not_contaminate_records_router() -> None:
    # The records immutability proof reads records_router.routes; ensure no /retention path leaks.
    record_paths = [r.path for r in records_router.routes if isinstance(r, APIRoute)]
    assert not any("retention-policies" in p for p in record_paths)
