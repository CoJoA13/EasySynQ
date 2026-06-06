"""The pure obsoletion safety check (slice S-dcr-2; doc 05 §7.3). No I/O — fully unit-testable.

doc 05 §7.3: retiring a document is **blocked** when removing it would create a
coverage/dependency gap — specifically when the document (1) still ``governs``-links an
**active** process, OR (2) is ``references``-linked by an **Effective** document, OR (3) provides
★ mandatory-item coverage that would have **no replacement** (it is the sole Effective coverer of
a ★ clause). The block is overridable only by an explicit ``force_retire`` + recorded
justification (audited).

S-dcr-2 ships this predicate + SURFACES it (the ``GET /documents/{id}/where-used``
``obsoletion_safety`` block + a RETIRE-DCR's ``impact_assessment``). The actual 409-blocking gate
(with the ``force_retire`` escape hatch) is wired in **S-dcr-5**, where ``POST
/dcrs/{id}/implement`` for a RETIRE DCR is the DCR-governed obsoletion call site (owner decision,
decisions-register R40 addendum) — the shipped S4 ``document.obsolete`` endpoint is left
untouched this slice.
"""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class ObsoletionReason:
    code: str  # governs_active_process | referenced_by_effective | sole_star_coverage
    detail: str


@dataclasses.dataclass(frozen=True)
class ObsoletionSafety:
    blocked: bool
    reasons: tuple[ObsoletionReason, ...]


def evaluate_obsoletion(
    *,
    governing_active_processes: list[tuple[str, str]],
    referencing_effective_documents: list[tuple[str, str]],
    sole_star_clauses: list[tuple[str, str]],
) -> ObsoletionSafety:
    """Apply the doc 05 §7.3 block rule. Inputs are ``(id, label)`` pairs the service resolves:
    - ``governing_active_processes``: ACTIVE processes this doc ``governs`` (process_link).
    - ``referencing_effective_documents``: Effective docs that ``references`` this doc (inbound
      link).
    - ``sole_star_clauses``: ★ clauses for which this doc is the ONLY Effective coverer (no
      replacement). Returns ``blocked`` iff any leg fires, with a structured reason per leg
      (empty → safe to obsolete).
    """
    reasons: list[ObsoletionReason] = []
    if governing_active_processes:
        names = ", ".join(label for _id, label in governing_active_processes)
        reasons.append(
            ObsoletionReason(
                "governs_active_process",
                f"still governs {len(governing_active_processes)} active process(es): {names}",
            )
        )
    if referencing_effective_documents:
        names = ", ".join(label for _id, label in referencing_effective_documents)
        reasons.append(
            ObsoletionReason(
                "referenced_by_effective",
                f"referenced by {len(referencing_effective_documents)} Effective doc(s): {names}",
            )
        )
    if sole_star_clauses:
        names = ", ".join(label for _id, label in sole_star_clauses)
        reasons.append(
            ObsoletionReason(
                "sole_star_coverage",
                f"sole Effective coverer of {len(sole_star_clauses)} ★ clause(s): {names}",
            )
        )
    return ObsoletionSafety(blocked=bool(reasons), reasons=tuple(reasons))
