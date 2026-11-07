"""Real PostgreSQL claims and archive publication, with synthetic source bytes."""

import asyncio
import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, text

from easysynq_api.config import get_settings
from easysynq_api.db.models.backup_policy import BackupPolicy
from easysynq_api.db.models.organization import Organization
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.backup import drill, service

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 1, 9, tzinfo=UTC)  # 02:00 Los Angeles


@pytest.fixture
async def policy(app_under_test, tmp_path, monkeypatch):
    del app_under_test
    org_id, policy_id = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(service, "_now", lambda: NOW)
    monkeypatch.setattr(drill, "_latest_checkpoint_bundle", lambda *_: None)
    monkeypatch.setattr(drill.realm_export, "export_realm", lambda **_: None)
    monkeypatch.setattr(drill.config_snapshot, "build_config_snapshot", lambda *_: {})

    def capture(_dsn, path):
        path.write_bytes(b"PGDMP synthetic scheduled source")
        return {"organization": 1}, []

    monkeypatch.setattr(drill, "_capture_and_dump", capture)
    async with get_sessionmaker()() as session:
        session.add(
            Organization(
                id=org_id,
                legal_name="Synthetic scheduler",
                short_code=org_id.hex,
                timezone="America/Los_Angeles",
            )
        )
        await session.flush()
        session.add(
            BackupPolicy(
                id=policy_id,
                org_id=org_id,
                destination=str(tmp_path),
                cron="0 2 * * *",
                created_at=NOW - timedelta(days=4),
            )
        )
        await session.commit()
    try:
        yield org_id, policy_id, tmp_path
    finally:
        async with get_sessionmaker()() as session:
            await session.execute(delete(BackupPolicy).where(BackupPolicy.id == policy_id))
            await session.execute(delete(Organization).where(Organization.id == org_id))
            await session.commit()


def own(result, org_id):
    return [row for row in result["backups"] if row["org_id"] == str(org_id)]


async def marker(policy_id):
    async with get_sessionmaker()() as session:
        row = await session.get(BackupPolicy, policy_id)
        return row.last_scheduled_attempt_at


async def test_schedule_uses_stored_cron_org_timezone_and_persisted_watermark(policy, monkeypatch):
    org_id, policy_id, destination = policy
    monkeypatch.setattr(service, "_now", lambda: NOW - timedelta(minutes=1))
    # A new policy before today's slot must not invent older occurrences.
    async with get_sessionmaker()() as session:
        row = await session.get(BackupPolicy, policy_id)
        row.created_at = NOW - timedelta(hours=1)
        await session.commit()
    assert own(await service.run_scheduled_backups(only_due=True), org_id) == []
    assert await marker(policy_id) is None
    monkeypatch.setattr(service, "_now", lambda: NOW)
    first = own(await service.run_scheduled_backups(only_due=True), org_id)
    assert len(first) == 1 and first[0]["verified"]
    assert await marker(policy_id) == NOW
    assert own(await service.run_scheduled_backups(only_due=True), org_id) == []
    # The immediate CLI/service path remains immediate and doesn't consume tomorrow's schedule.
    assert len(own(await service.run_scheduled_backups(), org_id)) == 1
    assert await marker(policy_id) == NOW
    assert len(list(destination.glob("*.tar*"))) == 4


async def test_concurrent_workers_skip_locked_policy_and_redelivery_does_not_duplicate(
    policy, monkeypatch
):
    org_id, policy_id, destination = policy
    entered, release = threading.Event(), threading.Event()
    original = drill._capture_and_dump

    def blocked(dsn, path):
        entered.set()
        assert release.wait(15), "test did not release source capture"
        return original(dsn, path)

    monkeypatch.setattr(drill, "_capture_and_dump", blocked)
    first = asyncio.create_task(service.run_scheduled_backups(only_due=True))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        assert own(await service.run_scheduled_backups(only_due=True), org_id) == []
    finally:
        release.set()
    assert len(own(await first, org_id)) == 1
    assert own(await service.run_scheduled_backups(only_due=True), org_id) == []
    assert await marker(policy_id) == NOW
    assert len(list(destination.glob("*.tar*"))) == 2


async def test_missed_late_minute_catches_up_once_after_local_midnight(policy, monkeypatch):
    org_id, policy_id, destination = policy
    last = datetime.fromisoformat("2026-10-02T06:00Z")  # October 1, 23:00 Los Angeles
    now = datetime.fromisoformat("2026-10-02T07:00Z")  # October 2, 00:00 Los Angeles
    async with get_sessionmaker()() as session:
        row = await session.get(BackupPolicy, policy_id)
        row.cron = "0,30 23 * * *"
        row.last_scheduled_attempt_at = last
        await session.commit()
    monkeypatch.setattr(service, "_now", lambda: now)
    caught_up = own(await service.run_scheduled_backups(only_due=True), org_id)
    assert len(caught_up) == 1 and caught_up[0]["verified"]
    assert await marker(policy_id) == now
    assert own(await service.run_scheduled_backups(only_due=True), org_id) == []
    assert len(list(destination.glob("*.tar*"))) == 2


