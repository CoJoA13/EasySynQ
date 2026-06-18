# S-obj-1 — Quality Objectives backend engine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the clause-6.2 Quality Objectives backend (migration `0049`): objectives as kind=DOCUMENT subtypes, `objective_plan` rows, append-only `KPI_READING` measurements rolled up into `current_value`, a direction-aware/amber RAG read, and a `/objectives` API — riding the already-seeded `objective.*`/`kpi.*` keys (no catalog change), unblocking the PDCA dashboard.

**Architecture:** A `quality_objective` is a shared-PK subtype of `documented_information` (`kind=DOCUMENT`, type `OBJ`) — the `form_template`/`capa` precedent — created through the existing vault document lifecycle. Each measurement is an evidence-grade `KPI_READING` record (via `capture_record(..., _commit=False)`) with a `kpi_measurement` projection; recording one rolls `current_value` up on the satellite under `FOR UPDATE`+`populate_existing`. On/off-target is a **pure** function computed at read. Quality Policy is the already-seeded `POL` singleton (R25 enforced by the base partial-unique index).

**Tech Stack:** Python 3.12 · FastAPI · SQLAlchemy 2 (async) · Alembic · PostgreSQL 16 · pytest (unit + testcontainers integration).

**Spec:** [docs/superpowers/specs/2026-06-11-s-obj-quality-objectives-design.md](../specs/2026-06-11-s-obj-quality-objectives-design.md)

---

## Conventions (read once)

- **TDD, one behavior per test.** Write the failing test → run-expect-FAIL → minimal implementation → run-expect-PASS → commit. Frequent commits (one per task minimum).
- **Windows-box gate caveat (this machine):** the api **unit** + **integration** suites are **Linux-CI-only** here (`-m unit` access-violations on libmagic; `-m integration` rejects the Proactor loop). Locally, the reliable api gates are **`uv run ruff check`**, **`ruff format --check`**, **`mypy --strict`**; the full unit/integration runs happen in **CI / Docker**. Each step's `pytest` command is the canonical CI command — run it in CI (or `just up` Docker). The migration round-trip (`/check-migrations`) runs locally on a throwaway PG16.
- **Single test:** `uv run pytest apps/api/tests/unit/test_objective_rules.py::test_name -v` (unit) · `uv run pytest -m integration apps/api/tests/integration/test_quality_objectives.py::test_name -v` (integration, needs Docker).
- **Static gate (run before every commit):** `uv run ruff check apps/api && uv run ruff format --check apps/api && uv run mypy --strict apps/api/src` (the `/check-api` static half).
- **Txn ownership:** services own the commit. Compose engine calls + inserts + audit in ONE transaction; pass `_commit=False` to `capture_record` so the measurement + projection + rollup commit together.
- **The `populate_existing` trap (S-drift-1):** any handler-level `.with_for_update()` load of a row the authz resolver already `session.get`-loaded MUST add `.execution_options(populate_existing=True)` or it returns stale identity-map attributes. The rollup load does this.
- **Pin fixtures to the real serializer (S-obj-2, not here):** when the web slice lands, MSW fixtures copy the `_objective`/`_measurement`/`_scorecard` dict shapes from this slice's serializers (`satisfies <Type>`), never a guess.
- **Audit:** objective/policy lifecycle reuses the existing `DOCUMENT_*` events; new acts emit additive `OBJECTIVE_*` event types with `object_type='document'` + `scope_ref=<identifier>` (R39 — no new `audit_object_type`). No `signature_event` for measurements/plans (R2 closed).
- **Numbers are `Decimal`.** Numeric columns map to `Mapped[Decimal]`; the pure rule accepts `Decimal | None`.

---

## File structure

**Create:**
- `apps/api/src/easysynq_api/db/models/_objective_enums.py` — `ObjectiveDirection` enum + `OBJECTIVE_DIRECTION_VALUES`
- `apps/api/src/easysynq_api/db/models/quality_objective.py` — `QualityObjective` subtype
- `apps/api/src/easysynq_api/db/models/objective_plan.py` — `ObjectivePlan`
- `apps/api/src/easysynq_api/db/models/kpi_measurement.py` — `KpiMeasurement`
- `apps/api/src/easysynq_api/domain/objectives/__init__.py`
- `apps/api/src/easysynq_api/domain/objectives/rules.py` — pure RAG / pct / attainment / latest-period
- `apps/api/src/easysynq_api/services/objectives/__init__.py` — re-exports
- `apps/api/src/easysynq_api/services/objectives/service.py` — create_objective, record_measurement, plan CRUD, policy helper (txn owner)
- `apps/api/src/easysynq_api/services/objectives/queries.py` — list / get / measurements / scorecard
- `apps/api/src/easysynq_api/api/objectives.py` — the `/objectives` router
- `migrations/versions/0049_quality_objectives.py`
- `apps/api/tests/unit/test_objective_rules.py`
- `apps/api/tests/unit/test_objective_enums.py`
- `apps/api/tests/integration/test_quality_objectives.py`

**Modify:**
- `apps/api/src/easysynq_api/db/models/__init__.py` — import + `__all__` the 3 models + the direction enum
- `apps/api/src/easysynq_api/db/models/_audit_enums.py` — add 3 `OBJECTIVE_*` `EventType` members (rebuilt into `EVENT_TYPE_VALUES`)
- `apps/api/src/easysynq_api/main.py` — mount the objectives router
- `packages/contracts/openapi.yaml` — the new paths + component schemas
- `docs/decisions-register.md` (R44) · `docs/14-data-model.md` · `docs/16-roadmap.md` · `docs/slice-history.md` · `CLAUDE.md` (back-prop, Task 11/12)

---

## Task 1: Direction enum + audit event types

**Files:**
- Create: `apps/api/src/easysynq_api/db/models/_objective_enums.py`
- Create: `apps/api/tests/unit/test_objective_enums.py`
- Modify: `apps/api/src/easysynq_api/db/models/_audit_enums.py`

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/unit/test_objective_enums.py
import pytest

pytestmark = pytest.mark.unit

from easysynq_api.db.models._audit_enums import EVENT_TYPE_VALUES, EventType
from easysynq_api.db.models._objective_enums import (
    OBJECTIVE_DIRECTION_VALUES,
    ObjectiveDirection,
)


def test_direction_values_round_trip() -> None:
    assert OBJECTIVE_DIRECTION_VALUES == ("HIGHER_IS_BETTER", "LOWER_IS_BETTER")
    assert {d.value for d in ObjectiveDirection} == set(OBJECTIVE_DIRECTION_VALUES)


def test_new_objective_event_types_present() -> None:
    for name in (
        "OBJECTIVE_MEASUREMENT_RECORDED",
        "OBJECTIVE_PLAN_ADDED",
        "OBJECTIVE_PLAN_REMOVED",
    ):
        assert hasattr(EventType, name)
        assert getattr(EventType, name).value in EVENT_TYPE_VALUES
```

- [ ] **Step 2: Run — expect FAIL** (`ModuleNotFoundError: _objective_enums`)

Run: `uv run pytest apps/api/tests/unit/test_objective_enums.py -v`

- [ ] **Step 3: Implement**

```python
# apps/api/src/easysynq_api/db/models/_objective_enums.py
"""Objective-family enums (S-obj-1). ``create_type=False`` — the 0049 migration owns CREATE TYPE;
the migration sources its CREATE-TYPE tuple from ``OBJECTIVE_DIRECTION_VALUES`` (the 0010 rule)."""
from __future__ import annotations

import enum

from sqlalchemy import Enum as SAEnum


class ObjectiveDirection(enum.Enum):
    HIGHER_IS_BETTER = "HIGHER_IS_BETTER"
    LOWER_IS_BETTER = "LOWER_IS_BETTER"


def _vals(e: type[enum.Enum]) -> list[str]:
    return [m.value for m in e]


OBJECTIVE_DIRECTION_VALUES = tuple(_vals(ObjectiveDirection))

objective_direction_enum = SAEnum(
    ObjectiveDirection,
    name="objective_direction",
    values_callable=_vals,
    create_type=False,
)
```

Then add to `_audit_enums.py` — find the `EventType` class and add three members alongside the existing ones (mirror the `DOCUMENT_ACKNOWLEDGED`/`DISTRIBUTION_UPDATED` additions from S-ack), and confirm they flow into `EVENT_TYPE_VALUES = tuple(_vals(EventType))`:

```python
    # S-obj-1 — clause 6.2 objective acts (object_type='document', R39 reuse).
    OBJECTIVE_MEASUREMENT_RECORDED = "OBJECTIVE_MEASUREMENT_RECORDED"
    OBJECTIVE_PLAN_ADDED = "OBJECTIVE_PLAN_ADDED"
    OBJECTIVE_PLAN_REMOVED = "OBJECTIVE_PLAN_REMOVED"
