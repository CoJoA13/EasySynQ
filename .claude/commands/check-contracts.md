---
description: Lint OpenAPI and verify its committed contract lock (mirrors the contracts CI job)
allowed-tools: Bash(npx:*), Bash(bash:*)
---

Reproduce the OpenAPI gates from the `contracts` CI job locally:

```
npx --yes @redocly/cli lint --config packages/contracts/redocly.yaml packages/contracts/openapi.yaml
bash scripts/gen-contracts.sh --check
```

Run after adding/changing any endpoint (document new endpoints in `openapi.yaml` in-PR). Report
pass/fail; on failure, show the offending rule + location and propose the fix.
