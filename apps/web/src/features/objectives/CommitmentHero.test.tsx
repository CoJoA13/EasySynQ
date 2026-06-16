import { expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { renderWithProviders } from "../../test/render";
import { TONE_GLYPH } from "../../lib/status";
import type { Objective } from "../../lib/types";
import { CommitmentHero } from "./CommitmentHero";

const OBJ: Objective = {
  id: "x",
  identifier: "OBJ-001",
  title: "On-time delivery rate",
  current_state: "Draft",
  target_value: "95",
  unit: "%",
  baseline_value: "80",
  current_value: "92",
  direction: "HIGHER_IS_BETTER",
  at_risk_threshold: "90",
  due_date: "2026-12-31",
  process_id: null,
  policy_id: null,
  rag: "amber",
  pct_toward_target: 0.8,
  attainment: "in_progress",
  plans: [],
};

it("shows current vs target, the RAG and attainment badges, and the meta", async () => {
  const { container } = renderWithProviders(<CommitmentHero objective={OBJ} />);
  expect(screen.getByText("92")).toBeInTheDocument();
  expect(screen.getByText(/target 95\s*%/i)).toBeInTheDocument();
  // The RAG pill is the canonical StatusBadge: amber → tone warning → ◔ glyph + "Status: Amber" name
  // (status is never colour-only, DP-7); the label still carries the meaning.
  expect(screen.getByText("Amber")).toBeInTheDocument();
  expect(screen.getByLabelText("Status: Amber")).toBeInTheDocument();
  expect(screen.getByText(TONE_GLYPH.warning)).toBeInTheDocument();
  expect(screen.getByText("In progress")).toBeInTheDocument();
  expect(screen.getByText("Higher is better")).toBeInTheDocument();
  expect(await axe(container)).toHaveNoViolations();
});

it("renders an em dash and no progress bar when unmeasured", () => {
  renderWithProviders(
    <CommitmentHero
      objective={{ ...OBJ, current_value: null, rag: "unmeasured", pct_toward_target: null }}
    />,
  );
  expect(screen.getByText("—")).toBeInTheDocument();
  expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
});
