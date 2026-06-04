"""The §4.3 calm inventory summary (slice S-ing-1, doc 09 §4.3) — pure assembly.

The heavy aggregation (disposition counts, extension histogram, exact-dup-by-sha256 clusters) is
done
as SQL aggregates in ``services/ingestion/repository.py`` over the run's ``import_file`` rows —
never by
loading rows into RAM. This module only assembles the canonical ``import_run.counts`` JSONB shape
from
those aggregate values, so the shape stays in one place and is unit-testable with plain numbers. No
``kind`` histogram yet — kind is slice 2 (classification)."""

from __future__ import annotations

from typing import Any


def build_summary(
    *,
    total_files: int,
    total_bytes: int,
    disposition_counts: dict[str, int],
    ext_histogram: dict[str, int],
    exact_dup_clusters: int,
    exact_dup_files: int,
) -> dict[str, Any]:
    """Assemble the calm inventory summary written to ``import_run.counts`` at scan-complete."""
    return {
        "total_files": total_files,
        "total_bytes": total_bytes,
        "included": disposition_counts.get("included", 0),
        "excluded": disposition_counts.get("excluded", 0),
        "quarantine": disposition_counts.get("quarantine", 0),
        "ext_histogram": ext_histogram,
        "exact_dup_clusters": exact_dup_clusters,
        "exact_dup_files": exact_dup_files,
    }
