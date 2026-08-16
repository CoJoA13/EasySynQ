---
id: 20260816010910
title: keycloak-profile-reconciliation
principal: 1d
interest: +review each external realm-profile change
hotspot: apps/api/src/easysynq_api/services/keycloak_provisioning.py
business_capability: identity-provisioning
payoff_trigger: Keycloak exposes versioned/CAS user-profile updates, or supported external profile-policy administration is introduced
quadrant: prudent-deliberate
category: infrastructure
ai_authored: true
created: 2026-08-16
---

Keycloak 26.7 exposes user-profile policy as a whole document without an ETag or compare-and-swap token. EasySynQ must reconcile only the required flags for email, first name, and last name so its approved optional-profile onboarding contract works for fresh and existing realms; the implementation preserves every returned custom attribute and validator and writes only when reconciliation is needed. A simultaneous external administrator edit between GET and PUT could still be overwritten, so this boundary must be replaced or fenced when versioned mutation becomes available or external profile-policy administration becomes supported.
