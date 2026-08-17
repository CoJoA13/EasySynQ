---
id: 20260813234519
title: playwright-responsive-browser-harness
principal: 2d
interest: +review per browser cohort or fixture change
hotspot: apps/web/e2e
business_capability: accessibility
payoff_trigger: a material non-Chromium divergence or expansion beyond the focused responsive cohort
quadrant: prudent-deliberate
category: testing
ai_authored: true
created: 2026-08-13
---

ADR 0003 deliberately isolates responsive evidence behind a dedicated authenticated test entry, uses Chromium as the only engine, and starts with a single worker. The Playwright layer centrally owns synthetic fixtures so undeclared API and external traffic fail closed, trading broader engine and cohort coverage for deterministic focused evidence. Production authentication and the real Compose/Keycloak boundary now have a separate narrow live acceptance; this debt retains only the responsive engine/cohort boundary. Revisit it on a material non-Chromium divergence or expansion beyond the focused responsive cohort.
