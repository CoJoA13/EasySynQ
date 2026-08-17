# S-first-admin-review-hardening — bind recovery to the active identity and credential

**Date:** 2026-08-16

**Status:** Owner approved on 2026-08-16; ready for executable implementation planning after written-spec review.

**Programme:** Identity onboarding

**Parent slice:** S-first-admin-provisioning

## 1. Outcome

The first-administrator flow remains browser-first and no-SMTP, but every public bootstrap action is
bound to the one administrator and the one temporary-password generation it is allowed to affect.
Break-glass state cannot cause a second administrator to be created, definitive validation failures
remain recoverable, retries keep the EasySynQ and Keycloak profiles consistent, and an operator can
acknowledge with a reminted setup secret without discarding a still-valid shown password.

This design closes the five unresolved review findings on PR #466. It refines, rather than replaces,
R64, R65, R66, ADR 0005, and the approved S-first-admin-provisioning design.

## 2. Findings and binding closure

1. **An unrelated administrator already exists.** Public bootstrap MUST refuse a new claim when any
   System Administrator assignment exists outside the user already linked to the active claim.
2. **Keycloak definitively rejects create-time profile input.** When a non-conflict Keycloak 4xx
   proves that no account was created, the still-unowned claim MUST be released. Ambiguous outcomes
   and existing claimed identities retain the claim.
3. **Acknowledgment can name the wrong password generation.** Every temporary-password reset MUST
   rotate a volatile credential receipt. Acknowledgment MUST prove both the bootstrap secret and the
   receipt for the active credential generation.
4. **A claimed identity can diverge across stores.** A retry MAY correct email, first name, or last
   name. It MUST reconcile those values on the exact marker-bound Keycloak identity and the linked
   EasySynQ user under one serialized application boundary.
5. **A reminted secret cannot be entered while the password is visible.** The SPA MUST keep the
   password visible and allow a replacement setup secret to be supplied for acknowledgment. It MUST
   also provide an explicit reissue path when the shown password has been superseded.

## 3. Credential-generation receipt

### 3.1 Persistent state

Migration `0088` adds nullable `system_config.bootstrap_credential_receipt_hash`, a lowercase
64-character SHA-256 hex digest. The receipt itself is a cryptographically random, high-entropy,
URL-safe value and is never persisted. Hashing is sufficient because the receipt has at least 256
bits of random entropy; the bootstrap secret remains the primary authority proof.

The nullable migration default preserves existing rows. A pending claim without a receipt is not
acknowledgeable; retrying provisioning issues a new password and receipt. Consumed installations do
not re-enter bootstrap.

The receipt hash remains after successful acknowledgment so a lost successful response can replay
the same consumed acknowledgment, including after the bootstrap secret expires. It confers no
post-setup identity or authorization capability.

### 3.2 Public contract

`FirstAdministratorProvisioned` gains required `credential_receipt`. The value is returned in the
same show-once response as `temporary_password` and has no separate read endpoint.

`BootstrapAcknowledgeRequest` requires:

```json
{
  "secret": "<current setup secret>",
  "credential_receipt": "<receipt returned with the shown password>"
}
```

The SPA sends no bearer token to either endpoint. The receipt is absent from public user summaries,
problems, audit payloads, logs, URLs, browser storage, and TanStack Query or mutation caches.

### 3.3 Issuance and acknowledgment

Credential issuance retains the singleton row lock across the Keycloak reset and receipt-state
commit. On a successful reset it rotates the receipt, stores only the hash, updates
`bootstrap_credential_issued_at`, and then returns the password and plaintext receipt.

The receipt state is safety-critical. If its outer transaction cannot commit, the API does not
return the password; the next retry resets the same bound identity again. The credential audit event
remains a deliberate non-fatal under-claim: its insert/flush is isolated in a database savepoint so
an audit-only failure can roll back the event while the required receipt state still commits.

Acknowledgment verifies, in order:

1. rate-limit admission;
2. bootstrap proof using the existing constant-time and generic-denial rules;
3. complete claim, linked user, credential-issued state, and administrator assignment;
4. constant-time equality between the supplied receipt digest and the active stored digest; and
5. the absence of an unrelated System Administrator assignment.

A receipt mismatch returns `409 bootstrap_credential_superseded`, records no bootstrap consumption,
and never reveals the current receipt or password. The current matching secret and receipt retain
the existing idempotent consumed-replay behavior. A mismatched bootstrap secret remains the generic,
counted `403 bootstrap_invalid` response regardless of receipt content.

## 4. Administrator uniqueness

Every serialized bootstrap boundary distinguishes the claim-owned administrator from unrelated
administrator assignments:

- before a claim exists, any System Administrator assignment blocks claim creation;
- while a claim exists but has no linked user, any System Administrator assignment blocks further
  public bootstrap activity;
- after the claim links a user, only that user may hold the System Administrator assignment while
  the public flow continues; and
- any additional or different administrator assignment blocks profile persistence, credential
  issuance, and acknowledgment without deleting or reassigning data.

