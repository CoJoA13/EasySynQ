"""Independent storage-worker contract and owned cleanup proofs."""

from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from threading import Event
from uuid import UUID

import pytest

pytestmark = pytest.mark.unit

ORG_ID = UUID("11111111-1111-4111-8111-111111111111")
WITNESS_ID = UUID("22222222-2222-4222-8222-222222222222")
NAMESPACE_HASH = "a" * 64
PREFIX = "checkpoints/11111111-1111-4111-8111-111111111111/"
ORIGINAL_TERMINAL_XML = (
    b'<ListVersionsResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">\n'
    b"<Name>history-probe</Name>\n"
    b"<Prefix>checkpoints%2F11111111-1111-4111-8111-111111111111%2F</Prefix>\n"
    b"<KeyMarker/><VersionIdMarker/><MaxKeys>1000</MaxKeys>\n"
    b"<EncodingType>url</EncodingType><IsTruncated>false</IsTruncated>\n"
    b"<Version><Key>checkpoints%2F11111111-1111-4111-8111-111111111111%2Fopaque</Key>"
    b"<VersionId>null</VersionId><IsLatest>true</IsLatest></Version>\n"
    b"</ListVersionsResult>"
)
ORIGINAL_BODY = b"unclassified original body"


def _entry_xml(*, kind: str = "Version", key: str = "opaque", version: str = "null") -> bytes:
    return (
        f"<{kind}><Key>checkpoints%2F11111111-1111-4111-8111-111111111111%2F{key}</Key>"
        f"<VersionId>{version}</VersionId><IsLatest>true</IsLatest></{kind}>"
    ).encode()


def _page_xml(
    entries: bytes = b"",
    *,
    truncated: bool = False,
    key: str | None = None,
    next_key: str | None = None,
) -> bytes:
    marker = "" if key is None else key.replace("/", "%2F")
    next_marker = "" if next_key is None else next_key.replace("/", "%2F")
    return (
        (
            '<ListVersionsResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
            "<Name>history-probe</Name>"
            "<Prefix>checkpoints%2F11111111-1111-4111-8111-111111111111%2F</Prefix>"
            f"<KeyMarker>{marker}</KeyMarker><VersionIdMarker/>"
            "<MaxKeys>1000</MaxKeys><EncodingType>url</EncodingType>"
            f"<IsTruncated>{str(truncated).lower()}</IsTruncated>"
            f"<NextKeyMarker>{next_marker}</NextKeyMarker><NextVersionIdMarker/>"
        ).encode()
        + entries
        + b"</ListVersionsResult>"
    )


def _store_process(
    tmp_path: Path,
    program: str,
    payload: dict[str, object],
    *,
    store_definition: str = "",
) -> dict:
    """Inspect only a fresh test-owned store in a finite process, never pytest SQLite."""
    from easysynq_api.services.audit import _history_spool_store

    source = Path(_history_spool_store.__file__).resolve().parents[3]
    prelude = """
import json, os, sqlite3, sys
from uuid import UUID
sys.path.insert(0, sys.argv[1])
from easysynq_api.services.audit._history_spool_store import _SpoolStore
from easysynq_api.services.audit._history_spool_protocol import _SpoolWitness
from easysynq_api.services.audit.history_collection import (
    HistoryCollectionError, HistoryCollectionLimits,
)
payload = json.load(sys.stdin)
org = UUID("11111111-1111-4111-8111-111111111111")
scopes = tuple(_SpoolWitness(UUID(value), "a" * 64, "history-probe")
               for value in payload["witnesses"])
limits = HistoryCollectionLimits(16, 10000, 33554432, 33554432, 30, 32)
"""
    prelude += store_definition + "\nstore = _SpoolStore(org, scopes, limits)\n"
    completed = subprocess.run(  # noqa: S603 - fixed interpreter and test-owned inspection program
        [sys.executable, "-I", "-B", "-c", prelude + program, str(source)],
        input=json.dumps(payload).encode(),
        cwd=tmp_path,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    result = json.loads(completed.stdout)
    assert sorted(path.name for path in tmp_path.iterdir()) == ["spool.sqlite3"]
    (tmp_path / "spool.sqlite3").unlink()
    return result


def _leaves(error: BaseException) -> tuple[BaseException, ...]:
    if isinstance(error, BaseExceptionGroup):
        return tuple(leaf for child in error.exceptions for leaf in _leaves(child))
    return (error,)


def test_real_storage_retains_original_page_and_body_then_removes_owned_spool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from easysynq_api.services.audit._history_spool import _SpoolSession, _SpoolWitness
    from easysynq_api.services.audit.history_collection import HistoryCollectionLimits

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    limits = HistoryCollectionLimits(8, 16, 1_000_000, 1_048_576, 30, 32)
    scope = _SpoolWitness(WITNESS_ID, NAMESPACE_HASH, "history-probe")
    with _SpoolSession(
        ORG_ID,
        (scope,),
        limits,
        cancel=Event(),
        deadline=time.monotonic() + 30,
    ) as spool:
        ticket = spool.reserve_page(0, None, None)
        assert ticket is not None
        assert (ticket.page_id, ticket.witness_index, ticket.page_index) == (1, 0, 0)
        admitted = spool.admit_page(ticket, ORIGINAL_TERMINAL_XML)
        assert (admitted.first_ordinal, admitted.version_count, admitted.delete_count) == (1, 1, 0)
        spool.record_body(admitted.first_ordinal, ORIGINAL_BODY)
        result = spool.finish()

    assert len(result.witnesses) == 1
    witness = result.witnesses[0]
    assert (witness.witness_id, witness.namespace_hash) == (WITNESS_ID, NAMESPACE_HASH)
    assert witness.terminal_reached is True
    assert (witness.page_attempts, witness.admitted_pages) == (1, 1)
    assert (witness.version_observations, witness.delete_observations) == (1, 0)
    assert (witness.successful_reads, witness.unavailable_reads) == (1, 0)
    assert (witness.duplicate_body_deliveries, witness.conflicting_locators) == (0, 0)
    assert result.admitted_total_bytes == len(ORIGINAL_TERMINAL_XML) + len(ORIGINAL_BODY)
    assert result.issues == ()
    assert (result.failed_issues, result.incomplete_issues, result.issues_omitted) == (0, 0, 0)
    assert list(tmp_path.iterdir()) == []


def test_nonadjacent_exact_cursor_pair_stops_without_another_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from easysynq_api.services.audit._history_spool import _SpoolSession, _SpoolWitness
    from easysynq_api.services.audit.history_collection import HistoryCollectionLimits

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    limits = HistoryCollectionLimits(8, 16, 1_000_000, 1_048_576, 30, 32)
    scope = _SpoolWitness(WITNESS_ID, NAMESPACE_HASH, "history-probe")
    # The literal version label "null" and absent version marker are distinct.
    cursors = ((None, None), (PREFIX + "cursor", "null"), (PREFIX + "cursor", None))
    next_cursors = (cursors[1], cursors[2], cursors[1])
    with _SpoolSession(
        ORG_ID,
        (scope,),
        limits,
        cancel=Event(),
        deadline=time.monotonic() + 30,
    ) as spool:
        for index, (key, version) in enumerate(cursors):
            next_key, next_version = next_cursors[index]
            assert next_key is not None
            marker = "" if key is None else key.replace("/", "%2F")
            next_marker = next_key.replace("/", "%2F")
            page = (
                '<ListVersionsResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                "<Name>history-probe</Name>"
                "<Prefix>checkpoints%2F11111111-1111-4111-8111-111111111111%2F</Prefix>"
                f"<KeyMarker>{marker}</KeyMarker>"
                f"<VersionIdMarker>{version or ''}</VersionIdMarker>"
                "<MaxKeys>1000</MaxKeys><EncodingType>url</EncodingType>"
                "<IsTruncated>true</IsTruncated>"
                f"<NextKeyMarker>{next_marker}</NextKeyMarker>"
                f"<NextVersionIdMarker>{next_version or ''}</NextVersionIdMarker>"
                "<Version>"
                "<Key>checkpoints%2F11111111-1111-4111-8111-111111111111%2Fopaque</Key>"
                "<VersionId>null</VersionId><IsLatest>true</IsLatest>"
                "</Version>"
                "</ListVersionsResult>"
            ).encode()
            ticket = spool.reserve_page(0, key, version)
            assert ticket is not None
            admitted = spool.admit_page(ticket, page)
            assert (admitted.first_ordinal, admitted.version_count, admitted.delete_count) == (
                index + 1,
                1,
                0,
            )
            spool.record_body(admitted.first_ordinal, ORIGINAL_BODY)
        assert spool.reserve_page(0, PREFIX + "cursor", "null") is None
        spool.record_cycle(0)
        result = spool.finish()

    witness = result.witnesses[0]
    assert witness.terminal_reached is False
    assert (witness.page_attempts, witness.admitted_pages) == (3, 3)
    assert (witness.successful_reads, witness.duplicate_body_deliveries) == (3, 2)
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert (issue.code, issue.severity, issue.witness_id, issue.count) == (
        "CURSOR_CYCLE",
        "incomplete",
        WITNESS_ID,
        1,
    )
    assert issue.observation_ordinals == ()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("budget", ["body", "observations"])
def test_budget_exhaustion_poisons_session_and_never_returns_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    budget: str,
) -> None:
    from easysynq_api.services.audit._history_spool import _SpoolSession, _SpoolWitness
    from easysynq_api.services.audit.history_collection import (
        HistoryCollectionError,
        HistoryCollectionLimits,
    )

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    limits = HistoryCollectionLimits(8, 1, 1_000_000, 1_048_576, 30, 32)
    scope = _SpoolWitness(WITNESS_ID, NAMESPACE_HASH, "history-probe")
    if budget == "body":
        limits = replace(
            limits, maximum_total_bytes=len(ORIGINAL_TERMINAL_XML) + len(ORIGINAL_BODY) - 1
        )
    two_observations = ORIGINAL_TERMINAL_XML.replace(
        b"</ListVersionsResult>",
        b"<DeleteMarker>"
        b"<Key>checkpoints%2F11111111-1111-4111-8111-111111111111%2Fdeleted</Key>"
        b"<VersionId>delete-1</VersionId><IsLatest>false</IsLatest>"
        b"</DeleteMarker></ListVersionsResult>",
    )
    with _SpoolSession(
        ORG_ID,
        (scope,),
        limits,
        cancel=Event(),
        deadline=time.monotonic() + 30,
    ) as spool:
        ticket = spool.reserve_page(0, None, None)
        assert ticket is not None
        with pytest.raises(HistoryCollectionError) as caught:
            if budget == "body":
                admitted = spool.admit_page(ticket, ORIGINAL_TERMINAL_XML)
                spool.record_body(admitted.first_ordinal, ORIGINAL_BODY)
            else:
                spool.admit_page(ticket, two_observations)
        assert caught.value.code == "RESOURCE_LIMIT"
        with pytest.raises(HistoryCollectionError):
            spool.finish()
        with pytest.raises(HistoryCollectionError):
            spool.reserve_page(0, None, None)
    assert list(tmp_path.iterdir()) == []


