# S-user-create Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Creating a user becomes one form in the EasySynQ Admin SPA — the API provisions the Keycloak account itself and returns a generated temporary password shown once — removing the shell round trip to `scripts/new-keycloak-user.sh`.

**Architecture:** A new async Keycloak Admin REST client (`services/keycloak_provisioning.py`) is driven by two new additive endpoints on the existing users router. `POST /users/provision` creates the Keycloak account *without a credential*, commits the `app_user` row plus role assignments in one PostgreSQL transaction, then sets the temporary password — so a failed write leaves an unusable orphan that the existing invite endpoint adopts. `POST /users/{id}/temporary-password` reissues a credential using the same client call. The SPA gains a `CreateUserModal` with a show-once password panel shared with the Manage drawer's reset action.

**Tech Stack:** FastAPI / Python 3.12 · SQLAlchemy 2.x async · Alembic · httpx (`AsyncClient` + `MockTransport`) · React 19 / TypeScript / Mantine · vitest + MSW + jest-axe.

**Spec:** [`docs/superpowers/specs/2026-08-03-user-create-design.md`](../specs/2026-08-03-user-create-design.md)

## Global Constraints

- **Branch:** `feat/s-user-create`, already created, currently at the spec commit. Never commit to `main`.
- **No new permission key.** The catalog stays at **102**. `POST /users/provision` and `POST /users/{id}/temporary-password` both gate on the existing **`user.create`**; supplying `role_ids` *additionally* requires **`permission.grant`**.
- **Migration head is `0084`.** This slice adds exactly one migration, **`0085`**, containing only `ALTER TYPE event_type ADD VALUE`. No table, column, or index changes.
- **No Keycloak in CI (D1).** Every Keycloak interaction is tested through `httpx.MockTransport`. Never add a live Keycloak dependency to any test.
- **`POST /api/v1/users` is frozen.** Its request body, response shape, status codes, and existing tests must remain byte-identical. It is the link implementation and the fallback.
- **EasySynQ never deletes a Keycloak account.** No compensating delete in any path, ever.
- **The temporary password is never persisted, never logged, and never placed in an audit payload, problem detail, or error message.**
- **The new client must be `async`.** `services/keycloak_admin.py` and `services/backup/realm_export.py` are synchronous because they run in CLI and Celery contexts. This one runs inside a FastAPI request handler, so a sync `httpx.Client` would block the event loop across three to four Keycloak round trips. Use `httpx.AsyncClient`; `httpx.MockTransport` supports async.
- **Do not copy `realm_export.py`'s fail-open posture.** That module returns `None` on any error because a backup must degrade rather than fail. Provisioning must **fail closed**.
- **R61:** no site-specific data in any file. `scripts/check-no-site-data.sh` must pass. Use `jdoe` / `example.local` style placeholders.
- **Ruff `--fix` PostToolUse hook:** it strips an import the moment nothing references it. Add the *using* code before or in the same edit as the import, and read a `NameError` on a symbol you never touched as this hook rather than your logic.

---

## File Structure

**Created**

| File | Responsibility |
|---|---|
| `apps/api/src/easysynq_api/domain/identity/__init__.py` | Package marker. |
| `apps/api/src/easysynq_api/domain/identity/temp_password.py` | Pure temporary-password generation and realm-policy conformance. No I/O. |
| `apps/api/src/easysynq_api/services/keycloak_provisioning.py` | Async, fail-closed Keycloak Admin REST client: exact lookup, create-without-credential, set temporary password. |
| `migrations/versions/0085_user_credential_issued.py` | Additive `event_type` enum value. |
| `apps/api/tests/unit/test_temp_password.py` | Policy conformance and the short-username trap. |
| `apps/api/tests/unit/test_keycloak_provisioning.py` | MockTransport coverage of the client. |
| `apps/api/tests/integration/test_users_provision.py` | Endpoint behaviour, 409 branches, recovery, authz. |
| `apps/web/src/admin/ShowOncePassword.tsx` | The show-once credential panel, shared by create and reset. |
| `apps/web/src/admin/CreateUserModal.tsx` | The create form, collision/link state, and success panel. |
| `apps/web/src/admin/CreateUserModal.test.tsx` | Component tests for the above. |

**Modified**

| File | Change |
|---|---|
| `apps/api/src/easysynq_api/db/models/_audit_enums.py:128` | Add the `USER_CREDENTIAL_ISSUED` member. |
| `apps/api/src/easysynq_api/api/users.py` | Two new routes; `provision` declared before `/users/{user_id}`. |
| `packages/contracts/openapi.yaml` | Both new endpoints and their problem codes. |
| `docs/15-api-design.md` | Both new endpoints and their gates. |
| `apps/web/src/lib/types.ts` | `ProvisionUserRequest`, `ProvisionedUser`. |
| `apps/web/src/admin/UsersAdmin.tsx` | Remove the Invite modal, add Create, add the Manage reset action. |
| `apps/web/src/admin/UsersAdmin.test.tsx` | Update for the removed Invite button; cover the reset action. |

---

## Task 1: Temporary-password generation

**Files:**
- Create: `apps/api/src/easysynq_api/domain/identity/__init__.py`
- Create: `apps/api/src/easysynq_api/domain/identity/temp_password.py`
- Test: `apps/api/tests/unit/test_temp_password.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `generate_temporary_password(username: str) -> str`; `MIN_LENGTH: int`; `satisfies_realm_policy(password: str, username: str) -> bool`.

> **The trap that must not be "improved".** The realm policy is `length(12) and notUsername(undefined)`. Keycloak's `notUsername` means *the password must not equal the username* — it is **not** a substring rule. Do **not** "harden" this into `username not in password`: for a short username such as `a`, every generated password contains that letter and the regeneration loop would never terminate. Enforce equality only, exactly as the realm does.

- [ ] **Step 1: Write the failing test**

Create `apps/api/tests/unit/test_temp_password.py`:

```python
"""Temporary-password generation must satisfy the live realm policy.

The realm (`infra/compose/keycloak/realm-export.json`) sets
`passwordPolicy = length(12) and notUsername(undefined)`. A generated password that violates it
fails only at Keycloak `set-password` time — on site, in front of the person being onboarded. These
tests make that failure impossible to ship.
"""

from __future__ import annotations

import pytest

from easysynq_api.domain.identity.temp_password import (
    MIN_LENGTH,
    generate_temporary_password,
    satisfies_realm_policy,
)


def test_min_length_matches_the_realm_policy_floor() -> None:
    assert MIN_LENGTH >= 12


@pytest.mark.parametrize("username", ["jdoe", "a", "operator", "J.Doe"])
def test_generated_password_satisfies_realm_policy(username: str) -> None:
    for _ in range(50):
        password = generate_temporary_password(username)
        assert len(password) >= MIN_LENGTH
        assert password.lower() != username.lower()
        assert satisfies_realm_policy(password, username)


