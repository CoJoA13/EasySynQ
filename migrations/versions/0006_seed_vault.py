"""seed: the iso9001:2015 framework + default document types (slice S3)

Idempotent. Seeds the single ``iso9001:2015`` framework row (so
``documented_information.framework_id`` resolves) and a few starter document types for the
DEFAULT org, so documents can be created out of the box. ``code`` drives the ``{TYPE}`` token
of an identifier (``POL-…``, ``SOP-…``, ``WI-…``, ``FRM-…``).

Revision ID: 0006_seed_vault
Revises: 0005_vault
Create Date: 2026-06-01
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

revision: str = "0006_seed_vault"
down_revision: str | None = "0005_vault"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (code, name, document_level, is_singleton)
_DOCUMENT_TYPES: tuple[tuple[str, str, str, bool], ...] = (
    ("POL", "Quality Policy", "L1_POLICY", True),
    ("SOP", "Procedure", "L2_PROCEDURE", False),
    ("WI", "Work Instruction", "L3_WORK_INSTRUCTION", False),
    ("FRM", "Form", "L4_FORM", False),
)


def upgrade() -> None:
    bind = op.get_bind()
    org_id = bind.execute(
        sa.text("SELECT id FROM organization WHERE short_code = 'DEFAULT'")
    ).scalar_one()

    framework_t = sa.table(
        "framework",
        sa.column("org_id", postgresql.UUID(as_uuid=True)),
        sa.column("code", sa.Text),
        sa.column("name", sa.Text),
        sa.column("is_active", sa.Boolean),
        sa.column("is_authorable", sa.Boolean),
    )
    bind.execute(
        pg_insert(framework_t)
        .values(
            org_id=org_id,
            code="iso9001:2015",
            name="ISO 9001:2015",
            is_active=True,
            is_authorable=False,
        )
        .on_conflict_do_nothing(index_elements=["org_id", "code"])
    )

    document_type_t = sa.table(
        "document_type",
        sa.column("org_id", postgresql.UUID(as_uuid=True)),
        sa.column("code", sa.Text),
        sa.column("name", sa.Text),
        sa.column("document_level", postgresql.ENUM(name="document_level", create_type=False)),
        sa.column("is_singleton", sa.Boolean),
    )
    rows: list[dict[str, Any]] = [
        {
            "org_id": org_id,
            "code": code,
            "name": name,
            "document_level": level,
            "is_singleton": singleton,
        }
        for code, name, level, singleton in _DOCUMENT_TYPES
    ]
    bind.execute(
        pg_insert(document_type_t)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["org_id", "code"])
    )


def downgrade() -> None:
    bind = op.get_bind()
    codes = [code for code, _, _, _ in _DOCUMENT_TYPES]
    del_types = sa.text("DELETE FROM document_type WHERE code IN :codes").bindparams(
        sa.bindparam("codes", expanding=True)
    )
    bind.execute(del_types, {"codes": codes})
    bind.execute(sa.text("DELETE FROM framework WHERE code = 'iso9001:2015'"))
