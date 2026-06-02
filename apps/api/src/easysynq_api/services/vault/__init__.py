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
from .mirror import MirrorSyncResult, atomic_swap, list_effective_versions, sync_mirror
from .mirror_sink import (
    CapturingMirrorEnqueueSink,
    CeleryMirrorEnqueueSink,
    LoggingMirrorEnqueueSink,
    MirrorEnqueueSink,
    get_mirror_enqueue_sink,
    set_mirror_enqueue_sink,
)
from .render import (
    LoggingRenderSink,
    RenderRequest,
    RenderResult,
    RenderSink,
    RenderStatus,
    get_render_sink,
    set_render_sink,
)
from .render_gotenberg import GotenbergRenderSink
from .service import break_lock, checkin, checkout, create_document, heartbeat, init_upload
from .signature import (
    CapturingSignatureEventSink,
    DbSignatureEventSink,
    LoggingSignatureEventSink,
    SignatureEvent,
    SignatureEventSink,
    get_vault_signature_sink,
)
from .watermark import stamp_controlled_copy

__all__ = [
    "CapturingMirrorEnqueueSink",
    "CapturingSignatureEventSink",
    "CapturingVaultAuditSink",
    "CeleryMirrorEnqueueSink",
    "DbSignatureEventSink",
    "GotenbergRenderSink",
    "LoggingMirrorEnqueueSink",
    "LoggingRenderSink",
    "LoggingSignatureEventSink",
    "LoggingVaultAuditSink",
    "MirrorEnqueueSink",
    "MirrorSyncResult",
    "RenderRequest",
    "RenderResult",
    "RenderSink",
    "RenderStatus",
    "SignatureEvent",
    "SignatureEventSink",
    "TransitionResult",
    "VaultAuditEvent",
    "VaultAuditSink",
    "approve",
    "atomic_swap",
    "audit_transition",
    "break_lock",
    "checkin",
    "checkout",
    "create_document",
    "get_mirror_enqueue_sink",
    "get_render_sink",
    "get_vault_audit_sink",
    "get_vault_signature_sink",
    "heartbeat",
    "init_upload",
    "list_effective_versions",
    "obsolete",
    "release",
    "release_due",
    "request_changes",
    "set_mirror_enqueue_sink",
    "set_render_sink",
    "stamp_controlled_copy",
    "start_revision",
    "submit_review",
    "sync_mirror",
]
