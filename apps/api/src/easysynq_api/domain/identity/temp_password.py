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
