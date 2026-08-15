---
id: 20260815111629
title: generated-contract-eof-hook
principal: 2h
interest: +5min/contract-change
hotspot: scripts/gen-contracts.sh
business_capability: contract-delivery
payoff_trigger: next generated contract change
quadrant: prudent-inadvertent
category: code_quality
ai_authored: true
created: 2026-08-15
---

The Redocly bundle command emits packages/contracts/dist/openapi.json without a final newline and the contract lock hashes those exact bytes, while the global pre-commit end-of-file fixer rewrites the artifact and aborts the commit. This final-review wave therefore preserves the verified generator output and narrowly skips only that incompatible formatting hook after all substantive hooks pass. Close by making generated output and hook policy agree, proving contracts-check stays clean and a normal contract commit no longer needs a skip.
