---
id: 20260813074918
title: native-row-control-pattern
principal: 1d
interest: +review per new interactive table
hotspot: apps/web/src
business_capability: accessibility
payoff_trigger: a third table needs primary row interaction or row-level activation regresses
quadrant: prudent-deliberate
category: code_quality
ai_authored: true
created: 2026-08-13
---

The slice deliberately applies visible native primary controls directly at the two current row-click exceptions instead of adding a shared row-action component or lint rule. This keeps the change small and aligned with existing Mantine patterns, but future tables could reintroduce row-level activation unless reviewers carry the contract. Revisit the abstraction or add an executable source guard when a third interactive table needs the pattern or row-level activation regresses.
