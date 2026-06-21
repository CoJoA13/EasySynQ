"""The versioned clause-4.2 interested-parties register content (S-interested-parties-1, R51) —
pure, no I/O.

``build_register`` produces the canonical dict that is BOTH the register version's WORM source blob
(``rfc8785.dumps`` — JCS) AND the ``metadata_snapshot.interested_party_register`` fold, so the bytes
and the snapshot can never diverge (the S-rec-3 invariant; the ``domain/context/register_content``
sibling, verbatim).

Like the context register and unlike the risk register, clause 4.2 has **no computed/graded axis**
(``party_type``/``influence``/``status`` are categorical user inputs, not a derived band), so there
is **no ``criteria`` block and no ``resolve_criteria``** — the frozen content is purely the rows.
The live satellite rows are read back as-is.
"""

from __future__ import annotations

from typing import Any

from easysynq_api.db.models._vault_enums import VersionState


def build_register(*, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The canonical register content frozen at publish. Rows are sorted by ``id`` for a stable,
    reproducible serialization (rfc8785/JCS canonicalizes each row's KEYS; this canonicalizes the
    LIST order). The SAME dict becomes the WORM source blob AND the ``metadata_snapshot`` fold (the
    ``build_register`` context precedent — interested-parties carries no per-method ``criteria``
    map)."""
    return {"rows": sorted(rows, key=lambda r: str(r["id"]))}


def register_needs_freeze(
    *,
    latest_version_state: VersionState | None,
    latest_register: dict[str, Any] | None,
    working: dict[str, Any],
) -> bool:
    """True when publish must mint a NEW frozen register version (the context
    ``register_needs_freeze`` switch).

    - no version at all → first publish
    - latest is not a Draft → a revision (the latest version is the governing Effective one, whose
      snapshot CARRIES a register; the FSM, not this predicate, guards the other states)
    - the latest Draft's frozen register ≠ the working register → an edit happened since the last
      freeze (or the latest Draft is a register-less legacy byte-version) → re-freeze so the
      approver
      always signs the CURRENT register.

    Equal dicts on a Draft → skip (the no-edit re-publish after request_changes dedups). Both sides
    MUST come from ``build_register`` — never a hand-built dict (string/order canonicalization
    differs)."""
    if latest_version_state is None:
        return True
    if latest_version_state is not VersionState.Draft:
        return True
    return latest_register != working
