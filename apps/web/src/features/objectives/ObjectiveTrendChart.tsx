import { Group, Stack, Text } from "@mantine/core";
import type { Measurement, ObjectiveDirection, ObjectiveRag } from "../../lib/types";
import { DIRECTION_LABEL, RAG_GLYPH, RAG_LABEL } from "./labels";

// SVG point fill by RAG — CSS-var colours (the BandPreview ZONE_COLOR idiom), NOT the Mantine
// colour *name* tokens in labels.ts RAG_COLOR (those feed Mantine `color` props, not SVG fills).
const RAG_FILL: Record<ObjectiveRag, string> = {
  green: "var(--mantine-color-green-6)",
  amber: "var(--mantine-color-yellow-6)",
  red: "var(--mantine-color-red-6)",
  unmeasured: "var(--mantine-color-gray-5)", // unreachable for a measurement; kept total
};

const VIEW_W = 720;
const VIEW_H = 280;
const M = { top: 16, right: 16, bottom: 28, left: 48 };
const PLOT_W = VIEW_W - M.left - M.right;
const PLOT_H = VIEW_H - M.top - M.bottom;

const GRID = "var(--mantine-color-gray-3)";
const AXIS_TEXT = "var(--mantine-color-gray-6)";
const TARGET_LINE = "var(--mantine-color-gray-5)";
const VALUE_LINE = "var(--mantine-color-blue-6)";
const DIRECTION_GLYPH: Record<ObjectiveDirection, string> = {
  HIGHER_IS_BETTER: "↑",
  LOWER_IS_BETTER: "↓",
};

interface Point {
  period: string;
  value: number;
  target: number;
  rag: ObjectiveRag;
  valueStr: string;
  targetStr: string;
}

// Format a computed axis-tick value. `decimals` adapts to the domain span so small-magnitude KPIs
// (e.g. a 0.001 defect rate) aren't all flattened to "0.00"; trailing-zero noise is stripped.
// Readings themselves render their RAW decimal string (never rounded — a rounded value could
// contradict its server RAG colour, e.g. 94.999 vs target 95), so this is only for gridline labels.
function fmtTick(value: number, decimals: number): string {
  return Number(value.toFixed(decimals)).toString();
}