def test_cancellation_arriving_with_terminal_error_wins_and_cleans_spool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from easysynq_api.services.audit import _history_spool_protocol as wire
    from easysynq_api.services.audit._history_spool import _SpoolSession, _SpoolWitness
    from easysynq_api.services.audit.history_collection import (
        HistoryCollectionCancelled,
        HistoryCollectionLimits,
    )

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    cancel = Event()
    limits = HistoryCollectionLimits(8, 16, 1_000_000, 1_048_576, 30, 32)
    scope = _SpoolWitness(WITNESS_ID, NAMESPACE_HASH, "history-probe")
    with _SpoolSession(
        ORG_ID,
        (scope,),
        limits,
        cancel=cancel,
        deadline=time.monotonic() + 30,
    ) as spool:
        original_receive = spool._receive
        saw_terminal_error = False

        def receive_then_cancel(deadline: float) -> bytes:
            nonlocal saw_terminal_error
            payload = original_receive(deadline)
            if wire.metadata(payload).get("op") == "ERROR":
                saw_terminal_error = True
                cancel.set()
            return payload

        monkeypatch.setattr(spool, "_receive", receive_then_cancel)
        with pytest.raises(HistoryCollectionCancelled):
            spool.reserve_page(3, None, None)
        assert saw_terminal_error
    assert list(tmp_path.iterdir()) == []


def test_selector_fatal_and_unregister_failure_preserve_both_identities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from easysynq_api.services.audit._history_spool import _SpoolSession, _SpoolWitness
    from easysynq_api.services.audit.history_collection import HistoryCollectionLimits

    fatal = KeyboardInterrupt("synthetic selector interruption")
    unregister_fault = RuntimeError("synthetic unregister failure")

    class FailingSelector:
        closed = False

        def register(self, _fd: int, _event: int) -> None:
            pass

        def select(self, _timeout: float) -> None:
            raise fatal

        def unregister(self, _fd: int) -> None:
            raise unregister_fault

        def close(self) -> None:
            self.closed = True

    limits = HistoryCollectionLimits(8, 16, 1_000_000, 1_048_576, 30, 32)
    scope = _SpoolWitness(WITNESS_ID, NAMESPACE_HASH, "history-probe")
    spool = _SpoolSession(ORG_ID, (scope,), limits, cancel=Event(), deadline=time.monotonic() + 30)
    selected = FailingSelector()
    # No OS resources are needed to exercise the simultaneous selector-failure seam.
    monkeypatch.setattr(spool, "_selector", selected)
    try:
        with pytest.raises(BaseExceptionGroup) as caught:
            spool._wait(123, selectors.EVENT_READ, time.monotonic() + 10)
        assert caught.value.exceptions == (fatal, unregister_fault)
    finally:
        assert spool._cleanup() == []
    assert selected.closed


def test_early_page_budget_rejection_preserves_resource_error_during_large_upload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from easysynq_api.services.audit._history_spool import _SpoolSession, _SpoolWitness
    from easysynq_api.services.audit.history_collection import (
        HistoryCollectionError,
        HistoryCollectionLimits,
    )

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    # A large bounded original upload cannot fit the pipe after the worker rejects
    # PAGE_BEGIN; the terminal RESOURCE_LIMIT must survive the ensuing pipe closure.
    original_xml = ORIGINAL_TERMINAL_XML + b" " * 1_048_576
    limits = HistoryCollectionLimits(8, 16, len(ORIGINAL_TERMINAL_XML), 2_097_152, 30, 32)
    scope = _SpoolWitness(WITNESS_ID, NAMESPACE_HASH, "history-probe")
    with _SpoolSession(
        ORG_ID,
        (scope,),
        limits,
        cancel=Event(),
        deadline=time.monotonic() + 30,
    ) as spool:
        ticket = spool.reserve_page(0, None, None)
        assert ticket is not None
        with pytest.raises(HistoryCollectionError) as caught:
            spool.admit_page(ticket, original_xml)
        assert caught.value.code == "RESOURCE_LIMIT"
        with pytest.raises(HistoryCollectionError):
            spool.finish()
    assert list(tmp_path.iterdir()) == []


