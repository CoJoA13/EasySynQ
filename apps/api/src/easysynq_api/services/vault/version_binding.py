"""The blob→object version binding: which stored version a blob row was sealed as.

A recovery generation must bind every referenced object to its exact version, not merely to a
bucket and key. Because blobs are content-addressed and only the sha256 is checked, an object
overwritten after a generation was written is otherwise indistinguishable from the sealed one: equal
bytes hash identically, so a current-version restore silently prefers the newer version.

These helpers are pure and hold the only mapping from a write's outcome to the two columns, so every
call site records the same shapes and the database CHECK
(``ck_blob_object_version_binding``, migration 0093) can stay exact.
"""

from __future__ import annotations

from typing import Final

#: The verified WORM promotion's read-back version (documents, records).
PROMOTION: Final = "promotion"
#: A direct server-side put that returned a version.
WRITE: Final = "write"
#: Observed later by ``backup bind-versions``; attests only what was current at that moment.
BACKFILL: Final = "backfill"
#: The write returned no version because the bucket has none (renditions). NOT a failure, and NOT
#: the same as an absent binding: it records that no version exists to bind.
UNVERSIONED: Final = "unversioned"

BOUND_SOURCES: Final = (PROMOTION, WRITE, BACKFILL)
SOURCES: Final = (*BOUND_SOURCES, UNVERSIONED)


def binding(version_id: str | None, *, source: str) -> dict[str, str | None]:
    """The two blob columns for a binding, or the unversioned shape when there is no version.

    Refuses an unknown source, and refuses a bound source without a version id, rather than writing
    a row the CHECK would reject at the database boundary.
    """
    if source not in SOURCES:
        raise ValueError(f"unknown object version source: {source!r}")
    if source == UNVERSIONED:
        if version_id is not None:
            raise ValueError("an unversioned binding cannot carry a version id")
        return {"object_version_id": None, "object_version_source": UNVERSIONED}
    if not version_id:
        raise ValueError(f"{source} binding requires a version id")
    return {"object_version_id": version_id, "object_version_source": source}


def promotion_binding(version_id: str) -> dict[str, str | None]:
    """The binding for a WORM promotion, whose read-back version id is always present."""
    return binding(version_id, source=PROMOTION)


def write_binding(version_id: str | None) -> dict[str, str | None]:
    """The binding for a direct server-side put: bound when the bucket returned a version, and
    explicitly ``unversioned`` when it did not."""
    if version_id is None:
        return binding(None, source=UNVERSIONED)
    return binding(version_id, source=WRITE)
