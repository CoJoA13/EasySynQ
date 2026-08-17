from pathlib import Path

import yaml

OPENAPI = Path(__file__).resolve().parents[4] / "packages/contracts/openapi.yaml"
DECISIONS_REGISTER = OPENAPI.parents[2] / "docs/decisions-register.md"
API_DESIGN = OPENAPI.parents[2] / "docs/15-api-design.md"


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
