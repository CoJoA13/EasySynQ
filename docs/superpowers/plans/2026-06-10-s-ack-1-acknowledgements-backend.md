# S-ack-1 — Acknowledgements Backend Engine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the acknowledgements backend (spec: `docs/superpowers/specs/2026-06-10-s-ack-acknowledgements-design.md`): migration `0048` (distribution_entry + acknowledgement + DOC_ACK enums + R42 `document.distribute` + the seeded `doc_acknowledgement` workflow definition), the ack sweep (cancel-before-mint, the universal mint), the DOC_ACK decide leg, and the distribution/coverage endpoints.

**Architecture:** Obligations are workflow-engine tasks — one instance+task per (user × Effective version), minted by an idempotent sweep (the S-drift-1 `sweep_reviews` template) and decided through a fourth `POST /tasks/{id}/decision` dispatch branch (the `decide_periodic_review` template) that writes the immutable `acknowledgement` row + a `DOCUMENT_ACKNOWLEDGED` audit event in ONE transaction — **never a signature_event** (R2; `document.acknowledge` is sig_hook=false). Coverage truth = distribution × acknowledgements under the R43 MAJOR-only carry-forward rule (`acked version_seq ≥ last_major_seq`); tasks are only the to-do surface.

**Tech Stack:** FastAPI / SQLAlchemy 2 async / Alembic / Celery+Beat / PostgreSQL 16. Branch `feat/s-ack-1` (spec already committed).

**Plan-time reconciliations against the spec (all verified against live code):**
1. The spec's §4 "fresh-session-per-unit (S-ing-5)" note is replaced by the **one-session-one-commit shape of `sweep_reviews`** (services/vault/review.py:115-246) — the proven sibling; the advisory lock is session-level so one session is also what the lock needs.
2. The spec's "every chain starts MAJOR" premise is false in the built system (`_checkin` defaults `change_significance="MINOR"`). `last_major_seq` therefore has a defined no-MAJOR fallback: **the lowest version_seq** (any-version ack satisfies — the doc never had a substantive change boundary).
3. `wf_repo.users_with_roles` matches Role **names**; `distribution_entry.target_id` is a role **uuid** → the audience resolver queries `RoleAssignment.role_id` directly (no wf_repo change).
4. There is NO engine-level terminate helper — the sweep's cancel copies the S-dcr-4 inline force-terminate (services/dcr/service.py:664-705): PENDING tasks `with_for_update()` → SKIPPED, instance → a terminal sentinel. The engine file is **not edited** (the welded-path rule).

**Local verification reality (this Windows box):** unit tests run locally per-file (`uv run pytest tests/unit/<file> -v`); the `-m integration` suite is **Linux-CI-only** (psycopg-async vs ProactorEventLoop). Integration steps below therefore verify locally with `--collect-only` + static checks, and run for real in CI at PR time. All `uv run` commands run from `C:\dev\EasySynQ\apps\api`.

---

### Task 1: ORM foundation — enums, models, column, registration, settings, lock key

**Files:**
- Create: `apps/api/src/easysynq_api/db/models/_ack_enums.py`
- Create: `apps/api/src/easysynq_api/db/models/distribution_entry.py`
- Create: `apps/api/src/easysynq_api/db/models/acknowledgement.py`
- Modify: `apps/api/src/easysynq_api/db/models/_workflow_enums.py` (DOC_ACK ×2)
- Modify: `apps/api/src/easysynq_api/db/models/_audit_enums.py` (2 event types)
- Modify: `apps/api/src/easysynq_api/db/models/documented_information.py` (+`acknowledgement_required`)
- Modify: `apps/api/src/easysynq_api/db/models/__init__.py` (register BOTH new modules — the 0027 phantom-DROP rule)
- Modify: `apps/api/src/easysynq_api/config.py` (+`ack_due_days`)
- Modify: `apps/api/src/easysynq_api/services/common/pg_locks.py` (+`LOCK_ACK_SWEEP`)

- [ ] **Step 1: Create `_ack_enums.py`**

```python
"""Native-PG enum bindings for the distribution/acknowledgement cluster (slice S-ack-1).

``distribution_target_type`` carries all four doc-14 §5.6 kinds (R43: enum-4-accept-2 — the API
refuses ``process``/``folder`` until owner-assignment binding lands). ``ack_created_reason`` is the
doc-17 A9-resolution discriminator (release vs R15 target-entry). Created by migration 0048;
referenced here with ``create_type=False``.
"""

from __future__ import annotations

import enum

from sqlalchemy import Enum as SAEnum


class DistributionTargetType(enum.Enum):
    user = "user"
    org_role = "org_role"
    process = "process"  # reserved — owner-assignment track (R43)
    folder = "folder"  # reserved — owner-assignment track (R43)


class AckCreatedReason(enum.Enum):
    release = "release"
    target_entry = "target_entry"


def _vals(e: type[enum.Enum]) -> list[str]:
    return [m.value for m in e]


distribution_target_type_enum = SAEnum(
    DistributionTargetType, name="distribution_target_type", values_callable=_vals, create_type=False
)
ack_created_reason_enum = SAEnum(
    AckCreatedReason, name="ack_created_reason", values_callable=_vals, create_type=False
)

# Canonical value tuples — migration 0048 sources its CREATE TYPE from these (the 0010 rule).
DISTRIBUTION_TARGET_TYPE_VALUES = tuple(_vals(DistributionTargetType))
ACK_CREATED_REASON_VALUES = tuple(_vals(AckCreatedReason))
```

- [ ] **Step 2: Create `distribution_entry.py`** (the `document_link.py` editable-metadata template)

```python
"""The per-document distribution list — WHO a controlled document is issued to (slice S-ack-1;
doc 04 §8.1, doc 14 §5.6, R15/R43).

An entry targets a ``user`` / ``org_role`` (v1) or a reserved ``process`` / ``folder`` kind, with a
per-entry ``ack_required``. Entries are editable issuance **config**, NOT evidence — created and
removed, never updated (grants ``SELECT, INSERT, DELETE``; change = delete + re-add, the
``document_link`` precedent). Acknowledgements deliberately carry NO FK here: entries are
deletable; the Cl 7.3 evidence must survive them.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, Index, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._ack_enums import DistributionTargetType, distribution_target_type_enum


class DistributionEntry(Base):
    __tablename__ = "distribution_entry"
    __table_args__ = (
        UniqueConstraint(
            "document_id", "target_type", "target_id", name="uq_distribution_entry_target"
        ),
        Index("ix_distribution_entry_document_id", "document_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "documented_information.id", ondelete="RESTRICT", name="fk_distribution_entry_document"
        ),
        nullable=False,
    )
    target_type: Mapped[DistributionTargetType] = mapped_column(
        distribution_target_type_enum, nullable=False
    )
    # The targeted principal's id (app_user.id / role.id; process/folder ids reserved). Polymorphic
    # over target_type — no FK by design (the workflow_instance.subject_id precedent).
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    ack_required: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

(`default=True` needs `from sqlalchemy import Boolean` only if typed explicitly — use `Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)` with the `Boolean` import to match house style.)

- [ ] **Step 3: Create `acknowledgement.py`** (the append-only evidence row)

```python
"""The read-and-understood acknowledgement — Clause 7.3 awareness evidence (slice S-ack-1;
doc 04 §8.2, doc 14 §5.6, R43).

An immutable, append-only row pinned to the exact ``document_version_id`` (acknowledging Rev C is
evidence about Rev C forever); the R43 carry-forward satisfaction rule lives in the COVERAGE
computation, never here. NOT a ``record`` subtype (no record_type member) and NOT a
``signature_event`` (``document.acknowledge`` is sig_hook=false; R2's enum has no acknowledge
meaning). DB-grant append-only: migration 0048 REVOKEs UPDATE, DELETE from the app role (the
``capa_stage`` house style — harder than doc 14 §1.2's "App" enforcement).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, Index, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._ack_enums import AckCreatedReason, ack_created_reason_enum


class Acknowledgement(Base):
    __tablename__ = "acknowledgement"
    __table_args__ = (
        # One ack per (user, version) — the decide leg's idempotency backstop.
        UniqueConstraint("user_id", "document_version_id", name="uq_acknowledgement_user_version"),
        # The satisfaction lookup (who acked which seq of this doc).
        Index("ix_acknowledgement_document_id_user_id", "document_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    # Denormalized for coverage queries (doc 14 §5.6 carries only the version FK; org_id +
    # document_id added per the §1.1 convention + the index plan — a spec-noted build divergence).
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "documented_information.id", ondelete="RESTRICT", name="fk_acknowledgement_document"
        ),
        nullable=False,
    )
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_version.id", ondelete="RESTRICT", name="fk_acknowledgement_version"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )
    acknowledged_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    client_ip: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_reason: Mapped[AckCreatedReason] = mapped_column(
        ack_created_reason_enum, nullable=False
    )
```

- [ ] **Step 4: Add the enum members.** In `_workflow_enums.py` append to `WorkflowSubjectType`:

```python
    DOC_ACK = "DOC_ACK"  # S-ack-1: per-user read-&-understood obligations (doc 10 §8.1, R43)
```

and to `TaskType` (after `DCR_TRIAGE`):

```python
    DOC_ACK = "DOC_ACK"  # S-ack-1: the doc-10 §8.1 canonical doc-ack task (FINDING_ACK stays audits')
```

In `_audit_enums.py` append to `EventType` after `BLOB_INTEGRITY_FAILED`:

```python
    # S-ack-1 (doc 04 §8.2, R43): the acknowledgements family. DOCUMENT_ACKNOWLEDGED = a user's
    # read-&-understood act (the immutable acknowledgement row's audit shadow — never a
    # signature_event, R2); DISTRIBUTION_UPDATED = distribution-list/flag management
    # (document.distribute, R42). Both key on object_type=document with scope_ref=identifier so
    # GET /documents/{id}/audit-events surfaces them. Added via ALTER TYPE event_type ADD VALUE in
    # 0048 (the additive pattern; a from-scratch ``upgrade head`` rebuilds from EVENT_TYPE_VALUES).
    DOCUMENT_ACKNOWLEDGED = "DOCUMENT_ACKNOWLEDGED"
    DISTRIBUTION_UPDATED = "DISTRIBUTION_UPDATED"
```

- [ ] **Step 5: Add the document flag.** In `documented_information.py`, directly after `last_reviewed_at` (before `created_at`):

```python
    # S-ack-1 (doc 04 §8.2, R43): the per-document master switch — obligations exist iff this AND
    # the entry's ack_required. Mutable working-row state; frozen into metadata_snapshot at checkin.
    acknowledgement_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
```

- [ ] **Step 6: Register the modules.** In `db/models/__init__.py` add (alphabetical, mirroring existing lines):

```python
from .acknowledgement import Acknowledgement
from .distribution_entry import DistributionEntry
```

and add `"Acknowledgement", "DistributionEntry",` to `__all__`. (Skipping this makes `alembic check` phantom-DROP the new tables — the 0027 lesson.)

- [ ] **Step 7: Settings + lock key.** In `config.py`, after `blob_verify_sample_size`:

```python
    # S-ack-1 (R43): the informational acknowledgement due window — due_at = mint + N days. RAG
    # display only; no escalation in v1 (the notifications family owns delivery/escalation).
    ack_due_days: int = 14
```

In `pg_locks.py` after `LOCK_BLOB_VERIFY`:

```python
# S-ack-1: serialize the acknowledgement sweep (daily Beat + the doc-scoped release/distribution
# enqueues share one lock — overlapping fires must not double-mint per-user instances).
LOCK_ACK_SWEEP = 7710008
```

- [ ] **Step 8: Static gate.** Run: `uv run ruff check . ; uv run ruff format --check . ; uv run mypy --strict src`
Expected: clean (no new errors).

- [ ] **Step 9: Commit.**

```bash
git add apps/api/src/easysynq_api/db/models apps/api/src/easysynq_api/config.py apps/api/src/easysynq_api/services/common/pg_locks.py
git commit -m "feat(s-ack-1): ORM foundation - distribution_entry + acknowledgement models, DOC_ACK enums, ack settings"
```

---

### Task 2: Migration 0048 + authz catalog bump

**Files:**
- Create: `migrations/versions/0048_acknowledgements.py`
- Modify: `apps/api/tests/integration/test_authz.py` (99 → 100 + the R42 flag block)

- [ ] **Step 1: Write the migration.** Full file (the 0040 create_table + 0045 seed + 0047 key-seed recipes):

```python
"""S-ack-1 (doc 04 §8, R15/R42/R43): the acknowledgements family schema.

