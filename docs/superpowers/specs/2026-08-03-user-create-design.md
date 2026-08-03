# Design — S-user-create: one-step user creation from the Admin SPA

> **Status:** approved design, pending implementation plan.
> **Date:** 2026-08-03 · **Repo commit at design time:** `98f10f8` · **Migration head:** `0084` → this slice adds `0085`
> **Source:** LAB handoff [§4.1](../plans/2026-08-02-lab-handoff.md) — "Easier user creation", owner-selected 2026-08-03.
> **Permission catalog:** 102 keys, unchanged by this slice (no new key).

---

## 1. Why this document exists

Bringing a person into EasySynQ today is **two disconnected steps in two different systems**:

1. Create the Keycloak account — `scripts/new-keycloak-user.sh` at the server, or the Keycloak
   admin console — and read back its `sub`.
2. Paste that `sub` into **Administration → Users** to pre-create the `INVITED` `app_user` row.

Step 1 requires shell access to the host. For an administrator who is also an ordinary user of the
install, that means physically travelling to the machine to add a colleague. The seam is not
conceptual — it is a round trip.

`apps/api/src/easysynq_api/api/users.py` already names the fix as a known deferral:

> *"the operator creates the Keycloak account out-of-band — in-app Keycloak admin-API provisioning
> is a v1 convenience, D1/no-Keycloak-in-CI"*

**This slice is that deferral.** After it, creating a user is one form in the Admin SPA, reachable
from anywhere the app is.

### 1.1 What this slice is *not*

The System Administrator holding **no `document.*` content keys** is a deliberate segregation-of-duties
decision (deny-by-default, doc 07), **not** a defect and **not** in scope. Nothing here widens ADMIN's
reach into the QMS; it only removes the shell round trip from account provisioning.

---

## 2. Decisions taken (owner, 2026-08-03)

| # | Question | Decision |
|---|----------|----------|
| D1 | How does the new person get their first credential? | **Generated temporary password, shown once in the SPA.** Keycloak forces them to choose their own at first login. No SMTP dependency. |
| D2 | Username already exists in Keycloak but is unlinked? | **Offer to link it inline** from the create form. |
| D3 | Roles assigned during creation? | **Role picker in the create form**, optional. |
| D4 | Fate of the old paste-a-`sub` invite? | **Keep the API, drop it from the UI.** |
| D5 | Include a password-reset action? | **Yes** — an "Issue new temp password" action on an existing user. |

### 2.1 Why not email (the rejected option)

A Keycloak *execute-actions* email is the better end-user experience and remains the natural
successor. It is **not viable today**:

- the shipped realm (`infra/compose/keycloak/realm-export.json`) has **no `smtpServer` block** — Keycloak
  cannot send mail at all until realm SMTP is configured;
- `SMTP_*` defaults to Mailpit, a development catcher (`.env.example`: "email is best-effort in MVP");
- configuring a relay is a **site-infrastructure** task with unknown constraints (reachable relay,
  modern-auth restrictions on hosted mail, egress on 587/25, SPF and IP allow-listing), and it is only
  testable *at the site* — the exact round trip this slice exists to remove.

Making the first credential depend on SMTP would therefore **block user creation on a site visit**, and
leave it blocked if the relay does not cooperate.

Recorded for later: EasySynQ's *own* notification email is already fully built and merely dormant
(`config.py` `smtp_*`, a drain worker, DB-backed templates, per-user cadence under R54); an empty
`smtp_host` means "no deliverable transport" and the drain suppresses. **Configuring a relay therefore
unlocks the whole notification family, not just this flow** — which is why it deserves its own slice
with its own payoff rather than being welded on as a prerequisite here. This design leaves the seam
(§9) so email becomes additive.

---

## 3. Verified grounding

Every fact below was read from the tree at `98f10f8`, not assumed.