def test_cancellation_during_controlled_error_cleanup_wins_after_all_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from easysynq_api.services.audit._history_spool import _SpoolSession, _SpoolWitness
    from easysynq_api.services.audit.history_collection import (
        HistoryCollectionCancelled,
        HistoryCollectionLimits,
    )

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    cancel = Event()
    limits = HistoryCollectionLimits(8, 16, 1_000_000, 1_048_576, 30, 32)
    scope = _SpoolWitness(WITNESS_ID, NAMESPACE_HASH, "history-probe")
    with _SpoolSession(
        ORG_ID,
        (scope,),
        limits,
        cancel=cancel,
        deadline=time.monotonic() + 30,
    ) as spool:
        original_cleanup = spool._cleanup
        cleanup_completed = False

        def cleanup_then_cancel() -> list[BaseException]:
            nonlocal cleanup_completed
            faults = original_cleanup()
            cleanup_completed = True
            cancel.set()
            return faults

        monkeypatch.setattr(spool, "_cleanup", cleanup_then_cancel)
        with pytest.raises(HistoryCollectionCancelled):
            spool.reserve_page(3, None, None)
        assert cleanup_completed
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "case", ["fatal-primary", "mixed-primary", "cleanup-primary", "controlled-with-cleanup-faults"]
)
def test_post_cleanup_cancellation_preserves_fatal_groups_and_cleanup_failures(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    from easysynq_api.services.audit._history_spool import _SpoolSession, _SpoolWitness
    from easysynq_api.services.audit.history_collection import (
        HistoryCollectionCancelled,
        HistoryCollectionError,
        HistoryCollectionLimits,
    )

    cancel = Event()
    limits = HistoryCollectionLimits(8, 16, 1_000_000, 1_048_576, 30, 32)
    scope = _SpoolWitness(WITNESS_ID, NAMESPACE_HASH, "history-probe")
    spool = _SpoolSession(ORG_ID, (scope,), limits, cancel=cancel, deadline=time.monotonic() + 30)
    fatal = KeyboardInterrupt("synthetic fatal identity")
    cleanup_failed = HistoryCollectionError("CLEANUP_FAILED")
    controlled = HistoryCollectionError("WORKER_FAILED")
    primary = {
        "fatal-primary": fatal,
        "mixed-primary": BaseExceptionGroup("synthetic mixed group", [controlled, fatal]),
        "cleanup-primary": cleanup_failed,
        "controlled-with-cleanup-faults": controlled,
    }[case]
    cleanup_faults = [fatal, cleanup_failed] if case == "controlled-with-cleanup-faults" else []

    def cleanup_then_cancel() -> list[BaseException]:
        cancel.set()
        return cleanup_faults

    monkeypatch.setattr(spool, "_cleanup", cleanup_then_cancel)
    with pytest.raises((KeyboardInterrupt, HistoryCollectionError, BaseExceptionGroup)) as caught:
        spool._abandon(primary)
    if case == "controlled-with-cleanup-faults":
        assert isinstance(caught.value, BaseExceptionGroup)
        assert type(caught.value.exceptions[0]) is HistoryCollectionCancelled
        assert caught.value.exceptions[1:] == (fatal, cleanup_failed)
    else:
        assert caught.value is primary


def test_store_retains_exact_rows_blob_cursors_and_witness_local_conflicts(tmp_path: Path) -> None:
    second_id = "33333333-3333-4333-8333-333333333333"
    original = _page_xml(
        _entry_xml(kind="DeleteMarker", key="gone", version="delete-1") + _entry_xml() * 3
    )
    other = _page_xml(_entry_xml())
    result = _store_process(
        tmp_path,
        """
try:
    for witness, page_hex in enumerate(payload["pages"]):
        ticket = store.reserve_page(witness, None, None)
        raw = bytes.fromhex(page_hex)
        store.page_begin(ticket, len(raw))
        store.page_chunk(raw)
        admitted = store.page_end()
        bodies = [b"opaque-A", b"opaque-A", b"opaque-B"] if witness == 0 else [b"opaque-C"]
        for index, body in enumerate(bodies):
            store.record_body(admitted.first_ordinal + index, body)
    db = store._connection()
    summary = store.finish()
    pages = [row[0].hex() for row in db.execute("SELECT raw FROM pages ORDER BY id")]
    cursors = [[w, k.hex(), v.hex()] for w,k,v in db.execute(
        "SELECT witness,cursor_key,cursor_version FROM cursors ORDER BY witness")]
    rows = [[ordinal,witness,key.hex(),version.hex(),kind,outcome,
             None if body is None else body.hex(),duplicate]
            for ordinal,witness,key,version,kind,outcome,body,duplicate in db.execute(
        "SELECT ordinal,witness,key,version,kind,outcome,body,duplicate "
        "FROM observations ORDER BY ordinal")]
    result = {"pages": pages, "cursors": cursors, "rows": rows,
              "conflicts": [w.conflicting_locators for w in summary.witnesses],
              "duplicates": [w.duplicate_body_deliveries for w in summary.witnesses],
              "issues": [[i.code,i.count,list(i.observation_ordinals)] for i in summary.issues]}
finally:
    store.close()
print(json.dumps(result))
""",
        {"witnesses": [str(WITNESS_ID), second_id], "pages": [original.hex(), other.hex()]},
    )
    key, version = (PREFIX + "opaque").encode().hex(), b"null".hex()
    assert result["pages"] == [original.hex(), other.hex()]
    assert result["cursors"] == [[0, "00", "00"], [1, "00", "00"]]
    assert result["rows"] == [
        [1, 0, key, version, "version", "body", b"opaque-A".hex(), 0],
        [2, 0, key, version, "version", "body", b"opaque-A".hex(), 1],
        [3, 0, key, version, "version", "body", b"opaque-B".hex(), 0],
        [4, 0, (PREFIX + "gone").encode().hex(), b"delete-1".hex(), "marker", "marker", None, 0],
        [5, 1, key, version, "version", "body", b"opaque-C".hex(), 0],
    ]
    assert result["conflicts"] == [1, 0]
    assert result["duplicates"] == [1, 0]
    assert result["issues"] == [["DELETE_OBSERVATION", 1, [4]], ["LOCATOR_CONFLICT", 1, [3]]]


def test_store_exact_body_equality_survives_an_index_hash_collision(tmp_path: Path) -> None:
    result = _store_process(
        tmp_path,
        """
from easysynq_api.services.audit import _history_spool_store as module
class CollidingHash:
    def digest(self):
        return b"x" * 32
module.hashlib.sha256 = lambda body: CollidingHash()
try:
    raw = bytes.fromhex(payload["page"])
    ticket = store.reserve_page(0,None,None)
    store.page_begin(ticket,len(raw)); store.page_chunk(raw)
    admitted = store.page_end()
    for index, body in enumerate((b"first", b"different", b"first")):
        store.record_body(admitted.first_ordinal + index, body)
    summary = store.finish().witnesses[0]
    result = {"duplicates": summary.duplicate_body_deliveries,
              "conflicts": summary.conflicting_locators}
finally:
    store.close()
print(json.dumps(result))
""",
        {"witnesses": [str(WITNESS_ID)], "page": _page_xml(_entry_xml() * 3).hex()},
    )
    assert result == {"duplicates": 1, "conflicts": 1}


def test_fixed_schema_extension_precedes_locked_authorizer(tmp_path: Path) -> None:
    result = _store_process(
        tmp_path,
        """
try:
    db = store._connection()
    assert db.execute("SELECT value FROM derived_example").fetchone() == (7,)
    assert db.execute("SELECT count(*) FROM witnesses").fetchone() == (1,)
    rejected = []
    for sql in ("CREATE TABLE rogue(value)", "DROP INDEX observation_locator",
                "INSERT INTO derived_example(value) VALUES(8)", "PRAGMA temp_store=FILE"):
        try: db.execute(sql)
        except sqlite3.DatabaseError: rejected.append(sql)
        else: raise AssertionError("schema initialization privilege escaped into operations")
    result = {"value": db.execute("SELECT value FROM derived_example").fetchone()[0],
              "denied": len(rejected)}
finally:
    store.close()
print(json.dumps(result))
""",
        {"witnesses": [str(WITNESS_ID)]},
        store_definition="""
base_store = _SpoolStore
class _SpoolStore(base_store):
    def _create_schema(self, db):
        super()._create_schema(db)
        db.execute("CREATE TABLE derived_example(value INTEGER NOT NULL)")
        db.execute("INSERT INTO derived_example(value) VALUES(7)")

    @staticmethod
    def _authorize(action, arg1, arg2, database, source):
        if action == sqlite3.SQLITE_READ and arg1 == "derived_example":
            return sqlite3.SQLITE_OK
        return base_store._authorize(action, arg1, arg2, database, source)
""",
    )
    assert result == {"value": 7, "denied": 4}
    assert list(tmp_path.iterdir()) == []


def test_store_policy_fixed_schema_authorizer_and_live_writable_descriptors(tmp_path: Path) -> None:
    result = _store_process(
        tmp_path,
        """
import fcntl, stat
def writable_regular_files():
    observed = []
    for name in os.listdir("/proc/self/fd"):
        try:
            fd = int(name)
            info = os.fstat(fd)
            flags = fcntl.fcntl(fd,fcntl.F_GETFL)
            if stat.S_ISREG(info.st_mode) and flags & os.O_ACCMODE != os.O_RDONLY:
                observed.append(os.readlink("/proc/self/fd/"+name))
        except FileNotFoundError:
            continue
        except OSError as error:
            if error.errno != 9: raise
    return sorted(observed)
try:
    db = store._connection()
    rejected = []
    for sql in ("ATTACH ':memory:' AS other", "DETACH other", "CREATE TABLE rogue(x)",
                "DROP TABLE observations", "PRAGMA temp_store=FILE", "VACUUM",
                "SELECT load_extension('untrusted')", "CREATE VIRTUAL TABLE rogue USING fts5(x)"):
        try: db.execute(sql)
        except sqlite3.DatabaseError: rejected.append(sql)
        else: raise AssertionError("forbidden SQL admitted")
    raw = bytes.fromhex(payload["page"])
    ticket = store.reserve_page(0,None,None)
    assert not db.in_transaction
    store.page_begin(ticket,len(raw))
    assert db.in_transaction
    transaction_fds = writable_regular_files()
    for offset in range(0,len(raw),65536): store.page_chunk(raw[offset:offset+65536])
    admitted = store.page_end()
    assert not db.in_transaction
    for index in range(admitted.version_count):
        store.record_body(admitted.first_ordinal + index, bytes([index % 251]) * 1024)
        assert not db.in_transaction
    sort_fds = []
    db.set_progress_handler(lambda: (sort_fds.extend(writable_regular_files()),0)[1],100)
    sorted_rows = list(db.execute("SELECT body FROM observations ORDER BY body"))
    db.set_progress_handler(None,0)
    summary = store.finish()
    # Read-only introspection of this same test-owned connection, never reopen.
    db.set_authorizer(None)
    names = ("page_size","max_page_count","journal_mode","temp_store","mmap_size","cache_size",
             "hard_heap_limit","synchronous","trusted_schema","threads","busy_timeout")
    settings = {name: db.execute("PRAGMA " + name).fetchone()[0] for name in names}
    tables = [row[0] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    columns = {name: [[row[1],row[2]] for row in db.execute("PRAGMA table_info(" + name + ")")]
               for name in ("cursors","observations")}
    indexes = [row[0] for row in db.execute(
        "SELECT sql FROM sqlite_master WHERE name='observation_locator'")]
    info = os.stat("spool.sqlite3")
    result = {"settings":settings,"tables":tables,"columns":columns,"indexes":indexes,
              "denied":len(rejected),"transaction_fds":transaction_fds,
              "sort_fds":sorted(set(sort_fds)),
              "mode":stat.S_IMODE(info.st_mode),"links":info.st_nlink,"uid":info.st_uid,
              "rows":len(sorted_rows),"reads":summary.witnesses[0].successful_reads,
              "limits":[db.getlimit(kind) for kind in (sqlite3.SQLITE_LIMIT_ATTACHED,
                  sqlite3.SQLITE_LIMIT_LENGTH,sqlite3.SQLITE_LIMIT_SQL_LENGTH,
                  sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER,sqlite3.SQLITE_LIMIT_COLUMN)]}
finally:
    store.close()
print(json.dumps(result))
""",
        {"witnesses": [str(WITNESS_ID)], "page": _page_xml(_entry_xml() * 1_000).hex()},
    )
    assert result["settings"] == {
        "page_size": 4096,
        "max_page_count": 8192,
        "journal_mode": "memory",
        "temp_store": 2,
        "mmap_size": 0,
        "cache_size": -1024,
        "hard_heap_limit": 67_108_864,
        "synchronous": 0,
        "trusted_schema": 0,
        "threads": 0,
        "busy_timeout": 0,
    }
    assert result["tables"] == [
        "cursors",
        "issues",
        "locators",
        "observations",
        "pages",
        "witnesses",
    ]
    assert result["columns"]["cursors"] == [
        ["witness", "INTEGER"],
        ["cursor_key", "BLOB"],
        ["cursor_version", "BLOB"],
    ]
    assert ["key", "BLOB"] in result["columns"]["observations"]
    assert ["version", "BLOB"] in result["columns"]["observations"]
    assert result["indexes"] == [
        "CREATE INDEX observation_locator ON observations(witness,key,version,digest)",
    ]
    assert result["denied"] == 8
    assert result["transaction_fds"] == result["sort_fds"] == [str(tmp_path / "spool.sqlite3")]
    assert (result["mode"], result["links"], result["uid"]) == (0o600, 1, os.geteuid())
    assert (result["rows"], result["reads"]) == (1000, 1000)
    assert result["limits"] == [0, 16_842_752, 8192, 32, 32]


def test_real_worker_maximum_xml_thousand_rows_and_maximum_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from easysynq_api.services.audit._history_spool import _SpoolSession, _SpoolWitness
    from easysynq_api.services.audit.history_collection import HistoryCollectionLimits

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    original = _page_xml(_entry_xml() * 1_000)
    original += b" " * (16_777_216 - len(original))
    body = bytes(range(256)) * 256
    limits = HistoryCollectionLimits(1, 1_000, 16_842_752, 33_554_432, 30, 32)
    scope = _SpoolWitness(WITNESS_ID, NAMESPACE_HASH, "history-probe")
    with _SpoolSession(
        ORG_ID,
        (scope,),
        limits,
        cancel=Event(),
        deadline=time.monotonic() + 30,
    ) as spool:
        ticket = spool.reserve_page(0, None, None)
        assert ticket is not None
        admission = spool.admit_page(ticket, original)
        assert (admission.first_ordinal, admission.version_count, admission.delete_count) == (
            1,
            1000,
            0,
        )
        spool.record_body(1, body)
        for ordinal in range(2, 1001):
            spool.record_version_failure(ordinal, "PROVIDER_FAILURE")
        result = spool.finish()
    assert result.admitted_total_bytes == 16_842_752
    assert result.witnesses[0].successful_reads == 1
    assert result.witnesses[0].unavailable_reads == 999
    assert result.issues[0].count == 999
    assert result.issues[0].observation_ordinals == (2, 3, 4, 5)
    assert list(tmp_path.iterdir()) == []


def test_required_witness_gaps_markers_and_display_cap_remain_sticky(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from easysynq_api.services.audit._history_spool import _SpoolSession, _SpoolWitness
    from easysynq_api.services.audit.history_collection import HistoryCollectionLimits

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    other_id = UUID("33333333-3333-4333-8333-333333333333")
    scopes = (
        _SpoolWitness(WITNESS_ID, NAMESPACE_HASH, "history-probe"),
        _SpoolWitness(other_id, NAMESPACE_HASH, "history-probe"),
    )
    original = _page_xml(
        _entry_xml() * 3
        + _entry_xml(key="control%01")
        + _entry_xml(kind="DeleteMarker", key="gone")
    )
    limits = HistoryCollectionLimits(8, 16, 1_000_000, 1_048_576, 30, 1)
    with _SpoolSession(
        ORG_ID,
        scopes,
        limits,
        cancel=Event(),
        deadline=time.monotonic() + 30,
    ) as spool:
        ticket = spool.reserve_page(0, None, None)
        assert ticket is not None
        spool.admit_page(ticket, original)
        spool.record_body(1, b"")
        spool.record_version_failure(2, "DELETE_MARKER", delete_marker=True)
        spool.record_version_failure(3, "PROVIDER_FAILURE")
        spool.record_version_failure(4, "INELIGIBLE_LOCATOR", ineligible=True)
        ticket = spool.reserve_page(1, None, None)
        assert ticket is not None
        spool.record_list_failure(ticket, "CURSOR_INVALID")
        result = spool.finish()
    assert [
        (w.terminal_reached, w.successful_reads, w.unavailable_reads, w.delete_observations)
        for w in result.witnesses
    ] == [(True, 1, 3, 1), (False, 0, 0, 0)]
    assert (result.failed_issues, result.incomplete_issues, result.issues_omitted) == (1, 3, 3)
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert (issue.witness_id, issue.code, issue.count, issue.observation_ordinals) == (
        WITNESS_ID,
        "DELETE_OBSERVATION",
        2,
        (5, 2),
    )
    assert "control" not in repr(result)
    assert "opaque" not in repr(result)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "unfinished", ["missing-witness", "pending-page", "pending-version", "nonterminal"]
)
def test_finish_rejects_each_unresolved_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unfinished: str,
) -> None:
    from easysynq_api.services.audit._history_spool import _SpoolSession, _SpoolWitness
    from easysynq_api.services.audit.history_collection import (
        HistoryCollectionError,
        HistoryCollectionLimits,
    )

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    limits = HistoryCollectionLimits(8, 16, 1_000_000, 1_048_576, 30, 32)
    scope = _SpoolWitness(WITNESS_ID, NAMESPACE_HASH, "history-probe")
    with _SpoolSession(
        ORG_ID,
        (scope,),
        limits,
        cancel=Event(),
        deadline=time.monotonic() + 30,
    ) as spool:
        if unfinished != "missing-witness":
            ticket = spool.reserve_page(0, None, None)
            assert ticket is not None
            if unfinished != "pending-page":
                original = (
                    _page_xml(_entry_xml(), truncated=True, next_key=PREFIX + "next")
                    if unfinished == "nonterminal"
                    else ORIGINAL_TERMINAL_XML
                )
                spool.admit_page(ticket, original)
                if unfinished == "nonterminal":
                    spool.record_body(1, ORIGINAL_BODY)
        with pytest.raises(HistoryCollectionError) as caught:
            spool.finish()
        assert caught.value.code == "PROTOCOL_INVALID"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("case", ["page-budget", "file-budget", "oversize-xml", "oversize-body"])
