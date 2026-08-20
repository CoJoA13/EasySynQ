# Design — atomic `S-worm-retention` + `S-container-identity`

> **Status:** owner-approved design and owner-approved implementation boundary.
> **Date:** 2026-08-18 · **Repo commit at design time:** `1737821` · **Migration head:**
> `0088_bootstrap_credential` · **Next revision:** `0089`
> **Authority:** the merged execution-order design in
> [`2026-08-06-audit-remediation-execution-order-design.md`](2026-08-06-audit-remediation-execution-order-design.md),
> decisions R27, R37, and R38 in [`../../decisions-register.md`](../../decisions-register.md), and the
> owner decisions recorded here.
> **Release boundary:** WORM retention and container capability separation are one implementation PR,
> one release candidate, and one acceptance verdict. Neither half may ship alone.
> **Implementation authority:** the owner reviewed this specification and explicitly approved the
> task-level implementation plan, including D15 and D16, on 2026-08-18. Production edits remain governed
> by that plan and its review checkpoints.

---

## 1. Why this slice exists

The current vault proves the identity of staged source bytes, but it does not durably bind the resulting
WORM target version. A successful promotion returns a MinIO `VersionId` only in memory. `blob` persists a
bucket and logical key, while normal reads, recovery, and R27 cleanup can therefore address a mutable key
rather than one immutable object version.

Retention is also disconnected from domain policy. Both vault buckets receive one 30-day GOVERNANCE
default, while Record `worm_lock_period`, document-type defaults, later policy extensions, event-based
anchors, permanent retention, and shared-owner maximums do not reach the physical object. The database can
record COMPLIANCE although setup does not make the bucket enforce that mode.

That gap cannot be repaired credibly while ordinary application containers retain MinIO root and
governance-bypass authority. At the design baseline, `migrate`, `api`, `worker`, and `beat` inherit one
`.env`; ordinary runtime code consequently receives unrelated database-owner, MinIO-root, Keycloak-admin,
backup, audit, and signing capabilities. The API also performs the immediate R27 bypass in-process, and a
generic worker performs retry bypasses.

The merged execution-order authority therefore requires an atomic pair:

1. bind each owner to an exact target version and enforce monotone per-owner WORM obligations; and
2. remove root, governance-bypass, and unrelated privileged capabilities from ordinary containers at the
   same release boundary.

A retention-only release would protect an object from the same principal that can bypass the protection.
An identity-only release would narrow credentials without making the advertised retention policy true.
Both are invalid partial claims.

### 1.1 Design baseline and supersession

This design was audited at `main` commit `1737821`. A fresh
`cd apps/api && uv run alembic heads` reported `0088_bootstrap_credential (head)`, so the historical
`0086` reservation is stale. This pair owns `0089` after a mandatory heads recheck immediately before the
migration is authored.

This document supersedes two historical implementation assumptions for this slice:

- the old `0086` number is replaced by the next live head, currently `0089`; and
- the old populated-object backfill proposal is replaced by an explicit **clean-deployment-only**
  contract. The implementation refuses legacy `blob` rows rather than guessing a target `VersionId`.

Historical programme documents remain unchanged as history. Later migration-owning slices must resolve
their revision from the live Alembic head; this design does not reserve their new numbers.
Specifically, the execution-order table's historical `0089` reservation for `S-recovery-generation` is
superseded: that slice is now unnumbered and must claim the live next revision only when its own approved
design reaches implementation. No two current slices own `0089`.

---

## 2. Owner decisions

The owner approved these decisions interactively on 2026-08-18.

| ID | Decision | Selected contract |
|---|---|---|
| **D1 — end-to-end isolation** | Does API-compromise containment cover indirect task invocation as well as secret readability? | Yes. A compromised API can neither read privileged credentials nor submit arbitrary privileged work. |
| **D2 — R27 operation** | Automatic or operator-launched destruction after second approval? | Automatic through an isolated maintenance service once every signed recovery prerequisite is satisfied. |
| **D3 — object-lock mode** | GOVERNANCE, COMPLIANCE, or both? | GOVERNANCE only. A recorded COMPLIANCE installation stops for explicit verification; it is never silently relabelled. |
| **D4 — deployment compatibility** | Populated in-place upgrade or clean deployment? | Clean deployment only. No legacy object-version or volume-ownership backfill is claimed. |
| **D5 — document source retention** | How are controlled-document source bytes protected? | Pin the document-type policy at check-in, otherwise use the installation minimum; anchor at check-in; shared bytes take the longest obligation. |
| **D6 — unresolved event basis** | What protects an event-based Record before the event occurs? | Use capture date provisionally, then only extend when the real event date is recorded. |
| **D7 — policy extension** | Synchronous all-or-nothing request or durable pending work? | A visible, resumable extension operation; logical lengthening applies immediately and completes only after every exact version verifies. |
| **D8 — rollback** | May old code be restarted after the transition? | No in-place downgrade. Recover with a forward fix or a fresh restore target; physical locks are never presented as reversible. |
| **D9 — execution architecture** | How does automatic privileged work cross the API boundary? | Purpose-separated services claim durable PostgreSQL intents. No API-reachable privileged queue. |
| **D10 — permanent retention** | How does `PERMANENT` map to storage? | A legal hold on the exact version in addition to GOVERNANCE retention. R27 alone may remove it after revalidating dual control. |
| **D11 — R27 authority witness** | Can the executor trust approval rows written through the ordinary API credential? | No. A separate browser-facing R27 authorizer with its own Keycloak audience and signing key produces the two non-forgeable attestations. |
| **D12 — recovery safety** | May authorized R27 bytes be erased before a verified replacement recovery generation excludes them? | No. Execution remains visibly `WAITING_FOR_RECOVERY_GENERATION` until a separately signed generation attestation passes. |
| **D13 — hold application** | Must a permanent owner wait on an asynchronous hold service? | No. The ordinary vault principal may set legal hold only to `ON`, enforced by MinIO policy; it verifies synchronously before owner commit. That principal can never request `OFF`. |
| **D14 — R27 approver root** | Can API-provisioned identities become R27 approvers through ordinary application grants? | No. R27 additionally requires a host/bootstrap-controlled Keycloak realm role and WebAuthn step-up that the API provisioning account cannot grant or satisfy. |
| **D15 — ordinary hold release authority** | How can a non-permanent domain hold be released without giving the API or R27 a second authority path? | The API creates only a non-authoritative intent. A host-invoked, DB-only `hold-release-authorizer` authorizes its exact canonical digest after explicit confirmation; a distinct no-ingress, no-delete, no-bypass `hold-maintenance` service independently rechecks every owner before exact-version `OFF`. Permanent owners remain R27-only. |
| **D16 — R27 manifest lifetime** | How long may a two-person signed R27 manifest wait for recovery-generation proof? | `R27_MANIFEST_TTL_SECONDS` defaults to 86,400 seconds and is validated to 300–604,800 seconds. This is separate from the at-most-120-second action token; expiry requires fresh two-person authorization, never extension or reuse. |

### 2.1 Selected architecture — database-backed maintenance plane

The API writes typed, durable non-authoritative work intents and returns their state. A purpose-specific
maintenance service claims work from PostgreSQL, revalidates independently witnessed authority, performs
one allow-listed operation, and records the result. Privileged executors do not consume the ordinary Redis
queue and expose no API-reachable port.

Ordinary domain hold release uses two purpose-separated database handoffs. The API can create and display
a typed `PENDING_AUTHORIZATION` intent, but that row is not authority. A host operator invokes a bounded
`hold-release-authorizer` one-shot that reconstructs and confirms the exact canonical digest before
recording immutable authorization. A separate `hold-maintenance` process can claim only that authorized
operation, rechecks the complete live owner set, and uses an OFF-only, no-bypass MinIO principal. Neither
service can consume or create R27 authority, and R27 cannot consume an ordinary hold authorization.

R27 user authority deliberately follows a separate path. A browser-facing `r27-authorizer` is routed by
Caddy on a network the main API does not join. The browser obtains a distinct audience-bound Keycloak
token that is never sent to the main API. The authorizer independently renders/canonicalizes the exact
destruction manifest, enforces request/approval authorization, and signs requester and approver
attestations. It has no MinIO credential. The executor trusts the authorizer public key, not actor IDs or
mutable rows written by the ordinary application role.

Each actor must also hold the dedicated Keycloak `r27-approver` realm role and complete a fresh
WebAuthn/passkey step-up for the authorizer client. That role has no ordinary API assignment path. Initial
and replacement membership is a host/bootstrap-controlled, audited operation; the API's scoped Keycloak
service account is denied realm-role mapping. EasySynQ's existing `record.dispose` check still applies, so
external enrollment supplements rather than replaces application authorization.

This uses PostgreSQL, already the authority for approvals and durable recovery markers, as the trust
handoff. It avoids another broker and avoids inventing a command-signing protocol. Polling latency is an
accepted tradeoff for a smaller and more inspectable security boundary.

### 2.2 Rejected — separate privileged broker

A second Redis instance could be unreachable from the API, but a trusted dispatcher would still have to
read database authority and publish work. It adds a broker, dispatcher, credentials, backup/recovery
surface, and health topology without removing the need for durable PostgreSQL intents.

