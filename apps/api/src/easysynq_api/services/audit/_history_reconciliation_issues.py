"""Complete indexed groups and bounded stable references; display is a projection."""

from __future__ import annotations

import dataclasses
import re
from typing import Literal, cast
from uuid import UUID

from . import _history_spool_protocol as wire
from ._history_reconciliation_store import _operation, _ReconciliationStore
from .history_collection import HistoryCollectionError
from .history_reconciliation import HistoryReconciliationIssue, HistoryReference

_BRIDGE_PAIRS = frozenset(
    {
        "PAGE_CONFLICT",
        "MANIFEST_PARTITION_INVALID",
        "MANIFEST_LOCATOR_DUPLICATE",
        "MANIFEST_ORDER_INVALID",
        "MANIFEST_COUNT_MISMATCH",
        "IMMUTABLE_LOCATOR_CONFLICT",
        "SIGNED_HEAD_CONFLICT",
        "WITNESS_SUMMARY_MISMATCH",
    }
)
_LINEAGE_PAIRS = frozenset({"IMMUTABLE_LOCATOR_CONFLICT", "ANCHOR_ID_CONFLICT", "LINEAGE_FORK"})
_COMPONENTS = frozenset({"collection", "bridge", "lineage", "composition"})


@dataclasses.dataclass(frozen=True, slots=True)
class _IssueRef:
    """A stable side identity (such as a raw ID or commitment), never a body export."""

    kind: Literal["observation", "page"]
    index: int
    order: tuple[str, str, str, str]
    value: bytes


def _text(value: object, maximum: int = 4096) -> None:
    if type(value) is not str:
        wire.invalid()
    try:
        if len(value.encode("utf-8")) > maximum:
            wire.invalid()
    except UnicodeError:
        wire.invalid()


def _ready(store: _ReconciliationStore) -> None:
    if store.state != "sealed":
        wire.invalid()


def _maximum_refs(component: str, code: str) -> int:
    if (
        component == "collection"
        or (component == "bridge" and code in _BRIDGE_PAIRS)
        or (component == "lineage" and code in _LINEAGE_PAIRS)
        or (component == "composition" and code == "GLOBAL_SIGNED_HEAD_CONFLICT")
    ):
        return 2
    return 1


def _refs(store: _ReconciliationStore, key: tuple[str, ...]) -> list[_IssueRef]:
    rows = store._connection().execute(
        "SELECT kind,reference_index,o0,o1,o2,o3,value FROM issue_refs "
        "WHERE component=? AND severity=? AND code=? AND s0=? AND s1=? AND s2=? AND s3=? "
        "ORDER BY slot LIMIT 2",
        key,
    )
    return [_IssueRef(row[0], row[1], (row[2], row[3], row[4], row[5]), row[6]) for row in rows]


