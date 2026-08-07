# Design — `S-upload-identity`: verify exact staged bytes before WORM promotion

> **Status:** owner-approved design, pending written-spec review and implementation plan.
> **Date:** 2026-08-06 · **Repo commit at design time:** `d166295` · **Migration head:** `0085` (unchanged)
> **Authority:** PR #444's merged execution-order design, especially §2 D-C and §5.
> **Slice boundary:** mutable staging → content-addressed WORM storage. No production implementation is
> authorized by this document until the owner reviews this committed spec and approves the later plan.

---

## 1. Why this document exists

EasySynQ currently trusts a caller-declared SHA-256 at the exact point where trust matters most.
`services/vault/storage.py::finalize_worm` probes the staged key and then performs an unconditional
server-side copy into a WORM bucket. It does not read or hash the staged bytes. A caller can therefore:

1. declare `sha256(good)`;
2. PUT different bytes at the staging key, including different bytes with the same size; and
3. cause those bytes to be WORM-locked and represented by a `blob.sha256` value they do not have.

The same shared boundary serves controlled-document check-in, Record evidence, ingestion commit, and
server-generated artifacts. A false content address can therefore become a durable Document source,
Record attachment, or imported artifact and can be reused later through the global `blob.sha256` dedup
key.

There is a second defect in a naive repair. Calling the existing `hash_object()` and then the existing
`finalize_worm()` would still leave a time-of-check/time-of-use race: both staging buckets are mutable,
and the unbound copy can read a replacement written after the hash pass. Verification and promotion
must refer to the same exact source instance.

PR #444 ratified D-C: EasySynQ itself streams and hashes staged bytes **before** WORM promotion. This
design supplies the source-instance binding, failure contract, cleanup ownership, and falsifiers that
the execution-order document deliberately left to this slice.

### 1.1 Required RED falsifier

The implementation begins with the merged programme's fixed falsifier:

1. compute `sha256(good)`;
2. stage different, same-sized `evil` bytes under that claim;
3. attempt controlled-document check-in, Record evidence capture, and ingestion commit;
4. require refusal before any target copy, `Blob`/owner-row write, or success audit;
5. require a durable and truthful rejection audit followed by exact-source cleanup; and
6. bypass the digest comparison and prove those tests turn RED.

The same-size condition is load-bearing. It proves the server enforces content identity rather than
mistaking `Content-Length`, a multipart ETag, or a caller-supplied checksum header for SHA-256.

---

## 2. Binding invariants

The slice is complete only if every implementation choice preserves all of these invariants.

| ID | Invariant |
|---|---|
| **UI-1 — server authority** | The SHA-256 used as `blob.sha256` is recomputed by EasySynQ from a bounded stream of the source bytes. Browser claims, object keys, sizes, ETags, and checksum headers are not proof of content. |
| **UI-2 — one source instance** | The GET that is hashed and the server-side copy identify the same staging bucket, key, and `VersionId`. A later overwrite of that key cannot change the copied bytes. |
| **UI-3 — refusal before durability** | A digest mismatch produces no WORM target copy, `Blob`, `DocumentVersion`, Record/evidence row, import success ledger, or success audit. |
| **UI-4 — audit before deletion** | Rejection evidence commits in an independent transaction before EasySynQ attempts to delete the rejected source version. If the audit cannot commit, the source remains. |
| **UI-5 — exact cleanup** | Cleanup names the exact rejected `VersionId`. It never deletes the latest object by key and cannot delete a newer replacement. |
| **UI-6 — bounded resources** | Hashing is chunked and closes the response body on success, mismatch, cancellation, and read failure. The API never materializes an entire staged object. |
| **UI-7 — no insecure fallback** | A promotion that needs staging refuses a missing or unusable `VersionId`. It never falls back to a key-only GET/copy. |
| **UI-8 — caller-owned transactions** | The storage boundary does not commit, audit, delete staging, or translate to HTTP. Domain callers retain ownership of success atomicity and rejection sequencing. |
| **UI-9 — later target binding stays later** | This slice may use a returned target `VersionId` transiently to verify a copy, but it does not add or claim the durable WORM target-version schema reserved for `S-worm-retention` + `S-container-identity`. |

