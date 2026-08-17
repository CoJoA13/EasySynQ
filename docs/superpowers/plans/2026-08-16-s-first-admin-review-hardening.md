# First Administrator Review Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close all five unresolved PR #466 review findings by binding bootstrap to the claim-owned administrator and active temporary-password generation, making definitive failures recoverable, reconciling marker-owned profiles, and keeping reminted-secret recovery inside the show-once browser flow.

**Architecture:** Migration `0088` adds a nullable SHA-256 receipt digest to the `system_config` singleton. Every Keycloak password reset rotates a random volatile receipt, and acknowledgment requires both the bootstrap secret and the active receipt; claimed-profile correction performs a marker-verified full-representation Keycloak update and the EasySynQ projection update under the singleton lock. The SPA keeps the password, receipt, secret, and profile in component memory, accepts a reminted secret without resetting a current password, and offers explicit reissue only after a generation is superseded.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, SQLAlchemy 2 async, PostgreSQL 16, Alembic, Redis, httpx, Keycloak 26.7 Admin REST/OIDC, pytest, OpenAPI 3.1, React 19, TypeScript 6, Mantine 7, Vitest 4, MSW 2, Docker Compose, Playwright Test Chromium.

## Global Constraints

- Work only in the existing linked feature worktree on `codex/first-admin-bootstrap`. Confirm the branch starts at approved design commit `d1f22c0`, and preserve the primary checkout's unrelated `.superdesign/` directory.
- Do not prune or modify either unrelated prunable `/tmp` worktree record.
- Binding authority is R64–R66, ADR 0005 including its 2026-08-16 receipt amendment, `docs/superpowers/specs/2026-08-15-s-first-admin-provisioning-design.md`, and `docs/superpowers/specs/2026-08-16-s-first-admin-review-hardening-design.md`.
- Re-read the relevant debt before each hotspot: bootstrap claim state machine before `services/setup`, credential lock and credential-receipt state before `administrator.py`, Keycloak profile reconciliation before `keycloak_provisioning.py`, bootstrap admission coupling before `setup/service.py`, and responsive/live-CI debt before browser or harness changes.
- Preserve R64 ordering: create or adopt the Keycloak identity without a credential, commit the EasySynQ user and role, then reset the credential. Never delete a Keycloak identity as compensation.
- A valid bootstrap proof is checked before administrator-existence disclosure. Public setup may continue only with no System Administrator or the one administrator linked to the active claim.
- A definitive create-time non-conflict Keycloak 4xx releases only a still-unowned claim. Outage, malformed response, ambiguous conflict, existing marker-owned identity, linked user, or issued credential retains the claim.
- Generate credential receipts with at least 256 random bits. Persist only a lowercase 64-character SHA-256 digest. Never place the plaintext receipt, setup secret, password, Keycloak subject, or claim marker in logs, audit payloads, problems, URLs, storage, query caches, mutation caches, setup sheets, or public user projections.
- `credential_receipt` is required in every successful provision response and acknowledgment request. No compatibility endpoint, optional fallback, or receipt-free acknowledgment survives under R65.
- A stale receipt returns `409 bootstrap_credential_superseded`, records no consumption, and does not alter the current credential. A mismatched secret remains generic counted `403 bootstrap_invalid`, regardless of receipt content.
- Preserve consumed same-secret/same-receipt acknowledgment replay after expiry. Preserve all complete-claim and administrator-assignment checks on replay.
- The Keycloak claimed-profile updater verifies exact subject, canonical username, and exact bootstrap marker, copies the full returned user representation, and changes only `email`, `emailVerified`, `firstName`, and `lastName`. It never updates an unrelated identity.
- Every behavior change begins with the smallest focused failing test. Read `superpowers:test-driven-development` and its `writing-good-tests.md` reference before implementation.
- Each task ends with requirements review and security/code-quality review before its scoped commit. Do not resolve or reply to GitHub review threads until the implementation has fresh evidence and the owner explicitly authorizes GitHub replies.
- Use durable background jobs for the Docker-backed live proof and genuinely long full suites. Never overlap the Docker-heavy live and full integration jobs.
- Do not change `baseline_commit`, CI topology, migration snapshot, or test counts without fresh corresponding evidence. Do not claim Firefox, WebKit, actual assistive technology, SMTP, deployment, general live acceptance, or disposable Fedora proof.
- Do not push, merge, deploy, prune worktrees, or modify GitHub review-thread state without explicit owner approval.

## File Ownership Map

- `migrations/versions/0088_bootstrap_credential_receipt.py`, `apps/api/src/easysynq_api/db/models/system_config.py` — nullable receipt digest and database invariant.
- `packages/contracts/openapi.yaml` plus generated bundle, API model, TypeScript schema, and contract lock — required receipt fields and stable review-hardening problem codes.
- `apps/api/src/easysynq_api/problems.py`, `apps/api/src/easysynq_api/api/setup.py` — canonical errors and HTTP request/response wiring.
- `apps/api/src/easysynq_api/services/setup/administrator.py` — administrator uniqueness, definitive claim release, serialized profile projection, receipt issuance, and acknowledgment fencing.
- `apps/api/src/easysynq_api/services/keycloak_provisioning.py`, `apps/api/src/easysynq_api/services/identity/provisioning.py` — exact marker-owned full-user profile reconciliation and shared identity result.
- `apps/web/src/setup/FirstAdministratorStep.tsx`, its focused tests, and `apps/web/src/lib/types.ts` — volatile receipt state, reminted-secret acknowledgment, and explicit reissue.
- `apps/web/e2e/support/api.ts`, `apps/web/e2e/smoke.spec.ts`, `apps/web/e2e-live/first-admin.spec.ts`, `scripts/test-first-admin-keycloak.sh`, and deployment structural tests — synthetic and live receipt/recovery evidence.
- `apps/api/tests/unit/test_first_admin_contract.py`, `test_setup_administrator.py`, `test_identity_provisioning.py`, `test_keycloak_provisioning.py`, migration coherence, setup integration, and response-schema tests — contract, pure, transport, concurrency, and populated-migration proofs.
- `docs/decisions-register.md`, `docs/08-setup-and-onboarding.md`, `docs/15-api-design.md`, `docs/current-status.md`, `docs/slice-history.md`, existing implementation report, ADR/debt records — final authority and freshly verified evidence only.

---

### Task 1: Persist the active credential receipt digest

**Files:**

- Create: `migrations/versions/0088_bootstrap_credential_receipt.py`
- Modify: `apps/api/src/easysynq_api/db/models/system_config.py`
- Modify: `apps/api/tests/migration/test_migration_coherence.py`

**Interfaces:**

- Produces database field `SystemConfig.bootstrap_credential_receipt_hash: str | None`.
- The nullable state is inert until Task 4 publishes and implements the receipt-bearing API.
- Persisted digests match `^[0-9a-f]{64}$`; no plaintext receipt column is created.

- [ ] **Step 1: Add failing migration-head and populated-round-trip assertions**

Extend `_BOOTSTRAP_CLAIM_COLUMNS` with `bootstrap_credential_receipt_hash`, assert Alembic head is
`0088_bootstrap_credential`, seed a 64-character digest at 0088, downgrade to 0087 and prove
only that column disappears, then re-upgrade and prove the row survives with the nullable column
restored:

