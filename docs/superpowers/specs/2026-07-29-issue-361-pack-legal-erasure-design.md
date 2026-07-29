# Issue #361 — erase evidence-pack derivatives under an R27 legal order

> Owner-approved and implemented design for the physical-erasure fast-follow from Batch 6. Backend
> + migration + OpenAPI + integration tests + docs. Migration head `0081 → 0082`; no new permission
> key and no new endpoint.

## 1. Problem

A sealed Evidence Pack contains two independent copies of its source material:

- the canonical ZIP, registered as a WORM `EVIDENCE` Record under the system-managed
  `PERMANENT` / `RETAIN_PERMANENT` policy; and
- the optional PDF portfolio, cached as a non-WORM rendition blob.

When an included Record is destroyed, Batch 6's serve-time guard stops new pack downloads and share
links. It does not erase those copied bytes. An already-issued direct S3 URL remains usable until it
expires, and the ZIP remains reachable through the generic
`/records/{pack_record_id}/evidence/{sha}/download` route. The legal-order erasure is therefore
incomplete even though the dedicated pack routes fail closed.

The pack's own permanent policy is intentional. An ordinary retention-driven DESTROY of a member
does not authorize overriding that separate policy or its WORM lock. R27's dual-controlled legal
order is the authority that can cover every derivative copy of the erased content.

## 2. Decision — full invalidation, R27-only

Adopt Issue #361 Option A for an R27 WORM destroy only:

1. Every sealed pack whose bytes or dossier embed the source Record becomes `UNAVAILABLE`.
2. Every live share link for that pack is revoked in the same database transaction.
3. The pack's registered `EVIDENCE` Record receives a derived R27 disposition tombstone.
4. The ZIP and portfolio blob rows/pointers are removed and authority-bound
   `pending_blob_purge` markers are written before commit.
5. The bytes are physically purged immediately after commit; the existing hourly marker reaper
   completes any storage-failure or crash gap.

`UNAVAILABLE` is terminal. The pack header, membership, seal, summaries, and audit history remain
as the tombstone proving what artifact existed and why it was invalidated.

Ordinary policy DESTROY remains unchanged: it does not purge an independently retained pack or
revoke its links. The existing conservative serve-time destroyed-member check remains in place, so
such a pack is retained but not newly delivered. This slice does not silently broaden ordinary
retention authority into governance bypass.

## 3. Schema and audit lineage

Migration `0082` adds:

- `UNAVAILABLE` to PostgreSQL `pack_status` and Python `PackStatus`;
- `evidence_pack.invalidated_at`;
- `evidence_pack.invalidated_by_disposition_event_id`, a nullable FK to the source R27 event;
- `disposition_event.derived_from_disposition_event_id`, a nullable self-FK used only by a
  derivative-copy R27 tombstone; and
- additive audit event `PACK_INVALIDATED`.

An unavailable pack must name the source event and have both artifact pointers cleared. The
pack-record tombstone copies the original requester's id, approver's id, and legal basis and links
back to the original immutable event. It does **not** fabricate a second
`worm_destroy_request`: the original two-person legal order is the authority for all exact
derivative copies, and the self-FK records that lineage explicitly.

For a legal order that directly destroys the pack's own `pack_record_id`, that source event is
already the pack-record tombstone; no second derived event is written.

Each invalidated share row receives `revoked_at`, the approving actor as `revoked_by`, and a
legal-erasure reason. `PACK_SHARE_REVOKED` remains the per-link audit event and
`PACK_INVALIDATED` records the pack-level source Record, event, request, and purged artifact
identities.

## 4. Build-versus-destroy serialization

One organization-scoped PostgreSQL transaction advisory read/write lock closes the last-copy race:

- Pack Stage 1 and Stage 2 take the **shared** transaction lock before locking or building a pack.
- R27 approval takes the **exclusive** transaction lock before locking the request or source Record.

Different pack builds may still run concurrently because their locks are shared. An R27 destroy is
rare and waits for any already-running pack builds in that organization:

- if a build wins first, it seals and the legal-order transaction then discovers and invalidates
  it; or
- if the destroy wins first, the build starts after the tombstone is committed and the existing
  classification/subject guards exclude or refuse the erased Record.

The shared lock is acquired before the pack-row lock, and the exclusive lock before the source
Record/request locks. That fixed order prevents a pack-row ↔ legal-erasure advisory-lock deadlock.
Both locks are transaction-scoped and release automatically on commit, rollback, or connection
loss.

## 5. Complete dependency discovery

One repository dependency resolver is shared by the serve-time guard and legal-erasure cascade. A
pack depends on a Record when it is:

- an `INCLUDED` `pack_item` Record;
- the pack's own registered `pack_record_id`;
- a FINDING/CAPA dossier subject;
- a dossier's origin finding, linked CAPA, or source audit; or
- a correction predecessor/successor whose identifier is copied into a finding dossier.

