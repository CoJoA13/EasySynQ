---
id: 20260814022608
title: browser-probe-hardening
principal: 2h
interest: +review per browser harness or Docker-context change
hotspot: apps/web/e2e
business_capability: accessibility
payoff_trigger: next browser harness or Docker-context change
quadrant: prudent-inadvertent
category: testing
ai_authored: true
created: 2026-08-14
---

The fail-closed child-probe harness has no explicit timeout-triggered child termination, so an abnormal hang could outlive the parent test timeout. The Docker reinclusion invariant also does not normalize leading `./` path components before comparing protected browser-only roots. Both are non-blocking after the slice review because normal completion and the committed Docker ignore set are proven, but the next harness or Docker-context change should close these mutation gaps.