```python
_BOOTSTRAP_RECEIPT_HASH = "a" * 64

connection.execute(
    sa.text(
        "UPDATE system_config SET bootstrap_credential_receipt_hash = :digest "
        "WHERE org_id = :org"
    ),
    {"digest": _BOOTSTRAP_RECEIPT_HASH, "org": org_id},
)
command.downgrade(config, "0087_first_admin_bootstrap")
assert _column_nullable(connection, "system_config", "bootstrap_credential_receipt_hash") is None
command.upgrade(config, "head")
assert _column_nullable(connection, "system_config", "bootstrap_credential_receipt_hash") == "YES"
```

- [ ] **Step 2: Run migration RED**

Run:

```bash
cd apps/api && UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest tests/migration/test_migration_coherence.py -k populated -q
```

Expected: migration coherence fails because head remains 0087 and the receipt column is absent.

- [ ] **Step 3: Implement migration and ORM state**

Create revision `0088_bootstrap_credential` with `down_revision = "0087_first_admin_bootstrap"`.
The owner approved this shortened internal identifier because the repository's Alembic version
column is `VARCHAR(32)`; retain the descriptive filename and do not widen Alembic infrastructure:

```python
def upgrade() -> None:
    op.add_column(
        "system_config",
        sa.Column("bootstrap_credential_receipt_hash", sa.String(length=64), nullable=True),
    )
    op.create_check_constraint(
        "ck_system_config_bootstrap_credential_receipt_hash_hex",
        "system_config",
        "bootstrap_credential_receipt_hash IS NULL OR "
        "bootstrap_credential_receipt_hash ~ '^[0-9a-f]{64}$'",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_system_config_bootstrap_credential_receipt_hash_hex",
        "system_config",
        type_="check",
    )
    op.drop_column("system_config", "bootstrap_credential_receipt_hash")
```

Mirror the column as `Mapped[str | None] = mapped_column(String(64), nullable=True)` and update the
nearby comment to say the plaintext receipt is never stored.

- [ ] **Step 4: Run Task 1 GREEN gates**

Run:

```bash
cd apps/api && UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest tests/migration/test_migration_coherence.py -k populated -q
cd apps/api && UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run alembic heads
```

Expected: focused tests pass and Alembic reports exactly `0088_bootstrap_credential (head)`.

- [ ] **Step 5: Review and commit Task 1**

Run scoped Ruff and diff checks, confirm the migration is linear and downgrade is bounded, obtain
requirements plus migration review, then commit:

```bash
git add migrations/versions/0088_bootstrap_credential_receipt.py \
  apps/api/src/easysynq_api/db/models/system_config.py \
  apps/api/tests/migration/test_migration_coherence.py
git commit -m "feat: persist first-admin credential receipt state"
```

---

### Task 2: Refuse unrelated administrators and release definitive rejected claims

**Files:**

- Modify: `apps/api/src/easysynq_api/services/setup/administrator.py`
- Modify after owner-approved concurrency review: `apps/api/src/easysynq_api/services/authz/admin_guard.py`
- Modify after owner-approved concurrency review: `apps/api/src/easysynq_api/services/authz/__init__.py`
- Modify after owner-approved concurrency review: `apps/api/src/easysynq_api/api/authz.py`
- Modify after owner-approved concurrency review: `apps/api/src/easysynq_api/api/users.py`
- Modify after owner-approved concurrency review: `apps/api/src/easysynq_api/cli/grant_role.py`
- Modify: `apps/api/src/easysynq_api/problems.py`
- Modify: `packages/contracts/openapi.yaml`
- Modify: `apps/api/tests/unit/test_first_admin_contract.py`
- Modify: `apps/api/tests/integration/test_setup.py`
- Modify after owner-approved concurrency review: `apps/api/tests/integration/test_authz.py`
- Modify after owner-approved concurrency review: `apps/api/tests/integration/test_users_provision.py`
- Modify: `apps/api/tests/unit/test_setup_administrator.py`
- Modify after owner-approved concurrency review: `docs/adr/0005-provision-first-administrator-in-setup.md`
- Modify after owner-approved concurrency review: `docs/debt/20260815194752-bootstrap-claim-state-machine.md`
- Create after owner-approved concurrency review: `docs/debt/20260816120427-bootstrap-provider-lock-duration.md`
- Regenerate: `packages/contracts/dist/openapi.json`
- Regenerate: `packages/contracts/.contract.lock`
- Regenerate: `apps/api/src/easysynq_api/_generated/models.py`
- Regenerate: `apps/web/src/api/_generated/schema.d.ts`

**Interfaces:**

- Produces `_assert_only_claim_administrator(session, cfg) -> None`.
- Produces `409 bootstrap_administrator_exists` only after valid bootstrap proof.
- Narrows claim release to a definitively absent lookup followed by `KeycloakRejected` from create.
- Owner-approved review correction: every supported System Administrator writer shares the existing
  per-organization admin-set advisory lock. Bootstrap acquires singleton then admin-set lock before
  Keycloak identity work and retains both through persistence or fresh-absence release revalidation.
  Ordinary role grants do not acquire the admin-set lock.

- [ ] **Step 1: Write the stable-error contract RED**

Require `bootstrap_administrator_exists` and `bootstrap_credential_superseded` in both canonical
problem enums, and require the provision/acknowledgment 409 descriptions to exclude invalid/expired
secret wording:

```python
problem_codes = set(schemas["FirstAdministratorProblem"]["properties"]["code"]["enum"])
assert {"bootstrap_administrator_exists", "bootstrap_credential_superseded"} <= problem_codes
for operation in (provision, acknowledge):
    description = operation["responses"]["409"]["description"].lower()
    assert "invalid secret" not in description
    assert "expired" not in description
```

- [ ] **Step 2: Write administrator-existence RED cases**

Add focused integration tests that use the real database:

```python
async def test_break_glass_administrator_blocks_public_claim(...):
    secret = await _reset_uninitialized()
    grant_role(_sub("existing-admin"))
    response = await _provision(app_client, secret, _sub("second-admin"))
    assert response.status_code == 409
    assert response.json()["code"] == "bootstrap_administrator_exists"
    assert (await _config()).bootstrap_admin_claim_id is None
    assert _setup_keycloak.accounts == {}


async def test_unrelated_administrator_blocks_pending_claim_without_releasing_it(...):
    secret = await _reset_uninitialized()
    await _establish_claim_only(secret, _sub("bound-admin"))
    grant_role(_sub("unrelated-admin"))
    response = await _provision(app_client, secret, _sub("bound-admin"))
    assert response.status_code == 409
    assert response.json()["code"] == "bootstrap_administrator_exists"
    assert (await _config()).bootstrap_admin_claim_id is not None
```

Also prove a claim-owned System Administrator can reissue and acknowledge normally.

- [ ] **Step 3: Write definitive rejection RED**

Extend the existing redaction test so the fake returns `KeycloakRejected` before account creation,
then assert the claim is cleared and a corrected identity can be provisioned:

```python
rejected = await _provision(app_client, secret, "rejected-admin")
assert rejected.status_code == 422
assert (await _config()).bootstrap_admin_claim_id is None
_setup_keycloak.rejection_detail_template = None
assert (await _provision(app_client, secret, "corrected-admin")).status_code == 201
```

Keep separate assertions that lookup outage, malformed marker, fail-after-create, and rejected update
retain the claim.

