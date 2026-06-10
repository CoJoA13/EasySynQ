# S-drift-3 — D1 Blob Verify + D4 Superseded-Copies + Admin Drift-Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the drift family's detection legs: a daily rolling D1 blob re-hash that alarms `BLOB_INTEGRITY_FAILED`, a live D4 report of outstanding exported/printed copies of superseded versions, and a `drift.read`-gated admin status surface.

**Architecture:** Migration `0047` adds two additive enum values + the `drift.read` SYSTEM key (R38/R41) — no new tables. A new `services/vault/blob_verify.py` mirrors `mirror_scan`'s posture (never raises, FAILED-salvages, one persist txn, advisory-lock single-flight) and stamps `blob.verified_at` on OK only. A new read-only `services/vault/drift_report.py` aggregates the latest `drift_scan` per kind + the D4 join; `api/drift.py` exposes two GETs.

**Tech Stack:** FastAPI, SQLAlchemy 2 async, Alembic, Celery/Beat, boto3/MinIO, pytest (`asyncio_mode=auto`; unit tests auto-marked by directory).

**Spec:** `docs/superpowers/specs/2026-06-10-s-drift-3-blob-verify-drift-surface-design.md` (read it first).

**Branch:** `feat/s-drift-3-blob-verify` (already created; the spec is committed on it).

**⚠ This box (native Windows):** integration tests are Linux-CI-only (`-m integration` rejects the ProactorEventLoop) and the FULL `-m unit` suite hits a libmagic access violation in `test_ingestion_helpers.py` — but **targeted unit files run fine** (`uv run pytest tests/unit/test_blob_verify.py`). Local gates = targeted unit + ruff/format/mypy (`/check-api` static legs) + `/check-migrations` + `/check-contracts`. Integration tests are authored with their feature, validated by ruff/mypy locally and by Linux CI.

---

### Task 1: Migration 0047 + ORM enum members + catalog-count bump

**Files:**
- Modify: `apps/api/src/easysynq_api/db/models/_drift_enums.py`
- Modify: `apps/api/src/easysynq_api/db/models/_audit_enums.py`
- Create: `migrations/versions/0047_blob_verify_drift_read.py`
- Create: `apps/api/tests/unit/test_drift3_enums.py`
- Modify: `apps/api/tests/integration/test_authz.py:130-142`

- [ ] **Step 1: Write the failing unit test**

Create `apps/api/tests/unit/test_drift3_enums.py`:

```python
"""S-drift-3: the two additive enum members exist in the ORM (the migration's *_VALUES source)."""

from __future__ import annotations

from easysynq_api.db.models._audit_enums import EVENT_TYPE_VALUES, EventType
from easysynq_api.db.models._drift_enums import DRIFT_SCAN_KIND_VALUES, DriftScanKind


def test_blob_rehash_kind_member() -> None:
    assert DriftScanKind.BLOB_REHASH.value == "BLOB_REHASH"
    assert "BLOB_REHASH" in DRIFT_SCAN_KIND_VALUES


def test_blob_integrity_failed_event_member() -> None:
    assert EventType.BLOB_INTEGRITY_FAILED.value == "BLOB_INTEGRITY_FAILED"
    assert "BLOB_INTEGRITY_FAILED" in EVENT_TYPE_VALUES
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd apps/api && uv run pytest tests/unit/test_drift3_enums.py -v`
Expected: FAIL — `AttributeError: BLOB_REHASH`.

- [ ] **Step 3: Add the ORM members**

In `_drift_enums.py`, change the `DriftScanKind` class to:

```python
class DriftScanKind(enum.Enum):
    MIRROR = "MIRROR"
    # S-drift-3: the D1 blob integrity re-hash (doc 03 §8.2, doc 05 §9.1 row D1). Added via
    # ``ALTER TYPE drift_scan_kind ADD VALUE`` in 0047 (the additive pattern; a from-scratch
    # ``upgrade head`` rebuilds the type from DRIFT_SCAN_KIND_VALUES, so the member lives here too).
    BLOB_REHASH = "BLOB_REHASH"
```

In `_audit_enums.py`, append to the END of the `EventType` class (after `MIRROR_TAMPER`):

```python
    # S-drift-3 (doc 03 §8.2 / doc 05 §9.1 D1): the blob integrity verify alarm. ONE event type
    # (owner fork) — the classification rides after.classification (HASH_MISMATCH | OBJECT_MISSING
    # | READ_ERROR); every class is equally alarm-worthy (OBJECT_MISSING = storage tamper OR a
    # broken blob-row-iff-bytes invariant, never skippable). object_type=config keyed on the org
    # (a deduplicated blob has no single owning document); the after payload carries the sha256.
    # Added via ALTER TYPE event_type ADD VALUE in 0047 (the additive pattern; a from-scratch
    # ``upgrade head`` rebuilds from EVENT_TYPE_VALUES).
    BLOB_INTEGRITY_FAILED = "BLOB_INTEGRITY_FAILED"
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd apps/api && uv run pytest tests/unit/test_drift3_enums.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Write the migration**

Create `migrations/versions/0047_blob_verify_drift_read.py`:

```python
"""D1 blob verify + the admin drift surface: BLOB_REHASH kind + BLOB_INTEGRITY_FAILED + drift.read

Slice S-drift-3 (doc 03 §8.2, doc 05 §9.1 rows D1/D4, doc 07 §3.9, R38/R41) — no new tables:

1. **drift_scan_kind += BLOB_REHASH** — the S-drift-2 spec's declared seam (additive ADD VALUE,
   no-op downgrade; a from-scratch ``upgrade head`` rebuilds the type from the ORM
   DRIFT_SCAN_KIND_VALUES, which already carries the member).
2. **event_type += BLOB_INTEGRITY_FAILED** — the D1 mismatch alarm (one event type, owner fork;
   classification rides the payload).
3. **R38/R41: the drift.read SYSTEM-domain key** (is_system_domain=true, sod_sensitive=false,
   sig_hook=false, finest_scope=SYSTEM) + one role_grant to System Administrator. ⚠ Org lookup =
   the RESILIENT pattern (scalar_one_or_none on 'DEFAULT' + a fall-back to the only org — the
   0043/0045 recipe, PR #107): setup G-E renames the short_code, so a DEFAULT-only lookup (the
   0028 shape) silently skips an operational install. If the fallback finds ≠1 org, the grant is
   skipped (permission still seeded) — never abort the upgrade.

Neither new enum value is used by a row in THIS migration (the PG16 in-txn rule is satisfied).
Downgrade: role_grant rows BEFORE the permission row (the RESTRICT FK); the ADD VALUEs are
irreversible in PostgreSQL → no-op (0001/0046 drop the types wholesale, so up↔down still passes).

Revision ID: 0047_blob_verify_drift_read
Revises: 0046_mirror_drift_scan
Create Date: 2026-06-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

revision: str = "0047_blob_verify_drift_read"
down_revision: str | None = "0046_mirror_drift_scan"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_KEY = "drift.read"


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Additive enum values (IF NOT EXISTS → idempotent; not used by any row in this txn).
    op.execute("ALTER TYPE drift_scan_kind ADD VALUE IF NOT EXISTS 'BLOB_REHASH'")
    op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'BLOB_INTEGRITY_FAILED'")

    # 2. R38/R41: seed the drift.read SYSTEM key (idempotent).
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
                    "resource": "drift",
                    "action": "read",
                    "is_system_domain": True,
                    "sod_sensitive": False,
                    "sig_hook": False,
                    "finest_scope": "SYSTEM",
                }
            ]
        )
        .on_conflict_do_nothing(index_elements=["key"])
    )

    # 3. Grant to System Administrator — resilient org lookup (the 0043/0045 recipe, #107):
    # setup G-E renames the short_code, so DEFAULT-only would skip an operational install.
    org_id = bind.execute(
        sa.text("SELECT id FROM organization WHERE short_code = 'DEFAULT'")
    ).scalar_one_or_none()
    if org_id is None:
        org_rows = bind.execute(sa.text("SELECT id FROM organization")).fetchall()
        org_id = org_rows[0][0] if len(org_rows) == 1 else None
    if org_id is not None:
        perm_id = bind.execute(
            sa.text("SELECT id FROM permission WHERE key = :k"), {"k": _NEW_KEY}
        ).scalar_one()
        role_id = bind.execute(
            sa.text("SELECT id FROM role WHERE org_id = :o AND name = 'System Administrator'"),
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


def downgrade() -> None:
    bind = op.get_bind()
    # role_grant BEFORE permission (the RESTRICT FK) so a populated-DB downgrade does not abort.
    bind.execute(
        sa.text(
            "DELETE FROM role_grant WHERE permission_id IN "
            "(SELECT id FROM permission WHERE key = :k)"
        ),
        {"k": _NEW_KEY},
    )
    bind.execute(sa.text("DELETE FROM permission WHERE key = :k"), {"k": _NEW_KEY})
    # The two ADD VALUEs are irreversible in PostgreSQL → no-op (0001/0046 DROP the types
    # wholesale, so the up↔down round-trip still passes; a re-upgrade rebuilds from the ORM
    # *_VALUES).
```

- [ ] **Step 6: Bump the catalog-count assertion**

In `apps/api/tests/integration/test_authz.py`, replace lines 131-132:

```python
    # 96 closed v1 keys + the 2 additive retention.* keys opened in 0028 (R38).
    assert len(perms) == 98
```

with:

```python
    # 96 closed v1 keys + the 2 additive retention.* keys (0028) + drift.read (0047) — R38.
    assert len(perms) == 99
```

and after the existing `retention.*` assertion block (after line 142), add:

```python
    # R38/R41: drift.read is SYSTEM-domain (admin-side operational read), non-sig-hook.
    assert by_key["drift.read"]["is_system_domain"] is True
    assert by_key["drift.read"]["sig_hook"] is False
    assert by_key["drift.read"]["sod_sensitive"] is False
```

- [ ] **Step 7: Run the migrations round-trip**

Run: the `/check-migrations` skill (round-trips `alembic upgrade head` ↔ `downgrade` ↔ `alembic check` on a throwaway PG16).
Expected: PASS (clean `alembic check`; no phantom diffs — no new tables/models in this slice).

- [ ] **Step 8: Commit**

```bash
git add apps/api/src/easysynq_api/db/models/_drift_enums.py \
        apps/api/src/easysynq_api/db/models/_audit_enums.py \
        migrations/versions/0047_blob_verify_drift_read.py \
        apps/api/tests/unit/test_drift3_enums.py \
        apps/api/tests/integration/test_authz.py
git commit -m "feat(s-drift-3): mig 0047 — BLOB_REHASH kind + BLOB_INTEGRITY_FAILED + drift.read (R38/R41)"
```

---

### Task 2: `storage.hash_object` — the streaming re-hash read

**Files:**
- Modify: `apps/api/src/easysynq_api/services/vault/storage.py` (after `stream_object`, ~line 206)
- Create: `apps/api/tests/unit/test_storage_hash_object.py`

- [ ] **Step 1: Write the failing test**

Create `apps/api/tests/unit/test_storage_hash_object.py`:

```python
"""S-drift-3: hash_object streams sha256 in bounded chunks via the INTERNAL client (never presign)."""

from __future__ import annotations

import hashlib
import io
from typing import Any

import pytest

from easysynq_api.services.vault import storage


class _FakeBody:
    def __init__(self, data: bytes) -> None:
        self._io = io.BytesIO(data)
        self.reads: list[int] = []
        self.closed = False

    def read(self, n: int = -1) -> bytes:
        self.reads.append(n)
        return self._io.read(n)

    def close(self) -> None:
        self.closed = True


async def test_hash_object_streams_and_matches_sha256(monkeypatch: pytest.MonkeyPatch) -> None:
    data = b"\x01\x02" * (3 * 1024 * 1024 // 2) + b"tail"  # >3 MiB → multiple 1 MiB chunks
    body = _FakeBody(data)

    class _FakeClient:
        def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803 — boto3 API
            assert (Bucket, Key) == ("docs", "ab/cd/key")
            return {"Body": body}

    monkeypatch.setattr(storage, "_client", lambda: _FakeClient())
    digest = await storage.hash_object("ab/cd/key", bucket="docs")
    assert digest == hashlib.sha256(data).hexdigest()
    # Bounded memory: every read was chunk-sized, and the body was closed.
    assert all(n == 1 << 20 for n in body.reads)
    assert body.closed
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd apps/api && uv run pytest tests/unit/test_storage_hash_object.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'hash_object'`.

- [ ] **Step 3: Implement `hash_object`**

In `apps/api/src/easysynq_api/services/vault/storage.py`, directly after the `stream_object` function, add (`hashlib` is needed — check the imports at the top of the file and add `import hashlib` to the stdlib block if absent):

```python
def _hash_object_sync(object_key: str, bucket: str) -> str:
    digest = hashlib.sha256()
    body = _client().get_object(Bucket=bucket, Key=object_key)["Body"]
    try:
        while True:
            chunk: bytes = body.read(_STREAM_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    finally:
        body.close()
    return digest.hexdigest()


async def hash_object(object_key: str, *, bucket: str | None = None) -> str:
    """Stream-hash a blob's bytes server-side (the S-drift-3 D1 verify read): sha256 over 1 MiB
    chunks — bounded memory unlike :func:`fetch_bytes`, which materialises the whole object. The
    **internal** worker path (D1 reads bytes directly, never presigns). WORM object-lock blocks
    writes/deletes, not GETs."""
    return await asyncio.to_thread(_hash_object_sync, object_key, bucket or _doc_bucket())
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd apps/api && uv run pytest tests/unit/test_storage_hash_object.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/easysynq_api/services/vault/storage.py \
        apps/api/tests/unit/test_storage_hash_object.py
git commit -m "feat(s-drift-3): storage.hash_object — chunked sha256 over the internal client"
```

---

### Task 3: The D1 scanner core — `services/vault/blob_verify.py`

**Files:**
- Create: `apps/api/src/easysynq_api/services/vault/blob_verify.py`
- Create: `apps/api/tests/unit/test_blob_verify.py`

- [ ] **Step 1: Write the failing unit tests (the classification matrix + report semantics)**

Create `apps/api/tests/unit/test_blob_verify.py`:

```python
"""S-drift-3 unit proofs — the D1 classification matrix, salvage-on-abort, and report semantics.