```

- [ ] **Step 4: Run — expect PASS**

Run: `uv run pytest apps/api/tests/unit/test_objective_enums.py -v`

- [ ] **Step 5: Static gate + commit**

```bash
uv run ruff check apps/api && uv run ruff format apps/api && uv run mypy --strict apps/api/src
git add apps/api/src/easysynq_api/db/models/_objective_enums.py apps/api/src/easysynq_api/db/models/_audit_enums.py apps/api/tests/unit/test_objective_enums.py
git commit -m "feat(s-obj-1): objective direction enum + OBJECTIVE_* event types"
```

---

## Task 2: The pure RAG / pct / attainment rule

**Files:**
- Create: `apps/api/src/easysynq_api/domain/objectives/__init__.py` (empty)
- Create: `apps/api/src/easysynq_api/domain/objectives/rules.py`
- Create: `apps/api/tests/unit/test_objective_rules.py`

This is the load-bearing pure logic — TDD it exhaustively, no DB.

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/unit/test_objective_rules.py
import datetime
from decimal import Decimal

import pytest

pytestmark = pytest.mark.unit

from easysynq_api.db.models._objective_enums import ObjectiveDirection
from easysynq_api.domain.objectives.rules import (
    attainment,
    pct_toward_target,
    rag_status,
)

HI = ObjectiveDirection.HIGHER_IS_BETTER
LO = ObjectiveDirection.LOWER_IS_BETTER
D = Decimal


def test_unmeasured_when_current_is_none() -> None:
    assert rag_status(current=None, target=D(90), direction=HI, at_risk_threshold=D(80)) == "unmeasured"


def test_higher_is_better_green_amber_red() -> None:
    assert rag_status(current=D(95), target=D(90), direction=HI, at_risk_threshold=D(80)) == "green"
    assert rag_status(current=D(85), target=D(90), direction=HI, at_risk_threshold=D(80)) == "amber"
    assert rag_status(current=D(75), target=D(90), direction=HI, at_risk_threshold=D(80)) == "red"
    # boundary: current == target → green; current == threshold → amber
    assert rag_status(current=D(90), target=D(90), direction=HI, at_risk_threshold=D(80)) == "green"
    assert rag_status(current=D(80), target=D(90), direction=HI, at_risk_threshold=D(80)) == "amber"


def test_lower_is_better_green_amber_red() -> None:
    # "reduce complaints": target 5, at_risk 10
    assert rag_status(current=D(4), target=D(5), direction=LO, at_risk_threshold=D(10)) == "green"
    assert rag_status(current=D(8), target=D(5), direction=LO, at_risk_threshold=D(10)) == "amber"
    assert rag_status(current=D(12), target=D(5), direction=LO, at_risk_threshold=D(10)) == "red"
    assert rag_status(current=D(5), target=D(5), direction=LO, at_risk_threshold=D(10)) == "green"
    assert rag_status(current=D(10), target=D(5), direction=LO, at_risk_threshold=D(10)) == "amber"


def test_no_threshold_collapses_amber_to_red() -> None:
    assert rag_status(current=D(85), target=D(90), direction=HI, at_risk_threshold=None) == "red"
    assert rag_status(current=D(95), target=D(90), direction=HI, at_risk_threshold=None) == "green"


def test_pct_toward_target() -> None:
    # baseline 50, target 100, current 75 → 50%
    assert pct_toward_target(current=D(75), target=D(100), baseline=D(50)) == pytest.approx(0.5)
    # no baseline → fraction of target
    assert pct_toward_target(current=D(45), target=D(90), baseline=None) == pytest.approx(0.5)
    # current None → None; zero denominator → None
    assert pct_toward_target(current=None, target=D(90), baseline=None) is None
    assert pct_toward_target(current=D(75), target=D(50), baseline=D(50)) is None


def test_attainment_met_missed_in_progress() -> None:
    due = datetime.date(2026, 6, 30)
    # before due → in_progress regardless of value
    assert attainment(current=D(50), target=D(90), direction=HI, due_date=due,
                      today=datetime.date(2026, 6, 1)) == "in_progress"
    # at/after due: target reached → met, else missed
    assert attainment(current=D(95), target=D(90), direction=HI, due_date=due,
                      today=datetime.date(2026, 7, 1)) == "met"
    assert attainment(current=D(50), target=D(90), direction=HI, due_date=due,
                      today=datetime.date(2026, 7, 1)) == "missed"
    # lower-is-better met
    assert attainment(current=D(3), target=D(5), direction=LO, due_date=due,
                      today=datetime.date(2026, 7, 1)) == "met"
    # current None at due → missed (never measured)
    assert attainment(current=None, target=D(90), direction=HI, due_date=due,
                      today=datetime.date(2026, 7, 1)) == "missed"
```

- [ ] **Step 2: Run — expect FAIL** (`ModuleNotFoundError: domain.objectives.rules`)

Run: `uv run pytest apps/api/tests/unit/test_objective_rules.py -v`

- [ ] **Step 3: Implement**

```python
# apps/api/src/easysynq_api/domain/objectives/rules.py
"""Pure clause-6.2 objective math (S-obj-1) — no I/O, total, deterministic. RAG is computed at read
(N9: against a rule, never an auto-compliance verdict; N6: no SPC/forecast)."""
from __future__ import annotations

import datetime
from decimal import Decimal

from easysynq_api.db.models._objective_enums import ObjectiveDirection

Numeric = Decimal


def _on_or_better(current: Numeric, target: Numeric, direction: ObjectiveDirection) -> bool:
    if direction is ObjectiveDirection.HIGHER_IS_BETTER:
        return current >= target
    return current <= target


def _within_amber(
    current: Numeric, target: Numeric, threshold: Numeric, direction: ObjectiveDirection
) -> bool:
    """Between the at-risk threshold and the target (exclusive of green, inclusive of threshold)."""
    if direction is ObjectiveDirection.HIGHER_IS_BETTER:
        return threshold <= current < target
    return target < current <= threshold


def rag_status(
    *,
    current: Numeric | None,
    target: Numeric,
    direction: ObjectiveDirection,
    at_risk_threshold: Numeric | None,
) -> str:
    """'green' | 'amber' | 'red' | 'unmeasured'."""
    if current is None:
        return "unmeasured"
    if _on_or_better(current, target, direction):
        return "green"
    if at_risk_threshold is not None and _within_amber(current, target, at_risk_threshold, direction):
        return "amber"
    return "red"


def pct_toward_target(
    *, current: Numeric | None, target: Numeric, baseline: Numeric | None
) -> float | None:
    """Fraction of the way from baseline (or 0) to target. None when unmeasured or zero span."""
    if current is None:
        return None
    base = baseline if baseline is not None else Decimal(0)
    span = target - base
    if span == 0:
        return None
    return float((current - base) / span)


def attainment(
    *,
    current: Numeric | None,
    target: Numeric,
    direction: ObjectiveDirection,
    due_date: datetime.date,
    today: datetime.date,
) -> str:
    """'in_progress' before the due date; at/after, 'met' iff the target is reached, else 'missed'."""
    if today < due_date:
        return "in_progress"
    if current is None:
        return "missed"
    return "met" if _on_or_better(current, target, direction) else "missed"
```

- [ ] **Step 4: Run — expect PASS**

Run: `uv run pytest apps/api/tests/unit/test_objective_rules.py -v`

- [ ] **Step 5: Static gate + commit**

```bash
uv run ruff check apps/api && uv run ruff format apps/api && uv run mypy --strict apps/api/src
git add apps/api/src/easysynq_api/domain/objectives apps/api/tests/unit/test_objective_rules.py
git commit -m "feat(s-obj-1): pure direction-aware RAG / pct / attainment rule"
```

---

## Task 3: ORM models + registration

**Files:**
- Create: `apps/api/src/easysynq_api/db/models/quality_objective.py`
- Create: `apps/api/src/easysynq_api/db/models/objective_plan.py`
- Create: `apps/api/src/easysynq_api/db/models/kpi_measurement.py`
- Modify: `apps/api/src/easysynq_api/db/models/__init__.py`
- Create: `apps/api/tests/unit/test_objective_models.py`

Mirror `form_template.py` (shared-PK subtype) and `capa.py` (named FKs). All FKs named explicitly under 63 chars.

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/unit/test_objective_models.py
import pytest

pytestmark = pytest.mark.unit

from easysynq_api.db.models import KpiMeasurement, ObjectivePlan, QualityObjective


def test_quality_objective_columns() -> None:
    cols = set(QualityObjective.__table__.columns.keys())
    assert {
        "id", "org_id", "target_value", "unit", "baseline_value", "current_value",
        "direction", "at_risk_threshold", "due_date", "process_id", "policy_id",
        "created_at", "updated_at",
    } <= cols
    # owner is the base documented_information column, NOT duplicated here
    assert "owner_user_id" not in cols


def test_kpi_measurement_and_plan_columns() -> None:
    assert {"id", "org_id", "record_id", "objective_id", "process_id", "period",
            "value", "target_at_capture", "unit", "source"} <= set(
        KpiMeasurement.__table__.columns.keys()
    )
    assert {"id", "org_id", "objective_id", "action", "resource",
            "responsible_user_id", "due_date"} <= set(ObjectivePlan.__table__.columns.keys())
```

- [ ] **Step 2: Run — expect FAIL** (`ImportError: cannot import name 'QualityObjective'`)

Run: `uv run pytest apps/api/tests/unit/test_objective_models.py -v`

- [ ] **Step 3: Implement the three models**

```python
# apps/api/src/easysynq_api/db/models/quality_objective.py
"""The ``quality_objective`` subtype (S-obj-1, doc 14 §6, R3/R44). ``quality_objective.id`` IS the
``documented_information.id`` (kind=DOCUMENT, type OBJ) — the ``form_template`` precedent. The
commitment fields are the editable working copy frozen into ``metadata_snapshot`` at check-in;
``current_value`` is operational (rolled from KPI readings), never versioned. Owner = the BASE
``documented_information.owner_user_id`` (not duplicated)."""
from __future__ import annotations

import datetime
import uuid
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._objective_enums import ObjectiveDirection, objective_direction_enum


