# 0003 — Use Playwright for responsive browser evidence

**Date:** 2026-08-13

**Status:** Accepted

## Context

EasySynQ's nine-route shared-register cohort needs durable real-browser evidence for the responsive
contract. The existing jsdom suite proves component structure and semantics, but it cannot measure actual
viewport overflow, localized horizontal reachability, browser focus treatment, forced-colors behavior, or
request-intercepted recovery.

The production application mounts its OIDC provider and redirects an unauthenticated browser through
Keycloak. Coupling responsive evidence to that separate security boundary would require a live stack or a
production authentication shortcut. The repository also has no browser-test runtime, fixture boundary, or
CI gate today.

## Decision

Add `@playwright/test` as a locked development dependency and use a dedicated test-only Vite entry that
mounts the production application route tree and components with deterministic authenticated context. The
dedicated entry is never referenced by the production build or deployment inputs.

Chromium is the only browser engine. The harness uses one worker by default, owns its synthetic fixtures
centrally, and intercepts all browser traffic: undeclared API requests and external HTTP(S) traffic fail
closed. The stable `web` aggregate requires the browser job in addition to the existing Vitest shards, so
responsive browser failures cannot be hidden behind a green conventional web check.

## Consequences

The shared-register cohort gains repeatable viewport, overflow, focus, forced-colors, recovery, and
browser-exposed semantic evidence without adding a production authentication bypass or requiring Docker,
FastAPI, PostgreSQL, or Keycloak. The separate entry and fail-closed fixtures keep failures deterministic
and scoped to the frontend contract.

The repository must maintain the Playwright dependency, Chromium installation in CI, dedicated entry,
fixture map, and required browser job. Chromium-only evidence does not claim Firefox, WebKit, or actual
assistive-technology acceptance. One worker favors deterministic diagnostics over suite throughput.

## Alternatives

### Production entry with emulated OIDC

Emulating OIDC discovery, authorization, callback, token exchange, and session behavior would exercise the
production root, but it would couple responsive evidence to a separate security boundary and risk
normalizing a browser-only production-authentication shortcut.

### Playwright component testing

Direct component mounts would simplify authentication, but they would not exercise the real browser
router, application shell, route chrome, document overflow, and page-owned scroll boundary together.

### Live Docker and Keycloak stack

A seeded live stack would provide broader integration evidence, but it would pull database, setup,
identity, deployment, fixture lifecycle, and host-runtime availability into a focused responsive frontend
slice.

### Multi-engine or manual-only evidence

Firefox and WebKit would broaden compatibility evidence but materially increase the first gate's CI and
maintenance scope. Manual screenshots or a PR checklist would prove only one run and would not prevent the
responsive contract from regressing.

## Payoff trigger

Revisit the dedicated entry, engine matrix, fixture ownership, worker count, and live-stack boundary on
production-auth browser acceptance, a material non-Chromium divergence, or expansion beyond the focused
cohort.

## Reassessment — 2026-08-15

The Records register and detail routes expand the focused responsive-browser cohort. They continue to use
the dedicated authenticated test entry, centrally owned fail-closed fixtures, Chromium as the only engine,
one worker, zero retries, and the synthetic frontend boundary rather than a live application stack.

This focused-cohort expansion does not otherwise fire the payoff trigger. Production-auth browser
acceptance, a material non-Chromium divergence, and a reason to revisit fixture ownership, worker count,
or the live-stack boundary remain unproven.
