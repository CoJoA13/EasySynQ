"""The v1 ``RuleHeuristicClassifier`` — the §6 weighted-evidence scorer (slice S-ing-2).

PURE, no IO. Implements the ``ClassifierProvider`` seam (doc 09 §3.4/§6.6): given a file's extracted
features + a rule pack + the clause→PDCA map, it scores the four §6.1 dimensions and returns a
``ClassificationResult`` with per-dimension confidence, a row-level band, and a human-readable
evidence list. A future ML/LLM provider is additive — it produces the SAME result shape.

**Scoring (§6.3, formula made concrete).** A candidate's raw score = the sum of the weights of the
rule's matchers that fire; the dimension score = ``min(100, sum)`` (a capped weighted sum, NO
divide-by-max). Seed weights are calibrated so the doc 09 §6.5 worked examples reproduce (e.g.
SOP-PUR-002: doc-code 40 + header 30 + folder 15 + keywords 7 = 92). Bands: High ≥85 / Medium 60-84
/ Low <60; ``top2_margin = top - runner_up``; a dimension is *ambiguous* when its top-two are
within ``AMBIGUOUS_MARGIN`` (10). The row band is the headline (type) confidence — the §6.5
single-number model; if ANY scored dimension is ambiguous the band is AMBIGUOUS (routes to
Needs-Decision regardless — §6.4). kind is excluded (always human-confirmed); clause/process
confidences ride per-dimension.

**Kind is scored only (R10).** UNKNOWN emerges when ``max(DOCUMENT, RECORD) < KIND_UNKNOWN_FLOOR``;
it is never auto-confirmed — confirmation is the S-ing-4 review slice.

**PDCA is derived, never guessed (§6.1).** From the highest-confidence matched **requirement-node**
clause (bare section headers are excluded — they are not in ``clause_pdca``), tie within 5 pts → the
highest-numbered clause. ``None`` when no requirement-node clause matched.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .rule_pack import (
    MAX_FILENAME_LENGTH,
    MAX_HEADER_LENGTH,
    Matcher,
    Rule,
    RulePack,
)

HIGH_THRESHOLD = 85
MEDIUM_THRESHOLD = 60
AMBIGUOUS_MARGIN = 10
KIND_UNKNOWN_FLOOR = 30  # max(DOCUMENT, RECORD) below this → kind is UNKNOWN

_PROCESS_FOLDER_WEIGHT = 30  # an existing process name appearing as a folder token
_PROCESS_HEADER_WEIGHT = 15  # …or in the header
_PDCA_TIE_MARGIN = 5  # within this, the higher-numbered clause wins the PDCA derivation
_DATE_RE = re.compile(
    r"\b(19|20)\d{2}[-/.]\d{1,2}[-/.]\d{1,2}\b|\b\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}\b"
)
_PREDICATE_SCAN_CAP = 50_000  # chars of full_text a structural predicate inspects


@dataclass(frozen=True, slots=True)
class FileFeatures:
    """The classifier input — extracted features + the path/name context (doc 09 §6.2)."""

    filename: str
    rel_path: str
    ext: str | None = None
    mime_type: str | None = None
    header_block: str | None = None
    full_text: str | None = None
    embedded_props: Mapping[str, Any] = field(default_factory=dict)
    structure_hints: Mapping[str, Any] = field(default_factory=dict)
    extract_failed: bool = False


@dataclass(frozen=True, slots=True)
class Evidence:
    dimension: str
    candidate: str
    signal_type: str
    weight: int
    explanation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "candidate": self.candidate,
            "signal_type": self.signal_type,
            "weight": self.weight,
            "explanation": self.explanation,
        }


@dataclass(frozen=True, slots=True)
class _Scored:
    candidate: str
    score: int
    domain: str | None
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    kind: str  # DOCUMENT | RECORD | UNKNOWN
    kind_conf: int
    type_code: str | None
    type_conf: int
    clause_numbers: tuple[str, ...]
    clause_conf: int
    process_names: tuple[str, ...]
    process_conf: int
    pdca_phase: str | None
    band: str  # HIGH | MEDIUM | LOW | AMBIGUOUS
    ambiguous: bool
    top2_margin: int
    evidence: tuple[Evidence, ...]
    classifier_version: str


def band_of(score: int) -> str:
    if score >= HIGH_THRESHOLD:
        return "HIGH"
    if score >= MEDIUM_THRESHOLD:
        return "MEDIUM"
    return "LOW"


def _clause_sort_key(number: str) -> tuple[int, ...]:
    out: list[int] = []
    for part in number.split("."):
        try:
            out.append(int(part))
        except ValueError:
            out.append(0)
    return tuple(out)


def _path_tokens(rel_path: str) -> list[str]:
    parts = re.split(r"[/\\]", rel_path)[:-1]  # drop the filename component
    return [p.lower() for p in parts if p]


def _target_text(target: str | None, f: FileFeatures, path_blob: str) -> str:
    if target == "filename":
        return f.filename[:MAX_FILENAME_LENGTH]
    if target == "header":
        return (f.header_block or "")[:MAX_HEADER_LENGTH]
    if target == "content":
        return f.full_text or ""
    if target == "path":
        return path_blob
    return ""


def _eval_predicate(name: str, f: FileFeatures) -> bool:
    text = (f.full_text or "")[:_PREDICATE_SCAN_CAP].lower()
    sh = f.structure_hints or {}
    if name == "has_revision_history":
        return "revision history" in text or bool(sh.get("has_revision_history"))
    if name == "has_approval_block":
        return any(
            k in text for k in ("approved by", "prepared by", "reviewed by", "authorised by")
        )
    if name == "has_dated_signatures":
        return ("signature" in text or "signed" in text) and _DATE_RE.search(text) is not None
    return False


def _fires(m: Matcher, f: FileFeatures, path_blob: str) -> bool:
    if m.predicate is not None:
        return _eval_predicate(m.predicate, f)
    text = _target_text(m.target, f, path_blob)
    if not text:
        return False
    if m.regex is not None:
        return m.regex.search(text) is not None
    low = text.lower()
    return any(kw in low for kw in m.keywords)


def _score_rules(
    rules: Sequence[Rule], dimension: str, f: FileFeatures, path_blob: str
) -> list[_Scored]:
    scored: list[_Scored] = []
    for rule in rules:
        total = 0
        ev: list[Evidence] = []
        for m in rule.matchers:
            if _fires(m, f, path_blob):
                total += m.weight
                ev.append(Evidence(dimension, rule.candidate, m.signal, m.weight, m.explanation))
        if total > 0:
            scored.append(_Scored(rule.candidate, min(100, total), rule.domain, tuple(ev)))
    scored.sort(key=lambda c: c.score, reverse=True)
    return scored


def _margin(scored: list[_Scored]) -> int:
    if not scored:
        return 0
    if len(scored) == 1:
        return scored[0].score
    return scored[0].score - scored[1].score


def _is_ambiguous(scored: list[_Scored]) -> bool:
    return (
        len(scored) >= 2
        and scored[0].score > 0
        and (scored[0].score - scored[1].score) < AMBIGUOUS_MARGIN
    )


def _score_processes(
    process_names: Sequence[str], f: FileFeatures, path_blob: str
) -> list[_Scored]:
    scored: list[_Scored] = []
    header = (f.header_block or "").lower()
    for name in process_names:
        low = name.lower()
        total = 0
        ev: list[Evidence] = []
        if low and low in path_blob:
            total += _PROCESS_FOLDER_WEIGHT
            ev.append(
                Evidence(
                    "process",
                    name,
                    "folder_path_token",
                    _PROCESS_FOLDER_WEIGHT,
                    f"Folder path names the existing process {name!r}",
                )
            )
        if low and low in header:
            total += _PROCESS_HEADER_WEIGHT
            ev.append(
                Evidence(
                    "process",
                    name,
                    "header_keyword",
                    _PROCESS_HEADER_WEIGHT,
                    f"Header names the existing process {name!r}",
                )
            )
        if total > 0:
            scored.append(_Scored(name, min(100, total), None, tuple(ev)))
    scored.sort(key=lambda c: c.score, reverse=True)
    return scored


def _derive_pdca(clause_scored: list[_Scored], clause_pdca: Mapping[str, str]) -> str | None:
    """The highest-confidence matched REQUIREMENT-NODE clause's phase (ties → highest-numbered)."""
    eligible = [c for c in clause_scored if c.candidate in clause_pdca]
    if not eligible:
        return None
    top = eligible[0].score
    contenders = [c for c in eligible if top - c.score <= _PDCA_TIE_MARGIN]
    winner = max(contenders, key=lambda c: _clause_sort_key(c.candidate))
    return clause_pdca[winner.candidate]


