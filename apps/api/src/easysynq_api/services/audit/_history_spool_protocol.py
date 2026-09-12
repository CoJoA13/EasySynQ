"""Fixed version-one storage messages; never accepts SQL, paths, or readers."""

from __future__ import annotations

import dataclasses
import json
import re
from typing import Any, NoReturn
from uuid import UUID

from .history_collection import (
    HistoryCollectionError,
    HistoryCollectionIssue,
    HistoryCollectionLimits,
    HistoryWitnessSummary,
    _IssueCode,
    _IssueSeverity,
)

FRAME_MAX = 131_072
CHUNK_MAX = 65_536
PAGE_MAX = 16_777_216
ISOLATED_CODES = frozenset(
    {
        "RUNTIME_UNSUPPORTED",
        "WORKER_START_FAILED",
        "WORKER_FAILED",
        "PROTOCOL_INVALID",
        "OUTPUT_LIMIT",
        "DEADLINE_EXCEEDED",
    }
)
LIST_CODES = ISOLATED_CODES | frozenset(
    {
        "PROVIDER_FAILURE",
        "TRANSPORT_FAILURE",
        "RESPONSE_INVALID",
        "BODY_LIMIT",
        "PAGE_LIMIT",
        "SCOPE_MISMATCH",
        "CURSOR_INVALID",
        "LENGTH_MISMATCH",
        "ROUTING_REJECTED",
    }
)
VERSION_CODES = ISOLATED_CODES | frozenset(
    {
        "PROVIDER_FAILURE",
        "TRANSPORT_FAILURE",
        "RESPONSE_INVALID",
        "VERSION_MISMATCH",
        "DELETE_MARKER",
        "LENGTH_MISMATCH",
        "BODY_LIMIT",
        "ROUTING_REJECTED",
    }
)
ISSUES: dict[_IssueCode, _IssueSeverity] = {
    "DELETE_OBSERVATION": "failed",
    "LOCATOR_CONFLICT": "failed",
    "LIST_UNAVAILABLE": "incomplete",
    "VERSION_UNAVAILABLE": "incomplete",
    "INELIGIBLE_LOCATOR": "incomplete",
    "CURSOR_CYCLE": "incomplete",
}


@dataclasses.dataclass(frozen=True, slots=True)
class _SpoolWitness:
    witness_id: UUID
    namespace_hash: str
    bucket: str


@dataclasses.dataclass(frozen=True, slots=True)
class _PageTicket:
    page_id: int
    witness_index: int
    page_index: int


@dataclasses.dataclass(frozen=True, slots=True)
class _PageAdmission:
    first_ordinal: int
    version_count: int
    delete_count: int


@dataclasses.dataclass(frozen=True, slots=True)
class _SpoolSummary:
    witnesses: tuple[HistoryWitnessSummary, ...]
    issues: tuple[HistoryCollectionIssue, ...]
    failed_issues: int
    incomplete_issues: int
    issues_omitted: int
    admitted_total_bytes: int


def invalid() -> NoReturn:
    raise HistoryCollectionError("PROTOCOL_INVALID") from None


def issue_code(value: object) -> _IssueCode:
    match value:
        case "DELETE_OBSERVATION":
            return "DELETE_OBSERVATION"
        case "LOCATOR_CONFLICT":
            return "LOCATOR_CONFLICT"
        case "LIST_UNAVAILABLE":
            return "LIST_UNAVAILABLE"
        case "VERSION_UNAVAILABLE":
            return "VERSION_UNAVAILABLE"
        case "INELIGIBLE_LOCATOR":
            return "INELIGIBLE_LOCATOR"
        case "CURSOR_CYCLE":
            return "CURSOR_CYCLE"
        case _:
            invalid()


def fields(value: Any, expected: set[str]) -> dict[str, Any]:
    if type(value) is not dict or value.keys() != expected:
        invalid()
    return value


def integer(value: Any, low: int = 0, high: int = 1_073_741_824) -> int:
    if type(value) is not int or not low <= value <= high:
        invalid()
    return value


