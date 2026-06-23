# S-notify-5a — Awareness events (`doc.released`, read-scope-filtered) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Emit a `doc.released` awareness notification to exactly the users who can read the document, via a transactional outbox fanned out by a Beat worker — without ever blocking a release.

**Architecture:** A best-effort SAVEPOINT hook in the SERIALIZABLE `_cutover` writes one `awareness_event` outbox row, atomic with the release. A `awareness_fan_out` Beat (@120 s) claims pending rows, resolves the read-scoped audience via the real PDP (per-user `authorize(document.read)` — deny-wins, ABAC-correct), and creates per-recipient notification (+ digest/email) rows reusing the slice-3a machinery, idempotently and version-discriminated so each new Effective version re-notifies.

**Tech Stack:** FastAPI / Python 3.12 · SQLAlchemy 2.x async · Alembic · PostgreSQL 16 · Celery/Beat · pytest (unit + testcontainers integration).

**Spec:** `docs/superpowers/specs/2026-06-23-s-notify-5a-awareness-events-design.md` (validated 2026-06-23).

## Global Constraints

- **BE-only.** No web/openapi change. No new endpoint (reads are the existing self-scoped `GET /notifications`).
- **No new permission key** (R38; catalog stays **102**). The audience resolver only *consumes* `document.read`.
- **Migration head `0065` → `0066`** (next `0066`). Single tree.
- **The migration must round-trip** `alembic up↔down↔alembic check` clean on a throwaway PG16 (`/check-migrations`) **and** survive a populated-DB downgrade.
- **Family migration traps (CI-blind):** `REVOKE DELETE` on a new TABLE (0010's `ALTER DEFAULT PRIVILEGES` auto-grants all DML); migration-managed partial indexes go in `migrations/env.py::_MIGRATION_MANAGED_INDEXES` **and stay out of the ORM `__table_args__`**; a new model module **must** be imported in `db/models/__init__.py` + `__all__`; the downgrade template-delete is `NOT EXISTS`-guarded (the `notification.template_id` RESTRICT FK).
- **R53 (load-bearing):** a notification bug must **never** block a release/transition. The emit hook is a best-effort SAVEPOINT; on a SERIALIZABLE race it re-raises so `_cutover`'s race-loss path produces the clean 409.
- **R32:** awareness email/in-app payload is **summary + deep link only** (no controlled content). Only whitelisted variables substitute.
- **Welded-path parity:** the slice-1..4 `test_notification_*` suites pin `dispatch._enqueue_one`. Any extraction must keep them green — run the **full `/check-api`** (not a per-file unit run) as the parity gate.
- **Worker idempotency:** PK-pinned `FOR UPDATE SKIP LOCKED` + `populate_existing=True` + fresh session per event + one commit per event. **No per-event advisory lock** (the `outbox_drain` precedent — do not add one to "match" digest/escalation).
- **Integration assertions are delta-based / run-scoped** (never assume a clean *or* dirty shared DB); FK-ordered cleanup of any org/user a test creates (the S-notify-4 `test_restore` lesson — a leaked second `Organization` aborts `test_restore`'s `scalar_one()`).
- **Verify command:** API fast loop = `/check-api` (ruff + format-check + mypy-strict + `pytest -m unit`); integration = `cd apps/api && uv run pytest -m integration tests/integration/<file>.py` (needs Docker). Migrations = `/check-migrations`.

---

## File Structure

**Create:**
- `apps/api/src/easysynq_api/db/models/awareness_event.py` — the `AwarenessEvent` ORM model (the outbox row).
- `migrations/versions/0066_awareness_events.py` — the migration (table + `notification.subject_version_id` + 2 partial indexes + `REVOKE DELETE` + `doc.released` template seed).
- `apps/api/src/easysynq_api/services/authz/resource.py` — `build_document_resource_context` (extracted from `api/documents`).
- `apps/api/src/easysynq_api/services/authz/audience.py` — `resolve_document_readers` (the read-scope audience resolver).
- `apps/api/src/easysynq_api/services/notifications/awareness.py` — `record_awareness_event` (the SERIALIZABLE-aware emit helper).
- `apps/api/src/easysynq_api/services/notifications/fanout.py` — `fan_out_awareness` / `process_one_awareness_event`.
- Tests: `apps/api/tests/unit/test_awareness_record.py`, `apps/api/tests/integration/test_authz_resource_extraction.py`, `apps/api/tests/integration/test_audience_resolver.py`, `apps/api/tests/integration/test_awareness_enqueue.py`, `apps/api/tests/integration/test_awareness_e2e.py`, plus an addition to the existing task-registration unit test.

**Modify:**
- `apps/api/src/easysynq_api/db/models/notification.py` — add nullable `subject_version_id` column.
- `apps/api/src/easysynq_api/db/models/__init__.py` — import `AwarenessEvent` + `__all__`.
- `migrations/env.py` (repo root) — add the 2 partial indexes to `_MIGRATION_MANAGED_INDEXES`.
- `apps/api/src/easysynq_api/api/documents.py` — `_document_scope_by_id` becomes a thin delegate.
- `apps/api/src/easysynq_api/services/notifications/dispatch.py` — extract `resolve_delivery` shared helper; add `enqueue_awareness_one`.
- `apps/api/src/easysynq_api/services/notifications/constants.py` — `EVENT_DOC_RELEASED` + `VARIABLE_WHITELIST` entry.
- `apps/api/src/easysynq_api/services/vault/lifecycle.py` — the emit hook in `_cutover`.
- `apps/api/src/easysynq_api/tasks/notifications.py` + `tasks/app.py` — the `awareness_fan_out` Beat + schedule.
- `apps/api/tests/unit/test_notification_task_registration.py` — assert the new Beat task.

---

## Task 1: Migration 0066 — the `awareness_event` table, `notification.subject_version_id`, the dedup + claim indexes, the `doc.released` template

**Files:**
- Create: `apps/api/src/easysynq_api/db/models/awareness_event.py`
- Modify: `apps/api/src/easysynq_api/db/models/notification.py` (add `subject_version_id`)
- Modify: `apps/api/src/easysynq_api/db/models/__init__.py` (import + `__all__`)
- Modify: `migrations/env.py` (`_MIGRATION_MANAGED_INDEXES`)
- Create: `migrations/versions/0066_awareness_events.py`

**Interfaces:**
- Produces: `AwarenessEvent` ORM model (`id, org_id, event_key, subject_type, subject_id, subject_version_id, actor_user_id, context, occurred_at, fanned_out_at, created_at`); `notification.subject_version_id` column; the partial indexes `ix_awareness_event_pending` (claim scan) and `uq_notification_dedup_awareness` (version-discriminated dedup); the seeded `doc.released` template.

- [ ] **Step 1: Write the `AwarenessEvent` model**

Create `apps/api/src/easysynq_api/db/models/awareness_event.py` (mirror the `SlaPolicy` 0065 precedent; the partial index `ix_awareness_event_pending` is **migration-managed → NOT in `__table_args__`**):

```python
"""Awareness-event outbox (slice S-notify-5a, doc 10 §9.2).

One row per QMS lifecycle awareness fact (v1: ``doc.released``). Written best-effort + atomic with
the release inside the SERIALIZABLE ``_cutover`` (services/vault/lifecycle.py), then fanned out by the
``awareness_fan_out`` Beat (services/notifications/fanout.py): the worker resolves the read-scoped
audience and creates per-recipient notification rows, stamping ``fanned_out_at`` once.

Created by migration 0066. The app role holds INSERT/SELECT/UPDATE but **not DELETE** (the 0066 REVOKE
counters 0010's ``ALTER DEFAULT PRIVILEGES`` auto-grant). The claim index ``ix_awareness_event_pending``
is migration-managed (a partial index round-trips wrong if declared on the ORM) — see migrations/env.py.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class AwarenessEvent(Base):
    __tablename__ = "awareness_event"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "organization.id", ondelete="RESTRICT", name="fk_awareness_event_org_id_organization"
        ),
        nullable=False,
    )
    event_key: Mapped[str] = mapped_column(Text, nullable=False)
    subject_type: Mapped[str] = mapped_column(Text, nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # The Effective document_version.id at release — the dedup discriminator so each new version
    # re-notifies (spec §5). Plain uuid (no FK — operational outbox, mirrors subject_id); always set
    # for doc.released, nullable for future non-version awareness keys.
    subject_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "app_user.id", ondelete="RESTRICT", name="fk_awareness_event_actor_user_id_app_user"
        ),
        nullable=True,
    )
    context: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    occurred_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fanned_out_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

- [ ] **Step 2: Register the model + add the notification column**

In `apps/api/src/easysynq_api/db/models/__init__.py`, add (alphabetical) the import near the top of the import block and `"AwarenessEvent",` in `__all__`:

```python
from .awareness_event import AwarenessEvent   # near the other A* / audit imports
```
```python
    "AwarenessEvent",   # in __all__, alphabetical position
```

In `apps/api/src/easysynq_api/db/models/notification.py`, add a nullable column **immediately after** the existing `subject_id` (line 74):

```python
    subject_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # S-notify-5a: the Effective version id for an awareness row — discriminates the awareness
    # dedup index so each new Effective version re-notifies (NULL for task rows). No FK (mirror
    # subject_id); the dedup partial-unique is migration-managed (uq_notification_dedup_awareness).
    subject_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
```

- [ ] **Step 3: Add the 2 partial indexes to `migrations/env.py`**

In `migrations/env.py`, add both names to `_MIGRATION_MANAGED_INDEXES` (so `alembic check` ignores them — they are created in the migration, not the ORM):

```python
        "ix_task_timer_pending",
        "ix_awareness_event_pending",          # S-notify-5a claim scan
        "uq_notification_dedup_awareness",     # S-notify-5a version-discriminated dedup
    }
)
```

- [ ] **Step 4: Write the migration**

Create `migrations/versions/0066_awareness_events.py`:

```python
"""awareness events (doc.released) — slice S-notify-5a

Revision ID: 0066_awareness_events
Revises: 0065_escalation_timers
Create Date: 2026-06-23
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0066_awareness_events"
down_revision: str | None = "0065_escalation_timers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "easysynq_app"

_DOC_RELEASED_EMAIL_BODY = (
    "Hi {{recipient.first_name}},\n\n"
    'A new Effective version of {{subject.identifier}} — "{{subject.title}}" '
    "({{version.label}}) has been released.\n\n"
    "Open it: {{deep_link}}\n\n"
    "— EasySynQ\n"
    "Manage your notifications: {{prefs_link}}"
)


def upgrade() -> None:
    bind = op.get_bind()

    # 1. The awareness_event outbox table.
    op.create_table(
        "awareness_event",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_key", sa.Text(), nullable=False),
        sa.Column("subject_type", sa.Text(), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subject_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "context",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fanned_out_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_awareness_event"),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_awareness_event_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["app_user.id"],
            name="fk_awareness_event_actor_user_id_app_user",
            ondelete="RESTRICT",
        ),
    )

    # 2. Claim-scan partial index (migration-managed → excluded from env.py autogenerate).
    op.create_index(
        "ix_awareness_event_pending",
        "awareness_event",
        ["occurred_at"],
        postgresql_where=sa.text("fanned_out_at IS NULL"),
    )

    # 3. The version-discriminated awareness dedup column + partial-unique index on notification.
    op.add_column(
        "notification",
        sa.Column("subject_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "uq_notification_dedup_awareness",
        "notification",
        ["recipient_user_id", "event_key", "subject_type", "subject_id", "subject_version_id"],
        unique=True,
        postgresql_where=sa.text("task_id IS NULL"),
    )

    # 4. App-role grants — awareness_event is an operational outbox: INSERT (emit) + SELECT/UPDATE
    #    (claim + stamp), but NOT DELETE (the 0063 ledger posture). 0010's ALTER DEFAULT PRIVILEGES
    #    already granted full DML, so REVOKE DELETE to enforce; the GRANT is a defensive no-op.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE ON awareness_event TO {_APP_ROLE}';
                EXECUTE 'REVOKE DELETE ON awareness_event FROM {_APP_ROLE}';
            END IF;
        END $$;
        """
    )

    # 5. Seed the doc.released template. Raw INSERT + ON CONFLICT to reference the partial-unique
    #    uq_notification_template_one_effective (event_key, locale WHERE is_effective) — re-upgrade-safe.
    bind.execute(
        sa.text(
            "INSERT INTO notification_template"
            " (id, event_key, locale, version, is_effective,"
            "  in_app_title, in_app_body, email_subject, email_body)"
            " VALUES (:id, 'doc.released', 'en', 1, TRUE,"
            "         :in_app_title, :in_app_body, :email_subject, :email_body)"
            " ON CONFLICT (event_key, locale) WHERE is_effective DO NOTHING"
        ),
        {
            "id": uuid.uuid4(),
            "in_app_title": "{{subject.identifier}} {{version.label}} is now Effective",
            "in_app_body": (
                'A new Effective version of {{subject.identifier}} — "{{subject.title}}"'
                " ({{version.label}}) has been released."
            ),
            "email_subject": "[EasySynQ] Now Effective: {{subject.identifier}} {{version.label}}",
            "email_body": _DOC_RELEASED_EMAIL_BODY,
        },
    )


