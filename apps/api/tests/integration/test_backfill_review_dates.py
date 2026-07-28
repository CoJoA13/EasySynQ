import datetime
import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient

from easysynq_api.cli.backfill_review_dates import backfill
from easysynq_api.db.models.documented_information import DocumentedInformation
from easysynq_api.db.session import get_sessionmaker

from . import s5_helpers as s5
from .test_periodic_review import _release_doc
from .test_vault import _auth

pytestmark = pytest.mark.integration


async def test_backfill_recomputes_changed_only_and_is_idempotent(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    app_under_test: object,
) -> None:
    salt = uuid.uuid4().hex[:10]
    author = f"kc-backfill-author-{salt}"
    approver = f"kc-backfill-approver-{salt}"
    await s5.grant_lifecycle(author)
    await s5.grant_lifecycle(approver)
    await s5.set_approver_release(await s5.default_org_id(), True)

    document_id, _ = await _release_doc(
        app_client,
        _auth(token_factory, author),
        _auth(token_factory, approver),
        await s5.type_id("SOP"),
        f"backfill-review-date-{salt}".encode(),
    )
    doc_id = uuid.UUID(document_id)

    async with get_sessionmaker()() as session:
        doc = await session.get(DocumentedInformation, doc_id)
        assert doc is not None
        assert doc.next_review_due is not None
        canonical_due = doc.next_review_due
        wrong = canonical_due + datetime.timedelta(days=400)
        doc.next_review_due = wrong
        await session.commit()

        changed = await backfill(session, dry_run=False)
        assert (doc_id, wrong, canonical_due) in changed
        await session.refresh(doc)
        assert doc.next_review_due == canonical_due

        # Idempotent: a second run reports this doc unchanged.
        changed2 = await backfill(session, dry_run=False)
        assert all(changed_id != doc_id for changed_id, _old, _new in changed2)
