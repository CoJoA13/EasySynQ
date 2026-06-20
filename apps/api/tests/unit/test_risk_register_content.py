"""S-risk-1b unit proofs — the pure register content (build/criteria/resolve/needs-freeze) + the
static-before-{risk_id} route ordering. The key proof is ``resolve_criteria``: the live band grades
against the GOVERNING version's FROZEN per-method criteria, never a live module constant, so a code
band edit cannot re-grade a published register (R49 L2)."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi import FastAPI

from easysynq_api.db.models._risk_enums import ScoringMethod
from easysynq_api.db.models._vault_enums import VersionState
from easysynq_api.domain.risk.register_content import (
    build_register,
    criteria_for_methods,
    register_needs_freeze,
    resolve_criteria,
)
from easysynq_api.domain.risk.rules import RiskBand, default_criteria, risk_band

pytestmark = pytest.mark.unit

_M = ScoringMethod.MATRIX_5X5


def _row(rid: str, *, rating: int = 20) -> dict[str, object]:
    return {"id": rid, "type": "risk", "risk_rating": rating, "scoring_method": _M.value}


def test_build_register_sorts_rows_by_id_and_wraps_criteria() -> None:
    crit = criteria_for_methods({_M})
    reg = build_register(rows=[_row("ccc"), _row("aaa"), _row("bbb")], criteria=crit)
    assert [r["id"] for r in reg["rows"]] == ["aaa", "bbb", "ccc"]  # stable, reproducible order
    assert reg["criteria"] == crit


def test_build_register_is_reproducible_regardless_of_input_order() -> None:
    crit = criteria_for_methods({_M})
    a = build_register(rows=[_row("b"), _row("a")], criteria=crit)
    b = build_register(rows=[_row("a"), _row("b")], criteria=crit)
    assert a == b  # the bytes (rfc8785 over this) must be identical → the freeze dedups


def test_criteria_for_methods_keys_by_value() -> None:
    assert criteria_for_methods({_M}) == {"5x5_matrix": default_criteria(_M)}


def test_criteria_for_methods_empty_register() -> None:
    assert criteria_for_methods(set()) == {}


def test_resolve_criteria_uses_the_governing_frozen_entry() -> None:
    frozen = {"method": "5x5_matrix", "max_rating": 25, "bands": [{"band": "low", "min": 1}]}
    governing = {"rows": [], "criteria": {"5x5_matrix": frozen}}
    assert resolve_criteria(governing, _M) is frozen


def test_resolve_criteria_falls_back_to_default_pre_first_release() -> None:
    assert resolve_criteria(None, _M) == default_criteria(_M)


def test_resolve_criteria_falls_back_when_method_absent_from_frozen() -> None:
    governing = {"criteria": {"some_future_method": {"bands": []}}}
    assert resolve_criteria(governing, _M) == default_criteria(_M)


def test_resolve_criteria_freeze_beats_a_code_regrade() -> None:
    """THE L2 proof: a frozen criteria that shifts the Critical floor down to 10 grades rating 12 as
    Critical, while the live code default (Critical ≥ 20) grades the SAME rating as High. The live
    band MUST follow the frozen criteria — a code band edit can never re-grade a published row."""
    frozen = {
        "method": "5x5_matrix",
        "max_rating": 25,
        "bands": [{"band": "critical", "min": 10}, {"band": "low", "min": 1}],
    }
    governing = {"criteria": {"5x5_matrix": frozen}}
    assert risk_band(12, resolve_criteria(governing, _M)) is RiskBand.critical
    assert risk_band(12, default_criteria(_M)) is RiskBand.high  # live code disagrees → freeze wins


def test_needs_freeze_first_publish_no_version() -> None:
    assert register_needs_freeze(latest_version_state=None, latest_register=None, working={"a": 1})


def test_needs_freeze_revision_from_a_governing_effective() -> None:
    # The latest version is the governing Effective one (not a Draft) → always re-freeze.
    assert register_needs_freeze(
        latest_version_state=VersionState.Effective,
        latest_register={"a": 1},
        working={"a": 1},
    )


def test_needs_freeze_unchanged_draft_dedups() -> None:
    assert not register_needs_freeze(
        latest_version_state=VersionState.Draft,
        latest_register={"a": 1},
        working={"a": 1},
    )


def test_needs_freeze_changed_draft_refreezes() -> None:
    assert register_needs_freeze(
        latest_version_state=VersionState.Draft,
        latest_register={"a": 1},
        working={"a": 2},
    )


def test_risks_register_static_route_precedes_risk_id(
    resolve_route_endpoint: Callable[[FastAPI, str, str], str | None],
) -> None:
    """``/risks/{risk_id}`` matches "register" with the str path-convertor (UUIDs validate
    post-match), so the static ``/risks/register`` MUST mount first — else GET /risks/register
    resolves to get_risk_endpoint (422 on the UUID parse). A real UUID never matches "register"
    (S-pack-2 ordering). The lifecycle POSTs are 3-segment, so they cannot collide."""
    from easysynq_api.main import create_app

    app = create_app()
    assert resolve_route_endpoint(app, "/api/v1/risks/register", "GET") == "get_register_endpoint"
    assert (
        resolve_route_endpoint(app, "/api/v1/risks/register/publish", "POST")
        == "publish_register_endpoint"
    )


def test_rsk_is_not_a_leadership_artifact() -> None:
    """The register-head release rides the generic ``_cutover``, whose S-leadership-1 gate fires
    only for POL/OBJ/MR. RSK ∉ LEADERSHIP_DOC_TYPES → the gate is a verified no-op (so the org
    leadership flag, on or off, never blocks an RSK release) — proven here without touching the
    shared org config (a global-flag toggle would pollute the shared integration DB)."""
    from easysynq_api.services.vault.leadership_authorization import LEADERSHIP_DOC_TYPES

    assert "RSK" not in LEADERSHIP_DOC_TYPES
    assert set(LEADERSHIP_DOC_TYPES) == {"POL", "OBJ", "MR"}