### 2.1 D-C and staging versioning are complementary

The server-computed SHA-256 remains the **content authority** ratified by D-C. Staging `VersionId` is
only the **source-instance coordinate** that closes the hash/copy race. EasySynQ never treats a version
ID or ETag as a digest, and readiness verifies rather than assumes that versioning is enabled.

This distinction also preserves PR #444's sequence:

- `S-upload-identity` binds a mutable **source** version without an Alembic migration;
- the later `S-worm-retention` + `S-container-identity` slice owns durable **target** object-version
  identity, retention ratcheting, and separated MinIO principals.

---

## 3. Owner decisions and alternatives

The owner approved these decisions on 2026-08-06.

| # | Decision | Selection |
|---|---|---|
| D1 | What happens to bytes rejected for false identity? | Commit audit evidence, then delete only the exact rejected staging version. Preserve the user's working/import state. |
| D2 | How is the source instance made stable? | Enable versioning on `staging` and `import-staging`. |
| D3 | What happens when source identity is absent? | Fail closed. The upload or pre-change import scan must restart; there is no SHA-only promotion fallback. |
| D4 | Promotion architecture | Stream/hash an exact `VersionId`, then server-side-copy that same version. |

### 3.1 Selected — version-bound server-side promotion

The producer supplies a typed source reference. The storage boundary GETs its exact version, hashes it
in bounded chunks, then copies that same version into the appropriate WORM domain. The response ETag is
carried as an additional `CopySourceIfMatch` guard but is never parsed as MD5 or SHA-256; multipart ETags
remain valid opaque validators.

This preserves the current direct-to-MinIO upload and server-side-copy architecture. A concurrent
overwrite becomes a different source version and cannot alter the verified copy.

### 3.2 Rejected — ETag-only conditional copy

Hashing the latest object and then copying only when its ETag remains unchanged is smaller operationally,
but cleanup remains key-racy and an ETag is a weaker object coordinate than an exact version. It also
does not implement the owner's decision to make staging versions explicit in the application contract.

### 3.3 Rejected — application-mediated relay

The API could stream the staged body through itself and upload those verified bytes into WORM storage.
That removes object-store source-binding semantics, but it moves every byte through the application and
introduces temporary-capacity, multipart completion/abort, retry, and secure-cleanup work. It is a much
larger change than the defect requires.

---

## 4. Scope and trust classes

One shared promotion boundary covers all current `finalize_worm` paths so callers cannot drift.

### 4.1 Browser-supplied, untrusted bytes

- controlled-document check-in;
- Record evidence capture; and
- Record correction evidence.

The browser computes the expected SHA-256, but that value is only an untrusted claim. The browser also
returns the opaque version header from its direct PUT so the server can identify the exact upload.

### 4.2 Import-worker bytes

- ingestion DOCUMENT commit; and
- ingestion RECORD evidence commit.

The scanner already hashes the source while streaming it into `import-staging`, but the canonical staging
key remains mutable until this change. The scan result must therefore retain the exact version produced
or selected by the scan, and extract/classify/commit must read that same staged locator rather than
re-resolving the latest key.

### 4.3 Server-generated bytes

The shared path also covers canonical form-schema JSON, objective commitments, risk/context/interested-
party registers, management-review minutes, generated import-report Markdown, and evidence-pack ZIPs.
Their expected hash is server-computed, but they still traverse a mutable staging bucket and must carry
the exact version returned by `put_object`.

### 4.4 Existing WORM dedup is not a staging promotion

If an authoritative `Blob` row already identifies a WORM object in the required domain, the caller may
reuse that object without a staging version. No staged bytes are trusted or promoted in this branch.
The current foreign-domain and WORM checks remain mandatory.

