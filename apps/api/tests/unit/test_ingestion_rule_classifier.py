"""The pure ``RuleHeuristicClassifier`` (S-ing-2, doc 09 §6).

Reproduces the doc 09 §6.5 worked examples (the weight calibration), proves the scoring formula
stays in [0,100], kind=UNKNOWN below the floor (R10), PDCA derivation (highest-confidence req-node
clause, ties → highest-numbered, bare headers excluded), the AMBIGUOUS band on a near-tie, and the
§5.3 extract-failed fallback to filename/path signals."""

from __future__ import annotations

import pytest

from easysynq_api.domain.ingestion.rule_classifier import (
    FileFeatures,
    RuleHeuristicClassifier,
    _clause_sort_key,
    _derive_pdca,
    _is_ambiguous,
    _Scored,
    band_of,
)
from easysynq_api.domain.ingestion.rule_pack import Matcher, Rule, RulePack, default_rule_pack

_CLAUSE_PDCA = {
    "4.3": "PLAN",
    "5.2": "PLAN",
    "6.2": "PLAN",
    "7.2": "PLAN",
    "7.1.5.2": "PLAN",
    "8.4": "DO",
    "8.7": "DO",
    "9.2": "CHECK",
    "9.3": "CHECK",
    "10.2": "ACT",
}


@pytest.fixture
def clf() -> RuleHeuristicClassifier:
    return RuleHeuristicClassifier(default_rule_pack())


def _classify(clf: RuleHeuristicClassifier, f: FileFeatures, processes: tuple[str, ...] = ()):
    return clf.classify(f, clause_pdca=_CLAUSE_PDCA, process_names=processes)


def test_sop_example_reproduces_high_92(clf: RuleHeuristicClassifier) -> None:
    r = _classify(
        clf,
        FileFeatures(
            filename="SOP-PUR-002 Purchasing.docx",
            rel_path="Procedures/SOP-PUR-002 Purchasing.docx",
            ext="docx",
            header_block="Standard Operating Procedure - Purchasing",
            full_text="supplier and purchasing process steps and responsibilities. "
            "Revision History. Approved by: J. Smith",
        ),
    )
    assert r.kind == "DOCUMENT"
    assert r.type_code == "SOP" and r.type_conf == 92
    assert "8.4" in r.clause_numbers
    assert r.pdca_phase == "DO"
    assert r.band == "HIGH" and not r.ambiguous
    assert any(e.dimension == "type" and e.explanation for e in r.evidence)


def test_policy_example_reproduces_high_96(clf: RuleHeuristicClassifier) -> None:
    r = _classify(
        clf,
        FileFeatures(
            filename="Quality Policy.pdf",
            rel_path="Quality Manual/Quality Policy.pdf",
            ext="pdf",
            header_block="Quality Policy",
            full_text="commitment to satisfy applicable requirements; a framework for setting "
            "quality objectives.",
        ),
    )
    assert r.type_code == "POL" and r.type_conf == 96
    assert "5.2" in r.clause_numbers and r.pdca_phase == "PLAN" and r.band == "HIGH"


def test_audit_example_is_record_clause_9_2_check(clf: RuleHeuristicClassifier) -> None:
    r = _classify(
        clf,
        FileFeatures(
            filename="Internal Audit Report Q2 2023.pdf",
            rel_path="Records/Audits/2023/Internal Audit Report Q2.pdf",
            ext="pdf",
            header_block="Internal Audit Report",
            full_text="audit findings and audit criteria. Lead auditor signature 2023-06-30",
        ),
    )
    assert r.kind == "RECORD" and r.type_code == "AUDIT" and r.type_conf == 90
    assert "9.2" in r.clause_numbers and r.pdca_phase == "CHECK" and r.band == "HIGH"


def test_unknown_kind_below_floor_low_band(clf: RuleHeuristicClassifier) -> None:
    r = _classify(
        clf,
        FileFeatures(filename="scan0421.pdf", rel_path="scan0421.pdf", ext="pdf"),
    )
    assert r.kind == "UNKNOWN" and r.type_code is None and r.band == "LOW"


