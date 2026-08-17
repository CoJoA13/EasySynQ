# S-first-admin-provisioning — first administrator without Keycloak administration

**Date:** 2026-08-15

**Status:** Owner approved on 2026-08-15; ready for an executable implementation plan after written-spec review.

**Programme:** Identity onboarding

**Slice:** First-administrator bootstrap and no-SMTP user onboarding consistency

## 1. Outcome

A fresh EasySynQ installation creates its first System Administrator entirely through `/setup`.
The operator never opens the Keycloak console, runs a Keycloak user script, or reads and pastes a
Keycloak subject. EasySynQ generates a temporary password, shows it once in the setup wizard, and
Keycloak requires the administrator to replace it at first sign-in.

After setup, Administration → Users retains the shipped one-form provisioning flow: a caller with
`user.create` creates the Keycloak identity and EasySynQ user together, receives a show-once
temporary password, and hands it directly to the new user. SMTP is not a prerequisite.

## 2. Owner-approved decisions

The owner approved these decisions in sequence on 2026-08-15:

1. Keycloak remains the credential and authentication authority, but its console, scripts, and
   internal identifiers disappear from normal first-run and user-creation workflows.
2. The setup wizard creates the first administrator using the one-time, expiring bootstrap secret.
3. The setup wizard shows the generated temporary password once; Keycloak forces a password change
   at first sign-in.
4. SMTP and activation email are not required because reliable site email is not currently
   available.
5. `user.create` and `user.update` remain separate permissions. Assigning roles remains separately
   gated by `permission.grant`.
6. A durable bootstrap claim binds retries to one identity so partial failure cannot create a second
   administrator.
7. The old fixed-`qmsadmin` appliance credential and setup-sheet password are removed for new
   installations in favor of the same browser flow used by every supported install path.

The alternatives and architectural consequences are recorded in
[ADR 0005](../../adr/0005-provision-first-administrator-in-setup.md). R66 makes the security and
recovery rules binding.

## 3. Scope

### 3.1 In scope

- replace the authenticated, grant-only `POST /setup/bootstrap` flow with secret-authorized
  first-administrator provisioning in `/setup`;
- add a public pre-authentication bootstrap form for administrator identity details;
- create and link the Keycloak identity without exposing its subject;
- assign the seeded System Administrator role as part of the bootstrap operation;
- issue and acknowledge a show-once temporary password before authentication begins;
- persist a single pending bootstrap identity claim for idempotent recovery;
- share the Keycloak create/link/credential ordering between bootstrap and ordinary user creation;
- retain separate post-setup `user.create`, `user.update`, `permission.grant`, and system-tier
  credential-reset boundaries;
- migrate all repository consumers, contracts, installation paths, tests, and current manuals;
- remove fixed `qmsadmin` creation and password output from the appliance path; and
- prove the complete flow against a real Docker-backed Keycloak instance.

### 3.2 Out of scope

- SMTP setup or activation email;
- custom invitation or password-reset tokens owned by EasySynQ;
- MFA redesign;
- external identity federation or directory synchronization;
- general Keycloak administration in the EasySynQ UI;
- changing the permission catalog or default non-administrator roles; and
- allowing `user.update` to create identities or `user.create` to assign roles without
  `permission.grant`.

## 4. Existing authority and compatibility

R64 remains binding for all Keycloak provisioning: create the Keycloak account without a credential,
commit the EasySynQ user and role state, set the temporary password afterward, never delete a
Keycloak account as compensation, store no password, and audit only events that actually occurred.

R65 permits replacing the provisional first-run interface because EasySynQ has not completed a
supported production deployment. The replacement must migrate every known consumer atomically and
must preserve identity data, deny-by-default authorization, audit integrity, tenancy, and schema
upgrade safety.

Existing `OPERATIONAL` installations never re-enter bootstrap. Existing `IN_SETUP` installations
whose bootstrap secret was consumed continue with the authenticated setup gates. Nullable claim
state added by this slice must therefore read as “no pending claim” on upgrade.

## 5. Operator flow

### 5.1 Fresh installation

1. The installer starts Keycloak and generates its internal service-administrator credential in
   `.env`. This credential remains an infrastructure secret and is never an operator login.
2. The installer mints the existing one-time, expiring EasySynQ bootstrap secret.
3. `GET /setup/state` returns `UNINITIALIZED`; the SPA renders a public **Create the first
   administrator** step instead of starting OIDC authentication.
4. The operator enters the setup secret, username, display name, and optional email, first name, and
   last name.
5. The API creates the Keycloak identity without a credential, creates the EasySynQ `app_user` in
   `INVITED` state, assigns the fixed System Administrator role, then sets a generated temporary
   password with Keycloak's temporary-credential flag.
6. The form is replaced by the existing **Temporary password — shown once** presentation with Copy
   and **I’ve saved it — Continue to sign in** controls.
7. Continue acknowledges receipt, consumes the bootstrap secret, changes setup state to `IN_SETUP`,
   clears the credential and secret from component state, and starts OIDC sign-in. An acknowledgment
   failure leaves the show-once panel in place with Retry and does not redirect.
