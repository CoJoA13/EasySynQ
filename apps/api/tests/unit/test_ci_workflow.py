"""Semantic regression for the expensive CI gates and their failure propagation."""

from __future__ import annotations

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
_PIPELINE = _ROOT / ".gitlab-ci.yml"


def _step(job: dict[str, Any], name: str) -> tuple[int, dict[str, Any]]:
    matches = [(index, step) for index, step in enumerate(job["steps"]) if step.get("name") == name]
    assert len(matches) == 1, f"expected exactly one {name!r} step, got {len(matches)}"
    return matches[0]


def _assert_hard_fail(job: dict[str, Any]) -> None:
    assert "continue-on-error" not in job
    for step in job["steps"]:
        assert "continue-on-error" not in step, step.get("name") or step.get("uses")


def test_ci_workflow_preserves_complete_hard_fail_gates() -> None:
    workflow = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]

    web_shards = jobs["web-shards"]
    _assert_hard_fail(web_shards)
    assert "if" not in web_shards
    assert web_shards["name"] == "web tests (${{ matrix.shard }}/2)"
    assert web_shards["strategy"] == {
        "fail-fast": False,
        "matrix": {"shard": [1, 2]},
    }
    install_index, install = _step(web_shards, "Vitest shard ${{ matrix.shard }}/2")
    assert install == {
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
    assert install_index < static_index
    assert [step.get("run") for step in web_shards["steps"]].count(
        "npm test -- --shard=${{ matrix.shard }}/2"
    ) == 1

    web_browser = jobs["web-browser"]
    _assert_hard_fail(web_browser)
    assert web_browser == {
        "name": "web browser (Chromium)",
        "runs-on": "ubuntu-latest",
        "steps": [
            {"uses": "actions/checkout@v7"},
            {
                "uses": "actions/setup-node@v7",
                "with": {
                    "node-version": "26",
                    "cache": "npm",
                    "cache-dependency-path": "apps/web/package-lock.json",
                },
            },
            {"working-directory": "apps/web", "run": "npm ci"},
            {
                "name": "install Chromium",
                "working-directory": "apps/web",
                "run": "npx playwright install --with-deps chromium",
            },
            {
                "name": "responsive browser evidence",
                "working-directory": "apps/web",
                "run": "npm run test:browser",
            },
            {
                "name": "upload browser diagnostics",
                "if": "${{ failure() }}",
                "uses": "actions/upload-artifact@v7",
                "with": {
                    "name": "playwright-report",
                    "path": "apps/web/playwright-report\napps/web/test-results\n",
                    "if-no-files-found": "ignore",
                    "retention-days": 7,
                },
            },
        ],
    }
    for step in web_browser["steps"]:
        command = step.get("run", "")
        assert "|| true" not in command
        assert "--changed" not in command
        assert "--retries" not in command

    web_gate = jobs["web"]
    _assert_hard_fail(web_gate)
    assert web_gate == {
        "name": "web",
        "needs": ["web-shards", "web-browser"],
        "if": "${{ always() }}",
        "runs-on": "ubuntu-latest",
        "steps": [
            {
                "name": "gate on the shard results",
                "run": (
                    "shards_result='${{ needs.web-shards.result }}'\n"
                    "browser_result='${{ needs.web-browser.result }}'\n"
                    'if [ "$shards_result" != "success" ] '
                    '|| [ "$browser_result" != "success" ]; then\n'
                    '  echo "web checks did not all pass '
                    '(web-shards=$shards_result, web-browser=$browser_result)"\n'
                    "  exit 1\n"
                    "fi\n"
                    'echo "all web checks passed"\n'
                ),
            }
        ],
    }

    expected_commands = {
        ("contracts", "CI workflow contract"): (
            "bash scripts/tests/test-ci-hardening.sh\n"
            "bash scripts/tests/test-check-compose-images-lock.sh\n"
        ),
        ("contracts", "generated contract lock"): "bash scripts/gen-contracts.sh --check",
        (
            "contract-responses",
            "validate authenticated operational responses (disposable testcontainers only)",
        ): (
            "uv run pytest tests/integration/test_contract_response_schemas.py "
            "-m contract --tb=short"
        ),
        ("api", "unit tests"): "uv run pytest tests/unit -m unit",
        (
            "integration-shards",
            "integration tests (shard ${{ matrix.group }}/4, "
            "testcontainers spin their own Postgres)",
        ): (
            "uv run pytest tests/integration -m integration --splits 4 "
            "--group ${{ matrix.group }} --durations-path .test_durations "
            "--store-durations --clean-durations"
        ),
    }
    for (job_name, step_name), command in expected_commands.items():
        job = jobs[job_name]
        _assert_hard_fail(job)
        assert "if" not in job
        _, step = _step(job, step_name)
        assert "if" not in step
        assert step["run"] == command

    contracts = jobs["contracts"]
    _assert_hard_fail(contracts)
    authority_index, authority = _step(
        contracts, "Agent authority and Claude compatibility contracts"
    )
    assert authority == {
        "name": "Agent authority and Claude compatibility contracts",
        "run": (
            "bash scripts/tests/test-agent-authority.sh\n"
            "bash scripts/tests/test-claude-hooks.sh\n"
            "./scripts/check-repo-authority.sh\n"
        ),
    }
    assert "|| true" not in authority["run"]
    expected_contract_steps = [
        {"uses": "actions/checkout@v7"},
        {
            "name": "Agent authority and Claude compatibility contracts",
            "run": (
                "bash scripts/tests/test-agent-authority.sh\n"
                "bash scripts/tests/test-claude-hooks.sh\n"
                "./scripts/check-repo-authority.sh\n"
            ),
        },
        {
            "name": "R61 backstop regression harness",
            "run": "bash scripts/tests/test-check-no-site-data.sh",
        },
        {
            "name": "R61 site-data backstop (check-no-site-data)",
            "run": "./scripts/check-no-site-data.sh",
        },
        {
            "name": "doctor shell contracts",
            "run": "bash scripts/tests/test-doctor.sh",
        },
        {
            "uses": "actions/setup-node@v7",
            "with": {
                "node-version": "26",
                "cache": "npm",
                "cache-dependency-path": "packages/contracts/package-lock.json",
            },
        },
        {
            "name": "PostgreSQL MCP disabled contract",
            "run": "node --test scripts/tests/test-postgres-mcp-disabled.mjs",
        },
        {
            "name": "CI workflow contract",
            "run": (
                "bash scripts/tests/test-ci-hardening.sh\n"
                "bash scripts/tests/test-check-compose-images-lock.sh\n"
            ),
        },
        {
            "name": "install locked contract tools",
            "run": "npm ci --prefix packages/contracts --ignore-scripts",
        },
        {
            "name": "contract toolchain regressions",
            "run": (
                "bash scripts/tests/test-run-contract-tool.sh\n"
                "node --test scripts/tests/test-contract-lock.mjs\n"
                "bash scripts/tests/test-gen-contracts.sh\n"
            ),
        },
        {
            "name": "lint OpenAPI",
            "run": (
                "bash scripts/run-contract-tool.sh redocly lint --config "
                "packages/contracts/redocly.yaml packages/contracts/openapi.yaml"
            ),
        },
        {
            "name": "audit locked contract tools",
            "run": ("npm --prefix packages/contracts audit --package-lock-only --audit-level=high"),
        },
        {"name": "generated contract lock", "run": "bash scripts/gen-contracts.sh --check"},
    ]
    assert contracts["steps"] == expected_contract_steps
    setup_index = next(
        index
        for index, step in enumerate(contracts["steps"])
        if step.get("uses") == "actions/setup-node@v7"
    )
    assert authority_index < setup_index

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
    variable. It was written, was correct, and had NEVER run, because nothing set it anywhere in
    the repository — the same inertness the audit had already fixed once for ``EASYSYNQ_RELEASE``.
    Dependabot #448 was CLEAN on all sixteen checks while shipping an image whose CMD dies on
    start, which is what that unrun proof would have caught.

    So this pins the fix rather than trusting it to stay: an opt-in proof nothing opts into is
    indistinguishable from no proof, and deleting one line would silently restore that state.
    ⚠ It also pins the LOCATION. The `security` job already builds the image, which makes it the
    tempting home, but it is deliberately non-required — a guard there could not redden a PR, so
    moving the flag would satisfy the letter of the fix and none of its point.
    """
    workflow = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    _, unit_step = _step(workflow["jobs"]["api"], "unit tests")
    assert unit_step.get("env", {}).get("EASYSYNQ_IMAGE_PROOF") == "1"

    for job_name, job in workflow["jobs"].items():
        if job_name == "api":
            continue
        for step in job["steps"]:
            assert "EASYSYNQ_IMAGE_PROOF" not in (step.get("env") or {}), (
                f"the image runtime proof moved to {job_name!r}; it belongs in `api`, which can "
                "fail a merge"
            )


def _flatten_script(value: Any) -> str:
    """GitLab ``script:`` may nest a list when a YAML anchor is spliced in.

    Flatten before matching, or a spliced anchor makes the whole block unsearchable.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_flatten_script(item) for item in value)
    return ""