async def test_handled_failure_consumes_attempt_and_reports_without_poisoning_next_run(
    policy, monkeypatch
):
    org_id, policy_id, _ = policy
    reports = []

    async def report(_sm, **kwargs):
        reports.append(kwargs)

    monkeypatch.setattr(service, "_report_backup_failure", report)

    def broken(*_):
        raise OSError("synthetic source failure")

    monkeypatch.setattr(drill, "_capture_and_dump", broken)
    result = own(await service.run_scheduled_backups(only_due=True), org_id)
    assert "synthetic source failure" in result[0]["error"]
    assert await marker(policy_id) == NOW
    assert any(report["org_id"] == org_id for report in reports)
    assert own(await service.run_scheduled_backups(only_due=True), org_id) == []


async def test_locked_policy_reads_current_cron(policy, monkeypatch):
    org_id, policy_id, _ = policy
    async with get_sessionmaker()() as session:
        row = await session.get(BackupPolicy, policy_id)
        row.created_at = NOW - timedelta(hours=1)
        row.cron = "15 2 * * *"
        await session.commit()
    assert own(await service.run_scheduled_backups(only_due=True), org_id) == []
    monkeypatch.setattr(service, "_now", lambda: NOW + timedelta(minutes=15))
    assert len(own(await service.run_scheduled_backups(only_due=True), org_id)) == 1


async def test_lost_database_lock_does_not_overlap_live_archive_writer(policy, monkeypatch):
    import psycopg

    from easysynq_api.services.backup.dsn import conn_kwargs

    org_id, policy_id, destination = policy
    entered, release = threading.Event(), threading.Event()
    captures, backend = [], []
    original_capture, original_tz = drill._capture_and_dump, service.resolve_org_tz

    async def resolve(session, org):
        if org == org_id:
            backend.append(await session.scalar(text("select pg_backend_pid()")))
        return await original_tz(session, org)

    def blocked(dsn, path):
        captures.append(path)
        entered.set()
        assert release.wait(15)
        return original_capture(dsn, path)

    async def report(*_args, **_kwargs):
        pass

    monkeypatch.setattr(service, "resolve_org_tz", resolve)
    monkeypatch.setattr(service, "_report_backup_failure", report)
    monkeypatch.setattr(drill, "_capture_and_dump", blocked)
    first = asyncio.create_task(service.run_scheduled_backups(only_due=True))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        # Only terminate the exact backend captured from this disposable test's owned worker.
        with psycopg.connect(**conn_kwargs(get_settings().sync_dsn), autocommit=True) as conn:
            assert conn.execute("SELECT pg_terminate_backend(%s)", (backend[0],)).fetchone()[0]
        assert own(await service.run_scheduled_backups(only_due=True), org_id) == []
        assert await marker(policy_id) is None
    finally:
        release.set()
    assert "error" in own(await first, org_id)[0]
    assert await marker(policy_id) is None
    recovered = own(await service.run_scheduled_backups(only_due=True), org_id)
    assert len(recovered) == 1 and recovered[0]["verified"]
    assert await marker(policy_id) == NOW
    assert len(captures) == 1
    assert len(list(destination.glob("*.tar*"))) == 2


