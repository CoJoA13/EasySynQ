"""Reporting services (slice S10): PG-only QMS-health computations (doc 13 §1.2). The org-wide
Compliance Checklist is the MVP member; dashboards/exports/evidence-packs are deferred (v1)."""

from __future__ import annotations

from .checklist import compute_checklist, coverage_status

__all__ = ["compute_checklist", "coverage_status"]
