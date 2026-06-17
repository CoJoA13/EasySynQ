"""Native-PG enum bindings for the workflow/task cluster (slice S5).

The minimal approval machinery (C7): a declarative ``workflow_definition`` → ``workflow_stage``
spec, a running ``workflow_instance`` that pins ``definition_version``, and the ``task`` /
``task_outcome`` atoms behind ``POST /tasks/{id}/decision`` + My Tasks (doc 14 §7, doc 10).
The full enum value sets are created now (forward-compatible with DCR/CAPA/audit subjects);
MVP only ever uses the DOCUMENT subject + the APPROVE/REVIEW task path. Created by the Alembic
migration; referenced here with ``create_type=False``.

``task_outcome_kind`` is named with the ``_kind`` suffix on purpose: a PG type and a table share
one namespace, so the enum cannot be called ``task_outcome`` (the ``task_outcome`` table's implicit
row type already owns that name). Outcome values are lowercase (register R2 reconciliation), while
``task_type`` values are uppercase — they are distinct vocabularies.
"""

from __future__ import annotations

import enum

from sqlalchemy import Enum as SAEnum


class WorkflowSubjectType(enum.Enum):
    DOCUMENT = "DOCUMENT"
    DCR = "DCR"
    CAPA = "CAPA"
    AUDIT = "AUDIT"
    MGMT_REVIEW = "MGMT_REVIEW"
    PERIODIC_REVIEW = "PERIODIC_REVIEW"
    DOC_ACK = "DOC_ACK"  # S-ack-1: per-user read-&-understood obligations (doc 10 §8.1, R43)
    # S-improvement-4 (clause 10.3, R46): an Improvement Initiative's engine-routed Top-Management
    # authorization — the signed ``verify`` leadership sign-off that closes a Completed initiative
    # (the CAPA/DCR engine precedent on an own-table subject). Added via ``ALTER TYPE
    # workflow_subject_type ADD VALUE`` in 0053; a from-scratch upgrade rebuilds from these members.
    IMPROVEMENT_INITIATIVE = "IMPROVEMENT_INITIATIVE"
    # S-leadership-1 (clause 5.2/6.2/9.3, doc 10 §2.5, R45): a leadership artifact's (POL/OBJ/MR)
    # engine-routed Top-Management *release authorization* — the signed ``verify`` sign-off that
    # is a precondition for releasing the document (the S-improvement-4 caller pattern on a document
    # subject; ``subject_id`` is the ``documented_information`` id, the signature binds to the
    # ``document_version``). A DISTINCT subject type (not overloading DOCUMENT) keeps the
    # ``/tasks/{id}/decision`` dispatch uniformly subject-type-keyed and the welded DOCUMENT
    # fallthrough byte-identical. Added via ``ALTER TYPE … ADD VALUE`` in 0054.
    LEADERSHIP_AUTHORIZATION = "LEADERSHIP_AUTHORIZATION"


class WorkflowStageMode(enum.Enum):
    SEQUENTIAL = "SEQUENTIAL"
    PARALLEL = "PARALLEL"
    ROUTER = "ROUTER"


class TaskType(enum.Enum):
    APPROVE = "APPROVE"
    REVIEW = "REVIEW"
    PERIODIC_REVIEW = "PERIODIC_REVIEW"
    AUDIT_TASK = "AUDIT_TASK"
    FINDING_ACK = "FINDING_ACK"
    CAPA_STAGE = "CAPA_STAGE"
    CAPA_ACTION = "CAPA_ACTION"
    VERIFY = "VERIFY"
    MR_INPUT = "MR_INPUT"
    MR_ACTION = "MR_ACTION"
    DCR_TRIAGE = "DCR_TRIAGE"
    DOC_ACK = "DOC_ACK"  # S-ack-1: doc-10 §8.1 doc-ack task (FINDING_ACK stays audits')


class TaskState(enum.Enum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    DONE = "DONE"
    SKIPPED = "SKIPPED"
    ESCALATED = "ESCALATED"
    EXPIRED = "EXPIRED"


class TaskOutcomeKind(enum.Enum):
    approve = "approve"
    reject = "reject"
    acknowledge = "acknowledge"
    complete = "complete"
    verify = "verify"
    changes_requested = "changes_requested"


def _vals(e: type[enum.Enum]) -> list[str]:
    return [m.value for m in e]


workflow_subject_type_enum = SAEnum(
    WorkflowSubjectType, name="workflow_subject_type", values_callable=_vals, create_type=False
)
workflow_stage_mode_enum = SAEnum(
    WorkflowStageMode, name="workflow_stage_mode", values_callable=_vals, create_type=False
)
task_type_enum = SAEnum(TaskType, name="task_type", values_callable=_vals, create_type=False)
task_state_enum = SAEnum(TaskState, name="task_state", values_callable=_vals, create_type=False)
task_outcome_kind_enum = SAEnum(
    TaskOutcomeKind, name="task_outcome_kind", values_callable=_vals, create_type=False
)
