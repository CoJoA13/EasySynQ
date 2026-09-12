"""Worker-owned complete mixed-history phases, with scalar indexed cursors."""

from __future__ import annotations

import sqlite3
from typing import Any
from uuid import UUID

from . import _history_reconciliation_protocol as protocol
from . import _history_spool_protocol as wire
from ._history_reconciliation_bridge import _BridgeKernel
from ._history_reconciliation_issues import _IssueIndex, _IssueRef
from ._history_reconciliation_lineage import _LineageKernel
from ._history_reconciliation_store import _operation, _ReconciliationStore


@_operation
def _step(store: _ReconciliationStore, engine: _ReconciliationEngine) -> protocol._Progress:
    if store.state != "sealed" or engine._phase >= len(protocol.PHASES) - 1:
        wire.invalid()
    phase = protocol.PHASES[engine._phase]
    before = engine._work + engine.bridge.work + engine.lineage.work + store._identity_work
    if phase in protocol.PHASES[:6] or phase in protocol.PHASES[7:11]:
        done = engine.bridge.step(phase)
    elif phase == "body-index":
        done = store.index_bodies_step()
    elif phase in protocol.PHASES[11:16]:
        done = engine.lineage.step(phase)
    elif phase == "witness-coverage":
        done = engine._coverage_step()
    elif phase == "global-heads":
        done = engine._heads_step()
    else:
        done = engine._issues_step()
    after = engine._work + engine.bridge.work + engine.lineage.work + store._identity_work
    if not 0 <= after - before <= (512 if phase == "package-pages" else 64):
        wire.invalid()
    if done:
        engine._phase += 1
    return protocol._Progress(
        protocol.PHASES[engine._phase], after, engine._phase == len(protocol.PHASES) - 1
    )


@_operation
def _finish(store: _ReconciliationStore, engine: _ReconciliationEngine) -> protocol._RawResult:
    if store.state != "sealed" or engine._phase != len(protocol.PHASES) - 1 or engine._finished:
        wire.invalid()
    from ._history_reconciliation_report import _build_report

    result = _build_report(store, engine.bridge, engine.lineage, engine.issues)
    engine._finished = True
    return result


