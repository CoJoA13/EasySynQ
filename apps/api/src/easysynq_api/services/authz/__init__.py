"""Authorization enforcement (PEP) — gathers grants, runs the PDP, audits, enforces.

See ``pep.require`` (the route dependency), ``pep.assert_can_grant`` (the two-tier guard,
R35), ``repository.gather_grants`` (DB → ``ResolvedGrant``), and ``audit`` (the S6 seam).
"""

from .audit import AuthzAuditEvent, AuthzAuditSink, CapturingAuthzAuditSink, LoggingAuthzAuditSink
from .pep import (
    assert_can_assign_role,
    assert_can_grant,
    enforce,
    evaluate,
    get_authz_audit_sink,
    invalidate_user_permissions,
    require,
)
from .repository import gather_grants, get_permission, granted_permission_keys

__all__ = [
    "AuthzAuditEvent",
    "AuthzAuditSink",
    "CapturingAuthzAuditSink",
    "LoggingAuthzAuditSink",
    "assert_can_assign_role",
    "assert_can_grant",
    "enforce",
    "evaluate",
    "gather_grants",
    "get_authz_audit_sink",
    "get_permission",
    "granted_permission_keys",
    "invalidate_user_permissions",
    "require",
]
