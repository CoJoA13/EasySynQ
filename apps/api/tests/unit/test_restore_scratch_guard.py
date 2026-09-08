from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from easysynq_api.config import Settings
from easysynq_api.services.backup import archive, drill, restore


def _settings(*, scratch_bucket: str) -> Settings:
    return Settings(
        database_url_sync="postgresql://owner@example.invalid/easysynq",
        backup_encryption_key="test-backup-key",
        s3_endpoint="https://objects.example.invalid",
        s3_region="test-region-1",
        s3_access_key="test-access-key",
        s3_secret_key="test-secret-key",
        s3_bucket_documents="test-documents-worm",
        s3_bucket_records="test-records-worm",
        s3_bucket_audit_checkpoints="test-audit-checkpoints-worm",
        s3_bucket_restore_scratch=scratch_bucket,
    )


def _run_operator_restore(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    scratch_bucket: str,
    blobs: list[dict[str, object]] | None = None,
    protected_buckets: set[str] | None = None,
    storage: _RecordingS3 | None = None,
    db_creations: list[str] | None = None,
) -> tuple[restore.RestoreResult, list[str], list[str]]:
    archive_path = tmp_path / "backup.tar"
    archive_path.write_bytes(b"archive")
    copy_buckets: list[str] = []
    cleanup_buckets: list[str] = []

    monkeypatch.setattr(archive, "verify_archive", lambda _src: True)
    monkeypatch.setattr(restore.crypto, "is_encrypted_archive", lambda _src: False)
    monkeypatch.setattr(
        archive,
        "read_manifest",
        lambda _src: {
            "blobs": blobs or [],
            "config": {"table_counts": {"organization": 1}},
            "legs": {},
        },
    )
    monkeypatch.setattr(archive, "unpack_dump", lambda _src, target: target / "db.dump")
    monkeypatch.setattr(restore, "_sweep_stale_restore", lambda _dsn: None)
    monkeypatch.setattr(
        drill,
        "_create_scratch_db",
        lambda _dsn, db: db_creations.append(db) if db_creations is not None else None,
    )
    monkeypatch.setattr(drill, "_drop_scratch_db", lambda _dsn, _db: None)
    monkeypatch.setattr(archive, "restore_database", lambda _dsn, _db, _dump: None)
    monkeypatch.setattr(
        drill,
        "_scratch_worm_bucket_names",
        lambda _settings, _dsn, _db: set(protected_buckets or set()),
    )
    if storage is None:
        monkeypatch.setattr(
            drill,
            "_delete_scratch_objects",
            lambda _settings, bucket, _prefix, **_kwargs: cleanup_buckets.append(bucket),
        )
        monkeypatch.setattr(
            drill,
            "_copy_blobs",
            lambda _settings, _blobs, bucket, _prefix, **_kwargs: copy_buckets.append(bucket),
        )
    else:
        monkeypatch.setattr(drill, "_s3", lambda _settings: storage)
    monkeypatch.setattr(
        drill,
        "run_triad",
        lambda _settings, _handle: drill.DrillResult("PASS", "restore verified"),
    )
    monkeypatch.setattr(restore, "_scratch_max_audit_id", lambda _dsn, _db: 0)
    monkeypatch.setattr(restore, "_scratch_max_bundled_checkpoint", lambda _dsn, _db: None)
    monkeypatch.setattr(
        restore,
        "_restored_org_id",
        lambda _dsn, _db: uuid.UUID("11111111-1111-1111-1111-111111111111"),
    )
    monkeypatch.setattr(restore, "_scratch_canonical_version", lambda _dsn, _db: 1)
    monkeypatch.setattr(
        restore,
        "_reverify_chain",
        lambda _dsn, _db, _version: {"verified": True, "attested": False},
    )

    result = restore.run_restore(
        _settings(scratch_bucket=scratch_bucket),
        archive_path=str(archive_path),
        fetch_off_host=lambda _settings, _org_id: 0,
    )
    return result, copy_buckets, cleanup_buckets


