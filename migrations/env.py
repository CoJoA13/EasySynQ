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


# The full-text search index (slice S10) is a functional/expression GIN index created by the 0020
# migration via raw DDL — it cannot be round-tripped through ``Base.metadata`` (PG normalizes the
# ``to_tsvector('english', …)`` expression, so a modelled ``Index(text(...))`` never compares equal),
# so like the audit partitions it is excluded from autogenerate / ``alembic check``.
_MIGRATION_MANAGED_INDEXES = frozenset({"ix_documented_information_search_tsv"})


def _include_object(
    obj: object, name: str | None, type_: str, reflected: bool, compare_to: object
) -> bool:
    """Exclude objects that are created by migrations but never modelled in ``Base.metadata`` (so
    ``alembic check`` would otherwise try to drop them): the monthly ``audit_event_YYYY_MM`` child
    partitions + their PG-named child indexes (0010 + the ``roll_partitions`` Beat job, slice S6),
    and the functional FTS GIN index (0020, slice S10). The parent ``audit_event`` and every modelled
    table/index/column is still compared, so real schema drift is caught."""
    if name is not None and name.startswith("audit_event_"):
        return False
    return not (type_ == "index" and name in _MIGRATION_MANAGED_INDEXES)


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