def _gitlab_jobs() -> dict[str, Any]:
    """Top-level GitLab jobs, excluding the dot-prefixed YAML fragments and global keys."""
    pipeline = yaml.safe_load(_PIPELINE.read_text(encoding="utf-8"))
    return {
        name: body
        for name, body in pipeline.items()
        if isinstance(body, dict) and not name.startswith(".") and name not in {"variables"}
    }


def test_gitlab_pipeline_preserves_complete_hard_fail_gates() -> None:
    """No GitLab job may be allowed to fail.

    GitLab's `allow_failure: true` is the direct counterpart of GitHub's `continue-on-error`,
    and it is worse in one respect: an allowed-to-fail job still renders a green pipeline, so
    the gate reads exactly as it does when the job passed.
    """
    for name, job in _gitlab_jobs().items():
        assert job.get("allow_failure") is not True, f"{name} is allowed to fail"


def test_gitlab_migrations_job_runs_the_complete_suite_without_escape_hatches() -> None:
    pipeline = yaml.safe_load(_PIPELINE.read_text(encoding="utf-8"))
    migrations = pipeline["migrations"]
    command = "uv run pytest tests/migration"

    assert migrations["script"].count(command) == 1
    assert not any("pytest tests/migration/" in entry for entry in migrations["script"])
    assert migrations["extends"] == [".uv", ".rules-code-and-main"]
    for job in (migrations, pipeline[".uv"]):
        for bypass in ("allow_failure", "rules", "when", "only", "except"):
            assert bypass not in job, f"migrations gate cannot inherit {bypass}"
    # Its only conditions are the reviewed template, pinned by the compute-budget tests below.
    assert all("when" not in rule for rule in pipeline[".rules-code-and-main"]["rules"])


