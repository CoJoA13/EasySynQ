# S-register-steward — the Register Steward role (R52) design note

> Status: approved (owner, 2026-06-21). BE-only. Migration **0062**. NO new permission key
> (catalog stays 102). NO contract change. NO FE change.

## What this is

The three register-steward consoles (Risk 6.1 / Context 4.1 / Interested Parties 4.2) shipped
self-service in the UI (S-risk-5, S-context-fe, S-interested-parties-fe), but their lifecycle acts
are only exercisable today by a **SYSTEM override**:

- **start-revision / publish** gate on `register.manage` @ SYSTEM — held by the **QMS Owner** role
  (`0004` line 185), so a QMS-Owner-roled user *can* already do these. (Process Owner holds
  `register.manage` only @ PROCESS, `0058` — the backend forces SYSTEM for the org-level head, so
  that PROCESS grant does not reach it.)
- **release** gates on `document.release` over the head's multi-axis `_register_release_scope`
  (artifact + folder + level + lifecycle_state + SoD-2). `document.release` is granted to **no
  seeded role** (`0004` line 47 is the catalog row only; `seed_personas.py:54` states it outright) —
  release of *every* document is **SYSTEM-override-only** in v1, by deliberate posture.

So "fully self-service register stewardship without an override" reduces to **granting release
authority via a role**. This slice seeds a dedicated reserved **Register Steward** role that holds
the full stewardship set, closing the F-1 deferral named across R49/R50/R51.

This is the FIRST seeded role to hold `document.release`, so v1 release authority becomes
**role-grantable**, not override-only — recorded as the binding decision **R52**.

## The owner decisions (AskUserQuestion, 2026-06-21)

1. **Next slice** = the Register Steward role (the register-arc capstone), over MR per-process-deny /
   stale-review / SMTP delivery / others.
2. **Release authority** = a **new dedicated Register Steward role** (over extending QMS Owner, or a
   narrow new `register.release` key). Cleanest SoD-2: the steward who *releases* is distinct from the
   QMS-Owner/Approver who *approves*. Trade-off accepted: `document.release @ SYSTEM` makes the role a
   universal releaser (still gated by SoD-2 + sig-hook + the leadership-authorization preflight).
3. **Key set** = the **full stewardship set**: `register.read · register.manage · document.release ·
   document.read · document.read_draft`, all @ SYSTEM. Deliberately **excludes** `document.approve`
   (SoD: the approver stays a separate Approver / QMS-Owner). `document.obsolete` omitted — register
   heads are reserved from obsoletion at the `lifecycle.obsolete` chokepoint (D-3b).
4. **Record as binding** = yes — **R52** + this spec + the register self-range bump `R1–R51 → R1–R52`.

## Binding constraints carried

- D1 single-org · D4 fixed stack · deny-by-default / deny-wins · ADMIN holds no `document.*` (the
  Register Steward is a content role, not the System Administrator — consistent).
- Permission catalog is **additive-only (R38)** — this slice adds **no key** (catalog stays 102); it
  adds a **role + role_grants** (data seed), which is the additive role-grant lane.
- WORM / append-only invariants untouched (no schema change).
- The migration uses the **resilient org lookup** (`DEFAULT` → else the sole org → else skip), per
  `services/common/org.py` + the 0053/0054 precedent — an operational install renames `short_code`
  away from `DEFAULT` (this install: `AHT`), so `0004`'s bare `scalar_one('DEFAULT')` would abort.

## As-built

### Migration `0062_register_steward_role.py` (data-only — no DDL, no `Base.metadata` change)

- Resilient org lookup → `org_id` (return early on an uninitialized DB).
- `pg_insert(role).values(org_id, name='Register Steward', is_reserved=True, description=…)
  .on_conflict_do_nothing(['org_id','name'])`.
- Resolve `role_id` + the 5 `permission_id`s; insert `role_grant` rows each with
  `scope_template={'level':'SYSTEM'}`, `.on_conflict_do_nothing(['org_id','role_id','permission_id'])`.
