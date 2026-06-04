"""Pure tests for the membership/summary helpers (S-pack-1): ``exclusion_summary`` (the R28 shape)
and ``_build_items`` (one RECORD row per candidate + one DOCUMENT_VERSION row per included pinned
version). Built from transient ORM instances — no DB."""

from __future__ import annotations

import uuid

from easysynq_api.db.models._pack_enums import PackInclusionStatus, PackItemType
from easysynq_api.db.models.documented_information import DocumentedInformation
from easysynq_api.db.models.record import Record
from easysynq_api.services.packs.service import ClassifiedRecord, _build_items, exclusion_summary


def _cr(status: PackInclusionStatus, *, version: uuid.UUID | None = None) -> ClassifiedRecord:
    rid = uuid.uuid4()
    record = Record(id=rid, source_version_id=version, content_hash="sha256:deadbeef")
    base = DocumentedInformation(id=rid)
    return ClassifiedRecord(record, base, status, None)


def test_exclusion_summary_counts_and_ids() -> None:
    perm = _cr(PackInclusionStatus.EXCLUDED_PERMISSION)
    absent = _cr(PackInclusionStatus.EXCLUDED_ABSENCE)
    classified = [
        _cr(PackInclusionStatus.INCLUDED),
        perm,
        absent,
        _cr(PackInclusionStatus.EXCLUDED_PERMISSION),
    ]
    summary = exclusion_summary(classified)
    assert summary["permission_count"] == 2
    assert summary["absence_count"] == 1
    assert str(perm.record.id) in summary["permission"]
    assert str(absent.record.id) in summary["absence"]


def test_build_items_emits_record_rows_for_all_and_version_rows_for_included() -> None:
    org, pack = uuid.uuid4(), uuid.uuid4()
    vid = uuid.uuid4()
    included = _cr(PackInclusionStatus.INCLUDED, version=vid)
    excluded = _cr(PackInclusionStatus.EXCLUDED_ABSENCE, version=uuid.uuid4())
    items, included_count = _build_items(org, pack, [included, excluded])

    record_rows = [i for i in items if i.item_type is PackItemType.RECORD]
    version_rows = [i for i in items if i.item_type is PackItemType.DOCUMENT_VERSION]
    # Every candidate (incl. excluded) gets a RECORD row — the exclusion report IS the table (R28).
    assert len(record_rows) == 2
    # Only the INCLUDED record's pinned version is emitted (an excluded record's version is not).
    assert len(version_rows) == 1
    assert version_rows[0].version_id == vid
    # item_count = included records + their distinct pinned versions.
    assert included_count == 2


def test_build_items_deduplicates_shared_pinned_version() -> None:
    org, pack = uuid.uuid4(), uuid.uuid4()
    vid = uuid.uuid4()
    items, included_count = _build_items(
        org,
        pack,
        [
            _cr(PackInclusionStatus.INCLUDED, version=vid),
            _cr(PackInclusionStatus.INCLUDED, version=vid),
        ],
    )
    version_rows = [i for i in items if i.item_type is PackItemType.DOCUMENT_VERSION]
    assert len(version_rows) == 1  # the shared version appears once
    assert included_count == 3  # 2 included records + 1 distinct version
