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


def test_policy_distinguishes_equality_from_containment() -> None:
    """The realm's ``notUsername`` is an EQUALITY rule, not containment.

    This is the mutation-discriminating case, and the only test in this file that is: a password
    at or above the length floor that CONTAINS the username without being equal to it must be
    ACCEPTED. An implementation using ``username not in password`` returns False here and this
    test fails — which is exactly the point. Every other assertion in this file passes under
    both rules, because they use sub-floor passwords whose length leg short-circuits first.
    """
    username = "quality-manager"  # >= MIN_LENGTH, so the length leg cannot mask the comparison
    contains_but_not_equal = f"Xk4m-{username}-Pq7r"
    assert len(contains_but_not_equal) >= MIN_LENGTH
    assert satisfies_realm_policy(contains_but_not_equal, username) is True

    # Equality is still rejected at a length that likewise cannot short-circuit on the length leg.
    long_username = username * 2
    assert satisfies_realm_policy(long_username, long_username) is False


def test_alphabet_excludes_visually_ambiguous_characters() -> None:
    # The value is transcribed by hand or read aloud, so 0/O and 1/l/I must not appear.
    generated = "".join(generate_temporary_password("jdoe") for _ in range(50))
    for ambiguous in "0O1lI":
        assert ambiguous not in generated
