"""Async Keycloak Admin REST operations for in-app user provisioning (slice S-user-create).

This runs inside a FastAPI request handler, so it uses ``httpx.AsyncClient`` — unlike
``services/keycloak_admin.py`` (CLI) and ``services/backup/realm_export.py`` (Celery worker), which
are synchronous. A blocking client here would stall the event loop across several round trips.

FAIL CLOSED. ``realm_export.py`` deliberately swallows every error because a Keycloak outage must
not fail the nightly backup; provisioning has the opposite obligation — a half-known state must
surface, never be papered over.

Two behaviours are inherited from ``scripts/new-keycloak-user.sh`` and are load-bearing:

* the admin ``GET /users?username=`` query is a CONTAINS match (``ann`` also returns ``joann``), so
  ``exact=true`` is sent AND the returned username is re-verified;
* a FAILED lookup is not proof of absence — a transient 403/5xx raises, and so does a NON-EMPTY
  response in which no item's username matches exactly (Keycloak normalizes usernames, so
  provisioning ``JDoe`` while ``jdoe`` already exists must never read as absent). Only an EMPTY
  response is "definitively absent" and may fall through to CREATE.
"""

from __future__ import annotations

import types
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

_TIMEOUT = 15.0


class KeycloakProvisioningError(RuntimeError):
    """A provisioning operation could not be completed safely."""


class KeycloakNotConfigured(KeycloakProvisioningError):
    """Keycloak admin credentials are absent — fail closed rather than skipping silently."""


class KeycloakUnavailable(KeycloakProvisioningError):
    """Keycloak was unreachable or returned an unusable response."""


class KeycloakConflict(KeycloakProvisioningError):
    """Keycloak refused the create as a duplicate. ``field`` is ``username`` or ``email``."""

    def __init__(self, field: str, message: str) -> None:
        self.field = field
        super().__init__(message)


