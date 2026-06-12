# S-mr-1 Management Review Backend (clause 9.3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Management Review (ISO 9001 clause 9.3) backend so one released Management Review flips the 9.3 ★ compliance-checklist node COVERED — the last unbuilt mandatory-★ family.

**Architecture:** The Management Review aggregate is a `kind=DOCUMENT` shared-PK subtype of `documented_information` (type `MR`) — the Quality-Objective (S-obj-3) recipe verbatim. The convened review's minutes (auto-compiled 9.3.2 inputs as-of + the 9.3.3 decisions/outputs) freeze into the version snapshot at submit (rfc8785 JSON WORM source + `metadata_snapshot.mgmt_review_minutes`); the document rides the unchanged submit→approve→release machinery, and release sets `current_effective_version_id` → the ★ flips with zero checklist code. Outputs spawn `MR_ACTION` tasks (on a `MGMT_REVIEW` workflow instance); the review closes only when those actions close (the `_audit_close_gate` pattern); a daily Beat sweep mints the next Scheduled review.

**Tech Stack:** Python 3.12 · FastAPI · SQLAlchemy 2.x async · Alembic · PostgreSQL 16 · Celery/Beat · `rfc8785` (JCS canonical JSON) · pytest (unit + testcontainers integration). Toolchain: `uv` (managed Python 3.12 at `~/.local/bin/uv`). Verify with `/check-api`, `/check-migrations`, `/check-contracts`.

**Spec:** `docs/superpowers/specs/2026-06-12-s-mr-1-management-review-backend-design.md` (owner-approved). Read it before starting.

**Migration head:** current `0049_quality_objectives` → this slice adds `0050_management_review`.

---

## Conventions that bind every task (read once)

- **Migrations live at repo-root `migrations/versions/`** (NOT under `apps/api`). API source is `apps/api/src/easysynq_api/`. Run API commands with `uv` from `apps/api`.
- **Every FK is `ondelete="RESTRICT"` and explicitly named** (`name="fk_<table>_<col>_<target>"`) — the convention default can exceed PG's 63-char limit.
- **Every `op.create_index` in the migration MUST be mirrored by an `Index(...)` in the ORM `__table_args__`** or `alembic check` phantom-DROPs it → migrations CI red (invisible to CI-from-source; only the round-trip catches it).
- **Every new model module MUST be imported in `db/models/__init__.py` + added to `__all__`** — that file is the sole place `Base.metadata` is populated.
- **New PG enums:** declare in an ORM enums module with `create_type=False` + a `<NAME>_VALUES = tuple(_vals(<Enum>))` constant; the migration sources its `CREATE TYPE` tuple from that constant (never a hand-retyped list).
- **Commit after each task** with a `feat(s-mr-1): …` message. Branch is `feat/s-mr-1-management-review` (already created).
- **Decimals serialize as `str()` everywhere** (never float). Dates/datetimes `.isoformat()`. UUIDs `str()`. Enums `.value`.
- After the data-layer phase, run `/check-migrations` (round-trip up↔down↔`alembic check` on a throwaway PG16). After each API phase, run `/check-api`. Before the PR, run the `diff-critic` agent.

---

## File structure

**New model/enum modules** (`apps/api/src/easysynq_api/db/models/`):
- `_mgmt_review_enums.py` — `ReviewInputType`, `ReviewOutputType`, `ManagementReviewCloseState` + their `_VALUES` + `_enum` bindings.
- `management_review.py` — the `kind=DOCUMENT` shared-PK subtype.
- `review_input.py`, `review_output.py` — the child tables.

**New domain (pure, no I/O)** (`apps/api/src/easysynq_api/domain/mgmt_review/`):
- `minutes.py` — `build_minutes(...)` (the JSON-safe dict builder).
- `inputs.py` — the read-shape→summary projections.
- `close_gate.py` — `output_blocks_close(...)` predicate.

**New services** (`apps/api/src/easysynq_api/services/mgmt_review/`):
- `repository.py` — reads (get/list/close-gate rows/open-MR check).
- `compile.py` — `compile_inputs(...)` (owner-grant-gated).
- `service.py` — create / outputs CRUD / submit / release / close / the close gate / `_conflict`/`_not_found`/`_validation_error`.
- `cadence.py` — `sweep_mgmt_reviews(...)`.
- `spawn.py` — `spawn_mr_actions(...)` (at release).
- `decide.py` — `decide_mr_task(...)` (the new /tasks dispatch leg).

**New task wrapper:** `apps/api/src/easysynq_api/tasks/mgmt_review.py`.
**New router:** `apps/api/src/easysynq_api/api/mgmt_review.py`.
**New migration:** `migrations/versions/0050_management_review.py`.

**Modified:** `db/models/__init__.py` · `db/models/_audit_enums.py` · `db/models/system_config.py` · `services/vault/service.py` (the freeze + `_snapshot` fold) · `services/common/pg_locks.py` · `tasks/__init__.py` · `tasks/app.py` · `api/workflow.py` (the decide leg) · `main.py` · `packages/contracts/openapi.yaml`.

**New tests** (`apps/api/tests/`): `unit/test_mgmt_review_minutes.py` · `unit/test_mgmt_review_close_gate.py` · `unit/test_mgmt_review_inputs.py` · `unit/test_mgmt_review_cadence.py` · `unit/test_mgmt_review_task_registration.py` · `integration/test_mgmt_review.py`.

---

## Phase 1 — Enums, ORM models, migration 0050 (the data layer)

### Task 1: Enum module

**Files:**
- Create: `apps/api/src/easysynq_api/db/models/_mgmt_review_enums.py`

- [ ] **Step 1: Write the module** (mirrors `_objective_enums.py:1-27`)

```python
"""Management Review enums (S-mr-1, clause 9.3). ``create_type=False`` — the 0050 migration owns
CREATE TYPE; the migration sources its CREATE-TYPE tuple from the ``*_VALUES`` constants (the 0010 rule)."""

from __future__ import annotations

import enum

from sqlalchemy import Enum as SAEnum


class ReviewInputType(enum.Enum):
    PRIOR_ACTIONS = "PRIOR_ACTIONS"                    # 9.3.2(a)
    CONTEXT_CHANGES = "CONTEXT_CHANGES"                # 9.3.2(b) — gap (no source)
    CUSTOMER_SATISFACTION = "CUSTOMER_SATISFACTION"    # 9.3.2(c1) — gap
    OBJECTIVES_STATUS = "OBJECTIVES_STATUS"            # 9.3.2(c2)
    PROCESS_PERFORMANCE = "PROCESS_PERFORMANCE"        # 9.3.2(c3)
    NONCONFORMITIES_CAPA = "NONCONFORMITIES_CAPA"      # 9.3.2(c4)
    MONITORING_RESULTS = "MONITORING_RESULTS"          # 9.3.2(c5)
    AUDIT_RESULTS = "AUDIT_RESULTS"                    # 9.3.2(c6)
    SUPPLIER_PERFORMANCE = "SUPPLIER_PERFORMANCE"      # 9.3.2(c7) — gap
    RESOURCE_ADEQUACY = "RESOURCE_ADEQUACY"            # 9.3.2(d) — gap
    RISK_OPPORTUNITY_ACTIONS = "RISK_OPPORTUNITY_ACTIONS"  # 9.3.2(e) — gap
    IMPROVEMENT_OPPORTUNITIES = "IMPROVEMENT_OPPORTUNITIES"  # 9.3.2(f) — gap


class ReviewOutputType(enum.Enum):
    DECISION = "DECISION"        # recorded, untracked
    ACTION = "ACTION"            # owner + due → spawns an MR_ACTION task
    IMPROVEMENT = "IMPROVEMENT"  # reserved — tagged for the deferred initiative family


class ManagementReviewCloseState(enum.Enum):
    ActionsTracked = "ActionsTracked"  # set at release; output actions in flight
    Closed = "Closed"                  # all actions done (the close gate passed)


def _vals(e: type[enum.Enum]) -> list[str]:
    return [m.value for m in e]


REVIEW_INPUT_TYPE_VALUES = tuple(_vals(ReviewInputType))
REVIEW_OUTPUT_TYPE_VALUES = tuple(_vals(ReviewOutputType))
MANAGEMENT_REVIEW_CLOSE_STATE_VALUES = tuple(_vals(ManagementReviewCloseState))

review_input_type_enum = SAEnum(
    ReviewInputType, name="review_input_type", values_callable=_vals, create_type=False
)
review_output_type_enum = SAEnum(
    ReviewOutputType, name="review_output_type", values_callable=_vals, create_type=False
)
management_review_close_state_enum = SAEnum(
    ManagementReviewCloseState,
    name="management_review_close_state",
    values_callable=_vals,
    create_type=False,
)
```

