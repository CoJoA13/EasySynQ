---
description: Run the web loop — eslint + tsc typecheck + build
allowed-tools: Bash(cd:*), Bash(npm run:*)
---

Run the web quality loop:

```
cd apps/web && npm run lint && npm run typecheck && npm run build
```

Report pass/fail per stage. On failure, show the output and propose a fix.
