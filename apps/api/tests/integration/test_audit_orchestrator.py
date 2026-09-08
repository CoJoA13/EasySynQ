"""Real-PostgreSQL coverage for the nightly audit verification orchestrator.

This module owns a private migrated database so its empty linked-chain and no-sink premises do not
depend on audit fixtures in other integration modules. Assertions remain run-scoped because alarm
notifications and unlinked audit rows intentionally persist between tests in this module.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from sqlalchemy import func, select
from testcontainers.postgres import PostgresContainer

from easysynq_api import config
from easysynq_api.db.models._audit_enums import ActorType, AuditObjectType, EventType
from easysynq_api.db.models.app_user import AppUser, UserStatus
from easysynq_api.db.models.audit_checkpoint import AuditCheckpoint
from easysynq_api.db.models.audit_checkpoint_sink import AuditCheckpointSink
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.notification import Notification
from easysynq_api.db.models.organization import Organization
from easysynq_api.db.models.role import Role, RoleAssignment
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.audit.checkpoint import load_signing_key, load_verify_key
from easysynq_api.services.audit.verify import VerifyResult, verify_chain
from easysynq_api.services.notifications.constants import EVENT_INTEGRITY_ALARM
from easysynq_api.services.notifications.ops_channel import OperatorAlert
from easysynq_api.services.notifications.ops_events import (
    CHECK_VERIFY_KEY,
    CHECK_WITNESS_REQUIRED,
)
from easysynq_api.tasks import audit as audit_tasks

pytestmark = pytest.mark.integration

_ADMIN_ROLE = "System Administrator"


@pytest.fixture(scope="module")
def _pg() -> Iterator[str]:
    with PostgresContainer(
        "postgres:18", username="test", password="test", dbname="test", driver="psycopg"
    ) as pg:
        yield pg.get_connection_url()


async def _default_org_id() -> uuid.UUID:
    async with get_sessionmaker()() as session:
        org_ids = (
            (
                await session.execute(
                    select(Organization.id).order_by(Organization.created_at).limit(2)
                )
            )
            .scalars()
            .all()
        )
    assert len(org_ids) == 1
    return org_ids[0]


async def _seed_unique_admin(org_id: uuid.UUID) -> uuid.UUID:
    salt = uuid.uuid4().hex
    async with get_sessionmaker()() as session:
        user = AppUser(
            org_id=org_id,
            keycloak_subject=f"audit-orchestrator-admin-{salt}",
            display_name=f"Audit Orchestrator Admin {salt[:8]}",
            email=f"audit-orchestrator-{salt}@example.invalid",
            status=UserStatus.ACTIVE,
        )
        session.add(user)
        await session.flush()
        role_id = (
            await session.execute(
                select(Role.id).where(Role.org_id == org_id, Role.name == _ADMIN_ROLE)
            )
        ).scalar_one_or_none()
        if role_id is None:
            role = Role(org_id=org_id, name=_ADMIN_ROLE, is_reserved=True)
            session.add(role)
            await session.flush()
            role_id = role.id
        session.add(RoleAssignment(org_id=org_id, user_id=user.id, role_id=role_id))
        await session.commit()
        return user.id


def _configure_key_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    witness_required: bool,
) -> tuple[Path, Path]:
    private_path = tmp_path / "audit-orchestrator-signing.pem"
    public_path = tmp_path / "audit-orchestrator-public.pem"
    monkeypatch.setenv("AUDIT_CHECKPOINT_SIGNING_KEY_PATH", str(private_path))
    monkeypatch.setenv("AUDIT_CHECKPOINT_PUBLIC_KEY_PATH", str(public_path))
    monkeypatch.setenv("AUDIT_WITNESS_REQUIRED", "true" if witness_required else "false")
    config.get_settings.cache_clear()
    settings = config.get_settings()
    assert urlsplit(settings.database_url).username == "easysynq_app"
    return private_path, public_path


async def _audit_ids(org_id: uuid.UUID, event_type: EventType | None = None) -> set[int]:
    async with get_sessionmaker()() as session:
        statement = select(AuditEvent.id).where(AuditEvent.org_id == org_id)
        if event_type is not None:
            statement = statement.where(AuditEvent.event_type == event_type)
        return set((await session.execute(statement)).scalars().all())


async def _new_audit_rows(org_id: uuid.UUID, before: set[int]) -> list[AuditEvent]:
    async with get_sessionmaker()() as session:
        rows = (
            (await session.execute(select(AuditEvent).where(AuditEvent.org_id == org_id)))
            .scalars()
            .all()
        )
        return [row for row in rows if row.id not in before]


async def _notifications_for(user_id: uuid.UUID) -> list[Notification]:
    async with get_sessionmaker()() as session:
        return list(
            (
                await session.execute(
                    select(Notification).where(
                        Notification.recipient_user_id == user_id,
                        Notification.event_key == EVENT_INTEGRITY_ALARM,
                    )
                )
            )
            .scalars()
            .all()
        )


async def _real_verify(org_id: uuid.UUID, verify_key: Any) -> VerifyResult:
    async with get_sessionmaker()() as session:
        return await verify_chain(session, org_id, verify_key=verify_key)


async def _assert_empty_chain_checkpoint_and_sinks(org_id: uuid.UUID) -> None:
    async with get_sessionmaker()() as session:
        linked = (
            await session.execute(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.org_id == org_id, AuditEvent.chained_at.is_not(None))
            )
        ).scalar_one()
        checkpoints = (
            await session.execute(
                select(func.count())
                .select_from(AuditCheckpoint)
                .where(AuditCheckpoint.org_id == org_id)
            )
        ).scalar_one()
        sinks = (
            await session.execute(
                select(func.count())
                .select_from(AuditCheckpointSink)
                .where(AuditCheckpointSink.org_id == org_id)
            )
        ).scalar_one()
    assert linked == 0
    assert checkpoints == 0
    assert sinks == 0


def _capture_alerts(
    monkeypatch: pytest.MonkeyPatch,
) -> list[OperatorAlert]:
    captured: list[OperatorAlert] = []

    async def capture(_settings: config.Settings, alert: OperatorAlert) -> dict[str, str]:
        captured.append(alert)
        return {}

    monkeypatch.setattr(audit_tasks, "send_operator_alert", capture)
    return captured


async def test_missing_verify_key_commits_admin_alarm_without_tamper_event(
    app_under_test: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    private_path, public_path = _configure_key_paths(monkeypatch, tmp_path, witness_required=False)
    assert app_under_test is not None
    assert not private_path.exists()
    assert not public_path.exists()
    assert load_verify_key() is None

    org_id = await _default_org_id()
    admin_id = await _seed_unique_admin(org_id)
    await _assert_empty_chain_checkpoint_and_sinks(org_id)
    local = await _real_verify(org_id, None)
    assert local.verified is True
    assert local.checked == 0
    assert local.breaks == []

    before_all = await _audit_ids(org_id)
    before_failures = await _audit_ids(org_id, EventType.CHAIN_VERIFY_FAIL)
    captured = _capture_alerts(monkeypatch)

    result = await audit_tasks._run_verify_chain()

    assert result == 0
    assert await _audit_ids(org_id) == before_all
    assert await _audit_ids(org_id, EventType.CHAIN_VERIFY_FAIL) == before_failures
    notes = await _notifications_for(admin_id)
    assert len(notes) == 1
    note = notes[0]
    assert note.org_id == org_id
    assert note.title == "Audit integrity alarm"
    assert note.context is not None
    assert note.context["check"] == CHECK_VERIFY_KEY
    assert note.context["break_count"] == 0
    assert "attestation DISABLED" in note.context["reason_summary"]
    assert CHECK_VERIFY_KEY in note.body
    assert "attestation DISABLED" in note.body
    assert "{{" not in note.title
    assert "{{" not in note.body

    assert len(captured) == 1
    alert = captured[0]
    assert alert.event == EVENT_INTEGRITY_ALARM
    assert alert.severity == "critical"
    assert alert.summary == "audit verify key missing — checkpoint attestation is DISABLED"
    assert alert.detail["check"] == CHECK_VERIFY_KEY
    assert "attestation DISABLED" in alert.detail["reason"]
    assert alert.org_id is None


async def test_clean_verification_leaves_no_alarm_or_audit_rows(
    app_under_test: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_key_paths(monkeypatch, tmp_path, witness_required=False)
    assert app_under_test is not None
    load_signing_key()
    verify_key = load_verify_key()
    assert verify_key is not None

    org_id = await _default_org_id()
    admin_id = await _seed_unique_admin(org_id)
    await _assert_empty_chain_checkpoint_and_sinks(org_id)
    local = await _real_verify(org_id, verify_key)
    assert local.verified is True
    assert local.checked == 0
    assert local.breaks == []
    assert local.checkpoint is not None
    assert local.checkpoint.present is False

    before_all = await _audit_ids(org_id)
    before_failures = await _audit_ids(org_id, EventType.CHAIN_VERIFY_FAIL)
    captured = _capture_alerts(monkeypatch)

    result = await audit_tasks._run_verify_chain()

    assert result == 0
    assert captured == []
    assert await _notifications_for(admin_id) == []
    assert await _audit_ids(org_id) == before_all
    assert await _audit_ids(org_id, EventType.CHAIN_VERIFY_FAIL) == before_failures


async def test_required_witness_on_empty_linked_chain_persists_alarm_and_returns_nonzero(
    app_under_test: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_key_paths(monkeypatch, tmp_path, witness_required=True)
    assert app_under_test is not None
    load_signing_key()
    verify_key = load_verify_key()
    assert verify_key is not None

    org_id = await _default_org_id()
    admin_id = await _seed_unique_admin(org_id)
    await _assert_empty_chain_checkpoint_and_sinks(org_id)
    local = await _real_verify(org_id, verify_key)
    assert local.verified is True
    assert local.checked == 0
    assert local.breaks == []
    assert local.checkpoint is not None
    assert local.checkpoint.present is False

    before_all = await _audit_ids(org_id)
    before_failures = await _audit_ids(org_id, EventType.CHAIN_VERIFY_FAIL)
    captured = _capture_alerts(monkeypatch)

    result = await audit_tasks._run_verify_chain()

    assert result == 1
    new_rows = await _new_audit_rows(org_id, before_all)
    assert len(new_rows) == 1
    event = new_rows[0]
    assert event.id not in before_failures
    assert event.event_type is EventType.CHAIN_VERIFY_FAIL
    assert event.actor_type is ActorType.system
    assert event.actor_id is None
    assert event.object_type is AuditObjectType.audit
    assert event.object_id == org_id
    assert event.after is not None
    assert event.after["break_count"] == 0
    assert event.after["first_break_at_id"] is None
    assert event.after["checkpoint_reason"] == "no checkpoint anchored yet"
    assert len(event.after["offhost_reasons"]) == 2
    assert any("AUDIT_WITNESS_REQUIRED" in reason for reason in event.after["offhost_reasons"])
    assert any("declared witness is absent" in reason for reason in event.after["offhost_reasons"])
    assert event.prev_hash is None
    assert event.row_hash is None
    assert event.chained_at is None
    assert await _audit_ids(org_id, EventType.CHAIN_VERIFY_FAIL) == before_failures | {event.id}

    notes = await _notifications_for(admin_id)
    assert len(notes) == 1
    note = notes[0]
    assert note.org_id == org_id
    assert note.title == "Audit integrity alarm"
    assert note.context is not None
    assert note.context["check"] == CHECK_WITNESS_REQUIRED
    assert note.context["break_count"] == 0
    assert "AUDIT_WITNESS_REQUIRED" in note.context["reason_summary"]
    assert CHECK_WITNESS_REQUIRED in note.body
    assert "declared witness is absent" in note.body
    assert "{{" not in note.title
    assert "{{" not in note.body

    assert len(captured) == 1
    alert = captured[0]
    assert alert.event == EVENT_INTEGRITY_ALARM
    assert alert.severity == "critical"
    assert alert.detail["check"] == CHECK_WITNESS_REQUIRED
    assert alert.detail["break_count"] == 0
    assert any("declared witness is absent" in reason for reason in alert.detail["reasons"])
    assert alert.org_id == str(org_id)