The hasher is injected (no MinIO): a mapping-backed fake returns digests; botocore exceptions are
constructed for the error rows. ``verify_rows`` NEVER raises — an infrastructure-class failure
aborts with (findings-so-far, ok-so-far, error) so the caller reports an honest FAILED that
salvages what was collected (MinIO-down must not mint hundreds of noise findings).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from botocore.exceptions import ClientError, EndpointConnectionError

from easysynq_api.services.vault.blob_verify import (
    CLASS_MISMATCH,
    CLASS_MISSING,
    CLASS_READ_ERROR,
    build_report,
    verify_rows,
)

Row = tuple[str, str, str, int]
_B = "documents"


def _row(sha: str, key: str = "k", size: int = 4) -> Row:
    return (sha, _B, key, size)


def _hasher(mapping: dict[str, str | Exception]) -> Callable[[str, str], Awaitable[str]]:
    async def h(object_key: str, bucket: str) -> str:
        outcome = mapping[object_key]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return h


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code}}, "GetObject")


async def test_matching_digest_is_ok_and_stamped() -> None:
    findings, ok, error = await verify_rows([_row("aa")], _hasher({"k": "aa"}))
    assert (findings, ok, error) == ([], ["aa"], None)


async def test_mismatch_classifies_and_carries_found_digest() -> None:
    findings, ok, error = await verify_rows([_row("aa")], _hasher({"k": "bb"}))
    assert error is None and ok == []
    assert findings[0].classification == CLASS_MISMATCH
    assert findings[0].found_sha256 == "bb"
    assert findings[0].sha256 == "aa"


async def test_nosuchkey_is_object_missing_never_skipped() -> None:
    findings, ok, error = await verify_rows([_row("aa")], _hasher({"k": _client_error("NoSuchKey")}))
    assert error is None
    assert findings[0].classification == CLASS_MISSING


async def test_object_scoped_client_error_is_read_error_finding() -> None:
    findings, ok, error = await verify_rows(
        [_row("aa")], _hasher({"k": _client_error("AccessDenied")})
    )
    assert error is None
    assert findings[0].classification == CLASS_READ_ERROR
    assert findings[0].note == "AccessDenied"


async def test_connection_failure_aborts_and_salvages() -> None:
    """Row 1 mismatches (collected), row 2 hits a connection-class error → abort: the report is
    FAILED, the mismatch finding survives, and row 3 is NEVER reached (no noise findings)."""
    reached: list[str] = []

    async def h(object_key: str, bucket: str) -> str:
        reached.append(object_key)
        if object_key == "k1":
            return "zz"
        raise EndpointConnectionError(endpoint_url="http://minio:9000")

    rows = [_row("aa", "k1"), _row("bb", "k2"), _row("cc", "k3")]
    findings, ok, error = await verify_rows(rows, h)
    assert reached == ["k1", "k2"]
    assert [f.classification for f in findings] == [CLASS_MISMATCH]
    assert ok == [] and error is not None


async def test_unexpected_exception_also_aborts_not_raises() -> None:
    findings, ok, error = await verify_rows([_row("aa")], _hasher({"k": RuntimeError("boom")}))
    assert findings == [] and ok == []
    assert error is not None and "RuntimeError" in error


async def test_build_report_statuses_and_counts() -> None:
    clean = build_report(findings=[], ok_shas=["a", "b"], total_blobs=9, sample_limit=2)
    assert clean.status == "CLEAN"
    assert clean.counts()["ok"] == 2
    assert clean.counts()["stamped"] == 2
    assert clean.counts()["full"] is False

    findings, ok, _ = await verify_rows([_row("aa")], _hasher({"k": "bb"}))
    divergent = build_report(findings=findings, ok_shas=ok, total_blobs=9, sample_limit=None)
    assert divergent.status == "DIVERGENT"
    c = divergent.counts()
    assert c["mismatched"] == 1 and c["full"] is True and c["scanned"] == 1

    failed = build_report(findings=findings, ok_shas=[], total_blobs=9, sample_limit=5, error="x")
    assert failed.status == "FAILED"
    assert failed.counts()["error"] == "x"


def test_report_is_failed_even_with_zero_findings_when_error() -> None:
    r = build_report(findings=[], ok_shas=[], total_blobs=0, sample_limit=None, error="pg down")
    assert r.status == "FAILED"


def test_sample_stmt_orders_nulls_first_then_oldest() -> None:
    """The rotation contract lives in the SQL: never-verified rows first, then oldest stamps,
    deterministic sha tiebreak — compiled against the postgresql dialect so the assertion checks
    what PG will actually execute."""
    from sqlalchemy.dialects import postgresql

    from easysynq_api.services.vault.blob_verify import _sample_stmt

    sql = str(_sample_stmt(limit=5).compile(dialect=postgresql.dialect()))
    assert "ORDER BY blob.verified_at ASC NULLS FIRST, blob.sha256" in sql
    assert "LIMIT" in sql
    full_sql = str(_sample_stmt(limit=None).compile(dialect=postgresql.dialect()))
    assert "LIMIT" not in full_sql
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd apps/api && uv run pytest tests/unit/test_blob_verify.py -v`
Expected: FAIL — `ModuleNotFoundError: ... blob_verify`.

- [ ] **Step 3: Implement the scanner core**

Create `apps/api/src/easysynq_api/services/vault/blob_verify.py`:

```python
"""The D1 blob integrity verify (S-drift-3, doc 03 §8.2, doc 05 §9.1 row D1).

Re-hash vault blobs against their content-addressed identity (``blob.sha256`` IS the PK) and alarm
on divergence — the only detector for bit-rot, storage-layer tamper, or a broken
blob-row-iff-bytes invariant. **Rolling sample:** each run verifies the K least-recently-verified
rows (``verified_at NULLS FIRST → oldest``), so rotation provably covers the FULL set every ⌈N/K⌉
runs (doc 03 §8.2's "rolling sample + full set periodically"; ``full=True`` is the on-demand
complete pass).

**Stamp-on-OK-only is load-bearing:** a finding leaves the blob at the rotation head, so every
subsequent scan re-detects and re-alarms until the operator restores the object — there is no
auto-correction here (unlike the mirror's vault-wins rebuild), and stamping a bad blob would let
the next run's clean sample mask an unresolved corruption as CLEAN on the latest-per-kind status
read. A transient READ_ERROR self-clears the same way (unstamped → re-verified next run).

Posture mirrors ``mirror_scan``: the scan NEVER raises — an object-scoped error is a finding, an
infrastructure-class failure (MinIO/PG down) is an honest FAILED report that salvages the findings
collected so far and mints NO noise findings for unreached rows. ``persist_blob_verify`` writes
the per-finding ``BLOB_INTEGRITY_FAILED`` audit events (``object_type=config`` keyed on the org —
a deduplicated blob has no single owning document; the ``after`` payload carries the sha256), the
``verified_at`` stamps, and the ``drift_scan`` ``kind=BLOB_REHASH`` summary row in ONE
transaction — a persist failure stamps nothing, so the next run redoes the same sample
(self-healing, no ledger). Reads go through the INTERNAL client (``storage.hash_object``) — never
presign (D1 is a worker read, not a browser read).
"""

from __future__ import annotations

