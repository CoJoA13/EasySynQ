from __future__ import annotations

import datetime
import importlib
import uuid
from types import SimpleNamespace

import pytest

from easysynq_api.db.models._audit_enums import EVENT_TYPE_VALUES, EventType
from easysynq_api.db.models._pack_enums import PACK_STATUS_VALUES, PackStatus
from easysynq_api.db.models._record_enums import RecordDispositionState
from easysynq_api.db.models._retention_enums import DispositionAction
from easysynq_api.db.models.disposition_event import DispositionEvent
from easysynq_api.db.models.evidence_pack import EvidencePack
from easysynq_api.db.models.record import Record
from easysynq_api.db.models.worm_destroy_request import WormDestroyRequest
from easysynq_api.problems import ProblemException
from easysynq_api.services.packs.repository import _artifact_alias_closure
from easysynq_api.services.records.disposition import (
    _authorized_reaper_bypass,
    _lock_r27_blob_rows,
    _PendingPurgeSnapshot,
    _r27_authority_matches,
)


def _authority_rows() -> tuple[
    Record,
    DispositionEvent,
    WormDestroyRequest,
    Record,
    DispositionEvent,
]:
    org_id = uuid.uuid4()
    requester = uuid.uuid4()
    approver = uuid.uuid4()
    source = Record(id=uuid.uuid4(), org_id=org_id)
    root = DispositionEvent(
        id=uuid.uuid4(),
        org_id=org_id,
        record_id=source.id,
        action=DispositionAction.DESTROY,
        tombstone=True,
        policy_id=None,
        approved_by=approver,
        requested_by=requester,
        is_worm_destroy=True,
        legal_basis="court order",
    )
    request = WormDestroyRequest(
        id=uuid.uuid4(),
        org_id=org_id,
        record_id=source.id,
        requested_by=requester,
        approved_by=approver,
        legal_basis="court order",
        executed_at=datetime.datetime.now(datetime.UTC),
    )
    derivative = Record(id=uuid.uuid4(), org_id=org_id)
    derived = DispositionEvent(
        id=uuid.uuid4(),
        org_id=org_id,
        record_id=derivative.id,
        action=DispositionAction.DESTROY,
        tombstone=True,
        policy_id=None,
        approved_by=approver,
        requested_by=requester,
        is_worm_destroy=True,
        legal_basis="court order",
        derived_from_disposition_event_id=root.id,
    )
    return source, root, request, derivative, derived


def test_pack_legal_erasure_enum_and_constraint_metadata() -> None:
    assert PackStatus.UNAVAILABLE.value in PACK_STATUS_VALUES
    assert EventType.PACK_INVALIDATED.value in EVENT_TYPE_VALUES
    assert {constraint.name for constraint in EvidencePack.__table__.constraints} >= {
        "ck_evidence_pack_invalidation_shape",
        "fk_evidence_pack_invalidation_event",
    }
    assert "fk_disposition_event_derived_from_event" in {
        constraint.name for constraint in DispositionEvent.__table__.constraints
    }


def test_r27_authority_accepts_root_and_exactly_one_derived_hop() -> None:
    source, root, request, derivative, derived = _authority_rows()

    assert _r27_authority_matches(source, root, request, source_event=None)
    assert _r27_authority_matches(derivative, derived, request, source_event=root)


def test_r27_authority_rejects_forged_or_deep_lineage() -> None:
    source, root, request, derivative, derived = _authority_rows()
    derived.legal_basis = "different order"
    assert not _r27_authority_matches(derivative, derived, request, source_event=root)

    derived.legal_basis = root.legal_basis
    root.derived_from_disposition_event_id = uuid.uuid4()
    assert not _r27_authority_matches(derivative, derived, request, source_event=root)

    root.derived_from_disposition_event_id = None
    request.record_id = uuid.uuid4()
    assert not _r27_authority_matches(source, root, request, source_event=None)


def test_artifact_alias_closure_is_transitive() -> None:
    a = EvidencePack(id=uuid.uuid4(), zip_blob_sha256="sha-a", portfolio_blob_sha256="sha-b")
    b = EvidencePack(id=uuid.uuid4(), zip_blob_sha256="sha-c", portfolio_blob_sha256="sha-a")
    c = EvidencePack(id=uuid.uuid4(), zip_blob_sha256="sha-c", portfolio_blob_sha256=None)
    unrelated = EvidencePack(
        id=uuid.uuid4(), zip_blob_sha256="sha-other", portfolio_blob_sha256=None
    )

    assert _artifact_alias_closure([a, b, c, unrelated], {a.id}) == {a.id, b.id, c.id}


class _FakeSession:
    async def rollback(self) -> None:
        return None


