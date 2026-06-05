"""Unit tests for the S-ing-3 normalization helpers (doc 09 §7-8): base-name grouping, doc-code
extraction, version-marker ordering, obsolete pre-flag, NFC stability, and ReDoS safety."""

from __future__ import annotations

import unicodedata

import pytest

from easysynq_api.domain.ingestion import normalize as n


def test_normalize_text_casefold_whitespace_and_nfc() -> None:
    assert n.normalize_text("  Hello\tWORLD\n foo ") == "hello world foo"
    nfd = unicodedata.normalize("NFD", "Café Société")
    nfc = unicodedata.normalize("NFC", "Café Société")
    assert n.normalize_text(nfd) == n.normalize_text(nfc)


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("SOP-PUR-002.docx", "SOP-PUR-002"),
        ("SOP-PUR-002_v3_FINAL.docx", "SOP-PUR-002"),  # version suffix trimmed
        ("SOP-PUR-002-v3.doc", "SOP-PUR-002"),
        ("POL-03_v2.docx", "POL-03"),
        ("F-7.5-03.pdf", "F-7.5-03"),  # internal dots preserved (not the ext)
        ("WI-WELD-14 Welding.docx", "WI-WELD-14"),
        ("report-final.docx", None),  # no digit → not a code
        ("just a memo.txt", None),
    ],
)
def test_extract_doc_code(filename: str, expected: str | None) -> None:
    assert n.extract_doc_code(filename) == expected


def test_extract_doc_code_is_verbatim_not_the_type_prefix() -> None:
    # the FULL code, original casing — never the classifier's bare type_code ("SOP")
    assert n.extract_doc_code("sop-pur-002.docx") == "sop-pur-002"
    assert n.extract_doc_code("SOP-PUR-002.docx") != "SOP"


def test_extract_doc_code_from_header_block() -> None:
    assert n.extract_doc_code("procedure.docx", "Document No: QP-01 Quality Plan") == "QP-01"


def test_normalize_base_name_groups_one_family() -> None:
    fam = [
        "SOP-PUR-002_v1.docx",
        "SOP-PUR-002_v2.docx",
        "SOP-PUR-002_v3 FINAL.docx",
        "SOP-PUR-002 (1).pdf",
    ]
    assert len({n.normalize_base_name(f) for f in fam}) == 1


def test_normalize_base_name_nfc_stable() -> None:
    nfd = unicodedata.normalize("NFD", "Política Pública v1.docx")
    nfc = unicodedata.normalize("NFC", "Política Pública v1.docx")
    assert n.normalize_base_name(nfd) == n.normalize_base_name(nfc)


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("x_v3_final.docx", (3, 2)),
        ("x_v2.docx", (2, 1)),
        ("x_v1.docx", (1, 1)),
        ("x.docx", (-1, 1)),
        ("x_draft.docx", (-1, 0)),
        ("x revB.docx", (2, 1)),
    ],
)
def test_parse_version_marker(filename: str, expected: tuple[int, int]) -> None:
    assert n.parse_version_marker(filename) == expected


def test_version_marker_orders_v3final_highest() -> None:
    keys = [n.parse_version_marker(f) for f in ["a_v1.docx", "a_v2.docx", "a_v3_final.docx"]]
    assert keys == sorted(keys)  # ascending == v1 < v2 < v3FINAL


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("SOP (old).docx", True),
        ("superseded SOP.docx", True),
        ("DO NOT USE me.docx", True),
        ("Archive SOP.docx", True),
        ("SOP-PUR-002.docx", False),
    ],
)
def test_is_obsolete_filename(filename: str, expected: bool) -> None:
    assert n.is_obsolete_filename(filename) is expected


def test_redos_safe_on_adversarial_input() -> None:
    # Bounded input (length-capped) bounds backtracking — these return promptly, never hang.
    evil = ("a-" * 5000) + ("." * 5000) + ("v1" * 5000)
    assert n.extract_doc_code(evil) is None or isinstance(n.extract_doc_code(evil), str)
    assert isinstance(n.normalize_base_name(evil), str)
    assert isinstance(n.parse_version_marker(evil), tuple)
    assert isinstance(n.is_obsolete_filename(evil), bool)
