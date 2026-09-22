---
description: Run the full local gate, then open a GitHub pull request against main
allowed-tools: Bash
---

Prepare and open a GitHub pull request for the current scoped branch. Protected `main` requires
the `gate` check green, a branch up to date with `main`, and every review conversation resolved;
direct and force pushes are disabled, and merges are squash-only.

1. Confirm we're on a feature branch (not `main`); if on `main`, stop and ask to branch first.
2. Run the full local gate before pushing:
   - `cd apps/api && uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest tests/unit -m unit`
   - `cd apps/web && npm run lint && npm run build && npm test`
   - `bash scripts/run-contract-tool.sh redocly lint --config packages/contracts/redocly.yaml packages/contracts/openapi.yaml` plus `bash scripts/gen-contracts.sh --check` if endpoints changed.
3. If all green: push the branch and use `gh pr create --base main` with a concise title and a body
   summarizing the final change and verification. The repository squash-merges with the PR body as
   the squash commit message, so the body is the permanent record of the slice — write it as such.
   Use the GitHub repository selected by the repository remote; authenticate through `gh auth login`
   without putting tokens in arguments.
4. Report the pull request URL. Do NOT merge — wait for the `gate` check to go green, the branch to
   be up to date with `main`, and review.

If any gate fails, stop and show the failure instead of opening the pull request.
