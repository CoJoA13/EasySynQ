# S-worm-retention + S-container-identity — Atomic Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every committed WORM owner resolve to one immutable MinIO object version protected through the longest live owner obligation, and move every privileged storage, R27, audit-signing, backup, migration, and identity capability into a purpose-separated non-root service boundary.

**Architecture:** Migration `0089` establishes exact WORM identity, an exhaustive owner registry, staged retention revisions, closed hold/R27 authority and execution state, and narrow runtime roles on a clean deployment only. A shared `services/vault/retention.py` boundary protects exact versions before owner visibility and a PostgreSQL-claiming `retention-maintenance` process completes forward-only extensions. Ordinary domain-hold release is two-stage: the API may create a non-authoritative intent, but only a host-invoked `hold-release-authorizer` one-shot can authorize its exact digest; a no-ingress, no-bypass `hold-maintenance` process independently revalidates owners and may set legal hold `OFF`. A separate browser-facing `r27-authorizer` validates a dedicated 120-second WebAuthn/LoA2 OIDC token and signs canonical requester/approver/cancellation attestations; a no-ingress `r27-maintenance` process can act only after a separately signed recovery-generation witness. Compose then delivers one credential set, network set, and writable-path set per service, with static non-root web serving and no shared runtime `.env`.

**Tech Stack:** Python 3.12, FastAPI/Pydantic v2, SQLAlchemy async, Alembic/PostgreSQL 16, boto3/MinIO GOVERNANCE object lock, Ed25519/`cryptography`, RFC 8785 canonical JSON (`rfc8785`), Keycloak 26.7 Authorization Code + PKCE S256 + WebAuthn LoA2, React 19/TypeScript, `oidc-client-ts`, TanStack Query, Vitest/MSW, Playwright, Docker Compose, Caddy. **Spec:** the owner-approved `docs/superpowers/specs/2026-08-18-s-worm-retention-container-identity-design.md` in this documentation checkpoint.

## Global Constraints

- This is one **atomic implementation PR and one release verdict**. Tasks are review checkpoints, not independently mergeable or shippable slices. The final tree must not contain a feature flag or profile that permits only retention or only identity separation to ship.
- Immediately before creating `0089`, run `cd apps/api && uv run alembic heads`. Continue only if the sole output is `0088_bootstrap_credential`; otherwise stop, rebase, renumber the migration, and update this plan before changing schema.
- Upgrade is **clean-deployment only**. Before any schema DDL, `0089` refuses non-empty `blob`, `document_version`, `evidence_blob`, `pending_blob_purge`, `worm_destroy_request`, or physical pack-owner state with `unsupported_legacy_physical_owner_state`. It never infers a VersionId from a key, digest, bucket latest, or version enumeration.
- Downgrade is a schema test path only. It refuses once an asserted WORM Blob, retention proposal/operation, R27 authority/execution/witness, maintenance intent, or new exact purge marker exists. Old images are never restarted against `0089` state.
- WORM is GOVERNANCE only. Setup cannot select COMPLIANCE. A recorded or actual COMPLIANCE state stops readiness with an operator-safe mismatch; nothing relabels or downgrades it.
- A MinIO VersionId is opaque text of 1–1024 characters. It is never parsed for ordering or time. Every WORM GET, HEAD, retention read/write, legal-hold read/write, hash, presign, recovery read, and delete supplies the exact `VersionId`.
- Non-WORM rendition, portfolio, mirror, and visual-diff Blob rows remain valid without WORM assertion fields. They retain explicitly named key-based non-WORM functions and are never included in the WORM owner registry.
- Normal storage paths may extend retention and set legal hold `ON`. They cannot shorten retention, set hold `OFF`, or pass `BypassGovernanceRetention`. A host-authorized ordinary domain release may set hold `OFF` through the separate no-bypass hold principal only after an exact owner recheck. Only an already claimed and fully revalidated R27 execution may combine hold release with Governance bypass/delete for one exact version.
- Remote over-retention is safe. A WORM owner or active longer policy value never commits before exact-version retention/hold read-back. A DB rollback after physical extension creates a reportable over-retained orphan, not permission to weaken storage.
- The closed WORM owner registry contains only `document_version.source_blob_sha256`, live `evidence_blob.blob_sha256 -> record`, and the defensive live sealed-pack pointer already backed by permanent Record evidence. Adding a new owner family requires extending registry, max-owner, hold, race, and exact-version tests in this PR.
- `PERMANENT` and active domain holds mean exact-version legal hold `ON`. Ordinary hold release is a typed asynchronous operation whose API-created row is not authority; host authorization of its exact digest and a fresh maintenance owner recheck are both required before physical `OFF`. Permanent release requires R27 and can never use the ordinary hold path.
- The R27 manifest lifetime is `R27_MANIFEST_TTL_SECONDS=86400` by default, validated to 300–604800 seconds. This is separate from the dedicated token lifetime, which is at most 120 seconds. Any owner change to this default must happen during plan review, before production code.
- R27 nonces are 32 random bytes encoded unpadded base64url. Legal basis is normalized non-empty UTF-8 text up to 4,000 characters. Bounded error codes are at most 64 characters; safe operator detail is at most 512 characters and never includes provider bodies, credentials, object keys, site names, or bearer tokens.
- Production starts with an empty recovery-generation verifier registry and has no recovery private key, witness endpoint, or recovery signer service. The real deployment therefore stops at `WAITING_FOR_RECOVERY_GENERATION`. Only isolated integration tests may inject an ephemeral public key and in-memory private test signer.
- All state-changing R27 browser actions use a new popup PKCE session with `max_age=0` and essential `acr=urn:easysynq:acr:r27-webauthn`; the token is memory-only, sent only to `/r27-authority/`, and cleared after the action settles. The ordinary API token is never sent there.
- Long-running first-party images use numeric identities: API-family UID/GID `10001:10001`, static web UID/GID `10002:10002`. They run with `read_only: true`, `cap_drop: [ALL]`, `no-new-privileges`, and only declared tmpfs/writable mounts. Runtime commands execute `/app/.venv/bin/...` directly; they never run `uv sync`, npm, or Vite.
- The common `.env` contains only non-secret topology/configuration and `*_FILE` paths. A service receives only its declared read-only secret mounts. Inline plus file-backed forms of the same secret are a startup error.
- API/general worker/Beat have no DB-owner DSN, MinIO root/bypass secret, R27 key, backup key, audit private key, or Keycloak bootstrap admin. Beat has Redis only and schedules no privileged work. The API cannot reach an executor network or submit a privileged Redis task.
- Tasks 1–6, 7–11, and 12–17 touch shared schema, storage, authority, and deployment seams and execute serially. Parallel agents may write tests/review disjoint surfaces only after the task owner records an active claim under the repository coordination workflow.
- Every behavior task begins with the listed RED proof and records the actual failure reason. A new test that unexpectedly passes is a blocker until its falsifier is corrected. Skipped/unavailable checks stay explicitly unverified.
- After every GREEN task: run the listed focused gate, `git diff --check`, inspect `git status --short`, stage only that task's files, and use the listed commit subject. Do not push or merge until the final combined verification and review.
- The release-blocking live walkthrough uses synthetic data only and follows the approved destructive-scope protocol: enumerate exact project containers/networks/volumes/binds, revalidate the disposable target, then reset only that project. Never print or commit the user's local credentials or generated secrets.

---

## Owner Decisions Recorded Before Task 1

The owner approved both decisions interactively on 2026-08-18, and the approved design records them as D15 and D16 in this documentation checkpoint:

1. **Approved — split ordinary hold release.** Use a host-invoked, DB-only `hold-release-authorizer` plus a separate no-ingress, no-delete, no-bypass `hold-maintenance` identity. This closes the gap between “a generic row is never authority” and the need to clear a non-permanent physical hold without granting the R27 principal a second authority path.
2. **Approved — 24-hour R27 manifest.** Default `R27_MANIFEST_TTL_SECONDS` to 86,400 seconds, with deploy-time validation restricted to 300–604,800 seconds. This is the lifetime of the two-person signed destruction manifest while it waits for recovery-generation proof; it is not the WebAuthn action-token lifetime, which remains at most 120 seconds.

If either decision changes later, update the spec, schema/service maps, tests, Compose topology, acceptance matrices, and this plan through a new owner-reviewed documentation checkpoint before changing production behavior.

---

## Checkpoints and Dependency Order

| Atomic review checkpoint | Tasks | Release status |
|---|---:|---|
| A — schema, exact assertion, closed owner registry | 1–4 | Reviewable only; must not merge/release |
| B — all WORM producers/consumers and forward-only maintenance | 5–6 | Reviewable only; must not merge/release |
| C — signed R27 authority, recovery gate, exact executor | 7–11 | Reviewable only; must not merge/release |
| D — purpose-separated services, secrets, images, profiles | 12–16 | Reviewable only; must not merge/release |
| E — contracts, browser, full proof, documentation | 17–19 | Final combined release verdict only |

The serial dependency is:

`1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7 -> 8 -> 9 -> 10 -> 11 -> 12 -> 13 -> 14 -> 15 -> 16 -> 17 -> 18 -> 19`.

---

## Exact Interface and Ownership Map

| Surface | Exact responsibility | Owning task |
|---|---|---:|
| `migrations/versions/0089_worm_retention_container_identity.py` | Clean guard, enums/tables/columns/indexes, role creation, grants, trigger/functions, empty downgrade | 1–2 |
| Blob assertion | Exact physical identity, immutable initial assertion, monotone verified state, retained historical row | 1–2 |
| Document retention authority | Pinned policy or installation-minimum identity plus check-in basis date | 1, 4 |
| `retention_revision` / `retention_operation*` | Separately visible proposed values, exact target work ledger, atomic activation after all targets verify | 1, 6 |
| `services/vault/worm.py` | `WormObjectLocator`, `WormRequirement`, `WormObjectState`, `VerifiedWormAssertion`, typed failures | 3 |
| `services/vault/storage.py` | Exact-version WORM read/apply/hash/presign/delete; key-only APIs named non-WORM | 3 |
| `services/vault/retention.py` | Exhaustive owner registry, max obligation, pack/physical lock order, protect-before-commit | 4 |
| WORM producers | Documents, Record evidence, imports, permanent pack artifacts persist returned VersionId and use shared protection even on dedup | 5 |
| WORM consumers | Document/Record downloads, verification, mirror source, backup/recovery inventory and R27 preflight use exact locators | 5 |
| `retention-maintenance` | PostgreSQL-claimed extensions/event updates/ordinary exact purges; no Redis, hold OFF, or bypass | 6, 12 |
| `hold-release-authorizer` | Host-invoked one-shot confirmation of an exact ordinary hold-release digest; DB authorization only, no MinIO/Redis/ingress | 6, 13–16 |
| `hold-maintenance` | Claim only host-authorized ordinary release rows, revalidate exact owners, set hold OFF with no delete/bypass | 6, 12–16 |
| `services/r27/canonical.py` / `crypto.py` | RFC 8785 canonical bytes/digests, Ed25519 signatures, immutable schema versions | 7 |
| `auth/r27_tokens.py` | Dedicated issuer/audience/azp/role/sid/ACR/auth-time/lifetime/jti validation | 7 |
| `r27-authorizer` | Prepare/commit canonical request, approval, cancellation; authorizer signing only; no MinIO | 8 |
| Recovery verifier key manager | Host-only public-key install/retire/revoke; production registry empty | 9 |
| `r27-maintenance` | Witness/key revalidation, exact pre-hash, pack-exclusive source transaction, exact purge/resume | 10 |
| Main API/general Celery | Read-only R27 status and ordinary jobs only; legacy R27/reaper/privileged tasks absent | 11–12 |
| Audit/backup maintenance | DB-backed schedules/intents and purpose-specific credentials outside Beat/general worker | 12 |
| `config_file_secrets.py` | Allow-listed scalar secret-file source with no-follow/regular/bounded/conflict checks | 13 |
| MinIO/Keycloak bootstrap/reconcile | Ordinary/retention/hold/R27 policies; bounded post-ready installation of generated provision/export/role-manager client secrets; R27 client/role/LoA2 flow; host enrollment | 14–16 |
| Images/Compose/Caddy | Numeric users, static web, service commands, secret mounts, networks, ingress isolation, S/M parity | 15 |
| Installer/appliance/doctor | Clean order, secret generation, volume ownership, operator commands, readiness and parity | 16 |
| OpenAPI/generated clients/web | Combined edge contract with runtime ownership, read-only main status, separate R27 token path and status UI | 17 |
| Live/CI/docs | Full gates, real MinIO/Keycloak/browser/profile walkthrough, authority truth and handoff evidence | 18–19 |

### Public Python contracts

```python
@dataclasses.dataclass(frozen=True, slots=True)
class WormObjectLocator:
    bucket: str
    object_key: str
    object_version_id: str

@dataclasses.dataclass(frozen=True, slots=True)
class WormRequirement:
    retain_until: datetime.datetime | None
    legal_hold: bool

@dataclasses.dataclass(frozen=True, slots=True)
class WormObjectState:
    locator: WormObjectLocator
    mode: Literal["GOVERNANCE"]
    retain_until: datetime.datetime
    legal_hold: bool
    read_at: datetime.datetime

@dataclasses.dataclass(frozen=True, slots=True)
class VerifiedWormAssertion:
    locator: WormObjectLocator
    asserted_retain_until: datetime.datetime
    asserted_at: datetime.datetime
    verified: WormObjectState

async def read_worm_state(locator: WormObjectLocator) -> WormObjectState: ...
async def apply_worm_protection(
    locator: WormObjectLocator, requirement: WormRequirement
) -> VerifiedWormAssertion: ...
async def stream_hash_exact(locator: WormObjectLocator) -> tuple[str, int]: ...
async def presign_worm_get(locator: WormObjectLocator) -> str: ...
async def delete_worm_version(
    locator: WormObjectLocator, *, release_hold: bool, bypass_governance: bool
) -> None: ...  # imported only by services.r27.executor
```

```python
class WormOwnerKind(enum.StrEnum):
    DOCUMENT_SOURCE = "DOCUMENT_SOURCE"
    RECORD_EVIDENCE = "RECORD_EVIDENCE"
    SEALED_PACK_DEFENSIVE = "SEALED_PACK_DEFENSIVE"

@dataclasses.dataclass(frozen=True, slots=True)
class ProposedWormOwner:
    kind: WormOwnerKind
    owner_id: uuid.UUID
    org_id: uuid.UUID
    blob_sha256: str
    basis_date: datetime.date
    duration: str
    requires_hold: bool

async def protect_proposed_owner(
    session: AsyncSession,
    *,
    owner: ProposedWormOwner,
    promotion: PromotionResult,
) -> VerifiedWormAssertion: ...

async def list_live_worm_owners(
    session: AsyncSession,
    locator: WormObjectLocator,
    *,
    proposed_owner: ProposedWormOwner | None = None,
) -> tuple[WormOwner, ...]: ...
```

### R27 edge contracts

The combined edge OpenAPI describes these paths, while app factories mount only their owned subset:

```text
GET  /api/v1/records/{record_id}/r27
     normal bearer; record.read; read-only status/history

GET  /r27-authority/v1/auth/config
     public client/issuer/callback/ACR configuration; no secret
POST /r27-authority/v1/actions/prepare
     dedicated bearer; body {action, record_id?, request_id?, legal_basis?}
POST /r27-authority/v1/actions/{challenge_id}/commit
     same dedicated bearer; body {action_nonce}
GET  /r27-authority/v1/requests/{request_id}
     dedicated bearer; current canonical manifest and authority state
```

`prepare` writes one challenge keyed by `(iss,jti)`, returns its server-generated action nonce and the canonical manifest, and does not consume the token. `commit` row-locks that challenge, verifies the same token/claims and current permission/state, consumes it atomically, writes the signed attestation/transition, and never accepts client-supplied bucket, key, VersionId, digest, owner, or derivative fields.

### Ordinary hold-release edge contracts

The existing normal-bearer legal-hold route stays synchronous for placement and becomes explicitly asynchronous for release:

```text
POST /api/v1/records/{record_id}/legal-hold
     body {active: true, reason}; 200 Record after exact hold ON read-back
     body {active: false, reason}; 202 HoldReleaseAccepted

GET  /api/v1/records/{record_id}/hold-releases/{operation_id}
     normal bearer; record.read; immutable request/authorization/physical status
```

`HoldReleaseAccepted` contains only `operation_id`, `record_id`, `state=PENDING_AUTHORIZATION`, `requested_at`, and an operator-safe message. `HoldReleaseStatus` enumerates `PENDING_AUTHORIZATION`, `AUTHORIZED`, `RUNNING`, `FAILED`, `VERIFIED`, and `CANCELLED_PRE_START`, with bounded error/result and timestamps; it never exposes object coordinates, credentials, host identity, or provider body. The browser cannot authorize an operation. Only the host command from Task 6 can create the non-app-writable authorization.

---

## Task 1: Establish the clean-only `0089` schema and ORM contract

**Files:**
- Create: `migrations/versions/0089_worm_retention_container_identity.py`
- Create: `apps/api/tests/migration/test_0089_worm_retention_container_identity.py`
- Create: `apps/api/src/easysynq_api/db/models/_worm_enums.py`
- Create: `apps/api/src/easysynq_api/db/models/document_worm_config.py`
- Create: `apps/api/src/easysynq_api/db/models/retention_revision.py`
- Create: `apps/api/src/easysynq_api/db/models/retention_operation.py`
- Create: `apps/api/src/easysynq_api/db/models/retention_operation_target.py`
- Create: `apps/api/src/easysynq_api/db/models/worm_hold_release_operation.py`
- Create: `apps/api/src/easysynq_api/db/models/worm_hold_release_authorization.py`
- Create: `apps/api/src/easysynq_api/db/models/_r27_enums.py`
- Create: `apps/api/src/easysynq_api/db/models/r27_request.py`
- Create: `apps/api/src/easysynq_api/db/models/r27_manifest.py`
- Create: `apps/api/src/easysynq_api/db/models/r27_manifest_target.py`
- Create: `apps/api/src/easysynq_api/db/models/r27_manifest_derivative.py`
- Create: `apps/api/src/easysynq_api/db/models/r27_action_challenge.py`
- Create: `apps/api/src/easysynq_api/db/models/r27_authorizer_key.py`
- Create: `apps/api/src/easysynq_api/db/models/r27_attestation.py`
- Create: `apps/api/src/easysynq_api/db/models/r27_execution.py`
- Create: `apps/api/src/easysynq_api/db/models/recovery_generation_verifier_key.py`
- Create: `apps/api/src/easysynq_api/db/models/recovery_generation_witness.py`
- Create: `apps/api/src/easysynq_api/db/models/audit_maintenance_schedule.py`
- Create: `apps/api/src/easysynq_api/db/models/backup_maintenance_operation.py`
- Modify: `apps/api/src/easysynq_api/db/models/{blob,document_version,record,retention_policy,pending_blob_purge}.py`
- Modify: `apps/api/src/easysynq_api/db/models/__init__.py`
- Modify: `migrations/env.py`

**Interfaces:**
- Consumes: sole head `0088_bootstrap_credential`; current empty physical-owner tables; existing Record policy/basis and pack/disposition foreign keys.
- Produces: linear `0089`; exact Blob assertion; document authority/config; pending revision/target state; R27/recovery/maintenance rows; empty downgrade/re-upgrade path.

- [ ] **Step 1: Recheck the live migration head before writing the revision**

```bash
(cd apps/api && uv run alembic heads)
```

Expected: exactly `0088_bootstrap_credential (head)`. Any other result stops Task 1.

- [ ] **Step 2: Write four isolated migration RED fixtures**

Use Testcontainers PostgreSQL and upgrade only to named revisions. Assert:

1. empty `0088 -> 0089` succeeds;
2. parameterized legacy rows in `blob`, `document_version`, `evidence_blob`, `pending_blob_purge`, `worm_destroy_request`, or pack physical-pointer state each refuse with `unsupported_legacy_physical_owner_state`, leave `alembic_version=0088_bootstrap_credential`, and leave the reflected schema unchanged;
3. populated `0089` state refuses downgrade without changing schema/version;
4. empty `0089 -> 0088 -> 0089` succeeds.

Run before creating the migration:

```bash
(cd apps/api && uv run pytest tests/migration/test_0089_worm_retention_container_identity.py -q)
```

Expected RED: the named revision/module is absent; do not accept a generic migration-coherence pass as this proof.

- [ ] **Step 3: Add the pre-DDL clean guard and schema enums**

The first statement in `upgrade()` calls `_refuse_legacy_physical_owner_state(bind)`. It executes bounded `EXISTS` queries only; it does not read MinIO. Create native enums with these exact labels:

```python
RetentionAuthorityKind = POLICY | INSTALLATION_MINIMUM
RetentionRevisionState = PROPOSED | ACTIVE | SUPERSEDED
RetentionOperationState = PENDING | RUNNING | FAILED | VERIFIED | CANCELLED_PRE_START
RetentionTargetState = PENDING | RUNNING | FAILED | VERIFIED
HoldReleaseState = PENDING_AUTHORIZATION | AUTHORIZED | RUNNING | FAILED | VERIFIED |
                   CANCELLED_PRE_START
R27ActionKind = REQUEST | APPROVE | CANCEL
R27RequestState = WAITING_FOR_SECOND_APPROVER | WAITING_FOR_RECOVERY_GENERATION |
                  READY_FOR_FINALIZATION | FINALIZING | EXECUTED | CANCELLED | STALE | FAILED
R27ExecutionState = CLAIMED | SOURCE_COMMITTED | PURGING | EXECUTED | FAILED
R27ResultCode = PHYSICAL_ERASED | LOGICAL_ONLY_SURVIVING_OWNER
MaintenanceState = PENDING | RUNNING | FAILED | VERIFIED
```

