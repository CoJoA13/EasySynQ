"""Boolean ORM attributes use predicate names without changing physical schema contracts."""

from easysynq_api.db.models import (
    Framework,
    NotificationTemplate,
    RetentionPolicy,
    SlaPolicy,
    WorkflowDefinition,
)


def test_predicate_attributes_preserve_physical_column_names() -> None:
    assert WorkflowDefinition.is_effective.property.columns[0].name == "effective"
    assert NotificationTemplate.is_effective.property.columns[0].name == "is_effective"
    assert RetentionPolicy.is_active.property.columns[0].name == "active"
    assert SlaPolicy.is_active.property.columns[0].name == "active"
    assert Framework.is_active.property.columns[0].name == "is_active"
