# apps/api/tests/unit/test_objective_commitment.py
import datetime
import uuid
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
import rfc8785

from easysynq_api.db.models._objective_enums import ObjectiveDirection
from easysynq_api.db.models._vault_enums import Classification, VersionState
from easysynq_api.domain.objectives.commitment import (
    Commitment,
    build_commitment,
    commitment_needs_freeze,
    parse_commitment,
    resolve_commitment,
)
from easysynq_api.services.vault.service import _snapshot

pytestmark = pytest.mark.unit

HI = ObjectiveDirection.HIGHER_IS_BETTER
LO = ObjectiveDirection.LOWER_IS_BETTER


def test_build_commitment_all_fields_are_json_strings() -> None:
    c = build_commitment(
        target_value=Decimal("98.5"),
        unit="%",
        direction=HI,
        due_date=datetime.date(2026, 12, 31),
        at_risk_threshold=Decimal("95"),
        baseline_value=Decimal("90"),
        policy_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
    )
    assert c == {
        "target_value": "98.5",
        "unit": "%",
        "direction": "HIGHER_IS_BETTER",
        "due_date": "2026-12-31",
        "at_risk_threshold": "95",
        "baseline_value": "90",
        "policy_id": "11111111-1111-1111-1111-111111111111",
    }


def test_build_commitment_nullable_fields_are_none() -> None:
    c = build_commitment(
        target_value=Decimal("5"),
        unit="count",
        direction=LO,
        due_date=datetime.date(2026, 6, 30),
        at_risk_threshold=None,
        baseline_value=None,
        policy_id=None,
    )
    assert c["at_risk_threshold"] is None
    assert c["baseline_value"] is None
    assert c["policy_id"] is None


def test_build_commitment_is_rfc8785_serializable_and_deterministic() -> None:
    # The WORM source blob is rfc8785.dumps(commitment); it must serialize and be byte-stable.
    c = build_commitment(
        target_value=Decimal("98"),
        unit="%",
        direction=HI,
        due_date=datetime.date(2026, 12, 31),
        at_risk_threshold=None,
        baseline_value=None,
        policy_id=None,
    )
    # Pin the exact JCS bytes (the test_audit_canonical encoder-pin pattern): key order + the
    # decimals-as-strings encoding both break loudly on an rfc8785/encoding change.
    assert rfc8785.dumps(c) == (
        b'{"at_risk_threshold":null,"baseline_value":null,"direction":"HIGHER_IS_BETTER",'
        b'"due_date":"2026-12-31","policy_id":null,"target_value":"98","unit":"%"}'
    )
    # decimals are strings, never floats (exact, reproducible bytes)
    assert b"98.0" not in rfc8785.dumps(c)


def _fake_doc() -> SimpleNamespace:
    return SimpleNamespace(
        identifier="OBJ-001",
        title="On-time delivery",
        document_type_id=uuid.uuid4(),
        owner_user_id=uuid.uuid4(),
        folder_path=None,
        classification=Classification.Internal,
        framework_id=uuid.uuid4(),
        review_period_months=24,
        acknowledgement_required=False,
    )


def test_snapshot_adds_objective_commitment_only_when_passed() -> None:
    doc = _fake_doc()
    plain = _snapshot(doc)
    assert "objective_commitment" not in plain  # ordinary docs are byte-untouched
    assert "field_schema" not in plain
    commitment = {"target_value": "98", "unit": "%", "direction": "HIGHER_IS_BETTER"}
    withc = _snapshot(doc, objective_commitment=commitment)
    assert withc["objective_commitment"] == commitment
    # the base shape is otherwise identical
    assert {k: withc[k] for k in plain} == plain


_POL = uuid.uuid4()


def _full_kwargs() -> dict[str, Any]:
    return {
        "target_value": Decimal("98.5"),
        "unit": "%",
        "direction": ObjectiveDirection.HIGHER_IS_BETTER,
        "due_date": datetime.date(2026, 12, 31),
        "at_risk_threshold": Decimal("95"),
        "baseline_value": Decimal("90"),
        "policy_id": _POL,
    }


def test_parse_commitment_round_trips_build_commitment() -> None:
    built = build_commitment(**_full_kwargs())
    parsed = parse_commitment(built)
    assert parsed == Commitment(**_full_kwargs())
    # exact decimal strings survive (never float-lossy)
    assert str(parsed.target_value) == "98.5"


def test_parse_commitment_none_legs() -> None:
    kwargs = {
        **_full_kwargs(),
        "at_risk_threshold": None,
        "baseline_value": None,
        "policy_id": None,
    }
    parsed = parse_commitment(build_commitment(**kwargs))
    assert parsed.at_risk_threshold is None
    assert parsed.baseline_value is None
    assert parsed.policy_id is None


def test_resolve_commitment_prefers_governing_else_working_row() -> None:
    governing = build_commitment(**_full_kwargs())
    working = {**_full_kwargs(), "target_value": Decimal("50")}  # an in-edit working row
    resolved = resolve_commitment(governing, **working)
    assert resolved.target_value == Decimal("98.5")  # the governing frozen value wins
    assert resolve_commitment(None, **working).target_value == Decimal("50")  # pre-first-release


def test_needs_freeze_matrix() -> None:
    working = build_commitment(**_full_kwargs())
    other = build_commitment(**{**_full_kwargs(), "target_value": Decimal("99")})
    # no version at all → first submit freezes
    assert commitment_needs_freeze(
        latest_version_state=None, latest_commitment=None, working=working
    )
    # latest is the governing Effective version (a revision) → freeze even though it HAS a
    # commitment
    assert commitment_needs_freeze(
        latest_version_state=VersionState.Effective, latest_commitment=working, working=working
    )
    # latest Draft with the SAME commitment (re-submit after request_changes, no edit) → skip
    assert not commitment_needs_freeze(
        latest_version_state=VersionState.Draft, latest_commitment=working, working=working
    )
    # latest Draft with a DIFFERENT commitment (a PATCH since the last freeze) → re-freeze
    assert commitment_needs_freeze(
        latest_version_state=VersionState.Draft, latest_commitment=other, working=working
    )
    # latest Draft with NO commitment (a legacy byte-version) → freeze (Codex-P2 belt-and-braces)
    assert commitment_needs_freeze(
        latest_version_state=VersionState.Draft, latest_commitment=None, working=working
    )


def test_parse_commitment_raises_on_malformed_snapshots() -> None:
    built = build_commitment(**_full_kwargs())
    # a missing key (even a nullable one) is a drift-class event — raise, never default
    truncated = {k: v for k, v in built.items() if k != "at_risk_threshold"}
    with pytest.raises(KeyError):
        parse_commitment(truncated)
    with pytest.raises(ValueError):
        parse_commitment({**built, "direction": "SIDEWAYS_IS_BETTER"})
