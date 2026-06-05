"""Deterministic, dependency-free MinHash + LSH near-dup detection (slice S-ing-3, doc 09 §7.1).

The §7.1 near-dup detector is "content shingling + MinHash/Jaccard over normalized text, threshold ≥
0.85". This module is the pure math — no I/O, no third-party dep (datasketch et al.), and crucially
**process-stable**: every hash is ``hashlib.blake2b`` over UTF-8 bytes (NEVER Python ``hash()``,
which is ``PYTHONHASHSEED``-salted), and the permutation coefficients are module constants derived
once from a fixed seed. So a signature is byte-identical across CI runs, workers, and re-deliveries
— the determinism the ``import_dupe_cluster`` DELETE-then-INSERT idempotency relies on.

**The contract (doc 09 §7.1):** MinHash + LSH only GENERATES candidate pairs (a cheap blocking
step); the cluster membership is decided by an **exact-Jaccard re-verification** of each candidate
pair against the threshold. The MinHash estimate is never the decision — it would make the 0.85
boundary depend on band geometry. The LSH knee for 32x4 / 128 perms sits at ≈(1/32)^(1/4)≈0.42,
deliberately well BELOW 0.85, so recall at 0.85 is ≈1.0 (it over-emits low-similarity candidates,
which the exact re-verify drops — the safe direction for a one-shot import).

Worst case (a pathological all-similar corpus) the candidate set is O(n^2); for v1 the in-process
path is the bounded/S-profile realization (doc 09 §14 — OpenSearch is the M/L drop-in); the dedup
worker heartbeats the source-root lock across the build so a long run is never mis-reaped.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

SHINGLE_K = 5  # k-word shingle size (doc 09 §7.1 normalized-text technique)
NUM_PERM = 128  # MinHash signature length
LSH_BANDS = 32  # 32 x 4 = 128; knee ≈ (1/32)^(1/4) ≈ 0.42 (below 0.85 → high recall)
LSH_ROWS = 4
NEAR_DUP_THRESHOLD = 0.85  # §7.1 default Jaccard threshold

_MERSENNE_PRIME = (1 << 61) - 1  # the classic MinHash multiply-mod prime
_MAX_HASH = (1 << 32) - 1
_PERM_SEED = 0x5159_3317  # a fixed literal seed → process-stable permutations


def _make_perms() -> tuple[tuple[int, int], ...]:
    # Seeded, non-crypto PRNG: the permutation coefficients must be STABLE + reproducible (not
    # cryptographically random) so signatures are byte-identical across processes.
    rng = random.Random(_PERM_SEED)  # noqa: S311 — deterministic perms, not a security primitive
    return tuple(
        (rng.randrange(1, _MERSENNE_PRIME), rng.randrange(0, _MERSENNE_PRIME))
        for _ in range(NUM_PERM)
    )


_PERMS = _make_perms()


def _base_hash(token: str) -> int:
    """A stable 64-bit hash of a shingle (blake2b over UTF-8 bytes — NEVER Python ``hash()``)."""
    return int.from_bytes(hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest(), "big")


def shingles(normalized_text: str, k: int = SHINGLE_K) -> frozenset[str]:
    """The k-word shingle set of already-``normalize_text``'d input. Empty when fewer than k tokens
    — the caller MUST exclude such files from near-dup (a fallback whole-text shingle would make
    every degenerate-text file mutually identical)."""
    tokens = normalized_text.split()
    if len(tokens) < k:
        return frozenset()
    return frozenset(" ".join(tokens[i : i + k]) for i in range(len(tokens) - k + 1))


def signature(shingle_set: frozenset[str]) -> tuple[int, ...]:
    """The MinHash signature of a NON-empty shingle set. Raises on empty (caller filters first)."""
    if not shingle_set:
        raise ValueError("signature() requires a non-empty shingle set")
    bases = [_base_hash(s) for s in shingle_set]  # order-independent (min reduction)
    return tuple(min(((a * h + b) % _MERSENNE_PRIME) & _MAX_HASH for h in bases) for a, b in _PERMS)


def estimate_jaccard(sig_a: Sequence[int], sig_b: Sequence[int]) -> float:
    """The MinHash Jaccard ESTIMATE (fraction of equal positions). Diagnostic only — NOT the
    clustering decision (use ``exact_jaccard`` for that)."""
    if not sig_a or not sig_b:
        return 0.0
    equal = sum(1 for x, y in zip(sig_a, sig_b, strict=False) if x == y)
    return equal / len(sig_a)


def exact_jaccard(set_a: frozenset[str], set_b: frozenset[str]) -> float:
    """The exact Jaccard of two shingle sets — the deterministic §7.1 cluster decision. 0.0 on
    an empty union (never a ZeroDivisionError)."""
    union = len(set_a | set_b)
    if union == 0:
        return 0.0
    return len(set_a & set_b) / union


def _band_key(band: tuple[int, ...]) -> bytes:
    """A stable bucket key for an LSH band (blake2b of the band's ints — process-stable)."""
    return hashlib.blake2b(b"".join(x.to_bytes(8, "big") for x in band), digest_size=16).digest()


def lsh_candidate_pairs(
    sigs: Mapping[Any, Sequence[int]],
) -> set[frozenset[Any]]:
    """Band the signatures into ``LSH_BANDS`` buckets; emit every same-bucket pair as a candidate
    (a cheap blocking step; the caller re-verifies each pair with ``exact_jaccard``). Deterministic:
    band order + blake2b bucket keys are stable. Keys are caller-defined (hashable + sortable; the
    dedup worker passes ``import_file`` UUIDs)."""
    buckets: dict[tuple[int, bytes], list[Any]] = {}
    for key, sig in sigs.items():
        for b in range(LSH_BANDS):
            band = tuple(sig[b * LSH_ROWS : (b + 1) * LSH_ROWS])
            buckets.setdefault((b, _band_key(band)), []).append(key)
    pairs: set[frozenset[Any]] = set()
    for members in buckets.values():
        if len(members) < 2:
            continue
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                pairs.add(frozenset((members[i], members[j])))
    return pairs


def connected_components(keys: Iterable[Any], edges: Iterable[frozenset[Any]]) -> list[list[Any]]:
    """Union-find over confirmed near-dup edges → clusters. Each returned cluster is SORTED (stable
    output) and only clusters of ≥2 are returned (a singleton is not a duplicate). The full key set
    is passed so isolated keys are simply dropped. Keys must be hashable + sortable (UUIDs work)."""
    parent: dict[Any, Any] = {k: k for k in keys}

    def find(x: Any) -> Any:
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:  # path-compress
            parent[x], x = root, parent[x]
        return root

    for edge in edges:
        a, b = tuple(edge)
        if a in parent and b in parent:
            parent[find(a)] = find(b)

    groups: dict[Any, list[Any]] = {}
    for k in parent:
        groups.setdefault(find(k), []).append(k)
    return [sorted(members) for members in groups.values() if len(members) >= 2]
