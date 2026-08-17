# First Administrator Provisioning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a fresh EasySynQ installation create its first System Administrator entirely inside `/setup`, show a generated temporary password once, force a Keycloak password change at first sign-in, and preserve the existing no-SMTP in-app flow for later users.

**Architecture:** Replace the provisional authenticated bootstrap grant with two public, secret-authorized setup operations backed by one durable claim on the `system_config` singleton. Keycloak creation remains credential-less until the EasySynQ user and System Administrator assignment commit; a claim marker on the Keycloak account makes retries adopt only the identity created by that claim. Shared identity primitives own exact lookup, credential-less creation, and temporary-password issuance, while setup and post-setup routes retain distinct authorization and audit contexts. The SPA renders the first-admin form only in `UNINITIALIZED`, keeps both secrets in component memory, acknowledges the show-once password before OIDC, and resumes the existing authenticated wizard in `IN_SETUP`.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, SQLAlchemy 2 async, PostgreSQL 16, Alembic, Redis, httpx, Keycloak Admin REST/OIDC, pytest, OpenAPI, React 19, TypeScript 6, React Router 7, TanStack Query 5, Mantine 7, Vitest 4, MSW 2, Docker Compose, Playwright Test Chromium.

## Global Constraints

- Work only in the linked worktree `.worktrees/first-admin-bootstrap` on branch `codex/first-admin-bootstrap`. Before implementation, confirm `main == origin/main`, confirm the feature branch still descends from the approved baseline, and preserve the primary checkout's unrelated untracked `.superdesign/` directory.
- Do not prune, repair, remove, or otherwise modify either unrelated prunable `/tmp` worktree record.
- Treat `docs/superpowers/specs/2026-08-15-s-first-admin-provisioning-design.md`, R64, R65, R66, ADR 0005, and the accepted design commit `1b778cc` as binding.
- Read every `docs/debt/` record before editing its hotspot. Re-read `20260815194752-bootstrap-claim-state-machine.md` before setup-service work, `20260813234519-playwright-responsive-browser-harness.md` before browser-harness work, and `20260815200349-first-admin-live-ci.md` before deciding any CI or release-gate change.
- Keycloak remains the credential authority. Never put a Keycloak subject, bootstrap claim marker, Keycloak admin credential, setup secret, or temporary password into a browser URL, persistent browser storage, query cache, database field not explicitly approved here, application log, audit payload, problem detail, setup sheet, or user roster.
- Preserve R64 ordering: create the Keycloak identity without a credential, commit the EasySynQ user and role state, then set a temporary credential. Never delete a Keycloak identity as compensation.
- The public bootstrap authority is only a valid, unexpired secret while the singleton setup state is `UNINITIALIZED`. A bearer token is not authority for either new endpoint, and no valid JWT may turn them into a post-setup user-management path.
- One durable claim binds one exact username. A retry, lost response, concurrent request, or reminted secret can only recover that bound identity. A definitively unrelated collision may release only a still-unowned rejected claim; every ambiguous result and every claim with a possible marked account, linked user, or issued credential remains bound and fails closed.
- Keep post-setup permission boundaries unchanged: `user.create` creates an identity; `user.update` edits; `permission.grant` separately gates requested roles; R64's system-tier guard remains mandatory for resetting any existing user's credential. Add no permission key and do not change the catalog count.
- Replace `POST /api/v1/setup/bootstrap` and every repository consumer atomically under R65. Do not add a compatibility endpoint, alias, feature flag, or duplicate UI path.
- Existing `OPERATIONAL` and legitimate `IN_SETUP` installations must not re-enter bootstrap. Nullable migration fields read as no pending claim; no existing Keycloak or EasySynQ identity is rewritten or deleted.
- The live Keycloak acceptance is a required pre-handoff/release gate for this slice, but `20260815200349-first-admin-live-ci.md` owns the deliberate decision not to add an unapproved full-stack PR job. Do not edit `.github/workflows/` unless the owner separately approves that CI expansion.
- The live harness must refuse a pre-existing `.env`, use a validated unique Compose project, validate every cleanup target, and tear down only its own project and volumes. It must never use the default Compose project or touch a developer's existing stack.
- Browser evidence is Chromium-only, one worker, zero retries. It does not claim Firefox, WebKit, SMTP, external federation, actual assistive-technology sessions, deployment, or disposable Fedora proof.
- Start every behavior change with the smallest focused failing proof. Run the adjacent affected gate after GREEN, inspect the bounded diff, and make one scoped commit per task.
- At every task boundary, perform an independent requirements review followed by an independent code-quality/security review before committing. Resolve findings through focused RED/GREEN evidence rather than accepting them mechanically.
- Invoke `debt-ops:add` immediately for any new deferred choice. Draft an ADR only if implementation creates another architecturally significant choice with at least two credible alternatives; ADR 0005 already owns the first-admin architecture.
- Run genuinely long full suites as durable background jobs. Record the exact argv, result, elapsed time, and diagnostics; a skipped, unavailable, interrupted, or partial run is not a pass.
- Do not change `docs/current-status.md` counts, CI topology, migration snapshot, or `baseline_commit` without fresh corresponding evidence. In particular, retain the implementation compatibility baseline instead of rewriting it to the current branch SHA.
- Run repository authority and site-data gates before handoff. Do not push, open a PR, merge, deploy, or prune worktrees without explicit owner approval.

## File Ownership Map

- `packages/contracts/openapi.yaml`, `packages/contracts/dist/openapi.json`, `packages/contracts/.contract.lock`, `apps/api/src/easysynq_api/_generated/models.py`, `apps/web/src/api/_generated/schema.d.ts` — public first-admin request, response, problem, and generated contract authority.
- `migrations/versions/0087_first_admin_bootstrap.py`, `apps/api/src/easysynq_api/db/models/system_config.py`, `apps/api/src/easysynq_api/db/models/_audit_enums.py` — durable claim fields, linked-user foreign key/index, and additive audit event.
- `apps/api/src/easysynq_api/services/keycloak_provisioning.py` — exact Keycloak lookup plus optional opaque bootstrap marker transport.
- `apps/api/src/easysynq_api/services/identity/provisioning.py` — shared Keycloak client factory, credential-less identity creation/recovery, and temporary-password issuance; no database transaction or authorization policy.
- `apps/api/src/easysynq_api/services/setup/administrator.py` — singleton secret authority, durable claim state machine, staged commits, System Administrator assignment, and pre-authentication audit semantics.
- `apps/api/src/easysynq_api/api/setup.py`, `apps/api/src/easysynq_api/services/setup/__init__.py`, `apps/api/src/easysynq_api/services/setup/service.py`, `apps/api/src/easysynq_api/problems.py`, `apps/api/src/easysynq_api/cli/setup.py` — HTTP replacement, old bootstrap removal, problem vocabulary, and safe remint behavior.
- `apps/api/src/easysynq_api/api/users.py` — ordinary user provisioning/reset adapted to the shared identity primitives without changing PEP, permission, two-tier, tenancy, response, or audit behavior.
- `apps/api/tests/unit/test_first_admin_contract.py`, `test_setup.py`, `test_keycloak_provisioning.py`, `test_identity_provisioning.py`, `test_deploy_configuration.py` — structural, pure, client, shared-service, and deployment contracts.
- `apps/api/tests/integration/test_setup.py`, `test_users_provision.py`, `test_contract_response_schemas.py`, `apps/api/tests/migration/test_migration_coherence.py` — state-machine, concurrency, permission preservation, response-schema, and populated migration proofs.
- `apps/web/src/setup/FirstAdministratorStep.tsx`, its focused tests, `apps/web/src/SetupWizard.tsx`, `apps/web/src/App.tsx`, `apps/web/src/admin/ShowOncePassword.tsx`, `apps/web/src/lib/types.ts`, and adjacent tests — pre-auth form, volatile show-once state, acknowledgment, OIDC transition, and existing user-create preservation.
- `apps/web/e2e/support/api.ts`, `apps/web/e2e/smoke.spec.ts`, and existing browser fixtures — mocked routed representation and fail-closed browser coverage only.
- `apps/web/e2e-live/first-admin.spec.ts`, `apps/web/playwright.live.config.ts`, `apps/web/vite.config.ts`, `apps/web/tsconfig.browser.json`, `apps/web/package.json`, `scripts/test-first-admin-keycloak.sh` — isolated real Compose/Keycloak acceptance.
- `infra/appliance/provision/easysynq-provision.sh`, `infra/appliance/provision/bin/easysynq-create-user`, current install runbooks/manuals, `docs/08-setup-and-onboarding.md`, `docs/15-api-design.md`, and `docs/dev-workflow.md` — one browser-first installation story and removal of the obsolete appliance human-account helper.
- `docs/adr/0003-use-playwright-for-responsive-browser-evidence.md`, `docs/debt/`, `docs/current-status.md`, and `docs/slice-history.md` — triggered browser-boundary reassessment, current debt, and fresh implementation evidence.

