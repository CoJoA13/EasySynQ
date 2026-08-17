# First-administrator PR Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make PR #466's Chromium check pass without weakening the responsive proof, give operators correct stable-code recovery guidance, and ensure a trusted bootstrap remint immediately clears stale public-proof throttling.

**Architecture:** Keep safe code-to-copy mapping and reissue presentation inside `FirstAdministratorStep`. Keep the host recovery correction inside the existing synchronous setup CLI transaction: delete the global bootstrap-failure Redis key while the locked PostgreSQL singleton is still uncommitted, aborting and rolling back if Redis cannot confirm reset. Document this cross-store ordering in ADR 0005 and the existing coupling debt record.

**Tech Stack:** React 19, TypeScript, Mantine, Vitest, Playwright Chromium, Python 3.13, SQLAlchemy 2, redis-py, PostgreSQL, Redis, pytest, Ruff, mypy, GitHub Actions.

## Global Constraints

- Preserve the full accessible action name `Issue a new temporary password`; visible text becomes exactly `Issue new password`.
- Keep the exact 320×800 forced-colors geometry, focus, 44 px target, and document-overflow assertions.
- Render only safe copy selected from a stable public error code; never render raw title, detail, subject, marker, or provider identifier.
- `bootstrap_administrator_exists` directs the operator to the documented host `release-administrator-blocker` procedure.
- `user_exists` says the bound username belongs to an unrelated identity and cannot be recovered by changing the form username.
- `keycloak_email_exists` says to retain the bound username and enter another email.
- Host-only remint clears `setup:bootstrap:fails` before committing or printing a replacement proof.
- Redis reset failure rolls back PostgreSQL and emits only an operator-safe error.
- Preserve singleton locking, setup-state checks, proof hashing/expiry, authority tiers, and public limiter behavior.
- Do not add a dependency, change OpenAPI, change migration head `0088_bootstrap_credential`, update residual state, push, merge, or resolve review threads before verification.

---

## File Structure

- `apps/web/src/setup/FirstAdministratorStep.tsx` — stable-code recovery copy and accessible/visible reissue labels.
- `apps/web/src/setup/FirstAdministratorStep.test.tsx` — safe copy, non-disclosure, and label split proofs.
- `apps/web/e2e/smoke.spec.ts` — unchanged real 320 px forced-colors geometry proof.
- `apps/api/src/easysynq_api/cli/setup.py` — synchronous trusted remint and Redis reset helper.
- `apps/api/tests/integration/test_setup.py` — real Redis reset, immediate admission, and rollback proofs.
- `docs/adr/0005-provision-first-administrator-in-setup.md` — ordering, alternatives, consequences, payoff.
- `docs/debt/20260816024758-bootstrap-admission-identity-coupling.md` — existing cross-store debt.
- Evidence homes change only if fresh complete-suite totals differ.

---

### Task 1: Safe recovery guidance and CI-stable reissue control

**Files:**
- Modify: `apps/web/src/setup/FirstAdministratorStep.tsx:46-130`
- Modify: `apps/web/src/setup/FirstAdministratorStep.tsx:406-420`
- Modify: `apps/web/src/setup/FirstAdministratorStep.test.tsx:811-856`
- Verify unchanged: `apps/web/e2e/smoke.spec.ts:275-291`

**Interfaces:**
- Consumes: `ApiError.code: string` and `PresentedError { heading, message, boundUsername? }`.
- Produces: separate safe mappings for three stable codes; a button with full accessible name and short visible text.

- [ ] **Step 1: Write the stable-code RED cases**

Replace the shared collision row and add the missing rows:

```ts
[
  "bootstrap_administrator_exists",
  409,
  "An existing System Administrator assignment blocks public setup. Run the documented host release-administrator-blocker recovery, then try again.",
],
[
  "user_exists",
  409,
  "The bound username belongs to an unrelated identity. Changing the username here cannot recover this claim. Ask a host identity administrator to resolve the collision.",
],
[
  "keycloak_email_exists",
  409,
  "That email belongs to another identity. Keep the bound username and enter another email.",
],
```

For every table row, retain injected unsafe `title`, `detail`, and `keycloak_subject` sentinels and assert none appears in `document.body`.

- [ ] **Step 2: Write the reissue label RED assertion**

In the existing superseded-recovery test:

```ts
const reissue = screen.getByRole("button", {
  name: "Issue a new temporary password",
});
expect(reissue).toHaveTextContent(/^Issue new password$/);
expect(reissue).toHaveStyle({ minHeight: "44px", maxWidth: "100%" });
```

- [ ] **Step 3: Run focused RED**

Run:

```bash
npm --prefix apps/web test -- src/setup/FirstAdministratorStep.test.tsx
```

Expected: failures for missing blocker copy, shared username/email guidance, old visible label, and missing width bound.

- [ ] **Step 4: Implement independent stable-code mappings**

Add before generic fallback:

```ts
if (error.code === "bootstrap_administrator_exists") {
  return {
    heading: "Administrator was not created",
    message:
      "An existing System Administrator assignment blocks public setup. Run the documented host release-administrator-blocker recovery, then try again.",
  };
}
if (error.code === "user_exists") {
  return {
    heading: "Administrator was not created",
    message:
      "The bound username belongs to an unrelated identity. Changing the username here cannot recover this claim. Ask a host identity administrator to resolve the collision.",
  };
}
if (error.code === "keycloak_email_exists") {
  return {
    heading: "Administrator was not created",
    message: "That email belongs to another identity. Keep the bound username and enter another email.",
  };
}
```

Do not consume `error.title`, `error.detail`, or provider fields.

- [ ] **Step 5: Implement the accessible/visible label split**

Use the existing button state and add only:

```tsx
aria-label={
  reissueRecovery === "retry" ? undefined : "Issue a new temporary password"
}
style={{ minHeight: 44, maxWidth: "100%" }}
```

The child text becomes:

```tsx
{reissueRecovery === "retry" ? "Retry issuing temporary password" : "Issue new password"}
```

- [ ] **Step 6: Run focused and neighboring GREEN**

```bash
npm --prefix apps/web test -- \
  src/setup/FirstAdministratorStep.test.tsx \
  src/SetupWizard.test.tsx \
  src/App.test.tsx \
  src/admin/CreateUserModal.test.tsx \
  src/admin/UsersAdmin.test.tsx
npm --prefix apps/web run test:browser -- --grep "first administrator setup"
npm --prefix apps/web run lint
npm --prefix apps/web run build
```

Expected: selected Vitest cases pass; focused Chromium is 1/1 with one worker/zero retries; lint/build exit 0.

- [ ] **Step 7: Commit the independently reviewable web correction**

```bash
git add apps/web/src/setup/FirstAdministratorStep.tsx \
  apps/web/src/setup/FirstAdministratorStep.test.tsx
git diff --cached --check
git commit -m "fix: clarify first-admin recovery guidance"
```

---

### Task 2: Fail-closed trusted remint rate-limit reset

**Files:**
- Modify: `apps/api/src/easysynq_api/cli/setup.py:28-66`
- Modify: `apps/api/tests/integration/test_setup.py:847-884`
- Modify: `apps/api/tests/integration/test_setup.py` near remint race tests
- Modify: `docs/adr/0005-provision-first-administrator-in-setup.md`
- Modify: `docs/debt/20260816024758-bootstrap-admission-identity-coupling.md`

**Interfaces:**
- Consumes: `get_settings().redis_url`, `services.setup.service._RL_KEY`, synchronous `redis.Redis.from_url`, the locked `SystemConfig` row, and `mint_secret() -> tuple[str, str]`.
- Produces: `_clear_bootstrap_failure_budget() -> None`; remint clears the key before commit and raises redacted `SystemExit` on reset failure.

- [ ] **Step 1: Extend the real remint recovery proof**

Before `mint_bootstrap()` in `test_reminted_secret_recovers_pending_claim_and_advanced_setup_refuses_remint`:

```python
async with aioredis.from_url(get_settings().redis_url, decode_responses=True) as client:
    await client.set(
        setup_service._RL_KEY,
        str(setup_service._RL_MAX),
        ex=setup_service._RL_WINDOW_SECONDS,
    )

replacement = mint_bootstrap()

async with aioredis.from_url(get_settings().redis_url, decode_responses=True) as client:
    assert await client.get(setup_service._RL_KEY) is None
```

Retain the immediate `_provision(app_client, replacement, username)` assertion at 200.

- [ ] **Step 2: Add the reset-failure rollback proof**