- [ ] **Step 2: Verify it imports**

Run: `cd apps/api && uv run python -c "from easysynq_api.db.models._mgmt_review_enums import REVIEW_INPUT_TYPE_VALUES; print(REVIEW_INPUT_TYPE_VALUES)"`
Expected: prints the 12-tuple.

- [ ] **Step 3: Commit**

```bash
git add apps/api/src/easysynq_api/db/models/_mgmt_review_enums.py
git commit -m "feat(s-mr-1): management-review enums (input/output type + close_state)"
```

### Task 2: The `management_review` subtype model

**Files:**
- Create: `apps/api/src/easysynq_api/db/models/management_review.py`

- [ ] **Step 1: Write the model** (mirrors `quality_objective.py` — shared-PK `kind=DOCUMENT` subtype; `id` has NO default)

```python
"""management_review — a kind=DOCUMENT shared-PK subtype of documented_information (type 'MR').

management_review.id IS the documented_information.id (the quality_objective/form_template precedent).
The minutes (compiled inputs + decisions) are frozen into document_version.metadata_snapshot at submit
(NOT a column here); review_date/attendees/period_label/close_state are mutable operational state."""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._mgmt_review_enums import ManagementReviewCloseState, management_review_close_state_enum


class ManagementReview(Base):
    __tablename__ = "management_review"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "documented_information.id",
            ondelete="RESTRICT",
            name="fk_management_review_id_documented_information",
        ),
        primary_key=True,
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT", name="fk_management_review_org_id_organization"),
        nullable=False,
    )
    period_label: Mapped[str | None] = mapped_column(nullable=True)
    review_date: Mapped[datetime.date | None] = mapped_column(nullable=True)
    attendees: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    close_state: Mapped[ManagementReviewCloseState | None] = mapped_column(
        management_review_close_state_enum, nullable=True
    )
    closed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=func.now()
    )
```

> Note: `attendees` is a JSONB list-of-objects (the informational roster `[{name, role?, user_id?}]`). `text` is imported for parity with later tasks even if unused here; remove if `ruff` flags it. `period_label`/`review_date` use bare `Mapped[str|None]`/`Mapped[date|None]` (SQLAlchemy infers `Text`/`Date`); if `ruff`/mypy prefers explicit types, use `mapped_column(Text, ...)`/`mapped_column(Date, ...)` with the imports — match whatever `quality_objective.py` does for its `Text`/`Date` columns.

- [ ] **Step 2: Commit** (model imports verified after `__init__.py` registration in Task 6)

```bash
git add apps/api/src/easysynq_api/db/models/management_review.py
git commit -m "feat(s-mr-1): management_review kind=DOCUMENT subtype model"
```

### Task 3: The `review_input` child model

**Files:**
- Create: `apps/api/src/easysynq_api/db/models/review_input.py`

- [ ] **Step 1: Write the model** (plain mutable child — `id` HAS `default=uuid.uuid4`; index the parent FK)

```python
"""review_input — the compiled 9.3.2 input rows for a Management Review (mutable working projection in
Draft; frozen by the version snapshot at submit). NOT REVOKE-protected — the snapshot is the WORM authority."""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._mgmt_review_enums import ReviewInputType, review_input_type_enum


class ReviewInput(Base):
    __tablename__ = "review_input"
    __table_args__ = (Index("ix_review_input_management_review_id", "management_review_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT", name="fk_review_input_org_id_organization"),
        nullable=False,
    )
    management_review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "management_review.id",
            ondelete="RESTRICT",
            name="fk_review_input_management_review_id_management_review",
        ),
        nullable=False,
    )
    input_type: Mapped[ReviewInputType] = mapped_column(review_input_type_enum, nullable=False)
    available: Mapped[bool] = mapped_column(nullable=False)
    source_ref: Mapped[dict] = mapped_column(JSONB, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

- [ ] **Step 2: Commit**

```bash
git add apps/api/src/easysynq_api/db/models/review_input.py
git commit -m "feat(s-mr-1): review_input child model"
```

### Task 4: The `review_output` child model

**Files:**
- Create: `apps/api/src/easysynq_api/db/models/review_output.py`

- [ ] **Step 1: Write the model** (decision content frozen by the snapshot; `spawned_*` are reserved-null operational columns — NO FK on `spawned_capa_id`/`spawned_initiative_id`, the `capa.origin_finding_id` pattern)

```python
"""review_output — the 9.3.3 decisions/actions of a Management Review. Decision content (type/description/
owner/due) freezes into the version snapshot at submit; spawned_* + tracking columns mutate post-release."""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import Date, DateTime, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._mgmt_review_enums import ReviewOutputType, review_output_type_enum


class ReviewOutput(Base):
    __tablename__ = "review_output"
    __table_args__ = (Index("ix_review_output_management_review_id", "management_review_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT", name="fk_review_output_org_id_organization"),
        nullable=False,
    )
    management_review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "management_review.id",
            ondelete="RESTRICT",
            name="fk_review_output_management_review_id_management_review",
        ),
        nullable=False,
    )
    output_type: Mapped[ReviewOutputType] = mapped_column(review_output_type_enum, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app_user.id", ondelete="RESTRICT", name="fk_review_output_owner_user_id_app_user"),
        nullable=True,
    )
    due_date: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    spawned_task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("task.id", ondelete="RESTRICT", name="fk_review_output_spawned_task_id_task"),
        nullable=True,
    )
    spawned_capa_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)  # reserved-null, no FK
    spawned_initiative_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)  # reserved-null
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=func.now()
    )
```

- [ ] **Step 2: Commit**

```bash
git add apps/api/src/easysynq_api/db/models/review_output.py
git commit -m "feat(s-mr-1): review_output child model"
```

### Task 5: Add MGMT_REVIEW_* EventType members

**Files:**
- Modify: `apps/api/src/easysynq_api/db/models/_audit_enums.py` (end of `class EventType`, ~line 367-370 region)

- [ ] **Step 1: Add the members** (mirrors the S-obj-1 `OBJECTIVE_*` block; `EVENT_TYPE_VALUES` auto-derives)

```python
    # S-mr-1 — clause 9.3 Management Review acts (object_type='document', R39 reuse).
    # Added via ALTER TYPE event_type ADD VALUE in 0050 (the additive pattern; a from-scratch
    # ``upgrade head`` rebuilds the type from EVENT_TYPE_VALUES, so the members live here too).
    MGMT_REVIEW_INPUTS_COMPILED = "MGMT_REVIEW_INPUTS_COMPILED"
    MGMT_REVIEW_OUTPUT_RECORDED = "MGMT_REVIEW_OUTPUT_RECORDED"
    MGMT_REVIEW_ACTION_SPAWNED = "MGMT_REVIEW_ACTION_SPAWNED"
    MGMT_REVIEW_CLOSED = "MGMT_REVIEW_CLOSED"
```

- [ ] **Step 2: Verify the values flow through**

Run: `cd apps/api && uv run python -c "from easysynq_api.db.models._audit_enums import EVENT_TYPE_VALUES; assert 'MGMT_REVIEW_CLOSED' in EVENT_TYPE_VALUES; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add apps/api/src/easysynq_api/db/models/_audit_enums.py
git commit -m "feat(s-mr-1): MGMT_REVIEW_* event_type members"
```

### Task 6: Add the cadence columns to SystemConfig

**Files:**
- Modify: `apps/api/src/easysynq_api/db/models/system_config.py`

- [ ] **Step 1: Read the existing model** to match its column style (e.g. `audit_chain_lag_alarm_seconds` Integer + `server_default`).

Run: `cd apps/api && uv run python -c "import easysynq_api.db.models.system_config as m; print([c.name for c in m.SystemConfig.__table__.columns])"`

- [ ] **Step 2: Add two columns** (after the existing settings columns)

```python
    # S-mr-1: clause-9.3 management-review cadence (coded default; org-tunable later, additive).
    mgmt_review_cadence_months: Mapped[int] = mapped_column(
        Integer, server_default=text("12"), nullable=False
    )
    mgmt_review_owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app_user.id", ondelete="RESTRICT", name="fk_system_config_mgmt_review_owner_user_id_app_user"),
        nullable=True,
    )
