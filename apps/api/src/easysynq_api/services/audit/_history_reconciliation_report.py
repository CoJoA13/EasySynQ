"""Compact complete outcomes and an exact bounded parent decoding boundary."""

from __future__ import annotations

import dataclasses
import re
from typing import Any, Literal, cast

from . import _history_reconciliation_protocol as protocol
from . import _history_spool_protocol as wire
from . import bootstrap_bridge as bridge
from . import history_reconciliation as public
from ._history_reconciliation_bridge import _BridgeKernel
from ._history_reconciliation_issues import _IssueIndex, _maximum_refs
from ._history_reconciliation_lineage import _LineageKernel
from ._history_reconciliation_store import _ReconciliationStore
from .history_collection import HistoryWitnessSummary
from .lineage import AuditHead, BootstrapPin

ESTABLISHED_CHECKS = (
    "required-witness-provider-traversal",
    "external-root-content-binding",
    "committed-page-and-locator-closure",
    "retained-legacy-signature-authentication",
    "per-witness-signed-boundary-binding",
    "collected-observation-reconciliation",
    "v2-material-and-edge-consistency",
    "per-witness-v2-path-coverage",
    "cross-format-signed-head-consistency",
)
UNPROVED_CHECKS = (
    "provider-non-omission",
    "atomic-snapshot",
    "historical-deletion-absence",
    "witness-custody",
    "database-chain-agreement",
    "freshness",
    "rollback-memory-continuity",
    "durable-delivery",
    "operational-key-activation",
    "source-independent-recovery",
)
_BRIDGE_CODES = frozenset(
    {
        "ABOVE_BOOTSTRAP_BOUNDARY",
        "IMMUTABLE_LOCATOR_CONFLICT",
        "LEGACY_AUTHENTICATION_FAILED",
        "LEGACY_AUTHENTICATION_UNESTABLISHED",
        "LEGACY_BODY_COMMITMENT_MISMATCH",
        "LEGACY_BODY_INVALID",
        "LEGACY_BODY_MISSING",
        "LEGACY_BODY_UNAVAILABLE",
        "LEGACY_DELETE_MARKER",
        "LEGACY_KEYSET_MISMATCH",
        "LEGACY_KEY_MISSING",
        "MANIFEST_COUNT_MISMATCH",
        "MANIFEST_LOCATOR_DUPLICATE",
        "MANIFEST_ORDER_INVALID",
        "MANIFEST_PARTITION_INVALID",
        "OBSERVATION_SCOPE_MISMATCH",
        "PAGE_CONFLICT",
        "PAGE_IDENTITY_MISMATCH",
        "PAGE_INVALID",
        "PAGE_MISSING",
        "PAGE_UNLISTED",
        "ROOT_COMMITMENT_MISMATCH",
        "ROOT_ENROLLMENT_MISMATCH",
        "ROOT_INVALID",
        "ROOT_MISSING",
        "SIGNED_HEAD_CONFLICT",
        "UNLISTED_AUTHENTIC_LEGACY",
        "WITNESS_COLLECTION_GAP",
        "WITNESS_SUMMARY_MISMATCH",
    }
)
_LINEAGE_CODES = frozenset(
    {
        "ANCHOR_ID_CONFLICT",
        "AUDIT_HEAD_CONFLICT",
        "AUDIT_HEAD_REGRESSION",
        "DISCONNECTED_GRAPH",
        "EMPTY_GRAPH",
        "ENVELOPE_INVALID",
        "IDENTITY_MISMATCH",
        "IMMUTABLE_LOCATOR_CONFLICT",
        "KEY_EPOCH_VIOLATION",
        "LINEAGE_FORK",
        "REQUIRED_CHECKPOINT_CONFLICT",
        "REQUIRED_CHECKPOINT_MISSING",
        "SEQUENCE_DISCONTINUITY",
        "UNKNOWN_KEY",
    }
)
_LINEAGE_INCOMPLETE = frozenset(
    {"DISCONNECTED_GRAPH", "EMPTY_GRAPH", "REQUIRED_CHECKPOINT_MISSING", "UNKNOWN_KEY"}
)
_COMPOSITION_CODES = {
    "CHECKPOINT_BODY_INVALID": "failed",
    "GLOBAL_SIGNED_HEAD_CONFLICT": "failed",
    "V2_WITNESS_COVERAGE_MISSING": "incomplete",
}


