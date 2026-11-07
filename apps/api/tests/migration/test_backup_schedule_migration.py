"""A populated policy survives scheduler-watermark upgrade and downgrade."""

import asyncio
import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from easysynq_api.config import get_settings
from easysynq_api.readiness import MIGRATIONS_DIR


def test_populated_backup_watermark_roundtrip(migration_database_factory, monkeypatch):
    with migration_database_factory() as url:
        monkeypatch.setenv("DATABASE_URL", url)
        monkeypatch.setenv("DATABASE_URL_SYNC", url)
        get_settings.cache_clear()
        config = Config()
        config.set_main_option("script_location", str(MIGRATIONS_DIR))
        engine = sa.create_engine(url)
        org, policy = uuid.uuid4(), uuid.uuid4()
        try:
            command.upgrade(config, "0093_blob_object_version_binding")
            with engine.begin() as conn:
                conn.execute(
                    sa.text(
                        "INSERT INTO organization(id,legal_name,short_code) "
                        "VALUES (:id,'Synthetic migration','SYNTHETIC')"
                    ),
                    {"id": org},
                )
                conn.execute(
                    sa.text(
                        "INSERT INTO backup_policy(id,org_id,destination,cron) "
                        "VALUES (:id,:org,'/synthetic/backup','0 2 * * *')"
                    ),
                    {"id": policy, "org": org},
                )
            # The new image must read the configured pre-upgrade destination BEFORE its new
            # schema exists. Loading the whole current ORM model breaks this safety boundary.
            from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

            from easysynq_api.services.upgrade import _backup_destination

            async def before_upgrade():
                async_engine = create_async_engine(url)
                try:
                    async with AsyncSession(async_engine) as session:
                        assert await _backup_destination(session, org) == "/synthetic/backup"
                        assert (
                            await _backup_destination(session, uuid.uuid4())
                            == get_settings().backup_path
                        )
                finally:
                    await async_engine.dispose()

            asyncio.run(before_upgrade())
            command.upgrade(config, "head")
            with engine.begin() as conn:
                assert (
                    conn.execute(
                        sa.text("SELECT last_scheduled_attempt_at FROM backup_policy WHERE id=:id"),
                        {"id": policy},
                    ).scalar_one()
                    is None
                )
                conn.execute(
                    sa.text("UPDATE backup_policy SET last_scheduled_attempt_at=:now WHERE id=:id"),
                    {"id": policy, "now": datetime(2026, 10, 1, 2, tzinfo=UTC)},
                )
            command.downgrade(config, "0093_blob_object_version_binding")
            with engine.connect() as conn:
                assert conn.execute(
                    sa.text("SELECT destination,cron FROM backup_policy WHERE id=:id"),
                    {"id": policy},
                ).one() == ("/synthetic/backup", "0 2 * * *")
                assert "last_scheduled_attempt_at" not in {
                    c["name"] for c in sa.inspect(conn).get_columns("backup_policy")
                }
            command.upgrade(config, "head")
            with engine.connect() as conn:
                assert (
                    conn.execute(
                        sa.text("SELECT last_scheduled_attempt_at FROM backup_policy WHERE id=:id"),
                        {"id": policy},
                    ).scalar_one()
                    is None
                )
            command.check(config)
        finally:
            engine.dispose()
            get_settings.cache_clear()