```python
async def test_remint_redis_reset_failure_rolls_back_replacement_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from easysynq_api.cli import setup as setup_cli

    await _reset_uninitialized()
    before = await _config()
    original_hash = before.bootstrap_secret_hash
    original_expiry = before.bootstrap_expires_at

    def unavailable() -> None:
        raise SystemExit("bootstrap failure counter could not be reset")

    monkeypatch.setattr(setup_cli, "_clear_bootstrap_failure_budget", unavailable)

    with pytest.raises(SystemExit, match="failure counter could not be reset"):
        setup_cli.mint_bootstrap()

    after = await _config()
    assert after.bootstrap_secret_hash == original_hash
    assert after.bootstrap_expires_at == original_expiry
```

- [ ] **Step 3: Run focused RED**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest \
  tests/integration/test_setup.py \
  -k "reminted_secret_recovers or remint_redis_reset_failure" -q
```

Expected: the Redis key survives and the helper is missing.

- [ ] **Step 4: Implement the synchronous reset helper**

Import `redis` and canonical `_RL_KEY`, then add:

```python
def _clear_bootstrap_failure_budget() -> None:
    try:
        with redis.Redis.from_url(get_settings().redis_url, decode_responses=True) as client:
            client.delete(_RL_KEY)
    except Exception:  # noqa: BLE001 - trusted recovery must redact Redis/provider detail
        raise SystemExit("bootstrap failure counter could not be reset") from None
```

- [ ] **Step 5: Call reset inside the locked uncommitted transaction**

```python
secret, stored_hash = mint_secret()
try:
    _clear_bootstrap_failure_budget()
except SystemExit:
    session.rollback()
    raise
cfg.bootstrap_secret_hash = stored_hash
cfg.bootstrap_expires_at = datetime.datetime.now(datetime.UTC) + datetime.timedelta(
    hours=ttl_hours
)
session.commit()
return secret
```

The singleton row lock remains held. Deletion failure rolls back. A later commit failure may refresh attempts for the old proof but cannot publish new authority.

- [ ] **Step 6: Run focused and affected GREEN**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest \
  tests/integration/test_setup.py \
  -k "remint or bootstrap_rate_limit" -q
UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest \
  tests/unit/test_setup.py \
  tests/unit/test_setup_administrator.py \
  tests/unit/test_deploy_configuration.py -q
```

- [ ] **Step 7: Amend ADR 0005**

Append `## 2026-08-17 amendment — trusted remint resets bootstrap admission failures` with Nygard `Context`, `Decision`, `Consequences`, `Alternatives`, and `Payoff trigger` sections. State:

- the old proof's Redis budget can outlive PostgreSQL rotation;
- host remint holds `system_config`, deletes the global key, then commits hash/expiry;
- deletion failure rolls back and prints no replacement proof;
- commit failure after deletion can restore attempts for the old proof but grants no authority;
- generation-scoped keys and post-commit best-effort deletion were rejected;
- payoff is one transactional admission/proof store or generation-scoped state with atomic rotation.

- [ ] **Step 8: Extend the existing debt record**

Append `## 2026-08-17 — trusted remint cross-store ordering` with the same intentional non-atomicity and payoff trigger. Do not create a second debt record.

- [ ] **Step 9: Run static, authority, and site-data gates**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run ruff format --check \
  src/easysynq_api/cli/setup.py tests/integration/test_setup.py
UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run ruff check \
  src/easysynq_api/cli/setup.py tests/integration/test_setup.py
UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run mypy src/easysynq_api/cli/setup.py
cd ../..
just authority-check
bash scripts/check-no-site-data.sh
git diff --check
```

- [ ] **Step 10: Commit the trusted-remint correction**

```bash
git add apps/api/src/easysynq_api/cli/setup.py \
  apps/api/tests/integration/test_setup.py \
  docs/adr/0005-provision-first-administrator-in-setup.md \
  docs/debt/20260816024758-bootstrap-admission-identity-coupling.md