This is the only conditional exception to D3. API schemas therefore make `staging_version_id` nullable
for the dedup response shape, while services require it whenever no acceptable WORM `Blob` exists. An
older client cannot exploit the nullable wire shape to obtain key-only promotion.

### 4.5 Explicit non-goals

- No Alembic migration or new audit enum. `BLOB_INTEGRITY_FAILED` and `IMPORT_ITEM_FAILED` already exist.
- No durable target `object_version_id`, retention ratchet, MinIO principal split, or governance-bypass
  work; PR #444 assigns those to the next slice.
- No upload-size ceiling, presigned `Content-Length` enforcement, or browser-memory rewrite. Those remain
  in the separately owned `S-transfer-limits` and `S-web-transfer` slices. An expected size may be checked
  where a server path already has one, but this slice does not add a required browser size field.
- No general-purpose cleanup of abandoned valid working drafts/import runs. Rejected and temporary source
  versions are cleaned exactly; a broader TTL/janitor must preserve R9 scratch recovery and remains a
  separately designed lifecycle concern.
- No redesign of the global `Blob.sha256` primary key or cross-domain dedup semantics.

---

## 5. Source-reference contract

The storage layer receives a single immutable value object rather than parallel bucket/key/version
arguments:

```python
@dataclass(frozen=True, slots=True)
class StagedObjectRef:
    bucket: StagingDomain       # STAGING | IMPORT_STAGING; never arbitrary caller text
    object_key: str             # derived by the server, currently the lowercase expected SHA
    version_id: str             # opaque, bounded, neither empty nor the legacy "null" sentinel
    expected_sha256: str
    content_type: str
    expected_size: int | None = None
```

`StagingDomain` is a closed enum resolved through settings. Interactive clients provide only the
`version_id`; the server derives bucket and key from the endpoint and declared SHA. A client cannot use
the new field to make EasySynQ read an arbitrary bucket or object. The literal S3 `null` version is
rejected because it identifies an object created before versioning was enabled, not a version produced
under this contract.

The expected size is populated for imports and server-generated bytes because those producers already
know it. Browser size remains `None` until `S-transfer-limits`; SHA-256 verification is mandatory in all
cases.

The successful result is also typed:

```python
@dataclass(frozen=True, slots=True)
class PromotionResult:
    outcome: PromotionOutcome  # COPIED | ADOPTED_EXISTING
    verified_sha256: str
    size: int
    content_type: str | None
    retain_until: datetime
    source: StagedObjectRef
    source_etag: str
    target_bucket: str
    target_key: str
    target_version_id: str  # required transient verification value; not persisted by this slice
```

Typed storage exceptions carry the safe facts needed by the caller's audit/error orchestration:

- `StagingVersionRequired`;
- `StagedSourceUnavailable`;
- `UploadIdentityMismatch` with expected/observed digest and size;
- `StagedSourceChanged` for a failed copy precondition;
- `StorageUnavailable`; and
- `WormNotApplied` / `TargetIdentityConflict`.

These exceptions are service-layer types, not `ProblemException`. Storage does not know whether its
caller is HTTP, Celery ingestion, or a server-generated freeze.

---

## 6. Producer and API changes

### 6.1 Browser PUT and CORS

MinIO returns `x-amz-version-id` on a successful PUT to a versioned bucket. The staging CORS policy must
expose both `x-amz-version-id` and `ETag` to the EasySynQ web origin.

`apps/web/src/lib/upload.ts::putToPresigned` changes from `Promise<void>` to a typed result containing the
non-empty version ID. It still sends no bearer token and does not trust or interpret the ETag. A 2xx PUT
without `x-amz-version-id` is an upload failure; the web client does not call check-in/capture.

Document check-in adds:

```jsonc
{
  "sha256": "...",
  "staging_version_id": "opaque-version-id", // null only after init-upload said dedup=true
  "change_reason": "...",
  "change_significance": "MINOR",
  "mime_type": "application/pdf"
}
```

