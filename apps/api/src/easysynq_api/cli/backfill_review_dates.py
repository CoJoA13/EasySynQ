"""Backfill stored ``documented_information.next_review_due`` into the canonical org tz
(S-orgtz-unify, R56). One-time + idempotent; recompute needs Python (add_months + per-org tz), so a
CLI (not raw SQL in a migration). Runs inside the api image (DB reachable):

    python -m easysynq_api.cli.backfill_review_dates --dry-run
    python -m easysynq_api.cli.backfill_review_dates

Only ``next_review_due`` is stored; the MR cadence next-due is derived (no backfill). MGMT-review
documents have no review_period_months, so they are naturally skipped.
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
from ..db.models.document_version import DocumentVersion
from ..db.models.documented_information import DocumentedInformation
from ..db.models.organization import Organization
from ..services.common.org_clock import resolve_org_tz
from ..services.vault.review import compute_next_review_due


async def backfill(
    session: AsyncSession, *, dry_run: bool
) -> list[tuple[uuid.UUID, datetime.date | None, datetime.date | None]]:
    """Recompute next_review_due per doc in its org's canonical tz; return the rows that change.
    Commits when not dry_run."""
    changed: list[tuple[uuid.UUID, datetime.date | None, datetime.date | None]] = []
    org_ids = (await session.execute(select(Organization.id))).scalars().all()
    for org_id in org_ids:
        org_tz = await resolve_org_tz(session, org_id)
        docs = (
            (
                await session.execute(
                    select(DocumentedInformation).where(
                        DocumentedInformation.org_id == org_id,
                        DocumentedInformation.review_period_months.is_not(None),
                        DocumentedInformation.next_review_due.is_not(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        for doc in docs:
            # Anchor identically to compute_next_review_due's release/confirm rule: the LATER of
            # last_reviewed_at / the governing effective version's effective_from.
            # effective_from is always non-None after a release commit (lifecycle._cutover sets
            # version.effective_from = eff_from before cutover).
            effective_from: datetime.datetime | None = None
            if doc.current_effective_version_id is not None:
                effective_from = (
                    await session.execute(
                        select(DocumentVersion.effective_from).where(
                            DocumentVersion.id == doc.current_effective_version_id
                        )
                    )
                ).scalar_one_or_none()
            new = compute_next_review_due(
                doc.review_period_months, doc.last_reviewed_at, effective_from, org_tz
            )
            if new != doc.next_review_due:
                changed.append((doc.id, doc.next_review_due, new))
                if not dry_run:
                    doc.next_review_due = new
    if not dry_run:
        await session.commit()
    return changed


async def _run(dry_run: bool) -> list[tuple[uuid.UUID, datetime.date | None, datetime.date | None]]:
    engine = create_async_engine(get_settings().database_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessionmaker() as session:
            return await backfill(session, dry_run=dry_run)
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="easysynq-backfill-review-dates",
        description="Recompute stored next_review_due into the canonical org tz (S-orgtz-unify).",
    )
    parser.add_argument("--dry-run", action="store_true", help="report changes without writing")
    args = parser.parse_args(argv)
    changed = asyncio.run(_run(args.dry_run))
    verb = "would change" if args.dry_run else "changed"
    print(f"{verb}: {len(changed)} document(s)")
    for doc_id, old, new in changed:
        print(f"  {doc_id}: {old} -> {new}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
