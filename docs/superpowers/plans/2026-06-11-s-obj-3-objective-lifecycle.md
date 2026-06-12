# S-obj-3 Objective Lifecycle & Release — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire a Quality Objective (the `kind=DOCUMENT` `OBJ` subtype) through the controlled-document lifecycle to Effective — freezing its commitment into a WORM version on submit, then riding the generic review → approve → release machinery — so a released objective flips the ISO 6.2 ★ compliance node to COVERED.

**Architecture:** A `form_template`-style commitment-freeze (canonical-JSON WORM source blob + a `metadata_snapshot.objective_commitment` fold) is folded into a single `objective.manage`-gated "Submit for review" action; the OBJ then rides the existing DOCUMENT `/tasks` approve leg (`document.approve`) and the INV-1 SERIALIZABLE `release` cutover (`document.release`). No migration (head stays `0049`), no new permission key (catalog stays 100), no new signature meaning (R2 closed). The front-end reuses the `ApprovalStepper`, adds Submit/Release affordances, an `ObjectiveCommitmentContext` approver card, and a create-modal policy picker.

**Tech Stack:** FastAPI / Python 3.12 (api), Alembic-free, PostgreSQL 16 + MinIO (WORM), React/TS + Mantine + TanStack Query + MSW + vitest (web). Spec: [docs/superpowers/specs/2026-06-11-s-obj-3-objective-lifecycle-design.md](docs/superpowers/specs/2026-06-11-s-obj-3-objective-lifecycle-design.md).

---

## Conventions & environment notes (read once)

- **Commits:** Conventional Commits, `feat(s-obj-3): …` / `test(s-obj-3): …`; end every commit message body with the trailer `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`. Work happens on the existing branch `feat/s-obj-3-objective-lifecycle`.
- **API test environment (this Windows box):** api **unit** tests run locally (`cd apps/api && uv run pytest tests/unit/<file> -v`); there is a known baseline of 17 expected failures in 3 unrelated files — ignore those. api **integration** tests need Docker testcontainers and are validated in CI; if Docker is available locally they run too, otherwise the local end-to-end proof is the live-stack heredoc smoke in Task 18. Write the integration tests regardless — they are the behavioral contract CI enforces.
- **Static gate:** after every backend task, run `/check-api` (ruff + format-check + mypy-strict + unit). After every web task, run `/check-web` (eslint + strict tsc + build + vitest).
- **Path note:** all `apps/api` Python paths below are under `apps/api/src/easysynq_api/`; tests under `apps/api/tests/`. Web paths are under `apps/web/src/`.

---

## File Structure

**Backend — created:**
- `apps/api/src/easysynq_api/domain/objectives/commitment.py` — the pure `build_commitment(...)` (the canonical commitment dict).
- `apps/api/src/easysynq_api/services/objectives/lifecycle.py` — `submit_objective_for_review(...)` (freeze + T2 + instantiate-approval, one txn).
- `apps/api/tests/unit/test_objective_commitment.py` — unit tests for `build_commitment` + `_snapshot` fold.
- `apps/api/tests/integration/test_objective_lifecycle.py` — the create→submit→approve→release→Effective + 6.2-COVERED integration proof + the approval/policy/capabilities reads.

**Backend — modified:**
- `apps/api/src/easysynq_api/services/vault/service.py` — `_snapshot` gains `objective_commitment` kwarg; new `checkin_objective_commitment(...)`.
- `apps/api/src/easysynq_api/services/objectives/__init__.py` — export `submit_objective_for_review`.
- `apps/api/src/easysynq_api/api/objectives.py` — `_objective` serializer gains `capabilities`/`effective_from`; new helpers (`_load_objective_doc`, `_objective_capabilities`, `_objective_effective_from`, `_objective_release_scope`, `_approval_instance`/`_approval_task`); new endpoints (`POST …/submit-review`, `POST …/release`, `GET …/approval`, `GET /objectives/policy`).
- `packages/contracts/openapi.yaml` — the four new paths + the `Objective` schema additions.

**Web — created:**
- `apps/web/src/features/review/ObjectiveCommitmentContext.tsx` — the approver's frozen-commitment card.
- Test files alongside each touched component (vitest).

**Web — modified:**
- `apps/web/src/lib/types.ts` — `Objective` gains `capabilities?`/`effective_from?`; new `EffectivePolicy` type; `DocumentVersion` gains `metadata_snapshot?` (verify).
- `apps/web/src/features/objectives/hooks.ts` + `mutations.ts` — `useObjectiveApproval`, `useEffectivePolicy`, `useSubmitObjectiveForReview`, `useReleaseObjective`.
- `apps/web/src/features/objectives/ObjectiveDetailPage.tsx` — Submit / `ApprovalStepper` / Release affordances.
- `apps/web/src/features/objectives/NewObjectiveModal.tsx` — the policy picker.
- `apps/web/src/features/review/ReviewApprovePage.tsx` — render `ObjectiveCommitmentContext` for an objective subject.
- `apps/web/src/test/msw/handlers.ts` — handlers + fixtures for the four new endpoints + an OBJ approval instance/version/task.

---

# Phase 1 — Backend freeze primitives (pure, unit-tested)

### Task 1: `build_commitment` — the canonical commitment dict

**Files:**
- Create: `apps/api/src/easysynq_api/domain/objectives/commitment.py`
- Test: `apps/api/tests/unit/test_objective_commitment.py`

- [ ] **Step 1: Write the failing test**

Create `apps/api/tests/unit/test_objective_commitment.py`:

```python
import datetime
import uuid
from decimal import Decimal

import pytest
import rfc8785

from easysynq_api.db.models._objective_enums import ObjectiveDirection
from easysynq_api.domain.objectives.commitment import build_commitment

pytestmark = pytest.mark.unit

HI = ObjectiveDirection.HIGHER_IS_BETTER
LO = ObjectiveDirection.LOWER_IS_BETTER


def test_build_commitment_all_fields_are_json_strings() -> None:
    c = build_commitment(
        target_value=Decimal("98.5"),
        unit="%",
        direction=HI,
        due_date=datetime.date(2026, 12, 31),
        at_risk_threshold=Decimal("95"),
        baseline_value=Decimal("90"),
        policy_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
    )
    assert c == {
        "target_value": "98.5",
        "unit": "%",
        "direction": "HIGHER_IS_BETTER",
        "due_date": "2026-12-31",
        "at_risk_threshold": "95",
        "baseline_value": "90",
        "policy_id": "11111111-1111-1111-1111-111111111111",
    }


def test_build_commitment_nullable_fields_are_none() -> None:
    c = build_commitment(
        target_value=Decimal("5"),
        unit="count",
        direction=LO,
        due_date=datetime.date(2026, 6, 30),
        at_risk_threshold=None,
        baseline_value=None,
        policy_id=None,
    )
    assert c["at_risk_threshold"] is None
    assert c["baseline_value"] is None
    assert c["policy_id"] is None


def test_build_commitment_is_rfc8785_serializable_and_deterministic() -> None:
    # The WORM source blob is rfc8785.dumps(commitment); it must serialize and be byte-stable.
    c = build_commitment(
        target_value=Decimal("98"),
        unit="%",
        direction=HI,
        due_date=datetime.date(2026, 12, 31),
        at_risk_threshold=None,
        baseline_value=None,
        policy_id=None,
    )
    assert rfc8785.dumps(c) == rfc8785.dumps(c)
    # decimals are strings, never floats (exact, reproducible bytes)
    assert b"98.0" not in rfc8785.dumps(c)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd apps/api && uv run pytest tests/unit/test_objective_commitment.py -v`
Expected: FAIL — `ModuleNotFoundError: easysynq_api.domain.objectives.commitment`.

- [ ] **Step 3: Write the implementation**

Create `apps/api/src/easysynq_api/domain/objectives/commitment.py`:

```python
"""The versioned Quality-Objective commitment (S-obj-3, clause 6.2).

``build_commitment`` produces the canonical dict that is BOTH the version's WORM source blob
(``rfc8785.dumps`` — JCS) AND the ``metadata_snapshot.objective_commitment`` fold, so the bytes and
the snapshot can never diverge (the S-rec-3 invariant). Decimals serialize as STRINGS (never float)
so the WORM bytes are exact + reproducible. ``current_value`` is the operational rollup OUTSIDE the
version and is deliberately NOT part of the commitment.
"""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal
from typing import Any

from ...db.models._objective_enums import ObjectiveDirection


def build_commitment(
    *,
    target_value: Decimal,
    unit: str,
    direction: ObjectiveDirection,
    due_date: datetime.date,
    at_risk_threshold: Decimal | None,
    baseline_value: Decimal | None,
    policy_id: uuid.UUID | None,
) -> dict[str, Any]:
    return {
        "target_value": str(target_value),
        "unit": unit,
        "direction": direction.value,
        "due_date": due_date.isoformat(),
        "at_risk_threshold": str(at_risk_threshold) if at_risk_threshold is not None else None,
        "baseline_value": str(baseline_value) if baseline_value is not None else None,
        "policy_id": str(policy_id) if policy_id is not None else None,
    }
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd apps/api && uv run pytest tests/unit/test_objective_commitment.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/easysynq_api/domain/objectives/commitment.py apps/api/tests/unit/test_objective_commitment.py
git commit -m "feat(s-obj-3): build_commitment — the canonical objective commitment dict"
```

---

### Task 2: `_snapshot` gains the `objective_commitment` kwarg

**Files:**
- Modify: `apps/api/src/easysynq_api/services/vault/service.py:93-121` (`_snapshot`)
- Test: `apps/api/tests/unit/test_objective_commitment.py` (append)

- [ ] **Step 1: Write the failing test (append to the existing file)**

Append to `apps/api/tests/unit/test_objective_commitment.py`:

```python
from types import SimpleNamespace

from easysynq_api.db.models._vault_enums import Classification
from easysynq_api.services.vault.service import _snapshot


def _fake_doc() -> SimpleNamespace:
    return SimpleNamespace(
        identifier="OBJ-001",
        title="On-time delivery",
        document_type_id=uuid.uuid4(),
        owner_user_id=uuid.uuid4(),
        folder_path=None,
        classification=Classification.Internal,
        framework_id=uuid.uuid4(),
        review_period_months=24,
        acknowledgement_required=False,
    )


def test_snapshot_adds_objective_commitment_only_when_passed() -> None:
    doc = _fake_doc()
    plain = _snapshot(doc)
    assert "objective_commitment" not in plain  # ordinary docs are byte-untouched
    assert "field_schema" not in plain
    commitment = {"target_value": "98", "unit": "%", "direction": "HIGHER_IS_BETTER"}
    withc = _snapshot(doc, objective_commitment=commitment)
    assert withc["objective_commitment"] == commitment
    # the base shape is otherwise identical
    assert {k: withc[k] for k in plain} == plain
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd apps/api && uv run pytest tests/unit/test_objective_commitment.py::test_snapshot_adds_objective_commitment_only_when_passed -v`
Expected: FAIL — `_snapshot() got an unexpected keyword argument 'objective_commitment'`.

- [ ] **Step 3: Edit `_snapshot`**

In `apps/api/src/easysynq_api/services/vault/service.py`, change the `_snapshot` signature and tail. Replace:

```python
def _snapshot(
    doc: DocumentedInformation,
    *,
    field_schema: dict[str, Any] | None = None,
    distribution: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
```

with:

```python
def _snapshot(
    doc: DocumentedInformation,
    *,
    field_schema: dict[str, Any] | None = None,
    distribution: list[dict[str, Any]] | None = None,
    objective_commitment: dict[str, Any] | None = None,
) -> dict[str, Any]:
```

and replace the tail:

```python
    if field_schema is not None:
        snap["field_schema"] = field_schema
    return snap
```

with:

```python
    if field_schema is not None:
        snap["field_schema"] = field_schema
    # S-obj-3 (clause 6.2): the OBJ's versioned commitment is frozen here, the form_template
    # field_schema precedent — one optional kwarg, the shared body never branches on doc kind.
    if objective_commitment is not None:
        snap["objective_commitment"] = objective_commitment
    return snap
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd apps/api && uv run pytest tests/unit/test_objective_commitment.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/easysynq_api/services/vault/service.py apps/api/tests/unit/test_objective_commitment.py
git commit -m "feat(s-obj-3): _snapshot folds objective_commitment via one optional kwarg"
```

---

# Phase 2 — Backend submit (freeze + T2 + approval, integration-tested)

### Task 3: `checkin_objective_commitment` — the freeze

**Files:**
- Modify: `apps/api/src/easysynq_api/services/vault/service.py` (add a new function after `checkin_form_schema`)

This function mints the Draft version + WORM source blob but **flushes-not-commits** (it is a sub-step of submit, sharing the submit transaction). It is proven by the Task 5 integration test (a unit test cannot exercise MinIO WORM + the DB).

- [ ] **Step 1: Add the function**

In `apps/api/src/easysynq_api/services/vault/service.py`, immediately after `checkin_form_schema` (after its `return version`, before `def schema_from_version`), add:

```python
async def checkin_objective_commitment(
    session: AsyncSession,
    sink: VaultAuditSink,
    actor: AppUser,
    doc: DocumentedInformation,
    *,
    commitment: dict[str, Any],
    change_reason: str,
    change_significance: str,
) -> DocumentVersionModel:
    """Freeze a Quality Objective's ``commitment`` (a pre-built JSON-safe dict) into an immutable
    ``DocumentVersion`` (S-obj-3 — the ``checkin_form_schema`` precedent). The canonical-serialized
    commitment is the version's WORM source blob (server-side write — no client upload, ``application/
    json`` → ``no_controlled_rendition`` R26), and the SAME dict is pinned into ``metadata_snapshot``
    in one transaction. Unlike ``checkin_form_schema`` this FLUSHES (does not commit): the freeze is a
    sub-step of ``submit_objective_for_review``, which owns the submit/approval txn boundary."""
    if not change_reason or not change_reason.strip():
        raise ProblemException(
            status=422,
            code="validation_error",
            title="Check-in requires a change reason (INV-3)",
            errors=[{"field": "change_reason", "code": "required", "message": "must be non-empty"}],
        )
    try:
        significance = ChangeSignificance(change_significance)
    except ValueError as exc:
        raise ProblemException(
            status=422,
            code="validation_error",
            title="Check-in requires change_significance MAJOR or MINOR (INV-3)",
            errors=[{"field": "change_significance", "code": "invalid", "message": "MAJOR|MINOR"}],
        ) from exc

    payload = rfc8785.dumps(commitment)  # JCS — identical commitment → identical source blob
    sha = hashlib.sha256(payload).hexdigest()
    if await repository.get_blob(session, sha) is None:
        await storage.put_staging_bytes(payload, sha, content_type="application/json")
        promoted = await storage.finalize_worm(sha)
        if not promoted.exists:  # pragma: no cover - defensive (we just wrote it)
            raise ProblemException(
                status=500, code="internal_error", title="Commitment object upload failed"
            )
        if promoted.retain_until is None:
            raise ProblemException(
                status=423, code="worm_required", title="Commitment object is not WORM-locked"
            )
        await session.execute(
            pg_insert(Blob)
            .values(
                sha256=sha,
                org_id=actor.org_id,
                size_bytes=promoted.size or len(payload),
                mime_type="application/json",
                bucket=get_settings().s3_bucket_documents,
                object_key=sha,
                worm_locked=True,
                worm_retain_until=promoted.retain_until,
            )
            .on_conflict_do_nothing(index_elements=["sha256"])
        )
        await session.flush()

    seq = await repository.next_version_seq(session, doc.id)
    dist_snap = await _distribution_snapshot(session, doc.id)
    version = DocumentVersionModel(
        org_id=actor.org_id,
        document_id=doc.id,
        version_seq=seq,
        revision_label=revision_label(seq),
        change_significance=significance,
        change_reason=change_reason.strip(),
        version_state=VersionState.Draft,
        source_blob_sha256=sha,
        # SAME commitment dict → bytes ≡ snapshot.
        metadata_snapshot=_snapshot(doc, objective_commitment=commitment, distribution=dist_snap),
        author_user_id=actor.id,
        created_by=actor.id,
    )
    session.add(version)
    _emit(
        session,
        sink,
        "CHECKIN",
        actor,
        "document_version",
        version.id,
        identifier=doc.identifier,
        reason=change_reason.strip(),
    )
    await session.flush()  # NOT commit — submit_objective_for_review owns the txn boundary
    return version
```

- [ ] **Step 2: Static check**

Run: `/check-api`
Expected: PASS (ruff + mypy clean; no new unit tests fail). This function is exercised by Task 5's integration test.

- [ ] **Step 3: Commit**

```bash
git add apps/api/src/easysynq_api/services/vault/service.py
git commit -m "feat(s-obj-3): checkin_objective_commitment — freeze the commitment into a WORM version"
```

---

### Task 4: `submit_objective_for_review` service

**Files:**
- Create: `apps/api/src/easysynq_api/services/objectives/lifecycle.py`
- Modify: `apps/api/src/easysynq_api/services/objectives/__init__.py`

- [ ] **Step 1: Create the service**

Create `apps/api/src/easysynq_api/services/objectives/lifecycle.py`:

```python
"""Quality-Objective lifecycle (S-obj-3, clause 6.2). ``submit_objective_for_review`` folds a
commitment-freeze + the T2 transition + the approval-workflow instantiation into ONE transaction,
then the OBJ rides the generic DOCUMENT decide leg (approve) + ``release`` cutover, unchanged."""

from __future__ import annotations

from easysynq_api.db.models._vault_enums import DocumentCurrentState
from easysynq_api.db.models.app_user import AppUser
from easysynq_api.db.models.documented_information import DocumentedInformation
from easysynq_api.db.models.quality_objective import QualityObjective
from easysynq_api.domain.objectives.commitment import build_commitment
from easysynq_api.problems import ProblemException
from easysynq_api.services.vault import VaultAuditSink, repository as vault_repo
from easysynq_api.services.vault.lifecycle import audit_transition, submit_review
from easysynq_api.services.vault.service import checkin_objective_commitment
from easysynq_api.services.workflow import instantiate_approval
from sqlalchemy.ext.asyncio import AsyncSession


async def submit_objective_for_review(
    session: AsyncSession,
    vault_sink: VaultAuditSink,
    actor: AppUser,
    doc: DocumentedInformation,
    qo: QualityObjective,
) -> DocumentedInformation:
    """Freeze the commitment (first submit only) → T2 (Draft→InReview) → instantiate the approval
    workflow → audit, all in one transaction. ``doc`` MUST be loaded ``with_for_update`` +
    ``populate_existing`` (the authz resolver already identity-mapped it — the S-drift-1 trap)."""
    if doc.current_state is not DocumentCurrentState.Draft:
        raise ProblemException(
            status=409,
            code="conflict",
            title="Objective is not in Draft",
            detail=f"current_state is {doc.current_state.value}",
        )
    # Freeze a new version IFF none exists yet (first submit). A re-submit after request_changes
    # advances the existing latest Draft version unchanged — there is no commitment-edit path in v1,
    # so re-freezing identical bytes would be a pointless duplicate version.
    if await vault_repo.latest_version(session, doc.id) is None:
        commitment = build_commitment(
            target_value=qo.target_value,
            unit=qo.unit,
            direction=qo.direction,
            due_date=qo.due_date,
            at_risk_threshold=qo.at_risk_threshold,
            baseline_value=qo.baseline_value,
            policy_id=qo.policy_id,
        )
        await checkin_objective_commitment(
            session,
            vault_sink,
            actor,
            doc,
            commitment=commitment,
            change_reason="Objective commitment submitted for review",
            change_significance="MAJOR",
        )
    result = await submit_review(session, actor, doc)
    await instantiate_approval(session, result.doc, actor)
    audit_transition(session, vault_sink, result, actor)
    await session.commit()
    return result.doc
```

- [ ] **Step 2: Export it**

In `apps/api/src/easysynq_api/services/objectives/__init__.py`, add the import + `__all__` entry. Change the `from .service import (...)` block to also import from `lifecycle`, by inserting after the existing `from .service import (...)` block:

