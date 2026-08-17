# 0005 — Provision the first administrator in the setup wizard

**Date:** 2026-08-15

**Status:** Accepted

## Context

EasySynQ’s post-setup Users screen already creates a Keycloak identity, links the internal user, and
shows a temporary password once. First-run setup is inconsistent: most install paths require an
operator to create or federate a Keycloak identity separately before presenting the EasySynQ
bootstrap secret, while the appliance creates a fixed `qmsadmin` identity and writes its temporary
password to a setup sheet.

The product has no reliable site SMTP dependency and was only partly deployed. The owner wants one
browser workflow in which Keycloak remains the authentication authority but is never an operator
destination. The first-administrator boundary must still handle the non-atomic write across Keycloak
and PostgreSQL without deleting identity data or creating more than one administrator.

## Decision

Provision the first administrator through the public pre-authentication `/setup` experience. A
valid, unexpired EasySynQ bootstrap secret authorizes exactly one durable identity claim while setup
is `UNINITIALIZED`. The API creates the Keycloak account without a credential, commits the EasySynQ
user and System Administrator assignment, then issues a generated temporary password that is shown
once. Operator acknowledgment consumes the bootstrap secret, advances setup to `IN_SETUP`, and
starts Keycloak sign-in, where a password change is mandatory.

Persist a minimal bootstrap claim and tag the created Keycloak identity with its opaque operation
identifier. Retries may recover or reset only that bound identity. Unrelated collisions fail closed,
and EasySynQ never compensates by deleting a Keycloak account. The bootstrap operations use a system
audit actor because the selected administrator has not authenticated.

Replace the authenticated grant-only bootstrap API and migrate all repository consumers atomically
under R65. Remove fixed `qmsadmin` creation and human-password output from new appliance installs.
Keep internal Keycloak service credentials and the post-setup `user.create` provisioning flow.

## Consequences

Every supported fresh-install path has the same browser-first operator experience and no longer
requires Keycloak expertise, server user scripts, or subject copy/paste. SMTP remains optional. The
first administrator and every later local user receive the same show-once temporary-password
experience.

The API gains a small persistent state machine and a public secret-authorized provisioning surface.
It requires strict rate limiting, serialization, fail-closed collision handling, explicit
acknowledgment, truthful system-actor audit events, and live Keycloak acceptance. A migration is
required for the pending claim. Existing operational installations bypass the new path.

The browser must retain the bootstrap secret and temporary password only in volatile component
memory until acknowledgment. Losing the password before acknowledgment causes a reset, not a read.
The old temporary password becomes invalid.

## Alternatives

### Generate a fixed administrator during installation

The appliance already approximates this by creating `qmsadmin` and writing its temporary password to
a protected setup sheet. It is smaller operationally but leaves a human credential on disk, forces a
predefined username, and makes appliance and ordinary installs behave differently.

### Prompt for the administrator in the installer or a CLI

A terminal command could create the Keycloak and EasySynQ identities and print a temporary password.
It removes Keycloak-console work but still requires server access, splits setup between terminal and
browser, and leaves the most security-sensitive onboarding step outside the product UI.

### Keep the authenticated bootstrap flow

The current design lets any already-authenticated Keycloak identity present the bootstrap secret and
become System Administrator. It avoids a public provisioning endpoint but preserves the exact
Keycloak/script dependency this change is intended to remove.

## Payoff trigger

If the identity provider and EasySynQ persistence can participate in one transactional provisioning
boundary, remove the staged bootstrap claim and recovery marker. Until then, retain the claim because
it is what makes partial external-system failure recoverable without deletion or duplicate grants.

## 2026-08-16 amendment — Keycloak user-profile compatibility

### Context

Live first-administrator acceptance against the shipped Keycloak version reached the mandatory
password update and was then diverted to `VERIFY_PROFILE`. When a realm has no explicit user-profile
policy, Keycloak requires email, first name, and last name for ordinary users. EasySynQ’s approved
first-administrator and later-user contracts make all three fields optional, and SMTP remains an
optional installation dependency. Requiring or inventing profile data would therefore change the
product contract rather than fix the identity-provider mismatch.

Realm export alone cannot correct existing installations, and disabling `VERIFY_PROFILE` would
remove useful validation for attributes that a site intentionally configures.

### Decision

