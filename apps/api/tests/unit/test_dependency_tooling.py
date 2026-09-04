"""Semantic regression coverage for locked dependency tooling configuration."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parents[4]


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _just_recipe_commands(source: str, name: str) -> list[str]:
    match = re.search(rf"(?m)^{re.escape(name)}:\n((?:    [^\n]+\n)+)", source)
    assert match is not None, f"missing just recipe {name!r}"
    return [line.removeprefix("    ") for line in match.group(1).splitlines()]


def test_locked_contract_toolchain_manifest_and_resolution_are_exact() -> None:
    manifest = _read_json(_ROOT / "packages" / "contracts" / "package.json")
    lock = _read_json(_ROOT / "packages" / "contracts" / "package-lock.json")
    api_project = _read_toml(_ROOT / "apps" / "api" / "pyproject.toml")

    assert api_project["project"]["requires-python"] == ">=3.12,<3.13"
    assert manifest == {
        "name": "@easysynq/contracts-toolchain",
        "version": "0.1.0",
        "private": True,
        "overrides": {"@redocly/openapi-core": {"js-yaml": "4.3.1"}},
        "devDependencies": {
            "@redocly/cli": "2.49.0",
            "openapi-typescript": "7.13.0",
        },
    }
    assert lock["lockfileVersion"] == 3
    assert lock["packages"][""]["devDependencies"] == manifest["devDependencies"]
    assert lock["packages"]["node_modules/@redocly/cli"]["version"] == "2.49.0"
    assert lock["packages"]["node_modules/openapi-typescript"]["version"] == "7.13.0"
    assert lock["packages"]["node_modules/js-yaml"]["version"] == "4.3.1"
    assert all(
        package["version"] != "4.3.0"
        for name, package in lock["packages"].items()
        if name.endswith("/js-yaml")
    )


def test_locked_python_security_group_and_resolution_are_exact() -> None:
    project = _read_toml(_ROOT / "apps" / "api" / "pyproject.toml")
    lock = _read_toml(_ROOT / "apps" / "api" / "uv.lock")

    assert project["dependency-groups"]["security"] == ["pip-audit==2.10.1"]
    pip_audit = [package for package in lock["package"] if package["name"] == "pip-audit"]
    assert [package["version"] for package in pip_audit] == ["2.10.1"]

    editable_root = next(
        package
        for package in lock["package"]
        if package["name"] == "easysynq-api" and package["source"] == {"editable": "."}
    )
    assert editable_root["dev-dependencies"]["security"] == [{"name": "pip-audit"}]
    assert editable_root["metadata"]["requires-dev"]["security"] == [
        {"name": "pip-audit", "specifier": "==2.10.1"}
    ]


def test_dependabot_tracks_only_version_updates_for_the_locked_contract_toolchain() -> None:
    dependabot = yaml.safe_load((_ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    contract_entries = [
        entry
        for entry in dependabot["updates"]
        if entry["package-ecosystem"] == "npm" and entry["directory"] == "/packages/contracts"
    ]

    assert contract_entries == [
        {
            "package-ecosystem": "npm",
            "directory": "/packages/contracts",
            "schedule": {"interval": "weekly"},
            "open-pull-requests-limit": 5,
            "groups": {
                "contract-tools-minor-patch": {
                    "applies-to": "version-updates",
                    "update-types": ["minor", "patch"],
                }
            },
        }
    ]
    contract_entry = contract_entries[0]
    assert "target-branch" not in contract_entry
    assert "auto-merge" not in contract_entry
    assert all(
        group.get("applies-to", "version-updates") != "security-updates"
        for group in contract_entry["groups"].values()
    )


def test_api_image_python_major_matches_requires_python() -> None:
    """The API image's base interpreter must satisfy `requires-python`.

    A mismatched base does NOT fail the build, which is why this guard is static and blocking. With
    a newer base, `uv sync --locked` cannot satisfy `requires-python` from `/usr/local/bin`, so it
    downloads a managed CPython into root-owned `/root/.local/share/uv/python/` and writes that path
    as the venv's `home`. The image runs as `USER easysynq` (uid 10001), so the CMD `uv run
    gunicorn ...` dies with `Failed to query Python interpreter ... Permission denied`. The
    `security` job BUILDS the image but only Trivy-scans it, and the runtime proof that would catch
    it (`test_the_built_api_image_is_unprivileged_and_starts_offline`) is skipif-gated on
    `EASYSYNQ_IMAGE_PROOF`, which nothing sets -- so the break ships green, as Dependabot #448
    (python 3.12 -> 3.14) did on all sixteen checks. This test runs in the required `api` job and
    needs no Docker, covering the likeliest case cheaply; the general gap stays open as
    RES-IMAGE-PROOF-NEVER-ENABLED.
    """
    requires_python = _read_toml(_ROOT / "apps" / "api" / "pyproject.toml")["project"][
        "requires-python"
    ]
    floor = re.search(r">=(\d+)\.(\d+)", requires_python)
    ceiling = re.search(r"<(\d+)\.(\d+)", requires_python)
    assert floor is not None and ceiling is not None, requires_python
    low = (int(floor.group(1)), int(floor.group(2)))
    high = (int(ceiling.group(1)), int(ceiling.group(2)))

    dockerfile = (_ROOT / "apps" / "api" / "Dockerfile").read_text(encoding="utf-8")
    bases = re.findall(r"(?m)^FROM python:(\d+)\.(\d+)[.-]", dockerfile)
    assert bases, "no `FROM python:<major>.<minor>` line in apps/api/Dockerfile"
    for major, minor in bases:
        assert low <= (int(major), int(minor)) < high, (
            f"apps/api/Dockerfile pins python:{major}.{minor} but requires-python is "
            f"{requires_python}; uv would build the venv on a downloaded interpreter under "
            "root-owned /root and the image would fail to start as uid 10001"
        )

    # Belt-and-braces, not evidence: under `uv run` (what CI uses) uv refuses first with "resolved
    # to Python X, which is incompatible with the project's Python requirement", so this leg can
    # only be mutation-proven by invoking pytest through the venv interpreter directly. Kept because
    # it states the three-way coupling in one place; the load-bearing legs are the Dockerfile base
    # and the Dependabot ceiling above, both of which redden under the ordinary harness.
    tracked = (_ROOT / "apps" / "api" / ".python-version").read_text(encoding="utf-8").strip()
    tracked_parts = tuple(int(part) for part in tracked.split(".")[:2])
    assert low <= tracked_parts < high, f".python-version {tracked} is outside {requires_python}"

    # Keep Dependabot from re-proposing the bump every week. Unlike the reportlab/Mantine majors,
    # this ceiling has a proven RUNTIME consequence, so the refusal is pinned here as well as
    # commented in the config.
    dependabot = yaml.safe_load((_ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    api_docker = next(
        entry
        for entry in dependabot["updates"]
        if entry["package-ecosystem"] == "docker" and entry["directory"] == "/apps/api"
    )
    assert {"dependency-name": "python", "versions": [f">={high[0]}.{high[1]}"]} in api_docker[
        "ignore"
    ]


def test_vulnerable_postgres_mcp_connector_is_disabled() -> None:
    mcp_config = _read_json(_ROOT / ".mcp.json")
    dependabot = yaml.safe_load((_ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    justfile = (_ROOT / "justfile").read_text(encoding="utf-8")

    assert mcp_config == {"mcpServers": {}}
    assert "setup-mcp:" not in justfile
    assert "up-mcp" not in justfile
    assert not (_ROOT / "tools" / "mcp-postgres" / "package.json").exists()
    assert not (_ROOT / "tools" / "mcp-postgres" / "package-lock.json").exists()
    assert not (_ROOT / "scripts" / "run-postgres-mcp.sh").exists()
    assert not (_ROOT / "infra" / "compose" / "compose.mcp.yml").exists()
    assert not [
        entry
        for entry in dependabot["updates"]
        if entry["package-ecosystem"] == "npm" and entry["directory"] == "/tools/mcp-postgres"
    ]


def test_local_contract_entry_points_use_the_locked_launcher() -> None:
    pre_commit = yaml.safe_load((_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    contracts_hook = next(
        hook
        for repo in pre_commit["repos"]
        if repo["repo"] == "local"
        for hook in repo["hooks"]
        if hook["id"] == "contracts-lint"
    )
    check_contracts = (_ROOT / ".claude" / "commands" / "check-contracts.md").read_text(
        encoding="utf-8"
    )
    pr_command = (_ROOT / ".claude" / "commands" / "pr.md").read_text(encoding="utf-8")
    justfile = (_ROOT / "justfile").read_text(encoding="utf-8")

    assert contracts_hook == {
        "id": "contracts-lint",
        "name": "openapi contract lint (redocly --config, matches CI)",
        "entry": (
            "bash scripts/run-contract-tool.sh redocly lint --config "
            "packages/contracts/redocly.yaml packages/contracts/openapi.yaml"
        ),
        "language": "system",
        "files": "^packages/contracts/",
        "pass_filenames": False,
    }
    assert "Bash(npx:*)" not in check_contracts
    assert (
        "bash scripts/run-contract-tool.sh redocly lint --config "
        "packages/contracts/redocly.yaml packages/contracts/openapi.yaml" in check_contracts
    )
    assert "bash scripts/gen-contracts.sh --check" in check_contracts
    assert "bash scripts/run-contract-tool.sh redocly lint" in pr_command
    assert (
        "setup:\n"
        "    cd apps/api && uv sync\n"
        "    npm ci --prefix apps/web\n"
        "    npm ci --prefix packages/contracts --ignore-scripts\n"
        "    just contracts\n"
        "    pre-commit install" in justfile
    )


def test_local_npm_security_recipe_is_the_exact_ci_mirror() -> None:
    justfile = (_ROOT / "justfile").read_text(encoding="utf-8")

    assert _just_recipe_commands(justfile, "security-npm") == [
        (
            "node --test scripts/tests/test-web-security-lock.mjs "
            "scripts/tests/test-npm-audit-runner.mjs "
            "scripts/tests/test-check-npm-audit.mjs "
            "scripts/tests/test-npm-audit-policy.mjs "
            "scripts/tests/test-router-rsc-policy.mjs"
        ),
        "node scripts/check-npm-audit.mjs",
    ]


def test_active_guidance_tracks_both_frozen_npm_locks_and_mixed_policy() -> None:
    readme = (_ROOT / "README.md").read_text(encoding="utf-8")
    dev_workflow = (_ROOT / "docs" / "dev-workflow.md").read_text(encoding="utf-8")
    fresh_linux = (_ROOT / "docs" / "runbooks" / "fresh-linux-setup.md").read_text(encoding="utf-8")

    assert "apps/web/package-lock.json" in dev_workflow
    assert "packages/contracts/package-lock.json" in dev_workflow
    assert "frozen" in dev_workflow
    assert "just security-npm" in dev_workflow
    normalized_workflow = " ".join(dev_workflow.split())
    assert "npm high/critical findings are gated" in normalized_workflow
    assert "pip-audit and Trivy findings are report-only" in normalized_workflow
    assert "`security` is warn-only" not in dev_workflow

    for setup_guide in (readme, fresh_linux):
        assert "just setup" in setup_guide
        assert "packages/contracts/package-lock.json" in setup_guide


def test_contract_lock_hook_keeps_the_reminder_without_stale_ci_claims() -> None:
    hook = (_ROOT / ".claude" / "hooks" / "contract-lock-drift.sh").read_text(encoding="utf-8")

    assert "Contract-lock reminder:" in hook
    assert "NO CI JOB RUNS scripts/gen-contracts.sh" not in hook
    assert "NO CI job runs scripts/gen-contracts.sh" not in hook
