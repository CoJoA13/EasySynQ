"""Host-only first-run setup operations (slice S8a and ADR 0005).

``mint-bootstrap`` generates a high-entropy single-use secret, stores its **salted hash** + TTL on
``system_config`` (the plaintext is shown ONCE here, never persisted), and prints it. The operator
opens ``/setup`` in the browser and uses it to create the first System Administrator.

``release-administrator-blocker`` is the exceptional pre-operational recovery path for an unrelated
System Administrator assignment that blocks that browser flow. It changes only the named user's
role assignment in PostgreSQL; it never contacts Keycloak or changes identity/user state. Because it
bypasses application audit, every use requires an independent incident/change record.

Run it inside the api container (where the DB is reachable):

    easysynq setup mint-bootstrap [--ttl-hours 24]
    easysynq setup release-administrator-blocker --subject <keycloak-subject> [--org CODE]

Uses a sync engine on the owner DSN — a one-shot script, not coupled to the app's event loop.
While setup remains ``UNINITIALIZED``, re-running replaces only the secret proof and expiry so an
expired pending administrator claim can be recovered. Advanced setup refuses reminting.
"""

from __future__ import annotations

import argparse
import datetime
from collections.abc import Sequence

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.models.app_user import AppUser
from ..db.models.organization import Organization
from ..db.models.role import Role, RoleAssignment
from ..db.models.system_config import SetupState, SystemConfig
from ..services.authz.admin_guard import SYSTEM_ADMIN_ROLE, lock_admin_set_sync
from ..services.setup.bootstrap import mint_secret


def mint_bootstrap(org_short_code: str = "DEFAULT", ttl_hours: int = 24) -> str:
    """Mint + persist a fresh bootstrap secret; return the plaintext secret (shown once)."""
    engine = create_engine(get_settings().sync_dsn)
    try:
        with Session(engine) as session:
            org = session.scalar(
                select(Organization).where(Organization.short_code == org_short_code)
            )
            if org is None:
                raise SystemExit(f"no organization with short_code={org_short_code!r}")
            cfg = session.scalar(
                select(SystemConfig)
                .where(SystemConfig.org_id == org.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if cfg is None:
                raise SystemExit("system_config is not initialized")
            if cfg.setup_state is not SetupState.UNINITIALIZED:
                raise SystemExit("bootstrap can only be minted while setup is UNINITIALIZED")
            secret, stored_hash = mint_secret()
            cfg.bootstrap_secret_hash = stored_hash
            cfg.bootstrap_expires_at = datetime.datetime.now(datetime.UTC) + datetime.timedelta(
                hours=ttl_hours
            )
            session.commit()
            return secret
    finally:
        engine.dispose()


def _release_result(result: str, subject: str, org_short_code: str) -> str:
    return f"{result} for subject {subject!r} in organization {org_short_code!r}"


def release_administrator_blocker(
    subject: str,
    org_short_code: str = "DEFAULT",
) -> str:
    """Remove one unrelated pre-operational System Administrator assignment.

    The singleton setup row is locked before the shared per-organization administrator-set lock.
    Every refusal and database failure explicitly rolls back; an absent user or assignment is a
    no-write, safely repeatable result.
    """
    engine = create_engine(get_settings().sync_dsn)
    try:
        with Session(engine) as session:
            try:
                org = session.scalar(
                    select(Organization).where(Organization.short_code == org_short_code)
                )
                if org is None:
                    raise SystemExit(_release_result("release refused", subject, org_short_code))

                cfg = session.scalar(
                    select(SystemConfig)
                    .where(SystemConfig.org_id == org.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if cfg is None or cfg.setup_state is not SetupState.UNINITIALIZED:
                    raise SystemExit(_release_result("release refused", subject, org_short_code))

                lock_admin_set_sync(session, org.id)
                user = session.scalar(
                    select(AppUser).where(
                        AppUser.org_id == org.id,
                        AppUser.keycloak_subject == subject,
                    )
                )
                if user is None:
                    session.rollback()
                    return _release_result("nothing released", subject, org_short_code)
                if cfg.bootstrap_admin_user_id == user.id:
                    raise SystemExit(_release_result("release refused", subject, org_short_code))

                role = session.scalar(
                    select(Role).where(
                        Role.org_id == org.id,
                        Role.name == SYSTEM_ADMIN_ROLE,
                    )
                )
                if role is None:
                    raise SystemExit(_release_result("release failed", subject, org_short_code))

                assignments = session.scalars(
                    select(RoleAssignment).where(
                        RoleAssignment.org_id == org.id,
                        RoleAssignment.user_id == user.id,
                        RoleAssignment.role_id == role.id,
                    )
                ).all()
                if not assignments:
                    session.rollback()
                    return _release_result("nothing released", subject, org_short_code)

                for assignment in assignments:
                    session.delete(assignment)
                session.flush()
                session.commit()
                return _release_result("released administrator blocker", subject, org_short_code)
            except SystemExit:
                session.rollback()
                raise
            except Exception:  # noqa: BLE001 - host recovery must redact every database failure
                session.rollback()
                raise SystemExit(
                    _release_result("release failed", subject, org_short_code)
                ) from None
    finally:
        engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="setup", description="Host-only first-run setup and recovery operations."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    mint = sub.add_parser("mint-bootstrap", help="mint + print a one-time bootstrap secret")
    mint.add_argument("--org", default="DEFAULT", help="organization short_code (default DEFAULT)")
    mint.add_argument(
        "--ttl-hours", type=int, default=24, help="secret validity in hours (default 24)"
    )
    release = sub.add_parser(
        "release-administrator-blocker",
        help="remove one unrelated pre-operational System Administrator assignment",
    )
    release.add_argument("--subject", required=True, help="exact Keycloak subject to release")
    release.add_argument(
        "--org", default="DEFAULT", help="organization short_code (default DEFAULT)"
    )
    args = parser.parse_args(argv)

    if args.command == "mint-bootstrap":
        secret = mint_bootstrap(args.org, args.ttl_hours)
        print("Bootstrap secret minted (valid for", args.ttl_hours, "hours, single-use).")
        print("Open /setup and create the first administrator with this secret:")
        print()
        print(f"    {secret}")
        print()
        print("This is shown ONCE and is not stored in plaintext. Re-run to mint a new one.")
        return 0
    if args.command == "release-administrator-blocker":
        print(release_administrator_blocker(args.subject, args.org))
        print("Record this host recovery in an independent incident/change record.")
        return 0
    return 2  # pragma: no cover - argparse 'required=True' makes this unreachable


if __name__ == "__main__":
    raise SystemExit(main())
