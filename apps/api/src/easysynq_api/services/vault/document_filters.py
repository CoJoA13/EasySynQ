"""Shared effective-date filter materialization for Library and report consumers."""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.sql.elements import ColumnElement

from ...db.models.document_version import DocumentVersion
from ...db.models.documented_information import DocumentedInformation
from ...problems import ProblemException
from ..common.org_clock import current_org_tz

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_effective_from_bound(
    value: str,
    *,
    org_tz: datetime.tzinfo | None = None,
    end_of_day: bool = False,
) -> datetime.datetime:
    """Parse an effective-date filter bound.

    A canonical bare date is a calendar date in ``org_tz`` (R8/R56). Lower bounds use local
    midnight; date-only upper bounds use the final representable microsecond of that same local
    day. Date-times retain their existing behavior: an explicit offset is respected, while an
    offset-less date-time keeps the historical UTC default.
    """
    try:
        if _ISO_DATE_RE.fullmatch(value):
            local_date = datetime.date.fromisoformat(value)
            local_time = datetime.time.max if end_of_day else datetime.time.min
            return datetime.datetime.combine(
                local_date,
                local_time,
                tzinfo=org_tz or current_org_tz(),
            )
        ts = datetime.datetime.fromisoformat(value)
    except ValueError as exc:
        raise ProblemException(
            status=422, code="validation_error", title="Invalid effective_from filter value"
        ) from exc
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=datetime.UTC)


def effective_from_filter_condition(
    op: str,
    value: str,
    *,
    org_tz: datetime.tzinfo | None = None,
) -> ColumnElement[bool]:
    """Build the shared current-effective-version bound for one ``gte`` or ``lte`` filter."""
    ts = parse_effective_from_bound(
        value,
        org_tz=org_tz,
        end_of_day=op == "lte" and _ISO_DATE_RE.fullmatch(value) is not None,
    )
    bound = (
        DocumentVersion.effective_from >= ts
        if op == "gte"
        else DocumentVersion.effective_from <= ts
    )
    return (
        select(1)
        .select_from(DocumentVersion)
        .where(
            DocumentVersion.id == DocumentedInformation.current_effective_version_id,
            DocumentVersion.effective_from.is_not(None),
            bound,
        )
        .exists()
    )


@dataclass(frozen=True)
class DeferredEffectiveFromFilter:
    """A raw report bound that must be materialized inside the report's DB snapshot."""

    op: str
    value: str

    def materialize(self, org_tz: datetime.tzinfo) -> ColumnElement[bool]:
        return effective_from_filter_condition(self.op, self.value, org_tz=org_tz)