### 2.3 Rejected — signed commands on the ordinary broker

Signed envelopes could make the ordinary broker an untrusted transport, but would add signer custody,
canonicalization, replay windows, nonce persistence, rotation, flood handling, and more complex failure
analysis. The API would still be able to fill the privileged transport with unauthenticated work.

### 2.4 Rejected — host-invoked break glass for every R27 request

Keeping bypass credentials out of all long-running containers gives strong custody separation, but it
changes approved R27 destruction into a manual operator workflow. The owner selected automatic execution
through an isolated service instead.

---

## 3. Binding invariants

The implementation is incomplete unless all invariants below hold together.

| ID | Invariant |
|---|---|
| **WR-1 — exact target identity** | Every committed WORM owner resolves to one durable `(bucket, key, VersionId)` whose physical coordinates and content digest are immutable after assertion. All WORM reads, verification, recovery, hold, and destructive calls specify that version. Rebuildable non-WORM derivatives retain their separate contract. |
| **WR-2 — protection before visibility** | No Document source version, Record WORM evidence owner, permanent generated artifact owner, or ingestion WORM success becomes active or visible until the exact target version's required retention and legal-hold state read back successfully. |
| **WR-3 — maximum owner obligation** | One shared physical version is retained through the maximum obligation of every live owner plus the proposed owner. Deduplication is never a reason to skip retention. |
| **WR-4 — monotone storage** | Normal storage paths may set or extend retention and may enable a required hold. They never shorten retention, remove a hold, or bypass GOVERNANCE. Ordinary domain hold release exists only through the D15 authority split; permanent release remains R27-only. |
| **WR-5 — safe external/DB ordering** | Remote over-retention before a database rollback is safe and reconcilable. No active owner/policy obligation may commit ahead of verified physical protection. A proposed longer revision remains explicitly pending for old owners until synchronization. |
| **WR-6 — GOVERNANCE truth** | Setup, stored configuration, MinIO bucket state, per-version calls, documentation, and diagnostics all say and enforce GOVERNANCE. COMPLIANCE is not a selectable or silently converted mode. |
| **WR-7 — permanent means held** | A `PERMANENT` owner or active domain legal hold keeps the exact physical version under legal hold. No scheduled disposition clears it. |
| **WR-8 — R27 authority is exact** | Only an executed, non-cancelled, two-person R27 request with matching immutable event, organization, legal basis, owner set, and exact version can authorize hold removal plus governance bypass. A boolean marker is never authority. |
| **WR-9 — surviving-owner protection** | R27 may erase a physical version only when no surviving owner still requires the bytes. Otherwise it disposes the targeted logical owner and records that shared physical bytes remain. |
| **WR-10 — no all-versions purge** | No EasySynQ path enumerates and deletes every version at a key as a substitute for exact identity. A newer same-key version survives an older version's purge. |
| **WR-11 — derivative invalidation** | R27 preserves R27's existing atomic Evidence Pack contract: affected packs become `UNAVAILABLE`, live shares are revoked, ZIP/portfolio pointers are cleared, and one-hop derivative tombstones bind the source event before any physical purge. |
| **WR-12 — pack/erase serialization** | Pack sealing holds the organization-scoped shared lock; R27 finalization holds its exclusive side. No pack can cross the signed destruction manifest or verified replacement-generation boundary. |
| **WR-13 — recovery generation first** | R27 physical invalidation/deletion cannot complete until a replacement recovery generation, built after the signed manifest and excluding every target/derivative, is independently verified. Missing support leaves the request pending, not bypassed. |
| **WR-14 — destructive target revalidation** | A signed manifest is not enough to trust a mutable locator. Immediately before any hold removal or bypass, R27 independently reads and hashes the signed exact version and compares it with the immutable Blob digest; any mismatch fails before a destructive storage call. |
| **WR-15 — ordinary hold release is exact** | A main-API row cannot authorize physical `OFF`. Only a host-confirmed authorization over one canonical operation digest plus a fresh `hold-maintenance` owner recheck may release a non-permanent hold on one exact version. A permanent or still-required hold fails closed. |
| **CI-1 — no ordinary bypass** | API, general worker, Beat, web, and migration containers never receive MinIO root or `s3:BypassGovernanceRetention`. |
| **CI-2 — no indirect privileged submission** | The API cannot authenticate to a privileged queue, invoke privileged service code, or provide an executable command payload. It can only create schema-validated domain intents through authorized application behavior. |
| **CI-3 — purpose-separated credentials** | Ordinary hold authorization, ordinary hold maintenance, R27, audit signing, backup/restore, migration, Keycloak bootstrap/reconciliation, and ordinary runtime capabilities use separate principals and secret mounts. No combined maintenance credential bundle exists. |
| **CI-4 — non-root runtime** | Every long-running application and web container runs with a pinned non-zero UID/GID, dropped capabilities, `no-new-privileges`, and only declared writable paths. |
| **CI-5 — no runtime secret generation race** | Persistent signing and verification keys are generated once before concurrent services start. A service never falls back to an ephemeral key because a shared path is absent or unwritable. |
| **CI-6 — trusted-host boundary** | Docker-group/root-equivalent host administrators remain trusted and explicitly outside container-compromise containment. No documentation claims otherwise. |
| **CI-7 — non-API-writable R27 witness** | The ordinary application role cannot create, alter, or sign accepted R27 attestations. The executor verifies two distinct authorizer signatures over one canonical manifest and rejects unsigned/changed authority rows. |
| **CI-8 — externally enrolled approvers** | Both R27 actors hold the non-API-grantable Keycloak `r27-approver` role and present a fresh required WebAuthn authentication context. API-created users, password-only sessions, ordinary-audience tokens, and forged app role assignments cannot qualify. |
| **CI-9 — one action, one fresh session proof** | Each R27 request, approval, or cancellation consumes one dedicated-audience token from a fresh R27 WebAuthn step-up. Exact issuer, audience, authorized party, subject, session, assurance level, authentication time, token lifetime, and unique token/action nonces are validated and bound to the signed action; replay is rejected. |
| **CI-10 — recovery trust is host-rooted** | The ordinary API cannot install recovery verification keys or witnesses. Production starts with an empty trusted verifier registry, so R27 remains pending until a later approved recovery service and a host-controlled, auditable public-key activation exist. Retired and revoked keys have distinct fail-closed semantics. |
| **OP-1 — visible incomplete work** | Pending, retrying, failed, and verified retention/maintenance work is externally distinguishable. A skipped, timed-out, or partially completed check is not success. |
| **OP-2 — idempotent recovery** | Restarting any maintenance service safely resumes durable work without weakening storage or discarding still-valid signed R27 approvals. A changed target manifest requires new authorization rather than unsafe reuse. |
| **OP-3 — forward-only recovery** | Old images are not restarted against protected state. Physical locks and ownership changes are never described as automatically reversible. |

---

## 4. Service and capability topology

Services may share a hardened image, but each Compose service has a distinct process identity, database
role where required, MinIO/Keycloak principal where required, secret set, network set, and writable mount
set. A shared image is not a shared capability.

| Service | Required capabilities | Explicitly absent |
|---|---|---|
| **`api`** | Application DB role; ordinary non-bypass vault client; scoped Keycloak user-provisioning service account; ordinary Redis client. | DB owner; MinIO root/bypass; legal-hold removal; backup key; audit private key; Keycloak bootstrap admin; privileged broker or service endpoint. |
| **general `worker`** | Application DB role; ordinary vault/mirror access; ordinary Redis tasks; only the mounts needed by its allow-listed jobs. | DB owner; MinIO root/bypass; R27 execution; backup key; audit private key; Keycloak bootstrap admin. |
| **`beat`** | Ordinary Redis scheduling only. | DB owner, application storage credentials, R27, backup, Keycloak, and signing secrets. It schedules no privileged operation. |
| **`retention-maintenance`** | Narrow retention-operation DB role; ordinary non-bypass vault principal; bounded polling schedule. | R27 authorizer/executor keys; MinIO root/bypass; legal-hold removal; backup, audit-signing, or Keycloak authority; Redis. |
| **`hold-release-authorizer` (one-shot)** | Host-invoked narrow authorization DB role; reconstruct and display one canonical hold-release digest; explicit operator confirmation; immutable authorization write. | MinIO, Redis, ingress, R27 authority, application mutation, bulk/wildcard authorization, delete, bypass, or retention authority. |
| **`hold-maintenance`** | Narrow authorized-hold-operation DB role; exact-version lock inspection; legal-hold `OFF` principal constrained to the ordinary WORM domains; bounded polling schedule. | Redis, ingress, DB owner, R27 authority/key, object data read/write, delete, retention write/shortening, Governance bypass, backup, audit-signing, or Keycloak authority. |
| **`r27-authorizer`** | Dedicated-audience Keycloak validation including `r27-approver` and fresh WebAuthn context; `record.dispose` evaluation; narrow R27-authority DB role; R27 attestation private key; canonical manifest and approval endpoints routed directly from Caddy. | Main API network; Redis; MinIO credentials; recovery-generation signer; Keycloak role-mapping administration; backup or audit authority. |
| **`r27-maintenance`** | Narrow R27 database role; exact-version records/documents policy; legal-hold transition and GOVERNANCE-bypass credential; audit result write. | Redis, inbound port, DB owner, backup key, audit signing key, Keycloak admin, arbitrary bucket administration. |
| **recovery verifier key manager (one-shot)** | Host-invoked narrow DB role for audited install/retire/revoke of recovery **public** keys only. | Long-running process, network ingress, recovery private key, MinIO, Redis, application/R27 mutation, or witness signing. |
| **`audit-signer`** | Narrow checkpoint database role; audit private key; its own internal schedule/intent claimant. | Redis, MinIO root/bypass, backup key, Keycloak admin, general application mutation. |
| **`backup-maintenance`** | Bounded database export/restore role, backup encryption key, backup/scratch mounts, scoped Keycloak export identity, typed backup intent/schedule. | R27 bypass, audit private key, general Redis work, MinIO administration unless a later backup design explicitly grants a narrower storage leg. |
| **`migrate`** | Database-owner DSN for the bounded migration process. | All S3, Keycloak, audit, backup, ordinary application, and Redis secrets. |
| **Keycloak bootstrap/init** | Bootstrap administrator secret only for initial dependency bootstrap. | Application, storage, R27, audit, and backup credentials. The bootstrap secret is not a steady-state API credential. |
| **`keycloak-reconcile` (one-shot)** | Bootstrap administrator plus the exact generated ordinary-provisioning, backup-export, and R27-role-manager client secrets for fixed realm/client/role/flow reconciliation and bounded positive/negative probes. | Application DB/storage, R27 attestation or execution, audit, backup encryption, Redis, runtime ingress, or arbitrary operator-supplied realm/client/role/flow targets. |
| **MinIO bootstrap/init** | MinIO root and policy/user-provisioning authority. | Application database, Keycloak, audit, and backup credentials. Root is never copied into an application service. |
| **`web`** | Static assets and unprivileged HTTP listener. | Application secrets, Node development runtime, database/storage credentials, writable application state. |

