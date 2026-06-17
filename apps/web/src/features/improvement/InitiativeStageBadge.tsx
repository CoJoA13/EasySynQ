import type { MantineSize } from "@mantine/core";
import { StatusBadge } from "../../lib/StatusBadge";
import type { InitiativeStage } from "../../lib/types";
import { INITIATIVE_STAGE_META } from "./labels";

// The clause-10.3 initiative stage pill (mirrors DcrStateBadge). Tone + glyph + label = a status you
// can't misread (DP-5); the canonical glyph rides the tone via StatusBadge.
export function InitiativeStageBadge({
  stage,
  size = "sm",
}: {
  stage: InitiativeStage;
  size?: MantineSize;
}) {
  const { label, tone } = INITIATIVE_STAGE_META[stage];
  return <StatusBadge tone={tone} label={label} kind="State" size={size} />;
}
