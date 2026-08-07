# S-upload-identity — Version-Bound Upload Verification — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bind every non-deduplicated document, Record, import, and server-generated WORM promotion to one exact staging object version, stream-hash that version before copy, and durably audit a refusal before deleting only the rejected version.

**Architecture:** Add a closed, immutable staged-object contract in `services/vault/staged_identity.py`; replace the key-latest `finalize_worm` flow with one exact-version GET/hash/adopt-or-copy/retention boundary in `services/vault/storage.py`; put rollback → fresh audit commit → exact cleanup in `services/vault/upload_rejection.py`; and adapt each producer without persisting the target WORM `VersionId`. `staging` and `import-staging` become versioned, readiness fails closed if either is not `Enabled`, browser PUT exposes `x-amz-version-id`, and import locators pin the version in the existing text URI. Existing correct-domain WORM dedup bypasses staging entirely.

**Tech Stack:** Python 3.12, FastAPI/Pydantic v2, SQLAlchemy async, boto3/botocore, Celery, PostgreSQL 16, MinIO S3 API, React 19/TypeScript, TanStack Query, Vitest/MSW, pytest/testcontainers, OpenAPI/Redocly. Design authority: `docs/superpowers/specs/2026-08-06-s-upload-identity-design.md`.

## Global Constraints

