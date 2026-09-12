"""Indexed v2 material and edge closure; authenticated routes confer no authority."""

from __future__ import annotations

import dataclasses
import sqlite3
from typing import Literal
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from . import _history_spool_protocol as wire
from . import checkpoint_v2 as codec
from . import lineage
from ._history_reconciliation_issues import _IssueIndex, _IssueRef
from ._history_reconciliation_store import _operation, _ReconciliationStore
from .history_reconciliation import HistoryReconciliationError, HistoryReconciliationTip
from .lineage import AuditHead, StreamEnrollment

_PHASES = ("v2-route", "v2-events", "v2-diagnostics", "v2-required", "v2-path")
type _Relation = Literal["not-provided", "unassessed", "included", "missing", "conflicting"]


@dataclasses.dataclass(frozen=True, slots=True)
class _Node:
    anchor_hash: str
    anchor_id: str
    previous_anchor_hash: str
    sequence: int
    key_id: str
    key_epoch: int
    latest_id: int
    latest_row_hash: str
    next_key_id: str | None
    next_public_key: bytes | None
    representative_raw_id: int
    edge_state: str


def _edge_fault(
    child: _Node,
    *,
    expected_key_id: str,
    expected_epoch: int,
    expected_sequence: int,
    previous_head: AuditHead | None,
) -> str | None:
    if (child.key_id, child.key_epoch) != (expected_key_id, expected_epoch):
        return "KEY_EPOCH_VIOLATION"
    if child.sequence != expected_sequence:
        return "SEQUENCE_DISCONTINUITY"
    if previous_head is not None:
        if child.latest_id < previous_head.latest_id:
            return "AUDIT_HEAD_REGRESSION"
        if (
            child.latest_id == previous_head.latest_id
            and child.latest_row_hash != previous_head.latest_row_hash
        ):
            return "AUDIT_HEAD_CONFLICT"
    return None


@_operation
def _step(store: _ReconciliationStore, kernel: _LineageKernel, phase: str) -> bool:
    if store.state != "sealed" or kernel._phase >= len(_PHASES) or phase != _PHASES[kernel._phase]:
        wire.invalid()
    methods = (
        kernel._route_step,
        kernel._events_step,
        kernel._diagnostics_step,
        kernel._required_step,
        kernel._path_step,
    )
    done = methods[kernel._phase]()
    if done:
        kernel._phase += 1
    return done


