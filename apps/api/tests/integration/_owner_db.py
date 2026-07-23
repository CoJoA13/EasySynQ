"""Owner-role DB access for test teardown of append-only rows.

The app (and thus ``get_sessionmaker()``) connects as the non-owner ``easysynq_app`` role
(conftest), which — after migration 0072, matching the AC#6a house style — is structurally denied
UPDATE/DELETE on the append-only ``disposition_event`` tombstone. Test teardown that must remove
those rows (to clear the FK-RESTRICT chain before deleting the record) therefore runs as the OWNER
(``database_url_sync``), exactly as alembic + the AC#6a tests already do.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from easysynq_api.config import get_settings
from easysynq_api.db.models.disposition_event import DispositionEvent


async def owner_delete_disposition_events(record_ids: Sequence[uuid.UUID]) -> None:
    """DELETE the ``disposition_event`` tombstones for ``record_ids`` as the OWNER role. No-op on an
    empty list. Opens (and disposes) a dedicated owner engine per call — the AC#6a per-test-engine
    pattern, safe across pytest-asyncio's per-test event loop."""
    ids = list(record_ids)
    if not ids:
        return
    owner_dsn = get_settings().database_url_sync
    assert owner_dsn is not None, (
        "database_url_sync (owner role) must be set for append-only teardown"
    )
    engine = create_async_engine(owner_dsn)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            await session.execute(
                delete(DispositionEvent).where(DispositionEvent.record_id.in_(ids))
            )
            await session.commit()
    finally:
        await engine.dispose()
