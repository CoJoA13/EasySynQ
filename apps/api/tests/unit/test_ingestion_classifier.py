"""The §4.2 filters/quarantine classifier ladder (S-ing-1, doc 09 §4.2) — pure, no IO/libmagic."""

from __future__ import annotations

from easysynq_api.domain.ingestion.classifier import classify

CAP = 100  # tiny oversize cap for the tests


def test_junk_excluded() -> None:
    assert classify("Thumbs.db", "db", 10, CAP).reason == "junk"
    assert classify(".DS_Store", None, 10, CAP).reason == "junk"
    assert classify("desktop.ini", "ini", 10, CAP).reason == "junk"
    assert classify("~$report.docx", "docx", 10, CAP).reason == "junk"  # Office lock file
    assert classify("Thumbs.db", "db", 10, CAP).disposition == "excluded"


def test_empty_excluded_and_precedes_temp() -> None:
    assert classify("notes.txt", "txt", 0, CAP).reason == "empty"
    # a 0-byte .tmp reads as "empty" (the calmer verdict), not temp_backup
    assert classify("x.tmp", "tmp", 0, CAP).reason == "empty"


def test_temp_backup_quarantine() -> None:
    assert classify("x.tmp", "tmp", 10, CAP).reason == "temp_backup"
    assert classify("x.bak", "bak", 10, CAP).reason == "temp_backup"
    assert classify("doc~", None, 10, CAP).reason == "temp_backup"
    assert classify("x.tmp", "tmp", 10, CAP).disposition == "quarantine"


def test_unsupported_binary_by_ext_and_mime() -> None:
    assert classify("setup.exe", "exe", 10, CAP).reason == "unsupported_binary"
    assert classify("disk.iso", "iso", 10, CAP).reason == "unsupported_binary"
    # post-read mime refinement: an extensionless dosexec is caught once mime is sniffed
    assert (
        classify("blob", None, 10, CAP, mime="application/x-dosexec").reason == "unsupported_binary"
    )
    assert classify("setup.exe", "exe", 10, CAP).disposition == "excluded"


def test_oversize_quarantine() -> None:
    f = classify("big.pdf", "pdf", CAP + 1, CAP)
    assert f.disposition == "quarantine" and f.reason == "oversize"
    assert classify("ok.pdf", "pdf", CAP, CAP).included_candidate  # exactly at cap is fine


def test_archive_quarantine_but_ooxml_included() -> None:
    assert classify("bundle.zip", "zip", 10, CAP).reason == "archive"
    assert classify("data.7z", "7z", 10, CAP).reason == "archive"
    # OOXML keeps its docx ext → NOT matched as an archive even if libmagic says application/zip
    assert classify("report.docx", "docx", 10, CAP, mime="application/zip").included_candidate


def test_encrypted_quarantine() -> None:
    assert classify("locked.pdf", "pdf", 10, CAP, encrypted=True).reason == "needs_password"


def test_included_candidate() -> None:
    f = classify("procedure.docx", "docx", 10, CAP)
    assert f.disposition == "included" and f.included_candidate and f.reason is None


def test_precedence_unsupported_before_oversize() -> None:
    # a huge .exe is EXCLUDED (unsupported), not quarantined oversize — proves the ladder order
    assert classify("big.exe", "exe", CAP + 1, CAP).reason == "unsupported_binary"


def test_to_dict_shape() -> None:
    assert classify("Thumbs.db", "db", 10, CAP).to_dict() == {
        "disposition": "excluded",
        "reason": "junk",
        "detail": "Thumbs.db",
    }