def downgrade() -> None:
    # Guard the template DELETE: notification.template_id is RESTRICT, and a fan-out that ran after
    # upgrade will have stamped rows referencing this template. A plain DELETE aborts on a populated
    # DB (CI is blind — fresh DB, the worker never fired). Leave it in place when children exist
    # (the 0023/0065 NOT-EXISTS precedent).
    op.execute(
        "DELETE FROM notification_template t "
        "WHERE t.event_key = 'doc.released' "
        "AND NOT EXISTS (SELECT 1 FROM notification n WHERE n.template_id = t.id)"
    )
    op.drop_index("uq_notification_dedup_awareness", table_name="notification")
    op.drop_column("notification", "subject_version_id")
    op.drop_index("ix_awareness_event_pending", table_name="awareness_event")
    op.drop_table("awareness_event")
```

- [ ] **Step 5: Run the migration round-trip + alembic check**

Run: `/check-migrations`
Expected: up↔down↔`alembic check` clean on a throwaway PG16 (no phantom DROP/ADD; the 2 partial indexes are ignored via `_MIGRATION_MANAGED_INDEXES`; the `awareness_event` table + `notification.subject_version_id` column reconcile against the ORM).

Also run the model-import sanity: `cd apps/api && uv run python -c "from easysynq_api.db.models import AwarenessEvent; print(AwarenessEvent.__tablename__)"`
Expected: `awareness_event`

- [ ] **Step 6: Verify the GRANT posture on a live PG16 (migration-reviewer gate)**

After `alembic upgrade head` on a throwaway PG16, run:
`SELECT privilege_type FROM information_schema.role_table_grants WHERE grantee = 'easysynq_app' AND table_name = 'awareness_event' ORDER BY 1;`
Expected: `INSERT, SELECT, UPDATE` (no `DELETE`).

- [ ] **Step 7: Commit**

```bash
git add apps/api/src/easysynq_api/db/models/awareness_event.py apps/api/src/easysynq_api/db/models/__init__.py apps/api/src/easysynq_api/db/models/notification.py migrations/env.py migrations/versions/0066_awareness_events.py
git commit -m "feat(s-notify-5a): migration 0066 — awareness_event outbox + version-discriminated dedup + doc.released template"
```

---

## Task 2: Extract `build_document_resource_context`

**Files:**
- Create: `apps/api/src/easysynq_api/services/authz/resource.py`
- Modify: `apps/api/src/easysynq_api/api/documents.py` (`_document_scope_by_id` → thin delegate)
- Test: `apps/api/tests/integration/test_authz_resource_extraction.py`

**Interfaces:**
- Produces: `build_document_resource_context(session: AsyncSession, doc_id: uuid.UUID) -> ResourceContext` — the doc's authz scope (artifact_id, folder_path, document_level, lifecycle_state, process_ids), with the doc-missing degraded fallback `ResourceContext(artifact_id=str(doc_id))`.
- Consumes (Task 3): the audience resolver calls this once per document.

**Current code (verbatim, `api/documents.py:367-387`)** — the body to move:

```python
async def _document_scope_by_id(session: AsyncSession, doc_id: uuid.UUID) -> ResourceContext:
    doc = await session.get(DocumentedInformation, doc_id)
    if doc is None:
        return ResourceContext(artifact_id=str(doc_id))
    level: str | None = None
    if doc.document_type_id:
        dt = await session.get(DocumentType, doc.document_type_id)
        level = dt.document_level.value if dt else None
    return ResourceContext(
        artifact_id=str(doc.id),
        folder_path=doc.folder_path,
        document_level=level,
        lifecycle_state=doc.current_state.value,
        process_ids=await vault_repo.process_ids_for_doc(session, doc.id),
    )
