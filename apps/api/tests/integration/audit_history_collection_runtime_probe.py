"""Finite installed-image exercises; independent acceptance lives in the sibling producer.

Only the synthetic mode replaces network-call boundaries. Provider mode delegates
every LIST/GET to the installed unchanged R83/R80 workers. Resource child modes
use test-owned private seams, never a new product SQL or worker hook.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib.util
import json
import os
import platform
import resource
import selectors
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import quote
from uuid import UUID
from xml.sax.saxutils import escape

_IPC_MODES = (
    "valid-result",
    "oversized",
    "truncated",
    "flood",
    "stale",
    "late-output",
    "withheld-eof",
)


def _json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()


def _literal_frame(value: dict[str, Any]) -> bytes:
    payload = b"J" + _json(value)
    return len(payload).to_bytes(4, "big") + payload


def _fixture_frame(incoming: Any) -> bytes:
    """Read the real parent's bounded request without importing application code."""
    header = incoming.read(4)
    assert len(header) == 4
    length = int.from_bytes(header, "big")
    assert 0 < length <= 131_072
    payload = bytearray()
    while len(payload) < length:
        chunk = incoming.read(min(65_536, length - len(payload)))
        assert chunk
        payload.extend(chunk)
    return bytes(payload)


def _fake_worker(mode: str) -> int:
    """Enumerated hostile process only; never accepted as production worker evidence."""
    output = sys.stdout.buffer
    incoming = sys.stdin.buffer
    ready = _literal_frame({"op": "READY", "version": 1})
    if mode == "oversized":
        output.write((131_073).to_bytes(4, "big"))
        output.flush()
        return 0
    if mode == "truncated":
        output.write(ready[:-1])
        output.flush()
        return 0
    if mode == "flood":
        try:
            for _ in range(1024):
                output.write(ready * 1000)
                output.flush()
        except BrokenPipeError:
            return 0
        return 1
    output.write(ready)
    output.flush()
    request = json.loads(_fixture_frame(incoming)[1:])
    output.write(_literal_frame({"op": "ACK", "id": 0 if mode == "stale" else 1, "version": 1}))
    output.flush()
    if mode == "stale":
        return 0
    scope = request["witnesses"][0]
    while True:
        request = json.loads(_fixture_frame(incoming)[1:])
        response: dict[str, Any] = {"op": "ACK", "id": request["id"]}
        if request["op"] == "RESERVE_PAGE":
            response["ticket"] = {"page_id": 1, "witness_index": 0, "page_index": 0}
        elif request["op"] == "PAGE_BEGIN":
            while True:
                chunk = _fixture_frame(incoming)
                if chunk.startswith(b"J"):
                    assert json.loads(chunk[1:])["op"] == "PAGE_END"
                    break
            response["admission"] = {"first_ordinal": 0, "version_count": 0, "delete_count": 0}
        elif request["op"] == "FINISH":
            assert incoming.read(1) == b""
            response["summary"] = {
                "witnesses": [
                    {
                        "witness_id": scope["witness_id"],
                        "namespace_hash": scope["namespace_hash"],
                        "terminal_reached": True,
                        "page_attempts": 1,
                        "admitted_pages": 1,
                        "version_observations": 0,
                        "delete_observations": 0,
                        "successful_reads": 0,
                        "unavailable_reads": 0,
                        "duplicate_body_deliveries": 0,
                        "conflicting_locators": 0,
                    }
                ],
                "issues": [],
                "failed_issues": 0,
                "incomplete_issues": 0,
                "issues_omitted": 0,
                "admitted_total_bytes": 300,
            }
            output.write(_literal_frame(response))
            output.flush()
            if mode == "late-output":
                output.write(b"trailing-output")
                output.flush()
            elif mode == "withheld-eof":
                time.sleep(10)
            return 0
        else:
            raise AssertionError("unexpected fixed fake-worker request")
        output.write(_literal_frame(response))
        output.flush()


# Fake peers exercise the installed parent, so they need no provider/application imports.
if (
    __name__ == "__main__"
    and len(sys.argv) > 1
    and sys.argv[1] in {"ipc-worker-" + mode for mode in _IPC_MODES}
):
    raise SystemExit(_fake_worker(sys.argv[1].removeprefix("ipc-worker-")))
else:
    import certifi

    from easysynq_api.services.audit import _history_spool as spool_module
    from easysynq_api.services.audit import _history_spool_protocol as wire
    from easysynq_api.services.audit import _history_spool_worker as worker_module
    from easysynq_api.services.audit import history_collection as collection
    from easysynq_api.services.audit import (
        isolated_raw,
        isolated_version_page,
        raw_transport,
        version_page_transport,
    )
    from easysynq_api.services.audit._history_spool_store import _SpoolStore
    from easysynq_api.services.audit.bootstrap_bridge import BridgeWitnessPin
    from easysynq_api.services.audit.sink import ExplicitHistoryReader
    from easysynq_api.services.audit.version_page import decode_checkpoint_version_page

_ORG = UUID("00000000-0000-4000-8000-000000000011")
_PREFIX = f"checkpoints/{_ORG}/"
_WITNESSES = (
    UUID("00000000-0000-4000-8000-000000000021"),
    UUID("00000000-0000-4000-8000-000000000022"),
)
_RESOURCE_KINDS = {
    "address_space": resource.RLIMIT_AS,
    "cpu_seconds": resource.RLIMIT_CPU,
    "file_descriptors": resource.RLIMIT_NOFILE,
    "core_bytes": resource.RLIMIT_CORE,
    "file_bytes": resource.RLIMIT_FSIZE,
}
_SOURCE_NAMES = (
    "history_collection.py",
    "_history_spool.py",
    "_history_spool_protocol.py",
    "_history_spool_store.py",
    "_history_spool_worker.py",
    "isolated_raw.py",
    "isolated_version_page.py",
    "raw_transport.py",
    "version_page_transport.py",
    "version_page.py",
)


