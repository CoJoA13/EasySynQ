"""Live Keycloak realm export for the backup archive (slice S11, doc 08 §8.1, doc 12 §6.2).

A disaster-recovery restore must be able to recover the identity config (users / roles / federation
mappings) — without the realm, a restored install has no accounts. The worker runs the *api* image
(no ``kcadm.sh``), so the export goes through the Keycloak **Admin REST API over httpx** on the
INTERNAL network (``http://keycloak:8080``), mirroring ``readiness._check_keycloak``: obtain an
admin token (``admin-cli`` password grant on the ``master`` realm) → ``GET /admin/realms/{realm}``.

GRACEFUL DEGRADATION (a hard constraint): a Keycloak outage MUST NOT fail the nightly backup. On any
error this returns ``None`` and the caller records ``legs.realm_export = "absent"`` + a logged
warning. The realm export contains identity data → it rides INSIDE the encrypted archive.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger("easysynq.backup.realm")

_TIMEOUT = 15.0


def realm_name_from_issuer(oidc_issuer: str) -> str:
    """Parse the realm name from ``OIDC_ISSUER`` (``…/realms/<name>``); default ``easysynq``."""
    issuer = (oidc_issuer or "").rstrip("/")
    if "/realms/" in issuer:
        return issuer.rsplit("/realms/", 1)[1] or "easysynq"
    return "easysynq"


def export_realm(
    *,
    base_url: str,
    realm: str,
    admin_user: str,
    admin_password: str,
    admin_realm: str = "master",
) -> dict[str, Any] | None:
    """Return the live realm representation, or ``None`` on ANY failure (caller records absent).
    Never raises — a Keycloak outage degrades the backup, it does not block it."""
    if not (base_url and admin_user and admin_password):
        logger.warning("realm-export: Keycloak admin not configured; recording realm_export:absent")
        return None
    try:
        with httpx.Client(base_url=base_url.rstrip("/"), timeout=_TIMEOUT) as client:
            token_resp = client.post(
                f"/realms/{admin_realm}/protocol/openid-connect/token",
                data={
                    "grant_type": "password",
                    "client_id": "admin-cli",
                    "username": admin_user,
                    "password": admin_password,
                },
            )
            token_resp.raise_for_status()
            access = token_resp.json()["access_token"]
            realm_resp = client.get(
                f"/admin/realms/{realm}", headers={"Authorization": f"Bearer {access}"}
            )
            realm_resp.raise_for_status()
            body = realm_resp.json()
            return body if isinstance(body, dict) else None
    except Exception as exc:  # noqa: BLE001 — an outage must never block the nightly backup
        logger.warning("realm-export: failed (%s); recording realm_export:absent", str(exc)[:200])
        return None
