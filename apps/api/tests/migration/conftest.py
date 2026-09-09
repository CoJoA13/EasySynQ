"""Shared disposable PostgreSQL fixtures for real migration tests."""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager

import psycopg
import pytest
from psycopg import sql
from sqlalchemy.engine import make_url
from testcontainers.postgres import PostgresContainer

from easysynq_api.services.backup.dsn import conn_kwargs


@pytest.fixture(scope="module")
def postgres_admin_url() -> Iterator[str]:
    """Use only an explicitly disposable server; never infer a developer/production DSN."""
    configured = os.environ.get("MIGRATION_TEST_DATABASE_URL")
    if configured:
        yield configured
        return
    with PostgresContainer(
        "postgres:18",
        username="test",
        password="test",
        dbname="test",
        driver="psycopg",
    ) as postgres:
        yield postgres.get_connection_url()


@contextmanager
def _scratch_database(admin_url: str) -> Iterator[str]:
    database = f"easysynq_migrations_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(
        **conn_kwargs(admin_url, dbname="postgres"),
        autocommit=True,
    ) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))

    scratch_url = make_url(admin_url).set(database=database).render_as_string(hide_password=False)
    try:
        yield scratch_url
    finally:
        with psycopg.connect(
            **conn_kwargs(admin_url, dbname="postgres"),
            autocommit=True,
        ) as connection:
            connection.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (database,),
            )
            connection.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database)))


@pytest.fixture(scope="module")
def migration_database_factory(
    postgres_admin_url: str,
) -> Callable[[], AbstractContextManager[str]]:
    """Return a factory so each migration scenario owns an isolated scratch database."""

    def create_database() -> AbstractContextManager[str]:
        return _scratch_database(postgres_admin_url)

    return create_database