export function ObjectiveTrendChart({
  measurements,
  unit,
  direction,
}: {
  measurements: Measurement[];
  unit: string;
  direction?: ObjectiveDirection;
}) {
  // Chart only readings in the objective's current (governing) unit — a unit-changing revision
  // (S-obj-4) leaves old-unit rows in the history that aren't comparable on one numeric axis
  // (Codex P2). They stay in the table below; this mirrors the backend rollup, which likewise
  // only counts same-unit readings into current_value.
  const sameUnit = measurements.filter((m) => m.unit === unit);
  const hiddenForUnit = measurements.length - sameUnit.length;
  // The API returns measurements NEWEST-FIRST; reverse → oldest-left, newest-right.
  const series: Point[] = [...sameUnit].reverse().map((m) => ({
    period: m.period,
    value: Number(m.value),
    target: Number(m.target_at_capture),
    rag: m.rag,
    valueStr: m.value,
    targetStr: m.target_at_capture,
  }));

  const n = series.length;
  if (n === 0) {
    // MeasurementsSection only mounts us with ≥1 reading, so an empty series means EVERY reading
    // is in a previous unit (a unit-changing revision) — nothing comparable to chart. Explain it
    // rather than vanishing silently (Codex P3); the readings are still in the table below.
    if (measurements.length === 0) return null;
    return (
      <Text size="xs" c="dimmed">
        The {measurements.length} reading{measurements.length === 1 ? "" : "s"} below{" "}
        {measurements.length === 1 ? "is" : "are"} in a previous unit — no comparable trend to chart
        in {unit}.
      </Text>
    );
  }

  // y-domain over values ∪ targets, padded ~8%; NEVER forced to 0; degenerate domain → ±1/±|v|·0.1.
  const ys = series.flatMap((p) => [p.value, p.target]).filter((v) => Number.isFinite(v));
  let lo = Math.min(...ys);
  let hi = Math.max(...ys);
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) {
    lo = 0;
    hi = 1;
  }
  if (hi === lo) {
    const pad = Math.abs(lo) * 0.1 || 1;
    lo -= pad;
    hi += pad;
  } else {
    const pad = (hi - lo) * 0.08;
    lo -= pad;
    hi += pad;
  }

  // Axis-tick decimals from the domain span: enough to distinguish small-magnitude gridlines
  // without trailing noise on large ones (a span of ~100 → 0dp; ~2 → 1dp; ~0.01 → 3dp).
  const span = hi - lo;
  const tickDecimals = span > 0 ? Math.min(6, Math.max(0, 1 - Math.floor(Math.log10(span)))) : 2;

  // categorical x: evenly spaced; a single point sits centred.
  const xAt = (i: number) => (n === 1 ? M.left + PLOT_W / 2 : M.left + (PLOT_W * i) / (n - 1));
  const yAt = (v: number) => M.top + PLOT_H - (PLOT_H * (v - lo)) / (hi - lo);

  // y gridlines (4 bands).
  const TICKS = 4;
  const yTicks = Array.from({ length: TICKS + 1 }, (_, i) => lo + ((hi - lo) * i) / TICKS);

  // x labels thinned so they never crowd: all when ≤8, else first/last + every Nth.
  const everyNth = n <= 8 ? 1 : Math.ceil(n / 6);
  const showXLabel = (i: number) => i === 0 || i === n - 1 || i % everyNth === 0;

  const valuePts = series.map((p, i) => `${xAt(i)},${yAt(p.value)}`).join(" ");
  // stepped (step-after) target line: hold each target until the next reading's x. For a single
  // reading, draw a full-width horizontal reference instead — a one-coordinate polyline renders
  // nothing, so the target would otherwise be invisible (Codex P2).
  const targetStepPts: string[] = [];
  if (n === 1) {
    const ty = yAt(series[0]!.target);
    targetStepPts.push(`${M.left},${ty}`, `${M.left + PLOT_W},${ty}`);
  } else {
    series.forEach((p, i) => {
      targetStepPts.push(`${xAt(i)},${yAt(p.target)}`);
      if (i < n - 1) targetStepPts.push(`${xAt(i + 1)},${yAt(p.target)}`);
    });
  }

  const first = series[0]!;
  const last = series[n - 1]!;
  const summary =
    `KPI trend, ${unit}: ${n} reading${n === 1 ? "" : "s"} from ${first.period} to ${last.period}; ` +
    `latest ${last.valueStr} ${unit}, status ${RAG_LABEL[last.rag]}.`;

  return (
    <Stack gap={6}>
      <svg
        role="img"
        aria-label={summary}
        viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
        preserveAspectRatio="xMidYMid meet"
        style={{ width: "100%", height: "auto" }}
      >
        {/* y gridlines + labels */}
        {yTicks.map((t, i) => {
          const y = yAt(t);
          return (
            <g key={`y${i}`}>
              <line x1={M.left} y1={y} x2={M.left + PLOT_W} y2={y} stroke={GRID} strokeWidth={1} />
              <text
                x={M.left - 6}
                y={y + 3}
                textAnchor="end"
                fontSize={10}
                fill={AXIS_TEXT}
              >{`${fmtTick(t, tickDecimals)} ${unit}`}</text>
            </g>
          );
        })}

        {/* x axis baseline + thinned period labels */}
        <line
          x1={M.left}
          y1={M.top + PLOT_H}
          x2={M.left + PLOT_W}
          y2={M.top + PLOT_H}
          stroke={GRID}
          strokeWidth={1}
        />
        {series.map((p, i) =>
          showXLabel(i) ? (
            <text
              key={`x${i}`}
              x={xAt(i)}
              y={M.top + PLOT_H + 16}
              textAnchor="middle"
              fontSize={10}
              fill={AXIS_TEXT}
            >
              {p.period}
            </text>
          ) : null,
        )}

        {/* stepped, dashed target reference line */}
        <polyline
          points={targetStepPts.join(" ")}
          fill="none"
          stroke={TARGET_LINE}
          strokeWidth={1.5}
          strokeDasharray="5 4"
        />

        {/* value line — only with ≥2 readings (a trend needs two points) */}
        {n >= 2 && <polyline points={valuePts} fill="none" stroke={VALUE_LINE} strokeWidth={2} />}

        {/* per-reading markers: the canonical RAG glyph (the DP-5 non-colour channel — ✓/◔/✕),
            coloured by the server RAG verbatim (N9 — never recomputed). data-rag is the stable
            marker/test hook; the glyph IS the marker so the status survives a greyscale read. */}
        {series.map((p, i) => (
          <g key={`p${i}`} data-rag={p.rag}>
            {/* <title> MUST be the first child of its <g> so SVG 1.1 user agents expose it as the
                point's hover tooltip (Codex P3). */}
            <title>{`${p.period}: ${p.valueStr} ${unit} (target ${p.targetStr}) — ${RAG_LABEL[p.rag]}`}</title>
            <text
              x={xAt(i)}
              y={yAt(p.value)}
              textAnchor="middle"
              dominantBaseline="central"
              fontSize={13}
              fontWeight={700}
              fill={RAG_FILL[p.rag]}
              aria-hidden
            >
              {RAG_GLYPH[p.rag]}
            </text>
          </g>
        ))}
      </svg>

      {/* legend */}
      <Group gap="md" wrap="wrap">
        <Group gap={6}>
          <svg width={18} height={10} aria-hidden>
            <line x1={1} y1={5} x2={17} y2={5} stroke={VALUE_LINE} strokeWidth={2} />
          </svg>
          <Text size="xs" c="dimmed">
            Value
          </Text>
        </Group>
        <Group gap={6}>
          <svg width={18} height={10} aria-hidden>
            <line
              x1={1}
              y1={5}
              x2={17}
              y2={5}
              stroke={TARGET_LINE}
              strokeWidth={1.5}
              strokeDasharray="5 4"
            />
          </svg>
          <Text size="xs" c="dimmed">
            Target
          </Text>
        </Group>
        {(["green", "amber", "red"] as const).map((r) => (
          <Group gap={6} key={r}>
            <svg width={12} height={12} aria-hidden>
              <text
                x={6}
                y={6}
                textAnchor="middle"
                dominantBaseline="central"
                fontSize={12}
                fontWeight={700}
                fill={RAG_FILL[r]}
              >
                {RAG_GLYPH[r]}
              </text>
            </svg>
            <Text size="xs" c="dimmed">
              {RAG_LABEL[r]}
            </Text>
          </Group>
        ))}
      </Group>

      {hiddenForUnit > 0 && (
        <Text size="xs" c="dimmed">
          {hiddenForUnit} earlier reading{hiddenForUnit === 1 ? "" : "s"} in a different unit
          {hiddenForUnit === 1 ? " is" : " are"} listed in the table below.
        </Text>
      )}
      {n === 1 && (
        <Text size="xs" c="dimmed">
          One reading so far.
        </Text>
      )}
      {direction && (
        <Text
          size="xs"
          c="dimmed"
        >{`${DIRECTION_GLYPH[direction]} ${DIRECTION_LABEL[direction]}`}</Text>
      )}
    </Stack>
  );
}
