# Register Steward Role (S-register-steward) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Seed a reserved `Register Steward` role so the three register-steward consoles (Risk 6.1 / Context 4.1 / Interested Parties 4.2) are exercisable without a SYSTEM override, closing the F-1 deferral (R49/R50/R51) — recorded as the binding decision R52.

**Architecture:** A data-only Alembic migration (`0062`) inserts one reserved role per org (idempotent, by-org, name-agnostic so it reaches a renamed `AHT` install) plus five `role_grant` rows at SYSTEM scope (`register.read · register.manage · document.release · document.read · document.read_draft`) — no new permission key, no schema change, no ORM change. The frontend is untouched: the steward consoles already gate on the **server-computed** `register_capabilities` (`can_manage`/`can_release`), so the role's grants light up the affordances automatically and the role auto-appears in `GET /roles` for an admin to assign.

**Tech Stack:** Python 3.12, Alembic, SQLAlchemy 2.x (sync psycopg2 in migrations), FastAPI, PostgreSQL 16, pytest + testcontainers (`-m integration`). Toolchain `uv` at `~/.local/bin/uv`.

## Global Constraints

- **Migration head:** current `0061_interested_party_register`; this slice adds **`0062_register_steward_role`** (next head). `down_revision = "0061_interested_party_register"`.
- **NO new permission key** — every key already exists (seeded `0004`); catalog count **stays 102** (R38: "additive" covers a new role + new grants on existing keys).
- **NO contract change** (`openapi.yaml` untouched — adding a role is data, not schema; `GET /roles` schema unchanged) and **NO frontend change**.
- **Data-only migration** — no DDL, no `Base.metadata`/ORM change, so `alembic check` stays clean.
- **Resilient/multi-org seed by NAME** (the `0057` precedent): seed for every org via `SELECT id FROM organization`, never a bare `WHERE short_code='DEFAULT'` (an operational install renames it to `AHT`; `services/common/org.py`). Return early if no org exists.
- **The key set excludes `document.approve`** (SoD: the approver stays a separate Approver/QMS-Owner). `is_reserved=True`.
- **Downgrade** deletes the steward's `role_assignment` rows → its `role_grant` rows → the `role` row (both FKs to `role.id` are `ondelete=RESTRICT`).
- Integration assertions are **run-scoped / delta-based**, never absolute counts on the shared session DB (except the seed-catalog test, which is the canonical exact-set assertion).
- Apply `.claude/rules/engineering-patterns.md` (alembic-check, additivity, run-scoped integration assertions, the static-route order — n/a here).

## File Structure