```

- [ ] **Step 1: Write the failing parity test**

Create `apps/api/tests/integration/test_authz_resource_extraction.py`. Follow the existing `tests/integration/` fixture conventions (the `app_under_test` fixture that repoints `get_sessionmaker()`, and the seeded-doc helpers used in `test_vault.py`). The test asserts the extracted builder yields the same `ResourceContext` as the api delegate for a real seeded doc:

```python
import uuid

import pytest
from easysynq_api.services.authz.resource import build_document_resource_context

pytestmark = pytest.mark.integration


async def test_build_document_resource_context_matches_delegate(app_under_test, seeded_document):
    # seeded_document: a fixture/helper returning a created DocumentedInformation id (reuse the
    # existing test_vault.py creation helper). Assert the builder populates the expected fields.
    from easysynq_api.db.session import get_sessionmaker

    async with get_sessionmaker()() as session:
        rc = await build_document_resource_context(session, seeded_document.id)
    assert rc.artifact_id == str(seeded_document.id)
    assert rc.lifecycle_state is not None  # the doc's current_state.value


async def test_build_document_resource_context_missing_doc_degrades(app_under_test):
    from easysynq_api.db.session import get_sessionmaker

    missing = uuid.uuid4()
    async with get_sessionmaker()() as session:
        rc = await build_document_resource_context(session, missing)
    assert rc.artifact_id == str(missing)
    assert rc.folder_path is None
    assert rc.process_ids == frozenset()
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd apps/api && uv run pytest -m integration tests/integration/test_authz_resource_extraction.py -v`
Expected: FAIL — `ModuleNotFoundError: easysynq_api.services.authz.resource`.

- [ ] **Step 3: Create the extracted builder**

Create `apps/api/src/easysynq_api/services/authz/resource.py`:

```python
"""Document → ResourceContext builder (extracted from api/documents for reuse).

The audience resolver (services/authz/audience.py) and the api document gate both need a document's
authz scope. This is the single builder; api/documents._document_scope_by_id is a thin delegate so
authority still flows api→services (services never imports api).
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models.document_type import DocumentType
from ...db.models.documented_information import DocumentedInformation
from ...domain.authz import ResourceContext
from ..vault import repository as vault_repo


async def build_document_resource_context(
    session: AsyncSession, doc_id: uuid.UUID
) -> ResourceContext:
    """Resolve a document's authz scope (ARTIFACT + folder + doc-class + process_ids + lifecycle).

    Returns a degraded ResourceContext(artifact_id=str(doc_id)) when the doc is missing — the api
    gate relies on this fallback, so it MUST be preserved byte-identically.
    """
    doc = await session.get(DocumentedInformation, doc_id)
    if doc is None:
        return ResourceContext(artifact_id=str(doc_id))
    level: str | None = None
    if doc.document_type_id:
        dt = await session.get(DocumentType, doc.document_type_id)
        level = dt.document_level.value if dt else None
    return ResourceContext(
        artifact_id=str(doc.id),
        folder_path=doc.folder_path,
        document_level=level,
        lifecycle_state=doc.current_state.value,
        process_ids=await vault_repo.process_ids_for_doc(session, doc.id),
    )
```

- [ ] **Step 4: Make `_document_scope_by_id` a thin delegate**

In `api/documents.py`, replace the body of `_document_scope_by_id` (lines 367-387) with the delegate, and add the import near the other `services.authz` imports:

```python
from ..services.authz.resource import build_document_resource_context
```
```python
async def _document_scope_by_id(session: AsyncSession, doc_id: uuid.UUID) -> ResourceContext:
    return await build_document_resource_context(session, doc_id)
```

(Leave `_document_scope` (line 355) unchanged — it still calls `_document_scope_by_id`. The `DocumentType`/`DocumentedInformation`/`vault_repo` imports stay in `documents.py` — they are used elsewhere in the module; do not remove them.)

- [ ] **Step 5: Run the parity test + the FULL api gate**

Run: `cd apps/api && uv run pytest -m integration tests/integration/test_authz_resource_extraction.py -v`
Expected: PASS.

Run: `/check-api`
Expected: green (ruff + mypy-strict + all unit tests). The document-gate tests (`test_documents*`, `test_authz*`) must stay green — they are the extraction parity backstop.

- [ ] **Step 6: Commit**

```bash
git add apps/api/src/easysynq_api/services/authz/resource.py apps/api/src/easysynq_api/api/documents.py apps/api/tests/integration/test_authz_resource_extraction.py
git commit -m "refactor(s-notify-5a): extract build_document_resource_context; documents._document_scope_by_id delegates"
```

---

## Task 3: `resolve_document_readers` — the read-scope audience resolver

**Files:**
- Create: `apps/api/src/easysynq_api/services/authz/audience.py`
- Test: `apps/api/tests/integration/test_audience_resolver.py`

**Interfaces:**
- Consumes: `build_document_resource_context` (Task 2); `gather_grants(session, user_id, org_id, "document.read") -> list[ResolvedGrant]`; `authorize(grants, "document.read", resource, RequestContext(now=now)) -> Decision` (`.allow`).
- Produces: `resolve_document_readers(session, org_id, doc_id, *, now) -> list[uuid.UUID]` — all ACTIVE users in `org_id` the PDP allows to read `doc_id` at fan-out time.

- [ ] **Step 1: Write the failing tests**

Create `apps/api/tests/integration/test_audience_resolver.py`. These need real grants in a DB (testcontainers), so they are integration tests. Seed via the existing authz test helpers (a role grant + a permission_override, as `test_authz.py` does). Cover the deny-wins + scope + active cases:

```python
import datetime
import uuid

import pytest

pytestmark = pytest.mark.integration


async def test_system_reader_included_and_deny_override_excluded(app_under_test, authz_fixture):
    """A SYSTEM document.read holder is in the audience; a SYSTEM-scope DENY override beats it."""
    from easysynq_api.db.session import get_sessionmaker
    from easysynq_api.services.authz.audience import resolve_document_readers

    now = datetime.datetime.now(datetime.UTC)
    async with get_sessionmaker()() as session:
        readers = await resolve_document_readers(
            session, authz_fixture.org_id, authz_fixture.doc_id, now=now
        )
    assert authz_fixture.system_reader_id in readers          # role ALLOW @ SYSTEM
    assert authz_fixture.denied_reader_id not in readers      # DENY override beats the ALLOW
    assert authz_fixture.no_grant_user_id not in readers      # deny-by-default
    assert authz_fixture.inactive_reader_id not in readers    # LOCKED/DISABLED/RETIRED excluded


async def test_actor_self_suppression_is_caller_side(app_under_test, authz_fixture):
    """resolve_document_readers returns ALL readers incl. an actor; the fan-out subtracts the actor."""
    from easysynq_api.db.session import get_sessionmaker
    from easysynq_api.services.authz.audience import resolve_document_readers

    now = datetime.datetime.now(datetime.UTC)
    async with get_sessionmaker()() as session:
        readers = await resolve_document_readers(
            session, authz_fixture.org_id, authz_fixture.doc_id, now=now
        )
    # The resolver does not self-suppress (that is the fan-out's job — Task 7); it returns the actor
    # if the actor can read. Assert the resolver is audience-complete.
    assert authz_fixture.system_reader_id in readers
```

(The `authz_fixture` builds: one org, a doc, a `system_reader` with `document.read` @ SYSTEM via a role grant, a `denied_reader` with the same role grant PLUS a `document.read` DENY override @ SYSTEM, a `no_grant_user`, and an `inactive_reader` (status RETIRED) with the role grant. Reuse the seed helpers in `tests/integration/test_authz.py`. **FK-ordered teardown** of the org/users it creates.)

- [ ] **Step 2: Run to verify it fails**

Run: `cd apps/api && uv run pytest -m integration tests/integration/test_audience_resolver.py -v`
Expected: FAIL — `ModuleNotFoundError: easysynq_api.services.authz.audience`.

- [ ] **Step 3: Implement the resolver**

Create `apps/api/src/easysynq_api/services/authz/audience.py`:

```python
"""Read-scope audience resolver (slice S-notify-5a, doc 10 §9.2).

