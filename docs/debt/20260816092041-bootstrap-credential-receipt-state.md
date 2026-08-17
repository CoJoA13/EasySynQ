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

First-administrator acknowledgment persists a hash of a volatile credential receipt so it can prove that the operator acknowledged the currently active temporary-password generation. The existing nullable digest also encodes a durable pending generation: reissue clears and commits the active digest before Keycloak reset, then promotes a new digest only after reset; a failed promotion therefore leaves every receipt superseded while retaining the truthful prior issuance timestamp. This extra state is necessary because Keycloak password reset and EasySynQ acknowledgment cannot share one transaction; it adds migration, contract, retry, and recovery complexity until credential delivery can be transactionally attested across both systems.