@pytest.mark.parametrize(
    "role_field",
    ["s3_bucket_records", "s3_bucket_audit_checkpoints"],
)
def test_operator_restore_rejects_every_non_document_worm_role_before_copy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    role_field: str,
) -> None:
    configured = _settings(scratch_bucket="test-safe-scratch")
    target = str(getattr(configured, role_field))

    result, copy_buckets, cleanup_buckets = _run_operator_restore(
        monkeypatch,
        tmp_path,
        scratch_bucket=target,
    )

    assert result.result == "FAIL", result
    assert copy_buckets == []
    assert cleanup_buckets == []


class _RecordingPaginator:
    def __init__(self, client: _RecordingS3) -> None:
        self.client = client

    def paginate(self, *, Bucket: str, Prefix: str) -> list[dict[str, object]]:
        self.client.events.append(("list", Bucket, Prefix))
        return [{"Contents": [{"Key": key} for key in self.client.listed_keys]}]


class _RecordingS3:
    def __init__(
        self,
        *,
        lock_response: object | None = None,
        lock_error: Exception | None = None,
        listed_keys: list[str] | None = None,
        fail_copy_number: int | None = None,
        events: list[tuple[object, ...]] | None = None,
    ) -> None:
        self.lock_response = lock_response
        self.lock_error = lock_error
        self.listed_keys = listed_keys or []
        self.fail_copy_number = fail_copy_number
        self.events = events if events is not None else []

    def get_object_lock_configuration(self, *, Bucket: str) -> object:
        self.events.append(("metadata", Bucket))
        if self.lock_error is not None:
            raise self.lock_error
        return self.lock_response

    def copy_object(self, **kwargs: object) -> None:
        self.events.append(("copy", kwargs))
        copy_count = sum(event[0] == "copy" for event in self.events)
        if copy_count == self.fail_copy_number:
            raise RuntimeError("copy failed")

    def get_paginator(self, operation: str) -> _RecordingPaginator:
        assert operation == "list_objects_v2"
        return _RecordingPaginator(self)

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        self.events.append(("delete", Bucket, Key))


class _CatalogCursor:
    def __init__(self, rows: list[object], queries: list[str]) -> None:
        self.rows = rows
        self.queries = queries

    def __enter__(self) -> _CatalogCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, query: str) -> None:
        self.queries.append(query)

    def fetchall(self) -> list[object]:
        return self.rows


class _CatalogConnection:
    def __init__(self, rows: list[object], queries: list[str]) -> None:
        self.rows = rows
        self.queries = queries

    def __enter__(self) -> _CatalogConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def cursor(self) -> _CatalogCursor:
        return _CatalogCursor(self.rows, self.queries)


class _RunnerCatalogCursor:
    def __init__(
        self,
        rows: list[object],
        events: list[tuple[object, ...]],
        *,
        query_fails: bool,
    ) -> None:
        self.rows = rows
        self.events = events
        self.query_fails = query_fails

    def __enter__(self) -> _RunnerCatalogCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, query: str) -> None:
        self.events.append(("catalog-query", query))
        if self.query_fails:
            raise RuntimeError("catalog query failed")

    def fetchall(self) -> list[object]:
        return self.rows


class _RunnerCatalogConnection:
    def __init__(
        self,
        rows: list[object],
        events: list[tuple[object, ...]],
        *,
        query_fails: bool,
    ) -> None:
        self.rows = rows
        self.events = events
        self.query_fails = query_fails

    def __enter__(self) -> _RunnerCatalogConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def cursor(self) -> _RunnerCatalogCursor:
        return _RunnerCatalogCursor(
            self.rows,
            self.events,
            query_fails=self.query_fails,
        )