class _LineageKernel:
    def __init__(
        self, store: _ReconciliationStore, enrollment: StreamEnrollment, issues: _IssueIndex
    ) -> None:
        self._store, self._enrollment, self._issues = store, enrollment, issues
        try:
            key = Ed25519PublicKey.from_public_bytes(enrollment.bootstrap.initial_public_key)
            key_id = codec.public_key_id(key)
        except ValueError:
            lineage._input_error()
        if key_id != enrollment.bootstrap.initial_key_id:
            lineage._input_error()
        self._phase = 0
        self._route_stage = "locators"
        self._locator_cursor = (-1, b"", b"", 0)
        self._locator_first: tuple[tuple[int, bytes, bytes], int, _IssueRef] | None = None
        self._raw_after = 0
        self._raw_count = 0
        self._node_count = 0
        self._admitted_count = 0
        self._material_count = 0
        self._diagnostic_stage = "unknown"
        self._diagnostic_raw = 0
        self._diagnostic_hash = ""
        self._conflict_cursor = ("", "")
        self._conflict_first: tuple[str, _IssueRef] | None = None
        self._delivery_after = 0
        self._authenticated_deliveries = 0
        self._required_after = ""
        self._required_found = False
        self._required_ref: _IssueRef | None = None
        self._relation: _Relation = (
            "not-provided" if enrollment.required_checkpoint is None else "unassessed"
        )
        self._path_previous = enrollment.bootstrap.commitment_hash
        self._path_length = 0
        self._used_epochs = 0
        self._previous_epoch: int | None = None
        self._path_done = False
        self._tip: HistoryReconciliationTip | None = None
        self.work = 0

    @property
    def _db(self) -> sqlite3.Connection:
        return self._store._connection()

    @property
    def consistent(self) -> bool:
        return self._phase == len(_PHASES) and self._path_done and not self._has_issues()

    @property
    def required_checkpoint_relation(self) -> _Relation:
        return self._relation

    @property
    def duplicate_observations(self) -> int:
        return self._authenticated_deliveries - self._node_count

    @property
    def path_length(self) -> int:
        return self._path_length if self.consistent else 0

    @property
    def used_epoch_count(self) -> int:
        return self._used_epochs if self.consistent else 0

    def tip(self) -> HistoryReconciliationTip | None:
        return self._tip if self.consistent else None

    def step(self, phase: str) -> bool:
        return _step(self._store, self, phase)

    def _has_issues(self) -> bool:
        return (
            self._db.execute(
                "SELECT 1 FROM reconciliation_issues WHERE component='lineage' LIMIT 1"
            ).fetchone()
            is not None
        )

    def _issue(
        self,
        code: str,
        severity: str,
        subject: tuple[str, ...] = (),
        refs: tuple[_IssueRef, ...] = (),
    ) -> None:
        self._issues.add("lineage", severity, code, subject, refs)

    def _observation_ref(self, ordinal: int, value: bytes) -> _IssueRef:
        row = self._db.execute(
            "SELECT w.uuid,o.key,o.version,o.digest FROM observations o JOIN witnesses w ON "
            "w.id=o.witness WHERE o.ordinal=?",
            (ordinal,),
        ).fetchone()
        return _IssueRef(
            "observation",
            ordinal,
            (str(UUID(bytes=row[0])), row[1].decode(), row[2].decode(), row[3].hex()),
            value,
        )

    def _raw_ref(self, raw_id: int, value: bytes | None = None) -> _IssueRef:
        ordinal = self._db.execute(
            "SELECT representative_ordinal FROM raw_bodies WHERE raw_id=?", (raw_id,)
        ).fetchone()[0]
        return self._observation_ref(ordinal, raw_id.to_bytes(8, "big") if value is None else value)

    def _node_ref(self, node: _Node) -> _IssueRef:
        return self._raw_ref(node.representative_raw_id, node.anchor_hash.encode())

    def _locator_step(self) -> bool:
        remaining = 64
        while remaining:
            # SQLite can seek only the three-column prefix of a row-value
            # comparison ending in the INTEGER PRIMARY KEY alias. Seek that
            # locator separately, then seek its ordinal with fixed equality.
            rows = self._db.execute(
                "SELECT o.ordinal,d.raw_id,r.format FROM observations o "
                "LEFT JOIN body_deliveries d ON d.ordinal=o.ordinal LEFT JOIN raw_bodies r "
                "ON r.raw_id=d.raw_id WHERE o.witness=? AND o.key=? AND o.version=? "
                "AND o.ordinal>? ORDER BY o.ordinal LIMIT ?",
                (*self._locator_cursor, remaining),
            ).fetchall()
            locator = self._locator_cursor[:3]
            for ordinal, raw_id, body_format in rows:
                self._locator_cursor = (*locator, ordinal)
                if body_format != "v2":
                    continue
                ref = self._observation_ref(ordinal, raw_id.to_bytes(8, "big"))
                if self._locator_first is not None and locator == self._locator_first[0]:
                    if raw_id != self._locator_first[1]:
                        self._issue(
                            "IMMUTABLE_LOCATOR_CONFLICT",
                            "failed",
                            ref.order[:3],
                            (self._locator_first[2], ref),
                        )
                else:
                    self._locator_first = locator, raw_id, ref
            self.work += len(rows)
            remaining -= len(rows)
            if not remaining:
                return False
            next_locator = self._db.execute(
                "SELECT witness,key,version FROM observations WHERE (witness,key,version) "
                ">(?,?,?) ORDER BY witness,key,version LIMIT 1",
                locator,
            ).fetchone()
            if next_locator is None:
                self._route_stage = "raws"
                return False
            self._locator_cursor = (*next_locator, 0)
            self._locator_first = None
        return False

    def _route_step(self) -> bool:
        if not self._store._identity_done:
            wire.invalid()
        if self._route_stage == "locators":
            return self._locator_step()
        rows = self._db.execute(
            "SELECT r.raw_id,o.body FROM raw_bodies r JOIN observations o ON "
            "o.ordinal=r.representative_ordinal WHERE r.raw_id>? AND r.format='v2' ORDER BY "
            "r.raw_id LIMIT 64",
            (self._raw_after,),
        ).fetchall()
        for raw_id, raw in rows:
            self._raw_after = raw_id
            self._raw_count += 1
            ref = self._raw_ref(raw_id)
            key_id = None
            try:
                route = codec.inspect_envelope_route(raw)
            except codec.CheckpointV2Error:
                state = "invalid"
                self._issue("ENVELOPE_INVALID", "failed", (ref.order[3],), (ref,))
            else:
                if (route.org_id, route.stream_id) != (
                    self._enrollment.org_id,
                    self._enrollment.stream_id,
                ):
                    state = "identity"
                    self._issue("IDENTITY_MISMATCH", "failed", (ref.order[3],), (ref,))
                else:
                    key_id, state = route.key_id, "pending"
            self._db.execute("INSERT INTO v2_routes VALUES(?,?,?)", (raw_id, key_id, state))
        self.work += len(rows)
        if rows:
            return False
        if not self._raw_count:
            self._issue("EMPTY_GRAPH", "incomplete")
        pin = self._enrollment.bootstrap
        self._material(pin.initial_key_id, pin.initial_public_key)
        self._db.execute(
            "INSERT INTO parent_events(predecessor_hash) VALUES(?)", (pin.commitment_hash,)
        )
        return True

    def _material(self, key_id: str, public_key: bytes) -> None:
        if self._db.execute("SELECT 1 FROM materials WHERE key_id=?", (key_id,)).fetchone():
            return
        if self._material_count >= self._store.limits.maximum_observations + 1:
            raise HistoryReconciliationError("RESOURCE_LIMIT")
        self._db.execute("INSERT INTO materials VALUES(?,?)", (key_id, public_key))
        self._db.execute("INSERT INTO material_events(key_id) VALUES(?)", (key_id,))
        self._material_count += 1

    def _wake_parent(self, predecessor: str) -> None:
        if (
            predecessor == self._enrollment.bootstrap.commitment_hash
            or self._db.execute(
                "SELECT 1 FROM admitted_nodes WHERE envelope_hash=?", (predecessor,)
            ).fetchone()
        ):
            # A later key event can authenticate a child below the old cursor.
            # Reset only this dependency; assessed edges have left its waiting index.
            self._db.execute(
                "UPDATE parent_events SET done=0,last_envelope_hash='' WHERE predecessor_hash=?",
                (predecessor,),
            )

    def _authenticate_batch(self, event_id: int, key_id: str, after: int, budget: int) -> None:
        rows = self._db.execute(
            "SELECT r.raw_id,o.body FROM v2_routes r JOIN raw_bodies b ON b.raw_id=r.raw_id "
            "JOIN observations o ON o.ordinal=b.representative_ordinal WHERE r.key_id=? AND "
            "r.state='pending' AND r.raw_id>? ORDER BY r.raw_id LIMIT ?",
            (key_id, after, budget),
        ).fetchall()
        if not rows:
            self._db.execute("UPDATE material_events SET done=1 WHERE event_id=?", (event_id,))
            self.work += 1
            return
        material = self._db.execute(
            "SELECT public_key FROM materials WHERE key_id=?", (key_id,)
        ).fetchone()[0]
        key = Ed25519PublicKey.from_public_bytes(material)
        for raw_id, raw in rows:
            ref = self._raw_ref(raw_id)
            try:
                envelope = codec.verify_envelope(
                    raw,
                    public_key=key,
                    org_id=self._enrollment.org_id,
                    stream_id=self._enrollment.stream_id,
                )
            except codec.CheckpointV2Error:
                self._db.execute("UPDATE v2_routes SET state='invalid' WHERE raw_id=?", (raw_id,))
                self._issue("ENVELOPE_INVALID", "failed", (ref.order[3],), (ref,))
            else:
                self._db.execute(
                    "UPDATE v2_routes SET state='authenticated' WHERE raw_id=?", (raw_id,)
                )
                self._db.execute(
                    "INSERT INTO v2_raw_nodes VALUES(?,?)", (raw_id, envelope.anchor_hash)
                )
                existing = self._db.execute(
                    "SELECT representative_raw_id FROM v2_nodes WHERE envelope_hash=?",
                    (envelope.anchor_hash,),
                ).fetchone()
                if existing is not None:
                    old = self._raw_ref(existing[0])
                    if (ref.order, ref.index) < (old.order, old.index):
                        self._db.execute(
                            "UPDATE v2_nodes SET representative_raw_id=? WHERE envelope_hash=?",
                            (raw_id, envelope.anchor_hash),
                        )
                else:
                    if self._node_count >= self._store.limits.maximum_observations:
                        raise HistoryReconciliationError("RESOURCE_LIMIT")
                    self._db.execute(
                        "INSERT INTO v2_nodes VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            envelope.anchor_hash,
                            str(envelope.anchor_id),
                            envelope.previous_anchor_hash,
                            envelope.sequence,
                            envelope.key_id,
                            envelope.key_epoch,
                            envelope.latest_id,
                            envelope.latest_row_hash,
                            envelope.next_key_id,
                            envelope.next_public_key,
                            raw_id,
                            "waiting",
                        ),
                    )
                    self._node_count += 1
                    self._wake_parent(envelope.previous_anchor_hash)
            self._db.execute(
                "UPDATE material_events SET last_raw_id=? WHERE event_id=?", (raw_id, event_id)
            )
            self.work += 1

    def _assess_batch(self, event_id: int, predecessor: str, after: str, budget: int) -> None:
        rows = self._db.execute(
            "SELECT * FROM v2_nodes WHERE predecessor_hash=? AND edge_state='waiting' AND "
            "envelope_hash>? ORDER BY envelope_hash LIMIT ?",
            (predecessor, after, budget),
        ).fetchall()
        if not rows:
            self._db.execute("UPDATE parent_events SET done=1 WHERE event_id=?", (event_id,))
            self.work += 1
            return
        pin = self._enrollment.bootstrap
        if predecessor == pin.commitment_hash:
            key_id, epoch, sequence, head = (
                pin.initial_key_id,
                pin.initial_key_epoch,
                1,
                pin.audit_boundary,
            )
        else:
            parent = self._db.execute(
                "SELECT "
                "a.permitted_next_key_id,a.permitted_next_epoch,n.sequence,n.audit_id,"
                "n.row_hash FROM admitted_nodes a JOIN v2_nodes n ON "
                "n.envelope_hash=a.envelope_hash WHERE a.envelope_hash=?",
                (predecessor,),
            ).fetchone()
            if parent is None:
                wire.invalid()
            key_id, epoch, sequence, head = (
                parent[0],
                parent[1],
                parent[2] + 1,
                AuditHead(parent[3], parent[4]),
            )
        for row in rows:
            child = _Node(*row)
            fault = _edge_fault(
                child,
                expected_key_id=key_id,
                expected_epoch=epoch,
                expected_sequence=sequence,
                previous_head=head,
            )
            if fault is None:
                self._db.execute(
                    "INSERT INTO admitted_nodes VALUES(?,?,?,?)",
                    (
                        child.anchor_hash,
                        predecessor,
                        child.next_key_id or child.key_id,
                        child.key_epoch + 1 if child.next_key_id is not None else child.key_epoch,
                    ),
                )
                self._admitted_count += 1
                self._db.execute(
                    "INSERT INTO parent_events(predecessor_hash) VALUES(?)", (child.anchor_hash,)
                )
                if child.next_key_id is not None:
                    if child.next_public_key is None:
                        wire.invalid()
                    self._material(child.next_key_id, child.next_public_key)
            self._db.execute(
                "UPDATE v2_nodes SET edge_state=? WHERE envelope_hash=?",
                ("admitted" if fault is None else fault, child.anchor_hash),
            )
            self._db.execute(
                "UPDATE parent_events SET last_envelope_hash=? WHERE event_id=?",
                (child.anchor_hash, event_id),
            )
            self.work += 1

    def _events_step(self) -> bool:
        start = self.work
        while self.work - start < 64:
            event = self._db.execute(
                "SELECT event_id,key_id,last_raw_id FROM material_events WHERE done=0 ORDER BY "
                "event_id LIMIT 1"
            ).fetchone()
            if event is not None:
                self._authenticate_batch(event[0], event[1], event[2], 64 - (self.work - start))
                continue
            parent = self._db.execute(
                "SELECT event_id,predecessor_hash,last_envelope_hash FROM parent_events WHERE "
                "done=0 ORDER BY event_id LIMIT 1"
            ).fetchone()
            if parent is None:
                return True
            self._assess_batch(parent[0], parent[1], parent[2], 64 - (self.work - start))
        return False

    def _diagnostics_step(self) -> bool:
        if self._diagnostic_stage == "unknown":
            rows = self._db.execute(
                "SELECT raw_id FROM v2_routes WHERE raw_id>? ORDER BY raw_id LIMIT 64",
                (self._diagnostic_raw,),
            ).fetchall()
            for (raw_id,) in rows:
                self._diagnostic_raw = raw_id
                state = self._db.execute(
                    "SELECT state FROM v2_routes WHERE raw_id=?", (raw_id,)
                ).fetchone()[0]
                if state == "pending":
                    ref = self._raw_ref(raw_id)
                    self._issue("UNKNOWN_KEY", "incomplete", (ref.order[3],), (ref,))
            self.work += len(rows)
            if not rows:
                self._diagnostic_stage = "nodes"
            return False
        if self._diagnostic_stage == "nodes":
            rows = self._db.execute(
                "SELECT * FROM v2_nodes WHERE envelope_hash>? ORDER BY envelope_hash LIMIT 64",
                (self._diagnostic_hash,),
            ).fetchall()
            for row in rows:
                node = _Node(*row)
                self._diagnostic_hash = node.anchor_hash
                if node.edge_state == "waiting":
                    self._issue(
                        "DISCONNECTED_GRAPH",
                        "incomplete",
                        (node.anchor_hash,),
                        (self._node_ref(node),),
                    )
                elif node.edge_state != "admitted":
                    self._issue(
                        node.edge_state, "failed", (node.anchor_hash,), (self._node_ref(node),)
                    )
            self.work += len(rows)
            if not rows:
                self._diagnostic_stage = "anchors"
            return False
        if self._diagnostic_stage in {"anchors", "forks"}:
            if self._diagnostic_stage == "anchors":
                rows = self._db.execute(
                    "SELECT anchor_id,envelope_hash,representative_raw_id FROM v2_nodes WHERE "
                    "(anchor_id,envelope_hash)>(?,?) ORDER BY anchor_id,envelope_hash LIMIT 64",
                    self._conflict_cursor,
                ).fetchall()
                code = "ANCHOR_ID_CONFLICT"
            else:
                rows = self._db.execute(
                    "SELECT a.predecessor_hash,a.envelope_hash,n.representative_raw_id FROM "
                    "admitted_nodes a JOIN v2_nodes n ON n.envelope_hash=a.envelope_hash WHERE "
                    "(a.predecessor_hash,a.envelope_hash)>(?,?) ORDER BY "
                    "a.predecessor_hash,a.envelope_hash LIMIT 64",
                    self._conflict_cursor,
                ).fetchall()
                code = "LINEAGE_FORK"
            for subject, anchor_hash, raw_id in rows:
                ref = self._raw_ref(raw_id, anchor_hash.encode())
                if self._conflict_first is not None and self._conflict_first[0] == subject:
                    self._issue(code, "failed", (subject,), (self._conflict_first[1], ref))
                else:
                    self._conflict_first = subject, ref
                self._conflict_cursor = subject, anchor_hash
            self.work += len(rows)
            if not rows:
                self._diagnostic_stage = (
                    "forks" if self._diagnostic_stage == "anchors" else "deliveries"
                )
                self._conflict_first, self._conflict_cursor = None, ("", "")
            return False
        rows = self._db.execute(
            "SELECT o.ordinal,w.uuid,v.envelope_hash FROM observations o JOIN witnesses w ON "
            "w.id=o.witness JOIN body_deliveries d ON d.ordinal=o.ordinal JOIN v2_raw_nodes v "
            "ON v.raw_id=d.raw_id WHERE o.ordinal>? ORDER BY o.ordinal LIMIT 64",
            (self._delivery_after,),
        ).fetchall()
        for ordinal, witness, anchor_hash in rows:
            self._delivery_after = ordinal
            self._authenticated_deliveries += 1
            old = self._db.execute(
                "SELECT representative_ordinal FROM v2_deliveries WHERE witness=? AND "
                "envelope_hash=?",
                (witness, anchor_hash),
            ).fetchone()
            if old is None:
                self._db.execute(
                    "INSERT INTO v2_deliveries VALUES(?,?,?)", (witness, anchor_hash, ordinal)
                )
            else:
                ref = self._observation_ref(ordinal, anchor_hash.encode())
                prior = self._observation_ref(old[0], anchor_hash.encode())
                if (ref.order, ref.index) < (prior.order, prior.index):
                    self._db.execute(
                        "UPDATE v2_deliveries SET representative_ordinal=? WHERE witness=? AND "
                        "envelope_hash=?",
                        (ordinal, witness, anchor_hash),
                    )
        self.work += len(rows)
        return not rows

    def _required_step(self) -> bool:
        pin = self._enrollment.required_checkpoint
        if pin is None:
            return True
        rows = self._db.execute(
            "SELECT n.* FROM admitted_nodes a JOIN v2_nodes n ON "
            "n.envelope_hash=a.envelope_hash WHERE a.envelope_hash>? ORDER BY a.envelope_hash "
            "LIMIT 64",
            (self._required_after,),
        ).fetchall()
        for row in rows:
            node = _Node(*row)
            self._required_after = node.anchor_hash
            same_hash, same_sequence = (
                node.anchor_hash == pin.anchor_hash,
                node.sequence == pin.sequence,
            )
            if same_hash and same_sequence:
                self._required_found = True
            elif same_hash or same_sequence:
                ref = self._node_ref(node)
                if self._required_ref is None or (ref.order, ref.index) < (
                    self._required_ref.order,
                    self._required_ref.index,
                ):
                    self._required_ref = ref
        self.work += len(rows)
        if rows:
            return False
        if self._required_ref is not None:
            self._relation = "conflicting"
            self._issue("REQUIRED_CHECKPOINT_CONFLICT", "failed", refs=(self._required_ref,))
        elif self._required_found:
            self._relation = "included"
        else:
            self._relation = "missing"
            self._issue("REQUIRED_CHECKPOINT_MISSING", "incomplete")
        return True

    def _path_step(self) -> bool:
        if self._has_issues():
            return True
        for _ in range(64):
            rows = self._db.execute(
                "SELECT n.* FROM admitted_nodes a JOIN v2_nodes n ON "
                "n.envelope_hash=a.envelope_hash WHERE a.predecessor_hash=? ORDER BY "
                "a.envelope_hash LIMIT 2",
                (self._path_previous,),
            ).fetchall()
            if not rows:
                if self._path_length != self._admitted_count or not self._path_length:
                    wire.invalid()
                self._path_done = True
                return True
            if len(rows) != 1 or self._path_length >= self._store.limits.maximum_observations:
                wire.invalid()
            node = _Node(*rows[0])
            self._db.execute(
                "INSERT INTO path_nodes VALUES(?,?,?,?)",
                (node.sequence, node.anchor_hash, node.key_id, node.key_epoch),
            )
            if self._previous_epoch != node.key_epoch:
                self._db.execute(
                    "INSERT INTO used_epochs VALUES(?,?,?)",
                    (
                        node.key_epoch,
                        node.key_id,
                        self._path_previous if self._path_length else None,
                    ),
                )
                self._used_epochs += 1
            self._previous_epoch = node.key_epoch
            self._path_previous = node.anchor_hash
            self._path_length += 1
            self._tip = HistoryReconciliationTip(
                node.anchor_hash,
                node.sequence,
                AuditHead(node.latest_id, node.latest_row_hash),
                node.key_id,
                node.key_epoch,
            )
            self.work += 1
        return False
