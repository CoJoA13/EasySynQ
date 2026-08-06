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
from easysynq_api.problems import ProblemException
from easysynq_api.services.records import service as records_service
from easysynq_api.services.vault import (
    StagedObjectRef,
    StagedVersionLocator,
    StagingDomain,
)

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


@pytest.mark.parametrize("invalid_version", ["", "v" * 1025])
def test_evidence_ref_rejects_invalid_staging_version_id(invalid_version: str) -> None:
    with pytest.raises(ValidationError):
        EvidenceRef.model_validate({"sha256": "a" * 64, "staging_version_id": invalid_version})


def test_evidence_ref_carries_nullable_staging_version_for_conditional_service_validation() -> None:
    parsed = EvidenceRef.model_validate({"sha256": "a" * 64, "staging_version_id": None})
    assert parsed.model_dump() == {
        "sha256": "a" * 64,
        "content_type": "application/octet-stream",
        "staging_version_id": None,
    }


def test_evidence_ref_rejects_empty_content_type_before_service_mapping() -> None:
    with pytest.raises(ValidationError):
        EvidenceRef.model_validate({"sha256": "a" * 64, "content_type": ""})


def _evidence_input(
    sha256: str,
    content_type: str,
    source: StagedObjectRef | None,
) -> object:
    evidence_input = getattr(records_service, "EvidenceInput", None)
    assert evidence_input is not None, "EvidenceInput service contract is missing"
    return evidence_input(sha256=sha256, content_type=content_type, source=source)


def _staged_source(sha256: str, version_id: str) -> StagedObjectRef:
    return StagedObjectRef(
        locator=StagedVersionLocator(
            domain=StagingDomain.STAGING,
            object_key=sha256,
            version_id=version_id,
        ),
        expected_sha256=sha256,
        content_type="application/pdf",
    )


def test_evidence_input_rejects_a_source_with_different_evidence_claims() -> None:
    source = _staged_source("a" * 64, "v-one")

    with pytest.raises(ValueError, match="sha256"):
        _evidence_input("b" * 64, "application/pdf", source)
    with pytest.raises(ValueError, match="content type"):
        _evidence_input("a" * 64, "text/plain", source)


def test_normalize_evidence_collapses_duplicate_same_sha_and_same_version() -> None:
    source = _staged_source("a" * 64, "v-one")
    first = _evidence_input("a" * 64, "application/pdf", source)
    duplicate = _evidence_input("a" * 64, "application/pdf", source)
    normalize = getattr(records_service, "_normalize_evidence", None)
    assert normalize is not None, "_normalize_evidence is missing"

    assert normalize([first, duplicate]) == [first]


@pytest.mark.parametrize("second_version", ["v-two", None])
def test_normalize_evidence_rejects_same_sha_with_ambiguous_staging_versions(
    second_version: str | None,
) -> None:
    first = _evidence_input("a" * 64, "application/pdf", _staged_source("a" * 64, "v-one"))
    second = _evidence_input(
        "a" * 64,
        "application/pdf",
        _staged_source("a" * 64, second_version) if second_version is not None else None,
    )
    normalize = getattr(records_service, "_normalize_evidence", None)
    assert normalize is not None, "_normalize_evidence is missing"

    with pytest.raises(ProblemException) as caught:
        normalize([first, second])

    assert (caught.value.status, caught.value.code) == (422, "validation_error")
    assert caught.value.errors == [
        {
            "field": "evidence",
            "code": "ambiguous_staging_version",
            "message": "the same evidence sha256 names different staging versions",
        }
    ]


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