- [ ] **Step 4: Extend Blob, DocumentVersion, Record, policy, and exact purge identity**

Add to `blob`:

```text
object_version_id                 TEXT NULL
worm_enforced_mode                TEXT NULL
worm_asserted_retain_until        TIMESTAMPTZ NULL
worm_asserted_at                  TIMESTAMPTZ NULL
worm_retain_until                 existing column = verified retain-until
worm_retention_verified_at        TIMESTAMPTZ NULL
worm_legal_hold                   BOOLEAN NULL
worm_legal_hold_verified_at       TIMESTAMPTZ NULL
purged_at                         TIMESTAMPTZ NULL
purge_execution_id                UUID NULL
```

Create partial unique index `uq_blob_worm_physical_identity` on `(bucket, object_key, object_version_id) WHERE object_version_id IS NOT NULL`. For `worm_locked=true`, require nonblank VersionId, `worm_enforced_mode='GOVERNANCE'`, non-null assertion/verification timestamps, verified retain-until not earlier than asserted retain-until, and non-null hold state/verification. For `worm_locked=false`, forbid WORM mode/assertion/verification/hold fields while allowing null VersionId for non-versioned non-WORM buckets.

Add to `document_version`: `retention_authority_kind`, nullable `retention_policy_id`, nullable `document_worm_config_id`, and non-null `retention_basis_date`; a CHECK requires exactly the matching authority FK. Add `record.retention_basis_provisional BOOLEAN NOT NULL DEFAULT false`. Add `retention_policy.active_revision_no INTEGER NOT NULL DEFAULT 1`.

Add `pending_blob_purge.object_version_id TEXT`, `r27_execution_id UUID`, explicit state/attempt/error/timestamps, and require a nonblank VersionId for every authority-bound new marker. The clean guard permits making it non-null after the legacy-null discriminator is removed.

- [ ] **Step 5: Create document minimum and forward-only retention tables**

`document_worm_config` is one row per org: `id`, `org_id UNIQUE`, `active_period TEXT`, `active_revision_no`, timestamps. It is seeded by setup from required `DOCUMENT_WORM_MINIMUM`; no migration literal is a site default.

`retention_revision` stores `authority_kind`, exactly one authority FK, `revision_no`, immutable `active_values JSONB`, immutable `proposed_values JSONB`, state, actor/audit IDs, timestamps. A partial unique index allows one `PROPOSED` revision per authority.

`retention_operation` references the revision and records state, bounded attempt/error, lease UUID/timestamps, progress counts, and completion. `retention_operation_target` binds operation, Blob SHA, bucket/key/VersionId, required retain-until/hold, state/attempt/error/read-back; unique `(operation_id, blob_sha256, object_version_id)`.

`worm_hold_release_operation` binds org, Record, Blob SHA, VersionId, initiating actor, idempotency key, normalized release basis, canonical operation bytes/digest, owner-snapshot digest, state/attempt/error/result and timestamps. The app role may create only `PENDING_AUTHORIZATION`; it cannot supply an authorization identity/time or advance state. The row is authority to request recomputation only, never authority to set physical `OFF` or remove a permanent hold.

`worm_hold_release_authorization` is one-to-one with the operation and stores the canonical operation digest, host operator identity, authorizing audit event, authorization time, and the dedicated authorizer role that wrote it. A trigger permits insert only while the operation is unchanged, still logically released, not permanent, and `PENDING_AUTHORIZATION`, then atomically advances it to `AUTHORIZED`. The app, retention, hold-maintenance, and R27 roles have no INSERT/UPDATE/DELETE grant on this table; authorization history cannot be changed or deleted.

- [ ] **Step 6: Replace the empty legacy R27 table with closed authority state**

Because the guard proved the legacy table empty, rename `worm_destroy_request` to `r27_request`, remove its nullable-timestamp state encoding, and add the exact request columns. Preserve the durable request UUID/FK identity while replacing the behavioral contract.

Create normalized immutable tables:

- `r27_manifest`: request FK unique, schema version, 43-character nonce, canonical bytes, SHA-256, excluded-set SHA-256, expected state, issue/expiry;
- `r27_manifest_target`: manifest/order, Blob SHA, bucket/key/VersionId, unique physical target;
- `r27_manifest_derivative`: manifest/order, closed kind, domain ID, optional Blob SHA;
- `r27_action_challenge`: action, request/record, `(iss,jti)` unique, action nonce unique, accepted claim JSON, manifest digest, expiry/consumption;
- `r27_authorizer_key`: key ID, Ed25519 public key/fingerprint, active/retired/revoked timestamps; referenced keys cannot delete;
- `r27_attestation`: challenge/request/action unique, canonical bytes/digest, key/signature, mapped app user, exact `iss/sub/sid/jti/aud/azp/acr/auth_time/amr`, permission result, issue/expiry;
- `r27_execution`: request unique, execution UUID unique, state/result, claim/attempt/error/source-commit/purge/completion timestamps;
- `recovery_generation_verifier_key`: exact approved public-key lifecycle fields and immutable audit link;
- `recovery_generation_witness`: key/witness nonce, request/manifest/generation/exclusion binding, canonical bytes, signature, verified time, consumed execution; unique `(key_id,witness_nonce)` and `(manifest_sha256,generation_id)`.

Use `BYTEA` for canonical/signature/key bytes, `CHAR(64)` for lowercase SHA-256/fingerprints, bounded `VARCHAR` for codes/IDs, `JSONB` only for closed snapshot arrays/claim values, and `TIMESTAMPTZ` everywhere time is security-relevant.

- [ ] **Step 7: Add database-backed audit/backup schedule and intent state**

`audit_maintenance_schedule` has one row per closed job kind (`CHAIN_LINK`, `VERIFY_CHAIN`, `CHECKPOINT_ANCHOR`, `ROLL_PARTITIONS`), interval, next due time, lease, last start/success, bounded error. `backup_maintenance_operation` has `BACKUP` or `RESTORE_TEST`, org/policy/requester, scheduled/manual source, state/lease/attempt/result/timestamps. Neither table accepts a command string, Python module, bucket, path, or shell payload.

- [ ] **Step 8: Implement refusal-safe downgrade and register migration-managed indexes**

`downgrade()` first checks every new state source and any `blob.worm_locked=true`; on data it raises `populated_0089_downgrade_refused`. On empty state it drops new tables/enums/columns, restores the empty legacy `worm_destroy_request` shape/index, and leaves no new roles or functions. Add all raw partial indexes to `_MIGRATION_MANAGED_INDEXES` so `alembic check` does not propose false drops.

- [ ] **Step 9: Run the migration and model gates**

```bash
(cd apps/api && uv run pytest tests/migration/test_0089_worm_retention_container_identity.py -q)
(cd apps/api && uv run pytest tests/migration/test_migration_coherence.py -q)
(cd apps/api && uv run alembic check)
(cd apps/api && uv run alembic heads)
```

Expected GREEN: all four isolated paths pass; coherence passes; no autogenerate operations; sole head `0089_worm_retention_container_identity`.

- [ ] **Step 10: Verify and commit Task 1**

```bash
(cd apps/api && uv run ruff check src/easysynq_api/db/models tests/migration/test_0089_worm_retention_container_identity.py)
(cd apps/api && uv run ruff format --check src/easysynq_api/db/models tests/migration/test_0089_worm_retention_container_identity.py)
git diff --check
git add migrations/versions/0089_worm_retention_container_identity.py migrations/env.py apps/api/src/easysynq_api/db/models apps/api/tests/migration/test_0089_worm_retention_container_identity.py
git commit -m "feat(worm): establish exact retention authority schema"
```

---

## Task 2: Enforce runtime database authority and append-only owner pointers

**Files:**
- Modify: `migrations/versions/0089_worm_retention_container_identity.py`
- Create: `apps/api/tests/integration/test_worm_database_authority.py`
- Create: `apps/api/tests/integration/test_r27_database_authority.py`
- Modify: `apps/api/tests/integration/conftest.py`
- Modify: `apps/api/src/easysynq_api/config.py`

**Interfaces:**
- Consumes: Task 1 tables and existing `easysynq_app`/`easysynq_linker` roles.
- Produces: login roles `easysynq_retention`, `easysynq_hold_authorizer`, `easysynq_hold_maintenance`, `easysynq_r27_authorizer`, `easysynq_r27_maintenance`, `easysynq_recovery_key_manager`, `easysynq_audit_signer`, and `easysynq_backup`; state-dependent triggers and narrow functions/grants.

- [ ] **Step 1: Write real-role RED tests using independent connections**

The tests connect as each actual role and prove:

- app can insert a complete asserted Blob with its owner transaction, but cannot mutate/delete its digest/org/bucket/key/VersionId/WORM mode/initial assertion or delete/repoint Document source/EvidenceBlob pointers;
- retention can only advance verified retain-until and `OFF -> ON`, never shorten or set `ON -> OFF`;
- app-forged hold operations, authorization rows, authorization fields, and state changes are denied; the hold-authorizer can bind only the exact pending digest and cannot set a physical result; hold maintenance can claim only an authorized non-permanent release and cannot authorize itself, delete, bypass, or change coordinates;
- R27 maintenance can release hold or record purge only through a function bound to its claimed execution and cannot change coordinates/digest/mode;
- app cannot insert/update/delete manifest, attestation, accepted witness/key, R27 final event, or bypass marker;
- authorizer cannot mutate execution/witness/key or Blob protection and cannot assume the R27 role;
- R27 maintenance cannot create human attestations or assume authorizer;
- key manager can install/retire/revoke public verifier keys only, cannot delete them or write witnesses;
- migration/audit/backup secrets are not valid credentials for another role.

Run:

```bash
(cd apps/api && uv run pytest tests/integration/test_worm_database_authority.py tests/integration/test_r27_database_authority.py -q)
```

Expected RED: Task 1 has not yet installed the roles/grants/triggers.

- [ ] **Step 2: Create NOINHERIT login roles from migrate-only secret fields**

Add migrate-only settings `RETENTION_DB_PASSWORD`, `HOLD_AUTHORIZER_DB_PASSWORD`, `HOLD_MAINTENANCE_DB_PASSWORD`, `R27_AUTHORIZER_DB_PASSWORD`, `R27_MAINTENANCE_DB_PASSWORD`, `RECOVERY_KEY_MANAGER_DB_PASSWORD`, `AUDIT_SIGNER_DB_PASSWORD`, and `BACKUP_DB_PASSWORD`. In `0089`, create/alter each as `LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION`. Quote passwords through SQLAlchemy bound parameters/driver literal handling; never interpolate raw values into DDL or logs.

Revoke `PUBLIC` schema/table/function rights and explicitly revoke the broad default privileges inherited from the 0010-era owner defaults before granting anything. Each new role gets `USAGE` on `public` and only the named tables/sequences/functions.

- [ ] **Step 3: Install immutable Blob and owner-pointer triggers**

Create `easysynq_reject_worm_blob_identity_mutation()` and `easysynq_reject_worm_owner_pointer_mutation()`. Once a Blob is asserted or referenced by a WORM owner, the former rejects changes to SHA/org/bucket/key/VersionId/WORM flag/mode/initial assertion and rejects delete. The latter rejects updates/deletes of `document_version.source_blob_sha256` and `evidence_blob.blob_sha256`; lifecycle/disposition changes owner liveness without erasing history.

Add defense-in-depth trigger checks even when a role has a column grant. Do not key authorization on a caller-provided boolean.

- [ ] **Step 4: Expose only role-bound transition functions**

Use `SECURITY DEFINER`, fixed `search_path=public,pg_temp`, owner-only body, explicit role grants, and revoked `PUBLIC EXECUTE`:

```sql
easysynq_ratchet_worm_assertion(blob_sha, version_id, retain_until, hold_on,
                                verified_at, operation_id)
easysynq_authorize_hold_release(operation_id, operation_digest, operator_identity,
                                audit_event_id, authorized_at)
easysynq_record_ordinary_hold_release(blob_sha, version_id, operation_id,
                                      verified_at)
easysynq_record_r27_hold_release(blob_sha, version_id, execution_id, verified_at)
easysynq_record_r27_purge(blob_sha, version_id, execution_id, purged_at)
easysynq_enqueue_ordinary_exact_purge(record_id, disposition_event_id, blob_sha)
```

Each function resolves immutable coordinates itself, row-locks the binding operation/execution, validates its current state/organization, and makes only the monotone or exact result update. The hold authorization function is executable only by `easysynq_hold_authorizer`, verifies canonical bytes/digest and the unchanged logical release, and cannot write physical result state. The ordinary hold result function is executable only by `easysynq_hold_maintenance`, requires the matching immutable authorization and `AUTHORIZED`/`RUNNING` operation, rechecks that the owner is non-permanent, and cannot write a bypass/delete marker. The ordinary enqueue function always writes `bypass_governance=false`; only the R27 source transaction can create a bypass marker tied to its execution.

- [ ] **Step 5: Grant the exact handoff**

- `easysynq_app`: normal application DML outside protected tables; SELECT on R27 status views; INSERT complete Blob/owner rows; no post-assertion protected update/delete; execute ordinary intent function only.
- `easysynq_retention`: SELECT owner registry/policy/config/operation views; claim/update retention targets; execute ratchet; no application mutation, hold OFF, bypass, or Redis need.
- `easysynq_hold_authorizer`: read one pending ordinary hold-release summary plus current actor/Record/hold facts; execute only `easysynq_authorize_hold_release`; append its audit; no general application mutation, MinIO, Redis, or result transition.
- `easysynq_hold_maintenance`: read authorized hold-release/owner/Blob facts; claim an authorized row; execute only `easysynq_record_ordinary_hold_release`; no app/authorizer mutation, retention shortening, delete, bypass, R27, Redis, or audit-signing key.
- `easysynq_r27_authorizer`: read Record/permission/owner snapshot inputs; write challenges/manifests/attestations/request pre-finalization only; read no secret table and no execution mutation.
- `easysynq_r27_maintenance`: read authority/witness/key/owner inputs; claim/update execution and exact markers; execute R27 result functions; no authorizer writes.
- `easysynq_recovery_key_manager`: lifecycle columns on public verifier keys plus append-only audit insert.
- `easysynq_audit_signer`: linker/checkpoint/schedule-specific reads/writes plus append-only audit, no general application DML.
- `easysynq_backup`: backup policy/operation/result and bounded export/restore reads, no R27/audit-signing authority.

Add a migration test that queries `information_schema.role_table_grants`, `role_column_grants`, function ACLs, and role membership so future broad defaults cannot silently reopen the boundary.

- [ ] **Step 6: Run role and migration gates**

```bash
(cd apps/api && uv run pytest tests/integration/test_worm_database_authority.py tests/integration/test_r27_database_authority.py -q)
(cd apps/api && uv run pytest tests/migration/test_0089_worm_retention_container_identity.py tests/migration/test_migration_coherence.py -q)
(cd apps/api && uv run alembic check)
```

Expected GREEN: all positive grants work and every cross-role mutation is denied by PostgreSQL, not merely by a mocked repository.

- [ ] **Step 7: Verify and commit Task 2**

```bash
(cd apps/api && uv run ruff check tests/integration/test_worm_database_authority.py tests/integration/test_r27_database_authority.py)
git diff --check
git add migrations/versions/0089_worm_retention_container_identity.py apps/api/src/easysynq_api/config.py apps/api/tests/integration/conftest.py apps/api/tests/integration/test_worm_database_authority.py apps/api/tests/integration/test_r27_database_authority.py
git commit -m "feat(identity): enforce WORM and R27 database roles"
```

---

## Task 3: Add exact-version WORM storage primitives and eliminate all-version purge

**Files:**
- Create: `apps/api/src/easysynq_api/services/vault/worm.py`
- Create: `apps/api/tests/unit/test_worm_storage.py`
- Modify: `apps/api/src/easysynq_api/services/vault/storage.py`
- Modify: `apps/api/src/easysynq_api/services/vault/staged_identity.py`
- Modify: `apps/api/src/easysynq_api/services/vault/__init__.py`
- Modify: `apps/api/tests/unit/test_storage_promotion.py`
- Modify: `apps/api/tests/integration/test_upload_identity_storage.py`
- Create: `scripts/require-no-match.sh`
- Create: `scripts/tests/test-require-no-match.sh`

**Interfaces:**
- Consumes: `PromotionResult.target_version_id` from S-upload-identity and per-process ordinary or R27 MinIO credentials.
- Produces: validated locator/state/assertion types; exact retention/hold/hash/presign/delete; no all-version WORM deletion function; one tested repository-root no-match assertion helper.

- [ ] **Step 1: Add a fail-safe no-match assertion helper with its own RED/GREEN proof**

`scripts/require-no-match.sh PATTERN PATH...` invokes `rg -n -- PATTERN PATH...` and handles its status explicitly: status 1 (no match) is success, status 0 (forbidden match printed) becomes exit 1, and status 2 or any other error is propagated as failure. It requires at least one path and never uses `! rg`, which would misclassify an invalid regex, unreadable path, or I/O error as success.

First create `scripts/tests/test-require-no-match.sh` and show RED because the helper is absent. Then implement the helper and prove: an absent string exits 0; a present string exits 1 and shows the matching safe fixture line; an invalid regex exits 2; and a missing/unreadable path is nonzero. The fixture is created under `mktemp -d`, the exact directory is trap-cleaned, and no repository file is changed.

```bash
bash scripts/tests/test-require-no-match.sh
```

Expected RED: the required helper does not exist. Expected GREEN: all four exit-status cases pass.

- [ ] **Step 2: Write the exact-version and same-key-newer-version REDs**

Unit fakes fail if any WORM request omits `VersionId`, changes it, treats bucket default as per-version proof, accepts COMPLIANCE, accepts missing legal-hold read-back, shortens a date, or passes bypass through the ordinary function.

The real-MinIO integration test creates locked version V1, then newer same-key V2. It calls the future exact R27 delete for V1 and asserts V1 is absent while V2 remains readable. A mutation that restores the current list-all-versions loop must turn this test RED.

```bash
(cd apps/api && uv run pytest tests/unit/test_worm_storage.py tests/integration/test_upload_identity_storage.py -k 'worm or same_key_newer' -q)
```

Expected RED: exact WORM contracts do not exist and current `purge_object` enumerates every version.

- [ ] **Step 3: Implement immutable WORM types and validation**

Implement the public contracts from the interface map. `WormObjectLocator` rejects blank bucket/key, VersionId outside 1–1024, and literal `"null"`. `WormObjectState` requires timezone-aware UTC-comparable timestamps, exact GOVERNANCE, and a retain-until after read time for a newly committed owner. Typed failures are:

```python
class WormStorageError(Exception): ...
class WormVersionMissing(WormStorageError): ...
class WormModeMismatch(WormStorageError): ...
class WormReadbackMismatch(WormStorageError): ...
class WormProtectionWouldWeaken(WormStorageError): ...
class WormIdentityMismatch(WormStorageError): ...
class WormCapabilityDenied(WormStorageError): ...
```

Provider text stays chained as the private cause and never enters public/log payloads.

- [ ] **Step 4: Read and ratchet retention/hold for one VersionId**

`read_worm_state` calls `get_object_retention` and `get_object_legal_hold` with the exact bucket/key/VersionId. Require response mode `GOVERNANCE`, a valid timezone-aware `RetainUntilDate`, and `LegalHold.Status in {"ON","OFF"}`. Missing fields are mismatch, not a default.

`apply_worm_protection` first reads current state, computes only `max(current, requirement)`, calls `put_object_retention` only for a later date, calls `put_object_legal_hold(Status="ON")` only when required and currently off, then reads both back and requires exact-or-stronger state for the same locator. It has no `OFF` or bypass parameter.

- [ ] **Step 5: Add exact stream/hash/read/presign operations**

`stream_hash_exact` issues `get_object(..., VersionId=...)`, requires returned VersionId equality, hashes 1 MiB chunks, counts bytes, and closes the body on success, error, or cancellation. `presign_worm_get` includes `VersionId` in `Params` and never re-resolves latest. Add an exact streaming response helper so API downloads do not buffer entire objects.

Keep existing key-only functions only after renaming them with `_non_worm` in their public name, for example `presign_non_worm_get(bucket, key)`. No WORM caller may import them after Task 5.

- [ ] **Step 6: Replace list-all-versions deletion with an R27-only exact primitive**

Delete `_purge_object_sync`/`purge_object` and every `list_object_versions` deletion loop. Implement:

```python
async def delete_worm_version(
    locator: WormObjectLocator,
    *,
    release_hold: bool,
    bypass_governance: bool,
) -> None:
    ...
```

The function always supplies VersionId. When requested, it sets legal hold `OFF` for that VersionId, re-reads OFF, then sends one `delete_object(..., VersionId=..., BypassGovernanceRetention=True)`. It is placed in an internal R27 storage submodule or guarded export imported only by `services.r27.executor`; ordinary code has no wrapper accepting those flags. Absence of the exact version is idempotent success only after a version-specific HEAD/GET confirms `NoSuchVersion`; `NoSuchBucket`, AccessDenied, transport, and provider 5xx remain failures.

- [ ] **Step 7: Persist promotion's exact target identity in a typed assertion input**

Keep `PromotionResult.target_version_id` mandatory and add:

```python
def worm_locator_from_promotion(result: PromotionResult) -> WormObjectLocator:
    return WormObjectLocator(
        bucket=result.target_bucket,
        object_key=result.target_key,
        object_version_id=result.target_version_id,
    )
```