def test_generation_is_not_deterministic() -> None:
    assert len({generate_temporary_password("jdoe") for _ in range(20)}) > 1


def test_single_character_username_terminates() -> None:
    # Guards the substring-rule trap: `notUsername` is equality, not containment. A containment
    # rule would make this call loop forever for a one-letter username.
    assert generate_temporary_password("a")


def test_policy_rejects_password_equal_to_username_case_insensitively() -> None:
    assert satisfies_realm_policy("Xk4m-Pq7r-Ts2v-Wy8n-Bd3h", "jdoe") is True
    assert satisfies_realm_policy("JDOE", "jdoe") is False
    assert satisfies_realm_policy("short", "jdoe") is False


def test_alphabet_excludes_visually_ambiguous_characters() -> None:
    # The value is transcribed by hand or read aloud, so 0/O and 1/l/I must not appear.
    generated = "".join(generate_temporary_password("jdoe") for _ in range(50))
    for ambiguous in "0O1lI":
        assert ambiguous not in generated
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd apps/api && uv run pytest tests/unit/test_temp_password.py -v
```

Expected: collection error — `ModuleNotFoundError: No module named 'easysynq_api.domain.identity'`.

- [ ] **Step 3: Write the implementation**

Create `apps/api/src/easysynq_api/domain/identity/__init__.py` (empty file).

Create `apps/api/src/easysynq_api/domain/identity/temp_password.py`:

```python
"""Temporary-password generation for in-app Keycloak provisioning (slice S-user-create).

The value is generated server-side, handed to the operator once, and set on the Keycloak account as
TEMPORARY so Keycloak forces the person to choose their own at first login. It is never persisted,
logged, or placed in an audit payload.

The realm policy is ``length(12) and notUsername(undefined)``. ``notUsername`` is an EQUALITY rule —
the password must not BE the username. It is deliberately not implemented as a substring rule: for a
one-character username every candidate would contain it and the retry loop would never terminate.
"""

from __future__ import annotations

import secrets

# Visually unambiguous — the value is transcribed by hand or read aloud, so 0/O and 1/l/I are out.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
_GROUPS = 5
_GROUP_LEN = 4

# The realm floor is 12; we generate 20 alphanumerics in hyphen-separated groups, well clear of it.
MIN_LENGTH = 12

_MAX_ATTEMPTS = 8


def satisfies_realm_policy(password: str, username: str) -> bool:
    """Mirror the realm's ``length(12) and notUsername(undefined)`` policy."""
    return len(password) >= MIN_LENGTH and password.lower() != username.lower()


def generate_temporary_password(username: str) -> str:
    """Return a fresh temporary password satisfying the realm policy.

    Raises ``RuntimeError`` if a conforming value cannot be produced, rather than returning one that
    Keycloak would reject at ``set-password`` time.
    """
    for _ in range(_MAX_ATTEMPTS):
        groups = [
            "".join(secrets.choice(_ALPHABET) for _ in range(_GROUP_LEN)) for _ in range(_GROUPS)
        ]
        candidate = "-".join(groups)
        if satisfies_realm_policy(candidate, username):
            return candidate
    raise RuntimeError("could not generate a policy-conforming temporary password")
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd apps/api && uv run pytest tests/unit/test_temp_password.py -v
```

Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/easysynq_api/domain/identity apps/api/tests/unit/test_temp_password.py
git commit -m "feat(identity): realm-policy-conforming temporary password generator"
```

---

## Task 2: Async Keycloak provisioning client

**Files:**
- Create: `apps/api/src/easysynq_api/services/keycloak_provisioning.py`
- Test: `apps/api/tests/unit/test_keycloak_provisioning.py`

**Interfaces:**
- Consumes: nothing from Task 1 (the endpoint composes them).
- Produces:
  - `class KeycloakProvisioningError(RuntimeError)`
  - `class KeycloakNotConfigured(KeycloakProvisioningError)`
  - `class KeycloakUnavailable(KeycloakProvisioningError)`
  - `class KeycloakConflict(KeycloakProvisioningError)` with attribute `field: str` (`"username"` or `"email"`)
  - `@dataclass(frozen=True) class UserLookup` with `found: bool`, `subject: str | None`
  - `class KeycloakProvisioningClient` — async context manager with
    `find_user_by_username(username: str) -> UserLookup`,
    `create_user(*, username: str, email: str | None, first_name: str | None, last_name: str | None) -> str`,
    `set_temporary_password(*, subject: str, password: str) -> None`

> **Two behaviours lifted from `scripts/new-keycloak-user.sh`, both load-bearing.**
> 1. The admin `GET /users?username=` query is a **contains** match — `ann` also returns `joann`. Send `exact=true` **and** re-verify the returned username before acting on it.
> 2. A **failed lookup is not proof of absence.** A transient 403/5xx must never fall through to CREATE. This client raises `KeycloakUnavailable` for a failure and returns `UserLookup(found=False)` only for a definitive absence, so the two are impossible to conflate at the call site.

- [ ] **Step 1: Write the failing test**

Create `apps/api/tests/unit/test_keycloak_provisioning.py`:

```python
"""Async Keycloak provisioning client — mock transport only (D1: no live identity service in CI)."""

from __future__ import annotations

import json

import httpx
import pytest

from easysynq_api.services.keycloak_provisioning import (
    KeycloakConflict,
    KeycloakNotConfigured,
    KeycloakProvisioningClient,
    KeycloakUnavailable,
)

_KWARGS = {
    "base_url": "http://keycloak:8080",
    "realm": "easysynq",
    "admin_user": "admin",
    "admin_password": "secret",
}


def _client(handler: object) -> KeycloakProvisioningClient:
    return KeycloakProvisioningClient(
        **_KWARGS,  # type: ignore[arg-type]
        _transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )


def _token_ok(request: httpx.Request) -> httpx.Response | None:
    if request.method == "POST" and request.url.path.endswith("/protocol/openid-connect/token"):
        return httpx.Response(200, json={"access_token": "admin-token"})
    return None


@pytest.mark.asyncio
async def test_lookup_requires_exact_and_reverifies_the_returned_username() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_ok(request)
        if token is not None:
            return token
        if request.method == "GET" and request.url.path == "/admin/realms/easysynq/users":
            seen.update(dict(request.url.params))
            # Keycloak echoing a DIFFERENT user (the contains-match hazard) must not be accepted.
            return httpx.Response(200, json=[{"id": "sub-joann", "username": "joann"}])
        raise AssertionError(f"unexpected: {request.method} {request.url}")

    async with _client(handler) as kc:
        result = await kc.find_user_by_username("ann")

    assert seen["exact"] == "true"
    assert seen["username"] == "ann"
    assert result.found is False
    assert result.subject is None


@pytest.mark.asyncio
async def test_lookup_returns_the_subject_on_an_exact_match() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_ok(request)
        if token is not None:
            return token
        return httpx.Response(200, json=[{"id": "sub-jdoe", "username": "jdoe"}])

    async with _client(handler) as kc:
        result = await kc.find_user_by_username("jdoe")

    assert result.found is True
    assert result.subject == "sub-jdoe"


@pytest.mark.asyncio
async def test_lookup_failure_is_not_absence() -> None:
    """A transient 5xx must raise — never fall through to CREATE as if the user were absent."""

    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_ok(request)
        if token is not None:
            return token
        return httpx.Response(503, json={"error": "unavailable"})

    async with _client(handler) as kc:
        with pytest.raises(KeycloakUnavailable):
            await kc.find_user_by_username("jdoe")


@pytest.mark.asyncio
async def test_create_user_sends_no_credential_and_returns_the_subject() -> None:
    posted: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_ok(request)
        if token is not None:
            return token
        if request.method == "POST" and request.url.path == "/admin/realms/easysynq/users":
            posted.append(json.loads(request.content))
            return httpx.Response(
                201,
                headers={"Location": "http://keycloak:8080/admin/realms/easysynq/users/sub-new"},
            )
        raise AssertionError(f"unexpected: {request.method} {request.url}")

    async with _client(handler) as kc:
        subject = await kc.create_user(
            username="jdoe", email="jdoe@example.local", first_name="J", last_name="Doe"
        )

    assert subject == "sub-new"
    body = posted[0]
    assert body["username"] == "jdoe"
    assert body["enabled"] is True
    # The account is created WITHOUT a credential; the password is set only after the PG commit.
    assert "credentials" not in body


@pytest.mark.asyncio
async def test_create_user_maps_a_conflict_to_the_offending_field() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_ok(request)
        if token is not None:
            return token
        return httpx.Response(409, json={"errorMessage": "User exists with same email"})

    async with _client(handler) as kc:
        with pytest.raises(KeycloakConflict) as excinfo:
            await kc.create_user(
                username="jdoe", email="taken@example.local", first_name=None, last_name=None
            )

    assert excinfo.value.field == "email"


@pytest.mark.asyncio
async def test_set_temporary_password_marks_the_credential_temporary() -> None:
    sent: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        token = _token_ok(request)
        if token is not None:
            return token
        if request.method == "PUT" and request.url.path.endswith("/users/sub-new/reset-password"):
            sent.append(json.loads(request.content))
            return httpx.Response(204)
        raise AssertionError(f"unexpected: {request.method} {request.url}")

    async with _client(handler) as kc:
        await kc.set_temporary_password(subject="sub-new", password="Xk4m-Pq7r-Ts2v-Wy8n-Bd3h")

    assert sent[0]["temporary"] is True
    assert sent[0]["type"] == "password"


@pytest.mark.asyncio
async def test_missing_admin_credentials_fail_closed() -> None:
    client = KeycloakProvisioningClient(
        base_url="http://keycloak:8080", realm="easysynq", admin_user="", admin_password=""
    )
    with pytest.raises(KeycloakNotConfigured):
        async with client as kc:
            await kc.find_user_by_username("jdoe")
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd apps/api && uv run pytest tests/unit/test_keycloak_provisioning.py -v
```

Expected: collection error — `ModuleNotFoundError: No module named 'easysynq_api.services.keycloak_provisioning'`.

- [ ] **Step 3: Write the implementation**

Create `apps/api/src/easysynq_api/services/keycloak_provisioning.py`:

```python
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
* a FAILED lookup is not proof of absence — a transient 403/5xx raises rather than reporting the
  user as absent, because falling through to CREATE would conflict on a real account.
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
            assert self._client is not None
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
                token = response.json().get("access_token")
            except (httpx.HTTPError, ValueError) as exc:
                raise KeycloakUnavailable(f"Keycloak admin token request failed: {exc}") from exc
            if not isinstance(token, str) or not token:
                raise KeycloakUnavailable("Keycloak token response omitted access_token")
            self._token = token
        return {"Authorization": f"Bearer {self._token}"}

    async def find_user_by_username(self, username: str) -> UserLookup:
        headers = await self._headers()
        assert self._client is not None
        try:
            response = await self._client.get(
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
            # Re-verify: `exact=true` is belt-and-braces, but never act on an account we did not ask for.
            if item.get("username") == username and isinstance(item.get("id"), str):
                return UserLookup(found=True, subject=item["id"])
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
        assert self._client is not None
        # No `credentials` key: the account is created WITHOUT a password. The credential is set
        # only after the PostgreSQL row commits, so a failed write leaves an unusable orphan.
        payload: dict[str, Any] = {"username": username, "enabled": True}
        if email:
            payload["email"] = email
            payload["emailVerified"] = True
        if first_name:
            payload["firstName"] = first_name
        if last_name:
            payload["lastName"] = last_name
        try:
            response = await self._client.post(
                f"/admin/realms/{self._realm}/users", headers=headers, json=payload
            )
        except httpx.HTTPError as exc:
            raise KeycloakUnavailable(f"Keycloak user create failed: {exc}") from exc
        if response.status_code == 409:
            raise KeycloakConflict(
                _conflict_field(response), "Keycloak refused the account as a duplicate"
            )
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

    async def set_temporary_password(self, *, subject: str, password: str) -> None:
        headers = await self._headers()
        assert self._client is not None
        subject_path = quote(subject, safe="")
        try:
            response = await self._client.put(
                f"/admin/realms/{self._realm}/users/{subject_path}/reset-password",
                headers=headers,
                json={"type": "password", "value": password, "temporary": True},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            # The message must never interpolate `password`.
            raise KeycloakUnavailable(f"Keycloak set-password failed: {exc}") from exc


def _conflict_field(response: httpx.Response) -> str:
    """Classify a Keycloak 409 as an email or username duplicate.

    ``loginWithEmailAllowed`` is true in the shipped realm and duplicate emails are disallowed, so a
    conflict can come from either field and the operator needs to know which one to edit.
    """
    try:
        body = response.json()
    except ValueError:
        return "username"
    message = body.get("errorMessage", "") if isinstance(body, dict) else ""
    return "email" if "email" in str(message).lower() else "username"
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd apps/api && uv run pytest tests/unit/test_keycloak_provisioning.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Run the API gate**

```bash
cd apps/api && uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src
```

Expected: all clean.

- [ ] **Step 6: Commit**

```bash
git add apps/api/src/easysynq_api/services/keycloak_provisioning.py apps/api/tests/unit/test_keycloak_provisioning.py
git commit -m "feat(identity): async fail-closed Keycloak provisioning client"
```

---

## Task 3: Migration 0085 — the credential-issued audit event

**Files:**
- Create: `migrations/versions/0085_user_credential_issued.py`
- Modify: `apps/api/src/easysynq_api/db/models/_audit_enums.py:128`

**Interfaces:**
- Consumes: nothing.
- Produces: `EventType.USER_CREDENTIAL_ISSUED` (value `"USER_CREDENTIAL_ISSUED"`), consumed by Task 5.

> `0016_user_admin_events.py` added `USER_CREATED`/`USER_STATUS_CHANGED` and is the exact precedent — same shape, same downgrade reasoning. Read it before writing this one.

- [ ] **Step 1: Add the Python enum member**

In `apps/api/src/easysynq_api/db/models/_audit_enums.py`, immediately after the `USER_STATUS_CHANGED` line (128), inside the same commented block:

```python
    USER_CREATED = "USER_CREATED"
    USER_STATUS_CHANGED = "USER_STATUS_CHANGED"
    # Credential issuance for in-app Keycloak provisioning (S-user-create). Records THAT a temporary
    # password was issued — never its value. Added via ALTER TYPE … ADD VALUE in 0085 (the 0011-0016
    # additive pattern); a from-scratch ``upgrade head`` rebuilds the type from EVENT_TYPE_VALUES.
    USER_CREDENTIAL_ISSUED = "USER_CREDENTIAL_ISSUED"
