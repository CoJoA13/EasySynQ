"""S-leadership-1 integration proofs — the signed, engine-routed Top-Management *release
authorization* for leadership artifacts (Quality Policy POL §5.2, Quality Objectives OBJ §6.2,
Management Review MR §9.3) over HTTP against testcontainer Postgres.

A leadership artifact is approved as today; when the org flag
``leadership_release_requires_top_management_authorization`` is on, the Approved version may not be
RELEASED until a "Top Management" member signs ``meaning=verify`` on the ``document_version`` (mig
0054). Authority is the role-resolved candidate pool (no permission key gates the SIGN); the REQUEST
reuses ``document.approve``.

The full request→verify→release-to-Effective mechanism is proven on **OBJ** (a non-singleton
leadership type — POL is ``is_singleton=True`` (R25), so the shared test org tolerates at most ONE
Effective POL and a test must NOT push a second one). POL + MR prove their release endpoints hit the
same shared ``_cutover`` gate (blocked → 409). The welded approve/release path is untouched.

The integration suite shares one session DB across files (sequential within a pytest-split
shard), so: (1) assertions are scoped to **this run's own** ids, never absolute counts; (2) the
org-level flag is flipped ON then reset OFF in a ``finally``; (3) the shared org may hold ≥2 "Top
Management" members, so an ANY-quorum decline must be decisive and ``_my_pending_task`` is
self-scoped.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from easysynq_api.db.models._audit_enums import AuditObjectType, EventType
from easysynq_api.db.models._signature_enums import SignatureMeaning, SignedObjectType
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.role import Role, RoleAssignment
from easysynq_api.db.models.signature_event import SignatureEvent
from easysynq_api.db.models.system_config import SystemConfig
from easysynq_api.db.session import get_sessionmaker

from . import s5_helpers as s5
from .test_quality_objectives import _grant
from .test_vault import _auth, _create, _ensure_user

pytestmark = pytest.mark.integration

_OBJ_KEYS = ("objective.read", "objective.manage", "kpi.read", "kpi.record")
_MR_KEYS = ("mgmtReview.create", "mgmtReview.read", "mgmtReview.record_outputs")
_RELEASE_KEYS = ("document.release", "document.read", "document.read_draft")
_REQUEST_KEYS = ("document.approve", "document.read")


# --- helpers ----------------------------------------------------------------------------------


async def _set_leadership_flag(org_id: uuid.UUID, value: bool) -> None:
    """Upsert the org's S-leadership-1 release-gate flag (a system_config row exists once
    OPERATIONAL; the ``s5.set_approver_release`` shape)."""
    async with get_sessionmaker()() as s:
        cfg = await s.get(SystemConfig, org_id)
        if cfg is None:
            s.add(
                SystemConfig(
                    org_id=org_id,
                    leadership_release_requires_top_management_authorization=value,
                )
            )
        else:
            cfg.leadership_release_requires_top_management_authorization = value
        await s.commit()


async def _assign_top_mgmt(subject: str) -> uuid.UUID:
    """Assign the seeded "Top Management" role to a user (candidate-pool authority, not a permission
    override; the ``test_improvement_authorization._assign_top_mgmt`` pattern)."""
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        role = (await s.execute(select(Role).where(Role.name == "Top Management"))).scalar_one()
        s.add(
            RoleAssignment(
                org_id=user.org_id,
                user_id=user.id,
                role_id=role.id,
                bound_scope={"level": "SYSTEM"},
            )
        )
        await s.commit()
        return user.id


async def _create_objective(client: AsyncClient, h: dict[str, str], title: str) -> str:
    r = await client.post(
        "/api/v1/objectives",
        headers=h,
        json={
            "title": title,
            "target_value": "98",
            "unit": "%",
            "direction": "HIGHER_IS_BETTER",
            "due_date": "2026-12-31",
            "at_risk_threshold": "95",
            "baseline_value": "90",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _create_review(client: AsyncClient, h: dict[str, str], title: str) -> str:
    r = await client.post(
        "/api/v1/management-reviews",
        headers=h,
        json={"title": title, "period_label": "2026 Annual"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _approved_pol(
    client: AsyncClient, token_factory: Callable[..., str], salt: str
) -> tuple[str, dict[str, str], dict[str, str]]:
    """Drive a POL (Quality Policy — a generic kind=DOCUMENT) to Approved. Returns (doc_id, author
    headers [the requester — holds document.approve via the lifecycle override], releaser headers [a
    THIRD party with document.release]). POL is SINGLETON, so callers here never RELEASE it (only
    assert the gate blocks) — the shared org is not polluted with a second Effective POL."""
    author, approver, releaser = f"ld-au-{salt}", f"ld-ap-{salt}", f"ld-rl-{salt}"
    ha, hap, hrl = (
        _auth(token_factory, author),
        _auth(token_factory, approver),
        _auth(token_factory, releaser),
    )
    await s5.grant_lifecycle(author)
    await s5.grant_role(approver, "Approver")
    await _grant(releaser, _RELEASE_KEYS)
    did = await s5.drive_to_approved(client, ha, hap, await s5.type_id("POL"), b"Quality policy v1")
    return did, ha, hrl


async def _approved_obj(
    client: AsyncClient, token_factory: Callable[..., str], salt: str
) -> tuple[str, dict[str, str], dict[str, str]]:
    """Drive a Quality Objective (OBJ — a NON-singleton leadership type) to Approved via the
    objectives flow. Returns (obj_id, requester headers [document.approve override], releaser
    headers [document.release; SoD-2: ≠ author/approver]). OBJ may be released to Effective
    repeatedly, so it carries the full release-to-Effective happy path."""
    submitter, approver = f"ld-objsm-{salt}", f"ld-objap-{salt}"
    requester, releaser = f"ld-objrq-{salt}", f"ld-objrl-{salt}"
    hs, hap = _auth(token_factory, submitter), _auth(token_factory, approver)
    hrq, hrl = _auth(token_factory, requester), _auth(token_factory, releaser)
    await _grant(submitter, _OBJ_KEYS)
    await s5.grant_role(approver, "Approver")
    await _grant(requester, _REQUEST_KEYS)
    await _grant(releaser, _RELEASE_KEYS)
    oid = await _create_objective(client, hs, f"Objective {salt}")
    sr = await client.post(f"/api/v1/objectives/{oid}/submit-review", headers=hs)
    assert sr.status_code == 200, sr.text
    task_id = await s5.task_for_doc(oid)
    dec = await client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hap, json={"outcome": "approve"}
    )
    assert dec.status_code == 200, dec.text
    return oid, hrq, hrl


async def _request(
    client: AsyncClient, headers: dict[str, str], doc_id: str, **body: object
) -> dict:
    r = await client.post(
        f"/api/v1/documents/{doc_id}/request-leadership-authorization",
        headers=headers,
        json=body or {},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _my_pending_task(client: AsyncClient, headers: dict[str, str], instance_id: str) -> str:
    r = await client.get(f"/api/v1/tasks?instance_id={instance_id}&state=PENDING", headers=headers)
    assert r.status_code == 200, r.text
    tasks = r.json()
    assert len(tasks) == 1, tasks
    return str(tasks[0]["id"])


async def _status(client: AsyncClient, headers: dict[str, str], doc_id: str) -> dict:
    r = await client.get(f"/api/v1/documents/{doc_id}/leadership-authorization", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


# --- 1. OBJ (non-singleton): the full mechanism (request → verify → release to Effective) ------


async def test_obj_full_mechanism_request_verify_release(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The happy path on a non-singleton leadership artifact: with the flag on, an Approved Quality
    Objective is BLOCKED from release until a Top-Management member signs ``verify`` on its version;
    that mints a single ``signature_event(meaning=verify, signed_object_type=document_version)`` + a
    LEADERSHIP_AUTHORIZED audit, after which ``/objectives/{id}/release`` succeeds (→ Effective)."""
    org_id = await s5.default_org_id()
    salt = uuid.uuid4().hex[:8]
    tm_subj = f"ld-objtm-{salt}"
    tm_id = await _assign_top_mgmt(tm_subj)
    htm = _auth(token_factory, tm_subj)
    oid, hrq, hrl = await _approved_obj(app_client, token_factory, salt)

    await _set_leadership_flag(org_id, True)
    try:
        pre = await _status(app_client, hrl, oid)
        assert pre["is_leadership_artifact"] is True
        assert pre["required"] is True
        assert pre["authorized"] is False
        # CX-1: the releaser can READ the status (document.read) but does NOT hold
        # document.approve → can_request is False (the per-caller, scope-aware request gate).
        assert pre["can_request"] is False
        version_id = pre["version_id"]
        assert version_id is not None

        # Release is BLOCKED before authorization.
        blocked = await app_client.post(f"/api/v1/objectives/{oid}/release", headers=hrl)
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["code"] == "leadership_authorization_required"

        # Request → a Top-Management task materializes; the objective stays Approved.
        req = await _request(app_client, hrq, oid, comment="Please authorize release")
        assert req["current_state"] == "leadership_authorization"
        instance_id = str(req["instance_id"])

        # The Top-Management member finds + signs their own task (verify).
        task_id = await _my_pending_task(app_client, htm, instance_id)
        decision = (
            await app_client.post(
                f"/api/v1/tasks/{task_id}/decision",
                headers=htm,
                json={"outcome": "verify", "comment": "Endorsed by leadership"},
            )
        ).json()
        assert decision["current_state"] == "COMPLETED", decision
        sig_id = decision["signature_event_id"]
        assert sig_id is not None
        assert decision["document_version_id"] == version_id

        # The verify signature binds to the document_version; the leadership act is first-class.
        async with get_sessionmaker()() as s:
            sig = (
                await s.execute(
                    select(SignatureEvent).where(SignatureEvent.id == uuid.UUID(sig_id))
                )
            ).scalar_one()
            assert sig.meaning is SignatureMeaning.verify
            assert sig.signed_object_type is SignedObjectType.document_version
            assert str(sig.signed_object_id) == version_id
            assert str(sig.signer_user_id) == str(tm_id)
            authorized = (
                await s.execute(
                    select(AuditEvent).where(
                        AuditEvent.object_id == uuid.UUID(version_id),
                        AuditEvent.event_type == EventType.LEADERSHIP_AUTHORIZED,
                    )
                )
            ).scalar_one()
            assert authorized.object_type == AuditObjectType.version
            assert authorized.scope_ref is not None

        # GET status now reflects authorized; release then succeeds → Effective.
        after = await _status(app_client, hrl, oid)
        assert after["authorized"] is True
        assert after["instance"]["current_state"] == "COMPLETED"
        rel = await app_client.post(f"/api/v1/objectives/{oid}/release", headers=hrl)
        assert rel.status_code == 200, rel.text
        assert rel.json()["current_state"] == "Effective"
    finally:
        await _set_leadership_flag(org_id, False)


