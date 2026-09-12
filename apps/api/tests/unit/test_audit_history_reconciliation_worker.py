"""Independent version-two protocol and real owned-resource failure proofs."""

from __future__ import annotations

import dataclasses
import json
import os
import resource
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from threading import Event
from typing import Any

import pytest

from easysynq_api.services.audit import history_reconciliation as reconciliation
from easysynq_api.services.audit.history_collection import HistoryCollectionError
from tests.unit.test_audit_history_reconciliation import _inputs
from tests.unit.test_audit_history_spool import _literal_frame, _page_xml

pytestmark = pytest.mark.unit
_AUDIT = Path(reconciliation.__file__).parent


def _init(*, root_length: int | None = None, page_lengths: tuple[int, ...] = ()) -> dict:
    """Author the wire fixture independently of its production encoder."""
    values = _inputs(reconciliation)
    enrollment = dataclasses.asdict(values["enrollment"])
    stream = enrollment["stream"]
    stream["org_id"] = "11111111-1111-4111-8111-111111111111"
    stream["stream_id"] = str(stream["stream_id"])
    stream["bootstrap"]["initial_public_key"] = stream["bootstrap"]["initial_public_key"].hex()
    witnesses = []
    enrollment["witnesses"] = list(enrollment["witnesses"][:1])
    for witness in enrollment["witnesses"]:
        witness["witness_id"] = str(witness["witness_id"])
        witnesses.append({**witness, "bucket": "history-probe"})
    enrollment["legacy_keys"] = list(enrollment["legacy_keys"])
    for material in enrollment["legacy_keys"]:
        material["public_key"] = material["public_key"].hex()
    return {
        "op": "INIT",
        "id": 1,
        "version": 2,
        "enrollment": enrollment,
        "witnesses": witnesses,
        "limits": dataclasses.asdict(values["limits"]),
        "root_length": root_length,
        "page_lengths": list(page_lengths),
    }


def _raw_worker(tmp_path: Path, messages: list[dict | bytes], *, tail: bytes = b"") -> list[dict]:
    worker = _AUDIT / "_history_reconciliation_worker.py"
    assert worker.is_file(), "reconciliation worker is not implemented"
    completed = subprocess.run(  # noqa: S603 - fixed isolated executable, synthetic framed input
        [sys.executable, "-I", "-B", "-u", str(worker), str(worker.parents[3])],
        input=b"".join(_literal_frame(message) for message in messages) + tail,
        cwd=tmp_path,
        capture_output=True,
        timeout=15,
        check=False,
        env={"LANG": "C.UTF-8", "TZ": "UTC"},
        start_new_session=True,
    )
    assert completed.returncode != 0
    assert completed.stderr == b""
    output = completed.stdout
    frames = []
    while output:
        assert len(output) >= 4
        length = int.from_bytes(output[:4], "big")
        assert 1 < length <= 131072 and len(output) >= length + 4
        payload, output = output[4 : length + 4], output[length + 4 :]
        assert payload[:1] == b"J"
        frames.append(json.loads(payload[1:]))
    assert frames[0] == {"op": "READY", "version": 2}
    assert not any("result" in frame for frame in frames)
    assert {p.name for p in tmp_path.iterdir()} <= {"spool.sqlite3"}
    for path in tmp_path.iterdir():
        path.unlink()
    return frames


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown",
        "missing",
        "duplicate",
        "version",
        "secret",
        "namespace",
        "key",
        "lengths",
        "capacity",
    ],
)
def test_init_rejects_invalid_public_scope(tmp_path: Path, mutation: str) -> None:
    request: Any = _init()
    if mutation == "unknown":
        request["sql"] = "SELECT 1"
    elif mutation == "missing":
        del request["enrollment"]
    elif mutation == "duplicate":
        request = b'J{"version":2,' + json.dumps(request).encode()[1:]
    elif mutation == "version":
        request["version"] = True
    elif mutation == "secret":
        request["witnesses"][0]["secret_key"] = "synthetic-forbidden"
    elif mutation == "namespace":
        request["witnesses"][0]["namespace_hash"] = "0" * 64
    elif mutation == "key":
        request["enrollment"]["stream"]["bootstrap"]["initial_public_key"] = "00" * 32
    elif mutation == "lengths":
        request["page_lengths"] = [False]
    else:
        request["page_lengths"] = [1] * 17
    frames = _raw_worker(tmp_path, [request])
    assert not any(frame.get("op") == "ACK" for frame in frames)


