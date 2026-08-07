"""Semantic regression coverage for locked dependency tooling configuration."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parents[4]


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


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
            "@redocly/cli": "2.46.0",
            "openapi-typescript": "7.13.0",
        },
    }
    assert lock["lockfileVersion"] == 3
    assert lock["packages"][""]["devDependencies"] == manifest["devDependencies"]
    assert lock["packages"]["node_modules/@redocly/cli"]["version"] == "2.46.0"
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
