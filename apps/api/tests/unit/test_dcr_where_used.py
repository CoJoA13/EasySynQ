"""S-dcr-2 unit proofs — the pure where-used projection + the §7.3 obsoletion predicate
(``domain/dcr/where_used.py`` + ``domain/dcr/obsoletion.py``). No I/O."""

from __future__ import annotations

import pytest

from easysynq_api.domain.dcr import bucket_links, evaluate_obsoletion

pytestmark = pytest.mark.unit


def _link(link_type: str, direction: str, level: str | None = None) -> dict:
    return {
        "link_id": "L",
        "link_type": link_type,
        "direction": direction,
        "document_id": "D",
        "identifier": "X",
        "title": "t",
        "current_state": "Effective",
        "document_level": level,
    }


def test_bucket_links_directional_mapping() -> None:
    links = [
        _link("parent_of", "outbound"),  # neighbour is my child
        _link("parent_of", "inbound"),  # neighbour is my parent
        _link("child_of", "outbound"),  # I am neighbour's child → neighbour is my parent
        _link("child_of", "inbound"),  # neighbour is my child
        _link("references", "inbound"),  # neighbour references me
        _link("references", "outbound", level="L3_WORK_INSTRUCTION"),  # I reference a non-form
        _link("references", "outbound", level="L4_FORM"),  # I reference a form
        _link("supersedes", "outbound"),  # I supersede neighbour
        _link("supersedes", "inbound"),  # neighbour supersedes me
    ]
    b = bucket_links(links)
    assert len(b["child_documents"]) == 2  # parent_of-out + child_of-in
    assert len(b["parent_documents"]) == 2  # parent_of-in + child_of-out
    assert len(b["referenced_by"]) == 1
    assert len(b["references_out"]) == 1  # the non-form outbound reference
    assert len(b["forms_templates"]) == 1  # the L4_FORM outbound reference
    assert len(b["supersedes"]) == 1
    assert len(b["superseded_by"]) == 1


def test_bucket_links_empty() -> None:
    b = bucket_links([])
    assert all(v == [] for v in b.values())


def test_obsoletion_safe_when_no_legs_fire() -> None:
    safety = evaluate_obsoletion(
        governing_active_processes=[],
        referencing_effective_documents=[],
        sole_star_clauses=[],
    )
    assert safety.blocked is False
    assert safety.reasons == ()


def test_obsoletion_blocks_on_governs_active_process() -> None:
    safety = evaluate_obsoletion(
        governing_active_processes=[("p1", "Purchasing")],
        referencing_effective_documents=[],
        sole_star_clauses=[],
    )
    assert safety.blocked is True
    assert [r.code for r in safety.reasons] == ["governs_active_process"]


def test_obsoletion_blocks_on_all_three_legs() -> None:
    safety = evaluate_obsoletion(
        governing_active_processes=[("p1", "Purchasing")],
        referencing_effective_documents=[("d2", "SOP-QA-007")],
        sole_star_clauses=[("8.4", "8.4 External provider control")],
    )
    assert safety.blocked is True
    assert {r.code for r in safety.reasons} == {
        "governs_active_process",
        "referenced_by_effective",
        "sole_star_coverage",
    }