def _build_report(
    store: _ReconciliationStore,
    bootstrap: _BridgeKernel,
    lineage: _LineageKernel,
    issues: _IssueIndex,
) -> protocol._RawResult:
    summary = store._sealed_summary
    failed, incomplete = issues.counts()
    status: public._Status = "failed" if failed else "incomplete" if incomplete else "consistent"
    shown = issues.display(store.limits.maximum_issues)
    usable = None
    if status == "consistent":
        tip = lineage.tip()
        if not bootstrap.consistent or not lineage.consistent or tip is None:
            wire.invalid()
        usable = public.HistoryReconciliationUsable(
            store.scope.enrollment.stream.bootstrap,
            tip,
            lineage.path_length,
            lineage.used_epoch_count,
            bootstrap.witness_summaries(),
        )
    row = (
        store._connection()
        .execute(
            "SELECT coalesce(sum(r.format='legacy'),0),coalesce(sum(r.format='v2'),0),"
            "coalesce(sum(r.format='invalid'),0) FROM body_deliveries d JOIN raw_bodies r "
            "ON r.raw_id=d.raw_id"
        )
        .fetchone()
    )
    return public.HistoryReconciliationReport(
        status,
        "collected-required-witness-history",
        summary.witnesses,
        public.HistoryReconciliationCounts(
            len(store.scope.page_lengths),
            (store.scope.root_length or 0) + sum(store.scope.page_lengths),
            summary.admitted_total_bytes,
            row[0],
            row[1],
            row[2],
            bootstrap.duplicate_bodies,
            lineage.duplicate_observations,
            bootstrap.duplicate_pages,
        ),
        shown,
        failed,
        incomplete,
        failed + incomplete - len(shown),
        lineage.required_checkpoint_relation,
        ESTABLISHED_CHECKS if status == "consistent" else (),
        UNPROVED_CHECKS,
        usable,
    )


def _report_payload(report: public.HistoryReconciliationReport) -> dict[str, Any]:
    result = dataclasses.asdict(report)
    for witness in result["witnesses"]:
        witness["witness_id"] = str(witness["witness_id"])
    for issue in result["issues"]:
        if issue["witness_id"] is not None:
            issue["witness_id"] = str(issue["witness_id"])
    if result["usable"] is not None:
        usable = result["usable"]
        usable["bootstrap_pin"]["initial_public_key"] = usable["bootstrap_pin"][
            "initial_public_key"
        ].hex()
        for witness in usable["witness_summaries"]:
            witness["witness_id"] = str(witness["witness_id"])
    if len(wire.encode(result)) > protocol.RESULT_MAX:
        wire.invalid()
    return result


def _fields(value: Any, record: Any) -> dict[str, Any]:
    return wire.fields(value, {f.name for f in dataclasses.fields(record)})


def _digest(value: Any, *, key: bool = False) -> str:
    if (
        type(value) is not str
        or re.fullmatch(("ed25519-sha256:" if key else "") + "[0-9a-f]{64}", value) is None
    ):
        wire.invalid()
    return value


def _head(value: Any) -> AuditHead:
    _fields(value, AuditHead)
    return AuditHead(
        wire.integer(value["latest_id"], 1, public._MAX_BIGINT), _digest(value["latest_row_hash"])
    )


def _witnesses(value: Any, scope: protocol._PublicScope) -> tuple[HistoryWitnessSummary, ...]:
    if type(value) is not list or len(value) != len(scope.witnesses):
        wire.invalid()
    witnesses = []
    for raw, enrolled in zip(value, scope.witnesses, strict=True):
        _fields(raw, HistoryWitnessSummary)
        if (
            type(raw["witness_id"]) is not str
            or raw["witness_id"] != str(enrolled.witness_id)
            or type(raw["namespace_hash"]) is not str
            or raw["namespace_hash"] != enrolled.namespace_hash
            or type(raw["terminal_reached"]) is not bool
        ):
            wire.invalid()
        for name in raw.keys() - {"witness_id", "namespace_hash", "terminal_reached"}:
            wire.integer(raw[name], 0, 100_000)
        w = HistoryWitnessSummary(**{**raw, "witness_id": enrolled.witness_id})
        if (
            not 1 <= w.page_attempts <= scope.limits.maximum_pages
            or not 0 <= w.page_attempts - w.admitted_pages <= 1
            or (
                w.terminal_reached and (not w.admitted_pages or w.page_attempts != w.admitted_pages)
            )
            or w.version_observations != w.successful_reads + w.unavailable_reads
            or w.version_observations + w.delete_observations > w.admitted_pages * 1000
            or w.duplicate_body_deliveries > w.successful_reads
            or w.conflicting_locators > w.successful_reads // 2
        ):
            wire.invalid()
        witnesses.append(w)
    if (
        sum(w.page_attempts for w in witnesses) > scope.limits.maximum_pages
        or sum(w.version_observations + w.delete_observations for w in witnesses)
        > scope.limits.maximum_observations
    ):
        wire.invalid()
    return tuple(witnesses)