```python
from .lifecycle import submit_objective_for_review
```

and add `"submit_objective_for_review",` to the `__all__` list (keep it alphabetically ordered — it goes after `"remove_objective_plan",`).

- [ ] **Step 3: Static check**

Run: `/check-api`
Expected: PASS (ruff/mypy clean). Note: ruff may want the `sqlalchemy.ext.asyncio` import ordered; if it flags import order, run `cd apps/api && uv run ruff check --fix .` and re-run.

- [ ] **Step 4: Commit**

```bash
git add apps/api/src/easysynq_api/services/objectives/lifecycle.py apps/api/src/easysynq_api/services/objectives/__init__.py
git commit -m "feat(s-obj-3): submit_objective_for_review — freeze + T2 + instantiate approval (one txn)"
```

---

### Task 5: `POST /objectives/{id}/submit-review` endpoint + integration test

**Files:**
- Modify: `apps/api/src/easysynq_api/api/objectives.py` (imports + a `_load_objective_doc` helper + the endpoint)
- Create: `apps/api/tests/integration/test_objective_lifecycle.py`

- [ ] **Step 1: Write the failing integration test**

Create `apps/api/tests/integration/test_objective_lifecycle.py`:

```python
"""S-obj-3 integration: the objective lifecycle (submit → approve → release → Effective), the
6.2-★ flip to COVERED, and the new reads. Grants are SYSTEM-scope PermissionOverrides on JIT users
keyed by keycloak_subject (the test_quality_objectives / s5_helpers precedent)."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from easysynq_api.db.models._signature_enums import SignatureMeaning
from easysynq_api.db.models._vault_enums import DocumentCurrentState, VersionState
from easysynq_api.db.models.document_version import DocumentVersion
from easysynq_api.db.models.documented_information import DocumentedInformation
from easysynq_api.db.models.signature_event import SignatureEvent as SignatureEventRow
from easysynq_api.db.session import get_sessionmaker

from . import s5_helpers as s5
from .test_quality_objectives import _grant
from .test_vault import _auth

pytestmark = pytest.mark.integration

_OBJ_KEYS = ("objective.read", "objective.manage", "kpi.read", "kpi.record")


async def _create_objective(client: AsyncClient, h: dict[str, str], title: str) -> str:
    r = await client.post(
        "/api/v1/objectives",
        headers=h,
        json={
            "title": title,
            "target_value": "98",
            "unit": "%",
            "direction": "HIGHER_IS_BETTER",
            "due_date": "2026-12-31",
            "at_risk_threshold": "95",
            "baseline_value": "90",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_submit_freezes_the_commitment_and_enters_review(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"obj-sub-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _OBJ_KEYS)
    oid = await _create_objective(app_client, h, "On-time delivery")

    r = await app_client.post(f"/api/v1/objectives/{oid}/submit-review", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["current_state"] == "InReview"

    # a Draft version exists with the frozen commitment in its metadata_snapshot
    async with get_sessionmaker()() as s:
        v = (
            await s.execute(
                select(DocumentVersion).where(DocumentVersion.document_id == uuid.UUID(oid))
            )
        ).scalar_one()
        commitment = (v.metadata_snapshot or {}).get("objective_commitment")
        assert commitment is not None
        assert commitment["target_value"] == "98"
        assert commitment["unit"] == "%"
        assert commitment["direction"] == "HIGHER_IS_BETTER"
        assert commitment["at_risk_threshold"] == "95"


async def test_submit_requires_objective_manage(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    owner = f"obj-own-{uuid.uuid4()}"
    ho = _auth(token_factory, owner)
    await _grant(owner, _OBJ_KEYS)
    oid = await _create_objective(app_client, ho, "Needs manage")

    # a reader without objective.manage cannot submit
    reader = f"obj-rdr-{uuid.uuid4()}"
    hr = _auth(token_factory, reader)
    await _grant(reader, ("objective.read",))
    r = await app_client.post(f"/api/v1/objectives/{oid}/submit-review", headers=hr)
    assert r.status_code == 403, r.text


async def test_submit_twice_is_a_conflict(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"obj-dbl-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _OBJ_KEYS)
    oid = await _create_objective(app_client, h, "Submit once")
    assert (await app_client.post(f"/api/v1/objectives/{oid}/submit-review", headers=h)).status_code == 200
    again = await app_client.post(f"/api/v1/objectives/{oid}/submit-review", headers=h)
    assert again.status_code == 409, again.text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd apps/api && uv run pytest tests/integration/test_objective_lifecycle.py::test_submit_freezes_the_commitment_and_enters_review -v -m integration`