def test_additional_hard_bounds_abandon_the_whole_spool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    from easysynq_api.services.audit._history_spool import _SpoolSession, _SpoolWitness
    from easysynq_api.services.audit.history_collection import (
        HistoryCollectionError,
        HistoryCollectionLimits,
    )

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    limits = HistoryCollectionLimits(
        1, 16, 33_554_432, 65_536 if case == "file-budget" else 33_554_432, 30, 32
    )
    scope = _SpoolWitness(WITNESS_ID, NAMESPACE_HASH, "history-probe")
    with _SpoolSession(
        ORG_ID,
        (scope,),
        limits,
        cancel=Event(),
        deadline=time.monotonic() + 30,
    ) as spool:
        ticket = spool.reserve_page(0, None, None)
        assert ticket is not None
        with pytest.raises(HistoryCollectionError) as caught:
            if case == "page-budget":
                spool.admit_page(
                    ticket, _page_xml(_entry_xml(), truncated=True, next_key=PREFIX + "next")
                )
                spool.record_body(1, ORIGINAL_BODY)
                spool.reserve_page(0, PREFIX + "next", None)
            elif case == "file-budget":
                spool.admit_page(ticket, ORIGINAL_TERMINAL_XML + b" " * 131_072)
            elif case == "oversize-xml":
                spool.admit_page(ticket, b" " * 16_777_217)
            else:
                spool.admit_page(ticket, ORIGINAL_TERMINAL_XML)
                spool.record_body(1, b"x" * 65_537)
        assert caught.value.code == (
            "RESOURCE_LIMIT" if case.endswith("budget") else "PROTOCOL_INVALID"
        )
        with pytest.raises(HistoryCollectionError):
            spool.finish()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "mutation",
    [
        "unavailable-no-issues",
        "unavailable-wrong-count",
        "listed-and-get-marker-undercount",
        "rows-above-page-cap",
        "false-severity-total",
        "terminal-gap",
        "pending-body-count",
    ],
)
def test_final_summary_rejects_impossible_semantics(mutation: str) -> None:
    from easysynq_api.services.audit._history_spool_protocol import _SpoolWitness, decode_summary
    from easysynq_api.services.audit.history_collection import (
        HistoryCollectionError,
        HistoryCollectionLimits,
    )

    witness = {
        "witness_id": str(WITNESS_ID),
        "namespace_hash": NAMESPACE_HASH,
        "terminal_reached": True,
        "page_attempts": 1,
        "admitted_pages": 1,
        "version_observations": 1,
        "delete_observations": 0,
        "successful_reads": 0,
        "unavailable_reads": 1,
        "duplicate_body_deliveries": 0,
        "conflicting_locators": 0,
    }
    issue = {
        "witness_id": str(WITNESS_ID),
        "code": "VERSION_UNAVAILABLE",
        "severity": "incomplete",
        "count": 1,
        "observation_ordinals": [1],
    }
    summary = {
        "witnesses": [witness],
        "issues": [issue],
        "failed_issues": 0,
        "incomplete_issues": 1,
        "issues_omitted": 0,
        "admitted_total_bytes": 500,
    }
    if mutation == "unavailable-no-issues":
        summary["issues"] = []
        summary["incomplete_issues"] = 0
    elif mutation == "unavailable-wrong-count":
        issue["count"] = 2
    elif mutation == "listed-and-get-marker-undercount":
        witness["delete_observations"] = 1
        issue.update(code="DELETE_OBSERVATION", severity="failed")
        summary.update(failed_issues=1, incomplete_issues=0)
    elif mutation == "rows-above-page-cap":
        witness.update(version_observations=1001, unavailable_reads=1001)
        issue["count"] = 1001
    elif mutation == "false-severity-total":
        issue.update(code="DELETE_OBSERVATION", severity="failed")
    elif mutation == "terminal-gap":
        issue["code"] = "LIST_UNAVAILABLE"
        issue["observation_ordinals"] = []
    else:
        witness["version_observations"] = 2
    limits = HistoryCollectionLimits(2, 2000, 1_000_000, 1_048_576, 30, 32)
    scopes = (_SpoolWitness(WITNESS_ID, NAMESPACE_HASH, "history-probe"),)
    with pytest.raises(HistoryCollectionError) as caught:
        decode_summary(summary, scopes, limits)
    assert caught.value.code == "PROTOCOL_INVALID"