class KeycloakRejected(KeycloakProvisioningError):
    """Keycloak refused the create as invalid input — a 4xx other than 409 (an invalid email, or a
    value the realm's user-profile validation rejects). Distinct from ``KeycloakUnavailable``: the
    dependency IS reachable and retrying the same, unchanged form cannot succeed. ``detail`` is a
    bounded, sanitised explanation built by ``_bounded_message`` — never the raw response body."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True)
class UserLookup:
    """A DEFINITIVE lookup outcome. A failure raises instead of being represented here, so a
    caller cannot mistake "we could not tell" for "absent"."""

    found: bool
    subject: str | None = None


class KeycloakProvisioningClient:
    def __init__(
        self,
        *,
        base_url: str,
        realm: str,
        admin_user: str,
        admin_password: str,
        admin_realm: str = "master",
        _transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = (base_url or "").rstrip("/")
        self._realm = quote(realm or "", safe="")
        self._admin_realm = quote(admin_realm, safe="")
        self._admin_user = admin_user
        self._admin_password = admin_password
        self._transport = _transport
        self._client: httpx.AsyncClient | None = None
        self._token: str | None = None

    async def __aenter__(self) -> KeycloakProvisioningClient:
        self._client = httpx.AsyncClient(
            base_url=self._base_url, timeout=_TIMEOUT, transport=self._transport
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: types.TracebackType | None,
    ) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _headers(self) -> dict[str, str]:
        if not all((self._base_url, self._realm, self._admin_user, self._admin_password)):
            raise KeycloakNotConfigured("Keycloak admin credentials are not configured")
        if self._token is None:
            if self._client is None:
                raise KeycloakUnavailable("Client not initialized; use async context manager")
            try:
                response = await self._client.post(
                    f"/realms/{self._admin_realm}/protocol/openid-connect/token",
                    data={
                        "grant_type": "password",
                        "client_id": "admin-cli",
                        "username": self._admin_user,
                        "password": self._admin_password,
                    },
                )
                response.raise_for_status()
                body = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise KeycloakUnavailable(f"Keycloak admin token request failed: {exc}") from exc
            if not isinstance(body, dict):
                # A 200 with a JSON body that isn't an object (`null`, an array, ...) — `.json()`
                # succeeds, so this must be checked explicitly before `.get`, mirroring
                # `find_user_by_username`'s `isinstance(body, list)` guard below.
                raise KeycloakUnavailable("Keycloak token response was not an object")
            token = body.get("access_token")
            if not isinstance(token, str) or not token:
                raise KeycloakUnavailable("Keycloak token response omitted access_token")
            self._token = token
        return {"Authorization": f"Bearer {self._token}"}

    async def find_user_by_username(self, username: str) -> UserLookup:
        headers = await self._headers()
        client = self._client
        if client is None:
            raise KeycloakUnavailable("Client not initialized; use async context manager")
        try:
            response = await client.get(
                f"/admin/realms/{self._realm}/users",
                params={"username": username, "exact": "true"},
                headers=headers,
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # NOT absence — a transient failure must never fall through to CREATE.
            raise KeycloakUnavailable(f"Keycloak user lookup failed: {exc}") from exc
        if not isinstance(body, list):
            raise KeycloakUnavailable("Keycloak users response was not a list")
        for item in body:
            if not isinstance(item, dict):
                continue
            # Re-verify: `exact=true` guards against contains-match hazards;
            # never act on an account we did not ask for.
            if item.get("username") == username and isinstance(item.get("id"), str):
                return UserLookup(found=True, subject=item["id"])
        if body:
            # A NON-EMPTY response with no exact match is a lookup FAILURE, not absence: Keycloak
            # normalizes usernames (lowercases them), so provisioning `JDoe` when `jdoe` already
            # exists returns exactly this shape. Reporting absent here would let the caller CREATE
            # and 409 against that very account instead of surfacing the "link the existing
            # account" affordance — the same hazard `scripts/new-keycloak-user.sh`'s `user_sub`
            # refuses to act on. Only a genuinely EMPTY response is definitively absent.
            returned = sorted(
                {
                    item["username"]
                    for item in body
                    if isinstance(item, dict) and isinstance(item.get("username"), str)
                }
            )
            raise KeycloakUnavailable(
                f"Keycloak username lookup mismatch: asked for {username!r}, got {returned!r}"
            )
        return UserLookup(found=False)

    async def create_user(
        self,
        *,
        username: str,
        email: str | None,
        first_name: str | None,
        last_name: str | None,
    ) -> str:
        headers = await self._headers()
        client = self._client
        if client is None:
            raise KeycloakUnavailable("Client not initialized; use async context manager")
        # No `credentials` key: the account is created WITHOUT a password.
        # The credential is set only after the PostgreSQL row commits,
        # so a failed write leaves an unusable orphan.
        payload: dict[str, Any] = {"username": username, "enabled": True}
        if email:
            payload["email"] = email
            payload["emailVerified"] = True
        if first_name:
            payload["firstName"] = first_name
        if last_name:
            payload["lastName"] = last_name
        try:
            response = await client.post(
                f"/admin/realms/{self._realm}/users", headers=headers, json=payload
            )
        except httpx.HTTPError as exc:
            raise KeycloakUnavailable(f"Keycloak user create failed: {exc}") from exc
        if response.status_code == 409:
            raise await self._classify_create_conflict(username, response)
        if 400 <= response.status_code < 500:
            # Any other 4xx is rejected input, not an outage: the dependency IS reachable, and
            # retrying the identical form cannot succeed (an invalid email, or a value the realm's
            # user-profile validation refuses). Never surface the raw body — only a bounded,
            # sanitised explanation (`_bounded_message`).
            raise KeycloakRejected(_bounded_message(response))
        if response.status_code not in (200, 201):
            raise KeycloakUnavailable(f"Keycloak user create returned {response.status_code}")
        location = response.headers.get("Location", "")
        subject = location.rstrip("/").rsplit("/", 1)[-1] if location else ""
        if subject:
            return subject
        # Keycloak normally returns the new id in Location; fall back to an exact re-read.
        lookup = await self.find_user_by_username(username)
        if not lookup.found or lookup.subject is None:
            raise KeycloakUnavailable("Keycloak created the account but its id could not be read")
        return lookup.subject

    async def _classify_create_conflict(
        self, username: str, response: httpx.Response
    ) -> KeycloakConflict:
        """Classify a create 409 from ground truth, not the error message.

        Keycloak's duplicate-user 409 commonly reports "User exists with same username or email" —
        a message containing "email" even when the request supplied none at all and the collision
        is purely on username. Re-read the username with the same ``exact=true``, re-verified
        lookup used elsewhere (``find_user_by_username``): if it now resolves, the conflict IS the
        username; if it is definitively absent, the collision must be the email — create only
        409s on one of the two unique fields. Only if the re-read itself fails do we fall back to
        the message heuristic: a lookup outage must not escape as a mismatched conflict, and must
        not be guessed at either.
        """
        try:
            lookup = await self.find_user_by_username(username)
        except KeycloakUnavailable:
            return KeycloakConflict(
                _conflict_field(response), "Keycloak refused the account as a duplicate"
            )
        field = "username" if lookup.found else "email"
        return KeycloakConflict(field, "Keycloak refused the account as a duplicate")

    async def set_temporary_password(self, *, subject: str, password: str) -> None:
        headers = await self._headers()
        client = self._client
        if client is None:
            raise KeycloakUnavailable("Client not initialized; use async context manager")
        subject_path = quote(subject, safe="")
        try:
            response = await client.put(
                f"/admin/realms/{self._realm}/users/{subject_path}/reset-password",
                headers=headers,
                json={"type": "password", "value": password, "temporary": True},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            # The message must never interpolate `password`.
            raise KeycloakUnavailable(f"Keycloak set-password failed: {exc}") from exc


def _conflict_field(response: httpx.Response) -> str:
    """Classify a Keycloak 409 as an email or username duplicate — the FALLBACK heuristic, used
    only when ``_classify_create_conflict``'s ground-truth re-read is itself unavailable.

    ``loginWithEmailAllowed`` is true in the shipped realm and duplicate emails are disallowed, so a
    conflict can come from either field and the operator needs to know which one to edit. Keycloak's
    own combined message ("User exists with same username or email") mentions BOTH words — that must
    NOT be read as proof of an email collision, or a pure username collision (the common case, and
    the one supplying no email at all) gets wrongly told its email collided. Prefer "username"
    whenever the message is ambiguous (mentions both, or neither): a username conflict has a
    recovery path (the "link the existing account" affordance) that guessing "email" would hide.
    """
    try:
        body = response.json()
    except ValueError:
        return "username"
    message = str(body.get("errorMessage", "") if isinstance(body, dict) else "").lower()
    return "email" if "email" in message and "username" not in message else "username"


_MAX_REJECTED_DETAIL = 200


def _bounded_message(response: httpx.Response) -> str:
    """A short, sanitised explanation for a Keycloak 4xx rejection — never the raw response body.

    Only ``errorMessage`` (Keycloak's own human-readable admin-REST error field, the same one
    ``_conflict_field`` reads) is taken, and it is capped to a bounded length so neither a malformed
    nor a surprisingly large body can inflate a problem detail. This can never carry the temporary
    password (the create payload never contains a credential — see ``create_user``) or the admin
    bearer token (which lives only in this client's own request headers, never in Keycloak's
    response body).
    """
    try:
        body = response.json()
    except ValueError:
        return "Keycloak rejected the request"
    message = body.get("errorMessage") if isinstance(body, dict) else None
    if not isinstance(message, str) or not message:
        return "Keycloak rejected the request"
    return message[:_MAX_REJECTED_DETAIL]
