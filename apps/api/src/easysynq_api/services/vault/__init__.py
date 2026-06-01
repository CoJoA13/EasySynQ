"""The vault use-case layer: document creation, the check-out/check-in cycle, and the
object-store (``storage``) + check-out lock (``locks``) it orchestrates over the domain."""

from .audit import (
    CapturingVaultAuditSink,
    LoggingVaultAuditSink,
    VaultAuditEvent,
    VaultAuditSink,
    get_vault_audit_sink,
)
from .service import break_lock, checkin, checkout, create_document, heartbeat, init_upload

__all__ = [
    "CapturingVaultAuditSink",
    "LoggingVaultAuditSink",
    "VaultAuditEvent",
    "VaultAuditSink",
    "break_lock",
    "checkin",
    "checkout",
    "create_document",
    "get_vault_audit_sink",
    "heartbeat",
    "init_upload",
]