```

> Ensure `Integer`, `text`, `ForeignKey`, `UUID`, `uuid` are imported in `system_config.py` (add any missing). `mgmt_review_owner_user_id` is the owner the cadence sweep assigns the minted Draft MR to; NULL → the sweep degrades to a logged no-op (it can't create an ownerless document).

- [ ] **Step 3: Commit**

```bash
git add apps/api/src/easysynq_api/db/models/system_config.py
git commit -m "feat(s-mr-1): system_config mgmt-review cadence columns"
```

### Task 7: Register models + enums in `db/models/__init__.py`

**Files:**
- Modify: `apps/api/src/easysynq_api/db/models/__init__.py`

- [ ] **Step 1: Add the enum imports** (in the leading `from ._*_enums import (...)` block, alphabetically)

```python
from ._mgmt_review_enums import (
    ManagementReviewCloseState,
    ReviewInputType,
    ReviewOutputType,
)
```

- [ ] **Step 2: Add the model imports** (alphabetical `from .<module> import <Class>` block — `management_review` near `.kpi_measurement`; `review_input`/`review_output` near `.retention_policy`/before `.role`)

```python
from .management_review import ManagementReview
from .review_input import ReviewInput
from .review_output import ReviewOutput
```

- [ ] **Step 3: Add to `__all__`** (alphabetical string list — `"ManagementReview"`, `"ManagementReviewCloseState"`, `"ReviewInput"`, `"ReviewInputType"`, `"ReviewOutput"`, `"ReviewOutputType"`). Verify exact alpha neighbours when editing.

- [ ] **Step 4: Verify metadata is populated**

Run: `cd apps/api && uv run python -c "from easysynq_api.db.models import Base, ManagementReview, ReviewInput, ReviewOutput; assert 'management_review' in Base.metadata.tables; print('ok')"`
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/easysynq_api/db/models/__init__.py
git commit -m "feat(s-mr-1): register management-review models + enums"
```

### Task 8: Migration 0050 — tables, enums, document_type + workflow seeds, cadence columns

**Files:**
- Create: `migrations/versions/0050_management_review.py`

- [ ] **Step 1: Write the migration.** Header: `revision = "0050_management_review"`, `down_revision = "0049_quality_objectives"`. Structure the upgrade in this order: (1) `ALTER TYPE event_type ADD VALUE` for the four MGMT_REVIEW_* members (in `autocommit_block()`, sourced from `EVENT_TYPE_VALUES` — see below); (2) `CREATE TYPE` the three new enums from their `*_VALUES`; (3) `create_table` the three tables + `create_index` each parent-FK index; (4) `add_column` the two `system_config` columns; (5) the `document_type` seed (`('MR','Management Review','L1_POLICY',False)`) with the resilient org lookup; (6) the `management_review` `workflow_definition` + one `workflow_stage` seed.

```python
"""Management Review family (clause 9.3): management_review + review_input + review_output, new enums,
the MR document_type + workflow_definition seeds, and the system_config cadence columns. (S-mr-1)"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

from easysynq_api.db.models._audit_enums import EVENT_TYPE_VALUES  # noqa: F401  (proves the members exist)
from easysynq_api.db.models._mgmt_review_enums import (
    MANAGEMENT_REVIEW_CLOSE_STATE_VALUES,
    REVIEW_INPUT_TYPE_VALUES,
    REVIEW_OUTPUT_TYPE_VALUES,
)

revision: str = "0050_management_review"
down_revision: str | None = "0049_quality_objectives"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "easysynq_app"
_NEW_EVENT_TYPES = (
    "MGMT_REVIEW_INPUTS_COMPILED",
    "MGMT_REVIEW_OUTPUT_RECORDED",
    "MGMT_REVIEW_ACTION_SPAWNED",
    "MGMT_REVIEW_CLOSED",
)
_MR_TYPE = ("MR", "Management Review", "L1_POLICY", False)  # (code, name, document_level, is_singleton)
_DEF_KEY = "management_review"


def _org_id(bind) -> object:
    org_id = bind.execute(
        sa.text("SELECT id FROM organization WHERE short_code = 'DEFAULT'")
    ).scalar_one_or_none()
    if org_id is None:
        org_id = bind.execute(sa.text("SELECT id FROM organization")).scalar_one()
    return org_id


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for value in _NEW_EVENT_TYPES:
            op.execute(f"ALTER TYPE event_type ADD VALUE IF NOT EXISTS '{value}'")

    bind = op.get_bind()
    postgresql.ENUM(*REVIEW_INPUT_TYPE_VALUES, name="review_input_type").create(bind, checkfirst=True)
    postgresql.ENUM(*REVIEW_OUTPUT_TYPE_VALUES, name="review_output_type").create(bind, checkfirst=True)
    postgresql.ENUM(*MANAGEMENT_REVIEW_CLOSE_STATE_VALUES, name="management_review_close_state").create(
        bind, checkfirst=True
    )
    review_input_type = postgresql.ENUM(name="review_input_type", create_type=False)
    review_output_type = postgresql.ENUM(name="review_output_type", create_type=False)
    close_state = postgresql.ENUM(name="management_review_close_state", create_type=False)

    op.create_table(
        "management_review",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("period_label", sa.Text(), nullable=True),
        sa.Column("review_date", sa.Date(), nullable=True),
        sa.Column("attendees", postgresql.JSONB(), nullable=True),
        sa.Column("close_state", close_state, nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_management_review"),
        sa.ForeignKeyConstraint(["id"], ["documented_information.id"],
                                name="fk_management_review_id_documented_information", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"],
                                name="fk_management_review_org_id_organization", ondelete="RESTRICT"),
    )
    op.create_table(
        "review_input",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("management_review_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("input_type", review_input_type, nullable=False),
        sa.Column("available", sa.Boolean(), nullable=False),
        sa.Column("source_ref", postgresql.JSONB(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_review_input"),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"],
                                name="fk_review_input_org_id_organization", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["management_review_id"], ["management_review.id"],
                                name="fk_review_input_management_review_id_management_review", ondelete="RESTRICT"),
    )
    op.create_index("ix_review_input_management_review_id", "review_input", ["management_review_id"])
    op.create_table(
        "review_output",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("management_review_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("output_type", review_output_type, nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("spawned_task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("spawned_capa_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("spawned_initiative_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_review_output"),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"],
                                name="fk_review_output_org_id_organization", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["management_review_id"], ["management_review.id"],
                                name="fk_review_output_management_review_id_management_review", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["app_user.id"],
                                name="fk_review_output_owner_user_id_app_user", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["spawned_task_id"], ["task.id"],
                                name="fk_review_output_spawned_task_id_task", ondelete="RESTRICT"),
    )
    op.create_index("ix_review_output_management_review_id", "review_output", ["management_review_id"])

    op.add_column("system_config", sa.Column("mgmt_review_cadence_months", sa.Integer(),
                                             server_default=sa.text("12"), nullable=False))
    op.add_column("system_config", sa.Column("mgmt_review_owner_user_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_system_config_mgmt_review_owner_user_id_app_user", "system_config",
                          "app_user", ["mgmt_review_owner_user_id"], ["id"], ondelete="RESTRICT")

    org_id = _org_id(bind)
    document_type_t = sa.table(
        "document_type",
        sa.column("org_id", postgresql.UUID(as_uuid=True)),
        sa.column("code", sa.Text),
        sa.column("name", sa.Text),
        sa.column("document_level", postgresql.ENUM(name="document_level", create_type=False)),
        sa.column("is_singleton", sa.Boolean),
    )
    code, name, level, singleton = _MR_TYPE
    bind.execute(
        pg_insert(document_type_t)
        .values(org_id=org_id, code=code, name=name, document_level=level, is_singleton=singleton)
        .on_conflict_do_nothing(index_elements=["org_id", "code"])
    )

    # The management_review workflow_definition — a CONTAINER for the MGMT_REVIEW instance the
    # MR_INPUT/MR_ACTION tasks hang off (tasks are added via the S5 direct-insert pattern, not the engine).
    # Mirror the 0045 periodic_review seed shape EXACTLY (definition insert + re-read id + stage insert).
    # <Implementer: copy 0045_periodic_review.py's _seed_workflow body verbatim, swapping _DEF_KEY,
    #  subject_type='MGMT_REVIEW', a single stage key='prepare' with assignees={"context_users":"owner_user_id",
    #  "task_type":"MR_INPUT","action_expected":"prepare"}, mode='PARALLEL', quorum='ANY', transitions=[],
    #  signature=None (no sign-off on this container instance), stages={"entry":"prepare"}, default_sla=None.>


def downgrade() -> None:
    bind = op.get_bind()
    # workflow seed (guarded by live instances), then document_type (guarded by live docs), then tables.
    has_instances = bind.execute(
        sa.text("SELECT EXISTS(SELECT 1 FROM workflow_instance wi JOIN workflow_definition wd "
                "ON wi.definition_id = wd.id WHERE wd.key = :k)"),
        {"k": _DEF_KEY},
    ).scalar()
    if not has_instances:
        bind.execute(sa.text("DELETE FROM workflow_stage WHERE definition_id IN "
                             "(SELECT id FROM workflow_definition WHERE key = :k)"), {"k": _DEF_KEY})
        bind.execute(sa.text("DELETE FROM workflow_definition WHERE key = :k"), {"k": _DEF_KEY})
    bind.execute(
        sa.text("DELETE FROM document_type dt WHERE dt.code = :c AND NOT EXISTS "
                "(SELECT 1 FROM documented_information di WHERE di.document_type_id = dt.id)"),
        {"c": _MR_TYPE[0]},
    )
    op.drop_constraint("fk_system_config_mgmt_review_owner_user_id_app_user", "system_config", type_="foreignkey")
    op.drop_column("system_config", "mgmt_review_owner_user_id")
    op.drop_column("system_config", "mgmt_review_cadence_months")
    op.drop_index("ix_review_output_management_review_id", table_name="review_output")
    op.drop_table("review_output")
    op.drop_index("ix_review_input_management_review_id", table_name="review_input")
    op.drop_table("review_input")
    op.drop_table("management_review")
    op.execute("DROP TYPE IF EXISTS management_review_close_state")
    op.execute("DROP TYPE IF EXISTS review_output_type")
    op.execute("DROP TYPE IF EXISTS review_input_type")
    # event_type ADD VALUEs are irreversible in PG → no-op (the 0011/0048 precedent).
```

