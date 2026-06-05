"""Unit tests for the §7.2 canonical-pick (doc 09 §7.2): the ordered tie-breakers + the provably
TOTAL order (all-tie → lowest rel_path then file_id, deterministic across passes)."""

from __future__ import annotations

import datetime
import uuid

from easysynq_api.domain.ingestion.normalize import parse_version_marker
from easysynq_api.domain.ingestion.version_family import FileForPick, order_members

_UTC = datetime.UTC


def _f(
    name: str,
    *,
    fid: uuid.UUID | None = None,
    rel_path: str | None = None,
    ext: str | None = None,
    mtime: datetime.datetime | None = None,
    embedded_modified: str | None = None,
) -> FileForPick:
    version, status = parse_version_marker(name)
    return FileForPick(
        file_id=fid or uuid.uuid4(),
        filename=name,
        rel_path=rel_path if rel_path is not None else name,
        ext=ext,
        mtime=mtime,
        embedded_modified=embedded_modified,
        version=version,
        status_rank=status,
    )


def test_version_marker_wins_first() -> None:
    members = [_f("SOP_v1.docx"), _f("SOP_v3_FINAL.docx"), _f("SOP_v2.docx")]
    assert order_members(members)[0].filename == "SOP_v3_FINAL.docx"


def test_recency_breaks_a_no_version_tie() -> None:
    old = _f("a.docx", rel_path="a.docx", mtime=datetime.datetime(2020, 1, 1, tzinfo=_UTC))
    new = _f("b.docx", rel_path="b.docx", mtime=datetime.datetime(2023, 1, 1, tzinfo=_UTC))
    assert order_members([old, new])[0].filename == "b.docx"


def test_embedded_modified_preferred_over_mtime() -> None:
    a = _f(
        "a.docx",
        rel_path="a.docx",
        mtime=datetime.datetime(2023, 1, 1, tzinfo=_UTC),
        embedded_modified="2019-01-01T00:00:00Z",  # older embedded → loses
    )
    b = _f(
        "b.docx",
        rel_path="b.docx",
        mtime=datetime.datetime(2019, 1, 1, tzinfo=_UTC),
        embedded_modified="2024-01-01T00:00:00Z",  # newer embedded → wins
    )
    assert order_members([a, b])[0].filename == "b.docx"


def test_format_prefers_editable_over_pdf() -> None:
    docx = _f("x.docx", ext="docx", rel_path="x.docx")
    pdf = _f("x.pdf", ext="pdf", rel_path="x.pdf")
    assert order_members([pdf, docx])[0].ext == "docx"


def test_path_prefers_current_over_archive() -> None:
    cur = _f("x.docx", ext="docx", rel_path="Current/x.docx")
    arch = _f("x.docx", ext="docx", rel_path="Archive/x.docx")
    assert "current" in order_members([arch, cur])[0].rel_path.lower()


def test_total_order_all_tie_is_deterministic() -> None:
    # Everything ties → rel_path then file_id make it total + order-independent across passes.
    f_hi = FileForPick(
        file_id=uuid.UUID(int=2),
        filename="a",
        rel_path="z/a",
        ext=None,
        mtime=None,
        embedded_modified=None,
        version=-1,
        status_rank=1,
    )
    f_lo = FileForPick(
        file_id=uuid.UUID(int=1),
        filename="a",
        rel_path="a/a",
        ext=None,
        mtime=None,
        embedded_modified=None,
        version=-1,
        status_rank=1,
    )
    o1 = [m.file_id for m in order_members([f_hi, f_lo])]
    o2 = [m.file_id for m in order_members([f_lo, f_hi])]
    assert o1 == o2  # order-independent
    assert order_members([f_hi, f_lo])[0].rel_path == "a/a"  # lexically-lowest rel_path canonical


def test_unparseable_embedded_modified_falls_through() -> None:
    f = _f(
        "x.docx",
        rel_path="x.docx",
        mtime=datetime.datetime(2022, 1, 1, tzinfo=_UTC),
        embedded_modified="not-a-real-date",  # must not crash; falls back to mtime
    )
    assert order_members([f])[0].filename == "x.docx"
