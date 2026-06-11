"""The acknowledgements engine (slice S-ack-1; doc 04 §8, R42/R43)."""

from .queries import coverage_counts, coverage_matrix, list_entries, resolve_audience
from .sink import get_ack_enqueue_sink, set_ack_enqueue_sink

__all__ = [
    "coverage_counts",
    "coverage_matrix",
    "get_ack_enqueue_sink",
    "list_entries",
    "resolve_audience",
    "set_ack_enqueue_sink",
]