Before the shared identity lookup/create path handles either the first administrator or an ordinary
later user, EasySynQ reads the realm’s complete user-profile policy from Keycloak. It removes only
the `required` member, when present, from the exact built-in attributes `email`, `firstName`, and
`lastName`. It preserves every other member, validator, permission, group, custom attribute, and
unmanaged-attribute policy returned by Keycloak, and performs a whole-document update only when one
of those three members changed.

The reconciliation runs before identity lookup so retries and existing realms receive the same
policy. A malformed policy, missing or duplicate target built-in, failed read, or failed write stops
provisioning. `VERIFY_PROFILE` remains enabled and continues to enforce every other configured
profile rule.

### Consequences

The approved optional identity fields no longer interrupt first sign-in after the temporary password
is replaced, and the same semantics apply to ordinary local-user provisioning. Existing realm
customization is retained except for the three explicitly optional required flags. An already
reconciled realm is read but not rewritten.

Keycloak exposes this policy through a whole-document update without a version or compare-and-swap
token. A concurrent external administrator edit could be overwritten between the read and write;
this accepted limitation is tracked in
[`keycloak-profile-reconciliation`](../debt/20260816010910-keycloak-profile-reconciliation.md).

### Alternatives

Making the three public setup fields required or fabricating placeholder values was rejected because
it would contradict the approved optional-field and optional-SMTP contract. Disabling
`VERIFY_PROFILE` was rejected because it would bypass unrelated site profile rules. Updating only
the shipped realm export was rejected because it would not repair an existing realm. An unconditional
whole-document write was rejected because it would create needless overwrite risk after the realm is
already reconciled.

### Payoff trigger

Replace or fence the whole-document reconciliation when Keycloak provides a versioned or
compare-and-swap user-profile update, or when EasySynQ introduces supported external profile-policy
administration. Remove reconciliation only if a future supported identity provider natively preserves
EasySynQ’s optional-field semantics for both bootstrap and ordinary users.

### 2026-08-16 live-proof correction — managed bootstrap claim marker

The fresh-realm live acceptance proved an adjacent Keycloak 26 behavior: the default
`unmanagedAttributePolicy` is disabled, so a successful Admin REST user create silently ignores the
unknown `easysynqBootstrapClaim` attribute. The subsequent exact-user read therefore cannot prove
marker ownership and fails closed before credential issuance.

The same pre-lookup realm-profile reconciliation now also defines `easysynqBootstrapClaim` as one
managed, non-multivalued attribute whose view and edit permissions are both admin-only. It appends
that exact definition when absent, replaces a broader or otherwise mismatched definition, and refuses
duplicate definitions. Every other attribute, group, validator, annotation, and the existing
unmanaged-attribute policy remain unchanged; in particular, EasySynQ does not switch the realm to
`ADMIN_EDIT` or `ENABLED`.

This keeps the opaque recovery marker available to the Admin REST boundary without exposing it in
end-user profile contexts or enabling unrelated custom attributes. It expands the existing
whole-document user-profile write boundary and therefore remains covered by the registered
Keycloak-profile-reconciliation debt rather than introducing a second residual.

Broadly enabling unmanaged attributes was rejected because `ADMIN_EDIT` would admit every unknown
attribute through administrative writes and `ENABLED` would also expose that surface to end-user
contexts. Weakening the exact marker check was rejected because it is the proof that recovery acts
only on the claim-bound identity. Updating only the realm export was rejected because fresh defaults
and existing realms both require deterministic runtime reconciliation.

## 2026-08-16 amendment — canonical usernames and serialized proof admission

### Context

Keycloak canonicalizes local usernames to lowercase. Preserving submitted case could therefore bind
a durable bootstrap claim to a spelling that an exact retry lookup could never recover. Independent
security review also found that the public bootstrap endpoints checked the Redis failure budget
before acquiring the PostgreSQL singleton lock, allowing racing invalid proofs to pass admission
together, and that separate Redis `INCR`/`EXPIRE` calls could leave a permanent no-TTL lockout.

A separate response-loss edge existed after acknowledgment: once a correctly consumed secret
expired, an otherwise idempotent retry was rejected before the already-complete claim could be
returned.

### Decision

All supported local Keycloak usernames are trimmed and canonicalized to lowercase through the shared
identity-provisioning boundary. First-administrator claims, Keycloak lookup/create operations,
responses, audit projections, and ordinary in-app user provisioning all use that canonical value.
Display-name case is preserved.