import dataclasses
import datetime
import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from botocore.exceptions import ClientError
from sqlalchemy import Select, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...db.models._audit_enums import ActorType, AuditObjectType, EventType
from ...db.models._drift_enums import DriftScanKind, DriftScanStatus
from ...db.models.audit_event import AuditEvent
from ...db.models.blob import Blob
from ...db.models.drift_scan import DriftScan
from ..common.org import get_single_org_id
from . import storage

logger = logging.getLogger("easysynq.vault")

# The D1 classification set (one BLOB_INTEGRITY_FAILED event type — owner fork; the classification
# rides the audit payload). All three are equally alarm-worthy.
CLASS_MISMATCH = "HASH_MISMATCH"
CLASS_MISSING = "OBJECT_MISSING"
CLASS_READ_ERROR = "READ_ERROR"

# S3 error codes that mean "the object is gone" (tamper or a broken blob-row-iff-bytes invariant).
_MISSING_CODES = frozenset({"NoSuchKey", "404", "NoSuchBucket"})

Hasher = Callable[[str, str], Awaitable[str]]


@dataclasses.dataclass(frozen=True, slots=True)
class BlobFinding:
    sha256: str
    bucket: str
    object_key: str
    size_bytes: int
    classification: str  # CLASS_MISMATCH | CLASS_MISSING | CLASS_READ_ERROR
    found_sha256: str | None = None
    note: str | None = None


@dataclasses.dataclass
class BlobVerifyReport:
    scan_id: uuid.UUID
    started_at: datetime.datetime
    status: str  # CLEAN | DIVERGENT | FAILED
    findings: list[BlobFinding]
    ok_shas: list[str]
    total_blobs: int
    sample_limit: int | None  # None = the full set
    error: str | None = None

    def counts(self) -> dict[str, object]:
        by = {CLASS_MISMATCH: 0, CLASS_MISSING: 0, CLASS_READ_ERROR: 0}
        for f in self.findings:
            by[f.classification] += 1
        out: dict[str, object] = {
            "scanned": len(self.ok_shas) + len(self.findings),
            "ok": len(self.ok_shas),
            "mismatched": by[CLASS_MISMATCH],
            "missing": by[CLASS_MISSING],
            "read_errors": by[CLASS_READ_ERROR],
            "stamped": len(self.ok_shas),
            "total_blobs": self.total_blobs,
            "sample_limit": self.sample_limit,
            "full": self.sample_limit is None,
            "scan_id": str(self.scan_id),
        }
        if self.error:
            out["error"] = self.error
        return out


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


async def _default_hasher(object_key: str, bucket: str) -> str:
    return await storage.hash_object(object_key, bucket=bucket)


def build_report(
    *,
    findings: list[BlobFinding],
    ok_shas: list[str],
    total_blobs: int,
    sample_limit: int | None,
    error: str | None = None,
    scan_id: uuid.UUID | None = None,
    started_at: datetime.datetime | None = None,
) -> BlobVerifyReport:
    """FAILED beats DIVERGENT beats CLEAN: an aborted pass is never reported clean, and salvaged
    findings ride the FAILED report (they were real observations)."""
    status = "FAILED" if error else ("DIVERGENT" if findings else "CLEAN")
    return BlobVerifyReport(
        scan_id=scan_id or uuid.uuid4(),
        started_at=started_at or _now(),
        status=status,
        findings=findings,
        ok_shas=ok_shas,
        total_blobs=total_blobs,
        sample_limit=sample_limit,
        error=error,
    )


async def verify_rows(
    rows: Sequence[tuple[str, str, str, int]],
    hasher: Hasher,
) -> tuple[list[BlobFinding], list[str], str | None]:
    """Hash each ``(sha256, bucket, object_key, size_bytes)`` row. Returns
    ``(findings, ok_shas, error)``; a non-None error means an infrastructure-class failure aborted
    the pass (remaining rows NOT reached — no noise findings for them) and the caller reports
    FAILED, salvaging what was collected. NEVER raises."""
    findings: list[BlobFinding] = []
    ok: list[str] = []
    for sha, bucket, key, size in rows:
        try:
            found = await hasher(key, bucket)
        except ClientError as exc:
            code = str((exc.response.get("Error") or {}).get("Code", ""))
            classification = CLASS_MISSING if code in _MISSING_CODES else CLASS_READ_ERROR
            findings.append(
                BlobFinding(
                    sha, bucket, key, size, classification, note=code or type(exc).__name__
                )
            )
            continue
        except Exception as exc:  # noqa: BLE001 — connection-class/unexpected: abort + salvage
            return findings, ok, f"{type(exc).__name__}: {exc}"
        if found != sha:
            findings.append(
                BlobFinding(sha, bucket, key, size, CLASS_MISMATCH, found_sha256=found)
            )
        else:
            ok.append(sha)
    return findings, ok, None


def _sample_stmt(*, limit: int | None) -> Select[Any]:
    """The rotation sample: never-verified rows first (NULLS FIRST), then the oldest stamps, with
    a deterministic sha tiebreak. Column-select, never entities (identity-map hygiene). ``None``
    = the full set."""
    stmt = select(Blob.sha256, Blob.bucket, Blob.object_key, Blob.size_bytes).order_by(
        Blob.verified_at.asc().nulls_first(), Blob.sha256
    )
    return stmt if limit is None else stmt.limit(limit)


async def verify_blobs(
    session: AsyncSession,
    *,
    sample_size: int | None = None,
    full: bool = False,
    hasher: Hasher | None = None,
) -> BlobVerifyReport:
    """The D1 scan: select the rotation sample and re-hash it. DB-read-only;
    ``persist_blob_verify`` is the single writer. NEVER raises (an SQL/infra failure → an honest
    FAILED report)."""
    started = _now()
    scan_id = uuid.uuid4()
    limit: int | None = None
    if not full:
        limit = sample_size if sample_size is not None else get_settings().blob_verify_sample_size
    try:
        total = (await session.execute(select(func.count()).select_from(Blob))).scalar_one()
        rows = [
            (str(r[0]), str(r[1]), str(r[2]), int(r[3]))
            for r in (await session.execute(_sample_stmt(limit=limit))).all()
        ]
        findings, ok, error = await verify_rows(rows, hasher or _default_hasher)
        return build_report(
            findings=findings,
            ok_shas=ok,
            total_blobs=total,
            sample_limit=limit,
            error=error,
            scan_id=scan_id,
            started_at=started,
        )
    except Exception as exc:  # noqa: BLE001 — the scan never raises (the mirror_scan posture)
        logger.exception("blob.verify: scan infrastructure failure")
        return build_report(
            findings=[],
            ok_shas=[],
            total_blobs=0,
            sample_limit=limit,
            error=f"{type(exc).__name__}: {exc}",
            scan_id=scan_id,
            started_at=started,
        )


async def persist_blob_verify(
    session: AsyncSession, report: BlobVerifyReport, *, triggered_by: str
) -> bool:
    """ONE txn: a ``BLOB_INTEGRITY_FAILED`` audit event per finding + the verified_at stamps
    (OK rows only) + the ``drift_scan`` BLOB_REHASH summary row. Returns success: a failure is
    logged, never raised, and stamps nothing — the next run redoes the same sample (self-healing).
    NO per-clean-scan audit event (the hourly-CLEAN-spam rule); EVERY scan gets its summary row
    (the row-per-scan contract)."""
    if report.status == "FAILED":
        await session.rollback()  # the failed scan may have poisoned the txn
    try:
        org_id = await get_single_org_id(session)
        if org_id is None:
            logger.warning("blob.verify: no organization yet; results not persisted")
            return False
        finished_at = _now()
        for f in report.findings:
            after: dict[str, object] = {
                "sha256": f.sha256,
                "bucket": f.bucket,
                "object_key": f.object_key,
                "classification": f.classification,
                "found_sha256": f.found_sha256,
                "size_bytes": f.size_bytes,
                "scan_id": str(report.scan_id),
            }
            if f.note:
                after["note"] = f.note
            session.add(
                AuditEvent(
                    org_id=org_id,
                    occurred_at=finished_at,
                    actor_id=None,
                    actor_type=ActorType.system,
                    event_type=EventType.BLOB_INTEGRITY_FAILED,
                    object_type=AuditObjectType.config,
                    object_id=org_id,
                    after=after,
                )
            )
        if report.ok_shas:
            await session.execute(
                update(Blob).where(Blob.sha256.in_(report.ok_shas)).values(verified_at=func.now())
            )
        session.add(
            DriftScan(
                org_id=org_id,
                kind=DriftScanKind.BLOB_REHASH,
                started_at=report.started_at,
                finished_at=finished_at,
                status=DriftScanStatus(report.status),
                counts=report.counts(),
                triggered_by=triggered_by,
            )
        )
        await session.commit()
        return True
    except Exception:  # noqa: BLE001 — persistence must never raise into the pipeline
        logger.exception("blob.verify: failed to persist results")
        await session.rollback()
        return False
```

- [ ] **Step 4: Run the unit tests to verify they pass**

Run: `cd apps/api && uv run pytest tests/unit/test_blob_verify.py -v`
Expected: all PASS.

- [ ] **Step 5: Static checks**

Run: `cd apps/api && uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: clean. (Fix any line-length/typing nits before committing.)

- [ ] **Step 6: Commit**

```bash
git add apps/api/src/easysynq_api/services/vault/blob_verify.py \
        apps/api/tests/unit/test_blob_verify.py
git commit -m "feat(s-drift-3): D1 scanner core — rolling re-hash, stamp-on-OK-only, FAILED-salvage"
```

---

### Task 4: D1 integration proofs (Linux-CI)

**Files:**
- Create: `apps/api/tests/integration/test_blob_verify.py`

- [ ] **Step 1: Author the integration tests**

Create `apps/api/tests/integration/test_blob_verify.py`. These cannot run on this box (Linux-CI-only) — validate with ruff/mypy locally; CI proves them.

