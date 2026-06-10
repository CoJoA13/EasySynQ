"""S-drift-3 unit proofs — the D1 classification matrix, salvage-on-abort, and report semantics.

The hasher is injected (no MinIO): a mapping-backed fake returns digests; botocore exceptions are
constructed for the error rows. ``verify_rows`` NEVER raises — an infrastructure-class failure
aborts with (findings-so-far, ok-so-far, error) so the caller reports an honest FAILED that
salvages what was collected (MinIO-down must not mint hundreds of noise findings).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from botocore.exceptions import ClientError, EndpointConnectionError

from easysynq_api.services.vault.blob_verify import (
    CLASS_MISMATCH,
    CLASS_MISSING,
    CLASS_READ_ERROR,
    build_report,
    verify_rows,
)

Row = tuple[str, str, str, int]
_B = "documents"


def _row(sha: str, key: str = "k", size: int = 4) -> Row:
    return (sha, _B, key, size)


def _hasher(mapping: dict[str, str | Exception]) -> Callable[[str, str], Awaitable[str]]:
    async def h(object_key: str, bucket: str) -> str:
        outcome = mapping[object_key]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return h


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code}}, "GetObject")


async def test_matching_digest_is_ok_and_stamped() -> None:
    findings, ok, error = await verify_rows([_row("aa")], _hasher({"k": "aa"}))
    assert (findings, ok, error) == ([], ["aa"], None)


async def test_mismatch_classifies_and_carries_found_digest() -> None:
    findings, ok, error = await verify_rows([_row("aa")], _hasher({"k": "bb"}))
    assert error is None and ok == []
    assert findings[0].classification == CLASS_MISMATCH
    assert findings[0].found_sha256 == "bb"
    assert findings[0].sha256 == "aa"


async def test_nosuchkey_is_object_missing_never_skipped() -> None:
    findings, _ok, error = await verify_rows(
        [_row("aa")], _hasher({"k": _client_error("NoSuchKey")})
    )
    assert error is None
    assert findings[0].classification == CLASS_MISSING


async def test_object_scoped_client_error_is_read_error_finding() -> None:
    findings, _ok, error = await verify_rows(
        [_row("aa")], _hasher({"k": _client_error("AccessDenied")})
    )
    assert error is None
    assert findings[0].classification == CLASS_READ_ERROR
    assert findings[0].note == "AccessDenied"


async def test_connection_failure_aborts_and_salvages() -> None:
    """Row 1 mismatches (collected), row 2 hits a connection-class error → abort: the report is
    FAILED, the mismatch finding survives, and row 3 is NEVER reached (no noise findings)."""
    reached: list[str] = []

    async def h(object_key: str, bucket: str) -> str:
        reached.append(object_key)
        if object_key == "k1":
            return "zz"
        raise EndpointConnectionError(endpoint_url="http://minio:9000")

    rows = [_row("aa", "k1"), _row("bb", "k2"), _row("cc", "k3")]
    findings, ok, error = await verify_rows(rows, h)
    assert reached == ["k1", "k2"]
    assert [f.classification for f in findings] == [CLASS_MISMATCH]
    assert ok == [] and error is not None


async def test_unexpected_exception_also_aborts_not_raises() -> None:
    findings, ok, error = await verify_rows([_row("aa")], _hasher({"k": RuntimeError("boom")}))
    assert findings == [] and ok == []
    assert error is not None and "RuntimeError" in error


async def test_build_report_statuses_and_counts() -> None:
    clean = build_report(findings=[], ok_shas=["a", "b"], total_blobs=9, sample_limit=2)
    assert clean.status == "CLEAN"
    assert clean.counts()["ok"] == 2
    assert clean.counts()["stamped"] == 2
    assert clean.counts()["full"] is False

    findings, ok, _ = await verify_rows([_row("aa")], _hasher({"k": "bb"}))
    divergent = build_report(findings=findings, ok_shas=ok, total_blobs=9, sample_limit=None)
    assert divergent.status == "DIVERGENT"
    c = divergent.counts()
    assert c["mismatched"] == 1 and c["full"] is True and c["scanned"] == 1

    failed = build_report(findings=findings, ok_shas=[], total_blobs=9, sample_limit=5, error="x")
    assert failed.status == "FAILED"
    assert failed.counts()["error"] == "x"


def test_report_is_failed_even_with_zero_findings_when_error() -> None:
    r = build_report(findings=[], ok_shas=[], total_blobs=0, sample_limit=None, error="pg down")
    assert r.status == "FAILED"


def test_sample_stmt_orders_nulls_first_then_oldest() -> None:
    """The rotation contract lives in the SQL: never-verified rows first, then oldest stamps,
    deterministic sha tiebreak — compiled against the postgresql dialect so the assertion checks
    what PG will actually execute."""
    from sqlalchemy.dialects import postgresql

    from easysynq_api.services.vault.blob_verify import _sample_stmt

    sql = str(_sample_stmt(limit=5).compile(dialect=postgresql.dialect()))
    assert "ORDER BY blob.verified_at ASC NULLS FIRST, blob.sha256" in sql
    assert "LIMIT" in sql
    full_sql = str(_sample_stmt(limit=None).compile(dialect=postgresql.dialect()))
    assert "LIMIT" not in full_sql