def _issue(
    value: Any, scope: protocol._PublicScope, observations: int
) -> public.HistoryReconciliationIssue:
    _fields(value, public.HistoryReconciliationIssue)
    component, code = value["component"], value["code"]
    if type(component) is not str or type(code) is not str:
        wire.invalid()
    severity: str | None
    if component == "collection":
        severity = wire.ISSUES[wire.issue_code(code)]
    elif component == "bridge" and code in _BRIDGE_CODES:
        severity = "incomplete" if code in bridge._INCOMPLETE else "failed"
    elif component == "lineage" and code in _LINEAGE_CODES:
        severity = "incomplete" if code in _LINEAGE_INCOMPLETE else "failed"
    elif component == "composition":
        severity = _COMPOSITION_CODES.get(code)
    else:
        wire.invalid()
    if severity is None or type(value["severity"]) is not str or value["severity"] != severity:
        wire.invalid()
    witness = value["witness_id"]
    if witness is not None:
        witness = protocol._uuid(witness)
        if witness not in {w.witness_id for w in scope.witnesses}:
            wire.invalid()
    if witness is None and (component == "collection" or code == "V2_WITNESS_COVERAGE_MISSING"):
        wire.invalid()
    count = wire.integer(value["count"], 1, 100_000 if component == "collection" else 1)
    refs = value["references"]
    if type(refs) is not list or len(refs) > _maximum_refs(component, code):
        wire.invalid()
    references = []
    for ref in refs:
        _fields(ref, public.HistoryReference)
        kind = ref["kind"]
        if type(kind) is not str or kind not in {"observation", "page"}:
            wire.invalid()
        if kind == "page" and component != "bridge":
            wire.invalid()
        index = wire.integer(
            ref["index"],
            1 if kind == "observation" else 0,
            observations if kind == "observation" else len(scope.page_lengths) - 1,
        )
        references.append(
            public.HistoryReference(cast(Literal["observation", "page"], kind), index)
        )
    if len(set(references)) != len(references):
        wire.invalid()
    return public.HistoryReconciliationIssue(
        cast(Literal["collection", "bridge", "lineage", "composition"], component),
        code,
        cast(Literal["failed", "incomplete"], severity),
        witness,
        count,
        tuple(references),
    )