def test_the_gitlab_image_runtime_proof_runs_in_the_job_that_can_fail_a_merge() -> None:
    """`EASYSYNQ_IMAGE_PROOF` must be set in the api job, and only there.

    Identical reasoning to the GitHub pin: the proof is skipif-gated, so an unset variable
    leaves it inert while the job still reports success. GitLab has no non-required jobs, but
    the placement still matters — the proof belongs with the suite that builds and starts the
    image, not scattered into a job that never runs it.
    """
    jobs = _gitlab_jobs()
    api_script = _flatten_script(jobs["api"]["script"])
    assert "EASYSYNQ_IMAGE_PROOF=1" in api_script

    for name, job in jobs.items():
        if name == "api":
            continue
        body = _flatten_script(job.get("script", [])) + _flatten_script(
            job.get("before_script", [])
        )
        assert "EASYSYNQ_IMAGE_PROOF" not in body, (
            f"the image runtime proof moved to {name!r}; it belongs with the api suite"
        )


def test_gitlab_requires_external_trust_runtime_acceptance_after_image_proof() -> None:
    jobs = _gitlab_jobs()
    invocation = (
        'runuser -u ci -- env HOME="$CI_PROJECT_DIR/.ci-home" PATH="$PATH" '
        'DOCKER_HOST="$DOCKER_HOST" '
        'python3 "$CI_PROJECT_DIR/scripts/run-audit-external-acceptance.py"'
    )
    api = jobs["api"]
    assert api.get("allow_failure") is not True
    assert isinstance(api["script"], list)
    matches = [
        entry
        for entry in api["script"]
        if isinstance(entry, str) and "run-audit-external-acceptance.py" in entry
    ]
    assert matches == [invocation]
    image_proof_index = next(
        index for index, entry in enumerate(api["script"]) if "EASYSYNQ_IMAGE_PROOF=1" in entry
    )
    acceptance_index = api["script"].index(invocation)
    assert image_proof_index < acceptance_index
    for escape in ("|| true", "|| :", " if ", "easysynq_acceptance_enable", "skip"):
        assert escape not in invocation.lower()

    for name, job in jobs.items():
        occurrences = _flatten_script(job.get("script", [])).count(
            "run-audit-external-acceptance.py"
        )
        assert occurrences == (1 if name == "api" else 0)


