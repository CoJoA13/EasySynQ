"""S-capa-1 integration proofs — CAPA core + intake (capas / complaints / ncrs) over HTTP against
testcontainer Postgres + MinIO + Redis.

The seeded ``capa.*`` / ``ncr.*`` keys ride the Process-Owner / QMS-Owner / Internal-Auditor roles
(PROCESS-scoped placeholders), but the test actor has no role assignment, so each test grants the
keys it needs via SYSTEM-scope overrides (the ``test_audits`` precedent; a SYSTEM grant matches any
resource context). Assertions are scoped to **this run's own** capa / complaint / ncr ids — the
integration suite shares one session DB across files, so absolute counts are never asserted.
"""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from easysynq_api.db.models._audit_enums import AuditObjectType, EventType
from easysynq_api.db.models._capa_enums import NcSeverity
from easysynq_api.db.models.app_user import AppUser
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.authz_grant import PermissionOverride
from easysynq_api.db.models.capa import Capa
from easysynq_api.db.models.capa_stage import CapaStage
from easysynq_api.db.models.permission import Permission
from easysynq_api.db.models.role import Role, RoleAssignment
from easysynq_api.db.models.scope import Scope
from easysynq_api.db.models.signature_event import SignatureEvent
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.domain.authz.types import Effect, ScopeLevel
from easysynq_api.domain.capa import default_target_date
from easysynq_api.problems import ProblemException
from easysynq_api.services.capa import advance_capa_to_containment
from easysynq_api.services.common.org_clock import resolve_org_tz

from .test_vault import _auth, _ensure_user

pytestmark = pytest.mark.integration

_CAPA_KEYS = (
    "capa.read",
    "capa.create",
    "capa.update",
    "ncr.read",
    "ncr.create",
    "ncr.record_correction",
    "record.read",
    "record.create",
)


def _subject(prefix: str) -> str:
    return f"kc-{prefix}-{uuid.uuid4().hex[:10]}"


async def _grant(subject: str, keys: tuple[str, ...]) -> uuid.UUID:
    """Grant the given permission keys at SYSTEM scope via override (the S2/test_audits pattern)."""
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
        return user.id


async def _event_count(object_id: str, event_type: EventType) -> int:
    async with get_sessionmaker()() as s:
        return (
            await s.execute(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.object_id == uuid.UUID(object_id),
                    AuditEvent.event_type == event_type,
                )
            )
        ).scalar_one()


# --- CAPA lifecycle ---------------------------------------------------------------------------


