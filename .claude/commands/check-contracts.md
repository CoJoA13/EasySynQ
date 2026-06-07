---
description: Lint the OpenAPI contract with redocly (mirrors the contracts CI job)
allowed-tools: Bash(npx:*)
---

Reproduce the `contracts` CI job locally — redocly-lint the living API contract:

```
npx --yes @redocly/cli lint --config packages/contracts/redocly.yaml packages/contracts/openapi.yaml
```

Run after adding/changing any endpoint (document new endpoints in `openapi.yaml` in-PR). Report
pass/fail; on failure, show the offending rule + location and propose the fix.
