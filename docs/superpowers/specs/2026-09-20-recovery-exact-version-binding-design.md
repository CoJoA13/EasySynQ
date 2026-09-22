# Persistent exact-version binding for recovery generations

Bounded design for the next step of
[`RES-SOURCE-INDEPENDENT-RECOVERY`](../../open-residuals.md#res-source-independent-recovery) and
[issue #3](https://gitlab.com/synqsuite-group/EasySynQ/-/issues/3) acceptance item 4. It covers the
**exact-version boundary only**; the service-capability boundary, complete encrypted generations and
the source-denied recovered-stack proof stay open and are not claimed here.

## Outcome and scope

A recovery generation must bind every referenced object to **its exact stored version**, not merely
to a bucket and key. Today `blob` records `bucket` and `object_key`
(`db/models/blob.py:35-36`), the manifest carries `sha256, size_bytes, bucket, object_key`
(`services/backup/archive.py:46-51`), and every restore path resolves whatever version is *current*
at restore time (`services/backup/drill.py:358-379`). A generation therefore describes an object
identity, not an object state.

This is not theoretical. WORM promotion already pins an exact source version and returns the sealed
target version as `PromotionResult.target_version_id`
(`services/vault/staged_identity.py:118`, `services/vault/storage.py:529-574`); the value is
computed, verified against a read-back, and then discarded when the `blob` row is written. The
binding this design adds is a value the write path already proves.

**In scope**

1. Persist the sealed object version on the `blob` row at write time.
2. Bind a generation to those versions: manifest v3 carries the version per blob.
3. Resolve bytes at that exact version in all three restore paths, failing closed when a bound
   version cannot be resolved, and assert `size_bytes`, which is carried but never checked today.
4. A bounded backfill for rows written before this slice, honestly labelled as *observed at backfill
   time*, never as *sealed at write time*.

**Out of scope, and unchanged:** the archive still contains **no object bytes**. Restore stays
source-dependent and remains a non-cutover integrity check. Every existing CURRENT LIMIT block, CLI
warning and `post_cutover_actions: []` compatibility key stays exactly as it is. No claim about
production recovery or upgrade eligibility follows from this slice.

## Why this first

Nothing downstream can be exact until the binding exists. A complete encrypted generation that
captured bytes without version binding would seal "some version of this key", which cannot be
reconciled against the database snapshot it ships with. The contract's wording — "binds every
referenced object to its exact version" — is a prerequisite, not a parallel task.

It is also the cheapest honest step: the value exists at the write path, and the failure mode it
closes is real. A content-addressed key is written once, so the current version is normally the
sealed one; an overwrite, a lifecycle action or an operator error creates a newer version that
today's restore would silently prefer, because only the bytes' sha256 is checked and a re-uploaded
object with equal bytes hashes identically.

## Model

`blob` gains two columns:

| Column | Type | Meaning |
|---|---|---|
| `object_version_id` | Text, nullable | The opaque store version id of the sealed object. |
| `object_version_source` | Text, nullable | `promotion` (returned by the verified write) or `backfill` (observed later). |

Nullable because rows predate the column; the pair is written together or not at all. A CHECK
constrains the source to the two literals and requires both columns to be null or both set.

`BlobRef` gains `object_version_id` and `object_version_source`, and the manifest becomes
`manifest_version: 3`. A v2 manifest stays readable: restore of an older generation keeps today's
behaviour and is reported as unbound, never upgraded by inference.

A generation records its binding state in `manifest["config"]["version_binding"]`:

| Value | Meaning |
|---|---|
| `sealed` | Every blob is bound and every binding came from `promotion`. |
| `observed` | Every blob is bound; at least one binding came from `backfill`. |
| `partial` | At least one blob has no binding. |
| `absent` | Manifest v2, or no blobs. |

## Restore behaviour

For each blob in a v3 manifest **with** a binding, the copy, the scratch re-hash and the stored
locator re-hash all address that exact version (`CopySource={... "VersionId": ...}` and
`get_object(VersionId=...)`), and the re-hash additionally asserts the object's length equals
`size_bytes`. Failure to resolve a bound version — `NoSuchVersion`, a delete marker, a digest
mismatch or a length mismatch — is a **FAIL** with a distinct reason, never a downgrade to the
current version. A blob with no binding keeps exactly today's current-version behaviour.

Restore reports the generation's binding state in its result so an operator can tell a `sealed`
generation from an `observed` one. No binding state makes a restore a cutover; `sealed` only means
the generation is exact about what it references.

## Backfill

`easysynq backup bind-versions` walks `blob` rows with no binding, resolves each object's current
version with a head request, and records `object_version_source='backfill'`. It is idempotent, never
overwrites an existing binding, and reports counts of bound, already-bound, missing and failed rows.
A missing object is reported, not silently skipped, and leaves the row unbound.

The honest limit, which the runbook and the command's own output must state: a backfilled binding
attests the version observed at backfill time. It cannot prove that version is the one originally
sealed. Only generations written after this slice can be `sealed`.

⚠ `RES-MINIO-VERSION-LIST-DENY` records that the pinned provider mishandles explicit
`ListBucketVersions` denials. This design needs no version *listing*: head, copy and get with an
explicit `VersionId` are sufficient, so it neither depends on nor relaxes that limitation.

## Implementation boundary

- One migration adding both columns and the CHECK, with a downgrade that drops them.
- Eight `pg_insert(Blob)` sites across seven modules (`services/vault/service.py` ×2,
  `services/vault/mirror.py`, `services/records/service.py`, `services/records/render.py`,
  `services/ingestion/commit.py`, `services/packs/portfolio.py`, `services/diff/visual.py`). Each
  must supply the version from its own verified write response. A site whose write cannot yield one
  records no binding rather than a guess, and its blob makes the generation `partial`.
- `archive.py` (BlobRef, manifest v3, binding state), `drill.py` (copy and both re-hash legs),
  `restore.py` (result reporting), one CLI subcommand.

## Acceptance evidence

Unit: manifest v3 shape and the four binding states; v2 manifests still read; the CHECK rejects a
half-written pair; restore reasons distinct for missing version, delete marker, digest mismatch and
length mismatch; backfill idempotence and its labelling.

Integration, against real MinIO with versioning: a generation over promoted objects is `sealed`;
**overwriting an object after the generation is written and then restoring resolves the bound
version, not the newer one** — the case that motivates the slice; a deleted version FAILs with its
own reason; a truncated object FAILs on length; a v2 archive restores exactly as before; the
backfill binds unbound rows and reports a missing object.

Mutation evidence is required for the motivating case: with the exact-version resolution reverted to
current-version behaviour, the overwrite test must fail, and be shown failing.

## Obligations deliberately still open

Object bytes remain outside the generation, so this slice proves nothing about recovery after source
loss. Service-capability separation, the certified worker-owned destination, fresh role-preserving
targets, streaming encryption (the envelope is still whole-archive in memory,
`services/backup/crypto.py:66-74`) and the source-denied boot-and-read proof all stay open under the
same record. `RES-SOURCE-INDEPENDENT-RECOVERY` and issue #3 do not close here.

## Amendment 1 — two further provenance values (approved by the owner 2026-09-22)

Implementation found the model above too narrow at two of the eight write sites, and shipped in
!63 (`c62ce09`) with two additional `object_version_source` literals. This amendment records them
so the design, the database CHECK (`ck_blob_object_version_binding`, migration `0093`) and
`services/vault/version_binding.py` say the same thing. The owner approved it as written on
2026-09-22, so the shipped behaviour is design policy, not only recorded fact.

| Value | Meaning | Effect on the generation state |
|---|---|---|
| `write` | A direct server-side put (not the WORM promotion) that returned a version id. | Counts as bound at write time: a generation whose bindings are all `promotion` or `write` is `sealed`. |
| `unversioned` | The write returned no version because the bucket has none — the `renditions` bucket is created without versioning. Carries no version id (the CHECK forbids one). | Neither seals nor spoils: such blobs are excluded from the `sealed` / `observed` / `partial` judgement, and a generation with only `unversioned` blobs is `absent`. |

The alternative — leaving rendition rows unbound — would make every generation `partial` forever,
which would hide the real `partial` case (a row that should be bound and is not). Renditions are
derived and rebuildable from the bound source objects, so a generation is exact about the
irreplaceable bytes without them. The `sealed` definition in the Model section therefore reads:
every bindable blob is bound and every binding came from `promotion` or `write`. Restore behaviour
is unchanged: an `unversioned` blob keeps today's current-version copy, and no binding is ever
inferred.
