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
