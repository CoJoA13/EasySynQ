# apps/api/tests/unit/test_document_control_report.py
from __future__ import annotations

import datetime

from easysynq_api.services.reports.document_control import (
    build_provenance,
    register_content_hash,
)


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
        filters={"filter[current_state][eq]": "Effective"},
        row_count=2,
        content_hash="sha256:abc",
    )
    assert prov["report_name"] == "Controlled Document Register"
    assert prov["generated_at"] == now.isoformat()
    assert prov["as_of"] == now.isoformat()
    assert prov["scope"] == "org:DEFAULT"
    assert prov["app_version"] == "0.1.0"
    assert prov["row_count"] == 2
    assert prov["content_hash"] == "sha256:abc"
    assert prov["filters"] == {"filter[current_state][eq]": "Effective"}
    assert prov["generated_by"] == "Mara Quality"
