# S-drift-2 — Mirror tamper/staleness scan (D2+D3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the D2+D3 mirror integrity scan: re-hash the live mirror tree against a PG-persisted
build manifest, classify divergence (`STALE_REVISION` vs `UNEXPECTED_CONTENT`/extra/missing/symlink),
quarantine divergent bytes BEFORE any rebuild (R11), audit `MIRROR_STALE`/`MIRROR_TAMPER`, persist a
`drift_scan` summary, and compose scan-first into every `sync_mirror` plus a new hourly Beat scan.

**Architecture:** Migration `0046` adds `mirror_build` (the vault-side expected-state baseline — the
on-disk manifest is NEVER trusted) + `drift_scan` (the §9.2 scan summary) + 2 additive `event_type`
values. A new `services/vault/mirror_scan.py` holds a pure compare/quarantine core (unit-testable, no
DB) and a DB-coupled orchestration layer (`scan_mirror` → `persist_scan_results` → `scan_and_sync`).
`mirror.py` gains only the manifest doc-id enrichment + the `mirror_build` insert. Tasks/CLI compose:
sync = scan→always-rebuild; hourly scan = scan→rebuild-if-needed; both under `LOCK_MIRROR_SYNC`.

**Tech stack:** FastAPI/SQLAlchemy 2 async (Python 3.12, `uv`), Alembic, Celery+Beat, pytest
(+testcontainers for `-m integration`).

**Spec:** `docs/superpowers/specs/2026-06-09-s-drift-2-mirror-tamper-scan-design.md` (approved).
Two plan-level amendments to fold back into the spec in Task 7: (a) the `counts` key `rebuilt` is
named **`rebuild_triggered`** (the decision is recorded before the rebuild runs); (b) `scan_mirror`
is read-only — the audit events + `drift_scan` row are written by **`persist_scan_results`** (still
one txn, still after quarantine, still before any rebuild — the §6 posture is unchanged).

**This-box caveats (native Windows 11):** the full `-m unit` / `-m integration` suites are
Linux-CI-only here. Run the *targeted* unit files listed per task; symlink-creating tests may need
Windows Developer Mode — if `os.symlink` raises a privilege error, rely on the static checks +
Linux CI (the documented posture in `.claude/rules/windows-dev.md`). Static gate per task (from
`apps/api/`): `uv run ruff check . ; uv run ruff format --check . ; uv run mypy`. Migration gate:
the `/check-migrations` skill (throwaway PG16 via Docker).

**Branch:** `feat/s-drift-2-mirror-tamper-scan` (already created; spec committed).

---

## File structure

| File | Action | Responsibility |
|---|---|---|
| `apps/api/src/easysynq_api/db/models/_drift_enums.py` | Create | `DriftScanKind`/`DriftScanStatus` + SAEnum bindings + `*_VALUES` |
| `apps/api/src/easysynq_api/db/models/mirror_build.py` | Create | `MirrorBuild` ORM (the baseline registry) |
| `apps/api/src/easysynq_api/db/models/drift_scan.py` | Create | `DriftScan` ORM (the scan summary) |
| `apps/api/src/easysynq_api/db/models/_audit_enums.py` | Modify | `MIRROR_STALE`/`MIRROR_TAMPER` members |
| `apps/api/src/easysynq_api/db/models/__init__.py` | Modify | register the 2 models + 2 enums (the 0027 lesson) |
| `migrations/versions/0046_mirror_drift_scan.py` | Create | tables + enums + event values + grants |
| `apps/api/src/easysynq_api/services/common/org.py` | Create | `get_single_org_id` (resilient runtime org lookup) |
| `apps/api/src/easysynq_api/services/vault/mirror.py` | Modify | `_write(extra=…)` doc-id enrichment; `mirror_build` insert + keep-last-20 prune in `sync_mirror`; docstring fix |
| `apps/api/src/easysynq_api/services/vault/mirror_scan.py` | Create | the scanner: pure core + pointer integrity + DB orchestration |
| `apps/api/src/easysynq_api/services/common/pg_locks.py` | Modify | `holds_advisory_lock` (the lock-loss re-check, spec §11.5) |
| `apps/api/src/easysynq_api/config.py` | Modify | `mirror_scan_interval_seconds: int = 3600` |
| `apps/api/src/easysynq_api/tasks/mirror.py` | Modify | sync task → `scan_and_sync(always)`; NEW `easysynq.mirror.scan` task |
| `apps/api/src/easysynq_api/tasks/app.py` | Modify | hourly `mirror-scan` Beat entry (settings-driven) |
| `apps/api/src/easysynq_api/cli/mirror.py` | Modify | `scan` subcommand (scan-only, no rebuild) |
| `apps/api/tests/unit/test_mirror.py` | Modify | manifest doc-id enrichment proof |
| `apps/api/tests/unit/test_mirror_scan.py` | Create | the classification matrix + quarantine + counts |
| `apps/api/tests/unit/test_mirror_scan_task_registration.py` | Create | task/Beat/settings registration |
| `apps/api/tests/integration/test_mirror_scan.py` | Create | e2e tamper→quarantine→audit→correct proofs |
| `docs/05-revision-and-drift.md`, `docs/14-data-model.md`, `docs/12-security-and-audit.md`, `docs/runbooks/mirror-drift-scan.md` (+ `00-index.md`), spec, `CLAUDE.md`, `docs/slice-history.md` | Modify/Create | Task 7 docs |

`tasks/__init__.py` needs NO change (the `mirror` module is already imported). No
`openapi.yaml`/permission changes (verify none in diff).

---

### Task 1: Migration 0046 + ORM models

**Files:**
- Create: `apps/api/src/easysynq_api/db/models/_drift_enums.py`
- Create: `apps/api/src/easysynq_api/db/models/mirror_build.py`
- Create: `apps/api/src/easysynq_api/db/models/drift_scan.py`
- Modify: `apps/api/src/easysynq_api/db/models/_audit_enums.py` (after `REVIEW_OVERDUE`, ~line 341)
- Modify: `apps/api/src/easysynq_api/db/models/__init__.py`
- Create: `migrations/versions/0046_mirror_drift_scan.py`

- [ ] **Step 1: Write `_drift_enums.py`**

```python
"""Native-PG enum bindings for the drift-detection family (S-drift-2, doc 05 §9.1-§9.2, R11).

``drift_scan_kind`` starts with MIRROR (D2+D3); S-drift-3's D1 blob re-hash adds BLOB_REHASH via
``ALTER TYPE … ADD VALUE`` (the event_type additive precedent). Created by the 0046 migration;
referenced here with ``create_type=False``.
"""

from __future__ import annotations

import enum

from sqlalchemy import Enum as SAEnum


class DriftScanKind(enum.Enum):
    MIRROR = "MIRROR"


class DriftScanStatus(enum.Enum):
    CLEAN = "CLEAN"
    DIVERGENT = "DIVERGENT"
    FAILED = "FAILED"


def _vals(e: type[enum.Enum]) -> list[str]:
    return [m.value for m in e]


drift_scan_kind_enum = SAEnum(
    DriftScanKind, name="drift_scan_kind", values_callable=_vals, create_type=False
)
drift_scan_status_enum = SAEnum(
    DriftScanStatus, name="drift_scan_status", values_callable=_vals, create_type=False
)

# Re-used by the 0046 CREATE TYPE so the ORM and the hand-authored DDL never drift.
DRIFT_SCAN_KIND_VALUES = tuple(_vals(DriftScanKind))
DRIFT_SCAN_STATUS_VALUES = tuple(_vals(DriftScanStatus))
```

- [ ] **Step 2: Write `mirror_build.py`**

```python
"""The PG-persisted mirror-build baseline (S-drift-2, doc 05 §9.2, R11).

One row per ``sync_mirror`` build, keyed by the ``.builds/<hex>`` dir name. ``manifest`` is the
build's file/symlink entry list (file entries carry additive ``document_id``/``version_id`` keys) —
the scan's expected-state AUTHORITY: the on-disk ``_meta/manifest.json`` is never trusted (its
deliberately non-deterministic ``generated_at`` also rules out recompute), only byte-verified
against ``manifest_sha256``. Inserted in the build txn (commit-then-swap: an orphan row for a
never-swapped build is harmless — the scan looks up by ``current``'s ACTUAL target); keep-last-20
pruned in the same txn. A regenerable registry, NOT an audit record (the visual_diff posture).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class MirrorBuild(Base):
    __tablename__ = "mirror_build"
    __table_args__ = (UniqueConstraint("build_name", name="uq_mirror_build_build_name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    build_name: Mapped[str] = mapped_column(Text, nullable=False)
    built_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Stamped in a small post-swap commit — the pointer-integrity anchor (spec §11.1): the scan
    # verifies `current` against the newest SWAPPED row, so a repointed/rolled-back/planted tree
    # is MIRROR_TAMPER, never mistaken for the benign no-baseline state. NULL = built-not-swapped
    # (a commit-then-swap crash orphan, or the swap-then-crash window the scan self-heals).
    swapped_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    manifest: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    # sha256 of the EXACT bytes written to _meta/manifest.json (generated_at is non-deterministic).
    manifest_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    documents: Mapped[int] = mapped_column(Integer, nullable=False)
    files: Mapped[int] = mapped_column(Integer, nullable=False)
    symlinks: Mapped[int] = mapped_column(Integer, nullable=False)
```

- [ ] **Step 3: Write `drift_scan.py`**

```python
"""The per-scan drift summary (S-drift-2, doc 05 §9.2 "write scan summary"; R11).

Family-generic (owner fork §0.3): kind=MIRROR now; S-drift-3's D1 blob re-hash reuses it with an
additive BLOB_REHASH kind, and the S-drift-3 admin drift-status surface reads latest-per-kind via
``ix_drift_scan_kind_started_at``. Written ONCE at scan terminal (write-once by code — the
tamper-evident record is the audit trail; this is the queryable operational summary). ``counts``:
{scanned, ok, stale, tampered, extra, missing, symlink_divergent, quarantined, errors, build_name,
is_current, baseline, scan_id, rebuild_triggered}.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._drift_enums import (
    DriftScanKind,
    DriftScanStatus,
    drift_scan_kind_enum,
    drift_scan_status_enum,
)


class DriftScan(Base):
    __tablename__ = "drift_scan"
    __table_args__ = (Index("ix_drift_scan_kind_started_at", "kind", "started_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[DriftScanKind] = mapped_column(drift_scan_kind_enum, nullable=False)
    started_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[DriftScanStatus] = mapped_column(drift_scan_status_enum, nullable=False)
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    triggered_by: Mapped[str] = mapped_column(Text, nullable=False)  # 'beat' | 'sync' | 'cli'
```

- [ ] **Step 4: Add the EventType members** — in `_audit_enums.py`, immediately after
  `REVIEW_OVERDUE = "REVIEW_OVERDUE"` (keep the comment style):

```python
    # S-drift-2 (doc 05 §9.1 D2/D3 + §9.2, R11): the mirror integrity scan. MIRROR_STALE = a
    # mirrored file's divergent digest matches known vault bytes of the SAME document (an older
    # version's source/rendition — STALE_REVISION); MIRROR_TAMPER = foreign bytes / extra / missing
    # / symlink divergence (UNEXPECTED_CONTENT et al. — the alarm-worthy class). Doc-attributable
    # anomalies key object_type=document (scope_ref=identifier); generated/top-level paths key on
    # config. Added via ALTER TYPE event_type ADD VALUE in 0046 (the additive pattern; a
    # from-scratch ``upgrade head`` rebuilds from EVENT_TYPE_VALUES).
    MIRROR_STALE = "MIRROR_STALE"
    MIRROR_TAMPER = "MIRROR_TAMPER"
```

- [ ] **Step 5: Register in `db/models/__init__.py`** — add imports (alphabetical slots):

```python
from ._drift_enums import DriftScanKind, DriftScanStatus
from .drift_scan import DriftScan
from .mirror_build import MirrorBuild
```

and add `"DriftScan"`, `"DriftScanKind"`, `"DriftScanStatus"`, `"MirrorBuild"` to `__all__`
(alphabetical).

- [ ] **Step 6: Write `migrations/versions/0046_mirror_drift_scan.py`** (the 0042 template):

```python
"""mirror drift scan: the D2+D3 baseline + scan-summary tables (S-drift-2)

The thesis slice of the v1.x drift family (doc 05 §9.1 D2/D3, §9.2, R11). NO new permission key
(the scan is a system op; the admin read surface is S-drift-3); NO endpoint change.

1. **Two fresh enums** (CREATE TYPE → usable same-txn): ``drift_scan_kind`` (MIRROR; S-drift-3 adds
   BLOB_REHASH additively) + ``drift_scan_status`` (CLEAN/DIVERGENT/FAILED). Values from the ORM
   ``*_VALUES``.
2. **mirror_build** — the PG-persisted per-build manifest, the scan's expected-state authority
   (the on-disk manifest.json is never trusted; verified byte-wise via ``manifest_sha256``).
   Keyed UNIQUE(build_name) = the ``.builds/<hex>`` dir name. Mutable registry (SELECT/INSERT/
   DELETE — the keep-last-20 prune), NOT append-only.
3. **drift_scan** — one summary row per scan (doc 05 §9.2 "write scan summary"); write-once by code
   (SELECT/INSERT).
4. **event_type** += MIRROR_STALE, MIRROR_TAMPER (additive ADD VALUE; no-op downgrade — the 0011
   pattern).

Downgrade: drop both tables; DROP the two fresh enums; event values stay (PG cannot remove them).

Revision ID: 0046_mirror_drift_scan
Revises: 0045_periodic_review
Create Date: 2026-06-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from easysynq_api.db.models._drift_enums import (
    DRIFT_SCAN_KIND_VALUES,
    DRIFT_SCAN_STATUS_VALUES,
)

revision: str = "0046_mirror_drift_scan"
down_revision: str | None = "0045_periodic_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "easysynq_app"
_NEW_EVENT_TYPES = ("MIRROR_STALE", "MIRROR_TAMPER")


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Event types (IF NOT EXISTS → idempotent; not used by any row in this txn).
    for value in _NEW_EVENT_TYPES:
        op.execute(f"ALTER TYPE event_type ADD VALUE IF NOT EXISTS '{value}'")

    # 2. The fresh enums (CREATE TYPE → usable same-txn). Tuples from the ORM *_VALUES.
    postgresql.ENUM(*DRIFT_SCAN_KIND_VALUES, name="drift_scan_kind").create(bind, checkfirst=True)
    postgresql.ENUM(*DRIFT_SCAN_STATUS_VALUES, name="drift_scan_status").create(
        bind, checkfirst=True
    )
    kind_t = postgresql.ENUM(name="drift_scan_kind", create_type=False)
    status_t = postgresql.ENUM(name="drift_scan_status", create_type=False)

    # 3. mirror_build — the vault-side expected-state baseline.
    op.create_table(
        "mirror_build",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("build_name", sa.Text(), nullable=False),
        sa.Column(
            "built_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("swapped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("manifest_sha256", sa.Text(), nullable=False),
        sa.Column("documents", sa.Integer(), nullable=False),
        sa.Column("files", sa.Integer(), nullable=False),
        sa.Column("symlinks", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_mirror_build_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_mirror_build"),
        sa.UniqueConstraint("build_name", name="uq_mirror_build_build_name"),
    )

    # 4. drift_scan — the per-scan summary (doc 05 §9.2).
    op.create_table(
        "drift_scan",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", kind_t, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", status_t, nullable=False),
        sa.Column("counts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("triggered_by", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_drift_scan_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_drift_scan"),
    )
    op.create_index("ix_drift_scan_kind_started_at", "drift_scan", ["kind", "started_at"])

    # 5. Least-privilege grants (pg_roles-guarded — the 0042 pattern). mirror_build needs DELETE
    # for the keep-last-20 prune + UPDATE for the post-swap/self-heal ``swapped_at`` stamp;
    # drift_scan is write-once (no UPDATE).
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON mirror_build TO {_APP_ROLE}';
                EXECUTE 'GRANT SELECT, INSERT ON drift_scan TO {_APP_ROLE}';
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.drop_index("ix_drift_scan_kind_started_at", table_name="drift_scan")
    op.drop_table("drift_scan")
    op.drop_table("mirror_build")
    op.execute("DROP TYPE IF EXISTS drift_scan_status")
    op.execute("DROP TYPE IF EXISTS drift_scan_kind")
    # Event values: deliberate no-op (PG cannot remove an enum value; the 0011 precedent).
```