A missing/blank target VersionId is `WormIdentityMismatch`; there is no key-latest fallback.

- [ ] **Step 8: Run unit and real-MinIO GREEN gates**

```bash
(cd apps/api && uv run pytest tests/unit/test_worm_storage.py tests/unit/test_storage_promotion.py -q)
(cd apps/api && uv run pytest tests/integration/test_upload_identity_storage.py -q)
bash scripts/require-no-match.sh 'list_object_versions|purge_object' apps/api/src/easysynq_api
```

Expected GREEN: tests pass and the helper proves no WORM purge implementation remains (only an explicitly classified non-WORM cleanup may remain under a different allow-listed symbol).

- [ ] **Step 9: Verify and commit Task 3**

```bash
(cd apps/api && uv run ruff check src/easysynq_api/services/vault tests/unit/test_worm_storage.py tests/unit/test_storage_promotion.py tests/integration/test_upload_identity_storage.py)
(cd apps/api && uv run mypy src)
bash scripts/tests/test-require-no-match.sh
git diff --check
git add apps/api/src/easysynq_api/services/vault apps/api/tests/unit/test_worm_storage.py apps/api/tests/unit/test_storage_promotion.py apps/api/tests/integration/test_upload_identity_storage.py scripts/require-no-match.sh scripts/tests/test-require-no-match.sh
git commit -m "feat(worm): address every protected object by exact version"
```

---

## Task 4: Implement the exhaustive WORM owner registry and protection-before-visibility boundary

**Files:**
- Create: `apps/api/src/easysynq_api/services/vault/retention.py`
- Create: `apps/api/tests/unit/test_worm_retention.py`
- Create: `apps/api/tests/integration/test_worm_owner_registry.py`
- Modify: `apps/api/src/easysynq_api/domain/records/retention.py`
- Modify: `apps/api/src/easysynq_api/services/records/repository.py`
- Modify: `apps/api/src/easysynq_api/services/packs/locks.py`
- Modify: `apps/api/tests/unit/test_records_purge_locks.py`

**Interfaces:**
- Consumes: Task 3 exact storage types, Document/Record/policy/config schema, existing organization pack advisory lock.
- Produces: one tested owner query, max-retention/hold calculation, sorted physical locks, `protect_proposed_owner` and reconciliation.

- [ ] **Step 1: Write closed-registry and max-owner REDs**

Build fixtures for:

- Document source under P3Y;
- same Blob added as Record evidence under P10Y;
- permanent sealed pack evidence;
- rendition, structured PDF, portfolio, mirror rendition, and visual-diff cache pointers;
- disposed DESTROY/R27 Record versus ARCHIVE_COLD/TRANSFER Record;
- active and released domain holds.

Assert the first three approved legs are included according to their liveness rule, excluded derivative legs never appear, and P3Y + proposed P10Y produces one exact P10Y requirement. Add a mutation test that moves protection into the `new Blob only` branch and must fail the second-owner case.

```bash
(cd apps/api && uv run pytest tests/unit/test_worm_retention.py tests/integration/test_worm_owner_registry.py -q)
```

Expected RED: there is no exhaustive registry or proposed-owner max calculation.

- [ ] **Step 2: Correct unresolved event retention semantics**

Change record capture resolution so every `event:*` basis stores capture date and `retention_basis_provisional=true`; `captured_at` stores the same date with provisional false. Keep `retention_until` pure. Add tests that a later real event can extend, an earlier event records the real logical basis but cannot reduce current physical protection, and a missing event never means unprotected.

- [ ] **Step 3: Define owner and requirement types**

Alongside the public types, define:

```python
@dataclasses.dataclass(frozen=True, slots=True)
class WormOwner:
    kind: WormOwnerKind
    owner_id: uuid.UUID
    org_id: uuid.UUID
    blob_sha256: str
    basis_date: datetime.date
    duration: str
    domain_hold: bool
    permanent: bool

def owner_requirement(owner: WormOwner) -> WormRequirement: ...
def aggregate_requirements(
    current: WormObjectState, owners: Sequence[WormOwner]
) -> WormRequirement: ...
```

Finite policy uses `worm_lock_period` when non-null, else `duration`; `PERMANENT` maps to `legal_hold=True`. The finite retain-until converts the date result to the repository's documented UTC end-of-day convention and never invents a finite substitute for permanent.

- [ ] **Step 4: Implement one exhaustive SQL owner registry**

`list_live_worm_owners` uses three explicitly named query legs and no generic liveness helper:

1. every immutable `DocumentVersion.source_blob_sha256`, with its pinned policy or installation config and check-in basis;
2. `EvidenceBlob -> Record` unless a destructive DESTROY/R27 `DispositionEvent` exists; ARCHIVE_COLD and TRANSFER survive;
3. defensive live sealed-pack pointer while the permanent Record evidence leg also exists.

Cross-org rows, incomplete WORM assertions, dangling policy/config references, and impossible permanent-without-hold states fail closed. A code comment lists the excluded non-WORM pointers verbatim.

- [ ] **Step 5: Establish one physical lock order**

Use the existing organization pack lock in shared mode when owner creation can intersect pack sealing and exclusive mode only for R27. After it, acquire transaction advisory locks sorted by the actual PostgreSQL lock-key tuple derived from `(bucket,key,VersionId)`, not lexical caller order. Update pack build/portfolio paths to use the same shared lock API. Tests launch inverted concurrent requests and prove no deadlock or manifest crossing.

- [ ] **Step 6: Implement protection before owner commit**

`protect_proposed_owner`:

1. validates promotion SHA/org/domain and exact locator;
2. acquires pack then physical lock before adopt/copy use is finalized;
3. resolves live owners plus proposed owner;
4. reads current exact state;
5. applies only the aggregate later date/hold ON;
6. reads back exact state;
7. returns `VerifiedWormAssertion` for the caller to write with Blob/owner in one transaction.

It does not commit. Storage failure/mismatch raises before owner insert or success audit. DB failure after protection leaves a safely over-retained exact version and emits a bounded reconciliation signal on retry; it never rolls storage backward.

- [ ] **Step 7: Add reconciliation without latest-version inference**

`reconcile_exact_version` accepts a persisted complete locator only, reacquires locks, recomputes owners, and only extends. An incomplete assertion becomes a visible `WormIdentityMismatch`; it never queries versions to guess.

- [ ] **Step 8: Run registry/lock GREEN gates**

```bash
(cd apps/api && uv run pytest tests/unit/test_worm_retention.py tests/unit/test_records_purge_locks.py -q)
(cd apps/api && uv run pytest tests/integration/test_worm_owner_registry.py -q)
```

Expected GREEN: exact owner membership, max obligation, hold mapping, and lock ordering pass.

- [ ] **Step 9: Verify and commit Task 4**

```bash
(cd apps/api && uv run ruff check src/easysynq_api/services/vault/retention.py src/easysynq_api/domain/records/retention.py tests/unit/test_worm_retention.py tests/integration/test_worm_owner_registry.py)
(cd apps/api && uv run mypy src)
git diff --check
git add apps/api/src/easysynq_api/services/vault/retention.py apps/api/src/easysynq_api/domain/records/retention.py apps/api/src/easysynq_api/services/records/repository.py apps/api/src/easysynq_api/services/packs/locks.py apps/api/tests/unit/test_worm_retention.py apps/api/tests/unit/test_records_purge_locks.py apps/api/tests/integration/test_worm_owner_registry.py
git commit -m "feat(worm): calculate protection from every live owner"
```

---

## Task 5: Route every WORM producer and consumer through exact verified identity

**Files:**
- Modify: `apps/api/src/easysynq_api/services/vault/service.py`
- Modify: `apps/api/src/easysynq_api/services/records/service.py`
- Modify: `apps/api/src/easysynq_api/services/ingestion/commit.py`
- Modify: `apps/api/src/easysynq_api/services/packs/build.py`
- Modify: `apps/api/src/easysynq_api/api/documents.py`
- Modify: `apps/api/src/easysynq_api/api/records.py`
- Modify: `apps/api/src/easysynq_api/services/vault/mirror.py`
- Modify: `apps/api/src/easysynq_api/services/vault/blob_verify.py`
- Modify: `apps/api/src/easysynq_api/tasks/blob_verify.py`
- Modify: `apps/api/src/easysynq_api/services/backup/{archive,drill,restore,service}.py`
- Modify: `apps/api/src/easysynq_api/services/diff/visual.py`
- Modify: `apps/api/tests/integration/test_records.py`
- Modify: `apps/api/tests/integration/test_packs.py`
- Modify: `apps/api/tests/unit/test_ingestion_commit.py`
- Modify: `apps/api/tests/unit/test_sealed_pack_retention.py`
- Create: `apps/api/tests/integration/test_worm_producer_identity.py`
- Create: `apps/api/tests/integration/test_worm_consumer_identity.py`

**Interfaces:**
- Consumes: Task 4 protection boundary and existing staged promotion.
- Produces: persisted VersionId/assertion on all WORM writes; exact locator on all WORM reads; explicit non-WORM classification for every excluded pointer.

- [ ] **Step 1: Inventory every Blob pointer as an executable RED guard**

Add a test/AST guard with an allow-listed map from every model Blob pointer and production storage call to `WORM_OWNER`, `WORM_CONSUMER`, or `NON_WORM_DERIVATIVE`. It fails on an unclassified new pointer, a WORM path calling a key-only helper, or a derivative entering owner aggregation.

```bash
(cd apps/api && uv run pytest tests/integration/test_worm_producer_identity.py tests/integration/test_worm_consumer_identity.py -q)
```

Expected RED: current writers discard target VersionId and readers are key-only.

- [ ] **Step 2: Protect controlled-document source check-in**

At check-in, resolve and persist the document type's default `RetentionPolicy`; when absent, resolve and persist the org's `DocumentWormConfig`. Pin check-in UTC date. For new promotion and correct-domain dedup alike, construct `ProposedWormOwner`, call `protect_proposed_owner`, persist `object_version_id` and complete assertion, then insert/activate `DocumentVersion` in the same transaction. No Document version/success audit is visible on storage failure.

- [ ] **Step 3: Protect Record evidence and ingestion commit**

Replace `_attach_evidence` and ingestion Blob insert branches so both a newly copied Blob and a deduplicated existing Blob invoke protection with the Record's pinned policy, provisional/real basis, and hold. Persist promotion target VersionId only when creating the Blob; on dedup validate its existing locator/assertion and ratchet it. Do not delete immutable EvidenceBlob links on later disposition.

- [ ] **Step 4: Protect permanent generated pack artifacts**

When a sealed pack ZIP becomes permanent Record evidence, use the same proposed-owner path and exact promotion locator. Portfolio and visual-diff artifacts remain non-WORM and must not acquire assertion fields. The build retains the organization shared lock through the protected owner commit.

- [ ] **Step 5: Make WORM downloads, mirror source, integrity verification, and backup inventory exact**

Add `asserted_worm_locator(blob)` that requires the complete DB assertion. Document/Record source downloads, source mirror reads, Blob integrity verification, and backup/recovery manifests pass it to exact stream/presign/hash. Migrate `tasks/blob_verify.py` to call the exact-locator `services/vault/blob_verify.py` boundary; it remains an ordinary read/hash job and receives no maintenance credential. Rendition/portfolio/visual-diff reads call the named non-WORM helper. A response whose returned version differs is an integrity failure.

- [ ] **Step 6: Prove protection-before-visibility and dedup ratchet with real stores**

Tests inject failure after copy but before read-back, and after protection but before DB commit. Assert respectively: no owner/success audit; and only an unowned over-retained version plus safe retry. Create Document P3Y then byte-identical Record P10Y and assert one Blob/VersionId, exact storage P10Y, both owners. Create permanent owner and require legal hold ON before commit.

- [ ] **Step 7: Run affected GREEN gates**

```bash
(cd apps/api && uv run pytest tests/integration/test_worm_producer_identity.py tests/integration/test_worm_consumer_identity.py -q)
(cd apps/api && uv run pytest tests/integration/test_upload_identity_storage.py tests/integration/test_records_disposition.py -q)
(cd apps/api && uv run pytest tests/unit/test_storage_promotion.py tests/unit/test_storage_hash_object.py tests/unit/test_storage_presign.py -q)
```

Expected GREEN: all WORM producers/consumers are exact; non-WORM derivatives still work.

- [ ] **Step 8: Verify and commit Task 5**

```bash
(cd apps/api && uv run ruff check src tests/integration/test_worm_producer_identity.py tests/integration/test_worm_consumer_identity.py)
(cd apps/api && uv run mypy src)
git diff --check
git add apps/api/src apps/api/tests
git commit -m "feat(worm): protect owners before exact-version visibility"
```

Before staging, inspect `git status --short` and exclude unrelated test files; the broad `git add` is permitted only after that explicit path review.

---

## Task 6: Stage policy/config extensions and run retention/hold/disposition maintenance outside Redis

**Files:**
- Create: `apps/api/src/easysynq_api/maintenance/runtime.py`
- Create: `apps/api/src/easysynq_api/maintenance/retention.py`
- Create: `apps/api/src/easysynq_api/maintenance/hold.py`
- Create: `apps/api/src/easysynq_api/cli/retention_maintenance.py`
- Create: `apps/api/src/easysynq_api/cli/hold_maintenance.py`
- Create: `apps/api/src/easysynq_api/cli/hold_release_authorizer.py`
- Create: `apps/api/tests/unit/test_retention_maintenance.py`
- Create: `apps/api/tests/unit/test_hold_release_authority.py`
- Create: `apps/api/tests/integration/test_retention_extension.py`
- Create: `apps/api/tests/integration/test_worm_hold_release.py`
- Modify: `apps/api/src/easysynq_api/services/records/retention_policies.py`
- Modify: `apps/api/src/easysynq_api/services/records/disposition.py`
- Modify: `apps/api/src/easysynq_api/services/records/repository.py`
- Modify: `apps/api/src/easysynq_api/api/retention_policies.py`
- Modify: `apps/api/src/easysynq_api/api/records.py`
- Modify: `apps/api/src/easysynq_api/tasks/app.py`
- Modify: `apps/api/src/easysynq_api/tasks/records.py`

**Interfaces:**
- Consumes: retention revision/target rows and Task 4 reconciliation.
- Produces: typed pending policy/config response, bounded retention claimant, host-authorized exact hold-release claimant, event extension, and ordinary exact purge; no privileged Redis tasks and no shared OFF/bypass principal.

- [ ] **Step 1: Write partial-failure/resume and hold-release REDs**

For a P3Y -> P10Y policy change over three exact targets, fail storage after target one. Assert active policy remains P3Y, proposed P10Y is visible, new captures use P10Y, target one remains over-locked VERIFIED, other targets remain FAILED/PENDING, and restart resumes without shortening or duplicating audit. After all verify, assert one transaction activates P10Y and marks the proposal/operation VERIFIED.

For domain hold release, assert logical release creates only a `PENDING_AUTHORIZATION` typed operation and physical hold remains. With real DB roles, prove the app cannot insert or forge `worm_hold_release_authorization`, set an operator identity/time, change the canonical digest, or advance the operation. The host one-shot refuses a changed/stale/permanent/cross-org operation, displays the exact bounded summary for confirmation, and is the only role that may bind the digest and advance it to `AUTHORIZED`. Hold maintenance refuses an unsigned/app-forged/stale row, rechecks every current owner, blocks permanent or surviving holds, and can set exact-version `OFF` only with the no-bypass principal. Ordinary and retention MinIO credentials receive AccessDenied for direct OFF.

```bash
(cd apps/api && uv run pytest tests/unit/test_retention_maintenance.py tests/unit/test_hold_release_authority.py tests/integration/test_retention_extension.py tests/integration/test_worm_hold_release.py -q)
```

Expected RED: policy PATCH mutates active values immediately; hold release and retention sweep run through current app/Celery paths.

- [ ] **Step 2: Return a typed pending result for strict lengthening**

Keep `retention.read` for reads and require SYSTEM-scope `retention.manage` for policy/config change. Validate that duration, WORM period, preservation action, and review flag do not weaken. A physical lengthening transaction writes immutable audit, `retention_revision(PROPOSED)`, `retention_operation(PENDING)`, and an exact target manifest together; it leaves active columns unchanged.

The response is:

```python
class RetentionChangeAccepted(BaseModel):
    operation_id: UUID
    authority_kind: Literal["POLICY", "INSTALLATION_MINIMUM"]
    active_values: RetentionValues
    proposed_values: RetentionValues
    state: Literal["PENDING"]
    target_count: int
```

New owner resolution uses `max(active, proposed)` while a proposal is open. A second change to the same authority returns 409 `retention_extension_in_progress`. Cancellation is allowed only before any target leaves PENDING; there is no cancel after physical work.

- [ ] **Step 3: Implement deterministic DB claims and bounded backoff**

`run_retention_maintenance_once(limit=100)` claims ordered targets using `FOR UPDATE SKIP LOCKED`, a random lease UUID, and a 60-second lease. It reacquires pack/physical locks, recomputes current owners, treats stored required values as a floor, calls exact reconciliation, and records verified state. Transient failures use bounded exponential backoff capped at 15 minutes; terminal identity/mode mismatch is visible FAILED with a bounded code. It never receives Redis settings.

- [ ] **Step 4: Activate only after every current target verifies**

Under authority row lock, rescan for owners/targets added during work. Add missing targets and remain RUNNING. Only when every current exact target verifies does one transaction copy proposed values to `RetentionPolicy` or `DocumentWormConfig`, increment active revision, mark old revision superseded/current revision active, and complete operation. A crash at any boundary resumes idempotently.

- [ ] **Step 5: Move event-anchor extension and ordinary hold release into separate typed work**

When a real event is recorded, store the true logical basis/provisional false and compare the resulting floor. A later floor creates/extends exact targets before the transition reports synchronized; an earlier date leaves physical state untouched. Hold ON remains synchronous before logical hold placement.

An authorized user with `record.hold.manage` may logically release a non-permanent domain hold through the main API. In that transaction the server resolves exact coordinates and current owners, canonicalizes a closed `HOLD_RELEASE_V1` payload (schema version, operation/org/Record/Blob/VersionId, initiating AppUser, normalized basis, owner-snapshot digest, issue time, idempotency key), stores its SHA-256, appends the request audit, and creates `PENDING_AUTHORIZATION`. Client-supplied coordinates, actor, digest, state, or operator fields are ignored/rejected. The API response says the logical release is pending physical host authorization; it never claims storage `OFF`.

The explicit host command is:

```text
./scripts/easysynq hold-release authorize --operation-id UUID
```

It starts the one-shot `hold-release-authorizer` with only its narrow DB credential. The process row-locks the operation, rebuilds `HOLD_RELEASE_V1` from authoritative rows, verifies unchanged digest/idempotency/org/Record/current logical release, proves the original actor was an active AppUser with `record.hold.manage` at request time from the immutable request audit, refuses any permanent owner, and prints only organization UUID, Record UUID/title, actor username, normalized basis, target digest, and current owner/hold summary. After an interactive exact `authorize UUID` confirmation, its DB-only function records the host OS operator identity supplied by the trusted wrapper, appends authorization audit, and advances to `AUTHORIZED`. `--yes` is permitted only with both `--operation-id` and `--expected-digest`; there is no bulk/wildcard mode.

`hold-maintenance` claims only `AUTHORIZED` rows under `FOR UPDATE SKIP LOCKED`, reacquires the organization/physical locks, rebuilds and compares the exact digest, and independently recomputes all live owners. If any owner requires hold or is permanent, it records bounded FAILED/blocked state without changing MinIO. Otherwise it sets legal hold `OFF` for exactly the bound VersionId, reads back `OFF`, and records the result through the hold-only DB function. Its MinIO policy has no delete, retention-shortening, or Governance bypass action. Retention and R27 processes cannot claim these rows; R27 never services normal releases. A request changed after authorization is cancelled/stale and requires a new operation and host confirmation.

- [ ] **Step 6: Move ordinary retention disposition and exact purge to maintenance**

Remove `records-retention-sweep` and `records-reap-pending-blob-purges` from Beat and the general worker registry. Retention maintenance evaluates due records, writes logical disposition through its bounded role/function, and processes non-bypass exact purge intents after rechecking no owners and expired retention. It never calls R27 delete/bypass and never deletes all versions or Blob history.

- [ ] **Step 7: Add service health/state without claiming partial success**

Expose safe DB status (last claim/success, oldest pending, pending/running/failed/verified counts) through the main read-only admin status surface. The loop exits non-zero on startup role/policy mismatch; liveness alone is not readiness.

- [ ] **Step 8: Run GREEN and absence gates**

```bash
(cd apps/api && uv run pytest tests/unit/test_retention_maintenance.py tests/unit/test_hold_release_authority.py tests/integration/test_retention_extension.py tests/integration/test_worm_hold_release.py -q)
(cd apps/api && uv run pytest tests/integration/test_records_disposition.py -q)
bash scripts/require-no-match.sh 'records\.retention_sweep|records\.reap_pending_blob_purges' apps/api/src/easysynq_api/tasks
```

Expected GREEN: resumable extension/disposition and independently authorized hold-release proofs pass; app-role forgery and every non-hold OFF credential fail; the helper proves no privileged Celery schedule/registration remains.

- [ ] **Step 9: Verify and commit Task 6**

