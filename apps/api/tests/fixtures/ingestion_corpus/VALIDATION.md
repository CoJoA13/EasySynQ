# Classifier accuracy band — `rule-heuristic-1` (INTERIM, SYNTHETIC CORPUS ONLY)

> **Status: INTERIM — synthetic corpus only.** The figures below are **NOT representative of real
> production QMS shares** and must not be cited as production-confidence accuracy. The v1.x
> real-corpus validation sprint (`S-ing-2a`) is the prerequisite for any production claim. Until
> then, Mara must treat every confidence score as a heuristic hint, never ground truth — and `kind`
> is human-confirmed on **every** item regardless of band (R10).

Per Decisions Register **R10** + doc 09 **§6.4a**, every `classifier_version` ships a *measured*
per-dimension accuracy band and a published validation method. This documents the band for the v1
`RuleHeuristicClassifier` (`classifier_version = "rule-heuristic-1"`).

## What was measured

- **Harness:** `apps/api/tests/unit/test_ingestion_accuracy.py` (runs in CI on every change; it both
  measures the band and asserts the regression floors below).
- **Corpus:** `apps/api/tests/fixtures/ingestion_corpus/corpus.json` — **45 hand-authored labeled
  examples** spanning documents (POL/SOP/WI/FRM) and records (AUDIT/MGMT_REVIEW/CAPA/CALIBRATION/
  COMPETENCE/SUPPLIER_EVAL) plus low-signal/UNKNOWN-type files (e.g. a customer-complaint record that
  classifies `kind=RECORD` with `type=null`). Each entry carries ground-truth `kind` / `type` / `clauses[]`.
- **Method:** the classifier scores each entry; we compute `kind` accuracy, `type` accuracy (over
  entries with a labeled concrete type), and **micro** `clause` precision/recall (over the multi-label
  clause set).

## Measured figures (synthetic corpus, 45 examples)

| Dimension | Metric | Measured |
|---|---|---|
| `kind` (DOCUMENT/RECORD/UNKNOWN) | accuracy | **0.91** |
| `type` (concrete doc/record type) | accuracy | **1.00** |
| `clause` | micro precision | **0.89** |
| `clause` | micro recall | **1.00** |

## Honest limitations (read this)

1. **The corpus is keyword-aligned with the rule pack.** It was authored from the same doc 09 §6.2
   signal taxonomy the matchers key on, so `type` accuracy and `clause` recall here approach 1.0 by
   construction. This proves the *machinery* (the scorer fires, bands form, evidence is produced),
   **not** real-world accuracy. Treat these as best-case ceilings.
2. **Real-world accuracy will be materially lower** — expect double-digit percentage-point drops,
   especially `clause` recall, on real shares with OCR noise, mislabeled/missing metadata, mixed
   languages, rare types, and house naming schemes the pack does not anticipate.
3. **No inter-rater check / sampling stratification** was performed (a single author labeled a
   synthetic set). The v1.x real-corpus sprint must add: ≥500 labeled docs from real QMS shares,
   stratified by kind/type/confidence, a documented labeling protocol + inter-rater agreement
   (Cohen's κ), an 80/20 hold-out, and a quarterly re-measure cadence.

## Regression floors (CI-enforced)

The harness asserts the measured band stays at or above these floors (set well below the synthetic
measurements precisely because the synthetic figures are inflated — the floors are a guard against a
genuine regression in the scorer, not a representation of real accuracy):

| Dimension | Floor |
|---|---|
| `kind` accuracy | ≥ 0.85 |
| `type` accuracy | ≥ 0.85 |
| `clause` precision | ≥ 0.80 |
| `clause` recall | ≥ 0.60 |

## Provider-swap rule (R10 / §6.4a / §6.6)

A future `ClassifierProvider` (ML/LLM) may become the default **only after** re-publishing its
measured band on the same validation corpus — a tracked, comparable operation. `classifier_version`
is recorded on every `import_classification` row, so a re-classify is always attributable.
