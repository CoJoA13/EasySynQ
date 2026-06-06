"""Pure audit-finding predicates (no I/O) -- the close-gate rule (slice S-aud-2; doc 10 §5.3,
decisions-register R39).

The audit close gate (Closing -> Closed) is **block-until-corrected**: an audit cannot close while
any *live* NC finding lacks a linked CAPA at ``close_state=Closed``. A live NC is a finding of type
``NC`` that has NOT been superseded by a correction (an auditor declassifies a legitimately-rejected
NC by retyping it NC -> Observation/OFI, which supersedes the original and drops it from the live
set). A Rejected CAPA never satisfies the gate (only ``Closed`` does). A live NC with no CAPA at all
(``auto_capa_close_state is None`` -- a data anomaly, since NC findings auto-create a CAPA) also
blocks fail-closed rather than silently passing.

The service-layer gate loads ``(finding_type, is_superseded, auto_capa_close_state)`` per finding
and filters with this single predicate, so the rule has one source of truth (unit-tested alone).
"""

from __future__ import annotations

from ...db.models._capa_enums import CapaCloseState
from ...db.models._iso_audit_enums import FindingType


def finding_blocks_close(
    finding_type: FindingType,
    is_superseded: bool,
    auto_capa_close_state: CapaCloseState | None,
) -> bool:
    """True iff this finding blocks the audit close: a live (not-superseded) NC whose linked CAPA is
    absent or not yet ``Closed``. OBSERVATION / OFI and superseded findings never block."""
    if finding_type is not FindingType.NC:
        return False
    if is_superseded:
        return False
    return auto_capa_close_state is not CapaCloseState.Closed
