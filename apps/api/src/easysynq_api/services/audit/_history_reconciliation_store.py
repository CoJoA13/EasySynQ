"""One fixed database: immutable evidence followed by private derived indexes."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable
from typing import Concatenate, cast

from . import _history_reconciliation_protocol as protocol
from . import _history_spool_protocol as wire
from . import _history_spool_store as base
from .history_collection import HistoryCollectionError
from .history_reconciliation import HistoryReconciliationLimits

# Every relation is created before the authorizer is installed. Later kernels
# insert only into these fixed indexes; the protocol never accepts SQL or schema.
_SCHEMA = (
    "CREATE TABLE raw_bodies(raw_id INTEGER PRIMARY KEY, digest BLOB NOT NULL, byte_length "
    "INTEGER NOT NULL, representative_ordinal INTEGER NOT NULL, format TEXT, decoded_state "
    "TEXT)",
    "CREATE INDEX raw_lookup ON raw_bodies(digest,byte_length,raw_id)",
    "CREATE TABLE body_deliveries(ordinal INTEGER PRIMARY KEY, raw_id INTEGER NOT NULL)",
    "CREATE INDEX raw_deliveries ON body_deliveries(raw_id,ordinal)",
    "CREATE INDEX exact_deliveries ON observations(witness,key,version,ordinal)",
    "CREATE TABLE reconciliation_issues(component TEXT NOT NULL, severity TEXT NOT NULL, code "
    "TEXT NOT NULL, s0 TEXT NOT NULL, s1 TEXT NOT NULL, s2 TEXT NOT NULL, s3 TEXT NOT NULL, "
    "witness_id BLOB, count INTEGER NOT NULL, PRIMARY "
    "KEY(component,severity,code,s0,s1,s2,s3))",
    "CREATE INDEX issue_display ON reconciliation_issues(severity,component,code,s0,s1,s2,s3)",
    "CREATE TABLE issue_refs(component TEXT NOT NULL, severity TEXT NOT NULL, code TEXT NOT "
    "NULL, s0 TEXT NOT NULL, s1 TEXT NOT NULL, s2 TEXT NOT NULL, s3 TEXT NOT NULL, slot "
    "INTEGER NOT NULL CHECK(slot IN (0,1)), kind TEXT NOT NULL, reference_index INTEGER NOT "
    "NULL, o0 TEXT NOT NULL, o1 TEXT NOT NULL, o2 TEXT NOT NULL, o3 TEXT NOT NULL, value BLOB "
    "NOT NULL, PRIMARY KEY(component,severity,code,s0,s1,s2,s3,slot))",
    "CREATE TABLE package_deliveries(position INTEGER PRIMARY KEY, kind TEXT NOT NULL, "
    "byte_length INTEGER NOT NULL, raw_digest BLOB, raw_body BLOB, wire_valid INTEGER NOT "
    "NULL)",
    "CREATE TABLE bridge_pages(page_hash TEXT PRIMARY KEY, page_index INTEGER NOT NULL, "
    "representative_position INTEGER NOT NULL, entry_count INTEGER NOT NULL)",
    "CREATE INDEX bridge_page_index ON bridge_pages(page_index,page_hash)",
    "CREATE TABLE root_pages(page_index INTEGER PRIMARY KEY, page_hash TEXT NOT NULL UNIQUE, "
    "entry_count INTEGER NOT NULL)",
    "CREATE TABLE page_deliveries(position INTEGER PRIMARY KEY, page_hash TEXT NOT NULL)",
    "CREATE INDEX canonical_page_deliveries ON page_deliveries(page_hash,position)",
    "CREATE TABLE page_entries(page_hash TEXT NOT NULL, entry_position INTEGER NOT NULL, "
    "witness BLOB NOT NULL, key BLOB NOT NULL, version BLOB NOT NULL, body_hash TEXT NOT "
    "NULL, body_length INTEGER NOT NULL, PRIMARY KEY(page_hash,entry_position))",
    "CREATE INDEX page_entry_locator ON page_entries(witness,key,version,page_hash,entry_position)",
    "CREATE TABLE committed_entries(witness BLOB NOT NULL, key BLOB NOT NULL, version BLOB "
    "NOT NULL, page_hash TEXT NOT NULL, entry_position INTEGER NOT NULL, body_hash TEXT NOT "
    "NULL, body_length INTEGER NOT NULL, PRIMARY KEY(witness,key,version))",
    "CREATE TABLE legacy_bodies(raw_id INTEGER PRIMARY KEY, authentication_state TEXT NOT "
    "NULL, audit_id INTEGER, row_hash TEXT, commitment TEXT)",
    "CREATE INDEX legacy_heads ON legacy_bodies(audit_id,row_hash,raw_id)",
    "CREATE TABLE legacy_membership(ordinal INTEGER PRIMARY KEY, committed INTEGER NOT NULL, "
    "exact_match INTEGER NOT NULL)",
    "CREATE TABLE legacy_witness_heads(witness BLOB NOT NULL, audit_id INTEGER NOT NULL, "
    "row_hash TEXT NOT NULL, representative_ordinal INTEGER NOT NULL, PRIMARY "
    "KEY(witness,audit_id,row_hash))",
    "CREATE TABLE v2_routes(raw_id INTEGER PRIMARY KEY, key_id TEXT, state TEXT NOT NULL)",
    "CREATE INDEX waiting_routes ON v2_routes(key_id,state,raw_id)",
    "CREATE TABLE v2_nodes(envelope_hash TEXT PRIMARY KEY, anchor_id TEXT NOT NULL, "
    "predecessor_hash TEXT NOT NULL, sequence INTEGER NOT NULL, key_id TEXT NOT NULL, "
    "key_epoch INTEGER NOT NULL, audit_id INTEGER NOT NULL, row_hash TEXT NOT NULL, "
    "next_key_id TEXT, next_public_key BLOB, representative_raw_id INTEGER NOT NULL, "
    "edge_state TEXT NOT NULL)",
    "CREATE INDEX waiting_edges ON v2_nodes(predecessor_hash,edge_state,envelope_hash)",
    "CREATE INDEX anchor_identity ON v2_nodes(anchor_id,envelope_hash)",
    "CREATE INDEX v2_heads ON v2_nodes(audit_id,row_hash,envelope_hash)",
    "CREATE TABLE v2_raw_nodes(raw_id INTEGER PRIMARY KEY, envelope_hash TEXT NOT NULL)",
    "CREATE INDEX node_raws ON v2_raw_nodes(envelope_hash,raw_id)",
    "CREATE TABLE materials(key_id TEXT PRIMARY KEY, public_key BLOB NOT NULL)",
    "CREATE TABLE material_events(event_id INTEGER PRIMARY KEY, key_id TEXT NOT NULL UNIQUE, "
    "last_raw_id INTEGER NOT NULL DEFAULT 0, done INTEGER NOT NULL DEFAULT 0)",
    "CREATE INDEX pending_materials ON material_events(done,event_id)",
    "CREATE TABLE parent_events(event_id INTEGER PRIMARY KEY, predecessor_hash TEXT NOT NULL "
    "UNIQUE, last_envelope_hash TEXT NOT NULL DEFAULT '', done INTEGER NOT NULL DEFAULT 0)",
    "CREATE INDEX pending_parents ON parent_events(done,event_id)",
    "CREATE TABLE admitted_nodes(envelope_hash TEXT PRIMARY KEY, predecessor_hash TEXT NOT "
    "NULL, permitted_next_key_id TEXT NOT NULL, permitted_next_epoch INTEGER NOT NULL)",
    "CREATE INDEX admitted_successors ON admitted_nodes(predecessor_hash,envelope_hash)",
    "CREATE TABLE path_nodes(sequence INTEGER PRIMARY KEY, envelope_hash TEXT NOT NULL "
    "UNIQUE, key_id TEXT NOT NULL, key_epoch INTEGER NOT NULL)",
    "CREATE TABLE used_epochs(key_epoch INTEGER PRIMARY KEY, key_id TEXT NOT NULL, "
    "introduced_by TEXT)",
    "CREATE TABLE v2_deliveries(witness BLOB NOT NULL, envelope_hash TEXT NOT NULL, "
    "representative_ordinal INTEGER NOT NULL, PRIMARY KEY(witness,envelope_hash))",
)
_TABLES = frozenset(
    {
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
)
_WRITES = frozenset({sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE})


def _raw_digest(body: bytes) -> bytes:
    return hashlib.sha256(body).digest()


def _operation[**P, Result](
    method: Callable[Concatenate[_ReconciliationStore, P], Result],
) -> Callable[Concatenate[_ReconciliationStore, P], Result]:
    @base._operation
    def run(self: base._SpoolStore, /, *args: P.args, **kwargs: P.kwargs) -> Result:
        if not isinstance(self, _ReconciliationStore):
            wire.invalid()
        return method(self, *args, **kwargs)

    return run


class _ReconciliationStore(base._SpoolStore):
    def __init__(self, scope: protocol._PublicScope) -> None:
        self._scope = scope
        self._reconciliation_limits = scope.limits
        self._package_bytes = 0
        self._identity_work = 0
        self._identity_after = 0
        self._identity_ordinal: int | None = None
        self._identity_candidate = 0
        self._identity_digest = b""
        self._identity_done = False
        self._issue_group_count = 0
        self._next_package = 0 if scope.root_length is not None else 1
        self._state = "package" if self._next_package <= len(scope.page_lengths) else "collecting"
        self._package_position: int | None = None
        self._package_length = 0
        self._package_offset = 0
        self._package_digest = hashlib.sha256()
        super().__init__(
            scope.enrollment.stream.org_id,
            scope.witnesses,
            protocol.collection_limits(scope.limits),
        )

    @property
    def scope(self) -> protocol._PublicScope:
        return self._scope

    @property
    def limits(self) -> HistoryReconciliationLimits:
        return self._reconciliation_limits

    @property
    def state(self) -> str:
        return self._state

    @property
    def package_bytes(self) -> int:
        return self._package_bytes

    def _create_schema(self, db: sqlite3.Connection) -> None:
        super()._create_schema(db)
        for statement in _SCHEMA:
            db.execute(statement)

    @staticmethod
    def _authorize(
        action: int,
        arg1: str | None,
        arg2: str | None,
        _database: str | None,
        _source: str | None,
    ) -> int:
        if action in _WRITES | {sqlite3.SQLITE_READ} and arg1 in _TABLES:
            return sqlite3.SQLITE_OK
        return base._SpoolStore._authorize(action, arg1, arg2, _database, _source)

    @staticmethod
    def _sealed_authorize(
        action: int,
        arg1: str | None,
        arg2: str | None,
        database: str | None,
        source: str | None,
    ) -> int:
        if action in _WRITES and arg1 in base._TABLES | {"package_deliveries"}:
            return sqlite3.SQLITE_DENY
        return _ReconciliationStore._authorize(action, arg1, arg2, database, source)

    def _budget(self, *, added_bytes: int = 0, added_observations: int = 0) -> None:
        super()._budget(added_bytes=added_bytes, added_observations=added_observations)
        if (
            self._package_bytes + self._total + added_bytes
            > self._reconciliation_limits.maximum_total_bytes
        ):
            raise HistoryCollectionError("RESOURCE_LIMIT")

    def _require_collecting(self) -> None:
        if self._state != "collecting":
            wire.invalid()

    def _idle(self) -> None:
        self._require_collecting()
        super()._idle()

    def _check_ticket(self, ticket: wire._PageTicket) -> None:
        self._require_collecting()
        super()._check_ticket(ticket)

    def _pending_version(self, ordinal: int) -> tuple[int, bytes, bytes]:
        self._require_collecting()
        return super()._pending_version(ordinal)

    @_operation
    def package_begin(self, kind: str, index: int, length: int) -> None:
        if self._state != "package" or self._package_position is not None or self._blob is not None:
            wire.invalid()
        wire.integer(index, 0, 1023)
        wire.integer(length, 0, self.limits.maximum_total_bytes)
        position = self._next_package
        expected_kind = "root" if position == 0 else "page"
        expected_index = 0 if position == 0 else position - 1
        expected_length = (
            self.scope.root_length if position == 0 else self.scope.page_lengths[position - 1]
        )
        if (
            type(kind) is not str
            or kind != expected_kind
            or index != expected_index
            or length != expected_length
        ):
            wire.invalid()
        self._budget(added_bytes=length)
        valid = length <= (protocol.ROOT_MAX if kind == "root" else protocol.BRIDGE_PAGE_MAX)
        db = self._connection()
        db.execute("BEGIN")
        db.execute(
            "INSERT INTO package_deliveries(position,kind,byte_length,wire_valid) VALUES(?,?,?,?)",
            (position, kind, length, int(valid)),
        )
        if valid:
            db.execute(
                "UPDATE package_deliveries SET raw_body=zeroblob(?) WHERE position=?",
                (length, position),
            )
            if length:
                self._blob = db.blobopen("package_deliveries", "raw_body", position)
        self._package_position = position
        self._package_length, self._package_offset = length, 0
        self._package_digest = hashlib.sha256()

    @_operation
    def package_chunk(self, body: bytes) -> None:
        if (
            self._package_position is None
            or type(body) is not bytes
            or not 0 < len(body) <= wire.CHUNK_MAX
            or self._package_offset + len(body) > self._package_length
        ):
            wire.invalid()
        if self._blob is not None:
            self._blob.write(body)
        self._package_digest.update(body)
        self._package_offset += len(body)

    @_operation
    def package_end(self) -> None:
        if self._package_position is None or self._package_offset != self._package_length:
            wire.invalid()
        if self._blob is not None:
            self._blob.close()
            self._blob = None
        db = self._connection()
        db.execute(
            "UPDATE package_deliveries SET raw_digest=? WHERE position=?",
            (self._package_digest.digest(), self._package_position),
        )
        db.execute("COMMIT")
        self._package_bytes += self._package_length
        self._package_position = None
        self._next_package += 1
        if self._next_package > len(self.scope.page_lengths):
            self._state = "collecting"

    @_operation
    def seal(self, expected_observations: int) -> wire._SpoolSummary:
        self._require_collecting()
        wire.integer(expected_observations, 0, self.limits.maximum_observations)
        summary = super().finish()
        if (
            self._ordinal != expected_observations
            or sum(w.version_observations + w.delete_observations for w in summary.witnesses)
            != expected_observations
        ):
            wire.invalid()
        self._state = "sealed"
        # Reinstall to invalidate SQLite's cached statement authorizations, so a
        # previously prepared evidence UPDATE cannot outlive the write boundary.
        self._connection().set_authorizer(self._sealed_authorize)
        return summary

    def _observation_order(self, ordinal: int) -> tuple[bytes, bytes, bytes, bytes, int]:
        row = (
            self._connection()
            .execute(
                "SELECT w.uuid,o.key,o.version,o.digest,o.ordinal FROM observations o "
                "JOIN witnesses w ON w.id=o.witness WHERE o.ordinal=?",
                (ordinal,),
            )
            .fetchone()
        )
        if row is None:
            wire.invalid()
        return cast(tuple[bytes, bytes, bytes, bytes, int], row)

    def _link_identity(self, raw_id: int | None, body: bytes) -> None:
        ordinal = self._identity_ordinal
        if ordinal is None:
            wire.invalid()
        db = self._connection()
        db.execute("BEGIN")
        if raw_id is None:
            raw_id = db.execute(
                "INSERT INTO raw_bodies(digest,byte_length,representative_ordinal) VALUES(?,?,?)",
                (self._identity_digest, len(body), ordinal),
            ).lastrowid
            if raw_id is None:
                wire.invalid()
        else:
            representative = db.execute(
                "SELECT representative_ordinal FROM raw_bodies WHERE raw_id=?",
                (raw_id,),
            ).fetchone()[0]
            if self._observation_order(ordinal) < self._observation_order(representative):
                db.execute(
                    "UPDATE raw_bodies SET representative_ordinal=? WHERE raw_id=?",
                    (ordinal, raw_id),
                )
        db.execute("INSERT INTO body_deliveries(ordinal,raw_id) VALUES(?,?)", (ordinal, raw_id))
        db.execute("COMMIT")
        self._identity_after = ordinal
        self._identity_ordinal = None
        self._identity_candidate = 0

    @_operation
    def index_bodies_step(self) -> bool:
        if self.state != "sealed":
            wire.invalid()
        if self._identity_done:
            return True
        db = self._connection()
        work = 0
        body: bytes | None = None
        while work < 64:
            if body is None:
                if self._identity_ordinal is None:
                    row = db.execute(
                        "SELECT ordinal,body FROM observations "
                        "WHERE ordinal>? AND body IS NOT NULL "
                        "ORDER BY ordinal LIMIT 1",
                        (self._identity_after,),
                    ).fetchone()
                    if row is None:
                        self._identity_done = True
                        return True
                    self._identity_ordinal = row[0]
                    self._identity_digest = _raw_digest(row[1])
                    self._identity_candidate = 0
                    body = row[1]
                else:
                    body = db.execute(
                        "SELECT body FROM observations WHERE ordinal=?", (self._identity_ordinal,)
                    ).fetchone()[0]
                if type(body) is not bytes:
                    wire.invalid()
                work += 1
                self._identity_work += 1
                if work == 64:
                    break
            candidate = db.execute(
                "SELECT r.raw_id,o.body FROM raw_bodies r "
                "JOIN observations o ON o.ordinal=r.representative_ordinal "
                "WHERE r.digest=? AND r.byte_length=? AND r.raw_id>? ORDER BY r.raw_id LIMIT 1",
                (self._identity_digest, len(body), self._identity_candidate),
            ).fetchone()
            if candidate is None:
                self._link_identity(None, body)
                body = None
            else:
                work += 1
                self._identity_work += 1
                self._identity_candidate = candidate[0]
                if candidate[1] == body:
                    self._link_identity(candidate[0], body)
                    body = None
        return False
