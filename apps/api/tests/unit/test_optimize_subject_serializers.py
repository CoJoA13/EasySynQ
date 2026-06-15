"""S-optimize-1 (critique #5, power-user triage): the task + DCR serializers now carry the subject /
target human identity so the inbox/rail and the DCR register triage in place. Pure-serializer unit
tests (no DB) — the join + list/detail agreement is proven in tests/integration."""

from __future__ import annotations

import datetime
import uuid

from easysynq_api.api.dcr import _dcr
from easysynq_api.api.workflow import _SUBJECT_TITLE_CAP, _short, _task
from easysynq_api.db.models._dcr_enums import DcrChangeType, DcrReasonClass, DcrState
from easysynq_api.db.models._vault_enums import ChangeSignificance
from easysynq_api.db.models._workflow_enums import TaskState, TaskType
from easysynq_api.db.models.dcr import Dcr
from easysynq_api.db.models.workflow import Task


def _t() -> Task:
    return Task(
        id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        instance_id=uuid.uuid4(),
        stage_key="quality_approval",
        type=TaskType.APPROVE,
        state=TaskState.PENDING,
        assignee_user_id=None,
        candidate_pool=[str(uuid.uuid4())],
        action_expected="approve",
        due_at=None,
    )


def _d() -> Dcr:
    d = Dcr(
        id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        identifier="DCR-2026-0001",
        target_document_id=uuid.uuid4(),
        change_type=DcrChangeType.REVISE,
        change_significance=ChangeSignificance.MAJOR,
        reason_class=DcrReasonClass.capa,
        reason_text="Tighten the supplier re-qualification cadence.",
        source_link_type=None,
        source_link_id=None,
        proposed_effective_from=None,
        resulting_version_id=None,
        state=DcrState.Open,
        decision=None,
        created_by=uuid.uuid4(),
    )
    d.created_at = datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC)
    return d


# --- _task subject enrichment -----------------------------------------------------------------


def test_task_omits_subject_when_not_passed() -> None:
    out = _task(_t())
    assert "subject_type" not in out
    assert "subject_id" not in out
    assert "subject_identifier" not in out
    assert "subject_title" not in out


def test_task_carries_full_subject_when_passed() -> None:
    out = _task(
        _t(),
        subject_type="DOCUMENT",
        subject_id="018f-doc",
        subject_identifier="SOP-PUR-014",
        subject_title="Supplier Re-qualification",
    )
    assert out["subject_type"] == "DOCUMENT"
    assert out["subject_id"] == "018f-doc"
    assert out["subject_identifier"] == "SOP-PUR-014"
    assert out["subject_title"] == "Supplier Re-qualification"


def test_task_subject_present_even_when_identifier_unresolved() -> None:
    # An unresolvable subject (e.g. a not-yet-built subject table) still carries type/id; the label
    # keys are present and null — the FE shows a calm fallback, never a crash.
    out = _task(_t(), subject_type="AUDIT", subject_id="018f-aud")
    assert out["subject_type"] == "AUDIT"
    assert out["subject_identifier"] is None
    assert out["subject_title"] is None


# --- _short (DCR reason_text title cap) --------------------------------------------------------


def test_short_passes_through_short_titles() -> None:
    assert _short("Purchasing") == "Purchasing"
    assert _short("  trimmed  ") == "trimmed"


def test_short_returns_none_for_none() -> None:
    assert _short(None) is None


def test_short_truncates_with_ellipsis() -> None:
    long = "x" * 500
    out = _short(long)
    assert out is not None
    assert len(out) == _SUBJECT_TITLE_CAP
    assert out.endswith("…")


def test_task_truncates_long_subject_title() -> None:
    out = _task(
        _t(),
        subject_type="DCR",
        subject_id="018f-dcr",
        subject_identifier="DCR-2026-0001",
        subject_title="y" * 500,
    )
    assert out["subject_title"] is not None
    assert len(out["subject_title"]) == _SUBJECT_TITLE_CAP
    assert out["subject_title"].endswith("…")


# --- _dcr target enrichment --------------------------------------------------------------------


def test_dcr_omits_target_identity_by_default() -> None:
    # raise/create call sites pass neither → both keys present and null (contract-nullable).
    out = _dcr(_d())
    assert out["target_identifier"] is None
    assert out["target_title"] is None
    assert out["target_document_id"] is not None  # the bare id is still there


def test_dcr_carries_target_identity_when_passed() -> None:
    out = _dcr(_d(), target_identifier="SOP-PUR-014", target_title="Supplier Re-qualification")
    assert out["target_identifier"] == "SOP-PUR-014"
    assert out["target_title"] == "Supplier Re-qualification"
    # The existing fields are untouched.
    assert out["identifier"] == "DCR-2026-0001"
    assert out["change_type"] == "REVISE"
