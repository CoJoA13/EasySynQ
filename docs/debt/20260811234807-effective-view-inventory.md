---
id: 20260811234807
title: effective-view-inventory
principal: 2d
interest: +review/new-material-selector
hotspot: apps/web/src/lib/effectiveView.ts
business_capability: navigation
payoff_trigger: Two independent feature teams add material query views in parallel, or classification requires feature data
quadrant: prudent-deliberate
category: code_quality
ai_authored: true
created: 2026-08-11
---

The centralized effective-view classifier deliberately creates one maintained inventory of material URL selectors. It keeps chrome and route recovery consistent now, but a future query-selected view can render correctly while receiving stale global behavior if its descriptor is omitted. ADR 0001 defines the trigger for replacing the static inventory with typed feature-contributed descriptors.
