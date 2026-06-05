"""Pure helpers: the §4.3 summary reducer, the guarded mime sniff, the cheap encrypted-header probe,
and the ingestion enum members (S-ing-1)."""

from __future__ import annotations

from easysynq_api.db.models._audit_enums import AuditObjectType, EventType
from easysynq_api.db.models._ingestion_enums import IMPORT_RUN_STATUS_VALUES, ImportRunStatus
from easysynq_api.domain.ingestion.summary import build_summary
from easysynq_api.services.ingestion import mime
from easysynq_api.services.ingestion.service import _looks_encrypted


def test_build_summary_shape() -> None:
    assert build_summary(
        total_files=10,
        total_bytes=1234,
        disposition_counts={"included": 7, "excluded": 2, "quarantine": 1},
        ext_histogram={"pdf": 5, "docx": 3},
        exact_dup_clusters=2,
        exact_dup_files=5,
    ) == {
        "total_files": 10,
        "total_bytes": 1234,
        "included": 7,
        "excluded": 2,
        "quarantine": 1,
        "ext_histogram": {"pdf": 5, "docx": 3},
        "exact_dup_clusters": 2,
        "exact_dup_files": 5,
    }


def test_build_summary_defaults_missing_dispositions() -> None:
    s = build_summary(
        total_files=0,
        total_bytes=0,
        disposition_counts={},
        ext_histogram={},
        exact_dup_clusters=0,
        exact_dup_files=0,
    )
    assert s["included"] == 0 and s["excluded"] == 0 and s["quarantine"] == 0


def test_sniff_mime_always_returns_str() -> None:
    assert isinstance(mime.sniff_mime(b"%PDF-1.4\n", "x.pdf"), str)
    assert isinstance(mime.sniff_mime(b"", "notes.txt"), str)


def test_libmagic_sniff_never_raises() -> None:
    out = mime._libmagic_sniff(b"\x00\x01\x02")
    assert out is None or isinstance(out, str)


def test_sniff_mime_falls_back_to_extension(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # Force the libmagic-absent path (the bare CI runner) → mimetypes fallback.
    monkeypatch.setattr(mime, "_libmagic_sniff", lambda head: None)
    assert mime.sniff_mime(b"hello", "notes.txt") == "text/plain"
    assert mime.sniff_mime(b"hello", "weird.zzzunknown") == "application/octet-stream"


def test_looks_encrypted() -> None:
    assert _looks_encrypted(b"%PDF-1.5\n5 0 obj\n/Encrypt 9 0 R", "pdf")
    assert not _looks_encrypted(b"%PDF-1.5\nhello world", "pdf")
    # an encrypted OOXML is an OLE/CFB container; a plain one is a ZIP (PK..)
    assert _looks_encrypted(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1rest", "docx")
    assert not _looks_encrypted(b"PK\x03\x04rest", "docx")
    # legacy .doc is ALWAYS CFB but not encrypted → must not be flagged
    assert not _looks_encrypted(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "doc")


def test_status_enum_values() -> None:
    # S-ing-2 ADD VALUEs Extracting/Classifying/Classified (the additive-enum pattern); order is
    # load-bearing (the migration ADD VALUEs the new states; they precede the terminals).
    assert IMPORT_RUN_STATUS_VALUES == (
        "Created",
        "Scanning",
        "Scanned",
        "Extracting",
        "Classifying",
        "Classified",
        "Failed",
        "Cancelled",
    )
    assert ImportRunStatus.SCANNED.value == "Scanned"
    assert ImportRunStatus.CLASSIFIED.value == "Classified"


def test_audit_enum_members_exist() -> None:
    assert AuditObjectType.import_run.value == "import_run"
    for name in (
        "IMPORT_RUN_CREATED",
        "IMPORT_RUN_STAGE_CHANGED",
        "IMPORT_RUN_FAILED",
        "IMPORT_RUN_CANCELLED",
    ):
        assert hasattr(EventType, name)
