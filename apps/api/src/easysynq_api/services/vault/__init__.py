"""The vault use-case layer: document creation, the check-out/check-in cycle, the lifecycle FSM +
release cutover, and the object-store (``storage``) + check-out lock (``locks``) it orchestrates."""

from .audit import (
    CapturingVaultAuditSink,
    LoggingVaultAuditSink,
    VaultAuditEvent,
    VaultAuditSink,
    get_vault_audit_sink,
)
from .lifecycle import (
    TransitionResult,
    approve,
    audit_transition,
    obsolete,
    release,
    release_due,
    request_changes,
    start_revision,
    submit_review,
)
from .service import break_lock, checkin, checkout, create_document, heartbeat, init_upload
from .signature import (
    CapturingSignatureEventSink,
    DbSignatureEventSink,
    LoggingSignatureEventSink,
    SignatureEvent,
    SignatureEventSink,
    get_vault_signature_sink,
)

__all__ = [
    "CapturingSignatureEventSink",
    "CapturingVaultAuditSink",
    "DbSignatureEventSink",
    "LoggingSignatureEventSink",
    "LoggingVaultAuditSink",
    "SignatureEvent",
    "SignatureEventSink",
    "TransitionResult",
    "VaultAuditEvent",
    "VaultAuditSink",
    "approve",
    "audit_transition",
    "break_lock",
    "checkin",
    "checkout",
    "create_document",
    "get_vault_audit_sink",
    "get_vault_signature_sink",
    "heartbeat",
    "init_upload",
    "obsolete",
    "release",
    "release_due",
    "request_changes",
    "start_revision",
    "submit_review",
]
