"""Native-PG enum bindings for the CAPA / NCR / Complaint family (slice S-capa-1; doc 02 Cl 10.2,
doc 10 §6, doc 14 §9/§14).

The retained-evidence CAPA is a ``kind=RECORD`` shared-PK subtype with a mutable ``close_state``
lifecycle column (the ``record.disposition_state`` / ``audit.state`` precedent); ``capa_stage`` is
an
append-only stage-block trail; ``ncr`` is an own table (ISO 9001 8.7 nonconforming output) and
``complaint`` is a lightweight record subtype (R16). Created by the Alembic migration; referenced
here with ``create_type=False``.

Enum-value canon (all lowercase — extends the R2 ``signature_event.meaning`` / R16
``source=complaint``
lowercase precedent; doc 14 §9 wrote ``capa.source`` as ``AUDIT`` which is a spec typo, normalized
here; recorded in decisions-register R39):
- ``capa_source``: ``audit``, ``process``, ``complaint``, ``review_output`` (``review_output`` is a
  RESERVED forward seam for the deferred Management-Review family — never written in v1).
- ``ncr_source``: ``audit``, ``process``, ``complaint``, ``internal`` (differs from ``capa_source``:
  an NCR can be ``internal``, a CAPA can be ``review_output``).
- ``nc_severity``: shared by ``capa`` / ``ncr`` / ``complaint`` severity (and ``audit_finding`` in
  S-aud-2) — a single closed Critical/Major/Minor vocabulary.
"""

from __future__ import annotations

import enum

from sqlalchemy import Enum as SAEnum


class CapaSource(enum.Enum):
    audit = "audit"
    process = "process"
    complaint = "complaint"
    # RESERVED for the deferred Management-Review family (doc 10 §7) — forward-compatible, never
    # written until that slice ships (decisions-register R39 residual note).
    review_output = "review_output"
    # S-risk-3 (clause 6.1, R49 §7): a CAPA spawned to TREAT a risk_opportunity row (the one-click
    # risk→CAPA seam). The risk row's ``linked_capa_id`` latch holds the spawned CAPA; this source
    # tags the CAPA's origin. Added via ``ALTER TYPE capa_source ADD VALUE`` in 0059 (the additive
    # pattern; a from-scratch ``upgrade head`` rebuilds the type from CAPA_SOURCE_VALUES). Appended
    # LAST so the Python member order matches PG's append-to-end ADD VALUE.
    risk = "risk"


class NcrSource(enum.Enum):
    audit = "audit"
    process = "process"
    complaint = "complaint"
    internal = "internal"


class NcSeverity(enum.Enum):
    # The closed nonconformity-severity vocabulary, shared by capa.severity, ncr.severity,
    # complaint.severity (+ audit_finding.severity in S-aud-2). Title-case per doc 14 §9.
    Critical = "Critical"
    Major = "Major"
    Minor = "Minor"


class CapaCloseState(enum.Enum):
    # The doc 10 §6 CAPA lifecycle. Also the ``capa_stage.stage`` discriminator (doc 14 §9: "same as
    # close_state"). NOT purely linear — a not-effective Verify loops back to RootCause (the
    # effectiveness loop, S-capa-3: cycle_marker bump + re-propose/re-approve to ActionPlan).
    # Rejected is a terminal branch. S-capa-1 SERVICE wires only Raised→Containment; the full map
    # is defined in domain/capa/fsm.py for forward-compat.
    Raised = "Raised"
    Containment = "Containment"
    RootCause = "RootCause"
    ActionPlan = "ActionPlan"
    Implement = "Implement"
    Verify = "Verify"
    Closed = "Closed"
    Rejected = "Rejected"


class NcrDisposition(enum.Enum):
    # ISO 9001 8.7 disposition of nonconforming output (decisions-register R20, verbatim tokens).
    use_as_is = "use_as_is"
    rework = "rework"
    scrap = "scrap"
    # ``return`` is a Python keyword — the member is ``RETURN_``; the canonical token (the .value,
    # what every JSON/OpenAPI/DB surface sees via values_callable) is ``return``.
    RETURN_ = "return"
    concession = "concession"
    regrade = "regrade"


def _vals(e: type[enum.Enum]) -> list[str]:
    return [m.value for m in e]


capa_source_enum = SAEnum(CapaSource, name="capa_source", values_callable=_vals, create_type=False)
ncr_source_enum = SAEnum(NcrSource, name="ncr_source", values_callable=_vals, create_type=False)
nc_severity_enum = SAEnum(NcSeverity, name="nc_severity", values_callable=_vals, create_type=False)
capa_close_state_enum = SAEnum(
    CapaCloseState, name="capa_close_state", values_callable=_vals, create_type=False
)
ncr_disposition_enum = SAEnum(
    NcrDisposition, name="ncr_disposition", values_callable=_vals, create_type=False
)

# The canonical v1 value tuples, re-used by the migration's CREATE TYPE so the ORM and the
# hand-authored DDL never drift (the AUDIT_STATE_VALUES / EVENT_TYPE_VALUES precedent).
CAPA_SOURCE_VALUES = tuple(_vals(CapaSource))
NCR_SOURCE_VALUES = tuple(_vals(NcrSource))
NC_SEVERITY_VALUES = tuple(_vals(NcSeverity))
CAPA_CLOSE_STATE_VALUES = tuple(_vals(CapaCloseState))
NCR_DISPOSITION_VALUES = tuple(_vals(NcrDisposition))