- [ ] **Step 7: Run the migration gate**

Run: the `/check-migrations` skill (round-trips `upgrade head` ↔ `downgrade -1` ↔ `alembic check`
on a throwaway PG16).
Expected: clean — in particular NO phantom-DROP (the models ARE imported in `__init__.py`) and
`alembic check` reports no diff.

- [ ] **Step 8: Run the api static checks**

Run (from `apps/api/`): `uv run ruff check . ; uv run ruff format --check . ; uv run mypy`
Expected: all clean.

- [ ] **Step 9: Commit**

```bash
git add apps/api/src/easysynq_api/db/models migrations/versions/0046_mirror_drift_scan.py
git commit -m "feat(s-drift-2): mig 0046 — mirror_build + drift_scan tables, MIRROR_STALE/MIRROR_TAMPER event types"
```

---

### Task 2: Manifest enrichment + the `mirror_build` baseline write

**Files:**
- Create: `apps/api/src/easysynq_api/services/common/org.py`
- Modify: `apps/api/src/easysynq_api/services/vault/mirror.py`
- Test: `apps/api/tests/unit/test_mirror.py` (add one test)

- [ ] **Step 1: Write the failing test** — append to `apps/api/tests/unit/test_mirror.py`
  (reuse the module's `_eff`, `LoggingRenderSink`, `build_tree` imports; model on
  `test_build_tree_rendered_branch`'s monkeypatch of `mirror_mod.storage.fetch_bytes`):

```python
async def test_build_tree_manifest_carries_doc_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S-drift-2: every doc-owned manifest entry (source + metadata.json + CHANGELOG.md) carries
    additive document_id/version_id keys (the scan's attribution + STALE classification hook);
    generated top-level entries (INDEX.md) carry neither. Schema marker stays /1 (additive)."""

    async def _fetch(key: str, *, bucket: str) -> bytes:
        return b"PDF"

    monkeypatch.setattr(mirror_mod.storage, "fetch_bytes", _fetch)
    eff = _eff()
    build = tmp_path / "b"
    manifest, _ = await build_tree(build, [eff], LoggingRenderSink())

    doc_entries = [e for e in manifest if "document_id" in e]
    assert len(doc_entries) == 3  # source file + metadata.json + CHANGELOG.md
    for entry in doc_entries:
        assert entry["document_id"] == str(eff.document_id)
        assert entry["version_id"] == str(eff.version_id)
        assert "sha256" in entry  # still a normal file entry

    index_entry = next(e for e in manifest if e["path"] == "INDEX.md")
    assert "document_id" not in index_entry and "version_id" not in index_entry

    raw = json.loads((build / "_meta" / "manifest.json").read_text())
    assert raw["schema"] == "easysynq.mirror.manifest/1"
```

- [ ] **Step 2: Run it — expect FAIL**

Run (from `apps/api/`):
`uv run pytest tests/unit/test_mirror.py::test_build_tree_manifest_carries_doc_ids -v`
Expected: FAIL — `assert len(doc_entries) == 3` finds 0 (no `document_id` keys yet).

- [ ] **Step 3: Create `services/common/org.py`**

```python
"""The resilient runtime single-org lookup (the 0038/0043/0045 lesson, at runtime).

An OPERATIONAL install renames ``short_code`` away from ``'DEFAULT'`` at setup G-E (this live
install: ``AHT``), so a bare ``short_code='DEFAULT'`` lookup aborts. D1 = single-org: fall back to
the only row. Returns ``None`` pre-setup (zero orgs) — callers skip persistence rather than crash
(the mirror sync/scan must work on an empty install).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models.organization import Organization


async def get_single_org_id(session: AsyncSession) -> uuid.UUID | None:
    org_id = (
        await session.execute(select(Organization.id).where(Organization.short_code == "DEFAULT"))
    ).scalar_one_or_none()
    if org_id is not None:
        return org_id
    rows = (await session.execute(select(Organization.id))).scalars().all()
    return rows[0] if len(rows) == 1 else None
```

- [ ] **Step 4: Enrich the manifest in `mirror.py`** — three edits:

(a) `_write` gains a keyword-only `extra` (existing callers unchanged):

```python
def _write(
    path: Path,
    data: bytes,
    manifest: list[dict[str, object]],
    rel_root: Path,
    *,
    extra: dict[str, object] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)  # parent-safe (the _ImportReport/<label>/ case)
    path.write_bytes(data)
    entry: dict[str, object] = {
        "path": str(path.relative_to(rel_root)),
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
    }
    if extra:
        entry.update(extra)
    manifest.append(entry)
```

(b) in `build_tree`'s per-doc loop, build the ref once and pass it to the THREE doc-owned writes
(source file, `metadata.json`, `CHANGELOG.md` — NOT the symlinks, NOT INDEX.md/_ImportReport):

```python
        # S-drift-2: doc attribution for the scan (additive manifest keys — schema stays /1).
        doc_ref: dict[str, object] = {
            "document_id": str(eff.document_id),
            "version_id": str(eff.version_id),
        }
        _write(doc_dir / source_filename, content, manifest, build_root, extra=doc_ref)
        _write(
            doc_dir / "metadata.json",
            _metadata(eff, source_filename, render_status, no_rendition, refs, proc_refs),
            manifest,
            build_root,
            extra=doc_ref,
        )
        _write(
            doc_dir / "CHANGELOG.md", _changelog_md(eff).encode(), manifest, build_root, extra=doc_ref
        )
```

- [ ] **Step 5: Run the test — expect PASS**

Run: `uv run pytest tests/unit/test_mirror.py -v` (the WHOLE file — prove no existing
manifest-shape test broke).
Expected: all PASS.

- [ ] **Step 6: Persist the baseline in `sync_mirror`** — in `mirror.py`:

(a) imports: add `from sqlalchemy import delete, select, update` (extend the existing line),
`from ...db.models.drift_scan import DriftScan` is NOT needed here — only:

```python
from ...db.models.mirror_build import MirrorBuild
from ..common.org import get_single_org_id
```

(b) module constant near the top (under `logger`): `_KEEP_BUILD_ROWS = 20`

(c) in `sync_mirror._build`, between `build_tree(...)` returning and `await s.commit()`
(`current_target` is computed once at the top of `sync_mirror` — see (d)):

```python
        # S-drift-2: persist the build manifest as the scan's expected-state baseline (keyed by
        # the .builds/<name> dir — commit-then-swap means an orphan row for a never-swapped build
        # is harmless; the scan verifies current's target against the newest SWAPPED row).
        # manifest_sha256 = the EXACT bytes just written (generated_at makes recompute impossible
        # — deliberate).
        manifest_bytes = (build_root / "_meta" / "manifest.json").read_bytes()
        org_id = await get_single_org_id(s)
        if org_id is None:
            logger.info("mirror.sync: no organization yet; baseline row skipped")
        else:
            s.add(
                MirrorBuild(
                    org_id=org_id,
                    build_name=build_root.name,
                    manifest=manifest,
                    manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
                    documents=len(effs),
                    files=sum(1 for e in manifest if "sha256" in e),
                    symlinks=sum(1 for e in manifest if "symlink_to" in e),
                )
            )
            await s.flush()
            # Keep-last-N prune — but NEVER the row `current` still points at: under a
            # persistent swap-failure mode, orphan rows pile above it and deleting it would
            # silently disable tamper detection on the still-served tree (the 4-lens fold §11.4).
            stale_ids = (
                (
                    await s.execute(
                        select(MirrorBuild.id)
                        .where(MirrorBuild.build_name != (current_target or ""))
                        .order_by(MirrorBuild.built_at.desc(), MirrorBuild.id.desc())
                        .offset(_KEEP_BUILD_ROWS)
                    )
                )
                .scalars()
                .all()
            )
            if stale_ids:
                await s.execute(delete(MirrorBuild).where(MirrorBuild.id.in_(stale_ids)))
```

(d) restructure `sync_mirror`'s tail so the swap happens INSIDE the session scope and the
pointer-integrity stamp lands right after it (the 4-lens fold §11.1). Replace everything from
`if session is not None:` to the final `return MirrorSyncResult(...)` with:

```python
    # The row `current` points at must survive the prune (see _build); resolve it up front.
    try:
        current_target: str | None = Path(os.readlink(root / "current")).name
    except OSError:
        current_target = None

    async def _build_swap_stamp(s: AsyncSession) -> tuple[list[dict[str, object]], int, int]:
        manifest, pending, count = await _build(s)  # commits: renditions + the baseline row
        atomic_swap(root, build_root)
        # Stamp swap success (pointer integrity, spec §11.1). A crash between the swap and this
        # commit self-heals: the scan treats current→newest-unswapped-row as the crash window
        # and persist_scan_results stamps it. No-op when the baseline row was skipped (no org).
        await s.execute(
            update(MirrorBuild)
            .where(MirrorBuild.build_name == build_root.name)
            .values(swapped_at=func.now())
        )
        await s.commit()
        return manifest, pending, count

    if session is not None:
        manifest, pending, count = await _build_swap_stamp(session)
    else:
        async with get_sessionmaker()() as own:
            manifest, pending, count = await _build_swap_stamp(own)

    files = sum(1 for entry in manifest if "sha256" in entry)
    symlinks = sum(1 for entry in manifest if "symlink_to" in entry)
    return MirrorSyncResult(
        documents=count, files=files, symlinks=symlinks, pending_renditions=pending
    )
```

(`func` comes via the existing `from sqlalchemy import …` line — extend it with `func` if not
already imported in mirror.py; `update` is already imported.)

- [ ] **Step 7: Fix the stale docstring** — in `mirror.py`'s module docstring, replace the final
  "**Deferred (with seams):**" sentence about the scan with:

```
**The drift scan (S-drift-2):** the D2+D3 SHA-256 integrity scan / quarantine / ``MIRROR_STALE`` +
``MIRROR_TAMPER`` audit events live in ``mirror_scan.py``; this module persists each build's
manifest into ``mirror_build`` (the scan's vault-side expected state — the on-disk
``_meta/manifest.json`` is a generated artifact, verified but never trusted as authority).
```

(keep the "rendering is S7b (live)" lead-in of that paragraph intact).

- [ ] **Step 8: Static checks + the unit file**

Run (from `apps/api/`): `uv run ruff check . ; uv run ruff format --check . ; uv run mypy ; uv run pytest tests/unit/test_mirror.py -v`
Expected: all clean / all PASS. (The `mirror_build` insert itself is proven in Task 6's
integration tests — it needs a real PG.)

- [ ] **Step 9: Commit**

```bash
git add apps/api/src/easysynq_api/services apps/api/tests/unit/test_mirror.py
git commit -m "feat(s-drift-2): manifest doc-id enrichment + mirror_build baseline write in sync_mirror"
```

---

### Task 3: The scanner pure core (`compare_tree` + quarantine)

**Files:**
- Create: `apps/api/src/easysynq_api/services/vault/mirror_scan.py` (pure core only — the DB layer
  is Task 4)
- Test: `apps/api/tests/unit/test_mirror_scan.py`

- [ ] **Step 1: Write the failing tests** — `apps/api/tests/unit/test_mirror_scan.py`:

```python
"""S-drift-2 unit proofs — the pure compare/classify/quarantine core (no DB).

The classification matrix (doc 05 §9.1 D2/D3): content mismatch (pre-classified, resolved against
vault digests), EXTRA, MISSING, SYMLINK_DIVERGENT (retarget + both type-swaps), the manifest-tamper
self-check, the never-follow-symlinks walk, unreadable-file findings, quarantine layout + failure
tolerance, and the counts() math. Symlink-creating tests may need Windows Developer Mode locally;
they run in Linux CI regardless.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

import pytest

from easysynq_api.services.vault import mirror_scan as scan_mod
from easysynq_api.services.vault.mirror_scan import (
    CLASS_EXTRA,
    CLASS_MISSING,
    CLASS_STALE,
    CLASS_SYMLINK,
    CLASS_UNEXPECTED,
    _CONTENT_MISMATCH,
    Finding,
    _quarantine_dir,
    classify_mismatch,
    compare_tree,
    quarantine_tree,
    write_quarantine,
    write_quarantine_index,
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_build(
    build: Path,
    files: dict[str, bytes],
    links: dict[str, str] | None = None,
    **extra_by_path: dict[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    """Lay a fabricated build tree + its manifest (the build_tree output shape) into ``build``.
    Returns (manifest entry list, manifest_sha256-of-the-on-disk-manifest.json)."""
    manifest: list[dict[str, Any]] = []
    for rel, data in files.items():
        p = build / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        entry: dict[str, Any] = {"path": rel, "sha256": _sha(data), "size_bytes": len(data)}
        entry.update(extra_by_path.get(rel, {}))
        manifest.append(entry)
    for rel, target in (links or {}).items():
        p = build / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, p, target_is_directory=True)
        manifest.append({"path": rel, "symlink_to": target})
    doc = {
        "schema": "easysynq.mirror.manifest/1",
        "generated_at": "2026-06-09T00:00:00+00:00",
        "files": sorted(manifest, key=lambda e: str(e["path"])),
    }
    raw = (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode()
    (build / "_meta").mkdir(parents=True, exist_ok=True)
    (build / "_meta" / "manifest.json").write_bytes(raw)
    return manifest, _sha(raw)


def _by_path(findings: list[Finding]) -> dict[str, Finding]:
    return {f.path: f for f in findings}


def test_clean_tree_no_findings(tmp_path: Path) -> None:
    build = tmp_path / "b"
    manifest, msha = _make_build(build, {"DO/08-Operation/SOP_RevA/source.pdf": b"PDF"})
    findings, scanned = compare_tree(build, manifest, msha)
    assert findings == []
    assert scanned == 2  # the source file + _meta/manifest.json


def test_content_mismatch_is_pre_classified_with_doc_attribution(tmp_path: Path) -> None:
    build = tmp_path / "b"
    doc_id = str(uuid.uuid4())
    manifest, msha = _make_build(
        build,
        {"a/source.pdf": b"GOOD"},
        **{"a/source.pdf": {"document_id": doc_id, "version_id": str(uuid.uuid4())}},
    )
    (build / "a" / "source.pdf").write_bytes(b"EVIL")
    findings, _ = compare_tree(build, manifest, msha)
    f = _by_path(findings)["a/source.pdf"]
    assert f.classification == _CONTENT_MISMATCH  # resolved against vault digests by scan_mirror
    assert f.expected_sha256 == _sha(b"GOOD")
    assert f.found_sha256 == _sha(b"EVIL")
    assert f.document_id == doc_id


def test_classify_mismatch_stale_vs_unexpected() -> None:
    known = {_sha(b"OLD-REV")}
    assert classify_mismatch(_sha(b"OLD-REV"), known) == CLASS_STALE
    assert classify_mismatch(_sha(b"FOREIGN"), known) == CLASS_UNEXPECTED
    assert classify_mismatch(_sha(b"ANYTHING"), set()) == CLASS_UNEXPECTED


def test_extra_and_missing_files(tmp_path: Path) -> None:
    build = tmp_path / "b"
    manifest, msha = _make_build(build, {"a/keep.pdf": b"K", "a/gone.pdf": b"G"})
    (build / "a" / "gone.pdf").unlink()
    (build / "STRAY.txt").write_bytes(b"not from the vault")
    findings, _ = compare_tree(build, manifest, msha)
    by = _by_path(findings)
    assert by["a/gone.pdf"].classification == CLASS_MISSING
    assert by["STRAY.txt"].classification == CLASS_EXTRA
    assert by["STRAY.txt"].found_sha256 == _sha(b"not from the vault")
    assert len(findings) == 2


def test_symlink_retarget_and_type_swaps(tmp_path: Path) -> None:
    build = tmp_path / "b"
    manifest, msha = _make_build(
        build,
        {"real/doc/source.pdf": b"P", "swapped-to-link.txt": b"T"},
        links={"PLAN/04-Context/doc": "../../real/doc", "retargeted": "../real/doc"},
    )
    # retarget one symlink; swap a file→symlink; swap a symlink→file
    (build / "retargeted").unlink()
    os.symlink("real", build / "retargeted", target_is_directory=True)
    (build / "swapped-to-link.txt").unlink()
    os.symlink("real/doc/source.pdf", build / "swapped-to-link.txt")
    link_path = build / "PLAN" / "04-Context" / "doc"
    link_path.unlink()
    link_path.write_bytes(b"now a file")
    findings, _ = compare_tree(build, manifest, msha)
    by = _by_path(findings)
    assert by["retargeted"].classification == CLASS_SYMLINK
    assert by["retargeted"].symlink_expected == "../real/doc"
    assert by["retargeted"].symlink_found == "real"
    assert by["swapped-to-link.txt"].classification == CLASS_SYMLINK
    assert by["PLAN/04-Context/doc"].classification == CLASS_SYMLINK
    assert len(findings) == 3


def test_manifest_tamper_detected_via_stored_digest(tmp_path: Path) -> None:
    build = tmp_path / "b"
    manifest, msha = _make_build(build, {"a/source.pdf": b"P"})
    mpath = build / "_meta" / "manifest.json"
    mpath.write_bytes(mpath.read_bytes().replace(b"easysynq", b"tampered"))
    findings, _ = compare_tree(build, manifest, msha)
    f = _by_path(findings)["_meta/manifest.json"]
    assert f.classification == CLASS_UNEXPECTED
    assert f.expected_sha256 == msha


def test_walker_never_follows_symlinks(tmp_path: Path) -> None:
    """A symlinked dir's contents must NOT be re-walked (py3.12 rglob would); an out-of-tree
    symlink target must never be entered."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_bytes(b"OUTSIDE")
    build = tmp_path / "b"
    manifest, msha = _make_build(
        build, {"real/doc/source.pdf": b"P"}, links={"alias/doc": "../real/doc"}
    )
    os.symlink(outside, build / "escape", target_is_directory=True)
    findings, scanned = compare_tree(build, manifest, msha)
    by = _by_path(findings)
    assert set(by) == {"escape"}  # the extra symlink itself — never its contents
    assert by["escape"].classification == CLASS_EXTRA
    assert not any("secret" in f.path or "alias/doc/" in f.path for f in findings)


def test_unreadable_file_is_a_tamper_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    build = tmp_path / "b"
    manifest, msha = _make_build(build, {"a/source.pdf": b"P"})

    def _boom(path: Path) -> str:
        raise OSError("permission denied")

    monkeypatch.setattr(scan_mod, "_hash_file", _boom)
    findings, _ = compare_tree(build, manifest, msha)
    f = _by_path(findings)["a/source.pdf"]
    assert f.classification == CLASS_UNEXPECTED
    assert f.note is not None and "unreadable" in f.note


def test_quarantine_copies_divergent_and_extra_only(tmp_path: Path) -> None:
    mirror_root = tmp_path / "m"
    build = mirror_root / ".builds" / "abc"
    manifest, msha = _make_build(build, {"a/source.pdf": b"GOOD"})
    (build / "a" / "source.pdf").write_bytes(b"EVIL")
    (build / "STRAY.txt").write_bytes(b"STRAY")
    findings = [
        Finding("a/source.pdf", CLASS_UNEXPECTED, _sha(b"GOOD"), _sha(b"EVIL")),
        Finding("STRAY.txt", CLASS_EXTRA, None, _sha(b"STRAY")),
        Finding("gone.pdf", CLASS_MISSING, _sha(b"G"), None),
        Finding("link", CLASS_SYMLINK, symlink_expected="../a", symlink_found="../b"),
    ]
    scan_id = uuid.uuid4()
    qdir = _quarantine_dir(mirror_root, scan_id)
    write_quarantine(qdir, build, findings)
    write_quarantine_index(qdir, "abc", scan_id, findings)
    qdirs = list((mirror_root / ".quarantine").iterdir())
    assert len(qdirs) == 1 and scan_id.hex in qdirs[0].name
    assert (qdirs[0] / "a" / "source.pdf").read_bytes() == b"EVIL"
    assert (qdirs[0] / "STRAY.txt").read_bytes() == b"STRAY"
    assert not (qdirs[0] / "gone.pdf").exists()
    index = json.loads((qdirs[0] / "quarantine.json").read_text())
    assert index["build_name"] == "abc" and index["scan_id"] == str(scan_id)
    assert len(index["findings"]) == 4  # ALL findings recorded, even uncopyable ones
    assert findings[0].quarantine_path is not None  # stamped back for the audit payload
    assert findings[0].quarantined_sha256 == _sha(b"EVIL")  # chain of custody: re-hashed copy
    assert findings[2].quarantine_path is None


def test_quarantine_copy_failure_is_noted_never_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mirror_root = tmp_path / "m"
    build = mirror_root / ".builds" / "abc"
    manifest, msha = _make_build(build, {"a/source.pdf": b"GOOD"})
    findings = [Finding("a/source.pdf", CLASS_UNEXPECTED, _sha(b"GOOD"), _sha(b"EVIL"))]

    def _boom(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(scan_mod.shutil, "copy2", _boom)
    qdir = _quarantine_dir(mirror_root, uuid.uuid4())
    write_quarantine(qdir, build, findings)  # must not raise
    assert findings[0].quarantine_path is None
    assert findings[0].note is not None and "quarantine copy failed" in findings[0].note


def test_manifest_deleted_is_a_missing_finding(tmp_path: Path) -> None:
    """A DELETED manifest.json is flagged, not silent (the tampered case alone is asymmetric)."""
    build = tmp_path / "b"
    manifest, msha = _make_build(build, {"a/source.pdf": b"P"})
    (build / "_meta" / "manifest.json").unlink()
    findings, _ = compare_tree(build, manifest, msha)
    f = _by_path(findings)["_meta/manifest.json"]
    assert f.classification == CLASS_MISSING
    assert f.expected_sha256 == msha


def test_quarantine_tree_moves_bytes_out(tmp_path: Path) -> None:
    """A foreign/rogue tree is quarantined BY MOVE — bytes preserved exactly, source gone (so
    _prune_builds can never destroy it and a rogue `current` dir no longer blocks the swap)."""
    mirror_root = tmp_path / "m"
    feral = mirror_root / ".builds" / "feral"
    (feral / "deep").mkdir(parents=True)
    (feral / "deep" / "payload.bin").write_bytes(b"PLANTED")
    finding = Finding(".builds/feral", CLASS_EXTRA)
    qdir = _quarantine_dir(mirror_root, uuid.uuid4())
    quarantine_tree(qdir, feral, finding)
    assert not feral.exists()  # moved, not copied
    assert (qdir / ".builds" / "feral" / "deep" / "payload.bin").read_bytes() == b"PLANTED"
    assert finding.quarantine_path is not None
```

(The "no quarantine dir when clean" behavior now lives at the orchestration level — `scan_mirror`
creates the dir only when findings exist — and is asserted by the integration clean-scan test.)

- [ ] **Step 2: Run them — expect FAIL (import error)**

Run: `uv run pytest tests/unit/test_mirror_scan.py -v`
Expected: collection error — `easysynq_api.services.vault.mirror_scan` does not exist.

- [ ] **Step 3: Write the pure core** — `apps/api/src/easysynq_api/services/vault/mirror_scan.py`:

```python
"""The D2+D3 mirror tamper/staleness scan (S-drift-2; doc 05 §9.1-§9.2.1, R11).

The mirror is NEVER trusted as truth: the expected state is the PG-persisted ``mirror_build``
manifest (keyed by ``current``'s actual ``.builds/<name>`` target), and the on-disk
``_meta/manifest.json`` is itself byte-verified against the build-time ``manifest_sha256``.
Divergent bytes are QUARANTINED to ``<mirror>/.quarantine/`` BEFORE any rebuild (R11 — the rebuild
prunes the old tree, so scan-first is what preserves forensic evidence); every anomaly is audited
(``MIRROR_STALE`` = known vault bytes of the same document at the wrong currency;
``MIRROR_TAMPER`` = foreign/extra/missing/symlink divergence); one ``drift_scan`` summary row per
scan. This module is split pure-core (``compare_tree``/``classify_mismatch``/``write_quarantine``
— no DB) vs orchestration (``scan_mirror``/``persist_scan_results``/``scan_and_sync``). Callers
hold ``LOCK_MIRROR_SYNC`` (scan and sync serialize — a swap can never prune a tree mid-walk).
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
import logging
import os
import shutil
import uuid
from pathlib import Path

logger = logging.getLogger("easysynq.mirror.scan")

MANIFEST_PATH = "_meta/manifest.json"

# Doc 05 §9.1 D3 classifications (the event type rides on them: STALE → MIRROR_STALE, the rest →
# MIRROR_TAMPER).
CLASS_STALE = "STALE_REVISION"
CLASS_UNEXPECTED = "UNEXPECTED_CONTENT"
CLASS_EXTRA = "EXTRA"
CLASS_MISSING = "MISSING"
CLASS_SYMLINK = "SYMLINK_DIVERGENT"
# The `current` pointer itself diverges (missing / a real directory / a foreign target / a
# rollback to an older swapped build) — always MIRROR_TAMPER (spec §11.1).
CLASS_POINTER = "POINTER_DIVERGENT"
# Pre-classification: a digest mismatch awaiting the vault digest check (scan_mirror resolves it
# to STALE_REVISION or UNEXPECTED_CONTENT).
_CONTENT_MISMATCH = "CONTENT_MISMATCH"


@dataclasses.dataclass(slots=True)
class Finding:
    path: str
    classification: str
    expected_sha256: str | None = None
    found_sha256: str | None = None
    document_id: str | None = None
    version_id: str | None = None  # the expected entry's version — STALE excludes its own digests
    note: str | None = None
    symlink_expected: str | None = None
    symlink_found: str | None = None
    quarantine_path: str | None = None
    quarantined_sha256: str | None = None


@dataclasses.dataclass(slots=True)
class ScanReport:
    scan_id: uuid.UUID
    started_at: datetime.datetime
    baseline: str  # "ok" | "none" (EMPTY registry only — fresh install / pre-0046 upgrade)
    status: str  # "CLEAN" | "DIVERGENT" | "FAILED"
    is_current: bool
    build_name: str | None
    findings: list[Finding]
    scanned: int = 0
    error: str | None = None
    # resolve_pointer's verdict on `current` itself (spec §11.1):
    # "ok" | "none" | "selfheal" | "missing" | "rogue_dir" | "foreign" | "rollback"
    pointer: str = "ok"

    def counts(self) -> dict[str, object]:
        by: dict[str, int] = {}
        for f in self.findings:
            by[f.classification] = by.get(f.classification, 0) + 1
        present_divergent = sum(1 for f in self.findings if f.classification != CLASS_MISSING)
        out: dict[str, object] = {
            "scanned": self.scanned,
            "ok": max(self.scanned - present_divergent, 0),
            "stale": by.get(CLASS_STALE, 0),
            "tampered": sum(
                by.get(c, 0)
                for c in (CLASS_UNEXPECTED, CLASS_EXTRA, CLASS_MISSING, CLASS_SYMLINK, CLASS_POINTER)
            ),
            "extra": by.get(CLASS_EXTRA, 0),
            "missing": by.get(CLASS_MISSING, 0),
            "symlink_divergent": by.get(CLASS_SYMLINK, 0),
            "quarantined": sum(1 for f in self.findings if f.quarantine_path is not None),
            "errors": sum(1 for f in self.findings if f.note is not None),
            "build_name": self.build_name,
            "is_current": self.is_current,
            "baseline": self.baseline,
            "pointer": self.pointer,
            "scan_id": str(self.scan_id),
        }
        if self.error:
            out["error"] = self.error
        return out


def _now() -> datetime.datetime:
    return datetime.datetime.now(tz=datetime.UTC)


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _walk_tree(root: Path) -> dict[str, str]:
    """Relative-posix-path → ``'file' | 'symlink'`` for everything under ``root``. Built on
    ``os.walk(followlinks=False)`` — NEVER ``rglob`` (Py3.12 follows symlinks); a symlinked dir is
    recorded as a symlink and pruned so its contents are never entered (in-tree aliases would
    double-walk, out-of-tree targets must never be read)."""
    found: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(dirpath)
        for d in list(dirnames):
            full = base / d
            if full.is_symlink():
                found[full.relative_to(root).as_posix()] = "symlink"
                dirnames.remove(d)
        for name in filenames:
            full = base / name
            kind = "symlink" if full.is_symlink() else "file"
            found[full.relative_to(root).as_posix()] = kind
    return found


def classify_mismatch(found_sha256: str, known_digests: set[str]) -> str:
    """Doc 05 §9.1 D3: known vault bytes of the SAME document (any version's source or cached
    rendition) → STALE_REVISION; anything else → UNEXPECTED_CONTENT."""
    return CLASS_STALE if found_sha256 in known_digests else CLASS_UNEXPECTED


def compare_tree(
    build_dir: Path, manifest: list[dict[str, object]], manifest_sha256: str
) -> tuple[list[Finding], int]:
    """Walk ``build_dir`` against the PG-persisted manifest. Returns (findings, paths-scanned).
    Content mismatches come back as ``_CONTENT_MISMATCH`` (the caller resolves them against the
    vault digests); everything else is final. Pure: no DB, no writes."""
    files = {str(e["path"]).replace("\\", "/"): e for e in manifest if "sha256" in e}
    links = {str(e["path"]).replace("\\", "/"): e for e in manifest if "symlink_to" in e}
    found = _walk_tree(build_dir)
    findings: list[Finding] = []

    for rel, entry in files.items():
        expected = str(entry["sha256"])
        doc_id = str(entry["document_id"]) if "document_id" in entry else None
        ver_id = str(entry["version_id"]) if "version_id" in entry else None
        kind = found.get(rel)
        if kind is None:
            findings.append(
                Finding(rel, CLASS_MISSING, expected_sha256=expected, document_id=doc_id)
            )
            continue
        if kind == "symlink":
            # A type swap (file → symlink): expected_sha256 + symlink_found convey it; `note`
            # stays reserved for the error channel feeding counts()["errors"].
            findings.append(
                Finding(
                    rel,
                    CLASS_SYMLINK,
                    expected_sha256=expected,
                    document_id=doc_id,
                    symlink_found=os.readlink(build_dir / rel),
                )
            )
            continue
        try:
            got = _hash_file(build_dir / rel)
        except OSError as exc:
            findings.append(
                Finding(
                    rel,
                    CLASS_UNEXPECTED,
                    expected_sha256=expected,
                    document_id=doc_id,
                    note=f"unreadable: {exc}",
                )
            )
            continue
        if got != expected:
            findings.append(
                Finding(
                    rel,
                    _CONTENT_MISMATCH,
                    expected_sha256=expected,
                    found_sha256=got,
                    document_id=doc_id,
                    version_id=ver_id,
                )
            )

    for rel, entry in links.items():
        target = str(entry["symlink_to"])
        kind = found.get(rel)
        if kind is None:
            findings.append(Finding(rel, CLASS_MISSING, symlink_expected=target))
        elif kind == "file":
            # A type swap (symlink → file): symlink_expected with no symlink_found conveys it.
            findings.append(Finding(rel, CLASS_SYMLINK, symlink_expected=target))
        else:
            actual = os.readlink(build_dir / rel)
            if actual != target:
                findings.append(
                    Finding(rel, CLASS_SYMLINK, symlink_expected=target, symlink_found=actual)
                )

    expected_paths = set(files) | set(links)
    for rel, kind in sorted(found.items()):
        if rel in expected_paths:
            continue
        if rel == MANIFEST_PATH:
            # The manifest is expected on disk but lives OUTSIDE its own entry list — verify it
            # byte-wise against the build-time digest (never read it as authority).
            try:
                got = _hash_file(build_dir / rel)
            except OSError as exc:
                findings.append(
                    Finding(
                        rel,
                        CLASS_UNEXPECTED,
                        expected_sha256=manifest_sha256,
                        note=f"unreadable: {exc}",
                    )
                )
                continue
            if got != manifest_sha256:
                findings.append(
                    Finding(rel, CLASS_UNEXPECTED, expected_sha256=manifest_sha256, found_sha256=got)
                )
            continue
        if kind == "symlink":
            try:
                actual: str | None = os.readlink(build_dir / rel)
            except OSError:
                actual = None
            findings.append(Finding(rel, CLASS_EXTRA, symlink_found=actual))
        else:
            try:
                got_extra: str | None = _hash_file(build_dir / rel)
            except OSError as exc:
                findings.append(Finding(rel, CLASS_EXTRA, note=f"unreadable: {exc}"))
                continue
            findings.append(Finding(rel, CLASS_EXTRA, found_sha256=got_extra))

    # A DELETED manifest.json must be flagged too — it lives outside its own entry list, so the
    # MISSING loop above never sees it (the 4-lens fold §11.6: only the tampered case was caught).
    if MANIFEST_PATH not in found:
        findings.append(Finding(MANIFEST_PATH, CLASS_MISSING, expected_sha256=manifest_sha256))

    return findings, len(found)


def _quarantine_dir(mirror_root: Path, scan_id: uuid.UUID) -> Path:
    """The per-scan quarantine dir, created 0o700 — users on the mirror export must never be able
    to browse tampered lookalike content (the 4-lens fold §11.6; chmod is weak on Windows — the
    production mount is Linux)."""
    stamp = _now().strftime("%Y%m%dT%H%M%SZ")
    qdir = mirror_root / ".quarantine" / f"{stamp}__{scan_id.hex}"
    qdir.mkdir(parents=True, exist_ok=True)
    os.chmod(qdir.parent, 0o700)
    os.chmod(qdir, 0o700)
    return qdir


def quarantine_tree(qdir: Path, src: Path, finding: Finding) -> None:
    """Quarantine a whole foreign/rogue tree BY MOVE (same-volume rename): preserves the bytes
    exactly, takes them out of ``_prune_builds``' reach, and (for a rogue real-dir ``current``)
    unblocks the next atomic swap. A move failure is noted, never raised."""
    dest = qdir / finding.path
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        finding.quarantine_path = str(dest)
    except OSError as exc:
        finding.note = (f"{finding.note}; " if finding.note else "") + (
            f"quarantine move failed: {exc}"
        )


def write_quarantine(
    qdir: Path,
    base: Path,
    findings: list[Finding],
) -> None:
    """R11: copy divergent bytes OUT of the tree BEFORE any rebuild can prune it. Copies every
    readable divergent/extra regular file (``found_sha256`` set, final classification), resolved
    against ``base``; MISSING/symlink findings have no bytes to copy and are recorded in the index
    only. Each copy is RE-HASHED (``quarantined_sha256`` — chain of custody: the preserved bytes
    must provably match the audited ``found_sha256``). A copy failure is noted on the finding,
    never raised — quarantine must not block correction."""
    for f in findings:
        if f.found_sha256 is None or f.classification not in (
            CLASS_STALE,
            CLASS_UNEXPECTED,
            CLASS_EXTRA,
        ):
            continue
        dest = qdir / f.path
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(base / f.path, dest)
            f.quarantine_path = str(dest)
            f.quarantined_sha256 = _hash_file(dest)
            if f.quarantined_sha256 != f.found_sha256:
                f.note = (f"{f.note}; " if f.note else "") + (
                    "quarantined bytes differ from the scanned digest (concurrent writer?)"
                )
        except OSError as exc:
            f.note = (f"{f.note}; " if f.note else "") + f"quarantine copy failed: {exc}"


def write_quarantine_index(
    qdir: Path, build_name: str | None, scan_id: uuid.UUID, findings: list[Finding]
) -> None:
    """The per-scan ``quarantine.json`` — every finding is recorded, even uncopyable ones."""
    index = {
        "schema": "easysynq.mirror.quarantine/1",
        "scan_id": str(scan_id),
        "build_name": build_name,
        "created_at": _now().isoformat(),
        "findings": [
            {
                "path": f.path,
                "classification": f.classification,
                "expected_sha256": f.expected_sha256,
                "found_sha256": f.found_sha256,
                "quarantined_sha256": f.quarantined_sha256,
                "symlink_expected": f.symlink_expected,
                "symlink_found": f.symlink_found,
                "quarantine_path": f.quarantine_path,
                "note": f.note,
            }
            for f in findings
        ],
    }
    (qdir / "quarantine.json").write_bytes(
        (json.dumps(index, indent=2, sort_keys=True) + "\n").encode()
    )
```

and add the `quarantined_sha256` field to the `Finding` dataclass (after `quarantine_path`):

```python
    quarantined_sha256: str | None = None
```

NOTE for the implementer: `write_quarantine` is called AFTER `_CONTENT_MISMATCH` resolution (Task
4), so it only ever sees final classifications; the unit tests construct final-classified findings
directly.

- [ ] **Step 4: Run the tests — expect PASS**

Run: `uv run pytest tests/unit/test_mirror_scan.py -v`
Expected: all PASS (on this box, symlink tests may need Developer Mode — if they error on
`os.symlink` privileges, note it and rely on Linux CI; do NOT skip-decorate them).

- [ ] **Step 5: Static checks**

Run: `uv run ruff check . ; uv run ruff format --check . ; uv run mypy`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add apps/api/src/easysynq_api/services/vault/mirror_scan.py apps/api/tests/unit/test_mirror_scan.py
git commit -m "feat(s-drift-2): scanner pure core — compare_tree classification matrix + R11 quarantine"
```

---

### Task 4: DB orchestration — pointer integrity, `scan_mirror`, `persist_scan_results`, `scan_and_sync`

**Files:**
- Modify: `apps/api/src/easysynq_api/services/vault/mirror_scan.py`
- Modify: `apps/api/src/easysynq_api/services/common/pg_locks.py` (add `holds_advisory_lock`)
- Test: `apps/api/tests/unit/test_mirror_scan.py` (append)

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/test_mirror_scan.py`:

```python
# --- orchestration (no-DB paths via the stubbed registry probe; DB paths are integration) ---

from easysynq_api.services.vault.mirror_scan import (  # noqa: E402
    CLASS_POINTER,
    PointerRow,
    ScanReport,
    resolve_pointer,
    scan_and_sync,
    scan_mirror,
)


def _prow(name: str, built: str, swapped: str | None) -> PointerRow:
    import datetime as dt

    def _ts(s: str) -> dt.datetime:
        return dt.datetime.fromisoformat(s).replace(tzinfo=dt.UTC)

    return PointerRow(name, _ts(built), _ts(swapped) if swapped else None)


def test_resolve_pointer_matrix() -> None:
    """The spec §11.1 pointer-integrity matrix, pure: the `current` symlink is verified against
    the registry, never trusted."""
    a = _prow("a", "2026-06-01T00:00:00", "2026-06-01T00:00:01")
    b = _prow("b", "2026-06-02T00:00:00", "2026-06-02T00:00:01")
    orphan_new = _prow("c", "2026-06-03T00:00:00", None)  # commit-then-swap-crash, newest
    orphan_old = _prow("z", "2026-05-01T00:00:00", None)  # ancient never-swapped orphan

    assert resolve_pointer(None, False, []) == ("none", None)  # empty registry: benign
    assert resolve_pointer("b", False, [a, b]) == ("ok", b)  # normal
    assert resolve_pointer(None, False, [a, b]) == ("missing", None)  # current deleted: TAMPER
    assert resolve_pointer(None, True, [a, b]) == ("rogue_dir", None)  # current is a real dir
    assert resolve_pointer("x", False, [a, b]) == ("foreign", None)  # planted/renamed tree
    assert resolve_pointer("a", False, [a, b]) == ("rollback", a)  # an older swapped build
    assert resolve_pointer("c", False, [a, b, orphan_new]) == ("selfheal", orphan_new)
    assert resolve_pointer("z", False, [a, b, orphan_old]) == ("rollback", orphan_old)


async def test_scan_empty_registry_is_no_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fresh install / pre-0046 upgrade: an EMPTY registry is the ONLY benign no-baseline —
    zero findings, zero quarantine. The registry probe is stubbed; session=None proves the
    path makes no other DB call."""

    async def _no_rows(session: object) -> list[PointerRow]:
        return []

    monkeypatch.setattr(scan_mod, "_pointer_rows", _no_rows)
    report = await scan_mirror(None, mirror_path=tmp_path)  # type: ignore[arg-type]
    assert report.baseline == "none"
    assert report.pointer == "none"
    assert report.status == "CLEAN"
    assert report.is_current is False
    assert report.findings == []
    assert not (tmp_path / ".quarantine").exists()


async def test_scan_failure_is_failed_never_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An infrastructure failure (the registry probe explodes) → an honest FAILED report, no
    raise (the backup posture; spec §8)."""

    async def _boom(session: object) -> list[PointerRow]:
        raise RuntimeError("pg exploded")

    monkeypatch.setattr(scan_mod, "_pointer_rows", _boom)
    report = await scan_mirror(None, mirror_path=tmp_path)  # type: ignore[arg-type]
    assert report.status == "FAILED"
    assert report.error is not None and "pg exploded" in report.error
    assert report.findings == []


def _report(
    status: str,
    *,
    findings: list[Finding] | None = None,
    baseline: str = "ok",
    is_current: bool = True,
) -> ScanReport:
    return ScanReport(
        scan_id=uuid.uuid4(),
        started_at=scan_mod._now(),
        baseline=baseline,
        status=status,
        is_current=is_current,
        build_name="abc",
        findings=findings or [],
    )


async def test_scan_and_sync_failed_gating(monkeypatch: pytest.MonkeyPatch) -> None:
    """Spec §8: `always` (the sync path) rebuilds even on FAILED; `if_needed` (the hourly path)
    does NOT — a scan failure is not evidence the mirror is wrong."""
    calls: list[str] = []

    async def _failed_scan(session: object, *, mirror_path: object = None) -> ScanReport:
        return _report("FAILED")

    async def _persist_ok(session: object, report: object, **kw: object) -> bool:
        return True

    async def _fake_sync(**kw: object) -> object:
        calls.append("rebuild")
        return object()

    async def _lock_held(session: object, key: int) -> bool:
        return True

    monkeypatch.setattr(scan_mod, "scan_mirror", _failed_scan)
    monkeypatch.setattr(scan_mod, "persist_scan_results", _persist_ok)
    monkeypatch.setattr(scan_mod, "sync_mirror", _fake_sync)
    monkeypatch.setattr(scan_mod, "holds_advisory_lock", _lock_held)

    _, result = await scan_and_sync(None, rebuild="if_needed", triggered_by="beat")  # type: ignore[arg-type]
    assert result is None and calls == []  # hourly: no rebuild on FAILED

    _, result = await scan_and_sync(None, rebuild="always", triggered_by="sync")  # type: ignore[arg-type]
    assert result is not None and calls == ["rebuild"]  # sync: correction never blocked


async def test_scan_and_sync_defers_rebuild_when_findings_not_persisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spec §11.5: unpersisted findings defer the correction — the rebuild would erase the
    on-disk evidence the next scan needs to re-detect and audit."""
    calls: list[str] = []
    divergent = _report("DIVERGENT", findings=[Finding("a", CLASS_UNEXPECTED, "e", "f")])

    async def _scan(session: object, *, mirror_path: object = None) -> ScanReport:
        return divergent

    async def _persist_fails(session: object, report: object, **kw: object) -> bool:
        return False

    async def _fake_sync(**kw: object) -> object:
        calls.append("rebuild")
        return object()

    monkeypatch.setattr(scan_mod, "scan_mirror", _scan)
    monkeypatch.setattr(scan_mod, "persist_scan_results", _persist_fails)
    monkeypatch.setattr(scan_mod, "sync_mirror", _fake_sync)

    _, result = await scan_and_sync(None, rebuild="always", triggered_by="sync")  # type: ignore[arg-type]
    assert result is None and calls == []


async def test_scan_and_sync_skips_rebuild_when_lock_lost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spec §11.5: a mid-scan connection loss FREES the session-level advisory lock — a lockless
    rebuild could race a concurrent sync's prune, so the pipeline re-verifies before correcting."""
    calls: list[str] = []

    async def _failed_scan(session: object, *, mirror_path: object = None) -> ScanReport:
        return _report("FAILED")

    async def _persist_ok(session: object, report: object, **kw: object) -> bool:
        return True

    async def _fake_sync(**kw: object) -> object:
        calls.append("rebuild")
        return object()

    async def _lock_lost(session: object, key: int) -> bool:
        return False

    monkeypatch.setattr(scan_mod, "scan_mirror", _failed_scan)
    monkeypatch.setattr(scan_mod, "persist_scan_results", _persist_ok)
    monkeypatch.setattr(scan_mod, "sync_mirror", _fake_sync)
    monkeypatch.setattr(scan_mod, "holds_advisory_lock", _lock_lost)

    _, result = await scan_and_sync(None, rebuild="always", triggered_by="sync")  # type: ignore[arg-type]
    assert result is None and calls == []


def test_counts_math() -> None:
    findings = [
        Finding("a", CLASS_STALE, "e", "f", quarantine_path="/q/a"),
        Finding("b", CLASS_UNEXPECTED, "e", "f", note="unreadable: x"),
        Finding("c", CLASS_EXTRA, None, "f", quarantine_path="/q/c"),
        Finding("d", CLASS_MISSING, "e", None),
        Finding("e", CLASS_SYMLINK, symlink_expected="x", symlink_found="y"),
    ]
    report = ScanReport(
        scan_id=uuid.uuid4(),
        started_at=scan_mod._now(),
        baseline="ok",
        status="DIVERGENT",
        is_current=True,
        build_name="abc",
        findings=findings,
        scanned=10,
    )
    c = report.counts()
    assert c["scanned"] == 10
    assert c["ok"] == 6  # 10 walked - 4 present-divergent (MISSING is not on disk)
    assert c["stale"] == 1
    assert c["tampered"] == 4
    assert (c["extra"], c["missing"], c["symlink_divergent"]) == (1, 1, 1)
    assert c["quarantined"] == 2
    assert c["errors"] == 1
    assert c["baseline"] == "ok" and c["is_current"] is True
    assert c["scan_id"] == str(report.scan_id)
```

- [ ] **Step 2: Run them — expect a whole-file collection error**

Run: `uv run pytest tests/unit/test_mirror_scan.py -v`
Expected: the FILE errors at collection — the new module-level import names (`PointerRow`,
`resolve_pointer`, `scan_mirror`, `scan_and_sync`) do not exist yet, which takes Task 3's
passing tests down with it until Step 3 lands. That whole-file ImportError IS the red state
(`test_counts_math` will pass immediately once the file collects — counts shipped in Task 3).

- [ ] **Step 3a: Add `holds_advisory_lock` to `services/common/pg_locks.py`** (after
  `pg_advisory_lock`):

```python
async def holds_advisory_lock(session: AsyncSession, key: int) -> bool:
    """Does THIS session's connection still hold session-level advisory lock ``key``? A dropped
    connection silently FREES the lock while the Python context manager believes it is held —
    the pool then hands the next statement a fresh, lockless connection. Callers doing
    work-after-failure (the S-drift-2 scan pipeline) re-verify before irreversible steps.
    (Single-arg ``pg_try_advisory_lock(bigint)`` stores the key as classid=high32/objid=low32;
    our keys fit in 32 bits, so classid is 0.)"""
    return bool(
        (
            await session.execute(
                text(
                    "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype = 'advisory' "
                    "AND classid = 0 AND objid = :k AND pid = pg_backend_pid())"
                ),
                {"k": key},
            )
        ).scalar()
    )
```

- [ ] **Step 3b: Append the orchestration layer to `mirror_scan.py`** — new imports at the top:

```python
from typing import Literal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...db.models._audit_enums import ActorType, AuditObjectType, EventType
from ...db.models._drift_enums import DriftScanKind, DriftScanStatus
from ...db.models._vault_enums import VersionState
from ...db.models.audit_event import AuditEvent
from ...db.models.document_version import DocumentVersion
from ...db.models.documented_information import DocumentedInformation
from ...db.models.drift_scan import DriftScan
from ...db.models.mirror_build import MirrorBuild
from ..common.org import get_single_org_id
from ..common.pg_locks import LOCK_MIRROR_SYNC, holds_advisory_lock
from .mirror import MirrorSyncResult, sync_mirror
from .render import RenderSink
```

and the functions (append after `write_quarantine`):

```python
@dataclasses.dataclass(frozen=True, slots=True)
class PointerRow:
    """The (build_name, built_at, swapped_at) projection the pointer-integrity check needs."""

    build_name: str
    built_at: datetime.datetime
    swapped_at: datetime.datetime | None


def resolve_pointer(
    current_target: str | None, current_is_real_dir: bool, rows: list[PointerRow]
) -> tuple[str, PointerRow | None]:
    """Pure pointer-integrity matrix (spec §11.1): verify `current` against the registry, never
    trust it. Returns (pointer_state, the row to scan against or None). States: 'none' (empty
    registry — the only benign no-baseline), 'ok', 'selfheal' (the swap-then-crash window:
    current → the newest not-yet-stamped row; persist completes the bookkeeping), and the four
    MIRROR_TAMPER states 'missing' / 'rogue_dir' / 'foreign' / 'rollback'."""
    if not rows:
        return ("none", None)
    swapped = [r for r in rows if r.swapped_at is not None]
    newest_swapped = max(swapped, key=lambda r: r.built_at) if swapped else None
    if current_target is None:
        return ("rogue_dir" if current_is_real_dir else "missing", None)
    cur = next((r for r in rows if r.build_name == current_target), None)
    if cur is None:
        return ("foreign", None)
    if cur.swapped_at is None:
        if newest_swapped is None or cur.built_at >= newest_swapped.built_at:
            return ("selfheal", cur)
        return ("rollback", cur)  # an ancient never-swapped orphan resurrected under current
    if newest_swapped is not None and cur.build_name != newest_swapped.build_name:
        return ("rollback", cur)
    return ("ok", cur)


async def _pointer_rows(session: AsyncSession) -> list[PointerRow]:
    rows = (
        await session.execute(
            select(
                MirrorBuild.build_name, MirrorBuild.built_at, MirrorBuild.swapped_at
            ).order_by(MirrorBuild.built_at)
        )
    ).all()
    return [PointerRow(name, built, swapped) for name, built, swapped in rows]


def _scan_builds_area(
    root: Path, registered: set[str], current_target: str | None
) -> list[Finding]:
    """Unregistered ``.builds/`` children are EXTRA → MIRROR_TAMPER: the next sync's
    ``_prune_builds`` would rmtree them UNAUDITED (spec §11.2; they get quarantined BY MOVE).
    Registered orphans (failed-swap leftovers) are benign; current's own target belongs to the
    pointer check. Mirror-root siblings stay deliberately out of scope."""
    findings: list[Finding] = []
    builds = root / ".builds"
    if not builds.is_dir():
        return findings
    for child in sorted(builds.iterdir()):
        if child.name in registered or child.name == current_target:
            continue
        findings.append(Finding(f".builds/{child.name}", CLASS_EXTRA))
    return findings


async def _known_digests(
    session: AsyncSession, document_id: uuid.UUID, exclude_version_id: uuid.UUID | None
) -> set[str]:
    """Every digest the vault knows for this document EXCEPT the expected version's own (spec
    §11.3 — doc 05's STALE is "matches an OLDER version"; same-version bytes in the wrong role,
    e.g. raw source bytes over the banded controlled-copy rendition, are TAMPER)."""
    stmt = select(DocumentVersion.source_sha256, DocumentVersion.rendition_blob_sha256).where(
        DocumentVersion.document_id == document_id
    )
    if exclude_version_id is not None:
        stmt = stmt.where(DocumentVersion.id != exclude_version_id)
    rows = (await session.execute(stmt)).all()
    return {digest for row in rows for digest in row if digest}


async def _is_current(session: AsyncSession, manifest: list[dict[str, object]]) -> bool:
    """The D3 staleness backstop: does the scanned build still cover EXACTLY the live Effective
    version set? Behind-vault is NOT tamper (no audit) — it just makes the hourly task rebuild."""
    expected = {str(e["version_id"]) for e in manifest if "version_id" in e}
    live = (
        (
            await session.execute(
                select(DocumentVersion.id).where(
                    DocumentVersion.version_state == VersionState.Effective
                )
            )
        )
        .scalars()
        .all()
    )
    return expected == {str(v) for v in live}


async def scan_mirror(
    session: AsyncSession, *, mirror_path: str | os.PathLike[str] | None = None
) -> ScanReport:
    """The D2+D3 scan: verify the `current` POINTER against the registry (spec §11.1) → load the
    PG baseline → walk + classify → sweep the .builds area → QUARANTINE divergent bytes (R11,
    before any rebuild; foreign/rogue trees by MOVE). Read-only on the DB. NEVER raises — an
    infrastructure failure returns an honest FAILED report (the backup posture). Persistence is
    ``persist_scan_results``."""
    scan_id = uuid.uuid4()
    started_at = _now()
    root = Path(mirror_path) if mirror_path is not None else Path(get_settings().mirror_path)
    current = root / "current"
    current_is_real_dir = current.is_dir() and not current.is_symlink()
    try:
        current_target: str | None = Path(os.readlink(current)).name
    except OSError:
        current_target = None
    build_name = current_target
    try:
        rows = await _pointer_rows(session)
        pointer, cur = resolve_pointer(current_target, current_is_real_dir, rows)
        if pointer == "none":
            return ScanReport(
                scan_id=scan_id,
                started_at=started_at,
                baseline="none",
                status="CLEAN",
                is_current=False,
                build_name=build_name,
                findings=[],
                pointer="none",
            )

        findings: list[Finding] = []
        tree_findings: list[Finding] = []
        scanned = 0
        is_current = False
        build_dir: Path | None = None

        if pointer in ("missing", "rogue_dir", "foreign"):
            findings.append(Finding("current", CLASS_POINTER, symlink_found=current_target))
        if cur is not None:
            row = (
                await session.execute(
                    select(MirrorBuild).where(MirrorBuild.build_name == cur.build_name)
                )
            ).scalar_one()
            build_dir = root / ".builds" / cur.build_name
            if build_dir.is_dir():
                tree_findings, scanned = compare_tree(
                    build_dir, row.manifest, row.manifest_sha256
                )
                for f in tree_findings:
                    if f.classification == _CONTENT_MISMATCH:
                        known: set[str] = set()
                        if f.document_id is not None and f.found_sha256 is not None:
                            known = await _known_digests(
                                session,
                                uuid.UUID(f.document_id),
                                uuid.UUID(f.version_id) if f.version_id else None,
                            )
                        f.classification = classify_mismatch(f.found_sha256 or "", known)
                findings.extend(tree_findings)
            if pointer == "rollback":
                # The per-file pass above covered the tree against ITS OWN row's manifest (known
                # old vault bytes — no wholesale quarantine needed); this is the pointer event.
                findings.append(Finding("current", CLASS_POINTER, symlink_found=current_target))
            if pointer in ("ok", "selfheal"):
                is_current = await _is_current(session, row.manifest)

        builds_findings = _scan_builds_area(root, {r.build_name for r in rows}, current_target)
        findings.extend(builds_findings)

        if findings:
            qdir = _quarantine_dir(root, scan_id)
            if build_dir is not None and build_dir.is_dir():
                write_quarantine(qdir, build_dir, tree_findings)
            for f in builds_findings:
                quarantine_tree(qdir, root / f.path, f)
            if pointer == "rogue_dir":
                pf = next(f for f in findings if f.classification == CLASS_POINTER)
                quarantine_tree(qdir, current, pf)  # also unblocks the next atomic swap
            elif pointer == "foreign" and current_target is not None:
                src = root / ".builds" / current_target
                if src.is_dir():
                    pf = next(f for f in findings if f.classification == CLASS_POINTER)
                    quarantine_tree(qdir, src, pf)
            write_quarantine_index(qdir, build_name, scan_id, findings)

        return ScanReport(
            scan_id=scan_id,
            started_at=started_at,
            baseline="ok",
            status="DIVERGENT" if findings else "CLEAN",
            is_current=is_current,
            build_name=build_name,
            findings=findings,
            scanned=scanned,
            pointer=pointer,
        )
    except Exception as exc:  # noqa: BLE001 — an infra failure is an honest FAILED, never a raise
        logger.exception("mirror.scan.failed")
        return ScanReport(
            scan_id=scan_id,
            started_at=started_at,
            baseline="ok",
            status="FAILED",
            is_current=False,
            build_name=build_name,
            findings=[],
            error=str(exc),
        )


async def persist_scan_results(
    session: AsyncSession, report: ScanReport, *, rebuild_triggered: bool, triggered_by: str
) -> bool:
    """One txn: a ``MIRROR_STALE``/``MIRROR_TAMPER`` audit event per anomaly (doc-attributable →
    object_type=document + scope_ref=identifier, the S-ing-5 precedent; else config keyed on the
    org) + the ``drift_scan`` summary row + the selfheal ``swapped_at`` stamp (spec §11.1).
    Quarantine files are already durably written (a crash between leaves bytes-without-events;
    the divergence is still on disk, so the next scan re-detects — self-healing). NO
    per-clean-scan audit event (hourly CLEAN events would spam the trail) — but EVERY scan gets
    its summary row (the row-per-scan contract). Returns success: a failure is logged, never
    raised, and the caller defers the rebuild when findings would otherwise go unrecorded
    (spec §11.5)."""
    if report.status == "FAILED":
        await session.rollback()  # the failed scan may have poisoned the txn
    try:
        org_id = await get_single_org_id(session)
        if org_id is None:
            logger.warning("mirror.scan: no organization yet; scan results not persisted")
            return False
        finished_at = _now()
        if report.pointer == "selfheal" and report.build_name is not None:
            # The swap-then-crash window: complete the crashed bookkeeping (the scan itself
            # stays read-only; an attacker cannot mint registry rows without DB write access).
            await session.execute(
                update(MirrorBuild)
                .where(
                    MirrorBuild.build_name == report.build_name,
                    MirrorBuild.swapped_at.is_(None),
                )
                .values(swapped_at=func.now())
            )
        for f in report.findings:
            event_type = (
                EventType.MIRROR_STALE
                if f.classification == CLASS_STALE
                else EventType.MIRROR_TAMPER
            )
            object_type, object_id, scope_ref = AuditObjectType.config, org_id, None
            if f.document_id is not None:
                doc_uuid = uuid.UUID(f.document_id)
                # Column-select, NOT session.get — a full entity would sit STALE in the identity
                # map when this same session's rebuild re-reads documents (the 4-lens fold §11.6).
                identifier = (
                    await session.execute(
                        select(DocumentedInformation.identifier).where(
                            DocumentedInformation.id == doc_uuid
                        )
                    )
                ).scalar_one_or_none()
                object_type, object_id, scope_ref = (
                    AuditObjectType.document,
                    doc_uuid,
                    identifier,
                )
            after: dict[str, object] = {
                "path": f.path,
                "classification": f.classification,
                "expected_sha256": f.expected_sha256,
                "found_sha256": f.found_sha256,
                "quarantine_path": f.quarantine_path,
                "quarantined_sha256": f.quarantined_sha256,
                "build_name": report.build_name,
                "scan_id": str(report.scan_id),
            }
            if f.classification == CLASS_POINTER:
                after["pointer_state"] = report.pointer
            if f.note:
                after["note"] = f.note
            if f.symlink_expected:
                after["symlink_expected"] = f.symlink_expected
            if f.symlink_found:
                after["symlink_found"] = f.symlink_found
            session.add(
                AuditEvent(
                    org_id=org_id,
                    occurred_at=finished_at,
                    actor_id=None,
                    actor_type=ActorType.system,
                    event_type=event_type,
                    object_type=object_type,
                    object_id=object_id,
                    scope_ref=scope_ref,
                    after=after,
                )
            )
        session.add(
            DriftScan(
                org_id=org_id,
                kind=DriftScanKind.MIRROR,
                started_at=report.started_at,
                finished_at=finished_at,
                status=DriftScanStatus(report.status),
                counts={**report.counts(), "rebuild_triggered": rebuild_triggered},
                triggered_by=triggered_by,
            )
        )
        await session.commit()
        return True
    except Exception:  # noqa: BLE001 — persistence must never raise into the pipeline
        logger.exception("mirror.scan: failed to persist scan results")
        await session.rollback()
        return False


async def scan_and_sync(
    session: AsyncSession,
    *,
    rebuild: Literal["always", "if_needed"],
    triggered_by: str,
    mirror_path: str | os.PathLike[str] | None = None,
    render_sink: RenderSink | None = None,
) -> tuple[ScanReport, MirrorSyncResult | None]:
    """The owner-fork §0.1 pipeline: scan-first (quarantine + audit + summary), THEN the rebuild
    as the vault-wins correction. ``always`` = the sync path (R11's per-sync leg; rebuilds even on
    a FAILED scan — a broken scan must never block correction). ``if_needed`` = the hourly path
    (rebuilds on DIVERGENT / behind-vault / no-baseline; NOT on FAILED — a scan failure is not
    evidence the mirror is wrong, and the nightly sync remains the convergence backstop). Two
    §11.5 guards: unpersisted FINDINGS defer the rebuild (it would erase the on-disk evidence the
    next scan needs to re-detect and audit — and a broken PG fails the rebuild anyway), and after
    any failure the advisory-lock ownership is re-verified (a dropped connection frees the
    session-level lock silently). The caller holds ``LOCK_MIRROR_SYNC``."""
    report = await scan_mirror(session, mirror_path=mirror_path)
    needs = report.status == "DIVERGENT" or report.baseline == "none" or not report.is_current
    do_rebuild = rebuild == "always" or (report.status != "FAILED" and needs)
    persisted = await persist_scan_results(
        session, report, rebuild_triggered=do_rebuild, triggered_by=triggered_by
    )
    if do_rebuild and not persisted and report.findings:
        logger.error(
            "mirror.scan: findings not persisted; deferring the rebuild to preserve re-detection",
            extra={"extra_fields": report.counts()},
        )
        do_rebuild = False
    if do_rebuild and (report.status == "FAILED" or not persisted):
        if not await holds_advisory_lock(session, LOCK_MIRROR_SYNC):
            logger.error("mirror.scan: advisory lock lost; skipping the rebuild this tick")
            return report, None
    result: MirrorSyncResult | None = None
    if do_rebuild:
        result = await sync_mirror(
            mirror_path=mirror_path, render_sink=render_sink, session=session
        )
    return report, result
```

- [ ] **Step 4: Run the unit file — expect PASS**

Run: `uv run pytest tests/unit/test_mirror_scan.py -v`
Expected: all PASS.

- [ ] **Step 5: Static checks**

Run: `uv run ruff check . ; uv run ruff format --check . ; uv run mypy`
Expected: clean. (Watch for an import cycle: `mirror_scan` imports from `mirror`, never the
reverse.)

- [ ] **Step 6: Commit**

```bash
git add apps/api/src/easysynq_api/services/vault/mirror_scan.py apps/api/tests/unit/test_mirror_scan.py
git commit -m "feat(s-drift-2): scan_mirror + persist_scan_results + the scan_and_sync pipeline"
```

---

### Task 5: Tasks, Beat cadence, settings knob, CLI

**Files:**
- Modify: `apps/api/src/easysynq_api/config.py`
- Modify: `apps/api/src/easysynq_api/tasks/mirror.py`
- Modify: `apps/api/src/easysynq_api/tasks/app.py`
- Modify: `apps/api/src/easysynq_api/cli/mirror.py`
- Test: `apps/api/tests/unit/test_mirror_scan_task_registration.py`

- [ ] **Step 1: Write the failing test** — `tests/unit/test_mirror_scan_task_registration.py`:

```python
"""S-drift-2: the hourly scan task is registered + its Beat entry rides the R11 settings knob."""

from __future__ import annotations

from easysynq_api.config import Settings, get_settings
from easysynq_api.tasks import app


def test_scan_task_registered() -> None:
    assert "easysynq.mirror.scan" in app.tasks


def test_beat_entry_schedule_matches_settings() -> None:
    # Under default env both sides are 3600.0, so a hardcoded schedule would also pass — the
    # real knob proof is test_default_interval_is_hourly_r11 + the settings-driven app.py wiring
    # (the literal-pinning limitation matches the existing task-registration convention).
    entry = app.conf.beat_schedule["mirror-scan"]
    assert entry["task"] == "easysynq.mirror.scan"
    assert entry["schedule"] == float(get_settings().mirror_scan_interval_seconds)


def test_default_interval_is_hourly_r11() -> None:
    assert Settings.model_fields["mirror_scan_interval_seconds"].default == 3600
```

- [ ] **Step 2: Run it — expect FAIL**

Run: `uv run pytest tests/unit/test_mirror_scan_task_registration.py -v`
Expected: FAIL (no `mirror_scan_interval_seconds` field; no `easysynq.mirror.scan` task; no
`mirror-scan` Beat entry).

- [ ] **Step 3: The settings knob** — in `config.py`, under the `# renderer + mirror` block
  (after `mirror_path`):

```python
    # S-drift-2: the D2+D3 mirror integrity-scan cadence (doc 05 §9.2.1 / R11: default hourly,
    # configurable — the ACCEPTED DRIFT WINDOW equals this interval; tightening narrows the
    # window at the cost of I/O).
    mirror_scan_interval_seconds: int = 3600
```

- [ ] **Step 4: Rework `tasks/mirror.py`** — replace the `sync_mirror` call with the pipeline and
  add the scan task (full new file body below; the module docstring gains the scan):

```python
"""Celery/Beat tasks for the read-only filesystem mirror (S7 sync + the S-drift-2 D2+D3 scan).

``mirror_sync`` is the scan-first full rebuild (R11's per-sync detection leg: scan the outgoing
tree, quarantine + audit divergence, THEN rebuild + swap — the rebuild prunes the old tree, so
scan-first is what preserves forensic evidence). Triggers: the nightly Beat reconcile, the
post-commit release/obsolete enqueue (``mirror_sink``), and the ``easysynq mirror sync`` CLI.
``mirror_scan`` is the hourly Beat integrity scan (doc 05 §9.2.1 — the accepted drift window =
the configured interval): same pipeline, but rebuilds only when divergent / behind-vault /
baseline-less (a CLEAN tick does no tree churn) and NOT on a FAILED scan. Both serialize under
``LOCK_MIRROR_SYNC`` (skip-if-held). Own disposed async engine per ``asyncio.run`` (the app's
non-owner ``easysynq_app`` role).
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import get_settings
from ..services.common.pg_locks import LOCK_MIRROR_SYNC, pg_advisory_lock
from ..services.vault.mirror_scan import scan_and_sync
from ..services.vault.render_gotenberg import GotenbergRenderSink
from .app import app

logger = logging.getLogger("easysynq.mirror.tasks")


async def _run_mirror_sync() -> int:
    """Scan-first rebuild under the advisory lock; returns the document count written (0 if
    another sync/scan holds the lock and this tick is skipped)."""
    engine = create_async_engine(get_settings().database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session, pg_advisory_lock(session, LOCK_MIRROR_SYNC) as held:
            if not held:
                logger.info("mirror.sync: another sync/scan holds the lock; skipping this tick")
                return 0
            # The worker renders for real (S7b); the api never renders (it presigns the cache).
            report, result = await scan_and_sync(
                session, rebuild="always", triggered_by="sync", render_sink=GotenbergRenderSink()
            )
            logger.info(
                "mirror.sync.done",
                extra={
                    "extra_fields": {
                        "documents": result.documents if result else 0,
                        "files": result.files if result else 0,
                        "symlinks": result.symlinks if result else 0,
                        "pending_renditions": result.pending_renditions if result else 0,
                        "scan_status": report.status,
                        "scan_findings": len(report.findings),
                    }
                },
            )
            return result.documents if result is not None else 0
    finally:
        await engine.dispose()


async def _run_mirror_scan() -> dict[str, object]:
    """The hourly D2+D3 integrity scan; rebuilds only when needed (never on FAILED)."""
    engine = create_async_engine(get_settings().database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session, pg_advisory_lock(session, LOCK_MIRROR_SYNC) as held:
            if not held:
                logger.info("mirror.scan: another sync/scan holds the lock; skipping this tick")
                return {"skipped_lock_held": 1}
            report, result = await scan_and_sync(
                session,
                rebuild="if_needed",
                triggered_by="beat",
                render_sink=GotenbergRenderSink(),
            )
            summary: dict[str, object] = {
                **report.counts(),
                "rebuild_triggered": result is not None,
            }
            logger.info("mirror.scan.done", extra={"extra_fields": summary})
            return summary
    finally:
        await engine.dispose()


@app.task(name="easysynq.mirror.sync")  # type: ignore[untyped-decorator]
def mirror_sync() -> int:
    """Scan-first full rebuild + atomic swap of the read-only mirror; returns the doc count."""
    return asyncio.run(_run_mirror_sync())


@app.task(name="easysynq.mirror.scan")  # type: ignore[untyped-decorator]
def mirror_scan() -> dict[str, object]:
    """Hourly D2+D3 integrity scan (R11); rebuilds only on divergence/staleness/no-baseline."""
    return asyncio.run(_run_mirror_scan())
```

- [ ] **Step 5: The Beat entry** — in `tasks/app.py`, after the `documents-review-sweep` entry:

```python
        # S-drift-2: the D2+D3 mirror integrity scan (doc 05 §9.2.1 / R11 — the accepted drift
        # window equals this interval; default hourly, configurable via
        # MIRROR_SCAN_INTERVAL_SECONDS). The nightly mirror-sync also scans (scan-first pipeline).
        "mirror-scan": {
            "task": "easysynq.mirror.scan",
            "schedule": float(_settings.mirror_scan_interval_seconds),
        },
```

- [ ] **Step 6: The CLI `scan` subcommand + scan-first `sync`** — in `cli/mirror.py`: extend the
  module docstring's command list with
  `python -m easysynq_api.cli.mirror scan   # integrity scan only (R11) — no rebuild`,
  add the imports:

```python
from ..services.vault.mirror_scan import (
    ScanReport,
    persist_scan_results,
    scan_and_sync,
    scan_mirror,
)
```

Rework `_sync` so the CLI sync path ALSO scans first (spec §7: every sync execution scans —
task, Beat, and CLI alike). The body keeps the `force` rendition-clear, then runs the pipeline:

```python
async def _sync(*, force: bool) -> MirrorSyncResult | None:
    """Scan-first rebuild under the advisory lock; ``None`` if another sync/scan holds the lock
    (skip). ``force`` clears every cached rendition first (``rebuild``) so each doc re-renders —
    used after a template change (e.g. the S7c verify QR) where the content-addressed cache would
    otherwise be a hit. (The clear nulls ``rendition_blob_sha256`` BEFORE the scan, so a
    tampered-with-an-old-rendition file classifies TAMPER rather than STALE on this manual path —
    run ``scan`` first if forensic classification matters.)"""
    engine = create_async_engine(get_settings().database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session, pg_advisory_lock(session, LOCK_MIRROR_SYNC) as held:
            if not held:
                return None
            if force:
                # Scoped to Effective (spec §11.6): only Effective versions ever re-render, so a
                # blanket null would PERMANENTLY destroy superseded-rendition digests and
                # mis-classify every future rendition-rollback tamper as TAMPER instead of STALE.
                # Committed BEFORE the pipeline — persist_scan_results rolls back on a FAILED
                # scan and would otherwise silently undo the forced re-render.
                await session.execute(
                    update(DocumentVersion)
                    .where(DocumentVersion.version_state == VersionState.Effective)
                    .values(rendition_blob_sha256=None)
                )
                await session.commit()
            # Render for real (S7b) — like the Beat task; without this the CLI rebuild would write
            # every doc as render_status="pending" (the no-op default sink).
            _report, result = await scan_and_sync(
                session, rebuild="always", triggered_by="cli", render_sink=GotenbergRenderSink()
            )
            return result
    finally:
        await engine.dispose()
```

(The `from ..services.vault.mirror import MirrorSyncResult, sync_mirror` import line drops
`sync_mirror` — it is no longer called directly here; keep `MirrorSyncResult` for the type. Add
`from ..db.models._vault_enums import VersionState` for the scoped force-clear.)

add the runner:

```python
async def _scan() -> ScanReport | None:
    """Detect/quarantine/audit only — NO rebuild (the operator follows with ``sync`` to correct).
    ``None`` if another sync/scan holds the lock."""
    engine = create_async_engine(get_settings().database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session, pg_advisory_lock(session, LOCK_MIRROR_SYNC) as held:
            if not held:
                return None
            report = await scan_mirror(session)
            await persist_scan_results(
                session, report, rebuild_triggered=False, triggered_by="cli"
            )
            return report
    finally:
        await engine.dispose()
```

and in `main()` register + branch (before the existing sync/rebuild handling):

```python
    sub.add_parser(
        "scan", help="integrity scan only — detect/quarantine/audit, no rebuild (doc 05 §9.2, R11)"
    )
```

```python
    if args.command == "scan":
        report = asyncio.run(_scan())
        if report is None:
            print("mirror scan skipped: another sync/scan is already in progress")
            return 0
        c = report.counts()
        print(
            f"mirror scan: status={report.status} baseline={report.baseline} "
            f"scanned={c['scanned']} findings={len(report.findings)} "
            f"quarantined={c['quarantined']} is_current={report.is_current}"
        )
        return 1 if report.status == "FAILED" else 0
```

- [ ] **Step 7: Run the tests — expect PASS**

Run: `uv run pytest tests/unit/test_mirror_scan_task_registration.py tests/unit/test_mirror_scan.py -v`
Expected: all PASS.

- [ ] **Step 8: Static checks**

Run: `uv run ruff check . ; uv run ruff format --check . ; uv run mypy`
Expected: clean.

- [ ] **Step 9: Commit**

```bash
git add apps/api/src/easysynq_api apps/api/tests/unit/test_mirror_scan_task_registration.py
git commit -m "feat(s-drift-2): hourly mirror-scan Beat task (R11 knob), scan-first sync, CLI scan"
```

---

### Task 6: Integration proofs (Linux CI)

**Files:**
- Create: `apps/api/tests/integration/test_mirror_scan.py`

These run in Linux CI only on this box — write them, run the static checks locally, and lean on
the `integration` CI job. Model every convention on `tests/integration/test_mirror.py` (it is the
canonical reference: `app_client`/`token_factory`/`tmp_path` fixtures, the s5 helpers, the
approver-releases SoD posture).

- [ ] **Step 1: Write the integration tests**

```python
"""S-drift-2 integration proofs — the D2+D3 scan end-to-end against a real vault.

Tamper the LIVE mirror tree four ways (foreign bytes / an older revision's bytes / extra / missing)
→ the scan classifies (MIRROR_TAMPER vs MIRROR_STALE), QUARANTINES before the rebuild (R11),
audits per anomaly, writes the drift_scan summary, and the scan_and_sync pipeline corrects the
tree (a re-scan is CLEAN). Plus the §11 folds: pointer integrity (a rollback'd / unregistered
`current` is TAMPER, never benign), the foreign-.builds quarantine-by-move, the
current-row-protected keep-last-N prune, the CLEAN/FAILED row-per-scan contract, and the REAL
task-path lock skip-tick. ⚠ Run-scoped/delta assertions only (the shared session DB): every
audit/drift_scan lookup keys on THIS scan's scan_id; SoD-2: releases come from the approver
(subj.b), never the author.
"""

from __future__ import annotations

import datetime
import hashlib
import os
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text

from easysynq_api.db.models._audit_enums import EventType
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.drift_scan import DriftScan
from easysynq_api.db.models.mirror_build import MirrorBuild
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.common.pg_locks import LOCK_MIRROR_SYNC
from easysynq_api.services.vault import mirror as mirror_mod
from easysynq_api.services.vault.mirror import sync_mirror
from easysynq_api.services.vault.mirror_scan import (
    CLASS_EXTRA,
    CLASS_MISSING,
    CLASS_STALE,
    CLASS_UNEXPECTED,
    ScanReport,
    persist_scan_results,
    scan_and_sync,
    scan_mirror,
)
from easysynq_api.services.vault.render import LoggingRenderSink

from . import s5_helpers as s5
from .test_mirror import _doc_dir, _grant_release_actors, _source_in
from .test_vault import _auth, _checkin, _upload

pytestmark = pytest.mark.integration


@pytest.fixture
def subj() -> SimpleNamespace:
    salt = uuid.uuid4().hex[:10]
    return SimpleNamespace(a=f"kc-author-{salt}", b=f"kc-approver-{salt}")


async def _sync(mirror: Path) -> None:
    await sync_mirror(mirror_path=mirror, render_sink=LoggingRenderSink())


async def _events_for_scan(scan_id: uuid.UUID) -> list[AuditEvent]:
    async with get_sessionmaker()() as s:
        return list(
            (
                await s.execute(
                    select(AuditEvent).where(
                        AuditEvent.after["scan_id"].astext == str(scan_id)
                    )
                )
            )
            .scalars()
            .all()
        )


async def _scan_row(scan_id: uuid.UUID) -> DriftScan | None:
    async with get_sessionmaker()() as s:
        return (
            await s.execute(
                select(DriftScan).where(DriftScan.counts["scan_id"].astext == str(scan_id))
            )
        ).scalar_one_or_none()


async def test_sync_writes_baseline_row(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    """Every sync persists a mirror_build row keyed by current's actual .builds target, with the
    manifest + the byte digest of the on-disk manifest.json."""
    mirror = tmp_path / "m"
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    await s5.drive_to_effective(app_client, ha, hb, hb, await s5.type_id("SOP"), b"BASE-V1")
    await _sync(mirror)

    build_name = Path(os.readlink(mirror / "current")).name
    async with get_sessionmaker()() as s:
        row = (
            await s.execute(select(MirrorBuild).where(MirrorBuild.build_name == build_name))
        ).scalar_one()
    manifest_bytes = (mirror / "current" / "_meta" / "manifest.json").read_bytes()
    assert row.manifest_sha256 == hashlib.sha256(manifest_bytes).hexdigest()
    assert any("document_id" in e for e in row.manifest)
    assert row.files == sum(1 for e in row.manifest if "sha256" in e)
    assert row.swapped_at is not None  # the post-swap pointer-integrity stamp (spec §11.1)


async def test_baseline_keep_last_n_prune(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The keep-last-N prune holds (N monkeypatched to 1 so the proof needs three syncs, not 23)
    AND it never deletes the row `current` points at (spec §11.4). At each sync's prune, the
    then-current build's row is excluded — so it takes a THIRD sync for the first build's row to
    become prunable. Run-scoped: only THIS test's three build rows are asserted on (pruning other
    tests' stale rows is the prune doing its job — the registry is regenerable)."""
    mirror = tmp_path / "m"
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    await s5.drive_to_effective(app_client, ha, hb, hb, await s5.type_id("SOP"), b"PRUNE-V1")
    monkeypatch.setattr(mirror_mod, "_KEEP_BUILD_ROWS", 1)

    await _sync(mirror)
    first_build = Path(os.readlink(mirror / "current")).name
    await _sync(mirror)
    second_build = Path(os.readlink(mirror / "current")).name
    # After sync 2: first's row SURVIVES — it was current's target during sync 2's prune (the
    # §11.4 exclusion: never disarm detection on the still-served tree).
    async with get_sessionmaker()() as s:
        names = set(
            (
                await s.execute(
                    select(MirrorBuild.build_name).where(
                        MirrorBuild.build_name.in_([first_build, second_build])
                    )
                )
            ).scalars()
        )
    assert names == {first_build, second_build}

    await _sync(mirror)
    third_build = Path(os.readlink(mirror / "current")).name
    assert len({first_build, second_build, third_build}) == 3
    # After sync 3 (current was second during its prune): first is finally beyond keep-1 and
    # unprotected → pruned; second (protected) and third (newest) survive.
    async with get_sessionmaker()() as s:
        names = set(
            (
                await s.execute(
                    select(MirrorBuild.build_name).where(
                        MirrorBuild.build_name.in_([first_build, second_build, third_build])
                    )
                )
            ).scalars()
        )
    assert names == {second_build, third_build}


async def test_clean_scan_after_sync(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    mirror = tmp_path / "m"
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    await s5.drive_to_effective(app_client, ha, hb, hb, await s5.type_id("SOP"), b"CLEAN-V1")
    await _sync(mirror)

    async with get_sessionmaker()() as s:
        report = await scan_mirror(s, mirror_path=mirror)
        persisted = await persist_scan_results(
            s, report, rebuild_triggered=False, triggered_by="cli"
        )
    assert report.status == "CLEAN"
    assert report.baseline == "ok"
    assert report.pointer == "ok"
    assert report.is_current is True
    assert report.findings == []
    assert not (mirror / ".quarantine").exists()
    assert persisted is True
    # The noise posture is meaningful only through persist: NO audit events for a clean scan…
    assert await _events_for_scan(report.scan_id) == []
    # …but EVERY scan gets its drift_scan summary row (the row-per-scan contract, spec §6).
    row = await _scan_row(report.scan_id)
    assert row is not None and row.status.value == "CLEAN"


async def test_tamper_detect_quarantine_audit_correct(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    """The headline D2 proof: foreign bytes + extra + missing → MIRROR_TAMPER each, quarantined
    BEFORE the vault-wins rebuild, audited (doc-attributed where possible), summarized; the
    pipeline corrects and a re-scan is CLEAN."""
    mirror = tmp_path / "m"
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    doc = await s5.drive_to_effective(
        app_client, ha, hb, hb, await s5.type_id("SOP"), b"TAMPER-GOOD"
    )
    await _sync(mirror)

    src = _source_in(_doc_dir(mirror, doc["identifier"]))
    src.write_bytes(b"TAMPER-EVIL")
    (mirror / "current" / "STRAY.txt").write_text("not from the vault")
    changelog = _doc_dir(mirror, doc["identifier"]) / "CHANGELOG.md"
    changelog.unlink()

    async with get_sessionmaker()() as s:
        report, result = await scan_and_sync(
            s,
            rebuild="if_needed",
            triggered_by="beat",
            mirror_path=mirror,
            render_sink=LoggingRenderSink(),
        )

    assert report.status == "DIVERGENT"
    assert result is not None  # the rebuild ran
    by = {f.path: f for f in report.findings}
    rel_src = src.relative_to(mirror / "current").as_posix()
    rel_log = changelog.relative_to(mirror / "current").as_posix()
    assert by[rel_src].classification == CLASS_UNEXPECTED
    assert by["STRAY.txt"].classification == CLASS_EXTRA
    assert by[rel_log].classification == CLASS_MISSING

    # R11: the tampered bytes were quarantined before the rebuild pruned the old tree.
    qdirs = list((mirror / ".quarantine").iterdir())
    assert len(qdirs) == 1
    assert (qdirs[0] / rel_src).read_bytes() == b"TAMPER-EVIL"
    assert (qdirs[0] / "STRAY.txt").read_bytes() == b"not from the vault"

    # Audited per anomaly — all MIRROR_TAMPER; the doc-owned ones attributed to the document.
    events = await _events_for_scan(report.scan_id)
    assert len(events) == 3
    assert {e.event_type for e in events} == {EventType.MIRROR_TAMPER}
    doc_events = [e for e in events if str(e.object_id) == doc["id"]]
    assert len(doc_events) == 2  # the source file + the missing CHANGELOG.md
    assert all(e.scope_ref == doc["identifier"] for e in doc_events)

    # The drift_scan summary row.
    row = await _scan_row(report.scan_id)
    assert row is not None
    assert row.status.value == "DIVERGENT"
    assert row.counts["rebuild_triggered"] is True
    assert row.triggered_by == "beat"

    # Corrected: the live tree re-hashes clean.
    assert _source_in(_doc_dir(mirror, doc["identifier"])).read_bytes() == b"TAMPER-GOOD"
    assert not (mirror / "current" / "STRAY.txt").exists()
    async with get_sessionmaker()() as s:
        rescan = await scan_mirror(s, mirror_path=mirror)
    assert rescan.status == "CLEAN"


async def test_stale_revision_classification(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    """D3: replacing the mirrored content with an OLDER revision's bytes is MIRROR_STALE
    (STALE_REVISION), not tamper — the bytes are known vault content of the same document."""
    mirror = tmp_path / "m"
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    type_id = await s5.type_id("SOP")
    doc = await s5.drive_to_effective(app_client, ha, hb, hb, type_id, b"STALE-V1")
    did = doc["id"]
    # Revise to v2 (author a) → approve + release (b) — the test_mirror.py supersession recipe.
    await app_client.post(f"/api/v1/documents/{did}/start-revision", headers=ha)
    sha2 = await _upload(app_client, ha, did, b"STALE-V2")
    await _checkin(app_client, ha, did, sha2, change_reason="v2", change_significance="MINOR")
    await app_client.post(f"/api/v1/documents/{did}/submit-review", headers=ha)
    task_id = await s5.task_for_doc(did)
    await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hb, json={"outcome": "approve"}
    )
    await app_client.post(f"/api/v1/documents/{did}/release", headers=hb, json={})
    await _sync(mirror)

    src = _source_in(_doc_dir(mirror, doc["identifier"]))
    assert src.read_bytes() == b"STALE-V2"
    src.write_bytes(b"STALE-V1")  # roll the file back to the superseded revision's bytes

    async with get_sessionmaker()() as s:
        report = await scan_mirror(s, mirror_path=mirror)
        await persist_scan_results(s, report, rebuild_triggered=False, triggered_by="cli")
    rel_src = src.relative_to(mirror / "current").as_posix()
    f = next(f for f in report.findings if f.path == rel_src)
    assert f.classification == CLASS_STALE
    events = await _events_for_scan(report.scan_id)
    assert [e.event_type for e in events] == [EventType.MIRROR_STALE]

    # The OLDER-RENDITION leg (spec §11.7): a rollback to a superseded version's cached
    # controlled-copy rendition digest is STALE too (drops if rendition_blob_sha256 ever falls
    # out of _known_digests). Seed a fake rendition digest on the superseded version, then
    # plant bytes with exactly that digest.
    old_rendition = b"OLD-RENDITION-BYTES"
    async with get_sessionmaker()() as s:
        await s.execute(
            text(
                "UPDATE document_version SET rendition_blob_sha256 = :sha "
                "WHERE document_id = :doc AND version_state = 'Superseded'"
            ),
            {"sha": hashlib.sha256(old_rendition).hexdigest(), "doc": did},
        )
        await s.commit()
    src.write_bytes(old_rendition)
    async with get_sessionmaker()() as s:
        report2 = await scan_mirror(s, mirror_path=mirror)
    f2 = next(f for f in report2.findings if f.path == rel_src)
    assert f2.classification == CLASS_STALE


async def test_behind_vault_build_is_not_current_no_audit(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    """The D3 currency backstop: a release AFTER the last sync makes the build not-current —
    NOT tamper (zero findings, zero audit events), but the if_needed pipeline rebuilds."""
    mirror = tmp_path / "m"
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    type_id = await s5.type_id("SOP")
    await s5.drive_to_effective(app_client, ha, hb, hb, type_id, b"CURRENT-V1")
    await _sync(mirror)
    doc2 = await s5.drive_to_effective(app_client, ha, hb, hb, type_id, b"CURRENT-V2-DOC")

    async with get_sessionmaker()() as s:
        report, result = await scan_and_sync(
            s,
            rebuild="if_needed",
            triggered_by="beat",
            mirror_path=mirror,
            render_sink=LoggingRenderSink(),
        )
    assert report.status == "CLEAN"  # the tree matches its baseline — nothing was tampered
    assert report.is_current is False  # but the vault moved on
    assert report.findings == []
    assert await _events_for_scan(report.scan_id) == []  # behind-vault is never audited
    row = await _scan_row(report.scan_id)  # the row-per-scan contract holds for CLEAN scans
    assert row is not None and row.status.value == "CLEAN"
    assert row.counts["rebuild_triggered"] is True
    assert result is not None  # the rebuild caught the mirror up
    assert _doc_dir(mirror, doc2["identifier"]).is_dir()


async def test_unregistered_current_is_foreign_tamper(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    """Spec §11.1: with a NON-empty registry, a current target with no row is FOREIGN →
    MIRROR_TAMPER (a planted/renamed tree must never pass as the benign no-baseline); the
    pipeline still corrects and the fresh build is registered. (The TRUE empty-registry
    no-baseline — the pre-0046 production upgrade — is the stubbed unit test: the shared
    integration DB always carries other tests' registry rows.)"""
    mirror = tmp_path / "m"
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    await s5.drive_to_effective(app_client, ha, hb, hb, await s5.type_id("SOP"), b"UPGRADE-V1")
    await _sync(mirror)

    build_name = Path(os.readlink(mirror / "current")).name
    async with get_sessionmaker()() as s:
        await s.execute(text("DELETE FROM mirror_build WHERE build_name = :b"), {"b": build_name})
        await s.commit()

    async with get_sessionmaker()() as s:
        report, result = await scan_and_sync(
            s,
            rebuild="if_needed",
            triggered_by="beat",
            mirror_path=mirror,
            render_sink=LoggingRenderSink(),
        )
    assert report.pointer == "foreign"
    assert report.status == "DIVERGENT"
    assert [f.classification for f in report.findings if f.path == "current"] == [
        "POINTER_DIVERGENT"
    ]
    events = await _events_for_scan(report.scan_id)
    assert {e.event_type for e in events} == {EventType.MIRROR_TAMPER}
    assert result is not None  # corrected: a fresh, registered build serves
    new_build = Path(os.readlink(mirror / "current")).name
    assert new_build != build_name
    async with get_sessionmaker()() as s:
        row = (
            await s.execute(select(MirrorBuild).where(MirrorBuild.build_name == new_build))
        ).scalar_one_or_none()
    assert row is not None and row.swapped_at is not None  # the post-swap stamp landed


async def test_persist_writes_failed_row(app_under_test: object) -> None:
    """Spec §8/§11.7: a FAILED report still gets its drift_scan summary row — the runbook's
    'persistent FAILED stream' operator signal depends on it. (The report is constructed
    directly; producing a real infra failure is the unit suite's job.)"""
    report = ScanReport(
        scan_id=uuid.uuid4(),
        started_at=datetime.datetime.now(tz=datetime.UTC),
        baseline="ok",
        status="FAILED",
        is_current=False,
        build_name="deadbeef",
        findings=[],
        error="simulated",
    )
    async with get_sessionmaker()() as s:
        persisted = await persist_scan_results(
            s, report, rebuild_triggered=False, triggered_by="cli"
        )
    assert persisted is True
    row = await _scan_row(report.scan_id)
    assert row is not None and row.status.value == "FAILED"
    assert row.counts["error"] == "simulated"


async def test_pointer_rollback_is_tamper(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    """Spec §11.1: repointing `current` at a restored OLDER swapped build is MIRROR_TAMPER
    (POINTER_DIVERGENT) — whole-tree rollback must never pass as a benign stale mirror."""
    mirror = tmp_path / "m"
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    await s5.drive_to_effective(app_client, ha, hb, hb, await s5.type_id("SOP"), b"PTR-V1")
    await _sync(mirror)
    first_build = Path(os.readlink(mirror / "current")).name
    saved = tmp_path / "saved-old-build"
    shutil.copytree(mirror / ".builds" / first_build, saved, symlinks=True)

    await s5.drive_to_effective(app_client, ha, hb, hb, await s5.type_id("SOP"), b"PTR-V2")
    await _sync(mirror)  # second build supersedes; _prune_builds removed the first dir

    # The attack: restore the old build dir and repoint current at it.
    shutil.copytree(saved, mirror / ".builds" / first_build, symlinks=True)
    tmp_link = mirror / ".current.attack.tmp"
    os.symlink(os.path.join(".builds", first_build), tmp_link)
    os.replace(tmp_link, mirror / "current")

    async with get_sessionmaker()() as s:
        report, result = await scan_and_sync(
            s,
            rebuild="if_needed",
            triggered_by="beat",
            mirror_path=mirror,
            render_sink=LoggingRenderSink(),
        )
    assert report.pointer == "rollback"
    assert report.status == "DIVERGENT"
    pointer_findings = [f for f in report.findings if f.path == "current"]
    assert len(pointer_findings) == 1
    events = await _events_for_scan(report.scan_id)
    assert EventType.MIRROR_TAMPER in {e.event_type for e in events}
    assert result is not None  # corrected: current repointed at a fresh build
    rescan_target = Path(os.readlink(mirror / "current")).name
    assert rescan_target != first_build


async def test_foreign_builds_tree_quarantined_by_move(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    """Spec §11.2: an unregistered .builds/ child is EXTRA→TAMPER and is MOVED to quarantine —
    otherwise the next sync's prune would rmtree the planted bytes unaudited."""
    mirror = tmp_path / "m"
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    await s5.drive_to_effective(app_client, ha, hb, hb, await s5.type_id("SOP"), b"FERAL-V1")
    await _sync(mirror)
    feral = mirror / ".builds" / "feral"
    (feral / "deep").mkdir(parents=True)
    (feral / "deep" / "payload.bin").write_bytes(b"PLANTED")

    async with get_sessionmaker()() as s:
        report = await scan_mirror(s, mirror_path=mirror)
    f = next(f for f in report.findings if f.path == ".builds/feral")
    assert f.classification == CLASS_EXTRA
    assert not feral.exists()  # moved, not copied — out of _prune_builds' reach
    qdirs = list((mirror / ".quarantine").iterdir())
    assert len(qdirs) == 1
    assert (qdirs[0] / ".builds" / "feral" / "deep" / "payload.bin").read_bytes() == b"PLANTED"


async def test_destroyed_served_tree_is_tamper_not_clean(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
) -> None:
    """C1 (Task-4 quality fold): destroying the served build tree while `current` stays valid
    must be MIRROR_TAMPER (POINTER) + rebuild — NOT a silent CLEAN (the most basic tamper)."""
    mirror = tmp_path / "m"
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    await s5.drive_to_effective(app_client, ha, hb, hb, await s5.type_id("SOP"), b"GONE-V1")
    await _sync(mirror)
    build_name = Path(os.readlink(mirror / "current")).name
    shutil.rmtree(mirror / ".builds" / build_name)  # current symlink intact, target gone

    async with get_sessionmaker()() as s:
        report, result = await scan_and_sync(
            s,
            rebuild="if_needed",
            triggered_by="beat",
            mirror_path=mirror,
            render_sink=LoggingRenderSink(),
        )
    assert report.status == "DIVERGENT"
    assert any(f.classification == "POINTER_DIVERGENT" for f in report.findings)
    events = await _events_for_scan(report.scan_id)
    assert EventType.MIRROR_TAMPER in {e.event_type for e in events}
    assert result is not None  # the hourly path rebuilt rather than reporting a false CLEAN


async def test_scan_task_skips_when_sync_lock_held(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scan and sync share LOCK_MIRROR_SYNC — the REAL task path skip-ticks while a holder holds
    the lock (the test_mirror_sync_advisory_lock_serializes convention: drive _run_mirror_scan,
    not the bare primitive — a vacuous two-session lock test passes even if the task never takes
    the lock)."""
    from easysynq_api.config import get_settings
    from easysynq_api.tasks.mirror import _run_mirror_scan

    monkeypatch.setenv("MIRROR_PATH", str(tmp_path / "m"))
    get_settings.cache_clear()
    try:
        async with get_sessionmaker()() as holder:
            held = (
                await holder.execute(
                    text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_MIRROR_SYNC}
                )
            ).scalar()
            assert held is True
            assert await _run_mirror_scan() == {"skipped_lock_held": 1}  # contended → skipped
            await holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_MIRROR_SYNC})
    finally:
        get_settings.cache_clear()
```

NOTE for the implementer: the `from .test_mirror import _doc_dir, _grant_release_actors,
_source_in` cross-import is the established pattern (test_mirror.py itself imports from
test_vault). If `_grant_release_actors`'s `subj` shape differs, inline the two-line grant body
instead.

- [ ] **Step 2: Static checks (the local gate for integration code)**

Run: `uv run ruff check . ; uv run ruff format --check . ; uv run mypy`
Expected: clean. (`uv run pytest -m integration` needs Docker + Linux — it runs in CI; do NOT
attempt it on this box.)

- [ ] **Step 3: Commit**

```bash
git add apps/api/tests/integration/test_mirror_scan.py
git commit -m "test(s-drift-2): integration proofs — tamper/stale/quarantine/audit/correct + lock + upgrade path"
```

---

### Task 7: Docs + spec amendments

**Files:**
- Modify: `docs/05-revision-and-drift.md` (§9.1 D2/D3 rows + §9.2 closing para)
- Modify: `docs/14-data-model.md` (the two tables, wherever the visual_diff/0042-era tables live)
- Modify: `docs/12-security-and-audit.md` (§3 Integrity alert row)
- Create: `docs/runbooks/mirror-drift-scan.md` + add a line to `docs/runbooks/00-index.md`
- Modify: `docs/superpowers/specs/2026-06-09-s-drift-2-mirror-tamper-scan-design.md` (2 amendments)
- Modify: `CLAUDE.md` (Recent learnings + Current status) · `docs/slice-history.md` (slice entry)

- [ ] **Step 1: docs/05** — in the §9.1 D2 and D3 rows and the §9.2 closing paragraph, add a
  shipped marker + pointers (keep the normative text intact), e.g. append to the D2 "How it works"
  cell: `*(Shipped S-drift-2: `services/vault/mirror_scan.py`; expected state = the PG-persisted
  `mirror_build` manifest; events `MIRROR_STALE`/`MIRROR_TAMPER`; summary row `drift_scan`.)*` and
  to §9.2.1's cadence row: `*(`MIRROR_SCAN_INTERVAL_SECONDS`, default 3600 — the `mirror-scan`
  Beat entry.)*`

- [ ] **Step 2: docs/14** — add `mirror_build` + `drift_scan` (columns per the spec §2 tables,
  with the one-line purpose each + the additive `drift_scan_kind` note for S-drift-3) in the
  section where operational/cache tables (visual_diff) are documented; note `event_type` gained
  `MIRROR_STALE`/`MIRROR_TAMPER` in the §12 enum list if one is maintained there.

- [ ] **Step 3: docs/12 §3** — the "Integrity" alert row ("blob re-hash verify pass/fail,
  audit-chain verify pass/fail, mirror-sync regeneration"): append "mirror tamper/staleness scan
  (`MIRROR_STALE`/`MIRROR_TAMPER`, S-drift-2)".

- [ ] **Step 4: The runbook** — create `docs/runbooks/mirror-drift-scan.md`:

```markdown
# Mirror drift scan — operator notes (S-drift-2, R11)