- [ ] **Step 4: Run Task 2 RED**

Run:

```bash
cd apps/api && UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest tests/integration/test_setup.py -k 'break_glass_administrator_blocks or unrelated_administrator_blocks or keycloak_rejection_releases' -q
cd apps/api && UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest tests/unit/test_first_admin_contract.py -q
```

Expected: the existing-admin cases incorrectly proceed and the create rejection leaves the claim bound.

- [ ] **Step 5: Implement the serialized administrator invariant and canonical errors**

Query all System Administrator assignment user IDs in the singleton organization:

```python
async def _assert_only_claim_administrator(session: AsyncSession, cfg: SystemConfig) -> None:
    ids = set(
        (
            await session.scalars(
                select(RoleAssignment.user_id)
                .join(Role, Role.id == RoleAssignment.role_id)
                .where(RoleAssignment.org_id == cfg.org_id, Role.name == SYSTEM_ADMIN_ROLE)
            )
        ).all()
    )
    allowed = {cfg.bootstrap_admin_user_id} if cfg.bootstrap_admin_user_id is not None else set()
    if ids - allowed or (ids and not allowed):
        raise ProblemException(
            status=409,
            code="bootstrap_administrator_exists",
            title="An administrator already exists outside this bootstrap claim",
        )
```

Call it after proof validation in `_establish_claim`, and at the locked persistence, issuance, and
acknowledgment boundaries. Do not clear the claim on this error.

Add `bootstrap_administrator_exists` and `bootstrap_credential_superseded` to `ProblemCode` and the
OpenAPI shared enum. Update both 409 descriptions, run `just contracts`, then require
`bash scripts/tests/test-gen-contracts.sh` and `just contracts-check` to pass without a second-run
diff. The superseded code is published here for one atomic canonical vocabulary; Task 4 begins using
it when receipt acknowledgment becomes executable.

- [ ] **Step 6: Release only a definitively rejected create**

Keep the initial lookup outcome in `_resolve_identity`. Catch `KeycloakRejected` immediately around
the create/adopt call only when `initial.found is False`; call `_release_unowned_claim` before
returning redacted `422 validation_error`. Do not use the outer generic `KeycloakRejected` handler to
release because Task 3's claimed-profile update rejection must retain the identity-bound claim.

- [ ] **Step 7: Run Task 2 GREEN and preservation tests**

Run:

```bash
cd apps/api && UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest tests/integration/test_setup.py -k 'administrator or claim or collision or rejection or outage or marker' -q
cd apps/api && UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest tests/unit/test_setup_administrator.py -q
cd apps/api && UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest tests/unit/test_first_admin_contract.py -q
just contracts-check
```

Expected: new cases pass; existing collision, outage, marker, audit, remint, and claim-recovery cases remain green.

- [ ] **Step 8: Review and commit Task 2**

Run scoped Ruff/mypy/diff checks, obtain requirements and security review, then commit only the
service and focused tests:

```bash
git add apps/api/src/easysynq_api/services/setup/administrator.py \
  apps/api/src/easysynq_api/problems.py apps/api/tests/integration/test_setup.py \
  apps/api/tests/unit/test_setup_administrator.py apps/api/tests/unit/test_first_admin_contract.py \
  packages/contracts/openapi.yaml packages/contracts/dist/openapi.json \
  packages/contracts/.contract.lock apps/api/src/easysynq_api/_generated/models.py \
  apps/web/src/api/_generated/schema.d.ts
git commit -m "fix: fence first-admin bootstrap authority"
```

---

### Task 3: Reconcile the exact claimed Keycloak profile

**Files:**

- Modify: `apps/api/src/easysynq_api/services/keycloak_provisioning.py`
- Modify: `apps/api/src/easysynq_api/services/identity/provisioning.py`
- Modify: `apps/api/src/easysynq_api/services/setup/administrator.py`
- Modify: `apps/api/tests/unit/test_keycloak_provisioning.py`
- Modify: `apps/api/tests/unit/test_identity_provisioning.py`
- Modify: `apps/api/tests/integration/test_setup.py`

**Interfaces:**

- Produces `KeycloakProvisioningClient.reconcile_claimed_user_profile(...) -> None`.
- Inputs: exact `subject`, canonical `username`, `bootstrap_claim_id`, and optional email/name values.
- Behavior: full GET, exact identity/marker validation, copy-preserving conditional PUT, redacted fail-closed errors.
- `_persist_user_and_role` receives the open Keycloak client and updates existing EasySynQ display/email projection from the same normalized request.

- [ ] **Step 1: Write transport RED for preservation and exact ownership**

Add tests using `httpx.MockTransport` with a full representation containing required actions,
custom attributes, federation link, timestamps, and the bootstrap marker. Require a GET followed by
one PUT whose JSON differs only in approved fields:

```python
await client.reconcile_claimed_user_profile(
    subject="sub-1",
    username="first.admin",
    bootstrap_claim_id=claim,
    email="corrected@example.local",
    first_name="Corrected",
    last_name=None,
)
assert put_body["id"] == "sub-1"
assert put_body["username"] == "first.admin"
assert put_body["attributes"] == original["attributes"]
assert put_body["requiredActions"] == original["requiredActions"]
assert put_body["federationLink"] == original["federationLink"]
assert put_body["email"] == "corrected@example.local"
assert put_body["emailVerified"] is True
assert put_body["firstName"] == "Corrected"
assert put_body["lastName"] is None
```

Add no-PUT equality, wrong subject, wrong username, missing/malformed/wrong marker, 400/409 rejected,
403/5xx unavailable, malformed JSON, and cleared optional field cases.

- [ ] **Step 2: Write setup-service RED for two-store convergence**

Extend `_FakeKeycloak` so account representations retain email, first name, last name, marker, and
unrelated fields. Add:

```python
async def test_claimed_retry_reconciles_corrected_profile_in_both_stores(...):
    # Fail after Keycloak create, then retry the same bound username with corrected optional fields.
    # Assert Keycloak and AppUser contain the corrected values and the marker remains unchanged.


async def test_concurrent_profile_retries_converge_on_one_complete_profile(...):
    # Submit two complete profiles concurrently. Assert final Keycloak and AppUser projections equal
    # either complete profile A or complete profile B, never a field-wise mixture.
```

Add a rejected claimed-profile update test that returns 422, redacts provider details, retains the
claim, and succeeds after corrected input.

- [ ] **Step 3: Run Task 3 RED**

Run:

```bash
cd apps/api && UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest tests/unit/test_keycloak_provisioning.py -k claimed_user_profile -q
cd apps/api && UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest tests/integration/test_setup.py -k 'reconciles_corrected_profile or concurrent_profile_retries or rejected_claimed_profile' -q
```

Expected: client method is absent and setup retries leave Keycloak profile unchanged.

- [ ] **Step 4: Implement the fail-closed Keycloak full-representation update**

Add the exact method:

```python
async def reconcile_claimed_user_profile(
    self,
    *,
    subject: str,
    username: str,
    bootstrap_claim_id: uuid.UUID,
    email: str | None,
    first_name: str | None,
    last_name: str | None,
) -> None:
```

