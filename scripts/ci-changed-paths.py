#!/usr/bin/env python3
"""Decide which CI suites a run owes, from the paths it changed (.github/ci-paths.yml).

Runs inside the `changes` job of .github/workflows/ci.yml and writes the flags to $GITHUB_OUTPUT.
Outside a pull request the answer does not depend on paths at all: a tag or manually started run
owes everything, and a push to main owes only the backstops. Stdlib only — the runner's Python
has no PyYAML, and the lists file uses a deliberately tiny subset (top-level keys, `- item` lines,
`#` comments) so it can be parsed here and pinned by a unit test without a dependency.

    EVENT=pull_request BASE_REF=main python3 scripts/ci-changed-paths.py
    EVENT=push REF=refs/heads/main python3 scripts/ci-changed-paths.py
    EVENT=workflow_dispatch REF=refs/heads/main python3 scripts/ci-changed-paths.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LISTS = ROOT / ".github" / "ci-paths.yml"
FLAGS = ("code", "api_suites", "web_suites", "docs_only", "full", "main_push")


def _git() -> str:
    resolved = shutil.which("git")
    if resolved is None:
        raise RuntimeError("git is required to decide changed paths")
    return resolved


def read_lists(path: Path = LISTS) -> dict[str, list[str]]:
    """The tiny YAML subset the lists file is allowed to use. Anything else is a hard error, so a
    future edit that needs real YAML fails here rather than silently selecting nothing."""
    lists: dict[str, list[str]] = {}
    current: str | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if line.startswith("- ") or line.startswith("  - "):
            if current is None:
                raise ValueError(f"{path}: list item before any key: {raw!r}")
            item = line.strip()[2:].strip()
            if not item or " " in item:
                raise ValueError(f"{path}: bad list item: {raw!r}")
            lists[current].append(item)
        elif line.endswith(":") and not line.startswith(" "):
            current = line[:-1].strip()
            if not current or current in lists:
                raise ValueError(f"{path}: bad or repeated key: {raw!r}")
            lists[current] = []
        else:
            raise ValueError(f"{path}: unsupported line: {raw!r}")
    for key in ("code", "api_suites", "web_suites"):
        if not lists.get(key):
            raise ValueError(f"{path}: missing or empty list: {key}")
    return lists


def matches(path: str, patterns: list[str]) -> bool:
    """`<dir>/**` matches everything under that directory; anything else is an exact root file."""
    for pattern in patterns:
        if pattern.endswith("/**"):
            if path.startswith(pattern[:-2]):
                return True
        elif "*" in pattern:
            raise ValueError(f"only <dir>/** or an exact root file name is allowed: {pattern}")
        elif path == pattern:
            return True
    return False


def is_docs(path: str) -> bool:
    return path.startswith("docs/") or ("/" not in path and path.endswith(".md"))


def diff_names(base: str, head: str = "HEAD", *, cwd: Path = ROOT) -> list[str]:
    """Every path a change touches, on BOTH sides of a rename.

    `git diff --name-only` reports only the destination of a detected rename, so moving
    `apps/api/x.py` to `docs/x.py` would read as a docs-only change while production code was
    removed. `--no-renames` makes the source appear as a deletion and the destination as an
    addition, and both then take part in suite selection.
    """
    out = subprocess.run(  # noqa: S603 - resolved binary, fixed arguments
        [_git(), "diff", "--name-only", "--no-renames", f"{base}...{head}"],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [line for line in out.splitlines() if line]


def changed_files(base_ref: str) -> list[str]:
    # The PR run checks out the merge commit; `origin/<base>...HEAD` lists what the PR itself
    # changes relative to the base, which is exactly what merge evidence should key on.
    subprocess.run(  # noqa: S603 - resolved binary, fixed arguments
        [_git(), "fetch", "--no-tags", "--depth=1", "origin", base_ref],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return diff_names(f"origin/{base_ref}")


def decide(event: str, ref: str, base_ref: str, files: list[str] | None = None) -> dict[str, bool]:
    lists = read_lists()
    if event == "pull_request":
        paths = changed_files(base_ref) if files is None else files
        code = any(matches(p, lists["code"]) for p in paths)
        # Belt and braces: a path that is neither docs nor listed code must still count as code,
        # or an unlisted new file would silently ride the docs lane. The unit test forbids such
        # paths in the tracked tree; this keeps an untracked-at-test-time one honest at run time.
        code = code or any(not is_docs(p) for p in paths)
        return {
            "code": code,
            "api_suites": any(matches(p, lists["api_suites"]) for p in paths),
            "web_suites": any(matches(p, lists["web_suites"]) for p in paths),
            "docs_only": bool(paths) and not code,
            "full": False,
            "main_push": False,
        }
    if event == "push" and ref.startswith("refs/tags/v"):
        return dict.fromkeys(FLAGS, True) | {"docs_only": False, "main_push": False}
    if event == "push":
        return dict.fromkeys(FLAGS, False) | {"main_push": True}
    # workflow_dispatch and anything else that starts a run by hand: everything.
    return dict.fromkeys(FLAGS, True) | {"docs_only": False, "main_push": False}


def main() -> int:
    flags = decide(
        os.environ.get("EVENT", ""),
        os.environ.get("REF", ""),
        os.environ.get("BASE_REF", "main"),
    )
    lines = [f"{key}={'true' if value else 'false'}" for key, value in flags.items()]
    print("\n".join(lines))
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
