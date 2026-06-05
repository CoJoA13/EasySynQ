"""Pure quorum evaluation for the declarative workflow engine (S-wf-engine, doc 10 §2.4). No I/O.

A stage's quorum is a tri-state over its live (non-skipped) per-candidate tasks: PENDING (more
decisions needed), MET (advance), or FAILED (early-fail — the remaining pending decisions can no
longer reach the threshold, so the stage fails immediately rather than hanging — doc 10 §2.4 line
134). ``approvals`` is the count of **distinct** approving deciders (the engine passes
``len({outcome.decided_by})``), never raw task rows, so one actor can't satisfy a multi-task quorum.
"""

from __future__ import annotations

import math
from typing import Any, Literal

QuorumState = Literal["PENDING", "MET", "FAILED"]


def required_approvals(spec: dict[str, Any], resolved_count: int) -> int:
    """The minimum distinct approvals a (flat) quorum spec needs for the resolved candidate count.
    Used at materialization for the under-quorum fail-closed check (resolved_count < this →
    NEEDS_ATTENTION)."""
    qtype = str(spec.get("type", "ANY")).upper()
    if qtype == "ALL":
        return max(resolved_count, 1)
    if qtype == "N_OF_M":
        return int(spec.get("n", 1))
    if qtype == "PERCENT":
        return math.ceil(int(spec.get("p", 100)) * resolved_count / 100)
    return 1  # ANY (and any unknown type defaults to the weakest legal quorum: one approval)


def quorum_state(
    spec: dict[str, Any], approvals: int, rejects: int, resolved_count: int
) -> QuorumState:
    """Tri-state evaluation. ``approvals`` = distinct approvers; ``rejects`` = decided-reject count;
    ``resolved_count`` = the materialized candidate-task count. ``remaining`` undecided = the rest.
    """
    remaining = max(resolved_count - approvals - rejects, 0)
    qtype = str(spec.get("type", "ANY")).upper()

    if qtype == "ALL":
        if rejects > 0:
            return "FAILED"
        return "MET" if resolved_count > 0 and approvals >= resolved_count else "PENDING"

    if qtype == "N_OF_M":
        n = int(spec.get("n", 1))
        if approvals >= n:
            return "MET"
        if approvals + remaining < n:
            return "FAILED"
        return "PENDING"

    if qtype == "PERCENT":
        p = int(spec.get("p", 100))
        if approvals * 100 >= p * resolved_count and resolved_count > 0:
            return "MET"
        if (approvals + remaining) * 100 < p * resolved_count:
            return "FAILED"
        return "PENDING"

    # ANY (default): the first approval wins; only an all-reject sweep fails it.
    if approvals >= 1:
        return "MET"
    if remaining == 0:
        return "FAILED"
    return "PENDING"
