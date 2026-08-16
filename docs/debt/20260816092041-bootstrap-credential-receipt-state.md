---
id: 20260816092041
title: bootstrap-credential-receipt-state
principal: 2d
interest: +review/credential-reissue-change
hotspot: apps/api/src/easysynq_api/services/setup/administrator.py
business_capability: identity-onboarding
payoff_trigger: identity-provider credential issuance and EasySynQ acknowledgment gain one transactionally attested delivery boundary
quadrant: prudent-deliberate
category: code_quality
ai_authored: true
created: 2026-08-16
---

First-administrator acknowledgment will persist a hash of a volatile credential receipt so it can prove that the operator acknowledged the currently active temporary-password generation. This extra state is necessary because Keycloak password reset and EasySynQ acknowledgment cannot share one transaction; it adds migration, contract, retry, and recovery complexity until credential delivery can be transactionally attested across both systems.
