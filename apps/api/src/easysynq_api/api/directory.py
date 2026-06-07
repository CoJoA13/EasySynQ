"""A minimal user-name directory — read-only (slice S-web-2).

``GET /directory/users`` resolves ``owner_user_id`` (a bare UUID on the document list/detail rows)
to a display name so a client can render a friendly **Owner** column / **Owner** facet. It returns
**only** ``{id, display_name}`` for **ACTIVE, non-guest** users — deliberately a far narrower
projection than the admin roster ``GET /users`` (which is ``user.read``-gated and exposes e-mail /
keycloak_subject / status / roles). Those PII fields are **never** exposed here.

Gating mirrors ``GET /documents``: **authentication only** (any org member). The list endpoint
already hands every authenticated caller the ``owner_user_id`` UUIDs of the rows they may read;
resolving those to colleague display names within a single-org QMS is the same information class.
A ``require("document.read")`` at SYSTEM scope would wrongly exclude an ordinary reader, so
authentication is the boundary. Display names only, ACTIVE non-guest only — minimal disclosure.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_user
from ..db.models.app_user import AppUser, UserStatus
from ..db.session import get_session

router = APIRouter(prefix="/api/v1", tags=["directory"])


@router.get("/directory/users")
async def list_directory_users(
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    """ACTIVE non-guest org members as ``{id, display_name}``, ordered by display name."""
    users = (
        (
            await session.execute(
                select(AppUser)
                .where(
                    AppUser.org_id == caller.org_id,
                    AppUser.status == UserStatus.ACTIVE,
                    AppUser.is_guest.is_(False),
                )
                .order_by(AppUser.display_name)
            )
        )
        .scalars()
        .all()
    )
    return [{"id": str(u.id), "display_name": u.display_name} for u in users]
