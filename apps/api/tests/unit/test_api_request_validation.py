"""Request-model guards that keep invalid generated inputs away from operational services."""

import pathlib

import pytest
import yaml
from pydantic import BaseModel, ValidationError

from easysynq_api.api.capa import (
    ActionPlanPropose,
    ComplaintCreate,
    ContainmentCreate,
    ImplementCreate,
    RootCauseCreate,
    VerifyCreate,
)
from easysynq_api.api.documents import CheckIn, InitUpload
from easysynq_api.api.records import EvidenceRef, RecordInitUpload

_OPENAPI = pathlib.Path(__file__).resolve().parents[4] / "packages" / "contracts" / "openapi.yaml"
_SHA_BODY_OPERATIONS = (
    "/documents/{document_id}/versions:init-upload",
    "/documents/{document_id}/checkin",
    "/records:init-upload",
    "/records",
    "/records/{record_id}/correction",
)


@pytest.mark.parametrize("model", [InitUpload, CheckIn, RecordInitUpload, EvidenceRef])
@pytest.mark.parametrize(
    ("invalid_sha", "error_type"),
    [
        ("", "string_too_short"),
        ("a" * 63, "string_too_short"),
        ("a" * 65, "string_too_long"),
        ("A" * 64, "string_pattern_mismatch"),
        ("g" * 64, "string_pattern_mismatch"),
    ],
)
def test_content_address_models_reject_noncanonical_sha256(
    model: type[BaseModel],
    invalid_sha: str,
    error_type: str,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        model.model_validate({"sha256": invalid_sha})
    assert exc_info.value.errors()[0]["type"] == error_type


@pytest.mark.parametrize("model", [InitUpload, CheckIn, RecordInitUpload, EvidenceRef])
def test_content_address_models_accept_lowercase_sha256(model: type[BaseModel]) -> None:
    assert model.model_validate({"sha256": "a" * 64}).sha256 == "a" * 64


@pytest.mark.parametrize("invalid_version", ["", "v" * 1025])
def test_checkin_rejects_invalid_staging_version_id(invalid_version: str) -> None:
    with pytest.raises(ValidationError):
        CheckIn.model_validate({"sha256": "a" * 64, "staging_version_id": invalid_version})


def test_checkin_allows_null_staging_version_for_conditional_service_validation() -> None:
    assert (
        CheckIn.model_validate({"sha256": "a" * 64, "staging_version_id": None}).staging_version_id
        is None
    )


@pytest.mark.parametrize("path", _SHA_BODY_OPERATIONS)
def test_sha_body_operations_document_validation_response(path: str) -> None:
    contract = yaml.safe_load(_OPENAPI.read_text(encoding="utf-8"))
    assert "422" in contract["paths"][path]["post"]["responses"]


@pytest.mark.parametrize(
    ("model", "extra"),
    [
        (ContainmentCreate, {}),
        (RootCauseCreate, {}),
        (ActionPlanPropose, {}),
        (ImplementCreate, {}),
        (VerifyCreate, {"decision": "effective"}),
    ],
)
def test_capa_stage_models_reject_empty_content_blocks(
    model: type[BaseModel],
    extra: dict[str, str],
) -> None:
    with pytest.raises(ValidationError, match="Dictionary should have at least 1 item"):
        model.model_validate({"content_block": {}, **extra})

    parsed = model.model_validate({"content_block": {"note": "bounded"}, **extra})
    assert parsed.content_block == {"note": "bounded"}


def test_complaint_model_rejects_empty_description() -> None:
    with pytest.raises(ValidationError, match="String should have at least 1 character"):
        ComplaintCreate.model_validate({"description": ""})
