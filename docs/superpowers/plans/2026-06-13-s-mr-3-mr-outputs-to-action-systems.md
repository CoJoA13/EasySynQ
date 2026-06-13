# S-mr-3 — Management Review outputs → action systems — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire a released Management Review's ACTION outputs to the CAPA (full backend+FE) and DCR (backend-only) systems, and add a SoD-aware `capabilities.release` to the MR detail serializer so the FE Release button can't show-then-403.

**Architecture:** One migration (`0051`) adds the `review_output.spawned_capa_id → capa.id` FK + the additive `dcr_reason_class 'mgmt_review'` and two `event_type` values. A new `services/mgmt_review/actions.py` holds two service functions (`spawn_capa_for_output` one-shot-latch, `spawn_dcr_for_output` 1:N idempotent) that reuse the existing `build_capa(_commit=False)` / `raise_dcr(_commit=False)` cores atomically. Two thin POST endpoints under `/management-reviews/{review_id}/outputs/{output_id}/…` gate on `capa.create` / `changeRequest.create`. `_mr_capabilities` mirrors `_objective_capabilities`'s release branch. The FE adds a severity-picking modal + Raise/View-CAPA affordances on ACTION rows and gates Release on `capabilities.release`.

**Tech Stack:** FastAPI / Python 3.12 / SQLAlchemy 2 / Alembic / PostgreSQL 16 · React/TS + Mantine + Tanstack Query + MSW + vitest + jest-axe.

**Spec:** `docs/superpowers/specs/2026-06-13-s-mr-3-mr-outputs-to-action-systems-design.md`. **Branch:** `feat/s-mr-3-mr-outputs-to-action-systems`.

**⚠ Windows verification reality:** native `uv run pytest -m integration` FAILS on this box (ProactorEventLoop) and the FULL unit suite crashes — so **integration + full-unit are CI-only**. Per backend task: verify locally with `ruff check`, `ruff format --check`, `mypy`, and the **targeted** unit tests that run fine (route/serializer/enum). The integration tests are written here and run in CI. The **FE runs fully natively** (vitest/tsc/eslint/build).

---

## Task 1: ORM enums + the `spawned_capa_id` FK

**Files:**
- Modify: `apps/api/src/easysynq_api/db/models/_dcr_enums.py` (add `DcrReasonClass.mgmt_review`)
- Modify: `apps/api/src/easysynq_api/db/models/_audit_enums.py` (add two `EventType` members)
- Modify: `apps/api/src/easysynq_api/db/models/review_output.py` (add the FK to `spawned_capa_id`)
- Test: `apps/api/tests/unit/test_mgmt_review_enums.py` (new — trivial member-existence assertions)

- [ ] **Step 1: Write the failing test**

Create `apps/api/tests/unit/test_mgmt_review_enums.py`:

```python
"""S-mr-3: the additive enum members + the un-reserved FK exist on the ORM."""

from easysynq_api.db.models._audit_enums import EventType
from easysynq_api.db.models._dcr_enums import DCR_REASON_CLASS_VALUES, DcrReasonClass
from easysynq_api.db.models.review_output import ReviewOutput


def test_dcr_reason_class_has_mgmt_review() -> None:
    assert DcrReasonClass.mgmt_review.value == "mgmt_review"
    assert "mgmt_review" in DCR_REASON_CLASS_VALUES


def test_event_types_for_mr_spawns_exist() -> None:
    assert EventType.MGMT_REVIEW_CAPA_SPAWNED.value == "MGMT_REVIEW_CAPA_SPAWNED"
    assert EventType.MGMT_REVIEW_DCR_SPAWNED.value == "MGMT_REVIEW_DCR_SPAWNED"


def test_spawned_capa_id_has_capa_fk() -> None:
    col = ReviewOutput.__table__.c.spawned_capa_id
    fks = list(col.foreign_keys)
    assert len(fks) == 1
    fk = fks[0]
    assert fk.column.table.name == "capa"
    assert fk.name == "fk_review_output_spawned_capa_id_capa"
    assert fk.ondelete == "RESTRICT"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && uv run pytest tests/unit/test_mgmt_review_enums.py -v`
Expected: FAIL — `AttributeError: mgmt_review` / `MGMT_REVIEW_CAPA_SPAWNED` and `assert len(fks) == 1` fails (no FK yet).

- [ ] **Step 3: Add `DcrReasonClass.mgmt_review`**

In `_dcr_enums.py`, inside `class DcrReasonClass`, add the member after `customer_requirement` (before `other`):

```python
    customer_requirement = "customer_requirement"
    mgmt_review = "mgmt_review"
    other = "other"
```

(`DCR_REASON_CLASS_VALUES = tuple(_vals(DcrReasonClass))` updates automatically — no other edit.)

- [ ] **Step 4: Add the two `EventType` members**

In `_audit_enums.py`, inside `class EventType`, immediately after `MGMT_REVIEW_CLOSED = "MGMT_REVIEW_CLOSED"`:

```python
    MGMT_REVIEW_CLOSED = "MGMT_REVIEW_CLOSED"
    # S-mr-3 — clause 9.3 → §10/§7.5: an MR ACTION output spawns a CAPA / DCR (object_type=document).
    MGMT_REVIEW_CAPA_SPAWNED = "MGMT_REVIEW_CAPA_SPAWNED"
    MGMT_REVIEW_DCR_SPAWNED = "MGMT_REVIEW_DCR_SPAWNED"
```

(`EVENT_TYPE_VALUES = tuple(_vals(EventType))` updates automatically.)

- [ ] **Step 5: Add the FK to `spawned_capa_id`**

In `review_output.py`, replace the bare `spawned_capa_id` column:

```python
    spawned_capa_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
```

with the FK form (mirrors `spawned_task_id` directly above it):

```python
    spawned_capa_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("capa.id", ondelete="RESTRICT", name="fk_review_output_spawned_capa_id_capa"),
        nullable=True,
    )
```

(`ForeignKey` is already imported in this file.)

- [ ] **Step 6: Run test to verify it passes**

Run: `cd apps/api && uv run pytest tests/unit/test_mgmt_review_enums.py -v`
Expected: PASS (3 tests).

Also run: `cd apps/api && uv run ruff check src/easysynq_api/db/models && uv run mypy src/easysynq_api/db/models/review_output.py`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add apps/api/src/easysynq_api/db/models/_dcr_enums.py apps/api/src/easysynq_api/db/models/_audit_enums.py apps/api/src/easysynq_api/db/models/review_output.py apps/api/tests/unit/test_mgmt_review_enums.py
git commit -m "feat(s-mr-3): un-reserve spawned_capa_id FK + add mgmt_review reason-class + CAPA/DCR spawn event types (ORM)"
```

---

## Task 2: Migration `0051` (FK + additive enums) + round-trip

**Files:**
- Create: `migrations/versions/0051_mr_outputs_to_actions.py`

- [ ] **Step 1: Write the migration**

Create `migrations/versions/0051_mr_outputs_to_actions.py`. Match the 0050 header style exactly (typed `revision`/`down_revision`):

```python
"""S-mr-3 — MR outputs → action systems.

Un-reserves ``review_output.spawned_capa_id`` (adds the FK → ``capa.id``, RESTRICT — the
``spawned_task_id`` precedent on the same table; the COLUMN already exists from 0050) and adds three
additive enum values: ``dcr_reason_class 'mgmt_review'`` (the MR→DCR justification) and the
``MGMT_REVIEW_CAPA_SPAWNED`` / ``MGMT_REVIEW_DCR_SPAWNED`` event types. No data, no seed.

Revision ID: 0051_mr_outputs_to_actions
Revises: 0050_management_review
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0051_mr_outputs_to_actions"
down_revision: str | None = "0050_management_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Additive enum values (IF NOT EXISTS → idempotent; autocommit_block — the 0048 pattern; a
    # from-scratch ``upgrade head`` already has them via the *_VALUES tuples, so these no-op there
    # and only really add on an incrementally-migrated production DB).
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE dcr_reason_class ADD VALUE IF NOT EXISTS 'mgmt_review'")
        op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'MGMT_REVIEW_CAPA_SPAWNED'")
        op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'MGMT_REVIEW_DCR_SPAWNED'")
    # 2. Un-reserve spawned_capa_id: the FK on the EXISTING column (the 0044 create_foreign_key
    # precedent). RESTRICT — a CAPA the review spawned can't be row-deleted out from under it
    # (CAPAs are records whose lifecycle is state, never a row-delete). Acyclic (review_output→capa;
    # capa never points back), so no use_alter. The name MUST match the ORM constraint (else
    # ``alembic check`` phantom-DROPs it).
    op.create_foreign_key(
        "fk_review_output_spawned_capa_id_capa",
        "review_output",
        "capa",
        ["spawned_capa_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    # Drop the FK (always safe — an FK-drop never RESTRICT-aborts, even on a populated DB). The ADD
    # VALUEs are irreversible in PG → no-op (the 0011/0047/0048 precedent).
    op.drop_constraint(
        "fk_review_output_spawned_capa_id_capa", "review_output", type_="foreignkey"
    )
```

- [ ] **Step 2: Run the migrations round-trip gate**

Run: `/check-migrations`
Expected: PASS — `alembic upgrade head` → `downgrade` → `upgrade head` → `alembic check` reports **no** drift (the FK is named-mirrored in the ORM from Task 1; `env.py` doesn't exclude FKs, so the check round-trips it).

- [ ] **Step 3: Verify head ordering**

Run: `cd apps/api && uv run alembic heads`
Expected: a single head `0051_mr_outputs_to_actions`.

- [ ] **Step 4: (Best-effort) populated-downgrade reasoning check**

The populated-downgrade risk is theoretical: `op.drop_constraint(..., type_="foreignkey")` drops the constraint definition — it never RESTRICT-aborts (only a row-DELETE against a RESTRICT FK aborts). The standard round-trip in Step 2 is the gate. No code change.

- [ ] **Step 5: Commit**

```bash
git add migrations/versions/0051_mr_outputs_to_actions.py
git commit -m "feat(s-mr-3): migration 0051 — spawned_capa_id FK + mgmt_review/CAPA/DCR-spawn enum values"
```

---

## Task 3: `spawn_capa_for_output` service (the CAPA un-reserve core)

**Files:**
- Create: `apps/api/src/easysynq_api/services/mgmt_review/actions.py`
- Modify: `apps/api/src/easysynq_api/services/mgmt_review/__init__.py` (export the new fns)
- Test: `apps/api/tests/integration/test_mgmt_review_actions.py` (new)

- [ ] **Step 1: Write the failing integration test**

Create `apps/api/tests/integration/test_mgmt_review_actions.py`. Reuse the existing `_drive_review_to_release` helper (it creates a released MR with an ACTION output + spawned MR_ACTION task):

```python
"""S-mr-3 integration: MR ACTION output → CAPA / DCR spawns + the close-gate decouple."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models._audit_enums import AuditObjectType, EventType
from easysynq_api.db.models._capa_enums import CapaSource
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.capa import Capa
from easysynq_api.db.models.signature_event import SignatureEvent
from easysynq_api.db.session import get_sessionmaker

