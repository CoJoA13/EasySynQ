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


# Functional/expression + partial indexes created by migrations via raw DDL — they cannot be
# round-tripped through ``Base.metadata`` (PG normalizes the expression/predicate, so a modelled
# ``Index`` never compares equal), so like the audit partitions they are excluded from autogenerate /
# ``alembic check``:
#   * ``ix_documented_information_search_tsv`` — the FTS GIN index (0020, slice S10).
#   * ``ix_worm_destroy_request_open`` — the "one open request per record" partial UNIQUE (0024,
#     ``... WHERE executed_at IS NULL AND cancelled_at IS NULL``; slice S-rec-2).
#   * ``uq_import_decision_run_idem`` — the review-decision idempotency partial UNIQUE (0032,
#     ``... WHERE idempotency_key IS NOT NULL``; slice S-ing-4).
#   * ``uq_dcr_spawn_idempotency_key`` — the CAPA→DCR spawn idempotency partial UNIQUE (0044,
#     ``... WHERE spawn_idempotency_key IS NOT NULL``; slice S-dcr-5).
#   * ``uq_improvement_initiative_spawn`` — the improvement-initiative spawn idempotency partial
#     UNIQUE (0052, ``(org_id, source_link_id, spawn_idempotency_key) WHERE spawn_idempotency_key IS
#     NOT NULL``; slice S-improvement-1).
#   * ``uq_notification_dedup_task`` — the notification dedup partial UNIQUE (0063,
#     ``... WHERE task_id IS NOT NULL``; slice S-notify-1).
#   * ``uq_notification_email_one_per_notification`` — the notification_email one-per-notification
#     partial UNIQUE (0064, ``... WHERE notification_id IS NOT NULL``; slice S-notify-3a).
#   * ``ix_notification_digest_pending`` — the partial index backing the hourly digest sweep
#     (0064, ``(digest_due_at, recipient_user_id) WHERE digested_at IS NULL AND digest_due_at IS NOT
#     NULL``; slice S-notify-3a Codex P2).
_MIGRATION_MANAGED_INDEXES = frozenset(
    {
        "ix_documented_information_search_tsv",
        "ix_worm_destroy_request_open",
        "uq_import_decision_run_idem",
        "uq_dcr_spawn_idempotency_key",
        "uq_improvement_initiative_spawn",
        "uq_notification_dedup_task",
        "uq_notification_email_one_per_notification",
        "ix_notification_digest_pending",
        "ix_task_timer_pending",
        "ix_awareness_event_pending",          # S-notify-5a claim scan
        "uq_notification_dedup_awareness",     # S-notify-5a version-discriminated dedup
    }
)


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
