---
id: 20260815011105
title: related-label-authz-test-hardening
principal: 1d
interest: +security review per related-label family change
hotspot: apps/api/tests/integration/test_records.py
business_capability: evidence-operations
payoff_trigger: before adding another related-label family or supported production deployment
quadrant: prudent-deliberate
category: testing
ai_authored: true
created: 2026-08-15
---

The related-label tests prove independent permission keys and restricted fallbacks, but they do not yet behaviorally cover a related-object explicit DENY, time/IP predicate, or the absence of appended authorization-audit events. The Task 5 reviewer classified this as non-blocking security-test hardening; final-branch review will triage it while the active fix round stays scoped to single-gather behavior and production SQL boundedness.
