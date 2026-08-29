# 0002 — Use native primary controls for table actions

**Date:** 2026-08-13

**Status:** Accepted

## Context

EasySynQ's high-traffic register tables generally keep rows structural and put one native link or button
inside the identifying cell. Two remaining tables attach activation to `Table.Tr`: CAPA List duplicates
button keyboard behavior on a focusable row, while Audit Program leaves row selection unavailable to
keyboard users and also contains an independent Edit button.

The application needs a consistent semantic boundary that preserves table structure, browser keyboard
behavior, visible focus, useful accessible names, and independent nested actions without turning each row
into a custom composite widget.

## Decision

Keep table rows structural. When a row has one primary action, render a visible native link or button in
the identifying cell and let that control own focus, accessible naming, and Enter/Space activation.

Optional Arrow Up/Down enhancement may move focus between controls explicitly marked `data-rownav`, but
it does not remove them from normal tab order, activate them, follow selection with focus, or intercept
keys from an unmarked independent action. No row-level click handler, interactive role, `tabIndex`,
stretched overlay, or synthetic activation is used.

Apply the decision directly to the two current exceptions rather than adding a shared primary-row
component. Existing native controls remain unchanged.

## Consequences

Keyboard and assistive-technology users encounter ordinary links and buttons rather than simulated
interactive rows. Pointer users must activate the visible primary control instead of clicking any cell.
Nested actions such as Audit Program Edit remain independent, and the shared arrow helper needs a
narrow event-origin guard.

The direct implementation is deliberately small, but it provides no automatic prevention against a
future row-level click handler. Reviewers must carry the contract until the payoff trigger justifies a
shared abstraction or executable source guard.

## Alternatives

### Shared primary-row component

A shared component could enforce naming and styling, but the two current actions have different state,
copy, and side effects. The repository's existing Mantine link/button patterns already cover the reusable
part, so another abstraction would add API surface without a third consumer.

### Stretched or overlaid whole-row control

A stretched control preserves a large pointer target but becomes fragile around Audit Program's Edit
button. It creates stacking, click-exclusion, focus-indication, and accessible hit-area complexity.

### Interactive row with ARIA and synthetic keys

An interactive role, `tabIndex`, Enter/Space handler, and nested-control event suppression would
reimplement browser semantics incompletely while mixing table structure and control behavior.

## Payoff trigger

Revisit the direct pattern when a third table needs a primary row interaction or a row-level activation
regression is introduced. At that point, evaluate a focused shared component or executable source guard
while preserving native controls and structural rows.
