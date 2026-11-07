"""Completed-batch progress on the worker's engine, independent of staged results."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, async_sessionmaker

from ...db.models._ingestion_enums import ImportRunStatus
from . import locks
from . import repository as repo


class PipelineProgressStopped(Exception):
    """The stage or source-lock owner changed; discard pending results without failing the run."""


async def heartbeat_batch(
    session: AsyncSession,
    run_id: uuid.UUID,
    *,
    source_root_hash: str,
    token: str | None,
    expected_status: ImportRunStatus,
    ttl: int,
    advanced: bool = True,
) -> None:
    """Admit the current stage/owner and persist only completed-work progress.

    Call after a batch commit or during read-only computation, never while holding the run row
    lock. A connection-bound caller still gets a separate connection from its own engine.
    Startup keep-alives use ``advanced=False`` so they cannot hide a lack of completed work.
    """
    bind = getattr(session, "bind", None)
    if isinstance(bind, AsyncEngine):
        engine = bind
    elif isinstance(bind, AsyncConnection):
        engine = bind.engine
    else:
        raise ValueError("Batch progress requires an AsyncEngine or AsyncConnection binding")
    async with async_sessionmaker(engine, expire_on_commit=False)() as progress_session:
        run = await repo.get_run(progress_session, run_id, for_update=True)
        if (
            run is None
            or not token
            or run.status is not expected_status
            or run.source_root_hash != source_root_hash
            or run.lock_token != token
        ):
            raise PipelineProgressStopped
        if not await locks.heartbeat(source_root_hash, token, ttl=ttl):
            raise PipelineProgressStopped
        if advanced and not await repo.record_batch_progress(
            progress_session,
            run_id,
            source_root_hash=source_root_hash,
            token=token,
            expected_status=expected_status,
        ):
            raise PipelineProgressStopped
        await progress_session.commit()
