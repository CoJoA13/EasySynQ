"""S-capa-1 integration proofs — CAPA core + intake (capas / complaints / ncrs) over HTTP against
testcontainer Postgres + MinIO + Redis.

The seeded ``capa.*`` / ``ncr.*`` keys ride the Process-Owner / QMS-Owner / Internal-Auditor roles
(PROCESS-scoped placeholders), but the test actor has no role assignment, so each test grants the
keys it needs via SYSTEM-scope overrides (the ``test_audits`` precedent; a SYSTEM grant matches any
resource context). Assertions are scoped to **this run's own** capa / complaint / ncr ids — the
integration suite shares one session DB across files, so absolute counts are never asserted.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from easysynq_api.db.models._audit_enums import AuditObjectType, EventType
from easysynq_api.db.models.app_user import AppUser
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.authz_grant import PermissionOverride
from easysynq_api.db.models.capa_stage import CapaStage
from easysynq_api.db.models.permission import Permission
from easysynq_api.db.models.scope import Scope
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.domain.authz.types import Effect, ScopeLevel
from easysynq_api.problems import ProblemException
from easysynq_api.services.capa import advance_capa_to_containment

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


async def test_capa_read_requires_grant(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    subject = _subject("capa-nogrant")
    async with get_sessionmaker()() as s:
        await _ensure_user(s, subject)
        await s.commit()
    h = _auth(token_factory, subject)
    assert (await app_client.get("/api/v1/capas", headers=h)).status_code == 403
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
