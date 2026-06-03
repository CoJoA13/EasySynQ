"""S9 unit proofs — the frozen ISO 9001:2015 clause catalog + the clause enums/events.

The catalog data (``db.seeds.iso9001_clauses``) is the authoritative seed for migration 0018; these
assertions freeze its shape against doc 02 §2.1 (the ★ mandatory set, Register R30 — incl. 8.5.6)
and §3.2 (PDCA), and prove the self-referential tree is well-formed before it ever touches a DB.
"""

from __future__ import annotations

import pytest

from easysynq_api.db.models._audit_enums import EventType
from easysynq_api.db.models._clause_enums import PDCA_PHASE_VALUES, PdcaPhase
from easysynq_api.db.seeds.iso9001_clauses import CLAUSES

pytestmark = pytest.mark.unit

# doc 02 §2.1 / Register R30 — exactly the 20 mandatory documented-information clauses.
_EXPECTED_STARS = {
    "4.3",
    "4.4",
    "5.2",
    "6.2",
    "7.1.5.1",
    "7.1.5.2",
    "7.2",
    "8.1",
    "8.2.3",
    "8.3",
    "8.4",
    "8.5.2",
    "8.5.3",
    "8.5.6",
    "8.6",
    "8.7",
    "9.1.1",
    "9.2",
    "9.3",
    "10.2",
}


def _numbers() -> set[str]:
    return {c[0] for c in CLAUSES}


def test_catalog_numbers_are_unique() -> None:
    numbers = [c[0] for c in CLAUSES]
    assert len(numbers) == len(set(numbers))
    assert len(numbers) == 83


def test_star_set_matches_doc02_section_2_1() -> None:
    stars = {number for number, _p, _t, _i, is_star, _ph, _r in CLAUSES if is_star}
    assert stars == _EXPECTED_STARS
    assert len(stars) == 20
    assert "8.5.6" in stars  # R30 explicitly requires production/service change control


def test_tree_parents_are_the_immediate_numeric_parent() -> None:
    numbers = _numbers()
    for number, parent, *_rest in CLAUSES:
        if parent is None:
            assert "." not in number  # only the bare chapter clauses 4..10 are roots
        else:
            assert parent in numbers
            assert parent == number.rsplit(".", 1)[0]


def test_top_level_clauses_are_section_headers() -> None:
    tops = {c[0]: c for c in CLAUSES if c[1] is None}
    assert set(tops) == {"4", "5", "6", "7", "8", "9", "10"}
    for _number, _p, _t, _i, _s, _ph, requirement_node in tops.values():
        assert requirement_node is False


def test_every_clause_has_a_valid_pdca_phase() -> None:
    assert set(PDCA_PHASE_VALUES) == {"PLAN", "DO", "CHECK", "ACT"}
    for _number, _p, _t, _i, _s, phase, _r in CLAUSES:
        assert phase in PDCA_PHASE_VALUES


def test_clause_audit_events_exist() -> None:
    assert EventType.CLAUSE_MAPPED.value == "CLAUSE_MAPPED"
    assert EventType.CLAUSE_UNMAPPED.value == "CLAUSE_UNMAPPED"


def test_pdca_phase_enum_members() -> None:
    assert {m.value for m in PdcaPhase} == {"PLAN", "DO", "CHECK", "ACT"}
