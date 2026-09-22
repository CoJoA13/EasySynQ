"""Semantic regression for the CI gate: .github/workflows/ci.yml and its path policy (R86).

The structural sibling is scripts/tests/test-ci-hardening.sh (Bash + grep, runs before
dependencies exist). This file parses the YAML and pins what a text match cannot: the exact
commands each suite runs, which job carries the built-image proof, the `gate` job's coverage of
every other job, and the path policy that decides what a pull request owes — including a walk over
`git ls-files` proving no tracked non-docs path can fall into the docs-only lane.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[4]
_WORKFLOW = _ROOT / ".github" / "workflows" / "ci.yml"
_PATHS = _ROOT / ".github" / "ci-paths.yml"
_FILTER = _ROOT / "scripts" / "ci-changed-paths.py"

_ALL_JOBS = {
    "changes",
    "contracts",
    "compose-images-lock",
    "workflow-and-secrets",
    "dependency-review",
    "api",
    "migrations",
    "security",
    "docs-tests",
    "contract-responses",
    "integration-shards",
    "web-shards",
    "web-browser",
    "release-gate",
    "gate",
}


def _workflow() -> dict[str, Any]:
    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))


def _jobs() -> dict[str, Any]:
    return _workflow()["jobs"]


def _paths() -> dict[str, list[str]]:
    return yaml.safe_load(_PATHS.read_text(encoding="utf-8"))


def _filter_module() -> Any:
    spec = importlib.util.spec_from_file_location("ci_changed_paths", _FILTER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _step(job: dict[str, Any], name: str) -> tuple[int, dict[str, Any]]:
    matches = [(index, step) for index, step in enumerate(job["steps"]) if step.get("name") == name]
    assert len(matches) == 1, f"expected exactly one {name!r} step, got {len(matches)}"
    return matches[0]


def _assert_hard_fail(job: dict[str, Any]) -> None:
    assert "continue-on-error" not in job
    for step in job.get("steps", []):
        assert "continue-on-error" not in step, step.get("name") or step.get("uses")
        assert "|| true" not in step.get("run", "")


# --- hard-fail discipline and exact collection ---------------------------------------------------


def test_ci_workflow_preserves_complete_hard_fail_gates() -> None:
    jobs = _jobs()
    assert set(jobs) == _ALL_JOBS
    for job in jobs.values():
        _assert_hard_fail(job)

    web_shards = jobs["web-shards"]
    assert web_shards["name"] == "web tests (${{ matrix.shard }}/2)"
    assert web_shards["strategy"] == {"fail-fast": False, "matrix": {"shard": [1, 2]}}
    vitest_index, vitest = _step(web_shards, "Vitest shard ${{ matrix.shard }}/2")
    assert vitest == {
        "name": "Vitest shard ${{ matrix.shard }}/2",
        "working-directory": "apps/web",
        "run": "npm test -- --shard=${{ matrix.shard }}/2",
    }
    static_index, static = _step(web_shards, "lint and build")
    assert static == {
        "name": "lint and build",
        "if": "${{ !cancelled() && matrix.shard == 2 }}",
        "working-directory": "apps/web",
        "run": "npm run lint && npm run build",
    }
    assert vitest_index < static_index

    web_browser = jobs["web-browser"]
    assert web_browser["name"] == "web browser (Chromium)"
    for step in web_browser["steps"]:
        command = step.get("run", "")
        assert "--changed" not in command
        assert "--retries" not in command
    _, diagnostics = _step(web_browser, "upload browser diagnostics")
    assert diagnostics["if"] == "${{ failure() }}"

    expected_commands = {
        ("api", "unit tests"): "uv run pytest tests/unit -m unit",
        ("api", "mandatory runtime acceptance (built image)"): (
            "python3 scripts/run-audit-external-acceptance.py"
        ),
        (
            "migrations",
            "populated migration coherence regressions",
        ): "uv run pytest tests/migration",
        ("migrations", "autogenerate drift check (models == migrations)"): "uv run alembic check",
        (
            "contract-responses",
            "validate authenticated operational responses (disposable testcontainers only)",
        ): (
            "uv run pytest tests/integration/test_contract_response_schemas.py "
            "-m contract --tb=short"
        ),
        (
            "integration-shards",
            "integration tests (shard ${{ matrix.group }}/4, "
            "testcontainers spin their own Postgres)",
        ): (
            "uv run pytest tests/integration -m integration --splits 4 "
            "--group ${{ matrix.group }} --durations-path .test_durations "
            "--store-durations --clean-durations"
        ),
        ("contracts", "generated contract lock"): "bash scripts/gen-contracts.sh --check",
        ("contracts", "CI workflow contract"): (
            "bash scripts/tests/test-ci-hardening.sh\n"
            "bash scripts/tests/test-check-compose-images-lock.sh\n"
        ),
        ("security", "built-image fixed-version threshold (gated)"): (
            "bash scripts/check-built-image-security.sh"
        ),
    }
    for (job_name, step_name), command in expected_commands.items():
        _, step = _step(jobs[job_name], step_name)
        assert "if" not in step, (job_name, step_name)
        assert step["run"] == command, (job_name, step_name)

    assert jobs["integration-shards"]["strategy"]["matrix"] == {"group": [1, 2, 3, 4]}

    package = json.loads((_ROOT / "apps" / "web" / "package.json").read_text(encoding="utf-8"))
    assert package["scripts"]["test"] == "vitest run"
    assert package["scripts"]["build"] == "tsc --noEmit && vite build"
    vitest_config = (_ROOT / "apps" / "web" / "vite.config.ts").read_text(encoding="utf-8")
    assert 'pool: "forks"' in vitest_config
    assert "maxWorkers: 1" in vitest_config
    assert "isolate: false" not in vitest_config


def test_the_image_runtime_proof_is_enabled_in_a_job_that_can_fail_a_merge() -> None:
    """The api job must set ``EASYSYNQ_IMAGE_PROOF``, and it must be the api job.

    ``test_the_built_api_image_is_unprivileged_and_starts_offline`` is ``skipif``-gated on this
    variable. It was written, was correct, and had NEVER run, because nothing set it — the same
    inertness the audit had already fixed once for ``EASYSYNQ_RELEASE``. Dependabot #448 was CLEAN
    on every check while shipping an image whose CMD dies on start. Pinned in `api`, which the
    `gate` job owes whenever code changes; `security` also builds the image but is a different
    gate with a different purpose.
    """
    jobs = _jobs()
    _, unit_step = _step(jobs["api"], "unit tests")
    assert unit_step.get("env", {}).get("EASYSYNQ_IMAGE_PROOF") == "1"
    for job_name, job in jobs.items():
        if job_name == "api":
            continue
        for step in job.get("steps", []):
            assert "EASYSYNQ_IMAGE_PROOF" not in (step.get("env") or {}), job_name


def test_security_gates_both_built_images_through_the_validating_runner() -> None:
    """A base scan or a report-only action must not substitute for built-artifact evidence."""
    security = _jobs()["security"]
    runs = [step.get("run", "") for step in security["steps"]]
    uses = [step.get("uses", "") for step in security["steps"]]
    assert not any("trivy-action" in used for used in uses)
    assert any("docker build -f apps/api/Dockerfile -t easysynq-api:scan ." in run for run in runs)
    assert any(
        "docker build -f apps/web/Dockerfile -t easysynq-web:scan apps/web" in run for run in runs
    )
    assert not any("grep -E '^FROM '" in run for run in runs)
    assert "bash scripts/check-built-image-security.sh" in runs
    assert "bash scripts/tests/test-built-image-security.sh" in runs
    assert runs.index("bash scripts/tests/test-built-image-security.sh") < runs.index(
        "bash scripts/check-built-image-security.sh"
    )
    assert "bash scripts/tests/test-pip-audit-runner.sh" in runs
    assert "bash scripts/run-pip-audit.sh" in runs
    assert "node scripts/check-npm-audit.mjs" in runs
    assert not any("npm audit " in run for run in runs)


# --- the one required check ----------------------------------------------------------------------


def test_gate_needs_every_other_job_and_judges_each_by_what_the_run_owed() -> None:
    """GitHub cannot require a path-conditional job (a skipped required check blocks merging
    forever), so `gate` is the single required check and must cover everything."""
    jobs = _jobs()
    gate = jobs["gate"]
    assert set(gate["needs"]) == _ALL_JOBS - {"gate"}
    assert gate["if"] == "${{ always() }}"
    script = gate["steps"][0]["run"]
    for job_name in _ALL_JOBS - {"gate"}:
        assert f"owed {job_name} " in script, job_name
    # A skipped job is acceptable ONLY when the run did not owe it; a failure never is.
    assert '"$expected" = "true"' in script
    assert 'if [ "$result" = "skipped" ]; then' in script
    assert 'echo "::error::$job reported $result"; failed=1' in script
    assert 'exit "$failed"' in script


@pytest.mark.parametrize(
    ("job_name", "condition"),
    [
        ("api", "needs.changes.outputs.code == 'true'"),
        (
            "migrations",
            "needs.changes.outputs.code == 'true' || needs.changes.outputs.main_push == 'true'",
        ),
        (
            "security",
            "needs.changes.outputs.code == 'true' || needs.changes.outputs.main_push == 'true'",
        ),
        ("docs-tests", "needs.changes.outputs.docs_only == 'true'"),
        ("contract-responses", "needs.changes.outputs.api_suites == 'true'"),
        ("integration-shards", "needs.changes.outputs.api_suites == 'true'"),
        ("web-shards", "needs.changes.outputs.web_suites == 'true'"),
        ("web-browser", "needs.changes.outputs.web_suites == 'true'"),
        ("release-gate", "startsWith(github.ref, 'refs/tags/v')"),
        ("dependency-review", "github.event_name == 'pull_request'"),
    ],
)
def test_each_conditional_job_keys_off_the_filter_and_gate_expects_the_same(
    job_name: str, condition: str
) -> None:
    jobs = _jobs()
    job = jobs[job_name]
    assert job["if"] == condition
    if job_name not in {"release-gate", "dependency-review"}:
        assert job["needs"] == "changes"
    # The gate must expect the job exactly when its own condition says it runs.
    script = jobs["gate"]["steps"][0]["run"]
    expectation = {
        "api": '"$CODE"',
        "migrations": '"$backstops"',
        "security": '"$backstops"',
        "docs-tests": '"$DOCS_ONLY"',
        "contract-responses": '"$API_SUITES"',
        "integration-shards": '"$API_SUITES"',
        "web-shards": '"$WEB_SUITES"',
        "web-browser": '"$WEB_SUITES"',
        "release-gate": '"$IS_TAG"',
        "dependency-review": '"$IS_PR"',
    }[job_name]
    assert any(
        line.split()[:2] == ["owed", job_name] and line.rstrip().endswith(expectation)
        for line in script.splitlines()
    ), (job_name, expectation)


@pytest.mark.parametrize(
    "job_name", ["contracts", "compose-images-lock", "workflow-and-secrets", "changes"]
)
def test_guards_run_on_every_run(job_name: str) -> None:
    job = _jobs()[job_name]
    assert "if" not in job
    assert "needs" not in job


# --- triggers and cancellation -------------------------------------------------------------------


def test_only_pull_request_runs_are_superseded_and_main_is_never_cancelled() -> None:
    workflow = _workflow()
    on = workflow[True] if True in workflow else workflow["on"]
    assert on["push"] == {"branches": ["main"], "tags": ["v*"]}
    assert on["pull_request"] is None
    assert on["workflow_dispatch"] is None
    # The weekly run re-scans an unchanged main for new advisories; it owes every suite.
    assert on["schedule"] == [{"cron": "17 6 * * 1"}]
    assert workflow["concurrency"] == {
        "group": "ci-${{ github.event_name == 'pull_request' && github.ref || github.run_id }}",
        "cancel-in-progress": "${{ github.event_name == 'pull_request' }}",
    }
    assert workflow["permissions"] == {"contents": "read"}


# --- path policy ---------------------------------------------------------------------------------


def _is_docs(path: str) -> bool:
    return path.startswith("docs/") or ("/" not in path and path.endswith(".md"))


def _covered(path: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if pattern.endswith("/**"):
            if path.startswith(pattern[:-2]):
                return True
        else:
            assert "*" not in pattern, f"only <dir>/** or exact root files: {pattern}"
            if path == pattern:
                return True
    return False


def test_every_non_docs_path_is_code_for_the_ci_rules() -> None:
    """A tracked file outside docs/ and root *.md must never fall into the docs-only lane."""
    git = shutil.which("git")
    assert git is not None
    tracked = subprocess.run(  # noqa: S603 - resolved binary, fixed arguments
        [git, "ls-files"], cwd=_ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    assert len(tracked) > 1000
    code = _paths()["code"]
    uncovered = [p for p in tracked if not _is_docs(p) and not _covered(p, code)]
    assert uncovered == []
    assert not any(_is_docs(p) and _covered(p, code) for p in tracked)


def test_the_suite_path_lists_name_what_the_suites_read() -> None:
    lists = _paths()
    for key in ("code", "api_suites", "web_suites"):
        assert ".github/**" in lists[key], "a workflow edit must exercise every suite"
    assert {
        "apps/api/**",
        "migrations/**",
        "infra/**",
        "packages/contracts/**",
        "scripts/**",
    } <= set(lists["api_suites"])
    assert {"apps/web/**", "packages/contracts/**"} <= set(lists["web_suites"])


def test_the_filter_job_runs_the_tracked_decision_script() -> None:
    changes = _jobs()["changes"]
    checkout = changes["steps"][0]
    assert checkout["uses"].startswith("actions/checkout@")
    assert checkout["with"] == {"persist-credentials": False, "fetch-depth": 0}
    _, decide = _step(changes, "decide which suites this run owes")
    assert decide["run"] == "python3 scripts/ci-changed-paths.py"
    assert decide["env"] == {
        "EVENT": "${{ github.event_name }}",
        "BASE_REF": "${{ github.event.pull_request.base.ref }}",
        "REF": "${{ github.ref }}",
    }
    for output in ("code", "api_suites", "web_suites", "docs_only", "full", "main_push"):
        assert changes["outputs"][output] == f"${{{{ steps.filter.outputs.{output} }}}}"


@pytest.mark.parametrize(
    ("event", "ref", "files", "expected"),
    [
        (
            "pull_request",
            "refs/pull/1/merge",
            ["docs/x.md", "README.md"],
            {"code": False, "docs_only": True, "api_suites": False, "web_suites": False},
        ),
        (
            "pull_request",
            "refs/pull/1/merge",
            ["apps/web/src/a.ts"],
            {"code": True, "docs_only": False, "api_suites": False, "web_suites": True},
        ),
        (
            "pull_request",
            "refs/pull/1/merge",
            ["apps/api/src/x.py"],
            {"code": True, "docs_only": False, "api_suites": True, "web_suites": False},
        ),
        (
            "pull_request",
            "refs/pull/1/merge",
            [".github/workflows/ci.yml"],
            {"code": True, "docs_only": False, "api_suites": True, "web_suites": True},
        ),
        # An unlisted new file counts as code at run time too — belt and braces over the walk above.
        (
            "pull_request",
            "refs/pull/1/merge",
            ["brand-new-top-level.txt"],
            {"code": True, "docs_only": False, "api_suites": False, "web_suites": False},
        ),
        # Docs plus code is a code change, never the docs lane.
        (
            "pull_request",
            "refs/pull/1/merge",
            ["docs/x.md", "scripts/x.sh"],
            {"code": True, "docs_only": False, "api_suites": True, "web_suites": False},
        ),
        ("push", "refs/heads/main", None, {"code": False, "main_push": True, "full": False}),
        ("push", "refs/tags/v1.2.3", None, {"code": True, "main_push": False, "full": True}),
        ("workflow_dispatch", "refs/heads/main", None, {"code": True, "full": True}),
        ("schedule", "refs/heads/main", None, {"code": True, "full": True, "main_push": False}),
    ],
)
def test_the_decision_script_owes_the_right_suites(
    event: str, ref: str, files: list[str] | None, expected: dict[str, bool]
) -> None:
    module = _filter_module()
    flags = module.decide(event, ref, "main", files=files)
    for key, value in expected.items():
        assert flags[key] is value, (key, flags)


def test_the_decision_script_refuses_a_lists_file_it_cannot_parse_exactly(
    tmp_path: Path,
) -> None:
    """The lists file uses a tiny YAML subset on purpose; anything richer must fail loudly
    rather than silently select nothing."""
    module = _filter_module()
    bad = tmp_path / "ci-paths.yml"
    bad.write_text("code:\n  - apps/**\napi_suites: [a]\nweb_suites:\n  - x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported line"):
        module.read_lists(bad)


# --- the docs lane selection ---------------------------------------------------------------------


def test_the_docs_lane_selection_keeps_its_documentation_pinning_tests() -> None:
    """Narrowing DOCS_TEST_PATTERN (e.g. "balancing" its quotes) would silently drop these."""
    import re

    docs = _jobs()["docs-tests"]
    pattern = re.compile(docs["env"]["DOCS_TEST_PATTERN"])
    unit = _ROOT / "apps" / "api" / "tests" / "unit"
    selected = {
        path.name
        for path in unit.glob("test_*.py")
        if any(pattern.search(line) for line in path.read_text(encoding="utf-8").splitlines())
    }
    assert {
        "test_recovery_claims_content.py",
        "test_identity_onboarding_contract.py",
        "test_first_admin_contract.py",
        # Selected only by the quote-anchored alternatives (`"docs"` or `\.md"`).
        "test_upload_identity_rollback_runbook.py",
    } <= selected
    script = docs["steps"][-1]["run"]
    assert 'grep -rlE "$DOCS_TEST_PATTERN" tests/unit --include="test_*.py"' in script
    assert 'test -n "$files"' in script
    assert "uv run pytest $files -m unit" in script


def test_every_job_that_runs_unit_tests_materializes_the_env_file_first() -> None:
    """compose.yml declares `env_file: ../../.env`, so any unit test that renders the Compose files
    (the deploy-configuration tests, which the docs lane selects by content) fails on a runner
    without the ignored repo-root copy. The api job materializes it before its Compose validation;
    the docs lane inherited that on GitLab and lost it in the port — the first live docs-only run
    (PR #555) caught it. Pin it for every job that runs the unit tree or a content selection of it;
    release-gate runs one named file (the images-lock pin) that renders nothing and is excluded."""
    jobs = _jobs()
    running_unit = {
        name
        for name, job in jobs.items()
        if any(
            "uv run pytest" in step.get("run", "")
            and ("pytest tests/unit " in step["run"] or "pytest $files" in step["run"])
            for step in job["steps"]
        )
    }
    assert running_unit == {"api", "docs-tests"}
    release_steps = jobs["release-gate"]["steps"]
    assert "test_images_lock_pinned.py" in release_steps[-1]["run"]
    for name in sorted(running_unit):
        runs = [step.get("run", "") for step in jobs[name]["steps"]]
        materialize = [i for i, run in enumerate(runs) if "cp .env.example .env" in run]
        pytest_steps = [i for i, run in enumerate(runs) if "uv run pytest" in run]
        assert materialize, f"{name} never materializes .env"
        assert materialize[0] < pytest_steps[0], f"{name} materializes .env after its unit tests"


def test_the_decision_script_sees_both_sides_of_a_rename(tmp_path: Path) -> None:
    """`git diff --name-only` reports only a rename's destination, so moving code into docs/ would
    read as docs-only while production code was removed. Both paths must take part."""
    git = shutil.which("git")
    assert git is not None
    repo = tmp_path / "repo"
    repo.mkdir()

    def run(*args: str) -> None:
        subprocess.run(  # noqa: S603 - resolved binary, fixed arguments in a temp repo
            [git, *args],
            cwd=repo,
            check=True,
            capture_output=True,
            env={
                "GIT_AUTHOR_NAME": "t",
                "GIT_AUTHOR_EMAIL": "t@example.test",
                "GIT_COMMITTER_NAME": "t",
                "GIT_COMMITTER_EMAIL": "t@example.test",
                "HOME": str(tmp_path),
                "PATH": "/usr/bin:/bin",
            },
        )

    run("init", "-q", "-b", "main")
    (repo / "apps" / "api").mkdir(parents=True)
    (repo / "apps" / "api" / "x.py").write_text("print('x')\n" * 20, encoding="utf-8")
    run("add", ".")
    run("commit", "-q", "-m", "base")
    (repo / "docs").mkdir()
    run("mv", "apps/api/x.py", "docs/x.py")
    run("commit", "-q", "-m", "move code into docs")

    module = _filter_module()
    names = set(module.diff_names("HEAD~1", "HEAD", cwd=repo))
    assert names == {"apps/api/x.py", "docs/x.py"}
    flags = module.decide("pull_request", "refs/pull/1/merge", "main", files=sorted(names))
    assert flags["code"] is True and flags["docs_only"] is False


def _git_runner(tmp_path: Path) -> Any:
    git = shutil.which("git")
    assert git is not None

    def run(cwd: Path, *args: str) -> str:
        return subprocess.run(  # noqa: S603 - resolved binary, fixed arguments in a temp repo
            [git, *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            env={
                "GIT_AUTHOR_NAME": "t",
                "GIT_AUTHOR_EMAIL": "t@example.test",
                "GIT_COMMITTER_NAME": "t",
                "GIT_COMMITTER_EMAIL": "t@example.test",
                "HOME": str(tmp_path),
                "PATH": "/usr/bin:/bin",
            },
        ).stdout

    return run


def test_changed_paths_survive_a_base_that_moves_after_checkout(tmp_path: Path) -> None:
    """The 2026-09-22 shallow-fetch race: `main` advanced between the runner's checkout and the
    `changes` step. The old `git fetch --depth=1 origin main` pulled the new tip as a shallow commit
    with no reachable parent, `origin/main...HEAD` had no merge base, and `changes` exited 128.
    The selection must come from the merge commit's first parent: what the run actually tested."""
    run = _git_runner(tmp_path)
    origin, seed, runner = tmp_path / "origin.git", tmp_path / "seed", tmp_path / "runner"
    run(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    run(tmp_path, "clone", "-q", origin.as_uri(), str(seed))
    (seed / "apps" / "api").mkdir(parents=True)
    (seed / "apps" / "api" / "x.py").write_text("x = 1\n", encoding="utf-8")
    run(seed, "add", ".")
    run(seed, "commit", "-q", "-m", "base")
    run(seed, "push", "-q", "origin", "HEAD:main")

    # The runner: a full clone at the base, a PR commit, and GitHub's merge ref (base first).
    run(tmp_path, "clone", "-q", origin.as_uri(), str(runner))
    run(runner, "switch", "-q", "-c", "pr")
    (runner / "apps" / "web").mkdir(parents=True)
    (runner / "apps" / "web" / "a.ts").write_text("export {};\n", encoding="utf-8")
    run(runner, "add", ".")
    run(runner, "commit", "-q", "-m", "pr change")
    run(runner, "switch", "-q", "--detach", "origin/main")
    run(runner, "merge", "-q", "--no-ff", "-m", "Merge pr into main", "pr")

    # main moves on after the checkout, touching a path the PR never did.
    (seed / "docs").mkdir()
    (seed / "docs" / "later.md").write_text("later\n", encoding="utf-8")
    run(seed, "add", ".")
    run(seed, "commit", "-q", "-m", "main moved")
    run(seed, "push", "-q", "origin", "HEAD:main")

    module = _filter_module()
    assert module.changed_files("main", cwd=runner) == ["apps/web/a.ts"]


def test_changed_paths_refuse_a_checkout_that_is_not_the_merge_commit(tmp_path: Path) -> None:
    """Without the merge ref there is no tested base to diff from; guessing one could select the
    docs lane for a code change, so the decision fails closed instead."""
    run = _git_runner(tmp_path)
    repo = tmp_path / "repo"
    run(tmp_path, "init", "-q", "-b", "main", str(repo))
    (repo / "x.py").write_text("x = 1\n", encoding="utf-8")
    run(repo, "add", ".")
    run(repo, "commit", "-q", "-m", "only commit")
    module = _filter_module()
    with pytest.raises(RuntimeError, match="merge commit"):
        module.changed_files("main", cwd=repo)


# --- supply-chain hardening ----------------------------------------------------------------------

_FULL_SHA_PIN = re.compile(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")
_DIGEST_PINNED_IMAGE = re.compile(r"[\w./-]+:[\w.-]+@sha256:[0-9a-f]{64}")


def _all_steps() -> list[tuple[str, dict[str, Any]]]:
    return [(name, step) for name, job in _jobs().items() for step in job.get("steps", [])]


def test_every_action_is_pinned_to_a_full_commit_sha_with_its_version_comment() -> None:
    """A tag is mutable: whoever controls the action repository can move `v7` to new code, and
    every run picks it up. A 40-hex SHA cannot move. The trailing `# vX.Y.Z` comment is what lets
    Dependabot's github-actions ecosystem bump the SHA and the readable version together."""
    uses = [(job, step["uses"]) for job, step in _all_steps() if "uses" in step]
    assert uses, "the workflow runs no actions at all?"
    for job, ref in uses:
        assert _FULL_SHA_PIN.match(ref), (job, ref)
    raw_uses = [
        line.strip()
        for line in _WORKFLOW.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith(("- uses:", "uses:"))
    ]
    assert len(raw_uses) == len(uses)
    for line in raw_uses:
        assert re.search(r"@[0-9a-f]{40} # v\d+\.\d+\.\d+$", line), line


def test_no_checkout_leaves_the_token_in_the_git_config() -> None:
    """actions/checkout persists the job token into .git/config by default, where any later step
    (or an uploaded artifact that includes the workspace) can read it. No job pushes."""
    checkouts = [
        (job, step)
        for job, step in _all_steps()
        if step.get("uses", "").startswith("actions/checkout@")
    ]
    assert checkouts
    for job, step in checkouts:
        assert step.get("with", {}).get("persist-credentials") is False, job


def test_every_job_has_a_timeout() -> None:
    """The default is six hours. A hung image build or testcontainer should fail in minutes."""
    for name, job in _jobs().items():
        timeout = job.get("timeout-minutes")
        assert isinstance(timeout, int), name
        assert 0 < timeout <= 60, (name, timeout)


def test_workflow_and_secrets_lints_the_workflow_and_gates_a_full_history_secret_scan() -> None:
    job = _jobs()["workflow-and-secrets"]
    checkout = job["steps"][0]
    # The secret scan reads every commit, so a shallow clone would silently scan one.
    assert checkout["with"] == {"persist-credentials": False, "fetch-depth": 0}

    _, actionlint = _step(job, "lint workflows (actionlint + shellcheck)")
    _, zizmor = _step(job, "audit workflow security (zizmor)")
    _, gitleaks = _step(job, "scan the full history for secrets (gitleaks, gated)")
    for step, image in (
        (actionlint, "rhysd/actionlint:"),
        (zizmor, "ghcr.io/zizmorcore/zizmor:"),
        (gitleaks, "ghcr.io/gitleaks/gitleaks:"),
    ):
        assert "if" not in step
        assert image in step["run"]
        pinned = _DIGEST_PINNED_IMAGE.search(step["run"])
        assert pinned is not None and pinned.group(0).startswith(image), step["run"]

    # zizmor audits the whole .github tree (dependabot.yml too) with the online audits enabled.
    assert zizmor["run"].rstrip().endswith("--no-progress .github")
    assert zizmor["env"] == {"GH_TOKEN": "${{ github.token }}"}
    # gitleaks scans git history (not just the tree) and fails the job on any finding.
    # Scoped to what HEAD reaches: `--all` (the default) would let a leak on an unrelated branch
    # fail every pull request.
    assert (
        ' git --redact --no-banner --exit-code 1 --log-opts="--full-history HEAD" .'
        in (gitleaks["run"])
    )
    assert "--exit-code 0" not in gitleaks["run"]
    assert "--baseline-path" not in gitleaks["run"]


def test_the_secret_scan_config_extends_the_default_rules_and_allowlists_narrowly() -> None:
    config = (_ROOT / ".gitleaks.toml").read_text(encoding="utf-8")
    assert "[extend]\nuseDefault = true" in config
    # One path allowlist, for the pinned audit vectors only; everything else is by fingerprint.
    assert "'''^apps/api/tests/fixtures/audit_[a-z0-9_]+_vectors\\.json$'''" in config
    assert config.count("paths = [") == 1
    assert "[[rules]]" not in config, "never replace the upstream rules"

    fingerprints = [
        line
        for line in (_ROOT / ".gitleaksignore").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    assert fingerprints
    for line in fingerprints:
        commit, path, rule, lineno = line.rsplit(":", 3)
        assert re.fullmatch(r"[0-9a-f]{40}", commit), line
        assert rule in {"generic-api-key", "private-key"}, line
        assert lineno.isdigit(), line
        assert not path.startswith("apps/api/tests/fixtures/"), "fixtures are path-allowlisted"


def test_dependency_review_gates_high_severity_on_pull_requests_only() -> None:
    job = _jobs()["dependency-review"]
    assert job["if"] == "github.event_name == 'pull_request'"
    (step,) = job["steps"]
    assert step["uses"].startswith("actions/dependency-review-action@")
    assert step["with"] == {"fail-on-severity": "high", "comment-summary-in-pr": "never"}
