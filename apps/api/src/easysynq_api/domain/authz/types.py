"""Framework-free value types for the authorization PDP.

These mirror the data-model authz cluster (doc 14 §3) but carry *no* SQLAlchemy or
FastAPI dependency, so the PDP (pdp.py) is a pure function the acceptance proofs can
drive with hand-built inputs. The PEP (services.authz) builds ``ResolvedGrant``s from
``role_assignment``+``role_grant`` and ``permission_override``+``scope`` rows.
"""

from __future__ import annotations

import dataclasses
import enum
from collections.abc import Mapping
from typing import Any


class Effect(enum.Enum):
    """A grant either ALLOWs or DENYs. Deny always wins (register R3 / AZ-INV-2)."""

    ALLOW = "ALLOW"
    DENY = "DENY"


class ScopeLevel(enum.Enum):
    """The ABAC scope level a grant applies at (doc 07 §5.1). ``FRAMEWORK`` is a
    reserved multi-standard hook (doc 18 §10) — present in MVP, all rows iso9001."""

    SYSTEM = "SYSTEM"
    FRAMEWORK = "FRAMEWORK"
    PROCESS = "PROCESS"
    FOLDER = "FOLDER"
    DOC_CLASS = "DOC_CLASS"
    ARTIFACT = "ARTIFACT"


# Specificity rank: higher = more specific. Used ONLY to break ALLOW-vs-ALLOW ties
# (doc 07 §5.2 / §6.3). It NEVER rescues an ALLOW over a DENY — deny wins first.
_SPECIFICITY: dict[ScopeLevel, int] = {
    ScopeLevel.ARTIFACT: 5,
    ScopeLevel.DOC_CLASS: 4,
    ScopeLevel.FOLDER: 3,
    ScopeLevel.PROCESS: 2,
    ScopeLevel.FRAMEWORK: 1,
    ScopeLevel.SYSTEM: 0,
}


def specificity(level: ScopeLevel) -> int:
    """Specificity rank for ALLOW-vs-ALLOW tie-breaking (higher = narrower scope)."""
    return _SPECIFICITY[level]


@dataclasses.dataclass(frozen=True, slots=True)
class ResolvedGrant:
    """One grant reaching a principal, already resolved to a concrete scope.

    ``source`` is human-readable provenance (e.g. ``role:Author`` / ``user_override``)
    surfaced in ``/effective-permissions`` and the audit hook. ``is_override`` breaks
    same-level ALLOW ties in favor of a per-user override (doc 07 §6.3 step 6).
    """

    effect: Effect
    level: ScopeLevel
    selector: Mapping[str, Any]
    predicates: Mapping[str, Any]
    source: str
    is_override: bool = False


@dataclasses.dataclass(frozen=True, slots=True)
class ResourceContext:
    """Attributes of the target a grant's scope is matched against (doc 07 §6.1 ``X``).

    A SYSTEM-scoped resource (e.g. the role/user admin surface) leaves every field at
    its default — only SYSTEM grants match it, since the narrower levels need an
    ``artifact_id`` / ``process_ids`` / ``folder_path`` / ``document_level`` to bind.
    ``folder_path`` is the ltree text form (dot-separated labels, e.g. ``SOPs.Purchasing``).
    """

    artifact_id: str | None = None
    document_level: str | None = None
    kind: str | None = None
    concrete_type: str | None = None
    process_ids: frozenset[str] = dataclasses.field(default_factory=frozenset)
    folder_path: str | None = None
    lifecycle_state: str | None = None
    requirement_source: str | None = None
    framework_id: str | None = None

    @classmethod
    def system(cls) -> ResourceContext:
        """The SYSTEM-scoped target used by the role/user/permission admin endpoints."""
        return cls()


@dataclasses.dataclass(frozen=True, slots=True)
class RequestContext:
    """Per-request context ``C`` (doc 07 §6.1): clock, source IP, step-up assurance.

    ``step_up_satisfied`` is the Part-11 seam — v1 policy is "authenticated session",
    so it defaults True; S5/Part-11 tightens it for sig-hook actions (doc 07 §6.3 step 7).
    """

    now: Any  # datetime.datetime; typed loosely so the PDP stays import-light
    source_ip: str | None = None
    step_up_satisfied: bool = True


@dataclasses.dataclass(frozen=True, slots=True)
class Decision:
    """The PDP verdict. ``reason`` is a stable machine string; ``source`` is the
    winning grant's provenance. ``require_reason`` propagates the most-specific ALLOW's
    ``require_reason`` predicate to the caller (doc 07 §10, OV-3)."""

    allow: bool
    reason: str
    source: str | None = None
    require_reason: bool = False