Creates ``distribution_entry`` (editable issuance config — SELECT/INSERT/DELETE) and the
append-only ``acknowledgement`` evidence row (REVOKE UPDATE,DELETE — the capa_stage house style),
adds ``documented_information.acknowledgement_required``, the additive DOC_ACK task/subject enum
values + the two audit event types, seeds the R42 ``document.distribute`` key (catalog 99 → 100,
granted to QMS Owner) and the single-stage ``doc_acknowledgement`` workflow definition (the 0045
recipe: per-user context assignee, quorum ANY, NO signature block — an ack is never a
signature_event, R2).

Revision ID: 0048_acknowledgements
Revises: 0047_blob_verify_drift_read
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

from easysynq_api.db.models._ack_enums import (
    ACK_CREATED_REASON_VALUES,
    DISTRIBUTION_TARGET_TYPE_VALUES,
)

revision: str = "0048_acknowledgements"
down_revision: str | None = "0047_blob_verify_drift_read"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "easysynq_app"
_NEW_KEY = "document.distribute"
_DEF_KEY = "doc_acknowledgement"
_NEW_EVENT_TYPES = ("DOCUMENT_ACKNOWLEDGED", "DISTRIBUTION_UPDATED")
_STAGES: tuple[dict[str, Any], ...] = (
    {
        "key": "ack",
        "mode": "PARALLEL",
        "assignees": {
            "context_users": "user_id",
            "task_type": "DOC_ACK",
            "action_expected": "acknowledge",
        },
        "quorum": {"type": "ANY"},
        "transitions": [],
        # NO signature block — an ack writes an acknowledgement row + audit event, never a
        # signature_event (R2/R43; document.acknowledge is sig_hook=false).
    },
)


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Additive enum values (IF NOT EXISTS → idempotent).
    op.execute("ALTER TYPE task_type ADD VALUE IF NOT EXISTS 'DOC_ACK'")
    op.execute("ALTER TYPE workflow_subject_type ADD VALUE IF NOT EXISTS 'DOC_ACK'")
    for value in _NEW_EVENT_TYPES:
        op.execute(f"ALTER TYPE event_type ADD VALUE IF NOT EXISTS '{value}'")

    # 2. The two fresh enums (tuples from the ORM *_VALUES — the 0010 rule).
    postgresql.ENUM(*DISTRIBUTION_TARGET_TYPE_VALUES, name="distribution_target_type").create(
        bind, checkfirst=True
    )
    postgresql.ENUM(*ACK_CREATED_REASON_VALUES, name="ack_created_reason").create(
        bind, checkfirst=True
    )
    target_type = postgresql.ENUM(name="distribution_target_type", create_type=False)
    created_reason = postgresql.ENUM(name="ack_created_reason", create_type=False)

    # 3. The per-document master switch (NOT NULL needs a server_default backfill on an
    # existing table; the ORM uses a Python-side default — the is_singleton precedent).
    op.add_column(
        "documented_information",
        sa.Column(
            "acknowledgement_required",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )

    # 4. distribution_entry — editable issuance config (doc 14 §5.6).
    op.create_table(
        "distribution_entry",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_type", target_type, nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ack_required", sa.Boolean(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_distribution_entry_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documented_information.id"],
            name="fk_distribution_entry_document",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["app_user.id"],
            name="fk_distribution_entry_created_by_app_user",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_distribution_entry"),
        sa.UniqueConstraint(
            "document_id", "target_type", "target_id", name="uq_distribution_entry_target"
        ),
    )
    op.create_index("ix_distribution_entry_document_id", "distribution_entry", ["document_id"])

    # 5. acknowledgement — the append-only Cl 7.3 evidence (doc 14 §5.6 + org_id/document_id/
    # created_reason per the build conventions).
    op.create_table(
        "acknowledgement",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "acknowledged_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("client_ip", sa.Text(), nullable=True),
        sa.Column("created_reason", created_reason, nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_acknowledgement_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documented_information.id"],
            name="fk_acknowledgement_document",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_version.id"],
            name="fk_acknowledgement_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name="fk_acknowledgement_user_id_app_user",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_acknowledgement"),
        sa.UniqueConstraint(
            "user_id", "document_version_id", name="uq_acknowledgement_user_version"
        ),
    )
    op.create_index(
        "ix_acknowledgement_document_id_user_id", "acknowledgement", ["document_id", "user_id"]
    )

    # 6. Least-privilege grants (pg_roles-guarded): distribution_entry is editable config
    # (no UPDATE — change = delete + re-add); acknowledgement is append-only (the capa_stage
    # REVOKE house style).
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                EXECUTE 'GRANT SELECT, INSERT, DELETE ON distribution_entry TO {_APP_ROLE}';
                EXECUTE 'REVOKE UPDATE ON distribution_entry FROM {_APP_ROLE}';
                EXECUTE 'GRANT SELECT, INSERT ON acknowledgement TO {_APP_ROLE}';
                EXECUTE 'REVOKE UPDATE, DELETE ON acknowledgement FROM {_APP_ROLE}';
            END IF;
        END $$;
        """
    )

    # 7. R42: seed document.distribute (CONTENT-domain, ARTIFACT-finest, non-sig-hook, non-SoD;
    # catalog 99 → 100). The 0047 recipe.
    permission_t = sa.table(
        "permission",
        sa.column("key", sa.Text),
        sa.column("resource", sa.Text),
        sa.column("action", sa.Text),
        sa.column("is_system_domain", sa.Boolean),
        sa.column("sod_sensitive", sa.Boolean),
        sa.column("sig_hook", sa.Boolean),
        sa.column("finest_scope", postgresql.ENUM(name="scope_level", create_type=False)),
    )
    bind.execute(
        pg_insert(permission_t)
        .values(
            [
                {
                    "key": _NEW_KEY,
                    "resource": "document",
                    "action": "distribute",
                    "is_system_domain": False,
                    "sod_sensitive": False,
                    "sig_hook": False,
                    "finest_scope": "ARTIFACT",
                }
            ]
        )
        .on_conflict_do_nothing(index_elements=["key"])
    )

    # 8. Resilient org lookup (the 0045 HARD variant — the doc_acknowledgement seed is
    # load-bearing: a missing definition makes the daily sweep degrade-to-no-op forever, and the
    # QMS Owner grant should land with it). NEVER skip-if-absent.
    org_id = bind.execute(
        sa.text("SELECT id FROM organization WHERE short_code = 'DEFAULT'")
    ).scalar_one_or_none()
    if org_id is None:
        org_id = bind.execute(sa.text("SELECT id FROM organization")).scalar_one()

    # 8a. Grant document.distribute to QMS Owner (ARTIFACT key on the role's QMS reach — the
    # SYSTEM-level scope_template mirrors the role's existing content grants' broadest template).
    perm_id = bind.execute(
        sa.text("SELECT id FROM permission WHERE key = :k"), {"k": _NEW_KEY}
    ).scalar_one()
    role_id = bind.execute(
        sa.text("SELECT id FROM role WHERE org_id = :o AND name = 'QMS Owner'"),
        {"o": org_id},
    ).scalar_one_or_none()
    if role_id is not None:
        role_grant_t = sa.table(
            "role_grant",
            sa.column("org_id", postgresql.UUID(as_uuid=True)),
            sa.column("role_id", postgresql.UUID(as_uuid=True)),
            sa.column("permission_id", postgresql.UUID(as_uuid=True)),
            sa.column("scope_template", postgresql.JSONB),
        )
        bind.execute(
            pg_insert(role_grant_t)
            .values(
                [
                    {
                        "org_id": org_id,
                        "role_id": role_id,
                        "permission_id": perm_id,
                        "scope_template": {"level": "SYSTEM"},
                    }
                ]
            )
            .on_conflict_do_nothing(index_elements=["org_id", "role_id", "permission_id"])
        )

    # 9. The doc_acknowledgement workflow definition (the 0045 seed shape verbatim).
    definition_t = sa.table(
        "workflow_definition",
        sa.column("org_id", postgresql.UUID(as_uuid=True)),
        sa.column("key", sa.Text),
        sa.column("version", sa.Integer),
        sa.column("effective", sa.Boolean),
        sa.column("subject_type", postgresql.ENUM(name="workflow_subject_type", create_type=False)),
        sa.column("stages", postgresql.JSONB),
        sa.column("default_sla", postgresql.JSONB),
    )
    bind.execute(
        pg_insert(definition_t)
        .values(
            org_id=org_id,
            key=_DEF_KEY,
            version=1,
            effective=True,
            subject_type="DOC_ACK",
            stages={"entry": "ack"},
            default_sla=None,  # due_at is stamped by the sweep (now + ACK_DUE_DAYS), not an SLA
        )
        .on_conflict_do_nothing(index_elements=["org_id", "key", "version"])
    )
    definition_id = bind.execute(
        sa.text(
            "SELECT id FROM workflow_definition WHERE org_id = :org AND key = :key AND version = 1"
        ),
        {"org": org_id, "key": _DEF_KEY},
    ).scalar_one()
    stage_t = sa.table(
        "workflow_stage",
        sa.column("org_id", postgresql.UUID(as_uuid=True)),
        sa.column("definition_id", postgresql.UUID(as_uuid=True)),
        sa.column("key", sa.Text),
        sa.column("mode", postgresql.ENUM(name="workflow_stage_mode", create_type=False)),
        sa.column("assignees", postgresql.JSONB),
        sa.column("quorum", postgresql.JSONB),
        sa.column("transitions", postgresql.JSONB),
        sa.column("signature", postgresql.JSONB),
    )
    for st in _STAGES:
        bind.execute(
            pg_insert(stage_t)
            .values(
                org_id=org_id,
                definition_id=definition_id,
                key=st["key"],
                mode=st["mode"],
                assignees=st.get("assignees"),
                quorum=st.get("quorum"),
                transitions=st.get("transitions"),
                signature=st.get("signature"),
            )
            .on_conflict_do_nothing(index_elements=["definition_id", "key"])
        )


def downgrade() -> None:
    bind = op.get_bind()
    # Seed delete guarded by child instances (the 0023/0045 precedent).
    has_instances = bind.execute(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM workflow_instance wi "
            "JOIN workflow_definition wd ON wi.definition_id = wd.id WHERE wd.key = :k)"
        ),
        {"k": _DEF_KEY},
    ).scalar()
    if not has_instances:
        bind.execute(
            sa.text(
                "DELETE FROM workflow_stage WHERE definition_id IN "
                "(SELECT id FROM workflow_definition WHERE key = :k)"
            ),
            {"k": _DEF_KEY},
        )
        bind.execute(sa.text("DELETE FROM workflow_definition WHERE key = :k"), {"k": _DEF_KEY})
    # role_grant BEFORE permission (RESTRICT FK).
    bind.execute(
        sa.text(
            "DELETE FROM role_grant WHERE permission_id IN "
            "(SELECT id FROM permission WHERE key = :k)"
        ),
        {"k": _NEW_KEY},
    )
    bind.execute(sa.text("DELETE FROM permission WHERE key = :k"), {"k": _NEW_KEY})
    op.drop_index("ix_acknowledgement_document_id_user_id", table_name="acknowledgement")
    op.drop_table("acknowledgement")
    op.drop_index("ix_distribution_entry_document_id", table_name="distribution_entry")
    op.drop_table("distribution_entry")
    op.drop_column("documented_information", "acknowledgement_required")
    for enum_name in ("ack_created_reason", "distribution_target_type"):
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
    # The ADD VALUEs are irreversible in PG → no-op (the 0011/0047 precedent).
```

- [ ] **Step 2: Bump the catalog assertion.** In `tests/integration/test_authz.py` (line ~131):

```python
    # 96 closed v1 keys + the 2 additive retention.* keys (0028) + drift.read (0047)
    # + document.distribute (0048) — R38/R42.
    assert len(perms) == 100
```

and after the `drift.read` flag block add:

```python
    # R38/R42: document.distribute is CONTENT-domain, ARTIFACT-finest, non-sig-hook, non-SoD.
    assert by_key["document.distribute"]["is_system_domain"] is False
    assert by_key["document.distribute"]["sig_hook"] is False
    assert by_key["document.distribute"]["sod_sensitive"] is False
```

- [ ] **Step 3: Round-trip the migration.** Run the `/check-migrations` skill (throwaway PG16: upgrade head → downgrade 0047 → upgrade head → `alembic check`).
Expected: clean — no phantom diffs (the ORM models registered in Task 1 make `alembic check` see both tables).

- [ ] **Step 4: Commit.**

```bash
git add migrations/versions/0048_acknowledgements.py apps/api/tests/integration/test_authz.py
git commit -m "feat(s-ack-1): migration 0048 - ack schema + DOC_ACK enums + R42 document.distribute + doc_acknowledgement seed"
```

---

### Task 3: Pure domain rules (TDD) — satisfaction + sweep set-algebra

**Files:**
- Create: `apps/api/src/easysynq_api/domain/ack/__init__.py`
- Create: `apps/api/src/easysynq_api/domain/ack/rules.py`
- Test: `apps/api/tests/unit/test_ack_rules.py`

- [ ] **Step 1: Write the failing tests** (`tests/unit/test_ack_rules.py`):

```python
"""Unit tests for the S-ack-1 pure rules (domain/ack/rules.py): the R43 last-MAJOR satisfaction
boundary (incl. the no-MAJOR fallback) and the sweep's cancel-before-mint set-algebra."""

from __future__ import annotations

import uuid

import pytest

from easysynq_api.domain.ack.rules import last_major_seq, plan_obligations

pytestmark = pytest.mark.unit

U1, U2, U3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def test_last_major_is_the_newest_major_at_or_below_current() -> None:
    # (seq, is_major): 1.0 MAJOR, 1.1 MINOR, 2.0 MAJOR, 2.1 MINOR — current seq 4.
    versions = [(1, True), (2, False), (3, True), (4, False)]
    assert last_major_seq(versions, current_seq=4) == 3


def test_last_major_ignores_versions_beyond_current() -> None:
    # A scheduled future MAJOR (seq 5) must not move the boundary for the current Effective (4).
    versions = [(1, True), (2, False), (3, True), (4, False), (5, True)]
    assert last_major_seq(versions, current_seq=4) == 3


def test_no_major_falls_back_to_lowest_seq() -> None:
    # _checkin defaults MINOR, so a chain with no MAJOR is real: any-version ack satisfies.
    versions = [(1, False), (2, False)]
    assert last_major_seq(versions, current_seq=2) == 1


def test_satisfaction_is_seq_at_or_above_boundary() -> None:
    boundary = last_major_seq([(1, True), (2, False), (3, True)], current_seq=3)
    assert boundary == 3
    # acked Rev 1.1 (seq 2) — below the MAJOR boundary → NOT satisfied; acked seq 3 → satisfied.
    assert not (2 >= boundary)
    assert 3 >= boundary


def test_plan_mints_unsatisfied_audience_without_open_tasks() -> None:
    to_mint, to_cancel = plan_obligations(
        audience={U1, U2}, satisfied=set(), open_tasks={}, last_major=3
    )
    assert to_mint == {U1, U2}
    assert to_cancel == set()


def test_plan_skips_satisfied_and_already_open() -> None:
    to_mint, to_cancel = plan_obligations(
        audience={U1, U2, U3}, satisfied={U1}, open_tasks={U2: 3}, last_major=3
    )
    assert to_mint == {U3}
    assert to_cancel == set()


def test_plan_cancels_left_audience_and_stale_pins_and_remints_in_one_pass() -> None:
    # U1 left the audience; U2's task pins seq 1 < last_major 3 (a MAJOR superseded it);
    # U3's task pins the boundary itself — survives. CANCEL-BEFORE-MINT in ONE pass: the
    # stale-pinned-but-still-in-audience U2 ends the sweep with exactly one fresh task.
    to_mint, to_cancel = plan_obligations(
        audience={U2, U3}, satisfied=set(), open_tasks={U1: 3, U2: 1, U3: 3}, last_major=3
    )
    assert to_cancel == {U1, U2}
    assert to_mint == {U2}


def test_plan_cancels_satisfied_open_tasks_defensively() -> None:
    to_mint, to_cancel = plan_obligations(
        audience={U1}, satisfied={U1}, open_tasks={U1: 3}, last_major=3
    )
    assert to_mint == set()
    assert to_cancel == {U1}
```

- [ ] **Step 2: Run to verify failure.** Run: `uv run pytest tests/unit/test_ack_rules.py -v`
Expected: FAIL — `ModuleNotFoundError: easysynq_api.domain.ack`.

- [ ] **Step 3: Implement** `domain/ack/rules.py` (+ an empty-docstring `__init__.py` re-exporting both):

```python
"""Pure S-ack-1 rules — no I/O (slice S-ack-1; doc 04 §8, R43).

``last_major_seq`` is the R43 satisfaction boundary: a user is satisfied iff they hold an
acknowledgement on a version with ``version_seq >= last_major_seq`` (acks stay version-pinned
evidence; only THIS computation walks MINOR chains). A chain with no MAJOR version is real
(check-in defaults MINOR) — the boundary falls back to the LOWEST seq (any-version ack satisfies;
the doc never had a substantive change boundary).

``plan_obligations`` is the sweep's set-algebra: cancel-before-mint (a stale open task must never
shadow the fresh mint under the open-task guard).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping


def last_major_seq(versions: Iterable[tuple[int, bool]], *, current_seq: int) -> int:
    """The newest MAJOR version_seq at or below ``current_seq``; lowest seq when no MAJOR exists.

    ``versions`` are ``(version_seq, is_major)`` pairs (any order, may include future/scheduled
    seqs beyond ``current_seq`` — those never move the boundary)."""
    in_range = [(seq, major) for seq, major in versions if seq <= current_seq]
    majors = [seq for seq, major in in_range if major]
    if majors:
        return max(majors)
    return min(seq for seq, _ in in_range)


def plan_obligations(
    *,
    audience: set[uuid.UUID],
    satisfied: set[uuid.UUID],
    open_tasks: Mapping[uuid.UUID, int],
    last_major: int,
) -> tuple[set[uuid.UUID], set[uuid.UUID]]:
    """(to_mint, to_cancel) for one ack-eligible document.

    ``open_tasks`` maps user → the open task's pinned version_seq. Cancel wins: a user whose open
    task is cancelled this pass (stale pin) is re-minted by the caller's post-cancel recompute,
    not double-counted here."""
    stale = {u for u, pinned in open_tasks.items() if pinned < last_major}
    left = set(open_tasks) - audience
    already_done = set(open_tasks) & satisfied
    to_cancel = stale | left | already_done
    surviving_open = set(open_tasks) - to_cancel
    to_mint = audience - satisfied - surviving_open
    # A cancelled-stale user who is still in the audience re-mints in the same pass:
    to_mint |= (stale & audience) - satisfied
    return to_mint, to_cancel
```

The invariant pinned by the one-pass test above: *a stale-pinned audience member ends the sweep with exactly one fresh open task* — cancel-before-mint inside one sweep run, no second pass needed.

- [ ] **Step 4: Run to verify pass.** Run: `uv run pytest tests/unit/test_ack_rules.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Static gate + commit.**

```bash
uv run ruff check . && uv run mypy --strict src
git add apps/api/src/easysynq_api/domain/ack apps/api/tests/unit/test_ack_rules.py
git commit -m "feat(s-ack-1): pure ack rules - R43 last-MAJOR boundary + sweep set-algebra (TDD)"
```

---

### Task 4: The ack service package — audience, satisfaction queries, coverage

**Files:**
- Create: `apps/api/src/easysynq_api/services/ack/__init__.py`
- Create: `apps/api/src/easysynq_api/services/ack/queries.py`
- Test: covered by Task 8's integration file (session-bound — no unit fakes per house convention; `--collect-only` locally)

- [ ] **Step 1: Write `services/ack/queries.py`** — the session-bound reads every other piece composes:

```python
"""S-ack-1 data access: audience resolution, satisfaction, coverage (doc 04 §8.1/§8.2, R43).

Audience resolution is LIVE (doc 04 §8.1 "resolved dynamically"): user targets + org_role members
(RoleAssignment by role_id — NOT wf_repo.users_with_roles, which matches Role NAMES), restricted
to ACTIVE non-guest users (doc 07 §5.4: a read_only/guest principal can never acknowledge).
``process``/``folder`` targets are refused at create (R43) so the resolver never sees them.
Coverage truth = distribution × acknowledgement under the R43 boundary; tasks are only the to-do
surface (and the source of due_at for the overdue count)."""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._ack_enums import DistributionTargetType
from ...db.models._user_enums import UserStatus
from ...db.models._vault_enums import ChangeSignificance
from ...db.models._workflow_enums import TaskState, WorkflowSubjectType
from ...db.models.acknowledgement import Acknowledgement
from ...db.models.app_user import AppUser
from ...db.models.distribution_entry import DistributionEntry
from ...db.models.document_version import DocumentVersion
from ...db.models.documented_information import DocumentedInformation
from ...db.models.role import RoleAssignment
from ...db.models.workflow import Task, WorkflowInstance
from ...domain.ack.rules import last_major_seq


async def list_entries(
    session: AsyncSession, document_id: uuid.UUID
) -> list[DistributionEntry]:
    return list(
        (
            await session.execute(
                select(DistributionEntry)
                .where(DistributionEntry.document_id == document_id)
                .order_by(DistributionEntry.created_at)
            )
        )
        .scalars()
        .all()
    )


async def resolve_audience(
    session: AsyncSession, org_id: uuid.UUID, entries: list[DistributionEntry]
) -> set[uuid.UUID]:
    """The deduplicated, ACTIVE, non-guest user set across this doc's ack-required entries."""
    direct = {
        e.target_id
        for e in entries
        if e.ack_required and e.target_type is DistributionTargetType.user
    }
    role_ids = {
        e.target_id
        for e in entries
        if e.ack_required and e.target_type is DistributionTargetType.org_role
    }
    candidates: set[uuid.UUID] = set(direct)
    if role_ids:
        rows = (
            await session.execute(
                select(RoleAssignment.user_id).where(
                    RoleAssignment.org_id == org_id, RoleAssignment.role_id.in_(role_ids)
                )
            )
        ).scalars()
        candidates.update(rows)
    if not candidates:
        return set()
    active = (
        await session.execute(
            select(AppUser.id).where(
                AppUser.id.in_(candidates),
                AppUser.org_id == org_id,
                AppUser.status == UserStatus.ACTIVE,
                AppUser.is_guest.is_(False),
            )
        )
    ).scalars()
    return set(active)


async def boundary_seq(session: AsyncSession, doc: DocumentedInformation) -> int | None:
    """The R43 last-MAJOR boundary for the doc's current Effective version (None when no
    Effective version exists)."""
    if doc.current_effective_version_id is None:
        return None
    current = await session.get(DocumentVersion, doc.current_effective_version_id)
    if current is None:
        return None
    pairs = (
        await session.execute(
            select(
                DocumentVersion.version_seq,
                DocumentVersion.change_significance == ChangeSignificance.MAJOR,
            ).where(DocumentVersion.document_id == doc.id)
        )
    ).all()
    return last_major_seq(
        [(seq, bool(major)) for seq, major in pairs], current_seq=current.version_seq
    )


async def satisfied_users(
    session: AsyncSession, document_id: uuid.UUID, boundary: int
) -> set[uuid.UUID]:
    """Users holding an acknowledgement at or above the boundary (the carry-forward rule)."""
    rows = (
        await session.execute(
            select(Acknowledgement.user_id)
            .join(DocumentVersion, DocumentVersion.id == Acknowledgement.document_version_id)
            .where(
                Acknowledgement.document_id == document_id,
                DocumentVersion.version_seq >= boundary,
            )
            .distinct()
        )
    ).scalars()
    return set(rows)


async def open_ack_tasks(
    session: AsyncSession, document_id: uuid.UUID
) -> list[tuple[Task, WorkflowInstance]]:
    """Open (PENDING) DOC_ACK tasks for this document, with their instances."""
    return list(
        (
            await session.execute(
                select(Task, WorkflowInstance)
                .join(WorkflowInstance, Task.instance_id == WorkflowInstance.id)
                .where(
                    WorkflowInstance.subject_type == WorkflowSubjectType.DOC_ACK,
                    WorkflowInstance.subject_id == document_id,
                    Task.state == TaskState.PENDING,
                )
            )
        ).all()
    )


def pinned_seq_map(
    pairs: list[tuple[Task, WorkflowInstance]], seq_by_version: dict[str, int]
) -> dict[uuid.UUID, int]:
    """user → the open task's pinned version_seq (context document_version_id → seq). A task
    whose context is unreadable maps to seq 0 — always stale, so the sweep cancels it."""
    out: dict[uuid.UUID, int] = {}
    for task, instance in pairs:
        if task.assignee_user_id is None:
            continue
        vid = str((instance.context or {}).get("document_version_id", ""))
        out[task.assignee_user_id] = seq_by_version.get(vid, 0)
    return out


async def version_seqs(session: AsyncSession, version_ids: set[str]) -> dict[str, int]:
    valid = [uuid.UUID(v) for v in version_ids if v]
    if not valid:
        return {}
    rows = (
        await session.execute(
            select(DocumentVersion.id, DocumentVersion.version_seq).where(
                DocumentVersion.id.in_(valid)
            )
        )
    ).all()
    return {str(vid): seq for vid, seq in rows}


async def coverage_counts(
    session: AsyncSession, doc: DocumentedInformation
) -> dict[str, Any] | None:
    """{required, acknowledged, pending, overdue} for the current Effective version; None when
    the doc has no Effective version (an honest absence, not a 0/0)."""
    boundary = await boundary_seq(session, doc)
    if boundary is None:
        return None
    entries = await list_entries(session, doc.id)
    audience = await resolve_audience(session, doc.org_id, entries)
    if not doc.acknowledgement_required:
        return {"required": 0, "acknowledged": 0, "pending": 0, "overdue": 0}
    satisfied = await satisfied_users(session, doc.id, boundary)
    now = datetime.datetime.now(datetime.UTC)
    open_pairs = await open_ack_tasks(session, doc.id)
    overdue = {
        t.assignee_user_id
        for t, _ in open_pairs
        if t.due_at is not None and t.due_at < now and t.assignee_user_id in audience
    }
    done = len(audience & satisfied)
    return {
        "required": len(audience),
        "acknowledged": done,
        "pending": len(audience) - done,
        "overdue": len(overdue - satisfied),
    }


async def coverage_matrix(
    session: AsyncSession, doc: DocumentedInformation
) -> list[dict[str, Any]]:
    """The named per-user status list (the QM chase view, gate document.distribute)."""
    boundary = await boundary_seq(session, doc)
    entries = await list_entries(session, doc.id)
    audience = await resolve_audience(session, doc.org_id, entries)
    if boundary is None or not audience:
        return []
    satisfied = await satisfied_users(session, doc.id, boundary)
    open_pairs = await open_ack_tasks(session, doc.id)
    due_by_user = {t.assignee_user_id: t.due_at for t, _ in open_pairs}
    acks = (
        await session.execute(
            select(Acknowledgement, DocumentVersion.revision_label)
            .join(DocumentVersion, DocumentVersion.id == Acknowledgement.document_version_id)
            .where(
                Acknowledgement.document_id == doc.id,
                Acknowledgement.user_id.in_(audience),
                DocumentVersion.version_seq >= boundary,
            )
        )
    ).all()
    ack_by_user = {a.user_id: (a, label) for a, label in acks}
    users = (
        await session.execute(
            select(AppUser.id, AppUser.display_name).where(AppUser.id.in_(audience))
        )
    ).all()
    now = datetime.datetime.now(datetime.UTC)
    out: list[dict[str, Any]] = []
    for uid, name in sorted(users, key=lambda r: (r[1] or "")):
        ack_pair = ack_by_user.get(uid)
        due = due_by_user.get(uid)
        if uid in satisfied and ack_pair is not None:
            ack, label = ack_pair
            status = "acknowledged"
            out.append(
                {
                    "user_id": str(uid),
                    "display_name": name,
                    "status": status,
                    "acknowledged_at": ack.acknowledged_at.isoformat(),
                    "acknowledged_revision_label": label,
                    "due_at": None,
                }
            )
        else:
            status = "overdue" if (due is not None and due < now) else "pending"
            out.append(
                {
                    "user_id": str(uid),
                    "display_name": name,
                    "status": status,
                    "acknowledged_at": None,
                    "acknowledged_revision_label": None,
                    "due_at": due.isoformat() if due else None,
                }
            )
    return out
```

(Check the actual `UserStatus` import path — `db/models/app_user.py` defines it in-module per the orm intel; adjust the import to `from ...db.models.app_user import AppUser, UserStatus` if so.)

- [ ] **Step 2: Create `services/ack/__init__.py`:**

```python
"""The acknowledgements engine (slice S-ack-1; doc 04 §8, R42/R43)."""

from .decide import decide_doc_ack  # noqa: F401  (wired in Task 7)
from .queries import coverage_counts, coverage_matrix, list_entries, resolve_audience  # noqa: F401
from .sink import get_ack_enqueue_sink, set_ack_enqueue_sink  # noqa: F401  (wired in Task 6)
from .sweep import sweep_acks  # noqa: F401  (wired in Task 5)
```

(Until Tasks 5–7 land, comment the not-yet-existing imports and uncomment per task — or create stub modules in those tasks first; keep `__init__` consistent with what exists at each commit.)

- [ ] **Step 3: Static gate + commit.**

```bash
uv run ruff check . && uv run mypy --strict src
git add apps/api/src/easysynq_api/services/ack
git commit -m "feat(s-ack-1): ack queries - live audience resolution + R43 coverage reads"
```

---

### Task 5: The sweep — cancel-before-mint, Celery task, Beat, registration test

**Files:**
- Create: `apps/api/src/easysynq_api/services/ack/sweep.py`
- Create: `apps/api/src/easysynq_api/tasks/ack.py`
- Modify: `apps/api/src/easysynq_api/tasks/__init__.py` (add `ack,` to the import tuple)
- Modify: `apps/api/src/easysynq_api/tasks/app.py` (Beat entry)
- Test: `apps/api/tests/unit/test_ack_task_registration.py`

- [ ] **Step 1: Write the failing registration test:**

```python
"""The ack-sweep Celery task is registered AND Beat-scheduled (the tasks/__init__ rule)."""

from easysynq_api.tasks import app


def test_ack_sweep_task_is_registered() -> None:
    assert "easysynq.ack.sweep" in app.tasks


def test_ack_sweep_is_beat_scheduled_daily() -> None:
    entries = {e["task"]: e["schedule"] for e in app.conf.beat_schedule.values()}
    assert entries.get("easysynq.ack.sweep") == 86400.0
```

- [ ] **Step 2: Run to verify failure.** Run: `uv run pytest tests/unit/test_ack_task_registration.py -v`
Expected: FAIL (`easysynq.ack.sweep` not in app.tasks).

- [ ] **Step 3: Implement `services/ack/sweep.py`:**

```python
"""The acknowledgement sweep — the ONE universal obligation mint (slice S-ack-1; doc 04 §8.2/§8.3,
R15/R43, spec §4).

One idempotent pass covers EVERY trigger family: release (the cutover enqueues a doc-scoped run
post-commit), R15 target entry, flag flips, entry adds/removes, imported docs later gaining
distribution — the daily Beat run is the self-heal. CANCEL-BEFORE-MINT: a stale open task (left
audience / superseded-by-MAJOR pin / lapsed flag) is terminated FIRST so the open-task mint guard
never shadows the fresh mint. Cancel = instance termination + skip PENDING tasks (the S-dcr-4
inline force-terminate; NEVER a task-state flip — decide() accepts only PENDING). One session, one
commit, under LOCK_ACK_SWEEP (the sweep_reviews posture: acks-late re-delivery makes concurrent
runs real)."""

from __future__ import annotations

import datetime
import logging
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...db.models._ack_enums import AckCreatedReason
from ...db.models._vault_enums import DocumentCurrentState, DocumentKind
from ...db.models._workflow_enums import TaskState, WorkflowSubjectType
from ...db.models.documented_information import DocumentedInformation
from ...db.models.workflow import Task
from ...domain.ack.rules import plan_obligations
from ..common.pg_locks import LOCK_ACK_SWEEP, pg_advisory_lock
from ..workflow import engine as wf_engine
from ..workflow import repository as wf_repo
from . import queries

logger = logging.getLogger("easysynq.ack")

_TERMINAL_INSTANCE_STATES = (
    wf_engine.COMPLETED,
    wf_engine.REJECTED,
    wf_engine.NEEDS_ATTENTION,
    "CANCELLED",
)
CANCELLED = "CANCELLED"  # the sweep's terminal sentinel for a lapsed obligation
_DEF_KEY = "doc_acknowledgement"


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


async def _cancel_instance(session: AsyncSession, instance_id: uuid.UUID) -> bool:
    """Force-terminate one obligation instance (the S-dcr-4 inline precedent): PENDING tasks →
    SKIPPED under FOR UPDATE, instance → CANCELLED. Returns False if already terminal."""
    instance = await wf_repo.lock_instance_for_update(session, instance_id)
    if instance is None or instance.current_state in _TERMINAL_INSTANCE_STATES:
        return False
    pending = (
        (
            await session.execute(
                select(Task)
                .where(Task.instance_id == instance.id, Task.state == TaskState.PENDING)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    for sibling in pending:
        sibling.state = TaskState.SKIPPED
    instance.current_state = CANCELLED
    return True


async def sweep_acks(
    session: AsyncSession, *, document_id: uuid.UUID | None = None
) -> dict[str, int]:
    """Reconcile obligations for every ack-eligible Effective document (or ONE doc when scoped).

    Pass A (eligible docs): cancel lapsed open tasks, then mint one instance+task per unsatisfied
    audience member with no surviving open task, pinned to the current Effective version, due_at =
    now + ACK_DUE_DAYS. Pass B: cancel ALL open DOC_ACK tasks on docs that are no longer eligible
    (flag off / not Effective). Idempotent: re-runs no-op."""
    async with pg_advisory_lock(session, LOCK_ACK_SWEEP) as held:
        if not held:
            logger.info("ack_sweep: another sweep holds the lock; skipping this tick")
            return {"tasks_created": 0, "tasks_cancelled": 0, "skipped_lock_held": 1}

        created = cancelled = 0
        doc_filter = [DocumentedInformation.id == document_id] if document_id else []

        eligible = (
            (
                await session.execute(
                    select(DocumentedInformation).where(
                        DocumentedInformation.kind == DocumentKind.DOCUMENT,
                        DocumentedInformation.current_state == DocumentCurrentState.Effective,
                        DocumentedInformation.acknowledgement_required.is_(True),
                        *doc_filter,
                    )
                )
            )
            .scalars()
            .all()
        )
        # Resolve the definition ONCE; a mis-seeded org degrades to a logged no-op (the
        # sweep_reviews posture), never a 500-shaped Beat failure.
        if eligible and (
            await wf_repo.effective_definition(
                session, eligible[0].org_id, _DEF_KEY, WorkflowSubjectType.DOC_ACK
            )
            is None
        ):
            logger.error("ack_sweep: no effective doc_acknowledgement definition — seed missing")
            eligible = []

        eligible_ids = {d.id for d in eligible}
        due_at = _now() + datetime.timedelta(days=get_settings().ack_due_days)

        for doc in eligible:
            boundary = await queries.boundary_seq(session, doc)
            if boundary is None:
                continue  # Effective state without a version row — FK-guaranteed unreachable
            entries = await queries.list_entries(session, doc.id)
            audience = await queries.resolve_audience(session, doc.org_id, entries)
            satisfied = await queries.satisfied_users(session, doc.id, boundary)
            open_pairs = await queries.open_ack_tasks(session, doc.id)
            seqs = await queries.version_seqs(
                session,
                {str((i.context or {}).get("document_version_id", "")) for _, i in open_pairs},
            )
            open_map = queries.pinned_seq_map(open_pairs, seqs)
            to_mint, to_cancel = plan_obligations(
                audience=audience, satisfied=satisfied, open_tasks=open_map, last_major=boundary
            )
            instance_by_user = {
                t.assignee_user_id: i.id for t, i in open_pairs if t.assignee_user_id
            }
            for user_id in to_cancel:
                iid = instance_by_user.get(user_id)
                if iid is not None and await _cancel_instance(session, iid):
                    cancelled += 1
            reason = (
                AckCreatedReason.release if document_id is not None else AckCreatedReason.target_entry
            )
            for user_id in to_mint:
                instance = await wf_engine.instantiate(
                    session,
                    org_id=doc.org_id,
                    definition_key=_DEF_KEY,
                    subject_type=WorkflowSubjectType.DOC_ACK,
                    subject_id=doc.id,
                    context={
                        "user_id": str(user_id),
                        "document_id": str(doc.id),
                        "document_version_id": str(doc.current_effective_version_id),
                        "created_reason": reason.value,
                        "identifier": doc.identifier,
                    },
                    actor=None,
                )
                await session.flush()
                await session.execute(
                    update(Task).where(Task.instance_id == instance.id).values(due_at=due_at)
                )
                created += 1

        # Pass B: docs with open DOC_ACK obligations that are no longer eligible.
        stale_subject_q = (
            select(Task.instance_id, queries.WorkflowInstance.subject_id)
            .join(queries.WorkflowInstance, Task.instance_id == queries.WorkflowInstance.id)
            .where(
                queries.WorkflowInstance.subject_type == WorkflowSubjectType.DOC_ACK,
                Task.state == TaskState.PENDING,
            )
        )
        if document_id is not None:
            stale_subject_q = stale_subject_q.where(
                queries.WorkflowInstance.subject_id == document_id
            )
        for instance_id, subject_id in (await session.execute(stale_subject_q)).all():
            if subject_id not in eligible_ids and await _cancel_instance(session, instance_id):
                cancelled += 1

        await session.commit()
        return {"tasks_created": created, "tasks_cancelled": cancelled, "skipped_lock_held": 0}
```

Implementation notes for the engineer:
- `created_reason`: a doc-scoped run is triggered by release/distribution writes → `release`; the daily org-wide run's mints are target-entry catch-ups → `target_entry`. This heuristic is the spec §4.3 rule ("`release` when minted by the release-scoped run for a fresh version, else `target_entry`").
- Import `WorkflowInstance` directly (`from ...db.models.workflow import Task, WorkflowInstance`) rather than via `queries.` — the sketch above shows the dependency; write it as a direct import.
- Pass B intentionally re-checks docs Pass A already handled (a doc-scoped run where the doc became ineligible falls through Pass A's empty `eligible` to Pass B).

- [ ] **Step 4: Implement `tasks/ack.py`** (the `tasks/review.py` shape + the doc-scoped arg):

```python
"""Celery/Beat task for the S-ack-1 acknowledgement sweep (doc 04 §8.2/§8.3, R15/R43)."""

from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import get_settings
from ..services.ack.sweep import sweep_acks
from .app import app

logger = logging.getLogger("easysynq.ack.tasks")


async def _run_ack_sweep(document_id: str | None) -> dict[str, int]:
    engine = create_async_engine(get_settings().database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session:
            summary = await sweep_acks(
                session, document_id=uuid.UUID(document_id) if document_id else None
            )
            logger.info("ack.sweep", extra={"extra_fields": summary})
            return summary
    finally:
        await engine.dispose()


@app.task(name="easysynq.ack.sweep")  # type: ignore[untyped-decorator]
def ack_sweep(document_id: str | None = None) -> dict[str, int]:
    """The daily (or doc-scoped, post-release/post-distribution) acknowledgement sweep."""
    return asyncio.run(_run_ack_sweep(document_id))
```

- [ ] **Step 5: Register + schedule.** In `tasks/__init__.py` add `ack,` first in the import tuple. In `tasks/app.py` add to `beat_schedule`:

```python
        # S-ack-1: daily acknowledgement sweep (doc 04 §8.3 / R15 target-entry catch-up + the
        # self-heal for lost doc-scoped enqueues).
        "ack-sweep": {
            "task": "easysynq.ack.sweep",
            "schedule": 86400.0,  # daily
        },
```

- [ ] **Step 6: Run to verify pass.** Run: `uv run pytest tests/unit/test_ack_task_registration.py tests/unit/test_ack_rules.py -v`
Expected: PASS.

- [ ] **Step 7: Static gate + commit.**

```bash
uv run ruff check . && uv run mypy --strict src
git add apps/api/src/easysynq_api/services/ack/sweep.py apps/api/src/easysynq_api/tasks apps/api/tests/unit/test_ack_task_registration.py
git commit -m "feat(s-ack-1): the ack sweep - cancel-before-mint universal mint + daily Beat task"
```

---

### Task 6: The enqueue sink + release/obsolete hooks

**Files:**
- Create: `apps/api/src/easysynq_api/services/ack/sink.py`
- Modify: `apps/api/src/easysynq_api/services/vault/lifecycle.py` (3 post-commit call sites)

- [ ] **Step 1: Write `services/ack/sink.py`** (the `mirror_sink.py` trio verbatim, renamed):

```python
"""The ack-sweep enqueue seam (slice S-ack-1) — the mirror_sink Protocol/Celery/Logging/Capturing
trio so tests assert fired-exactly-once-post-commit."""

from __future__ import annotations

import logging
from typing import Protocol

logger = logging.getLogger("easysynq.ack")


class AckEnqueueSink(Protocol):
    def enqueue(self, document_id: str | None = None, reason: str | None = None) -> None: ...


class CeleryAckEnqueueSink:
    """Default sink — dispatches ``easysynq.ack.sweep`` (doc-scoped). Broker errors are logged and
    swallowed (the daily Beat sweep is the self-heal). Lazy task import (the tasks → services
    cycle)."""

    def enqueue(self, document_id: str | None = None, reason: str | None = None) -> None:
        try:
            from ...tasks.ack import ack_sweep

            ack_sweep.delay(document_id)
        except Exception:  # noqa: BLE001 — best-effort; the daily Beat sweep is the backstop
            logger.warning(
                "ack.enqueue_failed",
                extra={"extra_fields": {"document_id": document_id, "reason": reason}},
            )


class LoggingAckEnqueueSink:
    def enqueue(self, document_id: str | None = None, reason: str | None = None) -> None:
        logger.info(
            "ack.enqueue",
            extra={"extra_fields": {"document_id": document_id, "reason": reason}},
        )


class CapturingAckEnqueueSink:
    """Test double — records each enqueue so a test asserts exactly-once, post-commit."""

    def __init__(self) -> None:
        self.calls: list[tuple[str | None, str | None]] = []

    def enqueue(self, document_id: str | None = None, reason: str | None = None) -> None:
        self.calls.append((document_id, reason))


_default_sink: AckEnqueueSink = CeleryAckEnqueueSink()


def get_ack_enqueue_sink() -> AckEnqueueSink:
    return _default_sink


def set_ack_enqueue_sink(sink: AckEnqueueSink) -> AckEnqueueSink:
    global _default_sink
    previous = _default_sink
    _default_sink = sink
    return previous
```

- [ ] **Step 2: Hook the three lifecycle sites** (each beside the existing mirror enqueue, AFTER the commit / outside the SERIALIZABLE txn — a race loser must not enqueue):

In `release(...)` after `get_mirror_enqueue_sink().enqueue("release")`:

```python
    # S-ack-1: a fresh Effective version may re-arm acknowledgements (MAJOR) — doc-scoped sweep.
    get_ack_enqueue_sink().enqueue(str(doc_id), reason="release")
```

In `release_due(...)` — the sweep releases MANY docs; enqueue per released doc. Change the post-loop block to:

```python
        if released:
            get_mirror_enqueue_sink().enqueue("release_due")
            for _version_id, doc_id in released_docs:
                get_ack_enqueue_sink().enqueue(str(doc_id), reason="release_due")
```

where `released_docs` is a new local accumulating `(version_id, doc_id)` alongside the existing `released.append(version_id)` (keep `released`'s shape — the return contract is pinned).

In the obsolete path after `get_mirror_enqueue_sink().enqueue("obsolete")`:

```python
    # S-ack-1: an Obsoleted doc's open obligations lapse — the doc-scoped sweep cancels them.
    get_ack_enqueue_sink().enqueue(str(doc.id), reason="obsolete")
```

Add the import at the top of lifecycle.py: `from ..ack.sink import get_ack_enqueue_sink` (no cycle: `services.ack.sink` imports only `tasks` lazily).

- [ ] **Step 3: Static gate + commit.**

```bash
uv run ruff check . && uv run mypy --strict src
git add apps/api/src/easysynq_api/services/ack/sink.py apps/api/src/easysynq_api/services/vault/lifecycle.py
git commit -m "feat(s-ack-1): ack enqueue sink + post-commit release/obsolete hooks"
```

---

### Task 7: The DOC_ACK decide leg

**Files:**
- Create: `apps/api/src/easysynq_api/services/ack/decide.py`
- Modify: `apps/api/src/easysynq_api/api/workflow.py` (the fourth dispatch branch)

- [ ] **Step 1: Implement `services/ack/decide.py`** (the `decide_periodic_review` template; deviations: enforce `document.acknowledge`, NO signature, the ack INSERT):

```python
"""The DOC_ACK decide leg (slice S-ack-1; doc 04 §8.2, spec §5).

The fourth ``POST /tasks/{id}/decision`` dispatch branch. Authz = candidate-membership
(404-collapse, the sibling posture) AND ``document.acknowledge`` enforced at the document's scope
(its first consumer; key failure is a calm 403 — the task is honestly yours, the capability is
missing). One transaction: engine.decide(_commit=False) + the immutable acknowledgement INSERT +
DOCUMENT_ACKNOWLEDGED — NEVER a signature_event (R2/R43)."""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._ack_enums import AckCreatedReason
from ...db.models._audit_enums import ActorType, AuditObjectType, EventType
from ...db.models._vault_enums import DocumentCurrentState, DocumentKind
from ...db.models.acknowledgement import Acknowledgement
from ...db.models.app_user import AppUser
from ...db.models.audit_event import AuditEvent
from ...db.models.document_type import DocumentType
from ...db.models.document_version import DocumentVersion
from ...db.models.documented_information import DocumentedInformation
from ...db.models.workflow import Task
from ...domain.authz import ResourceContext
from ...logging import request_id_var
from ...problems import ProblemException
from ..authz import AuthzAuditSink, enforce
from ..workflow import engine as wf_engine
from ..workflow import repository as wf_repo
from . import queries

_ALLOWED_OUTCOMES = {"acknowledge"}


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _rid() -> uuid.UUID | None:
    raw = request_id_var.get()
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def _client_ip(request: Request) -> str | None:
    # The pack_share XFF-aware shape (Caddy fronts the API, so the socket peer is the proxy).
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


def _not_found() -> ProblemException:
    return ProblemException(status=404, code="not_found", title="Task not found")


async def decide_doc_ack(
    session: AsyncSession,
    task: Task,
    actor: AppUser,
    *,
    outcome: str,
    comment: str | None,
    idempotency_key: str | None,
    request: Request,
    authz_sink: AuthzAuditSink,
) -> dict[str, Any]:
    instance = await wf_repo.lock_instance_for_update(session, task.instance_id)
    if instance is None or instance.org_id != actor.org_id:
        raise _not_found()
    pool = [str(u) for u in (task.candidate_pool or [])]
    if task.assignee_user_id != actor.id and str(actor.id) not in pool:
        raise _not_found()

    # Live doc re-check under FOR UPDATE — populate_existing because the route's task lookup /
    # any prior session.get has identity-mapped the row (the S-drift-1 trap; spec §5).
    doc = (
        await session.execute(
            select(DocumentedInformation)
            .where(DocumentedInformation.id == instance.subject_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if doc is None or doc.org_id != actor.org_id or doc.kind != DocumentKind.DOCUMENT:
        raise _not_found()

    # The key's first consumer: enforce document.acknowledge at the document's scope. Membership
    # failures 404-collapse above; a key failure is a calm 403 (doc 10 §8.3).
    level: str | None = None
    if doc.document_type_id:
        dt = await session.get(DocumentType, doc.document_type_id)
        level = dt.document_level.value if dt else None
    resource = ResourceContext(
        artifact_id=str(doc.id),
        folder_path=doc.folder_path,
        document_level=level,
        lifecycle_state=doc.current_state.value,
    )
    await enforce(session, authz_sink, request, actor, "document.acknowledge", resource)

    if outcome not in _ALLOWED_OUTCOMES:
        raise ProblemException(
            status=422,
            code="validation_error",
            title="A DOC_ACK task accepts outcome acknowledge",
        )

    # The obligation must still stand (the sweep may not have caught up):
    ctx = instance.context or {}
    try:
        pinned_version_id = uuid.UUID(str(ctx.get("document_version_id")))
    except (ValueError, TypeError):
        raise ProblemException(
            status=409, code="ack_obligation_lapsed", title="Obligation context unreadable"
        ) from None
    pinned = await session.get(DocumentVersion, pinned_version_id)
    if (
        doc.current_state is not DocumentCurrentState.Effective
        or not doc.acknowledgement_required
        or pinned is None
        or pinned.document_id != doc.id
    ):
        raise ProblemException(
            status=409,
            code="ack_obligation_lapsed",
            title="The acknowledgement obligation no longer stands",
        )
    entries = await queries.list_entries(session, doc.id)
    audience = await queries.resolve_audience(session, doc.org_id, entries)
    if actor.id not in audience:
        raise ProblemException(
            status=409,
            code="ack_obligation_lapsed",
            title="The acknowledgement obligation no longer stands",
        )
    boundary = await queries.boundary_seq(session, doc)
    if boundary is None or pinned.version_seq < boundary:
        raise ProblemException(
            status=409,
            code="ack_superseded",
            title="A newer MAJOR revision superseded this acknowledgement task",
        )

    result = await wf_engine.decide(
        session,
        task,
        actor,
        outcome=outcome,
        comment=comment,
        idempotency_key=idempotency_key,
        _commit=False,
    )
    if result.get("replayed"):
        # Response-parity on replay: re-derive the ack row's id (no rows are added).
        ack = (
            await session.execute(
                select(Acknowledgement).where(
                    Acknowledgement.user_id == actor.id,
                    Acknowledgement.document_version_id == pinned_version_id,
                )
            )
        ).scalar_one_or_none()
        result["document_id"] = str(doc.id)
        result["document_version_id"] = str(pinned_version_id)
        result["acknowledgement_id"] = str(ack.id) if ack is not None else None
        await session.commit()
        return result

    reason_raw = str(ctx.get("created_reason", AckCreatedReason.target_entry.value))
    try:
        reason = AckCreatedReason(reason_raw)
    except ValueError:
        reason = AckCreatedReason.target_entry
    ack_row = Acknowledgement(
        org_id=actor.org_id,
        document_id=doc.id,
        document_version_id=pinned_version_id,
        user_id=actor.id,
        acknowledged_at=_now(),
        client_ip=_client_ip(request),
        created_reason=reason,
    )
    session.add(ack_row)
    try:
        await session.flush()  # UNIQUE(user_id, document_version_id) backstop
    except IntegrityError:
        # Raising rolls the WHOLE txn back (engine rows included — _commit=False), so the task
        # stays PENDING; the duplicate means evidence already exists (a cancelled-task remnant
        # race) — the sweep reconciles the open task next pass.
        await session.rollback()
        raise ProblemException(
            status=409, code="conflict", title="Acknowledgement already recorded"
        ) from None
    session.add(
        AuditEvent(
            org_id=actor.org_id,
            occurred_at=_now(),
            actor_id=actor.id,
            actor_type=ActorType.user,
            event_type=EventType.DOCUMENT_ACKNOWLEDGED,
            object_type=AuditObjectType.document,
            object_id=doc.id,
            scope_ref=doc.identifier,
            after={
                "acknowledgement_id": str(ack_row.id),
                "revision_label": pinned.revision_label,
                "created_reason": reason.value,
            },
            request_id=_rid(),
        )
    )
    result["document_id"] = str(doc.id)
    result["document_version_id"] = str(pinned_version_id)
    result["acknowledgement_id"] = str(ack_row.id)
    await session.commit()
    return result
```

- [ ] **Step 2: Add the dispatch branch.** In `api/workflow.py::decide_endpoint`, AFTER the PERIODIC_REVIEW branch and BEFORE the `_OUTCOME_PERMISSION` fallback:

```python
    # A DOC_ACK obligation routes to the ack service: candidate-membership 404-collapses, then
    # document.acknowledge is enforced at the document's scope (the key's first consumer); the
    # decision writes the immutable acknowledgement row + DOCUMENT_ACKNOWLEDGED — no signature
    # (R2/R43; sig_hook=false).
    if instance is not None and instance.subject_type is WorkflowSubjectType.DOC_ACK:
        return await decide_doc_ack(
            session,
            task,
            caller,
            outcome=body.outcome,
            comment=body.comment,
            idempotency_key=idempotency_key,
            request=request,
            authz_sink=authz_sink,
        )
```

with the import `from ..services.ack.decide import decide_doc_ack` added beside the sibling imports.

- [ ] **Step 3: Static gate + commit.**

```bash
uv run ruff check . && uv run mypy --strict src
git add apps/api/src/easysynq_api/services/ack/decide.py apps/api/src/easysynq_api/api/workflow.py
git commit -m "feat(s-ack-1): DOC_ACK decide leg - membership 404-collapse + document.acknowledge enforce + immutable ack row"
```

---

### Task 8: Distribution + acknowledgements endpoints

**Files:**
- Modify: `apps/api/src/easysynq_api/api/documents.py` (4 routes + gate + models, the `document_link` CRUD mirror)
- Test: `apps/api/tests/integration/test_acknowledgements.py` (Task 9 writes it; this task wires the routes)

- [ ] **Step 1: Add the gate + body models** in `api/documents.py` (beside `_manage_metadata` / `MetadataUpdate`):

```python
_distribute = require("document.distribute", async_scope_resolver=_document_scope)


class DistributionEntryCreate(BaseModel):
    target_type: str
    target_id: uuid.UUID
    ack_required: bool = True


class DistributionUpdate(BaseModel):
    acknowledgement_required: bool | None = None
    add_entries: list[DistributionEntryCreate] = Field(default_factory=list)
```

and the imports: `from ..db.models._ack_enums import DistributionTargetType`, `from ..db.models.distribution_entry import DistributionEntry`, `from ..db.models.role import Role`, `from ..services.ack import queries as ack_queries`, `from ..services.ack.sink import get_ack_enqueue_sink`.

- [ ] **Step 2: The four routes** (after the document_link CRUD block):

```python
_V1_TARGET_KINDS = {DistributionTargetType.user, DistributionTargetType.org_role}


def _distribution_entry(e: DistributionEntry) -> dict[str, Any]:
    return {
        "id": str(e.id),
        "target_type": e.target_type.value,
        "target_id": str(e.target_id),
        "ack_required": e.ack_required,
        "created_at": e.created_at.isoformat(),
    }


@router.get("/documents/{document_id}/distribution")
async def get_distribution_endpoint(
    document_id: uuid.UUID,
    caller: AppUser = Depends(_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """The distribution list + the doc flag + a counts-only coverage rollup (doc 15 §8.5; gated
    ``document.read`` — Sam-safe, no names)."""
    doc = await _load_document(session, caller, document_id)
    entries = await ack_queries.list_entries(session, doc.id)
    coverage = await ack_queries.coverage_counts(session, doc)
    return {
        "acknowledgement_required": doc.acknowledgement_required,
        "entries": [_distribution_entry(e) for e in entries],
        "coverage": coverage,
    }


@router.post("/documents/{document_id}/distribution")
async def update_distribution_endpoint(
    document_id: uuid.UUID,
    body: DistributionUpdate,
    caller: AppUser = Depends(_distribute),
    session: AsyncSession = Depends(get_session),
    vault_sink: VaultAuditSink = Depends(get_vault_audit_sink),
) -> dict[str, Any]:
    """Add entries and/or set the doc-level flag (R42 ``document.distribute``). 422 on the
    deferred ``process``/``folder`` kinds (R43); targets are validated in-org. Post-commit the
    doc-scoped ack sweep reconciles obligations."""
    doc = await _load_document(session, caller, document_id, for_update=True)
    before = {"acknowledgement_required": doc.acknowledgement_required}
    added: list[dict[str, Any]] = []
    for item in body.add_entries:
        try:
            kind = DistributionTargetType(item.target_type)
        except ValueError as exc:
            raise ProblemException(
                status=422, code="validation_error", title="Unknown target_type"
            ) from exc
        if kind not in _V1_TARGET_KINDS:
            raise ProblemException(
                status=422,
                code="target_kind_deferred",
                title="process/folder targets are deferred until owner-assignment lands (R43)",
            )
        if kind is DistributionTargetType.user:
            target = await session.get(AppUser, item.target_id)
            if target is None or target.org_id != caller.org_id:
                raise ProblemException(
                    status=404, code="not_found", title="Target user not found"
                )
        else:
            role = await session.get(Role, item.target_id)
            if role is None or role.org_id != caller.org_id:
                raise ProblemException(
                    status=404, code="not_found", title="Target role not found"
                )
        session.add(
            DistributionEntry(
                org_id=doc.org_id,
                document_id=doc.id,
                target_type=kind,
                target_id=item.target_id,
                ack_required=item.ack_required,
                created_by=caller.id,
            )
        )
        added.append(
            {"target_type": kind.value, "target_id": str(item.target_id),
             "ack_required": item.ack_required}
        )
    if body.acknowledgement_required is not None:
        doc.acknowledgement_required = body.acknowledgement_required
        doc.updated_by = caller.id
    try:
        await session.flush()  # UNIQUE(document_id, target_type, target_id) backstop
    except IntegrityError:
        await session.rollback()
        raise ProblemException(
            status=409, code="conflict", title="Distribution entry already exists"
        ) from None
    _emit(
        session,
        vault_sink,
        "DISTRIBUTION_UPDATED",
        caller,
        doc.org_id,
        "document",
        doc.id,
        identifier=doc.identifier,
        after={
            "before": before,
            "acknowledgement_required": doc.acknowledgement_required,
            "added_entries": added,
        },
    )
    await session.commit()
    get_ack_enqueue_sink().enqueue(str(doc.id), reason="distribution_updated")
    entries = await ack_queries.list_entries(session, doc.id)
    coverage = await ack_queries.coverage_counts(session, doc)
    return {
        "acknowledgement_required": doc.acknowledgement_required,
        "entries": [_distribution_entry(e) for e in entries],
        "coverage": coverage,
    }


@router.delete(
    "/documents/{document_id}/distribution/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_distribution_entry_endpoint(
    document_id: uuid.UUID,
    entry_id: uuid.UUID,
    caller: AppUser = Depends(_distribute),
    session: AsyncSession = Depends(get_session),
    vault_sink: VaultAuditSink = Depends(get_vault_audit_sink),
) -> Response:
    """Remove one entry (R42). The doc-scoped sweep cancels lapsed obligations post-commit."""
    doc = await _load_document(session, caller, document_id)
    entry = await session.get(DistributionEntry, entry_id)
    if entry is None or entry.org_id != caller.org_id or entry.document_id != doc.id:
        raise ProblemException(status=404, code="not_found", title="Distribution entry not found")
    before = _distribution_entry(entry)
    await session.delete(entry)
    _emit(
        session,
        vault_sink,
        "DISTRIBUTION_UPDATED",
        caller,
        doc.org_id,
        "document",
        doc.id,
        identifier=doc.identifier,
        after={"removed_entry": before},
    )
    await session.commit()
    get_ack_enqueue_sink().enqueue(str(doc.id), reason="distribution_updated")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/documents/{document_id}/acknowledgements")
async def get_acknowledgements_endpoint(
    document_id: uuid.UUID,
    caller: AppUser = Depends(_distribute),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    """The NAMED per-user status matrix for the current Effective version (doc 13 §6.3: Mara sees
    the full matrix; Sam's own status rides his tasks + the counts rollup). Gated R42
    ``document.distribute``."""
    doc = await _load_document(session, caller, document_id)
    return await ack_queries.coverage_matrix(session, doc)
```

Check the `_emit` helper signature against `services/vault/service.py:60-88` — it is `(session, sink, event_type, actor, obj_type, obj_id, *, identifier, reason, after)` with NO org positional (org comes off the actor); reconcile the calls above to the real signature when wiring (drop the `doc.org_id` positional if not present).

- [ ] **Step 3: Static gate + commit.**

```bash
uv run ruff check . && uv run mypy --strict src
git add apps/api/src/easysynq_api/api/documents.py
git commit -m "feat(s-ack-1): distribution CRUD + counts rollup + the R42-gated named ack matrix"
```

---

### Task 9: Integration test suite

**Files:**
- Create: `apps/api/tests/integration/test_acknowledgements.py`

All tests follow the `test_periodic_review.py` conventions: salted `subj` fixture, `app_under_test`/`app_client`, run-scoped assertions, self-provided preconditions. **Linux-CI-only on this box** — verify locally with `uv run pytest tests/integration/test_acknowledgements.py --collect-only -q`.

- [ ] **Step 1: File header + helpers:**

```python
"""S-ack-1 integration proofs: the full distribute→release→mint→acknowledge→coverage loop,
R15 target-entry catch-up, MINOR carry-forward / MAJOR re-arm, the decide authz matrix,
append-only DB grants, and sweep idempotency (spec §8)."""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Callable
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.session import get_sessionmaker

from . import s5_helpers as s5
from .test_vault import _auth, _checkin, _create, _ensure_user, _map_clause, _upload

pytestmark = pytest.mark.integration


@pytest.fixture
def subj() -> SimpleNamespace:
    salt = uuid.uuid4().hex[:10]
    return SimpleNamespace(
        a=f"kc-ack-author-{salt}",  # author/distributor
        b=f"kc-ack-approver-{salt}",  # approver/releaser
        sam=f"kc-ack-sam-{salt}",  # the acknowledger
        outsider=f"kc-ack-outsider-{salt}",
    )


ACK_PERMS = ("document.acknowledge",)
DISTRIBUTE_PERMS = ("document.distribute",)


async def grant_keys(subject: str, keys: tuple[str, ...]) -> uuid.UUID:
    """SYSTEM-scope overrides for the named keys (the grant_lifecycle shape)."""
    from easysynq_api.db.models.authz import Permission, PermissionOverride, Scope
    from easysynq_api.db.models._authz_enums import Effect, ScopeLevel

    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        for key in keys:
            perm = (await s.execute(select(Permission).where(Permission.key == key))).scalar_one()
            scope = Scope(org_id=user.org_id, level=ScopeLevel.SYSTEM)
            s.add(scope)
            await s.flush()
            s.add(
                PermissionOverride(
                    org_id=user.org_id, user_id=user.id, permission_id=perm.id,
                    effect=Effect.ALLOW, scope_id=scope.id,
                )
            )
        await s.commit()
        return user.id


async def _ack_task_for(doc_uuid: uuid.UUID, user_id: uuid.UUID) -> uuid.UUID:
    """The open DOC_ACK task for (doc, user)."""
    from easysynq_api.db.models._workflow_enums import TaskState, WorkflowSubjectType
    from easysynq_api.db.models.workflow import Task, WorkflowInstance

    async with get_sessionmaker()() as s:
        return (
            await s.execute(
                select(Task.id)
                .join(WorkflowInstance, Task.instance_id == WorkflowInstance.id)
                .where(
                    WorkflowInstance.subject_type == WorkflowSubjectType.DOC_ACK,
                    WorkflowInstance.subject_id == doc_uuid,
                    Task.assignee_user_id == user_id,
                    Task.state == TaskState.PENDING,
                )
            )
        ).scalar_one()


async def _run_sweep(document_id: uuid.UUID | None = None) -> dict[str, int]:
    from easysynq_api.services.ack.sweep import sweep_acks

    async with get_sessionmaker()() as session:
        return await sweep_acks(session, document_id=document_id)
```

(Adjust the authz model import paths to the real modules — grep `class PermissionOverride` under `db/models/` when writing; `s5_helpers.py:17-31` has the exact import lines to copy.)

- [ ] **Step 2: The full-loop test:**

```python
async def test_full_loop_distribute_release_mint_ack_coverage(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    app_under_test: object,
) -> None:
    """distribute(user target, flag on) → release MAJOR → sweep mints Sam's task → Sam
    acknowledges → ack row + DOCUMENT_ACKNOWLEDGED + coverage 1/1; re-sweep no-ops."""
    from easysynq_api.db.models._audit_enums import AuditObjectType, EventType
    from easysynq_api.db.models.acknowledgement import Acknowledgement
    from easysynq_api.db.models.audit_event import AuditEvent

    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    await grant_keys(subj.a, DISTRIBUTE_PERMS)
    sam_id = await grant_keys(subj.sam, ACK_PERMS)
    await s5.set_approver_release(await s5.default_org_id(), True)
    ha, hb, hs = (_auth(token_factory, x) for x in (subj.a, subj.b, subj.sam))
    type_id = await s5.type_id("SOP")

    # Release a MAJOR rev (the test_periodic_review _release_doc inline shape).
    doc = await _create(app_client, ha, type_id)
    did = doc["id"]
    doc_uuid = uuid.UUID(did)
    await app_client.post(f"/api/v1/documents/{did}/checkout", headers=ha)
    sha = await _upload(app_client, ha, did, f"ack-loop-{subj.a}".encode())
    ci = await _checkin(app_client, ha, did, sha, change_reason="initial",
                        change_significance="MAJOR")
    assert ci.status_code == 201, ci.text
    await _map_clause(app_client, ha, did)
    # Distribute BEFORE release: flag on + Sam as a direct user target.
    dist = await app_client.post(
        f"/api/v1/documents/{did}/distribution",
        headers=ha,
        json={"acknowledgement_required": True,
              "add_entries": [{"target_type": "user", "target_id": str(sam_id)}]},
    )
    assert dist.status_code == 200, dist.text
    sr = await app_client.post(f"/api/v1/documents/{did}/submit-review", headers=ha)
    assert sr.status_code == 200, sr.text
    task_id = await s5.task_for_doc(did)
    dec = await app_client.post(f"/api/v1/tasks/{task_id}/decision", headers=hb,
                                json={"outcome": "approve"})
    assert dec.status_code == 200, dec.text
    rel = await app_client.post(f"/api/v1/documents/{did}/release", headers=hb, json={})
    assert rel.status_code == 200, rel.text

    # The doc-scoped sweep (the release hook's Celery leg, driven directly here).
    result = await _run_sweep(doc_uuid)
    assert result["tasks_created"] == 1

    ack_task = await _ack_task_for(doc_uuid, sam_id)
    # Sam's inbox carries it (self-scoped /tasks).
    inbox = (await app_client.get("/api/v1/tasks", headers=hs)).json()
    assert any(t["id"] == str(ack_task) and t["type"] == "DOC_ACK" for t in inbox)

    r = await app_client.post(f"/api/v1/tasks/{ack_task}/decision", headers=hs,
                              json={"outcome": "acknowledge"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["current_state"] == "COMPLETED"
    assert body["acknowledgement_id"] is not None

    async with get_sessionmaker()() as s:
        acks = (
            (await s.execute(select(Acknowledgement).where(
                Acknowledgement.document_id == doc_uuid))).scalars().all()
        )
        assert len(acks) == 1
        assert acks[0].user_id == sam_id
        assert acks[0].created_reason.value == "release"
        audit = (
            (await s.execute(select(AuditEvent).where(
                AuditEvent.object_type == AuditObjectType.document,
                AuditEvent.object_id == doc_uuid,
                AuditEvent.event_type == EventType.DOCUMENT_ACKNOWLEDGED,
            ))).scalars().all()
        )
        assert len(audit) == 1
        assert audit[0].scope_ref is not None

    cov = (await app_client.get(f"/api/v1/documents/{did}/distribution", headers=ha)).json()
    assert cov["coverage"] == {"required": 1, "acknowledged": 1, "pending": 0, "overdue": 0}

    # Idempotency: a re-sweep mints nothing.
    again = await _run_sweep(doc_uuid)
    assert again["tasks_created"] == 0
```

- [ ] **Step 3: The remaining tests** — write each with the same machinery (full code in-file; compact descriptions here name the exact assertions, all run-scoped):

```python
async def test_r15_target_entry_catchup(...):
    """Release an ack-required doc with an org_role target ('Employee (Read-only)') and ZERO
    members → sweep mints 0. Then s5.grant_role(subj.sam, 'Employee (Read-only)') + grant_keys
    ACK_PERMS → the ORG-WIDE sweep (document_id=None) mints Sam's task with
    created_reason='target_entry' (read the instance context). Assert exclusion: Sam acks, a
    SECOND org-wide sweep mints nothing for him."""

async def test_minor_release_carries_forward_major_rearms(...):
    """Sam acks Rev 1.0 (MAJOR). Re-release MINOR (checkout→upload new bytes→_checkin(...,
    change_significance='MINOR')→submit→approve→release) → doc-scoped sweep: tasks_created == 0
    AND coverage stays acknowledged=1 (carry-forward). Re-release MAJOR → sweep: Sam's old
    satisfaction lapses → tasks_created == 1, coverage acknowledged=0/pending=1; the old
    acknowledgement row still exists (evidence is never touched)."""

async def test_major_release_cancels_stale_pinned_task(...):
    """Mint Sam's task on Rev 1.0 (un-acked). Release MAJOR Rev 2.0 → doc-scoped sweep:
    tasks_cancelled == 1 (the 1.0-pinned task → SKIPPED, instance CANCELLED) and
    tasks_created == 1 (a fresh 2.0-pinned task). Deciding the OLD task id now 409s
    ('Task not decidable' — bare-SKIPPED branch)."""

async def test_decide_authz_matrix(...):
    """(a) outsider (no membership) posts acknowledge → 404. (b) Sam WITHOUT the
    document.acknowledge override posts → 403 permission_denied (grant only membership: mint the
    task for Sam but skip grant_keys). (c) Sam WITH the key posts outcome='approve' → 422.
    (d) Idempotency-Key replay: two identical POSTs → both 200, same acknowledgement_id, exactly
    ONE Acknowledgement row. (e) flag flipped off mid-flight (direct DB write) → posting → 409
    ack_obligation_lapsed."""

async def test_left_audience_cancelled_by_sweep(...):
    """Mint via a direct user target; DELETE the entry via the API (assert 204 + a
    DISTRIBUTION_UPDATED audit row) → doc-scoped sweep → tasks_cancelled == 1; coverage
    required == 0."""

async def test_target_kind_422_and_unknown_target_404(...):
    """POST distribution with target_type='process' → 422 code target_kind_deferred; with
    target_type='user' and a random uuid → 404; with target_type='nonsense' → 422."""

async def test_acknowledgement_append_only_db_grant(...):
    """Direct UPDATE acknowledgement SET client_ip='x' via the app role's sessionmaker → expect
    a programming/permission error (the REVOKE proof; the app_under_test fixture connects
    non-owner — the conftest AC#6a precedent: pytest.raises on the execute)."""

async def test_matrix_gated_and_shaped(...):
    """GET /documents/{id}/acknowledgements as Sam (no document.distribute) → 403; as the
    distributor → rows [{user_id, display_name, status, acknowledged_at,
    acknowledged_revision_label, due_at}] with Sam 'pending' before, 'acknowledged' after."""

async def test_obsolete_cancels_obligations(...):
    """Mint Sam's task; obsolete the doc (the document.obsolete endpoint, granted via
    grant_lifecycle) → doc-scoped sweep → tasks_cancelled == 1 (Pass B: doc left Effective)."""
```

Implementation notes: `s5.type_id` exists in s5_helpers (used by test_periodic_review); for (b) in the authz matrix, mint Sam's task by adding him as a `user` target — task minting needs no key, only the act does. For the org-wide sweep tests, scope assertions to THIS doc's uuid (other tests' docs share the DB).

- [ ] **Step 4: Verify collection locally.** Run: `uv run pytest tests/integration/test_acknowledgements.py --collect-only -q`
Expected: all tests collected, zero import errors.

- [ ] **Step 5: Commit.**

```bash
git add apps/api/tests/integration/test_acknowledgements.py
git commit -m "test(s-ack-1): integration proofs - full loop, R15 catch-up, MINOR/MAJOR semantics, authz matrix, append-only grant"
```

---

### Task 10: Snapshot keys

**Files:**
- Modify: `apps/api/src/easysynq_api/services/vault/service.py` (`_snapshot` + the checkin call site)
- Test: extend `apps/api/tests/integration/test_acknowledgements.py`

- [ ] **Step 1: Extend `_snapshot`** — additive keys, uniform for all docs (never branch the shared snapshot — the S-rec-3 rule):

```python
def _snapshot(
    doc: DocumentedInformation,
    *,
    field_schema: dict[str, Any] | None = None,
    distribution: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
```

body addition after `"review_period_months": ...`:

```python
        # S-ack-1 (doc 04 §6.1): the version self-describes its audience/ack policy.
        "acknowledgement_required": doc.acknowledgement_required,
        "distribution": distribution or [],
```

- [ ] **Step 2: Feed the checkin call site** (service.py:~317). Before the `metadata_snapshot=_snapshot(doc)` construction, load and serialize the entries:

```python
    dist_rows = (
        await session.execute(
            select(DistributionEntry)
            .where(DistributionEntry.document_id == doc.id)
            .order_by(DistributionEntry.created_at)
        )
    ).scalars().all()
    dist_snap = [
        {"target_type": e.target_type.value, "target_id": str(e.target_id),
         "ack_required": e.ack_required}
        for e in dist_rows
    ]
```

and pass `distribution=dist_snap` into BOTH `_snapshot` call sites in service.py (ordinary checkin :317 and the form-schema checkin :558). The ingestion call site (`services/ingestion/commit.py:347`) passes nothing — an imported doc has no entries at commit time; the default `[]` is honest. Add the `DistributionEntry` import to service.py.

- [ ] **Step 3: Pin it.** Add to the integration file:

```python
async def test_snapshot_carries_ack_keys(...):
    """After distribute(flag on, one entry) → checkout → checkin: the NEW version's
    metadata_snapshot has acknowledgement_required=True and the one-entry distribution list
    (read the DocumentVersion row directly)."""
```

(`domain/diff/metadata.py` needs NO change — `SNAPSHOT_FIELDS` is an allowlist, the new keys are auto-excluded from the diff; revisiting is S-ack-2's call per the spec.)

- [ ] **Step 4: Static gate + collection check + commit.**

```bash
uv run ruff check . && uv run mypy --strict src
uv run pytest tests/integration/test_acknowledgements.py --collect-only -q
git add apps/api/src/easysynq_api/services/vault/service.py apps/api/tests/integration/test_acknowledgements.py
git commit -m "feat(s-ack-1): freeze acknowledgement_required + distribution into metadata_snapshot"
```

---

### Task 11: Contracts

**Files:**
- Modify: `packages/contracts/openapi.yaml`

- [ ] **Step 1: Enum extensions.** Add `DOC_ACK` to `Task.type` enum (after `DCR_TRIAGE`) AND to `WorkflowInstance.subject_type` enum. Add `acknowledge` to `Decision.outcome` AND `DecisionResult.outcome` enums.

- [ ] **Step 2: DecisionResult enrichment fields** (additionalProperties:false — must be declared; the S-drift-1 nullable-tail precedent). After `signature_event_id`:

```yaml
        document_version_id: { type: [string, "null"], format: uuid }
        acknowledgement_id: { type: [string, "null"], format: uuid }
```

- [ ] **Step 3: The decideTask description sentence**, appended after the PERIODIC_REVIEW sentence:

```yaml
        For DOC_ACK subjects the only accepted outcome is acknowledge — membership 404-collapses,
        document.acknowledge is enforced at the document's scope (403), and the decision writes the
        immutable acknowledgement row + a DOCUMENT_ACKNOWLEDGED audit event (never a
        signature_event; 409 ack_superseded when a newer MAJOR replaced the task, 409
        ack_obligation_lapsed when the obligation no longer stands).
```

- [ ] **Step 4: The four new paths + schemas** (the `/documents/{document_id}/approval` template; gates named in summaries):

```yaml
  /documents/{document_id}/distribution:
    get:
      tags: [documents]
      operationId: getDocumentDistribution
      summary: "Distribution list + doc flag + counts-only ack coverage. Gated document.read."
      parameters:
        - { name: document_id, in: path, required: true, schema: { type: string, format: uuid } }
      responses:
        "200":
          description: "Entries, the acknowledgement_required flag, and the coverage rollup (null when no Effective version)."
          content:
            application/json:
              schema: { $ref: "#/components/schemas/DocumentDistribution" }
        "403": { $ref: "#/components/responses/ProblemResponse" }
        "404": { $ref: "#/components/responses/ProblemResponse" }
    post:
      tags: [documents]
      operationId: updateDocumentDistribution
      summary: "Add entries and/or set the doc-level ack flag (R42 document.distribute). 422 target_kind_deferred for process/folder."
      parameters:
        - { name: document_id, in: path, required: true, schema: { type: string, format: uuid } }
      requestBody:
        required: true
        content:
          application/json:
            schema: { $ref: "#/components/schemas/DistributionUpdate" }
      responses:
        "200":
          description: "The updated distribution representation."
          content:
            application/json:
              schema: { $ref: "#/components/schemas/DocumentDistribution" }
        "403": { $ref: "#/components/responses/ProblemResponse" }
        "404": { $ref: "#/components/responses/ProblemResponse" }
        "409":
          description: "Duplicate entry (UNIQUE document/target backstop)."
          content:
            application/problem+json:
              schema: { $ref: "#/components/schemas/Problem" }
        "422": { $ref: "#/components/responses/ProblemResponse" }
  /documents/{document_id}/distribution/{entry_id}:
    delete:
      tags: [documents]
      operationId: deleteDocumentDistributionEntry
      summary: "Remove a distribution entry (R42 document.distribute); the doc-scoped sweep cancels lapsed obligations."
      parameters:
        - { name: document_id, in: path, required: true, schema: { type: string, format: uuid } }
        - { name: entry_id, in: path, required: true, schema: { type: string, format: uuid } }
      responses:
        "204": { description: "Removed." }
        "403": { $ref: "#/components/responses/ProblemResponse" }
        "404": { $ref: "#/components/responses/ProblemResponse" }
  /documents/{document_id}/acknowledgements:
    get:
      tags: [documents]
      operationId: getDocumentAcknowledgements
      summary: "The named per-user ack status matrix for the current Effective version. Gated document.distribute (R42)."
      parameters:
        - { name: document_id, in: path, required: true, schema: { type: string, format: uuid } }
      responses:
        "200":
          description: "One row per current audience member."
          content:
            application/json:
              schema:
                type: array
                items: { $ref: "#/components/schemas/AckStatusRow" }
        "403": { $ref: "#/components/responses/ProblemResponse" }
        "404": { $ref: "#/components/responses/ProblemResponse" }
```

and under `components/schemas`:

```yaml
    DistributionEntry:
      type: object
      additionalProperties: false
      required: [id, target_type, target_id, ack_required, created_at]
      properties:
        id: { type: string, format: uuid }
        target_type: { type: string, enum: [user, org_role, process, folder] }
        target_id: { type: string, format: uuid }
        ack_required: { type: boolean }
        created_at: { type: string, format: date-time }
    DistributionUpdate:
      type: object
      additionalProperties: false
      properties:
        acknowledgement_required: { type: [boolean, "null"] }
        add_entries:
          type: array
          items:
            type: object
            additionalProperties: false
            required: [target_type, target_id]
            properties:
              target_type: { type: string, enum: [user, org_role, process, folder], description: "v1 accepts user/org_role; process/folder → 422 target_kind_deferred (R43)." }
              target_id: { type: string, format: uuid }
              ack_required: { type: boolean, default: true }
    AckCoverage:
      type: object
      additionalProperties: false
      required: [required, acknowledged, pending, overdue]
      properties:
        required: { type: integer }
        acknowledged: { type: integer }
        pending: { type: integer }
        overdue: { type: integer }
    DocumentDistribution:
      type: object
      additionalProperties: false
      required: [acknowledgement_required, entries, coverage]
      properties:
        acknowledgement_required: { type: boolean }
        entries:
          type: array
          items: { $ref: "#/components/schemas/DistributionEntry" }
        coverage:
          oneOf:
            - { $ref: "#/components/schemas/AckCoverage" }
            - { type: "null" }
    AckStatusRow:
      type: object
      additionalProperties: false
      required: [user_id, display_name, status]
      properties:
        user_id: { type: string, format: uuid }
        display_name: { type: [string, "null"] }
        status: { type: string, enum: [acknowledged, pending, overdue] }
        acknowledged_at: { type: [string, "null"], format: date-time }
        acknowledged_revision_label: { type: [string, "null"] }
        due_at: { type: [string, "null"], format: date-time }
```

- [ ] **Step 5: Lint.** Run the `/check-contracts` skill (redocly lint).
Expected: clean.

- [ ] **Step 6: Commit.**

```bash
git add packages/contracts/openapi.yaml
git commit -m "contracts(s-ack-1): DOC_ACK enums, acknowledge outcome, distribution/ack paths + schemas"
```

---

### Task 12: Register entries + doc back-propagation

**Files:**
- Modify: `docs/decisions-register.md` (R42 + R43 after R41; update the header line's "R1–R41")
- Modify: `docs/04-document-control-and-vault.md` (§8.2: the MAJOR-only note; §12: distribution now keyed)
- Modify: `docs/08-setup-and-onboarding.md` (§10.1 `acknowledge.read` → `document.acknowledge` — the missed R5 normalization)
- Modify: `docs/14-data-model.md` (§5.6 shapes as built; §7 task_type/subject_type members)
- Modify: `docs/15-api-design.md` (§8.5 split into the four endpoints; §8.8 non-sig-hook carve-out)
- Modify: `docs/16-roadmap.md` (mark the Distribution & acknowledgement row 🟡 backend-shipped)
- Modify: `CLAUDE.md` (Current status: S-ack-1; migration head 0048, next 0049)

- [ ] **Step 1: Write R42 + R43** from the spec §11 verbatim decisions (R42: `document.distribute`, third R38-additive key, catalog 99→100, seeded QMS Owner, riding manage_metadata rejected per the R41 reasoning, resolves doc 15 §8.5's dangling reference. R43: MAJOR-only re-ack + the §3 carry-forward boundary INCLUDING the no-MAJOR lowest-seq fallback, superseding doc 04 §8.2's blanket re-trigger; ack = own append-only table + audit event, never a signature_event; engine-task mechanism via additive DOC_ACK enums; enum-4-accept-2; the §10 deferral list — Remind/§6.3 report/process+folder resolution/bulk re-ack/org config flag/delegation carve-out/ack retention class).

- [ ] **Step 2: The section-doc edits** — each a surgical note citing the register entry (the established "reconciled per Decisions Register R43" footnote style), never a rewrite.

- [ ] **Step 3: Commit.**

```bash
git add docs CLAUDE.md
git commit -m "docs(s-ack-1): R42 document.distribute + R43 acknowledgements model + back-propagation"
```

---

### Task 13: Final gates, diff-critic, PR

- [ ] **Step 1: Full local gates.** Run the `/check-api` skill (ruff + format + mypy-strict + unit — expect ONLY the known 17 Windows baseline failures in the 3 known files, nothing in ack files), `/check-migrations`, `/check-contracts`. `/check-web` is untouched by this slice but run it once (cheap insurance against accidental drift).

- [ ] **Step 2: Unit suite spot-check.** Run: `uv run pytest tests/unit/test_ack_rules.py tests/unit/test_ack_task_registration.py -v`
Expected: PASS.

- [ ] **Step 3: diff-critic.** Run the `diff-critic` agent on the full branch diff (`Agent` tool, `subagent_type: diff-critic`). Fix confirmed findings; re-run gates.

- [ ] **Step 4: Push + CI.** `git push -u origin feat/s-ack-1`; watch all five required checks (the `integration` job runs the new suite for real).

- [ ] **Step 5: Live smoke** (the backend-live-smoke mechanics memory): rebuild migrate/api/worker/beat (`docker compose … up -d --build`), confirm the 0048 migrate leg, then drive the loop service-side via the worker-exec heredoc (distribute → release → `sweep_acks` → acknowledge via HTTP as the demo row with `document.acknowledge` + `document.distribute` SYSTEM overrides on the LIVE subject's app_user row — the S-web-8 JIT-row trap) and verify coverage counts + the audit rows.

- [ ] **Step 6: Open the PR** via the `/pr` skill — title `feat(s-ack-1): acknowledgements backend engine — distribution, R43 obligations sweep, DOC_ACK decide leg (mig 0048)`, body summarizing the spec §0 owner decisions + R42/R43, ending with the standard generated-with footer.

---

## Self-review checklist (run after writing, fixed inline)

- **Spec coverage:** §2 schema → Tasks 1–2; §3 rule → Task 3; §4 sweep/triggers → Tasks 5–6; §5 decide → Task 7; §6 endpoints/coverage → Tasks 4+8; §7 events → Tasks 1/7/8; §8 tests → Tasks 3/5/9/10; §11 register → Task 12. The §2 snapshot fold → Task 10. No §-item unowned.
- **Known reconciliations** are declared in the header (one-session sweep, no-MAJOR fallback, role-id resolution, inline force-terminate) — implementers must not "fix" them back toward the spec's sketch without an owner ask.
- **Type consistency:** `plan_obligations(audience, satisfied, open_tasks: Mapping[user, pinned_seq], last_major)` is used identically in Task 3 (definition), Task 5 (sweep call); `sweep_acks(session, *, document_id)` matches the Celery wrapper and test helper; `decide_doc_ack`'s signature matches the Task 7 dispatch call; `coverage_counts` return shape matches the Task 8 route and the Task 9 assertion; `_distribution_entry` keys match the `DistributionEntry` contract schema.
- **Verify-before-trust steps embedded:** `_emit` signature check (Task 8), authz model import paths (Task 9), `UserStatus` import path (Task 4) — each flagged at its use site.