def test_page_admission_rejects_ordinal_range_beyond_observation_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from easysynq_api.services.audit._history_spool import _SpoolSession, _SpoolWitness
    from easysynq_api.services.audit.history_collection import (
        HistoryCollectionError,
        HistoryCollectionLimits,
    )

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    scope = _SpoolWitness(WITNESS_ID, NAMESPACE_HASH, "history-probe")
    limits = HistoryCollectionLimits(8, 16, 1_000_000, 1_048_576, 30, 32)
    with _SpoolSession(
        ORG_ID,
        (scope,),
        limits,
        cancel=Event(),
        deadline=time.monotonic() + 30,
    ) as spool:
        ticket = spool.reserve_page(0, None, None)
        assert ticket is not None
        monkeypatch.setattr(
            spool,
            "_rpc",
            lambda *args, **kwargs: {
                "admission": {"first_ordinal": 16, "version_count": 2, "delete_count": 0},
            },
        )
        with pytest.raises(HistoryCollectionError) as caught:
            spool.admit_page(ticket, ORIGINAL_TERMINAL_XML)
        assert caught.value.code == "PROTOCOL_INVALID"
    assert list(tmp_path.iterdir()) == []


def _literal_frame(value: dict | bytes) -> bytes:
    payload = (
        b"J" + json.dumps(value, separators=(",", ":")).encode()
        if isinstance(value, dict)
        else value
    )
    return len(payload).to_bytes(4, "big") + payload


def _raw_worker(
    tmp_path: Path, requests: list[dict | bytes], *, tail: bytes = b""
) -> tuple[int, list[dict]]:
    from easysynq_api.services.audit import _history_spool

    worker = Path(_history_spool.__file__).with_name("_history_spool_worker.py").resolve()
    source = worker.parents[3]
    init = {
        "op": "INIT",
        "id": 1,
        "version": 1,
        "org_id": str(ORG_ID),
        "witnesses": [
            {
                "witness_id": str(WITNESS_ID),
                "namespace_hash": NAMESPACE_HASH,
                "bucket": "history-probe",
            },
        ],
        "limits": {
            "maximum_pages": 8,
            "maximum_observations": 16,
            "maximum_total_bytes": 1000000,
            "maximum_spool_bytes": 1048576,
            "maximum_wall_seconds": 30,
            "maximum_issues": 32,
        },
    }
    completed = subprocess.run(  # noqa: S603 - fixed isolated storage-worker executable
        [sys.executable, "-I", "-B", "-u", str(worker), str(source)],
        input=b"".join(_literal_frame(value) for value in [init, *requests]) + tail,
        cwd=tmp_path,
        capture_output=True,
        timeout=15,
        check=False,
        env={"LANG": "C.UTF-8", "TZ": "UTC"},
        start_new_session=True,
    )
    frames = []
    output = completed.stdout
    while output:
        assert len(output) >= 4
        size = int.from_bytes(output[:4], "big")
        assert 0 < size <= 131_072 and len(output) >= size + 4
        payload, output = output[4 : 4 + size], output[4 + size :]
        assert payload[:1] == b"J"
        frames.append(json.loads(payload[1:]))
    assert frames[:2] == [{"op": "READY", "version": 1}, {"op": "ACK", "id": 1, "version": 1}]
    assert completed.stderr == b""
    assert sorted(path.name for path in tmp_path.iterdir()) == ["spool.sqlite3"]
    (tmp_path / "spool.sqlite3").unlink()
    return completed.returncode, frames


@pytest.mark.parametrize(
    "metadata_request",
    [
        {"op": "SQL", "id": 2, "sql": "SELECT 1"},
        {"op": "RESERVE_PAGE", "id": 2, "witness": 0, "key": None, "version": None, "unknown": 1},
        {"op": "RESERVE_PAGE", "id": True, "witness": 0, "key": None, "version": None},
        {"op": "RESERVE_PAGE", "id": 1, "witness": 0, "key": None, "version": None},
        {"op": "RESERVE_PAGE", "id": 3, "witness": 0, "key": None, "version": None},
        {"op": "FINISH", "id": 2},
        b'J{"op":"FINISH","id":2,"id":2}',
        b'J{"op":"FINISH","id":2} {}',
        b'J{"op":"RESERVE_PAGE","id":2,"witness":NaN,"key":null,"version":null}',
        b'J{"op":"FINISH","id":2,"nested":' + b"[" * 20 + b"0" + b"]" * 20 + b"}",
        b'J{"op":"FINISH","id":2,"invalid":"\xff"}',
    ],
)
def test_raw_worker_rejects_metadata_and_sequence_violations(
    tmp_path: Path,
    metadata_request: dict | bytes,
) -> None:
    status, frames = _raw_worker(tmp_path, [metadata_request])
    assert status != 0
    assert all("summary" not in frame for frame in frames)