```

- [ ] **Step 2: Write the migration**

Create `migrations/versions/0085_user_credential_issued.py`:

```python
"""USER_CREDENTIAL_ISSUED event — in-app Keycloak credential issuance (slice S-user-create).

S-user-create adds one-step user creation from the Admin SPA: the API provisions the Keycloak
account and issues a temporary password. Creating the user rides the existing ``USER_CREATED``
event; issuing a credential to an EXISTING user has no honest existing event, so this migration adds
one. Reusing ``USER_STATUS_CHANGED`` was rejected — no status changes, and a misleading audit record
is worse than a migration.

The event records THAT a credential was issued. The password itself is never persisted, logged, or
placed in an audit payload.

**No new columns.** ``app_user`` already carries ``keycloak_subject``/``display_name``/``email``/
``status``, so this slice needs only the additive enum value.

Additive enum (the 0011-0016 precedent): ``ALTER TYPE event_type ADD VALUE`` is in-txn-safe on PG16
(no row USES the value here), irreversible → no-op enum downgrade (0001's downgrade DROP TYPEs
``event_type`` wholesale, so the up↔down round-trip still passes). The Python ``EventType`` carries
the new member too (``_audit_enums.py``) so a from-scratch ``upgrade head`` — which rebuilds the type
from ``EVENT_TYPE_VALUES`` — matches a migrated DB.

Revision ID: 0085_user_credential_issued
Revises: 0084_clause7_support_do
Create Date: 2026-08-03
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0085_user_credential_issued"
down_revision: str | None = "0084_clause7_support_do"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'USER_CREDENTIAL_ISSUED'")


def downgrade() -> None:
    # The ADD VALUE on event_type is irreversible in PostgreSQL → no-op (0001's downgrade DROP TYPEs
    # event_type wholesale, so the round-trip still passes). No columns were added.
    pass
```

- [ ] **Step 3: Verify the head is linear**

```bash
cd apps/api && uv run alembic heads
```

Expected: exactly one line, `0085_user_credential_issued (head)`. Two heads means `down_revision` is wrong — fix before continuing.

- [ ] **Step 4: Run the migrations gate**

Run the `/check-migrations` skill (round-trips `alembic up↔down` plus `alembic check` on a throwaway PG16).

Expected: clean. `alembic check` in particular must report no drift — an enum value present in `EVENT_TYPE_VALUES` but missing from the migration would surface here.

- [ ] **Step 5: Commit**

```bash
git add migrations/versions/0085_user_credential_issued.py apps/api/src/easysynq_api/db/models/_audit_enums.py
git commit -m "feat(audit): add USER_CREDENTIAL_ISSUED event (migration 0085)"
```

---

## Task 4: `POST /users/provision`

**Files:**
- Modify: `apps/api/src/easysynq_api/api/users.py`
- Modify: `packages/contracts/openapi.yaml`
- Modify: `docs/15-api-design.md`

**Interfaces:**
- Consumes: `generate_temporary_password` (Task 1); `KeycloakProvisioningClient`, `KeycloakConflict`, `KeycloakNotConfigured`, `KeycloakUnavailable`, `UserLookup` (Task 2).
- Produces: `POST /api/v1/users/provision` returning `{"user": {...}, "temporary_password": str, "password_delivery": "shown_once"}`; problem codes `keycloak_username_exists_unlinked` (carrying member `keycloak_subject`), `user_exists`, `keycloak_email_exists`, `keycloak_unavailable`, `keycloak_not_configured`.

> **Route order.** Declare `provision` **before** the existing `@router.get("/users/{user_id}")`. Methods differ today so there is no live shadow, but the repo's static-before-parametrised rule is cheap to honour and survives a later `POST /users/{id}`.

> **The role leg must not bypass SoD.** Role assignment is gated by `permission.grant`, not `user.create`, and passes `assert_can_assign_role` (`services/authz/pep.py:308`). Assigning roles from the create form does not change that. Require `permission.grant` only when `role_ids` is non-empty, so a plain create still works for a caller holding only `user.create`.

- [ ] **Step 1: Add the imports and request model**

In `apps/api/src/easysynq_api/api/users.py`, extend the existing imports:

```python
from ..config import get_settings
from ..domain.identity.temp_password import generate_temporary_password
from ..services.authz import (
    assert_can_assign_role,
    authorize_or_raise,
    disable_removes_last_admin,
    gather_grants,
    invalidate_user_permissions,
    require,
)
from ..services.authz.pep import get_authz_audit_sink
from ..services.authz.audit import AuthzAuditSink
from ..services.backup.realm_export import realm_name_from_issuer
from ..services.keycloak_provisioning import (
    KeycloakConflict,
    KeycloakNotConfigured,
    KeycloakProvisioningClient,
    KeycloakUnavailable,
)
```

If any of `authorize_or_raise` / `gather_grants` is not exported from `services.authz`, import it from its defining module — check `services/authz/__init__.py` first and adjust rather than guessing. Add the model beside `UserInvite`:

```python
class UserProvision(BaseModel):
    username: str
    display_name: str | None = None
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    role_ids: list[uuid.UUID] = []
```

- [ ] **Step 2: Add the route, declared before `GET /users/{user_id}`**

```python
_permission_grant = require("permission.grant")


def _kc_client() -> KeycloakProvisioningClient:
    settings = get_settings()
    return KeycloakProvisioningClient(
        base_url=settings.keycloak_admin_url,
        realm=realm_name_from_issuer(settings.oidc_issuer),
        admin_user=settings.keycloak_admin_user,
        admin_password=settings.keycloak_admin_password,
    )


@router.post("/users/provision", status_code=status.HTTP_201_CREATED)
async def provision_user(
    body: UserProvision,
    caller: AppUser = Depends(_user_create),
    session: AsyncSession = Depends(get_session),
    sink: AuthzAuditSink = Depends(get_authz_audit_sink),
) -> dict[str, Any]:
    """Create the Keycloak account AND the ``app_user`` row in one call, returning a generated
    temporary password shown once (slice S-user-create).

    Ordering is deliberate: the Keycloak account is created WITHOUT a credential, the PostgreSQL row
    commits, and only then is the password set. A failed write therefore leaves an unusable orphan
    that ``POST /users`` adopts via the ``keycloak_username_exists_unlinked`` link path — EasySynQ
    never deletes a Keycloak account.
    """
    username = body.username.strip()
    if not username:
        raise ProblemException(
            status=422, code="validation_error", title="username must not be empty"
        )

    # Roles are a distinct authority with their own key and SoD guard; only demand it when asked for.
    if body.role_ids:
        await authorize_or_raise(session, caller, "permission.grant")
        # Validate BEFORE touching Keycloak: an unknown role id would otherwise reach the INSERT and
        # raise an FK violation as a 500, after an account had already been created in Keycloak.
        known = set(
            (
                await session.execute(
                    select(Role.id).where(
                        Role.org_id == caller.org_id, Role.id.in_(body.role_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        unknown = [str(r) for r in body.role_ids if r not in known]
        if unknown:
            raise ProblemException(
                status=422,
                code="validation_error",
                title="Unknown role id(s)",
                detail=", ".join(unknown),
            )
        for role_id in body.role_ids:
            await assert_can_assign_role(session, sink, caller, role_id)

    password = generate_temporary_password(username)

    try:
        async with _kc_client() as kc:
            lookup = await kc.find_user_by_username(username)
            if lookup.found:
                assert lookup.subject is not None
                linked = await session.scalar(
                    select(AppUser.id).where(AppUser.keycloak_subject == lookup.subject)
                )
                if linked is not None:
                    raise ProblemException(
                        status=409,
                        code="user_exists",
                        title="That username already has an EasySynQ user",
                    )
                raise ProblemException(
                    status=409,
                    code="keycloak_username_exists_unlinked",
                    title="A sign-in account with that username already exists",
                    detail="Link the existing account instead of creating a new one.",
                    members={"keycloak_subject": lookup.subject},
                )

            subject = await kc.create_user(
                username=username,
                email=body.email,
                first_name=body.first_name,
                last_name=body.last_name,
            )

            user = AppUser(
                org_id=caller.org_id,
                keycloak_subject=subject,
                display_name=body.display_name or username,
                email=body.email,
                status=UserStatus.INVITED,
            )
            session.add(user)
            await session.flush()
            for role_id in body.role_ids:
                session.add(
                    RoleAssignment(org_id=caller.org_id, user_id=user.id, role_id=role_id)
                )
            _emit_user_event(
                session,
                caller,
                EventType.USER_CREATED,
                user.id,
                after={
                    "status": UserStatus.INVITED.value,
                    "email": body.email,
                    "provisioning": "keycloak_created",
                    "credential_issued": True,
                },
            )
            await session.commit()

            # Only now does the account become usable. A failure here leaves a real app_user whose
            # account has no credential — repaired by POST /users/{id}/temporary-password.
            await kc.set_temporary_password(subject=subject, password=password)
    except KeycloakNotConfigured as exc:
        raise ProblemException(
            status=503,
            code="keycloak_not_configured",
            title="Keycloak admin access is not configured on this install",
        ) from exc
    except KeycloakConflict as exc:
        code = "keycloak_email_exists" if exc.field == "email" else "user_exists"
        title = (
            "That email is already used by another sign-in account"
            if exc.field == "email"
            else "That username already exists"
        )
        raise ProblemException(status=409, code=code, title=title) from exc
    except KeycloakUnavailable as exc:
        raise ProblemException(
            status=502, code="keycloak_unavailable", title="Keycloak could not be reached"
        ) from exc

    await session.refresh(user)
    names = await _role_names_by_user(session, caller.org_id, [user.id])
    return {
        "user": _represent(user, names.get(user.id, [])),
        "temporary_password": password,
        "password_delivery": "shown_once",
    }
```

> If `authorize_or_raise` does not exist with that name, use whichever helper `services/authz` exposes for a non-dependency permission check, or add `Depends(_permission_grant)` on a nested route-free helper. Do **not** silently drop the check.

- [ ] **Step 3: Document the endpoint in the contract**

Insert into `packages/contracts/openapi.yaml` between the `/users` block (ends line 649) and `/users/{user_id}` (line 651), matching the surrounding style exactly:

```yaml
  /users/provision:
    post:
      tags: [users]
      operationId: provisionUser
      summary: >-
        Create the Keycloak sign-in account AND the INVITED app_user in one call, returning a
        generated temporary password shown once (S-user-create). Needs user.create; role_ids
        additionally needs permission.grant. 409 keycloak_username_exists_unlinked carries the
        existing keycloak_subject so the caller can link instead via POST /users.
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required: [username]
              properties:
                username: { type: string }
                display_name: { type: [string, "null"] }
                email: { type: [string, "null"] }
                first_name: { type: [string, "null"] }
                last_name: { type: [string, "null"] }
                role_ids:
                  type: array
                  items: { type: string, format: uuid }
      responses:
        "201":
          description: >-
            The created user plus its temporary password. The password is returned ONCE and is
            never stored, logged, or re-readable.
          content:
            application/json:
              schema:
                type: object
                required: [user, temporary_password, password_delivery]
                properties:
                  user: { $ref: "#/components/schemas/UserAdmin" }
                  temporary_password: { type: string }
                  password_delivery: { type: string, enum: [shown_once] }
        "403": { $ref: "#/components/responses/ProblemResponse" }
        "409": { $ref: "#/components/responses/ProblemResponse" }
        "422": { $ref: "#/components/responses/ProblemResponse" }
        "502": { $ref: "#/components/responses/ProblemResponse" }
        "503": { $ref: "#/components/responses/ProblemResponse" }
```

Then add one row to the users section of `docs/15-api-design.md` naming the endpoint, the `user.create` gate, the conditional `permission.grant` for `role_ids`, and the four new problem codes (`keycloak_username_exists_unlinked`, `keycloak_email_exists`, `keycloak_unavailable`, `keycloak_not_configured`).

- [ ] **Step 4: Run the contract and API gates**

```bash
cd apps/api && uv run ruff check src && uv run ruff format --check src && uv run mypy src && uv run pytest -m unit -q
```

Then run the `/check-contracts` skill.

Expected: both clean; the existing `tests/unit/test_user_admin.py` still passes unchanged.

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/easysynq_api/api/users.py packages/contracts/openapi.yaml docs/15-api-design.md
git commit -m "feat(api): POST /users/provision — create the Keycloak account and app_user in one call"
```

---

## Task 5: `POST /users/{user_id}/temporary-password`

**Files:**
- Modify: `apps/api/src/easysynq_api/api/users.py`
- Modify: `packages/contracts/openapi.yaml`
- Modify: `docs/15-api-design.md`

**Interfaces:**
- Consumes: Task 1, Task 2, and `EventType.USER_CREDENTIAL_ISSUED` (Task 3).
- Produces: `POST /api/v1/users/{user_id}/temporary-password` returning `{"temporary_password": str, "password_delivery": "shown_once"}`.

- [ ] **Step 1: Add the route**

Append to `apps/api/src/easysynq_api/api/users.py`:

```python
@router.post("/users/{user_id}/temporary-password")
async def issue_temporary_password(
    user_id: uuid.UUID,
    caller: AppUser = Depends(_user_create),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Issue a fresh temporary password for an existing linked user (slice S-user-create).

    Two jobs: it repairs a provision that committed the row but failed to set the credential, and it
    removes the last operational reason to run ``scripts/new-keycloak-user.sh``. Gated on
    ``user.create`` — issuing a credential is the same authority as creating the account.
    """
    target = await _get_user(session, user_id, caller.org_id)
    if not target.keycloak_subject:
        raise ProblemException(
            status=409,
            code="user_not_linked",
            title="That user has no linked sign-in account",
        )
    # `app_user` does not store the Keycloak username, so the closest identifier we hold is passed
    # to the policy guard. The generated value is 20 random characters and cannot collide with any
    # username in practice — the check is a belt-and-braces guard, not the primary defence.
    password = generate_temporary_password(target.display_name or "")
    try:
        async with _kc_client() as kc:
            await kc.set_temporary_password(subject=target.keycloak_subject, password=password)
    except KeycloakNotConfigured as exc:
        raise ProblemException(
            status=503,
            code="keycloak_not_configured",
            title="Keycloak admin access is not configured on this install",
        ) from exc
    except KeycloakUnavailable as exc:
        raise ProblemException(
            status=502, code="keycloak_unavailable", title="Keycloak could not be reached"
        ) from exc

    # Records THAT a credential was issued — never its value.
    _emit_user_event(
        session,
        caller,
        EventType.USER_CREDENTIAL_ISSUED,
        target.id,
        after={"credential_issued": True},
    )
    await session.commit()
    return {"temporary_password": password, "password_delivery": "shown_once"}
