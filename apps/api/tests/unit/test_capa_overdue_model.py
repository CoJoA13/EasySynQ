from easysynq_api.db.models._audit_enums import EVENT_TYPE_VALUES, EventType
from easysynq_api.db.models.capa import Capa


def test_event_type_has_capa_overdue_members():
    assert EventType.CAPA_OVERDUE.value == "CAPA_OVERDUE"
    assert EventType.CAPA_TARGET_DATE_SET.value == "CAPA_TARGET_DATE_SET"
    assert "CAPA_OVERDUE" in EVENT_TYPE_VALUES
    assert "CAPA_TARGET_DATE_SET" in EVENT_TYPE_VALUES


def test_capa_model_has_overdue_columns():
    cols = Capa.__table__.columns
    assert "target_completion_date" in cols
    assert "overdue_notified_at" in cols
    assert cols["target_completion_date"].nullable is True
    assert cols["overdue_notified_at"].nullable is True
