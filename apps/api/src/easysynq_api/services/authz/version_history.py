"""State-aware authorization for immutable Document version history (issue #406).

``document.read`` remains the live Document metadata gate and is enforced by each route before
these helpers run.  This module owns the additional per-version boundary:

* Effective needs no specialized key;
* Draft / InReview / Approved need ``document.read_draft``;
* Superseded / Obsolete need ``document.read_obsolete``.

Lists filter unauthorized rows.  Detail/download/diff surfaces enforce the selected row(s) and
emit the normal PEP audit decision.  Every specialized decision evaluates the full canonical
Document scope with only lifecycle/version identity replaced by the immutable version's values.
"""

from __future__ import annotations

import dataclasses
import datetime
from collections.abc import Iterable, Sequence

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._vault_enums import VersionState
from ...db.models.app_user import AppUser
from ...db.models.document_version import DocumentVersion
from ...domain.authz import RequestContext, ResourceContext, authorize
from ..common.client_ip import client_ip
from .audit import AuthzAuditSink
from .pep import enforce
from .repository import gather_grants

_PERMISSION_BY_STATE: dict[VersionState, str | None] = {
    VersionState.Draft: "document.read_draft",
    VersionState.InReview: "document.read_draft",
    VersionState.Approved: "document.read_draft",
    VersionState.Effective: None,
    VersionState.Superseded: "document.read_obsolete",
    VersionState.Obsolete: "document.read_obsolete",
}


def required_version_read_permission(state: VersionState) -> str | None:
    """Return the specialized read key for one immutable version state."""
    return _PERMISSION_BY_STATE[state]


def version_resource(
    document_resource: ResourceContext,
    version: DocumentVersion,
) -> ResourceContext:
    """Project one version onto the canonical Document scope without dropping any selector.

    Permission scopes remain Document-scoped (the ARTIFACT id is the Document id), while lifecycle
    predicates must see the immutable version's state rather than the mutable headline state.
    Version/author identity is also carried for a complete version-relative context.
    """
    return dataclasses.replace(
        document_resource,
        lifecycle_state=version.version_state.value,
        version_id=str(version.id),
        author_user_id=str(version.author_user_id),
    )


def _request_context(request: Request, caller: AppUser) -> RequestContext:
    return RequestContext(
        now=datetime.datetime.now(datetime.UTC),
        source_ip=client_ip(request),
        actor_user_id=str(caller.id),
    )


async def filter_readable_versions(
    session: AsyncSession,
    request: Request,
    caller: AppUser,
    document_resource: ResourceContext,
    versions: Sequence[DocumentVersion],
) -> list[DocumentVersion]:
    """Return the authorized subset of a mixed-state version collection.

    The route's audited ``document.read`` dependency has already admitted the live Document.  Like
    the Library's per-row filter, specialized row decisions use the pure PDP to avoid one audit row
    per hidden/visible history entry.
    """
    keys = {
        key
        for version in versions
        if (key := required_version_read_permission(version.version_state)) is not None
    }
    grants_by_key = {
        key: await gather_grants(session, caller.id, caller.org_id, key) for key in sorted(keys)
    }
    context = _request_context(request, caller)
    visible: list[DocumentVersion] = []
    for version in versions:
        key = required_version_read_permission(version.version_state)
        if (
            key is None
            or authorize(
                grants_by_key[key],
                key,
                version_resource(document_resource, version),
                context,
            ).allow
        ):
            visible.append(version)
    return visible


async def can_read_any_version_state(
    session: AsyncSession,
    request: Request,
    caller: AppUser,
    document_resource: ResourceContext,
    permission_key: str,
) -> bool:
    """Return whether one specialized key admits any immutable state assigned to that key.

    This is the authz-only answer used by the Document detail ``read_draft`` affordance.  It
    intentionally probes the candidate immutable states rather than the mutable Document headline
    or the existence of a particular history row: capabilities describe what the caller may do,
    while the history collection separately describes what rows currently exist.  Every probe
    preserves the canonical Document selectors and the live request context.
    """
    states = tuple(
        state
        for state, required_key in _PERMISSION_BY_STATE.items()
        if required_key == permission_key
    )
    if not states:
        return False
    grants = await gather_grants(session, caller.id, caller.org_id, permission_key)
    context = _request_context(request, caller)
    return any(
        authorize(
            grants,
            permission_key,
            dataclasses.replace(
                document_resource,
                lifecycle_state=state.value,
                version_id=None,
                author_user_id=None,
            ),
            context,
        ).allow
        for state in states
    )


async def enforce_version_reads(
    session: AsyncSession,
    sink: AuthzAuditSink,
    request: Request,
    caller: AppUser,
    document_resource: ResourceContext,
    versions: Iterable[DocumentVersion],
) -> None:
    """Enforce every distinct version side needed by a detail/download/diff operation."""
    seen: set[object] = set()
    for version in versions:
        if version.id in seen:
            continue
        seen.add(version.id)
        key = required_version_read_permission(version.version_state)
        if key is not None:
            await enforce(
                session,
                sink,
                request,
                caller,
                key,
                version_resource(document_resource, version),
            )