```python
"""S-drift-3 integration proofs — D1 blob verify end-to-end against the real vault + MinIO.

Synthetic tamper via planted blob ROWS (never fight WORM object-lock): a row whose sha256 doesn't
match the real bytes it points at → HASH_MISMATCH; a row pointing at a nonexistent key →
OBJECT_MISSING. ⚠ Run-scoped/delta assertions only (the shared session DB): every audit/drift_scan
lookup keys on THIS scan's scan_id; planted rows are run-unique and DELETED in finally (a leaked
plant would fail other runs' clean passes). SoD-2: releases come from the approver, never the
author.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select

from easysynq_api.db.models._audit_enums import EventType
from easysynq_api.db.models._drift_enums import DriftScanKind, DriftScanStatus
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.blob import Blob
from easysynq_api.db.models.document_version import DocumentVersion
from easysynq_api.db.models.drift_scan import DriftScan
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.vault.blob_verify import (
    CLASS_MISMATCH,
    CLASS_MISSING,
    persist_blob_verify,
    verify_blobs,
)

from . import s5_helpers as s5
from .test_mirror import _grant_release_actors
from .test_vault import _auth

pytestmark = pytest.mark.integration


@pytest.fixture
def subj() -> SimpleNamespace:
    salt = uuid.uuid4().hex[:10]
    return SimpleNamespace(a=f"kc-author-{salt}", b=f"kc-approver-{salt}", salt=salt)


async def _source_blob_of(document_id: str) -> Blob:
    async with get_sessionmaker()() as s:
        v1 = (
            await s.execute(
                select(DocumentVersion)
                .where(DocumentVersion.document_id == uuid.UUID(document_id))
                .order_by(DocumentVersion.version_seq)
                .limit(1)
            )
        ).scalar_one()
        return (
            await s.execute(select(Blob).where(Blob.sha256 == v1.source_blob_sha256))
        ).scalar_one()


async def _events_for_scan(scan_id: uuid.UUID) -> list[AuditEvent]:
    async with get_sessionmaker()() as s:
        return list(
            (
                await s.execute(
                    select(AuditEvent).where(AuditEvent.after["scan_id"].astext == str(scan_id))
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


async def test_clean_pass_stamps_verified_at_and_writes_clean_row(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
) -> None:
    """A legit blob hashes clean → verified_at stamped, NO audit event, a CLEAN summary row."""
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    doc = await s5.drive_to_effective(
        app_client, ha, hb, hb, await s5.type_id("SOP"), f"D1-CLEAN-{subj.salt}".encode()
    )
    real = await _source_blob_of(doc["id"])
    assert real.verified_at is None  # never verified yet

    async with get_sessionmaker()() as s:
        report = await verify_blobs(s, full=True)
        assert await persist_blob_verify(s, report, triggered_by="cli") is True

    # Run-scoped: OUR blob was stamped and is not a finding; no event carries our sha.
    assert real.sha256 in report.ok_shas
    assert not [f for f in report.findings if f.sha256 == real.sha256]
    async with get_sessionmaker()() as s:
        stamped = (
            await s.execute(select(Blob.verified_at).where(Blob.sha256 == real.sha256))
        ).scalar_one()
    assert stamped is not None
    row = await _scan_row(report.scan_id)
    assert row is not None and row.kind is DriftScanKind.BLOB_REHASH
    assert row.triggered_by == "cli"
    events = await _events_for_scan(report.scan_id)
    assert not [e for e in events if (e.after or {}).get("sha256") == real.sha256]


async def test_planted_tamper_alarms_and_realarm_until_resolved(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
) -> None:
    """A wrong-sha row over real bytes → HASH_MISMATCH; a row with no object → OBJECT_MISSING.
    Both alarm BLOB_INTEGRITY_FAILED, stay UNSTAMPED, and re-alarm on the next scan (the
    persistent-alarm contract — no auto-correction exists for blobs)."""
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    doc = await s5.drive_to_effective(
        app_client, ha, hb, hb, await s5.type_id("SOP"), f"D1-TAMPER-{subj.salt}".encode()
    )
    real = await _source_blob_of(doc["id"])
    fake_sha = hashlib.sha256(f"planted-mismatch-{subj.salt}".encode()).hexdigest()
    missing_sha = hashlib.sha256(f"planted-missing-{subj.salt}".encode()).hexdigest()

    async with get_sessionmaker()() as s:
        s.add(
            Blob(
                sha256=fake_sha,
                org_id=real.org_id,
                size_bytes=real.size_bytes,
                mime_type="application/octet-stream",
                bucket=real.bucket,
                object_key=real.object_key,  # real bytes, wrong claimed digest → MISMATCH
            )
        )
        s.add(
            Blob(
                sha256=missing_sha,
                org_id=real.org_id,
                size_bytes=3,
                mime_type="application/octet-stream",
                bucket=real.bucket,
                object_key=f"nonexistent/{subj.salt}",  # no bytes → MISSING
            )
        )
        await s.commit()

    try:
        async with get_sessionmaker()() as s:
            report = await verify_blobs(s, full=True)
            assert await persist_blob_verify(s, report, triggered_by="beat") is True

        mine = {f.sha256: f for f in report.findings if f.sha256 in (fake_sha, missing_sha)}
        assert mine[fake_sha].classification == CLASS_MISMATCH
        assert mine[fake_sha].found_sha256 == real.sha256  # the real bytes' actual digest
        assert mine[missing_sha].classification == CLASS_MISSING
        assert report.status == "DIVERGENT"

        events = await _events_for_scan(report.scan_id)
        by_sha = {(e.after or {}).get("sha256"): e for e in events}
        assert by_sha[fake_sha].event_type is EventType.BLOB_INTEGRITY_FAILED
        assert by_sha[missing_sha].event_type is EventType.BLOB_INTEGRITY_FAILED
        assert (by_sha[fake_sha].after or {})["classification"] == CLASS_MISMATCH

        row = await _scan_row(report.scan_id)
        assert row is not None and row.status is DriftScanStatus.DIVERGENT

        # Findings are NOT stamped → still at the rotation head → the NEXT scan re-alarms.
        async with get_sessionmaker()() as s:
            stamps = (
                await s.execute(
                    select(Blob.sha256, Blob.verified_at).where(
                        Blob.sha256.in_([fake_sha, missing_sha])
                    )
                )
            ).all()
        assert all(v is None for _, v in stamps)

        async with get_sessionmaker()() as s:
            report2 = await verify_blobs(s, full=True)
            assert await persist_blob_verify(s, report2, triggered_by="beat") is True
        again = {f.sha256 for f in report2.findings}
        assert {fake_sha, missing_sha} <= again
        assert len(await _events_for_scan(report2.scan_id)) >= 2  # re-audited under a NEW scan id
    finally:
        async with get_sessionmaker()() as s:
            await s.execute(delete(Blob).where(Blob.sha256.in_([fake_sha, missing_sha])))
            await s.commit()


async def test_rolling_sample_orders_by_verified_at_nulls_first(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
) -> None:
    """sample_size=0 → empty sample (CLEAN, nothing scanned) proves the LIMIT is honored end to
    end; the NULLS-FIRST/oldest ordering contract is proven by the compiled-SQL unit test
    (test_sample_stmt_orders_nulls_first_then_oldest) — a shared-DB ordering assertion would race
    other tests' rows."""
    async with get_sessionmaker()() as s:
        report = await verify_blobs(s, sample_size=0)
    assert report.status == "CLEAN"
    assert report.counts()["scanned"] == 0
```

- [ ] **Step 2: Validate statically (cannot run integration on this box)**

Run: `cd apps/api && uv run ruff check tests/integration/test_blob_verify.py && uv run ruff format --check tests/integration/test_blob_verify.py`
Expected: clean. (Linux CI runs the test itself.)

- [ ] **Step 3: Commit**

```bash
git add apps/api/tests/integration/test_blob_verify.py
git commit -m "test(s-drift-3): D1 integration proofs — planted-row tamper, re-alarm, clean stamping"
```

---

### Task 5: Settings knobs + advisory lock + Beat task + registration tests

**Files:**
- Modify: `apps/api/src/easysynq_api/config.py` (after `mirror_scan_interval_seconds`, ~line 91)
- Modify: `apps/api/src/easysynq_api/services/common/pg_locks.py` (~line 33)
- Create: `apps/api/src/easysynq_api/tasks/blob_verify.py`
- Modify: `apps/api/src/easysynq_api/tasks/app.py` (the `beat_schedule` dict)
- Modify: `apps/api/src/easysynq_api/tasks/__init__.py`
- Create: `apps/api/tests/unit/test_blob_verify_task_registration.py`

- [ ] **Step 1: Write the failing registration test**

Create `apps/api/tests/unit/test_blob_verify_task_registration.py` (the `test_mirror_scan_task_registration.py` convention):

```python
"""S-drift-3: the daily D1 verify task is registered + its Beat entry rides the settings knobs."""

from __future__ import annotations

from easysynq_api.config import Settings, get_settings
from easysynq_api.services.common.pg_locks import LOCK_BLOB_VERIFY, LOCK_MIRROR_SYNC
from easysynq_api.tasks import app


def test_verify_task_registered() -> None:
    assert "easysynq.blob.verify" in app.tasks


def test_beat_entry_schedule_matches_settings() -> None:
    entry = app.conf.beat_schedule["blob-verify"]
    assert entry["task"] == "easysynq.blob.verify"
    assert entry["schedule"] == float(get_settings().blob_verify_interval_seconds)


def test_default_knobs() -> None:
    assert Settings.model_fields["blob_verify_interval_seconds"].default == 86400
    assert Settings.model_fields["blob_verify_sample_size"].default == 500


def test_lock_is_distinct_from_mirror_sync() -> None:
    # Blob verify never touches the mirror; sharing LOCK_MIRROR_SYNC would couple unrelated
    # cadences (an hourly mirror scan starving the daily verify and vice versa).
    assert LOCK_BLOB_VERIFY != LOCK_MIRROR_SYNC
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd apps/api && uv run pytest tests/unit/test_blob_verify_task_registration.py -v`
Expected: FAIL — `ImportError: cannot import name 'LOCK_BLOB_VERIFY'`.

- [ ] **Step 3: Add the knobs, the lock, the task, the Beat entry, the registration**

In `config.py`, directly after the `mirror_scan_interval_seconds: int = 3600` line, add:

```python
    # S-drift-3: the D1 blob integrity verify (doc 03 §8.2 / doc 05 §9.1 D1) — a daily rolling
    # re-hash of the K least-recently-verified blobs; rotation provably covers the FULL set every
    # ⌈N/K⌉ days (a separate full-set schedule is deliberately NOT needed; CLI --full on demand).
    blob_verify_interval_seconds: int = 86400
    blob_verify_sample_size: int = 500
```

In `pg_locks.py`, after the `LOCK_REVIEW_SWEEP = 7710006` line (keep the trailing S-ing-5 comment
below it intact), add:

```python
# S-drift-3: serialize the daily D1 blob re-hash — one rotation pass at a time (a second
# concurrent verify skips its tick). DISTINCT from LOCK_MIRROR_SYNC: blob verify never touches
# the mirror, and sharing would couple unrelated cadences.
LOCK_BLOB_VERIFY = 7710007
```

Create `apps/api/src/easysynq_api/tasks/blob_verify.py`:

