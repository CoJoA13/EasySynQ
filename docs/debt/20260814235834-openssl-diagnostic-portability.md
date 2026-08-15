---
id: 20260814235834
title: openssl-diagnostic-portability
principal: 30m
interest: +one failed API unit/full gate per OpenSSL wording variant
hotspot: apps/api/tests/unit/test_upload_identity_rollback_runbook.py
business_capability: contributor-workflow
payoff_trigger: before Task 11 full-check evidence or branch handoff
quadrant: prudent-inadvertent
category: testing
ai_authored: true
created: 2026-08-14
---

The upload-identity rollback runbook test expects invalid-certificate stderr to contain `Could not read certificate`, while this host's OpenSSL emits the semantically equivalent `Could not find certificate from <path>`. The product rejection still occurs, but the wording-specific assertion makes the full API unit gate environment-sensitive; this unrelated failure must be diagnosed or explicitly left failed before handoff.
