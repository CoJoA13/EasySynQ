"""Authorization enforcement (PEP) — gathers grants, runs the PDP, audits, enforces.

See ``pep.require`` (the route dependency), ``pep.assert_can_grant`` (the two-tier guard,
R35), ``repository.gather_grants`` (DB → ``ResolvedGrant``), and ``audit`` (the S6 seam).
"""

from .admin_guard import (
    SYSTEM_ADMIN_ROLE,
    disable_removes_last_admin,
    lock_admin_set,
    lock_admin_set_sync,
    revoke_removes_last_admin,
)
from .audit import AuthzAuditEvent, AuthzAuditSink, CapturingAuthzAuditSink, LoggingAuthzAuditSink
from .pep import (
    assert_can_assign_role,
    assert_can_delete_override,
    assert_can_grant,
    assert_can_reset_credential,
    assert_can_revoke_role,
    enforce,
    evaluate,
    evaluate_with_context,
    get_authz_audit_sink,
    invalidate_user_permissions,
    require,
)
from .repository import gather_grants, get_permission, granted_permission_keys

__all__ = [
    "SYSTEM_ADMIN_ROLE",
    "AuthzAuditEvent",
    "AuthzAuditSink",
    "CapturingAuthzAuditSink",
    "LoggingAuthzAuditSink",
    "assert_can_assign_role",
    "assert_can_delete_override",
    "assert_can_grant",
    "assert_can_reset_credential",
    "assert_can_revoke_role",
    "disable_removes_last_admin",
    "enforce",
    "evaluate",
    "evaluate_with_context",
    "gather_grants",
    "get_authz_audit_sink",
    "get_permission",
    "granted_permission_keys",
    "invalidate_user_permissions",
    "lock_admin_set",
    "lock_admin_set_sync",
    "require",
    "revoke_removes_last_admin",
]