def _runtime() -> dict[str, Any]:
    directory = Path(collection.__file__).parent
    return {
        "uid": os.getuid(),
        "dev_packages_absent": [
            name for name in ("mypy", "pytest", "ruff") if importlib.util.find_spec(name) is None
        ],
        "runtime_versions": {"python": platform.python_version(), "sqlite": sqlite3.sqlite_version},
        "installed_sources": {
            name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
            for name in _SOURCE_NAMES
        },
    }


def _limits(**changes: Any) -> collection.HistoryCollectionLimits:
    values = {
        "maximum_pages": 16,
        "maximum_observations": 6000,
        "maximum_total_bytes": 67_108_864,
        "maximum_spool_bytes": 67_108_864,
        "maximum_wall_seconds": 90,
        "maximum_issues": 32,
    }
    return collection.HistoryCollectionLimits(**(values | changes))


def _page(
    bucket: str,
    rows: list[tuple[str, str, str]],
    cursor: tuple[str | None, str | None] = (None, None),
    following: tuple[str | None, str | None] = (None, None),
    *,
    size: int = 0,
) -> bytes:
    fields = (
        '<ListVersionsResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        f"<Name>{bucket}</Name><Prefix>{quote(_PREFIX, safe='-_.~')}</Prefix>"
        "<MaxKeys>1000</MaxKeys><EncodingType>url</EncodingType>"
        f"<IsTruncated>{'true' if following[0] is not None else 'false'}</IsTruncated>"
    )
    for tag, value, encoded in (
        ("KeyMarker", cursor[0], True),
        ("VersionIdMarker", cursor[1], False),
        ("NextKeyMarker", following[0], True),
        ("NextVersionIdMarker", following[1], False),
    ):
        if value is not None:
            fields += f"<{tag}>{quote(value, safe='-_.~') if encoded else escape(value)}</{tag}>"
    for kind, key, version in rows:
        fields += (
            f"<{kind}><Key>{quote(key, safe='-_.~')}</Key>"
            f"<VersionId>{escape(version)}</VersionId><IsLatest>false</IsLatest></{kind}>"
        )
    body = (fields + "</ListVersionsResult>").encode()
    return body + b" " * max(0, size - len(body))


def _fd_inventory(pid: int) -> list[dict[str, Any]]:
    result = []
    for path in Path(f"/proc/{pid}/fd").iterdir():
        try:
            info = path.stat()
            target = os.readlink(path)
            flags = next(
                int(line.split()[1], 8)
                for line in Path(f"/proc/{pid}/fdinfo/{path.name}").read_text().splitlines()
                if line.startswith("flags:")
            )
        except FileNotFoundError:
            continue
        result.append(
            {
                "fd": int(path.name),
                "target": target,
                "regular": stat.S_ISREG(info.st_mode),
                "writable": bool(flags & os.O_ACCMODE != os.O_RDONLY),
                "size": info.st_size,
                "deleted": info.st_nlink == 0 or target.endswith(" (deleted)"),
            }
        )
    return sorted(result, key=lambda item: item["fd"])


class _ObservedSession(spool_module._SpoolSession):
    """Delegate the real session; collect independent OS observations at call boundaries."""

    records: ClassVar[list[Any]] = []
    forbidden: tuple[bytes, ...] = ()
    kill_after_body = False

    def __enter__(self) -> Any:
        self.records.append(self)
        self.observed_process: Any = None
        self.observed_directory: Path | None = None
        self.initial: dict[str, Any] | None = None
        self.samples: list[dict[str, Any]] = []
        self.cleanup_attempts = 0
        super().__enter__()
        assert self._process is not None and self._directory is not None
        self._remember_owned()
        process = self.observed_process
        directory = self.observed_directory
        self.initial = {
            "limits": {
                name: list(resource.prlimit(process.pid, kind))
                for name, kind in _RESOURCE_KINDS.items()
            },
            "directory_mode": stat.S_IMODE(directory.stat().st_mode),
            "database_mode": stat.S_IMODE((directory / "spool.sqlite3").stat().st_mode),
            "uid": (directory / "spool.sqlite3").stat().st_uid,
            "environment": (Path(f"/proc/{process.pid}/environ"))
            .read_bytes()
            .decode()
            .split("\0")[:-1],
            "argv_flags": Path(f"/proc/{process.pid}/cmdline").read_bytes().split(b"\0")[1:4],
        }
        self.initial["argv_flags"] = [value.decode() for value in self.initial["argv_flags"]]
        self.scan()
        return self

    def _remember_owned(self) -> None:
        if self._process is not None:
            self.observed_process = self._process
        if self._directory is not None:
            self.observed_directory = Path(self._directory)

    def _cleanup(self) -> list[BaseException]:
        # Failed __enter__ invokes this before returning to the observer. Preserve
        # only ownership handles; READY/INIT and OS policy may not be established.
        self._remember_owned()
        self.cleanup_attempts += 1
        return super()._cleanup()

    def scan(self) -> None:
        process = self.observed_process
        path = self.observed_directory / "spool.sqlite3"
        payload = path.read_bytes()
        assert all(value not in payload for value in self.forbidden)
        self.samples.append(
            {
                "database_bytes": len(payload),
                "fds": _fd_inventory(process.pid),
                "children": Path(f"/proc/{process.pid}/task/{process.pid}/children").read_text(),
                "directory_entries": sorted(p.name for p in self.observed_directory.iterdir()),
            }
        )

    def _send(self, payload: bytes, deadline: float) -> None:
        assert all(value not in payload for value in self.forbidden)
        super()._send(payload, deadline)

    def _send_upload(self, payload: bytes, deadline: float, request_id: int) -> None:
        assert all(value not in payload for value in self.forbidden)
        super()._send_upload(payload, deadline, request_id)

    def admit_page(self, ticket: Any, raw_body: bytes) -> Any:
        result = super().admit_page(ticket, raw_body)
        self.scan()
        return result

    def record_body(self, ordinal: int, body: bytes) -> None:
        super().record_body(ordinal, body)
        if ordinal in {1, 4096, 4097, 5000, 5002}:
            self.scan()
        if self.kill_after_body:
            assert self.observed_process.poll() is None
            os.killpg(self.observed_process.pid, signal.SIGKILL)
            # Observe death without reaping: the product retains ownership of wait().
            deadline = time.monotonic() + 2
            while ") Z " not in Path(f"/proc/{self.observed_process.pid}/stat").read_text():
                assert time.monotonic() < deadline
                time.sleep(0.005)

    def finish(self) -> Any:
        self.scan()
        return super().finish()

    def evidence(self) -> dict[str, Any]:
        process = self.observed_process
        directory = self.observed_directory
        return (self.initial or {}) | {
            "initialized": self.initial is not None,
            "worker_started": process is not None,
            "directory_created": directory is not None,
            "cleanup_attempts": self.cleanup_attempts,
            "samples": self.samples,
            "exit_code": None if process is None else process.returncode,
            "reaped": None if process is None else process.returncode is not None,
            "pipes_closed": None
            if process is None
            else process.stdin.closed and process.stdout.closed,
            "selector_closed": self._selector is None,
            "directory_removed": None if directory is None else not directory.exists(),
        }


