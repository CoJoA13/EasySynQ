# S-obj-4 — Objective Commitment Revision Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an Effective Quality Objective's commitment be revised end-to-end (Effective → UnderRevision → PATCH edit → re-freeze → re-approve → re-release with the INV-1 cutover superseding v1), switch every grading read to the governing frozen commitment, and weld the generic byte-path seam shut on OBJ rows.

**Architecture:** Owner-approved spec at `docs/superpowers/specs/2026-06-12-s-obj-4-commitment-revision-design.md` (read it first — the five owner forks O-1…O-6 + micro-calls A/B/C bind this plan). Backend: pure domain helpers (`parse_commitment`, `commitment_needs_freeze`, `resolve_commitment`) → the byte-path guard → `PATCH /objectives/{id}` → `start-revision` + the revision-aware submit → the read-back join. FE: detail affordances + edit modal + register chips + approver before/after. **Migration-free** (head stays `0049`); no new permission key (catalog stays 100); no audit-enum change.

**Tech Stack:** FastAPI/SQLAlchemy-async/Python 3.12 (`uv`), pytest (unit local; integration is Linux-CI — verify locally via `--collect-only` + ruff), React/TS + Mantine + vitest/MSW, redocly for `packages/contracts/openapi.yaml`.

**Working directories:** API commands run from `apps/api/`; web commands from `apps/web/`. Branch: `feat/s-obj-4-commitment-revision` (already created; the spec is committed on it).

---

## File structure (what changes where)

| File | Responsibility in this slice |
|---|---|
| `apps/api/src/easysynq_api/domain/objectives/commitment.py` | + `Commitment` dataclass, `parse_commitment`, `resolve_commitment`, `commitment_needs_freeze` (pure, unit-tested) |
| `apps/api/src/easysynq_api/services/vault/service.py` | + `reject_objective_byte_path` guard, called in `checkout`/`checkin`; stale-comment fix in `checkin_objective_commitment` |
| `apps/api/src/easysynq_api/api/documents.py` | guard calls in `start_revision_endpoint` + `submit_review_endpoint` |
| `apps/api/src/easysynq_api/api/objectives.py` | + `PATCH /objectives/{id}`, `POST /objectives/{id}/start-revision`, submit body, release unit-reset, serializer read-back + `pending_commitment`, capabilities `edit`/`start_revision` |
| `apps/api/src/easysynq_api/services/objectives/lifecycle.py` | revision-aware submit (state guard, content-aware freeze, WD/lock release, `change_reason`) |
| `apps/api/src/easysynq_api/services/objectives/service.py` | `record_measurement` reads the governing commitment (O-2) |
| `apps/api/src/easysynq_api/services/objectives/queries.py` | the governing-snapshot outerjoin (5-tuple `ObjectiveRow`) |
| `apps/api/tests/unit/test_objective_commitment.py` | + parse/round-trip/freeze-matrix/resolve tests |
| `apps/api/tests/integration/test_objective_lifecycle.py` | delete the open-seam test; extend the capabilities pin |
| `apps/api/tests/integration/test_objective_revision.py` | NEW — guard 422s, PATCH, start-revision, the full revision round-trip, read-back, mid-revision measurement, unit-change reset |
| `packages/contracts/openapi.yaml` | 2 new paths, `ObjectiveCommitment`/`ObjectiveUpdate` schemas, capabilities/pending additions, prose fixes, 4× 422 notes |
| `apps/web/src/lib/types.ts` | `ObjectiveState` alias, `ObjectiveCommitment` lifted, capabilities widened, `pending_commitment`, `ObjectiveUpdateBody` |
| `apps/web/src/test/msw/handlers.ts` | PATCH + start-revision handlers, UnderRevision detail fixture, two-version fixtures, 4-flag capabilities |
| `apps/web/src/features/objectives/mutations.ts` | `useUpdateObjective`, `useStartObjectiveRevision` |
| `apps/web/src/features/objectives/EditCommitmentModal.tsx` (+test) | NEW — the commitment edit modal |
| `apps/web/src/features/objectives/ProposedRevisionCard.tsx` (+test) | NEW — was→now pending-revision card |
| `apps/web/src/features/objectives/ObjectiveDetailPage.tsx` (+test) | affordances, revision panel, StateBadge header |
| `apps/web/src/features/objectives/ObjectivesRegisterPage.tsx` (+test) | non-Effective state chips |
| `apps/web/src/features/review/ObjectiveCommitmentContext.tsx` (+test) | `previous` prop, was→now rows |
| `apps/web/src/features/review/ReviewApprovePage.tsx` (+test) | two-newest-commitments pick, `previous` threading |
| `docs/slice-history.md`, `CLAUDE.md` | the S-obj-4 entry + learnings line |

⚠ Carry the spec's s11 traps throughout: `populate_existing` on every handler-level locked load; snapshot strings re-parse before `rules.py`; the guard runs BEFORE lock/WD checks; contract additions to `additionalProperties:false` schemas are load-bearing; MSW pinned to as-built serializers; `import { expect, it } from "vitest"` in every new web test; `{open && <Modal/>}`.

---

### Task 1: Pure domain — `Commitment`, `parse_commitment`, `resolve_commitment`, `commitment_needs_freeze`

**Files:**
- Modify: `apps/api/src/easysynq_api/domain/objectives/commitment.py`
- Test: `apps/api/tests/unit/test_objective_commitment.py` (append)

