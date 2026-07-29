# Issue #363 — carry the initiating generator into Evidence Pack builds

> Owner-approved design for the worker-principal fast-follow from Batch 6. Backend + migration +
> integration tests + authoritative docs. Migration head `0082 → 0083`; no new permission key,
> endpoint, or response field.

## 1. Problem

`POST /evidence-packs/{id}/generate` re-authorizes a Finding/CAPA subject in the request, commits
`BUILDING`, and enqueues a Celery task carrying only the pack id. The worker then reloads
`evidence_pack.created_by` and treats that original creator as the generator.

That creates two related authorization defects:

1. A retry from `FAILED` initiated by another user classifies candidate evidence with the
   creator's `record.read` grants. The retrying generator can therefore seal and later share bytes
   selected with another principal's authority.
2. Revoking the initiating generator's `finding.read`, `capa.read`, or related dossier-subject read
   after the generate commit but before dossier construction does not stop the worker from sealing
   the now-unreadable subject.

Passing a user id only in the Celery arguments is not authoritative enough. A delayed message from
an older attempt could arrive after the reaper marks the pack `FAILED` and a different generator
starts a new attempt, then seal the new attempt using the stale message identity.

## 2. Decision — the locked pack row owns build authority

Migration `0083` adds two nullable attempt-context fields to `evidence_pack`:

- `build_requested_by`, a RESTRICT FK to `app_user`; and
- `build_source_ip`, a PostgreSQL `text` value that preserves the accepted request representation.

`generate_pack` writes both fields in the same transaction that changes the pack to `BUILDING`.
The task continues to carry only `pack_id`. After taking the organization pack-build lock and the
pack row lock, the worker reads the current attempt context from that row.

This makes the row-lock/idempotency boundary authoritative:

- a redelivered task uses the same persisted attempt context;
- a delayed old task that arrives after a later retry uses the later retry's context, not stale
  task arguments; and
- two overlapping deliveries serialize on the pack row, so only the first live `BUILDING` attempt
  can seal.

Existing non-DRAFT rows are backfilled to `created_by` for upgrade compatibility. A missing
initiating principal on a newly processed build remains fail-closed; the worker never silently
falls back to a different user.

## 3. Source-IP policy

The generate request's `request.client.host` is snapshotted losslessly into `build_source_ip`. Text
is intentional: the existing PDP compares `ip_allow` values as exact strings, while PostgreSQL
`inet` would canonicalize an expanded IPv6 spelling after the request-time decision and could make
the worker deny that same accepted context. At worker time:

- grants, explicit DENYs, account status, and `valid_from` / `valid_until` are evaluated fresh;
- the authorization clock is the worker's current time; and
- only the request-bound `source_ip` attribute is replayed from the initiating generate request.

This treats the asynchronous build as work performed on behalf of the accepted request. It
preserves legitimate `ip_allow`-restricted reads instead of failing every such build merely because
a worker has no client socket, while still allowing later revocation, expiry, or a new explicit
DENY to stop the seal.

Preview classification receives the live create request IP. Seal-time classification receives the
persisted generate request IP. The preview and build therefore evaluate `record.read` with the same
kind of request context; a changed grant between them is intentionally reflected by the final
worker classification.

The stored address is not a new trust input: the API derives it through the same
`request.client.host` path used by the existing PEP. This slice does not add forwarded-header trust
or CIDR interpretation.

## 4. Worker authorization

One shared subject-check resolver produces the exact permission/resource questions used at create,
generate, and build:

- Finding subjects require `finding.read`;
- each Finding source audit requires `audit.read`;
- a linked auto-CAPA requires `capa.read` at its process scope;
- CAPA subjects require `capa.read` at their process scope; and
- an origin Finding requires `finding.read`.

The request paths continue through `enforce`. The worker path uses the same PEP evaluation and
authorization-audit sink with an explicit `RequestContext(now, source_ip, actor_user_id)`. Because
each Celery invocation creates a task-local engine for its fresh `asyncio.run()` loop, that sink's
independent short transaction is bound to the same task-local sessionmaker and disposed with it.

Immediately before `build_dossier`, the worker evaluates every subject check for the persisted
initiating generator. Any denial:

1. emits the normal authorization decision hook;
2. rolls back the build transaction;
3. moves the pack to `FAILED`;
4. records a bounded error reason; and
5. emits `PACK_BUILD_FAILED`.

No dossier bytes are constructed or sealed after the denial.

## 5. Generator attribution and evidence classification

The initiating generator, not `created_by`, is used for every authority-bearing or attribution
operation in the worker:

- `classify_candidates(..., record.read)` with the persisted source IP;
- the final subject-read checks;
- `capture_record(..., captured_by=generator)` for the sealed pack Record;
- `PACK_GENERATED`; and
- `PACK_BUILD_FAILED`.

`created_by` retains its original meaning: who created the DRAFT header and preview. The new field
means who initiated the current/latest build attempt. No API response field is added in this slice;
the durable sealed attribution remains the pack Record and audit trail.

## 6. Failure and retry semantics

- Non-active or missing initiating users fail the build before content classification.
- A denied subject check fails only Finding/CAPA builds; Clause/Process packs have no dossier
  subject but still classify evidence with the initiating generator.
- A failed attempt retains its attempt context for diagnosis. A later successful
  `generate_pack` overwrites it atomically with the new caller and IP.
- The stalled-build reaper does not rewrite attempt context.
- Stage 2 portfolio generation uses already-sealed membership and does not make a new read-
  authorization decision.

## 7. Verification

Docker-backed integration coverage must prove:

- a different generator retry includes/excludes evidence from that generator's `record.read`, not
  the creator's;
- revocation after `generate` but before `build_dossier` moves the pack to `FAILED` and does not
  seal a pack Record;
- a matching `ip_allow` survives request-to-worker handoff for subject reads and evidence
  classification;
- a mismatched/revoked/expired grant still denies at the worker;
- the sealed Record and `PACK_GENERATED` actor are the initiating generator;
- an old/redelivered task cannot inject a prior attempt's identity; and
- existing Clause/Process and Finding/CAPA happy paths remain green.

Migration coverage proves upgrade/backfill/downgrade/re-upgrade, FK metadata parity, app-role
access, and clean `alembic check`.

## 8. Documentation

Add R58 to `docs/decisions-register.md`; amend Evidence Pack generation in
`docs/06-records-and-evidence.md`, the entity in `docs/14-data-model.md`, and the worker contract in
`docs/15-api-design.md`; record the persisted-worker-principal pattern in
`.claude/rules/engineering-patterns.md`; close the residual in `docs/slice-history.md`; and update
the remediation tracker accurately.

## 9. Non-goals

- No new permission key, endpoint, pack status, or response field.
- No change to R27 legal erasure, pack retention, sharing, or portfolio assembly.
- No trust of `X-Forwarded-For` or new reverse-proxy policy.
- No CIDR/range expansion of the PDP's existing exact `ip_allow` comparison.
- No correction-chain transitive-read redesign.
- No change to dormant `concrete_type` (#345) or obsolete-version policy (#406).
