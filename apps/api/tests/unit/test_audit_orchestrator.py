from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import ANY, AsyncMock, Mock, call

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from easysynq_api.config import Settings
from easysynq_api.services.audit.checkpoint import OffHostCheckpointResult
from easysynq_api.services.audit.verify import VerifyResult
from easysynq_api.services.notifications.constants import EVENT_INTEGRITY_ALARM
from easysynq_api.services.notifications.ops_events import (
    CHECK_CHAIN_VERIFY,
    CHECK_VERIFY_KEY,
)
from easysynq_api.tasks import audit as audit_tasks

pytestmark = pytest.mark.unit


class _ScalarRows:
    def __init__(self, values: list[uuid.UUID]) -> None:
        self._values = values

    def scalars(self) -> _ScalarRows:
        return self

    def all(self) -> list[uuid.UUID]:
        return self._values


class _Session:
    def __init__(
        self,
        org_ids: list[uuid.UUID],
        *,
        execute_error: BaseException | None = None,
    ) -> None:
        self.org_ids = org_ids
        self.execute_error = execute_error
        self.added: list[object] = []
        self.commit = AsyncMock()

    async def execute(self, _statement: object) -> _ScalarRows:
        if self.execute_error is not None:
            raise self.execute_error
        return _ScalarRows(self.org_ids)

    def add(self, row: object) -> None:
        self.added.append(row)


class _SessionContext:
    def __init__(self, session: _Session) -> None:
        self._session = session

    async def __aenter__(self) -> _Session:
        return self._session

    async def __aexit__(self, *_args: object) -> None:
        return None


class _Sessionmaker:
    def __init__(self, session: _Session) -> None:
        self._session = session
        self.calls = 0

    def __call__(self) -> _SessionContext:
        self.calls += 1
        return _SessionContext(self._session)


class _Engine:
    def __init__(self) -> None:
        self.dispose = AsyncMock()


def _settings(*, witness_required: bool = False) -> Settings:
    return Settings(
        database_url="postgresql+psycopg://app:app@db.test/easysynq",
        audit_witness_required=witness_required,
    )


def _clean_verify() -> VerifyResult:
    return VerifyResult(verified=True, checked=0, pending=0, breaks=[])


def _unconfigured_offhost() -> OffHostCheckpointResult:
    return OffHostCheckpointResult(
        offhost_configured=False,
        sinks_read=0,
        verified=False,
        reasons=["no off-host sink configured — independent attestation unavailable"],
    )


def _install_database_runtime(
    monkeypatch: pytest.MonkeyPatch,
    session: _Session,
) -> tuple[_Engine, _Sessionmaker]:
    engine = _Engine()
    sessionmaker = _Sessionmaker(session)

    def create_engine(database_url: str) -> _Engine:
        assert database_url == "postgresql+psycopg://app:app@db.test/easysynq"
        return engine

    def create_sessionmaker(created_engine: object, *, expire_on_commit: bool) -> _Sessionmaker:
        assert created_engine is engine
        assert expire_on_commit is False
        return sessionmaker

    monkeypatch.setattr(audit_tasks, "create_async_engine", create_engine)
    monkeypatch.setattr(audit_tasks, "async_sessionmaker", create_sessionmaker)
    return engine, sessionmaker


def _assert_could_not_run_alert(alert: Any, error: BaseException) -> None:
    assert alert.event == EVENT_INTEGRITY_ALARM
    assert alert.severity == "critical"
    assert alert.summary == "nightly audit chain verification could not run"
    assert alert.detail == {"check": CHECK_CHAIN_VERIFY, "error": str(error)}
    assert alert.org_id is None


async def test_engine_construction_failure_alerts_and_reraises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = RuntimeError("engine construction sentinel")
    sessionmaker = Mock()
    verify = AsyncMock()
    send = AsyncMock(return_value={})
    key = Ed25519PrivateKey.generate().public_key()

    def fail_engine(_database_url: str) -> object:
        raise failure

    monkeypatch.setattr(audit_tasks, "get_settings", lambda: _settings())
    monkeypatch.setattr(audit_tasks, "load_verify_key", lambda: key)
    monkeypatch.setattr(audit_tasks, "create_async_engine", fail_engine)
    monkeypatch.setattr(audit_tasks, "async_sessionmaker", sessionmaker)
    monkeypatch.setattr(audit_tasks, "verify_chain", verify)
    monkeypatch.setattr(audit_tasks, "send_operator_alert", send)

    with pytest.raises(RuntimeError) as exc_info:
        await audit_tasks._run_verify_chain()

    assert exc_info.value is failure
    sessionmaker.assert_not_called()
    verify.assert_not_awaited()
    send.assert_awaited_once()
    _assert_could_not_run_alert(send.await_args.args[1], failure)


