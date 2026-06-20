import { Group, Stack } from "@mantine/core";
import type { RiskBand, RiskRow } from "../../lib/types";
import { TONE_GLYPH } from "../../lib/status";
import { StatusBadge } from "../../lib/StatusBadge";
import { bandForCell, cellRating, MATRIX_AXIS } from "./matrix";
import { RISK_BAND_LABEL, RISK_BAND_ORDER, RISK_BAND_TONE } from "./labels";

// Hand-rolled 5×5 risk-matrix heatmap (D4 — no chart lib; the ObjectiveTrendChart/BandPreview SVG
// idioms). X = Likelihood (1→5 left→right), Y = Severity (1→5 bottom→top), so the top-right cell
// (L5×S5=25) is Critical and the bottom-left (1×1=1) is Low — the standard orientation. Each cell is
// tinted by its band tone (a v1-mirrored bandForCell — see matrix.ts), shows the rating (the
// non-colour per-cell channel, so the heatmap reads in greyscale) + the count of the org's risks
// there, carries a <title>, and the selected row's cell gets an accent ring. role="img" + a summary
// aria-label give the a11y tree the same signal.

// Light band tints — dark text stays AA-legible on all; the rating + glyph-legend carry the meaning,
// never colour alone (DP-5). critical is a stronger red than high (the heatmap gradient).
const BAND_FILL: Record<RiskBand, string> = {
  critical: "var(--mantine-color-red-3)",
  high: "var(--mantine-color-red-1)",
  medium: "var(--mantine-color-yellow-2)",
  low: "var(--mantine-color-green-2)",
  unscored: "var(--mantine-color-gray-1)",
};

// The achievable 5×5 product range per band (spec §4) — the legend's threshold key.
const BAND_RANGE: Record<RiskBand, string> = {
  critical: "20–25",
  high: "12–16",
  medium: "6–10",
  low: "1–5",
  unscored: "—",
};

const CELL = 48;
const M = { top: 8, right: 8, bottom: 38, left: 58 };
const GRID = CELL * 5;
const VIEW_W = M.left + GRID + M.right;
const VIEW_H = M.top + GRID + M.bottom;

const GRID_STROKE = "var(--mantine-color-gray-4)";
const AXIS_TEXT = "var(--mantine-color-gray-7)";
const CELL_TEXT = "var(--mantine-color-dark-6)";
const SEL_RING = "var(--mantine-color-indigo-7)";

export function RiskMatrix({ rows, selected }: { rows: RiskRow[]; selected?: RiskRow | null }) {
  // Density: count rows at each (likelihood, severity). Only rows with both in 1..5 land on the grid
  // (the server guarantees this, but guard for a defensive render).
  const countAt = (l: number, s: number) =>
    rows.filter((r) => r.likelihood === l && r.severity === s).length;

  const plotted = rows.filter(
    (r) => r.likelihood >= 1 && r.likelihood <= 5 && r.severity >= 1 && r.severity <= 5,
  ).length;
  const highCount = rows.filter((r) => r.band === "critical" || r.band === "high").length;
  const summary = `Risk matrix, likelihood × severity (5×5). ${plotted} risk${
    plotted === 1 ? "" : "s"
  } plotted; ${highCount} high or critical.`;

  // x for a likelihood (1..5), y for a severity (1..5, 5 at top).
  const xOf = (l: number) => M.left + (l - 1) * CELL;
  const yOf = (s: number) => M.top + (5 - s) * CELL;

  return (
    <Stack gap="xs">
      <svg
        role="img"
        aria-label={summary}
        viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
        preserveAspectRatio="xMidYMid meet"
        style={{ width: "100%", height: "auto", maxWidth: VIEW_W }}
      >
        {/* cells */}
        {MATRIX_AXIS.map((s) =>
          MATRIX_AXIS.map((l) => {
            const band = bandForCell(l, s);
            const rating = cellRating(l, s);
            const n = countAt(l, s);
            const x = xOf(l);
            const y = yOf(s);
            const isSel = !!selected && selected.likelihood === l && selected.severity === s;
            const title =
              `Likelihood ${l} × Severity ${s} = ${rating} — ${RISK_BAND_LABEL[band]}` +
              (n > 0 ? ` · ${n} risk${n === 1 ? "" : "s"}` : "");
            return (
              <g key={`${l}-${s}`}>
                {/* <title> first child → SVG 1.1 hover tooltip (the ObjectiveTrendChart Codex-P3 rule). */}
                <title>{title}</title>
                <rect
                  x={x}
                  y={y}
                  width={CELL}
                  height={CELL}
                  fill={BAND_FILL[band]}
                  stroke={GRID_STROKE}
                  strokeWidth={1}
                />
                {/* the rating — the always-present non-colour per-cell value (distinguishes bands in
                    greyscale), dimmed in the top-left corner. */}
                <text x={x + 5} y={y + 13} fontSize={9} fill={AXIS_TEXT} aria-hidden>
                  {rating}
                </text>
                {/* the density count — bold + centred when this cell holds risks. */}
                {n > 0 && (
                  <text
                    x={x + CELL / 2}
                    y={y + CELL / 2 + 5}
                    textAnchor="middle"
                    fontSize={16}
                    fontWeight={700}
                    fill={CELL_TEXT}
                    aria-hidden
                  >
                    {n}
                  </text>
                )}
                {isSel && (
                  <rect
                    x={x + 1.5}
                    y={y + 1.5}
                    width={CELL - 3}
                    height={CELL - 3}
                    fill="none"
                    stroke={SEL_RING}
                    strokeWidth={3}
                  />
                )}
              </g>
            );
          }),
        )}

        {/* Y axis (Severity) tick labels — 5 at top */}
        {MATRIX_AXIS.map((s) => (
          <text
            key={`ys${s}`}
            x={M.left - 8}
            y={yOf(s) + CELL / 2 + 4}
            textAnchor="end"
            fontSize={11}
            fill={AXIS_TEXT}
          >
            {s}
          </text>
        ))}
        {/* X axis (Likelihood) tick labels */}
        {MATRIX_AXIS.map((l) => (
          <text
            key={`xl${l}`}
            x={xOf(l) + CELL / 2}
            y={M.top + GRID + 16}
            textAnchor="middle"
            fontSize={11}
            fill={AXIS_TEXT}
          >
            {l}
          </text>
        ))}
        {/* axis titles */}
        <text
          x={M.left + GRID / 2}
          y={VIEW_H - 4}
          textAnchor="middle"
          fontSize={11}
          fontWeight={600}
          fill={AXIS_TEXT}
        >
          Likelihood →
        </text>
        <text
          x={14}
          y={M.top + GRID / 2}
          textAnchor="middle"
          fontSize={11}
          fontWeight={600}
          fill={AXIS_TEXT}
          transform={`rotate(-90 14 ${M.top + GRID / 2})`}
        >
          Severity →
        </text>
      </svg>

      {/* legend — band tone + glyph + label + the achievable-rating range (the threshold key). */}
      <Group gap="xs" wrap="wrap">
        {RISK_BAND_ORDER.map((b) => (
          <StatusBadge
            key={b}
            tone={RISK_BAND_TONE[b]}
            glyph={TONE_GLYPH[RISK_BAND_TONE[b]]}
            label={`${RISK_BAND_LABEL[b]} ${BAND_RANGE[b]}`}
            kind="Band"
          />
        ))}
      </Group>
    </Stack>
  );
}
