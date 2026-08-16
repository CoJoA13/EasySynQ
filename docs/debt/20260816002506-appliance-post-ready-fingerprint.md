---
id: 20260816002506
title: appliance-post-ready-fingerprint
principal: 1d
interest: +review/appliance-provisioner-change
hotspot: infra/appliance/provision/easysynq-provision.sh
business_capability: identity-onboarding
payoff_trigger: a maintained shell AST/policy validator is adopted for the appliance provisioner
quadrant: prudent-deliberate
category: testing
ai_authored: true
created: 2026-08-16
---

The first-administrator deployment guard fingerprints the approved post-ready appliance segment because ad hoc regex parsing cannot safely model Bash pipelines, modifiers, and executable paths. This makes any legitimate post-ready provisioner change require an explicit fingerprint review; replace it with a maintained shell AST/policy validator when one is available.