GET `/admin/realms/{realm}/users/{quoted-subject}`. Require a dict, exact `id`, exact `username`, and
`_bootstrap_claim_id(representation) == str(bootstrap_claim_id)`. Copy with `copy.deepcopy`, assign
`email`, `emailVerified = email is not None`, `firstName`, and `lastName`, compare those effective
fields, and PUT the complete copy only when changed. Map 400/409 to bounded `KeycloakRejected`; map
403/404/5xx, transport failures, malformed bodies, or ownership mismatches to
`KeycloakUnavailable`.

- [ ] **Step 5: Serialize Keycloak and EasySynQ projection reconciliation**

Pass the open client into `_persist_user_and_role`. After locking and `_assert_claim`, invoke
`reconcile_claimed_user_profile` before writing AppUser fields. For both new and recovered rows set:

```python
user.display_name = profile.display_name
user.email = profile.email
```

Keep username canonical and external-only. A Keycloak rejection here returns redacted 422 without
calling `_release_unowned_claim`; a failed EasySynQ commit leaves a marker-owned identity that the
next retry reconciles again.

- [ ] **Step 6: Run Task 3 GREEN and shared-provisioning preservation**

Run:

```bash
cd apps/api && UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest tests/unit/test_keycloak_provisioning.py tests/unit/test_identity_provisioning.py -q
cd apps/api && UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest tests/integration/test_setup.py -k 'profile or identity or collision or database_failure' -q
cd apps/api && UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest tests/integration/test_users_provision.py -q
```

Expected: all new profile proofs and ordinary user provisioning pass; ordinary username collisions
are not adopted or rewritten.

- [ ] **Step 7: Review and commit Task 3**

Run scoped Ruff/mypy/diff checks, inspect every Keycloak request for raw-detail/subject leakage,
obtain requirements and security review, then commit:

```bash
git add apps/api/src/easysynq_api/services/keycloak_provisioning.py \
  apps/api/src/easysynq_api/services/identity/provisioning.py \
  apps/api/src/easysynq_api/services/setup/administrator.py \
  apps/api/tests/unit/test_keycloak_provisioning.py \
  apps/api/tests/unit/test_identity_provisioning.py \
  apps/api/tests/integration/test_setup.py
git commit -m "fix: reconcile claimed first-admin profiles"
```

---

### Task 4: Fence acknowledgment to the active password generation

**Files:**

- Modify: `apps/api/src/easysynq_api/services/setup/administrator.py`
- Modify: `apps/api/src/easysynq_api/api/setup.py`
- Modify: `apps/api/src/easysynq_api/services/setup/__init__.py`
- Modify: `apps/api/tests/unit/test_setup_administrator.py`
- Modify: `apps/api/tests/unit/test_first_admin_contract.py`
- Modify: `apps/api/tests/integration/test_setup.py`
- Modify: `apps/api/tests/integration/test_contract_response_schemas.py` only if the generated schema fixture requires an explicit new success value.
- Modify: `packages/contracts/openapi.yaml`
- Regenerate: `packages/contracts/dist/openapi.json`
- Regenerate: `packages/contracts/.contract.lock`
- Regenerate: `apps/api/src/easysynq_api/_generated/models.py`
- Regenerate: `apps/web/src/api/_generated/schema.d.ts`

**Interfaces:**

- `FirstAdministratorProvisioned` adds `credential_receipt: str`.
- `acknowledge_first_administrator(session, *, secret: str, credential_receipt: str)` replaces the receipt-free signature.
- Produces private helpers `_new_credential_receipt() -> tuple[str, str]` and `_receipt_matches(receipt, digest) -> bool`.

- [ ] **Step 1: Write receipt-bearing OpenAPI RED**

Require:

```python
provisioned = schemas["FirstAdministratorProvisioned"]
assert provisioned["required"] == [
    "administrator",
    "temporary_password",
    "credential_receipt",
    "password_delivery",
]
assert provisioned["properties"]["credential_receipt"] == {
    "type": "string",
    "minLength": 43,
    "maxLength": 43,
    "pattern": "^[A-Za-z0-9_-]{43}$",
}
ack = schemas["BootstrapAcknowledgeRequest"]
assert ack["required"] == ["secret", "credential_receipt"]
```

- [ ] **Step 2: Write pure receipt RED**

Add unit assertions:

```python
receipt, digest = _new_credential_receipt()
assert len(receipt) == 43
assert re.fullmatch(r"[A-Za-z0-9_-]{43}", receipt)
assert digest == hashlib.sha256(receipt.encode()).hexdigest()
assert _receipt_matches(receipt, digest) is True
assert _receipt_matches("x" * 43, digest) is False
assert _receipt_matches(receipt, None) is False
```

Patch `hmac.compare_digest` and prove missing, malformed, and wrong receipts still execute one
comparison against a fixed dummy digest.

- [ ] **Step 3: Write issuance/acknowledgment RED**

Add deterministic integration tests:

```python
first = await _provision(app_client, secret, username)
second = await _provision(app_client, secret, username)
assert first.json()["credential_receipt"] != second.json()["credential_receipt"]

stale = await app_client.post(
    "/api/v1/setup/administrator/acknowledge",
    json={"secret": secret, "credential_receipt": first.json()["credential_receipt"]},
)
assert stale.status_code == 409
assert stale.json()["code"] == "bootstrap_credential_superseded"
assert (await _config()).setup_state is SetupState.UNINITIALIZED

current = await app_client.post(
    "/api/v1/setup/administrator/acknowledge",
    json={"secret": secret, "credential_receipt": second.json()["credential_receipt"]},
)
assert current.status_code == 200
```

Update every existing acknowledgment helper/caller to provide the provision response receipt only
after this RED is observed.

- [ ] **Step 4: Add transaction-failure RED**

Characterize two distinct failures:

- audit INSERT/flush fails inside the savepoint: provision still returns 200/201, receipt hash is
  durable, and acknowledgment succeeds;
- outer receipt-state commit fails after Keycloak reset: API returns a redacted
  `503 dependency_unavailable` without password or receipt,
  then a retry rotates the password and returns acknowledgeable state.

Assert no plaintext receipt appears in `system_config`, audit `after`, captured logs, or problem text.

- [ ] **Step 5: Run Task 4 RED**

Run:

```bash
cd apps/api && UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest tests/unit/test_setup_administrator.py -k receipt -q
cd apps/api && UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest tests/unit/test_first_admin_contract.py -q
cd apps/api && UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest tests/integration/test_setup.py -k 'credential_receipt or stale_receipt or receipt_state_commit or credential_audit_savepoint' -q
```

Expected: helpers/fields are absent and stale acknowledgment currently consumes bootstrap.

- [ ] **Step 6: Implement receipt creation and required state commit**

Use:

```python
_DUMMY_RECEIPT_DIGEST = "0" * 64


def _new_credential_receipt() -> tuple[str, str]:
    receipt = secrets.token_urlsafe(32)
    return receipt, hashlib.sha256(receipt.encode("utf-8")).hexdigest()


def _receipt_matches(receipt: str, stored_digest: str | None) -> bool:
    supplied = hashlib.sha256(receipt.encode("utf-8")).hexdigest()
    return hmac.compare_digest(supplied, stored_digest or _DUMMY_RECEIPT_DIGEST)
```