```python
"""Celery/Beat task for the D1 blob integrity verify (S-drift-3, doc 03 §8.2, doc 05 §9.1 D1).

Daily rolling re-hash of the K least-recently-verified vault blobs against their sha256 PK.
Stamp-on-OK-only: a finding re-alarms every run until the operator restores the object (there is
no auto-correction for blobs — restore-from-backup is the runbook action). Single-flight under
``LOCK_BLOB_VERIFY`` (skip-if-held); own disposed async engine per ``asyncio.run`` (the app's
non-owner role); the scan itself NEVER raises (an infra failure is an honest FAILED summary row).
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import get_settings
from ..services.common.pg_locks import LOCK_BLOB_VERIFY, pg_advisory_lock
from ..services.vault.blob_verify import persist_blob_verify, verify_blobs
from .app import app

logger = logging.getLogger("easysynq.blob.tasks")


async def _run_blob_verify() -> dict[str, object]:
    """The rolling D1 pass under the advisory lock; returns the summary counts (or a skip marker
    when another verify holds the lock)."""
    engine = create_async_engine(get_settings().database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session, pg_advisory_lock(session, LOCK_BLOB_VERIFY) as held:
            if not held:
                logger.info("blob.verify: another verify holds the lock; skipping this tick")
                return {"skipped_lock_held": 1}
            report = await verify_blobs(session)
            persisted = await persist_blob_verify(session, report, triggered_by="beat")
            summary: dict[str, object] = {**report.counts(), "persisted": persisted}
            logger.info("blob.verify.done", extra={"extra_fields": summary})
            return summary
    finally:
        await engine.dispose()


@app.task(name="easysynq.blob.verify")  # type: ignore[untyped-decorator]
def blob_verify() -> dict[str, object]:
    """Daily D1 rolling blob re-hash (doc 03 §8.2): stamps verified_at on OK, alarms on findings."""
    return asyncio.run(_run_blob_verify())
```

In `tasks/app.py`, add to the `beat_schedule` dict (after the `"mirror-scan"` entry):

```python
        # S-drift-3: the D1 blob integrity verify (doc 03 §8.2 / doc 05 §9.1 D1) — a daily
        # rolling re-hash of the least-recently-verified blobs (BLOB_VERIFY_INTERVAL_SECONDS,
        # default daily; sample size BLOB_VERIFY_SAMPLE_SIZE, default 500 → full coverage every
        # ⌈N/500⌉ days by rotation).
        "blob-verify": {
            "task": "easysynq.blob.verify",
            "schedule": float(_settings.blob_verify_interval_seconds),
        },
```

In `tasks/__init__.py`, add `blob_verify` to the registration import (alphabetical):

```python
from . import (  # noqa: F401  (registers the Celery tasks)
    audit,
    backup,
    blob_verify,
    ingestion,
    lifecycle,
    mirror,
    packs,
    records,
    review,
    visual_diff,
)
```

- [ ] **Step 4: Run the registration tests to verify they pass**

Run: `cd apps/api && uv run pytest tests/unit/test_blob_verify_task_registration.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/easysynq_api/config.py \
        apps/api/src/easysynq_api/services/common/pg_locks.py \
        apps/api/src/easysynq_api/tasks/blob_verify.py \
        apps/api/src/easysynq_api/tasks/app.py \
        apps/api/src/easysynq_api/tasks/__init__.py \
        apps/api/tests/unit/test_blob_verify_task_registration.py
git commit -m "feat(s-drift-3): easysynq.blob.verify Beat task — daily rolling D1 under LOCK_BLOB_VERIFY"
```

---

### Task 6: Operator CLI — `cli/blob.py`

**Files:**
- Create: `apps/api/src/easysynq_api/cli/blob.py`

- [ ] **Step 1: Implement the CLI** (the `cli/mirror.py` shape; no dedicated unit test — the same convention as the mirror CLI, covered by static checks + the live smoke)

Create `apps/api/src/easysynq_api/cli/blob.py`:

```python
"""Operator CLI for the D1 blob integrity verify (S-drift-3) — runs inside the api/worker image.

    python -m easysynq_api.cli.blob verify                  # rolling sample (settings size)
    python -m easysynq_api.cli.blob verify --full           # the on-demand complete pass
    python -m easysynq_api.cli.blob verify --sample-size N  # override the sample size

Acquires the same ``LOCK_BLOB_VERIFY`` the Beat task uses, so a manual run and the scheduler
cannot race (skips if held). After restoring a corrupted object from backup, re-run ``verify``
to clear the alarm (stamp-on-OK-only: a finding re-alarms every run until the re-hash passes).
Exit 1 on a FAILED (infrastructure) scan.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import get_settings
from ..services.common.pg_locks import LOCK_BLOB_VERIFY, pg_advisory_lock
from ..services.vault.blob_verify import BlobVerifyReport, persist_blob_verify, verify_blobs


async def _verify(*, full: bool, sample_size: int | None) -> BlobVerifyReport | None:
    """The scan+persist pipeline under the advisory lock; ``None`` if another verify holds it."""
    engine = create_async_engine(get_settings().database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session, pg_advisory_lock(session, LOCK_BLOB_VERIFY) as held:
            if not held:
                return None
            report = await verify_blobs(session, sample_size=sample_size, full=full)
            await persist_blob_verify(session, report, triggered_by="cli")
            return report
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="easysynq-blob", description="Vault blob integrity CLI.")
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser(
        "verify",
        help="re-hash blobs against their sha256 identity (doc 03 §8.2, D1); rolling by default",
    )
    verify.add_argument(
        "--full", action="store_true", help="verify EVERY blob (the periodic full set)"
    )
    verify.add_argument(
        "--sample-size", type=int, default=None, help="override BLOB_VERIFY_SAMPLE_SIZE"
    )
    args = parser.parse_args(argv)

    report = asyncio.run(_verify(full=args.full, sample_size=args.sample_size))
    if report is None:
        print("blob verify skipped: another verify is already in progress")
        return 0
    c = report.counts()
    print(
        f"blob verify: status={report.status} scanned={c['scanned']} ok={c['ok']} "
        f"mismatched={c['mismatched']} missing={c['missing']} read_errors={c['read_errors']} "
        f"total_blobs={c['total_blobs']} full={c['full']}"
    )
    return 1 if report.status == "FAILED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Static checks**

Run: `cd apps/api && uv run ruff check src/easysynq_api/cli/blob.py && uv run mypy src`
Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add apps/api/src/easysynq_api/cli/blob.py
git commit -m "feat(s-drift-3): easysynq blob verify CLI — rolling/--full under LOCK_BLOB_VERIFY"
```

---

### Task 7: The D4 report + status read — `services/vault/drift_report.py`

**Files:**
- Create: `apps/api/src/easysynq_api/services/vault/drift_report.py`
- Create: `apps/api/tests/integration/test_drift_report.py`

- [ ] **Step 1: Implement the read-only service**

Create `apps/api/src/easysynq_api/services/vault/drift_report.py`:

```python
"""Read-only drift reporting (S-drift-3): the admin status read + the D4 superseded-copies report.

D4 (doc 05 §9.1 / R11): ``EXPORTED``/``PRINTED`` audit events are emitted only for the
THEN-Effective version (``render_dynamic_copy``), so any such event whose version is now
Superseded/Obsolete is by construction an outstanding copy of a superseded rendition. There is no
decrement leg (a paper copy cannot be un-printed): the count is the honest upper bound, and the
S7c verify token is the per-copy resolution. Copies of the CURRENTLY Effective version are
deliberately excluded — they are controlled, not outstanding. Per doc 05 §9.2.1, D4 is the ONLY
detection leg that reaches copies outside the mirror.

The status read is the seam S-drift-2 reserved: the latest ``drift_scan`` per kind rides
``ix_drift_scan_kind_started_at`` (DISTINCT ON), plus the D1 rolling-cursor coverage
(``blob.verified_at``) and the D4 headline. LIVE reads, no persistence, no side effects.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from ...db.models._audit_enums import AuditObjectType, EventType
from ...db.models._drift_enums import DriftScanKind
from ...db.models._vault_enums import VersionState
from ...db.models.audit_event import AuditEvent
from ...db.models.blob import Blob
from ...db.models.document_version import DocumentVersion
from ...db.models.documented_information import DocumentedInformation
from ...db.models.drift_scan import DriftScan

_COPY_EVENTS = (EventType.EXPORTED, EventType.PRINTED)
_OUTSTANDING_STATES = (VersionState.Superseded, VersionState.Obsolete)


def _superseded_base() -> Select[Any]:
    """One grouped row per (now-superseded version that has at least one EXPORTED/PRINTED event),
    with the document's CURRENT effective revision label for the operator's recall list
    (NULL-safe outer join — an obsoleted document has no effective version)."""
    cur = aliased(DocumentVersion)
    return (
        select(
            DocumentVersion.document_id.label("document_id"),
            DocumentedInformation.identifier.label("identifier"),
            DocumentVersion.id.label("version_id"),
            DocumentVersion.revision_label.label("revision_label"),
            DocumentVersion.version_state.label("version_state"),
            cur.revision_label.label("current_revision_label"),
            func.count().filter(AuditEvent.event_type == EventType.EXPORTED).label("exported"),
            func.count().filter(AuditEvent.event_type == EventType.PRINTED).label("printed"),
            func.max(AuditEvent.occurred_at).label("last_copy_at"),
        )
        .join_from(
            AuditEvent, DocumentVersion, DocumentVersion.id == AuditEvent.object_id
        )
        .join(
            DocumentedInformation,
            DocumentedInformation.id == DocumentVersion.document_id,
        )
        .outerjoin(cur, cur.id == DocumentedInformation.current_effective_version_id)
        .where(
            AuditEvent.event_type.in_(_COPY_EVENTS),
            AuditEvent.object_type == AuditObjectType.version,
            DocumentVersion.version_state.in_(_OUTSTANDING_STATES),
        )
        .group_by(
            DocumentVersion.document_id,
            DocumentedInformation.identifier,
            DocumentVersion.id,
            DocumentVersion.revision_label,
            DocumentVersion.version_state,
            cur.revision_label,
        )
    )


async def _superseded_totals(session: AsyncSession) -> tuple[int, int]:
    sub = _superseded_base().subquery()
    versions, copies = (
        await session.execute(
            select(
                func.count(), func.coalesce(func.sum(sub.c.exported + sub.c.printed), 0)
            ).select_from(sub)
        )
    ).one()
    return int(versions), int(copies)


async def superseded_copies(
    session: AsyncSession, *, limit: int = 50, offset: int = 0
) -> dict[str, Any]:
    """The D4 report: per-version outstanding-copy rows (newest copy first) + full-set totals
    (computed over the WHOLE filtered set, not the page)."""
    versions, copies = await _superseded_totals(session)
    sub = _superseded_base().subquery()
    rows = (
        await session.execute(
            select(sub)
            .order_by(desc(sub.c.last_copy_at), sub.c.version_id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    items = [
        {
            "document_id": str(r.document_id),
            "identifier": r.identifier,
            "version_id": str(r.version_id),
            "revision_label": r.revision_label,
            "version_state": (
                r.version_state.value
                if isinstance(r.version_state, VersionState)
                else str(r.version_state)
            ),
            "current_revision_label": r.current_revision_label,
            "exported": int(r.exported),
            "printed": int(r.printed),
            "last_copy_at": r.last_copy_at.isoformat(),
        }
        for r in rows
    ]
    return {"total": {"versions": versions, "copies": copies}, "items": items}


async def drift_status(session: AsyncSession) -> dict[str, Any]:
    """The thin admin status read: latest scan per kind (null until that scanner's first run) +
    the D1 coverage block + the D4 headline."""
    latest = (
        (
            await session.execute(
                select(DriftScan)
                .distinct(DriftScan.kind)
                .order_by(DriftScan.kind, DriftScan.started_at.desc())
            )
        )
        .scalars()
        .all()
    )
    scans: dict[str, Any] = {k.value: None for k in DriftScanKind}
    for row in latest:
        scans[row.kind.value] = {
            "status": row.status.value,
            "started_at": row.started_at.isoformat(),
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
            "counts": row.counts,
            "triggered_by": row.triggered_by,
        }
    total, never, oldest = (
        await session.execute(
            select(
                func.count(),
                func.count().filter(Blob.verified_at.is_(None)),
                func.min(Blob.verified_at),
            ).select_from(Blob)
        )
    ).one()
    versions, copies = await _superseded_totals(session)
    return {
        "scans": scans,
        "blob_coverage": {
            "total": int(total),
            "never_verified": int(never),
            "oldest_verified_at": oldest.isoformat() if oldest is not None else None,
        },
        "superseded_copies": {"versions": versions, "copies": copies},
    }
```