### 4.1 Ordinary vault policy

The ordinary vault principal is scoped to the required documents, records, rendition, and staging
resources. It may perform the exact Get/Head/Put/Copy/List/version operations required by current flows,
read lock state, apply or extend GOVERNANCE retention, and set a legal hold only to `ON`. It has no MinIO
administrative action, no `s3:BypassGovernanceRetention`, and no ability to set a legal hold to `OFF`.

The implementation must prove the effective MinIO policy, not only compare JSON text. If the selected
MinIO release cannot enforce the `s3:object-lock-legal-hold = ON` condition on
`s3:PutObjectLegalHold`, implementation stops for owner review; an unconditioned grant to an ordinary
principal is forbidden. The design relies on MinIO's documented condition-key support for that action,
then treats the live pinned-version denial proof as authority over prose documentation:
[MinIO policy action reference](https://min.io/docs/minio/kubernetes/eks/administration/identity-access-management/policy-based-access-control.html).

### 4.1.1 Ordinary hold-release vault policy

The `hold-maintenance` MinIO principal is smaller than the ordinary vault principal. It may inspect legal
hold state and request `OFF` only for the exact VersionId of a host-authorized ordinary release. It cannot
read or write object bytes, list objects or versions, set hold `ON`, read or change retention, delete any
object, or pass `s3:BypassGovernanceRetention`. The service still revalidates authorization and every live
owner under lock because IAM cannot prove the domain release contract.

The implementation must prove the pinned MinIO release can enforce the OFF-only condition with live allow
and denial tests. If it cannot, implementation stops for owner review; it does not grant an unconditional
`s3:PutObjectLegalHold`, borrow the R27 principal, or add bypass/delete capability.

### 4.2 R27 vault policy

The R27 principal is restricted to exact-version lock inspection, legal-hold transition, and deletion in
the WORM domains that R27 can lawfully affect. It does not receive MinIO administration or unrelated
bucket access. IAM cannot prove the business request, so the service must still revalidate the complete
R27 authority chain before every physical action.

### 4.3 Database handoff

Each maintenance loop uses PostgreSQL row claiming with deterministic ordering, transaction-scoped locks,
bounded batches, and `FOR UPDATE SKIP LOCKED` or an equivalently proven single-claim mechanism. The API
cannot provide a module name, shell string, bucket, or object key as an arbitrary command. Target
coordinates are resolved from authoritative rows and rechecked against the requesting organization.

For ordinary hold release, the application role may insert a schema-validated
`PENDING_AUTHORIZATION` intent and read its safe status, but cannot write authorization, claim work, or
record physical results. The host-invoked `hold-release-authorizer` role alone can write immutable
authorization bound to the reconstructed canonical operation digest and advance it to `AUTHORIZED`.
`hold-maintenance` alone can claim that authorization, revalidate all owners, and record the exact
verified `OFF` result. Those roles cannot assume one another, and neither can read or mutate R27 authority.

The ordinary application role becomes read-only for R27 request/approval attestations, signed manifests,
replacement-generation witnesses, R27 final disposition events, and privileged purge markers. The
`r27-authorizer` alone writes accepted human authority; `r27-maintenance` alone writes final execution
state. Their roles cannot assume one another. Authorizer signatures bind all security-relevant fields, so
inserting or changing a row with the ordinary application credential cannot create valid authority.

The application role may insert a complete newly asserted WORM Blob as part of the protected owner
transaction, but it cannot update or delete an asserted WORM Blob. Database column grants and triggers
reserve monotone retain-until/verification updates to `retention-maintenance`, and reserve R27 result and
purge state to `r27-maintenance`. Ordinary verified hold-release state is reserved to `hold-maintenance`;
no maintenance role may rewrite physical coordinates or content identity.
The comprehensive later `S-db-grants` slice remains deferred, but these WORM and R27 grants are part of
this atomic boundary and cannot be deferred with it.

This slice adds and revokes every role/grant needed to make R27 authority non-forgeable by the API and to
isolate the named services. The later `S-db-grants` slice still owns the comprehensive table-by-table
application privilege redesign outside these exact boundaries.

### 4.4 R27 OIDC, step-up, and replay boundary

Keycloak provisions a public OIDC client with the exact client ID and audience
`easysynq-r27-authorizer`. Only Authorization Code with PKCE S256 is enabled. Implicit flow, Direct Access
Grants, device flow, and service accounts are disabled. Redirect and post-logout URIs are exact configured
R27 browser callback/origin values rather than wildcards. An explicit audience mapper places
`easysynq-r27-authorizer` in the access token `aud`; protocol mappers make `auth_time` and `sid` available
to the authorizer. The normal web/API client does not receive this audience.

The dedicated browser flow maps `urn:easysynq:acr:r27-webauthn` to Keycloak LoA 2, makes that ACR the
client minimum, and requires the WebAuthn Authenticator at LoA 2 with user verification. The LoA condition
uses `Max Age = 0`. Every signing or cancellation authorization request also sends `max_age=0` and an
essential `acr` claim for that exact value. The authorizer accepts an action token only when all of these
checks pass:

- RS256 signature under the current realm JWK selected by `kid`; exact configured realm `iss`; `exp` is
  after receipt, `nbf` when present is not later than 30 seconds after receipt, `iat` is not later than 30
  seconds after receipt, and configured client token lifetime plus observed `exp - iat` are at most 120
  seconds;
- `aud` contains, and `azp` equals, `easysynq-r27-authorizer`;
- non-empty `sub`, required `sid`, and the host-managed realm role `r27-approver`;
- exact `acr=urn:easysynq:acr:r27-webauthn` and `auth_time` within 120 seconds before or 30 seconds after
  receipt; and
- non-empty `jti` not previously consumed by any R27 action.

`amr`, when Keycloak emits it, is recorded in the attestation but is not accepted as a substitute for the
exact ACR; the design does not infer passkey use from a provider-dependent `amr` string. Missing any
required claim is denial. The authorizer binds `iss`, `sub`, `sid`, `jti`, `aud`, `azp`, `acr`, `auth_time`,
the mapped application user, role/permission results, manifest digest, and action nonce into its signature.
A unique database constraint on `(iss, jti)` makes each action token single-use. Viewing the canonical
manifest does not consume it; the state-changing POST consumes it atomically with the signed transition.

The server generates a cryptographically random manifest nonce with a database uniqueness constraint and
a distinct one-time action nonce for each request, approval, or cancellation. The requester and approver
must use separate fresh step-up tokens and distinct `sub` values. A request nonce can never be attached to
another Record or regenerated request. Cancellation also goes directly through the authorizer, requires a
fresh qualifying token plus current `record.dispose`, consumes its token/action nonce, and is allowed only
before finalization is claimed. Under row lock, the executor accepts only an unexpired, non-cancelled,
unexecuted request in its exact ready state and atomically claims it; an idempotent retry may resume only
that same execution identity.

The dedicated token is acquired by the static web application through this separate PKCE flow and sent
only to the Caddy-routed authorizer path. The normal API bearer token is rejected by the authorizer, and
the authorizer token is rejected by the main API. The authorizer returns the canonical manifest it signs
so the user does not approve details rendered only from a potentially compromised API response.

The runtime API provisioning service account can create/manage only its approved ordinary user fields and
is explicitly denied realm-role mapping, authorizer-client administration, and authentication-flow
administration. A host-only audited command enrolls or removes an existing Keycloak subject from the R27
role; it never handles the user's passkey private material. This configuration follows Keycloak's
documented step-up/ACR model, while release acceptance is the pinned-realm live token and denial proof:
[Keycloak step-up authentication](https://www.keycloak.org/docs/latest/server_admin/index.html#_step-up-flow).

### 4.5 Host boundary

The Docker host, its root-equivalent administrators, and the appliance account that controls Docker remain
trusted. Container identity prevents an ordinary application compromise from inheriting unrelated
capabilities; it does not claim to protect secrets from a hostile root operator.

---

## 5. Schema and exact-version contract

### 5.1 Migration `0089`

Immediately before authoring the migration, run:

```bash
cd apps/api && uv run alembic heads
```

If the result is no longer exactly `0088_bootstrap_credential`, stop and rebase this design's revision
number before writing the migration. Filename order and this prose are not migration authority.

The upgrade is valid only when no legacy `blob` owner state exists. It checks before altering the
contract and exits with an operator-safe clean-deployment error if it finds a `blob`, pending blob purge,
or another physical-owner row that would require target-version inference. It does not select “latest,”
enumerate versions, or bind a row based only on key and digest.

The final clean-install schema includes the following concepts; the implementation plan owns exact SQL
types, bounded lengths, names, indexes, and constraint syntax while preserving these semantics.

### 5.2 `blob` physical identity and WORM assertion

`blob` is a generic content-addressed object locator today: it represents WORM source/evidence objects and
non-WORM rebuildable renditions. The migration preserves that distinction instead of forcing every Blob
into GOVERNANCE.

Each WORM Blob stores:

- authoritative target `object_version_id`, non-empty and non-null for committed WORM state;
- bucket and object key;
- enforced mode, constrained to `GOVERNANCE` when `worm_locked=true`;
- latest asserted and read-back-verified retain-until timestamps;
- latest assertion and verification timestamps;
- verified legal-hold state and its verification timestamp; and
- a uniqueness constraint on non-null `(bucket, object_key, object_version_id)`.

Conditional constraints require the version and complete assertion tuple for `worm_locked=true` and
forbid a false GOVERNANCE assertion. Existing deliberately non-WORM rendition/portfolio/diff Blob rows
remain `worm_locked=false`; they do not acquire retention merely because they share the table. Their
version field may remain null when their non-versioned bucket cannot supply one, and their exact cleanup
contract remains separate from WORM source identity.

The current SHA-256 identity remains the content address. Version identity is an additional physical
coordinate, not a replacement digest. A WORM Blob cannot be marked verified from a bucket default or a
key-only response.

Once `worm_locked=true` or a WORM owner references the row, database enforcement makes the Blob's primary
content digest, organization boundary, bucket, object key, `object_version_id`, WORM flag, enforced mode,
and initial promotion assertion immutable. No runtime role, including either maintenance role, can update
those columns; changing physical identity means inserting a newly asserted Blob and following an explicit
domain transition. Triggers reject post-assertion mutation even if an application query bypasses its ORM.
The ordinary application role also lacks `DELETE` on asserted WORM rows. The bounded migration owner can
change schema but is absent after migration; its theoretical database-owner authority remains inside the
trusted-host boundary and is never treated as runtime authorization.

Mutable assertion state is narrowly separated from immutable identity. `retention-maintenance` can only
ratchet retain-until and corresponding verification fields forward and can record verified hold `ON`;
constraints reject a shorter date or an `OFF` transition. `hold-maintenance` can record verified hold
`OFF` only for its claimed, host-authorized ordinary operation after the owner recheck; it cannot record
permanent release or a purge. `r27-maintenance` can record a verified permanent hold release and exact
purge result only for its already claimed signed request. Neither can alter the target coordinates,
digest, mode, or original assertion. Historical rows and purge evidence remain rather than deleting or
repointing a Blob after physical disposition.

### 5.3 owner obligations

Document versions persist the policy identity resolved at check-in and the check-in retention basis date.
When their document type has no policy, they bind to the then-current installation document minimum. A
later document-type default replacement affects future versions; a lengthening of an already pinned
policy or installation minimum creates physical extension work for existing owners.

Records keep their existing pinned `retention_policy_id` and `retention_basis_date`. The effective WORM
period is `worm_lock_period` when configured, otherwise `duration`. An unresolved `event:*` basis uses
capture date provisionally; recording the real event recomputes the deadline and may only extend it.

`PERMANENT`, an active domain legal hold, or another indefinite surviving obligation requires physical
legal hold. The physical hold remains enabled while any owner requires it.

### 5.4 authoritative WORM owner registry

The retention helper uses one exhaustive, tested WORM owner registry rather than the broader generic Blob
liveness query.

| Pointer family | WORM owner rule | Obligation source |
|---|---|---|
| `document_version.source_blob_sha256` | Every immutable Document version remains an owner; lifecycle supersession does not erase its controlled source. | Version-pinned document-type policy or installation minimum, anchored at check-in. |
| `evidence_blob.blob_sha256 -> record` | Owner while the Record has no destructive `DESTROY`/R27 disposition event. `ARCHIVE_COLD` and `TRANSFER` remain owners. | Record-pinned retention policy/basis plus Record legal hold. |
| sealed Evidence Pack ZIP registered as permanent Record evidence | Covered by the `EvidenceBlob` leg; the live pack pointer is additionally checked as a defensive owner until the R27 source transaction invalidates it. | Permanent pack Record policy/hold. |

The following remain generic **non-WORM** liveness owners and are excluded from retention aggregation:
`document_version.rendition_blob_sha256`, `record.structured_pdf_blob_sha256`,
`evidence_pack.portfolio_blob_sha256`, mirror-generated renditions, and visual-diff caches. R27 still
clears and cleans its affected derived pointers under R27's existing derivative contract; it does not
mislabel those rebuildable objects as WORM.

Every producer/consumer inventory in the implementation plan must classify its Blob pointer through this
closed registry. Adding another WORM owner family later requires extending the registry and its max-owner,
hold, disposition-race, and exact-version tests.

The WORM legs of that registry are append-only references: after insert,
`document_version.source_blob_sha256` and `evidence_blob.blob_sha256` cannot be updated or deleted by the
ordinary application role. A lifecycle/disposition event changes whether the immutable association is a
live owner; it does not repoint historical evidence. Database triggers enforce the pointer rule in
addition to ORM/domain checks. The defensive live pack pointer remains mutable only through the pack/R27
state machines and cannot override the permanent EvidenceBlob owner leg.

### 5.5 retention operations

A durable retention-extension operation stores a proposed revision separately from the active policy:

- organization and policy/config authority;
- active and proposed logical values;
- initiating actor and audit identity;
- state (`PENDING`, `RUNNING`, `FAILED`, `VERIFIED`, plus a narrowly justified cancellation state only
  before any physical extension);
- bounded attempt/progress/error metadata; and
- immutable created/completed timestamps.

Its target rows bind blob content identity, exact object version, required retain-until/hold state, and
per-target state. Creating the proposed revision and target manifest commits together, but the active
policy row remains at the prior verified value. New captures compute the maximum of active and pending
values, so they receive the requested longer protection immediately. Existing owners remain truthfully
governed by the old active floor until their exact versions verify. After every target verifies, one
transaction promotes the proposed values to active and completes the operation.

The processor always recomputes current owners under lock before acting; the stored target is a required
floor and work ledger, not permission to ignore a later owner. A failed operation leaves the proposed
revision visible and incomplete without claiming the longer value is active for unsynchronized existing
owners. There is no cancellation after physical work begins; resolution is forward-only.

Only one active extension operates on a given policy or installation minimum. Another requested
lengthening is serialized until the active operation completes. A shortening is rejected, never queued.

### 5.6 legal-hold control

Permanent/domain-hold owner creation sets legal hold `ON` synchronously with the ordinary vault principal,
then reads it back before committing the owner. MinIO IAM conditions make `OFF` impossible for that
principal; a live negative test is release-blocking. A failure leaves no active owner and requires an
idempotent retry through the existing create/check-in/import operation rather than a new half-visible
Document or Record state.

Ordinary hold removal is asynchronous and typed. An authorized API request creates a closed
`HOLD_RELEASE_V1` operation in `PENDING_AUTHORIZATION`; its canonical bytes/digest bind organization,
Record, immutable Blob digest and exact VersionId, initiating application actor, normalized release basis,
owner-snapshot digest, issue time, and idempotency identity. The request audit and operation are durable,
but neither row authorizes physical `OFF`.

The supported host command accepts one operation UUID, starts the DB-only `hold-release-authorizer`,
reconstructs the canonical bytes from authoritative rows, verifies the original authority and current
logical release, displays a bounded summary, refuses permanent/cross-organization/stale operations, and
requires explicit confirmation. Its immutable authorization binds the exact digest and advances only the
unchanged row to `AUTHORIZED`; there is no browser/API authorization route or bulk/wildcard mode.

`hold-maintenance` claims only `AUTHORIZED` work, rechecks every current owner under the physical lock,
and sets legal hold `OFF` for that exact VersionId only when no surviving owner still requires it. It reads
the result back before recording `VERIFIED`. A changed target, stale authorization, permanent owner, or
surviving hold requirement fails closed. Its principal cannot delete, bypass Governance, change retention,
or consume R27 authority; R27 cannot consume an ordinary hold authorization.

### 5.7 R27 attestations, recovery witness, and exact markers

The R27 authorizer stores a canonical manifest and two separately signed attestations. The signed bytes
bind at least: schema/key version, unique manifest/action nonces, organization, Record, legal-basis digest,
requester, approver, immutable Blob SHA-256 plus exact WORM `(bucket,key,VersionId)` targets, derivative
pack/artifact inventory and excluded-set digest, issued/expiry times, the complete accepted OIDC claim set
from §4.4, and the expected pre-execution state. Requester and approver are distinct Keycloak subjects and
application users. Any changed field invalidates verification.

The canonical manifest expires after `R27_MANIFEST_TTL_SECONDS`, which defaults to 86,400 seconds and is
validated to 300–604,800 seconds. This is independent of the at-most-120-second OIDC action-token lifetime.
Expiry is rechecked before every state transition and external action. A manifest that expires while
waiting for recovery generation becomes stale and requires a new canonical manifest plus two fresh human
authorizations; the prior manifest is never extended, refreshed, or reused.

The ordinary application role has read-only access to accepted R27 authority rows and no access to the
authorizer private key. `r27-maintenance` receives only the pinned public key. Key rotation preserves the
verification keys required by every still-pending attestation.

The request state machine is closed and row-locked:

`WAITING_FOR_SECOND_APPROVER -> WAITING_FOR_RECOVERY_GENERATION -> READY_FOR_FINALIZATION -> FINALIZING -> EXECUTED`.

`CANCELLED` is reachable only from a waiting or ready state through the fresh authorizer flow; `STALE`
records a manifest/authority mismatch that requires a new request; `FAILED` records a bounded retryable or terminal
execution result without conferring authority. The second attestation makes the first waiting transition.
An accepted recovery witness makes the ready transition. The executor atomically changes only that ready,
unexpired, unexecuted, non-cancelled row to `FINALIZING`, persists one execution identity, and resumes only
that identity after a crash. Neither a second executor nor cancellation can cross that claim.

This migration creates a versioned `recovery_generation_verifier_key` trust registry and this slice ships
the host-only audited `./scripts/easysynq recovery-verifier install|retire|revoke` public-key command, but
installs **no production key**. The command accepts only an Ed25519 public key plus declared key
ID/validity, verifies and displays its fingerprint before an explicit operator confirmation, and invokes
a bounded one-shot key-manager process whose credential no long-running container receives. The API,
authorizer, and all ordinary workers have no write access; `r27-maintenance` has read-only access. Each key
record binds a unique key ID, algorithm, public key and fingerprint, `not_before`, optional `retired_at`,
optional `revoked_at`, installing actor, and immutable audit reference. Referenced keys cannot be deleted.

The future recovery-generation installer generates the production Ed25519 pair in a bounded one-shot,
writes its private key only to the recovery signer's dedicated secret, and presents only the public key to
that host command for activation. The private key never enters this slice's services, database, Compose
environment, or command arguments. New witnesses are accepted only when their signature verifies under a
key active at `issued_at`. Retirement stops new witnesses but keeps already-bound pending witnesses verifiable.
Revocation invalidates every unexecuted witness for that key, including one created before revocation, and
returns affected requests to `WAITING_FOR_RECOVERY_GENERATION`; they require a newly verified generation
and witness. Key status is rechecked at finalization, not cached at intake.

A recovery witness binds its schema/key ID, unique witness nonce, R27 request and manifest digest,
replacement generation ID, exact excluded target/derivative set digest, `VERIFIED` result, verification
completion time, generation identity, and signature. Database constraints make `(key_id, witness_nonce)`
and `(manifest_digest, generation_id)` unique and bind at most one accepted witness to one R27 request. The
verification must finish after the manifest's second authorization and must cover exactly its exclusion
digest. On the ready-to-finalizing claim, the witness is consumed once by that request/execution identity;
an idempotent retry of the same execution may reuse the persisted verification result, but no other request
may use it. The executor rechecks signature, key status, generation state, timestamps, uniqueness, binding,
and unconsumed/open request state under lock.

Until a production key and future recovery service exist and a valid witness passes that contract, R27
stays `WAITING_FOR_RECOVERY_GENERATION`. A database boolean or row written by the API cannot satisfy it.
The integration harness uses a strictly test-only mechanism: the test process creates an ephemeral
Ed25519 pair, inserts only its public key through an isolated database-owner fixture, and passes the private
key directly to the test signer in memory. No Compose secret, runtime setting, image path, or production
endpoint accepts that private key; startup rejects the test fixture mode outside the isolated test profile.

`pending_blob_purge` gains the exact target `object_version_id` and retains its existing immutable
Record, disposition event, destroy request, actors, legal basis, organization, and bypass semantics.
The marker never authorizes all versions at a key. Pre-version or otherwise ambiguous markers are refused;
this clean-deployment slice does not convert them.

### 5.8 GOVERNANCE configuration

Setup no longer offers COMPLIANCE. Persisted storage configuration is either reduced to an enforced
GOVERNANCE constant or constrained so no other value can be committed. Setup/readiness reads back the
actual bucket configuration and refuses a mismatch. An existing recorded COMPLIANCE value stops with an
operator instruction to inspect the real storage state; it is never automatically downgraded or relabelled.

The installation-wide document WORM minimum is a validated duration, seeded for development from a
placeholder-backed configuration value rather than a site-specific literal. Once protected owners exist,
it follows the same extend-forward rule as a retention policy.

---

## 6. Retention calculation and physical ordering

One shared retention module in `services/vault/` owns the calculation/application boundary for controlled
Document sources, Record WORM evidence, permanent generated artifacts, policy extensions, event-anchor
updates, legal holds, and R27 preflight. Callers do not reproduce MinIO retention math or reuse the broader
non-WORM Blob-liveness query as a retention oracle.

For a proposed owner of exact version `V`:

1. acquire the organization pack lock when the operation can intersect a pack build (shared for build,
   exclusive for R27), then sorted physical-object advisory locks;
2. perform or adopt the exact-version target copy while that physical lock is held;
3. resolve every live WORM owner of `V` from the closed registry plus the proposed owner;
4. compute each finite obligation from its pinned policy/configuration and basis;
5. set `required_until = max(current_verified_storage_until, every_owner_until)`;
6. set `required_hold = any(owner requires indefinite/domain hold)`;
7. read retention and legal-hold state for **that VersionId**;
8. apply only a later GOVERNANCE date and/or hold `ON` through the ordinary principal;
9. read both values back for **that VersionId**;
10. refuse a mode, date, hold, version, or identity mismatch; and
11. only then persist verified assertion state and activate the owner.

If the copy/lock succeeds and the database transaction fails, storage may contain an unowned, over-retained
exact version. That is a safe, auditable orphan/reconciliation case. Reordering the database commit before
verified physical protection is forbidden. Every producer uses the same lock order; an implementation
cannot copy first and acquire serialization later.

### 6.1 document policy precedence

At check-in, a document version pins its document type's configured default policy. If none exists, it
uses the installation-wide document WORM minimum. The basis is check-in date. A later Record attachment
adds its Record obligation; the exact shared version takes the maximum. Cross-domain dedup refusals remain
unchanged.

### 6.2 Record and event policy

The Record uses its existing pinned policy. If its basis is known, physical retention anchors there. If an
`event:*` basis is not yet known, capture date supplies the provisional anchor. When the real event arrives,
the service computes a new floor and queues/executes an extension before reporting the event transition as
fully synchronized. It never shortens a lock even if the real date would calculate earlier.

### 6.3 shared owners and holds

The owner set is recalculated under the physical-object lock for create, dedup, policy extension, hold
change, and disposition. Removing one owner never weakens another owner's retention. A physical legal hold
is removed only when no surviving owner requires it and a valid authority permits the transition.

---

## 7. Workflow contracts

### 7.1 new document/Record owner

1. The existing staged-identity boundary verifies and promotes exact source bytes.
2. Promotion returns target `VersionId`; the caller treats a missing value as fatal.
3. The shared retention boundary computes the owner-set maximum and applies finite retention.
4. If an indefinite/domain hold is required, the ordinary principal sets hold `ON`; policy prevents `OFF`.
5. Retention and hold state read back successfully for the exact version.
6. The owner and Blob assertion become active together in the caller's transaction.

A storage timeout, missing version, unreadable lock state, or mismatch produces no active owner and no
success audit. Retriable pending state preserves the exact operation and target; it never falls back to a
key-only request.

### 7.2 policy/configuration extension

The management transaction requires `retention.manage` at SYSTEM scope, validates a strict lengthening,
writes the proposed revision and immutable audit, creates the operation and target manifest, and returns a
typed pending result. It does **not** replace the active policy values yet. `retention.read` remains the
read authority. New data uses `max(active, proposed)` immediately.

`retention-maintenance` claims bounded targets, reacquires the physical lock, recomputes live owners,
extends and reads back the exact version, and records target progress. Partial failure leaves the old
active policy plus the visible longer proposed revision, preserves completed over-locks, and exposes
remaining work. Retry is idempotent and uses bounded backoff. After every current target verifies, one
transaction promotes the proposal to active and marks the operation `VERIFIED`.

### 7.3 ordinary domain hold release

The main API checks `record.hold.manage`, resolves the exact asserted WORM target, canonicalizes
`HOLD_RELEASE_V1`, appends the immutable request audit, and returns `202 PENDING_AUTHORIZATION`. It cannot
authorize, claim, or complete the operation. The browser displays safe state and never offers an authorize
control.

The host invokes `./scripts/easysynq hold-release authorize --operation-id UUID`. That bounded wrapper
passes only the dedicated DB secret to the one-shot authorizer, supplies the trusted host-operator
identity, and requires an exact confirmation. Non-interactive use additionally supplies the expected
digest. The one-shot rebuilds and verifies the operation before recording immutable authorization; it has
no MinIO credential.

`hold-maintenance` atomically claims only the resulting `AUTHORIZED` row. Under the same sorted physical
locking discipline as other WORM changes, it reloads the immutable target and complete owner registry,
rejects any permanent or surviving hold obligation, uses the OFF-only principal for one exact VersionId,
and verifies the physical result. On ambiguity or failure it records a bounded `FAILED` state and retries
only after all authorization and owner checks pass again; it never deletes, bypasses, or broadens the
target.

### 7.4 R27 authorization, recovery gate, and purge

The main API may display read-only R27 state but cannot request, approve, sign, execute, or directly mutate
accepted R27 authority. The static browser obtains the separate authorizer token and sends request/approval
actions directly to `r27-authorizer`. The authorizer independently enforces `record.dispose`, dedicated
R27 approver enrollment, the exact §4.4 token/step-up/replay contract, two distinct identities, legal-basis
validation, canonical target/derivative display, expiry, and exact signed-manifest creation. It signs the
immutable Blob digest together with every physical coordinate. It acquires the organization-exclusive
pack/R27 lock while snapshotting the manifest so the initial target set is coherent; the lock is reacquired
at finalization, and intervening changes make the signature stale.

The browser supplies the Record, legal basis, and action nonce, never a bucket, key, VersionId, digest, or
derivative inventory. The authorizer resolves those values through its read-only owner registry view and
refuses any incomplete/unverified WORM assertion; client-supplied target fields are schema errors rather
than overrides. Its lack of MinIO credentials is intentional: database immutability protects this
snapshot, while the executor performs the independent exact-byte proof immediately before destructive use.

After the second signature, the request enters `WAITING_FOR_RECOVERY_GENERATION`. It is not yet a completed
disposition, and no pack is invalidated or object deleted. The later recovery-generation service must build
and verify a replacement that excludes the signed targets and derivatives, then issue its own signed
witness over the manifest digest. Until that capability lands, production requests remain visibly pending.

When a valid recovery witness exists, `r27-maintenance`:

1. atomically claims only an open `READY_FOR_FINALIZATION` request and verifies it is unexpired,
   unexecuted, non-cancelled, and bound to the same one-time witness/execution identity;
2. verifies both authorizer signatures, distinct subjects, complete OIDC claim bindings, expiry, and key
   versions;
3. verifies the recovery-generation signature, active/non-revoked key, manifest digest, excluded set,
   generation state/timestamps, uniqueness, and verification result;
4. acquires the organization-scoped **exclusive** pack/R27 lock, while every pack seal uses its shared
   side;
5. recomputes the exact WORM owner and derivative inventory and refuses a stale signed manifest;
6. under sorted physical locks, streams every signed exact `VersionId`, hashes its bytes, and compares the
   result with both the signed digest and immutable Blob SHA-256; a missing/mismatched target records a
   fail-closed result before disposition, hold release, bypass, or purge-marker creation;
7. in one source transaction, records the immutable R27 disposition, marks every affected pack
   `UNAVAILABLE`, revokes live shares, clears ZIP/portfolio pointers, creates one-hop derivative
   tombstones, and writes exact-version purge markers;
8. commits that authority/state before attempting external deletion; and
9. claims each marker under the sorted physical lock, revalidates the complete signed chain, witness/key
   status, immutable coordinates, surviving owners, and exact-version SHA-256 immediately before any
   destructive call, then removes a required legal hold, explicitly bypasses GOVERNANCE for that
   VersionId, and verifies only that version is absent.

If a surviving owner still requires shared bytes, the targeted logical owner is disposed while physical
bytes remain and the immutable result identifies the blocker. If the manifest becomes stale while waiting
for recovery, the operation requires a new canonical authorization rather than silently expanding a legal
order.

Crash recovery repeats all signature, generation, lock, authority, and owner checks. A stored bypass
boolean, service credential, database actor ID, or prior attempt is never enough. A failed operation
remains pending/failed with bounded diagnostics and an operational alert; it is not silently converted to
success.

### 7.5 audit signing

The audit signer owns the persistent private key and its database-backed schedule/state. API and general
workers may verify with the public key but cannot sign. Key material is generated once during installation,
mounted read-only, and never synthesized ephemerally because a path is missing.

### 7.6 backup/restore

Backup and restore run through their purpose-specific service or bounded one-shot profile. Manual requests
become typed database intents; scheduled work is owned by that service rather than ordinary Beat. No
user-controlled shell string or module path is executed. This identity separation does not claim to close
the later backup-content, destination, restore-target, or recovery-generation slices.

---

## 8. Errors, observability, and reconciliation

The public/admin contract distinguishes at least:

- accepted and pending physical synchronization;
- ordinary hold release pending host authorization, authorized, running, failed, cancelled before start,
  and physically verified;
- signed R27 authority waiting for a verified replacement recovery generation;
- retrying after a transient storage or dependency failure;
- terminal failure requiring operator action;
- missing or mismatched exact version;
- physical retention/hold read-back mismatch;
- unsupported legacy populated deployment;
- recorded/actual WORM mode mismatch;
- surviving owner prevented physical R27 erasure; and
- credential/policy denial that proves a service lacks the required capability.

Diagnostics expose safe identifiers and bounded error classes, not credentials, site names, arbitrary
provider bodies, or object-store secrets. Metrics/health include last successful claim, oldest pending age,
pending/retrying/failed counts, and service-specific readiness. A privileged service is not ready merely
because its process is alive; it must prove its database role and required storage action without performing
a destructive probe.

Reconciliation scans durable pending operations and verified WORM Blob assertions. It never chooses the
latest version for an ambiguous WORM key. An unowned over-retained exact version may be reported for later
reviewed cleanup; it is never weakened automatically. An R27 request lacking a valid authorizer or
recovery-generation signature remains pending/failed even if matching mutable database rows exist.

---

## 9. Secrets, images, and Compose

### 9.1 secret delivery

The installer generates role-specific secret files before Compose starts. Application configuration gains
consistent `*_FILE` support with validation that rejects an ambiguous simultaneous inline and file value.
Compose mounts declared secrets read-only into only the services that consume them. The common `.env`
contains non-secret configuration and is not mounted wholesale into runtime services.

Required splits include:

- MinIO root/bootstrap versus ordinary ON-only vault versus ordinary OFF-only hold maintenance versus R27;
- database owner versus application versus hold authorizer, hold maintenance, and every other narrowly
  required maintenance role;
- Keycloak bootstrap administrator/reconciler versus scoped provisioning, export, and R27-role-manager
  accounts;
- dedicated-audience R27 authorizer OIDC client and its attestation private/public key split;
- empty recovery-generation verifier registry plus host-only one-shot key-manager credential; a future
  recovery installer activates only its public key, while neither a production nor test private key is
  mounted into this slice's runtime services;
- audit private key versus public verification key;
- backup encryption key; and
- verification-token key material generated once before concurrent startup.

Local development secrets remain untracked and must not appear in logs, rendered evidence, screenshots,
fixtures, documentation, or Git.

### 9.2 non-root images

API and web images declare pinned numeric non-zero UID/GID values. The web becomes a multi-stage build with
a pinned minimal static runtime listening on an unprivileged port; `vite preview` and development
dependencies are absent from the final image.

Long-running application containers use:

- `USER` in the image plus explicit compatible Compose identity where needed;
- `cap_drop: [ALL]` and `no-new-privileges`;
- read-only root filesystems;
- tmpfs or exact writable mounts for bounded runtime paths; and
- no Docker socket.

Caddy is the only shared ingress. It routes ordinary `/api` traffic to the main API and a distinct R27
authority path to `r27-authorizer` across separate backend networks. The main API does not join the
authorizer backend network, and the executor has no ingress route. Browser tests prove that the dedicated
authorizer token is never attached to ordinary API requests.

Fresh named volumes/directories are initialized with their final ownership. This slice does not recursively
rewrite populated legacy volumes. A bounded root init job is allowed only when its exact empty/fresh target,
symlink/reparse behavior, ownership, and resulting permissions are tested.

### 9.3 profile and appliance parity

The identity, secret, network, and mount contract must render and boot through:

- development profile S;
- development profile M;
- production profile S;
- production profile M; and
- appliance base + S + air-gap + production overlays.

This pair owns only the target-image/profile mechanics necessary for its clean deployment and walkthrough.
It does not claim full build identity, offline bundle completeness, four-leg deploy/rollback, or production
upgrade eligibility assigned to later programme slices.

### 9.4 installation flow

Installation performs, in order:

1. validate clean-deployment eligibility and stop on legacy physical-owner state;
2. generate secret/key material without printing it;
3. initialize fresh volume ownership;
4. start PostgreSQL, MinIO, Redis, and Keycloak dependencies;
5. provision MinIO users/policies, then run the bounded post-ready Keycloak reconciler to install the
   generated scoped-client secrets, identities, R27 client/role, and authentication flow, leaving
   `r27-approver` with no automatic runtime-API membership;
6. run target migration `0089` with only the DB-owner secret;
7. start purpose-separated non-root runtime services;
8. run readiness/doctor checks that verify actual policy, identity, and secret absence; and
9. seed only synthetic development data when the explicit development command is requested.

---

## 10. Compatibility and recovery boundary

### 10.1 clean deployment only

The supported path for this pre-release slice is a new PostgreSQL/MinIO deployment. `0089` remains a valid
linear migration and may upgrade an `0088` schema only when the physical-owner tables are empty. It does not
claim an in-place migration of documents, Records, object versions, pending purges, root-owned application
volumes, or historical credentials.

The implementation/walkthrough may reset this development deployment only after enumerating the exact
EasySynQ containers, networks, and volumes and revalidating that the user-authorized disposable target is
still correct. No installer or ordinary upgrade command deletes data implicitly.

### 10.2 rollback

There is no supported in-place rollback to a pre-split image after protected data exists:

- physical retention extensions and legal holds are not reversed;
- old images are not given the new secrets or started against `0089` state;
- a code failure is repaired forward; and
- disaster recovery restores into a fresh/cleared, versioned WORM target under R37.

An Alembic downgrade is a test/development schema operation only and refuses to discard populated exact-
version, retention-operation, or authority state. It is not an operator rollback procedure.

### 10.3 deliberately unclosed work

This slice does not close or claim:

- the comprehensive `S-db-grants` privilege redesign;
- populated deployment upgrade, migration lock timeout, or writer-quiescence residuals;
- complete backup legs, destination durability, restore-target inventory, or recovery-generation
  production; this slice defines and enforces only the signed fail-closed prerequisite interface;
- build identity, offline install completeness, or deploy/rollback four-leg proof;
- protection from a malicious Docker/root host operator; or
- customer production certification from a synthetic development walkthrough.

### 10.4 atomic delivery with review checkpoints

The merged execution-order authority requires one implementation PR and one release verdict. To keep that
large atomic boundary reviewable, work proceeds in ordered commits/checkpoints inside one draft PR:

1. `0089`, conditional Blob assertion, closed owner registry, staged policy-revision contract, and
   migration refusal tests;
2. exact-version retention/hold engine across every WORM producer and consumer;
3. R27 authorizer signatures, read-only main-API boundary, recovery-generation gate, pack-lock protocol,
   and isolated executor;
4. purpose-specific principals/secrets, non-root images, static web runtime, and every Compose/appliance
   shape; and
5. integrated contracts, complete verification, live walkthrough, and documentation truth.

Each checkpoint receives its focused review/evidence, but none is merged, released, or described as
shipped independently. The final integration owner rechecks the combined diff and all cross-checkpoint
invariants. A feature flag or disabled service may keep an incomplete branch safe during review; the final
tree contains no configuration that can ship only retention or only credential separation.

---

## 11. RED-first acceptance

Production work begins only after focused tests demonstrate current failure for the intended reason. Each
load-bearing predicate has a safe mutation or equivalent negative control.

| Proof | Expected against design baseline | Required falsifier / assertion |
|---|---|---|
| **Exact target identity** | **RED** | Promotion result is persisted as `Blob.object_version_id`; removing that assignment or resolving latest-by-key fails reads/recovery tests. |
| **WORM coordinate immutability** | **RED** | With the ordinary application DB credential, updates/deletes that repoint an owner or substitute a newer same-key VersionId, different WORM target, digest, bucket/key, mode, or assertion are denied by grants/triggers. The authorizer rejects client-supplied coordinates or an unverified assertion and signs only its immutable DB-resolved target. A DB-owner/object-store corruption fixture that makes signed coordinates disagree with exact-version bytes is rejected by finalizer hashing before hold release, bypass, disposition, or purge. |
| **Shared P3Y/P10Y bytes** | **RED** | Owner A P3Y then owner B byte-identical P10Y leaves one exact version at P10Y. Moving retention application into the new-Blob-only branch turns the test RED. |
| **Same-key newer version survives** | **RED** | Create a newer target version after durable ownership; R27 of the old version removes only the old VersionId. Reintroducing all-version enumeration fails. |
| **Document policy reaches storage** | **RED** | Document-type default and installation fallback both produce exact-version read-back at check-in. Bucket default alone cannot satisfy the test. |
| **Event anchor** | **RED** | Unknown event uses capture date; later event extends; an earlier computed date never shortens. |
| **Permanent hold** | **RED** | `PERMANENT` becomes active only after exact-version legal hold reads `ON`; scheduled disposition cannot remove it. |
| **Ordinary hold release** | **RED** | An API-created or app-forged row cannot authorize `OFF`; the host one-shot alone can authorize an unchanged canonical digest, and only `hold-maintenance` can release one non-permanent exact version after a fresh owner recheck. Permanent/surviving owners, cross-role claims, delete, bypass, retention change, and broader MinIO actions are denied. |
| **Pending extension resume** | **RED** | Inject failure after some exact versions extend; active values remain old, proposed values stay visible, new captures use the longer maximum, restart resumes, and final activation occurs only after all targets verify. |
| **Normal bypass denial** | **RED** | Real MinIO denies explicit Governance bypass to the ordinary principal while preserving allowed promote/read/extend behavior. |
| **API-side R27 absence** | **RED** | Main API has read-only R27 authority access, cannot reach authorizer/executor, cannot call bypass code, and cannot send privileged Redis work. |
| **R27 forgery resistance** | **RED** | Rows forged with the ordinary app credential, API-provisioned users without external enrollment, password-only/stale or ordinary-audience tokens, altered manifests, duplicated actors, stale keys, or invalid signatures cannot leave authorization-pending state. |
| **R27 step-up and replay** | **RED** | Wrong `iss`/`aud`/`azp`, missing `sub`/`sid`/role, wrong ACR, `auth_time` older than 120 seconds, reused `(iss,jti)`, reused manifest/action nonce, same actor, and cancel-after-finalization are denied. Two valid actors require two separate fresh WebAuthn actions, and an executor race claims exactly one open request. |
| **Recovery-generation gate** | **RED** | Two valid approvals without a signed verified replacement remain `WAITING_FOR_RECOVERY_GENERATION`; a DB boolean/forged row does not release the gate. |
| **Recovery verifier lifecycle** | **RED** | The empty production registry stays fail-closed; app/authorizer cannot install a key; inactive/unknown/retired-for-new/revoked keys, duplicate witness/generation bindings, pre-authorization generation times, exclusion mismatch, and witness reuse are denied. Retirement preserves an already-bound witness; revocation invalidates it before execution. No test private-key hook starts outside the isolated test profile. |
| **R27 isolated exact execution** | **RED** | With a test-signed replacement-generation witness, only `r27-maintenance` can finalize/purge the authorized exact version. Invalid actor/event/version/owner/generation binding is denied. |
| **Derivative atomicity/race** | **RED** | A pack seal holding the shared organization lock cannot cross R27's exclusive finalization; affected packs, shares, pointers, and one-hop tombstones change atomically before purge markers. |
| **Rendered secret absence** | **RED** | API/worker/Beat lack DB owner, MinIO root/bypass, Keycloak bootstrap admin, backup key, and audit private key; migrate receives DB owner only. |
| **Non-root runtime** | **RED** | Dockerfile structure and live `id -u` prove non-zero identity for API/web and all long-running application services. |
| **Key restart stability** | **RED** | Concurrent startup/recreate uses one installer-generated persistent key; no service generates an ephemeral fallback. |
| **Migration clean guard** | **RED** | `0088 -> 0089` succeeds empty, refuses legacy Blob/purge state, and populated downgrade refuses data loss. |
| **WORM/non-WORM Blob split** | **RED** | WORM source/evidence requires a complete GOVERNANCE/version assertion while rendition, portfolio, and visual-diff producers remain valid non-WORM rows without false retention metadata. |

Tests assert public state, exact object-store responses, rendered Compose, or live process identity. They do
not mock the MinIO behavior they claim to prove. Critical REDs are observed before implementation and
their failure reason is recorded.

---

## 12. Verification ladder

Run the smallest focused test after each implementation seam, then the affected and full gates. The final
branch requires fresh evidence from at least:

```bash
cd apps/api && uv run alembic heads
cd apps/api && uv run alembic check
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

Additional targeted gates cover:

- domain retention calculation and owner-set aggregation;
- explicit `retention.read`/SYSTEM-scope `retention.manage` allow and denial behavior for proposed
  revisions and operation state;
- controlled-document, Record, ingestion, generated-artifact, disposition, and policy-extension
  integrations;
- real MinIO VersionId, retention, legal-hold, IAM denial, and exact-delete behavior;
- real database roles and MinIO principals proving the API cannot forge ordinary hold authorization, the
  host one-shot binds only the unchanged canonical digest, hold maintenance rechecks every owner, the
  OFF-only principal can release one eligible exact version, and every permanent/cross-role/delete/bypass/
  retention/broader-storage attempt is denied;
- real application-role PostgreSQL mutation denial for asserted WORM identity plus owner-only tamper
  fixtures proving exact-version SHA mismatch stops before every destructive MinIO call;
- separate Testcontainers PostgreSQL fixtures for: empty `0088 -> 0089`; populated legacy refusal with
  unchanged `alembic_version` and schema; populated `0089` downgrade refusal; and empty downgrade/re-upgrade.
  Generic `just migrate-roundtrip` is supplemental rather than proof of those refusal paths;
- dedicated-audience R27 PKCE/token routing, exact issuer/audience/authorized-party/session/ACR/auth-time
  validation, one-time token/manifest/action nonces, host-controlled realm-role enrollment, denial of
  realm-role mapping to the API service account, canonical manifest display, two signed attestations,
  cancellation/finalizer races, ordinary API/database forgery denial, authorizer key rotation,
  recovery-generation gate, recovery verifier install/retire/revoke rules, derivative lock race, and
  test-signed exact-version execution;
- rendered Compose for every S/M development/production/appliance combination;
- live effective UID/GID, read-only filesystem, writable-path, restart, and secret-readability checks;
- bounded Keycloak reconciliation, three distinct generated confidential-client credentials, scoped
  positive probes, wrong/swapped-secret and privilege denials, and no bootstrap-admin leakage into runtime;
- web static-image build/runtime behavior; and
- current CI's actual required job/check inventory rather than a historical count.

The profile render commands are explicit and use the same installer-generated, untracked fixture. For
production/appliance renders its `.env` contains a complete internally consistent placeholder tuple:
`SITE_ADDRESS`, `PUBLIC_BASE_URL`, `APP_BASE_URL`, and `KEYCLOAK_HOSTNAME` are one HTTPS app origin;
`MINIO_SITE_ADDRESS` and `S3_PUBLIC_ENDPOINT` are one distinct HTTPS object origin; all required database,
OIDC, and secret-file inputs are also present. The appliance command runs from an isolated fixture copy so
its compatibility backfill cannot modify the developer's real `.env`:

```bash
docker compose --env-file .env -f infra/compose/compose.yml -f infra/compose/compose.s.yml -f infra/compose/compose.dev.yml config
docker compose --env-file .env -f infra/compose/compose.yml -f infra/compose/compose.m.yml -f infra/compose/compose.dev.yml config
docker compose --env-file .env -f infra/compose/compose.yml -f infra/compose/compose.s.yml -f infra/compose/compose.production.yml config
docker compose --env-file .env -f infra/compose/compose.yml -f infra/compose/compose.m.yml -f infra/compose/compose.production.yml config
EASYSYNQ_APP_DIR="$PWD" infra/appliance/provision/bin/easysynq-compose config
```

The implementation plan names isolated project names and the corresponding `up`, readiness, inspection,
and teardown commands for each live smoke. Static `config` output alone is not runtime identity evidence.

Unavailable, skipped, partial, or environment-blocked checks are reported with their exact command and
failure. They are never promoted to PASS by reasoning.

---

## 13. Live walkthrough

The walkthrough is release-blocking for this slice and uses only synthetic data.

### 13.1 fresh setup

1. Enumerate the exact development project's containers, volumes, network, and bind mounts.
2. Confirm the user-authorized disposable target immediately before removal.
3. Reset only that project and prove no unintended volume or path was removed.
4. Install from the target branch with role-specific local secrets.
5. Seed the local administrator and synthetic organization/policy/content fixtures without printing or
   committing credentials.
6. Create two distinct synthetic R27 test identities, enroll their passkeys, and assign the external R27
   realm role through the host-only audited path; the ordinary API seed/provisioning path must be unable to
   perform that assignment.

### 13.2 identity and secret inspection

1. Show every long-running application/web process has its intended non-zero UID/GID.
2. Inspect rendered/live configuration and demonstrate privileged secrets are absent from API, worker,
   Beat, web, and migration after migration exits.
3. Demonstrate API/worker cannot read the R27 authorizer, R27 executor, backup, or audit private-key
   mounts and cannot reach the authorizer/executor backend networks.
4. Demonstrate ordinary MinIO operations succeed while explicit Governance bypass returns access denied.
5. Demonstrate the ordinary vault identity can set legal hold `ON` but receives access denied for `OFF`.
6. Using the ordinary application DB credential, attempt to repoint/delete an asserted WORM Blob and show
   PostgreSQL denies the exact statements; show maintenance roles cannot change its coordinates either.
7. Show the production recovery verifier registry starts empty and API/authorizer identities cannot add a
   key; do not activate the ephemeral integration-test key in the live development deployment.

### 13.3 retention behavior

1. Check in a controlled document and show its persisted target VersionId and exact-version retention
   read-back.
2. Attach byte-identical content to a longer-retained Record and show the same target version ratchets
   forward.
3. Exercise an unresolved event basis, then record its real event and show extension without shortening.
4. Stop the retention processor, request a policy extension, show visible pending state, restart it, and
   show verified completion.
5. Create a temporary domain hold and show legal hold `ON` for the exact version.

### 13.4 ordinary hold-release boundary

1. Request release through the supported UI/API and show `PENDING_AUTHORIZATION` while the physical exact
   version remains held.
2. With the application DB role, attempt to forge authorization or advance the operation and show denial.
3. Run the exact host authorization command for that one operation, confirm its bounded digest summary,
   and show the state becomes `AUTHORIZED` without a MinIO call.
4. Let `hold-maintenance` recheck owners, set `OFF` for the exact VersionId, and read back `VERIFIED`; show
   its principal cannot delete, bypass Governance, alter retention, or affect another version.
5. Separately create a permanent owner and show the ordinary authorizer refuses it and legal hold remains
   `ON`.

### 13.5 R27 boundary and deliberate recovery gate

1. Show ordinary deletion and ordinary Governance bypass fail.
2. Complete the two-person flow directly against `r27-authorizer` with the dedicated audience and show
   two fresh passkey steps; inspect the accepted exact `iss`/`aud`/`azp`/`sub`/`sid`/`acr`/`auth_time`
   bindings and show both signatures bind the same canonical manifest.
3. Show the ordinary API/database credential cannot create or alter accepted authority and has no
   privileged task route.
4. Replay one consumed action token/nonce and try a password-only, stale, or ordinary-audience token; show
   each is denied without changing request state.
5. Show the authorized request remains visibly `WAITING_FOR_RECOVERY_GENERATION` and no pack pointer,
   owner, legal hold, or physical version changes.
6. Attempt a forged database recovery flag/row and show the gate remains closed.
7. Show the executor's exact-version success path only in the real-MinIO integration harness with a
   test-signed recovery-generation witness, including exact-byte hashing and newer-version survival; do
   not present that test witness as a production walkthrough.

Successful production live deletion, derivative invalidation, and newer-version survival become mandatory
acceptance for `S-recovery-generation`, when a real signed replacement generation can satisfy the gate.

### 13.6 browser and profile parity

Walk through login, Documents, Approvals, Records, the separate R27 authorization flow, and every affected
pending/failure status exposed by the web surface. Prove authorizer tokens do not travel to the ordinary
API. API/operational-only state is shown with its supported command or endpoint rather than a fake UI
claim. Render and smoke development, production S/M, and appliance overlays under the same identity and
secret assertions.

The handoff records exact commands, counts, observed browser behavior, compatibility decisions, and every
unverified item. The separate disposable Fedora proof remains its own PR/release gate and is not replaced
by this walkthrough.

---

## 14. Documentation and handoff

Implementation updates executable and narrative truth together, including the OpenAPI source/generated
artifacts for new operation state, setup/admin manuals, security/retention documentation, fresh-install
runbook, appliance instructions, backup/key-rotation runbooks where identity ownership changes, current
status, slice history, and any R61-protected clauses affected by the design.

Every example uses placeholders from its first draft. Run the site-data scanner before handoff. Actual
local administrator credentials, secrets, object keys, hostnames, bucket names, and weakness inventories
remain untracked.

The implementation PR states:

- the observable exact-version and capability outcome;
- all files and migrations changed;
- focused RED/GREEN and mutation evidence;
- affected/full test commands with results;
- live MinIO, browser, profile, and appliance walkthrough evidence;
- the clean-deployment and no-downgrade compatibility decisions;
- intentionally deferred programme work; and
- every partial, skipped, unavailable, or failed check.

The owner approved this amended specification and its task-level implementation plan on 2026-08-18. That
plan maps each invariant and acceptance proof to concrete files, tasks, commands, checkpoints, and
rollback/refusal behavior without weakening this design; implementation must follow it serially and stop
at every named owner-review condition.
