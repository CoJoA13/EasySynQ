"""Guard the Celery task registration (S-pack-1): if ``tasks/packs.py`` is not imported in
``tasks/__init__.py``, ``generate_pack``'s ``.delay`` publishes a message by a name no worker
registered → the pack stays BUILDING forever. Fails at CI rather than silently in production."""

from __future__ import annotations

import asyncio
import datetime
import uuid
from types import SimpleNamespace

import pytest

import easysynq_api.tasks  # noqa: F401 — importing the package registers every task module
from easysynq_api.services.authz import audit as authz_audit
from easysynq_api.services.authz.audit import AuthzAuditEvent, DbAuthzAuditSink
from easysynq_api.tasks import packs as pack_tasks
from easysynq_api.tasks.app import app


def test_pack_tasks_are_registered() -> None:
    assert "easysynq.packs.build_evidence_pack" in app.tasks
    assert "easysynq.packs.reap_stalled_builds" in app.tasks


def test_reaper_is_beat_scheduled() -> None:
    tasks = {entry["task"] for entry in app.conf.beat_schedule.values()}
    assert "easysynq.packs.reap_stalled_builds" in tasks
    # The build is .delay-triggered, NOT Beat-scheduled.
    assert "easysynq.packs.build_evidence_pack" not in tasks


def test_build_task_binds_authz_sink_to_each_task_local_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fresh asyncio.run() must never reuse the process-global authz-audit connection pool."""

    class FakeEngine:
        def __init__(self) -> None:
            self.disposed = False

        async def dispose(self) -> None:
            self.disposed = True

    class FakeSessionContext:
        def __init__(self, session_factory: FakeSessionmaker) -> None:
            self._session_factory = session_factory

        async def __aenter__(self) -> FakeSession:
            session = FakeSession()
            self._session_factory.sessions.append(session)
            return session

        async def __aexit__(self, *_args: object) -> None:
            return None

    class FakeSession:
        def __init__(self) -> None:
            self.added: list[object] = []
            self.committed = False

        def add(self, row: object) -> None:
            self.added.append(row)

        async def commit(self) -> None:
            self.committed = True

    class FakeSessionmaker:
        def __init__(self) -> None:
            self.sessions: list[FakeSession] = []

        def __call__(self) -> FakeSessionContext:
            return FakeSessionContext(self)

    engines: list[FakeEngine] = []
    session_factories: list[FakeSessionmaker] = []
    builds: list[tuple[object, uuid.UUID, DbAuthzAuditSink, FakeSessionmaker]] = []

    def fake_engine(_url: str) -> FakeEngine:
        engine = FakeEngine()
        engines.append(engine)
        return engine

    def fake_sessionmaker(_engine: FakeEngine, *, expire_on_commit: bool) -> FakeSessionmaker:
        assert expire_on_commit is False
        session_factory = FakeSessionmaker()
        session_factories.append(session_factory)
        return session_factory

    async def fake_build(
        session: object,
        pack_id: uuid.UUID,
        *,
        authz_sink: DbAuthzAuditSink,
        rejection_sessionmaker: FakeSessionmaker,
    ) -> None:
        builds.append((session, pack_id, authz_sink, rejection_sessionmaker))
        await authz_sink.record(
            AuthzAuditEvent(
                occurred_at=datetime.datetime.now(datetime.UTC),
                actor_id=str(uuid.uuid4()),
                org_id=str(uuid.uuid4()),
                permission_key="finding.read",
                decision="deny",
                reason="no_matching_allow",
            )
        )

    async def fake_portfolio(_session: object, _pack_id: uuid.UUID) -> None:
        return None

    def fail_global_sessionmaker() -> None:
        raise AssertionError("global sessionmaker used by worker sink")

    monkeypatch.setattr(pack_tasks, "get_settings", lambda: SimpleNamespace(database_url="test"))
    monkeypatch.setattr(pack_tasks, "create_async_engine", fake_engine)
    monkeypatch.setattr(pack_tasks, "async_sessionmaker", fake_sessionmaker)
    monkeypatch.setattr(pack_tasks, "build", fake_build)
    monkeypatch.setattr(pack_tasks, "build_and_cache_portfolio", fake_portfolio)
    monkeypatch.setattr(authz_audit, "get_sessionmaker", fail_global_sessionmaker)

    first_id = uuid.uuid4()
    second_id = uuid.uuid4()
    asyncio.run(pack_tasks._run_build(str(first_id)))
    asyncio.run(pack_tasks._run_build(str(second_id)))

    assert [entry[1] for entry in builds] == [first_id, second_id]
    assert len({id(factory) for factory in session_factories}) == 2
    assert len({id(entry[2]) for entry in builds}) == 2
    assert all(
        rejection_sessionmaker is task_sessionmaker
        for (*_, rejection_sessionmaker), task_sessionmaker in zip(
            builds, session_factories, strict=True
        )
    )
    assert all(
        len(factory.sessions) == 3
        and len(factory.sessions[1].added) == 1
        and factory.sessions[1].committed
        for factory in session_factories
    )
    assert all(engine.disposed for engine in engines)