def test_gitlab_pipeline_collects_only_the_authoritative_test_trees() -> None:
    """The GitLab counterpart of the GitHub per-job command pins."""
    jobs = _gitlab_jobs()
    expected = {
        "api": "pytest tests/unit -m unit",
        "integration-shards": "pytest tests/integration -m integration",
        "contract-responses": (
            "pytest tests/integration/test_contract_response_schemas.py -m contract"
        ),
        "release-gate": "pytest tests/unit/test_images_lock_pinned.py -q",
    }
    for job_name, command in expected.items():
        script = _flatten_script(jobs[job_name]["script"])
        assert command in script, f"{job_name} no longer runs {command!r}"


def test_security_job_gates_npm_and_keeps_trivy_findings_report_only() -> None:
    workflow = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    security = workflow["jobs"]["security"]

    assert set(security) == {"runs-on", "steps"}
    assert security["runs-on"] == "ubuntu-latest"
    _assert_hard_fail(security)

    setup_matches = [
        (index, step)
        for index, step in enumerate(security["steps"])
        if step.get("uses") == "actions/setup-node@v7"
    ]
    assert len(setup_matches) == 1
    setup_index, setup = setup_matches[0]
    assert setup == {
        "uses": "actions/setup-node@v7",
        "with": {
            "node-version": "26",
            "cache": "npm",
            "cache-dependency-path": "apps/web/package-lock.json",
        },
    }

    install_index, install = _step(security, "install frozen web dependencies for npm policy")
    assert install == {
        "name": "install frozen web dependencies for npm policy",
        "working-directory": "apps/web",
        "run": "npm ci --ignore-scripts",
    }
    regression_index, regressions = _step(security, "npm advisory policy regressions")
    assert regressions == {
        "name": "npm advisory policy regressions",
        "run": (
            "node --test \\\n"
            "  scripts/tests/test-web-security-lock.mjs \\\n"
            "  scripts/tests/test-npm-audit-runner.mjs \\\n"
            "  scripts/tests/test-check-npm-audit.mjs \\\n"
            "  scripts/tests/test-npm-audit-policy.mjs \\\n"
            "  scripts/tests/test-router-rsc-policy.mjs\n"
        ),
    }
    policy_index, policy = _step(security, "npm advisory policy (web lock)")
    assert policy == {
        "name": "npm advisory policy (web lock)",
        "run": "node scripts/check-npm-audit.mjs",
    }
    assert [step for step in security["steps"] if "npm" in step.get("name", "").lower()] == [
        install,
        regressions,
        policy,
    ]
    first_trivy_index, _ = _step(
        security, "trivy filesystem scan (vuln + secret + IaC misconfig; HIGH/CRITICAL)"
    )
    assert setup_index < install_index < regression_index < policy_index < first_trivy_index

    trivy_steps = [
        step
        for step in security["steps"]
        if step.get("uses") == "aquasecurity/trivy-action@v0.36.0"
    ]
    assert len(trivy_steps) == 3
    assert [step["with"]["exit-code"] for step in trivy_steps] == ["0", "0", "0"]