async def test_database_read_failure_alerts_reraises_and_disposes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = OSError("organization read sentinel")
    session = _Session([], execute_error=failure)
    engine, sessionmaker = _install_database_runtime(monkeypatch, session)
    send = AsyncMock(return_value={})
    verify = AsyncMock()
    emit = AsyncMock()
    key = Ed25519PrivateKey.generate().public_key()

    monkeypatch.setattr(audit_tasks, "get_settings", lambda: _settings())
    monkeypatch.setattr(audit_tasks, "load_verify_key", lambda: key)
    monkeypatch.setattr(audit_tasks, "verify_chain", verify)
    monkeypatch.setattr(audit_tasks, "emit_integrity_alarm", emit)
    monkeypatch.setattr(audit_tasks, "send_operator_alert", send)

    with pytest.raises(OSError) as exc_info:
        await audit_tasks._run_verify_chain()

    assert exc_info.value is failure
    assert sessionmaker.calls == 1
    session.commit.assert_not_awaited()
    verify.assert_not_awaited()
    emit.assert_not_awaited()
    send.assert_awaited_once()
    _assert_could_not_run_alert(send.await_args.args[1], failure)
    engine.dispose.assert_awaited_once_with()


async def test_clean_run_does_not_commit_and_disposes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_ids = [uuid.uuid4(), uuid.uuid4()]
    session = _Session(org_ids)
    engine, sessionmaker = _install_database_runtime(monkeypatch, session)
    verify = AsyncMock(side_effect=[_clean_verify(), _clean_verify()])
    offhost = AsyncMock(side_effect=[_unconfigured_offhost(), _unconfigured_offhost()])
    emit = AsyncMock()
    send = AsyncMock(return_value={})
    key = Ed25519PrivateKey.generate().public_key()

    monkeypatch.setattr(audit_tasks, "get_settings", lambda: _settings())
    monkeypatch.setattr(audit_tasks, "load_verify_key", lambda: key)
    monkeypatch.setattr(audit_tasks, "verify_chain", verify)
    monkeypatch.setattr(audit_tasks, "verify_offhost_checkpoint", offhost)
    monkeypatch.setattr(audit_tasks, "emit_integrity_alarm", emit)
    monkeypatch.setattr(audit_tasks, "send_operator_alert", send)

    result = await audit_tasks._run_verify_chain()

    assert result == 0
    assert sessionmaker.calls == 1
    assert verify.await_args_list == [call(session, org_id, verify_key=key) for org_id in org_ids]
    assert offhost.await_args_list == [call(session, org_id, verify_key=key) for org_id in org_ids]
    emit.assert_not_awaited()
    send.assert_not_awaited()
    assert session.added == []
    session.commit.assert_not_awaited()
    engine.dispose.assert_awaited_once_with()


@pytest.mark.parametrize("created_counts", [(1, 0), (0, 1), (0, 0)])
async def test_missing_key_fans_out_per_org_and_preserves_dirty(
    monkeypatch: pytest.MonkeyPatch,
    created_counts: tuple[int, int],
) -> None:
    org_ids = [uuid.uuid4(), uuid.uuid4()]
    session = _Session(org_ids)
    engine, sessionmaker = _install_database_runtime(monkeypatch, session)
    verify = AsyncMock(side_effect=[_clean_verify(), _clean_verify()])
    offhost = AsyncMock()
    emit = AsyncMock(side_effect=created_counts)
    send = AsyncMock(return_value={})

    monkeypatch.setattr(audit_tasks, "get_settings", lambda: _settings())
    monkeypatch.setattr(audit_tasks, "load_verify_key", lambda: None)
    monkeypatch.setattr(audit_tasks, "verify_chain", verify)
    monkeypatch.setattr(audit_tasks, "verify_offhost_checkpoint", offhost)
    monkeypatch.setattr(audit_tasks, "emit_integrity_alarm", emit)
    monkeypatch.setattr(audit_tasks, "send_operator_alert", send)

    result = await audit_tasks._run_verify_chain()

    assert result == 0
    assert sessionmaker.calls == 1
    assert verify.await_args_list == [call(session, org_id, verify_key=None) for org_id in org_ids]
    offhost.assert_not_awaited()
    assert emit.await_args_list == [
        call(
            session,
            org_id=org_id,
            check=CHECK_VERIFY_KEY,
            reasons=[ANY],
            break_count=0,
            now=ANY,
        )
        for org_id in org_ids
    ]
    assert all("DISABLED" in entry.kwargs["reasons"][0] for entry in emit.await_args_list)
    assert session.added == []
    if any(created_counts):
        session.commit.assert_awaited_once_with()
    else:
        session.commit.assert_not_awaited()
    send.assert_awaited_once()
    alert = send.await_args.args[1]
    assert alert.event == EVENT_INTEGRITY_ALARM
    assert alert.severity == "critical"
    assert alert.summary == "audit verify key missing — checkpoint attestation is DISABLED"
    assert alert.detail["check"] == CHECK_VERIFY_KEY
    assert "DISABLED" in alert.detail["reason"]
    assert alert.org_id is None
    engine.dispose.assert_awaited_once_with()
