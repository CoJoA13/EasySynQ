"""Bounded SQLite passes for externally pinned legacy bootstrap reconciliation.

Original evidence is immutable. Each call visits at most 64 indexed records or
one bounded wire page. Cursors and the at-most-four witness reductions stay here;
neither the caller nor the supplied root can assert a completed manifest.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import sqlite3
from typing import Literal, cast
from uuid import UUID

from . import _history_spool_protocol as wire
from . import bootstrap_bridge as bridge
from . import bootstrap_bridge_codec as codec
from . import legacy_checkpoint_compat as legacy
from ._history_reconciliation_issues import _IssueIndex, _IssueRef
from ._history_reconciliation_store import _operation, _ReconciliationStore
from .history_reconciliation import HistoryReconciliationError
from .lineage import AuditHead

_BODY_DOMAIN = b"EasySynQ/AuditLegacyBridge/v1/raw-body\0"
_PHASES = (
    "package-root",
    "package-pages",
    "manifest-partition",
    "manifest-duplicates",
    "manifest-order",
    "manifest-counts",
    "legacy-auth",
    "legacy-membership",
    "legacy-heads",
    "legacy-summaries",
)
type _Locator = tuple[bytes, bytes, bytes]
type _Entry = tuple[bytes, bytes, bytes, str, int, str, int]
type _Order = tuple[str, str, str, str]


def _add(
    issues: _IssueIndex, code: str, subject: tuple[str, ...] = (), refs: tuple[_IssueRef, ...] = ()
) -> None:
    issues.add(
        "bridge", "incomplete" if code in bridge._INCOMPLETE else "failed", code, subject, refs
    )


def _ref(kind: Literal["observation", "page"], index: int, order: _Order) -> _IssueRef:
    # R78 reference identity is (domain, complete order), not delivery ordinal.
    return _IssueRef(kind, index, order, json.dumps(order, ensure_ascii=True).encode())


def _subject(locator: _Locator) -> tuple[str, str, str]:
    return str(UUID(bytes=locator[0])), locator[1].decode(), locator[2].decode()


def _admit_root(
    raw: bytes | None, enrollment: bridge.BridgeEnrollment, entry_limit: int, issues: _IssueIndex
) -> codec._Root | None:
    if raw is None:
        _add(issues, "ROOT_MISSING")
        return None
    try:
        root = codec._decode_root(raw)
    except codec._Invalid:
        _add(issues, "ROOT_INVALID")
        return None
    pin = enrollment.stream.bootstrap
    if root.commitment_hash != pin.commitment_hash:
        _add(issues, "ROOT_COMMITMENT_MISMATCH")
        return None
    if (
        root.org_id != enrollment.stream.org_id
        or root.stream_id != enrollment.stream.stream_id
        or root.initial_key_id != pin.initial_key_id
        or root.initial_public_key != pin.initial_public_key
        or root.initial_key_epoch != pin.initial_key_epoch
        or root.audit_boundary != pin.audit_boundary
        or {(w.witness_id, w.namespace_hash) for w in root.witnesses}
        != {(w.witness_id, w.namespace_hash) for w in enrollment.witnesses}
    ):
        _add(issues, "ROOT_ENROLLMENT_MISMATCH")
        return None
    if root.entry_count > entry_limit:
        raise HistoryReconciliationError("RESOURCE_LIMIT")
    return root


@dataclasses.dataclass(slots=True)
class _Endpoint:
    valid: bool = True
    count: int = 0
    low: tuple[AuditHead, _IssueRef] | None = None
    high: tuple[AuditHead, _IssueRef] | None = None


@_operation
def _step(store: _ReconciliationStore, kernel: _BridgeKernel, phase: str) -> bool:
    if store.state != "sealed" or kernel._phase >= len(_PHASES) or phase != _PHASES[kernel._phase]:
        wire.invalid()
    methods = (
        kernel._root_step,
        kernel._pages_step,
        kernel._partition_step,
        kernel._duplicates_step,
        kernel._order_step,
        kernel._counts_step,
        kernel._auth_step,
        kernel._membership_step,
        kernel._heads_step,
        kernel._summaries_step,
    )
    done = methods[kernel._phase]()
    if done:
        kernel._phase += 1
    return done


class _BridgeKernel:
    def __init__(
        self, store: _ReconciliationStore, enrollment: bridge.BridgeEnrollment, issues: _IssueIndex
    ) -> None:
        self._store, self._enrollment, self._issues = store, enrollment, issues
        self._keys = bridge._admit(enrollment)
        self._keys_complete = False
        self._phase = 0
        self._root: codec._Root | None = None
        self._root_read = False
        self._root_after = 0
        self._page_after = 0
        self._duplicate_pages = 0
        self._duplicate_bodies = 0
        self._parsed_entries = 0
        self._manifest_ok = False
        self._complete = False
        self._partition_stage = "conflicts"
        self._page_cursor = (-1, "")
        self._page_first: _IssueRef | None = None
        self._required_after = -1
        self._missing = False
        self._partition_bad = False
        self._entry_cursor = (b"", b"", b"", "", -1)
        self._duplicate_previous: tuple[_Locator, _IssueRef] | None = None
        self._duplicate_bad = False
        self._ordered_page = -1
        self._ordered_hash = ""
        self._ordered_entry = -1
        self._previous: tuple[_Locator, _IssueRef] | None = None
        self._order_bad = False
        self._entry_count = 0
        self._witness_counts = {w.witness_id.bytes: 0 for w in enrollment.witnesses}
        self._foreign_entries = False
        self._counts_checked = False
        self._counts_bad = False
        self._observation_after = 0
        self._locator_after: _Locator = (b"", b"", b"")
        self._membership_stage = "conflicts"
        self._legacy_cursor = (b"", b"", b"", 0)
        self._legacy_first: tuple[_Locator, _IssueRef] | None = None
        self._head_cursor = (0, "", b"")
        self._head_id = 0
        self._head_hash = ""
        self._head_ref: _IssueRef | None = None
        self._first_hash_ref: _IssueRef | None = None
        self._head_hash_count = 0
        self._conflicted: set[bytes] = set()
        self._endpoints = {w.witness_id.bytes: _Endpoint() for w in enrollment.witnesses}
        self._summaries: tuple[bridge.BridgeWitnessSummary, ...] = ()
        self.work = 0

    @property
    def root_bound(self) -> bool:
        return self._root is not None

    @property
    def manifest_complete(self) -> bool:
        return self._complete

    @property
    def duplicate_bodies(self) -> int:
        return self._duplicate_bodies

    @property
    def duplicate_pages(self) -> int:
        return self._duplicate_pages

    @property
    def consistent(self) -> bool:
        return (
            self._phase == len(_PHASES)
            and self._db.execute(
                "SELECT 1 FROM reconciliation_issues WHERE component='bridge' LIMIT 1"
            ).fetchone()
            is None
        )

    @property
    def _db(self) -> sqlite3.Connection:
        return self._store._connection()

    def witness_summaries(self) -> tuple[bridge.BridgeWitnessSummary, ...]:
        return self._summaries if self.consistent else ()

    def step(self, phase: str) -> bool:
        return _step(self._store, self, phase)

    def _page_ref(self, page_hash: str) -> _IssueRef:
        row = self._db.execute(
            "SELECT page_index,representative_position FROM bridge_pages WHERE page_hash=?",
            (page_hash,),
        ).fetchone()
        return _ref("page", row[1] - 1, (f"{row[0]:04d}", page_hash, "", ""))

    def _body_ref(self, ordinal: int) -> _IssueRef:
        row = self._db.execute(
            "SELECT w.uuid,o.key,o.version,l.commitment FROM observations o "
            "JOIN witnesses w ON w.id=o.witness JOIN body_deliveries d ON d.ordinal=o.ordinal "
            "JOIN legacy_bodies l ON l.raw_id=d.raw_id WHERE o.ordinal=?",
            (ordinal,),
        ).fetchone()
        return _ref("observation", ordinal, (*_subject(row[:3]), row[3]))

    def _root_step(self) -> bool:
        if not self._root_read:
            self._root_read = True
            row = self._db.execute(
                "SELECT raw_body,wire_valid FROM package_deliveries WHERE position=0"
            ).fetchone()
            if row is not None and not row[1]:
                _add(self._issues, "ROOT_INVALID")
            else:
                self._root = _admit_root(
                    None if row is None else row[0],
                    self._enrollment,
                    self._store.limits.maximum_manifest_entries,
                    self._issues,
                )
            if self._root is not None:
                supplied = {k.key_id for k in self._enrollment.legacy_keys}
                required = set(self._root.legacy_key_ids)
                for key_id in required - supplied:
                    _add(self._issues, "LEGACY_KEY_MISSING", (key_id,))
                for key_id in supplied - required:
                    _add(self._issues, "LEGACY_KEYSET_MISMATCH", (key_id,))
                self._keys_complete = required <= supplied
        if self._root is None:
            return True
        batch = self._root.pages[self._root_after : self._root_after + 64]
        for page in batch:
            self._db.execute(
                "INSERT INTO root_pages VALUES(?,?,?)",
                (page.page_index, page.page_hash, page.entry_count),
            )
        self.work += len(batch)
        self._root_after += len(batch)
        return self._root_after == len(self._root.pages)

    def _pages_step(self) -> bool:
        row = self._db.execute(
            "SELECT position,raw_body,raw_digest,wire_valid FROM package_deliveries "
            "WHERE position>? ORDER BY position LIMIT 1",
            (self._page_after,),
        ).fetchone()
        if row is None:
            return True
        self._page_after = row[0]
        self.work += 1
        try:
            if not row[3]:
                raise codec._Invalid
            page = codec._decode_page(row[1])
        except codec._Invalid:
            digest = row[2].hex()
            _add(
                self._issues,
                "PAGE_INVALID",
                (digest,),
                (_ref("page", row[0] - 1, ("", digest, "", "")),),
            )
            return False
        ref = _ref("page", row[0] - 1, (f"{page.page_index:04d}", page.page_hash, "", ""))
        if (
            page.org_id != self._enrollment.stream.org_id
            or page.stream_id != self._enrollment.stream.stream_id
        ):
            _add(self._issues, "PAGE_IDENTITY_MISMATCH", (page.page_hash,), (ref,))
            return False
        self._db.execute("INSERT INTO page_deliveries VALUES(?,?)", (row[0], page.page_hash))
        if self._db.execute(
            "SELECT 1 FROM bridge_pages WHERE page_hash=?", (page.page_hash,)
        ).fetchone():
            self._duplicate_pages += 1
            return False
        if self._parsed_entries + len(page.entries) > 524_288:
            raise HistoryReconciliationError("RESOURCE_LIMIT")
        self._db.execute(
            "INSERT INTO bridge_pages VALUES(?,?,?,?)",
            (page.page_hash, page.page_index, row[0], len(page.entries)),
        )
        for index, entry in enumerate(page.entries):
            witness, key, version = entry.locator
            self._db.execute(
                "INSERT INTO page_entries VALUES(?,?,?,?,?,?,?)",
                (
                    page.page_hash,
                    index,
                    witness.bytes,
                    key.encode(),
                    version.encode(),
                    entry.body_hash,
                    entry.body_bytes,
                ),
            )
        self._parsed_entries += len(page.entries)
        if (
            self._root is not None
            and not self._db.execute(
                "SELECT 1 FROM root_pages WHERE page_hash=?", (page.page_hash,)
            ).fetchone()
        ):
            _add(self._issues, "PAGE_UNLISTED", (page.page_hash,), (ref,))
        return False

    def _partition_step(self) -> bool:
        if self._root is None:
            return True
        if self._partition_stage == "conflicts":
            rows = self._db.execute(
                "SELECT page_index,page_hash FROM bridge_pages WHERE (page_index,page_hash)>(?,?) "
                "ORDER BY page_index,page_hash LIMIT 64",
                self._page_cursor,
            ).fetchall()
            for index, page_hash in rows:
                ref = self._page_ref(page_hash)
                if index == self._page_cursor[0]:
                    if self._page_first is None:
                        wire.invalid()
                    _add(self._issues, "PAGE_CONFLICT", (f"{index:04d}",), (self._page_first, ref))
                else:
                    self._page_first = ref
                self._page_cursor = (index, page_hash)
            self.work += len(rows)
            if not rows:
                self._partition_stage = "missing"
            return False
        rows = self._db.execute(
            "SELECT r.page_index,r.page_hash,r.entry_count,b.page_index,b.entry_count FROM "
            "root_pages r "
            "LEFT JOIN bridge_pages b ON b.page_hash=r.page_hash WHERE r.page_index>? ORDER "
            "BY r.page_index LIMIT 64",
            (self._required_after,),
        ).fetchall()
        for index, page_hash, expected, actual_index, actual_count in rows:
            self._required_after = index
            if self._partition_stage == "missing":
                if actual_index is None:
                    self._missing = True
                    _add(self._issues, "PAGE_MISSING", (f"{index:04d}",))
            elif (
                actual_index != index
                or expected != actual_count
                or (index < len(self._root.pages) - 1 and actual_count != 512)
            ):
                self._partition_bad = True
                _add(self._issues, "MANIFEST_PARTITION_INVALID", refs=(self._page_ref(page_hash),))
        self.work += len(rows)
        if rows:
            return False
        if self._partition_stage == "missing" and not self._missing:
            self._partition_stage, self._required_after = "partition", -1
            return False
        self._manifest_ok = not (self._missing or self._partition_bad)
        return True

    def _duplicates_step(self) -> bool:
        if not self._manifest_ok:
            return True
        rows = self._db.execute(
            "SELECT witness,key,version,page_hash,entry_position FROM page_entries "
            "WHERE (witness,key,version,page_hash,entry_position)>(?,?,?,?,?) "
            "ORDER BY witness,key,version,page_hash,entry_position LIMIT 64",
            self._entry_cursor,
        ).fetchall()
        for row in rows:
            self._entry_cursor = row
            if not self._db.execute(
                "SELECT 1 FROM root_pages WHERE page_hash=?", (row[3],)
            ).fetchone():
                continue
            locator, ref = row[:3], self._page_ref(row[3])
            if self._duplicate_previous is not None and locator == self._duplicate_previous[0]:
                self._duplicate_bad = True
                _add(
                    self._issues,
                    "MANIFEST_LOCATOR_DUPLICATE",
                    _subject(locator),
                    (self._duplicate_previous[1], ref),
                )
            self._duplicate_previous = locator, ref
        self.work += len(rows)
        if rows:
            return False
        self._manifest_ok = not self._duplicate_bad
        return True

    def _entries(self) -> list[_Entry] | None:
        """One fixed page cursor and at most 64 entries; empty batch advances a page."""
        if not self._ordered_hash:
            row = self._db.execute(
                "SELECT page_index,page_hash FROM root_pages WHERE page_index>? ORDER BY "
                "page_index LIMIT 1",
                (self._ordered_page,),
            ).fetchone()
            if row is None:
                return None
            self._ordered_page, self._ordered_hash = row
            self._ordered_entry = -1
        rows = self._db.execute(
            "SELECT witness,key,version,page_hash,entry_position,body_hash,body_length FROM "
            "page_entries "
            "WHERE page_hash=? AND entry_position>? ORDER BY entry_position LIMIT 64",
            (self._ordered_hash, self._ordered_entry),
        ).fetchall()
        if rows:
            self._ordered_entry = rows[-1][4]
        else:
            self._ordered_hash = ""
        self.work += len(rows)
        return cast(list[_Entry], rows)

    def _order_step(self) -> bool:
        if not self._manifest_ok:
            return True
        rows = self._entries()
        if rows is None:
            self._manifest_ok = not self._order_bad
            self._ordered_page = -1
            return True
        for row in rows:
            locator, ref = row[:3], self._page_ref(row[3])
            if self._previous is not None and self._previous[0] >= locator:
                self._order_bad = True
                _add(self._issues, "MANIFEST_ORDER_INVALID", refs=(self._previous[1], ref))
            self._previous = locator, ref
            self._entry_count += 1
            if locator[0] in self._witness_counts:
                self._witness_counts[locator[0]] += 1
            else:
                self._foreign_entries = True
        return False

    def _counts_step(self) -> bool:
        if not self._manifest_ok:
            return True
        if self._root is None:
            wire.invalid()
        if not self._counts_checked:
            self._counts_checked = True
            self._counts_bad = (
                self._foreign_entries
                or self._entry_count != self._root.entry_count
                or self._witness_counts
                != {w.witness_id.bytes: w.entry_count for w in self._root.witnesses}
            )
        if self._counts_bad:
            rows = self._db.execute(
                "SELECT page_index,page_hash FROM root_pages WHERE page_index>? ORDER BY "
                "page_index LIMIT 64",
                (self._ordered_page,),
            ).fetchall()
            for index, page_hash in rows:
                self._ordered_page = index
                _add(self._issues, "MANIFEST_COUNT_MISMATCH", refs=(self._page_ref(page_hash),))
            self.work += len(rows)
            return not rows
        entries = self._entries()
        if entries is None:
            self._complete = True
            return True
        for entry in entries:
            self._db.execute("INSERT INTO committed_entries VALUES(?,?,?,?,?,?,?)", entry)
        return False

    def _authenticate(self, raw_id: int) -> tuple[str, int | None, str | None, str]:
        old = self._db.execute(
            "SELECT authentication_state,audit_id,row_hash,commitment FROM legacy_bodies "
            "WHERE raw_id=?",
            (raw_id,),
        ).fetchone()
        if old is not None:
            return cast(tuple[str, int | None, str | None, str], old)
        raw = self._db.execute(
            "SELECT o.body FROM raw_bodies r JOIN observations o ON "
            "o.ordinal=r.representative_ordinal WHERE r.raw_id=?",
            (raw_id,),
        ).fetchone()[0]
        digest = hashlib.sha256(_BODY_DOMAIN + raw).hexdigest()
        head = None
        try:
            payload = legacy._decode(raw, self._enrollment.stream.org_id)
        except legacy._InvalidLegacy:
            state = "LEGACY_BODY_INVALID"
        else:
            if legacy._authenticate(payload, self._keys):
                head, state = payload.head, "authenticated"
            else:
                state = (
                    "LEGACY_AUTHENTICATION_FAILED"
                    if self._keys_complete
                    else "LEGACY_AUTHENTICATION_UNESTABLISHED"
                )
        result = (
            state,
            None if head is None else head.latest_id,
            None if head is None else head.latest_row_hash,
            digest,
        )
        self._db.execute("INSERT INTO legacy_bodies VALUES(?,?,?,?,?)", (raw_id, *result))
        return result

    def collection_gap(self, witness: UUID, reason: str, refs: tuple[_IssueRef, ...] = ()) -> None:
        if witness.bytes not in self._witness_counts:
            _add(self._issues, "OBSERVATION_SCOPE_MISMATCH", (str(witness),), refs)
        else:
            _add(self._issues, "WITNESS_COLLECTION_GAP", (str(witness), reason), refs)

    def _auth_step(self) -> bool:
        if not self._store._identity_done:
            wire.invalid()
        rows = self._db.execute(
            "SELECT "
            "o.ordinal,w.uuid,o.key,o.version,o.kind,o.outcome,o.failure,"
            "d.raw_id,r.format,r.byte_length "
            "FROM observations o JOIN witnesses w ON w.id=o.witness "
            "LEFT JOIN body_deliveries d ON d.ordinal=o.ordinal LEFT JOIN raw_bodies r ON "
            "r.raw_id=d.raw_id "
            "WHERE o.ordinal>? ORDER BY o.ordinal LIMIT 64",
            (self._observation_after,),
        ).fetchall()
        for ordinal, witness, key, version, kind, outcome, failure, raw_id, format_, length in rows:
            self._observation_after = ordinal
            locator = witness, key, version
            subject = _subject(locator)
            ref = _ref("observation", ordinal, (*subject, ""))
            if (
                kind == "gap"
            ):  # Typed pure-domain input; collected gaps have no observation ordinal.
                self.collection_gap(UUID(bytes=witness), failure, (ref,))
                continue
            committed = self._db.execute(
                "SELECT body_hash,body_length FROM committed_entries WHERE witness=? AND key=? "
                "AND version=?",
                locator,
            ).fetchone()
            # The mixed engine classifies every raw before entering here. Unknown
            # transport outcomes remain collection diagnostics; a filename never
            # supplies their format. Pure-domain fixtures retain their declared
            # LegacyBodyObservation type without asserting successful decoding.
            typed_legacy = kind in {"legacy-marker", "legacy-unavailable"}
            if raw_id is not None and format_ not in {"legacy", "v2", "invalid"}:
                wire.invalid()
            if raw_id is None and not typed_legacy:
                continue
            if raw_id is not None and format_ != "legacy" and committed is None:
                continue
            name = subject[1].rsplit("/", 1)[-1]
            prefix, separator, _ = name.partition("-")
            scoped = (
                witness in self._witness_counts
                and subject[1].startswith(f"checkpoints/{self._enrollment.stream.org_id}/")
                and bool(separator and prefix and all("0" <= c <= "9" for c in prefix))
            )
            if not scoped and (format_ == "legacy" or typed_legacy):
                _add(self._issues, "OBSERVATION_SCOPE_MISMATCH", subject, (ref,))
                continue
            if raw_id is None:
                _add(
                    self._issues,
                    "LEGACY_DELETE_MARKER" if outcome == "marker" else "LEGACY_BODY_UNAVAILABLE",
                    subject,
                    (ref,),
                )
                continue
            state, audit_id, row_hash, digest = self._authenticate(raw_id)
            ref = _ref("observation", ordinal, (*subject, digest))
            previous = self._db.execute(
                "SELECT 1 FROM legacy_locators WHERE witness=? AND key=? AND version=? AND "
                "raw_id=?",
                (*locator, raw_id),
            ).fetchone()
            if previous is None:
                self._db.execute(
                    "INSERT INTO legacy_locators VALUES(?,?,?,?,?)", (*locator, raw_id, ordinal)
                )
            elif state == "authenticated":
                self._duplicate_bodies += 1
            exact = committed is not None and committed == (digest, length)
            self._db.execute(
                "INSERT INTO legacy_membership VALUES(?,?,?)",
                (ordinal, int(committed is not None), int(exact)),
            )
            if committed is not None and not exact:
                _add(self._issues, "LEGACY_BODY_COMMITMENT_MISMATCH", subject, (ref,))
            if state != "authenticated":
                _add(self._issues, state, (digest,), (ref,))
                continue
            old = self._db.execute(
                "SELECT representative_ordinal FROM legacy_witness_heads WHERE witness=? AND "
                "audit_id=? AND row_hash=?",
                (witness, audit_id, row_hash),
            ).fetchone()
            if old is None:
                self._db.execute(
                    "INSERT INTO legacy_witness_heads VALUES(?,?,?,?)",
                    (witness, audit_id, row_hash, ordinal),
                )
            else:
                old_ref = self._body_ref(old[0])
                if (ref.order, ref.index) < (old_ref.order, old_ref.index):
                    self._db.execute(
                        "UPDATE legacy_witness_heads SET representative_ordinal=? "
                        "WHERE witness=? AND "
                        "audit_id=? AND row_hash=?",
                        (ordinal, witness, audit_id, row_hash),
                    )
            if self._complete and committed is None:
                _add(self._issues, "UNLISTED_AUTHENTIC_LEGACY", subject, (ref,))
        self.work += len(rows)
        return not rows

    def _membership_step(self) -> bool:
        if self._membership_stage == "conflicts":
            rows = self._db.execute(
                "SELECT witness,key,version,raw_id,representative_ordinal FROM legacy_locators "
                "WHERE (witness,key,version,raw_id)>(?,?,?,?) ORDER BY "
                "witness,key,version,raw_id LIMIT 64",
                self._legacy_cursor,
            ).fetchall()
            for row in rows:
                locator, ref = row[:3], self._body_ref(row[4])
                if self._legacy_first is not None and locator == self._legacy_first[0]:
                    _add(
                        self._issues,
                        "IMMUTABLE_LOCATOR_CONFLICT",
                        _subject(locator),
                        (self._legacy_first[1], ref),
                    )
                else:
                    self._legacy_first = locator, ref
                self._legacy_cursor = row[:4]
            self.work += len(rows)
            if not rows:
                self._membership_stage = "missing"
            return False
        if not self._complete:
            return True
        rows = self._db.execute(
            "SELECT witness,key,version FROM committed_entries WHERE "
            "(witness,key,version)>(?,?,?) ORDER BY witness,key,version LIMIT 64",
            self._locator_after,
        ).fetchall()
        for locator in rows:
            self._locator_after = locator
            if not self._db.execute(
                "SELECT 1 FROM legacy_locators WHERE witness=? AND key=? AND version=? LIMIT 1",
                locator,
            ).fetchone():
                _add(self._issues, "LEGACY_BODY_MISSING", _subject(locator))
        self.work += len(rows)
        if not rows:
            self._locator_after = b"", b"", b""
            return True
        return False

    def _flush_hash(self) -> None:
        if self._head_ref is None:
            return
        boundary = cast(AuditHead, self._enrollment.stream.bootstrap.audit_boundary)
        self._head_hash_count += 1
        if self._first_hash_ref is None:
            self._first_hash_ref = self._head_ref
        if self._head_hash_count > 1 or (
            self._head_id == boundary.latest_id and self._head_hash != boundary.latest_row_hash
        ):
            _add(
                self._issues,
                "SIGNED_HEAD_CONFLICT",
                (f"{self._head_id:019d}",),
                (self._first_hash_ref, self._head_ref),
            )
            for witness in self._witness_counts:  # At most four bounded exact lookups.
                if self._db.execute(
                    "SELECT 1 FROM legacy_witness_heads WHERE witness=? AND audit_id=? LIMIT 1",
                    (witness, self._head_id),
                ).fetchone():
                    self._conflicted.add(witness)
        if self._head_id > boundary.latest_id:
            _add(
                self._issues,
                "ABOVE_BOOTSTRAP_BOUNDARY",
                (f"{self._head_id:019d}",),
                (self._head_ref,),
            )

    def _heads_step(self) -> bool:
        rows = self._db.execute(
            "SELECT audit_id,row_hash,witness,representative_ordinal FROM legacy_witness_heads "
            "WHERE (audit_id,row_hash,witness)>(?,?,?) ORDER BY audit_id,row_hash,witness LIMIT 64",
            self._head_cursor,
        ).fetchall()
        for audit_id, row_hash, witness, ordinal in rows:
            ref = self._body_ref(ordinal)
            if (audit_id, row_hash) != (self._head_id, self._head_hash):
                self._flush_hash()
                if audit_id != self._head_id:
                    self._first_hash_ref, self._head_hash_count = None, 0
                self._head_id, self._head_hash, self._head_ref = audit_id, row_hash, ref
            elif self._head_ref is None or (ref.order, ref.index) < (
                self._head_ref.order,
                self._head_ref.index,
            ):
                self._head_ref = ref
            self._head_cursor = audit_id, row_hash, witness
        self.work += len(rows)
        if not rows:
            self._flush_hash()
            self._head_ref = None
            return True
        return False

    def _summaries_step(self) -> bool:
        if not self._complete:
            return True
        rows = self._db.execute(
            "SELECT witness,key,version,body_hash,body_length FROM committed_entries "
            "WHERE (witness,key,version)>(?,?,?) ORDER BY witness,key,version LIMIT 64",
            self._locator_after,
        ).fetchall()
        for row in rows:
            self._locator_after = row[:3]
            endpoint = self._endpoints[row[0]]
            bodies = self._db.execute(
                "SELECT "
                "l.authentication_state,l.audit_id,l.row_hash,l.commitment,"
                "r.byte_length,d.representative_ordinal "
                "FROM legacy_locators d JOIN legacy_bodies l ON l.raw_id=d.raw_id JOIN "
                "raw_bodies r ON r.raw_id=d.raw_id "
                "WHERE d.witness=? AND d.key=? AND d.version=? ORDER BY d.raw_id LIMIT 2",
                row[:3],
            ).fetchall()
            if len(bodies) != 1 or bodies[0][0] != "authenticated" or bodies[0][3:5] != row[3:5]:
                endpoint.valid = False
                continue
            body = bodies[0]
            head, ref = AuditHead(body[1], body[2]), self._body_ref(body[5])
            endpoint.count += 1
            if endpoint.low is None or (head.latest_id, ref.order, ref.index) < (
                endpoint.low[0].latest_id,
                endpoint.low[1].order,
                endpoint.low[1].index,
            ):
                endpoint.low = head, ref
            if (
                endpoint.high is None
                or head.latest_id > endpoint.high[0].latest_id
                or (
                    head == endpoint.high[0]
                    and (ref.order, ref.index) < (endpoint.high[1].order, endpoint.high[1].index)
                )
            ):
                endpoint.high = head, ref
        self.work += len(rows)
        if rows:
            return False
        if self._root is None:
            wire.invalid()
        summaries = []
        for witness in self._root.witnesses:
            endpoint = self._endpoints[witness.witness_id.bytes]
            if (
                not endpoint.valid
                or witness.witness_id.bytes in self._conflicted
                or endpoint.low is None
                or endpoint.high is None
            ):
                continue
            low, low_ref = endpoint.low
            high, high_ref = endpoint.high
            if (
                endpoint.count != witness.entry_count
                or low != witness.lowest_head
                or high != witness.highest_head
                or high != self._enrollment.stream.bootstrap.audit_boundary
            ):
                _add(
                    self._issues,
                    "WITNESS_SUMMARY_MISMATCH",
                    (str(witness.witness_id),),
                    (low_ref, high_ref),
                )
            summaries.append(
                bridge.BridgeWitnessSummary(witness.witness_id, endpoint.count, low, high)
            )
        self._summaries = tuple(summaries)
        return True