---

### Task 1: Publish the first-administrator setup contract

**Files:**

- Create: `apps/api/tests/unit/test_first_admin_contract.py`
- Modify: `packages/contracts/openapi.yaml`
- Modify: `apps/api/src/easysynq_api/problems.py`
- Regenerate: `packages/contracts/dist/openapi.json`
- Regenerate: `packages/contracts/.contract.lock`
- Regenerate: `apps/api/src/easysynq_api/_generated/models.py`
- Regenerate: `apps/web/src/api/_generated/schema.d.ts`

**Interfaces:**

- Removes: `POST /setup/bootstrap`.
- Produces: public `POST /setup/administrator` with `201` for first issuance and `200` for recovery/reissue; public `POST /setup/administrator/acknowledge` with `200`.
- Produces schemas `FirstAdministratorRequest`, `FirstAdministratorSummary`, `FirstAdministratorProvisioned`, `BootstrapAcknowledgeRequest`, and `BootstrapAcknowledgeResponse`.
- Adds stable problem codes `bootstrap_identity_bound` and `bootstrap_not_ready`; `Problem.bound_username` is optional and appears only after a valid secret proves access to the pending claim.

- [ ] **Step 1: Write the failing structural contract proof**

Create `apps/api/tests/unit/test_first_admin_contract.py` with assertions equivalent to:

```python
from pathlib import Path

import yaml

OPENAPI = Path(__file__).resolve().parents[4] / "packages/contracts/openapi.yaml"


def test_first_administrator_replaces_the_authenticated_bootstrap_contract() -> None:
    spec = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    paths = spec["paths"]
    assert "/setup/bootstrap" not in paths
    provision = paths["/setup/administrator"]["post"]
    acknowledge = paths["/setup/administrator/acknowledge"]["post"]
    assert provision["security"] == []
    assert acknowledge["security"] == []
    assert provision["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/FirstAdministratorRequest"
    }
    assert set(provision["responses"]) == {"200", "201", "403", "409", "422", "429", "502", "503"}
    assert acknowledge["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/BootstrapAcknowledgeResponse"
    }


def test_first_administrator_response_never_publishes_keycloak_identity() -> None:
    spec = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    schemas = spec["components"]["schemas"]
    summary = schemas["FirstAdministratorSummary"]
    assert summary["required"] == ["id", "username", "display_name", "email", "status"]
    assert summary["properties"]["display_name"] == {"type": "string"}
    assert "keycloak_subject" not in summary["properties"]
    response = schemas["FirstAdministratorProvisioned"]
    assert response["required"] == ["administrator", "temporary_password", "password_delivery"]
    assert response["properties"]["password_delivery"]["enum"] == ["shown_once"]
    assert schemas["Problem"]["properties"]["bound_username"] == {"type": ["string", "null"]}
```

- [ ] **Step 2: Run RED**

Run:

```bash
cd apps/api && uv run pytest tests/unit/test_first_admin_contract.py -q
```

Expected: FAIL because only `/setup/bootstrap` exists and none of the new schemas or problem fields exist.

- [ ] **Step 3: Define the exact OpenAPI surface**

In `packages/contracts/openapi.yaml`, use these schema shapes:

```yaml
FirstAdministratorRequest:
  type: object
  additionalProperties: false
  required: [secret, username, display_name]
  properties:
    secret: { type: string, minLength: 1, maxLength: 512 }
    username: { type: string, minLength: 1, maxLength: 255 }
    display_name: { type: string, minLength: 1, maxLength: 255 }
    email: { type: [string, "null"], maxLength: 320 }
    first_name: { type: [string, "null"], maxLength: 255 }
    last_name: { type: [string, "null"], maxLength: 255 }
FirstAdministratorSummary:
  type: object
  additionalProperties: false
  required: [id, username, display_name, email, status]
  properties:
    id: { type: string, format: uuid }
    username: { type: string }
    display_name: { type: string }
    email: { type: [string, "null"] }
    status: { type: string, enum: [INVITED] }
FirstAdministratorProvisioned:
  type: object
  additionalProperties: false
  required: [administrator, temporary_password, password_delivery]
  properties:
    administrator: { $ref: "#/components/schemas/FirstAdministratorSummary" }
    temporary_password: { type: string }
    password_delivery: { type: string, enum: [shown_once] }
BootstrapAcknowledgeRequest:
  type: object
  additionalProperties: false
  required: [secret]
  properties:
    secret: { type: string, minLength: 1, maxLength: 512 }
BootstrapAcknowledgeResponse:
  type: object
  additionalProperties: false
  required: [setup_state, admin_user_id]
  properties:
    setup_state: { type: string, enum: [IN_SETUP] }
    admin_user_id: { type: string, format: uuid }
```

Both paths declare `security: []`. Neither request nor response accepts roles or returns a Keycloak subject. Document `403` invalid/expired, `409` no secret/advanced/bound/not-ready/collision, `422` validation or Keycloak rejection, `429` rate limit, `502` Keycloak unavailable, and `503` missing Keycloak admin configuration.

- [ ] **Step 4: Extend the canonical problem vocabulary and regenerate**

Add both codes to `ProblemCode` and the OpenAPI enum. Add `bound_username` as a nullable optional Problem property. Then run:

```bash
just contracts
cd apps/api && uv run pytest tests/unit/test_first_admin_contract.py -q
just contracts-check
```

Expected: contract proof PASS; generated Python/TypeScript artifacts and lock match the OpenAPI source.

- [ ] **Step 5: Review and commit the contract checkpoint**

Run:

```bash
git diff --check -- packages/contracts/openapi.yaml packages/contracts/dist/openapi.json packages/contracts/.contract.lock apps/api/src/easysynq_api/_generated/models.py apps/web/src/api/_generated/schema.d.ts apps/api/src/easysynq_api/problems.py apps/api/tests/unit/test_first_admin_contract.py
git diff --stat
git add packages/contracts/openapi.yaml packages/contracts/dist/openapi.json packages/contracts/.contract.lock apps/api/src/easysynq_api/_generated/models.py apps/web/src/api/_generated/schema.d.ts apps/api/src/easysynq_api/problems.py apps/api/tests/unit/test_first_admin_contract.py
git commit -m "feat: publish first administrator setup contract"
```

---

### Task 2: Persist the durable bootstrap claim and audit event

**Files:**

- Create: `migrations/versions/0087_first_admin_bootstrap.py`
- Modify: `apps/api/src/easysynq_api/db/models/system_config.py`
- Modify: `apps/api/src/easysynq_api/db/models/_audit_enums.py`
- Modify: `apps/api/tests/unit/test_setup.py`
- Modify: `apps/api/tests/migration/test_migration_coherence.py`

**Interfaces:**

