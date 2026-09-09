"""Real PostgreSQL proofs for bounded online migration lock waits."""

from __future__ import annotations

import asyncio
import datetime
import queue
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass

import psycopg
import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from easysynq_api.config import get_settings
from easysynq_api.readiness import MIGRATIONS_DIR
from easysynq_api.services.backup.dsn import conn_kwargs

_SEEDED_PARTITION_INSTANT = "2026-08-15 12:00:00+00"
_WATCHDOG_SECONDS = 15.0
_CLEANUP_SECONDS = 5.0
_POLL_SECONDS = 0.05


@dataclass(frozen=True)
class _ObservedLock:
    pid: int
    wait_event_type: str
    mode: str
    granted: bool


@dataclass(frozen=True)
class _ContentionResult:
    migration_exception: Exception
    migration_lock: _ObservedLock
    following_writer_lock: _ObservedLock
    owned_pids: frozenset[int]
    following_writer_finished_while_original_writer_open: bool
    version: str
    pending_blob_purge_exists: bool
    checkpoint_sink_enabled_at_exists: bool
    audit_scope_ref_index_exists: bool
    import_commit_owner_snapshot_exists: bool


def _config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return config


@contextmanager
def _migration_environment(
    monkeypatch: pytest.MonkeyPatch,
    scratch_url: str,
    *,
    application_name: str | None = None,
) -> Iterator[tuple[Config, str]]:
    migration_application_name = f"easysynq_lock_timeout_{uuid.uuid4().hex}"
    migration_url = (
        make_url(scratch_url)
        .update_query_dict({"application_name": migration_application_name})
        .render_as_string(hide_password=False)
    )
    application_url = scratch_url
    if application_name is not None:
        application_url = (
            make_url(scratch_url)
            .update_query_dict({"application_name": application_name})
            .render_as_string(hide_password=False)
        )
    try:
        with monkeypatch.context() as migration_environment:
            migration_environment.setenv("DATABASE_URL", application_url)
            migration_environment.setenv("DATABASE_URL_SYNC", migration_url)
            get_settings.cache_clear()
            yield _config(), migration_application_name
    finally:
        get_settings.cache_clear()


def _connect(
    database_url: str,
    application_name: str,
    *,
    autocommit: bool = False,
) -> psycopg.Connection[tuple[object, ...]]:
    return psycopg.connect(
        **conn_kwargs(database_url),
        application_name=application_name,
        autocommit=autocommit,
    )


def _backend_pid(connection: psycopg.Connection[tuple[object, ...]]) -> int:
    row = connection.execute("SELECT pg_backend_pid()").fetchone()
    if row is None:
        raise AssertionError("owned PostgreSQL connection did not return its backend PID")
    return int(row[0])


def _insert_audit_event(
    connection: psycopg.Connection[tuple[object, ...]],
    org_id: object,
    marker: str,
) -> None:
    connection.execute(
        """
        INSERT INTO audit_event (
            org_id, occurred_at, actor_type, event_type, object_type, object_id, scope_ref
        )
        VALUES (%s, %s, 'system', 'ACCESS_DENIED', 'config', %s, %s)
        """,
        (org_id, _SEEDED_PARTITION_INSTANT, uuid.uuid4(), marker),
    )


def _create_synthetic_organization(
    observer: psycopg.Connection[tuple[object, ...]],
    label: str,
) -> object:
    row = observer.execute(
        "INSERT INTO organization (legal_name, short_code) VALUES (%s, %s) RETURNING id",
        (f"Migration lock test {label}", f"LOCK-{label.upper()}-{uuid.uuid4().hex[:8]}"),
    ).fetchone()
    if row is None:
        raise AssertionError(f"could not create the {label} synthetic organization")
    return row[0]


def _wait_for_migration_pid(
    observer: psycopg.Connection[tuple[object, ...]],
    application_name: str,
    deadline: float,
) -> int:
    while time.monotonic() < deadline:
        rows = observer.execute(
            "SELECT pid FROM pg_stat_activity "
            "WHERE datname = current_database() AND application_name = %s",
            (application_name,),
        ).fetchall()
        if len(rows) > 1:
            raise AssertionError("migration application_name matched more than one backend")
        if rows:
            return int(rows[0][0])
        threading.Event().wait(_POLL_SECONDS)
    raise AssertionError("migration backend did not appear before the watchdog deadline")


