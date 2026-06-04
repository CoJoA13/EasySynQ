"""Rule-pack loader + the ReDoS-vetting mechanism (S-ing-2, doc 09 §6.3).

Proves the YAML loader validates structure + confines/vets regex: a nested-quantifier (OWASP ReDoS)
pattern is REFUSED at load (``RulePackError``), regex is allowed only on length-capped filename /
header targets, and the built-in ISO 9001 pack loads + validates clean."""

from __future__ import annotations

from pathlib import Path

import pytest

from easysynq_api.domain.ingestion.rule_pack import (
    RulePackError,
    default_rule_pack,
    load_rule_pack,
    validate_pattern,
)


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "pack.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_default_pack_loads_and_validates() -> None:
    pack = default_rule_pack()
    assert pack.version == "rule-heuristic-1"
    assert pack.type_rules and pack.kind_rules and pack.clause_rules
    # every type rule has a domain and at least one matcher
    for rule in pack.type_rules:
        assert rule.domain in ("document", "record")
        assert rule.matchers


def test_redos_nested_quantifier_pattern_is_refused_at_load(tmp_path: Path) -> None:
    body = """
version: t
type_rules:
  - candidate: X
    domain: document
    matchers:
      - {signal: explicit_doc_code, target: filename, pattern: '(a+)+$', weight: 10, explanation: x}
"""
    with pytest.raises(RulePackError, match="nested quantifier"):
        load_rule_pack(_write(tmp_path, body))


def test_validate_pattern_rejects_owasp_redos_examples() -> None:
    for bad in (r"(a+)+", r"(a*)*", r"(.*a){10}", r"(\d+)+"):
        with pytest.raises(RulePackError):
            validate_pattern(bad)


def test_validate_pattern_rejects_overlong() -> None:
    with pytest.raises(RulePackError, match="exceeds"):
        validate_pattern("a" * 300)


def test_validate_pattern_accepts_anchored_doc_code() -> None:
    rx = validate_pattern(r"\bSOP[-_ ]")
    assert rx.search("SOP-PUR-002") is not None
    assert rx.search("nope") is None


def test_validate_pattern_accepts_quantified_non_capturing_and_named_groups() -> None:
    # The group-modifier ``?`` in (?:..) / (?P<n>..) must NOT be mistaken for a body quantifier:
    # these are safe (no +/*/{ in the body) and must be accepted (the diff-review fix).
    for safe in (r"(?:abc)+", r"(?P<n>abc)+", r"\b(?:SOP|WI)[-_ ]"):
        validate_pattern(safe)  # must not raise RulePackError


def test_regex_pattern_on_content_target_is_refused(tmp_path: Path) -> None:
    body = """
version: t
type_rules:
  - candidate: X
    domain: document
    matchers:
      - {signal: content_keyword, target: content, pattern: 'abc', weight: 10, explanation: x}
"""
    with pytest.raises(RulePackError, match="regex pattern target"):
        load_rule_pack(_write(tmp_path, body))


def test_missing_version_is_refused(tmp_path: Path) -> None:
    with pytest.raises(RulePackError, match="version"):
        load_rule_pack(_write(tmp_path, "type_rules: []\n"))


def test_unknown_signal_is_refused(tmp_path: Path) -> None:
    body = """
version: t
kind_rules:
  - candidate: DOCUMENT
    matchers:
      - {signal: telepathy, target: header, keywords: [x], weight: 5, explanation: x}
"""
    with pytest.raises(RulePackError, match="unknown signal"):
        load_rule_pack(_write(tmp_path, body))


def test_matcher_needs_exactly_one_of_pattern_keywords_predicate(tmp_path: Path) -> None:
    body = """
version: t
kind_rules:
  - candidate: DOCUMENT
    matchers:
      - {signal: header_keyword, target: header, weight: 5, explanation: x}
"""
    with pytest.raises(RulePackError, match="exactly one of"):
        load_rule_pack(_write(tmp_path, body))


def test_keywords_are_lowercased(tmp_path: Path) -> None:
    body = """
version: t
type_rules:
  - candidate: X
    domain: document
    matchers:
      - signal: header_keyword
        target: header
        keywords: ["Quality POLICY"]
        weight: 5
        explanation: y
"""
    pack = load_rule_pack(_write(tmp_path, body))
    assert pack.type_rules[0].matchers[0].keywords == ("quality policy",)