Both bootstrap proof endpoints keep a fast Redis admission check and repeat it after taking the
singleton `system_config` row lock, immediately before proof validation. Failed-proof recording is
one atomic Redis Lua operation that replaces the counter with `SET ... EX`: a positive remaining TTL
is preserved, a new or legacy no-TTL counter receives the full window, and malformed or unavailable
state fails closed.

Acknowledgment always performs the salted-hash comparison before consulting setup state. A matching
proof may ignore expiry only when setup is already `IN_SETUP` and the secret is durably consumed;
the complete-claim and administrator-assignment checks still run before idempotent success. Missing
or mismatched proofs remain generic, counted `bootstrap_invalid` denials.

### Consequences

Case variants converge on one recoverable identity instead of stranding a claim. Concurrent invalid
provision and acknowledgment requests share one serialized attempt budget, and a partial Redis TTL
write can no longer create an indefinite numeric lockout. A caller that lost the successful
acknowledgment response can recover after expiry without reissuing a credential or weakening denial
behavior for any mismatched proof.

### Alternatives

Keeping only the pre-lock Redis check was rejected because racing requests could all pass before any
failure was recorded. A split `INCR` followed by `EXPIRE`, including a Redis transaction that still
exposes an error between application calls, was rejected because it can strand a numeric counter
without a TTL; the server-side script makes the replacement and expiry one atomic operation.

Rejecting uppercase input was considered, but canonicalizing before any durable or external write
matches the supported Keycloak behavior and is less error-prone for operators. Continuing to reject
all expired acknowledgment retries was rejected because a committed acknowledgment with a lost
response must remain recoverable without another credential side effect.

### Payoff trigger

Replace the custom Redis admission script and cross-store lock coupling when bootstrap admission can
live in one transactional datastore. Revisit the lowercase rule before adding a supported identity
provider with different documented canonicalization semantics; migrate any pending claims before
changing the canonical form. The mirrored deferred boundary is tracked in
[`bootstrap-admission-identity-coupling`](../debt/20260816024758-bootstrap-admission-identity-coupling.md).

## 2026-08-16 amendment — credential-generation receipt and claimed-profile recovery

### Context

Independent PR review found that the singleton row lock prevents a password reset after bootstrap
consumption, but does not prove that the operator acknowledged the password generation visible in a
particular browser response. Two same-username requests can both reset the credential before either
is acknowledged; the later reset invalidates the earlier response, whose setup secret alone could
still consume bootstrap.

The same review exposed three adjacent recovery gaps. A host break-glass role assignment can create
an administrator before a public claim, a definitive Keycloak validation rejection can strand an
unowned claim, and adoption of a marker-bound Keycloak identity can persist corrected profile values
only in EasySynQ. The browser also had no in-panel way to supply a reminted setup secret after the
password was displayed.

### Decision

Bind acknowledgment to the active temporary-password generation with a random volatile credential
receipt. Persist only its SHA-256 digest on `system_config`, rotate it on every reset, return the
plaintext receipt only with the show-once password, and require both the current setup secret and
receipt for acknowledgment. A stale receipt fails without consuming bootstrap. Required receipt
state must commit before the password is returned; the credential audit retains its non-fatal
under-claim behavior through an isolated savepoint.

Serialize public bootstrap against System Administrator assignments, allowing only the administrator
already linked to the active claim. Release a still-unowned claim only after a definitive create-time
Keycloak validation rejection proves no identity exists.

Permit a retry to correct optional profile values on the exact marker-bound Keycloak identity. Read
its full representation, verify subject, canonical username, and marker, preserve every unrelated
field, and conditionally update only email and name fields while the singleton lock also protects the
EasySynQ projection update. A rejected update retains the claim because the identity already exists.

Keep the browser credential visible after acknowledgment failure. It may accept a reminted setup
secret to acknowledge the same generation or explicitly reissue a password after the server reports
that the visible generation was superseded. Every secret, password, and receipt remains volatile and
is cleared before authentication begins.

### Consequences

Acknowledgment now proves receipt of the current usable password rather than merely proving that some
password was once issued. Response loss and reminted-secret recovery remain in-app, while concurrent
tabs cannot consume bootstrap on behalf of an invalidated credential.

