"""Backfill ``capa.target_completion_date`` for existing non-terminal CAPAs (S-capa-overdue).

Before S-capa-overdue, CAPAs were raised without a ``target_completion_date``.  Task 2 now
defaults the date at raise time, but EXISTING rows remain NULL.  This one-time, idempotent CLI
sets the canonical default for each non-terminal CAPA where the column is still NULL:

    target_completion_date = default_target_date(severity, created_at_in_org_tz)

Where ``created_at_in_org_tz`` is the CAPA record's ``documented_information.created_at``
converted to the org's canonical timezone (via ``resolve_org_tz``).

Terminal CAPAs (``Closed`` or ``Rejected``) are never touched.  The ``IS NULL`` filter makes
the run idempotent: a re-run changes 0 rows.

Usage (inside the api image, DB reachable):

    python -m easysynq_api.cli.backfill_capa_target_dates --dry-run
    python -m easysynq_api.cli.backfill_capa_target_dates
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import get_settings
from ..db.models._capa_enums import CapaCloseState
from ..db.models.capa import Capa
from ..db.models.documented_information import DocumentedInformation
from ..domain.capa import default_target_date
from ..services.common.org_clock import resolve_org_tz

_TERMINAL_STATES = {CapaCloseState.Closed, CapaCloseState.Rejected}


async def backfill(
    session: AsyncSession, *, dry_run: bool
) -> list[tuple[uuid.UUID, datetime.date]]:
    """Set target_completion_date for every non-terminal CAPA where it is NULL.

    Returns the list of (capa_id, computed_date) for each row that was (or would be) updated.
    Commits when not dry_run.
    """
    changed: list[tuple[uuid.UUID, datetime.date]] = []

    # Fetch all non-terminal CAPAs with NULL target_completion_date, joined to get created_at.
    rows = (
        await session.execute(
            select(Capa, DocumentedInformation.created_at)
            .join(DocumentedInformation, DocumentedInformation.id == Capa.id)
            .where(
                Capa.target_completion_date.is_(None),
                Capa.close_state.not_in(list(_TERMINAL_STATES)),
            )
        )
    ).all()

    for capa, created_at in rows:
        org_tz = await resolve_org_tz(session, capa.org_id)
        raised_on: datetime.date = created_at.astimezone(org_tz).date()
        new_date = default_target_date(capa.severity, raised_on)
        changed.append((capa.id, new_date))
        if not dry_run:
            capa.target_completion_date = new_date

    if not dry_run:
        await session.commit()

    return changed


async def _run(dry_run: bool) -> list[tuple[uuid.UUID, datetime.date]]:
    engine = create_async_engine(get_settings().database_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessionmaker() as session:
            return await backfill(session, dry_run=dry_run)
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="easysynq-backfill-capa-target-dates",
        description=(
            "Set target_completion_date for existing non-terminal CAPAs where it is NULL "
            "(S-capa-overdue backfill)."
        ),
    )
    parser.add_argument("--dry-run", action="store_true", help="report changes without writing")
    args = parser.parse_args(argv)
    changed = asyncio.run(_run(args.dry_run))
    verb = "would set" if args.dry_run else "set"
    print(f"{verb}: {len(changed)} CAPA(s)")
    for capa_id, new_date in changed:
        print(f"  {capa_id}: -> {new_date}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
