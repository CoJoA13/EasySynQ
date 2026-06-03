"""The records use-case layer (slice S-rec-1, doc 06): immutable capture, evidence-linking, and
correction-via-new-record, plus the retention-policy resolution + repository access.

Mirrors ``services/vault`` (the document side): ``service`` owns the transactions (capture/correct/
link, each an atomic commit with its in-txn audit row), ``repository`` owns DB access (the retention
tier queries, the evidence satellites). Records reuse the vault's blob/WORM/numbering primitives.
"""

from .service import (
    capture_correction,
    capture_record,
    emit_record_event,
    link_evidence,
    record_init_upload,
    resolve_capture_retention,
    unlink_evidence,
)

__all__ = [
    "capture_correction",
    "capture_record",
    "emit_record_event",
    "link_evidence",
    "record_init_upload",
    "resolve_capture_retention",
    "unlink_evidence",
]
