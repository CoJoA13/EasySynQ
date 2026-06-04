"""The records use-case layer (slice S-rec-1, doc 06): immutable capture, evidence-linking, and
correction-via-new-record, plus the retention-policy resolution + repository access.

Mirrors ``services/vault`` (the document side): ``service`` owns the transactions (capture/correct/
link, each an atomic commit with its in-txn audit row), ``repository`` owns DB access (the retention
tier queries, the evidence satellites). Records reuse the vault's blob/WORM/numbering primitives.
"""

from .disposition import (
    advance_disposition,
    approve_worm_destroy,
    cancel_worm_destroy,
    place_legal_hold,
    release_legal_hold,
    request_worm_destroy,
    sweep_due_records,
)
from .service import (
    capture_correction,
    capture_record,
    emit_record_event,
    emit_record_event_system,
    link_evidence,
    record_init_upload,
    resolve_capture_retention,
    unlink_evidence,
)

__all__ = [
    "advance_disposition",
    "approve_worm_destroy",
    "cancel_worm_destroy",
    "capture_correction",
    "capture_record",
    "emit_record_event",
    "emit_record_event_system",
    "link_evidence",
    "place_legal_hold",
    "record_init_upload",
    "release_legal_hold",
    "request_worm_destroy",
    "resolve_capture_retention",
    "sweep_due_records",
    "unlink_evidence",
]
