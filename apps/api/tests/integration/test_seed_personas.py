"""The dev seed-personas CLI grants a SoD-correct author/approver/releaser trio (the S-web-5
fixture): the author holds the full authoring chain at SYSTEM (incl. manage_metadata — the gap the
Author role has), the approver holds the Approver ROLE (candidate-pool membership resolves by role),
the releaser holds document.release; and no one can do everything (SoD is non-overridable)."""

from __future__ import annotations

import datetime
import uuid

import pytest
from sqlalchemy import func, select

from easysynq_api.cli.seed_personas import seed_personas
from easysynq_api.db.models.app_user import AppUser
from easysynq_api.db.models.authz_grant import PermissionOverride
from easysynq_api.db.models.organization import Organization
from easysynq_api.db.models.permission import Permission
from easysynq_api.db.models.role import Role, RoleAssignment
from easysynq_api.db.models.scope import Scope
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.domain.authz import RequestContext, ResourceContext, authorize
from easysynq_api.domain.authz.types import ScopeLevel
from easysynq_api.services.authz.repository import gather_grants

pytestmark = pytest.mark.integration


async def _allows(user_id: uuid.UUID, org_id: uuid.UUID, key: str) -> bool:
    async with get_sessionmaker()() as s:
        grants = await gather_grants(s, user_id, org_id, key)
    ctx = RequestContext(now=datetime.datetime.now(datetime.UTC))
    return authorize(grants, key, ResourceContext.system(), ctx).allow


async def _org_short_code() -> str:
    async with get_sessionmaker()() as s:
        return (
            await s.execute(
                select(Organization.short_code).order_by(Organization.created_at).limit(1)
            )
        ).scalar_one()


async def _user(subject: str) -> AppUser:
    async with get_sessionmaker()() as s:
        return (
            await s.execute(select(AppUser).where(AppUser.keycloak_subject == subject))
        ).scalar_one()


async def test_seed_personas_grants_sod_trio(app_under_test: object) -> None:
    salt = uuid.uuid4().hex[:10]
    author_sub = f"kc-priya-{salt}"
    approver_sub = f"kc-ken-{salt}"
    releaser_sub = f"kc-mara-{salt}"
    short = await _org_short_code()

    seed_personas(author_sub, approver_sub, releaser_sub, short)

    author = await _user(author_sub)
    approver = await _user(approver_sub)
    releaser = await _user(releaser_sub)

    # author: the full authoring chain at SYSTEM — incl. manage_metadata (the Author-role gap) — but
    # NOT approve (SoD-1 aside, the author simply isn't granted it → can't self-approve).
    assert await _allows(author.id, author.org_id, "document.create")
    assert await _allows(author.id, author.org_id, "document.manage_metadata")
    assert await _allows(author.id, author.org_id, "document.submit")
    assert not await _allows(author.id, author.org_id, "document.approve")
    assert not await _allows(author.id, author.org_id, "document.release")

    # approver: the seeded Approver ROLE (the approval task's candidate pool resolves by role).
    async with get_sessionmaker()() as s:
        roles = (
            (
                await s.execute(
                    select(Role.name)
                    .join(RoleAssignment, RoleAssignment.role_id == Role.id)
                    .where(RoleAssignment.user_id == approver.id)
                )
            )
            .scalars()
            .all()
        )
    assert "Approver" in roles

    # releaser: document.release at SYSTEM — and distinct from the author (SoD-2 author-side).
    assert await _allows(releaser.id, releaser.org_id, "document.release")


async def test_seed_personas_is_idempotent(app_under_test: object) -> None:
    salt = uuid.uuid4().hex[:10]
    author_sub = f"kc-priya-{salt}"
    short = await _org_short_code()

    seed_personas(author_sub, f"kc-ken-{salt}", f"kc-mara-{salt}", short)
    seed_personas(author_sub, f"kc-ken-{salt}", f"kc-mara-{salt}", short)  # re-run is a no-op

    author = await _user(author_sub)
    async with get_sessionmaker()() as s:
        perm_id = (
            await s.execute(select(Permission.id).where(Permission.key == "document.create"))
        ).scalar_one()
        count = (
            await s.execute(
                select(func.count())
                .select_from(PermissionOverride)
                .join(Scope, Scope.id == PermissionOverride.scope_id)
                .where(
                    PermissionOverride.user_id == author.id,
                    PermissionOverride.permission_id == perm_id,
                    Scope.level == ScopeLevel.SYSTEM,
                )
            )
        ).scalar_one()
    assert count == 1  # not duplicated on the second run