@pytest.mark.parametrize(
    "mutation", ["short", "long", "tag", "offset", "sequence", "oversized", "early-end"]
)
def test_raw_worker_rejects_page_chunk_violations(tmp_path: Path, mutation: str) -> None:
    binary = b"B" + (3).to_bytes(8, "big") + (0).to_bytes(4, "big") + b"abcd"
    if mutation == "short":
        binary = binary[:-2]
    elif mutation == "long":
        binary += b"e"
    elif mutation == "tag":
        binary = b"X" + binary[1:]
    elif mutation == "offset":
        binary = binary[:9] + (1).to_bytes(4, "big") + binary[13:]
    elif mutation == "sequence":
        binary = b"B" + (4).to_bytes(8, "big") + binary[9:]
    elif mutation == "oversized":
        binary = binary[:13] + b"x" * 65_537
    else:
        binary = b'J{"op":"PAGE_END","id":3}'
    status, frames = _raw_worker(
        tmp_path,
        [
            {"op": "RESERVE_PAGE", "id": 2, "witness": 0, "key": None, "version": None},
            {
                "op": "PAGE_BEGIN",
                "id": 3,
                "ticket": {"page_id": 1, "witness_index": 0, "page_index": 0},
                "length": 4,
            },
            binary,
        ],
    )
    assert status != 0
    assert not any("admission" in frame or "summary" in frame for frame in frames)


@pytest.mark.parametrize("tail", [b"\0", b"\0\0\0\0", (131073).to_bytes(4, "big"), b"\0\0\0\x04J{"])
def test_raw_worker_rejects_truncated_or_oversized_frames(tmp_path: Path, tail: bytes) -> None:
    status, frames = _raw_worker(tmp_path, [], tail=tail)
    assert status != 0
    assert all("summary" not in frame for frame in frames)


@pytest.mark.parametrize(
    "case",
    ["ticket-reuse", "outcome-reuse", "pending-outcome", "unreserved-cycle", "failure-reuse"],
)
def test_consumed_tickets_outcomes_and_out_of_order_commands_poison_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    from easysynq_api.services.audit._history_spool import _SpoolSession, _SpoolWitness
    from easysynq_api.services.audit.history_collection import (
        HistoryCollectionError,
        HistoryCollectionLimits,
    )

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    limits = HistoryCollectionLimits(8, 16, 1_000_000, 1_048_576, 30, 32)
    scope = _SpoolWitness(WITNESS_ID, NAMESPACE_HASH, "history-probe")
    with _SpoolSession(
        ORG_ID,
        (scope,),
        limits,
        cancel=Event(),
        deadline=time.monotonic() + 30,
    ) as spool:
        ticket = spool.reserve_page(0, None, None)
        assert ticket is not None
        if case == "failure-reuse":
            spool.record_list_failure(ticket, "PROVIDER_FAILURE")
        elif case != "unreserved-cycle":
            spool.admit_page(ticket, ORIGINAL_TERMINAL_XML)
        with pytest.raises(HistoryCollectionError) as caught:
            if case == "ticket-reuse":
                spool.admit_page(ticket, ORIGINAL_TERMINAL_XML)
            elif case == "outcome-reuse":
                spool.record_body(1, ORIGINAL_BODY)
                spool.record_body(1, ORIGINAL_BODY)
            elif case == "pending-outcome":
                spool.reserve_page(0, PREFIX + "next", None)
            elif case == "unreserved-cycle":
                spool.record_cycle(0)
            else:
                spool.record_list_failure(ticket, "PROVIDER_FAILURE")
        assert caught.value.code == "PROTOCOL_INVALID"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "kind,code,ineligible,delete_marker",
    [
        ("list", "CLEANUP_FAILED", False, False),
        ("list", "provider exception text", False, False),
        ("version", "CLEANUP_FAILED", False, False),
        ("version", "provider exception text", False, False),
        ("version", "DELETE_MARKER", False, False),
        ("version", "PROVIDER_FAILURE", False, True),
        ("version", "INELIGIBLE_LOCATOR", False, False),
        ("version", "INELIGIBLE_LOCATOR", True, True),
        ("version", "PROVIDER_FAILURE", True, False),
        ("version", "PROVIDER_FAILURE", 1, False),
    ],
)
def test_fixed_failure_codes_reject_arbitrary_text_cleanup_and_impossible_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    code: str,
    ineligible: bool | int,
    delete_marker: bool,
) -> None:
    from easysynq_api.services.audit._history_spool import _SpoolSession, _SpoolWitness
    from easysynq_api.services.audit.history_collection import (
        HistoryCollectionError,
        HistoryCollectionLimits,
    )

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    limits = HistoryCollectionLimits(8, 16, 1_000_000, 1_048_576, 30, 32)
    scope = _SpoolWitness(WITNESS_ID, NAMESPACE_HASH, "history-probe")
    with _SpoolSession(
        ORG_ID,
        (scope,),
        limits,
        cancel=Event(),
        deadline=time.monotonic() + 30,
    ) as spool:
        ticket = spool.reserve_page(0, None, None)
        assert ticket is not None
        if kind == "version":
            spool.admit_page(ticket, ORIGINAL_TERMINAL_XML)
        with pytest.raises(HistoryCollectionError) as caught:
            if kind == "list":
                spool.record_list_failure(ticket, code)
            else:
                spool.record_version_failure(
                    1, code, ineligible=ineligible, delete_marker=delete_marker
                )
        assert caught.value.code == "PROTOCOL_INVALID"
    assert list(tmp_path.iterdir()) == []


def test_real_worker_late_conflict_and_duplicate_after_4096_observations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from easysynq_api.services.audit._history_spool import _SpoolSession, _SpoolWitness
    from easysynq_api.services.audit.history_collection import HistoryCollectionLimits

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    limits = HistoryCollectionLimits(5, 5000, 5_000_000, 8_388_608, 60, 32)
    scope = _SpoolWitness(WITNESS_ID, NAMESPACE_HASH, "history-probe")
    with _SpoolSession(
        ORG_ID,
        (scope,),
        limits,
        cancel=Event(),
        deadline=time.monotonic() + 60,
    ) as spool:
        for index in range(5):
            key = None if index == 0 else PREFIX + f"cursor-{index}"
            next_key = None if index == 4 else PREFIX + f"cursor-{index + 1}"
            raw = _page_xml(_entry_xml() * 1000, truncated=index != 4, key=key, next_key=next_key)
            ticket = spool.reserve_page(0, key, None)
            assert ticket is not None
            admission = spool.admit_page(ticket, raw)
            assert admission.first_ordinal == index * 1000 + 1
            for ordinal in range(admission.first_ordinal, admission.first_ordinal + 1000):
                spool.record_body(
                    ordinal, b"late different" if ordinal == 4097 else b"opaque original"
                )
        result = spool.finish()
    witness = result.witnesses[0]
    assert (
        witness.successful_reads,
        witness.duplicate_body_deliveries,
        witness.conflicting_locators,
    ) == (5000, 4998, 1)
    assert result.issues[0].code == "LOCATOR_CONFLICT"
    assert result.issues[0].observation_ordinals == (4097,)
    assert list(tmp_path.iterdir()) == []