| Fact | Source | Consequence |
|---|---|---|
| `api`, `worker`, `beat`, `migrate` share `x-api-env: env_file: ../../.env` | `infra/compose/compose.yml:20` | **The API container already holds `KEYCLOAK_ADMIN_URL/USER/PASSWORD`. No compose change is needed.** |
| `services/keycloak_admin.py` — fail-closed httpx admin client with an injectable `_transport` | that module | The precedent to extend. Its `_transport` seam is how CI tests without Keycloak. |
| `services/backup/realm_export.py` is deliberately **fail-open** | its docstring | Do **not** copy that posture — a backup must degrade, provisioning must not. |
| `httpx.MockTransport` + `tests/unit/test_keycloak_admin.py` | tests | Established no-Keycloak-in-CI pattern (D1). |
| `realm_name_from_issuer(oidc_issuer)` | `realm_export.py` | Reuse for realm resolution; do not hard-code `easysynq`. |
| Realm `passwordPolicy = length(12) and notUsername(undefined)` | `realm-export.json` | A generated password violating this fails **at the site**, in `set-password`. Must be unit-pinned. |
| `loginWithEmailAllowed: true`, `duplicateEmailsAllowed` unset | `realm-export.json` | Keycloak also rejects a **duplicate email** — a second collision case, distinct from username. |
| `bruteForceProtected: true` | `realm-export.json` | Repeated bad logins lock the account; the error surface should not be mistaken for a bad temp password. |
| Role assignment is gated by **`permission.grant`**, not `user.create`, and passes `assert_can_assign_role` | `api/authz.py:274` | The role picker needs its own gate and must not bypass the SoD guard. |
| JIT reconciles `INVITED → ACTIVE` on first login by `keycloak_subject` | `auth/dependencies.py` | Unchanged. Provisioned users ride the existing path. |
| `EventType` has only `USER_CREATED`, `USER_STATUS_CHANGED` | `db/models/_audit_enums.py:127` | A credential issuance has no honest existing event → one additive enum value (§8). |
| Alembic head is `0084_clause7_support_do` | `uv run alembic heads` | This slice's migration is **0085**. |

---

## 4. API design

### 4.1 New endpoint — `POST /api/v1/users/provision`

Purely **additive**. `POST /api/v1/users` (invite-by-subject) keeps its exact contract, tests, and
OpenAPI shape; it is no longer surfaced in the UI but remains the escape hatch for scripted and
federated setups, and the fallback when the Keycloak admin API is unreachable.

> **Route ordering.** Mount the static `/users/provision` route **before** `/users/{user_id}`, per the
> repo's static-before-parametrised rule. The methods differ (`POST` vs `GET`/`PATCH`) so there is no
> actual shadow today, but honoring the pattern is free and survives a later `POST /users/{id}`.

**Request**

```jsonc
{
  "username":     "jdoe",              // required — the Keycloak login name
  "display_name": "J. Doe",            // optional
  "email":        "jdoe@example.local",// optional
  "first_name":   "J",                 // optional — Keycloak profile
  "last_name":    "Doe",               // optional
  "role_ids":     ["<uuid>", "..."]    // optional; empty/absent = no roles
}
```

**201 response** — the only time the password is ever transmitted:

```jsonc
{
  "user": { /* the standard _represent(...) shape from users.py */ },
  "temporary_password": "…",
  "password_delivery": "shown_once"
}
```

`password_delivery` is the **forward seam for §2.1**: when realm SMTP later exists, an
`emailed_link` variant can be added and `temporary_password` omitted, without a breaking change to
clients that already read this field. It is the one piece of future-proofing this design carries,
because it costs a single enum-valued string today and avoids reshaping the response later.

**Error responses** (RFC-problem, matching the existing surface):

| Status | `code` | Meaning |
|---|---|---|
| 409 | `keycloak_username_exists_unlinked` | Username exists in Keycloak with **no** `app_user`. Body carries `keycloak_subject` → drives the D2 link affordance. |
| 409 | `user_exists` | Username exists in Keycloak **and** is already linked to an `app_user`. Nothing to offer. |
| 409 | `keycloak_email_exists` | Keycloak rejected a duplicate email (`loginWithEmailAllowed`). Distinct message so the operator edits the right field. |
| 422 | `validation_error` | Empty/invalid username; unknown `role_ids`. |
| 502 | `keycloak_unavailable` | Admin API unreachable or returned an unusable response. |
| 503 | `keycloak_not_configured` | Admin credentials absent — fail **closed**, never a silent skip. |