def _usable(
    value: Any, scope: protocol._PublicScope, counts: public.HistoryReconciliationCounts
) -> public.HistoryReconciliationUsable:
    _fields(value, public.HistoryReconciliationUsable)
    pin = _fields(value["bootstrap_pin"], BootstrapPin)
    _digest(pin["commitment_hash"])
    _digest(pin["initial_key_id"], key=True)
    protocol._key_bytes(pin["initial_public_key"])
    wire.integer(pin["initial_key_epoch"], 0, public._MAX_BIGINT)
    _head(pin["audit_boundary"])
    if pin != protocol.scope_payload(scope)["enrollment"]["stream"]["bootstrap"]:
        wire.invalid()
    tip = _fields(value["tip"], public.HistoryReconciliationTip)
    checked_tip = public.HistoryReconciliationTip(
        _digest(tip["anchor_hash"]),
        wire.integer(tip["sequence"], 1, scope.limits.maximum_observations),
        _head(tip["audit_head"]),
        _digest(tip["key_id"], key=True),
        wire.integer(tip["key_epoch"], 0, public._MAX_BIGINT),
    )
    length = wire.integer(value["path_length"], 1, scope.limits.maximum_observations)
    epochs = wire.integer(value["used_epoch_count"], 1, length)
    bootstrap = scope.enrollment.stream.bootstrap
    boundary = bootstrap.audit_boundary
    if boundary is None:
        wire.invalid()
    if (
        checked_tip.sequence != length
        or length * len(scope.witnesses) > counts.v2_body_deliveries
        or checked_tip.key_epoch != bootstrap.initial_key_epoch + epochs - 1
        or (epochs == 1 and checked_tip.key_id != bootstrap.initial_key_id)
        or checked_tip.audit_head.latest_id < boundary.latest_id
        or (
            checked_tip.audit_head.latest_id == boundary.latest_id
            and checked_tip.audit_head != boundary
        )
    ):
        wire.invalid()
    raw_witnesses = value["witness_summaries"]
    if type(raw_witnesses) is not list or len(raw_witnesses) != len(scope.witnesses):
        wire.invalid()
    witnesses = []
    for raw, enrolled in zip(raw_witnesses, scope.witnesses, strict=True):
        _fields(raw, bridge.BridgeWitnessSummary)
        if protocol._uuid(raw["witness_id"]) != enrolled.witness_id:
            wire.invalid()
        count = wire.integer(raw["committed_locators"], 1, scope.limits.maximum_manifest_entries)
        low, high = _head(raw["lowest_head"]), _head(raw["highest_head"])
        if (
            high != boundary
            or low.latest_id > high.latest_id
            or (low.latest_id == high.latest_id and low != high)
        ):
            wire.invalid()
        witnesses.append(bridge.BridgeWitnessSummary(enrolled.witness_id, count, low, high))
    committed = sum(w.committed_locators for w in witnesses)
    if committed > min(scope.limits.maximum_manifest_entries, counts.legacy_body_deliveries):
        wire.invalid()
    required = scope.enrollment.stream.required_checkpoint
    if required is not None and (
        required.sequence > length
        or ((required.anchor_hash == checked_tip.anchor_hash) != (required.sequence == length))
    ):
        wire.invalid()
    return public.HistoryReconciliationUsable(
        bootstrap, checked_tip, length, epochs, tuple(witnesses)
    )


def _collection_counts(
    witnesses: tuple[HistoryWitnessSummary, ...],
    issues: tuple[public.HistoryReconciliationIssue, ...],
    omitted: int,
) -> None:
    first = 1
    for witness in witnesses:
        own = [
            i for i in issues if i.component == "collection" and i.witness_id == witness.witness_id
        ]
        counts = {i.code: i.count for i in own}
        if len(counts) != len(own):
            wire.invalid()
        end = first + witness.version_observations + witness.delete_observations
        if any(not first <= ref.index < end for i in own for ref in i.references):
            wire.invalid()
        first = end
        deleted = counts.get("DELETE_OBSERVATION", 0)
        if (
            "DELETE_OBSERVATION" in counts
            and not witness.delete_observations
            <= deleted
            <= witness.delete_observations + witness.unavailable_reads
        ):
            wire.invalid()
        unavailable = (
            counts.get("VERSION_UNAVAILABLE", 0)
            + counts.get("INELIGIBLE_LOCATOR", 0)
            + max(0, deleted - witness.delete_observations)
        )
        if unavailable > witness.unavailable_reads:
            wire.invalid()
        if (
            "LOCATOR_CONFLICT" in counts
            and counts["LOCATOR_CONFLICT"] != witness.conflicting_locators
        ) or (
            "LIST_UNAVAILABLE" in counts
            and counts["LIST_UNAVAILABLE"] != witness.page_attempts - witness.admitted_pages
        ):
            wire.invalid()
        if "CURSOR_CYCLE" in counts and (
            counts["CURSOR_CYCLE"] != 1
            or witness.terminal_reached
            or witness.page_attempts != witness.admitted_pages
        ):
            wire.invalid()
        if not omitted and (
            unavailable != witness.unavailable_reads
            or deleted < witness.delete_observations
            or counts.get("LOCATOR_CONFLICT", 0) != witness.conflicting_locators
            or counts.get("LIST_UNAVAILABLE", 0) != witness.page_attempts - witness.admitted_pages
            or (not witness.terminal_reached)
            != any(c in counts for c in ("LIST_UNAVAILABLE", "CURSOR_CYCLE"))
        ):
            wire.invalid()