> ⚠ Before writing the workflow seed, **Read `migrations/versions/0045_periodic_review.py` in full** and copy its `workflow_definition`/`workflow_stage` insert body verbatim into the marked spot (the `<Implementer: …>` comment), adapting the four values noted. Do **not** invent the insert shape.

- [ ] **Step 2: Round-trip the migration** (the load-bearing check — catches the phantom-DROP + populated-downgrade traps CI-from-source is blind to)

Run: `/check-migrations`
Expected: up↔down↔`alembic check` clean on a throwaway PG16. If `alembic check` reports a phantom-DROP, a `create_index` is missing its ORM `Index(...)` mirror (Tasks 3/4) — fix and re-run.

- [ ] **Step 3: Commit**

```bash
git add migrations/versions/0050_management_review.py
git commit -m "feat(s-mr-1): migration 0050 — MR tables, enums, document_type + workflow seeds, cadence columns"
```

---

## Phase 2 — The minutes freeze (`build_minutes` + `checkin_mgmt_review_minutes` + the `_snapshot` fold)

### Task 9: `build_minutes` (pure JSON-safe dict builder)

**Files:**
- Create: `apps/api/src/easysynq_api/domain/mgmt_review/__init__.py` (empty)
- Create: `apps/api/src/easysynq_api/domain/mgmt_review/minutes.py`
- Test: `apps/api/tests/unit/test_mgmt_review_minutes.py`

- [ ] **Step 1: Write the failing test** (every leaf must be a JSON-safe primitive so rfc8785 bytes are reproducible)

```python
import datetime
import uuid

import rfc8785
from easysynq_api.domain.mgmt_review.minutes import build_minutes


def test_build_minutes_is_json_safe_and_rfc8785_serializable() -> None:
    m = build_minutes(
        period_label="2026 Annual",
        review_date=datetime.date(2026, 6, 12),
        attendees=[{"name": "Mara", "role": "Quality Manager"}],
        inputs=[{"input_type": "AUDIT_RESULTS", "available": True, "summary": {"open": 2}}],
        outputs=[{"output_type": "ACTION", "description": "Tighten X", "owner_user_id": str(uuid.uuid4())}],
        compiled_at=datetime.datetime(2026, 6, 12, 9, 0, tzinfo=datetime.UTC),
    )
    assert m["review_date"] == "2026-06-12"
    assert m["compiled_at"].startswith("2026-06-12T09:00")
    # rfc8785 raises on non-JSON-safe leaves; this proves every leaf is a primitive:
    assert isinstance(rfc8785.dumps(m), bytes)
```

- [ ] **Step 2: Run it — fails** (`ModuleNotFoundError`)

Run: `cd apps/api && uv run pytest tests/unit/test_mgmt_review_minutes.py -v`

- [ ] **Step 3: Implement** (mirrors `domain/objectives/commitment.py:build_commitment` — dates `.isoformat()`, the caller pre-coerces nested leaves)

```python
"""build_minutes — the JSON-safe minutes dict for a Management Review's WORM source blob + snapshot.

Mirrors domain/objectives/commitment.build_commitment: every date/datetime → .isoformat(), every Decimal/
UUID inside inputs/outputs/attendees → str (the CALLER coerces nested leaves before passing them in), so
rfc8785.dumps produces exact, reproducible bytes (JCS sorts keys; non-JSON-safe leaves raise)."""

from __future__ import annotations

import datetime
from typing import Any


def build_minutes(
    *,
    period_label: str | None,
    review_date: datetime.date | None,
    attendees: list[dict[str, Any]] | None,
    inputs: list[dict[str, Any]],
    outputs: list[dict[str, Any]],
    compiled_at: datetime.datetime,
) -> dict[str, Any]:
    return {
        "period_label": period_label,
        "review_date": review_date.isoformat() if review_date is not None else None,
        "attendees": attendees or [],
        "inputs": inputs,
        "outputs": outputs,
        "compiled_at": compiled_at.isoformat(),
    }
```

- [ ] **Step 4: Run it — passes.** Run: `cd apps/api && uv run pytest tests/unit/test_mgmt_review_minutes.py -v`
- [ ] **Step 5: Commit**

```bash
git add apps/api/src/easysynq_api/domain/mgmt_review/ apps/api/tests/unit/test_mgmt_review_minutes.py
git commit -m "feat(s-mr-1): build_minutes (JSON-safe minutes dict)"
```

### Task 10: Extend `_snapshot` with the `mgmt_review_minutes` kwarg + add `checkin_mgmt_review_minutes`

**Files:**
- Modify: `apps/api/src/easysynq_api/services/vault/service.py`

- [ ] **Step 1: Extend `_snapshot`** (`service.py:94-128`) — add **one** optional kwarg + **one** trailing `if`:

```python
def _snapshot(
    doc: DocumentedInformation,
    *,
    field_schema: dict[str, Any] | None = None,
    distribution: list[dict[str, Any]] | None = None,
    objective_commitment: dict[str, Any] | None = None,
    mgmt_review_minutes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snap: dict[str, Any] = { ... }   # UNCHANGED base
    if field_schema is not None:
        snap["field_schema"] = field_schema
    if objective_commitment is not None:
        snap["objective_commitment"] = objective_commitment
    if mgmt_review_minutes is not None:
        snap["mgmt_review_minutes"] = mgmt_review_minutes
    return snap
```

- [ ] **Step 2: Add `checkin_mgmt_review_minutes`** adjacent to `checkin_objective_commitment` — copy that function (`service.py:654-748`) **verbatim**, substituting `minutes` for `commitment` and the snapshot kwarg. The body (rfc8785 → sha → get_blob dedup → put_staging_bytes(application/json) → finalize_worm → Blob upsert → flush → DocumentVersion(metadata_snapshot=_snapshot(doc, mgmt_review_minutes=minutes, distribution=dist_snap)) → flush → _emit("CHECKIN", …, version.id)) is **byte-identical** except the variable name and the snapshot kwarg. **NO preamble** — hash the bare `rfc8785.dumps(minutes)`. Flush before `_emit` (the uuid-PK-at-flush gotcha). Keep `content_type="application/json"` and `change_significance="MAJOR"`.

```python
async def checkin_mgmt_review_minutes(
    session, sink, actor, doc, *, minutes: dict[str, Any], change_reason: str,
    change_significance: str = "MAJOR",
) -> DocumentVersionModel:
    # ... identical to checkin_objective_commitment, with:
    payload = rfc8785.dumps(minutes)            # bare — NO preamble
    # ... metadata_snapshot=_snapshot(doc, mgmt_review_minutes=minutes, distribution=dist_snap) ...
    # ... flush BEFORE _emit(session, sink, "CHECKIN", actor, "document_version", version.id, identifier=doc.identifier, reason=...) ...
```

> Read `checkin_objective_commitment` in full first and reproduce its structure exactly (the INV-3 change_reason guard, the `ChangeSignificance` parse, the `if await repository.get_blob(...) is None:` dedup guard with the inner flush, the 500/423 guards). Do NOT commit inside this helper — it flushes; the caller owns the txn.

- [ ] **Step 3: Verify no objective/document snapshot regressed**

Run: `cd apps/api && uv run pytest tests/unit -k "snapshot or objective" -q` (and the vault unit tests). Expected: green — the base snapshot shape is unchanged for non-MR docs.

- [ ] **Step 4: Commit**

