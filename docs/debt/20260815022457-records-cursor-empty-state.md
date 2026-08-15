---
id: 20260815022457
title: records-cursor-empty-state
principal: 30m
interest: +review per Records empty-state change
hotspot: apps/web/src/features/records/RecordsPage.tsx
business_capability: evidence-operations
payoff_trigger: before Records browser acceptance or supported production
quadrant: prudent-inadvertent
category: code_quality
ai_authored: true
created: 2026-08-15
---

The Records register currently treats any URL state as filtered, including a cursor-only page. A valid unfiltered final cursor page with no rows therefore shows the filtered-empty message instead of the unfiltered-empty message. Task 8 review classified this as nonblocking; pay it down before browser acceptance or supported production by deriving the state from record criteria excluding cursor and adding a cursor-only empty-page regression test.