### 4.2 The link mechanism (D2) — no new lookup endpoint

The `409 keycloak_username_exists_unlinked` body carries the existing `keycloak_subject`. The SPA's
**"Link the existing account"** button calls **the existing `POST /api/v1/users`** with that subject.

This is deliberate and it is what makes D4 pay for itself: the endpoint we chose to keep *becomes* the
link implementation. Consequences:

- **no new lookup/search endpoint**, and therefore no account-enumeration surface beyond what
  `user.create` already implies;
- linking **does not touch the existing account's password** — the person may already know it. If they
  do not, the operator uses the §4.3 reset action as a separate, deliberate step.

### 4.3 Password reset — `POST /api/v1/users/{user_id}/temporary-password` (D5)

Issues a fresh generated temporary password for an existing linked user, reusing the identical
Keycloak `set-password` call the provision flow already needs. Returns the same show-once shape.

Gated on `user.create` (issuing a credential is the same authority as creating the account; see §7).
Two jobs, both real:

1. it repairs the §5 step-5 failure (a row exists, the account has no credential);
2. it removes the last operational reason to open `scripts/new-keycloak-user.sh`, which today doubles
   as the password-reset tool.

---

## 5. Sequencing and the failure model

Keycloak is an external system and **cannot join the PostgreSQL transaction**. The order below is
chosen so that every failure is recoverable *without ever deleting a Keycloak account*.

```
0. Generate the temporary password           in-process; never leaves this request
1. Precheck username in Keycloak             (exact=true)
2. Create the Keycloak account                enabled, NO credential yet
3. Read back its `sub`
4. PG transaction: app_user(INVITED) + role assignments + audit  → COMMIT
5. Set the temporary password in Keycloak
6. Return the password to the caller — once
```

### 5.1 Why this order

- **The account is created without a credential** (step 2 before step 5). If the PG write fails, the
  orphan account is *unusable* rather than a live account with a password nobody was shown.
- **The PG commit precedes the password** so a successful response always implies a real `app_user`.

### 5.2 Failure matrix

| Fails at | State left behind | Recovery |
|---|---|---|
| 1 (precheck) | nothing | Error surfaced; retry. |
| 2 (KC create) | nothing | Error surfaced; retry. |
| 4 (PG txn) | Keycloak account exists, **no credential**, no `app_user` | **Retry the same username** → precheck finds it unlinked → the D2 link path adopts it, then §4.3 issues a password. *The D2 answer is the recovery mechanism, for free.* |
| 5 (set password) | `app_user` exists, account has no credential | **§4.3 reset action.** This is precisely why D5 is in scope. |

### 5.3 Non-destructive posture (binding)

**EasySynQ never deletes a Keycloak account.** No compensating delete, no rollback-by-deletion, in any
path. Deleting identity records to tidy a half-failed write is the kind of destructive compensation
that turns a transient error into data loss — and a pre-existing account must never be removable by a
failed provision attempt. Orphans are recovered by adoption (§5.2), not erased.

### 5.4 Precheck exactness (inherited hard lesson)

The Keycloak admin `GET /users?username=X` query is a **contains** match — querying `ann` also returns
`joann`. `scripts/new-keycloak-user.sh` documents this and the fix: `exact=true`, **plus** re-verifying
the returned username before acting on it.

Equally load-bearing, from the same script: a **failed lookup is not proof of absence**. A transient
403/5xx must **not** fall through to CREATE. The client distinguishes three outcomes — *found* /
*definitively absent* / *lookup failed* — and only *definitively absent* proceeds to create.

---

## 6. Password generation and secrets hygiene

### 6.1 Generation

- Generated **server-side** with `secrets` — never client-side, never operator-chosen.
- **Must satisfy the live realm policy**: `length(12)` and `notUsername`. Target ≥16 characters and
  assert the username does not appear in the generated value (case-insensitively).
- Grouped for legible transcription (the value is read aloud or copied by hand).
- **Unit-pinned against the policy.** A generated password that violates the realm policy would fail
  only at `set-password` — at the site, in front of the person being onboarded. That failure mode must
  be impossible to ship.

