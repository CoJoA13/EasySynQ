---
description: Triage GitLab merge-request review findings — verify each in code, fix or file, reply on every discussion, resolve only what you addressed
disable-model-invocation: true
---

Triage the review findings on an open GitLab merge request (Codex, or any automated reviewer). The goal is an honest
record: every finding assessed, every thread answered, and the thread state matching reality.

## 1. Fetch everything, including what arrived while you worked

```bash
glab api 'projects/:id/merge_requests/<iid>/discussions?per_page=100' --paginate \
  | jq '.[] | select(any(.notes[]; .resolvable == true and .resolved == false))
        | {id, notes: [.notes[] | {id, body, position, resolvable, resolved}]}'
```

The repository remote selects the GitLab project; `:id` is expanded by `glab`. Keep each discussion ID
and its note IDs distinct. A reviewer may post a **new round while you are working** — re-fetch before
resolving anything. Authenticate with `glab auth login`; never put a token in a command argument.

## 2. Verify each finding in the code

**Never triage from the finding text alone.** Open the file, read the branch, confirm the failure
scenario is reachable. Findings have been right about a real defect and wrong about its cause, and one
suspected trap turned out to be the reviewer's own error.

Equally: do not dismiss a finding because it is inconvenient. This repo's automated rounds have surfaced a
genuine account-takeover path, a stale-comment-driven false claim, and two contract omissions no gate
could see.

For each, decide:
- **Introduced by this merge request** → fix it.
- **Pre-existing** (the same defect exists on `main`) → file it, and say so on the thread with evidence.
  Check with `git show main:<file>` rather than asserting it.
- **Not valid** → say why, concretely.

## 3. Fix, or file — not both, and not neither

Fixing: one subagent per coherent group, not per finding. Mutation-verify every fix (`/mutation-verify`).

Filing: a GitLab issue per coherent change, grouped where several findings are genuinely **one** fix
(three surfaces describing the same stale rule = one issue). Each issue needs the failure scenario, the
affected paths, a suggested fix, and a link back to the thread. Run the R61 check over issue bodies
before publishing — they are public prose.

⚠ **When rounds stop converging** — a round finding variants rather than new classes — say so plainly
and propose filing the remainder. Do not chase indefinitely; that is the owner's call to make with real
information.

## 4. Reply on every thread

```bash
# Write the exact reply to this local text file first, preserving newlines.
glab api -X POST 'projects/:id/merge_requests/<iid>/discussions/<discussion_id>/notes' \
  -F body=@/tmp/review-reply.txt
```

State what changed and where (commit SHA), or why it was not changed. End each with the automation
signature line if the workflow asks for one. A reply that just says "fixed" is not useful to the next
reader — name the mechanism.

## 5. Resolve ONLY what you addressed

```bash
glab api -X PUT 'projects/:id/merge_requests/<iid>/discussions/<discussion_id>' \
  -F resolved=true
```

⚠ **The trap, hit in this repo:** a loop written over an earlier thread list swept up five threads from a
round that arrived mid-triage, marking them handled without them ever being read. Resolving is a claim
that a human can rely on.

- Re-fetch the unresolved list immediately before resolving.
- Resolve by **explicit id list** of what you triaged — never "everything currently unresolved".
- Leave open anything you deferred to the owner, and say on the thread that you left it open.
- If you resolve something in error, repeat the same GitLab update with `-F resolved=false` and
  correct the record out loud.

## 6. Report

Threads triaged, fixed vs filed (with issue numbers), what remains open and why, and the gate results
after any fixes.
