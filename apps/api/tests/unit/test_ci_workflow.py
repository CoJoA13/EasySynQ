"""Semantic regression for the expensive CI gates and their failure propagation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parents[4]
_WORKFLOW = _ROOT / ".github" / "workflows" / "ci.yml"


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
