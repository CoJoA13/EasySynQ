"""Named PROOF (doc 06 §1.3 / §10): a record's *content* is IMMUTABLE — the surface exposes no way
to edit captured record content in place. Corrections capture a NEW record (``correction_of``); the
old one is never mutated.

S-rec-2 reframes the proof from "no PATCH/PUT at all" to "no PATCH/PUT on record **content**": the
disposition state machine (doc 06 §5.3, doc 15 §8.9) is a sanctioned, audited *state advance* (the
only mutable fields are ``disposition_state``/``legal_hold``), so ``PATCH .../disposition`` is
whitelisted (exactly as the evidence-link ``DELETE`` is — an annotation, not sealed content). The
legal-hold + dual-control-destroy writes are POST sub-resources, not in-place edits. Every other
PATCH/PUT and any non-whitelisted DELETE remains forbidden. No DB needed."""

from __future__ import annotations

from fastapi.routing import APIRoute

from easysynq_api.api.records import router as records_router

# The sanctioned state-machine exceptions to record-content immutability (doc 06 §5.3): a
# disposition advance is the ONLY in-place PATCH; the only content-DELETE is the evidence-link.
_DISPOSITION_PATCH_SUFFIX = "/disposition"
_EVIDENCE_LINK_DELETE_SUFFIX = "/evidence-links/{link_id}"


def _routes() -> list[APIRoute]:
    return [r for r in records_router.routes if isinstance(r, APIRoute)]


def test_records_router_has_no_in_place_content_edit() -> None:
    routes = _routes()
    assert routes, "expected the records router to mount routes"
    for route in routes:
        if "PUT" in route.methods:  # PUT is never permitted on any record route
            raise AssertionError(f"records route {route.path} exposes PUT")
        if "PATCH" in route.methods:
            # The ONLY permitted PATCH is the disposition state advance (not a content edit).
            assert route.path.endswith(_DISPOSITION_PATCH_SUFFIX), (
                f"records route {route.path} exposes a content PATCH"
            )
        if "DELETE" in route.methods:
            # The ONLY permitted DELETE is unlinking evidence (an annotation, not sealed content).
            assert route.path.endswith(_EVIDENCE_LINK_DELETE_SUFFIX), (
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


def test_disposition_patch_is_the_only_patch() -> None:
    # Exactly one PATCH route exists, and it is the disposition advance — proves the whitelist is
    # tight (a future stray PATCH to edit record content would fail the content-edit proof above and
    # this count).
    patch_paths = [r.path for r in _routes() if "PATCH" in r.methods]
    assert patch_paths == ["/api/v1/records/{record_id}/disposition"]
