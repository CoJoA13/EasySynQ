"""Native-PG enum bindings for the ingestion engine (slices S-ing-1/2, doc 09 §3-6, doc 14 §13).

``import_run_status`` is the import-run state machine (doc 09 §3 / doc 14 §13 §546). S-ing-1 shipped
the run-lifecycle states it could reach: ``Created`` -> ``Scanning`` -> ``Scanned`` (+ ``Failed`` /
``Cancelled`` terminals). **S-ing-2 ADD VALUEs the extract/classify stages** (``Extracting`` ->
``Classifying`` -> ``Classified`` — the resting checkpoint "classified, awaiting S-ing-4 review")
via ``ALTER TYPE ... ADD VALUE`` — the additive-enum pattern (the ``event_type`` 0011-0029
precedent), which keeps each slice's migration churn-free and dodges speculative state names. Later
slices add ``Committing`` ... ``Completed``/``PartiallyCommitted``.

The S-ing-2 staging enums (``import_extract_status``, ``import_kind``, ``import_confidence_band``)
are fresh ``CREATE TYPE``s. ``import_kind`` deliberately carries an **UNKNOWN** value the vault
``DocumentKind`` (DOCUMENT|RECORD only) does not — the classifier may decline to guess kind; the
human confirmation that resolves UNKNOWN→DOCUMENT/RECORD is the S-ing-4 review slice (R10).

All bindings use ``create_type=False`` here (the ORM and the hand-authored DDL never drift because
both source the ``*_VALUES`` tuples below).
"""

from __future__ import annotations

import enum

from sqlalchemy import Enum as SAEnum


class ImportRunStatus(enum.Enum):
    """The import-run state machine (doc 09 §3). S-ing-2 extends the S-ing-1 subset; later slices
    ADD VALUE the commit/complete states."""

    CREATED = "Created"
    SCANNING = "Scanning"
    SCANNED = "Scanned"
    EXTRACTING = "Extracting"  # S-ing-2: Stage 2 (text/metadata/OCR) in progress
    CLASSIFYING = "Classifying"  # S-ing-2: Stage 3 (the 4-dimension scorer) in progress
    CLASSIFIED = "Classified"  # S-ing-2: classified, awaiting S-ing-4 human review (resting)
    FAILED = "Failed"
    CANCELLED = "Cancelled"


class ImportExtractStatus(enum.Enum):
    """The per-file extraction outcome (doc 09 §5). EXTRACTED = native text; OCR = Tesseract ran;
    EMPTY = no extractable text (still classifiable on filename/path); FAILED = extractor error
    (the run never fails on it — §5.3)."""

    EXTRACTED = "extracted"
    OCR = "ocr"
    EMPTY = "empty"
    FAILED = "failed"


class ImportKind(enum.Enum):
    """The classifier's ``kind`` dimension (doc 09 §6.1). UNKNOWN is a staging-only value the vault
    ``DocumentKind`` lacks — kind is **always human-confirmed** (R10), so the scorer may decline."""

    DOCUMENT = "DOCUMENT"
    RECORD = "RECORD"
    UNKNOWN = "UNKNOWN"


class ImportConfidenceBand(enum.Enum):
    """The row-level review band (doc 09 §6.4): High ≥85 / Medium 60-84 / Low <60, or AMBIGUOUS when
    the top two candidates are within the margin (<10) on a scored dimension."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    AMBIGUOUS = "AMBIGUOUS"


def _vals(e: type[enum.Enum]) -> list[str]:
    return [m.value for m in e]


import_run_status_enum = SAEnum(
    ImportRunStatus, name="import_run_status", values_callable=_vals, create_type=False
)
import_extract_status_enum = SAEnum(
    ImportExtractStatus, name="import_extract_status", values_callable=_vals, create_type=False
)
import_kind_enum = SAEnum(ImportKind, name="import_kind", values_callable=_vals, create_type=False)
import_confidence_band_enum = SAEnum(
    ImportConfidenceBand, name="import_confidence_band", values_callable=_vals, create_type=False
)

# Re-used by the migration's enum-create / ADD VALUE steps so the ORM and the hand-authored DDL
# never drift.
IMPORT_RUN_STATUS_VALUES = tuple(_vals(ImportRunStatus))
IMPORT_EXTRACT_STATUS_VALUES = tuple(_vals(ImportExtractStatus))
IMPORT_KIND_VALUES = tuple(_vals(ImportKind))
IMPORT_CONFIDENCE_BAND_VALUES = tuple(_vals(ImportConfidenceBand))
