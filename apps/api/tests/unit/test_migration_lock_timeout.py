"""Migration-only lock-timeout configuration and lifecycle regressions."""

from __future__ import annotations

import io
import runpy
from collections.abc import Callable
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import alembic.context as alembic_context
import pytest
import sqlalchemy
from alembic import command
from alembic.config import Config

from easysynq_api import config as config_module
from easysynq_api.config import get_settings

_ROOT = Path(__file__).resolve().parents[4]
_ENVIRONMENT = _ROOT / "migrations" / "env.py"
_MIGRATIONS = _ROOT / "migrations"
_SET_TIMEOUT = "SET SESSION lock_timeout = '5s'"


class _AlembicConfig:
    config_file_name: str | None = None
    config_ini_section = "alembic"

    def __init__(self) -> None:
        self.options: dict[str, str] = {}

    def set_main_option(self, name: str, value: str) -> None:
        self.options[name] = value

    def get_section(self, _name: str) -> dict[str, str]:
        return dict(self.options)


class _RecordingCursor:
    def __init__(self, statements: list[str], fail_on: str | None) -> None:
        self.statements = statements
        self.fail_on = fail_on

    def __enter__(self) -> _RecordingCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, statement: str) -> None:
        self.statements.append(statement)
        if self.fail_on == "execute":
            raise RuntimeError("set-lock-timeout-failed")


class _RecordingConnection:
    def __init__(self, *, autocommit: bool, fail_on: str | None = None) -> None:
        self.autocommit = autocommit
        self.fail_on = fail_on
        self.statements: list[str] = []

    def cursor(self) -> _RecordingCursor:
        if self.fail_on == "cursor":
            raise RuntimeError("cursor-open-failed")
        return _RecordingCursor(self.statements, self.fail_on)


def _load_offline_environment(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    config = _AlembicConfig()
    monkeypatch.setattr(alembic_context, "config", config, raising=False)
    monkeypatch.setattr(alembic_context, "is_offline_mode", lambda: True)
    monkeypatch.setattr(alembic_context, "configure", lambda **_kwargs: None)
    monkeypatch.setattr(alembic_context, "begin_transaction", nullcontext)
    monkeypatch.setattr(alembic_context, "run_migrations", lambda: None)
    monkeypatch.setattr(
        config_module,
        "get_settings",
        lambda: SimpleNamespace(sync_dsn="postgresql+psycopg://fixture/fixture"),
    )
    return runpy.run_path(str(_ENVIRONMENT))


def test_set_migration_lock_timeout_restores_autocommit_after_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = _load_offline_environment(monkeypatch)
    connection = _RecordingConnection(autocommit=False)

    environment["_set_migration_lock_timeout"](connection, object())

    assert connection.statements == [_SET_TIMEOUT]
    assert connection.autocommit is False


@pytest.mark.parametrize(
    ("fail_on", "expected_error"),
    [("cursor", "cursor-open-failed"), ("execute", "set-lock-timeout-failed")],
)
@pytest.mark.parametrize("original_autocommit", [False, True])
def test_set_migration_lock_timeout_restores_autocommit_after_failure(
    monkeypatch: pytest.MonkeyPatch,
    fail_on: str,
    expected_error: str,
    original_autocommit: bool,
) -> None:
    environment = _load_offline_environment(monkeypatch)
    connection = _RecordingConnection(
        autocommit=original_autocommit,
        fail_on=fail_on,
    )

    with pytest.raises(RuntimeError, match=expected_error):
        environment["_set_migration_lock_timeout"](connection, object())

    assert connection.autocommit is original_autocommit


class _SetupFailingEngine:
    def __init__(self) -> None:
        self.listener: Callable[[object, object], None] | None = None
        self.disposed = False

    def connect(self) -> object:
        if self.listener is None:
            raise AssertionError("migration engine connected before registering its listener")
        self.listener(_RecordingConnection(autocommit=False, fail_on="execute"), object())
        raise AssertionError("failed listener unexpectedly allowed a connection checkout")

    def dispose(self) -> None:
        self.disposed = True


def test_failed_connection_setup_disposes_engine_and_prevents_migration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _AlembicConfig()
    engine = _SetupFailingEngine()
    migration_runs: list[None] = []

    def listen(
        target: _SetupFailingEngine,
        event_name: str,
        listener: Callable[[object, object], None],
    ) -> None:
        assert event_name == "connect"
        target.listener = listener

    monkeypatch.setattr(alembic_context, "config", config, raising=False)
    monkeypatch.setattr(alembic_context, "is_offline_mode", lambda: False)
    monkeypatch.setattr(alembic_context, "configure", lambda **_kwargs: None)
    monkeypatch.setattr(alembic_context, "begin_transaction", nullcontext)
    monkeypatch.setattr(alembic_context, "run_migrations", lambda: migration_runs.append(None))
    monkeypatch.setattr(
        config_module,
        "get_settings",
        lambda: SimpleNamespace(sync_dsn="postgresql+psycopg://fixture/fixture"),
    )
    monkeypatch.setattr(sqlalchemy, "engine_from_config", lambda *_args, **_kwargs: engine)
    monkeypatch.setattr(sqlalchemy.event, "listen", listen)

    with pytest.raises(RuntimeError, match="set-lock-timeout-failed"):
        runpy.run_path(str(_ENVIRONMENT))

    assert migration_runs == []
    assert engine.disposed is True


def test_offline_generation_has_no_runtime_lock_timeout_instruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = io.StringIO()
    config = Config(output_buffer=output)
    config.set_main_option("script_location", str(_MIGRATIONS))
    try:
        with monkeypatch.context() as environment:
            environment.setenv(
                "DATABASE_URL",
                "postgresql+psycopg://fixture:fixture@localhost/fixture",
            )
            environment.setenv(
                "DATABASE_URL_SYNC",
                "postgresql+psycopg://fixture:fixture@localhost/fixture",
            )
            get_settings.cache_clear()
            command.upgrade(
                config,
                "0072_disposition_append_only:0073_pending_blob_purge",
                sql=True,
            )
    finally:
        get_settings.cache_clear()

    generated_sql = output.getvalue()
    assert "CREATE TABLE pending_blob_purge" in generated_sql
    assert "lock_timeout" not in generated_sql
