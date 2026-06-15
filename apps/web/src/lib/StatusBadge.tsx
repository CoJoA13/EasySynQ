import { Badge, type MantineSize } from "@mantine/core";
import { TONE_GLYPH, type Tone } from "./status";

// The single status pill for the whole SPA. Status is NEVER colour-only (DP-5 / doc-11 DP-7): the
// `tone` drives an AA-tuned --es-*-soft / --es-*-text token pair (via the variant="status" resolver in
// theme/mantine.ts — light + dark come free), the glyph adds a non-colour channel, and the caller's
// `label` carries the meaning. `glyph` defaults to the canonical glyph for the tone; an explicit glyph
// overrides it. `kind` prefixes the accessible name (e.g. "State: Effective", "Coverage: Gap").
export function StatusBadge({
  tone,
  label,
  glyph,
  kind = "Status",
  size = "sm",
}: {
  tone: Tone;
  label: string;
  glyph?: string;
  kind?: string;
  size?: MantineSize;
}) {
  return (
    <Badge
      variant="status"
      color={tone}
      size={size}
      leftSection={<span aria-hidden="true">{glyph ?? TONE_GLYPH[tone]}</span>}
      aria-label={`${kind}: ${label}`}
    >
      {label}
    </Badge>
  );
}