The contract and database gain one receipt field, and recovery gains another state value that every
consumer and fixture must migrate atomically. Required receipt persistence narrows R64's non-fatal
credential-audit rule: audit insertion may still under-claim, but failure to persist the receipt
state itself must suppress the password response so the next retry can reset safely.

EasySynQ serializes its own claimed-profile writes, but Keycloak exposes user updates as a whole
`UserRepresentation` without a version or compare-and-swap token. A simultaneous external Keycloak
administrator edit can still race the preserved-representation PUT. That deliberate limitation is
tracked with the existing profile-reconciliation boundary.

### Alternatives

Refusing all password reissuance until acknowledgment was rejected because a lost response would
need a separate manual override and would regress the approved recovery flow. Relying on the existing
row lock plus stronger UI warnings was rejected because it cannot prove which returned password was
acknowledged. Persisting plaintext passwords or receipts was rejected because it violates the
show-once secrecy boundary.

Binding the original optional profile values and rejecting every correction was considered. The
owner selected marker-scoped reconciliation because it keeps recovery in-app and lets an operator
correct input without Keycloak administration. Replacing the full Keycloak representation with a
minimal update body was rejected because it risks clearing unrelated provider-managed fields.

### Payoff trigger

Remove the receipt state when identity-provider credential issuance and EasySynQ acknowledgment can
participate in one transactionally attested delivery boundary. Replace or fence whole-representation
profile reconciliation when Keycloak provides versioned or compare-and-swap user updates, or when
external profile administration becomes a supported concurrent workflow. The receipt-state cost is
tracked in
[`bootstrap-credential-receipt-state`](../debt/20260816092041-bootstrap-credential-receipt-state.md).

## 2026-08-16 amendment — serialized administrator writers and definitive claim release

### Context

The bootstrap administrator check originally locked only the `system_config` singleton and then
read role assignments. Generic API grants, in-app provisioning, and host break-glass grants did not
take that row lock, so an administrator assignment could commit after bootstrap's check and before
its own persistence, credential issuance, or acknowledgment. The existing per-organization
administrator advisory lock serialized only revoke and disable writers and therefore did not close
the insert side of the invariant.

A create-time Keycloak validation rejection also released a still-unowned claim from the initial
pre-create absence observation. While the create call was in flight, another same-claim request or
external identity operation could make that observation stale; a delayed rejection could then clear
the current claim despite a now-present marker-owned identity or changed administrator state.

### Decision

Use the existing per-organization transaction advisory `lock_admin_set` as the shared protocol for
every supported runtime System Administrator assignment writer. Generic API grants, in-app user
provisioning, host `grant-role`, bootstrap inserts, role revocation, and user disable take the same
key before observing or mutating the administrator set and retain it through commit or rollback.
The synchronous CLI helper and asynchronous application helper share one key derivation. Ordinary
role assignment paths do not take the lock.

Bootstrap always acquires locks in one order: the singleton `system_config` row first, then the
organization's administrator advisory lock. After establishing the durable claim, it reacquires and
validates both locks before the first Keycloak identity read and retains them across lookup,
create/adopt, and the EasySynQ persistence or rejection-release decision. A non-conflict
`KeycloakRejected` may release only after an exact-username re-read is definitively absent and the
locked database claim remains unowned, unissued, unchanged, and free of an unrelated administrator.
A found or malformed identity, provider uncertainty, changed claim, linked user, issued credential,
or changed administrator set retains the claim. Every exception path explicitly rolls back the
lock-owning transaction.

Development persona seeding writes only fixed ordinary roles, owner-assignment writes only the
fixed Process Owner role, and Alembic seed/backfill operations run offline rather than as supported
concurrent runtime administrator writers; they remain outside this protocol. Direct database writes
that bypass supported application and host surfaces remain unsupported.

### Consequences

Bootstrap's administrator uniqueness check now conflicts with every supported concurrent
administrator insert, and same-claim requests cannot overlap the external identity stage. Ordinary
role traffic retains its prior concurrency. Claim release is based on a fresh provider observation
inside the same database serialization boundary as the release decision, so uncertainty retains
recoverable state.

The tradeoff is deliberately longer database lock duration while Keycloak lookup/create/adopt is in
flight. Provider latency or outage can queue bootstrap and administrator writers for the same
organization, so provider timeouts and explicit rollback remain part of the safety boundary. This
cost is tracked in
[`bootstrap-provider-lock-duration`](../debt/20260816120427-bootstrap-provider-lock-duration.md).

