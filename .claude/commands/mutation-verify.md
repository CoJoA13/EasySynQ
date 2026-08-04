---
description: Prove a test actually fails when the behaviour it names is broken (the anti-false-PASS loop)
disable-model-invocation: true
---

Prove that a test genuinely constrains the behaviour it claims to. A green test is not evidence — a test
that passes against *broken* code is worse than no test, because it advertises a guarantee that does not
exist.

This is the repo's standing discipline (`engineering-patterns`: *"Prefer hunting the false-PASS direction
on any gate/proof"*), and it keeps paying. Real examples from this codebase:

- A password-policy test named for the exact safety property passed against the forbidden implementation
  in **~83% of runs** — the bug was probabilistic, so a single green run proved nothing.
- Deleting an authorization guard's entire per-user-override leg left the suite **16/16 green** — the
  half of the guard that mattered most was unprotected.
- A unit test *named* `..._reverifies_the_returned_username` was asserting the **unsafe** behaviour.

## The loop

For each behaviour the test claims to pin:

1. **Break exactly that behaviour** in the production code — the smallest edit that removes the
   guarantee, not a wholesale revert.
2. **Run the covering test.** It MUST fail, and fail *for the stated reason* — read the assertion
   message, don't just accept a red result. A test that fails with an unrelated error (import, fixture,
   timeout) is not verified.
3. **Restore exactly**, and confirm the production file is untouched:
   ```bash
   git diff -- <the production file>
   ```
   Empty output, or the mutation is still in your tree.
4. **Re-run** and confirm green again.

Repeat per behaviour. One mutation proves one property — a test that survives every mutation you can
think of is pinning nothing.

## Two traps this repo has hit

- ⚠ **Never use `git stash` when the semantics are already committed on the branch.** Stashing reverts
  your *uncommitted* work, not the behaviour under test, so the run is meaningless and reads as a pass.
  Hand-mutate the line in place and restore it in place.
- ⚠ **Mutate one thing at a time.** When several fixes live in one file, a whole-file revert also undoes
  the others and you learn nothing about which test covers what. Remove the single kwarg, the single
  branch, the single line.

## When a mutation does NOT fail the test

That is the finding. Do not adjust the mutation until it fails — the test is inadequate. Either it
asserts something trivially true, or its inputs never reach the branch in question (a very common shape:
an assertion that short-circuits on an earlier condition, so the interesting comparison never runs).
Strengthen the test, then re-verify.

## Report

For each mutation: what you broke, the real failing output, confirmation the restore left `git diff`
empty, and the passing re-run. If any mutation did not produce a failure, say so plainly and treat it as
a defect in the test rather than a nuisance to work around.