The D2+D3 integrity scan re-hashes every mirrored file against the vault-persisted build manifest
(`mirror_build`) on **every mirror-sync** and on an **hourly Beat scan**
(`MIRROR_SCAN_INTERVAL_SECONDS`, default 3600 — the accepted drift window equals this interval;
tighten it to narrow the window at the cost of I/O). Divergence is **quarantined before the
vault-wins rebuild**, audited (`MIRROR_STALE` = an older revision's bytes; `MIRROR_TAMPER` =
foreign bytes / extra / missing files / symlink changes — treat as a security signal), and
summarized in the `drift_scan` table.

## Quarantine

- Location: `<mirror_path>/.quarantine/<UTC-stamp>__<scan-id>/` (tree-preserving, re-hashed
  copies of divergent bytes + a `quarantine.json` index; created `0o700` — not user-browsable).
  Foreign/rogue whole trees (a planted `.builds/` dir, a hijacked `current`) are quarantined
  **by move**, so the bytes are preserved exactly and the sync's build-prune can never destroy
  them. The area inherits the mirror mount contract (writable only by the worker — see
  `nfs-root-squash-mirror-caveat.md`).
- The `current` symlink itself is verified against the vault-side build registry: a repointed,
  rolled-back, or replaced-by-a-directory `current` raises `MIRROR_TAMPER`
  (`classification: POINTER_DIVERGENT`) — treat it as a deliberate-action signal.
