---
id: 20260816173328
title: uninitialized-admin-recovery
principal: 2d
interest: +manual incident record per recovery
hotspot: apps/api/src/easysynq_api/cli/setup.py
business_capability: identity-provisioning
payoff_trigger: a trusted authenticated or host-attested workflow can adopt or resolve the pre-existing administrator with durable in-application audit
quadrant: prudent-deliberate
category: infrastructure
ai_authored: true
created: 2026-08-16
---

A host-only setup recovery may remove one explicitly named unrelated System Administrator assignment while setup is UNINITIALIZED so the browser can create the approved first administrator. The command preserves the Keycloak identity and EasySynQ user row but operates before an authenticated application administrator exists, so it cannot use the normal application audit actor and requires an independent change or incident record. Replace this exceptional path when pre-auth recovery can be durably attested inside the product.