```bash
git add apps/api/src/easysynq_api/services/vault/service.py
git commit -m "feat(s-mr-1): checkin_mgmt_review_minutes freeze + _snapshot mgmt_review_minutes fold"
```

---

## Phase 3 — Create, lifecycle (submit/release/approval), and the router skeleton

### Task 11: The MR service — `create_review` + `_obj`-style helpers + the conflict helpers

**Files:**
- Create: `apps/api/src/easysynq_api/services/mgmt_review/__init__.py`
- Create: `apps/api/src/easysynq_api/services/mgmt_review/service.py`
- Create: `apps/api/src/easysynq_api/services/mgmt_review/repository.py`

- [ ] **Step 1: Write `repository.py`** — reads: `get_review(session, review_id) -> ManagementReview | None`; `get_review_doc(session, review_id) -> (ManagementReview, DocumentedInformation) | None` (load both with `populate_existing=True` for the freeze caller); `list_reviews(session, org_id) -> list[(ManagementReview, identifier, title, current_state)]` (join `documented_information`, the `list_objectives` shape); `list_inputs(session, review_id)` / `list_outputs(session, review_id)`; `open_review_exists(session, org_id) -> bool` (a non-Effective, non-closed MR document exists — the cadence idempotency check). Mirror `services/objectives/queries.py` join shapes.

- [ ] **Step 2: Write `service.py`'s conflict helpers + `create_review`.** Copy `_not_found`/`_conflict`/`_validation_error` from `services/audits/service.py:93-107`. `create_review` mirrors `services/objectives/service.py:111-159`:

```python
async def create_review(session, sink, actor, *, title, period_label=None, area_code=None,
                        folder_path=None, classification=DocumentClassification.Internal) -> ManagementReview:
    dt_id = await _mr_document_type_id(session, actor.org_id)   # SELECT document_type WHERE code='MR'; 422 if unseeded
    # (validate any FK inputs BEFORE create_document — it commits the base doc internally)
    doc = await create_document(session, sink, actor, title=title, document_type_id=dt_id,
                                area_code=area_code, folder_path=folder_path, classification=classification)
    # Auto-map to clause 9.3 so the ★ checklist resolves on release (mirror objectives/service.py:137-156):
    clause = (await session.execute(
        select(Clause).where(Clause.number == "9.3", Clause.framework_id == doc.framework_id)
    )).scalar_one_or_none()
    if clause is not None:
        session.add(ClauseMapping(org_id=actor.org_id, framework_id=doc.framework_id, clause_id=clause.id,
                                  documented_information_id=doc.id, is_requirement_level=True, created_by=actor.id))
    mr = ManagementReview(id=doc.id, org_id=actor.org_id, period_label=period_label)
    session.add(mr)
    await session.commit()
    await session.refresh(mr)
    return mr
```

> ⚠ Confirm `"9.3"` is the exact seeded `Clause.number` for Management Review: `cd apps/api && uv run python -c "...select Clause.number like '9.3%'..."` or grep `db/seeds/iso9001_clauses.py`. (Recon confirmed 9.3 is `is_mandatory_star=True`.)

- [ ] **Step 3: Commit**

```bash
git add apps/api/src/easysynq_api/services/mgmt_review/__init__.py apps/api/src/easysynq_api/services/mgmt_review/service.py apps/api/src/easysynq_api/services/mgmt_review/repository.py
git commit -m "feat(s-mr-1): mgmt-review service create_review + repository reads"
```

### Task 12: `submit_review` + `release` service functions

**Files:**
- Modify: `apps/api/src/easysynq_api/services/mgmt_review/service.py`

- [ ] **Step 1: Implement `submit_review_for_review`** mirroring `services/objectives/lifecycle.py:28-102`. Load doc + mr with `with_for_update().execution_options(populate_existing=True)` (the S-drift-1 trap); gate `doc.current_state in (Draft, UnderRevision)` else 409; build the minutes dict (inputs from `list_inputs` source_refs, outputs from `list_outputs` decision content, attendees/period/review_date from the mr row, `compiled_at = now`); call `checkin_mgmt_review_minutes(...)` (the freeze, a sub-step that flushes); delete the working-draft lock if present; `submit_review(session, actor, doc)` (the generic vault submit, Draft→InReview); `instantiate_approval(session, result.doc, actor)` (a **DOCUMENT**-subject approval instance); `audit_transition(...)`; `await session.commit()`.

- [ ] **Step 2: Implement `release_review`** — thin wrapper delegating to the generic `release(caller, review_id, vault_sink, sig_sink)` (the same INV-1 SERIALIZABLE cutover OBJ uses; the shared doc id drives it with zero subtype code). The release endpoint (Task 14) owns the `document.release` enforce + SoD-2 scope + `session.expire_all()` + the `spawn_mr_actions` call (Phase 5).

- [ ] **Step 3: Commit**

```bash
git add apps/api/src/easysynq_api/services/mgmt_review/service.py
git commit -m "feat(s-mr-1): submit_review_for_review (freeze+submit) + release wrapper"
```

### Task 13: Output CRUD + the input-recompile entry point

**Files:**
- Modify: `apps/api/src/easysynq_api/services/mgmt_review/service.py`

- [ ] **Step 1: Implement** `add_output` / `update_output` / `delete_output` (Draft-only — 409 if the doc has left Draft; `ACTION` outputs require `owner_user_id`; emit `MGMT_REVIEW_OUTPUT_RECORDED` on add — see the audit-emit pattern in Task 22). Also implement `update_review_meta(session, review, *, period_label?, review_date?, attendees?)` (Draft-only — the fields the freeze reads into the minutes; wire it to a `PATCH /management-reviews/{review_id}` route gated `mgmtReview.record_outputs`, using `body.model_fields_set` for omitted-vs-null per the objectives PATCH precedent). `compile_inputs` is Phase 4 (Task 16). Each mutation is gated at the router on `mgmtReview.record_outputs`.

- [ ] **Step 2: Commit**

```bash
git add apps/api/src/easysynq_api/services/mgmt_review/service.py
git commit -m "feat(s-mr-1): review_output CRUD (Draft-only)"
```

### Task 14: The router skeleton — create/list/detail/submit/release/approval

**Files:**
- Create: `apps/api/src/easysynq_api/api/mgmt_review.py`
- Test: `apps/api/tests/integration/test_mgmt_review.py` (start it here; grows through the plan)

- [ ] **Step 1: Write a first integration test** — create → list → detail (the happy path; gates ride SYSTEM overrides, see the test harness note below)

```python
# (mirror the harness in tests/integration/test_objective_lifecycle.py — _grant(subject, keys), app_client, headers)
async def test_create_and_read_management_review(app_client, ...):
    hs = ...  # headers for a subject granted mgmtReview.create/read (+ document.* for create_document) via SYSTEM overrides
    r = await app_client.post("/api/v1/management-reviews", json={"title": "Q2 2026 Review"}, headers=hs)
    assert r.status_code == 201
    rid = r.json()["id"]
    lst = (await app_client.get("/api/v1/management-reviews", headers=hs)).json()
    assert any(row["id"] == rid for row in lst["data"])
    det = (await app_client.get(f"/api/v1/management-reviews/{rid}", headers=hs)).json()
    assert det["current_state"] == "Draft"
```

