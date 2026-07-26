"""Parity guard: every closed OpenAPI enum that mirrors a Python enum must list exactly its values.

Batch 12 fixed two instances of the same silent drift. ``AuditEvent.object_type`` had grown from 8
to 16 values across eight ``ALTER TYPE audit_object_type ADD VALUE`` migrations while the contract
still advertised the original 8; ``ImportRunStatus`` had gained four states (Reviewing, Committing,
Completed, PartiallyCommitted) the contract never learned about.

Nothing catches this on its own. ``redocly lint`` only checks that the document is well-formed --
it has no idea what the server actually emits -- so a stale closed enum lints clean forever, and a
generated client silently rejects (or mis-types) a perfectly valid response. This test is the only
thing standing between the next ``ADD VALUE`` migration and a re-opened contract gap, so it reads
the REAL openapi.yaml and the REAL Python enums rather than any hand-copied list.

To extend: add a row to ``_CASES``. The path is a dotted walk from the document root.
"""

from __future__ import annotations

import enum
import pathlib
from typing import Any

import pytest
import yaml

from easysynq_api.db.models._audit_enums import AuditObjectType
from easysynq_api.db.models._ingestion_enums import ImportRunStatus

_OPENAPI = pathlib.Path(__file__).resolve().parents[4] / "packages" / "contracts" / "openapi.yaml"

# (label, dotted path to the `enum` list in openapi.yaml, the authoritative Python enum)
_CASES: list[tuple[str, str, type[enum.Enum]]] = [
    (
        "ImportRunStatus",
        "components.schemas.ImportRunStatus.enum",
        ImportRunStatus,
    ),
    (
        "AuditEvent.object_type",
        "components.schemas.AuditEvent.properties.object_type.enum",
        AuditObjectType,
    ),
]


def _document() -> dict[str, Any]:
    assert _OPENAPI.is_file(), f"contract not found at {_OPENAPI}"
    loaded = yaml.safe_load(_OPENAPI.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _walk(document: dict[str, Any], dotted: str) -> Any:
    node: Any = document
    for part in dotted.split("."):
        assert isinstance(node, dict) and part in node, (
            f"{dotted!r} does not resolve in openapi.yaml (stopped at {part!r}) -- the schema was "
            f"renamed or restructured; update _CASES."
        )
        node = node[part]
    return node


@pytest.mark.parametrize(
    ("label", "dotted", "python_enum"), _CASES, ids=[case[0] for case in _CASES]
)
def test_openapi_enum_matches_python_enum(
    label: str, dotted: str, python_enum: type[enum.Enum]
) -> None:
    """The contract's closed enum must be exactly the Python enum's value set.

    Compared as sets (declaration order is cosmetic and would make this brittle), but duplicates in
    the contract are still an error -- a set comparison alone would hide them.
    """
    published = _walk(_document(), dotted)
    assert isinstance(published, list), f"{label}: expected a YAML list at {dotted}"
    assert len(published) == len(set(published)), (
        f"{label}: openapi.yaml lists duplicate values: "
        f"{sorted({v for v in published if published.count(v) > 1})}"
    )

    authoritative = {member.value for member in python_enum}
    missing = sorted(authoritative - set(published))
    extra = sorted(set(published) - authoritative)
    assert not missing and not extra, (
        f"{label} has drifted from {python_enum.__name__}.\n"
        f"  missing from openapi.yaml : {missing or '-'}\n"
        f"  not in {python_enum.__name__:<18}: {extra or '-'}\n"
        f"Add the value(s) at {dotted} in packages/contracts/openapi.yaml."
    )