def _chunk(request_id: int, offset: int, body: bytes) -> bytes:
    return b"B" + request_id.to_bytes(8, "big") + offset.to_bytes(4, "big") + body


@pytest.mark.parametrize(
    "mutation",
    [
        "short",
        "long",
        "offset",
        "id",
        "tag",
        "oversized",
        "end",
        "replay",
        "root-absent",
        "page-index",
        "declared-length",
    ],
)
def test_package_upload_rejects_malformed_order_and_chunks(tmp_path: Path, mutation: str) -> None:
    init = _init(root_length=4)
    begin = {"op": "PACKAGE_BEGIN", "id": 2, "kind": "root", "index": 0, "length": 4}
    chunk = _chunk(2, 0, b"abcd")
    end = {"op": "PACKAGE_END", "id": 2}
    if mutation == "short":
        chunk = chunk[:-1]
    elif mutation == "long":
        chunk += b"e"
    elif mutation == "offset":
        chunk = _chunk(2, 1, b"abcd")
    elif mutation == "id":
        chunk = _chunk(3, 0, b"abcd")
    elif mutation == "tag":
        chunk = b"X" + chunk[1:]
    elif mutation == "oversized":
        chunk = _chunk(2, 0, b"x" * 65537)
    elif mutation == "end":
        end["id"] = 3
    elif mutation == "root-absent":
        init["root_length"] = None
    elif mutation == "page-index":
        begin.update(kind="page", index=1)
    elif mutation == "declared-length":
        begin["length"] = 3
    messages: list[dict | bytes] = [init, begin, chunk, end]
    if mutation == "replay":
        messages += [{**begin, "id": 3}, _chunk(3, 0, b"abcd"), {**end, "id": 3}]
    frames = _raw_worker(tmp_path, messages)
    expected_acks = [1, 2] if mutation == "replay" else [1]
    assert [frame["id"] for frame in frames if frame["op"] == "ACK"] == expected_acks


def _empty_collection() -> list[dict | bytes]:
    page = _page_xml()
    return [
        {"op": "RESERVE_PAGE", "id": 2, "witness": 0, "key": None, "version": None},
        {
            "op": "PAGE_BEGIN",
            "id": 3,
            "ticket": {"page_id": 1, "witness_index": 0, "page_index": 0},
            "length": len(page),
        },
        _chunk(3, 0, page),
        {"op": "PAGE_END", "id": 3},
        {"op": "SEAL", "id": 4, "expected_observations": 0},
    ]


@pytest.mark.parametrize(
    "operation", ["BODY", "RESERVE_PAGE", "SEAL", "FINISH", "FINISH_RECONCILIATION", "INIT", "SQL"]
)
def test_seal_permanently_rejects_evidence_and_premature_finalization(
    tmp_path: Path, operation: str
) -> None:
    frames = _raw_worker(
        tmp_path,
        [
            _init(),
            *_empty_collection(),
            {"op": operation, "id": 5},
            {"op": "FINISH_RECONCILIATION", "id": 6},
        ],
    )
    assert any(frame.get("id") == 4 and "summary" in frame for frame in frames)
    assert frames[-1] == {"op": "ERROR", "id": 5, "code": "PROTOCOL_INVALID"}


@pytest.mark.parametrize("operation", ["STEP", "SEAL", "FINISH_RECONCILIATION", "RESERVE_PAGE"])
def test_package_and_collection_must_finish_before_reconciliation(
    tmp_path: Path, operation: str
) -> None:
    frames = _raw_worker(tmp_path, [_init(root_length=0), {"op": operation, "id": 2}])
    assert frames[-1] == {"op": "ERROR", "id": 2, "code": "PROTOCOL_INVALID"}


