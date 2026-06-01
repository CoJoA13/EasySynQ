"""Dev/ops bootstrap: assign a seeded role to a user by Keycloak subject.

A **pre-S8 stopgap** so the authz admin API is usable before the first-run wizard (slice
S8) grants the first System Administrator. This is an explicit operator action — like
recreating the demo Keycloak user (CLAUDE.md) — **not** an app-logic auto-grant, so
AZ-INV-6 and the setup-wizard model stay intact.

Run it inside the api container (where the DB is reachable):

    easysynq grant-role <keycloak-subject> ["Role Name"]

Idempotent: re-running is a no-op. JIT-creates the ``app_user`` row if absent. Uses a sync
engine — it is a one-shot script, not coupled to the app's event loop.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.models.app_user import AppUser, UserStatus
from ..db.models.organization import Organization
from ..db.models.role import Role, RoleAssignment


def grant_role(
    subject: str,
    role_name: str = "System Administrator",
    org_short_code: str = "DEFAULT",
    bound_scope: dict[str, Any] | None = None,
) -> str:
    """Assign ``role_name`` to the user with Keycloak ``subject`` (creating the user row if
    needed). Returns a human-readable result line. Idempotent."""
    engine = create_engine(get_settings().sync_dsn)
    try:
        with Session(engine) as session:
            org = session.scalar(
                select(Organization).where(Organization.short_code == org_short_code)
            )
            if org is None:
                raise SystemExit(f"no organization with short_code={org_short_code!r}")

            # Validate the role before touching the user row (fail fast; nothing to roll back).
            role = session.scalar(select(Role).where(Role.org_id == org.id, Role.name == role_name))
            if role is None:
                available = sorted(
                    session.scalars(select(Role.name).where(Role.org_id == org.id)).all()
                )
                raise SystemExit(f"no role named {role_name!r}; available: {', '.join(available)}")

            user = session.scalar(select(AppUser).where(AppUser.keycloak_subject == subject))
            created = user is None
            if user is None:
                user = AppUser(
                    org_id=org.id,
                    keycloak_subject=subject,
                    display_name=subject,
                    status=UserStatus.ACTIVE,
                )
                session.add(user)
                session.flush()

            existing = session.scalar(
                select(RoleAssignment).where(
                    RoleAssignment.user_id == user.id, RoleAssignment.role_id == role.id
                )
            )
            suffix = " (user JIT-created)" if created else ""
            if existing is not None:
                session.commit()
                return f"already assigned: {subject} -> {role_name}{suffix}"

            session.add(
                RoleAssignment(
                    org_id=org.id, user_id=user.id, role_id=role.id, bound_scope=bound_scope
                )
            )
            session.commit()
            return f"assigned: {subject} -> {role_name}{suffix}"
    finally:
        engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="grant_role",
        description="Assign a seeded role to a Keycloak subject (pre-S8 bootstrap).",
    )
    parser.add_argument("--subject", required=True, help="Keycloak subject (the JWT 'sub' claim)")
    parser.add_argument(
        "--role", default="System Administrator", help='role name (default "System Administrator")'
    )
    parser.add_argument(
        "--org", default="DEFAULT", help="organization short_code (default DEFAULT)"
    )
    parser.add_argument(
        "--bound-scope",
        default=None,
        help="optional JSON scope binding for non-system roles, e.g. "
        '\'{"level":"FOLDER","selector":{"folder_path":"SOPs.Purchasing"}}\'',
    )
    args = parser.parse_args(argv)
    bound = json.loads(args.bound_scope) if args.bound_scope else None
    print(grant_role(args.subject, args.role, args.org, bound))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