def test_gitlab_security_gates_both_built_images_after_the_live_npm_gate() -> None:
    """A base scan or a conditional runner must not substitute for built-artifact evidence."""
    pipeline = yaml.safe_load(_PIPELINE.read_text(encoding="utf-8"))
    security = pipeline["security"]
    assert security["extends"] == [".uv", ".dind", ".rules-security"]
    for job in [security, pipeline[".uv"], pipeline[".dind"]]:
        for escape in ("allow_failure", "rules", "when", "only", "except"):
            assert escape not in job, f"security gate cannot inherit {escape}"
    # Its only conditions are the reviewed template, pinned by the compute-budget tests below.
    assert all("when" not in rule for rule in pipeline[".rules-security"]["rules"])

    script = security["script"]
    required = [
        "node scripts/check-npm-audit.mjs",
        "trivy fs --scanners vuln,secret,misconfig --severity HIGH,CRITICAL "
        "--exit-code 0 --format table .",
        "bash scripts/tests/test-built-image-security.sh",
        "docker build -f apps/api/Dockerfile -t easysynq-api:scan .",
        "docker build -f apps/web/Dockerfile -t easysynq-web:scan apps/web",
        "bash scripts/check-built-image-security.sh",
    ]
    for command in required:
        assert script.count(command) == 1, f"missing unconditional command: {command}"
    indexes = [script.index(command) for command in required]
    assert indexes == sorted(indexes)
    assert 'cd "$CI_PROJECT_DIR"' in script[: indexes[0]]
    assert not any("cd " in line for line in script[indexes[0] :])
    body = _flatten_script(script[indexes[0] :])
    assert "||" not in body
    assert "set +e" not in body
    assert "trivy image" not in body
    assert "grep -E '^FROM '" not in body
    assert "bash scripts/tests/test-pip-audit-runner.sh" in script
    assert "bash scripts/run-pip-audit.sh" in script
    setup = _flatten_script(security["before_script"])
    assert "aquasec/trivy:0.74.0" in setup
    assert "TRIVY_DB_REPOSITORY=docker.io/aquasec/trivy-db:2" in setup
    assert "TRIVY_JAVA_DB_REPOSITORY=docker.io/aquasec/trivy-java-db:1" in setup


# --- compute budget: MRs are narrowed by path, never tags or manual runs ------------------------

_TAG = {"if": "$CI_COMMIT_TAG"}
_MANUAL = {"if": "$CI_PIPELINE_SOURCE =~ /^(web|api|trigger)$/"}
_MAIN_PUSH = {"if": '$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH && $CI_PIPELINE_SOURCE == "push"'}
_MR = '$CI_PIPELINE_SOURCE == "merge_request_event"'


def _pipeline() -> dict[str, Any]:
    return yaml.safe_load(_PIPELINE.read_text(encoding="utf-8"))


def _resolved_rules(pipeline: dict[str, Any], job: dict[str, Any]) -> list[Any] | None:
    """The rules a job receives: its own, else those of the last `extends` template setting them."""
    if "rules" in job:
        return list(job["rules"])
    extends = job.get("extends", [])
    templates = [extends] if isinstance(extends, str) else list(extends)
    for name in reversed(templates):
        if "rules" in pipeline[name]:
            return list(pipeline[name]["rules"])
    return None


def _mr_changes(rule: dict[str, Any]) -> list[str]:
    assert rule["if"] == _MR
    assert rule["changes"]["compare_to"] == "refs/heads/main"
    assert "when" not in rule
    return list(rule["changes"]["paths"])


def _is_docs_only(path: str) -> bool:
    return path.startswith("docs/") or ("/" not in path and path.endswith(".md"))