The inverse of an access check: given a document, who may read it? Used by the awareness fan-out to
target doc.released at exactly the document.read holders. This is the ONLY ABAC-correct answer — DENY
overrides and time-windowed predicates are not join-expressible (spec §4) — so it is the per-user PDP
loop, reusing the exact path every request takes. Resolved at fan-out time (an R32-bounded residual,
spec §4/§15: not re-verified at later digest send).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models.app_user import AppUser, UserStatus
from ...domain.authz import RequestContext, authorize
from .repository import gather_grants
from .resource import build_document_resource_context

# Mirror recipients.py / auth dependencies: a deactivated user is never an awareness recipient.
_INACTIVE = {UserStatus.LOCKED, UserStatus.DISABLED, UserStatus.RETIRED}
_READ = "document.read"


async def resolve_document_readers(
    session: AsyncSession,
    org_id: uuid.UUID,
    doc_id: uuid.UUID,
    *,
    now: datetime.datetime,
) -> list[uuid.UUID]:
    """All ACTIVE users in ``org_id`` who can read ``doc_id`` per the real PDP (deny-wins,
    ABAC-complete), evaluated at ``now``. source_ip is None (worker has no request IP), so an
    ip_allow-gated grant fails to match → fail-safe under-inclusion (spec §4)."""
    resource = await build_document_resource_context(session, doc_id)
    user_ids = (
        (
            await session.execute(
                select(AppUser.id).where(
                    AppUser.org_id == org_id,
                    AppUser.status.notin_(_INACTIVE),
                )
            )
        )
        .scalars()
        .all()
    )
    ctx = RequestContext(now=now)  # source_ip=None, step_up_satisfied=True, actor_user_id=None
    readers: list[uuid.UUID] = []
    for uid in user_ids:
        grants = await gather_grants(session, uid, org_id, _READ)
        if authorize(grants, _READ, resource, ctx).allow:
            readers.append(uid)
    return readers
```

- [ ] **Step 4: Run the tests**

Run: `cd apps/api && uv run pytest -m integration tests/integration/test_audience_resolver.py -v`
Expected: PASS (deny-wins excludes the denied reader; SYSTEM reader included; inactive + no-grant excluded).

- [ ] **Step 5: Run mypy + ruff**

Run: `/check-api`
Expected: green.

- [ ] **Step 6: Commit**

```bash
git add apps/api/src/easysynq_api/services/authz/audience.py apps/api/tests/integration/test_audience_resolver.py
git commit -m "feat(s-notify-5a): resolve_document_readers — read-scope audience resolver (deny-wins, fan-out-time)"
```

---

## Task 4: `dispatch.resolve_delivery` (shared helper) + `enqueue_awareness_one` + the `doc.released` whitelist

**Files:**
- Modify: `apps/api/src/easysynq_api/services/notifications/constants.py` (`EVENT_DOC_RELEASED` + whitelist)
- Modify: `apps/api/src/easysynq_api/services/notifications/dispatch.py` (extract `resolve_delivery`; add `enqueue_awareness_one`)
- Test: `apps/api/tests/integration/test_awareness_enqueue.py`

**Interfaces:**
- Produces: `enqueue_awareness_one(session, *, org_id, subject, subject_id, subject_version_id, recipient, event_key, context_vars, now, org_enabled, org_pierce) -> EnqueueOutcome` (a sibling of `_enqueue_one` with `task_id=NULL`, the version-discriminated dedup target); `resolve_delivery(...) -> _DeliveryPlan` (the extracted class/mode/email-eligibility resolution); `EVENT_DOC_RELEASED = "doc.released"`.
- Consumes (Task 7): the fan-out worker calls `enqueue_awareness_one` per reader.

- [ ] **Step 1: Add the event key + whitelist in `constants.py`**

```python
EVENT_DIGEST_DAILY = "digest.daily"
EVENT_DOC_RELEASED = "doc.released"
```
Add the whitelist entry (the `version.label` key carries the `version.revision_label` value — spec §6/§9):

```python
    EVENT_DIGEST_DAILY: frozenset({"recipient.first_name", "item_count", "items", "prefs_link"}),
    EVENT_DOC_RELEASED: frozenset(
        {
            "recipient.first_name",
            "subject.identifier",
            "subject.title",
            "subject.kind",
            "version.label",
            "deep_link",
            "prefs_link",
        }
    ),
}
```

- [ ] **Step 2: Write the failing tests**

Create `apps/api/tests/integration/test_awareness_enqueue.py` (DB-bound → integration). Cover: a `created` in-app row with `task_id IS NULL` + `subject_version_id` set + `digest_due_at` set (AWARENESS default DAILY → no immediate email); a second call with the SAME `(recipient, doc.released, subject, subject_id, subject_version_id)` → `deduped`; a call with a DIFFERENT `subject_version_id` → `created` (the re-release fix); a missing template → `no_template`:

```python
import datetime
import uuid

import pytest

pytestmark = pytest.mark.integration


async def test_enqueue_awareness_created_then_deduped_then_reversion(app_under_test, awareness_fix):
    from easysynq_api.db.models.notification import Notification
    from easysynq_api.db.session import get_sessionmaker
    from easysynq_api.services.notifications.dispatch import enqueue_awareness_one
    from easysynq_api.services.notifications.subjects import SubjectInfo
    from sqlalchemy import select

    now = datetime.datetime.now(datetime.UTC)
    subj = SubjectInfo(identifier="SOP-1", title="A", kind="DOCUMENT", deep_link="http://x/documents/1")
    v1, v2 = uuid.uuid4(), uuid.uuid4()
    async with get_sessionmaker()() as session:
        o1 = await enqueue_awareness_one(
            session, org_id=awareness_fix.org_id, subject=subj, subject_id=awareness_fix.doc_id,
            subject_version_id=v1, recipient=awareness_fix.recipient, event_key="doc.released",
            context_vars={"version.label": "1.0"}, now=now, org_enabled=False, org_pierce=False,
        )
        await session.commit()
    assert o1 == "created"

    async with get_sessionmaker()() as session:
        o2 = await enqueue_awareness_one(
            session, org_id=awareness_fix.org_id, subject=subj, subject_id=awareness_fix.doc_id,
            subject_version_id=v1, recipient=awareness_fix.recipient, event_key="doc.released",
            context_vars={"version.label": "1.0"}, now=now, org_enabled=False, org_pierce=False,
        )
        await session.commit()
    assert o2 == "deduped"  # same version → suppressed

    async with get_sessionmaker()() as session:
        o3 = await enqueue_awareness_one(
            session, org_id=awareness_fix.org_id, subject=subj, subject_id=awareness_fix.doc_id,
            subject_version_id=v2, recipient=awareness_fix.recipient, event_key="doc.released",
            context_vars={"version.label": "2.0"}, now=now, org_enabled=False, org_pierce=False,
        )
        await session.commit()
        rows = (
            await session.execute(
                select(Notification).where(
                    Notification.recipient_user_id == awareness_fix.recipient.user_id,
                    Notification.event_key == "doc.released",
                )
            )
        ).scalars().all()
    assert o3 == "created"  # NEW version re-notifies (the re-release fix)
    assert len(rows) == 2
    assert all(r.task_id is None for r in rows)
    assert {r.subject_version_id for r in rows} == {v1, v2}
```

(`awareness_fix` seeds one org + one active recipient (an `AppUser` + a `Recipient`) and ensures the `doc.released` template exists — migration 0066 seeds it, so a fresh testcontainer DB already has it. FK-ordered teardown.)

- [ ] **Step 3: Run to verify it fails**

Run: `cd apps/api && uv run pytest -m integration tests/integration/test_awareness_enqueue.py -v`
Expected: FAIL — `ImportError: cannot import name 'enqueue_awareness_one'`.

- [ ] **Step 4: Extract `resolve_delivery` and add `enqueue_awareness_one` in `dispatch.py`**

Add the imports `NotificationClass` (from `.classes`) and `dataclasses`. Add the helper + the dataclass above `_enqueue_one`:

```python
import dataclasses  # add to the import block
from .classes import NotificationClass, class_of  # widen the existing class_of import