- [ ] **Step 2: Author the integration tests** (Linux-CI-only; static-check locally)

Create `apps/api/tests/integration/test_drift_report.py`:

```python
"""S-drift-3 integration proofs — the D4 superseded-copies report + the drift status read.

D4 events are PLANTED directly (render_dynamic_copy 409s without a real Gotenberg rendition in
this env; the emitter is covered by the S7d tests — the report's contract is the audit-trail
shape: event_type EXPORTED/PRINTED, object_type=version, object_id=version_id). ⚠ Run-scoped
assertions only: every lookup filters to THIS test's document/identifier; totals are asserted as
deltas, never absolutes. SoD-2: the approver (subj.b) releases, never the author.
"""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Callable
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models._audit_enums import ActorType, AuditObjectType, EventType
from easysynq_api.db.models._vault_enums import VersionState
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.document_version import DocumentVersion
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.vault.blob_verify import persist_blob_verify, verify_blobs
from easysynq_api.services.vault.drift_report import drift_status, superseded_copies

from . import s5_helpers as s5
from .test_mirror import _grant_release_actors
from .test_vault import _auth, _checkin, _upload

pytestmark = pytest.mark.integration


@pytest.fixture
def subj() -> SimpleNamespace:
    salt = uuid.uuid4().hex[:10]
    return SimpleNamespace(a=f"kc-author-{salt}", b=f"kc-approver-{salt}", salt=salt)


async def _versions_of(document_id: str) -> list[DocumentVersion]:
    async with get_sessionmaker()() as s:
        return list(
            (
                await s.execute(
                    select(DocumentVersion)
                    .where(DocumentVersion.document_id == uuid.UUID(document_id))
                    .order_by(DocumentVersion.version_seq)
                )
            )
            .scalars()
            .all()
        )


async def _plant_copy_event(version_id: uuid.UUID, event_type: EventType) -> None:
    """Plant the exact row render_dynamic_copy emits (system actor: simplest FK-safe shape)."""
    async with get_sessionmaker()() as s:
        org_id = await s5.default_org_id()
        s.add(
            AuditEvent(
                org_id=org_id,
                occurred_at=datetime.datetime.now(datetime.UTC),
                actor_id=None,
                actor_type=ActorType.system,
                event_type=event_type,
                object_type=AuditObjectType.version,
                object_id=version_id,
            )
        )
        await s.commit()


async def _supersede(
    app_client: AsyncClient,
    ha: dict[str, str],
    hb: dict[str, str],
    document_id: str,
    content: bytes,
) -> None:
    """The test_mirror_scan supersession recipe: revise → approve → release (v_prev → Superseded)."""
    await app_client.post(f"/api/v1/documents/{document_id}/start-revision", headers=ha)
    sha2 = await _upload(app_client, ha, document_id, content)
    await _checkin(
        app_client, ha, document_id, sha2, change_reason="v2", change_significance="MINOR"
    )
    await app_client.post(f"/api/v1/documents/{document_id}/submit-review", headers=ha)
    task_id = await s5.task_for_doc(document_id)
    await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hb, json={"outcome": "approve"}
    )
    rel = await app_client.post(f"/api/v1/documents/{document_id}/release", headers=hb, json={})
    assert rel.status_code == 200, rel.text


async def test_superseded_copies_counts_only_non_effective_versions(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
) -> None:
    await _grant_release_actors(subj)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    doc = await s5.drive_to_effective(
        app_client, ha, hb, hb, await s5.type_id("SOP"), f"D4-V1-{subj.salt}".encode()
    )
    did = doc["id"]
    v1 = (await _versions_of(did))[0]
    # Copies made while v1 governed (the only window render_dynamic_copy serves it).
    await _plant_copy_event(v1.id, EventType.EXPORTED)
    await _plant_copy_event(v1.id, EventType.PRINTED)

    # While v1 is still Effective the report must NOT count it (controlled, not outstanding).
    async with get_sessionmaker()() as s:
        before = await superseded_copies(s, limit=500)
    assert not [i for i in before["items"] if i["document_id"] == did]

    await _supersede(app_client, ha, hb, did, f"D4-V2-{subj.salt}".encode())
    versions = await _versions_of(did)
    v1_after, v2 = versions[0], versions[1]
    assert v1_after.version_state in (VersionState.Superseded, VersionState.Obsolete)
    # A copy of the NEW Effective version stays excluded.
    await _plant_copy_event(v2.id, EventType.EXPORTED)

    async with get_sessionmaker()() as s:
        after = await superseded_copies(s, limit=500)
    mine = [i for i in after["items"] if i["document_id"] == did]
    assert len(mine) == 1
    row = mine[0]
    assert row["version_id"] == str(v1.id)
    assert row["exported"] == 1 and row["printed"] == 1
    assert row["identifier"] == doc["identifier"]
    assert row["current_revision_label"] == v2.revision_label
    assert row["last_copy_at"] is not None
    # Delta-based totals: ours added exactly one version and two copies.
    assert after["total"]["versions"] >= before["total"]["versions"] + 1
    assert after["total"]["copies"] >= before["total"]["copies"] + 2


async def test_drift_status_shape_and_blob_rehash_leg(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
) -> None:
    """After a verify run, the BLOB_REHASH leg is non-null with the summary shape; coverage and
    headline blocks are present (run-scoped: ≥ / non-null, never absolutes on the shared DB)."""
    async with get_sessionmaker()() as s:
        report = await verify_blobs(s, sample_size=1)
        assert await persist_blob_verify(s, report, triggered_by="cli") is True
        status = await drift_status(s)

    assert set(status) == {"scans", "blob_coverage", "superseded_copies"}
    assert set(status["scans"]) == {"MIRROR", "BLOB_REHASH"}
    leg = status["scans"]["BLOB_REHASH"]
    assert leg is not None
    assert leg["status"] in ("CLEAN", "DIVERGENT", "FAILED")
    assert leg["triggered_by"] in ("beat", "sync", "cli")
    assert "scan_id" in leg["counts"]
    cov = status["blob_coverage"]
    assert cov["total"] >= 0 and cov["never_verified"] >= 0
    sc = status["superseded_copies"]
    assert sc["versions"] >= 0 and sc["copies"] >= 0
```

- [ ] **Step 3: Static checks**

Run: `cd apps/api && uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add apps/api/src/easysynq_api/services/vault/drift_report.py \
        apps/api/tests/integration/test_drift_report.py
git commit -m "feat(s-drift-3): drift_report — D4 superseded-copies read + latest-per-kind status"
```

---

### Task 8: The admin endpoints — `api/drift.py` + mount + endpoint proofs

**Files:**
- Create: `apps/api/src/easysynq_api/api/drift.py`
- Modify: `apps/api/src/easysynq_api/main.py` (import block ~line 24; mount block ~line 143)
- Create: `apps/api/tests/integration/test_drift_endpoints.py`

- [ ] **Step 1: Implement the router**

Create `apps/api/src/easysynq_api/api/drift.py`:

```python
"""The thin admin drift-status surface (S-drift-3, doc 05 §9.1, doc 15) — gated drift.read (R41).

Two cheap, pure reads (no scan trigger, no side effect — pure GETs): the latest drift_scan per
kind (the S-drift-2 ``(kind, started_at DESC)`` index read) + D1 blob coverage + the D4
superseded-copies report. ``drift.read`` is the R38-additive SYSTEM-domain key seeded in 0047 and
granted to System Administrator — as-built the SYSTEM-domain key IS the admin gate (the
``config.update`` precedent). The S-web-8 UI consumes exactly this surface.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.app_user import AppUser
from ..db.session import get_session
from ..services.authz import require
from ..services.vault import drift_report

router = APIRouter(prefix="/api/v1", tags=["admin"])

# drift.read is SYSTEM-domain / admin-side (doc 07 §3.9, R41) — operational integrity status.
_drift_read = require("drift.read")


@router.get("/admin/drift/status")
async def drift_status_endpoint(
    caller: AppUser = Depends(_drift_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Latest scan per kind + the D1 rolling-cursor coverage + the D4 headline. Needs drift.read."""
    return await drift_report.drift_status(session)


@router.get("/admin/drift/superseded-copies")
async def superseded_copies_endpoint(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    caller: AppUser = Depends(_drift_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """The D4 report: outstanding EXPORTED/PRINTED copies of now-superseded versions (doc 05
    §9.1 D4 / R11 — the only detection leg that reaches copies outside the mirror). Needs
    drift.read."""
    return await drift_report.superseded_copies(session, limit=limit, offset=offset)
```

- [ ] **Step 2: Mount it**

In `main.py`, add to the alphabetical import block (after the `directory` import, line ~26):

```python
from .api.drift import router as drift_router
```

and in the mount block, directly after the `config_router` line (~143):

```python
    app.include_router(drift_router)  # S-drift-3: admin drift status + D4 report (drift.read)
```

- [ ] **Step 3: Author the endpoint integration tests**

Create `apps/api/tests/integration/test_drift_endpoints.py`:

```python
"""S-drift-3 endpoint proofs — drift.read gates both admin GETs (deny-by-default; the seeded
System Administrator grant from 0047 admits; a grant-less user gets 403, never a 500/leak)."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient

from . import s5_helpers as s5
from .test_vault import _auth

pytestmark = pytest.mark.integration


async def test_drift_endpoints_deny_without_key_allow_with_role(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
) -> None:
    salt = uuid.uuid4().hex[:10]
    admin, nobody = f"kc-driftadmin-{salt}", f"kc-driftnobody-{salt}"
    await s5.grant_role(admin, "System Administrator")  # holds drift.read via the 0047 seed

    for path in ("/api/v1/admin/drift/status", "/api/v1/admin/drift/superseded-copies"):
        r = await app_client.get(path, headers=_auth(token_factory, nobody))
        assert r.status_code == 403, f"{path}: expected deny-by-default, got {r.status_code}"

    r = await app_client.get(
        "/api/v1/admin/drift/status", headers=_auth(token_factory, admin)
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"scans", "blob_coverage", "superseded_copies"}
    assert set(body["scans"]) == {"MIRROR", "BLOB_REHASH"}

    r = await app_client.get(
        "/api/v1/admin/drift/superseded-copies", headers=_auth(token_factory, admin)
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"total", "items"}
    assert set(body["total"]) == {"versions", "copies"}


async def test_superseded_copies_pagination_validation(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
) -> None:
    salt = uuid.uuid4().hex[:10]
    admin = f"kc-driftadmin2-{salt}"
    await s5.grant_role(admin, "System Administrator")
    h = _auth(token_factory, admin)
    assert (
        await app_client.get("/api/v1/admin/drift/superseded-copies?limit=0", headers=h)
    ).status_code == 422
    assert (
        await app_client.get("/api/v1/admin/drift/superseded-copies?limit=501", headers=h)
    ).status_code == 422
    assert (
        await app_client.get("/api/v1/admin/drift/superseded-copies?offset=-1", headers=h)
    ).status_code == 422
```

- [ ] **Step 4: Static checks**

Run: `cd apps/api && uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/easysynq_api/api/drift.py \
        apps/api/src/easysynq_api/main.py \
        apps/api/tests/integration/test_drift_endpoints.py
git commit -m "feat(s-drift-3): /admin/drift/status + /admin/drift/superseded-copies (drift.read)"
```

---

### Task 9: The contract — `openapi.yaml`

**Files:**
- Modify: `packages/contracts/openapi.yaml` (after the `/admin/config` path block, ~line 3620)

- [ ] **Step 1: Document both endpoints**

