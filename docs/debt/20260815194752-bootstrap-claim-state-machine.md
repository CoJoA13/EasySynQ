---
id: 20260815194752
title: bootstrap-claim-state-machine
principal: 3d
interest: +1d/identity-provider-change
hotspot: apps/api/src/easysynq_api/services/setup
business_capability: identity-onboarding
payoff_trigger: identity provisioning and EasySynQ persistence gain one transactional boundary
quadrant: prudent-deliberate
category: code_quality
ai_authored: true
created: 2026-08-15
---

ADR 0005 deliberately introduces a staged bootstrap identity claim because Keycloak and PostgreSQL cannot commit atomically. The claim, Keycloak recovery marker, and active credential-receipt digest add state-machine and maintenance cost, but they prevent duplicate administrator grants, destructive compensation, and acknowledgment of a superseded temporary password after partial or concurrent failures. Remove this machinery if identity provisioning, credential delivery, and application persistence gain one transactionally attested boundary.