def test_real_worker_applies_os_limits_private_modes_and_fully_reaps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import resource
    import stat

    from easysynq_api.services.audit._history_spool import _SpoolSession, _SpoolWitness
    from easysynq_api.services.audit.history_collection import HistoryCollectionLimits

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    limits = HistoryCollectionLimits(8, 16, 1_000_000, 1_048_576, 30, 32)
    scope = _SpoolWitness(WITNESS_ID, NAMESPACE_HASH, "history-probe")
    with _SpoolSession(
        ORG_ID,
        (scope,),
        limits,
        cancel=Event(),
        deadline=time.monotonic() + 30,
    ) as spool:
        process = spool._process
        assert process is not None and spool._directory is not None
        expected = {
            resource.RLIMIT_AS: 536_870_912,
            resource.RLIMIT_CPU: 120,
            resource.RLIMIT_NOFILE: 32,
            resource.RLIMIT_CORE: 0,
            resource.RLIMIT_FSIZE: 1_048_576,
        }
        assert {kind: resource.prlimit(process.pid, kind) for kind in expected} == {
            kind: (value, value) for kind, value in expected.items()
        }
        directory = Path(spool._directory)
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
        assert stat.S_IMODE((directory / "spool.sqlite3").stat().st_mode) == 0o600
        proc = Path(f"/proc/{process.pid}")
        assert (proc / "cmdline").read_bytes().split(b"\0")[1:4] == [b"-I", b"-B", b"-u"]
        environment = dict(
            item.split(b"=", 1)
            for item in (proc / "environ").read_bytes().rstrip(b"\0").split(b"\0")
        )
        names = sorted(name.decode(errors="replace") for name in environment)
        assert names == ["LANG", "TZ"], names
        assert environment[b"LANG"] == b"C.UTF-8"
        assert environment[b"TZ"] == b"UTC"
        assert os.readlink(proc / "cwd") == str(directory)
        assert (proc / "task" / str(process.pid) / "children").read_text() == ""
        assert not any("socket:" in os.readlink(fd) for fd in (proc / "fd").iterdir())
        ticket = spool.reserve_page(0, None, None)
        assert ticket is not None
        admission = spool.admit_page(ticket, _page_xml())
        assert (admission.first_ordinal, admission.version_count, admission.delete_count) == (
            0,
            0,
            0,
        )
        result = spool.finish()
        assert result.witnesses[0].terminal_reached
        assert process.returncode == 0
        assert process.stdin is not None and process.stdin.closed
        assert process.stdout is not None and process.stdout.closed
        assert spool._selector is None and spool._directory is None
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("milestone", ["committed-prefix", "open-upload"])
def test_storage_death_discards_committed_prefix_and_pending_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    milestone: str,
) -> None:
    from easysynq_api.services.audit import _history_spool_protocol as wire
    from easysynq_api.services.audit._history_spool import _SpoolSession, _SpoolWitness
    from easysynq_api.services.audit.history_collection import (
        HistoryCollectionError,
        HistoryCollectionLimits,
    )

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    scope = _SpoolWitness(WITNESS_ID, NAMESPACE_HASH, "history-probe")
    limits = HistoryCollectionLimits(8, 16, 8_388_608, 8_388_608, 30, 32)
    with _SpoolSession(
        ORG_ID,
        (scope,),
        limits,
        cancel=Event(),
        deadline=time.monotonic() + 30,
    ) as spool:
        ticket = spool.reserve_page(0, None, None)
        assert ticket is not None
        raw = _page_xml(_entry_xml(), truncated=True, next_key=PREFIX + "next")
        spool.admit_page(ticket, raw)
        spool.record_body(1, ORIGINAL_BODY)
        process = spool._process
        assert process is not None and spool._directory is not None
        if milestone == "open-upload":
            ticket = spool.reserve_page(0, PREFIX + "next", None)
            assert ticket is not None
            database = Path(spool._directory) / "spool.sqlite3"
            committed_size = database.stat().st_size
            spool._sequence += 1
            spool._send(
                wire.encode(
                    {
                        "op": "PAGE_BEGIN",
                        "id": spool._sequence,
                        "ticket": {"page_id": ticket.page_id, "witness_index": 0, "page_index": 1},
                        "length": 2_097_152,
                    }
                ),
                time.monotonic() + 10,
            )
            observation_deadline = time.monotonic() + 5
            while (
                database.stat().st_size <= committed_size
                and time.monotonic() < observation_deadline
            ):
                time.sleep(0.01)
            assert database.stat().st_size > committed_size
            # No page bytes or PAGE_END were sent: this live growth belongs to the
            # zeroblob transaction, which cannot have committed an admitted page.
        os.killpg(process.pid, signal.SIGKILL)
        with pytest.raises(HistoryCollectionError):
            spool.finish()
        assert process.returncode is not None
        with pytest.raises(HistoryCollectionError):
            spool.reserve_page(0, PREFIX + "next", None)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("stop", ["cancel", "whole-deadline", "upload-deadline"])
def test_upload_checks_shared_cancellation_and_one_absolute_command_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stop: str,
) -> None:
    from easysynq_api.services.audit import _history_spool as module
    from easysynq_api.services.audit.history_collection import (
        HistoryCollectionCancelled,
        HistoryCollectionError,
        HistoryCollectionLimits,
    )

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    cancel = Event()
    limits = HistoryCollectionLimits(8, 16, 8_388_608, 8_388_608, 30, 32)
    scope = module._SpoolWitness(WITNESS_ID, NAMESPACE_HASH, "history-probe")
    with module._SpoolSession(
        ORG_ID,
        (scope,),
        limits,
        cancel=cancel,
        deadline=time.monotonic() + 30,
    ) as spool:
        ticket = spool.reserve_page(0, None, None)
        assert ticket is not None
        original_send = spool._send_upload
        clock_offset = 0.0
        calls = 0
        monkeypatch.setattr(module, "_monotonic", lambda: time.monotonic() + clock_offset)

        def advance_after_chunk(payload: bytes, deadline: float, request_id: int) -> None:
            nonlocal clock_offset, calls
            original_send(payload, deadline, request_id)
            calls += 1
            if stop == "cancel":
                cancel.set()
            else:
                clock_offset += 40 if stop == "whole-deadline" else 4

        monkeypatch.setattr(spool, "_send_upload", advance_after_chunk)
        expected = HistoryCollectionCancelled if stop == "cancel" else HistoryCollectionError
        with pytest.raises(expected) as caught:
            spool.admit_page(ticket, ORIGINAL_TERMINAL_XML + b" " * 1_048_576)
        if stop != "cancel":
            assert caught.value.code == "DEADLINE_EXCEEDED"
        assert calls == (3 if stop == "upload-deadline" else 1)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("constraint", ["address-space", "descriptors", "cpu"])
def test_worker_limit_function_is_actually_enforced_in_finite_child(
    tmp_path: Path,
    constraint: str,
) -> None:
    from easysynq_api.services.audit import _history_spool_worker

    program = """
import errno, importlib.util, os, resource, sys
spec = importlib.util.spec_from_file_location("limited_worker",sys.argv[1])
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)
worker._apply_limits()
kind = sys.argv[2]
if kind == "address-space":
    try: allocation = bytearray(600000000)
    except MemoryError: print("address-space enforced")
    else: raise AssertionError("address-space ceiling not enforced")
elif kind == "descriptors":
    opened = []
    try:
        for _ in range(64):
            try: opened.append(os.open(os.devnull,os.O_RDONLY))
            except OSError as error:
                assert error.errno == errno.EMFILE
                assert len(opened) < 32
                print("descriptors enforced")
                break
        else: raise AssertionError("descriptor ceiling not enforced")
    finally:
        for fd in opened: os.close(fd)
else:
    assert resource.getrlimit(resource.RLIMIT_CPU) == (120,120)
    resource.setrlimit(resource.RLIMIT_CPU,(1,1))
    while True: pass
"""
    result = subprocess.run(  # noqa: S603 - fixed interpreter and finite resource-control program
        [sys.executable, "-I", "-B", "-c", program, _history_spool_worker.__file__, constraint],
        cwd=tmp_path,
        capture_output=True,
        timeout=8,
        check=False,
    )
    if constraint == "cpu":
        assert result.returncode == -signal.SIGKILL
    else:
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        assert result.stdout == f"{constraint} enforced\n".encode()
    assert list(tmp_path.iterdir()) == []


def test_forced_sqlite_heap_failure_is_resource_limit_and_poisons_store(tmp_path: Path) -> None:
    result = _store_process(
        tmp_path,
        """
db = store._connection()
db.set_authorizer(None)
assert db.execute("PRAGMA hard_heap_limit").fetchone()[0] == 67108864
try:
    db.execute("PRAGMA hard_heap_limit=1").fetchone()
except MemoryError:
    # Setting the cap can succeed before preparing its own result fails.
    pass
finally:
    db.set_authorizer(store._authorize)
try:
    # A new statement cannot allocate under this test-process-only one-byte cap.
    # Catch the first guarded operation; zeroblob streaming alone is not a heap proof.
    try: store.reserve_page(0,None,None)
    except BaseException as error:
        outcome = getattr(error,"code",type(error).__name__)
    else: raise AssertionError("heap ceiling was not reached")
    try: store.finish()
    except HistoryCollectionError: poisoned = True
    else: poisoned = False
    result = {"code":outcome,"poisoned":poisoned,"closed":store._closed}
finally:
    store.close()
print(json.dumps(result))
""",
        {"witnesses": [str(WITNESS_ID)]},
    )
    assert result == {"code": "RESOURCE_LIMIT", "poisoned": True, "closed": True}


@pytest.mark.parametrize("failure", ["spawn", "selector-setup"])
def test_enter_failure_reaps_any_worker_and_removes_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    from easysynq_api.services.audit import _history_spool as module
    from easysynq_api.services.audit.history_collection import (
        HistoryCollectionError,
        HistoryCollectionLimits,
    )

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    sentinel = RuntimeError("synthetic selector setup failure")

    def fail(*args: object, **kwargs: object) -> None:
        if failure == "spawn":
            raise OSError("synthetic process creation failure")
        raise sentinel

    if failure == "spawn":
        monkeypatch.setattr(module.subprocess, "Popen", fail)
    else:
        monkeypatch.setattr(module.selectors, "DefaultSelector", fail)
    limits = HistoryCollectionLimits(8, 16, 1_000_000, 1_048_576, 30, 32)
    scope = module._SpoolWitness(WITNESS_ID, NAMESPACE_HASH, "history-probe")
    session = module._SpoolSession(
        ORG_ID, (scope,), limits, cancel=Event(), deadline=time.monotonic() + 30
    )
    with pytest.raises(HistoryCollectionError if failure == "spawn" else RuntimeError) as caught:
        session.__enter__()
    if failure == "spawn":
        assert caught.value.code == "WORKER_START_FAILED"
    else:
        assert caught.value is sentinel
        assert session._process is not None and session._process.returncode is not None
    assert list(tmp_path.iterdir()) == []


