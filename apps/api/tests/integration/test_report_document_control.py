"""Task 2 — the Controlled Document Register SERVICE (services/reports/document_control.py):
authz-filtered query + batched enrichment, exercised over a real testcontainer DB (doc 13 §6.1,
doc 15 §8.15). Run-scoped: the shared DB carries other tests' documents, so we assert deltas /
membership for OUR doc, never an absolute row count.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from types import SimpleNamespace

import pytest
from httpx import AsyncClient

from . import s5_helpers as s5
from .test_vault import _auth

pytestmark = pytest.mark.integration


@pytest.fixture
def subj() -> SimpleNamespace:
    salt = uuid.uuid4().hex[:10]
    return SimpleNamespace(a=f"kc-reg-a-{salt}", b=f"kc-reg-b-{salt}")


async def test_register_includes_a_new_effective_document_and_hash_changes(
    app_client: AsyncClient,
    app_under_test: object,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
) -> None:
    """The full register is complete (not paginated): a newly-Effective doc appears, and the
    content hash reacts to the larger set. Run-scoped: we assert OUR doc is present + the hash
    differs before vs after, never an absolute count on the shared DB."""
    from easysynq_api.db.session import get_sessionmaker
    from easysynq_api.services.reports.document_control import (
        compute_document_control_register,
    )

    from .test_vault import _ensure_user

    await s5.grant_lifecycle(subj.a)  # author: full lifecycle perms incl. document.read (SYSTEM)
    await s5.grant_lifecycle(subj.b)  # approver/releaser: same, SoD gates self-approval not read
    org_id = await s5.default_org_id()
    await s5.set_approver_release(org_id, True)  # SoD-2: approver may also release
    h_author = _auth(token_factory, subj.a)
    h_approver = _auth(token_factory, subj.b)
    type_id = await s5.type_id("SOP")

    sm = get_sessionmaker()
    async with sm() as session:
        caller = await _ensure_user(session, subj.a)  # the SYSTEM document.read holder
        before = await compute_document_control_register(
            session, caller, filters=[], source_ip=None
        )

    # Drive a brand-new document to Effective — its atomically-allocated identifier
    # (SOP-PUR-NNN, sequence-unique) is our run-scoped membership marker.
    eff = await s5.drive_to_effective(
        app_client, h_author, h_approver, h_approver, type_id, b"register-content"
    )
    identifier = eff["identifier"]

    async with sm() as session:
        caller = await _ensure_user(session, subj.a)
        after = await compute_document_control_register(session, caller, filters=[], source_ip=None)

    ids = {r["identifier"] for r in after.rows}
    assert identifier in ids
    assert after.row_count == len(after.rows)
    assert after.content_hash != before.content_hash
    row = next(r for r in after.rows if r["identifier"] == identifier)
    assert row["current_state"] == "Effective"
    assert row["effective_revision_label"]  # a released doc has a revision label
    assert isinstance(row["clause_refs"], list)
    assert isinstance(row["process_links"], list)