- [ ] **Step 1: Write the failing tests** — append to `apps/api/tests/unit/test_objective_commitment.py` (add any missing imports to the file's existing import block):

```python
import datetime
import uuid
from decimal import Decimal

from easysynq_api.db.models._objective_enums import ObjectiveDirection
from easysynq_api.db.models._vault_enums import VersionState
from easysynq_api.domain.objectives.commitment import (
    Commitment,
    build_commitment,
    commitment_needs_freeze,
    parse_commitment,
    resolve_commitment,
)

_POL = uuid.uuid4()


def _full_kwargs() -> dict:
    return {
        "target_value": Decimal("98.5"),
        "unit": "%",
        "direction": ObjectiveDirection.HIGHER_IS_BETTER,
        "due_date": datetime.date(2026, 12, 31),
        "at_risk_threshold": Decimal("95"),
        "baseline_value": Decimal("90"),
        "policy_id": _POL,
    }


def test_parse_commitment_round_trips_build_commitment() -> None:
    built = build_commitment(**_full_kwargs())
    parsed = parse_commitment(built)
    assert parsed == Commitment(**_full_kwargs())
    # exact decimal strings survive (never float-lossy)
    assert str(parsed.target_value) == "98.5"


def test_parse_commitment_none_legs() -> None:
    kwargs = {**_full_kwargs(), "at_risk_threshold": None, "baseline_value": None, "policy_id": None}
    parsed = parse_commitment(build_commitment(**kwargs))
    assert parsed.at_risk_threshold is None
    assert parsed.baseline_value is None
    assert parsed.policy_id is None


def test_resolve_commitment_prefers_governing_else_working_row() -> None:
    governing = build_commitment(**{**_full_kwargs(), "target_value": Decimal("98.5")})
    working = {**_full_kwargs(), "target_value": Decimal("50")}  # an in-edit working row
    resolved = resolve_commitment(governing, **working)
    assert resolved.target_value == Decimal("98.5")  # the governing frozen value wins
    assert resolve_commitment(None, **working).target_value == Decimal("50")  # pre-first-release


def test_needs_freeze_matrix() -> None:
    working = build_commitment(**_full_kwargs())
    other = build_commitment(**{**_full_kwargs(), "target_value": Decimal("99")})
    # no version at all → first submit freezes
    assert commitment_needs_freeze(
        latest_version_state=None, latest_commitment=None, working=working
    )
    # latest is the governing Effective version (a revision) → freeze even though it HAS a commitment
    assert commitment_needs_freeze(
        latest_version_state=VersionState.Effective, latest_commitment=working, working=working
    )
    # latest Draft with the SAME commitment (re-submit after request_changes, no edit) → skip
    assert not commitment_needs_freeze(
        latest_version_state=VersionState.Draft, latest_commitment=working, working=working
    )
    # latest Draft with a DIFFERENT commitment (a PATCH since the last freeze) → re-freeze
    assert commitment_needs_freeze(
        latest_version_state=VersionState.Draft, latest_commitment=other, working=working
    )
    # latest Draft with NO commitment (a legacy byte-version) → freeze (Codex-P2 belt-and-braces)
    assert commitment_needs_freeze(
        latest_version_state=VersionState.Draft, latest_commitment=None, working=working
    )
```

- [ ] **Step 2: Run to verify failure**

Run (from `apps/api/`): `uv run pytest tests/unit/test_objective_commitment.py -v`
Expected: FAIL — `ImportError: cannot import name 'Commitment'`

- [ ] **Step 3: Implement** — in `apps/api/src/easysynq_api/domain/objectives/commitment.py`, add `import dataclasses` and `from ...db.models._vault_enums import VersionState` to the imports, then append after `build_commitment`:

```python
@dataclasses.dataclass(frozen=True)
class Commitment:
    """The typed view of a commitment dict (build_commitment's output / a version snapshot's
    ``objective_commitment``) — parsed back to domain types for the pure rules (rules.py compares
    Decimals; feeding it the snapshot's STRINGS would TypeError)."""

    target_value: Decimal
    unit: str
    direction: ObjectiveDirection
    due_date: datetime.date
    at_risk_threshold: Decimal | None
    baseline_value: Decimal | None
    policy_id: uuid.UUID | None


def parse_commitment(snapshot: dict[str, Any]) -> Commitment:
    """The strict inverse of ``build_commitment``. Only ever fed dicts that build_commitment
    minted (the S-obj-4 byte-path guard makes a foreign governing snapshot unconstructible), so a
    malformed dict is a drift-class event — raise, never paper over."""
    return Commitment(
        target_value=Decimal(snapshot["target_value"]),
        unit=str(snapshot["unit"]),
        direction=ObjectiveDirection(snapshot["direction"]),
        due_date=datetime.date.fromisoformat(snapshot["due_date"]),
        at_risk_threshold=(
            Decimal(snapshot["at_risk_threshold"])
            if snapshot.get("at_risk_threshold") is not None
            else None
        ),
        baseline_value=(
            Decimal(snapshot["baseline_value"])
            if snapshot.get("baseline_value") is not None
            else None
        ),
        policy_id=(
            uuid.UUID(snapshot["policy_id"]) if snapshot.get("policy_id") is not None else None
        ),
    )


def resolve_commitment(
    governing: dict[str, Any] | None,
    *,
    target_value: Decimal,
    unit: str,
    direction: ObjectiveDirection,
    due_date: datetime.date,
    at_risk_threshold: Decimal | None,
    baseline_value: Decimal | None,
    policy_id: uuid.UUID | None,
) -> Commitment:
    """The read-back switch (S-obj-4, O-3): the GOVERNING frozen commitment when one exists, else
    the working-row fields (pre-first-release — bit-identical to the S-obj-3 read). Every grading
    read (register/scorecard/detail/record_measurement) resolves through here so an in-flight
    revision edit can never re-grade the live scorecard (the F-2 deferred half, closed)."""
    if governing is not None:
        return parse_commitment(governing)
    return Commitment(
        target_value=target_value,
        unit=unit,
        direction=direction,
        due_date=due_date,
        at_risk_threshold=at_risk_threshold,
        baseline_value=baseline_value,
        policy_id=policy_id,
    )


def commitment_needs_freeze(
    *,
    latest_version_state: VersionState | None,
    latest_commitment: dict[str, Any] | None,
    working: dict[str, Any],
) -> bool:
    """True when submit must mint a NEW frozen commitment version (S-obj-4).

    - no version at all → first submit (the S-obj-3 path)
    - latest is not a Draft → a revision (the latest version is the governing Effective one,
      whose snapshot CARRIES a commitment — the S-obj-3 ``is None`` guard would invert here)
    - the latest Draft's commitment ≠ the working commitment → a PATCH happened since the last
      freeze (or the latest Draft is a commitment-less legacy byte-version) → re-freeze so the
      approver always signs the CURRENT commitment.

    Equal dicts on a Draft → skip (the no-edit re-submit after request_changes dedups: T3
    reverted the version_state, the same Draft version re-advances). Both sides MUST come from
    ``build_commitment``/the snapshot it minted — never a hand-built dict (string
    canonicalization differs)."""
    if latest_version_state is None:
        return True
    if latest_version_state is not VersionState.Draft:
        return True
    return latest_commitment != working
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/unit/test_objective_commitment.py -v`
Expected: PASS (all, including the pre-existing build_commitment tests)

- [ ] **Step 5: Static gates + commit**

Run: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src`
Expected: clean.

```bash
git add apps/api/src/easysynq_api/domain/objectives/commitment.py apps/api/tests/unit/test_objective_commitment.py
git commit -m "feat(s-obj-4): pure commitment parse/resolve + the revision-aware freeze predicate"
```

---

### Task 2: The byte-path guard (O-5) — checkout/checkin service-level, start-revision/submit-review endpoint-level

**Files:**
- Modify: `apps/api/src/easysynq_api/services/vault/service.py` (helper + 2 call sites)
- Modify: `apps/api/src/easysynq_api/api/documents.py` (2 endpoint guards)
- Modify: `apps/api/tests/integration/test_objective_lifecycle.py` (DELETE `test_submit_freezes_even_after_a_generic_byte_checkin`, lines 268-305)
- Create: `apps/api/tests/integration/test_objective_revision.py`

- [ ] **Step 1: Write the integration test** — create `apps/api/tests/integration/test_objective_revision.py`:

```python
"""S-obj-4 integration: the byte-path guard (O-5), the PATCH edit surface (O-1), start-revision +
the revision-aware submit, the read-back switch (O-3), mid-revision measurement capture (O-2), and
the unit-change reset (micro-call B). Run-scoped/delta assertions — the session DB is shared."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models._vault_enums import VersionState
from easysynq_api.db.models.document_version import DocumentVersion
from easysynq_api.db.models.documented_information import DocumentedInformation
from easysynq_api.db.models.working_draft import WorkingDraft
from easysynq_api.db.session import get_sessionmaker

from . import s5_helpers as s5
from .test_objective_lifecycle import _OBJ_KEYS, _create_objective
from .test_quality_objectives import _grant
from .test_vault import _auth, _checkin

pytestmark = pytest.mark.integration


async def test_generic_byte_path_rejected_on_objective(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """O-5: checkout/checkin/start-revision/submit-review 422 on an OBJ id (the commitment is the
    ONLY content an objective can carry; generic submit would bypass the content-aware freeze).
    Reads stay open — the approver card depends on /versions. Replaces the S-obj-3
    test_submit_freezes_even_after_a_generic_byte_checkin (the seam is now welded shut; the
    snapshot-keyed freeze stays pinned at unit level as belt-and-braces)."""
    subject = f"obj4-guard-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _OBJ_KEYS)
    await _grant(subject, ("document.checkout", "document.edit", "document.submit", "document.read_draft"))
    oid = await _create_objective(app_client, h, "Byte-guard objective")

    for path in ("checkout", "start-revision", "submit-review"):
        r = await app_client.post(f"/api/v1/documents/{oid}/{path}", headers=h)
        assert r.status_code == 422, f"{path}: {r.text}"
        body = r.json()
        assert body["errors"][0]["code"] == "objective_managed_via_objectives", path
    # checkin: the guard fires BEFORE the working-draft 409 (deterministic 422, no checkout exists)
    ci = await _checkin(app_client, h, oid, "0" * 64, change_reason="x", change_significance="MAJOR")
    assert ci.status_code == 422, ci.text
    assert ci.json()["errors"][0]["code"] == "objective_managed_via_objectives"
    # reads stay open
    vs = await app_client.get(f"/api/v1/documents/{oid}/versions", headers=h)
    assert vs.status_code == 200, vs.text
```

- [ ] **Step 2: Verify the test collects (integration runs in Linux CI, not on this box)**

Run: `uv run pytest tests/integration/test_objective_revision.py --collect-only -q`
Expected: 1 test collected, no import errors.

- [ ] **Step 3: Implement the service-level guard** — in `apps/api/src/easysynq_api/services/vault/service.py`, add to the model imports `from ...db.models.quality_objective import QualityObjective`, then add ABOVE `async def checkout(`:

```python
async def reject_objective_byte_path(
    session: AsyncSession, doc: DocumentedInformation
) -> None:
    """S-obj-4 (O-5): a Quality Objective's content IS its frozen commitment — the generic byte
    path (checkout/checkin) and the generic lifecycle writers (start-revision/submit-review, see
    api/documents.py) must not touch an OBJ: a byte-version would show the approver a stale
    commitment, and a generic submit would advance a version around the content-aware freeze.
    Kind guard = satellite existence (the S-rec-1 posture); a PK probe. Reads stay open."""
    if await session.get(QualityObjective, doc.id) is not None:
        raise ProblemException(
            status=422,
            code="validation_error",
            title="Quality Objectives are managed via /objectives",
            errors=[
                {
                    "field": "document_id",
                    "code": "objective_managed_via_objectives",
                    "message": "use the /objectives lifecycle (edit/start-revision/submit-review)",
                }
            ],
        )
```

Then make it the FIRST statement of `checkout` (before `locks.acquire`) and of `checkin` (before the working-draft check):

```python
async def checkout(
    session: AsyncSession, sink: VaultAuditSink, actor: AppUser, doc: DocumentedInformation
) -> WorkingDraft:
    await reject_objective_byte_path(session, doc)  # S-obj-4 O-5 — before the lock, deterministic
    token = await locks.acquire(doc.id)
    ...
```

```python
async def checkin(
    ...
) -> tuple[DocumentVersionModel, bool]:
    await reject_objective_byte_path(session, doc)  # S-obj-4 O-5 — before the WD check, deterministic
    wd = await repository.get_working_draft(session, doc.id)
    ...
```

- [ ] **Step 4: Implement the endpoint-level guards** — in `apps/api/src/easysynq_api/api/documents.py`, extend the existing `from ..services.vault import (...)` import list with `reject_objective_byte_path` — **note** it is exported from `service.py`, so if the package import fails add it to `services/vault/__init__.py`'s `from .service import (...)` block + `__all__` (alphabetical) — then guard the two lifecycle endpoints (the shared vault functions CANNOT carry this guard — the namespaced objective endpoints call them):

```python
@router.post("/documents/{document_id}/submit-review")
async def submit_review_endpoint(...):
    ...
    doc = await _load_document(session, caller, document_id, for_update=True)
    # S-obj-4 (O-5): a generic submit on an OBJ would advance a version AROUND the content-aware
    # commitment freeze (objective submit re-freezes when the working commitment changed) — the
    # guard lives HERE, not in submit_review, which the objective endpoint also calls.
    await reject_objective_byte_path(session, doc)
    result = await submit_review(session, caller, doc)
    ...
```

```python
@router.post("/documents/{document_id}/start-revision")
async def start_revision_endpoint(...):
    doc = await _load_document(session, caller, document_id)
    # S-obj-4 (O-5): objective revisions ride POST /objectives/{id}/start-revision
    # (objective.manage — the QMS Owner holds no document.edit); same guard placement rationale.
    await reject_objective_byte_path(session, doc)
    return _document(await start_revision(session, vault_sink, caller, doc))
```

- [ ] **Step 5: Delete the superseded test** — in `apps/api/tests/integration/test_objective_lifecycle.py`, delete `test_submit_freezes_even_after_a_generic_byte_checkin` (the whole function, lines 268-305) and the now-unused `_checkin`/`_upload` names from its `from .test_vault import` line (keep `_auth`).

- [ ] **Step 6: Static gates + collect**

Run: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src && uv run pytest tests/integration/test_objective_revision.py tests/integration/test_objective_lifecycle.py --collect-only -q`
Expected: clean; both files collect.

- [ ] **Step 7: Run the unit suite (no regressions outside the 17-failure Windows baseline)**

Run: `uv run pytest tests/unit -q`
Expected: failures ONLY in the 3 known Windows-baseline files (mirror symlinks / ingestion O_NOFOLLOW).

- [ ] **Step 8: Commit**

```bash
git add apps/api/src/easysynq_api/services/vault apps/api/src/easysynq_api/api/documents.py apps/api/tests/integration/test_objective_revision.py apps/api/tests/integration/test_objective_lifecycle.py
git commit -m "feat(s-obj-4): guard the generic byte/lifecycle writers on OBJ rows (O-5)"
```

---

### Task 3: `PATCH /objectives/{objective_id}` — the edit surface (O-1)

**Files:**
- Modify: `apps/api/src/easysynq_api/api/objectives.py`
- Test: `apps/api/tests/integration/test_objective_revision.py` (append)

> Staleness posture (spec s11): the PATCH rides `_load_objective_doc(for_update=True)` — the
> SAME doc-row-FOR-UPDATE + `populate_existing`-on-both-rows loader the S-obj-3 submit pinned —
> so a PATCH that waits on a concurrent submit's lock re-reads the committed state and 409s
> (covered sequentially by `test_patch_409_outside_draft_or_under_revision`; no separate
> two-session harness is built for the endpoint).

- [ ] **Step 1: Write the integration tests** — append to `test_objective_revision.py`:

```python
async def test_patch_edits_working_commitment_in_draft(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"obj4-patch-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _OBJ_KEYS)
    oid = await _create_objective(app_client, h, "Patchable objective")
    r = await app_client.patch(
        f"/api/v1/objectives/{oid}", headers=h, json={"target_value": "97", "at_risk_threshold": None}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["target_value"] == "97"
    assert body["at_risk_threshold"] is None  # explicit null CLEARS
    # omitted fields inherit: baseline untouched by the partial PATCH
    assert body["baseline_value"] == "90"


async def test_patch_409_outside_draft_or_under_revision(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"obj4-p409-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _OBJ_KEYS)
    oid = await _create_objective(app_client, h, "InReview is read-only")
    assert (await app_client.post(f"/api/v1/objectives/{oid}/submit-review", headers=h)).status_code == 200
    r = await app_client.patch(f"/api/v1/objectives/{oid}", headers=h, json={"target_value": "1"})
    assert r.status_code == 409, r.text


async def test_patch_explicit_null_on_required_field_422_and_bad_policy_422(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"obj4-p422-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _OBJ_KEYS)
    oid = await _create_objective(app_client, h, "Validation objective")
    r1 = await app_client.patch(f"/api/v1/objectives/{oid}", headers=h, json={"target_value": None})
    assert r1.status_code == 422, r1.text
    r2 = await app_client.patch(
        f"/api/v1/objectives/{oid}", headers=h, json={"policy_id": str(uuid.uuid4())}
    )
    assert r2.status_code == 422, r2.text  # not the current Effective POL (mirrors create)


async def test_patch_requires_objective_manage(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    owner = f"obj4-pown-{uuid.uuid4()}"
    ho = _auth(token_factory, owner)
    await _grant(owner, _OBJ_KEYS)
    oid = await _create_objective(app_client, ho, "Manage-gated patch")
    reader = f"obj4-prdr-{uuid.uuid4()}"
    hr = _auth(token_factory, reader)
    await _grant(reader, ("objective.read",))
    r = await app_client.patch(f"/api/v1/objectives/{oid}", headers=hr, json={"target_value": "1"})
    assert r.status_code == 403, r.text
```

- [ ] **Step 2: Collect-check**

Run: `uv run pytest tests/integration/test_objective_revision.py --collect-only -q`
Expected: 5 tests collected.

- [ ] **Step 3: Implement the endpoint** — in `apps/api/src/easysynq_api/api/objectives.py`:

(a) Add to the imports: `from ..db.models._vault_enums import DocumentCurrentState`.

(b) Add the body model below `PlanCreate`:

```python
class ObjectiveUpdate(BaseModel):
    """S-obj-4 (O-1): a partial commitment edit. Omitted ≠ null — ``model_fields_set``
    distinguishes them (the documents metadata-PATCH precedent); explicit null CLEARS the three
    nullable fields and 422s on the four NOT-NULL ones."""

    target_value: Decimal | None = None
    unit: str | None = Field(default=None, min_length=1, max_length=50)
    direction: ObjectiveDirection | None = None
    due_date: datetime.date | None = None
    at_risk_threshold: Decimal | None = None
    baseline_value: Decimal | None = None
    policy_id: uuid.UUID | None = None
```

(c) Add the endpoint after `get_objective_endpoint` (path-id route order is irrelevant among `{objective_id}` siblings; the literals `scorecard`/`policy` stay first):

```python
@router.patch("/objectives/{objective_id}")
async def update_objective_endpoint(
    objective_id: uuid.UUID,
    body: ObjectiveUpdate,
    caller: AppUser = Depends(_objective_manage_path),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Edit the working-copy commitment (S-obj-4, O-1) — legal only in Draft/UnderRevision (the
    FRM form-schema posture). The satellite is the editable working copy (its model docstring);
    the edit is deliberately UNAUDITED (the documents metadata-PATCH precedent, micro-call A): the
    auditable act is the freeze at submit, and consecutive frozen snapshots reconstruct every
    before/after. No version is minted here; the read-back switch (O-3) keeps an Effective
    objective's register/scorecard/detail reads on the GOVERNING frozen commitment, so an
    in-flight edit is visible only via pending_commitment."""
    doc, qo = await _load_objective_doc(session, caller, objective_id, for_update=True)
    if doc.current_state not in (DocumentCurrentState.Draft, DocumentCurrentState.UnderRevision):
        raise ProblemException(
            status=409,
            code="conflict",
            title="Objective commitment is only editable in Draft or UnderRevision",
            detail=f"current_state is {doc.current_state.value}",
        )
    fields = body.model_fields_set
    for f in ("target_value", "unit", "direction", "due_date"):
        if f in fields and getattr(body, f) is None:
            raise ProblemException(
                status=422,
                code="validation_error",
                title=f"{f} cannot be null",
                errors=[{"field": f, "code": "not_nullable", "message": "provide a value"}],
            )
    if "policy_id" in fields and body.policy_id is not None:
        # Mirrors create_objective: the only legal link is the current Effective POL (R25).
        eff = await current_effective_policy(session, caller.org_id)
        if eff is None or eff.id != body.policy_id:
            raise ProblemException(
                status=422,
                code="validation_error",
                title="policy_id must be the current Effective Quality Policy",
            )
    if "target_value" in fields and body.target_value is not None:
        qo.target_value = body.target_value
    if "unit" in fields and body.unit is not None:
        qo.unit = body.unit
    if "direction" in fields and body.direction is not None:
        qo.direction = body.direction
    if "due_date" in fields and body.due_date is not None:
        qo.due_date = body.due_date
    if "at_risk_threshold" in fields:
        qo.at_risk_threshold = body.at_risk_threshold
    if "baseline_value" in fields:
        qo.baseline_value = body.baseline_value
    if "policy_id" in fields:
        qo.policy_id = body.policy_id
    doc.updated_by = caller.id
    await session.commit()
    row = await get_objective(session, objective_id)
    if row is None:  # pragma: no cover — just mutated it, cannot be absent
        raise ProblemException(
            status=500, code="internal_error", title="Objective row not found after update"
        )
    qo2, ident, title, state = row
    return _objective(qo2, identifier=ident, title=title, current_state=state, today=_today())
```

(NOTE: `row` unpacking becomes a 5-tuple in Task 7 — that task updates this site too.)

- [ ] **Step 4: Static gates**

Run: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/easysynq_api/api/objectives.py apps/api/tests/integration/test_objective_revision.py
git commit -m "feat(s-obj-4): PATCH /objectives/{id} — the Draft/UnderRevision commitment edit surface (O-1)"
```

---

### Task 4: `POST /objectives/{objective_id}/start-revision` (O-4)

**Files:**
- Modify: `apps/api/src/easysynq_api/api/objectives.py`
- Test: `apps/api/tests/integration/test_objective_revision.py` (append)

- [ ] **Step 1: Write the integration tests** — append (this also introduces `_drive_to_effective`, reused by every later task):

```python
async def _drive_to_effective(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    title: str,
) -> tuple[str, dict[str, str], dict[str, str], dict[str, str]]:
    """Create → submit (owner) → approve (Approver role) → release (third party). Returns
    (objective_id, h_owner, h_approver, h_releaser). Self-provided personas per call (run-scoped)."""
    salt = uuid.uuid4().hex[:8]
    owner, approver, releaser = f"obj4-ow-{salt}", f"obj4-ap-{salt}", f"obj4-rl-{salt}"
    ho, hap, hrl = (
        _auth(token_factory, owner),
        _auth(token_factory, approver),
        _auth(token_factory, releaser),
    )
    await _grant(owner, _OBJ_KEYS)
    await s5.grant_role(approver, "Approver")
    await _grant(approver, ("document.review",))  # changes_requested needs document.review
    await _grant(releaser, ("document.release", "document.read", "document.read_draft"))
    oid = await _create_objective(app_client, ho, title)
    assert (
        await app_client.post(f"/api/v1/objectives/{oid}/submit-review", headers=ho)
    ).status_code == 200
    task_id = await s5.task_for_doc(oid)
    dec = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hap, json={"outcome": "approve"}
    )
    assert dec.status_code == 200, dec.text
    rel = await app_client.post(f"/api/v1/objectives/{oid}/release", headers=hrl)
    assert rel.status_code == 200, rel.text
    return oid, ho, hap, hrl


async def test_start_revision_flips_under_revision_and_keeps_governing(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    oid, ho, _hap, _hrl = await _drive_to_effective(app_client, token_factory, "Revisable objective")
    r = await app_client.post(f"/api/v1/objectives/{oid}/start-revision", headers=ho)
    assert r.status_code == 200, r.text
    assert r.json()["current_state"] == "UnderRevision"
    async with get_sessionmaker()() as s:
        doc = await s.get(DocumentedInformation, uuid.UUID(oid))
        assert doc is not None
        assert doc.current_effective_version_id is not None  # R43: the pointer never moved
        v = await s.get(DocumentVersion, doc.current_effective_version_id)
        assert v is not None and v.version_state is VersionState.Effective  # v1 keeps governing


async def test_start_revision_409_on_draft_and_403_for_reader(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"obj4-sr409-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _OBJ_KEYS)
    oid = await _create_objective(app_client, h, "Draft cannot revise")
    r = await app_client.post(f"/api/v1/objectives/{oid}/start-revision", headers=h)
    assert r.status_code == 409, r.text
    reader = f"obj4-srrdr-{uuid.uuid4()}"
    hr = _auth(token_factory, reader)
    await _grant(reader, ("objective.read",))
    r2 = await app_client.post(f"/api/v1/objectives/{oid}/start-revision", headers=hr)
    assert r2.status_code == 403, r2.text
```

- [ ] **Step 2: Collect-check** — `uv run pytest tests/integration/test_objective_revision.py --collect-only -q` → all collect.

- [ ] **Step 3: Implement** — in `api/objectives.py`: extend the `from ..services.vault import (...)` list with `start_revision` (it IS exported from the package, `services/vault/__init__.py:19`), then add after `update_objective_endpoint`:

```python
@router.post("/objectives/{objective_id}/start-revision")
async def start_objective_revision_endpoint(
    objective_id: uuid.UUID,
    caller: AppUser = Depends(_objective_manage_path),
    session: AsyncSession = Depends(get_session),
    vault_sink: VaultAuditSink = Depends(get_vault_audit_sink),
) -> dict[str, Any]:
    """T7 (Effective → UnderRevision) for an objective (S-obj-4, O-4) — a thin wrapper over the
    SAME vault start_revision (FSM guard, Redis edit lock, WorkingDraft seeded from Effective,
    REVISION_STARTED audit, commits), gated objective.manage: the F-1 asymmetry — the QMS Owner
    holds no document.edit, so the generic route is unreachable (and guarded on OBJ rows anyway).
    The Effective version keeps governing (R43: in-force is the pointer, which only the v2
    cutover moves — the 6.2 ★ stays COVERED through the whole revision window)."""
    doc, _qo = await _load_objective_doc(session, caller, objective_id, for_update=True)
    await start_revision(session, vault_sink, caller, doc)
    row = await get_objective(session, objective_id)
    if row is None:  # pragma: no cover — just transitioned it, cannot be absent
        raise ProblemException(
            status=500, code="internal_error", title="Objective row not found after start-revision"
        )
    qo2, ident, title, state = row
    return _objective(qo2, identifier=ident, title=title, current_state=state, today=_today())
```

(`row` unpacking becomes a 5-tuple in Task 7.)

- [ ] **Step 4: Static gates + commit**

Run: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src`

```bash
git add apps/api/src/easysynq_api/api/objectives.py apps/api/tests/integration/test_objective_revision.py
git commit -m "feat(s-obj-4): POST /objectives/{id}/start-revision — T7 via the shared vault path (O-4)"
```

---

### Task 5: The revision-aware submit (T9 + content-aware freeze + lock/WD release + change_reason)

**Files:**
- Modify: `apps/api/src/easysynq_api/services/objectives/lifecycle.py` (rewrite)
- Modify: `apps/api/src/easysynq_api/api/objectives.py` (submit body)
- Modify: `apps/api/src/easysynq_api/services/vault/service.py` (stale-comment drive-by)
- Test: `apps/api/tests/integration/test_objective_revision.py` (append)

- [ ] **Step 1: Write the integration tests** — append:

```python
async def test_full_revision_round_trip(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """THE slice test: Effective v1 → start-revision → PATCH → re-submit (new frozen version,
    WD gone) → re-approve → re-release → v1 Superseded + v2 Effective (INV-1) + the edit lock was
    released (a second start-revision succeeds)."""
    oid, ho, hap, hrl = await _drive_to_effective(app_client, token_factory, "Round-trip objective")
    assert (
        await app_client.post(f"/api/v1/objectives/{oid}/start-revision", headers=ho)
    ).status_code == 200
    p = await app_client.patch(f"/api/v1/objectives/{oid}", headers=ho, json={"target_value": "99"})
    assert p.status_code == 200, p.text
    sub = await app_client.post(
        f"/api/v1/objectives/{oid}/submit-review",
        headers=ho,
        json={"change_reason": "Raise the bar after Q2 results"},
    )
    assert sub.status_code == 200, sub.text
    assert sub.json()["current_state"] == "InReview"
    async with get_sessionmaker()() as s:
        versions = (
            (
                await s.execute(
                    select(DocumentVersion)
                    .where(DocumentVersion.document_id == uuid.UUID(oid))
                    .order_by(DocumentVersion.version_seq)
                )
            )
            .scalars()
            .all()
        )
        assert len(versions) == 2  # the v1 commitment + the re-frozen v2
        v2 = versions[-1]
        assert (v2.metadata_snapshot or {})["objective_commitment"]["target_value"] == "99"
        assert v2.change_reason == "Raise the bar after Q2 results"
        assert v2.version_state is VersionState.InReview
        assert versions[0].version_state is VersionState.Effective  # v1 STILL governs
        wd = (
            await s.execute(select(WorkingDraft).where(WorkingDraft.document_id == uuid.UUID(oid)))
        ).scalar_one_or_none()
        assert wd is None  # the start-revision WorkingDraft was consumed by the submit

    task_id = await s5.task_for_doc(oid)
    dec = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hap, json={"outcome": "approve"}
    )
    assert dec.status_code == 200, dec.text
    rel = await app_client.post(f"/api/v1/objectives/{oid}/release", headers=hrl)
    assert rel.status_code == 200, rel.text
    assert rel.json()["current_state"] == "Effective"
    async with get_sessionmaker()() as s:
        versions = (
            (
                await s.execute(
                    select(DocumentVersion)
                    .where(DocumentVersion.document_id == uuid.UUID(oid))
                    .order_by(DocumentVersion.version_seq)
                )
            )
            .scalars()
            .all()
        )
        v1, v2 = versions[0], versions[-1]
        assert v1.version_state is VersionState.Superseded
        assert v1.effective_to is not None
        assert v1.superseded_by_version_id == v2.id
        assert v2.version_state is VersionState.Effective and v2.effective_to is None
    # the edit lock was released at submit: a NEW revision can start (would 409 lock_conflict else)
    again = await app_client.post(f"/api/v1/objectives/{oid}/start-revision", headers=ho)
    assert again.status_code == 200, again.text


async def test_resubmit_after_changes_requested(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Unchanged re-submit re-advances the SAME Draft version (no duplicate freeze); a PATCH in
    the changes_requested window re-freezes a NEW version carrying the edit."""
    salt = uuid.uuid4().hex[:8]
    owner, approver = f"obj4-rs-{salt}", f"obj4-ra-{salt}"
    ho, hap = _auth(token_factory, owner), _auth(token_factory, approver)
    await _grant(owner, _OBJ_KEYS)
    await s5.grant_role(approver, "Approver")
    await _grant(approver, ("document.review",))
    oid = await _create_objective(app_client, ho, "Changes-requested objective")
    assert (
        await app_client.post(f"/api/v1/objectives/{oid}/submit-review", headers=ho)
    ).status_code == 200
    task_id = await s5.task_for_doc(oid)
    rc = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision",
        headers=hap,
        json={"outcome": "changes_requested", "comment": "tighten the threshold"},
    )
    assert rc.status_code == 200, rc.text
    # leg 1: no edit → the same Draft re-advances, still ONE version
    assert (
        await app_client.post(f"/api/v1/objectives/{oid}/submit-review", headers=ho)
    ).status_code == 200
    async with get_sessionmaker()() as s:
        n1 = len(
            (
                await s.execute(
                    select(DocumentVersion).where(DocumentVersion.document_id == uuid.UUID(oid))
                )
            )
            .scalars()
            .all()
        )
    assert n1 == 1
    # leg 2: changes_requested again, PATCH, re-submit → a NEW frozen version with the edit
    task_id = await s5.task_for_doc(oid)
    rc2 = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision",
        headers=hap,
        json={"outcome": "changes_requested", "comment": "again"},
    )
    assert rc2.status_code == 200, rc2.text
    assert (
        await app_client.patch(
            f"/api/v1/objectives/{oid}", headers=ho, json={"at_risk_threshold": "96"}
        )
    ).status_code == 200
    assert (
        await app_client.post(f"/api/v1/objectives/{oid}/submit-review", headers=ho)
    ).status_code == 200
    async with get_sessionmaker()() as s:
        versions = (
            (
                await s.execute(
                    select(DocumentVersion)
                    .where(DocumentVersion.document_id == uuid.UUID(oid))
                    .order_by(DocumentVersion.version_seq)
                )
            )
            .scalars()
            .all()
        )
    assert len(versions) == 2
    frozen = versions[-1]
    assert (frozen.metadata_snapshot or {})["objective_commitment"]["at_risk_threshold"] == "96"


async def test_submit_409_on_in_review(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = f"obj4-s409-{uuid.uuid4()}"
    h = _auth(token_factory, subject)
    await _grant(subject, _OBJ_KEYS)
    oid = await _create_objective(app_client, h, "Double submit")
    assert (await app_client.post(f"/api/v1/objectives/{oid}/submit-review", headers=h)).status_code == 200
    r = await app_client.post(f"/api/v1/objectives/{oid}/submit-review", headers=h)
    assert r.status_code == 409, r.text
```

- [ ] **Step 2: Collect-check** — `uv run pytest tests/integration/test_objective_revision.py --collect-only -q`.

- [ ] **Step 3: Rewrite the lifecycle service** — replace the body of `apps/api/src/easysynq_api/services/objectives/lifecycle.py` with:

```python
"""Quality-Objective lifecycle (S-obj-3/S-obj-4, clause 6.2). ``submit_objective_for_review``
folds a content-aware commitment-freeze + the T2/T9 transition + the approval-workflow
instantiation into ONE transaction, then the OBJ rides the generic DOCUMENT decide leg (approve)
+ ``release`` cutover, unchanged."""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._vault_enums import DocumentCurrentState
from ...db.models.app_user import AppUser
from ...db.models.documented_information import DocumentedInformation
from ...db.models.quality_objective import QualityObjective
from ...domain.objectives.commitment import build_commitment, commitment_needs_freeze
from ...problems import ProblemException
from ..vault import VaultAuditSink, audit_transition, locks, submit_review
from ..vault import repository as vault_repo
from ..vault.service import checkin_objective_commitment
from ..workflow import instantiate_approval

logger = logging.getLogger(__name__)

_EDITABLE = (DocumentCurrentState.Draft, DocumentCurrentState.UnderRevision)


async def submit_objective_for_review(
    session: AsyncSession,
    vault_sink: VaultAuditSink,
    actor: AppUser,
    doc: DocumentedInformation,
    qo: QualityObjective,
    *,
    change_reason: str | None = None,
) -> DocumentedInformation:
    """Freeze the commitment when it changed (``commitment_needs_freeze``) → T2/T9 → instantiate
    the approval workflow → audit, all in one transaction. ``doc`` MUST be loaded
    ``with_for_update`` + ``populate_existing`` (the authz resolver already identity-mapped both
    rows — the S-drift-1 trap; a stale satellite would freeze yesterday's commitment). From
    UnderRevision (T9), the start-revision WorkingDraft is deleted in the txn and its edit lock
    released post-commit (the generic-checkin pattern, O-4)."""
    if doc.current_state not in _EDITABLE:
        raise ProblemException(
            status=409,
            code="conflict",
            title="Objective is not in Draft or UnderRevision",
            detail=f"current_state is {doc.current_state.value}",
        )
    working = build_commitment(
        target_value=qo.target_value,
        unit=qo.unit,
        direction=qo.direction,
        due_date=qo.due_date,
        at_risk_threshold=qo.at_risk_threshold,
        baseline_value=qo.baseline_value,
        policy_id=qo.policy_id,
    )
    # S-obj-4: freeze unless the latest version is a Draft already carrying the CURRENT working
    # commitment — covers the first submit (no version), a revision (the latest is the governing
    # Effective version — the S-obj-3 ``is None`` guard would have SKIPPED here and T9 would have
    # IllegalTransition'd), a PATCH since the last freeze, and a legacy commitment-less
    # byte-version (the generic path is guarded now; belt-and-braces).
    latest = await vault_repo.latest_version(session, doc.id)
    if commitment_needs_freeze(
        latest_version_state=latest.version_state if latest is not None else None,
        latest_commitment=(
            (latest.metadata_snapshot or {}).get("objective_commitment")
            if latest is not None
            else None
        ),
        working=working,
    ):
        default_reason = (
            "Objective commitment revised"
            if doc.current_state is DocumentCurrentState.UnderRevision
            else "Objective commitment submitted for review"
        )
        await checkin_objective_commitment(
            session,
            vault_sink,
            actor,
            doc,
            commitment=working,
            change_reason=(change_reason or "").strip() or default_reason,
            change_significance="MAJOR",
        )
    # O-4: leaving the editable window — drop the start-revision WorkingDraft (in-txn) and release
    # its edit lock post-commit (the generic-checkin pattern). Release regardless of holder: the
    # objective surface owns its lock (checkout is guarded on OBJ rows, so only start_revision
    # mints one). No WD exists on a plain Draft submit — both steps no-op.
    wd = await vault_repo.get_working_draft(session, doc.id)
    token = (wd.lock_token or "") if wd is not None else ""
    if wd is not None:
        await session.delete(wd)
    result = await submit_review(session, actor, doc)
    await instantiate_approval(session, result.doc, actor)
    audit_transition(session, vault_sink, result, actor)
    await session.commit()
    if token and not await locks.release(doc.id, token):
        logger.warning("objective submit: edit-lock token no longer matched (lock had lapsed)")
    return result.doc
```

- [ ] **Step 4: Thread the body through the endpoint** — in `api/objectives.py`, add below `ObjectiveUpdate`:

```python
class ObjectiveSubmitBody(BaseModel):
    """Optional INV-3 change reason for the freeze (defaults: first submit vs revision)."""

    change_reason: str | None = Field(default=None, max_length=500)
```

and change `submit_objective_endpoint`:

```python
@router.post("/objectives/{objective_id}/submit-review")
async def submit_objective_endpoint(
    objective_id: uuid.UUID,
    body: ObjectiveSubmitBody | None = None,
    caller: AppUser = Depends(_objective_manage_path),
    session: AsyncSession = Depends(get_session),
    vault_sink: VaultAuditSink = Depends(get_vault_audit_sink),
) -> dict[str, Any]:
    # FOR UPDATE + populate_existing serializes concurrent submits and dodges the stale-identity-map
    # trap; submit_objective_for_review freezes when the commitment changed (T2 AND T9 — S-obj-4),
    # instantiates approval, and commits atomically. Approval routes through POST
    # /tasks/{id}/decision (DOCUMENT leg).
    doc, qo = await _load_objective_doc(session, caller, objective_id, for_update=True)
    await submit_objective_for_review(
        session, vault_sink, caller, doc, qo,
        change_reason=body.change_reason if body is not None else None,
    )
    row = await get_objective(session, objective_id)
    ...  # (unchanged re-read + serialize)
```

- [ ] **Step 5: Drive-by comment fix** — in `services/vault/service.py` (`checkin_objective_commitment`, the flush comment around line 710-714), replace the stale parenthetical (`checkin``/``checkin_form_schema`` emit pre-flush…`) with:

```python
    # Flush BEFORE _emit — NOT commit (submit_objective_for_review owns the txn boundary). The
    # ``default=uuid.uuid4`` id is a FLUSH-time default (a pending instance reads ``id`` as None),
    # so the flush populates version.id for the audit row's object_id — the same flush-before-emit
    # contract as ``checkin`` and ``checkin_form_schema``.
```

- [ ] **Step 6: Static gates + unit suite + collect**

Run: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src && uv run pytest tests/unit -q && uv run pytest tests/integration/test_objective_revision.py --collect-only -q`
Expected: clean / Windows-baseline-only failures / all collect. ⚠ `test_objective_lifecycle.py::test_submit_twice_is_a_conflict` (InReview 409) and the 409-title change: the old title was "Objective is not in Draft" — no existing test asserts the title text, only the 409 status. If CI disagrees, fix the test, not the guard.

- [ ] **Step 7: Commit**

```bash
git add apps/api/src/easysynq_api/services/objectives/lifecycle.py apps/api/src/easysynq_api/api/objectives.py apps/api/src/easysynq_api/services/vault/service.py apps/api/tests/integration/test_objective_revision.py
git commit -m "feat(s-obj-4): revision-aware submit — T9, content-aware re-freeze, lock/WD release, change_reason"
```

---

### Task 6: Release — the unit-change `current_value` reset (micro-call B)

**Files:**
- Modify: `apps/api/src/easysynq_api/api/objectives.py` (release endpoint)
- Test: `apps/api/tests/integration/test_objective_revision.py` (append)

- [ ] **Step 1: Write the integration test** — append (also exercises measurements; `kpi.record`/`kpi.read` are already in `_OBJ_KEYS`):

```python
async def _record(
    app_client: AsyncClient, h: dict[str, str], oid: str, *, value: str, unit: str, period: str
) -> int:
    r = await app_client.post(
        f"/api/v1/objectives/{oid}/measurements",
        headers=h,
        json={"period": period, "value": value, "unit": unit},
    )
    return r.status_code


async def test_unit_change_revision_resets_current_value(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    oid, ho, hap, hrl = await _drive_to_effective(app_client, token_factory, "Unit-change objective")
    assert await _record(app_client, ho, oid, value="92", unit="%", period="2026-05-31") == 201
    detail = (await app_client.get(f"/api/v1/objectives/{oid}", headers=ho)).json()
    assert detail["current_value"] == "92"
    # revise % → count
    assert (
        await app_client.post(f"/api/v1/objectives/{oid}/start-revision", headers=ho)
    ).status_code == 200
    assert (
        await app_client.patch(
            f"/api/v1/objectives/{oid}", headers=ho,
            json={"unit": "count", "target_value": "10", "at_risk_threshold": None, "baseline_value": None},
        )
    ).status_code == 200
    assert (
        await app_client.post(f"/api/v1/objectives/{oid}/submit-review", headers=ho)
    ).status_code == 200
    task_id = await s5.task_for_doc(oid)
    assert (
        await app_client.post(
            f"/api/v1/tasks/{task_id}/decision", headers=hap, json={"outcome": "approve"}
        )
    ).status_code == 200
    rel = await app_client.post(f"/api/v1/objectives/{oid}/release", headers=hrl)
    assert rel.status_code == 200, rel.text
    # micro-call B: the old %-readings can't grade a count-target — honest unmeasured
    assert rel.json()["current_value"] is None
    assert rel.json()["rag"] == "unmeasured"
    # the next reading validates against the NEW governing unit and re-rolls
    assert await _record(app_client, ho, oid, value="8", unit="count", period="2026-06-30") == 201
    assert await _record(app_client, ho, oid, value="8", unit="%", period="2026-07-31") == 422
```

(The `unit="count"` 201 leg only passes after Task 7 wires `record_measurement` to the governing
commitment — until then the working row ALREADY says "count", so it passes here too; the `"%"`
422 leg pins the post-release gate either way.)

- [ ] **Step 2: Collect-check** — `uv run pytest tests/integration/test_objective_revision.py --collect-only -q`.

- [ ] **Step 3: Implement** — in `api/objectives.py::release_objective_endpoint`, replace the post-`enforce` block:

```python
    doc, _ = await _load_objective_doc(session, caller, objective_id)
    resource = await _objective_release_scope(session, doc)
    await enforce(session, authz_sink, request, caller, "document.release", resource, sig_hook=True)
    # Micro-call B: capture the CURRENTLY-governing unit before the cutover demotes it (None
    # pre-first-release). The doc row was loaded pre-release, so its pointer is the prior one.
    prior_unit: str | None = None
    if doc.current_effective_version_id is not None:
        pv = await session.get(DocumentVersion, doc.current_effective_version_id)
        pc = (pv.metadata_snapshot or {}).get("objective_commitment") if pv is not None else None
        prior_unit = pc.get("unit") if isinstance(pc, dict) else None
    await release(caller, objective_id, vault_sink, sig_sink)
    # release() committed in its own SERIALIZABLE session; this request session's identity map
    # still holds the pre-release state — expire it so the re-reads refresh from the DB.
    session.expire_all()
    # Micro-call B: a unit-changing revision makes current_value (old-unit readings) garbage
    # against the new target — reset to NULL (rag honestly reads unmeasured until a reading in
    # the new unit lands; a crash in this gap self-heals at the next measurement, which validates
    # against the NEW governing unit).
    doc_after = await session.get(DocumentedInformation, objective_id)
    new_unit: str | None = None
    if doc_after is not None and doc_after.current_effective_version_id is not None:
        nv = await session.get(DocumentVersion, doc_after.current_effective_version_id)
        nc = (nv.metadata_snapshot or {}).get("objective_commitment") if nv is not None else None
        new_unit = nc.get("unit") if isinstance(nc, dict) else None
    if prior_unit is not None and new_unit is not None and prior_unit != new_unit:
        qo_after = await session.get(QualityObjective, objective_id)
        if qo_after is not None and qo_after.current_value is not None:
            qo_after.current_value = None
            await session.commit()
    row = await get_objective(session, objective_id)
    ...  # (unchanged re-read + serialize)
```

- [ ] **Step 4: Static gates + commit**

Run: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src`

```bash
git add apps/api/src/easysynq_api/api/objectives.py apps/api/tests/integration/test_objective_revision.py
git commit -m "feat(s-obj-4): reset current_value at a unit-changing re-release (micro-call B)"
```

---

### Task 7: The read-back switch (O-3) — query join, serializer, `pending_commitment`, capabilities, `record_measurement`

**Files:**
- Modify: `apps/api/src/easysynq_api/services/objectives/queries.py`
- Modify: `apps/api/src/easysynq_api/api/objectives.py`
- Modify: `apps/api/src/easysynq_api/services/objectives/service.py` (`record_measurement`)
- Modify: `apps/api/tests/integration/test_objective_lifecycle.py` (capabilities pin)
- Test: `apps/api/tests/integration/test_objective_revision.py` (append)

- [ ] **Step 1: Write the integration tests** — append:

```python
async def test_reads_serve_governing_during_revision(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """O-3/F-2 closed: during an UnderRevision edit, register/scorecard/detail keep grading
    against the GOVERNING frozen commitment; the edit shows only as pending_commitment."""
    oid, ho, _hap, _hrl = await _drive_to_effective(app_client, token_factory, "Governing reads")
    assert (
        await app_client.post(f"/api/v1/objectives/{oid}/start-revision", headers=ho)
    ).status_code == 200
    assert (
        await app_client.patch(f"/api/v1/objectives/{oid}", headers=ho, json={"target_value": "50"})
    ).status_code == 200
    detail = (await app_client.get(f"/api/v1/objectives/{oid}", headers=ho)).json()
    assert detail["target_value"] == "98"  # the governing v1 target, NOT the in-edit 50
    assert detail["pending_commitment"]["target_value"] == "50"  # the edit, detail-only
    assert detail["capabilities"]["edit"] is True
    assert detail["capabilities"]["start_revision"] is True
    row = next(
        o for o in (await app_client.get("/api/v1/objectives", headers=ho)).json()["data"]
        if o["id"] == oid
    )
    assert row["target_value"] == "98"
    assert "pending_commitment" not in row  # detail-only
    sc = next(
        o
        for o in (await app_client.get("/api/v1/objectives/scorecard", headers=ho)).json()["objectives"]
        if o["id"] == oid
    )
    assert sc["target_value"] == "98"


async def test_pending_commitment_null_without_divergence(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    oid, ho, _hap, _hrl = await _drive_to_effective(app_client, token_factory, "No divergence")
    detail = (await app_client.get(f"/api/v1/objectives/{oid}", headers=ho)).json()
    assert detail["pending_commitment"] is None  # working == governing after release


async def test_measurement_mid_revision_captures_governing(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """O-2: the unit gate + target_at_capture read the governing commitment — an unapproved edit
    can never leak into evidence-grade KPI_READING records."""
    oid, ho, _hap, _hrl = await _drive_to_effective(app_client, token_factory, "Mid-rev capture")
    assert (
        await app_client.post(f"/api/v1/objectives/{oid}/start-revision", headers=ho)
    ).status_code == 200
    assert (
        await app_client.patch(
            f"/api/v1/objectives/{oid}", headers=ho, json={"unit": "count", "target_value": "10"}
        )
    ).status_code == 200
    # governing unit is still "%" — a "count" reading is rejected, a "%" one accepted
    assert await _record(app_client, ho, oid, value="9", unit="count", period="2026-05-31") == 422
    assert await _record(app_client, ho, oid, value="97", unit="%", period="2026-05-31") == 201
    ms = (await app_client.get(f"/api/v1/objectives/{oid}/measurements", headers=ho)).json()["data"]
    assert ms[0]["target_at_capture"] == "98"  # the governing v1 target, never the in-edit 10
```

- [ ] **Step 2: Collect-check** — `uv run pytest tests/integration/test_objective_revision.py --collect-only -q`.

- [ ] **Step 3: The query join** — rewrite `apps/api/src/easysynq_api/services/objectives/queries.py`'s header + the two row queries:

```python
"""Quality Objectives read queries (S-obj-1/S-obj-4). Returns rows + the joined base identity +
the GOVERNING frozen commitment (the current Effective version's snapshot fold — a per-row PK
probe via current_effective_version_id, the drift_report outerjoin precedent; NULL
pre-first-release, where the serializer falls back to the working row). RAG/pct are computed in
the serializer from the pure rule over the RESOLVED commitment."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._vault_enums import DocumentCurrentState
from ...db.models.document_version import DocumentVersion
from ...db.models.documented_information import DocumentedInformation
from ...db.models.kpi_measurement import KpiMeasurement
from ...db.models.objective_plan import ObjectivePlan
from ...db.models.quality_objective import QualityObjective

# (qo, identifier, title, current_state, governing_commitment | None)
ObjectiveRow = tuple[QualityObjective, str, str, DocumentCurrentState, Any]


def _row_select() -> Select[Any]:
    return (
        select(
            QualityObjective,
            DocumentedInformation.identifier,
            DocumentedInformation.title,
            DocumentedInformation.current_state,
            DocumentVersion.metadata_snapshot["objective_commitment"].label(
                "governing_commitment"
            ),
        )
        .join(DocumentedInformation, QualityObjective.id == DocumentedInformation.id)
        .outerjoin(
            DocumentVersion,
            DocumentVersion.id == DocumentedInformation.current_effective_version_id,
        )
    )


async def list_objectives(
    session: AsyncSession, org_id: uuid.UUID, *, process_id: uuid.UUID | None = None
) -> list[ObjectiveRow]:
    stmt = _row_select().where(QualityObjective.org_id == org_id).order_by(
        DocumentedInformation.identifier
    )
    if process_id is not None:
        stmt = stmt.where(QualityObjective.process_id == process_id)
    return [tuple(r) for r in (await session.execute(stmt)).all()]


async def get_objective(session: AsyncSession, objective_id: uuid.UUID) -> ObjectiveRow | None:
    row = (
        await session.execute(_row_select().where(QualityObjective.id == objective_id))
    ).first()
    return tuple(row) if row is not None else None
```

(`list_plans` / `list_measurements` unchanged.)

- [ ] **Step 4: The serializer** — in `api/objectives.py`:

(a) Extend the domain import: `from ..domain.objectives.commitment import build_commitment, resolve_commitment`.

(b) Rewrite `_objective` to resolve through the governing commitment:

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
    governing: dict[str, Any] | None = None,
    pending_commitment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # S-obj-4 (O-3, F-2 closed): every commitment-shaped field + the RAG/pct/attainment grade
    # resolve through the GOVERNING frozen commitment when one exists (the version snapshot,
    # never the mutable working row) — an in-flight revision edit can never re-grade the live
    # register/scorecard. Pre-first-release (governing None) the working row IS the commitment
    # (bit-identical to the S-obj-3 output). current_value stays operational (satellite-side).
    c = resolve_commitment(
        governing,
        target_value=qo.target_value,
        unit=qo.unit,
        direction=qo.direction,
        due_date=qo.due_date,
        at_risk_threshold=qo.at_risk_threshold,
        baseline_value=qo.baseline_value,
        policy_id=qo.policy_id,
    )
    rag = rag_status(
        current=qo.current_value,
        target=c.target_value,
        direction=c.direction,
        at_risk_threshold=c.at_risk_threshold,
    )
    out: dict[str, Any] = {
        "id": str(qo.id),
        "identifier": identifier,
        "title": title,
        "current_state": (
            current_state.value if hasattr(current_state, "value") else str(current_state)
        ),
        "target_value": str(c.target_value),
        "unit": c.unit,
        "baseline_value": str(c.baseline_value) if c.baseline_value is not None else None,
        "current_value": str(qo.current_value) if qo.current_value is not None else None,
        "direction": c.direction.value,
        "at_risk_threshold": (
            str(c.at_risk_threshold) if c.at_risk_threshold is not None else None
        ),
        "due_date": c.due_date.isoformat(),
        "process_id": str(qo.process_id) if qo.process_id else None,
        "policy_id": str(c.policy_id) if c.policy_id is not None else None,
        "rag": rag,
        "pct_toward_target": pct_toward_target(
            current=qo.current_value,
            target=c.target_value,
            baseline=c.baseline_value,
            direction=c.direction,
        ),
        "attainment": attainment(
            current=qo.current_value,
            target=c.target_value,
            direction=c.direction,
            due_date=c.due_date,
            today=today,
        ),
        "plans": [_plan(p) for p in (plans or [])],
    }
    # S-obj-3 (detail-only): the caller's lifecycle affordances + the effective date for the
    # stepper. LIST/scorecard/create/submit/release call-sites pass neither → unchanged output.
    # A detail response ALWAYS carries effective_from (null until Effective) AND, since S-obj-4,
    # pending_commitment (the in-edit working commitment when it diverges from governing, else
    # null — the edit modal's seed + the "proposed revision" card); capabilities doubles as the
    # detail marker.
    if capabilities is not None:
        out["capabilities"] = capabilities
        out["effective_from"] = effective_from
        out["pending_commitment"] = pending_commitment
    return out
```

(c) Update EVERY 4-tuple unpack to the 5-tuple and thread `governing=`. Six sites:
- `create_objective_endpoint`: `_, ident, title, state, gov = row` → `_objective(qo, ..., governing=gov)` (fresh create → `gov` is None);
- `list_objectives_endpoint`: `for qo, i, t, s, gov in rows` → `_objective(qo, ..., governing=gov)`;
- `scorecard_endpoint`: same loop change;
- `update_objective_endpoint` (Task 3) + `submit_objective_endpoint` + `start_objective_revision_endpoint` + `release_objective_endpoint`: `qo2, ident, title, state, gov = row` → pass `governing=gov`.

(d) Detail endpoint — compute `pending_commitment`:

```python
@router.get("/objectives/{objective_id}")
async def get_objective_endpoint(...):
    row = await get_objective(session, objective_id)
    if row is None:
        raise ProblemException(status=404, code="not_found", title="Objective not found")
    qo, ident, title, state, gov = row
    plans = await list_plans(session, objective_id)
    doc = await session.get(DocumentedInformation, objective_id)
    if doc is None:  # pragma: no cover — the satellite row exists, so the base must too
        raise ProblemException(status=500, code="internal_error", title="Objective base row missing")
    caps = await _objective_capabilities(session, caller, doc, qo)
    eff = await _objective_effective_from(session, doc)
    # S-obj-4: the in-edit working commitment, exposed only while it diverges from governing.
    working = build_commitment(
        target_value=qo.target_value,
        unit=qo.unit,
        direction=qo.direction,
        due_date=qo.due_date,
        at_risk_threshold=qo.at_risk_threshold,
        baseline_value=qo.baseline_value,
        policy_id=qo.policy_id,
    )
    pending = working if (gov is not None and working != gov) else None
    return _objective(
        qo, identifier=ident, title=title, current_state=state, today=_today(), plans=plans,
        capabilities=caps, effective_from=eff, governing=gov, pending_commitment=pending,
    )
```

(e) Capabilities — in `_objective_capabilities`, replace the return:

```python
    # S-obj-4 (micro-call C): edit/start_revision ride the SAME objective.manage answer as submit
    # (permission-only, state-blind — the FE combines with current_state); split keys so the
    # contract self-describes and an Edit button never gates on a flag named "submit".
    return {
        "submit": submit_cap,
        "edit": submit_cap,
        "start_revision": submit_cap,
        "release": release_cap,
    }
```

- [ ] **Step 5: `record_measurement` reads the governing commitment (O-2)** — in `services/objectives/service.py`: add imports `from ...db.models.document_version import DocumentVersion`, `from ...domain.objectives.commitment import parse_commitment`, `from typing import Any`. Then replace the unit-gate/target block (between the `qo is None` 404 and `capture_record`):

```python
    # S-obj-4 (O-2): the unit gate + target_at_capture read the GOVERNING frozen commitment when
    # one exists — a mid-revision working-row edit must never leak an unapproved unit/target into
    # evidence-grade KPI_READING records (R44: target_at_capture is frozen at capture, never
    # rewritten). Working-row fallback pre-first-release (today's behavior). Fresh reads
    # (populate_existing — a cutover may have committed while we waited on the qo lock); NO
    # doc-row lock: it would invert _load_objective_doc's doc→satellite lock order against a
    # concurrent submit.
    doc_row = (
        await session.execute(
            select(DocumentedInformation)
            .where(DocumentedInformation.id == objective_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    governing: dict[str, Any] | None = None
    if doc_row is not None and doc_row.current_effective_version_id is not None:
        ver = (
            await session.execute(
                select(DocumentVersion)
                .where(DocumentVersion.id == doc_row.current_effective_version_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        raw = (ver.metadata_snapshot or {}).get("objective_commitment") if ver is not None else None
        governing = raw if isinstance(raw, dict) else None
    if governing is not None:
        gc = parse_commitment(governing)
        effective_unit, effective_target = gc.unit, gc.target_value
    else:
        effective_unit, effective_target = qo.unit, qo.target_value
    # The reading must be in the objective's GOVERNING unit — current_value/RAG compare the raw
    # value against the governing target with no conversion (a mismatch would corrupt the scorecard).
    if unit != effective_unit:
        raise ProblemException(
            status=422,
            code="validation_error",
            title=f"Measurement unit '{unit}' must match the objective unit '{effective_unit}'",
        )

    target_at_capture = effective_target
```

(The later `base = await session.get(DocumentedInformation, objective_id)` audit lookup can now reuse `doc_row` — replace it with `base = doc_row`.)

- [ ] **Step 6: Extend the existing capabilities pin** — in `tests/integration/test_objective_lifecycle.py::test_detail_exposes_capabilities_for_the_manager`, add after the existing asserts:

```python
    assert detail["capabilities"]["edit"] is True  # S-obj-4: same objective.manage answer
    assert detail["capabilities"]["start_revision"] is True
    assert detail["pending_commitment"] is None  # Draft, no governing version yet
```

- [ ] **Step 7: Static gates + unit suite + full collect**

Run: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src && uv run pytest tests/unit -q && uv run pytest tests/integration --collect-only -q`
Expected: clean / baseline-only / all collect.

- [ ] **Step 8: Commit**

```bash
git add apps/api/src/easysynq_api/services/objectives apps/api/src/easysynq_api/api/objectives.py apps/api/tests/integration
git commit -m "feat(s-obj-4): the governing-commitment read-back switch — join, serializer, pending_commitment, O-2 capture (O-3)"
```

---

### Task 8: Contracts (`packages/contracts/openapi.yaml`)

**Files:**
- Modify: `packages/contracts/openapi.yaml`

- [ ] **Step 1: Add the two paths** — next to the existing `/objectives/{objective_id}` path item (~line 4600), add a `patch:` operation to it and a new `/objectives/{objective_id}/start-revision` path (mirror the style of the surrounding operations — `tags: [objectives]`, `operationId`, `security`, the shared `Problem` error responses):

```yaml
# inside the existing /objectives/{objective_id} path item, alongside get:
    patch:
      tags: [objectives]
      operationId: updateObjective
      summary: "Edit the working-copy commitment (S-obj-4). Gated objective.manage; Draft/UnderRevision only (409 otherwise)."
      description: >-
        A partial commitment edit on the MUTABLE quality_objective working row — no version is
        minted and the edit is deliberately unaudited (the documents metadata-PATCH posture: the
        auditable act is the freeze at submit, and consecutive frozen snapshots reconstruct every
        before/after). Omitted fields inherit; an explicit null CLEARS at_risk_threshold /
        baseline_value / policy_id and 422s on the four required fields. While a governing
        Effective version exists, reads keep serving IT — the edit surfaces only as the detail's
        pending_commitment until re-release.
      requestBody:
        required: true
        content:
          application/json:
            schema: { $ref: "#/components/schemas/ObjectiveUpdate" }
      responses:
        "200":
          description: "The updated objective (the bare list shape — no capabilities/pending_commitment)."
          content:
            application/json:
              schema: { $ref: "#/components/schemas/Objective" }
        "409":
          description: "Not in Draft or UnderRevision (conflict; detail names the current state)."
```

```yaml
  /objectives/{objective_id}/start-revision:
    post:
      tags: [objectives]
      operationId: startObjectiveRevision
      summary: "Effective→UnderRevision (T7) for an objective — gated objective.manage (S-obj-4)."
      description: >-
        A thin wrapper over the SAME vault start_revision the documents surface uses (edit lock +
        working draft + REVISION_STARTED audit) — namespaced because the QMS Owner (the only
        objective.manage holder in v1) holds no document.edit. The Effective version KEEPS
        governing (R43: in-force is the current_effective_version_id pointer, which only the v2
        release cutover moves) — the 6.2 ★ stays COVERED through the revision window.
      parameters:
        - { name: objective_id, in: path, required: true, schema: { type: string, format: uuid } }
      responses:
        "200":
          description: "The objective, now UnderRevision."
          content:
            application/json:
              schema: { $ref: "#/components/schemas/Objective" }
        "409":
          description: "Not Effective (T7), or a revision/edit lock is already held (lock_conflict)."
```

(Match the file's actual parameter/error-response conventions for the sibling objective ops — copy the `get:` operation's `parameters`/`4xx` skeleton verbatim.)

- [ ] **Step 2: Schemas** — add `ObjectiveCommitment` + `ObjectiveUpdate` beside the objective schemas (~line 7410), extend `Objective` + `ObjectiveCapabilities`:

```yaml
    ObjectiveCommitment:
      type: object
      additionalProperties: false
      required: [target_value, unit, direction, due_date, at_risk_threshold, baseline_value, policy_id]
      description: >-
        The canonical 7-field commitment dict (domain/objectives/commitment.py build_commitment) —
        decimals as STRINGS, direction the enum value, due_date ISO. The SAME dict is the frozen
        version's WORM source bytes and its metadata_snapshot.objective_commitment fold.
      properties:
        target_value: { type: string, description: "Decimal string." }
        unit: { type: string }
        direction: { type: string, enum: [HIGHER_IS_BETTER, LOWER_IS_BETTER] }
        due_date: { type: string, format: date }
        at_risk_threshold: { type: [string, "null"], description: "Decimal string." }
        baseline_value: { type: [string, "null"], description: "Decimal string." }
        policy_id: { type: [string, "null"], format: uuid }

    ObjectiveUpdate:
      type: object
      additionalProperties: false
      description: >-
        S-obj-4 partial commitment edit. Omitted ≠ null: explicit null clears the three nullable
        fields; null on target_value/unit/direction/due_date is a 422.
      properties:
        target_value: { type: [string, "null"], description: "Decimal string." }
        unit: { type: [string, "null"], minLength: 1, maxLength: 50 }
        direction: { type: [string, "null"], enum: [HIGHER_IS_BETTER, LOWER_IS_BETTER, null] }
        due_date: { type: [string, "null"], format: date }
        at_risk_threshold: { type: [string, "null"], description: "Decimal string." }
        baseline_value: { type: [string, "null"], description: "Decimal string." }
        policy_id: { type: [string, "null"], format: uuid, description: "Must be the current Effective Quality Policy when non-null." }
```

To `Objective.properties` append:

```yaml
        pending_commitment:
          oneOf:
            - { $ref: "#/components/schemas/ObjectiveCommitment" }
            - { type: "null" }
          description: >-
            S-obj-4 (detail-only, like capabilities/effective_from): the in-edit WORKING
            commitment when it diverges from the governing frozen one (a revision/edit in
            flight), else null. The main commitment fields always read the GOVERNING frozen
            commitment once one exists.
```

To `ObjectiveCapabilities.properties` append:

```yaml
        edit: { type: boolean, description: "objective.manage at the objective's scope (S-obj-4; identical computation to submit — state-blind, the SPA gates Draft/UnderRevision)." }
        start_revision: { type: boolean, description: "objective.manage at the objective's scope (S-obj-4; the SPA gates Effective)." }
```

- [ ] **Step 3: Prose fixes** — (a) the `/objectives/{objective_id}/submit-review` op: replace the summary/description/409 (first-release-pinned, now wrong) with T2/T9 + the content-aware freeze:

```yaml
      summary: "Draft→InReview (T2) / UnderRevision→InReview (T9) — freezes the commitment when it changed + instantiates the approval workflow. Gated objective.manage (PROCESS scope from objective)."
      description: >-
        One transaction (S-obj-3/S-obj-4): the commitment is frozen into a new Draft WORM version
        whenever the working commitment differs from the latest version's frozen one — the first
        submit, every revision re-submit (the latest version is then the governing Effective
        one), and a re-submit after an edit in the changes_requested window; an UNCHANGED
        re-submit re-advances the existing Draft version (no duplicate version). The optional
        change_reason lands on the frozen version (INV-3); significance is always MAJOR. From
        UnderRevision the start-revision edit lock is released. Approval then routes through
        POST /tasks/{id}/decision (the generic DOCUMENT leg, C7).
```

with the 409 description → `"Not in Draft or UnderRevision (conflict; detail names the current state)."`, and add the request body:

```yaml
      requestBody:
        required: false
        content:
          application/json:
            schema:
              type: object
              additionalProperties: false
              properties:
                change_reason: { type: [string, "null"], maxLength: 500 }
```

(b) the `/objectives/{objective_id}/release` op description: replace the "exactly one version stream" sentence with:

```yaml
        S-obj-3/S-obj-4 mirror of POST /documents/{document_id}/release, minus the request body —
        the latest Approved version is the only cutover candidate. On a revision re-release the
        SERIALIZABLE cutover supersedes the prior Effective version (INV-1: demote-before-promote)
        and, when the new governing unit differs from the old, resets current_value to null (the
        old-unit readings cannot grade the new target — rag reads unmeasured until a new reading
        lands). SoD-2 overlays the gate (the author/approver of that version cannot release it →
        403 sod_violation).
```

- [ ] **Step 4: The four guarded document ops** — add to the `responses` (or extend the existing `422` description) of `/documents/{document_id}/checkout`, `/documents/{document_id}/checkin`, `/documents/{document_id}/start-revision`, `/documents/{document_id}/submit-review`:

```yaml
        "422":
          description: "Validation error; on a Quality Objective id: objective_managed_via_objectives (S-obj-4 — OBJ rows are managed via the /objectives lifecycle)."
```

- [ ] **Step 5: Lint + commit**

Run (repo root): `npx --yes @redocly/cli@latest lint packages/contracts/openapi.yaml` (or the `/check-contracts` skill).
Expected: no errors (warnings at the pre-existing baseline).

```bash
git add packages/contracts/openapi.yaml
git commit -m "docs(s-obj-4): contract — PATCH + start-revision paths, ObjectiveCommitment/Update schemas, capability flags, prose fixes"
```

---

### Task 9: FE types + MSW fixtures/handlers

**Files:**
- Modify: `apps/web/src/lib/types.ts`
- Modify: `apps/web/src/features/review/ObjectiveCommitmentContext.tsx` (type import only)
- Modify: `apps/web/src/test/msw/handlers.ts`

- [ ] **Step 1: types.ts** — in the S-obj-2 objectives block:

(a) Replace the `ObjectiveState` union with an alias (structurally identical — unifies `StateBadge` reuse):

```ts
// The 7-state document lifecycle — an objective IS a kind=DOCUMENT subtype (R44), so its state
// union is the document one (S-obj-4 unified the alias so StateBadge renders both).
export type ObjectiveState = DocumentCurrentState;
```

(b) Add the commitment type (lifted from ObjectiveCommitmentContext so `Objective.pending_commitment` can reference it):

```ts
// Pinned to the api build_commitment serializer (domain/objectives/commitment.py) — all decimals
// are STRINGS, direction is the enum .value, dates are ISO strings.
export interface ObjectiveCommitment {
  target_value: string;
  unit: string;
  direction: ObjectiveDirection;
  due_date: string;
  at_risk_threshold: string | null;
  baseline_value: string | null;
  policy_id: string | null;
}
```

(c) In `Objective`, replace the detail-only block:

```ts
  // S-obj-3/4 (detail-only; absent on list/scorecard rows; effective_from null until Effective;
  // pending_commitment = the in-edit working commitment when it diverges from governing, else null):
  capabilities?: { submit: boolean; release: boolean; edit: boolean; start_revision: boolean };
  effective_from?: string | null;
  pending_commitment?: ObjectiveCommitment | null;
```

(d) Add the PATCH body type after `ObjectiveCreateBody`:

```ts
// PATCH /objectives/{id} (S-obj-4) — the SPA always sends the FULL commitment (explicit null
// clears the nullable fields; no omitted-field ambiguity). The API also accepts partials.
export interface ObjectiveUpdateBody {
  target_value: string;
  unit: string;
  direction: ObjectiveDirection;
  due_date: string;
  at_risk_threshold: string | null;
  baseline_value: string | null;
  policy_id: string | null;
}
```

- [ ] **Step 2: ObjectiveCommitmentContext type lift** — in `apps/web/src/features/review/ObjectiveCommitmentContext.tsx`, delete the local `export interface ObjectiveCommitment {...}` and replace with:

```ts
import type { ObjectiveCommitment } from "../../lib/types";

// Re-export so existing importers (ReviewApprovePage) keep their path.
export type { ObjectiveCommitment };
```

- [ ] **Step 3: MSW** — in `apps/web/src/test/msw/handlers.ts`:

(a) Update `objectiveDetailFixture` (the serializer now ALWAYS carries pending_commitment on detail):

```ts
  capabilities: { submit: true, release: false, edit: true, start_revision: true },
  effective_from: null,
  pending_commitment: null,
```

(b) After `objectiveVersionWithCommitment`, add the revision-era fixtures:

```ts
// S-obj-4: the v2 revision commitment (target 95 → 97) + the still-governing v1 Effective version.
const objectiveCommitmentV2 = { ...objectiveCommitment, target_value: "97" };

export const objectiveVersionV2WithCommitment = {
  ...objectiveVersionWithCommitment,
  id: "veob2222-2222-2222-2222-222222222222",
  version_seq: 2,
  revision_label: "Rev B",
  change_reason: "Objective commitment revised",
  metadata_snapshot: { objective_commitment: objectiveCommitmentV2 },
  created_at: "2026-06-12T09:00:00+00:00",
} satisfies DocumentVersion;

export const objectiveVersionV1Effective = {
  ...objectiveVersionWithCommitment,
  version_state: "Effective",
  effective_from: "2026-06-01T09:00:00+00:00",
} satisfies DocumentVersion;

// An Effective objective with a revision in flight: the MAIN fields are the GOVERNING values
// (api/objectives.py _objective resolves the version snapshot); the edit lives ONLY in
// pending_commitment (detail-only). Pinned to the as-built serializer.
export const objectiveUnderRevisionDetailFixture: Objective = {
  ...objectiveFixtures[0]!,
  current_state: "UnderRevision",
  plans: objectivePlanFixtures,
  capabilities: { submit: true, release: false, edit: true, start_revision: true },
  effective_from: "2026-06-01T09:00:00+00:00",
  pending_commitment: { ...objectiveCommitmentV2 },
} satisfies Objective;
```

(c) Add the write handlers next to the existing objectives block (PATCH before the literal-tail
posts is fine — method-disambiguated):

```ts
  // S-obj-4: PATCH merges over the bare row (the api returns the LIST shape — no detail keys).
  http.patch("/api/v1/objectives/:id", async ({ request }) => {
    const body = (await request.json()) as Partial<Objective>;
    return HttpResponse.json({ ...objectiveFixtures[0]!, ...body });
  }),
  http.post("/api/v1/objectives/:id/start-revision", () =>
    HttpResponse.json({ ...objectiveFixtures[0]!, current_state: "UnderRevision" } satisfies Objective),
  ),
```

- [ ] **Step 4: Fix the tsc fallout** — the widened `capabilities` type breaks every fixture/test that builds a 2-flag object. Find them all:

Run (from `apps/web/`): `npx tsc --noEmit`
Expected failures list every site (at minimum `ObjectiveDetailPage.test.tsx`'s inline `capabilities:` objects). Update each to the 4-flag shape (`edit`/`start_revision` mirroring `submit`), and add `pending_commitment: null` where a detail fixture is spread. Re-run until clean.

- [ ] **Step 5: Web gates + commit**

Run: `npx eslint src && npx tsc --noEmit && npx vitest run src/features/objectives src/features/review`
Expected: clean; existing tests green.

```bash
git add apps/web/src/lib/types.ts apps/web/src/test/msw/handlers.ts apps/web/src/features/review/ObjectiveCommitmentContext.tsx apps/web/src/features/objectives apps/web/src/features/review
git commit -m "feat(s-obj-4): FE types + MSW — commitment type lift, 4-flag capabilities, pending_commitment, revision fixtures"
```

---

### Task 10: FE mutations — `useUpdateObjective` + `useStartObjectiveRevision`

**Files:**
- Modify: `apps/web/src/features/objectives/mutations.ts`
- Test: `apps/web/src/features/objectives/lifecycle.test.tsx` (append — the existing mutation-hook test file)

- [ ] **Step 1: Write the failing tests** — append to `lifecycle.test.tsx` (match the file's existing harness — it already wraps hooks in a QueryClient + MSW; **`import { expect, it } from "vitest"`**):

```tsx
it("useUpdateObjective PATCHes the commitment and invalidates the objective reads", async () => {
  // renderHook with the file's existing wrapper; mutateAsync with a full ObjectiveUpdateBody;
  // assert the resolved body echoes the PATCH (MSW merges it over the bare row).
});

it("useStartObjectiveRevision POSTs start-revision and lands UnderRevision", async () => {
  // mutateAsync(id) → resolved current_state === "UnderRevision".
});
```

(Write them concretely in the file's established `renderHook`/`waitFor` style — copy the
`useSubmitObjectiveForReview` test's harness verbatim and swap the hook + assertion.)

- [ ] **Step 2: Run to verify failure** — `npx vitest run src/features/objectives/lifecycle.test.tsx`
Expected: FAIL — hooks don't exist.

- [ ] **Step 3: Implement** — append to `mutations.ts` (after `useReleaseObjective`); add `ObjectiveUpdateBody` to the type import:

```ts
// S-obj-4: edit the working-copy commitment (objective.manage; Draft/UnderRevision — 409 otherwise).
// The SPA always sends the FULL body (explicit null clears); reads keep serving the GOVERNING
// commitment, so the edit shows up only via the detail's pending_commitment.
export function useUpdateObjective(objectiveId: string) {
  const api = useApi();
  const invalidate = useInvalidateObjective();
  return useMutation({
    mutationFn: (body: ObjectiveUpdateBody) =>
      api.send<Objective>("PATCH", `/api/v1/objectives/${objectiveId}`, body),
    onSuccess: () => invalidate(objectiveId),
  });
}

// S-obj-4: Effective→UnderRevision (T7) via the namespaced objective route (objective.manage —
// the QMS Owner holds no document.edit; the generic documents route is guarded on OBJ rows).
export function useStartObjectiveRevision() {
  const api = useApi();
  const invalidate = useInvalidateObjective();
  return useMutation({
    mutationFn: (id: string) =>
      api.send<Objective>("POST", `/api/v1/objectives/${id}/start-revision`),
    onSuccess: (_d, id) => invalidate(id),
  });
}
```

- [ ] **Step 4: Run to verify pass** — `npx vitest run src/features/objectives/lifecycle.test.tsx` → PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/objectives/mutations.ts apps/web/src/features/objectives/lifecycle.test.tsx
git commit -m "feat(s-obj-4): useUpdateObjective + useStartObjectiveRevision"
```

---

### Task 11: FE detail page — affordances, revision panel, StateBadge header, ProposedRevisionCard

**Files:**
- Create: `apps/web/src/features/objectives/ProposedRevisionCard.tsx`
- Create: `apps/web/src/features/objectives/ProposedRevisionCard.test.tsx`
- Modify: `apps/web/src/features/objectives/ObjectiveDetailPage.tsx`
- Test: `apps/web/src/features/objectives/ObjectiveDetailPage.test.tsx` (append)

- [ ] **Step 1: Write the failing tests** — append to `ObjectiveDetailPage.test.tsx` (use the file's existing render harness + `server.use(...)` per-test overrides; `import { expect, it } from "vitest"`; **the first content assertion must `waitFor`/`findBy`**):

```tsx
it("shows Start revision on an Effective objective with the capability", async () => {
  // override GET /objectives/:id → { ...objectiveDetailFixture, current_state: "Effective",
  //   effective_from: "2026-06-01T09:00:00+00:00" }
  // assert: findByRole button "Start revision"; NO "Submit for review"; NO "Edit commitment".
});

it("UnderRevision: calm revision panel replaces the stepper; Submit is offered", async () => {
  // override GET /objectives/:id → objectiveUnderRevisionDetailFixture (approval handler still
  // returns the COMPLETED v1 instance — the panel must show INSTEAD of the stepper).
  // assert: findByText /revision in progress/i and /keeps governing/i;
  //         queryByText("Released to effective") is NOT in the document (stepper hidden);
  //         button "Submit for review" present. (The Edit affordance + its test land in Task 12.)
});

it("renders the proposed-revision card with was→now rows when pending_commitment diverges", async () => {
  // objectiveUnderRevisionDetailFixture: governing target 95, pending 97
  // assert: findByText(/proposed revision/i); a row containing "95 % → 97 %".
});

it("hides Start revision without the capability", async () => {
  // Effective + capabilities { ...all true, start_revision: false } → no Start revision button.
});
```

(Write them fully in the file's idiom — the existing "shows Submit for review on a Draft…" test
is the copy-paste skeleton: same `renderDetail()` helper, same `server.use(http.get(...))`
override shape, same `findByRole("button", { name: ... })` assertions.)

- [ ] **Step 2: Run to verify failure** — `npx vitest run src/features/objectives/ObjectiveDetailPage.test.tsx` → new tests FAIL.

- [ ] **Step 3: Implement `ProposedRevisionCard`** — create `apps/web/src/features/objectives/ProposedRevisionCard.tsx`:

```tsx
import { Card, Stack, Table, Text } from "@mantine/core";
import type { Objective, ObjectiveCommitment } from "../../lib/types";

const DIRECTION_LABEL: Record<ObjectiveCommitment["direction"], string> = {
  HIGHER_IS_BETTER: "Higher is better",
  LOWER_IS_BETTER: "Lower is better",
};

// The governing→pending field pairs, formatted for display. The detail's MAIN fields ARE the
// governing commitment (the API read-back switch), so "was" comes straight off the objective.
function rows(o: Objective, p: ObjectiveCommitment): Array<{ label: string; was: string; now: string }> {
  const fmtNum = (v: string | null, unit: string) => (v !== null ? `${v} ${unit}` : "—");
  const all = [
    { label: "Target", was: `${o.target_value} ${o.unit}`, now: `${p.target_value} ${p.unit}` },
    { label: "Direction", was: DIRECTION_LABEL[o.direction], now: DIRECTION_LABEL[p.direction] },
    { label: "At-risk threshold", was: fmtNum(o.at_risk_threshold, o.unit), now: fmtNum(p.at_risk_threshold, p.unit) },
    { label: "Baseline", was: fmtNum(o.baseline_value, o.unit), now: fmtNum(p.baseline_value, p.unit) },
    { label: "Due date", was: o.due_date, now: p.due_date },
    {
      label: "Quality Policy",
      was: o.policy_id !== null ? "Linked" : "—",
      now: p.policy_id !== null ? "Linked" : "—",
    },
  ];
  return all.filter((r) => r.was !== r.now);
}

// S-obj-4: the in-edit (unapproved) commitment, shown calmly beside the governing one. Renders
// nothing when there is no divergence — the steady state stays unmarked.
export function ProposedRevisionCard({ objective }: { objective: Objective }) {
  const pending = objective.pending_commitment;
  if (!pending) return null;
  const changed = rows(objective, pending);
  return (
    <Card withBorder>
      <Stack gap="xs">
        <Text fw={600}>Proposed revision</Text>
        <Text size="xs" c="dimmed">
          Not yet in force — the released commitment keeps governing until this revision is
          approved and re-released.
        </Text>
        {changed.length === 0 ? (
          <Text size="sm" c="dimmed">No field changes (re-freeze pending).</Text>
        ) : (
          <Table withRowBorders={false} aria-label="Proposed commitment changes">
            <Table.Tbody>
              {changed.map((r) => (
                <Table.Tr key={r.label}>
                  <Table.Td>
                    <Text size="sm" c="dimmed">{r.label}</Text>
                  </Table.Td>
                  <Table.Td>{`${r.was} → ${r.now}`}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </Stack>
    </Card>
  );
}
```

Create `ProposedRevisionCard.test.tsx` (3 cases: null pending → renders nothing [assert container has no "Proposed revision" text]; diverging target → "95 % → 97 %" row; identical fields filtered out).

- [ ] **Step 4: Rewire `ObjectiveDetailPage.tsx`** — imports: add `StateBadge` (`../document/StateBadge`) + `ProposedRevisionCard` + `useStartObjectiveRevision`. (The Edit affordance, its `canEdit` gate, and the modal all land in Task 12 — this task ships the page WITHOUT any Edit reference, so nothing dangles.) Replace the gating + header + Lifecycle card:

```tsx
  const draftLike = o.current_state === "Draft" || o.current_state === "UnderRevision";
  const underRevision = o.current_state === "UnderRevision";
  // Affordances gate on capability AND state — quiet absence, never a dead button (the
  // AuthorActions posture: canRevise = Effective && caps; draftLike = Draft ∪ UnderRevision).
  const canSubmit = o.capabilities?.submit === true && draftLike;
  const canRelease = o.capabilities?.release === true && o.current_state === "Approved";
  const canStartRevision = o.capabilities?.start_revision === true && o.current_state === "Effective";
```

(Task 12 adds `const canEdit = o.capabilities?.edit === true && draftLike;` here.)

Header badge:

```tsx
          <Group gap="xs" mb={4} aria-label="Objective reference">
            <Text c="dimmed" size="sm" fw={500}>{o.identifier}</Text>
            <StateBadge state={o.current_state} />
          </Group>
```

Between `<CommitmentHero ... />` and the Lifecycle card: `<ProposedRevisionCard objective={o} />`.

The Lifecycle card:

```tsx
        {(canSubmit || canRelease || canStartRevision || instance) && (
          <Card withBorder>
            <Stack gap="sm">
              <Text fw={600}>Lifecycle</Text>
              {underRevision ? (
                // O-6a: the latest-instance read still returns v1's COMPLETED cycle here — the
                // stepper would render "Not yet released" against a doc that IS released. A calm
                // panel replaces it until re-submit creates the v2 instance.
                <Alert color="yellow" title="Revision in progress">
                  The released commitment keeps governing until this revision is approved and
                  re-released.
                </Alert>
              ) : (
                instance && (
                  <ApprovalStepper
                    instance={instance}
                    docState={o.current_state}
                    effectiveFrom={o.effective_from ?? null}
                    nameOf={nameOf}
                  />
                )
              )}
              {actionError && (
                <Alert color="red" withCloseButton onClose={() => setActionError(null)}>
                  {actionError}
                </Alert>
              )}
              {canStartRevision && (
                <Group>
                  <Button
                    variant="default"
                    loading={startRevision.isPending}
                    onClick={() => void doStartRevision()}
                  >
                    Start revision
                  </Button>
                  <Text size="xs" c="dimmed">
                    Opens an editable draft — the released commitment keeps governing.
                  </Text>
                </Group>
              )}
              {/* Task 12 inserts the canEdit "Edit commitment" button here. */}
              {canSubmit && (
                <Group>
                  <Button color="teal" loading={submit.isPending} onClick={() => void doSubmit()}>
                    Submit for review
                  </Button>
                  <Text size="xs" c="dimmed">Freezes the commitment and starts approval.</Text>
                </Group>
              )}
              {canRelease && (
                <Group>
                  <Button color="teal" loading={release.isPending} onClick={() => void doRelease()}>
                    Release
                  </Button>
                  <Text size="xs" c="dimmed">Releases the Approved objective → Effective.</Text>
                </Group>
              )}
            </Stack>
          </Card>
        )}
```

with the supporting state/handlers (mirroring `doSubmit`):

```tsx
  const startRevision = useStartObjectiveRevision();

  async function doStartRevision() {
    if (!id) return;
    setActionError(null);
    try {
      await startRevision.mutateAsync(id);
    } catch (e) {
      setActionError(errMsg(e));
    }
  }
```

- [ ] **Step 5: Run** — `npx vitest run src/features/objectives/ObjectiveDetailPage.test.tsx src/features/objectives/ProposedRevisionCard.test.tsx`
Expected: PASS (Edit-modal rendering itself lands in Task 12 — these tests assert the button only).

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/features/objectives
git commit -m "feat(s-obj-4): detail affordances — start-revision, revision panel, proposed-revision card, StateBadge header"
```

---

### Task 12: FE `EditCommitmentModal` + the Edit affordance

**Files:**
- Create: `apps/web/src/features/objectives/EditCommitmentModal.tsx`
- Create: `apps/web/src/features/objectives/EditCommitmentModal.test.tsx`
- Modify: `apps/web/src/features/objectives/ObjectiveDetailPage.tsx` (the `canEdit` gate + button + render)
- Test: `apps/web/src/features/objectives/ObjectiveDetailPage.test.tsx` (append the Edit-button test)

- [ ] **Step 1: Write the failing tests** — `EditCommitmentModal.test.tsx` (`import { expect, it, describe } from "vitest"`; the NewObjectiveModal.test.tsx harness is the skeleton):

```tsx
// Cases:
// 1. seeds from pending_commitment when present (target input shows "97", not the governing "95")
// 2. seeds from the objective fields when pending is null
// 3. saves: type a new target → Save → the PATCH body carries the FULL commitment with the
//    typed target (spy via server.use(http.patch(..., async ({request}) => {...capture...})))
// 4. clearing the threshold sends an explicit null (NOT omitted) — assert "at_risk_threshold" in
//    Object.keys(body) && body.at_risk_threshold === null
// 5. soft-warn: a backwards threshold (direction HIGHER_IS_BETTER, threshold > target) shows the
//    BandPreview warn and Save stays ENABLED (warn-not-block — the S-obj-2 posture)
// 6. reopen resets: dirty the target field, Cancel (unmounts via {open && ...}), reopen → the
//    seed value is back (the persistently-mounted-modal trap)
```

and append to `ObjectiveDetailPage.test.tsx`:

```tsx
it("offers Edit commitment on Draft/UnderRevision with the capability and opens the modal", async () => {
  // objectiveUnderRevisionDetailFixture → findByRole button "Edit commitment"; click → the
  // modal dialog appears (findByRole "dialog", name /edit commitment/i) seeded with the
  // PENDING target "97". Also: an Effective override shows NO Edit button (state gate).
});
```

- [ ] **Step 2: Run to verify failure** — `npx vitest run src/features/objectives/EditCommitmentModal.test.tsx` → FAIL (module missing).

- [ ] **Step 3: Implement** — create `EditCommitmentModal.tsx` (the NewObjectiveModal field set minus title/process; full-body PATCH):

```tsx
import {
  Alert, Button, Checkbox, Group, Input, Modal, SegmentedControl, Stack, Text, TextInput,
} from "@mantine/core";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import type { Objective, ObjectiveDirection, ObjectiveUpdateBody } from "../../lib/types";
import { useUpdateObjective } from "./mutations";
import { useEffectivePolicy } from "./hooks";
import { BandPreview } from "./BandPreview";

interface Props {
  opened: boolean;
  objective: Objective;
  onClose: () => void;
}

// S-obj-4 (O-1): edit the working-copy commitment. Seeds from pending_commitment when a revision
// edit is already in flight (the detail's MAIN fields are the GOVERNING values — seeding from
// them would silently revert a prior edit), else from the objective fields. Always sends the
// FULL body (explicit null clears). Parent renders {open && <EditCommitmentModal/>} so close
// unmounts + resets (the S-web-7d persistently-mounted-modal trap).
export function EditCommitmentModal({ opened, objective, onClose }: Props) {
  const update = useUpdateObjective(objective.id);
  const { data: policy, isError: policyError, isLoading: policyLoading } = useEffectivePolicy();
  const [error, setError] = useState<string | null>(null);

  const seed = objective.pending_commitment ?? {
    target_value: objective.target_value,
    unit: objective.unit,
    direction: objective.direction,
    due_date: objective.due_date,
    at_risk_threshold: objective.at_risk_threshold,
    baseline_value: objective.baseline_value,
    policy_id: objective.policy_id,
  };
  const [target, setTarget] = useState(seed.target_value);
  const [unit, setUnit] = useState(seed.unit);
  const [direction, setDirection] = useState<ObjectiveDirection>(seed.direction);
  const [dueDate, setDueDate] = useState(seed.due_date);
  const [baseline, setBaseline] = useState(seed.baseline_value ?? "");
  const [threshold, setThreshold] = useState(seed.at_risk_threshold ?? "");
  const [linkPolicy, setLinkPolicy] = useState(seed.policy_id !== null);

  const targetIsNumber = target.trim() !== "" && !Number.isNaN(Number(target));
  const canSave = targetIsNumber && unit.trim() !== "" && dueDate !== "";

  async function save() {
    setError(null);
    const body: ObjectiveUpdateBody = {
      target_value: target.trim(),
      unit: unit.trim(),
      direction,
      due_date: dueDate,
      at_risk_threshold: threshold.trim() === "" ? null : threshold.trim(),
      baseline_value: baseline.trim() === "" ? null : baseline.trim(),
      policy_id: linkPolicy && policy ? policy.id : null,
    };
    try {
      await update.mutateAsync(body);
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong saving the commitment.");
    }
  }

  return (
    <Modal opened={opened} onClose={onClose} title="Edit commitment">
      <Stack gap="sm">
        {error && <Alert color="red">{error}</Alert>}
        <Group grow>
          <TextInput label="Target" required value={target} onChange={(e) => setTarget(e.currentTarget.value)} />
          <TextInput label="Unit" required value={unit} onChange={(e) => setUnit(e.currentTarget.value)} />
        </Group>
        <Input.Wrapper label="Direction">
          <SegmentedControl
            fullWidth
            value={direction}
            onChange={(v) => setDirection(v as ObjectiveDirection)}
            data={[
              { value: "HIGHER_IS_BETTER", label: "Higher is better" },
              { value: "LOWER_IS_BETTER", label: "Lower is better" },
            ]}
          />
        </Input.Wrapper>
        <TextInput
          type="date" label="Due date" required value={dueDate}
          onChange={(e) => setDueDate(e.currentTarget.value)}
        />
        <Group grow>
          <TextInput label="Baseline" value={baseline} onChange={(e) => setBaseline(e.currentTarget.value)} />
          <TextInput label="At-risk threshold" value={threshold} onChange={(e) => setThreshold(e.currentTarget.value)} />
        </Group>
        <BandPreview target={target} threshold={threshold} direction={direction} />
        {policy ? (
          <Checkbox
            label={`Consistent with ${policy.identifier} — ${policy.title}`}
            checked={linkPolicy}
            onChange={(e) => setLinkPolicy(e.currentTarget.checked)}
          />
        ) : policyError ? (
          // Neutral copy on an errored read — never the positive "no policy yet" (S-home-1 class).
          <Text size="xs" c="dimmed">Couldn&apos;t load the Quality Policy — you can still save.</Text>
        ) : policyLoading ? null : (
          <Text size="xs" c="dimmed">No effective Quality Policy yet — the link is optional.</Text>
        )}
        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose}>Cancel</Button>
          <Button onClick={() => void save()} loading={update.isPending} disabled={!canSave}>
            Save changes
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
```

- [ ] **Step 4: Wire the Edit affordance into the detail page** — in `ObjectiveDetailPage.tsx`:

(a) import `EditCommitmentModal`; (b) add the state + gate next to the Task-11 gates:

```tsx
  const [editOpen, setEditOpen] = useState(false);
  const canEdit = o.capabilities?.edit === true && draftLike;
```

(c) widen the Lifecycle-card condition to `(canSubmit || canRelease || canStartRevision || canEdit || instance)`; (d) replace the Task-11 placeholder comment inside the card with the button:

```tsx
              {canEdit && (
                <Group>
                  <Button variant="default" onClick={() => setEditOpen(true)}>
                    Edit commitment
                  </Button>
                </Group>
              )}
```

(e) after the Lifecycle card's closing `)}`, conditionally render (unmount-on-close — the S-web-7d trap):

```tsx
        {editOpen && (
          <EditCommitmentModal opened objective={o} onClose={() => setEditOpen(false)} />
        )}
```

- [ ] **Step 5: Run** — `npx vitest run src/features/objectives/EditCommitmentModal.test.tsx src/features/objectives/ObjectiveDetailPage.test.tsx`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/features/objectives
git commit -m "feat(s-obj-4): EditCommitmentModal — pending-seeded, full-body PATCH, warn-not-block"
```

---

### Task 13: FE register state chips (O-6c)

**Files:**
- Modify: `apps/web/src/features/objectives/ObjectivesRegisterPage.tsx`
- Test: `apps/web/src/features/objectives/ObjectivesRegisterPage.test.tsx` (append)

- [ ] **Step 1: Write the failing tests** — append (the default fixtures are all `current_state: "Draft"`, so the chip shows on every default row; add an Effective override case):

```tsx
it("marks non-Effective rows with a state chip and leaves Effective rows clean", async () => {
  // default fixtures (all Draft) → each row shows the "Draft" StateBadge next to the Ref link;
  // then server.use(scorecard → rows with one current_state:"Effective" and one "UnderRevision")
  // → the Effective row has NO state badge; the UnderRevision row shows "Under revision".
});
```

(Concrete idiom: `within(row).getByLabelText("State: Draft")` — StateBadge sets
`aria-label={"State: " + label}`; the Effective row asserts `queryByLabelText(/^State:/)` is null
`within` that row.)

- [ ] **Step 2: Run to verify failure** — `npx vitest run src/features/objectives/ObjectivesRegisterPage.test.tsx`.

- [ ] **Step 3: Implement** — in `ObjectivesRegisterPage.tsx`: import `Group` (extend the Mantine import) + `import { StateBadge } from "../document/StateBadge";`, then the Ref cell:

```tsx
                <Table.Td>
                  <Group gap="xs" wrap="nowrap">
                    <Anchor component={Link} to={`/objectives/${o.id}`}>
                      {o.identifier}
                    </Anchor>
                    {/* O-6c: exception-marking — the steady state (Effective) stays unmarked;
                        Draft/InReview/UnderRevision/... get the shared StateBadge. */}
                    {o.current_state !== "Effective" && (
                      <StateBadge state={o.current_state} size="xs" />
                    )}
                  </Group>
                </Table.Td>
```

(`ObjectiveState` is now an alias of `DocumentCurrentState` — no prop-type friction.)

- [ ] **Step 4: Run + commit**

`npx vitest run src/features/objectives/ObjectivesRegisterPage.test.tsx` → PASS.

```bash
git add apps/web/src/features/objectives/ObjectivesRegisterPage.tsx apps/web/src/features/objectives/ObjectivesRegisterPage.test.tsx
git commit -m "feat(s-obj-4): register state chips on non-Effective objectives (O-6c)"
```

---

### Task 14: FE approver before/after (O-6b) + the two-commitment-version regression pin

**Files:**
- Modify: `apps/web/src/features/review/ObjectiveCommitmentContext.tsx`
- Modify: `apps/web/src/features/review/ReviewApprovePage.tsx`
- Test: `apps/web/src/features/review/ObjectiveCommitmentContext.test.tsx` (append)
- Test: `apps/web/src/features/review/ReviewApprovePage.test.tsx` (append)

- [ ] **Step 1: Write the failing tests:**

`ObjectiveCommitmentContext.test.tsx` — append:

```tsx
it("renders was→now for changed fields and plain values for unchanged ones on a revision", () => {
  // previous = { ...commitment, target_value: "95" }, commitment target "97", same unit "%":
  // assert getByText("95 % → 97 %"); the Due-date row renders plain "2026-12-31" (unchanged).
});

it("renders plain values when there is no previous commitment (first release)", () => {
  // previous omitted → the Target row is exactly "97 %" (no arrow).
});
```

`ReviewApprovePage.test.tsx` — append (THE missing two-version pin):

```tsx
it("a revision approval shows the NEWEST frozen commitment with was→now against the governing one", async () => {
  // server.use(versions → [objectiveVersionV2WithCommitment, objectiveVersionV1Effective])
  //   — newest-first, exactly what GET /documents/{id}/versions returns (version_seq DESC).
  // assert: the commitment card shows "95 % → 97 %" (v2 over v1) — i.e. .find picks the
  // NEWEST commitment (the InReview one being signed), and the SECOND-newest feeds "was".
});
```

- [ ] **Step 2: Run to verify failure** — `npx vitest run src/features/review`.

- [ ] **Step 3: Implement `ObjectiveCommitmentContext`** — add the `previous` prop + delta rendering:

```tsx
export function ObjectiveCommitmentContext({
  commitment,
  previous = null,
  title,
  identifier,
}: {
  commitment: ObjectiveCommitment;
  previous?: ObjectiveCommitment | null;
  title?: string;
  identifier?: string;
}) {
  // S-obj-4 (O-6b): on a revision, each CHANGED field renders "was → now" against the previous
  // frozen commitment (the governing one being superseded); unchanged fields render plain.
  const val = (f: (c: ObjectiveCommitment) => string) =>
    previous !== null && f(previous) !== f(commitment)
      ? `${f(previous)} → ${f(commitment)}`
      : f(commitment);
  const fmtTarget = (c: ObjectiveCommitment) => `${c.target_value} ${c.unit}`;
  const fmtDirection = (c: ObjectiveCommitment) => DIRECTION_LABEL[c.direction];
  const fmtThreshold = (c: ObjectiveCommitment) =>
    c.at_risk_threshold !== null ? `${c.at_risk_threshold} ${c.unit}` : "—";
  const fmtBaseline = (c: ObjectiveCommitment) =>
    c.baseline_value !== null ? `${c.baseline_value} ${c.unit}` : "—";
  const fmtDue = (c: ObjectiveCommitment) => c.due_date;
  const fmtPolicy = (c: ObjectiveCommitment) =>
    c.policy_id !== null ? "Linked to the Quality Policy" : "—";
  ...
```

subtitle: `{previous ? "The revised objective commitment you are approving — changes shown as was → now." : "The objective commitment you are approving."}`, and the rows become:

```tsx
            {row("Target", val(fmtTarget))}
            {row("Direction", val(fmtDirection))}
            {row("At-risk threshold", val(fmtThreshold))}
            {row("Baseline", val(fmtBaseline))}
            {row("Due date", val(fmtDue))}
            {/* R25: the Quality Policy is a singleton, so presence is unambiguous (Codex P2). */}
            {row("Quality Policy", val(fmtPolicy))}
```

- [ ] **Step 4: Implement the `ReviewApprovePage` pick** — replace the `objectiveCommitment` computation (lines 63-75):

```tsx
  // S-obj-3/4: an objective subject freezes its commitment into the version metadata_snapshot —
  // render that instead of a page redline. Detection keys on the snapshot field, never the
  // document type. versions is newest-first (version_seq DESC), so [0] is the InReview commitment
  // the approver is signing and [1] is the governing one it supersedes (the was→now source) —
  // pinned by the two-version revision test.
  const frozenCommitments = (versions ?? [])
    .map(
      (v) =>
        (v.metadata_snapshot as { objective_commitment?: ObjectiveCommitment } | null)
          ?.objective_commitment,
    )
    .filter((c): c is ObjectiveCommitment => Boolean(c));
  const objectiveCommitment = frozenCommitments[0] ?? null;
  const previousCommitment = frozenCommitments[1] ?? null;
```

and thread it: `<ObjectiveCommitmentContext commitment={objectiveCommitment} previous={previousCommitment} ... />`.

- [ ] **Step 5: Run + commit**

`npx vitest run src/features/review` → PASS (incl. the three pre-existing ObjectiveCommitmentContext tests — `previous` defaults null, rendering unchanged).

```bash
git add apps/web/src/features/review
git commit -m "feat(s-obj-4): approver was→now commitment delta + the two-version newest-first regression pin (O-6b)"
```

---

### Task 15: Full local gates

- [ ] **Step 1: API** — run the `/check-api` skill (or from `apps/api/`: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src && uv run pytest tests/unit -q`).
Expected: clean; unit failures ONLY in the 3 known Windows-baseline files.

- [ ] **Step 2: Web** — run the `/check-web` skill (or from `apps/web/`: `npx eslint src && npx tsc --noEmit && npm run build && npx vitest run --pool=forks --poolOptions.forks.singleFork=true`).
Expected: clean; note the new total test count (baseline was 679) for the slice-history entry. ⚠ the full parallel vitest run can flakily mass-fail with "document is not defined" — the singleFork flags above give the clean signal.

- [ ] **Step 3: Contracts** — `/check-contracts`. Expected: clean.

- [ ] **Step 4: Migrations** — `/check-migrations`. Expected: clean **no-op** (head stays `0049`; this slice ships no migration — a dirty `alembic check` here means an ORM change leaked).

- [ ] **Step 5: Fix any fallout, then commit**

```bash
git add -A
git commit -m "chore(s-obj-4): gate fixes"   # only if fixes were needed
```

---

### Task 16: Docs + wrap (slice-history entry, CLAUDE.md learnings line)

**Files:**
- Modify: `docs/slice-history.md` (the S-obj-4 entry, newest-first position)
- Modify: `CLAUDE.md` (one learnings line, newest-first; + the Current-status pointer)

- [ ] **Step 1: Write the slice-history entry** — follow the S-obj-3 entry's shape; it MUST cover: migration-free (head `0049`, catalog 100); the five owner forks O-1…O-6 + micro-calls A/B/C by name; the freeze-guard INVERSION (V-1) and the content-aware `commitment_needs_freeze`; the read-back switch closing F-2 (join = per-row PK probe; strings re-parse); O-2 governing-commitment capture + the unit-change reset; the four-writer guard + the rewritten open-seam test; the WD/lock release at submit; the FE surfaces (revision panel, was→now, register chips, EditCommitmentModal seeded from pending); test deltas (api integration count, web count from Task 15); deferrals carried (abandon-revision/T8, cycle history, mgmt-review routing, Process-Owner manage, charts, tunable RAG, title-edit path).

- [ ] **Step 2: Add the CLAUDE.md learnings line** (cap ~12 — demote the oldest if needed) — one dense line: S-obj-4 closes the S-obj-3 deferrals; the ⚠ traps worth carrying (freeze-guard polarity inverts on revision — content-compare, never `is None`; reads/measurements resolve the GOVERNING snapshot via one shared helper; the guard lives at endpoints for shared vault fns; no audit on working-copy edits is the system posture). Update the **Current status** tail to name S-obj-4.

- [ ] **Step 3: Commit**

```bash
git add docs/slice-history.md CLAUDE.md
git commit -m "docs(s-obj-4): slice-history entry + learnings"
```

---

### Task 17: Pre-PR verification (session-level — run by the orchestrator, not a fresh subagent)

- [ ] **Step 1: diff-critic** — run the `diff-critic` agent (`Agent` tool, `subagent_type: diff-critic`) on the full branch diff (`git diff main...HEAD`). Fix confirmed findings; re-run gates.

- [ ] **Step 2: Backend live smoke** (owner's box; Docker stack up) — rebuild first:

```bash
docker compose --env-file .env -f infra/compose/compose.yml -f infra/compose/compose.s.yml build api worker beat && docker compose --env-file .env -f infra/compose/compose.yml -f infra/compose/compose.s.yml up -d
```

then drive the full revision loop service-side via the worker heredoc (the S-obj-3 smoke pattern — `MSYS_NO_PATHCONV=1 docker compose ... exec -T worker sh -c "cd /app; uv run python -"` with a script that: creates an OBJ via the service, drives submit→approve→release with three seeded personas, records a `%` measurement, start-revision → PATCH (target+unit change) → re-submit → approve → re-release, then asserts: v1 `Superseded` + v2 `Effective` + `effective_count==1`; the 6.2 checklist row reads COVERED at EVERY step (start-revision included — R43); detail RAG/target read the NEW frozen commitment; `current_value` is None after the unit change; a new `count` reading 201s and re-rolls).

- [ ] **Step 3: FE live smoke** (Chrome MCP; the owner does the Keycloak login) — ⚠ **rebuild the web image too** (`docker compose ... up -d --build web` + hard refresh — api-only rebuilds leave the old bundle); grants = the s6 SYSTEM overrides on the LIVE `demo` `app_user` row (org **AHT**). Walk: `/objectives` register chips → detail Start revision → Edit commitment (BandPreview + save) → Proposed revision card + governing hero unchanged → Submit → `/tasks` approver card shows was→now → approve → Release → detail shows the new target, register re-grades.

- [ ] **Step 4: PR** — `/pr` on owner OK (squash-merge after green CI + Codex triage: disregard D1-moot multi-tenant framing; verify each claim against code before fixing).