### Alternatives

A table-wide role-assignment lock was rejected because it would serialize unrelated organizations
and ordinary role traffic. A claim compare-and-swap alone was rejected because it would not conflict
with administrator writers or make a stale external absence observation current. A bootstrap-only
singleton lock was rejected because other supported writers do not acquire it. Releasing from the
initial absence observation was rejected because Keycloak and PostgreSQL do not share a snapshot.

### Payoff trigger

Remove the singleton-held provider stage and its lock-duration cost when identity provisioning and
EasySynQ persistence can participate in one transactionally attested boundary. Replace the shared
advisory protocol only when the database enforces an equivalent per-organization administrator-set
invariant across every supported runtime writer without serializing ordinary roles.

## 2026-08-16 amendment — pre-operational administrator blocker recovery

### Context

A supported but unrelated pre-operational System Administrator assignment makes the public
first-administrator flow fail closed. The existing host `grant-role` command cannot recover that
state because it adds the same assignment that blocks setup. Direct SQL would bypass setup-state,
organization, claim-ownership, and shared-lock invariants.

### Decision

Provide the exceptional host-only command `easysynq setup release-administrator-blocker --subject
<keycloak-subject> [--org CODE]`. It is valid only while the selected organization's setup state is
exactly `UNINITIALIZED`. It locks the `system_config` singleton row first, then takes the shared
per-organization administrator-set advisory lock. Under those locks it resolves the exact same-org
user, refuses the user linked to the bootstrap claim, and removes only that user's seeded System
Administrator assignment.

The command never contacts Keycloak; creates, deletes, disables, or adopts an identity or
`app_user`; removes another role; changes setup or claim state; consumes bootstrap proof; or grants
replacement access. An absent named user or assignment is a no-write idempotent result. Every
mutation-capable failure rolls back before a generic operator-safe error is emitted.

Because no trusted authenticated application administrator exists at this boundary, the command
cannot produce the normal actor-attributed application audit event. Each use therefore requires an
independent incident/change record containing the command, operator, reason, subject, organization,
and time. This deliberate cost is tracked in
[`uninitialized-admin-recovery`](../debt/20260816173328-uninitialized-admin-recovery.md).

### Consequences

An installation blocked by an unrelated assignment can return to the owner-approved browser flow
without erasing identity or attribution history and without weakening public bootstrap's
administrator uniqueness check. Recovery remains a host-access procedure with an out-of-product
audit obligation.

### Alternatives

Adopting the unrelated administrator into the public claim was rejected because a bootstrap proof
must not take control of an identity without the claim marker. Removing all administrators was
rejected as over-broad. Direct SQL was rejected because it cannot enforce the transaction protocol.
Using `grant-role` was rejected because it creates, rather than releases, the blocker.

### Payoff trigger

Replace this host exception when a trusted authenticated or host-attested workflow can resolve the
pre-existing administrator with durable in-application audit, as specified by the linked debt
record.

## 2026-08-17 amendment — trusted remint resets bootstrap admission failures

### Context

The global Redis bootstrap-failure budget belongs to the active proof admission boundary, but a
trusted host remint rotates the proof hash and expiry in PostgreSQL. Without coordinating the two,
the old proof's exhausted Redis budget can outlive the PostgreSQL rotation and reject the valid
replacement proof before it can recover its pending claim.

### Decision

While holding the locked `system_config` row in the uncommitted trusted-remint transaction, the host
command deletes the canonical global Redis failure key and waits for Redis to acknowledge that
deletion before it assigns and commits the replacement hash and expiry. Redis/provider failure is
redacted to an operator-safe error; deletion failure explicitly rolls back the database transaction
and prints no replacement proof.

### Consequences

A valid reminted proof starts with an empty bootstrap admission budget and can recover the same
claim under the existing singleton-lock protocol. A commit failure after Redis deletion can allow
attempts for the old proof to be recorded again, but cannot publish new bootstrap authority. The
cross-store ordering is intentionally non-atomic and remains a recovery-boundary limitation.

### Alternatives

Generation-scoped Redis keys were rejected because the current global admission state has no atomic
generation rotation with the PostgreSQL proof. Post-commit best-effort deletion was rejected because
it could publish replacement authority while leaving a valid proof rate-limited.

