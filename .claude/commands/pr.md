---
description: Run the full local gate, then open a PR against protected main
allowed-tools: Bash
---

Prepare and open a PR for the current `feat/sN-*` branch (main is protected — PR + green CI required).

1. Confirm we're on a feature branch (not `main`); if on `main`, stop and ask to branch first.
2. Run the full local gate before pushing:
   - `cd apps/api && uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest -m unit`
   - `cd apps/web && npm run lint && npm run typecheck && npm run build && npm test`
   - redocly lint on `packages/contracts/openapi.yaml` if endpoints changed.
3. If all green: push the branch and `gh pr create` with a concise title + body summarizing the slice.
   End the PR body with the Co-Authored-By / Generated-with trailers per the repo convention.
4. Report the PR URL. Do NOT merge — wait for green CI.

If any gate fails, stop and show the failure instead of opening the PR.