def test_valid_step_advances_owned_engine_without_publishing_a_result(tmp_path: Path) -> None:
    frames = _raw_worker(tmp_path, [_init(), *_empty_collection(), {"op": "STEP", "id": 5}])
    from easysynq_api.services.audit._history_reconciliation_protocol import decode_progress

    assert set(frames[-1]) == {"op", "id", "progress"}
    assert frames[-1]["op"] == "ACK" and frames[-1]["id"] == 5
    progress = decode_progress(frames[-1]["progress"])
    assert progress.phase == "package-pages" and not progress.done


def _session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    init: dict | None = None,
    *,
    cancel: Event | None = None,
) -> Any:
    from easysynq_api.services.audit._history_reconciliation_protocol import decode_init
    from easysynq_api.services.audit._history_reconciliation_session import _ReconciliationSession

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    return _ReconciliationSession(
        decode_init(init or _init()), cancel=cancel or Event(), deadline=time.monotonic() + 30
    )


def test_real_session_retains_collection_after_seal_and_owns_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _session(tmp_path, monkeypatch, _init(root_length=0, page_lengths=(3, 3))) as session:
        from easysynq_api.services.audit.bootstrap_bridge import BridgePageObservation

        session.upload_package(b"", (BridgePageObservation(b"abc"), BridgePageObservation(b"abc")))
        process = session._process
        directory = Path(session._directory)
        assert process is not None
        expected = {
            resource.RLIMIT_AS: 536870912,
            resource.RLIMIT_CPU: 120,
            resource.RLIMIT_NOFILE: 32,
            resource.RLIMIT_CORE: 0,
            resource.RLIMIT_FSIZE: 33554432,
        }
        for kind, limit in expected.items():
            assert resource.prlimit(process.pid, kind) == (limit, limit)
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
        assert stat.S_IMODE((directory / "spool.sqlite3").stat().st_mode) == 0o600
        proc = Path(f"/proc/{process.pid}")
        assert (proc / "cmdline").read_bytes().split(b"\0")[1:4] == [b"-I", b"-B", b"-u"]
        assert set((proc / "environ").read_bytes().rstrip(b"\0").split(b"\0")) == {
            b"LANG=C.UTF-8",
            b"TZ=UTC",
        }
        assert not any("socket:" in os.readlink(fd) for fd in (proc / "fd").iterdir())
        assert (proc / "task" / str(process.pid) / "children").read_text() == ""
        ticket = session.reserve_page(0, None, None)
        session.admit_page(ticket, _page_xml())
        summary = session.seal(0)
        assert summary.admitted_total_bytes == len(_page_xml()) + 6
        assert summary.witnesses[0].terminal_reached
        assert process.poll() is None and directory.is_dir()
        session._guard_external_io()
        assert session.step().phase == "package-pages"
        with pytest.raises(HistoryCollectionError) as caught:
            session.finish_reconciliation()
        assert type(caught.value) is HistoryCollectionError
        assert caught.value.code == "PROTOCOL_INVALID"
        assert process.returncode is not None
        assert process.stdin.closed and process.stdout.closed
        assert session._selector is None and session._directory is None
    assert list(tmp_path.iterdir()) == []