8. Keycloak requires the administrator to replace the temporary password. The authenticated
   administrator returns to `/setup`; existing login reconciliation advances `INVITED → ACTIVE`,
   and the administrator completes organization, storage, backup, authentication, and finalization
   gates.

The temporary password never appears in a server setup sheet, terminal output, user roster, URL, log,
audit payload, browser storage, or query cache.

### 5.2 Later users

Administration → Users remains the normal account surface. `user.create` permits account creation;
`user.update` permits editing existing users. Supplying roles at creation additionally requires
`permission.grant` and the existing role-assignment segregation-of-duties guard. A creator without
`permission.grant` creates an account with no application roles.

Resetting an existing user’s credential remains an account-takeover capability and retains R64’s
system-tier guard. The subject-based endpoint may remain as a non-primary orphan-adoption mechanism,
but no normal UI or installation instruction asks an operator to handle a Keycloak subject.

## 6. Components and interfaces

### 6.1 Setup UI

The pre-authentication setup surface owns only bootstrap identity creation and recovery. It reuses
`ShowOncePassword` rather than creating a second credential presentation. It holds the setup secret
and temporary password in component memory only, blocks dismissal while a request is in flight, and
clears both values before redirecting to Keycloak.

On reload while a claim is pending, the same form accepts the setup secret and bound username. The
API returns a specific bound-identity response when the secret is valid but the submitted username
differs; no Keycloak subject is disclosed.

### 6.2 Bootstrap API

`POST /api/v1/setup/administrator` replaces the old authenticated grant-only bootstrap operation.
It is outside the PEP because no authorized application user exists yet. Its complete authority is a
valid, unexpired bootstrap secret while setup is `UNINITIALIZED`. It is rate-limited, row-locked, and
denied once bootstrap is acknowledged or setup has advanced.

The request contains the bootstrap secret and administrator profile fields. A successful response
contains a public user projection, `temporary_password`, and `password_delivery: "shown_once"`.
Neither request nor response includes a Keycloak subject.

`POST /api/v1/setup/administrator/acknowledge` accepts the same bootstrap proof, verifies that a
credential was issued for the bound identity, sets `bootstrap_consumed_at`, transitions
`UNINITIALIZED → IN_SETUP`, and is idempotent for that same consumed secret. It never returns the
password.

The old `POST /api/v1/setup/bootstrap` contract and all known consumers are removed atomically under
R65. No compatibility shim is retained.

### 6.3 Shared provisioning service

The existing provisioning sequence moves out of the users route into a service with explicit units
for exact username lookup, credential-less Keycloak creation, EasySynQ user persistence, role
assignment, and temporary credential issuance. The ordinary `POST /users/provision` route and the
bootstrap service call the same primitives while retaining their distinct authorization and audit
contexts.

The Keycloak client accepts a bootstrap claim marker only for first-administrator creation. That
marker lets a retry distinguish an account created by this bootstrap attempt from an unrelated
username collision without exposing the marker or subject to the browser. The opaque, non-secret
marker remains as Keycloak account metadata after acknowledgment so later incident reconstruction
can explain the account's origin; it has no authentication or authorization meaning.

## 7. Bootstrap claim and failure model

The installation has four logical bootstrap phases:

1. **Unclaimed** — the secret is valid and no administrator identity is bound.
2. **Claimed** — one claim identifier and username are durable; no different administrator may be
   selected after the corresponding Keycloak account exists.
3. **Credential issued** — the EasySynQ user and System Administrator assignment are committed and a
   temporary Keycloak password was set, but the operator has not acknowledged receipt.
4. **Consumed** — receipt was acknowledged, setup is `IN_SETUP`, and the bootstrap authority cannot
   issue another credential.

The claim records a random operation identifier, bound username, timestamps, and the linked
`app_user` when available. It stores no plaintext secret or password. First-administrator Keycloak
creation tags the account with the operation identifier so a retry can adopt only the account created
by that claim.

Failure behavior is deterministic:

| Failure | Durable result | Recovery |
|---|---|---|
| Invalid, expired, or absent secret | No change | Generic denial; failed attempts are rate-limited. |
| Keycloak unavailable before creation | Claim may remain, no live credential | Retry the same bound identity after Keycloak recovers. |
| Unrelated username/email collision | No role grant and no credential change | Fail closed; choose another identity only when Keycloak proves the claim created no account. |
| Keycloak account created, database commit failed | Credential-less account marked with the claim | Retry adopts only the matching claimed account; never delete it. |
| User/role committed, password issuance failed | First admin exists but has no usable credential | Retry issues a password to the same bound identity. |
| Response lost after password issuance | Bound account has an unknown temporary password | Re-enter the secret to issue a new password; the previous credential is invalidated. |
| Acknowledgment lost after commit | Secret is already consumed for the same identity | Idempotent acknowledgment succeeds without issuing or revealing a credential. |

Reminting an expired bootstrap secret may resume a pending claim but may not select a second identity.
There is no compensating Keycloak delete on any path.

## 8. Authorization, audit, and secrets

