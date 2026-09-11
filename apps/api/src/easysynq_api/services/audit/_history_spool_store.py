"""Single fixed-schema SQLite owner. Any unexpected operation poisons the store."""

from __future__ import annotations

import functools
import hashlib
import os
import sqlite3
import stat
from collections.abc import Callable
from typing import Concatenate
from uuid import UUID

from . import _history_spool_protocol as wire
from .history_collection import (
    HistoryCollectionError,
    HistoryCollectionIssue,
    HistoryCollectionLimits,
    HistoryWitnessSummary,
)
from .version_page import (
    CheckpointVersionPageDecodeError,
    CheckpointVersionPageInputError,
    decode_checkpoint_version_page,
)

_SCHEMA = (
    "CREATE TABLE witnesses (id INTEGER PRIMARY KEY, uuid BLOB NOT NULL UNIQUE, "
    "namespace_hash TEXT NOT NULL, bucket TEXT NOT NULL, terminal INTEGER NOT NULL DEFAULT 0, "
    "stopped TEXT, next_key BLOB NOT NULL, next_version BLOB NOT NULL)",
    "CREATE TABLE pages (id INTEGER PRIMARY KEY, witness INTEGER NOT NULL, "
    "page_index INTEGER NOT NULL, cursor_key BLOB NOT NULL, cursor_version BLOB NOT NULL, "
    "state TEXT NOT NULL, raw BLOB, failure TEXT, UNIQUE(witness,page_index))",
    "CREATE TABLE cursors (witness INTEGER NOT NULL, cursor_key BLOB NOT NULL, "
    "cursor_version BLOB NOT NULL, UNIQUE(witness,cursor_key,cursor_version))",
    "CREATE TABLE observations (ordinal INTEGER PRIMARY KEY, page INTEGER NOT NULL, "
    "witness INTEGER NOT NULL, key BLOB NOT NULL, version BLOB NOT NULL, kind TEXT NOT NULL, "
    "outcome TEXT, failure TEXT, body BLOB, digest BLOB, duplicate INTEGER NOT NULL DEFAULT 0)",
    "CREATE INDEX observation_locator ON observations(witness,key,version,digest)",
    "CREATE TABLE locators (witness INTEGER NOT NULL, key BLOB NOT NULL, version BLOB NOT NULL, "
    "first_ordinal INTEGER NOT NULL, conflict INTEGER NOT NULL DEFAULT 0, "
    "PRIMARY KEY(witness,key,version))",
    "CREATE TABLE issues (witness INTEGER NOT NULL, code TEXT NOT NULL, count INTEGER NOT NULL, "
    "r1 INTEGER, r2 INTEGER, r3 INTEGER, r4 INTEGER, PRIMARY KEY(witness,code))",
)
_TABLES = frozenset({"witnesses", "pages", "cursors", "observations", "locators", "issues"})


def _cursor(value: str | None) -> bytes:
    return b"\0" if value is None else b"\1" + value.encode("utf-8")


def _uncursor(value: bytes) -> str | None:
    return None if value == b"\0" else value[1:].decode("utf-8")


def _operation[**P, Result](
    method: Callable[Concatenate[_SpoolStore, P], Result],
) -> Callable[Concatenate[_SpoolStore, P], Result]:
    @functools.wraps(method)
    def owned(self: _SpoolStore, /, *args: P.args, **kwargs: P.kwargs) -> Result:
        if self._poisoned or self._closed:
            wire.invalid()
        try:
            return method(self, *args, **kwargs)
        except BaseException as error:  # noqa: BLE001 - all failures abandon the database
            self._poisoned = True
            primary: BaseException = error
            if isinstance(error, MemoryError):
                primary = HistoryCollectionError("RESOURCE_LIMIT")
            elif isinstance(error, sqlite3.Error):
                code = (
                    "RESOURCE_LIMIT"
                    if getattr(error, "sqlite_errorcode", None)
                    in {
                        sqlite3.SQLITE_FULL,
                        sqlite3.SQLITE_NOMEM,
                        sqlite3.SQLITE_TOOBIG,
                    }
                    else "STORAGE_FAILED"
                )
                primary = HistoryCollectionError(code)
            elif isinstance(
                error, (CheckpointVersionPageDecodeError, CheckpointVersionPageInputError)
            ):
                primary = HistoryCollectionError("PROTOCOL_INVALID")
            try:
                self.close()
            except BaseException as cleanup:  # noqa: BLE001 - preserve both identities
                raise BaseExceptionGroup(
                    "history storage operation and cleanup failed", [primary, cleanup]
                ) from None
            raise primary from None

    return owned