```

- [ ] **Step 2: Document it**

Insert into `packages/contracts/openapi.yaml` after the `/users/{user_id}` block:

```yaml
  /users/{user_id}/temporary-password:
    post:
      tags: [users]
      operationId: issueTemporaryPassword
      summary: >-
        Issue a fresh temporary password for a linked user, returned once (S-user-create). Repairs a
        provision whose credential step failed, and replaces scripts/new-keycloak-user.sh as the
        password-reset path. Needs user.create.
      parameters:
        - { name: user_id, in: path, required: true, schema: { type: string, format: uuid } }
      responses:
        "200":
          description: >-
            The new temporary password. Returned ONCE; never stored, logged, or re-readable.
          content:
            application/json:
              schema:
                type: object
                required: [temporary_password, password_delivery]
                properties:
                  temporary_password: { type: string }
                  password_delivery: { type: string, enum: [shown_once] }
        "403": { $ref: "#/components/responses/ProblemResponse" }
        "404": { $ref: "#/components/responses/ProblemResponse" }
        "409": { $ref: "#/components/responses/ProblemResponse" }
        "502": { $ref: "#/components/responses/ProblemResponse" }
        "503": { $ref: "#/components/responses/ProblemResponse" }
```

Then add the row to `docs/15-api-design.md`, naming the `user.create` gate and the `user_not_linked` / `keycloak_unavailable` / `keycloak_not_configured` problem codes.

- [ ] **Step 3: Run the gates**

```bash
cd apps/api && uv run ruff check src && uv run mypy src && uv run pytest -m unit -q
```

Then `/check-contracts`.

Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add apps/api/src/easysynq_api/api/users.py packages/contracts/openapi.yaml docs/15-api-design.md
git commit -m "feat(api): POST /users/{id}/temporary-password — reissue a show-once credential"
```

