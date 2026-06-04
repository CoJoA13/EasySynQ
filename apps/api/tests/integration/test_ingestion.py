"""S-ing-1 integration: the end-to-end scan against real PG/MinIO/Redis, the ``import.*``
execute/review
gate split, deny-by-default, the source-root lock (duplicate-active 409), the setup latch (423), and
org isolation (404, never a leak). The scan is driven in-process (no Celery worker in tests), the
``services.packs`` build precedent."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.config import get_settings
from easysynq_api.db.models.app_user import AppUser, UserStatus
from easysynq_api.db.models.authz_grant import PermissionOverride
from easysynq_api.db.models.organization import Organization
from easysynq_api.db.models.permission import Permission
from easysynq_api.db.models.scope import Scope
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.domain.authz.types import Effect, ScopeLevel
from easysynq_api.services.ingestion.service import run_scan

from .test_authz import _assign_role, _auth
from .test_records import _grant, _subject


@pytest.fixture(autouse=True)
def _stub_scan_enqueue(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Celery app binds its broker to the default localhost Redis at import time (not the
    testcontainer), and the shared-DB contract is "never trigger real Celery/Beat" — so stub the
    create-path enqueue; every test drives ``run_scan`` directly (the packs ``build`` precedent)."""
    from easysynq_api.tasks.ingestion import scan_source

    monkeypatch.setattr(scan_source, "delay", lambda *a, **k: None)


def _seed_source() -> Path:
    """Seed the per-test read-only source root with a messy mix; return its path."""
    root = Path(get_settings().import_source_root)
    (root / "Procedure.docx").write_text("a controlled purchasing procedure")
    (root / "Thumbs.db").write_text("junk")  # excluded (junk)
    (root / "draft.tmp").write_text("scratch")  # quarantined (temp_backup)
    (root / "empty.txt").write_text("")  # excluded (empty)
    sub = root / "sub"
    sub.mkdir(exist_ok=True)
    (sub / "copy1.txt").write_text("identical evidence bytes")
    (sub / "copy2.txt").write_text("identical evidence bytes")  # exact dup of copy1
    return root


async def test_scan_happy_path(app_client: AsyncClient, token_factory: Callable[..., str]) -> None:
    admin = _subject("avery")
    await _assign_role(admin, "System Administrator")
    h = _auth(token_factory, admin)
    _seed_source()

    created = await app_client.post("/api/v1/admin/imports", headers=h, json={"source_root": "."})
    assert created.status_code == 202, created.text
    run_id = created.json()["id"]
    assert created.json()["status"] == "Created"

    # Drive the scan directly (no Celery worker runs in the test).
    async with get_sessionmaker()() as session:
        await run_scan(session, uuid.UUID(run_id))

    got = (await app_client.get(f"/api/v1/admin/imports/{run_id}", headers=h)).json()
    assert got["status"] == "Scanned"
    counts = got["counts"]
    assert counts["total_files"] == 6
    assert counts["included"] == 3  # Procedure + copy1 + copy2
    assert counts["excluded"] == 2  # Thumbs.db (junk) + empty.txt (empty)
    assert counts["quarantine"] == 1  # draft.tmp (temp_backup)
    assert counts["exact_dup_clusters"] == 1  # copy1 == copy2
    assert counts["exact_dup_files"] == 2

    files = (await app_client.get(f"/api/v1/admin/imports/{run_id}/files", headers=h)).json()[
        "files"
    ]
    by_name = {f["filename"]: f for f in files}
    assert by_name["Thumbs.db"]["scan_flags"]["disposition"] == "excluded"
    assert by_name["draft.tmp"]["scan_flags"]["reason"] == "temp_backup"
    assert by_name["empty.txt"]["scan_flags"]["reason"] == "empty"
    assert by_name["Procedure.docx"]["included_candidate"] is True
    assert by_name["Procedure.docx"]["sha256"]  # included → content-addressed + staged
    assert by_name["Procedure.docx"]["staged_blob_uri"].startswith("s3://import-staging/")
    # the two identical copies dedup to one content address
    assert by_name["copy1.txt"]["sha256"] == by_name["copy2.txt"]["sha256"]
    assert by_name["Thumbs.db"]["sha256"] is None  # excluded → never hashed

    # cancel on a terminal run → 409
    cancel = await app_client.post(f"/api/v1/admin/imports/{run_id}/cancel", headers=h)
    assert cancel.status_code == 409


