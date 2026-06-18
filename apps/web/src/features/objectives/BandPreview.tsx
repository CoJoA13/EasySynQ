import { Box, Group, Stack, Text } from "@mantine/core";
import { bandZones, RAG_GLYPH, RAG_LABEL, type RagZone } from "./labels";
import type { ObjectiveDirection } from "../../lib/types";

const ZONE_COLOR: Record<RagZone, string> = {
  red: "var(--mantine-color-red-6)",
  amber: "var(--mantine-color-yellow-6)",
  green: "var(--mantine-color-green-6)",
};

export function BandPreview({
  target,
  threshold,
  direction,
}: {
  target: string;
  threshold: string;
  direction: ObjectiveDirection;
}) {
  const t = Number(target);
  if (target.trim() === "" || Number.isNaN(t)) return null;
  const thr = threshold.trim() === "" || Number.isNaN(Number(threshold)) ? null : Number(threshold);
  const model = bandZones({ target: t, threshold: thr, direction });

  return (
    <Stack gap={4}>
      <Box
        role="img"
        // The accessible name carries the MEANING per zone (not the raw colour key), so a screen
        // reader / a11y-tree audit gets the same non-colour channel the visual glyphs give — the
        // aria-hidden glyph chips are decorative duplicates of this label (Codex P2).
        aria-label={`Status band, worse to better: ${model.zones
          .map((z) => RAG_LABEL[z])
          .join(", ")}`}
        style={{ display: "flex", height: 18, borderRadius: 4, overflow: "hidden" }}
      >
        {/* Each zone carries its canonical glyph (✕/◔/✓) in a white inset chip — the DP-5 non-colour
            channel, so the band reads worse→better in greyscale, not by colour alone. The chip's dark
            glyph stays AA-legible on all three zone colours (white-on-yellow text would not). */}
        {model.zones.map((z) => (
          <Box
            key={z}
            style={{
              flex: 1,
              background: ZONE_COLOR[z],
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <Box
              aria-hidden
              style={{
                width: 14,
                height: 14,
                borderRadius: "50%",
                background: "var(--mantine-color-white)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: 10,
                fontWeight: 700,
                lineHeight: 1,
                color: "var(--mantine-color-dark-7)",
              }}
            >
              {RAG_GLYPH[z]}
            </Box>
          </Box>
        ))}
      </Box>
      <Group justify="space-between">
        {thr !== null && (
          <Text size="xs" c="dimmed">
            {thr} at-risk
          </Text>
        )}
        <Text size="xs" c="dimmed">
          {t} target ✓
        </Text>
      </Group>
      {model.warn && (
        <Text size="xs" c="yellow.8">
          {model.warn}
        </Text>
      )}
    </Stack>
  );
}