def _store_process(tmp_path: Path, program: str, init: dict | None = None) -> dict:
    """Inspect this test child's sole connection; never reopen its discarded database."""
    prelude = """
import json, os, resource, runpy, sqlite3, sys
policy = runpy.run_path(sys.argv[1])
policy["_apply_limits"]()
sys.path.insert(0, sys.argv[2])
from easysynq_api.services.audit._history_reconciliation_protocol import decode_init
from easysynq_api.services.audit._history_reconciliation_store import _ReconciliationStore
from easysynq_api.services.audit.history_collection import HistoryCollectionError
payload = json.load(sys.stdin)
scope = decode_init(payload["init"])
resource.setrlimit(resource.RLIMIT_FSIZE, (scope.limits.maximum_spool_bytes,)*2)
store = _ReconciliationStore(scope)
"""
    worker = _AUDIT / "_history_reconciliation_worker.py"
    result = subprocess.run(  # noqa: S603 - finite test-owned program, never worker protocol code
        [sys.executable, "-I", "-B", "-c", prelude + program, str(worker), str(worker.parents[3])],
        input=json.dumps({"init": init or _init(), "xml": _page_xml().hex()}).encode(),
        cwd=tmp_path,
        capture_output=True,
        timeout=15,
        check=False,
        env={"LANG": "C.UTF-8", "TZ": "UTC"},
        start_new_session=True,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert result.stderr == b""
    assert [p.name for p in tmp_path.iterdir()] == ["spool.sqlite3"]
    (tmp_path / "spool.sqlite3").unlink()
    return json.loads(result.stdout)


def test_sqlite_policy_and_cached_evidence_writes_are_locked_at_seal(tmp_path: Path) -> None:
    result = _store_process(
        tmp_path,
        """
try:
    db = store._connection()
    rejected = []
    for sql in ("ATTACH ':memory:' AS extra", "PRAGMA temp_store=FILE", "CREATE TABLE rogue(x)",
                "DROP TABLE raw_bodies", "SELECT load_extension('forbidden')", "VACUUM"):
        try: db.execute(sql)
        except sqlite3.DatabaseError: rejected.append(sql)
        else: raise AssertionError("unknown SQL was admitted")
    # Prime SQLite's statement cache with actual writes before sealing.
    update = "UPDATE witnesses SET bucket=bucket WHERE id=0"
    db.execute(update)
    raw = bytes.fromhex(payload["xml"])
    ticket = store.reserve_page(0,None,None)
    store.page_begin(ticket,len(raw))
    store.page_chunk(raw)
    store.page_end()
    store.seal(0)
    for sql in (update, "DELETE FROM pages", "DELETE FROM package_deliveries",
                "INSERT INTO issues(witness,code,count) VALUES(0,'LIST_UNAVAILABLE',1)"):
        try: db.execute(sql)
        except sqlite3.DatabaseError: rejected.append(sql)
        else: raise AssertionError("sealed evidence was mutable")
    # Derived rows remain writable while original evidence is read-only.
    db.execute("INSERT INTO materials VALUES(?,?)", ("synthetic-key", b"x"*32))
    assert db.execute("SELECT count(*) FROM materials").fetchone() == (1,)
    assert db.execute("SELECT count(*) FROM witnesses").fetchone() == (1,)
    db.set_authorizer(None)  # Test-only introspection after the denial assertions.
    names = ("page_size", "max_page_count", "journal_mode", "temp_store", "mmap_size",
             "cache_size", "hard_heap_limit", "synchronous", "trusted_schema",
             "threads", "busy_timeout")
    settings = {n:db.execute("PRAGMA "+n).fetchone()[0] for n in names}
    tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    result = {"denied":len(rejected), "settings":settings, "tables":tables,
              "limits":[db.getlimit(n) for n in (sqlite3.SQLITE_LIMIT_ATTACHED,
                sqlite3.SQLITE_LIMIT_LENGTH,sqlite3.SQLITE_LIMIT_SQL_LENGTH,
                sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER,sqlite3.SQLITE_LIMIT_COLUMN)]}
finally:
    store.close()
print(json.dumps(result))
""",
    )
    assert result["denied"] == 10
    assert result["settings"] == dict(
        page_size=4096,
        max_page_count=8192,
        journal_mode="memory",
        temp_store=2,
        mmap_size=0,
        cache_size=-1024,
        hard_heap_limit=67108864,
        synchronous=0,
        trusted_schema=0,
        threads=0,
        busy_timeout=0,
    )
    assert result["limits"] == [0, 16842752, 8192, 32, 32]
    assert set(result["tables"]) == {
        "witnesses",
        "pages",
        "cursors",
        "observations",
        "locators",
        "issues",
        "raw_bodies",
        "body_deliveries",
        "reconciliation_issues",
        "issue_refs",
        "package_deliveries",
        "bridge_pages",
        "root_pages",
        "page_deliveries",
        "page_entries",
        "committed_entries",
        "legacy_bodies",
        "legacy_membership",
        "legacy_locators",
        "legacy_witness_heads",
        "v2_routes",
        "v2_nodes",
        "v2_raw_nodes",
        "materials",
        "material_events",
        "parent_events",
        "admitted_nodes",
        "path_nodes",
        "used_epochs",
        "v2_deliveries",
    }


@pytest.mark.parametrize("lengths", [(0, (0,)), (262144, (2097152,)), (262145, (2097153, 2097153))])
def test_package_exact_bytes_oversize_digests_and_duplicate_accounting(
    tmp_path: Path, lengths: tuple
) -> None:
    result = _store_process(
        tmp_path,
        """
import hashlib
try:
    sizes = [scope.root_length, *scope.page_lengths]
    for position,length in enumerate(sizes):
        kind = "root" if position == 0 else "page"
        store.package_begin(kind, max(0,position-1), length)
        for offset in range(0,length,65536):
            store.package_chunk(b"x"*min(65536,length-offset))
        store.package_end()
    rows = store._connection().execute(
        "SELECT position,byte_length,raw_digest,raw_body,wire_valid "
        "FROM package_deliveries ORDER BY position"
    ).fetchall()
    assert len(rows) == len(sizes)
    for position,length,digest,raw,valid in rows:
        expected_valid = length <= (262144 if position == 0 else 2097152)
        assert bool(valid) == expected_valid
        assert raw == (b"x"*length if expected_valid else None)
        assert digest == hashlib.sha256(b"x"*length).digest()
    result = {"package":store.package_bytes, "collection":store._total,
              "rows":len(rows),"state":store.state}
finally:
    store.close()
print(json.dumps(result))
""",
        _init(root_length=lengths[0], page_lengths=lengths[1]),
    )
    assert result == {
        "package": lengths[0] + sum(lengths[1]),
        "collection": 0,
        "rows": 1 + len(lengths[1]),
        "state": "collecting",
    }


@pytest.mark.parametrize("short_by", [0, 1])
def test_package_and_original_xml_share_the_aggregate_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, short_by: int
) -> None:
    init = _init(root_length=4)
    init["limits"]["maximum_total_bytes"] = 4 + len(_page_xml()) - short_by
    with _session(tmp_path, monkeypatch, init) as session:
        session.upload_package(b"root", ())
        ticket = session.reserve_page(0, None, None)
        if short_by:
            with pytest.raises(HistoryCollectionError) as caught:
                session.admit_page(ticket, _page_xml())
            assert caught.value.code == "RESOURCE_LIMIT"
        else:
            session.admit_page(ticket, _page_xml())
            assert session.seal(0).admitted_total_bytes == len(_page_xml()) + 4
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "fault", ["cancel", "whole-deadline", "command-deadline", "pipe", "worker-death"]
)
def test_package_upload_fault_ends_the_single_owned_lifetime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    import signal

    from easysynq_api.services.audit import _history_spool as base
    from easysynq_api.services.audit.history_collection import HistoryCollectionCancelled

    cancel = Event()
    with _session(tmp_path, monkeypatch, _init(root_length=262144), cancel=cancel) as session:
        process = session._process
        original_send = session._send_upload
        offset = 0.0
        calls = 0
        monkeypatch.setattr(base, "_monotonic", lambda: time.monotonic() + offset)

        def send(payload: bytes, deadline: float, request_id: int) -> None:
            nonlocal calls, offset
            original_send(payload, deadline, request_id)
            calls += 1
            if fault == "cancel":
                cancel.set()
            elif fault == "whole-deadline":
                offset += 40
            elif fault == "command-deadline":
                offset += 4
            elif fault == "worker-death":
                os.killpg(process.pid, signal.SIGKILL)
            elif fault == "pipe":

                def broken(fd: int, body: bytes) -> int:
                    raise OSError("synthetic pipe failure")

                monkeypatch.setattr(base, "_write_fd", broken)

        monkeypatch.setattr(session, "_send_upload", send)
        with pytest.raises(
            HistoryCollectionCancelled if fault == "cancel" else HistoryCollectionError
        ) as caught:
            session.upload_package(b"x" * 262144, ())
        if fault != "cancel":
            assert caught.value.code == (
                "DEADLINE_EXCEEDED" if "deadline" in fault else "WORKER_FAILED"
            )
        if fault in {"cancel", "whole-deadline", "command-deadline"}:
            assert calls == (3 if fault == "command-deadline" else 1)
        assert process.returncode is not None
        assert process.stdin.closed and process.stdout.closed
        assert session._directory is None and session._selector is None
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("failure", ["spawn", "selector"])
def test_failed_session_start_cleans_partial_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from easysynq_api.services.audit import _history_reconciliation_session as module

    sentinel = RuntimeError("synthetic selector initialization failure")
    session = _session(tmp_path, monkeypatch)
    if failure == "spawn":

        def spawn(*args: Any, **kwargs: Any) -> Any:
            raise OSError("synthetic spawn failure")

        monkeypatch.setattr(module.subprocess, "Popen", spawn)
    else:

        def selector() -> Any:
            raise sentinel

        monkeypatch.setattr(module.selectors, "DefaultSelector", selector)
    with pytest.raises(HistoryCollectionError if failure == "spawn" else RuntimeError) as caught:
        session.__enter__()
    if failure == "spawn":
        assert caught.value.code == "WORKER_START_FAILED"
    else:
        assert caught.value is sentinel
        assert session._process.returncode is not None
    assert list(tmp_path.iterdir()) == []


