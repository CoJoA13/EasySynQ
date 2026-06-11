"""Pure S-ack-1 rules — no I/O (slice S-ack-1; doc 04 §8, R43).

``last_major_seq`` is the R43 satisfaction boundary: a user is satisfied iff they hold an
acknowledgement on a version with ``version_seq >= last_major_seq`` (acks stay version-pinned
evidence; only THIS computation walks MINOR chains). A chain with no MAJOR version is real
(the API requires the caller to pick MAJOR or MINOR at check-in — a chain's first version can
legally be MINOR) — the boundary falls back to the LOWEST seq (any-version ack satisfies;
the doc never had a substantive change boundary).

``plan_obligations`` is the sweep's set-algebra: cancel-before-mint (a stale open task must never
shadow the fresh mint under the open-task guard).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping


def last_major_seq(versions: Iterable[tuple[int, bool]], *, current_seq: int) -> int:
    """The newest MAJOR version_seq at or below ``current_seq``; lowest seq when no MAJOR exists.

    ``versions`` are ``(version_seq, is_major)`` pairs (any order, may include future/scheduled
    seqs beyond ``current_seq`` — those never move the boundary)."""
    in_range = [(seq, major) for seq, major in versions if seq <= current_seq]
    majors = [seq for seq, major in in_range if major]
    if majors:
        return max(majors)
    return min(seq for seq, _ in in_range)


def plan_obligations(
    *,
    audience: set[uuid.UUID],
    satisfied: set[uuid.UUID],
    open_tasks: Mapping[uuid.UUID, int],
    last_major: int,
) -> tuple[set[uuid.UUID], set[uuid.UUID]]:
    """(to_mint, to_cancel) for one ack-eligible document.

    ``open_tasks`` maps user → the open task's pinned version_seq. Cancel wins: a stale-pinned
    user still in the audience is re-minted in the SAME pass (cancel-before-mint), ending the
    sweep with exactly one fresh open task."""
    stale = {u for u, pinned in open_tasks.items() if pinned < last_major}
    left = set(open_tasks) - audience
    already_done = set(open_tasks) & satisfied
    to_cancel = stale | left | already_done
    surviving_open = set(open_tasks) - to_cancel
    # A stale-pinned audience member is NOT in surviving_open (stale ⊆ to_cancel), so the single
    # term already re-mints them in this same pass — cancel-before-mint needs no second clause.
    to_mint = audience - satisfied - surviving_open
    return to_mint, to_cancel
