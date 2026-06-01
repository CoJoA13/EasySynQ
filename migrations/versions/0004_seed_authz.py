"""seed: the closed v1 permission catalog + the 8 starter roles (slice S2)

Idempotent data migration. Seeds the doc-07 permission catalog **verbatim** (register R5)
— 96 keys across 26 resources, each tagged ``is_system_domain`` (drives the two-tier grant
guard, R35), ``sig_hook`` (the Part-11 signature actions), ``sod_sensitive`` and
``finest_scope`` — and the 8 starter roles (doc 07 §4.2) with their ``role_grant`` bundles
for the DEFAULT org. The document set is the doc-18-C8 16-key set (no separate
``document.checkin``). Two load-bearing facts the S2 proofs depend on: the System
Administrator bundle holds **no** content permission, and the Approver bundle lacks
``document.edit``.

All inserts are ``ON CONFLICT DO NOTHING`` so re-running is safe. Downgrade removes only
the seeded rows.

Revision ID: 0004_seed_authz
Revises: 0003_authz
Create Date: 2026-06-01
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

revision: str = "0004_seed_authz"
down_revision: str | None = "0003_authz"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# --- the closed v1 catalog. Content rows: (key, sig_hook, sod_sensitive, finest_scope) ---
# All content rows are is_system_domain=False. Document set per doc 18 C8 (no `document.checkin`).
_CONTENT: tuple[tuple[str, bool, bool, str], ...] = (
    ("document.read", False, False, "ARTIFACT"),
    ("document.read_obsolete", False, False, "ARTIFACT"),
    ("document.read_draft", False, False, "ARTIFACT"),
    ("document.create", False, True, "DOC_CLASS"),
    ("document.checkout", False, False, "ARTIFACT"),
    ("document.edit", False, True, "ARTIFACT"),
    ("document.submit", False, True, "ARTIFACT"),
    ("document.review", False, True, "ARTIFACT"),
    ("document.approve", True, True, "ARTIFACT"),
    ("document.release", True, True, "ARTIFACT"),
    ("document.obsolete", True, True, "ARTIFACT"),
    ("document.delete_draft", False, False, "ARTIFACT"),
    ("document.manage_metadata", False, False, "ARTIFACT"),
    ("document.acknowledge", False, False, "ARTIFACT"),
    ("document.print_controlled", False, False, "ARTIFACT"),
    ("document.export", False, True, "ARTIFACT"),
    ("record.read", False, False, "ARTIFACT"),
    ("record.create", False, False, "DOC_CLASS"),
    ("record.correct", False, True, "ARTIFACT"),
    ("record.dispose", True, True, "DOC_CLASS"),
    ("record.set_retention", False, False, "DOC_CLASS"),
    ("record.export", False, True, "PROCESS"),
    ("changeRequest.create", False, True, "DOC_CLASS"),
    ("changeRequest.read", False, False, "PROCESS"),
    ("changeRequest.assess", False, True, "PROCESS"),
    ("changeRequest.route", False, False, "PROCESS"),
    ("changeRequest.approve", False, True, "PROCESS"),
    ("changeRequest.implement", False, True, "PROCESS"),
    ("changeRequest.close", False, False, "PROCESS"),
    ("audit.read", False, False, "PROCESS"),
    ("audit.create", False, True, "SYSTEM"),
    ("audit.plan", False, True, "SYSTEM"),
    ("audit.conduct", False, True, "PROCESS"),
    ("audit.close", False, True, "PROCESS"),
    ("finding.create", False, True, "PROCESS"),
    ("finding.read", False, False, "PROCESS"),
    ("finding.link_capa", False, False, "PROCESS"),
    ("ncr.create", False, True, "PROCESS"),
    ("ncr.read", False, False, "PROCESS"),
    ("ncr.record_correction", False, False, "PROCESS"),
    ("capa.create", False, True, "PROCESS"),
    ("capa.read", False, False, "PROCESS"),
    ("capa.update", False, True, "PROCESS"),
    ("capa.record_rca", False, False, "PROCESS"),
    ("capa.plan_action", False, True, "PROCESS"),
    ("capa.capture_effectiveness", False, False, "PROCESS"),
    ("capa.verify", True, True, "PROCESS"),
    ("capa.close", True, True, "PROCESS"),
    ("process.read", False, False, "PROCESS"),
    ("process.create", False, False, "SYSTEM"),
    ("process.manage", False, False, "PROCESS"),
    ("process.assign_owner", False, False, "PROCESS"),
    ("clauseMap.read", False, False, "SYSTEM"),
    ("clauseMap.map_artifact", False, False, "ARTIFACT"),
    ("mgmtReview.read", False, False, "SYSTEM"),
    ("mgmtReview.create", False, False, "SYSTEM"),
    ("mgmtReview.record_outputs", False, True, "SYSTEM"),
    ("objective.read", False, False, "PROCESS"),
    ("objective.manage", False, False, "PROCESS"),
    ("register.read", False, False, "PROCESS"),
    ("register.manage", False, False, "PROCESS"),
    ("kpi.read", False, False, "PROCESS"),
    ("kpi.record", False, False, "PROCESS"),
    ("report.read", False, False, "PROCESS"),
    ("report.evidence_pack.generate", False, True, "PROCESS"),
    ("report.export", False, True, "PROCESS"),
    ("report.compliance_checklist.read", False, False, "SYSTEM"),
)
# sig_hook flags above mark the two record/capa/document signature actions per doc 07 §11;
# note document.approve/release/obsolete + record.dispose + capa.verify/close are the only
# sig-hooks, but several of those are also is_system_domain — that flag is set per row above.

# System administration domains (is_system_domain=True; never sig-hook/sod-sensitive; SYSTEM).
_SYSTEM_KEYS: tuple[str, ...] = (
    "user.create",
    "user.read",
    "user.update",
    "user.deactivate",
    "user.role.assign",
    "user.role.revoke",
    "permission.grant",
    "permission.revoke",
    "role.create",
    "role.read",
    "role.update",
    "role.delete",
    "delegation.administer",
    "guest.administer",
    "framework.read",
    "config.read",
    "config.update",
    "storage.read",
    "storage.manage",
    "backup.read",
    "backup.run",
    "backup.configure",
    "restore.run",
    "import.execute",
    "import.review",
    "import.commit",
    "system.audit_log.read",
    "system.audit_log.export",
    "system.health.read",
)

# --- the 8 starter roles (doc 07 §4.2). Per-key scope_template override, else the default. ---
_SYSTEM_SCOPE: dict[str, Any] = {"level": "SYSTEM"}
_PROCESS_SCOPE: dict[str, Any] = {
    "level": "PROCESS",
    "selector": {"process_id": ":assignment_process"},
}
_FOLDER_SCOPE: dict[str, Any] = {
    "level": "FOLDER",
    "selector": {"folder_path": ":assigned_folder"},
}
_DOC_CLASS_SCOPE: dict[str, Any] = {
    "level": "DOC_CLASS",
    "selector": {"document_level": ":assigned_doc_class"},
}
# QMS Owner holds permission.grant CONTENT-tier: marked so the two-tier guard (R35) blocks
# it from granting a system-domain permission.
_CONTENT_GRANT_SCOPE: dict[str, Any] = {
    "level": "SYSTEM",
    "predicates": {"content_only": True},
}

_QMS_OWNER_KEYS: tuple[str, ...] = (
    "document.read",
    "document.read_draft",
    "document.read_obsolete",
    "record.read",
    "changeRequest.read",
    "audit.read",
    "finding.read",
    "ncr.read",
    "capa.read",
    "process.read",
    "clauseMap.read",
    "mgmtReview.read",
    "objective.read",
    "register.read",
    "kpi.read",
    "report.read",
    "report.compliance_checklist.read",
    "framework.read",
    "role.read",
    "objective.manage",
    "register.manage",
    "mgmtReview.create",
    "mgmtReview.record_outputs",
    "report.evidence_pack.generate",
    "report.export",
    "audit.plan",
    "capa.verify",
    "capa.close",
    "permission.grant",
)

_PROCESS_OWNER_KEYS: tuple[str, ...] = (
    "document.create",
    "document.checkout",
    "document.edit",
    "document.submit",
    "document.manage_metadata",
    "document.read",
    "document.read_draft",
    "record.create",
    "record.read",
    "capa.create",
    "capa.record_rca",
    "capa.plan_action",
    "capa.capture_effectiveness",
    "capa.read",
    "process.manage",
    "process.read",
    "kpi.record",
    "kpi.read",
    "changeRequest.create",
    "changeRequest.read",
    "finding.read",
    "audit.read",
    "objective.read",
    "register.read",
    "report.read",
)

_AUTHOR_KEYS: tuple[str, ...] = (
    "document.create",
    "document.checkout",
    "document.edit",
    "document.submit",
    "document.read",
    "document.read_draft",
    "record.create",
    "changeRequest.create",
)

_APPROVER_KEYS: tuple[str, ...] = (
    "document.review",
    "document.approve",
    "document.read",
    "document.read_draft",
    "changeRequest.approve",
    "changeRequest.read",
)

_AUDITOR_READ_KEYS: tuple[str, ...] = (
    "document.read",
    "document.read_obsolete",
    "document.read_draft",
    "record.read",
    "changeRequest.read",
    "ncr.read",
    "capa.read",
    "process.read",
    "clauseMap.read",
    "objective.read",
    "register.read",
    "kpi.read",
    "report.read",
    "audit.read",
    "audit.create",
    "finding.read",
)
_AUDITOR_PROCESS_KEYS: tuple[str, ...] = (
    "audit.conduct",
    "audit.close",
    "finding.create",
    "finding.link_capa",
)

_EMPLOYEE_KEYS: tuple[str, ...] = (
    "document.read",
    "document.print_controlled",
    "document.acknowledge",
    "record.read",
    "process.read",
)

_GUEST_KEYS: tuple[str, ...] = ("document.read", "record.read", "report.read")
_GUEST_SCOPE: dict[str, Any] = {"level": "ARTIFACT", "predicates": {"read_only": True}}


def _role(
    name: str, reserved: bool, description: str, grants: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    return {
        "name": name,
        "is_reserved": reserved,
        "description": description,
        "grants": grants,
    }


def _roles() -> list[dict[str, Any]]:
    sysadmin = {k: _SYSTEM_SCOPE for k in _SYSTEM_KEYS}

    qms_owner: dict[str, dict[str, Any]] = {}
    for k in _QMS_OWNER_KEYS:
        qms_owner[k] = (
            _CONTENT_GRANT_SCOPE if k == "permission.grant" else _SYSTEM_SCOPE
        )

    auditor: dict[str, dict[str, Any]] = {k: _SYSTEM_SCOPE for k in _AUDITOR_READ_KEYS}
    auditor.update({k: _PROCESS_SCOPE for k in _AUDITOR_PROCESS_KEYS})

    return [
        _role(
            "System Administrator",
            True,
            "Runs the system end-to-end. Holds no QMS-content authority by default (AZ-INV-6).",
            sysadmin,
        ),
        _role(
            "QMS Owner",
            True,
            "Governs the QMS: org-wide read, QMS config, audits/CAPA closure, evidence packs, "
            "and delegated content-only permission granting (R35).",
            qms_owner,
        ),
        _role(
            "Process Owner",
            False,
            "Owns a process: authors/manages its documents, records and CAPAs.",
            {k: _PROCESS_SCOPE for k in _PROCESS_OWNER_KEYS},
        ),
        _role(
            "Author",
            False,
            "Authors documents within a folder. No approve/release.",
            {k: _FOLDER_SCOPE for k in _AUTHOR_KEYS},
        ),
        _role(
            "Approver",
            False,
            "Reviews/approves documents of a class. No edit/submit (SoD).",
            {k: _DOC_CLASS_SCOPE for k in _APPROVER_KEYS},
        ),
        _role(
            "Internal Auditor",
            False,
            "Broad read plus audit/finding authority. Hard-excluded from edit/approve/release.",
            auditor,
        ),
        _role(
            "Employee (Read-only)",
            False,
            "Reads and acknowledges controlled documents in their area.",
            {k: _PROCESS_SCOPE for k in _EMPLOYEE_KEYS},
        ),
        _role(
            "External Auditor (Guest)",
            False,
            "Time-boxed, read-only access within a bound evidence pack.",
            {k: _GUEST_SCOPE for k in _GUEST_KEYS},
        ),
    ]


def _permission_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, sig_hook, sod_sensitive, finest in _CONTENT:
        # All content permissions are is_system_domain=False — including the six sig-hook
        # actions (document.approve/release/obsolete, record.dispose, capa.verify/close),
        # which a content-tier granter (QMS Owner) may still grant (R35).
        resource, _, action = key.partition(".")
        rows.append(
            {
                "key": key,
                "resource": resource,
                "action": action,
                "is_system_domain": False,
                "sod_sensitive": sod_sensitive,
                "sig_hook": sig_hook,
                "finest_scope": finest,
            }
        )
    for key in _SYSTEM_KEYS:
        resource, _, action = key.partition(".")
        rows.append(
            {
                "key": key,
                "resource": resource,
                "action": action,
                "is_system_domain": True,
                "sod_sensitive": False,
                "sig_hook": False,
                "finest_scope": "SYSTEM",
            }
        )
    return rows


def upgrade() -> None:
    bind = op.get_bind()

    permission_t = sa.table(
        "permission",
        sa.column("key", sa.Text),
        sa.column("resource", sa.Text),
        sa.column("action", sa.Text),
        sa.column("is_system_domain", sa.Boolean),
        sa.column("sod_sensitive", sa.Boolean),
        sa.column("sig_hook", sa.Boolean),
        sa.column(
            "finest_scope", postgresql.ENUM(name="scope_level", create_type=False)
        ),
    )
    role_t = sa.table(
        "role",
        sa.column("org_id", postgresql.UUID(as_uuid=True)),
        sa.column("name", sa.Text),
        sa.column("description", sa.Text),
        sa.column("is_reserved", sa.Boolean),
    )
    role_grant_t = sa.table(
        "role_grant",
        sa.column("org_id", postgresql.UUID(as_uuid=True)),
        sa.column("role_id", postgresql.UUID(as_uuid=True)),
        sa.column("permission_id", postgresql.UUID(as_uuid=True)),
        sa.column("scope_template", postgresql.JSONB),
    )

    bind.execute(
        pg_insert(permission_t)
        .values(_permission_rows())
        .on_conflict_do_nothing(index_elements=["key"])
    )

    org_id = bind.execute(
        sa.text("SELECT id FROM organization WHERE short_code = 'DEFAULT'")
    ).scalar_one()

    roles = _roles()
    bind.execute(
        pg_insert(role_t)
        .values(
            [
                {
                    "org_id": org_id,
                    "name": r["name"],
                    "description": r["description"],
                    "is_reserved": r["is_reserved"],
                }
                for r in roles
            ]
        )
        .on_conflict_do_nothing(index_elements=["org_id", "name"])
    )

    perm_ids = {
        key: pid for key, pid in bind.execute(sa.text("SELECT key, id FROM permission"))
    }
    role_ids = {
        name: rid
        for name, rid in bind.execute(
            sa.text("SELECT name, id FROM role WHERE org_id = :org"), {"org": org_id}
        )
    }

    grant_rows: list[dict[str, Any]] = []
    for r in roles:
        rid = role_ids[r["name"]]
        for key, scope_template in r["grants"].items():
            grant_rows.append(
                {
                    "org_id": org_id,
                    "role_id": rid,
                    "permission_id": perm_ids[key],
                    "scope_template": scope_template,
                }
            )
    bind.execute(
        pg_insert(role_grant_t)
        .values(grant_rows)
        .on_conflict_do_nothing(index_elements=["org_id", "role_id", "permission_id"])
    )


def downgrade() -> None:
    bind = op.get_bind()
    role_names = [r["name"] for r in _roles()]
    keys = [row["key"] for row in _permission_rows()]

    del_grants = sa.text(
        "DELETE FROM role_grant WHERE role_id IN (SELECT id FROM role WHERE name IN :names)"
    ).bindparams(sa.bindparam("names", expanding=True))
    bind.execute(del_grants, {"names": role_names})

    del_roles = sa.text("DELETE FROM role WHERE name IN :names").bindparams(
        sa.bindparam("names", expanding=True)
    )
    bind.execute(del_roles, {"names": role_names})

    del_perms = sa.text("DELETE FROM permission WHERE key IN :keys").bindparams(
        sa.bindparam("keys", expanding=True)
    )
    bind.execute(del_perms, {"keys": keys})