@contextmanager
def _observe(forbidden: tuple[str, ...], *, kill: bool = False) -> Iterator[list[Any]]:
    original = spool_module._SpoolSession
    _ObservedSession.records = []
    _ObservedSession.forbidden = tuple(value.encode() for value in forbidden)
    _ObservedSession.kill_after_body = kill
    spool_module._SpoolSession = _ObservedSession
    try:
        yield _ObservedSession.records
    finally:
        spool_module._SpoolSession = original
        _ObservedSession.forbidden = ()
        _ObservedSession.kill_after_body = False


def _scope(config: dict[str, Any]) -> tuple[Any, Any]:
    readers = tuple(
        collection.RequiredHistoryWitness(
            UUID(item["witness_id"]),
            ExplicitHistoryReader(
                item["endpoint"],
                item["bucket"],
                "us-east-1",
                item["access_key"],
                item["secret_key"],
            ),
        )
        for item in config["witnesses"]
    )
    pins = tuple(
        BridgeWitnessPin(UUID(item["witness_id"]), item["namespace_hash"])
        for item in config["witnesses"]
    )
    return pins, readers


def _collection_case(config: dict[str, Any], mode: str) -> dict[str, Any]:
    pins, readers = _scope(config)
    forbidden = tuple(
        item[field] for item in config["witnesses"] for field in ("access_key", "secret_key")
    )
    original_page = isolated_version_page.read_raw_checkpoint_version_page_isolated
    original_raw = isolated_raw.read_raw_checkpoint_version_isolated
    pages: list[dict[str, Any]] = []
    reads: list[list[Any]] = []
    counts: dict[str, int] = {}
    list_counts: dict[str, int] = {}
    limits = _limits(maximum_wall_seconds=450 if mode == "provider" else 45)
    if mode == "wall":
        limits = _limits(maximum_wall_seconds=1)
    if mode == "file":
        limits = _limits(maximum_spool_bytes=1_048_576)
    if mode == "page-budget":
        limits = _limits(maximum_pages=1)
    if mode == "observation-budget":
        limits = _limits(maximum_observations=999)
    if mode == "byte-budget":
        limits = _limits(maximum_total_bytes=1)
    caller_cancel = threading.Event()

    def list_page(reader: Any, org: UUID, **kwargs: Any) -> Any:
        bucket = reader.bucket
        index = list_counts.get(bucket, 0)
        list_counts[bucket] = index + 1
        cursor = (kwargs["key_marker"], kwargs["version_id_marker"])
        if mode == "provider":
            value = original_page(reader, org, **kwargs)
        else:
            witness_b = bucket == readers[1].reader.bucket
            if witness_b and mode == "list-gap":
                raise version_page_transport.VersionPageReadError("PROVIDER_FAILURE")
            if mode == "wall":
                assert kwargs["cancel"].wait(3)
                raise version_page_transport.VersionPageReadCancelled()
            if mode == "cancel":
                caller_cancel.set()
                raise version_page_transport.VersionPageReadError("PROVIDER_FAILURE")
            large = mode in {"scaling", "file", "page-budget", "observation-budget", "byte-budget"}
            rows = [("Version", _PREFIX + "opaque%2F+雪", "null")] * (
                2 if witness_b else 1000 if large or mode == "boundary" else 2
            )
            following: tuple[str | None, str | None] = (None, None)
            if large and not witness_b and index < 4:
                following = (_PREFIX + f"cursor-{index + 1}", "null" if index % 2 else None)
            if mode == "cycle" and witness_b:
                following = (_PREFIX + ("cursor-a" if index != 1 else "cursor-b"), "null")
            if mode == "marker" and witness_b:
                rows = [("DeleteMarker", _PREFIX + "opaque%2F+雪", "marker")]
            if mode == "ineligible" and witness_b:
                rows = [("Version", _PREFIX + "control\x01", "null")]
            raw = _page(
                bucket,
                rows,
                cursor,
                following,
                size=16_777_216 if mode == "boundary" and not witness_b else 0,
            )
            decoded = decode_checkpoint_version_page(
                raw, bucket=bucket, org_id=org, key_marker=cursor[0], version_id_marker=cursor[1]
            )
            value = version_page_transport.RawCheckpointVersionPage(raw, decoded)
        page = value.page
        pages.append(
            {
                "bucket": bucket,
                "cursor": list(cursor),
                "next": [page.next_key_marker, page.next_version_id_marker],
                "truncated": page.truncated,
                "body_bytes": len(value.body),
                "body_sha256": hashlib.sha256(value.body).hexdigest(),
                "rows": [["version", row.key, row.version_id] for row in page.versions]
                + [["delete_marker", row.key, row.version_id] for row in page.delete_markers],
            }
        )
        return value

    def raw_read(reader: Any, ref: Any, **kwargs: Any) -> Any:
        index = counts.get(reader.bucket, 0) + 1
        counts[reader.bucket] = index
        if mode == "provider":
            value = original_raw(reader, ref, **kwargs)
        else:
            if reader.bucket == readers[1].reader.bucket and mode == "unavailable":
                raise raw_transport.RawVersionReadError("PROVIDER_FAILURE")
            if mode in {"boundary", "file"}:
                body = bytes(range(256)) * 256 if index == 1 or mode == "file" else b""
            else:
                body = b"\x00opaque unknown\xff" if index != 4097 else b"late contradiction"
            value = raw_transport.RawCheckpointVersion(ref.key, ref.version_id, body)
        reads.append(
            [
                reader.bucket,
                ref.key,
                ref.version_id,
                len(value.body),
                hashlib.sha256(value.body).hexdigest(),
            ]
        )
        return value

    isolated_version_page.read_raw_checkpoint_version_page_isolated = list_page
    isolated_raw.read_raw_checkpoint_version_isolated = raw_read
    before_threads = set(threading.enumerate())
    report = None
    outcome = "report"
    started = time.monotonic()
    try:
        with _observe(forbidden, kill=mode == "worker-death") as sessions:
            try:
                report = collection.collect_required_checkpoint_history(
                    _ORG, pins, readers, limits, cancel=caller_cancel
                )
            except collection.HistoryCollectionError as error:
                outcome = error.code
            except collection.HistoryCollectionCancelled:
                outcome = "cancelled"
            workers = [session.evidence() for session in sessions]
    finally:
        isolated_version_page.read_raw_checkpoint_version_page_isolated = original_page
        isolated_raw.read_raw_checkpoint_version_isolated = original_raw
    assert set(threading.enumerate()) == before_threads
    assert all(secret not in repr(report) for secret in forbidden)
    return {
        "outcome": outcome,
        "report": None if report is None else dataclasses.asdict(report),
        "pages": pages,
        "reads": reads,
        "attempted_exact_reads": sum(counts.values()),
        "list_attempts": sum(list_counts.values()),
        "workers": workers,
        "watchdogs_removed": True,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


def _store_case(mode: str, config: dict[str, Any]) -> dict[str, Any]:
    """Separate limited interpreter; introspect this connection only, never reopen it."""
    worker_module._apply_limits()
    effective = {name: list(resource.getrlimit(kind)) for name, kind in _RESOURCE_KINDS.items()}
    if mode == "memory":
        try:
            _allocation = bytearray(603_979_776)
        except MemoryError:
            return {"effective": effective, "outcome": "MemoryError"}
        raise AssertionError("memory control was not contained")
    if mode == "descriptors":
        opened = []
        try:
            for _ in range(64):
                opened.append(os.open(os.devnull, os.O_RDONLY))
        except OSError as error:
            return {"effective": effective, "errno": error.errno, "opened": len(opened)}
        finally:
            for fd in opened:
                os.close(fd)
        raise AssertionError("descriptor control was not contained")
    if mode == "cpu":
        print(_json({"effective": effective}).decode(), flush=True)
        while True:
            pass
    if mode == "file-limit":
        resource.setrlimit(resource.RLIMIT_FSIZE, (65_536, 65_536))
        with open("limited-file", "wb", buffering=0) as output:
            try:
                for _ in range(3):
                    output.write(b"f" * 65_536)
            except OSError as error:
                return {"effective": effective, "errno": error.errno, "bytes": output.tell()}
        raise AssertionError("file control was not contained")
    scopes = (wire._SpoolWitness(_WITNESSES[0], "a" * 64, "store-fixture"),)
    connection_events = []
    sys.addaudithook(
        lambda event, args: connection_events.append(args) if event == "sqlite3.connect" else None
    )
    store = _SpoolStore(_ORG, scopes, _limits())
    db = store._connection()
    try:
        if mode == "heap":
            db.set_authorizer(None)
            original_heap = db.execute("PRAGMA hard_heap_limit").fetchone()[0]
            try:
                db.execute("PRAGMA hard_heap_limit=1").fetchone()
            except MemoryError:
                pass
            db.set_authorizer(store._authorize)
            try:
                store.reserve_page(0, None, None)
            except collection.HistoryCollectionError as error:
                outcome = error.code
            else:
                raise AssertionError("heap control did not fail")
            try:
                store.finish()
            except collection.HistoryCollectionError:
                poisoned = True
            else:
                poisoned = False
            return {
                "effective": effective,
                "original_heap": original_heap,
                "outcome": outcome,
                "poisoned": poisoned,
                "closed": store._closed,
            }
        for page_index in range(5):
            cursor = (None, None) if page_index == 0 else (_PREFIX + str(page_index), None)
            raw = bytes.fromhex(config["store_pages"][page_index])
            ticket = store.reserve_page(0, *cursor)
            store.page_begin(ticket, len(raw))
            for offset in range(0, len(raw), 65_536):
                store.page_chunk(raw[offset : offset + 65_536])
            admitted = store.page_end()
            for ordinal in range(admitted.first_ordinal, admitted.first_ordinal + 1000):
                store.record_body(ordinal, ordinal.to_bytes(4, "big") + b"o" * 2044)
        retained_pages = [
            hashlib.sha256(row[0]).hexdigest()
            for row in db.execute("SELECT raw FROM pages ORDER BY id")
        ]
        retained_bodies = hashlib.sha256()
        for ordinal, body in db.execute("SELECT ordinal,body FROM observations ORDER BY ordinal"):
            retained_bodies.update(ordinal.to_bytes(8, "big"))
            retained_bodies.update(len(body).to_bytes(8, "big"))
            retained_bodies.update(body)
        denied = []
        for sql in (
            "ATTACH ':memory:' AS other",
            "DETACH other",
            "CREATE TABLE rogue(x)",
            "DROP TABLE observations",
            "PRAGMA temp_store=FILE",
            "VACUUM",
            "SELECT load_extension('untrusted')",
            "CREATE VIRTUAL TABLE rogue USING fts5(x)",
        ):
            try:
                db.execute(sql)
            except sqlite3.DatabaseError:
                denied.append(sql)
            else:
                raise AssertionError("forbidden store operation accepted")
        db.set_authorizer(None)
        names = (
            "page_size",
            "max_page_count",
            "journal_mode",
            "temp_store",
            "mmap_size",
            "cache_size",
            "hard_heap_limit",
            "synchronous",
            "trusted_schema",
            "threads",
            "busy_timeout",
        )
        settings = {name: db.execute("PRAGMA " + name).fetchone()[0] for name in names}
        columns = {
            table: [[row[1], row[2]] for row in db.execute("PRAGMA table_info(" + table + ")")]
            for table in ("cursors", "observations")
        }
        indexes = [
            row[0]
            for row in db.execute("SELECT sql FROM sqlite_master WHERE name='observation_locator'")
        ]
        if mode == "disk-journal":
            db.execute("PRAGMA journal_mode=DELETE")
        if mode == "disk-sort":
            db.execute("PRAGMA temp_store=FILE")
        seen: dict[tuple[Any, ...], dict[str, Any]] = {}

        def observe() -> int:
            for entry in _fd_inventory(os.getpid()):
                if entry["regular"] and entry["writable"]:
                    seen[(entry["target"], entry["deleted"])] = entry
            return 0

        db.set_progress_handler(observe, 1000)
        db.execute("BEGIN")
        db.execute("UPDATE observations SET body=substr(body,1,2047)||x'7a'")
        assert db.in_transaction
        observe()
        db.execute("ROLLBACK")
        sorted_rows = sum(
            1
            for _ in db.execute(
                "SELECT ordinal FROM observations ORDER BY body || printf('%01024d',ordinal)"
            )
        )
        db.set_progress_handler(None, 0)
        return {
            "effective": effective,
            "settings": settings,
            "columns": columns,
            "indexes": indexes,
            "rows": sorted_rows,
            "writable_fds": list(seen.values()),
            "retained_page_sha256": retained_pages,
            "retained_body_sha256": retained_bodies.hexdigest(),
            "denied_operations": len(denied),
            "connection_count": len(connection_events),
            "database_bytes": Path("spool.sqlite3").stat().st_size,
            "directory_entries": sorted(os.listdir(".")),
        }
    finally:
        store.close()
        assert len(connection_events) == 1


def _child_case(config: dict[str, Any], mode: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="resource-", dir=config["writable"]) as directory:
        started_cpu = resource.getrusage(resource.RUSAGE_CHILDREN)
        process = subprocess.Popen(  # noqa: S603 - fixed probe child and enumerated mode
            [
                sys.executable,
                "-I",
                "-B",
                "-u",
                __file__,
                "store-" + mode,
                "--config",
                config["config_path"],
            ],
            cwd=directory,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"LANG": "C.UTF-8", "TZ": "UTC"},
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=140 if mode == "cpu" else 45)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate(timeout=2)
        ended_cpu = resource.getrusage(resource.RUSAGE_CHILDREN)
        assert stderr == b""
        value = json.loads(stdout)
        entries = sorted(os.listdir(directory))
    return {
        "observation": value,
        "exit_code": process.returncode,
        "child_cpu_ms": int(
            (ended_cpu.ru_utime + ended_cpu.ru_stime - started_cpu.ru_utime - started_cpu.ru_stime)
            * 1000
        ),
        "files_before_cleanup": entries,
        "reaped": process.returncode is not None,
        "pipes_closed": process.stdout.closed and process.stderr.closed,
        "directory_removed": not Path(directory).exists(),
    }