```bash
(cd apps/api && uv run ruff check src/easysynq_api/maintenance src/easysynq_api/cli/retention_maintenance.py src/easysynq_api/cli/hold_maintenance.py src/easysynq_api/cli/hold_release_authorizer.py src/easysynq_api/services/records tests/unit/test_retention_maintenance.py tests/unit/test_hold_release_authority.py tests/integration/test_retention_extension.py tests/integration/test_worm_hold_release.py)
(cd apps/api && uv run mypy src)
git diff --check
git add apps/api/src/easysynq_api/maintenance apps/api/src/easysynq_api/cli/retention_maintenance.py apps/api/src/easysynq_api/cli/hold_maintenance.py apps/api/src/easysynq_api/cli/hold_release_authorizer.py apps/api/src/easysynq_api/services/records apps/api/src/easysynq_api/api/retention_policies.py apps/api/src/easysynq_api/api/records.py apps/api/src/easysynq_api/tasks apps/api/tests/unit/test_retention_maintenance.py apps/api/tests/unit/test_hold_release_authority.py apps/api/tests/integration/test_retention_extension.py apps/api/tests/integration/test_worm_hold_release.py
git commit -m "feat(retention): stage and resume forward-only protection"
```

---

## Task 7: Implement canonical R27 manifests, Ed25519 attestations, and strict dedicated token validation

**Files:**
- Create: `apps/api/src/easysynq_api/services/r27/__init__.py`
- Create: `apps/api/src/easysynq_api/services/r27/types.py`
- Create: `apps/api/src/easysynq_api/services/r27/canonical.py`
- Create: `apps/api/src/easysynq_api/services/r27/crypto.py`
- Create: `apps/api/src/easysynq_api/auth/r27_tokens.py`
- Create: `apps/api/tests/unit/test_r27_canonical.py`
- Create: `apps/api/tests/unit/test_r27_crypto.py`
- Create: `apps/api/tests/unit/test_r27_tokens.py`
- Modify: `apps/api/src/easysynq_api/config.py`
- Modify: `apps/api/pyproject.toml`
- Modify: `apps/api/uv.lock`

**Interfaces:**
- Consumes: RFC 8785 dependency, realm JWKS, approved exact claim contract, installer-generated authorizer key.
- Produces: versioned canonical payloads/signatures and a dedicated validated claim object; no bearer persistence or normal-auth fallback.

- [ ] **Step 1: Write canonical substitution and token rejection REDs**

Tests require byte-identical canonical output for semantically identical input ordering and a different digest/signature for any changed organization, Record, legal basis, target coordinate/digest, derivative, excluded-set digest, nonce, expected state, time, OIDC claim, permission result, or action.

Token cases reject: algorithm other than RS256, missing/unknown `kid`, wrong signature/issuer/audience/azp, normal API audience, missing `sub/sid/jti/r27-approver`, wrong ACR, `auth_time` older than 120 seconds or more than 30 seconds in the future, `iat/nbf` too far in the future, expired token, and `exp-iat > 120` seconds. `amr` alone never substitutes for ACR.

```bash
(cd apps/api && uv run pytest tests/unit/test_r27_canonical.py tests/unit/test_r27_crypto.py tests/unit/test_r27_tokens.py -q)
```

Expected RED: modules and strict validator are absent; ordinary token validation is materially weaker.

- [ ] **Step 2: Define closed payload models**

Use strict Pydantic/dataclass models with `extra="forbid"`. The manifest schema is exactly `easysynq.r27-manifest.v1` and includes:

```json
{
  "schema": "easysynq.r27-manifest.v1",
  "request_id": "uuid",
  "manifest_nonce": "base64url-32-bytes",
  "org_id": "uuid",
  "record_id": "uuid",
  "legal_basis": "normalized text",
  "legal_basis_sha256": "hex",
  "targets": [{"blob_sha256":"hex","bucket":"text","object_key":"text","object_version_id":"text"}],
  "derivatives": [{"kind":"PACK_ZIP|PACK_PORTFOLIO|PACK_RECORD|PACK_SHARE","id":"uuid","blob_sha256":"hex|null"}],
  "excluded_set_sha256": "hex",
  "expected_state": "WAITING_FOR_SECOND_APPROVER",
  "issued_at": "RFC3339 UTC",
  "expires_at": "RFC3339 UTC"
}
```

Sort targets by `(bucket,key,VersionId,sha)` and derivatives by `(kind,id,sha-or-empty)` before canonicalization. Reject duplicates and empty target sets. Hash legal-basis normalized UTF-8 and excluded-set canonical bytes with SHA-256.

- [ ] **Step 3: Canonicalize only with RFC 8785**

Call `rfc8785.dumps()` over JSON-compatible primitives. Do not hand-roll `sort_keys`, whitespace, float handling, or timestamp serialization. Timestamps are whole-second UTC `Z`; UUIDs are lowercase canonical strings. Persist the canonical bytes and digest, not a later reconstruction.

The attestation schema `easysynq.r27-attestation.v1` binds action, action nonce, manifest digest, authorizer key ID, complete accepted claim subset (`iss/sub/sid/jti/aud/azp/acr/auth_time/amr`), mapped app-user ID, permission result/scope digest, issued/expiry, and expected transition.

- [ ] **Step 4: Load/sign/verify Ed25519 without fallback generation**

Add `cryptography` as a direct production dependency in `pyproject.toml` because this module imports it directly; regenerate `uv.lock` and assert no unrelated package/version drift. `rfc8785` is already direct and remains pinned by the committed lock.

`load_authorizer_signing_key(path)` requires an existing regular non-symlink PEM file, Ed25519 private key, and mode/readability contract. It derives key ID as an installer-supplied stable identifier and SHA-256 fingerprint from DER SubjectPublicKeyInfo. Missing/invalid key is startup failure; runtime never generates one.

`sign_attestation` returns the key ID, canonical bytes/digest, and 64-byte signature. `verify_attestation` looks up the pinned public key ID, rejects retired-for-new/revoked keys according to action time, verifies canonical digest equality, then Ed25519 signature.

- [ ] **Step 5: Implement a separate R27 validator**

`validate_r27_token(token, now) -> ValidatedR27Claims` uses its own cached JWKS client/config. It does not call or branch inside normal `authenticate()`. Require exact issuer; `aud` array contains and `azp` equals `easysynq-r27-authorizer`; required realm role; exact ACR; time bounds above; nonempty opaque `sub/sid/jti`; and no JIT app-user creation. Normalize `aud` to a sorted tuple and record optional `amr` as emitted.

Validation does **not** consume jti; prepare binds it to one challenge, and commit atomically consumes the row. This preserves the approved view-before-consume rule.

- [ ] **Step 6: Run GREEN and dependency gates**

```bash
(cd apps/api && uv run pytest tests/unit/test_r27_canonical.py tests/unit/test_r27_crypto.py tests/unit/test_r27_tokens.py -q)
(cd apps/api && uv run ruff check src/easysynq_api/services/r27 src/easysynq_api/auth/r27_tokens.py tests/unit/test_r27_canonical.py tests/unit/test_r27_crypto.py tests/unit/test_r27_tokens.py)
(cd apps/api && uv run mypy src)
```

Expected GREEN: every single-field mutation invalidates authority and every non-qualifying token is denied.

- [ ] **Step 7: Verify and commit Task 7**

```bash
git diff --check
git add apps/api/src/easysynq_api/services/r27 apps/api/src/easysynq_api/auth/r27_tokens.py apps/api/src/easysynq_api/config.py apps/api/tests/unit/test_r27_canonical.py apps/api/tests/unit/test_r27_crypto.py apps/api/tests/unit/test_r27_tokens.py apps/api/pyproject.toml apps/api/uv.lock
git commit -m "feat(r27): define signed canonical authority"
```

---

## Task 8: Build the isolated R27 authorizer prepare/commit state machine

**Files:**
- Create: `apps/api/src/easysynq_api/services/r27/repository.py`
- Create: `apps/api/src/easysynq_api/services/r27/authorizer.py`
- Create: `apps/api/src/easysynq_api/cli/r27_authorizer_key.py`
- Create: `apps/api/src/easysynq_api/api/r27_authority.py`
- Create: `apps/api/src/easysynq_api/r27_authorizer_main.py`
- Create: `apps/api/tests/unit/test_r27_authorizer_app.py`
- Create: `apps/api/tests/integration/test_r27_authorizer.py`
- Modify: `apps/api/src/easysynq_api/services/authz/pep.py`
- Modify: `apps/api/src/easysynq_api/services/authz/repository.py`
- Modify: `apps/api/src/easysynq_api/services/packs/locks.py`
- Modify: `apps/api/tests/integration/test_contract_response_schemas.py`

**Interfaces:**
- Consumes: dedicated validated token, authorizer DB role, canonical signer, exact owner/derivative snapshot, shared `record.dispose` evaluator.
- Produces: prepared challenge and immutable signed request/approval/cancellation transitions; no MinIO call or main-API route.

- [ ] **Step 1: Write two-person, replay, and client-coordinate REDs**

Integration tests cover:

- un-enrolled/API-provisioned user denied despite ordinary permissions;
- enrolled user without mapped AppUser or current `record.dispose` denied (no JIT);
- request prepare displays canonical manifest without consuming token;
- commit consumes the same `(iss,jti)`/action nonce exactly once;
- second actor must have distinct Keycloak `sub` and AppUser;
- approval uses a new fresh token/challenge and signs the same immutable manifest;
- request/approval/cancel with normal bearer, stale/password-only token, duplicate actor, altered manifest, changed permission, expired challenge/manifest, or replay leaves state unchanged;
- any client body containing bucket/key/VersionId/digest/owner/derivative is a 422 extra-field refusal;
- cancel cannot cross a FINALIZING claim.

```bash
(cd apps/api && uv run pytest tests/unit/test_r27_authorizer_app.py tests/integration/test_r27_authorizer.py -q)
```

Expected RED: current ordinary API directly writes mutable requests/approvals and has no signing/replay boundary.

- [ ] **Step 2: Create an authorizer-only FastAPI app**

`r27_authorizer_main.py` exposes only `/healthz`, `/readyz`, `/r27-authority/v1/auth/config`, `/actions/prepare`, `/actions/{challenge_id}/commit`, and `/requests/{id}`. `GET /healthz` is unauthenticated, dependency-free, and returns HTTP 200 with exactly `{"status":"ok","service":"r27-authorizer"}` once the event loop is serving. `GET /readyz` returns 200 only after the authorizer DB role and signing key/public-key registry match; otherwise it returns a bounded 503 problem. It does not include the main API router, setup routes, docs mutation routes, Redis/Celery code, or ordinary auth dependency. Unit tests assert the exact health response and readiness mismatch.

- [ ] **Step 3: Map identity and re-evaluate application permission independently**

Resolve `ValidatedR27Claims.sub` to one existing active `AppUser` in the same org; never create one. Reuse the production multi-axis authorization evaluator with an explicit SYSTEM/Record scope and `record.dispose`; do not trust web `usePermissions`, stored caller role text, or an API-provided allow result. Persist the exact permission/scope digest in the attestation.

- [ ] **Step 4: Prepare request under the exclusive pack/R27 lock**

For `REQUEST`, normalize legal basis, allocate request UUID/manifest nonce/action nonce server-side, take the organization exclusive pack lock, row-lock the Record, resolve exact asserted WORM targets and derivative inventory from DB, and persist canonical manifest plus one challenge. No MinIO credential is available. Incomplete assertion or targetless/stale Record fails closed.

For `APPROVE` and `CANCEL`, row-lock the existing request/manifest and return that same manifest plus a distinct action nonce. Repeated prepare with the same `(iss,jti)` returns the same unconsumed challenge only when every input/claim matches; otherwise deny replay/conflict.

- [ ] **Step 5: Commit one signed transition atomically**

Under challenge/request locks, revalidate token signature/time/claims, challenge expiry, current permission, manifest expiry/state/inventory digest, and same token binding. Then:

- `REQUEST`: write requester attestation and set `WAITING_FOR_SECOND_APPROVER`;
- `APPROVE`: require distinct subject/AppUser, write approver attestation, set `WAITING_FOR_RECOVERY_GENERATION`;
- `CANCEL`: write cancellation attestation and set `CANCELLED` only from waiting/ready.

Mark challenge consumed in the same commit. A unique `(iss,jti)`, action nonce, and request/action constraint makes a racing second commit lose without partial authority.

- [ ] **Step 6: Register/rotate authorizer public keys safely**

At installer/bootstrap time, `python -m easysynq_api.cli.r27_authorizer_key install --public-key-file /run/secrets/r27_authorizer_public_key --key-id ID` runs as a bounded one-shot with the authorizer DB role and public key only; it inserts the public key/fingerprint matching the separately mounted private key. Authorizer startup may select only that active key. Rotation installs a new key before switching the private mount; old referenced public keys remain verifiable. Retired keys cannot sign new actions; revoked keys make unexecuted requests FAILED/STALE on reconciliation. The authorizer cannot delete key history.

- [ ] **Step 7: Validate both app factories against the combined contract seam**

Parameterize response-schema discovery so operations tagged/runtime-owned by `main-api` are mounted on `main:app` and `r27-authorizer` operations on `r27_authorizer_main:app`. Do not add authorizer operations to a deferred/unchecked allowlist.

- [ ] **Step 8: Run GREEN gates**

```bash
(cd apps/api && uv run pytest tests/unit/test_r27_authorizer_app.py tests/integration/test_r27_authorizer.py -q)
(cd apps/api && uv run pytest tests/integration/test_contract_response_schemas.py -m contract --tb=short)
```

Expected GREEN: two distinct signatures over one manifest, replay denial, callback app isolation, and response schemas pass.

- [ ] **Step 9: Verify and commit Task 8**

```bash
(cd apps/api && uv run ruff check src/easysynq_api/services/r27 src/easysynq_api/api/r27_authority.py src/easysynq_api/r27_authorizer_main.py tests/unit/test_r27_authorizer_app.py tests/integration/test_r27_authorizer.py)
(cd apps/api && uv run mypy src)
git diff --check
git add apps/api/src/easysynq_api/services/r27 apps/api/src/easysynq_api/cli/r27_authorizer_key.py apps/api/src/easysynq_api/api/r27_authority.py apps/api/src/easysynq_api/r27_authorizer_main.py apps/api/src/easysynq_api/auth apps/api/src/easysynq_api/services/packs/locks.py apps/api/tests/unit/test_r27_authorizer_app.py apps/api/tests/integration/test_r27_authorizer.py apps/api/tests/integration/test_contract_response_schemas.py
git commit -m "feat(r27): isolate two-person authorizer flow"
```

---

## Task 9: Add host-rooted recovery verifier lifecycle and the fail-closed witness interface

**Files:**
- Create: `apps/api/src/easysynq_api/services/r27/recovery.py`
- Create: `apps/api/src/easysynq_api/cli/recovery_verifier.py`
- Create: `apps/api/tests/unit/test_recovery_verifier_cli.py`
- Create: `apps/api/tests/integration/test_r27_recovery_gate.py`
- Modify: `scripts/easysynq`
- Modify: `apps/api/src/easysynq_api/problems.py`

**Interfaces:**
- Consumes: public Ed25519 key file and one-shot key-manager DB role; approved witness schema.
- Produces: audited install/retire/revoke and verified witness binding; no production private key or witness ingress.

- [ ] **Step 1: Write empty-registry and lifecycle REDs**

Tests require production-empty registry; app/authorizer/R27 roles cannot install a key; install rejects private/invalid/non-Ed25519 keys, duplicate IDs/fingerprints, invalid validity, or absent exact fingerprint confirmation; unknown/not-yet-active/retired-for-new/revoked keys reject witnesses; retirement preserves already bound witness; revocation invalidates it before execution and returns request to waiting; duplicate nonce/generation binding and pre-second-approval verification time are denied.

```bash
(cd apps/api && uv run pytest tests/unit/test_recovery_verifier_cli.py tests/integration/test_r27_recovery_gate.py -q)
```

Expected RED: registry/CLI/verifier behavior is absent.

- [ ] **Step 2: Implement the host-only CLI contract**

Exact wrapper syntax:

```text
./scripts/easysynq recovery-verifier install --key-id ID --public-key-file PATH --not-before RFC3339
./scripts/easysynq recovery-verifier retire --key-id ID
./scripts/easysynq recovery-verifier revoke --key-id ID
```

`install` reads a regular no-follow file, accepts only Ed25519 public PEM, prints key ID/algorithm/validity/SHA-256 fingerprint, and requires the operator to type the full fingerprint unless `--confirm-fingerprint` is supplied by an audited noninteractive harness. Never accept key bytes or private key in argv/env. The wrapper later runs an operator-profile one-shot container with only the public file and key-manager DSN secret.

- [ ] **Step 3: Verify canonical recovery witnesses**

Schema `easysynq.recovery-generation-witness.v1` binds key ID, unique 32-byte nonce, request ID, manifest digest, generation ID, exact excluded-set digest, literal result `VERIFIED`, generation identity, verification-completed time, issued time, and signature. `verify_and_bind_witness` checks canonical bytes/signature/key active at issue, time after second approval, exact digest/set, uniqueness, request open/unexpired, and stores one witness. There is no API route or production command for this function in this slice.

- [ ] **Step 4: Keep test signing impossible outside isolated tests**

The integration fixture generates an ephemeral Ed25519 pair in the pytest process, inserts only the public key through its owner fixture, signs in memory, and never writes/mounts the private key. Any `EASYSYNQ_R27_TEST_SIGNER` or equivalent startup switch is forbidden; add a source/config guard that no runtime setting/image/Compose path accepts a recovery private key.

- [ ] **Step 5: Reconcile retire/revoke semantics**

Retirement blocks witnesses issued after `retired_at` but keeps an already bound pending witness valid. Revocation invalidates every unexecuted witness immediately; under request lock clear its consumption/readiness binding and set `WAITING_FOR_RECOVERY_GENERATION` with immutable audit. Executed history stays immutable.

- [ ] **Step 6: Run GREEN and CLI guards**

```bash
(cd apps/api && uv run pytest tests/unit/test_recovery_verifier_cli.py tests/integration/test_r27_recovery_gate.py -q)
bash -n scripts/easysynq
bash scripts/require-no-match.sh 'RECOVERY.*PRIVATE|TEST_SIGNER' apps/api/src infra/compose .env.example
```

Expected GREEN: lifecycle/revocation pass; the helper proves no production private-key intake exists (test names may appear only under `apps/api/tests`).

- [ ] **Step 7: Verify and commit Task 9**

```bash
(cd apps/api && uv run ruff check src/easysynq_api/services/r27/recovery.py src/easysynq_api/cli/recovery_verifier.py tests/unit/test_recovery_verifier_cli.py tests/integration/test_r27_recovery_gate.py)
git diff --check
git add apps/api/src/easysynq_api/services/r27/recovery.py apps/api/src/easysynq_api/cli/recovery_verifier.py apps/api/src/easysynq_api/problems.py apps/api/tests/unit/test_recovery_verifier_cli.py apps/api/tests/integration/test_r27_recovery_gate.py scripts/easysynq
git commit -m "feat(r27): root recovery trust in public verifier keys"
```

---

## Task 10: Implement the isolated exact-version R27 executor and derivative transaction

**Files:**
- Create: `apps/api/src/easysynq_api/services/r27/executor.py`
- Create: `apps/api/src/easysynq_api/maintenance/r27.py`
- Create: `apps/api/src/easysynq_api/cli/r27_maintenance.py`
- Create: `apps/api/tests/unit/test_r27_executor.py`
- Create: `apps/api/tests/integration/test_r27_execution.py`
- Create: `apps/api/tests/integration/test_r27_pack_race.py`
- Modify: `apps/api/src/easysynq_api/services/records/disposition.py`
- Modify: `apps/api/src/easysynq_api/services/records/repository.py`
- Modify: `apps/api/src/easysynq_api/services/packs/{build,portfolio,locks}.py`
- Modify: `apps/api/src/easysynq_api/services/vault/storage.py`
- Modify: `apps/api/tests/integration/test_records_disposition.py`

**Interfaces:**
- Consumes: two authorizer attestations, active recovery witness/key, exact owner registry/storage, exclusive pack lock, R27 DB/MinIO roles.
- Produces: one resumable execution, immutable logical disposition/derivative invalidation, exact markers and exact purge result; no ingress/Redis.

- [ ] **Step 1: Write recovery-gate, claim-race, tamper, owner, and newer-version REDs**

Tests prove:

- two approvals alone remain `WAITING_FOR_RECOVERY_GENERATION` with no logical/physical change;
- forged DB boolean/row or app-written marker cannot reach READY;
- two executors race and exactly one claims a ready request/execution UUID;
- altered signature/claim/key/witness/manifest/target/derivative/state/expiry fails before source commit;
- owner/derivative inventory change yields STALE and requires new authorization;
- exact pre-hash mismatch or missing signed VersionId fails before disposition, hold OFF, bypass, or marker;
- shared surviving owner yields executed logical disposition with `LOGICAL_ONLY_SURVIVING_OWNER`, no hold/delete;
- source commit is atomic across disposition, packs UNAVAILABLE, share revoke, ZIP/portfolio pointer clear, derivative tombstones, and exact markers;
- crash after source commit resumes the same execution and exact markers;
- V1 purge leaves newer same-key V2 alive.

```bash
(cd apps/api && uv run pytest tests/unit/test_r27_executor.py tests/integration/test_r27_execution.py tests/integration/test_r27_pack_race.py -q)
```

Expected RED: current API approval immediately writes disposition and calls an all-version purge through the app worker boundary.

- [ ] **Step 2: Claim only one READY request**

`run_r27_maintenance_once(limit=20)` selects deterministic ready/retry rows with `FOR UPDATE SKIP LOCKED`. It revalidates unexpired/open/non-cancelled state and unconsumed valid witness, allocates one execution UUID, transitions READY -> FINALIZING, and persists `R27Execution(CLAIMED)` atomically. A retry can resume only that UUID; cancellation cannot cross the claim.

