"""The near-dup detector seam + the in-process MinHash impl (slice S-ing-3, doc 09 §7.1/§14, R34).

``DedupDetector`` is the engine-agnostic near-dup seam (the ``services/search`` ``Indexer``
precedent). ``get_dedup_detector()`` returns the in-process MinHash impl — the MVP / S-profile
realization (doc 09 §14: near-dup uses in-process MinHash when OpenSearch is disabled).
``OpenSearchDedupDetector`` is the **documented, not-built** v1 drop-in (the reserved
``OpenSearchIndexer`` precedent) — when it lands it consumes the sidecar, swapped in here, no caller
change.

The detector only CLUSTERS near-duplicates (the §7.1 detector). It does NOT pick the canonical —
that is the §7.2 deterministic tie-break the dedup worker owns (it needs file metadata the detector
lacks). Clustering is on **exact-Jaccard re-verification** of LSH candidate pairs, never the MinHash
estimate (doc 09 §7.1) — so the 0.85 boundary is deterministic.

``detect_near`` is ``async`` and takes an optional ``heartbeat`` coroutine the worker passes to keep
the source-root lock alive across a long build (the CLAUDE.md "heartbeat DURING compute" rule); the
in-process impl is pure CPU but awaits it on a fixed cadence.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol

from ...domain.ingestion import minhash
from ...domain.ingestion.normalize import normalize_text

# Await the heartbeat / yield this often while building signatures + verifying candidate pairs.
_HEARTBEAT_EVERY = 256


@dataclasses.dataclass(frozen=True, slots=True)
class NearDupItem:
    """One file's near-dup input: its id + the raw extracted text (the detector normalizes it)."""

    file_id: uuid.UUID
    text: str


@dataclasses.dataclass(frozen=True, slots=True)
class NearDupCluster:
    """A near-dup cluster: ≥2 member file ids (sorted, deterministic) + the cluster's representative
    similarity (the MIN pairwise exact-Jaccard among confirmed edges — the most conservative)."""

    member_file_ids: tuple[uuid.UUID, ...]
    jaccard: float


class DedupDetector(Protocol):
    """Engine-agnostic near-dup seam. The in-process MinHash impl is the MVP/S-profile path; an
    OpenSearch-backed detector is the v1 drop-in (R34 / doc 09 §14)."""

    async def detect_near(
        self,
        items: Sequence[NearDupItem],
        *,
        threshold: float = minhash.NEAR_DUP_THRESHOLD,
        heartbeat: Callable[[], Awaitable[None]] | None = None,
    ) -> list[NearDupCluster]: ...


class InProcessMinHashDetector:
    """Pure-Python MinHash + LSH near-dup clustering (doc 09 §7.1) — no external service, fully
    deterministic, the S-profile / OpenSearch-disabled realization (doc 09 §14)."""

    async def detect_near(
        self,
        items: Sequence[NearDupItem],
        *,
        threshold: float = minhash.NEAR_DUP_THRESHOLD,
        heartbeat: Callable[[], Awaitable[None]] | None = None,
    ) -> list[NearDupCluster]:
        # Eligible = files whose normalized text yields ≥1 shingle (≥ SHINGLE_K tokens). Sub-k /
        # empty texts are excluded (a fallback whole-text shingle would cluster all empties).
        shingle_by_id: dict[uuid.UUID, frozenset[str]] = {}
        for n, item in enumerate(items):
            sh = minhash.shingles(normalize_text(item.text))
            if sh:
                shingle_by_id[item.file_id] = sh
            if heartbeat is not None and n % _HEARTBEAT_EVERY == 0:
                await heartbeat()
        if len(shingle_by_id) < 2:
            return []

        sigs: dict[uuid.UUID, tuple[int, ...]] = {}
        for n, (fid, sh) in enumerate(shingle_by_id.items()):
            sigs[fid] = minhash.signature(sh)
            if heartbeat is not None and n % _HEARTBEAT_EVERY == 0:
                await heartbeat()

        candidates = minhash.lsh_candidate_pairs(sigs)
        # Exact-Jaccard re-verify each candidate pair (the deterministic §7.1 decision). Keep the
        # confirmed pairwise scores so a cluster can report its most-conservative (min) similarity.
        confirmed: list[frozenset[uuid.UUID]] = []
        pair_jaccard: dict[frozenset[uuid.UUID], float] = {}
        for n, pair in enumerate(candidates):
            a, b = tuple(pair)
            j = minhash.exact_jaccard(shingle_by_id[a], shingle_by_id[b])
            if j >= threshold:
                confirmed.append(pair)
                pair_jaccard[pair] = j
            if heartbeat is not None and n % _HEARTBEAT_EVERY == 0:
                await heartbeat()

        clusters: list[NearDupCluster] = []
        for members in minhash.connected_components(sigs.keys(), confirmed):
            member_set = set(members)
            scores = [j for p, j in pair_jaccard.items() if set(p) <= member_set]
            clusters.append(
                NearDupCluster(
                    member_file_ids=tuple(sorted(members)),
                    jaccard=min(scores) if scores else threshold,
                )
            )
        # Deterministic output order: by the (sorted) first member.
        clusters.sort(key=lambda c: c.member_file_ids[0])
        return clusters


_DETECTOR: DedupDetector = InProcessMinHashDetector()


def get_dedup_detector() -> DedupDetector:
    """The single seam the dedup worker calls. Returns the in-process MinHash detector in the MVP /
    S-profile (doc 09 §14); an ``OpenSearchDedupDetector`` is the v1 drop-in (R34 — swap here, no
    caller change), landing with the OpenSearch sidecar in the slice that consumes it."""
    return _DETECTOR