def _open_transaction_death(config: dict[str, Any]) -> dict[str, Any]:
    scopes = (wire._SpoolWitness(_WITNESSES[0], "a" * 64, "store-fixture"),)
    spool = spool_module._SpoolSession(
        _ORG, scopes, _limits(), cancel=threading.Event(), deadline=time.monotonic() + 30
    )
    errors = []
    with spool:
        process = spool._process
        directory = Path(spool._directory)
        ticket = spool.reserve_page(0, None, None)
        spool.admit_page(
            ticket,
            _page(
                "store-fixture",
                [("Version", _PREFIX + "prefix", "null")],
                following=(_PREFIX + "next", None),
            ),
        )
        spool.record_body(1, b"committed opaque prefix")
        ticket = spool.reserve_page(0, _PREFIX + "next", None)
        database = directory / "spool.sqlite3"
        before = database.stat().st_size
        spool._sequence += 1
        spool._send(
            wire.encode(
                {
                    "op": "PAGE_BEGIN",
                    "id": spool._sequence,
                    "ticket": dataclasses.asdict(ticket),
                    "length": 2_097_152,
                }
            ),
            time.monotonic() + 10,
        )
        deadline = time.monotonic() + 5
        while database.stat().st_size <= before:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        during = database.stat().st_size
        descriptors = _fd_inventory(process.pid)
        os.killpg(process.pid, signal.SIGKILL)
        for operation in (spool.finish, lambda: spool.reserve_page(0, None, None)):
            try:
                operation()
            except collection.HistoryCollectionError as error:
                errors.append(error.code)
            else:
                raise AssertionError("dead storage published a result")
    return {
        "before_bytes": before,
        "during_bytes": during,
        "fds": descriptors,
        "errors": errors,
        "exit_code": process.returncode,
        "directory_removed": not directory.exists(),
        "pipes_closed": process.stdin.closed and process.stdout.closed,
        "selector_closed": spool._selector is None,
    }