class QualityObjective(Base):
    __tablename__ = "quality_objective"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "documented_information.id",
            ondelete="RESTRICT",
            name="fk_quality_objective_id_documented_information",
        ),
        primary_key=True,
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT",
                   name="fk_quality_objective_org_id_organization"),
        nullable=False,
    )
    target_value: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    unit: Mapped[str] = mapped_column(Text, nullable=False)
    baseline_value: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    current_value: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    direction: Mapped[ObjectiveDirection] = mapped_column(objective_direction_enum, nullable=False)
    at_risk_threshold: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    due_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    process_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("process.id", ondelete="RESTRICT",
                   name="fk_quality_objective_process_id_process"),
        nullable=True,
    )
    policy_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documented_information.id", ondelete="RESTRICT",
                   name="fk_quality_objective_policy_id_doc_info"),
        nullable=True,
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=func.now()
    )
```

```python
# apps/api/src/easysynq_api/db/models/objective_plan.py
"""The ``objective_plan`` satellite (S-obj-1, doc 14 §6) — the 6.2 '…and planning to achieve them'
action rows (mutable; per-row history). FK → quality_objective.id (RESTRICT)."""
from __future__ import annotations

import datetime
import uuid

from sqlalchemy import Date, DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class ObjectivePlan(Base):
    __tablename__ = "objective_plan"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT",
                   name="fk_objective_plan_org_id_organization"),
        nullable=False,
    )
    objective_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("quality_objective.id", ondelete="RESTRICT", name="fk_objective_plan_objective_id"),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    resource: Mapped[str | None] = mapped_column(Text, nullable=True)
    responsible_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app_user.id", ondelete="RESTRICT",
                   name="fk_objective_plan_responsible_user_id_app_user"),
        nullable=True,
    )
    due_date: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=func.now()
    )
```

```python
# apps/api/src/easysynq_api/db/models/kpi_measurement.py
"""The ``kpi_measurement`` projection (S-obj-1, doc 14 §6 / §9.1.1) of a ``KPI_READING`` record. The
record (record_id) is the WORM evidence; this is the append-only queryable time-series. Insert-only
(REVOKE UPDATE,DELETE in 0049 — corrections create a new record + projection). ``target_at_capture``
freezes the objective's then-target so a later target edit can't rewrite a past verdict."""
from __future__ import annotations

import datetime
import uuid
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class KpiMeasurement(Base):
    __tablename__ = "kpi_measurement"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT",
                   name="fk_kpi_measurement_org_id_organization"),
        nullable=False,
    )
    record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("record.id", ondelete="RESTRICT", name="fk_kpi_measurement_record_id_record"),
        nullable=False,
    )
    objective_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("quality_objective.id", ondelete="RESTRICT",
                   name="fk_kpi_measurement_objective_id"),
        nullable=True,
    )
    process_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("process.id", ondelete="RESTRICT", name="fk_kpi_measurement_process_id_process"),
        nullable=True,
    )
    period: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    target_at_capture: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    unit: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

Then register all three in `db/models/__init__.py` (the SOLE place `Base.metadata` is populated — a missed import makes `alembic check` phantom-DROP and reds CI). Add imports next to the existing alphabetical block and the `__all__` entries:

```python
from .kpi_measurement import KpiMeasurement
from .objective_plan import ObjectivePlan
from .quality_objective import QualityObjective
```
```python
    "KpiMeasurement",
    "ObjectivePlan",
    "QualityObjective",
```

- [ ] **Step 4: Run — expect PASS**

Run: `uv run pytest apps/api/tests/unit/test_objective_models.py -v`

- [ ] **Step 5: Static gate + commit**

```bash
uv run ruff check apps/api && uv run ruff format apps/api && uv run mypy --strict apps/api/src
git add apps/api/src/easysynq_api/db/models/quality_objective.py apps/api/src/easysynq_api/db/models/objective_plan.py apps/api/src/easysynq_api/db/models/kpi_measurement.py apps/api/src/easysynq_api/db/models/__init__.py apps/api/tests/unit/test_objective_models.py
git commit -m "feat(s-obj-1): quality_objective / objective_plan / kpi_measurement ORM models"
```

---

## Task 4: Migration `0049` (enum · tables · REVOKE · OBJ type · event types)

**Files:**
- Create: `migrations/versions/0049_quality_objectives.py`

