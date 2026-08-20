"""Regression proofs that real-role and ordinary integration fixtures are cluster-isolated."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa


def _assert_login(dsn: str, expected_role: str) -> None:
    """Authenticate through the supplied real-role DSN, rather than an owner connection."""
    engine = sa.create_engine(dsn)
    try:
        with engine.connect() as connection:
            assert connection.scalar(sa.text("SELECT current_user")) == expected_role
    finally:
        engine.dispose()


async def test_ordinary_then_authority_fixture_passwords_do_not_cross_contaminate(
    app_under_test: Any,
    dsns: dict[str, str],
    database_authority_dsns: dict[str, str],
) -> None:
    """The app fixture's legacy login pair must remain valid after authority setup."""
    _assert_login(dsns["app"], "easysynq_app")
    _assert_login(database_authority_dsns["easysynq_app"], "easysynq_app")


async def test_authority_then_ordinary_fixture_passwords_do_not_cross_contaminate(
    database_authority_dsns: dict[str, str],
    app_under_test: Any,
    dsns: dict[str, str],
) -> None:
    """Authority credentials must remain valid when ordinary app setup follows them."""
    _assert_login(database_authority_dsns["easysynq_app"], "easysynq_app")
    _assert_login(dsns["app"], "easysynq_app")