After Keycloak accepts the reset, assign the new digest and issued timestamp, `flush()` that state in
the outer transaction, then add/flush `USER_CREDENTIAL_ISSUED` inside `session.begin_nested()`.
Catch only the nested audit failure, log no sensitive value, and commit the outer transaction. If the
outer flush/commit fails, rollback and raise redacted `503 dependency_unavailable`; return neither
password nor receipt. Return both only after the required commit succeeds.

- [ ] **Step 7: Implement receipt-bound acknowledgment, API wiring, and contract**

Require `credential_receipt` with `min_length=43`, `max_length=43`, and the URL-safe pattern in
`BootstrapAcknowledgeRequest`. Include the receipt in the provision response and pass it into:

```python
async def acknowledge_first_administrator(
    session: AsyncSession, *, secret: str, credential_receipt: str
) -> dict[str, str]:
```

After secret validation and complete-claim checks, compare the receipt before either fresh
consumption or consumed replay. On mismatch raise the stable 409. Retain receipt hash after success.

Add the exact receipt constraints to `FirstAdministratorProvisioned` and
`BootstrapAcknowledgeRequest` in OpenAPI, update the 503 description to cover required bootstrap
state persistence as well as missing provider configuration, run `just contracts`, and prove
generator plus `contracts-check` convergence before API response-schema tests.

- [ ] **Step 8: Run Task 4 GREEN and affected API cohort**

Run:

```bash
cd apps/api && UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest tests/unit/test_setup.py tests/unit/test_setup_administrator.py tests/unit/test_first_admin_contract.py -q
cd apps/api && UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest tests/integration/test_setup.py tests/integration/test_backup.py tests/integration/test_users_provision.py -q
cd apps/api && UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest tests/integration/test_contract_response_schemas.py -m contract -q
```

Expected: all receipt, replay, transaction, setup, backup, users, and response-schema cases pass.

- [ ] **Step 9: Review and commit Task 4**

Run scoped Ruff/mypy/diff checks, obtain requirements and security review with explicit transaction,
rollback, constant-time, and logging scrutiny, then commit:

```bash
git add apps/api/src/easysynq_api/services/setup/administrator.py \
  apps/api/src/easysynq_api/api/setup.py \
  apps/api/src/easysynq_api/services/setup/__init__.py \
  apps/api/tests/unit/test_setup_administrator.py apps/api/tests/unit/test_first_admin_contract.py \
  apps/api/tests/integration/test_setup.py \
  apps/api/tests/integration/test_contract_response_schemas.py \
  packages/contracts/openapi.yaml packages/contracts/dist/openapi.json \
  packages/contracts/.contract.lock apps/api/src/easysynq_api/_generated/models.py \
  apps/web/src/api/_generated/schema.d.ts
git commit -m "fix: bind first-admin acknowledgment to credential"
```

---

### Task 5: Keep reminted-secret and superseded-password recovery in the SPA

**Files:**

- Modify: `apps/web/src/lib/types.ts`
- Modify: `apps/web/src/setup/FirstAdministratorStep.tsx`
- Modify: `apps/web/src/setup/FirstAdministratorStep.test.tsx`
- Verify unchanged: `apps/web/src/App.tsx`, `apps/web/src/SetupWizard.tsx`, `apps/web/src/admin/ShowOncePassword.tsx`

**Interfaces:**

- `FirstAdministratorProvisioned.credential_receipt: string` mirrors generated OpenAPI.
- Component refs add `receiptRef` and never put receipt/secret/password into React Query or browser storage.
- Acknowledgment state is one of ordinary retry, replacement-secret required, or superseded/reissue required.

- [ ] **Step 1: Write focused frontend RED**

Add MSW-driven tests that prove:

```typescript
expect(await acknowledgeRequest.json()).toEqual({
  secret: MAXIMUM_FIRST_ADMIN_SECRET,
  credential_receipt: "R".repeat(43),
});
```

Then cover:

- `bootstrap_invalid`: password remains visible; **Enter current setup secret** appears; submitting a
  replacement secret retries acknowledgment with the same receipt and no second provision request;
- `bootstrap_credential_superseded`: shown password is labeled no longer current; **Issue a new
  temporary password** posts the bound normalized profile and current secret, then replaces both
  password and receipt;
- successful acknowledgment clears password, receipt, and secret synchronously before
  `onAcknowledged` observes component/query/storage state;
- local/session storage spies and QueryClient mutation/query observers never see receipt values; and
- beforeunload stays installed during replacement-secret and reissue requests.

- [ ] **Step 2: Run frontend RED**

Run:

```bash
npm --prefix apps/web test -- src/setup/FirstAdministratorStep.test.tsx
```

Expected: acknowledgment body lacks receipt and neither recovery control exists.

- [ ] **Step 3: Implement volatile receipt and typed acknowledgment states**

Add `credential_receipt` to the local interface. Destructure both response values synchronously:

```typescript
const { temporary_password, credential_receipt } =
  await apiSend<FirstAdministratorProvisioned>(...);
passwordRef.current = temporary_password;
receiptRef.current = credential_receipt;
```

Replace the boolean failure flag with:

```typescript
type AcknowledgeRecovery = "retry" | "replacement-secret" | "superseded" | null;
```

Branch `ApiError.code`: `bootstrap_invalid` selects replacement-secret; the new superseded code
selects reissue; every network/5xx failure selects ordinary retry.

- [ ] **Step 4: Implement replacement-secret and reissue controls**

For replacement proof, render one required `PasswordInput` labeled **Current setup secret** and a
button **Retry with current setup secret**. Update only `secretRef`/form secret and call acknowledgment
with the existing receipt.

For superseded state, mark the shown value as invalid and render **Issue a new temporary password**.
Call the existing provision function with the retained normalized profile and current secret; do not
clear the old presentation until the replacement response is available. Replace password and receipt
in one synchronous state boundary.

On success clear:

```typescript
passwordRef.current = "";
receiptRef.current = "";
secretRef.current = "";
setTemporaryPassword("");
setForm((current) => ({ ...current, secret: "" }));
```

before awaiting `onAcknowledged()`.

- [ ] **Step 5: Run Task 5 GREEN and adjacent preservation**

Run:

```bash
npm --prefix apps/web test -- src/setup/FirstAdministratorStep.test.tsx src/SetupWizard.test.tsx src/App.test.tsx src/admin/CreateUserModal.test.tsx src/admin/UsersAdmin.test.tsx
npm --prefix apps/web run lint
npm --prefix apps/web run build
```

Expected: focused recovery and all existing setup/user-create tests pass; lint/build exit 0 with only
the registered Vite large-chunk advisory.

- [ ] **Step 6: Review and commit Task 5**

Search the changed frontend for `localStorage`, `sessionStorage`, query/mutation APIs, URLs, toasts,
and logging; prove none receives secrets. Obtain requirements plus accessibility/security review,
then commit:

```bash
git add apps/web/src/lib/types.ts apps/web/src/setup/FirstAdministratorStep.tsx \
  apps/web/src/setup/FirstAdministratorStep.test.tsx
git commit -m "fix: recover first-admin credential acknowledgment"
```

---

### Task 6: Prove responsive synthetic and live Keycloak recovery

**Files:**

- Modify: `apps/web/e2e/support/api.ts`
- Modify: `apps/web/e2e/smoke.spec.ts`
- Modify: `apps/web/e2e-live/first-admin.spec.ts`
- Modify: `apps/api/tests/unit/test_deploy_configuration.py`
- Modify only if required by exact live orchestration: `scripts/test-first-admin-keycloak.sh`

