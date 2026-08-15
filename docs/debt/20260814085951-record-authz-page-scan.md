---
id: 20260814085951
title: record-authz-page-scan
principal: 3d
interest: +latency and database work per deeply scoped page
hotspot: apps/api/src/easysynq_api/api/records.py
business_capability: evidence-operations
payoff_trigger: one GET /records page must scan more than 2000 matching candidates
quadrant: prudent-deliberate
category: code_quality
ai_authored: true
created: 2026-08-14
---

Cursor pages will evaluate the canonical Python PDP over deterministic candidate batches until they contain a full readable page, preserving process scope, predicates, correction fallback, and deny-always-wins without creating a second authorization engine. This favors authorization correctness over a new SQL grant compiler during the first read-console slice. When one page must scan more than 2,000 matching candidates, introduce a proven SQL candidate predicate or readable-ID projection that remains equivalent to the canonical PDP.