class _RunnerHarness:
    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        *,
        runner: str,
        failure: str | None = None,
        table_counts: dict[str, int] | None = None,
    ) -> None:
        self.monkeypatch = monkeypatch
        self.tmp_path = tmp_path
        self.runner = runner
        self.failure = failure
        self.events: list[tuple[object, ...]] = []
        self.settings = _settings(scratch_bucket="test-safe-scratch")
        self.table_counts = {"organization": 1} if table_counts is None else table_counts
        self.blobs = [
            _blob(bucket="test-source-one", sha="a" * 64, key="vault/one"),
            _blob(bucket="test-source-one", sha="b" * 64, key="vault/two"),
        ]
        self.storage = _RecordingS3(
            lock_error=(
                _client_error("AccessDenied")
                if failure == "metadata"
                else _client_error("ObjectLockConfigurationNotFoundError")
            ),
            listed_keys=["scratch/copied-first-object"],
            fail_copy_number=2 if failure == "partial-copy" else None,
            events=self.events,
        )
        self._wire()

    def _wire(self) -> None:
        manifest_blobs = [
            {
                "sha256": blob.sha256,
                "size_bytes": blob.size_bytes,
                "bucket": blob.bucket,
                "object_key": blob.object_key,
            }
            for blob in self.blobs
        ]
        manifest = {
            "blobs": manifest_blobs,
            "config": {"table_counts": self.table_counts},
            "legs": {},
        }
        self.monkeypatch.setattr(archive, "verify_archive", lambda _src: True)
        self.monkeypatch.setattr(archive, "read_manifest", lambda _src: manifest)
        self.monkeypatch.setattr(
            archive,
            "unpack_dump",
            lambda _src, target: target / "db.dump",
        )
        self.monkeypatch.setattr(
            archive,
            "restore_database",
            self._restore_database,
        )
        self.monkeypatch.setattr(drill, "_s3", lambda _settings: self.storage)
        self.monkeypatch.setattr(drill, "_autocommit", self._autocommit)
        self.monkeypatch.setattr(drill, "_create_scratch_db", self._create_db)
        self.monkeypatch.setattr(drill, "_drop_scratch_db", self._drop_db)
        self.monkeypatch.setattr(drill, "_sweep_stale_scratch", lambda _dsn: None)
        self.monkeypatch.setattr(drill, "_sweep_stale_verify", lambda _dsn: None)
        self.monkeypatch.setattr(restore, "_sweep_stale_restore", lambda _dsn: None)
        self.monkeypatch.setattr(
            drill,
            "run_triad",
            lambda _settings, _handle: drill.DrillResult("PASS", "restore verified"),
        )
        self.monkeypatch.setattr(
            drill,
            "_capture_and_dump",
            lambda _dsn, _path: (self.table_counts, self.blobs),
        )
        self.monkeypatch.setattr(archive, "build_manifest", lambda *_args, **_kwargs: manifest)
        self.monkeypatch.setattr(
            archive,
            "pack_archive",
            lambda *_args, **_kwargs: self.tmp_path / "easysynq-backup-fresh.tar",
        )
        retained = self.tmp_path / "easysynq-backup-20260908T010203Z-deadbeef.tar"
        retained.write_bytes(b"archive")
        self.monkeypatch.setattr(drill, "_newest_retained_archive", lambda _dest: retained)
        self.monkeypatch.setattr(drill.crypto, "is_encrypted_archive", lambda _src: False)
        self.monkeypatch.setattr(restore.crypto, "is_encrypted_archive", lambda _src: False)
        self.monkeypatch.setattr(restore, "_scratch_max_audit_id", lambda _dsn, _db: 0)
        self.monkeypatch.setattr(
            restore,
            "_scratch_max_bundled_checkpoint",
            lambda _dsn, _db: None,
        )
        self.monkeypatch.setattr(
            restore,
            "_restored_org_id",
            lambda _dsn, _db: uuid.UUID("11111111-1111-1111-1111-111111111111"),
        )
        self.monkeypatch.setattr(restore, "_scratch_canonical_version", lambda _dsn, _db: 1)
        self.monkeypatch.setattr(
            restore,
            "_reverify_chain",
            lambda _dsn, _db, _version: {"verified": True, "attested": False},
        )

    def _create_db(self, _dsn: str, dbname: str) -> None:
        self.events.append(("create", dbname))

    def _restore_database(self, _dsn: str, dbname: str, _dump: Path) -> None:
        self.events.append(("restore", dbname))
        if self.failure == "partial-pg":
            raise RuntimeError("partial database restore")

    def _autocommit(self, _dsn: str, *, dbname: str | None = None) -> _RunnerCatalogConnection:
        self.events.append(("catalog", dbname))
        if self.failure == "catalog-connect":
            raise RuntimeError("catalog connection failed")
        rows: list[object] = []
        if self.failure == "custom-role":
            rows = [({"bucket": self.settings.s3_bucket_restore_scratch},)]
        return _RunnerCatalogConnection(
            rows,
            self.events,
            query_fails=self.failure == "catalog-query",
        )

    def _drop_db(self, _dsn: str, dbname: str) -> None:
        self.events.append(("drop", dbname))

    def run(self) -> drill.DrillResult | restore.RestoreResult:
        if self.runner == "fresh":
            return drill.run_drill(self.settings, destination=str(self.tmp_path))
        if self.runner == "retained":
            return drill.verify_retained_archive(self.settings, destination=str(self.tmp_path))
        source = self.tmp_path / "operator-backup.tar"
        source.write_bytes(b"archive")
        return restore.run_restore(
            self.settings,
            archive_path=str(source),
            fetch_off_host=lambda _settings, _org_id: 0,
        )


