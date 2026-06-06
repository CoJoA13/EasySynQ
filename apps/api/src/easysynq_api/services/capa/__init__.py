"""CAPA / NCR / Complaint use-case layer (slice S-capa-1)."""

from __future__ import annotations

from .service import (
    advance_capa_to_containment,
    advance_capa_to_implement,
    advance_capa_to_root_cause,
    build_capa,
    capture_complaint,
    close_capa,
    create_ncr,
    decide_capa_action_plan,
    propose_action_plan,
    raise_capa,
    record_ncr_disposition,
    spawn_capa_from_complaint,
    verify_capa,
)

__all__ = [
    "advance_capa_to_containment",
    "advance_capa_to_implement",
    "advance_capa_to_root_cause",
    "build_capa",
    "capture_complaint",
    "close_capa",
    "create_ncr",
    "decide_capa_action_plan",
    "propose_action_plan",
    "raise_capa",
    "record_ncr_disposition",
    "spawn_capa_from_complaint",
    "verify_capa",
]
