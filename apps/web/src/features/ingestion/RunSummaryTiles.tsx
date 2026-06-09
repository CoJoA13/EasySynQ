import { Group, Paper, SimpleGrid, Stack, Text } from "@mantine/core";
import type { ImportRun } from "../../lib/types";

// run.counts is stage-namespaced + loosely typed (Record<string, unknown> | null). Walk it safely:
// every step degrades to 0 on a missing/non-object node, so a partial or null counts never crashes
// and never yields NaN (noUncheckedIndexedAccess — no bare index without a fallback). DP-7: every
// tile is glyph + label + value (never color-only); aria-labels are distinct per tile.
function countAt(counts: Record<string, unknown> | null, ...path: string[]): number {
  let node: unknown = counts;
  for (const key of path) {
    if (node === null || typeof node !== "object") return 0;
    node = (node as Record<string, unknown>)[key];
  }
  return typeof node === "number" && Number.isFinite(node) ? node : 0;
}

function MetricTile({
  glyph,
  glyphColor,
  label,
  value,
  ariaValue,
}: {
  glyph: string;
  glyphColor: string;
  label: string;
  value: string;
  ariaValue: string;
}) {
  return (
    <Paper
      withBorder
      p="md"
      radius="md"
      role="group"
      aria-label={`${label}: ${ariaValue}`}
    >
      <Stack gap={4}>
        <Group gap="xs" justify="space-between" wrap="nowrap">
          <Text size="sm" c="dimmed">
            {label}
          </Text>
          <Text aria-hidden c={glyphColor}>
            {glyph}
          </Text>
        </Group>
        <Text fz="1.75rem" fw={700} ff="monospace">
          {value}
        </Text>
      </Stack>
    </Paper>
  );
}

export function RunSummaryTiles({ run }: { run: ImportRun }) {
  const counts = run.counts;
  const high = countAt(counts, "classify", "band", "HIGH");
  const medium = countAt(counts, "classify", "band", "MEDIUM");
  const needs = countAt(counts, "queues", "needs");
  const kindConfirmed = countAt(counts, "review", "kind_confirmed");
  const keepItems = countAt(counts, "review", "keep_items");

  return (
    <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }} spacing="md">
      <MetricTile
        glyph="●"
        glyphColor="var(--es-success)"
        label="Auto-classified · High"
        value={String(high)}
        ariaValue={String(high)}
      />
      <MetricTile
        glyph="▲"
        glyphColor="var(--es-warning)"
        label="Medium"
        value={String(medium)}
        ariaValue={String(medium)}
      />
      <MetricTile
        glyph="✕"
        glyphColor="var(--es-danger)"
        label="Needs decision"
        value={String(needs)}
        ariaValue={String(needs)}
      />
      <MetricTile
        glyph="☑"
        glyphColor="var(--es-accent)"
        label="Kind confirmed"
        value={`${kindConfirmed} / ${keepItems}`}
        ariaValue={`${kindConfirmed} of ${keepItems}`}
      />
    </SimpleGrid>
  );
}
