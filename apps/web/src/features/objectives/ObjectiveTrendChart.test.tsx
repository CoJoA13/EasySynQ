import { expect, it } from "vitest";
import { render } from "@testing-library/react";
import { axe } from "jest-axe";
import { MantineProvider } from "@mantine/core";
import type { ReactElement } from "react";
import type { Measurement } from "../../lib/types";
import { theme } from "../../theme/mantine";
import { ObjectiveTrendChart } from "./ObjectiveTrendChart";

function renderChart(ui: ReactElement) {
  return render(<MantineProvider theme={theme}>{ui}</MantineProvider>);
}

// The main chart is the role="img" SVG; legend swatches are separate aria-hidden SVGs. Scope
// data-point queries to the chart so legend dots/lines never leak into the count.
function chartSvg(container: HTMLElement): SVGSVGElement {
  const svg = container.querySelector<SVGSVGElement>("svg[role='img']");
  if (!svg) throw new Error("chart SVG not found");
  return svg;
}

// The API returns measurements NEWEST-FIRST (order_by desc(period)). The chart reverses to
// oldest-left. Build the fixture in that newest-first order so the chart must reverse it.
const NEWEST_FIRST: Measurement[] = [
  {
    id: "m3",
    objective_id: "obj1",
    record_id: "r3",
    period: "2026-03-01",
    value: "98",
    target_at_capture: "95",
    unit: "%",
    source: "Logistics MIS",
    created_at: "2026-03-02T00:00:00Z",
    rag: "green",
  },
  {
    id: "m2",
    objective_id: "obj1",
    record_id: "r2",
    period: "2026-02-01",
    value: "92",
    target_at_capture: "95",
    unit: "%",
    source: "Logistics MIS",
    created_at: "2026-02-02T00:00:00Z",
    rag: "amber",
  },
  {
    id: "m1",
    objective_id: "obj1",
    record_id: "r1",
    period: "2026-01-01",
    value: "80",
    target_at_capture: "95",
    unit: "%",
    source: "Logistics MIS",
    created_at: "2026-01-02T00:00:00Z",
    rag: "red",
  },
] satisfies Measurement[];

const SINGLE: Measurement[] = [
  {
    id: "m1",
    objective_id: "obj1",
    record_id: "r1",
    period: "2026-01-01",
    value: "80",
    target_at_capture: "95",
    unit: "%",
    source: "Logistics MIS",
    created_at: "2026-01-02T00:00:00Z",
    rag: "red",
  },
] satisfies Measurement[];

// A unit-changing revision (S-obj-4) leaves an old-unit reading in the history alongside
// current-unit ones; the chart must plot only the current-unit readings (the rest stay in the table).
const MIXED_UNIT: Measurement[] = [
  {
    id: "mu2",
    objective_id: "obj1",
    record_id: "r2",
    period: "2026-02-01",
    value: "97",
    target_at_capture: "99",
    unit: "%",
    source: null,
    created_at: "2026-02-02T00:00:00Z",
    rag: "amber",
  },
  {
    id: "mu1",
    objective_id: "obj1",
    record_id: "r1",
    period: "2026-01-01",
    value: "500",
    target_at_capture: "400",
    unit: "ppm",
    source: null,
    created_at: "2026-01-02T00:00:00Z",
    rag: "red",
  },
] satisfies Measurement[];

it("renders N points oldest-left (reversing the newest-first input)", () => {
  const { container } = renderChart(<ObjectiveTrendChart measurements={NEWEST_FIRST} unit="%" />);
  const svg = chartSvg(container);
  const points = svg.querySelectorAll("circle");
  expect(points.length).toBe(3);
  // The reading <title> children are ordered oldest-left → the first point is the earliest period.
  const titles = Array.from(svg.querySelectorAll("circle title")).map((t) => t.textContent ?? "");
  expect(titles[0]).toContain("2026-01-01");
  expect(titles[titles.length - 1]).toContain("2026-03-01");
});

it("fills each point by its server RAG (verbatim, never recomputed)", () => {
  const { container } = renderChart(<ObjectiveTrendChart measurements={NEWEST_FIRST} unit="%" />);
  const fills = Array.from(chartSvg(container).querySelectorAll("circle")).map((c) =>
    c.getAttribute("fill"),
  );
  // oldest-left: red, amber, green
  expect(fills[0]).toBe("var(--mantine-color-red-6)");
  expect(fills[1]).toBe("var(--mantine-color-yellow-6)");
  expect(fills[2]).toBe("var(--mantine-color-green-6)");
});

it("draws a stepped (dashed) target line and a value line", () => {
  const { container } = renderChart(<ObjectiveTrendChart measurements={NEWEST_FIRST} unit="%" />);
  const polylines = chartSvg(container).querySelectorAll("polyline");
  // a value polyline + a stepped target polyline
  expect(polylines.length).toBeGreaterThanOrEqual(2);
  // at least one dashed line (the target reference)
  const dashed = Array.from(polylines).filter((p) => p.getAttribute("stroke-dasharray"));
  expect(dashed.length).toBeGreaterThanOrEqual(1);
});

it("renders the single-reading state: one point, no value polyline, the caption", () => {
  const { container, getByText } = renderChart(
    <ObjectiveTrendChart measurements={SINGLE} unit="%" />,
  );
  const svg = chartSvg(container);
  expect(svg.querySelectorAll("circle").length).toBe(1);
  // no value polyline (a trend needs ≥2 points); the dashed target reference is still present.
  expect(svg.querySelectorAll("polyline").length).toBe(1);
  const target = svg.querySelector("polyline[stroke-dasharray]");
  expect(target).not.toBeNull();
  // ...and it must be a real full-width segment (≥2 coordinates) — a one-coordinate polyline is
  // invisible, leaving the single-reading state with no visible target reference.
  const coords = (target?.getAttribute("points") ?? "").trim().split(/\s+/).filter(Boolean);
  expect(coords.length).toBeGreaterThanOrEqual(2);
  expect(getByText(/one reading so far/i)).toBeInTheDocument();
});

it("exposes role=img with a meaningful aria-label", () => {
  const { container } = renderChart(<ObjectiveTrendChart measurements={NEWEST_FIRST} unit="%" />);
  const svg = container.querySelector("svg[role='img']");
  expect(svg).not.toBeNull();
  const label = svg?.getAttribute("aria-label") ?? "";
  expect(label).toMatch(/trend/i);
  expect(label).toContain("3");
  expect(label).toContain("%");
});

it("charts only readings in the current unit and notes the rest", () => {
  const { container, getByText } = renderChart(
    <ObjectiveTrendChart measurements={MIXED_UNIT} unit="%" />,
  );
  const svg = chartSvg(container);
  // only the same-unit (%) reading is plotted; the ppm reading is excluded.
  expect(svg.querySelectorAll("circle").length).toBe(1);
  expect(svg.querySelector("circle title")?.textContent).toContain("%");
  expect(getByText(/in a different unit/i)).toBeInTheDocument();
});

it("has no accessibility violations", async () => {
  const { container } = renderChart(
    <ObjectiveTrendChart measurements={NEWEST_FIRST} unit="%" direction="HIGHER_IS_BETTER" />,
  );
  expect(await axe(container)).toHaveNoViolations();
});