git diff --cached --check
git commit -m "fix: reset bootstrap failures on trusted remint"
```

---

### Task 3: Final verification, evidence convergence, and GitHub resolution

**Files:**
- Verify: all Task 1–2 files
- Conditionally modify from fresh totals: `docs/current-status.md`
- Conditionally modify from fresh totals: `docs/slice-history.md`
- Conditionally modify from fresh totals: `.superpowers/sdd/2026-08-15-s-first-admin-provisioning/implementation-report.md`
- Update remotely: PR #466 and eight inline review threads

**Interfaces:**
- Consumes: Task 1–2 commits, fresh suite output, Actions logs, and unresolved thread IDs.
- Produces: clean pushed branch, truthful evidence, passing checks, evidence replies, and supported resolved threads.

- [ ] **Step 1: Run the full final Chromium cohort**

```bash
npm --prefix apps/web run test:browser
```

Expected: 40/40 Chromium, one worker, zero retries. Do not claim Firefox, WebKit, or assistive-technology sessions.

- [ ] **Step 2: Run fresh complete inventories that changed**

Use durable jobs and avoid overlapping Docker-heavy work:

```bash
cd apps/api
env UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run pytest tests/integration -m integration
cd ../..
npm --prefix apps/web test
```

Preserve exact passed/skipped/deselected and file/test totals. API unit, contract, migration, and CI topology did not change.

- [ ] **Step 3: Reconcile evidence only if totals differ**

Update the integration/Vitest totals and a narrow review-fix note in all three evidence homes from Step 2's exact output. Retain unchanged:

```text
baseline_commit = 1dcbc2bc12b14e11f037a657d44659412a7a39c0
API unit = 1,835 passed, 1 expected release-only skip
contract = 284/284
migration head = 0088_bootstrap_credential
next migration = 0089
contract hash = 5ab98c4a060563a8d1ea4fd2c57eba5a7a2923d69b52bd9ef623d6a528f98a58
CI topology = 11 jobs / 15 expanded checks
```

Do not change `docs/open-residuals.md`.

- [ ] **Step 4: Run final repository gates**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run ruff format --check .
UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run ruff check .
UV_CACHE_DIR=/tmp/easysynq-uv-cache uv run mypy src
cd ../..
npm --prefix apps/web run lint
npm --prefix apps/web run build
just contracts-check
just authority-check
bash scripts/check-no-site-data.sh
git diff --check origin/main...HEAD
```

- [ ] **Step 5: Remove only validated browser artifacts**

Inspect exact worktree paths `.playwright-dist`, `playwright-report`, and `test-results`, refuse symlinks, and delete only those generated directories. Confirm root `.env` is absent without reading it. Preserve primary `.superdesign/` and unrelated prunable `/tmp` worktree records.

- [ ] **Step 6: Commit evidence if Step 3 changed files**

```bash
git add docs/current-status.md docs/slice-history.md \
  .superpowers/sdd/2026-08-15-s-first-admin-provisioning/implementation-report.md
git diff --cached --check
git commit -m "docs: record first-admin PR review fixes"
```

Skip when no evidence file changed.

- [ ] **Step 7: Self-review and push without force**

```bash
git status --short
git log --oneline origin/main..HEAD
git diff --stat origin/main...HEAD
git diff --check origin/main...HEAD
git push origin codex/first-admin-bootstrap
```

Do not merge.

- [ ] **Step 8: Reply inline to five already-fixed threads**

Cite exact current commits/tests for: shared-lock administrator uniqueness, definitive claim release, credential receipt/generation binding, claimed-profile reconciliation, and reminted-secret UI recovery. Do not claim new code for historical fixes.

- [ ] **Step 9: Reply inline to three newly fixed threads**

Cite the pushed commits and focused evidence for: blocker host guidance, remint reset/rollback, and separate username/email guidance.

- [ ] **Step 10: Resolve only supported threads**

Resolve each thread only after GitHub exposes the cited commit. Leave any mismatched or newly disputed thread open.

- [ ] **Step 11: Inspect the new Actions run**

```bash
gh pr checks 466 --repo CoJoA13/EasySynQ --watch
```

Expected: all required checks pass, including `web browser (Chromium)` and `web`. On failure, retrieve exact logs and return to systematic debugging; never retry blindly.

---

## Plan Self-Review

- Spec coverage: Task 1 owns responsive control and safe copy; Task 2 owns remint, ADR, and debt; Task 3 owns fresh proof and GitHub convergence.
- Placeholder scan: no deferred implementation markers or unspecified validation steps remain.
- Type consistency: `_clear_bootstrap_failure_budget() -> None`, `mint_bootstrap(...) -> str`, `PresentedError`, stable codes, Redis key, labels, and evidence paths match across tasks.
- Scope: no OpenAPI, migration, dependency, permission, residual, or browser-engine expansion.