**Interfaces:**

- Synthetic fixture returns a 43-character receipt and requires exact receipt-bearing acknowledgment.
- Browser scenario demonstrates ordinary failure, reminted-secret recovery, superseded receipt, reissue, and successful login boundary at 320×800 forced colors.
- Live scenario uses the real API receipt and unchanged isolated Compose project/cleanup contract.

- [ ] **Step 1: Write structural and browser RED**

Extend deployment structural guards so the live spec must read `credential_receipt`, post it on
acknowledgment, and never write it to storage or URL. Update the synthetic scenario before its router
so it expects:

```typescript
expect(acknowledgmentBody).toEqual({
  secret: MAXIMUM_FIRST_ADMIN_SECRET,
  credential_receipt: MAXIMUM_FIRST_ADMIN_RECEIPT,
});
```

Add exact assertions for the replacement-secret and reissue controls, zero overflow, 44px action
targets, visible forced-colors focus, and no login until the active receipt succeeds.

- [ ] **Step 2: Run exact browser RED**

Run:

```bash
cd apps/api && UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest tests/unit/test_deploy_configuration.py -k first_admin_live -q
npm --prefix apps/web run test:browser -- --grep "first administrator setup"
```

Expected: structural receipt guard and exact routed request fail against the old fixture/spec.

- [ ] **Step 3: Update fail-closed synthetic routing**

Add `MAXIMUM_FIRST_ADMIN_RECEIPT = "R".repeat(43)`. Return it with provision responses and require it
in acknowledgment. Route one generic failure, one `bootstrap_invalid`, and one
`bootstrap_credential_superseded` deterministically; reject every unexpected body, bearer header,
old endpoint, external request, setup-detail GET in tokenless mode, or premature login.

- [ ] **Step 4: Update the real live flow**

Capture the real provision response receipt and include it in acknowledgment. Keep all output
secret-free: do not print response bodies, passwords, receipts, bootstrap secrets, or Keycloak admin
credentials. Preserve one Chromium test, one worker, zero retries, validated unique project cleanup,
`.env` refusal, `--rmi local`, and absence checks for project containers, volumes, networks, images,
Playwright artifacts, and temporary `.env`.

- [ ] **Step 5: Run synthetic GREEN and full shared-browser preservation**

Run:

```bash
cd apps/api && UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest tests/unit/test_deploy_configuration.py -q
npm --prefix apps/web run test:browser -- --grep "first administrator setup"
npm --prefix apps/web run test:browser
```

Expected: deploy guards pass; focused case passes; full Chromium passes with one worker and zero retries.

- [ ] **Step 6: Run the live proof as a durable isolated job**

Before launch verify root `.env` is absent and no `easysynq-first-admin-*` resources exist. Launch
from the feature-worktree root with exact argv:

```bash
bash scripts/test-first-admin-keycloak.sh
```

Retrieve the bounded finished result. Require exit 0, exactly 1/1 Chromium test, one worker, zero
retries, forced password change, authenticated `/setup`, original temporary-password rejection, and
recorded exact-project cleanup. Independently confirm no `.env`, Playwright artifact directories, or
matching containers/volumes/networks/images remain.

- [ ] **Step 7: Review and commit Task 6**

Review output and source for secrets, project-name validation, external network fail-closed behavior,
and receipt non-retention. Obtain requirements plus harness/security review, then commit:

```bash
git add apps/web/e2e/support/api.ts apps/web/e2e/smoke.spec.ts \
  apps/web/e2e-live/first-admin.spec.ts apps/api/tests/unit/test_deploy_configuration.py \
  scripts/test-first-admin-keycloak.sh
git commit -m "test: prove first-admin credential recovery"
```

If the harness script did not change, omit it from `git add` and state that explicitly in the report.

---

### Task 7: Converge binding authority and current API documentation

**Files:**

- Modify: `docs/decisions-register.md`
- Modify: `docs/08-setup-and-onboarding.md`
- Modify: `docs/15-api-design.md`
- Modify: `apps/api/tests/unit/test_first_admin_contract.py`
- Modify: `apps/api/tests/unit/test_deploy_configuration.py`
- Modify: `apps/api/tests/integration/test_users_provision.py`
- Append evidence: `.superpowers/sdd/2026-08-16-s-first-admin-review-hardening/task-7-report.md`

**Interfaces:**

- R66 clarifies that acknowledgment binds the active shown credential generation.
- Current docs describe reminted-secret acknowledgment, superseded-password reissue, unrelated-admin refusal, and marker-scoped profile correction without provider internals.
- The ordinary-user integration fixture uses the same managed Keycloak marker profile as production.

- [ ] **Step 1: Add executable documentation RED**

Extend the existing first-admin contract/deployment content tests to require current docs to name:

- acknowledgment of the active shown credential generation;
- entering a reminted setup secret without discarding a current password;
- explicit reissue after `bootstrap_credential_superseded`;
- refusal when an unrelated administrator already exists; and
- corrected optional profile reconciliation only for the bound first administrator.

Use explicit current-authority assertions rather than a broad keyword scan:

```python
assert "active shown credential generation" in decisions_register
assert "current setup secret" in setup_and_onboarding
assert "bootstrap_credential_superseded" in setup_and_onboarding
assert "bootstrap_administrator_exists" in api_design
assert "bound first administrator" in api_design
```

Run and observe failure before editing authority prose:

```bash
cd apps/api && UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest \
  tests/unit/test_first_admin_contract.py tests/unit/test_deploy_configuration.py \
  -k 'first_admin or bootstrap' -q
```

- [ ] **Step 2: Update binding and current documentation**

Add a dated R66 clarification matching the approved design. Update docs 08 and 15 with public request
and response shapes, stable errors, recovery states, and no-Keycloak-console operator wording. Do not
copy mutable test totals into manuals or change any owner-visible residual state.

- [ ] **Step 3: Converge the ordinary-user managed-profile fixture**

Keep the production Keycloak profile unchanged. Extend the existing test fixture with the exact
admin-only, non-multivalued `easysynqBootstrapClaim` attribute that production reconciliation now
requires. Run the smallest ordinary-user case first, then its full file:

```bash
cd apps/api && UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest \
  tests/integration/test_users_provision.py::test_provision_creates_the_account_the_row_and_the_credential -q
cd apps/api && UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest \
  tests/integration/test_users_provision.py -q
```

- [ ] **Step 4: Verify and commit the Task 7 authority phase**

Run the focused documentation contract, Ruff, mypy, authority, site-data, and diff checks. Append the
RED/GREEN and fixture-convergence evidence to the Task 7 report. Commit the fixture independently from
the authority prose so the scopes remain reviewable:

```bash
git add apps/api/tests/integration/test_users_provision.py
git commit -m "test: converge managed Keycloak profile fixture"

git add docs/decisions-register.md docs/08-setup-and-onboarding.md docs/15-api-design.md \
  apps/api/tests/unit/test_first_admin_contract.py \
  apps/api/tests/unit/test_deploy_configuration.py
git commit -m "docs: clarify first-admin credential recovery"
```

---

### Task 8: Add the host-only pre-operational administrator recovery command

**Files:**