- [ ] **Step 2: Run it — fails** (404, no router).
- [ ] **Step 3: Implement the router** mirroring `api/objectives.py`. `router = APIRouter(prefix="/api/v1", tags=["management-reviews"])`. Module-level gates: `_mr_read = require("mgmtReview.read")`, `_mr_create = require("mgmtReview.create")`, `_mr_outputs = require("mgmtReview.record_outputs")` (all SYSTEM-scope → the default `_system_scope` resolver, no async resolver). Serializers `_mgmt_review(mr, *, identifier, title, current_state)`, `_review_input(ri)`, `_review_output(ro)` (str UUIDs, `.value` enums, `.isoformat()` dates; the list envelope is `{"data":[...]}`). Routes (**literal sub-paths before `/{review_id}`**): `POST /management-reviews` (201; `enforce(...)` imperatively on `mgmtReview.create` since there's no path id), `GET /management-reviews` (`_mr_read`), `GET /management-reviews/{review_id}` (`_mr_read`; 404 cross-org), `POST /management-reviews/{review_id}/compile-inputs` (`_mr_outputs`, Phase 4), `POST/PATCH/DELETE /management-reviews/{review_id}/outputs[/{output_id}]` (`_mr_outputs`), `POST /management-reviews/{review_id}/submit-review` (`_mr_outputs`), `GET /management-reviews/{review_id}/approval` (`_mr_read`; copy `_approval_instance`/`_approval_task` from `api/objectives.py:227-257`, query `WorkflowSubjectType.DOCUMENT`), `POST /management-reviews/{review_id}/release` (imperative `document.release` enforce + SoD-2 scope — copy `_objective_release_scope` from `objectives.py:317-334`; then `release_review`, `session.expire_all()`, `spawn_mr_actions` [Phase 5]), `POST /management-reviews/{review_id}/close` (`_mr_outputs`, Phase 6).

- [ ] **Step 4: Register the router** in `main.py` — import `from .api.mgmt_review import router as mgmt_review_router` (alphabetical block) + `app.include_router(mgmt_review_router)  # S-mr-1: clause-9.3 Management Review` in the **content-router** cluster (near `objectives_router`).

- [ ] **Step 5: Run the test — passes.** Run: `cd apps/api && uv run pytest tests/integration/test_mgmt_review.py -v -m integration` (needs Docker; on this Windows box the integration run is the Linux CI — run `/check-api` for the static loop locally and rely on CI for `-m integration`, or run via testcontainers if Docker is up).
- [ ] **Step 6: Commit**

```bash
git add apps/api/src/easysynq_api/api/mgmt_review.py apps/api/src/easysynq_api/main.py apps/api/tests/integration/test_mgmt_review.py
git commit -m "feat(s-mr-1): mgmt-review router (create/list/detail/submit/release/approval) + registration"
```

---

## Phase 4 — The input compiler (owner-grant-gated, fail-closed gap rows)

### Task 15: Pure input projections

**Files:**
- Create: `apps/api/src/easysynq_api/domain/mgmt_review/inputs.py`
- Test: `apps/api/tests/unit/test_mgmt_review_inputs.py`

- [ ] **Step 1: Write failing tests** for the pure shape→summary projections (one per sourced read; deterministic). Example:

```python
from easysynq_api.domain.mgmt_review.inputs import summarize_scorecard, summarize_capas


def test_summarize_scorecard_counts_by_rag() -> None:
    sc = {"total": 5, "on_target": 3, "by_rag": {"green": 3, "amber": 1, "red": 1, "unmeasured": 0}}
    assert summarize_scorecard(sc) == {"total": 5, "on_target": 3, "by_rag": sc["by_rag"]}
```

- [ ] **Step 2: Implement** `summarize_scorecard`, `summarize_audits`, `summarize_capas_ncrs`, `summarize_kpis`, `summarize_process_perf`, `summarize_prior_actions`, and a `gap_row(reason)` helper — each returning a plain JSON-safe dict (the `source_ref` content). Pure, no I/O.
- [ ] **Step 3: Run — passes.** Commit.

```bash
git add apps/api/src/easysynq_api/domain/mgmt_review/inputs.py apps/api/tests/unit/test_mgmt_review_inputs.py
git commit -m "feat(s-mr-1): pure 9.3.2 input projections"
```

### Task 16: `compile_inputs` — owner-grant-gated read orchestration

**Files:**
- Create: `apps/api/src/easysynq_api/services/mgmt_review/compile.py`

- [ ] **Step 1: Implement `compile_inputs(session, review, owner)`** (Draft only). For each sourced input type, **PDP-check the OWNER's grant** (the non-auditing path — `gather_grants` + `authorize`, NOT `pep.evaluate`), and if held, call the service read + project; else write a gap row. Then replace the working `review_input` set (delete-then-insert in one txn) and emit `MGMT_REVIEW_INPUTS_COMPILED`.

```python
from easysynq_api.services.authz import gather_grants
from easysynq_api.domain.authz import authorize, ResourceContext, RequestContext

async def _owner_holds(session, owner, key) -> bool:
    grants = await gather_grants(session, owner.id, owner.org_id, key)
    return authorize(grants, key, ResourceContext.system(), RequestContext(now=datetime.datetime.now(datetime.UTC))).allow

# sourced reads (service-layer, org-wide):
#   OBJECTIVES_STATUS  ← objective.read     → list_objectives + the inline RAG loop (see objectives.py:464) → summarize_scorecard
#   AUDIT_RESULTS      ← audit.read         → audits_repo.list_audits(session, org_id)        → summarize_audits
#   NONCONFORMITIES_CAPA ← capa.read(+ncr.read+complaint.read) → capa_repo.list_capas/list_ncrs/list_complaints → summarize_capas_ncrs
#   MONITORING_RESULTS ← kpi.read           → select(func.count()).where(KpiMeasurement.org_id==org_id)  → summarize_kpis
#   PROCESS_PERFORMANCE← report.compliance_checklist.read (+drift.read) → compute_checklist(session, org_id) + drift_status(session) → summarize_process_perf
#   PRIOR_ACTIONS      ← (the previous released MR's outputs; gap row until a 2nd review exists)
# gap rows (available=False, fixed reason): CONTEXT_CHANGES, CUSTOMER_SATISFACTION, SUPPLIER_PERFORMANCE,
#   RESOURCE_ADEQUACY, RISK_OPPORTUNITY_ACTIONS, IMPROVEMENT_OPPORTUNITIES.
```

> ⚠ `drift_status(session)` takes **NO** `org_id` (single-org vault). `list_complaints` returns **2-tuples** `(Complaint, identifier)`. `list_ncrs` returns **bare `Ncr` rows**. There is no service-layer scorecard fn — replicate the `api/objectives.py:464` RAG-count loop (`resolve_commitment` → `rag_status`). Each `review_input.source_ref` is `{"available": bool, "summary": {...}?, "reason": str?, "generated_at": iso}`.

- [ ] **Step 2: Wire it** into the `POST /compile-inputs` route (the owner = the review doc's `owner_user_id`; load the `AppUser` for it). Add an integration test asserting the six sourced rows + the gap rows, and that a caller/owner lacking `audit.read` yields an `available=False AUDIT_RESULTS` row (not a 403).
- [ ] **Step 3: Run + commit**

```bash
git add apps/api/src/easysynq_api/services/mgmt_review/compile.py apps/api/src/easysynq_api/api/mgmt_review.py apps/api/tests/integration/test_mgmt_review.py
git commit -m "feat(s-mr-1): compile_inputs — owner-gated 9.3.2 reads, fail-closed gap rows"
```

---

## Phase 5 — Outputs → work: spawn MR_ACTION at release + the decide leg

### Task 17: `spawn_mr_actions` (at release)

**Files:**
- Create: `apps/api/src/easysynq_api/services/mgmt_review/spawn.py`

- [ ] **Step 1: Implement.** At release, for the review: resolve the `management_review` `WorkflowDefinition` (`wf_repo.effective_definition(session, org_id, "management_review", WorkflowSubjectType.MGMT_REVIEW)`); create ONE `WorkflowInstance(subject_type=MGMT_REVIEW, subject_id=review.id, definition_id, definition_version, current_state="OPEN", revision=0)`, `flush()`; then for each `ACTION` `review_output` with an `owner_user_id`, add a `Task(org_id, instance_id=instance.id, stage_key="action", type=TaskType.MR_ACTION, assignee_user_id=output.owner_user_id, candidate_pool=[str(output.owner_user_id)], action_expected="complete", state=TaskState.PENDING, due_at=_org_midnight(output.due_date))`, `flush()`, set `output.spawned_task_id = task.id`, emit `MGMT_REVIEW_ACTION_SPAWNED`. Set `review.close_state = ManagementReviewCloseState.ActionsTracked`. (Mirror the S5 direct-insert at `services/workflow/service.py:63-86`; `_org_midnight` = `datetime.combine(due_date, time(0,0), tzinfo=ZoneInfo(get_settings().easysynq_org_timezone))` — the `review.py:180` recipe.)

> If no `MGMT_REVIEW` definition is seeded (mis-seed), raise `_conflict("mgmt_review_workflow_unseeded", ...)` — the instance FK requires it (the seed is Task 8). An output owner must be a real `app_user.id`.

- [ ] **Step 2: Call it** from the release route AFTER `release_review` + `session.expire_all()`.
- [ ] **Step 3: Commit**

```bash
git add apps/api/src/easysynq_api/services/mgmt_review/spawn.py apps/api/src/easysynq_api/api/mgmt_review.py
git commit -m "feat(s-mr-1): spawn MR_ACTION tasks at release"
```

### Task 18: The `decide_mr_task` service + the new decide dispatch leg

**Files:**
- Create: `apps/api/src/easysynq_api/services/mgmt_review/decide.py`
- Modify: `apps/api/src/easysynq_api/api/workflow.py`

- [ ] **Step 1: Implement `decide_mr_task`** mirroring `decide_periodic_review`. 404-collapse non-membership (`task.assignee_user_id != caller.id and str(caller.id) not in (task.candidate_pool or [])` → 404); `wf_engine.decide(session, task, caller, outcome=..., _commit=False)` for the task→DONE + idempotency replay; then commit. (MR_ACTION outcomes are `complete`/`changes_requested` — pick the verb set; the close gate only cares that the task reaches `TaskState.DONE`.)

- [ ] **Step 2: Add the dispatch leg** in `api/workflow.py:decide_endpoint`, **before** the DOCUMENT fallthrough:

```python
    if instance is not None and instance.subject_type is WorkflowSubjectType.MGMT_REVIEW:
        return await decide_mr_task(session, task, caller, outcome=body.outcome, ...)
```

- [ ] **Step 3: Integration test** — a spawned MR_ACTION appears in `GET /tasks?type=MR_ACTION` for the owner; the owner decides it `DONE`; a non-member gets 404 on the decide.
- [ ] **Step 4: Commit**

```bash
git add apps/api/src/easysynq_api/services/mgmt_review/decide.py apps/api/src/easysynq_api/api/workflow.py apps/api/tests/integration/test_mgmt_review.py
git commit -m "feat(s-mr-1): MGMT_REVIEW decide leg for MR_ACTION tasks"
```

---

## Phase 6 — The close gate

### Task 19: `output_blocks_close` (pure predicate)

**Files:**
- Create: `apps/api/src/easysynq_api/domain/mgmt_review/close_gate.py`
- Test: `apps/api/tests/unit/test_mgmt_review_close_gate.py`

- [ ] **Step 1: Write failing tests** (mirror `finding_blocks_close` semantics — type-gate first, then the fail-closed `is not DONE` tail)

```python
from easysynq_api.db.models._mgmt_review_enums import ReviewOutputType
from easysynq_api.db.models._workflow_enums import TaskState
from easysynq_api.domain.mgmt_review.close_gate import output_blocks_close


def test_decision_never_blocks() -> None:
    assert output_blocks_close(ReviewOutputType.DECISION, None) is False

def test_action_with_no_task_blocks_fail_closed() -> None:
    assert output_blocks_close(ReviewOutputType.ACTION, None) is True

def test_action_pending_blocks() -> None:
    assert output_blocks_close(ReviewOutputType.ACTION, TaskState.PENDING) is True

def test_action_done_does_not_block() -> None:
    assert output_blocks_close(ReviewOutputType.ACTION, TaskState.DONE) is False
```

- [ ] **Step 2: Run — fails.**
- [ ] **Step 3: Implement** (the `finding_blocks_close` shape verbatim)

```python
"""output_blocks_close — pure close-gate predicate for a Management Review (the finding_blocks_close shape).

True iff this output blocks the review close: an ACTION whose spawned MR_ACTION task is absent or not yet
DONE. A DECISION/IMPROVEMENT never blocks. A None task-state (unspawned/missing) blocks (fail-closed)."""

from __future__ import annotations

from easysynq_api.db.models._mgmt_review_enums import ReviewOutputType
from easysynq_api.db.models._workflow_enums import TaskState


def output_blocks_close(output_type: ReviewOutputType, task_state: TaskState | None) -> bool:
    if output_type is not ReviewOutputType.ACTION:
        return False
    return task_state is not TaskState.DONE
```

> ⚠ The tail is `task_state is not TaskState.DONE` — do NOT write `is X` (that silently passes a None/missing task: a fail-OPEN bug). Type-gate FIRST.

- [ ] **Step 4: Run — passes. Commit.**

```bash
git add apps/api/src/easysynq_api/domain/mgmt_review/close_gate.py apps/api/tests/unit/test_mgmt_review_close_gate.py
git commit -m "feat(s-mr-1): output_blocks_close pure predicate"
```

### Task 20: The service-layer close gate + the close endpoint

**Files:**
- Modify: `apps/api/src/easysynq_api/services/mgmt_review/repository.py`, `service.py`

- [ ] **Step 1: Add the close-gate loader** in `repository.py` (mirror `findings_for_close_gate` — **OUTERJOIN** the spawned task so an unlinked ACTION yields `None` → blocks):

```python
ReviewCloseGateRow = tuple[ReviewOutputType, TaskState | None]

async def outputs_for_close_gate(session, review_id) -> Sequence[ReviewCloseGateRow]:
    rows = await session.execute(
        select(ReviewOutput.output_type, Task.state)
        .outerjoin(Task, Task.id == ReviewOutput.spawned_task_id)
        .where(ReviewOutput.management_review_id == review_id)
    )
    return [(ot, ts) for ot, ts in rows.all()]
```

- [ ] **Step 2: Add `close_review`** in `service.py` (the gate + the state flip):

```python
async def close_review(session, sink, actor, review) -> ManagementReview:
    rows = await repo.outputs_for_close_gate(session, review.id)
    blocking = sum(1 for ot, ts in rows if output_blocks_close(ot, ts))
    if blocking:
        raise _conflict("review_close_blocked",
                        f"Cannot close: {blocking} open action(s) whose MR_ACTION task is not done")
    review.close_state = ManagementReviewCloseState.Closed
    review.closed_at = datetime.datetime.now(datetime.UTC)
    # emit MGMT_REVIEW_CLOSED (object_type=document, scope_ref=identifier)
    await session.commit()
    return review
```

- [ ] **Step 3: Wire the `POST /close` route** (`_mr_outputs` gate; 409 surfaces `review_close_blocked` with the count). Integration test: close 409s while an action is open; passes after the owner marks it DONE.
- [ ] **Step 4: Commit**

```bash
git add apps/api/src/easysynq_api/services/mgmt_review/repository.py apps/api/src/easysynq_api/services/mgmt_review/service.py apps/api/src/easysynq_api/api/mgmt_review.py apps/api/tests/integration/test_mgmt_review.py
git commit -m "feat(s-mr-1): review close gate (spawned actions must be DONE)"
```

---

## Phase 7 — The cadence Beat sweep

### Task 21: `sweep_mgmt_reviews` + lock constant + task wrapper + registration

**Files:**
- Modify: `apps/api/src/easysynq_api/services/common/pg_locks.py`
- Create: `apps/api/src/easysynq_api/services/mgmt_review/cadence.py`
- Create: `apps/api/src/easysynq_api/tasks/mgmt_review.py`
- Modify: `apps/api/src/easysynq_api/tasks/__init__.py`, `tasks/app.py`
- Test: `apps/api/tests/unit/test_mgmt_review_cadence.py`, `apps/api/tests/unit/test_mgmt_review_task_registration.py`

- [ ] **Step 1: Add the lock constant** in `pg_locks.py`: `LOCK_MGMT_REVIEW_SWEEP = 7710009  # S-mr-1: serialize the daily management-review cadence sweep` (next free after `LOCK_ACK_SWEEP=7710008`).

- [ ] **Step 2: Write the cadence unit test** for `next_mr_due` math (no history / within window / open-MR-skip):

```python
import datetime
from easysynq_api.services.mgmt_review.cadence import next_mr_due

def test_next_mr_due_adds_cadence_to_last_effective() -> None:
    assert next_mr_due(datetime.date(2025, 6, 1), 12) == datetime.date(2026, 6, 1)

def test_next_mr_due_none_history_is_due_now() -> None:
    assert next_mr_due(None, 12) is None  # caller treats None-history as "mint the first one"
```

- [ ] **Step 3: Implement `cadence.py`.** `next_mr_due(last_effective_from_date, cadence_months)` uses `add_months` (`from ...services.vault.review import add_months, today_org, _org_tz`). `sweep_mgmt_reviews(session)`: acquire `LOCK_MGMT_REVIEW_SWEEP` (`pg_advisory_lock` — skip-and-return if not held); resolve the single org; read `system_config` cadence + owner (degrade to logged no-op if owner is NULL); resolve the `management_review` definition once (degrade if unseeded); `if await repo.open_review_exists(session, org_id): return no-op`; compute the last released MR's `effective_from` (via `documented_information`) → `next_mr_due`; if `due_date <= today_org()` within the lead window, call `create_review(...)` under the configured owner + add the `MR_INPUT` task on a fresh `MGMT_REVIEW` instance (the Task 17 spawn shape, `type=TaskType.MR_INPUT`, `action_expected="prepare"`); commit. Return `{"mgmt_reviews_opened": n, "skipped_open": m}`.

> ⚠ `create_review` commits internally (it calls `create_document` which commits the base doc). Accept the two-commit shape for the sweep (the org-wide `open_review_exists` check before create is the idempotency guard). The idempotency check is **org-scoped**, NOT subject-scoped (an MR's subject is the doc you're about to mint — see the cluster-F gotcha).

- [ ] **Step 4: Write `tasks/mgmt_review.py`** — copy `tasks/review.py` verbatim (fresh `create_async_engine` + `async_sessionmaker(expire_on_commit=False)` + try/finally `engine.dispose()`), swap to `sweep_mgmt_reviews`, name `easysynq.documents.mgmt_review_sweep`, keep `# type: ignore[untyped-decorator]`, return `dict[str, int]`.

- [ ] **Step 5: Register** — add `mgmt_review,` to the `tasks/__init__.py` import tuple (alphabetical); add the `"documents-mgmt-review-sweep": {"task": "easysynq.documents.mgmt_review_sweep", "schedule": 86400.0}` entry to `tasks/app.py` `beat_schedule`.

- [ ] **Step 6: Write `test_mgmt_review_task_registration.py`** (the two-test pin):

```python
from easysynq_api.tasks import app

def test_mgmt_review_sweep_task_is_registered() -> None:
    assert "easysynq.documents.mgmt_review_sweep" in app.tasks

def test_mgmt_review_sweep_is_beat_scheduled_daily() -> None:
    entries = {e["task"]: e["schedule"] for e in app.conf.beat_schedule.values()}
    assert entries.get("easysynq.documents.mgmt_review_sweep") == 86400.0
```

- [ ] **Step 7: Run the unit tests — pass.** Run: `cd apps/api && uv run pytest tests/unit/test_mgmt_review_cadence.py tests/unit/test_mgmt_review_task_registration.py -v`
- [ ] **Step 8: Commit**

```bash
git add apps/api/src/easysynq_api/services/common/pg_locks.py apps/api/src/easysynq_api/services/mgmt_review/cadence.py apps/api/src/easysynq_api/tasks/mgmt_review.py apps/api/src/easysynq_api/tasks/__init__.py apps/api/src/easysynq_api/tasks/app.py apps/api/tests/unit/test_mgmt_review_cadence.py apps/api/tests/unit/test_mgmt_review_task_registration.py
git commit -m "feat(s-mr-1): cadence Beat sweep + lock + task registration"
```

---

## Phase 8 — Audit emission, the headline ★-flip test, openapi, final gates

### Task 22: Audit-event emission across the service

**Files:**
- Modify: `apps/api/src/easysynq_api/services/mgmt_review/service.py`, `compile.py`, `spawn.py`

- [ ] **Step 1: Add the `AuditEvent` rows** at each act, mirroring `services/objectives/service.py:293-312` — direct `session.add(AuditEvent(org_id=actor.org_id, occurred_at=now, actor_id=actor.id, actor_type=ActorType.user, event_type=EventType.MGMT_REVIEW_*, object_type=AuditObjectType.document, object_id=review.id, scope_ref=<identifier>, after={...}))` BEFORE the commit. `MGMT_REVIEW_INPUTS_COMPILED` (compile), `MGMT_REVIEW_OUTPUT_RECORDED` (add_output), `MGMT_REVIEW_ACTION_SPAWNED` (spawn, per task), `MGMT_REVIEW_CLOSED` (close). Create/submit/release reuse the existing `DOCUMENT_*` events (they ride `create_document`/`submit_review`/`release`). Do NOT use `emit_record_event` (it hardcodes `object_type=record`).
- [ ] **Step 2: Commit**

```bash
git add apps/api/src/easysynq_api/services/mgmt_review/
git commit -m "feat(s-mr-1): MGMT_REVIEW_* audit events (object_type=document)"
```

### Task 23: The headline integration test — 9.3 PARTIAL→COVERED on release

**Files:**
- Modify: `apps/api/tests/integration/test_mgmt_review.py`

- [ ] **Step 1: Write the full-loop test** (mirror `test_objective_lifecycle.py:105-169` — **delta-asserted**, shared DB):

```python
async def _clause_9_3_row(client, h) -> dict:
    body = (await client.get("/api/v1/reports/compliance-checklist", headers=h)).json()
    return next(r for r in body["rows"] if r["number"] == "9.3")

async def test_released_management_review_flips_9_3_covered(app_client, ...):
    hs = ...  # submitter; grant report.compliance_checklist.read (+ the read keys the compiler needs) via SYSTEM overrides
    before = await _clause_9_3_row(app_client, hs)
    eff0 = before["effective_count"]
    # create → compile-inputs → add an output → submit-review → approve the DOCUMENT task → release
    rid = (await app_client.post("/api/v1/management-reviews", json={"title": "Annual 2026"}, headers=hs)).json()["id"]
    await app_client.post(f"/api/v1/management-reviews/{rid}/compile-inputs", headers=hs)
    await app_client.post(f"/api/v1/management-reviews/{rid}/outputs",
                          json={"output_type": "DECISION", "description": "QMS remains effective"}, headers=hs)
    await app_client.post(f"/api/v1/management-reviews/{rid}/submit-review", json={}, headers=hs)
    # ... approve the DOCUMENT approval task (the /tasks decide leg, approver ≠ author — the SoD harness) ...
    await app_client.post(f"/api/v1/management-reviews/{rid}/release", json={}, headers=hs_releaser)
    after = await _clause_9_3_row(app_client, hs)
    assert after["effective_count"] == eff0 + 1
    assert after["status"] == "COVERED"
```

> ⚠ Delta-assert (`eff0 + 1`), never `== 1` (shared DB). The submitter is the frozen version's author → SoD binds the approver/releaser to be a different subject; use the persona harness (`priya`/`ken`/`mara`) the objective lifecycle test uses. Confirm `9.3` appears in `body["rows"]` (it is `is_mandatory_star=True`).

- [ ] **Step 2: Add the close-loop assertions** — spawn an `ACTION` output (with an owner) instead, release, assert the `MR_ACTION` task exists for the owner, the close 409s while it's open, the owner decides it DONE, the close passes (`close_state == "Closed"`).
- [ ] **Step 3: Commit**

```bash
git add apps/api/tests/integration/test_mgmt_review.py
git commit -m "test(s-mr-1): 9.3 PARTIAL->COVERED on release + the close-loop"
```

### Task 24: OpenAPI contract

**Files:**
- Modify: `packages/contracts/openapi.yaml`

- [ ] **Step 1: Add the paths + component schemas** mirroring the objectives endpoints (`additionalProperties: false`, explicit `required`, decimal-as-string, nullable `[type,"null"]`, the `{data:[array]}` list envelope, `ProblemResponse` refs). Declare literal sub-paths (`/management-reviews/{id}/...`) and document the order note. Schemas: `ManagementReview`, `ManagementReviewCreate`, `ReviewInput`, `ReviewOutput`, `ReviewOutputCreate`. The `current_state` enum is the 7 canonical document states. Do NOT touch the `CapaSource.review_output` reserved note (CAPA un-reserve is slice-2).
- [ ] **Step 2: Lint.** Run: `/check-contracts` (redocly). Expected: clean.
- [ ] **Step 3: Commit**

```bash
git add packages/contracts/openapi.yaml
git commit -m "docs(s-mr-1): openapi — /management-reviews endpoints + schemas"
```

### Task 25: Full gates + diff-critic

- [ ] **Step 1:** `/check-api` (ruff + format + mypy-strict + unit) — green.
- [ ] **Step 2:** `/check-migrations` (round-trip + `alembic check`) — green; verify a **populated downgrade** doesn't abort (create an MR, then downgrade) — the NOT-EXISTS guards.
- [ ] **Step 3:** `/check-contracts` — green.
- [ ] **Step 4:** Run the `diff-critic` agent on the branch diff (`Agent` tool, `subagent_type: diff-critic`). Fold only confirmed findings.
- [ ] **Step 5:** Pre-merge live smoke (the worker heredoc — rebuild migrate/api/worker/beat): create review → compile inputs → record outputs → submit → approve → release → assert 9.3 ★ flips + the `MR_ACTION` tasks exist → close. The live migrate run is itself a smoke leg.

---

## Deferrals carried (do NOT implement here — named in the spec)

The four sourceless 9.3.2 inputs + risk/opportunity (e) + `improvement_initiative` (f) ship as gap rows; CAPA un-reserve + the DCR `mgmt_review` link (the `spawned_capa_id`/`spawned_initiative_id` columns ship reserved-null) are slice-2; the rendered "Management Review Pack" PDF is v1.1; a dedicated Top-Management approval routing is a named enhancement; the S-mr-2 UI + the Home "next review in N days" widget is the trailing front-end-only slice; org-tunable cadence is v1.x.

## Register entry (write with the PR)

Add **R45** (the draft is in spec §s12) to `docs/decisions-register.md`, the `docs/slice-history.md` narrative entry, and a capped `CLAUDE.md` Recent-learnings line on merge. Back-propagate docs `02/06/07/10/13/14/15/16/18` per spec §s12.