def _client_error(code: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": "test storage error"}},
        "GetObjectLockConfiguration",
    )


def _blob(*, bucket: str, sha: str = "a" * 64, key: str = "objects/source") -> archive.BlobRef:
    return archive.BlobRef(sha256=sha, size_bytes=4, bucket=bucket, object_key=key)


def test_copy_checks_the_complete_source_set_before_opening_s3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(scratch_bucket="test-safe-scratch")
    blobs = [
        _blob(bucket="test-source-one", sha="a" * 64),
        _blob(bucket=settings.s3_bucket_restore_scratch, sha="b" * 64),
    ]
    monkeypatch.setattr(
        drill,
        "_s3",
        lambda _settings: pytest.fail("source collision must be rejected before S3 is opened"),
    )

    with pytest.raises(archive.BackupError, match="protected bucket role"):
        drill._copy_blobs(
            settings,
            blobs,
            settings.s3_bucket_restore_scratch,
            "run/",
            protected_buckets=set(),
        )


def test_copy_preserves_the_source_locator_and_scratch_hash_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(scratch_bucket="test-safe-scratch")
    client = _RecordingS3()
    monkeypatch.setattr(drill, "_s3", lambda _settings: client)
    blob = _blob(bucket="test-source-one", sha="c" * 64, key="vault/exact-key")

    drill._copy_blobs(
        settings,
        [blob],
        settings.s3_bucket_restore_scratch,
        "run-123/",
        protected_buckets=set(),
    )

    assert client.events == [
        (
            "copy",
            {
                "Bucket": "test-safe-scratch",
                "Key": f"run-123/{'c' * 64}",
                "CopySource": {"Bucket": "test-source-one", "Key": "vault/exact-key"},
            },
        )
    ]


@pytest.mark.parametrize(
    ("lock_response", "lock_error"),
    [
        ({"ObjectLockConfiguration": {"ObjectLockEnabled": "Enabled"}}, None),
        (
            {
                "ObjectLockConfiguration": {
                    "ObjectLockEnabled": "Enabled",
                    "Rule": {"DefaultRetention": {"Mode": "GOVERNANCE", "Days": 30}},
                }
            },
            None,
        ),
        ({}, None),
        ({"ObjectLockConfiguration": {}}, None),
        (None, _client_error("AccessDenied")),
        (None, _client_error("NoSuchBucket")),
        (None, _client_error("NoSuchObjectLockConfiguration")),
        (None, RuntimeError("transport failed")),
    ],
)
def test_metadata_preflight_fails_closed_without_object_mutation(
    monkeypatch: pytest.MonkeyPatch,
    lock_response: object | None,
    lock_error: Exception | None,
) -> None:
    settings = _settings(scratch_bucket="test-safe-scratch")
    client = _RecordingS3(lock_response=lock_response, lock_error=lock_error)
    monkeypatch.setattr(drill, "_s3", lambda _settings: client)

    with pytest.raises(archive.BackupError):
        drill._preflight_scratch_target(
            settings,
            settings.s3_bucket_restore_scratch,
            source_buckets=set(),
            protected_buckets=set(),
        )

    assert client.events == [("metadata", "test-safe-scratch")]