def _leaves(error: BaseException) -> list[BaseException]:
    if isinstance(error, BaseExceptionGroup):
        return [leaf for member in error.exceptions for leaf in _leaves(member)]
    return [error]


def _cleanup_failure() -> dict[str, Any]:
    scopes = (wire._SpoolWitness(_WITNESSES[0], "a" * 64, "store-fixture"),)
    session = spool_module._SpoolSession(
        _ORG, scopes, _limits(), cancel=threading.Event(), deadline=time.monotonic() + 20
    )
    session.__enter__()
    process = session._process
    directory = Path(session._directory)
    original_remove = shutil.rmtree
    attempts = []

    def denied_remove(path: Any, *args: Any, **kwargs: Any) -> None:
        assert Path(path) == directory
        attempts.append(process.returncode)
        raise OSError("synthetic owned removal failure")

    try:
        ticket = session.reserve_page(0, None, None)
        session.admit_page(ticket, _page("store-fixture", []))
        shutil.rmtree = denied_remove
        try:
            session.finish()
        except (collection.HistoryCollectionError, ExceptionGroup) as error:
            codes = [
                leaf.code
                for leaf in _leaves(error)
                if type(leaf) is collection.HistoryCollectionError
            ]
        else:
            raise AssertionError("failed cleanup returned diagnostics")
        assert directory.exists()
    finally:
        shutil.rmtree = original_remove
        session.__exit__(None, None, None)
    return {
        "codes": codes,
        "removal_attempt_exit_codes": attempts,
        "directory_removed": not directory.exists(),
        "exit_code": process.returncode,
        "pipes_closed": process.stdin.closed and process.stdout.closed,
        "selector_closed": session._selector is None,
    }


