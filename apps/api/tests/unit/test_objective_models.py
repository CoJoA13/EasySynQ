import pytest

from easysynq_api.db.models import KpiMeasurement, ObjectivePlan, QualityObjective

pytestmark = pytest.mark.unit


def test_quality_objective_columns() -> None:
    cols = set(QualityObjective.__table__.columns.keys())
    assert {
        "id",
        "org_id",
        "target_value",
        "unit",
        "baseline_value",
        "current_value",
        "direction",
        "at_risk_threshold",
        "due_date",
        "process_id",
        "policy_id",
        "created_at",
        "updated_at",
    } <= cols
    # owner is the base documented_information column, NOT duplicated here
    assert "owner_user_id" not in cols


def test_kpi_measurement_and_plan_columns() -> None:
    assert {
        "id",
        "org_id",
        "record_id",
        "objective_id",
        "process_id",
        "period",
        "value",
        "target_at_capture",
        "unit",
        "source",
    } <= set(KpiMeasurement.__table__.columns.keys())
    assert {
        "id",
        "org_id",
        "objective_id",
        "action",
        "resource",
        "responsible_user_id",
        "due_date",
    } <= set(ObjectivePlan.__table__.columns.keys())
