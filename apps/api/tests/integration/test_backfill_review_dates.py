import datetime

import pytest
from sqlalchemy import select, update

from easysynq_api.cli.backfill_review_dates import backfill
from easysynq_api.db.models.documented_information import DocumentedInformation
from easysynq_api.db.models.organization import Organization
from easysynq_api.db.session import get_sessionmaker

pytestmark = pytest.mark.integration


async def test_backfill_recomputes_changed_only_and_is_idempotent(app_under_test: object) -> None:
    async with get_sessionmaker()() as session:
        org_id = (
            await session.execute(
                select(Organization.id).order_by(Organization.created_at).limit(1)
            )
        ).scalar_one()
        # Pick any Effective doc with a review period + effective version; if none exists in the
        # shared DB, create one via the test harness used by test_periodic_review. For the assertion
        # we only need: a documented_information row with review_period_months set and a non-null
        # next_review_due deliberately stored as a WRONG value, then assert backfill fixes it.
        doc = (
            await session.execute(
                select(DocumentedInformation)
                .where(
                    DocumentedInformation.org_id == org_id,
                    DocumentedInformation.review_period_months.is_not(None),
                    DocumentedInformation.next_review_due.is_not(None),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if doc is None:
            pytest.skip("No periodic-review doc in the shared DB; create via the review harness")
        wrong = doc.next_review_due + datetime.timedelta(days=400)
        await session.execute(
            update(DocumentedInformation)
            .where(DocumentedInformation.id == doc.id)
            .values(next_review_due=wrong)
        )
        await session.commit()

        changed = await backfill(session, dry_run=False)
        assert any(c[0] == doc.id for c in changed)
        refreshed = await session.get(DocumentedInformation, doc.id)
        assert refreshed.next_review_due != wrong  # recomputed to the canonical-tz value

        # Idempotent: a second run reports this doc unchanged.
        changed2 = await backfill(session, dry_run=False)
        assert all(c[0] != doc.id for c in changed2)