- Modify: `apps/api/src/easysynq_api/cli/setup.py`
- Modify: `scripts/easysynq`
- Modify: `apps/api/tests/integration/test_setup.py`
- Modify: `apps/api/tests/unit/test_deploy_configuration.py`
- Modify: `docs/manuals/administrator-it-manual.md`
- Modify: `docs/adr/0005-provision-first-administrator-in-setup.md`
- Read and preserve: `docs/debt/20260816173328-uninitialized-admin-recovery.md`
- Append evidence: `.superpowers/sdd/2026-08-16-s-first-admin-review-hardening/task-8-report.md`

**Interfaces:**

- Add `easysynq setup release-administrator-blocker --subject <keycloak-subject> [--org CODE]`.
- The command is host-only, requires an independent incident/change record, and is valid only while
  the organization is exactly `UNINITIALIZED`.
- It removes only the named unrelated user's `System Administrator` assignment. It never deletes or
  disables the Keycloak identity or `app_user`, removes another role, advances setup, adopts a claim,
  consumes bootstrap proof, or grants a role.
- Lock order is `system_config` singleton row `FOR UPDATE` then the per-organization administrator
  advisory lock. All failure paths roll back.

- [ ] **Step 1: Add focused RED tests for recovery semantics**

Add real-database tests that prove:

- the exact unrelated administrator assignment blocks public bootstrap before recovery;
- recovery removes only that one assignment while preserving the `app_user`, Keycloak subject,
  display name, status, non-administrator assignments, and historical database rows;
- the claim-linked administrator is refused without mutation;
- `IN_SETUP` and `COMPLETED` are refused without mutation;
- an absent named user or already-absent assignment is safely repeatable and creates no persistent row;
- the singleton lock is taken before `lock_admin_set_sync`;
- an injected flush/commit failure rolls back the assignment removal; and
- result/error text does not disclose claim IDs, secrets, passwords, provider details, or unrelated
  administrator identities.

Add a structural deployment test that initially fails because the host wrapper/help and setup CLI do
not expose `release-administrator-blocker`.

Run RED before implementation:

```bash
cd apps/api && UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest \
  tests/integration/test_setup.py -k 'release_administrator_blocker' -q
cd apps/api && UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest \
  tests/unit/test_deploy_configuration.py -k 'administrator_blocker' -q
```

- [ ] **Step 2: Implement the minimal transactional command**

In `cli/setup.py`, add a sync one-shot operation that:

1. resolves the exact organization;
2. locks its `SystemConfig` row with `populate_existing=True`;
3. requires `SetupState.UNINITIALIZED`;
4. acquires `lock_admin_set_sync(session, org.id)`;
5. resolves the exact same-organization `AppUser` by Keycloak subject;
6. refuses when `bootstrap_admin_user_id` is that user;
7. resolves the seeded `System Administrator` role and exact assignment;
8. deletes only that assignment and commits once; and
9. rolls back every mutation-capable exception before emitting a generic operator-safe failure.

Do not call Keycloak and do not create an `app_user`, role, or assignment. The no-user/no-assignment
paths return a generic idempotent result without a persistent write. Add the argparse subcommand and
host-wrapper help. Document the independent incident/change-record requirement and the exact refusal
boundaries in the administrator manual. Amend ADR 0005 with the approved recovery boundary and link
the already-registered debt record; do not register duplicate debt.

- [ ] **Step 3: Run GREEN and affected transaction gates**

Run the focused tests first, then the setup integration file and relevant static checks:

```bash
cd apps/api && UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest \
  tests/integration/test_setup.py -k 'release_administrator_blocker' -q
cd apps/api && UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest \
  tests/unit/test_deploy_configuration.py -k 'administrator_blocker' -q
cd apps/api && UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest tests/integration/test_setup.py -q
cd apps/api && UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run ruff check \
  src/easysynq_api/cli/setup.py tests/integration/test_setup.py \
  tests/unit/test_deploy_configuration.py
cd apps/api && UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run mypy src
```

- [ ] **Step 4: Review and commit recovery independently**

Obtain requirements and security/transaction review. Explicitly inspect lock ordering, rollback after
ORM deletion, organization scoping, idempotency, claim-owner refusal, output redaction, and preservation
of identity/history/non-admin roles. Append the report, then commit only the reviewed recovery scope:

```bash
git add apps/api/src/easysynq_api/cli/setup.py scripts/easysynq \
  apps/api/tests/integration/test_setup.py \
  apps/api/tests/unit/test_deploy_configuration.py \
  docs/manuals/administrator-it-manual.md \
  docs/adr/0005-provision-first-administrator-in-setup.md
git commit -m "feat: recover blocked first-admin setup"
```

---

### Task 9: Converge supported install paths and both migration boundaries

**Files:**

- Modify: `docs/runbooks/fresh-linux-setup.md`
- Modify: `docs/manuals/installation-guide.md`
- Modify: `scripts/easysynq`
- Modify: `apps/api/tests/unit/test_deploy_configuration.py`
- Modify: `apps/api/tests/unit/test_first_admin_contract.py`
- Modify: `apps/api/tests/migration/test_migration_coherence.py`
- Append evidence: `.superpowers/sdd/2026-08-16-s-first-admin-review-hardening/task-9-report.md`

**Interfaces:**

- Supported fresh installs never create a fixed/demo Keycloak identity before first-admin setup.
- Operators mint the setup secret, open `/setup` without signing in, create the first administrator,
  show/copy the temporary password, acknowledge the active credential generation, then sign in and
  complete the required password change.
- `just demo-user` remains only an explicitly labeled post-bootstrap development fixture, not part of
  the supported first-install path.
- Populated migration evidence independently covers `0087 -> 0086 -> 0087` and
  `0088 -> 0087 -> 0088`.

- [ ] **Step 1: Add install-path and migration RED tests**

Extend executable documentation guards to reject `just demo-user`, fixed demo credentials, and
sign-in-before-setup language in the supported Fedora/installation first-run sections. Require the
exact `/setup` order and the host recovery command in the operator manual and CLI help.

Extend populated migration coherence so one test proves both boundaries independently:

1. at 0087, populate the claim/user binding, downgrade to 0086, prove only the 0087 claim columns,
   foreign key, and index are removed while organization/user/setup base rows remain, then re-upgrade
   to 0087 and prove nullable claim state is recreated cleanly;
2. repopulate the 0087 claim, upgrade to 0088, populate the receipt digest, downgrade to 0087 and
   prove the claim survives while only receipt storage disappears, then re-upgrade to 0088 and prove
   the nullable receipt column returns without fabricating a digest.

Run RED before document/test convergence:

```bash
cd apps/api && UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest \
  tests/unit/test_deploy_configuration.py tests/unit/test_first_admin_contract.py \
  -k 'fresh_linux or installation or first_admin or administrator_blocker' -q
cd apps/api && UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest \
  tests/migration/test_migration_coherence.py -k populated -q
```

- [ ] **Step 2: Update supported install and recovery guidance**

Remove the pre-bootstrap demo-user step from both supported install paths and correct the host CLI
help. Keep demo identity creation only in its clearly labeled post-bootstrap developer-fixture section.
Document `release-administrator-blocker` as a narrow pre-operational break-glass tool requiring an
independent incident/change record; do not present SQL, Keycloak-console manipulation, role deletion,
or identity deletion as a normal recovery path.

- [ ] **Step 3: Implement and verify both populated migration proofs**

