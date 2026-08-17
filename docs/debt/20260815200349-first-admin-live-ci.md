---
id: 20260815200349
title: first-admin-live-ci
principal: 2d
interest: +review per identity/bootstrap change
hotspot: .github/workflows
business_capability: identity-onboarding
payoff_trigger: CI runtime budget and stable full-stack Docker capacity are approved for the live Keycloak gate
quadrant: prudent-deliberate
category: testing
ai_authored: true
created: 2026-08-15
---

The first-administrator slice requires a Docker-backed Keycloak Chromium acceptance before handoff, but does not add that expensive full-stack flow to every pull-request CI run. The executable harness remains a release/handoff gate so mock-only tests cannot satisfy the identity boundary. Move it into required CI when runner capacity and runtime budget are explicitly approved.
