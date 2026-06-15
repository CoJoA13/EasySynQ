import { Button, Group, Menu, Paper, Text } from "@mantine/core";
import { useState } from "react";
import { ConfirmDestructive } from "../../lib/ConfirmDestructive";
import type { ImportDecisionAction, ImportDecisionAfter } from "../../lib/types";

// The selection-active context bar (mockup #screen-ingestion §6). Presentational only: ReviewCockpit
// (Task 14) owns the selection Set + wires these handlers to the Task-4 mutations (one Idempotency-Key
// per bulk op). Renders nothing when nothing is selected. R10 (D-5): "Confirm kind" is a kind-confirm
// over the *selection*; "Bulk accept all High" is the selector-based whole-bucket accept and must NOT
// confirm kind. Theme tokens via Mantine props / var(--es-*) only — never hardcoded hex.

// Representative corrective choices for the v1 bulk menus. The full picklist (driven by reference-data)
// is a follow-on; these cover the operator journey + keep the bar entirely client-side.
const TYPE_CHOICES = ["SOP", "WI", "FORM", "POLICY"] as const;

export function BulkActionBar({
  count,
  onBulk,
  onConfirmKind,
  onAcceptAllHigh,
}: {
  count: number;
  onBulk: (action: ImportDecisionAction, after?: ImportDecisionAfter) => void;
  onConfirmKind: (kind: "DOCUMENT" | "RECORD") => void;
  onAcceptAllHigh: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  if (count <= 0) return null;

  return (
    <Paper
      component="section"
      aria-label="Bulk actions"
      withBorder
      p="sm"
      mb="md"
      style={{ borderColor: "var(--es-accent)", background: "var(--es-surface-2)" }}
    >
      <Group gap="sm" wrap="wrap" align="center">
        <Text size="sm">
          <Text span fw={700}>
            {count} items selected
          </Text>{" "}
          in this view
        </Text>

        {/* Confirm kind — the R10 human act over the selection. */}
        <Menu position="bottom-start" withinPortal={false}>
          <Menu.Target>
            <Button variant="subtle" size="xs" aria-label="Confirm kind for selected">
              Confirm kind ▾
            </Button>
          </Menu.Target>
          <Menu.Dropdown>
            <Menu.Item onClick={() => onConfirmKind("DOCUMENT")}>Document</Menu.Item>
            <Menu.Item onClick={() => onConfirmKind("RECORD")}>Record</Menu.Item>
          </Menu.Dropdown>
        </Menu>

        {/* Correct to type — a representative type picklist → correct decision with `after.type_code`. */}
        <Menu position="bottom-start" withinPortal={false}>
          <Menu.Target>
            <Button variant="subtle" size="xs" aria-label="Correct to type for selected">
              Correct to type ▾
            </Button>
          </Menu.Target>
          <Menu.Dropdown>
            {TYPE_CHOICES.map((code) => (
              <Menu.Item key={code} onClick={() => onBulk("correct", { type_code: code })}>
                {code}
              </Menu.Item>
            ))}
          </Menu.Dropdown>
        </Menu>

        {/* Reassign owner — representative item; the full owner picker is a follow-on. */}
        <Menu position="bottom-start" withinPortal={false}>
          <Menu.Target>
            <Button variant="subtle" size="xs" aria-label="Reassign owner for selected">
              Reassign owner ▾
            </Button>
          </Menu.Target>
          <Menu.Dropdown>
            <Menu.Item onClick={() => onBulk("correct", { owner: "Quality Manager" })}>
              Quality Manager
            </Menu.Item>
          </Menu.Dropdown>
        </Menu>

        {/* Set clause — representative item; the full clause tree is a follow-on. */}
        <Menu position="bottom-start" withinPortal={false}>
          <Menu.Target>
            <Button variant="subtle" size="xs" aria-label="Set clause for selected">
              Set clause ▾
            </Button>
          </Menu.Target>
          <Menu.Dropdown>
            <Menu.Item onClick={() => onBulk("correct", { clause_numbers: ["8.4"] })}>
              8.4 — Control of external provision
            </Menu.Item>
          </Menu.Dropdown>
        </Menu>

        <Button
          variant="subtle"
          size="xs"
          color="red"
          aria-label="Exclude selected"
          onClick={() => setConfirming(true)}
        >
          Exclude
        </Button>
        <ConfirmDestructive
          opened={confirming}
          onCancel={() => setConfirming(false)}
          onConfirm={async () => {
            onBulk("exclude");
            setConfirming(false);
          }}
          title={`Exclude ${count} item${count === 1 ? "" : "s"}?`}
          consequence="Excludes the selected items from this import — they won't be committed. You can re-include them before commit."
          confirmLabel="Exclude items"
          irreversible={false}
        />

        {/* Selector-based whole-bucket accept — distinct from Confirm kind; never confirms kind (D-5). */}
        <Button
          variant="default"
          size="xs"
          aria-label="Bulk accept all High"
          onClick={onAcceptAllHigh}
        >
          Bulk accept all High ✓
        </Button>

        <Text size="xs" c="dimmed" style={{ marginInlineStart: "auto" }}>
          Bulk actions are fully keyboard-driven · setting kind here counts as your confirmation
        </Text>
      </Group>
    </Paper>
  );
}