def _wait_for_relation_lock(
    observer: psycopg.Connection[tuple[object, ...]],
    pid: int,
    mode: str,
    deadline: float,
) -> _ObservedLock:
    while time.monotonic() < deadline:
        rows = observer.execute(
            """
            SELECT a.pid, a.wait_event_type, l.mode, l.granted
            FROM pg_stat_activity a JOIN pg_locks l ON l.pid = a.pid
            WHERE a.pid = %s AND l.relation = 'audit_event'::regclass
            """,
            (pid,),
        ).fetchall()
        for lock_pid, wait_event_type, lock_mode, granted in rows:
            if wait_event_type == "Lock" and lock_mode == mode and granted is False:
                return _ObservedLock(
                    pid=int(lock_pid),
                    wait_event_type=str(wait_event_type),
                    mode=str(lock_mode),
                    granted=bool(granted),
                )
        threading.Event().wait(_POLL_SECONDS)
    raise AssertionError(f"backend {pid} did not expose an ungranted {mode} before the deadline")


def _column_exists(
    observer: psycopg.Connection[tuple[object, ...]],
    table_name: str,
    column_name: str,
) -> bool:
    row = observer.execute(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = %s
              AND column_name = %s
        )
        """,
        (table_name, column_name),
    ).fetchone()
    if row is None:
        raise AssertionError("column catalog query returned no result")
    return bool(row[0])


def _cancel_backend(
    observer: psycopg.Connection[tuple[object, ...]],
    pid: int,
) -> bool:
    row = observer.execute("SELECT pg_cancel_backend(%s)", (pid,)).fetchone()
    return row is not None and row[0] is True


def _terminate_backend(
    observer: psycopg.Connection[tuple[object, ...]],
    pid: int,
) -> bool:
    row = observer.execute("SELECT pg_terminate_backend(%s)", (pid,)).fetchone()
    return row is not None and row[0] is True


def _exercise_autocommit_lock_contention(
    scratch_url: str,
    config: Config,
    migration_application_name: str,
) -> _ContentionResult:
    original_writer: psycopg.Connection[tuple[object, ...]] | None = None
    following_writer: psycopg.Connection[tuple[object, ...]] | None = None
    observer: psycopg.Connection[tuple[object, ...]] | None = None
    migration_outcome: queue.Queue[Exception | None] = queue.Queue(maxsize=1)
    following_writer_outcome: queue.Queue[Exception | None] = queue.Queue(maxsize=1)
    migration_done = threading.Event()
    following_writer_done = threading.Event()
    migration_thread: threading.Thread | None = None
    following_writer_thread: threading.Thread | None = None
    migration_pid: int | None = None
    original_writer_pid: int | None = None
    following_writer_pid: int | None = None

    try:
        original_writer = _connect(scratch_url, f"{migration_application_name}_original")
        following_writer = _connect(scratch_url, f"{migration_application_name}_following")
        observer = _connect(
            scratch_url,
            f"{migration_application_name}_observer",
            autocommit=True,
        )
        owned_original_writer = original_writer
        owned_following_writer = following_writer
        owned_observer = observer

        original_writer_pid = _backend_pid(owned_original_writer)
        following_writer_pid = _backend_pid(owned_following_writer)
        observer_pid = _backend_pid(owned_observer)
        owned_original_writer.commit()
        owned_following_writer.commit()

        original_org_id = _create_synthetic_organization(owned_observer, "original")
        following_org_id = _create_synthetic_organization(owned_observer, "following")

        _insert_audit_event(owned_original_writer, original_org_id, "lock-timeout-seed")
        owned_original_writer.commit()
        _insert_audit_event(owned_original_writer, original_org_id, "lock-timeout-blocker")

        def migrate() -> None:
            try:
                command.upgrade(config, "head")
            except Exception as exc:  # noqa: BLE001 - the SQLSTATE is the behavior under test
                migration_outcome.put(exc)
            else:
                migration_outcome.put(None)
            finally:
                migration_done.set()

        def insert_following_event() -> None:
            try:
                _insert_audit_event(
                    owned_following_writer,
                    following_org_id,
                    "lock-timeout-following",
                )
                owned_following_writer.commit()
            except Exception as exc:  # noqa: BLE001 - surfaced to the owning test thread
                owned_following_writer.rollback()
                following_writer_outcome.put(exc)
            else:
                following_writer_outcome.put(None)
            finally:
                following_writer_done.set()

        watchdog_deadline = time.monotonic() + _WATCHDOG_SECONDS
        migration_thread = threading.Thread(target=migrate, name="migration-lock-timeout")
        migration_thread.start()
        migration_pid = _wait_for_migration_pid(
            owned_observer,
            migration_application_name,
            watchdog_deadline,
        )
        migration_lock = _wait_for_relation_lock(
            owned_observer,
            migration_pid,
            "ShareLock",
            watchdog_deadline,
        )

        following_writer_thread = threading.Thread(
            target=insert_following_event,
            name="migration-following-writer",
        )
        following_writer_thread.start()
        following_writer_lock = _wait_for_relation_lock(
            owned_observer,
            following_writer_pid,
            "RowExclusiveLock",
            watchdog_deadline,
        )

        if not migration_done.wait(max(0.0, watchdog_deadline - time.monotonic())):
            if not _cancel_backend(owned_observer, migration_pid):
                raise AssertionError("watchdog could not cancel the owned migration backend")
            if not migration_done.wait(_CLEANUP_SECONDS):
                raise AssertionError("migration worker did not finish after owned-backend cancel")

        migration_exception = migration_outcome.get(timeout=_CLEANUP_SECONDS)
        if migration_exception is None:
            raise AssertionError("contended migration unexpectedly reached head")
        if not following_writer_done.wait(_CLEANUP_SECONDS):
            raise AssertionError("following writer remained blocked after the migration stopped")
        following_exception = following_writer_outcome.get(timeout=_CLEANUP_SECONDS)
        if following_exception is not None:
            raise AssertionError(
                "following writer failed after migration rollback"
            ) from following_exception

        writer_state = owned_observer.execute(
            "SELECT state, xact_start IS NOT NULL FROM pg_stat_activity WHERE pid = %s",
            (original_writer_pid,),
        ).fetchone()
        following_finished_while_original_open = writer_state == ("idle in transaction", True)

        version_row = owned_observer.execute("SELECT version_num FROM alembic_version").fetchone()
        if version_row is None:
            raise AssertionError("Alembic version row disappeared after migration failure")
        pending_blob_purge_exists = owned_observer.execute(
            "SELECT to_regclass('public.pending_blob_purge')"
        ).fetchone() == ("pending_blob_purge",)
        audit_scope_ref_index_exists = owned_observer.execute(
            "SELECT to_regclass('public.ix_audit_event_org_id_scope_ref_id')"
        ).fetchone() != (None,)

        return _ContentionResult(
            migration_exception=migration_exception,
            migration_lock=migration_lock,
            following_writer_lock=following_writer_lock,
            owned_pids=frozenset(
                {original_writer_pid, following_writer_pid, observer_pid, migration_pid}
            ),
            following_writer_finished_while_original_writer_open=(
                following_finished_while_original_open
            ),
            version=str(version_row[0]),
            pending_blob_purge_exists=pending_blob_purge_exists,
            checkpoint_sink_enabled_at_exists=_column_exists(
                owned_observer,
                "audit_checkpoint_sink",
                "enabled_at",
            ),
            audit_scope_ref_index_exists=audit_scope_ref_index_exists,
            import_commit_owner_snapshot_exists=_column_exists(
                owned_observer,
                "import_run",
                "commit_owner_snapshot",
            ),
        )
    finally:
        cleanup_errors: list[str] = []
        if observer is not None and migration_thread is not None and migration_thread.is_alive():
            try:
                if migration_pid is None:
                    rows = observer.execute(
                        "SELECT pid FROM pg_stat_activity "
                        "WHERE datname = current_database() AND application_name = %s",
                        (migration_application_name,),
                    ).fetchall()
                    if len(rows) == 1:
                        migration_pid = int(rows[0][0])
                if migration_pid is not None:
                    _cancel_backend(observer, migration_pid)
            except Exception as exc:  # noqa: BLE001 - cleanup must report every owned failure
                cleanup_errors.append(f"migration cancel failed: {exc}")

        if (
            observer is not None
            and following_writer_thread is not None
            and following_writer_thread.is_alive()
            and following_writer_pid is not None
        ):
            try:
                _cancel_backend(observer, following_writer_pid)
            except Exception as exc:  # noqa: BLE001 - cleanup must report every owned failure
                cleanup_errors.append(f"following writer cancel failed: {exc}")

        for worker in (following_writer_thread, migration_thread):
            if worker is not None:
                worker.join(_CLEANUP_SECONDS)

        for worker, pid in (
            (following_writer_thread, following_writer_pid),
            (migration_thread, migration_pid),
        ):
            if worker is None or not worker.is_alive():
                continue
            cleanup_errors.append(f"worker required backend termination: {worker.name}")
            if observer is None or pid is None:
                continue
            try:
                _terminate_backend(observer, pid)
            except Exception as exc:  # noqa: BLE001 - cleanup must report every owned failure
                cleanup_errors.append(f"backend termination failed for {worker.name}: {exc}")
            worker.join(_CLEANUP_SECONDS)

        for worker in (following_writer_thread, migration_thread):
            if worker is not None and worker.is_alive():
                cleanup_errors.append(
                    f"worker remained alive after backend termination: {worker.name}"
                )

        for connection, label in (
            (original_writer, "original writer"),
            (following_writer, "following writer"),
        ):
            if connection is None:
                continue
            if (
                connection is following_writer
                and following_writer_thread is not None
                and following_writer_thread.is_alive()
            ):
                continue
            try:
                connection.rollback()
            except Exception as exc:  # noqa: BLE001 - cleanup must report every owned failure
                cleanup_errors.append(f"{label} rollback failed: {exc}")
            try:
                connection.close()
            except Exception as exc:  # noqa: BLE001 - cleanup must report every owned failure
                cleanup_errors.append(f"{label} close failed: {exc}")
        if observer is not None:
            try:
                observer.close()
            except Exception as exc:  # noqa: BLE001 - cleanup must report every owned failure
                cleanup_errors.append(f"observer close failed: {exc}")
        if cleanup_errors:
            raise AssertionError("; ".join(cleanup_errors))


def _sqlstate(exception: Exception) -> str | None:
    original = getattr(exception, "orig", None)
    return getattr(original if original is not None else exception, "sqlstate", None)


def test_autocommit_boundary_survives_bounded_audit_index_lock_wait(
    migration_database_factory: Callable[[], AbstractContextManager[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with migration_database_factory() as scratch_url:
        with _migration_environment(monkeypatch, scratch_url) as (
            config,
            migration_application_name,
        ):
            command.upgrade(config, "0072_disposition_append_only")
            result = _exercise_autocommit_lock_contention(
                scratch_url,
                config,
                migration_application_name,
            )

        assert len(result.owned_pids) == 4
        assert result.migration_lock.wait_event_type == "Lock"
        assert result.migration_lock.mode == "ShareLock"
        assert result.migration_lock.granted is False
        assert result.following_writer_lock.wait_event_type == "Lock"
        assert result.following_writer_lock.mode == "RowExclusiveLock"
        assert result.following_writer_lock.granted is False
        assert result.following_writer_finished_while_original_writer_open
        assert result.version == "0073_pending_blob_purge"
        assert result.pending_blob_purge_exists
        assert not result.checkpoint_sink_enabled_at_exists
        assert not result.audit_scope_ref_index_exists
        assert not result.import_commit_owner_snapshot_exists
        assert _sqlstate(result.migration_exception) == "55P03"


def test_failed_transactional_segment_keeps_0074_version(
    migration_database_factory: Callable[[], AbstractContextManager[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with migration_database_factory() as scratch_url:
        with _migration_environment(monkeypatch, scratch_url) as (
            config,
            migration_application_name,
        ):
            command.upgrade(config, "0074_operator_alarms")
            result = _exercise_autocommit_lock_contention(
                scratch_url,
                config,
                migration_application_name,
            )

        assert result.migration_lock.mode == "ShareLock"
        assert result.migration_lock.granted is False
        assert result.following_writer_lock.mode == "RowExclusiveLock"
        assert result.following_writer_lock.granted is False
        assert result.following_writer_finished_while_original_writer_open
        assert result.version == "0074_operator_alarms"
        assert result.pending_blob_purge_exists
        assert result.checkpoint_sink_enabled_at_exists
        assert not result.audit_scope_ref_index_exists
        assert not result.import_commit_owner_snapshot_exists
        assert _sqlstate(result.migration_exception) == "55P03"


def test_uncontended_populated_transition_commits_without_changing_ordinary_session_timeout(
    migration_database_factory: Callable[[], AbstractContextManager[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with migration_database_factory() as scratch_url:
        with _migration_environment(monkeypatch, scratch_url) as (
            config,
            migration_application_name,
        ):
            command.upgrade(config, "0072_disposition_append_only")
            ordinary = _connect(
                scratch_url,
                f"{migration_application_name}_ordinary",
                autocommit=True,
            )
            try:
                ordinary.execute("SET SESSION lock_timeout = '17s'")
                org_id = _create_synthetic_organization(ordinary, "uncontended")
                _insert_audit_event(ordinary, org_id, "lock-timeout-uncontended")

                command.upgrade(config, "0073_pending_blob_purge")

                with _connect(
                    scratch_url,
                    f"{migration_application_name}_verifier",
                    autocommit=True,
                ) as verifier:
                    version = verifier.execute("SELECT version_num FROM alembic_version").fetchone()
                    pending_table = verifier.execute(
                        "SELECT to_regclass('public.pending_blob_purge')"
                    ).fetchone()
                ordinary_timeout = ordinary.execute("SHOW lock_timeout").fetchone()
                fresh_engine = sa.create_engine(scratch_url)
                try:
                    with fresh_engine.connect() as fresh_connection:
                        fresh_timeout = fresh_connection.exec_driver_sql(
                            "SHOW lock_timeout"
                        ).scalar_one()
                finally:
                    fresh_engine.dispose()
            finally:
                ordinary.close()

        assert version == ("0073_pending_blob_purge",)
        assert pending_table == ("pending_blob_purge",)
        assert ordinary_timeout == ("17s",)
        assert fresh_timeout == "0"


def test_real_upgrade_reports_migration_lock_timeout_and_keeps_archive_pointer(
    migration_database_factory: Callable[[], AbstractContextManager[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from easysynq_api.services import upgrade as upgrade_service

    with migration_database_factory() as scratch_url:
        operator_application_name = f"easysynq_operator_upgrade_{uuid.uuid4().hex}"
        with _migration_environment(
            monkeypatch,
            scratch_url,
            application_name=operator_application_name,
        ) as (
            config,
            migration_application_name,
        ):
            command.upgrade(config, "0074_operator_alarms")
            blocker: psycopg.Connection[tuple[object, ...]] | None = None
            observer: psycopg.Connection[tuple[object, ...]] | None = None
            operator_outcome: queue.Queue[dict[str, object] | Exception] = queue.Queue(maxsize=1)
            operator_done = threading.Event()
            operator_thread: threading.Thread | None = None
            migration_pid: int | None = None
            readiness_called = False
            watchdog_cancelled = False
            expected_archive = "/synthetic/easysynq-pre-upgrade.tar.enc"

            def verified_backup_boundary(*_args: object, **_kwargs: object) -> dict[str, object]:
                return {"verified": True, "archive": expected_archive}

            async def readiness_must_not_run() -> list[dict[str, object]]:
                nonlocal readiness_called
                readiness_called = True
                raise AssertionError("readiness ran after migration failure")

            def run_operator_upgrade(operator_org_id: uuid.UUID) -> None:
                try:
                    operator_outcome.put(asyncio.run(upgrade_service.run_upgrade(operator_org_id)))
                except Exception as exc:  # noqa: BLE001 - surfaced to the owning test thread
                    operator_outcome.put(exc)
                finally:
                    operator_done.set()

            monkeypatch.setattr(upgrade_service, "build_durable_backup", verified_backup_boundary)
            monkeypatch.setattr(upgrade_service, "check_all", readiness_must_not_run)
            monkeypatch.setattr(
                upgrade_service,
                "_now",
                lambda: datetime.datetime(2026, 8, 15, 12, tzinfo=datetime.UTC),
            )

            try:
                blocker = _connect(
                    scratch_url,
                    f"{migration_application_name}_operator_blocker",
                )
                observer = _connect(
                    scratch_url,
                    f"{migration_application_name}_operator_observer",
                    autocommit=True,
                )
                owned_blocker = blocker
                owned_observer = observer
                blocker_pid = _backend_pid(owned_blocker)
                owned_blocker.commit()
                blocker_org_id = _create_synthetic_organization(
                    owned_observer,
                    "operator-blocker",
                )
                operator_org_id = _create_synthetic_organization(owned_observer, "operator")
                _insert_audit_event(
                    owned_blocker,
                    blocker_org_id,
                    "operator-lock-timeout-blocker",
                )
                baseline_row = owned_observer.execute(
                    "SELECT COALESCE(MAX(id), 0) FROM audit_event"
                ).fetchone()
                if baseline_row is None:
                    raise AssertionError("audit high-water query returned no row")
                audit_baseline = int(baseline_row[0])

                watchdog_deadline = time.monotonic() + _WATCHDOG_SECONDS
                operator_thread = threading.Thread(
                    target=run_operator_upgrade,
                    args=(uuid.UUID(str(operator_org_id)),),
                    name="operator-upgrade-lock-timeout",
                )
                operator_thread.start()
                migration_pid = _wait_for_migration_pid(
                    owned_observer,
                    migration_application_name,
                    watchdog_deadline,
                )
                migration_lock = _wait_for_relation_lock(
                    owned_observer,
                    migration_pid,
                    "ShareLock",
                    watchdog_deadline,
                )
                if not operator_done.wait(max(0.0, watchdog_deadline - time.monotonic())):
                    watchdog_cancelled = True
                    if not _cancel_backend(owned_observer, migration_pid):
                        raise AssertionError("watchdog could not cancel operator migration backend")
                    if not operator_done.wait(_CLEANUP_SECONDS):
                        raise AssertionError(
                            "operator upgrade did not return after migration cancel"
                        )

                outcome = operator_outcome.get(timeout=_CLEANUP_SECONDS)
                if isinstance(outcome, Exception):
                    raise AssertionError(
                        "run_upgrade raised instead of reporting failure"
                    ) from outcome
                events = owned_observer.execute(
                    """
                    SELECT event_type::text, after
                    FROM audit_event
                    WHERE org_id = %s AND id > %s
                      AND event_type::text IN (
                          'UPGRADE_STARTED', 'UPGRADE_FAILED', 'UPGRADE_COMPLETED'
                      )
                    ORDER BY id
                    """,
                    (operator_org_id, audit_baseline),
                ).fetchall()
                blocker_state = owned_observer.execute(
                    "SELECT state, xact_start IS NOT NULL FROM pg_stat_activity WHERE pid = %s",
                    (blocker_pid,),
                ).fetchone()
            finally:
                cleanup_errors: list[str] = []
                if operator_thread is not None and operator_thread.is_alive():
                    if observer is not None and migration_pid is not None:
                        try:
                            _cancel_backend(observer, migration_pid)
                        except Exception as exc:  # noqa: BLE001 - preserve cleanup failures
                            cleanup_errors.append(f"operator migration cancel failed: {exc}")
                    operator_thread.join(_CLEANUP_SECONDS)
                if operator_thread is not None and operator_thread.is_alive():
                    cleanup_errors.append("operator worker required backend termination")
                    if observer is not None:
                        try:
                            rows = observer.execute(
                                "SELECT pid FROM pg_stat_activity "
                                "WHERE datname = current_database() "
                                "AND application_name IN (%s, %s)",
                                (operator_application_name, migration_application_name),
                            ).fetchall()
                            owned_runtime_pids = {int(row[0]) for row in rows}
                            if migration_pid is not None:
                                owned_runtime_pids.add(migration_pid)
                            for owned_pid in owned_runtime_pids:
                                _terminate_backend(observer, owned_pid)
                        except Exception as exc:  # noqa: BLE001 - preserve cleanup failures
                            cleanup_errors.append(f"operator backend termination failed: {exc}")
                    operator_thread.join(_CLEANUP_SECONDS)
                if operator_thread is not None and operator_thread.is_alive():
                    cleanup_errors.append(
                        "operator worker remained alive after backend termination"
                    )
                if blocker is not None:
                    try:
                        blocker.rollback()
                    except Exception as exc:  # noqa: BLE001 - preserve cleanup failures
                        cleanup_errors.append(f"operator blocker rollback failed: {exc}")
                    try:
                        blocker.close()
                    except Exception as exc:  # noqa: BLE001 - preserve cleanup failures
                        cleanup_errors.append(f"operator blocker close failed: {exc}")
                if observer is not None:
                    try:
                        observer.close()
                    except Exception as exc:  # noqa: BLE001 - preserve cleanup failures
                        cleanup_errors.append(f"operator observer close failed: {exc}")
                if cleanup_errors:
                    raise AssertionError("; ".join(cleanup_errors))

        assert migration_lock.mode == "ShareLock"
        assert migration_lock.granted is False
        assert blocker_state == ("idle in transaction", True)
        assert watchdog_cancelled is False
        assert outcome["result"] == "FAILED"
        assert outcome["stage"] == "migrate"
        assert outcome["pre_backup_archive"] == expected_archive
        assert "LockNotAvailable" in str(outcome["reason"])
        assert "lock timeout" in str(outcome["reason"]).lower()
        assert readiness_called is False
        assert [event_type for event_type, _after in events] == [
            "UPGRADE_STARTED",
            "UPGRADE_FAILED",
        ]
        failure_after = dict(events[-1][1])
        assert failure_after["stage"] == "migrate"
        assert failure_after["pre_backup_archive"] == expected_archive
        assert "LockNotAvailable" in str(failure_after["error"])
        assert "lock timeout" in str(failure_after["error"]).lower()