Expected: FAIL with 404/405 (the route does not exist yet). *(If Docker testcontainers are unavailable locally, this is validated in CI; proceed to implement and rely on `/check-api` + Task 18's live smoke locally.)*

- [ ] **Step 3: Add the imports to `api/objectives.py`**

In `apps/api/src/easysynq_api/api/objectives.py`, extend the imports. After the existing `from sqlalchemy.ext.asyncio import AsyncSession` line add:

```python
from sqlalchemy import select
```

Add to the existing model imports (after `from ..db.models.quality_objective import QualityObjective`):

```python
from ..db.models.document_type import DocumentType
from ..db.models.document_version import DocumentVersion
from ..db.models.documented_information import DocumentedInformation
from ..db.models._workflow_enums import WorkflowSubjectType
from ..db.models.workflow import Task, WorkflowInstance
```

Extend the `from ..domain.authz import ResourceContext` import to:

```python
from ..domain.authz import RequestContext, ResourceContext, authorize
```

Extend the services imports. After `from ..services.authz import AuthzAuditSink, enforce, get_authz_audit_sink, require` add:

```python
from ..services.authz import gather_grants
from ..services.authz.repository import gather_sod_constraints, get_allow_approver_release
```

Extend the objectives-service import block to also import `current_effective_policy` and `submit_objective_for_review`:

```python
from ..services.objectives import (
    add_objective_plan,
    create_objective,
    current_effective_policy,
    get_objective,
    list_measurements,
    list_objectives,
    list_plans,
    record_measurement,
    remove_objective_plan,
    submit_objective_for_review,
)
```

Extend the vault import + add workflow/release imports. Replace `from ..services.vault import VaultAuditSink, get_vault_audit_sink` with:

```python
from ..services.vault import (
    SignatureEventSink,
    VaultAuditSink,
    get_vault_audit_sink,
    get_vault_signature_sink,
    release,
)
from ..services.vault.release_scope import enrich_release_sod_scope
from ..services.workflow import repository as wf_repo
```

> **Verify (Step 8):** confirm `SignatureEventSink`, `get_vault_signature_sink`, and `release` are exported from `services/vault/__init__.py` (documents.py imports them; if they live in a submodule, import from there — e.g. `from ..services.vault.lifecycle import release`). `mypy` in `/check-api` will tell you.

- [ ] **Step 4: Add the `_load_objective_doc` helper**

In `api/objectives.py`, after the `_objective_scope` helper (before the `_objective_read = require(...)` line), add:

```python
async def _load_objective_doc(
    session: AsyncSession, caller: AppUser, objective_id: uuid.UUID, *, for_update: bool = False
) -> tuple[DocumentedInformation, QualityObjective]:
    """Load the objective's base document + satellite, 404 if it isn't an OBJ in the caller's org.
    ``for_update`` takes the row lock + ``populate_existing`` (the authz resolver already
    session.get-loaded the satellite — the S-drift-1 identity-map staleness trap)."""
    if for_update:
        doc = (
            await session.execute(
                select(DocumentedInformation)
                .where(DocumentedInformation.id == objective_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
    else:
        doc = await session.get(DocumentedInformation, objective_id)
    qo = await session.get(QualityObjective, objective_id)
    if doc is None or qo is None or doc.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="Objective not found")
    return doc, qo
```

- [ ] **Step 5: Add the submit endpoint**

In `api/objectives.py`, after the `get_objective_endpoint` (the `GET /objectives/{objective_id}` handler), add:

```python
@router.post("/objectives/{objective_id}/submit-review")
async def submit_objective_endpoint(
    objective_id: uuid.UUID,
    caller: AppUser = Depends(_objective_manage_path),
    session: AsyncSession = Depends(get_session),
    vault_sink: VaultAuditSink = Depends(get_vault_audit_sink),
) -> dict[str, Any]:
    # FOR UPDATE + populate_existing serializes concurrent submits and dodges the stale-identity-map
    # trap; submit_objective_for_review freezes the commitment, runs T2, instantiates approval, and
    # commits atomically. Approval then routes through POST /tasks/{id}/decision (DOCUMENT leg).
    doc, qo = await _load_objective_doc(session, caller, objective_id, for_update=True)
    await submit_objective_for_review(session, vault_sink, caller, doc, qo)
    row = await get_objective(session, objective_id)
    assert row is not None  # just mutated it
    qo2, ident, title, state = row
    return _objective(qo2, identifier=ident, title=title, current_state=state, today=_today())
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd apps/api && uv run pytest tests/integration/test_objective_lifecycle.py -k submit -v -m integration`
Expected: PASS (3 submit tests) — or validated in CI if Docker is unavailable locally.

- [ ] **Step 7: Static check**

Run: `/check-api`
Expected: PASS (ruff + mypy + unit). Fix any import-path issues flagged by mypy (Step 3's verify note).

- [ ] **Step 8: Commit**

```bash
git add apps/api/src/easysynq_api/api/objectives.py apps/api/tests/integration/test_objective_lifecycle.py
git commit -m "feat(s-obj-3): POST /objectives/{id}/submit-review — freeze + submit for review"
```

---

# Phase 3 — Backend release (the cutover, integration-tested)

### Task 6: `POST /objectives/{id}/release` + the create→submit→approve→release→COVERED proof

**Files:**
- Modify: `apps/api/src/easysynq_api/api/objectives.py` (release scope helper + endpoint)
- Modify: `apps/api/tests/integration/test_objective_lifecycle.py` (append the end-to-end test)

- [ ] **Step 1: Write the failing test (append)**

Append to `apps/api/tests/integration/test_objective_lifecycle.py`:

```python
async def _clause_6_2_row(client: AsyncClient, h: dict[str, str]) -> dict:
    body = (await client.get("/api/v1/reports/compliance-checklist", headers=h)).json()
    return next(r for r in body["rows"] if r["number"] == "6.2")


async def test_full_lifecycle_to_effective_flips_6_2_covered(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    salt = uuid.uuid4().hex[:8]
    submitter, approver, releaser = f"obj-sm-{salt}", f"obj-ap-{salt}", f"obj-rl-{salt}"
    hs, hap, hrl = (
        _auth(token_factory, submitter),
        _auth(token_factory, approver),
        _auth(token_factory, releaser),
    )
    # submitter owns + submits the objective; approver is in the document_approval pool via the role;
    # releaser is a THIRD party holding document.release (SoD-2: author≠releaser, approver≠releaser).
    await _grant(submitter, _OBJ_KEYS)
    await _grant(submitter, ("report.compliance_checklist.read",))
    await s5.grant_role(approver, "Approver")
    await _grant(releaser, ("document.release", "document.read", "document.read_draft"))

    before = await _clause_6_2_row(app_client, hs)
    eff0 = before["effective_count"]

    oid = await _create_objective(app_client, hs, "Lifecycle objective")
    assert (await app_client.post(f"/api/v1/objectives/{oid}/submit-review", headers=hs)).status_code == 200

    task_id = await s5.task_for_doc(oid)
    dec = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hap, json={"outcome": "approve"}
    )
    assert dec.status_code == 200, dec.text
    assert dec.json()["signature_event"]["meaning"] == "approval"

    rel = await app_client.post(f"/api/v1/objectives/{oid}/release", headers=hrl)
    assert rel.status_code == 200, rel.text
    assert rel.json()["current_state"] == "Effective"

    # the released version is Effective + carries a release signature; the doc points at it
    async with get_sessionmaker()() as s:
        doc = await s.get(DocumentedInformation, uuid.UUID(oid))
        assert doc.current_state is DocumentCurrentState.Effective
        assert doc.current_effective_version_id is not None
        v = await s.get(DocumentVersion, doc.current_effective_version_id)
        assert v.version_state is VersionState.Effective
        n = (
            await s.execute(
                select(func.count())
                .select_from(SignatureEventRow)
                .where(
                    SignatureEventRow.signed_object_id == v.id,
                    SignatureEventRow.meaning == SignatureMeaning.release,
                )
            )
        ).scalar_one()
        assert n == 1

    # the 6.2 ★ checklist node now counts this Effective objective (delta-asserted — shared DB)
    after = await _clause_6_2_row(app_client, hs)
    assert after["effective_count"] == eff0 + 1
    assert after["status"] == "COVERED"


async def test_author_cannot_release_their_own_objective(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    salt = uuid.uuid4().hex[:8]
    submitter, approver = f"obj-sa-{salt}", f"obj-aa-{salt}"
    hs, hap = _auth(token_factory, submitter), _auth(token_factory, approver)
    await _grant(submitter, _OBJ_KEYS)
    await _grant(submitter, ("document.release", "document.read"))  # holds the key but is the author
    await s5.grant_role(approver, "Approver")
    oid = await _create_objective(app_client, hs, "SoD objective")
    await app_client.post(f"/api/v1/objectives/{oid}/submit-review", headers=hs)
    task_id = await s5.task_for_doc(oid)
    await app_client.post(f"/api/v1/tasks/{task_id}/decision", headers=hap, json={"outcome": "approve"})
    # SoD-2: the version author cannot release their own objective → 403 sod_violation
    rel = await app_client.post(f"/api/v1/objectives/{oid}/release", headers=hs)
    assert rel.status_code == 403, rel.text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd apps/api && uv run pytest tests/integration/test_objective_lifecycle.py::test_full_lifecycle_to_effective_flips_6_2_covered -v -m integration`
Expected: FAIL (the `/release` route does not exist) — or validated in CI.

- [ ] **Step 3: Add the release-scope helper + endpoint**

In `api/objectives.py`, add the release-scope helper after `_load_objective_doc`:

```python
async def _objective_release_scope(
    session: AsyncSession, doc: DocumentedInformation
) -> ResourceContext:
    """Release scope = the objective's document scope + the SoD-2 inputs for the version the cutover
    will promote (the latest Approved): its author + approval signers. Mirrors the document
    _release_scope (documents.py)."""
    level: str | None = None
    if doc.document_type_id:
        dt = await session.get(DocumentType, doc.document_type_id)
        level = dt.document_level.value if dt else None
    base = ResourceContext(
        artifact_id=str(doc.id), folder_path=doc.folder_path, document_level=level
    )
    return await enrich_release_sod_scope(session, base, doc.id, None)
```

Then add the endpoint after `submit_objective_endpoint`:

```python
@router.post("/objectives/{objective_id}/release")
async def release_objective_endpoint(
    objective_id: uuid.UUID,
    request: Request,
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    authz_sink: AuthzAuditSink = Depends(get_authz_audit_sink),
    vault_sink: VaultAuditSink = Depends(get_vault_audit_sink),
    sig_sink: SignatureEventSink = Depends(get_vault_signature_sink),
) -> dict[str, Any]:
    # Enforce document.release imperatively over the SoD-2-enriched scope (author/approver≠releaser),
    # then the shared release() runs the INV-1 SERIALIZABLE cutover in its own session. An OBJ shares
    # the documented_information id, so the kind-agnostic cutover drives it Effective + signs release.
    doc, _ = await _load_objective_doc(session, caller, objective_id)
    resource = await _objective_release_scope(session, doc)
    await enforce(
        session, authz_sink, request, caller, "document.release", resource, sig_hook=True
    )
    await release(caller, objective_id, vault_sink, sig_sink)
    # release() committed in its own session; drop the request session's identity-map cache so the
    # re-read reflects Effective (the document release endpoint returns release()'s detached doc; the
    # objective serializer additionally needs the satellite, so we re-read fresh).
    session.expire_all()
    row = await get_objective(session, objective_id)
    assert row is not None
    qo, ident, title, state = row
    return _objective(qo, identifier=ident, title=title, current_state=state, today=_today())
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd apps/api && uv run pytest tests/integration/test_objective_lifecycle.py -v -m integration`
Expected: PASS (all lifecycle tests) — or validated in CI; locally confirm via Task 18 smoke.

- [ ] **Step 5: Static check**

Run: `/check-api`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/api/src/easysynq_api/api/objectives.py apps/api/tests/integration/test_objective_lifecycle.py
git commit -m "feat(s-obj-3): POST /objectives/{id}/release — INV-1 cutover, flips 6.2 COVERED"
```

---

# Phase 4 — Backend read endpoints + serializer additions

### Task 7: `GET /objectives/{id}/approval` (the stepper read)

**Files:**
- Modify: `apps/api/src/easysynq_api/api/objectives.py` (instance serializers + endpoint)
- Modify: `apps/api/tests/integration/test_objective_lifecycle.py` (append)

- [ ] **Step 1: Write the failing test (append)**

Append to `apps/api/tests/integration/test_objective_lifecycle.py`:

```python
async def test_approval_read_is_null_before_submit_then_present(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"obj-apr-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _OBJ_KEYS)
    oid = await _create_objective(app_client, h, "Approval read")

    # null before submit (no cycle)
    pre = await app_client.get(f"/api/v1/objectives/{oid}/approval", headers=h)
    assert pre.status_code == 200, pre.text
    assert pre.json() is None

    await app_client.post(f"/api/v1/objectives/{oid}/submit-review", headers=h)
    post = await app_client.get(f"/api/v1/objectives/{oid}/approval", headers=h)
    assert post.status_code == 200, post.text
    inst = post.json()
    assert inst["subject_type"] == "DOCUMENT"
    assert inst["subject_id"] == oid
    assert any(t["type"] == "APPROVE" for t in inst["tasks"])
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd apps/api && uv run pytest tests/integration/test_objective_lifecycle.py -k approval_read -v -m integration`
Expected: FAIL (route missing) — or CI.

- [ ] **Step 3: Add the local instance/task serializers + endpoint**

In `api/objectives.py`, add these serializers in the `# --- serializers ---` section (after `_objective`). They mirror `api/workflow.py`'s `_instance`/`_task` (kept local to avoid a cross-router private import; the FE `WorkflowInstance`/`Task` types pin the shape):

```python
def _approval_task(t: Task) -> dict[str, Any]:
    return {
        "id": str(t.id),
        "instance_id": str(t.instance_id),
        "stage_key": t.stage_key,
        "type": t.type.value,
        "state": t.state.value,
        "assignee_user_id": str(t.assignee_user_id) if t.assignee_user_id else None,
        "candidate_pool": t.candidate_pool,
        "action_expected": t.action_expected,
        "due_at": t.due_at.isoformat() if t.due_at else None,
    }


def _approval_instance(i: WorkflowInstance, tasks: list[Task]) -> dict[str, Any]:
    return {
        "id": str(i.id),
        "definition_id": str(i.definition_id),
        "definition_version": i.definition_version,
        "subject_type": i.subject_type.value,
        "subject_id": str(i.subject_id),
        "current_state": i.current_state,
        "started_at": i.started_at.isoformat() if i.started_at else None,
        "revision": i.revision,
        "tasks": [_approval_task(t) for t in tasks],
    }
```

Then add the endpoint (place it among the `{objective_id}` sub-routes, e.g. after `get_objective_endpoint`):

```python
@router.get("/objectives/{objective_id}/approval")
async def get_objective_approval_endpoint(
    objective_id: uuid.UUID,
    caller: AppUser = Depends(_objective_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any] | None:
    """The objective's current approval cycle — the latest workflow instance + tasks, or ``null``
    before submit. Gated ``objective.read`` (the objective owner may hold no ``document.read``); the
    OBJ approval instance carries ``subject_type=DOCUMENT`` (instantiate_approval hardcodes it)."""
    qo = await session.get(QualityObjective, objective_id)
    if qo is None or qo.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="Objective not found")
    instance = await wf_repo.latest_instance_for_subject(
        session, caller.org_id, WorkflowSubjectType.DOCUMENT, objective_id
    )
    if instance is None:
        return None
    tasks = await wf_repo.list_instance_tasks(session, instance.id)
    return _approval_instance(instance, tasks)
```

> **Verify (Step 5):** confirm the `Task` model exposes `due_at` and `assignee_user_id` (it does — the workflow `_task` serializer + the MSW `docAckTask` fixture include them). If `Task.type`/`Task.state` are not enums with `.value`, adjust. mypy in `/check-api` flags mismatches.

- [ ] **Step 4: Run to verify it passes**

Run: `cd apps/api && uv run pytest tests/integration/test_objective_lifecycle.py -k approval_read -v -m integration`
Expected: PASS — or CI.

- [ ] **Step 5: Static check + commit**

Run: `/check-api` → PASS, then:

```bash
git add apps/api/src/easysynq_api/api/objectives.py apps/api/tests/integration/test_objective_lifecycle.py
git commit -m "feat(s-obj-3): GET /objectives/{id}/approval — objective.read-gated stepper read"
```

---

### Task 8: `_objective` serializer gains `capabilities` + `effective_from` (detail-only)

**Files:**
- Modify: `apps/api/src/easysynq_api/api/objectives.py` (serializer + `_objective_capabilities`/`_objective_effective_from` + the detail endpoint)
- Modify: `apps/api/tests/integration/test_objective_lifecycle.py` (append)

- [ ] **Step 1: Write the failing test (append)**

Append to `apps/api/tests/integration/test_objective_lifecycle.py`:

```python
async def test_detail_exposes_capabilities_for_the_manager(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"obj-cap-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _OBJ_KEYS)
    oid = await _create_objective(app_client, h, "Caps objective")
    detail = (await app_client.get(f"/api/v1/objectives/{oid}", headers=h)).json()
    assert detail["capabilities"]["submit"] is True  # holds objective.manage
    assert detail["capabilities"]["release"] is False  # no document.release
    assert detail["effective_from"] is None  # Draft, not yet effective


async def test_list_omits_capabilities(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"obj-lst-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _OBJ_KEYS)
    await _create_objective(app_client, h, "List objective")
    rows = (await app_client.get("/api/v1/objectives", headers=h)).json()["data"]
    assert all("capabilities" not in r for r in rows)  # detail-only, no per-row authz cost
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd apps/api && uv run pytest tests/integration/test_objective_lifecycle.py -k capabilities -v -m integration`
Expected: FAIL (no `capabilities` key) — or CI.

- [ ] **Step 3: Extend the `_objective` serializer**

In `api/objectives.py`, change the `_objective` signature to add the two optional kwargs, and restructure its return to assign `out` then conditionally add. Replace:

```python
def _objective(
    qo: QualityObjective,
    *,
    identifier: str,
    title: str,
    current_state: Any,
    today: datetime.date,
    plans: list[ObjectivePlan] | None = None,
) -> dict[str, Any]:
    rag = rag_status(
        current=qo.current_value,
        target=qo.target_value,
        direction=qo.direction,
        at_risk_threshold=qo.at_risk_threshold,
    )
    return {
        "id": str(qo.id),
```

with:

```python
def _objective(
    qo: QualityObjective,
    *,
    identifier: str,
    title: str,
    current_state: Any,
    today: datetime.date,
    plans: list[ObjectivePlan] | None = None,
    capabilities: dict[str, bool] | None = None,
    effective_from: str | None = None,
) -> dict[str, Any]:
    rag = rag_status(
        current=qo.current_value,
        target=qo.target_value,
        direction=qo.direction,
        at_risk_threshold=qo.at_risk_threshold,
    )
    out: dict[str, Any] = {
        "id": str(qo.id),
```

and replace the serializer tail:

```python
        "plans": [_plan(p) for p in (plans or [])],
    }
```

with:

```python
        "plans": [_plan(p) for p in (plans or [])],
    }
    # S-obj-3 (detail-only): the caller's lifecycle affordances + the effective date for the stepper.
    if capabilities is not None:
        out["capabilities"] = capabilities
    if effective_from is not None:
        out["effective_from"] = effective_from
    return out
```

- [ ] **Step 4: Add `_objective_capabilities` + `_objective_effective_from`**

In `api/objectives.py`, add after `_objective_release_scope`:

```python
async def _objective_capabilities(
    session: AsyncSession, caller: AppUser, doc: DocumentedInformation, qo: QualityObjective
) -> dict[str, bool]:
    """The caller's lifecycle affordances on this objective (detail-only). submit = objective.manage
    at the objective's scope (the _objective_scope rule); release = document.release + the SoD-2
    overlay over the version the cutover would promote. Mirrors documents._document_capabilities."""
    now = datetime.datetime.now(datetime.UTC)
    ctx = RequestContext(now=now, actor_user_id=str(caller.id))
    submit_scope = (
        ResourceContext(process_ids=frozenset({str(qo.process_id)}))
        if qo.process_id is not None
        else ResourceContext.system()
    )
    mgr_grants = await gather_grants(session, caller.id, caller.org_id, "objective.manage")
    submit_cap = authorize(mgr_grants, "objective.manage", submit_scope, ctx).allow

    level: str | None = None
    if doc.document_type_id:
        dt = await session.get(DocumentType, doc.document_type_id)
        level = dt.document_level.value if dt else None
    base = ResourceContext(
        artifact_id=str(doc.id), folder_path=doc.folder_path, document_level=level
    )
    release_scope = await enrich_release_sod_scope(session, base, doc.id, None)
    sod = await gather_sod_constraints(session, caller.org_id)
    allow_approver_release = await get_allow_approver_release(session, caller.org_id)
    rel_ctx = RequestContext(
        now=now, actor_user_id=str(caller.id), allow_approver_release=allow_approver_release
    )
    rel_grants = await gather_grants(session, caller.id, caller.org_id, "document.release")
    release_cap = authorize(
        rel_grants, "document.release", release_scope, rel_ctx, sig_hook=True, sod=sod
    ).allow
    return {"submit": submit_cap, "release": release_cap}


async def _objective_effective_from(
    session: AsyncSession, doc: DocumentedInformation
) -> str | None:
    if doc.current_effective_version_id is None:
        return None
    v = await session.get(DocumentVersion, doc.current_effective_version_id)
    return v.effective_from.isoformat() if v is not None and v.effective_from else None
```

- [ ] **Step 5: Wire them into the detail endpoint**

In `api/objectives.py`, replace the `get_objective_endpoint` body:

```python
    row = await get_objective(session, objective_id)
    if row is None:
        raise ProblemException(status=404, code="not_found", title="Objective not found")
    qo, ident, title, state = row
    plans = await list_plans(session, objective_id)
    return _objective(
        qo, identifier=ident, title=title, current_state=state, today=_today(), plans=plans
    )
```

with:

```python
    row = await get_objective(session, objective_id)
    if row is None:
        raise ProblemException(status=404, code="not_found", title="Objective not found")
    qo, ident, title, state = row
    plans = await list_plans(session, objective_id)
    doc = await session.get(DocumentedInformation, objective_id)
    assert doc is not None  # the satellite row exists, so the base must too
    caps = await _objective_capabilities(session, caller, doc, qo)
    eff = await _objective_effective_from(session, doc)
    return _objective(
        qo,
        identifier=ident,
        title=title,
        current_state=state,
        today=_today(),
        plans=plans,
        capabilities=caps,
        effective_from=eff,
    )
```

- [ ] **Step 6: Run to verify it passes + static check**

Run: `cd apps/api && uv run pytest tests/integration/test_objective_lifecycle.py -k capabilities -v -m integration` → PASS (or CI). Then `/check-api` → PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/api/src/easysynq_api/api/objectives.py apps/api/tests/integration/test_objective_lifecycle.py
git commit -m "feat(s-obj-3): objective detail exposes capabilities + effective_from (detail-only)"
```

---

### Task 9: `GET /objectives/policy` (the create-modal POL picker)

**Files:**
- Modify: `apps/api/src/easysynq_api/api/objectives.py` (endpoint, registered BEFORE `/objectives/{objective_id}`)
- Modify: `apps/api/tests/integration/test_objective_lifecycle.py` (append)

- [ ] **Step 1: Write the failing test (append)**

Append to `apps/api/tests/integration/test_objective_lifecycle.py`:

```python
async def test_policy_endpoint_returns_null_when_no_effective_policy(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    # A fresh install has the POL document_type but no Effective POL document → null (calm degrade).
    subject = f"obj-pol-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, ("objective.read",))
    r = await app_client.get("/api/v1/objectives/policy", headers=h)
    assert r.status_code == 200, r.text
    assert r.json() is None


async def test_policy_endpoint_requires_objective_read(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"obj-pol2-{uuid.uuid4()}"
    h = _auth(token_factory, subject)  # no grant
    r = await app_client.get("/api/v1/objectives/policy", headers=h)
    assert r.status_code == 403, r.text
```

> Note: this assumes the test DB has no Effective POL. If a sibling test ever releases a POL into the shared session DB, change the first assertion to `assert r.json() is None or set(r.json()) == {"id", "identifier", "title"}` (tolerate either). The shape proof is the second assertion's contract.

- [ ] **Step 2: Run to verify it fails**

Run: `cd apps/api && uv run pytest tests/integration/test_objective_lifecycle.py -k policy_endpoint -v -m integration`
Expected: FAIL (route missing) — or CI.

- [ ] **Step 3: Add the endpoint (before the `{objective_id}` route)**

In `api/objectives.py`, add this endpoint immediately after `scorecard_endpoint` and **before** `get_objective_endpoint` (route ordering — the literal `policy` must precede the `{objective_id}` str-convertor, the `scorecard` precedent):

```python
@router.get("/objectives/policy")
async def get_objective_policy_endpoint(
    caller: AppUser = Depends(_objective_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any] | None:
    """The current Effective Quality Policy (POL singleton, R25) for the create modal's policy link,
    or ``null`` when none is effective yet. Gated ``objective.read``."""
    pol = await current_effective_policy(session, caller.org_id)
    if pol is None:
        return None
    return {"id": str(pol.id), "identifier": pol.identifier, "title": pol.title}
```

- [ ] **Step 4: Run to verify it passes + static check**

Run: `cd apps/api && uv run pytest tests/integration/test_objective_lifecycle.py -k policy_endpoint -v -m integration` → PASS (or CI). Then `/check-api` → PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/easysynq_api/api/objectives.py apps/api/tests/integration/test_objective_lifecycle.py
git commit -m "feat(s-obj-3): GET /objectives/policy — the Effective POL singleton for the create modal"
```

---

# Phase 5 — Contracts

### Task 10: OpenAPI entries for the four new paths + `Objective` schema additions

**Files:**
- Modify: `packages/contracts/openapi.yaml`

- [ ] **Step 1: Document the new paths**

Add to `packages/contracts/openapi.yaml` (mirror the existing objectives paths' style; gates noted in `description`): `POST /objectives/{objective_id}/submit-review` (200 → `Objective`), `POST /objectives/{objective_id}/release` (200 → `Objective`), `GET /objectives/{objective_id}/approval` (200 → `WorkflowInstance` or `null`), `GET /objectives/policy` (200 → an object `{id, identifier, title}` or `null`). Reference the existing `WorkflowInstance` schema if present (it's used by `/documents/{id}/approval`); otherwise add a minimal inline schema matching `_approval_instance`.

- [ ] **Step 2: Extend the `Objective` schema**

In the `Objective` schema, add two optional properties:

```yaml
        capabilities:
          type: object
          description: Detail-only. The caller's lifecycle affordances (omitted on list/scorecard rows).
          properties:
            submit: { type: boolean }
            release: { type: boolean }
        effective_from:
          type: [string, "null"]
          format: date
          description: Detail-only. The Effective version's effective date (null until Effective).
```

- [ ] **Step 3: Lint + commit**

Run: `/check-contracts`
Expected: PASS (redocly clean).

```bash
git add packages/contracts/openapi.yaml
git commit -m "docs(s-obj-3): openapi — objective lifecycle endpoints + Objective.capabilities/effective_from"
```

---

# Phase 6 — Front-end

> All web tasks: run `/check-web` after each; component tests using a jest-dom matcher MUST `import { expect, it } from "vitest"` (the tsc-only trap). Pin MSW fixtures to the real serializer shapes above.

### Task 11: Types + MSW fixtures/handlers for the four endpoints

**Files:**
- Modify: `apps/web/src/lib/types.ts`
- Modify: `apps/web/src/test/msw/handlers.ts`

- [ ] **Step 1: Extend the types**

In `apps/web/src/lib/types.ts`, add to the `Objective` interface (after `plans: ObjectivePlan[];`):

```ts
  // S-obj-3 (detail-only; absent on list/scorecard rows):
  capabilities?: { submit: boolean; release: boolean };
  effective_from?: string | null;
```

Add a new exported type near the Objective family:

```ts
// GET /objectives/policy — the Effective Quality Policy singleton, or null.
export interface EffectivePolicy {
  id: string;
  identifier: string;
  title: string;
}
```

Verify the `DocumentVersion` type (the `useDocumentVersions` row) exposes `metadata_snapshot`; if not, add it (the `_version` serializer returns it):

```ts
  metadata_snapshot?: Record<string, unknown> | null;
```

- [ ] **Step 2: Add MSW fixtures + handlers**

In `apps/web/src/test/msw/handlers.ts`, near the objective fixtures (~928-1048) add an objective approval instance + a version-with-commitment + an objective approval task fixture:

```ts
const objectiveCommitment = {
  target_value: "95",
  unit: "%",
  direction: "HIGHER_IS_BETTER",
  due_date: "2026-12-31",
  at_risk_threshold: "90",
  baseline_value: "80",
  policy_id: null,
};

export const objectiveApprovalInstance = {
  id: "wfob1111-1111-1111-1111-111111111111",
  definition_id: "df000001-0001-0001-0001-000000000001",
  definition_version: 1,
  subject_type: "DOCUMENT",
  subject_id: OBJ_DETAIL_ID,
  current_state: "IN_APPROVAL",
  started_at: "2026-06-11T09:00:00+00:00",
  revision: 0,
  tasks: [
    {
      id: "tkob1111-1111-1111-1111-111111111111",
      instance_id: "wfob1111-1111-1111-1111-111111111111",
      stage_key: "quality_approval",
      type: "APPROVE",
      state: "PENDING",
      assignee_user_id: null,
      candidate_pool: ["bbbb1111-1111-1111-1111-111111111111"],
      action_expected: "approve",
      due_at: null,
    },
  ],
};

export const objectiveVersionWithCommitment = {
  id: "veob1111-1111-1111-1111-111111111111",
  document_id: OBJ_DETAIL_ID,
  version_seq: 1,
  revision_label: "Rev A",
  version_state: "InReview",
  source_blob_sha256: "obj-commitment-sha",
  metadata_snapshot: { objective_commitment: objectiveCommitment },
  created_at: "2026-06-11T09:00:00+00:00",
};

export const effectivePolicyFixture = {
  id: "po000001-0001-0001-0001-000000000001",
  identifier: "POL-001",
  title: "Quality Policy",
};
```

In the objectives handler block (~1050-1080) add these handlers (after the existing objectives handlers):

```ts
  http.get("/api/v1/objectives/policy", () => HttpResponse.json(effectivePolicyFixture)),
  http.get("/api/v1/objectives/:id/approval", () => HttpResponse.json(objectiveApprovalInstance)),
  http.post("/api/v1/objectives/:id/submit-review", () =>
    HttpResponse.json({ ...objectiveDetailFixture, current_state: "InReview" }),
  ),
  http.post("/api/v1/objectives/:id/release", () =>
    HttpResponse.json({ ...objectiveDetailFixture, current_state: "Effective" }),
  ),
```

> **Route order:** MSW matches in registration order — `/api/v1/objectives/policy` MUST be registered BEFORE `/api/v1/objectives/:id` (the existing detail handler), or `policy` resolves to `:id`. Place the `policy` handler above the existing `http.get("/api/v1/objectives/:id", ...)` line.

If the objective detail fixture should expose lifecycle affordances for the detail tests, give `objectiveDetailFixture` a `capabilities`/`effective_from`:

```ts
const objectiveDetailFixture: Objective = {
  ...objectiveFixtures[0]!,
  plans: objectivePlanFixtures,
  capabilities: { submit: true, release: false },
  effective_from: null,
} satisfies Objective;
```

- [ ] **Step 3: Static check + commit**

Run: `/check-web` → PASS (tsc + build clean; existing tests still green).

```bash
git add apps/web/src/lib/types.ts apps/web/src/test/msw/handlers.ts
git commit -m "feat(s-obj-3): web types + MSW fixtures for the objective lifecycle endpoints"
```

---

### Task 12: Web hooks + mutations

**Files:**
- Modify: `apps/web/src/features/objectives/hooks.ts`
- Modify: `apps/web/src/features/objectives/mutations.ts`
- Test: `apps/web/src/features/objectives/lifecycle.test.tsx` (create)

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/features/objectives/lifecycle.test.tsx`:

```tsx
import { expect, it } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { AuthContext } from "../../lib/auth";
import { TEST_AUTH } from "../../test/render";
import { useObjectiveApproval, useEffectivePolicy } from "./hooks";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <AuthContext.Provider value={TEST_AUTH}>{children}</AuthContext.Provider>
    </QueryClientProvider>
  );
}

it("useObjectiveApproval returns the instance + APPROVE task", async () => {
  const { result } = renderHook(
    () => useObjectiveApproval("ob000001-0001-0001-0001-000000000001"),
    { wrapper },
  );
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data?.subject_type).toBe("DOCUMENT");
  expect(result.current.data?.tasks?.some((t) => t.type === "APPROVE")).toBe(true);
});

it("useEffectivePolicy returns the policy", async () => {
  const { result } = renderHook(() => useEffectivePolicy(), { wrapper });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data?.identifier).toBe("POL-001");
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd apps/web && npx vitest run src/features/objectives/lifecycle.test.tsx`
Expected: FAIL — `useObjectiveApproval`/`useEffectivePolicy` not exported.

- [ ] **Step 3: Add the hooks**

In `apps/web/src/features/objectives/hooks.ts`, add to the imports:

```ts
import type {
  EffectivePolicy, MeasurementListResponse, Objective, ObjectiveScorecard, ProcessRow, WorkflowInstance,
} from "../../lib/types";
```

and append these hooks:

```ts
// GET /objectives/{id}/approval — the approval cycle for the detail-page stepper (objective.read).
export function useObjectiveApproval(id: string | null) {
  const api = useApi();
  const query = useQuery({
    queryKey: ["objective-approval", id],
    queryFn: () => api.get<WorkflowInstance | null>(`/api/v1/objectives/${id!}/approval`),
    enabled: id !== null,
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}

// GET /objectives/policy — the Effective Quality Policy for the create modal (or null).
export function useEffectivePolicy() {
  const api = useApi();
  const query = useQuery({
    queryKey: ["effective-policy"],
    queryFn: () => api.get<EffectivePolicy | null>("/api/v1/objectives/policy"),
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}
```

- [ ] **Step 4: Add the mutations**

In `apps/web/src/features/objectives/mutations.ts`, append:

```ts
function useInvalidateObjective(): (id: string) => void {
  const qc = useQueryClient();
  return (id: string) => {
    void qc.invalidateQueries({ queryKey: ["objective", id] });
    void qc.invalidateQueries({ queryKey: ["objective-approval", id] });
    void qc.invalidateQueries({ queryKey: ["objectives-scorecard"] });
  };
}

export function useSubmitObjectiveForReview() {
  const api = useApi();
  const invalidate = useInvalidateObjective();
  return useMutation({
    mutationFn: (id: string) => api.send<Objective>("POST", `/api/v1/objectives/${id}/submit-review`),
    onSuccess: (_d, id) => invalidate(id),
  });
}

export function useReleaseObjective() {
  const api = useApi();
  const invalidate = useInvalidateObjective();
  return useMutation({
    mutationFn: (id: string) => api.send<Objective>("POST", `/api/v1/objectives/${id}/release`, {}),
    onSuccess: (_d, id) => invalidate(id),
  });
}
```

- [ ] **Step 5: Run the test + static check + commit**

Run: `cd apps/web && npx vitest run src/features/objectives/lifecycle.test.tsx` → PASS. Then `/check-web` → PASS.

```bash
git add apps/web/src/features/objectives/hooks.ts apps/web/src/features/objectives/mutations.ts apps/web/src/features/objectives/lifecycle.test.tsx
git commit -m "feat(s-obj-3): web hooks/mutations — objective approval, policy, submit, release"
```

---

### Task 13: ObjectiveDetailPage lifecycle affordances

**Files:**
- Modify: `apps/web/src/features/objectives/ObjectiveDetailPage.tsx`
- Test: `apps/web/src/features/objectives/ObjectiveDetailPage.test.tsx` (append)

- [ ] **Step 1: Write the failing test (append to the existing test file)**

Append to `apps/web/src/features/objectives/ObjectiveDetailPage.test.tsx` (it already imports `expect, it`, `screen`, `waitFor`, `http`, `HttpResponse`, `server`):

```tsx
import userEvent from "@testing-library/user-event";

it("shows Submit for review when capabilities.submit and state is Draft", async () => {
  renderAt(ID);
  await waitFor(() => expect(screen.getByText("OBJ-001")).toBeInTheDocument());
  expect(screen.getByRole("button", { name: /submit for review/i })).toBeInTheDocument();
});

it("submits for review", async () => {
  const user = userEvent.setup();
  renderAt(ID);
  await waitFor(() => expect(screen.getByText("OBJ-001")).toBeInTheDocument());
  await user.click(screen.getByRole("button", { name: /submit for review/i }));
  // the detail re-fetch (now InReview via the submit handler) hides the Submit button + shows the stepper
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: /submit for review/i })).not.toBeInTheDocument(),
  );
  expect(await screen.findByLabelText("Approval progress")).toBeInTheDocument();
});
```

> Note: the submit handler returns `current_state: "InReview"`; on success the detail query is invalidated and re-fetched. To make the re-fetch reflect InReview, the test can also `server.use(http.get("/api/v1/objectives/:id", () => HttpResponse.json({ ...objectiveDetailFixture, current_state: "InReview" })))` AFTER clicking — simplest is to assert the Submit button disappears (state moved off Draft) and the stepper appears (approval present). If using the static detail fixture (Draft) makes the button persist, override the detail handler to InReview before asserting.

- [ ] **Step 2: Run to verify it fails**

Run: `cd apps/web && npx vitest run src/features/objectives/ObjectiveDetailPage.test.tsx`
Expected: FAIL — no Submit button.

- [ ] **Step 3: Rewrite `ObjectiveDetailPage.tsx`**

Replace `apps/web/src/features/objectives/ObjectiveDetailPage.tsx` with:

```tsx
import { Alert, Badge, Button, Card, Container, Group, Loader, Stack, Text, Title } from "@mantine/core";
import { useParams } from "react-router-dom";
import { useObjective, useObjectiveApproval } from "./hooks";
import { useReleaseObjective, useSubmitObjectiveForReview } from "./mutations";
import { CommitmentHero } from "./CommitmentHero";
import { PlansSection } from "./PlansSection";
import { MeasurementsSection } from "./MeasurementsSection";
import { ApprovalStepper } from "../document/ApprovalStepper";
import { useUserDirectory } from "../document/useUserDirectory";

export function ObjectiveDetailPage() {
  const { id = null } = useParams();
  const { data: o, isLoading, isError, forbidden } = useObjective(id);
  const { data: instance } = useObjectiveApproval(id);
  const { data: directory } = useUserDirectory();
  const submit = useSubmitObjectiveForReview();
  const release = useReleaseObjective();

  if (isLoading) {
    return (
      <Container size="lg" py="md">
        <Loader />
      </Container>
    );
  }

  if (isError || !o) {
    return (
      <Container size="lg" py="md">
        <Alert color={forbidden ? "gray" : "red"} title="Couldn't load this objective">
          {forbidden
            ? "You don't have access to this objective."
            : "It may have been removed, or you may not have access."}
        </Alert>
      </Container>
    );
  }

  const nameOf = (uid: string | null) =>
    (uid && directory?.find((u) => u.id === uid)?.display_name) || "—";
  const canSubmit = o.capabilities?.submit && o.current_state === "Draft";
  const canRelease = o.capabilities?.release && o.current_state === "Approved";

  return (
    <Container size="lg" py="md">
      <Stack gap="lg">
        <div>
          <Group gap="xs" mb={4} aria-label="Objective reference">
            <Text c="dimmed" size="sm" fw={500}>{o.identifier}</Text>
            <Badge color="gray" variant="light">{o.current_state}</Badge>
          </Group>
          <Title order={2}>{o.title}</Title>
        </div>
        <CommitmentHero objective={o} />
        {(canSubmit || canRelease || instance) && (
          <Card withBorder>
            <Stack gap="sm">
              <Text fw={600}>Lifecycle</Text>
              {instance && (
                <ApprovalStepper
                  instance={instance}
                  docState={o.current_state}
                  effectiveFrom={o.effective_from ?? null}
                  nameOf={nameOf}
                />
              )}
              {canSubmit && (
                <Group>
                  <Button
                    color="teal"
                    loading={submit.isPending}
                    onClick={() => id && submit.mutate(id)}
                  >
                    Submit for review
                  </Button>
                  <Text size="xs" c="dimmed">Freezes the commitment and starts approval.</Text>
                </Group>
              )}
              {canRelease && (
                <Group>
                  <Button
                    color="teal"
                    loading={release.isPending}
                    onClick={() => id && release.mutate(id)}
                  >
                    Release
                  </Button>
                  <Text size="xs" c="dimmed">Releases the Approved objective → Effective.</Text>
                </Group>
              )}
            </Stack>
          </Card>
        )}
        <PlansSection objectiveId={o.id} plans={o.plans} />
        <MeasurementsSection objectiveId={o.id} unit={o.unit} />
      </Stack>
    </Container>
  );
}
```

> **Verify:** `useUserDirectory` import path — confirm it is `../document/useUserDirectory` (the `ApprovalsTab` uses `useUserDirectory()`; match its import). If it lives elsewhere, fix the path. `ApprovalStepper`'s `docState` accepts the objective's `current_state` (the `ObjectiveState` union is identical to `DocumentCurrentState`).

- [ ] **Step 4: Run the test + static check + commit**

Run: `cd apps/web && npx vitest run src/features/objectives/ObjectiveDetailPage.test.tsx` → PASS. Then `/check-web` → PASS.

```bash
git add apps/web/src/features/objectives/ObjectiveDetailPage.tsx apps/web/src/features/objectives/ObjectiveDetailPage.test.tsx
git commit -m "feat(s-obj-3): objective detail — Submit / Approvals stepper / Release affordances"
```

---

### Task 14: ObjectiveCommitmentContext + ReviewApprovePage detection

**Files:**
- Create: `apps/web/src/features/review/ObjectiveCommitmentContext.tsx`
- Modify: `apps/web/src/features/review/ReviewApprovePage.tsx`
- Test: `apps/web/src/features/review/ObjectiveCommitmentContext.test.tsx` (create)

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/features/review/ObjectiveCommitmentContext.test.tsx`:

```tsx
import { expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { ObjectiveCommitmentContext } from "./ObjectiveCommitmentContext";

const commitment = {
  target_value: "95",
  unit: "%",
  direction: "HIGHER_IS_BETTER",
  due_date: "2026-12-31",
  at_risk_threshold: "90",
  baseline_value: "80",
  policy_id: null,
};

it("renders the frozen commitment under review", () => {
  render(
    <MantineProvider>
      <ObjectiveCommitmentContext commitment={commitment} title="On-time delivery" identifier="OBJ-001" />
    </MantineProvider>,
  );
  expect(screen.getByText("OBJ-001")).toBeInTheDocument();
  expect(screen.getByText("On-time delivery")).toBeInTheDocument();
  expect(screen.getByText("95")).toBeInTheDocument();
  expect(screen.getByText(/higher is better/i)).toBeInTheDocument();
  expect(screen.getByText("2026-12-31")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd apps/web && npx vitest run src/features/review/ObjectiveCommitmentContext.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Create `ObjectiveCommitmentContext.tsx`**

Create `apps/web/src/features/review/ObjectiveCommitmentContext.tsx`:

```tsx
import { Card, Stack, Table, Text } from "@mantine/core";

export interface ObjectiveCommitment {
  target_value: string;
  unit: string;
  direction: "HIGHER_IS_BETTER" | "LOWER_IS_BETTER";
  due_date: string;
  at_risk_threshold: string | null;
  baseline_value: string | null;
  policy_id: string | null;
}

const DIRECTION_LABEL: Record<ObjectiveCommitment["direction"], string> = {
  HIGHER_IS_BETTER: "Higher is better",
  LOWER_IS_BETTER: "Lower is better",
};

// S-obj-3: the approver's left column for an objective approval — the FROZEN commitment from the
// version's metadata_snapshot (read off useDocumentVersions, which the approver holds document.read
// for). Replaces the page redline (meaningless for a first-release JSON-source objective).
export function ObjectiveCommitmentContext({
  commitment,
  title,
  identifier,
}: {
  commitment: ObjectiveCommitment;
  title?: string;
  identifier?: string;
}) {
  const row = (label: string, value: string) => (
    <Table.Tr>
      <Table.Td><Text size="sm" c="dimmed">{label}</Text></Table.Td>
      <Table.Td>{value}</Table.Td>
    </Table.Tr>
  );
  return (
    <Card withBorder>
      <Stack gap="sm">
        <div>
          {identifier && <Text ff="monospace" size="sm">{identifier}</Text>}
          {title && <Text fw={600}>{title}</Text>}
          <Text size="xs" c="dimmed">The objective commitment you are approving.</Text>
        </div>
        <Table withRowBorders={false} aria-label="Objective commitment">
          <Table.Tbody>
            {row("Target", `${commitment.target_value} ${commitment.unit}`)}
            {row("Direction", DIRECTION_LABEL[commitment.direction])}
            {row("At-risk threshold", commitment.at_risk_threshold ?? "—")}
            {row("Baseline", commitment.baseline_value ?? "—")}
            {row("Due date", commitment.due_date)}
          </Table.Tbody>
        </Table>
      </Stack>
    </Card>
  );
}
```

- [ ] **Step 4: Wire detection into `ReviewApprovePage.tsx`**

In `apps/web/src/features/review/ReviewApprovePage.tsx`, add the import:

```tsx
import { ObjectiveCommitmentContext, type ObjectiveCommitment } from "./ObjectiveCommitmentContext";
```

In the DOCUMENT-leg `return` (the final `return` block), derive the commitment from the version-under-review and branch the left column. Replace:

```tsx
        <Grid.Col span={{ base: 12, md: 7 }}>
          <Stack gap="md">
            {doc && <Text fw={600}>{doc.title}</Text>}
            {docId && <VersionCompare documentId={docId} versions={versions ?? []} />}
          </Stack>
        </Grid.Col>
```

with:

```tsx
        <Grid.Col span={{ base: 12, md: 7 }}>
          <Stack gap="md">
            {doc && !objectiveCommitment && <Text fw={600}>{doc.title}</Text>}
            {objectiveCommitment ? (
              <ObjectiveCommitmentContext
                commitment={objectiveCommitment}
                title={doc?.title}
                identifier={doc?.identifier}
              />
            ) : (
              docId && <VersionCompare documentId={docId} versions={versions ?? []} />
            )}
          </Stack>
        </Grid.Col>
```

and add the derivation just above the final `return` (after the `decidedAlert` const, where `versions` is in scope):

```tsx
  // S-obj-3: an objective subject freezes its commitment into the version metadata_snapshot — render
  // that instead of a page redline. Detection keys on the snapshot field, never the document type.
  const objectiveCommitment =
    ((versions ?? [])
      .map((v) => (v.metadata_snapshot as { objective_commitment?: ObjectiveCommitment } | null)?.objective_commitment)
      .find(Boolean) as ObjectiveCommitment | undefined) ?? null;
```

- [ ] **Step 5: Run the tests + static check + commit**

Run: `cd apps/web && npx vitest run src/features/review/ObjectiveCommitmentContext.test.tsx src/features/review/ReviewApprovePage.test.tsx` → PASS (existing ReviewApprovePage tests must stay green — the DOCUMENT redline path is unchanged when no `objective_commitment` is present). Then `/check-web` → PASS.

```bash
git add apps/web/src/features/review/ObjectiveCommitmentContext.tsx apps/web/src/features/review/ObjectiveCommitmentContext.test.tsx apps/web/src/features/review/ReviewApprovePage.tsx
git commit -m "feat(s-obj-3): approver sees the frozen objective commitment in /tasks"
```

---

### Task 15: NewObjectiveModal policy picker

**Files:**
- Modify: `apps/web/src/features/objectives/NewObjectiveModal.tsx`
- Test: `apps/web/src/features/objectives/NewObjectiveModal.test.tsx` (append or create)

- [ ] **Step 1: Write the failing test**

Append to (or create) `apps/web/src/features/objectives/NewObjectiveModal.test.tsx`:

```tsx
import { expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "../../test/render";
import { NewObjectiveModal } from "./NewObjectiveModal";

it("offers the Effective Quality Policy as a link option", async () => {
  renderWithProviders(<NewObjectiveModal opened onClose={() => {}} onCreated={() => {}} />);
  // the policy select is populated from GET /objectives/policy (the MSW POL-001 fixture)
  await waitFor(() => expect(screen.getByText(/quality policy/i)).toBeInTheDocument());
});
```

> If the existing `NewObjectiveModal.test.tsx` already renders the modal, reuse its setup; this asserts the new policy affordance appears.

- [ ] **Step 2: Run to verify it fails**

Run: `cd apps/web && npx vitest run src/features/objectives/NewObjectiveModal.test.tsx`
Expected: FAIL — no policy element.

- [ ] **Step 3: Add the policy picker**

In `apps/web/src/features/objectives/NewObjectiveModal.tsx`:

Add to the imports:

```tsx
import { useEffectivePolicy } from "./hooks";
```

Add the hook + a state field inside the component (near the other `useState`s):

```tsx
  const { data: policy } = useEffectivePolicy();
  const [linkPolicy, setLinkPolicy] = useState(false);
```

Add `policy_id` to the `body` built in `submit()` (after `process_id: processId,`):

```tsx
      policy_id: linkPolicy && policy ? policy.id : null,
```

(Ensure `ObjectiveCreateBody` includes `policy_id?: string | null` — add it to the type in `lib/types.ts` if missing.)

Render the policy control inside the advanced `<Collapse>` `<Stack>` (after the process `<Select>`), so a missing policy degrades calmly:

```tsx
            {policy ? (
              <Checkbox
                label={`Consistent with ${policy.identifier} — ${policy.title}`}
                checked={linkPolicy}
                onChange={(e) => setLinkPolicy(e.currentTarget.checked)}
              />
            ) : (
              <Text size="xs" c="dimmed">No effective Quality Policy yet — you can link one later.</Text>
            )}
```

Add `Checkbox` to the Mantine import on line 2-3.

- [ ] **Step 4: Run the test + static check + commit**

Run: `cd apps/web && npx vitest run src/features/objectives/NewObjectiveModal.test.tsx` → PASS. Then `/check-web` → PASS.

```bash
git add apps/web/src/features/objectives/NewObjectiveModal.tsx apps/web/src/features/objectives/NewObjectiveModal.test.tsx apps/web/src/lib/types.ts
git commit -m "feat(s-obj-3): create modal — link the Effective Quality Policy"
```

---

# Phase 7 — Verify, review, smoke

### Task 16: Full local gates

- [ ] **Step 1:** Run `/check-api` → PASS (ruff + format + mypy-strict + unit).
- [ ] **Step 2:** Run `/check-web` → PASS (eslint + tsc + build + the full vitest suite; confirm the test-count delta vs 659 baseline).
- [ ] **Step 3:** Run `/check-contracts` → PASS (redocly).
- [ ] **Step 4:** Run `/check-migrations` → PASS (no migration added; `alembic check` clean, head stays `0049`).

### Task 17: diff-critic adversarial review

- [ ] **Step 1:** Run the `diff-critic` agent on the branch diff (`Agent` tool, `subagent_type: diff-critic`). Triage findings — fix genuine bugs (WORM/append-only, snapshot-cache, authz, the cross-session `expire_all` freshness, the freeze-iff-no-version guard). Re-run gates after fixes.

### Task 18: Live-stack smoke (the local backend proof)

- [ ] **Step 1:** Rebuild the changed services: `docker compose --env-file .env -f infra/compose/compose.yml -f infra/compose/compose.s.yml up -d --build api worker beat` (note: `--build` goes on the raw compose command, NOT `just up s`).
- [ ] **Step 2:** Grant the live `demo` `app_user` row (org **AHT**) the SYSTEM overrides: `objective.read`, `objective.manage`, `document.submit`, `document.approve`, `document.release`, `document.read`, `document.read_draft` (the content-read overrides are likely already present from prior smokes — confirm).
- [ ] **Step 3:** Backend heredoc smoke via the worker container: create an OBJ → `submit-review` → approve via `/tasks` decision → `release`; assert `current_state=Effective`, the version's `metadata_snapshot.objective_commitment` matches, a `signature_event(meaning=release)` exists, and `GET /reports/compliance-checklist` shows the 6.2 node `COVERED`.
- [ ] **Step 4:** FE smoke via Chrome MCP (localhost only; the owner performs the Keycloak login): open an objective detail page → Submit for review → see the stepper → approve in `/tasks` (the commitment context card renders) → Release → the objective reads Effective and the Home CHECK quadrant's 6.2 coverage reflects it.

### Task 19: PR + slice docs

- [ ] **Step 1:** Update `docs/slice-history.md` with the S-obj-3 entry (the lifecycle wiring, the freeze, the four endpoints, the FE affordances, the 6.2-COVERED close, the named deferrals: commitment-revision-edit + management-review-approval-routing). Add a `CLAUDE.md` Recent-learnings line. Record the api + web test deltas.
- [ ] **Step 2:** Open the PR (`/pr` or `gh pr create`), let CI go green (all five jobs), triage the Codex review (disregard multi-tenant nitpicks moot under D1; fix genuine bugs), squash-merge on the owner's OK.

---

## Self-Review (run before handing off to execution)

**Spec coverage:** s2 freeze → Tasks 1-3; s3 submit/release/approval/policy → Tasks 4-9; s4 capabilities/effective_from → Task 8; s5 authz (no new key) → rides existing keys (no task needed); s6 no migration → Task 16 step 4 confirms; s7 contracts → Task 10; s8 FE → Tasks 11-15; s9 testing → integration test in Tasks 5-9 + web tests + Task 18 smoke; s10 risks → Tasks 5/6 (populate_existing, freeze-iff-no-version, expire_all), Task 14 (detection); s11 deferrals → Task 19 docs. ✅ All spec sections map to a task.

**Type/signature consistency:** `build_commitment` kwargs match the call in `submit_objective_for_review`; `checkin_objective_commitment(..., commitment=...)` matches; `_objective(..., capabilities=, effective_from=)` matches the detail call; `_approval_instance`/`_approval_task` match the FE `WorkflowInstance`/`Task` types; `useObjectiveApproval`/`useEffectivePolicy`/`useSubmitObjectiveForReview`/`useReleaseObjective` names match their test + component usages; `ObjectiveCommitment` shape matches `build_commitment`'s output and the MSW `objectiveCommitment` fixture.

**Open verifies (flagged inline, resolved by mypy/`/check-*`):** the `services/vault` export paths for `release`/`SignatureEventSink`/`get_vault_signature_sink` (Task 5 Step 3); `Task.due_at`/`.assignee_user_id` existence (Task 7); `useUserDirectory` import path (Task 13); the FE `DocumentVersion.metadata_snapshot` field (Task 11); `ObjectiveCreateBody.policy_id` (Task 15).