The successful builder persists the exact dossier-only shared-PK Record set in
`evidence_pack.embedded_record_ids_at_seal` (`[]` for new non-dossier seals). Runtime serve and R27
discovery read that immutable snapshot, not current correction pointers. Legacy null snapshots use
the current relation only when the target Record existed no later than `generated_at`.

The R27 transaction resolves affected `SEALED` packs in its organization, locks them in stable UUID
order, and re-checks the dependency after locking. Artifact-SHA closure also includes any pack that
points at the exact same ZIP or portfolio bytes: content-addressed equality means it is another
copy of the artifact, even if reached through a different header.

DRAFT and FAILED packs contain no sealed copy and are not invalidated. A BUILDING pack cannot cross
the exclusive legal-erasure lock; it either finishes before discovery or observes the committed
tombstone after the lock releases.

Before any liveness decision, the transaction collects source evidence, every affected pack
Record's evidence/rendition, and detached ZIP/portfolio identities, then locks the complete set in
lexical SHA order. Liveness treats `ARCHIVE_COLD`/`TRANSFER` Records as preserved owners despite
their `DISPOSED` state; only a destructive disposition event removes Record ownership.

## 6. Authority-bound purge

The original Record, event, and executed request remain the root authority. For each affected pack:

- the pack Record is flipped to `DISPOSED`;
- a derived `DispositionEvent(action=DESTROY, is_worm_destroy=true)` names the original event;
- the ZIP uses the ordinary Record evidence cleanup and live-referencer check;
- the portfolio uses an explicit pack-pointer liveness check; and
- each physical object receives a marker bound to the pack Record, its derived event, and the
  original request.

The reaper accepts that marker only if:

1. the target pack Record is `DISPOSED`;
2. the target event is a matching derived R27 tombstone;
3. its source event is a lawful R27 event;
4. the executed request belongs to the source event's Record; and
5. requester, approver, legal basis, org, and lineage agree across all rows.

The marker's bypass boolean never establishes authority. ZIP deletion may use governance bypass;
the non-WORM portfolio does not request bypass. Physical-object advisory locks from Issue #359 and
exact `(bucket, object_key)` ownership checks from Issue #360 remain unchanged.

Blob deletion remains liveness-safe. A shared blob row is removed only after no unaffected live
Record, document version, or available pack pointer still owns the same physical object. The
invalidated pack pointers are cleared regardless, so no pack route retains reachability to a
shared object that must remain for another lawful owner.

## 7. Route closure

After the successful database commit:

- dedicated authenticated pack download sees `UNAVAILABLE`;
- public landing/download sees the revoked link / unavailable pack;
- new share minting is refused;
- generic pack-record evidence download has neither a live Record attachment nor blob row; and
- deletion of the physical object invalidates every already-issued S3 URL.

If storage deletion fails, the pack is already unavailable, its database pointers and blob row are
gone, and an authority-bound marker durably schedules the remaining byte erase. The only residual
reachability is an already-issued direct S3 URL until the marker reaper succeeds; that storage
outage case is explicit and recoverable rather than silently reported as complete.

## 8. Verification

Docker-backed integration coverage must prove:

- full two-person R27 flow over an included Record invalidates the pack, revokes its links,
  disposes the pack Record, erases ZIP/portfolio rows and bytes, and closes both the generic Record
  route and a URL issued before destruction;
- the pack-record derived event and every marker validate through the original R27 lineage;
- a storage failure leaves an unavailable pack plus markers, and the reaper later removes both
  artifacts;
- a forged or mismatched derived lineage is refused without an S3 call;
- ordinary retention DESTROY does not change the pack status, pointers, share rows, or independent
  retention policy;
- dossier-only dependencies and direct `pack_record_id` destruction take the same path;
- shared artifact liveness never deletes bytes still owned by an unaffected live object; and
- a deterministic build-versus-destroy race yields either seal-then-invalidate or
  destroy-then-refuse, never a live post-erasure pack.

Unit and migration tests pin enum parity, task/lock registration, schema constraints, app-role
privileges, and the dependency/lineage decision tables.

## 9. Documentation

Add an R27 derivative-copy addendum to `docs/decisions-register.md`; amend Evidence Pack properties
in `docs/06-records-and-evidence.md`; update the entities in `docs/14-data-model.md`, route/status
prose in `docs/15-api-design.md`, OpenAPI `EvidencePack.status`, the recurring WORM/lock-order
pattern, and the shipped slice history.

## 10. Non-goals

- No physical invalidation for ordinary retention-driven DESTROY.
- No deletion of pack headers, membership, seals, or audit history.
- No attempt to recall an archive the recipient already downloaded outside EasySynQ.
- No new permission key, endpoint, or user-facing workflow.
- No worker-principal change from Issue #363.
- No change to the dormant `concrete_type` selector (#345) or version-history policy (#406).
- No broader redesign of cross-domain content-addressed deduplication.
