"""Live auth-reachability probe for the G-D setup gate (slice S8c, doc 08 §9).

The app always authenticates via Keycloak (OIDC); upstream federation (LDAP/OIDC/SAML) is configured
*in Keycloak* and is out of scope here. So the ES-app-level live check is: the configured OIDC
issuer publishes a well-formed discovery document whose ``issuer`` matches and that advertises a
``jwks_uri`` — i.e. the realm the app validates tokens against is reachable + correctly wired. A
misconfigured/unreachable issuer would strand the org, which is exactly what G-D exists to catch.

Mirrors ``storage.worm_probe``: a real network call with a short timeout that **never raises** — a
failure is reported as ``(False, detail)`` so the caller turns it into a clean 422, not a 500/hang.
The integration test monkeypatches this (the test issuer is not reachable).
"""

from __future__ import annotations

import httpx

_DISCOVERY_PATH = "/.well-known/openid-configuration"
_TIMEOUT_SECONDS = 5.0


async def probe_oidc_discovery(issuer: str) -> tuple[bool, str]:
    """GET the issuer's OIDC discovery doc and confirm it is well-formed + self-consistent.

    Returns ``(verified, detail)``. Verified iff: HTTP 200, the doc's ``issuer`` equals the
    configured issuer (a common misconfiguration that breaks token validation), and a ``jwks_uri``
    is advertised. Any network/parse error → ``(False, <reason>)``.
    """
    issuer = (issuer or "").rstrip("/")
    if not issuer:
        return False, "no OIDC issuer is configured"
    url = f"{issuer}{_DISCOVERY_PATH}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            return False, f"discovery document returned HTTP {resp.status_code}"
        doc = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        return False, f"OIDC issuer not reachable: {type(exc).__name__}"[:200]

    if doc.get("issuer", "").rstrip("/") != issuer:
        return False, "discovery 'issuer' does not match the configured issuer"
    if not doc.get("jwks_uri"):
        return False, "discovery document advertises no jwks_uri"
    return True, "OIDC issuer reachable and well-formed"