- Adds nullable `SystemConfig.bootstrap_admin_claim_id`, `bootstrap_admin_username`, `bootstrap_admin_user_id`, `bootstrap_claimed_at`, and `bootstrap_credential_issued_at`.
- `bootstrap_admin_user_id` references `app_user.id` with `ON DELETE RESTRICT` and owns index `ix_system_config_bootstrap_admin_user_id`.
- Adds `EventType.BOOTSTRAP_IDENTITY_CLAIMED`; PostgreSQL enum downgrade remains a documented no-op while claim columns/index downgrade normally.

- [ ] **Step 1: Discover the executable migration head before naming the revision**

Run:

```bash
cd apps/api && uv run alembic heads
```

Expected: exactly `0086_record_page_index (head)`. Stop and reconcile authority if the result differs; do not create a branch or merge revision from prose.

- [ ] **Step 2: Write RED model/enum and populated migration assertions**

Extend `test_setup_event_types_resolve` to require `BOOTSTRAP_IDENTITY_CLAIMED`. Extend the populated coherence test so head must expose all five nullable columns, the named index, and the RESTRICT foreign key. Insert an existing `app_user`, bind it as `bootstrap_admin_user_id`, and populate claim timestamps before the downgrade.

After `command.downgrade(config, "0086_record_page_index")`, assert the five columns and index are absent while the existing `system_config`, `organization`, and `app_user` rows remain. After `command.upgrade(config, "head")`, assert the schema is restored, existing identity/setup rows are unchanged, and the re-added nullable claim fields read `NULL`.

Run:

```bash
cd apps/api && uv run pytest tests/unit/test_setup.py -q
cd apps/api && uv run pytest tests/migration/test_migration_coherence.py -q
```

Expected: unit FAIL on the missing enum member; migration FAIL on missing columns/index/revision.

- [ ] **Step 3: Add model fields and migration `0087_first_admin_bootstrap`**

Import `Index` and use matching ORM metadata plus fields:

```python
__table_args__ = (Index("ix_system_config_bootstrap_admin_user_id", "bootstrap_admin_user_id"),)

bootstrap_admin_claim_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
bootstrap_admin_username: Mapped[str | None] = mapped_column(Text, nullable=True)
bootstrap_admin_user_id: Mapped[uuid.UUID | None] = mapped_column(
    UUID(as_uuid=True),
    ForeignKey("app_user.id", ondelete="RESTRICT", name="fk_system_config_bootstrap_admin_user_id_app_user"),
    nullable=True,
)
bootstrap_claimed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
bootstrap_credential_issued_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

The migration must declare `down_revision = "0086_record_page_index"`, add the enum value with `IF NOT EXISTS`, add the five columns, add the named FK and index, and remove only the index/FK/columns on downgrade. Do not attempt to remove the PostgreSQL enum value.

- [ ] **Step 4: Run GREEN and static/model coherence**

Run:

```bash
cd apps/api && uv run pytest tests/unit/test_setup.py -q
cd apps/api && uv run pytest tests/migration/test_migration_coherence.py -q
cd apps/api && uv run alembic heads
cd apps/api && uv run ruff check src/easysynq_api/db/models/system_config.py src/easysynq_api/db/models/_audit_enums.py tests/unit/test_setup.py tests/migration/test_migration_coherence.py ../../migrations/versions/0087_first_admin_bootstrap.py
```

Expected: all tests PASS; Alembic reports only `0087_first_admin_bootstrap (head)`.

- [ ] **Step 5: Review and commit the persistence checkpoint**

Run:

```bash
git diff --check -- migrations/versions/0087_first_admin_bootstrap.py apps/api/src/easysynq_api/db/models/system_config.py apps/api/src/easysynq_api/db/models/_audit_enums.py apps/api/tests/unit/test_setup.py apps/api/tests/migration/test_migration_coherence.py
git add migrations/versions/0087_first_admin_bootstrap.py apps/api/src/easysynq_api/db/models/system_config.py apps/api/src/easysynq_api/db/models/_audit_enums.py apps/api/tests/unit/test_setup.py apps/api/tests/migration/test_migration_coherence.py
git commit -m "feat: persist first administrator bootstrap claim"
```

---

### Task 3: Extract fail-closed shared Keycloak identity primitives

**Files:**

- Create: `apps/api/src/easysynq_api/services/identity/__init__.py`
- Create: `apps/api/src/easysynq_api/services/identity/provisioning.py`
- Create: `apps/api/tests/unit/test_identity_provisioning.py`
- Modify: `apps/api/src/easysynq_api/services/keycloak_provisioning.py`
- Modify: `apps/api/tests/unit/test_keycloak_provisioning.py`

**Interfaces:**

- Extends `UserLookup` with `bootstrap_claim_id: str | None`.
- Extends `KeycloakProvisioningClient.create_user` with keyword-only `bootstrap_claim_id: uuid.UUID | None = None`; only a bootstrap call sends the `easysynqBootstrapClaim` Keycloak attribute.
- Produces shared `IdentityProfile`, `CredentiallessIdentity`, `IdentityUsernameExists`, `ensure_credentialless_identity`, `issue_temporary_credential`, and `keycloak_client`.
- Shared code owns no SQLAlchemy session, role assignment, PEP decision, or audit event.

- [ ] **Step 1: Write Keycloak client RED tests**

Add tests proving exact lookup extracts only a single string from `attributes.easysynqBootstrapClaim`, an absent attribute returns no claim, a present malformed/multiple value fails closed as `KeycloakUnavailable`, ordinary create sends no `attributes` or `credentials`, bootstrap create sends the exact opaque marker and no credential, and reset retains `temporary: true`.

Run:

```bash
cd apps/api && uv run pytest tests/unit/test_keycloak_provisioning.py -q
```

Expected: FAIL because lookup has no marker and create does not accept the optional claim ID.

- [ ] **Step 2: Implement the bounded Keycloak transport change**

Use:

```python
BOOTSTRAP_CLAIM_ATTRIBUTE = "easysynqBootstrapClaim"

@dataclass(frozen=True, slots=True)
class UserLookup:
    found: bool
    subject: str | None = None
    bootstrap_claim_id: str | None = None
```

When a found user has no bootstrap attribute, return `None`. When it has `attributes` shaped as `{BOOTSTRAP_CLAIM_ATTRIBUTE: [one_nonempty_string]}`, return that string. If the named attribute exists in any other shape, raise `KeycloakUnavailable` rather than treating corrupted or unreadable marker state as absence. Add the attribute only when `bootstrap_claim_id` is supplied. Keep exact username re-verification, conflict re-read, bounded error messages, timeout, and fail-closed outage behavior unchanged.

- [ ] **Step 3: Write shared-service RED tests**

Create `test_identity_provisioning.py` for:

- definitely absent username creates one credential-less identity;
- ordinary existing username raises `IdentityUsernameExists` with the known subject;
- bootstrap existing username is recoverable only when its marker exactly equals the expected claim;
- missing or different well-formed marker raises `IdentityUsernameExists` and never resets a password;
- malformed marker state propagates `KeycloakUnavailable` and never creates or resets an account;
- create-409 race re-reads and adopts only the exact matching bootstrap marker;
- unresolved bootstrap conflict fails closed as unavailable rather than guessing;
- credential issuance generates a realm-conforming password, calls reset once, and returns only the generated value; and
- the client factory derives realm/admin settings from the existing configuration.

Run:

```bash
cd apps/api && uv run pytest tests/unit/test_identity_provisioning.py -q
```

Expected: collection FAIL because `services.identity.provisioning` does not exist.

- [ ] **Step 4: Implement the exact shared surface**

```python
@dataclasses.dataclass(frozen=True, slots=True)
class IdentityProfile:
    username: str
    email: str | None
    first_name: str | None
    last_name: str | None

@dataclasses.dataclass(frozen=True, slots=True)
class CredentiallessIdentity:
    subject: str
    created: bool

class IdentityUsernameExists(Exception):
    def __init__(self, subject: str) -> None:
        self.subject = subject
        super().__init__("username already exists")