Change only migration coherence tests; do not alter revision source. Run the focused populated test,
then the full migration test file and confirm Alembic has exactly one 0088 head:

```bash
cd apps/api && UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest \
  tests/migration/test_migration_coherence.py -k populated -q
cd apps/api && UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest \
  tests/migration/test_migration_coherence.py -q
cd apps/api && UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run alembic heads
```

- [ ] **Step 4: Review and commit acceptance convergence**

Review the supported install sequence and both populated downgrade/upgrade boundaries independently.
Run Ruff, authority, site-data, and diff checks. Append the report and commit the bounded acceptance
scope:

```bash
git add docs/runbooks/fresh-linux-setup.md docs/manuals/installation-guide.md \
  scripts/easysynq apps/api/tests/unit/test_deploy_configuration.py \
  apps/api/tests/unit/test_first_admin_contract.py \
  apps/api/tests/migration/test_migration_coherence.py
git commit -m "test: close first-admin acceptance gaps"
```

---

### Task 10: Run final evidence, re-review the whole branch, and converge current truth

**Files:**

- Modify: `.superpowers/sdd/2026-08-15-s-first-admin-provisioning/implementation-report.md`
- Modify only with fresh full evidence: `docs/current-status.md`
- Modify only with fresh full evidence: `docs/slice-history.md`
- Verify unchanged unless an owner-visible residual changes: `docs/open-residuals.md`
- Append evidence: `.superpowers/sdd/2026-08-16-s-first-admin-review-hardening/task-10-report.md`

- [ ] **Step 1: Run all short/affected final-tree gates**

Run:

```bash
cd apps/api && UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run ruff format --check .
cd apps/api && UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run ruff check .
cd apps/api && UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run mypy src
npm --prefix apps/web run lint
npm --prefix apps/web run build
just contracts-check
cd apps/api && UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run alembic heads
just authority-check
bash scripts/check-no-site-data.sh
git diff --check origin/main...HEAD
```

Require Ruff, mypy, ESLint, build, contract, 0088 head, authority, site-data, and diff checks to pass.
Record known diagnostics without upgrading them into coverage claims.

- [ ] **Step 2: Run full suites sequentially as durable jobs**

Use these exact workloads, never overlapping Docker-heavy live/integration. Because Task 8 modifies the
setup CLI module used by the live harness, obtain one fresh final-tree live proof before the full
integration suite:

```bash
bash scripts/test-first-admin-keycloak.sh
cd apps/api && env UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest tests/unit -m unit
cd apps/api && env UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest tests/integration -m integration
cd apps/api && env UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest tests/integration/test_contract_response_schemas.py -m contract
npm --prefix apps/web test
npm --prefix apps/web run test:browser
```

Retrieve bounded results and record exact files/tests/passed/skipped counts, one-worker Chromium
count, elapsed time, and diagnostics. A failed, cancelled, partial, or unavailable job is not a pass.

- [ ] **Step 3: Perform two independent whole-branch re-reviews**

Review `origin/main...HEAD` against R64–R66 and both approved designs. Requirements review covers all
prior findings, both migration boundaries, recovery safety, contract, compatibility,
install/browser/live behavior, and evidence truth.
Security/code-quality review explicitly covers row-lock/commit/savepoint ordering, ORM expiry and
rollback, stale receipt constant-time behavior, administrator existence disclosure, Keycloak marker
and full-representation validation, external-update debt, secret/log/cache/storage boundaries, and
live cleanup. It also reviews recovery organization scoping, lock order, exact-role removal,
claim-owner refusal, rollback, idempotency, and identity/history preservation. Resolve every
Critical/Important finding with a focused RED/GREEN round; do not merely document an unfixed merge
blocker.

- [ ] **Step 4: Update evidence only from accepted fresh results**

Append a dated review-hardening section to the implementation report. Update `current-status.md` and
the top Identity Onboarding slice-history entry only with freshly retrieved counts, migration head
0088/next 0089, contract hash, and required Chromium/live evidence. Keep `baseline_commit` unchanged.
Leave `open-residuals.md` byte-identical unless an existing `RES-*` closure contract was actually met.
Record the supported-install and host-recovery compatibility decisions without claiming SMTP,
Firefox/WebKit, actual assistive-technology use, deployment, general live acceptance, or disposable
Fedora proof.

- [ ] **Step 5: Run post-document gates and exact cleanup checks**

Re-run authority, site-data, contracts-check, Alembic head, changed-range diff, and the static gates
affected by documentation/generated changes. Confirm feature worktree clean except intended staged
files; primary checkout still contains only unrelated `.superdesign/`; `.env`, Playwright artifacts,
and exact live-project Docker resources are absent; unrelated `/tmp` worktree records remain untouched.
Before deleting generated Playwright artifacts, validate the exact three paths
`apps/web/.playwright-dist`, `apps/web/playwright-report`, and `apps/web/test-results`, confirm none is a
symlink/reparse point, and remove only those disposable outputs. Report that removal as non-recoverable
generated-test cleanup.

- [ ] **Step 6: Commit final evidence without pushing**

Stage only the reviewed authority/evidence files and commit:

```bash
git add docs/decisions-register.md docs/08-setup-and-onboarding.md docs/15-api-design.md \
  .superpowers/sdd/2026-08-15-s-first-admin-provisioning/implementation-report.md \
  docs/current-status.md docs/slice-history.md
git commit -m "docs: record first-admin review hardening evidence"
```

Omit any unchanged file from `git add`. Confirm the branch is clean and ahead of its remote. Report
all commits and evidence to the owner; do not push or resolve GitHub threads until explicitly approved.

---

## Final Acceptance Checklist

- [ ] All five unresolved review findings have focused RED/GREEN evidence.
- [ ] Alembic has one head: `0088_bootstrap_credential`; populated 0087/0088 round trip passes.
- [ ] Receipt plaintext exists only in the one provision response and volatile component memory.
- [ ] Stale receipt cannot consume bootstrap; active and consumed-replay receipts behave exactly as designed.
- [ ] Unrelated administrators block every public bootstrap stage without becoming an existence oracle.
- [ ] Definitive create rejection releases only an unowned claim; ambiguous/existing identity states retain it.
- [ ] Marker-owned profile correction preserves unrelated Keycloak fields and converges both stores.
- [ ] Reminted-secret acknowledgment and explicit superseded-password reissue are usable and accessible.
- [ ] Required synthetic and live Chromium evidence passes with one worker and zero retries.
- [ ] Full API unit, integration, contract, Vitest, and Chromium totals are freshly recorded or explicitly left unchanged when not rerun.
- [ ] Ruff, mypy, ESLint, build, contracts, authority, site-data, and diff gates pass on the final tree.
- [ ] Two independent whole-branch reviews have no unresolved Critical or Important finding.
- [ ] Supported first-install docs never create or sign in as a demo identity before `/setup` first-admin creation.
- [ ] Host-only blocker recovery is `UNINITIALIZED`-only, exact-subject, claim-owner refusing, transactionally rolled back on failure, and removes only the named System Administrator assignment.
- [ ] Populated `0087 -> 0086 -> 0087` and `0088 -> 0087 -> 0088` migration proofs both pass.
- [ ] No `.env`, Playwright artifact, live Docker resource, secret, site data, unrelated worktree edit, push, merge, deployment, or GitHub-thread mutation remains.