async def test_raise_capa_then_containment(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("capa")
    await _grant(subject, _CAPA_KEYS)
    h = _auth(token_factory, subject)

    r = await app_client.post(
        "/api/v1/capas",
        headers=h,
        json={"title": "Mislabelled lot", "severity": "Major", "problem": "wrong label applied"},
    )
    assert r.status_code == 201, r.text
    capa = r.json()
    capa_id = capa["id"]
    assert capa["close_state"] == "Raised"
    assert capa["severity"] == "Major"
    assert capa["source"] == "process"
    assert capa["identifier"].startswith("REC-")
    assert capa["origin_finding_id"] is None

    # The detail view carries the sealed Raised stage block.
    detail = (await app_client.get(f"/api/v1/capas/{capa_id}", headers=h)).json()
    assert [s["stage"] for s in detail["stages"]] == ["Raised"]
    assert detail["stages"][0]["content_block"]["problem"] == "wrong label applied"

    # Advance Raised → Containment.
    r = await app_client.post(
        f"/api/v1/capas/{capa_id}/containment",
        headers=h,
        json={"content_block": {"correction": "quarantined the lot", "evidence_note": "photo"}},
    )
    assert r.status_code == 200, r.text
    assert r.json()["close_state"] == "Containment"

    detail = (await app_client.get(f"/api/v1/capas/{capa_id}", headers=h)).json()
    assert [s["stage"] for s in detail["stages"]] == ["Raised", "Containment"]

    assert await _event_count(capa_id, EventType.CAPA_RAISED) == 1
    assert await _event_count(capa_id, EventType.CAPA_TRANSITIONED) == 1


async def test_invalid_capa_transition_is_409(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("capa-inv")
    await _grant(subject, _CAPA_KEYS)
    h = _auth(token_factory, subject)
    capa_id = (
        await app_client.post("/api/v1/capas", headers=h, json={"title": "P", "severity": "Minor"})
    ).json()["id"]
    # First Containment is legal; a second is illegal (Containment → Containment is no transition).
    assert (
        await app_client.post(
            f"/api/v1/capas/{capa_id}/containment", headers=h, json={"content_block": {"c": "1"}}
        )
    ).status_code == 200
    r = await app_client.post(
        f"/api/v1/capas/{capa_id}/containment", headers=h, json={"content_block": {"c": "2"}}
    )
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "invalid_capa_transition"


async def test_review_output_source_is_rejected(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    # review_output is a reserved forward seam (Mgmt-Review) — never written in v1.
    subject = _subject("capa-rev")
    await _grant(subject, _CAPA_KEYS)
    h = _auth(token_factory, subject)
    r = await app_client.post(
        "/api/v1/capas",
        headers=h,
        json={"title": "P", "severity": "Minor", "source": "review_output"},
    )
    assert r.status_code == 422, r.text


async def test_empty_content_block_is_422(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("capa-empty")
    await _grant(subject, _CAPA_KEYS)
    h = _auth(token_factory, subject)
    capa_id = (
        await app_client.post("/api/v1/capas", headers=h, json={"title": "P", "severity": "Minor"})
    ).json()["id"]
    r = await app_client.post(
        f"/api/v1/capas/{capa_id}/containment", headers=h, json={"content_block": {}}
    )
    assert r.status_code == 422, r.text


async def test_unknown_process_id_is_404(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    # The _check_process guard rejects a process_id that is not in the actor's org (here, unknown).
    subject = _subject("capa-proc")
    await _grant(subject, _CAPA_KEYS)
    h = _auth(token_factory, subject)
    r = await app_client.post(
        "/api/v1/capas",
        headers=h,
        json={"title": "P", "severity": "Minor", "process_id": str(uuid.uuid4())},
    )
    assert r.status_code == 404, r.text


# --- Complaints + idempotent spawn ------------------------------------------------------------


async def test_complaint_capture_and_idempotent_spawn(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("cmp")
    await _grant(subject, _CAPA_KEYS)
    h = _auth(token_factory, subject)

    r = await app_client.post(
        "/api/v1/complaints",
        headers=h,
        json={
            "description": "delivered late",
            "customer": "Acme Co",
            "channel": "email",
            "severity": "Major",
        },
    )
    assert r.status_code == 201, r.text
    complaint = r.json()
    complaint_id = complaint["id"]
    assert complaint["spawned_capa_id"] is None
    assert complaint["identifier"].startswith("REC-")

    # First spawn → 201, a Raised CAPA sourced from the complaint (severity inherited).
    r1 = await app_client.post(f"/api/v1/complaints/{complaint_id}/spawn-capa", headers=h, json={})
    assert r1.status_code == 201, r1.text
    capa = r1.json()
    assert capa["source"] == "complaint"
    assert capa["severity"] == "Major"
    assert capa["close_state"] == "Raised"

    # The complaint now carries the latch.
    refreshed = (await app_client.get(f"/api/v1/complaints/{complaint_id}", headers=h)).json()
    assert refreshed["spawned_capa_id"] == capa["id"]

    # Idempotent replay → 200, the SAME CAPA, no second spawn event.
    r2 = await app_client.post(f"/api/v1/complaints/{complaint_id}/spawn-capa", headers=h, json={})
    assert r2.status_code == 200, r2.text
    assert r2.json()["id"] == capa["id"]
    assert await _event_count(complaint_id, EventType.COMPLAINT_SPAWNED_CAPA) == 1
    assert await _event_count(complaint_id, EventType.COMPLAINT_CAPTURED) == 1


async def test_spawn_capa_replay_reauthorizes_read_at_capa_scope(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Batch 6 (read-authz on returned bodies): the idempotent spawn-capa REPLAY must re-authorize
    capa.read at the returned CAPA's OWN scope. A caller holding capa.create (which only gates the
    create side, at a caller-CHOSEN process) but NOT capa.read must not harvest a pre-existing
    (cross-process) CAPA's header via replay. Mutation-verify: without the read re-check the
    replay returns 200 + the CAPA body."""
    # Owner (full CAPA keys) captures a complaint and spawns its CAPA.
    owner = _subject("spawn-owner")
    await _grant(owner, _CAPA_KEYS)
    ho = _auth(token_factory, owner)
    complaint_id = (
        await app_client.post(
            "/api/v1/complaints", headers=ho, json={"description": "late", "severity": "Minor"}
        )
    ).json()["id"]
    r1 = await app_client.post(f"/api/v1/complaints/{complaint_id}/spawn-capa", headers=ho, json={})
    assert r1.status_code == 201, r1.text
    capa_id = r1.json()["id"]

    # Attacker holds capa.create (passes the create gate) but NOT capa.read.
    attacker = _subject("spawn-attacker")
    await _grant(attacker, ("capa.create",))
    ha = _auth(token_factory, attacker)
    # Replaying spawn-capa on the same complaint returns the existing CAPA → read re-check at the
    # CAPA's own scope denies (the attacker has no capa.read) → 403, header NOT leaked.
    r2 = await app_client.post(f"/api/v1/complaints/{complaint_id}/spawn-capa", headers=ha, json={})
    assert r2.status_code == 403, r2.text
    assert capa_id not in r2.text  # the CAPA header/body must not leak in the denial

    # A caller WITH capa.read still gets the idempotent 200 + body (the fix keeps the happy path).
    reader = _subject("spawn-reader")
    await _grant(reader, ("capa.create", "capa.read"))
    hr = _auth(token_factory, reader)
    r3 = await app_client.post(f"/api/v1/complaints/{complaint_id}/spawn-capa", headers=hr, json={})
    assert r3.status_code == 200, r3.text
    assert r3.json()["id"] == capa_id


async def test_spawn_requires_severity(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("cmp-sev")
    await _grant(subject, _CAPA_KEYS)
    h = _auth(token_factory, subject)
    complaint_id = (
        await app_client.post("/api/v1/complaints", headers=h, json={"description": "x"})
    ).json()["id"]
    # No severity on the complaint and none in the spawn body → 422.
    r = await app_client.post(f"/api/v1/complaints/{complaint_id}/spawn-capa", headers=h, json={})
    assert r.status_code == 422, r.text
    # Providing one at spawn-time succeeds (late triage).
    r = await app_client.post(
        f"/api/v1/complaints/{complaint_id}/spawn-capa", headers=h, json={"severity": "Minor"}
    )
    assert r.status_code == 201, r.text
    assert r.json()["severity"] == "Minor"


# --- NCRs -------------------------------------------------------------------------------------


async def test_ncr_create_then_disposition(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("ncr")
    await _grant(subject, _CAPA_KEYS)
    h = _auth(token_factory, subject)

    r = await app_client.post(
        "/api/v1/ncrs",
        headers=h,
        json={"source": "process", "description": "out-of-spec weld", "severity": "Major"},
    )
    assert r.status_code == 201, r.text
    ncr = r.json()
    ncr_id = ncr["id"]
    assert ncr["identifier"].startswith("NCR-")
    assert ncr["disposition"] is None

    r = await app_client.patch(
        f"/api/v1/ncrs/{ncr_id}/disposition",
        headers=h,
        json={"disposition": "rework", "notes": "re-weld per WI-12"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["disposition"] == "rework"
    assert body["disposition_authorized_by"] is not None
    assert body["disposed_at"] is not None

    # One-shot: a second disposition is a 409.
    r = await app_client.patch(
        f"/api/v1/ncrs/{ncr_id}/disposition", headers=h, json={"disposition": "scrap"}
    )
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "ncr_already_dispositioned"

    # The 'return' token (a Python keyword in the enum) round-trips through the API.
    r2 = await app_client.post(
        "/api/v1/ncrs",
        headers=h,
        json={"source": "audit", "description": "wrong part", "severity": "Minor"},
    )
    nid2 = r2.json()["id"]
    rd = await app_client.patch(
        f"/api/v1/ncrs/{nid2}/disposition", headers=h, json={"disposition": "return"}
    )
    assert rd.status_code == 200, rd.text
    assert rd.json()["disposition"] == "return"
    assert await _event_count(ncr_id, EventType.NCR_CREATED) == 1
    assert await _event_count(ncr_id, EventType.NCR_DISPOSITIONED) == 1
    # NCR events key on object_type=ncr (own table), not record.
    async with get_sessionmaker()() as s:
        ev = (
            await s.execute(
                select(AuditEvent).where(
                    AuditEvent.object_id == uuid.UUID(ncr_id),
                    AuditEvent.event_type == EventType.NCR_CREATED,
                )
            )
        ).scalar_one()
    assert ev.object_type == AuditObjectType.ncr


# --- structural invariants --------------------------------------------------------------------


async def test_capa_stage_is_append_only(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The running app connects as the NON-OWNER easysynq_app role → the capa_stage REVOKE bites
    (SQLSTATE 42501). Immutability of the sealed stage trail is structural, not conventional."""
    subject = _subject("capa-ao")
    await _grant(subject, _CAPA_KEYS)
    h = _auth(token_factory, subject)
    capa_id = (
        await app_client.post("/api/v1/capas", headers=h, json={"title": "P", "severity": "Minor"})
    ).json()["id"]
    async with get_sessionmaker()() as s:
        stage_id = (
            await s.execute(select(CapaStage.id).where(CapaStage.capa_id == uuid.UUID(capa_id)))
        ).scalar_one()
    for stmt in (
        "UPDATE capa_stage SET cycle_marker = 99 WHERE id = :id",
        "DELETE FROM capa_stage WHERE id = :id",
    ):
        async with get_sessionmaker()() as s:
            with pytest.raises(DBAPIError) as exc:
                await s.execute(text(stmt), {"id": stage_id})
                await s.commit()
            assert getattr(exc.value.orig, "sqlstate", None) == "42501", stmt


async def test_grant_backfill_present(app_under_test: object) -> None:
    """The slice-0 grant backfill (0036) granted the three orphaned keys to the right roles. Needs
    ``app_under_test`` to repoint ``get_sessionmaker()`` at the testcontainer DB (no app_client)."""
    async with get_sessionmaker()() as s:
        for role_name, perm_key in (
            ("Process Owner", "capa.update"),
            ("QMS Owner", "ncr.create"),
            ("Internal Auditor", "ncr.create"),
            ("QMS Owner", "ncr.record_correction"),
        ):
            n = (
                await s.execute(
                    text(
                        "SELECT count(*) FROM role_grant rg "
                        "JOIN role r ON rg.role_id = r.id "
                        "JOIN permission p ON rg.permission_id = p.id "
                        "WHERE r.name = :rn AND p.key = :pk"
                    ),
                    {"rn": role_name, "pk": perm_key},
                )
            ).scalar_one()
            # Exactly one grant per (role, permission) — the seeded single org + the idempotent
            # on_conflict backfill. A >1 would mean a duplicate/double-applied backfill.
            assert n == 1, f"expected one backfill grant for {role_name} → {perm_key}, got {n}"


async def test_cross_org_advance_is_denied(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The service layer is the authoritative org boundary (the resolver does not org-check). An
    actor from another org cannot advance this org's CAPA — 404 before any write. Proven at the
    service layer to avoid leaving a 2nd Org in the shared DB."""
    subject = _subject("capa-xorg")
    await _grant(subject, _CAPA_KEYS)
    h = _auth(token_factory, subject)
    capa_id = uuid.UUID(
        (
            await app_client.post(
                "/api/v1/capas", headers=h, json={"title": "P", "severity": "Minor"}
            )
        ).json()["id"]
    )
    intruder = AppUser(id=uuid.uuid4(), org_id=uuid.uuid4(), keycloak_subject="kc-other-org")
    async with get_sessionmaker()() as s:
        with pytest.raises(ProblemException) as exc:
            await advance_capa_to_containment(s, intruder, capa_id, content_block={"c": "x"})
    assert exc.value.status == 404
    # Untouched for its own org.
    detail = (await app_client.get(f"/api/v1/capas/{capa_id}", headers=h)).json()
    assert detail["close_state"] == "Raised"


async def test_capa_list_filters_not_403_ncr_still_enforces(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """S-capa-raise-process: GET /capas row-filters (filter-not-403, doc 18 §5.2) — a no-grant
    caller gets 200 + an empty list (they hold capa.read at no scope, so no CAPA matches), NOT the
    pre-slice 403. GET /ncrs is still SYSTEM-enforced (no row-filter) → 403."""
    subject = _subject("capa-nogrant")
    async with get_sessionmaker()() as s:
        await _ensure_user(s, subject)
        await s.commit()
    h = _auth(token_factory, subject)
    capas = await app_client.get("/api/v1/capas", headers=h)
    assert capas.status_code == 200, capas.text
    assert capas.json()["data"] == []
    assert (await app_client.get("/api/v1/ncrs", headers=h)).status_code == 403


async def test_config_exposes_allow_capa_self_verify(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("capa-cfg")
    await _grant(subject, ("config.update",))
    h = _auth(token_factory, subject)
    # Default OFF (fail-closed).
    cfg = (await app_client.get("/api/v1/admin/config", headers=h)).json()
    assert cfg["allow_capa_self_verify"] is False
    # Flippable + readable back (the forward seam an operator can pre-set for S-capa-3).
    r = await app_client.patch(
        "/api/v1/admin/config", headers=h, json={"allow_capa_self_verify": True}
    )
    assert r.status_code == 200, r.text
    assert r.json()["allow_capa_self_verify"] is True
    # Restore default so the shared-DB config doesn't leak into other tests — assert it stuck (a
    # silent failure would leave the flag set and pollute the shared session DB).
    rr = await app_client.patch(
        "/api/v1/admin/config", headers=h, json={"allow_capa_self_verify": False}
    )
    assert rr.status_code == 200 and rr.json()["allow_capa_self_verify"] is False, rr.text


# --- S-capa-2: RCA + Action-Plan + the severity-routed approval ------------------------------
#
# The CAPA approval candidate pool resolves by Role MEMBERSHIP (``users_with_roles``), NOT by the
# SYSTEM permission overrides the proposer rides — so an approver must be ASSIGNED the seeded
# ``QMS Owner`` / ``Top Management`` role (the S-capa-2 test gotcha). Assertions are run-scoped to
# the specific CAPA / signature ids this test created (the shared session DB grows the pools).

_PROPOSE_KEYS = (
    "capa.read",
    "capa.create",
    "capa.update",
    "capa.record_rca",
    "capa.plan_action",
)

_ACTION_PLAN = {
    "action_items": [{"description": "re-train operators", "owner": "diego", "due_date": "2026-08"}]
}


async def _assign_seeded_role(subject: str, role_name: str) -> uuid.UUID:
    """Assign a SEEDED role (e.g. ``QMS Owner`` / ``Top Management``) to a user via RoleAssignment —
    candidate pools are role-membership, not SYSTEM permission overrides."""
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        role = (await s.execute(select(Role).where(Role.name == role_name))).scalar_one()
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


async def _drive_to_root_cause(
    client: AsyncClient, headers: dict[str, str], *, severity: str, title: str
) -> str:
    """Raise a CAPA at ``severity`` and walk Raised→Containment→RootCause; return its id."""
    r = await client.post(
        "/api/v1/capas",
        headers=headers,
        json={"title": title, "severity": severity, "problem": "p"},
    )
    assert r.status_code == 201, r.text
    capa_id = r.json()["id"]
    c = await client.post(
        f"/api/v1/capas/{capa_id}/containment",
        headers=headers,
        json={"content_block": {"correction": "isolate the lot"}},
    )
    assert c.status_code == 200, c.text
    rc = await client.post(
        f"/api/v1/capas/{capa_id}/root-cause",
        headers=headers,
        json={"content_block": {"root_cause": "missing check", "method": "5-whys"}},
    )
    assert rc.status_code == 200, rc.text
    assert rc.json()["close_state"] == "RootCause"
    return capa_id


async def _my_pending_task(client: AsyncClient, headers: dict[str, str], instance_id: str) -> str:
    """The caller's own PENDING task for an instance (self-scoped My-Tasks; one per candidate)."""
    r = await client.get(f"/api/v1/tasks?instance_id={instance_id}&state=PENDING", headers=headers)
    assert r.status_code == 200, r.text
    tasks = r.json()
    assert len(tasks) == 1, tasks
    return tasks[0]["id"]


async def test_root_cause_requires_containment_first(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The RootCause gate is the pure FSM: a freshly-Raised CAPA cannot jump to RootCause (409)."""
    subject = _subject("rca-fsm")
    await _grant(subject, _PROPOSE_KEYS)
    h = _auth(token_factory, subject)
    capa_id = (
        await app_client.post("/api/v1/capas", headers=h, json={"title": "x", "severity": "Minor"})
    ).json()["id"]
    r = await app_client.post(
        f"/api/v1/capas/{capa_id}/root-cause", headers=h, json={"content_block": {"rc": "x"}}
    )
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "invalid_capa_transition"


async def test_minor_action_plan_qm_approval_writes_signature(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Minor: a single QMS-Owner approves → the action plan is sealed as a SIGNED ActionPlan stage
    block (``signature_event(meaning=approval, signed_object=capa_stage)`` + ``signed_event_id``),
    and ``close_state`` flips RootCause→ActionPlan only at approval-complete."""
    qm_subj = _subject("qm-minor")
    approver = await _assign_seeded_role(qm_subj, "QMS Owner")
    ha = _auth(token_factory, qm_subj)

    proposer_subj = _subject("ap-minor")
    proposer = await _grant(proposer_subj, _PROPOSE_KEYS)
    hp = _auth(token_factory, proposer_subj)
    capa_id = await _drive_to_root_cause(app_client, hp, severity="Minor", title="Minor AP")

    pr = await app_client.post(
        f"/api/v1/capas/{capa_id}/action-plan", headers=hp, json={"content_block": _ACTION_PLAN}
    )
    assert pr.status_code == 200, pr.text
    body = pr.json()
    assert body["close_state"] == "RootCause"  # NOT flipped until approval completes
    iid = body["approval_instance"]["id"]
    assert body["approval_instance"]["current_state"] == "qm_approval"

    task_id = await _my_pending_task(app_client, ha, iid)
    # the single-task read carries the subject discriminator; this is a CAPA action-plan approval
    # task, so it points back at the CAPA it approves
    detail_task = (await app_client.get(f"/api/v1/tasks/{task_id}", headers=ha)).json()
    assert detail_task["subject_type"] == "CAPA"
    assert detail_task["subject_id"] == capa_id
    dr = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=ha, json={"outcome": "approve"}
    )
    assert dr.status_code == 200, dr.text
    decision = dr.json()
    assert decision["current_state"] == "COMPLETED"
    assert decision["capa_close_state"] == "ActionPlan"
    sig_id = decision["signature_event_id"]
    assert sig_id is not None

    # The CAPA is now ActionPlan with a SIGNED ActionPlan stage carrying the action items + both
    # actor identities sealed into the immutable block.
    detail = (await app_client.get(f"/api/v1/capas/{capa_id}", headers=hp)).json()
    assert detail["close_state"] == "ActionPlan"
    ap_stages = [s for s in detail["stages"] if s["stage"] == "ActionPlan"]
    assert len(ap_stages) == 1
    assert ap_stages[0]["content_block"]["action_items"] == _ACTION_PLAN["action_items"]
    assert ap_stages[0]["content_block"]["approved_by"] == str(approver)
    assert ap_stages[0]["content_block"]["proposed_by"] == str(proposer)

    async with get_sessionmaker()() as s:
        sig = (
            await s.execute(select(SignatureEvent).where(SignatureEvent.id == uuid.UUID(sig_id)))
        ).scalar_one()
        assert sig.meaning.value == "approval"
        assert sig.signed_object_type.value == "capa_stage"
        assert sig.signer_user_id == approver
        stage = (
            await s.execute(select(CapaStage).where(CapaStage.id == sig.signed_object_id))
        ).scalar_one()
        assert stage.stage.value == "ActionPlan"
        assert stage.signed_event_id == sig.id  # mutual reference, set at INSERT (no UPDATE)


async def test_critical_action_plan_two_tier_approval(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Critical routes to SEQUENTIAL QMS-Owner → Top-Management stages: the QM approval advances the
    flow (still PENDING), and only the Top-Management approval completes it (the 'QM AND
    top-management' conjunction, doc 10 §6.3)."""
    qm_subj, tm_subj = _subject("qm-crit"), _subject("tm-crit")
    qm = await _assign_seeded_role(qm_subj, "QMS Owner")
    tm = await _assign_seeded_role(tm_subj, "Top Management")
    hqm, htm = _auth(token_factory, qm_subj), _auth(token_factory, tm_subj)

    proposer_subj = _subject("ap-crit")
    await _grant(proposer_subj, _PROPOSE_KEYS)
    hp = _auth(token_factory, proposer_subj)
    capa_id = await _drive_to_root_cause(app_client, hp, severity="Critical", title="Crit AP")

    pr = await app_client.post(
        f"/api/v1/capas/{capa_id}/action-plan", headers=hp, json={"content_block": _ACTION_PLAN}
    )
    iid = pr.json()["approval_instance"]["id"]
    assert pr.json()["approval_instance"]["current_state"] == "crit_qm"

    # QM tier: advances to the Top-Management stage but does NOT complete (no signature yet).
    qm_task = await _my_pending_task(app_client, hqm, iid)
    r1 = (
        await app_client.post(
            f"/api/v1/tasks/{qm_task}/decision", headers=hqm, json={"outcome": "approve"}
        )
    ).json()
    assert r1["current_state"] == "crit_topmgmt"
    assert r1.get("signature_event_id") is None
    assert (await app_client.get(f"/api/v1/capas/{capa_id}", headers=hp)).json()[
        "close_state"
    ] == "RootCause"

    # Top-Management tier: completes + signs + flips to ActionPlan.
    tm_task = await _my_pending_task(app_client, htm, iid)
    r2 = (
        await app_client.post(
            f"/api/v1/tasks/{tm_task}/decision", headers=htm, json={"outcome": "approve"}
        )
    ).json()
    assert r2["current_state"] == "COMPLETED"
    assert r2["capa_close_state"] == "ActionPlan"
    async with get_sessionmaker()() as s:
        sig = (
            await s.execute(
                select(SignatureEvent).where(
                    SignatureEvent.id == uuid.UUID(r2["signature_event_id"])
                )
            )
        ).scalar_one()
        assert sig.signer_user_id == tm  # the COMPLETING (Top-Management) approver signs
    del qm  # the QM's approval is the task_outcome trail; one approval signature per plan


async def test_critical_dual_role_user_cannot_clear_both_tiers(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A single user holding BOTH QMS-Owner and Top-Management cannot satisfy the Critical two-tier
    conjunction alone — the cross-STAGE distinct-approver guard 409s their second decision."""
    dual_subj = _subject("dual")
    await _assign_seeded_role(dual_subj, "QMS Owner")
    await _assign_seeded_role(dual_subj, "Top Management")
    hd = _auth(token_factory, dual_subj)

    proposer_subj = _subject("ap-dual")
    await _grant(proposer_subj, _PROPOSE_KEYS)
    hp = _auth(token_factory, proposer_subj)
    capa_id = await _drive_to_root_cause(app_client, hp, severity="Critical", title="Dual AP")
    iid = (
        await app_client.post(
            f"/api/v1/capas/{capa_id}/action-plan", headers=hp, json={"content_block": _ACTION_PLAN}
        )
    ).json()["approval_instance"]["id"]

    qm_task = await _my_pending_task(app_client, hd, iid)
    assert (
        await app_client.post(
            f"/api/v1/tasks/{qm_task}/decision", headers=hd, json={"outcome": "approve"}
        )
    ).json()["current_state"] == "crit_topmgmt"
    tm_task = await _my_pending_task(app_client, hd, iid)  # they also hold Top Management
    blocked = await app_client.post(
        f"/api/v1/tasks/{tm_task}/decision", headers=hd, json={"outcome": "approve"}
    )
    assert blocked.status_code == 409, blocked.text
    # The CAPA is NOT yet approved — it stays RootCause until a DISTINCT top-mgmt member signs.
    assert (await app_client.get(f"/api/v1/capas/{capa_id}", headers=hp)).json()[
        "close_state"
    ] == "RootCause"


async def test_action_plan_requires_root_cause_first_and_content(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The propose endpoint guards the FSM (only from RootCause, 409 else) and rejects an empty plan
    (422). Engine-level fail-closed on an empty approver pool is covered by test_workflow_engine."""
    proposer_subj = _subject("ap-guard")
    await _grant(proposer_subj, _PROPOSE_KEYS)
    hp = _auth(token_factory, proposer_subj)
    # A freshly-Raised CAPA cannot propose an action plan (not at RootCause yet).
    capa_id = (
        await app_client.post("/api/v1/capas", headers=hp, json={"title": "g", "severity": "Minor"})
    ).json()["id"]
    early = await app_client.post(
        f"/api/v1/capas/{capa_id}/action-plan", headers=hp, json={"content_block": _ACTION_PLAN}
    )
    assert early.status_code == 409, early.text
    assert early.json()["code"] == "invalid_capa_transition"
    # At RootCause, an empty plan is rejected.
    rc_capa = await _drive_to_root_cause(app_client, hp, severity="Minor", title="Empty AP")
    empty = await app_client.post(
        f"/api/v1/capas/{rc_capa}/action-plan", headers=hp, json={"content_block": {}}
    )
    assert empty.status_code == 422, empty.text


async def test_repropose_blocked_while_approval_active(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """At most one active approval per CAPA: a second propose while the first is still running 409s
    ``capa_approval_in_progress``."""
    await _assign_seeded_role(_subject("qm-repro"), "QMS Owner")
    proposer_subj = _subject("ap-repro")
    await _grant(proposer_subj, _PROPOSE_KEYS)
    hp = _auth(token_factory, proposer_subj)
    capa_id = await _drive_to_root_cause(app_client, hp, severity="Minor", title="Re-propose AP")
    first = await app_client.post(
        f"/api/v1/capas/{capa_id}/action-plan", headers=hp, json={"content_block": _ACTION_PLAN}
    )
    assert first.status_code == 200, first.text
    assert first.json()["approval_instance"]["current_state"] == "qm_approval"  # active
    second = await app_client.post(
        f"/api/v1/capas/{capa_id}/action-plan", headers=hp, json={"content_block": _ACTION_PLAN}
    )
    assert second.status_code == 409, second.text
    assert second.json()["code"] == "capa_approval_in_progress"


async def test_capa_approval_task_rejects_non_candidate(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A user who is NOT in the approval candidate pool cannot decide the task (404 collapse) — the
    role-resolved pool is the authority."""
    await _assign_seeded_role(_subject("qm-auth"), "QMS Owner")
    proposer_subj = _subject("ap-auth")
    await _grant(proposer_subj, _PROPOSE_KEYS)
    hp = _auth(token_factory, proposer_subj)
    capa_id = await _drive_to_root_cause(app_client, hp, severity="Minor", title="Authz AP")
    iid = (
        await app_client.post(
            f"/api/v1/capas/{capa_id}/action-plan", headers=hp, json={"content_block": _ACTION_PLAN}
        )
    ).json()["approval_instance"]["id"]
    # The proposer (not a QMS-Owner role member) cannot see the approval task.
    mine = (await app_client.get(f"/api/v1/tasks?instance_id={iid}", headers=hp)).json()
    assert mine == []
    # …and POSTing a decision on the QMS-Owner's task collapses to 404 (the sole authority is the
    # service's _assert_capa_approver — task-ownership + live-role, both 404, no 403 info leak).
    from easysynq_api.db.models.workflow import Task as _Task

    async with get_sessionmaker()() as s:
        qm_task_id = (
            await s.execute(select(_Task.id).where(_Task.instance_id == uuid.UUID(iid)).limit(1))
        ).scalar_one()
    blocked = await app_client.post(
        f"/api/v1/tasks/{qm_task_id}/decision", headers=hp, json={"outcome": "approve"}
    )
    assert blocked.status_code == 404, blocked.text


async def test_action_plan_decision_idempotent_replay_carries_capa_fields(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """An idempotent replay (same task + Idempotency-Key) of a COMPLETING approval re-derives the
    CAPA-specific fields (capa_close_state + signature_event_id), so a retry's body matches."""
    qm_subj = _subject("qm-idem")
    await _assign_seeded_role(qm_subj, "QMS Owner")
    ha = _auth(token_factory, qm_subj)
    proposer_subj = _subject("ap-idem")
    await _grant(proposer_subj, _PROPOSE_KEYS)
    hp = _auth(token_factory, proposer_subj)
    capa_id = await _drive_to_root_cause(app_client, hp, severity="Minor", title="Idem AP")
    iid = (
        await app_client.post(
            f"/api/v1/capas/{capa_id}/action-plan", headers=hp, json={"content_block": _ACTION_PLAN}
        )
    ).json()["approval_instance"]["id"]
    task_id = await _my_pending_task(app_client, ha, iid)
    key = "idem-" + uuid.uuid4().hex
    first = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision",
        headers={**ha, "Idempotency-Key": key},
        json={"outcome": "approve"},
    )
    assert first.status_code == 200 and first.json()["current_state"] == "COMPLETED", first.text
    sig_id = first.json()["signature_event_id"]
    # Replay with the SAME key → the recorded outcome, NOT a 409, with the CAPA fields re-derived.
    replay = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision",
        headers={**ha, "Idempotency-Key": key},
        json={"outcome": "approve"},
    )
    assert replay.status_code == 200, replay.text
    rb = replay.json()
    assert rb.get("replayed") is True
    assert rb["current_state"] == "COMPLETED"
    assert rb["capa_close_state"] == "ActionPlan"
    assert rb["signature_event_id"] == sig_id


# --- S-capa-3: Implement / Verify / Close — the M4 gate + severity-aware SoD-4 ---------------
#
# The implement key (capa.capture_effectiveness) + verify/close (capa.verify/capa.close) ride SYSTEM
# overrides (the family precedent). Evidence on a stage is linked by reusing the CAPA's OWN record
# id as the evidence artifact (the M4 gate only checks for ≥1 evidence_for_link(CAPA_STAGE) row; the
# capa.id IS a record id). SoD-4 needs a DISTINCT implementer vs verifier (Critical/Major hard).

_IMPLEMENT_KEYS = (*_PROPOSE_KEYS, "capa.capture_effectiveness", "record.create")
_VERIFY_KEYS = ("capa.read", "capa.verify", "capa.close", "record.create", "record.read")


async def _drive_to_action_plan(
    client: AsyncClient, hp: dict[str, str], ha: dict[str, str], *, severity: str, title: str
) -> str:
    """Raise + walk to an APPROVED ActionPlan (single QMS-Owner approval — Minor/Major). Returns the
    CAPA id at close_state=ActionPlan."""
    capa_id = await _drive_to_root_cause(client, hp, severity=severity, title=title)
    iid = (
        await client.post(
            f"/api/v1/capas/{capa_id}/action-plan", headers=hp, json={"content_block": _ACTION_PLAN}
        )
    ).json()["approval_instance"]["id"]
    task_id = await _my_pending_task(client, ha, iid)
    dr = await client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=ha, json={"outcome": "approve"}
    )
    assert dr.status_code == 200, dr.text
    assert dr.json()["capa_close_state"] == "ActionPlan", dr.text
    return capa_id


async def _latest_stage_id(
    client: AsyncClient, h: dict[str, str], capa_id: str, stage_name: str
) -> str:
    """The latest stage of a type (implement/verify/close return no stages → GET the detail)."""
    detail = (await client.get(f"/api/v1/capas/{capa_id}", headers=h)).json()
    matching = [s for s in detail["stages"] if s["stage"] == stage_name]
    assert matching, f"no {stage_name} stage on {capa_id}: {detail['stages']}"
    return matching[-1]["id"]


async def _link_stage_evidence(
    client: AsyncClient, h: dict[str, str], evidence_record_id: str, stage_id: str
) -> str:
    """Link a record (the CAPA's own record id, the evidence artifact) to a CAPA stage; return its
    link id."""
    r = await client.post(
        f"/api/v1/records/{evidence_record_id}/evidence-links",
        headers=h,
        json={"target_type": "capa_stage", "target_id": stage_id},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_full_capa_close_happy_path(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The real path driving a Major CAPA to Closed: implement → link evidence → verify(effective, a
    DISTINCT actor) → link effectiveness evidence → close. The Verify stage carries a REAL
    signature_event(meaning=verify); this is the production path that satisfies the S-aud-2
    audit-close gate."""
    qm_subj = _subject("qm-cl")
    await _assign_seeded_role(qm_subj, "QMS Owner")
    ha = _auth(token_factory, qm_subj)
    impl_subj = _subject("impl-cl")
    await _grant(impl_subj, _IMPLEMENT_KEYS)
    hp = _auth(token_factory, impl_subj)
    ver_subj = _subject("ver-cl")
    verifier = await _grant(ver_subj, _VERIFY_KEYS)
    hv = _auth(token_factory, ver_subj)

    capa_id = await _drive_to_action_plan(app_client, hp, ha, severity="Major", title="Close OK")

    impl = await app_client.post(
        f"/api/v1/capas/{capa_id}/implement",
        headers=hp,
        json={"content_block": {"actions_done": "retrained operators"}},
    )
    assert impl.status_code == 200 and impl.json()["close_state"] == "Implement", impl.text
    impl_stage = await _latest_stage_id(app_client, hp, capa_id, "Implement")
    await _link_stage_evidence(app_client, hp, capa_id, impl_stage)

    ver = await app_client.post(
        f"/api/v1/capas/{capa_id}/verify",
        headers=hv,
        json={"decision": "effective", "content_block": {"check": "re-audited, no recurrence"}},
    )
    assert ver.status_code == 200 and ver.json()["close_state"] == "Verify", ver.text
    ver_stage = await _latest_stage_id(app_client, hv, capa_id, "Verify")
    await _link_stage_evidence(app_client, hv, capa_id, ver_stage)

    close = await app_client.post(f"/api/v1/capas/{capa_id}/close", headers=hv)
    assert close.status_code == 200, close.text
    assert close.json()["close_state"] == "Closed"

    async with get_sessionmaker()() as s:
        stage = (
            await s.execute(select(CapaStage).where(CapaStage.id == uuid.UUID(ver_stage)))
        ).scalar_one()
        assert stage.signed_event_id is not None
        sig = (
            await s.execute(
                select(SignatureEvent).where(SignatureEvent.id == stage.signed_event_id)
            )
        ).scalar_one()
        assert sig.meaning.value == "verify"
        assert sig.signed_object_type.value == "capa_stage"
        assert sig.signer_user_id == verifier


async def test_close_incomplete_when_effectiveness_evidence_missing(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """An effective verification with NO effectiveness evidence on the Verify stage → 409
    capa_close_incomplete (NOT the loop — the verification is not discarded). Linking the evidence
    then closes."""
    qm_subj = _subject("qm-inc")
    await _assign_seeded_role(qm_subj, "QMS Owner")
    ha = _auth(token_factory, qm_subj)
    impl_subj = _subject("impl-inc")
    await _grant(impl_subj, _IMPLEMENT_KEYS)
    hp = _auth(token_factory, impl_subj)
    ver_subj = _subject("ver-inc")
    await _grant(ver_subj, _VERIFY_KEYS)
    hv = _auth(token_factory, ver_subj)

    capa_id = await _drive_to_action_plan(app_client, hp, ha, severity="Major", title="Incomplete")
    await app_client.post(
        f"/api/v1/capas/{capa_id}/implement", headers=hp, json={"content_block": {"done": "x"}}
    )
    impl_stage = await _latest_stage_id(app_client, hp, capa_id, "Implement")
    await _link_stage_evidence(app_client, hp, capa_id, impl_stage)
    await app_client.post(
        f"/api/v1/capas/{capa_id}/verify",
        headers=hv,
        json={"decision": "effective", "content_block": {"c": "x"}},
    )

    close = await app_client.post(f"/api/v1/capas/{capa_id}/close", headers=hv)
    assert close.status_code == 409, close.text
    assert close.json()["code"] == "capa_close_incomplete"
    assert (await app_client.get(f"/api/v1/capas/{capa_id}", headers=hv)).json()[
        "close_state"
    ] == "Verify"

    ver_stage = await _latest_stage_id(app_client, hv, capa_id, "Verify")
    await _link_stage_evidence(app_client, hv, capa_id, ver_stage)
    close2 = await app_client.post(f"/api/v1/capas/{capa_id}/close", headers=hv)
    assert close2.status_code == 200 and close2.json()["close_state"] == "Closed", close2.text


async def test_sod4_major_implementer_cannot_verify(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """SoD-4 (hard for Major): the actor who recorded the implementation may not verify the CAPA
    (409 sod_self_verify) — even holding capa.verify."""
    qm_subj = _subject("qm-sod")
    await _assign_seeded_role(qm_subj, "QMS Owner")
    ha = _auth(token_factory, qm_subj)
    impl_subj = _subject("impl-sod")
    await _grant(impl_subj, (*_IMPLEMENT_KEYS, "capa.verify", "capa.close"))
    hp = _auth(token_factory, impl_subj)

    capa_id = await _drive_to_action_plan(app_client, hp, ha, severity="Major", title="SoD Major")
    await app_client.post(
        f"/api/v1/capas/{capa_id}/implement", headers=hp, json={"content_block": {"done": "x"}}
    )
    blocked = await app_client.post(
        f"/api/v1/capas/{capa_id}/verify",
        headers=hp,
        json={"decision": "effective", "content_block": {"c": "x"}},
    )
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["code"] == "sod_self_verify"


async def test_sod4_minor_respects_self_verify_flag(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """SoD-4 (soft for Minor): the implementer self-verify is blocked by default, but the per-org
    allow_capa_self_verify flag relaxes it for a Minor CAPA. Flip-and-restore (shared DB)."""
    qm_subj = _subject("qm-min")
    await _assign_seeded_role(qm_subj, "QMS Owner")
    ha = _auth(token_factory, qm_subj)
    impl_subj = _subject("impl-min")
    await _grant(impl_subj, (*_IMPLEMENT_KEYS, "capa.verify", "capa.close"))
    hp = _auth(token_factory, impl_subj)
    admin_subj = _subject("adm-min")
    await _grant(admin_subj, ("config.update",))
    hadmin = _auth(token_factory, admin_subj)

    capa_id = await _drive_to_action_plan(app_client, hp, ha, severity="Minor", title="SoD Minor")
    await app_client.post(
        f"/api/v1/capas/{capa_id}/implement", headers=hp, json={"content_block": {"done": "x"}}
    )
    blocked = await app_client.post(
        f"/api/v1/capas/{capa_id}/verify",
        headers=hp,
        json={"decision": "effective", "content_block": {"c": "x"}},
    )
    assert blocked.status_code == 409 and blocked.json()["code"] == "sod_self_verify", blocked.text

    cfg = await app_client.patch(
        "/api/v1/admin/config", headers=hadmin, json={"allow_capa_self_verify": True}
    )
    assert cfg.status_code == 200 and cfg.json()["allow_capa_self_verify"] is True, cfg.text
    try:
        ok = await app_client.post(
            f"/api/v1/capas/{capa_id}/verify",
            headers=hp,
            json={"decision": "effective", "content_block": {"c": "x"}},
        )
        assert ok.status_code == 200 and ok.json()["close_state"] == "Verify", ok.text
    finally:
        rr = await app_client.patch(
            "/api/v1/admin/config", headers=hadmin, json={"allow_capa_self_verify": False}
        )
        assert rr.status_code == 200 and rr.json()["allow_capa_self_verify"] is False, rr.text


async def test_not_effective_loops_to_root_cause_then_reapprove_and_close(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A not-effective verification loops the CAPA back to RootCause (cycle_marker++); a new plan
    is re-proposed + re-approved, then a fresh implement/verify(effective) closes it (cycle 1)."""
    qm_subj = _subject("qm-loop")
    await _assign_seeded_role(qm_subj, "QMS Owner")
    ha = _auth(token_factory, qm_subj)
    impl_subj = _subject("impl-loop")
    await _grant(impl_subj, _IMPLEMENT_KEYS)
    hp = _auth(token_factory, impl_subj)
    ver_subj = _subject("ver-loop")
    await _grant(ver_subj, _VERIFY_KEYS)
    hv = _auth(token_factory, ver_subj)

    capa_id = await _drive_to_action_plan(app_client, hp, ha, severity="Major", title="Loop")
    # cycle 0: implement + verify(not_effective) + close → loops to RootCause, cycle 1.
    await app_client.post(
        f"/api/v1/capas/{capa_id}/implement", headers=hp, json={"content_block": {"done": "v1"}}
    )
    impl0 = await _latest_stage_id(app_client, hp, capa_id, "Implement")
    await _link_stage_evidence(app_client, hp, capa_id, impl0)
    await app_client.post(
        f"/api/v1/capas/{capa_id}/verify",
        headers=hv,
        json={"decision": "not_effective", "content_block": {"c": "recurred"}},
    )
    looped = await app_client.post(f"/api/v1/capas/{capa_id}/close", headers=hv)
    assert looped.status_code == 200, looped.text
    assert looped.json()["close_state"] == "RootCause"
    assert looped.json()["cycle_marker"] == 1

    # cycle 1: re-propose + re-approve a revised plan, then implement/verify(effective)/close.
    iid = (
        await app_client.post(
            f"/api/v1/capas/{capa_id}/action-plan", headers=hp, json={"content_block": _ACTION_PLAN}
        )
    ).json()["approval_instance"]["id"]
    task_id = await _my_pending_task(app_client, ha, iid)
    dr = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=ha, json={"outcome": "approve"}
    )
    assert dr.status_code == 200 and dr.json()["capa_close_state"] == "ActionPlan", dr.text
    await app_client.post(
        f"/api/v1/capas/{capa_id}/implement", headers=hp, json={"content_block": {"done": "v2"}}
    )
    impl1 = await _latest_stage_id(app_client, hp, capa_id, "Implement")
    await _link_stage_evidence(app_client, hp, capa_id, impl1)
    await app_client.post(
        f"/api/v1/capas/{capa_id}/verify",
        headers=hv,
        json={"decision": "effective", "content_block": {"c": "fixed"}},
    )
    ver1 = await _latest_stage_id(app_client, hv, capa_id, "Verify")
    await _link_stage_evidence(app_client, hv, capa_id, ver1)
    final = await app_client.post(f"/api/v1/capas/{capa_id}/close", headers=hv)
    assert final.status_code == 200 and final.json()["close_state"] == "Closed", final.text
    assert final.json()["cycle_marker"] == 1


async def test_verify_stage_evidence_is_frozen(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Effectiveness evidence on a Verify stage is frozen: an unlink 409s evidence_frozen."""
    qm_subj = _subject("qm-frz")
    await _assign_seeded_role(qm_subj, "QMS Owner")
    ha = _auth(token_factory, qm_subj)
    impl_subj = _subject("impl-frz")
    await _grant(impl_subj, _IMPLEMENT_KEYS)
    hp = _auth(token_factory, impl_subj)
    ver_subj = _subject("ver-frz")
    await _grant(ver_subj, _VERIFY_KEYS)
    hv = _auth(token_factory, ver_subj)

    capa_id = await _drive_to_action_plan(app_client, hp, ha, severity="Major", title="Freeze")
    await app_client.post(
        f"/api/v1/capas/{capa_id}/implement", headers=hp, json={"content_block": {"done": "x"}}
    )
    await app_client.post(
        f"/api/v1/capas/{capa_id}/verify",
        headers=hv,
        json={"decision": "effective", "content_block": {"c": "x"}},
    )
    ver_stage = await _latest_stage_id(app_client, hv, capa_id, "Verify")
    link_id = await _link_stage_evidence(app_client, hv, capa_id, ver_stage)
    frozen = await app_client.delete(
        f"/api/v1/records/{capa_id}/evidence-links/{link_id}", headers=hv
    )
    assert frozen.status_code == 409, frozen.text
    assert frozen.json()["code"] == "evidence_frozen"


async def test_implement_requires_action_plan(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The FSM gate: a freshly-Raised CAPA cannot implement (not at ActionPlan) — 409."""
    impl_subj = _subject("impl-fsm")
    await _grant(impl_subj, _IMPLEMENT_KEYS)
    hp = _auth(token_factory, impl_subj)
    capa_id = (
        await app_client.post("/api/v1/capas", headers=hp, json={"title": "x", "severity": "Minor"})
    ).json()["id"]
    early = await app_client.post(
        f"/api/v1/capas/{capa_id}/implement", headers=hp, json={"content_block": {"done": "x"}}
    )
    assert early.status_code == 409 and early.json()["code"] == "invalid_capa_transition", (
        early.text
    )


async def test_close_requires_verify_first(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The close FSM gate: a CAPA at Implement (not yet verified) cannot close — 409
    invalid_capa_transition (close acts only from Verify)."""
    qm_subj = _subject("qm-cv")
    await _assign_seeded_role(qm_subj, "QMS Owner")
    ha = _auth(token_factory, qm_subj)
    impl_subj = _subject("impl-cv")
    await _grant(impl_subj, (*_IMPLEMENT_KEYS, "capa.close"))
    hp = _auth(token_factory, impl_subj)

    capa_id = await _drive_to_action_plan(
        app_client, hp, ha, severity="Major", title="ClosePremature"
    )
    await app_client.post(
        f"/api/v1/capas/{capa_id}/implement", headers=hp, json={"content_block": {"done": "x"}}
    )
    early = await app_client.post(f"/api/v1/capas/{capa_id}/close", headers=hp)
    assert early.status_code == 409, early.text
    assert early.json()["code"] == "invalid_capa_transition"


async def test_close_incomplete_when_implement_evidence_missing(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """The M4 'implemented-action-with-evidence' clause is reachable: an effective verification with
    NO completion evidence on the Implement stage → 409 capa_close_incomplete naming that clause
    (the Implement STAGE exists by FSM, but its evidence_for_link is optional)."""
    qm_subj = _subject("qm-ie")
    await _assign_seeded_role(qm_subj, "QMS Owner")
    ha = _auth(token_factory, qm_subj)
    impl_subj = _subject("impl-ie")
    await _grant(impl_subj, _IMPLEMENT_KEYS)
    hp = _auth(token_factory, impl_subj)
    ver_subj = _subject("ver-ie")
    await _grant(ver_subj, _VERIFY_KEYS)
    hv = _auth(token_factory, ver_subj)

    capa_id = await _drive_to_action_plan(app_client, hp, ha, severity="Major", title="NoImplEvid")
    # Implement but DO NOT link completion evidence to the Implement stage.
    await app_client.post(
        f"/api/v1/capas/{capa_id}/implement", headers=hp, json={"content_block": {"done": "x"}}
    )
    await app_client.post(
        f"/api/v1/capas/{capa_id}/verify",
        headers=hv,
        json={"decision": "effective", "content_block": {"c": "x"}},
    )
    # Link ONLY effectiveness evidence (the Verify stage) → implemented-action-with-evidence fails.
    ver_stage = await _latest_stage_id(app_client, hv, capa_id, "Verify")
    await _link_stage_evidence(app_client, hv, capa_id, ver_stage)
    close = await app_client.post(f"/api/v1/capas/{capa_id}/close", headers=hv)
    assert close.status_code == 409, close.text
    assert close.json()["code"] == "capa_close_incomplete"
    assert "implemented_action_with_evidence" in close.json()["title"]


async def test_capa_list_and_detail_carry_title_created_at_raised_by(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("capa")
    await _grant(subject, _CAPA_KEYS)
    h = _auth(token_factory, subject)

    raised = (
        await app_client.post(
            "/api/v1/capas",
            headers=h,
            json={"title": "Torque wrench miscalibration", "severity": "Minor"},
        )
    ).json()
    capa_id = raised["id"]
    # the create response itself carries the metadata (every single-CAPA endpoint runs through
    # _capa_full now, so a write response never returns a null title for a CAPA that has one)
    assert raised["title"] == "Torque wrench miscalibration"
    assert raised["created_at"] is not None

    # list row carries title + created_at (raised_by is detail-only → null on the list row)
    listing = (await app_client.get("/api/v1/capas", headers=h)).json()
    row = next(r for r in listing["data"] if r["id"] == capa_id)
    assert row["title"] == "Torque wrench miscalibration"
    assert row["created_at"] is not None
    assert row["raised_by"] is None

    # detail carries title + created_at + raised_by (the Raised stage's actor)
    detail = (await app_client.get(f"/api/v1/capas/{capa_id}", headers=h)).json()
    assert detail["title"] == "Torque wrench miscalibration"
    assert detail["created_at"] is not None
    assert detail["raised_by"] == detail["stages"][0]["created_by"]


# --- S-web-7b: thin read-enrichments (stage evidence_links + /approval) ----------------------


async def test_capa_detail_stage_carries_evidence_links(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("capaev")
    await _grant(subject, _CAPA_KEYS)
    h = _auth(token_factory, subject)
    rr = await app_client.post(
        "/api/v1/capas", headers=h, json={"title": "Evidence shape", "severity": "Minor"}
    )
    assert rr.status_code == 201, rr.text
    raised = rr.json()
    detail = (await app_client.get(f"/api/v1/capas/{raised['id']}", headers=h)).json()
    # every stage now carries an evidence_links array (empty until a record is linked)
    assert all("evidence_links" in s for s in detail["stages"])
    assert detail["stages"][0]["evidence_links"] == []


async def test_capa_approval_read_null_then_pending(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("capaap")
    await _grant(subject, (*_CAPA_KEYS, "capa.record_rca", "capa.plan_action"))
    h = _auth(token_factory, subject)
    raised = (
        await app_client.post(
            "/api/v1/capas", headers=h, json={"title": "Approval read", "severity": "Minor"}
        )
    ).json()
    cid = raised["id"]
    # no cycle yet → null
    assert (await app_client.get(f"/api/v1/capas/{cid}/approval", headers=h)).json() is None
    # walk to RootCause then propose an action plan → a non-null approval with the proposed plan
    await app_client.post(
        f"/api/v1/capas/{cid}/containment", headers=h, json={"content_block": {"correction": "x"}}
    )
    await app_client.post(
        f"/api/v1/capas/{cid}/root-cause", headers=h, json={"content_block": {"root_cause": "y"}}
    )
    await app_client.post(
        f"/api/v1/capas/{cid}/action-plan",
        headers=h,
        json={"content_block": {"action_items": ["fix it"]}},
    )
    approval = (await app_client.get(f"/api/v1/capas/{cid}/approval", headers=h)).json()
    assert approval is not None
    assert approval["instance"]["subject_id"] == cid
    assert approval["proposed_action_plan"] == {"action_items": ["fix it"]}


# --- S-capa-overdue: target_completion_date defaulted at raise --------------------------------


async def test_target_completion_date_defaulted_at_raise(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A raised CAPA carries target_completion_date = raise_date + severity offset (30/60/90 d).

    Asserts on the ORM row directly (the HTTP serializer does not yet expose the field).
    Resolves the org tz the same way the service does to get the raise_date in the right frame.

    Tests BOTH Critical (+30) and Major (+60) so a hardcoded-Critical mutation at the
    build_capa/spawn call site is mutation-distinguishing (Fix 3 severity-threading coverage).
    """
    subject = _subject("capa-tgt")
    await _grant(subject, _CAPA_KEYS)
    h = _auth(token_factory, subject)

    # Resolve org tz via the same path the service uses (calendar.tz → org.tz → env → UTC).
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        org_id = user.org_id
        target_tz = await resolve_org_tz(s, org_id)

    # --- Critical (+30 d) ---
    raise_date_before = datetime.datetime.now(target_tz).date()
    r = await app_client.post(
        "/api/v1/capas",
        headers=h,
        json={"title": "TCD test Critical", "severity": "Critical", "problem": "test"},
    )
    raise_date_after = datetime.datetime.now(target_tz).date()
    assert r.status_code == 201, r.text
    capa_id = uuid.UUID(r.json()["id"])

    async with get_sessionmaker()() as s:
        capa = (await s.execute(select(Capa).where(Capa.id == capa_id))).scalar_one()
        tcd = capa.target_completion_date

    assert tcd is not None
    # Permit raise_date to straddle midnight (normally instantaneous; makes the test robust).
    expected = {
        default_target_date(NcSeverity.Critical, raise_date_before),
        default_target_date(NcSeverity.Critical, raise_date_after),
    }
    assert tcd in expected, f"Critical: expected one of {expected}, got {tcd}"

    # --- Major (+60 d) — mutation-distinguishing: hardcoded-Critical returns +30, not +60 ---
    raise_date_before = datetime.datetime.now(target_tz).date()
    r2 = await app_client.post(
        "/api/v1/capas",
        headers=h,
        json={"title": "TCD test Major", "severity": "Major", "problem": "test"},
    )
    raise_date_after = datetime.datetime.now(target_tz).date()
    assert r2.status_code == 201, r2.text
    capa_id_major = uuid.UUID(r2.json()["id"])

    async with get_sessionmaker()() as s:
        capa_major = (await s.execute(select(Capa).where(Capa.id == capa_id_major))).scalar_one()
        tcd_major = capa_major.target_completion_date

    assert tcd_major is not None
    expected_major = {
        default_target_date(NcSeverity.Major, raise_date_before),
        default_target_date(NcSeverity.Major, raise_date_after),
    }
    assert tcd_major in expected_major, f"Major: expected one of {expected_major}, got {tcd_major}"