def test_extract_failed_still_types_on_filename(clf: RuleHeuristicClassifier) -> None:
    # No header/full_text (extract failed) but a strong filename doc-code still types it (§5.3).
    r = _classify(
        clf,
        FileFeatures(
            filename="WI-WELD-14.pdf",
            rel_path="Work Instructions/WI-WELD-14.pdf",
            ext="pdf",
            extract_failed=True,
        ),
    )
    assert r.type_code == "WI"


def test_all_scores_in_range(clf: RuleHeuristicClassifier) -> None:
    for f in (
        FileFeatures(
            filename="SOP-1.docx", rel_path="Procedures/SOP-1.docx", header_block="procedure"
        ),
        FileFeatures(filename="x.bin", rel_path="x.bin"),
    ):
        r = _classify(clf, f)
        for conf in (r.kind_conf, r.type_conf, r.clause_conf, r.process_conf):
            assert 0 <= conf <= 100


def test_band_thresholds() -> None:
    assert band_of(85) == "HIGH"
    assert band_of(84) == "MEDIUM"
    assert band_of(60) == "MEDIUM"
    assert band_of(59) == "LOW"


def test_ambiguous_band_on_near_tie() -> None:
    a = Matcher(
        signal="header_keyword", target="header", keywords=("alpha",), weight=50, explanation="a"
    )
    b = Matcher(
        signal="header_keyword", target="header", keywords=("beta",), weight=45, explanation="b"
    )
    pack = RulePack(
        version="t",
        type_rules=(Rule("A", (a,), "document"), Rule("B", (b,), "document")),
    )
    r = RuleHeuristicClassifier(pack).classify(
        FileFeatures(filename="x", rel_path="x", header_block="alpha beta"), clause_pdca={}
    )
    assert r.ambiguous and r.band == "AMBIGUOUS" and r.top2_margin == 5


def test_is_ambiguous_edges() -> None:
    near = [_Scored("A", 50, None, ()), _Scored("B", 45, None, ())]
    clear = [_Scored("A", 50, None, ()), _Scored("B", 30, None, ())]
    assert _is_ambiguous(near)
    assert not _is_ambiguous(clear)
    assert not _is_ambiguous([_Scored("A", 50, None, ())])  # single candidate
    assert not _is_ambiguous([])


def test_clause_sort_key_orders_numerically() -> None:
    assert _clause_sort_key("8.4") < _clause_sort_key("8.5.6")
    assert _clause_sort_key("9.2") < _clause_sort_key("10.2")


def test_pdca_derives_from_highest_confidence_clause() -> None:
    scored = [_Scored("8.4", 60, None, ()), _Scored("5.2", 30, None, ())]
    assert _derive_pdca(scored, _CLAUSE_PDCA) == "DO"  # 8.4 (DO) outscores 5.2 (PLAN)


def test_pdca_tie_prefers_highest_numbered_clause() -> None:
    # within 5 pts → the higher-numbered clause wins (deterministic)
    scored = [_Scored("5.2", 50, None, ()), _Scored("9.2", 48, None, ())]
    assert _derive_pdca(scored, _CLAUSE_PDCA) == "CHECK"  # 9.2 > 5.2


def test_pdca_excludes_bare_header_clauses() -> None:
    # "7" is a bare section header — NOT in clause_pdca (requirement-node only) → ignored.
    scored = [_Scored("7", 90, None, ()), _Scored("5.2", 40, None, ())]
    assert _derive_pdca(scored, _CLAUSE_PDCA) == "PLAN"  # derives from 5.2, not the header


def test_pdca_none_when_no_requirement_node_clause() -> None:
    assert _derive_pdca([_Scored("7", 90, None, ())], _CLAUSE_PDCA) is None
    assert _derive_pdca([], _CLAUSE_PDCA) is None
