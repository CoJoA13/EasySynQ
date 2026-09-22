"""The blob→object version binding: the pure mapping, the manifest, and what each state means.

The database CHECK and the restore legs are exercised against real PostgreSQL/MinIO in
``tests/integration/test_restore.py``; everything here is pure.
"""

from __future__ import annotations

import pytest

from easysynq_api.services.backup import archive
from easysynq_api.services.vault import version_binding


def _ref(sha: str, *, version: str | None = None, source: str | None = None) -> archive.BlobRef:
    return archive.BlobRef(
        sha256=sha,
        size_bytes=3,
        bucket="documents",
        object_key=sha,
        object_version_id=version,
        object_version_source=source,
    )


# --- the pure mapping ---------------------------------------------------------------------------


def test_promotion_binding_carries_the_read_back_version() -> None:
    assert version_binding.promotion_binding("v-1") == {
        "object_version_id": "v-1",
        "object_version_source": "promotion",
    }


def test_a_write_that_returned_a_version_is_bound_to_it() -> None:
    assert version_binding.write_binding("v-2") == {
        "object_version_id": "v-2",
        "object_version_source": "write",
    }


def test_a_write_against_an_unversioned_bucket_records_that_fact() -> None:
    """`unversioned` is not the same as absent: it says there is no version to bind."""
    assert version_binding.write_binding(None) == {
        "object_version_id": None,
        "object_version_source": "unversioned",
    }


@pytest.mark.parametrize("source", ["promotion", "write", "backfill"])
def test_a_bound_source_refuses_an_empty_version(source: str) -> None:
    with pytest.raises(ValueError, match="requires a version id"):
        version_binding.binding("", source=source)


def test_unversioned_refuses_to_carry_a_version() -> None:
    with pytest.raises(ValueError, match="cannot carry a version id"):
        version_binding.binding("v-3", source=version_binding.UNVERSIONED)


def test_an_unknown_source_is_refused_before_the_database_sees_it() -> None:
    with pytest.raises(ValueError, match="unknown object version source"):
        version_binding.binding("v-4", source="guessed")


# --- generation binding state -------------------------------------------------------------------


def test_every_object_bound_by_its_own_write_is_sealed() -> None:
    blobs = [_ref("a", version="v1", source="promotion"), _ref("b", version="v2", source="write")]
    assert archive.binding_state(blobs) == archive.BINDING_SEALED


def test_one_backfilled_binding_makes_the_generation_observed() -> None:
    blobs = [
        _ref("a", version="v1", source="promotion"),
        _ref("b", version="v2", source="backfill"),
    ]
    assert archive.binding_state(blobs) == archive.BINDING_OBSERVED


def test_one_unbound_object_makes_the_generation_partial() -> None:
    blobs = [_ref("a", version="v1", source="promotion"), _ref("b")]
    assert archive.binding_state(blobs) == archive.BINDING_PARTIAL


def test_unversioned_objects_neither_seal_nor_spoil_a_generation() -> None:
    """Renditions are derived and rebuildable, and their bucket has no versions at all."""
    blobs = [_ref("a", version="v1", source="promotion"), _ref("b", source="unversioned")]
    assert archive.binding_state(blobs) == archive.BINDING_SEALED

    only_renditions = [_ref("b", source="unversioned")]
    assert archive.binding_state(only_renditions) == archive.BINDING_ABSENT


def test_a_generation_with_no_blobs_is_absent_not_sealed() -> None:
    assert archive.binding_state([]) == archive.BINDING_ABSENT


# --- manifest -------------------------------------------------------------------------------------


def test_manifest_v3_carries_the_binding_and_the_generation_state() -> None:
    manifest = archive.build_manifest(
        [_ref("a", version="v1", source="promotion")], config={"table_counts": {"blob": 1}}
    )
    assert manifest["manifest_version"] == 3
    assert manifest["config"]["version_binding"] == archive.BINDING_SEALED
    assert manifest["config"]["table_counts"] == {"blob": 1}
    assert manifest["blobs"][0]["object_version_id"] == "v1"
    assert manifest["blobs"][0]["object_version_source"] == "promotion"


def test_a_v2_manifest_reads_back_as_unbound_rather_than_inferring_a_version() -> None:
    """A generation cannot become exact retroactively: no binding is read as no binding."""
    legacy = {
        "manifest_version": 2,
        "config": {},
        "blobs": [{"sha256": "a", "size_bytes": 3, "bucket": "documents", "object_key": "a"}],
    }
    refs = archive.blob_refs_from_manifest(legacy)
    assert refs == [_ref("a")]
    # The archive predates binding entirely, which is what an operator must see...
    assert archive.binding_state(refs, manifest_version=2) == archive.BINDING_ABSENT


def test_an_unbacked_current_generation_is_partial_not_absent() -> None:
    """...and is a DIFFERENT fact from a current generation whose rows were never backfilled.

    Judging by the blob list alone cannot tell these apart — both are entirely unbound — so the
    caller passes the manifest version it read. `partial` says "this generation has unbacked
    objects"; `absent` says "this archive predates version binding".
    """
    refs = [_ref("a")]
    assert archive.binding_state(refs, manifest_version=3) == archive.BINDING_PARTIAL
    assert archive.binding_state(refs) == archive.BINDING_PARTIAL


def test_manifest_round_trip_preserves_every_binding_field() -> None:
    blobs = [
        _ref("a", version="v1", source="promotion"),
        _ref("b", source="unversioned"),
        _ref("c", version="v3", source="backfill"),
    ]
    assert archive.blob_refs_from_manifest(archive.build_manifest(blobs, config={})) == blobs