def _upload_deadline() -> dict[str, Any]:
    scopes = (wire._SpoolWitness(_WITNESSES[0], "a" * 64, "store-fixture"),)
    session = spool_module._SpoolSession(
        _ORG, scopes, _limits(), cancel=threading.Event(), deadline=time.monotonic() + 20
    )
    chunks_sent = 0
    write_bytes_by_attempt = []
    deadlines = []
    started = time.monotonic()
    with session:
        process = session._process
        directory = Path(session._directory)
        ticket = session.reserve_page(0, None, None)
        original_send = session._send_upload
        original_write = spool_module._write_fd
        stdin_fd = process.stdin.fileno()

        def delayed_chunk(payload: bytes, deadline: float, request_id: int) -> None:
            nonlocal chunks_sent
            attempt = len(write_bytes_by_attempt)
            write_bytes_by_attempt.append(0)
            deadlines.append(deadline)

            def observe_write(fd: int, body: bytes) -> int:
                written = original_write(fd, body)
                if fd == stdin_fd:
                    write_bytes_by_attempt[attempt] += written
                return written

            try:
                spool_module._write_fd = observe_write
                original_send(payload, deadline, request_id)
            finally:
                spool_module._write_fd = original_write
            chunks_sent += 1
            # Actual elapsed time crosses the one upload-command deadline.
            if chunks_sent == 1:
                time.sleep(10.1)

        try:
            session._send_upload = delayed_chunk
            session.admit_page(ticket, _page("store-fixture", [], size=1_048_576))
        except collection.HistoryCollectionError as error:
            code = error.code
        else:
            raise AssertionError("upload deadline accepted a prefix")
        finally:
            session._send_upload = original_send
    return {
        "code": code,
        "chunk_attempts": len(write_bytes_by_attempt),
        "chunks_sent": chunks_sent,
        "write_bytes_by_attempt": write_bytes_by_attempt,
        "distinct_deadlines": len(set(deadlines)),
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "exit_code": process.returncode,
        "directory_removed": not directory.exists(),
        "pipes_closed": process.stdin.closed and process.stdout.closed,
    }


def _ipc_case(config: dict[str, Any], mode: str) -> dict[str, Any]:
    original = subprocess.Popen
    processes = []
    directories = []

    def spawn(argv: Any, **kwargs: Any) -> Any:
        assert argv[4] == str(Path(worker_module.__file__).resolve())
        process = original(
            [
                sys.executable,
                "-I",
                "-B",
                "-u",
                __file__,
                "ipc-worker-" + mode,
                "--config",
                config["config_path"],
            ],
            **kwargs,
        )
        processes.append(process)
        directories.append(Path(kwargs["cwd"]))
        return process

    scopes = (wire._SpoolWitness(_WITNESSES[0], "a" * 64, "store-fixture"),)
    session = spool_module._SpoolSession(
        _ORG, scopes, _limits(), cancel=threading.Event(), deadline=time.monotonic() + 10
    )
    original_receive = session._receive
    final_frames = []

    def receive(deadline: float) -> bytes:
        payload = original_receive(deadline)
        decoded = json.loads(payload[1:])
        if "summary" in decoded:
            final_frames.append(
                {"id": decoded["id"], "sha256": hashlib.sha256(payload).hexdigest()}
            )
        return payload

    session._receive = receive
    subprocess.Popen = spawn
    started = time.monotonic()
    try:
        try:
            with session:
                ticket = session.reserve_page(0, None, None)
                session.admit_page(ticket, _page("store-fixture", []))
                session.finish()
        except collection.HistoryCollectionError as error:
            code = error.code
        else:
            if mode != "valid-result":
                raise AssertionError("hostile worker returned a result")
            code = "accepted"
    finally:
        subprocess.Popen = original
    assert len(processes) == len(directories) == 1
    process = processes[0]
    return {
        "code": code,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "exit_code": process.returncode,
        "reaped": process.returncode is not None,
        "pipes_closed": process.stdin.closed and process.stdout.closed,
        "selector_closed": session._selector is None,
        "directory_removed": not directories[0].exists(),
        "spawn_kind": "adversarial-ipc-producer",
        "final_frames": final_frames,
    }