@dataclasses.dataclass(frozen=True)
class _DeliveryPlan:
    """The per-recipient delivery resolution shared by _enqueue_one and enqueue_awareness_one."""
    klass: NotificationClass
    wants_email: bool
    is_immediate: bool
    digest_due_at: datetime.datetime | None
    email_next_attempt_at: datetime.datetime | None


async def resolve_delivery(
    session: AsyncSession,
    *,
    recipient: Recipient,
    event_key: str,
    org_enabled: bool,
    org_pierce: bool,
    now: datetime.datetime,
) -> _DeliveryPlan:
    """Resolve class/mode/email-eligibility for one recipient (pure over prefs + flags + clock)."""
    pref = await session.get(NotificationPreference, recipient.user_id)
    eff = effective_preferences(pref)
    klass = class_of(event_key)
    mode = eff.modes[klass]
    base_eligible = _email_eligible(
        org_enabled=org_enabled, email=recipient.email, user_opt_in=eff.email_enabled
    )
    wants_email = base_eligible and mode is not NotificationDigestMode.OFF
    is_daily = wants_email and mode is NotificationDigestMode.DAILY
    is_immediate = wants_email and mode is NotificationDigestMode.IMMEDIATE
    digest_due_at = next_digest_at(eff, now) if is_daily else None
    email_next_attempt_at: datetime.datetime | None = None
    if is_immediate and in_quiet_window(eff, now) and not should_pierce(klass, org_pierce):
        email_next_attempt_at = window_end(eff, now)
    return _DeliveryPlan(
        klass=klass,
        wants_email=wants_email,
        is_immediate=is_immediate,
        digest_due_at=digest_due_at,
        email_next_attempt_at=email_next_attempt_at,
    )
```

Refactor `_enqueue_one`'s eligibility block (lines 123-136) + its email branch to call `resolve_delivery` (behaviour-identical — the parity backstop is the `test_notification_*` suite):

```python
    plan = await resolve_delivery(
        session, recipient=recipient, event_key=event_key,
        org_enabled=org_enabled, org_pierce=org_pierce, now=now,
    )
    # ... in .values(...): digest_due_at=plan.digest_due_at,
    # ... replace the email branch:
    if plan.wants_email and plan.is_immediate:
        email_addr: str = recipient.email  # type: ignore[assignment]
        session.add(
            NotificationEmail(
                org_id=instance.org_id,
                notification_id=new_id,
                recipient_user_id=recipient.user_id,
                recipient_email=email_addr,
                subject=forms.email_subject,
                body=forms.email_body,
                next_attempt_at=plan.email_next_attempt_at,
            )
        )
    return "created"
```

Add `enqueue_awareness_one` (after `_enqueue_one`):

```python
async def enqueue_awareness_one(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    subject: SubjectInfo,
    subject_id: uuid.UUID,
    subject_version_id: uuid.UUID | None,
    recipient: Recipient,
    event_key: str,
    context_vars: dict[str, object],
    now: datetime.datetime,
    org_enabled: bool,
    org_pierce: bool,
) -> EnqueueOutcome:
    """Enqueue one awareness notification (no task). task_id=NULL; dedup is version-discriminated on
    (recipient, event_key, subject_type, subject_id, subject_version_id) WHERE task_id IS NULL, so a
    NEW Effective version re-notifies (spec §5/§7). The in-app row is always created; an email row is
    added only on IMMEDIATE mode (awareness defaults to DAILY → the digest sweep delivers it)."""
    variables: dict[str, object] = {
        "recipient.first_name": recipient.first_name,
        "subject.identifier": subject.identifier,
        "subject.title": subject.title,
        "subject.kind": subject.kind,
        "deep_link": subject.deep_link,
        "prefs_link": prefs_link(),
        **context_vars,
    }
    forms = await render(session, event_key, variables)
    if forms is None:
        logger.warning("notification.template_missing", extra={"event_key": event_key})
        return "no_template"

    plan = await resolve_delivery(
        session, recipient=recipient, event_key=event_key,
        org_enabled=org_enabled, org_pierce=org_pierce, now=now,
    )
    stmt = (
        pg_insert(Notification)
        .values(
            org_id=org_id,
            recipient_user_id=recipient.user_id,
            event_key=event_key,
            subject_type=subject.kind,
            subject_id=subject_id,
            subject_version_id=subject_version_id,
            task_id=None,
            title=forms.in_app_title,
            body=forms.in_app_body,
            deep_link=subject.deep_link,
            template_id=forms.template_id,
            template_version=forms.template_version,
            context=variables_as_json(variables),
            digest_due_at=plan.digest_due_at,
        )
        .on_conflict_do_nothing(
            index_elements=[
                "recipient_user_id",
                "event_key",
                "subject_type",
                "subject_id",
                "subject_version_id",
            ],
            index_where=sa.text("task_id IS NULL"),
        )
        .returning(Notification.id)
    )
    new_id = (await session.execute(stmt)).scalar_one_or_none()
    if new_id is None:
        return "deduped"

    if plan.wants_email and plan.is_immediate:
        email_addr: str = recipient.email  # type: ignore[assignment]
        session.add(
            NotificationEmail(
                org_id=org_id,
                notification_id=new_id,
                recipient_user_id=recipient.user_id,
                recipient_email=email_addr,
                subject=forms.email_subject,
                body=forms.email_body,
                next_attempt_at=plan.email_next_attempt_at,
            )
        )
    return "created"
```

- [ ] **Step 5: Run the enqueue tests + the FULL api gate (parity)**

Run: `cd apps/api && uv run pytest -m integration tests/integration/test_awareness_enqueue.py -v`
Expected: PASS (created → deduped on same version → created on new version; 2 rows, both `task_id IS NULL`).

Run: `/check-api`
Expected: green. **The `test_notification_*` suites must stay green** — they prove `resolve_delivery` did not perturb `_enqueue_one`. If any moves, revert to duplicating the block in `enqueue_awareness_one` (the spec's documented fallback) and re-run.

- [ ] **Step 6: Commit**

```bash
git add apps/api/src/easysynq_api/services/notifications/constants.py apps/api/src/easysynq_api/services/notifications/dispatch.py apps/api/tests/integration/test_awareness_enqueue.py
git commit -m "feat(s-notify-5a): enqueue_awareness_one + extracted resolve_delivery; doc.released whitelist"
```

---

## Task 5: `record_awareness_event` — the SERIALIZABLE-aware emit helper

**Files:**
- Create: `apps/api/src/easysynq_api/services/notifications/awareness.py`
- Test: `apps/api/tests/unit/test_awareness_record.py`

**Interfaces:**
- Produces: `record_awareness_event(session, *, org_id, event_key, subject_type, subject_id, subject_version_id, actor_user_id, occurred_at, context) -> None` (best-effort SAVEPOINT insert; re-raises serialization errors, swallows the rest); `_is_serialization_error(exc) -> bool` (the pure SQLSTATE classifier, `{40001, 40P01, 23505}`).
- Consumes (Task 6): `_cutover` calls `record_awareness_event`.

- [ ] **Step 1: Write the failing unit test for the classifier + swallow semantics**

Create `apps/api/tests/unit/test_awareness_record.py` (the classifier is pure → a true unit test; no DB):

```python
import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError

from easysynq_api.services.notifications.awareness import _is_serialization_error


class _Orig:
    def __init__(self, sqlstate: str) -> None:
        self.sqlstate = sqlstate
        self.pgcode = sqlstate


@pytest.mark.unit
@pytest.mark.parametrize("code", ["40001", "40P01", "23505"])
def test_is_serialization_error_true_for_retryable_states(code: str) -> None:
    exc = DBAPIError("stmt", {}, _Orig(code))
    assert _is_serialization_error(exc) is True


