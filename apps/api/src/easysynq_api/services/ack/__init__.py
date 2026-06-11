"""The acknowledgements engine (slice S-ack-1; doc 04 §8, R42/R43)."""

from .decide import decide_doc_ack
from .queries import coverage_counts, coverage_matrix, list_entries, resolve_audience
from .sink import get_ack_enqueue_sink, set_ack_enqueue_sink

__all__ = [
    "coverage_counts",
    "coverage_matrix",
    "decide_doc_ack",
    "get_ack_enqueue_sink",
    "list_entries",
    "resolve_audience",
    "set_ack_enqueue_sink",
]
