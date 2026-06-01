"""authz core: enums + reduced doc-14 RBAC/ABAC model (slice S2)

Creates the hybrid RBAC + ABAC schema that the deny-wins PDP/PEP enforce: the global
permission catalog (reference data, no org_id — key is globally unique per doc 14 §15.1),
org-scoped roles + their two join tables (role_grant / role_assignment per the doc-14 ERD),
reusable ABAC scopes, per-user overrides, and the SoD-constraint table (seeded/enforced in
S5). delegation + guest_grant are deferred to v1.x (doc 18 §11 D-2).

All FKs ON DELETE RESTRICT (no hard delete; doc 18 §4). The seed lands in 0004_seed_authz.

Revision ID: 0003_authz
Revises: 0002_app_user
Create Date: 2026-06-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_authz"
down_revision: str | None = "0002_app_user"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ENUMS: dict[str, tuple[str, ...]] = {
    # Value order matches the domain enums' definition order (db/models/_authz_enums.py).
    "scope_level": (
        "SYSTEM",
        "FRAMEWORK",
        "PROCESS",
        "FOLDER",
        "DOC_CLASS",
        "ARTIFACT",
    ),
    "grant_effect": ("ALLOW", "DENY"),
    "sod_target_binding": (
        "SAME_VERSION",
        "SAME_DOCUMENT",
        "SAME_PROCESS",
        "SAME_CAPA",
    ),
    "sod_severity": ("HARD_DENY", "FLAG_AND_REQUIRE_REASON"),
}


def _org_fk(table: str, column: str = "org_id") -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        ["organization.id"],
        name=f"fk_{table}_{column}_organization",
        ondelete="RESTRICT",
    )


def upgrade() -> None:
    bind = op.get_bind()
    for name, values in _ENUMS.items():
        postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)

    scope_level = postgresql.ENUM(name="scope_level", create_type=False)
    grant_effect = postgresql.ENUM(name="grant_effect", create_type=False)
    sod_target_binding = postgresql.ENUM(name="sod_target_binding", create_type=False)
    sod_severity = postgresql.ENUM(name="sod_severity", create_type=False)

    # permission — GLOBAL reference catalog (no org_id; key globally unique, doc 14 §15.1).
    op.create_table(
        "permission",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("resource", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column(
            "is_system_domain", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column(
            "sod_sensitive", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column("sig_hook", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("finest_scope", scope_level, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_permission"),
        sa.UniqueConstraint("key", name="uq_permission_key"),
    )

    # role — org-scoped convenience bundle.
    op.create_table(
        "role",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "is_reserved", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        _org_fk("role"),
        sa.PrimaryKeyConstraint("id", name="pk_role"),
        sa.UniqueConstraint("org_id", "name", name="uq_role_org_id_name"),
    )

    # role_grant — (role -> permission) bundle entry with a parameterized scope template.
    op.create_table(
        "role_grant",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("permission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "scope_template", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        _org_fk("role_grant"),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["role.id"],
            name="fk_role_grant_role_id_role",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["permission_id"],
            ["permission.id"],
            name="fk_role_grant_permission_id_permission",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_role_grant"),
        sa.UniqueConstraint(
            "org_id",
            "role_id",
            "permission_id",
            name="uq_role_grant_org_id_role_id_permission_id",
        ),
    )

    # role_assignment — (user -> role) with a concrete bound scope.
    op.create_table(
        "role_assignment",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "bound_scope", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        _org_fk("role_assignment"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name="fk_role_assignment_user_id_app_user",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["role.id"],
            name="fk_role_assignment_role_id_role",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_role_assignment"),
    )

    # scope — reusable ABAC boundary (framework_id is a reserved multi-standard hook).
    op.create_table(
        "scope",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("framework_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("level", scope_level, nullable=False),
        sa.Column("selector", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("predicates", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        _org_fk("scope"),
        sa.PrimaryKeyConstraint("id", name="pk_scope"),
    )

    # permission_override — direct per-user ALLOW/DENY (deny-wins); scope_id always concrete.
    op.create_table(
        "permission_override",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("permission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("effect", grant_effect, nullable=False),
        sa.Column("scope_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("predicates", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "require_reason", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        _org_fk("permission_override"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name="fk_permission_override_user_id_app_user",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["permission_id"],
            ["permission.id"],
            name="fk_permission_override_permission_id_permission",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["scope_id"],
            ["scope.id"],
            name="fk_permission_override_scope_id_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["app_user.id"],
            name="fk_permission_override_created_by_app_user",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_permission_override"),
    )
    op.create_index(
        "ix_permission_override_user_id_permission_id_scope_id",
        "permission_override",
        ["user_id", "permission_id", "scope_id"],
    )

    # sod_constraint — table only; seeded + enforced in S5.
    op.create_table(
        "sod_constraint",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("duty_a", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("duty_b", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "relation",
            sa.Text(),
            server_default="SAME_PRINCIPAL_FORBIDDEN",
            nullable=False,
        ),
        sa.Column("target_binding", sod_target_binding, nullable=False),
        sa.Column("severity", sod_severity, nullable=False),
        sa.Column(
            "org_overridable", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        _org_fk("sod_constraint"),
        sa.PrimaryKeyConstraint("id", name="pk_sod_constraint"),
    )


def downgrade() -> None:
    op.drop_table("sod_constraint")
    op.drop_index(
        "ix_permission_override_user_id_permission_id_scope_id",
        table_name="permission_override",
    )
    op.drop_table("permission_override")
    op.drop_table("scope")
    op.drop_table("role_assignment")
    op.drop_table("role_grant")
    op.drop_table("role")
    op.drop_table("permission")
    for name in _ENUMS:
        op.execute(f"DROP TYPE IF EXISTS {name}")
