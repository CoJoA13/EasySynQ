"""S5 separation-of-duties proofs (doc 07 §7.1, doc 18 §7 S5 row), end-to-end over HTTP.

[PROOF] **SoD-1** — the author of a version cannot approve it (→ 403 ``sod_violation`` with
``conflicting_duty``). **SoD-2** — the author can never release their own edit (even with
``allow_approver_release`` on); the sole approver may release only when the flag is on; a third
party always may. **SoD-3** — an Internal-Auditor-only principal is excluded from approve (RBAC).
Each is evaluated against the immutable version + signature history, not a current field.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from types import SimpleNamespace

import pytest
from httpx import AsyncClient

from . import s5_helpers as s5
from .test_vault import _auth, _checkin, _create, _upload

pytestmark = pytest.mark.integration


@pytest.fixture
def subj() -> SimpleNamespace:
    salt = uuid.uuid4().hex[:10]
    return SimpleNamespace(a=f"kc-author-{salt}", b=f"kc-approver-{salt}", c=f"kc-releaser-{salt}")


async def _to_in_review(client: AsyncClient, h: dict[str, str], type_id: str) -> str:
    did = (await _create(client, h, type_id))["id"]
    await client.post(f"/api/v1/documents/{did}/checkout", headers=h)
    sha = await _upload(client, h, did, f"sod-{did}".encode())
    await _checkin(client, h, did, sha, change_reason="v1", change_significance="MAJOR")
    assert (
        await client.post(f"/api/v1/documents/{did}/submit-review", headers=h)
    ).status_code == 200
    return did


# --- SoD-1: no self-approval (the headline) --------------------------------------------


async def test_sod1_author_cannot_approve_own_version(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """[PROOF] SoD-1 → 403 sod_violation. ``a`` holds document.approve (override) yet, being the
    version's author, is denied — and the body names the conflicting duty."""
    await s5.grant_lifecycle(subj.a)
    ha = _auth(token_factory, subj.a)
    did = await _to_in_review(app_client, ha, await s5.type_id("SOP"))
    task_id = await s5.task_for_doc(did)

    dec = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=ha, json={"outcome": "approve"}
    )
    assert dec.status_code == 403, dec.text
    body = dec.json()
    assert body["code"] == "sod_violation"
    assert body["conflicting_duty"]["duty_b"] == {"permission": "document.approve"}


# --- SoD-2: no self-release; approver-release behind the flag ---------------------------


async def test_sod2_author_release_and_approver_block(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """flag OFF: the author (a) and the sole approver (b) are both denied release; a third party
    (c) succeeds. (Denials first — they don't change state — then the third-party release.)"""
    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    await s5.grant_lifecycle(subj.c)
    await s5.set_approver_release(await s5.default_org_id(), False)
    ha, hb, hc = (
        _auth(token_factory, subj.a),
        _auth(token_factory, subj.b),
        _auth(token_factory, subj.c),
    )
    did = await s5.drive_to_approved(app_client, ha, hb, await s5.type_id("SOP"), b"sod2-off")

    author_release = await app_client.post(f"/api/v1/documents/{did}/release", headers=ha, json={})
    assert author_release.status_code == 403, author_release.text
    assert author_release.json()["code"] == "sod_violation"

    approver_release = await app_client.post(
        f"/api/v1/documents/{did}/release", headers=hb, json={}
    )
    assert approver_release.status_code == 403, approver_release.text
    assert approver_release.json()["code"] == "sod_violation"

    third_party = await app_client.post(f"/api/v1/documents/{did}/release", headers=hc, json={})
    assert third_party.status_code == 200, third_party.text
    assert third_party.json()["current_state"] == "Effective"


async def test_sod2_approver_release_allowed_when_flag_on(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    await s5.set_approver_release(await s5.default_org_id(), True)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    did = await s5.drive_to_approved(app_client, ha, hb, await s5.type_id("SOP"), b"sod2-on")

    rel = await app_client.post(f"/api/v1/documents/{did}/release", headers=hb, json={})
    assert rel.status_code == 200, rel.text  # approver may release with the flag on


async def test_sod2_author_never_releases_even_with_flag_on(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """The author side is unconditional — the flag relaxes the approver, never the author."""
    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    await s5.set_approver_release(await s5.default_org_id(), True)
    ha, hb = _auth(token_factory, subj.a), _auth(token_factory, subj.b)
    did = await s5.drive_to_approved(app_client, ha, hb, await s5.type_id("SOP"), b"sod2-author")

    rel = await app_client.post(f"/api/v1/documents/{did}/release", headers=ha, json={})
    assert rel.status_code == 403, rel.text
    assert rel.json()["code"] == "sod_violation"


# --- SoD-3: auditor independence (RBAC) ------------------------------------------------


async def test_sod3_auditor_cannot_approve(
    app_client: AsyncClient, token_factory: Callable[..., str], subj: SimpleNamespace
) -> None:
    """An Internal-Auditor-only principal lacks document.approve entirely (the role is hard-excluded
    from edit/approve/release) → deny-by-default permission_denied, never an approval."""
    await s5.grant_lifecycle(subj.a)
    auditor = f"kc-auditor-{uuid.uuid4().hex[:8]}"
    await s5.grant_role(auditor, "Internal Auditor")
    ha, h_auditor = _auth(token_factory, subj.a), _auth(token_factory, auditor)

    did = await _to_in_review(app_client, ha, await s5.type_id("SOP"))
    task_id = await s5.task_for_doc(did)

    dec = await app_client.post(
        f"/api/v1/tasks/{task_id}/decision", headers=h_auditor, json={"outcome": "approve"}
    )
    assert dec.status_code == 403, dec.text
    assert dec.json()["code"] == "permission_denied"
