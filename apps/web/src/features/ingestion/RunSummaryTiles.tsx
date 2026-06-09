import { Group, Paper, SimpleGrid, Stack, Text } from "@mantine/core";
import type { ImportChecklistReviewStats, ImportRun } from "../../lib/types";

// run.counts is a FLAT, top-level-merged bag of per-stage keys (e.g. by_band.HIGH, quarantine,
// proposal.keep_items, commit.committed) — there is NO `classify`/`queues`/`review` namespace; the
// folded review stats live ONLY on the checklist endpoint. countAt walks the bag safely: every step
// degrades to 0 on a missing/non-object node, so a partial or null counts never crashes and never
// yields NaN (noUncheckedIndexedAccess). DP-7: every tile is glyph + label + value; labels distinct.
export function countAt(counts: Record<string, unknown> | null, ...path: string[]): number {
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

export function RunSummaryTiles({
  run,
  review,
}: {
  run: ImportRun;
  review?: ImportChecklistReviewStats;
}) {
  const counts = run.counts;
  // High/Medium come from the run's flat band histogram; "Needs decision" + "Kind confirmed" are
  // folded review stats that exist ONLY on the checklist (passed in from ReviewCockpit) — never on
  // run.counts. Absent review → 0 (calm during the checklist's first load).
  const high = countAt(counts, "by_band", "HIGH");
  const medium = countAt(counts, "by_band", "MEDIUM");
  const needs = review?.undecided ?? 0;
  const kindConfirmed = review?.kind_confirmed ?? 0;
  const keepItems = review?.keep_items ?? 0;

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
