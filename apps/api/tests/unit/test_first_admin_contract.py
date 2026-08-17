from pathlib import Path

import yaml

OPENAPI = Path(__file__).resolve().parents[4] / "packages/contracts/openapi.yaml"
DECISIONS_REGISTER = OPENAPI.parents[2] / "docs/decisions-register.md"
API_DESIGN = OPENAPI.parents[2] / "docs/15-api-design.md"
ADMINISTRATOR_MANUAL = OPENAPI.parents[2] / "docs/manuals/administrator-it-manual.md"
VISION_SCOPE = OPENAPI.parents[2] / "docs/01-vision-and-scope.md"
SECURITY_AUDIT = OPENAPI.parents[2] / "docs/12-security-and-audit.md"

CURRENT_FIRST_ADMIN_SOURCES = {
    "bootstrap secret helper": (
        OPENAPI.parents[2] / "apps/api/src/easysynq_api/services/setup/bootstrap.py",
        "/setup/administrator",
        ("/setup/bootstrap",),
    ),
    "system configuration model": (
        OPENAPI.parents[2] / "apps/api/src/easysynq_api/db/models/system_config.py",
        "/setup/administrator",
        ("/setup/bootstrap",),
    ),
    "administrator-set guard": (
        OPENAPI.parents[2] / "apps/api/src/easysynq_api/services/authz/admin_guard.py",
        "System Administrator",
        ("qmsadmin",),
    ),
}


def _markdown_section(document: str, heading: str) -> str:
    start = document.index(heading)
    remaining = document[start + len(heading) :]
    next_heading = remaining.find("\n## ")
    if next_heading == -1:
        return document[start:]
    return document[start : start + len(heading) + next_heading]


def _assert_bounded_public_application_api_authority(
    vision_scope: str,
    security_audit: str,
) -> None:
    n8 = next(line for line in vision_scope.splitlines() if line.startswith("| N8 |"))
    anti_automation = next(
        line for line in security_audit.splitlines() if "Anti-automation on auth" in line
    )
    ssrf = next(line for line in security_audit.splitlines() if "**A10 SSRF**" in line)

    legacy_absolute_claims = (
        "All access is authenticated and authorized",
        "no anonymous endpoints",
        "no anonymous/public endpoints",
    )
    current_authority = "\n".join((n8, anti_automation, ssrf))
    for claim in legacy_absolute_claims:
        assert claim not in current_authority

    bounded_heading = "### 2.6 Bounded public health and first-run setup exceptions"
    assert bounded_heading in security_audit
    bounded_exception = _markdown_section(security_audit, bounded_heading)
    normalized_exception = " ".join(bounded_exception.replace("`", "").split())

    for required_scope in ("QMS content", "customer/site data", "ordinary application operations"):
        assert required_scope in n8

    for route in (
        "GET /healthz",
        "GET /readyz",
        "GET /setup/state",
        "POST /setup/administrator",
        "POST /setup/administrator/acknowledge",
    ):
        assert route in normalized_exception

    for required_safeguard in (
        "bootstrap secret",
        "generic denial",
        "atomic rate limiting",
        "never in URLs",
        "no protected QMS content",
    ):
        assert required_safeguard in normalized_exception


def test_first_administrator_replaces_the_authenticated_bootstrap_contract() -> None:
    spec = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    paths = spec["paths"]
    schemas = spec["components"]["schemas"]
    assert "/setup/bootstrap" not in paths
    provision = paths["/setup/administrator"]["post"]
    acknowledge = paths["/setup/administrator/acknowledge"]["post"]
    assert provision["security"] == []
    assert acknowledge["security"] == []
    assert provision["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/FirstAdministratorRequest"
    }
    assert set(provision["responses"]) == {"200", "201", "403", "409", "422", "429", "502", "503"}
    for status in ("403", "409", "422", "429", "502", "503"):
        assert provision["responses"][status]["$ref"] == (
            "#/components/responses/FirstAdministratorProblemResponse"
        )
    assert acknowledge["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/BootstrapAcknowledgeResponse"
    }
    for status in ("403", "409", "422", "429", "502", "503"):
        assert acknowledge["responses"][status]["$ref"] == (
            "#/components/responses/FirstAdministratorProblemResponse"
        )

    problem_codes = set(schemas["Problem"]["properties"]["code"]["enum"])
    first_administrator_codes = set(
        schemas["FirstAdministratorProblem"]["properties"]["code"]["enum"]
    )
    required_codes = {
        "bootstrap_administrator_exists",
        "bootstrap_credential_superseded",
    }
    assert required_codes <= problem_codes
    assert required_codes <= first_administrator_codes

    for operation in (provision, acknowledge):
        description = operation["responses"]["409"]["description"].lower()
        assert "invalid secret" not in description
        assert "expired" not in description