@pytest.mark.unit
def test_is_serialization_error_false_for_other_states() -> None:
    exc = DBAPIError("stmt", {}, _Orig("42703"))  # undefined_column
    assert _is_serialization_error(exc) is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd apps/api && uv run pytest tests/unit/test_awareness_record.py -v`
Expected: FAIL — `ModuleNotFoundError: easysynq_api.services.notifications.awareness`.

- [ ] **Step 3: Implement `record_awareness_event`**

Create `apps/api/src/easysynq_api/services/notifications/awareness.py`:

```python
"""Awareness-event emit (slice S-notify-5a, doc 10 §9.2).

Writes ONE awareness_event outbox row inside the caller's release txn, best-effort via a SAVEPOINT —
so a non-serialization failure rolls back only the awareness row and the release still commits (R53:
awareness must never block a transition). CRITICAL: the caller (_cutover) runs SERIALIZABLE, NOT
Read-Committed. A 40001/40P01/23505 raised by the INSERT poisons the whole txn and must NOT be
swallowed — it is re-raised so _cutover's race-loss path produces the clean 409 (spec §6). The
expensive fan-out is off the hot path (services/notifications/fanout.py).
"""

from __future__ import annotations

import datetime
import logging
import uuid

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models.awareness_event import AwarenessEvent

logger = logging.getLogger("easysynq.notifications.awareness")

# SQLSTATEs that poison a SERIALIZABLE txn (serialization_failure / deadlock_detected /
# unique_violation) — re-raise so the cutover's race-loss handler adjudicates a clean 409.
_SERIALIZATION_SQLSTATES = {"40001", "40P01", "23505"}


def _is_serialization_error(exc: DBAPIError) -> bool:
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return sqlstate in _SERIALIZATION_SQLSTATES


async def record_awareness_event(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    event_key: str,
    subject_type: str,
    subject_id: uuid.UUID,
    subject_version_id: uuid.UUID | None,
    actor_user_id: uuid.UUID | None,
    occurred_at: datetime.datetime,
    context: dict[str, object],
) -> None:
    """Best-effort, SERIALIZABLE-aware single-row emit. Never raises except on a serialization error
    (which must propagate to the caller's race-loss path)."""
    try:
        async with session.begin_nested():
            session.add(
                AwarenessEvent(
                    org_id=org_id,
                    event_key=event_key,
                    subject_type=subject_type,
                    subject_id=subject_id,
                    subject_version_id=subject_version_id,
                    actor_user_id=actor_user_id,
                    context=context,
                    occurred_at=occurred_at,
                )
            )
    except DBAPIError as exc:
        if _is_serialization_error(exc):
            raise  # SERIALIZABLE conflict — let _cutover's _is_race_loss produce the clean 409
        logger.warning("awareness.record_failed", exc_info=True, extra={"event_key": event_key})
    except Exception:  # noqa: BLE001 — best-effort: awareness must never block a release
        logger.warning("awareness.record_failed", exc_info=True, extra={"event_key": event_key})
```

- [ ] **Step 4: Run the unit tests**

Run: `cd apps/api && uv run pytest tests/unit/test_awareness_record.py -v`
Expected: PASS.

- [ ] **Step 5: Run the api gate**

Run: `/check-api`
Expected: green.

- [ ] **Step 6: Commit**

```bash
git add apps/api/src/easysynq_api/services/notifications/awareness.py apps/api/tests/unit/test_awareness_record.py
git commit -m "feat(s-notify-5a): record_awareness_event — SERIALIZABLE-aware best-effort outbox emit"
```

---

## Task 6: Wire the emit hook into `_cutover` (+ the concurrent-release atomicity test)

**Files:**
- Modify: `apps/api/src/easysynq_api/services/vault/lifecycle.py` (`_cutover`)
- Test: `apps/api/tests/integration/test_awareness_emit.py`

**Interfaces:**
- Consumes: `record_awareness_event` (Task 5); `EVENT_DOC_RELEASED` (Task 4).
- Produces: exactly one `awareness_event` row per successful release (zero for a race loser), with `subject_version_id = version.id` and `context = {"version.label": version.revision_label}`.

**Insertion point (verbatim, `lifecycle.py`):** after the `RELEASED`/`SUPERSEDED` `_audit(...)` block (ends ~line 571), **before** `await session.commit()` (line 573). In scope: `doc` (Effective), `version` (Effective, `version.revision_label` non-null str), `actor` (`AppUser | None`), `now`, `session`.

- [ ] **Step 1: Write the failing integration tests**

Create `apps/api/tests/integration/test_awareness_emit.py`. Cover: a normal release writes exactly one `awareness_event` with the right fields; a concurrent release (two SERIALIZABLE `_cutover` on the same doc) leaves exactly ONE `awareness_event` and the loser gets a clean 409 (proving the savepoint rolls back with the race loser). Reuse the existing release-race harness in `test_vault.py` / `test_lifecycle.py`:

```python
import datetime

import pytest

pytestmark = pytest.mark.integration


async def test_release_emits_one_awareness_event(app_under_test, approved_document):
    """Releasing an Approved doc writes exactly one doc.released awareness_event (subject_version_id
    + version.label captured)."""
    from easysynq_api.db.models.awareness_event import AwarenessEvent
    from easysynq_api.db.session import get_sessionmaker
    from sqlalchemy import select

    # ... release approved_document via the existing release() service path (the test_vault helper) ...

    async with get_sessionmaker()() as session:
        rows = (
            await session.execute(
                select(AwarenessEvent).where(
                    AwarenessEvent.subject_id == approved_document.doc_id,
                    AwarenessEvent.event_key == "doc.released",
                )
            )
        ).scalars().all()
    assert len(rows) == 1
    ev = rows[0]
    assert ev.subject_type == "DOCUMENT"
    assert ev.subject_version_id == approved_document.version_id
    assert ev.context.get("version.label") == approved_document.revision_label
    assert ev.fanned_out_at is None


async def test_concurrent_release_emits_exactly_one_event_loser_409(app_under_test, approved_document):
    """A race loser's whole txn (incl. the savepoint awareness row) rolls back → exactly one event,
    a clean 409 for the loser (no 500, no phantom row)."""
    # Drive two concurrent release() calls on the same doc via asyncio.gather (the existing
    # test_lifecycle concurrent-release harness). Assert: one succeeds, one raises a 409
    # ProblemException, and exactly one awareness_event row exists.
    ...
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd apps/api && uv run pytest -m integration tests/integration/test_awareness_emit.py -v`
Expected: FAIL — no `awareness_event` row is written (the hook does not exist yet).

- [ ] **Step 3: Insert the emit hook in `_cutover`**

In `lifecycle.py::_cutover`, immediately **after** the `SUPERSEDED` `_audit(...)` block (line 571) and **before** `await session.commit()` (line 573), add a local-import + the call (local import mirrors the file's cycle-prone-dep idiom at lines 350/450):

```python
    # S-notify-5a: emit the doc.released awareness event (doc 10 §9.2). Best-effort + SERIALIZABLE-
    # aware (record_awareness_event re-raises a 40001/40P01/23505 so the race-loss path below yields
    # the clean 409); atomic with the promotion + RELEASED audit → rolls back with a race loser.
    from ..notifications.awareness import record_awareness_event
    from ..notifications.constants import EVENT_DOC_RELEASED

    await record_awareness_event(
        session,
        org_id=doc.org_id,
        event_key=EVENT_DOC_RELEASED,
        subject_type="DOCUMENT",
        subject_id=doc.id,
        subject_version_id=version.id,
        actor_user_id=(actor.id if actor is not None else None),
        occurred_at=now,
        context={"version.label": version.revision_label},
    )

    await session.commit()  # INV-1 + SERIALIZABLE adjudicate the race here
