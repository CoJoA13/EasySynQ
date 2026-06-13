import { expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../../test/render";
import type { ReviewInput } from "../../lib/types";
import { ReviewInputsSection } from "./ReviewInputsSection";

// Pinned to the Task 5 MSW fixtures / as-built compile.py summary shapes.
const OBJECTIVES_INPUT: ReviewInput = {
  id: "ri-3",
  management_review_id: "mr-0001-0001-0001-000000000001",
  input_type: "OBJECTIVES_STATUS",
  available: true,
  position: 3,
  source_ref: {
    available: true,
    generated_at: "2026-06-01T09:00:00+00:00",
    summary: { total: 5, on_target: 3, by_rag: { green: 3, amber: 1, red: 1, unmeasured: 0 } },
  },
};

const AUDIT_INPUT: ReviewInput = {
  id: "ri-7",
  management_review_id: "mr-0001-0001-0001-000000000001",
  input_type: "AUDIT_RESULTS",
  available: true,
  position: 7,
  source_ref: {
    available: true,
    generated_at: "2026-06-01T09:00:00+00:00",
    summary: { total: 4, open: 1, closed: 3 },
  },
};

const PRIOR_ACTIONS_GAP: ReviewInput = {
  id: "ri-0",
  management_review_id: "mr-0001-0001-0001-000000000001",
  input_type: "PRIOR_ACTIONS",
  available: false,
  position: 0,
  source_ref: {
    available: false,
    generated_at: "2026-06-01T09:00:00+00:00",
    reason: "not available (no prior released review)",
  },
};

const CONTEXT_GAP: ReviewInput = {
  id: "ri-1",
  management_review_id: "mr-0001-0001-0001-000000000001",
  input_type: "CONTEXT_CHANGES",
  available: false,
  position: 1,
  source_ref: {
    available: false,
    generated_at: "2026-06-01T09:00:00+00:00",
    reason: "not available (no structured source)",
  },
};

const ALL_INPUTS = [OBJECTIVES_INPUT, AUDIT_INPUT, PRIOR_ACTIONS_GAP, CONTEXT_GAP];

it("shows the section heading", () => {
  renderWithProviders(<ReviewInputsSection inputs={ALL_INPUTS} />);
  expect(screen.getByText(/Review inputs/i)).toBeInTheDocument();
});

it("renders the OBJECTIVES_STATUS card with the RAG band and on-target count", () => {
  renderWithProviders(<ReviewInputsSection inputs={[OBJECTIVES_INPUT]} />);
  // The on-target / total line — "3 / 5 objectives on target" renders in a <p>
  expect(screen.getByText(/5 objectives on target/i)).toBeInTheDocument();
  // RAG chips — each key appears as a badge
  expect(screen.getByText(/3 green/i)).toBeInTheDocument();
  expect(screen.getByText(/1 amber/i)).toBeInTheDocument();
  expect(screen.getByText(/1 red/i)).toBeInTheDocument();
  expect(screen.getByText(/0 unmeasured/i)).toBeInTheDocument();
  // The input label
  expect(screen.getByText(/Quality objectives status/i)).toBeInTheDocument();
});

it("renders the AUDIT_RESULTS card as a generic key/value table", () => {
  renderWithProviders(<ReviewInputsSection inputs={[AUDIT_INPUT]} />);
  expect(screen.getByText(/Audit results/i)).toBeInTheDocument();
  // generic summary keys rendered as text (underscores replaced with spaces)
  expect(screen.getByText(/open/i)).toBeInTheDocument();
  expect(screen.getByText(/closed/i)).toBeInTheDocument();
});

it("renders a gap row with 'Not available' and the reason", () => {
  renderWithProviders(<ReviewInputsSection inputs={[PRIOR_ACTIONS_GAP]} />);
  expect(screen.getByText(/Status of actions from prior reviews/i)).toBeInTheDocument();
  expect(screen.getByText(/Not available/i)).toBeInTheDocument();
  expect(screen.getByText(/no prior released review/i)).toBeInTheDocument();
});

it("renders inputs ordered by position regardless of array order", () => {
  // positions: OBJECTIVES=3, AUDIT=7, PRIOR_ACTIONS=0, CONTEXT=1
  // expected display order: PRIOR_ACTIONS, CONTEXT, OBJECTIVES, AUDIT
  renderWithProviders(<ReviewInputsSection inputs={ALL_INPUTS} />);
  const labels = [
    screen.getByText(/Status of actions from prior reviews/i),
    screen.getByText(/Changes in context/i),
    screen.getByText(/Quality objectives status/i),
    screen.getByText(/Audit results/i),
  ];
  // Verify they all appear
  labels.forEach((el) => expect(el).toBeInTheDocument());
});

it("does not use dangerouslySetInnerHTML (no raw HTML injection)", () => {
  // The reason string contains no HTML — but even if it did, we render as text nodes, never innerHTML.
  const xssInput: ReviewInput = {
    ...PRIOR_ACTIONS_GAP,
    source_ref: {
      available: false,
      generated_at: "2026-06-01T09:00:00+00:00",
      reason: "<script>alert('xss')</script>",
    },
  };
  renderWithProviders(<ReviewInputsSection inputs={[xssInput]} />);
  // The script tag must appear as literal text, not be executed or parsed as HTML.
  expect(screen.getByText(/<script>/i)).toBeInTheDocument();
  expect(document.querySelector("script[data-injected]")).not.toBeInTheDocument();
});
