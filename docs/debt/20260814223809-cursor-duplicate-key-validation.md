---
id: 20260814223809
title: cursor-duplicate-key-validation
principal: 30m
interest: +security review per cursor codec change
hotspot: apps/api/src/easysynq_api/services/records/listing.py
business_capability: evidence-operations
payoff_trigger: before the first external client compatibility commitment or cursor format version change
quadrant: prudent-deliberate
category: code_quality
ai_authored: true
created: 2026-08-14
---

The Records cursor decoder validates the effective key set after ordinary JSON decoding, so duplicate JSON member names collapse before validation and are accepted. The task reviewer classified duplicate-key rejection as non-blocking hardening; final-branch review will triage it while the current fix round stays scoped to the malformed fingerprint error distinction.
