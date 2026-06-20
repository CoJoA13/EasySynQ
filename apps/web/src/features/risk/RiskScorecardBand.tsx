import { Group, Paper, Text } from "@mantine/core";
import type { RiskBand, RiskRow } from "../../lib/types";
import { StatusBadge } from "../../lib/StatusBadge";
import { RISK_BAND_LABEL, RISK_BAND_ORDER, RISK_BAND_TONE } from "./labels";

// The register page's scorecard rollup — computed CLIENT-SIDE from the live rows the table shows (the
// WORKING view), so it always agrees with the table and respects the per-process row-filter. This is
// distinct BY DESIGN from GET /risks/summary (the GOVERNING controlled read Home/MR/doc-13 consume) —
// the page is the working register, the endpoint is the published read-of-record (spec §3.3).
export function RiskScorecardBand({ rows }: { rows: RiskRow[] }) {
  const byBand = rows.reduce<Record<RiskBand, number>>(
    (acc, r) => {
      acc[r.band] = (acc[r.band] ?? 0) + 1;
      return acc;
    },
    { critical: 0, high: 0, medium: 0, low: 0, unscored: 0 },
  );
  const highRisk = byBand.critical + byBand.high;
  // The four canonical bands, plus unscored only when present (v1 never produces it).
  const chips: RiskBand[] =
    byBand.unscored > 0 ? [...RISK_BAND_ORDER, "unscored"] : RISK_BAND_ORDER;

  return (
    <Paper withBorder p="md" radius="md" bg="var(--mantine-color-gray-0)">
      <Group justify="space-between" wrap="wrap">
        <Text>
          {highRisk} of {rows.length} high or critical
        </Text>
        <Group gap="xs">
          {chips.map((b) => (
            <StatusBadge
              key={b}
              tone={RISK_BAND_TONE[b]}
              label={`${byBand[b]} ${RISK_BAND_LABEL[b].toLowerCase()}`}
              kind="Risks"
            />
          ))}
        </Group>
      </Group>
    </Paper>
  );
}
