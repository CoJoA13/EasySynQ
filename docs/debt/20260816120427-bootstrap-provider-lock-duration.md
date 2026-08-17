---
id: 20260816120427
title: bootstrap-provider-lock-duration
principal: 2d
interest: +latency/provider-outage
hotspot: apps/api/src/easysynq_api/services/setup/administrator.py
business_capability: identity-onboarding
payoff_trigger: identity provisioning and EasySynQ persistence gain a transactionally attested boundary
quadrant: prudent-deliberate
category: code_quality
ai_authored: true
created: 2026-08-16
---

Bootstrap deliberately holds the singleton row and per-org administrator advisory lock across Keycloak lookup/create/adopt and the EasySynQ persistence or definitive-release decision. This prevents stale claim release and unrelated administrator races, but provider latency or outage lengthens database lock duration and queues bootstrap or administrator writers for that organization. Preserve fail-closed rollback and bounded provider timeouts; remove the cross-system lock duration when identity provisioning and EasySynQ persistence gain one transactionally attested boundary.