def test_heap_exhaustion_poisoning_is_a_resource_error(tmp_path: Path) -> None:
    result = _store_process(
        tmp_path,
        """
db = store._connection()
db.set_authorizer(None)
assert db.execute("PRAGMA hard_heap_limit").fetchone()[0] == 67108864
try: db.execute("PRAGMA hard_heap_limit=1").fetchone()
except MemoryError: pass
finally: db.set_authorizer(store._authorize)
try:
    try: store.reserve_page(0,None,None)
    except HistoryCollectionError as error: code = error.code
    else: raise AssertionError("heap ceiling not enforced")
    result = {"code":code, "closed":store._closed, "poisoned":store._poisoned}
finally: store.close()
print(json.dumps(result))
""",
    )
    assert result == {"code": "RESOURCE_LIMIT", "closed": True, "poisoned": True}


def test_spool_file_growth_cannot_exceed_the_admitted_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from easysynq_api.services.audit.bootstrap_bridge import BridgePageObservation

    init = _init(page_lengths=(2097152,))
    init["limits"]["maximum_spool_bytes"] = 1048576
    with _session(tmp_path, monkeypatch, init) as session:
        with pytest.raises(HistoryCollectionError) as caught:
            session.upload_package(None, (BridgePageObservation(b"x" * 2097152),))
        assert caught.value.code == "RESOURCE_LIMIT"
        assert session._process.returncode is not None
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("ceiling", [65536, 262144])
def test_schema_capacity_failure_is_explicit_and_cleans_failed_init(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ceiling: int
) -> None:
    init = _init()
    init["limits"]["maximum_spool_bytes"] = ceiling
    session = _session(tmp_path, monkeypatch, init)
    with pytest.raises(HistoryCollectionError) as caught:
        session.__enter__()
    assert type(caught.value) is HistoryCollectionError
    assert caught.value.code == "RESOURCE_LIMIT"
    assert session._process.returncode is not None
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("tail", [b"\0", b"\0\0\0\0", (131073).to_bytes(4, "big"), b"\0\0\0\x04J{"])
def test_truncated_or_oversized_frame_never_returns_a_result(tmp_path: Path, tail: bytes) -> None:
    frames = _raw_worker(tmp_path, [_init()], tail=tail)
    assert [f for f in frames if f["op"] == "ACK"] == [{"op": "ACK", "id": 1, "version": 2}]


