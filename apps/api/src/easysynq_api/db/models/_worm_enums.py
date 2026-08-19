"""Closed PostgreSQL enum bindings for WORM retention and maintenance state."""

from __future__ import annotations

import enum

from sqlalchemy import Enum as SAEnum


class RetentionAuthorityKind(enum.Enum):
    POLICY = "POLICY"
    INSTALLATION_MINIMUM = "INSTALLATION_MINIMUM"


class RetentionRevisionState(enum.Enum):
    PROPOSED = "PROPOSED"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"


class RetentionOperationState(enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    FAILED = "FAILED"
    VERIFIED = "VERIFIED"
    CANCELLED_PRE_START = "CANCELLED_PRE_START"


class RetentionTargetState(enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    FAILED = "FAILED"
    VERIFIED = "VERIFIED"


class HoldReleaseState(enum.Enum):
    PENDING_AUTHORIZATION = "PENDING_AUTHORIZATION"
    AUTHORIZED = "AUTHORIZED"
    RUNNING = "RUNNING"
    FAILED = "FAILED"
    VERIFIED = "VERIFIED"
    CANCELLED_PRE_START = "CANCELLED_PRE_START"


class MaintenanceState(enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    FAILED = "FAILED"
    VERIFIED = "VERIFIED"


class AuditMaintenanceJobKind(enum.Enum):
    CHAIN_LINK = "CHAIN_LINK"
    VERIFY_CHAIN = "VERIFY_CHAIN"
    CHECKPOINT_ANCHOR = "CHECKPOINT_ANCHOR"
    ROLL_PARTITIONS = "ROLL_PARTITIONS"


class BackupMaintenanceKind(enum.Enum):
    BACKUP = "BACKUP"
    RESTORE_TEST = "RESTORE_TEST"


class MaintenanceSource(enum.Enum):
    SCHEDULED = "SCHEDULED"
    MANUAL = "MANUAL"


def _values(enum_type: type[enum.Enum]) -> list[str]:
    return [member.value for member in enum_type]


retention_authority_kind_enum = SAEnum(
    RetentionAuthorityKind,
    name="retention_authority_kind",
    values_callable=_values,
    create_type=False,
)
retention_revision_state_enum = SAEnum(
    RetentionRevisionState,
    name="retention_revision_state",
    values_callable=_values,
    create_type=False,
)
retention_operation_state_enum = SAEnum(
    RetentionOperationState,
    name="retention_operation_state",
    values_callable=_values,
    create_type=False,
)
retention_target_state_enum = SAEnum(
    RetentionTargetState,
    name="retention_target_state",
    values_callable=_values,
    create_type=False,
)
hold_release_state_enum = SAEnum(
    HoldReleaseState,
    name="hold_release_state",
    values_callable=_values,
    create_type=False,
)
maintenance_state_enum = SAEnum(
    MaintenanceState,
    name="maintenance_state",
    values_callable=_values,
    create_type=False,
)
audit_maintenance_job_kind_enum = SAEnum(
    AuditMaintenanceJobKind,
    name="audit_maintenance_job_kind",
    values_callable=_values,
    create_type=False,
)
backup_maintenance_kind_enum = SAEnum(
    BackupMaintenanceKind,
    name="backup_maintenance_kind",
    values_callable=_values,
    create_type=False,
)
maintenance_source_enum = SAEnum(
    MaintenanceSource,
    name="maintenance_source",
    values_callable=_values,
    create_type=False,
)

WORM_ENUM_VALUES = {
    "retention_authority_kind": tuple(_values(RetentionAuthorityKind)),
    "retention_revision_state": tuple(_values(RetentionRevisionState)),
    "retention_operation_state": tuple(_values(RetentionOperationState)),
    "retention_target_state": tuple(_values(RetentionTargetState)),
    "hold_release_state": tuple(_values(HoldReleaseState)),
    "maintenance_state": tuple(_values(MaintenanceState)),
    "audit_maintenance_job_kind": tuple(_values(AuditMaintenanceJobKind)),
    "backup_maintenance_kind": tuple(_values(BackupMaintenanceKind)),
    "maintenance_source": tuple(_values(MaintenanceSource)),
}
