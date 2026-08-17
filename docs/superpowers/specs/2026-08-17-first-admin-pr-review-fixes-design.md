# First-administrator PR review fixes

**Date:** 2026-08-17

**Status:** Accepted by owner

## Context

PR #466 is functionally complete, but its final GitHub Actions run exposed one narrow-width browser
failure and its latest automated review exposed three operator-recovery gaps. Five older unresolved
review threads describe behavior already implemented by the review-hardening commits and need evidence
replies rather than additional code.

The Chromium failure is real even though the focused test passes on the developer host: on GitHub's
Ubuntu/Chromium font metrics, the non-wrapping **Issue a new temporary password** button ends at
332.09 px in a 320 px viewport. The test correctly rejects the overflow and must not be weakened.

The remaining findings are:

- `bootstrap_administrator_exists` falls through to generic retry guidance although only the documented
  host recovery can remove the unrelated administrator assignment;
- `user_exists` and `keycloak_email_exists` share guidance even though a bound username cannot be
  changed while the email can be corrected; and
- `mint-bootstrap` rotates the PostgreSQL proof but leaves the Redis failed-proof bucket intact, so a
  trusted remint can remain rate-limited until the previous TTL expires.

## Decision

### Narrow-width recovery action

Keep the full accessible name **Issue a new temporary password**, but shorten the visible button label
to **Issue new password** and bound the button to its container. This retains the explicit recovery
meaning and 44 px target while avoiding font-dependent max-content overflow. The browser geometry test
continues to require the real control and document to stay inside 320 px.

### Actionable public error guidance

Map the three responses independently without rendering provider detail:

- `bootstrap_administrator_exists`: state that the existing administrator assignment blocks public
  setup and direct the operator to the documented host `release-administrator-blocker` procedure;
- `user_exists`: state that the bound username belongs to an unrelated identity, that changing the form
  username cannot recover the claim, and that a host identity administrator must resolve the collision;
- `keycloak_email_exists`: tell the operator to retain the bound username and enter another email.

No Keycloak subject, marker, raw title, or raw detail is rendered. The browser continues to own only
safe, stable-code copy.

### Trusted remint clears the failed-proof bucket

The host-only `mint-bootstrap` command keeps the `system_config` row locked, generates the replacement
proof, clears the existing global bootstrap-failure key through a synchronous Redis client, and only
then commits the replacement proof. If Redis cannot confirm deletion, the command aborts with an
operator-safe error and PostgreSQL rolls back, so it never prints a replacement proof that remains
known-throttled.

Redis deletion and PostgreSQL commit cannot be atomic. A database commit failure after Redis deletion
can grant another bounded attempt budget to the old proof, but it grants no new authority and occurs
only during a trusted host remint. ADR 0005 and the existing bootstrap-admission/identity-coupling debt
record will document that ordering and its payoff trigger.

## Alternatives considered

### Let the reissue button wrap

Rejected. A two-line label conflicts with the existing exact 44 px action-target proof and adds visual
height to an already dense recovery state. Short visible copy with the full accessible name is clearer.

### Relax the viewport assertion

Rejected. The CI measurement demonstrates a real control outside the viewport; weakening the test would
turn an accessibility regression into a passing claim.

### Generation-scope every Redis failure key

Rejected for this PR. It would require changing the admission key derivation across public endpoints,
host tooling, tests, and possibly persisted generation state. It is a credible future replacement for
the existing cross-store coupling but is not necessary to make a trusted remint immediately usable.

### Delete the Redis key after committing PostgreSQL, best effort

Rejected. A deletion failure would commit and potentially print a new proof that the public endpoint
still rejects as rate-limited, reproducing the reported recovery failure.

## Verification

- RED/GREEN component tests for all three stable error codes, safe copy, and the unchanged full
  accessible reissue name;
- RED/GREEN setup integration proof that a threshold Redis counter is absent after remint and the new
  proof is immediately admitted;
- fail-closed proof that Redis reset failure leaves the stored bootstrap proof unchanged;
- focused and full setup/API static gates;
- focused first-administrator Chromium followed by the full 40-test Chromium cohort, one worker and zero
  retries;
- web unit, lint, build, authority, site-data, and diff checks;
- exact replies to all eight unresolved threads, followed by resolution only after the supporting commit
  is pushed and GitHub exposes the matching head.

## Boundaries

This correction does not change bootstrap authority, add a public endpoint, reveal identity-provider
detail, add a permission, change the contract or migration head, close a residual, or claim a new browser
engine. It does not change the host-only recovery command's database behavior or allow public callers to
clear rate limiting.