### 6.2 Hygiene (binding)

- Returned in the 201/200 body **once**. Never persisted in PostgreSQL, never written to a log, never
  placed in an audit `before`/`after` payload, never in an error message or problem detail.
- The audit trail records **that a credential was issued** — never its value (§8).
- The SPA holds it in component state only, and clears it when the panel closes. It is not written to
  `localStorage`/`sessionStorage`, not put in a URL, and not re-fetchable.
- **Reissue means reset** — there is no "show it again". The UI must say so plainly, because an
  operator who assumes otherwise will close the panel and lose it.
- The `env_val` dotenv-parsing bug tracked as issue #422 (mishandling of escaped quotes) lives in the
  shell script and **must not be reproduced server-side** — settings come from pydantic config, which
  does not share that parser.

---

## 7. Permissions

**No new permission key. The catalog stays at 102 (R38 untouched).**

| Action | Gate |
|---|---|
| `POST /users/provision` | `user.create` |
| `POST /users/{id}/temporary-password` | `user.create` |
| Supplying `role_ids` on provision | **additionally** `permission.grant`, and each role through the existing `assert_can_assign_role` SoD guard |

Rationale for reusing `user.create`: this is a *richer implementation of the same capability* — bring a
new person into the system — not a new capability. The System Administrator already holds it. Adding a
key would be an R38 register-level change requiring the owner's decision, and nothing here needs one.

The role leg is **not** folded into `user.create`: assigning roles is a genuinely distinct authority
that already has its own key and its own segregation-of-duties guard. Bypassing `assert_can_assign_role`
because the assignment happens to originate from the create form would be a privilege-escalation seam.