The bootstrap endpoints are secret-authorized only while no application administrator exists. They
must not be reachable through a valid JWT as an alternate post-setup user-management path. All
post-bootstrap account operations continue through the PEP.

Bootstrap-originated audit rows use `actor_type=system` and `actor_id=NULL` because the chosen
administrator has not authenticated yet. The audit trail uses:

- a new `BOOTSTRAP_IDENTITY_CLAIMED` event when a durable claim is established;
- `USER_CREATED` only after the EasySynQ user commits;
- `ADMIN_BOOTSTRAPPED` only after the System Administrator assignment commits;
- `USER_CREDENTIAL_ISSUED` only after Keycloak accepts the temporary password; and
- `BOOTSTRAP_CONSUMED` only when the operator acknowledges receipt.

No event contains the bootstrap secret, temporary password, Keycloak admin credential, claim marker,
or Keycloak subject. Audit writes that precede credential issuance remain atomic with their database
state. Credential-issuance audit failure retains R64’s deliberate non-fatal under-claim posture so a
live one-time password is not lost behind a server error.

## 9. Migration and install-path changes

Implementation must run `uv run alembic heads` before naming or writing the migration. The migration
adds nullable bootstrap-claim fields to `system_config`, the bounded foreign key/index needed for the
linked administrator, and the additive `BOOTSTRAP_IDENTITY_CLAIMED` audit enum value. Existing rows
require no data rewrite. Downgrade removes the new claim columns and index; the PostgreSQL enum value
follows the repository’s established no-op downgrade convention.

All fresh-install paths converge on the browser bootstrap:

- `scripts/install.sh` continues generating Keycloak service credentials but creates no human realm
  user;
- the appliance provisioner stops creating fixed `qmsadmin` and stops writing a human password to
  `EASYSYNQ-SETUP.txt`;
- setup sheets contain only the application URL and one-time EasySynQ bootstrap secret; and
- runbooks stop instructing operators to create or federate the first Keycloak identity manually.

Existing Keycloak realm data and linked users are never deleted or rewritten by the migration.

## 10. Verification contract

Implementation begins with focused failing tests and proves:

### 10.1 API, service, and migration

- one valid secret creates exactly one linked System Administrator;
- concurrent requests serialize and cannot produce two claims, users, or administrator grants;
- bootstrap is denied outside `UNINITIALIZED`, after acknowledgment, and for invalid or expired
  secrets;
- exact Keycloak lookup distinguishes absent, matching-claim, and unrelated-collision states;
- every failure stage resumes the same claim without deleting an identity;
- response-loss retry resets only the bound credential and never returns an old password;
- acknowledgment is idempotent and cannot be used to issue another credential;
- audit event ordering and actor semantics match §8;
- no credential or secret appears in database fields, logs, problem details, or audit payloads;
- ordinary user provisioning and all existing permission/two-tier tests remain green; and
- a populated upgrade/downgrade/upgrade round trip preserves existing setup and identity data.

### 10.2 Web

- `UNINITIALIZED` renders first-administrator creation without starting OIDC;
- submit, pending, show-once, copy, acknowledgment, redirect, collision, outage, retry, reload, and
  expired-secret states have focused tests;
- secrets are absent from URLs, persistent storage, and TanStack Query cache;
- closing or navigation cannot discard an in-flight one-time credential response;
- after acknowledgment, the setup screen cannot issue another password and authentication begins;
- setup and user-create forms remain keyboard-operable, axe-clean, forced-colors-contained, and free
  of document-level overflow at 320 CSS px; and
- ordinary Administration → Users creation retains its shipped behavior.

### 10.3 Real acceptance

A Docker-backed acceptance run must use the shipped Keycloak realm and prove: fresh bootstrap,
temporary-password sign-in, Keycloak’s forced password change, return to authenticated `/setup`, and
rejection of the original temporary password afterward. Required Chromium evidence runs with one
worker and zero retries. Mock-only Keycloak tests do not satisfy this acceptance boundary.

After focused checks, run the affected API/web suites, static analysis, contract regeneration/check,
repository authority, site-data, and changed-file checks. Update mutable counts or status evidence
only from fresh completed runs; do not claim SMTP, external federation, Firefox/WebKit, deployment, or
actual assistive-technology coverage.

## 11. Acceptance criteria

1. A fresh supported install requires no Keycloak console, subject copy/paste, or user-creation shell
   command.
2. The first administrator is created in `/setup`, receives a show-once temporary password, and must
   replace it at first sign-in.
3. The one-time bootstrap authority binds to one identity and cannot create a second administrator.
4. Every partial failure has an in-app recovery path that preserves ordering, non-deletion, and audit
   truthfulness.
5. Passwords and bootstrap secrets are never persisted or disclosed outside their one intended
   presentation.
6. Existing operational and in-progress installations upgrade without losing identity or setup data.
7. Post-setup user creation and editing retain separate permission keys; role assignment and
   credential reset retain their stronger existing guards.
8. All fresh install paths and current documentation describe the same browser-first workflow.
9. Docker-backed Keycloak acceptance and fresh affected verification pass before handoff.
