---
description: Run the API fast loop — ruff lint + format-check + mypy-strict + unit tests
allowed-tools: Bash(cd:*), Bash(uv run:*)
---

Run the canonical API quality loop (unit tests only; integration needs Docker):

```
cd apps/api && uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest
```

Report pass/fail per stage. If anything fails, show the failing output and propose a fix.