---

## Task 6: Integration tests

**Files:**
- Create: `apps/api/tests/integration/test_users_provision.py`

**Interfaces:**
- Consumes: both endpoints from Tasks 4 and 5.
- Produces: nothing consumed later.

> **Shared-DB discipline.** The `-m integration` suite shares one session database across all files. Assert **deltas** or scope to rows this test created — never absolute counts such as `app_user == 0`. Any test writing an `audit_event` must use an `occurred_at` inside a seeded monthly partition (migration 0010 creates only `2026-06/07/08`); using real `datetime.now(UTC)` is fine because the current month is seeded.

> **Mocking.** Patch `easysynq_api.api.users._kc_client` to return a `KeycloakProvisioningClient` built with an `httpx.MockTransport`, so the endpoint exercises its real code path with no live Keycloak.

- [ ] **Step 1: Write the harness and the first tests**

Create `apps/api/tests/integration/test_users_provision.py`:

```python
"""S-user-create integration proofs — in-app Keycloak provisioning and credential issuance.

No live Keycloak (D1): ``api.users._kc_client`` is monkeypatched to build the real
``KeycloakProvisioningClient`` over an ``httpx.MockTransport``, so the endpoint exercises its true
code path against a scripted identity service.

Shared-DB discipline: every assertion is scoped to rows this test created, or is a delta. The suite
shares one session database, so absolute counts are invalid.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import httpx
import pytest
from sqlalchemy import select

from easysynq_api.api import users as users_api
from easysynq_api.db.models._audit_enums import EventType
from easysynq_api.db.models.app_user import AppUser, UserStatus
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.domain.identity.temp_password import MIN_LENGTH
from easysynq_api.services.keycloak_provisioning import KeycloakProvisioningClient

from . import s5_helpers as s5
from .test_vault import _auth

pytestmark = pytest.mark.integration

_ADMIN = "System Administrator"


def _sub(prefix: str) -> str:
    return f"kc-{prefix}-{uuid.uuid4().hex[:10]}"


async def _admin(token_factory: Callable[..., str]) -> dict[str, str]:
    sub = _sub("admin")
    await s5.grant_role(sub, _ADMIN)
    return _auth(token_factory, sub)


def _install_kc(
    monkeypatch: pytest.MonkeyPatch,
    *,
    existing: str | None = None,
    create_status: int = 201,
    create_body: dict[str, object] | None = None,
    new_subject: str = "kc-provisioned",
    lookup_status: int = 200,
) -> dict[str, list[object]]:
    """Script the identity service. ``existing`` makes the username resolve to that subject."""
    calls: dict[str, list[object]] = {"password": []}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/openid-connect/token"):
            return httpx.Response(200, json={"access_token": "t"})
        if request.method == "GET" and request.url.path.endswith("/users"):
            if lookup_status != 200:
                return httpx.Response(lookup_status, json={"error": "boom"})
            username = request.url.params["username"]
            if existing is None:
                return httpx.Response(200, json=[])
            return httpx.Response(200, json=[{"id": existing, "username": username}])
        if request.method == "POST" and request.url.path.endswith("/users"):
            if create_status != 201:
                return httpx.Response(create_status, json=create_body or {})
            return httpx.Response(
                201, headers={"Location": f"http://kc/admin/realms/easysynq/users/{new_subject}"}
            )
        if request.method == "PUT" and request.url.path.endswith("/reset-password"):
            calls["password"].append(request.url.path)
            return httpx.Response(204)
        raise AssertionError(f"unexpected: {request.method} {request.url}")

    def factory() -> KeycloakProvisioningClient:
        return KeycloakProvisioningClient(
            base_url="http://kc",
            realm="easysynq",
            admin_user="admin",
            admin_password="secret",
            _transport=httpx.MockTransport(handler),
        )

    monkeypatch.setattr(users_api, "_kc_client", factory)
    return calls


async def _app_user_count() -> int:
    sm = get_sessionmaker()
    async with sm() as s:
        return len((await s.execute(select(AppUser.id))).scalars().all())


async def test_provision_creates_the_account_the_row_and_the_credential(
    app_client: httpx.AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject = _sub("new")
    calls = _install_kc(monkeypatch, new_subject=subject)
    headers = await _admin(token_factory)

    resp = await app_client.post(
        "/api/v1/users/provision",
        headers=headers,
        json={"username": f"jdoe-{uuid.uuid4().hex[:6]}", "display_name": "J. Doe"},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert len(body["temporary_password"]) >= MIN_LENGTH
    assert body["password_delivery"] == "shown_once"
    assert body["user"]["status"] == UserStatus.INVITED.value
    # The credential is set only AFTER the row commits.
    assert len(calls["password"]) == 1

    sm = get_sessionmaker()
    async with sm() as s:
        user = await s.scalar(select(AppUser).where(AppUser.keycloak_subject == subject))
        assert user is not None and user.status is UserStatus.INVITED
        event = await s.scalar(
            select(AuditEvent).where(
                AuditEvent.object_id == user.id, AuditEvent.event_type == EventType.USER_CREATED
            )
        )
        assert event is not None
        # Secrets hygiene: the password must never reach an audit payload.
        assert body["temporary_password"] not in str(event.after)


async def test_unlinked_username_collision_returns_the_subject_for_the_link_path(
    app_client: httpx.AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orphan = _sub("orphan")
    _install_kc(monkeypatch, existing=orphan)
    headers = await _admin(token_factory)

    resp = await app_client.post(
        "/api/v1/users/provision", headers=headers, json={"username": "already-there"}
    )

    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "keycloak_username_exists_unlinked"
    # This member is what drives the SPA's "Link the existing account" button.
    assert body["keycloak_subject"] == orphan


async def test_keycloak_failure_creates_no_app_user(
    app_client: httpx.AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_kc(monkeypatch, lookup_status=503)
    headers = await _admin(token_factory)
    before = await _app_user_count()

    resp = await app_client.post(
        "/api/v1/users/provision", headers=headers, json={"username": "never-created"}
    )

    assert resp.status_code == 502
    assert resp.json()["code"] == "keycloak_unavailable"
    assert await _app_user_count() == before  # delta-based, never an absolute count
```

