"""The measured accuracy band for ``classifier_version='rule-heuristic-1'`` (S-ing-2, R10/§6.4a).

This IS the validation harness: it runs the v1 ``RuleHeuristicClassifier`` over a held-out, labeled
synthetic corpus (``tests/fixtures/ingestion_corpus/corpus.json``) and asserts the measured
per-dimension precision/recall meets the **published INTERIM band** (``VALIDATION.md``). The band is
INTERIM — synthetic corpus only, NOT representative of real production shares (a real-corpus sprint
is the v1.x prerequisite). The corpus is authored from the §6.2 taxonomy, NOT tuned to the rule-pack
weights (a hold-out, to avoid train-on-test)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from easysynq_api.domain.ingestion.rule_classifier import (
    ClassificationResult,
    FileFeatures,
    RuleHeuristicClassifier,
)
from easysynq_api.domain.ingestion.rule_pack import default_rule_pack

_CORPUS_PATH = (
    Path(__file__).resolve().parent.parent / "fixtures" / "ingestion_corpus" / "corpus.json"
)

# The org's requirement-node clause -> PDCA map (the classifier derives PDCA from this; the catalog
# is the authority at runtime). The corpus only exercises these clauses.
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

# The PUBLISHED INTERIM accuracy band (see VALIDATION.md). Measured on the synthetic corpus; the
# asserts are a floor (regressions below the published figure fail CI).
_BAND = {
    "kind_accuracy": 0.85,
    "type_accuracy": 0.85,
    "clause_precision": 0.80,
    "clause_recall": 0.60,
}


def _load() -> list[dict[str, Any]]:
    return json.loads(_CORPUS_PATH.read_text(encoding="utf-8"))["entries"]


def _features(raw: dict[str, Any]) -> FileFeatures:
    return FileFeatures(
        filename=raw["filename"],
        rel_path=raw["rel_path"],
        ext=raw.get("ext"),
        header_block=raw.get("header_block"),
        full_text=raw.get("full_text"),
    )


def _run() -> list[tuple[dict[str, Any], ClassificationResult]]:
    clf = RuleHeuristicClassifier(default_rule_pack())
    return [(e, clf.classify(_features(e["features"]), clause_pdca=_CLAUSE_PDCA)) for e in _load()]


def measure() -> dict[str, float]:
    results = _run()
    kind_ok = sum(1 for e, r in results if r.kind == e["kind"])
    typed = [(e, r) for e, r in results if e["type"] is not None]
    type_ok = sum(1 for e, r in typed if r.type_code == e["type"])
    tp = fp = fn = 0
    for e, r in results:
        pred, true = set(r.clause_numbers), set(e["clauses"])
        tp += len(pred & true)
        fp += len(pred - true)
        fn += len(true - pred)
    return {
        "kind_accuracy": kind_ok / len(results),
        "type_accuracy": (type_ok / len(typed)) if typed else 1.0,
        "clause_precision": (tp / (tp + fp)) if (tp + fp) else 1.0,
        "clause_recall": (tp / (tp + fn)) if (tp + fn) else 1.0,
        "corpus_size": float(len(results)),
    }


def test_corpus_is_substantial() -> None:
    assert len(_load()) >= 40  # an INTERIM floor; the real-corpus sprint is v1.x


def test_measured_band_meets_published_floor() -> None:
    m = measure()
    assert m["kind_accuracy"] >= _BAND["kind_accuracy"], m
    assert m["type_accuracy"] >= _BAND["type_accuracy"], m
    assert m["clause_precision"] >= _BAND["clause_precision"], m
    assert m["clause_recall"] >= _BAND["clause_recall"], m


if __name__ == "__main__":
    for k, v in measure().items():
        print(f"{k}: {v:.3f}")