Insert after the complete `/admin/config` path block (match the file's 2-space indent and `>-` description style):

```yaml
  /admin/drift/status:
    get:
      tags: [admin]
      operationId: getDriftStatus
      summary: Latest drift-scan per kind + D1 blob coverage + the D4 headline. Needs drift.read (SYSTEM, R41).
      description: >-
        The thin admin drift-status read (S-drift-3, doc 05 §9.1). scans.MIRROR / scans.BLOB_REHASH
        carry the latest drift_scan summary per kind (null until that scanner's first run);
        blob_coverage reports the D1 rolling re-hash cursor (verified_at: total rows,
        never-verified count, oldest stamp); superseded_copies is the D4 headline — outstanding
        EXPORTED/PRINTED copies of now-Superseded/Obsolete versions. Pure read; no scan is
        triggered.
      responses:
        "200":
          description: The drift status snapshot.
          content:
            application/json:
              schema:
                type: object
                required: [scans, blob_coverage, superseded_copies]
                properties:
                  scans:
                    type: object
                    description: Latest summary per drift_scan kind; null until first run.
                    required: [MIRROR, BLOB_REHASH]
                    properties:
                      MIRROR:
                        anyOf:
                          - $ref: "#/components/schemas/DriftScanSummary"
                          - { type: "null" }
                      BLOB_REHASH:
                        anyOf:
                          - $ref: "#/components/schemas/DriftScanSummary"
                          - { type: "null" }
                  blob_coverage:
                    type: object
                    required: [total, never_verified, oldest_verified_at]
                    properties:
                      total: { type: integer }
                      never_verified: { type: integer }
                      oldest_verified_at: { type: [string, "null"], format: date-time }
                  superseded_copies:
                    type: object
                    required: [versions, copies]
                    properties:
                      versions: { type: integer }
                      copies: { type: integer }
              example:
                scans:
                  MIRROR:
                    status: CLEAN
                    started_at: "2026-06-10T03:00:00+00:00"
                    finished_at: "2026-06-10T03:00:04+00:00"
                    counts: { scanned: 41, ok: 41, scan_id: "9b2f…" }
                    triggered_by: beat
                  BLOB_REHASH: null
                blob_coverage:
                  total: 1240
                  never_verified: 1240
                  oldest_verified_at: null
                superseded_copies: { versions: 3, copies: 7 }
        "403":
          description: Caller lacks drift.read (deny-by-default).

  /admin/drift/superseded-copies:
    get:
      tags: [admin]
      operationId: getSupersededCopies
      summary: The D4 outstanding exported/printed copies of now-superseded versions. Needs drift.read.
      description: >-
        Doc 05 §9.1 D4 / R11 — the only detection leg that reaches copies OUTSIDE the mirror.
        EXPORTED/PRINTED audit events are emitted only for the then-Effective version, so every
        event whose version is now Superseded/Obsolete is an outstanding copy of a superseded
        rendition. No decrement leg exists (a paper copy cannot be un-printed): the count is the
        honest upper bound; the public /verify token is the per-copy resolution. Totals cover the
        FULL filtered set, not the page.
      parameters:
        - name: limit
          in: query
          schema: { type: integer, minimum: 1, maximum: 500, default: 50 }
        - name: offset
          in: query
          schema: { type: integer, minimum: 0, default: 0 }
      responses:
        "200":
          description: Per-version outstanding-copy rows, newest copy first.
          content:
            application/json:
              schema:
                type: object
                required: [total, items]
                properties:
                  total:
                    type: object
                    required: [versions, copies]
                    properties:
                      versions: { type: integer }
                      copies: { type: integer }
                  items:
                    type: array
                    items:
                      type: object
                      required:
                        [document_id, identifier, version_id, revision_label, version_state,
                         current_revision_label, exported, printed, last_copy_at]
                      properties:
                        document_id: { type: string, format: uuid }
                        identifier: { type: string }
                        version_id: { type: string, format: uuid }
                        revision_label: { type: string }
                        version_state:
                          type: string
                          enum: [Superseded, Obsolete]
                        current_revision_label:
                          type: [string, "null"]
                          description: The document's CURRENT effective revision (null when obsoleted).
                        exported: { type: integer }
                        printed: { type: integer }
                        last_copy_at: { type: string, format: date-time }
              example:
                total: { versions: 1, copies: 3 }
                items:
                  - document_id: "5e0c…"
                    identifier: SOP-PUR-002
                    version_id: "77aa…"
                    revision_label: "2.0"
                    version_state: Superseded
                    current_revision_label: "2.1"
                    exported: 2
                    printed: 1
                    last_copy_at: "2026-05-30T14:22:00+00:00"
        "403":
          description: Caller lacks drift.read (deny-by-default).
```

And add to `components/schemas` (alphabetical placement among the existing schemas):

```yaml
    DriftScanSummary:
      type: object
      description: >-
        The latest drift_scan row for one kind (S-drift-2 mig 0046; S-drift-3 added the
        BLOB_REHASH kind). counts is the kind-specific summary bag (see the drift_scan model
        docstring) — clients must treat unknown keys as additive. The status payload wraps this
        in anyOf-null (no row yet for that kind).
      required: [status, started_at, finished_at, counts, triggered_by]
      properties:
        status:
          type: string
          enum: [CLEAN, DIVERGENT, FAILED]
        started_at: { type: string, format: date-time }
        finished_at: { type: [string, "null"], format: date-time }
        counts:
          type: object
          additionalProperties: true
        triggered_by:
          type: string
          enum: [beat, sync, cli]
```

- [ ] **Step 2: Lint the contract**

Run: the `/check-contracts` skill (redocly lint).
Expected: PASS. (The contract is OpenAPI **3.1.0** — null is expressed as `type: [string, "null"]`
/ `anyOf` with `{ type: "null" }`, the file's established convention, already used above. Never
`nullable: true`, which is 3.0-only.)

- [ ] **Step 3: Commit**

```bash
git add packages/contracts/openapi.yaml
git commit -m "contracts(s-drift-3): /admin/drift/status + /admin/drift/superseded-copies"
```

---

### Task 10: Docs in-PR

**Files:**
- Modify: `docs/05-revision-and-drift.md` (§9.1 rows D1/D4)
- Modify: `docs/03-architecture-and-stack.md` (§8.2 blob-integrity bullet)
- Modify: `docs/07-authorization-model.md` (§3.9 catalog table)
- Modify: `docs/14-data-model.md` (blob row note + drift_scan kind)
- Modify: `docs/15-api-design.md` (§8.17 table)
- Modify: `docs/decisions-register.md` (R41)
- Create: `docs/runbooks/blob-integrity-verify.md` (+ index entry in `docs/runbooks/00-index.md`)
- Modify: `docs/slice-history.md` (new entry)
- Modify: `CLAUDE.md` (Current status + Recent learnings)

- [ ] **Step 1: docs/05 §9.1 — mark D1 + D4 shipped**

In the D1 row, after "(Architecture §8.2.)" append:

```
*(✅ Shipped S-drift-3: a daily rolling re-hash of the K least-recently-verified blobs (`blob.verified_at` is the cursor, stamped on OK ONLY so a finding re-alarms until resolved); `BLOB_INTEGRITY_FAILED` audit alarm (classification HASH_MISMATCH / OBJECT_MISSING / READ_ERROR in the payload); summary row `drift_scan` kind=`BLOB_REHASH`; task `easysynq.blob.verify` + CLI `easysynq blob verify [--full]`.)*
```

In the D4 row, after "(reconciled per Decisions Register R11)." append:

```
*(✅ Shipped S-drift-3: the reportable count = `GET /admin/drift/superseded-copies` (drift.read), aggregating EXPORTED/PRINTED audit events over now-Superseded/Obsolete versions — the S7c verify token remains the per-copy resolution.)*
```

- [ ] **Step 2: docs/03 §8.2 — pointer**

In the "Blob integrity" bullet (line ~284), append:

```
*(✅ S-drift-3: `easysynq.blob.verify` — daily rolling sample, default 500/day, rotation = full coverage; `BLOB_INTEGRITY_FAILED` on mismatch.)*
```

- [ ] **Step 3: docs/07 §3.9 — the catalog row**

In the SYSTEM-domain section of the §3.9 permission table (after the `restore.run` row, line ~217), add:

```
| `drift.read` | Read the drift-detection status surface (latest scans, blob coverage, the D4 superseded-copies report) | SYSTEM | **R41 additive (R38)**, S-drift-3; granted to System Administrator |
```

- [ ] **Step 4: docs/14 — blob + drift_scan notes**

In the `blob` row of the §"blob" table (line ~270), change the Notes cell to append: `verified_at = the D1 rolling-verify cursor (S-drift-3): stamped on a passing re-hash ONLY, so findings stay at the rotation head and re-alarm.` In the `drift_scan` table section (added by S-drift-2), update the `kind` description to `enum drift_scan_kind = ('MIRROR','BLOB_REHASH')` with a note `BLOB_REHASH added additively in 0047 (S-drift-3)`.

- [ ] **Step 5: docs/15 §8.17 — the two endpoints**

Add two rows to the §8.17 Admin & Config table (after the `/admin/jobs/{id}` row):

```
| GET | `/admin/drift/status` | **S-drift-3:** latest `drift_scan` per kind + D1 blob coverage + the D4 headline. Gated on the SYSTEM-domain `drift.read` (R41); pure read, no scan trigger. |
| GET | `/admin/drift/superseded-copies` | **S-drift-3 (D4, R11):** outstanding EXPORTED/PRINTED copies of now-Superseded/Obsolete versions (`limit`/`offset`; totals over the full set). The only detection leg reaching copies outside the mirror; the public `/verify` token is the per-copy resolution. |
```

- [ ] **Step 6: decisions-register — R41**

Append after R40, following the register's entry format:

```
## R41 — `drift.read` (S-drift-3): the second R38-additive catalog key

**Decision (owner, 2026-06-10).** The admin drift-status surface (`GET /admin/drift/status`,
`GET /admin/drift/superseded-copies`) is gated on a NEW SYSTEM-domain key **`drift.read`**
(`is_system_domain=true`, `sod_sensitive=false`, `sig_hook=false`, `finest_scope=SYSTEM`), seeded
in migration 0047 and granted to **System Administrator**. Riding `storage.read` was rejected:
that key is storage *config*, the D4 copies report isn't storage at all, and riding would silently
widen every storage-config reader's view. Per R38: additive only — no rename/removal; the catalog
count moves 98 → 99. The trailing S-web-8 UI gates on the same key. Related S-drift-3 owner forks
(spec §0): ONE `BLOB_INTEGRITY_FAILED` event type (classification in the payload); D1 cadence =
one daily rolling task (rotation = the periodic full set; `--full` CLI on demand); D4 is a live
read (no persisted scan).
```

- [ ] **Step 7: the runbook**

Create `docs/runbooks/blob-integrity-verify.md`:

```markdown
# Blob integrity verify (D1) — operator runbook

**What it is.** A daily Beat task (`easysynq.blob.verify`, `BLOB_VERIFY_INTERVAL_SECONDS` default
86400) re-hashes the `BLOB_VERIFY_SAMPLE_SIZE` (default 500) least-recently-verified vault blobs
against their content-addressed identity (`blob.sha256`). Rotation covers the FULL set every
⌈N/sample⌉ days. `blob.verified_at` is stamped on a passing re-hash ONLY — a failing blob stays at
the rotation head and **re-alarms on every run until you resolve it**. Status:
`GET /admin/drift/status` (`drift.read`).

**On a `BLOB_INTEGRITY_FAILED` audit event** (`after.classification`):

- `HASH_MISMATCH` — the stored bytes no longer hash to the blob's identity (bit-rot or
  storage-layer tamper; WORM object-lock blocks legitimate overwrite, so treat as a security
  signal). `OBJECT_MISSING` — the object is GONE (storage tamper, or a blob row whose bytes were
  destroyed outside the app — a broken blob-row-iff-bytes invariant; either way alarm-worthy).
  `READ_ERROR` — an object-scoped read failure (e.g. ACL damage); transient ones self-clear on the
  next run.
- **Do NOT touch the mirror or the bucket in place.** Blobs are WORM-locked; there is no
  auto-correction. Restore the affected object(s) from a verified backup to a fresh/verified
  target per the backup-restore runbook (R37 — never mutate the locked bucket in place).
- After the restore, run `MSYS_NO_PATHCONV=1 docker compose --env-file .env -f
  infra/compose/compose.yml exec worker python -m easysynq_api.cli.blob verify --full` and
  confirm the re-hash passes (the alarm clears: the blob is stamped and leaves the rotation head).
- A `FAILED` scan status (not a finding) means infrastructure trouble (MinIO/PG unreachable) — the
  scan aborts honestly instead of minting noise findings; check `/readyz` and the worker logs.

**The D4 superseded-copies report** (`GET /admin/drift/superseded-copies`): the recall list —
which superseded revisions still have exported/printed copies in circulation, with the current
effective revision to quote. A reported copy is resolved per-copy via its printed verify token
(the public `/verify` page); the count never decrements (a paper copy cannot be un-printed).
```

Add to `docs/runbooks/00-index.md` (match the index's row format): a `blob-integrity-verify.md` entry described as "D1 blob re-hash alarms: classifications, restore-from-backup response, the D4 recall list".

- [ ] **Step 8: slice-history + CLAUDE.md**

`docs/slice-history.md`: add this entry at the top, adjusted to the file's existing per-slice
heading format:

```markdown
## S-drift-3 — D1 blob verify + D4 superseded-copies + the admin drift-status surface (2026-06-10)

Completes the drift family's detection legs (D1–D5 all shipped). Mig `0047`: `drift_scan_kind +=
BLOB_REHASH` (the S-drift-2 seam), `event_type += BLOB_INTEGRITY_FAILED`, and the **R41**
`drift.read` SYSTEM key (catalog 98→99; seeded + granted to System Administrator via the resilient
org lookup — this install renamed the short_code). Owner forks: a NEW `drift.read` key over riding
`storage.read` (the D4 report isn't storage; riding widens storage-config readers); ONE
`BLOB_INTEGRITY_FAILED` event (classification HASH_MISMATCH/OBJECT_MISSING/READ_ERROR in the
payload — no severity split exists, unlike MIRROR_STALE/TAMPER); daily rolling cadence where
**rotation IS the periodic full set** (verified_at NULLS FIRST → oldest, default 500/day, CLI
`--full` on demand). **Stamp-on-OK-only is the slice's load-bearing call**: `blob.verified_at` is
both the rotation cursor and the alarm latch — a finding stays at the head and re-alarms every run
(no auto-correction exists for blobs; restore-from-backup per the new
`runbooks/blob-integrity-verify.md`), and stamping a bad blob would let the next clean sample mask
it as CLEAN on the latest-per-kind status read. Per-object errors are findings; connection-class
errors ABORT as FAILED salvaging findings (no noise-event storms when MinIO is down). D4 is a LIVE
read — `EXPORTED`/`PRINTED` events × now-Superseded/Obsolete versions is sound by construction
(`render_dynamic_copy` only ever serves the then-Effective version); no decrement leg (the `/verify`
token is the per-copy resolution). New surface: `GET /admin/drift/status` +
`GET /admin/drift/superseded-copies` (both `drift.read`; S-web-8 consumes them). Integration
technique: synthetic tamper via planted blob ROWS (a wrong-sha row over real bytes / a row with no
object) — never fight WORM. Spec:
`docs/superpowers/specs/2026-06-10-s-drift-3-blob-verify-drift-surface-design.md`.
```

`CLAUDE.md`:
- **Current status:** mark the drift family ✅ COMPLETE: append after the S-drift-2 sentence —
  `**S-drift-3** ✅ = D1 blob verify + D4 superseded-copies + the admin drift-status surface (mig 0047: BLOB_REHASH kind + BLOB_INTEGRITY_FAILED + the R41 drift.read key; services/vault/blob_verify.py daily rolling re-hash [stamp-on-OK-only → persistent re-alarm; LOCK_BLOB_VERIFY; easysynq.blob.verify + CLI blob verify --full] + services/vault/drift_report.py live D4 read + GET /admin/drift/status + /admin/drift/superseded-copies). The drift family (D1–D5) is COMPLETE; trailing S-web-8 UI remains.` Update the migration-head sentence to: `**Migration head `0047` (next `0048`) — S-drift-3 added `0047`.**`
- **Recent learnings:** add one bullet (newest first, keep the cap — demote the oldest if >12):
  `2026-06-10 — **S-drift-3 (D1+D4+admin drift surface) COMPLETES the drift family** (mig 0047). ⚠ **Stamp-on-OK-only**: blob.verified_at is the rotation cursor AND the alarm latch — stamping a failed blob would let the next clean sample mask unresolved corruption as CLEAN on the latest-per-kind status read; a finding stays at the rotation head and re-alarms per run (no auto-correction exists for blobs — restore-from-backup is the runbook action). ⚠ An infra-class failure (MinIO down) ABORTS the scan as FAILED salvaging findings — per-object errors are findings, connection errors are not (500 noise events otherwise). D4 is a LIVE read (EXPORTED/PRINTED × now-Superseded/Obsolete versions; render_dynamic_copy only ever served the then-Effective version, so the join is sound by construction). R41 = drift.read (catalog 98→99, the resilient org lookup in the 0047 grant seed). Narrative: docs/slice-history.md.`

- [ ] **Step 9: Commit**

```bash
git add docs/05-revision-and-drift.md docs/03-architecture-and-stack.md \
        docs/07-authorization-model.md docs/14-data-model.md docs/15-api-design.md \
        docs/decisions-register.md docs/runbooks/blob-integrity-verify.md \
        docs/runbooks/00-index.md docs/slice-history.md CLAUDE.md
git commit -m "docs(s-drift-3): D1/D4 shipped, R41 drift.read, runbook, slice history"
```

---

### Task 11: Full local gates + review

- [ ] **Step 1: Run every local gate**

- `/check-api` (ruff + format-check + mypy-strict + unit; if the full `-m unit` run hits the known
  libmagic access violation on this box, run the slice's unit files targeted:
  `cd apps/api && uv run pytest tests/unit/test_drift3_enums.py tests/unit/test_storage_hash_object.py tests/unit/test_blob_verify.py tests/unit/test_blob_verify_task_registration.py -v`
  and rely on Linux CI for the full suite).
- `/check-migrations` (0046↔0047 round-trip + `alembic check`).
- `/check-contracts` (redocly).
Expected: all PASS.

- [ ] **Step 2: diff-critic on the branch diff**

Dispatch the `diff-critic` agent (read-only) on `git diff main...HEAD`. Fold ONLY confirmed
findings; fix in this session (no spawn_task chips for in-PR follow-ups).

- [ ] **Step 3: Commit any review fixes**

```bash
git add -A && git commit -m "fix(s-drift-3): diff-critic review folds"
```

(Skip if no findings.)

- [ ] **Step 4: Post-PR follow-ups (note, not now)**

After the PR's FIRST green CI run: if the new integration tests are heavy, re-run
`bash scripts/refresh-test-durations.sh <green-run-id>` and commit the `apps/api/.test_durations`
diff in-PR (the #109 contract). Pre-merge live smoke (rebuild `migrate api worker beat` images
first — new CLI module + Beat entry are not in the container until rebuilt): plant a bad blob row
via the worker heredoc, run the verify task, check the audit row + `drift_scan` row + both
endpoints as `demo` (holds `drift.read` natively post-0047), then clean up and confirm CLEAN.
```