def _raw_worker_ready(process: subprocess.Popen[bytes], deadline: float) -> bytes:
    """One bounded READY frame; partial headers/bodies never reset the deadline."""
    expected = _literal_frame({"op": "READY", "version": 1})
    output = bytearray()
    target = 4
    fd = process.stdout.fileno()
    blocking = os.get_blocking(fd)
    with selectors.DefaultSelector() as selector:
        try:
            os.set_blocking(fd, False)
            selector.register(fd, selectors.EVENT_READ)
            while len(output) < target:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(process.args, 10)
                if not selector.select(remaining):
                    continue
                try:
                    chunk = os.read(fd, target - len(output))
                except BlockingIOError:
                    continue
                assert chunk, "actual worker READY frame ended early"
                output.extend(chunk)
                if len(output) == 4:
                    assert output == expected[:4], "actual worker READY frame length differs"
                    target = len(expected)
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(process.args, 10)
            assert output == expected, "actual worker READY metadata differs"
        finally:
            os.set_blocking(fd, blocking)
    return bytes(output)


def _raw_worker_case(config: dict[str, Any], mode: str) -> dict[str, Any]:
    """Malformed incoming frames sent to the actual installed storage executable."""
    scope = {
        "witness_id": str(_WITNESSES[0]),
        "namespace_hash": "a" * 64,
        "bucket": "store-fixture",
    }
    initialize = {
        "op": "INIT",
        "id": 1,
        "version": 1,
        "org_id": str(_ORG),
        "witnesses": [scope],
        "limits": dataclasses.asdict(_limits()),
    }
    if mode == "oversized-input":
        payload = (131_073).to_bytes(4, "big")
    elif mode == "truncated-input":
        payload = _literal_frame(initialize)[:-1]
    elif mode == "stale-input":
        payload = _literal_frame(initialize | {"id": 9})
    elif mode == "duplicate-field":
        raw = b'J{"op":"INIT","id":1,"id":1}'
        payload = len(raw).to_bytes(4, "big") + raw
    else:
        rows = [("Version", _PREFIX + "raw", "null")] * (1001 if mode == "entry-over" else 1)
        if mode in {"valid-empty", "trailing-chunk"}:
            rows = []
        raw = _page("store-fixture", rows)
        ticket = {"page_id": 1, "witness_index": 0, "page_index": 0}
        payload = _literal_frame(initialize) + _literal_frame(
            {"op": "RESERVE_PAGE", "id": 2, "witness": 0, "key": None, "version": None}
        )
        payload += _literal_frame(
            {
                "op": "PAGE_BEGIN",
                "id": 3,
                "ticket": ticket,
                "length": 16_777_217 if mode == "xml-over" else len(raw),
            }
        )
        for offset in range(0, len(raw), 65_536):
            chunk = (
                b"B"
                + (4 if mode == "chunk-sequence" else 3).to_bytes(8, "big")
                + offset.to_bytes(4, "big")
                + raw[offset : offset + 65_536]
            )
            payload += len(chunk).to_bytes(4, "big") + chunk
        payload += _literal_frame({"op": "PAGE_END", "id": 3})
        if mode == "trailing-chunk":
            chunk = b"B" + (3).to_bytes(8, "big") + len(raw).to_bytes(4, "big") + b"x"
            payload += len(chunk).to_bytes(4, "big") + chunk
        if mode == "body-over":
            payload += _literal_frame({"op": "BODY", "id": 4, "ordinal": 1, "length": 65_537})
        payload += _literal_frame({"op": "FINISH", "id": 5 if mode == "body-over" else 4})
    with tempfile.TemporaryDirectory(prefix="raw-worker-", dir=config["writable"]) as directory:
        process = subprocess.Popen(  # noqa: S603 - installed worker, fixed source root, malformed test bytes only
            [
                sys.executable,
                "-I",
                "-B",
                "-u",
                str(Path(worker_module.__file__).resolve()),
                str(Path(spool_module.__file__).resolve().parents[3]),
            ],
            cwd=directory,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"LANG": "C.UTF-8", "TZ": "UTC"},
            start_new_session=True,
        )
        startup_started = time.monotonic()
        exchange_started = None
        startup_elapsed_ms = 0
        ready = b""
        timeout_state = None
        try:
            ready = _raw_worker_ready(process, startup_started + 10)
            startup_elapsed_ms = int((time.monotonic() - startup_started) * 1000)
            exchange_started = time.monotonic()
            stdout, stderr = process.communicate(payload, timeout=5)
            exchange_elapsed_ms = int((time.monotonic() - exchange_started) * 1000)
            stdout = ready + stdout
        except subprocess.TimeoutExpired:
            observed_at = time.monotonic()
            timeout_state = {
                "mode": mode,
                "phase": "startup" if exchange_started is None else "exchange",
                "ready_observed": bool(ready),
                "startup_elapsed_ms": (
                    int((observed_at - startup_started) * 1000)
                    if exchange_started is None
                    else startup_elapsed_ms
                ),
                "exchange_elapsed_ms": (
                    0 if exchange_started is None else int((observed_at - exchange_started) * 1000)
                ),
                "exit_at_timeout": process.poll(),
            }
            raise
        finally:
            try:
                try:
                    if process.poll() is None:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                finally:
                    try:
                        # Also drain an exited-at-timeout child; it may still own open pipes.
                        process.communicate(timeout=2)
                    finally:
                        for stream in (process.stdin, process.stdout, process.stderr):
                            if not stream.closed:
                                stream.close()
            finally:
                if timeout_state is not None:
                    print(
                        "AUDIT_HISTORY_COLLECTION_RAW_WORKER_FAILURE "
                        + _json(
                            timeout_state
                            | {
                                "exit_after_cleanup": process.returncode,
                                "pipes_closed": all(
                                    stream.closed
                                    for stream in (process.stdin, process.stdout, process.stderr)
                                ),
                            }
                        ).decode(),
                        file=sys.stderr,
                        flush=True,
                    )
        assert stderr == b"" and len(stdout) <= 131_072
        frames = []
        while stdout:
            size = int.from_bytes(stdout[:4], "big")
            frame, stdout = stdout[4 : 4 + size], stdout[4 + size :]
            assert len(frame) == size and frame[:1] == b"J"
            frames.append(json.loads(frame[1:]))
        assert frames[0] == {"op": "READY", "version": 1}
        if mode != "valid-empty":
            assert all("summary" not in frame for frame in frames)
    return {
        "exit_code": process.returncode,
        "frames": frames,
        "reaped": process.returncode is not None,
        "pipes_closed": process.stdin.closed and process.stdout.closed and process.stderr.closed,
        "directory_removed": not Path(directory).exists(),
        "spawn_kind": "production-worker",
        "startup_elapsed_ms": startup_elapsed_ms,
        "exchange_elapsed_ms": exchange_elapsed_ms,
    }