from . import s5_helpers as s5
from .test_mgmt_review import _auth, _drive_review_to_release, _grant

pytestmark = pytest.mark.integration


async def _action_output_id(client: AsyncClient, h: dict[str, str], rid: str) -> str:
    det = (await client.get(f"/api/v1/management-reviews/{rid}", headers=h)).json()
    return next(o["id"] for o in det["outputs"] if o["output_type"] == "ACTION")


async def test_raise_capa_from_action_output(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    salt = uuid.uuid4().hex[:8]
    owner_sub = f"mr-own-{salt}"
    owner_id = await _grant(owner_sub, ())
    rid = await _drive_review_to_release(
        app_client, token_factory, salt, action_owner_subject=owner_sub, action_owner_id=owner_id
    )
    hs = _auth(token_factory, f"mr-sm-{salt}")
    await _grant(f"mr-sm-{salt}", ("capa.create", "capa.read"))
    oid = await _action_output_id(app_client, hs, rid)

    r = await app_client.post(
        f"/api/v1/management-reviews/{rid}/outputs/{oid}/raise-capa",
        headers=hs,
        json={"severity": "Major"},
    )
    assert r.status_code == 201, r.text
    capa_id = r.json()["spawned_capa_id"]
    assert capa_id is not None

    async with get_sessionmaker()() as s:
        capa = (await s.execute(select(Capa).where(Capa.id == uuid.UUID(capa_id)))).scalar_one()
        assert capa.source is CapaSource.review_output
        assert capa.severity.value == "Major"
        # NO signature on a recording act (R43)
        sigs = (
            (await s.execute(select(SignatureEvent).where(SignatureEvent.signed_object_id == capa.id)))
            .scalars()
            .all()
        )
        assert sigs == []
        # the MR-side audit fired
        ev = (
            await s.execute(
                select(AuditEvent).where(
                    AuditEvent.event_type == EventType.MGMT_REVIEW_CAPA_SPAWNED,
                    AuditEvent.object_type == AuditObjectType.document,
                )
            )
        ).scalars().all()
        assert any(e.after.get("capa_id") == capa_id for e in ev)

    # one-shot latch: a second spawn 409s
    again = await app_client.post(
        f"/api/v1/management-reviews/{rid}/outputs/{oid}/raise-capa",
        headers=hs,
        json={"severity": "Minor"},
    )
    assert again.status_code == 409, again.text
    assert again.json()["code"] == "capa_already_spawned"


async def test_raise_capa_404_on_unknown_output(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """An unknown output id under a real review → 404 (the not-found guard, ordered first)."""
    salt = uuid.uuid4().hex[:8]
    owner_sub = f"mr-own-{salt}"
    owner_id = await _grant(owner_sub, ())
    rid = await _drive_review_to_release(
        app_client, token_factory, salt, action_owner_subject=owner_sub, action_owner_id=owner_id
    )
    hs = _auth(token_factory, f"mr-sm-{salt}")
    await _grant(f"mr-sm-{salt}", ("capa.create",))
    r = await app_client.post(
        f"/api/v1/management-reviews/{rid}/outputs/{uuid.uuid4()}/raise-capa",
        headers=hs,
        json={"severity": "Major"},
    )
    assert r.status_code == 404, r.text


async def test_spawned_capa_does_not_block_close(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """F3: a spawned CAPA (still open) does NOT block MR close — the MR_ACTION task DONE is the sole
    close signal."""
    salt = uuid.uuid4().hex[:8]
    owner_sub = f"mr-own-{salt}"
    owner_id = await _grant(owner_sub, ())
    ho = _auth(token_factory, owner_sub)
    rid = await _drive_review_to_release(
        app_client, token_factory, salt, action_owner_subject=owner_sub, action_owner_id=owner_id
    )
    hs = _auth(token_factory, f"mr-sm-{salt}")
    await _grant(f"mr-sm-{salt}", ("capa.create", "capa.read"))
    oid = await _action_output_id(app_client, hs, rid)
    # spawn a CAPA (stays Raised — open)
    sp = await app_client.post(
        f"/api/v1/management-reviews/{rid}/outputs/{oid}/raise-capa",
        headers=hs,
        json={"severity": "Major"},
    )
    assert sp.status_code == 201, sp.text
    # complete the MR_ACTION task
    tasks = (await app_client.get("/api/v1/tasks?type=MR_ACTION", headers=ho)).json()
    action_task = next(t for t in tasks if t["assignee_user_id"] == str(owner_id))
    done = await app_client.post(
        f"/api/v1/tasks/{action_task['id']}/decision", headers=ho, json={"outcome": "complete"}
    )
    assert done.status_code == 200, done.text
    # close succeeds despite the open CAPA
    closed = await app_client.post(f"/api/v1/management-reviews/{rid}/close", headers=hs)
    assert closed.status_code == 200, closed.text
    assert closed.json()["close_state"] == "Closed"
```

(Confirm during implementation that `SignatureEvent.signed_object_id` is the right column for the no-signature assertion — grep `db/models/signature_event.py`; if the column differs, adjust. The intent: assert zero signature rows reference the spawned CAPA.)

- [ ] **Step 2: Run to verify it fails (CI-only on this box)**

Run: `cd apps/api && uv run pytest -m integration tests/integration/test_mgmt_review_actions.py -v`
Expected on Windows: the suite errors at collection/runtime (ProactorEventLoop) — **do not rely on local integration**. The real FAIL signal is in CI. Locally, proceed to implement and verify with ruff/mypy + the route/serializer unit tests; CI runs these.

- [ ] **Step 3: Create the actions service**

Create `apps/api/src/easysynq_api/services/mgmt_review/actions.py`:

```python
"""On-demand spawns from a released Management Review's ACTION outputs (S-mr-3, clause 9.3 → §10/
§7.5). A CAPA spawn is a one-shot latch on ``review_output.spawned_capa_id``; a DCR spawn is 1:N
(the link lives one-way on the DCR), retry-safe via an Idempotency-Key. Both are *recording* acts:
they mint an audit event but NO signature (R43). Each reuses the canonical create core
(``build_capa`` / ``raise_dcr``) with ``_commit=False`` so the link + audit commit in ONE txn — the
``_auto_capa_for_finding`` / ``raise_dcr_from_capa`` atomic precedents."""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._audit_enums import ActorType, AuditObjectType, EventType
from ...db.models._capa_enums import CapaSource, NcSeverity
from ...db.models._dcr_enums import DcrChangeType, DcrReasonClass, DcrSourceLinkType
from ...db.models._mgmt_review_enums import ManagementReviewCloseState, ReviewOutputType
from ...db.models._vault_enums import ChangeSignificance
from ...db.models.app_user import AppUser
from ...db.models.audit_event import AuditEvent
from ...db.models.dcr import Dcr
from ...db.models.review_output import ReviewOutput
from ..capa.service import build_capa
from ..dcr.service import raise_dcr
from .repository import get_review_doc
from .service import _conflict, _not_found  # package-private helpers (same package)


async def spawn_capa_for_output(
    session: AsyncSession,
    actor: AppUser,
    *,
    review_id: uuid.UUID,
    output_id: uuid.UUID,
    severity: NcSeverity,
) -> ReviewOutput:
    """Spawn a CAPA from an ACTION output of a released review (F2 on-demand). One-shot latch on
    ``spawned_capa_id``. The output row is locked FOR UPDATE so two concurrent spawns serialize (the
    loser sees the latch set → 409, never an orphaned second CAPA). ``build_capa(_commit=False)`` →
    set the link → audit ``MGMT_REVIEW_CAPA_SPAWNED`` (no signature) → one commit."""
    pair = await get_review_doc(session, review_id)
    if pair is None:
        raise _not_found("Management Review")
    review, doc = pair
    output = (
        await session.execute(
            select(ReviewOutput)
            .where(ReviewOutput.id == output_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if output is None or output.management_review_id != review_id:
        raise _not_found("Review output")
    if output.output_type is not ReviewOutputType.ACTION:
        raise _conflict("output_not_actionable", "Only an ACTION output can spawn a CAPA")
    if review.close_state is not ManagementReviewCloseState.ActionsTracked:
        raise _conflict(
            "review_not_tracking",
            "A CAPA can only be spawned while the review's actions are being tracked "
            "(it must be released, and not already closed).",
        )
    if output.spawned_capa_id is not None:
        raise _conflict("capa_already_spawned", "This action has already spawned a CAPA")
    capa = await build_capa(
        session,
        actor,
        title=f"CAPA (from management review {doc.identifier})",
        severity=severity,
        source=CapaSource.review_output,
        process_id=None,
        origin_finding_id=None,
        raised_block={
            "source": CapaSource.review_output.value,
            "review_id": str(review_id),
            "output_id": str(output_id),
            "severity": severity.value,
        },
        _commit=False,
    )
    output.spawned_capa_id = capa.id
    session.add(
        AuditEvent(
            org_id=actor.org_id,
            occurred_at=datetime.datetime.now(datetime.UTC),
            actor_id=actor.id,
            actor_type=ActorType.user,
            event_type=EventType.MGMT_REVIEW_CAPA_SPAWNED,
            object_type=AuditObjectType.document,
            object_id=doc.id,
            scope_ref=doc.identifier,
            after={"output_id": str(output_id), "capa_id": str(capa.id)},
        )
    )
    await session.commit()
    await session.refresh(output)
    return output


async def _find_spawned_dcr_for_output(
    session: AsyncSession, org_id: uuid.UUID, output_id: uuid.UUID, idempotency_key: str | None
) -> Dcr | None:
    """The DCR this output already spawned for ``idempotency_key`` (None when no key). Scoped to
    (org, this output, key) — the ``(org_id, source_link_id, spawn_idempotency_key)`` partial-UNIQUE
    (S-dcr-5)."""
    if idempotency_key is None:
        return None
    return (
        await session.execute(
            select(Dcr).where(
                Dcr.org_id == org_id,
                Dcr.source_link_type == DcrSourceLinkType.mgmt_review,
                Dcr.source_link_id == output_id,
                Dcr.spawn_idempotency_key == idempotency_key,
            )
        )
    ).scalar_one_or_none()


async def spawn_dcr_for_output(
    session: AsyncSession,
    actor: AppUser,
    *,
    review_id: uuid.UUID,
    output_id: uuid.UUID,
    change_type: DcrChangeType,
    change_significance: ChangeSignificance,
    reason_text: str,
    target_document_id: uuid.UUID | None = None,
    proposed_effective_from: datetime.datetime | None = None,
    idempotency_key: str | None = None,
) -> tuple[Dcr, bool]:
    """Spawn a DCR from an ACTION output of a released review (F3, backend-only). 1:N — the link
    lives one-way on the DCR (``source_link_type=mgmt_review``, ``source_link_id=output.id``); an
    output may drive multiple changes. ``reason_class`` is fixed to ``mgmt_review``. An
    Idempotency-Key makes a retry return the same DCR (created=False). NO signature (raise_dcr emits
    only ``DCR_RAISED``). Mirrors ``raise_dcr_from_capa``."""
    pair = await get_review_doc(session, review_id)
    if pair is None:
        raise _not_found("Management Review")
    review, doc = pair
    output = await session.get(ReviewOutput, output_id)
    if output is None or output.management_review_id != review_id:
        raise _not_found("Review output")
    if output.output_type is not ReviewOutputType.ACTION:
        raise _conflict("output_not_actionable", "Only an ACTION output can spawn a DCR")
    if review.close_state is not ManagementReviewCloseState.ActionsTracked:
        raise _conflict(
            "review_not_tracking",
            "A DCR can only be spawned while the review's actions are being tracked "
            "(it must be released, and not already closed).",
        )
    existing = await _find_spawned_dcr_for_output(session, actor.org_id, output_id, idempotency_key)
    if existing is not None:
        return existing, False
    try:
        dcr = await raise_dcr(
            session,
            actor,
            change_type=change_type,
            change_significance=change_significance,
            reason_class=DcrReasonClass.mgmt_review,
            reason_text=reason_text,
            target_document_id=target_document_id,
            source_link_type=DcrSourceLinkType.mgmt_review,
            source_link_id=output_id,
            proposed_effective_from=proposed_effective_from,
            spawn_idempotency_key=idempotency_key,
            _commit=False,
        )
        session.add(
            AuditEvent(
                org_id=actor.org_id,
                occurred_at=datetime.datetime.now(datetime.UTC),
                actor_id=actor.id,
                actor_type=ActorType.user,
                event_type=EventType.MGMT_REVIEW_DCR_SPAWNED,
                object_type=AuditObjectType.document,
                object_id=doc.id,
                scope_ref=doc.identifier,
                after={"output_id": str(output_id), "dcr_id": str(dcr.id)},
            )
        )
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await _find_spawned_dcr_for_output(
            session, actor.org_id, output_id, idempotency_key
        )
        if existing is not None:
            return existing, False
        raise
    await session.refresh(dcr)
    return dcr, True
```

- [ ] **Step 4: Export the new fns**

In `services/mgmt_review/__init__.py`, add the import (near the other `from .X import` lines):

```python
from .actions import spawn_capa_for_output, spawn_dcr_for_output
```

and add both to `__all__` (alphabetically, e.g. after `"release_review",`):

```python
    "release_review",
    "spawn_capa_for_output",
    "spawn_dcr_for_output",
    "spawn_mr_actions",
```

- [ ] **Step 5: Local static gates**

Run: `cd apps/api && uv run ruff check src/easysynq_api/services/mgmt_review && uv run ruff format --check src/easysynq_api/services/mgmt_review && uv run mypy src/easysynq_api/services/mgmt_review/actions.py`
Expected: clean. (Integration runs in CI.)

- [ ] **Step 6: Commit**

```bash
git add apps/api/src/easysynq_api/services/mgmt_review/actions.py apps/api/src/easysynq_api/services/mgmt_review/__init__.py apps/api/tests/integration/test_mgmt_review_actions.py
git commit -m "feat(s-mr-3): spawn_capa_for_output / spawn_dcr_for_output service core + integration tests"
```

---

## Task 4: `raise-capa` endpoint + `_review_output.spawned_capa_id` + route test

**Files:**
- Modify: `apps/api/src/easysynq_api/api/mgmt_review.py` (serializer field, body model, endpoint, imports)
- Test: `apps/api/tests/unit/test_mgmt_review_routes.py` (add a route-resolution assertion)

- [ ] **Step 1: Add the route-resolution test**

Append to `apps/api/tests/unit/test_mgmt_review_routes.py`:

```python
def test_raise_capa_route_resolves() -> None:
    """POST /management-reviews/{id}/outputs/{oid}/raise-capa resolves to the spawn endpoint (not a
    shadow). Distinct suffix from /outputs/{oid}, so no str-convertor collision — but pin it."""
    from starlette.routing import Match

    from easysynq_api.main import create_app

    app = create_app()
    path = "/api/v1/management-reviews/r/outputs/o/raise-capa"
    winner = next(
        (
            r
            for r in app.router.routes
            if r.matches({"type": "http", "path": path, "method": "POST"})[0] != Match.NONE
        ),
        None,
    )
    assert winner is not None
    assert winner.endpoint.__name__ == "raise_output_capa_endpoint"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd apps/api && uv run pytest tests/unit/test_mgmt_review_routes.py::test_raise_capa_route_resolves -v`
Expected: FAIL — `winner is None` (endpoint doesn't exist yet).

- [ ] **Step 3: Extend imports + the `_review_output` serializer**

In `api/mgmt_review.py`:

Change the `fastapi` import line to add `Header`:

```python
from fastapi import APIRouter, Depends, Header, Request, Response, status
```

Add after the existing fastapi import:

```python
from fastapi.responses import JSONResponse
```

Change the `domain.authz` import:

```python
from ..domain.authz import RequestContext, ResourceContext, authorize
```

Add these imports (group with the existing model imports):

```python
from ..db.models._capa_enums import NcSeverity
from ..db.models._dcr_enums import DcrChangeType
from ..db.models._vault_enums import ChangeSignificance
```

Add `gather_grants` to the `services.authz` import block:

```python
from ..services.authz import (
    AuthzAuditSink,
    enforce,
    gather_grants,
    get_authz_audit_sink,
    require,
)
```

Add the repository helpers + the DCR helpers (new import lines):

```python
from ..services.authz.repository import gather_sod_constraints, get_allow_approver_release
from .dcr import _dcr, _dcr_doc_scope
```

Add the two service fns to the `from ..services.mgmt_review import (...)` block:

```python
    spawn_capa_for_output,
    spawn_dcr_for_output,
```

Now add `spawned_capa_id` to `_review_output` (after `spawned_task_id`):

```python
def _review_output(ro: ReviewOutput) -> dict[str, Any]:
    return {
        "id": str(ro.id),
        "management_review_id": str(ro.management_review_id),
        "output_type": ro.output_type.value,
        "description": ro.description,
        "owner_user_id": str(ro.owner_user_id) if ro.owner_user_id is not None else None,
        "due_date": ro.due_date.isoformat() if ro.due_date is not None else None,
        "spawned_task_id": str(ro.spawned_task_id) if ro.spawned_task_id is not None else None,
        "spawned_capa_id": str(ro.spawned_capa_id) if ro.spawned_capa_id is not None else None,
    }
```

- [ ] **Step 4: Add the request bodies + the `raise-capa` endpoint**

Add the body models near the other request bodies (after `ReviewSubmitBody`):

```python
class OutputCapaCreate(BaseModel):
    severity: NcSeverity


class OutputDcrCreate(BaseModel):
    change_type: DcrChangeType
    change_significance: ChangeSignificance
    reason_text: str = Field(min_length=1, max_length=4000)
    target_document_id: uuid.UUID | None = None
    proposed_effective_from: datetime.datetime | None = None
```

Add the endpoint immediately after `delete_output_endpoint` (keeps the outputs cluster together; it's a longer sub-path so no shadow):

```python
@router.post(
    "/management-reviews/{review_id}/outputs/{output_id}/raise-capa",
    status_code=status.HTTP_201_CREATED,
)
async def raise_output_capa_endpoint(
    review_id: uuid.UUID,
    output_id: uuid.UUID,
    body: OutputCapaCreate,
    request: Request,
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    authz_sink: AuthzAuditSink = Depends(get_authz_audit_sink),
) -> dict[str, Any]:
    """Spawn a CAPA from an ACTION output (F2 on-demand). Gate ``capa.create`` at SYSTEM (the MR has
    no process); the spawn sets ``source=review_output`` + the one-shot ``spawned_capa_id`` latch.
    Returns the refreshed output (carrying ``spawned_capa_id`` so the FE can deep-link to the CAPA
    board)."""
    await _load_review(session, caller, review_id)  # 404 cross-org
    await enforce(session, authz_sink, request, caller, "capa.create", ResourceContext.system())
    ro = await spawn_capa_for_output(
        session, caller, review_id=review_id, output_id=output_id, severity=body.severity
    )
    return _review_output(ro)
```

- [ ] **Step 5: Run to verify the route test passes + static gates**

Run: `cd apps/api && uv run pytest tests/unit/test_mgmt_review_routes.py -v`
Expected: PASS.

Run: `cd apps/api && uv run ruff check src/easysynq_api/api/mgmt_review.py && uv run ruff format --check src/easysynq_api/api/mgmt_review.py && uv run mypy src/easysynq_api/api/mgmt_review.py`
Expected: clean. (The raise-capa integration tests from Task 3 run in CI.)

- [ ] **Step 6: Commit**

```bash
git add apps/api/src/easysynq_api/api/mgmt_review.py apps/api/tests/unit/test_mgmt_review_routes.py
git commit -m "feat(s-mr-3): POST raise-capa endpoint + spawned_capa_id serializer field + route test"
```

---

## Task 5: `raise-dcr` endpoint + DCR integration tests

**Files:**
- Modify: `apps/api/src/easysynq_api/api/mgmt_review.py` (the `raise-dcr` endpoint)
- Modify: `apps/api/tests/integration/test_mgmt_review_actions.py` (add DCR tests)

- [ ] **Step 1: Add the DCR integration tests**

Append to `apps/api/tests/integration/test_mgmt_review_actions.py`:

```python
async def test_raise_dcr_from_action_output_links_mgmt_review(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    salt = uuid.uuid4().hex[:8]
    owner_sub = f"mr-own-{salt}"
    owner_id = await _grant(owner_sub, ())
    rid = await _drive_review_to_release(
        app_client, token_factory, salt, action_owner_subject=owner_sub, action_owner_id=owner_id
    )
    hs = _auth(token_factory, f"mr-sm-{salt}")
    # changeRequest.create at SYSTEM (a CREATE DCR has no target → SYSTEM scope)
    await _grant(f"mr-sm-{salt}", ("changeRequest.create", "changeRequest.read"))
    oid = await _action_output_id(app_client, hs, rid)

    r = await app_client.post(
        f"/api/v1/management-reviews/{rid}/outputs/{oid}/raise-dcr",
        headers=hs,
        json={
            "change_type": "CREATE",
            "change_significance": "MINOR",
            "reason_text": "Draft a supplier-evaluation SOP per the review decision",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["source_link_type"] == "mgmt_review"
    assert body["source_link_id"] == oid
    assert body["reason_class"] == "mgmt_review"


async def test_raise_dcr_idempotency_key_replays(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    salt = uuid.uuid4().hex[:8]
    owner_sub = f"mr-own-{salt}"
    owner_id = await _grant(owner_sub, ())
    rid = await _drive_review_to_release(
        app_client, token_factory, salt, action_owner_subject=owner_sub, action_owner_id=owner_id
    )
    hs = _auth(token_factory, f"mr-sm-{salt}")
    await _grant(f"mr-sm-{salt}", ("changeRequest.create", "changeRequest.read"))
    oid = await _action_output_id(app_client, hs, rid)
    key = uuid.uuid4().hex
    payload = {
        "change_type": "CREATE",
        "change_significance": "MINOR",
        "reason_text": "idempotent draft",
    }
    first = await app_client.post(
        f"/api/v1/management-reviews/{rid}/outputs/{oid}/raise-dcr",
        headers={**hs, "Idempotency-Key": key},
        json=payload,
    )
    assert first.status_code == 201, first.text
    replay = await app_client.post(
        f"/api/v1/management-reviews/{rid}/outputs/{oid}/raise-dcr",
        headers={**hs, "Idempotency-Key": key},
        json=payload,
    )
    assert replay.status_code == 200, replay.text  # 200 == replay, not a new DCR
    assert replay.json()["id"] == first.json()["id"]
    # a DIFFERENT key spawns a fresh DCR (1:N)
    other = await app_client.post(
        f"/api/v1/management-reviews/{rid}/outputs/{oid}/raise-dcr",
        headers={**hs, "Idempotency-Key": uuid.uuid4().hex},
        json=payload,
    )
    assert other.status_code == 201, other.text
    assert other.json()["id"] != first.json()["id"]
```

- [ ] **Step 2: Run to verify it fails (CI-only on this box)**

Run: `cd apps/api && uv run pytest -m integration tests/integration/test_mgmt_review_actions.py -k dcr -v`
Expected on Windows: ProactorEventLoop error — verify in CI. Implement, then rely on CI.

- [ ] **Step 3: Add the `raise-dcr` endpoint**

In `api/mgmt_review.py`, add immediately after `raise_output_capa_endpoint`:

```python
@router.post("/management-reviews/{review_id}/outputs/{output_id}/raise-dcr")
async def raise_output_dcr_endpoint(
    review_id: uuid.UUID,
    output_id: uuid.UUID,
    body: OutputDcrCreate,
    request: Request,
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    authz_sink: AuthzAuditSink = Depends(get_authz_audit_sink),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JSONResponse:
    """Spawn a DCR from an ACTION output (F3, backend-only). Gate ``changeRequest.create`` at the
    target document's scope (a CREATE DCR has no target → SYSTEM — the ``POST /dcrs`` precedent). 1:N
    + Idempotency-Key (201 new / 200 replay). The link lives one-way on the DCR
    (``source_link_type=mgmt_review``)."""
    await _load_review(session, caller, review_id)  # 404 cross-org
    scope = await _dcr_doc_scope(session, body.target_document_id)
    await enforce(session, authz_sink, request, caller, "changeRequest.create", scope)
    dcr, created = await spawn_dcr_for_output(
        session,
        caller,
        review_id=review_id,
        output_id=output_id,
        change_type=body.change_type,
        change_significance=body.change_significance,
        reason_text=body.reason_text,
        target_document_id=body.target_document_id,
        proposed_effective_from=body.proposed_effective_from,
        idempotency_key=idempotency_key,
    )
    return JSONResponse(
        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        content=_dcr(dcr),
    )
```

- [ ] **Step 4: Static gates**

Run: `cd apps/api && uv run ruff check src/easysynq_api/api/mgmt_review.py && uv run ruff format --check src/easysynq_api/api/mgmt_review.py && uv run mypy src/easysynq_api/api/mgmt_review.py`
Expected: clean. (DCR integration tests run in CI.)

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/easysynq_api/api/mgmt_review.py apps/api/tests/integration/test_mgmt_review_actions.py
git commit -m "feat(s-mr-3): POST raise-dcr endpoint (backend-only mgmt_review source-link) + integration tests"
```

---

## Task 6: Codex #1 — SoD-aware `capabilities.release`

**Files:**
- Modify: `apps/api/src/easysynq_api/api/mgmt_review.py` (`_mr_capabilities`, serializer kwarg, wire into detail)
- Test: `apps/api/tests/unit/test_mgmt_review_serializer.py` (new — serializer kwarg behaviour)
- Modify: `apps/api/tests/integration/test_mgmt_review_actions.py` (add the SoD-2 integration test)

- [ ] **Step 1: Write the serializer unit test**

Create `apps/api/tests/unit/test_mgmt_review_serializer.py`:

```python
"""S-mr-3: _mgmt_review includes a capabilities block only when one is passed (detail-only)."""

import datetime
import uuid

from easysynq_api.api.mgmt_review import _mgmt_review
from easysynq_api.db.models._mgmt_review_enums import ManagementReviewCloseState
from easysynq_api.db.models.management_review import ManagementReview


def _mr() -> ManagementReview:
    mr = ManagementReview(
        id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        period_label="2026 Annual",
        review_date=None,
        attendees=None,
        close_state=ManagementReviewCloseState.ActionsTracked,
        closed_at=None,
    )
    mr.created_at = datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC)
    return mr


def test_capabilities_absent_when_none() -> None:
    out = _mgmt_review(_mr(), identifier="MR-001", title="x", current_state="Effective")
    assert "capabilities" not in out


def test_capabilities_present_when_passed() -> None:
    out = _mgmt_review(
        _mr(),
        identifier="MR-001",
        title="x",
        current_state="Approved",
        capabilities={"release": False},
    )
    assert out["capabilities"] == {"release": False}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd apps/api && uv run pytest tests/unit/test_mgmt_review_serializer.py -v`
Expected: FAIL — `_mgmt_review()` has no `capabilities` kwarg (TypeError).

- [ ] **Step 3: Add the `capabilities` kwarg to `_mgmt_review`**

Replace the `_mgmt_review` function body to accept + include the optional block:

```python
def _mgmt_review(
    mr: ManagementReview,
    *,
    identifier: str,
    title: str,
    current_state: Any,
    capabilities: dict[str, bool] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(mr.id),
        "identifier": identifier,
        "title": title,
        "current_state": (
            current_state.value if hasattr(current_state, "value") else str(current_state)
        ),
        "period_label": mr.period_label,
        "review_date": mr.review_date.isoformat() if mr.review_date is not None else None,
        "attendees": mr.attendees,
        "close_state": mr.close_state.value if mr.close_state is not None else None,
        "closed_at": mr.closed_at.isoformat() if mr.closed_at is not None else None,
        "created_at": mr.created_at.isoformat(),
    }
    if capabilities is not None:
        out["capabilities"] = capabilities
    return out
```

- [ ] **Step 4: Add `_mr_capabilities` + wire into the detail endpoint**

Add `_mr_capabilities` right after the `_release_scope` helper:

```python
async def _mr_capabilities(
    session: AsyncSession, caller: AppUser, doc: DocumentedInformation
) -> dict[str, bool]:
    """The caller's SoD-sensitive lifecycle affordance on this MR (detail-only). Only ``release``
    needs the SoD-2 overlay (author/approver ≠ releaser over the version the cutover would promote);
    submit/close gate on ``mgmtReview.record_outputs`` (no author≠releaser rule), so the FE keeps
    those on ``usePermissions().can(...)``. Mirrors api/objectives.py:_objective_capabilities
    (release branch)."""
    now = datetime.datetime.now(datetime.UTC)
    release_scope = await _release_scope(session, doc)
    sod = await gather_sod_constraints(session, caller.org_id)
    allow_approver_release = await get_allow_approver_release(session, caller.org_id)
    rel_ctx = RequestContext(
        now=now, actor_user_id=str(caller.id), allow_approver_release=allow_approver_release
    )
    rel_grants = await gather_grants(session, caller.id, caller.org_id, "document.release")
    release_cap = authorize(
        rel_grants, "document.release", release_scope, rel_ctx, sig_hook=True, sod=sod
    ).allow
    return {"release": release_cap}
```

In `get_review_endpoint`, compute + pass the block (it already has `_doc` from `_load_review`):

```python
    mr, _doc = await _load_review(session, caller, review_id)
    row = await mr_repo.get_review_row(session, review_id)
    if row is None:  # pragma: no cover — the satellite exists, so the base must too
        raise ProblemException(status=404, code="not_found", title="Management Review not found")
    _mr, ident, title, state = row
    caps = await _mr_capabilities(session, caller, _doc)
    out = _mgmt_review(mr, identifier=ident, title=title, current_state=state, capabilities=caps)
    out["inputs"] = [_review_input(ri) for ri in await list_inputs(session, review_id)]
    out["outputs"] = [_review_output(ro) for ro in await list_outputs(session, review_id)]
    return out
```

- [ ] **Step 5: Add the SoD-2 integration test**

Append to `apps/api/tests/integration/test_mgmt_review_actions.py`. Drive an MR to **Approved** (stop before release) and assert the frozen-version author gets `release=false`, a distinct releaser gets `release=true`:

```python
async def test_capabilities_release_reflects_sod2(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Codex #1: capabilities.release is false for the frozen-minutes author (SoD-2) and true for a
    distinct document.release holder — computed at detail-read time."""
    salt = uuid.uuid4().hex[:8]
    submitter, approver, releaser = f"mr-sm-{salt}", f"mr-ap-{salt}", f"mr-rl-{salt}"
    hs = _auth(token_factory, submitter)
    hap = _auth(token_factory, approver)
    hrl = _auth(token_factory, releaser)
    await _grant(submitter, ("mgmtReview.create", "mgmtReview.read", "mgmtReview.record_outputs",
                             "document.release", "document.read"))
    await s5.grant_role(approver, "Approver")
    await _grant(releaser, ("document.release", "document.read", "mgmtReview.read"))

    # create → submit → approve  (NOT released)
    r = await app_client.post(
        "/api/v1/management-reviews", headers=hs,
        json={"title": f"Caps review {salt}", "period_label": "2026"},
    )
    rid = r.json()["id"]
    sub = await app_client.post(f"/api/v1/management-reviews/{rid}/submit-review", headers=hs)
    assert sub.status_code == 200, sub.text
    task_id = await s5.task_for_doc(rid)
    dec = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hap, json={"outcome": "approve"}
    )
    assert dec.status_code == 200, dec.text

    # the submitter authored the frozen minutes → SoD-2 denies their own release
    as_author = (await app_client.get(f"/api/v1/management-reviews/{rid}", headers=hs)).json()
    assert as_author["capabilities"]["release"] is False
    # a distinct releaser is allowed
    as_releaser = (await app_client.get(f"/api/v1/management-reviews/{rid}", headers=hrl)).json()
    assert as_releaser["capabilities"]["release"] is True

    # capabilities is detail-only — absent on list rows
    lst = (await app_client.get("/api/v1/management-reviews", headers=hs)).json()
    assert all("capabilities" not in row for row in lst["data"])
```

- [ ] **Step 6: Run the serializer unit test + static gates**

Run: `cd apps/api && uv run pytest tests/unit/test_mgmt_review_serializer.py -v`
Expected: PASS.

Run: `cd apps/api && uv run ruff check src/easysynq_api/api/mgmt_review.py && uv run ruff format --check src/easysynq_api/api/mgmt_review.py && uv run mypy src/easysynq_api/api/mgmt_review.py`
Expected: clean. (The SoD-2 integration test runs in CI.)

- [ ] **Step 7: Commit**

```bash
git add apps/api/src/easysynq_api/api/mgmt_review.py apps/api/tests/unit/test_mgmt_review_serializer.py apps/api/tests/integration/test_mgmt_review_actions.py
git commit -m "feat(s-mr-3): SoD-aware capabilities.release on the MR detail serializer (Codex #1)"
```

---

## Task 7: Contract (`openapi.yaml`)

**Files:**
- Modify: `packages/contracts/openapi.yaml`

- [ ] **Step 1: Add `spawned_capa_id` to the `ReviewOutput` schema**

Find the `ReviewOutput` schema (it has `spawned_task_id`). Add, with the same nullable style:

```yaml
        spawned_capa_id:
          type: string
          format: uuid
          nullable: true
          description: The CAPA this ACTION output spawned (S-mr-3 one-shot latch), or null.
```

- [ ] **Step 2: Add `capabilities` to `ManagementReviewDetail`**

In the `ManagementReviewDetail` schema (the `allOf` that adds `inputs` + `outputs`), add a `capabilities` property to the object member:

```yaml
        capabilities:
          type: object
          description: Detail-only. The caller's SoD-sensitive lifecycle affordances.
          properties:
            release:
              type: boolean
              description: Whether the caller may release this MR (SoD-2 author/approver ≠ releaser).
          required: [release]
```

- [ ] **Step 3: Add the two new paths**

Add under `paths:` (follow the existing `/management-reviews/{review_id}/outputs/{output_id}` style, with `201` for raise-capa and `200`/`201` for raise-dcr):

```yaml
  /management-reviews/{review_id}/outputs/{output_id}/raise-capa:
    post:
      tags: [management-reviews]
      summary: Spawn a CAPA from an ACTION output (one-shot latch)
      parameters:
        - { name: review_id, in: path, required: true, schema: { type: string, format: uuid } }
        - { name: output_id, in: path, required: true, schema: { type: string, format: uuid } }
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required: [severity]
              properties:
                severity: { type: string, enum: [Critical, Major, Minor] }
      responses:
        "201":
          description: The refreshed output, now carrying spawned_capa_id.
          content:
            application/json:
              schema: { $ref: "#/components/schemas/ReviewOutput" }
        "409": { description: Not an ACTION output, review not tracking, or a CAPA already spawned. }
  /management-reviews/{review_id}/outputs/{output_id}/raise-dcr:
    post:
      tags: [management-reviews]
      summary: Spawn a DCR from an ACTION output (1:N, idempotent)
      parameters:
        - { name: review_id, in: path, required: true, schema: { type: string, format: uuid } }
        - { name: output_id, in: path, required: true, schema: { type: string, format: uuid } }
        - { name: Idempotency-Key, in: header, required: false, schema: { type: string } }
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required: [change_type, change_significance, reason_text]
              properties:
                change_type: { type: string, enum: [REVISE, CREATE, RETIRE] }
                change_significance: { type: string, enum: [MAJOR, MINOR] }
                reason_text: { type: string, minLength: 1, maxLength: 4000 }
                target_document_id: { type: string, format: uuid, nullable: true }
                proposed_effective_from: { type: string, format: date-time, nullable: true }
      responses:
        "201":
          description: A new DCR (source_link_type=mgmt_review).
          content:
            application/json:
              schema: { $ref: "#/components/schemas/Dcr" }
        "200":
          description: An idempotent replay of an already-spawned DCR.
          content:
            application/json:
              schema: { $ref: "#/components/schemas/Dcr" }
```

(If a `Dcr` schema does not already exist in `openapi.yaml`, reference the closest existing DCR response schema used by `POST /dcrs`; grep for `Dcr` first. If none exists, inline a minimal object with the fields from `_dcr`.)

- [ ] **Step 4: Lint the contract**

Run: `/check-contracts`
Expected: redocly lint PASS, no errors.

- [ ] **Step 5: Commit**

```bash
git add packages/contracts/openapi.yaml
git commit -m "docs(s-mr-3): contract — ReviewOutput.spawned_capa_id, MR capabilities, raise-capa/raise-dcr"
```

---

## Task 8: FE types + `useRaiseMrCapa` mutation

**Files:**
- Modify: `apps/web/src/lib/types.ts` (ReviewOutput, MgmtReviewDetail)
- Modify: `apps/web/src/features/management-review/mutations.ts` (new mutation)

- [ ] **Step 1: Add `spawned_capa_id` to `ReviewOutput`**

In `lib/types.ts`, extend the `ReviewOutput` interface:

```typescript
export interface ReviewOutput {
  id: string;
  management_review_id: string;
  output_type: ReviewOutputType;
  description: string;
  owner_user_id: string | null;
  due_date: string | null;
  spawned_task_id: string | null;
  spawned_capa_id: string | null;
}
```

- [ ] **Step 2: Add `capabilities` to `MgmtReviewDetail`**

```typescript
export interface MgmtReviewDetail extends MgmtReview {
  inputs: ReviewInput[];
  outputs: ReviewOutput[];
  capabilities?: { release: boolean };
}
```

- [ ] **Step 3: Add the `useRaiseMrCapa` mutation**

In `features/management-review/mutations.ts`, add `NcSeverity` to the type import and append the hook:

```typescript
import type {
  MgmtReview,
  MgmtReviewCreateBody,
  MgmtReviewDetail,
  MgmtReviewMetaBody,
  NcSeverity,
  ReviewOutput,
  ReviewOutputCreateBody,
  ReviewOutputUpdateBody,
} from "../../lib/types";
```

```typescript
export function useRaiseMrCapa() {
  const api = useApi();
  const invalidate = useInvalidateReview();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, oid, severity }: { id: string; oid: string; severity: NcSeverity }) =>
      api.send<ReviewOutput>(
        "POST",
        `/api/v1/management-reviews/${id}/outputs/${oid}/raise-capa`,
        { severity },
      ),
    onSuccess: (_d, { id }) => {
      invalidate(id);
      void qc.invalidateQueries({ queryKey: ["capas"] });
    },
  });
}
```

- [ ] **Step 4: Typecheck**

Run: `cd apps/web && npx tsc --noEmit`
Expected: errors ONLY at the not-yet-updated `ReviewOutput` fixtures (Task 9/11 fix these) — note them. If `useApi`/`useQueryClient`/`useMutation` are already imported in `mutations.ts` (they are), no import churn beyond `NcSeverity`.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/lib/types.ts apps/web/src/features/management-review/mutations.ts
git commit -m "feat(s-mr-3): FE types (ReviewOutput.spawned_capa_id, MR capabilities) + useRaiseMrCapa"
```

---

## Task 9: FE `RaiseMrCapaModal` component

**Files:**
- Create: `apps/web/src/features/management-review/RaiseMrCapaModal.tsx`
- Test: `apps/web/src/features/management-review/RaiseMrCapaModal.test.tsx`
- Modify: `apps/web/src/test/msw/handlers.ts` (add the raise-capa handler + spawned_capa_id on fixtures)

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/features/management-review/RaiseMrCapaModal.test.tsx`:

```typescript
import { expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { RaiseMrCapaModal } from "./RaiseMrCapaModal";

const RID = "mr-0001-0001-0001-000000000001";
const OID = "ro-2";

it("requires a severity, posts it, and calls onCreated with the spawned capa id", async () => {
  server.use(
    http.post(
      `/api/v1/management-reviews/${RID}/outputs/${OID}/raise-capa`,
      async ({ request }) => {
        const body = (await request.json()) as { severity: string };
        expect(body.severity).toBe("Major");
        return HttpResponse.json(
          {
            id: OID,
            management_review_id: RID,
            output_type: "ACTION",
            description: "x",
            owner_user_id: null,
            due_date: null,
            spawned_task_id: null,
            spawned_capa_id: "capa-99",
          },
          { status: 201 },
        );
      },
    ),
  );
  const onCreated = vi.fn();
  renderWithProviders(
    <RaiseMrCapaModal opened reviewId={RID} outputId={OID} onClose={() => {}} onCreated={onCreated} />,
  );
  // submit is disabled until a severity is picked
  const raise = screen.getByRole("button", { name: "Raise CAPA" });
  expect(raise).toBeDisabled();
  await userEvent.click(screen.getByLabelText("Severity"));
  await userEvent.click(await screen.findByText("Major"));
  expect(raise).toBeEnabled();
  await userEvent.click(raise);
  await waitFor(() => expect(onCreated).toHaveBeenCalledWith("capa-99"));
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd apps/web && npx vitest run src/features/management-review/RaiseMrCapaModal.test.tsx`
Expected: FAIL — module `./RaiseMrCapaModal` not found.

- [ ] **Step 3: Create the component**

Create `apps/web/src/features/management-review/RaiseMrCapaModal.tsx`:

```typescript
import { Alert, Button, Group, Modal, Select, Stack, Text } from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import type { NcSeverity } from "../../lib/types";
import { useRaiseMrCapa } from "./mutations";

export function RaiseMrCapaModal({
  opened,
  reviewId,
  outputId,
  onClose,
  onCreated,
}: {
  opened: boolean;
  reviewId: string;
  outputId: string;
  onClose: () => void;
  onCreated: (capaId: string) => void;
}) {
  const m = useRaiseMrCapa();
  const [severity, setSeverity] = useState<NcSeverity | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setError(null);
    if (!severity) return;
    try {
      const ro = await m.mutateAsync({ id: reviewId, oid: outputId, severity });
      if (ro.spawned_capa_id) onCreated(ro.spawned_capa_id);
      setSeverity(null);
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not raise the CAPA.");
    }
  }

  return (
    <Modal opened={opened} onClose={onClose} title="Raise CAPA from this action">
      <Stack gap="sm">
        {error && <Alert color="red">{error}</Alert>}
        <Text size="sm" c="dimmed">
          Spawns a corrective/preventive action tracked in the CAPA system. Pick its severity.
        </Text>
        <Select
          label="Severity"
          required
          placeholder="Pick a severity"
          value={severity}
          onChange={(v) => setSeverity(v as NcSeverity)}
          data={["Critical", "Major", "Minor"]}
          comboboxProps={{ keepMounted: false }}
        />
        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} loading={m.isPending} disabled={!severity}>
            Raise CAPA
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
```

- [ ] **Step 4: Add the MSW handler + fix the detail fixture's outputs**

In `apps/web/src/test/msw/handlers.ts`: every `ReviewOutput` fixture must now carry `spawned_capa_id`. Update `mgmtReviewDetailFixture.outputs` so each output has `spawned_capa_id: null` (tsc will flag any missed one). Add a default raise-capa handler in the management-review block (after the `outputs` POST):

```typescript
  http.post("/api/v1/management-reviews/:id/outputs/:oid/raise-capa", () =>
    HttpResponse.json(
      { ...mgmtReviewDetailFixture.outputs[1], spawned_capa_id: "capa-spawned-0001" },
      { status: 201 },
    )),
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd apps/web && npx vitest run src/features/management-review/RaiseMrCapaModal.test.tsx`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/features/management-review/RaiseMrCapaModal.tsx apps/web/src/features/management-review/RaiseMrCapaModal.test.tsx apps/web/src/test/msw/handlers.ts
git commit -m "feat(s-mr-3): FE RaiseMrCapaModal (severity-required) + MSW raise-capa handler"
```

---

## Task 10: FE `ReviewOutputsSection` — Raise/View-CAPA affordances

**Files:**
- Modify: `apps/web/src/features/management-review/ReviewOutputsSection.tsx`
- Modify: `apps/web/src/features/management-review/ReviewOutputsSection.test.tsx`

- [ ] **Step 1: Add the failing test**

Append to `ReviewOutputsSection.test.tsx`. First add `spawned_capa_id: null` to the existing `DECISION`/`ACTION`/`IMPROVEMENT` fixtures (tsc requires it), then a new test. Note the new `tracking` prop:

```typescript
it("shows Raise CAPA on an ACTION row when tracking + capa.create, and View CAPA when spawned", async () => {
  grant("capa.create");
  const spawned: ReviewOutput = { ...ACTION, id: "ro-9", spawned_capa_id: "capa-77" };
  renderWithProviders(
    <ReviewOutputsSection reviewId={REVIEW_ID} outputs={[ACTION, spawned]} editable={false} tracking />,
  );
  // un-spawned ACTION → a Raise CAPA button
  await waitFor(() => expect(screen.getByRole("button", { name: "Raise CAPA" })).toBeInTheDocument());
  // already-spawned ACTION → a View CAPA deep-link
  const view = screen.getByRole("link", { name: /View CAPA/ });
  expect(view).toHaveAttribute("href", "/capa?capa=capa-77");
});

it("hides Raise CAPA without capa.create", async () => {
  grant("mgmtReview.read");
  renderWithProviders(
    <ReviewOutputsSection reviewId={REVIEW_ID} outputs={[ACTION]} editable={false} tracking />,
  );
  await waitFor(() => expect(screen.getByText("Action")).toBeInTheDocument());
  expect(screen.queryByRole("button", { name: "Raise CAPA" })).not.toBeInTheDocument();
});

it("has no accessibility violations with the Raise affordance", async () => {
  grant("capa.create");
  const { container } = renderWithProviders(
    <ReviewOutputsSection reviewId={REVIEW_ID} outputs={[ACTION]} editable={false} tracking />,
  );
  await waitFor(() => expect(screen.getByText("Action")).toBeInTheDocument());
  const { axe } = await import("jest-axe");
  expect(await axe(container)).toHaveNoViolations();
});
```

(Add `import { ReviewOutput }` is already present; the `grant(...)` helper already exists in the file.)

- [ ] **Step 2: Run to verify it fails**

Run: `cd apps/web && npx vitest run src/features/management-review/ReviewOutputsSection.test.tsx`
Expected: FAIL — `tracking` prop unknown + no Raise/View affordance.

- [ ] **Step 3: Implement the affordances**

Rewrite `ReviewOutputsSection.tsx`:

```typescript
import { Anchor, Badge, Button, Card, Group, Stack, Text, Title } from "@mantine/core";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import { usePermissions } from "../../app/shell/usePermissions";
import { useTask } from "../review/hooks";
import { TaskStateBadge } from "../document/TaskStateBadge";
import type { ReviewOutput } from "../../lib/types";
import { OUTPUT_LABEL } from "./labels";
import { useDeleteOutput } from "./mutations";
import { AddOutputModal } from "./AddOutputModal";
import { RaiseMrCapaModal } from "./RaiseMrCapaModal";

function ActionRow({ output, nameOf }: { output: ReviewOutput; nameOf: (id: string | null) => string }) {
  const { data: task } = useTask(output.spawned_task_id ?? null, { retry: false });
  return (
    <Group gap="xs" wrap="nowrap">
      <Text size="sm">{output.description}</Text>
      <Text size="xs" c="dimmed">
        · {nameOf(output.owner_user_id)}
        {output.due_date ? ` · due ${output.due_date}` : ""}
      </Text>
      {output.spawned_task_id && task && <TaskStateBadge state={task.state} />}
    </Group>
  );
}

export function ReviewOutputsSection({ reviewId, outputs, editable, tracking = false }: {
  reviewId: string; outputs: ReviewOutput[]; editable: boolean; tracking?: boolean;
}) {
  const { can } = usePermissions();
  const { data: directory } = useUserDirectory();
  const navigate = useNavigate();
  const del = useDeleteOutput();
  const [addOpen, setAddOpen] = useState(false);
  const [raiseFor, setRaiseFor] = useState<string | null>(null);
  const nameOf = (id: string | null) =>
    id ? (directory?.find((u) => u.id === id)?.display_name ?? "a user") : "—";
  const byType = (t: ReviewOutput["output_type"]) => outputs.filter((o) => o.output_type === t);
  const canEdit = editable && can("mgmtReview.record_outputs");
  const canRaiseCapa = tracking && can("capa.create");

  return (
    <Stack gap="sm">
      <Group justify="space-between">
        <Title order={3}>Review outputs (9.3.3)</Title>
        {canEdit && <Button size="xs" variant="light" onClick={() => setAddOpen(true)}>Add output</Button>}
      </Group>
      {(["DECISION", "ACTION", "IMPROVEMENT"] as const).map((t) => {
        const rows = byType(t);
        if (rows.length === 0) return null;
        return (
          <Card key={t} withBorder>
            <Stack gap="xs">
              <Group justify="space-between">
                <Text fw={600}>{OUTPUT_LABEL[t]}</Text>
                <Badge variant="light">{rows.length}</Badge>
              </Group>
              {rows.map((o) => (
                <Group key={o.id} justify="space-between" wrap="nowrap">
                  {t === "ACTION" ? <ActionRow output={o} nameOf={nameOf} />
                    : <Text size="sm">{o.description}</Text>}
                  <Group gap="xs" wrap="nowrap">
                    {t === "ACTION" && tracking && (
                      o.spawned_capa_id ? (
                        <Anchor component={Link} size="xs" to={`/capa?capa=${o.spawned_capa_id}`}>
                          View CAPA →
                        </Anchor>
                      ) : canRaiseCapa ? (
                        <Button size="compact-xs" variant="light" onClick={() => setRaiseFor(o.id)}>
                          Raise CAPA
                        </Button>
                      ) : null
                    )}
                    {canEdit && (
                      <Button size="compact-xs" variant="subtle" color="red"
                        onClick={() => void del.mutateAsync({ id: reviewId, oid: o.id })}>Remove</Button>
                    )}
                  </Group>
                </Group>
              ))}
            </Stack>
          </Card>
        );
      })}
      {outputs.length === 0 && <Text size="sm" c="dimmed">No outputs recorded yet.</Text>}
      {addOpen && <AddOutputModal opened reviewId={reviewId} onClose={() => setAddOpen(false)} />}
      {raiseFor && (
        <RaiseMrCapaModal
          opened
          reviewId={reviewId}
          outputId={raiseFor}
          onClose={() => setRaiseFor(null)}
          onCreated={(capaId) => navigate(`/capa?capa=${capaId}`)}
        />
      )}
    </Stack>
  );
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd apps/web && npx vitest run src/features/management-review/ReviewOutputsSection.test.tsx`
Expected: PASS (all, incl. the jest-axe smoke).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/management-review/ReviewOutputsSection.tsx apps/web/src/features/management-review/ReviewOutputsSection.test.tsx
git commit -m "feat(s-mr-3): Raise/View-CAPA affordances on ACTION outputs (tracking + capa.create gated)"
```

---

## Task 11: FE `ManagementReviewDetailPage` — gate Release on `capabilities.release` + pass `tracking`

**Files:**
- Modify: `apps/web/src/features/management-review/ManagementReviewDetailPage.tsx`
- Modify: `apps/web/src/features/management-review/ManagementReviewDetailPage.test.tsx`
- Modify: `apps/web/src/test/msw/handlers.ts` (add `capabilities` to the detail fixture)

- [ ] **Step 1: Add/extend the failing test**

In `ManagementReviewDetailPage.test.tsx`, add a fixture variant + tests proving the Release button follows `capabilities.release`:

```typescript
function mgmtReviewApproved(release: boolean) {
  return {
    id: ID,
    identifier: "MR-002",
    title: "Approved review",
    current_state: "Approved" as const,
    period_label: "2026 Annual",
    review_date: "2026-06-12",
    attendees: null,
    close_state: null,
    closed_at: null,
    created_at: "2026-06-01T09:00:00+00:00",
    inputs: [],
    outputs: [],
    capabilities: { release },
  };
}

it("shows Release when capabilities.release is true and state is Approved", async () => {
  grantRecordOutputs();
  server.use(http.get("/api/v1/management-reviews/:id", () => HttpResponse.json(mgmtReviewApproved(true))));
  renderAt(ID);
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Release" })).toBeInTheDocument(),
  );
});

it("hides Release when capabilities.release is false (SoD-2), even at Approved", async () => {
  grantRecordOutputs();
  server.use(http.get("/api/v1/management-reviews/:id", () => HttpResponse.json(mgmtReviewApproved(false))));
  renderAt(ID);
  await waitFor(() => expect(screen.getByText("Approved review")).toBeInTheDocument());
  expect(screen.queryByRole("button", { name: "Release" })).not.toBeInTheDocument();
});
```

(Use the file's existing `grantRecordOutputs`, `renderAt`, `server`, `http`, `HttpResponse` helpers. If `grantRecordOutputs` grants only `mgmtReview.record_outputs`, that's correct — Release must NOT depend on `document.release` via `can()` anymore.)

- [ ] **Step 2: Run to verify it fails**

Run: `cd apps/web && npx vitest run src/features/management-review/ManagementReviewDetailPage.test.tsx`
Expected: the "hides Release when false" test FAILS (the current code shows Release whenever `can("document.release")` regardless of `capabilities`).

- [ ] **Step 3: Change the gate + pass `tracking`**

In `ManagementReviewDetailPage.tsx`, update the comment + the `canRelease` line:

```typescript
  const isDraft = mr.current_state === "Draft";
  // Affordances derive from state + permission key — EXCEPT release, which the serializer computes
  // with the SoD-2 overlay (author/approver ≠ releaser) so the button never show-then-403s (Codex #1).
  const canRecord = can("mgmtReview.record_outputs");
  const canCompile = canRecord && isDraft;
  const canSubmit = canRecord && isDraft;
  const canRelease = mr.capabilities?.release === true && mr.current_state === "Approved";
  const canClose = canRecord && mr.close_state === "ActionsTracked";
```

Find the existing `<ReviewOutputsSection ... />` usage and add the `tracking` prop:

```typescript
<ReviewOutputsSection
  reviewId={mr.id}
  outputs={mr.outputs}
  editable={isDraft}
  tracking={mr.close_state === "ActionsTracked"}
/>
```

(Match the existing prop set — keep `editable` as it currently is; only ADD `tracking`.)

- [ ] **Step 4: Add `capabilities` to the default detail fixture**

In `handlers.ts`, add `capabilities: { release: true }` to `mgmtReviewDetailFixture` so the default detail handler typechecks and existing tests keep a defined value.

- [ ] **Step 5: Run to verify it passes**

Run: `cd apps/web && npx vitest run src/features/management-review/ManagementReviewDetailPage.test.tsx`
Expected: PASS (both new tests + the existing suite, incl. its jest-axe smoke).

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/features/management-review/ManagementReviewDetailPage.tsx apps/web/src/features/management-review/ManagementReviewDetailPage.test.tsx apps/web/src/test/msw/handlers.ts
git commit -m "feat(s-mr-3): gate MR Release on capabilities.release (Codex #1) + pass tracking to outputs"
```

---

## Task 12: Full gates + diff-critic + docs

**Files:**
- Modify: `docs/slice-history.md` (new S-mr-3 entry)
- Modify: `CLAUDE.md` (a Recent-learnings line + Current-status pointer)

- [ ] **Step 1: Run the full web gate**

Run: `/check-web`
Expected: eslint clean, `tsc --noEmit` clean (every `ReviewOutput` fixture now carries `spawned_capa_id`; the `MgmtReviewDetail` fixtures carry `capabilities`), build OK, full vitest suite green. Fix any cross-file fixture drift surfaced here (the per-file runs are blind to it).

- [ ] **Step 2: Run the full API + migrations + contracts gates**

Run: `/check-api`
Expected: ruff + format-check + mypy-strict clean; unit tests pass. (Integration is CI-only here.)

Run: `/check-migrations`
Expected: round-trip + `alembic check` clean.

Run: `/check-contracts`
Expected: redocly lint clean.

- [ ] **Step 3: Run the diff-critic adversarial review**

Use the `Agent` tool, `subagent_type: diff-critic`, on the branch diff vs `main`. Focus it on: the close-gate decouple (F3 — is the absence of a change correct?), the one-shot CAPA latch under the FOR-UPDATE lock, the DCR idempotency/IntegrityError path, the FK `ondelete=RESTRICT` vs CAPA disposal, the no-signature assertion (R43), and the `capabilities.release` SoD-2 computation. Fold only CONFIRMED findings; fix inline.

- [ ] **Step 4: Write the slice-history entry**

Add an S-mr-3 entry to `docs/slice-history.md` following the existing per-slice narrative shape: the CAPA un-reserve (FK migration 0051, on-demand one-shot spawn, F3 decouple), the DCR backend-only seam (mgmt_review source-link, 1:N idempotent, reason_class=mgmt_review), Codex #1 (capabilities.release), the deferrals (minutes-revision, improvement_initiative, the MR→DCR FE), and the test deltas.

- [ ] **Step 5: Add the CLAUDE.md learnings line + Current-status pointer**

Prepend a `2026-06-13 — S-mr-3 …` bullet to the **Recent learnings** block (newest first; cap ~12, demote the oldest if needed) capturing: the two-different-seams correction (spawned_initiative_id ≠ DCR link), the DCR-domain-has-no-FE finding (→ backend-only), the build_capa-not-raise_capa un-reserve, the FOR-UPDATE one-shot latch, F3 decouple, and migration `0051` (head 0050→0051). Update the **Current status** head-migration line to `0051`.

- [ ] **Step 6: Commit**

```bash
git add docs/slice-history.md CLAUDE.md
git commit -m "docs(s-mr-3): slice-history entry + CLAUDE.md learnings (head 0051)"
```

- [ ] **Step 7: Push + open the PR (on owner's OK)**

After all gates are green and diff-critic findings are folded, push the branch and open a PR against `main` (use the `/pr` skill or `gh`). Then run the pre-merge Chrome-MCP live smoke (rebuild api+worker+web; SYSTEM overrides on the live `demo` `app_user`, org **AHT**: add `capa.create`, `capa.read`, `changeRequest.create`, `document.release`; pre-create the row + grant before login). The owner does the Keycloak login. Squash-merge after green CI on the owner's OK.

---

## Self-review (plan author — completed)

- **Spec coverage:** CAPA un-reserve → Tasks 1–4; DCR backend-only → Tasks 1,2,3,5; Codex #1 → Tasks 6,8,11; contract → Task 7; FE CAPA → Tasks 8–11; migration `0051` → Task 2; close-gate decouple (F3) → Task 3 regression test; deferrals (minutes-revision, improvement_initiative, MR→DCR FE) → carried in docs (Task 12). ✓
- **Placeholder scan:** every code step shows complete code; the one judgment call (the `Dcr` openapi schema ref) is flagged with a concrete fallback. ✓
- **Type consistency:** `spawn_capa_for_output` / `spawn_dcr_for_output` signatures, `_mr_capabilities`, `useRaiseMrCapa({id,oid,severity})`, `RaiseMrCapaModal` props, and the `tracking` prop are used identically across tasks. The new event types + `DcrReasonClass.mgmt_review` are defined (Task 1) before use (Tasks 3,5). ✓
- **Known cross-file fixture ripple:** adding `spawned_capa_id` to `ReviewOutput` and `capabilities` to `MgmtReviewDetail` forces updates to ALL such fixtures — surfaced by `tsc` in Tasks 8/9/10/11 and gated by `/check-web` in Task 12.