- **Create** `migrations/versions/0062_register_steward_role.py` — the seed migration (role + 5 grants; idempotent; downgrade).
- **Modify** `apps/api/tests/integration/test_authz.py` — extend `test_seed_catalog_and_roles`: add `Register Steward` to the exact role-name set + assert its grant set (5 keys @ SYSTEM, no `document.approve`) + `is_reserved`.
- **Modify** `apps/api/tests/integration/test_risk_lifecycle.py` — add `test_register_steward_role_drives_lifecycle_without_override` (the headline: role-only, no override, end-to-end publish→approve→release + steward-cannot-self-approve 403).
- **Create** `apps/api/tests/integration/test_register_steward_role.py` — the Context/IPR cap-level checks + the leadership non-regression (a steward's `document.release` does not bypass the Top-Management gate).
- **Modify** `docs/decisions-register.md` — add **R52** + bump the self-range `R1–R51 → R1–R52`.
- **Modify** `docs/07-authorization-model.md` + `docs/14-data-model.md §3.1` — one-line Register Steward note in the role list / seed roles.

> CLAUDE.md "Recent learnings" + `docs/slice-history.md` are handled by the `/finish-slice` pass after merge, not in this plan.

---

### Task 1: The seed migration `0062_register_steward_role.py`

**Files:**
- Create: `migrations/versions/0062_register_steward_role.py`

**Interfaces:**
- Produces: a reserved role named `Register Steward` per org, with `role_grant` rows for `register.read · register.manage · document.release · document.read · document.read_draft`, each `scope_template={"level":"SYSTEM"}`. Later tasks rely on the exact role name `"Register Steward"` and that exact grant set.

- [ ] **Step 1: Write the migration file**

```python
"""Seed the reserved Register Steward role (R52)

S-register-steward. Seeds a NEW reserved ``Register Steward`` role holding the full register
stewardship set at SYSTEM scope: ``register.read · register.manage · document.release ·
document.read · document.read_draft``. This is the FIRST seeded role to hold ``document.release``
(release was SYSTEM-override-only in v1) — so the three register-steward consoles (Risk 6.1 /
Context 4.1 / Interested Parties 4.2) become self-service without a SYSTEM override. The role
deliberately EXCLUDES ``document.approve`` (SoD: the approver stays a separate Approver / QMS-Owner;
register publish still routes its approval to that pool, and release stays releaser ≠ approver).

NO new permission key (every key already exists, seeded in 0004) → the catalog count stays 102
(R38: "additive" covers a new role + new grants on existing keys). Data-only — no schema change, so
``alembic check`` is unaffected and no ORM model changes.

Idempotent + multi-org by NAME (not the ``DEFAULT`` org 0004 targets, so it reaches a renamed
install such as ``AHT``): inserts the role for EVERY org via ``on_conflict_do_nothing`` on
``(org_id, name)``, then the 5 grants via a CROSS JOIN of the role rows with the permission rows
(``on_conflict_do_nothing`` on ``(org_id, role_id, permission_id)``). Returns early on an
uninitialized DB (no org).

Downgrade: both FKs to ``role.id`` are ``ondelete=RESTRICT``, so delete the steward's
``role_assignment`` rows → its ``role_grant`` rows → the ``role`` row (scoped by name). No permission
is added, so none is removed. Round-trips up/down/check on PG16.

Revision ID: 0062_register_steward_role
Revises: 0061_interested_party_register
Create Date: 2026-06-21
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

revision: str = "0062_register_steward_role"
down_revision: str | None = "0061_interested_party_register"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLE_NAME = "Register Steward"
_ROLE_DESC = (
    "Stewards the org-level registers (Risk 6.1 / Context 4.1 / Interested Parties 4.2): "
    "start-revision, publish, and release the controlled register heads. Holds document.release "
    "(the releaser, distinct from the QMS-Owner/Approver) — SoD-2 still applies."
)
_KEYS: tuple[str, ...] = (
    "register.read",
    "register.manage",
    "document.release",
    "document.read",
    "document.read_draft",
)
_SYSTEM_SCOPE: dict[str, Any] = {"level": "SYSTEM"}


def upgrade() -> None:
    bind = op.get_bind()
    role_t = sa.table(
        "role",
        sa.column("org_id", postgresql.UUID(as_uuid=True)),
        sa.column("name", sa.Text),
        sa.column("description", sa.Text),
        sa.column("is_reserved", sa.Boolean),
    )
    role_grant_t = sa.table(
        "role_grant",
        sa.column("org_id", postgresql.UUID(as_uuid=True)),
        sa.column("role_id", postgresql.UUID(as_uuid=True)),
        sa.column("permission_id", postgresql.UUID(as_uuid=True)),
        sa.column("scope_template", postgresql.JSONB),
    )

    # 1. Seed the reserved role for EVERY org (single-org D1; by-org keeps the 0057 multi-org shape
    #    and is name-agnostic, so it reaches a renamed install such as AHT).
    org_ids = [row.id for row in bind.execute(sa.text("SELECT id FROM organization")).all()]
    if not org_ids:
        return  # uninitialized DB — no org yet
    bind.execute(
        pg_insert(role_t)
        .values(
            [
                {
                    "org_id": org_id,
                    "name": _ROLE_NAME,
                    "description": _ROLE_DESC,
                    "is_reserved": True,
                }
                for org_id in org_ids
            ]
        )
        .on_conflict_do_nothing(index_elements=["org_id", "name"])
    )

    # 2. Resolve (org, Register Steward role, key permission) for every (org, key) — a CROSS JOIN of
    #    the just-seeded role rows with the 5 permission rows (each key already exists from 0004).
    stmt = sa.text(
        "SELECT r.org_id AS org_id, r.id AS role_id, p.id AS permission_id "
        "FROM role r CROSS JOIN permission p "
        "WHERE r.name = :role AND p.key IN :keys"
    ).bindparams(sa.bindparam("keys", expanding=True))
    rows = bind.execute(stmt, {"role": _ROLE_NAME, "keys": list(_KEYS)}).all()
    bind.execute(
        pg_insert(role_grant_t)
        .values(
            [
                {
                    "org_id": row.org_id,
                    "role_id": row.role_id,
                    "permission_id": row.permission_id,
                    "scope_template": _SYSTEM_SCOPE,
                }
                for row in rows
            ]
        )
        .on_conflict_do_nothing(index_elements=["org_id", "role_id", "permission_id"])
    )


def downgrade() -> None:
    bind = op.get_bind()
    # Both role_grant.role_id and role_assignment.role_id are ondelete=RESTRICT → delete the children
    # before the role. Scoped to the Register Steward role only (every other role untouched).
    bind.execute(
        sa.text(
            "DELETE FROM role_assignment WHERE role_id IN "
            "(SELECT id FROM role WHERE name = :role)"
        ),
        {"role": _ROLE_NAME},
    )
    bind.execute(
        sa.text(
            "DELETE FROM role_grant WHERE role_id IN (SELECT id FROM role WHERE name = :role)"
        ),
        {"role": _ROLE_NAME},
    )
    bind.execute(sa.text("DELETE FROM role WHERE name = :role"), {"role": _ROLE_NAME})
```

- [ ] **Step 2: Round-trip the migration on a throwaway PG16**

Run: `/check-migrations` (alembic up → down → up → `alembic check`).
Expected: PASS — `alembic check` reports no diff (data-only, no DDL); up seeds the role + 5 grants into the `DEFAULT` org (seeded by `0002`), down removes them cleanly.

- [ ] **Step 3: Lint + typecheck the migration**

Run: `/check-api` (ruff check + format-check + mypy-strict + unit).
Expected: PASS. (The PostToolUse `ruff --fix` hook may touch import order — re-verify the file still imports `pg_insert`.)

- [ ] **Step 4: Commit**

```bash
git add migrations/versions/0062_register_steward_role.py
git commit -m "feat(s-register-steward): seed the reserved Register Steward role (R52)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Catalog/role seed assertion (`test_authz.py`)

**Files:**
- Modify: `apps/api/tests/integration/test_authz.py` (inside `test_seed_catalog_and_roles`)

**Interfaces:**
- Consumes: the seeded `Register Steward` role + grants from Task 1; the existing `roles` list + `h` (admin headers) + `app_client` already in the test.

- [ ] **Step 1: Add the role name to the exact set + assert the grants**

In `test_seed_catalog_and_roles`, add `"Register Steward"` to the `assert names == {...}` set (it is an EXACT set — currently 9 names; this makes 10):

```python
    assert names == {
        "System Administrator",
        "QMS Owner",
        "Process Owner",
        "Author",
        "Approver",
        "Internal Auditor",
        "Employee (Read-only)",
        "External Auditor (Guest)",
        "Top Management",  # the Critical CAPA action-plan second-tier approver (S-capa-2, 0038)
        "Register Steward",  # the register-head lifecycle steward (S-register-steward, R52, 0062)
    }
```

Then, after the existing Process Owner `register.manage` block, append the Register Steward grant assertions:

```python
    # S-register-steward (migration 0062, R52): a NEW reserved Register Steward role holds the full
    # register stewardship set at SYSTEM — the FIRST seeded role to hold document.release (release
    # was SYSTEM-override-only in v1). It EXCLUDES document.approve (SoD: the approver stays
    # separate). No new key (catalog stays 102 — asserted above).
    steward = next(r for r in roles if r["name"] == "Register Steward")
    assert steward["is_reserved"] is True
    steward_grants = {
        g["permission_key"]: g["scope_template"]["level"]
        for g in (await app_client.get(f"/api/v1/roles/{steward['id']}", headers=h)).json()[
            "grants"
        ]
    }
    assert steward_grants == {
        "register.read": "SYSTEM",
        "register.manage": "SYSTEM",
        "document.release": "SYSTEM",
        "document.read": "SYSTEM",
        "document.read_draft": "SYSTEM",
    }
    assert "document.approve" not in steward_grants  # SoD: the approver stays a separate role
```

- [ ] **Step 2: Run the test**

Run: `cd apps/api && uv run pytest -m integration tests/integration/test_authz.py::test_seed_catalog_and_roles -v`
Expected: PASS. (Sanity that it has teeth: on head `0061` — i.e. before Task 1's migration — the role is absent and this assertion fails on the name set; with `0062` it passes.)

- [ ] **Step 3: Commit**

```bash
git add apps/api/tests/integration/test_authz.py
git commit -m "test(s-register-steward): assert the Register Steward role + its SYSTEM grants

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: The headline — role-only stewardship end-to-end (`test_risk_lifecycle.py`)

**Files:**
- Modify: `apps/api/tests/integration/test_risk_lifecycle.py` (add one test, reusing the in-file `subj` fixture + `restore_register_head` + `_drive_to_editable` + `_create_risk` + `_status` helpers + `s5` + `_auth`)

**Interfaces:**
- Consumes: `s5.grant_role(subject, "Register Steward")` (assigns the role, SYSTEM-bound; from `s5_helpers`), `s5.task_for_doc`, and the file's existing `_drive_to_editable`, `_create_risk`, `_status`, `restore_register_head`, `subj`.

- [ ] **Step 1: Write the test**

Append to `apps/api/tests/integration/test_risk_lifecycle.py`:

```python
async def test_register_steward_role_drives_lifecycle_without_override(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    restore_register_head: None,
) -> None:
    """S-register-steward (R52): a user holding ONLY the seeded Register Steward role (NO SYSTEM
    override) drives the full register lifecycle — start-revision/publish (register.manage) and
    release (document.release) — with a SEPARATE Approver approving. Proves the role makes
    stewardship self-service without an override, and that the steward CANNOT self-approve (the role
    excludes document.approve), so SoD holds at the role level. The restore_register_head teardown
    returns the shared head to editable even on mid-lifecycle failure."""
    # subj.steward publishes; subj.releaser releases — BOTH hold ONLY the Register Steward role
    # (no _grant overrides). subj.approver holds ONLY the Approver role.
    await s5.grant_role(subj.steward, "Register Steward")
    await s5.grant_role(subj.releaser, "Register Steward")
    await s5.grant_role(subj.approver, "Approver")
    hs = _auth(token_factory, subj.steward)
    hap = _auth(token_factory, subj.approver)
    hrl = _auth(token_factory, subj.releaser)
    await _drive_to_editable(app_client, hs, hap, hrl)

    # register.manage @ SYSTEM (from the role) → the steward sees can_manage True with no override.
    pre = await _status(app_client, hs)
    assert pre["can_manage"] is True

    row = await _create_risk(app_client, hs, likelihood=4, severity=5)  # 20 → critical
    head_id = row["register_doc_id"]
    pub = await app_client.post("/api/v1/risks/register/publish", headers=hs)
    assert pub.status_code == 200, pub.text
    assert pub.json()["state"] == "InReview"

    # role separation: the steward (no document.approve) is 403 on the approval task.
    task_id = await s5.task_for_doc(head_id)
    self_approve = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hs, json={"outcome": "approve"}
    )
    assert self_approve.status_code == 403, self_approve.text

    # the SEPARATE Approver approves.
    dec = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hap, json={"outcome": "approve"}
    )
    assert dec.status_code == 200, dec.text

    # the caps PREDICT the release: the third-party releaser (role's document.release) → can_release
    # True with NO override; the publishing steward is the version author → SoD-2 → can_release False.
    assert (await _status(app_client, hrl))["can_release"] is True
    s_caps = await _status(app_client, hs)
    assert s_caps["can_manage"] is True
    assert s_caps["can_release"] is False

    rel = await app_client.post("/api/v1/risks/register/release", headers=hrl)
    assert rel.status_code == 200, rel.text
    assert rel.json()["state"] == "Effective"
    # restore_register_head returns the shared head to editable (even on failure).
```

- [ ] **Step 2: Run the test**

Run: `cd apps/api && uv run pytest -m integration tests/integration/test_risk_lifecycle.py::test_register_steward_role_drives_lifecycle_without_override -v`
Expected: PASS. (On head `0061` it would error at `s5.grant_role(subj.steward, "Register Steward")` — `scalar_one()` finds no role — which is its red.)

- [ ] **Step 3: Commit**

```bash
git add apps/api/tests/integration/test_risk_lifecycle.py
git commit -m "test(s-register-steward): role-only register lifecycle end-to-end (no override)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Context/IPR cap reach + the leadership non-regression (`test_register_steward_role.py`)

**Files:**
- Create: `apps/api/tests/integration/test_register_steward_role.py`

**Interfaces:**
- Consumes: `s5.grant_role` + `s5.default_org_id` (from `s5_helpers`); `_auth` (from `test_vault`); `_approved_obj` + `_set_leadership_flag` (from `test_leadership_authorization` — the import pattern `test_dcr_implement` already uses).

- [ ] **Step 1: Write the file**

```python
"""S-register-steward (R52) integration proofs — the Register Steward role beyond the Risk register.

Two proofs the risk-lifecycle headline does not cover:
1. The role reaches Context (4.1) + Interested Parties (4.2) stewardship — a role-only user sees
   ``can_manage`` True on both register-status reads (head-state independent: register.manage @
   SYSTEM does not depend on the head's lifecycle state).
2. NON-REGRESSION: the role's ``document.release`` @ SYSTEM does NOT open a leadership-authorization
   bypass — with the org flag ON, a role-only steward releasing an Approved leadership artifact (OBJ)
   is still blocked by the Top-Management gate (409 leadership_authorization_required), NOT a missing
   grant. The steward is not in the Top-Management candidate pool.

Run-scoped (own ids); the org flag is flipped ON then reset OFF in a finally (the shared session DB).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient

from . import s5_helpers as s5
from .test_leadership_authorization import _approved_obj, _set_leadership_flag
from .test_vault import _auth

pytestmark = pytest.mark.integration


async def test_steward_role_reaches_context_and_ip_stewardship(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A user holding ONLY the Register Steward role can manage the Context (4.1) + Interested
    Parties (4.2) register heads: register.manage @ SYSTEM (from the role) → can_manage True on both
    status reads, with NO SYSTEM override. Head-state independent (no lifecycle driven), so it does
    not pollute the shared singleton heads."""
    subject = f"rs-cx-ip-{uuid.uuid4().hex[:8]}"
    await s5.grant_role(subject, "Register Steward")
    h = _auth(token_factory, subject)

    ctx = await app_client.get("/api/v1/context/register", headers=h)
    assert ctx.status_code == 200, ctx.text
    assert ctx.json()["can_manage"] is True

    ip = await app_client.get("/api/v1/interested-parties/register", headers=h)
    assert ip.status_code == 200, ip.text
    assert ip.json()["can_manage"] is True


async def test_steward_release_still_blocked_by_leadership_gate(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """NON-REGRESSION: granting document.release @ SYSTEM via the Register Steward role does NOT
    bypass the S-leadership-1 Top-Management gate. Drive an OBJ to Approved, flip the org flag ON,
    then have a role-only steward (≠ the OBJ author/approver, so SoD-2 does not fire) attempt the
    release: it is blocked with leadership_authorization_required (the steward got PAST the
    document.release authz — proving the role's grant works — and was stopped ONLY by the leadership
    preflight at the cutover chokepoint)."""
    org_id = await s5.default_org_id()
    salt = uuid.uuid4().hex[:8]
    oid, _hrq, _hrl = await _approved_obj(app_client, token_factory, salt)  # OBJ at Approved

    steward = f"rs-ld-{salt}"
    await s5.grant_role(steward, "Register Steward")  # ONLY the role — no override
    hrs = _auth(token_factory, steward)

    await _set_leadership_flag(org_id, True)
    try:
        blocked = await app_client.post(f"/api/v1/objectives/{oid}/release", headers=hrs)
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["code"] == "leadership_authorization_required"
    finally:
        await _set_leadership_flag(org_id, False)
```

- [ ] **Step 2: Run the file**

Run: `cd apps/api && uv run pytest -m integration tests/integration/test_register_steward_role.py -v`
Expected: PASS (both tests). If the OBJ release 403s instead of 409, the steward lacks `document.release` — i.e. the migration grant is wrong; a 409 `leadership_authorization_required` proves the grant works AND the gate holds.

- [ ] **Step 3: Commit**

```bash
git add apps/api/tests/integration/test_register_steward_role.py
git commit -m "test(s-register-steward): role reaches CTX/IPR; no leadership-gate bypass

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Docs — R52 + the role-list notes

**Files:**
- Modify: `docs/decisions-register.md` (add R52; bump the self-range)
- Modify: `docs/07-authorization-model.md` (role list)
- Modify: `docs/14-data-model.md` (§3.1 seed roles)

**Interfaces:** none (docs only).

- [ ] **Step 1: Add the R52 entry**

Find the last decision (R51) in `docs/decisions-register.md` and add, mirroring the existing entry format:

```markdown
### R52 — Register Steward role (clause 4.1/4.2/6.1 register stewardship) — 2026-06-21

The three register-steward consoles (Risk 6.1 / Context 4.1 / Interested Parties 4.2) are made
self-service by a NEW reserved **Register Steward** role (migration `0062`) holding the full
stewardship set at SYSTEM scope — `register.read · register.manage · document.release ·
document.read · document.read_draft` — and deliberately EXCLUDING `document.approve` (SoD: the
approver stays a separate Approver/QMS-Owner; register publish still routes its approval to that
pool, and release stays releaser ≠ approver). This is the FIRST seeded role to hold
`document.release`, so v1 release authority becomes **role-grantable** rather than
SYSTEM-override-only; release remains gated by SoD-2 + the signature hook + (for POL/OBJ/MR) the
Top-Management leadership-authorization preflight (the steward is not in that pool and cannot bypass
it). No new permission key (catalog stays 102; R38 additive covers a new role + grants on existing
keys). Supersedes the F-1 deferral named in R49/R50/R51.
```

Then bump the register's self-range header from `R1–R51` to `R1–R52` (search for `R1–R51`).

- [ ] **Step 2: Add the role-list one-liners**

In `docs/07-authorization-model.md`, in the seeded-role list, add a `Register Steward` row mirroring the existing role entries (name + "stewards the org-level registers: start-revision/publish/release; holds `document.release` @ SYSTEM, excludes `document.approve` for SoD").

In `docs/14-data-model.md §3.1` (seed roles), add the same `Register Steward` row to the roles table/list.

- [ ] **Step 3: Lint the contract (unchanged, sanity) + commit**

Run: `/check-contracts`
Expected: PASS (no `openapi.yaml` change).

```bash
git add docs/decisions-register.md docs/07-authorization-model.md docs/14-data-model.md
git commit -m "docs(s-register-steward): R52 + the Register Steward role list entries

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage:**
- Migration 0062 (role + 5 grants, resilient/multi-org, downgrade) → Task 1. ✓
- No new key / catalog stays 102 → asserted Task 2. ✓
- SoD shape (no `document.approve`; separate approver; release-not-approve) → Task 1 docstring + Task 3 (self-approve 403) + Task 2 (no `document.approve` grant). ✓
- Server-cap-driven FE, no FE change → no FE task (verified: `lib/types.ts` has no role union; RisksRegisterPage role mention is a comment). ✓
- Tests: unit catalog/role (Task 2), Risk end-to-end no-override (Task 3), CTX/IPR cap reach + leadership non-regression (Task 4). ✓
- Docs R52 + self-range + docs/07 + docs/14 (Task 5). ✓
- No `openapi.yaml` change → Task 5 lints it unchanged. ✓

**2. Placeholder scan:** none — every step has the actual file content/command/expected output.

**3. Type/name consistency:** the role name `"Register Steward"`, the 5 keys, and `scope_template["level"]=="SYSTEM"` are identical across the migration (Task 1), the assertion (Task 2), the lifecycle test (Task 3), and R52 (Task 5). `down_revision = "0061_interested_party_register"`; new head `0062_register_steward_role`. Helpers consumed exist: `s5.grant_role`/`s5.task_for_doc`/`s5.default_org_id`, `_drive_to_editable`/`_create_risk`/`_status`/`restore_register_head`/`subj` (in `test_risk_lifecycle.py`), `_approved_obj`/`_set_leadership_flag` (in `test_leadership_authorization.py`), `_auth` (in `test_vault.py`).

**4. Pre-PR gate (after all tasks):** `/check-api` + `/check-migrations` + `/check-contracts` → `diff-critic` + `migration-reviewer` + a 3-lens adversarial Workflow → **Codex** (authz-sensitive) → a pre-merge live-smoke (assign the role to a fresh user, prove the consoles light up with no override; the owner does the Keycloak login). `/check-web` is N/A (no FE change).