def _covered_by(path: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if pattern.endswith("/**/*"):
            if path.startswith(pattern[: -len("**/*")]):
                return True
        else:
            assert "*" not in pattern, f"only <dir>/**/* or exact root files: {pattern}"
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
    code = list(_pipeline()[".changes-code"])
    uncovered = [
        path for path in tracked if not _is_docs_only(path) and not _covered_by(path, code)
    ]
    assert uncovered == []
    assert not any(_is_docs_only(path) and _covered_by(path, code) for path in tracked)


@pytest.mark.parametrize(
    ("job_name", "anchor", "required_paths"),
    [
        ("integration-shards", ".changes-api-suites", {"apps/api/**/*", "migrations/**/*"}),
        ("contract-responses", ".changes-api-suites", {"apps/api/**/*", "packages/contracts/**/*"}),
        ("web-tests", ".changes-web-suites", {"apps/web/**/*", "packages/contracts/**/*"}),
        ("web-browser", ".changes-web-suites", {"apps/web/**/*", "packages/contracts/**/*"}),
        ("api", ".changes-code", {"apps/**/*", "scripts/**/*", "infra/**/*"}),
    ],
)
def test_path_filtered_jobs_always_run_for_tags_and_manual_pipelines(
    job_name: str, anchor: str, required_paths: set[str]
) -> None:
    pipeline = _pipeline()
    rules = _resolved_rules(pipeline, pipeline[job_name])
    assert rules is not None and len(rules) == 3, job_name
    assert rules[:2] == [_TAG, _MANUAL], job_name
    paths = _mr_changes(rules[2])
    assert paths == list(pipeline[anchor]), job_name
    assert ".gitlab-ci.yml" in paths, "a pipeline edit must exercise every suite"
    assert required_paths <= set(paths), job_name


@pytest.mark.parametrize(
    ("job_name", "first_rules"),
    [
        ("migrations", [_TAG, _MANUAL, _MAIN_PUSH]),
        (
            "security",
            [_TAG, {"if": "$CI_PIPELINE_SOURCE =~ /^(web|api|trigger|schedule)$/"}, _MAIN_PUSH],
        ),
    ],
)
def test_merge_backstops_run_on_every_main_push_and_code_mr(
    job_name: str, first_rules: list[dict[str, str]]
) -> None:
    pipeline = _pipeline()
    rules = _resolved_rules(pipeline, pipeline[job_name])
    assert rules is not None and rules[:3] == first_rules, job_name
    assert len(rules) == 4
    assert _mr_changes(rules[3]) == list(pipeline[".changes-code"])


def test_the_docs_lane_runs_exactly_when_an_mr_touches_no_code() -> None:
    pipeline = _pipeline()
    job = pipeline["docs-tests"]
    assert _resolved_rules(pipeline, job) == [
        {"if": '$CI_PIPELINE_SOURCE != "merge_request_event"', "when": "never"},
        {
            "changes": {"paths": list(pipeline[".changes-code"]), "compare_to": "refs/heads/main"},
            "when": "never",
        },
        {"when": "on_success"},
    ]
    script = _flatten_script(job["script"])
    # Content-selected, unprivileged, and fail-closed on an empty selection.
    assert 'grep -rlE "$DOCS_TEST_PATTERN" tests/unit --include="test_*.py"' in script
    assert 'test -n "$files"' in script
    assert "runuser -u ci" in script
    assert "uv run pytest $files -m unit" in script


def test_the_docs_lane_selection_keeps_its_documentation_pinning_tests() -> None:
    """Narrowing DOCS_TEST_PATTERN (e.g. "balancing" its quotes) would silently drop these."""
    pattern = re.compile(_pipeline()["docs-tests"]["variables"]["DOCS_TEST_PATTERN"])
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


@pytest.mark.parametrize("job_name", ["contracts", "compose-images-lock", "renovate-config"])
def test_cheap_guards_run_on_every_pipeline(job_name: str) -> None:
    pipeline = _pipeline()
    assert _resolved_rules(pipeline, pipeline[job_name]) is None, job_name


def test_workflow_uses_merge_request_pipelines_and_never_cancels_main() -> None:
    workflow = _pipeline()["workflow"]
    assert workflow["auto_cancel"] == {"on_new_commit": "interruptible"}
    assert workflow["rules"] == [
        _TAG,
        {
            "if": "$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH",
            "auto_cancel": {"on_new_commit": "none"},
        },
        {"if": '$CI_PIPELINE_SOURCE == "push"', "when": "never"},
        {"when": "always"},
    ]
    assert _pipeline()["default"]["interruptible"] is True


def test_renovate_rebases_only_on_conflict_and_holds_majors_for_approval() -> None:
    renovate = json.loads((_ROOT / "renovate.json").read_text(encoding="utf-8"))
    assert renovate["rebaseWhen"] == "conflicted"
    assert renovate["major"] == {"dependencyDashboardApproval": True}