@pytest.mark.parametrize(
    "metadata_request",
    [
        {"op": "STEP", "id": 2},
        {"op": "SEAL", "id": 2, "expected_observations": 0},
        {"op": "FINISH_RECONCILIATION", "id": 2},
        {"op": "RESERVE_PAGE", "id": True, "witness": 0, "key": None, "version": None},
        {"op": "RESERVE_PAGE", "id": 1, "witness": 0, "key": None, "version": None},
        {"op": "RESERVE_PAGE", "id": 3, "witness": 0, "key": None, "version": None},
        b'J{"op":"STEP","id":2,"id":2}',
        b'J{"op":"STEP","id":2} {}',
    ],
)
def test_commands_require_valid_sequence_and_complete_collection(
    tmp_path: Path, metadata_request: dict | bytes
) -> None:
    frames = _raw_worker(tmp_path, [_init(), metadata_request])
    assert len([f for f in frames if f["op"] == "ACK"]) == 1


def test_seal_rejects_an_inexact_parent_count(tmp_path: Path) -> None:
    commands = _empty_collection()
    commands[-1] = {"op": "SEAL", "id": 4, "expected_observations": 1}
    frames = _raw_worker(tmp_path, [_init(), *commands])
    assert frames[-1] == {"op": "ERROR", "id": 4, "code": "PROTOCOL_INVALID"}
    assert not any("summary" in f for f in frames)


