"""The classifier rule pack — the §6.3 weighted-evidence matchers, loaded from YAML.

doc 09 §6.3 makes the v1 classifier a **transparent, org-overridable** weighted-evidence scorer, so
its matchers/weights/explanations live in a versioned YAML resource (``rule_packs/*.yaml``) rather
than buried in code. This module is the typed, frozen in-memory model + a strict loader/validator.
Org-override *loading* (an upload path) is deferred — v1 ships the vetted built-in pack only.

**ReDoS posture (the load-time mechanism, not just a claim).** Regex matchers are confined to the
``filename`` and ``header`` targets, both **length-capped** at match time (``MAX_FILENAME_LENGTH`` /
``MAX_HEADER_LENGTH``) — bounded input bounds backtracking. ``content``/``path`` use plain
case-insensitive substring (``keywords``), never regex. Every ``pattern`` is additionally vetted at
load: length-capped, compiled, and rejected if it contains a **nested quantifier** (a quantified
group whose body itself quantifies — the classic ``(a+)+`` catastrophic shape). A pathological
pattern is refused with ``RulePackError`` (proven by a unit test feeding an OWASP ReDoS example).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import yaml

MAX_FILENAME_LENGTH = 255
MAX_HEADER_LENGTH = 4096
_MAX_PATTERN_LENGTH = 256

# §6.2 signal taxonomy (recorded on every evidence entry).
_SIGNALS = frozenset(
    {
        "explicit_doc_code",
        "header_keyword",
        "structural_shape",
        "folder_path_token",
        "content_keyword",
        "embedded_property",
        "filename_version_marker",
    }
)
# Regex (``pattern``) is allowed ONLY on these length-capped targets (the ReDoS confinement).
_REGEX_TARGETS = frozenset({"filename", "header"})
_KEYWORD_TARGETS = frozenset({"filename", "header", "content", "path"})
# A quantified group whose single-level body also quantifies with +/*/{ → catastrophic-backtracking
# shape ((a+)+, (a*)*, (.*a){10}). ``?`` is NOT in the body class so non-capturing / named group
# modifiers ((?:..)+, (?P<n>..)+) are not false-flagged (their ``?`` is a group prefix, not a body
# quantifier); the genuinely catastrophic OWASP shapes all use +/*/{ in the body, still caught.
_NESTED_QUANTIFIER = re.compile(r"\([^()]*[+*{][^()]*\)[+*{]")


class RulePackError(ValueError):
    """A malformed or unsafe rule pack (raised at load — never at classify time)."""


@dataclass(frozen=True, slots=True)
class Matcher:
    """One weighted signal. Exactly one of ``pattern`` / ``keywords`` / ``predicate`` fires it."""

    signal: str
    weight: int
    explanation: str
    target: str | None = None
    regex: re.Pattern[str] | None = None
    keywords: tuple[str, ...] = ()
    predicate: str | None = None


@dataclass(frozen=True, slots=True)
class Rule:
    """All matchers supporting one candidate of a dimension (a type code / clause number / kind)."""

    candidate: str
    matchers: tuple[Matcher, ...]
    domain: str | None = None  # type rules only: "document" | "record" (disambiguates type_code)


@dataclass(frozen=True, slots=True)
class ScoringConfig:
    """The classifier's score-to-band cutoffs + dimension weights (doc 09 §6.3-§6.5).

    Defaults reproduce the calibrated v1 values; an org pack may override any subset via a top-level
    ``scoring:`` block. These live in the versioned pack (not Settings) so a cutoff change rides the
    SAME ``version`` pin as the matcher weights they are calibrated against — ``classifier_version``
    on every result then reflects the cutoffs too.
    """

    high_threshold: int = 85  # score ≥ this → HIGH band
    medium_threshold: int = 60  # score ≥ this (and < high) → MEDIUM; below → LOW
    ambiguous_margin: int = 10  # top-two within this → the dimension is ambiguous (Needs-Decision)
    kind_unknown_floor: int = 30  # max(DOCUMENT, RECORD) below this → kind is UNKNOWN (R10)
    process_folder_weight: int = 30  # an existing process name appearing as a folder token
    process_header_weight: int = 15  # …or in the header
    pdca_tie_margin: int = 5  # clause scores within this → the higher-numbered wins the PDCA derive


@dataclass(frozen=True, slots=True)
class RulePack:
    version: str
    kind_rules: tuple[Rule, ...] = ()
    type_rules: tuple[Rule, ...] = ()
    clause_rules: tuple[Rule, ...] = ()
    process_rules: tuple[Rule, ...] = ()
    scoring: ScoringConfig = ScoringConfig()  # frozen → a shared immutable default is safe


def validate_pattern(pattern: str) -> re.Pattern[str]:
    """Compile + ReDoS-vet one regex (load-time). Rejects over-long / nested-quantifier patterns."""
    if len(pattern) > _MAX_PATTERN_LENGTH:
        raise RulePackError(f"regex pattern exceeds {_MAX_PATTERN_LENGTH} chars")
    if _NESTED_QUANTIFIER.search(pattern):
        raise RulePackError(f"regex pattern has a nested quantifier (ReDoS risk): {pattern!r}")
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error as exc:  # malformed pattern
        raise RulePackError(f"invalid regex {pattern!r}: {exc}") from exc


def _matcher(raw: dict[str, Any]) -> Matcher:
    signal = raw.get("signal")
    if signal not in _SIGNALS:
        raise RulePackError(f"unknown signal {signal!r}")
    weight = raw.get("weight")
    if not isinstance(weight, int) or weight <= 0:
        raise RulePackError(f"matcher weight must be a positive int (got {weight!r})")
    explanation = raw.get("explanation")
    if not isinstance(explanation, str) or not explanation:
        raise RulePackError("matcher needs a non-empty explanation")
    kinds = [k for k in ("pattern", "keywords", "predicate") if raw.get(k) is not None]
    if len(kinds) != 1:
        raise RulePackError("matcher needs exactly one of pattern/keywords/predicate")
    target = raw.get("target")

    if raw.get("pattern") is not None:
        if target not in _REGEX_TARGETS:
            raise RulePackError(f"regex pattern target must be in {sorted(_REGEX_TARGETS)}")
        return Matcher(
            signal=signal,
            weight=weight,
            explanation=explanation,
            target=target,
            regex=validate_pattern(str(raw["pattern"])),
        )
    if raw.get("keywords") is not None:
        if target not in _KEYWORD_TARGETS:
            raise RulePackError(f"keywords target must be in {sorted(_KEYWORD_TARGETS)}")
        kws = raw["keywords"]
        if not isinstance(kws, list) or not all(isinstance(k, str) and k for k in kws):
            raise RulePackError("keywords must be a non-empty list of non-empty strings")
        return Matcher(
            signal=signal,
            weight=weight,
            explanation=explanation,
            target=target,
            keywords=tuple(k.lower() for k in kws),
        )
    return Matcher(
        signal=signal, weight=weight, explanation=explanation, predicate=str(raw["predicate"])
    )


def _rules(raw: Any, *, dimension: str) -> tuple[Rule, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise RulePackError(f"{dimension} must be a list of rules")
    out: list[Rule] = []
    for item in raw:
        if not isinstance(item, dict) or "candidate" not in item:
            raise RulePackError(f"{dimension} rule needs a 'candidate'")
        matchers = item.get("matchers")
        if not isinstance(matchers, list) or not matchers:
            raise RulePackError(f"{dimension} rule {item['candidate']!r} needs matchers")
        out.append(
            Rule(
                candidate=str(item["candidate"]),
                domain=item.get("domain"),
                matchers=tuple(_matcher(m) for m in matchers),
            )
        )
    return tuple(out)


def _scoring(raw: Any) -> ScoringConfig:
    """Parse + validate an optional top-level ``scoring:`` mapping. Absent → calibrated defaults.

    Every supplied knob must be a positive int; an unknown key is refused (a typo silently taking
    the default would be a quiet mis-calibration), and ``medium_threshold`` may not exceed
    ``high_threshold`` (else the MEDIUM band would be empty / inverted)."""
    if raw is None:
        return ScoringConfig()
    if not isinstance(raw, dict):
        raise RulePackError("scoring must be a mapping")
    known = {f.name for f in fields(ScoringConfig)}
    unknown = set(raw) - known
    if unknown:
        raise RulePackError(f"unknown scoring keys: {sorted(unknown)}")
    values: dict[str, int] = {}
    for key, val in raw.items():
        if isinstance(val, bool) or not isinstance(val, int) or val <= 0:
            raise RulePackError(f"scoring.{key} must be a positive int (got {val!r})")
        values[key] = val
    cfg = ScoringConfig(**values)
    if cfg.medium_threshold > cfg.high_threshold:
        raise RulePackError("scoring.medium_threshold must be <= high_threshold")
    return cfg


def load_rule_pack(path: str | Path) -> RulePack:
    """Load + validate a YAML rule pack. Raises ``RulePackError`` on malformed/unsafe content."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data.get("version"):
        raise RulePackError("rule pack needs a top-level 'version'")
    return RulePack(
        version=str(data["version"]),
        kind_rules=_rules(data.get("kind_rules"), dimension="kind_rules"),
        type_rules=_rules(data.get("type_rules"), dimension="type_rules"),
        clause_rules=_rules(data.get("clause_rules"), dimension="clause_rules"),
        process_rules=_rules(data.get("process_rules"), dimension="process_rules"),
        scoring=_scoring(data.get("scoring")),
    )


_DEFAULT_PACK_PATH = Path(__file__).parent / "rule_packs" / "iso9001_rule_pack_v1.yaml"
_cached: RulePack | None = None


def default_rule_pack() -> RulePack:
    """The built-in ISO 9001:2015 rule pack (loaded + validated once, cached)."""
    global _cached
    if _cached is None:
        _cached = load_rule_pack(_DEFAULT_PACK_PATH)
    return _cached
