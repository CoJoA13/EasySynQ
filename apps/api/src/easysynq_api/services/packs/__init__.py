"""Evidence packs (slice S-pack-1, doc 06 §7): scope resolution + preview + immutable build/seal.

A pack is an on-demand, scope-limited, immutable, self-verifying bundle of records + their evidence
+ a traceability manifest, registered on seal as a RETAIN_PERMANENT EVIDENCE Record. The use-case
layer (``service``) owns the request-side transactions (preview + generate); ``build`` is the seal.
"""

from .build import build
from .service import (
    ClassifiedRecord,
    classify_candidates,
    create_pack_with_preview,
    emit_pack_event,
    emit_pack_event_system,
    exclusion_summary,
    gap_summary,
    generate_pack,
    reap_stalled_builds,
)

__all__ = [
    "ClassifiedRecord",
    "build",
    "classify_candidates",
    "create_pack_with_preview",
    "emit_pack_event",
    "emit_pack_event_system",
    "exclusion_summary",
    "gap_summary",
    "generate_pack",
    "reap_stalled_builds",
]
