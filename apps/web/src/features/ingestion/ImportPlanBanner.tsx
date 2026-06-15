import { Badge, Group, Paper, Stack, Text } from "@mantine/core";
import { IconShield } from "../../lib/icons";

// The drift-safe import-default explainer. INFORMATIONAL ONLY (D-6) — the real per-family
// revision-chain opt-in lives in the merge flow (reconstruct_revision_chain), NOT a global toggle
// here, so there is deliberately NO "Change plan" control. Copy mirrors mockup §3 verbatim. The
// shield icon is aria-hidden; the heading carries the meaning for assistive tech.
export function ImportPlanBanner() {
  return (
    <Paper withBorder p="md" radius="md">
      <Group gap="sm" align="flex-start" wrap="nowrap">
        <IconShield size={22} style={{ color: "var(--es-accent)", flexShrink: 0 }} />

        <Stack gap={4}>
          <Group gap="xs" align="center">
            <Text fw={600}>Import plan</Text>
            <Badge variant="light" color="var(--es-accent)">
              Default · drift-safe
            </Badge>
          </Group>
          <Text size="sm" c="dimmed" maw="72ch">
            Import the current version only as the controlled baseline (
            <Text span ff="monospace">
              Rev A · Effective
            </Text>
            ); older copies in each version family are archived as provenance, never asserted as
            approved history. Revision-chain reconstruction is opt-in per family and confirmed at
            commit. Exactly one Effective version per document — drift is eliminated at the source.
          </Text>
        </Stack>
      </Group>
    </Paper>
  );
}