def test_metadata_preflight_accepts_only_the_bucket_operation_absent_lock_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(scratch_bucket="test-safe-scratch")
    client = _RecordingS3(lock_error=_client_error("ObjectLockConfigurationNotFoundError"))
    monkeypatch.setattr(drill, "_s3", lambda _settings: client)

    target = drill._preflight_scratch_target(
        settings,
        settings.s3_bucket_restore_scratch,
        source_buckets=set(),
        protected_buckets=set(),
    )

    assert target == "test-safe-scratch"
    assert client.events == [("metadata", "test-safe-scratch")]


def test_cleanup_rechecks_metadata_before_listing_or_deleting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(scratch_bucket="test-safe-scratch")
    client = _RecordingS3(
        lock_response={"ObjectLockConfiguration": {"ObjectLockEnabled": "Enabled"}},
        listed_keys=["run/object"],
    )
    monkeypatch.setattr(drill, "_s3", lambda _settings: client)

    with pytest.raises(archive.BackupError, match="protected bucket role"):
        drill._delete_scratch_objects(
            settings,
            settings.s3_bucket_restore_scratch,
            "run/",
            source_buckets=set(),
            protected_buckets=set(),
        )

    assert client.events == [("metadata", "test-safe-scratch")]


@pytest.mark.parametrize(
    ("target", "source_buckets", "protected_buckets"),
    [
        ("test-documents-worm", set(), set()),
        ("test-safe-scratch", {"test-safe-scratch"}, set()),
        ("test-safe-scratch", set(), {"test-safe-scratch"}),
    ],
)
def test_cleanup_rejects_every_known_protected_name_before_opening_s3(
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    source_buckets: set[str],
    protected_buckets: set[str],
) -> None:
    settings = _settings(scratch_bucket=target)
    monkeypatch.setattr(
        drill,
        "_s3",
        lambda _settings: pytest.fail("known protected name must not open S3"),
    )

    with pytest.raises(archive.BackupError, match="protected bucket role"):
        drill._delete_scratch_objects(
            settings,
            target,
            "run/",
            source_buckets=source_buckets,
            protected_buckets=protected_buckets,
        )


def test_cleanup_deletes_only_the_supplied_prefix_after_safe_recheck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(scratch_bucket="test-safe-scratch")
    client = _RecordingS3(
        lock_error=_client_error("ObjectLockConfigurationNotFoundError"),
        listed_keys=["run/object-one", "run/object-two"],
    )
    monkeypatch.setattr(drill, "_s3", lambda _settings: client)

    drill._delete_scratch_objects(
        settings,
        settings.s3_bucket_restore_scratch,
        "run/",
        source_buckets={"test-source-one"},
        protected_buckets={"test-custom-worm"},
    )

    assert client.events == [
        ("metadata", "test-safe-scratch"),
        ("list", "test-safe-scratch", "run/"),
        ("delete", "test-safe-scratch", "run/object-one"),
        ("delete", "test-safe-scratch", "run/object-two"),
    ]


def test_catalog_reads_all_worm_roles_from_the_named_scratch_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(scratch_bucket="test-safe-scratch")
    queries: list[str] = []
    dbnames: list[str | None] = []
    rows: list[object] = [
        ({"bucket": "test-custom-disabled", "endpoint": "https://other.invalid"},),
        ({"bucket": "test-custom-other-org"},),
        ({},),
        (None,),
    ]

    def _connect(_dsn: str, *, dbname: str | None = None) -> _CatalogConnection:
        dbnames.append(dbname)
        return _CatalogConnection(rows, queries)

    monkeypatch.setattr(drill, "_autocommit", _connect)

    protected = drill._scratch_worm_bucket_names(
        settings,
        settings.sync_dsn,
        "restore_easysynq_test",
    )

    assert dbnames == ["restore_easysynq_test"]
    assert queries == [
        "SELECT connection FROM public.audit_checkpoint_sink WHERE kind = 'worm_bucket'"
    ]
    assert protected == {
        "test-custom-disabled",
        "test-custom-other-org",
        "test-audit-checkpoints-worm",
    }