The check runs only after a valid bootstrap proof has been established, so an anonymous caller
cannot use it as an administrator-existence oracle. The stable response is
`409 bootstrap_administrator_exists`. Recovery is the documented host break-glass procedure; the
public endpoint never adopts, resets, or grants an unrelated administrator identity.

## 5. Definitive rejection and claim release

Claim release is limited to proof that the pending claim owns no identity and no application state:

- the exact username lookup was definitively absent;
- Keycloak returned a non-conflict input-validation rejection from account creation; and
- the singleton still contains the same claim and username with no linked user or issued credential.

The service clears the claim under the singleton lock before returning the redacted
`422 validation_error`. The operator may then correct the username or other profile values.

A create timeout, lookup failure, malformed marker, ambiguous conflict, failed profile update on an
existing claimed account, database failure after creation, or any other state in which an identity
may exist retains the claim. EasySynQ never deletes a Keycloak identity as compensation.

## 6. Claimed-identity profile reconciliation

Retries keep the canonical username fixed but MAY change display name, email, first name, and last
name. Reconciliation applies only after the Keycloak identity's full representation proves all of:

- the requested subject is the representation's subject;
- the canonical username is the bound username; and
- `easysynqBootstrapClaim` contains exactly the current claim identifier.

The Keycloak client reads the full user representation, copies it, changes only `email`,
`emailVerified`, `firstName`, and `lastName`, and preserves every unrelated field, required action,
federation link, custom attribute, and the bootstrap marker. Absent optional values are represented
using Keycloak's supported clearing semantics. It performs no PUT when the effective profile is
already equal.

The final exact read, conditional Keycloak update, and EasySynQ user projection update run while the
singleton row is locked. Each retry writes both stores from one normalized profile; concurrent
retries therefore converge on one complete serialized profile rather than mixing fields across
stores. The EasySynQ projection is updated on recovery even when its user row already exists.

A Keycloak validation rejection during reconciliation returns the redacted `422 validation_error`
and retains the claim because the marked identity exists. An outage or malformed representation
fails closed as `502 keycloak_unavailable`. No Keycloak subject, marker, or raw provider detail enters
the response.

The whole-representation PUT has no provider CAS token. The singleton lock serializes EasySynQ
writers but cannot fence an external Keycloak administrator; that deliberate boundary extends the
registered Keycloak profile-reconciliation debt.

## 7. Browser recovery states

The SPA keeps the temporary password, credential receipt, bootstrap secret, and submitted profile
only in component memory.

- **Ordinary acknowledgment/network failure:** keep the password visible and offer
  **Retry acknowledgment** with the same secret and receipt.
- **Invalid or expired bootstrap proof:** keep the password visible, explain that a current setup
  secret is required, and reveal a focused replacement-secret field. Submitting the reminted secret
  retries acknowledgment of the same still-valid password and receipt; it does not reset the
  password.
- **Superseded receipt:** label the shown password as no longer current and offer
  **Issue a new temporary password**. The action reuses the bound username and current normalized
  profile, requires the current setup secret, and replaces the visible password and receipt only
  after the new response arrives.
- **Successful acknowledgment:** synchronously clear password, receipt, and setup secret before the
  setup-state refetch and OIDC transition.

All actions remain single-flight. `beforeunload` protection covers provisioning, a visible password,
replacement-secret acknowledgment, and reissue. Error focus, live-region semantics, forced-colors
containment, 44 CSS-pixel actions, and 320 CSS-pixel document containment remain required.

## 8. Compatibility and documentation

R65 permits the additive required receipt fields because no supported production or external-client
compatibility boundary exists. OpenAPI source, generated API and web types, response-schema tests,
SPA consumers, synthetic fixtures, the live harness, current docs, and the contract lock migrate in
the same change. No compatibility shim or second acknowledgment endpoint is retained.

R66 is clarified so "shown once, then acknowledge" means acknowledgment of the active shown
credential generation. The existing credential-lock debt remains open because this design still
holds a PostgreSQL row lock across the Keycloak reset. The existing claim-state-machine and
Keycloak-profile-reconciliation debt records are updated where their boundaries expand. The new
credential-receipt state is mirrored in
[`bootstrap-credential-receipt-state`](../../debt/20260816092041-bootstrap-credential-receipt-state.md).

## 9. Verification contract

Implementation starts with focused RED proofs and demonstrates:

### 9.1 API, migration, and security

- break-glass-created or otherwise unrelated administrator assignments cannot create a claim,
  persist profile state, issue a credential, or consume bootstrap;
- the claim-owned administrator remains recoverable through issuance and acknowledgment;
- a definitive create-time validation rejection releases only an unowned claim and permits a
  corrected identity, while ambiguous or post-create failures retain it;