Each Record `EvidenceRef` gains its own nullable `staging_version_id`, because a capture may mix existing
WORM-dedup evidence and newly uploaded evidence. Duplicate evidence entries with the same SHA but
different version IDs are rejected as ambiguous rather than silently choosing the first one.

Init-upload request bodies remain unchanged in this slice. Their responses still indicate `dedup` and
provide the presigned URL; the source version exists only after the browser PUT.

### 6.2 Working-draft behavior

The existing `working_draft` and check-out lock remain the authority for who may upload/check in. A
mismatch keeps the row, metadata, lock, and existing SHA scratch marker so the author retains recovery
context. The exact rejected version is deleted, and the SHA marker alone can never authorize promotion;
the next attempt must supply a new exact version. Invalid bytes are not preserved under R9. No successful
`CHECKIN`/`NO_CHANGE` event is emitted for the rejected attempt.

A pre-deployment scratch reference that has no exact staging version cannot be promoted. The author is
told to re-upload; document metadata and the working draft remain.

### 6.3 Import staging URI

`StagedResult` gains `version_id`, and its existing text locator becomes an exact, URL-encoded URI:

```text
s3://import-staging/<sha256>?versionId=<opaque-url-encoded-version-id>
```

This uses the existing `import_file.staged_blob_uri` text column and requires no migration. A single
parser validates the scheme, allow-listed bucket, expected key, and non-empty version. Extract and
commit consume the parsed locator; they no longer fetch `import-staging/{sha}` as an unversioned latest
object.

The scan's temporary upload uses an unguessable `_tmp/<uuid>` key. Because that key has one producer,
the worker can HEAD it immediately after the existing high-level `upload_fileobj` call to capture the
exact temporary version even though boto3's high-level helper returns no result. The canonical copy from
that exact temporary version returns the version stored in `StagedResult`.

The current key-only `_object_exists` shortcut is forbidden. Before reusing an existing canonical
`import-staging/<sha>` object, the scanner captures its exact latest version and streams that pinned
version through the shared verifier. It reuses only a digest match; a mismatch or storage error is a
typed integrity failure, not proof that the key is absent. Two scanners that both observe absence may
create redundant canonical versions, but each source was independently hashed to the same SHA and is
safe to pin.

Every delete in a versioned staging bucket—including `_tmp/...` cleanup—must specify the exact version;
a plain delete would create a delete marker and leave the bytes behind.

Pre-change import rows whose URI has no `versionId` fail closed with a stable restart/rescan reason. The
existing run, classifications, and decisions remain visible; they are not silently promoted from a
newly resolved latest object.

### 6.4 Server-generated staging

`put_staging_bytes` returns a `StagedObjectRef` populated from the PUT result. Every generated-content
caller passes that object directly into promotion. A missing version header on a supposedly versioned
bucket is an infrastructure failure, not a reason to re-resolve the key.

---

## 7. One verification-and-promotion boundary

The current separate head/copy flow becomes one logical operation, implemented synchronously behind the
existing `asyncio.to_thread` boundary.

### 7.1 Ordered algorithm

1. **Validate the reference.** Resolve the closed staging domain, require a non-empty version ID, and
   require the server-derived object key to match the expected lowercase SHA convention.
2. **Read the exact source.** Call `GetObject` with bucket, key, and `VersionId`. Confirm the response
   identifies the requested version; capture its ETag and content metadata.
3. **Hash while streaming.** Read bounded chunks, update SHA-256 and byte count, and close the body in a
   `finally` block on every outcome. A retry after a read fault restarts from byte zero; partial hashes
   are never resumed.
4. **Compare before copy.** Compare the computed SHA-256 with `expected_sha256` using a constant-time
   comparison. Where `expected_size` is already known, compare the count too. A mismatch raises a typed
   refusal before any target request.