- [ ] **Step 3: Revalidate the complete signed and recovery chain**

Before every source or external step, verify both canonical attestation bytes/signatures/key lifecycle, distinct subjects/AppUsers, exact claims and action times, permission result, manifest expiry, recovery witness signature/key current status, generation/exclusion/timestamp uniqueness, and execution binding. Mutable actor IDs or bypass flags are never authority.

- [ ] **Step 4: Serialize pack and physical state, then pre-hash exact bytes**

Acquire organization exclusive pack lock, recompute WORM owners and all derivatives, compare canonical inventory digest, then acquire sorted physical locks. For each target stream/hash exact VersionId and compare with manifest target digest and immutable Blob SHA. An owner-DB corruption fixture that changes object bytes or coordinates must stop here.

- [ ] **Step 5: Commit logical disposition and derivative invalidation once**

In one transaction:

- append immutable R27 `DispositionEvent` bound to request/execution;
- change affected packs to `UNAVAILABLE`;
- revoke every live share;
- clear ZIP/portfolio pointers;
- append one-hop derivative tombstones bound to the source event;
- preserve immutable Document/EvidenceBlob/Blob history;
- create exact-version purge markers for targets with no surviving owner;
- mark surviving-owner results explicitly;
- transition execution to `SOURCE_COMMITTED`.

Do not call MinIO before this commit. Every pack seal path must hold the shared side of the same lock so a build cannot cross the signed inventory.

- [ ] **Step 6: Revalidate and purge each exact marker**

For each marker, reacquire physical lock and recheck execution, attestations, witness/key status, immutable coordinates, owners, and exact hash. Then set hold OFF, verify OFF, delete exactly its VersionId with explicit Governance bypass, verify `NoSuchVersion` for only that ID, and call the DB-bound purge-result function. A newer version is never queried/deleted. Blob assertion/history and marker/result remain.

- [ ] **Step 7: Prove R27 cannot consume ordinary hold-release authority**

`r27-maintenance` has no SELECT/claim/transition grant on ordinary `worm_hold_release_operation` rows, does not trust `worm_hold_release_authorization`, and has no code path that accepts `HOLD_RELEASE_V1`. Its MinIO credential remains the R27 exact-read/hold/bypass/delete credential, but its database functions require an already-claimed, fully signed R27 execution plus witness. Conversely, `hold-maintenance` cannot read R27 attestations/witnesses, execute R27 result functions, delete, or bypass. Real-role tests attempt both cross-claims and require database denial before any storage call.

- [ ] **Step 8: Make failures visible and retries bounded**

Inventory mismatch is STALE. Transient provider/dependency errors are FAILED with retry schedule and same execution. Terminal signature/identity/tamper failures are FAILED and alert. No error becomes EXECUTED by retry count. Metrics/logs carry request/execution UUID and bounded class only, never coordinates/legal basis/token/provider body.

- [ ] **Step 9: Run GREEN real DB/MinIO and race gates**

```bash
(cd apps/api && uv run pytest tests/unit/test_r27_executor.py -q)
(cd apps/api && uv run pytest tests/integration/test_r27_execution.py tests/integration/test_r27_pack_race.py tests/integration/test_records_disposition.py -q)
```

Expected GREEN: witness gate, exact hash/delete, one claimant, derivative atomicity, restart, surviving-owner, and V2 survival all pass.

- [ ] **Step 10: Verify and commit Task 10**

```bash
(cd apps/api && uv run ruff check src/easysynq_api/services/r27/executor.py src/easysynq_api/maintenance/r27.py src/easysynq_api/cli/r27_maintenance.py src/easysynq_api/services/records src/easysynq_api/services/packs tests/unit/test_r27_executor.py tests/integration/test_r27_execution.py tests/integration/test_r27_pack_race.py)
(cd apps/api && uv run mypy src)
git diff --check
git add apps/api/src/easysynq_api/services/r27/executor.py apps/api/src/easysynq_api/maintenance/r27.py apps/api/src/easysynq_api/cli/r27_maintenance.py apps/api/src/easysynq_api/services/records apps/api/src/easysynq_api/services/packs apps/api/src/easysynq_api/services/vault/storage.py apps/api/tests/unit/test_r27_executor.py apps/api/tests/integration/test_r27_execution.py apps/api/tests/integration/test_r27_pack_race.py apps/api/tests/integration/test_records_disposition.py
git commit -m "feat(r27): finalize signed exact-version destruction"
```

---

## Task 11: Remove legacy API/Celery R27 authority and expose read-only status

**Files:**
- Modify: `apps/api/src/easysynq_api/api/records.py`
- Modify: `apps/api/src/easysynq_api/services/records/disposition.py`
- Modify: `apps/api/src/easysynq_api/services/records/repository.py`
- Modify: `apps/api/src/easysynq_api/tasks/records.py`
- Modify: `apps/api/src/easysynq_api/tasks/app.py`
- Modify: `apps/api/src/easysynq_api/tasks/__init__.py`
- Modify: `apps/api/tests/integration/test_records_disposition.py`
- Create: `apps/api/tests/unit/test_r27_boundary_absence.py`

**Interfaces:**
- Consumes: Task 8 authorizer and Task 10 executor.
- Produces: main API read-only status/history; no ordinary request/approve/cancel/execute/bypass route or privileged Redis task.

- [ ] **Step 1: Write an absence RED that inspects routes, task registry, imports, and settings**

Assert the main FastAPI app has only `GET /api/v1/records/{record_id}/r27`; it rejects authorizer tokens; it exposes no POST/PATCH R27 action. Assert Celery has no R27/retention purge/audit signing/backup task, API code does not import executor/delete-worm-version, and Redis messages cannot name a privileged operation.

```bash
(cd apps/api && uv run pytest tests/unit/test_r27_boundary_absence.py -q)
```

Expected RED: legacy direct request/approve/cancel and reaper tasks remain.

- [ ] **Step 2: Delete legacy mutation services and routes**

Remove `request_worm_destroy`, `approve_worm_destroy`, legacy cancellation, `_purge_marked`, `reap_pending_blob_purges`, their ordinary API routes, and their task registrations. Do not keep a compatibility alias that writes unsigned authority. Historical schema/model names were already migrated in Task 1.

- [ ] **Step 3: Add one normal-bearer read-only R27 status view**

`GET /api/v1/records/{record_id}/r27` requires `record.read` and returns safe request state, actor display identities, manifest digest (not raw object coordinates), recovery wait/failure/result state, operation timestamps, and capability hints from the server. It never returns signatures, raw claim payloads, keys, provider detail, or legal-basis text to an unauthorized reader.

- [ ] **Step 4: Reject token confusion both ways**

Main API validation rejects a token whose audience/azp is the authorizer client even if issuer/sub are valid. Authorizer rejects the ordinary API token. Add integration tests that capture outbound web/API fixtures later and server-side 401/403 now.

- [ ] **Step 5: Run GREEN and source-absence gates**

```bash
(cd apps/api && uv run pytest tests/unit/test_r27_boundary_absence.py tests/integration/test_records_disposition.py tests/integration/test_r27_authorizer.py -q)
bash scripts/require-no-match.sh 'approve_worm_destroy|request_worm_destroy|reap_pending_blob_purges|purge_object' apps/api/src/easysynq_api
```

Expected GREEN: tests pass and the helper proves no legacy privileged symbol remains.

- [ ] **Step 6: Verify and commit Task 11**

```bash
(cd apps/api && uv run ruff check src/easysynq_api/api/records.py src/easysynq_api/services/records src/easysynq_api/tasks tests/unit/test_r27_boundary_absence.py)
git diff --check
git add apps/api/src/easysynq_api/api/records.py apps/api/src/easysynq_api/services/records apps/api/src/easysynq_api/tasks apps/api/tests/unit/test_r27_boundary_absence.py apps/api/tests/integration/test_records_disposition.py apps/api/tests/integration/test_r27_authorizer.py
git commit -m "refactor(r27): remove ordinary API execution authority"
```

---

## Task 12: Move audit signing and backup/restore out of Beat and the general worker

**Files:**
- Create: `apps/api/src/easysynq_api/maintenance/audit.py`
- Create: `apps/api/src/easysynq_api/maintenance/backup.py`
- Create: `apps/api/src/easysynq_api/cli/audit_maintenance.py`
- Create: `apps/api/src/easysynq_api/cli/backup_maintenance.py`
- Create: `apps/api/src/easysynq_api/tasks/beat_app.py`
- Create: `apps/api/tests/unit/test_privileged_maintenance_registration.py`
- Create: `apps/api/tests/integration/test_audit_maintenance.py`
- Create: `apps/api/tests/integration/test_backup_maintenance.py`
- Modify: `apps/api/src/easysynq_api/tasks/app.py`
- Modify: `apps/api/src/easysynq_api/tasks/audit.py`
- Modify: `apps/api/src/easysynq_api/tasks/backup.py`
- Modify: `apps/api/src/easysynq_api/api/setup.py`
- Modify: `apps/api/src/easysynq_api/services/audit/{checkpoint,linker}.py`
- Modify: `apps/api/src/easysynq_api/services/backup/{archive,drill,restore,service}.py`
- Modify: `scripts/easysynq`

**Interfaces:**
- Consumes: Task 1 audit schedule/backup operation rows and purpose-specific DB roles.
- Produces: DB-claimed audit/backup loops and typed manual intents; Beat/general worker hold no audit private key, backup key, DB owner, or Keycloak export credential.

- [ ] **Step 1: Write privileged-task absence and durable-intent REDs**

Assert the Celery registry/Beat schedule contains no audit link/sign/verify/partition, backup, restore-test, retention, purge, or R27 task. Killing/restarting audit and backup loops resumes due rows/intents without duplicate success. Setup's restore-test request writes a typed `BACKUP/RESTORE_TEST` operation rather than a task name/argument payload. A forged unknown kind or command/module/shell string is rejected by schema.

```bash
(cd apps/api && uv run pytest tests/unit/test_privileged_maintenance_registration.py tests/integration/test_audit_maintenance.py tests/integration/test_backup_maintenance.py -q)
```

Expected RED: Beat currently schedules audit and backup, the general worker registers them, and setup enqueues restore work through Redis.

- [ ] **Step 2: Implement a DB-backed audit loop**

`run_audit_maintenance_once(now, limit)` claims due `AuditMaintenanceSchedule` rows by closed kind. It runs existing linker, verify, checkpoint, or partition logic through the audit-signer DB role, records next due/start/success/error, and releases its lease. Only checkpoint signing loads the private key; API/CLI verification accepts the public key. Missing signing/public key is startup/readiness failure—delete all generate-on-missing fallback behavior.

- [ ] **Step 3: Implement typed backup operations and schedule ownership**

`backup-maintenance` evaluates `BackupPolicy.cron` and creates/claims closed `BACKUP` or `RESTORE_TEST` operations. Manual setup/operator requests insert the same typed row with org/policy/requester only. The service maps the enum to an internal allow-listed function; no row can carry executable text, destination override, module, argv, SQL, bucket, or shell. Record PASS/FAIL honestly and preserve current backup/restore-test semantics without claiming later backup-content work.

- [ ] **Step 4: Remove privileged Celery imports and credentials**

Delete task registration/schedule entries and change `tasks/audit.py`/`tasks/backup.py` into either non-Celery reusable adapters under maintenance or remove them. General worker continues ordinary jobs only. Beat imports a minimal `beat_app` settings surface with Redis/schedule for ordinary task names and no DB/S3/key settings.

- [ ] **Step 5: Make operator commands create or run bounded intents**

`./scripts/easysynq backup run` and `restore-test` invoke `backup-maintenance` intent/status commands, not `worker` and not arbitrary Celery `call`. Preserve existing confirmation/destructive restore rules. The audit command may run a one-shot verifier with public material only; signing stays in the signer service.

- [ ] **Step 6: Run GREEN and source guards**

```bash
(cd apps/api && uv run pytest tests/unit/test_privileged_maintenance_registration.py tests/integration/test_audit_maintenance.py tests/integration/test_backup_maintenance.py -q)
bash scripts/require-no-match.sh 'easysynq\.(audit|backup|records\.retention|records\.reap)' apps/api/src/easysynq_api/tasks
bash -n scripts/easysynq
```

Expected GREEN: no privileged task is broker-addressable and DB restarts resume bounded work.

- [ ] **Step 7: Verify and commit Task 12**

```bash
(cd apps/api && uv run ruff check src/easysynq_api/maintenance src/easysynq_api/cli src/easysynq_api/tasks tests/unit/test_privileged_maintenance_registration.py tests/integration/test_audit_maintenance.py tests/integration/test_backup_maintenance.py)
(cd apps/api && uv run mypy src)
git diff --check
git add apps/api/src/easysynq_api/maintenance apps/api/src/easysynq_api/cli apps/api/src/easysynq_api/tasks apps/api/src/easysynq_api/api/setup.py apps/api/src/easysynq_api/services/audit apps/api/src/easysynq_api/services/backup apps/api/tests/unit/test_privileged_maintenance_registration.py apps/api/tests/integration/test_audit_maintenance.py apps/api/tests/integration/test_backup_maintenance.py scripts/easysynq
git commit -m "refactor(identity): isolate audit and backup maintenance"
```

---

## Task 13: Add fail-closed file-secret settings and per-process capability configuration

**Files:**
- Create: `apps/api/src/easysynq_api/config_file_secrets.py`
- Create: `apps/api/src/easysynq_api/process_settings.py`
- Create: `apps/api/tests/unit/test_file_secret_settings.py`
- Create: `apps/api/tests/unit/test_process_capability_settings.py`
- Modify: `apps/api/src/easysynq_api/config.py`
- Modify: `apps/api/src/easysynq_api/db/session.py`
- Modify: `migrations/env.py`
- Modify: `apps/api/src/easysynq_api/main.py`
- Modify: `apps/api/src/easysynq_api/r27_authorizer_main.py`
- Modify: `apps/api/src/easysynq_api/tasks/app.py`
- Modify: `apps/api/src/easysynq_api/tasks/beat_app.py`
- Modify: `apps/api/src/easysynq_api/cli/{retention_maintenance,hold_maintenance,hold_release_authorizer,r27_maintenance,audit_maintenance,backup_maintenance,recovery_verifier}.py`
- Modify: `.env.example`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: non-secret env plus role-specific files under `/run/secrets`.
- Produces: one allow-listed no-follow scalar secret source and minimal settings model per process; no runtime `.env` injection or secret default.

- [ ] **Step 1: Write file-source and capability-matrix REDs**

Test: inline+file conflict even if one is empty; relative/outside-root path; symlink/FIFO/directory; unreadable/missing; empty/oversize (>64 KiB); NUL; multiple trailing newlines; and production secret inline all fail without logging the value. One terminal LF is stripped; other bytes are preserved as UTF-8. Dev/test may override the secret root to a temporary absolute directory; production root is exactly `/run/secrets`.

For each process kind, assert required secrets are present and forbidden secrets are absent. API/worker/Beat must fail startup if a forbidden owner/bypass/R27/backup/audit/bootstrap variable is supplied, even when its file is not mounted.

```bash
(cd apps/api && uv run pytest tests/unit/test_file_secret_settings.py tests/unit/test_process_capability_settings.py -q)
```

Expected RED: settings read direct env/`.env`, accept shared secrets/defaults, and have no process matrix.

- [ ] **Step 2: Implement the allow-listed file source**

`ApprovedFileSecretSource` recognizes only a constant map of sensitive scalar fields. It opens with `O_RDONLY|O_CLOEXEC|O_NOFOLLOW`, verifies `fstat` regular file and size 1–65536, decodes strict UTF-8, rejects NUL, and removes exactly one terminal LF. Both `NAME` and `NAME_FILE` present is `SecretSourceConflict`. Logs contain only the setting name/error class.

The allow-list includes all current secret scalars: DB URLs and migration role passwords; S3 access/secret keys; audit sink write/read secrets; Keycloak bootstrap/provision/export client secrets; backup encryption key; application KEK; SMTP password; operations webhook token; and any existing bootstrap credential secret. PEM/signing keys stay fixed path settings and are never loaded as scalar strings.

- [ ] **Step 3: Remove production credential defaults and split process models**

Define `ProcessKind`:

```text
MIGRATE, API, WORKER, BEAT, RETENTION, HOLD_AUTHORIZER, HOLD_MAINTENANCE,
R27_AUTHORIZER, R27_MAINTENANCE, RECOVERY_KEY_MANAGER, AUDIT_SIGNER,
BACKUP_MAINTENANCE, KEYCLOAK_RECONCILE
```

Each entry point constructs only its typed settings/capability view. Use the generic names `DATABASE_URL_FILE`, `DATABASE_URL_SYNC_FILE`, `S3_ACCESS_KEY_FILE`, and `S3_SECRET_KEY_FILE`; Compose mounts a different value per service. Do not add all role DSNs to one API-readable Settings object. BeatSettings is Redis-only. Hold-authorizer settings have DB plus the trusted wrapper's bounded operator-identity input but no S3/Redis/key; hold maintenance has DB plus hold-only S3 and no delete/bypass setting. R27 authorizer settings have OIDC/signing DB but no S3/Redis. R27 maintenance has DB/S3/public key but no authorizer private/Redis. Keycloak-reconcile settings have only fixed issuer/realm/client IDs plus the five fixed secret-file paths; no DB/S3/Redis or arbitrary reconciliation input.

- [ ] **Step 4: Validate fixed key paths and prohibit runtime generation**

Required paths are regular, non-symlink, read-only files at fixed names:

```text
/run/secrets/r27_authorizer_signing_key       authorizer only
/run/secrets/r27_authorizer_public_key        R27 maintenance only
/run/secrets/audit_checkpoint_signing_key     audit signer only
/run/secrets/audit_checkpoint_public_key      API/verifier consumers only
/run/secrets/verify_token_signing_key          owning signer only
/run/secrets/verify_token_public_key           API/verifier consumers only
```

Startup never creates/falls back when a path is missing. Installation owns generation.

- [ ] **Step 5: Make `.env.example` non-secret**

Retain topology/origins/bucket names/cadences and required `DOCUMENT_WORM_MINIMUM`, plus paths such as `DATABASE_URL_FILE=/run/secrets/database_url`. Remove plaintext credential/example-secret values. Use placeholders from the first draft. Add `.easysynq-secrets/` and isolated live fixture roots to `.gitignore`.

- [ ] **Step 6: Run GREEN and leak scans**

```bash
(cd apps/api && uv run pytest tests/unit/test_file_secret_settings.py tests/unit/test_process_capability_settings.py -q)
bash scripts/require-no-match.sh '(PASSWORD|SECRET|TOKEN|KEK)=([^<]|$)' .env.example
bash scripts/check-no-site-data.sh
```

Expected GREEN: settings tests pass; no plaintext example secret assignment.

- [ ] **Step 7: Verify and commit Task 13**

```bash
(cd apps/api && uv run ruff check src/easysynq_api/config.py src/easysynq_api/config_file_secrets.py src/easysynq_api/process_settings.py src/easysynq_api/db/session.py tests/unit/test_file_secret_settings.py tests/unit/test_process_capability_settings.py)
(cd apps/api && uv run mypy src)
git diff --check
git add apps/api/src/easysynq_api/config.py apps/api/src/easysynq_api/config_file_secrets.py apps/api/src/easysynq_api/process_settings.py apps/api/src/easysynq_api/db/session.py apps/api/src/easysynq_api/main.py apps/api/src/easysynq_api/r27_authorizer_main.py apps/api/src/easysynq_api/tasks/app.py apps/api/src/easysynq_api/tasks/beat_app.py apps/api/src/easysynq_api/cli/{retention_maintenance,hold_maintenance,hold_release_authorizer,r27_maintenance,audit_maintenance,backup_maintenance,recovery_verifier}.py apps/api/tests/unit/test_file_secret_settings.py apps/api/tests/unit/test_process_capability_settings.py migrations/env.py .env.example .gitignore
git commit -m "feat(identity): load only declared file-backed secrets"
```

---

## Task 14: Provision effective MinIO and Keycloak least-privilege identities

**Files:**
- Modify: `infra/compose/minio/minio-init.sh`
- Create: `infra/compose/minio/policies/ordinary-vault.json.in`
- Create: `infra/compose/minio/policies/retention-maintenance.json.in`
- Create: `infra/compose/minio/policies/hold-maintenance.json.in`
- Create: `infra/compose/minio/policies/r27-maintenance.json.in`
- Modify: `infra/compose/keycloak/realm-export.json`
- Modify: `infra/compose/keycloak/keycloak-init.sh`
- Create: `apps/api/src/easysynq_api/cli/keycloak_reconcile.py`
- Create: `apps/api/src/easysynq_api/cli/r27_approver.py`
- Create: `apps/api/tests/unit/test_identity_bootstrap_contract.py`
- Create: `apps/api/tests/integration/test_minio_principal_boundaries.py`
- Create: `apps/api/tests/integration/test_keycloak_r27_client.py`
- Create: `scripts/test-r27-keycloak.sh`
- Modify: `scripts/easysynq`

**Interfaces:**
- Consumes: bootstrap-only root/admin credentials and purpose-specific generated credentials.
- Produces: ordinary/retention/hold/R27 MinIO policies; a bounded post-ready Keycloak reconciler that installs the generated confidential client secrets; scoped Keycloak provisioner/export/role-manager clients; exact R27 public client/role/LoA2 flow; host-only role membership command.

- [ ] **Step 1: Write effective-policy REDs against real MinIO/Keycloak**

MinIO tests authenticate as each actual user and prove:

- ordinary promote/read/version/retention extend/hold ON succeeds;
- ordinary hold OFF and explicit Governance bypass/delete-before-expiry fail AccessDenied;
- retention exact read/extend/hold ON succeeds, but hold OFF/delete/bypass/admin fails;
- hold maintenance may read legal-hold state and set `OFF` only for an exact approved WORM version, but retention write/shorten, object GET/PUT/COPY/delete, Governance bypass, bucket/list/admin, and unrelated bucket access all fail;
- R27 exact read/hold transition/bypass delete in approved WORM buckets succeeds, but bucket admin/unrelated bucket access fails;
- root credentials are not any application credentials;
- if `s3:object-lock-legal-hold = ON` condition cannot be enforced by the pinned server, the test fails and implementation stops for owner review.

Keycloak tests prove exact client/audience/PKCE/redirect/ACR/token lifetime/claims, no implicit/direct/device/service-account flow, no default R27 members, and idempotent reconciliation of three distinct generated confidential secrets. Each provision/export/role-manager credential authenticates only as its matching client; a swapped/wrong secret fails. The scoped API user provisioner cannot map `r27-approver` or alter client/flow, and the host role manager can grant/revoke only that role. Reconciler stdout/stderr and rendered config contain none of the fixture secret values.

```bash
(cd apps/api && uv run pytest tests/unit/test_identity_bootstrap_contract.py tests/integration/test_minio_principal_boundaries.py tests/integration/test_keycloak_r27_client.py -q)
```

Expected RED: MinIO root equals application credential, realm has no R27 client/role/step-up flow, and generated confidential client secrets have no matching Keycloak provisioning path.

- [ ] **Step 2: Provision four MinIO policies/users idempotently**

Templates interpolate only validated bucket names; reject whitespace/metacharacters instead of building permissive JSON. Ordinary policy grants required exact object/version/list/copy operations, retention read/write, and `PutObjectLegalHold` only when requested status is ON. Retention policy is a smaller subset without OFF/delete/bypass. Hold policy grants only the minimum exact-version legal-hold read plus `PutObjectLegalHold` constrained to `OFF`; it has no object-data read/write, retention write, delete, list, or bypass. R27 policy permits exact version GET/retention/hold/delete plus bypass only in documents/records WORM domains. None grants admin policy/user/bucket actions. If the pinned MinIO release cannot enforce both ON-only and OFF-only action conditions, implementation stops for owner review; it does not grant unconditional `PutObjectLegalHold` to either principal.

`minio-init` reads root and output-user secrets from its bootstrap secret mount, creates/updates users/policies, performs positive/negative probes, and exits. Root never appears in a long-running application mount.

- [ ] **Step 3: Configure the exact R27 OIDC client and LoA2 flow**

Realm export/reconciliation creates public client ID and audience `easysynq-r27-authorizer`, Authorization Code only, PKCE S256, exact `${PUBLIC_BASE_URL}/r27/callback` redirect and `${PUBLIC_BASE_URL}/` post-logout origin, token lifespan 120 seconds, audience mapper, `sid`/`auth_time` claims, realm role `r27-approver`, and ACR `urn:easysynq:acr:r27-webauthn` mapped to LoA2 with WebAuthn user verification and Max Age 0. No wildcard URI.

- [ ] **Step 4: Reconcile generated confidential clients through one bounded post-ready service**

Create `python -m easysynq_api.cli.keycloak_reconcile`. It has no DB/S3/Redis/application settings and reads exactly five mode-0400 files from its own secret volume: bootstrap admin username/password plus `keycloak_provision_client_secret`, `keycloak_export_client_secret`, and `keycloak_role_manager_client_secret`. It polls the internal Keycloak OIDC discovery/token endpoints with bounded exponential backoff for at most five minutes after `keycloak: service_started`, sends credentials only in HTTPS/internal-HTTP request bodies (never argv, URL, rendered environment, log, or exception text), and exits nonzero on timeout/provider mismatch.

The reconciler has a fixed realm/client/role/flow allow-list. It idempotently creates or updates the ordinary user-provisioning, backup realm-export, and host R27-role-manager confidential clients with the exact generated secrets, installs their fine-grained permissions, configures the R27 public client/LoA2 flow, performs positive client-credentials probes for all three confidential clients, and performs negative authorization probes. It cannot accept a client ID, realm, redirect, role, flow, secret path, or permission from argv/database. Realm export contains only non-secret client definitions/placeholders; no generated client secret is templated into a bind-mounted file.

Use Keycloak fine-grained admin permissions to allow only required user fields/export read and explicitly deny realm-role mapping by the provisioner, R27 client/flow mutation, and cross-client secret reads. Release acceptance is the live 403 proof; if Keycloak 26.7 cannot express the denial, stop for owner review instead of retaining bootstrap admin in API. After successful reconciliation, the one-shot exits; only Keycloak itself and this bounded bootstrap service ever receive bootstrap-admin files. API, worker, Beat, R27 authorizer/maintenance, backup maintenance, and role manager receive only their own scoped credentials.

- [ ] **Step 5: Add host-only audited R27 membership management**

Exact syntax:

```text
./scripts/easysynq r27-approver grant --username USERNAME
./scripts/easysynq r27-approver revoke --username USERNAME
```

The wrapper invokes a one-shot operator service with scoped role-manager credentials and audit-only DB authority. It resolves one existing Keycloak subject, shows subject/username/action, requires confirmation, changes only `r27-approver`, appends audit, and never handles passkey private material. There is no API route or automatic persona seed that grants this role.

- [ ] **Step 6: Run GREEN real-service gates**

```bash
(cd apps/api && uv run pytest tests/unit/test_identity_bootstrap_contract.py -q)
(cd apps/api && uv run pytest tests/integration/test_minio_principal_boundaries.py tests/integration/test_keycloak_r27_client.py -q)
bash scripts/test-r27-keycloak.sh
```

Expected GREEN: effective allow/deny behavior and pinned realm token claims pass.

- [ ] **Step 7: Verify and commit Task 14**

```bash
(cd apps/api && uv run ruff check src/easysynq_api/cli/keycloak_reconcile.py src/easysynq_api/cli/r27_approver.py tests/unit/test_identity_bootstrap_contract.py tests/integration/test_minio_principal_boundaries.py tests/integration/test_keycloak_r27_client.py)
bash -n infra/compose/minio/minio-init.sh infra/compose/keycloak/keycloak-init.sh scripts/easysynq scripts/test-r27-keycloak.sh
git diff --check
git add infra/compose/minio infra/compose/keycloak apps/api/src/easysynq_api/cli/keycloak_reconcile.py apps/api/src/easysynq_api/cli/r27_approver.py apps/api/tests/unit/test_identity_bootstrap_contract.py apps/api/tests/integration/test_minio_principal_boundaries.py apps/api/tests/integration/test_keycloak_r27_client.py scripts/easysynq scripts/test-r27-keycloak.sh
git commit -m "feat(identity): provision R27 and vault principals"
```

---

## Task 15: Harden first-party images and compose the isolated service/network/secret topology

**Files:**
- Modify: `apps/api/Dockerfile`
- Modify: `apps/web/Dockerfile`
- Create: `apps/web/Caddyfile.runtime`
- Modify: `infra/compose/keycloak/Dockerfile`
- Create: `infra/compose/keycloak/keycloak-entrypoint.sh`
- Create: `infra/compose/init/secret-init.sh`
- Create: `infra/compose/init/runtime-volume-init.sh`
- Modify: `infra/compose/compose.yml`
- Modify: `infra/compose/compose.s.yml`
- Modify: `infra/compose/compose.m.yml`
- Modify: `infra/compose/compose.dev.yml`
- Modify: `infra/compose/compose.production.yml`
- Modify: `infra/compose/compose.airgap.yml`
- Modify: `infra/compose/caddy/Caddyfile`
- Modify: `infra/images.lock`
- Create: `apps/api/tests/unit/test_container_identity_contract.py`
- Modify: `apps/api/tests/unit/test_deploy_configuration.py`
- Modify: `apps/api/tests/unit/test_caddy_headers.py`
- Create: `scripts/tests/test-appliance-compose-contract.sh`

**Interfaces:**
- Consumes: Tasks 12–14 entry points/principals and untracked host secret source directory.
- Produces: pinned non-root API/static web images; role-secret volumes; isolated DB/storage/broker/edge networks; all base services in S/M/dev/prod/airgap.

- [ ] **Step 1: Write image, render, network, and secret-mount REDs**

Assert Dockerfiles end in numeric USER; web final stage contains no Node/npm/Vite/dev dependency and serves port 8080; a request directly to `/documents/synthetic-id?tab=approvals` returns the SPA shell rather than 404; the outer proxy targets `web:8080`; first-party long-running services have read-only root/cap-drop/no-new-privileges/tmpfs; only exact secret volumes/networks; no `env_file`; migrate receives owner DB only and exits; API cannot resolve/reach executor backend; Caddy routes authorizer before SPA/API fallback; executor has no published/exposed ingress.

```bash
(cd apps/api && uv run pytest tests/unit/test_container_identity_contract.py tests/unit/test_deploy_configuration.py tests/unit/test_caddy_headers.py -q)
```

Expected RED: images run root, web uses Vite/Node runtime, and Compose shares `.env`, credentials, volumes, and one network.

- [ ] **Step 2: Make the API-family image a fixed non-root runtime**

After OS/dependency installation create group/user `10001:10001`, make `/app` and installed `.venv` readable, set `UV_CACHE_DIR=/tmp/uv-cache`, and end with `USER 10001:10001`. Compose commands use `/app/.venv/bin/gunicorn`, `celery`, and `python` directly. Do not chown mounted populated volumes at startup.

- [ ] **Step 3: Load Keycloak secrets from mounted files through one exec wrapper**

Add `/opt/easysynq/bin/keycloak-entrypoint.sh` to the pinned Keycloak image as root-owned mode 0555 and set it as the image entrypoint. It accepts only Keycloak's existing command/arguments. For each of the exact non-secret path variables below it requires an absolute path under `/run/secrets`, rejects a symlink/non-regular/unreadable/empty/over-64-KiB file, reads without tracing or output, strips exactly one terminal LF, exports the corresponding Keycloak variable, unsets the path variable, and immediately `exec`s `/opt/keycloak/bin/kc.sh "$@"`:

```text
EASYSYNQ_KC_DB_PASSWORD_FILE               -> KC_DB_PASSWORD
EASYSYNQ_KC_BOOTSTRAP_ADMIN_USERNAME_FILE -> KC_BOOTSTRAP_ADMIN_USERNAME
EASYSYNQ_KC_BOOTSTRAP_ADMIN_PASSWORD_FILE -> KC_BOOTSTRAP_ADMIN_PASSWORD
```

The files are `/run/secrets/keycloak_db_password`, `/run/secrets/keycloak_bootstrap_admin_username`, and `/run/secrets/keycloak_bootstrap_admin_password`, owned `1000:1000` mode 0400. Rendered Compose contains only those three path strings, never `KC_DB_PASSWORD` or a bootstrap value. The bounded PostgreSQL-based `keycloak-init.sh` similarly accepts `POSTGRES_PASSWORD_FILE=/run/postgres-secrets/postgres_password` and `KEYCLOAK_DB_PASSWORD_FILE=/run/secrets/keycloak_db_password`, validates/loads them locally for its `psql` calls, and never receives a plaintext password field in rendered environment.

Container tests render config and reject any secret-valued Keycloak environment key, inspect `Config.Env` for paths-only delivery, verify exact mounts/ownership, start the image, and prove the wrapper reaches a healthy Keycloak without emitting any fixture secret to stdout/stderr. Missing, symlinked, malformed, or conflicting direct-value variables fail before Keycloak starts.

- [ ] **Step 4: Replace Vite preview with a pinned static web runtime**

Build with `node:22-slim`, then copy only `/app/dist` and `apps/web/Caddyfile.runtime` into `caddy:2.10.2-alpine` (record it in `infra/images.lock`). Create `10002:10002`, own `/srv` and Caddy's bounded config/data directories, end with that USER, and run:

```dockerfile
ENTRYPOINT ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
```

The internal Caddyfile listens on `:8080`, sets `/srv` as root, uses `try_files {path} /index.html`, then `file_server`. It does not proxy or expose a second origin. Healthcheck uses the final image's verified BusyBox `wget`; no npm/node/playwright/cache exists in the final layers. The outer Caddy target changes from `web:5173` to `web:8080`, and both a container-local deep-link request and a browser refresh on a real `BrowserRouter` route are acceptance proofs.

- [ ] **Step 5: Initialize and verify role-specific secret volumes idempotently**

Installer writes source files under untracked `.easysynq-secrets/source` mode 0700/0600. One bounded root `secret-init` one-shot is the only service mounting that host directory. `secret-init.sh` has a fixed source-to-role-volume/file map. On first use it requires an empty real target directory, copies without printing through same-directory temporary files, sets exact UID/mode, fsyncs files/directory, and atomically writes a root-owned `0444 .easysynq-secret-volume-v1` marker containing only role name, schema version, expected relative filenames, modes, UIDs/GIDs, and SHA-256 digests.

On routine Compose recreation, a marked populated secret volume is expected: the initializer uses no-follow checks, verifies that the marker and complete file inventory exactly match the fixed map, checks regular-file type/owner/mode/size and compares each target digest to its current source file, then exits zero without rewriting or changing ownership. A missing/invalid marker, unexpected entry, symlink, changed source/target digest, wrong metadata, or partial layout fails closed with file name/error class only. Rotation is a separate explicit operator command/plan; init never silently refreshes a secret. It never recursively traverses or rewrites a populated volume.

Create separate named volumes for `postgres`, `minio-root`, `minio-bootstrap`, `keycloak`, `keycloak-reconcile`, `migrate`, `api`, `worker`, `beat`, `retention`, `hold-authorizer`, `hold-maintenance`, `r27-authorizer`, `r27-maintenance`, `recovery-key-manager`, `audit-signer`, `backup`, and `r27-role-manager`. Runtime services mount only their volume at `/run/secrets:ro`. API-family outputs are `10001:10001 0400`; Keycloak runtime outputs are `1000:1000 0400`; web has none. The same generated bootstrap value may be copied into the Keycloak and bounded reconciler volumes, but no volume is shared between those services. Other pinned third-party UID mappings are asserted from the actual images in the container test before use.

- [ ] **Step 6: Initialize only fresh writable runtime volumes**

`runtime-volume-init` uses a fixed allow-list for mirror and backup state. On first use it requires an empty non-link root, creates one root-owned `0444 .easysynq-runtime-volume-v1` marker plus a single `data/` directory with exact `10001:10001` ownership/mode, and fsyncs both. On recreation it expects application data under `data/`: it verifies the marker contents, exact top-level inventory (`marker`, `data`), and the non-link data-directory owner/mode, then exits zero without traversing or changing anything below `data/`. Foreign top-level entries, marker mismatch, link/reparse target, or wrong data-root metadata fail closed. API mounts mirror `data/` read-only; worker owns mirror `data/` write; backup service alone owns backup `data/` write. All other first-party state is read-only root plus `/tmp` tmpfs (`nosuid,nodev,noexec`, bounded size, correct UID).

Tests perform initial start, `docker compose down` without `-v`, and a second `up`; both one-shots must exit successfully and data/key fingerprints must remain unchanged. Tampering with one marker/file/owner/layout must make only the relevant initializer fail without repair.

- [ ] **Step 7: Install exact services and commands**

Base Compose includes:

```text
migrate                 alembic upgrade head; one-shot
keycloak-reconcile      python -m easysynq_api.cli.keycloak_reconcile; one-shot
api                     gunicorn easysynq_api.main:app :8000
worker                  ordinary Celery worker
beat                    ordinary Celery Beat; Redis only
retention-maintenance   python -m easysynq_api.cli.retention_maintenance --loop
hold-maintenance        python -m easysynq_api.cli.hold_maintenance --loop
r27-authorizer          gunicorn easysynq_api.r27_authorizer_main:app :8001
r27-maintenance         python -m easysynq_api.cli.r27_maintenance --loop
audit-signer            python -m easysynq_api.cli.audit_maintenance --loop
backup-maintenance      python -m easysynq_api.cli.backup_maintenance --loop
recovery-verifier-key-manager  operator profile, one-shot only
hold-release-authorizer        operator profile, one-shot only
r27-role-manager        operator profile, one-shot only
```

The target topology is exhaustive; tests compare rendered services, mounts, networks, users, commands, and dependency conditions to this table. `secrets-*` means the named role volume mounted read-only at `/run/secrets`; only the listed relative files exist. `tmpfs` means the bounded `/tmp` mount described above and is not durable.

| Service | Command / UID:GID | Secret volume and exact files | Writable state | Networks | Required dependency conditions |
|---|---|---|---|---|---|
| `secret-init` | `/init/secret-init.sh`; `0:0`, bounded one-shot | host source read-only plus every `secrets-*` target write-only for initialization | secret target volumes only | none | none |
| `runtime-volume-init` | `/init/runtime-volume-init.sh`; `0:0`, bounded one-shot | none | `mirror`, `backup`, `restore-scratch` initialization only | none | none |
| `postgres` | pinned image entrypoint; effective server `999:999` | `secrets-postgres`: `postgres_password` | `pgdata` | `db-app`, `db-identity`, `db-migrate`, `db-retention`, `db-hold`, `db-r27-authorizer`, `db-r27-maintenance`, `db-audit`, `db-backup`, `db-operator` | `secret-init: service_completed_successfully` |
| `redis` | `redis-server --save '' --appendonly no`; effective server `999:999` | none | tmpfs only | `broker` | none |
| `minio` | `minio server /data --console-address :9001`; explicit `1000:1000` | `secrets-minio-root`: `minio_root_access_key`, `minio_root_secret_key` | `miniodata`, tmpfs | `storage-app`, `storage-retention`, `storage-hold`, `storage-r27`, `storage-audit`, `storage-backup`, `storage-bootstrap`, `storage-edge` | `secret-init: service_completed_successfully` |
| `minio-init` | `/init/minio-init.sh`; `0:0`, bounded one-shot | `secrets-minio-bootstrap`: `minio_root_access_key`, `minio_root_secret_key`, and `{ordinary,retention,hold,r27,audit,backup}_s3_{access,secret}_key` (the brace expansion is the fixed twelve-file matrix asserted by tests) | tmpfs only | `storage-bootstrap` | `secret-init: service_completed_successfully`; `minio: service_started` |
| `keycloak-init` | `/init/keycloak-init.sh`; `0:0`, bounded one-shot | `secrets-postgres` at `/run/postgres-secrets`: `postgres_password`; `secrets-keycloak` at `/run/secrets`: `keycloak_db_password` | `keycloakimport` first-boot seed only | `db-identity` | `secret-init: service_completed_successfully`; `postgres: service_healthy` |
| `keycloak` | wrapper then `kc.sh start --optimized --import-realm`; explicit `1000:1000` | `secrets-keycloak`: `keycloak_db_password`, `keycloak_bootstrap_admin_username`, `keycloak_bootstrap_admin_password`, all passed by the three exact `EASYSYNQ_KC_*_FILE` paths; no application service mounts them | tmpfs; `keycloakimport:ro` | `db-identity`, `edge-identity`, `identity-bootstrap`, `identity-app`, `identity-r27`, `identity-backup` | `keycloak-init: service_completed_successfully` |
| `keycloak-reconcile` | `/app/.venv/bin/python -m easysynq_api.cli.keycloak_reconcile`; `10001:10001`, bounded one-shot | `secrets-keycloak-reconcile`: `keycloak_bootstrap_admin_username`, `keycloak_bootstrap_admin_password`, `keycloak_provision_client_secret`, `keycloak_export_client_secret`, `keycloak_role_manager_client_secret` | tmpfs only | `identity-bootstrap` | `secret-init: service_completed_successfully`; `keycloak: service_started`; internal readiness loop succeeds before mutation |
| `renderer` | `gotenberg --api-port=3000`; `1001:1001` | none | tmpfs only | `render` | none |
| `tika` | pinned image entrypoint; `35002:35002` | none | tmpfs only | `render` | none |
| `migrate` | `/app/.venv/bin/alembic upgrade head`; `10001:10001`, one-shot | `secrets-migrate`: `database_url`, `retention_db_password`, `hold_authorizer_db_password`, `hold_maintenance_db_password`, `r27_authorizer_db_password`, `r27_maintenance_db_password`, `recovery_key_manager_db_password`, `audit_signer_db_password`, `backup_db_password` | tmpfs only | `db-migrate` | `secret-init: service_completed_successfully`; `postgres: service_healthy` |
| `api` | `/app/.venv/bin/gunicorn easysynq_api.main:app --bind 0.0.0.0:8000`; `10001:10001` | `secrets-api`: `database_url`, `s3_access_key`, `s3_secret_key`, `keycloak_provision_client_secret`, `application_kek`, `smtp_password`, `operations_webhook_token`, `audit_checkpoint_public_key`, `verify_token_public_key` | tmpfs; `mirror/data:ro` | `edge-app`, `db-app`, `broker`, `storage-app`, `identity-app`, `mail` only in dev | `secret-init`, `runtime-volume-init`, `migrate`, `minio-init`, and `keycloak-reconcile: service_completed_successfully`; `redis: service_healthy` |
| `worker` | `/app/.venv/bin/celery -A easysynq_api.tasks.app worker -l info`; `10001:10001` | `secrets-worker`: `database_url`, `s3_access_key`, `s3_secret_key`, `application_kek`, `smtp_password`, `verify_token_signing_key` | tmpfs; `mirror/data`; import source read-only | `db-app`, `broker`, `storage-app`, `render`, `mail` only in dev | `secret-init`, `runtime-volume-init`, `migrate`, and `minio-init: service_completed_successfully`; `redis: service_healthy`; `renderer`, `tika: service_started` |
| `beat` | `/app/.venv/bin/celery -A easysynq_api.tasks.beat_app beat -l info`; `10001:10001` | `secrets-beat`: `redis_url` only | tmpfs only | `broker` | `secret-init`, `migrate: service_completed_successfully`; `redis: service_healthy` |
| `retention-maintenance` | `/app/.venv/bin/python -m easysynq_api.cli.retention_maintenance --loop`; `10001:10001` | `secrets-retention`: `database_url`, `s3_access_key`, `s3_secret_key` | tmpfs only | `db-retention`, `storage-retention` | `secret-init`, `migrate`, `minio-init: service_completed_successfully` |
| `hold-maintenance` | `/app/.venv/bin/python -m easysynq_api.cli.hold_maintenance --loop`; `10001:10001` | `secrets-hold-maintenance`: `database_url`, `s3_access_key`, `s3_secret_key` | tmpfs only | `db-hold`, `storage-hold` | `secret-init`, `migrate`, `minio-init: service_completed_successfully` |
| `r27-authorizer` | `/app/.venv/bin/gunicorn easysynq_api.r27_authorizer_main:app --bind 0.0.0.0:8001`; `10001:10001` | `secrets-r27-authorizer`: `database_url`, `r27_authorizer_signing_key` | tmpfs only | `edge-r27`, `db-r27-authorizer`, `identity-r27` | `secret-init`, `migrate`, `keycloak-reconcile: service_completed_successfully` |
| `r27-maintenance` | `/app/.venv/bin/python -m easysynq_api.cli.r27_maintenance --loop`; `10001:10001` | `secrets-r27-maintenance`: `database_url`, `s3_access_key`, `s3_secret_key`, `r27_authorizer_public_key` | tmpfs only | `db-r27-maintenance`, `storage-r27` | `secret-init`, `migrate`, `minio-init: service_completed_successfully` |
| `audit-signer` | `/app/.venv/bin/python -m easysynq_api.cli.audit_maintenance --loop`; `10001:10001` | `secrets-audit-signer`: `database_url`, `audit_sink_write_access_key`, `audit_sink_write_secret_key`, `audit_sink_read_access_key`, `audit_sink_read_secret_key`, `audit_checkpoint_signing_key` | tmpfs only | `db-audit`, `storage-audit` | `secret-init`, `migrate`, `minio-init: service_completed_successfully` |
| `backup-maintenance` | `/app/.venv/bin/python -m easysynq_api.cli.backup_maintenance --loop`; `10001:10001` | `secrets-backup`: `database_url`, `s3_access_key`, `s3_secret_key`, `backup_encryption_key`, `keycloak_export_client_secret` | tmpfs; `backup/data`; `restore-scratch/data` | `db-backup`, `storage-backup`, `identity-backup` | `secret-init`, `runtime-volume-init`, `migrate`, `minio-init`, `keycloak-reconcile: service_completed_successfully` |
| `recovery-verifier-key-manager` | `/app/.venv/bin/python -m easysynq_api.cli.recovery_verifier ...`; `10001:10001`, operator one-shot | `secrets-recovery-key-manager`: `database_url` | tmpfs only | `db-operator` | `secret-init`, `migrate: service_completed_successfully` |
| `hold-release-authorizer` | `/app/.venv/bin/python -m easysynq_api.cli.hold_release_authorizer ...`; `10001:10001`, operator one-shot | `secrets-hold-authorizer`: `database_url` | tmpfs only | `db-operator` | `secret-init`, `migrate: service_completed_successfully` |
| `r27-role-manager` | `/app/.venv/bin/python -m easysynq_api.cli.r27_approver ...`; `10001:10001`, operator one-shot | `secrets-r27-role-manager`: `database_url`, `keycloak_role_manager_client_secret` | tmpfs only | `db-operator`, `identity-r27` | `secret-init`, `migrate`, `keycloak-reconcile: service_completed_successfully` |
| `web` | internal Caddy runtime; `10002:10002` | none | tmpfs only | `edge-app` | none |
| `proxy` | pinned Caddy entrypoint; explicit documented `0:0` third-party edge exception with only required bind capability | none | `caddydata`, `caddyconfig` | `edge-app`, `edge-identity`, `edge-r27`, production-only `storage-edge` | `keycloak-reconcile: service_completed_successfully`; `api`, `web`, `r27-authorizer: service_healthy` |
| `mailpit` | pinned image entrypoint; explicit `10003:10003`, dev profile only | none | tmpfs only | `mail` | none |