```

(Replace the existing bare `await session.commit()` line with the block above ending in that same commit — do not duplicate the commit.)

- [ ] **Step 4: Run the tests**

Run: `cd apps/api && uv run pytest -m integration tests/integration/test_awareness_emit.py -v`
Expected: PASS (one event on a normal release; exactly one event + a 409 loser on a concurrent release).

- [ ] **Step 5: Run the FULL release/lifecycle regression**

Run: `cd apps/api && uv run pytest -m integration tests/integration/test_vault.py tests/integration/test_lifecycle.py -v`
Expected: green — the existing release/supersession/race tests still pass (the hook is additive and atomic). Then `/check-api` green.

- [ ] **Step 6: Commit**

```bash
git add apps/api/src/easysynq_api/services/vault/lifecycle.py apps/api/tests/integration/test_awareness_emit.py
git commit -m "feat(s-notify-5a): emit doc.released awareness_event from _cutover (atomic, SERIALIZABLE-aware)"
```

---

## Task 7: The `awareness_fan_out` Beat worker + the end-to-end read-scope test

**Files:**
- Create: `apps/api/src/easysynq_api/services/notifications/fanout.py`
- Modify: `apps/api/src/easysynq_api/tasks/notifications.py` (the `awareness_fan_out` task)
- Modify: `apps/api/src/easysynq_api/tasks/app.py` (the Beat schedule entry)
- Modify: `apps/api/tests/unit/test_notification_task_registration.py` (assert the new task)
- Test: `apps/api/tests/integration/test_awareness_e2e.py`

**Interfaces:**
- Consumes: `resolve_document_readers` (Task 3); `enqueue_awareness_one` (Task 4); `escalation._recipient_for_user`; `subjects.resolve_subject`; `render` (template-existence probe).
- Produces: `fan_out_awareness(sessionmaker, now) -> dict[str, int]`; `process_one_awareness_event(session, *, event_id, now) -> int`; the `easysynq.notifications.awareness_fan_out` Beat task (@120 s).

- [ ] **Step 1: Write the failing end-to-end test**

Create `apps/api/tests/integration/test_awareness_e2e.py`. Cover the full `_cutover → awareness_event → fan_out → notification rows` path:

```python
import datetime

import pytest

pytestmark = pytest.mark.integration


async def test_fanout_read_scope_filter_and_self_suppression(app_under_test, e2e_fixture):
    """A reader gets the in-app row; a non-reader and the actor do not."""
    from easysynq_api.db.models.notification import Notification
    from easysynq_api.db.session import get_sessionmaker
    from easysynq_api.services.notifications.fanout import fan_out_awareness
    from sqlalchemy import select

    # e2e_fixture: an org, a released doc (one awareness_event with actor=actor_id), a `reader` with
    # document.read @ SYSTEM, a `non_reader` with no grant, and the `actor` (also a reader).
    now = datetime.datetime.now(datetime.UTC)
    await fan_out_awareness(get_sessionmaker(), now)

    async with get_sessionmaker()() as session:
        recips = (
            await session.execute(
                select(Notification.recipient_user_id).where(
                    Notification.event_key == "doc.released",
                    Notification.subject_id == e2e_fixture.doc_id,
                )
            )
        ).scalars().all()
    assert e2e_fixture.reader_id in recips
    assert e2e_fixture.non_reader_id not in recips     # read-scope filter
    assert e2e_fixture.actor_id not in recips          # self-suppression


async def test_fanout_idempotent_and_rerelease_renotifies(app_under_test, e2e_fixture):
    """A second sweep creates 0 new rows; a re-release (new version) re-notifies prior readers."""
    from easysynq_api.db.models.awareness_event import AwarenessEvent
    from easysynq_api.db.models.notification import Notification
    from easysynq_api.db.session import get_sessionmaker
    from easysynq_api.services.notifications.fanout import fan_out_awareness
    from sqlalchemy import func, select

    now = datetime.datetime.now(datetime.UTC)
    await fan_out_awareness(get_sessionmaker(), now)
    async with get_sessionmaker()() as session:
        count1 = (
            await session.execute(
                select(func.count())
                .select_from(Notification)
                .where(Notification.subject_id == e2e_fixture.doc_id)
            )
        ).scalar_one()
        # the event is stamped:
        assert (
            await session.execute(
                select(AwarenessEvent.fanned_out_at).where(
                    AwarenessEvent.subject_id == e2e_fixture.doc_id
                )
            )
        ).scalars().first() is not None

    # second sweep — no new event pending → no new rows
    await fan_out_awareness(get_sessionmaker(), now)
    async with get_sessionmaker()() as session:
        count2 = (
            await session.execute(
                select(func.count())
                .select_from(Notification)
                .where(Notification.subject_id == e2e_fixture.doc_id)
            )
        ).scalar_one()
    assert count2 == count1

    # re-release the same doc (a NEW Effective version) → a new awareness_event → fan out →
    # the reader gets a SECOND notification (the version-discriminated dedup lets it through).
    await e2e_fixture.rerelease()  # the fixture revises + releases v2 (one new awareness_event)
    await fan_out_awareness(get_sessionmaker(), now)
    async with get_sessionmaker()() as session:
        reader_rows = (
            await session.execute(
                select(func.count())
                .select_from(Notification)
                .where(
                    Notification.subject_id == e2e_fixture.doc_id,
                    Notification.recipient_user_id == e2e_fixture.reader_id,
                )
            )
        ).scalar_one()
    assert reader_rows == 2  # v1 + v2


async def test_fanout_org_email_off_creates_in_app_no_email(app_under_test, e2e_fixture):
    """org email OFF → the in-app row is still created, no NotificationEmail, digest_due_at set."""
    from easysynq_api.db.models.notification import Notification, NotificationEmail
    from easysynq_api.db.session import get_sessionmaker
    from easysynq_api.services.notifications.fanout import fan_out_awareness
    from sqlalchemy import select

    now = datetime.datetime.now(datetime.UTC)
    await fan_out_awareness(get_sessionmaker(), now)
    async with get_sessionmaker()() as session:
        row = (
            await session.execute(
                select(Notification).where(
                    Notification.subject_id == e2e_fixture.doc_id,
                    Notification.recipient_user_id == e2e_fixture.reader_id,
                )
            )
        ).scalar_one()
        emails = (
            await session.execute(
                select(NotificationEmail).where(NotificationEmail.notification_id == row.id)
            )
        ).scalars().all()
    assert row.digest_due_at is not None   # AWARENESS default DAILY
    assert emails == []                    # org email off → no email row
```

(`e2e_fixture` seeds the org + the released doc (driving the real `release()` so `_cutover` writes the `awareness_event`), the reader/non_reader/actor with grants, leaves `notifications_email_enabled=False` for the org-off test, and exposes `rerelease()`. **FK-ordered teardown.**)

- [ ] **Step 2: Run to verify it fails**

Run: `cd apps/api && uv run pytest -m integration tests/integration/test_awareness_e2e.py -v`
Expected: FAIL — `ModuleNotFoundError: easysynq_api.services.notifications.fanout`.

- [ ] **Step 3: Implement the fan-out worker**

Create `apps/api/src/easysynq_api/services/notifications/fanout.py`:

```python
"""Awareness fan-out Beat (slice S-notify-5a, doc 10 §9.2).

Claims pending awareness_event rows, resolves the read-scoped audience (the per-user PDP loop), and
creates per-recipient notification rows reusing the slice-3a machinery — idempotently. Mirrors the
escalation/digest claim+stamp shape but with PK-pinned FOR UPDATE SKIP LOCKED and NO per-event
advisory lock (the outbox_drain precedent). One commit per event → atomic claim+fanout+stamp; a worker
death rolls the whole txn back (fanned_out_at stays NULL → re-claimed). No reaper needed (fully
machine-driven, terminal-on-stamp).
"""

from __future__ import annotations

import datetime
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...db.models.awareness_event import AwarenessEvent
from ...db.models.system_config import SystemConfig
from ..authz.audience import resolve_document_readers
from .dispatch import enqueue_awareness_one
from .escalation import _recipient_for_user
from .render import render
from .subjects import resolve_subject

logger = logging.getLogger("easysynq.notifications.fanout")

_CLAIM_LIMIT = 200  # bound a release-burst fan-out per sweep (spec §8)


async def _pending_event_ids(
    session: AsyncSession, now: datetime.datetime
) -> list[uuid.UUID]:
    return list(
        (
            await session.execute(
                select(AwarenessEvent.id)
                .where(AwarenessEvent.fanned_out_at.is_(None))
                .order_by(AwarenessEvent.occurred_at)
                .limit(_CLAIM_LIMIT)
            )
        )
        .scalars()
        .all()
    )


async def _org_flags(session: AsyncSession, org_id: uuid.UUID) -> tuple[bool, bool]:
    cfg = (
        await session.execute(select(SystemConfig).where(SystemConfig.org_id == org_id))
    ).scalar_one_or_none()
    if cfg is None:
        return (False, False)
    return (cfg.notifications_email_enabled, cfg.notifications_escalation_pierce_quiet_hours)


