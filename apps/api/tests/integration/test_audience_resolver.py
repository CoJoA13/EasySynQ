"""S-notify-5a: resolve_document_readers — read-scope audience resolver integration tests.

Tests run against a real migrated PG16 via testcontainers (testcontainers are session-scoped;
the shared DB persists across tests). Covers the four cases from the brief:

1. A SYSTEM document.read role-grant holder IS in the audience.
2. A DENY permission_override beats a role ALLOW (deny-wins, the register-R3 backstop).
3. A user with no document.read grant is NOT in the audience (deny-by-default).
4. An INACTIVE user (status RETIRED) with a role grant is NOT in the audience.
5. The resolver does not self-suppress — it returns ALL readers incl. the actor (the fan-out
   worker subtracts the actor later, per the brief §7).

Seeding uses the DEFAULT org (the seeded one — so the seeded 'Employee (Read-only)' role is
available). No new org is created, so test_restore's scalar_one() is never threatened.
FK-ordered teardown: permission_override → role_assignment → scope → document → users.
"""

from __future__ import annotations

import datetime
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select

from easysynq_api.db.models._vault_enums import DocumentKind
from easysynq_api.db.models.app_user import AppUser, UserStatus
from easysynq_api.db.models.authz_grant import PermissionOverride
from easysynq_api.db.models.documented_information import DocumentedInformation
from easysynq_api.db.models.framework import Framework
from easysynq_api.db.models.organization import Organization
from easysynq_api.db.models.permission import Permission
from easysynq_api.db.models.role import Role, RoleAssignment
from easysynq_api.db.models.scope import Scope
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.domain.authz.types import Effect, ScopeLevel

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Authz fixture — four test users + one document in the DEFAULT org
# ---------------------------------------------------------------------------


