from __future__ import annotations

import inspect
from pathlib import Path

import yaml

from easysynq_api.api import users as users_api
from easysynq_api.services.authz.pep import assert_can_reset_credential

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


def test_reset_authority_describes_every_existing_user_including_self() -> None:
    register = (ROOT / "docs/decisions-register.md").read_text(encoding="utf-8")
    r64 = register.split("### R64 —", 1)[1].split("### R65 —", 1)[0]
    rule = r64.split("5. **Credential-reset authority", 1)[1].split("**No new permission key**", 1)[
        0
    ]
    model = (ROOT / "docs/07-authorization-model.md").read_text(encoding="utf-8")
    reset_section = model.split("#### 4.3.1 Credential reset", 1)[1].split("\n###", 1)[0]
    guard_doc = (inspect.getdoc(assert_can_reset_credential) or "").lower()

    assert "any existing user's credential, including their own" in rule.lower()
    assert "every existing linked user's credential reset, including the caller's own" in (
        reset_section.lower()
    )
    assert "another user" not in rule.lower()
    assert "another user" not in reset_section.lower()
    assert "another user" not in guard_doc
    assert "own" in guard_doc


def test_self_permission_contract_requires_system_tier() -> None:
    spec = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    own_response = spec["paths"]["/me/permissions"]["get"]["responses"]["200"]
    own_schema = own_response["content"]["application/json"]["schema"]
    assert own_schema == {"$ref": "#/components/schemas/MyEffectivePermissions"}
    schema = spec["components"]["schemas"]["MyEffectivePermissions"]
    assert set(schema["required"]) == {"scope", "permissions", "system_tier"}
    assert schema["additionalProperties"] is False
    assert schema["properties"]["system_tier"]["type"] == "boolean"

    admin_response = spec["paths"]["/users/{user_id}/effective-permissions"]["get"]["responses"][
        "200"
    ]
    assert admin_response["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/EffectivePermissions"
    }
