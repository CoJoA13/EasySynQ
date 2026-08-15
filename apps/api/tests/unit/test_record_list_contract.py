from pathlib import Path

import yaml

OPENAPI = Path(__file__).resolve().parents[4] / "packages/contracts/openapi.yaml"


def test_records_list_publishes_cursor_page_without_hidden_counts() -> None:
    spec = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    operation = spec["paths"]["/records"]["get"]
    names = {parameter["name"] for parameter in operation["parameters"]}
    assert names == {
        "limit",
        "cursor",
        "q",
        "record_type",
        "source_document_id",
        "captured_by",
        "disposition_state",
        "legal_hold",
    }
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/RecordPage"
    }
    cursor = next(
        parameter for parameter in operation["parameters"] if parameter["name"] == "cursor"
    )
    assert cursor["schema"] == {"type": "string", "minLength": 1}
    page = spec["components"]["schemas"]["RecordPage"]
    assert page["required"] == ["data", "page"]
    assert set(page["properties"]["page"]["properties"]) == {"limit", "returned", "next_cursor"}


def test_record_contract_publishes_safe_related_navigation_fields() -> None:
    spec = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    summary = spec["components"]["schemas"]["RecordSummary"]["properties"]
    assert {
        "captured_by_display_name",
        "source_document_identifier",
        "source_document_title",
        "source_document_readable",
        "source_version_label",
        "retention_policy_name",
    } <= summary.keys()
    record = spec["components"]["schemas"]["Record"]["properties"]
    assert record["correction_of_readable"] == {"type": "boolean"}
    assert record["superseded_by_correction_readable"] == {"type": "boolean"}
    link = spec["components"]["schemas"]["EvidenceLink"]
    assert {"target_label", "target_readable"} <= set(link["required"])
