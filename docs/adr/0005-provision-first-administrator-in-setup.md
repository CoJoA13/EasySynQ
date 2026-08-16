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