@_operation
def _add(
    store: _ReconciliationStore,
    component: str,
    severity: str,
    code: str,
    subject: tuple[str, ...],
    refs: tuple[_IssueRef, ...],
    count: int,
) -> None:
    _ready(store)
    if (
        type(component) is not str
        or component not in _COMPONENTS
        or type(severity) is not str
        or severity not in {"failed", "incomplete"}
    ):
        wire.invalid()
    if type(code) is not str or re.fullmatch("[A-Z][A-Z0-9_]{0,63}", code) is None:
        wire.invalid()
    if type(subject) is not tuple or len(subject) > 4 or type(refs) is not tuple or len(refs) > 64:
        wire.invalid()
    wire.integer(count, 1, 100_000)
    for part in subject:
        _text(part)
    for ref in refs:
        if (
            type(ref) is not _IssueRef
            or type(ref.kind) is not str
            or ref.kind not in {"observation", "page"}
            or type(ref.order) is not tuple
            or len(ref.order) != 4
            or type(ref.value) is not bytes
            or len(ref.value) > 65_536
        ):
            wire.invalid()
        wire.integer(
            ref.index,
            1 if ref.kind == "observation" else 0,
            store._ordinal if ref.kind == "observation" else len(store.scope.page_lengths) - 1,
        )
        for part in ref.order:
            _text(part)
    key = (component, severity, code, *subject, *("",) * (4 - len(subject)))
    db = store._connection()
    previous = db.execute(
        "SELECT count FROM reconciliation_issues WHERE component=? AND severity=? AND code=? "
        "AND s0=? AND s1=? AND s2=? AND s3=?",
        key,
    ).fetchone()
    if previous is None and store._issue_group_count >= store.limits.maximum_issue_groups:
        raise HistoryCollectionError("RESOURCE_LIMIT")
    retained = _refs(store, key)
    maximum = _maximum_refs(component, code)
    for ref in refs:
        for position, old in enumerate(retained):
            if (old.kind, old.value) == (ref.kind, ref.value):
                if (ref.order, ref.index) < (old.order, old.index):
                    retained[position] = ref
                break
        else:
            retained.append(ref)
        retained.sort(key=lambda item: (item.order, item.index))
        del retained[maximum:]
    witness = next(
        (
            w.witness_id
            for w in store.scope.witnesses
            if subject and str(w.witness_id) == subject[0]
        ),
        None,
    )
    db.execute("BEGIN")
    if previous is None:
        db.execute(
            "INSERT INTO reconciliation_issues VALUES(?,?,?,?,?,?,?,?,?)",
            (*key, None if witness is None else witness.bytes, count),
        )
    elif count > previous[0]:
        db.execute(
            "UPDATE reconciliation_issues SET count=? WHERE component=? AND severity=? AND code=? "
            "AND s0=? AND s1=? AND s2=? AND s3=?",
            (count, *key),
        )
    db.execute(
        "DELETE FROM issue_refs WHERE component=? AND severity=? AND code=? "
        "AND s0=? AND s1=? AND s2=? AND s3=?",
        key,
    )
    for slot, ref in enumerate(retained):
        db.execute(
            "INSERT INTO issue_refs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (*key, slot, ref.kind, ref.index, *ref.order, ref.value),
        )
    db.execute("COMMIT")
    if previous is None:
        store._issue_group_count += 1


@_operation
def _counts(store: _ReconciliationStore) -> tuple[int, int]:
    _ready(store)
    row = (
        store._connection()
        .execute(
            "SELECT coalesce(sum(severity='failed'),0),coalesce(sum(severity='incomplete'),0) "
            "FROM reconciliation_issues"
        )
        .fetchone()
    )
    return cast(tuple[int, int], row)


@_operation
def _display(store: _ReconciliationStore, limit: int) -> tuple[HistoryReconciliationIssue, ...]:
    _ready(store)
    wire.integer(limit, 1, store.limits.maximum_issues)
    rows = store._connection().execute(
        "SELECT component,severity,code,s0,s1,s2,s3,witness_id,count FROM reconciliation_issues "
        "ORDER BY severity,component,code,s0,s1,s2,s3 LIMIT ?",
        (limit,),
    )
    result = []
    for row in rows:
        refs = _refs(store, row[:7])
        result.append(
            HistoryReconciliationIssue(
                row[0],
                row[2],
                row[1],
                None if row[7] is None else UUID(bytes=row[7]),
                row[8],
                tuple(HistoryReference(ref.kind, ref.index) for ref in refs),
            )
        )
    return tuple(result)


class _IssueIndex:
    def __init__(self, store: _ReconciliationStore) -> None:
        self._store = store

    def add(
        self,
        component: str,
        severity: str,
        code: str,
        subject: tuple[str, ...],
        refs: tuple[_IssueRef, ...],
        *,
        count: int = 1,
    ) -> None:
        """Offer stable references; count is an authoritative group count, not an increment.

        Repeated bridge/lineage offers retain their single group. Collection
        import supplies its complete R84 count after evidence is sealed.
        """
        _add(self._store, component, severity, code, subject, refs, count)

    def counts(self) -> tuple[int, int]:
        return _counts(self._store)

    def display(self, limit: int) -> tuple[HistoryReconciliationIssue, ...]:
        return _display(self._store, limit)