- **Never auto-deleted** — it is forensic evidence. Review `MIRROR_TAMPER` events before cleanup;
  the audit rows keep the path + both digests, so digest-level evidence survives deletion. Clean
  up manually once an investigation closes: `rm -rf <mirror_path>/.quarantine/<stamp>__<id>/`.

## Operator commands (inside the api/worker container)

- `python -m easysynq_api.cli.mirror scan` — detect/quarantine/audit only, NO rebuild (exit 1
  only on a scan infrastructure failure).
- `python -m easysynq_api.cli.mirror sync` — scan-first full reconcile (the correction).

A persistent stream of `FAILED` rows in `drift_scan` (with no CLEAN/DIVERGENT between) means the
scan itself cannot run (mount/permissions/DB) — investigate; the nightly sync remains the
convergence backstop.
```

and add to `docs/runbooks/00-index.md` (match its list style):
`- mirror-drift-scan.md — the D2+D3 integrity scan: cadence knob, quarantine area + cleanup, the CLI.`

- [ ] **Step 5: Spec amendments** — in the spec's §2 `drift_scan` counts cell and §6/§7, rename
  `rebuilt` → `rebuild_triggered` and add one sentence to §4/§6: "(Implementation refinement:
  `scan_mirror` is DB-read-only; `persist_scan_results` writes the events + summary row in one
  txn — same ordering posture.)"

- [ ] **Step 6: CLAUDE.md + slice-history** — add the S-drift-2 line to `## Recent learnings`
  (newest first, evict the oldest if over ~12) + update `## Current status`: S-drift-2 ✅ pending
  merge, **migration head `0046` (next `0047`)**; add the per-slice narrative to
  `docs/slice-history.md` following its existing entry format.

- [ ] **Step 7: Commit**

```bash
git add docs CLAUDE.md
git commit -m "docs(s-drift-2): close the D2/D3 seam — docs 05/12/14, drift-scan runbook, spec amendments, slice history"
```

---

## Final verification (after all tasks — the session rhythm, not plan steps)

1. `uv run pytest tests/unit/test_mirror.py tests/unit/test_mirror_scan.py tests/unit/test_mirror_scan_task_registration.py -v` + full static checks + `/check-migrations` + `/check-contracts` (no contract change expected — verify the diff touches no `openapi.yaml`).
2. `diff-critic` agent on the full branch diff.
3. Pre-merge live smoke (rebuild `migrate api worker beat` images first): exec into the worker,
   tamper a mirrored file + plant a stray, run `easysynq.mirror.scan`, verify `.quarantine/`,
   the `MIRROR_*` audit rows, the `drift_scan` row, and the corrected tree; re-scan CLEAN.
4. PR → green CI (9 checks) → address Codex threads → squash-merge.
