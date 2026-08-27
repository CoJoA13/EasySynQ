"""Audit C9 — the paged files listing folds ONLY the page's own rows.

``list_files_review`` pages the file rows in SQL but previously materialized the ENTIRE run's
proposal-node set and append-only decision log per request — O(run) server work for a ≤200-row
page, degrading monotonically as the log grows. The fold is strictly per-file, so the loads are
now pushed down to the page's file ids. These proofs fabricate a minimal run directly (the full
pipeline is exercised elsewhere) and pin PAGING PARITY: each one-row page's folded state equals
the corresponding row of the unpaged listing, including a decided file — a wrong pushdown (the
fold no longer seeing that file's decisions) collapses its disposition back to undecided and
fails the equality. Assertions are scoped to this run's own rows.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from easysynq_api.db.models._ingestion_enums import ImportDecisionAction, ImportRunStatus
from easysynq_api.db.models.app_user import AppUser
from easysynq_api.db.models.import_decision import ImportDecision
from easysynq_api.db.models.import_file import ImportFile
from easysynq_api.db.models.import_proposal_node import ImportProposalNode
from easysynq_api.db.models.import_run import ImportRun
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.ingestion import repository as repo
from easysynq_api.services.ingestion.review import list_files_review

from .test_capa import _grant, _subject

pytestmark = pytest.mark.integration


async def _fabricate_run() -> tuple[AppUser, uuid.UUID, uuid.UUID, uuid.UUID]:
    """(caller, run_id, file1_id, file2_id) — two included files, nodes for both, and an EXCLUDE
    decision on file1 (rel_path order puts file1 on page 0, file2 on page 1)."""
    user_id = await _grant(_subject("rev-page"), ("import.review",))
    async with get_sessionmaker()() as s:
        caller = await s.get(AppUser, user_id)
        assert caller is not None
        run = ImportRun(
            org_id=caller.org_id,
            source_root=f"review-paging-{uuid.uuid4().hex}",
            source_root_hash=uuid.uuid4().hex,
            status=ImportRunStatus.PROPOSED,
            created_by=caller.id,
        )
        s.add(run)
        await s.flush()
        files = []
        for rel in ("a-first.docx", "b-second.docx"):
            f = ImportFile(
                org_id=caller.org_id,
                run_id=run.id,
                rel_path=rel,
                filename=rel,
                size_bytes=10,
                scan_flags={"disposition": "included"},
                included_candidate=True,
            )
            s.add(f)
            files.append(f)
        await s.flush()
        for f in files:
            s.add(
                ImportProposalNode(
                    org_id=caller.org_id,
                    run_id=run.id,
                    file_id=f.id,
                    conflict_flags={},
                )
            )
        s.add(
            ImportDecision(
                org_id=caller.org_id,
                run_id=run.id,
                file_id=files[0].id,
                action=ImportDecisionAction.EXCLUDE,
                decided_by=caller.id,
            )
        )
        await s.commit()
        return caller, run.id, files[0].id, files[1].id


async def test_one_row_pages_fold_identically_to_the_unpaged_listing(
    app_under_test: object,
) -> None:
    caller, run_id, file1, file2 = await _fabricate_run()
    async with get_sessionmaker()() as s:
        _run, unpaged = await list_files_review(
            s,
            caller,
            run_id,
            disposition=None,
            kind=None,
            band=None,
            review_status=None,
            limit=200,
            offset=0,
        )
        by_id = {f.id: state for f, _c, state in unpaged}
        assert set(by_id) == {file1, file2}
        # The decided file's fold reflects its decision — the discriminating half: a pushdown
        # that hides this file's decisions from the fold collapses it back to undecided.
        assert by_id[file1]["disposition"] == "excluded"
        assert by_id[file2]["disposition"] != "excluded"

        for offset, expected_file in ((0, file1), (1, file2)):
            _run, page = await list_files_review(
                s,
                caller,
                run_id,
                disposition=None,
                kind=None,
                band=None,
                review_status=None,
                limit=1,
                offset=offset,
            )
            assert [f.id for f, _c, _state in page] == [expected_file]
            assert page[0][2] == by_id[expected_file], (
                "a one-row page's folded state diverged from the unpaged listing"
            )


async def test_loaders_push_file_ids_down(app_under_test: object) -> None:
    """The repository loaders restrict to the requested file ids (and keep the unrestricted
    whole-run shape for the checklist path)."""
    _caller, run_id, file1, file2 = await _fabricate_run()
    async with get_sessionmaker()() as s:
        all_nodes = await repo.list_proposal_nodes(s, run_id)
        assert {n.file_id for n in all_nodes} == {file1, file2}
        only1 = await repo.list_proposal_nodes(s, run_id, file_ids=[file1])
        assert {n.file_id for n in only1} == {file1}

        all_decs = await repo.list_decisions(s, run_id)
        assert {d.file_id for d in all_decs} == {file1}
        assert await repo.list_decisions(s, run_id, file_ids=[file2]) == []
        # sanity: the fabricated rows are really this run's (shared-DB hygiene)
        assert (
            await s.execute(select(ImportDecision).where(ImportDecision.run_id == run_id))
        ).scalars().first() is not None