def test_scope_encoder_contains_only_exact_public_fields() -> None:
    from easysynq_api.services.audit import _history_reconciliation_protocol as protocol

    request = _init(root_length=0, page_lengths=(0, 65537))
    scope = protocol.decode_init(request)
    encoded = protocol.scope_payload(scope)
    assert json.loads(json.dumps(encoded)) == {
        k: v for k, v in request.items() if k not in {"op", "id"}
    }
    from easysynq_api.services.audit import _history_spool_protocol as wire

    assert (
        protocol.decode_init(wire.metadata(wire.encode({"op": "INIT", "id": 1, **encoded})))
        == scope
    )
    assert set(dataclasses.asdict(scope)) == {
        "enrollment",
        "witnesses",
        "limits",
        "root_length",
        "page_lengths",
    }


@pytest.mark.parametrize("fault", ["unknown-version", "wrong-id", "extra-field", "early-result"])
def test_parent_rejects_fabricated_worker_replies_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    from easysynq_api.services.audit import _history_spool_protocol as wire

    session = _session(tmp_path, monkeypatch, _init(root_length=65537))
    if fault == "unknown-version":
        receive = session._receive

        def altered_ready(deadline: float) -> bytes:
            reply = wire.metadata(receive(deadline))
            reply["version"] = 1
            return wire.encode(reply)

        monkeypatch.setattr(session, "_receive", altered_ready)
        with pytest.raises(HistoryCollectionError) as caught:
            session.__enter__()
    else:
        with session:
            if fault == "early-result":

                def early(payload: bytes, deadline: float, request_id: int) -> None:
                    data = wire.frame(wire.encode({"op": "ACK", "id": request_id}))
                    remaining = iter((data[1:4], data[4:]))
                    monkeypatch.setattr(session, "_exact", lambda size, bound: next(remaining))
                    session._early_terminal(data[:1], deadline, request_id)

                monkeypatch.setattr(session, "_send_upload", early)
            else:
                receive = session._receive

                def altered_ack(deadline: float) -> bytes:
                    reply = wire.metadata(receive(deadline))
                    if fault == "wrong-id":
                        reply["id"] += 1
                    else:
                        reply["result"] = {}
                    return wire.encode(reply)

                monkeypatch.setattr(session, "_receive", altered_ack)
            with pytest.raises(HistoryCollectionError) as caught:
                session.upload_package(b"x" * 65537, ())
    assert caught.value.code == "PROTOCOL_INVALID"
    assert session._process.returncode is not None
    assert list(tmp_path.iterdir()) == []