class _ReconciliationEngine:
    def __init__(self, store: _ReconciliationStore, scope: protocol._PublicScope) -> None:
        if store.state != "sealed" or scope is not store.scope:
            wire.invalid()
        self.store, self.scope = store, scope
        self.issues = _IssueIndex(store)
        self.bridge = _BridgeKernel(store, scope.enrollment, self.issues)
        self.lineage = _LineageKernel(store, scope.enrollment.stream, self.issues)
        self._phase = self._work = 0
        self._finished = False
        self._coverage_witness = self._coverage_sequence = 0
        self._legacy_cursor: tuple[int, str, bytes] = (0, "", b"")
        self._v2_cursor: tuple[int, str, str] = (0, "", "")
        self._legacy_next: Any = None
        self._v2_next: Any = None
        self._legacy_done = self._v2_done = False
        self._head_id = 0
        self._head_v2 = False
        self._head_refs: list[_IssueRef] = []
        self._issue_stage = "collection"
        self._issue_cursor = (-1, "")
        self._raw_after = 0

    @property
    def _db(self) -> sqlite3.Connection:
        return self.store._connection()

    def step(self) -> protocol._Progress:
        return _step(self.store, self)

    def finish(self) -> protocol._RawResult:
        return _finish(self.store, self)

    def _ref(self, ordinal: int, value: bytes) -> _IssueRef:
        row = self._db.execute(
            "SELECT w.uuid,o.key,o.version,o.digest FROM observations o JOIN witnesses w "
            "ON w.id=o.witness WHERE o.ordinal=?",
            (ordinal,),
        ).fetchone()
        return _IssueRef(
            "observation",
            ordinal,
            (
                str(UUID(bytes=row[0])),
                row[1].decode(),
                row[2].decode(),
                "" if row[3] is None else row[3].hex(),
            ),
            value,
        )

    def _coverage_step(self) -> bool:
        if not self.lineage.consistent or self._coverage_witness == len(self.scope.witnesses):
            return True
        witness = self.scope.witnesses[self._coverage_witness].witness_id
        rows = self._db.execute(
            "SELECT sequence,envelope_hash FROM path_nodes WHERE sequence>? ORDER BY sequence "
            "LIMIT 64",
            (self._coverage_sequence,),
        ).fetchall()
        for sequence, anchor_hash in rows:
            self._coverage_sequence = sequence
            if (
                self._db.execute(
                    "SELECT 1 FROM v2_deliveries WHERE witness=? AND envelope_hash=?",
                    (witness.bytes, anchor_hash),
                ).fetchone()
                is None
            ):
                self.issues.add(
                    "composition",
                    "incomplete",
                    "V2_WITNESS_COVERAGE_MISSING",
                    (str(witness), anchor_hash),
                    (),
                )
        self._work += len(rows)
        if not rows:
            self._coverage_witness += 1
            self._coverage_sequence = 0
        return False

    def _finish_head(self) -> None:
        if self._head_v2 and len(self._head_refs) == 2:
            self.issues.add(
                "composition",
                "failed",
                "GLOBAL_SIGNED_HEAD_CONFLICT",
                (f"{self._head_id:019d}",),
                tuple(self._head_refs),
            )

    def _heads_step(self) -> bool:
        # Merge two dependency indexes with one lookahead row each. No same-ID
        # group or all-history head inventory is ever loaded into Python.
        for _ in range(64):
            if self._legacy_next is None and not self._legacy_done:
                self._legacy_next = self._db.execute(
                    "SELECT audit_id,row_hash,witness,representative_ordinal FROM "
                    "legacy_witness_heads WHERE (audit_id,row_hash,witness)>(?,?,?) "
                    "ORDER BY audit_id,row_hash,witness LIMIT 1",
                    self._legacy_cursor,
                ).fetchone()
                self._legacy_done = self._legacy_next is None
            if self._v2_next is None and not self._v2_done:
                self._v2_next = self._db.execute(
                    "SELECT n.audit_id,n.row_hash,n.envelope_hash,r.representative_ordinal "
                    "FROM v2_nodes n JOIN raw_bodies r ON r.raw_id=n.representative_raw_id "
                    "WHERE (n.audit_id,n.row_hash,n.envelope_hash)>(?,?,?) ORDER BY "
                    "n.audit_id,n.row_hash,n.envelope_hash LIMIT 1",
                    self._v2_cursor,
                ).fetchone()
                self._v2_done = self._v2_next is None
            if self._legacy_done and self._v2_done:
                self._finish_head()
                return True
            is_v2 = self._legacy_done or (
                not self._v2_done and self._v2_next[:2] < self._legacy_next[:2]
            )
            row = self._v2_next if is_v2 else self._legacy_next
            if row[0] != self._head_id:
                self._finish_head()
                self._head_id, self._head_v2, self._head_refs = row[0], False, []
            self._head_v2 |= is_v2
            ref = self._ref(row[3], row[1].encode())
            for index, old in enumerate(self._head_refs):
                if old.value == ref.value:
                    if (ref.order, ref.index) < (old.order, old.index):
                        self._head_refs[index] = ref
                    break
            else:
                self._head_refs.append(ref)
            self._head_refs.sort(key=lambda item: (item.order, item.index))
            del self._head_refs[2:]
            if is_v2:
                self._v2_cursor, self._v2_next = row[:3], None
            else:
                self._legacy_cursor, self._legacy_next = row[:3], None
            self._work += 1
        return False

    def _issues_step(self) -> bool:
        if self._issue_stage == "collection":
            rows = self._db.execute(
                "SELECT witness,code,count,r1,r2,r3,r4 FROM issues WHERE (witness,code)>(?,?) "
                "ORDER BY witness,code LIMIT 64",
                self._issue_cursor,
            ).fetchall()
            for witness, code, count, *ordinals in rows:
                scope = self.scope.witnesses[witness]
                refs = tuple(self._ref(o, o.to_bytes(8, "big")) for o in ordinals if o is not None)
                self.issues.add(
                    "collection",
                    wire.ISSUES[wire.issue_code(code)],
                    code,
                    (str(scope.witness_id),),
                    refs,
                    count=count,
                )
                if code in {"LIST_UNAVAILABLE", "CURSOR_CYCLE"}:
                    self.bridge.collection_gap(scope.witness_id, code)
                self._issue_cursor = witness, code
            self._work += len(rows)
            if not rows:
                self._issue_stage = "shape"
            return False
        rows = self._db.execute(
            "SELECT raw_id,representative_ordinal,format FROM raw_bodies WHERE raw_id>? "
            "ORDER BY raw_id LIMIT 64",
            (self._raw_after,),
        ).fetchall()
        for raw_id, ordinal, body_format in rows:
            self._raw_after = raw_id
            if body_format == "invalid":
                ref = self._ref(ordinal, raw_id.to_bytes(8, "big"))
                self.issues.add(
                    "composition", "failed", "CHECKPOINT_BODY_INVALID", (ref.order[3],), (ref,)
                )
            elif body_format not in {"legacy", "v2"}:
                wire.invalid()
        self._work += len(rows)
        return not rows
