"""S-dcr-ui-2b integration proofs — the detail-only ``capabilities`` block on GET /dcrs/{id}.

The block is the PROCESS-scoped lifecycle affordance the SPA gates its write buttons on (the
``_mr_capabilities`` / ``_objective_capabilities`` precedent). The load-bearing assertion is the
honest ``implement``: it ANDs ``changeRequest.implement`` with the underlying
``document.release`` (REVISE) / ``document.obsolete`` (RETIRE) SoD-2 answer, so the SPA Implement
button never show-then-403s. Assertions are scoped to this run's own ids (the integration suite
shares one session DB).
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from httpx import AsyncClient

from easysynq_api.tasks.lifecycle import release_due_versions

from . import s5_helpers as s5
from .test_dcr import _auth, _grant, _subject  # the SYSTEM-override grant helpers
from .test_dcr_implement import _DCR_DRIVER_PERMS, _assign_seeded_role, _drive_dcr_to_approved

pytestmark = pytest.mark.integration

# changeRequest.* keys the capability block probes.
_CR_KEYS = (
    "changeRequest.read",
    "changeRequest.assess",
    "changeRequest.route",
    "changeRequest.implement",
    "changeRequest.close",
)


async def test_get_dcr_carries_capabilities_list_does_not(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """GET /dcrs/{id} carries a capabilities block (detail-only); GET /dcrs (list) omits it."""
    subj = _subject("caps-holder")
    await _grant(subj, _CR_KEYS)
    h = _auth(token_factory, subj)
    dcr = (
        await app_client.post(
            "/api/v1/dcrs",
            headers=h,
            json={
                "change_type": "CREATE",
                "change_significance": "MINOR",
                "reason_class": "regulatory",
                "reason_text": "caps probe",
            },
        )
    ).json()
    did = dcr["id"]

    detail = (await app_client.get(f"/api/v1/dcrs/{did}", headers=h)).json()
    assert set(detail["capabilities"]) == {"assess", "route", "implement", "close"}
    assert detail["capabilities"]["assess"] is True
    assert detail["capabilities"]["route"] is True
    assert detail["capabilities"]["close"] is True

    listed = (await app_client.get("/api/v1/dcrs", headers=h)).json()["data"]
    row = next(d for d in listed if d["id"] == did)
    assert "capabilities" not in row  # list rows are capabilities-free


async def test_no_grant_caller_gets_all_false(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """A caller with only changeRequest.read sees every write capability False (deny-by-default)."""
    reader = _subject("caps-reader")
    await _grant(reader, ("changeRequest.read",))
    hr = _auth(token_factory, reader)
    # Another user raises the DCR (the reader can read but not write).
    raiser = _subject("caps-raiser")
    await _grant(raiser, ("changeRequest.create", "changeRequest.read"))
    hraise = _auth(token_factory, raiser)
    did = (
        await app_client.post(
            "/api/v1/dcrs",
            headers=hraise,
            json={
                "change_type": "CREATE",
                "change_significance": "MINOR",
                "reason_class": "other",
                "reason_text": "deny probe",
            },
        )
    ).json()["id"]

    caps = (await app_client.get(f"/api/v1/dcrs/{did}", headers=hr)).json()["capabilities"]
    assert caps == {"assess": False, "route": False, "implement": False, "close": False}


async def test_implement_capability_is_honest_about_sod2(
    app_client: AsyncClient, token_factory: Callable[..., str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A REVISE DCR at Approved: the version AUTHOR holding changeRequest.implement +
    document.release sees implement=False (the SoD-2 underlying probe denies self-release); a
    THIRD party sees True."""
    monkeypatch.setattr(release_due_versions, "delay", lambda *a, **k: None)
    author = _subject("caps-author")
    await s5.grant_lifecycle(author)  # holds document.release
    await _grant(author, ("changeRequest.implement", "changeRequest.read"))
    ha = _auth(token_factory, author)
    approver = _subject("caps-approver")
    await s5.grant_role(approver, "Approver")
    hb = _auth(token_factory, approver)
    did = await s5.drive_to_approved(app_client, ha, hb, await s5.type_id("SOP"), b"caps-content")

    req = _subject("caps-req")
    await _grant(req, _DCR_DRIVER_PERMS)
    hreq = _auth(token_factory, req)
    qms = _subject("caps-qms")
    await _assign_seeded_role(qms, "QMS Owner")
    hq = _auth(token_factory, qms)
    dcr_id = await _drive_dcr_to_approved(
        app_client, hreq, hq, change_type="REVISE", target_document_id=did
    )

    # The author holds both keys but IS the version author -> implement FALSE (no self-release).
    caps_author = (await app_client.get(f"/api/v1/dcrs/{dcr_id}", headers=ha)).json()[
        "capabilities"
    ]
    assert caps_author["implement"] is False

    # A third party with both keys, ≠ author → implement TRUE.
    impl = _subject("caps-impl")
    await s5.grant_lifecycle(impl)
    await _grant(impl, ("changeRequest.implement", "changeRequest.read"))
    himpl = _auth(token_factory, impl)
    caps_impl = (await app_client.get(f"/api/v1/dcrs/{dcr_id}", headers=himpl)).json()[
        "capabilities"
    ]
    assert caps_impl["implement"] is True
