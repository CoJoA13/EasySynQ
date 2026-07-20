# apps/api/src/easysynq_api/services/reports/document_control.py
"""The Controlled Document Register report (ISO 9001 §7.5.3 master list; doc 13 §6.1, doc 15 §8.15).

``GET /reports/document-control`` (api/reports.py) returns the org's master list of controlled
Documents — permission-filtered by ``document.read`` (the ``list_documents`` row-filter), with an
audit-defensible provenance header + a content hash over the full as-of set. Read-only: NO
audit_event, NO WORM write, NO migration. The pure helpers (hash + provenance) are DB-free and
unit-tested; ``compute_document_control_register`` does the query + authz filter + batched
enrichment.
"""

from __future__ import annotations

import datetime
import hashlib
import json
from typing import Any

_REPORT_NAME = "Controlled Document Register"


def register_content_hash(rows: list[dict[str, Any]]) -> str:
    """A deterministic sha256 over the register's ROW DATA (not the provenance block, whose
    wall-clock ``generated_at`` would make every hash unique). Rows are sorted by ``identifier`` and
    canonically serialized so the hash is independent of DB return order and reproducible given the
    same filtered set + as-of. Filter-sensitive: a different row set → a different hash."""
    ordered = sorted(rows, key=lambda r: str(r.get("identifier") or ""))
    canonical = json.dumps(
        ordered, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def build_provenance(
    *,
    generated_by: str,
    generated_at: datetime.datetime,
    scope: str,
    app_version: str,
    filters: dict[str, str],
    row_count: int,
    content_hash: str,
) -> dict[str, Any]:
    """The audit-defensibility header block (doc 13 §6). ``as_of`` mirrors ``generated_at`` (the
    instant the register was materialized). ``filters`` echoes the applied ``filter[...]`` params so
    the content hash is reproducible."""
    stamp = generated_at.isoformat()
    return {
        "report_name": _REPORT_NAME,
        "generated_by": generated_by,
        "generated_at": stamp,
        "as_of": stamp,
        "scope": scope,
        "app_version": app_version,
        "filters": filters,
        "row_count": row_count,
        "content_hash": content_hash,
    }