def _decode_report(
    payload: bytes, scope: protocol._PublicScope
) -> public.HistoryReconciliationReport:
    if type(payload) is not bytes or not 0 < len(payload) <= protocol.RESULT_MAX:
        wire.invalid()
    value = wire.metadata(payload)
    _fields(value, public.HistoryReconciliationReport)
    if value["scope"] != "collected-required-witness-history":
        wire.invalid()
    witnesses = _witnesses(value["witnesses"], scope)
    observations = sum(w.version_observations + w.delete_observations for w in witnesses)
    raw_counts = _fields(value["counts"], public.HistoryReconciliationCounts)
    for key, number in raw_counts.items():
        wire.integer(
            number,
            0,
            scope.limits.maximum_total_bytes
            if key in {"package_bytes", "admitted_total_bytes"}
            else scope.limits.maximum_bridge_pages
            if key in {"supplied_pages", "canonical_page_duplicates"}
            else scope.limits.maximum_observations,
        )
    counts = public.HistoryReconciliationCounts(**raw_counts)
    if (
        counts.supplied_pages != len(scope.page_lengths)
        or counts.package_bytes != (scope.root_length or 0) + sum(scope.page_lengths)
        or counts.admitted_total_bytes < counts.package_bytes
        or counts.legacy_body_deliveries
        + counts.v2_body_deliveries
        + counts.invalid_shape_deliveries
        != sum(w.successful_reads for w in witnesses)
        or counts.legacy_duplicate_deliveries > counts.legacy_body_deliveries
        or counts.v2_duplicate_deliveries > max(0, counts.v2_body_deliveries - 1)
        or counts.canonical_page_duplicates > max(0, counts.supplied_pages - 1)
    ):
        wire.invalid()
    failed, incomplete, omitted = (
        wire.integer(value[name], 0, scope.limits.maximum_issue_groups)
        for name in ("failed_issues", "incomplete_issues", "issues_omitted")
    )
    status: public._Status = "failed" if failed else "incomplete" if incomplete else "consistent"
    raw_issues = value["issues"]
    if (
        type(value["status"]) is not str
        or value["status"] != status
        or failed + incomplete > scope.limits.maximum_issue_groups
        or type(raw_issues) is not list
        or len(raw_issues) != min(scope.limits.maximum_issues, failed + incomplete)
        or omitted != failed + incomplete - len(raw_issues)
    ):
        wire.invalid()
    issues = tuple(_issue(i, scope, observations) for i in raw_issues)
    _collection_counts(witnesses, issues, omitted)
    keys = [(i.severity, i.component, i.code) for i in issues]
    shown_failed = sum(i.severity == "failed" for i in issues)
    if keys != sorted(keys) or shown_failed > failed or len(issues) - shown_failed > incomplete:
        wire.invalid()
    if counts.invalid_shape_deliveries and not failed:
        wire.invalid()
    relation = value["required_checkpoint_relation"]
    required = scope.enrollment.stream.required_checkpoint
    if (
        type(relation) is not str
        or relation
        not in ({"not-provided"} if required is None else {"included", "missing", "conflicting"})
        or (relation == "missing" and not incomplete)
        or (relation == "conflicting" and not failed)
    ):
        wire.invalid()
    if value["established_checks"] != list(
        ESTABLISHED_CHECKS if status == "consistent" else ()
    ) or value["unproved_checks"] != list(UNPROVED_CHECKS):
        wire.invalid()
    for w in witnesses:
        if (
            (not w.terminal_reached and not incomplete)
            or ((w.delete_observations or w.conflicting_locators) and not failed)
            or (w.unavailable_reads and not failed + incomplete)
        ):
            wire.invalid()
    usable = None
    if status == "consistent":
        if (
            scope.root_length is None
            or counts.invalid_shape_deliveries
            or (required is not None and relation != "included")
        ):
            wire.invalid()
        usable = _usable(value["usable"], scope, counts)
    elif value["usable"] is not None:
        wire.invalid()
    return public.HistoryReconciliationReport(
        status,
        value["scope"],
        witnesses,
        counts,
        issues,
        failed,
        incomplete,
        omitted,
        cast(public._Relation, relation),
        tuple(value["established_checks"]),
        UNPROVED_CHECKS,
        usable,
    )
