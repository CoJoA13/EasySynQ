"""Task 2 — the Controlled Document Register SERVICE (services/reports/document_control.py):
authz-filtered query + batched enrichment, exercised over a real testcontainer DB (doc 13 §6.1,
doc 15 §8.15). Task 3 adds the HTTP route's two-layer gate (surface require(report.read) SYSTEM +
the per-row document.read filter). Run-scoped: the shared DB carries other tests' documents /
organizations, so we assert deltas / membership for OUR doc(s), never an absolute row count.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models.authz_grant import PermissionOverride
from easysynq_api.db.models.permission import Permission
from easysynq_api.db.models.scope import Scope
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.domain.authz.types import Effect, ScopeLevel

from . import s5_helpers as s5
from .test_vault import _auth, _create, _ensure_user

pytestmark = pytest.mark.integration

_ROUTE = "/api/v1/reports/document-control"


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
    from easysynq_api.services.reports.document_control import (
        compute_document_control_register,
    )

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


# --- Task 3: the HTTP route's two-layer gate ------------------------------------------------


async def _grant(subject: str, keys: tuple[str, ...]) -> None:
    """Grant SYSTEM-scope permission overrides (the test_reports.py checklist precedent)."""
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        for key in keys:
            perm = (await s.execute(select(Permission).where(Permission.key == key))).scalar_one()
            scope = Scope(org_id=user.org_id, level=ScopeLevel.SYSTEM)
            s.add(scope)
            await s.flush()
            s.add(
                PermissionOverride(
                    org_id=user.org_id,
                    user_id=user.id,
                    permission_id=perm.id,
                    effect=Effect.ALLOW,
                    scope_id=scope.id,
                )
            )
        await s.commit()


async def _grant_read_folder(subject: str, folder_path: str) -> None:
    """Grant document.read at FOLDER scope only (test_documents_list.py's ``_grant_read_folder``
    precedent) — a SUBSET grant, so the register's per-row filter drops an out-of-folder doc."""
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        perm = (
            await s.execute(select(Permission).where(Permission.key == "document.read"))
        ).scalar_one()
        scope = Scope(
            org_id=user.org_id, level=ScopeLevel.FOLDER, selector={"folder_path": folder_path}
        )
        s.add(scope)
        await s.flush()
        s.add(
            PermissionOverride(
                org_id=user.org_id,
                user_id=user.id,
                permission_id=perm.id,
                effect=Effect.ALLOW,
                scope_id=scope.id,
            )
        )
        await s.commit()


async def _create_in_folder(
    client: AsyncClient, h: dict[str, str], type_id: str, folder_path: str
) -> dict:
    r = await client.post(
        "/api/v1/documents",
        headers=h,
        json={
            "title": "T",
            "document_type_id": type_id,
            "area_code": "PUR",
            "folder_path": folder_path,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_endpoint_403s_without_report_read(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """The surface gate: a caller holding document.read but NOT SYSTEM report.read is refused
    before any query."""
    await _grant(subj.a, ("document.read",))
    resp = await app_client.get(_ROUTE, headers=_auth(token_factory, subj.a))
    assert resp.status_code == 403, resp.text


async def test_endpoint_returns_register_with_provenance(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """A SYSTEM report.read holder gets the register + a provenance block whose content_hash
    matches a recompute over the returned rows."""
    from easysynq_api.services.reports.document_control import register_content_hash

    await _grant(subj.a, ("report.read",))
    resp = await app_client.get(_ROUTE, headers=_auth(token_factory, subj.a))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"provenance", "rows"}
    prov = body["provenance"]
    assert prov["report_name"] == "Controlled Document Register"
    assert prov["row_count"] == len(body["rows"])
    assert prov["content_hash"] == register_content_hash(body["rows"])
    assert prov["scope"].startswith("org:")
    assert prov["app_version"]


async def test_row_filter_excludes_out_of_scope_document(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """The per-row document.read filter is a REAL security boundary, not just documentation: a
    caller who holds SYSTEM report.read (passes the surface gate) but only a FOLDER-scoped
    document.read grant gets a register that OMITS a document outside that folder while INCLUDING
    one inside it — a mutation-distinguishing exclusion (a SYSTEM document.read holder would see
    strictly more). Run-scoped: asserts membership for OUR two docs, never an absolute count."""
    await s5.grant_lifecycle(subj.a)  # creator: SYSTEM document.read + create/checkin etc.
    ha = _auth(token_factory, subj.a)
    type_id = await s5.type_id("SOP")
    folder = f"RegTest.{uuid.uuid4().hex[:10]}"

    doc_in = await _create_in_folder(app_client, ha, type_id, folder)
    doc_out = await _create(app_client, ha, type_id)  # default folder_path=None → excluded

    await _grant(subj.b, ("report.read",))  # the SYSTEM surface gate...
    await _grant_read_folder(subj.b, folder)  # ...but only a FOLDER-scoped document.read
    hb = _auth(token_factory, subj.b)

    resp = await app_client.get(_ROUTE, headers=hb)
    assert resp.status_code == 200, resp.text
    ids = {r["identifier"] for r in resp.json()["rows"]}
    assert doc_in["identifier"] in ids
    assert doc_out["identifier"] not in ids