@pytest.fixture
async def authz_fixture(app_under_test: object) -> SimpleNamespace:
    """Seed four users + a document in the seeded default org.

    Users:
      - system_reader:  Employee (Read-only) role grant at SYSTEM → document.read ALLOW.
      - denied_reader:  Same role grant PLUS a document.read DENY override at SYSTEM.
      - no_grant_user:  No role, no grant — deny-by-default.
      - inactive_reader: Same role grant as system_reader, but status=RETIRED.

    All created rows are torn down in FK-order at the end of the test. No new org is created
    so test_restore's scalar_one() is never threatened (the S-notify-4 lesson).
    """
    salt = uuid.uuid4().hex[:10]

    role_assignment_ids: list[uuid.UUID] = []
    override_ids: list[uuid.UUID] = []
    scope_ids: list[uuid.UUID] = []
    user_ids: list[uuid.UUID] = []
    doc_id: uuid.UUID
    org_id: uuid.UUID
    system_reader_id: uuid.UUID
    denied_reader_id: uuid.UUID
    no_grant_user_id: uuid.UUID
    inactive_reader_id: uuid.UUID

    async with get_sessionmaker()() as s:
        # Use the seeded default org so the seeded roles are available.
        org_id = (
            await s.execute(select(Organization.id).order_by(Organization.created_at).limit(1))
        ).scalar_one()

        framework_id = (await s.execute(select(Framework.id).limit(1))).scalar_one()

        # --- four test users (unique keycloak_subject per salt) ---
        sr = AppUser(
            org_id=org_id,
            keycloak_subject=f"kc-aud-sr-{salt}",
            display_name=f"AudienceTest SystemReader {salt}",
            status=UserStatus.ACTIVE,
        )
        dr = AppUser(
            org_id=org_id,
            keycloak_subject=f"kc-aud-dr-{salt}",
            display_name=f"AudienceTest DeniedReader {salt}",
            status=UserStatus.ACTIVE,
        )
        ng = AppUser(
            org_id=org_id,
            keycloak_subject=f"kc-aud-ng-{salt}",
            display_name=f"AudienceTest NoGrant {salt}",
            status=UserStatus.ACTIVE,
        )
        ir = AppUser(
            org_id=org_id,
            keycloak_subject=f"kc-aud-ir-{salt}",
            display_name=f"AudienceTest Inactive {salt}",
            status=UserStatus.RETIRED,
        )
        s.add_all([sr, dr, ng, ir])
        await s.flush()
        system_reader_id = sr.id
        denied_reader_id = dr.id
        no_grant_user_id = ng.id
        inactive_reader_id = ir.id
        user_ids.extend([system_reader_id, denied_reader_id, no_grant_user_id, inactive_reader_id])

        # --- a minimal document in the default org ---
        doc = DocumentedInformation(
            org_id=org_id,
            framework_id=framework_id,
            kind=DocumentKind.DOCUMENT,
            identifier=f"AUD-DOC-{salt[:8]}",
            title=f"Audience Resolver Test Doc {salt}",
            owner_user_id=sr.id,
            created_by=sr.id,
        )
        s.add(doc)
        await s.flush()
        doc_id = doc.id

        # --- 'Employee (Read-only)' role — holds document.read at SYSTEM ---
        role = (
            await s.execute(
                select(Role).where(Role.org_id == org_id, Role.name == "Employee (Read-only)")
            )
        ).scalar_one()

        # Assign the role to system_reader, denied_reader, and inactive_reader
        for uid in (system_reader_id, denied_reader_id, inactive_reader_id):
            ra = RoleAssignment(
                org_id=org_id,
                user_id=uid,
                role_id=role.id,
                bound_scope={"level": "SYSTEM"},
            )
            s.add(ra)
            await s.flush()
            role_assignment_ids.append(ra.id)

        # --- DENY override for denied_reader: document.read DENY at SYSTEM ---
        perm = (
            await s.execute(select(Permission).where(Permission.key == "document.read"))
        ).scalar_one()
        deny_scope = Scope(org_id=org_id, level=ScopeLevel.SYSTEM)
        s.add(deny_scope)
        await s.flush()
        scope_ids.append(deny_scope.id)
        deny_override = PermissionOverride(
            org_id=org_id,
            user_id=denied_reader_id,
            permission_id=perm.id,
            effect=Effect.DENY,
            scope_id=deny_scope.id,
        )
        s.add(deny_override)
        await s.flush()
        override_ids.append(deny_override.id)

        await s.commit()

    fixture = SimpleNamespace(
        org_id=org_id,
        doc_id=doc_id,
        system_reader_id=system_reader_id,
        denied_reader_id=denied_reader_id,
        no_grant_user_id=no_grant_user_id,
        inactive_reader_id=inactive_reader_id,
        _role_assignment_ids=role_assignment_ids,
        _override_ids=override_ids,
        _scope_ids=scope_ids,
        _user_ids=user_ids,
        _doc_id=doc_id,
    )

    yield fixture

    # --- FK-ordered teardown ---
    async with get_sessionmaker()() as s:
        # 1. permission_override rows (FK → scope, user, permission)
        if fixture._override_ids:
            await s.execute(
                delete(PermissionOverride).where(PermissionOverride.id.in_(fixture._override_ids))
            )
        # 2. role_assignment rows (FK → role, user, org)
        if fixture._role_assignment_ids:
            await s.execute(
                delete(RoleAssignment).where(RoleAssignment.id.in_(fixture._role_assignment_ids))
            )
        # 3. scope rows (FK target from permission_override — delete after override)
        if fixture._scope_ids:
            await s.execute(delete(Scope).where(Scope.id.in_(fixture._scope_ids)))
        # 4. document (FK → org, user; no child rows)
        await s.execute(
            delete(DocumentedInformation).where(DocumentedInformation.id == fixture._doc_id)
        )
        # 5. users (FK → org; must follow role_assignment/override deletion)
        if fixture._user_ids:
            await s.execute(delete(AppUser).where(AppUser.id.in_(fixture._user_ids)))
        await s.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_system_reader_included_and_deny_override_excluded(
    app_under_test: object, authz_fixture: SimpleNamespace
) -> None:
    """A SYSTEM document.read holder is in the audience; a SYSTEM-scope DENY override beats it."""
    from easysynq_api.db.session import get_sessionmaker
    from easysynq_api.services.authz.audience import resolve_document_readers

    now = datetime.datetime.now(datetime.UTC)
    async with get_sessionmaker()() as session:
        readers = await resolve_document_readers(
            session, authz_fixture.org_id, authz_fixture.doc_id, now=now
        )
    assert authz_fixture.system_reader_id in readers  # role ALLOW @ SYSTEM
    assert authz_fixture.denied_reader_id not in readers  # DENY override beats the ALLOW
    assert authz_fixture.no_grant_user_id not in readers  # deny-by-default
    assert authz_fixture.inactive_reader_id not in readers  # LOCKED/DISABLED/RETIRED excluded


async def test_actor_self_suppression_is_caller_side(
    app_under_test: object, authz_fixture: SimpleNamespace
) -> None:
    """resolve_document_readers returns ALL readers including the actor.

    The fan-out worker subtracts the actor (Task 7); the resolver itself is audience-complete.
    """
    from easysynq_api.db.session import get_sessionmaker
    from easysynq_api.services.authz.audience import resolve_document_readers

    now = datetime.datetime.now(datetime.UTC)
    async with get_sessionmaker()() as session:
        readers = await resolve_document_readers(
            session, authz_fixture.org_id, authz_fixture.doc_id, now=now
        )
    # The resolver does not self-suppress (that is the fan-out's job — Task 7); it returns the actor
    # if the actor can read. Assert the resolver is audience-complete.
    assert authz_fixture.system_reader_id in readers