# --- 2. Default (flag off) is unchanged behaviour (OBJ, non-singleton) -------------------------


async def test_release_not_gated_when_flag_off(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """With the flag OFF (the default), a leadership artifact releases without any Top-Management
    authorization — the additive feature is dormant and the welded path is unchanged."""
    org_id = await s5.default_org_id()
    salt = uuid.uuid4().hex[:8]
    await _set_leadership_flag(org_id, False)
    oid, _hrq, hrl = await _approved_obj(app_client, token_factory, salt)

    st = await _status(app_client, hrl, oid)
    assert st["is_leadership_artifact"] is True
    assert st["required"] is False  # leadership type, but the flag is off → not gated

    rel = await app_client.post(f"/api/v1/objectives/{oid}/release", headers=hrl)
    assert rel.status_code == 200, rel.text
    assert rel.json()["current_state"] == "Effective"


# --- 3. POL (singleton): recognized + release blocked (never pushed to Effective) --------------


async def test_pol_release_blocked_and_recognized(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A Quality Policy (POL §5.2 — the doc-10 §2.5 canonical example) is recognized as a leadership
    artifact, and its generic ``/documents/{id}/release`` path hits the cutover gate → 409 with the
    flag on. POL is ``is_singleton=True``, so this test never authorizes+releases it (the shared org
    tolerates only one Effective POL); the full release path is proven on OBJ."""
    org_id = await s5.default_org_id()
    salt = uuid.uuid4().hex[:8]
    did, ha, hrl = await _approved_pol(app_client, token_factory, salt)

    await _set_leadership_flag(org_id, True)
    try:
        st = await _status(app_client, ha, did)
        assert st["is_leadership_artifact"] is True
        assert st["required"] is True
        assert st["authorized"] is False
        blocked = await app_client.post(f"/api/v1/documents/{did}/release", headers=hrl, json={})
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["code"] == "leadership_authorization_required"
    finally:
        await _set_leadership_flag(org_id, False)


# --- 4. An ordinary (non-leadership) document is NOT gated, even with the flag on --------------


async def test_ordinary_document_not_gated_even_with_flag_on(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A non-leadership type (SOP) releases freely even when the flag is on — the gate discriminates
    on document_type.code ∈ {POL, OBJ, MR}, so ordinary documents are untouched."""
    org_id = await s5.default_org_id()
    salt = uuid.uuid4().hex[:8]
    author, approver, releaser = f"ld-sopau-{salt}", f"ld-sopap-{salt}", f"ld-soprl-{salt}"
    ha, hap, hrl = (
        _auth(token_factory, author),
        _auth(token_factory, approver),
        _auth(token_factory, releaser),
    )
    await s5.grant_lifecycle(author)
    await s5.grant_role(approver, "Approver")
    await _grant(releaser, _RELEASE_KEYS)
    did = await s5.drive_to_approved(app_client, ha, hap, await s5.type_id("SOP"), b"SOP v1")

    await _set_leadership_flag(org_id, True)
    try:
        st = await _status(app_client, ha, did)
        assert st["is_leadership_artifact"] is False
        assert st["required"] is False
        rel = await app_client.post(f"/api/v1/documents/{did}/release", headers=hrl, json={})
        assert rel.status_code == 200, rel.text  # ordinary doc released despite the flag
    finally:
        await _set_leadership_flag(org_id, False)


# --- 5. Request guards ------------------------------------------------------------------------


async def test_request_requires_leadership_artifact(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A request on a non-leadership type is a 409 ``not_a_leadership_artifact`` (the type check
    fires before the flag — independent of it)."""
    salt = uuid.uuid4().hex[:8]
    author, approver = f"ld-naau-{salt}", f"ld-naap-{salt}"
    ha, hap = _auth(token_factory, author), _auth(token_factory, approver)
    await s5.grant_lifecycle(author)
    await s5.grant_role(approver, "Approver")
    did = await s5.drive_to_approved(app_client, ha, hap, await s5.type_id("SOP"), b"SOP v1")
    r = await app_client.post(
        f"/api/v1/documents/{did}/request-leadership-authorization", headers=ha, json={}
    )
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "not_a_leadership_artifact"


async def test_request_requires_approved(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A request before the artifact is Approved is a 409 ``document_not_approved`` (a Draft POL has
    no Approved version to authorize)."""
    salt = uuid.uuid4().hex[:8]
    author = f"ld-draftau-{salt}"
    ha = _auth(token_factory, author)
    await s5.grant_lifecycle(author)
    doc = await _create(app_client, ha, await s5.type_id("POL"))
    r = await app_client.post(
        f"/api/v1/documents/{doc['id']}/request-leadership-authorization", headers=ha, json={}
    )
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "document_not_approved"


async def test_request_twice_is_conflict(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """At most one active authorization per document: a second request while the first is pending is
    a 409 ``authorization_in_progress``. (POL stays Approved — never released.)"""
    salt = uuid.uuid4().hex[:8]
    await _assign_top_mgmt(f"ld-twicetm-{salt}")
    did, ha, _ = await _approved_pol(app_client, token_factory, salt)
    await _request(app_client, ha, did)
    again = await app_client.post(
        f"/api/v1/documents/{did}/request-leadership-authorization", headers=ha, json={}
    )
    assert again.status_code == 409, again.text
    assert again.json()["code"] == "authorization_in_progress"


# --- 6. A Top-Management decline is decisive (no signature, re-requestable) ---------------------


async def test_decline_is_decisive_and_rerequestable(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A Top-Management decline mints NO signature, leaves the document unauthorized, and is
    DECISIVE — it ends the cycle (REJECTED) immediately even with ≥2 Top-Management members present
    (the service forces it terminal + skips sibling tasks, the decide_dcr_approval precedent). A
    fresh request is then allowed, and a non-verify/reject outcome (approve) is refused (422) so the
    generic ANY quorum cannot mint a spurious verify."""
    salt = uuid.uuid4().hex[:8]
    tm_subj = f"ld-dectm-{salt}"
    await _assign_top_mgmt(tm_subj)
    htm = _auth(token_factory, tm_subj)
    did, ha, _ = await _approved_pol(app_client, token_factory, salt)

    req = await _request(app_client, ha, did)
    task_id = await _my_pending_task(app_client, htm, str(req["instance_id"]))
    declined = (
        await app_client.post(
            f"/api/v1/tasks/{task_id}/decision",
            headers=htm,
            json={"outcome": "reject", "comment": "Not yet"},
        )
    ).json()
    assert declined["current_state"] == "REJECTED", declined
    assert declined.get("signature_event_id") is None

    # The document is untouched (unauthorized) — release would still be blocked.
    assert (await _status(app_client, ha, did))["authorized"] is False

    # REJECTED is terminal → a fresh authorization can be requested.
    again = await _request(app_client, ha, did)
    assert again["current_state"] == "leadership_authorization"

    # A generic positive outcome (approve) is refused (422) — never mints a verify signature.
    fresh = await _my_pending_task(app_client, htm, str(again["instance_id"]))
    bad = await app_client.post(
        f"/api/v1/tasks/{fresh}/decision", headers=htm, json={"outcome": "approve"}
    )
    assert bad.status_code == 422, bad.text


# --- 7. The SIGN is candidate-pool authority (a non-member 404-collapses) ----------------------


async def test_non_top_management_cannot_sign(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Authority is Top-Management role membership, not a permission key: the requesting author
    (not a Top-Management member) cannot decide the authorization task — the sensitive-task gate
    404-collapses."""
    salt = uuid.uuid4().hex[:8]
    await _assign_top_mgmt(f"ld-nmtm-{salt}")
    did, ha, _ = await _approved_pol(app_client, token_factory, salt)
    req = await _request(app_client, ha, did)
    task_id = str(req["tasks"][0]["id"])
    blocked = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=ha, json={"outcome": "verify"}
    )
    assert blocked.status_code == 404, blocked.text


# --- 8. Idempotent replay (POL — verify only, never released) -----------------------------------


async def test_sign_is_idempotent_on_replay(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """An Idempotency-Key replay of the same completing verify returns the SAME signature_event_id
    and writes no second signature (the engine replay + the ``_enrich_completed_replay``
    precedent). The POL is verified but never released (singleton-safe)."""
    salt = uuid.uuid4().hex[:8]
    tm_subj = f"ld-idtm-{salt}"
    await _assign_top_mgmt(tm_subj)
    htm = _auth(token_factory, tm_subj)
    did, ha, _ = await _approved_pol(app_client, token_factory, salt)
    req = await _request(app_client, ha, did)
    task_id = await _my_pending_task(app_client, htm, str(req["instance_id"]))

    key = uuid.uuid4().hex
    first = (
        await app_client.post(
            f"/api/v1/tasks/{task_id}/decision",
            headers={**htm, "Idempotency-Key": key},
            json={"outcome": "verify", "comment": "ok"},
        )
    ).json()
    assert first["current_state"] == "COMPLETED"
    sig_id = first["signature_event_id"]
    version_id = first["document_version_id"]
    assert sig_id is not None

    replay = (
        await app_client.post(
            f"/api/v1/tasks/{task_id}/decision",
            headers={**htm, "Idempotency-Key": key},
            json={"outcome": "verify", "comment": "ok"},
        )
    ).json()
    assert replay["signature_event_id"] == sig_id

    async with get_sessionmaker()() as s:
        sig_count = (
            await s.execute(
                select(func.count())
                .select_from(SignatureEvent)
                .where(
                    SignatureEvent.signed_object_id == uuid.UUID(version_id),
                    SignatureEvent.meaning == SignatureMeaning.verify,
                )
            )
        ).scalar_one()
        assert sig_count == 1


# --- 8b. A version is authorized exactly once (re-request 409s) --------------------------------


async def test_request_rejected_when_already_authorized(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Once a version is authorized (a COMPLETED verify), a fresh request for the SAME version is a
    409 ``already_authorized`` — a version is authorized exactly once (no duplicate verify sig, so
    the replay re-derivation stays single-valued). POL is verified but never released."""
    salt = uuid.uuid4().hex[:8]
    tm_subj = f"ld-duptm-{salt}"
    await _assign_top_mgmt(tm_subj)
    htm = _auth(token_factory, tm_subj)
    did, ha, _ = await _approved_pol(app_client, token_factory, salt)

    req = await _request(app_client, ha, did)
    task_id = await _my_pending_task(app_client, htm, str(req["instance_id"]))
    decision = (
        await app_client.post(
            f"/api/v1/tasks/{task_id}/decision", headers=htm, json={"outcome": "verify"}
        )
    ).json()
    assert decision["current_state"] == "COMPLETED", decision

    again = await app_client.post(
        f"/api/v1/documents/{did}/request-leadership-authorization", headers=ha, json={}
    )
    assert again.status_code == 409, again.text
    assert again.json()["code"] == "already_authorized"


# --- 9. MR (non-singleton): release endpoint hits the same shared _cutover gate ----------------


async def test_mr_release_gated_when_flag_on(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A Management Review (MR, §9.3) is recognized as a leadership artifact, and its
    ``/management-reviews/{id}/release`` path hits the shared cutover gate → 409 when the flag is
    on."""
    org_id = await s5.default_org_id()
    salt = uuid.uuid4().hex[:8]
    submitter, approver, releaser = f"ld-mrsm-{salt}", f"ld-mrap-{salt}", f"ld-mrrl-{salt}"
    hs, hap, hrl = (
        _auth(token_factory, submitter),
        _auth(token_factory, approver),
        _auth(token_factory, releaser),
    )
    await _grant(submitter, _MR_KEYS)
    await s5.grant_role(approver, "Approver")
    await _grant(releaser, _RELEASE_KEYS)
    async with get_sessionmaker()() as s:
        owner_id = (await _ensure_user(s, submitter)).id

    rid = await _create_review(app_client, hs, f"Management review {salt}")
    out = await app_client.post(
        f"/api/v1/management-reviews/{rid}/outputs",
        headers=hs,
        json={
            "output_type": "ACTION",
            "description": "Tighten supplier controls",
            "owner_user_id": str(owner_id),
            "due_date": "2026-12-31",
        },
    )
    assert out.status_code == 201, out.text
    submitted = await app_client.post(f"/api/v1/management-reviews/{rid}/submit-review", headers=hs)
    assert submitted.status_code == 200, submitted.text
    task_id = await s5.task_for_doc(rid)
    dec = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=hap, json={"outcome": "approve"}
    )
    assert dec.status_code == 200, dec.text

    await _set_leadership_flag(org_id, True)
    try:
        st = await _status(app_client, hrl, rid)
        assert st["is_leadership_artifact"] is True
        assert st["required"] is True
        rel = await app_client.post(f"/api/v1/management-reviews/{rid}/release", headers=hrl)
        assert rel.status_code == 409, rel.text
        assert rel.json()["code"] == "leadership_authorization_required"
    finally:
        await _set_leadership_flag(org_id, False)


# --- 10. CX-1: the per-caller, scope-aware ``can_request`` capability --------------------------


async def test_status_can_request_is_per_caller(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """CX-1: ``can_request`` on the leadership-authorization status is the server-computed,
    ABAC-aware answer to "may THIS caller request a Top-Management authorization" — it reflects
    whether the caller holds ``document.approve`` at the document's scope (the request endpoint's
    exact gate), NOT a SYSTEM-scoped /me/permissions probe (the visibility gap CX-1 closes). It is
    the pure authz answer, independent of the org flag / Approved state (so this test needs
    neither): the requester (holds document.approve) reads True; the releaser (holds
    document.release + read but NOT approve) reads False — both can GET the status."""
    salt = uuid.uuid4().hex[:8]
    oid, hrq, hrl = await _approved_obj(app_client, token_factory, salt)
    st_requester = await _status(app_client, hrq, oid)
    assert st_requester["can_request"] is True, st_requester
    st_releaser = await _status(app_client, hrl, oid)
    assert st_releaser["can_request"] is False, st_releaser
