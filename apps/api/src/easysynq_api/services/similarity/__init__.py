"""Near-duplicate detection seam (slice S-ing-3, doc 09 §7.1/§14, R34).

Engine-agnostic near-dup clustering, parallel to the ``services/search`` ``Indexer`` seam. The MVP /
S-profile impl is the in-process MinHash detector; an OpenSearch-backed detector is the v1
drop-in (R34 / doc 09 §14 graceful degradation). Dedup is an ingestion-time batch concern (§7),
distinct from live search, so it gets its own seam.
"""

from .detector import (
    DedupDetector,
    InProcessMinHashDetector,
    NearDupCluster,
    NearDupItem,
    get_dedup_detector,
)

__all__ = [
    "DedupDetector",
    "InProcessMinHashDetector",
    "NearDupCluster",
    "NearDupItem",
    "get_dedup_detector",
]
