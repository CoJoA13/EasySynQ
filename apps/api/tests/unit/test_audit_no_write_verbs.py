"""S10 named PROOF (doc 18 §7): the audit read API exposes NO write verbs — a route-inventory
assertion. Append-only + hash-chained is a *system invariant* (the app DB role lacks UPDATE/DELETE
on ``audit_event``); this guards that no write route is ever mounted on the audit surface (co-proves
AC#6, the append-only audit trail). A unit test over the router's route table — no DB needed."""

from __future__ import annotations

from fastapi.routing import APIRoute

from easysynq_api.api.audit import router as audit_router

_WRITE_VERBS = {"POST", "PUT", "PATCH", "DELETE"}


def test_audit_router_exposes_no_write_verbs() -> None:
    routes = [r for r in audit_router.routes if isinstance(r, APIRoute)]
    assert routes, "expected the audit router to mount read routes"
    for route in routes:
        offending = route.methods & _WRITE_VERBS
        assert not offending, f"audit route {route.path} exposes write verb(s) {offending}"


def test_audit_router_has_the_expected_read_surface() -> None:
    # Non-vacuous: the read endpoints (doc 15 §8.13) are present and are GET-only.
    paths = {r.path for r in audit_router.routes if isinstance(r, APIRoute)}
    assert "/api/v1/audit-events" in paths
    assert "/api/v1/audit-events/verify-chain" in paths
    for route in audit_router.routes:
        if isinstance(route, APIRoute) and route.path.startswith("/api/v1/audit"):
            assert route.methods <= {"GET", "HEAD"}, route.path
