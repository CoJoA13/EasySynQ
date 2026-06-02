"""S8b2 unit proofs — the backup DSN translation + archive pack/verify/unpack + the new event_type
values (no DB / MinIO / pg_dump).

The DB+MinIO-bound drill (the full backup→restore-into-scratch + integrity triad, gate G-C, AC#5)
is proven in ``tests/integration/test_backup.py`` + ``test_setup.py``; here we pin the pure, easily
broken parts: connection-param parsing (a wrong PGHOST/PGPASSWORD silently makes the drill connect
to the wrong place), the archive checksum round-trip (the 'checksum verified' leg of the drill),
and the enum guard (a missing Python EventType member is a runtime crash — see 0011/0012).
"""

from __future__ import annotations

import tarfile
from pathlib import Path

from easysynq_api.db.models._audit_enums import EVENT_TYPE_VALUES, EventType
from easysynq_api.services.backup import archive
from easysynq_api.services.backup.archive import BlobRef
from easysynq_api.services.backup.dsn import conn_kwargs, database_name, libpq_env

_DSN = "postgresql+psycopg://easysynq:s3cr3t@postgres:5432/easysynq"


def test_libpq_env_translates_sqlalchemy_url() -> None:
    env = libpq_env(_DSN)
    assert env["PGHOST"] == "postgres"
    assert env["PGPORT"] == "5432"
    assert env["PGUSER"] == "easysynq"
    assert env["PGPASSWORD"] == "s3cr3t"
    assert env["PGDATABASE"] == "easysynq"


def test_libpq_env_overrides_database() -> None:
    """The scratch restore targets a different DB on the same host (everything else unchanged)."""
    env = libpq_env(_DSN, dbname="scratch_easysynq_abc")
    assert env["PGDATABASE"] == "scratch_easysynq_abc"
    assert env["PGHOST"] == "postgres"


def test_conn_kwargs_and_database_name() -> None:
    kw = conn_kwargs(_DSN, dbname="scratch_x")
    assert kw["host"] == "postgres"
    assert kw["dbname"] == "scratch_x"
    assert database_name(_DSN) == "easysynq"


def test_libpq_env_url_decodes_credentials() -> None:
    """A percent-encoded password (e.g. an '@' in the secret) is decoded for libpq."""
    env = libpq_env("postgresql+psycopg://u:p%40ss@h:5432/db")
    assert env["PGPASSWORD"] == "p@ss"


def test_build_manifest_lists_blob_snapshot() -> None:
    blobs = [BlobRef(sha256="a" * 64, size_bytes=10, bucket="documents", object_key="a" * 64)]
    m = archive.build_manifest(blobs, config={"source": "restore-drill", "blob_count": 1})
    assert m["manifest_version"] == 1
    assert m["config"]["blob_count"] == 1
    assert m["blobs"][0]["sha256"] == "a" * 64
    assert m["blobs"][0]["bucket"] == "documents"


def test_pack_verify_unpack_roundtrip(tmp_path: Path) -> None:
    """pack_archive → verify_archive (checksum match) → unpack_dump recovers the dump bytes."""
    dump = tmp_path / "db.dump"
    dump.write_bytes(b"PGDMP-fake-custom-format-bytes")
    dest = tmp_path / "dest"
    manifest = archive.build_manifest([], config={"source": "scheduled-backup", "blob_count": 0})

    arc = archive.pack_archive(dump, manifest, dest, stamp="20260602T000000Z-deadbeef")
    assert arc.exists()
    assert arc.with_suffix(".tar.sha256").exists()
    assert archive.verify_archive(arc) is True

    out = tmp_path / "restore"
    recovered = archive.unpack_dump(arc, out)
    assert recovered.read_bytes() == b"PGDMP-fake-custom-format-bytes"
    # the manifest rides in the archive too
    with tarfile.open(arc) as tar:
        assert "manifest.json" in tar.getnames()


def test_verify_archive_detects_corruption(tmp_path: Path) -> None:
    """A flipped byte in the archive (or a missing checksum sidecar) fails verification — the leg
    that catches a corrupt backup before the restore even starts."""
    dump = tmp_path / "db.dump"
    dump.write_bytes(b"original")
    arc = archive.pack_archive(dump, archive.build_manifest([], config={}), tmp_path, stamp="s")
    assert archive.verify_archive(arc) is True

    arc.write_bytes(arc.read_bytes() + b"tampered")
    assert archive.verify_archive(arc) is False

    arc.with_suffix(".tar.sha256").unlink()
    assert archive.verify_archive(arc) is False


def test_new_event_types_present() -> None:
    """0014's three ALTER TYPE ADD VALUEs must also be Python EventType members, or a from-scratch
    ``upgrade head`` (which rebuilds the type from EVENT_TYPE_VALUES) drops them → inserts crash."""
    for name in ("BACKUP_CONFIGURED", "RESTORE_TEST_PASSED", "RESTORE_TEST_FAILED"):
        assert EventType(name).value == name
        assert name in EVENT_TYPE_VALUES
