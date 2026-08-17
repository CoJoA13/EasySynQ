---
id: 20260815215020
title: bootstrap-credential-lock
principal: 1d
interest: +latency/setup-reset
hotspot: apps/api/src/easysynq_api/services/setup/administrator.py
business_capability: identity-onboarding
payoff_trigger: identity-provider password reset no longer requires holding PostgreSQL locks across the network call
quadrant: prudent-deliberate
category: code_quality
ai_authored: true
created: 2026-08-15
---

First-administrator credential issuance deliberately holds the singleton `system_config` row lock and per-organization administrator lock across the Keycloak temporary-password reset and active receipt promotion. Before reissuing, it durably clears the prior receipt digest, then reacquires both locks in canonical order and revalidates the claim; the nullable digest is the pending-generation fence, so a failed post-reset promotion cannot resurrect acknowledgment authority for the inactive password. The network call still lengthens the PostgreSQL lock and makes acknowledgment/remint wait during a slow identity-provider response. Replace this cross-system lock duration when a provider-side fence or transactionally attested credential boundary can preserve the same pending/active invariant without holding database locks across the reset.
