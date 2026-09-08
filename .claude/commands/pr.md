---
description: Run the full local gate, then open a GitLab merge request against main
allowed-tools: Bash
---

Prepare and open a GitLab merge request for the current scoped branch. Protected `main` requires
a successful pipeline and resolved discussions; direct and force pushes are disabled.

1. Confirm we're on a feature branch (not `main`); if on `main`, stop and ask to branch first.
2. Run the full local gate before pushing:
   - `cd apps/api && uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest tests/unit -m unit`
   - `cd apps/web && npm run lint && npm run build && npm test`
   - `bash scripts/run-contract-tool.sh redocly lint --config packages/contracts/redocly.yaml packages/contracts/openapi.yaml` plus `bash scripts/gen-contracts.sh --check` if endpoints changed.
3. If all green: push the branch and use `glab mr create --target-branch main` with a concise title
   and description summarizing the final change and verification. Use the GitLab project selected by
   the repository remote; authenticate through `glab auth login` without putting tokens in arguments.
4. Report the merge request URL. Do NOT merge — wait for green CI and review.

If any gate fails, stop and show the failure instead of opening the merge request.
