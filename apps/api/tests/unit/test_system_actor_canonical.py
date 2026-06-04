"""S-rec-2 unit: a system-actor (NULL ``actor_id``) record event round-trips canonical_serialize.

The Beat retention sweep emits ``actor_id=None, actor_type='system'`` audit rows. This proves the
frozen v1 serializer (and thus the S6 chain-linker that hashes its output) handles a NULL actor_id
without crashing — the precedent is upgrade/backup, formalised here for records."""

from __future__ import annotations

import datetime
import uuid

import pytest

from easysynq_api.services.audit.canonical import (
    GENESIS_HASH,
    AuditRow,
    canonical_serialize,
    compute_row_hash,
)


@pytest.mark.unit
def test_system_actor_record_event_serializes() -> None:
    row = AuditRow(
        id=42,
        org_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        occurred_at=datetime.datetime(2026, 6, 3, 12, 0, 0, tzinfo=datetime.UTC),
        actor_id=None,  # the system actor — the sweep has no AppUser
        actor_type="system",
        event_type="RECORD_DISPOSED",
        object_type="record",
        object_id=uuid.UUID("00000000-0000-0000-0000-0000000000b2"),
        scope_ref=None,
        reason=None,
        before={"disposition_state": "DUE_FOR_REVIEW"},
        after={"disposition_state": "DISPOSED", "trigger": "sweep"},
        request_id=None,
        client_ip=None,
        user_agent=None,
        auth_context=None,
        signature_event_id=None,
    )
    payload = canonical_serialize(row, GENESIS_HASH)
    assert isinstance(payload, bytes) and payload  # no crash on NULL actor_id
    # And it hashes deterministically (the chain-linker's contract).
    assert compute_row_hash(row, GENESIS_HASH) == compute_row_hash(row, GENESIS_HASH)