async def test_gate_split_execute_vs_review(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    # review-only → can read, cannot create/cancel
    reviewer = _subject("mara")
    await _grant(reviewer, ("import.review",))
    hr = _auth(token_factory, reviewer)
    assert (await app_client.get("/api/v1/admin/imports", headers=hr)).status_code == 200
    denied = await app_client.post("/api/v1/admin/imports", headers=hr, json={"source_root": "."})
    assert denied.status_code == 403

    # execute-only → can reach create/cancel, cannot read
    operator = _subject("priya")
    await _grant(operator, ("import.execute",))
    he = _auth(token_factory, operator)
    assert (await app_client.get("/api/v1/admin/imports", headers=he)).status_code == 403
    # execute passes the gate → a missing run is 404 (not 403), proving the gate is satisfied
    missing = await app_client.post(f"/api/v1/admin/imports/{uuid.uuid4()}/cancel", headers=he)
    assert missing.status_code == 404


async def test_deny_by_default(app_client: AsyncClient, token_factory: Callable[..., str]) -> None:
    h = _auth(token_factory, _subject("nobody"))
    assert (await app_client.get("/api/v1/admin/imports", headers=h)).status_code == 403
    post = await app_client.post("/api/v1/admin/imports", headers=h, json={"source_root": "."})
    assert post.status_code == 403


async def test_duplicate_active_run_conflict(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    admin = _subject("avery")
    await _assign_role(admin, "System Administrator")
    h = _auth(token_factory, admin)
    _seed_source()
    first = await app_client.post("/api/v1/admin/imports", headers=h, json={"source_root": "."})
    assert first.status_code == 202
    # a 2nd run for the same root while the first holds the lock → 409 + the active run id
    second = await app_client.post("/api/v1/admin/imports", headers=h, json={"source_root": "."})
    assert second.status_code == 409
    assert second.json()["code"] == "conflict"
    assert second.json()["active_run_id"] == first.json()["id"]


async def test_latch_blocks_until_operational(
    app_client: AsyncClient, token_factory: Callable[..., str], dsns: dict[str, str]
) -> None:
    admin = _subject("avery")
    await _assign_role(admin, "System Administrator")
    h = _auth(token_factory, admin)
    engine = sa.create_engine(dsns["owner"])
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("UPDATE system_config SET setup_state='UNINITIALIZED'"))
        blocked = await app_client.get("/api/v1/admin/imports", headers=h)
        assert blocked.status_code == 423
        assert blocked.json()["code"] == "setup_incomplete"
    finally:
        with engine.begin() as conn:
            conn.execute(sa.text("UPDATE system_config SET setup_state='OPERATIONAL'"))
        engine.dispose()


async def test_org_isolation_returns_404(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    admin = _subject("avery")
    await _assign_role(admin, "System Administrator")
    h = _auth(token_factory, admin)
    _seed_source()
    run_id = (
        await app_client.post("/api/v1/admin/imports", headers=h, json={"source_root": "."})
    ).json()["id"]

    # a reviewer in a DIFFERENT org must not see org A's run — 404, never a 403/leak
    other = _subject("orgb-reviewer")
    async with get_sessionmaker()() as session:
        org_b = Organization(
            legal_name="Org B Ltd", short_code=f"ORGB{uuid.uuid4().hex[:6].upper()}"
        )
        session.add(org_b)
        await session.flush()
        user_b = AppUser(
            org_id=org_b.id,
            keycloak_subject=other,
            display_name=other,
            status=UserStatus.ACTIVE,
        )
        session.add(user_b)
        await session.flush()
        perm = (
            await session.execute(select(Permission).where(Permission.key == "import.review"))
        ).scalar_one()
        scope = Scope(org_id=org_b.id, level=ScopeLevel.SYSTEM)
        session.add(scope)
        await session.flush()
        session.add(
            PermissionOverride(
                org_id=org_b.id,
                user_id=user_b.id,
                permission_id=perm.id,
                effect=Effect.ALLOW,
                scope_id=scope.id,
            )
        )
        await session.commit()

    hb = _auth(token_factory, other)
    cross = await app_client.get(f"/api/v1/admin/imports/{run_id}", headers=hb)
    assert cross.status_code == 404