def test_unproved_reaping_preserves_directory_and_cleanup_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from easysynq_api.services.audit import _history_spool as base

    calls = []

    class Process:
        pid = 123
        returncode = None
        stdin = stdout = None

        def wait(self, *, timeout: float) -> int:
            calls.append("wait")
            raise subprocess.TimeoutExpired("synthetic worker", timeout)

    session = _session(tmp_path, monkeypatch)
    directory = tmp_path / "owned"
    directory.mkdir(mode=0o700)
    info = directory.stat()
    process = Process()
    monkeypatch.setattr(session, "_directory", str(directory))
    monkeypatch.setattr(session, "_identity", (info.st_dev, info.st_ino))
    monkeypatch.setattr(session, "_process", process)
    monkeypatch.setattr(base, "_killpg", lambda pid, sig: calls.append("kill"))
    faults = session._cleanup()
    assert calls == ["kill", "wait"] and directory.is_dir()
    assert len(faults) == 1 and type(faults[0]) is HistoryCollectionError
    assert faults[0].code == "CLEANUP_FAILED"
    process.returncode = 0
    assert session._cleanup() == []
    assert calls == ["kill", "wait"] and not directory.exists()


@pytest.mark.parametrize("constraint", ["address-space", "descriptors", "cpu"])
def test_actual_worker_os_policy_enforces_allocations(tmp_path: Path, constraint: str) -> None:
    import signal

    program = """
import errno, os, resource, runpy, sys
policy = runpy.run_path(sys.argv[1])
policy["_apply_limits"]()
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
                assert error.errno == errno.EMFILE and len(opened) < 32
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
    result = subprocess.run(  # noqa: S603 - test child's fixed policy and finite allocation probe
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            program,
            str(_AUDIT / "_history_reconciliation_worker.py"),
            constraint,
        ],
        cwd=tmp_path,
        capture_output=True,
        timeout=8,
        check=False,
    )
    assert result.stderr == b""
    if constraint == "cpu":
        assert result.returncode == -signal.SIGKILL
    else:
        assert result.returncode == 0
        assert result.stdout == f"{constraint} enforced\n".encode()
    assert list(tmp_path.iterdir()) == []


def test_pending_package_transaction_is_discarded_on_worker_death(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import signal

    from easysynq_api.services.audit.bootstrap_bridge import BridgePageObservation

    with _session(tmp_path, monkeypatch, _init(page_lengths=(2097152,))) as session:
        process = session._process
        path = Path(session._directory) / "spool.sqlite3"
        initial = path.stat().st_size
        original = session._send_upload
        calls = 0

        def interrupt(payload: bytes, deadline: float, request_id: int) -> None:
            nonlocal calls
            original(payload, deadline, request_id)
            calls += 1
            if calls == 1:
                # The full zeroblob has allocated pages, but only the first chunk
                # has been sent and PACKAGE_END cannot have committed this input.
                assert path.stat().st_size > initial
                os.killpg(process.pid, signal.SIGKILL)

        monkeypatch.setattr(session, "_send_upload", interrupt)
        with pytest.raises(HistoryCollectionError) as caught:
            session.upload_package(None, (BridgePageObservation(b"x" * 2097152),))
        assert caught.value.code == "WORKER_FAILED"
        assert process.returncode is not None and not path.exists()
    assert list(tmp_path.iterdir()) == []


def test_oversize_package_stream_does_not_allocate_a_sqlite_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # This is larger than SQLite's original 16 MiB value bound. The whole input
    # still must be consumed and hashed under the admitted aggregate budget.
    size = 17 * 1024 * 1024
    init = _init(root_length=size)
    init["limits"]["maximum_total_bytes"] = size + len(_page_xml())
    init["limits"]["maximum_spool_bytes"] = 1048576
    with _session(tmp_path, monkeypatch, init) as session:
        session.upload_package(b"x" * size, ())
        assert (Path(session._directory) / "spool.sqlite3").stat().st_size < 1048576
        ticket = session.reserve_page(0, None, None)
        session.admit_page(ticket, _page_xml())
        assert session.seal(0).admitted_total_bytes == len(_page_xml()) + size
    assert list(tmp_path.iterdir()) == []
