---
id: 20260816024758
title: bootstrap-admission-identity-coupling
principal: 2d
interest: +review/identity-provider-change
hotspot: apps/api/src/easysynq_api/services/setup/service.py
business_capability: identity-onboarding
payoff_trigger: bootstrap admission moves to one transactional datastore or a supported identity provider exposes different documented canonicalization or atomic limiter semantics
quadrant: prudent-deliberate
category: code_quality
ai_authored: true
created: 2026-08-16
---

ADR 0005 deliberately couples bootstrap admission to a custom Redis Lua counter plus PostgreSQL singleton serialization, and defines EasySynQ local usernames by Keycloak-compatible strip-and-lowercase canonicalization. This keeps racing invalid proofs within one expiring budget and makes identity retries recoverable on the supported provider, but it adds datastore-specific admission logic and identity-provider normalization knowledge to the application boundary. Revisit both mechanisms when admission can use one transactional store or a supported identity provider exposes different documented canonicalization or atomic limiter semantics.