def label(value: Any, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if type(value) is not str:
        invalid()
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError:
        invalid()
    if not 1 <= size <= 4_096:
        invalid()
    return value


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            invalid()
        output[key] = value
    return output


def _constant(_text: str) -> NoReturn:
    invalid()


def _depth(value: Any, level: int = 0) -> None:
    if level > 6:
        invalid()
    if type(value) is dict:
        for item in value.values():
            _depth(item, level + 1)
    elif type(value) is list:
        for item in value:
            _depth(item, level + 1)
    elif type(value) not in (str, int, bool, type(None)):
        invalid()


def metadata(payload: bytes) -> dict[str, Any]:
    if not 1 < len(payload) <= FRAME_MAX or payload[:1] != b"J":
        invalid()
    try:
        value = json.loads(
            payload[1:].decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant
        )
        _depth(value)
    except (ValueError, UnicodeError, RecursionError):
        invalid()
    if type(value) is not dict:
        invalid()
    return value


def encode(value: dict[str, Any]) -> bytes:
    payload = b"J" + json.dumps(
        value, ensure_ascii=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    if len(payload) > FRAME_MAX:
        invalid()
    return payload


def frame(payload: bytes) -> bytes:
    if not 0 < len(payload) <= FRAME_MAX:
        invalid()
    return len(payload).to_bytes(4, "big") + payload


def chunk(request_id: int, offset: int, body: bytes) -> bytes:
    if not 0 < len(body) <= CHUNK_MAX:
        invalid()
    return b"B" + request_id.to_bytes(8, "big") + offset.to_bytes(4, "big") + body


def decode_chunk(payload: bytes, request_id: int, offset: int, remaining: int) -> bytes:
    if (
        not 13 < len(payload) <= 13 + CHUNK_MAX
        or payload[:1] != b"B"
        or int.from_bytes(payload[1:9], "big") != request_id
        or int.from_bytes(payload[9:13], "big") != offset
        or len(payload) - 13 > remaining
    ):
        invalid()
    return payload[13:]


def ticket(value: Any) -> _PageTicket:
    value = fields(value, {"page_id", "witness_index", "page_index"})
    return _PageTicket(
        integer(value["page_id"], 1, 4_096),
        integer(value["witness_index"], 0, 3),
        integer(value["page_index"], 0, 4_095),
    )


def decode_init(
    value: dict[str, Any],
) -> tuple[UUID, tuple[_SpoolWitness, ...], HistoryCollectionLimits]:
    fields(value, {"op", "id", "version", "org_id", "witnesses", "limits"})
    if type(value["version"]) is not int or value["version"] != 1:
        invalid()
    try:
        org = UUID(value["org_id"])
        if str(org) != value["org_id"]:
            invalid()
        scopes = value["witnesses"]
        if type(scopes) is not list or not 1 <= len(scopes) <= 4:
            invalid()
        witnesses = []
        for scope in scopes:
            fields(scope, {"witness_id", "namespace_hash", "bucket"})
            witness_id = UUID(scope["witness_id"])
            digest = scope["namespace_hash"]
            bucket = label(scope["bucket"])
            if bucket is None:
                invalid()
            if (
                str(witness_id) != scope["witness_id"]
                or type(digest) is not str
                or re.fullmatch("[0-9a-f]{64}", digest) is None
            ):
                invalid()
            witnesses.append(_SpoolWitness(witness_id, digest, bucket))
        ids = [item.witness_id.bytes for item in witnesses]
        if ids != sorted(set(ids)):
            invalid()
    except (ValueError, TypeError, AttributeError):
        invalid()
    ranges = {
        "maximum_pages": (1, 4_096),
        "maximum_observations": (1, 100_000),
        "maximum_total_bytes": (1, 1_073_741_824),
        "maximum_spool_bytes": (65_536, 1_073_741_824),
        "maximum_wall_seconds": (1, 86_400),
        "maximum_issues": (1, 32),
    }
    raw_limits = fields(value["limits"], set(ranges))
    for name, bounds in ranges.items():
        integer(raw_limits[name], *bounds)
    if raw_limits["maximum_spool_bytes"] % 4_096:
        invalid()
    return org, tuple(witnesses), HistoryCollectionLimits(**raw_limits)


def summary_payload(summary: _SpoolSummary) -> dict[str, Any]:
    value = dataclasses.asdict(summary)
    for witness in value["witnesses"]:
        witness["witness_id"] = str(witness["witness_id"])
    for issue in value["issues"]:
        issue["witness_id"] = str(issue["witness_id"])
    return value


def decode_summary(
    value: Any, scopes: tuple[_SpoolWitness, ...], limits: HistoryCollectionLimits
) -> _SpoolSummary:
    fields(value, {field.name for field in dataclasses.fields(_SpoolSummary)})
    raw_witnesses = value["witnesses"]
    raw_issues = value["issues"]
    if (
        type(raw_witnesses) is not list
        or len(raw_witnesses) != len(scopes)
        or type(raw_issues) is not list
        or len(raw_issues) > limits.maximum_issues
    ):
        invalid()
    witnesses = []
    for raw, scope in zip(raw_witnesses, scopes, strict=True):
        fields(raw, {field.name for field in dataclasses.fields(HistoryWitnessSummary)})
        if (
            raw["witness_id"] != str(scope.witness_id)
            or raw["namespace_hash"] != scope.namespace_hash
            or type(raw["terminal_reached"]) is not bool
        ):
            invalid()
        for name in raw.keys() - {"witness_id", "namespace_hash", "terminal_reached"}:
            integer(raw[name], 0, 100_000)
        if raw["version_observations"] != raw["successful_reads"] + raw["unavailable_reads"]:
            invalid()
        if (
            not 1 <= raw["page_attempts"] <= limits.maximum_pages
            or raw["admitted_pages"] > raw["page_attempts"]
            or raw["page_attempts"] - raw["admitted_pages"] > 1
            or (raw["terminal_reached"] and not raw["admitted_pages"])
            or (raw["terminal_reached"] and raw["admitted_pages"] != raw["page_attempts"])
            or raw["version_observations"] + raw["delete_observations"]
            > raw["admitted_pages"] * 1_000
            or raw["duplicate_body_deliveries"] > raw["successful_reads"]
            or raw["conflicting_locators"] > raw["successful_reads"] // 2
        ):
            invalid()
        witnesses.append(HistoryWitnessSummary(**{**raw, "witness_id": scope.witness_id}))
    if (
        sum(w.page_attempts for w in witnesses) > limits.maximum_pages
        or sum(w.version_observations + w.delete_observations for w in witnesses)
        > limits.maximum_observations
    ):
        invalid()
    issues = []
    for raw in raw_issues:
        fields(raw, {"code", "severity", "witness_id", "count", "observation_ordinals"})
        code = issue_code(raw["code"])
        if raw["severity"] != ISSUES[code]:
            invalid()
        issue_scope = next((s for s in scopes if str(s.witness_id) == raw["witness_id"]), None)
        if issue_scope is None:
            invalid()
        integer(raw["count"], 1, 100_000)
        ordinals = raw["observation_ordinals"]
        if type(ordinals) is not list or len(ordinals) > 4:
            invalid()
        for ordinal in ordinals:
            integer(ordinal, 1, limits.maximum_observations)
        issues.append(
            HistoryCollectionIssue(
                code, ISSUES[code], issue_scope.witness_id, raw["count"], tuple(ordinals)
            )
        )
    keys = [(issue.witness_id.bytes, issue.code) for issue in issues]
    if keys != sorted(set(keys)):
        invalid()
    for name in ("failed_issues", "incomplete_issues", "issues_omitted"):
        integer(value[name], 0, 24)
    integer(value["admitted_total_bytes"], 0, limits.maximum_total_bytes)
    if value["failed_issues"] + value["incomplete_issues"] != len(issues) + value["issues_omitted"]:
        invalid()
    shown_failed = sum(issue.severity == "failed" for issue in issues)
    shown_incomplete = len(issues) - shown_failed
    if (
        not shown_failed <= value["failed_issues"] <= 2 * len(scopes)
        or not shown_incomplete <= value["incomplete_issues"] <= 4 * len(scopes)
        or len(issues)
        != min(limits.maximum_issues, value["failed_issues"] + value["incomplete_issues"])
    ):
        invalid()
    for witness in witnesses:
        own_issues = {i.code: i for i in issues if i.witness_id == witness.witness_id}
        counts = {code: issue.count for code, issue in own_issues.items()}
        total_groups = value["failed_issues"] + value["incomplete_issues"]
        if witness.unavailable_reads and not total_groups:
            invalid()
        deleted = counts.get("DELETE_OBSERVATION", 0)
        if "DELETE_OBSERVATION" in counts and not (
            witness.delete_observations
            <= deleted
            <= witness.delete_observations + witness.unavailable_reads
        ):
            invalid()
        known_unavailable = (
            counts.get("VERSION_UNAVAILABLE", 0)
            + counts.get("INELIGIBLE_LOCATOR", 0)
            + max(0, deleted - witness.delete_observations)
        )
        if known_unavailable > witness.unavailable_reads:
            invalid()
        if not value["issues_omitted"] and (
            deleted < witness.delete_observations
            or known_unavailable != witness.unavailable_reads
            or counts.get("LOCATOR_CONFLICT", 0) != witness.conflicting_locators
            or counts.get("LIST_UNAVAILABLE", 0) != witness.page_attempts - witness.admitted_pages
        ):
            invalid()
        gap = any(code in own_issues for code in ("LIST_UNAVAILABLE", "CURSOR_CYCLE"))
        if (witness.terminal_reached and gap) or (
            not witness.terminal_reached and not gap and not value["issues_omitted"]
        ):
            invalid()
        if not witness.terminal_reached and not value["incomplete_issues"]:
            invalid()
        if (witness.delete_observations or witness.conflicting_locators) and not value[
            "failed_issues"
        ]:
            invalid()
    return _SpoolSummary(
        tuple(witnesses),
        tuple(issues),
        value["failed_issues"],
        value["incomplete_issues"],
        value["issues_omitted"],
        value["admitted_total_bytes"],
    )
