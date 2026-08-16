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

Keycloak 26.7 exposes both realm user-profile policy and individual user updates as whole representations without an ETag or compare-and-swap token. EasySynQ must reconcile only the required flags for email, first name, and last name at realm level, and a pending marker-bound bootstrap retry must reconcile only that user's email/name fields while preserving every other returned field and attribute. Application writers are serialized and both paths write only when reconciliation is needed, but a simultaneous external administrator edit between GET and PUT could still be overwritten; replace or fence these boundaries when versioned mutation becomes available or external profile administration becomes supported.
