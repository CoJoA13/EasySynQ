# Issue #570 Testcontainers Imports Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. The owner authorized separate independent reviews of the plan, implementation, and final changes before a GitHub PR; the coordinator dispatches those reviews.

**Goal:** Remove the three deprecated integration fixture imports and prove the actual PostgreSQL, MinIO, Redis fixtures and all mandatory runtime cases run without their deprecation warnings.

**Architecture:** Import the same container classes through Testcontainers' community modules. Include the two PostgreSQL imports reached by mandatory runtime acceptance: the legacy shim emits a warning even when the community module was imported first, so the three conftest edits alone cannot satisfy closure. Preserve fixture scopes, image references, connection handling, application behavior, and runtime assertions.

**Tech Stack:** Python 3.12, frozen `apps/api/uv.lock` (Testcontainers 4.15.0 at the inspected base), pytest, Docker, Ruff, mypy.

**Spec:** `docs/open-residuals.md#res-testcontainers-import-deprecations`; [GitHub issue #570](https://github.com/CoJoA13/EasySynQ/issues/570). Base: `ddfbf400d1c3025515682ccf56e603570f1e8b6c`.

## Global Constraints

- "Do not suppress the warnings or treat an unaffected subset as closure evidence."
- Work only in `.worktrees/issue-570` on `codex/issue-570-testcontainers-imports`; use GitHub only.
- Serialize conftest ownership with #598 and verify against the final API lock after dependency PR #624.
- The coordinator authorized the two additional required-path imports; unrelated migration, orchestrator, and history imports remain outside this change.
- The coordinator runs finite dependency, build, and test workloads that may exceed 60 seconds through Codex Process Jobs. This worker supplies exact commands and does not launch or monitor those workloads.
- Do not close the residual, commit, push, or open a PR until the coordinator's required review stage authorizes that step.

## Review Focus

- Eager PostgreSQL import: the warning policy must fail on the old import during collection.
- Lazy MinIO and Redis imports: tests must actually start both fixtures, rather than stop at collection.
- Container compatibility: the same classes, images, lifecycle, and connection settings must remain in use.
- Mandatory acceptance: all eleven currently required cases must pass without skips or weakened limits.
- Runtime cleanup and shared execution: retain owned-resource cleanup and coordinate heavy Docker runs with the other workers.

## Environment and observed baseline

At plan inspection the worktree was clean and had no API virtual environment; the coordinator owns subsequent setup. Docker 29.8.2 is reachable; Python 3.12.14 is already installed, while `/usr/bin/python3` is 3.14.4. `uv`'s default cache is outside writable roots; the coordinator uses the shared ignored cache at `$ISSUE_570_REPO/.superpowers/tool-cache/uv`.

For execution, replace the quoted placeholders below with the shared GitHub checkout directory
(the parent of `.worktrees`) and the installed Python 3.12 executable. These task-specific variables
keep host paths out of durable documentation; exact executed paths remain in ignored evidence.

```bash
ISSUE_570_REPO="<repo>"
ISSUE_570_PYTHON="<python-3.12>"
export UV_CACHE_DIR="$ISSUE_570_REPO/.superpowers/tool-cache/uv"
export UV_PYTHON_DOWNLOADS=never
export UV_PYTHON="$ISSUE_570_PYTHON"
```

A read-only inspection of the legacy `audit-lineage-reader` environment found Testcontainers 4.15.0. Importing each community module and then its legacy module established class identity and emitted all three exact `DeprecationWarning` messages. This is package-mechanism evidence, not actual fixture or closure evidence; never execute tests against that environment's editable application source.

`./scripts/doctor.sh contributor` exited 1: API/web/contracts dependencies and `.env` are absent; native Gitleaks and SELinux verification are missing; project ports 80/443/9000 have listeners. It also failed its Python discovery/default uv policy. Use the installed 3.12 interpreter and writable cache below. Tests use disposable random-port containers and need no deployment `.env`; do not alter listeners or hydrate unrelated frontend dependencies for this patch. Record the doctor result rather than claim full contributor readiness.

`RES-AUDIT-RUNTIME-ACCEPTANCE-FAILURE` records historical runtime failures. The known negative-TLS `BrokenPipeError` fixture fix from GitHub PR #619 is already merged on this base; that does not establish the cause of the historical CI failures. If an unrelated runtime failure occurs, preserve the diagnostic result and report closure blocked. Do not repair it, relax assertions, or count a retry as closure.

## Task 1: Migrate imports and establish closure evidence

**Files:**
- Modify: `apps/api/tests/integration/conftest.py:19`, `:86`, `:144`.
- Modify: `apps/api/tests/integration/audit_external_runtime_acceptance.py:31`.
- Modify: `apps/api/tests/integration/test_audit_external_trust.py:24`.
- Existing tests: `tests/integration/test_blob_verify.py::test_clean_pass_stamps_verified_at_and_writes_clean_row` and `tests/integration/test_notification_stream.py::test_sweep_publishes_nudge_for_recent_unread`.
- Existing mandatory runner: `scripts/run-audit-external-acceptance.py`.
- Closure evidence, coordinated with other documentation writers: append a dated entry to `docs/slice-history.md`; remove only `RES-TESTCONTAINERS-IMPORT-DEPRECATIONS` from `docs/open-residuals.md` after complete proof.

**Interfaces:** Consumes the existing `_pg()`, `_minio()`, `_redis()`, `_pg_container()`, and `app_under_test(...)` fixtures. Produces identical fixture interfaces backed by community-module imports; no application interface changes.

- [x] **Step 1: Hydrate the assigned worktree from the frozen API lock.** Coordinator-run command, cwd `apps/api`:

```bash
env UV_CACHE_DIR="$ISSUE_570_REPO/.superpowers/tool-cache/uv" UV_PYTHON_DOWNLOADS=never uv sync --frozen --python "$ISSUE_570_PYTHON"
```

Expected: local `.venv` created, lock unchanged, imports available. Registry/download failure is an environment blocker, not a failing behavior test. Do not install into or mutate the legacy environment.

- [x] **Step 2: Capture actual baseline and RED before editing.** Coordinator-run commands, cwd `apps/api`, with `UV_CACHE_DIR="$ISSUE_570_REPO/.superpowers/tool-cache/uv"` and `UV_PYTHON_DOWNLOADS=never`:

```bash
uv run --frozen pytest tests/integration/test_blob_verify.py::test_clean_pass_stamps_verified_at_and_writes_clean_row tests/integration/test_notification_stream.py::test_sweep_publishes_nudge_for_recent_unread -v -W default::DeprecationWarning
uv run --frozen pytest tests/integration/test_blob_verify.py::test_clean_pass_stamps_verified_at_and_writes_clean_row tests/integration/test_notification_stream.py::test_sweep_publishes_nudge_for_recent_unread -v -W 'error:testcontainers.postgres is deprecated:DeprecationWarning' -W 'error:testcontainers.minio is deprecated:DeprecationWarning' -W 'error:testcontainers.redis is deprecated:DeprecationWarning'
```

The locked pytest 9.1.1 parses CLI warning filters with `escape=True`; the three literal message filters above are intentional. Do not replace them with a grouped regular expression. Expected baseline: both tests execute against real PostgreSQL/MinIO/Redis and report the three legacy-import warnings. Expected RED: nonzero exit caused by `testcontainers.postgres is deprecated` in conftest collection. Record actual output and exit statuses; missing dependencies or inaccessible images do not establish RED.

- [x] **Step 3: Make the five import substitutions.** Use `testcontainers.community.postgres` for all three in-scope `PostgresContainer` imports, `testcontainers.community.minio` for `MinioContainer`, and `testcontainers.community.redis` for `RedisContainer`. Preserve lazy imports, fixtures, and class names. Run Ruff's import-order fix only on these three files if required; inspect every resulting line. Do not add a source-text test that merely mirrors the import strings.

- [x] **Step 4: Prove the affected real fixtures.** Rerun the RED command unchanged. Expected: two passes, no skips/errors, no targeted warnings. The blob test migrates real PostgreSQL, stores and rehashes a real MinIO blob, and checks persisted verification state; the notification test writes database records and proves actual Redis pubsub delivery. Together they force all three assigned fixtures, including both lazy imports.

- [x] **Step 5: Prove the current complete mandatory runtime path.** Coordinator-run command, cwd repository root:

```bash
env UV_CACHE_DIR="$ISSUE_570_REPO/.superpowers/tool-cache/uv" UV_PYTHON_DOWNLOADS=never UV_PYTHON="$ISSUE_570_PYTHON" UV_FROZEN=true PYTHONWARNINGS='error:testcontainers.postgres is deprecated:DeprecationWarning,error:testcontainers.minio is deprecated:DeprecationWarning,error:testcontainers.redis is deprecated:DeprecationWarning' python3 scripts/run-audit-external-acceptance.py
```

Expected: exit 0, `runtime_tests=11`, `mandatory_tests=11`, `runtime_acceptance=passed`, and no cleanup failure. The warning policy propagates to the child pytest process and makes any of the three deprecated import messages fatal even though the runner redacts successful child output. Require the built image and complete eleven-case JUnit validation; direct pytest or selected runtime cases cannot substitute. Record immutable image ID, input digests from the runner output, statuses, and elapsed time. The runner owns cleanup; never delete another workstream's resources.

- [x] **Step 6: Run proportionate API and handoff gates.** Coordinator-run commands, cwd `apps/api`, with the same cache/download settings:

```bash
uv run --frozen ruff check .
uv run --frozen ruff format --check --diff .
uv run --frozen mypy src
```

Then from repository root run `git diff --check` and `bash scripts/check-no-site-data.sh`. Existing affected integration tests and mandatory acceptance are the meaningful regression gates; this import-only patch does not require another local full unit/frontend/contract/migration suite. The final GitHub PR still requires its normal CI gate. Inspect the diff and verify `apps/api/uv.lock`, fixtures, images, warning configuration, and runtime limits were not changed.

- [ ] **Step 7: Record closure only after complete passing evidence and required review.** Supply evidence to the coordinator for serialized ledger/history updates, including the baseline and RED, two real fixture passes, eleven mandatory cases, targeted warning enforcement, gates, exact tested lock/head, and every limitation. Run `./scripts/check-repo-authority.sh` after the documentation updates. If the final API lock changes, repeat fixture and mandatory runtime proof before removing the residual. Obtain independent implementation and final review through the coordinator; prepare the GitHub PR only after those gates.


**Execution evidence (2026-10-02):** Steps 1–6 are complete. The coordinator inspected the hydrated
frozen environment, two baseline fixture passes with all three warnings, warning-as-error RED
(exit 4), two fixture GREEN passes, eleven mandatory built-image cases (660.045 seconds), and
passing API Ruff/format/mypy, site-data, authority and whitespace gates. The tested five-import patch
and API lock match the recorded evidence. The dated candidate history entry contains the immutable
image and input identities.

**Step 7 partially complete:** The coordinator authorized the bounded candidate ledger removal and
dated history entry after complete runtime proof. Post-documentation authority/site-data/hook checks,
independent final review and GitHub PR preparation remain pending. No shipped or merged outcome is
claimed. A changed final API lock still requires fresh fixture and mandatory runtime evidence.
