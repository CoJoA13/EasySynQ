"""Native-PG enum bindings for the authz cluster.

``scope_level`` / ``grant_effect`` values are owned by the pure domain layer
(``domain.authz.types``) so the PDP and the schema can never drift. The SoD enums are
DB-only in MVP — the SoD *gate* lands in S5 (doc 18 §7). All four types are created by
the Alembic migration; these bindings reference them by name with ``create_type=False``.
"""

from __future__ import annotations

import enum

from sqlalchemy import Enum as SAEnum

from ...domain.authz.types import Effect, ScopeLevel


class SodTargetBinding(enum.Enum):
    SAME_VERSION = "SAME_VERSION"
    SAME_DOCUMENT = "SAME_DOCUMENT"
    SAME_PROCESS = "SAME_PROCESS"
    SAME_CAPA = "SAME_CAPA"


class SodSeverity(enum.Enum):
    HARD_DENY = "HARD_DENY"
    FLAG_AND_REQUIRE_REASON = "FLAG_AND_REQUIRE_REASON"


scope_level_enum = SAEnum(
    ScopeLevel,
    name="scope_level",
    values_callable=lambda e: [m.value for m in e],
    create_type=False,
)
grant_effect_enum = SAEnum(
    Effect, name="grant_effect", values_callable=lambda e: [m.value for m in e], create_type=False
)
sod_target_binding_enum = SAEnum(
    SodTargetBinding,
    name="sod_target_binding",
    values_callable=lambda e: [m.value for m in e],
    create_type=False,
)
sod_severity_enum = SAEnum(
    SodSeverity,
    name="sod_severity",
    values_callable=lambda e: [m.value for m in e],
    create_type=False,
)