async def test_precommit_crash_then_days_later_captures_fresh_once(policy, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession

    org_id, policy_id, destination = policy
    monday = NOW - timedelta(days=3)
    monkeypatch.setattr(service, "_now", lambda: monday)
    original_commit = AsyncSession.commit
    crashes = []

    async def commit(session):
        if not crashes and any(
            isinstance(row, BackupPolicy) and row.id == policy_id for row in session.dirty
        ):
            crashes.append(True)
            raise RuntimeError("synthetic precommit crash")
        await original_commit(session)

    async def report(*_args, **_kwargs):
        pass

    monkeypatch.setattr(AsyncSession, "commit", commit)
    monkeypatch.setattr(service, "_report_backup_failure", report)
    assert "error" in own(await service.run_scheduled_backups(only_due=True), org_id)[0]
    assert await marker(policy_id) is None
    old = next(destination.glob("*.tar*"))
    monkeypatch.setattr(service, "_now", lambda: NOW)
    fresh = own(await service.run_scheduled_backups(only_due=True), org_id)
    assert len(fresh) == 1 and fresh[0]["verified"]
    assert fresh[0]["archive"] != str(old)
    assert await marker(policy_id) == NOW
    assert own(await service.run_scheduled_backups(only_due=True), org_id) == []
    assert len(list(destination.glob("*.tar*"))) == 4


async def test_long_capture_does_not_consume_next_occurrence(policy, monkeypatch):
    org_id, policy_id, _ = policy
    clock = [NOW]
    monkeypatch.setattr(service, "_now", lambda: clock[0])
    async with get_sessionmaker()() as session:
        row = await session.get(BackupPolicy, policy_id)
        row.cron = "* * * * *"
        await session.commit()
    capture = drill._capture_and_dump

    def slow(dsn, path):
        result = capture(dsn, path)
        clock[0] += timedelta(minutes=2)
        return result

    monkeypatch.setattr(drill, "_capture_and_dump", slow)
    assert len(own(await service.run_scheduled_backups(only_due=True), org_id)) == 1
    assert await marker(policy_id) == NOW
    assert len(own(await service.run_scheduled_backups(only_due=True), org_id)) == 1
    assert await marker(policy_id) == NOW + timedelta(minutes=2)


async def test_malformed_cron_falls_back_with_warning(policy, caplog):
    org_id, policy_id, _ = policy
    async with get_sessionmaker()() as session:
        row = await session.get(BackupPolicy, policy_id)
        row.cron = "bad saved configuration"
        await session.commit()
    assert len(own(await service.run_scheduled_backups(only_due=True), org_id)) == 1
    assert "invalid backup cron" in caplog.text


async def test_database_failure_after_policy_list_still_alerts_out_of_band(policy, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession

    _, policy_id, _ = policy
    alerts = []
    real_scalar = AsyncSession.scalar

    async def scalar(session, statement, *args, **kwargs):
        if getattr(statement, "_for_update_arg", None) is not None:
            raise OSError("synthetic database outage after policy listing")
        return await real_scalar(session, statement, *args, **kwargs)

    async def alert(settings, payload):
        alerts.append(payload)

    monkeypatch.setattr(AsyncSession, "scalar", scalar)
    monkeypatch.setattr(service, "send_operator_alert", alert)
    result = await service.run_scheduled_backups(only_due=True)
    assert any(
        row.get("policy_id") == str(policy_id) and "error" in row for row in result["backups"]
    )
    assert alerts and all(item.severity == "critical" for item in alerts)
    assert await marker(policy_id) is None


async def test_default_calendar_timezone_precedes_org_timezone(policy):
    import psycopg

    from easysynq_api.db.models.working_calendar import WorkingCalendar
    from easysynq_api.services.backup.dsn import conn_kwargs

    org_id, policy_id, _ = policy
    calendar_id = uuid.uuid4()
    async with get_sessionmaker()() as session:
        org = await session.get(Organization, org_id)
        org.timezone = "UTC"
        row = await session.get(BackupPolicy, policy_id)
        row.created_at = NOW - timedelta(hours=1)
        session.add(
            WorkingCalendar(
                id=calendar_id,
                org_id=org_id,
                name="Synthetic",
                working_days=[1, 2, 3, 4, 5],
                holidays=[],
                timezone="America/Los_Angeles",
                is_default=True,
            )
        )
        await session.commit()
    try:
        assert len(own(await service.run_scheduled_backups(only_due=True), org_id)) == 1
    finally:
        # Operational calendars intentionally disallow runtime DELETE; only the disposable test
        # owner removes the exact fixture row before the fixture organization is cleaned up.
        with psycopg.connect(**conn_kwargs(get_settings().sync_dsn), autocommit=True) as conn:
            conn.execute("DELETE FROM working_calendar WHERE id=%s", (calendar_id,))


async def test_one_failed_org_does_not_abort_another_policy(policy, monkeypatch, tmp_path):
    org_id, _, destination = policy
    second_org, second_policy = uuid.uuid4(), uuid.uuid4()
    async with get_sessionmaker()() as session:
        session.add(
            Organization(
                id=second_org,
                legal_name="Synthetic second org",
                short_code=second_org.hex,
                timezone="UTC",
            )
        )
        await session.flush()
        session.add(
            BackupPolicy(
                id=second_policy,
                org_id=second_org,
                destination=str(tmp_path / "second"),
                cron="0 2 * * *",
                created_at=NOW - timedelta(days=1),
            )
        )
        await session.commit()
    original = service.build_scheduled_backup

    def build(*args, **kwargs):
        if kwargs["destination"] == str(destination):
            raise OSError("synthetic first destination unavailable")
        return original(*args, **kwargs)

    async def report(*args, **kwargs):
        pass

    monkeypatch.setattr(service, "build_scheduled_backup", build)
    monkeypatch.setattr(service, "_report_backup_failure", report)
    try:
        result = await service.run_scheduled_backups(only_due=True)
        assert "error" in own(result, org_id)[0]
        assert own(result, second_org)[0]["verified"]
        assert await marker(second_policy) == NOW
    finally:
        async with get_sessionmaker()() as session:
            await session.execute(delete(BackupPolicy).where(BackupPolicy.id == second_policy))
            await session.execute(delete(Organization).where(Organization.id == second_org))
            await session.commit()


async def test_clock_rollback_between_database_and_file_claim_consumes_nothing(policy, monkeypatch):
    org_id, policy_id, destination = policy
    async with get_sessionmaker()() as session:
        row = await session.get(BackupPolicy, policy_id)
        row.created_at = NOW - timedelta(hours=1)
        await session.commit()
    clock = iter([NOW, NOW - timedelta(days=1)])
    monkeypatch.setattr(service, "_now", lambda: next(clock))
    assert own(await service.run_scheduled_backups(only_due=True), org_id) == []
    assert await marker(policy_id) is None
    assert not list(destination.glob("*.tar*"))