async def process_one_awareness_event(
    session: AsyncSession, *, event_id: uuid.UUID, now: datetime.datetime
) -> int:
    """Fan out ONE awareness event. Returns the count of newly-created in-app rows. Idempotent:
    claims FOR UPDATE SKIP LOCKED + populate_existing; stamps fanned_out_at + commits ONCE. A
    template miss does NOT stamp (retry after restore — the 3a/4 rule)."""
    event = (
        await session.execute(
            select(AwarenessEvent)
            .where(AwarenessEvent.id == event_id, AwarenessEvent.fanned_out_at.is_(None))
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if event is None:
        return 0  # already claimed/stamped by a concurrent sweep, or vanished

    subject = await resolve_subject(session, event.subject_type, event.subject_id)
    if subject is None:
        # The subject vanished (e.g. hard-deleted) — nothing to notify; stamp so we don't re-claim.
        event.fanned_out_at = now
        await session.commit()
        return 0

    audience = await resolve_document_readers(session, event.org_id, event.subject_id, now=now)
    recipients = [uid for uid in audience if uid != event.actor_user_id]

    # Template-existence probe ONCE (recipient-independent). Missing → do NOT stamp (retry).
    if recipients and (await render(session, event.event_key, {})) is None:
        await session.rollback()
        logger.warning(
            "notifications.awareness_template_missing", extra={"event_key": event.event_key}
        )
        return 0

    org_enabled, org_pierce = await _org_flags(session, event.org_id)
    context_vars = dict(event.context or {})
    created = 0
    for uid in recipients:
        recipient = await _recipient_for_user(session, uid, org_id=event.org_id)
        if recipient is None:
            continue
        outcome = await enqueue_awareness_one(
            session,
            org_id=event.org_id,
            subject=subject,
            subject_id=event.subject_id,
            subject_version_id=event.subject_version_id,
            recipient=recipient,
            event_key=event.event_key,
            context_vars=context_vars,
            now=now,
            org_enabled=org_enabled,
            org_pierce=org_pierce,
        )
        if outcome == "created":
            created += 1

    event.fanned_out_at = now
    await session.commit()
    return created


async def fan_out_awareness(
    sessionmaker: async_sessionmaker[AsyncSession], now: datetime.datetime
) -> dict[str, int]:
    """Fan out every pending awareness event. Fresh session per event (the MissingGreenlet guard);
    per-event exception isolation (one event's failure must not wedge the cohort)."""
    counts: dict[str, int] = {"events": 0, "notifications": 0}
    async with sessionmaker() as session:
        ids = await _pending_event_ids(session, now)
    for event_id in ids:
        try:
            async with sessionmaker() as session:
                n = await process_one_awareness_event(session, event_id=event_id, now=now)
        except Exception:  # noqa: BLE001 — one event's failure must not wedge the sweep
            logger.warning(
                "notifications.awareness_event_failed",
                exc_info=True,
                extra={"event_id": str(event_id)},
            )
            continue
        counts["events"] += 1
        counts["notifications"] += n
    return counts
```

(Note: `resolve_subject`'s exact call shape — confirm against `services/notifications/subjects.py` when implementing; if its signature is positional `resolve_subject(session, subject_type, subject_id)`, the above matches; adjust if keyword-only. `SystemConfig`'s pierce-flag field name — confirm against the 3a addition; if it differs, mirror how `escalation`/`dispatch` callers read the org flags.)

- [ ] **Step 4: Register the Beat task**

In `tasks/notifications.py`, add the import next to the existing notification imports and append the `_run_*` + `@task` pair after `timer_sweep`:

```python
from ..services.notifications.fanout import fan_out_awareness
```
```python
async def _run_awareness_fanout() -> dict[str, int]:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    sm: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)
    try:
        summary = await fan_out_awareness(sm, datetime.datetime.now(datetime.UTC))
        logger.info("notifications.awareness_fanout", extra={"extra_fields": summary})
        return summary
    finally:
        await engine.dispose()


@task(name="easysynq.notifications.awareness_fanout")
def awareness_fanout() -> dict[str, int]:
    """Fan out pending awareness events (doc.released) to read-scoped audiences (every ~120 s)."""
    return asyncio.run(_run_awareness_fanout())
```

In `tasks/app.py`, append the Beat entry after the `notifications-timer-sweep` block (before the `beat_schedule` closing `},`):

```python
        # S-notify-5a: the awareness fan-out — resolves doc.released's read-scoped audience and
        # creates per-recipient notification rows. Every 2 minutes (awareness is daily-digest by
        # default, so ≤2-min in-app latency is ample).
        "notifications-awareness-fanout": {
            "task": "easysynq.notifications.awareness_fanout",
            "schedule": 120.0,
        },
```

- [ ] **Step 5: Add the registration assertion**

In `apps/api/tests/unit/test_notification_task_registration.py`, add (mirroring the existing `timer_sweep` assertion):

```python
def test_awareness_fanout_registered(celery_app) -> None:
    assert "easysynq.notifications.awareness_fanout" in celery_app.tasks
    entry = celery_app.conf.beat_schedule["notifications-awareness-fanout"]
    assert entry["task"] == "easysynq.notifications.awareness_fanout"
    assert entry["schedule"] == 120.0
```

- [ ] **Step 6: Run the e2e + registration tests**

Run: `cd apps/api && uv run pytest tests/unit/test_notification_task_registration.py -v`
Expected: PASS (the new task + Beat entry registered).

Run: `cd apps/api && uv run pytest -m integration tests/integration/test_awareness_e2e.py -v`
Expected: PASS (read-scope filter + self-suppression; idempotent second sweep = 0 new; re-release re-notifies; org-off → in-app only + digest_due_at).

- [ ] **Step 7: Run the full suites + parity**

Run: `/check-api`
Expected: green (mypy-strict catches the `_recipient_for_user` import use; all unit tests pass).

Run: `cd apps/api && uv run pytest -m integration tests/integration/ -k "notification or awareness or vault or lifecycle"`
Expected: green — awareness end-to-end + the existing notification/vault suites (run-scoped, delta-based assertions hold; no cross-file pollution).

- [ ] **Step 8: Commit**

```bash
git add apps/api/src/easysynq_api/services/notifications/fanout.py apps/api/src/easysynq_api/tasks/notifications.py apps/api/src/easysynq_api/tasks/app.py apps/api/tests/unit/test_notification_task_registration.py apps/api/tests/integration/test_awareness_e2e.py
git commit -m "feat(s-notify-5a): awareness_fan_out Beat — read-scoped doc.released fan-out, idempotent"
```

---

## Final verification (after all tasks)

- [ ] `/check-migrations` — 0066 round-trips clean (up↔down↔check) + the populated-DB downgrade (a `doc.released` notification present → the NOT-EXISTS guard leaves the template).
- [ ] `/check-api` — ruff + mypy-strict + the full `pytest -m unit` green; the `test_notification_*` parity suites green.
- [ ] `cd apps/api && uv run pytest -m integration tests/integration/` — the full integration suite green (run-scoped/delta-based; FK-ordered cleanup; no `test_restore` `scalar_one()` pollution).
- [ ] `/check-contracts` — no-op (no openapi change), but run to confirm the contract is untouched.
- [ ] Run the `migration-reviewer` agent on the 0066 diff and the `diff-critic` agent on the whole branch diff (pre-PR, per the slice rhythm).

## Plan self-review notes (coverage map)

- Spec §3 (outbox + fan-out arch) → Tasks 1 (table), 5 (emit), 7 (worker).
- Spec §4 (audience resolver, requester-invariant resource, source_ip=None, uid→Recipient, fan-out-time residual) → Tasks 2 (builder), 3 (resolver), 7 (`_recipient_for_user` hop).
- Spec §5 (data model, version-discriminated dedup, REVOKE DELETE, env.py) → Task 1.
- Spec §6 (emit hook, SERIALIZABLE re-raise, version.revision_label, fire-for-all) → Tasks 5 + 6.
- Spec §7 (enqueue_awareness_one, shared-helper extraction, dedup target) → Task 4.
- Spec §8 (fan-out worker, no advisory lock, no reaper, bounded LIMIT) → Task 7.
- Spec §9 (doc.released template, whitelist, deep link) → Tasks 1 (seed) + 4 (whitelist).
- Spec §11 (tests: deny-wins, re-release, idempotency, concurrent, no-template, org-off, SERIALIZABLE) → Tasks 3/4/5/6/7.
- Spec §12 (migration-trap checklist) → Task 1 (steps 5-6).