Any rendered service, secret file, writable mount, network edge, dependency, published port, or UID not represented above makes the contract test fail. The sole long-running root exception is the pinned outer Caddy edge; it keeps no application secret and is tested separately for its minimal capability/mount set. One-shots with UID 0 are bounded init only and are absent after successful installation.

S keeps one of each; M may scale only stateless API/worker/R27 authorizer, never Beat, hold/retention/R27/audit/backup maintenance, or key/operator managers.

- [ ] **Step 8: Replace the single trust network with purpose networks**

Use `edge-app`, `edge-identity`, `edge-r27`, `db-app`, `db-identity`, `db-migrate`, `db-retention`, `db-hold`, `db-r27-authorizer`, `db-r27-maintenance`, `db-audit`, `db-backup`, `db-operator`, `broker`, `storage-app`, `storage-retention`, `storage-hold`, `storage-r27`, `storage-audit`, `storage-backup`, `storage-bootstrap`, `storage-edge`, `identity-bootstrap`, `identity-app`, `identity-r27`, `identity-backup`, `render`, and development-only `mail`. PostgreSQL/MinIO/Keycloak join the required server-side networks; ordinary clients do not join privileged peer networks. `keycloak-reconcile` alone joins `identity-bootstrap`; no long-running first-party service does. Caddy joins only edge networks plus the production object-store edge where required. The main API does not join `edge-r27`, `db-hold`, `db-r27-*`, `storage-hold`, or `storage-r27`.

- [ ] **Step 9: Route authorizer only through Caddy**

Add first-match `/r27-authority/* -> r27-authorizer:8001` before ordinary `/api/*` and SPA fallback. Preserve security headers/CSP and add only the exact callback path. Hold/R27 maintenance, hold-release authorizer, and key/role managers have no Caddy route, published port, Redis, or inbound network.

Configure the authorizer's renderable readiness dependency with this exact container-local liveness probe (no bearer or external network):

```yaml
healthcheck:
  test:
    - CMD
    - /app/.venv/bin/python
    - -c
    - >-
      import json,sys,urllib.request;
      r=urllib.request.urlopen('http://127.0.0.1:8001/healthz', timeout=2);
      sys.exit(0 if r.status == 200 and json.load(r) == {'status':'ok','service':'r27-authorizer'} else 1)
  interval: 10s
  timeout: 3s
  retries: 12
  start_period: 5s
```

`proxy.depends_on.r27-authorizer.condition` is `service_healthy`. The container contract test requires this exact probe/timing and proves a mismatched response remains unhealthy; `readyz` remains the stronger doctor/readiness check after startup.

- [ ] **Step 10: Render every profile with an isolated fixture**

```bash
docker compose --env-file .fixture/.env -f infra/compose/compose.yml -f infra/compose/compose.s.yml -f infra/compose/compose.dev.yml config --quiet
docker compose --env-file .fixture/.env -f infra/compose/compose.yml -f infra/compose/compose.m.yml -f infra/compose/compose.dev.yml config --quiet
docker compose --env-file .fixture/.env -f infra/compose/compose.yml -f infra/compose/compose.s.yml -f infra/compose/compose.production.yml config --quiet
docker compose --env-file .fixture/.env -f infra/compose/compose.yml -f infra/compose/compose.m.yml -f infra/compose/compose.production.yml config --quiet
docker compose --env-file .fixture/.env -f infra/compose/compose.yml -f infra/compose/compose.s.yml -f infra/compose/compose.airgap.yml -f infra/compose/compose.production.yml config --quiet
docker compose --env-file .fixture/.env -f infra/compose/compose.yml -f infra/compose/compose.m.yml -f infra/compose/compose.airgap.yml -f infra/compose/compose.production.yml config --quiet
bash scripts/tests/test-appliance-compose-contract.sh
```

Expected GREEN: all six S/M development/production/airgap renders and the appliance wrapper contract pass. The ignored `.fixture/` has a complete internally consistent placeholder origin tuple, every required non-secret setting, and its own disposable source-secret/runtime-volume paths. The appliance contract test stubs side-effecting helpers and Compose, proves the wrapper passes exactly base + S + airgap + production plus its selected fixture env, then renders that exact stack with the real Compose binary. No command reads or mutates the developer's real `.env`.

- [ ] **Step 11: Run GREEN structural gates and commit**

```bash
(cd apps/api && uv run pytest tests/unit/test_container_identity_contract.py tests/unit/test_deploy_configuration.py tests/unit/test_caddy_headers.py -q)
bash -n infra/compose/init/secret-init.sh infra/compose/init/runtime-volume-init.sh infra/compose/keycloak/keycloak-entrypoint.sh
bash scripts/tests/test-appliance-compose-contract.sh
git diff --check
git add apps/api/Dockerfile apps/web/Dockerfile apps/web/Caddyfile.runtime infra/compose infra/images.lock apps/api/tests/unit/test_container_identity_contract.py apps/api/tests/unit/test_deploy_configuration.py apps/api/tests/unit/test_caddy_headers.py scripts/tests/test-appliance-compose-contract.sh
git commit -m "feat(identity): isolate hardened runtime services"
```

---

## Task 16: Make installer, operator wrapper, doctor, appliance, and CI honor the new identity contract

**Files:**
- Modify: `scripts/install.sh`
- Modify: `scripts/easysynq`
- Modify: `scripts/doctor.sh`
- Modify: `scripts/test-first-admin-keycloak.sh`
- Create: `scripts/generate-role-secrets.sh`
- Create: `scripts/validate-role-secrets.sh`
- Create: `scripts/seed-worm-r27-dev.sh`
- Create: `scripts/tests/test-role-secret-contract.sh`
- Create: `scripts/tests/test-install-identity-contract.sh`
- Modify: `scripts/tests/test-doctor.sh`
- Modify: `scripts/tests/test-ci-hardening.sh`
- Modify: `infra/appliance/boot-test.sh`
- Modify: `infra/appliance/provision/easysynq-provision.sh`
- Modify: `infra/appliance/provision/bin/easysynq-compose`
- Modify: `infra/appliance/provision/bin/easysynq-reconfigure`
- Modify: `infra/appliance/provision/bin/easysynq-status`
- Modify: `.github/workflows/ci.yml`
- Modify: `justfile`

**Interfaces:**
- Consumes: Task 15 Compose/images and a fresh authorized target.
- Produces: safe ordered install, untracked role-secret material, exact operator commands, actual identity/readiness doctor checks, S/M/dev/prod/airgap/appliance render parity.

- [ ] **Step 1: Write install-order, no-secret-output, and appliance-parity REDs**

Tests assert installer order is clean eligibility -> generate source secrets/keys -> secret/runtime-volume init -> PostgreSQL/MinIO/Redis/Keycloak prerequisites -> MinIO init + post-ready Keycloak reconciliation -> migrate -> runtime -> readiness -> optional explicit dev seed. Assert the reconciler exits successfully before any scoped client starts, and no implicit `down -v`, volume deletion, secret echo, compatibility backfill of secret values, or populated recursive chown occurs. Appliance wrapper uses the same base+S+airgap+production topology and cannot mutate the developer's real `.env` during tests.

```bash
bash scripts/tests/test-role-secret-contract.sh
bash scripts/tests/test-install-identity-contract.sh
bash scripts/tests/test-doctor.sh
(cd apps/api && uv run pytest tests/unit/test_deploy_configuration.py -q)
```

Expected RED: current installer writes secrets to `.env`, starts shared-capability services, and CI/profile checks omit M/new identities.

- [ ] **Step 2: Generate role secrets and persistent keys once without output**

`generate-role-secrets.sh --root ABSOLUTE_FRESH_DIR` refuses filesystem root, a home directory itself, repository/workspace roots, existing nonempty directories, symlinks, and unsafe permissions. It creates a 0700 directory owned by the invoking trusted host account (root on the appliance) and 0600 files using `openssl rand`/Python `cryptography` with no value on stdout/stderr. Generate distinct DB passwords/DSNs for retention, hold authorizer, hold maintenance, R27 authorizer/maintenance, recovery key manager, audit, and backup; MinIO root/ordinary/retention/hold/R27/audit/backup users; Keycloak bootstrap/provision/export/role-manager clients; backup encryption key; R27 authorizer Ed25519 pair/key ID; audit Ed25519 pair; and verify-token pair. Generate no recovery-generation private key.

`validate-role-secrets.sh` validates exact file set, nonempty/length/key pair/fingerprint/distinctness and modes without printing values. A test captures output and fails on any generated substring.

- [ ] **Step 3: Enforce the fresh installation order**

The installer first runs a non-mutating eligibility command against the target DB/Compose project. It never tears down data. After explicit fresh eligibility it runs secret init and fresh-volume init, starts only Postgres/MinIO/Redis/Keycloak prerequisites, runs MinIO init and the bounded post-ready Keycloak reconciler, requires both one-shots to exit zero, runs `migrate` with only owner DSN and role-password files, waits for migration exit, then starts hardened long-running services. Remove migration/reconciler containers after success or prove each is exited with no bootstrap secret mounted into a running application process.

- [ ] **Step 4: Update operator commands to the exact Compose identity**

Every `scripts/easysynq` and appliance invocation uses the same env/overlay/project and role-secret root. Add bounded commands for retention status/retry, pending ordinary hold-release list/show/authorize, R27 status, recovery-verifier lifecycle, R27 approver membership, backup intents, and public audit verification. `hold-release authorize` accepts one UUID, shows the exact bounded summary, and requires interactive confirmation; automation additionally supplies the expected digest. Do not expose `exec` of arbitrary maintenance modules, a wildcard/bulk hold authorization, or a generic task sender.

- [ ] **Step 5: Make doctor verify effective capability, not file text only**

`doctor.sh stack` checks:

- sole migration head and migration container exited;
- actual bucket GOVERNANCE/versioning/retention and service-specific IAM probes;
- ordinary/retention hold-OFF denial, hold-principal OFF-only behavior, and hold-maintenance authorization/claim health;
- API/worker/Beat forbidden secret absence;
- effective numeric UID/GID, read-only root, declared writable paths, cap drop/no-new-privileges;
- authorizer/executor network reachability positive/negative matrix;
- authorizer active key matches mounted public key;
- all three scoped Keycloak clients authenticate with their own credential, fail with swapped credentials, and no long-running application container mounts bootstrap admin files;
- recovery registry is empty or every installed key lifecycle is valid, without requiring one;
- pending/failed/oldest claim health for each maintenance service;
- SELinux `:z` label behavior under dev overlay.

A blocked or unavailable probe emits `UNVERIFIED`/`FAIL`, never PASS.

- [ ] **Step 6: Preserve explicit synthetic dev seeding only**

`seed-worm-r27-dev.sh` accepts credentials from files/prompt, never literals/argv/log, creates one synthetic org/admin plus two clearly synthetic R27 identities, and does **not** grant the R27 realm role automatically. The walkthrough uses the host command to grant it and users enroll their own test passkeys. Production/appliance install never invokes this script.

- [ ] **Step 7: Update appliance ownership and wrapper isolation**

Provision `.env` as non-secret topology only and the secret source root as root-owned 0700. Appliance compatibility backfill may add non-secret origin keys only; it cannot synthesize/migrate/print credential values. `easysynq-compose` includes base+S+airgap+production and operator profiles only when explicitly requested. Reconfigure restarts only affected services and preserves key/secret volumes.

- [ ] **Step 8: Expand CI render and structural coverage**

CI generates an isolated placeholder secret fixture and renders S/M dev, S/M production, S/M airgap-production, plus an isolated appliance copy. Add image inspection for USER/final web layers, Caddy validation, secret-mount/network allowlist, and operator wrapper tests. Do not claim the separate disposable Fedora proof from these renders.

- [ ] **Step 9: Run GREEN installer/appliance gates**

```bash
bash scripts/tests/test-role-secret-contract.sh
bash scripts/tests/test-install-identity-contract.sh
bash scripts/tests/test-doctor.sh
bash scripts/tests/test-ci-hardening.sh
infra/appliance/boot-test.sh --contract-only
(cd apps/api && uv run pytest tests/unit/test_deploy_configuration.py tests/unit/test_container_identity_contract.py -q)
bash scripts/check-no-site-data.sh
```

Expected GREEN: install order, secret handling, doctor behavior, and every render shape pass.

- [ ] **Step 10: Verify and commit Task 16**

```bash
bash -n scripts/install.sh scripts/easysynq scripts/doctor.sh scripts/generate-role-secrets.sh scripts/validate-role-secrets.sh scripts/seed-worm-r27-dev.sh scripts/tests/test-install-identity-contract.sh infra/appliance/boot-test.sh infra/appliance/provision/easysynq-provision.sh infra/appliance/provision/bin/easysynq-compose infra/appliance/provision/bin/easysynq-reconfigure infra/appliance/provision/bin/easysynq-status
git diff --check
git add scripts infra/appliance .github/workflows/ci.yml justfile apps/api/tests/unit/test_deploy_configuration.py apps/api/tests/unit/test_container_identity_contract.py
git commit -m "feat(install): enforce role-separated fresh deployment"
```

Inspect the staged `scripts/` set before commit so unrelated local scripts are excluded.

---

## Task 17: Publish the split edge contract and build the separate R27 browser flow

**Files:**
- Modify: `packages/contracts/openapi.yaml`
- Modify: `packages/contracts/.contract.lock`
- Modify: generated Python and TypeScript contract artifacts through `just contracts`
- Modify: `apps/api/src/easysynq_api/problems.py`
- Modify: `apps/api/tests/unit/test_problem_code_contract.py`
- Modify: `apps/api/tests/integration/test_contract_response_schemas.py`
- Create: `apps/web/src/lib/r27Authorizer.ts`
- Create: `apps/web/src/lib/r27Authorizer.test.tsx`
- Create: `apps/web/src/features/records/r27Types.ts`
- Create: `apps/web/src/features/records/r27Hooks.ts`
- Create: `apps/web/src/features/records/r27Hooks.test.tsx`
- Create: `apps/web/src/features/records/R27LifecyclePanel.tsx`
- Create: `apps/web/src/features/records/R27LifecyclePanel.test.tsx`
- Create: `apps/web/src/features/records/R27AuthorizeDialog.tsx`
- Create: `apps/web/src/features/records/R27CallbackPage.tsx`
- Create: `apps/web/src/features/records/holdReleaseTypes.ts`
- Create: `apps/web/src/features/records/holdReleaseHooks.ts`
- Create: `apps/web/src/features/records/holdReleaseHooks.test.tsx`
- Create: `apps/web/src/features/records/HoldReleaseStatusPanel.tsx`
- Create: `apps/web/src/features/records/HoldReleaseStatusPanel.test.tsx`
- Modify: `apps/web/src/features/records/RecordDetailPage.tsx`
- Modify: `apps/web/src/features/records/RecordDetailPage.test.tsx`
- Modify: `apps/web/src/lib/auth.tsx`
- Modify: `apps/web/src/App.tsx`
- Modify: `apps/web/src/test/msw/handlers.ts`
- Modify: `apps/web/e2e/support/api.ts`
- Modify: `apps/web/e2e/smoke.spec.ts`

**Interfaces:**
- Consumes: Task 6 ordinary hold-release and Task 8/11 R27 endpoint apps/statuses.
- Produces: one generated combined edge contract with runtime ownership; typed asynchronous ordinary hold-release status with no browser authorization; main read-only R27 status; memory-only popup PKCE authorizer client; status-driven accessible UI; strict token routing.

- [ ] **Step 1: Write contract and browser token-isolation REDs**

Tests require:

- main API R27 POST operations are absent and GET status schemas enumerate every state/result;
- legal-hold placement returns the Record only after synchronous ON read-back, release returns 202 `PENDING_AUTHORIZATION`, status enumerates every hold-release state, and no browser/API route can authorize it;
- authorizer prepare/commit/config/request schemas reject extra target fields;
- stable problem codes cover pending sync, recovery wait, retrying/terminal failure, exact version missing/mismatch, WORM read-back mismatch, legacy refusal, mode mismatch, surviving owner, and capability denial;
- R27 manager uses dedicated client/audience/callback/ACR/max_age, memory store, and clears after action;
- normal AuthProvider ignores `/r27/callback`;
- R27 fetch accepts only same-origin `/r27-authority/` and only an explicit authorizer token; normal `useApi` never handles it;
- ordinary API requests never carry R27 token and authorizer requests never carry ordinary token;
- consumed/unknown destructive action never presents a blind retry; it refetches state first.

```bash
just contracts-check
npm --prefix apps/web test -- src/lib/r27Authorizer.test.tsx src/features/records/r27Hooks.test.tsx src/features/records/R27LifecyclePanel.test.tsx src/features/records/holdReleaseHooks.test.tsx src/features/records/HoldReleaseStatusPanel.test.tsx src/features/records/RecordDetailPage.test.tsx
```

Expected RED: contract exposes legacy main POSTs and web has no isolated manager/status surface.

- [ ] **Step 2: Edit the OpenAPI source, then regenerate—never hand-edit outputs**

