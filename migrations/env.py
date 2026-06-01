"""Alembic environment. Single migration tree for the whole app.

Uses the SYNC DSN (psycopg3 sync) — migrations are never coupled to the app's
async event loop (doc 18 §4). ``target_metadata`` is the declarative ``Base`` with
all models imported so autogenerate sees the full schema.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Import models so Base.metadata is fully populated.
import easysynq_api.db.models  # noqa: F401
from easysynq_api.config import get_settings
from easysynq_api.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

_DSN = get_settings().sync_dsn
config.set_main_option("sqlalchemy.url", _DSN)

target_metadata = Base.metadata


def _include_object(
    obj: object, name: str | None, type_: str, reflected: bool, compare_to: object
) -> bool:
    """Exclude the monthly ``audit_event_YYYY_MM`` child partitions (and their PG-named child
    indexes) from autogenerate / ``alembic check``. They are created dynamically by the 0010
    migration + the ``roll_partitions`` Beat job — never modelled in ``Base.metadata`` — so without
    this filter ``alembic check`` would try to drop them. The parent ``audit_event`` (and its
    explicitly-named ``brin_*`` / ``ix_*`` indexes) are NOT excluded, so column drift is still
    caught (slice S6)."""
    return not (name is not None and name.startswith("audit_event_"))


def run_migrations_offline() -> None:
    context.configure(
        url=_DSN,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _DSN
    connectable = engine_from_config(
        section, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=_include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