def test_catalog_accepts_a_genuinely_empty_valid_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(scratch_bucket="test-safe-scratch")
    monkeypatch.setattr(
        drill,
        "_autocommit",
        lambda _dsn, *, dbname=None: _CatalogConnection([], []),
    )

    assert drill._scratch_worm_bucket_names(settings, settings.sync_dsn, "scratch-db") == set()


@pytest.mark.parametrize("bucket_override", [False, 0, "", []])
def test_catalog_preserves_falsey_bucket_override_fallback_inside_valid_object(
    monkeypatch: pytest.MonkeyPatch,
    bucket_override: object,
) -> None:
    settings = _settings(scratch_bucket="test-safe-scratch")
    monkeypatch.setattr(
        drill,
        "_autocommit",
        lambda _dsn, *, dbname=None: _CatalogConnection([({"bucket": bucket_override},)], []),
    )

    assert drill._scratch_worm_bucket_names(settings, settings.sync_dsn, "scratch-db") == {
        settings.s3_bucket_audit_checkpoints
    }


@pytest.mark.parametrize(
    "row",
    [
        (False,),
        (0,),
        ("",),
        ([],),
        ("nonempty-non-object",),
        ({"bucket": ["invalid"]},),
        ({"bucket": "Invalid Bucket"},),
        (None, None),
    ],
)
def test_catalog_rejects_malformed_worm_role_data(
    monkeypatch: pytest.MonkeyPatch,
    row: object,
) -> None:
    settings = _settings(scratch_bucket="test-safe-scratch")
    monkeypatch.setattr(
        drill,
        "_autocommit",
        lambda _dsn, *, dbname=None: _CatalogConnection([row], []),
    )

    with pytest.raises(archive.BackupError, match="cannot verify"):
        drill._scratch_worm_bucket_names(settings, settings.sync_dsn, "scratch-db")


@pytest.mark.parametrize(
    "role_field",
    ["s3_bucket_documents", "s3_bucket_records", "s3_bucket_audit_checkpoints"],
)
@pytest.mark.parametrize("runner", ["fresh", "retained"])
def test_drill_runners_reject_every_static_worm_role_before_db_creation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    role_field: str,
    runner: str,
) -> None:
    configured = _settings(scratch_bucket="test-safe-scratch")
    target = str(getattr(configured, role_field))
    settings = _settings(scratch_bucket=target)
    db_creations: list[str] = []
    monkeypatch.setattr(
        drill,
        "_create_scratch_db",
        lambda _dsn, dbname: db_creations.append(dbname),
    )
    monkeypatch.setattr(archive, "verify_archive", lambda _src: True)

    if runner == "fresh":
        monkeypatch.setattr(drill, "_capture_and_dump", lambda _dsn, _path: ({}, []))
        monkeypatch.setattr(archive, "build_manifest", lambda *_args, **_kwargs: {})
        monkeypatch.setattr(
            archive,
            "pack_archive",
            lambda *_args, **_kwargs: tmp_path / "easysynq-backup-test.tar",
        )
        result = drill.run_drill(settings, destination=str(tmp_path))
    else:
        retained = tmp_path / "easysynq-backup-20260908T010203Z-deadbeef.tar"
        retained.write_bytes(b"archive")
        monkeypatch.setattr(drill, "_newest_retained_archive", lambda _destination: retained)
        monkeypatch.setattr(drill.crypto, "is_encrypted_archive", lambda _src: False)
        monkeypatch.setattr(
            archive,
            "read_manifest",
            lambda _src: {"blobs": [], "config": {"table_counts": {}}},
        )
        result = drill.verify_retained_archive(settings, destination=str(tmp_path))

    assert result.result == "FAIL", result
    assert "protected bucket role" in result.reason
    assert db_creations == []


