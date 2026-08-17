---
id: 20260815215020
title: bootstrap-credential-lock
principal: 1d
interest: +latency/setup-reset
hotspot: apps/api/src/easysynq_api/services/setup/administrator.py
business_capability: identity-onboarding
payoff_trigger: identity provider password reset and PostgreSQL bootstrap state gain one transactional or fenced generation boundary
quadrant: prudent-deliberate
category: code_quality
ai_authored: true
created: 2026-08-15
---

First-administrator credential issuance deliberately holds the singleton system_config row lock across the Keycloak temporary-password reset and the credential receipt/state commit. The network call lengthens the PostgreSQL lock and makes acknowledgment/remint wait during a slow identity-provider response, but it prevents acknowledgment of the previous receipt while a reset is in flight. The receipt fence rejects a completed stale generation but does not make the external reset and PostgreSQL state transition atomic; replace the cross-system lock with a pending/active generation protocol or transactional provider boundary if setup availability or Keycloak latency makes this serialization cost material.
