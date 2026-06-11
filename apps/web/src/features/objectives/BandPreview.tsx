import { Box, Group, Stack, Text } from "@mantine/core";
import { bandZones, type RagZone } from "./labels";
import type { ObjectiveDirection } from "../../lib/types";

const ZONE_COLOR: Record<RagZone, string> = {
  red: "var(--mantine-color-red-6)",
  amber: "var(--mantine-color-yellow-6)",
  green: "var(--mantine-color-green-6)",
};

export function BandPreview({
  target, threshold, direction,
}: { target: string; threshold: string; direction: ObjectiveDirection }) {
  const t = Number(target);
  if (target.trim() === "" || Number.isNaN(t)) return null;
  const thr = threshold.trim() === "" || Number.isNaN(Number(threshold)) ? null : Number(threshold);
  const model = bandZones({ target: t, threshold: thr, direction });

  return (
    <Stack gap={4}>
      <Box
        role="img"
        aria-label={`Status band: ${model.zones.join(", ")} from worse to better`}
        style={{ display: "flex", height: 14, borderRadius: 4, overflow: "hidden" }}
      >
        {model.zones.map((z) => (
          <Box key={z} style={{ flex: 1, background: ZONE_COLOR[z] }} />
        ))}
      </Box>
      <Group justify="space-between">
        {thr !== null && <Text size="xs" c="dimmed">{thr} at-risk</Text>}
        <Text size="xs" c="dimmed">{t} target ✓</Text>
      </Group>
      {model.warn && (
        <Text size="xs" c="yellow.8">{model.warn}</Text>
      )}
    </Stack>
  );
}