5. **Recover an existing target if necessary.** If no `Blob` row exists but the target key already does,
   capture its latest target version and stream-hash that exact version. Require the expected digest and
   active retention, then return `ADOPTED_EXISTING` only for that exact compliant object. This closes
   copy-success/DB-rollback and ambiguous-copy retry without blindly adding another immutable version.
6. **Copy the verified instance.** Call `CopyObject` with a `CopySource` that includes the exact source
   `VersionId` and with `CopySourceIfMatch` set to the captured ETag. A 412 is a source-identity refusal,
   not an automatic re-hash-and-retry into success.
7. **Verify the target.** Require a non-empty target version from the copy, HEAD that exact version, and
   require the expected size plus an object-lock retain-until value. Return typed metadata without
   persisting target-version ownership or committing.

The source ETag is an opaque copy precondition. Multipart-origin ETags are explicitly covered by real
MinIO tests and are never compared to SHA-256.

### 7.2 Copy and retry boundaries

- GET/read failures retain the source and map to a retryable infrastructure failure.
- A copy retry reuses the same source version and ETag; it never resolves latest.
- A copy that may have succeeded is not blindly repeated. The next attempt follows step 5 and adopts an
  exact, retained target if present.
- A target at the content-addressed key whose bytes do not hash to that key is a
  `TargetIdentityConflict`. EasySynQ does not overwrite or represent it with a `Blob` row; it records an
  integrity failure and alerts an operator.
- Concurrent correct promotions may still create redundant WORM versions before one global `Blob` row
  wins. Every such version has independently verified bytes, so this is an efficiency residual rather
  than a false-identity path. Durable target-version ownership remains the next slice.

### 7.3 Caller responsibilities

After a successful result, each caller keeps its existing transaction discipline:

- insert/re-read the conflict-safe `Blob` row;
- re-assert the correct WORM domain;
- create its Document/Record/import owner rows;
- append the existing success audit; and
- commit those database changes together.

The storage helper never owns those writes. Record capture retains its existing physical-object advisory
locks through commit; this slice does not introduce a second lock order.

---

## 8. Failure, audit, and cleanup contract

### 8.1 Stable outward errors

The canonical problem vocabulary and OpenAPI problem schema gain these codes:

| Condition | HTTP/API behavior | Retry posture |
|---|---|---|
| Promotion needs staging but `staging_version_id` is absent | `422 staging_version_required` | Re-upload with the current client. |
| Exact source hashes to different bytes or known size differs | `422 upload_identity_mismatch` | Re-upload; never retry the rejected version. |
| Exact version is missing, deleted, or fails the copy precondition | `409 staged_source_unavailable` | Restart the upload; do not resolve latest. |
| GET/read/copy/object-store failure | `503 storage_unavailable` | Retain source; retry from byte zero with the same identity where the outcome is unambiguous. |
| Retention is absent, target identity conflicts, or rejection audit cannot commit | `503 storage_unavailable` | Fail closed and alert; do not claim success. |

Interactive messages are calm and corrective. They do not expose observed hashes, ETags, bucket names,
or version IDs. Import persists stable reason tokens instead of `repr(exc)`.

For server-generated content, an impossible digest mismatch is an internal integrity failure and maps to
`503 storage_unavailable`, even though it carries the same typed mismatch evidence internally. There is
no user upload to correct.

### 8.2 Rejection audit transaction

The storage helper raises a typed refusal without deleting anything. The caller then:

1. rolls back the primary business transaction;
2. writes rejection evidence in a fresh short transaction;
3. commits that audit transaction; and only then
4. attempts exact-version deletion.

Interactive document/Record and server-generated paths reuse `BLOB_INTEGRITY_FAILED`, keyed as the
existing integrity scanner does (`object_type=config`, `object_id=org_id`). The actor remains the real
user for interactive requests and `system` for detached producers. `scope_ref` carries the document
identifier or another existing non-secret operation scope when one exists.

The append-only `after` payload has a stable shape:

```jsonc
{
  "operation": "document_checkin | record_capture | server_generated",
  "classification": "digest_mismatch | size_mismatch | source_missing | source_changed",
  "source": {
    "bucket": "staging",
    "object_key": "<claimed-sha>",
    "version_id": "<opaque>",
    "etag": "<opaque-or-null>"
  },
  "expected": {"sha256": "...", "size_bytes": null},
  "observed": {"sha256": "...", "size_bytes": 1234},
  "cleanup": {"policy": "delete_exact_version_after_audit"}
}
```

The audit contains no uploaded body, filename, presigned URL, credentials, or user-facing storage
details. It states the cleanup policy rather than a mutable completion status because `audit_event` is
append-only.

Ingestion keeps its existing per-item rollback and fresh failure transaction. `IMPORT_ITEM_FAILED`
records the same stable classification/source/expected/observed context in `after`, alongside the failed
ledger row. It does **not** emit a duplicate `BLOB_INTEGRITY_FAILED` for the same item.

Success events (`CHECKIN`, `RECORD_CAPTURED`, and `IMPORT_ITEM_COMMITTED`) remain in the owner
transaction. No rejection path may emit one.

### 8.3 Exact cleanup and reconciliation

After the rejection audit commit returns:

- delete with bucket + key + exact `VersionId`;
- treat an already-absent exact version as idempotent success;
- never issue a key-only delete and never select latest;
- keep the working-draft/import rows while the committed audit/failed ledger identifies the rejected
  exact version; any later promotion must carry a new exact staged reference; and
- record cleanup success/failure in structured logs and bounded metrics keyed only by operation/bucket,
  never by digest or version labels.

If synchronous deletion fails, an idempotent cleanup task retries from the exact source reference stored
in the committed audit. The task validates the allow-listed staging bucket and audit classification
before deleting. Repeated failure raises an operator alert; it does not change the original mismatch
response into success and cannot broaden to another version.

If the rejection-audit transaction fails, cleanup is not attempted and the outward result is
`503 storage_unavailable`. Evidence must never disappear before the durable record that authorized its
deletion.

### 8.4 Infrastructure failures are not false-identity cleanup

A mid-stream read error, object-store outage, or ambiguous copy response is not proof that the staged
bytes are wrong. These failures retain the source, emit operational metrics/logs, and use the recovery
algorithm in §7.2. Only a durable identity/source refusal authorizes exact-version deletion.

---

## 9. Deployment and rollback

### 9.1 Idempotent MinIO provisioning

`infra/compose/minio/minio-init.sh` idempotently enables versioning on `staging` and `import-staging` and
installs the response-header CORS exposure needed by the browser. It does not change object-lock or
retention on those temporary buckets.

This slice does not install a blanket current/noncurrent-version expiry rule. Such a rule could silently
discard the recoverable scratch required by R9 or a long-running reviewed import. Rejected versions and
scanner temporary versions are removed explicitly by exact identity; storage growth from abandoned
otherwise-valid staging remains visible operational debt for a lifecycle design that can consult those
references before expiring them.

The API/storage readiness gate verifies both buckets report versioning `Enabled`. A bucket that is
missing, suspended, or inaccessible blocks new staging promotion. EasySynQ never silently degrades to
the former unversioned flow.

Every staging delete site is audited as part of implementation review. Once versioning is enabled, a
plain key delete creates a marker rather than deleting the intended bytes and is forbidden for exact
cleanup.

### 9.2 Cutover behavior

- The MinIO configuration lands before API/web code begins accepting the new flow.
- API/OpenAPI and web upload orchestration ship together in the single-host Compose release.
- Existing unversioned staging objects become legacy/null versions and cannot satisfy the new contract.
- Browser uploads in flight at cutover receive restart guidance while preserving their working draft.
- Pre-change import runs without versioned locators retain their review evidence but require
  restart/rescan before commit.
- Existing correct-domain WORM dedup remains usable because it does not depend on staging.