class _SpoolStore:
    """Private test seam: creates its sole database exclusively in the owned cwd."""

    def __init__(
        self,
        org_id: UUID,
        witnesses: tuple[wire._SpoolWitness, ...],
        limits: HistoryCollectionLimits,
    ) -> None:
        self._org = org_id
        self._witnesses = witnesses
        self._limits = limits
        self._db: sqlite3.Connection | None = None
        self._blob: sqlite3.Blob | None = None
        self._closed = False
        self._poisoned = False
        self._pending: wire._PageTicket | None = None
        self._upload_length = 0
        self._upload_offset = 0
        self._cycle: int | None = None
        self._total = 0
        self._pages = 0
        self._ordinal = 0
        self._initialize()

    @_operation
    def _initialize(self) -> None:
        directory = os.stat(".")
        if (
            not stat.S_ISDIR(directory.st_mode)
            or stat.S_IMODE(directory.st_mode) != 0o700
            or directory.st_uid != os.geteuid()
            or os.listdir(".")
        ):
            raise HistoryCollectionError("STORAGE_FAILED")
        fd = os.open("spool.sqlite3", os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            original = os.fstat(fd)
            self._verify_file(original)
        finally:
            os.close(fd)
        self._db = sqlite3.connect("spool.sqlite3", isolation_level=None, timeout=0)
        db = self._db
        opened = os.lstat("spool.sqlite3")
        self._verify_file(opened)
        if (original.st_dev, original.st_ino) != (opened.st_dev, opened.st_ino):
            raise HistoryCollectionError("STORAGE_FAILED")
        db.enable_load_extension(False)
        for category, value in (
            (sqlite3.SQLITE_LIMIT_ATTACHED, 0),
            (sqlite3.SQLITE_LIMIT_LENGTH, wire.PAGE_MAX + 65_536),
            (sqlite3.SQLITE_LIMIT_SQL_LENGTH, 8_192),
            (sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 32),
            (sqlite3.SQLITE_LIMIT_COLUMN, 32),
        ):
            db.setlimit(category, value)
            if db.getlimit(category) != value:
                raise HistoryCollectionError("RUNTIME_UNSUPPORTED")
        options = {row[0] for row in db.execute("PRAGMA compile_options")}
        if "TEMP_STORE=0" in options:
            raise HistoryCollectionError("RUNTIME_UNSUPPORTED")
        settings = (
            ("page_size", 4_096),
            ("max_page_count", self._limits.maximum_spool_bytes // 4_096),
            ("journal_mode", "memory"),
            ("temp_store", 2),
            ("mmap_size", 0),
            ("cache_size", -1_024),
            ("hard_heap_limit", 67_108_864),
            ("synchronous", 0),
            ("trusted_schema", 0),
            ("threads", 0),
            ("busy_timeout", 0),
        )
        for name, setting_value in settings:
            # Both name and value are from this fixed policy tuple, never protocol SQL.
            db.execute(f"PRAGMA {name}={setting_value}")
            result = db.execute(f"PRAGMA {name}").fetchone()
            if result is None or result[0] != setting_value:
                raise HistoryCollectionError("RUNTIME_UNSUPPORTED")
        for statement in _SCHEMA:
            db.execute(statement)
        for index, witness in enumerate(self._witnesses):
            db.execute(
                "INSERT INTO witnesses(id,uuid,namespace_hash,bucket,next_key,next_version) "
                "VALUES(?,?,?,?,?,?)",
                (
                    index,
                    witness.witness_id.bytes,
                    witness.namespace_hash,
                    witness.bucket,
                    b"\0",
                    b"\0",
                ),
            )
        db.set_authorizer(self._authorize)

    @staticmethod
    def _verify_file(info: os.stat_result) -> None:
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
        ):
            raise HistoryCollectionError("STORAGE_FAILED")

    @staticmethod
    def _authorize(
        action: int, arg1: str | None, arg2: str | None, _database: str | None, _source: str | None
    ) -> int:
        if action in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_TRANSACTION}:
            return sqlite3.SQLITE_OK
        if (
            action
            in {
                sqlite3.SQLITE_READ,
                sqlite3.SQLITE_INSERT,
                sqlite3.SQLITE_UPDATE,
                sqlite3.SQLITE_DELETE,
            }
            and arg1 in _TABLES
        ):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION and arg2 in {"count", "sum", "coalesce", "zeroblob"}:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    def _connection(self) -> sqlite3.Connection:
        if self._db is None:
            wire.invalid()
        return self._db

    def _idle(self) -> None:
        if (
            self._pending is not None
            or self._blob is not None
            or self._cycle is not None
            or self._connection()
            .execute("SELECT ordinal FROM observations WHERE outcome IS NULL LIMIT 1")
            .fetchone()
            is not None
        ):
            wire.invalid()

    def _budget(self, *, added_bytes: int = 0, added_observations: int = 0) -> None:
        if (
            self._total + added_bytes > self._limits.maximum_total_bytes
            or self._ordinal + added_observations > self._limits.maximum_observations
        ):
            raise HistoryCollectionError("RESOURCE_LIMIT")

    def _issue(self, witness: int, code: str, ordinal: int | None = None) -> None:
        db = self._connection()
        old = db.execute(
            "SELECT count,r1,r2,r3,r4 FROM issues WHERE witness=? AND code=?", (witness, code)
        ).fetchone()
        if old is None:
            db.execute(
                "INSERT INTO issues VALUES(?,?,?,?,?,?,?)",
                (witness, code, 1, ordinal, None, None, None),
            )
        else:
            reps = list(old[1:])
            if ordinal is not None and None in reps:
                reps[reps.index(None)] = ordinal
            db.execute(
                "UPDATE issues SET count=?,r1=?,r2=?,r3=?,r4=? WHERE witness=? AND code=?",
                (old[0] + 1, *reps, witness, code),
            )

    @_operation
    def reserve_page(
        self, witness: int, key: str | None, version: str | None
    ) -> wire._PageTicket | None:
        self._idle()
        wire.integer(witness, 0, len(self._witnesses) - 1)
        wire.label(key, optional=True)
        wire.label(version, optional=True)
        if (version is not None and key is None) or (
            key is not None and not key.startswith(f"checkpoints/{self._org}/")
        ):
            wire.invalid()
        db = self._connection()
        state = db.execute(
            "SELECT terminal,stopped,next_key,next_version FROM witnesses WHERE id=?", (witness,)
        ).fetchone()
        pair = (_cursor(key), _cursor(version))
        if state[0] or state[1] is not None or pair != state[2:]:
            wire.invalid()
        if (
            db.execute(
                "SELECT 1 FROM cursors WHERE witness=? AND cursor_key=? AND cursor_version=?",
                (witness, *pair),
            ).fetchone()
            is not None
        ):
            self._cycle = witness
            return None
        if self._pages >= self._limits.maximum_pages:
            raise HistoryCollectionError("RESOURCE_LIMIT")
        page_index = db.execute(
            "SELECT count(*) FROM pages WHERE witness=?", (witness,)
        ).fetchone()[0]
        result = wire._PageTicket(self._pages + 1, witness, page_index)
        db.execute("BEGIN")
        db.execute("INSERT INTO cursors VALUES(?,?,?)", (witness, *pair))
        db.execute(
            "INSERT INTO pages(id,witness,page_index,cursor_key,cursor_version,state) "
            "VALUES(?,?,?,?,?,?)",
            (result.page_id, witness, page_index, *pair, "reserved"),
        )
        db.execute("COMMIT")
        self._pages += 1
        self._pending = result
        return result

    def _check_ticket(self, ticket: wire._PageTicket) -> None:
        if type(ticket) is not wire._PageTicket or ticket != self._pending:
            wire.invalid()

    @_operation
    def page_begin(self, ticket: wire._PageTicket, length: int) -> None:
        self._check_ticket(ticket)
        if self._blob is not None:
            wire.invalid()
        wire.integer(length, 1, wire.PAGE_MAX)
        self._budget(added_bytes=length)
        db = self._connection()
        db.execute("BEGIN")
        # Stage the final state with the original zeroblob update. It becomes
        # visible only at PAGE_END's commit; updating this BLOB-bearing row again
        # would transiently copy a maximum page and consume the spool budget twice.
        db.execute(
            "UPDATE pages SET raw=zeroblob(?),state='admitted' WHERE id=?", (length, ticket.page_id)
        )
        self._blob = db.blobopen("pages", "raw", ticket.page_id)
        self._upload_length = length
        self._upload_offset = 0

    @_operation
    def page_chunk(self, body: bytes) -> None:
        if (
            self._blob is None
            or type(body) is not bytes
            or not 0 < len(body) <= wire.CHUNK_MAX
            or self._upload_offset + len(body) > self._upload_length
        ):
            wire.invalid()
        self._blob.write(body)
        self._upload_offset += len(body)

    @_operation
    def page_end(self) -> wire._PageAdmission:
        if (
            self._blob is None
            or self._pending is None
            or self._upload_offset != self._upload_length
        ):
            wire.invalid()
        self._blob.seek(0)
        raw = self._blob.read()
        self._blob.close()
        self._blob = None
        ticket = self._pending
        db = self._connection()
        cursors = db.execute(
            "SELECT cursor_key,cursor_version FROM pages WHERE id=?", (ticket.page_id,)
        ).fetchone()
        page = decode_checkpoint_version_page(
            raw,
            bucket=self._witnesses[ticket.witness_index].bucket,
            org_id=self._org,
            key_marker=_uncursor(cursors[0]),
            version_id_marker=_uncursor(cursors[1]),
        )
        count = len(page.versions) + len(page.delete_markers)
        self._budget(added_observations=count)
        admission = wire._PageAdmission(
            self._ordinal + 1 if count else 0, len(page.versions), len(page.delete_markers)
        )
        for kind, entries in (("version", page.versions), ("marker", page.delete_markers)):
            for entry in entries:
                self._ordinal += 1
                db.execute(
                    "INSERT INTO observations(ordinal,page,witness,key,version,kind,outcome) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (
                        self._ordinal,
                        ticket.page_id,
                        ticket.witness_index,
                        entry.key.encode(),
                        entry.version_id.encode(),
                        kind,
                        "marker" if kind == "marker" else None,
                    ),
                )
                if kind == "marker":
                    self._issue(ticket.witness_index, "DELETE_OBSERVATION", self._ordinal)
        db.execute(
            "UPDATE witnesses SET terminal=?,next_key=?,next_version=? WHERE id=?",
            (
                int(not page.truncated),
                _cursor(page.next_key_marker),
                _cursor(page.next_version_id_marker),
                ticket.witness_index,
            ),
        )
        db.execute("COMMIT")
        self._total += self._upload_length
        self._pending = None
        return admission

    @_operation
    def record_list_failure(self, ticket: wire._PageTicket, code: str) -> None:
        self._check_ticket(ticket)
        if self._blob is not None or type(code) is not str or code not in wire.LIST_CODES:
            wire.invalid()
        db = self._connection()
        db.execute("BEGIN")
        db.execute("UPDATE pages SET state='failure',failure=? WHERE id=?", (code, ticket.page_id))
        db.execute(
            "UPDATE witnesses SET stopped='LIST_UNAVAILABLE' WHERE id=?", (ticket.witness_index,)
        )
        self._issue(ticket.witness_index, "LIST_UNAVAILABLE")
        db.execute("COMMIT")
        self._pending = None

    def _pending_version(self, ordinal: int) -> tuple[int, bytes, bytes]:
        wire.integer(ordinal, 1, self._limits.maximum_observations)
        if self._pending is not None or self._blob is not None or self._cycle is not None:
            wire.invalid()
        row = (
            self._connection()
            .execute(
                "SELECT witness,key,version,kind,outcome FROM observations WHERE ordinal=?",
                (ordinal,),
            )
            .fetchone()
        )
        if row is None or row[3] != "version" or row[4] is not None:
            wire.invalid()
        witness, key, version = row[:3]
        if type(witness) is not int or type(key) is not bytes or type(version) is not bytes:
            wire.invalid()
        return witness, key, version

    @_operation
    def record_body(self, ordinal: int, body: bytes) -> None:
        locator = self._pending_version(ordinal)
        if type(body) is not bytes or len(body) > wire.CHUNK_MAX:
            wire.invalid()
        self._budget(added_bytes=len(body))
        db = self._connection()
        digest = hashlib.sha256(body).digest()
        duplicate = any(
            row[0] == body
            for row in db.execute(
                "SELECT body FROM observations WHERE witness=? AND key=? "
                "AND version=? AND digest=?",
                (*locator, digest),
            )
        )
        prior = db.execute(
            "SELECT first_ordinal,conflict FROM locators WHERE witness=? AND key=? AND version=?",
            locator,
        ).fetchone()
        db.execute("BEGIN")
        db.execute(
            "UPDATE observations SET outcome='body',body=?,digest=?,duplicate=? WHERE ordinal=?",
            (body, digest, int(duplicate), ordinal),
        )
        if prior is None:
            db.execute(
                "INSERT INTO locators(witness,key,version,first_ordinal) VALUES(?,?,?,?)",
                (*locator, ordinal),
            )
        elif not prior[1]:
            first = db.execute(
                "SELECT body FROM observations WHERE ordinal=?", (prior[0],)
            ).fetchone()
            if first[0] != body:
                db.execute(
                    "UPDATE locators SET conflict=1 WHERE witness=? AND key=? AND version=?",
                    locator,
                )
                self._issue(locator[0], "LOCATOR_CONFLICT", ordinal)
        db.execute("COMMIT")
        self._total += len(body)

    @_operation
    def record_version_failure(
        self, ordinal: int, code: str, *, ineligible: bool = False, delete_marker: bool = False
    ) -> None:
        witness, _, _ = self._pending_version(ordinal)
        if (
            type(ineligible) is not bool
            or type(delete_marker) is not bool
            or type(code) is not str
            or (ineligible and (delete_marker or code != "INELIGIBLE_LOCATOR"))
            or (not ineligible and code not in wire.VERSION_CODES)
            or (delete_marker != (code == "DELETE_MARKER"))
        ):
            wire.invalid()
        issue = (
            "INELIGIBLE_LOCATOR"
            if ineligible
            else "DELETE_OBSERVATION"
            if delete_marker
            else "VERSION_UNAVAILABLE"
        )
        db = self._connection()
        db.execute("BEGIN")
        db.execute(
            "UPDATE observations SET outcome='unavailable',failure=? WHERE ordinal=?",
            (code, ordinal),
        )
        self._issue(witness, issue, ordinal)
        db.execute("COMMIT")

    @_operation
    def record_cycle(self, witness: int) -> None:
        wire.integer(witness, 0, len(self._witnesses) - 1)
        if self._cycle != witness:
            wire.invalid()
        db = self._connection()
        db.execute("BEGIN")
        db.execute("UPDATE witnesses SET stopped='CURSOR_CYCLE' WHERE id=?", (witness,))
        self._issue(witness, "CURSOR_CYCLE")
        db.execute("COMMIT")
        self._cycle = None

    @_operation
    def finish(self) -> wire._SpoolSummary:
        self._idle()
        db = self._connection()
        summaries = []
        for index, scope in enumerate(self._witnesses):
            terminal, stopped = db.execute(
                "SELECT terminal,stopped FROM witnesses WHERE id=?", (index,)
            ).fetchone()
            attempts, admitted = db.execute(
                "SELECT count(*),coalesce(sum(state='admitted'),0) FROM pages WHERE witness=?",
                (index,),
            ).fetchone()
            if not attempts or (not terminal and stopped is None):
                wire.invalid()
            versions, markers, success, unavailable, duplicates = db.execute(
                "SELECT coalesce(sum(kind='version'),0),coalesce(sum(kind='marker'),0),"
                "coalesce(sum(outcome='body'),0),coalesce(sum(outcome='unavailable'),0),"
                "coalesce(sum(duplicate),0) FROM observations WHERE witness=?",
                (index,),
            ).fetchone()
            conflicts = db.execute(
                "SELECT count(*) FROM locators WHERE witness=? AND conflict=1", (index,)
            ).fetchone()[0]
            summaries.append(
                HistoryWitnessSummary(
                    scope.witness_id,
                    scope.namespace_hash,
                    bool(terminal),
                    attempts,
                    admitted,
                    versions,
                    markers,
                    success,
                    unavailable,
                    duplicates,
                    conflicts,
                )
            )
        issues = []
        for witness, code, count, *reps in db.execute(
            "SELECT witness,code,count,r1,r2,r3,r4 FROM issues ORDER BY witness,code"
        ):
            checked_code = wire.issue_code(code)
            issues.append(
                HistoryCollectionIssue(
                    checked_code,
                    wire.ISSUES[checked_code],
                    self._witnesses[witness].witness_id,
                    count,
                    tuple(value for value in reps if value is not None),
                )
            )
        failed = sum(issue.severity == "failed" for issue in issues)
        incomplete = len(issues) - failed
        maximum = self._limits.maximum_issues
        return wire._SpoolSummary(
            tuple(summaries),
            tuple(issues[:maximum]),
            failed,
            incomplete,
            max(0, len(issues) - maximum),
            self._total,
        )

    def close(self) -> None:
        faults = []
        if self._blob is not None:
            try:
                self._blob.close()
            except BaseException as error:  # noqa: BLE001 - still close the connection
                faults.append(error)
            self._blob = None
        if self._db is not None:
            try:
                self._db.close()
            except BaseException as error:  # noqa: BLE001 - retain failed owned close
                faults.append(error)
            self._db = None
        self._closed = True
        if faults:
            raise BaseExceptionGroup("history storage cleanup failed", faults)