### Payoff trigger

Replace this ordering with one transactional admission/proof store, or generation-scoped state with
atomic rotation.

## 2026-08-17 amendment — pending credential fence and acknowledged setup boundary

### Context

The active receipt digest was written in the same PostgreSQL transaction that followed the Keycloak
password reset. On reissue, a failure committing that digest rolled PostgreSQL back to the prior active
receipt even though Keycloak had already invalidated its password. A queued acknowledgment could then
consume bootstrap authority for an unusable credential. Separately, the provisioned administrator could
authenticate before acknowledgment and reach every latch-exempt authenticated setup route, including
finalization, while setup still remained `UNINITIALIZED`.

Bootstrap Redis reads and trusted-remint deletion could also wait without a bound while holding the
singleton row, and admission returned a threshold legacy counter before repairing its missing TTL.

### Decision

Use the existing nullable credential receipt digest as a pending-generation fence. Before every reissue,
while holding the singleton row and administrator-set locks, clear an active digest and commit that null
state before resetting the Keycloak password. Retain the prior non-null issuance timestamp because a
credential really was issued; the null digest means no generation is currently acknowledgeable. The
commit releases both locks, so the issuer reacquires them in canonical singleton-then-administrator order
and revalidates the claim, linked user, and administrator set. If another issuer promoted an active digest
in the interval, repeat the pending transition before any reset. Hold both locks from a confirmed pending
state through reset and promotion of the new digest. A post-reset promotion failure rolls back to the
durable pending state, making every old receipt fail as `bootstrap_credential_superseded`; retry may reset
and promote safely. Initial issuance already begins with a null digest and needs no extra pending commit.

Every authenticated setup detail, organization, storage, backup, restore-test, authentication, and
finalization service now requires exact `IN_SETUP` state plus non-null `bootstrap_consumed_at`. Guards run
before external probes or enqueueing and are repeated under the singleton lock before state-changing
commits. Locked rereads force a fresh ORM population so a cached pre-probe state cannot hide a concurrent
transition. Restore-test enqueue authorization linearizes at that locked guard, then commits the read-only
transaction before calling Celery so an unbounded broker wait cannot retain the singleton lock; a later
state transition does not revoke the already-authorized enqueue attempt. Provisioning and login alone
therefore cannot bypass receipt acknowledgment.

Bootstrap-specific asynchronous and synchronous Redis clients use finite connect and read timeouts
without changing unrelated Redis clients. Admission atomically validates the failure counter and repairs
a no-TTL legacy key before applying the threshold. Timeout failures remain redacted and fail closed; a
lock-owning database transaction rolls back before returning.

### Consequences

Post-reset database failure can suppress a password response but cannot revive acknowledgment authority
for an inactive password. A pending generation is recoverable by the same claim and current setup proof,
without a migration or public-contract change. Authenticated setup cannot advance or expose its sensitive
detail until credential acknowledgment records consumption. Bootstrap Redis outages have a bounded lock
cost, and legacy threshold counters expire instead of locking the installation indefinitely.

The extra pending commit adds one database round trip to reissue and the Keycloak reset still runs while
database locks are held. Those existing complexity and latency costs remain tracked in
[`bootstrap-credential-receipt-state`](../debt/20260816092041-bootstrap-credential-receipt-state.md),
[`bootstrap-credential-lock`](../debt/20260815215020-bootstrap-credential-lock.md), and
[`bootstrap-admission-identity-coupling`](../debt/20260816024758-bootstrap-admission-identity-coupling.md).

### Alternatives

Restoring the prior receipt after failure was rejected because it can acknowledge an inactive password.
Clearing both receipt digest and issuance timestamp was rejected because it would misrepresent a real
prior issuance as never issued and return the wrong recovery state. Adding a generation column or new
enum was unnecessary: the existing nullable digest expresses pending safely under the established lock
protocol. Guarding only finalization was rejected because earlier authenticated setup reads, probes,
writes, and enqueueing would still bypass the acknowledgment boundary.

### Payoff trigger

Replace the pending digest and lock-held reset when credential delivery and EasySynQ acknowledgment share
a transactionally attested provider boundary. Remove the setup-service guard only if a future approved
state machine provides an equivalent authenticated proof that the active credential was acknowledged.