def test_first_administrator_proof_failures_share_the_generic_403_contract() -> None:
    spec = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    paths = spec["paths"]
    provision_responses = paths["/setup/administrator"]["post"]["responses"]
    acknowledge_responses = paths["/setup/administrator/acknowledge"]["post"]["responses"]

    assert provision_responses["403"]["description"] == (
        "Missing, expired, or invalid bootstrap secret; all return bootstrap_invalid."
    )
    assert "unconsumed" in acknowledge_responses["403"]["description"].lower()
    assert "matching already-consumed" in acknowledge_responses["200"]["description"].lower()
    assert "idempotent" in acknowledge_responses["200"]["description"].lower()

    for path in ("/setup/administrator", "/setup/administrator/acknowledge"):
        responses = paths[path]["post"]["responses"]
        conflict_description = responses["409"]["description"].lower()
        assert "no secret" not in conflict_description
        assert "expired" not in conflict_description


def test_first_administrator_response_never_publishes_keycloak_identity() -> None:
    spec = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    schemas = spec["components"]["schemas"]
    summary = schemas["FirstAdministratorSummary"]
    assert summary["required"] == ["id", "username", "display_name", "email", "status"]
    assert summary["properties"]["display_name"] == {"type": "string"}
    assert "keycloak_subject" not in summary["properties"]
    assert (
        schemas["FirstAdministratorRequest"]["properties"]["display_name"]["pattern"] == r".*\S.*"
    )
    response = schemas["FirstAdministratorProvisioned"]
    assert response["required"] == [
        "administrator",
        "temporary_password",
        "credential_receipt",
        "password_delivery",
    ]
    assert response["properties"]["credential_receipt"] == {
        "type": "string",
        "minLength": 43,
        "maxLength": 43,
        "pattern": "^[A-Za-z0-9_-]{43}$",
    }
    assert response["properties"]["password_delivery"]["enum"] == ["shown_once"]
    acknowledge = schemas["BootstrapAcknowledgeRequest"]
    assert acknowledge["required"] == ["secret", "credential_receipt"]
    assert acknowledge["properties"]["credential_receipt"] == {
        "type": "string",
        "minLength": 43,
        "maxLength": 43,
        "pattern": "^[A-Za-z0-9_-]{43}$",
    }
    assert schemas["Problem"]["properties"]["bound_username"] == {"type": ["string", "null"]}
    assert "keycloak_subject" not in schemas["FirstAdministratorProblem"]["properties"]
    assert (
        schemas["FirstAdministratorProblem"]["properties"]["code"]["enum"]
        == schemas["Problem"]["properties"]["code"]["enum"]
    )


def test_current_first_administrator_authority_names_the_hardened_recovery_boundary() -> None:
    decisions_register = DECISIONS_REGISTER.read_text(encoding="utf-8")
    api_design = API_DESIGN.read_text(encoding="utf-8")

    assert "active shown credential generation" in decisions_register
    assert "bootstrap_administrator_exists" in api_design
    assert "bound first administrator" in api_design


def test_current_authority_bounds_public_access_to_health_and_first_run_setup() -> None:
    _assert_bounded_public_application_api_authority(
        VISION_SCOPE.read_text(encoding="utf-8"),
        SECURITY_AUDIT.read_text(encoding="utf-8"),
    )


def test_current_first_administrator_source_comments_name_only_live_invariants() -> None:
    for label, (path, required, retired) in CURRENT_FIRST_ADMIN_SOURCES.items():
        source = path.read_text(encoding="utf-8")
        assert required in source, f"{label} does not name the current invariant"
        for term in retired:
            assert term not in source, f"{label} retains retired current-source term {term}"


def test_administrator_blocker_recovery_is_narrow_and_externally_recorded() -> None:
    manual = ADMINISTRATOR_MANUAL.read_text(encoding="utf-8")
    recovery_start = manual.index(
        "If an unrelated System Administrator assignment already blocks the public "
        "first-administrator flow"
    )
    recovery_end = manual.index("### 5.4 ", recovery_start)
    recovery = manual[recovery_start:recovery_end]

    command = (
        "./scripts/easysynq setup release-administrator-blocker \\\n"
        "  --subject <keycloak-subject> --org <short-code>"
    )
    assert command in recovery
    assert "exactly `UNINITIALIZED`" in recovery
    assert "refuses the user linked to an active bootstrap claim" in recovery
    assert "removes only the exact named user's System Administrator assignment" in recovery
    assert "does not call Keycloak" in recovery
    assert "resume the normal `/setup` browser flow" in recovery
    assert "independent incident or change record" in recovery
    assert (
        "record the command, operator, reason, exact\nsubject, organization, and time" in recovery
    )
