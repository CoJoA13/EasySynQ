"""CAPA / NCR / Complaint use-case layer (slice S-capa-1)."""

from __future__ import annotations

from .service import (
    advance_capa_to_containment,
    build_capa,
    capture_complaint,
    create_ncr,
    raise_capa,
    record_ncr_disposition,
    spawn_capa_from_complaint,
)

__all__ = [
    "advance_capa_to_containment",
    "build_capa",
    "capture_complaint",
    "create_ncr",
    "raise_capa",
    "record_ncr_disposition",
    "spawn_capa_from_complaint",
]
