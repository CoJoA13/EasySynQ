"""S10 integration proofs — Postgres-FTS search + type-ahead suggest (doc 13 §2, doc 15 §8.14).

Covers: metadata-plane FTS finds an Effective doc by a title token; non-Effective docs are excluded
(doc 13's "Effective only" default — no draft-title leak to a document.read holder); results are
post-filtered by document.read (filter-not-403 — a caller who may read nothing gets 200 +
hidden_by_scope, the "N hidden by your access scope" footer); and the prefix suggest.
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
from .test_vault import _auth, _ensure_user

pytestmark = pytest.mark.integration


@pytest.fixture
def subj() -> SimpleNamespace:
    salt = uuid.uuid4().hex[:10]
    return SimpleNamespace(a=f"kc-a-{salt}", b=f"kc-b-{salt}")


async def _ensure(subject: str) -> None:
    """Create the app_user (zero grants) so the bearer authenticates but reads nothing."""
    async with get_sessionmaker()() as s:
        await _ensure_user(s, subject)
        await s.commit()


async def _create_titled(client: AsyncClient, h: dict[str, str], type_id: str, title: str) -> dict:
    r = await client.post(
        "/api/v1/documents",
        headers=h,
        json={"title": title, "document_type_id": type_id, "area_code": "PUR"},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _effective_titled(
    app_client: AsyncClient, ha: dict[str, str], hb: dict[str, str], title: str
) -> dict:
    """Drive a doc to Effective (author=a, approver+releaser=b), then retitle it (the title lives on
    documented_information, so search picks up the new value via the live FTS expression)."""
    eff = await s5.drive_to_effective(app_client, ha, hb, hb, await s5.type_id("SOP"), b"search")
    r = await app_client.patch(f"/api/v1/documents/{eff['id']}", headers=ha, json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()


async def test_search_finds_effective_by_title_token(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    await s5.set_approver_release(await s5.default_org_id(), True)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    token = uuid.uuid4().hex[:8]
    doc = await _effective_titled(app_client, ha, hb, f"Zephyr {token} Procedure")

    r = await app_client.get(f"/api/v1/search?q={token}", headers=ha)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["hidden_by_scope"] == 0
    hit = next(h for h in body["results"] if h["id"] == doc["id"])
    assert hit["type"] == "document"
    assert hit["identifier"] == doc["identifier"]
    assert hit["current_state"] == "Effective"
    assert set(hit) >= {
        "id",
        "identifier",
        "title",
        "current_state",
        "clause_refs",
        "snippet",
        "rank",
    }
    assert isinstance(hit["clause_refs"], list)  # drive_to_effective mapped a clause


async def test_search_excludes_non_effective(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """A Draft doc is excluded from search/suggest for a document.read holder (doc 13 'Effective
    only' default) — and it's a STATE exclusion, not a scope-hide (hidden_by_scope stays 0)."""
    await s5.grant_lifecycle(subj.a)
    ha = _auth(token_factory, subj.a)
    token = uuid.uuid4().hex[:8]
    await _create_titled(app_client, ha, await s5.type_id("SOP"), f"DraftOnly {token}")  # Draft

    r = await app_client.get(f"/api/v1/search?q={token}", headers=ha)
    assert r.status_code == 200, r.text
    assert r.json()["results"] == []
    assert r.json()["hidden_by_scope"] == 0  # excluded by state, not by access scope

    sg = await app_client.get("/api/v1/search/suggest?q=DraftOnly", headers=ha)
    assert all(token not in s["title"] for s in sg.json()["suggestions"])


async def test_search_filters_unreadable_results(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """A caller lacking document.read sees no rows but a non-zero hidden_by_scope (filter)."""
    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    await s5.set_approver_release(await s5.default_org_id(), True)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    token = uuid.uuid4().hex[:8]
    await _effective_titled(app_client, ha, hb, f"Quokka {token} Spec")

    await _ensure(f"kc-noperm-{token}")  # a user with zero grants
    hn = _auth(token_factory, f"kc-noperm-{token}")
    r = await app_client.get(f"/api/v1/search?q={token}", headers=hn)
    assert r.status_code == 200, r.text  # NEVER 403 — a list surface filters
    body = r.json()
    assert body["results"] == []
    assert body["hidden_by_scope"] >= 1


async def test_suggest_prefix(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    await s5.set_approver_release(await s5.default_org_id(), True)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    token = uuid.uuid4().hex[:8]
    doc = await _effective_titled(app_client, ha, hb, f"Z6prefix{token} Manual")

    r = await app_client.get(f"/api/v1/search/suggest?q=Z6prefix{token}", headers=ha)
    assert r.status_code == 200, r.text
    ids = [s["id"] for s in r.json()["suggestions"]]
    assert doc["id"] in ids


async def _add_override(
    subject: str,
    permission_key: str,
    effect: Effect,
    level: ScopeLevel,
    *,
    selector: dict[str, object] | None = None,
) -> None:
    """Seed a scoped permission override for ``subject`` (the register/test_authz precedent) — used
    here to seed a FRAMEWORK-scoped ``document.read`` DENY."""
    async with get_sessionmaker()() as s:
        user = await _ensure_user(s, subject)
        perm = (
            await s.execute(select(Permission).where(Permission.key == permission_key))
        ).scalar_one()
        scope = Scope(org_id=user.org_id, level=level, selector=selector)
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


async def test_framework_scoped_document_read_deny_hides_search_hit(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """#333: a caller with a broad SYSTEM document.read ALLOW + a FRAMEWORK-scoped document.read
    DENY must NOT see the framework-denied Effective doc in /search. The per-hit filter now sets the
    hit's framework_id (from the indexer projection), so the DENY wins and the doc is counted in
    hidden_by_scope. Pre-#333 the hit omitted framework_id, the DENY was dropped, and it leaked."""
    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    await s5.set_approver_release(await s5.default_org_id(), True)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    token = uuid.uuid4().hex[:12]
    doc = await _effective_titled(app_client, ha, hb, f"FwDeny {token}")

    denier = f"kc-fwdeny-{uuid.uuid4().hex[:8]}"
    await _add_override(denier, "document.read", Effect.ALLOW, ScopeLevel.SYSTEM)
    await _add_override(
        denier,
        "document.read",
        Effect.DENY,
        ScopeLevel.FRAMEWORK,
        selector={"framework_id": doc["framework_id"]},
    )
    hc = _auth(token_factory, denier)

    body = (await app_client.get(f"/api/v1/search?q={token}", headers=hc)).json()
    assert doc["id"] not in {h["id"] for h in body["results"]}  # framework DENY wins
    assert body["hidden_by_scope"] >= 1  # counted as scope-hidden, not state-excluded

    # The same completion applies on the suggest path (prefix over identifier/title).
    sg = (await app_client.get("/api/v1/search/suggest?q=FwDeny", headers=hc)).json()
    assert doc["id"] not in {s["id"] for s in sg["suggestions"]}