```

Implement `ensure_credentialless_identity(client: KeycloakProvisioningClient, profile: IdentityProfile, *, bootstrap_claim_id: uuid.UUID | None = None, allow_matching_claim: bool = False) -> CredentiallessIdentity`, `issue_temporary_credential(client: KeycloakProvisioningClient, *, subject: str, username: str) -> str`, and `keycloak_client() -> KeycloakProvisioningClient` with the behavior below.

For bootstrap recovery, `allow_matching_claim=True` still requires both the exact username and exact marker. For ordinary provisioning, any found username raises `IdentityUsernameExists`. Propagate email collisions and rejected input distinctly. Never expose a marker or subject in an exception message.

- [ ] **Step 5: Run GREEN and static checks**

Run:

```bash
cd apps/api && uv run pytest tests/unit/test_keycloak_provisioning.py tests/unit/test_identity_provisioning.py -q
cd apps/api && uv run ruff check src/easysynq_api/services/keycloak_provisioning.py src/easysynq_api/services/identity tests/unit/test_keycloak_provisioning.py tests/unit/test_identity_provisioning.py
cd apps/api && uv run mypy src/easysynq_api/services/keycloak_provisioning.py src/easysynq_api/services/identity
```

Expected: all focused tests, Ruff, and mypy PASS.

- [ ] **Step 6: Review and commit the shared identity checkpoint**

Run:

```bash
git diff --check -- apps/api/src/easysynq_api/services/keycloak_provisioning.py apps/api/src/easysynq_api/services/identity apps/api/tests/unit/test_keycloak_provisioning.py apps/api/tests/unit/test_identity_provisioning.py
git add apps/api/src/easysynq_api/services/keycloak_provisioning.py apps/api/src/easysynq_api/services/identity apps/api/tests/unit/test_keycloak_provisioning.py apps/api/tests/unit/test_identity_provisioning.py
git commit -m "refactor: share fail-closed identity provisioning"
```

---

### Task 4: Implement the first-administrator claim state machine and public endpoints

**Files:**

- Create: `apps/api/src/easysynq_api/services/setup/administrator.py`
- Create: `apps/api/tests/unit/test_setup_administrator.py`
- Modify: `apps/api/src/easysynq_api/api/setup.py`
- Modify: `apps/api/src/easysynq_api/services/setup/__init__.py`
- Modify: `apps/api/src/easysynq_api/services/setup/service.py`
- Modify: `apps/api/src/easysynq_api/cli/setup.py`
- Modify: `apps/api/tests/integration/test_setup.py`

**Interfaces:**

- Produces `provision_first_administrator(session, *, secret, profile)` and `acknowledge_first_administrator(session, *, secret)`.
- Removes `bootstrap_admin(session, actor, secret)` and the authenticated route.
- Bootstrap audit events use `actor_type=system`, `actor_id=NULL`, the singleton organization, and contain no secret, password, marker, or Keycloak subject.
- Remint replaces only secret hash/expiry while `UNINITIALIZED`; it preserves a pending claim and refuses after setup advances.

- [ ] **Step 1: Read the claim-state debt and write pure/state RED tests**

Re-read `docs/debt/20260815194752-bootstrap-claim-state-machine.md`. Create unit tests for profile trimming/default display name, exact bound-username comparison, secret validation ordering, summary projection, and system-audit payload allowlists. The audit allowlist is:

```python
ALLOWED_BOOTSTRAP_AFTER_KEYS = {
    EventType.BOOTSTRAP_IDENTITY_CLAIMED: {"username"},
    EventType.USER_CREATED: {"status", "email", "provisioning"},
    EventType.ADMIN_BOOTSTRAPPED: {"role"},
    EventType.USER_CREDENTIAL_ISSUED: {"credential_issued"},
    EventType.BOOTSTRAP_CONSUMED: set(),
}
```

Run:

```bash
cd apps/api && uv run pytest tests/unit/test_setup_administrator.py -q
```

Expected: collection FAIL because `services.setup.administrator` does not exist.

- [ ] **Step 2: Convert setup integration coverage to the new public boundary and observe RED**

Replace the old `_bootstrap(client, headers, secret)` fixture with a mock-Keycloak-backed helper that:

1. posts public profile data to `/api/v1/setup/administrator` with no bearer;
2. captures the returned temporary password and mock subject;
3. posts the secret to `/api/v1/setup/administrator/acknowledge` with no bearer; and
4. returns token headers for the created subject so the remaining authenticated setup-gate tests continue unchanged.

Add focused integration proofs for initial issuance, response-loss reissue, same-secret acknowledgment replay, reminted-secret recovery, different bound username, unrelated missing/different marker collision releasing only the still-unowned rejected claim, malformed marker state retaining the claim and failing closed, Keycloak outage before/after create, database failure after marked Keycloak create, password failure after user/role commit, no-secret/expired/invalid/rate-limit behavior, non-`UNINITIALIZED` denial, and system-actor event order/payload secrecy. Add an `asyncio.gather` concurrency proof that ends with exactly one claim ID, one linked user, one System Administrator assignment, and one claimed event.

Run:

```bash
cd apps/api && uv run pytest tests/integration/test_setup.py -k 'bootstrap or first_administrator or latch' -q
```

Expected: FAIL because the new routes/service do not exist and the old route is still present.

- [ ] **Step 3: Implement the state-machine data types and secret-authorized singleton load**

```python
@dataclasses.dataclass(frozen=True, slots=True)
class FirstAdministratorProfile:
    username: str
    display_name: str
    email: str | None
    first_name: str | None
    last_name: str | None

@dataclasses.dataclass(frozen=True, slots=True)
class FirstAdministratorProvisioned:
    admin_user_id: uuid.UUID
    username: str
    display_name: str
    email: str | None
    temporary_password: str
    created: bool
```

Load the one `SystemConfig` row with `FOR UPDATE`, derive its `org_id`, and fail closed if the singleton is absent or ambiguous. Apply rate limiting before secret verification. Username and display name are both required after trimming; optional blank profile fields normalize to `None`. A valid request with no claim creates and commits a random claim ID, exact normalized username, timestamp, and `BOOTSTRAP_IDENTITY_CLAIMED`. A valid request with an existing claim accepts only that exact username; otherwise raise `409 bootstrap_identity_bound` with `bound_username` and no other claim data.

- [ ] **Step 4: Implement staged identity/user/role/credential commits**

Use this ordering and reacquire the `SystemConfig` row lock at every database stage:

1. Claim commit.
2. Call `ensure_credentialless_identity` with `bootstrap_claim_id=claim_id` and `allow_matching_claim=True`.
3. Under the lock, reuse `bootstrap_admin_user_id` when present; otherwise reuse the exact marker-linked subject's existing `app_user` or create one `INVITED`, assign the seeded System Administrator role only when absent, set `bootstrap_admin_user_id`, and commit `USER_CREATED` then `ADMIN_BOOTSTRAPPED` truthfully.
4. Build every response projection before setting a credential.
5. `issue_temporary_credential` to the bound subject.
6. Under the lock, stamp `bootstrap_credential_issued_at`, append `USER_CREDENTIAL_ISSUED`, and commit. If this final audit/state commit fails after Keycloak accepts the password, roll back, log only the user UUID, return the password, and let the next retry reset it; this is R64's deliberate under-claim direction.

Never delete an account. A retry after any partial stage converges on the same marker, subject, user, and role. If a reliable exact lookup proves that the username belongs to an account with no marker or a different well-formed marker, or that create failed on email while the exact username remains absent, reacquire the row lock and clear the still-unowned claim only when `bootstrap_admin_user_id` and `bootstrap_credential_issued_at` are both null. This rejected attempt may then choose another username and returns `409 user_exists` or `409 keycloak_email_exists` without a subject. An outage, malformed marker, uncertain create result, or any claim with a linked user/credential must retain the claim and fail closed. A recovered response returns `created=False`, causing HTTP `200`; the first completed issuance returns `created=True`, causing `201`.

- [ ] **Step 5: Implement acknowledgment and safe remint semantics**

Acknowledgment verifies the current secret in constant time. Its first commit requires `UNINITIALIZED`, a claim ID, bound username, linked admin user, System Administrator assignment, and non-null credential-issued timestamp; it stamps `bootstrap_consumed_at`, advances to `IN_SETUP`, appends `BOOTSTRAP_CONSUMED`, and commits. If the same valid secret is presented after that commit and the stored consumed claim/user match, return the same `IN_SETUP/admin_user_id` response without another event. Every other advanced-state request is denied. Acknowledgment never returns or issues a credential.

Change `mint_bootstrap` so:

```python
if cfg.setup_state is not SetupState.UNINITIALIZED:
    raise SystemExit("bootstrap can only be minted while setup is UNINITIALIZED")
