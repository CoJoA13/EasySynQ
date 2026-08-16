from pathlib import Path

import yaml

OPENAPI = Path(__file__).resolve().parents[4] / "packages/contracts/openapi.yaml"


def test_first_administrator_replaces_the_authenticated_bootstrap_contract() -> None:
    spec = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    paths = spec["paths"]
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


def test_first_administrator_response_never_publishes_keycloak_identity() -> None:
    spec = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    schemas = spec["components"]["schemas"]
    summary = schemas["FirstAdministratorSummary"]
    assert summary["required"] == ["id", "username", "display_name", "email", "status"]
    assert summary["properties"]["display_name"] == {"type": "string"}
    assert "keycloak_subject" not in summary["properties"]
    assert schemas["FirstAdministratorRequest"]["properties"]["display_name"]["pattern"] == r".*\S.*"
    response = schemas["FirstAdministratorProvisioned"]
    assert response["required"] == ["administrator", "temporary_password", "password_delivery"]
    assert response["properties"]["password_delivery"]["enum"] == ["shown_once"]
    assert schemas["Problem"]["properties"]["bound_username"] == {"type": ["string", "null"]}
    assert "keycloak_subject" not in schemas["FirstAdministratorProblem"]["properties"]
    assert (
        schemas["FirstAdministratorProblem"]["properties"]["code"]["enum"]
        == schemas["Problem"]["properties"]["code"]["enum"]
    )
