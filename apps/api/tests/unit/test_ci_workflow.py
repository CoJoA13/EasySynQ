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

    web_gate = jobs["web"]
    _assert_hard_fail(web_gate)
    assert web_gate["name"] == "web"
    assert web_gate["needs"] == "web-shards"
    assert web_gate["if"] == "${{ always() }}"
    assert len(web_gate["steps"]) == 1
    assert web_gate["steps"][0] == {
        "name": "gate on the shard results",
        "run": (
            "result='${{ needs.web-shards.result }}'\n"
            'if [ "$result" != "success" ]; then\n'
            '  echo "web shards did not all pass (result=$result)"\n'
            "  exit 1\n"
            "fi\n"
            'echo "all web shards passed"\n'
        ),
    }

    expected_commands = {
        ("contracts", "CI workflow contract"): "bash scripts/tests/test-ci-hardening.sh",
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

    package = json.loads((_ROOT / "apps" / "web" / "package.json").read_text(encoding="utf-8"))
    assert package["scripts"]["test"] == "vitest run"
    assert package["scripts"]["build"] == "tsc --noEmit && vite build"

    vitest_config = (_ROOT / "apps" / "web" / "vite.config.ts").read_text(encoding="utf-8")
    assert 'pool: "forks"' in vitest_config
    assert "maxWorkers: 1" in vitest_config
    assert "isolate: false" not in vitest_config
