# apps/api/tests/unit/test_document_control_report.py
from __future__ import annotations

import datetime

from easysynq_api.db.models._vault_enums import DocumentCurrentState
from easysynq_api.domain.authz.types import Effect, ResolvedGrant, ScopeLevel
from easysynq_api.services.reports.document_control import (
    build_provenance,
    register_content_hash,
    report_read_resource_satisfiable,
)

_EFFECTIVE = DocumentCurrentState.Effective.value


def _mk_grant(**predicates: object) -> ResolvedGrant:
    return ResolvedGrant(
        effect=Effect.ALLOW,
        level=ScopeLevel.SYSTEM,
        selector={},
        predicates=predicates,
        source="test",
    )


def test_report_read_resource_satisfiable_matrix():
    """#347: the surface admits a report.read ALLOW only if its resource predicates can match ≥1
    real document. lifecycle_state + requirement_source are the only two resource predicates, so
    validating both closes the admission class (no more misleading 200-empty registers)."""
    # no resource predicates → satisfiable
    assert report_read_resource_satisfiable(_mk_grant()) is True
    # lifecycle_state intersecting a real state → satisfiable (list, scalar, or unknown mixed in)
    assert report_read_resource_satisfiable(_mk_grant(lifecycle_state=[_EFFECTIVE])) is True
    assert report_read_resource_satisfiable(_mk_grant(lifecycle_state=_EFFECTIVE)) is True
    assert (
        report_read_resource_satisfiable(_mk_grant(lifecycle_state=[_EFFECTIVE, "Bogus"])) is True
    )
    # lifecycle_state that matches nothing → NOT satisfiable (empty list / only-unknown states)
    assert report_read_resource_satisfiable(_mk_grant(lifecycle_state=[])) is False
    assert report_read_resource_satisfiable(_mk_grant(lifecycle_state=["Bogus"])) is False
    # requirement_source is v1-unimplemented → any PRESENT value (incl. falsy "") → NOT satisfiable
    assert report_read_resource_satisfiable(_mk_grant(requirement_source="iso_mandatory")) is False
    assert report_read_resource_satisfiable(_mk_grant(requirement_source="")) is False


def _rows() -> list[dict]:
    return [
        {"identifier": "SOP-QA-002", "title": "B", "current_state": "Effective"},
        {"identifier": "SOP-QA-001", "title": "A", "current_state": "Effective"},
    ]


def test_content_hash_is_deterministic_and_order_independent():
    a = register_content_hash(_rows())
    b = register_content_hash(list(reversed(_rows())))
    assert a == b
    assert a.startswith("sha256:")


def test_content_hash_is_filter_sensitive():
    base = register_content_hash(_rows())
    fewer = register_content_hash(_rows()[:1])
    assert base != fewer


def test_content_hash_reacts_to_a_field_change():
    rows = _rows()
    changed = [dict(rows[0], title="CHANGED"), rows[1]]
    assert register_content_hash(rows) != register_content_hash(changed)


def test_build_provenance_shape_excludes_hash_from_its_own_input():
    now = datetime.datetime(2026, 7, 19, 12, 0, tzinfo=datetime.UTC)
    prov = build_provenance(
        generated_by="Mara Quality",
        generated_at=now,
        scope="org:DEFAULT",
        app_version="0.1.0",
        filters={"filter[current_state][eq]": ["Effective"]},
        row_count=2,
        content_hash="sha256:abc",
        process_scope=None,
        excluded_processes=None,
    )
    assert prov["report_name"] == "Controlled Document Register"
    assert prov["generated_at"] == now.isoformat()
    assert prov["as_of"] == now.isoformat()
    assert prov["scope"] == "org:DEFAULT"
    assert prov["app_version"] == "0.1.0"
    assert prov["row_count"] == 2
    assert prov["content_hash"] == "sha256:abc"
    assert prov["filters"] == {"filter[current_state][eq]": ["Effective"]}
    assert prov["generated_by"] == "Mara Quality"
    assert prov["process_scope"] is None
    assert prov["excluded_processes"] is None


def test_build_provenance_shape_preserves_repeated_filter_values():
    """FIX 2: a repeated ``filter[...]`` query param (e.g. two ``filter[clause_refs][has]``
    values, ANDed by the parser) must be represented as a list, not collapsed to its last value —
    else ``provenance.filters`` misrepresents the applied query and can't reproduce the row set."""
    now = datetime.datetime(2026, 7, 19, 12, 0, tzinfo=datetime.UTC)
    prov = build_provenance(
        generated_by="Mara Quality",
        generated_at=now,
        scope="org:DEFAULT",
        app_version="0.1.0",
        filters={"filter[clause_refs][has]": ["8.4", "8.5"]},
        row_count=1,
        content_hash="sha256:abc",
        process_scope=None,
        excluded_processes=None,
    )
    assert prov["filters"] == {"filter[clause_refs][has]": ["8.4", "8.5"]}


def test_build_provenance_shape_records_process_scope_when_process_limited():
    """FIX 2 (Codex round 6, P2): when the caller's report.read is PROCESS-scoped (not org-wide),
    ``process_scope`` carries the name-resolved process(es) the register is confined to — never
    silently dropped/omitted — so an auditor reading the block can't mistake a process-limited
    register for the org-wide one."""
    now = datetime.datetime(2026, 7, 19, 12, 0, tzinfo=datetime.UTC)
    scope = [{"id": "11111111-1111-1111-1111-111111111111", "name": "Purchasing"}]
    prov = build_provenance(
        generated_by="Diego Process",
        generated_at=now,
        scope="org:DEFAULT",
        app_version="0.1.0",
        filters={},
        row_count=1,
        content_hash="sha256:abc",
        process_scope=scope,
        excluded_processes=None,
    )
    assert prov["process_scope"] == scope


def test_build_provenance_shape_records_excluded_processes():
    """#335 fix 1: an unconditional PROCESS report.read DENY on an org-wide (SYSTEM ALLOW) reader
    keeps ``process_scope`` null but records the denied process(es) in ``excluded_processes`` — so
    the register reads honestly as org-wide MINUS those, never mistaken for the full set."""
    now = datetime.datetime(2026, 7, 19, 12, 0, tzinfo=datetime.UTC)
    excluded = [{"id": "22222222-2222-2222-2222-222222222222", "name": "Logistics"}]
    prov = build_provenance(
        generated_by="Mara Quality",
        generated_at=now,
        scope="org:DEFAULT",
        app_version="0.1.0",
        filters={},
        row_count=3,
        content_hash="sha256:abc",
        process_scope=None,
        excluded_processes=excluded,
    )
    assert prov["process_scope"] is None  # org-wide by the SYSTEM ALLOW
    assert prov["excluded_processes"] == excluded