@pytest.mark.parametrize("runner", ["operator", "fresh", "retained"])
@pytest.mark.parametrize(
    ("failure", "expected_events"),
    [
        ("partial-pg", ["create", "restore", "drop"]),
        ("catalog-connect", ["create", "restore", "catalog", "drop"]),
        (
            "catalog-query",
            ["create", "restore", "catalog", "catalog-query", "drop"],
        ),
        ("custom-role", ["create", "restore", "catalog", "catalog-query", "drop"]),
        (
            "metadata",
            ["create", "restore", "catalog", "catalog-query", "metadata", "drop"],
        ),
    ],
)
def test_every_runner_failure_before_copy_skips_object_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runner: str,
    failure: str,
    expected_events: list[str],
) -> None:
    harness = _RunnerHarness(monkeypatch, tmp_path, runner=runner, failure=failure)

    result = harness.run()

    assert result.result == "FAIL", result
    assert [str(event[0]) for event in harness.events] == expected_events
    assert not {"copy", "list", "delete"}.intersection(expected_events)


@pytest.mark.parametrize("runner", ["operator", "fresh", "retained"])
def test_every_runner_orders_restore_catalog_metadata_then_copy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runner: str,
) -> None:
    harness = _RunnerHarness(monkeypatch, tmp_path, runner=runner)

    result = harness.run()

    assert result.result == "PASS", result
    significant = [
        str(event[0])
        for event in harness.events
        if event[0] in {"restore", "catalog", "metadata", "copy"}
    ]
    assert significant[:5] == ["restore", "catalog", "metadata", "copy", "copy"]


@pytest.mark.parametrize("runner", ["operator", "fresh", "retained"])
def test_every_runner_cleans_a_partial_copy_only_after_completed_preflight(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runner: str,
) -> None:
    harness = _RunnerHarness(monkeypatch, tmp_path, runner=runner, failure="partial-copy")

    result = harness.run()

    assert result.result == "FAIL", result
    assert [str(event[0]) for event in harness.events] == [
        "create",
        "restore",
        "catalog",
        "catalog-query",
        "metadata",
        "copy",
        "copy",
        "drop",
        "metadata",
        "list",
        "delete",
    ]


def test_operator_legacy_manifest_without_counts_uses_valid_catalog_guard(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    harness = _RunnerHarness(
        monkeypatch,
        tmp_path,
        runner="operator",
        table_counts={},
    )

    result = harness.run()

    assert result.result == "PASS", result
    assert result.triad["row_count_parity"] == "skipped (legacy archive, no manifest counts)"
    assert "catalog-query" in [str(event[0]) for event in harness.events]


def test_discard_reads_guard_inputs_before_drop_and_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(scratch_bucket="test-safe-scratch")
    events: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        drill,
        "_scratch_worm_bucket_names",
        lambda _settings, _dsn, db: events.append(("catalog", db)) or {"test-custom-worm"},
    )
    monkeypatch.setattr(
        drill,
        "_scratch_blob_locators",
        lambda handle: (
            events.append(("locators", handle.scratch_db))
            or [("a" * 64, "test-source-one", "vault/key")]
        ),
    )
    monkeypatch.setattr(
        drill,
        "_preflight_scratch_target",
        lambda _settings, bucket, **_kwargs: events.append(("preflight", bucket)) or bucket,
    )
    monkeypatch.setattr(
        drill,
        "_drop_scratch_db",
        lambda _dsn, db: events.append(("drop", db)),
    )
    monkeypatch.setattr(
        drill,
        "_delete_scratch_objects",
        lambda _settings, bucket, prefix, **kwargs: events.append(
            ("delete", bucket, prefix, kwargs)
        ),
    )

    restore.discard_target(settings, "restore_easysynq_run123")

    assert [event[0] for event in events] == [
        "catalog",
        "locators",
        "preflight",
        "drop",
        "delete",
    ]
    assert events[-1] == (
        "delete",
        "test-safe-scratch",
        "run123/",
        {
            "source_buckets": {"test-source-one"},
            "protected_buckets": {"test-custom-worm"},
        },
    )


def test_discard_still_drops_db_but_skips_objects_when_catalog_read_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(scratch_bucket="test-safe-scratch")
    events: list[tuple[str, str]] = []

    def _catalog_failure(_settings: Settings, _dsn: str, _db: str) -> set[str]:
        raise RuntimeError("catalog unavailable")

    monkeypatch.setattr(drill, "_scratch_worm_bucket_names", _catalog_failure)
    monkeypatch.setattr(
        drill,
        "_drop_scratch_db",
        lambda _dsn, db: events.append(("drop", db)),
    )
    monkeypatch.setattr(
        drill,
        "_delete_scratch_objects",
        lambda *_args, **_kwargs: events.append(("delete", "unexpected")),
    )

    restore.discard_target(settings, "restore_easysynq_run123")

    assert events == [("drop", "restore_easysynq_run123")]


