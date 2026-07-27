"""Small fail-closed Keycloak Admin REST operations used by deployment tooling.

Installer and appliance hostname changes must add the selected SPA callback without replacing
redirect URIs an operator already configured. Keycloak's ``kcadm update -s redirectUris=[...]``
replaces that whole array, so this helper reads the complete client representation, appends one
missing URI, and PUTs the representation back over the internal Docker network.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

_TIMEOUT = 15.0


class KeycloakAdminError(RuntimeError):
    """A deployment-time Keycloak operation could not be completed safely."""


def ensure_client_redirect_uri(
    *,
    base_url: str,
    realm: str,
    client_id: str,
    redirect_uri: str,
    admin_user: str,
    admin_password: str,
    admin_realm: str = "master",
    _transport: httpx.BaseTransport | None = None,
) -> bool:
    """Append ``redirect_uri`` when absent, preserving the client's full existing representation.

    Returns ``True`` when Keycloak was updated and ``False`` when the URI was already present.
    Raises ``KeycloakAdminError`` for missing configuration, ambiguous clients, or HTTP/schema
    failures so an installer cannot report success after silently losing login callbacks.
    """
    if not all((base_url, realm, client_id, redirect_uri, admin_user, admin_password)):
        raise KeycloakAdminError("Keycloak admin settings and redirect URI are required")

    realm_path = quote(realm, safe="")
    admin_realm_path = quote(admin_realm, safe="")
    try:
        with httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=_TIMEOUT,
            transport=_transport,
        ) as client:
            token_response = client.post(
                f"/realms/{admin_realm_path}/protocol/openid-connect/token",
                data={
                    "grant_type": "password",
                    "client_id": "admin-cli",
                    "username": admin_user,
                    "password": admin_password,
                },
            )
            token_response.raise_for_status()
            access_token = token_response.json().get("access_token")
            if not isinstance(access_token, str) or not access_token:
                raise KeycloakAdminError("Keycloak token response omitted access_token")
            headers = {"Authorization": f"Bearer {access_token}"}

            matches_response = client.get(
                f"/admin/realms/{realm_path}/clients",
                params={"clientId": client_id},
                headers=headers,
            )
            matches_response.raise_for_status()
            matches_body = matches_response.json()
            if not isinstance(matches_body, list):
                raise KeycloakAdminError("Keycloak clients response was not a list")
            matches = [
                item
                for item in matches_body
                if isinstance(item, dict) and item.get("clientId") == client_id
            ]
            if len(matches) != 1 or not isinstance(matches[0].get("id"), str):
                raise KeycloakAdminError(
                    f"expected exactly one Keycloak client with clientId={client_id!r}"
                )
            client_uuid = quote(matches[0]["id"], safe="")

            detail_response = client.get(
                f"/admin/realms/{realm_path}/clients/{client_uuid}", headers=headers
            )
            detail_response.raise_for_status()
            raw_representation = detail_response.json()
            if not isinstance(raw_representation, dict):
                raise KeycloakAdminError("Keycloak client representation was not an object")
            representation: dict[str, Any] = raw_representation
            raw_redirects = representation.get("redirectUris", [])
            if not isinstance(raw_redirects, list) or not all(
                isinstance(item, str) for item in raw_redirects
            ):
                raise KeycloakAdminError("Keycloak client redirectUris was not a string array")
            redirects: list[str] = raw_redirects
            if redirect_uri in redirects:
                return False

            representation["redirectUris"] = [*redirects, redirect_uri]
            update_response = client.put(
                f"/admin/realms/{realm_path}/clients/{client_uuid}",
                headers=headers,
                json=representation,
            )
            update_response.raise_for_status()
            return True
    except KeycloakAdminError:
        raise
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        raise KeycloakAdminError(f"Keycloak Admin API request failed: {exc}") from exc
