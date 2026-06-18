import { Group, Popover, Stack, Text, UnstyledButton } from "@mantine/core";
import { TONE_GLYPH, TONE_MEANING, TONES } from "./status";

// The in-product legend for the canonical non-colour status vocabulary (✓◔✕●○★). Status across the SPA
// is carried by a (tone → glyph + label) pair so it is never colour-alone (DP-5); this surfaces the
// shape channel's meaning so the employee and the external auditor read the same key. It is sourced
// ENTIRELY from lib/status (TONE_GLYPH + TONE_MEANING) — there is no second hand-typed glyph set to
// drift (the S-obj-rag lesson). Rendered as a popover from a calm rail-footer trigger; the glyph cells
// are aria-hidden (the visible meaning carries the accessible text — a status badge's own aria-label
// already names its meaning, so AT never needs the glyph itself).
export function GlyphLegend() {
  return (
    <Popover position="right-start" withArrow shadow="md" width={240}>
      <Popover.Target>
        <UnstyledButton
          aria-label="Status legend"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "6px 12px",
            borderRadius: 8,
            color: "var(--es-text-2)",
            fontSize: 13,
          }}
        >
          <span aria-hidden="true">{TONE_GLYPH.success}</span>
          Status legend
        </UnstyledButton>
      </Popover.Target>
      <Popover.Dropdown>
        <Stack gap="xs">
          <Text fw={600} size="sm">
            Status glyphs
          </Text>
          {TONES.map((tone) => (
            <Group key={tone} gap="sm" wrap="nowrap">
              <Text span aria-hidden="true" fw={700} ta="center" w={18}>
                {TONE_GLYPH[tone]}
              </Text>
              <Text size="sm">{TONE_MEANING[tone]}</Text>
            </Group>
          ))}
        </Stack>
      </Popover.Dropdown>
    </Popover>
  );
}