Mirror `0048_acknowledgements.py` exactly: autocommit `ADD VALUE` for the event types (none are USED in this migration's seeds, but follow the safe idempotent shape), fresh enum via `ENUM(*VALUES).create`, `create_table` with the named FKs/PK/UNIQUE from Task 3, REVOKE block, the `OBJ` document_type seed (the `0006` shape), resilient org lookup, strict-reverse downgrade.

- [ ] **Step 1: Implement the migration**

```python
# migrations/versions/0049_quality_objectives.py
"""S-obj-1 (doc 14 §6, R3/R44): the Quality Objectives family schema.

Creates ``quality_objective`` (kind=DOCUMENT subtype satellite), ``objective_plan`` (mutable action
rows), and the append-only ``kpi_measurement`` projection (REVOKE UPDATE,DELETE — the capa_stage/
acknowledgement house style). Adds the ``objective_direction`` enum + three additive OBJECTIVE_*
event types, and seeds the ``OBJ`` (Quality Objective) document_type. Rides the already-seeded
objective.*/kpi.* keys — NO new permission key, catalog stays 100.

Revision ID: 0049_quality_objectives
Revises: 0048_acknowledgements
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

from easysynq_api.db.models._objective_enums import OBJECTIVE_DIRECTION_VALUES

revision: str = "0049_quality_objectives"
down_revision: str | None = "0048_acknowledgements"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "easysynq_app"
_NEW_EVENT_TYPES = (
    "OBJECTIVE_MEASUREMENT_RECORDED",
    "OBJECTIVE_PLAN_ADDED",
    "OBJECTIVE_PLAN_REMOVED",
)
_OBJ_TYPE = ("OBJ", "Quality Objective", "L1_POLICY", False)  # (code, name, document_level, singleton)


def upgrade() -> None:
    # 1. Additive event-type values (IF NOT EXISTS → idempotent; not USED in this txn's seeds, but
    # the autocommit_block is safe + matches the 0048 shape).
    with op.get_context().autocommit_block():
        for value in _NEW_EVENT_TYPES:
            op.execute(f"ALTER TYPE event_type ADD VALUE IF NOT EXISTS '{value}'")

    bind = op.get_bind()

    # 2. The fresh enum (tuple from the ORM *_VALUES — the 0010 rule).
    postgresql.ENUM(*OBJECTIVE_DIRECTION_VALUES, name="objective_direction").create(
        bind, checkfirst=True
    )
    direction = postgresql.ENUM(name="objective_direction", create_type=False)

    # 3. quality_objective — the kind=DOCUMENT subtype satellite.
    op.create_table(
        "quality_objective",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_value", sa.Numeric(), nullable=False),
        sa.Column("unit", sa.Text(), nullable=False),
        sa.Column("baseline_value", sa.Numeric(), nullable=True),
        sa.Column("current_value", sa.Numeric(), nullable=True),
        sa.Column("direction", direction, nullable=False),
        sa.Column("at_risk_threshold", sa.Numeric(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("process_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("policy_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_quality_objective"),
        sa.ForeignKeyConstraint(["id"], ["documented_information.id"],
                                name="fk_quality_objective_id_documented_information", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"],
                                name="fk_quality_objective_org_id_organization", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["process_id"], ["process.id"],
                                name="fk_quality_objective_process_id_process", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["policy_id"], ["documented_information.id"],
                                name="fk_quality_objective_policy_id_doc_info", ondelete="RESTRICT"),
    )
    op.create_index("ix_quality_objective_process_id", "quality_objective", ["process_id"])

    # 4. objective_plan — mutable action rows.
    op.create_table(
        "objective_plan",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("objective_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("resource", sa.Text(), nullable=True),
        sa.Column("responsible_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_objective_plan"),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"],
                                name="fk_objective_plan_org_id_organization", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["objective_id"], ["quality_objective.id"],
                                name="fk_objective_plan_objective_id", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["responsible_user_id"], ["app_user.id"],
                                name="fk_objective_plan_responsible_user_id_app_user", ondelete="RESTRICT"),
    )
    op.create_index("ix_objective_plan_objective_id", "objective_plan", ["objective_id"])

    # 5. kpi_measurement — the append-only projection.
    op.create_table(
        "kpi_measurement",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("objective_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("process_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column("value", sa.Numeric(), nullable=False),
        sa.Column("target_at_capture", sa.Numeric(), nullable=False),
        sa.Column("unit", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_kpi_measurement"),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"],
                                name="fk_kpi_measurement_org_id_organization", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["record_id"], ["record.id"],
                                name="fk_kpi_measurement_record_id_record", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["objective_id"], ["quality_objective.id"],
                                name="fk_kpi_measurement_objective_id", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["process_id"], ["process.id"],
                                name="fk_kpi_measurement_process_id_process", ondelete="RESTRICT"),
    )
    op.create_index("ix_kpi_measurement_objective_id", "kpi_measurement", ["objective_id"])

    # 6. Least-privilege grants (pg_roles-guarded): kpi_measurement is append-only evidence.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON quality_objective TO {_APP_ROLE}';
                EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON objective_plan TO {_APP_ROLE}';
                EXECUTE 'GRANT SELECT, INSERT ON kpi_measurement TO {_APP_ROLE}';
                EXECUTE 'REVOKE UPDATE, DELETE ON kpi_measurement FROM {_APP_ROLE}';
            END IF;
        END $$;
        """
    )

    # 7. Seed the OBJ document_type for the DEFAULT org (resilient lookup — the 0045/0048 trap).
    org_id = bind.execute(
        sa.text("SELECT id FROM organization WHERE short_code = 'DEFAULT'")
    ).scalar_one_or_none()
    if org_id is None:
        org_id = bind.execute(sa.text("SELECT id FROM organization")).scalar_one()
    document_type_t = sa.table(
        "document_type",
        sa.column("org_id", postgresql.UUID(as_uuid=True)),
        sa.column("code", sa.Text),
        sa.column("name", sa.Text),
        sa.column("document_level", postgresql.ENUM(name="document_level", create_type=False)),
        sa.column("is_singleton", sa.Boolean),
    )
    code, name, level, singleton = _OBJ_TYPE
    bind.execute(
        pg_insert(document_type_t)
        .values(org_id=org_id, code=code, name=name, document_level=level, is_singleton=singleton)
        .on_conflict_do_nothing(index_elements=["org_id", "code"])
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM document_type WHERE code = :c"), {"c": _OBJ_TYPE[0]})
    op.drop_index("ix_kpi_measurement_objective_id", table_name="kpi_measurement")
    op.drop_table("kpi_measurement")
    op.drop_index("ix_objective_plan_objective_id", table_name="objective_plan")
    op.drop_table("objective_plan")
    op.drop_index("ix_quality_objective_process_id", table_name="quality_objective")
    op.drop_table("quality_objective")
    op.execute("DROP TYPE IF EXISTS objective_direction")
    # The event_type ADD VALUEs are irreversible in PG → no-op (the 0011/0048 precedent).
```

> **Resolved (verified):** `document_type` columns are `(org_id, code, name, document_level, is_singleton)` per `0006_seed_vault.py:63-84`; `document_level` is the PG enum `document_level` (`L1_POLICY|L2_PROCEDURE|L3_WORK_INSTRUCTION|L4_FORM`) — `L1_POLICY` is valid for OBJ. The `UNIQUE(org_id, code)` backs the `on_conflict_do_nothing`.

- [ ] **Step 2: Round-trip the migration — expect clean up↔down↔check**

Run: `/check-migrations` (or `just check-migrations`) — round-trips `0049` up↔down + `alembic check` on a throwaway PG16. Expected: PASS, no phantom diff.

- [ ] **Step 3: Static gate + commit**

```bash
uv run ruff check migrations && uv run ruff format migrations
git add migrations/versions/0049_quality_objectives.py
git commit -m "feat(s-obj-1): migration 0049 — objective tables + direction enum + OBJ type"
```

---

## Task 5: Service — `create_objective` + policy helper

**Files:**
- Create: `apps/api/src/easysynq_api/services/objectives/__init__.py`
- Create: `apps/api/src/easysynq_api/services/objectives/service.py`
- Create: `apps/api/tests/integration/test_quality_objectives.py`

`create_objective` reuses the public `create_document` (which commits the base doc), then adds the satellite + a `clause_mapping` to clause `6.2`, then commits. Validate measurable-by-construction (pydantic covers required fields; the service validates `policy_id` against the current Effective Quality Policy). Mirror `services/vault/service.py:149-199` for the create-document call and `clause_mapping.py` for the mapping insert.

- [ ] **Step 1: Write the failing integration test**

```python
# apps/api/tests/integration/test_quality_objectives.py
"""S-obj-1 integration: objectives ride the seeded objective.*/kpi.* keys (PROCESS-scoped); the test
actor has no role assignment, so each test grants the keys it needs at SYSTEM scope (the test_capa /
test_audits precedent — a SYSTEM grant matches any resource)."""
import datetime
import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models.authz_grant import PermissionOverride
from easysynq_api.db.models.permission import Permission
from easysynq_api.db.models.quality_objective import QualityObjective
from easysynq_api.db.models.scope import Scope
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.domain.authz.types import Effect, ScopeLevel

from .test_vault import _auth, _ensure_user  # the established integration helpers (test_capa precedent)

pytestmark = pytest.mark.integration

_OBJ_KEYS = ("objective.read", "objective.manage", "kpi.read", "kpi.record")


async def _grant(subject: str, keys: tuple[str, ...]) -> uuid.UUID:
    """Grant keys at SYSTEM scope via PermissionOverride (test_capa.py:55-78, verbatim). A SYSTEM
    override is a real Scope ROW (level=SYSTEM) referenced by scope_id — NOT an inline JSON scope."""
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)  # create-or-get the JIT app_user row by keycloak_subject
        for key in keys:
            perm = (await s.execute(select(Permission).where(Permission.key == key))).scalar_one()
            scope = Scope(org_id=user.org_id, level=ScopeLevel.SYSTEM)
            s.add(scope)
            await s.flush()  # populate scope.id
            s.add(PermissionOverride(
                org_id=user.org_id, user_id=user.id, permission_id=perm.id,
                effect=Effect.ALLOW, scope_id=scope.id,
            ))
        await s.commit()
        return user.id


async def test_create_objective_is_a_document_subtype_mapped_to_6_2(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"obj-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _OBJ_KEYS)
    r = await app_client.post(
        "/api/v1/objectives",
        headers=h,
        json={
            "title": "Raise on-time delivery to 98%",
            "target_value": "98",
            "unit": "%",
            "direction": "HIGHER_IS_BETTER",
            "due_date": "2026-12-31",
            "baseline_value": "90",
            "at_risk_threshold": "95",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["unit"] == "%"
    assert body["rag"] == "unmeasured"  # no reading yet
    assert body["identifier"].startswith("OBJ-")
    # the satellite row exists + the base is kind=DOCUMENT type OBJ
    async with get_sessionmaker()() as s:
        qo = (await s.execute(
            select(QualityObjective).where(QualityObjective.id == uuid.UUID(body["id"]))
        )).scalar_one()
        assert qo.target_value == 98


async def test_create_rejects_unknown_policy_id(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"obj-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _OBJ_KEYS)
    r = await app_client.post(
        "/api/v1/objectives",
        headers=h,
        json={
            "title": "Bad policy link", "target_value": "5", "unit": "count",
            "direction": "LOWER_IS_BETTER", "due_date": "2026-12-31",
            "policy_id": str(uuid.uuid4()),
        },
    )
    assert r.status_code == 422, r.text
```

> **Resolved (verified against the live code):** the `_grant`/`_auth`/`_ensure_user` shapes above are the real `test_capa.py:55-78` pattern — `PermissionOverride(user_id=…, effect=Effect.ALLOW, scope_id=<a Scope(level=SYSTEM) row>)` (there is **no** JSON `scope` column), and `AppUser.keycloak_subject` (not `profile_sub`). `_ensure_user` create-or-gets the JIT row so `_grant` is safe to call before the first request. Import `_auth`/`_ensure_user` from `.test_vault`.

- [ ] **Step 2: Run — expect FAIL** (404/no route yet)

Run: `uv run pytest -m integration apps/api/tests/integration/test_quality_objectives.py -v` (Docker/CI)

- [ ] **Step 3: Implement the service**

```python
# apps/api/src/easysynq_api/services/objectives/service.py
"""Quality Objectives service (S-obj-1) — the txn owner. ``create_objective`` reuses the vault
``create_document`` (kind=DOCUMENT, type OBJ), then adds the satellite + a clause_mapping to 6.2.
``record_measurement`` (Task 6) captures a KPI_READING record + projection + rolls up current_value."""
from __future__ import annotations

import datetime
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._objective_enums import ObjectiveDirection
from ...db.models.clause import Clause
from ...db.models.clause_mapping import ClauseMapping
from ...db.models.documented_information import DocumentedInformation
from ...db.models._vault_enums import DocumentCurrentState  # DocumentCurrentState.Effective
from ...db.models.document_type import DocumentType
from ...db.models.quality_objective import QualityObjective
from ...db.models.app_user import AppUser
from ...problems import ProblemException
from ..vault import VaultAuditSink, create_document  # confirm create_document is exported


async def _obj_document_type_id(session: AsyncSession, org_id: uuid.UUID) -> uuid.UUID:
    dt = (await session.execute(
        select(DocumentType).where(DocumentType.org_id == org_id, DocumentType.code == "OBJ")
    )).scalar_one_or_none()
    if dt is None:
        raise ProblemException(status=422, code="validation_error",
                               title="OBJ document_type is not seeded")
    return dt.id


async def current_effective_policy(
    session: AsyncSession, org_id: uuid.UUID
) -> DocumentedInformation | None:
    """The single Effective Quality Policy (POL singleton, R25), or None."""
    return (await session.execute(
        select(DocumentedInformation)
        .join(DocumentType, DocumentedInformation.document_type_id == DocumentType.id)
        .where(
            DocumentedInformation.org_id == org_id,
            DocumentType.code == "POL",
            DocumentedInformation.current_state == DocumentCurrentState.Effective,
        )
    )).scalar_one_or_none()


async def create_objective(
    session: AsyncSession,
    sink: VaultAuditSink,
    actor: AppUser,
    *,
    title: str,
    target_value: Decimal,
    unit: str,
    direction: ObjectiveDirection,
    due_date: datetime.date,
    baseline_value: Decimal | None = None,
    at_risk_threshold: Decimal | None = None,
    process_id: uuid.UUID | None = None,
    policy_id: uuid.UUID | None = None,
    area_code: str | None = None,
    folder_path: str | None = None,
    classification: str = "Internal",
) -> QualityObjective:
    # Measurable-by-construction: title/target/unit/direction/due are required by the caller/pydantic.
    if policy_id is not None:
        eff = await current_effective_policy(session, actor.org_id)
        if eff is None or eff.id != policy_id:
            raise ProblemException(
                status=422, code="validation_error",
                title="policy_id must be the current Effective Quality Policy",
            )
    dt_id = await _obj_document_type_id(session, actor.org_id)
    # create_document commits the base doc (the form_template two-step precedent).
    doc = await create_document(
        session, sink, actor,
        title=title, document_type_id=dt_id,
        area_code=area_code, folder_path=folder_path, classification=classification,
    )
    qo = QualityObjective(
        id=doc.id, org_id=actor.org_id,
        target_value=target_value, unit=unit, baseline_value=baseline_value,
        current_value=None, direction=direction, at_risk_threshold=at_risk_threshold,
        due_date=due_date, process_id=process_id, policy_id=policy_id,
    )
    session.add(qo)
    # Auto-map to clause 6.2 so the ★ checklist resolves on release.
    clause_6_2 = (await session.execute(
        select(Clause).where(Clause.number == "6.2")
    )).scalar_one_or_none()
    if clause_6_2 is not None:
        session.add(ClauseMapping(
            org_id=actor.org_id, framework_id=doc.framework_id,
            clause_id=clause_6_2.id, documented_information_id=doc.id,
            is_requirement_level=True, created_by=actor.id,
        ))
    await session.commit()
    await session.refresh(qo)
    return qo
```

> **Resolved (verified):** `create_document`/`VaultAuditSink`/`get_vault_audit_sink` ARE exported from `services/vault/__init__.py`; `ClauseMapping(org_id, framework_id, clause_id, documented_information_id, is_requirement_level, created_by)` matches exactly and `Clause.number == "6.2"` is correct (`created_by` is NOT NULL — pass `actor.id`). `create_document` returns the doc with `framework_id` populated, so the `ClauseMapping.framework_id=doc.framework_id` is safe. The create endpoint (Task 9) supplies the `VaultAuditSink` via `get_vault_audit_sink`.

Add a re-export `services/objectives/__init__.py`:
```python
from .service import create_objective, current_effective_policy

__all__ = ["create_objective", "current_effective_policy"]
```

- [ ] **Step 4: Implementation continues in Task 9** (the route). Re-run after Task 9 — expect PASS.

- [ ] **Step 5: Static gate + commit**

```bash
uv run ruff check apps/api && uv run ruff format apps/api && uv run mypy --strict apps/api/src
git add apps/api/src/easysynq_api/services/objectives apps/api/tests/integration/test_quality_objectives.py
git commit -m "feat(s-obj-1): create_objective service + policy-consistency + 6.2 auto-map"
```

---

## Task 6: Service — `record_measurement` + rollup

**Files:**
- Modify: `apps/api/src/easysynq_api/services/objectives/service.py`
- Modify: `apps/api/tests/integration/test_quality_objectives.py` (add tests)

Mirror `services/records/service.py:421` (`capture_record(..., _commit=False)`) + the S-drift-1 `FOR UPDATE`+`populate_existing` rollup. The KPI reading's `form_field_values` carries the structured payload; it is captured as an **ad-hoc** record (**no** `source_document_id` — the R21 trap; see the resolved note below), and the objective linkage lives on `kpi_measurement.objective_id`.

- [ ] **Step 1: Write the failing test**

```python
# add to test_quality_objectives.py
async def test_record_measurements_roll_up_latest_period_wins(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"obj-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _OBJ_KEYS)
    obj = (await app_client.post("/api/v1/objectives", headers=h, json={
        "title": "Cut complaints to 5/mo", "target_value": "5", "unit": "count",
        "direction": "LOWER_IS_BETTER", "due_date": "2026-12-31", "at_risk_threshold": "10",
    })).json()
    oid = obj["id"]
    # two readings, out of order — current_value must reflect the LATEST period
    r1 = await app_client.post(f"/api/v1/objectives/{oid}/measurements", headers=h,
        json={"period": "2026-03-31", "value": "12", "unit": "count"})
    assert r1.status_code == 201, r1.text
    r2 = await app_client.post(f"/api/v1/objectives/{oid}/measurements", headers=h,
        json={"period": "2026-06-30", "value": "8", "unit": "count"})
    assert r2.status_code == 201, r2.text
    # insert an older period AFTER — must NOT clobber current_value
    await app_client.post(f"/api/v1/objectives/{oid}/measurements", headers=h,
        json={"period": "2026-01-31", "value": "20", "unit": "count"})
    detail = (await app_client.get(f"/api/v1/objectives/{oid}", headers=h)).json()
    assert detail["current_value"] == "8"   # the 2026-06-30 reading
    assert detail["rag"] == "amber"          # 8 is between target 5 and threshold 10
    hist = (await app_client.get(f"/api/v1/objectives/{oid}/measurements", headers=h)).json()
    assert len(hist["data"]) == 3
    assert all(m["target_at_capture"] == "5" for m in hist["data"])  # frozen at capture
```

- [ ] **Step 2: Run — expect FAIL**

Run: `uv run pytest -m integration apps/api/tests/integration/test_quality_objectives.py::test_record_measurements_roll_up_latest_period_wins -v`

- [ ] **Step 3: Implement** (append to `service.py`)

```python
from sqlalchemy import desc

from ...db.models._audit_enums import ActorType, AuditObjectType, EventType
from ...db.models.audit_event import AuditEvent
from ...db.models.kpi_measurement import KpiMeasurement
from ..records import capture_record  # re-exported from services/records/__init__.py (verified)


async def record_measurement(
    session: AsyncSession,
    actor: AppUser,
    *,
    objective_id: uuid.UUID,
    period: datetime.date,
    value: Decimal,
    unit: str,
    source: str | None = None,
) -> KpiMeasurement:
    # Lock + freshen the objective (the authz resolver already session.get-loaded it — populate_existing
    # or we roll up over stale attributes; the S-drift-1 trap).
    qo = (await session.execute(
        select(QualityObjective)
        .where(QualityObjective.id == objective_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )).scalar_one_or_none()
    if qo is None:
        raise ProblemException(status=404, code="not_found", title="Objective not found")

    target_at_capture = qo.target_value
    # Evidence-grade reading: an AD-HOC KPI_READING record (the capture_complaint precedent — NO
    # source_document_id, so no R21 version-pin is required; a Draft objective has no version to pin).
    # The objective linkage + the frozen target live on kpi_measurement; _commit=False composes the
    # record + projection + rollup in ONE transaction.
    record = await capture_record(
        session, actor,
        record_type="KPI_READING",  # already a RecordType member (verified) — no enum addition needed
        title=f"KPI reading {period.isoformat()} ({qo.id})",
        form_field_values={
            "objective_id": str(objective_id), "period": period.isoformat(),
            "value": str(value), "target_at_capture": str(target_at_capture),
            "unit": unit, "source": source,
        },
        _commit=False,
    )
    measurement = KpiMeasurement(
        org_id=actor.org_id, record_id=record.id, objective_id=objective_id,
        process_id=qo.process_id, period=period, value=value,
        target_at_capture=target_at_capture, unit=unit, source=source,
    )
    session.add(measurement)
    await session.flush()

    # Roll up current_value = the value of the MAX-period reading (out-of-order safe).
    latest_value = (await session.execute(
        select(KpiMeasurement.value)
        .where(KpiMeasurement.objective_id == objective_id)
        .order_by(desc(KpiMeasurement.period), desc(KpiMeasurement.created_at))
        .limit(1)
    )).scalar_one()
    qo.current_value = latest_value

    # Audit (object_type=document, scope_ref=identifier — R39). Mirror services/ack/sweep.py:67-82
    # field-for-field: occurred_at + actor_type are NOT NULL with no server default.
    base = await session.get(DocumentedInformation, objective_id)
    session.add(AuditEvent(
        org_id=actor.org_id,
        occurred_at=datetime.datetime.now(datetime.UTC),
        actor_id=actor.id,
        actor_type=ActorType.user,
        event_type=EventType.OBJECTIVE_MEASUREMENT_RECORDED,
        object_type=AuditObjectType.document,
        object_id=objective_id,
        scope_ref=base.identifier if base else None,
        after={"period": period.isoformat(), "value": str(value),
               "current_value": str(latest_value)},
    ))
    await session.commit()
    await session.refresh(measurement)
    return measurement
```

> **Resolved (verified):** `capture_record` is re-exported from `services/records/__init__.py`; its signature accepts `form_field_values: dict` as the data-only payload with `evidence=()` defaulted and `_commit=False` (no blob needed for a numeric reading). **The R21 trap is why `source_document_id` is omitted** — `_resolve_source_version` 422s any non-FRM source document with no version pin, and a Draft objective has none; an ad-hoc record (no source pin) is the `capture_complaint` precedent. `RecordType.KPI_READING` already exists (no enum/migration addition). The `AuditEvent` fields above are the verified `sweep.py:67-82` shape (`actor_id`/`actor_type`/`occurred_at` are required; `object_type` is the `AuditObjectType` enum). `datetime` is already imported at the top of `service.py`.

- [ ] **Step 4: Run — expect PASS** (after Task 9 wires the route; re-run then)

- [ ] **Step 5: Static gate + commit**

```bash
uv run ruff check apps/api && uv run ruff format apps/api && uv run mypy --strict apps/api/src
git add apps/api/src/easysynq_api/services/objectives/service.py apps/api/tests/integration/test_quality_objectives.py
git commit -m "feat(s-obj-1): record_measurement — KPI_READING record + projection + rollup"
```

---

## Task 7: Service — `objective_plan` CRUD

**Files:**
- Modify: `apps/api/src/easysynq_api/services/objectives/service.py`
- Modify: `apps/api/tests/integration/test_quality_objectives.py`

- [ ] **Step 1: Write the failing test**

```python
# add to test_quality_objectives.py
async def test_objective_plan_add_and_remove(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"obj-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _OBJ_KEYS)
    oid = (await app_client.post("/api/v1/objectives", headers=h, json={
        "title": "Plan-bearing objective", "target_value": "100", "unit": "%",
        "direction": "HIGHER_IS_BETTER", "due_date": "2026-12-31",
    })).json()["id"]
    add = await app_client.post(f"/api/v1/objectives/{oid}/plans", headers=h,
        json={"action": "Run weekly stand-ups", "resource": "QA team"})
    assert add.status_code == 201, add.text
    plan_id = add.json()["id"]
    detail = (await app_client.get(f"/api/v1/objectives/{oid}", headers=h)).json()
    assert len(detail["plans"]) == 1
    rm = await app_client.delete(f"/api/v1/objectives/{oid}/plans/{plan_id}", headers=h)
    assert rm.status_code == 204
    detail2 = (await app_client.get(f"/api/v1/objectives/{oid}", headers=h)).json()
    assert detail2["plans"] == []
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement** (append to `service.py`)

```python
from ...db.models.objective_plan import ObjectivePlan


async def add_objective_plan(
    session: AsyncSession, actor: AppUser, *,
    objective_id: uuid.UUID, action: str,
    resource: str | None = None, responsible_user_id: uuid.UUID | None = None,
    due_date: datetime.date | None = None,
) -> ObjectivePlan:
    qo = await session.get(QualityObjective, objective_id)
    if qo is None:
        raise ProblemException(status=404, code="not_found", title="Objective not found")
    plan = ObjectivePlan(
        org_id=actor.org_id, objective_id=objective_id, action=action,
        resource=resource, responsible_user_id=responsible_user_id, due_date=due_date,
    )
    session.add(plan)
    await session.flush()
    base = await session.get(DocumentedInformation, objective_id)
    session.add(AuditEvent(
        org_id=actor.org_id, occurred_at=datetime.datetime.now(datetime.UTC),
        actor_id=actor.id, actor_type=ActorType.user,
        event_type=EventType.OBJECTIVE_PLAN_ADDED,
        object_type=AuditObjectType.document, object_id=objective_id,
        scope_ref=base.identifier if base else None,
        after={"plan_id": str(plan.id), "action": action},
    ))
    await session.commit()
    await session.refresh(plan)
    return plan


async def remove_objective_plan(
    session: AsyncSession, actor: AppUser, *, objective_id: uuid.UUID, plan_id: uuid.UUID
) -> None:
    plan = await session.get(ObjectivePlan, plan_id)
    if plan is None or plan.objective_id != objective_id:
        raise ProblemException(status=404, code="not_found", title="Plan not found")
    await session.delete(plan)
    base = await session.get(DocumentedInformation, objective_id)
    session.add(AuditEvent(
        org_id=actor.org_id, occurred_at=datetime.datetime.now(datetime.UTC),
        actor_id=actor.id, actor_type=ActorType.user,
        event_type=EventType.OBJECTIVE_PLAN_REMOVED,
        object_type=AuditObjectType.document, object_id=objective_id,
        scope_ref=base.identifier if base else None,
        after={"plan_id": str(plan_id)},
    ))
    await session.commit()
```

- [ ] **Step 4: Run — expect PASS** (after Task 9)
- [ ] **Step 5: Static gate + commit**

```bash
git add apps/api/src/easysynq_api/services/objectives/service.py apps/api/tests/integration/test_quality_objectives.py
git commit -m "feat(s-obj-1): objective_plan add/remove service"
```

---

## Task 8: Queries — list / get / measurements / scorecard

**Files:**
- Create: `apps/api/src/easysynq_api/services/objectives/queries.py`
- Modify: `apps/api/tests/integration/test_quality_objectives.py`

Reads join the satellite to `documented_information` for `identifier`/`title`/`current_state`. The RAG/pct/attainment are computed in the serializer (Task 9) from the pure rule — `queries.py` returns rows + base fields only.

- [ ] **Step 1: Write the failing test (scorecard rollup)**

```python
# add to test_quality_objectives.py
async def test_scorecard_rollup_counts_by_rag(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"obj-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _OBJ_KEYS)
    oid = (await app_client.post("/api/v1/objectives", headers=h, json={
        "title": "Green one", "target_value": "90", "unit": "%",
        "direction": "HIGHER_IS_BETTER", "due_date": "2026-12-31", "at_risk_threshold": "80",
    })).json()["id"]
    await app_client.post(f"/api/v1/objectives/{oid}/measurements", headers=h,
        json={"period": "2026-06-30", "value": "95", "unit": "%"})
    sc = (await app_client.get("/api/v1/objectives/scorecard", headers=h)).json()
    assert sc["total"] >= 1
    assert sc["on_target"] >= 1
    assert sc["by_rag"]["green"] >= 1
    assert any(row["id"] == oid and row["rag"] == "green" for row in sc["objectives"])
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement**

```python
# apps/api/src/easysynq_api/services/objectives/queries.py
"""Quality Objectives read queries (S-obj-1). Returns rows + the joined base identity; RAG/pct are
computed in the serializer from the pure rule."""
from __future__ import annotations

import uuid

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models.documented_information import DocumentedInformation
from ...db.models.kpi_measurement import KpiMeasurement
from ...db.models.objective_plan import ObjectivePlan
from ...db.models.quality_objective import QualityObjective

ObjectiveRow = tuple[QualityObjective, str, str, str]  # (qo, identifier, title, current_state)


async def list_objectives(
    session: AsyncSession, org_id: uuid.UUID, *, process_id: uuid.UUID | None = None
) -> list[ObjectiveRow]:
    stmt = (
        select(
            QualityObjective,
            DocumentedInformation.identifier,
            DocumentedInformation.title,
            DocumentedInformation.current_state,
        )
        .join(DocumentedInformation, QualityObjective.id == DocumentedInformation.id)
        .where(QualityObjective.org_id == org_id)
        .order_by(DocumentedInformation.identifier)
    )
    if process_id is not None:
        stmt = stmt.where(QualityObjective.process_id == process_id)
    return [tuple(r) for r in (await session.execute(stmt)).all()]  # type: ignore[misc]


async def get_objective(session: AsyncSession, objective_id: uuid.UUID) -> ObjectiveRow | None:
    row = (await session.execute(
        select(
            QualityObjective, DocumentedInformation.identifier,
            DocumentedInformation.title, DocumentedInformation.current_state,
        )
        .join(DocumentedInformation, QualityObjective.id == DocumentedInformation.id)
        .where(QualityObjective.id == objective_id)
    )).first()
    return tuple(row) if row is not None else None  # type: ignore[return-value]


async def list_plans(session: AsyncSession, objective_id: uuid.UUID) -> list[ObjectivePlan]:
    return list((await session.execute(
        select(ObjectivePlan).where(ObjectivePlan.objective_id == objective_id)
        .order_by(ObjectivePlan.created_at)
    )).scalars())


async def list_measurements(session: AsyncSession, objective_id: uuid.UUID) -> list[KpiMeasurement]:
    return list((await session.execute(
        select(KpiMeasurement).where(KpiMeasurement.objective_id == objective_id)
        .order_by(desc(KpiMeasurement.period), desc(KpiMeasurement.created_at))
    )).scalars())
```

Add `list_objectives`/`get_objective`/`list_plans`/`list_measurements` to the `services/objectives/__init__.py` re-export. The scorecard rollup is assembled in the route serializer (Task 9) from `list_objectives` + the pure rule.

- [ ] **Step 4: Run — expect PASS** (after Task 9)
- [ ] **Step 5: Static gate + commit**

```bash
git add apps/api/src/easysynq_api/services/objectives/queries.py apps/api/src/easysynq_api/services/objectives/__init__.py apps/api/tests/integration/test_quality_objectives.py
git commit -m "feat(s-obj-1): objective read queries (list/get/plans/measurements)"
```

---

## Task 9: Router — `api/objectives.py` (serializers · resolver · gates · mount)

**Files:**
- Create: `apps/api/src/easysynq_api/api/objectives.py`
- Modify: `apps/api/src/easysynq_api/main.py`
- Modify: `apps/api/tests/integration/test_quality_objectives.py` (the authz-matrix test)

Mirror `api/capa.py`: request bodies, private serializers, `_process_scope`/`_objective_scope`, `require(...)` gates (create = in-handler `enforce` with body `process_id`; path-id writes via the resolver), and the router mount. The serializer computes RAG/pct/attainment from the pure rule.

- [ ] **Step 1: Write the failing authz-matrix test**

```python
# add to test_quality_objectives.py
async def test_objective_read_requires_key(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"obj-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    # NO grant → calm 403 on the read
    r = await app_client.get("/api/v1/objectives", headers=h)
    assert r.status_code == 403


async def test_catalog_count_unchanged_no_new_key(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    async with get_sessionmaker()() as s:
        n = len((await s.execute(select(Permission))).scalars().all())
    assert n == 100  # S-obj-1 adds NO permission key
```

- [ ] **Step 2: Run — expect FAIL** (no route / 404)

- [ ] **Step 3: Implement the router**

```python
# apps/api/src/easysynq_api/api/objectives.py
"""The Quality Objectives surface (S-obj-1; clause 6.2). Rides the seeded objective.*/kpi.* keys
(PROCESS-scoped). create = in-handler enforce on the body process_id (the raise_capa precedent);
path-id writes use the _objective_scope resolver (the _capa_scope precedent). Reads gate at the key +
an org-scoped query. RAG/pct/attainment are computed in the serializer from the pure rule."""
from __future__ import annotations

import datetime
import uuid
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_user
from ..db.models._objective_enums import ObjectiveDirection
from ..db.models.app_user import AppUser
from ..db.models.kpi_measurement import KpiMeasurement
from ..db.models.objective_plan import ObjectivePlan
from ..db.models.quality_objective import QualityObjective
from ..db.session import get_session
from ..domain.authz import ResourceContext
from ..domain.objectives.rules import attainment, pct_toward_target, rag_status
from ..services.authz import AuthzAuditSink, enforce, get_authz_audit_sink, require
from ..services.objectives import (
    add_objective_plan,
    create_objective,
    get_objective,
    list_measurements,
    list_objectives,
    list_plans,
    record_measurement,
    remove_objective_plan,
)
from ..services.objectives import queries as obj_queries
from ..services.vault import VaultAuditSink, get_vault_audit_sink  # confirm exports

router = APIRouter(prefix="/api/v1", tags=["objectives"])


# --- request bodies ---
class ObjectiveCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    target_value: Decimal
    unit: str = Field(min_length=1, max_length=50)
    direction: ObjectiveDirection
    due_date: datetime.date
    baseline_value: Decimal | None = None
    at_risk_threshold: Decimal | None = None
    process_id: uuid.UUID | None = None
    policy_id: uuid.UUID | None = None


class MeasurementCreate(BaseModel):
    period: datetime.date
    value: Decimal
    unit: str = Field(min_length=1, max_length=50)
    source: str | None = Field(default=None, max_length=300)


class PlanCreate(BaseModel):
    action: str = Field(min_length=1, max_length=2000)
    resource: str | None = Field(default=None, max_length=500)
    responsible_user_id: uuid.UUID | None = None
    due_date: datetime.date | None = None


# --- serializers ---
def _measurement(m: KpiMeasurement) -> dict[str, Any]:
    return {
        "id": str(m.id), "objective_id": str(m.objective_id) if m.objective_id else None,
        "record_id": str(m.record_id), "period": m.period.isoformat(),
        "value": str(m.value), "target_at_capture": str(m.target_at_capture),
        "unit": m.unit, "source": m.source, "created_at": m.created_at.isoformat(),
    }


def _plan(p: ObjectivePlan) -> dict[str, Any]:
    return {
        "id": str(p.id), "objective_id": str(p.objective_id), "action": p.action,
        "resource": p.resource,
        "responsible_user_id": str(p.responsible_user_id) if p.responsible_user_id else None,
        "due_date": p.due_date.isoformat() if p.due_date else None,
    }


def _objective(
    qo: QualityObjective, *, identifier: str, title: str, current_state: str, today: datetime.date,
    plans: list[ObjectivePlan] | None = None,
) -> dict[str, Any]:
    rag = rag_status(current=qo.current_value, target=qo.target_value,
                     direction=qo.direction, at_risk_threshold=qo.at_risk_threshold)
    return {
        "id": str(qo.id), "identifier": identifier, "title": title,
        "current_state": current_state.value if hasattr(current_state, "value") else str(current_state),
        "target_value": str(qo.target_value), "unit": qo.unit,
        "baseline_value": str(qo.baseline_value) if qo.baseline_value is not None else None,
        "current_value": str(qo.current_value) if qo.current_value is not None else None,
        "direction": qo.direction.value,
        "at_risk_threshold": str(qo.at_risk_threshold) if qo.at_risk_threshold is not None else None,
        "due_date": qo.due_date.isoformat(),
        "process_id": str(qo.process_id) if qo.process_id else None,
        "policy_id": str(qo.policy_id) if qo.policy_id else None,
        "rag": rag,
        "pct_toward_target": pct_toward_target(
            current=qo.current_value, target=qo.target_value, baseline=qo.baseline_value),
        "attainment": attainment(current=qo.current_value, target=qo.target_value,
                                 direction=qo.direction, due_date=qo.due_date, today=today),
        "plans": [_plan(p) for p in (plans or [])],
    }


# --- scope helpers ---
def _process_scope(process_id: uuid.UUID | None) -> ResourceContext:
    if process_id is None:
        return ResourceContext.system()
    return ResourceContext(process_ids=frozenset({str(process_id)}))


async def _objective_scope(request: Request, session: AsyncSession) -> ResourceContext:
    raw = request.path_params.get("objective_id")
    if not raw:
        return ResourceContext.system()
    try:
        oid = uuid.UUID(str(raw))
    except ValueError:
        return ResourceContext.system()
    qo = await session.get(QualityObjective, oid)
    if qo is None or qo.process_id is None:
        return ResourceContext.system()
    return ResourceContext(process_ids=frozenset({str(qo.process_id)}))


_objective_read = require("objective.read")
_kpi_read = require("kpi.read", async_scope_resolver=_objective_scope)
_objective_manage_path = require("objective.manage", async_scope_resolver=_objective_scope)
_kpi_record = require("kpi.record", async_scope_resolver=_objective_scope)


def _today() -> datetime.date:
    return datetime.date.today()


# --- endpoints ---
@router.post("/objectives", status_code=status.HTTP_201_CREATED)
async def create_objective_endpoint(
    body: ObjectiveCreate, request: Request,
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    authz_sink: AuthzAuditSink = Depends(get_authz_audit_sink),
    vault_sink: VaultAuditSink = Depends(get_vault_audit_sink),
) -> dict[str, Any]:
    await enforce(session, authz_sink, request, caller, "objective.manage",
                  _process_scope(body.process_id))
    qo = await create_objective(
        session, vault_sink, caller,
        title=body.title, target_value=body.target_value, unit=body.unit,
        direction=body.direction, due_date=body.due_date, baseline_value=body.baseline_value,
        at_risk_threshold=body.at_risk_threshold, process_id=body.process_id, policy_id=body.policy_id,
    )
    row = await get_objective(session, qo.id)
    assert row is not None
    _, ident, title, state = row
    return _objective(qo, identifier=ident, title=title, current_state=state, today=_today())


@router.get("/objectives")
async def list_objectives_endpoint(
    process_id: uuid.UUID | None = None,
    caller: AppUser = Depends(_objective_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    rows = await list_objectives(session, caller.org_id, process_id=process_id)
    today = _today()
    return {"data": [
        _objective(qo, identifier=i, title=t, current_state=s, today=today)
        for qo, i, t, s in rows
    ]}


@router.get("/objectives/scorecard")
async def scorecard_endpoint(
    process_id: uuid.UUID | None = None,
    caller: AppUser = Depends(_objective_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    rows = await list_objectives(session, caller.org_id, process_id=process_id)
    today = _today()
    serialized = [_objective(qo, identifier=i, title=t, current_state=s, today=today)
                  for qo, i, t, s in rows]
    by_rag = {"green": 0, "amber": 0, "red": 0, "unmeasured": 0}
    for o in serialized:
        by_rag[o["rag"]] += 1
    return {
        "total": len(serialized),
        "on_target": by_rag["green"],
        "by_rag": by_rag,
        "objectives": serialized,
    }


@router.get("/objectives/{objective_id}")
async def get_objective_endpoint(
    objective_id: uuid.UUID,
    caller: AppUser = Depends(_objective_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    row = await get_objective(session, objective_id)
    if row is None:
        from ..problems import ProblemException
        raise ProblemException(status=404, code="not_found", title="Objective not found")
    qo, ident, title, state = row
    plans = await list_plans(session, objective_id)
    return _objective(qo, identifier=ident, title=title, current_state=state,
                      today=_today(), plans=plans)


@router.post("/objectives/{objective_id}/measurements", status_code=status.HTTP_201_CREATED)
async def record_measurement_endpoint(
    objective_id: uuid.UUID, body: MeasurementCreate,
    caller: AppUser = Depends(_kpi_record),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    m = await record_measurement(session, caller, objective_id=objective_id,
                                 period=body.period, value=body.value, unit=body.unit,
                                 source=body.source)
    return _measurement(m)


@router.get("/objectives/{objective_id}/measurements")
async def list_measurements_endpoint(
    objective_id: uuid.UUID,
    caller: AppUser = Depends(_kpi_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return {"data": [_measurement(m) for m in await list_measurements(session, objective_id)]}


@router.post("/objectives/{objective_id}/plans", status_code=status.HTTP_201_CREATED)
async def add_plan_endpoint(
    objective_id: uuid.UUID, body: PlanCreate,
    caller: AppUser = Depends(_objective_manage_path),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    plan = await add_objective_plan(session, caller, objective_id=objective_id, action=body.action,
                                    resource=body.resource,
                                    responsible_user_id=body.responsible_user_id,
                                    due_date=body.due_date)
    return _plan(plan)


@router.delete("/objectives/{objective_id}/plans/{plan_id}",
               status_code=status.HTTP_204_NO_CONTENT)
async def remove_plan_endpoint(
    objective_id: uuid.UUID, plan_id: uuid.UUID,
    caller: AppUser = Depends(_objective_manage_path),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await remove_objective_plan(session, caller, objective_id=objective_id, plan_id=plan_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
```

> **Route ordering:** `/objectives/scorecard` is declared BEFORE `/objectives/{objective_id}` so the literal isn't shadowed by the `{objective_id}` str-convertor (the S-pack-2 static-before-param lesson). Keep that order. Add a resolution unit test if unsure.

Mount in `main.py` using the existing alias idiom (match the ~24 other `as <name>_router` mounts) — add the import near the other `.api.*` router imports and the `include_router` beside the content routers inside `create_app()`:
```python
from .api.objectives import router as objectives_router
...
    app.include_router(objectives_router)  # S-obj-1: clause-6.2 Quality Objectives (objective.*/kpi.* keys)
```

> **Resolved (verified):** `get_vault_audit_sink`/`VaultAuditSink` are exported from `services/vault/__init__.py`; `from ..services.authz import AuthzAuditSink, enforce, get_authz_audit_sink, require` is byte-identical to `api/capa.py:40`; `main.py` mounts every router via `app.include_router(<alias>)` in `create_app()`. The in-handler `enforce` (create) + `require(async_scope_resolver=…)` (path writes) split matches the live capa pattern.

- [ ] **Step 4: Run the WHOLE integration file — expect PASS** (this completes Tasks 5–9)

Run: `uv run pytest -m integration apps/api/tests/integration/test_quality_objectives.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Full static gate + commit**

```bash
uv run ruff check apps/api && uv run ruff format apps/api && uv run mypy --strict apps/api/src
git add apps/api/src/easysynq_api/api/objectives.py apps/api/src/easysynq_api/main.py apps/api/tests/integration/test_quality_objectives.py
git commit -m "feat(s-obj-1): /objectives router — create/measure/plans/scorecard + authz"
```

---

## Task 10: OpenAPI contract

**Files:**
- Modify: `packages/contracts/openapi.yaml`

Document every new path + component schema in-PR (redocly-lint only, not codegen). Mirror the S-ack additions (`openapi.yaml:1486-1555` paths + `4720-4957` schemas).

- [ ] **Step 1: Add the paths + schemas**

Add, under `paths:`, entries for: `POST/GET /objectives`, `GET /objectives/scorecard`, `GET /objectives/{objective_id}`, `POST/GET /objectives/{objective_id}/measurements`, `POST /objectives/{objective_id}/plans`, `DELETE /objectives/{objective_id}/plans/{plan_id}` — each with `operationId`, `summary`, `description`, `tags: [objectives]`, request body `$ref`, and `responses` (`201`/`200`/`204`/`403`/`404`/`422`). Under `components/schemas:` add `Objective` (mirroring the `_objective` dict: `id, identifier, title, current_state, target_value, unit, baseline_value, current_value, direction, at_risk_threshold, due_date, process_id, policy_id, rag, pct_toward_target, attainment, plans`), `ObjectiveCreate`, `Measurement`, `MeasurementCreate`, `ObjectivePlan`, `PlanCreate`, `ObjectiveScorecard` (`total, on_target, by_rag, objectives`).

- [ ] **Step 2: Lint — expect PASS**

Run: `/check-contracts` (redocly lint on `packages/contracts/openapi.yaml`).

- [ ] **Step 3: Commit**

```bash
git add packages/contracts/openapi.yaml
git commit -m "docs(s-obj-1): openapi contract for /objectives"
```

---

## Task 11: Canon fold — R44 + back-prop + slice-history

**Files:**
- Modify: `docs/decisions-register.md` (add R44) · `docs/14-data-model.md` · `docs/16-roadmap.md` · `docs/slice-history.md`

- [ ] **Step 1: Write R44** in `decisions-register.md` (the spec s11 text): objective = kind=DOCUMENT subtype (R3); direction+amber RAG computed; current_value rolled from append-only KPI_READING records with target_at_capture; quality_policy = R25 singleton; rides seeded objective.*/kpi.* keys (no new key, catalog stays 100); no new SignatureMeaning (R2) / audit_object_type (R39). Add the D/C reconciliation-table rows if the doc has them.

- [ ] **Step 2: Back-prop** — update `docs/14-data-model.md` (the as-built `quality_objective`/`objective_plan`/`kpi_measurement` incl. the `direction`/`at_risk_threshold` additions vs the spec, and `current_value` rolled-not-versioned); `docs/16-roadmap.md` (objectives shipped → PDCA dashboard now buildable). (Docs 02/04/06/07/10/13/15/18 edits are light cross-refs — make the ones the reviewer flags; at minimum 14 + 16.)

- [ ] **Step 3: slice-history entry** — a bold `**S-obj-1**` entry: migration `0049`, the kind=DOCUMENT-subtype thesis, the evidence-grade KPI_READING rollup, direction+amber RAG, quality_policy-already-seeded, no-new-key, the ⚠ traps (populate_existing rollup; static-route-before-param scorecard; document_type seed column-name confirm), the deferrals, and the spec/plan pointers. Update the migration-head line to `0049`.

- [ ] **Step 4: Commit**

```bash
git add docs/decisions-register.md docs/14-data-model.md docs/16-roadmap.md docs/slice-history.md
git commit -m "docs(s-obj-1): R44 + back-propagation + slice-history"
```

---

## Task 12: Full gate · diff-critic · live smoke · PR

- [ ] **Step 1: Full local gates**

Run: `/check-migrations` (round-trip), `/check-contracts` (redocly), and the api static half (`ruff check` + `ruff format --check` + `mypy --strict`). The api unit/integration suites run in CI (Linux). Confirm green CI on the branch before the PR.

- [ ] **Step 2: diff-critic** — run the `diff-critic` agent on the branch diff (Agent tool, `subagent_type: diff-critic`). Fold only confirmed findings. Pay special attention to: the `populate_existing` rollup (stale-read false-PASS), the `document_type` seed column names, the `capture_record` signature/`_commit=False` composition, the FK-name ↔ ORM match (alembic check), and the catalog-count-stays-100 assertion.

- [ ] **Step 3: Live smoke** (localhost, the worker-exec heredoc — `docker compose … build migrate api worker beat` first; the live `0049` migrate is itself a smoke leg). Drive the service loop: author a Quality Policy → create an objective (grant the demo app_user SYSTEM overrides for `objective.manage`/`kpi.record`) → record two measurements → GET the objective (current_value rolled, RAG correct) → GET /objectives/scorecard. Confirm the 6.2 compliance-checklist node moved off PARTIAL once the objective is Effective.

- [ ] **Step 4: PR** — push the branch, open a PR against protected `main` (use `/pr` or `gh pr create --body-file`). Title: `feat(s-obj-1): Quality Objectives backend (clause 6.2) — migration 0049`. Body: the spec/plan links, the 5 CI gates, the deferrals.

- [ ] **Step 5: CLAUDE.md learnings line** (on merge) — a capped Recent-learnings bullet: S-obj-1 opens the Quality Objectives family; the kind=DOCUMENT-subtype + evidence-grade-rollup thesis; rides seeded keys (no catalog change); migration head `0049`; the traps. Update Current-status.

---

## Self-Review

**Spec coverage (every spec section → task):**
- s2 data model (quality_objective / objective_plan / kpi_measurement / OBJ type / direction enum / event types) → Tasks 1, 3, 4 ✓
- s3 RAG/pct/attainment rule → Task 2 ✓
- s4 lifecycle + measurement flow (create + policy-consistency + 6.2 auto-map; record → rollup) → Tasks 5, 6 ✓
- s5 API surface (create/list/get/measurements/plans/scorecard) → Tasks 8, 9 ✓
- s6 authz (ride seeded keys, `_objective_scope`, SYSTEM fallback, catalog stays 100) → Tasks 9 (resolver + matrix + count test) ✓
- s7 audit/events (OBJECTIVE_* additive, object_type='document', no signature) → Tasks 1, 6, 7 ✓
- s8 testing (unit rule + enums; integration loop + authz matrix + rollup + scorecard) → Tasks 2, 3, 5–9 ✓
- s10 deferrals → no code (named in the spec); periodic-review reminders inherited free (no task) ✓
- s11 R44 + back-prop → Task 11 ✓
- objective_plan (the "…and planning" half) → Task 7 ✓
- quality_policy built (OBJ-type seed + consistency validation; POL already seeded) → Tasks 4, 5 ✓

**Placeholder scan:** No "TBD"/"implement later". Each implementation step shows complete code. The `> Confirm before running:` notes are explicit grounding checks against real files (import paths, fixture column names, `capture_record`/`AuditEvent`/`document_type` shapes) — NOT placeholders; the executing subagent verifies each against the cited file before running its test.

**Type consistency:** `ObjectiveDirection` (`HIGHER_IS_BETTER`/`LOWER_IS_BETTER`) used identically in the enum, rule, model, and router. `rag_status`/`pct_toward_target`/`attainment` signatures match between Task 2 (definition) and Task 9 (serializer call). `create_objective`/`record_measurement`/`add_objective_plan`/`remove_objective_plan` signatures match between Tasks 5–7 (definition) and Task 9 (calls). `KpiMeasurement`/`QualityObjective`/`ObjectivePlan` column names match across Tasks 3, 4, 6, 8, 9. The serializer keys (`rag`, `current_value`, `pct_toward_target`, `attainment`, `plans`, scorecard `by_rag`/`on_target`/`total`/`objectives`) match the integration-test assertions in Tasks 5, 6, 8, 9.

**Grounding resolved (a parallel verifier pass read every cited file and the fixes are folded in):** the `AuditEvent` constructor (`actor_id`/`actor_type`/`occurred_at` + `AuditObjectType.document`), the `_grant` test helper (`PermissionOverride.user_id`/`effect=Effect.ALLOW`/`scope_id`→a `Scope(level=SYSTEM)` row; `AppUser.keycloak_subject`; reuse `_ensure_user`), the `DocumentCurrentState` import (`_vault_enums`), the `document_type` seed shape, and the `main.py` alias mount were all WRONG in the first draft and are now corrected. `create_document`/`capture_record`/`ClauseMapping`/the vault·records·authz exports/`RecordType.KPI_READING`/catalog==100/route-ordering were verified correct as written.

**Resolved design decision — KPI readings are AD-HOC evidence records (no R21 version pin).** `capture_record(source_document_id=objective_id)` would 422 under R21 (a non-FRM source doc must pin a version; a Draft objective has none). KPI readings therefore capture as ad-hoc `KPI_READING` records (the `capture_complaint` precedent) — still WORM/retention-governed evidence — with the objective linkage + frozen `target_at_capture` carried on `kpi_measurement`. Measuring does **not** require the objective to be Effective in v1 (requiring-Effective + a version pin is a v1.x tightening, noted in the spec s10). This is an implementation refinement within the owner's "each reading a KPI_READING record" decision, not a change to it.
