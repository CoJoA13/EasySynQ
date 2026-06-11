"""The acknowledgements engine (slice S-ack-1; doc 04 §8, R42/R43)."""

from .queries import coverage_counts, coverage_matrix, list_entries, resolve_audience

__all__ = ["coverage_counts", "coverage_matrix", "list_entries", "resolve_audience"]