@pytest.mark.parametrize(
    "module_name,function_name",
    [
        ("easysynq_api.services.packs.build", "build"),
        ("easysynq_api.services.packs.portfolio", "build_and_cache_portfolio"),
    ],
)
async def test_pack_build_stages_take_shared_lock_before_pack_row(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    function_name: str,
) -> None:
    module = importlib.import_module(module_name)
    events: list[str] = []
    org_id = uuid.uuid4()

    async def _org(_session: object, _pack_id: uuid.UUID) -> uuid.UUID:
        events.append("org")
        return org_id

    async def _lock(_session: object, seen_org_id: uuid.UUID) -> None:
        assert seen_org_id == org_id
        events.append("shared")

    async def _pack(_session: object, _pack_id: uuid.UUID, *, for_update: bool = False) -> None:
        assert for_update
        events.append("pack-row")
        return None

    monkeypatch.setattr(module.repo, "get_pack_org_id", _org)
    monkeypatch.setattr(module, "lock_pack_build_shared", _lock)
    monkeypatch.setattr(module.repo, "get_pack", _pack)
    await getattr(module, function_name)(_FakeSession(), uuid.uuid4())

    assert events == ["org", "shared", "pack-row"]


async def test_r27_approval_takes_exclusive_lock_before_request_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disposition_module = importlib.import_module("easysynq_api.services.records.disposition")
    locks_module = importlib.import_module("easysynq_api.services.packs.locks")
    events: list[str] = []
    org_id = uuid.uuid4()

    async def _lock(_session: object, seen_org_id: uuid.UUID) -> None:
        assert seen_org_id == org_id
        events.append("exclusive")

    async def _request(
        _session: object, _request_id: uuid.UUID, *, for_update: bool = False
    ) -> None:
        assert for_update
        events.append("request-row")
        return None

    monkeypatch.setattr(locks_module, "lock_pack_erasure_exclusive", _lock)
    monkeypatch.setattr(disposition_module.repo, "get_worm_destroy_request", _request)
    actor = SimpleNamespace(id=uuid.uuid4(), org_id=org_id)
    with pytest.raises(ProblemException) as exc:
        await disposition_module.approve_worm_destroy(
            _FakeSession(),
            actor,
            uuid.uuid4(),
            uuid.uuid4(),
        )

    assert exc.value.status == 404
    assert events == ["exclusive", "request-row"]


async def test_r27_blob_rows_are_locked_once_in_global_sha_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disposition_module = importlib.import_module("easysynq_api.services.records.disposition")
    org_id = uuid.uuid4()
    first = Record(
        id=uuid.uuid4(),
        org_id=org_id,
        structured_pdf_blob_sha256="sha-a",
    )
    second = Record(
        id=uuid.uuid4(),
        org_id=org_id,
        structured_pdf_blob_sha256="sha-y",
    )
    evidence_by_record = {
        first.id: [SimpleNamespace(sha256="sha-z"), SimpleNamespace(sha256="sha-c")],
        second.id: [SimpleNamespace(sha256="sha-b")],
    }
    locked: list[str] = []

    async def _evidence(
        _session: object, record_id: uuid.UUID
    ) -> list[tuple[object, SimpleNamespace]]:
        return [(object(), blob) for blob in evidence_by_record[record_id]]

    async def _lock(_session: object, sha: str) -> None:
        locked.append(sha)

    monkeypatch.setattr(disposition_module.repo, "list_evidence_blobs", _evidence)
    monkeypatch.setattr(disposition_module.repo, "lock_blob_for_update", _lock)

    await _lock_r27_blob_rows(
        _FakeSession(),
        [first, second],
        {"sha-d", "sha-b"},
    )

    assert locked == ["sha-a", "sha-b", "sha-c", "sha-d", "sha-y", "sha-z"]


async def test_derived_reaper_authority_requires_the_bound_unavailable_pack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disposition_module = importlib.import_module("easysynq_api.services.records.disposition")
    _source, root, request, derivative, derived = _authority_rows()
    derivative.disposition_state = RecordDispositionState.DISPOSED

    async def _authority(
        _session: object,
        *,
        record_id: uuid.UUID,
        disposition_event_id: uuid.UUID,
        worm_destroy_request_id: uuid.UUID | None,
    ) -> tuple[Record, DispositionEvent, WormDestroyRequest]:
        assert record_id == derivative.id
        assert disposition_event_id == derived.id
        assert worm_destroy_request_id == request.id
        return derivative, derived, request

    monkeypatch.setattr(disposition_module.repo, "get_pending_purge_authority", _authority)
    marker = _PendingPurgeSnapshot(
        purge_id=uuid.uuid4(),
        org_id=derivative.org_id,
        sha256="pack-artifact",
        bucket=disposition_module.get_settings().s3_bucket_records,
        object_key="pack-artifact",
        requested_bypass=True,
        record_id=derivative.id,
        disposition_event_id=derived.id,
        worm_destroy_request_id=request.id,
        authority_bound=True,
    )

    class _DerivedSession:
        def __init__(self, invalidated_pack_id: uuid.UUID | None) -> None:
            self.invalidated_pack_id = invalidated_pack_id
            self.pack_lookup_count = 0

        async def get(self, _model: object, event_id: uuid.UUID) -> DispositionEvent:
            assert event_id == root.id
            return root

        async def scalar(self, _statement: object) -> uuid.UUID | None:
            self.pack_lookup_count += 1
            return self.invalidated_pack_id

    missing_pack_session = _DerivedSession(None)
    assert await _authorized_reaper_bypass(missing_pack_session, marker) is None
    assert missing_pack_session.pack_lookup_count == 1

    bound_pack_session = _DerivedSession(uuid.uuid4())
    assert await _authorized_reaper_bypass(bound_pack_session, marker) is True
    assert bound_pack_session.pack_lookup_count == 1
