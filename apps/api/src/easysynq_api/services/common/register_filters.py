"""Server-side filtering for the CAPA / Risk / Audit registers.

Those three listings took **no query parameters at all**. They loaded a fixed newest-first window
(``REGISTER_SCAN_CAP``) and reported ``truncated``, so once an org passed the cap its OLDEST rows
were unreachable through the API and the SPA alike. For a CAPA register that is ISO-9001 audit
evidence, "you cannot retrieve your oldest entries" is the wrong answer.

The filters are pushed into SQL **before** the cap, which is the whole point: narrowing has to move
the window, not just trim what came back inside it.

This deliberately does NOT reuse ``api/documents.py``'s parser. That one is welded to the document
register — a version join for ``effective_from``, ``clause_refs has``, boolean fields — and it has
pinned tests. Per the repo's own doctrine, a right-sized sibling is safer than refactoring a
test-pinned core in place. The wire grammar and the ``unknown_filter`` error are identical, so the
two are consistent from a client's point of view (doc 15 §3.2).
"""

from __future__ import annotations

import datetime
import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from fastapi import Request
from sqlalchemy import ColumnElement
from sqlalchemy.orm.attributes import InstrumentedAttribute

from ...problems import ProblemException

_FILTER_KEY_RE = re.compile(r"^filter\[([^\]]+)\]\[([^\]]+)\]$")


@dataclass(frozen=True)
class RegisterFilter:
    """One filterable field: its column, the operators it accepts, and how to parse a value."""

    column: InstrumentedAttribute[Any]
    ops: frozenset[str]
    parse: Callable[[str], Any]


def parse_enum(values: type[Enum]) -> Callable[[str], Any]:
    """Parse a value against an enum, rejecting anything outside it with a 422."""

    def _parse(raw: str) -> Any:
        try:
            return values(raw)
        except ValueError as exc:
            raise ProblemException(
                status=422, code="validation_error", title=f"Not a valid value: {raw}"
            ) from exc

    return _parse


def parse_uuid(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise ProblemException(
            status=422, code="validation_error", title=f"Not a valid id: {raw}"
        ) from exc


def parse_date_boundary(raw: str) -> datetime.datetime:
    """Parse an ISO date (or datetime) into a UTC-aware datetime.

    A bare ``YYYY-MM-DD`` becomes midnight UTC, so ``gte`` includes the whole named day. A naive
    datetime is read as UTC rather than rejected — a comparison against a timezone-aware column
    would otherwise raise deep inside the driver.
    """
    try:
        parsed = datetime.datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ProblemException(
            status=422, code="validation_error", title=f"Not an ISO-8601 date: {raw}"
        ) from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=datetime.UTC)
    return parsed


def parse_register_filters(
    request: Request, allowed: Mapping[str, RegisterFilter]
) -> list[ColumnElement[bool]]:
    """Translate ``filter[field][op]`` query params into SQL conditions.

    A key matching the grammar but outside ``allowed`` is a 400 ``unknown_filter`` — the same
    contract ``GET /documents`` uses, so a client cannot silently believe an unsupported facet
    narrowed the result. A key that does not match the grammar at all is ignored, exactly as there.
    Repeated keys are ANDed.
    """
    conditions: list[ColumnElement[bool]] = []
    for raw_key, value in request.query_params.multi_items():
        match = _FILTER_KEY_RE.match(raw_key)
        if match is None:
            continue
        field, op = match.group(1), match.group(2)
        spec = allowed.get(field)
        if spec is None or op not in spec.ops:
            raise ProblemException(
                status=400, code="unknown_filter", title=f"Unknown filter: {raw_key}"
            )
        parsed = spec.parse(value)
        if op == "eq":
            conditions.append(spec.column == parsed)
        elif op == "gte":
            conditions.append(spec.column >= parsed)
        else:  # "lte" — the only remaining operator any register declares
            conditions.append(spec.column <= parsed)
    return conditions