def test_cleanup_attempts_all_resources_and_preserves_unexpected_identities(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from easysynq_api.services.audit import _history_spool as module
    from easysynq_api.services.audit.history_collection import (
        HistoryCollectionError,
        HistoryCollectionLimits,
    )

    events = []
    selector_fault = RuntimeError("synthetic selector close failure")
    pipe_fault = KeyboardInterrupt("synthetic pipe close failure")

    class Selector:
        def close(self) -> None:
            events.append("selector")
            raise selector_fault

    class Stream:
        closed = False

        def __init__(self, name: str) -> None:
            self.name = name

        def close(self) -> None:
            events.append(self.name)
            if self.name == "stdin":
                raise pipe_fault
            self.closed = True

    class Process:
        pid = 123
        returncode = None
        stdin = Stream("stdin")
        stdout = Stream("stdout")

        def wait(self, *, timeout: float) -> int:
            assert timeout == 2.0
            events.append("wait")
            self.returncode = 0
            return 0

    def kill(pid: int, sig: int) -> None:
        assert (pid, sig) == (123, signal.SIGKILL)
        events.append("kill")
        raise OSError("synthetic signal failure")

    limits = HistoryCollectionLimits(8, 16, 1_000_000, 1_048_576, 30, 32)
    scope = module._SpoolWitness(WITNESS_ID, NAMESPACE_HASH, "history-probe")
    session = module._SpoolSession(
        ORG_ID, (scope,), limits, cancel=Event(), deadline=time.monotonic() + 30
    )
    directory = tmp_path / "owned"
    directory.mkdir(mode=0o700)
    info = directory.stat()
    monkeypatch.setattr(session, "_directory", str(directory))
    monkeypatch.setattr(session, "_identity", (info.st_dev, info.st_ino))
    monkeypatch.setattr(session, "_selector", Selector())
    monkeypatch.setattr(session, "_process", Process())
    monkeypatch.setattr(module, "_killpg", kill)
    faults = session._cleanup()
    assert events == ["selector", "stdin", "stdout", "kill", "wait"]
    assert selector_fault in faults and pipe_fault in faults
    assert any(
        isinstance(error, HistoryCollectionError) and error.code == "CLEANUP_FAILED"
        for error in faults
    )
    assert not directory.exists()


def test_cleanup_never_signals_released_pid_or_removes_unreaped_worker_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from easysynq_api.services.audit import _history_spool as module
    from easysynq_api.services.audit.history_collection import (
        HistoryCollectionError,
        HistoryCollectionLimits,
    )

    calls = []

    class Process:
        pid = 123
        returncode = None
        stdin = None
        stdout = None

        def wait(self, *, timeout: float) -> int:
            calls.append("wait")
            raise subprocess.TimeoutExpired("synthetic worker", timeout)

    limits = HistoryCollectionLimits(8, 16, 1_000_000, 1_048_576, 30, 32)
    scope = module._SpoolWitness(WITNESS_ID, NAMESPACE_HASH, "history-probe")
    session = module._SpoolSession(
        ORG_ID, (scope,), limits, cancel=Event(), deadline=time.monotonic() + 30
    )
    directory = tmp_path / "owned"
    directory.mkdir(mode=0o700)
    info = directory.stat()
    process = Process()
    monkeypatch.setattr(session, "_directory", str(directory))
    monkeypatch.setattr(session, "_identity", (info.st_dev, info.st_ino))
    monkeypatch.setattr(session, "_process", process)
    monkeypatch.setattr(module, "_killpg", lambda pid, sig: calls.append("kill"))
    faults = session._cleanup()
    assert calls == ["kill", "wait"]
    assert directory.exists()
    assert any(
        isinstance(error, HistoryCollectionError) and error.code == "CLEANUP_FAILED"
        for error in faults
    )
    process.returncode = 0  # Test fake now represents a PID already released by wait.
    assert session._cleanup() == []
    assert calls == ["kill", "wait"]
    assert not directory.exists()


@pytest.mark.parametrize("failure", ["late-output", "nonzero-exit", "directory-removal"])
def test_finish_never_publishes_before_eof_zero_exit_and_successful_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    from easysynq_api.services.audit import _history_spool as module
    from easysynq_api.services.audit.history_collection import (
        HistoryCollectionError,
        HistoryCollectionLimits,
    )

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    limits = HistoryCollectionLimits(8, 16, 1_000_000, 1_048_576, 30, 32)
    scope = module._SpoolWitness(WITNESS_ID, NAMESPACE_HASH, "history-probe")
    with module._SpoolSession(
        ORG_ID,
        (scope,),
        limits,
        cancel=Event(),
        deadline=time.monotonic() + 30,
    ) as spool:
        ticket = spool.reserve_page(0, None, None)
        assert ticket is not None
        spool.admit_page(ticket, _page_xml())
        with monkeypatch.context() as fault_patch:
            if failure == "late-output":
                original_read = spool._read
                fault_patch.setattr(
                    spool,
                    "_read",
                    lambda size, deadline: b"x" if size == 1 else original_read(size, deadline),
                )
            elif failure == "nonzero-exit":
                process = spool._process
                assert process is not None
                original_wait = process.wait

                def nonzero_wait(*, timeout: float) -> int:
                    original_wait(timeout=timeout)
                    return 7

                fault_patch.setattr(process, "wait", nonzero_wait)
            else:

                def fail_removal(path: str) -> None:
                    raise OSError("synthetic removal failure")

                fault_patch.setattr(module.shutil, "rmtree", fail_removal)
            with pytest.raises((HistoryCollectionError, BaseExceptionGroup)) as caught:
                spool.finish()
            leaves = _leaves(caught.value)
            expected_code = {
                "late-output": "PROTOCOL_INVALID",
                "nonzero-exit": "WORKER_FAILED",
                "directory-removal": "CLEANUP_FAILED",
            }[failure]
            assert any(
                isinstance(error, HistoryCollectionError) and error.code == expected_code
                for error in leaves
            )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("mutation", ["short", "long", "tag", "sequence", "zero-trailing"])
def test_raw_worker_rejects_invalid_body_frames_and_never_returns_prefix(
    tmp_path: Path,
    mutation: str,
) -> None:
    body = b"B" + (4).to_bytes(8, "big") + (0).to_bytes(4, "big") + b"abcd"
    if mutation == "short":
        body = body[:-2]
    elif mutation == "long":
        body += b"e"
    elif mutation == "tag":
        body = b"X" + body[1:]
    elif mutation == "sequence":
        body = b"B" + (3).to_bytes(8, "big") + body[9:]
    status, frames = _raw_worker(
        tmp_path,
        [
            {"op": "RESERVE_PAGE", "id": 2, "witness": 0, "key": None, "version": None},
            {
                "op": "PAGE_BEGIN",
                "id": 3,
                "ticket": {"page_id": 1, "witness_index": 0, "page_index": 0},
                "length": len(ORIGINAL_TERMINAL_XML),
            },
            b"B" + (3).to_bytes(8, "big") + (0).to_bytes(4, "big") + ORIGINAL_TERMINAL_XML,
            {"op": "PAGE_END", "id": 3},
            {
                "op": "BODY",
                "id": 4,
                "ordinal": 1,
                "length": 0 if mutation == "zero-trailing" else 4,
            },
            body,
        ],
    )
    assert status != 0
    assert any("admission" in frame for frame in frames)
    assert not any("summary" in frame for frame in frames)


@pytest.mark.parametrize("mutation", ["bucket", "organization", "empty-truncated"])
def test_worker_admits_only_original_xml_in_the_reserved_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    from easysynq_api.services.audit._history_spool import _SpoolSession, _SpoolWitness
    from easysynq_api.services.audit.history_collection import (
        HistoryCollectionError,
        HistoryCollectionLimits,
    )

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    if mutation == "bucket":
        raw = ORIGINAL_TERMINAL_XML.replace(b"history-probe", b"different-probe")
    elif mutation == "organization":
        raw = ORIGINAL_TERMINAL_XML.replace(b"11111111-1111", b"22222222-2222")
    else:
        raw = _page_xml(truncated=True, next_key=PREFIX + "next")
    limits = HistoryCollectionLimits(8, 16, 1_000_000, 1_048_576, 30, 32)
    scope = _SpoolWitness(WITNESS_ID, NAMESPACE_HASH, "history-probe")
    with _SpoolSession(
        ORG_ID,
        (scope,),
        limits,
        cancel=Event(),
        deadline=time.monotonic() + 30,
    ) as spool:
        ticket = spool.reserve_page(0, None, None)
        assert ticket is not None
        with pytest.raises(HistoryCollectionError) as caught:
            spool.admit_page(ticket, raw)
        assert caught.value.code == "PROTOCOL_INVALID"
    assert list(tmp_path.iterdir()) == []