cfg.bootstrap_secret_hash = stored_hash
cfg.bootstrap_expires_at = expires_at
# Preserve every bootstrap_admin_* claim field; do not clear consumed state after setup advanced.
```

Update CLI text to say “Open `/setup` and create the first administrator”; remove the instruction to sign in first. Add tests proving an expired pending claim survives remint and an advanced install cannot remint itself into bootstrap.

- [ ] **Step 6: Replace the API route and remove old service exports**

Use Pydantic fields matching Task 1. Both endpoints depend only on `get_session`; neither depends on `get_current_user` or an authorization `require` dependency. Map `created` to `201/200` without adding it to the response body. Remove `BootstrapRequest`, `setup_bootstrap_endpoint`, `bootstrap_admin`, and its `__all__` export.

- [ ] **Step 7: Run focused and affected GREEN gates**

Run:

```bash
cd apps/api && uv run pytest tests/unit/test_setup.py tests/unit/test_setup_administrator.py tests/unit/test_first_admin_contract.py -q
cd apps/api && uv run pytest tests/integration/test_setup.py -q
cd apps/api && uv run ruff check src/easysynq_api/api/setup.py src/easysynq_api/services/setup src/easysynq_api/cli/setup.py tests/unit/test_setup.py tests/unit/test_setup_administrator.py tests/integration/test_setup.py
cd apps/api && uv run mypy src/easysynq_api/api/setup.py src/easysynq_api/services/setup src/easysynq_api/cli/setup.py
```

Expected: all setup tests and static checks PASS. Search confirms no executable consumer of `/setup/bootstrap` or `bootstrap_admin` remains.

- [ ] **Step 8: Review and commit the setup state-machine checkpoint**

Run:

```bash
rg -n '/setup/bootstrap|bootstrap_admin' apps/api packages/contracts apps/web infra scripts docs --glob '!docs/superpowers/**' --glob '!docs/adr/**' --glob '!docs/debt/**' --glob '!docs/slice-history.md'
git diff --check -- apps/api/src/easysynq_api/api/setup.py apps/api/src/easysynq_api/services/setup apps/api/src/easysynq_api/cli/setup.py apps/api/tests/unit/test_setup_administrator.py apps/api/tests/integration/test_setup.py
git add apps/api/src/easysynq_api/api/setup.py apps/api/src/easysynq_api/services/setup apps/api/src/easysynq_api/cli/setup.py apps/api/tests/unit/test_setup_administrator.py apps/api/tests/integration/test_setup.py
git commit -m "feat: provision first administrator during setup"
```

---

### Task 5: Move ordinary user provisioning onto the shared identity primitives

**Files:**

- Modify: `apps/api/src/easysynq_api/api/users.py`
- Modify: `apps/api/tests/integration/test_users_provision.py`
- Modify: `apps/api/tests/unit/test_identity_provisioning.py`

**Interfaces:**

- `POST /users/provision` keeps its request, `201` response, PEP gates, role behavior, collision affordances, staged audit commits, and problem mapping.
- `POST /users/{id}/temporary-password` keeps `user.create` plus R64's unconditional system-tier reset guard.
- Both routes obtain `identity_provisioning.keycloak_client()` and call the shared create/credential units; no route-local `_kc_client` remains.

- [ ] **Step 1: Point integration mocks at the future shared factory and observe RED**

Change `_install_kc` and all direct monkeypatches to target `easysynq_api.services.identity.provisioning.keycloak_client`. Add a preservation test proving ordinary provisioning sends no bootstrap marker and a reset still refuses a caller who has `user.create` but is not system-tier, regardless of target roles.

Run:

```bash
cd apps/api && uv run pytest tests/integration/test_users_provision.py -q
```

Expected: FAIL because `api.users` still calls its private `_kc_client` and bypasses the patched shared factory.

- [ ] **Step 2: Refactor only the external identity legs**

Import the module, not a copied function binding:

```python
from ..services.identity import provisioning as identity_provisioning
```

Replace lookup/create with `ensure_credentialless_identity`; catch `IdentityUsernameExists` and reuse `_raise_username_conflict(session, exc.subject)`. Preserve distinct email conflict, invalid input, 502, and 503 mappings. Replace each direct password generation/reset pair with `issue_temporary_credential`. Keep role validation before Keycloak, the database commit before password issuance, response precomputation, nonfatal credential-audit commit, user/role audit order, and R64 system-tier reset guard byte-for-byte in behavior.

- [ ] **Step 3: Run GREEN plus setup/ordinary cross-checks**

Run:

```bash
cd apps/api && uv run pytest tests/unit/test_identity_provisioning.py tests/unit/test_keycloak_provisioning.py -q
cd apps/api && uv run pytest tests/integration/test_users_provision.py tests/integration/test_setup.py -q
cd apps/api && uv run ruff check src/easysynq_api/api/users.py tests/integration/test_users_provision.py
cd apps/api && uv run mypy src/easysynq_api/api/users.py src/easysynq_api/services/identity
```

Expected: ordinary and first-admin provisioning PASS through the same primitives with their distinct authorization/audit boundaries intact.

- [ ] **Step 4: Review and commit the convergence checkpoint**

Run:

```bash
git diff --check -- apps/api/src/easysynq_api/api/users.py apps/api/tests/integration/test_users_provision.py apps/api/tests/unit/test_identity_provisioning.py
git add apps/api/src/easysynq_api/api/users.py apps/api/tests/integration/test_users_provision.py apps/api/tests/unit/test_identity_provisioning.py
git commit -m "refactor: converge in-app identity provisioning"
```

---

### Task 6: Build the volatile show-once first-administrator setup UI

**Files:**

- Create: `apps/web/src/setup/FirstAdministratorStep.tsx`
- Create: `apps/web/src/setup/FirstAdministratorStep.test.tsx`
- Modify: `apps/web/src/lib/types.ts`
- Modify: `apps/web/src/admin/ShowOncePassword.tsx`
- Modify: `apps/web/src/admin/CreateUserModal.test.tsx`
- Modify: `apps/web/src/admin/UsersAdmin.test.tsx`
- Modify: `apps/web/src/SetupWizard.tsx`
- Modify: `apps/web/src/SetupWizard.test.tsx`
- Modify: `apps/web/src/App.tsx`
- Modify: `apps/web/src/App.test.tsx`

**Interfaces:**

- `FirstAdministratorStep` owns profile, setup secret, temporary password, pending, collision, and retry state only in React component memory.
- `ShowOncePassword` gains optional `doneLabel`, `description`, and `busy`; existing callers retain current defaults.
- `SetupWizard` receives `setupState`, renders the public step only for `UNINITIALIZED`, and never calls sensitive `GET /setup` without a token.
- `App` refetches setup state after acknowledgment; when the fresh state is `IN_SETUP` and no token exists, the existing one-shot redirect latch starts OIDC exactly once.

- [ ] **Step 1: Add web types and write the focused RED interaction suite**

Add exact handwritten request/response types matching Task 1. Tests must prove:

- the form labels are Setup secret, Username, Display name, Email, First name, Last name;
- submit uses `POST /api/v1/setup/administrator` with `token=null`;
- success replaces the form with `Temporary password — shown once` and does not put either secret in `location`, `localStorage`, `sessionStorage`, or TanStack Query data;
- Copy uses the credential in memory;
- Continue posts only the setup secret to `/acknowledge`, stays on the panel while pending, clears both secrets before calling `onAcknowledged`, and calls it once;
- acknowledgment failure keeps the panel and exposes Retry without redirecting;
- `bootstrap_identity_bound` shows only the server-provided `bound_username` after valid-secret proof;
- outage, expired secret, validation, and unrelated collision render actionable messages without a Keycloak subject; and
- a cancelable `beforeunload` guard is active while provision/acknowledgment is pending or a password is visible, is removed after successful acknowledgment, and a later reload cannot re-display the old password; resubmission performs a reset and shows only the new value.

Run:

```bash
npm --prefix apps/web test -- src/setup/FirstAdministratorStep.test.tsx
```

Expected: collection FAIL because the component does not exist.

- [ ] **Step 2: Extend the shared show-once presentation without changing existing defaults**

```typescript
interface ShowOncePasswordProps {
  password: string;
  onDone: () => void;
  doneLabel?: string;
  description?: string;
  busy?: boolean;
}
```

Default `doneLabel` remains `Done`; default description remains the later-user handoff copy. Bootstrap supplies `I’ve saved it — Continue to sign in`, recovery-aware text, and `busy`. Disable both actions while acknowledgment is pending so navigation cannot discard a response mid-request. Keep the password only in props and preserve current CreateUser/Users tests.

- [ ] **Step 3: Implement `FirstAdministratorStep`**

Use `apiSend` directly rather than a retained query result. On success, synchronously copy `temporary_password` into local state and discard the response object. Do not add it to a query key, mutation cache, URL, toast, or error. Normalize optional blank fields to `null`; require nonblank display name and let the server bind exact trimmed username. Install a `beforeunload` listener whenever provision/acknowledgment is pending or the show-once value is visible; remove it after acknowledgment or component cleanup. Before `onAcknowledged`, set password and secret to empty strings.

- [ ] **Step 4: Write App/SetupWizard routing RED tests**

Add tests proving:

- `UNINITIALIZED` plus no token renders Create the first administrator and does not call `login`;
- `IN_SETUP` plus no token never renders the form, sets the existing redirect latch, and calls `login` once;
- acknowledgment refetches `/setup/state`, observes `IN_SETUP`, and then calls `login` once;
- acknowledgment/refetch failure does not call `login`;
- `IN_SETUP` plus token renders the existing organization/storage/backup/auth/finalize wizard;
- `OPERATIONAL` still routes to the app; and
- no frontend request reaches `/api/v1/setup/bootstrap`.

Run:

```bash
npm --prefix apps/web test -- src/SetupWizard.test.tsx src/App.test.tsx
```

Expected: FAIL because the existing wizard asks the operator to sign in before bootstrap and posts the removed endpoint.

- [ ] **Step 5: Split pre-authentication and authenticated setup rendering**

Give `SetupWizard` this boundary:

```typescript
interface SetupWizardProps {
  setupState: "UNINITIALIZED" | "IN_SETUP";
  token: string | null;
  login: () => Promise<void> | void;
  onBootstrapAcknowledged: () => Promise<void>;
  onFinalized: () => Promise<void>;
}
```

`UNINITIALIZED` renders `FirstAdministratorStep` as Step 1 without enabling `GET /setup`. `IN_SETUP` with a token starts at Organization and preserves every existing gate. `IN_SETUP` without a token shows named sign-in recovery while App's redirect effect owns the one automatic login. Remove the old secret field, Activate button, and `/setup/bootstrap` mutation.

In App, treat `IN_SETUP || OPERATIONAL` as authentication-required after setup-state success. Use the existing `es_auth_redirect` one-shot latch; `onBootstrapAcknowledged` only awaits `setupState.refetch({cancelRefetch:false})`, and the effect owns the single `login()` call after the result advances.

- [ ] **Step 6: Add accessibility, forced-colors, and 320px proofs**

In focused component tests, run `axe` against form, show-once, bound-collision, and outage states. Assert native labels, focus moves to the show-once heading/error, live feedback is announced, buttons meet the existing 44 CSS-pixel target style, long maximum values do not create document-level overflow at 320 CSS pixels, and forced-colors mode retains visible focus/action boundaries. Do not create a duplicate mobile DOM.

- [ ] **Step 7: Run focused and affected GREEN gates**

Run:

```bash
npm --prefix apps/web test -- src/setup/FirstAdministratorStep.test.tsx src/SetupWizard.test.tsx src/App.test.tsx src/admin/CreateUserModal.test.tsx src/admin/UsersAdmin.test.tsx
npm --prefix apps/web run lint
npm --prefix apps/web run build
```

Expected: all focused/neighboring tests, lint, typecheck, and production build PASS; only previously known build diagnostics may remain.

- [ ] **Step 8: Review and commit the web checkpoint**

Run:

```bash
rg -n '/api/v1/setup/bootstrap' apps/web/src apps/web/e2e
git diff --check -- apps/web/src/setup apps/web/src/admin/ShowOncePassword.tsx apps/web/src/admin/CreateUserModal.test.tsx apps/web/src/admin/UsersAdmin.test.tsx apps/web/src/SetupWizard.tsx apps/web/src/SetupWizard.test.tsx apps/web/src/App.tsx apps/web/src/App.test.tsx apps/web/src/lib/types.ts
git add apps/web/src/setup apps/web/src/admin/ShowOncePassword.tsx apps/web/src/admin/CreateUserModal.test.tsx apps/web/src/admin/UsersAdmin.test.tsx apps/web/src/SetupWizard.tsx apps/web/src/SetupWizard.test.tsx apps/web/src/App.tsx apps/web/src/App.test.tsx apps/web/src/lib/types.ts
git commit -m "feat: create first administrator in setup"
```

---

### Task 7: Converge appliance, installer guidance, and current manuals

**Files:**

- Delete: `infra/appliance/provision/bin/easysynq-create-user`
- Modify: `infra/appliance/provision/easysynq-provision.sh`
- Modify: `apps/api/tests/unit/test_deploy_configuration.py`
- Modify: `docs/runbooks/appliance-install.md`
- Modify: `docs/runbooks/install-online.md`
- Modify: `docs/runbooks/install-ubuntu-server.md`
- Modify: `docs/manuals/installation-guide.md`
- Modify: `docs/manuals/administrator-it-manual.md`
- Modify: `docs/08-setup-and-onboarding.md`
- Modify: `docs/15-api-design.md`
- Modify: `docs/dev-workflow.md`

**Interfaces:**

- New appliance installs create no fixed human Keycloak account and write no human password to `EASYSYNQ-SETUP.txt`.
- The setup sheet retains only the application URL, one-time EasySynQ setup secret, expiry/remint instruction, and next browser step.
- The fixed `easysynq` VM console credential in `infra/appliance/seed/user-data.yaml` and `Install-EasySynQ.ps1` is an operating-system console boundary, not a Keycloak account, and remains unchanged.
- `scripts/new-keycloak-user.sh`, `POST /users`, and `grant-role` remain explicitly labeled break-glass/orphan-adoption tools; no normal install or user-creation path asks for a subject.

- [ ] **Step 1: Write the deployment-document RED contract**

Add assertions that the appliance provisioner contains none of:

```python
FORBIDDEN_HUMAN_BOOTSTRAP_TEXT = (
    "qmsadmin",
    "easysynq-create-user",
    "Sign in:",
    "EasySynQ-Setup-1   (you must set a new password)",
)
```

Assert the setup sheet still contains the application URL and one-time bootstrap secret, instructs the operator to create the first administrator in `/setup`, keeps mode `0600`, and never writes a temporary human password. Assert the obsolete helper file is absent. Add a current-doc scan over the listed runbooks/manuals that rejects normal-flow instructions to create a Keycloak user, paste a subject, or call `/setup/bootstrap`.

Run:

```bash
cd apps/api && uv run pytest tests/unit/test_deploy_configuration.py -k 'appliance or first_admin or setup_sheet' -q
```

Expected: FAIL on fixed `qmsadmin`, password, helper, and old documentation.

- [ ] **Step 2: Remove the appliance account step and obsolete helper**

Delete the Keycloak creation step. Rewrite the sheet sequence to:

1. open the application URL;
2. enter the one-time setup secret and create the first administrator;
3. save the shown-once temporary password and continue;
4. sign in, replace the password, and complete the remaining setup gates.

Remove `easysynq-create-user` from helper lists and delete its payload file. Keep internal `KEYCLOAK_ADMIN_*` generation, Keycloak redirect setup, bootstrap minting, file permissions, and console-ready behavior.

- [ ] **Step 3: Rewrite current operator documentation from executable truth**

Across the listed current docs, state that EasySynQ creates the first Keycloak identity through `/setup`; the operator never visits Keycloak or handles a subject; SMTP is not required; the password is shown once and must be changed at first sign-in. Describe later-user creation through Administration → Users, keeping `user.create`, `user.update`, `permission.grant`, and system-tier reset separation. Keep host scripts only in a clearly named break-glass/orphan-recovery section.

Update API documentation to the two new paths and system-actor audit events. Do not rewrite historical plans or old shipped narrative as if it were current authority.

- [ ] **Step 4: Run GREEN, authority, and site-data checks**

Run:

```bash
cd apps/api && uv run pytest tests/unit/test_deploy_configuration.py -q
just authority-check
bash scripts/check-no-site-data.sh
```

Expected: deployment contracts, repository authority, and site-data checks PASS.

- [ ] **Step 5: Review and commit the install-path checkpoint**

Run:

```bash
rg -n 'qmsadmin|easysynq-create-user|/setup/bootstrap|create.*Keycloak.*first|paste.*subject' infra/appliance docs/runbooks docs/manuals docs/08-setup-and-onboarding.md docs/15-api-design.md docs/dev-workflow.md
git diff --check -- infra/appliance/provision apps/api/tests/unit/test_deploy_configuration.py docs/runbooks/appliance-install.md docs/runbooks/install-online.md docs/runbooks/install-ubuntu-server.md docs/manuals/installation-guide.md docs/manuals/administrator-it-manual.md docs/08-setup-and-onboarding.md docs/15-api-design.md docs/dev-workflow.md
git add -A infra/appliance/provision apps/api/tests/unit/test_deploy_configuration.py docs/runbooks/appliance-install.md docs/runbooks/install-online.md docs/runbooks/install-ubuntu-server.md docs/manuals/installation-guide.md docs/manuals/administrator-it-manual.md docs/08-setup-and-onboarding.md docs/15-api-design.md docs/dev-workflow.md
git commit -m "docs: converge installs on in-app administrator setup"
```

---

### Task 8: Prove mocked browser resilience and live Keycloak first login

**Files:**

- Modify: `apps/web/e2e/support/api.ts`
- Modify: `apps/web/e2e/smoke.spec.ts`
- Create: `apps/web/e2e-live/first-admin.spec.ts`
- Create: `apps/web/playwright.live.config.ts`
- Create: `scripts/test-first-admin-keycloak.sh`
- Modify: `apps/web/package.json`
- Modify: `apps/web/vite.config.ts`
- Modify: `apps/web/tsconfig.browser.json`
- Modify: `apps/api/tests/unit/test_deploy_configuration.py`
- Modify: `docs/adr/0003-use-playwright-for-responsive-browser-evidence.md`
- Modify: `docs/debt/20260813234519-playwright-responsive-browser-harness.md`

**Interfaces:**

- Existing `npm run test:browser` remains the deterministic synthetic routed/browser gate.
- New `npm run test:first-admin-live` runs only `e2e-live` against an already-running real stack.
- New `scripts/test-first-admin-keycloak.sh` exclusively owns environment generation, unique Compose startup, bootstrap minting, redirect authorization, live Playwright execution, and validated cleanup.
- Required live flow: fresh form → show-once password → acknowledgment → Keycloak temporary login → required password change → authenticated `/setup` → original temporary password rejected in a clean browser context.

- [ ] **Step 1: Re-read browser debt and add a mocked browser RED**

Re-read `20260813234519-playwright-responsive-browser-harness.md` and `20260815200349-first-admin-live-ci.md`. Extend the central fixture router for exact public setup-state/provision/acknowledge requests, abort all undeclared traffic, and add a smoke case for the `UNINITIALIZED` form, long-value 320px containment, keyboard focus, show-once acknowledgment failure/retry, and no premature OIDC call.

Run:

```bash
npm --prefix apps/web run test:browser -- --grep "first administrator setup"
```

Expected: FAIL because the fixture and browser case do not exist.

- [ ] **Step 2: Implement and pass the deterministic browser representation**

Keep the existing dedicated authenticated entry and fail-closed API router. Add only setup fixtures and assertions; do not weaken undeclared-request aborts or make the synthetic harness start real Keycloak.

Run:

```bash
npm --prefix apps/web run test:browser -- --grep "first administrator setup"
```

Expected: focused Chromium case PASS with one worker and zero retries.

- [ ] **Step 3: Write fail-closed live harness structural RED tests**

Add source-level tests requiring:

- refusal when repository `.env` already exists;
- a Compose project matching `easysynq-first-admin-[a-z0-9]+` and explicit `-p` on every Compose call;
- a trap installed before stack startup;
- cleanup guarded by both the validated project prefix and a `stack_started` flag;
- `down -v --remove-orphans` scoped to that project only;
- removal only of the exact `.env` file the harness created after resolving it beneath the isolated worktree;
- no default project, `just down -v`, broad wildcard, or recursive delete;
- Chromium, one worker, zero retries; and
- the package/live config excluded from Vitest discovery and included in browser TypeScript checking.

Run:

```bash
cd apps/api && uv run pytest tests/unit/test_deploy_configuration.py -k 'first_admin_live' -q
```

Expected: FAIL because the live config and harness do not exist.

- [ ] **Step 4: Implement the isolated live runner**

The script must:

```bash
PROJECT="easysynq-first-admin-$(openssl rand -hex 6)"
case "$PROJECT" in easysynq-first-admin-[a-z0-9]*) ;; *) exit 2 ;; esac
test ! -e .env || { echo "live acceptance refuses an existing .env" >&2; exit 2; }
```

Generate `.env` with `EASYSYNQ_ENV_ONLY=1 ./scripts/install.sh s`; mark ownership in a shell boolean; choose unused high loopback app and S3 ports; set all browser/OIDC/Keycloak URLs consistently to the app origin while retaining internal JWKS/discovery URLs; and construct one Compose argv with base, `compose.s.yml`, and `compose.dev.yml` plus the validated project.

Start `up -d --build`, wait on `/readyz`, authorize `${APP_ORIGIN}/*` with `easysynq_api.cli.keycloak_redirect`, mint a bootstrap secret inside the API container, and pass only the secret/base URL/random username/new password through process environment to Playwright. The trap must stop only that validated project and unlink only the exact `.env` it created. Preserve logs/traces on failure long enough for bounded reporting, then clean containers/volumes.

- [ ] **Step 5: Implement the live Playwright flow**

`playwright.live.config.ts` fails immediately if `EASYSYNQ_LIVE_BASE_URL` is absent, uses `testDir: "./e2e-live"`, Chromium only, `workers: 1`, `retries: 0`, no `webServer`, and `trace: "off"`, `screenshot: "off"`, `video: "off"` so a failed credential flow cannot persist browser artifacts containing secrets.

The spec must:

1. navigate to `/setup` and assert no Keycloak page was needed;
2. submit the environment-provided setup secret and random admin profile;
3. read the temporary password only from the show-once panel;
4. acknowledge and wait for the real Keycloak login form;
5. submit username and temporary password;
6. complete Keycloak's required update-password form with the generated new password;
7. assert return to authenticated `/setup` with the Organization step and no first-admin form;
8. create a second clean browser context, navigate to `/setup`, attempt the same username with the original temporary password, and assert Keycloak rejects it; and
9. never print either password or setup secret in assertion messages, attachments, or test titles.

- [ ] **Step 6: Run the real Docker-backed acceptance**

Run as a durable background job:

```bash
bash scripts/test-first-admin-keycloak.sh
```

Expected: PASS in Chromium with one worker and zero retries; the output records the test count but not credentials. On failure, retrieve bounded logs, fix through focused RED/GREEN, and rerun the same exact command. Confirm no harness containers, volumes, `.env`, or plaintext secret artifact remains afterward.

- [ ] **Step 7: Reassess the existing browser-harness debt**

Update ADR 0003 with a dated reassessment: the responsive register gate remains synthetic and fail-closed because the new production-auth acceptance is a separate narrow identity test; live-stack coverage is no longer absent repository-wide. Narrow `20260813234519-playwright-responsive-browser-harness.md` to the remaining responsive-engine/cohort boundary and remove production-auth/live-stack absence from its body/payoff trigger. Keep `20260815200349-first-admin-live-ci.md` open because no required PR job was added.

- [ ] **Step 8: Run complete browser/static preservation and commit**

Run:

```bash
npm --prefix apps/web run test:browser
npm --prefix apps/web run lint
npm --prefix apps/web run build
cd apps/api && uv run pytest tests/unit/test_deploy_configuration.py -q
git diff --check -- apps/web/e2e apps/web/e2e-live apps/web/playwright.live.config.ts apps/web/package.json apps/web/vite.config.ts apps/web/tsconfig.browser.json scripts/test-first-admin-keycloak.sh apps/api/tests/unit/test_deploy_configuration.py docs/adr/0003-use-playwright-for-responsive-browser-evidence.md docs/debt/20260813234519-playwright-responsive-browser-harness.md
git add apps/web/e2e apps/web/e2e-live apps/web/playwright.live.config.ts apps/web/package.json apps/web/vite.config.ts apps/web/tsconfig.browser.json scripts/test-first-admin-keycloak.sh apps/api/tests/unit/test_deploy_configuration.py docs/adr/0003-use-playwright-for-responsive-browser-evidence.md docs/debt/20260813234519-playwright-responsive-browser-harness.md
git commit -m "test: require live first administrator Keycloak proof"
```

---

### Task 9: Complete whole-slice verification, reviews, and evidence bookkeeping

**Files:**

- Create: `.superpowers/sdd/2026-08-15-s-first-admin-provisioning/implementation-report.md`
- Modify: `docs/current-status.md`
- Modify: `docs/slice-history.md`
- Modify only if evidence changes it: `docs/open-residuals.md`
- Modify only if a debt is actually paid or discovered: `docs/debt/`

**Interfaces:**

- Produces a traceable implementation report with requirements mapping, commits, exact fresh commands/results, compatibility changes, diagnostics, and unverified boundaries.
- Updates mutable counts/snapshots only from completed full gates; keeps `baseline_commit` unchanged unless separate authority explicitly changes its meaning.
- Handoff leaves a clean isolated branch and the primary checkout's unrelated state untouched.

- [ ] **Step 1: Run affected API, migration, contract, web, and browser cohorts**

Run:

```bash
cd apps/api && uv run pytest tests/unit/test_first_admin_contract.py tests/unit/test_setup.py tests/unit/test_setup_administrator.py tests/unit/test_keycloak_provisioning.py tests/unit/test_identity_provisioning.py tests/unit/test_deploy_configuration.py -q
cd apps/api && uv run pytest tests/integration/test_setup.py tests/integration/test_users_provision.py -q
cd apps/api && uv run pytest tests/migration/test_migration_coherence.py -q
just test-contract
npm --prefix apps/web test -- src/setup/FirstAdministratorStep.test.tsx src/SetupWizard.test.tsx src/App.test.tsx src/admin/CreateUserModal.test.tsx src/admin/UsersAdmin.test.tsx
npm --prefix apps/web run test:browser
bash scripts/test-first-admin-keycloak.sh
```

Expected: every affected gate PASS. Record known warnings exactly; do not normalize a setup error, retry, deselection, or missing Docker dependency into a pass.

- [ ] **Step 2: Run full suites as durable background jobs**

Start separate durable jobs for these exact argv and collect each final result:

```bash
cd apps/api && uv run pytest tests/unit -m unit
cd apps/api && uv run pytest tests/integration -m integration
cd apps/api && uv run pytest tests/integration/test_contract_response_schemas.py -m contract
npm --prefix apps/web test
```

Expected: all full jobs exit `0`. Update test totals only from these completed outputs. If resource limits require sequential jobs, preserve the exact commands rather than combining/truncating them.

- [ ] **Step 3: Run final static, generated, migration, authority, and site-data gates**

Run:

```bash
cd apps/api && uv run ruff format --check .
cd apps/api && uv run ruff check .
cd apps/api && uv run mypy src
npm --prefix apps/web run lint
npm --prefix apps/web run build
just contracts-check
cd apps/api && uv run alembic heads
just authority-check
bash scripts/check-no-site-data.sh
git diff --check origin/main...HEAD
```

Expected: all commands PASS; Alembic reports only `0087_first_admin_bootstrap (head)`. The current-status migration snapshot may then advance to `0087` with `0088` next.

- [ ] **Step 4: Perform independent whole-branch requirements and security/code-quality reviews**

Review `origin/main...HEAD` against every design acceptance criterion, R64/R65/R66, ADR 0005, the claim/debt records, public endpoint secrecy, concurrency, non-deletion, audit ordering, migration downgrade/upgrade, browser volatile state, installer removal, and cleanup safety. A second independent review must inspect transaction seams, Keycloak collision classification, ORM expiry after rollback, rate-limit behavior, bearer irrelevance, request/response logging exposure, Playwright trace secrecy, and changed-file test sufficiency.

For every material finding: write the smallest focused failing regression, implement the minimal correction, rerun affected gates, and commit a bounded fix. Do not fold unrelated cleanup into the review wave.

- [ ] **Step 5: Write the implementation report and current evidence**

The report must include:

- observable first-install and later-user outcomes;
- the old endpoint/helper removals under R65;
- every migration, contract, API, web, installation, and browser file family changed;
- exact commit IDs and exact commands/results;
- live Keycloak flow evidence including one worker/zero retries and old-password rejection;
- retained system-tier credential-reset, permission separation, and break-glass boundaries;
- known diagnostics and every unverified boundary; and
- the status of `bootstrap-claim-state-machine`, `playwright-responsive-browser-harness`, and `first-admin-live-ci` debt.

Update `docs/current-status.md` only with fresh complete counts and migration facts. Add a dated `S-first-admin-provisioning` entry to `docs/slice-history.md` without claiming merge, deployment, SMTP, non-Chromium engines, assistive technology, or Fedora proof.

- [ ] **Step 6: Run final document/branch checks and commit evidence**

Run:

```bash
just authority-check
bash scripts/check-no-site-data.sh
git diff --check -- .superpowers/sdd/2026-08-15-s-first-admin-provisioning/implementation-report.md docs/current-status.md docs/slice-history.md docs/open-residuals.md docs/debt
git status --short
git add .superpowers/sdd/2026-08-15-s-first-admin-provisioning/implementation-report.md docs/current-status.md docs/slice-history.md docs/open-residuals.md docs/debt
git commit -m "docs: record first administrator provisioning evidence"
```

- [ ] **Step 7: Verify the committed branch and stop for owner approval**

Run:

```bash
git status --short --branch
git log --oneline --decorate origin/main..HEAD
git diff --check origin/main...HEAD
git -C ../.. status --short --branch
```

Expected: feature worktree clean; primary checkout still shows only its pre-existing `.superdesign/`; unrelated `/tmp` worktree records remain untouched. Present the outcome, commits, tests, compatibility changes, open debt, and unverified boundaries. Do not push, open a PR, merge, deploy, delete the worktree, or prune anything until the owner explicitly approves the next action.
