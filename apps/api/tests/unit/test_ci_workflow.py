"""Semantic regression for the expensive CI gates and their failure propagation."""

from __future__ import annotations

import json
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


def test_gitlab_migrations_job_runs_the_complete_suite_unconditionally() -> None:
    pipeline = yaml.safe_load(_PIPELINE.read_text(encoding="utf-8"))
    migrations = pipeline["migrations"]
    command = "uv run pytest tests/migration"

    assert migrations["script"].count(command) == 1
    assert not any("pytest tests/migration/" in entry for entry in migrations["script"])
    for job in (migrations, pipeline[".uv"]):
        for bypass in ("allow_failure", "rules", "when", "only", "except"):
            assert bypass not in job, f"migrations gate cannot inherit {bypass}"


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
    assert security["extends"] == [".uv", ".dind"]
    for job in [security, pipeline[".uv"], pipeline[".dind"]]:
        for escape in ("allow_failure", "rules", "when", "only", "except"):
            assert escape not in job, f"security gate cannot inherit {escape}"

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


# --- compute budget: path rules may narrow branch pipelines, never main or tags ---------------

_MAIN = {"if": "$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH"}
_TAG = {"if": "$CI_COMMIT_TAG"}
_NOT_SCHEDULED = {"if": '$CI_PIPELINE_SOURCE == "schedule"', "when": "never"}


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


@pytest.mark.parametrize(
    ("job_name", "required_paths"),
    [
        ("integration-shards", {"apps/api/**/*", "migrations/**/*", "packages/contracts/**/*"}),
        ("contract-responses", {"apps/api/**/*", "migrations/**/*", "packages/contracts/**/*"}),
        ("web-tests", {"apps/web/**/*", "packages/contracts/**/*"}),
        ("web-browser", {"apps/web/**/*", "packages/contracts/**/*"}),
    ],
)
def test_path_filtered_suites_still_run_on_every_main_and_tag_pipeline(
    job_name: str, required_paths: set[str]
) -> None:
    pipeline = yaml.safe_load(_PIPELINE.read_text(encoding="utf-8"))
    rules = _resolved_rules(pipeline, pipeline[job_name])
    assert rules is not None, f"{job_name} has no rules"
    # Order is the policy: skip schedules, then run unconditionally on main and tags, and only
    # then consult the changed paths. A `changes` rule ahead of main would let it skip there.
    assert rules[:3] == [_NOT_SCHEDULED, _MAIN, _TAG], job_name
    assert len(rules) == 4, job_name
    changes = rules[3]["changes"]
    assert changes["compare_to"] == "refs/heads/main"
    paths = set(changes["paths"])
    assert ".gitlab-ci.yml" in paths, "a pipeline edit must exercise every suite"
    assert required_paths <= paths, job_name


def test_the_api_suite_is_not_path_filtered() -> None:
    """Its unit tests read docs, scripts and web files, so no path list could be complete."""
    pipeline = yaml.safe_load(_PIPELINE.read_text(encoding="utf-8"))
    assert _resolved_rules(pipeline, pipeline["api"]) == [_NOT_SCHEDULED, {"when": "on_success"}]


@pytest.mark.parametrize(
    "job_name", ["contracts", "security", "migrations", "compose-images-lock", "renovate-config"]
)
def test_cheap_guards_and_the_security_scan_run_on_every_pipeline(job_name: str) -> None:
    pipeline = yaml.safe_load(_PIPELINE.read_text(encoding="utf-8"))
    assert _resolved_rules(pipeline, pipeline[job_name]) is None, job_name


def test_only_feature_branch_pipelines_are_auto_cancelled() -> None:
    pipeline = yaml.safe_load(_PIPELINE.read_text(encoding="utf-8"))
    workflow = pipeline["workflow"]
    assert workflow["auto_cancel"] == {"on_new_commit": "interruptible"}
    # main is exempt: each main pipeline is the post-merge record for its own merge.
    assert workflow["rules"][0] == {**_MAIN, "auto_cancel": {"on_new_commit": "none"}}
    assert workflow["rules"][-1] == {"when": "always"}
    assert pipeline["default"]["interruptible"] is True


def test_renovate_rebases_only_on_conflict_and_holds_majors_for_approval() -> None:
    renovate = json.loads((_ROOT / "renovate.json").read_text(encoding="utf-8"))
    assert renovate["rebaseWhen"] == "conflicted"
    assert renovate["major"] == {"dependencyDashboardApproval": True}
