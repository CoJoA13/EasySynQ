"""S7d unit proofs — the EXPORTED/PRINTED audit event_type values + the ``after`` JSONB mapping.

``alembic check`` compares column TYPES, not native-enum LABEL membership, so a forgotten Python
``EventType`` member is a *runtime* ``ValueError`` (``EventType(label)`` in ``DbVaultAuditSink``),
not a CI failure — these tests are that guardrail. The intent + copy disposition ride in the
already-hashed ``after`` JSONB (canonical v1 §4.3), so no new hashed column is introduced.
"""

from __future__ import annotations

import datetime
import uuid

from easysynq_api.db.models._audit_enums import EVENT_TYPE_VALUES, AuditObjectType, EventType
from easysynq_api.services.vault.audit import VaultAuditEvent, to_audit_event
from easysynq_api.services.vault.service import _safe_pdf_filename


def test_export_print_event_types_resolve() -> None:
    """Both labels resolve to a Python member AND appear in the tuple the migration rebuilds the PG
    type from (so a from-scratch DB and an incrementally-migrated one converge)."""
    assert EventType("EXPORTED") is EventType.EXPORTED
    assert EventType("PRINTED") is EventType.PRINTED
    assert "EXPORTED" in EVENT_TYPE_VALUES
    assert "PRINTED" in EVENT_TYPE_VALUES


def test_to_audit_event_maps_after_and_event_type() -> None:
    """An EXPORTED vault event projects onto an ``audit_event`` row with the version object type,
    the user actor, and the export intent/copy disposition carried in ``after``."""
    actor, org, obj = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    after = {"intent": "export", "copy_status": "UNCONTROLLED IF PRINTED", "printed_by": "p.author"}
    row = to_audit_event(
        VaultAuditEvent(
            occurred_at=datetime.datetime(2026, 6, 2, tzinfo=datetime.UTC),
            event_type="EXPORTED",
            actor_id=str(actor),
            org_id=str(org),
            object_type="document_version",
            object_id=str(obj),
            identifier="SOP-PUR-014",
            after=after,
        )
    )
    assert row.event_type is EventType.EXPORTED
    assert row.object_type is AuditObjectType.version
    assert row.object_id == obj
    assert row.actor_id == actor
    assert row.after == after
    assert row.row_hash is None  # the chain-linker fills hashes later (R12), not the writer


def test_safe_pdf_filename_strips_header_breaking_chars() -> None:
    """[S7d/security] The export filename is interpolated into a Content-Disposition header, so it
    is reduced to a strict ASCII token — quotes/semicolons/spaces (which could break the header or
    spoof the saved name) become underscores; an all-unsafe input degrades to a safe default."""
    out = _safe_pdf_filename('SOP-A"B;-001', "Rev A")
    assert out == "SOP-A_B_-001_Rev_A.pdf"
    assert all(c not in out for c in '";\\/ ')
    assert _safe_pdf_filename("///", "\\") == "document.pdf"  # never empty / never a path