There is no Alembic revision. `working_draft.scratch_blob_ref` and `import_file.staged_blob_uri` remain
text columns; only the locator content becomes exact.

### 9.3 Rollback

Versioning remains enabled after deployment; rollback never suspends it or strips object versions. If
the application must roll back to code that cannot enforce exact source identity, new upload promotion is
disabled until a compatible application is restored. Read paths and already-committed vault content
remain available.

This is an intentional fail-closed operational migration, not a reversible security downgrade.

---

## 10. Verification contract

### 10.1 Storage unit tests

Add focused tests around the shared boundary:

1. same-sized `good`/`evil` bytes produce `UploadIdentityMismatch`, close the body, and make no copy;
2. GET, copy, and cleanup receive the exact source `VersionId`;
3. the response ETag is threaded only as a copy precondition;
4. a true 404 maps to missing source while access-denied/5xx HEAD or GET responses remain storage
   failures; mid-stream exceptions, copy failures, and absent retention map to their typed outcomes;
5. every stream closes on success and every failure branch;
6. an expected size is checked where supplied but cannot substitute for SHA-256; and
7. an existing exact WORM orphan is re-hashed and adopted, while a false target identity is refused.

The core mismatch test must be RED on `d166295` and must call the production `_finalize_sync` boundary,
not a pure helper that the broken production path bypasses.

### 10.2 Real MinIO integration tests

Unit fakes cannot prove the store's version and precondition semantics. Docker-backed tests must prove:

- both temporary buckets are versioned and PUT returns a non-empty version ID;
- a correct multipart object promotes even though its ETag is not a SHA-256 digest;
- a different same-sized multipart object is refused before target copy;
- after verification of `good`, writing a newer `evil` version at the same key cannot make `evil` reach
  WORM—the copy must contain the pinned `good` version or fail cleanly;
- exact-version rejection cleanup leaves a newer replacement version untouched; and
- a failed copy/read retains the source for retry.

The overwrite race is deterministic: pause immediately before the production `CopyObject`, write the
newer source version with the real MinIO client, then release the copy. A timing-only stress loop is not
an acceptable substitute.

### 10.3 End-to-end domain tests

**Controlled document**

- init upload, PUT same-sized false bytes, and check in;
- assert the stable 422 code, durable `BLOB_INTEGRITY_FAILED`, exact rejected version deletion, preserved
  working draft/lock, no target object/`Blob`/`DocumentVersion`, and no `CHECKIN`/`NO_CHANGE`;
- re-upload honest bytes with a new version and succeed once.

**Record evidence**

- exercise both capture and the shared correction/evidence path as appropriate;
- assert no base documented-information row, Record, `Blob`, `EvidenceBlob`, or `RECORD_CAPTURED` survives
  the mismatch transaction;
- assert the independent rejection audit and exact cleanup survive; and
- retry honest evidence successfully.

**Ingestion**

- after scan, replace or select a false exact import-staging version for one item while a peer remains
  honest;
- assert the bad item gets one failed ledger row and `IMPORT_ITEM_FAILED`, with no vault owner/success
  event, while the peer item succeeds;
- assert the run remains partial/reviewable; and
- restage/rescan honest bytes and prove resume creates exactly one final artifact.

**Server-generated callers**

- representative tests prove `put_staging_bytes` returns and every caller propagates a versioned source;
- a generated-content mismatch maps to infrastructure failure and a system integrity audit, never a
  user-correctable 422.

### 10.4 Browser and contract tests

- `putToPresigned` returns `x-amz-version-id`, remains bearer-free, and rejects a missing header;
- document orchestration sends the version on check-in and omits it only for explicit WORM dedup;
- Record evidence serializes one version per non-dedup attachment;
- an omitted client checksum header cannot bypass verification, and any altered checksum metadata the
  object store accepts cannot override the server-computed verdict;
- OpenAPI, problem-code lockstep, generated client types, and contract-response tests include all four
  new stable problem codes.

