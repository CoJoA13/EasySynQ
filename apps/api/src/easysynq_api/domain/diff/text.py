"""Pure text redline (slice S-dcr-3a; doc 05 §8.1/§8.3). No I/O.

Line-level LCS via stdlib ``difflib.SequenceMatcher`` over the two extracted texts → an ordered
list of hunks (``equal`` / ``insert`` / ``delete``), the inline-redline shape doc 05 §8.3 renders
(additions / deletions). Line-level (not word-level) matches the §8.3 worked example, which
anchors hunks at §-headed lines; it is also the cheapest faithful diff of "what the procedure
says" given the N4 fidelity note (we diff extracted text, never claim WYSIWYG). A ``replace``
opcode is decomposed into a ``delete`` (old lines) + an ``insert`` (new lines).
"""

from __future__ import annotations

import dataclasses
import difflib

# Cap the diffed text so a pathological huge extraction can't blow the response / CPU (the
# import_max_extract_text_bytes spirit). Beyond this the texts are truncated before diffing.
_MAX_CHARS = 1_000_000


@dataclasses.dataclass(frozen=True)
class Hunk:
    op: str  # "equal" | "insert" | "delete"
    text: str  # the joined lines for this hunk (newline-separated, no trailing newline)


def redline(old_text: str, new_text: str) -> list[Hunk]:
    """An ordered line-level redline of ``old_text`` → ``new_text``. ``insert`` = lines added in the
    new version, ``delete`` = lines removed from the old, ``equal`` = unchanged runs."""
    old_lines = old_text[:_MAX_CHARS].splitlines()
    new_lines = new_text[:_MAX_CHARS].splitlines()
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    hunks: list[Hunk] = []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            hunks.append(Hunk("equal", "\n".join(old_lines[i1:i2])))
        elif op == "delete":
            hunks.append(Hunk("delete", "\n".join(old_lines[i1:i2])))
        elif op == "insert":
            hunks.append(Hunk("insert", "\n".join(new_lines[j1:j2])))
        else:  # replace → delete the old run, then insert the new run
            hunks.append(Hunk("delete", "\n".join(old_lines[i1:i2])))
            hunks.append(Hunk("insert", "\n".join(new_lines[j1:j2])))
    return hunks
