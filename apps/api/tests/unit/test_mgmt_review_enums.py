"""S-mr-3: the additive enum members + the un-reserved FK exist on the ORM."""

from easysynq_api.db.models._audit_enums import EventType
from easysynq_api.db.models._dcr_enums import DCR_REASON_CLASS_VALUES, DcrReasonClass
from easysynq_api.db.models.review_output import ReviewOutput


def test_dcr_reason_class_has_mgmt_review() -> None:
    assert DcrReasonClass.mgmt_review.value == "mgmt_review"
    assert "mgmt_review" in DCR_REASON_CLASS_VALUES


def test_event_types_for_mr_spawns_exist() -> None:
    assert EventType.MGMT_REVIEW_CAPA_SPAWNED.value == "MGMT_REVIEW_CAPA_SPAWNED"
    assert EventType.MGMT_REVIEW_DCR_SPAWNED.value == "MGMT_REVIEW_DCR_SPAWNED"


def test_spawned_capa_id_has_capa_fk() -> None:
    col = ReviewOutput.__table__.c.spawned_capa_id
    fks = list(col.foreign_keys)
    assert len(fks) == 1
    fk = fks[0]
    assert fk.column.table.name == "capa"
    assert fk.name == "fk_review_output_spawned_capa_id_capa"
    assert fk.ondelete == "RESTRICT"
