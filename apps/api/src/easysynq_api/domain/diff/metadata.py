"""Pure metadata diff (slice S-dcr-3a; doc 05 §8.1/§8.2). No I/O.

Compares two versions' frozen ``metadata_snapshot`` JSONB field-by-field over the canonical
``_snapshot()`` field set (``services/vault/service.py`` — the only fields actually frozen onto a
version). Version COLUMNS (revision_label, change_significance, change_reason, version_state,
effective window, …) are NOT diffed here — they live in the provenance header band (doc 05 §8.1:
"Approval / provenance — listed, not diffed"). doc 05 §8.2's worked-example fields "Required
approvers" / "Review interval" / "Read-acknowledge required" are NOT in the v1 data model (no
``review_interval`` / approver-set is frozen on a version), so they are out of scope for the
diff.
"""

from __future__ import annotations

import dataclasses
from typing import Any

# The canonical frozen-metadata field set (mirrors services/vault/service.py::_snapshot).
# ``field_schema`` is present only on Form/Template versions (S-rec-3) — included so a form's
# pinned schema diffs too.
SNAPSHOT_FIELDS: tuple[str, ...] = (
    "identifier",
    "title",
    "document_type_id",
    "owner_user_id",
    "folder_path",
    "classification",
    "framework_id",
    "field_schema",
)


@dataclasses.dataclass(frozen=True)
class FieldDelta:
    field: str
    from_value: Any
    to_value: Any
    changed: bool


def diff_metadata(from_snapshot: dict[str, Any], to_snapshot: dict[str, Any]) -> list[FieldDelta]:
    """Field-by-field deltas over the frozen metadata snapshots. Emits one :class:`FieldDelta` per
    SNAPSHOT_FIELD present in EITHER snapshot (a field absent on one side reads as ``None``),
    with
    ``changed`` set when the two values differ."""
    deltas: list[FieldDelta] = []
    for field in SNAPSHOT_FIELDS:
        present = field in from_snapshot or field in to_snapshot
        if not present:
            continue
        old = from_snapshot.get(field)
        new = to_snapshot.get(field)
        deltas.append(FieldDelta(field=field, from_value=old, to_value=new, changed=old != new))
    return deltas