**Web affordance gating** follows the repo rule (per-key, at the resource's scope): the role picker
renders only when the caller holds `permission.grant`; the create button only with `user.create`.

---

## 8. Audit and migration

### 8.1 Events

| Path | Event | Payload |
|---|---|---|
| Provision (new Keycloak account) | `USER_CREATED` (existing) | `after = {status: INVITED, email, provisioning: "keycloak_created", credential_issued: true}` |
| Link an existing account (via kept `POST /users`) | `USER_CREATED` (existing, unchanged) | unchanged |
| Issue temp password (§4.3) | **`USER_CREDENTIAL_ISSUED`** (new) | `after = {credential_issued: true}` — **never the value** |

All events keep `object_type=user`, are emitted **pre-commit** so the change and its audit row commit
atomically (the existing `_emit_user_event` contract), and leave hashes NULL per R12.

### 8.2 Migration `0085` — additive enum value

Adding `USER_CREDENTIAL_ISSUED` to the `event_type` PostgreSQL enum:

- `ALTER TYPE … ADD VALUE` — the established additive pattern (precedents `0011`, `0012`, `0013`),
  with a **no-op downgrade**;
- the migration's enum tuple is **sourced from `EVENT_TYPE_VALUES`** in the ORM, not hand-retyped
  (the 0010 precedent), so a from-scratch `upgrade head` and a migrated database converge;
- the matching Python member is added to `EventType`.

Reusing `USER_STATUS_CHANGED` was rejected — no status changes, and a misleading audit record is worse
than a migration. There is no schema change beyond this enum value: `app_user` already carries
`keycloak_subject`, `display_name`, `email`, and `status`.

---

## 9. Web UI (`apps/web/src/admin/UsersAdmin.tsx`)

Today the file is 384 lines: a roster table, an **Invite user** modal (subject/display_name/email), and
a **Manage** drawer (`ManageUser`) handling roles and overrides.

### 9.1 Changes

- **Roster header** exposes a single primary action: **Create user**. The "Invite user" button is
  removed (D4); the modal component and its API call go with it.
- **Create user modal** — username (required), display name, email, first/last name, and an optional
  multi-role picker sourced from the existing `/roles` query, rendered only with `permission.grant`.
- **Collision state (D2)** — on `409 keycloak_username_exists_unlinked` the modal shows the inline
  warning and two actions: *Link the existing account* (calls the kept `POST /users` with the returned
  subject) and *Choose a different username*. A duplicate-email 409 is reported against the email field.
- **Success panel** — the created user plus the show-once temporary password with a copy control and
  unambiguous wording that it cannot be shown again.
- **Manage drawer** — gains an **Issue new temp password** action (§4.3) reusing the same show-once panel.

### 9.2 File size

Adding the create modal, collision state, and success panel to a 384-line file would push it past
comfortable. The create flow is extracted into its own component (`CreateUserModal.tsx`) with the
show-once panel as a small shared piece reused by the Manage drawer's reset action. `UsersAdmin.tsx`
keeps the roster and composition. This is targeted to the work at hand — no unrelated refactoring.

### 9.3 Scope seam — explicitly *not* in this slice

Handoff **§4.2** (permission-key visibility on a user) and **§4.4** (user profile) both touch this
screen and are **separate backlog items**. This slice adds the create/link/reset flows and **nothing**
about displaying effective permissions. `GET /users/{id}/effective-permissions` already exists and is
the natural foundation for §4.2 — it is deliberately left unconsumed here.

---

## 10. Testing

Constraint: **no Keycloak in CI (D1)**. Every Keycloak interaction is exercised through
`httpx.MockTransport` via the injectable `_transport`, following `tests/unit/test_keycloak_admin.py`.

### 10.1 Unit (`apps/api/tests/unit`)

- **Password policy conformance** — generated passwords satisfy `length(12)` and `notUsername`
  (including a username-substring case). Non-negotiable per §6.1.
- **Precheck exactness** — a contains-style match (`ann` vs `joann`) must not be treated as found;
  a mismatched returned username is refused.
- **Lookup failure ≠ absence** — a 403/5xx during precheck must **not** proceed to create.
- **Fail-closed configuration** — absent admin credentials raise, never silently skip (contrast with
  `realm_export.py`'s deliberate fail-open).
- **Secrets hygiene** — the generated password appears in no log record and no audit payload.

### 10.2 Integration (`apps/api/tests/integration`)

- Provision happy path → `app_user` INVITED, roles assigned, `USER_CREATED` audit emitted.
- Each 409 branch: unlinked-username (asserting the subject is returned), already-linked, duplicate email.
- **PG-failure recovery** — simulate a step-4 failure, then prove the retry surfaces the unlinked-link
  path (the §5.2 contract).
- Role leg authorization: `user.create` without `permission.grant` is refused when `role_ids` is
  supplied, and `assert_can_assign_role` is not bypassed.
- Temporary-password endpoint emits `USER_CREDENTIAL_ISSUED`.

Assertions are **run-scoped/delta-based** — the integration suite shares one session database, so no
absolute counts. Any `audit_event` write pins `occurred_at` inside a seeded monthly partition.

### 10.3 Web (`apps/web`)

- MSW fixtures **pinned to the real serializer shapes** (`_represent` and the new 201 body), using
  `satisfies` so strict `tsc` enforces them — never hand-typed guesses.
- Test files import `expect`/`it` **from `"vitest"`** (the jest-dom × vitest typing trap).
- Coverage: collision → link, show-once panel content and clearing, role picker hidden without
  `permission.grant`, the removed Invite button, and `jest-axe` on the new modal (auditing
  `document.body` for portalled content).

### 10.4 Gates

`/check-api` · `/check-web` · `/check-contracts` · `scripts/check-no-site-data.sh`, plus
**`/check-migrations`** (0085 lands). `openapi.yaml` and `docs/15-api-design.md` are updated in-PR.
Reviewers before the PR: `diff-critic`, `web-test-trap-reviewer` (apps/web changes),
**`migration-reviewer`** (0085).

---

## 11. Summary of impact

| | |
|---|---|
| **New endpoints** | `POST /users/provision`, `POST /users/{id}/temporary-password` |
| **Changed endpoints** | none — `POST /users` untouched |
| **Migration** | `0085`, additive enum value only, no-op downgrade |
| **Permission catalog** | unchanged, 102 keys |
| **Compose / deployment** | unchanged — the API container already has the admin credentials |
| **Removed** | the "Invite user" button (API retained) |