def _deadline(_signum: int, _frame: Any) -> None:
    raise TimeoutError("finite runtime probe deadline exceeded")


def _resource_cases(config: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    subcase_started = started
    active = "initialization"
    timings: dict[str, int] = {}

    def run(name: str, operation: Callable[..., Any], *args: Any) -> Any:
        nonlocal active, subcase_started
        active, subcase_started = name, time.monotonic()
        observed = operation(*args)
        timings[name] = int((time.monotonic() - subcase_started) * 1000)
        return observed

    try:
        result = runtime | {
            "cases": {
                mode: run("cases/" + mode, _child_case, config, mode)
                for mode in (
                    "memory",
                    "descriptors",
                    "file-limit",
                    "cpu",
                    "heap",
                    "store",
                    "disk-journal",
                    "disk-sort",
                )
            },
            "open_transaction_death": run(
                "open_transaction_death", _open_transaction_death, config
            ),
            "cleanup_failure": run("cleanup_failure", _cleanup_failure),
            "upload_deadline": run("upload_deadline", _upload_deadline),
            "ipc": {mode: run("ipc/" + mode, _ipc_case, config, mode) for mode in _IPC_MODES},
            "raw_worker": {
                mode: run("raw_worker/" + mode, _raw_worker_case, config, mode)
                for mode in (
                    "valid-empty",
                    "oversized-input",
                    "truncated-input",
                    "stale-input",
                    "duplicate-field",
                    "chunk-sequence",
                    "trailing-chunk",
                    "entry-over",
                    "xml-over",
                    "body-over",
                )
            },
        }
    except BaseException as error:
        print(
            "AUDIT_HISTORY_COLLECTION_RESOURCE_FAILURE "
            + _json(
                {
                    "subcase": active,
                    "subcase_elapsed_ms": int((time.monotonic() - subcase_started) * 1000),
                    "total_elapsed_ms": int((time.monotonic() - started) * 1000),
                    "completed_subcases_ms": timings,
                    "exception_type": type(error).__name__,
                }
            ).decode(),
            file=sys.stderr,
            flush=True,
        )
        raise
    return result | {"subcase_timings_ms": timings}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode")
    parser.add_argument("--config", required=True)
    arguments = parser.parse_args()
    config = json.loads(Path(arguments.config).read_text())
    config["config_path"] = arguments.config
    if arguments.mode.startswith("store-"):
        result = _store_case(arguments.mode.removeprefix("store-"), config)
    elif arguments.mode == "certifi":
        bundle = Path(certifi.where())
        result = {
            "certifi_path": str(bundle),
            "certifi_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
        }
    else:
        signal.signal(signal.SIGALRM, _deadline)
        signal.alarm({"provider": 460, "synthetic": 60, "resources": 200}[arguments.mode])
        tempfile.tempdir = config["writable"]
        runtime = _runtime()
        if arguments.mode == "provider":
            result = runtime | {"provider": _collection_case(config, "provider")}
        elif arguments.mode == "synthetic":
            modes = (
                "scaling",
                "boundary",
                "list-gap",
                "unavailable",
                "marker",
                "ineligible",
                "cycle",
                "wall",
                "cancel",
                "file",
                "page-budget",
                "observation-budget",
                "byte-budget",
                "worker-death",
            )
            result = runtime | {"cases": {mode: _collection_case(config, mode) for mode in modes}}
        elif arguments.mode == "resources":
            result = _resource_cases(config, runtime)
        else:
            raise ValueError("unknown finite probe mode")
        result["writable_entries_after"] = sorted(os.listdir(config["writable"]))
        signal.alarm(0)
    print(_json(result).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