- **Downgrade** (both FKs to `role.id` are `ondelete=RESTRICT`): delete the steward's
  `role_assignment` rows → its `role_grant` rows → the `role` row, scoped to `name='Register Steward'`
  (within the resolved org). Permission/catalog rows untouched (no new key).
- `alembic check` stays clean (no DDL); migrations CI round-trips up↔down↔check.

### Authz behavior (the SoD shape)

- The steward **publishes** (`register.manage`) + **releases** (`document.release`), never
  **approves** (no `document.approve`). Register publish still routes its approval task to the
  Approver / QMS-Owner pool; release stays `releaser ≠ approver` (SoD-2 enforced at `pep.py`).
- The steward is **not** in the Top-Management candidate pool, so releasing a leadership artifact
  (POL §5.2 / OBJ §6.2 / MR §9.3) with the org flag ON still hits the Top-Management authorization
  preflight at the cutover chokepoint — `document.release @ SYSTEM` does **not** bypass leadership auth.
- The steward holds **no `document.approve`**, so it never receives register approval tasks (the
  approval candidate pool is `document.approve` holders + the QMS-Owner stage).

### Frontend — NO change

The steward consoles already gate their Publish / Start-revision / Release affordances on the
**server-computed** `register_capabilities` (`can_manage` = `register.manage` @ SYSTEM; `can_release`
= `document.release` over the head's multi-axis scope) — the whole point of S-context-fe / the
S-risk-5 Codex-r2 fix. So the moment a user holds the Register Steward role, the server caps compute
`true` and the buttons light up; the new role auto-appears in `GET /roles` for an admin to assign.
No client probe, no contract, no FE code touched.

### Tests

- **Unit `test_authz`** (integration file `tests/integration/test_authz.py`): add `Register Steward`
  to the role-name set assertion; assert its exact grant set (the 5 keys @ SYSTEM, and that it does
  **not** hold `document.approve`); keep `len(perms) == 102` (no new key).
- **Integration (headline — no SYSTEM override):** assign a fresh user **only** the Register Steward
  role; drive `start-revision → edit a row → publish` on the **Risk** register; a **separate**
  approver approves the pending task via the `/tasks` decision arm; then the steward **release**s →
  the register head goes Effective. Assert the server caps (`can_manage`/`can_release`) predict the
  outcome at each step.
- **Integration (role separation):** the Register Steward user gets **403** approving the register's
  pending task (lacks `document.approve`) — proving the steward cannot self-approve, so SoD holds at
  the role level.
- **Integration (non-regression, leadership):** with the leadership flag ON, the steward releasing a
  leadership artifact (OBJ or MR) still hits the Top-Management preflight (blocked) —
  `document.release @ SYSTEM` does not open a leadership bypass.
- Context / Interested-Parties get a lighter **cap-level** check (a steward sees `can_manage` /
  `can_release` true on `GET /context/register` + `GET /interested-parties/register`), not the full
  publish→approve→release loop (the lifecycle is the byte-identical clone proven for Risk).

### Docs

- This spec; decisions-register **R52** (+ self-range `R1–R51 → R1–R52`).
- A one-line role note in `docs/07` (role list) + `docs/14 §3.1` (seed roles).
- CLAUDE.md learning + `docs/slice-history.md` narrative (the `/finish-slice` pass).
- No `openapi.yaml` change.

## Named residuals (not faked)

- **Persona / demo mapping:** the 8 personas are unchanged; the Register Steward role is left for an
  admin to assign (the live-smoke assigns it to a fresh demo user to prove no-override stewardship).
  A 9th persona / a `seed-personas` steward is a deliberate non-goal.
- **Narrow `register.release` key:** the least-privilege alternative (release scoped to register
  heads only) was considered and deferred — it is an R38 catalog-open + a backend gate change across
  the 3 register release endpoints + `register_capabilities` + the contract; out of scope for this
  capstone (the owner chose the role-grant lane).
