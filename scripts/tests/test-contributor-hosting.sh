#!/usr/bin/env bash
# Structural guard for contributor entry points; no downloads or installed hook tools required.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd -P)"
python3 - "$root" <<'PY'
from pathlib import Path
import re
import sys

root = Path(sys.argv[1])
config = (root / '.pre-commit-config.yaml').read_text()
assert re.findall(r'^  - repo: (.+)$', config, re.M) == ['local'], 'hooks still clone a remote repository'
for name in ['ruff', 'ruff-format', 'mypy', 'end-of-file-fixer', 'trailing-whitespace',
             'check-merge-conflict', 'check-yaml', 'check-added-large-files', 'gitleaks',
             'repository-authority', 'contracts-lint']:
    assert f'- id: {name}\n' in config, f'missing existing hook: {name}'
assert 'ruff check --force-exclude --fix' in config
assert 'ruff format --force-exclude' in config
assert 'gitleaks protect --verbose --redact --staged' in config
assert 'pre-commit-hooks==5.0.0' in config
assert '--allow-multiple-documents' in config
for path in ['.claude/commands/pr.md', '.claude/commands/triage-review.md']:
    text = (root / path).read_text()
    assert not re.search(r'(?m)^glab |`glab ', text), f'retired hosting CLI in {path}'
    assert re.search(r'(?m)^gh |`gh ', text), f'GitHub workflow missing from {path}'
for path in ['docs/manuals/installation-guide.md', 'docs/runbooks/install-ubuntu-server.md']:
    assert 'git clone https://github.com/CoJoA13/EasySynQ.git' in (root / path).read_text()
print('contributor hosting contracts: passed')
PY
