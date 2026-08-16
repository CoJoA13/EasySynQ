from __future__ import annotations

import inspect
from pathlib import Path

import yaml

from easysynq_api.api import users as users_api

ROOT = Path(__file__).resolve().parents[4]
OPENAPI = ROOT / "packages/contracts/openapi.yaml"


def test_identity_usernames_are_documented_as_canonical_lowercase() -> None:
    spec = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    first_admin = spec["components"]["schemas"]["FirstAdministratorRequest"]["properties"]
    later_user = spec["paths"]["/users/provision"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]["properties"]

    for username in (first_admin["username"], later_user["username"]):
        assert "canonical" in username["description"].lower()
        assert "lowercase" in username["description"].lower()


def test_existing_user_credential_reset_contract_is_unconditionally_system_tier() -> None:
    spec = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    operation = spec["paths"]["/users/{user_id}/temporary-password"]["post"]
    contract_text = f"{operation['summary']} {operation['responses']['422']['description']}".lower()
    route_doc = inspect.getdoc(users_api.issue_temporary_password) or ""

    assert "every existing linked user" in contract_text
    assert "unconditionally" in contract_text
    assert "every existing linked user" in route_doc.lower()
    assert "holds any system-domain" not in contract_text
    assert "holds any system-domain" not in route_doc.lower()

    for relative_path in ("docs/08-setup-and-onboarding.md", "docs/15-api-design.md"):
        content = (ROOT / relative_path).read_text(encoding="utf-8").lower()
        assert "every existing linked user" in content
        assert "an unprivileged target needs only" not in content

    setup_guide = (ROOT / "docs/08-setup-and-onboarding.md").read_text(encoding="utf-8").lower()
    assert "| **reset** in manage | `user.create` + system tier |" in setup_guide
