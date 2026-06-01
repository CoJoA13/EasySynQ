"""The authorization decision point (PDP) and its pure value types.

``authorize`` (pdp.py) implements the register-R3 precedence — deny-by-default,
deny-always-wins — exactly. ``types`` holds the framework-free dataclasses the PEP
populates from the database. See doc 07 §6 (resolution pipeline) and doc 18 §5.2.
"""

from .pdp import authorize
from .types import (
    Decision,
    Effect,
    RequestContext,
    ResolvedGrant,
    ResourceContext,
    ScopeLevel,
    specificity,
)

__all__ = [
    "Decision",
    "Effect",
    "RequestContext",
    "ResolvedGrant",
    "ResourceContext",
    "ScopeLevel",
    "authorize",
    "specificity",
]