Replace `/records/{id}/worm-destroy-requests*` mutation operations with `GET /records/{id}/r27`. Change the existing legal-hold operation to document distinct 200 placement and 202 release responses; add the read-only per-Record hold-release status route and closed schemas. Add `/r27-authority/v1/...` operations tagged with `x-easysynq-runtime: r27-authorizer`; main status is `main-api`. Add strict request/response discriminators, all state enums, safe summaries, problem codes, and security schemes for normal versus R27 audience. No OpenAPI operation authorizes ordinary hold release. Run:

```bash
just contracts
just contracts-check
```

Commit the source, `.contract.lock`, and generated Python/TS together.

- [ ] **Step 3: Implement a memory-only popup PKCE manager**

`r27Authorizer.ts` loads public config, constructs a separate `UserManager` with `WebStorageStateStore({store: new InMemoryWebStorage()})`, exact `/r27/callback`, client ID, S256, and no silent renew. Each action calls `signinPopup` with `max_age=0`, `acr_values`, and essential ACR claim. `R27CallbackPage` calls only the popup callback. On settle/error, remove user and close session state.

Normal `AuthProvider` treats callback only when `location.pathname === "/"`; it never consumes the R27 code/state.

- [ ] **Step 4: Implement a narrow authorizer fetch helper**

Accept only relative paths beginning `/r27-authority/v1/`, reject absolute/cross-origin URLs, attach only the explicitly supplied R27 bearer, use `credentials: "same-origin"`, and parse typed problems. It has no access to normal `getAccessToken`. Conversely `useApi` rejects an authorizer-path request.

- [ ] **Step 5: Add prepare-display-confirm-commit UI**

`R27AuthorizeDialog` obtains a fresh token, calls prepare, renders the canonical legal basis/Record/target digest and derivative summary returned by authorizer, and requires explicit confirmation before commit with action nonce. It never builds coordinates from main API state. Request, approve, and cancel each start a separate fresh popup.

- [ ] **Step 6: Add status-driven Record lifecycle UI**

`HoldReleaseStatusPanel` shows that release needs host authorization, then `AUTHORIZED`, `RUNNING`, `FAILED`, `VERIFIED`, or cancellation without exposing coordinates/operator identity. It never offers an authorize control. `R27LifecyclePanel` fetches normal read-only status and presents `WAITING_FOR_SECOND_APPROVER`, `WAITING_FOR_RECOVERY_GENERATION`, `READY_FOR_FINALIZATION`, `FINALIZING`, `FAILED`, `STALE`, `CANCELLED`, and `EXECUTED` with accessible status announcements. `usePermissions` is only an affordance hint; the server/authorizer decides. On ambiguous network/5xx after either release request or R27 commit, disable resubmit, refetch status/challenge, then offer a safe next action.

- [ ] **Step 7: Extend strict MSW and Playwright fixtures**

Add no permissive catch-all. Test hold placement success, release 202/pending-host status/verified refresh and absence of browser authorization; successful R27 request/second approval, cancellation, 403, stale/password-only token, recovery wait, terminal/retrying status, consumed action, callback return to exact Record, and intercepted headers proving token separation.

```bash
npm --prefix apps/web test -- src/lib/r27Authorizer.test.tsx src/features/records/r27Hooks.test.tsx src/features/records/R27LifecyclePanel.test.tsx src/features/records/holdReleaseHooks.test.tsx src/features/records/HoldReleaseStatusPanel.test.tsx src/features/records/RecordDetailPage.test.tsx
npm --prefix apps/web run test:browser -- e2e/smoke.spec.ts
```

Expected GREEN: unit and mocked browser flow pass without token confusion/blind destructive retry.

- [ ] **Step 8: Run contract/backend response gates**

```bash
just contracts-check
just test-contract
(cd apps/api && uv run pytest tests/integration/test_contract_response_schemas.py -m contract --tb=short)
```

Expected GREEN for both app factories and generated artifacts.

- [ ] **Step 9: Verify and commit Task 17**

```bash
npm --prefix apps/web run lint
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
git diff --check
git add packages/contracts apps/api/src/easysynq_api/problems.py apps/api/tests/unit/test_problem_code_contract.py apps/api/tests/integration/test_contract_response_schemas.py apps/web/src apps/web/e2e
git commit -m "feat(r27): expose isolated browser authorization flow"
```

---

## Task 18: Run integrated mutation proofs, profile smokes, and the release-blocking live walkthrough

**Files:**
- Create: `scripts/test-worm-r27-live.sh`
- Create: `apps/web/e2e-live/worm-r27.spec.ts`
- Create: `docs/superpowers/reviews/2026-08-18-s-worm-retention-container-identity-evidence.md`

**Interfaces:**
- Consumes: complete Tasks 1–17 tree and an explicitly authorized disposable local Compose project.
- Produces: fresh RED/GREEN/mutation evidence, real DB/MinIO/Keycloak/browser/runtime identity proof, all profile smoke results, and exact limitations.

- [ ] **Step 1: Re-run every load-bearing focused proof with a mutation/falsifier**

Record command, baseline failure from its RED commit, final result, and mutation that makes it fail for: persisted VersionId; coordinate immutability; shared P3Y/P10Y; newer same-key survival; document policy/fallback; event anchor; permanent hold; partial extension resume; app-forged ordinary hold authorization; cross-role OFF denial; ordinary bypass denial; API-side absence; R27 signature/replay; recovery gate/key lifecycle; exact executor; pack race; secret absence; SPA deep-link fallback; init/recreate idempotency; non-root; key restart; migration guard; WORM/non-WORM split.

Do not edit a test to make an unexplained pass. A mutation that still passes blocks release.

Expected RED: for each controlled mutation, the named focused test fails on the intended public-state, exact-store, grant, signature, identity, or routing assertion. A mutation that remains green is not evidence and must be repaired in the owning task.

- [ ] **Step 2: Revalidate destructive scope immediately before local reset**

`test-worm-r27-live.sh` prints exact Compose project, containers, networks, named volumes, and bind mounts. It requires an explicit `--project <isolated-name> --confirm-disposable <same-name>` and rejects default/broad/root paths. Recheck no link/reparse bind target. Only after owner confirmation may it run that project's `down -v`; it never deletes source/secret directories recursively. Record what is recoverable.

- [ ] **Step 3: Install fresh and seed synthetic identities/data**

Install target branch through Task 16 flow, seed synthetic admin/org/policies/content without logging credentials, create two synthetic R27 users, enroll distinct test passkeys, and grant external role through the host-only command. Prove ordinary API provisioner cannot grant it.

- [ ] **Step 4: Walk through live identity and secret isolation**

Capture:

- numeric UID/GID for API/web/worker/Beat/retention/hold authorizer/hold maintenance/R27 authorizer/R27 maintenance/audit/backup;
- read-only root and exact writable paths;
- forbidden secret unreadability in API/worker/Beat/web and migrate exited;
- API network failure to hold/R27 authorizers and executors while intended links succeed;
- ordinary/retention MinIO success plus bypass/hold-OFF denial, hold-principal OFF-only behavior, and R27 cross-claim denial;
- app/maintenance DB coordinate mutation denial;
- empty production recovery verifier registry and key-install denial for API/authorizer.

Use safe names/UUIDs only; redact local secrets/host/site data before committing evidence.

- [ ] **Step 5: Walk through live exact retention behavior**

In UI/supported endpoints: check in a controlled Document and inspect persisted VersionId/exact read-back; attach identical bytes to longer Record and prove one version ratchets; exercise provisional then real event without shortening; stop retention service, request extension, show pending, restart and show verified; create a temporary domain hold and show `ON`; request release and show `PENDING_AUTHORIZATION`; prove an app-role forged authorization is denied; run the exact host authorization command; then show hold maintenance rechecks owners and reads back `OFF` without delete/bypass. Separately create a permanent owner and show legal hold remains `ON` and the ordinary authorizer refuses it.

- [ ] **Step 6: Walk through real R27 browser authority and deliberate recovery wait**

Use the browser from login through Documents, Approvals, Records, R27 request and second approval. Show two fresh passkey steps, canonical manifest, exact claims/signature binding through supported status/diagnostics, replay/stale/password-only/ordinary-token denial, and normal/R27 token header separation. Show state remains `WAITING_FOR_RECOVERY_GENERATION`; no pack/owner/hold/version changes. Attempt a forged app-credential witness/flag and show gate remains closed.

Do **not** install the test recovery public key or demonstrate live production deletion. Run exact executor success only in the real-MinIO integration test with ephemeral in-process key, recording newer-version survival as test evidence—not production walkthrough.

- [ ] **Step 7: Smoke all shipped profiles under isolated project names**

Use unique project names such as `easysynq-worm-s-dev`, `...-m-dev`, `...-s-prod`, `...-m-prod`, `...-s-airgap`, `...-m-airgap`, and an isolated appliance copy. For each: `config`, dependency/bootstrap/up, readiness, identity/secret probes, `down` without `-v` then `up` to prove secret/runtime initializer idempotency and unchanged fingerprints/data, direct SPA deep-link refresh, and finally exact `down -v` of that project. Production origin values use placeholder test domains and do not contact external/customer services.

- [ ] **Step 8: Run the complete verification ladder fresh**

```bash
(cd apps/api && uv run alembic heads)
(cd apps/api && uv run alembic check)
just check
just test
just contracts-check
just test-contract
just test-browser
just migrate-roundtrip
just security-npm
just authority-check
bash scripts/check-no-site-data.sh
git diff --check
./scripts/doctor.sh contributor
./scripts/doctor.sh stack
```

Also run the dedicated isolated migration refusal suite, real MinIO principals/executor suites, real Keycloak script, profile/appliance smokes, and live Playwright spec. Long commands should run as durable process jobs with finished output retrieved; do not busy-wait or convert an unavailable service into PASS.

Expected GREEN: every available command exits 0 with no unexpected skip; any unavailable/partial command is recorded as such and blocks any claim that depends on it.

- [ ] **Step 9: Write evidence with exact outcomes and limitations**

The evidence file records commit, environment, commands, counts, mutation outcomes, browser observations, profile project names/teardown, and every failed/skipped/unavailable/partial item. It explicitly states production R27 deletion remains blocked pending `S-recovery-generation`, and the disposable Fedora proof remains separate.

- [ ] **Step 10: Verify and commit Task 18**

```bash
bash -n scripts/test-worm-r27-live.sh
bash scripts/check-no-site-data.sh
git diff --check
git add scripts/test-worm-r27-live.sh apps/web/e2e-live/worm-r27.spec.ts docs/superpowers/reviews/2026-08-18-s-worm-retention-container-identity-evidence.md
git commit -m "test(worm): prove atomic retention and identity boundary"
```

---

## Task 19: Update authority documentation, reconcile residuals, and perform final combined review

**Files:**
- Modify: `docs/current-status.md`
- Modify: `docs/slice-history.md`
- Modify: `docs/open-residuals.md`
- Modify: `docs/03-architecture-and-stack.md`
- Modify: `docs/06-records-and-evidence.md`
- Modify: `docs/08-setup-and-onboarding.md`
- Modify: `docs/12-security-and-audit.md`
- Modify: `docs/14-data-model.md`
- Modify: `docs/15-api-design.md`
- Modify: `docs/runbooks/fresh-linux-setup.md`
- Modify: `docs/runbooks/backup-restore.md`
- Create: `docs/runbooks/r27-authority-and-recovery-verifiers.md`
- Modify: `docs/dev-workflow.md`

**Interfaces:**
- Consumes: final code, generated contracts, live evidence, actual CI inventory.
- Produces: current truth, dated shipped history, honest residual closure/retention, operator runbooks, final atomic review verdict.

- [ ] **Step 1: Write documentation guards before narrative changes**

Extend authority checks to require: exact `0089`/service topology; GOVERNANCE-only wording; clean-only/no-downgrade boundary; production-empty recovery registry; main API read-only R27; purpose-specific secrets; non-root/static web; and live evidence link. Add negative guards for COMPLIANCE selection, all-version purge, API approval, shared `.env` secrets, runtime key fallback, and claims of production R27 deletion.

```bash
just authority-check
```

Expected RED: current docs still describe old Blob/R27/Compose behavior.

- [ ] **Step 2: Update executable/narrative truth in the right authority homes**

- `current-status.md`: current head, migration, actual services/gates/CI facts only;
- `slice-history.md`: dated implementation/evidence narrative;
- architecture/security/data/API docs: exact version, role/network/secret/authority contracts;
- setup/dev runbooks: fresh-only install and supported commands;
- backup runbook: backup-maintenance identity without claiming deferred content/destination work;
- new R27 runbook: approver grant/revoke, verifier install/retire/revoke, waiting state, rotation/revocation, incident diagnostics, and explicit absence of production recovery signer.

Use placeholders in every example; never paste live credentials, hostnames, object keys, fingerprints, user emails, or bucket names.

- [ ] **Step 3: Reconcile residuals without rewriting history**

Close `RES-RESTORE-SCRATCH-WORM-GUARD` only if its exact protected-target contract is actually satisfied by the final implementation/evidence; otherwise update its closure contract/status truthfully. Keep comprehensive DB grants, populated upgrade/lock/writer quiescence, complete backup/recovery generation, build/offline/deploy proof, malicious-host protection, and customer certification open/deferred exactly as the spec says. Add no duplicate ledger item for production recovery generation if an existing stable residual owns it.

- [ ] **Step 4: Run independent combined reviews**

Assign three fresh reviewers the complete `origin/main...HEAD` diff:

1. authority/spec/invariant coverage;
2. executability/migration/profile/test commands;
3. adversarial security: API compromise, exact identity, signature/replay/recovery/secret/network boundaries.

Resolve every blocker in its owning task and rerun affected/full gates. A clean checkpoint review is not approval of the combined atomic diff.

- [ ] **Step 5: Run final authority/site-data/diff gates**

```bash
just authority-check
bash scripts/check-no-site-data.sh
git diff --check
git status --short
```

Expected GREEN: authority/site-data/diff gates pass and only intended documentation/evidence changes remain unstaged.

- [ ] **Step 6: Commit documentation truth**

```bash
git add docs/current-status.md docs/slice-history.md docs/open-residuals.md docs/03-architecture-and-stack.md docs/06-records-and-evidence.md docs/08-setup-and-onboarding.md docs/12-security-and-audit.md docs/14-data-model.md docs/15-api-design.md docs/runbooks/fresh-linux-setup.md docs/runbooks/backup-restore.md docs/runbooks/r27-authority-and-recovery-verifiers.md docs/dev-workflow.md
git commit -m "docs(worm): record atomic retention and identity boundary"
```

- [ ] **Step 7: Prepare the final handoff without pushing**

Report observable outcome, exact files/commits, focused RED/GREEN/mutation evidence, full gate results, live walkthrough/profile results, clean-only/no-downgrade compatibility, retained residuals, and every unavailable/partial item. State that production R27 requests deliberately wait for a future signed recovery generation. Stop for owner review; use the repository's publish workflow only after explicit approval.

---

## Acceptance Coverage Matrix

| Approved invariant/proof | Primary implementation task(s) | Release evidence |
|---|---:|---|
| WR-1 exact target / exact identity RED | 1–5 | migration, storage, producer/consumer, live VersionId |
| WR-2 protection before visibility | 4–5 | fault injection + live check-in |
| WR-3 max shared owner | 4–6 | P3Y/P10Y mutation + live ratchet |
| WR-4 monotone storage | 2–6, 14 | DB/IAM negative proofs |
| WR-5 safe external/DB ordering | 4–6 | rollback/orphan/resume injection |
| WR-6 GOVERNANCE truth | 1, 3, 14, 16 | readiness + actual bucket proof |
| WR-7 permanent held | 4–6 | exact hold ON before commit |
| WR-15 ordinary domain hold release authority | 2, 6, 13–16 | host-confirmed digest, app-forgery denial, no-bypass exact OFF |
| WR-8 exact R27 authority | 7–11 | signatures, exact manifest and execution binding |
| WR-9 surviving-owner protection | 4, 10 | owner recheck and logical-only result |
| WR-10 no all-versions purge | 3, 10–11 | source absence + V2 survival |
| WR-11 derivative invalidation | 5, 10 | atomic source transaction proof |
| WR-12 pack/erase serialization | 4–5, 8, 10 | shared/exclusive race test |
| WR-13 recovery first | 9–10 | empty registry + waiting state |
| WR-14 destructive pre-hash | 3, 10 | owner/store tamper fixture |
| CI-1 no ordinary bypass | 11, 14–16 | effective IAM/secret absence |
| CI-2 no indirect privileged submission | 6, 11–16 | task/import/network absence |
| CI-3 purpose-separated credentials | 12–16 | rendered/live secret matrices |
| CI-4 non-root | 15–16 | image inspection + live UID/rootfs |
| CI-5 stable keys | 7–9, 13, 16 | restart/concurrent startup proof |
| CI-6 host trust boundary | 16, 19 | runbook/current truth |
| CI-7 signed authority | 2, 7–10 | app forgery + signature mutation |
| CI-8 externally enrolled actors | 8, 14, 17–18 | provisioner denial + two live passkeys |
| CI-9 fresh/replay-safe action | 7–8, 17–18 | claim matrix + replay proof |
| CI-10 host-rooted recovery trust | 9–10, 16 | one-shot key manager + empty production registry |
| OP-1 visible incomplete work | 6, 8–12, 17 | state APIs/UI/health/evidence |
| OP-2 idempotent recovery | 6, 8–10, 12 | process-kill/resume tests |
| OP-3 forward-only recovery | 1, 16, 19 | downgrade refusal/runbooks |
| Rendered secret absence / non-root / key restart | 13–16, 18 | all shapes + live inspection |
| Migration clean guard / WORM split | 1–5 | isolated Testcontainers + derivative tests |

### RED-first acceptance proof map

| Acceptance proof | Falsifying task/test | Final proof |
|---|---:|---|
| Exact target identity | 3, 5 — remove promotion VersionId persistence | exact producer/consumer + live check-in |
| WORM coordinate immutability | 2 — app/maintenance repoint/delete SQL | real-role PostgreSQL denial |
| Shared P3Y/P10Y bytes | 4–5 — protect new-Blob branch only | real MinIO dedup ratchet |
| Same-key newer version survives | 3, 10 — restore all-version loop | V1 purge/V2 read |
| Document policy reaches storage | 4–5 — skip policy/fallback protection | default and installation-minimum read-back |
| Event anchor | 4, 6 — unresolved basis `None` or shortening | provisional/later/earlier cases |
| Permanent hold | 4–6 — owner commit before hold read-back | real exact legal hold ON |
| Ordinary hold release | 2, 6, 14 — forge authorization/cross-claim/bypass | host-authorized exact OFF plus real-role/IAM denials |
| Pending extension resume | 6 — fail after target one | active/proposed/restart/activation states |
| Normal bypass denial | 14 — ordinary explicit bypass/OFF | real MinIO AccessDenied |
| API-side R27 absence | 11, 15 — route/task/import/network guard | app/task/render/live absence |
| R27 forgery resistance | 2, 7–10 — forged row/field/signature | DB/crypto/state denials |
| R27 step-up and replay | 7–8, 14, 17 | token matrix + two live passkeys/replay |
| Recovery-generation gate | 9–10 — approvals/forged flag only | WAITING with no source change |
| Recovery verifier lifecycle | 9–10 — retire/revoke/reuse mutations | lifecycle integration suite |
| R27 isolated exact execution | 10 — wrong binding/role/hash | test-signed real-MinIO execution |
| Derivative atomicity/race | 4, 10 — cross pack shared/exclusive lock | transaction and concurrency test |
| Rendered secret absence | 13, 15–16 | render + live unreadability matrix |
| SPA deep-link runtime | 15, 17–18 — remove `try_files` fallback or restore port 5173 | direct and proxied route refresh |
| Init/recreate idempotency | 15–16 — rerun marked volumes/tamper marker | down/up fingerprint persistence plus fail-closed tamper |
| Non-root runtime | 15–16 | image USER + live UID/rootfs |
| Key restart stability | 7–9, 13, 16 | concurrent recreate/persistent fingerprint |
| Migration clean guard | 1–2 | four isolated Testcontainers paths |
| WORM/non-WORM Blob split | 1, 3–5 | conditional assertion + derivative producers |

## Plan Self-Review Checklist

- [x] Every WR/CI/OP invariant and every RED-first acceptance row has a task, concrete falsifier, and final evidence location.
- [x] Every new table/type/function/service/path has one owner and consistent name across schema, code, Compose, tests, contract, and docs.
- [x] Every WORM pointer is classified; no generic Blob-liveness query is used as retention authority.
- [x] No plan step tells production code to infer latest VersionId, enumerate/delete all versions, remove a hold with ordinary credentials, or treat a mutable DB row/boolean as R27 authority.
- [x] Main API, general worker, and Beat have neither direct nor indirect privileged execution authority.
- [x] Production accepts no recovery private key/test signer and starts safely with an empty verifier registry.
- [x] The owner explicitly approved the design amendment that ordinary domain hold release requires a host-invoked DB-only authorizer plus a distinct no-bypass `hold-maintenance` service/role; R27 remains unable to consume that authority (2026-08-18).
- [x] The owner explicitly approved `R27_MANIFEST_TTL_SECONDS=86400`, validated to 300–604800 seconds, with this plan (2026-08-18).
- [x] Commands use actual repository entry points and preserve unavailable/skipped truth.
- [x] Profile/live destructive commands use isolated project names and exact target revalidation.
- [x] Placeholder/site-data scans pass and no local credential/fingerprint/object key appears in committed artifacts.
- [x] No `TBD`, `TODO`, “similar to”, or unspecified error-handling instruction remains.