- This is a **zero-migration** slice. `cd apps/api && uv run alembic heads` must remain `0085`; do not add a revision or column.
- Do not persist the durable target WORM `VersionId`; it is required in `PromotionResult` only. Durable target-version ownership belongs to `S-worm-retention`.
- Do not add transfer ceilings, multipart browser orchestration, staging lifecycle expiry, global Blob redesign, principal/retention changes, or abandoned-draft/import cleanup.
- A staging `version_id` is opaque, 1–1024 characters, and neither empty nor the literal `"null"`. Never parse an ordering or timestamp from it.
- The only staging domains are `STAGING` and `IMPORT_STAGING`. API clients provide a version ID only; server code derives domain and key from the endpoint and claimed lowercase SHA-256.
- Every source read, copy, and rejected/temp cleanup supplies `VersionId`. No fallback may GET/COPY/DELETE staging by key-latest after cutover. A key-only HEAD is allowed solely to discover an existing canonical import or target version; the next read pins the returned version and verifies it, and HEAD errors other than true absence never authorize creation. Every secure verify/PUT path also requires the logical source bucket's current versioning status to be exact `Enabled`; exact cleanup does not require `Enabled` and remains safe during suspension.
- SHA-256 is computed server-side over 1 MiB chunks. ETag is only an opaque `CopySourceIfMatch` precondition; client checksum headers and multipart ETags are not content authority.
- A refusal sequence is always: roll back owner transaction → commit a fresh rejection audit/failure ledger → delete the exact rejected version. If audit commit fails, do not delete. If delete fails, enqueue the exact-version retry.
- A document mismatch preserves `working_draft`, metadata, scratch SHA marker, and Redis check-out lock. An import mismatch preserves its run, file, classifications, and decisions.
- Existing correct-domain WORM Blob dedup requires no staging version because it performs no staging promotion.
- Keep the existing Record `(bucket, object_key)` transaction advisory locks, sorted by the actual PostgreSQL lock keys, held through owner commit. Introduce no second lock order.
- Runtime metric signals are fixed-schema structured log events (`upload_identity.metric`) because this repository has no metrics backend or `/metrics` route. Labels are bounded enums only: operation, classification, logical staging domain, storage stage, and outcome. Never label hashes, keys, versions, filenames, users, or orgs.
- `EASYSYNQ_COMPATIBILITY_READ_ONLY=1` is the rollback interlock for an exact-version-incompatible API: Caddy rejects mutating `/api/*` methods before proxying, and `worker`/`beat` remain stopped. It never disables staging versioning or blocks existing vault reads.
- Tasks 1–3 establish the shared boundary and must execute serially. Tasks 4–8 also touch shared caller surfaces; do not run implementers for them concurrently in the shared checkout.
- Tasks 1–7 may retain an explicitly named migration adapter for still-unmigrated internal callers so each intermediate commit type-checks and existing unrelated tests keep running. Task 8 must delete every adapter and its key-latest implementation before the full gates; no adapter may reach review/merge.
- Every command block assumes repository root at the start. Run each `cd apps/... && ...` line as its own invocation (or set that invocation's working directory); do not paste multiple such lines into one shell whose changed directory persists.
- After each task is green, run `git diff --check`, inspect `git status --short`, and commit only that task's files with the listed subject.

---

## Exact Interface and Ownership Map

| Surface | New/changed interface | Owning task |
|---|---|---|
| `services/vault/staged_identity.py` | `StagingDomain`, `StagedVersionLocator`, `StagedObjectRef`, `VerifiedStagedObject`, `PromotionOutcome`, `PromotionResult`, typed errors | 1 |
| `services/vault/storage.py` | versioning guard, `_finalize_sync(source, target_bucket, ...)`, `promote_worm`, `verify_staged`, `put_staging_bytes -> StagedObjectRef`, `delete_staged_version(locator)` | 1 |
| MinIO/readiness/rollback edge | version both staging buckets; CORS exposes PUT version header; `/readyz` checks both statuses; edge can block mutating API methods during an incompatible rollback | 2 |
| `services/vault/upload_rejection.py` | `RejectionContext`, fixed payload builder, fresh audit sink, `reject_after_owner_rollback`, `promote_for_owner`, audit-backed cleanup retry loader | 3 |
| HTTP error vocabulary | `staging_version_required`, `upload_identity_mismatch`, `staged_source_unavailable`, `storage_unavailable` | 3/9 |
| Documents | nullable `CheckIn.staging_version_id`; non-dedup check-in builds `StagedObjectRef(STAGING, ...)` | 4 |
| Browser | `putToPresigned -> { versionId }`; check-in sends version or `null` only on WORM dedup | 4 |
| Records | each `EvidenceRef` gets nullable `staging_version_id`; `EvidenceInput` carries its optional exact source | 5 |
| Import scan/extract | versioned `StagedResult`; exact `s3://...?...versionId=` parser; exact reads/temp deletes | 6 |
| Import commit | parse pinned locator; raw typed promotion; one `IMPORT_ITEM_FAILED` transaction; post-commit exact cleanup | 7 |
| Generated content | generated document freezes, pack ZIP, and import report pass the returned `StagedObjectRef` directly | 8 |
| Contract/docs | OpenAPI, lock, normative docs, engineering pattern, slice history | 9 |

The public storage types must settle on these signatures before any caller migration:

```python
class StagingDomain(enum.StrEnum):
    STAGING = "staging"
    IMPORT_STAGING = "import-staging"

@dataclasses.dataclass(frozen=True, slots=True)
class StagedVersionLocator:
    domain: StagingDomain
    object_key: str
    version_id: str

@dataclasses.dataclass(frozen=True, slots=True)
class StagedObjectRef:
    locator: StagedVersionLocator
    expected_sha256: str
    content_type: str
    expected_size: int | None = None

@dataclasses.dataclass(frozen=True, slots=True)
class VerifiedStagedObject:
    source: StagedObjectRef
    verified_sha256: str
    size: int
    content_type: str | None
    etag: str

@dataclasses.dataclass(frozen=True, slots=True)
class PromotionResult:
    outcome: PromotionOutcome
    verified_sha256: str
    size: int
    content_type: str | None
    retain_until: datetime.datetime
    source: StagedObjectRef
    source_etag: str
    target_bucket: str
    target_key: str
    target_version_id: str
```

`StagedVersionLocator` is the deletion-safe projection of the approved `StagedObjectRef`; it prevents the cleanup worker from inventing a content type merely to issue an exact delete. `StagedObjectRef.locator.object_key` must equal `expected_sha256` for promotable canonical sources.

---

## Task 1: Replace key-latest promotion with the exact-version verification boundary

**Files:**
- Create: `apps/api/src/easysynq_api/services/vault/staged_identity.py`
- Create: `apps/api/tests/unit/test_storage_promotion.py`
- Modify: `apps/api/src/easysynq_api/services/vault/storage.py`
- Modify: `apps/api/src/easysynq_api/services/vault/__init__.py`

- [ ] **Step 1: Write the fixed production falsifier first**

In `test_storage_promotion.py`, build a fake streaming S3 client whose exact source version contains same-sized false bytes. Import the desired `StagedObjectRef`/`UploadIdentityMismatch` and call the production `_finalize_sync` boundary:

```python
def test_finalize_sync_rejects_same_size_false_bytes_before_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    good = b"approved-2026"
    evil = b"tampered-2026"
    assert len(good) == len(evil)
    sha = hashlib.sha256(good).hexdigest()
    source = staged_ref(sha=sha, version_id="v-evil", size=len(good))
    client = FakeS3(source_bytes=evil, source_version="v-evil")

    with pytest.raises(UploadIdentityMismatch) as caught:
        storage._finalize_sync(source, "documents", client=client)

    assert caught.value.expected_sha256 == sha
    assert caught.value.observed_sha256 == hashlib.sha256(evil).hexdigest()
    assert client.copy_calls == []
    assert client.source_body.closed is True
```

Run:

```bash
cd apps/api && uv run pytest -m unit tests/unit/test_storage_promotion.py -x
```

Expected RED: collection fails because the typed contract/new `_finalize_sync` signature does not exist on `d166295`; the current production helper would copy the false bytes.

- [ ] **Step 2: Add and validate the immutable identity types**

Implement the interfaces from the ownership map in `staged_identity.py`. Add `__post_init__` validation with exact rules:

```python
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_VERSION_ID = 1024

def _validate_version_id(value: str) -> None:
    if not 1 <= len(value) <= _MAX_VERSION_ID or value == "null":
        raise StagingVersionRequired
```

`StagedVersionLocator` also requires an actual `StagingDomain` member and a non-empty object key. `StagedObjectRef` requires canonical lowercase SHA, matching canonical object key, non-empty content type, and a non-negative optional expected size; it never coerces a foreign domain string into the closed enum.

Add typed exceptions:

```python
class StagedPromotionError(Exception): ...
class IdentityRefusal(StagedPromotionError): ...
class StagingVersionRequired(StagedPromotionError): ...  # no exact source; never authorizes cleanup
class StagedSourceUnavailable(IdentityRefusal): ...
class UploadIdentityMismatch(IdentityRefusal): ...
class StagedSourceChanged(IdentityRefusal): ...
class StorageUnavailable(StagedPromotionError): ...
class WormNotApplied(StagedPromotionError): ...
class TargetIdentityConflict(StagedPromotionError): ...
```

`UploadIdentityMismatch` carries source, expected/observed digest, expected/observed size, optional ETag, and classification (`digest_mismatch` or `size_mismatch`). `StorageUnavailable` carries a bounded `StorageStage` enum (`versioning`, `staging_put`, `source_get`, `source_read`, `target_head`, `target_get`, `target_read`, `copy`, `retention`, `owner_rollback`, `audit`, `cleanup`) and chains the private cause.

- [ ] **Step 3: Unit-test validation independently**

Add parametrized tests for empty, `"null"`, and 1025-character versions; a runtime foreign-domain string; blank key/content type; uppercase/non-64 SHA; key/SHA divergence; negative expected size; and a valid opaque version containing URL-significant characters.

Run:

```bash
cd apps/api && uv run pytest -m unit tests/unit/test_storage_promotion.py -k 'ref or locator' -x
```

Expected GREEN for the type tests; the production falsifier remains RED until the next steps.

- [ ] **Step 4: Implement exact source verification**

In `storage.py`, add `_require_staging_versioning_sync(domain, client)` plus `_verify_staged_sync(source, *, client=None) -> VerifiedStagedObject` and async `verify_staged`. Resolve the logical domain through settings, call `get_bucket_versioning`, and require exact `Status == "Enabled"` before reading. Suspended/missing status or a probe error maps to `StorageUnavailable(stage=VERSIONING)` with no source GET/copy. Then call:

```python
response = client.get_object(
    Bucket=_bucket_for_domain(source.locator.domain),
    Key=source.locator.object_key,
    VersionId=source.locator.version_id,
)
```

Require the returned `VersionId` to equal the request; a different/missing response identity is `StorageUnavailable(stage=SOURCE_GET)` and still closes any returned body, because it does not prove that the requested source itself is rejectable. Retain a non-empty returned ETag as opaque text, stream `Body.read(_STREAM_CHUNK)` into SHA-256 and a byte counter, and close the body in `finally`. Compare digest with `hmac.compare_digest`; compare expected size only when non-null. Map only explicit object/version-not-found codes (`NoSuchKey`, `NoSuchVersion`, or the store's object-level `404`) to `StagedSourceUnavailable`; `NoSuchBucket` is infrastructure even though its HTTP status is also 404. Map AccessDenied, 5xx, botocore transport failures, and mid-stream failures to `StorageUnavailable` without treating them as absence.

- [ ] **Step 5: Make the falsifier GREEN before adding copy behavior**

Have `_finalize_sync` call `_verify_staged_sync` before any target request. Run:

```bash
cd apps/api && uv run pytest -m unit tests/unit/test_storage_promotion.py::test_finalize_sync_rejects_same_size_false_bytes_before_copy -x
```

Expected GREEN: mismatch raised, zero copy calls, body closed.

- [ ] **Step 6: Add exact-copy and body-closure tests**

Add tests proving:

- source GET uses exact `VersionId`;
- a missing/different response `VersionId` maps to infrastructure failure, retains the requested source, and closes its body;
- suspended/missing/inaccessible bucket versioning fails before source GET or target access;
- `CopySource` is `{"Bucket": ..., "Key": ..., "VersionId": ...}`;
- `CopySourceIfMatch` equals the source response ETag and is never compared to SHA;
- response `ChecksumSHA256` and other client-supplied checksum metadata is ignored—even claimed-good checksum metadata over false body bytes cannot override the streamed digest;
- a missing/blank source ETag is an infrastructure failure rather than permission to copy without `CopySourceIfMatch`;
- expected-size mismatch refuses before copy even if the digest matches;
- success, digest mismatch, size mismatch, missing source, cancellation/BaseException during read, mid-stream read failure, source GET failure, target HEAD/GET/read failure during orphan adoption, copy failure, and retention failure close every opened body; explicitly prove `NoSuchBucket` is not collapsed into source absence;
- retry after a mid-stream fault issues a fresh exact GET and hashes from byte zero; it never resumes a partial digest;
- copy `412` maps to `StagedSourceChanged`; copy `NoSuchKey`/`NoSuchVersion` for the pinned source maps to `StagedSourceUnavailable`; destination-bucket 404, AccessDenied, 5xx, transport, and every other copy error map to `StorageUnavailable(stage=COPY)`.

Run and confirm the new cases are RED before implementation:

```bash
cd apps/api && uv run pytest -m unit tests/unit/test_storage_promotion.py -k 'copy or closes or size' -x
```

- [ ] **Step 7: Implement exact target adoption/copy/verification**

After source verification:

1. HEAD the target latest only to discover whether a prior copy/orphan exists and capture its target `VersionId`; only explicit object absence proceeds to copy, while `NoSuchBucket`, authorization, and transport failures are infrastructure errors.
2. If present, require a non-empty/non-`null` captured target version, GET/hash that exact target version, and require expected digest, exact size, and active retain-until. A returned-but-missing/expired retention is `WormNotApplied`; authorization, 5xx, transport, or other retention-probe failures are `StorageUnavailable(stage=RETENTION)`. Return `ADOPTED_EXISTING`; target digest/size divergence is `TargetIdentityConflict`.
3. If absent, copy the exact source version with ETag precondition.
4. Require non-empty/non-`null` copy `VersionId`; HEAD/get retention for that exact target version; require exact size and active retain-until under the same strict error mapping; return `COPIED`. A target size divergence is a target identity conflict, not success.

Add a copy-ambiguity recovery test: fake `copy_object` writes a correctly retained target version and then raises a transport error; the next invocation must capture, hash, and adopt that exact orphan instead of blindly creating another immutable version.

Keep the test seam explicit but non-public:

```python
def _finalize_sync(
    source: StagedObjectRef,
    target_bucket: str,
    *,
    client: Any | None = None,
    before_copy: Callable[[], None] | None = None,
) -> PromotionResult:
```

`before_copy` defaults to `None` and is used only by the deterministic real-MinIO race test. Async `promote_worm` does not expose it.

- [ ] **Step 8: Add exact generated PUT and exact delete**

Change `_put_bytes_sync` to return the PUT response. `put_staging_bytes(data, sha, content_type)` must call the same versioning guard before PUT, require a valid 1–1024-character, non-`null` response `VersionId`, and return a `StagedObjectRef(STAGING, key=sha, expected_size=len(data))`; disabled versioning or any missing/invalid store-produced identity maps to `StorageUnavailable`, never the caller-correctable `StagingVersionRequired`.

Add:

```python
async def delete_staged_version(locator: StagedVersionLocator) -> None: ...
```

The sync delete sends domain-resolved bucket, exact key, and exact `VersionId`. Treat only explicit exact-object/version absence (`NoSuchKey`, `NoSuchVersion`, or the store's object-level `404`) as idempotent success; `NoSuchBucket`, authorization, and every other failure propagate. Add unit tests that fail if `VersionId` is removed or bucket absence is swallowed.

- [ ] **Step 9: Isolate the legacy caller adapter while the new suite goes green**

Rename the current key-latest sync body to `_legacy_finalize_sync` and keep `finalize_worm(sha, source_bucket=...)` as an explicitly documented **Task-8 migration adapter** for callers not yet converted. The new secure entry point is `promote_worm(source, *, target_bucket)` and the new `_finalize_sync` remains the production exact-version boundary required by the falsifier. No new or migrated caller may use the adapter, and Task 8 deletes both legacy symbols before full verification.

Run:

```bash
cd apps/api && uv run pytest -m unit tests/unit/test_storage_promotion.py tests/unit/test_storage_hash_object.py tests/unit/test_storage_presign.py
```

Expected GREEN.

- [ ] **Step 10: Verify and commit Task 1**

```bash
cd apps/api && uv run ruff check src/easysynq_api/services/vault/staged_identity.py src/easysynq_api/services/vault/storage.py tests/unit/test_storage_promotion.py
cd apps/api && uv run ruff format --check src/easysynq_api/services/vault/staged_identity.py src/easysynq_api/services/vault/storage.py tests/unit/test_storage_promotion.py
cd apps/api && uv run mypy src
git diff --check
git add apps/api/src/easysynq_api/services/vault/staged_identity.py apps/api/src/easysynq_api/services/vault/storage.py apps/api/src/easysynq_api/services/vault/__init__.py apps/api/tests/unit/test_storage_promotion.py
git commit -m "feat(upload): bind WORM promotion to verified source versions"
```

---

## Task 2: Version the staging buckets, expose the browser header, fail readiness closed, and install the rollback guard

**Files:**
- Modify: `infra/compose/minio/minio-init.sh`
- Modify: `infra/compose/compose.yml`
- Modify: `infra/compose/caddy/Caddyfile`
- Modify: `.env.example`
- Modify: `apps/api/src/easysynq_api/readiness.py`
- Modify: `apps/api/tests/unit/test_health.py`
- Modify: `apps/api/tests/unit/test_deploy_configuration.py`
- Modify: `apps/api/tests/unit/test_caddy_headers.py`
- Modify: `apps/api/tests/integration/conftest.py`
- Create: `apps/api/tests/integration/test_upload_identity_storage.py`

- [ ] **Step 1: Write RED provisioning and readiness tests**

In `test_deploy_configuration.py`, assert the init script contains both exact commands, a staging CORS rule, and no purge-on-delete/lifecycle expiry:

```python
assert "mc version enable local/staging" in init
assert "mc version enable local/import-staging" in init
assert "<ExposeHeader>x-amz-version-id</ExposeHeader>" in init
assert "<ExposeHeader>ETag</ExposeHeader>" in init
assert "--purge-on-delete" not in init
```

In `test_health.py`, fake `_minio_client` so one staging bucket reports `Suspended`; assert `_check_minio(settings).ready is False`. Add the all-enabled case. In `test_deploy_configuration.py`, assert `api`, `worker`, and `beat` each require `minio-init: condition: service_completed_successfully`; no producer may race the versioning/CORS initializer. In `test_deploy_configuration.py` and `test_caddy_headers.py`, also assert Compose passes `EASYSYNQ_COMPATIBILITY_READ_ONLY` with a safe `0` default and that a first-match guard combines `/api/*`, `POST PUT PATCH DELETE`, the environment placeholder, and a 503 response before every API proxy handle.

Run:

```bash
cd apps/api && uv run pytest -m unit tests/unit/test_health.py tests/unit/test_deploy_configuration.py tests/unit/test_caddy_headers.py -x
```

Expected RED: provisioning strings, version-aware readiness, and the compatibility guard are absent.

- [ ] **Step 2: Make MinIO provisioning idempotently version both buckets**

After each `mc mb --ignore-existing`, add:

```sh
mc version enable local/staging
mc version enable local/import-staging
```

Do not set `--excluded-prefixes`, `--exclude-folders`, `--purge-on-delete`, object lock, retention, or lifecycle expiry.

In `compose.yml`, make `api`, `worker`, and `beat` depend on `minio-init` with `condition: service_completed_successfully`. Retain their existing dependency conditions. A failed initializer must prevent promotion-capable processes from starting; do not weaken this to `service_started`.

- [ ] **Step 3: Install the staging CORS response-header exposure**

Pass `PUBLIC_BASE_URL: ${PUBLIC_BASE_URL:-http://localhost}` to `minio-init` in `compose.yml`. In `minio-init.sh`, require it and apply an S3 XML CORS document to `local/staging` with:

```xml
<CORSRule>
  <AllowedOrigin>${PUBLIC_BASE_URL}</AllowedOrigin>
  <AllowedMethod>PUT</AllowedMethod>
  <AllowedHeader>*</AllowedHeader>
  <ExposeHeader>x-amz-version-id</ExposeHeader>
  <ExposeHeader>ETag</ExposeHeader>
  <MaxAgeSeconds>3000</MaxAgeSeconds>
</CORSRule>
```

Use `mc cors set local/staging /tmp/staging-cors.xml`. Before interpolating, require a non-empty HTTP(S) origin and reject whitespace/newlines, `*`, and XML metacharacters rather than allowing environment text to alter the document. Do not use wildcard origins. `import-staging` needs no browser CORS rule.

- [ ] **Step 4: Install and validate the incompatible-rollback write guard**

Install an operator-controlled, default-off compatibility guard at the edge. Add this environment value to the `proxy` service in `compose.yml` and document it in `.env.example`:

```yaml
EASYSYNQ_COMPATIBILITY_READ_ONLY: ${EASYSYNQ_COMPATIBILITY_READ_ONLY:-0}
```

Immediately before `@sse` in the base Caddyfile, add the first-match handle:

```caddyfile
@compatibility_rollback_write {
	path /api/*
	method POST PUT PATCH DELETE
	vars {env.EASYSYNQ_COMPATIBILITY_READ_ONLY} 1
}
handle @compatibility_rollback_write {
	respond "Write operations are disabled during compatibility rollback." 503
}
```

The three matcher lines are ANDed: normal mode (`0`) is unchanged; rollback mode blocks only mutating application API requests, while `GET`, `HEAD`, `OPTIONS`, `/healthz`, `/readyz`, static UI, authentication, and existing vault reads remain routed normally. Do not put the guard on the separate production MinIO site; direct staged PUTs are harmless once init/check-in endpoints and all workers are disabled, and already-issued presigned URLs must not turn Caddy into an S3 signature intermediary.

Run the unit tests from Step 1. Expected GREEN for the guard assertions. Then validate the real base Caddyfile grammar with the same `caddy:2` image family declared by Compose:

```bash
docker run --rm \
  -e SITE_ADDRESS=:80 \
  -e EASYSYNQ_COMPATIBILITY_READ_ONLY=0 \
  -v "$PWD/infra/compose/caddy/Caddyfile:/etc/caddy/Caddyfile:ro" \
  caddy:2 caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
```

- [ ] **Step 5: Extract and implement version-aware readiness**

Refactor the nested MinIO client construction into `_minio_client(settings)`. In `_check_minio`, after `list_buckets`, call `get_bucket_versioning` for `settings.s3_bucket_staging` and `settings.s3_bucket_import_staging`. Any status other than exact `Enabled`, missing bucket, or access failure returns `DependencyStatus("minio", False, internal_detail)`; the public `/readyz` continues stripping detail.

Run the Task 2 unit tests again. Expected GREEN.

- [ ] **Step 6: Version and configure CORS on the shared real-MinIO fixture**

In `_minio`, after creating the two plain staging buckets, call `put_bucket_versioning(... Status="Enabled")`. Apply the same CORS rule to `staging` with test origin `http://test`.

- [ ] **Step 7: Add real-MinIO version/header/CORS tests**

In `test_upload_identity_storage.py`, use the fixture's boto3 credentials and assert:

- both `get_bucket_versioning` calls return `Enabled`;
- a `put_object` to each bucket returns non-empty, non-`null` `VersionId`;
- `get_bucket_cors(Bucket="staging")` exposes case-insensitive `x-amz-version-id` and `ETag`;
- a real presigned HTTP PUT with `Origin: http://test` returns `x-amz-version-id` and ETag, and its `Access-Control-Expose-Headers` makes both readable to browser JavaScript;
- no lifecycle configuration is installed on either staging bucket.

Run:

```bash
cd apps/api && uv run pytest -m integration tests/integration/test_upload_identity_storage.py -k 'versioning or cors' -x
```

Expected GREEN with Docker/MinIO available.

- [ ] **Step 8: Add the real multipart and deterministic overwrite proofs**

In the same file:

- force multipart upload with a low `TransferConfig.multipart_threshold`; assert its ETag is not treated as SHA and correct content promotes;
- stage same-sized false multipart bytes under the claimed good key; assert `UploadIdentityMismatch` and target 404;
- stage good version V1, call `_finalize_sync(..., before_copy=overwrite_with_evil_v2)`, and assert the target hashes to V1 good bytes or the helper cleanly raises `StagedSourceChanged`—never evil V2;
- delete a rejected V1 via `delete_staged_version`, leave newer V2, and prove exact V2 remains current/readable;
- wrap the real client so `copy_object` raises after source verification, then prove the exact source remains readable for retry.

Run:

```bash
cd apps/api && uv run pytest -m integration tests/integration/test_upload_identity_storage.py -x
```

Expected GREEN.

- [ ] **Step 9: Verify and commit Task 2**

```bash
cd apps/api && uv run pytest -m unit tests/unit/test_health.py tests/unit/test_deploy_configuration.py tests/unit/test_caddy_headers.py
cd apps/api && uv run ruff check src/easysynq_api/readiness.py tests/unit/test_health.py tests/unit/test_deploy_configuration.py tests/unit/test_caddy_headers.py tests/integration/conftest.py tests/integration/test_upload_identity_storage.py
git diff --check
git add infra/compose/minio/minio-init.sh infra/compose/compose.yml infra/compose/caddy/Caddyfile .env.example apps/api/src/easysynq_api/readiness.py apps/api/tests/unit/test_health.py apps/api/tests/unit/test_deploy_configuration.py apps/api/tests/unit/test_caddy_headers.py apps/api/tests/integration/conftest.py apps/api/tests/integration/test_upload_identity_storage.py
git commit -m "feat(upload): require versioned staging infrastructure"
```

---

## Task 3: Add durable rejection evidence, exact cleanup retry, stable problems, and bounded signals

**Files:**
- Create: `apps/api/src/easysynq_api/services/vault/upload_rejection.py`
- Create: `apps/api/src/easysynq_api/tasks/upload_identity.py`
- Create: `apps/api/tests/unit/test_upload_rejection.py`
- Create: `apps/api/tests/unit/test_upload_identity_task_registration.py`
- Modify: `apps/api/src/easysynq_api/tasks/__init__.py`
- Modify: `apps/api/src/easysynq_api/problems.py`
- Modify: `packages/contracts/openapi.yaml` (problem enum only)
- Modify: `packages/contracts/.contract.lock`
- Modify: `apps/api/tests/unit/test_problem_code_contract.py` only if its equality diagnostic needs improvement; never weaken it

- [ ] **Step 1: Write RED sequencing and payload tests**

Use fakes for owner session, fresh sink, delete, and enqueue. Pin this order:

```python
assert calls == ["owner.rollback", "audit.add", "audit.commit", "delete.exact"]
```

Also test:

- owner rollback failure yields `storage_unavailable` and performs no audit/delete/enqueue;
- audit commit failure yields `storage_unavailable`, performs no delete/enqueue;
- exact delete failure preserves the original public mismatch result and enqueues `(audit_id, occurred_at)`;
- infrastructure `StorageUnavailable` rolls back owner state but does not audit or delete;
- target conflict rolls back, commits retain-source integrity evidence, emits 503, and never deletes/enqueues cleanup;
- a cleanup metric/log has only bounded dimensions;
- payload has operation/classification/source/expected/observed/cleanup and no body, URL, credentials, filename, user, or raw exception.

Run:

```bash
cd apps/api && uv run pytest -m unit tests/unit/test_upload_rejection.py -x
```

Expected RED: module absent.

- [ ] **Step 2: Implement the fixed context and payload builder**

Use closed enums/literals:

```python
type UploadOperation = Literal[
    "document_checkin", "record_capture", "import_commit", "server_generated"
]
type RejectionClassification = Literal[
    "digest_mismatch", "size_mismatch", "source_missing", "source_changed",
    "target_identity_conflict",
]

@dataclasses.dataclass(frozen=True, slots=True)
class RejectionContext:
    operation: UploadOperation
    org_id: uuid.UUID
    actor_id: uuid.UUID | None
    actor_type: ActorType
    scope_ref: str | None
    user_correctable: bool
```

For the four source-refusal classifications, the payload must match design §8.2 exactly. Store logical domain value (`staging`/`import-staging`), claimed key, opaque version, optional ETag, expected/observed values, and `{"policy": "delete_exact_version_after_audit"}`.

Build target conflict evidence through a separate typed branch: classification `target_identity_conflict`, the same operation/scope and expected SHA/size, the bounded target domain plus whichever observed SHA/size the failing exact check established (null when that value was not safely observed), and `{"policy": "retain_source_operator_investigation"}`. It must not carry the source-delete policy, a durable target `VersionId`, or any target-ownership claim, and the cleanup worker must reject it. This is the fresh `BLOB_INTEGRITY_FAILED` required for an existing target whose identity does not match its content-addressed key/source.

- [ ] **Step 3: Implement a fresh short-transaction rejection sink**

`DbUploadRejectionSink(session_factory=None).record(context, refusal)` creates one `AuditEvent` with:

- `EventType.BLOB_INTEGRITY_FAILED`;
- `AuditObjectType.config` and `object_id=context.org_id`;
- user actor only for interactive requests; null/system for generated content;
- `occurred_at=datetime.now(datetime.UTC)` so current audit partitions are used;
- `scope_ref=context.scope_ref` and fixed `after` payload.

Flush to get the identity ID, commit, and return `AuditEventRef(id, occurred_at)`. An injected task-local `async_sessionmaker` must be honored.

- [ ] **Step 4: Implement owner orchestration and error mapping**

Add:

```python
async def reject_after_owner_rollback(
    failure: IdentityRefusal | TargetIdentityConflict,
    *,
    context: RejectionContext,
    rejection_sessionmaker: async_sessionmaker[AsyncSession] | None = None,
) -> NoReturn: ...

async def promote_for_owner(
    owner_session: AsyncSession,
    source: StagedObjectRef,
    *,
    target_bucket: str,
    context: RejectionContext,
    rejection_sessionmaker: async_sessionmaker[AsyncSession] | None = None,
) -> PromotionResult: ...
```

Behavior:

- call `storage.promote_worm`;
- `reject_after_owner_rollback` owns fresh audit → authorized exact delete (source refusals only) → public-error mapping but never rolls back an owner itself; this is the safe entry point after an outer savepoint has already rolled back and also records target conflicts with the retain-source policy;
- on `IdentityRefusal`, `promote_for_owner` rolls back its whole owner session, then delegates to `reject_after_owner_rollback`;
- for every promotion failure, if owner rollback itself fails, emit a bounded owner-rollback failure, raise public 503, and perform no audit or cleanup because transaction state is not trustworthy;
- if delete fails, enqueue retry from the committed audit reference and still map the original refusal;
- if publishing that retry also fails, preserve the original refusal response, emit a terminal operator alert/signal, and never retry with a broader or key-only delete;
- if audit fails, emit audit-stage failure and raise public `503 storage_unavailable` without cleanup;
- on non-refusal `StorageUnavailable` or `WormNotApplied`, roll back owner state, retain source, emit bounded signal, and return public 503 without false-identity audit/cleanup;
- on `TargetIdentityConflict`, rollback the owner, delegate to the same post-rollback helper, commit a fresh `BLOB_INTEGRITY_FAILED` target-conflict audit with no source-delete authorization, retain the source, alert/emit the bounded signal, and return public 503;
- user-correctable mismatch → 422 `upload_identity_mismatch`; missing supplied source/change → 409 `staged_source_unavailable`; generated-content refusals → 503 `storage_unavailable`.

Add `require_staging_ref(..., operation: UploadOperation)` for the no-version case: correct-domain WORM dedup callers skip it; otherwise missing/`null` maps to 422 `staging_version_required` and emits the missing/legacy metric under the supplied bounded operation without fabricating an audit or delete target.

- [ ] **Step 5: Add the four runtime problem strings**

Add to `ProblemCode` in sorted order:

```python
"staged_source_unavailable",
"staging_version_required",
"storage_unavailable",
"upload_identity_mismatch",
```

Add the same four strings to the OpenAPI `Problem.code` enum now, run `bash scripts/gen-contracts.sh` to regenerate `.contract.lock`, and keep `test_problem_code_contract` GREEN at this task boundary. Task 9 adds the request fields and endpoint-specific response prose; it does not defer runtime vocabulary parity.

- [ ] **Step 6: Write RED cleanup-task validation tests**

Tests must prove the worker:

- rejects non-positive audit IDs, malformed/non-UTC `occurred_at`, and attempts outside 1–5 before querying/deleting;
- accepts only `BLOB_INTEGRITY_FAILED` or `IMPORT_ITEM_FAILED` rows with one approved classification and cleanup policy;
- selects by both global identity `id` and partition key `occurred_at`;
- reconstructs only an allow-listed logical staging locator;
- rejects key/expected-SHA divergence, absent version, `"null"`, unknown bucket/event/classification/policy;
- exact object/version 404 is success, while `NoSuchBucket` is a retryable infrastructure failure;
- transient failure reschedules the same audit reference with bounded exponential delay and incremented attempt;
- attempt 5 logs/metrics terminal failure and does not broaden deletion.

- [ ] **Step 7: Implement and register the cleanup task**

In `tasks/upload_identity.py`, create/dispose a task-local async engine for each `asyncio.run`, load and validate the audit row, and call `storage.delete_staged_version`. Use task payload `(audit_event_id: int, occurred_at: str, attempt: int = 1)`; retry with `apply_async(... countdown=min(3600, 60 * 2 ** (attempt - 1)))`; max attempts 5.

Import `upload_identity` in `tasks/__init__.py`. Registration test:

```python
assert "easysynq.upload_identity.cleanup_rejected" in app.tasks
```

This is event-driven only; do not add Beat schedule.

- [ ] **Step 8: Make structured metric signals testable and private**

Implement one helper that logs event name `upload_identity.metric` with exact allowed keys:

```python
{"metric", "operation", "classification", "domain", "stage", "outcome", "count"}
```

Use closed value sets for mismatch, missing version, versioning/staging-PUT/GET/copy/retention failure, cleanup retry, cleanup success, and cleanup final failure. Unit-test `caplog` output and assert forbidden key names/values are absent.

- [ ] **Step 9: Run Task 3 tests and commit**

```bash
cd apps/api && uv run pytest -m unit tests/unit/test_upload_rejection.py tests/unit/test_upload_identity_task_registration.py tests/unit/test_problem_code_contract.py
bash scripts/gen-contracts.sh --check
cd apps/api && uv run ruff check src/easysynq_api/services/vault/upload_rejection.py src/easysynq_api/tasks/upload_identity.py tests/unit/test_upload_rejection.py tests/unit/test_upload_identity_task_registration.py
cd apps/api && uv run mypy src
git diff --check
git add apps/api/src/easysynq_api/services/vault/upload_rejection.py apps/api/src/easysynq_api/tasks/upload_identity.py apps/api/src/easysynq_api/tasks/__init__.py apps/api/src/easysynq_api/problems.py packages/contracts/openapi.yaml packages/contracts/.contract.lock apps/api/tests/unit/test_upload_rejection.py apps/api/tests/unit/test_upload_identity_task_registration.py
git commit -m "feat(upload): audit refusals before exact cleanup"
```

Do not include `test_problem_code_contract.py` in this commit unless its diagnostic changed; its existing equality assertion remains intentionally strict.

---

## Task 4: Bind controlled-document check-in and browser orchestration to the PUT version

**Files:**
- Modify: `apps/api/src/easysynq_api/api/documents.py`
- Modify: `apps/api/src/easysynq_api/services/vault/service.py`
- Modify: `apps/api/tests/unit/test_api_request_validation.py`
- Modify: `apps/api/tests/integration/test_vault.py`
- Modify: `apps/web/src/lib/upload.ts`
- Modify: `apps/web/src/lib/upload.test.ts`
- Modify: `apps/web/src/lib/types.ts`
- Modify: `apps/web/src/features/authoring/hooks.ts`
- Modify: `apps/web/src/features/authoring/CheckInPanel.test.tsx`
- Modify: `apps/web/src/test/msw/handlers.ts`

- [ ] **Step 1: Write RED browser header tests**

Change the happy PUT MSW response to include `x-amz-version-id: v-browser-1` and assert:

```ts
await expect(putToPresigned(url, file, "text/plain")).resolves.toEqual({
  versionId: "v-browser-1",
});
```

Also assert the request has no `authorization` and no `x-amz-checksum-*` header. Add 2xx-without-header, blank-header, literal-`null`, and 1025-character tests; each must reject and never pretend upload succeeded. Run:

```bash
npm --prefix apps/web test -- src/lib/upload.test.ts
```

Expected RED: current function returns `void` and accepts missing identity.

- [ ] **Step 2: Return the exact browser PUT version**

Add:

```ts
export interface PresignedPutResult { versionId: string }
```

Read the raw `resp.headers.get("x-amz-version-id")` after `resp.ok`; reject absent, whitespace-only, literal `"null"`, or more than 1024 characters, but return a valid original header value without trimming or otherwise normalizing the opaque ID. Continue sending no bearer and do not read or trust ETag.

- [ ] **Step 3: Write RED authoring orchestration tests**

In `CheckInPanel.test.tsx`, capture the check-in JSON and assert a non-dedup upload sends `staging_version_id: "v-browser-1"`. Extend the existing dedup test to assert it sends `staging_version_id: null` and performs no PUT. Update the default MinIO MSW handler to return the version header.

Run the file; expected RED until the hook propagates the return value.

- [ ] **Step 4: Propagate the version in the web hook**

Update the mutation:

```ts
let stagingVersionId: string | null = null;
if (!init.dedup && init.upload_url) {
  stagingVersionId = (await putToPresigned(init.upload_url, file, contentType)).versionId;
}
return api.send(..., {
  sha256,
  staging_version_id: stagingVersionId,
  ...
});
```

Do not allow `dedup=false` plus null URL to call check-in; raise a calm upload failure. Update `InitUploadResult` comments only; the init response shape is unchanged.

- [ ] **Step 5: Write RED API model/version-required tests**

Add `staging_version_id: str | None = Field(default=None, min_length=1, max_length=1024)` to the desired `CheckIn` shape in tests. Prove oversized/blank values fail Pydantic, while null parses for conditional service validation.

In `test_vault.py`, add a non-dedup check-in without the field and expect:

```python
assert response.status_code == 422
assert response.json()["code"] == "staging_version_required"
```

Assert working draft and lock remain. Expected RED.

- [ ] **Step 6: Adapt document check-in with the WORM-dedup carveout**

Thread `staging_version_id` from route to `service.checkin`. Preserve both existing no-change/latest-version and correct-documents-WORM Blob paths without requiring staging, but re-read/assert the current latest version's Blob is still in the documents WORM domain before the no-change bypass; a corrupt/missing/foreign Blob never legitimizes null staging identity. Only when the Blob row is absent:

```python
source = require_staging_ref(
    domain=StagingDomain.STAGING,
    sha256=sha256,
    version_id=staging_version_id,
    content_type=mime_type,
    operation="document_checkin",
)
promoted = await promote_for_owner(
    session,
    source,
    target_bucket=settings.s3_bucket_documents,
    context=RejectionContext.document_checkin(actor, doc.identifier),
)
```

Insert/re-read the conflict-safe Blob row from `PromotionResult`, keep the existing documents-domain/WORM assertion, version/audit owner transaction, and lock release only on success.

Migrate `test_checkin_refuses_foreign_bucket_source_blob` in `test_vault.py` at the same time: retain the `StagedObjectRef` returned by `put_staging_bytes`, call `promote_worm(source, target_bucket=records)`, and use `PromotionResult` metadata. No test helper may keep the legacy function alive accidentally.

- [ ] **Step 7: Add the false-byte document end-to-end proof**

Update the shared `_upload` helper to return/carry the PUT response `x-amz-version-id`. Add a test that uploads equal-length false bytes under the honest SHA, then check-ins with that exact version. Assert:

- 422 `upload_identity_mismatch` with no observed hash/version/bucket in the body;
- durable `BLOB_INTEGRITY_FAILED` with user actor and fixed payload;
- exact bad version is absent, but a newer replacement version written before cleanup remains;
- `working_draft`, scratch SHA, and Redis lock remain;
- no target object, Blob, DocumentVersion, `CHECKIN`, or `NO_CHANGE` exists;
- honest re-upload with a new version succeeds exactly once.

Also add a 409 exact-version-missing test and a 503 source-read failure test that proves infrastructure failure retains the source and emits no rejection cleanup.

- [ ] **Step 8: Run focused document/web gates and commit**

```bash
cd apps/api && uv run pytest -m unit tests/unit/test_api_request_validation.py tests/unit/test_storage_promotion.py tests/unit/test_upload_rejection.py
cd apps/api && uv run pytest -m integration tests/integration/test_vault.py -k 'upload_identity or checkin' -x
npm --prefix apps/web test -- src/lib/upload.test.ts src/features/authoring/CheckInPanel.test.tsx
npm --prefix apps/web run typecheck
git diff --check
git add apps/api/src/easysynq_api/api/documents.py apps/api/src/easysynq_api/services/vault/service.py apps/api/tests/unit/test_api_request_validation.py apps/api/tests/integration/test_vault.py apps/web/src/lib/upload.ts apps/web/src/lib/upload.test.ts apps/web/src/lib/types.ts apps/web/src/features/authoring/hooks.ts apps/web/src/features/authoring/CheckInPanel.test.tsx apps/web/src/test/msw/handlers.ts
git commit -m "feat(upload): bind document check-in to browser PUT version"
```

---

## Task 5: Bind each Record evidence attachment to its own exact source version

**Files:**
- Modify: `apps/api/src/easysynq_api/api/records.py`
- Modify: `apps/api/src/easysynq_api/services/records/service.py`
- Modify: `apps/api/src/easysynq_api/services/records/__init__.py`
- Modify: `apps/api/tests/unit/test_api_request_validation.py`
- Modify: `apps/api/tests/integration/test_records.py`

There is no dedicated Records evidence-upload SPA in this repository (`CLAUDE.md` names Records API/worker-complete with no management route), so this task proves per-item serialization at the Pydantic/OpenAPI/service boundary and does not invent a new UI.

- [ ] **Step 1: Define the service input and write RED normalization tests**

Add a frozen service value:

```python
@dataclasses.dataclass(frozen=True, slots=True)
class EvidenceInput:
    sha256: str
    content_type: str
    source: StagedObjectRef | None
```

Validate canonical SHA/non-empty content type and, when `source` exists, require `source.expected_sha256 == sha256` and `source.content_type == content_type`; internal callers cannot pair one locator with another evidence claim. Unit-test `_normalize_evidence` so duplicate same SHA/same version collapses once, but same SHA with different versions—including null versus non-null—raises top-level `422 validation_error` with nested field `evidence` and nested code `ambiguous_staging_version` (nested validation codes are intentionally outside `ProblemCode`).

- [ ] **Step 2: Extend `EvidenceRef` and route mapping**

Add nullable bounded `staging_version_id`. In capture and correction routes, derive a `StagedObjectRef(STAGING, key=sha)` only for supplied versions; pass `EvidenceInput` values plus an interactive `RejectionContext(operation="record_capture", ...)` into the service.

Correct-domain records-WORM dedup may pass `source=None`; a missing Blob later requires a source and returns `staging_version_required`.

- [ ] **Step 3: Write RED Record domain tests**

Update `_upload_evidence` to capture the PUT version. Add capture and correction-path cases for:

- missing version on non-dedup → 422;
- same SHA/different versions → 422 before storage;
- same-sized false bytes → 422 mismatch;
- exact missing version → 409;
- existing records-WORM Blob with null version → successful dedup.

For mismatch assert no base `DocumentedInformation`, Record, Blob, EvidenceBlob, content hash, `RECORD_CAPTURED`, or correction pointer survives; independent rejection audit and exact cleanup do survive; honest retry succeeds once.

- [ ] **Step 4: Adapt `_attach_evidence` without changing lock order**

Normalize inputs first, preserving old tuples as a private `_LegacyEvidenceInput` marker rather than conflating them with a new `EvidenceInput(source=None)`, then acquire the existing sorted physical-object locks for records target keys. For each item:

- if a correct records-WORM Blob exists, attach it without staging identity;
- if Blob is absent, require `EvidenceInput.source`; with an interactive rejection context use the record operation's missing-version mapper (422 plus bounded metric), while a raw/no-context ingestion call raises typed `StagingVersionRequired` for its stable failed-ledger mapping. Then verify/promote, insert conflict-safe Blob, re-read, and enforce records WORM domain;
- if the caller supplies no rejection context (reserved for ingestion Task 7 and the Task-8 import-report savepoint), call raw `storage.promote_worm` and let that outer scope own audit/cleanup.
- only a private `_LegacyEvidenceInput` from a still-unmigrated tuple may call the Task-8 `finalize_worm`/`_evidence_source_bucket` adapter when Blob is absent; new `EvidenceInput(source=None)` never enters that branch.

Do not release advisory locks before commit and do not reorder them by SHA text.

- [ ] **Step 5: Thread the typed input through capture/correction callers**

During the serialized caller migration, type `capture_record`/`capture_correction` as `Sequence[EvidenceInput | tuple[str, str]]`; `_normalize_evidence` converts the old tuple only through the explicitly named Task-8 legacy adapter path. New HTTP code accepts only `EvidenceInput`, and no migrated caller may add a tuple. Task 7 supplies import sources, Task 8 supplies generated sources and then narrows the signature to `Sequence[EvidenceInput]`, deleting the tuple and `_evidence_source_bucket` adapters together. Calls with `evidence=()` remain unchanged throughout.

- [ ] **Step 6: Run focused Record tests and commit**

```bash
cd apps/api && uv run pytest -m unit tests/unit/test_api_request_validation.py -k 'staging or evidence or content_address'
cd apps/api && uv run pytest -m integration tests/integration/test_records.py -k 'upload_identity or evidence or correction' -x
cd apps/api && uv run ruff check src/easysynq_api/api/records.py src/easysynq_api/services/records/service.py tests/integration/test_records.py
cd apps/api && uv run mypy src
git diff --check
git add apps/api/src/easysynq_api/api/records.py apps/api/src/easysynq_api/services/records/service.py apps/api/src/easysynq_api/services/records/__init__.py apps/api/tests/unit/test_api_request_validation.py apps/api/tests/integration/test_records.py
git commit -m "feat(upload): bind record evidence to staged versions"
```

---

## Task 6: Produce and consume exact versioned import-staging locators

**Files:**
- Create: `apps/api/tests/unit/test_ingestion_storage.py`
- Modify: `apps/api/src/easysynq_api/services/ingestion/storage.py`
- Modify: `apps/api/src/easysynq_api/services/ingestion/service.py`
- Modify: `apps/api/src/easysynq_api/services/ingestion/extract.py`
- Modify: `apps/api/tests/integration/test_ingestion.py`

- [ ] **Step 1: Write RED URI parser tests**

Pin one serializer/parser pair:

```python
uri = format_staged_uri(source)
assert uri == "s3://import-staging/<sha>?versionId=v%2F1%2Bopaque"
assert parse_staged_uri(
    uri,
    expected_sha256=sha,
    content_type="application/pdf",
    expected_size=123,
) == source
```

Reject wrong/missing scheme, foreign bucket, userinfo/port/fragment, wrong key, duplicate/unknown query parameters, blank/`null`/oversized version, and legacy `s3://import-staging/<sha>` with stable `StagingVersionRequired`.

Run:

```bash
cd apps/api && uv run pytest -m unit tests/unit/test_ingestion_storage.py -x
```

Expected RED: parser absent.

- [ ] **Step 2: Implement the exact locator and `StagedResult`**

Use `urllib.parse.urlsplit`, `parse_qs(..., strict_parsing=True, keep_blank_values=True)`, and `quote(version_id, safe="")` so `/`, `+`, `?`, `#`, and other URL-significant version characters round-trip exactly. `StagedResult` becomes:

```python
@dataclasses.dataclass(frozen=True, slots=True)
class StagedResult:
    sha256: str
    staged_blob_uri: str
    version_id: str
    size_bytes: int
    source: StagedObjectRef
```

The URI bucket is the configured import-staging bucket but parser maps it only to `StagingDomain.IMPORT_STAGING`. `parse_staged_uri` takes the caller-owned expected SHA, content type, and optional expected size and returns the complete immutable source; it never invents those values from URI text.

- [ ] **Step 3: Write RED scanner error-semantics tests**

With a fake boto client, prove:

- a true canonical HEAD 404 leads to copy;
- `import-staging` versioning not exact `Enabled` refuses before temp upload/canonical lookup;
- AccessDenied/500/`NoSuchBucket` does not mean absent and makes no copy;
- temp HEAD captures a valid 1–1024-character, non-`null` exact version after `upload_fileobj`; invalid store-produced identity is infrastructure failure;
- canonical reuse verifies the captured exact version's digest;
- canonical mismatch/storage error is typed refusal/failure;
- copy source and temp delete include exact VersionId, and the copy uses the temp HEAD ETag only as `CopySourceIfMatch`;
- copy result must provide a non-empty/non-`null` canonical VersionId.
- exact extract fetch closes its response body on success and read failure.

- [ ] **Step 4: Replace `_object_exists` with exact, verified staging**

Change `stage_stream(fileobj, *, content_type)` so `_process_file` passes its sniffed non-empty MIME type; upload the temp object with that content type. Require `import-staging` versioning through the shared guard before the unique temp upload. After upload, HEAD `_tmp/<uuid>` and capture its VersionId. Build a temp exact locator. For canonical SHA:

- HEAD latest only to capture an exact existing VersionId, with precise 404 handling;
- build `StagedObjectRef(IMPORT_STAGING, key=sha, expected_size=reader.size)` and call shared `vault.storage.verify_staged`/sync verifier on that exact version;
- if absent, `copy_object` from exact temp VersionId with the captured opaque temp ETag as `CopySourceIfMatch`, and require returned final VersionId;
- in `finally`, delete only the exact temp version if it was captured; never key-delete.

Return the exact canonical source/URI with `expected_size=reader.size`. Concurrent correct canonical versions are permitted because every reused/copied source is independently verified.

- [ ] **Step 5: Make extract fetch the exact locator**

Change `fetch_staged_bytes` to accept `StagedObjectRef`, GET exact VersionId, and close the response body in `finally` on success or failure. In `_extract_one`, require both `f.sha256` and `f.staged_blob_uri`, parse with expected SHA plus `f.mime_type or "application/octet-stream"` and `f.size_bytes`, and fetch exact. Map legacy/malformed locators to stable `staging_version_required`; map storage errors to bounded stable tokens instead of `repr(exc)`.

- [ ] **Step 6: Add real scan/extract pinning tests**

In `test_ingestion.py`, assert a newly scanned included file stores a URI with `versionId`, overwrite the same canonical key after scan, run extract, and prove extraction used the URI-pinned original. Add a pre-change URI test that fails the file closed while preserving its run/file review data.

- [ ] **Step 7: Run focused ingestion scan/extract tests and commit**

```bash
cd apps/api && uv run pytest -m unit tests/unit/test_ingestion_storage.py tests/unit/test_ingestion_helpers.py
cd apps/api && uv run pytest -m integration tests/integration/test_ingestion.py -k 'staged_blob_uri or versioned_locator or extract_pinned' -x
cd apps/api && uv run ruff check src/easysynq_api/services/ingestion/storage.py src/easysynq_api/services/ingestion/service.py src/easysynq_api/services/ingestion/extract.py tests/unit/test_ingestion_storage.py
cd apps/api && uv run mypy src
git diff --check
git add apps/api/src/easysynq_api/services/ingestion/storage.py apps/api/src/easysynq_api/services/ingestion/service.py apps/api/src/easysynq_api/services/ingestion/extract.py apps/api/tests/unit/test_ingestion_storage.py apps/api/tests/integration/test_ingestion.py
git commit -m "feat(upload): pin import staging locators to versions"
```

---

## Task 7: Carry import versions through commit, failure ledger, audit, and exact cleanup

**Files:**
- Modify: `apps/api/src/easysynq_api/services/ingestion/commit.py`
- Modify: `apps/api/src/easysynq_api/services/ingestion/service.py`
- Modify: `apps/api/tests/unit/test_ingestion_commit.py`
- Modify: `apps/api/tests/integration/test_ingestion.py`

- [ ] **Step 1: Write RED stable-reason and single-audit tests**

Unit-test a mapper from typed errors to exact import reason tokens:

```text
staging_version_required
upload_identity_digest_mismatch
upload_identity_size_mismatch
staged_source_missing
staged_source_changed
storage_unavailable_staging_put
storage_unavailable_versioning
storage_unavailable_source_get
storage_unavailable_source_read
storage_unavailable_target_head
storage_unavailable_target_get
storage_unavailable_target_read
storage_unavailable_copy
storage_unavailable_retention
worm_not_applied
target_identity_conflict
restage_source_changed
restage_source_unavailable
```

No `repr`, ETag, hash, bucket, version, filename, or endpoint may enter the reason string. Replace the current generic `repr(exc)[:500]` fallback in `_commit_items`: preserve existing allow-listed `_ItemCommitError.reason` tokens, map the new typed storage failures exactly, and map any unexpected exception to fixed `internal_error`. Emit `logger.exception("ingestion.commit.item_internal_error", extra={"extra_fields": {"run_id": str(run_id), "file_id": str(node.file_id)}})` separately for operator diagnosis; never copy that exception text into the ledger or audit. Add a unit guard that the persisted/audited reason can never be raw exception text.

- [ ] **Step 2: Return the failure audit row from the existing emitter**

Change `emit_import_event_system(...) -> AuditEvent` by constructing, adding, and returning the row; existing callers may ignore it. In `_record_failed`, accept optional typed rejection evidence, put its fixed context in the existing `IMPORT_ITEM_FAILED.after`, flush for audit ID, commit failed ledger + audit together, and return a small result containing `won` plus `AuditEventRef`.

Do not add `BLOB_INTEGRITY_FAILED` for imports.

- [ ] **Step 3: Parse the exact source in both commit paths**

At `_commit_document` and `_commit_record`, first read the authoritative Blob for `file.sha256`:

- if it is WORM-locked in the required documents/records domain, take the approved dedup branch with no source parse or staging version;
- if it exists in a foreign/non-WORM domain, retain the existing honest cross-domain failure and never use staging to overwrite that global identity;
- only if the Blob is absent, require `file.staged_blob_uri`, parse it against `file.sha256` with `file.mime_type or "application/octet-stream"` and `file.size_bytes`, and pass the exact `StagedObjectRef`.

For the promotion-required branch:

- document: raw `storage.promote_worm(source, target_bucket=documents)`;
- Record: `EvidenceInput(sha, content_type, source)` with no interactive rejection context, so the import outer transaction owns failure handling.

Blob row metadata comes from `PromotionResult`; keep the existing domain re-read, per-item owner/success audit, and per-item transaction.

- [ ] **Step 4: Sequence failed-ledger audit before cleanup**

In the per-node loop, catch typed `IdentityRefusal` separately, retain the typed refusal alongside its stable reason, allow the item session to roll back, then:

1. open the existing fresh `fs` transaction;
2. commit `record_failed_result` + one `IMPORT_ITEM_FAILED` with the fixed rejection payload;
3. only if that won/committed, exact-delete the refused source;
4. on delete failure, enqueue the Task 3 cleanup by audit reference.

If cleanup publication also fails, leave the exact source intact, emit the same terminal bounded operator signal as interactive cleanup, and do not broaden or inline-retry the delete.

Build the fixed rejection payload with `operation="import_commit"` and a system actor; this remains one `IMPORT_ITEM_FAILED`, never a duplicate generic integrity event.

An audit/ledger transaction failure performs no delete and propagates as commit infrastructure failure. Non-refusal storage errors retain the source and record a stable failed item without identity cleanup.

Catch `TargetIdentityConflict` as its own typed branch: after item rollback, commit the failed ledger plus one `IMPORT_ITEM_FAILED` containing the retain-source target-conflict evidence from Task 3, emit the terminal bounded alert, and perform no source cleanup. `WormNotApplied` and ordinary storage failures retain the source with only their stable reason context.

- [ ] **Step 5: Add the production restage-on-resume path for rejected items**

A partial run cannot reuse the deleted locator and the API container does not mount the import source. In the **commit worker** (which does have the read-only source mount), detect a prior failed-ledger reason in the restageable identity set (`upload_identity_digest_mismatch`, `upload_identity_size_mismatch`, `staged_source_missing`, `staged_source_changed`). Before retrying that item:

1. confine `run.source_root` and `file.rel_path` with the existing `FilesystemSourceProvider`;
2. reopen the source read-only and call `stage_stream(..., content_type=file.mime_type or "application/octet-stream")` to produce a new exact locator;
3. require the restaged digest and size to equal the inventory row; otherwise keep the old review evidence and record stable `restage_source_changed`/`restage_source_unavailable`;
4. update only that `ImportFile.staged_blob_uri` in the item transaction, preserving SHA, classification, proposal, and decisions;
5. promote the new exact source on the same resume attempt.

Do not auto-restage a never-attempted pre-change/legacy URI; those rows retain the approved restart/rescan requirement. Unit-test the restageable reason set, confinement, digest mismatch, and locator-only update.

- [ ] **Step 6: Add the peer-success/one-bad-item integration proof**

Create a two-item run. Pin false same-sized bytes for one exact import locator and leave the peer honest. Commit and assert:

- honest peer has one owner, Blob, and `IMPORT_ITEM_COMMITTED`;
- bad item has one failed ledger and one `IMPORT_ITEM_FAILED` with system actor/fixed evidence;
- bad item has no target/Blob/owner/success event;
- rejected exact source is deleted after audit while a newer version survives;
- run is `PartiallyCommitted`, classifications/decisions remain visible;
- restoring honest source bytes and resuming makes the worker restage a new exact version, updates only the failed locator, and creates exactly one final artifact without duplicating the peer.

Add the legacy no-version locator case with no acceptable WORM Blob: stable restart/rescan reason, review evidence preserved, no latest-resolution fallback. Add the converse cutover proof that a legacy locator with an already-authoritative correct-domain WORM Blob still deduplicates without staging; a foreign-domain Blob still fails rather than using that exception.

- [ ] **Step 7: Run focused import commit tests and commit**

```bash
cd apps/api && uv run pytest -m unit tests/unit/test_ingestion_commit.py tests/unit/test_ingestion_storage.py
cd apps/api && uv run pytest -m integration tests/integration/test_ingestion.py -k 'upload_identity or partial or resume or versioned_locator' -x
cd apps/api && uv run ruff check src/easysynq_api/services/ingestion/commit.py src/easysynq_api/services/ingestion/service.py tests/unit/test_ingestion_commit.py
cd apps/api && uv run mypy src
git diff --check
git add apps/api/src/easysynq_api/services/ingestion/commit.py apps/api/src/easysynq_api/services/ingestion/service.py apps/api/tests/unit/test_ingestion_commit.py apps/api/tests/integration/test_ingestion.py
git commit -m "feat(upload): audit and clean rejected import versions"
```

---

## Task 8: Migrate every server-generated producer without a key-latest escape hatch

**Files:**
- Create: `apps/api/tests/unit/test_generated_upload_identity.py`
- Modify: `apps/api/src/easysynq_api/services/vault/storage.py`
- Modify: `apps/api/src/easysynq_api/services/vault/service.py`
- Modify: `apps/api/src/easysynq_api/services/records/service.py`
- Modify: `apps/api/src/easysynq_api/services/packs/build.py`
- Modify: `apps/api/src/easysynq_api/tasks/packs.py`
- Modify: `apps/api/src/easysynq_api/services/ingestion/commit.py`
- Modify: `apps/api/tests/integration/test_structured_forms.py`
- Modify: `apps/api/tests/integration/test_packs.py`
- Modify: `apps/api/tests/integration/test_ingestion.py`

- [ ] **Step 1: Inventory the callers and make the guard RED**

Run and save the expected inventory in the new unit test/implementation notes:

```bash
rg -n "put_staging_bytes|finalize_worm|_legacy_finalize_sync|put_bytes\(.*_staging_bucket|_evidence_source_bucket" apps/api/src/easysynq_api --glob '*.py'
```

Expected pre-migration: six generated JSON freezes in `vault/service.py`, the import Markdown report, and the evidence-pack ZIP still stage/promote through separate calls or hidden bucket arguments. Add an AST-backed source guard that fails while any production call to `finalize_worm`, any `_evidence_source_bucket` keyword, any definition/reference of `_legacy_finalize_sync`, or any `put_bytes(..., bucket=..._staging_bucket())` call outside the storage implementation remains; do not rely on a same-line regex for multiline calls.

- [ ] **Step 2: Refactor the six generated document freezes through one helper**

Create `_ensure_generated_documents_blob(session, actor, *, payload, content_type, scope_ref, rejection_sessionmaker=None)`. It must:

1. hash the already-in-memory payload;
2. return existing correct documents-WORM dedup without staging;
3. call `source = await storage.put_staging_bytes(...)`;
4. call `promote_for_owner(... user_correctable=False, actor_type=system, operation="server_generated")` with the returned source directly;
5. insert/re-read the Blob row and enforce documents WORM domain.

Replace all six repeated blocks (`form schema`, objective commitment, risk register, context register, interested-party register, management-review minutes) with this helper. Preserve their existing outer commit/flush ownership and success audit behavior.

- [ ] **Step 3: Add generated mismatch mapping tests**

Monkeypatch `put_staging_bytes` to return a known source and `storage.promote_worm` to raise `UploadIdentityMismatch`. Assert the helper rolls back, writes a system `BLOB_INTEGRITY_FAILED`, exact-cleans after commit, and raises `503 storage_unavailable`, never 422.

Add one propagation test that asserts the exact object returned by `put_staging_bytes` is the object passed to promotion (identity, not a reconstructed key-latest ref).

- [ ] **Step 4: Migrate the evidence-pack ZIP**

Replace `put_bytes(... bucket=_staging_bucket())` with `put_staging_bytes` and pass `EvidenceInput(source=returned_ref)` plus a generated rejection context into `capture_record`. Thread the task-local `sessionmaker` from `tasks/packs._run_build` into `build`/rejection handling so fresh audit transactions never use the process-global pool across `asyncio.run` loops.

Keep pack `SEALED`, its generated Record, Blob, EvidenceBlob, and success audits in the existing owner transaction.

- [ ] **Step 5: Migrate the import Markdown report**

Capture `report_source = await put_staging_bytes(...)` and pass `EvidenceInput(source=report_source)` to `capture_record` **without** an inline rejection context, so `_attach_evidence` raises the raw typed failure. In `_finalize`, bind the `session.begin_nested()` handle: if report capture raises `IdentityRefusal` or `TargetIdentityConflict`, let the savepoint finish rolling back first, then call `reject_after_owner_rollback(..., operation="server_generated", rejection_sessionmaker=the commit worker's task-local maker)`. Catch the resulting generated 503 as the existing best-effort report failure and continue with the still-usable outer terminal transaction. Assert the run terminal update commits, the report Record/success rows do not, one system integrity audit commits, and exact cleanup follows only for a source refusal. Never call whole-session `rollback()` from inside the report savepoint.

- [ ] **Step 6: Add representative integration proofs**

- `test_structured_forms.py`: generated schema source records a real staged version and succeeds.
- `test_packs.py`: pack ZIP promotes the returned exact version and still seals once.
- `test_ingestion.py`: import report promotes the returned exact version and terminal report Record remains once-only.

Use a targeted monkeypatch in one representative generated path to force mismatch and assert 503/system audit/no success owner.

- [ ] **Step 7: Prove there is no legacy caller left**

Delete `_legacy_finalize_sync`, `finalize_worm`, the tuple-evidence normalization branch, and `_evidence_source_bucket`; narrow Record services to `Sequence[EvidenceInput]`. Only after those deletions run the guards below.

```bash
rg -n "finalize_worm|_legacy_finalize_sync|_evidence_source_bucket|put_bytes\(.*_staging_bucket" apps/api/src/easysynq_api --glob '*.py'
```

Expected: zero matches. Then inspect every remaining `delete_object` touching staging:

```bash
rg -n "delete_object\(" apps/api/src/easysynq_api/services/ingestion apps/api/src/easysynq_api/services/vault apps/api/src/easysynq_api/tasks
```

Expected: every staging/temp/rejection delete supplies `VersionId`; unrelated WORM purge code remains explicitly version-aware.

- [ ] **Step 8: Run focused generated tests and commit**

```bash
cd apps/api && uv run pytest -m unit tests/unit/test_generated_upload_identity.py tests/unit/test_storage_promotion.py tests/unit/test_upload_rejection.py
cd apps/api && uv run pytest -m integration tests/integration/test_structured_forms.py tests/integration/test_packs.py tests/integration/test_ingestion.py -k 'generated or schema or pack or report' -x
cd apps/api && uv run ruff check src/easysynq_api/services/vault/storage.py src/easysynq_api/services/vault/service.py src/easysynq_api/services/records/service.py src/easysynq_api/services/packs/build.py src/easysynq_api/tasks/packs.py src/easysynq_api/services/ingestion/commit.py tests/unit/test_generated_upload_identity.py
cd apps/api && uv run mypy src
git diff --check
git add apps/api/src/easysynq_api/services/vault/storage.py apps/api/src/easysynq_api/services/vault/service.py apps/api/src/easysynq_api/services/records/service.py apps/api/src/easysynq_api/services/packs/build.py apps/api/src/easysynq_api/tasks/packs.py apps/api/src/easysynq_api/services/ingestion/commit.py apps/api/tests/unit/test_generated_upload_identity.py apps/api/tests/integration/test_structured_forms.py apps/api/tests/integration/test_packs.py apps/api/tests/integration/test_ingestion.py
git commit -m "refactor(upload): propagate versions from generated staging"
```

---

## Task 9: Publish the exact contract and operational documentation

**Files:**
- Modify: `packages/contracts/openapi.yaml`
- Modify: `packages/contracts/.contract.lock`
- Modify: `apps/api/tests/unit/test_problem_code_contract.py` (only if a better diagnostic is needed; never weaken equality)
- Modify: `apps/api/tests/unit/test_api_request_validation.py`
- Modify: `docs/03-architecture-and-stack.md`
- Modify: `docs/04-document-control-and-vault.md`
- Modify: `docs/06-records-and-evidence.md`
- Modify: `docs/09-ingestion-engine.md`
- Modify: `docs/15-api-design.md`
- Modify: `docs/runbooks/minio-object-lock-prereq.md`
- Create: `docs/runbooks/upload-identity-rollback.md`
- Modify: `docs/runbooks/00-index.md`
- Modify: `.claude/rules/engineering-patterns.md`

- [ ] **Step 1: Make the request/response contract gap RED visibly**

First confirm the Task-3 problem vocabulary remains GREEN, then add assertions in `test_api_request_validation.py` for nullable/bounded `staging_version_id` on `CheckIn`/`EvidenceRef` and for documented 409/422/503 upload responses. Run:

```bash
cd apps/api && uv run pytest -m unit tests/unit/test_problem_code_contract.py tests/unit/test_api_request_validation.py -k 'problem or staging_version or upload_response'
```

Expected: problem-code parity stays GREEN; the new request-field/response assertions are RED because those OpenAPI surfaces are not published yet.

- [ ] **Step 2: Update request schemas and stable problems**

In OpenAPI:

- `CheckIn.staging_version_id`: nullable string, minLength 1, maxLength 1024; required conditionally only when no correct-domain dedup exists, described in prose;
- `EvidenceRef.staging_version_id`: same nullable bounded type, one per evidence item;
- retain the four Task-3 problem codes in `Problem.code` enum;
- document 422 mismatch/version-required, 409 exact-source-unavailable, and 503 storage/audit/WORM failures on document check-in, Record capture, and correction;
- keep init-upload request/response unchanged.

Add/retain unit assertions that Pydantic and OpenAPI agree on nullable/max-length behavior and that the new response statuses are documented.

- [ ] **Step 3: Regenerate the contract lock and generated validation artifacts**

```bash
bash scripts/gen-contracts.sh
npx --yes @redocly/cli lint --config packages/contracts/redocly.yaml packages/contracts/openapi.yaml
cd apps/api && uv run pytest -m unit tests/unit/test_problem_code_contract.py tests/unit/test_api_request_validation.py tests/unit/test_openapi_enum_parity.py
```

Expected GREEN. Commit `.contract.lock`; generated model/type directories are ignored build artifacts and must not be force-added.

- [ ] **Step 4: Correct the normative storage/upload prose**

Document these as-built facts:

- both temporary buckets are versioned but not WORM and have no blanket expiry;
- browser PUT version header/CORS and check-in pin;
- exact GET/hash before exact server-side copy; ETag is only precondition;
- WORM dedup carveout;
- Record evidence carries one version per attachment;
- import URI contains URL-encoded `versionId`, legacy rows require restart/rescan;
- audit-before-exact-delete and retry behavior;
- `upload_identity.metric` fixed-schema signals, sensitive-label prohibition, and operator alerts for sustained mismatches/terminal cleanup failure;
- `/readyz` blocks promotion if staging versioning is not enabled;
- rollback leaves bucket versioning enabled and disables incompatible promotion code.

Create `docs/runbooks/upload-identity-rollback.md`, link it from the MinIO prerequisite and the operator index, and make the rollback procedure operationally exact. Its first step establishes a reusable `ESQ_COMPOSE` Bash array: appliance operators use the installed `sudo easysynq-compose` helper; repository/online operators parse and strictly validate `EASYSYNQ_PROFILE` as only `s` or `m`, then build the exact `docker compose --env-file .env -f compose.yml -f compose.<validated-profile>.yml -f compose.production.yml` array and run `config --quiet`. Do not publish commands with `...`, `<profile>`, an unvalidated filename interpolation, or a base-only overlay set.

Use `"${ESQ_COMPOSE[@]}"` for every subsequent command:

1. Retain the new Compose, Caddy, and MinIO initialization configuration; never roll those files back and never suspend either staging bucket's versioning.
2. Set `EASYSYNQ_COMPATIBILITY_READ_ONLY=1` in the deployment environment and force-recreate only `proxy` with `"${ESQ_COMPOSE[@]}" up -d --no-deps --force-recreate proxy`.
3. Before changing application images, prove `POST /api/v1/rollback-write-probe` returns status 503 with the exact static guard body, prove `/healthz` remains reachable, and use an authenticated representative vault `GET` to prove already-committed content remains readable. Abort rollback if any proof fails.
4. Stop every promotion-capable asynchronous process with `"${ESQ_COMPOSE[@]}" stop worker beat`; confirm both containers are stopped. Then, and only then, introduce/start the older API image. Keep the compatibility guard enabled for its entire lifetime. Never start an older worker or Beat.
5. To recover, restore an exact-version-capable API image while the guard remains enabled, require `/readyz` to be 200 (including both versioning checks), set `EASYSYNQ_COMPATIBILITY_READ_ONLY=0`, and recreate `proxy` with `"${ESQ_COMPOSE[@]}" up -d --no-deps --force-recreate proxy`. Repeat the non-existent `POST /api/v1/rollback-write-probe` and require that the exact static guard response is gone (normally the API returns 404), proving routing without mutating business state; only then run `"${ESQ_COMPOSE[@]}" start worker beat` for the compatible processes.

Document that direct browser S3 PUTs use the separate MinIO origin and are not edge-blocked; with upload-init/check-in API writes blocked and all workers stopped they cannot promote anything. Already-issued presigned PUTs may leave harmless, versioned staging objects for a later compatible flow. Never run an exact-version-incompatible worker against versioned staging.

Remove/replace any claim that check-in trusts a client SHA, only HEADs size, or copies key-latest. Do not imply target WORM version ownership is solved.

- [ ] **Step 5: Add the reusable engineering rule**

In `engineering-patterns.md`, add a concise “version-bound staging promotion” rule: closed domain, exact VersionId for GET/COPY/DELETE, server hash before WORM, audit commit before rejected-source cleanup, correct-domain dedup exception, and task-local audit sessionmaker for workers. Defer shipped-history/Recent-learnings claims until Task 10 has fresh full-gate and mutation evidence.

- [ ] **Step 6: Verify docs/contract and commit Task 9**

```bash
npx --yes @redocly/cli lint --config packages/contracts/redocly.yaml packages/contracts/openapi.yaml
bash scripts/gen-contracts.sh --check
cd apps/api && uv run pytest -m unit tests/unit/test_problem_code_contract.py tests/unit/test_api_request_validation.py tests/unit/test_openapi_enum_parity.py
git diff --check
git add packages/contracts/openapi.yaml packages/contracts/.contract.lock apps/api/tests/unit/test_problem_code_contract.py apps/api/tests/unit/test_api_request_validation.py docs/03-architecture-and-stack.md docs/04-document-control-and-vault.md docs/06-records-and-evidence.md docs/09-ingestion-engine.md docs/15-api-design.md docs/runbooks/minio-object-lock-prereq.md docs/runbooks/upload-identity-rollback.md docs/runbooks/00-index.md .claude/rules/engineering-patterns.md
git commit -m "docs(upload): publish version-bound upload contract"
```

---

## Task 10: Run named mutations, full gates, and final scope audit

**Files:**
- No intended source changes; fix and commit only genuine failures found by verification.
- Modify after all gates are green: `docs/slice-history.md`
- Modify after all gates are green: `CLAUDE.md`

- [ ] **Step 1: Run the digest-accept mutation**

Temporarily change the production digest comparison in `storage.py` to accept mismatches. Run:

```bash
cd apps/api && uv run pytest -m unit tests/unit/test_storage_promotion.py -k mismatch
cd apps/api && uv run pytest -m integration tests/integration/test_vault.py tests/integration/test_records.py tests/integration/test_ingestion.py -k upload_identity_mismatch -x
```

Expected: storage plus all three false-byte domain proofs fail. Restore the exact comparison with `apply_patch`, rerun, and require GREEN.

- [ ] **Step 2: Run the source-version-substitution mutation**

Temporarily remove `VersionId` from `CopySource` (or substitute latest). Run:

```bash
cd apps/api && uv run pytest -m unit tests/unit/test_storage_promotion.py -k 'version and copy'
cd apps/api && uv run pytest -m integration tests/integration/test_upload_identity_storage.py -k overwrite -x
```

Expected: exact propagation and deterministic overwrite tests fail. Restore and rerun GREEN.

- [ ] **Step 3: Run the rolled-back-audit mutation**

Temporarily make rejection evidence use the owner session instead of the fresh sink. Run:

```bash
cd apps/api && uv run pytest -m unit tests/unit/test_upload_rejection.py -k 'order or durable'
cd apps/api && uv run pytest -m integration tests/integration/test_vault.py tests/integration/test_records.py -k 'upload_identity and audit' -x
```

Expected: sequencing/durable audit assertions fail. Restore and rerun GREEN.

- [ ] **Step 4: Run the key-delete/latest-delete mutation**

Temporarily remove `VersionId` from rejected cleanup. Run:

```bash
cd apps/api && uv run pytest -m unit tests/unit/test_storage_promotion.py -k delete
cd apps/api && uv run pytest -m integration tests/integration/test_upload_identity_storage.py tests/integration/test_vault.py -k newer -x
```

Expected: exact-delete argument and newer-version survival tests fail. Restore and rerun GREEN.

- [ ] **Step 5: Run the complete API fast loop**

```bash
cd apps/api && uv run ruff check .
cd apps/api && uv run ruff format --check .
cd apps/api && uv run mypy src
cd apps/api && uv run pytest -m unit
```

Expected: all four GREEN.

- [ ] **Step 6: Run the complete web loop**

```bash
cd apps/web && npm run lint
cd apps/web && npm run typecheck
cd apps/web && npm run build
cd apps/web && npm test
```

Expected: all four GREEN.

- [ ] **Step 7: Run contracts, integration, migration, and site-data gates**

```bash
npx --yes @redocly/cli lint --config packages/contracts/redocly.yaml packages/contracts/openapi.yaml
bash scripts/gen-contracts.sh --check
cd apps/api && uv run pytest -m contract --tb=short
cd apps/api && uv run pytest -m integration
cd apps/api && uv run alembic heads
bash scripts/check-no-site-data.sh
```

Expected: contract and integration suites GREEN; Alembic head remains exactly `0085`; R61 scan GREEN. Run the canonical throwaway-PG migration round trip from `.claude/commands/check-migrations.md` even though no migration changed, because ORM/storage-adjacent refactors must not mask drift.

- [ ] **Step 8: Perform the final mechanical scope audit**

```bash
rg -n "finalize_worm|_legacy_finalize_sync|_evidence_source_bucket" apps/api/src/easysynq_api --glob '*.py'
rg -n "delete_object\(" apps/api/src/easysynq_api/services/ingestion apps/api/src/easysynq_api/services/vault apps/api/src/easysynq_api/tasks
rg -n "VersionId" apps/api/src/easysynq_api/services/vault apps/api/src/easysynq_api/services/ingestion apps/api/src/easysynq_api/tasks/upload_identity.py
rg -n "target_version_id" apps/api/src/easysynq_api apps/api/migrations
rg -n "x-amz-version-id" infra apps/web apps/api/tests
git diff --check
git status --short
```

Expected: no legacy promotion/bucket escape hatch; every staging delete exact; all producers/readers/copies pin a version; target version appears only in transient storage/result handling and never a model/migration/audit payload; browser header is provisioned and consumed; only intentional changes are present.

- [ ] **Step 9: Record the shipped evidence only after the gates are green**

In `docs/slice-history.md` and `CLAUDE.md` Recent learnings, record the slice with the fresh command evidence from Steps 1–8: zero migration/head 0085, no target-version persistence, the four mutations that turned RED and were restored GREEN, and the complete gate result. Do not expand `CLAUDE.md` Current status beyond a short pointer.

```bash
bash scripts/check-no-site-data.sh
git diff --check
git add docs/slice-history.md CLAUDE.md
git commit -m "docs(upload): record version-bound promotion evidence"
```

- [ ] **Step 10: Request review and publish the branch**

Use a fresh review pass against the approved design, concentrating on transaction ordering, exact-version propagation, 404-versus-infrastructure mapping, worker loop/session ownership, privacy of public errors/metric labels, and mutation-test credibility. Fix findings with focused regression tests and commits. Then push/open or update the PR only when requested by the user.

All 12 required GitHub checks must be green before merge: `contracts`, `contract-responses`, `api`, `migrations`, four `integration-shards` plus `integration`, `web`, `security`, and `compose-images-lock`.
