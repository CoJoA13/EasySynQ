"""The ``form_template`` extension table — a shared-PK subtype of ``documented_information``
(doc 14 §5.5, slice S-rec-3).

A Form/Template is a *maintained Document* (``kind=DOCUMENT``, ``document_type`` code ``FRM``) that,
when filled, instantiates a structured Record (Mode-B capture, doc 06 §4.2). ``form_template.id`` IS
the base ``documented_information`` row's id (one-to-one), exactly like the ``record`` subtype: the
base carries the universal control fields, ``form_template`` adds the ``field_schema``.

``field_schema`` here is the **editable working copy** (the bespoke field-list DSL,
``domain/records/form_schema.py``). It is authored while the document is Draft/UnderRevision and
**frozen into each ``document_version.metadata_snapshot`` at check-in** — Mode-B capture validates +
pins the schema from the record's ``source_version_id`` snapshot, never from this mutable row, so
already-captured records keep showing the edition that was in force (doc 06 §4.2 "records keep
showing v2.0"). Per the every-table tenancy invariant (doc 14 §15.3) it carries its own ``org_id``.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class FormTemplate(Base):
    __tablename__ = "form_template"

    # Shared primary key: form_template.id == documented_information.id (subtype link).
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documented_information.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    # The editable WORKING schema (the bespoke field-list DSL). Nullable until first authored;
    # the version's metadata_snapshot is the pinned source of truth for capture.
    field_schema: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=func.now()
    )
