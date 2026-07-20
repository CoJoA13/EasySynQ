"""Task 2 — the Controlled Document Register SERVICE (services/reports/document_control.py):
authz-filtered query + batched enrichment, exercised over a real testcontainer DB (doc 13 §6.1,
doc 15 §8.15). Task 3 adds the HTTP route's two-layer gate (surface report.read at SYSTEM or
PROCESS scope + the per-row document.read filter, incl. its lifecycle_state predicate). Run-scoped:
the shared DB carries other tests' documents / organizations, so we assert deltas / membership for
OUR doc(s), never an absolute row count.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models._clause_enums import PdcaPhase
from easysynq_api.db.models.authz_grant import PermissionOverride
from easysynq_api.db.models.clause import Clause
from easysynq_api.db.models.permission import Permission
from easysynq_api.db.models.process import Process
from easysynq_api.db.models.process_link import ProcessLink
from easysynq_api.db.models.scope import Scope
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.domain.authz.types import Effect, ScopeLevel

from . import s5_helpers as s5
from .test_vault import _auth, _checkin, _create, _ensure_user, _upload

pytestmark = pytest.mark.integration

_ROUTE = "/api/v1/reports/document-control"


@pytest.fixture
def subj() -> SimpleNamespace:
    salt = uuid.uuid4().hex[:10]
    return SimpleNamespace(a=f"kc-reg-a-{salt}", b=f"kc-reg-b-{salt}", c=f"kc-reg-c-{salt}")


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
        user_id=caller.id, org_id=caller.org_id, filters=[], source_ip=None
    )

    # Drive a brand-new document to Effective — its atomically-allocated identifier
    # (SOP-PUR-NNN, sequence-unique) is our run-scoped membership marker.
    eff = await s5.drive_to_effective(
        app_client, h_author, h_approver, h_approver, type_id, b"register-content"
    )
    identifier = eff["identifier"]

    after = await compute_document_control_register(
        user_id=caller.id, org_id=caller.org_id, filters=[], source_ip=None
    )

    ids = {r["identifier"] for r in after.rows}
    assert identifier in ids
    assert after.row_count == len(after.rows)
    assert after.content_hash != before.content_hash
    row = next(r for r in after.rows if r["identifier"] == identifier)
    assert row["current_state"] == "Effective"
    assert row["effective_revision_label"]  # a released doc has a revision label
    assert isinstance(row["clause_refs"], list)
    assert isinstance(row["process_links"], list)


async def test_approved_by_reflects_the_approval_signature_not_the_release(
    app_client: AsyncClient,
    app_under_test: object,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
) -> None:
    """MAJOR regression guard: ``approved_by``/``approved_on`` must reflect the APPROVAL
    signature, never the later RELEASE signature. SoD-2's enforced default (an approver may not
    also release unless the org opts in — see the previous test's ``set_approver_release``) means
    a released document normally has a DISTINCT releaser, and release always postdates approval.
    A query that folds both ``approval`` and ``release`` meanings ordered by ``created_at`` with
    "latest wins" therefore reports the RELEASER as the document's approver on every ordinarily
    released document — wrong per doc 13 §6.1 (the column is the approval record).

    Mutation-distinguishing: with a distinct approver (subj.b) and releaser (subj.c) — the
    default SoD-2 path, no ``set_approver_release`` override — this asserts the APPROVER's
    display name. Against the pre-fix query (meaning IN [approval, release], latest wins) the
    releaser's signature is always later, so this assertion fails; the fix (meaning ==
    approval only) makes it pass."""
    from easysynq_api.services.reports.document_control import (
        compute_document_control_register,
    )

    await s5.grant_lifecycle(subj.a)  # author
    await s5.grant_lifecycle(subj.b)  # approver
    await s5.grant_lifecycle(subj.c)  # releaser — DISTINCT from both author and approver
    h_author = _auth(token_factory, subj.a)
    h_approver = _auth(token_factory, subj.b)
    h_releaser = _auth(token_factory, subj.c)
    type_id = await s5.type_id("SOP")

    sm = get_sessionmaker()
    async with sm() as session:
        approver_user = await _ensure_user(session, subj.b)
        approver_name = approver_user.display_name or approver_user.email or str(approver_user.id)
        releaser_user = await _ensure_user(session, subj.c)
        releaser_name = releaser_user.display_name or releaser_user.email or str(releaser_user.id)
    assert approver_name != releaser_name  # else the assertion below can't distinguish anything

    # Map a ★-mandatory clause (satisfies the S9 submit-review >=1-clause_mapping gate AND
    # exercises the clause_refs ★ enrichment the base test doesn't check).
    async with sm() as session:
        star_id, star_number = (
            await session.execute(
                select(Clause.id, Clause.number).where(Clause.is_mandatory_star.is_(True)).limit(1)
            )
        ).one()

    doc = await _create(app_client, h_author, type_id)
    did = doc["id"]
    cm = await app_client.post(
        f"/api/v1/documents/{did}/clause-mappings",
        headers=h_author,
        json={"clause_id": str(star_id)},
    )
    assert cm.status_code == 201, cm.text
    co = await app_client.post(f"/api/v1/documents/{did}/checkout", headers=h_author)
    assert co.status_code == 200, co.text
    sha = await _upload(app_client, h_author, did, b"approver-vs-releaser-regression")
    ci = await _checkin(
        app_client, h_author, did, sha, change_reason="v1", change_significance="MAJOR"
    )
    assert ci.status_code == 201, ci.text
    sr = await app_client.post(f"/api/v1/documents/{did}/submit-review", headers=h_author)
    assert sr.status_code == 200, sr.text
    task_id = await s5.task_for_doc(did)
    dec = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=h_approver, json={"outcome": "approve"}
    )
    assert dec.status_code == 200, dec.text
    rel = await app_client.post(f"/api/v1/documents/{did}/release", headers=h_releaser, json={})
    assert rel.status_code == 200, rel.text
    identifier = rel.json()["identifier"]

    async with sm() as session:
        caller = await _ensure_user(session, subj.a)
    result = await compute_document_control_register(
        user_id=caller.id, org_id=caller.org_id, filters=[], source_ip=None
    )

    row = next(r for r in result.rows if r["identifier"] == identifier)
    assert row["approved_by"] == approver_name
    assert row["approved_by"] != releaser_name
    assert row["approved_on"] is not None
    assert {"clause": star_number, "starred": True} in row["clause_refs"]


# --- FIX F (P2): an Obsolete document retains its formerly-effective version's evidence -----


async def test_obsolete_document_retains_formerly_effective_version_evidence(
    app_client: AsyncClient,
    app_under_test: object,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
) -> None:
    """Obsoleting a document (T11) clears ``current_effective_version_id`` — but the retired
    version + its approval history still exist (``services/vault/lifecycle.py::obsolete`` only
    flips the version's ``version_state`` to Obsolete; it never touches ``effective_from``/
    ``revision_label``/``source_blob_sha256``, and the approval ``signature_event`` is untouched).
    The register row must still surface that evidence — never null it out just because the doc is
    now Obsolete. Mutation-distinguishing: before the fix, ``eff_ids`` is built from
    ``current_effective_version_id`` only, which is None for an Obsolete doc, so
    ``effective_revision_label``/``blob_sha256``/``approved_by``/``approved_on`` all come back
    None; after the fix they resolve from the formerly-effective (latest Obsolete) version.
    Drives a doc Effective then Obsolete via the existing lifecycle helpers (mirrors
    test_lifecycle.py's ``test_obsolete_clears_effective_pointer_and_signs``)."""
    from easysynq_api.services.reports.document_control import (
        compute_document_control_register,
    )

    await s5.grant_lifecycle(subj.a)  # author + the obsoleter
    await s5.grant_lifecycle(subj.b)  # approver, also the releaser (SoD-2 relaxed below)
    await s5.set_approver_release(await s5.default_org_id(), True)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    type_id = await s5.type_id("SOP")

    sm = get_sessionmaker()
    async with sm() as session:
        approver_user = await _ensure_user(session, subj.b)
        approver_name = approver_user.display_name or approver_user.email or str(approver_user.id)

    eff = await s5.drive_to_effective(app_client, ha, hb, hb, type_id, b"obsolete-evidence")
    did = eff["id"]
    identifier = eff["identifier"]
    pre_revision = eff["current_effective_version_id"]
    assert pre_revision is not None

    ob = await app_client.post(
        f"/api/v1/documents/{did}/obsolete", headers=ha, json={"reason": "withdrawn"}
    )
    assert ob.status_code == 200, ob.text
    assert ob.json()["current_state"] == "Obsolete"
    assert ob.json()["current_effective_version_id"] is None  # T11 clears the pointer

    async with sm() as session:
        caller = await _ensure_user(session, subj.a)
    result = await compute_document_control_register(
        user_id=caller.id, org_id=caller.org_id, filters=[], source_ip=None
    )

    row = next(r for r in result.rows if r["identifier"] == identifier)
    assert row["current_state"] == "Obsolete"
    assert row["effective_revision_label"]  # NOT null despite the cleared pointer
    assert row["blob_sha256"]
    assert row["effective_from"] is not None
    assert row["approved_by"] == approver_name
    assert row["approved_on"] is not None


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


async def _grant_process(subject: str, key: str, process_id: str) -> None:
    """Grant ``key`` at PROCESS scope over a single process id (test_records_process_scope.py's
    ``_grant_process`` precedent) — the built-in Process Owner's actual grant shape
    (migrations/versions/0004_seed_authz.py ``_PROCESS_SCOPE for k in _PROCESS_OWNER_KEYS``)."""
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        perm = (await s.execute(select(Permission).where(Permission.key == key))).scalar_one()
        scope = Scope(
            org_id=user.org_id, level=ScopeLevel.PROCESS, selector={"process_ids": [process_id]}
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


async def _add_override(
    subject: str,
    permission_key: str,
    effect: Effect,
    level: ScopeLevel,
    *,
    selector: dict[str, object] | None = None,
    predicates: dict[str, object] | None = None,
) -> None:
    """A generic override-builder (test_authz.py's ``_add_override`` precedent) — used here to seed
    a DENY override carrying a ``lifecycle_state`` predicate (FIX 7), which none of this file's
    other helpers support."""
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        perm = (
            await s.execute(select(Permission).where(Permission.key == permission_key))
        ).scalar_one()
        scope = Scope(org_id=user.org_id, level=level, selector=selector, predicates=predicates)
        s.add(scope)
        await s.flush()
        s.add(
            PermissionOverride(
                org_id=user.org_id,
                user_id=user.id,
                permission_id=perm.id,
                effect=effect,
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


async def _create_process_linked_to(subject: str, org_id: uuid.UUID, doc_id: str) -> str:
    """Direct-ORM Process + ProcessLink seed (test_acknowledgements.py's precedent) — avoids
    needing extra process.create/document.manage_metadata grants just to set up a fixture. Returns
    the new process id (str)."""
    async with get_sessionmaker()() as s:
        creator = await _ensure_user(s, subject)
        proc = Process(
            org_id=org_id,
            name=f"RegTest-Proc-{uuid.uuid4().hex[:10]}",
            pdca_phase=PdcaPhase.DO,
            created_by=creator.id,
        )
        s.add(proc)
        await s.flush()
        s.add(
            ProcessLink(
                org_id=org_id,
                process_id=proc.id,
                documented_information_id=uuid.UUID(doc_id),
                created_by=creator.id,
            )
        )
        await s.commit()
        return str(proc.id)


async def test_endpoint_403s_without_report_read(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """The surface gate: a caller holding document.read but NO report.read grant at all (neither
    SYSTEM nor PROCESS) is refused before any query."""
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


# --- FIX 1 (P1): admit PROCESS-scoped report.read, not just SYSTEM -------------------------


async def test_process_scoped_report_read_admitted_at_surface_gate(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """The built-in Process Owner holds ``report.read`` at PROCESS scope, not SYSTEM
    (migrations/versions/0004_seed_authz.py ``_PROCESS_SCOPE for k in _PROCESS_OWNER_KEYS``). The
    surface gate must admit a PROCESS-scoped report.read ALLOW (not just SYSTEM) — while the
    per-row document.read filter still confines them to their linked-process document(s). Before
    the fix this 403s (the old gate required SYSTEM); after the fix it's 200 with the in-scope doc
    present and the out-of-scope doc absent — a mutation-distinguishing membership assertion, not
    just a status-code check."""
    await s5.grant_lifecycle(subj.a)  # creator: SYSTEM document.read + create/checkin etc.
    ha = _auth(token_factory, subj.a)
    type_id = await s5.type_id("SOP")

    doc_in = await _create(app_client, ha, type_id)
    doc_out = await _create(app_client, ha, type_id)

    org_id = await s5.default_org_id()
    process_id = await _create_process_linked_to(subj.a, org_id, doc_in["id"])

    await _grant_process(subj.b, "report.read", process_id)  # the PROCESS-scoped surface grant
    await _grant_process(subj.b, "document.read", process_id)  # the per-row filter, same scope
    hb = _auth(token_factory, subj.b)

    resp = await app_client.get(_ROUTE, headers=hb)
    assert resp.status_code == 200, resp.text
    ids = {r["identifier"] for r in resp.json()["rows"]}
    assert doc_in["identifier"] in ids
    assert doc_out["identifier"] not in ids


# --- Deny-always-wins at the SURFACE gate (not just the per-row filter) --------------------


async def test_surface_gate_honors_system_report_read_deny(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """Deny-always-wins (R3 / AZ-INV-2) must hold at the SURFACE gate too, not just the per-row
    document.read filter (see ``test_lifecycle_predicated_deny_wins_over_broad_system_allow``
    above). A caller with a SYSTEM report.read ALLOW *and* a SYSTEM report.read DENY override
    (the canonical admin revocation shape) must be refused — before the fix, the gate only checked
    ``any(ALLOW)`` and never inspected DENY, so the override was silently ignored and the caller
    was still admitted."""
    await _grant(subj.a, ("report.read",))  # the SYSTEM ALLOW
    await _add_override(subj.a, "report.read", Effect.DENY, ScopeLevel.SYSTEM)  # the revocation

    resp = await app_client.get(_ROUTE, headers=_auth(token_factory, subj.a))
    assert resp.status_code == 403, resp.text


# --- FIX A (P1, Codex round 2): the surface gate must evaluate grant PREDICATES ------------


async def test_surface_gate_denies_an_expired_report_read_grant(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """The surface gate is a raw effect+level check that never evaluated grant PREDICATES — an
    expired (``valid_until`` in the past) SYSTEM report.read ALLOW still admitted. Reusing the PDP's
    own ``_predicates_pass`` at the gate closes this: a time-boxed grant past its ``valid_until``
    must be refused, same as if it had no report.read grant at all. Mutation-distinguishing: RED
    against the pre-fix gate (a bare ``any(effect==ALLOW)``/``any(effect==DENY)`` over level-matched
    grants, no predicate evaluation whatsoever) — that gate admits (200) any ALLOW regardless of
    time-box; the fix refuses (403)."""
    await _add_override(
        subj.a,
        "report.read",
        Effect.ALLOW,
        ScopeLevel.SYSTEM,
        predicates={"valid_until": "2020-01-01T00:00:00+00:00"},  # long expired
    )

    resp = await app_client.get(_ROUTE, headers=_auth(token_factory, subj.a))
    assert resp.status_code == 403, resp.text


async def test_surface_gate_admits_despite_an_expired_report_read_deny(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """The flip side of ``test_surface_gate_denies_an_expired_report_read_grant``: predicate
    evaluation applies to DENY overrides too, not just ALLOW. A caller with a valid SYSTEM
    report.read ALLOW *and* a SYSTEM report.read DENY whose ``valid_until`` is long past must be
    ADMITTED (200) — the expired DENY is filtered out of the ``active`` set by
    ``_predicates_pass`` before the deny-wins check runs, so it can no longer block. Contrast with
    ``test_surface_gate_honors_system_report_read_deny`` (a non-expired DENY still 403s).
    Mutation-distinguishing: a gate that (re-)admits every DENY regardless of its predicates —
    i.e. drops the ``_predicates_pass`` filter on the DENY side, or never evaluates predicates at
    all — would see this expired-but-present DENY, treat it as still-active, and refuse (403)
    instead of 200."""
    await _grant(subj.a, ("report.read",))  # the SYSTEM ALLOW, no predicates — always active
    await _add_override(
        subj.a,
        "report.read",
        Effect.DENY,
        ScopeLevel.SYSTEM,
        predicates={"valid_until": "2020-01-01T00:00:00+00:00"},  # long expired
    )

    resp = await app_client.get(_ROUTE, headers=_auth(token_factory, subj.a))
    assert resp.status_code == 200, resp.text


async def test_surface_gate_denies_a_report_read_allow_outside_its_ip_allow(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """An ``ip_allow`` predicate is honored at the surface gate: a caller whose ONLY SYSTEM
    report.read ALLOW carries an ``ip_allow`` list that does not include the request's source IP
    must be refused (403) — the ALLOW is dropped by ``_predicates_pass`` before the effect+level
    check, leaving no admitting grant (deny-by-default). The value ``10.99.99.99`` is chosen so
    the predicate fails regardless of what the test client's actual source IP resolves to (per
    ``pdp._predicates_pass``, ``context.source_ip is None`` also fails the predicate — an
    ASGI test transport with no ``request.client`` fails exactly the same way as a mismatched IP).
    Mutation-distinguishing: a gate that never evaluates predicates on the ALLOW side (the
    pre-fix ``any(effect==ALLOW)`` over level-matched grants only) would admit this grant and
    return 200 instead of 403."""
    await _add_override(
        subj.a,
        "report.read",
        Effect.ALLOW,
        ScopeLevel.SYSTEM,
        predicates={"ip_allow": ["10.99.99.99"]},  # never matches the real test client source IP
    )

    resp = await app_client.get(_ROUTE, headers=_auth(token_factory, subj.a))
    assert resp.status_code == 403, resp.text


# --- FIX 7 (P1): lifecycle_state populated in the per-row ResourceContext ------------------


async def test_lifecycle_predicated_deny_wins_over_broad_system_allow(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """Deny-always-wins (R3): a caller with a broad SYSTEM document.read ALLOW but an explicit
    DENY predicated on the document's OWN lifecycle_state must NOT see that document. A
    freshly-created document is ``Draft``. Before the fix, the register's per-row ResourceContext
    omitted ``lifecycle_state`` (always None) — the predicate ``resource.lifecycle_state not in
    ["Draft"]`` compared None (never in the allow-list) and always evaluated True, so the DENY's
    predicate silently failed to match and the broader ALLOW won, leaving the document visible.
    After the fix, lifecycle_state="Draft" matches the predicate and the DENY wins."""
    await s5.grant_lifecycle(subj.a)  # creator
    ha = _auth(token_factory, subj.a)
    type_id = await s5.type_id("SOP")
    doc = await _create(app_client, ha, type_id)  # current_state == Draft

    await _grant(subj.b, ("report.read", "document.read"))  # broad SYSTEM ALLOW
    await _add_override(
        subj.b,
        "document.read",
        Effect.DENY,
        ScopeLevel.SYSTEM,
        predicates={"lifecycle_state": ["Draft"]},
    )
    hb = _auth(token_factory, subj.b)

    resp = await app_client.get(_ROUTE, headers=hb)
    assert resp.status_code == 200, resp.text
    ids = {r["identifier"] for r in resp.json()["rows"]}
    assert doc["identifier"] not in ids


# --- FIX 4-backend (P2): a process_id filter key on the shared allow-list -------------------


async def test_process_id_filter_narrows_register_to_linked_documents(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """``filter[process_id][eq]=<pid>`` narrows the register to documents linked to that
    process — the register's promised "process" facet."""
    await s5.grant_lifecycle(subj.a)
    ha = _auth(token_factory, subj.a)
    type_id = await s5.type_id("SOP")

    doc_in = await _create(app_client, ha, type_id)
    doc_out = await _create(app_client, ha, type_id)

    org_id = await s5.default_org_id()
    process_id = await _create_process_linked_to(subj.a, org_id, doc_in["id"])

    await _grant(subj.a, ("report.read",))
    resp = await app_client.get(f"{_ROUTE}?filter[process_id][eq]={process_id}", headers=ha)
    assert resp.status_code == 200, resp.text
    ids = {r["identifier"] for r in resp.json()["rows"]}
    assert doc_in["identifier"] in ids
    assert doc_out["identifier"] not in ids


async def test_process_id_filter_rejects_a_non_uuid_value() -> None:
    """A malformed ``filter[process_id][eq]`` value is a 422, mirroring the other UUID-valued
    filters (document_type/owner_user_id) — exercised directly on the pure builder, no HTTP
    round-trip needed."""
    from easysynq_api.api.documents import _filter_condition
    from easysynq_api.problems import ProblemException

    with pytest.raises(ProblemException) as exc_info:
        _filter_condition("process_id", "eq", "not-a-uuid")
    assert exc_info.value.status == 422