class RuleHeuristicClassifier:
    """The v1 ``ClassifierProvider`` (doc 09 §6.6). Bound to one rule pack; ``classify`` is pure."""

    def __init__(self, rule_pack: RulePack) -> None:
        self._pack = rule_pack

    @property
    def classifier_version(self) -> str:
        return self._pack.version

    def classify(
        self,
        features: FileFeatures,
        *,
        clause_pdca: Mapping[str, str],
        process_names: Sequence[str] = (),
    ) -> ClassificationResult:
        path_blob = " ".join(_path_tokens(features.rel_path))

        kind_scored = _score_rules(self._pack.kind_rules, "kind", features, path_blob)
        type_scored = _score_rules(self._pack.type_rules, "type", features, path_blob)
        clause_scored = _score_rules(self._pack.clause_rules, "clause", features, path_blob)
        process_scored = _score_processes(process_names, features, path_blob)

        # kind (scored only; UNKNOWN below the floor — R10)
        if kind_scored and kind_scored[0].score >= KIND_UNKNOWN_FLOOR:
            kind, kind_conf = kind_scored[0].candidate, kind_scored[0].score
        else:
            kind = "UNKNOWN"
            kind_conf = kind_scored[0].score if kind_scored else 0

        type_code = type_scored[0].candidate if type_scored else None
        type_conf = type_scored[0].score if type_scored else 0
        clause_numbers = tuple(c.candidate for c in clause_scored)
        clause_conf = clause_scored[0].score if clause_scored else 0
        proc_names = tuple(c.candidate for c in process_scored)
        process_conf = process_scored[0].score if process_scored else 0

        pdca_phase = _derive_pdca(clause_scored, clause_pdca)

        ambiguous = any(
            _is_ambiguous(s) for s in (kind_scored, type_scored, clause_scored, process_scored)
        )
        # The row band is the headline (type) confidence — the doc 09 §6.5 single-number model
        # (SOP 92 / POL 96 / AUDIT 90 …). kind is excluded (always human-confirmed, R10); clause +
        # process confidences ride per-dimension for the reviewer. ``ambiguous`` overrides to route
        # a near-tie to Needs-Decision regardless of band (§6.4).
        band = "AMBIGUOUS" if ambiguous else band_of(type_conf)

        evidence: list[Evidence] = []
        for scored in (kind_scored, type_scored, clause_scored, process_scored):
            for cand in scored:
                evidence.extend(cand.evidence)

        return ClassificationResult(
            kind=kind,
            kind_conf=kind_conf,
            type_code=type_code,
            type_conf=type_conf,
            clause_numbers=clause_numbers,
            clause_conf=clause_conf,
            process_names=proc_names,
            process_conf=process_conf,
            pdca_phase=pdca_phase,
            band=band,
            ambiguous=ambiguous,
            top2_margin=_margin(type_scored),
            evidence=tuple(evidence),
            classifier_version=self._pack.version,
        )