def test_operator_restore_does_not_clean_objects_when_documents_role_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configured = _settings(scratch_bucket="test-safe-scratch")

    result, copy_buckets, cleanup_buckets = _run_operator_restore(
        monkeypatch,
        tmp_path,
        scratch_bucket=configured.s3_bucket_documents,
    )

    assert result.result == "FAIL", result
    assert copy_buckets == []
    assert cleanup_buckets == []


def test_operator_restore_rejects_restored_custom_role_before_metadata_or_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = "test-custom-worm"
    storage = _RecordingS3(lock_error=_client_error("ObjectLockConfigurationNotFoundError"))

    result, _, _ = _run_operator_restore(
        monkeypatch,
        tmp_path,
        scratch_bucket=target,
        protected_buckets={target},
        storage=storage,
    )

    assert result.result == "FAIL", result
    assert storage.events == []


def test_operator_restore_checks_metadata_even_for_an_empty_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    storage = _RecordingS3(lock_error=_client_error("ObjectLockConfigurationNotFoundError"))

    result, _, _ = _run_operator_restore(
        monkeypatch,
        tmp_path,
        scratch_bucket="test-safe-scratch",
        storage=storage,
    )

    assert result.result == "PASS", result
    assert storage.events == [("metadata", "test-safe-scratch")]


def test_operator_restore_metadata_failure_never_authorizes_copy_or_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    storage = _RecordingS3(lock_error=_client_error("AccessDenied"), listed_keys=["run/object"])

    result, _, _ = _run_operator_restore(
        monkeypatch,
        tmp_path,
        scratch_bucket="test-safe-scratch",
        blobs=[
            {
                "sha256": "a" * 64,
                "size_bytes": 4,
                "bucket": "test-source-one",
                "object_key": "vault/source",
            }
        ],
        storage=storage,
    )

    assert result.result == "FAIL", result
    assert storage.events == [("metadata", "test-safe-scratch")]


def test_operator_restore_partial_copy_enables_guarded_prefix_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    storage = _RecordingS3(
        lock_error=_client_error("ObjectLockConfigurationNotFoundError"),
        listed_keys=["run/copied-first-object"],
        fail_copy_number=2,
    )
    blobs = [
        {
            "sha256": "a" * 64,
            "size_bytes": 4,
            "bucket": "test-source-one",
            "object_key": "vault/one",
        },
        {
            "sha256": "b" * 64,
            "size_bytes": 4,
            "bucket": "test-source-one",
            "object_key": "vault/two",
        },
    ]

    result, _, _ = _run_operator_restore(
        monkeypatch,
        tmp_path,
        scratch_bucket="test-safe-scratch",
        blobs=blobs,
        storage=storage,
    )

    assert result.result == "FAIL", result
    assert [event[0] for event in storage.events] == [
        "metadata",
        "copy",
        "copy",
        "metadata",
        "list",
        "delete",
    ]
    assert storage.events[-1][1] == "test-safe-scratch"


def test_operator_restore_rejects_a_later_source_collision_before_db_creation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_creations: list[str] = []
    result, copy_buckets, cleanup_buckets = _run_operator_restore(
        monkeypatch,
        tmp_path,
        scratch_bucket="test-safe-scratch",
        blobs=[
            {
                "sha256": "a" * 64,
                "size_bytes": 4,
                "bucket": "test-source-one",
                "object_key": "vault/one",
            },
            {
                "sha256": "b" * 64,
                "size_bytes": 4,
                "bucket": "test-safe-scratch",
                "object_key": "vault/two",
            },
        ],
        db_creations=db_creations,
    )

    assert result.result == "FAIL", result
    assert db_creations == []
    assert copy_buckets == []
    assert cleanup_buckets == []
