"""Dev/test fixture: seed a SoD-correct persona trio so the **full** document lifecycle
(author → approve → release, the S-web-5 journey) is demoable end-to-end.

SoD is non-overridable, so one user can never drive create→approve→release alone (SoD-1 forbids
self-approval; SoD-2's author-side block forbids self-release). This seeds three *distinct* users:

  - **Priya (author):** the 7 authoring content keys as SYSTEM overrides — create → submit-review.
  - **Ken (approver):** the seeded **Approver** role — it holds ``document.review``/``approve`` AND
    lands him in the approval task's **candidate pool** (which resolves by ROLE, not by override).
  - **Mara (releaser):** ``document.release`` (+reads) SYSTEM overrides — distinct from the author
    (SoD-2 author-side) and the approver (SoD-2 approver-side), so the cutover is unblocked.

Each persona is keyed by its Keycloak subject (the ``just seed-personas`` recipe creates the
matching login accounts and passes their subjects here). Like ``grant-role`` this is an explicit
operator fixture — **not** app logic (AZ-INV-6 / the setup model stay intact) — and idempotent
(re-run is a no-op; JIT-creates the ``app_user`` rows). Runs a sync engine; a one-shot script.

    easysynq seed-personas --author <sub> --approver <sub> --releaser <sub> [--org DEFAULT]
"""

from __future__ import annotations

import argparse
import uuid
from collections.abc import Sequence

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.models.app_user import AppUser, UserStatus
from ..db.models.authz_grant import PermissionOverride
from ..db.models.organization import Organization
from ..db.models.permission import Permission
from ..db.models.role import Role, RoleAssignment
from ..db.models.scope import Scope
from ..domain.authz.types import Effect, ScopeLevel

# The author needs the full authoring chain. NB the Author role alone can't map clauses
# (no document.manage_metadata) and Process Owner is PROCESS-scoped — so the proven path is the
# SYSTEM-override set (the "ride a SYSTEM override" pattern the slices use until owner-assignment).
# clauseMap.read lets the UI clause picker (GET /clauses) render for the author — the ≥1-clause-mapping
# submit gate is otherwise unusable from the browser (the S-web-5 live-smoke gap).
_AUTHOR_KEYS: tuple[str, ...] = (
    "document.read",
    "document.read_draft",
    "document.create",
    "document.checkout",
    "document.edit",
    "document.manage_metadata",
    "document.submit",
    "clauseMap.read",
)
# document.release is granted to no seeded role (the export/record precedent) → a SYSTEM override.
_RELEASER_KEYS: tuple[str, ...] = ("document.read", "document.read_draft", "document.release")


def _ensure_user(session: Session, org_id: uuid.UUID, subject: str, display_name: str) -> AppUser:
    user = session.scalar(select(AppUser).where(AppUser.keycloak_subject == subject))
    if user is None:
        user = AppUser(
            org_id=org_id,
            keycloak_subject=subject,
            display_name=display_name,
            status=UserStatus.ACTIVE,
        )
        session.add(user)
        session.flush()
    return user


def _ensure_system_overrides(session: Session, user: AppUser, keys: tuple[str, ...]) -> list[str]:
    """Grant each key as a SYSTEM ALLOW override (idempotent); returns newly-added keys."""
    added: list[str] = []
    for key in keys:
        perm = session.scalar(select(Permission).where(Permission.key == key))
        if perm is None:
            raise SystemExit(f"no permission named {key!r}")
        existing = session.scalar(
            select(PermissionOverride)
            .join(Scope, Scope.id == PermissionOverride.scope_id)
            .where(
                PermissionOverride.user_id == user.id,
                PermissionOverride.permission_id == perm.id,
                PermissionOverride.effect == Effect.ALLOW,
                Scope.level == ScopeLevel.SYSTEM,
            )
        )
        if existing is not None:
            continue
        scope = Scope(org_id=user.org_id, level=ScopeLevel.SYSTEM)
        session.add(scope)
        session.flush()
        session.add(
            PermissionOverride(
                org_id=user.org_id,
                user_id=user.id,
                permission_id=perm.id,
                effect=Effect.ALLOW,
                scope_id=scope.id,
            )
        )
        added.append(key)
    return added


def _ensure_role(session: Session, user: AppUser, org_id: uuid.UUID, role_name: str) -> bool:
    """Idempotently assign a seeded role (SYSTEM-bound). Returns True if newly assigned."""
    role = session.scalar(select(Role).where(Role.org_id == org_id, Role.name == role_name))
    if role is None:
        raise SystemExit(f"no role named {role_name!r}")
    existing = session.scalar(
        select(RoleAssignment).where(
            RoleAssignment.user_id == user.id, RoleAssignment.role_id == role.id
        )
    )
    if existing is not None:
        return False
    session.add(
        RoleAssignment(
            org_id=org_id, user_id=user.id, role_id=role.id, bound_scope={"level": "SYSTEM"}
        )
    )
    return True


def _resolve_org(session: Session, org_short_code: str) -> Organization:
    org = session.scalar(select(Organization).where(Organization.short_code == org_short_code))
    if org is not None:
        return org
    # Dev convenience: a single-org install (the common case) doesn't need the exact short code.
    orgs = session.scalars(select(Organization)).all()
    if len(orgs) == 1:
        print(f"(org {org_short_code!r} not found; using the only org {orgs[0].short_code!r})")
        return orgs[0]
    raise SystemExit(
        f"no organization with short_code={org_short_code!r}; have {[o.short_code for o in orgs]}"
    )


def seed_personas(
    author_sub: str,
    approver_sub: str,
    releaser_sub: str,
    org_short_code: str = "DEFAULT",
) -> list[str]:
    """Seed the author/approver/releaser grants (idempotent). Returns human-readable lines."""
    engine = create_engine(get_settings().sync_dsn)
    lines: list[str] = []
    try:
        with Session(engine) as session:
            org = _resolve_org(session, org_short_code)
            author = _ensure_user(session, org.id, author_sub, "Priya (Author)")
            a_added = _ensure_system_overrides(session, author, _AUTHOR_KEYS)
            lines.append(
                f"author   Priya  <{author_sub}>  authoring overrides: {a_added or 'present'}"
            )
            approver = _ensure_user(session, org.id, approver_sub, "Ken (Approver)")
            assigned = _ensure_role(session, approver, org.id, "Approver")
            lines.append(
                f"approver Ken    <{approver_sub}>  role Approver: "
                f"{'assigned' if assigned else 'present'}"
            )
            releaser = _ensure_user(session, org.id, releaser_sub, "Mara (Releaser)")
            r_added = _ensure_system_overrides(session, releaser, _RELEASER_KEYS)
            lines.append(
                f"releaser Mara   <{releaser_sub}>  release overrides: {r_added or 'present'}"
            )
            session.commit()
    finally:
        engine.dispose()
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="seed-personas",
        description="Seed SoD-correct author/approver/releaser persona grants (dev fixture).",
    )
    parser.add_argument("--author", required=True, help="Keycloak subject for the author (Priya)")
    parser.add_argument("--approver", required=True, help="Keycloak subject for the approver (Ken)")
    parser.add_argument(
        "--releaser", required=True, help="Keycloak subject for the releaser (Mara)"
    )
    parser.add_argument(
        "--org", default="DEFAULT", help="organization short_code (default DEFAULT)"
    )
    args = parser.parse_args(argv)
    for line in seed_personas(args.author, args.approver, args.releaser, args.org):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