- [ ] **Step 2: Add the remaining cases using the same harness**

Each reuses `_install_kc` and `_admin` exactly as above — no new scaffolding:

| Test | Arrangement | Assertion |
|---|---|---|
| Roles applied | `role_ids` for a seeded non-system role | matching `RoleAssignment` rows exist for the new user |
| Unknown role id | `role_ids=[uuid4()]` | `422 validation_error`, and `_app_user_count()` unchanged |
| Already-linked collision | `existing=<subject of a user created by a prior provision in this test>` | `409 user_exists`; `"keycloak_subject" not in body` |
| Duplicate email | `create_status=409, create_body={"errorMessage": "User exists with same email"}` | `409 keycloak_email_exists` |
| Role leg authz | caller granted a role holding `user.create` but **not** `permission.grant`, with `role_ids` supplied | `403`; `_app_user_count()` unchanged |
| Credential reissue | provision, then `POST /api/v1/users/{id}/temporary-password` | `200`, password ≥ `MIN_LENGTH`, and a `USER_CREDENTIAL_ISSUED` audit row exists for that user |

- [ ] **Step 2: Run them**

```bash
cd /home/cojoa13/Desktop/EasySynQ && sg docker -c "cd apps/api && uv run pytest -m integration tests/integration/test_users_provision.py -v"
```

Expected: all pass. Then confirm no cross-file pollution by running a document-creating file first:

```bash
sg docker -c "cd apps/api && uv run pytest -m integration tests/integration/test_vault.py tests/integration/test_users_provision.py -q"
```

- [ ] **Step 3: Commit**

```bash
git add apps/api/tests/integration/test_users_provision.py
git commit -m "test(api): integration coverage for user provisioning and credential issuance"
```

---

## Task 7: Web — types, show-once panel, create modal

**Files:**
- Modify: `apps/web/src/lib/types.ts`
- Create: `apps/web/src/admin/ShowOncePassword.tsx`
- Create: `apps/web/src/admin/CreateUserModal.tsx`
- Create: `apps/web/src/admin/CreateUserModal.test.tsx`

**Interfaces:**
- Consumes: the Task 4 and 5 response shapes.
- Produces: `ShowOncePassword` (props `{ password: string; onDone: () => void }`); `CreateUserModal` (props `{ opened: boolean; onClose: () => void; token: string | null }`).

> **Three web traps this task must avoid.**
> 1. Test files must `import { expect, it, describe, vi } from "vitest"` — the bare globals resolve to jest types and `tsc` fails on `.toBeInTheDocument` while `vitest run` passes.
> 2. MSW fixtures must be pinned to the **real** serializer shape with `satisfies`, not hand-typed.
> 3. Keep `aria-label`s distinct — a duplicate breaks the single-match `getByLabelText`.

- [ ] **Step 1: Add the types**

In `apps/web/src/lib/types.ts`:

```ts
export interface ProvisionUserRequest {
  username: string;
  display_name?: string;
  email?: string;
  first_name?: string;
  last_name?: string;
  role_ids?: string[];
}

// Mirrors api/users.py::provision_user — `user` is the standard _represent(...) shape.
export interface ProvisionedUser {
  user: AdminUser;
  temporary_password: string;
  password_delivery: "shown_once";
}

export interface IssuedTemporaryPassword {
  temporary_password: string;
  password_delivery: "shown_once";
}
```

`AdminUser` does **not** yet exist — the shape currently lives as a local `interface User` in `UsersAdmin.tsx:23-31`. Move it to `types.ts` verbatim under the name `AdminUser`, delete the local declaration, and import it in `UsersAdmin.tsx`:

```ts
// Mirrors api/users.py::_represent — pinned to the serializer, not to the mockup.
export interface AdminUser {
  id: string;
  keycloak_subject: string;
  display_name: string | null;
  email: string | null;
  status: string;
  mfa_enrolled: boolean;
  is_guest: boolean;
  roles: string[];
}
```

- [ ] **Step 2: Write the failing component test**

Create `apps/web/src/admin/CreateUserModal.test.tsx` covering:

- typing a username and submitting posts to `/api/v1/users/provision` and then renders the temporary password with copy affordance and the "cannot be shown again" wording;
- a `409 keycloak_username_exists_unlinked` response renders the inline warning plus a **Link the existing account** action, and activating it posts to `/api/v1/users` with the `keycloak_subject` from the problem body;
- a `409 keycloak_email_exists` surfaces against the email field;
- the role picker is absent when `usePermissions().can("permission.grant")` is false and present when true;
- `jest-axe` reports no violations, auditing `document.body` because Mantine portals its modal.

- [ ] **Step 3: Run it to verify it fails**

```bash
cd apps/web && npx vitest run src/admin/CreateUserModal.test.tsx
```

Expected: FAIL — module not found.

- [ ] **Step 4: Implement `ShowOncePassword.tsx` then `CreateUserModal.tsx`**

`ShowOncePassword.tsx` — the value lives in props only; never `localStorage`/`sessionStorage`, never a URL:

```tsx
import { Alert, Button, Code, Group, Stack, Text } from "@mantine/core";

export function ShowOncePassword({ password, onDone }: { password: string; onDone: () => void }) {
  return (
    <Stack gap="sm">
      <Alert color="yellow" title="Temporary password — shown once">
        <Stack gap="xs">
          <Code style={{ fontSize: "1.1rem", letterSpacing: "0.05em" }}>{password}</Code>
          <Text size="sm">
            Hand this to them directly. They must choose their own password at first login. This
            value is not stored and cannot be shown again — reissuing means resetting it.
          </Text>
          <Group>
            <Button
              variant="light"
              aria-label="Copy temporary password"
              onClick={() => void navigator.clipboard?.writeText(password)}
            >
              Copy
            </Button>
            <Button onClick={onDone}>Done</Button>
          </Group>
        </Stack>
      </Alert>
    </Stack>
  );
}
```

`CreateUserModal.tsx` — the collision subject comes off the RFC 9457 problem body, which `ApiError` already carries as `problem`:

```tsx
const [collision, setCollision] = useState<string | null>(null);
const [issued, setIssued] = useState<string | null>(null);

const createMut = useMutation({
  mutationFn: () => apiSend<ProvisionedUser>("POST", "/api/v1/users/provision", token, form),
  onSuccess: (data) => {
    setIssued(data.temporary_password);
    void qc.invalidateQueries({ queryKey: ["users"] });
  },
  onError: (e: unknown) => {
    if (e instanceof ApiError && e.code === "keycloak_username_exists_unlinked") {
      const subject = e.problem?.keycloak_subject;
      setCollision(typeof subject === "string" ? subject : null);
      return;
    }
    setError(e instanceof ApiError ? e.message : String(e));
  },
});

// The link path calls the KEPT invite endpoint with the subject the 409 handed back.
const linkMut = useMutation({
  mutationFn: () =>
    apiSend<AdminUser>("POST", "/api/v1/users", token, {
      keycloak_subject: collision,
      display_name: form.display_name || form.username,
      email: form.email || null,
    }),
  onSuccess: () => {
    void qc.invalidateQueries({ queryKey: ["users"] });
    close();
  },
});
```

Render `ShowOncePassword` when `issued` is set, the collision `Alert` (with **Link the existing account** and **Choose a different username**) when `collision` is set, and the form otherwise. `close()` must reset `issued`, `collision`, and `error` so a reopened modal never shows a stale credential.

- [ ] **Step 5: Run the test to verify it passes**

```bash
cd apps/web && npx vitest run src/admin/CreateUserModal.test.tsx
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/lib/types.ts apps/web/src/admin/ShowOncePassword.tsx apps/web/src/admin/CreateUserModal.tsx apps/web/src/admin/CreateUserModal.test.tsx
git commit -m "feat(web): create-user modal with inline link path and show-once password"
```

---

## Task 8: Web — wire into UsersAdmin and the Manage drawer

**Files:**
- Modify: `apps/web/src/admin/UsersAdmin.tsx`
- Modify: `apps/web/src/admin/UsersAdmin.test.tsx`

**Interfaces:**
- Consumes: `CreateUserModal`, `ShowOncePassword` (Task 7); `POST /users/{id}/temporary-password` (Task 5).
- Produces: nothing consumed later.

- [ ] **Step 1: Update the tests first**

In `apps/web/src/admin/UsersAdmin.test.tsx`:

- replace any assertion on the **Invite user** button with an assertion that it is **absent** and that **Create user** is present;
- add a test that the Manage drawer's **Issue new temp password** action posts to `/api/v1/users/{id}/temporary-password` and renders the show-once panel.

- [ ] **Step 2: Run to verify the new expectations fail**

```bash
cd apps/web && npx vitest run src/admin/UsersAdmin.test.tsx
```

Expected: FAIL on the absent-Invite and reset-action assertions.

- [ ] **Step 3: Implement**

In `UsersAdmin.tsx`: delete the `inviteOpen`/`invite` state, the `inviteMut` mutation, and the invite `Modal` (lines ~52–53, ~69–77, ~160–191); replace the header button with **Create user** opening `CreateUserModal`; update the header copy that currently reads "Invite a user (bind their Keycloak subject)…". In `ManageUser`, add the **Issue new temp password** button rendering `ShowOncePassword` on success.

- [ ] **Step 4: Run the full web gate**

Run the `/check-web` skill (eslint + strict `tsc --noEmit` + build + the whole vitest suite). The strict `tsc` leg is the only one that catches the jest-dom `expect` trap and `noUncheckedIndexedAccess` nits.

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/admin/UsersAdmin.tsx apps/web/src/admin/UsersAdmin.test.tsx
git commit -m "feat(web): Users admin creates accounts directly; Manage reissues a temp password"
```

---

## Final verification (before the PR)

- [ ] `/check-api` · `/check-web` · `/check-contracts` · `/check-migrations` all green.
- [ ] `bash scripts/check-no-site-data.sh` exits 0.
- [ ] `cd apps/api && uv run alembic heads` reports exactly `0085_user_credential_issued (head)`.
- [ ] Permission catalog assertion in `tests/unit/test_authz.py` still reads **102** — this slice adds no key.
- [ ] `git diff main -- apps/api/src/easysynq_api/api/users.py` shows the existing `invite_user` body **unchanged**.
- [ ] Reviewers on the branch diff: `diff-critic`, `web-test-trap-reviewer`, `migration-reviewer`.
- [ ] Grep the diff for the password variable reaching a logger or an audit payload: `git diff main | grep -n "password"` — review every hit.