### 10.5 Mutation checks

The implementation review performs four named mutations:

| Mutation | Test that must fail |
|---|---|
| Force the digest comparison to accept | storage mismatch plus all three false-byte end-to-end tests |
| Remove/substitute the verified source `VersionId` from the copy | unit propagation and deterministic real-MinIO overwrite tests |
| Write the rejection audit inside the rolled-back owner transaction | document/Record durable-audit assertions |
| Delete staging by key or delete latest | newer-replacement survival test |

A mutation that leaves the suite green means the proof is not exercising the production safety boundary.

### 10.6 Full gates and observability

The PR must pass the targeted API/web/MinIO suites and all 12 repository PR checks. Runtime metrics
distinguish:

- identity mismatch by operation and classification;
- missing/legacy source version;
- storage failure by GET/copy/retention stage; and
- cleanup retry/final failure.

Hashes, object keys, version IDs, user IDs, and filenames are forbidden as metric labels. Individual
mismatches are audited; sustained mismatch rates and any terminal cleanup failure alert operators.

---

## 11. Acceptance criteria

1. Every non-dedup document, Record, ingestion, and generated-content promotion streams and hashes an
   exact staging version before WORM copy.
2. Different same-sized bytes cannot create a target copy, owner row, `Blob`, or success audit under the
   claimed SHA-256.
3. A concurrent overwrite cannot alter the verified source instance; only the pinned version can copy.
4. Multipart ETags and client checksum headers are never treated as SHA-256 authority.
5. Missing source identity fails closed with a stable restart response; no key-only fallback exists.
6. Rejection evidence commits before exact-version deletion, and deletion failure is safely retryable
   without endangering a newer version.
7. The working draft/import review state survives rejection while the invalid locator cannot be reused.
8. Stream memory is bounded and every response body closes on all paths.
9. Existing correct-domain WORM dedup remains functional without fabricating a staging identity.
10. No Alembic migration, target-version claim, retention/principal change, transfer-limit work, or global
    dedup redesign enters the slice.
11. The fixed RED falsifier and four mutation checks prove the production boundary.
12. All targeted suites and all 12 repository checks pass with fresh evidence.

---

## 12. Implementation touchpoints for the later plan

This is a design inventory, not permission to edit production code. The implementation plan should
decompose work across these existing seams:

- `infra/compose/minio/minio-init.sh` and Compose readiness/setup tests;
- `apps/api/src/easysynq_api/services/vault/storage.py` typed references, bounded verification, source-
  bound copy, exact delete, and orphan adoption;
- document and Record API request models plus their service callers;
- ingestion staging locator production, exact reads, commit propagation, and failure ledger/audit;
- server-generated staging callers, including reports and packs;
- a short-transaction rejection sink and exact cleanup retry task;
- `apps/api/src/easysynq_api/problems.py`, OpenAPI, contract locks, and API documentation;
- `apps/web/src/lib/upload.ts`, authoring orchestration, and Record evidence orchestration where present;
  and
- focused unit, real-MinIO integration, domain integration, browser, and mutation tests.

The plan must start from RED tests and keep production edits serialized around the shared storage helper.
No later recovery/container slice may be folded into this PR.

---

## 13. Primary object-store references

- [AWS S3 CopyObject API](https://docs.aws.amazon.com/AmazonS3/latest/API/API_CopyObject.html) —
  `CopySource` version selection and conditional copy semantics.
- [AWS S3 multipart upload overview](https://docs.aws.amazon.com/AmazonS3/latest/userguide/mpuoverview.html)
  — multipart ETags are not content SHA-256 values.
- [MinIO S3 API compatibility](https://docs.min.io/aistor/developers/s3-api-compatibility/) — the
  deployed store's S3 compatibility contract.
- [MinIO object versioning](https://docs.min.io/aistor/administration/objects-and-versioning/versioning/)
  — version IDs, delete markers, and version-aware cleanup behavior.
