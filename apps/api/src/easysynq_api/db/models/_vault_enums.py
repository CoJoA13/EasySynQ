"""Native-PG enum bindings for the vault cluster (slice S3).

Canonical lifecycle tokens (doc 14 §5, register R1/C2): a *version* is never literally
``UnderRevision`` (6-state ``version_state``); the *document* headline adds the derived
``UnderRevision`` (7-state ``document_current_state``). Created by the Alembic migration;
referenced here with ``create_type=False``. The FSM that transitions these lands in S4 —
S3 only ever creates ``Draft``.
"""

from __future__ import annotations

import enum

from sqlalchemy import Enum as SAEnum


class VersionState(enum.Enum):
    Draft = "Draft"
    InReview = "InReview"
    Approved = "Approved"
    Effective = "Effective"
    Superseded = "Superseded"
    Obsolete = "Obsolete"


class DocumentCurrentState(enum.Enum):
    Draft = "Draft"
    InReview = "InReview"
    Approved = "Approved"
    Effective = "Effective"
    UnderRevision = "UnderRevision"
    Superseded = "Superseded"
    Obsolete = "Obsolete"


class DocumentKind(enum.Enum):
    DOCUMENT = "DOCUMENT"
    RECORD = "RECORD"


class DocumentLevel(enum.Enum):
    L1_POLICY = "L1_POLICY"
    L2_PROCEDURE = "L2_PROCEDURE"
    L3_WORK_INSTRUCTION = "L3_WORK_INSTRUCTION"
    L4_FORM = "L4_FORM"


class ChangeSignificance(enum.Enum):
    MAJOR = "MAJOR"
    MINOR = "MINOR"


class Classification(enum.Enum):
    Public = "Public"
    Internal = "Internal"
    Confidential = "Confidential"
    Restricted = "Restricted"


def _vals(e: type[enum.Enum]) -> list[str]:
    return [m.value for m in e]


version_state_enum = SAEnum(
    VersionState, name="version_state", values_callable=_vals, create_type=False
)
document_current_state_enum = SAEnum(
    DocumentCurrentState, name="document_current_state", values_callable=_vals, create_type=False
)
document_kind_enum = SAEnum(
    DocumentKind, name="document_kind", values_callable=_vals, create_type=False
)
document_level_enum = SAEnum(
    DocumentLevel, name="document_level", values_callable=_vals, create_type=False
)
change_significance_enum = SAEnum(
    ChangeSignificance, name="change_significance", values_callable=_vals, create_type=False
)
classification_enum = SAEnum(
    Classification, name="classification", values_callable=_vals, create_type=False
)