- migration `0087 → 0088 → 0087 → 0088` preserves populated setup and identity state;
- every reset rotates the receipt and stores no plaintext copy;
- a stale receipt cannot consume bootstrap, while the active receipt can;
- a consumed acknowledgment replays with the matching secret and receipt after expiry;
- mismatched proofs remain generic, constant-time, and rate-limited;
- receipt-state commit failure returns no password and retry recovers;
- audit-only failure does not suppress a safely acknowledgeable response;
- claimed profile correction changes only the approved Keycloak fields, preserves all unrelated
  representation content, and updates the EasySynQ projection;
- concurrent different-profile retries leave both stores on one complete profile; and
- no secret, password, receipt, claim marker, provider detail, or subject reaches logs, audit,
  problems, or unrelated schemas.

### 9.2 Web and browser

- the receipt is posted on acknowledgment and never retained outside component memory;
- ordinary failure retries the same generation;
- a reminted secret acknowledges the still-visible current password without reissue;
- a superseded receipt cannot transition setup and can explicitly reissue a new password;
- successful acknowledgment clears every volatile authority and credential value before login;
- reload remains the response-loss fallback and never re-reads a password;
- focused Vitest covers error mapping, focus, single-flight actions, storage/cache absence, and
  beforeunload lifecycle; and
- required Chromium proves the recovery states at 320×800 with forced colors, keyboard operation,
  exact request bodies, no old endpoint, no premature OIDC, and zero document overflow.

### 9.3 Live and affected gates

The Docker-backed Keycloak acceptance run must use the updated contract and prove one complete
first-administrator flow through forced password replacement and authenticated `/setup`. It also
proves that the original temporary password is rejected. Required evidence remains Chromium-only,
one worker, zero retries.

Affected API unit, setup/users/backup integration, migration, response-contract, generated-contract,
web unit, lint, type, build, authority, site-data, and diff gates run before handoff. Full-suite counts
are updated only when those suites are freshly rerun. Firefox, WebKit, actual assistive technology,
SMTP, deployment, general live acceptance, and disposable Fedora proof remain outside this review
hardening slice.

## 10. Whole-branch review correction — pre-operational administrator recovery

### 10.1 Problem and outcome

The final whole-branch review found that a supported fresh-install path still created a demo Keycloak
identity before browser setup, and that the documented response to `bootstrap_administrator_exists`
pointed to `grant-role`. That command adds the same System Administrator assignment that public
bootstrap correctly refuses, so an `UNINITIALIZED` installation with an unrelated assignment has no
supported recovery.

All supported fresh-install instructions MUST instead create the first identity in `/setup`, show its
temporary password once, and sign in only after acknowledgment. No guide or CLI help may require a
pre-created demo identity for first setup.

### 10.2 Host-only recovery command

Add `easysynq setup release-administrator-blocker --subject <keycloak-subject> [--org CODE]` as an
exceptional host recovery command. It MUST:

- load and lock the organization `system_config` row and require `UNINITIALIZED` setup;
- acquire the same per-organization administrator-set advisory lock after the singleton lock;
- resolve the exact organization user by the supplied Keycloak subject;
- refuse to remove the administrator assignment from the user linked to the active bootstrap claim;
- remove only that user's System Administrator role assignment when it is unrelated to the claim;
- preserve the Keycloak identity, `app_user`, every non-administrator role, and all historical
  attribution; and
- commit atomically or roll back without changing setup, claim, identity, or unrelated assignments.

The command is safely repeatable: an absent user or absent blocking assignment reports that nothing
was released and makes no write. It never deletes or disables an identity, advances setup, consumes a
bootstrap proof, or grants another role. The operator must mint or retain a valid setup secret and
resume the normal browser flow after the blocker is released.

This host action runs before a trusted authenticated application administrator exists, so it cannot
use the normal in-application audit actor. Output names only the operator-supplied subject and result;
no secret, password, claim marker, provider response, or unrelated administrator is disclosed. The
operator MUST record the command, operator, reason, subject, organization, and time in an independent
change or incident record. This deliberate boundary is registered as
[`uninitialized-admin-recovery`](../../debt/20260816173328-uninitialized-admin-recovery.md).

### 10.3 Alternatives

Adopting the unrelated administrator into the active public claim was rejected because it changes the
owner-approved create-first-account experience and would let a bootstrap proof take control of an
identity that lacks the claim marker. Direct SQL deletion was rejected because it bypasses the shared
lock protocol and cannot enforce exact setup, organization, role, or claim ownership. Automatically
removing every administrator was rejected because it is over-broad and could erase deliberate access
state. The exact-subject host command is the smallest recoverable intervention.

### 10.4 Verification

Focused RED/GREEN evidence MUST prove lock order, `UNINITIALIZED` gating, claim-owned refusal, exact
unrelated-assignment removal, preservation of identity/user/other roles, idempotent absence, rollback,
wrapper dispatch/help, and updated fresh-install instructions. The populated migration proof MUST
exercise both `0087 -> 0086 -> 0087` and `0088 -> 0087 -> 0088` boundaries before final evidence is
recorded. Generated Playwright artifacts remain disposable and MUST be absent at handoff.
