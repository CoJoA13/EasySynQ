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
from httpx import ASGITransport, AsyncClient
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
    app_client: AsyncClient,
    ha: dict[str, str],
    hb: dict[str, str],
    title: str,
    *,
    document_type_id: str | None = None,
) -> dict:
    """Drive a doc to Effective (author=a, approver+releaser=b), then retitle it (the title lives on
    documented_information, so search picks up the new value via the live FTS expression)."""
    type_id = document_type_id or await s5.type_id("SOP")
    eff = await s5.drive_to_effective(app_client, ha, hb, hb, type_id, b"search")
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
    predicates: dict[str, object] | None = None,
) -> None:
    """Seed a scoped permission override for ``subject`` (the register/test_authz precedent) — used
    here to seed a FRAMEWORK-scoped ``document.read`` DENY and an ip_allow-predicated ALLOW."""
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


async def test_concrete_type_deny_is_consistent_across_document_read_surfaces(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """R60/#345: exact type-code DENY hides OBJ while another L1 type remains readable.

    The equal document level proves the distinction comes from ``concrete_type`` on the canonical
    detail/list builder and both search candidate projections, not from the mandatory level match.
    """
    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    await s5.set_approver_release(await s5.default_org_id(), True)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    token = uuid.uuid4().hex[:10]
    objective_type_id = await s5.type_id("OBJ")
    review_type_id = await s5.type_id("MR")
    title_prefix = f"TypeGate {token}"
    objective = await _effective_titled(
        app_client,
        ha,
        hb,
        f"{title_prefix} objective",
        document_type_id=objective_type_id,
    )
    alternate = await _effective_titled(
        app_client,
        ha,
        hb,
        f"{title_prefix} review",
        document_type_id=review_type_id,
    )

    denier = f"kc-typedeny-{uuid.uuid4().hex[:8]}"
    await _add_override(denier, "document.read", Effect.ALLOW, ScopeLevel.SYSTEM)
    await _add_override(
        denier,
        "document.read",
        Effect.DENY,
        ScopeLevel.DOC_CLASS,
        selector={"document_level": "L1_POLICY", "concrete_type": "OBJ"},
    )
    hc = _auth(token_factory, denier)

    denied = await app_client.get(f"/api/v1/documents/{objective['id']}", headers=hc)
    assert denied.status_code == 403, denied.text
    allowed = await app_client.get(f"/api/v1/documents/{alternate['id']}", headers=hc)
    assert allowed.status_code == 200, allowed.text

    listed = await app_client.get("/api/v1/documents", headers=hc)
    assert listed.status_code == 200, listed.text
    listed_ids = {row["id"] for row in listed.json()["data"]}
    assert objective["id"] not in listed_ids
    assert alternate["id"] in listed_ids

    searched = await app_client.get(f"/api/v1/search?q={token}", headers=hc)
    assert searched.status_code == 200, searched.text
    search_ids = {hit["id"] for hit in searched.json()["results"]}
    assert objective["id"] not in search_ids
    assert alternate["id"] in search_ids
    assert searched.json()["hidden_by_scope"] >= 1

    suggested = await app_client.get(
        "/api/v1/search/suggest", headers=hc, params={"q": title_prefix}
    )
    assert suggested.status_code == 200, suggested.text
    suggestion_ids = {item["id"] for item in suggested.json()["suggestions"]}
    assert objective["id"] not in suggestion_ids
    assert alternate["id"] in suggestion_ids


async def test_ip_allow_predicated_read_matches_on_row_filters(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    app_under_test: object,
) -> None:
    """[Audit U1] The document.read row filters (/search, /search/suggest, GET /documents) thread
    the live source_ip into their RequestContext, so an ip_allow-predicated grant evaluates
    exactly as the detail-gate PEP does. Before the fix the filters built ctx with
    source_ip=None, so the fail-closed predicate silently never matched — an ip-bound reader
    saw NOTHING on any list surface. The wrong-IP twin proves the value is the real peer, not
    a hardcoded pass."""
    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    await s5.set_approver_release(await s5.default_org_id(), True)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    token = uuid.uuid4().hex[:8]
    doc = await _effective_titled(app_client, ha, hb, f"Ipbound{token} Spec")

    # The ASGI test transport presents client=("127.0.0.1", …) — the request's real peer.
    matching = f"kc-ipok-{token}"
    await _add_override(
        matching,
        "document.read",
        Effect.ALLOW,
        ScopeLevel.SYSTEM,
        predicates={"ip_allow": ["127.0.0.1"]},
    )
    hm = _auth(token_factory, matching)
    r = await app_client.get(f"/api/v1/search?q=Ipbound{token}", headers=hm)
    assert r.status_code == 200, r.text
    assert doc["id"] in [h_["id"] for h_ in r.json()["results"]]
    r = await app_client.get(f"/api/v1/search/suggest?q=Ipbound{token}", headers=hm)
    assert r.status_code == 200, r.text
    assert doc["id"] in [s_["id"] for s_ in r.json()["suggestions"]]
    r = await app_client.get("/api/v1/documents", headers=hm)
    assert r.status_code == 200, r.text
    assert doc["id"] in [d["id"] for d in r.json()["data"]]

    # An ip-bound grant for a DIFFERENT address must keep everything hidden (fail-closed).
    other = f"kc-ipwrong-{token}"
    await _add_override(
        other,
        "document.read",
        Effect.ALLOW,
        ScopeLevel.SYSTEM,
        predicates={"ip_allow": ["203.0.113.9"]},
    )
    ho = _auth(token_factory, other)
    r = await app_client.get(f"/api/v1/search?q=Ipbound{token}", headers=ho)
    assert r.status_code == 200 and r.json()["results"] == []
    r = await app_client.get(f"/api/v1/search/suggest?q=Ipbound{token}", headers=ho)
    assert r.status_code == 200 and r.json()["suggestions"] == []
    r = await app_client.get("/api/v1/documents", headers=ho)
    assert r.status_code == 200, r.text
    assert doc["id"] not in [d["id"] for d in r.json()["data"]]

    # The threaded value follows the request PEER, not any constant (false-pass-hunter pin: a
    # hardcoded "127.0.0.1" would pass every leg above). Through a transport presenting
    # 203.0.113.9 the roles invert: the 203-bound reader sees the doc, the 127-bound one loses it.
    alt_transport = ASGITransport(app=app_under_test, client=("203.0.113.9", 12345))
    async with AsyncClient(transport=alt_transport, base_url="http://test") as alt:
        r = await alt.get(f"/api/v1/search?q=Ipbound{token}", headers=ho)
        assert doc["id"] in [h_["id"] for h_ in r.json()["results"]]
        r = await alt.get("/api/v1/documents", headers=ho)
        assert doc["id"] in [d["id"] for d in r.json()["data"]]
        r = await alt.get(f"/api/v1/search?q=Ipbound{token}", headers=hm)
        assert r.json()["results"] == []


async def test_ip_allow_follows_the_forwarded_client_only_from_a_trusted_peer(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    app_under_test: object,
) -> None:
    """[S-proxy-trust] An ip_allow grant must narrow to the real client, and only a peer the
    operator declared a proxy may say who that is.

    Behind the shipped Caddy every request's socket peer is the proxy, so before this change the
    predicate compared an administrator's allowlist against the proxy's own container address:
    it never matched anything, which is a universal denial dressed up as a narrowing rule. The
    forgery direction is the same bug mirrored — believing X-Forwarded-For from a caller that is
    not a proxy would let that caller name its own address.

    The two legs below are the same request differing only in who the peer is, and they must
    disagree. Both run against the SHIPPED TRUSTED_PROXY_CIDRS default (loopback is in it, which
    is what the ASGI transport presents) rather than a test-only override, so a default that
    stopped covering the deployment topology would surface here.
    """
    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    await s5.set_approver_release(await s5.default_org_id(), True)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    token = uuid.uuid4().hex[:8]
    doc = await _effective_titled(app_client, ha, hb, f"Proxied{token} Spec")

    forwarded = f"kc-fwd-{token}"
    await _add_override(
        forwarded,
        "document.read",
        Effect.ALLOW,
        ScopeLevel.SYSTEM,
        predicates={"ip_allow": ["203.0.113.9"]},
    )
    hf = _auth(token_factory, forwarded)

    peerbound = f"kc-peer-{token}"
    await _add_override(
        peerbound,
        "document.read",
        Effect.ALLOW,
        ScopeLevel.SYSTEM,
        predicates={"ip_allow": ["127.0.0.1"]},
    )
    hp = _auth(token_factory, peerbound)

    # Leg 1 — a TRUSTED peer (loopback, per the shipped default) forwards a client address.
    # The grant bound to the forwarded address now matches; the one bound to the proxy's own
    # address must not, because the proxy is no longer who the request is attributed to.
    xff = {"x-forwarded-for": "203.0.113.9"}
    r = await app_client.get(f"/api/v1/search?q=Proxied{token}", headers={**hf, **xff})
    assert r.status_code == 200, r.text
    assert doc["id"] in [h_["id"] for h_ in r.json()["results"]], (
        "an ip_allow grant for the forwarded client must match behind a trusted proxy"
    )
    r = await app_client.get(f"/api/v1/search?q=Proxied{token}", headers={**hp, **xff})
    assert r.json()["results"] == [], (
        "the proxy's own address must stop being the attributed client once it forwards one"
    )

    # Leg 2 — the SAME header from an UNTRUSTED peer is not evidence. 203.0.113.9 is outside the
    # shipped allowlist, so a caller there cannot promote itself to 198.51.100.9; it stays 203.
    spoofed = f"kc-spoof-{token}"
    await _add_override(
        spoofed,
        "document.read",
        Effect.ALLOW,
        ScopeLevel.SYSTEM,
        predicates={"ip_allow": ["198.51.100.9"]},
    )
    hs = _auth(token_factory, spoofed)
    forge = {"x-forwarded-for": "198.51.100.9"}
    alt_transport = ASGITransport(app=app_under_test, client=("203.0.113.9", 12345))
    async with AsyncClient(transport=alt_transport, base_url="http://test") as alt:
        r = await alt.get(f"/api/v1/search?q=Proxied{token}", headers={**hs, **forge})
        assert r.json()["results"] == [], (
            "X-Forwarded-For from an untrusted peer must not choose the attributed address"
        )
        # Anti-vacuity: the same caller, same header, judged against its REAL peer, does see it.
        r = await alt.get(f"/api/v1/search?q=Proxied{token}", headers={**hf, **forge})
        assert doc["id"] in [h_["id"] for h_ in r.json()["results"]], (
            "the untrusted peer's own address is still what the predicate compares"
        )
